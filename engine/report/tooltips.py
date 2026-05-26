"""Drill-down tooltips for tear-sheet metrics (Phase 3.4).

Two layers of explanation are exposed for every metric the report renders:

1. A short ``hint`` (one sentence, ≤ 80 chars) injected as the ``<th>``
   tag's ``title`` attribute — surfaced as a native browser tooltip when
   the user hovers a metric label. No JavaScript needed.
2. A longer ``detail`` string (formula + interpretation rule of thumb)
   used by the JS modal in ``tearsheet.py`` for click-through drill-down.

Both come from the same dictionary so adding a new metric in one place
keeps both layers in sync. Unknown keys silently get an empty string.
"""

from __future__ import annotations

from typing import Dict, Tuple

# Each entry: key -> (hint, detail_html)
# detail_html is rendered raw inside the modal — keep it pre-escaped.
_TOOLTIPS: Dict[str, Tuple[str, str]] = {
    "n_trades": (
        "Total number of executed trades (skipped signals not counted).",
        "<p><b>Definition:</b> Total executed trades that produced a fill and "
        "an exit. Cancelled / skipped signals are tracked separately in the "
        "skipped-signals panel.</p>"
        "<p><b>Rule of thumb:</b> below ~30 trades the headline ratios are "
        "noisy; below ~100 they're directional only. Look at p5/p95 from "
        "Monte Carlo before drawing conclusions.</p>",
    ),
    "win_rate_pct": (
        "Wins / total executed trades (% of trades closed in profit).",
        "<p><b>Formula:</b> wins / total × 100</p>"
        "<p><b>Caveat:</b> a high win-rate with poor R:R is fragile — "
        "compare against expectancy and profit factor before celebrating.</p>",
    ),
    "profit_factor": (
        "Gross profit ÷ |gross loss|. ≥1.5 is solid, ≥2.0 is strong.",
        "<p><b>Formula:</b> Σ winners / |Σ losers|</p>"
        "<p>1.0 = breakeven, 1.5+ usable, 2.0+ excellent. PF&nbsp;==&nbsp;∞ means "
        "no losing trades — usually a sample-size warning, not a feature.</p>",
    ),
    "sharpe_ratio": (
        "Mean / std of bar-level returns × √(periods/yr). 2.0+ is excellent.",
        "<p><b>Formula:</b> (mean(returns) - rf) / std(returns) × √(periods)</p>"
        "<p><b>Reference:</b> &lt;0 negative edge · 0–1 weak · 1–2 acceptable · "
        "2–3 strong · &gt;3 excellent (and suspect — check curve fitting).</p>"
        "<p><b>Phase 1.1:</b> bar-level Sharpe captures intra-trade volatility "
        "the trade-level proxy hides — usually 30–40% lower than the legacy "
        "value. The lower number is the honest one.</p>",
    ),
    "sortino_ratio": (
        "Like Sharpe but penalising only downside volatility.",
        "<p><b>Formula:</b> (mean(returns) - rf) / downside_std × √(periods)</p>"
        "<p>Sortino &gt; Sharpe is normal — strategies with positive skew look "
        "better here. Watch when the gap explodes: that often signals a "
        "single fat-tail outlier doing the heavy lifting.</p>",
    ),
    "calmar_ratio": (
        "CAGR divided by max drawdown — annualised return per unit of pain.",
        "<p><b>Formula:</b> CAGR / |max drawdown|</p>"
        "<p>1.0 = average. 3.0+ is institutional-grade. Strongly affected by "
        "the longest drawdown in the sample, so prefer this over Sharpe when "
        "the user cares about path stability.</p>",
    ),
    "max_drawdown_pct": (
        "Deepest peak-to-trough equity decline (worst losing streak).",
        "<p><b>Formula:</b> max((peak − equity) / peak) × 100</p>"
        "<p>Any DD &gt; ~20% is psychologically uncomfortable for live trading "
        "even if maths says edge is intact. Cross-check against the Monte "
        "Carlo p95 — bootstrap often shows DDs the historical sample under-"
        "represents.</p>",
    ),
    "max_drawdown_abs": (
        "Same as Max Drawdown (%) but in dollars.",
        "<p>Useful for sizing — answer to <i>'how much capital must I budget "
        "for the worst path I've seen?'</i>.</p>",
    ),
    "recovery_factor": (
        "Total PnL ÷ |Max Drawdown|. Higher = faster recovery from the worst dip.",
        "<p><b>Formula:</b> total_pnl / |max_drawdown|</p>"
        "<p>2.0+ means the strategy made back its worst drawdown twice over "
        "during the test. &lt; 1.0 means even the headline profit didn't cover "
        "the worst losing streak — fragile.</p>",
    ),
    "var_95": (
        "Per-trade Value-at-Risk: 5% of trades lose at least this much.",
        "<p><b>Formula:</b> 5th percentile of trade-PnL distribution.</p>"
        "<p>Used together with CVaR to size risk caps. VaR is the threshold; "
        "CVaR is the average of trades worse than that threshold.</p>",
    ),
    "cvar_95": (
        "Mean PnL of the worst 5% of trades (expected shortfall).",
        "<p><b>Formula:</b> E[ pnl | pnl ≤ VaR(95%) ]</p>"
        "<p>Always more negative than VaR — use this number when sizing "
        "tail-risk reserves, not the headline max loss.</p>",
    ),
    "omega_ratio": (
        "Sum of gains over a threshold ÷ sum of losses below it (default 0).",
        "<p>1.0 = even. 1.3+ usable. Captures fat-tail asymmetry better than "
        "Sharpe because it doesn't assume normality of returns.</p>",
    ),
    "ulcer_index": (
        "Quadratic mean of % drawdowns — penalises long, deep underwater periods.",
        "<p><b>Formula:</b> √(mean((drawdown_pct)²))</p>"
        "<p>Lower is better. Pairs well with Calmar — a low Ulcer + high "
        "Calmar means equity grew with shallow & short DDs.</p>",
    ),
    "expectancy": (
        "Average PnL per trade in dollars. Negative = losing strategy.",
        "<p><b>Formula:</b> total_pnl / n_trades</p>"
        "<p>Multiply by your projected trade frequency to estimate annual "
        "income. Robust because it's just an arithmetic mean — no "
        "ratio-of-ratios games.</p>",
    ),
    "math_expectation": (
        "Probabilistic expectancy: P(win)·avg_win − P(loss)·avg_loss.",
        "<p><b>Formula:</b> p_win·avg_win − p_loss·avg_loss</p>"
        "<p>Should match Expectancy at high trade counts; small differences "
        "at low N reveal whether the edge is in <i>frequency</i> or "
        "<i>magnitude</i>.</p>",
    ),
    "avg_rr": (
        "Mean reward-to-risk ratio across winning trades.",
        "<p><b>Formula:</b> mean(reward / risk) for winners only.</p>"
        "<p>2.0+ R:R lets win-rate fall below 50% and still print money.</p>",
    ),
    "kelly_pct": (
        "Optimal % of equity per trade — fraction-of-Kelly is safer.",
        "<p><b>Formula:</b> p_win − (1−p_win) / (avg_win/avg_loss)</p>"
        "<p>Negative = no edge. In live trading <i>quarter-Kelly</i> is the "
        "industry standard because real distributions are non-stationary.</p>",
    ),
    "skipped_trade_count": (
        "Signals rejected before fill (lot caps, invalid SL, out-of-hours).",
        "<p>If the ratio &gt; 5% the strategy's nominal stats are biased — "
        "the rejections are surfaced in the dedicated skipped-signals "
        "panel above with a per-reason breakdown.</p>",
    ),
    "max_win_streak": (
        "Longest consecutive run of winning trades.",
        "<p>Sanity-check: if max win-streak ≫ expected from win-rate, the "
        "data is autocorrelated (consider block bootstrap).</p>",
    ),
    "max_loss_streak": (
        "Longest consecutive run of losing trades.",
        "<p>Use this for trader risk — psychologically can the user keep "
        "trading after this run? If not, reduce position size.</p>",
    ),
    "avg_duration_min": (
        "Average bars-in-position per trade, in minutes.",
        "<p>Shorter durations = more trades / costs / sensitivity to "
        "execution. Compare against your bar size to decide if the strategy "
        "is intra-bar or multi-bar by design.</p>",
    ),
    "total_pnl": (
        "Sum of all trade PnLs after costs (commission + spread + slippage).",
        "<p>Use the cost attribution panel to see what % of gross PnL was "
        "consumed by each cost component.</p>",
    ),
    "total_return_pct": (
        "Total PnL as % of initial equity.",
        "<p>For multi-year tests, compare against CAGR — % return alone hides "
        "the fact that 50% over 10 years is mediocre.</p>",
    ),
    "gross_profit": (
        "Sum of PnL across winning trades (before subtracting losers).",
        "<p>Pair with Gross Loss to compute Profit Factor.</p>",
    ),
    "gross_loss": (
        "Sum of |PnL| across losing trades.",
        "",
    ),
    "best_trade": (
        "Largest winning trade in dollars.",
        "<p>If 'best trade' is &gt; 30% of total PnL, the equity curve is "
        "hostage to a single outlier — re-test on a sub-period that excludes "
        "it.</p>",
    ),
    "worst_trade": (
        "Largest losing trade in dollars.",
        "<p>Should be ~ Stop Loss × position size. If it's much larger, "
        "execution model has gaps / GAP_SL slippage to investigate.</p>",
    ),
    "spread_total": (
        "Total dollars paid in spread costs across the run.",
        "",
    ),
    "slippage_total": (
        "Total dollars paid in slippage costs across the run.",
        "",
    ),
    "commission_total": (
        "Total commission paid across all trades.",
        "",
    ),
    "total_costs": (
        "Sum of spread + slippage + commission.",
        "<p>If costs &gt; 30% of gross PnL the edge is largely a market-"
        "structure artefact — reduce frequency or get tighter execution.</p>",
    ),
}


def get_hint(key: str) -> str:
    """Return the short tooltip text or '' if no entry exists."""
    return _TOOLTIPS.get(key, ("", ""))[0]


def get_detail(key: str) -> str:
    """Return the long detail HTML or '' if none."""
    return _TOOLTIPS.get(key, ("", ""))[1]


def has_detail(key: str) -> bool:
    """True iff an explanatory body is registered for ``key``."""
    return bool(_TOOLTIPS.get(key, ("", ""))[1])
