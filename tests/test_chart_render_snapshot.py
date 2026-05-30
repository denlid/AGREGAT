"""Tests for ChartRenderSnapshot (Roadmap v2 / S3).

Verifies that the array-first snapshot correctly replaces the DataFrame.copy()
hot path without changing behavior.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from gui.chart_tab.render_snapshot import ChartRenderSnapshot


def test_snapshot_from_engine_arrays_zero_copy():
    """Snapshot slices are views into source arrays, not copies."""
    n = 1000
    time = pd.date_range("2024-01-02 10:00", periods=n, freq="1min").to_numpy()
    open_ = np.arange(n, dtype=np.float64) + 100.0
    high = open_ + 1.0
    low = open_ - 1.0
    close = open_ + 0.5

    snapshot = ChartRenderSnapshot.from_engine_arrays(
        time=time,
        open_=open_,
        high=high,
        low=low,
        close=close,
        start=0,
        end=n,
        active_tf="1m",
    )

    assert snapshot.n == n
    assert snapshot.source_start == 0
    assert snapshot.source_end == n
    # Verify arrays are views (share memory with source).
    assert np.shares_memory(snapshot.open, open_)
    assert np.shares_memory(snapshot.close, close)


def test_snapshot_tail_slice():
    """Snapshot correctly slices the tail (last 5000 bars scenario)."""
    n = 10000
    time = pd.date_range("2024-01-02 10:00", periods=n, freq="1min").to_numpy()
    open_ = np.arange(n, dtype=np.float64) + 100.0
    high = open_ + 1.0
    low = open_ - 1.0
    close = open_ + 0.5

    start = n - 5000
    end = n
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

    assert snapshot.n == 5000
    assert snapshot.source_start == 5000
    assert snapshot.source_end == 10000
    # First element of snapshot should be element 5000 of source.
    assert snapshot.open[0] == open_[5000]
    assert snapshot.close[-1] == close[9999]


def test_candle_array_format():
    """candle_array() returns (n, 5) [x, open, close, low, high] format."""
    n = 100
    time = pd.date_range("2024-01-02 10:00", periods=n, freq="1min").to_numpy()
    open_ = np.full(n, 100.0)
    high = np.full(n, 102.0)
    low = np.full(n, 98.0)
    close = np.full(n, 101.0)

    snapshot = ChartRenderSnapshot.from_engine_arrays(
        time=time,
        open_=open_,
        high=high,
        low=low,
        close=close,
        start=0,
        end=n,
        active_tf="1m",
    )

    candle_arr = snapshot.candle_array()
    assert candle_arr.shape == (n, 5)
    assert candle_arr[0, 0] == 0.0  # x
    assert candle_arr[0, 1] == 100.0  # open
    assert candle_arr[0, 2] == 101.0  # close
    assert candle_arr[0, 3] == 98.0  # low
    assert candle_arr[0, 4] == 102.0  # high
    # Immutable flag set.
    assert not candle_arr.flags.writeable


def test_time_conversions():
    """time_int64() and time_float_seconds() produce correct formats."""
    n = 10
    time = pd.date_range("2024-01-02 10:00", periods=n, freq="1min").to_numpy()
    open_ = np.zeros(n)
    high = np.zeros(n)
    low = np.zeros(n)
    close = np.zeros(n)

    snapshot = ChartRenderSnapshot.from_engine_arrays(
        time=time,
        open_=open_,
        high=high,
        low=low,
        close=close,
        start=0,
        end=n,
        active_tf="1m",
    )

    time_int = snapshot.time_int64()
    time_float = snapshot.time_float_seconds()

    assert time_int.dtype == np.int64
    assert time_float.dtype == np.float64
    # Float seconds should be int64 nanoseconds / 1e9.
    assert np.allclose(time_float, time_int / 1e9)
