"""Tests for overlay diff reconciliation (Roadmap v2 / S4).

Verifies stable ID generation, diff computation, and viewport culling for
chart overlays.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from gui.chart_tab.overlay_diff import (
    cached_time64,
    diff_overlays,
    drawing_id,
    is_drawing_in_viewport,
)


def test_cached_time64_returns_datetime64():
    d = {"t": "2024-01-02 10:00"}
    val = cached_time64(d, "t", "_t_np")
    assert val is not None
    assert str(val).startswith("2024-01-02T10:00")
    # Cached on the dict.
    assert "_t_np" in d


def test_cached_time64_reuses_cache():
    d = {"t": "2024-01-02 10:00"}
    v1 = cached_time64(d, "t", "_t_np")
    cached = d["_t_np"]
    v2 = cached_time64(d, "t", "_t_np")
    # Same cached object returned (no re-parse).
    assert d["_t_np"] is cached
    assert v1 == v2


def test_cached_time64_invalidates_on_source_change():
    """If the source string changes (ROI drag), cache auto-refreshes."""
    d = {"t1": "2024-01-02 10:00"}
    v1 = cached_time64(d, "t1", "_t1_np")
    # Simulate a drag rewriting the time string.
    d["t1"] = "2024-01-02 15:00"
    v2 = cached_time64(d, "t1", "_t1_np")
    assert v1 != v2
    assert str(v2).startswith("2024-01-02T15:00")


def test_cached_time64_missing_key_returns_none():
    assert cached_time64({}, "t", "_t_np") is None


def test_drawing_id_stable_for_same_drawing():
    """Same drawing produces same ID."""
    d = {"type": "line", "t1": "2024-01-02 10:00", "t2": "2024-01-02 11:00",
         "p1": 100.0, "p2": 105.0, "color": "white", "width": 2}
    id1 = drawing_id(d)
    id2 = drawing_id(dict(d))  # Copy
    assert id1 == id2


def test_drawing_id_differs_for_different_geometry():
    """Different geometry produces different ID."""
    d1 = {"type": "line", "t1": "2024-01-02 10:00", "p1": 100.0, "p2": 105.0}
    d2 = {"type": "line", "t1": "2024-01-02 10:00", "p1": 100.0, "p2": 110.0}
    assert drawing_id(d1) != drawing_id(d2)


def test_drawing_id_differs_for_different_color():
    """Different color produces different ID (triggers update)."""
    d1 = {"type": "rect", "t1": "2024-01-02 10:00", "color": "white"}
    d2 = {"type": "rect", "t1": "2024-01-02 10:00", "color": "red"}
    assert drawing_id(d1) != drawing_id(d2)


def test_diff_overlays_detects_additions():
    """New drawings not in old set are flagged for addition."""
    old_ids = {}
    new_drawings = [
        {"type": "line", "t1": "2024-01-02 10:00", "p1": 100.0, "p2": 105.0},
        {"type": "rect", "t1": "2024-01-02 11:00", "p1": 90.0, "p2": 95.0},
    ]
    to_remove, to_keep, to_add = diff_overlays(old_ids, new_drawings)
    assert len(to_add) == 2
    assert len(to_remove) == 0
    assert len(to_keep) == 0


def test_diff_overlays_detects_removals():
    """Drawings in old but not new are flagged for removal."""
    d1 = {"type": "line", "t1": "2024-01-02 10:00", "p1": 100.0, "p2": 105.0}
    old_ids = {drawing_id(d1): "fake_item"}
    new_drawings = []  # All removed.
    to_remove, to_keep, to_add = diff_overlays(old_ids, new_drawings)
    assert len(to_remove) == 1
    assert len(to_keep) == 0
    assert len(to_add) == 0


def test_diff_overlays_reuses_unchanged():
    """Unchanged drawings are kept (reused), not recreated."""
    d1 = {"type": "line", "t1": "2024-01-02 10:00", "p1": 100.0, "p2": 105.0}
    d2 = {"type": "rect", "t1": "2024-01-02 11:00", "p1": 90.0, "p2": 95.0}
    old_ids = {drawing_id(d1): "item1", drawing_id(d2): "item2"}
    new_drawings = [dict(d1), dict(d2)]  # Same drawings.
    to_remove, to_keep, to_add = diff_overlays(old_ids, new_drawings)
    assert len(to_remove) == 0
    assert len(to_keep) == 2
    assert len(to_add) == 0


def test_diff_overlays_mixed_changes():
    """Diff correctly handles add + remove + keep simultaneously."""
    d_keep = {"type": "line", "t1": "2024-01-02 10:00", "p1": 100.0, "p2": 105.0}
    d_remove = {"type": "rect", "t1": "2024-01-02 11:00", "p1": 90.0, "p2": 95.0}
    d_add = {"type": "text", "t": "2024-01-02 12:00", "p": 102.0, "text": "new"}

    old_ids = {drawing_id(d_keep): "item_keep", drawing_id(d_remove): "item_remove"}
    new_drawings = [dict(d_keep), dict(d_add)]

    to_remove, to_keep, to_add = diff_overlays(old_ids, new_drawings)
    assert len(to_remove) == 1
    assert len(to_keep) == 1
    assert len(to_add) == 1
    assert drawing_id(d_remove) in to_remove
    assert drawing_id(d_keep) in to_keep


def test_viewport_culling_single_point_inside():
    """Single-point drawing inside viewport is visible."""
    vmin = np.datetime64("2024-01-02 10:00")
    vmax = np.datetime64("2024-01-02 12:00")
    d = {"type": "text", "t": "2024-01-02 11:00", "p": 100.0}
    assert is_drawing_in_viewport(d, vmin, vmax) is True


def test_viewport_culling_single_point_outside():
    """Single-point drawing outside viewport is culled."""
    vmin = np.datetime64("2024-01-02 10:00")
    vmax = np.datetime64("2024-01-02 12:00")
    d = {"type": "text", "t": "2024-01-02 15:00", "p": 100.0}
    assert is_drawing_in_viewport(d, vmin, vmax) is False


def test_viewport_culling_line_intersects():
    """Two-point drawing intersecting viewport is visible."""
    vmin = np.datetime64("2024-01-02 10:00")
    vmax = np.datetime64("2024-01-02 12:00")
    # Line spans from before viewport to inside.
    d = {"type": "line", "t1": "2024-01-02 09:00", "t2": "2024-01-02 11:00",
         "p1": 100.0, "p2": 105.0}
    assert is_drawing_in_viewport(d, vmin, vmax) is True


def test_viewport_culling_line_entirely_before():
    """Two-point drawing entirely before viewport is culled."""
    vmin = np.datetime64("2024-01-02 10:00")
    vmax = np.datetime64("2024-01-02 12:00")
    d = {"type": "line", "t1": "2024-01-02 07:00", "t2": "2024-01-02 08:00",
         "p1": 100.0, "p2": 105.0}
    assert is_drawing_in_viewport(d, vmin, vmax) is False


def test_viewport_culling_hline_no_time_always_visible():
    """Drawing with no time coordinate (hline) is always visible."""
    vmin = np.datetime64("2024-01-02 10:00")
    vmax = np.datetime64("2024-01-02 12:00")
    d = {"type": "hline", "p": 100.0}  # No time field.
    assert is_drawing_in_viewport(d, vmin, vmax) is True
