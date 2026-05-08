"""SL/TP collision tests for SHORT positions with spread (Bid model).

For short positions the chart is Bid-priced but exit fills at the Ask
(= Bid + spread). The engine must therefore convert the candle's High/Low
to the Ask side before checking SL/TP triggers:

    SL_short hit  when (High + spread) >= SL
    TP_short hit  when (Low  + spread) <= TP

The short *entry* itself fills at the Bid (= chart open), without any
spread adjustment, since the user is selling and the Bid is what the
chart shows.
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


class SellOnceAtOpen(Strategy):
    """Submit one sell_at_open order on the first candle, then sit still."""

    def __init__(self, sl=None, tp=None, volume=1.0):
        self._sent = False
        self._sl = sl
        self._tp = tp
        self._vol = volume

    def on_candle(self, ctx) -> None:  # type: ignore[override]
        if not self._sent and ctx.position is None:
            ctx.sell_at_open(volume=self._vol, sl=self._sl, tp=self._tp, reason="t", tag="t")
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


def test_short_sl_triggered_by_ask_when_high_alone_is_below_sl(app):
    """High = 103 alone would NOT trigger SL=104, but High + spread=2 = 105 does.

    With spread=2, Ask_high = High + 2. SL=104 must trigger because
    Ask_high (=105) >= SL (=104), even though raw High (=103) is below.
    """
    rows = [
        # signal bar
        ("2024-01-02 10:00", 100, 100, 99, 100),
        # fill at open=100 (Bid model: short entry = Bid = 100, no spread)
        ("2024-01-02 10:01", 100, 101, 99, 100),
        # bid High=103, Ask_high=105 → triggers SL=104
        ("2024-01-02 10:02", 100, 103, 99, 102),
        ("2024-01-02 10:03", 102, 103, 99, 100),
        ("2024-01-02 10:04", 100, 103, 99, 100),
    ]
    eng = _engine(app, spread=2.0)
    eng.load_data(_df(rows))
    eng.load_strategy(SellOnceAtOpen(sl=104.0))

    for _ in range(5):
        eng.step()

    assert len(eng.trades) == 1
    t = eng.trades[0]
    assert t.direction == "short"
    assert t.exit_reason == "SL"
    assert t.exit_price == pytest.approx(104.0)


def test_short_sl_not_triggered_when_ask_below_sl(app):
    """High = 102 with spread=1 → Ask_high = 103 < SL=104. No trigger."""
    rows = [
        ("2024-01-02 10:00", 100, 100, 99, 100),
        ("2024-01-02 10:01", 100, 101, 99, 100),  # fill
        ("2024-01-02 10:02", 100, 102, 99, 101),  # Ask_high=103 < SL=104
        ("2024-01-02 10:03", 101, 102, 99, 100),
        ("2024-01-02 10:04", 100, 102, 99, 100),
    ]
    eng = _engine(app, spread=1.0)
    eng.load_data(_df(rows))
    eng.load_strategy(SellOnceAtOpen(sl=104.0))

    for _ in range(5):
        eng.step()

    # Position must remain open — SL was NOT triggered
    assert len(eng.trades) == 0
    assert eng.position is not None


def test_short_tp_only_triggers_when_ask_low_below_tp(app):
    """Low=96 with spread=2 → Ask_low = 98. TP=97 NOT triggered.

    Bid Low=96 alone would have triggered TP=97 (the legacy bug),
    but the Ask side (Low+spread=98) is above 97, so TP must NOT fire.
    """
    rows = [
        ("2024-01-02 10:00", 100, 100, 99, 100),
        ("2024-01-02 10:01", 100, 101, 99, 100),  # fill
        ("2024-01-02 10:02", 100, 101, 96, 100),  # Bid Low=96, Ask_low=98 > TP=97
        ("2024-01-02 10:03", 100, 101, 99, 100),
        ("2024-01-02 10:04", 100, 101, 99, 100),
    ]
    eng = _engine(app, spread=2.0)
    eng.load_data(_df(rows))
    eng.load_strategy(SellOnceAtOpen(tp=97.0))

    for _ in range(5):
        eng.step()

    # Position still open; spread protected user from premature TP
    assert len(eng.trades) == 0
    assert eng.position is not None


def test_short_tp_triggered_when_ask_low_reaches_tp(app):
    """Low=95 + spread=2 → Ask_low = 97. TP=97 triggers."""
    rows = [
        ("2024-01-02 10:00", 100, 100, 99, 100),
        ("2024-01-02 10:01", 100, 101, 99, 100),  # fill
        ("2024-01-02 10:02", 100, 101, 95, 98),  # Ask_low=97 == TP=97
        ("2024-01-02 10:03", 100, 101, 99, 100),
        ("2024-01-02 10:04", 100, 101, 99, 100),
    ]
    eng = _engine(app, spread=2.0)
    eng.load_data(_df(rows))
    eng.load_strategy(SellOnceAtOpen(tp=97.0))

    for _ in range(5):
        eng.step()

    assert len(eng.trades) == 1
    t = eng.trades[0]
    assert t.exit_reason == "TP"
    assert t.exit_price == pytest.approx(97.0)


def test_short_optimistic_profile_has_zero_spread(app):
    """Optimistic profile zeros the spread, so Ask == Bid."""
    rows = [
        ("2024-01-02 10:00", 100, 100, 99, 100),
        ("2024-01-02 10:01", 100, 101, 99, 100),
        ("2024-01-02 10:02", 100, 104, 99, 102),  # raw High=104 == SL=104
        ("2024-01-02 10:03", 102, 103, 99, 100),
        ("2024-01-02 10:04", 100, 103, 99, 100),
    ]
    eng = _engine(app, profile="Optimistic", spread=2.0)
    eng.load_data(_df(rows))
    eng.load_strategy(SellOnceAtOpen(sl=104.0))

    for _ in range(5):
        eng.step()

    assert len(eng.trades) == 1
    assert eng.trades[0].exit_reason == "SL"


def test_long_sl_tp_unchanged_by_spread(app):
    """Phase 1 only adjusts shorts. Long triggers stay on raw Bid High/Low."""
    from tests.test_engine_pnl import BuyOnceAtOpen

    rows = [
        ("2024-01-02 10:00", 99, 101, 98, 100),
        ("2024-01-02 10:01", 100, 102, 99, 101),  # fill long at open
        ("2024-01-02 10:02", 101, 101, 94, 95),  # Low=94 ≤ SL=95
        ("2024-01-02 10:03", 95, 97, 94, 96),
        ("2024-01-02 10:04", 96, 97, 95, 96),
    ]
    eng = _engine(app, profile="Realistic", spread=2.0)
    eng.load_data(_df(rows))
    eng.load_strategy(BuyOnceAtOpen(sl=95.0))

    for _ in range(5):
        eng.step()

    assert len(eng.trades) == 1
    assert eng.trades[0].direction == "long"
    assert eng.trades[0].exit_reason == "SL"
    assert eng.trades[0].exit_price == pytest.approx(95.0)
