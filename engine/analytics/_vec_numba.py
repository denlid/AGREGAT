"""Numba-accelerated inner loop for the vectorised backtest engine.

When numba is available, :func:`run_vectorized_loop` executes the
per-bar simulation at near-native speed.  When numba is not installed
the function is still importable but runs as plain Python (slower but
semantically identical).

The loop is intentionally kept free of pandas, Trade objects, and
string ops — it returns raw arrays that :func:`engine.vectorized_engine
.run_vectorized` converts to Trade instances once, outside the hot loop.
"""

from __future__ import annotations

import numpy as np

try:
    from numba import njit  # type: ignore[import-untyped]

    HAS_NUMBA_VEC = True
except ImportError:

    def njit(*args, **kwargs):  # type: ignore[no-redef]
        if args and callable(args[0]):
            return args[0]
        return lambda fn: fn

    HAS_NUMBA_VEC = False


@njit(cache=True)
def _round_step(value: float, step: float) -> float:
    if step <= 0:
        return round(value * 100.0) / 100.0
    return round(round(value / step) * step * 1e10) / 1e10


@njit(cache=True)
def run_vectorized_loop(
    n: int,
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    sig: np.ndarray,
    sl_pts_arr: np.ndarray,
    tp_pts_arr: np.ndarray,
    risk_arr: np.ndarray,
    is_trading_hours_arr: np.ndarray,
    is_eod_arr: np.ndarray,
    is_weekend_arr: np.ndarray,
    enforce_th: bool,
    weekend_blocking: bool,
    spread: float,
    slip: float,
    commission: float,
    point_value: float,
    initial_equity: float,
    # Asset lot sizing params
    min_lot: float,
    lot_step: float,
    max_lots_per_100k: float,
    abs_max_lots: float,
) -> tuple:  # type: ignore[type-arg]
    """Pure-numeric inner loop — returns arrays of trade data.

    Returns
    -------
    (trade_count, entry_prices, exit_prices, pnls, pnl_points_arr,
     entry_indices, exit_indices, directions, exit_reasons,
     volumes, maes, mfes, initial_sls, equity_arr)

    ``directions``: +1 long, -1 short
    ``exit_reasons``: 1=SL, 2=TP, 3=EOD, 4=WEEKEND, 5=GAP_SL
    """
    max_trades = n // 2 + 2
    entry_prices = np.empty(max_trades, dtype=np.float64)
    exit_prices = np.empty(max_trades, dtype=np.float64)
    pnls = np.empty(max_trades, dtype=np.float64)
    pnl_pts = np.empty(max_trades, dtype=np.float64)
    entry_indices = np.empty(max_trades, dtype=np.int64)
    exit_indices = np.empty(max_trades, dtype=np.int64)
    directions = np.empty(max_trades, dtype=np.int64)
    exit_reasons = np.empty(max_trades, dtype=np.int64)
    volumes = np.empty(max_trades, dtype=np.float64)
    maes = np.empty(max_trades, dtype=np.float64)
    mfes = np.empty(max_trades, dtype=np.float64)
    initial_sls = np.empty(max_trades, dtype=np.float64)
    equity_arr = np.empty(n + 1, dtype=np.float64)
    equity_arr[0] = initial_equity

    trade_count = 0
    eq_idx = 0
    equity = initial_equity

    pos_dir = 0
    pos_entry = 0.0
    pos_sl = 0.0
    pos_tp = 0.0
    pos_volume = 0.0
    pos_entry_idx = -1
    pos_initial_sl = 0.0
    pos_mae = 0.0
    pos_mfe = 0.0

    pend_dir = 0
    pend_sl_price = 0.0
    pend_tp_price = 0.0
    pend_sl_pts = 0.0
    pend_risk = 0.0

    for i in range(n):
        bar_open = open_[i]
        bar_high = high[i]
        bar_low = low[i]
        bar_close = close[i]

        # 1) Fill pending order
        if pend_dir != 0 and pos_dir == 0:
            if enforce_th and not is_trading_hours_arr[i]:
                pend_dir = 0
            else:
                # calc lot
                sl_points = pend_sl_pts
                if sl_points > 0.0:
                    risk_amount = equity * (pend_risk / 100.0)
                    if risk_amount > 0.0:
                        raw_lots = risk_amount / (sl_points * point_value)
                        if max_lots_per_100k > 0:
                            margin_cap = (equity / 100000.0) * max_lots_per_100k
                            if raw_lots > margin_cap:
                                raw_lots = margin_cap
                        if raw_lots > abs_max_lots:
                            raw_lots = abs_max_lots
                        if lot_step > 0:
                            raw_lots = np.floor(raw_lots / lot_step) * lot_step
                        if raw_lots >= min_lot:
                            # adjust fill price
                            if pend_dir == +1:
                                fill = bar_open + spread + slip
                            else:
                                fill = bar_open - slip
                            pos_dir = pend_dir
                            pos_entry = fill
                            pos_volume = raw_lots
                            pos_entry_idx = i
                            pos_mae = 0.0
                            pos_mfe = 0.0
                            pos_sl = pend_sl_price
                            pos_tp = pend_tp_price
                            pos_initial_sl = pos_sl
                pend_dir = 0

        # 2) MAE/MFE + SL/TP
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

            closed = False
            exit_price = 0.0
            reason = 0

            if pos_dir == +1:
                if pos_sl > 0 and bar_open < pos_sl:
                    exit_price = bar_open - slip
                    reason = 5  # GAP_SL
                    closed = True
                elif pos_sl > 0 and bar_low <= pos_sl:
                    exit_price = pos_sl - slip
                    reason = 1  # SL
                    closed = True
                elif pos_tp > 0 and bar_high >= pos_tp:
                    exit_price = pos_tp - slip
                    reason = 2  # TP
                    closed = True
            else:
                ask_open = bar_open + spread
                ask_high = bar_high + spread
                ask_low = bar_low + spread
                if pos_sl > 0 and ask_open > pos_sl:
                    exit_price = ask_open + slip
                    reason = 5
                    closed = True
                elif pos_sl > 0 and ask_high >= pos_sl:
                    exit_price = pos_sl + slip
                    reason = 1
                    closed = True
                elif pos_tp > 0 and ask_low <= pos_tp:
                    exit_price = pos_tp + slip
                    reason = 2
                    closed = True

            if closed:
                if pos_dir == +1:
                    pnl_points_val = exit_price - pos_entry
                else:
                    pnl_points_val = pos_entry - exit_price
                gross = pnl_points_val * pos_volume * point_value
                comm = commission * pos_volume
                pnl = _round_step(gross - comm, 0.01)
                equity = _round_step(equity + pnl, 0.01)

                entry_prices[trade_count] = pos_entry
                exit_prices[trade_count] = exit_price
                pnls[trade_count] = pnl
                pnl_pts[trade_count] = pnl_points_val
                entry_indices[trade_count] = pos_entry_idx
                exit_indices[trade_count] = i
                directions[trade_count] = pos_dir
                exit_reasons[trade_count] = reason
                volumes[trade_count] = pos_volume
                maes[trade_count] = pos_mae
                mfes[trade_count] = pos_mfe
                initial_sls[trade_count] = pos_initial_sl
                trade_count += 1
                pos_dir = 0

        # 3) EOD close
        if pos_dir != 0 and is_eod_arr[i]:
            if pos_dir == +1:
                exit_price = bar_close - slip
            else:
                exit_price = bar_close + spread + slip
            if pos_dir == +1:
                pnl_points_val = exit_price - pos_entry
            else:
                pnl_points_val = pos_entry - exit_price
            gross = pnl_points_val * pos_volume * point_value
            comm = commission * pos_volume
            pnl = _round_step(gross - comm, 0.01)
            equity = _round_step(equity + pnl, 0.01)

            entry_prices[trade_count] = pos_entry
            exit_prices[trade_count] = exit_price
            pnls[trade_count] = pnl
            pnl_pts[trade_count] = pnl_points_val
            entry_indices[trade_count] = pos_entry_idx
            exit_indices[trade_count] = i
            directions[trade_count] = pos_dir
            exit_reasons[trade_count] = 3  # EOD
            volumes[trade_count] = pos_volume
            maes[trade_count] = pos_mae
            mfes[trade_count] = pos_mfe
            initial_sls[trade_count] = pos_initial_sl
            trade_count += 1
            pos_dir = 0

        # 4) Weekend close
        if pos_dir != 0 and weekend_blocking and is_weekend_arr[i]:
            if pos_dir == +1:
                exit_price = bar_close - slip
            else:
                exit_price = bar_close + spread + slip
            if pos_dir == +1:
                pnl_points_val = exit_price - pos_entry
            else:
                pnl_points_val = pos_entry - exit_price
            gross = pnl_points_val * pos_volume * point_value
            comm = commission * pos_volume
            pnl = _round_step(gross - comm, 0.01)
            equity = _round_step(equity + pnl, 0.01)

            entry_prices[trade_count] = pos_entry
            exit_prices[trade_count] = exit_price
            pnls[trade_count] = pnl
            pnl_pts[trade_count] = pnl_points_val
            entry_indices[trade_count] = pos_entry_idx
            exit_indices[trade_count] = i
            directions[trade_count] = pos_dir
            exit_reasons[trade_count] = 4  # WEEKEND
            volumes[trade_count] = pos_volume
            maes[trade_count] = pos_mae
            mfes[trade_count] = pos_mfe
            initial_sls[trade_count] = pos_initial_sl
            trade_count += 1
            pos_dir = 0

        # 5) Signal → pending order
        if pos_dir == 0 and pend_dir == 0 and sig[i] != 0:
            sl_pts_val = sl_pts_arr[i]
            tp_pts_val = tp_pts_arr[i]
            if not np.isnan(sl_pts_val) and sl_pts_val > 0:
                direction_dir = 1 if sig[i] > 0 else -1
                if direction_dir == +1:
                    pend_sl_price = bar_close - sl_pts_val
                    if not np.isnan(tp_pts_val) and tp_pts_val > 0:
                        pend_tp_price = bar_close + tp_pts_val
                    else:
                        pend_tp_price = 0.0
                else:
                    pend_sl_price = bar_close + sl_pts_val
                    if not np.isnan(tp_pts_val) and tp_pts_val > 0:
                        pend_tp_price = bar_close - tp_pts_val
                    else:
                        pend_tp_price = 0.0
                pend_sl_pts = sl_pts_val
                pend_risk = risk_arr[i]
                pend_dir = direction_dir

        eq_idx += 1
        equity_arr[eq_idx] = equity

    # Close any remaining position
    if pos_dir != 0:
        last_close = close[n - 1]
        if pos_dir == +1:
            exit_price = last_close - slip
            pnl_points_val = exit_price - pos_entry
        else:
            exit_price = last_close + spread + slip
            pnl_points_val = pos_entry - exit_price
        gross = pnl_points_val * pos_volume * point_value
        comm = commission * pos_volume
        pnl = _round_step(gross - comm, 0.01)
        equity = _round_step(equity + pnl, 0.01)

        entry_prices[trade_count] = pos_entry
        exit_prices[trade_count] = exit_price
        pnls[trade_count] = pnl
        pnl_pts[trade_count] = pnl_points_val
        entry_indices[trade_count] = pos_entry_idx
        exit_indices[trade_count] = n - 1
        directions[trade_count] = pos_dir
        exit_reasons[trade_count] = 3  # EOD
        volumes[trade_count] = pos_volume
        maes[trade_count] = pos_mae
        mfes[trade_count] = pos_mfe
        initial_sls[trade_count] = pos_initial_sl
        trade_count += 1
        eq_idx += 1
        equity_arr[eq_idx] = equity

    return (
        trade_count,
        entry_prices[:trade_count],
        exit_prices[:trade_count],
        pnls[:trade_count],
        pnl_pts[:trade_count],
        entry_indices[:trade_count],
        exit_indices[:trade_count],
        directions[:trade_count],
        exit_reasons[:trade_count],
        volumes[:trade_count],
        maes[:trade_count],
        mfes[:trade_count],
        initial_sls[:trade_count],
        equity_arr[: eq_idx + 1],
    )
