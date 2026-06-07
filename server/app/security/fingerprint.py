"""
AGREGAT Server — Device Fingerprint Validation

Validates and normalizes hardware fingerprints from desktop clients.
Detects suspicious patterns (VMs, emulators, known spoofing tools).
"""

import hashlib
import re
from typing import Optional


def normalize_fingerprint(fp: str) -> str:
    """Normalize fingerprint: lowercase, strip whitespace."""
    return fp.strip().lower()


def validate_fingerprint(fp: str) -> tuple[bool, str]:
    """
    Validate device fingerprint format and detect suspicious patterns.
    Returns (is_valid, reason).
    """
    if not fp or len(fp) < 32 or len(fp) > 128:
        return False, "invalid_length"

    # Must be hex or base64-like characters
    if not re.match(r'^[a-f0-9+/=_-]+$', fp):
        return False, "invalid_format"

    # Detect known VM/emulator patterns
    vm_patterns = [
        "0000000000000000",  # All zeros
        "ffffffffffffffff",  # All ones
        "vmware", "virtualbox", "qemu", "kvm", "hyper-v",
        "parallels", "bochs", "xen",
    ]
    fp_lower = fp.lower()
    for pattern in vm_patterns:
        if pattern in fp_lower:
            return False, f"vm_detected:{pattern}"

    # Detect repeating patterns (likely fake)
    if len(set(fp)) < 8:
        return False, "low_entropy"

    return True, "ok"


def hash_fingerprint(fp: str, pepper: str = "") -> str:
    """
    P1-11: Hash fingerprint with server-side pepper (HMAC-SHA256).
    
    SECURITY: Raw hardware fingerprints are sensitive tracking data.
    Storing HMAC(pepper, fingerprint) prevents DB leaks from exposing
    raw hardware identifiers. The pepper is stored in .env, not in DB.
    
    P1-11: In production, pepper is REQUIRED. Without it, a DB leak
    exposes raw fingerprints that can be reversed via rainbow tables.
    In development, falls back to SHA-256 with a warning.
    """
    import os as _os_fp
    import logging as _log_fp
    
    normalized = normalize_fingerprint(fp)
    
    if pepper:
        import hmac as _hmac
        return _hmac.new(
            pepper.encode(),
            normalized.encode(),
            hashlib.sha256,
        ).hexdigest()
    
    # P1-11: No pepper — check environment
    app_env = _os_fp.environ.get("APP_ENV", "production").lower()
    if app_env == "production":
        _log_fp.getLogger(__name__).error(
            "P1-11: hash_fingerprint called without pepper in production! "
            "Set FINGERPRINT_PEPPER in config.py. Falling back to SHA-256 "
            "(INSECURE — fingerprints can be reversed via rainbow tables)."
        )
    else:
        _log_fp.getLogger(__name__).warning(
            "P1-11: hash_fingerprint called without pepper in development. "
            "Set FINGERPRINT_PEPPER for production deployment."
        )
    
    return hashlib.sha256(normalized.encode()).hexdigest()


def compute_device_risk_score(
    fingerprint: str,
    ip_hash: Optional[str] = None,
    os_info: Optional[str] = None,
) -> dict:
    """
    Compute risk score for a device.
    Returns dict with score (0-100) and flags.
    """
    score = 0
    flags = []

    is_valid, reason = validate_fingerprint(fingerprint)
    if not is_valid:
        score += 50
        flags.append(reason)

    if os_info:
        os_lower = os_info.lower()
        if any(vm in os_lower for vm in ["vmware", "virtualbox", "qemu", "sandbox"]):
            score += 30
            flags.append("vm_os_detected")

    return {
        "score": min(score, 100),
        "flags": flags,
        "is_suspicious": score >= 40,
    }