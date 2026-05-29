"""Vectorized backtest engine — Mode 2 (Fast Quant) of the dual-engine
architecture.

This module mirrors a *subset* of :mod:`engine.backtest_engine`'s semantics
using NumPy array ops instead of a Python ``for``-loop over candles. It is
designed for parameter-sweep / optimisation workloads where the GUI is not
needed and only the final trade list / equity curve matters.

The core entry point is :func:`run_vectorized`, which accepts:

* an OHLCV ``pandas.DataFrame``,
* a strategy with a ``vectorized_signals(df) -> pd.DataFrame`` method,
* an :class:`engine.models.AssetConfig` describing tick / point / costs,
* an :class:`engine.execution.ExecutionModel` for spread / commission /
  slippage,

and returns a ``VectorizedResult`` with the per-trade list (each trade
matches the same ``Trade`` shape produced by :class:`BacktestEngine`),
the equity curve, peak drawdown, and a summary dict.

Strategy contract
-----------------
Strategies opt in to vectorised execution by implementing::

    def vectorized_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        # Return a frame indexed exactly like ``df`` with the columns:
        #   - "signal": int — +1 = open long, -1 = open short, 0 = no action
        #   - "sl": float — absolute stop loss price
        #   - "tp": float — absolute take-profit price; 0 or NaN = no TP
        #   - "risk_pct" (optional): per-trade risk fraction in % (defaults to engine.risk_pct)

The simulator opens at most one position at a time. While a position is open
subsequent ``signal`` values are ignored (so the strategy is free to keep
emitting "buy" markers without spamming entries). When SL or TP triggers
intra-bar the trade closes at that bar; the next non-zero signal reopens.

Constraints / non-goals
-----------------------
* Trailing stops, pending orders, partial closes and weekend / EOD logic
  are intentionally **not** modelled here — they are part of the "Visual"
  on-candle path. The vectorised engine targets the optimisation use-case
  where the strategy is fully described by the signal frame.
* Same-bar SL/TP collisions resolve worst-case (SL first, mirroring
  :pymeth:`BacktestEngine._check_sl_tp`).
* Spread on close follows the Phase 1 convention: shorts close at Ask,
  long fills clamped to the Bid side. This keeps SL/TP thresholds
  consistent with the on-candle engine.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, List, Optional, Sequence

import numpy as np
import pandas as pd

from engine.analytics._vec_numba import HAS_NUMBA_VEC, run_vectorized_loop
from engine.execution.execution import ExecutionModel, slippage_model_to_int
from engine.execution.prepared_dataset import PreparedDataset, prepare_vectorized_dataset
from engine.models.models import AssetConfig, Trade, get_session
from engine.analytics.stats_accumulator import DrawdownTracker


def _round_to_step(value: float, step: float = 0.01) -> float:
    """Round ``value`` to the nearest multiple of ``step``.

    Mirrors ``engine.core.backtest_core._round_to_step``; duplicated here to
    avoid a circular import between ``backtest_core`` (which imports
    :class:`VectorizedResult` from this module) and the vectorised engine.
    The two implementations must stay byte-identical — see TZ Phase 1 / A2.
    """
    if step is None or step <= 0:
        return round(value, 2)
    return round(round(value / step) * step, 10)


logger = logging.getLogger("StrategyTester.engine.vectorized")


@dataclass
class VectorizedResult:
    """Outcome of a single :func:`run_vectorized` call."""

    trades: List[Trade]
    equity_curve: np.ndarray  # equity after each closed trade, prefixed with init_eq
    peak_drawdown_pct: float
    peak_drawdown_abs: float
    summary: dict[str, Any]
    # Pending fills the vectorised engine had to drop because the next bar
    # was outside ``asset.trading_hours_*`` and ``enforce_trading_hours``
    # was set. Mirrors ``BacktestCore.skipped_trades`` so the GUI tabs
    # cannot drift between engines (TZ Phase 6 / A8). Each entry is
    # ``{'time': str, 'reason': 'TRADING_HOURS', 'direction': 'long'|'short'}``.
    skipped_trades: List[dict[str, Any]] = field(default_factory=list)
    perf: dict[str, Any] = field(default_factory=dict)


# Required columns in the strategy's signal frame. Any others are ignored.
# ``sl_points`` / ``tp_points`` are *price distances* from the fill price,
# matching the convention used by ``Context.calculate_lot`` in the visual
# engine (multiplied by ``asset.point_value`` to convert to dollar risk).
_REQUIRED_COLS: Sequence[str] = ("signal", "sl_points", "tp_points")


def _validate_signals(signals: pd.DataFrame, n_bars: int) -> None:
    """Cheap sanity check — surface bad strategies with a clear error."""
    if not isinstance(signals, pd.DataFrame):
        raise TypeError(
            "Strategy.vectorized_signals must return a pandas DataFrame; "
            f"got {type(signals).__name__}."
        )
    if len(signals) != n_bars:
        raise ValueError(
            f"Strategy.vectorized_signals returned {len(signals)} rows "
            f"but the input frame has {n_bars}."
        )
    missing = [c for c in _REQUIRED_COLS if c not in signals.columns]
    if missing:
        raise ValueError(
            f"Strategy.vectorized_signals is missing required columns: {missing}. "
            "Expected at least signal, sl_points, tp_points."
        )


def _calc_lot(
    *,
    equity: float,
    risk_pct: float,
    sl_points: float,
    asset: AssetConfig,
    abs_max_lots: float = 100.0,
) -> float:
    """Lightweight, allocation-free reimplementation of
    :pymeth:`engine.context.Context.calculate_lot` for the vectorised path.

    Returns 0.0 if the trade should be skipped (zero/negative SL distance,
    margin caps, or volume below ``asset.min_lot``). The caller is expected
    to skip rows where this returns 0.
    """
    if sl_points <= 0:
        return 0.0
    risk_amount = equity * (risk_pct / 100.0)
    if risk_amount <= 0:
        return 0.0
    point_value = float(asset.point_value or 1.0)
    raw_lots = risk_amount / (sl_points * point_value)
    # Apply broker caps mirroring Context.calculate_lot
    cap = float(asset.max_lots_per_100k or 0.0)
    if cap > 0:
        margin_cap = (equity / 100_000.0) * cap
        raw_lots = min(raw_lots, margin_cap)
    raw_lots = min(raw_lots, abs_max_lots)
    # Round down to the asset's lot step
    step = float(asset.lot_step or 0.01)
    if step > 0:
        raw_lots = np.floor(raw_lots / step) * step
    if raw_lots < float(asset.min_lot or 0.0):
        return 0.0
    return float(raw_lots)


def run_vectorized(
    df: pd.DataFrame,
    strategy: Any,
    *,
    asset: AssetConfig,
    execution_model: Optional[ExecutionModel] = None,
    initial_equity: float = 100_000.0,
    risk_pct: float = 1.0,
    absolute_max_lots: float = 100.0,
    prepared: Optional[PreparedDataset] = None,
    materialize_trades: bool = True,
) -> VectorizedResult:
    """Run a vectorised backtest.

    See module docstring for the strategy contract. Returns a
    :class:`VectorizedResult` with the same ``Trade`` objects that the
    on-candle engine produces, so downstream Analytics / Quant tabs can
    consume both interchangeably.
    """
    if not hasattr(strategy, "vectorized_signals"):
        raise AttributeError(
            "Strategy does not implement vectorized_signals(df). "
            "Either add the method or run with the Visual (on_candle) engine."
        )

    if df is None or len(df) == 0:
        return VectorizedResult([], np.array([initial_equity]), 0.0, 0.0, {}, perf={
            "bars": 0,
            "signals_gen_ms": 0.0,
            "signals_validate_ms": 0.0,
            "numpy_extract_ms": 0.0,
            "mask_build_ms": 0.0,
            "numba_loop_ms": 0.0,
            "python_loop_ms": 0.0,
            "trade_materialize_ms": 0.0,
            "summary_ms": 0.0,
            "total_ms": 0.0,
            "used_numba": False,
            "trade_count": 0,
        })

    import time
    t_run_start = time.perf_counter()
    t0 = time.perf_counter()

    signals = strategy.vectorized_signals(df)

    t_elapsed = time.perf_counter() - t0
    if t_elapsed > 1.0:
        logger.warning(
            f"⚠️ Ваша стратегія (vectorized_signals) виконувалась {t_elapsed:.2f} сек. "
            "Ймовірно, ви використовуєте повільні цикли (for/iterrows) замість NumPy/Pandas векторизації. "
            "Оптимізуйте генерацію сигналів для забезпечення HFT продуктивності."
        )

    n = len(df)
    t0 = time.perf_counter()
    _validate_signals(signals, n)
    t_validate = time.perf_counter() - t0

    # --- Phase 4 Fix: Look-ahead Bias Sandbox Check ---
    if n > 50:
        try:
            # We take a small slice to keep the check ultra-fast (e.g. max 1000 bars)
            test_size = min(1000, n)
            test_df = df.iloc[:test_size].copy()
            sig_full = strategy.vectorized_signals(test_df)["signal"].to_numpy()
            
            # Truncate 10 bars from the future and recalculate
            test_df_trunc = test_df.iloc[:-10].copy()
            sig_trunc = strategy.vectorized_signals(test_df_trunc)["signal"].to_numpy()
            
            # If past signals change because future data is missing -> Look-ahead bias!
            if not np.array_equal(sig_full[:len(sig_trunc)], sig_trunc):
                logger.error(
                    "🚨 CRITICAL WARNING: Look-ahead bias detected in vectorized_signals! "
                    "Past signals changed when future bars were removed. "
                    "Ensure you are not using .shift(-1) or non-causal indicators."
                )
        except Exception:
            pass # Ignore heuristic failures (e.g., if strategy requires min 2000 bars to warmup)

    em = execution_model or ExecutionModel()
    t0 = time.perf_counter()
    prep = prepared or prepare_vectorized_dataset(df, asset=asset, execution_model=em)
    t_numpy_extract = time.perf_counter() - t0
    t_mask_build = 0.0 if prepared is not None else t_numpy_extract

    open_ = prep.open
    high = prep.high
    low = prep.low
    close = prep.close
    times = prep.times
    is_trading_hours_arr = prep.is_trading_hours
    is_eod_arr = prep.is_eod
    is_weekend_arr = prep.is_weekend
    enforce_th = prep.enforce_trading_hours
    enforce_eod = prep.enforce_eod
    weekend_blocking = prep.weekend_blocking
    point_value = prep.point_value
    spread = prep.spread
    slip = prep.slip
    commission = prep.commission
    slip_per_leg = em._slippage(asset)
    slippage_cost_per_trade = slip_per_leg * 2.0 * point_value

    sig = signals["signal"].to_numpy(dtype=np.int64)
    sl_pts_arr = signals["sl_points"].to_numpy(dtype=np.float64)
    tp_pts_arr = signals["tp_points"].to_numpy(dtype=np.float64)
    if "risk_pct" in signals.columns:
        risk_arr = signals["risk_pct"].to_numpy(dtype=np.float64)
    else:
        risk_arr = np.full(n, risk_pct, dtype=np.float64)

    # ---- Fast path: Numba-JIT inner loop ----
    if HAS_NUMBA_VEC:
        logger.debug("Vectorised engine: using Numba-accelerated inner loop")
        t0 = time.perf_counter()
        result_tuple = run_vectorized_loop(
            n,
            open_,
            high,
            low,
            close,
            sig,
            sl_pts_arr,
            tp_pts_arr,
            risk_arr,
            is_trading_hours_arr,
            is_eod_arr,
            is_weekend_arr,
            enforce_th,
            weekend_blocking,
            spread,
            slip,
            commission,
            point_value,
            initial_equity,
            float(asset.min_lot or 0.0),
            float(asset.lot_step or 0.01),
            float(asset.max_lots_per_100k or 0.0),
            absolute_max_lots,
            slippage_model_to_int(em.slippage_model),
            float(em.slippage_k),
        )
        (
            trade_count,
            r_entry_prices,
            r_exit_prices,
            r_pnls,
            r_pnl_pts,
            r_entry_indices,
            r_exit_indices,
            r_directions,
            r_exit_reasons,
            r_volumes,
            r_maes,
            r_mfes,
            r_initial_sls,
            equity_curve,
        ) = result_tuple
        t_numba_loop = time.perf_counter() - t0

        _REASON_MAP = {1: "SL", 2: "TP", 3: "EOD", 4: "WEEKEND", 5: "GAP_SL"}
        dd_tracker = DrawdownTracker()
        dd_tracker.reset(initial_equity)
        trades: List[Trade] = []
        slippage_cost_per_trade_val = slip * 2.0 * point_value
        t0 = time.perf_counter()
        if materialize_trades:
            for ti in range(trade_count):
                e_idx = int(r_entry_indices[ti])
                x_idx = int(r_exit_indices[ti])
                d = "long" if r_directions[ti] == 1 else "short"
                sl_dist = (
                    abs(r_entry_prices[ti] - r_initial_sls[ti])
                    if r_initial_sls[ti] > 0
                    else 0.0
                )
                rr = (float(r_pnl_pts[ti]) / sl_dist) if sl_dist > 0 else None
                try:
                    entry_ts = pd.Timestamp(times[e_idx])
                    exit_ts = pd.Timestamp(times[x_idx])
                    dur = max(0, int((exit_ts - entry_ts).total_seconds() / 60))
                except (ValueError, TypeError):
                    dur = max(0, x_idx - e_idx)
                dd_tracker.update(
                    float(equity_curve[ti + 1])
                    if ti + 1 < len(equity_curve)
                    else float(equity_curve[-1])
                )
                vol = float(r_volumes[ti])
                trades.append(
                    Trade(
                        id=ti + 1,
                        direction=d,
                        entry_price=float(r_entry_prices[ti]),
                        exit_price=float(r_exit_prices[ti]),
                        pnl=float(r_pnls[ti]),
                        pnl_points=float(r_pnl_pts[ti]),
                        entry_index=e_idx,
                        exit_index=x_idx,
                        entry_time=str(times[e_idx]),
                        exit_time=str(times[x_idx]),
                        volume=vol,
                        duration_minutes=dur,
                        exit_reason=_REASON_MAP.get(int(r_exit_reasons[ti]), "MANUAL"),
                        mae=float(r_maes[ti]),
                        mfe=float(r_mfes[ti]),
                        session=get_session(times[e_idx], asset=asset),
                        initial_sl=float(r_initial_sls[ti]),
                        rr=rr,
                        mae_dollars=float(r_maes[ti]) * vol * point_value,
                        mfe_dollars=float(r_mfes[ti]) * vol * point_value,
                        commission=commission * vol,
                        spread_cost=0.0,
                        slippage_cost=_round_to_step(
                            slippage_cost_per_trade_val * vol, 0.01
                        ),
                    )
                )
        else:
            for ti in range(trade_count):
                dd_tracker.update(
                    float(equity_curve[ti + 1])
                    if ti + 1 < len(equity_curve)
                    else float(equity_curve[-1])
                )
        t_trade_materialize = time.perf_counter() - t0
        t0 = time.perf_counter()
        final_eq = float(equity_curve[-1])
        summary = {
            "n_trades": int(trade_count),
            "final_equity": final_eq,
            "total_return_pct": (final_eq - initial_equity) / initial_equity * 100.0
            if initial_equity > 0
            else 0.0,
            "peak_drawdown_pct": dd_tracker.max_drawdown_pct,
            "peak_drawdown_abs": dd_tracker.max_drawdown_abs,
        }
        t_summary = time.perf_counter() - t0
        perf = {
            "bars": int(n),
            "signals_gen_ms": round(t_elapsed * 1000.0, 3),
            "signals_validate_ms": round(t_validate * 1000.0, 3),
            "numpy_extract_ms": round(t_numpy_extract * 1000.0, 3),
            "mask_build_ms": round(t_mask_build * 1000.0, 3),
            "numba_loop_ms": round(t_numba_loop * 1000.0, 3),
            "python_loop_ms": 0.0,
            "trade_materialize_ms": round(t_trade_materialize * 1000.0, 3),
            "summary_ms": round(t_summary * 1000.0, 3),
            "total_ms": round((time.perf_counter() - t_run_start) * 1000.0, 3),
            "used_numba": True,
            "trade_count": int(trade_count),
        }
        logger.info("vectorized_perf %s", perf)
        return VectorizedResult(
            trades=trades,
            equity_curve=equity_curve,
            peak_drawdown_pct=dd_tracker.max_drawdown_pct,
            peak_drawdown_abs=dd_tracker.max_drawdown_abs,
            summary=summary,
            skipped_trades=[],
            perf=perf,
        )

    # ---- Fallback: pure-Python inner loop (no Numba) ----
    t_numba_loop = 0.0
    t_trade_materialize = 0.0
    t_summary = 0.0
    t0 = time.perf_counter()
    trades: List[Trade] = []  # type: ignore[no-redef]
    equity_track = [float(initial_equity)]
    equity = float(initial_equity)
    # Centralised DD tracker — same class the visual engine uses, so the
    # Stats / Quant tabs cannot drift between engines (TZ Phase 4 / Etap 4.1).
    dd_tracker = DrawdownTracker()
    dd_tracker.reset(initial_equity)
    next_trade_id = 0
    skipped_trades: List[dict[str, Any]] = []

    # Position state
    pos_dir = 0  # +1 long, -1 short, 0 flat
    pos_entry = 0.0
    pos_sl = 0.0
    pos_tp = 0.0
    pos_volume = 0.0
    pos_entry_idx = -1
    pos_entry_time: Optional[object] = None
    pos_initial_sl = 0.0
    pos_mae = 0.0
    pos_mfe = 0.0
    # Phase 1.3 — per-trade slippage points (volume-aware). Defaults to
    # the base ``slip`` so the python fallback stays bit-exact when the
    # model is "fixed" or k=0.
    pos_slip = slip

    # Pending order state — signals fire at the close of bar ``i`` but the
    # fill is deferred to the open of bar ``i+1`` to match the visual
    # engine (which queues a :class:`PendingOrder` and consumes it via
    # :pymeth:`BacktestCore._fill_pending`). TZ Phase 6 / A7.
    pend_dir = 0
    pend_sl_price = 0.0
    pend_tp_price = 0.0
    pend_sl_pts = 0.0
    pend_risk = 0.0

    def _close(exit_price: float, exit_idx: int, exit_reason: str) -> None:
        """Materialise the active position into a Trade and update equity."""
        nonlocal equity, next_trade_id
        nonlocal pos_dir, pos_entry, pos_sl, pos_tp, pos_volume
        nonlocal pos_entry_idx, pos_entry_time, pos_mae, pos_mfe, pos_initial_sl
        if pos_dir == +1:
            pnl_points = exit_price - pos_entry
        else:
            pnl_points = pos_entry - exit_price
        gross = pnl_points * pos_volume * point_value
        commission_cost = commission * pos_volume
        slip_cost = slippage_cost_per_trade * pos_volume
        pnl = gross - commission_cost
        # Quantise to cents, matching ``BacktestCore.execute_close``.
        # (Earlier revisions rounded to ``tick_size`` which broke parity on
        # any instrument with tick != 0.01 — see TZ Phase 1 / A2.)
        pnl = _round_to_step(pnl, 0.01)
        equity = _round_to_step(equity + pnl, 0.01)

        # Centralised DD tracker drives both abs and pct from the same peak.
        dd_tracker.update(equity)
        equity_track.append(equity)

        sl_dist = abs(pos_entry - pos_initial_sl) if pos_initial_sl > 0 else 0.0
        rr = (pnl_points / sl_dist) if sl_dist > 0 else None
        mae_dollars = pos_mae * pos_volume * point_value
        mfe_dollars = pos_mfe * pos_volume * point_value

        # Trade duration in real minutes (not bar count) so analytics that
        # buckets by ``duration_minutes`` works on any timeframe — TZ Phase 1 / A3.
        try:
            entry_ts = pd.Timestamp(pos_entry_time)
            exit_ts = pd.Timestamp(times[exit_idx])
            duration_minutes = max(0, int((exit_ts - entry_ts).total_seconds() / 60))
        except (ValueError, TypeError):
            duration_minutes = max(0, exit_idx - pos_entry_idx)

        next_trade_id += 1
        trade = Trade(
            id=next_trade_id,
            direction="long" if pos_dir == +1 else "short",
            entry_price=pos_entry,
            exit_price=exit_price,
            pnl=pnl,
            pnl_points=pnl_points,
            entry_index=pos_entry_idx,
            exit_index=exit_idx,
            entry_time=str(pos_entry_time),
            exit_time=str(times[exit_idx]),
            volume=pos_volume,
            duration_minutes=duration_minutes,
            exit_reason=exit_reason,
            mae=pos_mae,
            mfe=pos_mfe,
            session=get_session(pos_entry_time, asset=asset),
            initial_sl=pos_initial_sl,
            rr=rr,
            mae_dollars=mae_dollars,
            mfe_dollars=mfe_dollars,
            commission=commission_cost,
            spread_cost=0.0,  # spread is rolled into entry/exit price for the vectorised path
            slippage_cost=_round_to_step(slip_cost, 0.01),
        )
        trades.append(trade)
        pos_dir = 0

    for i in range(n):
        bar_open = open_[i]
        bar_high = high[i]
        bar_low = low[i]
        bar_close = close[i]

        # 1) Fill any pending order at *this* bar's open. Mirrors
        # ``BacktestCore._fill_pending`` which deferred the fill from the
        # signal bar to the next bar's open. (TZ Phase 6 / A7.)
        if pend_dir != 0 and pos_dir == 0:
            # Phase 1.7 — unified pending-order rejection: trading
            # hours / weekend / EOD all cancel the pending in the same
            # order as the visual engine's :py:meth:`_fill_pending`.
            skip_reason: Optional[str] = None
            if enforce_th and not is_trading_hours_arr[i]:
                skip_reason = "TRADING_HOURS"
            elif weekend_blocking and bool(is_weekend_arr[i]):
                skip_reason = "WEEKEND"
            elif enforce_eod and bool(is_eod_arr[i]):
                skip_reason = "EOD"
            if skip_reason is not None:
                skipped_trades.append(
                    {
                        "time": str(times[i]),
                        "reason": skip_reason,
                        "direction": "long" if pend_dir == +1 else "short",
                    }
                )
                pend_dir = 0
            else:
                direction = "long" if pend_dir == +1 else "short"
                volume = _calc_lot(
                    equity=equity,
                    risk_pct=pend_risk,
                    sl_points=pend_sl_pts,
                    asset=asset,
                    abs_max_lots=absolute_max_lots,
                )
                # Phase 1.3 — feed volume into the fill so adverse
                # slippage scales with order size.
                fill = em.adjust_fill_price(bar_open, direction, asset, volume=volume)
                if volume > 0:
                    pos_dir = pend_dir
                    pos_entry = fill
                    pos_volume = volume
                    pos_slip = em.slippage_price(asset, volume=volume)
                    pos_entry_idx = i
                    pos_entry_time = times[i]
                    pos_mae = 0.0
                    pos_mfe = 0.0
                    pos_sl = pend_sl_price
                    pos_tp = pend_tp_price
                    pos_initial_sl = pos_sl
                pend_dir = 0

        # 2) If a position is open, update MAE/MFE then check SL/TP.
        if pos_dir != 0:
            if pos_dir == +1:
                worst = pos_entry - bar_low
                best = bar_high - pos_entry
            else:
                worst = bar_high - pos_entry
                best = pos_entry - bar_low
            if worst > pos_mae:
                pos_mae = worst
            if best > pos_mfe:
                pos_mfe = best

            # SL/TP-trigger paths mirror BacktestCore._check_sl_tp:
            # gap-through detected on bar open, otherwise edge-based
            # trigger. Fills widen by ``slip`` in the adverse direction
            # so the backtest stays conservative (TZ Phase 5 / C1+C2).
            if pos_dir == +1:
                if pos_sl > 0 and bar_open < pos_sl:
                    _close(bar_open - pos_slip, i, "GAP_SL")
                elif pos_sl > 0 and bar_low <= pos_sl:
                    _close(pos_sl - pos_slip, i, "SL")
                elif pos_tp > 0 and bar_high >= pos_tp:
                    _close(pos_tp - pos_slip, i, "TP")
            else:
                ask_open = bar_open + spread
                ask_high = bar_high + spread
                ask_low = bar_low + spread
                if pos_sl > 0 and ask_open > pos_sl:
                    _close(ask_open + pos_slip, i, "GAP_SL")
                elif pos_sl > 0 and ask_high >= pos_sl:
                    _close(pos_sl + pos_slip, i, "SL")
                elif pos_tp > 0 and ask_low <= pos_tp:
                    _close(pos_tp + pos_slip, i, "TP")

        # 3) EOD close (mirrors BacktestCore._process_one_candle step 4).
        # Same Bid model: long sells at Bid - slip; short buys at Ask + slip.
        if pos_dir != 0 and is_eod_arr[i]:
            if pos_dir == +1:
                _close(bar_close - pos_slip, i, "EOD")
            else:
                _close(bar_close + spread + pos_slip, i, "EOD")

        # 4) Weekend close (mirrors BacktestCore._process_one_candle step 5).
        if pos_dir != 0 and weekend_blocking and bool(is_weekend_arr[i]):
            if pos_dir == +1:
                _close(bar_close - pos_slip, i, "WEEKEND")
            else:
                _close(bar_close + spread + pos_slip, i, "WEEKEND")

        # 5) Strategy hook: if flat and a signal fires here, queue a
        # pending order to be filled on the NEXT bar's open. Anchor SL/TP
        # absolutely on the signal-bar close (same convention the visual
        # engine's strategies use via ``buy_at_open(sl=close - sl_pts)``)
        # so both legs of slippage materialise in PnL — TZ Phase 6 / A7.
        if pos_dir == 0 and pend_dir == 0 and sig[i] != 0:
            sl_pts_val = float(sl_pts_arr[i])
            tp_pts_val = float(tp_pts_arr[i])
            if not np.isnan(sl_pts_val) and sl_pts_val > 0:
                direction_dir = +1 if sig[i] > 0 else -1
                if direction_dir == +1:
                    pend_sl_price = bar_close - sl_pts_val
                    pend_tp_price = (
                        bar_close + tp_pts_val
                        if not np.isnan(tp_pts_val) and tp_pts_val > 0
                        else 0.0
                    )
                else:
                    pend_sl_price = bar_close + sl_pts_val
                    pend_tp_price = (
                        bar_close - tp_pts_val
                        if not np.isnan(tp_pts_val) and tp_pts_val > 0
                        else 0.0
                    )
                pend_sl_pts = sl_pts_val
                pend_risk = float(risk_arr[i])
                pend_dir = direction_dir

    # 6) End of data: close any open position at the last close. Reason
    # stays ``EOD`` for back-compat (the visual engine emits ``EOD`` here
    # too via the EOD-time check on the last bar).
    if pos_dir != 0:
        last_close = close[-1]
        if pos_dir == +1:
            eod_price = last_close - pos_slip
        else:
            eod_price = last_close + spread + pos_slip
        _close(eod_price, n - 1, "EOD")

    t_python_loop = time.perf_counter() - t0
    t0 = time.perf_counter()
    summary = {
        "n_trades": len(trades),
        "final_equity": equity,
        "total_return_pct": (equity - initial_equity) / initial_equity * 100.0
        if initial_equity > 0
        else 0.0,
        "peak_drawdown_pct": dd_tracker.max_drawdown_pct,
        "peak_drawdown_abs": dd_tracker.max_drawdown_abs,
    }
    t_summary = time.perf_counter() - t0
    perf = {
        "bars": int(n),
        "signals_gen_ms": round(t_elapsed * 1000.0, 3),
        "signals_validate_ms": round(t_validate * 1000.0, 3),
        "numpy_extract_ms": round(t_numpy_extract * 1000.0, 3),
        "mask_build_ms": round(t_mask_build * 1000.0, 3),
        "numba_loop_ms": round(t_numba_loop * 1000.0, 3),
        "python_loop_ms": round(t_python_loop * 1000.0, 3),
        "trade_materialize_ms": round(t_trade_materialize * 1000.0, 3),
        "summary_ms": round(t_summary * 1000.0, 3),
        "total_ms": round((time.perf_counter() - t_run_start) * 1000.0, 3),
        "used_numba": False,
        "trade_count": int(len(trades)),
    }
    logger.info("vectorized_perf %s", perf)
    return VectorizedResult(
        trades=trades if materialize_trades else [],
        equity_curve=np.array(equity_track, dtype=float),
        peak_drawdown_pct=dd_tracker.max_drawdown_pct,
        peak_drawdown_abs=dd_tracker.max_drawdown_abs,
        summary=summary,
        skipped_trades=skipped_trades,
        perf=perf,
    )
