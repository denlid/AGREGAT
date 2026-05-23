"""
AGREGAT Centralised Configuration — Sprint 4.7

Single source of truth for every environment variable the engine reads.
Replaces ad-hoc ``os.environ.get(...)`` calls scattered across the codebase.

All settings are read from environment variables on first access and cached
for the lifetime of the process. Settings can also be overridden at runtime
via ``Settings(...)`` keyword arguments, which takes priority over env vars.

Usage::

    from engine.config import settings

    # Access any setting as an attribute
    dsn = settings.sentry_dsn
    use_gpu = settings.use_gpu

    # Override at runtime (tests, CLI overrides)
    settings.override(sentry_dsn="https://...")


Conventions
-----------
* Environment variable name → ``AGREGAT_<UPPERCASE_NAME>``.
* Python attribute name → ``snake_case`` of the env suffix.
* Every setting has a sensible default so the engine works out-of-the-box.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

logger = logging.getLogger("StrategyTester.engine.config")


def _env(name: str, default: str = "") -> str:
    """Read ``AGREGAT_<name>`` from the environment."""
    return os.environ.get(f"AGREGAT_{name}", default)


def _env_bool(name: str, default: bool = False) -> bool:
    """Parse ``AGREGAT_<name>`` as a boolean (1/true/yes/y/on → True)."""
    val = _env(name)
    if not val:
        return default
    return val.strip().lower() in {"1", "true", "yes", "on", "y"}


def _env_int(name: str, default: int = 0) -> int:
    """Parse ``AGREGAT_<name>`` as an integer."""
    val = _env(name, str(default))
    try:
        return int(val.strip())
    except (ValueError, TypeError):
        logger.warning("AGREGAT_%s=%r is not a valid int, falling back to %d", name, val, default)
        return default


# ---------------------------------------------------------------------------
# Settings descriptor — one field per env variable
# ---------------------------------------------------------------------------
@dataclass
class Settings:
    # ── Observability ────────────────────────────────────────────────────
    sentry_dsn: str = ""
    """Sentry DSN for error tracking. Env: ``AGREGAT_SENTRY_DSN``."""

    audit_db_path: str = ""
    """Path to the audit-log SQLite database. Env: ``AGREGAT_AUDIT_DB``.
    Empty → ``~/.agregat/audit.db``."""

    # ── GPU / Rendering ──────────────────────────────────────────────────
    use_gpu: bool = False
    """Enable pyqtgraph OpenGL backend. Env: ``AGREGAT_GL``. (theme.py)"""

    use_gl_candles: bool = False
    """Enable OpenGL candlestick rendering. Env: ``AGREGAT_GL_CANDLES``."""

    # ── Data / Performance ────────────────────────────────────────────────
    data_pager_enabled: bool = False
    """Enable the async data-pager for large files. Env: ``AGREGAT_DATA_PAGER``."""

    csv_cache_size: int = 16
    """Max number of CSV files in the LRU cache. Env: ``AGREGAT_CSV_CACHE_SIZE``."""

    # ── Testing ──────────────────────────────────────────────────────────
    run_heavy_perf: bool = False
    """Run heavy performance tests. Env: ``RUN_HEAVY_PERF``."""

    # ── Internal overrides (set programmatically, not from env) ──────────
    _overrides: Dict[str, Any] = field(default_factory=dict, repr=False)

    def __getattribute__(self, name: str) -> Any:
        """Honour programmatic overrides before env-var defaults."""
        if name.startswith("_"):
            return super().__getattribute__(name)
        overrides: Dict[str, Any] = super().__getattribute__("_overrides")
        if name in overrides:
            return overrides[name]
        return super().__getattribute__(name)

    def override(self, **kwargs: Any) -> None:
        """Override settings at runtime (takes priority over env vars).

        Typically called once at app startup from ``app.py``::

            from engine.config import settings
            settings.override(sentry_dsn="https://...", use_gpu=True)
        """
        self._overrides.update(kwargs)
        for k in kwargs:
            logger.debug("Settings override: %s = %r", k, kwargs[k])

    @classmethod
    def from_env(cls) -> Settings:
        """Construct a :class:`Settings` reading every field from env vars."""
        return cls(
            sentry_dsn=_env("SENTRY_DSN"),
            audit_db_path=_env("AUDIT_DB"),
            use_gpu=_env_bool("GL"),
            use_gl_candles=_env_bool("GL_CANDLES"),
            data_pager_enabled=_env_bool("DATA_PAGER"),
            csv_cache_size=_env_int("CSV_CACHE_SIZE", 16),
            run_heavy_perf=_env_bool("RUN_HEAVY_PERF"),
        )


# ---------------------------------------------------------------------------
# Module-level singleton — import once, use everywhere
# ---------------------------------------------------------------------------
settings: Settings = Settings.from_env()
"""Global settings singleton. Import ``from engine.config import settings``."""


__all__ = [
    "Settings",
    "settings",
]
