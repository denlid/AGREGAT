from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pandas as pd
import pytest
from PySide6.QtWidgets import QApplication

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


def _df(n: int = 256) -> pd.DataFrame:
    base = pd.Timestamp("2024-01-02 10:00")
    rows = []
    for i in range(n):
        ts = base + pd.Timedelta(minutes=i)
        rows.append((ts, 100.0 + i, 101.0 + i, 99.0 + i, 100.5 + i))
    df = pd.DataFrame(rows, columns=["time", "open", "high", "low", "close"])
    df["time"] = pd.to_datetime(df["time"])
    return df


def test_phase5_backend_state_reports_gpu_policy(qapp) -> None:
    tab = ChartTab(_Engine(_df(64)))
    state = tab._phase5_backend_state(5_000)
    assert "gpu_enabled" in state
    assert "gpu_backend" in state
    assert "gl_candles_requested" in state
    assert "gl_candles_active" in state
    assert "render_backend" in state


def test_phase5_adaptive_lod_reduces_budget_on_slow_frame(qapp) -> None:
    tab = ChartTab(_Engine(_df(128)))
    tab.candlestick_item.set_max_bars(2_000)
    tab._adaptive_lod_cooldown = 0
    decision = tab._maybe_adapt_render_budget({"total_ms": 99.0})
    assert decision["adaptive_lod_applied"] is True
    assert decision["adaptive_reason"] == "frame_budget_exceeded"
    assert decision["adaptive_lod_prev"] == 2_000
    assert decision["adaptive_lod_new"] < 2_000
    assert tab.candlestick_item._max_bars == decision["adaptive_lod_new"]


def test_phase5_adaptive_lod_respects_cooldown(qapp) -> None:
    tab = ChartTab(_Engine(_df(128)))
    tab.candlestick_item.set_max_bars(2_000)
    tab._adaptive_lod_cooldown = 2
    decision = tab._maybe_adapt_render_budget({"total_ms": 99.0})
    assert decision["adaptive_lod_applied"] is False
    assert decision["adaptive_reason"] == "cooldown"
    assert tab.candlestick_item._max_bars == 2_000


def test_phase5_adaptive_lod_skips_fast_frame(qapp) -> None:
    tab = ChartTab(_Engine(_df(128)))
    tab.candlestick_item.set_max_bars(2_000)
    tab._adaptive_lod_cooldown = 0
    decision = tab._maybe_adapt_render_budget({"total_ms": 5.0})
    assert decision["adaptive_lod_applied"] is False
    assert decision["adaptive_reason"] == "within_budget"
    assert tab.candlestick_item._max_bars == 2_000
