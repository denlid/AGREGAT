from __future__ import annotations

import pandas as pd
import pytest

from engine.api import Strategy as ApiStrategy
from engine.core.backtest_core import BacktestCore
from engine.models.models import AssetConfig


def _df(rows):
    df = pd.DataFrame(rows, columns=["time", "open", "high", "low", "close"])
    df["time"] = pd.to_datetime(df["time"])
    df["volume"] = 1.0
    return df


def _core(rows) -> BacktestCore:
    core = BacktestCore()
    core.asset_config = AssetConfig(
        name="TEST",
        point_value=1.0,
        tick_size=1.0,
        buffer=0.0,
        max_lots_per_100k=100,
        currency="USD",
        enforce_eod_close=False,
        close_on_weekend=False,
    )
    core.load_data(_df(rows))
    return core


def test_api_on_candle_runs_while_position_is_open() -> None:
    api = ApiStrategy(
        name="V2",
        pyramiding=1,
        process_orders_on_close=True,
    )
    seen_sizes: list[float] = []

    @api.on_candle()
    def _(c):
        seen_sizes.append(c.position_size)
        if c._ctx._engine.current_idx == 0:
            api.entry("L1", "long", qty=1.0, sl=95.0, tp=120.0)

    core = _core(
        [
            ("2024-01-02 10:00", 100, 101, 99, 100),
            ("2024-01-02 10:01", 100, 102, 99, 101),
            ("2024-01-02 10:02", 101, 103, 100, 102),
        ]
    )
    core.load_strategy(api._build_class()())

    for _ in range(core.total_bars):
        core.step()

    assert seen_sizes == [0.0, 1.0, 1.0]
    assert core.position_size == pytest.approx(1.0)


def test_api_pyramiding_and_exit_from_entry_close_only_target_leg() -> None:
    api = ApiStrategy(
        name="V2",
        pyramiding=2,
        process_orders_on_close=True,
    )

    @api.on_candle()
    def _(c):
        idx = c._ctx._engine.current_idx
        if idx == 0:
            api.entry("L1", "long", qty=1.0, sl=95.0, tp=120.0)
        if idx == 1:
            api.entry("L2", "long", qty=1.0, sl=96.0, tp=120.0)
            api.exit("x_L1", from_entry="L1", stop=99.0, limit=104.0)
        if idx == 3:
            api.close_all(reason="EOD")

    core = _core(
        [
            ("2024-01-02 10:00", 100, 101, 99, 100),
            ("2024-01-02 10:01", 101, 103, 100, 102),
            ("2024-01-02 10:02", 102, 105, 101, 104),
            ("2024-01-02 10:03", 104, 106, 103, 105),
            ("2024-01-02 10:04", 105, 106, 104, 105),
        ]
    )
    core.load_strategy(api._build_class()())

    for _ in range(core.total_bars):
        core.step()

    assert [(t.tag, t.exit_reason) for t in core.trades] == [
        ("L1", "TP"),
        ("L2", "EOD"),
    ]
    assert [t.entry_price for t in core.trades] == [100.0, 102.0]
    assert [t.exit_price for t in core.trades] == [104.0, 105.0]
    assert core.position_size == pytest.approx(0.0)


def test_process_orders_on_close_false_fills_next_open() -> None:
    api = ApiStrategy(
        name="V2",
        pyramiding=1,
        process_orders_on_close=False,
    )

    @api.on_candle()
    def _(c):
        if c._ctx._engine.current_idx == 0:
            api.entry("L1", "long", qty=1.0, sl=95.0, tp=120.0)

    core = _core(
        [
            ("2024-01-02 10:00", 100, 101, 99, 100),
            ("2024-01-02 10:01", 111, 112, 110, 111),
        ]
    )
    core.load_strategy(api._build_class()())

    for _ in range(core.total_bars):
        core.step()

    assert core.positions["L1"].entry_price == pytest.approx(111.0)
