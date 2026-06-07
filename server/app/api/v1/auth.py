"""
AGREGAT Server — V1 Auth API

License activation, token refresh, logout.

P1-1: Tightened rate limiting on activation (3 attempts per 5 min).
P1-2: Audit logging for all client auth events.
P1-5: Session fixation protection (revoke old sessions on re-activation).
"""

import hashlib
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas.auth import (
    ActivateRequest,
    ActivateResponse,
    RefreshRequest,
    RefreshResponse,
    LogoutResponse,
    ErrorResponse,
    EntitlementItem,
)
from app.schemas.user import MeResponse
from app.api.deps import (
    get_ip_hash,
    get_current_session,
    get_current_user,
    get_current_license,
    get_current_device,
    rate_limit_dependency,
)
from app.security.fingerprint import validate_fingerprint
from app.services.license_service import activate_license
from app.services.auth_service import refresh_session, logout
from app.services.audit import log_event

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/activate",
    response_model=ActivateResponse,
    responses={400: {"model": ErrorResponse}, 429: {"model": ErrorResponse}},
    # P1-1: Strict rate limiting on activation (3 attempts per 5 min per IP)
    dependencies=[Depends(rate_limit_dependency("activate", max_requests=3, window_seconds=300))],
)
async def activate(
    req: ActivateRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Activate a license key on a device.
    Returns access_token + refresh_token + entitlements.
    
    P1-1: Rate limited to 3 attempts per 5 minutes per IP.
    P1-2: Audit logged for all activation attempts (success + failure).
    P1-5: Previous sessions for this device are revoked (session fixation protection).
    """
    # Validate fingerprint
    is_valid, reason = validate_fingerprint(req.device_fingerprint)
    if not is_valid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"invalid_fingerprint: {reason}",
        )

    ip_hash = get_ip_hash(request)

    try:
        result = await activate_license(
            db=db,
            email=req.email,
            activation_key=req.activation_key,
            device_fingerprint=req.device_fingerprint,
            device_name=req.device_name,
            os_info=req.os_info,
            app_version=req.app_version,
            ip_hash=ip_hash,
        )
    except ValueError as e:
        error_code = str(e)
        
        # P1-2: Audit log failed activation attempts
        await log_event(
            db,
            action="client.activation_failed",
            ip_hash=ip_hash,
            payload={
                "email_prefix": req.email[:3] + "***" if req.email else "",
                "error": error_code,
                "device_fp_hash": hashlib.sha256(req.device_fingerprint.encode()).hexdigest()[:16] if req.device_fingerprint else "",
            },
        )
        
        status_codes = {
            "invalid_key": status.HTTP_400_BAD_REQUEST,
            "license_expired": status.HTTP_403_FORBIDDEN,
            "email_mismatch": status.HTTP_403_FORBIDDEN,
            "user_suspended": status.HTTP_403_FORBIDDEN,
            "device_limit_reached": status.HTTP_403_FORBIDDEN,
            "device_revoked": status.HTTP_403_FORBIDDEN,
        }
        raise HTTPException(
            status_code=status_codes.get(error_code, status.HTTP_400_BAD_REQUEST),
            detail=error_code,
        )

    # P1-2: Audit log successful activation
    await log_event(
        db,
        action="client.activated",
        ip_hash=ip_hash,
        payload={
            "user_id": result["user_id"],
            "device_fp_hash": hashlib.sha256(req.device_fingerprint.encode()).hexdigest()[:16] if req.device_fingerprint else "",
            "tier": result.get("license_tier", ""),
        },
    )

    # C-01: Return signed receipt — required for client-side verification
    return ActivateResponse(
        status="activated",
        user_id=result["user_id"],
        access_token=result["access_token"],
        refresh_token=result["refresh_token"],
        entitlements=[EntitlementItem(**e) for e in result["entitlements"]],
        expires_at=result["expires_at"],
        license_tier=result["license_tier"],
        receipt=result["receipt"],  # Ed25519 signed receipt for client verification
    )


@router.post(
    "/refresh",
    response_model=RefreshResponse,
    dependencies=[Depends(rate_limit_dependency("refresh", max_requests=30, window_seconds=60))],
)
async def refresh(
    req: RefreshRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Refresh access token using refresh token from request body.
    """
    try:
        result = await refresh_session(db, req.refresh_token, get_ip_hash(request))
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(e),
        )

    return RefreshResponse(
        access_token=result["access_token"],
        expires_at=result["expires_at"],
    )


@router.post("/logout", response_model=LogoutResponse)
async def logout_endpoint(
    request: Request,
    session=Depends(get_current_session),
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Logout: revoke current session."""
    await logout(db, session.id, user.id)
    
    # P1-2: Audit log logout
    await log_event(
        db,
        action="client.logout",
        actor_user_id=user.id,
        ip_hash=get_ip_hash(request),
        payload={"session_id": str(session.id)},
    )
    
    return LogoutResponse(status="logged_out")


@router.get("/me", response_model=MeResponse)
async def me(
    user=Depends(get_current_user),
    license_obj=Depends(get_current_license),
    device=Depends(get_current_device),
    db: AsyncSession = Depends(get_db),
):
    """Get current user info + license details."""
    from app.services.license_service import get_license_entitlements

    entitlements = await get_license_entitlements(db, license_obj.id)

    return MeResponse(
        user_id=str(user.id),
        email=user.email,
        license_tier=license_obj.tier,
        license_expires_at=license_obj.expires_at,
        entitlements=[EntitlementItem(**e) for e in entitlements],
        device_id=str(device.id),
    )