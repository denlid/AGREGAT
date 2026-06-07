"""
AGREGAT Server — Admin Users API

User management: list, create, update, suspend.
"""

import uuid
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select, func, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.user import User
from app.schemas.user import UserResponse, UserListResponse, UserCreateRequest, UserUpdateRequest
from app.api.deps import require_admin, require_owner, require_csrf_token, get_ip_hash
from app.services.audit import log_event

router = APIRouter(prefix="/users", tags=["admin-users"])


@router.get("", response_model=UserListResponse)
async def list_users(
    page: int = 1,
    per_page: int = 20,
    search: str = "",
    admin=Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    query = select(User).order_by(User.created_at.desc())
    if search:
        query = query.where(User.email.ilike(f"%{search}%"))

    total = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar() or 0
    offset = (page - 1) * per_page
    result = await db.execute(query.offset(offset).limit(per_page))
    users = result.scalars().all()

    return UserListResponse(
        users=[UserResponse(
            id=str(u.id), email=u.email, role=u.role,
            status=u.status, created_at=u.created_at, last_login_at=u.last_login_at,
        ) for u in users],
        total=total, page=page, per_page=per_page,
    )


@router.post("", response_model=UserResponse)
async def create_user(
    req: UserCreateRequest,
    request: Request,
    admin=Depends(require_owner),
    db: AsyncSession = Depends(get_db),
    _csrf=Depends(require_csrf_token),  # C-05: CSRF protection
):
    existing = (await db.execute(select(User).where(User.email == req.email))).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=400, detail="user_exists")

    user = User(email=req.email, role=req.role, status="active")
    db.add(user)
    await db.flush()
    await log_event(db, action="user.created", actor_user_id=admin.id, actor_role=admin.role,
                    target_type="user", target_id=str(user.id), ip_hash=get_ip_hash(request))
    await db.refresh(user)

    return UserResponse(id=str(user.id), email=user.email, role=user.role,
                        status=user.status, created_at=user.created_at, last_login_at=user.last_login_at)


@router.patch("/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: uuid.UUID,
    req: UserUpdateRequest,
    request: Request,
    admin=Depends(require_owner),
    db: AsyncSession = Depends(get_db),
    _csrf=Depends(require_csrf_token),  # C-05: CSRF protection
):
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="user_not_found")

    if user.is_owner and admin.id != user.id:
        raise HTTPException(status_code=403, detail="cannot_modify_owner")

    changes = {}
    if req.role is not None:
        changes["role"] = {"old": user.role, "new": req.role}
        user.role = req.role
    if req.status is not None:
        changes["status"] = {"old": user.status, "new": req.status}
        user.status = req.status

    await log_event(db, action="user.updated", actor_user_id=admin.id, actor_role=admin.role,
                    target_type="user", target_id=str(user_id), ip_hash=get_ip_hash(request), payload=changes)
    await db.flush()
    await db.refresh(user)

    return UserResponse(id=str(user.id), email=user.email, role=user.role,
                        status=user.status, created_at=user.created_at, last_login_at=user.last_login_at)