"""
AGREGAT Server — License Model

Licenses table: activation keys with expiration, tier, and device limits.
"""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import String, Text, Integer, DateTime, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.user import User
    from app.models.session import Session
    from app.models.entitlement import Entitlement


class License(Base):
    __tablename__ = "licenses"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    tier: Mapped[str] = mapped_column(String(20), nullable=False, default="beta")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    activation_key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    starts_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    max_devices: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoke_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="licenses")
    sessions: Mapped[list["Session"]] = relationship("Session", back_populates="license")
    entitlements: Mapped[list["Entitlement"]] = relationship(
        "Entitlement", back_populates="license", cascade="all, delete-orphan"
    )

    @property
    def is_active(self) -> bool:
        return self.status == "active" and self.revoked_at is None

    @property
    def is_expired(self) -> bool:
        return datetime.now(self.expires_at.tzinfo) > self.expires_at

    @property
    def is_revoked(self) -> bool:
        return self.revoked_at is not None

    def __repr__(self) -> str:
        return f"<License {self.activation_key} tier={self.tier} status={self.status}>"