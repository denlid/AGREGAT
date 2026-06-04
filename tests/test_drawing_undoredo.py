import os
import sys
import pytest
import copy

# CRITICAL: Import pandas BEFORE PySide6 to avoid shiboken/dateutil/six conflict
import numpy  # noqa: F401
import pandas  # noqa: F401

from unittest.mock import MagicMock

# PySide6 must come AFTER pandas
from PySide6.QtWidgets import QApplication
app = QApplication.instance() or QApplication([])

import pyqtgraph as pg

from gui.controllers.drawing_controller import DrawingController
from gui.persistence import drawings_persist

def cleanup_file(symbol):
    path = drawings_persist.config_path(symbol)
    if os.path.exists(path):
        try:
            os.remove(path)
        except OSError:
            pass

def test_drawing_controller_creation_and_basic_undo():
    symbol = "test_basic_undo"
    cleanup_file(symbol)
    
    # Instantiate controller with mock plot widget and test symbol
    mock_pw = MagicMock(spec=pg.PlotWidget)
    ctrl = DrawingController(mock_pw, persist_key=symbol)

    try:
        # Initial state should be empty
        assert len(ctrl.user_drawings) == 0

        # 1. Add a horizontal line
        ctrl._save_state()
        ctrl.user_drawings.append({"type": "hline", "p": 100.0, "color": "#FFFFFF", "width": 2})
        ctrl.save()

        assert len(ctrl.user_drawings) == 1
        assert ctrl.user_drawings[0]["p"] == 100.0

        # 2. Undo should restore back to empty list
        ctrl.undo()
        assert len(ctrl.user_drawings) == 0

        # 3. Redo should restore the line back
        ctrl.redo()
        assert len(ctrl.user_drawings) == 1
        assert ctrl.user_drawings[0]["p"] == 100.0
    finally:
        cleanup_file(symbol)

def test_drawing_property_mutation_undo_redo():
    symbol = "test_mutation_undo"
    cleanup_file(symbol)
    
    # Test that mutating properties of an existing drawing gets correctly undone/redone (Deep Copy Test)
    mock_pw = MagicMock(spec=pg.PlotWidget)
    ctrl = DrawingController(mock_pw, persist_key=symbol)

    try:
        # Add a horizontal line
        ctrl._save_state()
        line_item = {"type": "hline", "p": 100.0, "color": "#FFFFFF", "width": 2}
        ctrl.user_drawings.append(line_item)
        ctrl.save()

        # Now change color (as inside _change_color)
        ctrl._save_state()
        ctrl.user_drawings[0]["color"] = "#FF0000"  # Change to red
        ctrl.save()

        assert ctrl.user_drawings[0]["color"] == "#FF0000"

        # Undo should restore the original color (#FFFFFF) and NOT delete the item!
        ctrl.undo()
        assert len(ctrl.user_drawings) == 1
        assert ctrl.user_drawings[0]["color"] == "#FFFFFF"

        # Redo should restore the red color
        ctrl.redo()
        assert len(ctrl.user_drawings) == 1
        assert ctrl.user_drawings[0]["color"] == "#FF0000"
    finally:
        cleanup_file(symbol)

def test_drawing_multiple_steps_undo():
    symbol = "test_multi_steps"
    cleanup_file(symbol)
    
    mock_pw = MagicMock(spec=pg.PlotWidget)
    ctrl = DrawingController(mock_pw, persist_key=symbol)

    try:
        # Step 1: Add a horizontal line
        ctrl._save_state()
        ctrl.user_drawings.append({"type": "hline", "p": 100.0, "color": "#FFFFFF", "width": 2})
        ctrl.save()

        # Step 2: Change its position (drag)
        ctrl._save_state()
        ctrl.user_drawings[0]["p"] = 150.0
        ctrl.save()

        # Step 3: Change its color
        ctrl._save_state()
        ctrl.user_drawings[0]["color"] = "#00FF00"
        ctrl.save()

        # Verify state before undoing
        assert ctrl.user_drawings[0]["p"] == 150.0
        assert ctrl.user_drawings[0]["color"] == "#00FF00"

        # Undo 1: should restore position 150 and color #FFFFFF
        ctrl.undo()
        assert ctrl.user_drawings[0]["p"] == 150.0
        assert ctrl.user_drawings[0]["color"] == "#FFFFFF"

        # Undo 2: should restore position 100 and color #FFFFFF
        ctrl.undo()
        assert ctrl.user_drawings[0]["p"] == 100.0
        assert ctrl.user_drawings[0]["color"] == "#FFFFFF"

        # Undo 3: should delete the item
        ctrl.undo()
        assert len(ctrl.user_drawings) == 0
    finally:
        cleanup_file(symbol)
