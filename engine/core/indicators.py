"""
Technical indicators - pure NumPy, no external dependencies.

Single source of truth for SMA / EMA / ATR / RSI / MACD / Donchian
channel. BacktestCore and strategies use these exact implementations so
warm-up convention and smoothing windows stay consistent.
cannot drift on the warm-up convention or smoothing window.

All functions accept a ``np.ndarray`` of ``float64`` price points and
return a same-length array (or tuple of arrays) where the first
``period - 1`` (or equivalent) values are ``NaN``. This makes it safe to
slice with the latest index вЂ” strategies should always check
``np.isfinite(value)`` before using the result.

The module is import-clean: zero PySide6, zero pandas. Adding it to a
strategy adds no GUI footprint to a headless run.
"""

from __future__ import annotations

from typing import Tuple

import numpy as np


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _validate(arr: np.ndarray, period: int, name: str) -> None:
    if period <= 0:
        raise ValueError(f"{name}: period must be positive, got {period}")
    if arr.ndim != 1:
        raise ValueError(f"{name}: expected 1-D array, got shape {arr.shape}")


def _nan_array(n: int) -> np.ndarray:
    """Return an n-length float64 NaN array."""
    return np.full(n, np.nan, dtype=np.float64)


# ---------------------------------------------------------------------------
# Moving averages
# ---------------------------------------------------------------------------
def sma(arr: np.ndarray, period: int) -> np.ndarray:
    """Simple Moving Average over the trailing ``period`` bars.

    The first ``period - 1`` values are NaN; from index ``period - 1``
    onwards the value is the arithmetic mean of the trailing window.
    Implementation uses a cumulative sum so the cost is O(n) regardless
    of period.
    """
    _validate(arr, period, "sma")
    n = arr.size
    out = _nan_array(n)
    if n < period:
        return out
    cumsum = np.cumsum(arr, dtype=np.float64)
    out[period - 1] = cumsum[period - 1] / period
    if n > period:
        out[period:] = (cumsum[period:] - cumsum[:-period]) / period
    return out


def ema(arr: np.ndarray, period: int) -> np.ndarray:
    """Exponential Moving Average using the ``alpha = 2 / (period + 1)``
    convention with the standard SMA-seeded warm-up.

    The first ``period - 1`` values are NaN; the value at index
    ``period - 1`` is the SMA of the first ``period`` points (so the EMA
    is comparable to a simple moving average at warm-up). Subsequent
    values are computed iteratively.
    """
    _validate(arr, period, "ema")
    n = arr.size
    out = _nan_array(n)
    if n < period:
        return out
    alpha = 2.0 / (period + 1.0)
    out[period - 1] = float(arr[:period].mean())
    for i in range(period, n):
        out[i] = arr[i] * alpha + out[i - 1] * (1.0 - alpha)
    return out


# ---------------------------------------------------------------------------
# Volatility
# ---------------------------------------------------------------------------
def atr(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    period: int = 14,
) -> np.ndarray:
    """Wilder-style Average True Range.

    True Range is computed against the *previous* close (the first bar
    falls back to its own range). The series is then smoothed with
    Wilder's recursive average вЂ” equivalent to an EMA with
    ``alpha = 1 / period``. Returns an array of the same length; the
    first ``period - 1`` values are NaN by construction.
    """
    if not (high.shape == low.shape == close.shape):
        raise ValueError("atr: high/low/close must have identical shape")
    _validate(close, period, "atr")
    n = close.size
    if n == 0:
        return _nan_array(0)
    prev_close = np.concatenate(([close[0]], close[:-1]))
    true_range = np.maximum.reduce(
        [
            high - low,
            np.abs(high - prev_close),
            np.abs(low - prev_close),
        ]
    )
    out = _nan_array(n)
    if n < period:
        return out
    out[period - 1] = float(true_range[:period].mean())
    for i in range(period, n):
        out[i] = (out[i - 1] * (period - 1) + true_range[i]) / period
    return out


# ---------------------------------------------------------------------------
# Momentum
# ---------------------------------------------------------------------------
def rsi(close: np.ndarray, period: int = 14) -> np.ndarray:
    """Relative Strength Index using Wilder's smoothing.

    Returns the standard 0-100 RSI; the first ``period`` values are
    NaN. When the average loss is zero the RSI is reported as 100.0.
    """
    _validate(close, period, "rsi")
    n = close.size
    out = _nan_array(n)
    if n <= period:
        return out
    deltas = np.diff(close)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)

    avg_gain = float(gains[:period].mean())
    avg_loss = float(losses[:period].mean())
    out[period] = 100.0 if avg_loss == 0 else 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)
    for i in range(period + 1, n):
        gain = gains[i - 1]
        loss = losses[i - 1]
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        if avg_loss == 0:
            out[i] = 100.0
        else:
            rs = avg_gain / avg_loss
            out[i] = 100.0 - 100.0 / (1.0 + rs)
    return out


def macd(
    close: np.ndarray,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Moving Average Convergence Divergence.

    Returns a triple of (macd_line, signal_line, histogram). All three
    arrays are aligned with the input length; bars before the slow EMA
    or signal EMA warm-up are NaN.
    """
    if not (fast > 0 and slow > 0 and signal > 0):
        raise ValueError("macd: fast/slow/signal periods must be positive")
    if fast >= slow:
        raise ValueError("macd: fast period must be smaller than slow")
    fast_line = ema(close, fast)
    slow_line = ema(close, slow)
    macd_line = fast_line - slow_line

    # Run the signal EMA over the macd_line, but skip leading NaNs so the
    # warm-up matches a textbook MACD definition.
    signal_line = _nan_array(macd_line.size)
    valid_idx = np.where(np.isfinite(macd_line))[0]
    if valid_idx.size >= signal:
        start = int(valid_idx[0])
        ema_input = macd_line[start:]
        ema_out = ema(ema_input, signal)
        signal_line[start:] = ema_out

    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


# ---------------------------------------------------------------------------
# Channel
# ---------------------------------------------------------------------------
def donchian_channel(
    high: np.ndarray,
    low: np.ndarray,
    period: int,
    *,
    exclude_current: bool = True,
) -> Tuple[np.ndarray, np.ndarray]:
    """Donchian channel (rolling max-high / min-low).

    By default the *current* bar is excluded from the window so a
    breakout test compares the latest close against history that does
    not yet contain it. Pass ``exclude_current=False`` for an inclusive
    rolling window.
    """
    if high.shape != low.shape:
        raise ValueError("donchian_channel: high/low must have identical shape")
    _validate(high, period, "donchian_channel")

    n = high.size
    upper = _nan_array(n)
    lower = _nan_array(n)
    if exclude_current:
        # Window covers indices [i-period .. i-1] inclusive.
        for i in range(period, n):
            upper[i] = float(high[i - period : i].max())
            lower[i] = float(low[i - period : i].min())
    else:
        # Window covers indices [i-period+1 .. i] inclusive.
        for i in range(period - 1, n):
            upper[i] = float(high[i - period + 1 : i + 1].max())
            lower[i] = float(low[i - period + 1 : i + 1].min())
    return upper, lower


__all__ = [
    "sma",
    "ema",
    "atr",
    "rsi",
    "macd",
    "donchian_channel",
]
