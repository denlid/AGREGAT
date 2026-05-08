"""Backtest report exporter — comprehensive HTML + CSV + JSON.

Folder layout::

    report_<asset>_<YYYY-MM-DD_HH-MM-SS>/
        meta.json, stats.json, trades.csv, equity.csv, analytics.html
"""

from __future__ import annotations

import csv
import html
import json
import logging
import os
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger("StrategyTester.engine.report_export")


def _e(value: Any) -> str:
    """HTML-escape ``value`` for safe inline interpolation in ``_render_html``.

    Stringifies via ``str(...)`` first so non-string callers (numbers, ints,
    Trade ids) work without each call site spelling out the conversion. Any
    string control characters that could break out of the surrounding HTML
    context (``<``, ``>``, ``&``, ``"``, ``'``) are escaped — covering the
    XSS vector where a strategy or asset name like ``<script>alert(1)</script>``
    would otherwise execute as JavaScript when the report is opened.
    """
    return html.escape(str(value), quote=True)


def _to_jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if is_dataclass(value) and not isinstance(value, type):
        return _to_jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(k): _to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(v) for v in value]
    try:
        if isinstance(value, np.generic):
            return value.item()
    except Exception:
        pass
    try:
        return value.isoformat()
    except Exception:
        return str(value)


def _build_summary(engine: Any) -> Dict[str, Any]:
    trades = list(getattr(engine, "trades", []))
    init_eq = float(getattr(engine, "initial_equity", 0.0))
    eq = float(getattr(engine, "equity", init_eq))
    total_pnl = eq - init_eq
    n = len(trades)
    wins = [t for t in trades if t.pnl > 0]
    losses = [t for t in trades if t.pnl < 0]
    be = [t for t in trades if t.pnl == 0]
    n_w, n_l, n_be = len(wins), len(losses), len(be)
    wr = (100.0 * n_w / n) if n else 0.0
    gp = sum(t.pnl for t in wins)
    gl = abs(sum(t.pnl for t in losses))
    pf = gp / gl if gl > 0 else (float("inf") if gp > 0 else 0.0)
    avg_w = gp / n_w if n_w else 0.0
    avg_l = gl / n_l if n_l else 0.0
    best = max((t.pnl for t in trades), default=0)
    worst = min((t.pnl for t in trades), default=0)
    expectancy = total_pnl / n if n else 0.0
    p_win = n_w / n if n else 0
    p_loss = n_l / n if n else 0
    mo = p_win * avg_w - p_loss * avg_l

    # Drawdown
    pnl_arr = np.array([t.pnl for t in trades])
    eq_arr = init_eq + np.cumsum(pnl_arr) if n else np.array([init_eq])
    peak = np.maximum.accumulate(eq_arr) if n else eq_arr
    dd_abs = float((peak - eq_arr).max()) if n else 0.0
    dd_pct = float(np.where(peak > 0, (peak - eq_arr) / peak * 100, 0).max()) if n else 0.0

    # Sharpe/Sortino via equity curve
    try:
        from engine.analytics.equity_curve import (
            build_equity_curve,
            calmar_ratio,
            sharpe_ratio,
            sortino_ratio,
        )

        eq_series = build_equity_curve(trades, init_eq)
        sharpe = sharpe_ratio(eq_series)
        sortino = sortino_ratio(eq_series)
        calmar = calmar_ratio(eq_series, init_eq)
    except Exception:
        sharpe = sortino = calmar = 0.0

    # Recovery factor
    recovery = total_pnl / dd_abs if dd_abs > 0 else (float("inf") if total_pnl > 0 else 0.0)

    # Streaks
    max_ws = max_ls = cur_ws = cur_ls = 0
    for t in trades:
        if t.pnl > 0:
            cur_ws += 1
            cur_ls = 0
            max_ws = max(max_ws, cur_ws)
        elif t.pnl < 0:
            cur_ls += 1
            cur_ws = 0
            max_ls = max(max_ls, cur_ls)
        else:
            cur_ws = cur_ls = 0

    # Duration
    durations = []
    for t in trades:
        try:
            d = (pd.to_datetime(t.exit_time) - pd.to_datetime(t.entry_time)).total_seconds() / 60
            durations.append(d)
        except Exception:
            pass
    avg_dur = sum(durations) / len(durations) if durations else 0

    # RR — `rr` is optional on the Trade-like protocol (older callers, mocks,
    # and partial dataclasses may omit it); fall back to None when absent.
    rr_vals: list[float] = []
    for t in trades:
        rr = getattr(t, "rr", None)
        if rr is not None and rr > 0:
            rr_vals.append(float(rr))
    avg_rr = sum(rr_vals) / len(rr_vals) if rr_vals else 0

    # Longs/Shorts
    n_longs = sum(1 for t in trades if t.direction == "long")
    n_shorts = sum(1 for t in trades if t.direction == "short")

    # Session breakdown
    session_data = {}
    for t in trades:
        s = getattr(t, "session", "Unknown")
        if s not in session_data:
            session_data[s] = {"count": 0, "pnl": 0.0, "wins": 0}
        session_data[s]["count"] += 1
        session_data[s]["pnl"] += t.pnl
        if t.pnl > 0:
            session_data[s]["wins"] += 1

    # Kelly
    kelly = 0.0
    if avg_l > 0 and avg_w > 0:
        kelly = (wr / 100) - ((1 - wr / 100) / (avg_w / avg_l))

    # VaR / CVaR / Omega / Ulcer — pure engine metrics, no GUI dependency.
    pnls = np.array([t.pnl for t in trades], dtype="float64") if trades else np.array([])
    if pnls.size:
        from engine.analytics.equity_curve import build_equity_curve
        from engine.analytics.risk_metrics import (
            conditional_var,
            omega_ratio,
            ulcer_index,
            value_at_risk,
        )

        var_95 = value_at_risk(pnls, 0.95)
        cvar_95 = conditional_var(pnls, 0.95)
        omega = omega_ratio(pnls)
        eq_arr = build_equity_curve(trades, init_eq).to_numpy(dtype="float64")
        ulcer = ulcer_index(eq_arr)
    else:
        var_95 = cvar_95 = omega = ulcer = 0.0

    return {
        "n_trades": n,
        "wins": n_w,
        "losses": n_l,
        "breakeven": n_be,
        "win_rate_pct": round(wr, 2),
        "n_longs": n_longs,
        "n_shorts": n_shorts,
        "initial_equity": init_eq,
        "final_equity": eq,
        "total_pnl": round(total_pnl, 2),
        "total_return_pct": round((total_pnl / init_eq * 100) if init_eq else 0, 2),
        "gross_profit": round(gp, 2),
        "gross_loss": round(gl, 2),
        "profit_factor": round(pf, 2) if pf != float("inf") else "∞",
        "avg_win": round(avg_w, 2),
        "avg_loss": round(avg_l, 2),
        "best_trade": round(best, 2),
        "worst_trade": round(worst, 2),
        "expectancy": round(expectancy, 2),
        "math_expectation": round(mo, 2),
        "avg_rr": round(avg_rr, 2),
        "sharpe_ratio": round(sharpe, 2),
        "sortino_ratio": round(sortino, 2),
        "calmar_ratio": round(calmar, 2),
        "max_drawdown_pct": round(dd_pct, 2),
        "max_drawdown_abs": round(dd_abs, 2),
        "recovery_factor": round(recovery, 2) if abs(recovery) < 1e9 else "∞",
        "kelly_pct": round(kelly * 100, 2),
        "var_95": round(var_95, 2),
        "cvar_95": round(cvar_95, 2),
        "omega_ratio": round(omega, 2) if omega != float("inf") else "∞",
        "ulcer_index": round(ulcer, 2),
        "max_win_streak": max_ws,
        "max_loss_streak": max_ls,
        "avg_duration_min": round(avg_dur, 1),
        "sessions": session_data,
    }


def _trade_to_row(trade: Any) -> Dict[str, Any]:
    if is_dataclass(trade) and not isinstance(trade, type):
        return {k: _to_jsonable(v) for k, v in asdict(trade).items()}
    return {
        k: _to_jsonable(getattr(trade, k))
        for k in dir(trade)
        if not k.startswith("_") and not callable(getattr(trade, k, None))
    }


# ─── HTML labels for pretty display ───
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


def _color_class(key: str, val: Any) -> str:
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
    }
    neg_keys = {
        "worst_trade",
        "max_drawdown_pct",
        "max_drawdown_abs",
        "var_95",
        "cvar_95",
        "ulcer_index",
        "max_loss_streak",
    }
    if key in pos_keys:
        return "green" if v > 0 else "red" if v < 0 else ""
    if key == "win_rate_pct":
        return "green" if v >= 50 else "amber" if v >= 40 else "red"
    if key in neg_keys:
        return "red"
    return ""


def _render_html(meta: Dict[str, Any], summary: Dict[str, Any], trades: List[Any]) -> str:
    # Overview section
    overview_keys = [
        "n_trades",
        "wins",
        "losses",
        "breakeven",
        "win_rate_pct",
        "n_longs",
        "n_shorts",
    ]
    pnl_keys = [
        "initial_equity",
        "final_equity",
        "total_pnl",
        "total_return_pct",
        "gross_profit",
        "gross_loss",
        "profit_factor",
        "avg_win",
        "avg_loss",
        "best_trade",
        "worst_trade",
    ]
    risk_keys = [
        "sharpe_ratio",
        "sortino_ratio",
        "calmar_ratio",
        "max_drawdown_pct",
        "max_drawdown_abs",
        "recovery_factor",
        "var_95",
        "cvar_95",
        "omega_ratio",
        "ulcer_index",
    ]
    edge_keys = [
        "expectancy",
        "math_expectation",
        "avg_rr",
        "kelly_pct",
        "max_win_streak",
        "max_loss_streak",
        "avg_duration_min",
    ]

    def _section_rows(keys: Iterable[str]) -> str:
        rows = ""
        for k in keys:
            v = summary.get(k, "—")
            label = _LABELS.get(k, k)
            cc = _color_class(k, v)
            # CSS class names are constants from `_color_class` — safe to embed,
            # but the formatted value (`_fmt`) and the label both come from
            # caller-supplied `summary` keys/labels and must be escaped.
            cls = f' class="{_e(cc)}"' if cc else ""
            # `data-key` makes the row machine-readable (unit tests + downstream
            # tooling) without affecting the rendered label.
            rows += (
                f'<tr data-key="{_e(k)}"><th>{_e(label)}</th><td{cls}>{_e(_fmt(k, v))}</td></tr>\n'
            )
        return rows

    # Session table
    sess = summary.get("sessions", {})
    sess_rows = ""
    for sn, sd in sess.items():
        cnt = sd["count"]
        spnl = sd["pnl"]
        swr = (sd["wins"] / cnt * 100) if cnt else 0
        pc = "green" if spnl > 0 else "red" if spnl < 0 else ""
        wc = "green" if swr >= 50 else "amber" if swr >= 40 else "red"
        sess_rows += (
            f"<tr><td>{_e(sn)}</td><td>{int(cnt)}</td>"
            f'<td class="{wc}">{swr:.1f}%</td>'
            f'<td class="{pc}">${spnl:+,.2f}</td></tr>\n'
        )

    # Top 5 best / worst trades
    sorted_trades = sorted(trades, key=lambda t: t.pnl, reverse=True)
    best5 = sorted_trades[:5]
    worst5 = sorted_trades[-5:][::-1]

    def _trade_rows(tlist: Iterable[Any]) -> str:
        r = ""
        for t in tlist:
            pc = "green" if t.pnl > 0 else "red"
            entry_time = getattr(t, "entry_time", "")
            try:
                et = pd.to_datetime(entry_time).strftime("%Y-%m-%d %H:%M")
            except Exception:
                et = str(entry_time)
            exit_reason = getattr(t, "exit_reason", "")
            r += (
                f"<tr><td>#{int(t.id)}</td><td>{_e(t.direction.upper())}</td>"
                f'<td>{_e(et)}</td><td class="{pc}">${t.pnl:+,.2f}</td>'
                f"<td>{_e(exit_reason)}</td></tr>\n"
            )
        return r

    # Equity sparkline as inline SVG
    eq_svg = ""
    if trades:
        eq_vals = [float(summary.get("initial_equity", 0))]
        for t in trades:
            eq_vals.append(eq_vals[-1] + t.pnl)
        mn, mx = min(eq_vals), max(eq_vals)
        rng = mx - mn if mx != mn else 1
        w, h = 680, 120
        pts = []
        for i, v in enumerate(eq_vals):
            x = i / max(1, len(eq_vals) - 1) * w
            y = h - ((v - mn) / rng * (h - 10) + 5)
            pts.append(f"{x:.1f},{y:.1f}")
        polyline = " ".join(pts)
        final_color = "#26A69A" if eq_vals[-1] >= eq_vals[0] else "#EF5350"
        # Fill area
        fill_pts = f"0,{h} " + polyline + f" {w},{h}"
        eq_svg = f'''<svg width="{w}" height="{h}" style="margin:8px 0;">
          <defs><linearGradient id="eqg" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stop-color="{final_color}" stop-opacity="0.3"/>
            <stop offset="100%" stop-color="{final_color}" stop-opacity="0.02"/>
          </linearGradient></defs>
          <polygon points="{fill_pts}" fill="url(#eqg)"/>
          <polyline points="{polyline}" fill="none" stroke="{final_color}" stroke-width="2"/>
        </svg>'''

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    asset = meta.get("asset", "—")
    strat = meta.get("strategy", "—")
    n = len(trades)

    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>AGREGAT Report — {_e(asset)}</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ background: #0B0B0D; color: #E8E8EE; font-family: 'Segoe UI', system-ui, sans-serif; padding: 32px 40px; line-height: 1.5; }}
  .header {{ border-bottom: 1px solid #1E1E28; padding-bottom: 16px; margin-bottom: 24px; }}
  .header h1 {{ color: #5C8AFF; font-size: 22px; font-weight: 600; letter-spacing: 0.5px; }}
  .header .sub {{ color: #5A5A6E; font-size: 12px; margin-top: 4px; }}
  .header .sub b {{ color: #8A8A96; }}
  .cards {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-bottom: 24px; }}
  .card {{ background: #131316; border: 1px solid #1E1E28; border-radius: 8px; padding: 14px 16px; }}
  .card .label {{ color: #55555F; font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.8px; }}
  .card .value {{ font-size: 20px; font-weight: 700; font-family: 'Consolas', monospace; margin-top: 4px; }}
  h2 {{ color: #5C8AFF; font-size: 13px; text-transform: uppercase; letter-spacing: 1.2px; margin: 28px 0 10px 0; font-weight: 600; }}
  .grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }}
  table {{ border-collapse: collapse; width: 100%; }}
  th, td {{ padding: 7px 12px; text-align: left; border-bottom: 1px solid #1A1A22; font-size: 13px; }}
  th {{ color: #5A5A6E; font-weight: 500; width: 55%; }}
  td {{ font-family: 'Consolas', monospace; }}
  .green {{ color: #26A69A; }}
  .red {{ color: #EF5350; }}
  .amber {{ color: #FF9800; }}
  .section {{ background: #0F0F12; border: 1px solid #1E1E28; border-radius: 8px; padding: 16px; margin-bottom: 16px; }}
  .footer {{ color: #3A3A42; font-size: 11px; margin-top: 32px; text-align: center; border-top: 1px solid #1E1E28; padding-top: 16px; }}
</style></head><body>

<div class="header">
  <h1>📊 AGREGAT Backtest Report</h1>
  <div class="sub"><b>Asset:</b> {_e(asset)} &nbsp;·&nbsp; <b>Strategy:</b> {_e(strat)} &nbsp;·&nbsp; <b>Trades:</b> {int(n)} &nbsp;·&nbsp; {_e(now)}</div>
</div>

<div class="cards">
  <div class="card"><div class="label">Total P&L</div><div class="value {"green" if summary.get("total_pnl", 0) >= 0 else "red"}">${summary.get("total_pnl", 0):+,.2f}</div></div>
  <div class="card"><div class="label">Win Rate</div><div class="value {"green" if summary.get("win_rate_pct", 0) >= 50 else "amber" if summary.get("win_rate_pct", 0) >= 40 else "red"}">{summary.get("win_rate_pct", 0):.1f}%</div></div>
  <div class="card"><div class="label">Profit Factor</div><div class="value">{_fmt("profit_factor", summary.get("profit_factor", 0))}</div></div>
  <div class="card"><div class="label">Max Drawdown</div><div class="value red">{summary.get("max_drawdown_pct", 0):.2f}%</div></div>
</div>

<h2>📈 Equity Curve</h2>
<div class="section">{eq_svg}</div>

<div class="grid">
<div>
  <h2>📋 Trade Overview</h2>
  <div class="section"><table>{_section_rows(overview_keys)}</table></div>
  <h2>💰 Profit &amp; Loss</h2>
  <div class="section"><table>{_section_rows(pnl_keys)}</table></div>
</div>
<div>
  <h2>⚡ Risk Metrics</h2>
  <div class="section"><table>{_section_rows(risk_keys)}</table></div>
  <h2>🎯 Edge &amp; Analysis</h2>
  <div class="section"><table>{_section_rows(edge_keys)}</table></div>
</div>
</div>

<h2>🕐 Session Breakdown</h2>
<div class="section"><table>
<tr><th>Session</th><th>Trades</th><th>Win Rate</th><th>Net P&L</th></tr>
{sess_rows if sess_rows else '<tr><td colspan="4" style="color:#3A3A42">No session data</td></tr>'}
</table></div>

<div class="grid">
<div>
<h2>🏆 Top 5 Best Trades</h2>
<div class="section"><table>
<tr><th>ID</th><th>Dir</th><th>Entry</th><th>P&L</th><th>Exit</th></tr>
{_trade_rows(best5)}
</table></div>
</div>
<div>
<h2>💀 Top 5 Worst Trades</h2>
<div class="section"><table>
<tr><th>ID</th><th>Dir</th><th>Entry</th><th>P&L</th><th>Exit</th></tr>
{_trade_rows(worst5)}
</table></div>
</div>
</div>

<h2>📎 Metadata</h2>
<div class="section"><table>
<tr><th>Asset</th><td>{_e(asset)}</td></tr>
<tr><th>Strategy</th><td>{_e(strat)}</td></tr>
<tr><th>Date Range</th><td>{_e(meta.get("date_range_start", "—"))} → {_e(meta.get("date_range_end", "—"))}</td></tr>
<tr><th>Initial Equity</th><td>${meta.get("initial_equity", 0):,.2f}</td></tr>
<tr><th>Final Equity</th><td>${meta.get("final_equity", 0):,.2f}</td></tr>
<tr><th>Exported</th><td>{_e(now)}</td></tr>
</table></div>

<div class="footer">Generated by AGREGAT v7.78 · {_e(now)}</div>
</body></html>"""


def export_report(
    engine: Any,
    out_dir: str,
    *,
    strategy_name: Optional[str] = None,
    asset_name: Optional[str] = None,
) -> str:
    trades = list(getattr(engine, "trades", []))
    if not trades:
        raise RuntimeError("No trades to export — run the backtest first.")

    asset = asset_name or getattr(getattr(engine, "asset_config", None), "name", "ASSET")
    strat = strategy_name or type(getattr(engine, "strategy", None) or object()).__name__
    ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    folder = os.path.join(out_dir, f"report_{asset}_{ts}")
    os.makedirs(folder, exist_ok=True)

    # 1. trades.csv
    rows = [_trade_to_row(t) for t in trades]
    fieldnames = sorted({k for row in rows for k in row.keys()})
    with open(os.path.join(folder, "trades.csv"), "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    # 2. equity.csv
    track = list(getattr(engine, "equity_track", []) or [])
    with open(os.path.join(folder, "equity.csv"), "w", newline="", encoding="utf-8") as fh:
        eq_writer = csv.writer(fh)
        eq_writer.writerow(["trade_idx", "equity"])
        for i, eq in enumerate(track):
            eq_writer.writerow([i, eq])

    # 3. stats.json
    summary = _build_summary(engine)
    with open(os.path.join(folder, "stats.json"), "w", encoding="utf-8") as fh:
        json.dump(_to_jsonable(summary), fh, indent=2)

    # 4. meta.json
    dr = getattr(engine, "_date_range", (None, None)) or (None, None)
    meta = {
        "asset": asset,
        "strategy": strat,
        "n_trades": len(trades),
        "initial_equity": float(getattr(engine, "initial_equity", 0.0)),
        "final_equity": float(getattr(engine, "equity", 0.0)),
        "date_range_start": str(dr[0]) if dr[0] is not None else None,
        "date_range_end": str(dr[1]) if dr[1] is not None else None,
        "exported_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    with open(os.path.join(folder, "meta.json"), "w", encoding="utf-8") as fh:
        json.dump(_to_jsonable(meta), fh, indent=2)

    # 5. analytics.html
    with open(os.path.join(folder, "analytics.html"), "w", encoding="utf-8") as fh:
        fh.write(_render_html(meta, summary, trades))

    logger.info("Report exported: %s", folder)
    return folder
