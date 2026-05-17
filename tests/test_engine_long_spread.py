"""SL/TP collision tests for LONG positions with spread (Bid model).

For long positions the chart is Bid-priced and the trader sells the Bid
to exit. SL/TP triggers therefore use the candle's raw High/Low directly:

    SL_long hit  when Low  <= SL
    TP_long hit  when High >= TP

The long *entry* itself fills at the Ask = Bid + spread (the full spread,
not half), since the user is buying at market.
"""

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


class BuyOnceAtOpen(Strategy):
    """Submit one buy_at_open order on the first candle, then sit still."""

    def __init__(self, sl=None, tp=None, volume=1.0):
        self._sent = False
        self._sl = sl
        self._tp = tp
        self._vol = volume

    def on_candle(self, ctx) -> None:  # type: ignore[override]
        if not self._sent and ctx.position is None:
            ctx.buy_at_open(volume=self._vol, sl=self._sl, tp=self._tp, reason="t", tag="t")
            self._sent = True


def _engine(app, profile="Realistic", spread=2.0, point_value=1.0, tick_size=1.0):
    eng = BacktestEngine()
    eng.asset_config = AssetConfig(
        name="TEST",
        point_value=point_value,
        tick_size=tick_size,
        buffer=0.0,
        max_lots_per_100k=10,
        currency="USD",
        spread_points=spread,
    )
    eng.execution_model = ExecutionModel(profile=profile)
    eng.initial_equity = 100_000.0
    eng.equity = eng.initial_equity
    eng.max_equity = eng.initial_equity
    return eng


def test_long_entry_pays_full_spread(app):
    """Long entry fills at Ask = Bid + spread, regardless of slippage."""
    rows = [
        # signal bar
        ("2024-01-02 10:00", 100, 100, 100, 100),
        # entry bar: Bid open=100, expected fill = 100 + spread=2 = 102
        ("2024-01-02 10:01", 100, 105, 100, 102),
        # exit at Bid 102 (manual close not triggered here; SL=98 not hit, TP None)
        ("2024-01-02 10:02", 102, 105, 98, 100),
    ]
    eng = _engine(app, spread=2.0)
    eng.load_data(_df(rows))
    eng.load_strategy(BuyOnceAtOpen(sl=95.0))  # never hit

    for _ in range(3):
        eng.step()

    assert eng.position is not None
    assert eng.position.entry_price == pytest.approx(102.0)


def test_long_sl_fills_at_bid_no_spread(app):
    """Long SL triggers when raw Low <= SL; fills at the user-set SL price exactly.

    Bid model: the chart shows Bid; selling on SL hits the Bid directly
    with no spread adjustment. Spread only affects the entry side.
    """
    rows = [
        ("2024-01-02 10:00", 100, 100, 100, 100),
        # entry at fill 102 (open=100 + spread 2)
        ("2024-01-02 10:01", 100, 105, 100, 102),
        # Low=97 <= SL=98 -> fills at SL=98 (the user-anchored level)
        ("2024-01-02 10:02", 102, 103, 97, 100),
    ]
    eng = _engine(app, spread=2.0)
    eng.load_data(_df(rows))
    eng.load_strategy(BuyOnceAtOpen(sl=98.0))

    for _ in range(3):
        eng.step()

    assert len(eng.trades) == 1
    t = eng.trades[0]
    assert t.direction == "long"
    assert t.exit_reason == "SL"
    assert t.exit_price == pytest.approx(98.0)
    # PnL points: 98 - 102 = -4 (entry paid the full spread; exit is at chart Bid)
    assert t.pnl_points == pytest.approx(-4.0)


def test_long_sl_not_triggered_when_low_above_sl(app):
    """Bid model: long SL uses raw Bid Low (no spread). Low=99 > SL=98 -> no trigger."""
    rows = [
        ("2024-01-02 10:00", 100, 100, 100, 100),
        ("2024-01-02 10:01", 100, 105, 100, 102),  # entry at 102
        ("2024-01-02 10:02", 102, 103, 99, 100),  # Low=99 > SL=98
        ("2024-01-02 10:03", 100, 103, 99, 100),
    ]
    eng = _engine(app, spread=2.0)
    eng.load_data(_df(rows))
    eng.load_strategy(BuyOnceAtOpen(sl=98.0))

    for _ in range(4):
        eng.step()

    assert len(eng.trades) == 0
    assert eng.position is not None


def test_long_tp_fills_at_chart_price(app):
    """Long TP fires when raw High >= TP and fills at TP exactly (no spread)."""
    rows = [
        ("2024-01-02 10:00", 100, 100, 100, 100),
        ("2024-01-02 10:01", 100, 105, 100, 102),  # entry at 102
        ("2024-01-02 10:02", 102, 110, 100, 105),  # High=110 >= TP=108 -> fill 108
    ]
    eng = _engine(app, spread=2.0)
    eng.load_data(_df(rows))
    eng.load_strategy(BuyOnceAtOpen(tp=108.0, sl=90.0))

    for _ in range(3):
        eng.step()

    assert len(eng.trades) == 1
    t = eng.trades[0]
    assert t.exit_reason == "TP"
    assert t.exit_price == pytest.approx(108.0)
    # PnL points: 108 - 102 = 6
    assert t.pnl_points == pytest.approx(6.0)


def test_long_tp_not_triggered_when_high_below_tp(app):
    """Bid model: long TP uses raw Bid High (no spread). High=107 < TP=108 -> no trigger."""
    rows = [
        ("2024-01-02 10:00", 100, 100, 100, 100),
        ("2024-01-02 10:01", 100, 105, 100, 102),
        ("2024-01-02 10:02", 102, 107, 100, 105),  # High=107 < TP=108
        ("2024-01-02 10:03", 105, 107, 100, 105),
    ]
    eng = _engine(app, spread=2.0)
    eng.load_data(_df(rows))
    eng.load_strategy(BuyOnceAtOpen(tp=108.0, sl=90.0))

    for _ in range(4):
        eng.step()

    assert len(eng.trades) == 0
    assert eng.position is not None
