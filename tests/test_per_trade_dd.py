"""Tests for engine.analytics.per_trade_dd (Phase 4.1)."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from engine.analytics.per_trade_dd import (
    aggregate_mae_mfe,
    per_trade_drawdown,
    slice_bars_for_trade,
)


@dataclass
class _Trade:
    id: int = 1
    direction: str = "long"
    entry_price: float = 100.0
    exit_price: float = 102.0
    sl: float = 99.0
    pnl: float = 200.0
    volume: float = 1.0
    dollars_per_point: float = 100.0
    entry_bar_index: int = 0
    exit_bar_index: int = 5
    entry_time: str = ""
    exit_time: str = ""


def _bars(open_, high, low, close):
    return list(open_), list(high), list(low), list(close)


def test_per_trade_drawdown_long_basic_pnl_and_curves():
    """Long trade entered at 100; bars track winning trajectory."""
    o = [100, 101, 102, 101, 101.5, 102]
    h = [100.5, 101.5, 102.5, 101.5, 102.0, 102.5]
    low = [99.5, 100.5, 101.0, 100.5, 101.0, 101.5]
    c = [100.5, 101.0, 102.0, 101.0, 101.5, 102.0]
    res = per_trade_drawdown(_Trade(), o, h, low, c)
    # MFE = 102.5 - 100 = 2.5 * 100 = 250 ; MAE = (99.5 - 100) * 100 = -50
    assert abs(res.mfe - 250.0) < 1e-6
    assert abs(res.mae + 50.0) < 1e-6
    # Risk = 1 point × $100 = $100; mae_r = -50/100 = -0.5
    assert abs(res.mae_r + 0.5) < 1e-6
    assert abs(res.mfe_r - 2.5) < 1e-6
    # Curves are bar-aligned
    assert res.pnl_curve.shape == (6,)
    assert res.drawdown_curve.shape == (6,)


def test_per_trade_drawdown_short_swaps_mae_mfe_sources():
    """For shorts MAE comes from highs, MFE from lows."""
    t = _Trade(direction="short", entry_price=100.0, exit_price=98.0, sl=101.0)
    o = [100, 100.5, 100, 99.5, 99]
    h = [100.5, 101.0, 100.0, 99.5, 99.5]
    low = [99.5, 99.5, 99.0, 98.5, 98.0]
    c = [100, 100, 99.5, 99.0, 98.5]
    res = per_trade_drawdown(t, o, h, low, c)
    # Short MFE on the lowest low: (100-98) = 2 * 100 = 200
    assert abs(res.mfe - 200.0) < 1e-6
    # Short MAE on the highest high: (100-101) = -1 * 100 = -100
    assert abs(res.mae + 100.0) < 1e-6


def test_per_trade_drawdown_finds_max_dd_bar_and_recovery():
    """Equity peaks then dips; recovery_bar must be the first bar that
    reclaims the pre-DD peak."""
    # Long, +100/bar profile then dips -200, then recovers
    o = [100, 100, 100, 100, 100, 100]
    h = [100.5, 102, 103, 102, 101, 104]
    low = [99.5, 101, 102, 100, 100, 102]
    c = [100, 102, 103, 100, 101, 104]  # +0, +200, +300, 0, +100, +400
    res = per_trade_drawdown(_Trade(), o, h, low, c)
    assert res.max_dd < 0
    # Peak was at bar 2 (+300); DD reaches its minimum at bar 3 (-300).
    assert res.max_dd_bar == 3
    # Recovery: first bar after 3 with pnl >= 300 → bar 5 (+400).
    assert res.recovery_bar == 5


def test_per_trade_drawdown_handles_empty_window():
    res = per_trade_drawdown(_Trade(), [], [], [], [])
    assert res.pnl_curve.size == 0
    assert res.mae == 0.0 and res.mfe == 0.0
    assert res.recovery_bar is None


def test_per_trade_drawdown_no_sl_returns_none_r_multiples():
    t = _Trade(sl=0.0)  # No SL → no risk-dollars → mae_r/mfe_r is None
    res = per_trade_drawdown(t, [100], [100.5], [99.5], [100.5])
    assert res.mae_r is None and res.mfe_r is None


def test_slice_bars_for_trade_with_padding():
    df = pd.DataFrame(
        {
            "open": np.arange(100.0, 200.0),
            "high": np.arange(100.5, 200.5),
            "low": np.arange(99.5, 199.5),
            "close": np.arange(100.0, 200.0),
        }
    )
    t = _Trade(entry_bar_index=20, exit_bar_index=30)
    win, eb, xb = slice_bars_for_trade(df, t, pad_before=5, pad_after=5)
    # Window is iloc[15:36] → 21 rows (entry 20 ± 5, plus inclusive exit at 30 + 5).
    assert len(win) == 21
    assert eb == 5 and xb == 15


def test_aggregate_mae_mfe_returns_one_row_per_trade():
    df = pd.DataFrame(
        {
            "open": [100.0] * 10,
            "high": [101.0] * 10,
            "low": [99.0] * 10,
            "close": [100.5] * 10,
        }
    )
    trades = [
        _Trade(id=1, entry_bar_index=0, exit_bar_index=4),
        _Trade(id=2, entry_bar_index=5, exit_bar_index=9),
    ]
    out = aggregate_mae_mfe(trades, df)
    assert len(out) == 2
    ids = {row[0] for row in out}
    assert ids == {1, 2}
