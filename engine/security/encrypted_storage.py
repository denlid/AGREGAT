"""
AGREGAT Encrypted Storage Module

E2E-02: Fernet (AES-128-CBC) encrypted local storage with hardware-bound key.

All cached data (license, settings, tokens) is encrypted at rest using
Fernet symmetric encryption. The encryption key is derived from:
  - Hardware fingerprint (CPU + disk + MAC)
  - User-specific salt
  - Application secret (embedded in binary)

This prevents:
- Offline data theft (copying ~/.agregat/ to another machine)
- Cache file tampering (Fernet includes HMAC authentication)
- Forensic analysis of cached data

On Windows, additionally uses DPAPI (CryptProtectData) for the master key,
binding it to the user's Windows login credentials.

Architecture:
  master_key = HKDF(hw_fingerprint + app_secret + user_salt)
  [Windows only] master_key = DPAPI(CryptProtectData(master_key))
  fernet = Fernet(master_key)
  encrypted_data = fernet.encrypt(plaintext)
"""

import base64
import hashlib
import hmac
import json
import os
import platform
import sys
import time
from pathlib import Path
from typing import Optional

try:
    from cryptography.fernet import Fernet, InvalidToken
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    from cryptography.hazmat.primitives import hashes
    HAS_CRYPTOGRAPHY = True
except ImportError:
    HAS_CRYPTOGRAPHY = False


# ─── Configuration ─────────────────────────────────────────────────────

# CRIT-04: Application-level secret — MUST be set via environment variable.
# The previous hardcoded fallback (b"AGREGAT_SECRET_KEY") was trivially
# extractable from compiled binaries using `strings` or a hex editor.
# Now FAIL-CLOSED: production startup blocked without AGREGAT_STORAGE_SECRET.
_APP_SECRET_ENV = os.environ.get("AGREGAT_STORAGE_SECRET", "")
_APP_ENV_MODE = os.environ.get("APP_ENV", "production").lower()

if not _APP_SECRET_ENV:
    if _APP_ENV_MODE == "production":
        raise RuntimeError(
            "CRIT-04: AGREGAT_STORAGE_SECRET environment variable is required in production. "
            "Generate with: python -c 'import secrets; print(secrets.token_hex(32))' "
            "and set it in your environment before starting the application."
        )
    # Development-only fallback: derived from machine-specific data
    # This is intentionally NOT portable across machines
    import uuid as _uuid_dev
    _APP_SECRET = hashlib.sha256(
        f"DEV_FALLBACK_{platform.node()}_{_uuid_dev.getnode()}".encode()
    ).digest()
else:
    _APP_SECRET = _APP_SECRET_ENV.encode()

# Storage directory
STORAGE_DIR = Path.home() / ".agregat"

# Key derivation salt (unique per installation context)
_KEY_SALT = b"\x77\x88\xAA\xBB\xCC\xDD\xEE\xFF"


# ─── DPAPI (Windows CryptProtectData) ─────────────────────────────────

def _dpapi_protect(data: bytes) -> Optional[bytes]:
    """
    E2E-02: Encrypt data using Windows DPAPI (CryptProtectData).
    
    Binds the encrypted data to the current user's Windows credentials.
    Only the same user on the same machine can decrypt it.
    
    Returns encrypted bytes or None on failure/non-Windows.
    """
    if sys.platform != "win32":
        return None
    
    try:
        import ctypes
        import ctypes.wintypes
        
        class DATA_BLOB(ctypes.Structure):
            _fields_ = [
                ("cbData", ctypes.wintypes.DWORD),
                ("pbData", ctypes.c_void_p),
            ]
        
        input_data = DATA_BLOB()
        input_data.cbData = len(data)
        input_data.pbData = ctypes.cast(data, ctypes.c_void_p)
        
        output_data = DATA_BLOB()
        
        # P2-9: CRYPTPROTECT_UI_FORBIDDEN = don't show UI prompts
        # REMOVED CRYPTPROTECT_LOCAL_MACHINE — binds to user credentials instead
        # of machine, providing better security (requires user login to decrypt)
        CRYPTPROTECT_UI_FORBIDDEN = 0x1
        
        result = ctypes.windll.crypt32.CryptProtectData(
            ctypes.byref(input_data),
            None,  # Description
            None,  # Optional entropy
            None,  # Reserved
            None,  # Prompt struct
            CRYPTPROTECT_UI_FORBIDDEN,  # P2-9: User-bound, not machine-bound
            ctypes.byref(output_data),
        )
        
        if result:
            encrypted = ctypes.string_at(output_data.pbData, output_data.cbData)
            ctypes.windll.kernel32.LocalFree(output_data.pbData)
            return encrypted
        
        return None
    except Exception:
        return None


def _dpapi_unprotect(data: bytes) -> Optional[bytes]:
    """
    E2E-02: Decrypt data using Windows DPAPI (CryptUnprotectData).
    
    Returns decrypted bytes or None on failure.
    """
    if sys.platform != "win32":
        return None
    
    try:
        import ctypes
        import ctypes.wintypes
        
        class DATA_BLOB(ctypes.Structure):
            _fields_ = [
                ("cbData", ctypes.wintypes.DWORD),
                ("pbData", ctypes.c_void_p),
            ]
        
        input_data = DATA_BLOB()
        input_data.cbData = len(data)
        input_data.pbData = ctypes.cast(data, ctypes.c_void_p)
        
        output_data = DATA_BLOB()
        
        CRYPTPROTECT_UI_FORBIDDEN = 0x1
        
        result = ctypes.windll.crypt32.CryptUnprotectData(
            ctypes.byref(input_data),
            None,
            None,
            None,
            None,
            CRYPTPROTECT_UI_FORBIDDEN,
            ctypes.byref(output_data),
        )
        
        if result:
            decrypted = ctypes.string_at(output_data.pbData, output_data.cbData)
            ctypes.windll.kernel32.LocalFree(output_data.pbData)
            return decrypted
        
        return None
    except Exception:
        return None


# ─── Key Derivation ────────────────────────────────────────────────────

def _get_hardware_seed() -> str:
    """
    Get hardware-specific seed for key derivation.
    Uses the same fingerprint as license_client.py for consistency.
    """
    try:
        from engine.security.license_client import generate_device_fingerprint
        return generate_device_fingerprint()
    except ImportError:
        # Fallback: basic hardware info
        import uuid
        components = [
            platform.machine(),
            platform.processor(),
            str(uuid.getnode()),
            platform.node(),
        ]
        return hashlib.sha256("|".join(components).encode()).hexdigest()


def derive_master_key(user_salt: str = "") -> bytes:
    """
    Derive a 32-byte master encryption key from hardware + app secret.
    
    Key derivation chain:
      1. hw_seed = hardware_fingerprint()
      2. raw_key = HMAC-SHA256(app_secret, hw_seed + user_salt)
      3. [Windows] dpapi_key = CryptProtectData(raw_key)
      4. master_key = SHA256(raw_key) — 32 bytes for Fernet
    
    The key is bound to:
    - The specific hardware (can't copy to another machine)
    - The application secret (can't derive without binary)
    - [Windows] The user's Windows credentials (DPAPI)
    """
    hw_seed = _get_hardware_seed()
    
    # HMAC-based key derivation
    raw_key = hmac.new(
        _APP_SECRET,
        (hw_seed + user_salt).encode(),
        hashlib.sha256,
    ).digest()
    
    # On Windows, additionally protect with DPAPI
    if sys.platform == "win32":
        dpapi_protected = _dpapi_protect(raw_key)
        if dpapi_protected:
            # Hash the DPAPI output to get a consistent 32-byte key
            raw_key = hashlib.sha256(dpapi_protected).digest()
    
    return raw_key


def _make_fernet_key(raw_key: bytes) -> bytes:
    """Convert raw 32-byte key to Fernet-compatible base64 key."""
    return base64.urlsafe_b64encode(raw_key)


# ─── Encrypted Storage ─────────────────────────────────────────────────

class EncryptedStorage:
    """
    Encrypted key-value storage with hardware-bound encryption.
    
    All values are encrypted with Fernet (AES-128-CBC + HMAC-SHA256).
    The encryption key is derived from hardware fingerprint + app secret.
    
    Usage:
        storage = EncryptedStorage("license_data")
        storage.set("access_token", "eyJ...")
        token = storage.get("access_token")
    """
    
    def __init__(self, namespace: str, user_salt: str = ""):
        """
        Initialize encrypted storage.
        
        Args:
            namespace: Storage namespace (creates separate file)
            user_salt: Optional user-specific salt for key derivation
        """
        self._namespace = namespace
        self._file = STORAGE_DIR / f"{namespace}.enc"
        self._master_key = derive_master_key(user_salt)
        self._fernet_key = _make_fernet_key(self._master_key)
        self._cache: dict[str, str] = {}
        self._loaded = False
    
    def _load(self) -> None:
        """Load and decrypt storage from disk."""
        if self._loaded:
            return
        
        self._loaded = True
        
        if not self._file.exists():
            return
        
        try:
            if not HAS_CRYPTOGRAPHY:
                return
            
            fernet = Fernet(self._fernet_key)
            encrypted_data = self._file.read_bytes()
            # P2-10: TTL = 30 days for persistent storage (not session data)
            # Fernet tokens include a timestamp; old tokens are rejected.
            # 30 days balances security with usability for persistent storage.
            decrypted = fernet.decrypt(encrypted_data, ttl=2592000)
            self._cache = json.loads(decrypted.decode())
        except InvalidToken:
            # P2-11: Log decryption failure for forensic analysis
            # InvalidToken = tampered data, wrong key, or expired TTL
            import logging as _log_load
            _log_load.getLogger(__name__).warning(
                "P2-11: Encrypted storage decryption failed (namespace=%s). "
                "Possible causes: data tampering, hardware change, or expired TTL.",
                self._namespace,
            )
            self._cache = {}
        except json.JSONDecodeError:
            import logging as _log_json
            _log_json.getLogger(__name__).warning(
                "P2-11: Encrypted storage JSON decode failed (namespace=%s). "
                "Data may be corrupted.",
                self._namespace,
            )
            self._cache = {}
        except Exception as e:
            # P2-11: Log unexpected errors (don't silently swallow)
            import logging as _log_exc
            _log_exc.getLogger(__name__).error(
                "P2-11: Unexpected error loading encrypted storage (namespace=%s): %s",
                self._namespace, str(e),
            )
            self._cache = {}
    
    def _save(self) -> None:
        """Encrypt and save storage to disk."""
        if not HAS_CRYPTOGRAPHY:
            return
        
        try:
            STORAGE_DIR.mkdir(parents=True, exist_ok=True)
            fernet = Fernet(self._fernet_key)
            plaintext = json.dumps(self._cache, sort_keys=True).encode()
            encrypted = fernet.encrypt(plaintext)
            self._file.write_bytes(encrypted)
            # Restrictive permissions (owner read/write only)
            try:
                os.chmod(self._file, 0o600)
            except OSError:
                pass  # Windows handles permissions differently
        except Exception:
            pass
    
    def get(self, key: str, default: Optional[str] = None) -> Optional[str]:
        """Get a decrypted value."""
        self._load()
        return self._cache.get(key, default)
    
    def set(self, key: str, value: str) -> None:
        """Encrypt and store a value."""
        self._load()
        self._cache[key] = value
        self._save()
    
    def delete(self, key: str) -> None:
        """Delete a value."""
        self._load()
        self._cache.pop(key, None)
        self._save()
    
    def clear(self) -> None:
        """Clear all values and delete the file."""
        self._cache = {}
        self._loaded = True
        try:
            if self._file.exists():
                self._file.unlink()
        except Exception:
            pass
    
    def get_all(self) -> dict[str, str]:
        """Get all decrypted values."""
        self._load()
        return dict(self._cache)
    
    def set_all(self, data: dict[str, str]) -> None:
        """Set multiple values at once (single encrypt/save)."""
        self._load()
        self._cache.update(data)
        self._save()


# ─── Convenience Functions ─────────────────────────────────────────────

_default_storage: Optional[EncryptedStorage] = None


def get_storage(namespace: str = "default") -> EncryptedStorage:
    """Get or create an encrypted storage instance."""
    global _default_storage
    if namespace == "default" and _default_storage is None:
        _default_storage = EncryptedStorage("agregat_data")
    return _default_storage if namespace == "default" else EncryptedStorage(namespace)


def secure_store(key: str, value: str, namespace: str = "default") -> None:
    """Store an encrypted value."""
    get_storage(namespace).set(key, value)


def secure_retrieve(key: str, namespace: str = "default", default: Optional[str] = None) -> Optional[str]:
    """Retrieve a decrypted value."""
    return get_storage(namespace).get(key, default)


def secure_delete(key: str, namespace: str = "default") -> None:
    """Delete an encrypted value."""
    get_storage(namespace).delete(key)


# ─── Secure File Deletion ──────────────────────────────────────────────

def secure_delete_file(file_path: Path, passes: int = 3) -> bool:
    """
    P2-12: Securely delete a file by overwriting with random data before unlinking.
    
    WARNING: On SSDs with wear leveling, overwriting may NOT destroy the original
    data (the SSD controller may write to a new physical block). For SSDs,
    consider using full-disk encryption (BitLocker/FileVault) instead.
    
    This method is effective on HDDs but provides limited protection on SSDs.
    
    Args:
        file_path: Path to the file to delete
        passes: Number of overwrite passes (default: 3)
    
    Returns True if successfully deleted.
    """
    if not file_path.exists():
        return True
    
    try:
        file_size = file_path.stat().st_size
        
        # P2-12: Warn about SSD limitations
        import logging as _log_ssd
        _log_ssd.getLogger(__name__).debug(
            "P2-12: Secure delete on %s. Note: SSD wear leveling may prevent "
            "complete data destruction. Use full-disk encryption for SSDs.",
            file_path,
        )
        
        for i in range(passes):
            with open(file_path, "wb") as f:
                if i == 0:
                    # Pass 1: All zeros
                    f.write(b"\x00" * file_size)
                elif i == passes - 1:
                    # Last pass: All ones
                    f.write(b"\xff" * file_size)
                else:
                    # Middle passes: Random data
                    f.write(os.urandom(file_size))
                f.flush()
                os.fsync(f.fileno())
        
        # Finally unlink
        file_path.unlink()
        return True
    except Exception:
        return False
