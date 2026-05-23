"""Shared risk-adjusted metric implementations used by both trade-level
(equity_curve.py) and bar-level (bar_level_equity.py) analytics.

By centralising the core math here we ensure that Sharpe, Sortino and
Calmar are computed identically regardless of the sampling frequency.
"""

from __future__ import annotations

import numpy as np


def compute_sharpe(
    rets: np.ndarray,
    periods_per_year: float,
    risk_free: float = 0.0,
) -> float:
    """Annualised Sharpe ratio from a returns array."""
    if rets.size < 2:
        return 0.0
    per_period_rf = risk_free / periods_per_year
    excess = rets - per_period_rf
    mu = float(np.mean(excess))
    sd = float(np.std(excess, ddof=1))
    if sd <= 0:
        return 0.0
    return float(mu / sd * np.sqrt(periods_per_year))


def compute_sortino(
    rets: np.ndarray,
    periods_per_year: float,
    risk_free: float = 0.0,
) -> float:
    """Annualised Sortino ratio (downside deviation only) from returns."""
    if rets.size < 2:
        return 0.0
    per_period_rf = risk_free / periods_per_year
    excess = rets - per_period_rf
    downside = excess[excess < 0]
    if downside.size == 0:
        return 0.0
    dd = float(np.sqrt(np.mean(downside**2)))
    if dd <= 0:
        return 0.0
    return float(float(np.mean(excess)) / dd * np.sqrt(periods_per_year))


def compute_max_drawdown(eq: np.ndarray) -> tuple[float, float]:
    """Return (max_dd_abs, max_dd_pct) for an equity array."""
    if eq.size == 0:
        return 0.0, 0.0
    peak = np.maximum.accumulate(eq)
    dd_abs = peak - eq
    # Avoid div-by-zero if peak is 0 or negative
    dd_pct = np.where(peak > 0, dd_abs / peak, 0.0)
    return float(dd_abs.max()), float(dd_pct.max())


def compute_calmar(
    initial_equity: float,
    final_equity: float,
    max_dd_pct: float,
    years: float,
) -> float:
    """Annualised return / max-drawdown (Calmar ratio).

    Uses CAGR for the annual return. Returns NaN if the strategy lost
    money (total_return <= 0).
    """
    if years < 0.1 or initial_equity <= 0 or max_dd_pct <= 0:
        return 0.0
    total_return = final_equity / initial_equity
    if total_return <= 0:
        return float("nan")
    # CAGR calculation
    annual = total_return ** (1.0 / years) - 1.0
    return float(annual / max_dd_pct)
