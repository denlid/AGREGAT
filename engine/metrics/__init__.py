"""Sprint 4 — Metrics catalogue (Phase 1 — 40 of 80).

This package is the canonical home for all post-trade performance
metrics. Pre-existing helpers in ``engine.equity_curve`` and
``engine.risk_metrics`` are re-exported here so callers can pick a
metric by name from a single location.

Layout
------
- :mod:`engine.metrics.ratios`        — CAGR, Sharpe (skew-adjusted),
  Sortino, Calmar, Omega, Treynor, Information, Sterling, MAR.
- :mod:`engine.metrics.pain`          — Pain Index / Ratio, Ulcer
  Index, Tail Ratio, Gain-to-Pain.
- :mod:`engine.metrics.distribution`  — CVaR (95/99), Expected
  Shortfall, R-multiple stats, streak analysis, hold-time
  distribution, slippage drag.
- :mod:`engine.metrics.cross_cuts`    — Trade derivations
  (regime / session / dow / hod / direction) and the
  ``cross_cut(metric, group_by, filter)`` API.
- :mod:`engine.metrics.registry`      — String → callable map used by
  the SDK and (eventually) the pivot UI.
"""

from __future__ import annotations

from engine.analytics.equity_curve import (
    calmar_ratio,
    daily_returns,
    max_drawdown,
    sharpe_ratio,
    sortino_ratio,
    years_span,
)
from engine.metrics.cross_cuts import (
    CrossCutResult,
    cross_cut,
    derive_trade_attributes,
    trade_direction,
    trade_dow,
    trade_hod,
    trade_regime,
    trade_session,
)
from engine.metrics.distribution import (
    HoldTimeStats,
    RMultipleStats,
    StreakStats,
    cvar_95,
    cvar_99,
    expected_shortfall,
    hold_time_distribution,
    profit_factor,
    r_multiple_stats,
    recovery_factor,
    slippage_drag_pct,
    streak_analysis,
    var_95,
    var_99,
)
from engine.metrics.pain import (
    gain_to_pain,
    tail_ratio,
)
from engine.metrics.ratios import (
    cagr,
    information_ratio,
    mar_ratio,
    skew_adjusted_sharpe,
    sterling_ratio,
    treynor_ratio,
)
from engine.metrics.registry import METRIC_REGISTRY, MetricSpec, list_metrics
from engine.analytics.risk_metrics import (
    conditional_var,
    omega_ratio,
    pain_index,
    pain_ratio,
    risk_of_ruin,
    time_under_water,
    ulcer_index,
    value_at_risk,
)

__all__ = [
    # Ratios (Sprint 4.1 — first 9)
    "cagr",
    "skew_adjusted_sharpe",
    "treynor_ratio",
    "information_ratio",
    "sterling_ratio",
    "mar_ratio",
    "sharpe_ratio",
    "sortino_ratio",
    "calmar_ratio",
    # Pain
    "pain_index",
    "pain_ratio",
    "ulcer_index",
    "tail_ratio",
    "gain_to_pain",
    # Distribution
    "value_at_risk",
    "var_95",
    "var_99",
    "conditional_var",
    "cvar_95",
    "cvar_99",
    "expected_shortfall",
    "omega_ratio",
    "r_multiple_stats",
    "RMultipleStats",
    "streak_analysis",
    "StreakStats",
    "hold_time_distribution",
    "HoldTimeStats",
    "slippage_drag_pct",
    "profit_factor",
    "recovery_factor",
    # Risk extras
    "risk_of_ruin",
    "time_under_water",
    # Cross-cuts
    "cross_cut",
    "CrossCutResult",
    "derive_trade_attributes",
    "trade_regime",
    "trade_session",
    "trade_dow",
    "trade_hod",
    "trade_direction",
    # Equity helpers
    "max_drawdown",
    "daily_returns",
    "years_span",
    # Registry
    "METRIC_REGISTRY",
    "MetricSpec",
    "list_metrics",
]
