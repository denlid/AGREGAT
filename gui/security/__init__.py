"""
AGREGAT GUI — Security Module

M-01 FIX: CSP for QWebEngineView
M-02 FIX: Secure file permissions for settings
M-04 FIX: SSL verification for custom AI endpoints
M-06 FIX: Export path validation
H-03 FIX: AI code sharing consent & sanitization
H-04 FIX: Drag-and-drop file validation
"""

from .web_security import configure_web_security, WebSecurityConfig
from .file_security import (
    validate_dropped_file,
    validate_export_path,
    save_settings_secure,
    secure_file_permissions,
)
from .ai_privacy import (
    AIPrivacyManager,
    PrivacyLevel,
    SanitizationResult,
    ConsentRecord,
    SENSITIVE_PATTERNS,
    CRITICAL_PATTERNS,
    create_privacy_dialog_message,
)

__all__ = [
    # M-01: Web Security (CSP)
    'configure_web_security',
    'WebSecurityConfig',
    # M-02, M-06, H-04: File Security
    'validate_dropped_file',
    'validate_export_path',
    'save_settings_secure',
    'secure_file_permissions',
    # H-03: AI Privacy & Code Sanitization
    'AIPrivacyManager',
    'PrivacyLevel',
    'SanitizationResult',
    'ConsentRecord',
    'SENSITIVE_PATTERNS',
    'CRITICAL_PATTERNS',
    'create_privacy_dialog_message',
]
