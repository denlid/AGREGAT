"""
AGREGAT Server — Auth Schemas

Request/response models for authentication endpoints.
"""

import json
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
    """
    POST /v1/strategies/register
    
    M-NEW-06 FIX: Size limits added to prevent storage abuse.
    Previously: no limits on strategy_code, file_name, metadata — attacker could
    send megabytes of data to fill up the database.
    """
    strategy_hash: str = Field(..., min_length=32, max_length=64)
    # M-NEW-06: Limit strategy code to 50KB (reasonable for Python strategy files)
    strategy_code: Optional[str] = Field(None, max_length=50_000)
    # M-NEW-06: Limit filename to 255 chars (filesystem standard)
    file_name: Optional[str] = Field(None, max_length=255)
    metadata: Optional[dict] = {}

    @field_validator("strategy_code")
    @classmethod
    def validate_strategy_code_size(cls, v: Optional[str]) -> Optional[str]:
        """M-NEW-06: Reject strategy code exceeding 50KB."""
        if v and len(v.encode("utf-8")) > 50_000:
            raise ValueError("strategy_code exceeds maximum size of 50KB")
        return v

    @field_validator("metadata")
    @classmethod
    def validate_metadata_size(cls, v: Optional[dict]) -> Optional[dict]:
        """M-NEW-06: Reject metadata exceeding 10KB when serialized."""
        if v:
            try:
                serialized_size = len(json.dumps(v).encode("utf-8"))
                if serialized_size > 10_000:
                    raise ValueError("metadata exceeds maximum size of 10KB")
            except (TypeError, ValueError) as e:
                raise ValueError(f"metadata must be valid JSON-serializable dict: {e}")
        return v

    @field_validator("file_name")
    @classmethod
    def validate_file_name_safety(cls, v: Optional[str]) -> Optional[str]:
        """M-NEW-06: Prevent path traversal in file_name."""
        if v:
            # Block path traversal attempts
            if ".." in v or "/" in v or "\\" in v:
                raise ValueError("file_name must not contain path separators or '..'")
            # Only allow safe characters
            if not re.match(r'^[a-zA-Z0-9_\-\. ]+$', v):
                raise ValueError("file_name contains invalid characters")
        return v


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