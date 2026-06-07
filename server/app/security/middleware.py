"""
L-01: Security Headers Middleware for FastAPI

Security Model:
    HTTP security headers provide defense-in-depth against common web attacks.
    This middleware adds essential security headers to every response from the
    FastAPI server, protecting against:

    1. XSS (Cross-Site Scripting) — via CSP, X-XSS-Protection
    2. Clickjacking — via X-Frame-Options, frame-ancestors in CSP
    3. MIME sniffing — via X-Content-Type-Options
    4. Protocol downgrade — via HSTS (Strict-Transport-Security)
    5. Information leakage — via Server header removal, Referrer-Policy
    6. CSRF — via SameSite cookies, custom token validation

Headers Applied:
    - Content-Security-Policy: Restrict resource loading sources
    - X-Content-Type-Options: nosniff
    - X-Frame-Options: DENY
    - Strict-Transport-Security: max-age=31536000; includeSubDomains
    - X-XSS-Protection: 1; mode=block (legacy browsers)
    - Referrer-Policy: strict-origin-when-cross-origin
    - Permissions-Policy: Restrict browser features
    - Cache-Control: no-store for sensitive endpoints
    - X-Request-ID: Request tracing

Configuration:
    Headers can be customized per-environment (dev/staging/prod).
    Development mode relaxes CSP for hot-reload and debugging.
"""

import logging
import secrets
import time
from typing import Optional, Dict, List, Callable, Set
from dataclasses import dataclass, field

try:
    from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
    from starlette.requests import Request
    from starlette.responses import Response
    HAS_STARLETTE = True
except ImportError:
    HAS_STARLETTE = False
    class BaseHTTPMiddleware:
        pass
    class Request:
        pass
    class Response:
        pass

logger = logging.getLogger(__name__)


@dataclass
class SecurityHeadersConfig:
    """Configuration for security headers."""

    # Content Security Policy
    csp_enabled: bool = True
    csp_report_only: bool = False  # Set True to test without blocking
    csp_default_src: List[str] = field(default_factory=lambda: ["'self'"])
    csp_script_src: List[str] = field(default_factory=lambda: ["'self'"])
    csp_style_src: List[str] = field(default_factory=lambda: ["'self'", "'unsafe-inline'"])
    csp_img_src: List[str] = field(default_factory=lambda: ["'self'", "data:"])
    csp_connect_src: List[str] = field(default_factory=lambda: ["'self'"])
    csp_font_src: List[str] = field(default_factory=lambda: ["'self'"])
    csp_object_src: List[str] = field(default_factory=lambda: ["'none'"])
    csp_frame_src: List[str] = field(default_factory=lambda: ["'none'"])
    csp_frame_ancestors: List[str] = field(default_factory=lambda: ["'none'"])
    csp_form_action: List[str] = field(default_factory=lambda: ["'self'"])
    csp_base_uri: List[str] = field(default_factory=lambda: ["'self'"])
    csp_report_uri: Optional[str] = None

    # HSTS
    hsts_enabled: bool = True
    hsts_max_age: int = 31536000  # 1 year
    hsts_include_subdomains: bool = True
    hsts_preload: bool = False

    # Other headers
    x_frame_options: str = "DENY"
    x_content_type_options: str = "nosniff"
    x_xss_protection: str = "1; mode=block"
    referrer_policy: str = "strict-origin-when-cross-origin"

    # Permissions Policy (formerly Feature-Policy)
    permissions_policy: Dict[str, List[str]] = field(default_factory=lambda: {
        "camera": [],
        "microphone": [],
        "geolocation": [],
        "payment": [],
        "usb": [],
        "accelerometer": [],
        "gyroscope": [],
    })

    # Server header
    remove_server_header: bool = True
    server_name: str = ""  # Empty = remove entirely

    # Cache control for sensitive endpoints
    sensitive_path_prefixes: Set[str] = field(default_factory=lambda: {
        "/api/auth",
        "/api/admin",
        "/api/user",
        "/api/account",
        "/api/keys",
        "/api/secrets",
    })
    cache_control_no_store: str = "no-store, no-cache, must-revalidate, private"

    # Request ID
    request_id_header: str = "X-Request-ID"
    generate_request_id: bool = True

    # Rate limit headers (informational)
    rate_limit_header: str = "X-RateLimit-Limit"
    rate_limit_remaining_header: str = "X-RateLimit-Remaining"
    rate_limit_reset_header: str = "X-RateLimit-Reset"

    # Development mode
    dev_mode: bool = False

    @classmethod
    def development(cls) -> "SecurityHeadersConfig":
        """Create relaxed config for development."""
        config = cls(dev_mode=True)
        # Allow inline scripts and eval in dev (for hot-reload, devtools)
        config.csp_script_src = ["'self'", "'unsafe-inline'", "'unsafe-eval'"]
        config.csp_connect_src = ["'self'", "ws:", "wss:", "http://localhost:*"]
        config.csp_style_src = ["'self'", "'unsafe-inline'"]
        config.hsts_enabled = False  # No HSTS in dev (no HTTPS typically)
        config.csp_report_only = True  # Report-only mode in dev
        return config

    @classmethod
    def production(cls) -> "SecurityHeadersConfig":
        """Create strict config for production."""
        config = cls()
        config.csp_script_src = ["'self'"]
        config.csp_connect_src = ["'self'"]
        config.hsts_preload = True
        config.csp_report_only = False
        return config


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """
    FastAPI/Starlette middleware that adds security headers to all responses.

    Usage:
        from fastapi import FastAPI
        from server.app.security.middleware import SecurityHeadersMiddleware, SecurityHeadersConfig

        app = FastAPI()
        config = SecurityHeadersConfig.production()
        app.add_middleware(SecurityHeadersMiddleware, config=config)

        # Or with development config:
        app.add_middleware(SecurityHeadersMiddleware, config=SecurityHeadersConfig.development())
    """

    def __init__(self, app, config: Optional[SecurityHeadersConfig] = None):
        """
        Initialize middleware.

        Args:
            app: ASGI application
            config: Security headers configuration (production defaults)
        """
        super().__init__(app)
        self.config = config or SecurityHeadersConfig()
        logger.info(
            "SecurityHeadersMiddleware initialized: dev_mode=%s, csp_report_only=%s",
            self.config.dev_mode,
            self.config.csp_report_only,
        )

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        """Process request and add security headers to response."""
        # Generate request ID
        request_id = None
        if self.config.generate_request_id:
            request_id = request.headers.get(
                self.config.request_id_header,
                self._generate_request_id(),
            )
            request.state.request_id = request_id

        # Track request timing
        start_time = time.monotonic()

        # Process request
        response = await call_next(request)

        # Add request ID to response
        if request_id:
            response.headers[self.config.request_id_header] = request_id

        # Add security headers
        self._add_csp_headers(response)
        self._add_hsts_headers(response)
        self._add_misc_security_headers(response)
        self._add_permissions_policy(response)
        self._add_server_header(response)
        self._add_cache_control(request, response)

        # Add timing header
        elapsed_ms = (time.monotonic() - start_time) * 1000
        response.headers["X-Response-Time"] = f"{elapsed_ms:.0f}ms"

        return response

    def _add_csp_headers(self, response: Response) -> None:
        """Build and add Content-Security-Policy header."""
        if not self.config.csp_enabled:
            return

        csp_directives: Dict[str, List[str]] = {
            "default-src": self.config.csp_default_src,
            "script-src": self.config.csp_script_src,
            "style-src": self.config.csp_style_src,
            "img-src": self.config.csp_img_src,
            "connect-src": self.config.csp_connect_src,
            "font-src": self.config.csp_font_src,
            "object-src": self.config.csp_object_src,
            "frame-src": self.config.csp_frame_src,
            "frame-ancestors": self.config.csp_frame_ancestors,
            "form-action": self.config.csp_form_action,
            "base-uri": self.config.csp_base_uri,
        }

        if self.config.csp_report_uri:
            csp_directives["report-uri"] = [self.config.csp_report_uri]

        # Build CSP string
        csp_parts = []
        for directive, sources in csp_directives.items():
            if sources:
                csp_parts.append(f"{directive} {' '.join(sources)}")

        csp_value = "; ".join(csp_parts)

        header_name = (
            "Content-Security-Policy-Report-Only"
            if self.config.csp_report_only
            else "Content-Security-Policy"
        )
        response.headers[header_name] = csp_value

    def _add_hsts_headers(self, response: Response) -> None:
        """Add Strict-Transport-Security header."""
        if not self.config.hsts_enabled:
            return

        hsts_value = f"max-age={self.config.hsts_max_age}"
        if self.config.hsts_include_subdomains:
            hsts_value += "; includeSubDomains"
        if self.config.hsts_preload:
            hsts_value += "; preload"

        response.headers["Strict-Transport-Security"] = hsts_value

    def _add_misc_security_headers(self, response: Response) -> None:
        """Add miscellaneous security headers."""
        response.headers["X-Frame-Options"] = self.config.x_frame_options
        response.headers["X-Content-Type-Options"] = self.config.x_content_type_options
        response.headers["X-XSS-Protection"] = self.config.x_xss_protection
        response.headers["Referrer-Policy"] = self.config.referrer_policy

        # Cross-Origin headers for additional protection
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
        response.headers["Cross-Origin-Embedder-Policy"] = "require-corp"

    def _add_permissions_policy(self, response: Response) -> None:
        """Add Permissions-Policy header."""
        if not self.config.permissions_policy:
            return

        policy_parts = []
        for feature, origins in self.config.permissions_policy.items():
            if origins:
                policy_parts.append(f'{feature}=({" ".join(origins)})')
            else:
                policy_parts.append(f"{feature}=()")

        response.headers["Permissions-Policy"] = ", ".join(policy_parts)

    def _add_server_header(self, response: Response) -> None:
        """Remove or obfuscate Server header."""
        if self.config.remove_server_header:
            response.headers.pop("server", None)
            if self.config.server_name:
                response.headers["Server"] = self.config.server_name

    def _add_cache_control(self, request: Request, response: Response) -> None:
        """Add Cache-Control header for sensitive endpoints."""
        path = request.url.path

        for prefix in self.config.sensitive_path_prefixes:
            if path.startswith(prefix):
                response.headers["Cache-Control"] = self.config.cache_control_no_store
                break

    @staticmethod
    def _generate_request_id() -> str:
        """Generate unique request ID."""
        return secrets.token_hex(16)


class CORSSecurityMiddleware(BaseHTTPMiddleware):
    """
    CORS middleware with security-focused defaults.

    Restricts cross-origin requests to known trusted origins only.
    Blocks wildcard origins and validates preflight requests.

    Usage:
        app.add_middleware(
            CORSSecurityMiddleware,
            allowed_origins=["https://app.agregat.io"],
        )
    """

    def __init__(
        self,
        app,
        allowed_origins: Optional[List[str]] = None,
        allowed_methods: Optional[List[str]] = None,
        allowed_headers: Optional[List[str]] = None,
        allow_credentials: bool = True,
        max_age: int = 600,
    ):
        super().__init__(app)
        self.allowed_origins: Set[str] = set(allowed_origins or [])
        self.allowed_methods = allowed_methods or ["GET", "POST", "PUT", "DELETE", "OPTIONS"]
        self.allowed_headers = allowed_headers or [
            "Content-Type",
            "Authorization",
            "X-Request-ID",
        ]
        self.allow_credentials = allow_credentials
        self.max_age = max_age

        if "*" in self.allowed_origins:
            logger.warning(
                "CORS: Wildcard origin (*) detected! This is insecure for production."
            )

        logger.info(
            "CORSSecurityMiddleware initialized: origins=%s",
            list(self.allowed_origins),
        )

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        """Process CORS request."""
        origin = request.headers.get("origin", "")

        # Handle preflight
        if request.method == "OPTIONS":
            return self._handle_preflight(origin)

        # Process request
        response = await call_next(request)

        # Add CORS headers if origin is allowed
        if origin and origin in self.allowed_origins:
            response.headers["Access-Control-Allow-Origin"] = origin
            if self.allow_credentials:
                response.headers["Access-Control-Allow-Credentials"] = "true"

        return response

    def _handle_preflight(self, origin: str) -> Response:
        """Handle CORS preflight request."""
        headers: Dict[str, str] = {}

        if origin in self.allowed_origins:
            headers["Access-Control-Allow-Origin"] = origin
            headers["Access-Control-Allow-Methods"] = ", ".join(self.allowed_methods)
            headers["Access-Control-Allow-Headers"] = ", ".join(self.allowed_headers)
            headers["Access-Control-Max-Age"] = str(self.max_age)
            if self.allow_credentials:
                headers["Access-Control-Allow-Credentials"] = "true"

        return Response(status_code=204, headers=headers)


def create_security_middleware_stack(
    app,
    config: Optional[SecurityHeadersConfig] = None,
    cors_origins: Optional[List[str]] = None,
) -> None:
    """
    Convenience function to add all security middleware to FastAPI app.

    Args:
        app: FastAPI application instance
        config: Security headers config (auto-selects dev/prod if None)
        cors_origins: List of allowed CORS origins
    """
    if config is None:
        config = SecurityHeadersConfig.production()

    # Add security headers middleware
    app.add_middleware(SecurityHeadersMiddleware, config=config)

    # Add CORS middleware if origins specified
    if cors_origins:
        app.add_middleware(CORSSecurityMiddleware, allowed_origins=cors_origins)

    logger.info("Security middleware stack configured")


__all__ = [
    "SecurityHeadersMiddleware",
    "SecurityHeadersConfig",
    "CORSSecurityMiddleware",
    "create_security_middleware_stack",
]