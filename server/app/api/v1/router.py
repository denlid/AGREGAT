"""
AGREGAT Server — V1 API Router

Aggregates all v1 client-facing endpoints.
"""

from fastapi import APIRouter

from app.api.v1.auth import router as auth_router
from app.api.v1.heartbeat import router as heartbeat_router
from app.api.v1.strategies import router as strategies_router
from app.api.v1.audit import router as audit_router

router = APIRouter(prefix="/v1")

router.include_router(auth_router)
router.include_router(heartbeat_router)
router.include_router(strategies_router)
router.include_router(audit_router)