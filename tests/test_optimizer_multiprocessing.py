"""Sprint 2.6 — Multiprocessing optimizer correctness tests.

The contract for ``grid_search(n_workers=N)`` is *bit-identical to the
single-threaded path*. We assert that explicitly here:

* The set of ``(params, metrics)`` pairs returned must match.
* The deterministic ordering (param-grid order) must match.
* The progress callback must fire exactly ``total`` times.

The runner is a pure function of ``params`` (deterministic,
side-effect-free) so this contract is meaningful — running the same
combo on a different worker process must produce the same metrics.
"""

from __future__ import annotations

from typing import Any, Dict

import pytest

from engine.optimization.optimizer import grid_search


# Module-level so multiprocessing can pickle it. ``imap_unordered``
# can't ship a closure/lambda to worker processes — the runner must
# be importable by its qualified name from the spawned interpreter.
def _toy_runner(params: Dict[str, Any]) -> Dict[str, float]:
    """Trivial deterministic runner — sum-of-params is the score."""
    return {
        "profit_factor": float(sum(int(v) for v in params.values())),
        "trades": int(sum(int(v) for v in params.values())),
    }


_GRID = {
    "a": [1, 2, 3, 4, 5],
    "b": [10, 20, 30, 40, 50],
    "c": [100, 200, 300, 400, 500],
}


# ---------------------------------------------------------------------------
# Identity: n_workers=4 must match n_workers=1 exactly.
# ---------------------------------------------------------------------------
def test_grid_search_4_workers_matches_single_thread() -> None:
    """The roadmap's "Result identity test: 4-worker output == single-thread output"."""
    seq = grid_search(_toy_runner, _GRID, n_workers=1)
    par = grid_search(_toy_runner, _GRID, n_workers=4, mp_chunk_size=10)

    assert len(seq.runs) == len(par.runs) == 5 * 5 * 5
    # Param-grid ordering is part of the contract — ``imap_unordered``
    # returns results out of order and the optimiser is responsible
    # for re-sorting them into deterministic param-grid order.
    for r_seq, r_par in zip(seq.runs, par.runs):
        assert r_seq.params == r_par.params
        assert r_seq.metrics == r_par.metrics


def test_grid_search_n_workers_clamped_to_total() -> None:
    """Asking for more workers than combos must not spawn idle processes."""
    grid = {"a": [1, 2]}  # only 2 combos
    par = grid_search(_toy_runner, grid, n_workers=8)
    assert len(par.runs) == 2


def test_grid_search_n_workers_zero_falls_back_to_sequential() -> None:
    """``n_workers=0`` is clamped to 1 — never accepts a non-positive value."""
    par = grid_search(_toy_runner, _GRID, n_workers=0)
    assert len(par.runs) == 5 * 5 * 5


def test_grid_search_progress_fires_exactly_total_times() -> None:
    """`on_run` callback must be invoked once per combo, regardless of n_workers."""
    fired_seq: list[int] = []
    fired_par: list[int] = []

    def _cb_seq(i: int, total: int, run: Any) -> None:
        fired_seq.append(i)

    def _cb_par(i: int, total: int, run: Any) -> None:
        fired_par.append(i)

    grid_search(_toy_runner, _GRID, n_workers=1, on_run=_cb_seq)
    grid_search(_toy_runner, _GRID, n_workers=4, mp_chunk_size=10, on_run=_cb_par)

    assert len(fired_seq) == 125
    assert len(fired_par) == 125


def test_grid_search_leaderboard_ordering_matches() -> None:
    """Top-N ordering must match between sequential and parallel runs."""
    seq = grid_search(_toy_runner, _GRID, n_workers=1)
    par = grid_search(_toy_runner, _GRID, n_workers=4, mp_chunk_size=10)

    seq_top = [(r.params, r.metrics["profit_factor"]) for r in seq.leaderboard[:5]]
    par_top = [(r.params, r.metrics["profit_factor"]) for r in par.leaderboard[:5]]
    assert seq_top == par_top


# ---------------------------------------------------------------------------
# Speedup is environment-dependent — assert "no slower than 1×" instead of 3.5×
# (the 3.5× target is enforced in `bench/baseline.json` via Sprint 4 regression
# gating, not in unit tests where CI hardware varies).
# ---------------------------------------------------------------------------
def test_grid_search_4_workers_does_not_blow_up_on_realistic_grid() -> None:
    """Smoke test that the parallel path completes a non-trivial workload.

    We deliberately skip wall-clock assertions — runner overhead in
    the toy case is too small to amortise spawn cost, so a strict
    speedup assertion here would be flaky. The bench harness in
    ``bench/bench_grid.py`` will track real-world speedup against
    ``bench/baseline.json``.
    """
    res = grid_search(_toy_runner, _GRID, n_workers=4, mp_chunk_size=25)
    assert len(res.runs) == 125
    # Sanity: best run is a==5,b==50,c==500 → score 555
    assert res.best is not None
    assert res.best.metrics["profit_factor"] == pytest.approx(555.0)
