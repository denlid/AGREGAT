"""End-to-end mathematical objectivity audit for the strategy engine.

The point of this file is not to test whether a strategy is profitable. It
locks the accounting contract of the application against a hand-written ledger:
visible bars only, next-open fills, Bid/Ask execution, slippage, commission,
PnL, equity and drawdown must all reconcile.
"""

from __future__ import annotations

import pandas as pd
import pytest

from engine.core.backtest_core import BacktestCore
from engine.execution.execution import ExecutionModel
from engine.models.models import AssetConfig
from engine.strategy.context import Strategy


INITIAL_EQUITY = 100_000.0
POINT_VALUE = 10.0
SPREAD_POINTS = 2.0
SLIPPAGE_POINTS = 0.5
COMMISSION_PER_LOT = 8.0


def _df() -> pd.DataFrame:
    rows = [
        ("2024-01-02 10:00", 100, 101, 99, 100),
        ("2024-01-02 10:01", 100, 103, 99, 101),
        ("2024-01-02 10:02", 101, 104, 98, 103),
        ("2024-01-02 10:03", 103, 107, 100, 106),
        ("2024-01-02 10:04", 106, 107, 104, 105),
        ("2024-01-02 10:05", 100, 101, 99, 100),
        ("2024-01-02 10:06", 100, 101, 95, 98),
        ("2024-01-02 10:07", 100, 101, 99, 100),
        ("2024-01-02 10:08", 100, 105, 95, 100),
        ("2024-01-02 10:09", 100, 101, 99, 100),
    ]
    df = pd.DataFrame(rows, columns=["time", "open", "high", "low", "close"])
    df["time"] = pd.to_datetime(df["time"])
    df["volume"] = 1.0
    return df


def _core(profile: str = "Realistic") -> BacktestCore:
    core = BacktestCore()
    core.asset_config = AssetConfig(
        name="AUDIT",
        point_value=POINT_VALUE,
        tick_size=1.0,
        buffer=0.0,
        max_lots_per_100k=100,
        currency="USD",
        spread_points=SPREAD_POINTS,
        commission_per_lot=COMMISSION_PER_LOT,
        slippage_points=SLIPPAGE_POINTS,
    )
    core.execution_model = ExecutionModel(profile=profile)  # type: ignore[arg-type]
    core.initial_equity = INITIAL_EQUITY
    core.equity = INITIAL_EQUITY
    core.max_equity = INITIAL_EQUITY
    core.enable_bar_equity_tracking(True)
    return core


class _MathAuditStrategy(Strategy):
    """Three deterministic trades plus zero-future observations."""

    def __init__(self) -> None:
        super().__init__()
        self.visible_lengths: list[int] = []
        self.visible_last_closes: list[float] = []
        self.data_is_readonly = False

    def on_candle(self, ctx) -> None:  # type: ignore[override]
        idx = ctx._engine.current_idx
        close_view = ctx.data.close
        self.visible_lengths.append(len(close_view))
        self.visible_last_closes.append(float(close_view[-1]))
        if idx == 0:
            try:
                close_view[0] = -1.0
            except ValueError:
                self.data_is_readonly = True
            ctx.buy_at_open(volume=2.0, reason="long_manual", tag="L1")
        elif idx == 3 and ctx.position is not None:
            ctx.close(reason="MANUAL")
        elif idx == 4 and ctx.position is None:
            ctx.sell_at_open(volume=1.5, tp=97.0, reason="short_tp", tag="S1")
        elif idx == 6 and ctx.position is None:
            ctx.buy_at_open(
                volume=1.0,
                sl=96.0,
                tp=104.0,
                reason="long_collision",
                tag="L2",
            )


def test_strategy_math_objectivity_reconciles_against_manual_ledger() -> None:
    df = _df()
    core = _core()
    strategy = _MathAuditStrategy()
    core.load_data(df)
    core.load_strategy(strategy)

    for _ in range(len(df) + 2):
        core.step()

    assert core.last_run_error is None
    assert strategy.visible_lengths == list(range(1, len(df) + 1))
    assert strategy.visible_last_closes == pytest.approx(df["close"].tolist())
    assert strategy.data_is_readonly is True

    assert len(core.trades) == 3
    long_manual, short_tp, long_collision = core.trades

    # Trade 1, long manual close:
    # signal bar 0 -> next-open fill on bar 1. Long buys Ask:
    # 100 + spread 2 + slippage 0.5 = 102.5.
    # Manual long close on bar 3 sells Bid minus slippage:
    # 106 - 0.5 = 105.5.
    assert long_manual.direction == "long"
    assert long_manual.exit_reason == "MANUAL"
    assert long_manual.entry_index == 1
    assert long_manual.exit_index == 3
    assert long_manual.entry_price == pytest.approx(102.5)
    assert long_manual.exit_price == pytest.approx(105.5)
    assert long_manual.pnl_points == pytest.approx(3.0)
    assert long_manual.commission == pytest.approx(16.0)
    assert long_manual.spread_cost == pytest.approx(40.0)
    assert long_manual.slippage_cost == pytest.approx(20.0)
    assert long_manual.pnl == pytest.approx(44.0)

    # Trade 2, short TP:
    # signal bar 4 -> next-open fill on bar 5. Short sells Bid minus slippage:
    # 100 - 0.5 = 99.5.
    # On bar 6, Ask-low = bid low 95 + spread 2 = 97, so TP=97 is reached.
    # Stop/limit exits add only slippage to the user anchor: 97 + 0.5 = 97.5.
    assert short_tp.direction == "short"
    assert short_tp.exit_reason == "TP"
    assert short_tp.entry_index == 5
    assert short_tp.exit_index == 6
    assert short_tp.entry_price == pytest.approx(99.5)
    assert short_tp.exit_price == pytest.approx(97.5)
    assert short_tp.pnl_points == pytest.approx(2.0)
    assert short_tp.commission == pytest.approx(12.0)
    assert short_tp.spread_cost == pytest.approx(30.0)
    assert short_tp.slippage_cost == pytest.approx(15.0)
    assert short_tp.pnl == pytest.approx(18.0)

    # Trade 3, same-bar long SL/TP collision:
    # signal bar 6 -> next-open fill on bar 7 at 102.5.
    # Bar 8 has low <= SL and high >= TP. Worst-case rule must choose SL.
    # Long stop slips lower: 96 - 0.5 = 95.5.
    assert long_collision.direction == "long"
    assert long_collision.exit_reason == "SL"
    assert long_collision.entry_index == 7
    assert long_collision.exit_index == 8
    assert long_collision.entry_price == pytest.approx(102.5)
    assert long_collision.exit_price == pytest.approx(95.5)
    assert long_collision.pnl_points == pytest.approx(-7.0)
    assert long_collision.commission == pytest.approx(8.0)
    assert long_collision.spread_cost == pytest.approx(20.0)
    assert long_collision.slippage_cost == pytest.approx(10.0)
    assert long_collision.pnl == pytest.approx(-78.0)

    expected_total_pnl = 44.0 + 18.0 - 78.0
    assert sum(t.pnl for t in core.trades) == pytest.approx(expected_total_pnl)
    assert core.equity == pytest.approx(INITIAL_EQUITY + expected_total_pnl)
    assert core.equity_track == pytest.approx(
        [INITIAL_EQUITY, 100_044.0, 100_062.0, 99_984.0]
    )
    assert core.max_equity == pytest.approx(100_062.0)
    assert core.max_drawdown_abs == pytest.approx(78.0)
    assert core.max_drawdown_pct == pytest.approx(78.0 / 100_062.0 * 100.0)

    assert core.bar_equity is not None
    assert core.bar_equity[1] == pytest.approx(99_970.0)
    assert core.bar_equity.min() < INITIAL_EQUITY
    assert core.bar_equity[-1] == pytest.approx(core.equity)

    for trade in core.trades:
        assert trade.entry_index < trade.exit_index
        assert trade.volume > 0.0


def test_realistic_execution_never_outperforms_optimistic_on_same_trades() -> None:
    outcomes: dict[str, float] = {}
    for profile in ("Optimistic", "Realistic"):
        core = _core(profile=profile)
        core.load_data(_df())
        core.load_strategy(_MathAuditStrategy())
        for _ in range(len(_df()) + 2):
            core.step()
        assert len(core.trades) == 3
        outcomes[profile] = core.equity

    assert outcomes["Realistic"] < outcomes["Optimistic"]
