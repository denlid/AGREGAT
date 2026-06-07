"""
AGREGAT Server — FastAPI Application Entry Point

Ties together all routers, middleware, database lifespan, templates,
and security configuration into a single runnable application.
"""

import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path

import secrets as _secrets
from fastapi import FastAPI, Request, Response, HTTPException
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

# P0-2 FIX: allow_credentials ONLY when origins are explicitly configured
# Fail-safe: empty origins = no credentials (prevents credential leakage)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins_list,
    allow_credentials=bool(_allowed_origins_list),  # P0-2: Only if origins set
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Admin-Token", "X-CSRF-Token", "X-Agreqat-Signature", "X-Agreqat-Timestamp", "X-Agreqat-Nonce"],
    expose_headers=["X-RateLimit-Limit", "X-RateLimit-Remaining", "X-RateLimit-Reset"],
    max_age=600,  # Cache preflight for 10 minutes
)
if _allowed_origins_list:
    logger.info(f"CORS enabled for: {_allowed_origins_list}")

# ─── P2-6: Request Body Size Limit ────────────────────────────────────
# Prevent denial-of-service via massive request bodies.
# Default: 1MB for API, 10MB for file uploads (if any).
MAX_BODY_SIZE_API = 1 * 1024 * 1024       # 1 MB
MAX_BODY_SIZE_ADMIN = 5 * 1024 * 1024     # 5 MB (admin panel may have larger forms)


@app.middleware("http")
async def body_size_limit_middleware(request: Request, call_next):
    """
    P2-6: Reject requests with bodies exceeding size limits.
    Prevents DoS via massive payloads (e.g., 1GB JSON body).
    
    Limits:
    - API endpoints: 1 MB
    - Admin endpoints: 5 MB
    - Health/root: no limit (no body expected)
    """
    # Skip for GET/HEAD/OPTIONS (no body)
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return await call_next(request)
    
    # Skip for health/root (no body expected)
    if request.url.path in ("/health", "/"):
        return await call_next(request)
    
    # Determine limit based on path
    if request.url.path.startswith("/api/admin"):
        limit = MAX_BODY_SIZE_ADMIN
    else:
        limit = MAX_BODY_SIZE_API
    
    # Check Content-Length header first (fast reject)
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > limit:
                raise HTTPException(
                    status_code=413,
                    detail=f"request_body_too_large: max {limit // 1024}KB",
                )
        except ValueError:
            pass  # Invalid content-length — let body read handle it
    
    return await call_next(request)


# ─── P1-7: CSP Nonce Generation ───────────────────────────────────────
# Generate a unique nonce per request for inline script execution.
# This replaces 'unsafe-inline' with 'nonce-XXX' in CSP.

@app.middleware("http")
async def csp_nonce_middleware(request: Request, call_next):
    """
    P1-7: Generate a per-request CSP nonce for inline scripts.
    The nonce is attached to request.state and used in CSP header.
    
    Templates must add nonce to inline scripts:
      <script nonce="{{ request.state.csp_nonce }}">...</script>
    """
    # Generate cryptographically secure nonce (16 bytes, base64)
    request.state.csp_nonce = _secrets.token_urlsafe(16)
    response = await call_next(request)
    return response


# Security headers middleware (P0-5, P1-7, P3-1: CSP nonce + HSTS + full hardening)
@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    """
    Add comprehensive security headers to all responses.
    
    P0-5: Content-Security-Policy to prevent XSS
    P1-7: CSP nonce for inline scripts (replaces unsafe-inline)
    P3-1: HSTS with preload for transport security
    Additional: Permissions-Policy, COOP, COEP for isolation
    """
    response = await call_next(request)
    
    # ─── Content Security Policy (P0-5 + P1-7) ──────────────────────
    # P1-7: Use per-request nonce instead of 'unsafe-inline'
    csp_nonce = getattr(request.state, "csp_nonce", None)
    nonce_directive = f"'nonce-{csp_nonce}'" if csp_nonce else "'unsafe-inline'"
    
    csp_directives = [
        "default-src 'self'",
        f"script-src 'self' {nonce_directive}",  # P1-7: nonce-based
        f"style-src 'self' {nonce_directive}",    # P1-7: nonce-based
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

# P0-4 FIX: Minimal health response — no version/time leakage
@app.get("/health", tags=["system"])
async def health_check():
    """
    Health check endpoint for load balancers and monitoring.
    P0-4: Returns minimal info — no version or server time (prevents info leakage).
    """
    return {"status": "ok"}


# P0-5 FIX: Hide service info in production
@app.get("/", tags=["system"])
async def root():
    """
    Root endpoint.
    P0-5: In production returns 404 (no info leakage). In debug — basic info.
    """
    if settings.debug:
        return {
            "service": "AGREGAT License Server",
            "version": "2.0.0",
            "docs": "/docs",
            "health": "/health",
        }
    # Production: return 404 to avoid information disclosure
    raise HTTPException(status_code=404, detail="not_found")

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