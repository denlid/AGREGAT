"""
Risk metrics that need an explicit equity series — kept separate from
`equity_curve.py` so the Phase-4 additions stay localized.

Functions
---------
- risk_of_ruin            : analytic Kelly-style probability of hitting `ruin_pct` drawdown
- time_under_water        : per-step indicator + summary (max + average run length, % bars)
- ulcer_index             : RMS of percent drawdown
- pain_index              : average percent drawdown
- pain_ratio              : annualised return / pain_index
- value_at_risk           : VaR over per-trade PnL distribution
- conditional_var         : CVaR / Expected Shortfall over per-trade PnL
- omega_ratio             : Omega ratio over per-trade PnL

All equity-curve inputs are numpy arrays / pandas Series of *equity values*
(post-trade PnL applied). Trade-distribution inputs are 1-D arrays of per-trade
PnL.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Union

import numpy as np
from numpy.typing import NDArray

PnLArray = Union[NDArray[np.floating], list[float], tuple[float, ...]]


# ----------------------------------------------------------------------------
# Risk of Ruin
# ----------------------------------------------------------------------------
def risk_of_ruin(
    win_rate: float,
    avg_win: float,
    avg_loss: float,
    *,
    risk_per_trade_pct: float = 1.0,
    ruin_pct: float = 0.5,
) -> float:
    """Analytic risk-of-ruin using the Vince closed-form approximation.

    Parameters
    ----------
    win_rate
        Probability of a winning trade in [0, 1].
    avg_win, avg_loss
        Average dollar size of winning / losing trades (``avg_loss > 0``).
    risk_per_trade_pct
        Percent of starting equity risked per trade (e.g. ``1.0`` for 1 %).
    ruin_pct
        Fraction of starting equity considered "ruin" — default ``0.5`` (a
        50 % drawdown).

    Returns
    -------
    Probability in [0, 1] that the strategy's equity drops by ``ruin_pct``
    of its starting value, given infinitely many bets.

    Notes
    -----
    Closed-form::

        edge = (win_rate * payoff - (1 - win_rate)) / payoff
        RoR  = ((1 - edge) / (1 + edge)) ** N

    where ``payoff = avg_win / avg_loss`` and ``N`` is the number of risk-units
    in the bankroll up to the ruin threshold (``ruin_pct / risk_per_trade_pct``).

    The formula assumes losses of constant magnitude ``risk_per_trade_pct``;
    for variable position sizing this is a reasonable upper-bound estimate.
    """
    if win_rate <= 0 or avg_loss <= 0 or risk_per_trade_pct <= 0:
        return 1.0
    if win_rate >= 1.0:
        return 0.0
    payoff = avg_win / avg_loss
    if payoff <= 0:
        return 1.0

    edge = win_rate - (1.0 - win_rate) / payoff
    if edge <= 0:
        return 1.0
    if edge >= 1.0:
        return 0.0

    n_units = max(1, int(round(ruin_pct * 100.0 / risk_per_trade_pct)))
    base = (1.0 - edge) / (1.0 + edge)
    ror = base**n_units
    return float(min(1.0, max(0.0, ror)))


# ----------------------------------------------------------------------------
# Time under water
# ----------------------------------------------------------------------------
@dataclass(frozen=True)
class TimeUnderWaterStats:
    """Summary of underwater periods on an equity curve."""

    pct_bars_underwater: float  # fraction of bars where equity < running max
    max_run_bars: int  # longest consecutive underwater run (bar count)
    avg_run_bars: float  # mean length of underwater runs
    n_runs: int


def time_under_water(equity: np.ndarray) -> TimeUnderWaterStats:
    """Compute time-under-water statistics for an equity series.

    A bar is "underwater" iff equity < the running max up to that bar.
    """
    arr = np.asarray(equity, dtype=float)
    if arr.size == 0:
        return TimeUnderWaterStats(0.0, 0, 0.0, 0)
    running_max = np.maximum.accumulate(arr)
    underwater = arr < running_max
    pct = float(underwater.mean()) if arr.size > 0 else 0.0

    runs = []
    cur = 0
    for bar in underwater:
        if bar:
            cur += 1
        else:
            if cur > 0:
                runs.append(cur)
            cur = 0
    if cur > 0:
        runs.append(cur)

    if not runs:
        return TimeUnderWaterStats(pct, 0, 0.0, 0)
    return TimeUnderWaterStats(
        pct_bars_underwater=pct,
        max_run_bars=int(max(runs)),
        avg_run_bars=float(sum(runs) / len(runs)),
        n_runs=len(runs),
    )


# ----------------------------------------------------------------------------
# Ulcer / Pain
# ----------------------------------------------------------------------------
def ulcer_index(equity: np.ndarray) -> float:
    """RMS of percent drawdown across the series. Penalises both depth and duration."""
    arr = np.asarray(equity, dtype=float)
    if arr.size == 0:
        return 0.0
    running_max = np.maximum.accumulate(arr)
    dd = np.where(running_max > 0, (arr - running_max) / running_max * 100.0, 0.0)
    return float(math.sqrt(np.mean(dd * dd)))


def pain_index(equity: np.ndarray) -> float:
    """Mean of |percent drawdown| across the series."""
    arr = np.asarray(equity, dtype=float)
    if arr.size == 0:
        return 0.0
    running_max = np.maximum.accumulate(arr)
    dd = np.where(running_max > 0, (running_max - arr) / running_max * 100.0, 0.0)
    return float(np.mean(dd))


def pain_ratio(equity: np.ndarray, *, periods_per_year: float = 252.0) -> float:
    """Annualised return / pain_index."""
    arr = np.asarray(equity, dtype=float)
    if arr.size < 2:
        return 0.0
    pi = pain_index(arr)
    if pi <= 0:
        return 0.0
    total_return = (arr[-1] / arr[0] - 1.0) * 100.0
    years = max(1e-6, len(arr) / periods_per_year)
    annual = total_return / years
    return float(annual / pi)


# ----------------------------------------------------------------------------
# Per-trade PnL distribution metrics (VaR / CVaR / Omega)
# ----------------------------------------------------------------------------
def value_at_risk(pnls: PnLArray, confidence: float = 0.95) -> float:
    """Value-at-Risk on a per-trade PnL distribution.

    Returns the (1 - confidence)-percentile of ``pnls`` (typically a negative
    number for a profitable strategy, representing the tail loss threshold).
    """
    arr = np.asarray(pnls, dtype=float)
    if arr.size == 0:
        return 0.0
    return float(np.percentile(arr, (1.0 - confidence) * 100.0))


def conditional_var(pnls: PnLArray, confidence: float = 0.95) -> float:
    """Conditional Value-at-Risk (Expected Shortfall) on per-trade PnL.

    Mean of all PnL values at-or-below the VaR threshold.
    """
    arr = np.asarray(pnls, dtype=float)
    if arr.size == 0:
        return 0.0
    threshold = float(np.percentile(arr, (1.0 - confidence) * 100.0))
    tail = arr[arr <= threshold]
    if tail.size == 0:
        return 0.0
    return float(np.mean(tail))


def omega_ratio(pnls: PnLArray, threshold: float = 0.0) -> float:
    """Omega ratio on per-trade PnL: Σ gains over threshold / Σ losses below.

    Returns ``inf`` if there are gains but no losses, ``0.0`` if there are
    losses but no gains.
    """
    arr = np.asarray(pnls, dtype=float)
    if arr.size == 0:
        return 0.0
    gains = arr[arr > threshold] - threshold
    losses = threshold - arr[arr < threshold]
    if losses.size == 0:
        return float("inf") if gains.size > 0 else 0.0
    if gains.size == 0:
        return 0.0
    return float(np.sum(gains) / np.sum(losses))


__all__ = [
    "risk_of_ruin",
    "time_under_water",
    "ulcer_index",
    "pain_index",
    "pain_ratio",
    "value_at_risk",
    "conditional_var",
    "omega_ratio",
    "TimeUnderWaterStats",
]
