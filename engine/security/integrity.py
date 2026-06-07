"""
AGREGAT Engine — Binary & Code Integrity Verification

C-03 FIX: Compute and verify application integrity hash at startup.

Security model:
- SHA-256 hash of the main binary/script computed at launch
- Hash is sent to server with each heartbeat
- Server compares against known-good hash from activation
- Tampered/modified binaries are detected and sessions revoked
- Supports both PyInstaller frozen builds and development mode
- Optional: Ed25519 signature verification for release builds
"""

from __future__ import annotations

import hashlib
import logging
import os
import sys
import time
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger("StrategyTester.engine.security.integrity")


# ===================================================================
# Integrity Hash Computation
# ===================================================================

def compute_file_hash(file_path: str | Path, algorithm: str = "sha256") -> str:
    """
    Compute hash of a file.
    
    Args:
        file_path: Path to the file
        algorithm: Hash algorithm (sha256, sha512, md5)
    
    Returns:
        Hex digest string
    """
    h = hashlib.new(algorithm)
    path = Path(file_path)
    
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")
    
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    
    return h.hexdigest()


def compute_binary_integrity() -> Tuple[str, str]:
    """
    Compute integrity hash of the main application binary.
    
    Returns:
        (hash_hex, binary_path)
    
    Handles:
    - PyInstaller frozen builds (sys.executable)
    - Development mode (main script)
    - macOS .app bundles
    """
    if getattr(sys, "frozen", False):
        # PyInstaller bundle
        binary_path = sys.executable
    elif hasattr(sys, "_MEIPASS"):
        # PyInstaller onefile mode
        binary_path = sys.executable
    else:
        # Development mode — hash the main entry point
        binary_path = os.path.abspath(sys.argv[0]) if sys.argv[0] else __file__
    
    try:
        hash_value = compute_file_hash(binary_path)
        logger.info(
            "Integrity: computed hash for %s: %s",
            os.path.basename(binary_path),
            hash_value[:16] + "...",
        )
        return hash_value, binary_path
    except Exception as e:
        logger.error("Integrity: failed to compute hash: %s", e)
        # Return a placeholder — server will flag this
        return f"error:{e}", binary_path


def compute_directory_hash(
    directory: str | Path,
    include_patterns: Optional[list[str]] = None,
    exclude_patterns: Optional[list[str]] = None,
) -> str:
    """
    Compute a combined hash of all files in a directory.
    
    Useful for verifying the entire engine/ or gui/ package.
    
    Args:
        directory: Directory to hash
        include_patterns: Glob patterns to include (default: *.py)
        exclude_patterns: Glob patterns to exclude
    
    Returns:
        Combined SHA-256 hex digest
    """
    directory = Path(directory)
    if not directory.is_dir():
        raise NotADirectoryError(f"Not a directory: {directory}")
    
    if include_patterns is None:
        include_patterns = ["*.py", "*.pyd", "*.so", "*.dll"]
    if exclude_patterns is None:
        exclude_patterns = ["__pycache__", "*.pyc", "*.pyo", ".git"]
    
    h = hashlib.sha256()
    file_count = 0
    
    # Collect all matching files
    files = []
    for pattern in include_patterns:
        files.extend(directory.rglob(pattern))
    
    # Sort for deterministic ordering
    files.sort()
    
    for file_path in files:
        # Skip excluded patterns
        skip = False
        for excl in exclude_patterns:
            if excl in str(file_path):
                skip = True
                break
        if skip:
            continue
        
        if not file_path.is_file():
            continue
        
        try:
            # Include relative path in hash (detects file moves)
            rel_path = file_path.relative_to(directory)
            h.update(str(rel_path).encode())
            
            # Include file content
            file_hash = compute_file_hash(file_path)
            h.update(file_hash.encode())
            file_count += 1
        except (PermissionError, OSError):
            pass
    
    logger.debug(
        "Directory integrity: %d files hashed in %s",
        file_count, directory.name,
    )
    return h.hexdigest()


# ===================================================================
# Integrity Manager
# ===================================================================

class IntegrityManager:
    """
    Manages application integrity verification.
    
    Usage:
        mgr = IntegrityManager()
        mgr.compute_at_startup()
        
        # Later, during heartbeat:
        hash_value = mgr.get_current_hash()
        # Send hash_value to server
    """
    
    def __init__(self, app_dir: Optional[str | Path] = None):
        self._binary_hash: Optional[str] = None
        self._binary_path: Optional[str] = None
        self._engine_hash: Optional[str] = None
        self._computed_at: Optional[float] = None
        self._app_dir = Path(app_dir) if app_dir else Path(__file__).parent.parent.parent
    
    def compute_at_startup(self) -> dict:
        """
        Compute all integrity hashes at application startup.
        
        Returns:
            Dict with hash values
        """
        start = time.monotonic()
        
        # Binary hash
        self._binary_hash, self._binary_path = compute_binary_integrity()
        
        # Engine package hash
        engine_dir = self._app_dir / "engine"
        if engine_dir.is_dir():
            try:
                self._engine_hash = compute_directory_hash(engine_dir)
            except Exception as e:
                logger.warning("Integrity: engine hash failed: %s", e)
                self._engine_hash = f"error:{e}"
        
        self._computed_at = time.time()
        elapsed = (time.monotonic() - start) * 1000
        
        logger.info(
            "Integrity: startup hashes computed in %.0fms",
            elapsed,
        )
        
        return self.get_hashes()
    
    def get_current_hash(self) -> str:
        """Get the current binary hash (for heartbeat)."""
        if self._binary_hash is None:
            self.compute_at_startup()
        return self._binary_hash or ""
    
    def get_hashes(self) -> dict:
        """Get all computed hashes."""
        return {
            "binary_hash": self._binary_hash,
            "binary_path": self._binary_path,
            "engine_hash": self._engine_hash,
            "computed_at": self._computed_at,
        }
    
    def verify_unchanged(self) -> Tuple[bool, str]:
        """
        Re-compute hashes and verify they haven't changed.
        
        Returns:
            (is_unchanged, detail_message)
        """
        if self._binary_hash is None:
            return False, "No baseline hash computed"
        
        try:
            current_hash, _ = compute_binary_integrity()
        except Exception as e:
            return False, f"Hash computation failed: {e}"
        
        if current_hash != self._binary_hash:
            return False, (
                f"Binary modified! "
                f"Original: {self._binary_hash[:16]}... "
                f"Current: {current_hash[:16]}..."
            )
        
        return True, "Integrity verified"


# ===================================================================
# Ed25519 Signature Verification (for release builds)
# ===================================================================

def verify_release_signature(
    file_path: str | Path,
    signature_hex: str,
    public_key_hex: str,
) -> bool:
    """
    Verify Ed25519 signature of a file.
    
    Use this to verify that release builds are signed by the developer.
    
    Args:
        file_path: File to verify
        signature_hex: Hex-encoded Ed25519 signature
        public_key_hex: Hex-encoded Ed25519 public key
    
    Returns:
        True if signature is valid
    """
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        from cryptography.exceptions import InvalidSignature
        
        public_key_bytes = bytes.fromhex(public_key_hex)
        signature_bytes = bytes.fromhex(signature_hex)
        
        public_key = Ed25519PublicKey.from_public_bytes(public_key_bytes)
        
        file_data = Path(file_path).read_bytes()
        
        public_key.verify(signature_bytes, file_data)
        logger.info("Integrity: release signature VERIFIED for %s", file_path)
        return True
    
    except ImportError:
        logger.warning("cryptography not available — signature verification skipped")
        return False
    except InvalidSignature:
        logger.error("Integrity: INVALID signature for %s", file_path)
        return False
    except Exception as e:
        logger.error("Integrity: signature verification error: %s", e)
        return False


# ===================================================================
# Anti-Tampering Checks
# ===================================================================

def check_debugger_attached() -> bool:
    """
    Check if a debugger is attached to the process.
    
    This is a basic check — not foolproof but deters casual tampering.
    """
    # Windows: Check IsDebuggerPresent
    if sys.platform == "win32":
        try:
            import ctypes
            return bool(ctypes.windll.kernel32.IsDebuggerPresent())
        except Exception:
            pass
    
    # Linux/macOS: Check /proc/self/status for TracerPid
    try:
        with open("/proc/self/status", "r") as f:
            for line in f:
                if line.startswith("TracerPid:"):
                    pid = int(line.split(":")[1].strip())
                    return pid != 0
    except (FileNotFoundError, PermissionError):
        pass
    
    return False


def check_environment_suspicious() -> list[str]:
    """
    Check for suspicious environment indicators.
    
    Returns list of warnings (empty = clean).
    """
    warnings = []
    
    # Check for known hooking/debugging environment variables
    suspicious_env = [
        "LD_PRELOAD", "LD_LIBRARY_PATH", "DYLD_INSERT_LIBRARIES",
        "PYTHONBREAKPOINT", "PYTHONDONTWRITEBYTECODE",
        "PYTHONINSPECT", "PYTHONTRACE",
    ]
    
    for var in suspicious_env:
        if os.environ.get(var):
            warnings.append(f"Suspicious env var: {var}")
    
    # Check for unusual sys.path entries
    for p in sys.path:
        if p and ("/tmp/" in p or "/dev/shm/" in p):
            warnings.append(f"Suspicious sys.path: {p}")
    
    # Check if running under known debuggers/profilers
    if "pydevd" in sys.modules:
        warnings.append("Running under PyDev debugger")
    if "ptvsd" in sys.modules or "debugpy" in sys.modules:
        warnings.append("Running under VS Code debugger")
    if "pdb" in sys.modules:
        warnings.append("pdb module loaded")
    
    return warnings


# ===================================================================
# Public API
# ===================================================================

# Global integrity manager instance
_integrity_manager: Optional[IntegrityManager] = None


def get_integrity_manager() -> IntegrityManager:
    global _integrity_manager
    if _integrity_manager is None:
        _integrity_manager = IntegrityManager()
    return _integrity_manager


__all__ = [
    'IntegrityManager',
    'compute_binary_integrity',
    'compute_file_hash',
    'compute_directory_hash',
    'verify_release_signature',
    'check_debugger_attached',
    'check_environment_suspicious',
    'get_integrity_manager',
]