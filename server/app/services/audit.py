"""
AGREGAT Server — Audit Service

Immutable audit log with hash chain integrity verification.
Every event is cryptographically linked to the previous one.
"""

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import AuditEvent


def _compute_event_hash(
    ts: str,
    action: str,
    payload_hash: str,
    prev_hash: Optional[str],
) -> str:
    """Compute SHA-256 hash linking this event to the chain."""
    data = f"{ts}|{action}|{payload_hash}|{prev_hash or 'GENESIS'}"
    return hashlib.sha256(data.encode()).hexdigest()


def _compute_payload_hash(payload: dict) -> str:
    """Compute deterministic hash of event payload."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


async def log_event(
    db: AsyncSession,
    action: str,
    actor_user_id: Optional[uuid.UUID] = None,
    actor_role: Optional[str] = None,
    target_type: Optional[str] = None,
    target_id: Optional[str] = None,
    ip_hash: Optional[str] = None,
    user_agent_hash: Optional[str] = None,
    payload: Optional[dict] = None,
) -> AuditEvent:
    """
    Log an audit event with hash chain integrity.
    """
    payload = payload or {}
    payload_hash = _compute_payload_hash(payload)

    # Get previous event hash for chain linking
    stmt = (
        select(AuditEvent)
        .order_by(AuditEvent.id.desc())
        .limit(1)
    )
    result = await db.execute(stmt)
    prev_event = result.scalar_one_or_none()
    prev_hash = prev_event.event_hash if prev_event else None

    ts = datetime.now(timezone.utc).isoformat()
    event_hash = _compute_event_hash(ts, action, payload_hash, prev_hash)

    event = AuditEvent(
        ts=datetime.now(timezone.utc),
        actor_user_id=actor_user_id,
        actor_role=actor_role,
        action=action,
        target_type=target_type,
        target_id=target_id,
        ip_hash=ip_hash,
        user_agent_hash=user_agent_hash,
        payload_hash=payload_hash,
        payload_json=payload,
        prev_event_hash=prev_hash,
        event_hash=event_hash,
    )
    db.add(event)
    await db.flush()
    await db.refresh(event)
    return event


async def verify_chain_integrity(db: AsyncSession) -> dict:
    """
    Verify the entire audit log hash chain.
    Returns dict with is_valid, total_events, and any broken links.
    """
    stmt = select(AuditEvent).order_by(AuditEvent.id.asc())
    result = await db.execute(stmt)
    events = result.scalars().all()

    broken_links = []
    prev_hash = None

    for event in events:
        expected_hash = _compute_event_hash(
            event.ts.isoformat(),
            event.action,
            event.payload_hash,
            prev_hash,
        )
        if expected_hash != event.event_hash:
            broken_links.append({
                "event_id": event.id,
                "expected": expected_hash,
                "actual": event.event_hash,
            })
        prev_hash = event.event_hash

    return {
        "is_valid": len(broken_links) == 0,
        "total_events": len(events),
        "broken_links": broken_links,
    }


async def get_recent_events(
    db: AsyncSession,
    limit: int = 20,
    action_filter: Optional[str] = None,
) -> list[AuditEvent]:
    """Get recent audit events, optionally filtered by action."""
    stmt = select(AuditEvent).order_by(AuditEvent.id.desc()).limit(limit)
    if action_filter:
        stmt = stmt.where(AuditEvent.action.ilike(f"%{action_filter}%"))
    result = await db.execute(stmt)
    return list(result.scalars().all())