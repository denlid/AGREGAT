"""
AGREGAT Server — JWT Token Management

Opaque token strategy: tokens are random strings stored server-side.
No sensitive data in tokens. Server is source of truth.
"""

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt as pyjwt

from app.config import settings


def generate_access_token(session_id: str, user_id: str) -> str:
    """Generate short-lived access token (5 min)."""
    payload = {
        "sub": str(user_id),
        "sid": str(session_id),
        "type": "access",
        "iat": datetime.now(timezone.utc),
        "exp": datetime.now(timezone.utc) + timedelta(minutes=settings.jwt_access_token_expire_minutes),
        "jti": secrets.token_hex(16),
    }
    return pyjwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def generate_refresh_token() -> str:
    """Generate opaque refresh token (random string, stored server-side)."""
    return secrets.token_urlsafe(48)


def hash_refresh_token(token: str) -> str:
    """Hash refresh token for storage (never store plaintext)."""
    return hashlib.sha256(token.encode()).hexdigest()


def verify_access_token(token: str) -> Optional[dict]:
    """Verify and decode access token. Returns payload or None."""
    try:
        payload = pyjwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
        if payload.get("type") != "access":
            return None
        return payload
    except (pyjwt.ExpiredSignatureError, pyjwt.InvalidTokenError):
        return None


def get_token_expiry() -> datetime:
    """Calculate access token expiry time."""
    return datetime.now(timezone.utc) + timedelta(minutes=settings.jwt_access_token_expire_minutes)


def get_refresh_token_expiry() -> datetime:
    """Calculate refresh token expiry time."""
    return datetime.now(timezone.utc) + timedelta(days=settings.jwt_refresh_token_expire_days)