"""
AGREGAT Server — Admin Strategies API

View collected strategies from desktop clients.
"""

import uuid
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.strategy import StrategyRegistry
from app.models.user import User
from app.schemas.admin import StrategyResponse, StrategyListResponse
from app.api.deps import require_admin

router = APIRouter(prefix="/strategies", tags=["admin-strategies"])


@router.get("", response_model=StrategyListResponse)
async def list_strategies(
    page: int = 1,
    per_page: int = 20,
    user_id: str = "",
    admin=Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    query = select(StrategyRegistry).order_by(StrategyRegistry.last_seen_at.desc())
    if user_id:
        query = query.where(StrategyRegistry.user_id == uuid.UUID(user_id))

    total = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar() or 0
    offset = (page - 1) * per_page
    result = await db.execute(query.offset(offset).limit(per_page))
    strategies = result.scalars().all()

    items = []
    for s in strategies:
        email = (await db.execute(select(User.email).where(User.id == s.user_id))).scalar()
        items.append(StrategyResponse(
            id=str(s.id), user_id=str(s.user_id), user_email=email,
            strategy_hash=s.strategy_hash, file_name=s.file_name,
            has_code=s.strategy_code is not None,
            usage_count=s.usage_count,
            first_seen_at=s.first_seen_at, last_seen_at=s.last_seen_at,
        ))

    return StrategyListResponse(strategies=items, total=total, page=page, per_page=per_page)


@router.get("/{strategy_id}", response_model=StrategyResponse)
async def get_strategy(
    strategy_id: uuid.UUID,
    admin=Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    s = (await db.execute(select(StrategyRegistry).where(StrategyRegistry.id == strategy_id))).scalar_one_or_none()
    if not s:
        raise HTTPException(status_code=404, detail="strategy_not_found")

    email = (await db.execute(select(User.email).where(User.id == s.user_id))).scalar()
    return StrategyResponse(
        id=str(s.id), user_id=str(s.user_id), user_email=email,
        strategy_hash=s.strategy_hash, strategy_code=s.strategy_code,
        file_name=s.file_name, has_code=s.strategy_code is not None,
        usage_count=s.usage_count,
        first_seen_at=s.first_seen_at, last_seen_at=s.last_seen_at,
        metadata=s.metadata_json,
    )