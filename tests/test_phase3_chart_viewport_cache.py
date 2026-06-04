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


def test_chart_trade_marker_payload_caches_by_rt_identity(qapp) -> None:
    tab = ChartTab(_Engine(_df()))
    rt = tab.engine.df["time"].values
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
    tab.engine.trades = [trade]

    payload1, hit1 = tab._build_trade_marker_payload(rt)
    payload2, hit2 = tab._build_trade_marker_payload(rt)

    assert hit1 is False
    assert hit2 is True
    assert payload1 is payload2
    assert payload1["buy_x"].tolist() == [2.0]
    assert payload1["close_x"].tolist() == [5.0]
    assert payload1["win_x"].shape == (3,)
    assert payload1["win_x"][0] == 2.0
    assert payload1["win_x"][1] == 5.0
    assert np.isnan(payload1["win_x"][2])
