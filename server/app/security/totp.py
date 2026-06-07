""" AGREGAT Server — TOTP (Time-based One-Time Password) MFA for admin panel. Compatible with Google Authenticator, Authy, etc. """

import base64
import hashlib
import hmac
import secrets
import struct
import time
from typing import Optional
from urllib.parse import quote


def generate_totp_secret() -> str:
    """Generate a random Base32-encoded TOTP secret."""
    raw = secrets.token_bytes(20)
    return base64.b32encode(raw).decode("ascii")


def _hotp(secret: str, counter: int) -> str:
    """
    HMAC-based One-Time Password (RFC 4226).

    P1-3: Uses SHA-256 instead of SHA-1 for stronger HMAC.
    Compatible with modern authenticator apps (Google Authenticator, Authy, etc.)
    that support SHA-256 TOTP.
    """
    key = base64.b32decode(secret.upper())
    msg = struct.pack(">Q", counter)
    h = hmac.new(key, msg, hashlib.sha256).digest()
    offset = h[-1] & 0x0F
    code = struct.unpack(">I", h[offset:offset + 4])[0]
    code = (code & 0x7FFFFFFF) % 1000000
    return f"{code:06d}"


def generate_totp(secret: str, period: int = 30, t: Optional[int] = None) -> str:
    """Generate current TOTP code."""
    if t is None:
        t = int(time.time())
    counter = t // period
    return _hotp(secret, counter)


def verify_totp(
    secret: str,
    code: str,
    period: int = 30,
    window: int = 1,
) -> bool:
    """
    Verify TOTP code with time window tolerance.
    window=1 allows previous, current, and next period.

    NEW-CRIT-02 FIX: Uses hmac.compare_digest() for constant-time comparison
    to prevent timing side-channel attacks. Python == is NOT constant-time —
    it short-circuits on first mismatch, leaking information about correct
    digits via response time.
    """
    code = code.strip()
    if len(code) != 6 or not code.isdigit():
        return False

    t = int(time.time())
    counter = t // period

    for offset in range(-window, window + 1):
        expected = _hotp(secret, counter + offset)
        # CONSTANT-TIME comparison — prevents timing side-channel
        if hmac.compare_digest(expected, code):
            return True

    return False


def generate_totp_uri(secret: str, email: str, issuer: str = "AGREGAT") -> str:
    """
    Generate otpauth:// URI for QR code.

    P1-3: Uses SHA256 algorithm instead of SHA1.
    """
    encoded_issuer = quote(issuer)
    encoded_email = quote(email)
    return (
        f"otpauth://totp/{encoded_issuer}:{encoded_email}"
        f"?secret={secret}&issuer={encoded_issuer}"
        f"&algorithm=SHA256&digits=6&period=30"
    )


def generate_backup_codes(count: int = 8) -> list[str]:
    """
    Generate one-time backup codes (12-digit hex = 48 bits entropy).

    P1-4: Increased from 32-bit (8 hex chars) to 48-bit (12 hex chars).
    48 bits = ~281 trillion combinations. Even with unlimited attempts,
    brute-forcing is computationally infeasible.
    """
    codes = []
    for _ in range(count):
        code = secrets.token_hex(6)  # 12 hex chars = 48 bits
        codes.append(code)
    return codes


def verify_backup_code(stored_codes: list[str], code: str) -> tuple[bool, list[str]]:
    """
    Verify and consume a backup code.
    Returns (is_valid, remaining_codes).

    NEW-CRIT-02 FIX: Uses hmac.compare_digest for constant-time comparison
    on each backup code to prevent timing-based enumeration.
    """
    code = code.strip().lower()
    for stored in stored_codes:
        if hmac.compare_digest(stored, code):
            remaining = [c for c in stored_codes if c != stored]
            return True, remaining
    return False, stored_codes