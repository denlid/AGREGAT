"""
AGREGAT Server — Admin Sessions API

Session management: list active sessions, revoke sessions.
"""

import uuid
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.session import Session
from app.models.user import User
from app.models.device import Device
from app.schemas.admin import SessionResponse, SessionListResponse
from app.api.deps import require_admin, require_csrf_token, get_ip_hash
from app.services.audit import log_event

router = APIRouter(prefix="/sessions", tags=["admin-sessions"])


@router.get("", response_model=SessionListResponse)
async def list_sessions(
    page: int = 1,
    per_page: int = 20,
    user_id: str = "",
    active_only: bool = True,
    admin=Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    query = select(Session).order_by(Session.last_heartbeat_at.desc().nulls_last())
    if user_id:
        query = query.where(Session.user_id == uuid.UUID(user_id))
    if active_only:
        query = query.where(Session.revoked_at.is_(None))

    total = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar() or 0
    offset = (page - 1) * per_page
    result = await db.execute(query.offset(offset).limit(per_page))
    sessions = result.scalars().all()

    items = []
    for s in sessions:
        email = (await db.execute(select(User.email).where(User.id == s.user_id))).scalar()
        device_name = (await db.execute(select(Device.device_name).where(Device.id == s.device_id))).scalar()
        items.append(SessionResponse(
            id=str(s.id), user_id=str(s.user_id), user_email=email,
            device_id=str(s.device_id), device_name=device_name,
            license_id=str(s.license_id),
            issued_at=s.issued_at, expires_at=s.expires_at,
            last_heartbeat_at=s.last_heartbeat_at,
            revoked_at=s.revoked_at,
        ))

    return SessionListResponse(sessions=items, total=total, page=page, per_page=per_page)


@router.post("/{session_id}/revoke")
async def revoke_session(
    session_id: uuid.UUID,
    request: Request,
    admin=Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    _csrf=Depends(require_csrf_token),  # C-05: CSRF protection
):
    session = (await db.execute(select(Session).where(Session.id == session_id))).scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="session_not_found")

    from datetime import datetime, timezone
    session.revoked_at = datetime.now(timezone.utc)
    session.revoke_reason = "revoked_by_admin"

    await log_event(db, action="session.revoked", actor_user_id=admin.id, actor_role=admin.role,
                    target_type="session", target_id=str(session_id), ip_hash=get_ip_hash(request))
    return {"status": "revoked"}