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
    SecureString,
    secure_zero,
    mlock_supported,
)

from engine.security.request_signing import (
    sign_api_request,
    verify_signature,
    generate_nonce,
)

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
    StrategySandbox,
    SandboxConfig,
    SandboxResult,
    SandboxViolation,
    create_sandboxed_strategy,
)

# C-02: Secure secret storage (DPAPI/Keychain)
from engine.security.secret_store import (
    SecretStore,
    get_secret_store,
    store_secret,
    retrieve_secret,
    delete_secret,
    list_secret_keys,
)

# C-03: Binary integrity verification
from engine.security.integrity import (
    IntegrityChecker,
    IntegrityResult,
    compute_file_hash,
    verify_file_integrity,
    generate_manifest,
    verify_manifest,
)

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
    "SecureString",
    "secure_zero",
    "mlock_supported",
    # Request Signing (E2E-01)
    "sign_api_request",
    "verify_signature",
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
    "StrategySandbox",
    "SandboxConfig",
    "SandboxResult",
    "SandboxViolation",
    "create_sandboxed_strategy",
    # C-02: Secret Store (DPAPI/Keychain)
    "SecretStore",
    "get_secret_store",
    "store_secret",
    "retrieve_secret",
    "delete_secret",
    "list_secret_keys",
    # C-03: Binary Integrity
    "IntegrityChecker",
    "IntegrityResult",
    "compute_file_hash",
    "verify_file_integrity",
    "generate_manifest",
    "verify_manifest",
    # M-03: Update Checker
    "UpdateChecker",
    "UpdateStatus",
    "UpdateManifest",
    "UpdateResult",
    "VersionInfo",
    "generate_update_manifest",
]
