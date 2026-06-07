"""
AGREGAT Server — Entitlement Model

Entitlements table: granular capabilities granted per license.
Replaces simple boolean license check with fine-grained access control.
"""

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import String, Integer, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.license import License


class Entitlement(Base):
    __tablename__ = "entitlements"
    __table_args__ = (
        UniqueConstraint("license_id", "capability", name="uq_license_capability"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    license_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    capability: Mapped[str] = mapped_column(String(100), nullable=False)
    limit_value: Mapped[int | None] = mapped_column(Integer, nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JSONB, default={})

    # Relationships
    license: Mapped["License"] = relationship("License", back_populates="entitlements")

    def __repr__(self) -> str:
        return f"<Entitlement {self.capability} limit={self.limit_value}>"