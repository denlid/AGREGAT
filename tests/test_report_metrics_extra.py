"""Tests for engine.report.metrics_extra (Phase 3.1).

Cost attribution, monthly returns, rolling Sharpe, exit reason rows,
top-N trade extraction.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from engine.report.metrics_extra import (
    cost_attribution,
    exit_reason_rows,
    monthly_returns,
    rolling_sharpe,
    top_trades,
    underwater_series,
)


@dataclass
class _T:
    id: int = 0
    direction: str = "long"
    entry_time: str = ""
    exit_time: str = ""
    pnl: float = 0.0
    exit_reason: str = "TP"
    spread_cost: float = 0.0
    slippage_cost: float = 0.0
    commission: float = 0.0


def test_cost_attribution_sums_per_component_and_pct_of_gross():
    trades = [
        _T(pnl=100.0, spread_cost=2.0, slippage_cost=1.0, commission=0.5),
        _T(pnl=50.0, spread_cost=2.0, slippage_cost=1.0, commission=0.5),
        _T(pnl=-30.0, spread_cost=2.0, slippage_cost=1.0, commission=0.5),
    ]
    c = cost_attribution(trades)
    assert c.spread_total == 6.0
    assert c.slippage_total == 3.0
    assert c.commission_total == 1.5
    assert c.total_costs == 10.5
    # gross = pnl_after + costs = 120 + 10.5 = 130.5
    assert abs(c.gross_pnl - 130.5) < 1e-6
    # Each component as % of gross
    assert abs(c.spread_pct - (6.0 / 130.5 * 100)) < 0.01


def test_cost_attribution_handles_missing_fields():
    """Older Trade objects without spread/slip/comm must still aggregate to zero."""
    trades = [_T(pnl=100.0), _T(pnl=-50.0)]
    c = cost_attribution(trades)
    assert c.spread_total == c.slippage_total == c.commission_total == 0.0
    assert c.total_costs == 0.0
    assert c.gross_pnl == 50.0


def test_exit_reason_rows_sorted_by_count_desc():
    trades = [
        _T(pnl=100, exit_reason="TP"),
        _T(pnl=100, exit_reason="TP"),
        _T(pnl=100, exit_reason="TP"),
        _T(pnl=-50, exit_reason="SL"),
        _T(pnl=-50, exit_reason="SL"),
        _T(pnl=0, exit_reason="EOD"),
    ]
    rows = exit_reason_rows(trades)
    assert [r.reason for r in rows] == ["TP", "SL", "EOD"]
    assert rows[0].count == 3 and abs(rows[0].sum_pnl - 300.0) < 1e-6
    assert rows[1].count == 2 and abs(rows[1].sum_pnl + 100.0) < 1e-6


def test_top_trades_returns_best_and_worst_lists():
    trades = [_T(id=i, pnl=float((i * 7) % 11 - 5)) for i in range(20)]
    best, worst = top_trades(trades, n=5)
    assert len(best) == 5 and len(worst) == 5
    # best is sorted descending by pnl, worst ascending-from-most-painful (descending magnitude)
    assert best[0].pnl >= best[-1].pnl
    assert worst[0].pnl <= worst[-1].pnl


def test_top_trades_handles_fewer_than_n_trades():
    trades = [_T(id=1, pnl=5.0), _T(id=2, pnl=-3.0)]
    best, worst = top_trades(trades, n=10)
    assert len(best) == 2 and len(worst) == 2


def test_monthly_returns_grid_dimensions_and_year_totals():
    """Year totals must equal compounded monthly returns within 1e-6."""
    trades = [
        _T(pnl=1000, entry_time="2024-01-15"),
        _T(pnl=500, entry_time="2024-02-15"),
        _T(pnl=-300, entry_time="2024-03-15"),
        _T(pnl=2000, entry_time="2025-01-15"),
    ]
    m = monthly_returns(trades, initial_equity=10_000.0)
    assert m.years == [2024, 2025]
    assert m.pct_grid.shape == (2, 12)
    assert m.year_totals_pct.shape == (2,)
    # Compounded: (1+0.10)*(1+...)... should match year_totals_pct
    for i in range(len(m.years)):
        prod = 1.0
        for j in range(12):
            prod *= 1.0 + m.pct_grid[i, j] / 100.0
        compounded = (prod - 1.0) * 100.0
        assert abs(compounded - m.year_totals_pct[i]) < 1e-6


def test_monthly_returns_empty_input_returns_empty_struct():
    m = monthly_returns([], initial_equity=10_000.0)
    assert m.years == []
    assert m.pct_grid.shape == (0, 12)


def test_rolling_sharpe_window_correctness():
    """Rolling Sharpe of constant-PnL trades should be infinite-equivalent
    (std=0 → returns 0 by convention) — sanity check window math."""
    trades = [_T(pnl=10.0) for _ in range(20)]
    rs = rolling_sharpe(trades, window=10)
    assert rs.values.size == 20
    # First 9 are NaN, rest 0 (constant pnl → std=0).
    assert np.isnan(rs.values[:9]).all()
    assert (rs.values[9:] == 0.0).all()


def test_rolling_sharpe_returns_nan_when_below_window():
    trades = [_T(pnl=10.0) for _ in range(5)]
    rs = rolling_sharpe(trades, window=10)
    assert np.isnan(rs.values).all()
    assert rs.mean_value == 0.0


def test_underwater_series_max_drawdown_matches_dollar_dd():
    """For a known trade sequence, the deepest underwater % matches
    the absolute drawdown converted to %."""
    trades = [_T(pnl=100), _T(pnl=200), _T(pnl=-150), _T(pnl=-100), _T(pnl=300)]
    init = 1000.0
    uw = underwater_series(trades, initial_equity=init)
    # Deepest underwater after trade #4 = (1000+100+200-150-100=1050) vs peak 1300 → -19.23%
    deepest = float(uw.min())
    assert abs(deepest - ((1050 - 1300) / 1300 * 100)) < 1e-6
