"""Phase 0.3 (A1) — Visual ↔ vectorised engine PnL parity (property-based).

The existing :mod:`tests.test_dual_engine_parity` locks parity for the
two committed example strategies. This file complements that by sweeping
*random* signal patterns over *random* OHLCV data via Hypothesis, so any
future divergence in the SL / TP / EOD / spread / slippage handling on
either side surfaces as a falsifying example with the seed embedded.

Contract under test
-------------------
For every (data, signals) instance generated:

* The visual engine consumes the strategy via ``on_candle`` (signal at
  bar ``i`` queues an entry at the next bar's open).
* The vectorised engine consumes the same strategy via
  ``vectorized_signals`` (the dataframe convention used by
  :pymeth:`engine.execution.vectorized_engine.run_vectorized`).
* Trade counts must match exactly.
* Per-trade PnL must agree within ``$0.02`` (the rounding step used by
  the engines is ``$0.01``, so ``$0.02`` is the strictest floor that
  doesn't trip on legal floating-point noise).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List

import numpy as np
import pandas as pd
import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

from engine.core.backtest_core import BacktestCore
from engine.execution.execution import ExecutionModel
from engine.execution.vectorized_engine import run_vectorized
from engine.models.models import AssetConfig
from engine.strategy.context import Context, Strategy

# Tolerance per trade — see module docstring.
PARITY_TOL = 0.02


# ----------------------------------------------------------------- helpers
def _parity_asset() -> AssetConfig:
    """Asset with EOD/weekend gating off so the synthetic data isn't
    accidentally truncated. Mirrors ``test_dual_engine_parity``.
    """
    return AssetConfig(
        name="PARITY_PROP",
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


def _make_df(closes: np.ndarray) -> pd.DataFrame:
    """Synthesise a 1m OHLCV frame from a deterministic close path.

    High/low add fixed wicks so SL/TP can intra-bar fire (otherwise a
    flat dataset trivially gives parity).
    """
    n = len(closes)
    closes = np.asarray(closes, dtype=np.float64)
    open_ = np.concatenate(([closes[0]], closes[:-1]))
    high = np.maximum(open_, closes) + 0.5
    low = np.minimum(open_, closes) - 0.5
    times = pd.date_range("2024-01-02 09:30", periods=n, freq="1min")
    return pd.DataFrame(
        {
            "time": times,
            "open": open_,
            "high": high,
            "low": low,
            "close": closes,
            "volume": 1.0,
        }
    )


@dataclass
class _BarSignal:
    """One generated entry signal placed at a specific 1m bar index."""

    bar: int
    direction: int  # +1 long, -1 short
    sl_points: float
    tp_points: float


class _ScriptedStrategy(Strategy):
    """Replays a predetermined list of ``_BarSignal``s.

    Implements both engine APIs so the same instance drives the visual
    and the vectorised paths to identical entries / exits.
    """

    def __init__(self, signals: List[_BarSignal]) -> None:
        super().__init__()
        # Sort and de-dup by bar — only one entry per bar is allowed
        # (the engine ignores subsequent ones, which would silently
        # break parity if both paths handled it differently).
        seen: set[int] = set()
        cleaned: List[_BarSignal] = []
        for s in signals:
            if s.bar in seen:
                continue
            seen.add(s.bar)
            cleaned.append(s)
        cleaned.sort(key=lambda s: s.bar)
        self._signals = cleaned
        self._by_bar = {s.bar: s for s in cleaned}

    # Visual path -------------------------------------------------------
    def on_candle(self, ctx: Context) -> None:  # type: ignore[override]
        # Skip if a position is already open — both engines reject the
        # second entry under the legacy single-position rule.
        if ctx.position is not None:
            return
        idx = ctx._engine.current_idx
        sig = self._by_bar.get(idx)
        if sig is None:
            return
        ref = float(ctx.candle.get("close", 0.0))
        if ref <= 0:
            return
        # Mirror the vectorised engine's risk-based lot sizing so the
        # parity assertion is testing engine semantics, not whether the
        # caller correctly mirrored ``_calc_lot`` on both sides.
        volume = ctx.calculate_lot(sl_points=sig.sl_points, risk_pct=1.0)
        if volume is None or volume <= 0:
            return
        if sig.direction == +1:
            ctx.buy_at_open(
                volume=volume,
                sl=ref - sig.sl_points,
                tp=ref + sig.tp_points,
                reason="entry",
            )
        else:
            ctx.sell_at_open(
                volume=volume,
                sl=ref + sig.sl_points,
                tp=ref - sig.tp_points,
                reason="entry",
            )

    # Vectorised path ---------------------------------------------------
    def vectorized_signals(self, df: pd.DataFrame) -> pd.DataFrame:  # type: ignore[override]
        n = len(df)
        signal = np.zeros(n, dtype=np.int64)
        sl_arr = np.full(n, np.nan, dtype=np.float64)
        tp_arr = np.full(n, np.nan, dtype=np.float64)
        for s in self._signals:
            if 0 <= s.bar < n:
                signal[s.bar] = s.direction
                sl_arr[s.bar] = s.sl_points
                tp_arr[s.bar] = s.tp_points
        return pd.DataFrame(
            {"signal": signal, "sl_points": sl_arr, "tp_points": tp_arr}
        )


def _run_visual(strategy: _ScriptedStrategy, df: pd.DataFrame) -> Any:
    eng = BacktestCore()
    eng.asset_config = _parity_asset()
    eng.execution_model = ExecutionModel(profile="Optimistic")
    eng.load_data(df)
    eng.load_strategy(strategy)
    eng.step_n(len(df) + 5)
    if eng.position is not None and eng.total_bars > 0:
        last_candle = eng.candle_at(eng.total_bars - 1)
        eng.execute_close(reason="EOD", exit_candle=last_candle)
    return eng


def _run_vec(strategy: _ScriptedStrategy, df: pd.DataFrame) -> Any:
    return run_vectorized(
        df,
        strategy,
        asset=_parity_asset(),
        execution_model=ExecutionModel(profile="Optimistic"),
        initial_equity=100_000.0,
        risk_pct=1.0,
    )


# ------------------------------------------------------------- generators
def _close_path(values: List[float]) -> np.ndarray:
    """Deterministic close array from Hypothesis-generated tick deltas.

    We start at 100.0 and add per-bar deltas; clipping to a positive
    floor so the OHLC validation in ``BacktestCore.load_data`` doesn't
    reject the frame.
    """
    arr = np.asarray(values, dtype=np.float64).cumsum() + 100.0
    return np.clip(arr, 50.0, 200.0)


_close_strategy = st.lists(
    st.floats(min_value=-0.5, max_value=0.5, allow_nan=False, allow_infinity=False),
    min_size=120,
    max_size=240,
)

_signal_strategy = st.lists(
    st.builds(
        _BarSignal,
        bar=st.integers(min_value=2, max_value=110),
        direction=st.sampled_from([+1, -1]),
        sl_points=st.floats(
            min_value=0.5, max_value=5.0, allow_nan=False, allow_infinity=False
        ),
        tp_points=st.floats(
            min_value=0.5, max_value=5.0, allow_nan=False, allow_infinity=False
        ),
    ),
    min_size=0,
    max_size=8,
)


# ---------------------------------------------------------------- tests
@settings(
    max_examples=30,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.filter_too_much],
)
@given(closes=_close_strategy, signals=_signal_strategy)
def test_per_trade_pnl_parity_under_random_signals(
    closes: List[float], signals: List[_BarSignal]
) -> None:
    """Per-trade PnL parity within ``$0.02`` for any random scripted run."""
    df = _make_df(_close_path(closes))
    strat_visual = _ScriptedStrategy(signals)
    strat_vec = _ScriptedStrategy(signals)

    visual = _run_visual(strat_visual, df)
    vec = _run_vec(strat_vec, df)

    assert len(visual.trades) == len(vec.trades), (
        f"Trade count differs: visual={len(visual.trades)} vs quant={len(vec.trades)}; "
        f"signals={signals}"
    )
    for i, (v, q) in enumerate(zip(visual.trades, vec.trades)):
        assert abs(float(v.pnl) - float(q.pnl)) < PARITY_TOL, (
            f"Trade #{i}: visual PnL={v.pnl:.4f} vs quant PnL={q.pnl:.4f} "
            f"(delta={abs(v.pnl - q.pnl):.4f}, tol={PARITY_TOL}); signals={signals}"
        )
        # Direction and exit reason should be identical — drift here
        # usually points to an SL vs TP collision-rule disagreement.
        assert v.direction == q.direction, (
            f"Trade #{i}: direction mismatch visual={v.direction} vs quant={q.direction}"
        )


def test_no_signals_produces_zero_trades_in_both_engines() -> None:
    """Smoke test for the harness: empty signal list must yield empty
    trade lists in both engines (otherwise the property test would
    silently always pass on its degenerate input)."""
    df = _make_df(_close_path([0.0] * 200))
    strat = _ScriptedStrategy([])
    visual = _run_visual(strat, df)
    vec = _run_vec(strat, df)
    assert visual.trades == []
    assert vec.trades == []


@pytest.mark.parametrize("direction", [+1, -1])
def test_single_scripted_signal_is_parity_clean(direction: int) -> None:
    """A single hand-crafted signal in the middle of a calm price path
    should hit either TP or SL deterministically and produce identical
    trades across both engines.
    """
    df = _make_df(_close_path([0.01] * 200))
    sig = _BarSignal(bar=10, direction=direction, sl_points=2.0, tp_points=2.0)
    visual = _run_visual(_ScriptedStrategy([sig]), df)
    vec = _run_vec(_ScriptedStrategy([sig]), df)

    assert len(visual.trades) == len(vec.trades) == 1
    v, q = visual.trades[0], vec.trades[0]
    assert v.direction == q.direction
    assert abs(float(v.pnl) - float(q.pnl)) < PARITY_TOL
