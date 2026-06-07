"""
AGREGAT Server — MFA Token Model

P0-3 FIX: Persistent MFA token storage in PostgreSQL.
Replaces the in-memory _mfa_tokens dict for production use.

Security:
- Token is stored as SHA-256 hash (never plaintext)
- Short TTL (5 minutes) — auto-expired
- Single-use: consumed after successful verification
- Works across multiple server instances (HA-ready)
"""

import uuid
from datetime import datetime, timezone, timedelta

from sqlalchemy import (
    Column,
    DateTime,
    String,
    Text,
    Index,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

from app.database import Base


class MfaToken(Base):
    """
    Short-lived MFA token for admin login flow.
    Created after password verification, consumed after TOTP verification.
    
    Replaces in-memory _mfa_tokens dict (P0-3 fix).
    """

    __tablename__ = "mfa_tokens"

    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    
    # Email (lowercase) — used to look up the token
    email = Column(String(255), nullable=False, index=True)
    
    # SHA-256 hash of the MFA token (never store plaintext)
    token_hash = Column(String(64), nullable=False, unique=True)
    
    # Expiration (5 minutes by default)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    
    # Whether the token has been consumed (single-use)
    consumed = Column(String(1), default="0", nullable=False)  # "0" or "1"
    
    # Metadata
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    # Indexes
    __table_args__ = (
        Index("ix_mfa_tokens_email", "email"),
        Index("ix_mfa_tokens_token_hash", "token_hash"),
        Index("ix_mfa_tokens_expires_at", "expires_at"),
    )

    @property
    def is_expired(self) -> bool:
        """Check if token is expired."""
        return datetime.now(timezone.utc) > self.expires_at

    @property
    def is_consumed(self) -> bool:
        """Check if token has been consumed."""
        return self.consumed == "1"

    @property
    def is_valid(self) -> bool:
        """Check if token is valid (not expired, not consumed)."""
        return not self.is_expired and not self.is_consumed

    def __repr__(self) -> str:
        return f"<MfaToken email={self.email} valid={self.is_valid}>"