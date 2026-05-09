"""Tests for Phase 4 sections in the institutional tear-sheet."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from engine.report.charts import (
    cluster_scatter_svg,
    exposure_heatmap_svg,
    regime_bar_chart_svg,
)
from engine.report.tearsheet import render_html_tearsheet


@dataclass
class _Trade:
    id: int
    direction: str
    entry_time: str
    exit_time: str
    entry_price: float
    exit_price: float
    sl: float
    tp: float
    pnl: float
    volume: float
    session: str
    exit_reason: str
    entry_bar_index: int
    exit_bar_index: int
    mae_r: float = -0.4
    mfe_r: float = 0.6
    spread_cost: float = 0.0
    slippage_cost: float = 0.0
    commission_cost: float = 0.0


def _make_trades(n: int = 30):
    out = []
    for i in range(n):
        sess = ("london", "ny", "asia")[i % 3]
        reason = ("TP", "SL", "EOD")[i % 3]
        pnl = 50.0 if reason == "TP" else (-30.0 if reason == "SL" else 10.0)
        out.append(
            _Trade(
                id=i + 1,
                direction="long" if i % 2 == 0 else "short",
                entry_time=f"2024-01-{(i % 28) + 1:02d} {(i % 24):02d}:00",
                exit_time=f"2024-01-{(i % 28) + 1:02d} {(i % 24):02d}:30",
                entry_price=100.0,
                exit_price=101.0,
                sl=99.0,
                tp=103.0,
                pnl=pnl,
                volume=1.0,
                session=sess,
                exit_reason=reason,
                entry_bar_index=i * 10 + 50,
                exit_bar_index=i * 10 + 55,
            )
        )
    return out


def _make_df(n_bars: int = 600):
    idx = pd.date_range("2024-01-01", periods=n_bars, freq="1min")
    base = np.linspace(100.0, 110.0, n_bars)
    return pd.DataFrame(
        {
            "open": base,
            "high": base + 0.5,
            "low": base - 0.5,
            "close": base,
        },
        index=idx,
    )


def _meta_summary():
    meta = {"strategy": "X", "asset": "Y", "initial_equity": 10_000.0}
    summary = {"initial_equity": 10_000.0, "total_pnl": 100.0}
    return meta, summary


# ---------------------------------------------------------------------------
# SVG helpers
# ---------------------------------------------------------------------------


def test_exposure_heatmap_svg_is_well_formed():
    fraction = np.zeros((7, 24), dtype=float)
    fraction[0, 10] = 0.5
    fraction[3, 14] = 1.0
    svg = exposure_heatmap_svg(fraction)
    assert svg.startswith("<svg") and svg.endswith("</svg>")
    assert "Mon" in svg and "Sun" in svg


def test_exposure_heatmap_svg_handles_empty():
    svg = exposure_heatmap_svg(np.zeros((0, 0)))
    assert "<svg" in svg


def test_regime_bar_chart_svg_handles_empty():
    svg = regime_bar_chart_svg([])
    assert "<svg" in svg


def test_cluster_scatter_svg_handles_empty():
    svg = cluster_scatter_svg(None, None)
    assert "<svg" in svg


def test_cluster_scatter_svg_renders_points():
    proj = np.array([[0.0, 0.0], [1.0, 1.0], [-1.0, 0.5]])
    labels = np.array([0, 1, 0])
    svg = cluster_scatter_svg(proj, labels)
    assert svg.count("<circle") == 3


# ---------------------------------------------------------------------------
# Tearsheet integration
# ---------------------------------------------------------------------------


def test_tearsheet_includes_phase4_sections_when_df_provided():
    meta, summary = _meta_summary()
    trades = _make_trades(30)
    df = _make_df()
    html = render_html_tearsheet(meta, summary, trades, theme="dark", df=df)
    assert 'id="regime"' in html
    assert 'id="clusters"' in html
    assert 'id="exposure"' in html


def test_tearsheet_skips_regime_when_df_is_none():
    """Without a df the regime/exposure sections must be omitted —
    they need OHLC bars to compute SMA/ATR."""
    meta, summary = _meta_summary()
    trades = _make_trades(30)
    html = render_html_tearsheet(meta, summary, trades, theme="dark", df=None)
    assert 'id="regime"' not in html
    # Clusters depend only on trades, so they must still appear.
    assert 'id="clusters"' in html


def test_tearsheet_skips_clusters_when_too_few_trades():
    meta, summary = _meta_summary()
    trades = _make_trades(3)  # below the 6-trade minimum
    df = _make_df()
    html = render_html_tearsheet(meta, summary, trades, theme="dark", df=df)
    assert 'id="clusters"' not in html
