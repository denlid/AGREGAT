"""Benchmark the (currently single-threaded) optimizer grid_search.

Roadmap reference: Sprint 1, item 1.6 (and Sprint 2, item 2.6 — once
multiprocessing lands the same harness measures the speed-up).

Uses a tiny synthetic ``runner`` so the benchmark exclusively measures
optimizer overhead, not strategy work. That keeps the baseline number
sensitive to the change Sprint 2 is going to make (multiprocessing
fan-out) without being drowned by per-trial variance.
"""

from __future__ import annotations

import argparse
import math
from typing import Any, Dict

from bench._timer import time_call
from engine.optimization.optimizer import grid_search


def _toy_runner(params: Dict[str, Any]) -> Dict[str, float]:
    """Deterministic toy fitness with non-trivial CPU work.

    Sprint 2.6 — to *measure* the multiprocessing speed-up we need
    each trial to do enough CPU work that the per-task pickle
    overhead (~µs per arg) is amortised. A pure ``sin/cos/abs`` call
    finishes in nanoseconds and would make the parallel path appear
    slower than sequential. The 200-iteration ``math.sin`` loop
    here keeps each run in the 10–50 ms range, which is the same
    order of magnitude as a real backtest call.
    """
    a = float(params["a"])
    b = float(params["b"])
    c = float(params["c"])
    acc = 0.0
    for i in range(20_000):
        acc += math.sin(a + i) * math.cos(b + i) * (abs(c) + i)
    return {"profit_factor": math.sin(a) + math.cos(b) + abs(c) + 0.0 * acc}


def measure(grid_n: int = 8, repeats: int = 3, n_workers: int = 4) -> dict:
    """Run a ``grid_n^3`` grid search both single-threaded and parallel.

    Default ``grid_n=8`` ⇒ 512 trials per run, which on a 4-core
    machine is short enough for CI but long enough that the sprint-2
    multiprocessing change shows up clearly in the diff.

    Returns timings for both ``grid_search`` (single-threaded) and
    ``grid_search_parallel`` (Sprint 2.6) so ``bench/run_all.py`` can
    compute the speed-up ratio against ``bench/baseline.json``.
    """
    grid = {
        "a": [i / max(1, grid_n - 1) for i in range(grid_n)],
        "b": [i / max(1, grid_n - 1) for i in range(grid_n)],
        "c": [i / max(1, grid_n - 1) for i in range(grid_n)],
    }
    total = grid_n**3

    def _do_grid_seq() -> object:
        return grid_search(_toy_runner, grid)

    def _do_grid_par() -> object:
        return grid_search(_toy_runner, grid, n_workers=n_workers)

    seq = time_call(_do_grid_seq, repeats=repeats)
    par = time_call(_do_grid_par, repeats=repeats)
    return {
        "trials": int(total),
        "grid_n": int(grid_n),
        "repeats": int(repeats),
        "n_workers": int(n_workers),
        "grid_search": seq,
        "grid_search_parallel": par,
        # Median is the metric the regression gate cares about
        # (see ``bench/baseline.json`` and Sprint 4 perf gating).
        "speedup_median": float(seq["median_s"] / par["median_s"]) if par["median_s"] > 0 else 0.0,
    }


def main() -> dict:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--grid-n", type=int, default=8)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument(
        "--n-workers",
        type=int,
        default=4,
        help="Number of parallel processes for the multiprocessing grid_search benchmark.",
    )
    args = parser.parse_args()
    result = measure(grid_n=args.grid_n, repeats=args.repeats, n_workers=args.n_workers)
    print(result)
    return result


if __name__ == "__main__":
    main()
