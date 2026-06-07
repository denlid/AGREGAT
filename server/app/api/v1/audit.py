"""
AGREGAT Server — V1 Audit API

Client-side audit event logging.
Desktop clients send security-relevant events to the server.
"""

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas.auth import (
    ClientAuditEventRequest,
    ClientAuditEventResponse,
)
from app.api.deps import (
    get_current_session,
    get_current_user,
    get_ip_hash,
    get_user_agent_hash,
)
from app.services.audit import log_event

router = APIRouter(prefix="/audit", tags=["audit"])


@router.post("/client-event", response_model=ClientAuditEventResponse)
async def client_audit_event(
    req: ClientAuditEventRequest,
    request: Request,
    session=Depends(get_current_session),
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Log a client-side audit event.
    Used for security monitoring: strategy execution, file access, etc.
    """
    ip_hash = get_ip_hash(request)
    ua_hash = get_user_agent_hash(request)

    event = await log_event(
        db=db,
        action=f"client.{req.action}",
        actor_user_id=user.id,
        actor_role=user.role,
        ip_hash=ip_hash,
        user_agent_hash=ua_hash,
        payload=req.payload or {},
    )

    return ClientAuditEventResponse(
        status="logged",
        event_id=event.id,
    )