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
    "backend": "software",  # selected chart backend family
    "gl_candles": False,  # whether shader-backed candles are requested
}


def _reset_state_for_tests() -> None:
    """Restore :data:`STATE` to its module-load default. Used by the
    unit tests to make :func:`configure_gpu_acceleration` calls
    idempotent across test cases."""
    STATE["configured"] = False
    STATE["enabled"] = False
    STATE["reason"] = "not_configured"
    STATE["backend"] = "software"
    STATE["gl_candles"] = False
    if hasattr(configure_gpu_acceleration, "_configured"):
        delattr(configure_gpu_acceleration, "_configured")


from engine.utils.env_flag import truthy_env


def should_use_gl_candles(
    *,
    force_gpu: Optional[bool] = None,
    force_gl_candles: Optional[bool] = None,
    bars_hint: Optional[int] = None,
) -> bool:
    """Decide whether the shader-backed candle renderer should be requested.

    Phase 5 production hardening: unify the pyqtgraph OpenGL switch and the
    candle-shader switch into one predictable policy.

    Rules:
    * explicit ``force_gl_candles`` wins;
    * otherwise a truthy ``AGREGAT_GL_CANDLES`` wins;
    * otherwise GL candles are enabled only when GPU mode is enabled/requested
      *and* the chart is large enough to benefit.

    ``bars_hint`` is advisory only; when omitted we conservatively assume the
    caller can still benefit from GL candles if GPU was explicitly requested.
    """
    if force_gl_candles is not None:
        return bool(force_gl_candles)

    env_pref = truthy_env(os.environ.get("AGREGAT_GL_CANDLES"))
    if env_pref is not None:
        return bool(env_pref)

    if force_gpu is None:
        gpu_env = truthy_env(os.environ.get(_ENV_VAR))
        gpu_enabled = bool(gpu_env) if gpu_env is not None else is_opengl_available()
    else:
        gpu_enabled = bool(force_gpu)

    if not gpu_enabled:
        return False

    if bars_hint is None:
        return True
    return int(bars_hint) >= 2_000

def is_opengl_available() -> bool:
    """Best-effort check that pyqtgraph can switch to its OpenGL
    backend without crashing.

    Important: avoid creating a real ``QOpenGLContext`` here. On some
    Windows/PySide combinations (and notably on Python 3.14 CI boxes)
    ``QOpenGLContext.create()`` may crash the process with a native
    access violation instead of raising a Python exception. For the
    bootstrap decision we only need a conservative import-level probe.

    If the OpenGL bindings import, we treat the GPU path as *potentially*
    available and let the actual widget/backend setup decide later. Any
    later failure is already handled by :func:`configure_gpu_acceleration`.
    """
    try:
        import pyqtgraph as _pg  # noqa: F401
        from PySide6 import QtOpenGLWidgets as _QtOpenGLWidgets  # noqa: F401
        from PySide6.QtGui import QOpenGLContext  # noqa: F401
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
            STATE.update(configured=True, enabled=False, reason="env_disabled", backend="software", gl_candles=False)
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
        STATE.update(configured=True, enabled=False, reason="unavailable_or_disabled", backend="software", gl_candles=False)
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
        STATE.update(configured=True, enabled=False, reason="setconfig_failed", backend="software", gl_candles=False)
        return False

    configure_gpu_acceleration._configured = True
    STATE.update(
        configured=True,
        enabled=True,
        reason="enabled",
        backend="opengl",
        gl_candles=should_use_gl_candles(force_gpu=True),
    )
    logger.info("Chart GPU acceleration: pyqtgraph OpenGL backend enabled")
    return True


__all__ = [
    "STATE",
    "configure_gpu_acceleration",
    "is_opengl_available",
    "should_use_gl_candles",
]
