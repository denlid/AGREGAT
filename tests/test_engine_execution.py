"""End-to-end engine tests that exercise the ExecutionModel cost path."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pandas as pd
import pytest

from engine.backtest_engine import BacktestEngine  # noqa: E402
from engine.strategy.context import Strategy  # noqa: E402
from engine.execution.execution import ExecutionModel  # noqa: E402
from engine.models.models import AssetConfig  # noqa: E402


@pytest.fixture
def app():
    from PySide6.QtCore import QCoreApplication

    inst = QCoreApplication.instance()
    return inst if inst is not None else QCoreApplication([])


def _df(rows):
    df = pd.DataFrame(rows, columns=["time", "open", "high", "low", "close"])
    df["time"] = pd.to_datetime(df["time"])
    df["volume"] = 1.0
    return df


class BuyOnce(Strategy):
    """Buy at open of bar 1, hold through bar 2, close manually on bar 3."""

    def __init__(self):
        self._sent = False
        self._closed = False
        self._bars_open = 0

    def on_candle(self, ctx):
        if not self._sent and ctx.position is None:
            ctx.buy_at_open(volume=1.0, sl=None, tp=None, reason="t", tag="t")
            self._sent = True
            return
        if ctx.position is not None:
            self._bars_open += 1
            # Hold for one bar after fill so the close uses bar 3's close, not bar 2
            if self._bars_open >= 2 and not self._closed:
                self._closed = True
                ctx.close(reason="MANUAL")


def _engine(
    app,
    profile="Optimistic",
    spread=0.0,
    commission=0.0,
    slippage=0.0,
    point_value=10.0,
    tick_size=1.0,
):
    eng = BacktestEngine()
    eng.asset_config = AssetConfig(
        name="TEST",
        point_value=point_value,
        tick_size=tick_size,
        buffer=0.0,
        max_lots_per_100k=10,
        currency="USD",
        spread_points=spread,
        commission_per_lot=commission,
        slippage_points=slippage,
    )
    eng.execution_model = ExecutionModel(profile=profile)
    eng.initial_equity = 100_000.0
    eng.equity = eng.initial_equity
    eng.max_equity = eng.initial_equity
    rows = [
        ("2024-01-02 10:00", 100, 105, 100, 102),
        ("2024-01-02 10:01", 102, 108, 100, 104),  # bar 1: pending fills at open=102, _bars_open=1
        (
            "2024-01-02 10:02",
            104,
            110,
            100,
            108,
        ),  # bar 2: _bars_open=2 → ctx.close at this candle's close=108
    ]
    eng.load_data(_df(rows))
    eng.load_strategy(BuyOnce())
    for _ in range(3):
        eng.step()
    return eng


def test_optimistic_profile_no_costs_applied(app):
    eng = _engine(app, profile="Optimistic")
    assert len(eng.trades) == 1
    t = eng.trades[0]
    # Long at 102 → close at 108 close price; pnl_points=6, pnl=60 (vol=1, pv=10)
    assert t.entry_price == pytest.approx(102.0)
    assert t.exit_price == pytest.approx(108.0)
    assert t.pnl == pytest.approx(60.0)
    assert t.commission == pytest.approx(0.0)
    assert t.spread_cost == pytest.approx(0.0)


def test_realistic_profile_charges_spread_and_commission(app):
    """Bid model: long entry pays the full spread; long exit (MANUAL close)
    sells at Bid — no spread on exit. Round-trip spread cost is unchanged.
    """
    eng = _engine(app, profile="Realistic", spread=2.0, commission=5.0)
    t = eng.trades[0]
    # Long entry: 102 (Bid) + 2.0 (full spread) = 104
    assert t.entry_price == pytest.approx(104.0)
    # Long manual close: 108 (Bid) — no spread on this side
    assert t.exit_price == pytest.approx(108.0)
    # PnL: (108-104) * 1 vol * 10 pv = 40, then -5 commission = 35 (unchanged)
    assert t.pnl == pytest.approx(35.0)
    assert t.commission == pytest.approx(5.0)


def test_realistic_profile_slippage_compounds_costs(app):
    """spread=0, slippage=0.5 → entry +0.5, exit -0.5."""
    eng = _engine(app, profile="Realistic", spread=0.0, commission=0.0, slippage=0.5)
    t = eng.trades[0]
    assert t.entry_price == pytest.approx(102.5)
    assert t.exit_price == pytest.approx(107.5)
    # PnL: (107.5-102.5)*1*10 = 50.0, no commission
    assert t.pnl == pytest.approx(50.0)
    # slippage cost informational: 0.5 * 2 legs * 1 vol * 10 pv = 10
    assert t.slippage_cost == pytest.approx(10.0)


def test_costs_are_applied_to_equity(app):
    eng = _engine(app, profile="Realistic", spread=2.0, commission=5.0)
    # Equity diff = pnl == 35
    assert eng.equity == pytest.approx(eng.initial_equity + 35.0)


def test_max_drawdown_uses_post_cost_equity(app):
    """Cost-induced equity drop should still increase max_drawdown_abs."""
    eng = _engine(app, profile="Realistic", spread=20.0, commission=100.0)
    # Bid model: long entry = 102 + 20 (full spread) = 122; long exit = 108
    # (no spread on exit). pnl_pts = 108 - 122 = -14; gross = -140;
    # -100 commission = -240.
    t = eng.trades[0]
    assert t.pnl < 0
    assert eng.max_drawdown_abs > 0
