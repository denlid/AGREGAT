"""
AGREGAT Server — Admin Session Model

Persistent admin session storage in PostgreSQL.
Replaces the in-memory _admin_sessions dict for production use.

Security:
- Token is stored as SHA-256 hash (never plaintext)
- Each session has explicit expiry
- Revocation support for forced logout
- IP hash binding for session hijacking detection
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    String,
    Text,
    Boolean,
    Index,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID, JSONB
from sqlalchemy.orm import relationship

from app.database import Base


class AdminSession(Base):
    """
    Persistent admin session.
    Created after successful MFA verification.
    """

    __tablename__ = "admin_sessions"

    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )

    # SHA-256 hash of the session token (never store plaintext)
    token_hash = Column(String(64), nullable=False, unique=True)

    # User role at time of login (snapshot — checked against DB on each request)
    role = Column(String(20), nullable=False)  # "owner" or "admin"

    # IP hash at login time (for anomaly detection)
    ip_hash_at_login = Column(String(64), nullable=True)

    # User agent hash (for anomaly detection)
    user_agent_hash = Column(String(64), nullable=True)

    # Expiration
    expires_at = Column(DateTime(timezone=True), nullable=False)

    # Revocation
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    revoke_reason = Column(String(100), nullable=True)

    # Metadata
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    last_activity_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    # Relationship
    user = relationship("User", backref="admin_sessions")

    # Indexes
    __table_args__ = (
        Index("ix_admin_sessions_token_hash", "token_hash"),
        Index("ix_admin_sessions_user_id", "user_id"),
        Index("ix_admin_sessions_expires_at", "expires_at"),
    )

    @property
    def is_active(self) -> bool:
        """Check if session is active (not expired, not revoked)."""
        now = datetime.now(timezone.utc)
        return (
            self.revoked_at is None
            and self.expires_at > now
        )

    @property
    def is_expired(self) -> bool:
        """Check if session is expired."""
        return datetime.now(timezone.utc) > self.expires_at

    @property
    def is_revoked(self) -> bool:
        """Check if session has been revoked."""
        return self.revoked_at is not None

    def __repr__(self) -> str:
        return f"<AdminSession id={self.id} user_id={self.user_id} role={self.role} active={self.is_active}>"