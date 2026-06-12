from __future__ import annotations

import numpy as np
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


def test_strategy_drawings_are_tracked_and_clearable(qapp: QApplication) -> None:
    engine = BacktestEngine()
    chart = ChartTab(engine)
    rt = pd.date_range("2024-01-02 10:00", periods=3, freq="1min").to_numpy()
    engine.drawings.append(
        {
            "type": "line",
            "t1": "2024-01-02 10:00",
            "p1": 100.0,
            "t2": "2024-01-02 10:01",
            "p2": 101.0,
            "color": "yellow",
            "width": 1,
        }
    )

    chart._draw_strategy(np.asarray(rt))

    assert len(chart.strategy_graphic_items) == 1
    assert chart.strategy_graphic_items[0] in chart.plot_widget.items()

    chart._clear_strategy_drawings()

    assert chart.strategy_graphic_items == []
    assert engine.drawings == []
