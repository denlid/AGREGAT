"""
AGREGAT Server — Admin Licenses API

Full CRUD for licenses: create, list, update, revoke, view details.
"""

import uuid
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select, func, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.license import License
from app.models.user import User
from app.schemas.license import (
    LicenseResponse,
    LicenseCreateRequest,
    LicenseUpdateRequest,
    LicenseRevokeRequest,
    LicenseListResponse,
)
from app.api.deps import require_admin, require_csrf_token, get_ip_hash
from app.services.license_service import create_license, revoke_license
from app.services.audit import log_event

router = APIRouter(prefix="/licenses", tags=["admin-licenses"])


@router.post("", response_model=LicenseResponse)
async def create_license_endpoint(
    req: LicenseCreateRequest,
    request: Request,
    admin=Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    _csrf=Depends(require_csrf_token),  # C-05: CSRF protection
):
    """Create a new license for a user."""
    license_obj = await create_license(
        db=db,
        email=req.email,
        tier=req.tier,
        days=req.days,
        max_devices=req.max_devices,
        notes=req.notes,
        created_by=admin.id,
    )

    # H-03: Return PLAINTEXT key only at creation time (shown once to admin)
    # The DB stores only the hash — this plaintext is never recoverable again
    plaintext_key = getattr(license_obj, "_plaintext_key", "HIDDEN")

    return LicenseResponse(
        id=str(license_obj.id),
        user_id=str(license_obj.user_id),
        user_email=req.email,
        tier=license_obj.tier,
        status=license_obj.status,
        activation_key=plaintext_key,  # Plaintext shown ONCE at creation
        starts_at=license_obj.starts_at,
        expires_at=license_obj.expires_at,
        max_devices=license_obj.max_devices,
        notes=license_obj.notes,
        created_at=license_obj.created_at,
    )


@router.get("", response_model=LicenseListResponse)
async def list_licenses(
    page: int = 1,
    per_page: int = 20,
    search: str = "",
    tier: str = "",
    status_filter: str = "",
    admin=Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """List all licenses with pagination and filters."""
    query = select(License).order_by(License.created_at.desc())

    if search:
        # H-03: If search looks like a key (AGR-...), hash it first
        from app.services.license_service import hash_activation_key
        search_term = search.strip().upper()
        if search_term.startswith("AGR-"):
            key_hash = hash_activation_key(search_term)
            query = query.join(User).where(
                or_(
                    User.email.ilike(f"%{search}%"),
                    License.activation_key == key_hash,
                )
            )
        else:
            query = query.join(User).where(
                User.email.ilike(f"%{search}%")
            )
    if tier:
        query = query.where(License.tier == tier)
    if status_filter:
        query = query.where(License.status == status_filter)

    # Count total
    count_query = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_query)).scalar() or 0

    # Paginate
    offset = (page - 1) * per_page
    query = query.offset(offset).limit(per_page)
    result = await db.execute(query)
    licenses = result.scalars().all()

    # Load user emails
    license_responses = []
    for lic in licenses:
        user_stmt = select(User.email).where(User.id == lic.user_id)
        user_result = await db.execute(user_stmt)
        email = user_result.scalar()

        # H-03: Never expose the hash — show "HIDDEN" for existing licenses
        license_responses.append(LicenseResponse(
            id=str(lic.id),
            user_id=str(lic.user_id),
            user_email=email,
            tier=lic.tier,
            status=lic.status,
            activation_key="HIDDEN",  # Hash stored in DB — plaintext not recoverable
            starts_at=lic.starts_at,
            expires_at=lic.expires_at,
            max_devices=lic.max_devices,
            notes=lic.notes,
            created_at=lic.created_at,
            revoked_at=lic.revoked_at,
            revoke_reason=lic.revoke_reason,
        ))

    return LicenseListResponse(
        licenses=license_responses,
        total=total,
        page=page,
        per_page=per_page,
    )


@router.get("/{license_id}", response_model=LicenseResponse)
async def get_license(
    license_id: uuid.UUID,
    admin=Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Get license details by ID."""
    stmt = select(License).where(License.id == license_id)
    result = await db.execute(stmt)
    lic = result.scalar_one_or_none()

    if not lic:
        raise HTTPException(status_code=404, detail="license_not_found")

    user_stmt = select(User.email).where(User.id == lic.user_id)
    user_result = await db.execute(user_stmt)
    email = user_result.scalar()

    # H-03: Never expose the hash
    return LicenseResponse(
        id=str(lic.id),
        user_id=str(lic.user_id),
        user_email=email,
        tier=lic.tier,
        status=lic.status,
        activation_key="HIDDEN",
        starts_at=lic.starts_at,
        expires_at=lic.expires_at,
        max_devices=lic.max_devices,
        notes=lic.notes,
        created_at=lic.created_at,
        revoked_at=lic.revoked_at,
        revoke_reason=lic.revoke_reason,
    )


@router.patch("/{license_id}", response_model=LicenseResponse)
async def update_license(
    license_id: uuid.UUID,
    req: LicenseUpdateRequest,
    request: Request,
    admin=Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    _csrf=Depends(require_csrf_token),  # C-05: CSRF protection
):
    """Update license details."""
    stmt = select(License).where(License.id == license_id)
    result = await db.execute(stmt)
    lic = result.scalar_one_or_none()

    if not lic:
        raise HTTPException(status_code=404, detail="license_not_found")

    changes = {}
    if req.tier is not None:
        changes["tier"] = {"old": lic.tier, "new": req.tier}
        lic.tier = req.tier
    if req.expires_at is not None:
        changes["expires_at"] = {"old": lic.expires_at.isoformat(), "new": req.expires_at.isoformat()}
        lic.expires_at = req.expires_at
    if req.max_devices is not None:
        changes["max_devices"] = {"old": lic.max_devices, "new": req.max_devices}
        lic.max_devices = req.max_devices
    if req.notes is not None:
        lic.notes = req.notes

    await log_event(
        db,
        action="license.updated",
        actor_user_id=admin.id,
        actor_role=admin.role,
        target_type="license",
        target_id=str(license_id),
        ip_hash=get_ip_hash(request),
        payload=changes,
    )

    await db.flush()
    await db.refresh(lic)

    user_stmt = select(User.email).where(User.id == lic.user_id)
    user_result = await db.execute(user_stmt)
    email = user_result.scalar()

    # H-03: Never expose the hash
    return LicenseResponse(
        id=str(lic.id),
        user_id=str(lic.user_id),
        user_email=email,
        tier=lic.tier,
        status=lic.status,
        activation_key="HIDDEN",
        starts_at=lic.starts_at,
        expires_at=lic.expires_at,
        max_devices=lic.max_devices,
        notes=lic.notes,
        created_at=lic.created_at,
        revoked_at=lic.revoked_at,
        revoke_reason=lic.revoke_reason,
    )


@router.post("/{license_id}/revoke", response_model=LicenseResponse)
async def revoke_license_endpoint(
    license_id: uuid.UUID,
    req: LicenseRevokeRequest,
    request: Request,
    admin=Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    _csrf=Depends(require_csrf_token),  # C-05: CSRF protection
):
    """Revoke a license (kills all active sessions immediately)."""
    try:
        lic = await revoke_license(
            db=db,
            license_id=license_id,
            reason=req.reason,
            actor_user_id=admin.id,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    user_stmt = select(User.email).where(User.id == lic.user_id)
    user_result = await db.execute(user_stmt)
    email = user_result.scalar()

    # H-03: Never expose the hash
    return LicenseResponse(
        id=str(lic.id),
        user_id=str(lic.user_id),
        user_email=email,
        tier=lic.tier,
        status=lic.status,
        activation_key="HIDDEN",
        starts_at=lic.starts_at,
        expires_at=lic.expires_at,
        max_devices=lic.max_devices,
        notes=lic.notes,
        created_at=lic.created_at,
        revoked_at=lic.revoked_at,
        revoke_reason=lic.revoke_reason,
    )
