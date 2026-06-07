"""
AGREGAT Server — Admin Dashboard API

Dashboard statistics, overview data for admin panel.
"""

from fastapi import APIRouter, Depends
from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.user import User
from app.models.license import License
from app.models.session import Session
from app.models.device import Device
from app.models.strategy import StrategyRegistry
from app.schemas.admin import DashboardStats
from app.api.deps import require_admin
from app.services.audit import get_recent_events

router = APIRouter(tags=["admin-dashboard"])


@router.get("/stats", response_model=DashboardStats)
async def dashboard_stats(
    admin=Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """
    Get dashboard statistics:
    - User counts (total, active)
    - License counts (total, active, expired, revoked)
    - Active sessions
    - Total devices
    - Total strategies collected
    - Recent audit events
    """
    # Total users
    total_users = (await db.execute(select(func.count(User.id)))).scalar() or 0
    active_users = (await db.execute(
        select(func.count(User.id)).where(User.status == "active")
    )).scalar() or 0

    # Licenses
    total_licenses = (await db.execute(select(func.count(License.id)))).scalar() or 0
    active_licenses = (await db.execute(
        select(func.count(License.id)).where(
            and_(License.status == "active", License.revoked_at.is_(None))
        )
    )).scalar() or 0
    expired_licenses = (await db.execute(
        select(func.count(License.id)).where(
            and_(License.status == "active", License.expires_at < func.now())
        )
    )).scalar() or 0
    revoked_licenses = (await db.execute(
        select(func.count(License.id)).where(License.status == "revoked")
    )).scalar() or 0

    # Active sessions
    active_sessions = (await db.execute(
        select(func.count(Session.id)).where(Session.revoked_at.is_(None))
    )).scalar() or 0

    # Total devices
    total_devices = (await db.execute(
        select(func.count(Device.id)).where(Device.revoked_at.is_(None))
    )).scalar() or 0

    # Total strategies
    total_strategies = (await db.execute(
        select(func.count(StrategyRegistry.id))
    )).scalar() or 0

    # Recent audit events
    recent_events = await get_recent_events(db, limit=10)
    recent_audit = [
        {
            "id": e.id,
            "ts": e.ts.isoformat(),
            "action": e.action,
            "actor_role": e.actor_role,
            "target_type": e.target_type,
        }
        for e in recent_events
    ]

    return DashboardStats(
        total_users=total_users,
        active_users=active_users,
        total_licenses=total_licenses,
        active_licenses=active_licenses,
        expired_licenses=expired_licenses,
        revoked_licenses=revoked_licenses,
        active_sessions=active_sessions,
        total_devices=total_devices,
        total_strategies=total_strategies,
        recent_audit_events=recent_audit,
    )