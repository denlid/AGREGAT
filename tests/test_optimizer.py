"""Grid + random-search optimizer (decoupled from BacktestEngine)."""

from __future__ import annotations

import math

import pytest

from engine.optimization.optimizer import grid_search, random_search


def _toy_runner(params):
    """Toy fitness: profit_factor peaks at sl=20, tp=40."""
    sl = params["sl"]
    tp = params["tp"]
    score = 5.0 - abs(sl - 20) * 0.05 - abs(tp - 40) * 0.05
    return {"profit_factor": float(score), "trades": int(sl + tp)}


def test_grid_search_iterates_full_cartesian_product():
    grid = {"sl": [10, 20, 30], "tp": [20, 40, 60]}
    res = grid_search(_toy_runner, grid)
    assert len(res.runs) == 9


def test_grid_search_finds_known_optimum():
    grid = {"sl": [10, 15, 20, 25, 30], "tp": [20, 30, 40, 50, 60]}
    res = grid_search(_toy_runner, grid)
    assert res.best is not None
    assert res.best.params == {"sl": 20, "tp": 40}


def test_grid_search_progress_callback():
    seen = []
    grid = {"sl": [10, 20], "tp": [20, 40]}
    grid_search(_toy_runner, grid, on_run=lambda i, n, r: seen.append((i, n)))
    assert seen == [(1, 4), (2, 4), (3, 4), (4, 4)]


def test_random_search_respects_n_iter():
    grid = {"sl": [10, 20, 30], "tp": [20, 40, 60]}
    res = random_search(_toy_runner, grid, n_iter=15, seed=0)
    assert len(res.runs) == 15


def test_random_search_seed_is_deterministic():
    grid = {"sl": [10, 20, 30, 40, 50], "tp": [20, 40, 60, 80, 100]}
    a = random_search(_toy_runner, grid, n_iter=20, seed=42)
    b = random_search(_toy_runner, grid, n_iter=20, seed=42)
    assert [r.params for r in a.runs] == [r.params for r in b.runs]


def test_optimizer_handles_failing_runs_gracefully():
    def flaky_runner(params):
        if params["sl"] == 30:
            raise RuntimeError("simulated failure")
        return {"profit_factor": float(params["sl"])}

    grid = {"sl": [10, 20, 30, 40]}
    res = grid_search(flaky_runner, grid)
    assert len(res.runs) == 4
    # Failed run should rank last (worst).
    failing = next(r for r in res.runs if r.params["sl"] == 30)
    assert failing.metrics["profit_factor"] == float("-inf")
    # Best should still be identifiable.
    assert res.best.params["sl"] == 40


def test_optimizer_minimisation_mode():
    def loss_runner(params):
        return {"loss": (params["x"] - 5) ** 2}

    grid = {"x": [1, 3, 5, 7, 9]}
    res = grid_search(loss_runner, grid, score_key="loss", higher_is_better=False)
    assert res.best.params == {"x": 5}


def test_empty_grid_returns_empty_result():
    res = grid_search(_toy_runner, {})
    assert res.runs == []
    assert res.best is None


def test_leaderboard_is_sorted_descending_by_default():
    grid = {"sl": [10, 20, 30, 40, 50], "tp": [40]}
    res = grid_search(_toy_runner, grid)
    scores = [r.metrics["profit_factor"] for r in res.leaderboard]
    assert scores == sorted(scores, reverse=True)
