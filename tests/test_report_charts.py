"""Tests for engine.report.charts (Phase 3.1).

The chart helpers must produce well-formed inline SVG that survives a
basic XML parse and contains the expected geometry / colour cues.
Output is not pixel-perfect tested — the charts are smoke-tested for
shape and that they don't crash on edge-case inputs.
"""

from __future__ import annotations

from xml.etree import ElementTree as ET

import numpy as np

from engine.report import charts


def _is_well_formed_svg(s: str) -> bool:
    try:
        root = ET.fromstring(s)
    except ET.ParseError:
        return False
    return root.tag.endswith("svg")


def test_equity_curve_svg_is_well_formed_and_has_polyline():
    eq = [1000, 1100, 1050, 1300, 1280, 1500, 1450, 1700]
    svg = charts.equity_curve_svg(eq, initial_equity=1000)
    assert _is_well_formed_svg(svg)
    assert "polyline" in svg
    # Sign-aware colour: ends positive vs initial, expect POSITIVE.
    assert charts.POSITIVE in svg


def test_equity_curve_svg_handles_empty_input():
    svg = charts.equity_curve_svg([])
    assert _is_well_formed_svg(svg)
    assert "No equity" in svg


def test_underwater_svg_has_axis_ticks_and_negative_polygon():
    dd = [0.0, -1.0, -3.5, -7.2, -2.0, 0.0, -1.5]
    svg = charts.underwater_svg(dd)
    assert _is_well_formed_svg(svg)
    # Ticks render the deepest value
    assert "%" in svg
    assert "polygon" in svg


def test_monthly_heatmap_svg_renders_cells_per_month():
    pct = np.array(
        [[1, -2, 3, 0, 0, 0, 0, 0, 0, 0, 0, 0], [-1, 4, -2, 0, 0, 0, 0, 0, 0, 0, 0, 0]],
        dtype=float,
    )
    svg = charts.monthly_heatmap_svg([2024, 2025], pct)
    assert _is_well_formed_svg(svg)
    # 12 months × 2 years = 24 cell rects; plus 2 YTD rects = 26.
    rect_count = svg.count("<rect")
    assert rect_count >= 26
    # Both years labelled
    assert "2024" in svg and "2025" in svg


def test_monthly_heatmap_svg_handles_empty_grid():
    svg = charts.monthly_heatmap_svg([], np.empty((0, 12), dtype=float))
    assert _is_well_formed_svg(svg)
    assert "No monthly" in svg


def test_rolling_sharpe_svg_skips_when_below_window():
    svg = charts.rolling_sharpe_svg([float("nan")] * 5)
    assert "Need" in svg


def test_rolling_sharpe_svg_renders_when_enough_points():
    svg = charts.rolling_sharpe_svg([0.5, 0.6, 0.7, 0.8, 0.9, 1.0])
    assert _is_well_formed_svg(svg)
    assert "polyline" in svg


def test_monte_carlo_band_svg_renders_p5_p50_p95():
    n = 12
    svg = charts.monte_carlo_band_svg(
        p5=list(range(n)),
        p50=[v + 5 for v in range(n)],
        p95=[v + 12 for v in range(n)],
        initial_equity=0.0,
    )
    assert _is_well_formed_svg(svg)
    # All three lines plus enclosing band polygon
    assert svg.count("polyline") >= 3
    assert "polygon" in svg


def test_exit_reason_donut_svg_shows_legend_for_each_slice():
    svg = charts.exit_reason_donut_svg([("TP", 12), ("SL", 6), ("EOD", 2)])
    assert _is_well_formed_svg(svg)
    # Each slice => legend swatch (rect) + text label
    for label in ("TP", "SL", "EOD"):
        assert label in svg


def test_exit_reason_donut_svg_handles_zero_total():
    svg = charts.exit_reason_donut_svg([])
    assert "No exits" in svg
