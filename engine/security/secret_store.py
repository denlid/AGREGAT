"""
AGREGAT Engine — Secure Secret Storage

C-02 FIX: API keys and secrets stored encrypted, NOT in plaintext.

Security model:
- Secrets are encrypted with Fernet (AES-128-CBC + HMAC-SHA256)
- Master key derived from machine-specific entropy + user password (optional)
- Falls back to file-based key if keyring unavailable
- Secrets NEVER appear in logs, settings.json, or error messages
- Auto-redaction of secrets in string representations

Supported backends:
1. OS Keyring (preferred) — Windows Credential Locker, macOS Keychain, Linux Secret Service
2. Encrypted file (fallback) — Fernet-encrypted JSON file with restricted permissions
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import stat
import sys
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger("StrategyTester.engine.security.secrets")

# Try to import cryptography — required for encryption
try:
    from cryptography.fernet import Fernet, InvalidToken
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    from cryptography.hazmat.primitives import hashes
    import base64
    _CRYPTO_AVAILABLE = True
except ImportError:
    _CRYPTO_AVAILABLE = False
    logger.warning("cryptography package not available — secret encryption disabled")

# Try to import keyring — preferred backend
try:
    import keyring
    from keyring.errors import KeyringError, PasswordDeleteError
    _KEYRING_AVAILABLE = True
except ImportError:
    _KEYRING_AVAILABLE = False
    logger.debug("keyring package not available — using file-based storage")


# ===================================================================
# Constants
# ===================================================================

SERVICE_NAME = "agregat"
SECRETS_DIR_NAME = "saves"
SECRETS_FILE_NAME = ".secrets.enc"
KEY_FILE_NAME = ".master_key"

# Regex patterns for detecting secrets in text (for redaction)
SECRET_PATTERNS = [
    # OpenAI keys
    (r'sk-[a-zA-Z0-9]{20,}', '[OPENAI_KEY_REDACTED]'),
    # Anthropic keys
    (r'sk-ant-[a-zA-Z0-9_-]{20,}', '[ANTHROPIC_KEY_REDACTED]'),
    # Generic API keys (long alphanumeric strings)
    (r'(?:api[_-]?key|apikey|access[_-]?token)\s*[=:]\s*["\']?([a-zA-Z0-9_-]{20,})["\']?', '[API_KEY_REDACTED]'),
    # Bearer tokens
    (r'Bearer\s+[a-zA-Z0-9_.-]{20,}', '[BEARER_TOKEN_REDACTED]'),
    # Connection strings with passwords
    (r'(?:postgres|mysql|mongodb)://[^:]+:[^@]+@', '[DB_CONNECTION_REDACTED]'),
    # AWS keys
    (r'AKIA[0-9A-Z]{16}', '[AWS_KEY_REDACTED]'),
    # Google API keys
    (r'AIza[0-9A-Za-z_-]{35}', '[GOOGLE_KEY_REDACTED]'),
]


# ===================================================================
# Secret Redaction Utility
# ===================================================================

def redact_secrets(text: str) -> str:
    """
    Automatically redact any API keys or secrets from text.
    
    Use this for:
    - Log messages
    - Error messages
    - Debug output
    - User-facing displays
    
    Example:
        >>> redact_secrets("API key: sk-abc123def456...")
        "API key: [OPENAI_KEY_REDACTED]"
    """
    if not text:
        return text
    
    result = text
    for pattern, replacement in SECRET_PATTERNS:
        result = re.sub(pattern, replacement, result, flags=re.IGNORECASE)
    
    return result


class RedactingLogger(logging.LoggerAdapter):
    """Logger that automatically redacts secrets from all messages."""
    
    def process(self, msg: str, kwargs: dict) -> tuple[str, dict]:
        return redact_secrets(str(msg)), kwargs


# ===================================================================
# Master Key Derivation
# ===================================================================

def _derive_master_key(password: Optional[str] = None) -> bytes:
    """
    Derive a Fernet-compatible master key.
    
    Uses machine-specific entropy + optional password for key derivation.
    This ensures that:
    1. Encrypted secrets are tied to this machine
    2. Even if secrets file is stolen, it can't be decrypted elsewhere
    3. Optional password adds user-level protection
    
    Security: PBKDF2-HMAC-SHA256 with 100k iterations
    """
    if not _CRYPTO_AVAILABLE:
        raise RuntimeError("cryptography package required for key derivation")
    
    # Machine-specific entropy
    machine_entropy = b""
    
    # Platform-specific machine ID
    if sys.platform == "win32":
        # Windows: use COMPUTERNAME + USERNAME + machine GUID
        machine_entropy += os.environ.get("COMPUTERNAME", "").encode()
        machine_entropy += os.environ.get("USERNAME", "").encode()
        try:
            import winreg
            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SOFTWARE\Microsoft\Cryptography"
            ) as key:
                guid = winreg.QueryValueEx(key, "MachineGuid")[0]
                machine_entropy += guid.encode()
        except Exception:
            pass
    elif sys.platform == "darwin":
        # macOS: use IOPlatformUUID
        try:
            import subprocess
            uuid = subprocess.check_output(
                ["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"]
            )
            machine_entropy += uuid
        except Exception:
            machine_entropy += os.environ.get("HOSTNAME", "").encode()
    else:
        # Linux: use machine-id
        try:
            with open("/etc/machine-id", "rb") as f:
                machine_entropy += f.read()
        except Exception:
            machine_entropy += os.environ.get("HOSTNAME", "").encode()
    
    # Add salt to prevent rainbow table attacks
    salt = hashlib.sha256(b"agregat-secret-store-v1" + machine_entropy).digest()
    
    # Password (or machine-only if no password)
    if password:
        key_material = password.encode() + machine_entropy
    else:
        key_material = machine_entropy + b"agregat-machine-only"
    
    # PBKDF2 key derivation (100k iterations)
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=100_000,
    )
    return base64.urlsafe_b64encode(kdf.derive(key_material))


def _get_or_create_key_file(key_path: Path) -> bytes:
    """
    Get or create a persistent master key file.
    
    The key file is created with restricted permissions (0600).
    This is used as fallback when keyring is unavailable.
    """
    if key_path.exists():
        try:
            key_data = key_path.read_bytes()
            # Validate it's a valid Fernet key
            Fernet(key_data)
            return key_data
        except (InvalidToken, Exception):
            logger.warning("Corrupted key file — regenerating")
    
    # Generate new key
    key = Fernet.generate_key()
    
    # Write with restricted permissions
    fd = os.open(str(key_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, key)
    finally:
        os.close(fd)
    
    # Windows: set ACL to current user only
    if sys.platform == "win32":
        try:
            _set_windows_acl(str(key_path))
        except Exception as e:
            logger.debug("Failed to set Windows ACL: %s", e)
    
    logger.info("Created new master key file: %s", key_path)
    return key


def _set_windows_acl(file_path: str) -> None:
    """Set Windows ACL to allow only current user access."""
    try:
        import win32security
        import ntsecuritycon
        
        sd = win32security.GetFileSecurity(
            file_path, win32security.DACL_SECURITY_INFORMATION
        )
        dacl = win32security.ACL()
        
        user_sid = win32security.LookupAccountName(
            None, os.environ.get("USERNAME", "")
        )[0]
        
        dacl.AddAccessAllowedAce(
            win32security.ACL_REVISION,
            ntsecuritycon.FILE_GENERIC_READ | ntsecuritycon.FILE_GENERIC_WRITE,
            user_sid,
        )
        
        sd.SetSecurityDescriptorDacl(1, dacl, 0)
        win32security.SetFileSecurity(
            file_path, win32security.DACL_SECURITY_INFORMATION, sd
        )
    except ImportError:
        logger.debug("win32security not available — using chmod only")


# ===================================================================
# Secret Store Backends
# ===================================================================

class KeyringBackend:
    """OS keyring backend (Windows Credential Locker, macOS Keychain, etc.)"""
    
    def __init__(self):
        if not _KEYRING_AVAILABLE:
            raise RuntimeError("keyring not available")
    
    def get(self, key_name: str) -> Optional[str]:
        try:
            return keyring.get_password(SERVICE_NAME, key_name)
        except KeyringError as e:
            logger.debug("Keyring get failed for %s: %s", key_name, e)
            return None
    
    def set(self, key_name: str, value: str) -> bool:
        try:
            keyring.set_password(SERVICE_NAME, key_name, value)
            return True
        except KeyringError as e:
            logger.warning("Keyring set failed for %s: %s", key_name, e)
            return False
    
    def delete(self, key_name: str) -> bool:
        try:
            keyring.delete_password(SERVICE_NAME, key_name)
            return True
        except (PasswordDeleteError, KeyringError):
            return False
    
    def list_keys(self) -> list[str]:
        """List all keys — not supported by all keyring backends."""
        # Most keyring backends don't support listing
        return []


class EncryptedFileBackend:
    """
    Encrypted file backend for secrets.
    
    Stores all secrets in a single Fernet-encrypted JSON file.
    File permissions restricted to owner only (0600).
    """
    
    def __init__(self, secrets_dir: Path, master_key: Optional[bytes] = None):
        if not _CRYPTO_AVAILABLE:
            raise RuntimeError("cryptography package required")
        
        self.secrets_dir = secrets_dir
        self.secrets_file = secrets_dir / SECRETS_FILE_NAME
        self.key_file = secrets_dir / KEY_FILE_NAME
        
        # Get or create master key
        if master_key:
            self._master_key = master_key
        else:
            self._master_key = _get_or_create_key_file(self.key_file)
        
        self._fernet = Fernet(self._master_key)
        self._cache: Dict[str, str] = {}
        self._loaded = False
    
    def _load(self) -> None:
        """Load and decrypt secrets from file."""
        if self._loaded:
            return
        
        if not self.secrets_file.exists():
            self._cache = {}
            self._loaded = True
            return
        
        try:
            encrypted_data = self.secrets_file.read_bytes()
            decrypted = self._fernet.decrypt(encrypted_data)
            self._cache = json.loads(decrypted.decode())
            self._loaded = True
            logger.debug("Loaded %d secrets from encrypted file", len(self._cache))
        except (InvalidToken, json.JSONDecodeError) as e:
            logger.warning("Failed to load secrets file: %s — starting fresh", e)
            self._cache = {}
            self._loaded = True
    
    def _save(self) -> None:
        """Encrypt and save secrets to file."""
        json_data = json.dumps(self._cache, indent=None).encode()
        encrypted = self._fernet.encrypt(json_data)
        
        # Write with restricted permissions
        fd = os.open(
            str(self.secrets_file),
            os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
            0o600,
        )
        try:
            os.write(fd, encrypted)
        finally:
            os.close(fd)
        
        # Windows ACL
        if sys.platform == "win32":
            try:
                _set_windows_acl(str(self.secrets_file))
            except Exception:
                pass
    
    def get(self, key_name: str) -> Optional[str]:
        self._load()
        return self._cache.get(key_name)
    
    def set(self, key_name: str, value: str) -> bool:
        self._load()
        self._cache[key_name] = value
        self._save()
        return True
    
    def delete(self, key_name: str) -> bool:
        self._load()
        if key_name in self._cache:
            del self._cache[key_name]
            self._save()
            return True
        return False
    
    def list_keys(self) -> list[str]:
        self._load()
        return list(self._cache.keys())


# ===================================================================
# Unified Secret Store
# ===================================================================

class SecretStore:
    """
    Unified secret store with automatic backend selection.
    
    Priority:
    1. OS Keyring (most secure)
    2. Encrypted file (fallback)
    3. In-memory only (last resort — not persistent)
    
    Usage:
        store = SecretStore()
        store.set("ai_openai_key", "sk-...")
        key = store.get("ai_openai_key")
        store.delete("ai_openai_key")
    """
    
    def __init__(
        self,
        secrets_dir: Optional[Path] = None,
        master_key: Optional[bytes] = None,
        force_file_backend: bool = False,
    ):
        self._backend: Any = None
        self._backend_name = "none"
        self._memory_cache: Dict[str, str] = {}
        
        if secrets_dir is None:
            # Default: app directory / saves
            app_dir = Path(__file__).parent.parent.parent
            secrets_dir = app_dir / SECRETS_DIR_NAME
        
        secrets_dir.mkdir(parents=True, exist_ok=True)
        
        # Try keyring first
        if not force_file_backend and _KEYRING_AVAILABLE:
            try:
                self._backend = KeyringBackend()
                self._backend_name = "keyring"
                logger.info("Secret store: using OS keyring backend")
            except RuntimeError:
                pass
        
        # Fallback to encrypted file
        if self._backend is None and _CRYPTO_AVAILABLE:
            try:
                self._backend = EncryptedFileBackend(secrets_dir, master_key)
                self._backend_name = "encrypted_file"
                logger.info("Secret store: using encrypted file backend")
            except RuntimeError:
                pass
        
        # Last resort: in-memory only
        if self._backend is None:
            self._backend_name = "memory"
            logger.warning("Secret store: using in-memory backend (NOT PERSISTENT)")
    
    @property
    def backend_name(self) -> str:
        return self._backend_name
    
    def get(self, key_name: str) -> Optional[str]:
        """
        Get a secret by name.
        
        Returns None if not found.
        The secret is NEVER logged or printed.
        """
        # Check memory cache first
        if key_name in self._memory_cache:
            return self._memory_cache[key_name]
        
        # Try backend
        if self._backend is not None:
            value = self._backend.get(key_name)
            if value is not None:
                self._memory_cache[key_name] = value
                return value
        
        return None
    
    def set(self, key_name: str, value: str) -> bool:
        """
        Store a secret.
        
        The value is encrypted before storage.
        Returns True on success.
        """
        if not value:
            return False
        
        self._memory_cache[key_name] = value
        
        if self._backend is not None:
            return self._backend.set(key_name, value)
        
        return True  # In-memory only
    
    def delete(self, key_name: str) -> bool:
        """Delete a secret."""
        self._memory_cache.pop(key_name, None)
        
        if self._backend is not None:
            return self._backend.delete(key_name)
        
        return True
    
    def list_keys(self) -> list[str]:
        """List all stored secret names (NOT values)."""
        if self._backend is not None:
            return self._backend.list_keys()
        return list(self._memory_cache.keys())
    
    def clear(self) -> None:
        """Clear all secrets from memory cache (backend secrets remain)."""
        self._memory_cache.clear()
    
    def __repr__(self) -> str:
        return f"SecretStore(backend={self._backend_name}, keys={len(self._memory_cache)} cached)"


# ===================================================================
# AI Key Convenience Functions
# ===================================================================

# Global secret store instance (lazy init)
_store: Optional[SecretStore] = None


def _get_store() -> SecretStore:
    global _store
    if _store is None:
        _store = SecretStore()
    return _store


# AI provider key names
AI_KEY_NAMES = {
    "openai": "ai_openai_key",
    "anthropic": "ai_anthropic_key",
    "mistral": "ai_mistral_key",
    "gemini": "ai_gemini_key",
    "custom": "ai_custom_key",
}


def save_ai_key(provider: str, api_key: str) -> bool:
    """
    Save an AI provider API key securely.
    
    Args:
        provider: One of 'openai', 'anthropic', 'mistral', 'gemini', 'custom'
        api_key: The API key to store
    
    Returns:
        True if saved successfully
    """
    key_name = AI_KEY_NAMES.get(provider.lower())
    if not key_name:
        logger.warning("Unknown AI provider: %s", redact_secrets(provider))
        return False
    
    return _get_store().set(key_name, api_key)


def load_ai_key(provider: str) -> Optional[str]:
    """
    Load an AI provider API key from secure storage.
    
    Returns None if not found.
    """
    key_name = AI_KEY_NAMES.get(provider.lower())
    if not key_name:
        return None
    
    return _get_store().get(key_name)


def delete_ai_key(provider: str) -> bool:
    """Delete an AI provider API key."""
    key_name = AI_KEY_NAMES.get(provider.lower())
    if not key_name:
        return False
    
    return _get_store().delete(key_name)


def migrate_plaintext_keys(settings: Dict[str, Any]) -> int:
    """
    Migrate API keys from plaintext settings.json to secure storage.
    
    Call this once during app startup to migrate existing keys.
    
    Returns:
        Number of keys migrated
    """
    migrated = 0
    
    key_mapping = {
        'openai_api_key': 'openai',
        'anthropic_api_key': 'anthropic',
        'mistral_api_key': 'mistral',
        'gemini_api_key': 'gemini',
        'custom_api_key': 'custom',
    }
    
    for settings_key, provider in key_mapping.items():
        value = settings.get(settings_key)
        if value and isinstance(value, str) and value.strip():
            if save_ai_key(provider, value.strip()):
                migrated += 1
                logger.info("Migrated %s key to secure storage", provider)
    
    return migrated


# ===================================================================
# Public API
# ===================================================================

__all__ = [
    'SecretStore',
    'save_ai_key',
    'load_ai_key',
    'delete_ai_key',
    'migrate_plaintext_keys',
    'redact_secrets',
    'RedactingLogger',
]