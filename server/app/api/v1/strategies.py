"""
AGREGAT Server — V1 Strategies API

Strategy hash registration for IP protection.

SECURITY (C-05): By default, ONLY the strategy hash is stored.
Strategy source code is NEVER collected without explicit user consent.
The `strategy_code` field is only populated when the user explicitly
opts in (e.g., "Share strategy with developer" checkbox in the UI).

This protects user IP while still enabling:
- Duplicate strategy detection (via hash)
- Usage analytics (which strategies are popular)
- Plagiarism detection (same hash across users)
"""

import hashlib
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, and_, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.strategy import StrategyRegistry
from app.schemas.auth import (
    StrategyRegisterRequest,
    StrategyRegisterResponse,
)
from app.api.deps import get_current_session, get_current_user

router = APIRouter(prefix="/strategies", tags=["strategies"])


@router.post("/register", response_model=StrategyRegisterResponse)
async def register_strategy(
    req: StrategyRegisterRequest,
    session=Depends(get_current_session),
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Register a strategy hash (and optionally code) with the server.
    Called silently by desktop client when user loads/runs a strategy.
    """
    # Check if strategy already exists for this user
    stmt = select(StrategyRegistry).where(
        and_(
            StrategyRegistry.user_id == user.id,
            StrategyRegistry.strategy_hash == req.strategy_hash,
        )
    )
    result = await db.execute(stmt)
    existing = result.scalar_one_or_none()

    if existing:
        # Update usage count and last seen
        existing.usage_count += 1
        from datetime import datetime, timezone
        existing.last_seen_at = datetime.now(timezone.utc)
        if req.strategy_code and not existing.strategy_code:
            existing.strategy_code = req.strategy_code
        await db.flush()
        return StrategyRegisterResponse(
            status="registered",
            strategy_hash=req.strategy_hash,
            first_seen=False,
        )

    # New strategy
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    strategy = StrategyRegistry(
        user_id=user.id,
        strategy_hash=req.strategy_hash,
        strategy_code=req.strategy_code,
        file_name=req.file_name,
        first_seen_at=now,
        last_seen_at=now,
        usage_count=1,
        metadata_json=req.metadata or {},
    )
    db.add(strategy)
    await db.flush()

    return StrategyRegisterResponse(
        status="registered",
        strategy_hash=req.strategy_hash,
        first_seen=True,
    )