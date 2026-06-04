"""Benchmark chart redraw speedup from array-first snapshot (Roadmap v2 / S3).

Measures the speedup of the redraw hot path after replacing DataFrame.copy() +
reset_index() with zero-copy ChartRenderSnapshot. Simulates the redraw cycle
without full GUI initialization.

Expected gain: 2–3x on chart prep-side (DataFrame overhead eliminated).
"""

from __future__ import annotations

import argparse
import time

import numpy as np
import pandas as pd

from bench._timer import time_call
from engine.core.backtest_core import BacktestCore
from engine.models.models import AssetConfig
from gui.chart_tab.render_snapshot import ChartRenderSnapshot


def _synthetic_data(n: int) -> pd.DataFrame:
    """Generate synthetic OHLC data for benchmarking."""
    idx = pd.date_range("2024-01-02 10:00", periods=n, freq="1min")
    close = 100.0 + np.cumsum(np.random.default_rng(42).normal(0, 0.5, n))
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


def _baseline_dataframe_path(df: pd.DataFrame, idx: int, repeats: int) -> None:
    """Simulate the old DataFrame.copy() + reset_index() hot path."""
    for _ in range(repeats):
        # Old path: full copy + tail cap + reset_index.
        res_df = df.iloc[0 : idx + 1].copy()
        if len(res_df) > 5_000:
            tail = res_df.iloc[-5_000:].reset_index(drop=True)
            res_df = tail

        # Simulate _build_chart_arrays work.
        n = len(res_df)
        x = np.arange(n, dtype=np.float64)
        cl = res_df["close"].to_numpy(dtype=np.float64, copy=False)
        o = res_df["open"].to_numpy(dtype=np.float64, copy=False)
        l = res_df["low"].to_numpy(dtype=np.float64, copy=False)
        h = res_df["high"].to_numpy(dtype=np.float64, copy=False)
        candle_arr = np.column_stack((x, o, cl, l, h))
        _ = candle_arr  # Simulate usage


def _snapshot_path(
    time: np.ndarray,
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    idx: int,
    repeats: int,
) -> None:
    """Simulate the new ChartRenderSnapshot zero-copy path."""
    for _ in range(repeats):
        # New path: zero-copy slice + snapshot build.
        start = 0
        end = idx + 1
        if end - start > 5_000:
            start = end - 5_000

        snapshot = ChartRenderSnapshot.from_engine_arrays(
            time=time,
            open_=open_,
            high=high,
            low=low,
            close=close,
            start=start,
            end=end,
            active_tf="1m",
        )

        # Simulate candle_array() usage.
        candle_arr = snapshot.candle_array()
        _ = candle_arr


def measure(n_bars: int = 100_000, repeats_per_run: int = 100, bench_repeats: int = 3) -> dict:
    """Measure redraw hot path speedup.

    Args:
        n_bars: Dataset size.
        repeats_per_run: Number of redraw cycles per benchmark run (simulates
            rapid pan/zoom/step interactions).
        bench_repeats: Benchmark repeats for median timing.
    """
    df = _synthetic_data(n_bars)
    idx = n_bars - 1  # Simulate being at the end of the dataset.

    # Extract numpy arrays (what BacktestCore holds).
    time = df["time"].to_numpy()
    open_ = df["open"].to_numpy(dtype=np.float64)
    high = df["high"].to_numpy(dtype=np.float64)
    low = df["low"].to_numpy(dtype=np.float64)
    close = df["close"].to_numpy(dtype=np.float64)

    def _baseline() -> None:
        _baseline_dataframe_path(df, idx, repeats_per_run)

    def _snapshot() -> None:
        _snapshot_path(time, open_, high, low, close, idx, repeats_per_run)

    baseline = time_call(_baseline, repeats=bench_repeats)
    snapshot = time_call(_snapshot, repeats=bench_repeats)

    return {
        "bars": int(n_bars),
        "repeats_per_run": int(repeats_per_run),
        "bench_repeats": int(bench_repeats),
        "baseline_dataframe": baseline,
        "snapshot_zero_copy": snapshot,
        "speedup": float(baseline["median_s"] / snapshot["median_s"])
        if snapshot["median_s"] > 0
        else 0.0,
    }


def main() -> dict:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bars", type=int, default=100_000)
    parser.add_argument("--repeats", type=int, default=100)
    parser.add_argument("--bench-repeats", type=int, default=3)
    args = parser.parse_args()
    result = measure(n_bars=args.bars, repeats_per_run=args.repeats, bench_repeats=args.bench_repeats)
    print(result)
    return result


if __name__ == "__main__":
    main()
