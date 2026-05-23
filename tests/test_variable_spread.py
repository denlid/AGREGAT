"""Phase 1.4 — Per-bar spread loaded from data, propagated to fills."""

from __future__ import annotations

import os
import tempfile

import pandas as pd

from engine.core.backtest_core import BacktestCore
from engine.data.data_loader import DataLoader, _validate_ohlc
from engine.execution.execution import ExecutionModel
from engine.models.models import AssetConfig
from engine.strategy.context import Strategy


def test_data_loader_picks_up_spread_column() -> None:
    df = pd.DataFrame(
        {
            "time": pd.to_datetime(
                ["2024-01-02 10:00", "2024-01-02 10:01", "2024-01-02 10:02"]
            ),
            "open": [100.0, 101.0, 102.0],
            "high": [101.0, 102.0, 103.0],
            "low": [99.0, 100.0, 101.0],
            "close": [100.5, 101.5, 102.5],
            "volume": [1.0, 1.0, 1.0],
            "spread": [3.0, 1.0, 5.0],
        }
    )
    cleaned = _validate_ohlc(df.copy(), source="test")
    assert "spread" in cleaned.columns
    assert list(cleaned["spread"]) == [3.0, 1.0, 5.0]


def test_data_loader_negative_spread_clamped_to_zero() -> None:
    df = pd.DataFrame(
        {
            "time": pd.to_datetime(["2024-01-02 10:00", "2024-01-02 10:01"]),
            "open": [100.0, 101.0],
            "high": [101.0, 102.0],
            "low": [99.0, 100.0],
            "close": [100.5, 101.5],
            "volume": [1.0, 1.0],
            "spread": [-2.0, 1.5],
        }
    )
    cleaned = _validate_ohlc(df.copy(), source="test")
    assert list(cleaned["spread"]) == [0.0, 1.5]


def test_csv_loader_round_trips_spread() -> None:
    """End-to-end: write a CSV with a spread column, read it back."""
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "data.csv")
        df = pd.DataFrame(
            {
                "time": pd.to_datetime(["2024-01-02 10:00", "2024-01-02 10:01"]),
                "open": [100.0, 101.0],
                "high": [101.0, 102.0],
                "low": [99.0, 100.0],
                "close": [100.5, 101.5],
                "volume": [1.0, 1.0],
                "spread": [3.0, 1.0],
            }
        )
        df.to_csv(path, index=False)
        loaded = DataLoader.load_csv(path)
    assert "spread" in loaded.columns
    assert list(loaded["spread"]) == [3.0, 1.0]


# --- Engine integration --------------------------------------------------


class _BuyClose(Strategy):
    def __init__(self) -> None:
        self._sent = False
        self._closed = False

    def on_candle(self, ctx) -> None:  # type: ignore[override]
        if not self._sent and ctx.position is None:
            ctx.buy_at_open(volume=1.0, sl=None, tp=None, reason="t", tag="t")
            self._sent = True
        elif (
            self._sent
            and not self._closed
            and ctx.position is not None
            and ctx._engine.current_idx >= 4
        ):
            ctx.close()
            self._closed = True


def _df_with_spread(spreads: list[float]) -> pd.DataFrame:
    n = len(spreads)
    rows = []
    for i in range(n):
        rows.append(
            (f"2024-01-02 10:{i:02d}", 100 + i, 100 + i + 1, 100 + i - 1, 100 + i)
        )
    df = pd.DataFrame(rows, columns=["time", "open", "high", "low", "close"])
    df["time"] = pd.to_datetime(df["time"])
    df["volume"] = 1.0
    df["spread"] = spreads
    return df


def _make_core(spread_points_default: float) -> BacktestCore:
    core = BacktestCore()
    core.asset_config = AssetConfig(
        name="TEST",
        point_value=10.0,
        tick_size=1.0,
        buffer=0.0,
        max_lots_per_100k=10,
        currency="USD",
        spread_points=spread_points_default,
        slippage_points=0.0,
        commission_per_lot=0.0,
    )
    core.execution_model = ExecutionModel(profile="Realistic")
    core.initial_equity = 100_000.0
    core.equity = core.initial_equity
    core.max_equity = core.initial_equity
    return core


def test_backtest_core_exposes_per_bar_spread() -> None:
    core = _make_core(spread_points_default=2.0)
    df = _df_with_spread([3.0, 1.0, 5.0, 2.0, 4.0, 1.0])
    core.load_data(df)
    assert core.has_per_bar_spread
    assert core.spread_at(0) == 3.0
    assert core.spread_at(2) == 5.0
    assert core.spread_at(99) is None  # out of range


def test_backtest_core_no_spread_column_falls_back() -> None:
    core = _make_core(spread_points_default=2.0)
    df = _df_with_spread([3.0, 1.0])
    df = df.drop(columns=["spread"])
    core.load_data(df)
    assert not core.has_per_bar_spread
    assert core.spread_at(0) is None


def test_high_per_bar_spread_costs_more_than_low() -> None:
    """Same trade with higher per-bar spread should net less."""
    high_spreads = [10.0] * 8
    low_spreads = [1.0] * 8

    pnls: dict[str, float] = {}
    for label, spreads in (("high", high_spreads), ("low", low_spreads)):
        core = _make_core(spread_points_default=2.0)
        core.load_data(_df_with_spread(spreads))
        core.load_strategy(_BuyClose())
        for _ in range(15):
            core.step()
        pnls[label] = core.equity - core.initial_equity

    assert pnls["high"] < pnls["low"], (
        f"Wider per-bar spread did not cost more: high={pnls['high']} low={pnls['low']}"
    )
