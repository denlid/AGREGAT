"""Bar-level equity curve and bar-return based ratio metrics.

The trade-level :func:`engine.analytics.equity_curve.build_equity_curve`
is piecewise-constant — equity only changes when a trade closes. That
hides intra-trade drawdown and underestimates true volatility.

This module produces a *bar-level* equity series sampled at every 1m
candle: the unrealised PnL of any open position is added to realised
equity on every bar so the curve dips and recovers exactly the way the
broker statement would.

Why it matters
--------------
``Sharpe 2.5 in backtest, 1.4 in live`` is almost always a backtest
problem, not a live problem. Trade-return Sharpe ignores the path
between open and close, missing the intra-trade volatility that makes
a strategy painful to hold. Computing Sharpe / Sortino / Calmar on the
bar-level series is the institutional norm.

Public API
----------
* :func:`build_bar_level_equity` — dollar-equity per bar.
* :func:`bar_returns` — periodic returns derived from the bar series.
* :func:`bar_sharpe_ratio`, :func:`bar_sortino_ratio`, :func:`bar_calmar_ratio`
  — the same ratios as the trade-level helpers but with the
  bar-level series as input. They auto-detect the sampling frequency
  (so 1m bars annualise differently than 1h bars).
"""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger("StrategyTester.bar_level_equity")

# Trading-period scaling factors used for the *square-root-of-N*
# annualisation rule. Numbers reflect the conventional 252 trading days
# / ~6.5h per US session — close enough for forex/crypto-friendly
# back-of-envelope use.
SECONDS_PER_TRADING_YEAR = 252.0 * 6.5 * 3600.0


def build_bar_level_equity(
    bar_equity: np.ndarray,
    times: Optional[np.ndarray] = None,
) -> pd.Series:
    """Wrap a NumPy bar-equity buffer in a time-indexed :class:`pandas.Series`.

    Parameters
    ----------
    bar_equity
        ``shape=(n_bars,)`` float64 array — the realised + unrealised
        equity at the *close* of each bar. Produced by
        :class:`BacktestCore` when ``track_bar_equity=True``.
    times
        Optional matching ``datetime64[ns]`` array. When omitted, an
        integer ``RangeIndex`` is used (still usable for ratio metrics
        — frequency detection falls back to "assume 1 bar").

    Returns
    -------
    :class:`pandas.Series`
        Indexed by bar timestamp (or RangeIndex), values in dollars.
    """
    eq = np.asarray(bar_equity, dtype=np.float64)
    if times is None or len(times) != len(eq):
        return pd.Series(eq, dtype="float64")
    idx = pd.DatetimeIndex(times)
    return pd.Series(eq, index=idx, dtype="float64")


def bar_returns(equity: pd.Series) -> np.ndarray:
    """Per-bar simple returns (``equity[i] / equity[i-1] - 1``).

    Drops the first bar (no preceding bar to compare against). Equity
    values that round-trip through zero are guarded so the divide does
    not produce ``inf``.
    """
    if len(equity) < 2:
        return np.array([], dtype="float64")
    eq = equity.to_numpy(dtype="float64")
    prev = eq[:-1]
    cur = eq[1:]
    safe_prev = np.where(np.abs(prev) > 1e-12, prev, np.nan)
    rets = (cur - prev) / safe_prev
    rets = np.where(np.isfinite(rets), rets, 0.0)
    return rets.astype("float64")


def _periods_per_year(equity: pd.Series) -> float:
    """Estimate annualisation factor from the median bar spacing.

    Uses median (not mean) because optimization runs may interleave
    weekends / EOD gaps that skew the average. Falls back to 252 when
    the index is non-monotonic / non-temporal (RangeIndex case).
    """
    if not isinstance(equity.index, pd.DatetimeIndex) or len(equity) < 3:
        return 252.0
    deltas = np.diff(equity.index.asi8.astype("int64"))
    deltas = deltas[deltas > 0]
    if deltas.size == 0:
        return 252.0
    median_ns = float(np.median(deltas))
    if median_ns <= 0:
        return 252.0
    seconds = median_ns / 1e9
    return max(SECONDS_PER_TRADING_YEAR / seconds, 1.0)


def bar_sharpe_ratio(
    equity: pd.Series,
    risk_free: float = 0.0,
) -> float:
    """Annualised Sharpe based on **per-bar** equity returns.

    Uses the median bar spacing to determine the annualisation
    factor — the same series can be 1m-sampled or 1h-sampled and the
    ratio will scale correctly. Annualised risk-free rate is converted
    to a per-bar value before the excess-return mean.
    """
    rets = bar_returns(equity)
    if rets.size < 2:
        return 0.0
    ppy = _periods_per_year(equity)
    per_period_rf = risk_free / ppy
    excess = rets - per_period_rf
    mu = float(np.mean(excess))
    sd = float(np.std(excess, ddof=1))
    if sd <= 0:
        return 0.0
    return float(mu / sd * np.sqrt(ppy))


def bar_sortino_ratio(
    equity: pd.Series,
    risk_free: float = 0.0,
) -> float:
    """Annualised Sortino computed from per-bar equity returns.

    Penalises only the downside half-variance.
    """
    rets = bar_returns(equity)
    if rets.size < 2:
        return 0.0
    ppy = _periods_per_year(equity)
    per_period_rf = risk_free / ppy
    excess = rets - per_period_rf
    downside = excess[excess < 0]
    if downside.size == 0:
        return 0.0
    dd = float(np.sqrt(np.mean(downside**2)))
    if dd <= 0:
        return 0.0
    return float(float(np.mean(excess)) / dd * np.sqrt(ppy))


def bar_max_drawdown(equity: pd.Series) -> tuple[float, float]:
    """Return ``(max_dd_abs_dollars, max_dd_pct)`` on the bar-level series.

    Unlike trade-level max drawdown this includes intra-trade
    excursions, so the value will normally be larger.
    """
    if len(equity) == 0:
        return 0.0, 0.0
    eq = equity.to_numpy(dtype="float64")
    peak = np.maximum.accumulate(eq)
    dd_abs = peak - eq
    dd_pct = np.where(peak > 0, dd_abs / peak, 0.0)
    return float(dd_abs.max()), float(dd_pct.max())


def bar_calmar_ratio(equity: pd.Series, initial_equity: float) -> float:
    """Annualised return / max-drawdown using the bar-level curve.

    Mirrors :func:`engine.analytics.equity_curve.calmar_ratio` but with
    intra-trade DD baked in.
    """
    if len(equity) < 2 or initial_equity <= 0:
        return 0.0
    if not isinstance(equity.index, pd.DatetimeIndex):
        years = max(len(equity) / _periods_per_year(equity), 1e-9)
    else:
        span = float((equity.index[-1] - equity.index[0]).total_seconds())
        years = max(span / (365.25 * 24 * 3600), 1e-9)
    if years < 0.1:
        return 0.0
    final = float(equity.iloc[-1])
    total_return = final / initial_equity
    if total_return <= 0:
        return float("nan")
    annual = total_return ** (1.0 / years) - 1.0
    _dd_abs, dd_pct = bar_max_drawdown(equity)
    if dd_pct <= 0:
        return 0.0
    return float(annual / dd_pct)


__all__ = [
    "build_bar_level_equity",
    "bar_returns",
    "bar_sharpe_ratio",
    "bar_sortino_ratio",
    "bar_max_drawdown",
    "bar_calmar_ratio",
]
