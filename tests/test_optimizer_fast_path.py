"""Parity + wiring tests for the optimizer fast path (Roadmap v2 / S0).

These cover the S0.1/S0.2 wire-up:
- ``BacktestCore.run_vectorized(optimizer_mode=True)`` returns summary-only.
- ``make_vectorized_runner`` reuses one PreparedDataset across a sweep and
  produces metrics identical to a full per-combo ``run_vectorized`` call.

The point of S0 is that no numbers change — only the cost does. So the core
assertion is parity: optimizer-mode summary == full-run summary, combo by combo.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from engine.execution.execution import ExecutionModel
from engine.execution.prepared_dataset import prepare_vectorized_dataset
from engine.execution.vectorized_engine import run_vectorized
from engine.models.models import AssetConfig
from engine.optimization import grid_search, make_vectorized_runner
from engine.strategy.context import Param, Strategy


def _df(n: int = 400) -> pd.DataFrame:
    # Deterministic sawtooth-ish price so signals actually trade.
    # Lowercase OHLC — the canonical in-engine column format (BacktestCore
    # binds df["open"]/["close"] directly).
    idx = np.arange(n)
    close = 100.0 + 5.0 * np.sin(idx / 7.0) + (idx % 11) * 0.1
    high = close + 1.0
    low = close - 1.0
    open_ = close - 0.25
    times = pd.date_range("2024-01-02 10:00", periods=n, freq="1min")
    return pd.DataFrame(
        {
            "time": times,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": 1.0,
        }
    )


def _asset() -> AssetConfig:
    return AssetConfig(
        name="TEST",
        point_value=1.0,
        tick_size=1.0,
        buffer=0.0,
        max_lots_per_100k=10,
        currency="USD",
        min_lot=0.01,
        lot_step=0.01,
        spread_points=0.0,
        commission_per_lot=0.0,
        enforce_trading_hours=False,
    )


class ThresholdStrategy(Strategy):
    """Trades when close crosses an MA; SL/TP are tunable params.

    Param-driven so a grid sweep produces genuinely different results per
    combo — exactly the optimizer workload S0 targets.
    """

    PARAMS = [
        Param(name="sl_pts", label="SL", kind="float", default=2.0),
        Param(name="tp_pts", label="TP", kind="float", default=4.0),
        Param(name="ma", label="MA", kind="int", default=10),
    ]

    def vectorized_signals(self, df):
        n = len(df)
        col = "close" if "close" in df.columns else "Close"
        close = df[col].to_numpy(dtype=float)
        ma_win = int(self.params["ma"])
        ma = pd.Series(close).rolling(ma_win, min_periods=1).mean().to_numpy()
        sig = np.zeros(n, dtype=int)
        sig[close > ma] = +1
        sig[close < ma] = -1
        return pd.DataFrame(
            {
                "signal": sig,
                "sl_points": np.full(n, float(self.params["sl_pts"])),
                "tp_points": np.full(n, float(self.params["tp_pts"])),
            },
            index=df.index,
        )


_GRID = {"sl_pts": [2.0, 3.0], "tp_pts": [4.0, 6.0], "ma": [5, 10]}


def _full_run_summary(df, params, asset, em):
    strat = ThresholdStrategy()
    strat.apply_params(params)
    res = run_vectorized(df, strat, asset=asset, execution_model=em)
    return res.summary


def test_optimizer_mode_returns_summary_only_no_trades():
    """BacktestCore.run_vectorized(optimizer_mode=True) skips materialization."""
    from engine.core.backtest_core import BacktestCore

    df, asset, em = _df(), _asset(), ExecutionModel("Optimistic")
    core = BacktestCore()
    core.load_data(df)
    core.asset_config = asset
    core.execution_model = em
    strat = ThresholdStrategy()
    strat.apply_params({"sl_pts": 2.0, "tp_pts": 4.0, "ma": 10})
    core.load_strategy(strat)

    res = core.run_vectorized(optimizer_mode=True)
    assert res is not None
    # summary present; engine state untouched (no trades replayed).
    assert "total_return_pct" in res.summary
    assert res.perf.get("materialize_trades") is False
    assert len(core.trades) == 0


def test_runner_parity_with_full_run_across_grid():
    """Every combo's optimizer-mode summary == a full run_vectorized summary."""
    df, asset, em = _df(), _asset(), ExecutionModel("Optimistic")
    runner = make_vectorized_runner(df, ThresholdStrategy(), asset=asset, execution_model=em)

    keys = list(_GRID.keys())
    import itertools

    for combo in itertools.product(*[_GRID[k] for k in keys]):
        params = dict(zip(keys, combo))
        fast = runner(params)
        full = _full_run_summary(df, params, asset, em)
        for key in ("total_return_pct", "final_equity", "n_trades", "peak_drawdown_pct"):
            assert fast[key] == pytest.approx(full[key], rel=1e-9, abs=1e-9), (
                f"mismatch on {key} for {params}: fast={fast[key]} full={full[key]}"
            )


def test_grid_search_runs_through_fast_path():
    """End-to-end: grid_search ranks combos via the reusing runner."""
    df, asset, em = _df(), _asset(), ExecutionModel("Optimistic")
    runner = make_vectorized_runner(df, ThresholdStrategy(), asset=asset, execution_model=em)
    result = grid_search(runner, _GRID, score_key="total_return_pct", higher_is_better=True)

    assert len(result.runs) == 2 * 2 * 2
    # Best run's score must equal the max total_return_pct across runs.
    best = result.best
    assert best is not None
    scores = [r.metrics["total_return_pct"] for r in result.runs]
    assert best.metrics["total_return_pct"] == pytest.approx(max(scores))


def test_runner_reuses_single_prepared_dataset():
    """The runner holds one PreparedDataset and does not rebuild per combo."""
    df, asset, em = _df(), _asset(), ExecutionModel("Optimistic")
    prep = prepare_vectorized_dataset(df, asset=asset, execution_model=em)
    runner = make_vectorized_runner(
        df, ThresholdStrategy(), asset=asset, execution_model=em, prepared=prep
    )
    # Same object identity across calls — proves no rebuild.
    assert runner.prepared is prep
    runner({"sl_pts": 2.0, "tp_pts": 4.0, "ma": 5})
    assert runner.prepared is prep
