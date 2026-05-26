"""Sprint 4.1 — Core ratio metrics.

CAGR, skew-adjusted Sharpe, Treynor, Information Ratio, Sterling, MAR.

All functions accept a pandas equity series (timestamp index, equity
value) consistent with :func:`engine.equity_curve.build_equity_curve`.
``years_span`` and ``daily_returns`` are reused so the day-to-day
contract (resample ``"1D"``, drop flat days) matches the rest of the
codebase.

References for unit testing:

- CAGR: ``(equity[-1] / equity[0]) ** (1 / years) - 1``
- Skew-adjusted Sharpe: Pezier–White (2008) "On the Risk Adjusted
  Performance of Hedge Funds" — Sharpe corrected for the third moment
  so a heavy-left-tail strategy is penalised vs. a Gaussian one.
- Treynor: ``(R_p - R_f) / beta`` where ``beta`` is portfolio vs.
  benchmark.
- Information Ratio: ``mean(R_p - R_b) / std(R_p - R_b)`` annualised.
- Sterling: ``CAGR / mean(N largest drawdowns)`` (we average top N
  drawdowns, default ``N = 3``).
- MAR: ``CAGR / max_drawdown_pct``.
"""

from __future__ import annotations

import math
from typing import Sequence

import numpy as np
import pandas as pd

from engine.analytics.equity_curve import (
    daily_returns,
    max_drawdown,
    sharpe_ratio,
    years_span,
)


def cagr(equity: pd.Series) -> float:
    """Compound annual growth rate.

    Returns ``0.0`` if the series is too short or starts non-positive,
    and ``NaN`` if the strategy ended at or below zero (negative or
    zero compound returns are mathematically undefined under
    ``y^(1/T)``).
    """
    if len(equity) < 2:
        return 0.0
    initial = float(equity.iloc[0])
    final = float(equity.iloc[-1])
    if initial <= 0:
        return 0.0
    if final <= 0:
        return float("nan")
    yrs = years_span(equity)
    if yrs < 1e-6:
        return 0.0
    return float((final / initial) ** (1.0 / yrs) - 1.0)


def skew_adjusted_sharpe(
    equity: pd.Series,
    *,
    risk_free: float = 0.0,
    periods_per_year: int = 252,
) -> float:
    """Sharpe ratio adjusted for sample skewness (Pezier–White).

    Penalises heavy left tails and rewards heavy right tails::

        SR_adj = SR + (skew / 6) * SR^2

    where SR is the conventional annualised Sharpe. When skew is zero
    this returns the same value as :func:`engine.equity_curve.sharpe_ratio`.

    Returns ``0.0`` for series with fewer than two daily returns.
    """
    sr = sharpe_ratio(equity, risk_free=risk_free, periods_per_year=periods_per_year)
    rets = daily_returns(equity)
    if rets.size < 3 or sr == 0.0:
        return float(sr)
    # Sample skewness via Fisher-Pearson g1 (numerator-only — the
    # second-order term is small for the strategy distributions we
    # care about, and matches Pezier–White's formula closely enough
    # for ranking purposes).
    mu = float(rets.mean())
    sd = float(rets.std(ddof=1))
    if sd <= 0:
        return float(sr)
    skew = float(np.mean(((rets - mu) / sd) ** 3))
    return float(sr + (skew / 6.0) * sr * sr)


def treynor_ratio(
    equity: pd.Series,
    benchmark: pd.Series,
    *,
    risk_free: float = 0.0,
    periods_per_year: int = 252,
) -> float:
    """Treynor ratio: excess return per unit of systematic risk.

    Both ``equity`` and ``benchmark`` are equity series (price levels)
    sampled on the same calendar — we resample each to daily, align on
    the intersection, and compute beta vs. excess returns.

    Returns ``0.0`` when beta is non-positive (denominator undefined)
    or when fewer than two overlapping daily returns are available.
    """
    if len(equity) < 2 or len(benchmark) < 2:
        return 0.0
    rp = daily_returns(equity)
    rb = daily_returns(benchmark)
    if rp.size < 2 or rb.size < 2:
        return 0.0
    # Align on the overlap (we require the caller to pass series with
    # the same time-base; this just clips to the shorter one).
    n = min(rp.size, rb.size)
    rp = rp[-n:]
    rb = rb[-n:]
    daily_rf = risk_free / periods_per_year
    excess_p = rp - daily_rf
    excess_b = rb - daily_rf
    var_b = float(np.var(excess_b, ddof=1))
    if var_b <= 0:
        return 0.0
    beta = float(np.cov(excess_p, excess_b, ddof=1)[0, 1] / var_b)
    if beta <= 0:
        return 0.0
    annual_excess = float(np.mean(excess_p)) * periods_per_year
    return float(annual_excess / beta)


def information_ratio(
    equity: pd.Series,
    benchmark: pd.Series,
    *,
    periods_per_year: int = 252,
) -> float:
    """Information ratio: annualised active-return mean / tracking error.

    ``active_return = R_portfolio - R_benchmark`` per day.
    """
    if len(equity) < 2 or len(benchmark) < 2:
        return 0.0
    rp = daily_returns(equity)
    rb = daily_returns(benchmark)
    if rp.size < 2 or rb.size < 2:
        return 0.0
    n = min(rp.size, rb.size)
    active = rp[-n:] - rb[-n:]
    sd = float(active.std(ddof=1))
    if sd <= 0:
        return 0.0
    return float(active.mean() / sd * math.sqrt(periods_per_year))


def sterling_ratio(equity: pd.Series, *, n_drawdowns: int = 3) -> float:
    """Sterling ratio: CAGR / mean of the N worst drawdowns (as fractions).

    The ``n_drawdowns`` parameter is the number of worst drawdown
    *peaks* to average. With insufficient drawdown data we fall back
    to the single max drawdown (so the value matches
    ``cagr / max_drawdown_pct``).

    Returns ``0.0`` when the average drawdown is zero, ``NaN`` when
    CAGR is undefined.
    """
    g = cagr(equity)
    if math.isnan(g):
        return float("nan")
    if g == 0.0:
        return 0.0
    dds = _largest_drawdowns(equity, n_drawdowns)
    if not dds:
        return 0.0
    avg_dd = float(np.mean(dds))
    if avg_dd <= 0:
        return 0.0
    return float(g / avg_dd)


def mar_ratio(equity: pd.Series) -> float:
    """Managed Account Ratio: CAGR / max-drawdown (fraction).

    Returns ``0.0`` for zero / undefined drawdown, ``NaN`` for an
    undefined CAGR.
    """
    g = cagr(equity)
    if math.isnan(g):
        return float("nan")
    _, dd_pct = max_drawdown(equity)
    if dd_pct <= 0:
        return 0.0
    return float(g / dd_pct)


def _largest_drawdowns(equity: pd.Series, n: int) -> Sequence[float]:
    """Return the ``n`` largest peak-to-trough drawdown fractions.

    A drawdown is closed when equity makes a new all-time-high. The
    series is scanned once; we record the max-drawdown reached during
    each underwater run. If fewer than ``n`` distinct drawdowns exist
    we return what we have (possibly an empty list for a strictly
    monotonic series).
    """
    if len(equity) < 2 or n <= 0:
        return []
    arr = equity.to_numpy(dtype="float64")
    peak = arr[0]
    cur_dd = 0.0
    runs: list[float] = []
    for v in arr[1:]:
        if v > peak:
            if cur_dd > 0.0:
                runs.append(cur_dd)
                cur_dd = 0.0
            peak = v
        else:
            dd = (peak - v) / peak if peak > 0 else 0.0
            if dd > cur_dd:
                cur_dd = dd
    if cur_dd > 0.0:
        runs.append(cur_dd)
    runs.sort(reverse=True)
    return runs[:n]


__all__ = [
    "cagr",
    "skew_adjusted_sharpe",
    "treynor_ratio",
    "information_ratio",
    "sterling_ratio",
    "mar_ratio",
]
