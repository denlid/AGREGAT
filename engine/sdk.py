"""Python SDK convenience re-exports + ``Backtester`` ergonomic API.

The SDK has two layers:

1. **Re-exports** (Sprint 3.8) — bring the most-used engine helpers
   into a single import location so notebooks can write::

       from engine.sdk import BacktestCore, DataLoader, grid_search, ...

2. **Ergonomic ``Backtester``** (Sprint 4.5) — a one-call wrapper that
   accepts an ``asset`` config, a ``Strategy`` instance and a price
   DataFrame, then returns a :class:`BacktestResult` containing the
   trades, equity curve and a metrics dict. This is the form the
   roadmap markets to "ML / quant power-users" — they don't want to
   boot the GUI just to score a strategy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Union

import pandas as pd

from engine.strategy.context import Context, Strategy, StrategyLoader
from engine.core.backtest_core import BacktestCore
from engine.data.data_loader import DataLoader
from engine.analytics.equity_curve import (
    build_equity_curve,
    calmar_ratio,
    daily_returns,
    max_drawdown,
    sharpe_ratio,
    sortino_ratio,
)
from engine.execution.execution import ExecutionCosts, ExecutionModel
from engine.logging_config import (
    TraceContextAdapter,
    enable_json_logging,
    new_trace_id,
)
from engine.persistence_pkg.migrations import run_migrations
from engine.models.models import AssetConfig, Trade
from engine.analytics.monte_carlo import MCResult, bootstrap_pnl, shock_pnl
from engine.optimization.optimizer import (
    OptimizationResult,
    OptimizationRun,
    grid_search,
    random_search,
)
from engine.persistence_pkg.persistence import (
    AGBK_EXTENSION,
    AGBK_FORMAT_VERSION,
    load_session_raw,
    save_session,
)
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
from engine.optimization.walk_forward import (
    WalkForwardResult,
    make_splits,
    walk_forward,
)


# -----------------------------------------------------------------------------
# Backtester ergonomic API (Sprint 4.5)
# -----------------------------------------------------------------------------
@dataclass
class BacktestResult:
    """Return value of :meth:`Backtester.run`."""

    trades: list[Trade] = field(default_factory=list)
    equity_curve: pd.Series = field(default_factory=lambda: pd.Series(dtype="float64"))
    metrics: dict[str, float] = field(default_factory=dict)
    equity_track: list[float] = field(default_factory=list)

    @property
    def total_pnl(self) -> float:
        return float(sum(t.pnl for t in self.trades))

    @property
    def num_trades(self) -> int:
        return len(self.trades)


class Backtester:
    """One-call backtest harness for headless / notebook usage.

    Example
    -------
    .. code-block:: python

        from engine.sdk import Backtester
        from engine.models.models import AssetConfig
        from my_strategies import MyStrategy

        asset = AssetConfig(...)
        bt = Backtester(asset=asset, strategy=MyStrategy(), data=df)
        result = bt.run()
        print(result.metrics["sharpe"], result.num_trades)
    """

    def __init__(
        self,
        *,
        asset: AssetConfig,
        strategy: Strategy,
        data: Union[pd.DataFrame, str],
        starting_equity: float = 10_000.0,
        seed: Optional[int] = None,
    ) -> None:
        self._asset = asset
        self._strategy = strategy
        self._data_input = data
        self._starting_equity = float(starting_equity)
        self._seed = seed
        self._core: Optional[BacktestCore] = None

    # ------------------------------------------------------------------
    @property
    def core(self) -> BacktestCore:
        """The underlying :class:`BacktestCore` (instantiated lazily)."""
        if self._core is None:
            df = self._load_data()
            core = BacktestCore()
            core.equity = self._starting_equity
            core.asset_config = self._asset
            core.load_data(df)
            core.load_strategy(self._strategy)
            if self._seed is not None:
                core.set_seeds(default=self._seed)
            core.reset()
            self._core = core
        return self._core

    # ------------------------------------------------------------------
    def run(self) -> BacktestResult:
        """Run the strategy over all bars in vectorised step mode."""
        core = self.core
        core.step_n(core.total_bars)
        return self._collect_result(core)

    # ------------------------------------------------------------------
    def _load_data(self) -> pd.DataFrame:
        if isinstance(self._data_input, pd.DataFrame):
            return self._data_input
        if isinstance(self._data_input, str):
            loader = DataLoader()
            return loader.load_csv(self._data_input)
        raise TypeError(f"data must be DataFrame or path-string, got {type(self._data_input)!r}")

    # ------------------------------------------------------------------
    def _collect_result(self, core: BacktestCore) -> BacktestResult:
        trades = list(core.trades)
        equity_track = list(core.equity_track)
        equity_curve = build_equity_curve(trades, self._starting_equity)
        metrics = self._compute_metrics(equity_curve, trades)
        return BacktestResult(
            trades=trades,
            equity_curve=equity_curve,
            metrics=metrics,
            equity_track=equity_track,
        )

    # ------------------------------------------------------------------
    def _compute_metrics(self, equity: pd.Series, trades: list[Trade]) -> dict[str, float]:
        from engine.metrics import (
            cagr,
            mar_ratio,
            profit_factor,
        )

        pnls: list[float] = [float(t.pnl) for t in trades]
        wins = sum(1 for p in pnls if p > 0)
        net = float(sum(pnls))
        win_rate_val = float(wins / len(pnls)) if pnls else 0.0
        dd_abs, dd_pct = max_drawdown(equity)
        return {
            "num_trades": float(len(trades)),
            "net_profit": net,
            "win_rate": win_rate_val,
            "profit_factor": float(profit_factor(pnls)),
            "sharpe": float(sharpe_ratio(equity)),
            "sortino": float(sortino_ratio(equity)),
            "calmar": float(calmar_ratio(equity, self._starting_equity)),
            "cagr": float(cagr(equity)),
            "mar": float(mar_ratio(equity)),
            "max_drawdown_abs": float(dd_abs),
            "max_drawdown_pct": float(dd_pct),
        }


def run_backtest(
    *,
    asset: AssetConfig,
    strategy: Strategy,
    data: Union[pd.DataFrame, str],
    starting_equity: float = 10_000.0,
    seed: Optional[int] = None,
) -> BacktestResult:
    """Functional convenience wrapper around :class:`Backtester`."""
    return Backtester(
        asset=asset,
        strategy=strategy,
        data=data,
        starting_equity=starting_equity,
        seed=seed,
    ).run()


__all__ = [
    # Core
    "BacktestCore",
    "Context",
    "Strategy",
    "StrategyLoader",
    "DataLoader",
    "AssetConfig",
    "Trade",
    # Execution
    "ExecutionModel",
    "ExecutionCosts",
    # Equity / performance
    "build_equity_curve",
    "daily_returns",
    "max_drawdown",
    "sharpe_ratio",
    "sortino_ratio",
    "calmar_ratio",
    # Persistence
    "save_session",
    "load_session_raw",
    "AGBK_EXTENSION",
    "AGBK_FORMAT_VERSION",
    "run_migrations",
    # Monte Carlo
    "MCResult",
    "bootstrap_pnl",
    "shock_pnl",
    # Optimiser
    "grid_search",
    "random_search",
    "OptimizationRun",
    "OptimizationResult",
    # Walk-forward
    "make_splits",
    "walk_forward",
    "WalkForwardResult",
    # Risk
    "risk_of_ruin",
    "value_at_risk",
    "conditional_var",
    "omega_ratio",
    "time_under_water",
    "ulcer_index",
    "pain_index",
    "pain_ratio",
    # Logging
    "enable_json_logging",
    "new_trace_id",
    "TraceContextAdapter",
    # Sprint 4.5 ergonomic API
    "Backtester",
    "BacktestResult",
    "run_backtest",
]
