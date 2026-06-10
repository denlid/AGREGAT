from __future__ import annotations

import os

import numpy as np
import pandas as pd
import pytest
from PySide6.QtWidgets import QApplication

from engine.backtest_engine import BacktestEngine
from gui.controllers.session_state_controller import SessionStateController
from gui.controllers.toolbar_controller import ToolbarController
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


def test_large_tail_window_keeps_global_x_and_culls_old_drawings(qapp: QApplication) -> None:
    engine = BacktestEngine()
    chart = ChartTab(engine)
    df = _ohlc_frame(6_001)
    engine.load_data(df)
    engine.current_idx = len(df) - 1
    chart.drawing_ctrl.user_drawings = [
        {
            "type": "text",
            "t": str(df.iloc[0]["time"]),
            "p": 100.0,
            "text": "old",
            "color": "#FFFFFF",
        },
        {
            "type": "text",
            "t": str(df.iloc[1001]["time"]),
            "p": 100.0,
            "text": "tail",
            "color": "#FFFFFF",
        },
    ]
    chart.drawing_ctrl._dirty = True

    chart.redraw_chart()

    arr = chart.candlestick_item._arr
    assert chart._x_offset == 1001
    assert arr[0, 0] == 1001
    assert arr[-1, 0] == 6000
    assert len(chart.drawing_ctrl._graphic_items) == 1
    assert chart.drawing_ctrl._graphic_items[0].pos().x() == 1001
    sx, _ = chart.drawing_ctrl._snap(5800.0, 100.0, chart.res_times)
    assert sx == 5800
    assert chart.drawing_ctrl._idx_to_time(5800, chart.res_times) == str(
        pd.to_datetime(df.iloc[5800]["time"])
    )
    assert (
        chart.drawing_ctrl._time_to_idx(str(df.iloc[5800]["time"]), chart.res_times)
        == 5800
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


def test_camera_range_change_does_not_swap_charttab_candle_data(qapp: QApplication) -> None:
    engine = BacktestEngine()
    chart = ChartTab(engine)
    df = _ohlc_frame(6_001)
    engine.load_data(df)
    engine.current_idx = len(df) - 1

    chart.visual_tf_cb.setCurrentText("5m")
    before = chart.candlestick_item._arr.copy()

    assert chart.candlestick_item.active_tf is None
    assert getattr(chart.candlestick_item, "_pyramid", None) is None
    assert before.shape[0] == len(chart.res_times)

    chart.plot_widget.getViewBox().setRange(
        xRange=[chart._x_offset - 100, chart._x_offset + len(chart.res_times) + 100],
        padding=0,
    )
    qapp.processEvents()

    after = chart.candlestick_item._arr
    assert after.shape[0] == len(chart.res_times)
    np.testing.assert_array_equal(after, before)


def test_switch_timeframe_resets_viewport_to_rendered_candles(qapp: QApplication) -> None:
    engine = BacktestEngine()
    chart = ChartTab(engine)
    df = _ohlc_frame(6_001)
    engine.load_data(df)
    engine.current_idx = len(df) - 1

    chart.redraw_chart()
    chart.plot_widget.getViewBox().setRange(xRange=[0, 100], padding=0)
    chart._tracked_xrange = [0, 100]
    chart._user_zoom_width = 100

    chart.visual_tf_cb.setCurrentText("5m")
    qapp.processEvents()

    arr = chart.candlestick_item._arr
    xr = chart.plot_widget.getViewBox().viewRange()[0]

    assert arr.shape[0] == len(chart.res_times)
    assert xr[1] >= arr[0, 0]
    assert xr[0] <= arr[-1, 0]
    assert chart._tracked_xrange is not None
    assert chart._tracked_xrange[1] >= arr[0, 0]
    assert chart._tracked_xrange[0] <= arr[-1, 0]


def test_jump_to_end_uses_global_x_offset(qapp: QApplication) -> None:
    class _Host:
        chart_tab_2 = None

    engine = BacktestEngine()
    chart = ChartTab(engine)
    df = _ohlc_frame(6_001)
    engine.load_data(df)
    engine.current_idx = len(df) - 1
    chart.redraw_chart()
    chart.show()
    qapp.processEvents()

    host = _Host()
    host.chart_tab = chart
    ToolbarController(host).jump_to_end()

    xr = chart.plot_widget.getViewBox().viewRange()[0]
    expected_last_x = chart._x_offset + len(chart.res_times) - 1

    assert chart._x_offset == 1001
    assert xr[1] == pytest.approx(expected_last_x + 5)


def test_legacy_candles_mode_restore_enables_candle_layer(qapp: QApplication) -> None:
    class _Host:
        pass

    host = _Host()
    host.chart_tab = ChartTab(BacktestEngine())

    ctrl = SessionStateController(host, "")
    ctrl._restore_chart_mode("Candles")

    assert host.chart_tab.chart_mode_cb.currentText().endswith("Candles")
    assert host.chart_tab.candlestick_item.isVisible()
    assert not host.chart_tab.price_curve.isVisible()


def test_redraw_reapplies_current_chart_mode_visibility(qapp: QApplication) -> None:
    engine = BacktestEngine()
    chart = ChartTab(engine)
    df = _ohlc_frame(100)
    engine.load_data(df)
    engine.current_idx = len(df) - 1
    chart.chart_mode_cb.setCurrentIndex(1)
    chart.candlestick_item.hide()
    chart.price_curve.show()

    chart.redraw_chart()

    assert chart.candlestick_item.isVisible()
    assert not chart.price_curve.isVisible()


def test_switching_from_line_back_to_candles_rebuilds_visible_candles(
    qapp: QApplication,
) -> None:
    engine = BacktestEngine()
    chart = ChartTab(engine)
    df = _ohlc_frame(6_001)
    engine.load_data(df)
    engine.current_idx = len(df) - 1

    chart.chart_mode_cb.setCurrentIndex(0)
    chart.plot_widget.getViewBox().setRange(xRange=[-1_000, -900], padding=0)
    chart._need_autorange = False
    chart._tracked_xrange = [-1_000, -900]
    chart._user_zoom_width = 100
    chart.redraw_chart()

    assert chart.price_curve.isVisible()
    assert not chart.candlestick_item.isVisible()

    chart.chart_mode_cb.setCurrentIndex(1)
    qapp.processEvents()

    arr = chart.candlestick_item._arr
    xr = chart.plot_widget.getViewBox().viewRange()[0]
    stats = chart.candlestick_item._paint_stats

    assert chart.candlestick_item.isVisible()
    assert not chart.price_curve.isVisible()
    assert arr.shape[0] == len(chart.res_times)
    assert xr[1] >= arr[0, 0]
    assert xr[0] <= arr[-1, 0]
    assert stats["bars_drawn"] > 0
