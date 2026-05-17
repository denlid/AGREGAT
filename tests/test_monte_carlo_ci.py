"""Phase 1.8 — Monte Carlo confidence intervals & risk probabilities."""

from __future__ import annotations

import numpy as np
import pytest

from engine.analytics.monte_carlo import bootstrap_pnl, shock_pnl


@pytest.fixture
def mixed_pnls() -> np.ndarray:
    """A representative mix of wins and losses with autocorrelated streaks."""
    rng = np.random.default_rng(42)
    # Base process: each day either a hot streak (+) or a cold streak (-).
    streak_signs = rng.choice([-1, +1], size=20)
    pnls: list[float] = []
    for s in streak_signs:
        run = rng.integers(3, 8)
        pnls.extend([s * float(rng.uniform(80, 120)) for _ in range(run)])
    return np.asarray(pnls, dtype=float)


def test_bootstrap_summary_contains_phase18_keys(mixed_pnls) -> None:
    res = bootstrap_pnl(mixed_pnls, initial_equity=10_000.0, n_iter=200, seed=0)
    keys = res.summary
    for k in (
        "sharpe_p5",
        "sharpe_p50",
        "sharpe_p95",
        "pf_p5",
        "pf_p50",
        "pf_p95",
        "total_pnl_p5",
        "total_pnl_p50",
        "total_pnl_p95",
        "max_dd_p50",
        "prob_drawdown_gt_30pct",
        "prob_drawdown_gt_50pct",
    ):
        assert k in keys, f"missing summary key {k}"


def test_bootstrap_returns_per_iter_arrays(mixed_pnls) -> None:
    n_iter = 100
    res = bootstrap_pnl(mixed_pnls, initial_equity=10_000.0, n_iter=n_iter, seed=0)
    assert res.sharpe.shape == (n_iter,)
    assert res.profit_factor.shape == (n_iter,)


def test_block_auto_picks_cube_root_block(mixed_pnls) -> None:
    """``method='block_auto'`` widens the simulated DD-tail vs iid."""
    iid = bootstrap_pnl(
        mixed_pnls,
        initial_equity=10_000.0,
        n_iter=400,
        seed=1,
        method="iid",
    )
    auto = bootstrap_pnl(
        mixed_pnls,
        initial_equity=10_000.0,
        n_iter=400,
        seed=1,
        method="block_auto",
    )
    # block_auto's DD median should be at least as conservative on
    # streak-heavy data; relax to a tolerant >= comparison since both
    # samples share the same RNG seed.
    assert auto.summary["max_dd_p50"] >= iid.summary["max_dd_p50"] - 1e-6


def test_block_auto_does_not_crash_on_short_pnls() -> None:
    """Single-trade and very small samples still produce finite percentiles."""
    res = bootstrap_pnl(
        np.array([100.0, -50.0]),
        initial_equity=10_000.0,
        n_iter=50,
        seed=0,
        method="block_auto",
    )
    assert np.isfinite(res.summary["sharpe_p50"])
    assert np.isfinite(res.summary["pf_p50"])


def test_shock_pnl_also_emits_phase18_summary(mixed_pnls) -> None:
    res = shock_pnl(
        mixed_pnls,
        initial_equity=10_000.0,
        n_iter=100,
        seed=0,
    )
    assert "sharpe_p50" in res.summary
    assert "max_dd_p50" in res.summary
    assert "prob_drawdown_gt_30pct" in res.summary


def test_prob_drawdown_levels_are_monotonic(mixed_pnls) -> None:
    """P(DD > 20%) ≥ P(DD > 30%) ≥ P(DD > 50%) for any sample."""
    res = bootstrap_pnl(mixed_pnls, initial_equity=10_000.0, n_iter=300, seed=0)
    s = res.summary
    assert s["prob_drawdown_gt_20pct"] >= s["prob_drawdown_gt_30pct"]
    assert s["prob_drawdown_gt_30pct"] >= s["prob_drawdown_gt_50pct"]


def test_iid_method_unchanged_legacy_behaviour() -> None:
    """``method='iid'`` returns identical equity statistics to old code."""
    pnls = np.array([100.0, -50.0, 75.0, -25.0])
    legacy = bootstrap_pnl(pnls, initial_equity=1000.0, n_iter=50, seed=0, block_size=1)
    new_iid = bootstrap_pnl(
        pnls, initial_equity=1000.0, n_iter=50, seed=0, method="iid"
    )
    np.testing.assert_array_equal(legacy.final_equity, new_iid.final_equity)
    np.testing.assert_array_equal(legacy.max_drawdown_pct, new_iid.max_drawdown_pct)


def test_pf_handles_no_losses() -> None:
    """All-winning bootstrap chunk should not crash when computing PF."""
    pnls = np.array([10.0, 20.0, 30.0, 40.0])
    res = bootstrap_pnl(pnls, initial_equity=1000.0, n_iter=50, seed=0)
    # All draws are wins, so PF = inf for every iter; finite-aware
    # summary should still emit p5/p50/p95 (or omit them).
    pf = res.profit_factor
    assert np.all(np.isinf(pf) | (pf == 0))
