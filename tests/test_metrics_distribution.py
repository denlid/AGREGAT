"""Sprint 4.1 — Tests for engine.metrics.distribution + trade_stats."""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from engine.metrics.distribution import (
    cvar_95,
    cvar_99,
    expected_shortfall,
    hold_time_distribution,
    profit_factor,
    r_multiple_stats,
    recovery_factor,
    slippage_drag_pct,
    streak_analysis,
    var_95,
    var_99,
)
from engine.metrics.trade_stats import (
    avg_loss,
    avg_trade,
    avg_win,
    best_trade,
    commission_drag_pct,
    expectancy,
    gross_loss,
    gross_profit,
    loss_rate,
    net_profit,
    num_trades,
    payoff_ratio,
    spread_drag_pct,
    win_rate,
    worst_trade,
)


@dataclass
class _Trade:
    """Minimal trade-like object for cost-drag tests."""

    pnl: float
    commission: float = 0.0
    slippage_cost: float = 0.0
    spread_cost: float = 0.0


# -----------------------------------------------------------------------------
# VaR / CVaR / ES
# -----------------------------------------------------------------------------
class TestVaRCVaR:
    def test_var_95_negative_for_loss_distribution(self) -> None:
        rng = np.random.default_rng(0)
        pnls = rng.normal(0.0, 100.0, size=10_000)
        v = var_95(pnls)
        # 5% tail of N(0, 100²) ≈ −165
        assert -200.0 < v < -100.0

    def test_var_99_more_extreme_than_var_95(self) -> None:
        rng = np.random.default_rng(1)
        pnls = rng.normal(0.0, 100.0, size=10_000)
        assert var_99(pnls) <= var_95(pnls)

    def test_cvar_below_var(self) -> None:
        rng = np.random.default_rng(2)
        pnls = rng.normal(0.0, 100.0, size=10_000)
        assert cvar_95(pnls) <= var_95(pnls)

    def test_expected_shortfall_alias(self) -> None:
        pnls = np.array([-10.0, -5.0, -1.0, 1.0, 5.0, 10.0])
        assert expected_shortfall(pnls, confidence=0.95) == cvar_95(pnls)


# -----------------------------------------------------------------------------
# R-multiple stats
# -----------------------------------------------------------------------------
class TestRMultipleStats:
    def test_empty_array_returns_zeros(self) -> None:
        s = r_multiple_stats([])
        assert s.count == 0
        assert s.mean == 0.0
        assert s.histogram == ()

    def test_basic_stats(self) -> None:
        rs = [-2.0, -1.0, 0.5, 1.0, 2.0, 3.0]
        s = r_multiple_stats(rs)
        assert s.count == 6
        assert math.isclose(s.mean, sum(rs) / len(rs))
        assert s.median == 0.75
        assert s.expectancy == s.mean
        # Default bins: (-∞,-3,-2,-1,0,1,2,3,5,+∞) → 9 bins
        assert len(s.histogram) == 9


# -----------------------------------------------------------------------------
# Streak analysis
# -----------------------------------------------------------------------------
class TestStreaks:
    def test_empty_array(self) -> None:
        s = streak_analysis([])
        assert s.longest_win == 0
        assert s.longest_loss == 0
        assert s.current == 0

    def test_alternating_wins_losses(self) -> None:
        s = streak_analysis([1.0, -1.0, 1.0, -1.0])
        assert s.longest_win == 1
        assert s.longest_loss == 1

    def test_long_streak(self) -> None:
        s = streak_analysis([1.0, 1.0, 1.0, -1.0, -1.0, 1.0])
        assert s.longest_win == 3
        assert s.longest_loss == 2
        assert s.current == 1  # ended on a winner

    def test_average_streak(self) -> None:
        s = streak_analysis([1.0, 1.0, -1.0, 1.0, 1.0, 1.0, -1.0, -1.0])
        assert s.avg_win_streak > 0


# -----------------------------------------------------------------------------
# Hold-time distribution
# -----------------------------------------------------------------------------
class TestHoldTime:
    def test_empty(self) -> None:
        s = hold_time_distribution([])
        assert s.count == 0

    def test_quartiles(self) -> None:
        s = hold_time_distribution([10.0, 20.0, 30.0, 40.0, 50.0])
        assert s.count == 5
        assert s.median_min == 30.0
        assert s.min_min == 10.0
        assert s.max_min == 50.0


# -----------------------------------------------------------------------------
# Cost drags
# -----------------------------------------------------------------------------
class TestCostDrags:
    def test_zero_when_empty(self) -> None:
        assert slippage_drag_pct([]) == 0.0
        assert commission_drag_pct([]) == 0.0
        assert spread_drag_pct([]) == 0.0

    def test_realistic_split(self) -> None:
        # Two profitable trades after costs:
        # trade1 net=80, commission=10, slippage=5, spread=5 → gross 100
        # trade2 net=160, commission=20, slippage=10, spread=10 → gross 200
        # gross total = 300, commission total = 30 → 0.10, slippage 15 → 0.05
        trades = [
            _Trade(pnl=80.0, commission=10.0, slippage_cost=5.0, spread_cost=5.0),
            _Trade(pnl=160.0, commission=20.0, slippage_cost=10.0, spread_cost=10.0),
        ]
        assert math.isclose(commission_drag_pct(trades), 30.0 / 300.0)
        assert math.isclose(slippage_drag_pct(trades), 15.0 / 300.0)
        assert math.isclose(spread_drag_pct(trades), 15.0 / 300.0)


# -----------------------------------------------------------------------------
# Profit factor + recovery factor
# -----------------------------------------------------------------------------
class TestProfitAndRecovery:
    def test_profit_factor_basic(self) -> None:
        # 3 wins of 10, 2 losses of 5 → 30/10 = 3.0
        assert profit_factor([10.0, 10.0, 10.0, -5.0, -5.0]) == 3.0

    def test_profit_factor_no_losses(self) -> None:
        assert profit_factor([1.0, 2.0, 3.0]) == float("inf")

    def test_profit_factor_no_wins(self) -> None:
        assert profit_factor([-1.0, -2.0]) == 0.0

    def test_recovery_factor(self) -> None:
        assert recovery_factor(1000.0, 200.0) == 5.0

    def test_recovery_factor_zero_drawdown(self) -> None:
        assert recovery_factor(1000.0, 0.0) == 0.0


# -----------------------------------------------------------------------------
# Trade-stats helpers
# -----------------------------------------------------------------------------
class TestTradeStats:
    def test_num_trades(self) -> None:
        assert num_trades([1.0, 2.0, 3.0]) == 3

    def test_win_loss_rate(self) -> None:
        pnls = [1.0, 2.0, -1.0, 0.0]
        assert math.isclose(win_rate(pnls), 0.5)
        assert math.isclose(loss_rate(pnls), 0.25)

    def test_avg_win_loss_trade(self) -> None:
        pnls = [10.0, 20.0, -5.0, -15.0]
        assert avg_win(pnls) == 15.0
        assert avg_loss(pnls) == -10.0
        assert math.isclose(avg_trade(pnls), 2.5)

    def test_best_worst(self) -> None:
        pnls = [10.0, 20.0, -5.0, -15.0]
        assert best_trade(pnls) == 20.0
        assert worst_trade(pnls) == -15.0

    def test_payoff_ratio(self) -> None:
        # avg_win = 15, avg_loss = -10 → 1.5
        assert payoff_ratio([10.0, 20.0, -5.0, -15.0]) == 1.5

    def test_payoff_ratio_no_losses(self) -> None:
        assert payoff_ratio([1.0, 2.0, 3.0]) == 0.0

    def test_expectancy_matches_mean(self) -> None:
        pnls = [10.0, -5.0, 7.0, -3.0]
        assert math.isclose(expectancy(pnls), avg_trade(pnls))

    def test_gross_net_loss_profit(self) -> None:
        pnls = [10.0, -5.0, 7.0, -3.0]
        assert gross_profit(pnls) == 17.0
        assert gross_loss(pnls) == -8.0
        assert net_profit(pnls) == 9.0
