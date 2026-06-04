"""Perf-budget regression guard for vectorised trade materialisation.

History: ``Trade.__init__`` used to run ``pd.to_datetime(str)`` twice per
trade to cache its datetime64, costing ~480 µs/trade. A 5-year run (~98 k
trades) spent ~47 s of a ~48 s wall-clock total there — while the Numba
simulation itself took ~12 ms. Hoisting the datetime work out of the loop
and passing the datetime64 straight to ``Trade`` dropped that to ~5 µs/trade
(~50× on the full run).

This is a *regression* test, not a benchmark: the budget has ~10× headroom
over the optimised number so it won't flake on slow CI, but it fails loudly
if materialisation ever climbs back toward the per-trade-parse scale.
"""

from __future__ import annotations

import time

import numpy as np
import pandas as pd

from engine.execution.execution import ExecutionModel
from engine.execution.prepared_dataset import prepare_vectorized_dataset
from engine.execution.vectorized_engine import run_vectorized
from engine.models.models import AssetConfig


class _Strat:
    def vectorized_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        n = len(df)
        idx = np.arange(n)
        sig = np.zeros(n, dtype=int)
        sig[idx % 17 == 3] = 1
        sig[idx % 19 == 7] = -1
        return pd.DataFrame(
            {
                "signal": sig,
                "sl_points": np.full(n, 2.0),
                "tp_points": np.full(n, 4.0),
            },
            index=df.index,
        )


def _asset() -> AssetConfig:
    return AssetConfig(
        name="T",
        point_value=1.0,
        tick_size=0.01,
        buffer=0.0,
        max_lots_per_100k=10,
        currency="USD",
        min_lot=0.01,
        lot_step=0.01,
        spread_points=0.0,
        commission_per_lot=0.0,
        close_on_weekend=False,
        enforce_trading_hours=False,
    )


def _df(n_bars: int) -> pd.DataFrame:
    rng = np.random.default_rng(7)
    close = 100.0 + np.linspace(0.0, 30.0, n_bars) + rng.normal(0.0, 1.0, n_bars).cumsum()
    return pd.DataFrame(
        {
            "time": pd.date_range("2020-01-01", periods=n_bars, freq="min"),
            "open": np.r_[close[0], close[:-1]],
            "high": close + 1.0,
            "low": close - 1.0,
            "close": close,
            "volume": 1.0,
        }
    )


# 300k bars yields a few thousand trades — enough to amortise the JIT warmup
# and make the per-trade cost dominate, without making CI slow.
N_BARS = 300_000

# Optimised path is ~5 µs/trade; legacy per-trade-parse path was ~480 µs.
# 60 µs gives ~10× headroom over optimised while catching any return to the
# parse-in-the-loop regime.
PER_TRADE_BUDGET_MS = 0.060


def test_trade_materialize_per_trade_under_budget():
    df = _df(N_BARS)
    asset = _asset()
    em = ExecutionModel()
    prep = prepare_vectorized_dataset(df, asset=asset, execution_model=em)

    # Warm the Numba JIT so compilation time doesn't pollute the measurement.
    run_vectorized(df, _Strat(), asset=asset, execution_model=em, prepared=prep, skip_lookahead_check=True)

    res = run_vectorized(
        df, _Strat(), asset=asset, execution_model=em, prepared=prep, skip_lookahead_check=True
    )
    tc = res.perf["trade_count"]
    assert tc > 500, f"fixture should produce many trades, got {tc}"

    mat_ms = res.perf["trade_materialize_ms"]
    per_trade_ms = mat_ms / tc
    assert per_trade_ms < PER_TRADE_BUDGET_MS, (
        f"trade materialisation regressed: {per_trade_ms*1000:.1f} µs/trade "
        f"({mat_ms:.0f} ms / {tc} trades), budget {PER_TRADE_BUDGET_MS*1000:.0f} µs/trade. "
        "Did a per-trade pd.to_datetime / float(np_scalar) creep back into the loop?"
    )

    # The simulation itself must stay cheap relative to bar count, too.
    assert res.perf["numba_loop_ms"] < N_BARS / 1000.0  # < 1 µs/bar with headroom
