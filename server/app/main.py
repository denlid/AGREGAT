"""
AGREGAT Server — FastAPI Application Entry Point

Ties together all routers, middleware, database lifespan, templates,
and security configuration into a single runnable application.
"""

import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import settings
from app.database import init_db, close_db, get_db, async_session_factory
from app.api.v1.router import router as v1_router
from app.api.admin.router import router as admin_router
from app.api.deps import generate_csrf_token
from app.security.rate_limit import rate_limiter

# ─── Logging ───────────────────────────────────────────────────────────

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("agregat.server")

# ─── Templates ─────────────────────────────────────────────────────────

templates_path = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(templates_path))

# ─── Lifespan ──────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application startup and shutdown events.
    Initializes database tables on startup, cleans up on shutdown.
    """
    logger.info("AGREGAT Server starting up...")
    logger.info(f"Debug mode: {settings.debug}")
    logger.info(f"Database: {settings.database_url.split('@')[-1] if '@' in settings.database_url else 'configured'}")

    # MH-01: Only auto-create tables in debug mode.
    # Production MUST use Alembic migrations (alembic upgrade head).
    if settings.debug:
        try:
            await init_db()
            logger.info("[DEV] Database tables auto-created via create_all()")
        except Exception as e:
            logger.warning(f"Database init warning: {e}")
    else:
        logger.info(
            "[PROD] Skipping auto-create_all. "
            "Run 'alembic upgrade head' to apply migrations."
        )

    logger.info("AGREGAT Server ready")
    yield

    # Shutdown
    logger.info("AGREGAT Server shutting down...")
    await close_db()
    logger.info("Database connection closed")

# ─── FastAPI App ───────────────────────────────────────────────────────

app = FastAPI(
    title="AGREGAT License Server",
    description="Hardware-bound licensing system with server-authoritative validation",
    version="2.0.0",
    lifespan=lifespan,
    docs_url="/docs" if settings.debug else None,
    redoc_url="/redoc" if settings.debug else None,
)

# ─── Middleware ────────────────────────────────────────────────────────

def _parse_comma_list(value: str) -> list[str]:
    """Parse comma-separated string into list, stripping whitespace."""
    if not value or not value.strip():
        return []
    return [item.strip() for item in value.split(",") if item.strip()]

# C-03: Trusted Host — prevent host header injection / DNS rebinding
# Production: MUST set ALLOWED_HOSTS to actual domains (no wildcards)
_allowed_hosts_list = _parse_comma_list(settings.allowed_hosts)
if not settings.debug and ("*" in _allowed_hosts_list or not _allowed_hosts_list):
    logger.warning(
        "SECURITY: allowed_hosts is empty or contains '*' in production. "
        "Set ALLOWED_HOSTS in .env to your actual domain(s). "
        "TrustedHostMiddleware is DISABLED — this is a security risk."
    )
if _allowed_hosts_list and "*" not in _allowed_hosts_list:
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=_allowed_hosts_list,
    )
    logger.info(f"TrustedHostMiddleware enabled: {_allowed_hosts_list}")

# C-03: CORS — restrict to known origins (no wildcards in production)
_allowed_origins_list = _parse_comma_list(settings.allowed_origins)
if not settings.debug and ("*" in _allowed_origins_list or not _allowed_origins_list):
    logger.warning(
        "SECURITY: allowed_origins is empty or contains '*' in production. "
        "Set ALLOWED_ORIGINS in .env to your actual domain(s). "
        "CORS is set to empty list (no cross-origin requests allowed)."
    )
    _allowed_origins_list = []  # Fail-safe: no origins in production if misconfigured

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Admin-Token"],
    expose_headers=["X-RateLimit-Limit", "X-RateLimit-Remaining", "X-RateLimit-Reset"],
    max_age=600,  # Cache preflight for 10 minutes
)
if _allowed_origins_list:
    logger.info(f"CORS enabled for: {_allowed_origins_list}")

# Security headers middleware (P0-5, P3-1: CSP + HSTS + full hardening)
@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    """
    Add comprehensive security headers to all responses.
    
    P0-5: Content-Security-Policy to prevent XSS
    P3-1: HSTS with preload for transport security
    Additional: Permissions-Policy, COOP, COEP for isolation
    """
    response = await call_next(request)
    
    # ─── Content Security Policy (P0-5) ─────────────────────────────
    # Restrict resource loading to prevent XSS and data exfiltration
    csp_directives = [
        "default-src 'self'",
        "script-src 'self' 'unsafe-inline'",  # TODO: Replace unsafe-inline with nonce
        "style-src 'self' 'unsafe-inline'",
        "img-src 'self' data:",
        "font-src 'self'",
        "connect-src 'self'",
        "frame-ancestors 'none'",  # Prevent clickjacking
        "base-uri 'self'",
        "form-action 'self'",
        "object-src 'none'",  # Block Flash/plugins
        "upgrade-insecure-requests",
    ]
    response.headers["Content-Security-Policy"] = "; ".join(csp_directives)
    
    # ─── Anti-MIME-Sniffing ─────────────────────────────────────────
    response.headers["X-Content-Type-Options"] = "nosniff"
    
    # ─── Clickjacking Protection ────────────────────────────────────
    response.headers["X-Frame-Options"] = "DENY"
    
    # ─── XSS Filter (legacy browsers) ───────────────────────────────
    response.headers["X-XSS-Protection"] = "1; mode=block"
    
    # ─── HSTS (P3-1) ────────────────────────────────────────────────
    # Only when HTTPS is guaranteed (production behind TLS proxy)
    is_https = (
        request.url.scheme == "https"
        or request.headers.get("x-forwarded-proto") == "https"
    )
    if is_https or not settings.debug:
        response.headers["Strict-Transport-Security"] = (
            "max-age=31536000; includeSubDomains; preload"
        )
    
    # ─── Referrer Policy ────────────────────────────────────────────
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    
    # ─── Cache Control (prevent sensitive data caching) ─────────────
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, private"
    response.headers["Pragma"] = "no-cache"
    
    # ─── Permissions Policy (P0-5) ──────────────────────────────────
    # Disable unnecessary browser features
    response.headers["Permissions-Policy"] = (
        "camera=(), "
        "microphone=(), "
        "geolocation=(), "
        "payment=(), "
        "usb=(), "
        "magnetometer=(), "
        "gyroscope=(), "
        "accelerometer=()"
    )
    
    # ─── Cross-Origin Isolation ─────────────────────────────────────
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    response.headers["Cross-Origin-Embedder-Policy"] = "require-corp"
    response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
    
    return response

# Request logging middleware
@app.middleware("http")
async def request_logging_middleware(request: Request, call_next):
    """Log all requests for audit purposes."""
    method = request.method
    path = request.url.path
    client_ip = request.client.host if request.client else "unknown"

    logger.debug(f"{method} {path} from {client_ip}")

    response = await call_next(request)

    if response.status_code >= 400:
        logger.warning(f"{method} {path} -> {response.status_code}")

    return response

# ─── Exception Handlers ────────────────────────────────────────────────

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Catch-all exception handler for unhandled errors."""
    logger.error(f"Unhandled exception: {exc}", exc_info=True)

    # Log to audit trail
    try:
        from app.api.deps import get_ip_hash
        from app.services.audit import log_event

        ip_hash = get_ip_hash(request)
        async with async_session_factory() as db:
            await log_event(
                db=db,
                action="server.error",
                ip_hash=ip_hash,
                payload={
                    "path": str(request.url.path),
                    "method": request.method,
                    "error_type": type(exc).__name__,
                },
            )
    except Exception as log_err:
        logger.error(f"Failed to log error event: {log_err}")

    return JSONResponse(
        status_code=500,
        content={"detail": "internal_server_error"},
    )

# ─── Routers ───────────────────────────────────────────────────────────

# Client-facing API (v1)
app.include_router(v1_router, prefix="/api")

# Admin API + panel
app.include_router(admin_router, prefix="/api")

# ─── Admin Panel HTML Routes ──────────────────────────────────────────

@app.get("/admin", tags=["admin-panel"])
async def admin_login_page(request: Request, response: Response):
    """
    Serve admin login page with CSRF token.
    
    C-05: Generates a CSRF token and sets it as a cookie.
    The token is also passed to the template for inclusion in forms.
    """
    csrf_token = generate_csrf_token()
    response = templates.TemplateResponse("login.html", {
        "request": request,
        "csrf_token": csrf_token,
    })
    response.set_cookie(
        key="admin_csrf_token",
        value=csrf_token,
        httponly=False,  # Must be readable by JS for double-submit pattern
        secure=True,
        samesite="strict",
        max_age=3600,  # 1 hour
    )
    return response


@app.get("/admin/dashboard", tags=["admin-panel"])
async def admin_dashboard_page(request: Request, response: Response):
    """
    Serve admin dashboard (auth checked client-side via cookie).
    
    C-05: Refreshes CSRF token for authenticated sessions.
    """
    csrf_token = generate_csrf_token()
    response = templates.TemplateResponse("dashboard.html", {
        "request": request,
        "csrf_token": csrf_token,
    })
    response.set_cookie(
        key="admin_csrf_token",
        value=csrf_token,
        httponly=False,
        secure=True,
        samesite="strict",
        max_age=86400,  # 24 hours (matches session)
    )
    return response

# ─── Health Check ──────────────────────────────────────────────────────

@app.get("/health", tags=["system"])
async def health_check():
    """
    Health check endpoint for load balancers and monitoring.
    Returns server status and basic info.
    """
    return {
        "status": "healthy",
        "version": "2.0.0",
        "server_time": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
    }


@app.get("/", tags=["system"])
async def root():
    """Root endpoint — server info."""
    return {
        "service": "AGREGAT License Server",
        "version": "2.0.0",
        "docs": "/docs" if settings.debug else "disabled",
        "health": "/health",
    }

# ─── Run ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.server_host,
        port=settings.server_port,
        reload=settings.debug,
        log_level=settings.log_level.lower(),
    )