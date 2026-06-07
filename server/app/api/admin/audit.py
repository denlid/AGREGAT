"""
AGREGAT Server — Admin Audit API

View audit log, verify chain integrity.
"""

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas.admin import AuditEventResponse, AuditListResponse, ChainIntegrityResponse
from app.api.deps import require_admin
from app.services.audit import get_recent_events, verify_chain_integrity

router = APIRouter(prefix="/audit", tags=["admin-audit"])


@router.get("", response_model=AuditListResponse)
async def list_audit_events(
    page: int = 1,
    per_page: int = 50,
    action_filter: str = "",
    admin=Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    limit = per_page
    events = await get_recent_events(db, limit=limit, action_filter=action_filter or None)

    items = [
        AuditEventResponse(
            id=e.id,
            ts=e.ts,
            actor_user_id=str(e.actor_user_id) if e.actor_user_id else None,
            actor_role=e.actor_role,
            action=e.action,
            target_type=e.target_type,
            target_id=e.target_id,
            payload_json=e.payload_json,
            event_hash=e.event_hash,
            prev_event_hash=e.prev_event_hash,
        )
        for e in events
    ]

    return AuditListResponse(events=items, total=len(items), page=page, per_page=per_page)


@router.get("/integrity", response_model=ChainIntegrityResponse)
async def check_chain_integrity(
    admin=Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await verify_chain_integrity(db)
    return ChainIntegrityResponse(**result)