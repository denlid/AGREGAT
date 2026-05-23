"""Sprint 4.1 — Distribution-of-PnL metrics.

CVaR(95/99), Expected Shortfall alias, R-multiple stats, win/loss
streaks, hold-time distribution, slippage drag, profit factor and
recovery factor. The function names line up with the metric IDs we
register in :mod:`engine.metrics.registry`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence, Union

import numpy as np
from numpy.typing import NDArray

from engine.analytics.risk_metrics import conditional_var, value_at_risk

PnLArray = Union[NDArray[np.floating], Sequence[float]]


def _as_np(pnls: PnLArray) -> NDArray[np.floating]:
    """Coerce any accepted PnL container into a 1-D float64 NumPy array."""
    return np.asarray(pnls, dtype="float64")


# -- VaR / CVaR ----------------------------------------------------------------
def var_95(pnls: PnLArray) -> float:
    """5%-tail Value-at-Risk on per-trade PnL."""
    return value_at_risk(_as_np(pnls), confidence=0.95)


def var_99(pnls: PnLArray) -> float:
    """1%-tail Value-at-Risk on per-trade PnL."""
    return value_at_risk(_as_np(pnls), confidence=0.99)


def cvar_95(pnls: PnLArray) -> float:
    """5%-tail Conditional VaR (Expected Shortfall) on per-trade PnL."""
    return conditional_var(_as_np(pnls), confidence=0.95)


def cvar_99(pnls: PnLArray) -> float:
    """1%-tail Conditional VaR on per-trade PnL."""
    return conditional_var(_as_np(pnls), confidence=0.99)


def expected_shortfall(pnls: PnLArray, *, confidence: float = 0.95) -> float:
    """Alias for :func:`engine.risk_metrics.conditional_var`."""
    return conditional_var(_as_np(pnls), confidence=confidence)


# -- R-multiple stats ----------------------------------------------------------
@dataclass(frozen=True)
class RMultipleStats:
    """Summary stats over a per-trade R-multiple distribution."""

    count: int
    mean: float
    median: float
    std: float
    p25: float
    p75: float
    expectancy: float  # = mean R per trade
    histogram: tuple[tuple[float, float, int], ...]  # (lo, hi, count)


def r_multiple_stats(
    r_multiples: PnLArray,
    *,
    bins: Sequence[float] = (-3.0, -2.0, -1.0, 0.0, 1.0, 2.0, 3.0, 5.0),
) -> RMultipleStats:
    """Summary statistics over an array of trade R-multiples.

    The histogram bins are configurable; the default covers the
    canonical Van Tharp grid (``-3R … +5R``).
    """
    arr = np.asarray(r_multiples, dtype="float64")
    if arr.size == 0:
        return RMultipleStats(0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, ())
    edges = np.concatenate(([float("-inf")], np.asarray(bins, dtype="float64"), [float("inf")]))
    counts, _ = np.histogram(arr, bins=edges)
    histogram = tuple(
        (float(edges[i]), float(edges[i + 1]), int(counts[i])) for i in range(len(counts))
    )
    return RMultipleStats(
        count=int(arr.size),
        mean=float(arr.mean()),
        median=float(np.median(arr)),
        std=float(arr.std(ddof=1)) if arr.size > 1 else 0.0,
        p25=float(np.percentile(arr, 25)),
        p75=float(np.percentile(arr, 75)),
        expectancy=float(arr.mean()),
        histogram=histogram,
    )


# -- Streak analysis -----------------------------------------------------------
@dataclass(frozen=True)
class StreakStats:
    """Win/loss streak summary over an ordered sequence of trades."""

    longest_win: int
    longest_loss: int
    current: int  # +N if currently winning, -N if losing, 0 if last trade flat
    avg_win_streak: float
    avg_loss_streak: float


def streak_analysis(pnls: PnLArray) -> StreakStats:
    """Compute win / loss streak statistics from an *ordered* PnL array.

    A trade with PnL > 0 is a win, < 0 is a loss, == 0 is treated as
    flat (breaks the current streak without contributing to either
    side).
    """
    arr = np.asarray(pnls, dtype="float64")
    if arr.size == 0:
        return StreakStats(0, 0, 0, 0.0, 0.0)

    longest_win = 0
    longest_loss = 0
    win_streaks: list[int] = []
    loss_streaks: list[int] = []
    cur = 0  # +N winning, -N losing
    for v in arr:
        if v > 0:
            cur = cur + 1 if cur > 0 else 1
            if cur > longest_win:
                longest_win = cur
        elif v < 0:
            cur = cur - 1 if cur < 0 else -1
            if -cur > longest_loss:
                longest_loss = -cur
        else:
            if cur > 0:
                win_streaks.append(cur)
            elif cur < 0:
                loss_streaks.append(-cur)
            cur = 0
        # Record finalised streaks on sign flip too. The cleanest way
        # is a second pass: find runs of strictly positive / negative.
    # Second pass for "average streak length"
    sign = np.where(arr > 0, 1, np.where(arr < 0, -1, 0))
    win_streaks = []
    loss_streaks = []
    run = 0
    last = 0
    for s in sign:
        if s == last and s != 0:
            run += 1
        else:
            if last > 0 and run > 0:
                win_streaks.append(run)
            elif last < 0 and run > 0:
                loss_streaks.append(run)
            run = 1 if s != 0 else 0
            last = s
    if last > 0 and run > 0:
        win_streaks.append(run)
    elif last < 0 and run > 0:
        loss_streaks.append(run)

    return StreakStats(
        longest_win=int(longest_win),
        longest_loss=int(longest_loss),
        current=int(cur),
        avg_win_streak=float(np.mean(win_streaks)) if win_streaks else 0.0,
        avg_loss_streak=float(np.mean(loss_streaks)) if loss_streaks else 0.0,
    )


# -- Hold-time distribution ----------------------------------------------------
@dataclass(frozen=True)
class HoldTimeStats:
    """Hold-time summary in *minutes*."""

    count: int
    mean_min: float
    median_min: float
    p25_min: float
    p75_min: float
    min_min: float
    max_min: float


def hold_time_distribution(durations_minutes: Iterable[float]) -> HoldTimeStats:
    """Distribution stats over per-trade hold time in minutes."""
    arr = np.asarray(list(durations_minutes), dtype="float64")
    if arr.size == 0:
        return HoldTimeStats(0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    return HoldTimeStats(
        count=int(arr.size),
        mean_min=float(arr.mean()),
        median_min=float(np.median(arr)),
        p25_min=float(np.percentile(arr, 25)),
        p75_min=float(np.percentile(arr, 75)),
        min_min=float(arr.min()),
        max_min=float(arr.max()),
    )


# -- Slippage / profit-factor / recovery --------------------------------------
def slippage_drag_pct(trades: Iterable[object]) -> float:
    """Fraction of *gross* PnL eaten by slippage cost.

    ``gross = net_pnl + commission + slippage + spread`` so the result
    is comparable to :func:`commission_drag_pct` and
    :func:`spread_drag_pct`. Returns ``0.0`` when the gross PnL is zero
    or trades carry no slippage data.
    """
    total_slip = 0.0
    total_gross = 0.0
    for t in trades:
        slip = float(getattr(t, "slippage_cost", 0.0))
        comm = float(getattr(t, "commission", 0.0))
        spread = float(getattr(t, "spread_cost", 0.0))
        net = float(getattr(t, "pnl", 0.0))
        total_slip += slip
        total_gross += net + comm + slip + spread
    if total_gross <= 0:
        return 0.0
    return float(total_slip / total_gross)


def profit_factor(pnls: PnLArray) -> float:
    """Sum(profits) / |Sum(losses)|.

    Returns ``inf`` for a strategy with no losing trades and at least
    one winner; ``0.0`` for the all-losers case.
    """
    arr = np.asarray(pnls, dtype="float64")
    if arr.size == 0:
        return 0.0
    gains = float(arr[arr > 0].sum())
    losses = float(-arr[arr < 0].sum())
    if losses <= 0:
        return float("inf") if gains > 0 else 0.0
    if gains <= 0:
        return 0.0
    return float(gains / losses)


def recovery_factor(total_return_dollars: float, max_dd_dollars: float) -> float:
    """Total dollar return divided by absolute max drawdown.

    Returns ``0.0`` when ``max_dd_dollars`` is zero (no drawdown means
    the metric is undefined).
    """
    if max_dd_dollars <= 0:
        return 0.0
    return float(total_return_dollars / max_dd_dollars)


__all__ = [
    "var_95",
    "var_99",
    "cvar_95",
    "cvar_99",
    "expected_shortfall",
    "r_multiple_stats",
    "RMultipleStats",
    "streak_analysis",
    "StreakStats",
    "hold_time_distribution",
    "HoldTimeStats",
    "slippage_drag_pct",
    "profit_factor",
    "recovery_factor",
]
