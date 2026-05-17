"""Tests for BacktestEngine.set_date_range — slice filtering & reset behaviour."""

from __future__ import annotations

import pandas as pd
import pytest

from engine.backtest_engine import BacktestEngine


def _make_df(periods: int = 10, start: str = "2024-01-01"):
    """Generate a tiny OHLCV frame with daily candles."""
    times = pd.date_range(start, periods=periods, freq="D")
    return pd.DataFrame(
        {
            "time": times,
            "open": [100.0] * periods,
            "high": [101.0] * periods,
            "low": [99.0] * periods,
            "close": [100.0] * periods,
            "volume": [1000] * periods,
        }
    )


def test_set_date_range_filters_inclusive():
    eng = BacktestEngine()
    eng.load_data(_make_df(10))
    assert len(eng.df) == 10
    eng.set_date_range(pd.Timestamp("2024-01-03"), pd.Timestamp("2024-01-07"))
    assert len(eng.df) == 5
    assert eng.df["time"].iloc[0] == pd.Timestamp("2024-01-03")
    assert eng.df["time"].iloc[-1] == pd.Timestamp("2024-01-07")


def test_set_date_range_none_resets_to_full():
    eng = BacktestEngine()
    eng.load_data(_make_df(10))
    eng.set_date_range(pd.Timestamp("2024-01-03"), pd.Timestamp("2024-01-07"))
    assert len(eng.df) == 5
    eng.set_date_range(None, None)
    assert len(eng.df) == 10


def test_set_date_range_only_start():
    eng = BacktestEngine()
    eng.load_data(_make_df(10))
    eng.set_date_range(pd.Timestamp("2024-01-05"), None)
    assert len(eng.df) == 6
    assert eng.df["time"].iloc[0] == pd.Timestamp("2024-01-05")


def test_set_date_range_only_end():
    eng = BacktestEngine()
    eng.load_data(_make_df(10))
    eng.set_date_range(None, pd.Timestamp("2024-01-04"))
    assert len(eng.df) == 4


def test_set_date_range_no_op_without_data():
    """Calling before load_data must not crash."""
    eng = BacktestEngine()
    eng.set_date_range(pd.Timestamp("2024-01-01"), pd.Timestamp("2024-01-31"))
    assert eng.df is None


def test_load_data_records_full_copy():
    eng = BacktestEngine()
    df = _make_df(5)
    eng.load_data(df)
    assert eng.df_full is not None
    assert len(eng.df_full) == 5
