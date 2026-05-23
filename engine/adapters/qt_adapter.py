"""
Qt Adapter — wraps :class:`engine.core.backtest_core.BacktestCore` and
re-exports its callback notifications as Qt signals so the existing
PySide6 GUI can connect to them.

This is the **only** module in the engine layer that imports PySide6.
The pure-Python core itself is import-clean (and therefore runnable on a
headless Linux server, in a unit test that doesn't spin up a
``QCoreApplication``, in a future CLI walk-forward harness, etc.).

Construction is cheap: the adapter inherits from ``BacktestCore`` so all
of the engine's data fields and methods are available on the adapter
instance directly (``adapter.equity``, ``adapter.load_data(df)``,
``adapter.step()``…). On ``__init__`` it registers internal callbacks
on each ``on_*`` list that emit the corresponding Qt signal.

The historical public class name is :class:`BacktestEngine` — the thin
re-export at :mod:`engine.backtest_engine` keeps that name available so
no GUI / strategy / test code needs to change.
"""

from __future__ import annotations

from typing import Any, Dict

from PySide6.QtCore import QObject, Signal

from engine.core.backtest_core import BacktestCore
from engine.models.models import Trade


class QtBacktestAdapter(QObject, BacktestCore):  # type: ignore[misc]
    """Backtest core + Qt signal façade.

    Multiple inheritance: :class:`QObject` provides the signal machinery
    while :class:`BacktestCore` provides every backtest field and method.
    ``QObject`` is listed first so that PySide6's metaclass owns
    instantiation; both ``__init__``\\ s are called explicitly in
    :meth:`__init__` because :class:`BacktestCore` does not use
    cooperative ``super()``.
    """

    # Signals — match the original BacktestEngine surface 1:1 so existing
    # ``self.engine.<name>.connect(...)`` calls in the GUI keep working.
    data_loaded = Signal(int)  # total candles
    step_completed = Signal(int, dict)  # current_idx, current_candle
    trade_completed = Signal(object)  # Trade object
    stats_updated = Signal(dict)
    simulation_finished = Signal()
    error_occurred = Signal(str)
    position_changed = Signal()  # position opens/closes/SL changes
    layer_registered = Signal(str, str, bool)  # tag, label, default_on
    drawing_changed = Signal(int)  # drawing _id (added/updated/removed)

    def __init__(self) -> None:
        QObject.__init__(self)
        BacktestCore.__init__(self)
        self._wire_signals()

    # ------------------------------------------------------------------
    # Wiring
    # ------------------------------------------------------------------
    def _wire_signals(self) -> None:
        """Forward every core notification to the matching Qt signal."""
        self.on_data_loaded.append(self._emit_data_loaded)
        self.on_step_completed.append(self._emit_step_completed)
        self.on_trade_completed.append(self._emit_trade_completed)
        self.on_stats_updated.append(self._emit_stats_updated)
        self.on_simulation_finished.append(self._emit_simulation_finished)
        self.on_error.append(self._emit_error)
        self.on_position_changed.append(self._emit_position_changed)
        self.on_layer_registered.append(self._emit_layer_registered)
        self.on_drawing_changed.append(self._emit_drawing_changed)

    # ------------------------------------------------------------------
    # One-line emitters (kept as methods so PySide6 binds the signal
    # objects after the QObject base has been initialised; using bare
    # ``self.<signal>.emit`` references at append-time would resolve the
    # signal too eagerly and break under some PySide6 versions).
    # ------------------------------------------------------------------
    def _emit_data_loaded(self, total: int) -> None:
        self.data_loaded.emit(total)

    def _emit_step_completed(self, idx: int, candle: Dict[str, Any]) -> None:
        self.step_completed.emit(idx, candle)

    def _emit_trade_completed(self, trade: Trade) -> None:
        self.trade_completed.emit(trade)

    def _emit_stats_updated(self, stats: Dict[str, Any]) -> None:
        self.stats_updated.emit(stats)

    def _emit_simulation_finished(self) -> None:
        self.simulation_finished.emit()

    def _emit_error(self, message: str) -> None:
        self.error_occurred.emit(message)

    def _emit_position_changed(self) -> None:
        self.position_changed.emit()

    def _emit_layer_registered(self, tag: str, label: str, default_on: bool) -> None:
        self.layer_registered.emit(tag, label, default_on)

    def _emit_drawing_changed(self, drawing_id: int) -> None:
        self.drawing_changed.emit(drawing_id)


__all__ = ["QtBacktestAdapter"]
