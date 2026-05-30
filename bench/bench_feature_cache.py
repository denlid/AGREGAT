"""Benchmark feature-cache gain on EMA crossover sweep (Roadmap v2 / S1).

Measures the speedup when sweeping execution params (sl_pts, tp_pts) while
indicator params (fast_ema, slow_ema) stay fixed — the target workload for
feature-cache. Compares two realistic optimizer flows:

1. **without_feature_cache**: strategy without the contract (monolithic
   vectorized_signals) run through grid_search + vectorized_runner.
2. **with_feature_cache**: EMACrossoverFeatureCached (implements contract)
   run through the same flow.

Expected gain: 1.5–2x (with_cache vs without) on indicator-heavy strategies
where EMA computation dominates signals generation.
"""

from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

from bench._timer import time_call
from engine.execution.execution import ExecutionModel
from engine.models.models import AssetConfig
from engine.optimization import grid_search, make_vectorized_runner
from engine.strategy.context import Strategy
from strategies.ema_crossover_cached import EMACrossoverFeatureCached


class EMACrossoverNoCache(Strategy):
    """Same logic as EMACrossoverFeatureCached but WITHOUT feature-cache contract.

    This is the baseline: monolithic vectorized_signals that recomputes EMAs
    on every call. Represents strategies that haven't been refactored for S1.
    """

    PARAMS = EMACrossoverFeatureCached.PARAMS

    def vectorized_signals(self, df):
        # Inline the full logic — no split, no reuse.
        n = len(df)
        col = "close" if "close" in df.columns else "Close"
        close = df[col].to_numpy(dtype=float)

        fast_period = int(self.params["fast_ema"])
        slow_period = int(self.params["slow_ema"])

        fast_ema = pd.Series(close).ewm(span=fast_period, adjust=False).mean().to_numpy()
        slow_ema = pd.Series(close).ewm(span=slow_period, adjust=False).mean().to_numpy()

        sig = np.zeros(n, dtype=int)
        for i in range(1, n):
            if fast_ema[i - 1] <= slow_ema[i - 1] and fast_ema[i] > slow_ema[i]:
                sig[i] = +1
            elif fast_ema[i - 1] >= slow_ema[i - 1] and fast_ema[i] < slow_ema[i]:
                sig[i] = -1

        sl_pts = float(self.params["sl_pts"])
        tp_pts = float(self.params["tp_pts"])

        return pd.DataFrame(
            {
                "signal": sig,
                "sl_points": np.full(n, sl_pts, dtype=float),
                "tp_points": np.full(n, tp_pts, dtype=float),
            },
            index=df.index,
        )


def _df(n: int) -> pd.DataFrame:
    """Synthetic price with trend + noise so EMA crossovers happen."""
    idx = pd.date_range("2024-01-02 10:00", periods=n, freq="1min")
    trend = 100.0 + 10.0 * np.sin(np.arange(n) / 50.0)
    noise = np.cumsum(np.random.default_rng(42).normal(0, 0.5, n))
    close = trend + noise
    high = close + np.abs(np.random.default_rng(43).normal(0.5, 0.2, n))
    low = close - np.abs(np.random.default_rng(44).normal(0.5, 0.2, n))
    open_ = close - np.random.default_rng(45).normal(0, 0.3, n)
    return pd.DataFrame(
        {
            "time": idx,
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
        close_on_weekend=False,
        enforce_trading_hours=False,
    )


def measure(n_bars: int = 50_000, repeats: int = 3) -> dict:
    """Measure feature-cache gain on a realistic grid_search sweep.

    Args:
        n_bars: Dataset size.
        repeats: Benchmark repeats for median timing.
    """
    df = _df(n_bars)
    asset = _asset()
    em = ExecutionModel("Optimistic")

    # Sweep sl_pts/tp_pts while fast_ema/slow_ema fixed — the target workload.
    grid = {"sl_pts": [30.0, 40.0, 50.0], "tp_pts": [60.0, 80.0, 100.0, 120.0]}
    combos = 3 * 4

    def _without_cache() -> None:
        runner = make_vectorized_runner(df, EMACrossoverNoCache(), asset=asset, execution_model=em)
        grid_search(runner, grid, score_key="total_return_pct")

    def _with_cache() -> None:
        runner = make_vectorized_runner(df, EMACrossoverFeatureCached(), asset=asset, execution_model=em)
        grid_search(runner, grid, score_key="total_return_pct")

    without = time_call(_without_cache, repeats=repeats)
    with_cache = time_call(_with_cache, repeats=repeats)

    return {
        "bars": int(n_bars),
        "combos": int(combos),
        "repeats": int(repeats),
        "without_feature_cache": without,
        "with_feature_cache": with_cache,
        "speedup": float(without["median_s"] / with_cache["median_s"])
        if with_cache["median_s"] > 0
        else 0.0,
    }


def main() -> dict:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bars", type=int, default=50_000)
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    result = measure(n_bars=args.bars, repeats=args.repeats)
    print(result)
    return result


if __name__ == "__main__":
    main()

