"""Phase 1 — chart GPU acceleration bootstrap.

Implements step 1 of ``chart_gpu_optimization_plan.md`` Phase 1:
flips pyqtgraph's experimental OpenGL backend on at process start so
every subsequent ``pg.PlotWidget`` renders through a ``QOpenGLWidget``
viewport instead of the software ``QPainter`` path.

The module is intentionally tiny and side-effect-free until its public
entry point is called. It exposes:

* :func:`is_opengl_available` — runtime check that a working OpenGL
  binding is importable. Returns ``False`` gracefully on headless boxes
  (CI runners without a GPU, environments without ``PySide6-Addons``)
  so callers can keep the software backend without crashing.
* :func:`configure_gpu_acceleration` — single bootstrap call. Honours
  the ``AGREGAT_USE_GPU`` env var (truthy → force on, falsy → force
  off, unset → auto-detect via :func:`is_opengl_available`). Returns
  the actual decision (``True`` if pyqtgraph was switched to OpenGL,
  ``False`` otherwise) so callers / tests can assert on the outcome.

The decision is recorded in :data:`STATE` so debugging UIs / the
``Settings`` panel can surface whether the GPU path is live without
re-running detection.

Why this lives in ``gui.chart_tab`` rather than ``app.py``: keeping the
detection next to the chart code that benefits from it makes it easy
to unit-test (no QApplication required) and avoids polluting
``app.py`` with import-time pyqtgraph state.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger("StrategyTester.gui.chart_tab.gpu_config")

# Env var that lets ops toggle the GPU path without code changes. Same
# convention as ``QT_QPA_PLATFORM`` — string values, falsy = disable.
_ENV_VAR = "AGREGAT_USE_GPU"

# Module-level record of the last :func:`configure_gpu_acceleration`
# decision. Read-only contract for callers; ``configure_*`` is the only
# writer. Tests reset this between cases via ``_reset_state_for_tests``.
STATE: dict = {
    "configured": False,  # has configure_gpu_acceleration been called?
    "enabled": False,  # did pyqtgraph end up in OpenGL mode?
    "reason": "not_configured",  # short tag explaining the decision
}


def _reset_state_for_tests() -> None:
    """Restore :data:`STATE` to its module-load default. Used by the
    unit tests to make :func:`configure_gpu_acceleration` calls
    idempotent across test cases."""
    STATE["configured"] = False
    STATE["enabled"] = False
    STATE["reason"] = "not_configured"


from engine.utils.env_flag import truthy_env

def is_opengl_available() -> bool:
    """Best-effort check that pyqtgraph can switch to its OpenGL
    backend without crashing.

    pyqtgraph's OpenGL viewport drags ``PySide6.QtOpenGLWidgets`` and
    a working ``QOpenGLContext``. On a Linux CI runner without a GPU
    or on a stripped-down PySide6 install the imports raise
    :class:`ImportError`. We treat any failure here as "GPU path
    unavailable" and let the caller fall back.
    """
    try:
        import pyqtgraph as _pg  # noqa: F401
        from PySide6 import QtOpenGLWidgets as _QtOpenGLWidgets  # noqa: F401
        from PySide6.QtGui import QOpenGLContext, QSurfaceFormat
        ctx = QOpenGLContext()
        ctx.setFormat(QSurfaceFormat.defaultFormat())
        if not ctx.create():
            return False
    except Exception as exc:  # noqa: BLE001 — diagnostic only
        logger.debug("OpenGL probe failed: %r", exc)
        return False
    return True


def configure_gpu_acceleration(force: Optional[bool] = None) -> bool:
    """Switch pyqtgraph's global config to OpenGL when possible.

    Parameters
    ----------
    force
        * ``True`` — enable OpenGL even if :func:`is_opengl_available`
          would have skipped it. Useful in tests and as an explicit
          opt-in from the settings UI. Still bails out cleanly if the
          actual pyqtgraph import fails.
        * ``False`` — keep the software ``QPainter`` backend. Used by
          the settings UI / ops to disable the GPU path without
          unsetting the env var.
        * ``None`` (default) — auto-detect. Respects the
          ``AGREGAT_USE_GPU`` env var if set, otherwise enables when
          :func:`is_opengl_available` returns ``True``.

    Returns
    -------
    bool
        Whether pyqtgraph was actually flipped to OpenGL. Records the
        decision and a short reason tag in :data:`STATE`.

    Notes
    -----
    Must be called **before** any ``pg.PlotWidget`` is constructed —
    pyqtgraph reads ``useOpenGL`` at widget creation time, not on
    every paint. The bootstrap in :mod:`app` invokes this immediately
    after the ``QApplication`` is built so the main window's chart
    widgets pick up the new viewport.
    """
    global _configured
    if getattr(configure_gpu_acceleration, "_configured", False) and force is None:
        logger.debug("gpu_config: already configured, skipping")
        return STATE.get("enabled", False)
        
    if force is None:
        env_pref = truthy_env(os.environ.get(_ENV_VAR))
        if env_pref is False:
            STATE.update(configured=True, enabled=False, reason="env_disabled")
            try:
                import pyqtgraph as pg

                pg.setConfigOptions(useOpenGL=False)
            except Exception as exc:  # noqa: BLE001
                logger.debug("pyqtgraph not importable while disabling GPU: %r", exc)
            return False
        want_enable = env_pref if env_pref is not None else is_opengl_available()
    else:
        want_enable = bool(force)

    if not want_enable:
        STATE.update(configured=True, enabled=False, reason="unavailable_or_disabled")
        try:
            import pyqtgraph as pg

            pg.setConfigOptions(useOpenGL=False)
        except Exception as exc:  # noqa: BLE001
            logger.debug("pyqtgraph not importable while disabling GPU: %r", exc)
        return False

    try:
        import pyqtgraph as pg

        pg.setConfigOptions(useOpenGL=True)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to enable pyqtgraph OpenGL backend: %r", exc)
        STATE.update(configured=True, enabled=False, reason="setconfig_failed")
        return False

    configure_gpu_acceleration._configured = True
    STATE.update(configured=True, enabled=True, reason="enabled")
    logger.info("Chart GPU acceleration: pyqtgraph OpenGL backend enabled")
    return True


__all__ = [
    "STATE",
    "configure_gpu_acceleration",
    "is_opengl_available",
]
