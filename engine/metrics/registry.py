"""Sprint 4 — Metric registry.

Single source of truth that maps a stable string ID (e.g.
``"sharpe"``, ``"cagr"``, ``"cvar_95"``) to the callable that computes
it. The registry is intentionally minimal — each entry exposes the
metric ID, a human-readable display name, the function pointer and a
short summary so the upcoming pivot UI can surface the catalogue
without hand-rolling a switch statement.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from engine.analytics.equity_curve import calmar_ratio, max_drawdown, sharpe_ratio, sortino_ratio
from engine.metrics.distribution import (
    cvar_95,
    cvar_99,
    hold_time_distribution,
    profit_factor,
    r_multiple_stats,
    recovery_factor,
    slippage_drag_pct,
    streak_analysis,
    var_95,
    var_99,
)
from engine.metrics.pain import gain_to_pain, tail_ratio
from engine.metrics.ratios import (
    cagr,
    information_ratio,
    mar_ratio,
    skew_adjusted_sharpe,
    sterling_ratio,
    treynor_ratio,
)
from engine.metrics.trade_stats import (
    avg_loss,
    avg_trade,
    avg_win,
    best_trade,
    commission_drag_pct,
    expectancy,
    gross_loss,
    gross_profit,
    loss_rate,
    net_profit,
    num_trades,
    payoff_ratio,
    spread_drag_pct,
    win_rate,
    worst_trade,
)
from engine.analytics.risk_metrics import (
    omega_ratio,
    pain_index,
    pain_ratio,
    risk_of_ruin,
    time_under_water,
    ulcer_index,
)


@dataclass(frozen=True)
class MetricSpec:
    """Single entry in the metric catalogue."""

    id: str
    name: str
    func: Callable[..., Any]
    summary: str


# The display order is curated for the Sprint 4 DoD review (40
# metrics). Newer entries can be appended without breaking lookups.
METRIC_REGISTRY: dict[str, MetricSpec] = {
    # -- Core ratios (4.1) ------------------------------------------------
    "cagr": MetricSpec("cagr", "CAGR", cagr, "Compound annual growth rate."),
    "sharpe": MetricSpec(
        "sharpe", "Sharpe", sharpe_ratio, "Annualised Sharpe on daily equity returns."
    ),
    "skew_adjusted_sharpe": MetricSpec(
        "skew_adjusted_sharpe",
        "Sharpe (skew-adj)",
        skew_adjusted_sharpe,
        "Pezier–White skew-adjusted Sharpe.",
    ),
    "sortino": MetricSpec(
        "sortino", "Sortino", sortino_ratio, "Annualised Sortino — downside vol only."
    ),
    "calmar": MetricSpec(
        "calmar",
        "Calmar",
        calmar_ratio,
        "Annualised return / max drawdown.",
    ),
    "omega": MetricSpec("omega", "Omega", omega_ratio, "Σ gains over threshold / Σ losses below."),
    "treynor": MetricSpec(
        "treynor",
        "Treynor",
        treynor_ratio,
        "Excess return per unit of systematic risk (beta).",
    ),
    "information_ratio": MetricSpec(
        "information_ratio",
        "Information Ratio",
        information_ratio,
        "Annualised active return / tracking error.",
    ),
    "sterling": MetricSpec(
        "sterling",
        "Sterling",
        sterling_ratio,
        "CAGR / mean of N worst drawdowns (default N=3).",
    ),
    "mar": MetricSpec("mar", "MAR", mar_ratio, "CAGR / max-drawdown fraction."),
    # -- Pain family (4.1) ------------------------------------------------
    "pain_index": MetricSpec("pain_index", "Pain Index", pain_index, "Mean |percent drawdown|."),
    "pain_ratio": MetricSpec(
        "pain_ratio", "Pain Ratio", pain_ratio, "Annualised return / Pain Index."
    ),
    "ulcer_index": MetricSpec("ulcer_index", "Ulcer Index", ulcer_index, "RMS percent drawdown."),
    "tail_ratio": MetricSpec(
        "tail_ratio",
        "Tail Ratio",
        tail_ratio,
        "|95th-pctile gain| / |5th-pctile loss|.",
    ),
    "gain_to_pain": MetricSpec(
        "gain_to_pain",
        "Gain to Pain",
        gain_to_pain,
        "Σ returns / Σ |losses| (Schwager).",
    ),
    # -- Distribution (4.1) -----------------------------------------------
    "var_95": MetricSpec("var_95", "VaR (95%)", var_95, "5%-tail Value-at-Risk."),
    "var_99": MetricSpec("var_99", "VaR (99%)", var_99, "1%-tail Value-at-Risk."),
    "cvar_95": MetricSpec("cvar_95", "CVaR (95%)", cvar_95, "5%-tail Expected Shortfall."),
    "cvar_99": MetricSpec("cvar_99", "CVaR (99%)", cvar_99, "1%-tail Expected Shortfall."),
    "r_multiple_stats": MetricSpec(
        "r_multiple_stats",
        "R-Multiple Stats",
        r_multiple_stats,
        "Mean / median / std / histogram of trade R.",
    ),
    "streak_analysis": MetricSpec(
        "streak_analysis",
        "Streak Analysis",
        streak_analysis,
        "Longest / current win + loss streaks.",
    ),
    "hold_time_distribution": MetricSpec(
        "hold_time_distribution",
        "Hold-Time Dist.",
        hold_time_distribution,
        "Trade hold-time mean / median / quartiles.",
    ),
    "slippage_drag_pct": MetricSpec(
        "slippage_drag_pct",
        "Slippage Drag",
        slippage_drag_pct,
        "Fraction of gross PnL eaten by slippage.",
    ),
    "profit_factor": MetricSpec(
        "profit_factor",
        "Profit Factor",
        profit_factor,
        "Σ profits / |Σ losses|.",
    ),
    "recovery_factor": MetricSpec(
        "recovery_factor",
        "Recovery Factor",
        recovery_factor,
        "Total return $ / max-drawdown $.",
    ),
    # -- Trade-level summary (4.1 — 12 entries) --------------------------
    "num_trades": MetricSpec("num_trades", "Trades", num_trades, "Number of closed trades."),
    "win_rate": MetricSpec(
        "win_rate", "Win Rate", win_rate, "Fraction of strictly positive trades."
    ),
    "loss_rate": MetricSpec(
        "loss_rate", "Loss Rate", loss_rate, "Fraction of strictly negative trades."
    ),
    "avg_win": MetricSpec("avg_win", "Avg Win", avg_win, "Mean PnL of winning trades."),
    "avg_loss": MetricSpec(
        "avg_loss", "Avg Loss", avg_loss, "Mean PnL of losing trades (negative)."
    ),
    "avg_trade": MetricSpec("avg_trade", "Avg Trade", avg_trade, "Arithmetic mean PnL per trade."),
    "best_trade": MetricSpec("best_trade", "Best Trade", best_trade, "Largest single-trade PnL."),
    "worst_trade": MetricSpec(
        "worst_trade", "Worst Trade", worst_trade, "Smallest single-trade PnL."
    ),
    "payoff_ratio": MetricSpec(
        "payoff_ratio", "Payoff Ratio", payoff_ratio, "avg_win / |avg_loss|."
    ),
    "expectancy": MetricSpec(
        "expectancy", "Expectancy", expectancy, "win_rate*avg_win + loss_rate*avg_loss."
    ),
    "gross_profit": MetricSpec(
        "gross_profit", "Gross Profit", gross_profit, "Σ of positive trades."
    ),
    "gross_loss": MetricSpec(
        "gross_loss", "Gross Loss", gross_loss, "Σ of negative trades (negative)."
    ),
    "net_profit": MetricSpec(
        "net_profit", "Net Profit", net_profit, "Total PnL across all trades."
    ),
    "commission_drag_pct": MetricSpec(
        "commission_drag_pct",
        "Commission Drag",
        commission_drag_pct,
        "Fraction of gross PnL eaten by commission.",
    ),
    "spread_drag_pct": MetricSpec(
        "spread_drag_pct",
        "Spread Drag",
        spread_drag_pct,
        "Fraction of gross PnL eaten by spread.",
    ),
    # -- Drawdown / equity helpers ---------------------------------------
    "max_drawdown": MetricSpec(
        "max_drawdown",
        "Max Drawdown",
        max_drawdown,
        "(absolute, fractional) peak-to-trough drawdown.",
    ),
    "risk_of_ruin": MetricSpec(
        "risk_of_ruin",
        "Risk of Ruin",
        risk_of_ruin,
        "Vince closed-form RoR estimate.",
    ),
    "time_under_water": MetricSpec(
        "time_under_water",
        "Time Under Water",
        time_under_water,
        "% bars / max-run / mean-run underwater.",
    ),
}


def list_metrics() -> list[str]:
    """Return the registered metric IDs in the catalogue's display order."""
    return list(METRIC_REGISTRY.keys())


__all__ = ["MetricSpec", "METRIC_REGISTRY", "list_metrics"]
