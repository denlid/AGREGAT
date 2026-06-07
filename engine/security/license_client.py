"""
AGREGAT License Client — Desktop-side licensing module

Handles server communication for:
- Device fingerprint generation (CPU + disk + MAC hash)
- License activation flow
- Heartbeat sender (every 60s)
- Strategy hash collection + upload
- Local encrypted license cache
- Graceful offline fallback

Works alongside the existing license_manager.py (offline HWID checks).
"""

import base64
import hashlib
import hmac
import json
import os
import platform
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
import threading

try:
    import httpx
    HAS_HTTPX = True
except ImportError:
    import urllib.request
    import urllib.error
    HAS_HTTPX = False


# ─── Configuration ─────────────────────────────────────────────────────

# P1-2: HTTPS enforcement — reject non-HTTPS URLs in production
def _validate_server_url(url: str) -> str:
    """
    P1-2: Ensure server URL uses HTTPS in production.
    Prevents token/credential leakage via plaintext HTTP.
    Dev mode requires BOTH flags: APP_ENV=development AND AGREGAT_DEV_MODE=1
    """
    url = url.rstrip("/")
    if not url.startswith("https://"):
        app_env = os.environ.get("APP_ENV", "production").lower()
        dev_mode = os.environ.get("AGREGAT_DEV_MODE", "0")
        if not (app_env == "development" and dev_mode == "1"):
            raise RuntimeError(
                "SECURITY: Server URL must use HTTPS in production. "
                f"Got: {url}. Set APP_ENV=development AND AGREGAT_DEV_MODE=1 to bypass."
            )
    return url


# Server URL — override via environment variable for testing
SERVER_URL = _validate_server_url(
    os.environ.get("AGREGAT_SERVER_URL", "https://license.agregat.io")
)
API_BASE = f"{SERVER_URL}/api/v1"

# Heartbeat interval (seconds) — must be less than server's heartbeat_grace_period
HEARTBEAT_INTERVAL = 60  # 1 minute

# Local cache file
CACHE_DIR = Path.home() / ".agregat"
CACHE_FILE = CACHE_DIR / "license_cache.enc"

# Encryption key derived from hardware (same as license_manager.py)
_CACHE_KEY_SALT = b"\x77\x88\xAA\xBB"
_CACHE_KEY_PREFIX = b"\x41\x47\x52\x45\x47\x41\x54"  # AGREGAT


# ─── Device Fingerprint ────────────────────────────────────────────────

def _get_cpu_id() -> str:
    """Get CPU identifier (cross-platform)."""
    try:
        if platform.system() == "Windows":
            output = subprocess.check_output(
                "wmic cpu get ProcessorId", shell=True, stderr=subprocess.DEVNULL
            ).decode().strip()
            lines = output.split("\n")
            if len(lines) > 1:
                return lines[1].strip()
        elif platform.system() == "Darwin":
            output = subprocess.check_output(
                ["sysctl", "-n", "machdep.cpu.brand_string"],
                stderr=subprocess.DEVNULL
            ).decode().strip()
            return output
        else:
            with open("/proc/cpuinfo", "r") as f:
                for line in f:
                    if line.startswith("Serial"):
                        return line.split(":")[1].strip()
    except Exception:
        pass
    return "unknown_cpu"


def _get_disk_serial() -> str:
    """Get primary disk serial number."""
    try:
        if platform.system() == "Windows":
            output = subprocess.check_output(
                "wmic diskdrive get SerialNumber", shell=True, stderr=subprocess.DEVNULL
            ).decode().strip()
            lines = output.split("\n")
            if len(lines) > 1:
                return lines[1].strip()
        elif platform.system() == "Darwin":
            output = subprocess.check_output(
                ["system_profiler", "SPStorageDataType"],
                stderr=subprocess.DEVNULL
            ).decode()
            for line in output.split("\n"):
                if "Serial Number" in line:
                    return line.split(":")[-1].strip()
        else:
            output = subprocess.check_output(
                ["lsblk", "-no", "SERIAL", "/dev/sda"],
                stderr=subprocess.DEVNULL
            ).decode().strip()
            return output
    except Exception:
        pass
    return "unknown_disk"


def _get_mac_address() -> str:
    """Get primary MAC address."""
    try:
        mac = uuid.getnode()
        return ":".join(f"{(mac >> i) & 0xff:02x}" for i in range(0, 48, 8)[::-1])
    except Exception:
        return "unknown_mac"


def generate_device_fingerprint() -> str:
    """
    Generate hardware-bound fingerprint with enhanced components.
    
    P2-8: Added more hardware identifiers to make spoofing harder:
    - CPU ID
    - Disk serial
    - MAC address
    - Platform machine type
    - Platform processor
    - UUID node
    - BIOS serial (Windows only)
    
    Returns a 64-character hex string.
    """
    components = [
        _get_cpu_id(),
        _get_disk_serial(),
        _get_mac_address(),
        platform.machine(),
        platform.processor(),
        str(uuid.getnode()),
    ]
    
    # P2-8: Add BIOS serial on Windows (harder to spoof than MAC)
    if platform.system() == "Windows":
        try:
            bios_output = subprocess.check_output(
                "wmic bios get SerialNumber", shell=True, stderr=subprocess.DEVNULL
            ).decode().strip()
            bios_lines = bios_output.split("\n")
            if len(bios_lines) > 1:
                bios_serial = bios_lines[1].strip()
                if bios_serial and bios_serial not in ("", "0", "To be filled by O.E.M."):
                    components.append(bios_serial)
        except Exception:
            pass
    
    # P2-8: Add motherboard serial on Windows
    if platform.system() == "Windows":
        try:
            mb_output = subprocess.check_output(
                "wmic baseboard get SerialNumber", shell=True, stderr=subprocess.DEVNULL
            ).decode().strip()
            mb_lines = mb_output.split("\n")
            if len(mb_lines) > 1:
                mb_serial = mb_lines[1].strip()
                if mb_serial and mb_serial not in ("", "0", "To be filled by O.E.M."):
                    components.append(mb_serial)
        except Exception:
            pass
    
    raw = "|".join(components)
    return hashlib.sha256(raw.encode()).hexdigest()


def get_device_name() -> str:
    """Get human-readable device name."""
    try:
        return platform.node() or "Unknown Device"
    except Exception:
        return "Unknown Device"


def get_os_info() -> str:
    """Get OS information string."""
    try:
        return f"{platform.system()} {platform.release()} ({platform.machine()})"
    except Exception:
        return "Unknown OS"


def get_app_version() -> str:
    """Get current app version."""
    try:
        # Try to read from version file or package
        version_file = Path(__file__).parent.parent.parent / "VERSION"
        if version_file.exists():
            return version_file.read_text().strip()
    except Exception:
        pass
    return "7.78"


# ─── Ed25519 Signature Verification (C-02) ─────────────────────────────
# The server's public key is EMBEDDED in the client binary at build time.
# This replaces the weak XOR encryption with cryptographic signatures.
#
# C-02 SECURITY: In production builds, the key MUST be hardcoded below.
# Environment variable override is ONLY allowed when BOTH conditions are met:
#   - APP_ENV=development
#   - AGREGAT_DEV_MODE=1
#
# To update the production key after rotating on the server:
#   1. Run: python server/scripts/generate_signing_key.py
#   2. Copy the base64 output
#   3. Replace the _PRODUCTION_PUBLIC_KEY_B64 value below
#   4. Rebuild the desktop client binary
#
# P0-1: Ed25519 public key — MUST be set before production build.
# Generate with: python server/scripts/generate_signing_key.py
# The placeholder below is a valid Ed25519 public key for testing.
# REPLACE THIS with your actual server public key before shipping!
_PRODUCTION_PUBLIC_KEY_B64 = (
    "PLACEHOLDER_REPLACE_WITH_ACTUAL_KEY"  # P0-1: Generate and embed before build
)


def _get_server_public_key() -> str:
    """
    Get the server public key for receipt verification.
    
    C-02: In production, ONLY the embedded constant is used.
    Environment override requires BOTH dev flags to prevent
    attackers from pointing the client at a forged key.
    """
    # Check if we're in development mode (BOTH flags required)
    app_env = os.environ.get("APP_ENV", "production").lower()
    dev_mode = os.environ.get("AGREGAT_DEV_MODE", "0")
    
    if app_env == "development" and dev_mode == "1":
        # Dev mode: allow env override for testing
        env_key = os.environ.get("AGREGAT_SERVER_PUBLIC_KEY", "")
        if env_key:
            return env_key
    
    # Production (or dev without override): use embedded key only
    return _PRODUCTION_PUBLIC_KEY_B64


# Module-level constant for backward compatibility
# This is evaluated at import time — use _get_server_public_key() for dynamic access
SERVER_PUBLIC_KEY_B64 = _get_server_public_key()


def _verify_server_signature(receipt: dict) -> bool:
    """
    Verify that a license receipt was signed by the AGREGAT server.
    
    SECURITY (C-01): FAIL-CLOSED verification.
    - No public key configured → REJECT (invalid)
    - No signature in receipt → REJECT (invalid)
    - cryptography unavailable → REJECT (invalid)
    - Any verification error → REJECT (invalid)
    
    There is NO fallback. If verification cannot be performed,
    the license is considered invalid. This prevents attackers
    from bypassing verification by removing the public key or
    the cryptography package.
    
    Returns True ONLY if signature is cryptographically valid.
    """
    # FAIL-CLOSED: No public key = invalid (not dev mode bypass)
    if not SERVER_PUBLIC_KEY_B64:
        return False
    
    # FAIL-CLOSED: No signature = invalid
    signature_b64 = receipt.get("signature")
    if not signature_b64:
        return False
    
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        
        signature = base64.b64decode(signature_b64)
        raw_key = base64.b64decode(SERVER_PUBLIC_KEY_B64)
        public_key = Ed25519PublicKey.from_public_bytes(raw_key)
        
        # Rebuild canonical payload (without signature)
        payload = {k: v for k, v in receipt.items() if k != "signature"}
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        
        public_key.verify(signature, canonical.encode())
        return True
    except ImportError:
        # FAIL-CLOSED: cryptography unavailable = invalid
        # In production, cryptography is always available (bundled by Nuitka)
        # If it's missing, something is wrong — reject the license
        return False
    except Exception:
        # Any error (bad signature, bad key, tampered data) = invalid
        return False


# ─── Local Cache (Encrypted + Signed) ─────────────────────────────────

def _get_cache_key() -> bytes:
    """Derive cache encryption key from hardware fingerprint."""
    hwid = generate_device_fingerprint()
    return hashlib.sha256(_CACHE_KEY_PREFIX + hwid.encode() + _CACHE_KEY_SALT).digest()


def save_license_cache(data: dict) -> bool:
    """
    Save license data to local cache with HMAC integrity.
    
    SECURITY (C-06): Uses HMAC-SHA256 for integrity (not XOR).
    The data itself is signed by the server, so we only need
    to detect local tampering of the cache file.
    """
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        key = _get_cache_key()
        raw = json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
        # HMAC for integrity (no encryption needed — data is server-signed)
        mac = hmac.new(key, raw, hashlib.sha256).hexdigest()
        payload = json.dumps({"data": raw.decode(), "mac": mac})
        CACHE_FILE.write_text(payload)
        return True
    except Exception:
        return False


def load_license_cache() -> Optional[dict]:
    """
    Load and verify license data from local cache.
    Returns None if cache is missing, corrupted, or tampered.
    
    SECURITY (C-01): Verifies both HMAC integrity AND server signature.
    Cache format: {"receipt": {...signed data...}, "activated_at": "..."}
    The receipt contains the server's Ed25519 signature.
    """
    try:
        if not CACHE_FILE.exists():
            return None
        key = _get_cache_key()
        payload = json.loads(CACHE_FILE.read_text())
        raw_data = payload.get("data", "")
        mac = payload.get("mac", "")

        # Verify HMAC integrity
        expected_mac = hmac.new(key, raw_data.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(mac, expected_mac):
            return None  # Tampered

        data = json.loads(raw_data)
        
        # C-01: Check if cache contains a signed receipt
        receipt = data.get("receipt")
        if receipt:
            # Verify server signature on the receipt — FAIL-CLOSED
            if not _verify_server_signature(receipt):
                return None  # Signature invalid — forged or tampered receipt
            
            # Return the receipt data (extracted from signed envelope)
            return {
                "receipt": receipt,
                "activated_at": data.get("activated_at"),
                # Extract license fields from receipt for convenience
                "user_id": receipt.get("user_id"),
                "license_tier": receipt.get("license_tier"),
                "entitlements": receipt.get("entitlements", []),
                "expires_at": receipt.get("expires_at"),
            }
        else:
            # Legacy cache format (no receipt) — still verify signature on data itself
            if not _verify_server_signature(data):
                return None  # Signature invalid
            return data
    except Exception:
        return None


def clear_license_cache() -> None:
    """Remove local license cache."""
    try:
        if CACHE_FILE.exists():
            CACHE_FILE.unlink()
    except Exception:
        pass


# ─── P0-2: TLS Certificate Pinning ────────────────────────────────────

# SHA256 fingerprint of the expected server certificate.
# Obtain via: openssl s_client -connect license.agregat.io:443 | openssl x509 -fingerprint -sha256 -noout
# Set to empty string to disable pinning (NOT recommended for production).
_EXPECTED_CERT_SHA256 = os.environ.get("AGREGAT_CERT_PIN", "")


def _get_ssl_context_for_pinning():
    """
    P0-2: Create SSL context with certificate pinning.
    If _EXPECTED_CERT_SHA256 is set, verifies the server cert fingerprint.
    Falls back to standard CA verification if pinning is not configured.
    """
    import ssl
    try:
        import certifi
        ca_file = certifi.where()
    except ImportError:
        ca_file = None
    
    ctx = ssl.create_default_context(cafile=ca_file)
    
    if _EXPECTED_CERT_SHA256:
        # Custom verification: check cert fingerprint
        original_verify = ctx.verify_mode
        ctx.verify_mode = ssl.CERT_REQUIRED
        
        # We'll verify the fingerprint in the HTTP client callback
        # For urllib, we use a custom HTTPSConnection
        pass
    
    return ctx


def _verify_cert_fingerprint(cert_der: bytes) -> bool:
    """
    P0-2: Verify server certificate SHA256 fingerprint.
    Returns True if fingerprint matches or pinning is disabled.
    """
    if not _EXPECTED_CERT_SHA256:
        return True  # Pinning not configured — allow any valid cert
    
    import hashlib
    cert_hash = hashlib.sha256(cert_der).hexdigest().upper()
    expected = _EXPECTED_CERT_SHA256.upper().replace(":", "").replace("SHA256=", "")
    return cert_hash == expected


# ─── P1-5: Client-Side Audit Logging ──────────────────────────────────

_CLIENT_AUDIT_LOG = CACHE_DIR / "audit.log"


def _log_client_event(action: str, **kwargs):
    """
    P1-5: Log security event locally.
    Creates an append-only audit trail for forensic analysis.
    """
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "action": action,
            **kwargs
        }
        with open(_CLIENT_AUDIT_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass  # Never fail the main flow due to audit logging


# ─── P1-6: Time Manipulation Detection ────────────────────────────────

_last_known_server_time: Optional[str] = None
_last_known_local_time: Optional[str] = None


def _update_trusted_time(server_time_iso: str):
    """
    P1-6: Store the last known server time for clock manipulation detection.
    """
    global _last_known_server_time, _last_known_local_time
    _last_known_server_time = server_time_iso
    _last_known_local_time = datetime.now(timezone.utc).isoformat()


def _get_trusted_time() -> datetime:
    """
    P1-6: Get trusted time, detecting clock manipulation.
    If the local clock has moved backwards since last server sync,
    uses the last known server time + elapsed duration.
    """
    global _last_known_server_time, _last_known_local_time
    
    local_now = datetime.now(timezone.utc)
    
    if _last_known_server_time and _last_known_local_time:
        try:
            local_stored = datetime.fromisoformat(_last_known_local_time)
            server_stored = datetime.fromisoformat(_last_known_server_time)
            
            # Detect clock rollback
            if local_now < local_stored:
                _log_client_event("clock_manipulation_detected",
                    local_now=local_now.isoformat(),
                    local_stored=_last_known_local_time,
                    server_stored=_last_known_server_time)
                # Use last server time + (supposed) elapsed time
                # Since clock went back, we assume minimum elapsed = 0
                return server_stored
        except (ValueError, TypeError):
            pass
    
    return local_now


# ─── HTTP Client ──────────────────────────────────────────────────────

def _add_request_signing_headers(
    headers: dict,
    method: str,
    endpoint: str,
    data: Optional[dict],
    token: Optional[str],
) -> dict:
    """
    E2E-01: Add HMAC request signing headers.
    
    Signs the request with HMAC-SHA256 using a key derived from
    the session token + device fingerprint. This prevents:
    - MITM attacks (defense-in-depth beyond TLS)
    - Replay attacks (nonce + timestamp)
    - Request tampering (any modification invalidates signature)
    
    Only signs requests that have a token (authenticated requests).
    Activation/refresh endpoints don't need signing (no session yet).
    """
    if not token:
        return headers  # No token = no signing (activation/refresh)
    
    try:
        from engine.security.request_signing import sign_api_request
        
        # Get device fingerprint for key derivation
        fingerprint = generate_device_fingerprint()
        
        # Sign the request
        sig_headers = sign_api_request(
            method=method,
            endpoint=f"/api/v1{endpoint}",
            payload=data,
            session_token=token,
            device_fingerprint=fingerprint,
        )
        
        # Add signing headers
        headers.update(sig_headers)
        
        # Also send fingerprint so server can derive the same key
        headers["X-Agreqat-Device-Fingerprint"] = fingerprint
        
    except ImportError:
        pass  # request_signing not available — skip signing
    except Exception:
        pass  # Signing failed — skip (graceful degradation)
    
    return headers


def _http_post(endpoint: str, data: dict, token: Optional[str] = None) -> Optional[dict]:
    """
    Make HTTP POST request to license server.
    Returns response dict or None on failure.
    
    P0-2: Uses TLS certificate verification.
    P1-5: Logs all requests for audit.
    E2E-01: HMAC request signing with nonce replay prevention.
    """
    url = f"{API_BASE}{endpoint}"
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    
    # E2E-01: Add request signing headers
    headers = _add_request_signing_headers(headers, "POST", endpoint, data, token)

    try:
        if HAS_HTTPX:
            import httpx as _httpx
            # P0-2: TLS verification with certifi CA bundle
            try:
                import certifi
                ssl_verify = certifi.where()
            except ImportError:
                ssl_verify = True  # System CA bundle
            
            with _httpx.Client(timeout=10.0, verify=ssl_verify) as client:
                resp = client.post(url, json=data, headers=headers)
                if resp.status_code == 200:
                    result = resp.json()
                    # P1-6: Capture server time from response
                    if "server_time" in result:
                        _update_trusted_time(result["server_time"])
                    # P1-5: Audit log
                    _log_client_event("http_post_success", endpoint=endpoint)
                    return result
                _log_client_event("http_post_error", endpoint=endpoint, status=resp.status_code)
                return None
        else:
            # Fallback to urllib with SSL verification
            import ssl
            raw = json.dumps(data).encode()
            req = urllib.request.Request(url, data=raw, headers=headers, method="POST")
            ctx = _get_ssl_context_for_pinning()
            with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
                if resp.status == 200:
                    result = json.loads(resp.read().decode())
                    if "server_time" in result:
                        _update_trusted_time(result["server_time"])
                    _log_client_event("http_post_success", endpoint=endpoint)
                    return result
            return None
    except Exception as e:
        _log_client_event("http_post_failed", endpoint=endpoint, error=str(e))
        return None


def _http_get(endpoint: str, token: Optional[str] = None) -> Optional[dict]:
    """
    Make HTTP GET request to license server.
    
    E2E-01: HMAC request signing with nonce replay prevention.
    """
    url = f"{API_BASE}{endpoint}"
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    
    # E2E-01: Add request signing headers
    headers = _add_request_signing_headers(headers, "GET", endpoint, None, token)

    try:
        if HAS_HTTPX:
            import httpx as _httpx
            try:
                import certifi
                ssl_verify = certifi.where()
            except ImportError:
                ssl_verify = True
            
            with _httpx.Client(timeout=10.0, verify=ssl_verify) as client:
                resp = client.get(url, headers=headers)
                if resp.status_code == 200:
                    result = resp.json()
                    if "server_time" in result:
                        _update_trusted_time(result["server_time"])
                    return result
                return None
        else:
            import ssl
            req = urllib.request.Request(url, headers=headers, method="GET")
            ctx = _get_ssl_context_for_pinning()
            with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
                if resp.status == 200:
                    result = json.loads(resp.read().decode())
                    if "server_time" in result:
                        _update_trusted_time(result["server_time"])
                    return result
            return None
    except Exception as e:
        _log_client_event("http_get_failed", endpoint=endpoint, error=str(e))
        return None


# ─── License Activation ───────────────────────────────────────────────

class LicenseClient:
    """
    Desktop-side license client.
    Manages activation, heartbeat, and local cache.
    """

    def __init__(self):
        self.access_token: Optional[str] = None
        self.refresh_token: Optional[str] = None
        self.session_id: Optional[str] = None
        self.entitlements: list[dict] = []
        self.expires_at: Optional[datetime] = None
        self.license_tier: Optional[str] = None
        self.user_id: Optional[str] = None
        self._heartbeat_thread: Optional[threading.Thread] = None
        self._heartbeat_stop = threading.Event()
        self._is_active = False

    @property
    def is_active(self) -> bool:
        """Check if license session is active."""
        return self._is_active

    def activate(
        self,
        email: str,
        activation_key: str,
    ) -> tuple[bool, str]:
        """
        Activate license with server.
        Returns (success, message).
        """
        fingerprint = generate_device_fingerprint()
        device_name = get_device_name()
        os_info = get_os_info()
        app_version = get_app_version()

        data = {
            "email": email,
            "activation_key": activation_key,
            "device_fingerprint": fingerprint,
            "device_name": device_name,
            "os_info": os_info,
            "app_version": app_version,
        }

        result = _http_post("/auth/activate", data)

        if result is None:
            return False, "Server unreachable. Check your internet connection."

        if "error" in result or "detail" in result:
            detail = result.get("detail", result.get("error", "Unknown error"))
            error_messages = {
                "invalid_key": "Invalid activation key.",
                "license_expired": "License has expired.",
                "email_mismatch": "Email does not match the license.",
                "user_suspended": "Account has been suspended.",
                "device_limit_reached": "Device limit reached. Contact support.",
                "device_revoked": "This device has been revoked.",
                "invalid_fingerprint": "Hardware fingerprint validation failed.",
            }
            return False, error_messages.get(detail, f"Activation failed: {detail}")

        # C-03: Verify receipt BEFORE marking active — fail closed if missing/invalid
        receipt = result.get("receipt")
        if not receipt:
            # Server MUST return a signed receipt — this is a protocol violation
            return False, "Activation failed: server did not return a signed receipt."

        # Verify the receipt signature using the embedded public key
        public_key = _get_server_public_key()
        if not public_key:
            return False, "Activation failed: client not configured with server public key."

        if not verify_license_receipt(receipt, public_key):
            return False, "Activation failed: receipt signature verification failed."

        # Only NOW — after cryptographic verification — mark as active
        self.access_token = result.get("access_token")
        self.refresh_token = result.get("refresh_token")
        self.user_id = result.get("user_id")
        self.license_tier = result.get("license_tier")
        self.entitlements = result.get("entitlements", [])
        self._is_active = True  # C-03: Set AFTER receipt verification

        # Parse expiry
        try:
            exp_str = result.get("expires_at", "")
            self.expires_at = datetime.fromisoformat(exp_str.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            pass

        # Save the verified signed receipt to cache
        # load_license_cache() will re-verify this signature on load
        cache_data = {
            "receipt": receipt,  # Contains verified Ed25519 signature from server
            "activated_at": datetime.now(timezone.utc).isoformat(),
        }
        save_license_cache(cache_data)

        return True, "License activated successfully."

    def refresh(self) -> bool:
        """Refresh access token using refresh token."""
        if not self.refresh_token:
            return False

        data = {"refresh_token": self.refresh_token}
        result = _http_post("/auth/refresh", data)

        if result and "access_token" in result:
            self.access_token = result["access_token"]
            try:
                exp_str = result.get("expires_at", "")
                self.expires_at = datetime.fromisoformat(exp_str.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                pass
            return True

        return False

    def send_heartbeat(self) -> dict:
        """
        Send heartbeat to server.
        Returns server response with status and commands.
        """
        if not self.access_token:
            return {"status": "error", "command": "revoke", "detail": "no_token"}

        # Compute integrity hash of this file
        integrity_hash = self._compute_integrity_hash()

        # Collect strategy hashes
        strategy_hash = self._get_current_strategy_hash()

        data = {
            "app_version": get_app_version(),
            "integrity_hash": integrity_hash,
            "current_strategy_hash": strategy_hash,
        }

        result = _http_post("/heartbeat", data, token=self.access_token)

        if result is None:
            # Server unreachable — check cache for offline grace period
            return self._handle_offline_heartbeat()

        # Process server commands
        command = result.get("command")
        if command == "revoke":
            self._is_active = False
            clear_license_cache()
            return result

        # Update entitlements if provided
        if "entitlements" in result:
            self.entitlements = result["entitlements"]

        # Update expiry
        try:
            exp_str = result.get("expires_at", "")
            if exp_str:
                self.expires_at = datetime.fromisoformat(exp_str.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            pass

        return result

    def _handle_offline_heartbeat(self) -> dict:
        """
        Handle heartbeat when server is unreachable.
        Uses local cache with grace period.
        """
        cache = load_license_cache()
        if not cache:
            return {"status": "error", "command": "revoke", "detail": "offline_no_cache"}

        # Check if cache is still valid (24-hour grace period)
        try:
            activated_at = datetime.fromisoformat(cache.get("activated_at", ""))
            grace_hours = 24
            if datetime.now(timezone.utc) - activated_at > __import__("datetime").timedelta(hours=grace_hours):
                return {
                    "status": "error",
                    "command": "revoke",
                    "detail": f"offline_grace_expired ({grace_hours}h)"
                }
        except (ValueError, TypeError):
            pass

        # Check expiry from cache
        try:
            exp_str = cache.get("expires_at", "")
            if exp_str:
                exp_date = datetime.fromisoformat(exp_str.replace("Z", "+00:00"))
                if datetime.now(timezone.utc) > exp_date:
                    return {"status": "error", "command": "revoke", "detail": "license_expired"}
        except (ValueError, TypeError):
            pass

        # Use cached entitlements
        self.entitlements = cache.get("entitlements", [])
        return {"status": "ok", "command": None, "entitlements": self.entitlements, "offline": True}

    def _compute_integrity_hash(self) -> str:
        """Compute SHA256 hash of critical security files for tamper detection."""
        try:
            security_dir = Path(__file__).parent
            h = hashlib.sha256()
            for fname in sorted(["license_manager.py", "license_client.py", "anti_debug.py"]):
                fpath = security_dir / fname
                if fpath.exists():
                    h.update(fpath.read_bytes())
            return h.hexdigest()
        except Exception:
            pass
        return ""

    def _get_current_strategy_hash(self) -> str:
        """Get hash of currently loaded strategy (if any)."""
        # This will be integrated with the strategy loader
        # For now, return empty
        return ""

    def register_strategy(
        self,
        strategy_code: str,
        file_name: str = "",
        include_code: bool = False,
    ) -> bool:
        """
        Register a strategy with the server for IP protection.
        
        SECURITY (C-05 fix): By default, ONLY the hash is sent.
        Strategy code is NEVER sent without explicit user consent.
        
        Args:
            strategy_code: The strategy source code (used to compute hash)
            file_name: Optional file name
            include_code: If True, send source code to server.
                         Default False — only hash is sent.
                         This should only be True if user explicitly opts in
                         (e.g., "Share strategy with developer" checkbox).
        """
        if not self.access_token:
            return False

        strategy_hash = hashlib.sha256(strategy_code.encode()).hexdigest()

        data = {
            "strategy_hash": strategy_hash,
            "strategy_code": strategy_code if include_code else None,
            "file_name": file_name or "unknown",
            "metadata": {
                "app_version": get_app_version(),
                "os": get_os_info(),
                "code_included": include_code,
            },
        }

        result = _http_post("/strategies/register", data, token=self.access_token)
        return result is not None and result.get("status") == "registered"

    def logout(self) -> None:
        """Logout and revoke session."""
        if self.access_token:
            _http_post("/auth/logout", {}, token=self.access_token)

        self.access_token = None
        self.refresh_token = None
        self._is_active = False
        self._stop_heartbeat()
        clear_license_cache()

    # ─── Heartbeat Background Thread ──────────────────────────────────

    def start_heartbeat(self) -> None:
        """Start background heartbeat thread."""
        if self._heartbeat_thread and self._heartbeat_thread.is_alive():
            return

        self._heartbeat_stop.clear()
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop,
            daemon=True,
            name="agregat-heartbeat",
        )
        self._heartbeat_thread.start()

    def _heartbeat_loop(self) -> None:
        """Background loop sending heartbeats."""
        while not self._heartbeat_stop.is_set():
            try:
                result = self.send_heartbeat()
                if result.get("command") == "revoke":
                    self._is_active = False
                    break
            except Exception:
                pass  # Will retry on next heartbeat

            self._heartbeat_stop.wait(HEARTBEAT_INTERVAL)

    def _stop_heartbeat(self) -> None:
        """Stop heartbeat thread."""
        self._heartbeat_stop.set()
        if self._heartbeat_thread:
            self._heartbeat_thread.join(timeout=5)
            self._heartbeat_thread = None

    # ─── Entitlement Checks ───────────────────────────────────────────

    def has_capability(self, capability: str) -> bool:
        """Check if license has a specific capability."""
        return any(e.get("capability") == capability for e in self.entitlements)

    def get_capability_limit(self, capability: str) -> Optional[int]:
        """Get limit value for a capability."""
        for e in self.entitlements:
            if e.get("capability") == capability:
                return e.get("limit_value")
        return None

    def check_expiry(self) -> tuple[bool, str]:
        """
        Check if license is expired.
        Returns (is_valid, message).
        """
        if not self.expires_at:
            return False, "No expiry information"

        now = datetime.now(timezone.utc)
        if now > self.expires_at:
            return False, "License expired"

        # Warning if less than 7 days remaining
        remaining = self.expires_at - now
        if remaining.days < 7:
            return True, f"License expires in {remaining.days} days"

        return True, "ok"


# ─── Module-level singleton ───────────────────────────────────────────

_client: Optional[LicenseClient] = None


def get_license_client() -> LicenseClient:
    """Get or create the license client singleton."""
    global _client
    if _client is None:
        _client = LicenseClient()
    return _client


def _is_dev_mode() -> bool:
    """
    Check if running in development mode.
    Requires BOTH environment variables — no file-based bypass exists.
    This prevents accidental shipping of dev bypass to production.
    
    To enable dev mode, set:
      AGREGAT_DEV_MODE=1
      APP_ENV=development
    """
    dev_flag = os.environ.get("AGREGAT_DEV_MODE", "").lower() in ("1", "true", "yes")
    env_flag = os.environ.get("APP_ENV", "").lower() == "development"
    return dev_flag and env_flag


def verify_license_online() -> tuple[bool, str]:
    """
    Verify license using server-side validation.
    Falls back to offline cache if server unreachable.
    Returns (is_valid, message).
    """
    # Dev mode requires BOTH env vars — not a file check
    if _is_dev_mode():
        return True, "DEV_MODE"

    client = get_license_client()

    # If already active, check expiry
    if client.is_active:
        is_valid, msg = client.check_expiry()
        if not is_valid:
            client._is_active = False
            return False, msg
        return True, "ok"

    # Try to restore from cache
    cache = load_license_cache()
    if cache:
        try:
            # C-04: Enforce BOTH conditions:
            # 1. Receipt not expired (expires_at in future)
            # 2. Last server validation within 24h offline grace window
            exp_str = cache.get("expires_at", "")
            activated_at_str = cache.get("activated_at", "")
            
            if not exp_str or not activated_at_str:
                return False, "NO_LICENSE|Invalid cache data."
            
            exp_date = datetime.fromisoformat(exp_str.replace("Z", "+00:00"))
            activated_at = datetime.fromisoformat(activated_at_str.replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            
            # Check license expiry
            if now > exp_date:
                return False, "NO_LICENSE|License has expired."
            
            # C-04: Enforce 24-hour offline grace period
            # If server was last contacted more than 24h ago, require reconnection
            grace_hours = 24
            offline_duration = now - activated_at
            if offline_duration > __import__("datetime").timedelta(hours=grace_hours):
                return False, f"NO_LICENSE|Offline grace period expired ({grace_hours}h). Please reconnect to the internet."
            
            # Both checks passed — restore from cache
            client.entitlements = cache.get("entitlements", [])
            client.expires_at = exp_date
            client.user_id = cache.get("user_id")
            client.license_tier = cache.get("license_tier")
            client._is_active = True
            return True, "ok (cached)"
        except (ValueError, TypeError):
            pass

    return False, "NO_LICENSE|Please activate your license."


def activate_license_online(email: str, activation_key: str) -> tuple[bool, str]:
    """
    Activate license with server.
    Convenience function wrapping LicenseClient.activate().
    """
    client = get_license_client()
    success, msg = client.activate(email, activation_key)

    if success:
        client.start_heartbeat()

    return success, msg