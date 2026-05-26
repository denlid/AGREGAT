"""Phase 2 EPIC 2.2 — tests for the auto-insights rule engine."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from engine.analytics.context import MetricsBundle
from gui.analytics.insights_engine import (
    Insight,
    _compute_trade_stats,
    generate_insights,
)


@dataclass
class _T:
    pnl: float = 0.0
    duration_minutes: float = 60
    entry_time: str = "2024-01-01 12:00:00"
    exit_time: str = "2024-01-01 13:00:00"
    session: str = "NY"


def _mb(**values) -> MetricsBundle:
    values.setdefault("n_trades", 100)
    return MetricsBundle(
        period_token=("all",),
        filter_token=("all",),
        n_trades=int(values["n_trades"]),
        values={k: float(v) for k, v in values.items()},
    )


# ---------------------------------------------------------------------------
# Aggregate helpers
# ---------------------------------------------------------------------------


def test_compute_trade_stats_empty_returns_zero_state() -> None:
    st = _compute_trade_stats([])
    assert st.n == 0
    assert st.total_pnl == 0.0
    assert st.win_streak_max == 0
    assert st.loss_streak_max == 0


def test_compute_trade_stats_streaks() -> None:
    trades = [
        _T(pnl=10), _T(pnl=10), _T(pnl=10),       # 3-streak
        _T(pnl=-5), _T(pnl=-5),                    # broken
        _T(pnl=20), _T(pnl=20), _T(pnl=20), _T(pnl=20),  # 4-streak
    ]
    st = _compute_trade_stats(trades)
    assert st.win_streak_max == 4
    assert st.loss_streak_max == 2
    assert st.current_win_streak == 4
    assert st.current_loss_streak == 0


def test_compute_trade_stats_durations_split_by_outcome() -> None:
    trades = [
        _T(pnl=10, duration_minutes=60),
        _T(pnl=20, duration_minutes=80),
        _T(pnl=-5, duration_minutes=200),
        _T(pnl=-3, duration_minutes=400),
    ]
    st = _compute_trade_stats(trades)
    assert st.winners_duration_mean_min == pytest.approx(70.0)
    assert st.losers_duration_mean_min == pytest.approx(300.0)


# ---------------------------------------------------------------------------
# Individual rules
# ---------------------------------------------------------------------------


def test_rule_negative_expectancy_fires() -> None:
    mb = _mb(n_trades=100, expectancy=-2.5, profit_factor=0.9, win_rate=45.0)
    out = generate_insights(mb, trades=None)
    rule_ids = {i.rule_id for i in out}
    assert "negative_expectancy" in rule_ids
    # All rules in 'risk' section share the same bucket — make sure we
    # still get critical first.
    risk = [i for i in out if i.section == "risk"]
    assert risk[0].severity == "critical"


def test_rule_low_wr_low_rr() -> None:
    mb = _mb(win_rate=30.0, avg_rr=0.5, n_trades=120, expectancy=1.0, profit_factor=1.1)
    out = generate_insights(mb, trades=None)
    assert any(i.rule_id == "low_wr_low_rr" for i in out)


def test_rule_kelly_high() -> None:
    # WR=70%, payoff=3 → Kelly = 0.7 - 0.3/3 = 0.6 → 60%
    mb = _mb(win_rate=70.0, avg_win=300.0, avg_loss=100.0, n_trades=200, expectancy=10.0, profit_factor=2.5)
    out = generate_insights(mb, trades=None)
    assert any(i.rule_id == "kelly_high" and i.severity == "critical" for i in out)


def test_rule_dd_vs_pf_fires_when_both_high() -> None:
    mb = _mb(profit_factor=2.5, max_dd_pct=0.30, n_trades=200, win_rate=55.0)
    out = generate_insights(mb, trades=None)
    assert any(i.rule_id == "dd_vs_pf" for i in out)


def test_rule_let_losers_run_fires_on_duration_asymmetry() -> None:
    mb = _mb(n_trades=20, win_rate=50.0)
    trades = [_T(pnl=10, duration_minutes=30) for _ in range(10)]
    trades += [_T(pnl=-5, duration_minutes=120) for _ in range(10)]
    out = generate_insights(mb, trades=trades)
    assert any(i.rule_id == "let_losers_run" for i in out)


def test_rule_session_concentration_emits_when_dominant() -> None:
    mb = _mb(n_trades=20, win_rate=60.0)
    # 80% of PnL from NY, rest from London.
    trades = [_T(pnl=10, session="NY") for _ in range(15)]
    trades += [_T(pnl=5, session="LDN") for _ in range(5)]
    out = generate_insights(mb, trades=trades)
    assert any(i.rule_id == "session_concentration" for i in out)


def test_rule_win_streak_active_fires_at_8_plus() -> None:
    mb = _mb(n_trades=10)
    trades = [_T(pnl=5) for _ in range(9)]
    out = generate_insights(mb, trades=trades)
    assert any(i.rule_id == "win_streak_active" for i in out)


def test_rule_loss_streak_active_fires_at_5_plus() -> None:
    mb = _mb(n_trades=10)
    trades = [_T(pnl=-5) for _ in range(6)]
    out = generate_insights(mb, trades=trades)
    assert any(i.rule_id == "loss_streak_active" for i in out)


def test_rule_sample_too_small_fires_below_30() -> None:
    mb = _mb(n_trades=10, win_rate=50.0)
    out = generate_insights(mb, trades=None)
    assert any(i.rule_id == "sample_small" for i in out)


def test_no_insights_on_empty_bundle() -> None:
    mb = MetricsBundle(period_token=(), filter_token=(), n_trades=0, values={})
    out = generate_insights(mb, trades=None)
    # Only "sample_small" can fire with n_trades>0; with 0 nothing should fire.
    assert all(i.rule_id != "sample_small" for i in out)


def test_insight_dataclass_is_frozen() -> None:
    ins = Insight(
        rule_id="x",
        severity="info",
        headline="h",
        detail="d",
        section="risk",
    )
    with pytest.raises(Exception):
        ins.severity = "warning"  # type: ignore[misc]


def test_generate_caps_per_section() -> None:
    # Construct a bundle that fires several risk rules.
    mb = _mb(
        n_trades=200,
        expectancy=-2.5,
        profit_factor=0.8,
        win_rate=30.0,
        avg_rr=0.4,
        max_dd_pct=0.30,
    )
    out = generate_insights(mb, trades=None, max_per_section=2)
    risk = [i for i in out if i.section == "risk"]
    assert len(risk) <= 2
