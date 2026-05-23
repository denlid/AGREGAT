"""TZ Phase 1 / E2 — ``BacktestCore.run_vectorized`` must keep
``self.stats`` (the central StatsAccumulator) in sync with the trade
list. Before this fix the vectorised path only updated the legacy
``_stats_*`` mirror fields, so once Phase 4 (Stats centralisation)
landed the Stats-tab would silently report zeros for any Quant run.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from engine.strategy.context import Strategy
from engine.core.backtest_core import BacktestCore
from engine.execution.execution import ExecutionModel
from engine.models.models import AssetConfig


class _AlternatingTrades(Strategy):
    """Open longs on every other bar with tight SL/TP so they close in
    the same bar — gives us a deterministic mix of wins / losses /
    break-evens for the assertion below.
    """

    def __init__(self, n_bars: int) -> None:
        self._n = n_bars

    def vectorized_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        sig = np.zeros(self._n, dtype=int)
        sl = np.full(self._n, np.nan, dtype=float)
        tp = np.full(self._n, np.nan, dtype=float)
        # Long every 3rd bar starting at 1.
        for i in range(1, self._n, 3):
            sig[i] = +1
            sl[i] = 2.0
            tp[i] = 4.0
        return pd.DataFrame({"signal": sig, "sl_points": sl, "tp_points": tp}, index=df.index)


def _df() -> pd.DataFrame:
    # Alternate green/red 1-min bars so some entries hit TP and some SL.
    rows = [
        ("2024-01-02 09:30", 100, 100, 100, 100),
        ("2024-01-02 09:31", 100, 110, 100, 110),
        ("2024-01-02 09:32", 110, 110, 100, 100),  # red
        ("2024-01-02 09:33", 100, 110, 100, 110),  # green → TP
        ("2024-01-02 09:34", 110, 110, 100, 100),
        ("2024-01-02 09:35", 100, 110, 100, 110),
        ("2024-01-02 09:36", 110, 110, 100, 100),  # red → SL
    ]
    df = pd.DataFrame(rows, columns=["time", "open", "high", "low", "close"])
    df["time"] = pd.to_datetime(df["time"])
    df["volume"] = 1.0
    return df


def _make_core() -> BacktestCore:
    core = BacktestCore()
    core.asset_config = AssetConfig(
        name="TEST",
        point_value=10.0,
        tick_size=1.0,
        buffer=0.0,
        max_lots_per_100k=10,
        currency="USD",
        spread_points=0.0,
        commission_per_lot=0.0,
        slippage_points=0.0,
    )
    core.execution_model = ExecutionModel(profile="Optimistic")
    core.initial_equity = 100_000.0
    core.equity = core.initial_equity
    core.max_equity = core.initial_equity
    return core


def test_run_vectorized_populates_stats_accumulator() -> None:
    core = _make_core()
    df = _df()
    core.load_data(df)
    core.load_strategy(_AlternatingTrades(n_bars=len(df)))

    core.run_vectorized()

    assert core.trades, "expected at least one trade from the vectorised run"

    # ``self.stats`` should reflect the same totals derived from ``self.trades``.
    expected_wins = sum(1 for t in core.trades if t.pnl > 0)
    expected_losses = sum(1 for t in core.trades if t.pnl < 0)
    expected_be = sum(1 for t in core.trades if t.pnl == 0)
    expected_gp = sum(t.pnl for t in core.trades if t.pnl > 0)
    expected_gl = abs(sum(t.pnl for t in core.trades if t.pnl < 0))

    assert core.stats.wins == expected_wins
    assert core.stats.losses == expected_losses
    assert core.stats.break_evens == expected_be
    assert core.stats.gross_profit == expected_gp
    assert core.stats.gross_loss == expected_gl
    # Mirror fields must agree with the central accumulator.
    assert core._stats_wins == core.stats.wins
    assert core._stats_losses == core.stats.losses
    assert core._stats_gross_profit == core.stats.gross_profit
    assert core._stats_gross_loss == core.stats.gross_loss


def test_run_vectorized_stats_reset_on_rerun() -> None:
    """Re-running the vectorised path must wipe stats; we should never see
    accumulator values double when the same engine instance runs twice.
    """
    core = _make_core()
    df = _df()
    core.load_data(df)
    core.load_strategy(_AlternatingTrades(n_bars=len(df)))

    core.run_vectorized()
    first_total = core.stats.total_trades
    assert first_total > 0

    # Reload data + strategy and re-run — totals must equal the first run, not 2×.
    core.load_data(df)
    core.load_strategy(_AlternatingTrades(n_bars=len(df)))
    core.run_vectorized()

    assert core.stats.total_trades == first_total
