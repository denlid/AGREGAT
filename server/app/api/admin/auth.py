"""
AGREGAT Server — Admin Auth API

Admin login, MFA setup, MFA verification.
"""

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas.admin import (
    AdminLoginRequest,
    AdminLoginResponse,
    MFAVerifyRequest,
    MFASetupResponse,
)
from app.api.deps import get_ip_hash, get_user_agent_hash, require_admin, require_csrf_token, store_admin_session, remove_admin_session, store_mfa_token, verify_mfa_token, consume_mfa_token, rate_limit_dependency
from app.services.auth_service import admin_login, admin_verify_mfa
from app.services.audit import log_event
from app.security.webhook_alerts import alert_admin_login, alert_brute_force_detected
from app.security.totp import (
    generate_totp_secret,
    generate_totp_uri,
    generate_backup_codes,
    verify_totp,
)

router = APIRouter(prefix="/auth", tags=["admin-auth"])

# ─── Rate limits for admin auth (H-01) ─────────────────────────────────
# Protect against brute force attacks on login and MFA endpoints
_admin_login_rate_limit = rate_limit_dependency(
    endpoint="admin_login",
    max_requests=10,       # 10 login attempts per window
    window_seconds=300.0,  # 5 minute window
)
_admin_mfa_rate_limit = rate_limit_dependency(
    endpoint="admin_mfa_verify",
    max_requests=15,       # 15 MFA attempts per window
    window_seconds=300.0,  # 5 minute window
)


@router.post("/login", response_model=AdminLoginResponse)
async def admin_login_endpoint(
    req: AdminLoginRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _rate_limit=Depends(_admin_login_rate_limit),
):
    """
    Admin login step 1: verify email + password.
    Returns MFA challenge (always required for admins).
    """
    ip_hash = get_ip_hash(request)

    try:
        result = await admin_login(
            db=db,
            email=req.email,
            password=req.password,
            ip_hash=ip_hash,
        )
        
        # E2E-06: Send webhook alert for successful admin login
        alert_admin_login(
            email=req.email,
            ip_hash=ip_hash,
            success=True,
            mfa=True,
        )
    except ValueError as e:
        # E2E-06: Send webhook alert for failed admin login
        alert_admin_login(
            email=req.email,
            ip_hash=ip_hash,
            success=False,
            mfa=False,
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(e),
        )

    response = JSONResponse(content={
        "status": result["status"],
        "session_token": result.get("session_token"),
        "mfa_setup_required": result.get("mfa_setup_required", False),
    })

    # Store MFA token server-side for verification
    if result.get("session_token"):
        store_mfa_token(req.email, result["session_token"], ttl_seconds=300)

    # Set session token + email in secure cookies (email needed for MFA verify step)
    if result.get("session_token"):
        response.set_cookie(
            key="admin_mfa_token",
            value=result["session_token"],
            httponly=True,
            secure=True,
            samesite="strict",
            max_age=300,  # 5 minutes for MFA
        )
        response.set_cookie(
            key="admin_email",
            value=req.email,
            httponly=True,
            secure=True,
            samesite="strict",
            max_age=300,
        )

    return response


@router.post("/mfa/verify")
async def admin_mfa_verify(
    req: MFAVerifyRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
    _rate_limit=Depends(_admin_mfa_rate_limit),
):
    """
    Admin login step 2: verify TOTP or backup code.
    Returns authenticated session token.
    """
    ip_hash = get_ip_hash(request)

    # Extract email and MFA token from cookies
    mfa_token = request.cookies.get("admin_mfa_token") or req.session_token
    email = request.cookies.get("admin_email")
    if not email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="mfa_session_expired",
        )

    # Verify MFA token matches what was issued for this email (prevents token reuse)
    if not verify_mfa_token(email, mfa_token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="mfa_token_invalid_or_expired",
        )

    try:
        result = await admin_verify_mfa(
            db=db,
            session_token=mfa_token,
            totp_code=req.totp_code,
            backup_code=req.backup_code,
            email=email,
            ip_hash=ip_hash,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(e),
        )

    # Consume MFA token (single-use)
    consume_mfa_token(email)

    # P2-4: Store session in PostgreSQL with 1-hour expiry (was 24h)
    # Shorter sessions reduce the window for session hijacking
    import uuid as _uuid
    await store_admin_session(
        db=db,
        token=result["session_token"],
        user_id=_uuid.UUID(result["user_id"]),
        role=result["role"],
        ip_hash=ip_hash,
        user_agent_hash=get_user_agent_hash(request),
        ttl_seconds=3600,  # P2-4: 1 hour (was 86400 = 24h)
    )
    await db.commit()  # Persist the admin session

    # Set authenticated admin cookie
    response = JSONResponse(content={
        "status": result["status"],
        "user_id": result["user_id"],
        "role": result["role"],
    })

    response.set_cookie(
        key="admin_session",
        value=result["session_token"],
        httponly=True,
        secure=True,
        samesite="strict",
        max_age=3600,  # P2-4: 1 hour (was 86400 = 24h)
    )

    # Clear MFA token
    response.delete_cookie("admin_mfa_token")
    response.delete_cookie("admin_email")

    return response


@router.post("/mfa/setup", response_model=MFASetupResponse)
async def admin_mfa_setup(
    request: Request,
    db: AsyncSession = Depends(get_db),
    _rate_limit=Depends(_admin_mfa_rate_limit),
):
    """
    Setup TOTP MFA for admin (first-time setup).
    Returns TOTP URI for QR code + backup codes.
    
    H-01: Uses MFA token from login flow (not full admin session) since
    the user hasn't completed MFA setup yet. This fixes the broken
    first-time setup flow where require_admin would fail.
    
    H-02: TOTP secret and backup codes are ENCRYPTED with Fernet before
    being stored in the database. Plaintext is only sent to the user once.
    """
    from app.config import settings
    from cryptography.fernet import Fernet
    from sqlalchemy import select, and_
    from app.models.user import User
    import json as _json

    # H-01: Authenticate via MFA token from login flow (not admin session)
    mfa_token = request.cookies.get("admin_mfa_token")
    email = request.cookies.get("admin_email")
    if not email or not mfa_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="mfa_session_required",
        )

    # Verify MFA token is valid and matches email
    if not verify_mfa_token(email, mfa_token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="mfa_token_invalid_or_expired",
        )

    # Load user
    stmt = select(User).where(
        and_(
            User.email == email,
            User.role.in_(["owner", "admin"]),
            User.status == "active",
        )
    )
    result = await db.execute(stmt)
    admin = result.scalar_one_or_none()
    if not admin:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="user_not_found",
        )

    # Generate new TOTP secret
    totp_secret = generate_totp_secret()
    totp_uri = generate_totp_uri(totp_secret, admin.email)
    backup_codes = generate_backup_codes(8)

    # H-02: Encrypt TOTP secret before storing in database
    fernet = Fernet(settings.encryption_key.encode())
    encrypted_totp_secret = fernet.encrypt(totp_secret.encode()).decode()

    # H-02: Encrypt each backup code individually before storing
    encrypted_codes = [
        fernet.encrypt(code.encode()).decode()
        for code in backup_codes
    ]

    # Store ENCRYPTED values in user record (unverified until user confirms)
    admin.totp_secret = encrypted_totp_secret  # Fernet-encrypted
    admin.backup_codes = encrypted_codes  # List of Fernet-encrypted codes
    admin.totp_enabled = False  # Enabled after first successful verification

    await db.flush()  # Persist changes

    await log_event(
        db,
        action="admin.mfa_setup_initiated",
        actor_user_id=admin.id,
        actor_role=admin.role,
        ip_hash=get_ip_hash(request),
    )

    # Return PLAINTEXT to user (only time they see it)
    return MFASetupResponse(
        totp_uri=totp_uri,
        totp_secret=totp_secret,
        backup_codes=backup_codes,
    )


@router.post("/mfa/enable")
async def admin_mfa_enable(
    request: Request,
    req: MFAVerifyRequest,
    db: AsyncSession = Depends(get_db),
    _rate_limit=Depends(_admin_mfa_rate_limit),
):
    """
    Enable MFA after verifying first TOTP code.
    
    H-01: Uses MFA token from login flow (not full admin session) since
    the user hasn't completed MFA setup yet. After enabling, the user
    can proceed through the normal MFA verify flow to get a full session.
    
    H-02: Decrypts TOTP secret from Fernet before verification.
    """
    from app.config import settings
    from cryptography.fernet import Fernet, InvalidToken
    from sqlalchemy import select, and_
    from app.models.user import User

    # H-01: Authenticate via MFA token from login flow (not admin session)
    mfa_token = request.cookies.get("admin_mfa_token")
    email = request.cookies.get("admin_email")
    if not email or not mfa_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="mfa_session_required",
        )

    # Verify MFA token is valid and matches email
    if not verify_mfa_token(email, mfa_token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="mfa_token_invalid_or_expired",
        )

    # Load user
    stmt = select(User).where(
        and_(
            User.email == email,
            User.role.in_(["owner", "admin"]),
            User.status == "active",
        )
    )
    result = await db.execute(stmt)
    admin = result.scalar_one_or_none()
    if not admin:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="user_not_found",
        )

    totp_code = req.totp_code
    if not admin.totp_secret:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="mfa_not_setup",
        )

    # H-02: Decrypt TOTP secret before verification
    decrypted_secret = None
    if admin.totp_secret.startswith("gAAAAA"):
        # Fernet-encrypted
        try:
            fernet = Fernet(settings.encryption_key.encode())
            decrypted_secret = fernet.decrypt(admin.totp_secret.encode()).decode()
        except InvalidToken:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="mfa_secret_decryption_failed",
            )
    else:
        # Plaintext (legacy — should not happen after H-02 fix)
        decrypted_secret = admin.totp_secret

    if not verify_totp(decrypted_secret, totp_code):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid_totp",
        )

    admin.totp_enabled = True

    await db.flush()  # Persist changes

    await log_event(
        db,
        action="admin.mfa_enabled",
        actor_user_id=admin.id,
        actor_role=admin.role,
        ip_hash=get_ip_hash(request),
    )

    return {"status": "mfa_enabled"}


@router.post("/logout")
async def admin_logout(
    request: Request,
    response: Response,
    admin=Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    _csrf=Depends(require_csrf_token),  # C-05: CSRF protection
):
    """Admin logout: clear session cookie and revoke session in PostgreSQL."""
    token = request.cookies.get("admin_session")
    if token:
        await remove_admin_session(db, token)
        await db.commit()
    response.delete_cookie("admin_session")

    await log_event(
        db,
        action="admin.logout",
        actor_user_id=admin.id,
        actor_role=admin.role,
        ip_hash=get_ip_hash(request),
    )

    return {"status": "logged_out"}
