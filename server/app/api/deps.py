"""
AGREGAT Server — API Dependencies

FastAPI dependencies for authentication, authorization, rate limiting,
and request context extraction.
"""

import hashlib
import uuid
from typing import Optional

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.session import Session
from app.models.user import User
from app.models.license import License
from app.models.device import Device
from app.security.jwt import verify_access_token
from app.security.rate_limit import rate_limiter, get_rate_limit_settings
from app.services.audit import log_event

security = HTTPBearer()


def get_ip_hash(request: Request) -> str:
    """
    Extract and hash client IP address.
    
    H-09: Only trust X-Forwarded-For from known trusted proxies.
    Without this, any client can spoof their IP by setting the header,
    bypassing rate limits and audit logging.
    
    Configure trusted proxies via TRUSTED_PROXIES env var (comma-separated IPs).
    Example: TRUSTED_PROXIES=127.0.0.1,10.0.0.1,172.16.0.1
    """
    import os
    
    # Get trusted proxy list from environment
    trusted_proxies_env = os.environ.get("TRUSTED_PROXIES", "")
    trusted_proxies = set(
        ip.strip() for ip in trusted_proxies_env.split(",") if ip.strip()
    )
    
    # Get the direct client IP
    direct_ip = request.client.host if request.client else "unknown"
    
    # H-09: Only trust X-Forwarded-For if the direct connection is from a trusted proxy
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded and direct_ip in trusted_proxies:
        # Take the leftmost (original client) IP from the chain
        ip = forwarded.split(",")[0].strip()
    else:
        # Untrusted source — use direct IP only
        ip = direct_ip
    
    return hashlib.sha256(ip.encode()).hexdigest()


def get_user_agent_hash(request: Request) -> str:
    """Extract and hash user agent."""
    ua = request.headers.get("user-agent", "unknown")
    return hashlib.sha256(ua.encode()).hexdigest()


async def get_current_session(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: AsyncSession = Depends(get_db),
) -> Session:
    """
    Validate access token and return active session.
    Used by all authenticated v1 endpoints.
    """
    payload = verify_access_token(credentials.credentials)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid_or_expired_token",
        )

    session_id = uuid.UUID(payload["sid"])
    user_id = uuid.UUID(payload["sub"])

    stmt = select(Session).where(
        and_(
            Session.id == session_id,
            Session.user_id == user_id,
            Session.revoked_at.is_(None),
        )
    )
    result = await db.execute(stmt)
    session = result.scalar_one_or_none()

    if not session:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="session_revoked_or_not_found",
        )

    return session


async def get_current_user(
    session: Session = Depends(get_current_session),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Get current authenticated user from session."""
    stmt = select(User).where(User.id == session.user_id)
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()

    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="user_suspended",
        )

    return user


async def get_current_license(
    session: Session = Depends(get_current_session),
    db: AsyncSession = Depends(get_db),
) -> License:
    """Get current active license from session."""
    stmt = select(License).where(License.id == session.license_id)
    result = await db.execute(stmt)
    license_obj = result.scalar_one_or_none()

    if not license_obj or not license_obj.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="license_invalid",
        )

    if license_obj.is_expired:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="license_expired",
        )

    return license_obj


async def get_current_device(
    session: Session = Depends(get_current_session),
    db: AsyncSession = Depends(get_db),
) -> Device:
    """Get current device from session."""
    stmt = select(Device).where(Device.id == session.device_id)
    result = await db.execute(stmt)
    device = result.scalar_one_or_none()

    if not device or device.is_revoked:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="device_revoked",
        )

    return device


# Admin dependencies

async def get_admin_session_token(
    request: Request,
) -> str:
    """Extract admin session token from cookie or header."""
    token = request.cookies.get("admin_session")
    if not token:
        auth_header = request.headers.get("x-admin-token")
        if auth_header:
            token = auth_header
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="admin_auth_required",
        )
    return token


# ─── CSRF Protection (C-05) ────────────────────────────────────────────
# Double Submit Cookie pattern for admin panel forms.
# The CSRF token is stored in a cookie AND must be sent in a header/body.
# Since cookies are SameSite=strict, cross-origin POST is blocked.
# The token adds defense-in-depth against same-site CSRF.

import secrets as _secrets


def generate_csrf_token() -> str:
    """Generate a cryptographically secure CSRF token (32 bytes hex)."""
    return _secrets.token_hex(32)


def verify_csrf_token(request: Request, token: str) -> bool:
    """
    Verify CSRF token from request body/header matches cookie.
    
    The token must be present in BOTH:
    1. Cookie: admin_csrf_token (set by the server)
    2. Request body/header: csrf_token field
    
    Returns True if tokens match, False otherwise.
    """
    cookie_token = request.cookies.get("admin_csrf_token")
    if not cookie_token or not token:
        return False
    # Constant-time comparison to prevent timing attacks
    import hmac as _hmac
    return _hmac.compare_digest(cookie_token, token)


async def require_csrf_token(request: Request) -> None:
    """
    MH-05: FastAPI dependency: validates CSRF token via header only.
    
    SECURITY FIX: Previously read request.form() and request.json() which
    consumes the body stream, causing conflicts with downstream body parsing
    (FastAPI can only read the body once).
    
    Now only reads from X-CSRF-Token header. The admin panel JS fetch
    already sends this header. No form body parsing needed.
    
    Raises 403 if token is missing or invalid.
    """
    # Only read from header — avoids body stream consumption
    token = request.headers.get("x-csrf-token")
    
    if not token or not verify_csrf_token(request, token):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="csrf_token_invalid_or_missing",
        )


# ─── P0-3 FIX: PostgreSQL-based MFA token store ────────────────────────
# MFA tokens are now stored in PostgreSQL (not in-memory).
# This enables multi-instance deployment (HA) and survives server restarts.
# Tokens are short-lived (5 min), single-use, and stored as SHA-256 hashes.


def _hash_admin_token(token: str) -> str:
    """Hash admin token for storage."""
    return hashlib.sha256(token.encode()).hexdigest()


async def store_admin_session(
    db: AsyncSession,
    token: str,
    user_id: uuid.UUID,
    role: str,
    ip_hash: Optional[str] = None,
    user_agent_hash: Optional[str] = None,
    ttl_seconds: int = 86400,
) -> None:
    """
    Store admin session in PostgreSQL (called after successful MFA verification).
    Token is stored as SHA-256 hash — never plaintext.
    """
    from datetime import datetime, timezone, timedelta
    from app.models.admin_session import AdminSession

    token_hash = _hash_admin_token(token)
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)

    admin_session = AdminSession(
        user_id=user_id,
        token_hash=token_hash,
        role=role,
        ip_hash_at_login=ip_hash,
        user_agent_hash=user_agent_hash,
        expires_at=expires_at,
    )
    db.add(admin_session)
    await db.flush()


async def remove_admin_session(db: AsyncSession, token: str) -> None:
    """Remove admin session from PostgreSQL (logout)."""
    from app.models.admin_session import AdminSession

    token_hash = _hash_admin_token(token)
    stmt = select(AdminSession).where(AdminSession.token_hash == token_hash)
    result = await db.execute(stmt)
    session = result.scalar_one_or_none()
    if session and not session.is_revoked:
        from datetime import datetime, timezone
        session.revoked_at = datetime.now(timezone.utc)
        session.revoke_reason = "user_logout"
        await db.flush()


# P0-3: Async PostgreSQL-based MFA token functions
# These replace the in-memory _mfa_tokens dict.
# Called from admin auth endpoints with db session.

async def store_mfa_token(db: AsyncSession, email: str, token: str, ttl_seconds: int = 300) -> None:
    """
    P0-3: Store MFA token hash in PostgreSQL (called after password verification).
    Replaces in-memory store — works across multiple server instances.
    """
    from datetime import datetime, timezone, timedelta
    from app.models.mfa_token import MfaToken
    
    # Clean up any existing tokens for this email first
    from sqlalchemy import delete
    await db.execute(
        delete(MfaToken).where(MfaToken.email == email.lower())
    )
    
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
    mfa_token = MfaToken(
        email=email.lower(),
        token_hash=_hash_admin_token(token),
        expires_at=expires_at,
    )
    db.add(mfa_token)
    await db.flush()


async def verify_mfa_token(db: AsyncSession, email: str, token: str) -> bool:
    """
    P0-3: Verify MFA token from PostgreSQL.
    Returns True if token is valid (exists, not expired, not consumed).
    """
    from sqlalchemy import select, and_
    from app.models.mfa_token import MfaToken
    
    token_hash = _hash_admin_token(token)
    stmt = select(MfaToken).where(
        and_(
            MfaToken.email == email.lower(),
            MfaToken.token_hash == token_hash,
            MfaToken.consumed == "0",
        )
    )
    result = await db.execute(stmt)
    mfa_token = result.scalar_one_or_none()
    
    if not mfa_token:
        return False
    if mfa_token.is_expired:
        # Clean up expired token
        from sqlalchemy import delete
        await db.execute(delete(MfaToken).where(MfaToken.id == mfa_token.id))
        await db.flush()
        return False
    return True


async def consume_mfa_token(db: AsyncSession, email: str) -> None:
    """
    P0-3: Mark MFA token as consumed (single-use).
    Also cleans up all MFA tokens for this email.
    """
    from sqlalchemy import delete
    from app.models.mfa_token import MfaToken
    
    # Delete all MFA tokens for this email (cleanup)
    await db.execute(
        delete(MfaToken).where(MfaToken.email == email.lower())
    )
    await db.flush()


async def require_admin(
    token: str = Depends(get_admin_session_token),
    db: AsyncSession = Depends(get_db),
) -> User:
    """
    Require admin authentication.
    Validates token against PostgreSQL admin_sessions table.
    
    Security improvements over in-memory store:
    - Survives server restarts
    - Supports multi-instance deployment
    - Token stored as SHA-256 hash (never plaintext)
    - Revocation is persistent
    """
    from app.models.admin_session import AdminSession

    token_hash = _hash_admin_token(token)

    # Look up session in PostgreSQL
    stmt = select(AdminSession).where(
        and_(
            AdminSession.token_hash == token_hash,
            AdminSession.revoked_at.is_(None),
        )
    )
    result = await db.execute(stmt)
    admin_session = result.scalar_one_or_none()

    if not admin_session:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="admin_session_invalid",
        )

    # Check expiration
    if admin_session.is_expired:
        admin_session.revoked_at = admin_session.expires_at
        admin_session.revoke_reason = "expired"
        await db.flush()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="admin_session_expired",
        )

    # Update last activity
    from datetime import datetime, timezone
    admin_session.last_activity_at = datetime.now(timezone.utc)

    # Load user from DB (always check current status)
    stmt = select(User).where(
        and_(
            User.id == admin_session.user_id,
            User.role.in_(["owner", "admin"]),
            User.status == "active",
        )
    )
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()

    if not user:
        # User was deleted or suspended — revoke session
        admin_session.revoked_at = datetime.now(timezone.utc)
        admin_session.revoke_reason = "user_suspended"
        await db.flush()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="admin_user_not_found",
        )

    # Verify role hasn't changed (e.g., owner demoted to admin)
    if user.role != admin_session.role and not user.is_owner:
        # Role changed — require re-login
        admin_session.revoked_at = datetime.now(timezone.utc)
        admin_session.revoke_reason = "role_changed"
        await db.flush()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="admin_role_changed",
        )

    return user


async def require_owner(
    admin: User = Depends(require_admin),
) -> User:
    """Require owner role (highest privilege)."""
    if not admin.is_owner:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="owner_access_required",
        )
    return admin


def rate_limit_dependency(
    endpoint: str,
    max_requests: int = 60,
    window_seconds: float = 60.0,
):
    """
    Create a rate limiting dependency for an endpoint.
    
    H-08: Supports both sync (in-memory) and async (Redis) rate limiters.
    """
    import asyncio as _asyncio

    async def check_rate_limit(
        request: Request,
        db: AsyncSession = Depends(get_db),
    ):
        ip_hash = get_ip_hash(request)

        # H-08: Handle both sync and async rate limiters
        consume_fn = rate_limiter.consume
        if _asyncio.iscoroutinefunction(consume_fn):
            result = await consume_fn(ip_hash, endpoint, max_requests, window_seconds)
        else:
            result = consume_fn(ip_hash, endpoint, max_requests, window_seconds)

        if not result.allowed:
            from app.models.audit import RateLimitEvent
            event = RateLimitEvent(
                subject=ip_hash,
                endpoint=endpoint,
                result="limited",
                metadata_json={"remaining": result.remaining},
            )
            db.add(event)
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="rate_limit_exceeded",
                headers={
                    "X-RateLimit-Limit": str(result.limit),
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": str(int(result.reset_at)),
                },
            )

    return check_rate_limit
