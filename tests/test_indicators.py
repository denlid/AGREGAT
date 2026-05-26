"""
Tests for :mod:`engine.core.indicators`.

The indicators module is the single source of truth for SMA / EMA /
ATR / RSI / MACD / Donchian. These tests pin down the *exact* values
the visual and vectorised paths share so future refactors cannot
silently change the warm-up or smoothing convention.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from engine.core.indicators import (
    atr,
    donchian_channel,
    ema,
    macd,
    rsi,
    sma,
)


# ---------------------------------------------------------------------------
# SMA
# ---------------------------------------------------------------------------
def test_sma_warmup_returns_nan_until_period_full():
    arr = np.arange(10, dtype=np.float64)
    out = sma(arr, period=3)
    assert np.isnan(out[:2]).all()
    assert out[2] == pytest.approx(1.0)
    assert out[-1] == pytest.approx(8.0)


def test_sma_matches_naive_rolling_mean():
    rng = np.random.default_rng(42)
    arr = rng.normal(size=100).astype(np.float64)
    out = sma(arr, period=10)
    for i in range(9, len(arr)):
        assert out[i] == pytest.approx(arr[i - 9 : i + 1].mean())


def test_sma_short_input_is_all_nan():
    arr = np.array([1.0, 2.0], dtype=np.float64)
    out = sma(arr, period=5)
    assert np.isnan(out).all()


def test_sma_rejects_non_positive_period():
    arr = np.array([1.0, 2.0, 3.0], dtype=np.float64)
    with pytest.raises(ValueError):
        sma(arr, period=0)


# ---------------------------------------------------------------------------
# EMA
# ---------------------------------------------------------------------------
def test_ema_seeded_with_sma_at_warmup():
    arr = np.array([1, 2, 3, 4, 5], dtype=np.float64)
    out = ema(arr, period=3)
    # First two NaN, third = SMA(1,2,3) = 2.0
    assert np.isnan(out[0]) and np.isnan(out[1])
    assert out[2] == pytest.approx(2.0)
    # Subsequent values use alpha = 2/(3+1) = 0.5
    assert out[3] == pytest.approx(arr[3] * 0.5 + out[2] * 0.5)
    assert out[4] == pytest.approx(arr[4] * 0.5 + out[3] * 0.5)


def test_ema_constant_input_returns_constant():
    arr = np.full(50, 7.0, dtype=np.float64)
    out = ema(arr, period=10)
    assert out[-1] == pytest.approx(7.0)


# ---------------------------------------------------------------------------
# ATR
# ---------------------------------------------------------------------------
def test_atr_warmup_uses_simple_mean_of_first_period():
    high = np.array([10, 11, 12, 13, 14], dtype=np.float64)
    low = np.array([9, 10, 11, 12, 13], dtype=np.float64)
    close = np.array([9.5, 10.5, 11.5, 12.5, 13.5], dtype=np.float64)
    # TR per bar (vs prev close): [1.0, 1.5, 1.5, 1.5, 1.5]
    out = atr(high, low, close, period=3)
    expected_warmup = (1.0 + 1.5 + 1.5) / 3.0
    assert out[2] == pytest.approx(expected_warmup)


def test_atr_shape_validation():
    high = np.array([1.0, 2.0])
    low = np.array([1.0, 2.0, 3.0])
    close = np.array([1.0, 2.0])
    with pytest.raises(ValueError):
        atr(high, low, close, period=2)


# ---------------------------------------------------------------------------
# RSI
# ---------------------------------------------------------------------------
def test_rsi_pure_uptrend_yields_100():
    close = np.arange(1, 30, dtype=np.float64)  # strictly increasing
    out = rsi(close, period=14)
    # When losses are zero RSI is reported as 100.0.
    assert out[-1] == pytest.approx(100.0)


def test_rsi_returns_nan_until_warmup_complete():
    close = np.arange(1, 6, dtype=np.float64)
    out = rsi(close, period=14)
    assert np.isnan(out).all()


def test_rsi_in_valid_range():
    rng = np.random.default_rng(123)
    walk = np.cumsum(rng.normal(size=200))
    out = rsi(walk, period=14)
    finite = out[np.isfinite(out)]
    assert ((finite >= 0.0) & (finite <= 100.0)).all()


# ---------------------------------------------------------------------------
# MACD
# ---------------------------------------------------------------------------
def test_macd_returns_three_aligned_arrays():
    rng = np.random.default_rng(7)
    close = np.cumsum(rng.normal(size=200))
    line, signal_line, hist = macd(close, fast=12, slow=26, signal=9)
    assert line.shape == close.shape
    assert signal_line.shape == close.shape
    assert hist.shape == close.shape
    # Histogram = line - signal where both are finite.
    finite = np.isfinite(line) & np.isfinite(signal_line)
    assert np.allclose(hist[finite], (line - signal_line)[finite])


def test_macd_rejects_invalid_periods():
    with pytest.raises(ValueError):
        macd(np.zeros(100, dtype=np.float64), fast=26, slow=12)


# ---------------------------------------------------------------------------
# Donchian channel
# ---------------------------------------------------------------------------
def test_donchian_excludes_current_bar_by_default():
    high = np.array([10, 11, 12, 13, 14, 9], dtype=np.float64)
    low = np.array([5, 6, 7, 8, 9, 4], dtype=np.float64)
    upper, lower = donchian_channel(high, low, period=3)
    # At i=3 the window is bars 0..2 (highs 10,11,12; lows 5,6,7).
    assert upper[3] == pytest.approx(12.0)
    assert lower[3] == pytest.approx(5.0)
    # At i=5 the window is bars 2..4 (highs 12,13,14; lows 7,8,9).
    assert upper[5] == pytest.approx(14.0)
    assert lower[5] == pytest.approx(7.0)
    # Warm-up bars NaN.
    assert math.isnan(upper[0]) and math.isnan(lower[2])


def test_donchian_inclusive_window():
    high = np.array([10, 11, 12, 13, 14], dtype=np.float64)
    low = np.array([5, 6, 7, 8, 9], dtype=np.float64)
    upper, lower = donchian_channel(high, low, period=3, exclude_current=False)
    # At i=2 the window is bars 0..2 (highs 10,11,12; lows 5,6,7).
    assert upper[2] == pytest.approx(12.0)
    assert lower[2] == pytest.approx(5.0)
