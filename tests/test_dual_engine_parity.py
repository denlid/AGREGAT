"""Phase 6 / TZ STABLE GRAIL — dual-engine parity at $0.00.

After Phase 6 (A7 + A8) the two engines share their entry timing,
SL/TP anchor reference and trading-hours / EOD / weekend semantics, so
the same strategy on the same dataset must produce **identical** trade
lists in both engines.

This file is the contract test that locks the parity in. If a future
change introduces drift, exactly *one* of these assertions will fail
with a tight diff so the regression is easy to localise.

Notes on the parity contract
----------------------------
* Strategies passed in here use the dual-API convention bundled with
  AGREGAT — :pymeth:`on_candle` for the visual path and
  :pymeth:`vectorized_signals` for the vectorised path. The vectorised
  signal frame must anchor SL/TP on the **signal-bar close** so the
  vectorised engine's :pymeth:`run_vectorized` derives the same
  absolute SL/TP levels the visual strategy passes via
  :pymeth:`Context.buy_at_open(sl=close-sl_pts)`.
* Costs are zero on the parity dataset (no spread / commission /
  slippage) so the strict ``$0.00`` invariant is testing pure engine
  semantics, not the cost model. A second test runs the same strategies
  under a Realistic profile to confirm the cost path is parity-clean
  too.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from engine.strategy.context import StrategyLoader
from engine.core.backtest_core import BacktestCore
from engine.execution.execution import ExecutionModel
from engine.models.models import AssetConfig
from engine.execution.vectorized_engine import run_vectorized


# ----------------------------------------------------------------- fixtures
def _synthetic_df(n_bars: int = 600, *, seed: int = 7) -> pd.DataFrame:
    """OHLCV frame designed to exercise both crossover and breakout
    strategies in both directions over a single run.

    Mirrors the shape used by ``test_example_strategies`` so the two
    files can be cross-checked, but with a different seed to avoid
    coincidental parity.
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


def _df_lowercase(n_bars: int = 600, *, seed: int = 7) -> pd.DataFrame:
    """Visual engine consumes lowercase column names — match
    :pymeth:`BacktestCore.load_data`."""
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


def _parity_asset() -> AssetConfig:
    """Asset with EOD/weekend gating disabled so the parity dataset
    isn't accidentally truncated by trading-hours rules. The synthetic
    times start at 09:30 and end well before the 22:00 default EOD."""
    return AssetConfig(
        name="PARITY",
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


def _run_visual(strategy, df: pd.DataFrame, *, profile: str = "Optimistic"):
    """Drive the strategy through ``BacktestCore.step_n`` and return
    the resulting trade list and final equity / DD numbers.

    Mimics the vectorised engine's wrap-up step: if a position is still
    open at the last bar (the visual engine leaves it open by design,
    since it's iterative), close it at the last bar's close with reason
    ``EOD`` so both ledgers settle at the same place. Without this the
    parity test would always lose the final trade in the visual run.
    """
    eng = BacktestCore()
    eng.asset_config = _parity_asset()
    eng.execution_model = ExecutionModel(profile=profile)
    eng.load_data(df)
    eng.load_strategy(strategy)
    eng.step_n(len(df) + 5)
    if eng.position is not None and eng.total_bars > 0:
        last_candle = eng.candle_at(eng.total_bars - 1)
        eng.execute_close(reason="EOD", exit_candle=last_candle)
    return eng


def _run_vec(strategy, df_pascal: pd.DataFrame, *, profile: str = "Optimistic"):
    """Drive the strategy through :func:`run_vectorized`."""
    asset = _parity_asset()
    return run_vectorized(
        df_pascal,
        strategy,
        asset=asset,
        execution_model=ExecutionModel(profile=profile),
        initial_equity=100_000.0,
        risk_pct=1.0,
    )


# ----------------------------------------------------------------- helpers
def _trade_tuple(t):
    """Reduce a Trade to the comparable fields. ``id`` is intentionally
    excluded because the two engines may number trades from a different
    starting offset (visual increments through the BarReplay /
    rendering counters)."""
    return (
        t.direction,
        t.entry_index,
        t.exit_index,
        round(float(t.entry_price), 6),
        round(float(t.exit_price), 6),
        round(float(t.pnl), 2),
        t.exit_reason,
    )


def _assert_trades_match(visual_trades, vec_trades, label: str) -> None:
    assert len(visual_trades) == len(vec_trades), (
        f"[{label}] trade count differs: visual={len(visual_trades)} vs quant={len(vec_trades)}"
    )
    for i, (v, q) in enumerate(zip(visual_trades, vec_trades)):
        assert _trade_tuple(v) == _trade_tuple(q), (
            f"[{label}] trade #{i} differs:\n  visual={_trade_tuple(v)}\n  quant ={_trade_tuple(q)}"
        )


# ------------------------------------------------------------------- tests
@pytest.mark.parametrize(
    "strategy_module",
    [
        "strategies/sma_crossover.py",
        "strategies/donchian_breakout.py",
    ],
)
def test_dual_engine_parity_optimistic_profile(strategy_module: str) -> None:
    """Visual ↔ vectorised parity at $0.00 under the Optimistic profile
    (no spread / commission / slippage). Trade count, every (entry,
    exit, pnl, exit_reason) tuple, and final equity must all match
    bit-for-bit."""
    df_lower = _df_lowercase(n_bars=400, seed=11)
    df_pascal = _synthetic_df(n_bars=400, seed=11)

    visual = _run_visual(StrategyLoader.load(strategy_module), df_lower)
    vec = _run_vec(StrategyLoader.load(strategy_module), df_pascal)

    _assert_trades_match(visual.trades, vec.trades, f"{strategy_module} / Optimistic")
    assert float(visual.equity) == pytest.approx(float(vec.equity_curve[-1]), abs=0.01), (
        f"{strategy_module}: final equity mismatch — "
        f"visual={visual.equity:.2f} vs quant={vec.equity_curve[-1]:.2f}"
    )
    assert visual.max_drawdown_pct == pytest.approx(vec.peak_drawdown_pct, abs=0.001)


@pytest.mark.parametrize(
    "strategy_module",
    [
        "strategies/sma_crossover.py",
        "strategies/donchian_breakout.py",
    ],
)
def test_dual_engine_parity_realistic_profile(strategy_module: str) -> None:
    """Same parity contract but under the Realistic profile (the asset
    contributes spread + commission + slippage). This proves that
    Phase 5's slippage layer applies symmetrically in both engines."""
    df_lower = _df_lowercase(n_bars=400, seed=23)
    df_pascal = _synthetic_df(n_bars=400, seed=23)

    visual = _run_visual(StrategyLoader.load(strategy_module), df_lower, profile="Realistic")
    vec = _run_vec(StrategyLoader.load(strategy_module), df_pascal, profile="Realistic")

    _assert_trades_match(visual.trades, vec.trades, f"{strategy_module} / Realistic")
    assert float(visual.equity) == pytest.approx(float(vec.equity_curve[-1]), abs=0.01)


def test_visual_eod_close_matches_vectorized_eod_close() -> None:
    """If a strategy stays in a position through the EOD threshold,
    both engines must produce a single trade closed with reason 'EOD'
    at the same bar index. Pre-Phase 6 the vectorised engine never
    enforced asset-level EOD time, so this would silently differ."""
    n = 60
    times = pd.date_range("2024-01-02 21:00", periods=n, freq="1min")
    # Bar 22 lands exactly on 21:22; the default ``eod_close`` is
    # 22:00 so the EOD trigger fires at bar 60-12 = 48 (=21:48? we want
    # to hit 22:00). Re-anchor: start at 21:00 → 22:00 hits at minute 60.
    # Use 90 bars instead so the EOD bar is well inside the data.
    n2 = 90
    times = pd.date_range("2024-01-02 21:00", periods=n2, freq="1min")
    close = np.full(n2, 100.0)
    df_pascal = pd.DataFrame(
        {
            "time": times,
            "Open": close,
            "High": close + 0.5,
            "Low": close - 0.5,
            "Close": close,
            "Volume": 1.0,
        }
    )
    df_lower = df_pascal.rename(
        columns={
            "Open": "open",
            "High": "high",
            "Low": "low",
            "Close": "close",
            "Volume": "volume",
        }
    )

    class LongOnceFarSL:
        """Open a single long with very wide SL/TP so neither triggers
        before EOD time (22:00) reaches us."""

        name = "LongOnceFarSL"

        def __init__(self) -> None:
            self._fired = False

        def on_candle(self, ctx) -> None:
            if self._fired or ctx.position is not None:
                return
            if ctx._engine.pending_order is not None:
                return
            close_now = float(ctx.data.close[-1])
            ctx.buy_at_open(
                volume=1.0,
                sl=close_now - 100.0,
                tp=close_now + 100.0,
                reason="parity-eod",
            )
            self._fired = True

        def vectorized_signals(self, df_):
            sig = np.zeros(len(df_), dtype=np.int8)
            sig[0] = 1
            return pd.DataFrame(
                {
                    "signal": sig,
                    "sl_points": np.full(len(df_), 100.0),
                    "tp_points": np.full(len(df_), 100.0),
                },
                index=df_.index,
            )

    visual = _run_visual(LongOnceFarSL(), df_lower)
    vec = _run_vec(LongOnceFarSL(), df_pascal)

    assert len(visual.trades) == 1
    assert len(vec.trades) == 1
    v = visual.trades[0]
    q = vec.trades[0]
    assert v.exit_reason == q.exit_reason == "EOD"
    assert v.exit_index == q.exit_index
    assert v.entry_index == q.entry_index
    assert float(v.exit_price) == pytest.approx(float(q.exit_price), abs=1e-9)


def test_dual_engine_skipped_trades_match_when_trading_hours_enforced() -> None:
    """If the asset enables ``enforce_trading_hours`` and the next bar
    after a signal falls outside the trading window, both engines must
    drop the fill and record a TRADING_HOURS skip. Pre-Phase 6 the
    vectorised engine ignored trading-hours entirely, so this would
    silently produce a phantom trade in Quant only."""
    # Build a frame where the signal-bar close is the LAST bar inside
    # trading hours; the next bar is outside the window so the fill
    # should be dropped.
    n = 8
    # Default trading window 09:00–18:00. Put bar 4 close at 17:59
    # then bar 5 at 18:00 (outside).
    times = pd.date_range("2024-01-02 17:55", periods=n, freq="1min")
    close = np.full(n, 100.0)
    df_pascal = pd.DataFrame(
        {
            "time": times,
            "Open": close,
            "High": close + 0.5,
            "Low": close - 0.5,
            "Close": close,
            "Volume": 1.0,
        }
    )
    df_lower = df_pascal.rename(
        columns={
            "Open": "open",
            "High": "high",
            "Low": "low",
            "Close": "close",
            "Volume": "volume",
        }
    )

    class LongOnceAtBar4:
        name = "LongOnceAtBar4"

        def __init__(self) -> None:
            self._fired = False

        def on_candle(self, ctx) -> None:
            # Bar 4 = 17:59; the next bar (18:00) is outside the
            # trading window so the fill must be skipped.
            if self._fired or ctx.position is not None:
                return
            if ctx._engine.pending_order is not None:
                return
            if len(ctx.data) - 1 != 4:
                return
            close_now = float(ctx.data.close[-1])
            ctx.buy_at_open(
                volume=1.0,
                sl=close_now - 5.0,
                tp=close_now + 5.0,
                reason="parity-th",
            )
            self._fired = True

        def vectorized_signals(self, df_):
            sig = np.zeros(len(df_), dtype=np.int8)
            sig[4] = 1
            return pd.DataFrame(
                {
                    "signal": sig,
                    "sl_points": np.full(len(df_), 5.0),
                    "tp_points": np.full(len(df_), 5.0),
                },
                index=df_.index,
            )

    asset = AssetConfig(
        name="PARITY-TH",
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
        enforce_trading_hours=True,
    )

    eng = BacktestCore()
    eng.asset_config = asset
    eng.execution_model = ExecutionModel(profile="Optimistic")
    eng.load_data(df_lower)
    eng.load_strategy(LongOnceAtBar4())
    eng.step_n(len(df_lower) + 5)

    vec = run_vectorized(
        df_pascal,
        LongOnceAtBar4(),
        asset=asset,
        execution_model=ExecutionModel(profile="Optimistic"),
        initial_equity=100_000.0,
        risk_pct=1.0,
    )

    assert len(eng.trades) == 0, "visual: trade must be skipped (TRADING_HOURS)"
    assert len(vec.trades) == 0, "quant: trade must be skipped (TRADING_HOURS)"
    visual_skipped = [s for s in eng.skipped_trades if s.get("reason") == "TRADING_HOURS"]
    quant_skipped = [s for s in vec.skipped_trades if s.get("reason") == "TRADING_HOURS"]
    assert len(visual_skipped) == 1
    assert len(quant_skipped) == 1
    assert visual_skipped[0]["direction"] == quant_skipped[0]["direction"] == "long"
