"""Benchmark repeated vectorized runs with and without PreparedDataset reuse.

This harness is intentionally small and focused: it measures the hot-path
benefit of precomputing vectorized arrays/masks once and reusing them across
multiple runs of the same dataset.
"""

from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

from bench._timer import time_call
from engine.execution.execution import ExecutionModel
from engine.execution.prepared_dataset import prepare_vectorized_dataset
from engine.execution.vectorized_engine import run_vectorized
from engine.models.models import AssetConfig
from engine.strategy.context import Strategy


class _BenchStrategy(Strategy):
    def __init__(self, n: int) -> None:
        self._n = n

    def vectorized_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        idx = np.arange(self._n)
        sig = np.zeros(self._n, dtype=int)
        sig[idx % 17 == 3] = 1
        sig[idx % 19 == 7] = -1
        return pd.DataFrame(
            {
                "signal": sig,
                "sl_points": np.full(self._n, 2.0, dtype=float),
                "tp_points": np.full(self._n, 4.0, dtype=float),
            },
            index=df.index,
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


def _df(n_bars: int) -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=n_bars, freq="min")
    base = 100.0 + np.cumsum(np.sin(np.arange(n_bars) / 25.0) * 0.1)
    df = pd.DataFrame(
        {
            "time": idx,
            "Open": base,
            "High": base + 1.0,
            "Low": base - 1.0,
            "Close": base + np.cos(np.arange(n_bars) / 30.0) * 0.2,
            "Volume": 1.0,
        }
    )
    return df


def measure(n_bars: int = 100_000, runs: int = 10, repeats: int = 3) -> dict:
    df = _df(n_bars)
    asset = _asset()
    em = ExecutionModel("Optimistic")
    strat = _BenchStrategy(len(df))
    prepared = prepare_vectorized_dataset(df, asset=asset, execution_model=em)

    def _baseline_full() -> object:
        # Pre-S0 optimizer cost: full Trade materialization, equity curve,
        # and no PreparedDataset reuse — what every sweep combo used to pay.
        for _ in range(runs):
            run_vectorized(
                df,
                strat,
                asset=asset,
                execution_model=em,
            )
        return None

    def _plain() -> object:
        for _ in range(runs):
            run_vectorized(
                df,
                strat,
                asset=asset,
                execution_model=em,
                summary_only=True,
                compact_result=True,
                include_equity_curve=False,
            )
        return None

    def _reused() -> object:
        for _ in range(runs):
            run_vectorized(
                df,
                strat,
                asset=asset,
                execution_model=em,
                prepared=prepared,
                summary_only=True,
                compact_result=True,
                include_equity_curve=False,
            )
        return None

    baseline = time_call(_baseline_full, repeats=repeats)
    plain = time_call(_plain, repeats=repeats)
    reused = time_call(_reused, repeats=repeats)
    return {
        "bars": int(n_bars),
        "runs": int(runs),
        "repeats": int(repeats),
        "prepared_dataset": {
            "dataset_key": prepared.dataset_key,
            "memory_mb": prepared.memory_mb,
        },
        "baseline_full": baseline,
        "summary_only": plain,
        "prepared_reuse": reused,
        # Real S0 win: full-materialize baseline vs summary_only + reuse.
        "speedup_summary_only": float(baseline["median_s"] / plain["median_s"])
        if plain["median_s"] > 0
        else 0.0,
        "speedup_end_to_end": float(baseline["median_s"] / reused["median_s"])
        if reused["median_s"] > 0
        else 0.0,
        "speedup_reuse_only": float(plain["median_s"] / reused["median_s"])
        if reused["median_s"] > 0
        else 0.0,
    }


def main() -> dict:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bars", type=int, default=100_000)
    parser.add_argument("--runs", type=int, default=10)
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    result = measure(n_bars=args.bars, runs=args.runs, repeats=args.repeats)
    print(result)
    return result


if __name__ == "__main__":
    main()
