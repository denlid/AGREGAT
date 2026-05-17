"""Sprint 2.1 — XSS smoke test for ``engine.report_export._render_html``.

The report HTML is rendered from a meta dict whose ``asset`` and
``strategy`` keys come straight from the user (asset config name,
strategy class name) — and from per-trade fields like ``exit_reason``
and ``session`` that originate in user-authored strategy code. Any of
those strings can in principle contain HTML/JS; if they're inlined
without escaping, opening the rendered ``analytics.html`` would execute
as script (textbook stored-XSS).

This test asserts the inverse — every interpolated user-controlled
string is HTML-encoded so a payload like ``<script>alert(1)</script>``
renders as plain text instead of as a tag.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict

from engine.report_export import _render_html


@dataclass
class _FakeTrade:
    """Bare-minimum Trade-like object the renderer reads from."""

    id: int
    direction: str
    pnl: float
    entry_time: str
    exit_time: str
    exit_reason: str
    session: str
    rr: float | None = None


_PAYLOAD = "<script>alert('XSS')</script>"
_EXPECTED_ESCAPED = "&lt;script&gt;alert(&#x27;XSS&#x27;)&lt;/script&gt;"


def _meta(asset: str = "ES", strategy: str = "SmaCrossover") -> Dict[str, Any]:
    return {
        "asset": asset,
        "strategy": strategy,
        "n_trades": 0,
        "initial_equity": 100_000.0,
        "final_equity": 100_000.0,
        "date_range_start": "2020-01-01",
        "date_range_end": "2020-12-31",
    }


def _summary() -> Dict[str, Any]:
    return {
        "n_trades": 0,
        "wins": 0,
        "losses": 0,
        "breakeven": 0,
        "win_rate_pct": 0.0,
        "n_longs": 0,
        "n_shorts": 0,
        "initial_equity": 100_000.0,
        "final_equity": 100_000.0,
        "total_pnl": 0.0,
        "total_return_pct": 0.0,
        "gross_profit": 0.0,
        "gross_loss": 0.0,
        "profit_factor": 0.0,
        "avg_win": 0.0,
        "avg_loss": 0.0,
        "best_trade": 0.0,
        "worst_trade": 0.0,
        "sharpe_ratio": 0.0,
        "sortino_ratio": 0.0,
        "calmar_ratio": 0.0,
        "max_drawdown_pct": 0.0,
        "max_drawdown_abs": 0.0,
        "recovery_factor": 0.0,
        "var_95": 0.0,
        "cvar_95": 0.0,
        "omega_ratio": 0.0,
        "ulcer_index": 0.0,
        "expectancy": 0.0,
        "math_expectation": 0.0,
        "avg_rr": 0.0,
        "kelly_pct": 0.0,
        "max_win_streak": 0,
        "max_loss_streak": 0,
        "avg_duration_min": 0.0,
        "sessions": {},
    }


# ---------------------------------------------------------------------------
# meta-level fields (asset + strategy)
# ---------------------------------------------------------------------------
def test_xss_in_asset_name_is_escaped() -> None:
    """A ``<script>`` tag in ``meta['asset']`` must render as text, not script."""
    meta = _meta(asset=_PAYLOAD)
    rendered = _render_html(meta, _summary(), [])
    assert _PAYLOAD not in rendered, "Raw <script> tag leaked into HTML output"
    assert _EXPECTED_ESCAPED in rendered, (
        "Asset name was not html-escaped — XSS vector still present"
    )


def test_xss_in_strategy_name_is_escaped() -> None:
    """A ``<script>`` tag in ``meta['strategy']`` must render as text, not script."""
    meta = _meta(strategy=_PAYLOAD)
    rendered = _render_html(meta, _summary(), [])
    assert _PAYLOAD not in rendered
    assert _EXPECTED_ESCAPED in rendered


def test_xss_in_date_range_is_escaped() -> None:
    """Date range strings should also be escaped — they originate from user CSVs."""
    meta = _meta()
    meta["date_range_start"] = _PAYLOAD
    meta["date_range_end"] = _PAYLOAD
    rendered = _render_html(meta, _summary(), [])
    assert _PAYLOAD not in rendered


# ---------------------------------------------------------------------------
# per-trade fields (exit_reason, session)
# ---------------------------------------------------------------------------
def test_xss_in_exit_reason_is_escaped() -> None:
    """Strategy-authored ``exit_reason`` strings flow into the trade rows."""
    trade = _FakeTrade(
        id=1,
        direction="long",
        pnl=100.0,
        entry_time="2020-01-01T09:00:00",
        exit_time="2020-01-01T10:00:00",
        exit_reason=_PAYLOAD,
        session="London",
    )
    rendered = _render_html(_meta(), _summary(), [trade])
    assert _PAYLOAD not in rendered
    assert _EXPECTED_ESCAPED in rendered


def test_xss_in_session_breakdown_is_escaped() -> None:
    """Session names are dict keys in ``summary['sessions']`` and rendered to a table."""
    summary = _summary()
    summary["sessions"] = {_PAYLOAD: {"count": 3, "pnl": 50.0, "wins": 2}}
    rendered = _render_html(_meta(), summary, [])
    assert _PAYLOAD not in rendered
    assert _EXPECTED_ESCAPED in rendered


# ---------------------------------------------------------------------------
# benign asset names should still render verbatim (regression guard)
# ---------------------------------------------------------------------------
def test_benign_asset_name_renders_unchanged() -> None:
    """Don't over-escape — ``ES`` should still appear as ``ES`` in the output."""
    rendered = _render_html(_meta(asset="ES_M5"), _summary(), [])
    assert "ES_M5" in rendered
    # Title and metadata both reference the asset
    assert rendered.count("ES_M5") >= 2
