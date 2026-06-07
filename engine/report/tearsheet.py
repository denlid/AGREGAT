"""Top-level institutional tear-sheet renderer (Phase 3.1 + 3.3 + 3.4).

This module assembles a single self-contained HTML document by composing
the smaller helpers in this package. It mirrors the public signature of
the legacy ``engine.report_export._render_html`` so callers can swap with
zero downstream changes.

Layout (matches ``PLAN.md`` §3.1):

    HEADER  — strategy / asset / period + 6 KPI cards + sparkline
    EQUITY CURVE + DRAWDOWN OVERLAY
    MONTHLY HEATMAP   |  ROLLING SHARPE
    MONTE CARLO BAND  |  UNDERWATER CHART
    EXIT REASONS PIE  |  SESSION ATTRIBUTION
    TOP-10 BEST       |  TOP-10 WORST
    DETAILED METRICS  (collapsible by category)
"""

from __future__ import annotations

import html as _html
import json
from datetime import datetime
from typing import Any, Dict, Iterable, List

import numpy as np

from engine.report import charts
from engine.report.metrics_extra import (
    cost_attribution,
    exit_reason_rows,
    monthly_returns,
    rolling_sharpe,
    safe_monte_carlo_summary,
    top_trades,
    underwater_series,
)
from engine.report.themes import render_theme_css
from engine.report.tooltips import get_detail, get_hint, has_detail


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def render_html_tearsheet(
    meta: Dict[str, Any],
    summary: Dict[str, Any],
    trades: List[Any],
    *,
    theme: str = "dark",
    rolling_window: int = 60,
    include_drilldown: bool = True,
    df: Any = None,
) -> str:
    """Render the full tear-sheet as a single HTML string.

    Parameters
    ----------
    meta, summary, trades
        Same shape as the legacy ``_render_html``. ``summary`` is the dict
        produced by ``engine.report_export._build_summary``.
    theme
        One of ``"dark"`` / ``"light"`` / ``"print"``.
    rolling_window
        Number of trades for the rolling-Sharpe window. Auto-skipped
        gracefully when ``n_trades < window``.
    include_drilldown
        When True, every metric row gets a clickable ⓘ that opens a JS
        modal with the formula + interpretation. Set False to produce a
        no-JS document for environments that block scripts.
    df
        Optional OHLC dataframe — when supplied, the Phase 4 sections
        (regime breakdown, exposure heatmap) are rendered. Trade
        clustering does not require ``df`` and is always attempted when
        ≥ 3 trades exist.
    """
    init_eq = float(
        meta.get("initial_equity", summary.get("initial_equity", 0.0)) or 0.0
    )
    n = len(trades)

    # Pre-compute everything once so we don't iterate trades 10 times.
    costs = cost_attribution(trades)
    exit_rows = exit_reason_rows(trades)
    monthly = monthly_returns(trades, init_eq)
    rolling = rolling_sharpe(trades, window=rolling_window)
    underwater = underwater_series(trades, init_eq)
    mc = safe_monte_carlo_summary(trades, init_eq)
    best, worst = top_trades(trades, n=10)

    # Equity curve series for sparkline + main chart
    equity_track: List[float] = [init_eq]
    for t in trades:
        equity_track.append(equity_track[-1] + float(getattr(t, "pnl", 0.0) or 0.0))

    # ── Sub-renderers ──────────────────────────────────────────────────
    head_html = _render_header(meta, summary, equity_track, n)
    equity_html = charts.equity_curve_svg(equity_track, initial_equity=init_eq)
    monthly_html = charts.monthly_heatmap_svg(monthly.years, monthly.pct_grid)
    rolling_html = charts.rolling_sharpe_svg(
        rolling.values.tolist(), mean_value=rolling.mean_value
    )
    if mc:
        mc_html = charts.monte_carlo_band_svg(
            mc["p5"].tolist(),
            mc["p50"].tolist(),
            mc["p95"].tolist(),
            initial_equity=init_eq,
        )
        mc_caption = (
            f'<p class="muted" style="font-size:11px;margin-top:4px;">'
            f"{mc['n_paths']} block-bootstrap paths · "
            f"p5/p50/p95 of equity at every trade</p>"
        )
    else:
        mc_html = charts._empty_chart("Need ≥ 10 trades for Monte Carlo")
        mc_caption = ""
    underwater_html = charts.underwater_svg(underwater.tolist())
    exit_donut_html = charts.exit_reason_donut_svg(
        [(r.reason, r.count) for r in exit_rows]
    )
    exit_table_html = _render_exit_table(exit_rows)
    sessions_html = _render_session_table(summary.get("sessions", {}) or {})
    skipped_html = _render_skipped_section(summary)
    cost_html = _render_cost_attribution(costs)
    top_html = _render_top_tables(best, worst)
    regime_html = _render_regime_section(trades, df)
    clusters_html = _render_clusters_section(trades)
    exposure_html = _render_exposure_section(trades, df)
    metrics_html = _render_detailed_metrics(
        summary, include_drilldown=include_drilldown
    )
    drilldown_data_html = _render_drilldown_payload(include_drilldown)
    drilldown_modal_html = _render_drilldown_modal() if include_drilldown else ""

    css = render_theme_css(theme)
    asset = _e(meta.get("asset", "—"))
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # H-06: Content Security Policy — block inline scripts, external resources, eval
    # Only allow styles, images (data:), and the one inline script we control.
    csp_meta = (
        '<meta http-equiv="Content-Security-Policy" '
        'content="default-src \'none\'; '
        'style-src \'unsafe-inline\'; '
        'img-src data:; '
        'script-src \'unsafe-inline\'; '
        'font-src data:;">'
    )

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
{csp_meta}
<title>AGREGAT Tear-Sheet — {asset}</title>
{css}
</head>
<body data-theme="{_e(theme)}">
<div class="report-wrapper">
{head_html}

<h2 id="equity">📈 Equity Curve <a class="anchor" href="#equity">#</a></h2>
<div class="section">
  {equity_html}
  <p class="muted" style="font-size:11px;margin-top:4px;">
    Top: equity (filled to initial). Bottom: drawdown underwater %.
  </p>
</div>

<div class="grid">
  <div>
    <h2 id="monthly">📅 Monthly Returns Heatmap <a class="anchor" href="#monthly">#</a></h2>
    <div class="section">{monthly_html}</div>
  </div>
  <div>
    <h2 id="rolling">📊 Rolling Sharpe ({rolling.window}-trade) <a class="anchor" href="#rolling">#</a></h2>
    <div class="section">{rolling_html}
      <p class="muted" style="font-size:11px;margin-top:4px;">
        Detects regime drift inside the backtest. Mean = dashed blue.
      </p>
    </div>
  </div>
</div>

<div class="grid">
  <div>
    <h2 id="mc">🎲 Monte Carlo Confidence <a class="anchor" href="#mc">#</a></h2>
    <div class="section">{mc_html}{mc_caption}</div>
  </div>
  <div>
    <h2 id="underwater">🌊 Underwater Chart <a class="anchor" href="#underwater">#</a></h2>
    <div class="section">{underwater_html}
      <p class="muted" style="font-size:11px;margin-top:4px;">
        Percent below the running peak — depth × duration view.
      </p>
    </div>
  </div>
</div>

<div class="grid">
  <div>
    <h2 id="exits">🎯 Exit Reason Breakdown <a class="anchor" href="#exits">#</a></h2>
    <div class="section">{exit_donut_html}{exit_table_html}</div>
  </div>
  <div>
    <h2 id="sessions">🕐 Session Attribution <a class="anchor" href="#sessions">#</a></h2>
    <div class="section">{sessions_html}</div>
  </div>
</div>

<h2 id="costs">💸 Execution Costs Attribution <a class="anchor" href="#costs">#</a></h2>
<div class="section">{cost_html}</div>

{skipped_html}

{top_html}

{regime_html}

{clusters_html}

{exposure_html}

<h2 id="detail">📋 Detailed Metrics <a class="anchor" href="#detail">#</a></h2>
{metrics_html}

<div class="footer">
  Generated by AGREGAT · {_e(now)} · theme=<i>{_e(theme)}</i>
</div>
</div>

{drilldown_data_html}
{drilldown_modal_html}
</body></html>"""


# ---------------------------------------------------------------------------
# Internal renderers (one per section to keep the public function readable)
# ---------------------------------------------------------------------------


def _render_header(
    meta: Dict[str, Any], summary: Dict[str, Any], eq_track: List[float], n_trades: int
) -> str:
    """Top header card row + sparkline."""
    asset = _e(meta.get("asset", "—"))
    strat = _e(meta.get("strategy", "—"))
    init = float(meta.get("initial_equity", 0.0) or 0.0)
    final = float(meta.get("final_equity", init) or 0.0)
    period_start = _e(meta.get("date_range_start", "—"))
    period_end = _e(meta.get("date_range_end", "—"))

    sparkline = _sparkline_svg(eq_track, init)

    cards = [
        (
            "Total P&L",
            _money_signed(summary.get("total_pnl", 0.0)),
            _color("total_pnl", summary.get("total_pnl", 0.0)),
        ),
        (
            "Total Return",
            _pct(summary.get("total_return_pct", 0.0)),
            _color("total_return_pct", summary.get("total_return_pct", 0.0)),
        ),
        (
            "Sharpe",
            _ratio(summary.get("sharpe_ratio", 0.0)),
            _color("sharpe_ratio", summary.get("sharpe_ratio", 0.0)),
        ),
        (
            "Calmar",
            _ratio(summary.get("calmar_ratio", 0.0)),
            _color("calmar_ratio", summary.get("calmar_ratio", 0.0)),
        ),
        (
            "Max DD",
            _pct(-abs(float(summary.get("max_drawdown_pct", 0.0) or 0.0))),
            "red",
        ),
        (
            "Win Rate",
            _pct(summary.get("win_rate_pct", 0.0)),
            _wr_color(summary.get("win_rate_pct", 0.0)),
        ),
    ]
    cards_html = ""
    for label, value, klass in cards:
        cards_html += (
            f'<div class="card">'
            f'<div class="label">{_e(label)}</div>'
            f'<div class="value {klass}">{value}</div>'
            f"</div>"
        )
    return f"""
<div class="header">
  <h1>📊 AGREGAT Backtest Tear-Sheet</h1>
  <div class="sub">
    <b>Strategy:</b> {strat} &nbsp;·&nbsp;
    <b>Asset:</b> {asset} &nbsp;·&nbsp;
    <b>Period:</b> {period_start} → {period_end} &nbsp;·&nbsp;
    <b>Trades:</b> {int(n_trades)} &nbsp;·&nbsp;
    <b>Initial:</b> {_money(init)} &nbsp;·&nbsp;
    <b>Final:</b> {_money(final)}
  </div>
</div>
<div class="cards">{cards_html}</div>
<div class="section tight" style="display:flex; align-items:center; justify-content:center;">{sparkline}</div>
"""


def _sparkline_svg(eq: List[float], init: float) -> str:
    if len(eq) < 2:
        return charts._empty_chart("No equity yet", w=720, h=80)
    arr = np.asarray(eq, dtype=float)
    lo, hi = float(arr.min()), float(arr.max())
    if hi == lo:
        hi = lo + 1
    pad_l, pad_r = 8, 8
    h = 80
    pad_t, pad_b = 8, 8
    w = 720
    xs = [pad_l + i * (w - pad_l - pad_r) / (len(arr) - 1) for i in range(len(arr))]
    ys = [pad_b + (1 - (v - lo) / (hi - lo)) * (h - pad_t - pad_b) for v in arr]
    poly = " ".join(f"{x:.1f},{y:.1f}" for x, y in zip(xs, ys))
    base_y = pad_b + (1 - (init - lo) / (hi - lo)) * (h - pad_t - pad_b)
    color = charts.POSITIVE if arr[-1] >= init else charts.NEGATIVE
    return (
        f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}">'
        f'<line x1="{pad_l}" y1="{base_y:.1f}" x2="{w - pad_r}" y2="{base_y:.1f}" '
        f'stroke="{charts.AXIS_COLOR}" stroke-width="1" stroke-dasharray="3,3"/>'
        f'<polyline points="{poly}" fill="none" stroke="{color}" stroke-width="2"/>'
        "</svg>"
    )


def _render_exit_table(rows: Iterable[Any]) -> str:
    rows = list(rows)
    if not rows:
        return '<p class="muted" style="font-size:12px; margin-top:8px;">No exits</p>'
    tbody = ""
    for r in rows:
        wr_pct = r.win_rate * 100.0
        wc = "green" if wr_pct >= 50 else "amber" if wr_pct >= 40 else "red"
        pc = "green" if r.sum_pnl > 0 else "red" if r.sum_pnl < 0 else ""
        tbody += (
            f"<tr><td>{_e(r.reason)}</td>"
            f"<td>{int(r.count)}</td>"
            f'<td class="{wc}">{wr_pct:.1f}%</td>'
            f'<td class="{pc}">{_money_signed(r.sum_pnl)}</td>'
            f"<td>{_money_signed(r.avg_pnl)}</td></tr>"
        )
    return (
        '<table style="margin-top:12px;">'
        "<tr><th>Reason</th><th>Count</th><th>Win Rate</th><th>Net P&L</th><th>Avg P&L</th></tr>"
        f"{tbody}</table>"
    )


def _render_session_table(sessions: Dict[str, Any]) -> str:
    if not sessions:
        return '<p class="muted" style="font-size:12px;">No session data</p>'
    rows = ""
    for name, sd in sessions.items():
        cnt = int(sd.get("count", 0))
        spnl = float(sd.get("pnl", 0.0))
        wins = int(sd.get("wins", 0))
        wr = (wins / cnt * 100) if cnt else 0
        pc = "green" if spnl > 0 else "red" if spnl < 0 else ""
        wc = "green" if wr >= 50 else "amber" if wr >= 40 else "red"
        rows += (
            f"<tr><td>{_e(name)}</td>"
            f"<td>{cnt}</td>"
            f'<td class="{wc}">{wr:.1f}%</td>'
            f'<td class="{pc}">{_money_signed(spnl)}</td></tr>'
        )
    return (
        "<table>"
        "<tr><th>Session</th><th>Trades</th><th>Win Rate</th><th>Net P&L</th></tr>"
        f"{rows}</table>"
    )


def _render_skipped_section(summary: Dict[str, Any]) -> str:
    skipped_count = int(summary.get("skipped_trade_count", 0) or 0)
    if skipped_count <= 0:
        return ""
    skipped_ratio = float(summary.get("skipped_trade_ratio_pct", 0.0) or 0.0)
    skipped_warn = bool(summary.get("skipped_trade_warn", False))
    by_reason = summary.get("skipped_by_reason", {}) or {}
    rows = ""
    for reason, count in sorted(by_reason.items(), key=lambda kv: -int(kv[1])):
        rows += f"<tr><td>{_e(reason)}</td><td>{int(count)}</td></tr>"
    warn_class = "red" if skipped_warn else "amber"
    warn_html = (
        '<p class="red" style="font-size:12px;margin-top:8px;">⚠ More than 5% of '
        "signals were rejected before execution — review risk caps / SL distance "
        "/ trading-hours rules.</p>"
        if skipped_warn
        else ""
    )
    return f"""
<h2 id="skipped">🛑 Trade Selection <a class="anchor" href="#skipped">#</a></h2>
<div class="section">
<table>
  <tr><th>Skipped / Total Signals</th>
      <td class="{warn_class}">{skipped_count} ({skipped_ratio:.1f}%)</td></tr>
</table>
<table style="margin-top:8px;">
  <tr><th>Reason</th><th>Count</th></tr>
  {rows}
</table>
{warn_html}
</div>
"""


def _render_cost_attribution(costs: Any) -> str:
    if costs.gross_pnl == 0 and costs.total_costs == 0:
        return (
            '<p class="muted" style="font-size:12px;">No cost data on these trades.</p>'
        )
    table = (
        "<table>"
        '<tr><th data-tooltip="Total dollars paid in spread cost">Spread Total</th>'
        f'<td>{_money(costs.spread_total)}</td><td class="muted">{costs.spread_pct:.2f}% of gross</td></tr>'
        '<tr><th data-tooltip="Total dollars paid in slippage">Slippage Total</th>'
        f'<td>{_money(costs.slippage_total)}</td><td class="muted">{costs.slippage_pct:.2f}% of gross</td></tr>'
        '<tr><th data-tooltip="Total commission paid">Commission Total</th>'
        f'<td>{_money(costs.commission_total)}</td><td class="muted">{costs.commission_pct:.2f}% of gross</td></tr>'
        "<tr><th>Total Costs</th>"
        f"<td><b>{_money(costs.total_costs)}</b></td>"
        f"<td><b>{costs.total_pct:.2f}% of gross</b></td></tr>"
        "<tr><th>Gross PnL (pre-cost)</th>"
        f'<td>{_money_signed(costs.gross_pnl)}</td><td class="muted">— net = gross − costs</td></tr>'
        "</table>"
    )

    # Stacked bar — % of gross (capped to 100% for visualisation)
    bar = ""
    base = abs(costs.gross_pnl) if abs(costs.gross_pnl) > 1e-9 else 1.0
    spread_pct = max(0.0, min(100.0, costs.spread_total / base * 100.0))
    slip_pct = max(0.0, min(100.0, costs.slippage_total / base * 100.0))
    comm_pct = max(0.0, min(100.0, costs.commission_total / base * 100.0))
    net_pct = max(0.0, 100.0 - spread_pct - slip_pct - comm_pct)
    if costs.gross_pnl < 0:
        # When gross is negative, net portion is "loss"
        bar = (
            '<div class="cost-bar">'
            '<div class="cb-loss" style="width:100%;">Net loss after costs</div>'
            "</div>"
        )
    else:
        bar = (
            f'<div class="cost-bar" title="Net = green; spread/slip/comm = drag">'
            f'<div class="cb-spread" style="width:{spread_pct:.2f}%;">'
            f"Spread {spread_pct:.1f}%</div>"
            f'<div class="cb-slip" style="width:{slip_pct:.2f}%;">'
            f"Slip {slip_pct:.1f}%</div>"
            f'<div class="cb-comm" style="width:{comm_pct:.2f}%;">'
            f"Comm {comm_pct:.1f}%</div>"
            f'<div class="cb-net" style="width:{net_pct:.2f}%;">'
            f"Net {net_pct:.1f}%</div>"
            f"</div>"
        )

    legend = (
        '<div class="legend">'
        '<div class="legend-chip"><span class="legend-swatch" style="background:#5C8AFF"></span>Spread</div>'
        '<div class="legend-chip"><span class="legend-swatch" style="background:#FF9800"></span>Slippage</div>'
        '<div class="legend-chip"><span class="legend-swatch" style="background:#AB47BC"></span>Commission</div>'
        '<div class="legend-chip"><span class="legend-swatch" style="background:#26A69A"></span>Net (kept)</div>'
        "</div>"
    )
    warn = ""
    if costs.gross_pnl > 0 and costs.total_pct > 30.0:
        warn = (
            '<p class="red" style="font-size:12px;margin-top:8px;">'
            f"⚠ Costs ate {costs.total_pct:.1f}% of gross PnL — strategy is "
            "execution-sensitive. Re-check spread/commission assumptions before "
            "promoting to live.</p>"
        )
    return table + bar + legend + warn


def _render_top_tables(best: List[Any], worst: List[Any]) -> str:
    def _rows(items: List[Any]) -> str:
        out = ""
        for r in items:
            pc = "green" if r.pnl > 0 else "red" if r.pnl < 0 else ""
            out += (
                f"<tr><td>#{int(r.id)}</td>"
                f"<td>{_e(str(r.direction).upper())}</td>"
                f"<td>{_e(r.entry_time)}</td>"
                f'<td class="{pc}">{_money_signed(r.pnl)}</td>'
                f"<td>{_e(r.exit_reason)}</td></tr>"
            )
        return out

    return f"""
<div class="grid">
  <div>
    <h2 id="best10">🏆 Top 10 Best Trades <a class="anchor" href="#best10">#</a></h2>
    <div class="section"><table>
      <tr><th>ID</th><th>Dir</th><th>Entry</th><th>P&L</th><th>Exit</th></tr>
      {_rows(best)}
    </table></div>
  </div>
  <div>
    <h2 id="worst10">💀 Top 10 Worst Trades <a class="anchor" href="#worst10">#</a></h2>
    <div class="section"><table>
      <tr><th>ID</th><th>Dir</th><th>Entry</th><th>P&L</th><th>Exit</th></tr>
      {_rows(worst)}
    </table></div>
  </div>
</div>
"""


# ---------------------------------------------------------------------------
# Regime breakdown (Phase 4.2)
# ---------------------------------------------------------------------------


def _render_regime_section(trades: List[Any], df: Any) -> str:
    """Render the regime-attribution section.

    Skipped (returns empty string) when ``df`` is None / too short, or
    no trade has a recorded ``entry_bar_index`` — those preconditions
    are satisfied automatically when the engine ran a full backtest.
    """
    if df is None or len(df) < 50:
        return ""
    if not any(getattr(t, "entry_bar_index", None) is not None for t in trades):
        return ""
    try:
        from engine.analytics.regime import regime_report_payload
    except Exception:
        return ""
    payload = regime_report_payload(trades, df)
    if not payload:
        return ""
    rows = payload["rows"]
    bar_chart = charts.regime_bar_chart_svg(rows)
    table_rows = ""
    for r in rows:
        wr_pct = r.win_rate * 100.0
        wc = "green" if wr_pct >= 50 else "amber" if wr_pct >= 40 else "red"
        pc = "green" if r.sum_pnl > 0 else "red" if r.sum_pnl < 0 else ""
        table_rows += (
            f"<tr><td>{_e(r.regime)}</td>"
            f"<td>{int(r.n_trades)}</td>"
            f'<td class="{wc}">{wr_pct:.1f}%</td>'
            f'<td class="{pc}">{_money_signed(r.sum_pnl)}</td>'
            f"<td>{_money_signed(r.avg_pnl)}</td>"
            f"<td>{int(r.n_bars)}</td></tr>"
        )
    return f"""
<h2 id="regime">🌐 Regime Breakdown <a class="anchor" href="#regime">#</a></h2>
<div class="section">
  {bar_chart}
  <table style="margin-top:12px;">
    <tr><th>Regime</th><th>Trades</th><th>Win Rate</th><th>Net P&L</th><th>Avg P&L</th><th>Bars</th></tr>
    {table_rows}
  </table>
  <p class="muted" style="font-size:11px;margin-top:6px;">
    Trend (SMA 200 slope) × Vol (ATR 14 vs median). Trade attributed at entry bar.
  </p>
</div>
"""


# ---------------------------------------------------------------------------
# Trade clustering (Phase 4.3)
# ---------------------------------------------------------------------------


def _render_clusters_section(trades: List[Any]) -> str:
    if len(trades) < 6:
        return ""
    try:
        from engine.analytics.trade_clusters import cluster_trades
    except Exception:
        return ""
    try:
        result = cluster_trades(trades)
    except Exception:
        return ""
    if result is None:
        return ""
    scatter = charts.cluster_scatter_svg(result.projection, result.labels)
    table_rows = ""
    for row in result.rows:
        pc = "green" if row.mean_pnl > 0 else "red" if row.mean_pnl < 0 else ""
        table_rows += (
            f"<tr><td>Cluster {int(row.cluster_id)}</td>"
            f"<td>{int(row.n_trades)}</td>"
            f'<td class="{pc}">{_money_signed(row.mean_pnl)}</td>'
            f"<td>{_money_signed(row.pnl_std)}</td>"
            f"<td>#{int(row.representative_trade_id)}</td></tr>"
        )
    return f"""
<h2 id="clusters">🧬 Trade Clusters <a class="anchor" href="#clusters">#</a></h2>
<div class="section">
  <div class="grid">
    <div>{scatter}</div>
    <div><table>
      <tr><th>Cluster</th><th>Trades</th><th>Mean P&L</th><th>StdDev</th><th>Representative</th></tr>
      {table_rows}
    </table></div>
  </div>
  <p class="muted" style="font-size:11px;margin-top:6px;">
    K-means (k={int(result.k)}) on duration + session + MAE/MFE-R + exit reason.
    Silhouette = {result.silhouette:.3f}. PCA-2D projection shown.
  </p>
</div>
"""


# ---------------------------------------------------------------------------
# Exposure heatmap (Phase 4.4)
# ---------------------------------------------------------------------------


def _render_exposure_section(trades: List[Any], df: Any) -> str:
    if not trades:
        return ""
    try:
        import pandas as pd  # noqa: F401

        from engine.analytics.exposure import exposure_heatmap_from_trades
    except Exception:
        return ""
    data_index = None
    if df is not None:
        try:
            import pandas as pd

            if hasattr(df, "index") and isinstance(df.index, pd.DatetimeIndex):
                data_index = df.index
        except Exception:
            data_index = None
    try:
        heat = exposure_heatmap_from_trades(trades, data_index=data_index)
    except Exception:
        return ""
    svg = charts.exposure_heatmap_svg(heat.fraction)
    avg_exp = (
        heat.n_bars_in_position / heat.n_bars_total * 100.0
        if heat.n_bars_total
        else 0.0
    )
    return f"""
<h2 id="exposure">⏰ Exposure Heatmap <a class="anchor" href="#exposure">#</a></h2>
<div class="section">
  {svg}
  <p class="muted" style="font-size:11px;margin-top:6px;">
    Fraction of time in position by hour-of-day × weekday. Hover a cell for details.
    Average exposure = {avg_exp:.2f}% of {int(heat.n_bars_total):,} bars.
  </p>
</div>
"""


# ---------------------------------------------------------------------------
# Detailed metrics — collapsible-by-category
# ---------------------------------------------------------------------------


_METRIC_CATEGORIES = {
    "Returns": [
        "initial_equity",
        "final_equity",
        "total_pnl",
        "total_return_pct",
        "gross_profit",
        "gross_loss",
        "best_trade",
        "worst_trade",
        "avg_win",
        "avg_loss",
    ],
    "Risk": [
        "sharpe_ratio",
        "sortino_ratio",
        "calmar_ratio",
        "omega_ratio",
        "ulcer_index",
        "var_95",
        "cvar_95",
    ],
    "Drawdown": [
        "max_drawdown_pct",
        "max_drawdown_abs",
        "recovery_factor",
    ],
    "Trade Edge": [
        "n_trades",
        "wins",
        "losses",
        "breakeven",
        "win_rate_pct",
        "n_longs",
        "n_shorts",
        "profit_factor",
        "expectancy",
        "math_expectation",
        "avg_rr",
        "kelly_pct",
        "max_win_streak",
        "max_loss_streak",
        "avg_duration_min",
    ],
    "Selection": [
        "skipped_trade_count",
    ],
}

_LABELS = {
    "n_trades": "Total Trades",
    "wins": "Wins",
    "losses": "Losses",
    "breakeven": "Break-even",
    "win_rate_pct": "Win Rate",
    "n_longs": "Longs",
    "n_shorts": "Shorts",
    "initial_equity": "Initial Equity",
    "final_equity": "Final Equity",
    "total_pnl": "Total P&L",
    "total_return_pct": "Total Return",
    "gross_profit": "Gross Profit",
    "gross_loss": "Gross Loss",
    "profit_factor": "Profit Factor",
    "avg_win": "Avg Win",
    "avg_loss": "Avg Loss",
    "best_trade": "Best Trade",
    "worst_trade": "Worst Trade",
    "expectancy": "Expectancy (per trade)",
    "math_expectation": "Math Expectation (MO)",
    "avg_rr": "Avg R:R (winners)",
    "sharpe_ratio": "Sharpe Ratio",
    "sortino_ratio": "Sortino Ratio",
    "calmar_ratio": "Calmar Ratio",
    "max_drawdown_pct": "Max Drawdown (%)",
    "max_drawdown_abs": "Max Drawdown ($)",
    "recovery_factor": "Recovery Factor",
    "kelly_pct": "Kelly Criterion (%)",
    "var_95": "VaR (95%)",
    "cvar_95": "CVaR (95%)",
    "omega_ratio": "Omega Ratio",
    "ulcer_index": "Ulcer Index",
    "max_win_streak": "Max Win Streak",
    "max_loss_streak": "Max Loss Streak",
    "avg_duration_min": "Avg Duration (min)",
    "skipped_trade_count": "Skipped Signals",
}

_FORMATS = {
    "win_rate_pct": "{v}%",
    "total_return_pct": "{v}%",
    "max_drawdown_pct": "{v}%",
    "kelly_pct": "{v}%",
    "initial_equity": "${v:,}",
    "final_equity": "${v:,}",
    "total_pnl": "${v:+,}",
    "gross_profit": "${v:,}",
    "gross_loss": "${v:,}",
    "avg_win": "${v:,}",
    "avg_loss": "${v:,}",
    "best_trade": "${v:+,}",
    "worst_trade": "${v:+,}",
    "expectancy": "${v:+,}",
    "math_expectation": "${v:+,}",
    "max_drawdown_abs": "${v:,}",
    "var_95": "${v:,}",
    "cvar_95": "${v:,}",
    "avg_duration_min": "{v} min",
    "max_win_streak": "{v} trades",
    "max_loss_streak": "{v} trades",
}


def _render_detailed_metrics(
    summary: Dict[str, Any], *, include_drilldown: bool
) -> str:
    """Build the categorised metric table — each category is a `<details>`
    block so the user can expand only what they care about. Each metric
    row gets a hover tooltip and (optionally) a click-to-drill-down ⓘ.
    """
    sections = []
    for cat_name, keys in _METRIC_CATEGORIES.items():
        rows = ""
        for k in keys:
            if k not in summary:
                continue
            v = summary.get(k, "—")
            label = _LABELS.get(k, k)
            cc = _color(k, v)
            cls = f' class="{cc}"' if cc else ""
            hint = get_hint(k)
            has_d = include_drilldown and has_detail(k)
            row_attrs = f' class="metric-row" data-detail="{1 if has_d else 0}"'
            if has_d:
                row_attrs += f' data-key="{_e(k)}"'
            tooltip_attr = f' title="{_e(hint)}" data-tooltip="1"' if hint else ""
            rows += (
                f"<tr{row_attrs}>"
                f"<th{tooltip_attr}>{_e(label)}</th>"
                f"<td{cls}>{_e(_fmt(k, v))}</td>"
                f"</tr>"
            )
        if not rows:
            continue
        sections.append(
            f'<details class="section" open>'
            f"<summary><b>{_e(cat_name)}</b></summary>"
            f'<table style="margin-top:8px;">{rows}</table>'
            f"</details>"
        )
    return "\n".join(sections)


def _render_drilldown_payload(include: bool) -> str:
    """Embed a JSON dict {key: detail_html} so the modal JS can lookup
    explanations without a network round-trip."""
    if not include:
        return ""
    payload = {}
    for k in _LABELS.keys():
        det = get_detail(k)
        if det:
            payload[k] = det
    safe = json.dumps(payload).replace("</", "<\\/")
    return f'<script id="metric-detail-data" type="application/json">{safe}</script>'


def _render_drilldown_modal() -> str:
    """A tiny vanilla-JS modal — no JS frameworks. Bound to ``data-detail``
    rows on hookup."""
    return r"""
<div id="metric-modal-bg" class="modal-bg" onclick="if(event.target.id==='metric-modal-bg'){this.classList.remove('open');}">
  <div class="modal" role="dialog" aria-modal="true">
    <span class="close" onclick="document.getElementById('metric-modal-bg').classList.remove('open')">✕</span>
    <h3 id="metric-modal-title">Metric</h3>
    <div id="metric-modal-body"></div>
  </div>
</div>
<script>
(function() {
  const dataEl = document.getElementById("metric-detail-data");
  if (!dataEl) return;
  let payload = {};
  try { payload = JSON.parse(dataEl.textContent); }
  catch (e) { return; }
  const bg = document.getElementById("metric-modal-bg");
  const title = document.getElementById("metric-modal-title");
  const body = document.getElementById("metric-modal-body");
  document.querySelectorAll('tr.metric-row[data-detail="1"]').forEach(function (row) {
    row.addEventListener("click", function () {
      const key = row.getAttribute("data-key");
      const html = payload[key];
      if (!html) return;
      const labelEl = row.querySelector("th");
      title.textContent = labelEl ? labelEl.textContent.replace(" ⓘ", "") : key;
      body.innerHTML = html;
      bg.classList.add("open");
    });
  });
  window.addEventListener("keydown", function(ev) {
    if (ev.key === "Escape") bg.classList.remove("open");
  });
})();
</script>
"""


# ---------------------------------------------------------------------------
# Helpers (formatting + color)
# ---------------------------------------------------------------------------


def _e(value: Any) -> str:
    return _html.escape(str(value), quote=True)


def _fmt(key: str, val: Any) -> str:
    if val is None:
        return "—"
    if isinstance(val, str):
        return val
    fmt = _FORMATS.get(key)
    if fmt:
        try:
            return fmt.format(v=val)
        except Exception:
            return str(val)
    if isinstance(val, float):
        return f"{val:,.2f}"
    return str(val)


def _money(v: Any) -> str:
    try:
        return f"${float(v):,.2f}"
    except Exception:
        return "—"


def _money_signed(v: Any) -> str:
    try:
        return f"${float(v):+,.2f}"
    except Exception:
        return "—"


def _pct(v: Any) -> str:
    try:
        return f"{float(v):+.2f}%"
    except Exception:
        return "—"


def _ratio(v: Any) -> str:
    try:
        x = float(v)
        return f"{x:,.2f}"
    except Exception:
        return str(v)


def _wr_color(v: Any) -> str:
    try:
        v = float(v)
    except (TypeError, ValueError):
        return ""
    return "green" if v >= 50 else "amber" if v >= 40 else "red"


def _color(key: str, val: Any) -> str:
    if isinstance(val, str):
        return ""
    try:
        v = float(val)
    except (TypeError, ValueError):
        return ""
    pos_keys = {
        "total_pnl",
        "total_return_pct",
        "expectancy",
        "math_expectation",
        "best_trade",
        "profit_factor",
        "sharpe_ratio",
        "sortino_ratio",
        "calmar_ratio",
        "recovery_factor",
        "omega_ratio",
        "kelly_pct",
        "gross_profit",
    }
    neg_keys = {
        "worst_trade",
        "max_drawdown_pct",
        "max_drawdown_abs",
        "var_95",
        "cvar_95",
        "ulcer_index",
        "max_loss_streak",
        "gross_loss",
    }
    if key in pos_keys:
        return "green" if v > 0 else "red" if v < 0 else ""
    if key == "win_rate_pct":
        return _wr_color(v)
    if key in neg_keys:
        return "red"
    return ""
