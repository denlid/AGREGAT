from __future__ import annotations

import os

import pandas as pd
import pytest
from PySide6.QtWidgets import QApplication

from engine.backtest_engine import BacktestEngine
from gui.chart_tab import ChartTab

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def _ohlc_frame(rows: int = 8) -> pd.DataFrame:
    times = pd.date_range("2024-01-02 10:00", periods=rows, freq="1min")
    return pd.DataFrame(
        {
            "time": times,
            "open": [100.0 + i for i in range(rows)],
            "high": [101.0 + i for i in range(rows)],
            "low": [99.0 + i for i in range(rows)],
            "close": [100.5 + i for i in range(rows)],
            "volume": [1.0 for _ in range(rows)],
        }
    )


def test_switch_to_higher_timeframe_renders_without_extra_step(qapp: QApplication) -> None:
    engine = BacktestEngine()
    chart = ChartTab(engine)
    engine.load_data(_ohlc_frame())

    assert engine.current_idx == -1

    chart.visual_tf_cb.setCurrentText("5m")

    arr = chart.candlestick_item._arr
    assert arr.shape[0] == 1
    assert chart._cached_res_df is not None
    assert chart._cached_res_df.iloc[0]["time"] == pd.Timestamp("2024-01-02 10:00")
