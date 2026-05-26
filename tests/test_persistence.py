"""Persistence — save/load round-trip for backtest sessions."""

from __future__ import annotations

import os
import tempfile

import pandas as pd
import pytest

# Headless Qt for any QObject creation
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from engine.backtest_engine import BacktestEngine  # noqa: E402
from engine.strategy.context import Strategy  # noqa: E402
from engine.execution.execution import ExecutionModel  # noqa: E402
from engine.persistence.persistence import load_session, save_session  # noqa: E402


@pytest.fixture
def app():
    from PySide6.QtCore import QCoreApplication

    inst = QCoreApplication.instance()
    if inst is None:
        inst = QCoreApplication([])
    return inst


def _df(rows):
    df = pd.DataFrame(rows, columns=["time", "open", "high", "low", "close"])
    df["time"] = pd.to_datetime(df["time"])
    df["volume"] = 1.0
    return df


class BuyOnceAtOpen(Strategy):
    def __init__(self, sl=95.0, tp=None, vol=1.0):
        self._sent = False
        self._sl, self._tp, self._vol = sl, tp, vol

    def on_candle(self, ctx):
        if not self._sent and ctx.position is None:
            ctx.buy_at_open(volume=self._vol, sl=self._sl, tp=self._tp, reason="test", tag="test")
            self._sent = True


def _populate_engine(app):
    eng = BacktestEngine()
    from engine.models.models import AssetConfig

    eng.asset_config = AssetConfig(
        name="TEST",
        point_value=1.0,
        tick_size=0.01,
        buffer=0.0,
        max_lots_per_100k=10,
        currency="USD",
    )
    eng.initial_equity = 10_000.0
    eng.equity = eng.initial_equity
    eng.max_equity = eng.initial_equity
    rows = [
        ("2024-01-02 10:00", 100, 101, 99, 100),
        ("2024-01-02 10:01", 100, 102, 99, 101),
        ("2024-01-02 10:02", 100, 102, 90, 95),  # SL hit
        ("2024-01-02 10:03", 95, 97, 94, 96),
        ("2024-01-02 10:04", 96, 97, 95, 96),
    ]
    eng.load_data(_df(rows))
    eng.load_strategy(BuyOnceAtOpen(sl=95.0))
    for _ in range(5):
        eng.step()
    return eng


def test_save_then_load_restores_trades_and_equity(app, tmp_path):
    eng = _populate_engine(app)
    assert len(eng.trades) == 1

    target = tmp_path / "session.agbk"
    save_session(eng, str(target))
    assert target.exists()

    eng2 = BacktestEngine()
    eng2.initial_equity = 0.0  # garbage
    eng2.equity = 999.0
    meta = load_session(eng2, str(target))

    # Round-trip equality on the important state
    assert meta["trade_count"] == 1
    assert eng2.initial_equity == eng.initial_equity
    assert eng2.equity == pytest.approx(eng.equity)
    assert eng2.max_drawdown_abs == pytest.approx(eng.max_drawdown_abs)
    assert len(eng2.trades) == 1
    t1, t2 = eng.trades[0], eng2.trades[0]
    assert t1.id == t2.id
    assert t1.direction == t2.direction
    assert t1.exit_reason == t2.exit_reason
    assert t1.pnl == pytest.approx(t2.pnl)
    assert t1.mae_dollars == pytest.approx(t2.mae_dollars)
    # Stats accumulator rebuilt from trades
    assert eng2._stats_losses == 1
    assert eng2._stats_wins == 0


def test_save_includes_execution_model_overrides(app, tmp_path):
    eng = _populate_engine(app)
    eng.execution_model = ExecutionModel(
        profile="Custom",
        spread_points=2.0,
        commission_per_lot=1.0,
        slippage_points=0.5,
    )

    target = tmp_path / "with_costs.agbk"
    save_session(eng, str(target))

    eng2 = BacktestEngine()
    load_session(eng2, str(target))
    assert eng2.execution_model.profile == "Custom"
    assert eng2.execution_model.spread_points == 2.0
    assert eng2.execution_model.commission_per_lot == 1.0


def test_save_appends_extension_if_missing(app, tmp_path):
    eng = _populate_engine(app)
    target = tmp_path / "no_ext"
    save_session(eng, str(target))
    expected = tmp_path / "no_ext.agbk"
    assert expected.exists()


def test_load_missing_file_raises(app, tmp_path):
    eng = BacktestEngine()
    with pytest.raises(FileNotFoundError):
        load_session(eng, str(tmp_path / "does_not_exist.agbk"))


def test_round_trip_preserves_per_trade_costs(app, tmp_path):
    """TZ Phase 1 / E4 — commission, spread_cost and slippage_cost must
    survive a save/load cycle. Pre-fix the persistence layer wrote the
    fields fine but ``_row_to_trade`` already restored them; this test
    locks that contract and guards against future column reshuffles.
    """
    eng = _populate_engine(app)
    eng.execution_model = ExecutionModel(
        profile="Custom",
        spread_points=2.0,
        commission_per_lot=1.5,
        slippage_points=0.5,
    )
    # Force at least one trade with non-zero per-trade costs.
    if eng.trades:
        eng.trades[0].commission = 1.5
        eng.trades[0].spread_cost = 2.0
        eng.trades[0].slippage_cost = 0.5

    target = tmp_path / "costs_round_trip.agbk"
    save_session(eng, str(target))

    eng2 = BacktestEngine()
    load_session(eng2, str(target))
    assert len(eng2.trades) == len(eng.trades) == 1
    assert eng2.trades[0].commission == pytest.approx(1.5)
    assert eng2.trades[0].spread_cost == pytest.approx(2.0)
    assert eng2.trades[0].slippage_cost == pytest.approx(0.5)


def test_load_repopulates_stats_accumulator(app, tmp_path):
    """TZ Phase 1 / A5 — load_session must repopulate the centralised
    StatsAccumulator on the engine, not just the legacy mirror fields,
    so the Stats-tab reads correctly after a session restore.
    """
    eng = _populate_engine(app)
    target = tmp_path / "stats_round_trip.agbk"
    save_session(eng, str(target))

    eng2 = BacktestEngine()
    load_session(eng2, str(target))
    # The single populate-engine trade is a loss; the accumulator must agree.
    assert eng2.stats.losses == 1
    assert eng2.stats.wins == 0
    assert eng2.stats.total_trades == 1
    # Mirror fields must reflect the same values.
    assert eng2._stats_losses == eng2.stats.losses
    assert eng2._stats_wins == eng2.stats.wins
