"""Phase 2 EPIC 2.5 — tests for the milestone tracker."""

from __future__ import annotations

from dataclasses import dataclass

from gui.analytics.milestone_tracker import Milestone, MilestoneTracker


@dataclass
class _T:
    pnl: float
    entry_time: str = "2024-01-01 12:00:00"
    exit_time: str = "2024-01-01 13:00:00"


def test_trade_count_tiers_fire_once_each() -> None:
    t = MilestoneTracker()
    trades = [_T(pnl=10) for _ in range(100)]
    ms = t.evaluate(trades, initial_equity=1000)
    keys = {m.key for m in ms}
    assert "trade_count_100" in keys
    # Re-evaluate — tier should not fire again.
    ms2 = t.evaluate(trades, initial_equity=1000)
    assert all(m.key != "trade_count_100" for m in ms2)


def test_new_ath_equity_fires() -> None:
    t = MilestoneTracker()
    trades = [_T(pnl=10) for _ in range(50)]
    ms = t.evaluate(trades, initial_equity=1000)
    assert any(m.headline.lower().startswith("new equity high") for m in ms)


def test_longest_win_streak_fires_at_5_plus() -> None:
    t = MilestoneTracker()
    trades = [_T(pnl=10) for _ in range(7)]
    ms = t.evaluate(trades, initial_equity=0)
    assert any("longest win streak" in m.headline.lower() for m in ms)


def test_reset_clears_state() -> None:
    t = MilestoneTracker()
    trades = [_T(pnl=10) for _ in range(50)]
    t.evaluate(trades, initial_equity=1000)
    assert t.seen
    t.reset()
    assert not t.seen
    assert t.peak_equity == float("-inf")


def test_best_single_day_fires() -> None:
    t = MilestoneTracker()
    trades = [
        _T(pnl=10, entry_time="2024-01-01 09:00:00", exit_time="2024-01-01 10:00:00")
        for _ in range(3)
    ] + [
        _T(pnl=200, entry_time="2024-01-02 09:00:00", exit_time="2024-01-02 10:00:00"),
    ]
    ms = t.evaluate(trades, initial_equity=0)
    assert any("best single day" in m.headline.lower() for m in ms)


def test_milestone_is_frozen() -> None:
    import pytest

    m = Milestone(key="x", icon="x", headline="h", detail="d")
    with pytest.raises(Exception):
        m.headline = "n"  # type: ignore[misc]


def test_no_milestones_for_empty_trade_list() -> None:
    t = MilestoneTracker()
    assert t.evaluate([], initial_equity=0) == []
