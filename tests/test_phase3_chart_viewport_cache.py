from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pandas as pd
import pytest
from PySide6.QtWidgets import QApplication

from gui.chart_tab.items import CandlestickItem
from gui.chart_tab.tab import ChartTab
from gui.chart_tab.markers import build_trade_marker_arrays


class _Signal:
    def __init__(self) -> None:
        self._slots = []

    def connect(self, slot):
        self._slots.append(slot)


class _DummyAggregator:
    def advance_to(self, idx):
        return None

    def get_completed_tail(self, tf, limit):
        return []

    def get_forming(self, tf):
        return None


class _View:
    def __init__(self, x0: float, x1: float) -> None:
        self._xr = [float(x0), float(x1)]

    def viewRange(self):
        return [self._xr, [0.0, 1.0]]


class _Engine:
    def __init__(self, df: pd.DataFrame) -> None:
        self.df = df
        self.current_idx = len(df) - 1
        self.trades = []
        self.drawings = []
        self.position = None
        self.ctx = type("Ctx", (), {"candle": {}})()
        self.tf_aggregator = _DummyAggregator()
        self.layer_registered = _Signal()
        self.data_loaded = _Signal()
        self.step_completed = _Signal()

    def set_visible_tf(self, tf):
        return None


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _df(n: int = 64) -> pd.DataFrame:
    base = pd.Timestamp("2024-01-02 10:00")
    rows = []
    for i in range(n):
        ts = base + pd.Timedelta(minutes=i)
        rows.append((ts, 100.0 + i, 101.0 + i, 99.0 + i, 100.5 + i))
    df = pd.DataFrame(rows, columns=["time", "open", "high", "low", "close"])
    df["time"] = pd.to_datetime(df["time"])
    return df


def test_candlestick_visible_slice_uses_viewport_cache(qapp) -> None:
    item = CandlestickItem(max_bars=10_000)
    arr = np.column_stack(
        (
            np.arange(128, dtype=np.float64),
            np.linspace(100.0, 110.0, 128),
            np.linspace(100.5, 110.5, 128),
            np.linspace(99.5, 109.5, 128),
            np.linspace(101.0, 111.0, 128),
        )
    )
    arr.setflags(write=False)
    item.set_data(arr)
    item._view = _View(20.0, 40.0)

    lo1, hi1 = item._visible_slice()
    assert hi1 > lo1
    assert item._paint_stats["viewport_cache_hit"] is False

    lo2, hi2 = item._visible_slice()
    assert (lo2, hi2) == (lo1, hi1)
    assert item._paint_stats["viewport_cache_hit"] is True


def _candle_arr(n: int = 512) -> np.ndarray:
    x = np.arange(n, dtype=np.float64)
    open_ = np.linspace(100.0, 110.0, n)
    close = open_ + np.sin(x / 13.0)
    low = np.minimum(open_, close) - 0.5
    high = np.maximum(open_, close) + 0.5
    arr = np.column_stack((x, open_, close, low, high))
    arr.setflags(write=False)
    return arr


def test_candlestick_reuses_picture_when_pan_stays_inside_buffer(qapp) -> None:
    item = CandlestickItem(max_bars=10_000)
    item._view = _View(100.0, 200.0)
    item.set_data(_candle_arr())

    calls = []
    original_gen = item._gen

    def counted_gen():
        calls.append(1)
        return original_gen()

    item._gen = counted_gen
    item._view._xr = [110.0, 190.0]
    item._do_range_repaint()

    assert calls == []
    assert item._paint_stats["viewport_cache_hit"] is True


def test_candlestick_rebuilds_picture_when_pan_exits_buffer(qapp) -> None:
    item = CandlestickItem(max_bars=10_000)
    item._view = _View(100.0, 200.0)
    item.set_data(_candle_arr())

    calls = []
    original_gen = item._gen

    def counted_gen():
        calls.append(1)
        return original_gen()

    item._gen = counted_gen
    item._view._xr = [180.0, 280.0]
    item._do_range_repaint()

    assert calls == [1]


def test_candlestick_rebuilds_picture_when_zoom_changes_width(qapp) -> None:
    item = CandlestickItem(max_bars=10_000)
    item._view = _View(100.0, 200.0)
    item.set_data(_candle_arr())

    calls = []
    original_gen = item._gen

    def counted_gen():
        calls.append(1)
        return original_gen()

    item._gen = counted_gen
    item._view._xr = [120.0, 160.0]
    item._do_range_repaint()

    assert calls == [1]


def test_chart_range_sync_coalesces_rapid_range_changes(qapp) -> None:
    engine = _Engine(_df())
    chart = ChartTab(engine)
    chart.redraw_chart()

    emitted = []
    chart.sigTimeRangeChanged.connect(lambda t1, t2: emitted.append((t1, t2)))

    class Pager:
        def __init__(self) -> None:
            self.calls = []

        def ensure_visible(self, t1, t2):
            self.calls.append((t1, t2))

    pager = Pager()
    chart._data_pager = pager

    chart._on_my_range_changed(None, [[0.0, 10.0], [0.0, 1.0]])
    chart._on_my_range_changed(None, [[20.0, 30.0], [0.0, 1.0]])

    assert emitted == []
    assert pager.calls == []

    chart._flush_range_changed()

    assert len(emitted) == 1
    assert len(pager.calls) == 1
    assert emitted[0][0] < emitted[0][1]


def test_trade_marker_arrays_build_from_visible_rt_window(qapp) -> None:
    engine = _Engine(_df())
    rt = engine.df["time"].values
    trade = type(
        "Trade",
        (),
        {
            "entry_index": 2,
            "exit_index": 5,
            "entry_price": 101.0,
            "exit_price": 104.0,
            "pnl": 3.0,
            "side": "long",
        },
    )()
    arrays = build_trade_marker_arrays(
        rt=rt,
        trades=[trade],
        df_len=len(engine.df),
        show_connections=True,
    )

    assert arrays.bx == [2]
    assert arrays.cx == [5]
    assert arrays.win_x[:2] == [2.0, 5.0]
    assert np.isnan(arrays.win_x[2])
