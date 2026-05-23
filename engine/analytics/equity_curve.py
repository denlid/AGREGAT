"""
Equity Curve helpers — Build a time-indexed equity series from trades and
expose properly-annualised return / drawdown / Sharpe / Sortino / Calmar.

This module is the *correct* place to compute risk-adjusted metrics:
trade PnL is NOT a daily return, so you cannot annualise it with `* 252`.
Instead we resample the equity curve to a daily frequency and compute
returns from it.
"""

from __future__ import annotations

import logging
from typing import List, Sequence, Tuple

import numpy as np
import pandas as pd

from engine.models.models import Trade

logger = logging.getLogger("StrategyTester.equity_curve")

# Below this many daily-return samples Sharpe / Sortino are statistically
# unreliable. The functions still return the computed value, but emit a
# warning so the caller (and the Stats / Analytics tabs) can flag it. (TZ
# Phase 4 / D2.)
MIN_RETURNS_FOR_RATIO = 30


def build_equity_curve(
    trades: Sequence[Trade],
    initial_equity: float,
) -> pd.Series:
    """Return a pd.Series indexed by the trade exit timestamp.

    The first value is `initial_equity` at the first trade's entry (or `pd.Timestamp.now()`
    if no trades exist). Subsequent values are the running equity after each closed trade.
    """
    if not trades:
        return pd.Series([initial_equity], index=[pd.Timestamp.now()], dtype="float64")

    times: List[pd.Timestamp] = []
    values: List[float] = []
    eq = float(initial_equity)
    # Anchor on the first entry so the first equity point is the starting capital
    t0 = trades[0]
    try:
        anchor = pd.Timestamp(getattr(t0, "entry_time_np", None) or t0.entry_time)
    except Exception:
        anchor = pd.Timestamp.now()
    times.append(anchor)
    values.append(eq)

    for t in trades:
        try:
            # Use pre-cached np.datetime64 if available (avoids string parsing)
            ts = pd.Timestamp(getattr(t, "exit_time_np", None) or t.exit_time)
        except Exception:
            continue
        eq += float(t.pnl)
        times.append(ts)
        values.append(eq)

    s = pd.Series(values, index=pd.DatetimeIndex(times), dtype="float64")
    s = s[~s.index.duplicated(keep="last")]
    return s.sort_index()


def daily_returns(equity: pd.Series) -> np.ndarray:
    """Return pct_change between days with closed trades (ignoring flat days)."""
    if len(equity) < 2:
        return np.array([], dtype="float64")
    daily = equity.resample("1D").last().dropna()
    rets: np.ndarray = daily.pct_change().dropna().to_numpy(dtype="float64")
    return rets


def years_span(equity: pd.Series) -> float:
    """Trading-time span in years between the first and last equity points."""
    if len(equity) < 2:
        return 0.0
    span = float((equity.index[-1] - equity.index[0]).total_seconds())
    return max(span / (365.25 * 24 * 3600), 1e-9)


def max_drawdown(equity: pd.Series) -> Tuple[float, float]:
    """Return (max_dd_abs_dollars, max_dd_pct).

    `max_dd_pct` is expressed as a fraction (0.10 == 10%).
    """
    if len(equity) == 0:
        return 0.0, 0.0
    eq = equity.to_numpy(dtype="float64")
    peak = np.maximum.accumulate(eq)
    dd_abs = peak - eq
    dd_pct = np.where(peak > 0, dd_abs / peak, 0.0)
    return float(dd_abs.max()), float(dd_pct.max())


def _warn_small_sample(name: str, n: int) -> None:
    """Log a warning when the daily-return sample is too small to trust."""
    if n < MIN_RETURNS_FOR_RATIO:
        logger.warning(
            "%s computed on only %d daily returns (< %d) — "
            "treat the value as indicative, not statistically meaningful.",
            name,
            n,
            MIN_RETURNS_FOR_RATIO,
        )


def sharpe_ratio(equity: pd.Series, risk_free: float = 0.0, periods_per_year: int = 252) -> float:
    """Annualised Sharpe based on **daily** equity returns.

    `risk_free` is the annual risk-free rate as a fraction (e.g. 0.02 for 2%).
    Logs a warning when the sample size is below
    :data:`MIN_RETURNS_FOR_RATIO` — the value is still returned, but the
    caller / UI should flag it as low-confidence (TZ Phase 4 / D2).
    """
    rets = daily_returns(equity)
    if len(rets) < 2:
        return 0.0
    _warn_small_sample("sharpe_ratio", len(rets))
    daily_rf = risk_free / periods_per_year
    excess = rets - daily_rf
    mu = float(np.mean(excess))
    sd = float(np.std(excess, ddof=1))
    if sd <= 0:
        return 0.0
    return float(mu / sd * np.sqrt(periods_per_year))


def sortino_ratio(equity: pd.Series, risk_free: float = 0.0, periods_per_year: int = 252) -> float:
    """Annualised Sortino — only downside volatility penalised.

    Logs a warning when the sample size is below
    :data:`MIN_RETURNS_FOR_RATIO` (TZ Phase 4 / D2).
    """
    rets = daily_returns(equity)
    if len(rets) < 2:
        return 0.0
    _warn_small_sample("sortino_ratio", len(rets))
    daily_rf = risk_free / periods_per_year
    excess = rets - daily_rf
    downside = excess[excess < 0]
    if len(downside) == 0:
        # All-positive returns → undefined. Choose 0 to keep output finite.
        return 0.0
    dd = float(np.sqrt(np.mean(downside**2)))
    if dd <= 0:
        return 0.0
    return float(float(np.mean(excess)) / dd * np.sqrt(periods_per_year))


def calmar_ratio(equity: pd.Series, initial_equity: float) -> float:
    """Annualised return divided by max drawdown (fraction).

    Returns ``NaN`` for the genuinely undefined case where the strategy
    lost money over the window — ``(final/initial) ≤ 0`` means
    ``(negative)^(1/years)`` is complex / NaN, so a finite Calmar is
    meaningless. Previously we silently flattened this to ``0.0``,
    making a *losing* strategy look identical to a flat one (TZ Phase 4
    / D1). Other guards still return ``0.0`` to keep numeric output:

      • No equity history (< 2 points) → ``0.0``
      • Window < ~1.2 months (``years < 0.1``) → ``0.0``
      • ``initial_equity ≤ 0`` → ``0.0``
      • Zero drawdown — undefined / infinite — ``0.0``
    """
    if len(equity) < 2:
        return 0.0
    years = years_span(equity)
    if years < 0.1:
        return 0.0
    final = float(equity.iloc[-1])
    if initial_equity <= 0:
        return 0.0
    total_return = final / initial_equity
    if total_return <= 0:
        return float("nan")
    annual = total_return ** (1.0 / years) - 1.0
    _dd_abs, dd_pct = max_drawdown(equity)
    if dd_pct <= 0:
        return 0.0
    return float(annual / dd_pct)


__all__ = [
    "build_equity_curve",
    "daily_returns",
    "years_span",
    "max_drawdown",
    "sharpe_ratio",
    "sortino_ratio",
    "calmar_ratio",
    "MIN_RETURNS_FOR_RATIO",
]
