"""Optimizer-facing runner that reuses one PreparedDataset across a sweep.

Roadmap v2 / S0.2 — the vectorized fast path (``prepared`` reuse +
``summary_only``) already exists in
:func:`engine.execution.vectorized_engine.run_vectorized`, but nothing in the
product wired it into :func:`engine.optimization.optimizer.grid_search`. This
module is that missing consumer.

Usage
-----
    from engine.optimization import grid_search
    from engine.optimization.vectorized_runner import make_vectorized_runner

    runner = make_vectorized_runner(
        df, strategy, asset=asset, execution_model=em,
    )
    result = grid_search(runner, {"sl_pts": [10, 20], "tp_pts": [20, 40]},
                         score_key="total_return_pct")

The :class:`PreparedDataset` is built **once** at factory time and reused for
every param combo, so each run skips the numpy-extract + mask-build stages.
Combined with ``summary_only`` (no ``Trade`` objects, no equity curve) this is
the S0 baseline win — no new algorithm, just feeding the engine the fast-path
flags it already understands.

Scoring note
------------
``summary_only`` returns only the keys produced without trade materialization:
``n_trades``, ``final_equity``, ``total_return_pct``, ``peak_drawdown_pct``,
``peak_drawdown_abs``. ``profit_factor`` is **not** available in this mode (it
needs per-trade gross profit/loss), so pick a ``score_key`` from the list above
when using this runner. If you need profit_factor-based ranking, run the
optimizer with a full (non-summary) runner instead.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, Optional

import pandas as pd

if TYPE_CHECKING:
    from engine.execution.execution import ExecutionModel
    from engine.execution.prepared_dataset import PreparedDataset

__all__ = ["VectorizedRunner", "make_vectorized_runner"]

# Metric keys available in summary_only mode (see module docstring).
SUMMARY_ONLY_KEYS = (
    "n_trades",
    "final_equity",
    "total_return_pct",
    "peak_drawdown_pct",
    "peak_drawdown_abs",
)


class VectorizedRunner:
    """Picklable ``runner(params) -> metrics`` for :func:`grid_search`.

    Holds the dataset + strategy + a pre-built :class:`PreparedDataset` and,
    on each call, applies the param overrides and runs the vectorized engine
    in summary-only fast mode. Reuses the prepared dataset across every call.

    Implemented as a class (not a closure) so it stays picklable for the
    ``n_workers > 1`` multiprocessing path. The held ``df``/``prepared`` are
    numpy/pandas objects, so the naive multiprocess path pickles them once per
    worker; the zero-copy ``shared_arrays`` path (S0.3) is layered separately.
    """

    def __init__(
        self,
        df: pd.DataFrame,
        strategy: Any,
        *,
        asset: Any,
        execution_model: Optional[ExecutionModel] = None,
        initial_equity: float = 100_000.0,
        risk_pct: float = 1.0,
        prepared: Optional[PreparedDataset] = None,
    ) -> None:
        from engine.execution.prepared_dataset import prepare_vectorized_dataset

        self.df = df
        self.strategy = strategy
        self.asset = asset
        self.execution_model = execution_model
        self.initial_equity = float(initial_equity)
        self.risk_pct = float(risk_pct)
        # Build the prepared dataset ONCE — this is the reuse boundary.
        self.prepared = prepared or prepare_vectorized_dataset(
            df, asset=asset, execution_model=execution_model
        )

    def __call__(self, params: Dict[str, Any]) -> Dict[str, float]:
        from engine.execution.vectorized_engine import run_vectorized

        # Apply param overrides onto the (single) strategy instance. In the
        # sequential path this mutates in place between combos, which is fine
        # because each run reads params fresh. In the multiprocess path each
        # worker owns its own unpickled copy.
        apply = getattr(self.strategy, "apply_params", None)
        if callable(apply):
            apply(params)
        else:  # pragma: no cover - strategies without the helper
            existing = getattr(self.strategy, "params", None)
            if isinstance(existing, dict):
                existing.update(params)

        result = run_vectorized(
            self.df,
            self.strategy,
            asset=self.asset,
            execution_model=self.execution_model,
            initial_equity=self.initial_equity,
            risk_pct=self.risk_pct,
            prepared=self.prepared,
            summary_only=True,
            compact_result=True,
            include_equity_curve=False,
        )
        # grid_search ranks on a numeric metrics dict; summary is exactly that.
        return dict(result.summary)


def make_vectorized_runner(
    df: pd.DataFrame,
    strategy: Any,
    *,
    asset: Any,
    execution_model: Optional[ExecutionModel] = None,
    initial_equity: float = 100_000.0,
    risk_pct: float = 1.0,
    prepared: Optional[PreparedDataset] = None,
) -> VectorizedRunner:
    """Build a :class:`VectorizedRunner` for a one-dataset parameter sweep.

    Convenience wrapper around the class constructor; see the class and module
    docstrings for the reuse semantics and the summary-only scoring caveat.
    """
    return VectorizedRunner(
        df,
        strategy,
        asset=asset,
        execution_model=execution_model,
        initial_equity=initial_equity,
        risk_pct=risk_pct,
        prepared=prepared,
    )
