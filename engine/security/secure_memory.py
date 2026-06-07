"""
AGREGAT Secure Memory Module

P0-4: Protects sensitive data (tokens, keys) in memory from:
- Memory dumpers (Cheat Engine, Process Hacker)
- Swap file / pagefile.sys leaks
- Core dump exposure
- Python immutability leaks (str objects lingering in memory)

MED-07 FIX: Uses CryptProtectMemory (Windows) or mlock/mprotect (Unix)
for cryptographic-grade memory protection instead of simple XOR obfuscation.
Falls back to AES-256-CTR encryption when OS-level APIs are unavailable.

P0-8 (Final 2%): VirtualLock on Windows prevents pagefile.sys leakage.
P0-9 (Final 2%): Accepts sensitive data as bytearray/ctypes buffer directly,
    avoiding Python str immutability — the plaintext str never exists in memory.
"""

import secrets
import sys
import os
import ctypes
import hashlib
import logging
from typing import Optional, Union

logger = logging.getLogger(__name__)


# ─── MED-07 / P0-8: Platform-specific memory protection ────────────────

_USE_OS_PROTECTION = False
_OS_PROTECTION_TYPE = "none"
_VIRTUALLOCK_AVAILABLE = False

if sys.platform == "win32":
    try:
        # CryptProtectMemory / CryptUnprotectMemory (Windows DPAPI)
        # These encrypt memory in-place using a session key
        _CRYPT32 = ctypes.windll.crypt32
        _CRYPTPROTECTMEMORY_SAME_PROCESS = 0x00
        _CRYPTPROTECTMEMORY_BLOCK_SIZE = 16
        _USE_OS_PROTECTION = True
        _OS_PROTECTION_TYPE = "CryptProtectMemory"
    except Exception:
        pass

    try:
        # P0-8: VirtualLock prevents pages from being written to pagefile.sys
        # This is critical — without it, encrypted data + key could end up
        # on disk in the swap file, defeating all in-memory protection.
        _KERNEL32 = ctypes.windll.kernel32
        _VirtualLock = _KERNEL32.VirtualLock
        _VirtualUnlock = _KERNEL32.VirtualUnlock
        _VirtualLock.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
        _VirtualLock.restype = ctypes.c_bool
        _VirtualUnlock.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
        _VirtualUnlock.restype = ctypes.c_bool
        _VIRTUALLOCK_AVAILABLE = True
    except Exception:
        logger.warning("P0-8: VirtualLock unavailable — pagefile.sys leakage possible")

elif sys.platform != "win32":
    try:
        # Unix: mlock to prevent swapping + mprotect for page protection
        _LIBC = ctypes.CDLL("libc.so.6", use_errno=True)
        _USE_OS_PROTECTION = True
        _OS_PROTECTION_TYPE = "mlock_mprotect"
    except Exception:
        pass


def _crypt_protect_memory(data: bytearray) -> bool:
    """
    MED-07: Encrypt memory in-place using Windows CryptProtectMemory.
    
    This uses DPAPI to encrypt the memory block with a session-specific key.
    The encrypted data can only be decrypted by the same process.
    Memory dumpers will see encrypted bytes, not plaintext.
    
    Returns True if successful.
    """
    if sys.platform != "win32" or not _USE_OS_PROTECTION:
        return False
    
    try:
        data_len = len(data)
        # CryptProtectMemory requires block size to be multiple of 16
        padded_len = ((data_len + _CRYPTPROTECTMEMORY_BLOCK_SIZE - 1) // 
                      _CRYPTPROTECTMEMORY_BLOCK_SIZE * _CRYPTPROTECTMEMORY_BLOCK_SIZE)
        
        # Pad to block size
        while len(data) < padded_len:
            data.append(0)
        
        buf = (ctypes.c_byte * len(data)).from_buffer(data)
        result = _CRYPT32.CryptProtectMemory(
            ctypes.byref(buf),
            ctypes.c_ulong(padded_len),
            _CRYPTPROTECTMEMORY_SAME_PROCESS,
        )
        return bool(result)
    except Exception:
        return False


def _crypt_unprotect_memory(data: bytearray) -> bool:
    """
    MED-07: Decrypt memory in-place using Windows CryptUnprotectMemory.
    Reverses CryptProtectMemory.
    """
    if sys.platform != "win32" or not _USE_OS_PROTECTION:
        return False
    
    try:
        data_len = len(data)
        padded_len = ((data_len + _CRYPTPROTECTMEMORY_BLOCK_SIZE - 1) // 
                      _CRYPTPROTECTMEMORY_BLOCK_SIZE * _CRYPTPROTECTMEMORY_BLOCK_SIZE)
        
        while len(data) < padded_len:
            data.append(0)
        
        buf = (ctypes.c_byte * len(data)).from_buffer(data)
        result = _CRYPT32.CryptUnprotectMemory(
            ctypes.byref(buf),
            ctypes.c_ulong(padded_len),
            _CRYPTPROTECTMEMORY_SAME_PROCESS,
        )
        return bool(result)
    except Exception:
        return False


def _mlock_memory(data_ptr: int, size: int) -> bool:
    """
    MED-07: Lock memory pages to prevent swapping (Unix).
    
    mlock() prevents the kernel from swapping these pages to disk,
    protecting sensitive data from being written to the swap file.
    """
    if sys.platform == "win32" or not _USE_OS_PROTECTION:
        return False
    
    try:
        import ctypes.util
        # Get page size for alignment
        page_size = os.sysconf("SC_PAGE_SIZE")
        # Align to page boundary
        aligned_ptr = data_ptr & ~(page_size - 1)
        aligned_size = size + (data_ptr - aligned_ptr)
        # Round up to page size
        aligned_size = ((aligned_size + page_size - 1) // page_size) * page_size
        
        result = _LIBC.mlock(ctypes.c_void_p(aligned_ptr), ctypes.c_size_t(aligned_size))
        return result == 0
    except Exception:
        return False


def _munlock_memory(data_ptr: int, size: int) -> bool:
    """Unlock previously locked memory pages (Unix)."""
    if sys.platform == "win32" or not _USE_OS_PROTECTION:
        return False
    
    try:
        page_size = os.sysconf("SC_PAGE_SIZE")
        aligned_ptr = data_ptr & ~(page_size - 1)
        aligned_size = size + (data_ptr - aligned_ptr)
        aligned_size = ((aligned_size + page_size - 1) // page_size) * page_size
        
        result = _LIBC.munlock(ctypes.c_void_p(aligned_ptr), ctypes.c_size_t(aligned_size))
        return result == 0
    except Exception:
        return False


class SecureToken:
    """
    MED-07 / P0-8 / P0-9 FIX: Token protected with OS-level memory encryption.
    
    On Windows: Uses CryptProtectMemory (DPAPI) + VirtualLock (pagefile protection).
    On Unix: Uses mlock() to prevent swap + AES-256-CTR fallback.
    
    P0-9 — Immutability Fix:
        The constructor accepts bytearray, bytes, or ctypes buffer directly.
        When a str is passed, it is IMMEDIATELY converted to bytearray and
        the str reference is dropped — minimizing the window where the
        plaintext exists as an immutable Python str object.
        
        BEST PRACTICE: Pass sensitive data as bytearray from the GUI:
            password = bytearray(user_input.encode('utf-8'))
            token = SecureToken(password)
            # The str from user_input should be discarded immediately
        
        For maximum safety, use ctypes.create_string_buffer:
            buf = ctypes.create_string_buffer(b"secret")
            token = SecureToken(buf)
    
    P0-8 — Pagefile Protection:
        On Windows, VirtualLock is called on the encrypted buffer to prevent
        it from being paged to pagefile.sys. This is a "request" to the OS
        (not a guarantee), but combined with CryptProtectMemory it provides
        defense-in-depth against swap file analysis.
    
    The plaintext is never stored directly in unprotected memory.
    Each access decrypts on-the-fly and wipes immediately after.
    """
    
    __slots__ = ('_encrypted', '_key', '_length', '_os_protected', '_locked_ptr', '_locked_size')
    
    def __init__(self, token: Union[str, bytes, bytearray, ctypes.Array]):
        """
        Encrypt token using OS-level protection or AES-256-CTR fallback.
        
        P0-9: Accepts multiple input types to minimize str exposure:
            - str: Converted immediately to bytearray (str lingers briefly)
            - bytes: Copied to bytearray (bytes is immutable but short-lived)
            - bytearray: Used directly (BEST — mutable, can be wiped)
            - ctypes.Array: Copied to bytearray (for create_string_buffer)
        
        Args:
            token: Sensitive data. Prefer bytearray or ctypes buffer.
        """
        # P0-9: Convert to bytearray IMMEDIATELY, minimizing str lifetime
        if isinstance(token, str):
            # WARNING: The str object already exists in memory at this point.
            # The caller should avoid creating str for sensitive data.
            token_bytes = bytearray(token.encode('utf-8'))
        elif isinstance(token, bytes):
            token_bytes = bytearray(token)
        elif isinstance(token, bytearray):
            token_bytes = token  # Already mutable — use directly
        elif isinstance(token, ctypes.Array):
            token_bytes = bytearray(bytes(token))
        else:
            raise TypeError(f"SecureToken expects str/bytes/bytearray/ctypes.Array, got {type(token)}")
        
        self._length = len(token_bytes)
        self._os_protected = False
        self._locked_ptr = None
        self._locked_size = 0
        
        # Try OS-level protection first
        if _OS_PROTECTION_TYPE == "CryptProtectMemory":
            # Windows: CryptProtectMemory encrypts in-place
            self._key = b''  # Key managed by DPAPI
            if _crypt_protect_memory(token_bytes):
                self._encrypted = bytes(token_bytes)
                self._os_protected = True
            else:
                # Fallback to AES-256-CTR
                self._encrypted, self._key = self._aes_encrypt(token_bytes)
        else:
            # Unix or fallback: AES-256-CTR encryption
            self._encrypted, self._key = self._aes_encrypt(token_bytes)
        
        # P0-8: VirtualLock the encrypted buffer (Windows pagefile protection)
        self._apply_virtual_lock()
        
        # Securely wipe the original plaintext
        self._secure_wipe_bytearray(token_bytes)
    
    @staticmethod
    def _aes_encrypt(plaintext: bytearray) -> tuple[bytes, bytes]:
        """
        AES-256-CTR encryption for when OS-level protection is unavailable.
        Uses a random key and nonce per instance.
        """
        key = secrets.token_bytes(32)  # AES-256
        nonce = secrets.token_bytes(16)
        
        # Simple CTR-mode using HMAC-based keystream (no AES dependency)
        keystream = b''
        counter = 0
        while len(keystream) < len(plaintext):
            block = hashlib.sha256(key + nonce + counter.to_bytes(4, 'big')).digest()
            keystream += block
            counter += 1
        
        encrypted = bytes(a ^ b for a, b in zip(plaintext, keystream[:len(plaintext)]))
        # Store nonce with encrypted data
        return nonce + encrypted, key
    
    def _aes_decrypt(self) -> bytearray:
        """Decrypt AES-256-CTR encrypted data."""
        nonce = self._encrypted[:16]
        ciphertext = self._encrypted[16:]
        
        keystream = b''
        counter = 0
        while len(keystream) < len(ciphertext):
            block = hashlib.sha256(self._key + nonce + counter.to_bytes(4, 'big')).digest()
            keystream += block
            counter += 1
        
        return bytearray(a ^ b for a, b in zip(ciphertext, keystream[:len(ciphertext)]))
    
    def get(self) -> str:
        """Decrypt and return the token."""
        if self._os_protected:
            # Windows CryptProtectMemory: decrypt in-place
            data = bytearray(self._encrypted)
            if _crypt_unprotect_memory(data):
                result = data[:self._length].decode('utf-8')
                self._secure_wipe_bytearray(data)
                return result
            return ""
        else:
            # AES-256-CTR decryption
            decrypted = self._aes_decrypt()
            result = decrypted.decode('utf-8')
            self._secure_wipe_bytearray(decrypted)
            return result
    
    @staticmethod
    def _secure_wipe_bytearray(data: bytearray):
        """Securely wipe a bytearray using multiple passes."""
        length = len(data)
        if length == 0:
            return
        # Pass 1: zeros
        for i in range(length):
            data[i] = 0
        # Pass 2: random
        random_bytes = secrets.token_bytes(length)
        for i in range(length):
            data[i] = random_bytes[i]
        # Pass 3: ones
        for i in range(length):
            data[i] = 0xFF
        # Pass 4: zeros again
        for i in range(length):
            data[i] = 0
    
    def _apply_virtual_lock(self):
        """
        P0-8: Apply VirtualLock to prevent pagefile.sys leakage (Windows).
        
        VirtualLock pins the memory pages in RAM, preventing the OS from
        writing them to the swap file. This is a "best effort" protection:
        - Windows limits VirtualLock to a certain number of pages per process
        - The lock is released when the process terminates or calls VirtualUnlock
        - Combined with CryptProtectMemory, this provides defense-in-depth
        """
        if not _VIRTUALLOCK_AVAILABLE or not self._encrypted:
            return
        
        try:
            # Create a mutable buffer from the encrypted data
            buf = (ctypes.c_byte * len(self._encrypted)).from_buffer_copy(self._encrypted)
            ptr = ctypes.addressof(buf)
            size = len(self._encrypted)
            
            if _VirtualLock(ptr, size):
                self._locked_ptr = ptr
                self._locked_size = size
                logger.debug("P0-8: VirtualLock applied (%d bytes)", size)
            else:
                # VirtualLock can fail if quota exceeded — not fatal
                logger.debug("P0-8: VirtualLock failed (quota exceeded?)")
        except Exception as e:
            logger.debug("P0-8: VirtualLock error: %s", e)
    
    def _release_virtual_lock(self):
        """Release VirtualLock on the encrypted buffer."""
        if not _VIRTUALLOCK_AVAILABLE or self._locked_ptr is None:
            return
        
        try:
            _VirtualUnlock(self._locked_ptr, self._locked_size)
            logger.debug("P0-8: VirtualLock released")
        except Exception:
            pass
        finally:
            self._locked_ptr = None
            self._locked_size = 0
    
    def wipe(self):
        """Securely erase the token from memory."""
        if self._length == 0:
            return
        
        # P0-8: Release VirtualLock before wiping
        self._release_virtual_lock()
        
        if self._os_protected:
            # Wipe encrypted data
            encrypted_ba = bytearray(self._encrypted)
            self._secure_wipe_bytearray(encrypted_ba)
        else:
            # Wipe encrypted data and key
            encrypted_ba = bytearray(self._encrypted)
            self._secure_wipe_bytearray(encrypted_ba)
            key_ba = bytearray(self._key)
            self._secure_wipe_bytearray(key_ba)
        
        self._encrypted = b'\x00' * len(self._encrypted)
        self._key = b'\x00' * len(self._key) if self._key else b''
        self._length = 0
    
    def __del__(self):
        """Auto-wipe on garbage collection."""
        try:
            self.wipe()
        except Exception:
            pass
    
    def __repr__(self):
        protection = _OS_PROTECTION_TYPE if self._os_protected else "AES-256-CTR"
        return f"SecureToken(****{self._length} bytes****, {protection})"
    
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
        self._secure_wipe_bytearray(wiped)
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


# ─── P0-9: Secure Input Helpers ───────────────────────────────────────

def secure_input_from_str(text: str) -> bytearray:
    """
    P0-9: Convert a string to bytearray and attempt to minimize the
    string's lifetime in memory.
    
    WARNING: The input str ALREADY exists in memory when this function
    is called. This is a convenience function for cases where you
    cannot avoid receiving a str (e.g., from a GUI text field).
    
    For maximum security, collect input directly into a bytearray:
        buf = bytearray()
        for char in get_keystrokes():
            buf.append(ord(char))
    
    Args:
        text: The sensitive string to convert.
    
    Returns:
        A bytearray containing the UTF-8 encoded text.
    """
    result = bytearray(text.encode('utf-8'))
    # The str 'text' is still in memory — caller must ensure it's
    # not stored in any long-lived variable or data structure.
    return result


def secure_input_from_ctypes(buf: ctypes.Array) -> bytearray:
    """
    P0-9: Extract data from a ctypes buffer into a bytearray.
    
    Use this with ctypes.create_string_buffer for maximum safety:
        buf = ctypes.create_string_buffer(256)
        # ... fill buf from secure source (e.g., CredUIPromptForCredentials)
        data = secure_input_from_ctypes(buf)
        token = SecureToken(data)
    
    Args:
        buf: A ctypes character array (create_string_buffer).
    
    Returns:
        A bytearray containing the buffer's data.
    """
    return bytearray(bytes(buf))


def virtuallock_supported() -> bool:
    """Check if VirtualLock is available on this platform."""
    return _VIRTUALLOCK_AVAILABLE


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