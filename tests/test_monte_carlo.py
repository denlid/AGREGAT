"""Bootstrap / shock-injection Monte Carlo."""

from __future__ import annotations

import numpy as np
import pytest

from engine.analytics.monte_carlo import bootstrap_pnl, shock_pnl


def test_bootstrap_returns_correct_shape():
    pnls = np.array([10.0, -5.0, 20.0, -10.0, 5.0])
    res = bootstrap_pnl(pnls, initial_equity=1000.0, n_iter=200, seed=42)
    assert res.final_equity.shape == (200,)
    assert res.max_drawdown_pct.shape == (200,)
    assert res.equity_paths is None


def test_bootstrap_store_paths_keeps_full_matrix():
    pnls = np.array([10.0, -5.0, 20.0])
    res = bootstrap_pnl(pnls, initial_equity=1000.0, n_iter=50, store_paths=True, seed=1)
    assert res.equity_paths is not None
    assert res.equity_paths.shape == (50, 4)
    # First column = initial equity for every path
    assert np.all(res.equity_paths[:, 0] == 1000.0)


def test_bootstrap_summary_keys_present():
    pnls = np.array([1.0, -1.0, 2.0, -2.0])
    res = bootstrap_pnl(pnls, initial_equity=100.0, n_iter=100, seed=0)
    for key in [
        "iterations",
        "initial_equity",
        "final_equity_mean",
        "final_equity_p5",
        "final_equity_p95",
        "max_dd_mean",
        "max_dd_p5",
        "max_dd_p95",
        "prob_drawdown_gt_20pct",
        "prob_loss",
    ]:
        assert key in res.summary


def test_bootstrap_seed_is_deterministic():
    pnls = np.array([5.0, -3.0, 7.0, -2.0])
    a = bootstrap_pnl(pnls, initial_equity=500.0, n_iter=500, seed=123)
    b = bootstrap_pnl(pnls, initial_equity=500.0, n_iter=500, seed=123)
    assert np.array_equal(a.final_equity, b.final_equity)


def test_bootstrap_handles_empty_pnls():
    res = bootstrap_pnl(np.array([]), initial_equity=1000.0, n_iter=10)
    assert res.final_equity.size == 1
    assert res.summary["iterations"] == 1


def test_bootstrap_zero_iterations():
    res = bootstrap_pnl(np.array([1.0, -1.0]), initial_equity=100.0, n_iter=0)
    assert res.final_equity.size == 1


def test_shock_pnl_produces_perturbations():
    pnls = np.array([10.0, -5.0, 8.0, -3.0])
    no_shock = shock_pnl(
        pnls, initial_equity=1000.0, slippage_std_pct=0.0, missed_fill_pct=0.0, n_iter=100, seed=7
    )
    expected_final = 1000.0 + pnls.sum()
    assert np.allclose(no_shock.final_equity, expected_final)


def test_shock_missed_fills_zero_some_trades():
    pnls = np.array([5.0] * 50)
    res = shock_pnl(
        pnls, initial_equity=1000.0, slippage_std_pct=0.0, missed_fill_pct=50.0, n_iter=300, seed=11
    )
    # ~50% missed → expected mean PnL ≈ 0.5 * 50 * 5 = 125
    mean_final = float(res.final_equity.mean())
    assert 1100.0 < mean_final < 1150.0


def test_shock_slippage_widens_distribution():
    pnls = np.array([5.0] * 30)
    base = shock_pnl(
        pnls, initial_equity=1000.0, slippage_std_pct=0.0, missed_fill_pct=0.0, n_iter=200, seed=21
    )
    perturbed = shock_pnl(
        pnls, initial_equity=1000.0, slippage_std_pct=10.0, missed_fill_pct=0.0, n_iter=200, seed=21
    )
    assert perturbed.final_equity.std() > base.final_equity.std()


def test_max_drawdown_is_non_negative_percent():
    pnls = np.array([10.0, -50.0, 5.0, 100.0, -10.0])
    res = bootstrap_pnl(pnls, initial_equity=1000.0, n_iter=100, seed=0)
    assert (res.max_drawdown_pct >= 0).all()
    assert (res.max_drawdown_pct <= 100).all()
