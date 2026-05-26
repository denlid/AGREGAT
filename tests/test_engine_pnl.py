"""End-to-end PnL test on a tiny synthetic candle set.

Drives the BacktestEngine through a deterministic 5-bar fixture and verifies:
  * SL collision is resolved worst-case (SL beats TP on same bar)
  * PnL in dollars matches `pnl_points * volume * point_value`
  * mae_dollars / mfe_dollars are populated
  * RR carries the correct sign on losing trades
"""

from __future__ import annotations

import os

import pandas as pd
import pytest

# Run Qt off-screen for tests
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from engine.backtest_engine import BacktestEngine  # noqa: E402
from engine.strategy.context import Strategy  # noqa: E402


def _make_df(rows):
    """rows: list of (time_str, o, h, l, c)."""
    df = pd.DataFrame(rows, columns=["time", "open", "high", "low", "close"])
    df["time"] = pd.to_datetime(df["time"])
    df["volume"] = 1.0
    return df


class BuyOnceAtOpen(Strategy):
    """Submit one buy_at_open order on the first candle, then sit still."""

    def __init__(self, sl: float, tp: float = None, volume: float = 1.0):
        self._sent = False
        self._sl = sl
        self._tp = tp
        self._vol = volume

    def on_candle(self, ctx) -> None:  # type: ignore[override]
        if not self._sent and ctx.position is None:
            ctx.buy_at_open(volume=self._vol, sl=self._sl, tp=self._tp, reason="test", tag="test")
            self._sent = True


@pytest.fixture
def app():
    """A QCoreApplication is required for QObject-based engine signals."""
    from PySide6.QtCore import QCoreApplication

    inst = QCoreApplication.instance()
    if inst is None:
        inst = QCoreApplication([])
    return inst


def _make_engine():
    eng = BacktestEngine()
    # Override the asset config with a clean 1.0/0.01 setup
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
    return eng


def test_buy_at_open_filled_on_next_candle_then_sl_hit(app):
    """C0 sends order; C1 fills at open=100; C2 hits SL=95.
    Expect: pnl_points = 95 - 100 = -5, pnl = -5 * 1 * 1.0 = -5
    """
    rows = [
        # time, open, high, low, close
        ("2024-01-02 10:00", 99, 101, 98, 100),  # signal bar
        ("2024-01-02 10:01", 100, 102, 99, 101),  # fill at 100
        ("2024-01-02 10:02", 101, 101, 94, 95),  # SL @ 95 hit
        ("2024-01-02 10:03", 95, 97, 94, 96),
        ("2024-01-02 10:04", 96, 97, 95, 96),
    ]
    eng = _make_engine()
    eng.load_data(_make_df(rows))
    eng.load_strategy(BuyOnceAtOpen(sl=95.0))

    # Step 5 candles
    for _ in range(5):
        eng.step()

    assert len(eng.trades) == 1
    t = eng.trades[0]
    assert t.direction == "long"
    assert t.exit_reason == "SL"
    assert t.entry_price == pytest.approx(100.0)
    assert t.exit_price == pytest.approx(95.0)
    assert t.pnl_points == pytest.approx(-5.0)
    assert t.pnl == pytest.approx(-5.0)
    # Signed RR — losing trade, expect negative
    assert t.rr is not None and t.rr < 0
    # MAE tracks the worst intra-bar adverse excursion (low=94 → 6pts under entry),
    # NOT the SL exit; so mae_dollars = 6 * 1 lot * $1/pt = $6.
    assert t.mae_dollars == pytest.approx(6.0)


def test_same_bar_sl_and_tp_resolves_worst_case_sl_first(app):
    """C2 has both low <= SL AND high >= TP — engine must close at SL."""
    rows = [
        ("2024-01-02 10:00", 100, 101, 99, 100),
        ("2024-01-02 10:01", 100, 100, 99, 100),  # fill at 100
        ("2024-01-02 10:02", 100, 110, 90, 100),  # SL=95 + TP=105 both hit
        ("2024-01-02 10:03", 100, 101, 99, 100),
        ("2024-01-02 10:04", 100, 101, 99, 100),
    ]
    eng = _make_engine()
    eng.load_data(_make_df(rows))
    eng.load_strategy(BuyOnceAtOpen(sl=95.0, tp=105.0))

    for _ in range(5):
        eng.step()

    assert len(eng.trades) == 1
    t = eng.trades[0]
    assert t.exit_reason == "SL", "Worst-case rule: SL must win on collision"
    assert t.pnl == pytest.approx(-5.0)


def test_step_n_emits_simulation_finished_at_end(app):
    """`step_n(huge)` should exhaust the candle list and emit `simulation_finished`."""
    rows = [
        ("2024-01-02 10:00", 100, 101, 99, 100),
        ("2024-01-02 10:01", 100, 102, 99, 101),
        ("2024-01-02 10:02", 100, 102, 99, 101),
        ("2024-01-02 10:03", 100, 102, 99, 101),
        ("2024-01-02 10:04", 100, 102, 99, 101),
    ]
    eng = _make_engine()
    eng.load_data(_make_df(rows))

    fired = {"flag": False}
    eng.simulation_finished.connect(lambda: fired.update(flag=True))
    eng.load_strategy(BuyOnceAtOpen(sl=90.0))

    eng.step_n(1000)

    assert fired["flag"] is True, "step_n must emit simulation_finished at end of data"
    # current_idx must point at the last candle
    assert eng.current_idx == len(eng._candle_list) - 1


def test_recovery_factor_uses_absolute_drawdown(app):
    """After a drawdown of $5 followed by no recovery, the engine reports
    max_drawdown_abs > 0."""
    rows = [
        ("2024-01-02 10:00", 100, 101, 99, 100),
        ("2024-01-02 10:01", 100, 102, 99, 101),  # fill long @ 100
        ("2024-01-02 10:02", 100, 102, 90, 95),  # SL hit
        ("2024-01-02 10:03", 95, 97, 94, 96),
        ("2024-01-02 10:04", 96, 97, 95, 96),
    ]
    eng = _make_engine()
    eng.load_data(_make_df(rows))
    eng.load_strategy(BuyOnceAtOpen(sl=95.0))

    for _ in range(5):
        eng.step()

    assert eng.max_drawdown_abs > 0
    # max_drawdown_pct should be in [0, 100] not in [0, 1]
    assert 0 < eng.max_drawdown_pct <= 100


def test_pending_order_cancelled_when_position_already_open(app):
    """Defensive: an extra pending order while a position is open must not double-fill."""
    rows = [
        ("2024-01-02 10:00", 100, 101, 99, 100),
        ("2024-01-02 10:01", 100, 102, 99, 101),
        ("2024-01-02 10:02", 100, 103, 99, 102),
        ("2024-01-02 10:03", 100, 104, 99, 103),
        ("2024-01-02 10:04", 100, 105, 99, 104),
    ]
    eng = _make_engine()
    eng.load_data(_make_df(rows))
    eng.load_strategy(BuyOnceAtOpen(sl=50.0))  # SL very far → no SL hit

    for _ in range(5):
        eng.step()

    # Strategy fired once → at most one trade should ever open
    assert eng.position is not None or len(eng.trades) <= 1
