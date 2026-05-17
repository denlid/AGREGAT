"""Phase 6.2 (D5) — buy-and-hold benchmark equity series.

Used by the Analytics tab's equity-curve chart to overlay a "what if I
just held the asset" reference next to the strategy's curve. Pure
function — takes an OHLC dataframe and an initial equity, returns a
``pd.Series`` aligned to the input timestamps.

We intentionally keep the model trivially simple (one unit at the first
close, no leverage, no commissions) so users can mentally cross-check
the overlay. Strategies that want a different reference (e.g. a
benchmark CSV) can build their own series and feed it directly to the
chart via :func:`compute_relative_performance`.
"""

from __future__ import annotations

from typing import Optional, Tuple

import numpy as np
import pandas as pd


def buy_hold_equity(
    df: pd.DataFrame,
    initial_equity: float,
    *,
    price_col: str = "close",
    time_col: str = "time",
) -> pd.Series:
    """Return a ``pd.Series`` of buy-and-hold equity values.

    The series is indexed by the dataframe's ``time_col`` and starts at
    ``initial_equity`` on the first bar. Each subsequent value scales
    by the percentage change of ``price_col`` (close-to-close) so a
    +10 % move on the underlying produces a +10 % move on the curve.

    Returns an empty series when the dataframe is missing the required
    columns or contains fewer than two valid prices.
    """
    if df is None or len(df) < 2:
        return pd.Series(dtype="float64")
    if price_col not in df.columns or time_col not in df.columns:
        return pd.Series(dtype="float64")
    times = pd.to_datetime(df[time_col], errors="coerce")
    prices = pd.to_numeric(df[price_col], errors="coerce")
    mask = times.notna() & prices.notna() & (prices > 0)
    times = times[mask].reset_index(drop=True)
    prices = prices[mask].reset_index(drop=True)
    if len(prices) < 2:
        return pd.Series(dtype="float64")
    base = float(prices.iloc[0])
    if base <= 0:
        return pd.Series(dtype="float64")
    equity = (prices / base) * float(initial_equity)
    return pd.Series(equity.values, index=times.values, name="buy_hold")


def compute_relative_performance(
    strategy_equity: pd.Series,
    benchmark_equity: pd.Series,
) -> Tuple[Optional[float], Optional[float]]:
    """Return ``(strategy_total_return, benchmark_total_return)`` as fractions.

    Both arguments must be non-empty equity series. ``None`` is returned
    in either slot if its series is too short. Returns are computed
    against the *first* value in each series (which is conventionally
    the starting equity), so a +50 % strategy and a +20 % benchmark
    yield ``(0.5, 0.2)``.
    """
    s_ret = _total_return(strategy_equity)
    b_ret = _total_return(benchmark_equity)
    return s_ret, b_ret


def _total_return(series: pd.Series) -> Optional[float]:
    if series is None or len(series) < 2:
        return None
    arr = pd.to_numeric(series, errors="coerce").dropna().to_numpy()
    if arr.size < 2:
        return None
    base = float(arr[0])
    if base <= 0 or not np.isfinite(base):
        return None
    final = float(arr[-1])
    if not np.isfinite(final):
        return None
    return (final / base) - 1.0


__all__ = ["buy_hold_equity", "compute_relative_performance"]
