"""Phase 5 — trade analytics module."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pytest

from engine.analytics.trade_analytics import (
    compare_runs,
    drawdown_durations,
    exit_reason_attribution,
    hour_weekday_heatmap,
    mae_mfe_arrays,
    month_year_heatmap,
    r_multiples,
    simulate_slippage,
    summarise_run,
)


# ----------------------------------------------------------------------------
# Tiny test fixtures — minimal Trade-shaped objects.
# ----------------------------------------------------------------------------
@dataclass
class FakeTrade:
    pnl: float
    entry_time: str = "2024-01-15 10:30:00"
    initial_sl: float = 10.0
    volume: float = 1.0
    mae_dollars: float = 0.0
    mfe_dollars: float = 0.0
    mae: float = 0.0
    mfe: float = 0.0
    exit_reason: str = "TP"


# ----------------------------------------------------------------------------
# MAE / MFE
# ----------------------------------------------------------------------------
def test_mae_mfe_arrays_use_dollars_when_present():
    trades = [
        FakeTrade(pnl=20, mae_dollars=-3, mfe_dollars=8, initial_sl=10, volume=1),
        FakeTrade(pnl=-10, mae_dollars=-12, mfe_dollars=2, initial_sl=10, volume=1),
    ]
    res = mae_mfe_arrays(trades)
    assert np.array_equal(res.mae_dollars, [-3, -12])
    assert np.array_equal(res.mfe_dollars, [8, 2])
    # Risk = 10 * 1 = 10 → R-versions divide by 10.
    assert np.array_equal(res.mae_r, [-0.3, -1.2])
    assert np.array_equal(res.mfe_r, [0.8, 0.2])


def test_mae_mfe_arrays_fall_back_to_points():
    trades = [FakeTrade(pnl=5, mae=-1.5, mfe=3.0, initial_sl=10)]
    res = mae_mfe_arrays(trades)
    assert res.mae_dollars[0] == -1.5
    assert res.mfe_dollars[0] == 3.0


def test_mae_mfe_handles_zero_risk():
    trades = [FakeTrade(pnl=5, initial_sl=0, mae_dollars=-1, mfe_dollars=2)]
    res = mae_mfe_arrays(trades)
    assert np.isnan(res.mae_r[0])
    assert np.isnan(res.mfe_r[0])


# ----------------------------------------------------------------------------
# R-multiples
# ----------------------------------------------------------------------------
def test_r_multiples_basic():
    trades = [
        FakeTrade(pnl=20, initial_sl=10, volume=1),
        FakeTrade(pnl=-10, initial_sl=10, volume=1),
        FakeTrade(pnl=15, initial_sl=10, volume=1),
    ]
    res = r_multiples(trades)
    assert np.array_equal(res.r_values, [2.0, -1.0, 1.5])
    assert np.array_equal(res.cumulative_r, [2.0, 1.0, 2.5])
    assert res.expectancy_r == pytest.approx((2.0 - 1.0 + 1.5) / 3.0)


def test_r_multiples_skips_nan_for_expectancy():
    trades = [
        FakeTrade(pnl=20, initial_sl=10),
        FakeTrade(pnl=5, initial_sl=0),  # NaN R — skip from expectancy
    ]
    res = r_multiples(trades)
    assert np.isnan(res.r_values[1])
    assert res.expectancy_r == 2.0


def test_r_multiples_empty():
    res = r_multiples([])
    assert res.r_values.size == 0
    assert res.expectancy_r == 0.0


def test_r_multiples_use_real_engine_trade_shape():
    """Phase 1 (A1) regression — Trade.initial_sl is the *absolute price*
    of the SL on the chart, not a distance. Ensure r_multiples reads
    ``Trade.rr`` (already computed correctly by the engine) instead of
    treating ``initial_sl`` as a price-distance.
    """
    from engine.models.models import Trade

    real_trade = Trade(
        id=1,
        direction="long",
        entry_price=18_000.0,
        exit_price=18_050.0,
        pnl=50.0,
        pnl_points=50.0,
        entry_index=0,
        exit_index=10,
        entry_time="2024-01-15 10:00:00",
        exit_time="2024-01-15 10:10:00",
        volume=1.0,
        duration_minutes=10,
        exit_reason="TP",
        mae=2.0,
        mfe=50.0,
        mae_dollars=2.0,
        mfe_dollars=50.0,
        session="London_OTT",
        initial_sl=17_950.0,  # SL price → distance = 50pts
        rr=1.0,  # +50 pts profit on a 50-pt risk → R=+1
    )
    res = r_multiples([real_trade])
    # Expectation: R = +1.0 (one full R won). Pre-fix this returned ≈ 0.0028
    # because the function divided pnl by the absolute SL *price* (17 950).
    assert res.r_values[0] == pytest.approx(1.0)
    assert res.expectancy_r == pytest.approx(1.0)


def test_mae_mfe_arrays_use_real_engine_trade_shape():
    """Phase 1 (A1) regression — MAE/MFE R-normalisation must use the
    price *distance* between entry_price and initial_sl, not the absolute
    SL price.
    """
    from engine.models.models import Trade

    real_trade = Trade(
        id=1,
        direction="long",
        entry_price=18_000.0,
        exit_price=18_050.0,
        pnl=50.0,
        pnl_points=50.0,
        entry_index=0,
        exit_index=10,
        entry_time="2024-01-15 10:00:00",
        exit_time="2024-01-15 10:10:00",
        volume=1.0,
        duration_minutes=10,
        exit_reason="TP",
        mae=10.0,
        mfe=50.0,
        mae_dollars=10.0,
        mfe_dollars=50.0,
        session="London_OTT",
        initial_sl=17_950.0,
        rr=1.0,
    )
    res = mae_mfe_arrays([real_trade])
    # risk_distance = |18000 - 17950| = 50; mae_pts = 10 → mae_r = 0.2.
    assert res.mae_r[0] == pytest.approx(0.2)
    assert res.mfe_r[0] == pytest.approx(1.0)


# ----------------------------------------------------------------------------
# Hour × weekday heatmap
# ----------------------------------------------------------------------------
def test_hour_weekday_heatmap_shape_and_counts():
    # 2024-01-15 is a Monday.
    trades = [
        FakeTrade(pnl=10, entry_time="2024-01-15 10:00:00"),
        FakeTrade(pnl=-5, entry_time="2024-01-15 10:00:00"),
        FakeTrade(pnl=20, entry_time="2024-01-16 14:30:00"),  # Tuesday
    ]
    h = hour_weekday_heatmap(trades)
    assert h.counts.shape == (24, 7)
    assert h.counts[10, 0] == 2  # Monday 10:00 — 2 trades
    assert h.counts[14, 1] == 1  # Tuesday 14:00 — 1 trade
    assert h.win_rate[10, 0] == 0.5  # 1 win / 2 trades
    assert h.net_pnl[10, 0] == 5.0


def test_hour_weekday_heatmap_skips_unparseable_times():
    trades = [FakeTrade(pnl=10, entry_time=""), FakeTrade(pnl=20, entry_time="2024-01-15 10:00:00")]
    h = hour_weekday_heatmap(trades)
    assert h.counts.sum() == 1


def test_hour_weekday_heatmap_winrate_nan_when_no_trades():
    h = hour_weekday_heatmap([])
    assert np.isnan(h.win_rate[0, 0])
    assert h.counts[0, 0] == 0


# ----------------------------------------------------------------------------
# Month × year heatmap
# ----------------------------------------------------------------------------
def test_month_year_heatmap_groups_by_year_month():
    trades = [
        FakeTrade(pnl=10, entry_time="2024-01-15 10:00:00"),
        FakeTrade(pnl=20, entry_time="2024-01-22 10:00:00"),
        FakeTrade(pnl=-5, entry_time="2024-02-01 10:00:00"),
        FakeTrade(pnl=30, entry_time="2025-03-10 10:00:00"),
    ]
    h = month_year_heatmap(trades)
    assert h.years == [2024, 2025]
    assert h.net_pnl[0, 0] == 30  # Jan 2024
    assert h.net_pnl[0, 1] == -5  # Feb 2024
    assert h.net_pnl[1, 2] == 30  # Mar 2025
    assert h.counts[0, 0] == 2


def test_month_year_heatmap_empty():
    h = month_year_heatmap([])
    assert h.years == []
    assert h.net_pnl.shape == (0, 12)


# ----------------------------------------------------------------------------
# Drawdown durations
# ----------------------------------------------------------------------------
def test_drawdown_durations_no_drawdown():
    eq = np.array([100, 105, 110, 120], dtype=float)
    eps = drawdown_durations(eq)
    assert eps == []


def test_drawdown_durations_single_episode_recovers():
    eq = np.array([100, 110, 105, 102, 108, 120], dtype=float)
    eps = drawdown_durations(eq)
    assert len(eps) == 1
    e = eps[0]
    assert e.start_idx == 1  # peak at 110
    assert e.trough_idx == 3  # 102
    assert e.recover_idx == 5  # equity 120 retakes peak
    assert e.depth_dollars == pytest.approx(8.0)
    assert e.depth_pct == pytest.approx(8.0 / 110 * 100)


def test_drawdown_durations_unrecovered_at_end():
    eq = np.array([100, 110, 105, 100], dtype=float)
    eps = drawdown_durations(eq)
    assert len(eps) == 1
    assert eps[0].recover_idx is None


def test_drawdown_durations_multiple_episodes():
    eq = np.array([100, 110, 105, 115, 113, 120, 118, 125], dtype=float)
    eps = drawdown_durations(eq)
    assert len(eps) == 3


# ----------------------------------------------------------------------------
# Exit reason attribution
# ----------------------------------------------------------------------------
def test_exit_reason_attribution_aggregates_per_reason():
    trades = [
        FakeTrade(pnl=10, exit_reason="TP"),
        FakeTrade(pnl=20, exit_reason="TP"),
        FakeTrade(pnl=-5, exit_reason="SL"),
        FakeTrade(pnl=-10, exit_reason="SL"),
        FakeTrade(pnl=3, exit_reason="MANUAL"),
    ]
    rows = exit_reason_attribution(trades)
    by = {r.reason: r for r in rows}
    assert by["TP"].count == 2
    assert by["TP"].sum_pnl == 30
    assert by["TP"].win_rate == 1.0
    assert by["SL"].sum_pnl == -15
    assert by["SL"].win_rate == 0.0


def test_exit_reason_attribution_orders_by_total_pnl_desc():
    trades = [
        FakeTrade(pnl=-100, exit_reason="SL"),
        FakeTrade(pnl=50, exit_reason="TP"),
    ]
    rows = exit_reason_attribution(trades)
    assert rows[0].reason == "TP"
    assert rows[-1].reason == "SL"


# ----------------------------------------------------------------------------
# Slippage simulator
# ----------------------------------------------------------------------------
def test_slippage_subtracts_constant_per_trade():
    trades = [FakeTrade(pnl=10), FakeTrade(pnl=-5), FakeTrade(pnl=20)]
    res = simulate_slippage(trades, slip_per_trade_dollars=1.0)
    assert np.array_equal(res.perturbed_pnls, [9, -6, 19])
    assert res.base_total == 25
    assert res.perturbed_total == 22
    assert res.pnl_delta == -3


def test_slippage_zero_cost_is_noop():
    trades = [FakeTrade(pnl=10), FakeTrade(pnl=-5)]
    res = simulate_slippage(trades, slip_per_trade_dollars=0.0)
    assert res.pnl_delta == 0


def test_slippage_empty_input():
    res = simulate_slippage([], slip_per_trade_dollars=5.0)
    assert res.base_pnls.size == 0
    assert res.pnl_delta == 0.0


# ----------------------------------------------------------------------------
# Strategy comparison
# ----------------------------------------------------------------------------
def test_summarise_run_basic_kpis():
    trades = [
        FakeTrade(pnl=20),  # win
        FakeTrade(pnl=-10),  # loss
        FakeTrade(pnl=15),  # win
        FakeTrade(pnl=-5),  # loss
    ]
    s = summarise_run(trades, name="A", initial_equity=1000.0)
    assert s.n_trades == 4
    assert s.total_pnl == 20.0
    assert s.win_rate == 0.5
    assert s.profit_factor == pytest.approx((20 + 15) / (10 + 5))
    assert s.expectancy == pytest.approx(20.0 / 4)


def test_summarise_run_empty_returns_zeros():
    s = summarise_run([], name="X", initial_equity=1000.0)
    assert s.n_trades == 0
    assert s.total_pnl == 0.0
    assert s.profit_factor == 0.0


def test_compare_runs_diff_metrics():
    a = summarise_run([FakeTrade(pnl=10)], name="A", initial_equity=1000.0)
    b = summarise_run([FakeTrade(pnl=20)], name="B", initial_equity=1000.0)
    diff = compare_runs(a, b)
    assert diff["total_pnl"]["a"] == 10
    assert diff["total_pnl"]["b"] == 20
    assert diff["total_pnl"]["delta"] == 10
    assert diff["total_pnl"]["delta_pct"] == 100.0
