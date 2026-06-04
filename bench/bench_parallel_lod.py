"""Benchmark LOD pyramid build — sequential vs parallel.

Tests the speedup from parallelizing LOD level construction across
multiple threads.
"""

from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import time
import numpy as np
import pandas as pd
from concurrent.futures import ThreadPoolExecutor


def bench_lod_pyramid(n_bars=1_000_000, repeats=5):
    """Benchmark LOD pyramid build performance.

    Args:
        n_bars: Dataset size
        repeats: Benchmark iterations

    Returns:
        dict with timing breakdown
    """
    # Generate synthetic OHLC data
    time_arr = pd.date_range("2024-01-01", periods=n_bars, freq="1min")
    close = 100.0 + np.cumsum(np.random.randn(n_bars) * 0.5)
    high = close + np.abs(np.random.randn(n_bars) * 0.5)
    low = close - np.abs(np.random.randn(n_bars) * 0.5)
    open_ = close - np.random.randn(n_bars) * 0.3

    df = pd.DataFrame({
        "time": time_arr,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": 1.0,
    })

    from engine.data.lod_pyramid import LodPyramid

    # Benchmark: Sequential build (current implementation)
    sequential_times = []
    for _ in range(repeats):
        t0 = time.perf_counter()
        pyramid = LodPyramid.from_base(df, base_tf="1m")
        t1 = time.perf_counter()
        sequential_times.append((t1 - t0) * 1000)

    # Benchmark: Parallel build (proposed optimization)
    parallel_times = []
    for _ in range(repeats):
        t0 = time.perf_counter()
        pyramid = _build_pyramid_parallel(df, base_tf="1m")
        t1 = time.perf_counter()
        parallel_times.append((t1 - t0) * 1000)

    return {
        "n_bars": n_bars,
        "repeats": repeats,
        "sequential_ms": {
            "min": min(sequential_times),
            "max": max(sequential_times),
            "mean": sum(sequential_times) / len(sequential_times),
            "median": sorted(sequential_times)[len(sequential_times) // 2],
        },
        "parallel_ms": {
            "min": min(parallel_times),
            "max": max(parallel_times),
            "mean": sum(parallel_times) / len(parallel_times),
            "median": sorted(parallel_times)[len(parallel_times) // 2],
        },
        "speedup": sorted(sequential_times)[len(sequential_times) // 2] / sorted(parallel_times)[len(parallel_times) // 2],
    }


def _build_pyramid_parallel(df, base_tf="1m", levels=("1m", "5m", "15m", "1h", "1d")):
    """Parallel LOD pyramid build using ThreadPoolExecutor."""
    from engine.data.lod_pyramid import LodPyramid, _to_polars_or_none, _resample_polars, _resample_pandas

    levels_list = list(levels)
    if base_tf not in levels_list:
        levels_list = [base_tf, *levels_list]

    out = {base_tf: df}

    if len(df) == 0:
        for tf in levels_list:
            out.setdefault(tf, df.copy())
        return LodPyramid(levels=out, base_tf=base_tf)

    pl_base = _to_polars_or_none(df)

    # Parallel resample for non-base levels
    non_base_levels = [tf for tf in levels_list if tf != base_tf]

    def _resample_level(tf):
        if pl_base is not None:
            resampled = _resample_polars(pl_base, tf)
            if resampled is not None:
                return tf, resampled
        return tf, _resample_pandas(df, tf)

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(_resample_level, tf) for tf in non_base_levels]
        for future in futures:
            tf, resampled = future.result()
            out[tf] = resampled

    return LodPyramid(levels=out, base_tf=base_tf)


def main():
    print("=" * 70)
    print("Sprint 2.2: Parallel LOD Pyramid Benchmark")
    print("=" * 70)

    # Test 1: Small dataset
    print("\n[1] Small dataset (10K bars)")
    result = bench_lod_pyramid(n_bars=10_000, repeats=10)
    print(f"  Sequential:  {result['sequential_ms']['median']:.1f} ms")
    print(f"  Parallel:    {result['parallel_ms']['median']:.1f} ms")
    print(f"  Speedup:     {result['speedup']:.2f}x")

    # Test 2: Medium dataset
    print("\n[2] Medium dataset (100K bars)")
    result = bench_lod_pyramid(n_bars=100_000, repeats=5)
    print(f"  Sequential:  {result['sequential_ms']['median']:.1f} ms")
    print(f"  Parallel:    {result['parallel_ms']['median']:.1f} ms")
    print(f"  Speedup:     {result['speedup']:.2f}x")

    # Test 3: Large dataset (grail target)
    print("\n[3] Large dataset (1M bars)")
    result = bench_lod_pyramid(n_bars=1_000_000, repeats=3)
    print(f"  Sequential:  {result['sequential_ms']['median']:.1f} ms")
    print(f"  Parallel:    {result['parallel_ms']['median']:.1f} ms")
    print(f"  Speedup:     {result['speedup']:.2f}x")

    print("\n" + "=" * 70)
    print("[OK] Parallel LOD benchmark complete!")
    print("=" * 70)


if __name__ == "__main__":
    main()
