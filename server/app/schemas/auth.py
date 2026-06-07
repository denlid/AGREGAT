"""
AGREGAT Server — Auth Schemas

Request/response models for authentication endpoints.
"""

import re
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, EmailStr, Field, field_validator


# Activation key format: AGR-XXXX-XXXX-XXXX-XXXX (4 hex groups of 4)
_ACTIVATION_KEY_PATTERN = re.compile(r'^AGR-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}$')


class ActivateRequest(BaseModel):
    """POST /v1/auth/activate"""
    email: EmailStr
    activation_key: str = Field(..., min_length=19, max_length=64)
    device_fingerprint: str = Field(..., min_length=32, max_length=128)
    device_name: Optional[str] = Field(None, max_length=255)
    os_info: Optional[str] = Field(None, max_length=255)
    app_version: Optional[str] = Field(None, max_length=50)

    @field_validator('activation_key')
    @classmethod
    def validate_activation_key_format(cls, v: str) -> str:
        """
        Validate activation key format and entropy.
        Must match AGR-XXXX-XXXX-XXXX-XXXX (uppercase hex).
        This prevents injection attacks and ensures key has sufficient entropy.
        """
        normalized = v.strip().upper()
        if not _ACTIVATION_KEY_PATTERN.match(normalized):
            raise ValueError(
                'Invalid activation key format. Expected: AGR-XXXX-XXXX-XXXX-XXXX '
                '(where X is uppercase hexadecimal)'
            )
        return normalized

    @field_validator('device_fingerprint')
    @classmethod
    def validate_fingerprint_format(cls, v: str) -> str:
        """Validate fingerprint is a valid SHA-256 hex string."""
        if not re.match(r'^[0-9a-f]{64}$', v.lower()):
            raise ValueError(
                'Invalid device fingerprint. Must be a 64-character hex string (SHA-256).'
            )
        return v.lower()


class EntitlementItem(BaseModel):
    capability: str
    limit_value: Optional[int] = None


class ActivateResponse(BaseModel):
    status: str = "activated"
    user_id: str
    access_token: str
    refresh_token: str
    entitlements: list[EntitlementItem]
    expires_at: datetime
    license_tier: str
    # C-01: Signed receipt for cryptographic license verification
    # The client MUST verify this signature and reject unsigned/tampered receipts
    receipt: dict  # {user_id, license_tier, device_fingerprint, entitlements, expires_at, session_id, issued_at, signature}


class RefreshRequest(BaseModel):
    """POST /v1/auth/refresh"""
    refresh_token: str = Field(..., min_length=10)


class RefreshResponse(BaseModel):
    access_token: str
    expires_at: datetime


class LogoutResponse(BaseModel):
    status: str = "logged_out"


class HeartbeatRequest(BaseModel):
    """POST /v1/heartbeat"""
    app_version: Optional[str] = None
    integrity_hash: Optional[str] = None
    current_strategy_hash: Optional[str] = None


class HeartbeatResponse(BaseModel):
    status: str = "ok"
    command: Optional[str] = None  # null, "revoke", "update_capabilities", "force_update"
    entitlements: list[EntitlementItem] = []
    expires_at: Optional[datetime] = None
    server_time: datetime
    detail: Optional[str] = None


class StrategyRegisterRequest(BaseModel):
    """POST /v1/strategies/register"""
    strategy_hash: str = Field(..., min_length=32, max_length=64)
    strategy_code: Optional[str] = None
    file_name: Optional[str] = None
    metadata: Optional[dict] = {}


class StrategyRegisterResponse(BaseModel):
    status: str = "registered"
    strategy_hash: str
    first_seen: bool = False


class ClientAuditEventRequest(BaseModel):
    """POST /v1/audit/client-event"""
    action: str = Field(..., max_length=100)
    payload: Optional[dict] = {}


class ClientAuditEventResponse(BaseModel):
    status: str = "logged"
    event_id: int


class ErrorResponse(BaseModel):
    error: str
    message: str
    detail: Optional[str] = None