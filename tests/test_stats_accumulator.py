"""StatsAccumulator — incremental tally of closed trades."""

from __future__ import annotations

import pytest

from engine.models.models import Trade
from engine.analytics.stats_accumulator import StatsAccumulator


def _trade(pnl: float, direction: str = "long", tid: int = 0) -> Trade:
    return Trade(
        id=tid,
        direction=direction,
        entry_price=100.0,
        exit_price=100.0 + pnl,
        pnl=pnl,
        pnl_points=pnl,
        entry_index=0,
        exit_index=0,
        entry_time="2024-01-01 10:00",
        exit_time="2024-01-01 10:05",
        volume=1.0,
        duration_minutes=5,
        exit_reason="TP",
        mae=0.0,
        mfe=0.0,
        mae_dollars=0.0,
        mfe_dollars=0.0,
        session="London_OTT",
        initial_sl=99.0,
        rr=1.0,
        tag="",
    )


def test_starts_empty():
    s = StatsAccumulator()
    assert s.total_trades == 0
    assert s.win_rate == 0.0
    assert s.profit_factor == 0.0


def test_update_counts_wins_and_losses():
    s = StatsAccumulator()
    s.update(_trade(+10, tid=1))
    s.update(_trade(-5, tid=2))
    s.update(_trade(+5, tid=3))
    assert s.wins == 2
    assert s.losses == 1
    assert s.gross_profit == pytest.approx(15.0)
    assert s.gross_loss == pytest.approx(5.0)


def test_update_counts_longs_and_shorts():
    s = StatsAccumulator()
    s.update(_trade(+10, "long", tid=1))
    s.update(_trade(-5, "short", tid=2))
    s.update(_trade(+5, "long", tid=3))
    assert s.longs == 2
    assert s.shorts == 1


def test_zero_pnl_does_not_affect_win_loss():
    s = StatsAccumulator()
    s.update(_trade(0.0, tid=1))
    assert s.wins == 0
    assert s.losses == 0


def test_profit_factor_normal():
    s = StatsAccumulator()
    s.update(_trade(+30, tid=1))
    s.update(_trade(-10, tid=2))
    assert s.profit_factor == pytest.approx(3.0)


def test_profit_factor_with_zero_losses_returns_inf_when_wins_exist():
    s = StatsAccumulator()
    s.update(_trade(+10, tid=1))
    assert s.profit_factor == float("inf")


def test_profit_factor_with_no_trades_returns_zero():
    s = StatsAccumulator()
    assert s.profit_factor == 0.0


def test_win_rate_computation():
    s = StatsAccumulator()
    s.update(_trade(+10, tid=1))
    s.update(_trade(+10, tid=2))
    s.update(_trade(+10, tid=3))
    s.update(_trade(-5, tid=4))
    assert s.win_rate == pytest.approx(75.0)


def test_reset_zeroes_everything():
    s = StatsAccumulator()
    s.update(_trade(+10, tid=1))
    s.update(_trade(-5, tid=2))
    s.reset()
    assert s.wins == 0
    assert s.losses == 0
    assert s.gross_profit == 0.0
    assert s.gross_loss == 0.0
    assert s.longs == 0
    assert s.shorts == 0
