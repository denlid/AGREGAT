"""
Sparkline data helpers — rolling series builders for MetricCard sparklines.

Each helper takes the per-trade arrays already cached on
:class:`engine.analytics.context.AnalyticsContext` (or any equivalent) and
returns a small ``list[float]`` suitable for
:pymeth:`gui.widgets.metric_card.MetricCard.set_sparkline` —
typically 30–60 points.

Functions are pure / stateless and operate on NumPy arrays so they are easy
to unit-test and re-use from a worker thread.

The shape of every helper is the same::

    helper(pnl_np: np.ndarray, ...) -> list[float]

Empty / too-short inputs return ``[]`` so callers can safely pass straight
to ``set_sparkline()`` (which hides the spark when empty).
"""

from __future__ import annotations

import logging
from typing import Sequence

import numpy as np

logger = logging.getLogger("StrategyTester.gui.analytics.sparkline_data")


def _ensure_array(values: Sequence[float] | np.ndarray, dtype: str = "float64") -> np.ndarray:
    if isinstance(values, np.ndarray):
        if values.dtype != np.dtype(dtype):
            return values.astype(dtype, copy=False)
        return values
    return np.asarray(list(values), dtype=dtype)


def _decimate(values: np.ndarray, target: int) -> list[float]:
    """Down-sample ``values`` to at most ``target`` points using even stride.

    The first and last samples are always kept so the line shape is preserved
    at the endpoints. Returns a list (sparkline widget API).
    """
    n = int(values.size)
    if n == 0:
        return []
    if n <= target:
        return [float(v) for v in values]
    idx = np.linspace(0, n - 1, target).astype(int)
    return [float(values[i]) for i in idx]


def cumulative_equity(
    pnl_np: np.ndarray | Sequence[float],
    initial_equity: float = 0.0,
    *,
    points: int = 60,
) -> list[float]:
    """Cumulative equity curve decimated to ``points`` samples.

    Used by the **Total P&L** card.
    """
    arr = _ensure_array(pnl_np)
    if arr.size == 0:
        return []
    eq = float(initial_equity) + np.cumsum(arr)
    return _decimate(eq, points)


def underwater_curve(
    pnl_np: np.ndarray | Sequence[float],
    initial_equity: float = 0.0,
    *,
    points: int = 60,
) -> list[float]:
    """Underwater drawdown curve (negative-only) for the **Max DD** card.

    Returns a sequence where each value is ``-dd_pct`` (so a deeper drawdown
    is a more-negative number; the sparkline tints red because the net
    delta is negative).
    """
    arr = _ensure_array(pnl_np)
    if arr.size == 0:
        return []
    eq = float(initial_equity) + np.cumsum(arr)
    peak = np.maximum.accumulate(eq)
    safe_peak = np.where(peak > 0, peak, 1.0)
    dd_pct = (eq - peak) / safe_peak * 100.0  # values <= 0
    return _decimate(dd_pct, points)


def rolling_win_rate(
    pnl_np: np.ndarray | Sequence[float],
    *,
    window: int = 30,
    points: int = 60,
) -> list[float]:
    """Rolling win-rate (% wins) over a sliding ``window`` of trades."""
    arr = _ensure_array(pnl_np)
    if arr.size < window:
        return []
    wins = (arr > 0).astype("float64")
    # Convolution gives the running sum over ``window`` trades.
    kernel = np.ones(window, dtype="float64")
    sums = np.convolve(wins, kernel, mode="valid")
    wr = sums / window * 100.0
    return _decimate(wr, points)


def rolling_return(
    pnl_np: np.ndarray | Sequence[float],
    *,
    window: int = 30,
    points: int = 60,
) -> list[float]:
    """Rolling cumulative return ($) per sliding window of ``window`` trades."""
    arr = _ensure_array(pnl_np)
    if arr.size < window:
        return []
    kernel = np.ones(window, dtype="float64")
    sums = np.convolve(arr, kernel, mode="valid")
    return _decimate(sums, points)


def _rolling_window_2d(arr: np.ndarray, window: int) -> np.ndarray:
    """Return a 2D view of shape ``(n-window+1, window)``.

    Equivalent to ``np.lib.stride_tricks.sliding_window_view`` but kept
    explicit so we control dtype + memory layout.
    """
    if arr.size < window:
        return np.empty((0, window), dtype=arr.dtype)
    return np.lib.stride_tricks.sliding_window_view(arr, window)


def rolling_sharpe(
    pnl_np: np.ndarray | Sequence[float],
    *,
    window: int = 50,
    points: int = 60,
) -> list[float]:
    """Rolling per-trade Sharpe-like ratio (mean / std).

    Not annualised — the sparkline is for **shape**, not absolute scale.
    """
    arr = _ensure_array(pnl_np)
    if arr.size < window:
        return []
    view = _rolling_window_2d(arr, window)
    means = view.mean(axis=1)
    stds = view.std(axis=1)
    sharpe = np.where(stds > 0, means / stds, 0.0)
    return _decimate(sharpe, points)


def rolling_sortino(
    pnl_np: np.ndarray | Sequence[float],
    *,
    window: int = 50,
    points: int = 60,
) -> list[float]:
    """Rolling per-trade Sortino-like ratio (mean / downside std)."""
    arr = _ensure_array(pnl_np)
    if arr.size < window:
        return []
    view = _rolling_window_2d(arr, window)
    means = view.mean(axis=1)
    # Downside-only std: only negative returns contribute.
    neg = np.where(view < 0, view, 0.0)
    # population variance on the masked array
    sq = (neg ** 2).mean(axis=1)
    downside = np.sqrt(sq)
    with np.errstate(divide="ignore", invalid="ignore"):
        sortino = np.where(downside > 0, means / np.where(downside > 0, downside, 1.0), 0.0)
    return _decimate(sortino, points)


def histogram_bars(
    values: np.ndarray | Sequence[float],
    *,
    bins: int = 10,
    last_n: int = 50,
) -> list[float]:
    """Histogram of the most recent ``last_n`` values, padded to ``bins`` bars.

    Used by the **Avg Win** / **Avg Loss** cards. Returns an array of bin
    counts (floats) so the SparklineWidget shows the distribution shape.
    """
    arr = _ensure_array(values)
    if arr.size == 0:
        return []
    tail = arr[-last_n:]
    if tail.size == 0:
        return []
    counts, _edges = np.histogram(tail, bins=max(1, int(bins)))
    return [float(c) for c in counts]


def streak_bars(
    pnl_np: np.ndarray | Sequence[float],
    *,
    last_n: int = 20,
) -> list[float]:
    """Win/loss/BE sequence for the last ``last_n`` trades.

    Each element is ``+1`` (win), ``-1`` (loss) or ``0`` (BE). Used by the
    **Current Streak** card; the sparkline renders this as a wavy line that
    tints green or red based on net direction.
    """
    arr = _ensure_array(pnl_np)
    if arr.size == 0:
        return []
    tail = arr[-last_n:]
    out = np.where(tail > 0, 1.0, np.where(tail < 0, -1.0, 0.0))
    return [float(v) for v in out]


__all__ = [
    "cumulative_equity",
    "histogram_bars",
    "rolling_return",
    "rolling_sharpe",
    "rolling_sortino",
    "rolling_win_rate",
    "streak_bars",
    "underwater_curve",
]
