"""
AGREGAT Server — Admin API Router

Aggregates all admin endpoints under /admin prefix.
"""

from fastapi import APIRouter

from app.api.admin.auth import router as auth_router
from app.api.admin.dashboard import router as dashboard_router
from app.api.admin.licenses import router as licenses_router
from app.api.admin.users import router as users_router
from app.api.admin.devices import router as devices_router
from app.api.admin.sessions import router as sessions_router
from app.api.admin.strategies import router as strategies_router
from app.api.admin.audit import router as audit_router

router = APIRouter(prefix="/admin")

router.include_router(auth_router)
router.include_router(dashboard_router)
router.include_router(licenses_router)
router.include_router(users_router)
router.include_router(devices_router)
router.include_router(sessions_router)
router.include_router(strategies_router)
router.include_router(audit_router)