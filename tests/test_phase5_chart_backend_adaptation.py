from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pandas as pd
import pytest
from PySide6.QtWidgets import QApplication

from gui.chart_tab.gpu_config import should_use_gl_candles
from gui.chart_tab.gl_candle import GLCandlestickItem, is_gl_candle_available, make_candlestick_item
from gui.chart_tab.items import CandlestickItem


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


def _df(n: int = 256) -> pd.DataFrame:
    base = pd.Timestamp("2024-01-02 10:00")
    rows = []
    for i in range(n):
        ts = base + pd.Timedelta(minutes=i)
        rows.append((ts, 100.0 + i, 101.0 + i, 99.0 + i, 100.5 + i))
    df = pd.DataFrame(rows, columns=["time", "open", "high", "low", "close"])
    df["time"] = pd.to_datetime(df["time"])
    return df


def test_phase5_gpu_policy_respects_explicit_gl_candle_override(qapp) -> None:
    assert should_use_gl_candles(force_gl_candles=True, bars_hint=1) is True
    assert should_use_gl_candles(force_gl_candles=False, bars_hint=5_000) is False


def test_phase5_gl_candle_factory_force_false_uses_software_item(qapp) -> None:
    item = make_candlestick_item(force=False)
    assert isinstance(item, CandlestickItem)
    assert not isinstance(item, GLCandlestickItem)


def test_phase5_gl_candle_factory_force_true_matches_availability(qapp) -> None:
    item = make_candlestick_item(force=True)
    assert isinstance(item, GLCandlestickItem if is_gl_candle_available() else CandlestickItem)


def test_phase5_candlestick_item_exposes_current_bar_budget(qapp) -> None:
    item = CandlestickItem(max_bars=2_000)
    item.set_max_bars(750)
    assert item._max_bars == 750
