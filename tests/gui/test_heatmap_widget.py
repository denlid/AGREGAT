"""Phase 2 EPIC 2.1 — tests for the extracted HeatmapWidget."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from PySide6.QtWidgets import QApplication

from gui.analytics.heatmap_widget import HeatmapExtras, HeatmapWidget


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_set_grid_populates_dimensions(qapp: QApplication) -> None:
    hm = HeatmapWidget()
    data = np.array([[1.0, 2.0, -1.0], [3.0, np.nan, 4.0]])
    hm.set_grid(data, x_labels=["A", "B", "C"], y_labels=["R1", "R2"])
    assert hm._rows == 2
    assert hm._cols == 3
    assert hm._data is not None and hm._data.shape == data.shape


def test_set_grid_handles_empty_data(qapp: QApplication) -> None:
    hm = HeatmapWidget()
    hm.set_grid(None)
    assert hm._rows == 0
    assert hm._cols == 0
    hm.set_grid(np.zeros((0, 0)))
    assert hm._rows == 0


def test_set_grid_handles_all_nan(qapp: QApplication) -> None:
    hm = HeatmapWidget()
    hm.set_grid(np.full((3, 3), np.nan))
    # Nothing to render — guards work and we end with zero dims.
    assert hm._rows == 0


def test_tooltip_html_includes_extras(qapp: QApplication) -> None:
    hm = HeatmapWidget()
    data = np.array([[100.0, -50.0], [200.0, 0.0]])
    counts = np.array([[5, 3], [4, 0]])
    wr = np.array([[0.6, 0.4], [0.7, np.nan]])
    hm.set_grid(
        data,
        x_labels=["A", "B"],
        y_labels=["R0", "R1"],
        extras=HeatmapExtras(counts=counts, win_rate=wr),
    )
    html, _ = hm._build_tooltip_html(0, 0, "A", "R0", 100.0)
    assert "n</b>=5" in html or "n=5" in html
    assert "WR</b>=60%" in html or "WR=60%" in html
    assert "$+100" in html


def test_outlier_softening_does_not_change_dims(qapp: QApplication) -> None:
    hm = HeatmapWidget()
    data = np.array([[1.0, 10000.0], [-1.0, 2.0]])
    hm.set_grid(data, x_labels=["A", "B"], y_labels=["R0", "R1"])
    hm.set_outlier_softening(True)
    assert hm._rows == 2 and hm._cols == 2
    assert hm._tanh_softening is True


def test_annotations_pick_best_and_worst(qapp: QApplication) -> None:
    hm = HeatmapWidget()
    data = np.array([[1.0, -5.0], [10.0, 2.0]])
    hm.set_grid(data, x_labels=["A", "B"], y_labels=["R0", "R1"])
    # One ⭐ for the max (10), one ⚠ for the min (-5).
    assert len(hm._anno_items) == 2


def test_diff_mode_toggle(qapp: QApplication) -> None:
    hm = HeatmapWidget()
    data = np.array([[1.0, 2.0], [3.0, 4.0]])
    hm.set_grid(data, x_labels=["A", "B"], y_labels=["R0", "R1"])
    hm.set_diff_mode(True)
    html, _ = hm._build_tooltip_html(0, 0, "A", "R0", 1.0)
    assert "vs mean" in html


def test_clear_selection(qapp: QApplication) -> None:
    hm = HeatmapWidget()
    data = np.array([[1.0, 2.0], [3.0, 4.0]])
    hm.set_grid(data, x_labels=["A", "B"], y_labels=["R0", "R1"])
    hm._selected = (0, 1)
    hm.clear_selection()
    assert hm._selected is None
