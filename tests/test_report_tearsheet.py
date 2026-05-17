"""Tests for engine.report.tearsheet (Phase 3.1).

Verifies that the institutional tear-sheet renders all sections promised
in PLAN.md §3.1 — equity curve, monthly heatmap, rolling Sharpe, MC band,
underwater chart, exit-reason donut, session attribution, costs, top-10
tables, and the categorised metric drill-down.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import pytest

from engine.report.tearsheet import render_html_tearsheet
from engine.report.themes import known_themes
from engine.report_export import _build_summary, export_report


@dataclass
class _Trade:
    id: int
    direction: str
    entry_time: str
    exit_time: str
    pnl: float
    exit_reason: str = "TP"
    spread_cost: float = 0.5
    slippage_cost: float = 0.3
    commission: float = 0.2
    rr: float = 1.0
    session: str = "london"


class _Eng:
    def __init__(self, trades, init=100_000.0):
        self.trades = trades
        self.equity_track = [init] + [
            init + sum(t.pnl for t in trades[: i + 1]) for i in range(len(trades))
        ]
        self.initial_equity = init
        self.equity = init + sum(t.pnl for t in trades)
        self._date_range = (None, None)
        self.skipped_trades = []
        self.asset_config = type("AC", (), {"name": "TEST"})


def _make_trades(n: int = 30):
    trades = []
    for i in range(n):
        # Alternating wins / losses with small randomness
        pnl = 200 if i % 3 != 0 else -120
        if i % 11 == 0:
            pnl = -350  # occasional bigger loss
        trades.append(
            _Trade(
                id=i + 1,
                direction="long" if i % 2 == 0 else "short",
                entry_time=f"2024-{(i % 12) + 1:02d}-15 09:{(i * 3) % 60:02d}:00",
                exit_time=f"2024-{(i % 12) + 1:02d}-15 10:{(i * 3) % 60:02d}:00",
                pnl=pnl,
                exit_reason="TP" if pnl > 0 else "SL",
                spread_cost=0.6,
                slippage_cost=0.4,
                commission=0.3,
                session=("asia" if i % 3 == 0 else "london" if i % 3 == 1 else "ny"),
            )
        )
    return trades


def _meta(eng):
    return {
        "asset": "TEST",
        "strategy": "DemoStrategy",
        "n_trades": len(eng.trades),
        "initial_equity": eng.initial_equity,
        "final_equity": eng.equity,
        "date_range_start": "2024-01-01",
        "date_range_end": "2024-12-31",
        "exported_utc": "2024-12-31T12:00:00Z",
    }


def test_tearsheet_renders_all_required_sections():
    eng = _Eng(_make_trades(30))
    summary = _build_summary(eng)
    html = render_html_tearsheet(_meta(eng), summary, eng.trades)
    # Section anchors
    for anchor in (
        'id="equity"',
        'id="monthly"',
        'id="rolling"',
        'id="mc"',
        'id="underwater"',
        'id="exits"',
        'id="sessions"',
        'id="costs"',
        'id="detail"',
        'id="best10"',
        'id="worst10"',
    ):
        assert anchor in html, f"missing anchor {anchor}"
    # Categorised metrics
    for cat in ("Returns", "Risk", "Drawdown", "Trade Edge"):
        assert cat in html, f"missing category {cat}"


def test_tearsheet_includes_drilldown_payload_by_default():
    eng = _Eng(_make_trades(30))
    summary = _build_summary(eng)
    html = render_html_tearsheet(_meta(eng), summary, eng.trades)
    # JSON payload + modal markup must be present.
    assert "metric-detail-data" in html
    assert "metric-modal-bg" in html
    # Sharpe is a known-tooltip metric.
    assert 'data-key="sharpe_ratio"' in html


def test_tearsheet_drilldown_can_be_disabled():
    eng = _Eng(_make_trades(30))
    summary = _build_summary(eng)
    html = render_html_tearsheet(
        _meta(eng), summary, eng.trades, include_drilldown=False
    )
    assert "metric-detail-data" not in html
    assert "metric-modal-bg" not in html


@pytest.mark.parametrize("theme", list(known_themes()))
def test_tearsheet_supports_all_themes(theme):
    eng = _Eng(_make_trades(20))
    summary = _build_summary(eng)
    html = render_html_tearsheet(_meta(eng), summary, eng.trades, theme=theme)
    assert f'data-theme="{theme}"' in html
    # Each theme must declare its own --bg-page colour variable.
    assert "--bg-page" in html


def test_tearsheet_handles_empty_skipped_section_gracefully():
    eng = _Eng(_make_trades(15))
    summary = _build_summary(eng)
    summary["skipped_trade_count"] = 0
    html = render_html_tearsheet(_meta(eng), summary, eng.trades)
    # Skipped section should be omitted entirely when count is zero.
    assert 'id="skipped"' not in html


def test_tearsheet_renders_skipped_section_when_present():
    eng = _Eng(_make_trades(15))
    summary = _build_summary(eng)
    summary["skipped_trade_count"] = 7
    summary["skipped_trade_ratio_pct"] = 31.8
    summary["skipped_trade_warn"] = True
    summary["skipped_by_reason"] = {"LOT_LIMIT_STRICT": 5, "INVALID_SL": 2}
    html = render_html_tearsheet(_meta(eng), summary, eng.trades)
    assert 'id="skipped"' in html
    assert "LOT_LIMIT_STRICT" in html
    assert "31.8%" in html


def test_tearsheet_costs_section_includes_attribution_and_warning():
    eng = _Eng(_make_trades(40))
    summary = _build_summary(eng)
    html = render_html_tearsheet(_meta(eng), summary, eng.trades)
    assert "Spread Total" in html
    assert "Slippage Total" in html
    assert "Commission Total" in html
    assert "Total Costs" in html


def test_tearsheet_top_10_tables_have_at_most_ten_rows():
    eng = _Eng(_make_trades(60))
    summary = _build_summary(eng)
    html = render_html_tearsheet(_meta(eng), summary, eng.trades)
    # Each table starts with an "ID|Dir|Entry|P&L|Exit" header — count rows
    # in the best-trades section; the test is a smoke check that we are not
    # blowing past the 10-row cap.
    best_section = html.split('id="best10"')[1].split("</table>")[0]
    n_rows = best_section.count("<tr>")
    # 1 header + ≤10 body rows
    assert 1 <= n_rows <= 11, n_rows


def test_export_report_uses_tearsheet_by_default(tmp_path):
    eng = _Eng(_make_trades(20))
    folder = export_report(eng, str(tmp_path))
    assert os.path.isdir(folder)
    html = open(os.path.join(folder, "analytics.html")).read()
    assert "Tear-Sheet" in html
    assert "Monthly Returns Heatmap" in html


def test_export_report_legacy_layout_when_tearsheet_disabled(tmp_path):
    eng = _Eng(_make_trades(20))
    folder = export_report(eng, str(tmp_path), tearsheet=False)
    html = open(os.path.join(folder, "analytics.html")).read()
    assert "AGREGAT Backtest Report" in html
    assert "Tear-Sheet" not in html


def test_export_report_pdf_skipped_gracefully_when_weasyprint_missing(tmp_path):
    """``pdf=True`` must never break the run when WeasyPrint isn't available."""
    eng = _Eng(_make_trades(15))
    folder = export_report(eng, str(tmp_path), pdf=True)
    # HTML always exists; PDF only if weasyprint is installed.
    assert os.path.exists(os.path.join(folder, "analytics.html"))


def test_tearsheet_accepts_zero_trades_dataset_safely():
    """Edge case — empty trades shouldn't blow up downstream callers.

    ``export_report`` itself raises if no trades, but ``render_html_tearsheet``
    is also called directly (e.g. live preview) and must handle it.
    """
    summary = {
        "initial_equity": 1000.0,
        "final_equity": 1000.0,
        "total_pnl": 0.0,
        "total_return_pct": 0.0,
        "win_rate_pct": 0.0,
        "sharpe_ratio": 0.0,
        "calmar_ratio": 0.0,
        "max_drawdown_pct": 0.0,
    }
    meta = {
        "asset": "X",
        "strategy": "Y",
        "initial_equity": 1000.0,
        "final_equity": 1000.0,
    }
    html = render_html_tearsheet(meta, summary, [])
    assert "Tear-Sheet" in html
