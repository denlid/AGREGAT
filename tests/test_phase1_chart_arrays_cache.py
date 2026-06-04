from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pandas as pd
import pytest
from PySide6.QtWidgets import QApplication

from gui.chart_tab.items import CandlestickItem
from gui.chart_tab.tab import ChartTab


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


def _df(n: int = 32) -> pd.DataFrame:
    rows = []
    for i in range(n):
        rows.append((f"2024-01-02 10:{i:02d}", 100.0 + i, 101.0 + i, 99.0 + i, 100.5 + i))
    df = pd.DataFrame(rows, columns=["time", "open", "high", "low", "close"])
    df["time"] = pd.to_datetime(df["time"])
    return df


def test_build_chart_arrays_caches_by_res_df_identity(qapp) -> None:
    tab = ChartTab(_Engine(_df()))
    res_df = tab.engine.df.copy()

    arr1, hit1 = tab._build_chart_arrays(res_df)
    arr2, hit2 = tab._build_chart_arrays(res_df)

    assert hit1 is False
    assert hit2 is True
    assert arr1 is arr2
    assert arr1["candle_arr"].shape == (len(res_df), 5)
    assert arr1["candle_arr"].flags.writeable is False


def test_on_tf_changed_invalidates_chart_arrays_cache(qapp) -> None:
    tab = ChartTab(_Engine(_df()))
    res_df = tab.engine.df.copy()
    _, _ = tab._build_chart_arrays(res_df)

    assert tab._cached_chart_arrays is not None
    tab.on_tf_changed("5m")
    assert tab._cached_chart_arrays is None
    assert tab._cached_chart_arrays_key is None


def test_candlestick_item_skips_rebuild_for_same_readonly_numpy_array(qapp) -> None:
    item = CandlestickItem(max_bars=1_000)
    arr = np.array(
        [
            [0.0, 100.0, 101.0, 99.0, 102.0],
            [1.0, 101.0, 100.5, 100.0, 102.5],
        ],
        dtype=np.float64,
    )
    arr.setflags(write=False)

    item.set_data(arr)
    first_picture = item.picture
    first_lines = list(item._all_wicks)

    item.set_data(arr)

    assert item._paint_stats["data_cache_hit"] is True
    assert item.picture is first_picture
    assert item._all_wicks == first_lines


def test_candlestick_item_set_colors_regenerates_numpy_backed_picture(qapp) -> None:
    item = CandlestickItem(max_bars=1_000)
    arr = np.array(
        [
            [0.0, 100.0, 101.0, 99.0, 102.0],
            [1.0, 101.0, 100.5, 100.0, 102.5],
        ],
        dtype=np.float64,
    )
    arr.setflags(write=False)
    item.set_data(arr)

    item._paint_stats["gen_time_ms"] = -1.0
    item.set_colors("#123456", "#654321")

    assert item.bull_color == "#123456"
    assert item.bear_color == "#654321"
    assert item._paint_stats["gen_time_ms"] >= 0.0
