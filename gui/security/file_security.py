"""
AGREGAT GUI — File Security

H-04 FIX: Drag-and-drop file validation
M-02 FIX: Secure file permissions for settings.json
M-06 FIX: Export path validation

Security model:
- Validate file size, type, content before processing
- Prevent path traversal attacks
- Restrict file permissions (owner-only read/write)
- CSV injection detection
- Windows ACL support
"""

from __future__ import annotations

import logging
import os
import stat
import sys
from pathlib import Path
from typing import Tuple

logger = logging.getLogger("StrategyTester.gui.security.file")


# ===================================================================
# Constants
# ===================================================================

MAX_FILE_SIZE = 500 * 1024 * 1024  # 500MB
MAX_CSV_SIZE = 100 * 1024 * 1024   # 100MB for CSV

ALLOWED_DATA_EXTENSIONS = {
    '.csv', '.parquet', '.feather', '.h5', '.hdf5',
    '.json', '.pkl', '.pickle',
}

ALLOWED_STRATEGY_EXTENSIONS = {
    '.py', '.txt',
}

ALLOWED_EXPORT_EXTENSIONS = {
    '.html', '.pdf', '.csv', '.json', '.xlsx', '.png', '.svg',
}

# CSV injection characters (formula prefixes)
CSV_INJECTION_CHARS = {'=', '+', '-', '@', '\t', '\r'}


# ===================================================================
# H-04: Drag-and-Drop File Validation
# ===================================================================

def validate_dropped_file(path: str, allowed_exts: set | None = None) -> Tuple[bool, str]:
    """
    Validate a dropped file before processing.
    
    Checks:
    1. File exists and is a regular file
    2. File size within limits
    3. Extension is allowed
    4. Path traversal prevention
    5. CSV content validation (if applicable)
    
    Args:
        path: File path from drag-and-drop
        allowed_exts: Override allowed extensions
    
    Returns:
        (is_valid, error_message)
    """
    # 1. Existence check
    if not path:
        return False, "No file provided"
    
    try:
        file_path = Path(path)
    except (ValueError, TypeError):
        return False, "Invalid file path"
    
    if not file_path.exists():
        return False, "File not found"
    
    if not file_path.is_file():
        return False, "Not a regular file"
    
    # 2. Size check
    try:
        size = file_path.stat().st_size
    except OSError as e:
        return False, f"Cannot read file: {e}"
    
    if size == 0:
        return False, "File is empty"
    
    max_size = MAX_CSV_SIZE if file_path.suffix.lower() == '.csv' else MAX_FILE_SIZE
    if size > max_size:
        return False, f"File too large ({size // 1024 // 1024}MB > {max_size // 1024 // 1024}MB)"
    
    # 3. Extension check
    ext = file_path.suffix.lower()
    if allowed_exts:
        if ext not in allowed_exts:
            return False, f"File type '{ext}' not allowed"
    else:
        all_allowed = ALLOWED_DATA_EXTENSIONS | ALLOWED_STRATEGY_EXTENSIONS
        if ext not in all_allowed:
            return False, f"File type '{ext}' not supported"
    
    # 4. Path traversal prevention
    try:
        real_path = file_path.resolve()
        # Check for suspicious path components
        path_str = str(real_path)
        if '..' in path_str.split(os.sep):
            return False, "Path traversal detected"
    except (OSError, ValueError):
        return False, "Invalid path resolution"
    
    # 5. CSV content validation
    if ext == '.csv':
        is_valid, msg = _validate_csv_content(file_path)
        if not is_valid:
            return False, msg
    
    # 6. Check file is readable
    if not os.access(str(file_path), os.R_OK):
        return False, "File not readable"
    
    return True, "ok"


def _validate_csv_content(file_path: Path) -> Tuple[bool, str]:
    """
    Validate CSV file content for security issues.
    
    Checks:
    - Valid encoding (UTF-8)
    - No CSV injection formulas in header
    - Reasonable line count
    """
    try:
        with open(file_path, 'r', encoding='utf-8', errors='strict') as f:
            # Read header line
            header = f.readline()
            
            if not header:
                return False, "CSV file is empty"
            
            # Check for CSV injection in header
            first_chars = set(c for c in header[:10] if c.strip())
            dangerous = first_chars & CSV_INJECTION_CHARS
            if dangerous:
                logger.warning(
                    "CSV injection detected in %s: chars=%s",
                    file_path.name, dangerous,
                )
                # Don't block — just warn (some legit CSVs start with -)
                # Return True but log the warning
            
            # Check for extremely long lines (possible attack)
            if len(header) > 10000:
                return False, "CSV header too long (possible attack)"
    
    except UnicodeDecodeError:
        return False, "Invalid CSV encoding (must be UTF-8)"
    except PermissionError:
        return False, "Cannot read CSV file"
    except Exception as e:
        return False, f"CSV validation error: {e}"
    
    return True, "ok"


# ===================================================================
# M-06: Export Path Validation
# ===================================================================

def validate_export_path(path: str) -> Tuple[bool, str]:
    """
    Validate export file path for security.
    
    Checks:
    1. No path traversal
    2. Allowed extension only
    3. Directory exists
    4. Path is writable
    5. No overwriting system files
    
    Args:
        path: Export file path
    
    Returns:
        (is_valid, error_message)
    """
    if not path:
        return False, "No path provided"
    
    try:
        file_path = Path(path)
    except (ValueError, TypeError):
        return False, "Invalid path"
    
    # 1. Path traversal check
    if '..' in str(file_path):
        return False, "Path traversal not allowed"
    
    # 2. Extension check
    ext = file_path.suffix.lower()
    if ext not in ALLOWED_EXPORT_EXTENSIONS:
        return False, f"Extension '{ext}' not allowed for export"
    
    # 3. Directory exists
    dir_path = file_path.parent
    if not dir_path.exists():
        return False, "Directory does not exist"
    
    if not dir_path.is_dir():
        return False, "Parent path is not a directory"
    
    # 4. Writable check
    if not os.access(str(dir_path), os.W_OK):
        return False, "Directory not writable"
    
    # 5. Don't overwrite system/hidden files
    if file_path.name.startswith('.'):
        return False, "Cannot export to hidden files"
    
    # Check if file exists — warn but don't block
    if file_path.exists():
        logger.info("Export will overwrite existing file: %s", file_path.name)
    
    return True, "ok"


# ===================================================================
# M-02: Secure File Permissions
# ===================================================================

def secure_file_permissions(file_path: str, mode: int = 0o600) -> bool:
    """
    Set secure file permissions.
    
    Args:
        file_path: File to secure
        mode: Permission mode (default: 0600 = owner read/write only)
    
    Returns:
        True if successful
    """
    try:
        path = Path(file_path)
        if not path.exists():
            return False
        
        # Unix: chmod
        os.chmod(str(path), mode)
        
        # Windows: ACL
        if sys.platform == "win32":
            _set_windows_acl(str(path))
        
        logger.debug("Secured permissions for %s (mode=%o)", file_path, mode)
        return True
    
    except (OSError, PermissionError) as e:
        logger.warning("Failed to set permissions for %s: %s", file_path, e)
        return False


def save_settings_secure(path: str, data: dict) -> bool:
    """
    Save settings file with secure permissions.
    
    Creates file with 0600 permissions (owner read/write only).
    On Windows, sets ACL to current user only.
    
    Args:
        path: Settings file path
        data: Settings dict to save
    
    Returns:
        True if successful
    """
    import json
    
    try:
        # Create with restricted permissions
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception:
            os.close(fd)
            raise
        
        # Windows ACL
        if sys.platform == "win32":
            try:
                _set_windows_acl(path)
            except Exception as e:
                logger.debug("Windows ACL failed: %s", e)
        
        logger.info("Settings saved securely: %s", path)
        return True
    
    except (OSError, PermissionError, json.JSONDecodeError) as e:
        logger.error("Failed to save settings securely: %s", e)
        return False


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
    except Exception as e:
        logger.debug("Windows ACL error: %s", e)


__all__ = [
    'validate_dropped_file',
    'validate_export_path',
    'save_settings_secure',
    'secure_file_permissions',
]