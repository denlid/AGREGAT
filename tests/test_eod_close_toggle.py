"""Tests that EOD no longer force-closes open positions.

Position lifecycle belongs to the strategy. The asset-level EOD flag still
participates in pending-entry filtering, but ``_process_one_candle`` must not
close an already-open position before user strategy code can manage it.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pandas as pd

from engine.strategy.context import Strategy
from engine.core.backtest_core import BacktestCore
from engine.execution.execution import ExecutionModel
from engine.models.models import AssetConfig


# A trivial strategy that opens a long on the first bar and never exits.
class _AlwaysLong(Strategy):
    name = "always_long"

    def on_candle(self, ctx) -> None:  # noqa: D401
        if ctx.position is None and ctx._engine.pending_order is None:
            ctx.buy_at_open(sl=ctx.candle["close"] - 100.0)


def _df_through_eod(n_bars: int = 240) -> pd.DataFrame:
    """A 240-bar 1m frame that crosses the default 22:00 EOD boundary."""
    times = pd.date_range("2024-01-02 20:00", periods=n_bars, freq="1min")
    rng = np.random.default_rng(0)
    close = 100 + rng.normal(0, 0.5, n_bars).cumsum()
    high = close + 0.5
    low = close - 0.5
    open_ = np.concatenate(([close[0]], close[:-1]))
    return pd.DataFrame(
        {
            "time": times,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": np.full(n_bars, 1.0),
        }
    )


def _asset(*, enforce_eod: bool) -> AssetConfig:
    return AssetConfig(
        name="EOD",
        point_value=1.0,
        tick_size=0.25,
        buffer=0.0,
        max_lots_per_100k=100,
        currency="USD",
        min_lot=0.01,
        lot_step=0.01,
        spread_points=0.0,
        commission_per_lot=0.0,
        close_on_weekend=False,
        enforce_trading_hours=False,
        enforce_eod_close=enforce_eod,
    )


def _run(asset: AssetConfig) -> BacktestCore:
    eng = BacktestCore()
    eng.asset_config = asset
    eng.execution_model = ExecutionModel(profile="Optimistic")
    eng.load_data(_df_through_eod())
    eng.load_strategy(_AlwaysLong())
    eng.step_n(eng.total_bars + 5)
    return eng


def test_eod_flag_does_not_force_close_open_position() -> None:
    """Even enforce_eod_close=True must not auto-close an open position."""
    eng = _run(_asset(enforce_eod=True))
    eod_trades = [t for t in eng.trades if t.exit_reason == "EOD"]
    assert not eod_trades
    assert eng.position is not None


def test_eod_force_close_disabled_keeps_position_open() -> None:
    """With enforce_eod_close=False, the position must not close on the EOD minute."""
    eng = _run(_asset(enforce_eod=False))
    eod_trades = [t for t in eng.trades if t.exit_reason == "EOD"]
    assert not eod_trades, f"enforce_eod_close=False should disable EOD exits, got {eod_trades}"
    # Position should still be open at the end (no SL was placed within
    # range of the synthetic data).
    assert eng.position is not None


def test_default_value_is_true() -> None:
    """Backwards compatibility: AssetConfig defaults to enforce_eod_close=True."""
    a = AssetConfig(
        name="X",
        point_value=1.0,
        tick_size=0.01,
        buffer=0.0,
        max_lots_per_100k=10,
        currency="USD",
        min_lot=0.01,
        lot_step=0.01,
    )
    assert a.enforce_eod_close is True
