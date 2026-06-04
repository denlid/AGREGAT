"""Test Numba-path skipped-trades reconstruction (Roadmap v2 / S1.5 followup).

Numba revealed a pre-existing bug: the Numba inner loop returned
skipped_trades=[] while the Python path tracked TRADING_HOURS/WEEKEND/EOD
dropped fills. _reconstruct_skipped_trades() restores parity.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from engine.execution.execution import ExecutionModel
from engine.execution.vectorized_engine import run_vectorized
from engine.models.models import AssetConfig
from engine.strategy.context import Strategy


class LongOnceAtBar(Strategy):
    """Emit a single +1 long at bar `at`, fixed SL/TP."""

    def __init__(self, at: int, sl: float, tp: float, n: int):
        super().__init__()
        self._at, self._sl, self._tp, self._n = at, sl, tp, n

    def vectorized_signals(self, df):
        sig = np.zeros(self._n, dtype=int)
        if self._n:
            sig[self._at] = +1
        return pd.DataFrame(
            {
                "signal": sig,
                "sl_points": np.full(self._n, self._sl, dtype=float),
                "tp_points": np.full(self._n, self._tp, dtype=float),
            },
            index=df.index,
        )


def _asset(enforce_th: bool = False) -> AssetConfig:
    return AssetConfig(
        name="TEST", point_value=1.0, tick_size=1.0, buffer=0.0,
        max_lots_per_100k=10, currency="USD", min_lot=0.01, lot_step=0.01,
        spread_points=0.0, commission_per_lot=0.0,
        enforce_trading_hours=enforce_th,
    )


def test_skipped_trade_recorded_when_fill_bar_outside_hours():
    """Signal at last in-hours bar; fill bar is outside → TRADING_HOURS skip."""
    n = 8
    # Trading window 09:00-18:00. Signal at bar 4 (17:59 close), fill at bar 5
    # (18:00, outside) → dropped.
    times = pd.date_range("2024-01-02 17:55", periods=n, freq="1min")
    close = np.full(n, 100.0)
    df = pd.DataFrame(
        {
            "time": times,
            "open": close,
            "high": close + 0.5,
            "low": close - 0.5,
            "close": close,
            "volume": 1.0,
        }
    )
    strat = LongOnceAtBar(at=4, sl=5.0, tp=5.0, n=n)
    res = run_vectorized(df, strat, asset=_asset(enforce_th=True), execution_model=ExecutionModel("Optimistic"))

    assert len(res.trades) == 0, "trade must be skipped"
    th_skips = [s for s in res.skipped_trades if s.get("reason") == "TRADING_HOURS"]
    assert len(th_skips) == 1, f"expected 1 TRADING_HOURS skip, got {res.skipped_trades}"
    assert th_skips[0]["direction"] == "long"


def test_no_skips_when_trading_hours_not_enforced():
    """With enforce_trading_hours off, no skips regardless of bar times."""
    n = 8
    times = pd.date_range("2024-01-02 17:55", periods=n, freq="1min")
    close = np.full(n, 100.0)
    df = pd.DataFrame(
        {
            "time": times,
            "open": close,
            "high": close + 0.5,
            "low": close - 0.5,
            "close": close,
            "volume": 1.0,
        }
    )
    strat = LongOnceAtBar(at=4, sl=5.0, tp=5.0, n=n)
    res = run_vectorized(df, strat, asset=_asset(enforce_th=False), execution_model=ExecutionModel("Optimistic"))

    assert res.skipped_trades == [], "no skips when blocking disabled"


def test_normal_fill_produces_no_skip():
    """Signal mid-session with in-hours fill bar → trade fills, no skip."""
    n = 10
    times = pd.date_range("2024-01-02 10:00", periods=n, freq="1min")  # all in-hours
    close = np.full(n, 100.0)
    df = pd.DataFrame(
        {
            "time": times,
            "open": close,
            "high": close + 0.5,
            "low": close - 0.5,
            "close": close,
            "volume": 1.0,
        }
    )
    strat = LongOnceAtBar(at=2, sl=5.0, tp=5.0, n=n)
    res = run_vectorized(df, strat, asset=_asset(enforce_th=True), execution_model=ExecutionModel("Optimistic"))

    # Fill bar (bar 3) is in-hours → no TRADING_HOURS skip.
    th_skips = [s for s in res.skipped_trades if s.get("reason") == "TRADING_HOURS"]
    assert len(th_skips) == 0
