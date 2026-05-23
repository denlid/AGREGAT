"""Risk-of-ruin, time-under-water, ulcer / pain metrics."""

from __future__ import annotations

import numpy as np
import pytest

from engine.analytics.risk_metrics import (
    pain_index,
    pain_ratio,
    risk_of_ruin,
    time_under_water,
    ulcer_index,
)


# ----------------------------------------------------------------------------
# Risk of Ruin
# ----------------------------------------------------------------------------
def test_ror_zero_for_dominant_edge():
    """Edge huge → RoR ≈ 0."""
    ror = risk_of_ruin(win_rate=0.9, avg_win=10.0, avg_loss=1.0, risk_per_trade_pct=1.0)
    assert ror < 0.01


def test_ror_one_for_negative_edge():
    """Win rate 30% with 1:1 payoff → expected loss → RoR = 1."""
    ror = risk_of_ruin(win_rate=0.3, avg_win=1.0, avg_loss=1.0, risk_per_trade_pct=1.0)
    assert ror == 1.0


def test_ror_decreases_when_lowering_risk():
    """Smaller bets → lower RoR for the same edge."""
    a = risk_of_ruin(win_rate=0.55, avg_win=1.0, avg_loss=1.0, risk_per_trade_pct=2.0)
    b = risk_of_ruin(win_rate=0.55, avg_win=1.0, avg_loss=1.0, risk_per_trade_pct=1.0)
    assert b < a


def test_ror_handles_winrate_edge_cases():
    assert risk_of_ruin(0.0, 1, 1, risk_per_trade_pct=1.0) == 1.0
    assert risk_of_ruin(1.0, 1, 1, risk_per_trade_pct=1.0) == 0.0
    assert risk_of_ruin(0.5, 1, 0, risk_per_trade_pct=1.0) == 1.0


def test_ror_zero_risk_returns_one():
    assert risk_of_ruin(0.6, 1, 1, risk_per_trade_pct=0.0) == 1.0


# ----------------------------------------------------------------------------
# Time under water
# ----------------------------------------------------------------------------
def test_time_under_water_no_drawdown():
    eq = np.array([100, 105, 110, 120, 125], dtype=float)
    s = time_under_water(eq)
    assert s.pct_bars_underwater == 0.0
    assert s.max_run_bars == 0
    assert s.n_runs == 0


def test_time_under_water_single_run():
    eq = np.array([100, 110, 105, 102, 108, 120], dtype=float)
    s = time_under_water(eq)
    # Bars 2 and 3 are underwater (105 < 110, 102 < 110); bar 4 (108) still < 110.
    assert s.max_run_bars == 3
    assert s.n_runs == 1
    assert pytest.approx(s.pct_bars_underwater, rel=1e-6) == 3 / 6


def test_time_under_water_multiple_runs():
    eq = np.array([100, 110, 105, 115, 113, 120, 118, 125], dtype=float)
    s = time_under_water(eq)
    assert s.n_runs == 3
    assert s.max_run_bars == 1


def test_time_under_water_empty_input():
    s = time_under_water(np.array([]))
    assert s.pct_bars_underwater == 0.0
    assert s.max_run_bars == 0
    assert s.n_runs == 0


# ----------------------------------------------------------------------------
# Ulcer / Pain
# ----------------------------------------------------------------------------
def test_ulcer_index_zero_on_monotone_curve():
    eq = np.linspace(100, 200, 50)
    assert ulcer_index(eq) == pytest.approx(0.0)


def test_ulcer_index_positive_on_drawdown():
    eq = np.array([100, 90, 80, 90, 100], dtype=float)
    ui = ulcer_index(eq)
    assert ui > 0


def test_pain_index_average_drawdown():
    eq = np.array([100.0, 50.0, 100.0])
    # Drawdown sequence: [0, 50, 0] → pain ≈ 50/3
    assert pain_index(eq) == pytest.approx(50.0 / 3.0, rel=1e-6)


def test_pain_ratio_zero_when_no_pain():
    eq = np.linspace(100, 200, 50)
    assert pain_ratio(eq) == 0.0


def test_pain_ratio_positive_on_winning_strategy_with_drawdown():
    eq = np.array([100, 90, 95, 80, 110, 130, 140], dtype=float)
    pr = pain_ratio(eq, periods_per_year=252.0)
    assert pr > 0
