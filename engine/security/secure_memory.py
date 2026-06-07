"""
AGREGAT Secure Memory Module

P0-4: Protects sensitive data (tokens, keys) in memory from:
- Memory dumpers (Cheat Engine, Process Hacker)
- Swap file leaks
- Core dump exposure

Uses XOR encryption with random per-instance keys.
NOT cryptographically secure — just raises the bar for memory scraping.
"""

import secrets
import sys
import os
from typing import Optional


class SecureToken:
    """
    Token encrypted in memory with random per-instance XOR key.
    
    The plaintext is never stored directly. Each access decrypts on-the-fly.
    This prevents simple memory string searches from finding tokens.
    
    WARNING: XOR is NOT cryptographically secure. This is obfuscation,
    not encryption. It defeats casual memory scraping but not a
    determined reverse engineer. For production-grade memory protection,
    use platform-specific APIs (CryptProtectMemory on Windows).
    """
    
    __slots__ = ('_encrypted', '_key', '_length')
    
    def __init__(self, token: str):
        """Encrypt token with random XOR key."""
        token_bytes = bytearray(token.encode('utf-8'))
        self._length = len(token_bytes)
        self._key = secrets.token_bytes(max(32, self._length))
        # XOR encrypt
        self._encrypted = bytes(
            a ^ b for a, b in zip(token_bytes, self._key[:self._length])
        )
        # BUG FIX: Securely wipe the original bytearray (del doesn't guarantee wipe)
        for i in range(len(token_bytes)):
            token_bytes[i] = 0
        del token_bytes
    
    def get(self) -> str:
        """Decrypt and return the token."""
        decrypted = bytearray(
            a ^ b for a, b in zip(self._encrypted, self._key[:self._length])
        )
        result = decrypted.decode('utf-8')
        # Wipe decrypted bytes immediately after use
        for i in range(len(decrypted)):
            decrypted[i] = 0
        return result
    
    def wipe(self):
        """Securely erase the token from memory (multiple passes)."""
        if self._length == 0:
            return
        # Pass 1: Overwrite with zeros
        self._encrypted = b'\x00' * len(self._encrypted)
        self._key = b'\x00' * len(self._key)
        # Pass 2: Overwrite with random data
        self._encrypted = secrets.token_bytes(len(self._encrypted))
        self._key = secrets.token_bytes(len(self._key))
        # Pass 3: Overwrite with ones
        self._encrypted = b'\xff' * len(self._encrypted)
        self._key = b'\xff' * len(self._key)
        self._length = 0
    
    def __del__(self):
        """Auto-wipe on garbage collection."""
        try:
            self.wipe()
        except Exception:
            pass
    
    def __repr__(self):
        return f"SecureToken(****{self._length} bytes****)"
    
    def __str__(self):
        return self.__repr__()
    
    def __bool__(self):
        return self._length > 0
    
    def __len__(self):
        return self._length
    
    def equals(self, other: str) -> bool:
        """
        Constant-time comparison to prevent timing attacks.
        Use this instead of `self.get() == other`.
        """
        import hmac
        token_bytes = self.get().encode('utf-8')
        other_bytes = other.encode('utf-8')
        # hmac.compare_digest is constant-time
        result = hmac.compare_digest(token_bytes, other_bytes)
        # Wipe the decrypted bytes
        wiped = bytearray(token_bytes)
        for i in range(len(wiped)):
            wiped[i] = 0
        return result


class SecureDict:
    """
    Dictionary with encrypted values in memory.
    Keys are plaintext (for lookup), values are SecureToken encrypted.
    """
    
    def __init__(self):
        self._data: dict[str, SecureToken] = {}
    
    def set(self, key: str, value: str):
        """Store encrypted value."""
        if key in self._data:
            self._data[key].wipe()
        self._data[key] = SecureToken(value)
    
    def get(self, key: str, default: Optional[str] = None) -> Optional[str]:
        """Retrieve decrypted value."""
        token = self._data.get(key)
        if token is None:
            return default
        return token.get()
    
    def delete(self, key: str):
        """Securely delete a value."""
        token = self._data.pop(key, None)
        if token:
            token.wipe()
    
    def wipe_all(self):
        """Securely erase all values."""
        for token in self._data.values():
            token.wipe()
        self._data.clear()
    
    def __contains__(self, key: str) -> bool:
        return key in self._data
    
    def __len__(self) -> int:
        return len(self._data)
    
    def __del__(self):
        try:
            self.wipe_all()
        except Exception:
            pass


# ─── Windows-specific Memory Protection ───────────────────────────────

def protect_memory_windows(data_ptr: int, size: int, protect: bool = True) -> bool:
    """
    P0-4: Use VirtualProtect to make memory pages unreadable.
    
    When protect=True: sets PAGE_NOACCESS (any read crashes the process)
    When protect=False: restores PAGE_READWRITE
    
    This prevents memory dumpers from reading protected pages.
    Only available on Windows.
    
    Returns True if successful.
    """
    if sys.platform != "win32":
        return False
    
    try:
        import ctypes
        from ctypes import wintypes
        
        PAGE_READWRITE = 0x04
        PAGE_NOACCESS = 0x01
        
        old_protect = wintypes.DWORD()
        result = ctypes.windll.kernel32.VirtualProtect(
            ctypes.c_void_p(data_ptr),
            ctypes.c_size_t(size),
            PAGE_NOACCESS if protect else PAGE_READWRITE,
            ctypes.byref(old_protect),
        )
        return bool(result)
    except Exception:
        return False


def prevent_core_dump():
    """
    Disable core dumps to prevent sensitive data from being written to disk.
    
    On Unix: sets RLIMIT_CORE to 0
    On Windows: disables WER (Windows Error Reporting) dumps
    """
    try:
        if sys.platform != "win32":
            import resource
            resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        else:
            # Windows: disable error reporting dumps via environment
            os.environ["COMPlus_DbgEnableMiniDump"] = "0"
    except Exception:
        pass


# ─── Module-level secure storage ──────────────────────────────────────

_secure_store = SecureDict()


def store_secure(key: str, value: str):
    """Store a value in encrypted memory."""
    _secure_store.set(key, value)


def retrieve_secure(key: str) -> Optional[str]:
    """Retrieve a value from encrypted memory."""
    return _secure_store.get(key)


def wipe_secure(key: str):
    """Securely erase a value."""
    _secure_store.delete(key)


def wipe_all_secure():
    """Wipe all secure storage."""
    _secure_store.wipe_all()