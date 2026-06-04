"""Test float32 migration in BacktestCore.

Verifies that AGREGAT_USE_FLOAT32 env var correctly switches OHLC arrays
to float32 with 50% memory savings and negligible precision loss.
"""

import os
import numpy as np
import pandas as pd
import pytest


def test_float32_disabled_by_default():
    """Without env var, BacktestCore uses float64 (backward compatible)."""
    # Ensure env var is not set
    os.environ.pop("AGREGAT_USE_FLOAT32", None)

    from engine.core.backtest_core import BacktestCore

    engine = BacktestCore()
    df = pd.DataFrame({
        "time": pd.date_range("2024-01-01", periods=100, freq="1min"),
        "open": np.random.randn(100) + 100,
        "high": np.random.randn(100) + 101,
        "low": np.random.randn(100) + 99,
        "close": np.random.randn(100) + 100,
    })

    engine.load_data(df)

    # Verify float64 (default)
    assert engine._open.dtype == np.float64
    assert engine._high.dtype == np.float64
    assert engine._low.dtype == np.float64
    assert engine._close.dtype == np.float64


def test_float32_enabled_with_env_var():
    """With AGREGAT_USE_FLOAT32=1, BacktestCore uses float32."""
    os.environ["AGREGAT_USE_FLOAT32"] = "1"

    try:
        # Need to reimport to pick up env var
        import importlib
        import engine.core.backtest_core
        importlib.reload(engine.core.backtest_core)
        from engine.core.backtest_core import BacktestCore

        engine = BacktestCore()
        df = pd.DataFrame({
            "time": pd.date_range("2024-01-01", periods=100, freq="1min"),
            "open": np.random.randn(100) + 100,
            "high": np.random.randn(100) + 101,
            "low": np.random.randn(100) + 99,
            "close": np.random.randn(100) + 100,
        })

        engine.load_data(df)

        # Verify float32
        assert engine._open.dtype == np.float32
        assert engine._high.dtype == np.float32
        assert engine._low.dtype == np.float32
        assert engine._close.dtype == np.float32
    finally:
        os.environ.pop("AGREGAT_USE_FLOAT32", None)


def test_float32_memory_savings():
    """Float32 uses 50% less memory than float64."""
    os.environ["AGREGAT_USE_FLOAT32"] = "1"

    try:
        import importlib
        import engine.core.backtest_core
        importlib.reload(engine.core.backtest_core)
        from engine.core.backtest_core import BacktestCore

        engine_f32 = BacktestCore()
        df = pd.DataFrame({
            "time": pd.date_range("2024-01-01", periods=10000, freq="1min"),
            "open": np.random.randn(10000) + 100,
            "high": np.random.randn(10000) + 101,
            "low": np.random.randn(10000) + 99,
            "close": np.random.randn(10000) + 100,
        })

        engine_f32.load_data(df)

        mem_f32 = (
            engine_f32._open.nbytes +
            engine_f32._high.nbytes +
            engine_f32._low.nbytes +
            engine_f32._close.nbytes
        )

        # Compare to expected float64 size
        expected_f64 = 10000 * 4 * 8  # 4 arrays × 8 bytes
        expected_f32 = 10000 * 4 * 4  # 4 arrays × 4 bytes

        assert mem_f32 == expected_f32
        assert mem_f32 == expected_f64 // 2  # 50% savings
    finally:
        os.environ.pop("AGREGAT_USE_FLOAT32", None)


def test_float32_precision_acceptable():
    """Float32 precision loss is negligible for price data."""
    os.environ["AGREGAT_USE_FLOAT32"] = "1"

    try:
        import importlib
        import engine.core.backtest_core
        importlib.reload(engine.core.backtest_core)
        from engine.core.backtest_core import BacktestCore

        # Use realistic price data
        prices_f64 = 100.0 + np.cumsum(np.random.randn(1000) * 0.5)

        df = pd.DataFrame({
            "time": pd.date_range("2024-01-01", periods=1000, freq="1min"),
            "open": prices_f64,
            "high": prices_f64 + 0.5,
            "low": prices_f64 - 0.5,
            "close": prices_f64 + 0.1,
        })

        engine = BacktestCore()
        engine.load_data(df)

        # Compare float32 result to original float64
        max_error = np.max(np.abs(prices_f64 - engine._open.astype(np.float64)))

        # Error should be < 0.001 (negligible for price data)
        assert max_error < 0.001
    finally:
        os.environ.pop("AGREGAT_USE_FLOAT32", None)


def test_float32_chart_integration():
    """Float32 arrays work with ChartRenderSnapshot."""
    os.environ["AGREGAT_USE_FLOAT32"] = "1"

    try:
        import importlib
        import engine.core.backtest_core
        importlib.reload(engine.core.backtest_core)
        from engine.core.backtest_core import BacktestCore
        from gui.chart_tab.render_snapshot import ChartRenderSnapshot

        engine = BacktestCore()
        df = pd.DataFrame({
            "time": pd.date_range("2024-01-01", periods=100, freq="1min"),
            "open": np.random.randn(100) + 100,
            "high": np.random.randn(100) + 101,
            "low": np.random.randn(100) + 99,
            "close": np.random.randn(100) + 100,
        })

        engine.load_data(df)

        # Create snapshot from float32 arrays
        snapshot = ChartRenderSnapshot.from_engine_arrays(
            time=engine._time,
            open_=engine._open,
            high=engine._high,
            low=engine._low,
            close=engine._close,
            start=0,
            end=100,
            active_tf="1m",
        )

        # Verify snapshot works
        assert snapshot.n == 100
        candle_arr = snapshot.candle_array()
        assert candle_arr.shape == (100, 5)

        # Candle array should be float32 (or upcast to float64, both OK)
        assert candle_arr.dtype in (np.float32, np.float64)
    finally:
        os.environ.pop("AGREGAT_USE_FLOAT32", None)
