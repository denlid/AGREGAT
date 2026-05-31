from __future__ import annotations

import os
# Import pandas first to avoid the shibokensupport '_SixMetaPathImporter' bug on import
import pandas as pd
import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
import pyqtgraph as pg
from PySide6.QtWidgets import QApplication

from gui.controllers.drawing_controller import DrawingController


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_drawing_controller_hover_properties(qapp: QApplication) -> None:
    # 1. Initialize PlotWidget and DrawingController
    pw = pg.PlotWidget()
    ctrl = DrawingController(pw, persist_key="test_hover")
    
    # Clean up any loaded drawings to isolate the test
    ctrl.user_drawings.clear()
    
    # 2. Add drawings
    d_hline = {"type": "hline", "p": 100.0, "color": "#FFFFFF", "width": 2}
    d_marker = {"type": "marker", "t": "2026-05-31 12:00:00", "p": 150.0, "color": "#FF1744"}
    d_line = {"type": "line", "t1": "2026-05-31 11:00:00", "p1": 90.0, "t2": "2026-05-31 13:00:00", "p2": 110.0, "color": "#00FF00", "width": 2}
    d_rect = {"type": "rect", "t1": "2026-05-31 11:00:00", "p1": 80.0, "t2": "2026-05-31 13:00:00", "p2": 120.0, "color": "#0000FF", "width": 2}
    
    ctrl.user_drawings.extend([d_hline, d_marker, d_line, d_rect])
    
    # Render with mock time indexes
    rt = np.array(["2026-05-31 10:00:00", "2026-05-31 11:00:00", "2026-05-31 12:00:00", "2026-05-31 13:00:00", "2026-05-31 14:00:00"], dtype=object)
    ctrl.render(rt)
    
    # Verify we have created graphic items
    assert len(ctrl._graphic_items) > 0
    
    # Check that hline, marker, line, rect items have d_ref and mouseHovering initialized
    has_hline = False
    has_marker = False
    has_line = False
    has_rect = False
    
    for item in ctrl._graphic_items:
        if hasattr(item, "d_ref"):
            d = item.d_ref
            dtype = d.get("type")
            if dtype == "hline":
                has_hline = True
            elif dtype == "marker":
                has_marker = True
            elif dtype == "line":
                has_line = True
            elif dtype == "rect":
                has_rect = True
                
            # Check hover tracking exists
            assert hasattr(item, "mouseHovering")
            assert item.mouseHovering is False
            
            # Simulate a hover event to test that mouseHovering can toggle
            class MockHoverEvent:
                def __init__(self, exit_flag):
                    self._exit_flag = exit_flag
                def isExit(self):
                    return self._exit_flag
            
            if hasattr(item, "hoverEvent"):
                # Simulate mouse enter
                item.hoverEvent(MockHoverEvent(False))
                assert item.mouseHovering is True
                
                # Simulate mouse exit
                item.hoverEvent(MockHoverEvent(True))
                assert item.mouseHovering is False

    assert has_hline, "hline item was not found or lacked d_ref"
    assert has_marker, "marker item was not found or lacked d_ref"
    assert has_line, "line item was not found or lacked d_ref"
    assert has_rect, "rect item was not found or lacked d_ref"
