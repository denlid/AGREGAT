"""Background-worker scaffolding for the AGREGAT GUI.

Heavy analytics (Monte Carlo bootstraps, heatmap aggregation, session
matrices, drawdown matrices) used to run inline on the Qt event loop —
freezing the UI for hundreds of milliseconds and producing visible jank
during a backtest run. This module exposes two small primitives:

- :class:`ComputeWorker` — generic ``QThread`` that runs an arbitrary
  ``Callable`` off-thread and emits the result on the GUI thread via
  ``finished``. Cancellation is cooperative: callers check
  :pymeth:`is_cancelled` from inside the callable.
- :class:`Spinner` — minimal indeterminate progress overlay (a ``QLabel``
  that animates a spinner glyph). Designed to overlay any panel without
  pulling in extra dependencies. Use :pymeth:`Spinner.cover` to position
  it on top of a parent widget and :pymeth:`Spinner.start` /
  :pymeth:`Spinner.stop` to control it.

The scaffolding deliberately stays thin — pyqtgraph plotting, table
population, etc. must remain on the GUI thread, so the typical pattern is
to do CPU-heavy NumPy / aggregation work in the worker and let the
``finished`` slot perform the UI updates with the precomputed result.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Optional

from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal
from PySide6.QtWidgets import QLabel, QWidget

from gui.styling import style_helpers as _sh
from gui.styling.theme import font_mono, get_theme, register_theme_listener

from engine.logging_config import TraceContextAdapter, new_trace_id

logger = logging.getLogger("StrategyTester.gui.workers")


class ComputeWorker(QThread):
    """Run a Callable on a background QThread and surface the result via signals.

    Signals:
        finished(object): emitted on success; the payload is the callable's
            return value. Always check ``error`` first — if an exception was
            raised, ``finished`` is *not* emitted.
        error(Exception): emitted if the callable raised. The exception is
            forwarded as-is so the GUI slot can decide how to surface it.

    The worker captures a thread-safe ``cancel_event`` that the callable can
    poll via :pymeth:`is_cancelled`. AGREGAT analytics functions are
    long-running NumPy work — they typically check the flag between chunk
    boundaries.
    """

    finished = Signal(object)
    error = Signal(Exception)

    def __init__(
        self,
        fn: Callable[..., Any],
        *args: Any,
        parent: Optional[QObject] = None,
        trace_id: Optional[str] = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(parent)
        self._fn = fn
        self._args = args
        self._kwargs = kwargs
        self._cancel = threading.Event()
        # Sprint 2.7 — every async op carries a unique trace_id that
        # the JSON formatter (engine.logging_config.JSONFormatter)
        # surfaces in every record emitted from this worker. Callers
        # may pass ``trace_id`` explicitly to propagate an existing
        # trace through nested workers.
        self.trace_id: str = trace_id or new_trace_id()
        self._log = TraceContextAdapter(logger, trace_id=self.trace_id)

    def cancel(self) -> None:
        """Mark the worker as cancelled. The callable must poll
        :pymeth:`is_cancelled` to actually stop early — the thread is *not*
        killed forcibly."""
        self._cancel.set()

    def is_cancelled(self) -> bool:
        return self._cancel.is_set()

    def run(self) -> None:
        # Pass the cancel flag to the callable iff it accepts a `cancel_token`
        # kwarg; otherwise call as-is.
        kwargs = dict(self._kwargs)
        try:
            result = self._fn(*self._args, **kwargs)
        except Exception as exc:  # pragma: no cover — defensive
            self._log.exception("ComputeWorker callable raised")
            self.error.emit(exc)
            return
        if self._cancel.is_set():
            return
        self.finished.emit(result)


class Spinner(QLabel):
    """Minimal indeterminate spinner overlay.

    Renders a slowly-rotating glyph plus a caption ("Computing…") on top of
    a parent widget. Use :pymeth:`cover` to make the spinner fill its
    parent's geometry, then :pymeth:`start` / :pymeth:`stop` to control it.

    The spinner intentionally avoids QMovie / GIF assets to keep the
    repo asset-free. The animation is a pure-Python tick over a
    Unicode glyph cycle.
    """

    _GLYPHS = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")

    def __init__(
        self, parent: Optional[QWidget] = None, caption: str = "Computing…"
    ) -> None:
        super().__init__(parent)
        self._caption = caption
        self._idx = 0
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._sh = _sh
        self._get_theme = get_theme
        self._font_mono = font_mono
        self._apply_theme()
        register_theme_listener(self._apply_theme)
        self.setText(f"{self._GLYPHS[0]}  {self._caption}")
        self._timer = QTimer(self)
        self._timer.setInterval(80)
        self._timer.timeout.connect(self._tick)
        self.hide()

    def _apply_theme(self) -> None:
        sh = self._sh
        t = self._get_theme()
        # The translucent overlay tint matches the chart bg with alpha so it
        # blends on both light and dark themes.
        bg_hex = sh.c(t, "chart_bg")
        # Parse 6-hex into rgb for rgba() (Qt accepts hex but not in this form).
        try:
            r = int(bg_hex[1:3], 16)
            g = int(bg_hex[3:5], 16)
            b = int(bg_hex[5:7], 16)
        except Exception:
            r, g, b = 11, 11, 13
        self.setStyleSheet(
            f"background: rgba({r}, {g}, {b}, 200);"
            f"color: {sh.c(t, 'text')};"
            f"font-family: {self._font_mono()};"
            "font-size: 14px;"
            "padding: 12px;"
        )

    def cover(self, target: QWidget) -> None:
        """Reparent the spinner onto `target` and resize to fill it.

        Call this once during widget construction; the spinner stays
        hidden until :pymeth:`start` is invoked.
        """
        self.setParent(target)
        # Stay above sibling widgets even if added before them.
        self.raise_()
        self.setGeometry(0, 0, target.width(), target.height())
        target.installEventFilter(self)

    def eventFilter(self, watched: QObject, event: Any) -> bool:
        # Keep the overlay sized to the parent on resize.
        if isinstance(watched, QWidget) and watched is self.parent():
            from PySide6.QtCore import QEvent

            if event.type() == QEvent.Type.Resize:
                self.setGeometry(0, 0, watched.width(), watched.height())
        return super().eventFilter(watched, event)

    def start(self, caption: Optional[str] = None) -> None:
        if caption is not None:
            self._caption = caption
        self._idx = 0
        self.setText(f"{self._GLYPHS[0]}  {self._caption}")
        self.show()
        self.raise_()
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()
        self.hide()

    def _tick(self) -> None:
        self._idx = (self._idx + 1) % len(self._GLYPHS)
        self.setText(f"{self._GLYPHS[self._idx]}  {self._caption}")
