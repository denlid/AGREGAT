"""
AGREGAT Server — Admin Schemas

Request/response models for admin panel endpoints.
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, EmailStr, Field


class AdminLoginRequest(BaseModel):
    """POST /admin/auth/login"""
    email: EmailStr
    password: str = Field(..., min_length=1)


class AdminLoginResponse(BaseModel):
    status: str  # "mfa_required" or "authenticated"
    session_token: Optional[str] = None
    mfa_setup_required: bool = False


class MFAVerifyRequest(BaseModel):
    """POST /admin/auth/mfa/verify and /admin/auth/mfa/enable"""
    session_token: Optional[str] = None  # Required for mfa/verify, optional for mfa/enable
    totp_code: Optional[str] = Field(None, min_length=6, max_length=8)
    backup_code: Optional[str] = None


class MFASetupResponse(BaseModel):
    """POST /admin/auth/mfa/setup"""
    totp_uri: str
    totp_secret: str
    backup_codes: list[str]


class DashboardStats(BaseModel):
    """GET /admin/stats"""
    total_users: int
    active_users: int
    total_licenses: int
    active_licenses: int
    expired_licenses: int
    revoked_licenses: int
    active_sessions: int
    total_devices: int
    total_strategies: int
    recent_audit_events: list[dict]


class DeviceResponse(BaseModel):
    """Device details"""
    id: str
    user_id: str
    user_email: Optional[str] = None
    device_fingerprint: str
    device_name: Optional[str] = None
    os_info: Optional[str] = None
    app_version: Optional[str] = None
    first_seen_at: datetime
    last_seen_at: datetime
    revoked_at: Optional[datetime] = None
    revoke_reason: Optional[str] = None


class DeviceListResponse(BaseModel):
    devices: list[DeviceResponse]
    total: int
    page: int
    per_page: int


class SessionResponse(BaseModel):
    """Session details"""
    id: str
    user_id: str
    user_email: Optional[str] = None
    device_id: str
    device_name: Optional[str] = None
    license_id: str
    issued_at: datetime
    expires_at: datetime
    last_heartbeat_at: datetime
    revoked_at: Optional[datetime] = None


class SessionListResponse(BaseModel):
    sessions: list[SessionResponse]
    total: int
    page: int
    per_page: int


class StrategyResponse(BaseModel):
    """Strategy details"""
    id: str
    user_id: str
    user_email: Optional[str] = None
    strategy_hash: str
    strategy_code: Optional[str] = None
    file_name: Optional[str] = None
    has_code: bool = False
    first_seen_at: datetime
    last_seen_at: datetime
    usage_count: int
    metadata: Optional[dict] = None


class StrategyListResponse(BaseModel):
    strategies: list[StrategyResponse]
    total: int
    page: int
    per_page: int


class AuditEventResponse(BaseModel):
    """Audit event details"""
    id: int
    ts: datetime
    actor_user_id: Optional[str] = None
    actor_role: Optional[str] = None
    action: str
    target_type: Optional[str] = None
    target_id: Optional[str] = None
    payload_json: Optional[dict] = None
    event_hash: str
    prev_event_hash: Optional[str] = None


class AuditListResponse(BaseModel):
    events: list[AuditEventResponse]
    total: int
    page: int
    per_page: int


class ChainIntegrityResponse(BaseModel):
    """GET /admin/audit/integrity"""
    is_valid: bool
    total_events: int
    first_broken_id: Optional[int] = None
    first_broken_action: Optional[str] = None
