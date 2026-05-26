"""Phase 1.1 — Bar-level equity invariants.

The bar-level series ``BacktestCore.bar_equity`` must:

1. Reach the same final value as the trade-level
   :pyattr:`BacktestCore.equity` once every position has been closed —
   otherwise the running mark-to-market is inconsistent with the
   realised cash flow.
2. Touch a value ≤ the trade-level equity at *some* bar inside any
   trade where the price moves adversely (intra-trade drawdown). The
   trade-level curve cannot capture that.
3. Be allocated only when ``track_bar_equity`` is enabled — disabled
   by default so existing simulations don't pay the ~80 MB cost.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from engine.analytics.bar_level_equity import (
    bar_calmar_ratio,
    bar_max_drawdown,
    bar_returns,
    bar_sharpe_ratio,
    build_bar_level_equity,
)
from engine.core.backtest_core import BacktestCore
from engine.execution.execution import ExecutionModel
from engine.models.models import AssetConfig
from engine.strategy.context import Strategy


class _BuyAndExitOnLastBar(Strategy):
    """Open a long on bar 0, close it on the final bar."""

    def __init__(self, n_bars: int) -> None:
        self._sent = False
        self._n_bars = n_bars
        self._closed = False

    def on_candle(self, ctx) -> None:  # type: ignore[override]
        if not self._sent and ctx.position is None:
            ctx.buy_at_open(volume=1.0, sl=None, tp=None, reason="t", tag="t")
            self._sent = True
        elif (
            ctx.position is not None
            and ctx._engine.current_idx == self._n_bars - 1
            and not self._closed
        ):
            ctx.close()
            self._closed = True


def _make_core(track_bar_equity: bool = False) -> BacktestCore:
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
    if track_bar_equity:
        core.enable_bar_equity_tracking(True)
    return core


def _ramp_df(n: int = 20) -> pd.DataFrame:
    rows = []
    for i in range(n):
        # Dip at i=5, recover by i=15, finish at +5.
        if i < 5:
            base = 100 + i
        elif i < 10:
            base = 100 + i - 2 * (i - 4)  # dip
        else:
            base = 100 + (i - 10)
        rows.append(
            (
                f"2024-01-02 {10 + i // 60:02d}:{i % 60:02d}",
                base,
                base + 1,
                base - 1,
                base,
            )
        )
    df = pd.DataFrame(rows, columns=["time", "open", "high", "low", "close"])
    df["time"] = pd.to_datetime(df["time"])
    df["volume"] = 1.0
    return df


def test_bar_equity_default_is_disabled() -> None:
    core = _make_core(track_bar_equity=False)
    df = _ramp_df()
    core.load_data(df)
    assert core.track_bar_equity is False
    assert core.bar_equity is None


def test_bar_equity_buffer_allocated_after_enable() -> None:
    core = _make_core(track_bar_equity=True)
    df = _ramp_df()
    core.load_data(df)
    assert core.track_bar_equity is True
    assert core.bar_equity is not None
    assert core.bar_equity.shape[0] == len(df)
    # Pre-run all bars sit at initial_equity.
    assert np.allclose(core.bar_equity, core.initial_equity)


def test_bar_equity_final_matches_trade_level_after_full_run() -> None:
    """Bar-level final must equal trade-level realised equity."""
    df = _ramp_df()
    core = _make_core(track_bar_equity=True)
    core.load_data(df)
    core.load_strategy(_BuyAndExitOnLastBar(n_bars=len(df)))
    for _ in range(len(df) + 2):
        core.step()
    assert core.bar_equity is not None
    # All trades closed → no open position; last bar equity = realised.
    assert core.position is None
    assert core.bar_equity[-1] == core.equity


def test_bar_equity_captures_intra_trade_dip() -> None:
    """The bar-level curve must dip below initial during the adverse leg."""
    df = _ramp_df()
    core = _make_core(track_bar_equity=True)
    core.load_data(df)
    core.load_strategy(_BuyAndExitOnLastBar(n_bars=len(df)))
    for _ in range(len(df) + 2):
        core.step()
    assert core.bar_equity is not None
    min_bar_eq = float(core.bar_equity.min())
    # The trade-level curve starts and stays at initial_equity until
    # close, so a *bar-level* dip below initial is the smoking gun.
    assert min_bar_eq < core.initial_equity, (
        f"Bar equity did not dip below initial — observed min={min_bar_eq}"
    )


def test_bar_equity_module_helpers_compute_finite_metrics() -> None:
    times = pd.date_range("2024-01-01", periods=300, freq="1min")
    rng = np.random.default_rng(0)
    eq = 100_000.0 + np.cumsum(rng.normal(0.0, 5.0, size=300))
    series = build_bar_level_equity(eq, times.to_numpy())
    rets = bar_returns(series)
    assert rets.size == 299
    s = bar_sharpe_ratio(series)
    so = bar_calmar_ratio(series, initial_equity=100_000.0)
    dd_abs, dd_pct = bar_max_drawdown(series)
    assert np.isfinite(s)
    assert np.isfinite(so) or np.isnan(so)
    assert dd_abs >= 0.0
    assert 0.0 <= dd_pct < 1.0
