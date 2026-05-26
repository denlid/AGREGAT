"""Sprint 1 / item 1.5 — bit-identical replay contract.

Two consecutive runs of the same strategy on the same dataset, with the
same costs / asset / seed, must produce **bit-identical** trade lists,
equity curves and summary statistics. This holds for both the visual
:class:`BacktestCore` path and the vectorised :func:`run_vectorized`
path.

The test is the single guard rail that lets every later sprint (MC,
optimizer, walk-forward, AI advisor) trust the engine's output: if any
of those components introduce a non-deterministic source — system
clock, unseeded :pymod:`numpy.random`, dictionary iteration order,
floating-point summation reorder — exactly one of these assertions
will fail with a tight diff.

Notes
-----
* "Bit-identical" here means **identical Python ints / floats / strings /
  numpy arrays**. We do not compare across the visual ↔ vectorised
  boundary (that is the parity contract, owned by
  ``test_dual_engine_parity.py``). We only assert that *each engine on
  its own* is deterministic.
* The strategies used are the bundled :file:`sma_crossover.py` and
  :file:`donchian_breakout.py`, mirroring the parity test fixtures so
  the two suites stay in lock-step.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from engine.strategy.context import StrategyLoader
from engine.core.backtest_core import BacktestCore
from engine.execution.execution import ExecutionModel
from engine.models.models import AssetConfig, Trade
from engine.execution.vectorized_engine import run_vectorized


# ------------------------------------------------------------------- fixtures
def _synthetic_df(n_bars: int = 400, *, seed: int = 11) -> pd.DataFrame:
    """OHLCV frame designed to fire entries and exits in both directions.

    Uses a fixed seed so the determinism assertion compares two runs of
    the *same* dataset; if you need to change the dataset shape, update
    the seed in lock-step with the parity fixture in
    ``test_dual_engine_parity.py``.
    """
    rng = np.random.default_rng(seed)
    drift = np.linspace(0, 30, n_bars)
    sine = 8 * np.sin(np.linspace(0, 6 * np.pi, n_bars))
    noise = rng.normal(0, 1.0, n_bars).cumsum()
    close = 100 + drift + sine + noise
    high = close + rng.uniform(0.2, 1.0, n_bars)
    low = close - rng.uniform(0.2, 1.0, n_bars)
    open_ = np.concatenate(([close[0]], close[:-1]))
    times = pd.date_range("2024-01-02 09:30", periods=n_bars, freq="1min")
    return pd.DataFrame(
        {
            "time": times,
            "Open": open_,
            "High": high,
            "Low": low,
            "Close": close,
            "Volume": 1.0,
        }
    )


def _df_lowercase(n_bars: int = 400, *, seed: int = 11) -> pd.DataFrame:
    """Visual engine consumes lowercase column names."""
    df = _synthetic_df(n_bars, seed=seed)
    return df.rename(
        columns={
            "Open": "open",
            "High": "high",
            "Low": "low",
            "Close": "close",
            "Volume": "volume",
        }
    )


def _det_asset() -> AssetConfig:
    """Determinism asset config — costs zeroed, gating disabled."""
    return AssetConfig(
        name="DETERM",
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


def _trade_tuple(t: Trade) -> tuple:
    """Reduce a :class:`Trade` to the comparable fields.

    ``id`` is intentionally excluded because the visual engine increments
    it through GUI render counters that are stateful across the test
    process. We pin everything else: direction, indices, prices, PnL,
    lot, MAE/MFE, and exit reason.
    """
    return (
        t.direction,
        int(t.entry_index),
        int(t.exit_index) if t.exit_index is not None else -1,
        float(t.entry_price),
        float(t.exit_price),
        float(t.pnl),
        float(t.volume),
        float(t.mae),
        float(t.mfe),
        t.exit_reason,
    )


# ------------------------------------------------------------------- helpers
def _run_visual(strategy_path: str, df: pd.DataFrame) -> BacktestCore:
    """Drive ``BacktestCore.step_n`` and force-close any open position."""
    eng = BacktestCore()
    eng.asset_config = _det_asset()
    eng.execution_model = ExecutionModel(profile="Optimistic")
    eng.load_data(df)
    eng.load_strategy(StrategyLoader.load(strategy_path))
    eng.step_n(len(df) + 5)
    if eng.position is not None and eng.total_bars > 0:
        last = eng.candle_at(eng.total_bars - 1)
        eng.execute_close(reason="EOD", exit_candle=last)
    return eng


def _run_vec(strategy_path: str, df: pd.DataFrame):
    """Drive :func:`run_vectorized` with the determinism asset."""
    return run_vectorized(
        df,
        StrategyLoader.load(strategy_path),
        asset=_det_asset(),
        execution_model=ExecutionModel(profile="Optimistic"),
        initial_equity=100_000.0,
        risk_pct=1.0,
    )


# --------------------------------------------------------------------- tests
@pytest.mark.parametrize(
    "strategy_module",
    [
        "strategies/sma_crossover.py",
        "strategies/donchian_breakout.py",
    ],
)
def test_visual_engine_two_consecutive_runs_are_bit_identical(
    strategy_module: str,
) -> None:
    """``BacktestCore.run`` is deterministic across two back-to-back
    runs on the same dataset — every trade, equity sample and PnL
    figure matches bit-for-bit.

    If this fails the regression is almost certainly an unseeded random
    source or a mutating module-level cache; the strategy module is in
    the test name so triage is one ``git blame`` away.
    """
    df = _df_lowercase()
    a = _run_visual(strategy_module, df)
    b = _run_visual(strategy_module, df)

    assert len(a.trades) == len(b.trades), (
        f"{strategy_module}: trade count drifted between runs ({len(a.trades)} vs {len(b.trades)})"
    )
    for i, (ta, tb) in enumerate(zip(a.trades, b.trades)):
        assert _trade_tuple(ta) == _trade_tuple(tb), (
            f"{strategy_module}: trade #{i} drifted between runs:\n"
            f"  run-A={_trade_tuple(ta)}\n"
            f"  run-B={_trade_tuple(tb)}"
        )

    assert float(a.equity) == float(b.equity), (
        f"{strategy_module}: final equity drifted ({a.equity!r} vs {b.equity!r})"
    )
    assert a.max_drawdown_pct == b.max_drawdown_pct, (
        f"{strategy_module}: max DD% drifted ({a.max_drawdown_pct!r} vs {b.max_drawdown_pct!r})"
    )


@pytest.mark.parametrize(
    "strategy_module",
    [
        "strategies/sma_crossover.py",
        "strategies/donchian_breakout.py",
    ],
)
def test_vectorized_engine_two_consecutive_runs_are_bit_identical(
    strategy_module: str,
) -> None:
    """:func:`run_vectorized` is deterministic across two back-to-back
    runs on the same dataset — equity arrays match exactly and every
    trade tuple matches bit-for-bit.

    Equity curves are compared with :func:`numpy.array_equal` (no
    tolerance) so any FP-summation reorder triggers an immediate red.
    """
    df = _synthetic_df()
    a = _run_vec(strategy_module, df)
    b = _run_vec(strategy_module, df)

    assert len(a.trades) == len(b.trades), (
        f"{strategy_module}: vectorised trade count drifted ({len(a.trades)} vs {len(b.trades)})"
    )
    for i, (ta, tb) in enumerate(zip(a.trades, b.trades)):
        assert _trade_tuple(ta) == _trade_tuple(tb), (
            f"{strategy_module}: vectorised trade #{i} drifted:\n"
            f"  run-A={_trade_tuple(ta)}\n"
            f"  run-B={_trade_tuple(tb)}"
        )

    assert np.array_equal(a.equity_curve, b.equity_curve), (
        f"{strategy_module}: vectorised equity curve drifted between runs"
    )
    assert a.peak_drawdown_pct == b.peak_drawdown_pct
    assert a.peak_drawdown_abs == b.peak_drawdown_abs
    assert a.summary == b.summary, (
        f"{strategy_module}: vectorised summary dict drifted between runs"
    )
