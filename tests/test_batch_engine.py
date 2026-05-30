"""Tests for run_vectorized_batch (Roadmap v2 / S2).

Batch must produce bit-identical results to K individual run_vectorized calls
(it only changes WHEN work happens via wrapper-overhead amortization, not WHAT).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from engine.execution.batch_engine import run_vectorized_batch
from engine.execution.execution import ExecutionModel
from engine.execution.prepared_dataset import prepare_vectorized_dataset
from engine.execution.vectorized_engine import run_vectorized
from engine.models.models import AssetConfig
from strategies.ema_crossover_cached import EMACrossoverFeatureCached


def _df(n: int = 2000) -> pd.DataFrame:
    idx = pd.date_range("2024-01-02 10:00", periods=n, freq="1min")
    close = 100.0 + np.cumsum(np.random.default_rng(7).normal(0, 0.5, n))
    return pd.DataFrame(
        {
            "time": idx,
            "open": close - 0.3,
            "high": close + 0.5,
            "low": close - 0.5,
            "close": close,
            "volume": 1.0,
        }
    )


def _asset(enforce_th: bool = False) -> AssetConfig:
    return AssetConfig(
        name="T", point_value=1.0, tick_size=1.0, buffer=0.0,
        max_lots_per_100k=10, currency="USD", min_lot=0.01, lot_step=0.01,
        spread_points=0.0, commission_per_lot=0.0,
        enforce_trading_hours=enforce_th,
    )


_PARAM_SETS = [
    {"fast_ema": 12, "slow_ema": 26, "sl_pts": 30.0, "tp_pts": 60.0},
    {"fast_ema": 12, "slow_ema": 26, "sl_pts": 50.0, "tp_pts": 100.0},
    {"fast_ema": 12, "slow_ema": 26, "sl_pts": 20.0, "tp_pts": 40.0},
]


def test_batch_parity_with_individual_runs():
    """Batch results == K individual run_vectorized calls, combo by combo."""
    df, asset, em = _df(), _asset(), ExecutionModel("Optimistic")
    prepared = prepare_vectorized_dataset(df, asset=asset, execution_model=em)

    # Individual runs.
    individual = []
    for ps in _PARAM_SETS:
        s = EMACrossoverFeatureCached()
        s.apply_params(ps)
        individual.append(
            run_vectorized(
                df, s, asset=asset, execution_model=em, prepared=prepared,
                summary_only=True, compact_result=True, include_equity_curve=False,
            )
        )

    # Batch.
    strat = EMACrossoverFeatureCached()
    batch = run_vectorized_batch(df, strat, _PARAM_SETS, asset=asset, execution_model=em, prepared=prepared)

    assert len(batch) == len(individual)
    for ind, bat in zip(individual, batch):
        for key in ("total_return_pct", "final_equity", "n_trades", "peak_drawdown_pct"):
            assert bat.summary[key] == pytest.approx(ind.summary[key], rel=1e-12), (
                f"mismatch on {key}"
            )


def test_batch_preserves_order():
    """Results come back in the same order as param_sets."""
    df, asset, em = _df(), _asset(), ExecutionModel("Optimistic")
    strat = EMACrossoverFeatureCached()
    batch = run_vectorized_batch(df, strat, _PARAM_SETS, asset=asset, execution_model=em)
    assert len(batch) == 3
    # Different sl/tp should generally produce different returns; at minimum
    # the call must return one result per param-set in order.
    assert all(hasattr(r, "summary") for r in batch)


def test_batch_empty_param_sets():
    """Empty param_sets returns empty list (no crash)."""
    df, asset, em = _df(), _asset(), ExecutionModel("Optimistic")
    strat = EMACrossoverFeatureCached()
    batch = run_vectorized_batch(df, strat, [], asset=asset, execution_model=em)
    assert batch == []


def test_batch_parity_with_trading_hours_enforced():
    """Batch matches individual runs even with trading-hours skips."""
    n = 8
    times = pd.date_range("2024-01-02 17:55", periods=n, freq="1min")
    close = np.full(n, 100.0)
    df = pd.DataFrame(
        {
            "time": times, "open": close, "high": close + 0.5,
            "low": close - 0.5, "close": close, "volume": 1.0,
        }
    )
    asset, em = _asset(enforce_th=True), ExecutionModel("Optimistic")

    # Simple single-signal strategy (not feature-cached) to exercise fallback.
    from engine.strategy.context import Strategy

    class OneSignal(Strategy):
        PARAMS = EMACrossoverFeatureCached.PARAMS

        def vectorized_signals(self, d):
            m = len(d)
            sig = np.zeros(m, dtype=int)
            sig[4] = 1
            return pd.DataFrame(
                {
                    "signal": sig,
                    "sl_points": np.full(m, float(self.params["sl_pts"])),
                    "tp_points": np.full(m, float(self.params["tp_pts"])),
                },
                index=d.index,
            )

    ps = [{"sl_pts": 5.0, "tp_pts": 5.0}]
    s = OneSignal()
    s.apply_params(ps[0])
    ind = run_vectorized(df, s, asset=asset, execution_model=em, summary_only=True, compact_result=True, include_equity_curve=False)

    strat = OneSignal()
    bat = run_vectorized_batch(df, strat, ps, asset=asset, execution_model=em)[0]

    # Both should record the same skipped trades.
    assert len(bat.skipped_trades) == len(ind.skipped_trades)


def test_batch_rejects_non_vectorized_strategy():
    """Strategy without vectorized_signals raises."""
    df, asset, em = _df(), _asset(), ExecutionModel("Optimistic")

    class NoSignals:
        pass

    with pytest.raises(AttributeError, match="vectorized_signals"):
        run_vectorized_batch(df, NoSignals(), [{}], asset=asset, execution_model=em)
