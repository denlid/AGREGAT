"""Per-trade PnL trajectory + drawdown analytics (Phase 4.1).

For a single closed trade we want to answer the questions a quant always
asks after the fact:

    1. How much *unrealised* drawdown did the trade endure before the
       eventual exit? (the "intra-trade DD" that flat trade-level metrics
       hide)
    2. How far in our favour did price move before reversing? — the
       Maximum Favourable Excursion (MFE).
    3. What was the worst against-us excursion? — the Maximum Adverse
       Excursion (MAE).
    4. How long was the position underwater (PnL < 0)?
    5. After the deepest dip, when did equity recover to its pre-DD
       peak?

The vectorised helpers here are pure NumPy — they accept either a
``pandas.DataFrame`` window of OHLC bars or raw NumPy arrays so the GUI
side panel can compute these on the fly when the user selects a row in
the trades table (PLAN.md §4.1).

The companion GUI work (side panel + "Show on Chart" zoom) lives in the
gui/ layer and is intentionally kept out of this engine module so the
analytics can be reused outside the Qt application (CLI exports,
unit-tests, future Jupyter notebooks).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, List, Optional, Tuple

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class PerTradeDrawdown:
    """Result of running per-bar PnL analytics on a single trade.

    Attributes
    ----------
    bar_index
        0-based bar offset inside the trade window (entry bar = 0,
        exit bar = ``bar_index[-1]``).
    pnl_curve
        Unrealised PnL in dollars at the close of each bar inside the
        trade. ``pnl_curve[-1]`` matches the realised trade PnL within
        spread/commission rounding.
    drawdown_curve
        Negative-only series: ``min(pnl_curve_so_far - peak_so_far, 0)``
        — the bar-by-bar drawdown from the running peak. Used for the
        red fill_between overlay in the GUI side panel.
    mae
        Maximum Adverse Excursion ($) — the deepest negative point
        ``pnl_curve`` ever reached. Always ≤ 0.
    mfe
        Maximum Favourable Excursion ($) — the highest positive point
        ``pnl_curve`` ever reached. Always ≥ 0.
    max_dd
        Deepest peak-to-trough fall during the trade (≤ 0).
    max_dd_bar
        Bar index where the deepest DD was registered.
    time_in_dd
        Number of bars where ``pnl_curve`` < 0 (used to size the "how
        long was I in pain?" tooltip).
    recovery_bar
        Bar index at which equity climbed back to the pre-DD peak after
        the worst drawdown, or ``None`` if it never recovered before
        exit.
    risk_dollars
        Risk per trade in dollars (entry-to-SL × position size). Used
        to express MAE/MFE as R-multiples in the report. ``None`` when
        the trade has no SL.
    """

    bar_index: np.ndarray
    pnl_curve: np.ndarray
    drawdown_curve: np.ndarray
    mae: float
    mfe: float
    max_dd: float
    max_dd_bar: int
    time_in_dd: int
    recovery_bar: Optional[int]
    risk_dollars: Optional[float]

    @property
    def mae_r(self) -> Optional[float]:
        """MAE expressed as R-multiples (negative). None if no SL/risk."""
        if self.risk_dollars and self.risk_dollars > 0:
            return self.mae / self.risk_dollars
        return None

    @property
    def mfe_r(self) -> Optional[float]:
        """MFE expressed as R-multiples (positive). None if no SL/risk."""
        if self.risk_dollars and self.risk_dollars > 0:
            return self.mfe / self.risk_dollars
        return None


# ---------------------------------------------------------------------------
# Core: extract per-bar PnL trajectory for one trade
# ---------------------------------------------------------------------------


def _pos_value_per_point(trade: Any) -> float:
    """Dollars-per-point for the trade.

    We look at three common attribute names so this works against both
    legacy ``Trade`` objects and the post-Phase-1 execution model.
    """
    for attr in ("dollars_per_point", "value_per_point", "pnl_per_point"):
        v = getattr(trade, attr, None)
        if v is not None and float(v) > 0:
            return float(v)
    # Fallback: derive from the realised PnL and price change. This is
    # only used when an older Trade object doesn't expose the multiplier.
    entry = float(getattr(trade, "entry_price", 0.0) or 0.0)
    exit_p = float(getattr(trade, "exit_price", 0.0) or 0.0)
    pnl = float(getattr(trade, "pnl", 0.0) or 0.0)
    direction = str(getattr(trade, "direction", "long")).lower()
    sign = 1.0 if direction.startswith("l") else -1.0
    diff = (exit_p - entry) * sign
    if abs(diff) > 1e-9:
        return abs(pnl / diff)
    return 1.0


def per_trade_drawdown(
    trade: Any,
    bars_open: Iterable[float],
    bars_high: Iterable[float],
    bars_low: Iterable[float],
    bars_close: Iterable[float],
) -> PerTradeDrawdown:
    """Compute the per-bar PnL trajectory for ``trade`` over the given OHLC
    window (entry bar inclusive, exit bar inclusive).

    Why we walk the bars manually instead of just interpolating between
    entry/exit:
        * MAE / MFE require the *bar extremes* (high for longs, low for
          shorts) — interpolation would understate them.
        * The drawdown curve has to use the realised path, not just
          peaks, because the GUI overlay shows the actual underwater
          period, not a smooth interpolation.

    The returned arrays are aligned: ``len(pnl_curve) == n_bars``.
    """
    o = np.asarray(list(bars_open), dtype=float)
    h = np.asarray(list(bars_high), dtype=float)
    low_arr = np.asarray(list(bars_low), dtype=float)
    c = np.asarray(list(bars_close), dtype=float)
    n = int(min(len(o), len(h), len(low_arr), len(c)))
    if n == 0:
        empty_i = np.empty(0, dtype=int)
        empty_f = np.empty(0, dtype=float)
        return PerTradeDrawdown(
            bar_index=empty_i,
            pnl_curve=empty_f,
            drawdown_curve=empty_f,
            mae=0.0,
            mfe=0.0,
            max_dd=0.0,
            max_dd_bar=0,
            time_in_dd=0,
            recovery_bar=None,
            risk_dollars=_risk_dollars(trade),
        )
    o, h, low_arr, c = o[:n], h[:n], low_arr[:n], c[:n]

    direction = str(getattr(trade, "direction", "long")).lower()
    sign = 1.0 if direction.startswith("l") else -1.0
    entry = float(getattr(trade, "entry_price", 0.0) or 0.0)
    vpp = _pos_value_per_point(trade)
    volume = float(getattr(trade, "volume", 1.0) or 1.0)

    # Per-bar close-PnL: ((close − entry) * sign) * value_per_point * volume
    pnl_close = (c - entry) * sign * vpp * volume

    # MAE comes from the bar extreme that hurts most:
    #   long  → low_arr (price moves *down* against us)
    #   short → high     (price moves *up* against us)
    if sign > 0:
        worst_against = low_arr
        best_for_us = h
    else:
        worst_against = h
        best_for_us = low_arr
    pnl_worst = (worst_against - entry) * sign * vpp * volume
    pnl_best = (best_for_us - entry) * sign * vpp * volume

    mae = float(min(0.0, pnl_worst.min())) if pnl_worst.size else 0.0
    mfe = float(max(0.0, pnl_best.max())) if pnl_best.size else 0.0

    # Drawdown curve: pnl - running peak
    running_peak = np.maximum.accumulate(pnl_close)
    drawdown_curve = pnl_close - running_peak  # ≤ 0

    max_dd = float(drawdown_curve.min()) if drawdown_curve.size else 0.0
    max_dd_bar = int(np.argmin(drawdown_curve)) if drawdown_curve.size else 0

    time_in_dd = int((pnl_close < 0).sum())

    # Recovery: first bar after max_dd_bar where pnl_close >= peak_at_dd
    recovery_bar: Optional[int] = None
    if drawdown_curve.size and max_dd < 0:
        peak_at_dd = float(running_peak[max_dd_bar])
        for i in range(max_dd_bar + 1, n):
            if pnl_close[i] >= peak_at_dd:
                recovery_bar = int(i)
                break

    return PerTradeDrawdown(
        bar_index=np.arange(n, dtype=int),
        pnl_curve=pnl_close,
        drawdown_curve=drawdown_curve,
        mae=mae,
        mfe=mfe,
        max_dd=max_dd,
        max_dd_bar=max_dd_bar,
        time_in_dd=time_in_dd,
        recovery_bar=recovery_bar,
        risk_dollars=_risk_dollars(trade),
    )


def _risk_dollars(trade: Any) -> Optional[float]:
    """Compute initial $ risk (entry-to-SL × value-per-point × volume).

    Returns None when the trade has no SL or no usable price multiplier
    — callers will then suppress R-multiple display.
    """
    entry = float(getattr(trade, "entry_price", 0.0) or 0.0)
    sl = float(getattr(trade, "sl", 0.0) or 0.0)
    if entry <= 0 or sl <= 0:
        return None
    vpp = _pos_value_per_point(trade)
    volume = float(getattr(trade, "volume", 1.0) or 1.0)
    return float(abs(entry - sl) * vpp * volume)


# ---------------------------------------------------------------------------
# Lookup helpers for the GUI side-panel
# ---------------------------------------------------------------------------


def slice_bars_for_trade(
    df: pd.DataFrame,
    trade: Any,
    *,
    pad_before: int = 0,
    pad_after: int = 0,
) -> Tuple[pd.DataFrame, int, int]:
    """Return ``(window_df, entry_idx_in_window, exit_idx_in_window)``
    for the OHLC slice covering ``trade``.

    The "Show on Chart" button uses this with ``pad_before=50``,
    ``pad_after=50`` to zoom the main chart to ``entry ± 50`` bars.
    """
    if df is None or df.empty:
        return df, 0, 0
    # Modern engines store bar indexes on the trade itself.
    entry_bar = getattr(trade, "entry_bar_index", None)
    exit_bar = getattr(trade, "exit_bar_index", None)
    if entry_bar is None or exit_bar is None:
        # Fallback: locate by entry_time / exit_time
        et = pd.to_datetime(getattr(trade, "entry_time", None), errors="coerce")
        xt = pd.to_datetime(getattr(trade, "exit_time", None), errors="coerce")
        # Find the row with matching timestamp using the index. We assume
        # the dataframe is indexed on its time axis (default for AGREGAT).
        if isinstance(df.index, pd.DatetimeIndex):
            entry_bar = int(df.index.get_indexer([et], method="nearest")[0])
            exit_bar = int(df.index.get_indexer([xt], method="nearest")[0])
        else:
            entry_bar = 0
            exit_bar = max(0, len(df) - 1)
    entry_bar = int(entry_bar)
    exit_bar = int(exit_bar)
    lo = max(0, entry_bar - int(pad_before))
    hi = min(len(df), exit_bar + int(pad_after) + 1)
    window = df.iloc[lo:hi].copy()
    return window, entry_bar - lo, exit_bar - lo


def aggregate_mae_mfe(
    trades: Iterable[Any],
    df: Optional[pd.DataFrame],
) -> List[Tuple[int, float, float, float]]:
    """Compute (id, mae, mfe, max_dd) for every trade.

    Used by the report's "MAE/MFE summary" table — gives a single row
    per trade that the user can sort by worst MAE etc.
    """
    out: List[Tuple[int, float, float, float]] = []
    if df is None or df.empty:
        return out
    o = (
        df["open"].to_numpy()
        if "open" in df.columns
        else df.get("Open", pd.Series()).to_numpy()
    )
    h = (
        df["high"].to_numpy()
        if "high" in df.columns
        else df.get("High", pd.Series()).to_numpy()
    )
    low = (
        df["low"].to_numpy()
        if "low" in df.columns
        else df.get("Low", pd.Series()).to_numpy()
    )
    c = (
        df["close"].to_numpy()
        if "close" in df.columns
        else df.get("Close", pd.Series()).to_numpy()
    )
    if o.size == 0:
        return out
    for t in trades:
        eb_raw = getattr(t, "entry_bar_index", None)
        xb_raw = getattr(t, "exit_bar_index", None)
        if eb_raw is None or xb_raw is None:
            continue
        eb = int(eb_raw)
        xb = int(xb_raw)
        if eb < 0 or xb < eb:
            continue
        slc = slice(eb, min(xb + 1, len(o)))
        res = per_trade_drawdown(t, o[slc], h[slc], low[slc], c[slc])
        out.append((int(getattr(t, "id", 0)), res.mae, res.mfe, res.max_dd))
    return out
