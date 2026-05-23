"""TZ Phase 2b — pure-logic tests for the batched trade-marker builder.

The actual ``ChartTab`` is GUI-only, but the math that turns a list of
trades into ``(buy/sell/close)`` scatter arrays plus NaN-separated
``win/loss`` connection-line arrays now lives in
``gui.chart_tab.markers`` and can be exercised headlessly.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

from gui.chart_tab.markers import build_trade_marker_arrays


@dataclass
class FakeTrade:
    direction: str
    entry_index: int
    exit_index: int
    entry_time: str
    exit_time: str
    entry_price: float
    exit_price: float
    pnl: float


@dataclass
class FakePosition:
    direction: str
    entry_index: int
    entry_time: str
    entry_price: float
    sl: Optional[float] = None
    entry_time_np: Optional[np.datetime64] = None


def _rt(times):
    return pd.to_datetime(times).values


def test_long_winner_is_buy_marker_and_win_curve() -> None:
    rt = _rt(
        [
            "2024-01-02 09:00",
            "2024-01-02 09:01",
            "2024-01-02 09:02",
            "2024-01-02 09:03",
        ]
    )
    trade = FakeTrade(
        direction="long",
        entry_index=1,
        exit_index=3,
        entry_time="2024-01-02 09:01",
        exit_time="2024-01-02 09:03",
        entry_price=100.0,
        exit_price=105.0,
        pnl=5.0,
    )
    arrays = build_trade_marker_arrays(rt, [trade], df_len=4)

    assert arrays.bx == [1] and arrays.by == [100.0]
    assert arrays.sx == [] and arrays.sy == []
    assert arrays.cx == [3] and arrays.cy == [105.0]
    # Connection line: 3 entries — entry, exit, NaN (sentinel).
    assert arrays.win_x[:2] == [1, 3] and math.isnan(arrays.win_x[2])
    assert arrays.win_y[:2] == [100.0, 105.0] and math.isnan(arrays.win_y[2])
    assert arrays.loss_x == [] and arrays.loss_y == []


def test_short_loser_is_sell_marker_and_loss_curve() -> None:
    rt = _rt(
        [
            "2024-01-02 09:00",
            "2024-01-02 09:01",
            "2024-01-02 09:02",
            "2024-01-02 09:03",
        ]
    )
    trade = FakeTrade(
        direction="short",
        entry_index=1,
        exit_index=2,
        entry_time="2024-01-02 09:01",
        exit_time="2024-01-02 09:02",
        entry_price=100.0,
        exit_price=102.0,
        pnl=-2.0,
    )
    arrays = build_trade_marker_arrays(rt, [trade], df_len=4)

    assert arrays.sx == [1] and arrays.sy == [100.0]
    assert arrays.bx == []
    assert arrays.cx == [2] and arrays.cy == [102.0]
    assert arrays.loss_x[:2] == [1, 2] and math.isnan(arrays.loss_x[2])
    assert arrays.win_x == []


def test_multiple_trades_share_curve_with_nan_separators() -> None:
    rt = _rt([f"2024-01-02 09:0{i}" for i in range(8)])
    trades = [
        FakeTrade("long", 1, 2, "2024-01-02 09:01", "2024-01-02 09:02", 100, 101, 1.0),  # winner
        FakeTrade("long", 3, 4, "2024-01-02 09:03", "2024-01-02 09:04", 110, 109, -1.0),  # loser
        FakeTrade("short", 5, 6, "2024-01-02 09:05", "2024-01-02 09:06", 120, 118, 2.0),  # winner
    ]
    arrays = build_trade_marker_arrays(rt, trades, df_len=8)

    # Two winners → 2 segments × 3 entries (entry, exit, NaN) = 6 floats.
    assert len(arrays.win_x) == 6
    assert len(arrays.loss_x) == 3
    # NaNs land on positions 2 and 5 within the win arrays.
    assert math.isnan(arrays.win_x[2]) and math.isnan(arrays.win_x[5])
    # Markers: 2 long entries + 1 short entry + 3 closes.
    assert len(arrays.bx) == 2
    assert len(arrays.sx) == 1
    assert len(arrays.cx) == 3


def test_show_connections_false_clears_curve_arrays_only() -> None:
    rt = _rt(
        [
            "2024-01-02 09:00",
            "2024-01-02 09:01",
            "2024-01-02 09:02",
            "2024-01-02 09:03",
        ]
    )
    trade = FakeTrade(
        "long",
        1,
        2,
        "2024-01-02 09:01",
        "2024-01-02 09:02",
        100,
        101,
        1.0,
    )
    arrays = build_trade_marker_arrays(
        rt,
        [trade],
        df_len=4,
        show_connections=False,
    )
    assert arrays.bx == [1] and arrays.cx == [2]
    assert arrays.win_x == [] and arrays.loss_x == []


def test_trades_entirely_before_window_are_skipped() -> None:
    rt = _rt(["2024-01-02 12:00", "2024-01-02 12:01", "2024-01-02 12:02"])
    trade = FakeTrade(
        "long",
        0,
        1,
        "2024-01-02 09:00",
        "2024-01-02 09:01",
        100,
        101,
        1.0,
    )
    arrays = build_trade_marker_arrays(rt, [trade], df_len=10)
    assert arrays.bx == [] and arrays.cx == []
    assert arrays.win_x == []


def test_open_position_uses_close_price_for_unrealised_segment() -> None:
    rt = _rt(
        [
            "2024-01-02 09:00",
            "2024-01-02 09:01",
            "2024-01-02 09:02",
            "2024-01-02 09:03",
        ]
    )
    pos = FakePosition(
        direction="long",
        entry_index=1,
        entry_time="2024-01-02 09:01",
        entry_price=100.0,
    )
    arrays = build_trade_marker_arrays(
        rt,
        [],
        df_len=4,
        position=pos,
        position_close_price=103.0,
    )
    # Open long → buy marker at the entry candle, no close marker.
    assert arrays.bx == [1] and arrays.by == [100.0]
    assert arrays.cx == []
    # Unrealised winner (close > entry) → win bucket, segment ends at
    # the right edge of the rendered window (idx 3).
    assert arrays.win_x[:2] == [1, 3]
    assert arrays.win_y[:2] == [100.0, 103.0]


def test_out_of_bounds_indices_are_dropped() -> None:
    rt = _rt(["2024-01-02 09:00", "2024-01-02 09:01"])
    trade = FakeTrade(
        "long",
        5,
        9,
        "2024-01-02 09:00",
        "2024-01-02 09:01",
        100,
        101,
        1.0,
    )
    arrays = build_trade_marker_arrays(rt, [trade], df_len=4)
    assert arrays.bx == [] and arrays.cx == []
