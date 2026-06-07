"""
AGREGAT Security Module

Defense-in-depth architecture:
  Layer 1: License verification (online server-authoritative, Ed25519 signed receipts)
  Layer 2: Anti-debug + anti-tamper + integrity guards
  Layer 3: Request signing (HMAC) + encrypted storage
  Layer 4: Secure memory + anti-screenshot + clipboard protection

C-05: Legacy offline (HWID/XOR/HMAC) licensing exports have been REMOVED.
Only server-authoritative licensing with Ed25519 signed receipts is supported.
"""

from engine.security.license_client import (
    LicenseClient,
    get_license_client,
    verify_license_online,
    activate_license_online,
    generate_device_fingerprint,
    get_device_name,
    get_os_info,
    get_app_version,
)

from engine.security.anti_debug import (
    quick_security_check,
    is_debugger_attached,
    detect_vm,
    verify_self_integrity,
    get_integrity_guard,
    IntegrityGuard,
)

from engine.security.secure_memory import (
    SecureToken,
    SecureDict,
    virtuallock_supported,
    secure_input_from_str,
    secure_input_from_ctypes,
)

# Backward compatibility alias
SecureString = SecureToken

# Stub functions for backward compatibility — secure_memory.py was refactored
# to use SecureToken.wipe() instead of standalone secure_zero()
def secure_zero(buf):
    """Backward compat: delegates to SecureToken.wipe() or zeroes bytearray."""
    if hasattr(buf, 'wipe'):
        buf.wipe()
    elif isinstance(buf, (bytearray, memoryview)):
        for i in range(len(buf)):
            buf[i] = 0

def mlock_supported():
    """Backward compat: returns virtuallock_supported() on Windows."""
    return virtuallock_supported()

from engine.security.request_signing import (
    sign_api_request,
    verify_request_signature,
    generate_nonce,
)

# Backward compatibility alias
verify_signature = verify_request_signature

from engine.security.encrypted_storage import (
    EncryptedStorage,
    get_storage,
    secure_store,
    secure_retrieve,
    secure_delete,
    secure_delete_file,
)

from engine.security.anti_tamper import (
    detect_injected_dlls,
    verify_code_integrity,
    detect_iat_hooks,
    enable_anti_screenshot,
    disable_anti_screenshot,
    clear_clipboard,
    get_clipboard_text,
    run_advanced_integrity_check,
)

# C-01: Strategy sandboxing
from engine.security.strategy_sandbox import (
    validate_strategy_code,
    execute_in_sandbox,
    StrategyExecutionGuard,
    SandboxExecutionError,
    SandboxTimeoutError,
    SandboxSecurityError,
    SecurityViolation,
    StrategyASTValidator,
)

# Backward compatibility aliases for old API names
SandboxViolation = SecurityViolation

# C-02: Secure secret storage (DPAPI/Keychain)
from engine.security.secret_store import (
    SecretStore,
    save_ai_key,
    load_ai_key,
    delete_ai_key,
    migrate_plaintext_keys,
    redact_secrets,
    RedactingLogger,
)

# Backward compatibility stubs for old API
def get_secret_store() -> SecretStore:
    """Backward compat: returns a new SecretStore instance."""
    return SecretStore()

def store_secret(key: str, value: str) -> bool:
    """Backward compat: delegates to SecretStore.set()."""
    return SecretStore().set(key, value)

def retrieve_secret(key: str) -> Optional[str]:
    """Backward compat: delegates to SecretStore.get()."""
    return SecretStore().get(key)

def delete_secret(key: str) -> bool:
    """Backward compat: delegates to SecretStore.delete()."""
    return SecretStore().delete(key)

def list_secret_keys() -> list:
    """Backward compat: delegates to SecretStore.list_keys()."""
    return SecretStore().list_keys()

# C-03: Binary integrity verification
from engine.security.integrity import (
    IntegrityManager,
    compute_binary_integrity,
    compute_file_hash,
    compute_directory_hash,
    verify_release_signature,
    check_debugger_attached,
    check_environment_suspicious,
    get_integrity_manager,
)

# Backward compatibility aliases/stubs
IntegrityChecker = IntegrityManager

class IntegrityResult:
    """Backward compat stub for old IntegrityResult API."""
    def __init__(self, is_valid: bool = True, files_checked: int = 0, violations: list = None):
        self.is_valid = is_valid
        self.files_checked = files_checked
        self.violations = violations or []

def verify_file_integrity(file_path, expected_hash: str = "") -> bool:
    """Backward compat: delegates to compute_file_hash comparison."""
    actual = compute_file_hash(file_path)
    return actual == expected_hash if expected_hash else bool(actual)

def generate_manifest(directory) -> dict:
    """Backward compat stub: returns directory hash."""
    return {"directory_hash": compute_directory_hash(directory)}

def verify_manifest(manifest: dict) -> bool:
    """Backward compat stub: always True (use IntegrityManager for real checks)."""
    return True

# M-03: Secure update checker
from engine.security.update_checker import (
    UpdateChecker,
    UpdateStatus,
    UpdateManifest,
    UpdateResult,
    VersionInfo,
    generate_update_manifest,
)

__all__ = [
    # Online licensing (server-authoritative, Ed25519 signed receipts)
    "LicenseClient",
    "get_license_client",
    "verify_license_online",
    "activate_license_online",
    "generate_device_fingerprint",
    "get_device_name",
    "get_os_info",
    "get_app_version",
    # Anti-debug & anti-tamper
    "quick_security_check",
    "is_debugger_attached",
    "detect_vm",
    "verify_self_integrity",
    "get_integrity_guard",
    "IntegrityGuard",
    # Secure Memory
    "SecureToken",
    "SecureString",  # Backward compatibility alias
    "SecureDict",
    "secure_zero",   # Backward compat stub
    "mlock_supported",  # Backward compat stub
    "virtuallock_supported",
    "secure_input_from_str",
    "secure_input_from_ctypes",
    # Request Signing (E2E-01)
    "sign_api_request",
    "verify_request_signature",
    "verify_signature",  # Backward compat alias
    "generate_nonce",
    # Encrypted Storage (E2E-02)
    "EncryptedStorage",
    "get_storage",
    "secure_store",
    "secure_retrieve",
    "secure_delete",
    "secure_delete_file",
    # Anti-Tamper (E2E-03, E2E-04, E2E-08, E2E-09)
    "detect_injected_dlls",
    "verify_code_integrity",
    "detect_iat_hooks",
    "enable_anti_screenshot",
    "disable_anti_screenshot",
    "clear_clipboard",
    "get_clipboard_text",
    "run_advanced_integrity_check",
    # C-01: Strategy Sandboxing
    "validate_strategy_code",
    "execute_in_sandbox",
    "StrategyExecutionGuard",
    "SandboxExecutionError",
    "SandboxTimeoutError",
    "SandboxSecurityError",
    "SecurityViolation",
    "SandboxViolation",  # Backward compat alias
    "StrategyASTValidator",
    # C-02: Secret Store (DPAPI/Keychain)
    "SecretStore",
    "save_ai_key",
    "load_ai_key",
    "delete_ai_key",
    "migrate_plaintext_keys",
    "redact_secrets",
    "RedactingLogger",
    # Backward compat stubs
    "get_secret_store",
    "store_secret",
    "retrieve_secret",
    "delete_secret",
    "list_secret_keys",
    # C-03: Binary Integrity
    "IntegrityManager",
    "IntegrityChecker",  # Backward compat alias
    "IntegrityResult",   # Backward compat stub
    "compute_binary_integrity",
    "compute_file_hash",
    "compute_directory_hash",
    "verify_release_signature",
    "check_debugger_attached",
    "check_environment_suspicious",
    "get_integrity_manager",
    "verify_file_integrity",   # Backward compat stub
    "generate_manifest",       # Backward compat stub
    "verify_manifest",         # Backward compat stub
    # M-03: Update Checker
    "UpdateChecker",
    "UpdateStatus",
    "UpdateManifest",
    "UpdateResult",
    "VersionInfo",
    "generate_update_manifest",
]
