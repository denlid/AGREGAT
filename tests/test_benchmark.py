"""Phase 6.2 (D5) — benchmark equity overlay."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from engine.analytics.benchmark import buy_hold_equity, compute_relative_performance


def _df(closes):
    times = pd.date_range("2024-01-01 09:00", periods=len(closes), freq="1min")
    return pd.DataFrame({"time": times, "close": closes})


def test_buy_hold_starts_at_initial_equity():
    bh = buy_hold_equity(_df([100, 110, 120]), initial_equity=10_000)
    assert bh.iloc[0] == pytest.approx(10_000)


def test_buy_hold_scales_with_price():
    bh = buy_hold_equity(_df([100, 110, 90]), initial_equity=10_000)
    # +10% then -10% from base.
    assert bh.iloc[1] == pytest.approx(11_000)
    assert bh.iloc[2] == pytest.approx(9_000)


def test_buy_hold_empty_df_returns_empty_series():
    assert buy_hold_equity(pd.DataFrame(), initial_equity=10_000).empty


def test_buy_hold_missing_columns_returns_empty():
    df = pd.DataFrame({"only_time": [1, 2, 3]})
    assert buy_hold_equity(df, initial_equity=10_000).empty


def test_buy_hold_skips_non_positive_prices():
    """A bad row with close=0 must not crash the helper."""
    df = _df([100, 0, 110, np.nan, 120])
    bh = buy_hold_equity(df, initial_equity=10_000)
    # Three valid prices remain → series has length 3.
    assert len(bh) == 3
    assert bh.iloc[0] == pytest.approx(10_000)


def test_relative_performance_returns_fractions():
    strat = pd.Series([10_000, 15_000])
    bench = pd.Series([10_000, 12_000])
    s, b = compute_relative_performance(strat, bench)
    assert s == pytest.approx(0.5)
    assert b == pytest.approx(0.2)


def test_relative_performance_handles_too_short():
    s, b = compute_relative_performance(
        pd.Series([10_000]), pd.Series([10_000, 12_000])
    )
    assert s is None
    assert b == pytest.approx(0.2)


def test_relative_performance_handles_zero_base():
    """A zero-base strategy curve must NOT explode to inf."""
    s, b = compute_relative_performance(pd.Series([0.0, 100.0]), pd.Series([1.0, 2.0]))
    assert s is None
    assert b == pytest.approx(1.0)
