"""Batch parameter execution for vectorized sweeps (Roadmap v2 / S2).

`run_vectorized_batch` runs one strategy over many param-sets while amortizing
the per-run wrapper overhead that dominates optimizer sweeps.

Objective profiling (50k bars, EMA crossover, feature-cache ON) showed the
sweep cost breakdown per combo:
  - Numba inner loop:        0.23 ms   (3%)
  - engine total (reported): 2.16 ms   (31%)
  - Python wrapper overhead: 4.72 ms   (69%)  <-- the real target

The wrapper overhead is: look-ahead bias check (2x signal gen on a slice),
prepared-dataset handling, signal validation, per-run dict construction, and
strategy re-instantiation. A 2D `(bars, K)` Numba kernel would only attack the
3% inner loop — this batch attacks the 69% wrapper by hoisting the invariant
work out of the per-combo loop:

  - Look-ahead check: run ONCE for the strategy (it's strategy-level, not
    param-level), then `skip_lookahead_check=True` on every combo.
  - PreparedDataset: built ONCE, reused across all combos.
  - Feature bundle: reused via the engine-side feature cache (S1.5) when the
    strategy declares `feature_params()` and only execution params vary.

Capability contract: a strategy is batch-compatible iff it implements
`vectorized_signals` (every vectorized strategy does). No special marker is
required because batch produces bit-identical results to K single runs — it
only changes WHEN work happens, not WHAT. Strategies that are not
feature-cached still benefit from hoisting the look-ahead check and prepared
dataset; they just rebuild signals per combo (same as a single run would).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

import pandas as pd

from engine.execution.execution import ExecutionModel
from engine.execution.prepared_dataset import (
    PreparedDataset,
    prepare_vectorized_dataset,
)
from engine.execution.vectorized_engine import VectorizedResult, run_vectorized
from engine.models.models import AssetConfig

__all__ = ["run_vectorized_batch"]


def run_vectorized_batch(
    df: pd.DataFrame,
    strategy: Any,
    param_sets: Sequence[Dict[str, Any]],
    *,
    asset: AssetConfig,
    execution_model: Optional[ExecutionModel] = None,
    initial_equity: float = 100_000.0,
    risk_pct: float = 1.0,
    prepared: Optional[PreparedDataset] = None,
    summary_only: bool = True,
    compact_result: bool = True,
    include_equity_curve: bool = False,
) -> List[VectorizedResult]:
    """Run ``strategy`` over many ``param_sets`` with amortized wrapper cost.

    Hoists the invariant per-run work (look-ahead check, prepared dataset) out
    of the loop, then applies each param-set and runs the engine with
    ``skip_lookahead_check=True``. Results are bit-identical to calling
    :func:`run_vectorized` once per param-set — only the cost changes.

    Args:
        df: OHLCV frame.
        strategy: A single strategy instance, reused across param-sets via
            ``apply_params``. Must implement ``vectorized_signals``.
        param_sets: List of param-override dicts (one backtest per dict).
        asset: Asset config.
        execution_model: Execution model (spread/commission/slippage).
        initial_equity: Starting equity.
        risk_pct: Default per-trade risk.
        prepared: Optional pre-built PreparedDataset. Built once if omitted.
        summary_only / compact_result / include_equity_curve: Forwarded to the
            engine; defaults favor the optimizer fast path (no Trade objects,
            no equity curve).

    Returns:
        List of VectorizedResult, one per param-set, in the same order.

    Memory note: this batch keeps O(1) extra memory (one prepared dataset +
    one result at a time conceptually; results list is O(K) summaries). It does
    NOT allocate a (bars, K) array, so it scales to large K without blow-up —
    unlike a naive 2D-kernel batch.
    """
    if not hasattr(strategy, "vectorized_signals"):
        raise AttributeError(
            "Strategy does not implement vectorized_signals(df). "
            "run_vectorized_batch requires a vectorized strategy."
        )

    em = execution_model or ExecutionModel()

    # Hoist 1: build the prepared dataset ONCE (reused across all combos).
    prep = prepared or prepare_vectorized_dataset(df, asset=asset, execution_model=em)

    # Hoist 2: run the look-ahead bias check ONCE for the strategy. It is a
    # strategy-level property (does signal generation peek at the future?),
    # independent of execution params, so checking it per combo is pure waste.
    _run_lookahead_check_once(strategy, df)

    results: List[VectorizedResult] = []
    for params in param_sets:
        # Apply this combo's params onto the (single, reused) strategy instance.
        apply = getattr(strategy, "apply_params", None)
        if callable(apply):
            apply(params)
        else:  # pragma: no cover - strategies without the helper
            existing = getattr(strategy, "params", None)
            if isinstance(existing, dict):
                existing.update(params)

        res = run_vectorized(
            df,
            strategy,
            asset=asset,
            execution_model=em,
            initial_equity=initial_equity,
            risk_pct=risk_pct,
            prepared=prep,
            summary_only=summary_only,
            compact_result=compact_result,
            include_equity_curve=include_equity_curve,
            skip_lookahead_check=True,  # already checked once above
        )
        results.append(res)

    return results


def _run_lookahead_check_once(strategy: Any, df: pd.DataFrame) -> None:
    """Run the look-ahead bias heuristic a single time for the whole batch.

    Mirrors the check inside :func:`run_vectorized` but executes it once. Logs
    an error if past signals change when future bars are truncated (look-ahead
    bias). Swallows failures (e.g. strategies needing a long warmup) like the
    inline check does.
    """
    import logging

    import numpy as np

    logger = logging.getLogger("StrategyTester.engine.vectorized")
    n = len(df)
    if n <= 50:
        return
    try:
        test_size = min(1000, n)
        test_df = df.iloc[:test_size].copy()
        sig_full = strategy.vectorized_signals(test_df)["signal"].to_numpy()
        test_df_trunc = test_df.iloc[:-10].copy()
        sig_trunc = strategy.vectorized_signals(test_df_trunc)["signal"].to_numpy()
        if not np.array_equal(sig_full[: len(sig_trunc)], sig_trunc):
            logger.error(
                "🚨 CRITICAL WARNING: Look-ahead bias detected in vectorized_signals! "
                "Past signals changed when future bars were truncated. Batch results "
                "may be unreliable."
            )
    except Exception:
        pass  # Ignore heuristic failures (e.g. long-warmup strategies).
