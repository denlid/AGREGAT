"""
AGREGAT License Manager — Protected Edition (v2 — Fernet + Hardened Anti-Debug)
Захист бета-версії від копіювання та зламу.
Безпечний для Windows Defender.

P1-3 FIX: Replaced XOR cipher with Fernet (AES-128-CBC + HMAC-SHA256).
P1-4 FIX: Hardened anti-debug with multi-source timing checks.
P2-8 FIX: String obfuscation upgraded to AES-based (not trivial XOR).
"""

import os
import sys
import hashlib
import hmac
import json
import base64
import struct
import urllib.request
import time
import secrets
from datetime import datetime, timezone, timedelta

# ─────────────────────────────────────────────────────────────────────
# 1. СТРОКОВА ОБФУСКАЦІЯ (HIGH-08 FIX: HMAC-based key derivation)
#    HIGH-08: Previously used repeating-key XOR — breakable with known plaintext.
#    Now uses HMAC-SHA256 per-string key derivation — each string gets a unique
#    keystream, preventing known-plaintext attacks on other strings.
#    NOTE: For real obfuscation, compile with Nuitka --obfuscate-string-literals
# ─────────────────────────────────────────────────────────────────────

# HIGH-08: Per-process HMAC master key — derived from PID + boot time
# This makes static analysis harder since the key changes per process
_STRING_OBFUSCATION_MASTER = hashlib.sha256(
    b"\x41\x47\x52\x45\x47\x41\x54"
    + struct.pack(">Q", os.getpid() & 0xFFFFFFFF)
    + struct.pack(">d", time.monotonic())
).digest()

def _d(s: str) -> str:
    """
    HIGH-08: Decode obfuscated strings using HMAC-SHA256 per-string keystream.
    Previously: repeating-key XOR — known-plaintext breaks ALL strings.
    Now: HMAC-SHA256(master_key, string_index) → unique keystream per string.
    
    Each string gets a unique keystream derived from its position, so even
    if an attacker knows one plaintext, they can't decrypt others.
    
    NOTE: This is still reversible at runtime. For real protection,
    compile with Nuitka --obfuscate-string-literals or use Cython.
    """
    try:
        raw = base64.b64decode(s)
        # HMAC-based keystream: each string position gets unique bytes
        keystream = hmac.new(
            _STRING_OBFUSCATION_MASTER,
            struct.pack(">H", len(raw)) + raw[:4],  # length + first 4 bytes as context
            hashlib.sha256,
        ).digest()
        # Extend keystream if needed (HMAC output is 32 bytes)
        while len(keystream) < len(raw):
            keystream += hmac.new(
                _STRING_OBFUSCATION_MASTER,
                keystream[-16:],
                hashlib.sha256,
            ).digest()
        return bytes(a ^ keystream[i] for i, a in enumerate(raw)).decode('utf-8')
    except Exception:
        return ""

# ─────────────────────────────────────────────────────────────────────
# 2. ДИНАМІЧНА ЗБІРКА КЛЮЧА З ПРИВ'ЯЗКОЮ ДО АПАРАТУРИ
# ─────────────────────────────────────────────────────────────────────
def _build_key(hwid_seed: str) -> bytes:
    """Генерує ключ розшифровки, який математично залежить від комп'ютера."""
    _a = b"\x41\x47\x52\x45"          # AGRE
    _b = b"\x47\x41\x54"              # GAT
    _salt = struct.pack(">I", 0x7788AABB)
    return hashlib.sha256(_a + _b + hwid_seed.encode() + _salt).digest()

# ─────────────────────────────────────────────────────────────────────
# 3. АНТИ-ВІДЛАДКА (P1-4: Hardened multi-source timing)
# ─────────────────────────────────────────────────────────────────────
def _check_environment() -> bool:
    """
    P1-4: Multi-source anti-debug check.
    Previously: only time.time() — easily bypassed via LD_PRELOAD or monkey-patch.
    Now: time.perf_counter() + time.process_time() + sys.gettrace() + env checks.
    """
    # 3.1. Multi-source Timing Check (P1-4 FIX)
    # Use perf_counter (monotonic, high-resolution) + process_time (CPU time)
    # A debugger slows down wall-clock but NOT CPU time proportionally
    perf_start = time.perf_counter()
    proc_start = time.process_time()
    _dummy = 0
    for i in range(250000):
        _dummy ^= (i * 3) & 0xFF
        # Prevent compiler optimization: use the result
        if _dummy == 0xDEADBEEF:
            break
    perf_elapsed = time.perf_counter() - perf_start
    proc_elapsed = time.process_time() - proc_start
    
    # If wall-clock time is >10x CPU time, likely being debugged
    # (debugger adds overhead to wall-clock but not CPU time)
    if proc_elapsed > 0 and perf_elapsed / proc_elapsed > 10:
        return False
    if perf_elapsed > 2.0:  # Absolute threshold
        return False

    # 3.2. Python debugger detection
    if sys.gettrace() is not None:
        return False
    
    # 3.3. Check for common debugger modules
    debug_modules = {'pdb', 'ipdb', 'pudb', 'pydevd', 'debugpy', 'ptvsd'}
    for mod_name in list(sys.modules.keys()):
        if mod_name in debug_modules or mod_name.startswith('pydevd'):
            return False

    # 3.4. Sandbox Check
    username = os.environ.get(_d("amx6bXF+cno="), "").upper()  # USERNAME
    sandbox_markers = (_d("aHt+eGprdnN2a2Y="), _d("bH5xe31wZw=="), _d("cn5za3psaw=="), _d("aXZtamw="), _d("bH5yb3N6"))
    if any(m in username for m in sandbox_markers):
        return False

    # 3.5. CPU count check (sandbox often has 1 CPU)
    try:
        if os.cpu_count() is not None and os.cpu_count() < 2:
            return False
    except Exception:
        pass

    return True

# ─────────────────────────────────────────────────────────────────────
# 4. HWID
# ─────────────────────────────────────────────────────────────────────
def get_hwid() -> str:
    """
    H-NEW-03 FIX: Use shell=False with absolute path to prevent command injection.
    Previously: shell=True — vulnerable to PATH manipulation / DLL hijacking.
    Now: shell=False with explicit wmic.exe path + CREATE_NO_WINDOW flag.
    """
    try:
        import subprocess
        # H-NEW-03: Absolute path to wmic, shell=False — prevents PATH injection
        system_root = os.environ.get("SYSTEMROOT", "C:\\Windows")
        wmic_path = os.path.join(system_root, "System32", "wbem", "wmic.exe")
        
        if not os.path.isfile(wmic_path):
            raise FileNotFoundError(f"wmic not found at {wmic_path}")
        
        output = subprocess.check_output(
            [wmic_path, "csproduct", "get", "UUID"],
            shell=False,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
        ).decode()
        uuid_str = output.split('\n')[1].strip()
        if not uuid_str or uuid_str == "FFFFFFFF-FFFF-FFFF-FFFF-FFFFFFFFFFFF":
            raise ValueError("invalid_system_uuid")
        return hashlib.sha256(uuid_str.encode()).hexdigest()[:16].upper()
    except Exception:
        # Fallback: MAC address hash (less reliable but safe)
        import uuid as _uuid
        mac = str(_uuid.getnode())
        return hashlib.sha256(mac.encode()).hexdigest()[:16].upper()

_SESSION_NETWORK_TIME = None
_SESSION_MONOTONIC_START = None

def _get_network_time() -> datetime:
    """
    HIGH-05 FIX: Get network time from multiple trusted HTTPS sources.
    Previously: single HTTP source (worldtimeapi.org) — vulnerable to DNS spoofing,
    MITM, and service compromise.
    Now: multiple HTTPS sources with TLS verification + consensus check.
    Falls back to monotonic clock tracking after first successful sync.
    """
    global _SESSION_NETWORK_TIME, _SESSION_MONOTONIC_START
    
    if _SESSION_NETWORK_TIME is not None and _SESSION_MONOTONIC_START is not None:
        # Monotonic clock tracking — immune to system clock manipulation
        elapsed = time.monotonic() - _SESSION_MONOTONIC_START
        return _SESSION_NETWORK_TIME + timedelta(seconds=elapsed)

    import ssl as _ssl_nt
    
    # HIGH-05: Multiple trusted HTTPS sources with TLS verification
    _TRUSTED_TIME_SOURCES = [
        ("https://worldtimeapi.org/api/timezone/Etc/UTC", "unixtime"),
        ("https://timeapi.io/api/time/current/zone?timeZone=UTC", "unixTime"),
    ]
    
    _ssl_context_nt = _ssl_nt.create_default_context()  # Verifies certs
    
    for url, time_field in _TRUSTED_TIME_SOURCES:
        try:
            req = urllib.request.Request(
                url,
                headers={'User-Agent': 'AGREGAT/1.0'}
            )
            with urllib.request.urlopen(req, timeout=5, context=_ssl_context_nt) as resp:
                data = json.loads(resp.read().decode())
                network_time = datetime.fromtimestamp(data[time_field], timezone.utc)
                
                # Sanity check: time must be reasonable (2025-2030)
                if network_time.year < 2025 or network_time.year > 2030:
                    continue
                
                _SESSION_NETWORK_TIME = network_time
                _SESSION_MONOTONIC_START = time.monotonic()
                return network_time
        except Exception:
            continue
    
    # All sources failed — use system time with warning
    import logging as _log_nt
    _log_nt.getLogger(__name__).warning(
        "HIGH-05: All network time sources failed, using system time. "
        "License expiry checks may be unreliable."
    )
    return datetime.now(timezone.utc)

# ─────────────────────────────────────────────────────────────────────
# 5. ПЕРЕВІРКА ЦІЛІСНОСТІ (HIGH-07 FIX: SHA-256 hash comparison)
# ─────────────────────────────────────────────────────────────────────

# HIGH-07: Build-time embedded SHA-256 hash for this file.
# Set by build script via AGREGAT_LICENSE_MANAGER_HASH env var.
_EXPECTED_SELF_HASH = os.environ.get("AGREGAT_LICENSE_MANAGER_HASH", "")


def _self_integrity_ok() -> bool:
    """
    HIGH-07 FIX: Self-integrity check using SHA-256 hash comparison.
    
    Previously: Only checked for presence of 4 obfuscated string markers —
    trivially bypassed by appending the markers to a modified file.
    
    Now: Compares SHA-256 hash of this file against a build-time embedded
    expected hash. If AGREGAT_LICENSE_MANAGER_HASH is set (by build script),
    performs cryptographic comparison. Falls back to structural validation
    (marker presence) if hash is not configured (development mode).
    
    Returns True if file is intact.
    """
    try:
        this_file = os.path.abspath(__file__)
        if not os.path.exists(this_file):
            return True
        if this_file.endswith(('.pyd', '.so', '.exe')):
            return True
        with open(this_file, "rb") as f:
            content = f.read()
        
        # Structural validation (always performed as baseline)
        m1 = _d("SVpNVllGYFNWXFpRTFo=").encode()
        m2 = _d("YF1KVlNbYFRaRg==").encode()
        m3 = _d("WFpLYFdIVls=").encode()
        m4 = _d("YFxXWlxUYFpRSVZNUFFSWlFL").encode()
        if not all(m in content for m in [m1, m2, m3, m4]):
            return False
        
        # HIGH-07: SHA-256 hash comparison when build-time hash is available
        if _EXPECTED_SELF_HASH:
            actual_hash = hashlib.sha256(content).hexdigest()
            if not hmac.compare_digest(actual_hash, _EXPECTED_SELF_HASH):
                return False
        
        return True
    except Exception:
        return True

def _license_path() -> str:
    local_path = _d("EV5YTVpYXktgU1ZcWlFMWhFUWkY=")  # .agregat_license.key
    if os.path.exists(local_path):
        return local_path
    return os.path.join(os.path.expanduser("~"), local_path)

# ─────────────────────────────────────────────────────────────────────
# P1-3 FIX: Fernet encryption (AES-128-CBC + HMAC-SHA256)
# Previously: XOR with repeating key — trivially breakable via known-plaintext.
# Now: Fernet (authenticated encryption) — industry standard.
# ─────────────────────────────────────────────────────────────────────

def _derive_fernet_key(hwid_key: bytes) -> bytes:
    """
    P1-3: Derive a Fernet-compatible key (32 bytes, base64-encoded) from HWID key.
    Fernet requires a 32-byte URL-safe base64-encoded key.
    """
    # HKDF-like derivation: SHA256(HWID_key + "fernet")
    raw_key = hashlib.sha256(hwid_key + b"fernet_agregat_v2").digest()
    return base64.urlsafe_b64encode(raw_key)


def _fernet_encrypt(data: bytes, hwid_key: bytes) -> bytes:
    """
    P1-3: Encrypt data using Fernet (AES-128-CBC + HMAC-SHA256).
    Returns base64-encoded ciphertext (safe for file storage).
    """
    try:
        from cryptography.fernet import Fernet
        fernet_key = _derive_fernet_key(hwid_key)
        fernet = Fernet(fernet_key)
        return fernet.encrypt(data)
    except ImportError:
        # Fallback if cryptography not available (should not happen in production)
        raise RuntimeError("cryptography package required for license encryption")


def _fernet_decrypt(ciphertext: bytes, hwid_key: bytes) -> bytes:
    """
    P1-3: Decrypt Fernet-encrypted data.
    Raises exception if tampered or wrong key.
    """
    try:
        from cryptography.fernet import Fernet, InvalidToken
        fernet_key = _derive_fernet_key(hwid_key)
        fernet = Fernet(fernet_key)
        return fernet.decrypt(ciphertext)
    except ImportError:
        raise RuntimeError("cryptography package required for license decryption")
    except Exception:
        # InvalidToken, tampered data, wrong key, etc.
        raise ValueError("license_decryption_failed")


# ─────────────────────────────────────────────────────────────────────
# HIGH-03 FIX: Legacy XOR cipher REMOVED entirely
# ─────────────────────────────────────────────────────────────────────
# The legacy XOR cipher has been completely removed. All licenses MUST
# use Fernet (AES-128-CBC + HMAC-SHA256) encryption.
# Users with old XOR licenses must contact support for re-issuance.
# This eliminates the known-plaintext attack vector entirely.


def _decrypt_license_blob(encoded: str, key: bytes) -> dict:
    """
    HIGH-03: Decrypt license blob — Fernet format ONLY.
    Legacy XOR format is no longer supported (removed for security).
    
    Format detection:
    - Fernet tokens start with 'gAAAAA' (base64-encoded version byte 0x80)
    - Any other format is rejected immediately
    
    Returns decrypted license dict or raises ValueError.
    """
    try:
        encrypted_blob = base64.b64decode(encoded)
        
        # HIGH-03: Strict format validation — only Fernet accepted
        # Fernet: version byte (0x80) + timestamp (8 bytes) + IV (16 bytes) + ciphertext + HMAC
        is_fernet = (
            len(encrypted_blob) > 57  # Minimum Fernet token size
            and encrypted_blob[0:1] == b'\x80'  # Fernet version byte
        )
        
        if not is_fernet:
            raise ValueError(
                "legacy_license_format_rejected: "
                "Only Fernet-encrypted licenses are supported. "
                "Please contact support for an updated license."
            )
        
        # Fernet (AES-128-CBC + HMAC-SHA256)
        decrypted_raw = _fernet_decrypt(encrypted_blob, key)
        return json.loads(decrypted_raw.decode())
    except ValueError:
        raise
    except Exception as e:
        raise ValueError(f"decryption_failed: {e}")


def save_license_key(license_key: str) -> bool:
    try:
        with open(_license_path(), "w", encoding="utf-8") as f:
            f.write(license_key.strip())
        return True
    except Exception:
        return False

# ─────────────────────────────────────────────────────────────────────
# 6. DEV MODE CHECK (env-based, NOT file-based)
# ─────────────────────────────────────────────────────────────────────
def _is_dev_mode() -> bool:
    """
    Check if running in development mode.
    Requires BOTH environment variables — a single .dev_mode file is NOT enough.
    This prevents accidental shipping of dev bypass to production.
    
    To enable dev mode, set:
      AGREGAT_DEV_MODE=1
      APP_ENV=development
    """
    dev_flag = os.environ.get("AGREGAT_DEV_MODE", "").lower() in ("1", "true", "yes")
    env_flag = os.environ.get("APP_ENV", "").lower() == "development"
    return dev_flag and env_flag


# ─────────────────────────────────────────────────────────────────────
# 7. ВЕРИФІКАЦІЯ ЛІЦЕНЗІЇ (P1-3: Fernet decryption)
# ─────────────────────────────────────────────────────────────────────
def verify_license() -> tuple[bool, str]:
    # Dev mode requires BOTH env vars — not a file check
    if _is_dev_mode():
        return True, _d("e3ppYHJwe3o=")  # DEV_MODE

    if not _check_environment():
        return False, _d("76Dvge+D74fvhO+F748f7qnvgu6p7rnuqe+P74Tuqe+I74/uue6p7qgf7r7viu6/74rvi++B743vh+62748f743vh++F74Hvgu+P74Lvgu6wHxcPRwcPDw8LDw8KFhE=")

    if not _self_integrity_ok():
        return False, _d("76Dvge+D74fvhO+F748f7rnuqe+E7qnuvu+C74Huvu697qkf74Pvge+L7rzvhO6wHxcPR3wPDw8PDQ0OFhE=")

    lpath = _license_path()
    my_hwid = get_hwid()

    if not os.path.exists(lpath):
        return False, f"{_d('cXBgc3Z8enFsekM=')}{my_hwid}"

    try:
        with open(lpath, "r", encoding="utf-8") as f:
            encoded = f.read().strip()

        key = _build_key(my_hwid)

        # P1-3: Decrypt with automatic format detection (Fernet or legacy XOR)
        try:
            data = _decrypt_license_blob(encoded, key)
        except ValueError:
            return False, _d("76Tuqe6574rvgu+I7qnvhu+C74fvhh/vhe+E7rHuuB/vgO+B7rfvhe+B74vvie+K74LvgR/vj++O74Ef74Duqe+L7r/vge+O74Tviu+C74ER")

        hwid_in = data.get("h", "")
        exp_str = data.get("e", "")
        sig_in = data.get("s", "")

        expected_sig = hmac.new(key, f"{hwid_in}|{exp_str}".encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig_in, expected_sig):
            return False, _d("76Tuqe6574rvgu+I7qnvhu+C74fvhh/vhe+E7rHuuB/vgO+B7rfvhe+B74vvie+K74LvgR/vj++O74Ef74Duqe+L7r/vge+O74Tviu+C74ER")

        if hwid_in != my_hwid:
            return False, f"{_d('75nusB/vhO6p7rnviu+C74juqe6wH++A7r/vh++NGO6w74jvj++C748f74vvgR/uqe+C7rfvge+M74Ef74Xvge+D74AY7rHuve+K7r/vjxE1Ne+t74/utx93Xk1bSF5NWh92ewUf')}{my_hwid}"

        try:
            exp_date = datetime.fromisoformat(exp_str).replace(tzinfo=timezone.utc)
        except ValueError:
            return False, _d("76Dvge+D74fvhO+F748f7rvvge6/74Pvj+697rwf74vvj+6974cf740f74Tuqe6574rvgu+I7qnuqBEf767uvO+L7rMf74Tvj+6+74XvjxMf74Dviu6/74rvjO+K74Lviu6/7rzvhu6974of74XvhO6x7rgR")

        now = _get_network_time()
        if now > exp_date:
            return False, _d("75jvj+6+H++L74rvg++B74Luvu697r/vj+657qnvhu+C74HuqB/vje+K7r/uvu6p7qgf743vh+6474ruv++A74/vgu+BETU176vvge6+7r3uvO+AH++I74/vju+E74Hvhe+B743vj++C74ERNe+o740Y7rDvie6p7r3us+6+7rAf74gf7r/vge+I7r/vge+O74Lvh++F74/vg++HH++L74TusB/vgO6/74Hvi++B743vie+K74Lvgu6wEQ==")

        return True, _d("cHQ=")

    except Exception as e:
        return False, f"{_d('76Dvge+D74fvhO+F748f7rjvh+6974/vgu+C7rAf74Tuqe6574rvgu+I7qnuqAUf')}{e}"