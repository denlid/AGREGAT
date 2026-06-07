"""
AGREGAT Server — Session Model

Sessions table: active user sessions tied to user+device+license.
Heartbeat updates last_heartbeat_at. Revoked sessions block access.
"""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import String, Text, DateTime, func
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.user import User
    from app.models.device import Device
    from app.models.license import License


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    device_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    license_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    refresh_token_hash: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_heartbeat_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoke_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    client_metadata: Mapped[dict] = mapped_column(JSONB, default={})

    # Relationships
    user: Mapped["User"] = relationship("User")
    device: Mapped["Device"] = relationship("Device", back_populates="sessions")
    license: Mapped["License"] = relationship("License", back_populates="sessions")

    @property
    def is_active(self) -> bool:
        return (
            self.revoked_at is None
            and datetime.now(self.expires_at.tzinfo) <= self.expires_at
        )

    @property
    def is_revoked(self) -> bool:
        return self.revoked_at is not None

    def __repr__(self) -> str:
        return f"<Session {self.id} user={self.user_id} device={self.device_id}>"