"""
AGREGAT Server — V1 Heartbeat API

Periodic client heartbeat for license validation and server commands.
Critical for real-time license enforcement.

E2E-10: Strategy attestation — client sends integrity proof with each heartbeat.
The server validates the client's anti-tamper status and can revoke sessions
if tampering is detected.
"""

import hashlib
import json
import uuid
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas.auth import (
    HeartbeatRequest,
    HeartbeatResponse,
    EntitlementItem,
)
from app.api.deps import get_current_session, get_current_user, rate_limit_dependency
from app.services.auth_service import heartbeat as heartbeat_service
from app.services.audit import log_event

router = APIRouter(tags=["heartbeat"])


@router.post(
    "/heartbeat",
    response_model=HeartbeatResponse,
    # M-01: Rate limit heartbeat — client sends every 60-300s, so 30/5min is generous
    dependencies=[Depends(rate_limit_dependency("heartbeat", max_requests=30, window_seconds=300))],
)
async def heartbeat_endpoint(
    req: HeartbeatRequest,
    session=Depends(get_current_session),
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Client heartbeat: validates license is still active.
    Returns entitlements + any server commands (revoke, update, etc.)
    
    Desktop client should call this every 60-300 seconds.
    If command="revoke", client must immediately disable all features.
    
    E2E-10: Integrity attestation — the client sends an integrity_hash
    computed from critical security files. The server tracks this hash
    and can detect tampering if the hash changes unexpectedly.
    """
    # E2E-10: Validate client integrity hash
    integrity_status = _validate_integrity_hash(
        session_id=str(session.id),
        integrity_hash=req.integrity_hash,
        app_version=req.app_version,
    )
    
    # If integrity check fails, log it and optionally revoke
    if integrity_status == "tampered":
        await log_event(
            db,
            action="client.integrity_tampered",
            actor_user_id=user.id,
            actor_role=user.role,
            detail=json.dumps({
                "session_id": str(session.id),
                "integrity_hash": req.integrity_hash,
                "app_version": req.app_version,
            }),
        )
        # Return revoke command — client has been tampered with
        return HeartbeatResponse(
            status="error",
            command="revoke",
            entitlements=[],
            server_time=uuid.uuid4().hex[:12],  # Placeholder
            detail="Client integrity check failed. Possible tampering detected.",
        )
    
    result = await heartbeat_service(
        db=db,
        session_id=session.id,
        app_version=req.app_version,
        integrity_hash=req.integrity_hash,
        current_strategy_hash=req.current_strategy_hash,
    )

    return HeartbeatResponse(
        status=result["status"],
        command=result.get("command"),
        entitlements=[EntitlementItem(**e) for e in result.get("entitlements", [])],
        expires_at=result.get("expires_at"),
        server_time=result["server_time"],
        detail=result.get("detail"),
    )


# ─── E2E-10: Client Integrity Attestation ──────────────────────────────

# Track known-good integrity hashes per app version
# In production, populate this from your build pipeline
_KNOWN_GOOD_HASHES: dict[str, set[str]] = {
    # "7.78": {"abc123...", "def456..."},  # Add hashes from build
}

# Track last seen hash per session for anomaly detection
_SESSION_HASH_HISTORY: dict[str, list[str]] = {}
_MAX_HASH_HISTORY = 10


def _validate_integrity_hash(
    session_id: str,
    integrity_hash: str,
    app_version: str,
) -> str:
    """
    E2E-10: Validate client integrity hash.
    
    Returns:
        "ok" — hash is valid and consistent
        "unknown" — no known-good hash for this version (first run)
        "tampered" — hash changed unexpectedly (possible tampering)
    
    Detection logic:
    1. If we have known-good hashes for this version, check against them
    2. Track hash history per session — sudden changes are suspicious
    3. The integrity_hash is computed from security files on the client
       (license_client.py, license_manager.py, anti_debug.py)
    """
    if not integrity_hash:
        return "unknown"  # Client didn't send hash (old version?)
    
    # Check against known-good hashes (if available)
    known = _KNOWN_GOOD_HASHES.get(app_version)
    if known and integrity_hash not in known:
        return "tampered"
    
    # Track hash history for anomaly detection
    history = _SESSION_HASH_HISTORY.setdefault(session_id, [])
    
    if history and integrity_hash != history[-1]:
        # Hash changed mid-session — suspicious!
        # The integrity hash should be constant for a given binary
        return "tampered"
    
    # Record this hash
    history.append(integrity_hash)
    if len(history) > _MAX_HASH_HISTORY:
        history.pop(0)
    
    return "ok"
