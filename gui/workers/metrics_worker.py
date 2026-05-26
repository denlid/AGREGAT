"""
MetricsWorker — async compute layer for the metric cards / dashboard.

Builds on the existing :class:`gui.workers.workers.ComputeWorker` scaffolding
but adds two things the UI needs for the new dashboard:

* A *version token* that pins a worker to a particular AnalyticsContext
  snapshot. If the context bumps (trades arrived / reset) before the
  worker finishes, its result is dropped instead of stomping over the
  fresher state.
* A small dispatch helper that keeps only one worker-per-key in flight;
  scheduling a new one cancels the previous so rapid slider drags do not
  pile up.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Optional

from PySide6.QtCore import QObject, Signal

from engine.analytics.context import AnalyticsContext
from gui.workers.workers import ComputeWorker

logger = logging.getLogger("StrategyTester.gui.workers.metrics_worker")


class MetricsWorker(QObject):
    """Run a metrics callable against an AnalyticsContext on a worker thread.

    The callable receives the bound context as its first positional argument.
    Signals are emitted on the GUI thread:

    * ``finished(object)`` — fires with the callable's return value *only*
      when ``ctx.version`` still matches the snapshot taken at start time.
    * ``stale(int)`` — fires (with the captured version) when the worker
      finished but its data is no longer current. UI code can ignore the
      payload; useful for tests / debug logging.
    * ``error(Exception)`` — surfaced verbatim from the underlying compute.
    """

    finished = Signal(object)
    stale = Signal(int)
    error = Signal(Exception)

    def __init__(
        self,
        ctx: AnalyticsContext,
        fn: Callable[..., Any],
        *args: Any,
        parent: Optional[QObject] = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(parent)
        self._ctx = ctx
        self._fn = fn
        self._args = args
        self._kwargs = kwargs
        self._captured_version: int = ctx.version
        self._inner: Optional[ComputeWorker] = None

    # ------------------------------------------------------------------
    @property
    def captured_version(self) -> int:
        return self._captured_version

    def start(self) -> None:
        """Submit the callable to the global Qt thread-pool."""
        self._captured_version = self._ctx.version

        def _run() -> Any:
            return self._fn(self._ctx, *self._args, **self._kwargs)

        w = ComputeWorker(_run, parent=self)
        w.finished.connect(self._on_inner_finished)
        w.error.connect(self.error.emit)
        self._inner = w
        w.start()

    def cancel(self) -> None:
        if self._inner is not None:
            self._inner.cancel()

    # ------------------------------------------------------------------
    def _on_inner_finished(self, payload: Any) -> None:
        if self._ctx.version != self._captured_version:
            self.stale.emit(self._captured_version)
            return
        self.finished.emit(payload)


class MetricsDispatcher(QObject):
    """Owns at-most-one in-flight :class:`MetricsWorker` per logical key.

    A tab that has many independent cards / charts can share a dispatcher
    so that scheduling a fresh recompute under key ``"card.sharpe"``
    cancels the previous one without affecting other keys. Cancellation
    is cooperative — the inner callable must check
    :pymeth:`ComputeWorker.is_cancelled` if it is long-running.
    """

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._by_key: Dict[str, MetricsWorker] = {}

    def submit(
        self,
        key: str,
        ctx: AnalyticsContext,
        fn: Callable[..., Any],
        on_done: Callable[[Any], None],
        on_error: Optional[Callable[[Exception], None]] = None,
        *args: Any,
        **kwargs: Any,
    ) -> MetricsWorker:
        prev = self._by_key.pop(key, None)
        if prev is not None:
            prev.cancel()

        w = MetricsWorker(ctx, fn, *args, parent=self, **kwargs)

        def _wrap_done(payload: Any) -> None:
            # Only deliver if still the active worker for this key.
            if self._by_key.get(key) is w:
                self._by_key.pop(key, None)
                on_done(payload)

        def _wrap_err(exc: Exception) -> None:
            if self._by_key.get(key) is w:
                self._by_key.pop(key, None)
            if on_error is not None:
                on_error(exc)
            else:
                logger.exception("MetricsWorker[%s] failed: %s", key, exc)

        w.finished.connect(_wrap_done)
        w.error.connect(_wrap_err)
        # Stale results are silently dropped; nothing to deliver.
        self._by_key[key] = w
        w.start()
        return w

    def cancel_all(self) -> None:
        for w in list(self._by_key.values()):
            w.cancel()
        self._by_key.clear()


__all__ = ["MetricsWorker", "MetricsDispatcher"]
