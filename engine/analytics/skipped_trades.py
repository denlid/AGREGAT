"""Phase 1.2 — Skipped trade analytics.

A trade can be "skipped" before ever reaching the order book — invalid
SL distance, lot calculation hitting the broker cap in strict mode,
trading-hours window, etc. The engine appends each skip to
:pyattr:`engine.core.backtest_core.BacktestCore.skipped_trades` as a
plain dict with at least a ``reason`` key.

Aggregating these into ``count``, ``ratio_pct``, ``by_reason`` lets the
Stats tab and the HTML report surface a *Trade Selection* warning when
a high fraction of signals never become trades — usually a sign of
risk parameters that disagree with the strategy.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Iterable, Mapping

# Threshold above which the report's HTML renderer flips the section
# into a red warning. 5 % is loose — a strategy that drops 1 in 20
# signals because of risk caps is usually mis-calibrated.
SKIPPED_RATIO_WARN_PCT = 5.0


def aggregate_skipped(
    skipped: Iterable[Mapping[str, Any]],
    n_executed_trades: int,
) -> dict[str, Any]:
    """Build the aggregate dict consumed by ``_build_summary`` / Stats UI.

    Parameters
    ----------
    skipped
        Iterable of per-skip dicts; each is expected to expose a
        ``reason`` key. Any missing reason is bucketed under
        ``"UNKNOWN"`` so the dashboard never silently drops a category.
    n_executed_trades
        Number of trades that actually executed — used as the
        denominator for the *fraction of signals that turned into
        trades*. The denominator is ``executed + skipped`` so a 100 %
        skip rate yields 100 % (not infinity).

    Returns
    -------
    dict
        ``count``: total number of skipped trades.
        ``ratio_pct``: 100 * skipped / (executed + skipped).
        ``by_reason``: ``Counter``-style dict of bucket counts.
        ``warn``: True when ``ratio_pct`` exceeds
        :data:`SKIPPED_RATIO_WARN_PCT`.
    """
    skipped_list = list(skipped)
    counts = Counter(str(item.get("reason", "UNKNOWN")) for item in skipped_list)
    total = len(skipped_list)
    denom = max(int(n_executed_trades) + total, 1)
    ratio_pct = (100.0 * total / denom) if total else 0.0
    return {
        "count": total,
        "ratio_pct": round(ratio_pct, 2),
        "by_reason": dict(counts),
        "warn": ratio_pct > SKIPPED_RATIO_WARN_PCT,
    }


__all__ = [
    "aggregate_skipped",
    "SKIPPED_RATIO_WARN_PCT",
]
