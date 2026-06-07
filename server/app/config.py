"""
AGREGAT Server — Configuration

Loads settings from environment variables (.env file).
All secrets stay in .env, never in code.
"""

from pathlib import Path
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=Path(__file__).parent.parent / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # ─── Database ───
    database_url: str = "postgresql+asyncpg://agregat:agregat_dev@localhost:5432/agregat"

    # ─── JWT ───
    jwt_secret: str
    jwt_access_token_expire_minutes: int = 5
    jwt_refresh_token_expire_days: int = 30

    # ─── Server ───
    server_host: str = "0.0.0.0"
    server_port: int = 8000
    debug: bool = False

    # C-03: CORS origins — comma-separated list (no wildcards in production)
    # Example: "https://license.agregat.io,https://admin.agregat.io"
    allowed_origins: str = ""

    # C-03: Allowed Host headers — comma-separated list
    # Prevents DNS rebinding and Host header injection attacks
    # Example: "license.agregat.io,admin.agregat.io,localhost"
    allowed_hosts: str = ""

    # ─── Supabase ───
    supabase_url: Optional[str] = None
    supabase_service_key: Optional[str] = None

    # ─── Encryption ───
    encryption_key: str

    # ─── Admin Owner ───
    owner_email: str = "alephxau@gmail.com"
    owner_password_salt: str = "CHANGE_ME_IN_PRODUCTION"

    # ─── Rate Limits (JSON dict) ───
    # Format: {"endpoint_name": {"max_requests": N, "window_seconds": N}}
    rate_limits_json: str = "{}"

    @property
    def rate_limits(self) -> dict:
        """Parse rate_limits_json into dict."""
        import json
        try:
            return json.loads(self.rate_limits_json)
        except (json.JSONDecodeError, TypeError):
            return {}

    # ─── Rate Limiting ───
    rate_limit_requests_per_minute: int = 60
    rate_limit_auth_per_minute: int = 5

    # ─── Heartbeat ───
    heartbeat_interval_seconds: int = 120
    heartbeat_grace_period_seconds: int = 420  # 7 minutes

    # ─── Logging ───
    log_level: str = "INFO"
    log_format: str = "json"

    # ─── Redis (optional, for rate limiting) ───
    redis_url: Optional[str] = None

    # ─── MH-08: Activation key pepper ───
    # Server-side secret mixed into activation key hashes.
    # Prevents offline brute-force if DB leaks (keys have high entropy,
    # but pepper adds defense-in-depth).
    # Generate with: python -c "import secrets; print(secrets.token_hex(32))"
    activation_key_pepper: str = ""

    # ─── MH-09: Device fingerprint pepper ───
    # Server-side secret for HMAC of device fingerprints.
    # Prevents DB leaks from exposing raw hardware identifiers.
    # Can reuse activation_key_pepper or use a separate value.
    fingerprint_pepper: str = ""


# ─── H-10: Production validation ───────────────────────────────────────

def _validate_production_settings() -> None:
    """
    H-10: Fail fast in production if critical security settings are missing.
    
    This prevents running production with:
    - Empty ALLOWED_HOSTS (DNS rebinding vulnerability)
    - Empty ALLOWED_ORIGINS (CORS bypass)
    - Default password salt
    """
    import os
    import sys
    
    # Only validate in production (not debug mode)
    _temp_debug = os.environ.get("DEBUG", "false").lower() in ("true", "1", "yes")
    if _temp_debug:
        return
    
    errors = []
    
    # H-10: ALLOWED_HOSTS must be set in production
    _temp_hosts = os.environ.get("ALLOWED_HOSTS", "").strip()
    if not _temp_hosts:
        errors.append(
            "ALLOWED_HOSTS is empty — this is a DNS rebinding vulnerability. "
            "Set ALLOWED_HOSTS=your-domain.com in .env"
        )
    
    # C-03: ALLOWED_ORIGINS must be set in production
    _temp_origins = os.environ.get("ALLOWED_ORIGINS", "").strip()
    if not _temp_origins:
        errors.append(
            "ALLOWED_ORIGINS is empty — CORS may be misconfigured. "
            "Set ALLOWED_ORIGINS=https://your-domain.com in .env"
        )
    
    # Default salt check
    _temp_salt = os.environ.get("OWNER_PASSWORD_SALT", "").strip()
    if _temp_salt == "CHANGE_ME_IN_PRODUCTION":
        errors.append(
            "OWNER_PASSWORD_SALT is still set to default value. "
            "Generate a random salt and update .env"
        )
    
    if errors:
        print("=" * 70, file=sys.stderr)
        print("SECURITY CONFIGURATION ERROR — Production startup blocked", file=sys.stderr)
        print("=" * 70, file=sys.stderr)
        for err in errors:
            print(f"  ✗ {err}", file=sys.stderr)
        print("=" * 70, file=sys.stderr)
        print("Fix these issues in .env before running in production.", file=sys.stderr)
        print("For development, set DEBUG=true to bypass these checks.", file=sys.stderr)
        sys.exit(1)


# Run validation at import time (before app starts)
_validate_production_settings()


# Singleton settings instance
settings = Settings()
