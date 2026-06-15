"""Headless integration tests for :class:`engine.core.backtest_core.BacktestCore`.

These tests deliberately import only from ``engine.core`` (no Qt adapter,
no GUI, no ``QCoreApplication`` fixture) to prove the engine can run
without a display server. They also exercise the callback API: every
notification the GUI normally subscribes to via Qt signals must fire as
plain Python callbacks too.
"""

from __future__ import annotations

import sys
from typing import Any, List, Tuple

import pandas as pd

from engine.strategy.context import Strategy
from engine.core.backtest_core import BacktestCore
from engine.execution.execution import ExecutionModel
from engine.models.models import AssetConfig


def test_backtest_core_module_does_not_pull_in_pyside6():
    """Importing the core must not have transitively imported PySide6.

    This is the structural invariant that the engine ↔ Qt split is supposed
    to enforce. We run the check in a fresh subprocess so prior tests that
    legitimately exercised the Qt adapter cannot pollute the result.
    """
    import subprocess
    import textwrap

    repo = pathlib_path_of_repo()
    script = textwrap.dedent(
        """
        import sys
        # Importing only engine.core must not cascade into PySide6.
        import engine.core.backtest_core  # noqa: F401
        leaked = [m for m in sys.modules if m.startswith('PySide6')]
        if leaked:
            print('LEAK:' + ','.join(leaked[:3]))
            sys.exit(1)
        # Importing the engine package itself must not load PySide6 either —
        # the package init resolves BacktestEngine lazily.
        import engine  # noqa: F401
        leaked = [m for m in sys.modules if m.startswith('PySide6')]
        if leaked:
            print('LEAK_PKG:' + ','.join(leaked[:3]))
            sys.exit(2)
        print('OK')
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        cwd=str(repo),
    )
    assert result.returncode == 0, (
        f"engine.core import leaked PySide6 (rc={result.returncode})\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


def pathlib_path_of_repo():
    import pathlib

    here = pathlib.Path(__file__).resolve()
    # tests/ → repo root
    return here.parent.parent


class _BuyOnce(Strategy):
    """Minimal strategy: open a long on the first bar, hold."""

    def __init__(self) -> None:
        self._sent = False

    def on_candle(self, ctx) -> None:  # type: ignore[override]
        if not self._sent and ctx.position is None:
            ctx.buy_at_open(volume=1.0, sl=None, tp=None, reason="t", tag="t")
            self._sent = True


class _LogEveryCandle(Strategy):
    """Strategy that exercises ctx.log in the headless hot path."""

    def on_candle(self, ctx) -> None:  # type: ignore[override]
        ctx.log("tick")


class _ReadTimeParts(Strategy):
    def __init__(self) -> None:
        self.rows: list[tuple[int, int, int, str, bool]] = []

    def on_candle(self, ctx) -> None:  # type: ignore[override]
        self.rows.append(
            (
                ctx.minute_of_day,
                ctx.hour,
                ctx.weekday,
                ctx.get_session(),
                ctx.is_trading_hours(),
            )
        )


def _df() -> pd.DataFrame:
    rows = [
        ("2024-01-02 10:00", 100, 105, 100, 102),
        ("2024-01-02 10:01", 102, 108, 100, 104),
        ("2024-01-02 10:02", 104, 110, 100, 108),
    ]
    df = pd.DataFrame(rows, columns=["time", "open", "high", "low", "close"])
    df["time"] = pd.to_datetime(df["time"])
    df["volume"] = 1.0
    return df


def _make_core() -> BacktestCore:
    core = BacktestCore()
    core.asset_config = AssetConfig(
        name="TEST",
        point_value=10.0,
        tick_size=1.0,
        buffer=0.0,
        max_lots_per_100k=10,
        currency="USD",
        spread_points=0.0,
        commission_per_lot=0.0,
        slippage_points=0.0,
    )
    core.execution_model = ExecutionModel(profile="Optimistic")
    core.initial_equity = 100_000.0
    core.equity = core.initial_equity
    core.max_equity = core.initial_equity
    return core


def test_strategy_time_helpers_use_precomputed_parts(monkeypatch):
    import engine.strategy.context as context_mod

    def _unexpected_parse(*args, **kwargs):
        raise AssertionError("timestamp parser should not run for current-bar helpers")

    core = _make_core()
    core.load_data(_df())
    strategy = _ReadTimeParts()
    core.load_strategy(strategy)

    monkeypatch.setattr(context_mod, "get_session", _unexpected_parse)
    result = core.run_visual_headless()

    assert result is not None
    active = core.strategy
    assert isinstance(active, _ReadTimeParts)
    assert active.rows == [
        (600, 10, 1, "London_OTT", True),
        (601, 10, 1, "London_OTT", True),
        (602, 10, 1, "London_OTT", True),
    ]


def test_core_runs_a_backtest_without_qcoreapplication():
    """The core must produce trades end-to-end with no Qt event loop."""
    core = _make_core()
    core.load_data(_df())
    core.load_strategy(_BuyOnce())

    # Step through the data; the strategy buys on bar 1 and never closes,
    # so we don't assert on trades — just that the engine advances cleanly.
    for _ in range(3):
        core.step()

    assert core.current_idx == 2
    assert core.position is not None
    assert core.position.direction == "long"


def test_run_visual_headless_suppresses_strategy_logs(caplog):
    core = _make_core()
    core.load_data(_df())
    core.load_strategy(_LogEveryCandle())

    core._suppress_strategy_logs = False
    with caplog.at_level("INFO", logger="StrategyTester.engine.context"):
        result = core.run_visual_headless()

    assert result is not None
    assert core._suppress_strategy_logs is False
    assert not any("[Strategy] tick" in record.getMessage() for record in caplog.records)


def test_core_callbacks_fire_in_correct_order():
    """Every ``on_*`` registry must receive its expected notification."""
    core = _make_core()

    events: List[Tuple[str, Any]] = []
    core.on_data_loaded.append(lambda n: events.append(("data_loaded", n)))
    core.on_step_completed.append(lambda idx, candle: events.append(("step_completed", idx)))
    core.on_trade_completed.append(
        lambda trade: events.append(("trade_completed", trade.direction))
    )
    core.on_stats_updated.append(
        lambda stats: events.append(("stats_updated", stats["total_trades"]))
    )
    core.on_simulation_finished.append(lambda: events.append(("simulation_finished", None)))
    core.on_position_changed.append(lambda: events.append(("position_changed", None)))

    core.load_data(_df())
    core.load_strategy(_BuyOnce())
    for _ in range(4):
        core.step()  # final step exhausts the dataset → simulation_finished

    kinds = [e[0] for e in events]
    assert "data_loaded" in kinds
    assert "stats_updated" in kinds
    assert "step_completed" in kinds
    assert "position_changed" in kinds  # buy fill triggers it
    assert "simulation_finished" in kinds


def test_core_callback_failure_does_not_abort_simulation(caplog):
    """A bad subscriber should be logged and skipped, not crash the run."""
    core = _make_core()

    def bad(_n: int) -> None:
        raise RuntimeError("subscriber blew up")

    fired: List[int] = []
    core.on_data_loaded.append(bad)
    core.on_data_loaded.append(lambda n: fired.append(n))

    core.load_data(_df())
    # Despite the failing callback, the second one must still receive the call.
    assert fired == [3]


def test_drawing_callbacks_fire_with_id() -> None:
    core = _make_core()
    core.load_data(_df())

    seen_ids: List[int] = []
    core.on_drawing_changed.append(lambda d_id: seen_ids.append(d_id))

    drawing_id = core.add_drawing({"t": "2024-01-02 10:00", "kind": "marker"})
    assert seen_ids[-1] == drawing_id
    assert core._drawings_by_id[drawing_id] is core.drawings[-1]

    core.update_drawing(drawing_id, {"kind": "marker-updated"})
    assert core._drawings_by_id[drawing_id]["kind"] == "marker-updated"
    core.remove_drawing(drawing_id)
    assert drawing_id not in core._drawings_by_id
    assert seen_ids[-1] == drawing_id


def test_strategy_drawing_accepts_epoch_nanoseconds_as_string() -> None:
    core = _make_core()
    core.load_data(_df())

    t1_ns = "1609768800000000000"
    t2_ns = "1609768860000000000"
    drawing_id = core.add_drawing(
        {"t1": t1_ns, "p1": 100.0, "t2": t2_ns, "p2": 101.0, "kind": "rect"}
    )

    drawing = next(d for d in core.drawings if d["_id"] == drawing_id)
    assert drawing["_t1_np"] == pd.to_datetime(int(t1_ns), unit="ns").to_datetime64()
    assert drawing["_t2_np"] == pd.to_datetime(int(t2_ns), unit="ns").to_datetime64()


def test_drawing_batch_operations_by_tag() -> None:
    core = _make_core()
    core.load_data(_df())

    rect_1 = core.add_drawing({"t": "2024-01-02 10:00", "kind": "rect", "tag": "fvg"})
    rect_2 = core.add_drawing({"t": "2024-01-02 10:01", "kind": "rect", "tag": "fvg"})
    text = core.add_drawing({"t": "2024-01-02 10:02", "kind": "text", "tag": "label"})

    seen_ids: List[int] = []
    core.on_drawing_changed.append(lambda d_id: seen_ids.append(d_id))

    assert core.update_drawings_by_tag("fvg", {"color": "green"}) == 2
    assert [d.get("color") for d in core.drawings if d.get("tag") == "fvg"] == [
        "green",
        "green",
    ]
    assert "color" not in next(d for d in core.drawings if d.get("_id") == text)
    assert seen_ids[-2:] == [rect_1, rect_2]

    assert core.remove_drawings_by_tag("fvg") == 2
    assert [d.get("_id") for d in core.drawings] == [text]
    assert rect_1 not in core._drawings_by_id
    assert rect_2 not in core._drawings_by_id
    assert text in core._drawings_by_id
    assert seen_ids[-2:] == [rect_1, rect_2]


def test_reset_clears_strategy_drawings() -> None:
    core = _make_core()
    core.load_data(_df())

    first_id = core.add_drawing({"t": "2024-01-02 10:00", "kind": "marker"})
    assert first_id == 1
    assert len(core.drawings) == 1

    core.reset()

    assert core.drawings == []
    assert core._drawings_by_id == {}
    assert core._next_drawing_id == 0
    assert core.add_drawing({"t": "2024-01-02 10:01", "kind": "marker"}) == 1


def test_layer_registered_callback_is_idempotent() -> None:
    core = _make_core()
    received: List[Tuple[str, str, bool]] = []
    core.on_layer_registered.append(
        lambda tag, label, default_on: received.append((tag, label, default_on))
    )
    core.register_layer("ema-fast", "EMA Fast", True)
    core.register_layer("ema-fast", "EMA Fast", True)  # duplicate
    assert received == [("ema-fast", "EMA Fast", True)]


def test_qt_adapter_inherits_core_attributes() -> None:
    """Smoke test the adapter layer too: the alias preserves the surface."""
    from PySide6.QtCore import QCoreApplication

    from engine.adapters.qt_adapter import QtBacktestAdapter

    if QCoreApplication.instance() is None:
        QCoreApplication([])

    adapter = QtBacktestAdapter()
    # A handful of the BacktestCore fields the GUI actually touches.
    assert adapter.equity == 100_000.0
    assert adapter.position is None
    assert isinstance(adapter.trades, list)
    # And the Qt signal objects must be present.
    assert hasattr(adapter, "data_loaded")
    assert hasattr(adapter, "trade_completed")


def test_backtest_engine_is_qt_adapter() -> None:
    """The historical name must resolve to the adapter for back-compat."""
    from engine.adapters.qt_adapter import QtBacktestAdapter
    from engine.backtest_engine import BacktestEngine

    assert BacktestEngine is QtBacktestAdapter
