"""
Strategy parameter optimizer.

Two search strategies are provided:

- :func:`grid_search` — exhaustive over the cartesian product of all parameter values
- :func:`random_search` — random samples drawn from each parameter list

Both accept a ``runner`` callable (so they're decoupled from BacktestEngine — easy to
test with a synthetic toy fitness function) plus a ``param_grid``::

    {"sl_pts": [10, 20, 30], "tp_pts": [20, 40, 60], "min_displacement": [5, 10]}

The runner is called as::

    metrics = runner({"sl_pts": 10, "tp_pts": 20, "min_displacement": 5})

and must return a dict containing at least the score key (default ``"profit_factor"``).

Returned :class:`OptimizationResult` exposes the sorted leaderboard plus the best params.
"""

from __future__ import annotations

import itertools
import logging
import multiprocessing as mp
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

from engine.optimization.shared_arrays import (
    SharedArrayBundle,
    pool_initializer,
)

logger = logging.getLogger("StrategyTester.engine.optimizer")

# Default chunk size for the multiprocessing path. Each worker pulls
# this many param combos at a time so the per-task pickling overhead
# is amortised across multiple runner calls. 50 is a sweet spot for
# typical AGREGAT runners (~50–200ms each); workloads with much
# faster or slower individual runs may want to override via
# ``mp_chunk_size``.
_DEFAULT_MP_CHUNK_SIZE = 50


@dataclass(frozen=True)
class OptimizationRun:
    """A single (params, metrics) pair from one optimizer iteration."""

    params: Dict[str, Any]
    metrics: Dict[str, float]


@dataclass
class OptimizationResult:
    """Sortable leaderboard returned by every optimizer entrypoint."""

    runs: List[OptimizationRun] = field(default_factory=list)
    score_key: str = "profit_factor"
    higher_is_better: bool = True

    @property
    def leaderboard(self) -> List[OptimizationRun]:
        """Return runs sorted by ``score_key`` (best first)."""

        def keyfn(r: OptimizationRun) -> float:
            v = r.metrics.get(
                self.score_key, float("-inf") if self.higher_is_better else float("inf")
            )
            return (
                float(v)
                if not (isinstance(v, float) and np.isnan(v))
                else (float("-inf") if self.higher_is_better else float("inf"))
            )

        return sorted(self.runs, key=keyfn, reverse=self.higher_is_better)

    @property
    def best(self) -> Optional[OptimizationRun]:
        lb = self.leaderboard
        return lb[0] if lb else None


# ----------------------------------------------------------------------------
# Grid search
# ----------------------------------------------------------------------------
def _run_one(
    args: Tuple[
        int, Dict[str, Any], Callable[[Dict[str, Any]], Dict[str, float]], str, bool
    ],
) -> Tuple[int, OptimizationRun]:
    """Top-level worker function — must be importable so ``Pool`` can pickle it.

    ``args`` is a 5-tuple: ``(idx, params, runner, score_key, higher_is_better)``.
    Returns ``(idx, OptimizationRun)`` so the main process can re-order
    the worker output back into deterministic param-grid order — that's
    what makes the multiprocessing path bit-identical to the sequential
    path.
    """
    idx, params, runner, score_key, higher_is_better = args
    try:
        metrics = runner(params)
    except Exception as e:
        logger.warning(f"Optimizer run #{idx} failed for {params}: {e!r}")
        metrics = {score_key: float("-inf") if higher_is_better else float("inf")}
    return idx, OptimizationRun(params=params, metrics=metrics)


def grid_search(
    runner: Callable[[Dict[str, Any]], Dict[str, float]],
    param_grid: Dict[str, Sequence[Any]],
    *,
    score_key: str = "profit_factor",
    higher_is_better: bool = True,
    on_run: Optional[Callable[[int, int, OptimizationRun], None]] = None,
    n_workers: int = 1,
    mp_chunk_size: int = _DEFAULT_MP_CHUNK_SIZE,
    trace_id: Optional[str] = None,
    shared_arrays: Optional[SharedArrayBundle] = None,
) -> OptimizationResult:
    """Exhaustive grid search over ``param_grid``.

    Parameters
    ----------
    runner
        Callable that takes a params dict and returns a metrics dict.
        For ``n_workers > 1`` it must be picklable (top-level functions
        and classes work; lambdas and locally defined closures don't).
    param_grid
        ``{name: [values, ...]}`` mapping; the cartesian product is iterated.
    score_key
        Metric used to rank runs. Default ``"profit_factor"``.
    higher_is_better
        Sort direction.
    on_run
        Optional progress callback ``(idx, total, run)`` — useful for GUI dialogs.
    n_workers
        Number of parallel processes. ``1`` (default) uses the
        single-threaded path; ``>1`` distributes combos across a
        ``multiprocessing.Pool`` with ``imap_unordered`` for streaming
        progress. Sprint 2.6: target ≥3.5× speedup at ``n_workers=4``.
    mp_chunk_size
        Combos per worker batch in the multiprocessing path. Larger
        values amortise pickling overhead but reduce streaming
        granularity for ``on_run``.
    shared_arrays
        Phase 2.5 — optional :class:`SharedArrayBundle` whose NumPy
        arrays are attached zero-copy in every worker via the pool
        initializer. Eliminates the per-worker DataFrame copy that
        drove ``n_workers × df.nbytes`` RAM cost (≈1.9 GB on 8
        workers × 5 M bars × 6 cols). The runner reads its arrays via
        :func:`engine.optimization.shared_arrays.attached` keyed by
        the bundle name.
    """
    # Sprint 3.7 — structured logging with trace-id propagation.
    from engine.logging_config import TraceContextAdapter, new_trace_id

    _tid = trace_id or new_trace_id()
    _log = TraceContextAdapter(logger, trace_id=_tid)

    keys = list(param_grid.keys())
    values_lists = [list(param_grid[k]) for k in keys]
    if not keys:
        return OptimizationResult(
            score_key=score_key, higher_is_better=higher_is_better
        )

    combos = list(itertools.product(*values_lists))
    total = len(combos)
    _log.info("grid_search: %d combos, n_workers=%d", total, n_workers)

    # Clamp n_workers to [1, total] — asking for more workers than
    # combos just spawns idle processes.
    n_workers = max(1, min(int(n_workers), total))

    if n_workers == 1:
        runs: List[OptimizationRun] = []
        for i, combo in enumerate(combos):
            params = dict(zip(keys, combo))
            try:
                metrics = runner(params)
            except Exception as e:
                logger.warning(
                    f"Optimizer run {i + 1}/{total} failed for {params}: {e!r}"
                )
                metrics = {
                    score_key: float("-inf") if higher_is_better else float("inf")
                }
            run = OptimizationRun(params=params, metrics=metrics)
            runs.append(run)
            if on_run is not None:
                on_run(i + 1, total, run)
        return OptimizationResult(
            runs=runs, score_key=score_key, higher_is_better=higher_is_better
        )

    # Multiprocessing path. We index every combo so worker output can
    # be re-ordered into deterministic param-grid order even though
    # ``imap_unordered`` returns results as workers finish.
    payload = [
        (i, dict(zip(keys, combo)), runner, score_key, higher_is_better)
        for i, combo in enumerate(combos)
    ]
    indexed_runs: List[Optional[OptimizationRun]] = [None] * total
    completed = 0
    # ``"spawn"`` start method is the safest default across platforms
    # — fork is unsafe on macOS 10.15+ and broken in some Qt setups.
    ctx = mp.get_context("spawn")
    if shared_arrays is not None:
        # Each worker attaches the shared blocks once via the pool
        # initializer; the runner is expected to call
        # ``shared_arrays.attached(bundle_name)`` to read them.
        pool_kwargs = dict(
            initializer=pool_initializer,
            initargs=(shared_arrays.descriptor(),),
        )
    else:
        pool_kwargs = {}
    with ctx.Pool(processes=n_workers, **pool_kwargs) as pool:
        for idx, run in pool.imap_unordered(_run_one, payload, chunksize=mp_chunk_size):
            indexed_runs[idx] = run
            completed += 1
            if on_run is not None:
                # We pass the *completion order* index so the GUI
                # progress bar advances monotonically; the run itself
                # carries its true param-grid index via run.params.
                on_run(completed, total, run)

    runs = [r for r in indexed_runs if r is not None]
    return OptimizationResult(
        runs=runs, score_key=score_key, higher_is_better=higher_is_better
    )


# ----------------------------------------------------------------------------
# Random search
# ----------------------------------------------------------------------------
def random_search(
    runner: Callable[[Dict[str, Any]], Dict[str, float]],
    param_grid: Dict[str, Sequence[Any]],
    *,
    n_iter: int = 30,
    score_key: str = "profit_factor",
    higher_is_better: bool = True,
    seed: Optional[int] = None,
    on_run: Optional[Callable[[int, int, OptimizationRun], None]] = None,
) -> OptimizationResult:
    """Random search over ``param_grid`` — draws ``n_iter`` samples (with replacement)."""
    keys = list(param_grid.keys())
    if not keys:
        return OptimizationResult(
            score_key=score_key, higher_is_better=higher_is_better
        )

    rng = np.random.default_rng(seed)
    runs: List[OptimizationRun] = []
    for i in range(int(n_iter)):
        params = {k: rng.choice(np.asarray(param_grid[k], dtype=object)) for k in keys}
        # rng.choice returns numpy scalars — coerce to Python types for clean dicts.
        params = {k: (v.item() if hasattr(v, "item") else v) for k, v in params.items()}
        try:
            metrics = runner(params)
        except Exception as e:
            logger.warning(f"Optimizer run {i + 1}/{n_iter} failed for {params}: {e!r}")
            metrics = {score_key: float("-inf") if higher_is_better else float("inf")}
        run = OptimizationRun(params=params, metrics=metrics)
        runs.append(run)
        if on_run is not None:
            on_run(i + 1, int(n_iter), run)
    return OptimizationResult(
        runs=runs, score_key=score_key, higher_is_better=higher_is_better
    )


__all__ = [
    "OptimizationRun",
    "OptimizationResult",
    "grid_search",
    "random_search",
]
