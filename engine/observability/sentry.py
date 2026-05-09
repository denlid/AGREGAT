"""Sprint 4.6 — Optional Sentry integration.

Sentry is **opt-in**: if ``AGREGAT_SENTRY_DSN`` is unset (or
``sentry_sdk`` isn't installed) :func:`init_sentry` is a no-op.
We never raise on missing dependencies — the engine should keep
working even without an internet connection.

Usage::

    from engine.observability import init_sentry
    init_sentry()  # called once at app start

The function returns a :class:`SentryStatus` so callers can surface
whether reporting is live in the GUI / logs.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

_SENTRY_INITIALISED: bool = False
_SENTRY_DSN: Optional[str] = None


@dataclass(frozen=True)
class SentryStatus:
    """Return value of :func:`init_sentry` and :func:`sentry_status`."""

    enabled: bool
    dsn_present: bool
    sdk_installed: bool
    reason: str


def is_sentry_enabled() -> bool:
    """Whether Sentry is live in the current process."""
    return _SENTRY_INITIALISED


def sentry_status() -> SentryStatus:
    """Return the current Sentry init status."""
    dsn = _SENTRY_DSN or os.environ.get("AGREGAT_SENTRY_DSN", "").strip()
    has_dsn = bool(dsn)
    try:
        import sentry_sdk  # noqa: F401

        has_sdk = True
    except ImportError:
        has_sdk = False
    if _SENTRY_INITIALISED:
        return SentryStatus(True, has_dsn, has_sdk, "enabled")
    if not has_dsn:
        return SentryStatus(False, False, has_sdk, "AGREGAT_SENTRY_DSN not set")
    if not has_sdk:
        return SentryStatus(False, has_dsn, False, "sentry_sdk not installed")
    return SentryStatus(False, has_dsn, has_sdk, "init_sentry() not called")


def init_sentry(
    *,
    dsn: Optional[str] = None,
    environment: str = "production",
    release: Optional[str] = None,
    traces_sample_rate: float = 0.0,
) -> SentryStatus:
    """Initialise Sentry if the DSN is set and the SDK is importable.

    Parameters
    ----------
    dsn
        Explicit DSN; falls back to the ``AGREGAT_SENTRY_DSN`` env var.
    environment
        Sentry environment tag (``production``, ``ci``, ``local``).
    release
        Optional release identifier (e.g. ``"agregat@7.78"``).
    traces_sample_rate
        Performance-tracing sample rate. Default ``0.0`` keeps the
        impact on quant runs at zero.

    Returns
    -------
    :class:`SentryStatus`
        Describes whether reporting is active and why.
    """
    global _SENTRY_INITIALISED, _SENTRY_DSN
    raw_dsn: str = dsn if dsn is not None else os.environ.get("AGREGAT_SENTRY_DSN", "")
    chosen_dsn = raw_dsn.strip()
    if not chosen_dsn:
        logger.debug("Sentry: DSN not provided — skipping init.")
        _SENTRY_DSN = None
        return SentryStatus(False, False, _has_sdk(), "AGREGAT_SENTRY_DSN not set")
    if not _has_sdk():
        logger.warning(
            "Sentry: AGREGAT_SENTRY_DSN is set but sentry_sdk is not installed — "
            "reporting disabled."
        )
        _SENTRY_DSN = chosen_dsn
        return SentryStatus(False, True, False, "sentry_sdk not installed")
    try:
        import sentry_sdk

        sentry_sdk.init(
            dsn=chosen_dsn,
            environment=environment,
            release=release,
            traces_sample_rate=traces_sample_rate,
        )
        _SENTRY_INITIALISED = True
        _SENTRY_DSN = chosen_dsn
        logger.info("Sentry: initialised (env=%s)", environment)
        return SentryStatus(True, True, True, "enabled")
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("Sentry: init failed (%s) — disabling.", exc)
        return SentryStatus(False, True, True, f"init failed: {exc}")


def _has_sdk() -> bool:
    try:
        import sentry_sdk  # noqa: F401

        return True
    except ImportError:
        return False


__all__ = ["init_sentry", "is_sentry_enabled", "sentry_status", "SentryStatus"]
