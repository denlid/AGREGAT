"""
AGREGAT Server — License Schemas

Request/response models for license management.
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, EmailStr, Field


class LicenseResponse(BaseModel):
    """License details"""
    id: str
    user_id: str
    user_email: Optional[str] = None
    tier: str
    status: str
    activation_key: str
    starts_at: datetime
    expires_at: datetime
    max_devices: int
    notes: Optional[str] = None
    created_at: datetime
    revoked_at: Optional[datetime] = None
    revoke_reason: Optional[str] = None


class LicenseCreateRequest(BaseModel):
    """POST /admin/licenses"""
    email: EmailStr
    tier: str = "beta"
    days: int = Field(..., ge=1, le=3650)
    max_devices: int = Field(1, ge=1, le=10)
    notes: Optional[str] = None


class LicenseUpdateRequest(BaseModel):
    """PATCH /admin/licenses/:id"""
    tier: Optional[str] = None
    expires_at: Optional[datetime] = None
    max_devices: Optional[int] = None
    notes: Optional[str] = None


class LicenseRevokeRequest(BaseModel):
    """POST /admin/licenses/:id/revoke"""
    reason: Optional[str] = None


class LicenseListResponse(BaseModel):
    """GET /admin/licenses"""
    licenses: list[LicenseResponse]
    total: int
    page: int
    per_page: int