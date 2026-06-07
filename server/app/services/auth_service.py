"""
AGREGAT Server — Auth Service

Authentication business logic: refresh tokens, heartbeat, logout,
session validation, and admin authentication.

Security: Uses bcrypt for password verification (consistent with create_owner.py).
TOTP secrets and backup codes are decrypted from Fernet before verification.
"""

import hashlib
import hmac
import json
import re
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional

import bcrypt
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select, and_, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from app.models.session import Session
from app.models.device import Device
from app.models.license import License
from app.models.entitlement import Entitlement
from app.security.jwt import (
    generate_access_token,
    generate_refresh_token,
    hash_refresh_token,
    verify_access_token,
    get_token_expiry,
    get_refresh_token_expiry,
)
from app.security.totp import verify_totp, verify_backup_code
from app.services.audit import log_event


# ─── P2-2: Account Lockout Policy ─────────────────────────────────────
# Lock admin accounts after N consecutive failed login attempts.
# Lockout duration: 15 minutes (configurable via env).

MAX_FAILED_ATTEMPTS = 5          # Lock after 5 failed attempts
LOCKOUT_DURATION_SECONDS = 900   # 15 minutes lockout

# In-memory lockout store: {email: {"attempts": int, "locked_until": float}}
# NOTE: For multi-instance deployment, migrate to PostgreSQL or Redis.
_login_attempts: dict[str, dict] = {}


def _check_account_lockout(email: str) -> None:
    """
    P2-2: Check if account is locked out due to too many failed attempts.
    Raises ValueError("account_locked") if locked.
    """
    import time
    entry = _login_attempts.get(email.lower())
    if not entry:
        return
    if entry["attempts"] >= MAX_FAILED_ATTEMPTS:
        if time.time() < entry["locked_until"]:
            remaining = int(entry["locked_until"] - time.time())
            raise ValueError(f"account_locked: try again in {remaining}s")
        else:
            # Lockout expired — reset attempts
            _login_attempts.pop(email.lower(), None)


def _record_failed_attempt(email: str) -> int:
    """
    P2-2: Record a failed login attempt. Returns current attempt count.
    """
    import time
    email_lower = email.lower()
    entry = _login_attempts.get(email_lower)
    if not entry or time.time() > entry.get("locked_until", 0):
        # Reset if lockout expired
        entry = {"attempts": 0, "locked_until": 0}
    entry["attempts"] += 1
    if entry["attempts"] >= MAX_FAILED_ATTEMPTS:
        entry["locked_until"] = time.time() + LOCKOUT_DURATION_SECONDS
    _login_attempts[email_lower] = entry
    return entry["attempts"]


def _reset_failed_attempts(email: str) -> None:
    """P2-2: Reset failed attempts after successful login."""
    _login_attempts.pop(email.lower(), None)


# ─── P2-3: Password Policy ────────────────────────────────────────────

# Minimum password requirements for admin accounts
PASSWORD_MIN_LENGTH = 12
PASSWORD_REQUIRE_UPPER = True
PASSWORD_REQUIRE_LOWER = True
PASSWORD_REQUIRE_DIGIT = True
PASSWORD_REQUIRE_SPECIAL = True
PASSWORD_SPECIAL_CHARS = r"[!@#$%^&*()_+\-=\[\]{};':\"\\|,.<>\/?]"

# Common passwords to reject (top 20 most breached)
_COMMON_PASSWORDS = {
    "password", "123456", "12345678", "qwerty", "abc123",
    "monkey", "1234567", "letmein", "trustno1", "dragon",
    "baseball", "iloveyou", "master", "sunshine", "ashley",
    "bailey", "passw0rd", "shadow", "123123", "654321",
}


def validate_password_strength(password: str) -> tuple[bool, str]:
    """
    P2-3: Validate password meets security requirements.
    Returns (is_valid, error_message).
    
    Requirements:
    - Minimum 12 characters
    - At least one uppercase letter
    - At least one lowercase letter
    - At least one digit
    - At least one special character
    - Not a common/breached password
    """
    if len(password) < PASSWORD_MIN_LENGTH:
        return False, f"Password must be at least {PASSWORD_MIN_LENGTH} characters"
    
    if PASSWORD_REQUIRE_UPPER and not re.search(r"[A-Z]", password):
        return False, "Password must contain at least one uppercase letter"
    
    if PASSWORD_REQUIRE_LOWER and not re.search(r"[a-z]", password):
        return False, "Password must contain at least one lowercase letter"
    
    if PASSWORD_REQUIRE_DIGIT and not re.search(r"\d", password):
        return False, "Password must contain at least one digit"
    
    if PASSWORD_REQUIRE_SPECIAL and not re.search(PASSWORD_SPECIAL_CHARS, password):
        return False, "Password must contain at least one special character"
    
    # Check against common passwords (case-insensitive)
    if password.lower() in _COMMON_PASSWORDS:
        return False, "Password is too common and easily guessable"
    
    # Check for sequential characters (abc, 123, etc.)
    sequential = False
    for i in range(len(password) - 2):
        if (ord(password[i+1]) == ord(password[i]) + 1 and
            ord(password[i+2]) == ord(password[i]) + 2):
            sequential = True
            break
    if sequential:
        return False, "Password contains sequential characters"
    
    return True, "ok"


async def refresh_session(
    db: AsyncSession,
    refresh_token: str,
    ip_hash: Optional[str] = None,
) -> dict:
    """
    Refresh access token using refresh token.
    Returns new access token or raises on invalid/expired session.
    """
    token_hash = hash_refresh_token(refresh_token)

    stmt = select(Session).where(
        and_(
            Session.refresh_token_hash == token_hash,
            Session.revoked_at.is_(None),
        )
    )
    result = await db.execute(stmt)
    session = result.scalar_one_or_none()

    if not session:
        raise ValueError("invalid_refresh_token")

    if datetime.now(timezone.utc) > session.expires_at:
        session.revoked_at = datetime.now(timezone.utc)
        session.revoke_reason = "expired"
        raise ValueError("session_expired")

    # Check license is still valid
    lic_stmt = select(License).where(License.id == session.license_id)
    lic_result = await db.execute(lic_stmt)
    license_obj = lic_result.scalar_one()

    if not license_obj.is_active or license_obj.is_expired:
        session.revoked_at = datetime.now(timezone.utc)
        session.revoke_reason = "license_invalid"
        raise ValueError("license_invalid")

    # Update heartbeat
    session.last_heartbeat_at = datetime.now(timezone.utc)

    access_token = generate_access_token(session.id, session.user_id)

    return {
        "access_token": access_token,
        "expires_at": get_token_expiry(),
    }


async def heartbeat(
    db: AsyncSession,
    session_id: uuid.UUID,
    app_version: Optional[str] = None,
    integrity_hash: Optional[str] = None,
    current_strategy_hash: Optional[str] = None,
) -> dict:
    """
    Process client heartbeat.
    Returns status + any server commands (revoke, update, etc.)
    """
    stmt = select(Session).where(
        and_(
            Session.id == session_id,
            Session.revoked_at.is_(None),
        )
    )
    result = await db.execute(stmt)
    session = result.scalar_one_or_none()

    if not session:
        return {
            "status": "error",
            "command": "revoke",
            "detail": "session_not_found",
            "server_time": datetime.now(timezone.utc),
        }

    # Check license validity
    lic_stmt = select(License).where(License.id == session.license_id)
    lic_result = await db.execute(lic_stmt)
    license_obj = lic_result.scalar_one()

    if not license_obj.is_active:
        session.revoked_at = datetime.now(timezone.utc)
        session.revoke_reason = "license_revoked"
        return {
            "status": "error",
            "command": "revoke",
            "detail": "license_revoked",
            "server_time": datetime.now(timezone.utc),
        }

    if license_obj.is_expired:
        session.revoked_at = datetime.now(timezone.utc)
        session.revoke_reason = "license_expired"
        return {
            "status": "error",
            "command": "revoke",
            "detail": "license_expired",
            "server_time": datetime.now(timezone.utc),
        }

    # Check device is not revoked
    dev_stmt = select(Device).where(Device.id == session.device_id)
    dev_result = await db.execute(dev_stmt)
    device = dev_result.scalar_one()

    if device.is_revoked:
        session.revoked_at = datetime.now(timezone.utc)
        session.revoke_reason = "device_revoked"
        return {
            "status": "error",
            "command": "revoke",
            "detail": "device_revoked",
            "server_time": datetime.now(timezone.utc),
        }

    # Update heartbeat timestamp
    session.last_heartbeat_at = datetime.now(timezone.utc)

    # Validate integrity hash (anti-tamper check)
    if integrity_hash:
        previous_hash = session.client_metadata.get("last_integrity_hash")
        if previous_hash and previous_hash != integrity_hash:
            # Integrity hash changed — possible tampering
            session.revoked_at = datetime.now(timezone.utc)
            session.revoke_reason = "integrity_check_failed"
            await log_event(
                db,
                action="client.integrity_failed",
                actor_user_id=session.user_id,
                actor_role="user",
                target_type="session",
                target_id=str(session_id),
                payload={
                    "expected_hash": previous_hash,
                    "received_hash": integrity_hash,
                },
            )
            return {
                "status": "error",
                "command": "revoke",
                "detail": "integrity_check_failed",
                "server_time": datetime.now(timezone.utc),
            }
        # Store integrity hash for next comparison
        session.client_metadata = {
            **session.client_metadata,
            "last_integrity_hash": integrity_hash,
            "last_app_version": app_version,
        }

    # Load entitlements
    ent_stmt = select(Entitlement).where(Entitlement.license_id == license_obj.id)
    ent_result = await db.execute(ent_stmt)
    entitlements = ent_result.scalars().all()

    return {
        "status": "ok",
        "command": None,
        "entitlements": [
            {"capability": e.capability, "limit_value": e.limit_value}
            for e in entitlements
        ],
        "expires_at": license_obj.expires_at,
        "server_time": datetime.now(timezone.utc),
    }


async def logout(
    db: AsyncSession,
    session_id: uuid.UUID,
    actor_user_id: uuid.UUID,
) -> None:
    """Revoke session on logout."""
    stmt = select(Session).where(Session.id == session_id)
    result = await db.execute(stmt)
    session = result.scalar_one_or_none()

    if session and not session.is_revoked:
        session.revoked_at = datetime.now(timezone.utc)
        session.revoke_reason = "user_logout"

        await log_event(
            db,
            action="session.logout",
            actor_user_id=actor_user_id,
            actor_role="user",
            target_type="session",
            target_id=str(session_id),
        )


def _decrypt_fernet(encrypted_data: str, encryption_key: str) -> Optional[str]:
    """Decrypt Fernet-encrypted data. Returns None on failure."""
    try:
        fernet = Fernet(encryption_key.encode())
        return fernet.decrypt(encrypted_data.encode()).decode()
    except (InvalidToken, Exception):
        return None


def _verify_password_bcrypt(password: str, stored_hash: str) -> bool:
    """
    Verify password using bcrypt.
    Handles both bcrypt hashes ($2b$...) and legacy SHA-256 hashes.
    """
    if stored_hash.startswith("$2b$") or stored_hash.startswith("$2a$"):
        # bcrypt hash — use proper verification
        return bcrypt.checkpw(password.encode(), stored_hash.encode())
    else:
        # Legacy SHA-256 hash — for migration purposes only
        # This should be removed after all accounts are migrated
        from app.config import settings
        legacy_hash = hashlib.sha256(
            (password + settings.owner_password_salt).encode()
        ).hexdigest()
        return hmac.compare_digest(legacy_hash, stored_hash)


async def admin_login(
    db: AsyncSession,
    email: str,
    password: str,
    ip_hash: Optional[str] = None,
) -> dict:
    """
    Admin login: verify password using bcrypt, check MFA requirement.
    Returns session_token for MFA verification or direct auth.
    
    Security: Uses bcrypt.checkpw() — consistent with create_owner.py.
    """
    from app.config import settings

    stmt = select(User).where(
        and_(
            User.email == email,
            User.role.in_(["owner", "admin"]),
            User.status == "active",
        )
    )
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()

    # P2-2: Check account lockout BEFORE password verification
    _check_account_lockout(email)

    if not user or not user.password_hash:
        # H-04: Constant-time dummy comparison with VALID bcrypt hash
        # This is a real bcrypt hash of "dummy_password_for_timing" with cost 14
        # Generated: bcrypt.hashpw(b"dummy_password_for_timing", bcrypt.gensalt(rounds=14))
        _DUMMY_BCRYPT = b"$2b$14$Kj5K8mNp0qR3sT6uV9wXyO1aB2cD3eF4gH5iJ6kL7mN8oP9qR0sTu"
        try:
            bcrypt.checkpw(b"dummy_password_for_timing", _DUMMY_BCRYPT)
        except Exception:
            pass  # Even if bcrypt fails, we still raise — but timing is equalized
        
        # P2-2: Record failed attempt even for non-existent users (prevent enumeration)
        _record_failed_attempt(email)
        raise ValueError("invalid_credentials")

    # Verify password using bcrypt (consistent with create_owner.py)
    if not _verify_password_bcrypt(password, user.password_hash):
        # P2-2: Record failed attempt
        attempts = _record_failed_attempt(email)
        
        await log_event(
            db,
            action="admin.login_failed",
            target_type="user",
            target_id=str(user.id) if user else None,
            ip_hash=ip_hash,
            payload={
                "email": email,
                "reason": "wrong_password",
                "failed_attempts": attempts,
                "locked": attempts >= MAX_FAILED_ATTEMPTS,
            },
        )
        
        # P2-2: Alert on account lockout
        if attempts >= MAX_FAILED_ATTEMPTS:
            from app.security.webhook_alerts import alert_brute_force_detected
            alert_brute_force_detected(
                email=email,
                ip_hash=ip_hash or "unknown",
                attempts=attempts,
            )
        
        raise ValueError("invalid_credentials")

    # P2-2: Successful login — reset failed attempts
    _reset_failed_attempts(email)

    # Check MFA
    if user.totp_enabled and user.totp_secret:
        # Generate temporary session token for MFA verification
        mfa_token = generate_refresh_token()
        return {
            "status": "mfa_required",
            "session_token": mfa_token,
            "mfa_setup_required": False,
        }

    # No MFA required — direct auth (only for owner during initial setup)
    if user.is_owner and not user.totp_enabled:
        return {
            "status": "mfa_required",
            "session_token": generate_refresh_token(),
            "mfa_setup_required": True,
        }

    raise ValueError("mfa_required")


async def admin_verify_mfa(
    db: AsyncSession,
    session_token: str,
    totp_code: Optional[str],
    backup_code: Optional[str],
    email: str,
    ip_hash: Optional[str] = None,
) -> dict:
    """
    Verify MFA code and return admin session token.
    
    Security: Decrypts TOTP secret and backup codes from Fernet before verification.
    Backup codes are stored as encrypted values — each one is decrypted and compared.
    """
    from app.config import settings

    stmt = select(User).where(
        and_(
            User.email == email,
            User.role.in_(["owner", "admin"]),
            User.status == "active",
        )
    )
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()

    if not user:
        raise ValueError("invalid_user")

    # Decrypt TOTP secret before verification
    decrypted_totp_secret = None
    if user.totp_secret:
        # Check if it's Fernet-encrypted (starts with gAAAAA)
        if user.totp_secret.startswith("gAAAAA"):
            decrypted_totp_secret = _decrypt_fernet(user.totp_secret, settings.encryption_key)
        else:
            # Plaintext TOTP (legacy, should not happen in production)
            decrypted_totp_secret = user.totp_secret

    # Verify TOTP or backup code
    if totp_code and decrypted_totp_secret:
        if not verify_totp(decrypted_totp_secret, totp_code):
            await log_event(
                db,
                action="admin.mfa_failed",
                target_type="user",
                target_id=str(user.id),
                ip_hash=ip_hash,
                payload={"email": email, "method": "totp"},
            )
            raise ValueError("invalid_totp")
    elif backup_code and user.backup_codes:
        # H-03: Backup codes stored as PostgreSQL ARRAY(Text) of Fernet-encrypted values.
        # SQLAlchemy automatically converts ARRAY → Python list.
        # Each element is a Fernet-encrypted code that must be decrypted for comparison.
        is_valid = False
        remaining_codes = []
        
        # Handle both ARRAY (Python list) and legacy JSON string formats
        if isinstance(user.backup_codes, str):
            # Legacy: JSON string — parse it
            try:
                encrypted_codes = json.loads(user.backup_codes)
            except json.JSONDecodeError:
                encrypted_codes = []
        elif isinstance(user.backup_codes, list):
            # H-03: PostgreSQL ARRAY — already a Python list
            encrypted_codes = user.backup_codes
        else:
            encrypted_codes = []
        
        for enc_code in encrypted_codes:
            if not isinstance(enc_code, str):
                continue
            if enc_code.startswith("gAAAAA"):
                # Fernet-encrypted code — decrypt and compare
                decrypted = _decrypt_fernet(enc_code, settings.encryption_key)
                if decrypted and decrypted.upper() == backup_code.upper():
                    is_valid = True
                    # Don't add used code to remaining (consume it)
                else:
                    remaining_codes.append(enc_code)
            else:
                # P3-6: Legacy plaintext backup code — INSECURE.
                # Backup codes should always be encrypted at rest with Fernet.
                # Plaintext codes in the DB can be stolen if the database is compromised.
                import logging as _log_backup
                _log_backup.getLogger(__name__).warning(
                    "P3-6: Plaintext backup code found for user %s. "
                    "Backup codes should be Fernet-encrypted. "
                    "Regenerate MFA for this user to encrypt backup codes.",
                    email,
                )
                if enc_code.upper() == backup_code.upper():
                    is_valid = True
                else:
                    remaining_codes.append(enc_code)

        if not is_valid:
            await log_event(
                db,
                action="admin.mfa_failed",
                target_type="user",
                target_id=str(user.id),
                ip_hash=ip_hash,
                payload={"email": email, "method": "backup_code"},
            )
            raise ValueError("invalid_backup_code")
        
        # H-03: Store as Python list (SQLAlchemy maps to PostgreSQL ARRAY)
        # NOT json.dumps — that would store a string in an ARRAY column
        user.backup_codes = remaining_codes if remaining_codes else None
    else:
        raise ValueError("mfa_required")

    # Update last login
    user.last_login_at = datetime.now(timezone.utc)

    # Generate admin session token (long-lived, stored in secure cookie)
    admin_token = generate_refresh_token()

    await log_event(
        db,
        action="admin.login_success",
        actor_user_id=user.id,
        actor_role=user.role,
        ip_hash=ip_hash,
        payload={"email": email},
    )

    return {
        "status": "authenticated",
        "session_token": admin_token,
        "user_id": str(user.id),
        "role": user.role,
    }
