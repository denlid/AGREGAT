"""Sprint 4.1 — Trade-level summary metrics.

Twelve simple-but-essential per-trade aggregates that the equity-side
metrics don't already cover: win/loss rate, payoff, expectancy,
best/worst, gross/net profit and commission / spread drag.

Each function consumes either a numpy array of per-trade PnL or an
iterable of :class:`engine.models.Trade` (used to compute the cost
drags). The behaviour mirrors :mod:`engine.metrics.distribution` so
callers can pick a metric from one place.
"""

from __future__ import annotations

from typing import Iterable, Sequence, Union

import numpy as np
from numpy.typing import NDArray

PnLArray = Union[NDArray[np.floating], Sequence[float]]


def num_trades(pnls: PnLArray) -> int:
    """Number of trades in the array."""
    return int(np.asarray(pnls, dtype="float64").size)


def win_rate(pnls: PnLArray) -> float:
    """Fraction of trades with strictly positive PnL."""
    arr = np.asarray(pnls, dtype="float64")
    if arr.size == 0:
        return 0.0
    return float((arr > 0).mean())


def loss_rate(pnls: PnLArray) -> float:
    """Fraction of trades with strictly negative PnL."""
    arr = np.asarray(pnls, dtype="float64")
    if arr.size == 0:
        return 0.0
    return float((arr < 0).mean())


def avg_win(pnls: PnLArray) -> float:
    """Mean PnL of winning trades. ``0.0`` for the all-loser case."""
    arr = np.asarray(pnls, dtype="float64")
    wins = arr[arr > 0]
    return float(wins.mean()) if wins.size else 0.0


def avg_loss(pnls: PnLArray) -> float:
    """Mean PnL of losing trades (negative number). ``0.0`` if no losers."""
    arr = np.asarray(pnls, dtype="float64")
    losses = arr[arr < 0]
    return float(losses.mean()) if losses.size else 0.0


def avg_trade(pnls: PnLArray) -> float:
    """Arithmetic mean PnL per trade."""
    arr = np.asarray(pnls, dtype="float64")
    return float(arr.mean()) if arr.size else 0.0


def best_trade(pnls: PnLArray) -> float:
    """Largest single-trade PnL."""
    arr = np.asarray(pnls, dtype="float64")
    return float(arr.max()) if arr.size else 0.0


def worst_trade(pnls: PnLArray) -> float:
    """Smallest single-trade PnL."""
    arr = np.asarray(pnls, dtype="float64")
    return float(arr.min()) if arr.size else 0.0


def payoff_ratio(pnls: PnLArray) -> float:
    """``avg_win / |avg_loss|``. Returns ``0.0`` when the denominator is zero."""
    aw = avg_win(pnls)
    al = avg_loss(pnls)
    if al >= 0:
        return 0.0
    return float(aw / abs(al))


def expectancy(pnls: PnLArray) -> float:
    """``win_rate * avg_win + loss_rate * avg_loss``.

    Equivalent to :func:`avg_trade` for non-empty inputs but kept
    explicit because the documentation phrasing is ubiquitous in
    trading literature.
    """
    arr = np.asarray(pnls, dtype="float64")
    if arr.size == 0:
        return 0.0
    return float(arr.mean())


def gross_profit(pnls: PnLArray) -> float:
    """Sum of positive PnL trades."""
    arr = np.asarray(pnls, dtype="float64")
    return float(arr[arr > 0].sum()) if arr.size else 0.0


def gross_loss(pnls: PnLArray) -> float:
    """Sum of negative PnL trades (returned as a *negative* number)."""
    arr = np.asarray(pnls, dtype="float64")
    return float(arr[arr < 0].sum()) if arr.size else 0.0


def net_profit(pnls: PnLArray) -> float:
    """Total PnL across all trades."""
    arr = np.asarray(pnls, dtype="float64")
    return float(arr.sum()) if arr.size else 0.0


def commission_drag_pct(trades: Iterable[object]) -> float:
    """Fraction of *gross* PnL eaten by commission costs.

    Mirrors :func:`engine.metrics.distribution.slippage_drag_pct`. The
    trade is expected to expose ``pnl`` (net of all costs) and
    ``commission`` attributes — Sprint 1 :class:`Trade` already does.
    """
    total_comm = 0.0
    total_gross = 0.0
    for t in trades:
        comm = float(getattr(t, "commission", 0.0))
        slip = float(getattr(t, "slippage_cost", 0.0))
        spread = float(getattr(t, "spread_cost", 0.0))
        net = float(getattr(t, "pnl", 0.0))
        total_comm += comm
        # gross = net + all costs
        total_gross += net + comm + slip + spread
    if total_gross <= 0:
        return 0.0
    return float(total_comm / total_gross)


def spread_drag_pct(trades: Iterable[object]) -> float:
    """Fraction of *gross* PnL eaten by spread costs."""
    total_spread = 0.0
    total_gross = 0.0
    for t in trades:
        comm = float(getattr(t, "commission", 0.0))
        slip = float(getattr(t, "slippage_cost", 0.0))
        spread = float(getattr(t, "spread_cost", 0.0))
        net = float(getattr(t, "pnl", 0.0))
        total_spread += spread
        total_gross += net + comm + slip + spread
    if total_gross <= 0:
        return 0.0
    return float(total_spread / total_gross)


__all__ = [
    "num_trades",
    "win_rate",
    "loss_rate",
    "avg_win",
    "avg_loss",
    "avg_trade",
    "best_trade",
    "worst_trade",
    "payoff_ratio",
    "expectancy",
    "gross_profit",
    "gross_loss",
    "net_profit",
    "commission_drag_pct",
    "spread_drag_pct",
]
