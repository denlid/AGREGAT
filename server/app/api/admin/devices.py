"""
AGREGAT Server — Admin Devices API

Device management: list, revoke, view details.
"""

import uuid
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.device import Device
from app.models.user import User
from app.schemas.admin import DeviceResponse, DeviceListResponse
from app.api.deps import require_admin, require_csrf_token, get_ip_hash
from app.services.audit import log_event

router = APIRouter(prefix="/devices", tags=["admin-devices"])


@router.get("", response_model=DeviceListResponse)
async def list_devices(
    page: int = 1,
    per_page: int = 20,
    user_id: str = "",
    admin=Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    query = select(Device).order_by(Device.last_seen_at.desc().nulls_last())
    if user_id:
        query = query.where(Device.user_id == uuid.UUID(user_id))

    total = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar() or 0
    offset = (page - 1) * per_page
    result = await db.execute(query.offset(offset).limit(per_page))
    devices = result.scalars().all()

    items = []
    for d in devices:
        email = (await db.execute(select(User.email).where(User.id == d.user_id))).scalar()
        items.append(DeviceResponse(
            id=str(d.id), user_id=str(d.user_id), user_email=email,
            device_fingerprint=d.device_fingerprint, device_name=d.device_name,
            os_info=d.os_info, app_version=d.app_version,
            first_seen_at=d.first_seen_at, last_seen_at=d.last_seen_at,
            revoked_at=d.revoked_at, revoke_reason=d.revoke_reason,
        ))

    return DeviceListResponse(devices=items, total=total, page=page, per_page=per_page)


@router.post("/{device_id}/revoke")
async def revoke_device(
    device_id: uuid.UUID,
    request: Request,
    admin=Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    _csrf=Depends(require_csrf_token),  # C-05: CSRF protection
):
    device = (await db.execute(select(Device).where(Device.id == device_id))).scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="device_not_found")

    from datetime import datetime, timezone
    device.revoked_at = datetime.now(timezone.utc)

    # Revoke all sessions for this device
    from app.models.session import Session
    sessions = (await db.execute(
        select(Session).where(and_(Session.device_id == device_id, Session.revoked_at.is_(None)))
    )).scalars().all()
    for s in sessions:
        s.revoked_at = datetime.now(timezone.utc)
        s.revoke_reason = "device_revoked_by_admin"

    await log_event(db, action="device.revoked", actor_user_id=admin.id, actor_role=admin.role,
                    target_type="device", target_id=str(device_id), ip_hash=get_ip_hash(request))
    return {"status": "revoked"}