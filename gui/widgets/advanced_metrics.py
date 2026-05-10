"""
Advanced Metrics — Quant-grade metrics that are correct on heterogeneous trade
streams (variable time-between-trades). Annualised metrics route through
`engine.equity_curve` so we never multiply a per-trade return by 252.
"""

from __future__ import annotations

from typing import List

import numpy as np

from engine.analytics.equity_curve import (
    build_equity_curve,
    daily_returns,
)
from engine.analytics.equity_curve import (
    calmar_ratio as _eq_calmar,
)
from engine.analytics.equity_curve import (
    sharpe_ratio as _eq_sharpe,
)
from engine.analytics.equity_curve import (
    sortino_ratio as _eq_sortino,
)
from engine.models.models import Trade
from engine.analytics.risk_metrics import (
    conditional_var as _eng_cvar,
)
from engine.analytics.risk_metrics import (
    omega_ratio as _eng_omega,
)
from engine.analytics.risk_metrics import (
    ulcer_index as _eng_ulcer,
)
from engine.analytics.risk_metrics import (
    value_at_risk as _eng_var,
)

# ---------------------------------------------------------------------------
# Annualised, equity-curve-based ratios (PREFERRED)
# ---------------------------------------------------------------------------


def calculate_sharpe_ratio(
    trades: List[Trade],
    initial_equity: float,
    risk_free_rate: float = 0.0,
) -> float:
    """Annualised Sharpe based on daily equity returns."""
    if not trades:
        return 0.0
    eq = build_equity_curve(trades, initial_equity)
    return _eq_sharpe(eq, risk_free=risk_free_rate)


def calculate_sortino_ratio(
    trades: List[Trade],
    initial_equity: float,
    risk_free_rate: float = 0.0,
) -> float:
    """Annualised Sortino based on daily equity returns (downside only)."""
    if not trades:
        return 0.0
    eq = build_equity_curve(trades, initial_equity)
    return _eq_sortino(eq, risk_free=risk_free_rate)


def calculate_calmar_ratio(
    trades: List[Trade],
    initial_equity: float,
    years: float = None,  # kept for backwards compat; unused
) -> float:
    """Annualised return divided by max drawdown."""
    if not trades:
        return 0.0
    eq = build_equity_curve(trades, initial_equity)
    return _eq_calmar(eq, initial_equity)


# ---------------------------------------------------------------------------
# Per-trade VaR / CVaR / Omega / Ulcer / Kappa
# ---------------------------------------------------------------------------


def calculate_var(trades: List[Trade], confidence: float = 0.95) -> float:
    """Value-at-Risk over per-trade PnL distribution. Delegates to engine."""
    if not trades:
        return 0.0
    pnls = np.array([t.pnl for t in trades], dtype="float64")
    return _eng_var(pnls, confidence)


def calculate_cvar(trades: List[Trade], confidence: float = 0.95) -> float:
    """Expected Shortfall over per-trade PnL distribution. Delegates to engine."""
    if not trades:
        return 0.0
    pnls = np.array([t.pnl for t in trades], dtype="float64")
    return _eng_cvar(pnls, confidence)


def calculate_omega_ratio(trades: List[Trade], threshold: float = 0.0) -> float:
    """Omega = Σ gains over threshold / Σ losses below threshold. Delegates to engine."""
    if not trades:
        return 0.0
    pnls = np.array([t.pnl for t in trades], dtype="float64")
    return _eng_omega(pnls, threshold)


def calculate_ulcer_index(trades: List[Trade], initial_equity: float) -> float:
    """Ulcer Index — RMS of percentage drawdowns. Delegates to engine."""
    if not trades:
        return 0.0
    eq = build_equity_curve(trades, initial_equity).to_numpy(dtype="float64")
    return _eng_ulcer(eq)


def calculate_kappa_3(
    trades: List[Trade],
    initial_equity: float,
    risk_free_rate: float = 0.0,
) -> float:
    """Kappa-3 over daily equity returns (annualised)."""
    if not trades:
        return 0.0
    eq = build_equity_curve(trades, initial_equity)
    rets = daily_returns(eq)
    if len(rets) < 2:
        return 0.0
    daily_rf = risk_free_rate / 252.0
    excess = rets - daily_rf
    downside = excess[excess < 0]
    if len(downside) == 0:
        return float("inf") if np.mean(excess) > 0 else 0.0
    lpm3 = float(np.mean((-downside) ** 3))
    if lpm3 <= 0:
        return 0.0
    annual = float(np.mean(excess)) * 252.0
    return annual / (lpm3 ** (1.0 / 3.0) * np.sqrt(252.0))


# ---------------------------------------------------------------------------
# Trade-stream descriptive stats
# ---------------------------------------------------------------------------


def analyze_trade_duration(trades: List[Trade]) -> dict:
    """Return duration distribution stats."""
    if not trades:
        return {}
    durations = np.array([t.duration_minutes for t in trades], dtype="float64")
    return {
        "mean_duration": float(np.mean(durations)),
        "median_duration": float(np.median(durations)),
        "min_duration": float(np.min(durations)),
        "max_duration": float(np.max(durations)),
        "std_duration": float(np.std(durations, ddof=1)) if len(durations) > 1 else 0.0,
        "p25_duration": float(np.percentile(durations, 25)),
        "p75_duration": float(np.percentile(durations, 75)),
    }


def calculate_consecutive_wins_losses(trades: List[Trade]) -> dict:
    """Streak statistics over the trade stream."""
    if not trades:
        return {}

    current_streak = 0
    current_type = None
    max_win_streak = 0
    max_loss_streak = 0
    win_streaks: List[int] = []
    loss_streaks: List[int] = []

    for t in trades:
        trade_type = "WIN" if t.pnl > 0 else "LOSS" if t.pnl < 0 else "BE"
        if trade_type == current_type:
            current_streak += 1
        else:
            if current_type == "WIN" and current_streak > 0:
                win_streaks.append(current_streak)
                max_win_streak = max(max_win_streak, current_streak)
            elif current_type == "LOSS" and current_streak > 0:
                loss_streaks.append(current_streak)
                max_loss_streak = max(max_loss_streak, current_streak)
            current_type = trade_type
            current_streak = 1

    if current_type == "WIN" and current_streak > 0:
        win_streaks.append(current_streak)
        max_win_streak = max(max_win_streak, current_streak)
    elif current_type == "LOSS" and current_streak > 0:
        loss_streaks.append(current_streak)
        max_loss_streak = max(max_loss_streak, current_streak)

    return {
        "max_win_streak": max_win_streak,
        "max_loss_streak": max_loss_streak,
        "avg_win_streak": float(np.mean(win_streaks)) if win_streaks else 0.0,
        "avg_loss_streak": float(np.mean(loss_streaks)) if loss_streaks else 0.0,
        "total_win_streaks": len(win_streaks),
        "total_loss_streaks": len(loss_streaks),
    }


def calculate_skipped_trades_stats(engine) -> dict:
    """Aggregate skipped-trade reasons recorded by `Context.calculate_lot`."""
    skipped = getattr(engine, "skipped_trades", None) or []
    counts = {
        "INVALID_SL": 0,
        "LOT_LIMIT": 0,
        "ABS_LOT_LIMIT": 0,
        "TRADING_HOURS": 0,
        "OTHER": 0,
    }
    for s in skipped:
        r = s.get("reason", "OTHER")
        if r in counts:
            counts[r] += 1
        else:
            counts["OTHER"] += 1
    return {
        "total_skipped": len(skipped),
        "skip_reason_lot_limit": counts["LOT_LIMIT"],
        "skip_reason_abs_lot_limit": counts["ABS_LOT_LIMIT"],
        "skip_reason_sl_distance": counts["INVALID_SL"],
        "skip_reason_trading_hours": counts["TRADING_HOURS"],
        "skip_reason_other": counts["OTHER"],
    }


__all__ = [
    "calculate_sharpe_ratio",
    "calculate_sortino_ratio",
    "calculate_calmar_ratio",
    "calculate_var",
    "calculate_cvar",
    "calculate_omega_ratio",
    "calculate_ulcer_index",
    "calculate_kappa_3",
    "analyze_trade_duration",
    "calculate_consecutive_wins_losses",
    "calculate_skipped_trades_stats",
]
