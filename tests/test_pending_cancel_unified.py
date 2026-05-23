"""Phase 1.7 — Unified pending-order cancel: trading hours / weekend / EOD."""

from __future__ import annotations

from datetime import time

import pandas as pd

from engine.core.backtest_core import BacktestCore
from engine.execution.execution import ExecutionModel
from engine.models.models import AssetConfig
from engine.strategy.context import Strategy


class _BuyOnce(Strategy):
    def __init__(self) -> None:
        self._sent = False

    def on_candle(self, ctx) -> None:  # type: ignore[override]
        if not self._sent and ctx.position is None:
            ctx.buy_at_open(volume=1.0, sl=None, tp=None, reason="t", tag="t")
            self._sent = True


def _asset(
    *,
    enforce_trading_hours: bool = False,
    enforce_eod_close: bool = False,
    close_on_weekend: bool = False,
    trading_hours_start: time = time(9, 0),
    trading_hours_end: time = time(18, 0),
    eod_close: time = time(22, 0),
) -> AssetConfig:
    return AssetConfig(
        name="X",
        point_value=10.0,
        tick_size=1.0,
        buffer=0.0,
        max_lots_per_100k=10,
        currency="USD",
        spread_points=0.0,
        slippage_points=0.0,
        commission_per_lot=0.0,
        trading_hours_start=trading_hours_start,
        trading_hours_end=trading_hours_end,
        eod_close=eod_close,
        enforce_trading_hours=enforce_trading_hours,
        enforce_eod_close=enforce_eod_close,
        close_on_weekend=close_on_weekend,
    )


def _df(timestamps: list[str]) -> pd.DataFrame:
    rows = [
        (t, 100.0 + i, 100.5 + i, 99.5 + i, 100.0 + i, 1.0)
        for i, t in enumerate(timestamps)
    ]
    df = pd.DataFrame(rows, columns=["time", "open", "high", "low", "close", "volume"])
    df["time"] = pd.to_datetime(df["time"])
    return df


def _make_core(asset: AssetConfig) -> BacktestCore:
    core = BacktestCore()
    core.asset_config = asset
    core.execution_model = ExecutionModel(profile="Optimistic")
    core.initial_equity = 100_000.0
    core.equity = core.initial_equity
    core.max_equity = core.initial_equity
    return core


def test_pending_cancelled_outside_trading_hours() -> None:
    """Signal at 17:55, fill bar at 18:01 → outside hours → cancel."""
    asset = _asset(enforce_trading_hours=True)
    df = _df(["2024-01-03 17:55", "2024-01-03 18:01"])
    core = _make_core(asset)
    core.load_data(df)
    core.load_strategy(_BuyOnce())
    core.step()  # fires signal
    core.step()  # would fill — but outside trading hours
    assert core.position is None, "Pending should have been cancelled"
    assert len(core.skipped_trades) == 1
    assert core.skipped_trades[0]["reason"] == "TRADING_HOURS"


def test_pending_cancelled_on_weekend() -> None:
    """Signal Friday 23:30, fill bar Saturday 00:00 → weekend → cancel."""
    asset = _asset(close_on_weekend=True)
    # 2024-01-05 is Friday, 2024-01-06 is Saturday.
    df = _df(["2024-01-05 23:30", "2024-01-06 00:00"])
    core = _make_core(asset)
    core.load_data(df)
    core.load_strategy(_BuyOnce())
    core.step()
    core.step()
    assert core.position is None, "Pending should have been cancelled (weekend)"
    assert any(s["reason"] == "WEEKEND" for s in core.skipped_trades)


def test_pending_cancelled_at_eod() -> None:
    """Signal 21:55, fill bar 22:01 → past EOD → cancel."""
    asset = _asset(enforce_eod_close=True, eod_close=time(22, 0))
    df = _df(["2024-01-03 21:55", "2024-01-03 22:01"])
    core = _make_core(asset)
    core.load_data(df)
    core.load_strategy(_BuyOnce())
    core.step()
    core.step()
    assert core.position is None, "Pending should have been cancelled (EOD)"
    assert any(s["reason"] == "EOD" for s in core.skipped_trades)


def test_pending_filled_when_within_window() -> None:
    """Sanity: in-window pending is filled normally."""
    asset = _asset(
        enforce_trading_hours=True,
        enforce_eod_close=True,
        close_on_weekend=True,
    )
    df = _df(["2024-01-03 10:00", "2024-01-03 10:01"])
    core = _make_core(asset)
    core.load_data(df)
    core.load_strategy(_BuyOnce())
    core.step()
    core.step()
    assert core.position is not None
    assert core.skipped_trades == []
