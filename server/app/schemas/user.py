"""
AGREGAT Server — User Schemas

Request/response models for user endpoints.
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, EmailStr

from app.schemas.auth import EntitlementItem


class UserResponse(BaseModel):
    """GET /admin/users/:id"""
    id: str
    email: EmailStr
    role: str
    status: str
    created_at: datetime
    last_login_at: Optional[datetime] = None


class MeResponse(BaseModel):
    """GET /v1/me"""
    user_id: str
    email: EmailStr
    license_tier: str
    license_expires_at: datetime
    entitlements: list[EntitlementItem]
    device_id: str


class UserListResponse(BaseModel):
    """GET /admin/users"""
    users: list[UserResponse]
    total: int
    page: int
    per_page: int


class UserCreateRequest(BaseModel):
    """POST /admin/users"""
    email: EmailStr
    role: str = "user"


class UserUpdateRequest(BaseModel):
    """PATCH /admin/users/:id"""
    role: Optional[str] = None
    status: Optional[str] = None