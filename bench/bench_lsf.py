"""Benchmark the LSF (LinearStructureFVG) hot loop on a 200k-bar set.

Roadmap reference: Sprint 1, item 1.6.

Targets the close-only LSF primitives that the optimiser sweeps over
(``linear_pivots_close`` and ``linear_cisd_series_close``) — these are
the leaf functions all higher-level structure logic ultimately hits, so
a regression here cascades into every parameter-sweep job.

The script also runs the strategy end-to-end through the vectorised
engine to measure the realistic, full-stack time the user sees when
the *Run vectorised* button is clicked.
"""

from __future__ import annotations

import argparse

from bench._synthetic import synthetic_ohlcv
from bench._timer import time_call
from engine.core.linear import linear_cisd_series_close, linear_pivots_close
from engine.execution.execution import ExecutionModel
from engine.models.models import AssetConfig
from engine.execution.vectorized_engine import run_vectorized
from strategies.linear_structure_fvg import LinearStructureFVG


def _bench_asset() -> AssetConfig:
    return AssetConfig(
        name="BENCH",
        point_value=1.0,
        tick_size=0.25,
        buffer=0.0,
        max_lots_per_100k=10,
        currency="USD",
        min_lot=0.01,
        lot_step=0.01,
        spread_points=0.0,
        commission_per_lot=0.0,
        close_on_weekend=False,
        enforce_trading_hours=False,
    )


def measure(n_bars: int = 200_000, repeats: int = 3) -> dict:
    df = synthetic_ohlcv(n_bars=n_bars, pascal_case=True)
    close = df["Close"].to_numpy()

    pivots = time_call(lambda: linear_pivots_close(close, lookback=2), repeats=repeats)
    cisd = time_call(
        lambda: linear_cisd_series_close(close, lookback=2),
        repeats=repeats,
    )

    asset = _bench_asset()
    exec_model = ExecutionModel(profile="Optimistic")

    def _full_run() -> object:
        strat = LinearStructureFVG()
        return run_vectorized(
            df,
            strat,
            asset=asset,
            execution_model=exec_model,
            initial_equity=100_000.0,
            risk_pct=1.0,
        )

    full = time_call(_full_run, repeats=repeats)

    return {
        "n_bars": int(n_bars),
        "repeats": int(repeats),
        "linear_pivots_close": pivots,
        "linear_cisd_series_close": cisd,
        "lsf_run_vectorized": full,
    }


def main() -> dict:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--n-bars", type=int, default=200_000)
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    result = measure(n_bars=args.n_bars, repeats=args.repeats)
    print(result)
    return result


if __name__ == "__main__":
    main()
