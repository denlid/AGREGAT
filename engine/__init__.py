"""
Engine package — Core backtesting engine.
"""

import logging

logger = logging.getLogger("StrategyTester")
logger.setLevel(logging.DEBUG)

from engine.strategy.context import Context, Strategy, StrategyLoader
from engine.core.backtest_core import BacktestCore
from engine.data.data_loader import DataLoader

# NOTE: ``BacktestEngine`` is imported lazily via ``__getattr__`` so that
# ``import engine`` does not pull PySide6 into headless processes.
from engine.analytics.equity_curve import (
    build_equity_curve,
    calmar_ratio,
    daily_returns,
    max_drawdown,
    sharpe_ratio,
    sortino_ratio,
)
from engine.execution.execution import ExecutionCosts, ExecutionModel
from engine.persistence_pkg.integrity import IntegrityError, hash_archive_payload, hash_file, verify_archive
from engine.models.models import (
    SESSIONS,
    AssetConfig,
    PendingOrder,
    Position,
    Trade,
    get_session,
    is_eod,
    is_trading_hours,
    is_weekend,
)
from engine.persistence_pkg.persistence import (
    AGBK_EXTENSION,
    AGBK_FORMAT_VERSION,
    AGBK_VERSION,
    load_session,
    load_session_raw,
    save_session,
)
from engine.analytics.stats_accumulator import StatsAccumulator
from engine.data.tf_aggregator import TimeframeAggregator

# Heavy sub-modules are deferred via __getattr__ for faster startup.
# They are still listed in __all__ so `from engine import X` works.

__all__ = [
    "BacktestEngine",
    "BacktestCore",
    "Context",
    "Strategy",
    "StrategyLoader",
    "DataLoader",
    "TimeframeAggregator",
    "ExecutionModel",
    "ExecutionCosts",
    "StatsAccumulator",
    "AssetConfig",
    "PendingOrder",
    "Position",
    "Trade",
    "SESSIONS",
    "get_session",
    "is_trading_hours",
    "is_eod",
    "is_weekend",
    "logger",
    "build_equity_curve",
    "daily_returns",
    "max_drawdown",
    "sharpe_ratio",
    "sortino_ratio",
    "calmar_ratio",
    "save_session",
    "load_session",
    "load_session_raw",
    "AGBK_EXTENSION",
    "AGBK_FORMAT_VERSION",
    "AGBK_VERSION",
    # Integrity (Sprint 2.2)
    "IntegrityError",
    "hash_archive_payload",
    "hash_file",
    "verify_archive",
    # Phase 4 — Monte Carlo / optimizer / walk-forward / risk
    "MCResult",
    "bootstrap_pnl",
    "shock_pnl",
    "OptimizationRun",
    "OptimizationResult",
    "grid_search",
    "random_search",
    "WalkForwardSplit",
    "WalkForwardWindowResult",
    "WalkForwardResult",
    "make_splits",
    "walk_forward",
    "risk_of_ruin",
    "time_under_water",
    "ulcer_index",
    "pain_index",
    "pain_ratio",
    "value_at_risk",
    "conditional_var",
    "omega_ratio",
    "TimeUnderWaterStats",
    # Phase 5 — trade analytics
    "MAEMFEArrays",
    "mae_mfe_arrays",
    "RMultipleArrays",
    "r_multiples",
    "HourWeekdayHeatmap",
    "hour_weekday_heatmap",
    "MonthYearHeatmap",
    "month_year_heatmap",
    "DrawdownEpisode",
    "drawdown_durations",
    "AttributionRow",
    "exit_reason_attribution",
    "SlippageSimulationResult",
    "simulate_slippage",
    "StrategySummary",
    "summarise_run",
    "compare_runs",
    # Report export (Phase 7)
    "export_report",
]


# Mapping for lazy imports — module path → attribute names.
_LAZY_IMPORTS: dict[str, tuple[str, ...]] = {
    "engine.backtest_engine": ("BacktestEngine",),
    "engine.analytics.monte_carlo": ("MCResult", "bootstrap_pnl", "shock_pnl"),
    "engine.optimization.optimizer": ("OptimizationResult", "OptimizationRun", "grid_search", "random_search"),
    "engine.report_export": ("export_report",),
    "engine.analytics.risk_metrics": (
        "TimeUnderWaterStats",
        "conditional_var",
        "omega_ratio",
        "pain_index",
        "pain_ratio",
        "risk_of_ruin",
        "time_under_water",
        "ulcer_index",
        "value_at_risk",
    ),
    "engine.analytics.trade_analytics": (
        "AttributionRow",
        "DrawdownEpisode",
        "HourWeekdayHeatmap",
        "MAEMFEArrays",
        "MonthYearHeatmap",
        "RMultipleArrays",
        "SlippageSimulationResult",
        "StrategySummary",
        "compare_runs",
        "drawdown_durations",
        "exit_reason_attribution",
        "hour_weekday_heatmap",
        "mae_mfe_arrays",
        "month_year_heatmap",
        "r_multiples",
        "simulate_slippage",
        "summarise_run",
    ),
    "engine.optimization.walk_forward": (
        "WalkForwardResult",
        "WalkForwardSplit",
        "WalkForwardWindowResult",
        "make_splits",
        "walk_forward",
    ),
}

_LAZY_ATTR_TO_MODULE: dict[str, str] = {}
for _mod, _names in _LAZY_IMPORTS.items():
    for _n in _names:
        _LAZY_ATTR_TO_MODULE[_n] = _mod


def __getattr__(name: str) -> object:
    """PEP 562 lazy attribute access — defers heavy modules until needed.

    This keeps ``import engine`` fast for CLI/headless/test processes.
    """
    mod_path = _LAZY_ATTR_TO_MODULE.get(name)
    if mod_path is not None:
        import importlib

        mod = importlib.import_module(mod_path)
        attr = getattr(mod, name)
        globals()[name] = attr
        return attr
    raise AttributeError(f"module 'engine' has no attribute {name!r}")
