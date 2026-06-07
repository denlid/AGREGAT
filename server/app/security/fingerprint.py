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
    MH-09: Hash fingerprint with optional server-side pepper (HMAC-SHA256).
    
    SECURITY: Raw hardware fingerprints are sensitive tracking data.
    Storing HMAC(pepper, fingerprint) prevents DB leaks from exposing
    raw hardware identifiers. The pepper is stored in .env, not in DB.
    
    When pepper is empty (dev/legacy), falls back to plain SHA-256.
    """
    normalized = normalize_fingerprint(fp)
    if pepper:
        import hmac as _hmac
        return _hmac.new(
            pepper.encode(),
            normalized.encode(),
            hashlib.sha256,
        ).hexdigest()
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