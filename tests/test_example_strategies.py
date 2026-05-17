"""Smoke tests for the bundled example strategies.

These guarantees are user-visible: when the user opens AGREGAT and picks
the bundled :mod:`strategies.sma_crossover` or
:mod:`strategies.donchian_breakout` from the file picker, both engines
must run without raising and produce a sensible signal frame.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from engine.strategy.context import StrategyLoader, has_vectorized_signals
from engine.core.backtest_core import BacktestCore
from engine.execution.execution import ExecutionModel
from engine.models.models import AssetConfig
from engine.execution.vectorized_engine import run_vectorized


# ----------------------------------------------------------------- fixture
def _synthetic_df(n_bars: int = 600, *, seed: int = 1) -> pd.DataFrame:
    """Generate a synthetic OHLCV frame with enough non-trivial moves
    that both crossover and breakout strategies actually take trades.

    The walk has a deterministic upward drift so the breakout strategy
    sees plenty of long entries; the SMA strategy oscillates and triggers
    in both directions.
    """
    rng = np.random.default_rng(seed)
    # Random walk with a small positive drift overlaid with a slow
    # sine wave to ensure crossovers happen.
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


def _test_asset() -> AssetConfig:
    return AssetConfig(
        name="EXAMPLE",
        point_value=1.0,
        tick_size=0.25,
        buffer=0.0,
        max_lots_per_100k=10,
        currency="USD",
        min_lot=0.01,
        lot_step=0.01,
        spread_points=0.5,
        commission_per_lot=0.0,
    )


def _exec_model(asset: AssetConfig) -> ExecutionModel:
    return ExecutionModel()


@pytest.mark.parametrize(
    "strategy_module",
    [
        "strategies/sma_crossover.py",
        "strategies/donchian_breakout.py",
    ],
)
def test_example_strategy_loads_and_runs_vectorized(strategy_module: str) -> None:
    """Each bundled strategy must load via the user-facing
    :pyfunc:`StrategyLoader.load` and produce a valid signal frame for
    the vectorized engine.
    """
    strat = StrategyLoader.load(strategy_module)
    assert has_vectorized_signals(strat), f"{strategy_module} should expose vectorized_signals"

    df = _synthetic_df()
    signals = strat.vectorized_signals(df)
    assert {"signal", "sl_points", "tp_points"}.issubset(signals.columns)
    assert len(signals) == len(df)
    # We should see at least one of each direction with this data.
    assert (signals["signal"] != 0).any(), "strategy never emits a trade"

    asset = _test_asset()
    result = run_vectorized(
        df,
        strat,
        asset=asset,
        execution_model=_exec_model(asset),
        initial_equity=100_000.0,
        risk_pct=1.0,
    )
    # Equity curve always starts at the initial equity.
    assert np.isclose(result.equity_curve[0], 100_000.0)
    # The synthetic data should produce *some* trades through both
    # strategies — if not, the strategy is silent and shipping it would
    # confuse the user.
    assert len(result.trades) > 0, f"{strategy_module} produced zero trades on synthetic data"


def test_example_strategies_have_on_candle() -> None:
    """Both bundled strategies must also implement the visual
    ``on_candle`` API so the user can open them via Run-to-End and watch
    the chart fill in bar by bar.
    """
    for path in ("strategies/sma_crossover.py", "strategies/donchian_breakout.py"):
        strat = StrategyLoader.load(path)
        assert callable(getattr(strat, "on_candle", None)), (
            f"{path} should implement on_candle for the visual engine"
        )


# ---------------------------------------------------------------------------
# Visual engine end-to-end smoke
# ---------------------------------------------------------------------------
def _df_with_lowercase_cols(n_bars: int = 600, *, seed: int = 1) -> pd.DataFrame:
    """Same shape as :func:`_synthetic_df` but with the lowercase OHLCV
    column names that ``BacktestCore.load_data`` expects (it copies the
    DataFrame into NumPy columns ``open/high/low/close/volume``).
    """
    df = _synthetic_df(n_bars, seed=seed)
    return df.rename(
        columns={"Open": "open", "High": "high", "Low": "low", "Close": "close", "Volume": "volume"}
    )


@pytest.mark.parametrize(
    "strategy_module",
    [
        "strategies/sma_crossover.py",
        "strategies/donchian_breakout.py",
    ],
)
def test_example_strategy_runs_through_visual_engine(strategy_module: str) -> None:
    """End-to-end smoke for the iterative path: load the strategy,
    drive it through the entire frame via :py:meth:`BacktestCore.step_n`,
    and assert the engine reaches the last bar without raising.

    This catches regressions in the ``ctx.data`` migration that the
    vectorised-only smoke test cannot — e.g. accidental access to
    ``ctx.history`` semantics that no longer hold after Etap 3 moved
    ``current_idx`` advancement before the strategy hook.
    """
    strat = StrategyLoader.load(strategy_module)
    df = _df_with_lowercase_cols(n_bars=400)
    eng = BacktestCore()
    eng.asset_config = _test_asset()
    eng.execution_model = ExecutionModel(profile="Optimistic")
    eng.execution_model.spread_override = 0.0
    eng.execution_model.commission_override = 0.0
    eng.execution_model.slippage_override = 0.0
    eng.load_data(df)
    eng.load_strategy(strat)

    finished_calls: list[bool] = []
    eng.on_simulation_finished.append(lambda: finished_calls.append(True))

    eng.step_n(len(df) + 5)

    assert finished_calls == [True], "engine never fired on_simulation_finished"
    assert eng.current_idx == len(df) - 1
    # The synthetic frame is built so each strategy must take at least
    # one trade over 400 bars.
    assert len(eng.trades) > 0, f"{strategy_module} produced zero trades through the visual engine"
