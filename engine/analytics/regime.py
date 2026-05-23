"""Market-regime detection (Phase 4.2).

Two orthogonal classifiers run over the bar series:

    Trend  ::= bull | bear | range   (SMA 200 slope vs threshold)
    Vol    ::= hi | lo               (ATR 14 normalised vs its median)

Combined into 6 buckets — bull_lo, bull_hi, bear_lo, bear_hi,
range_lo, range_hi — which is enough resolution to answer the typical
quant questions:

    "Does my edge survive in low-vol bear markets?"
    "Is the breakout strategy actually only printing money in
     high-vol bull regimes?"

We expose:

    - ``regime_per_bar(...)``: returns a length-N int8 array of bucket
      indices ready to plot or join with a ``trades`` table.
    - ``classify_trades(...)``: per-trade attribution — what regime each
      trade was opened in.
    - ``regime_breakdown(...)``: aggregated stats per regime for the
      tear-sheet "Regime Breakdown" section.

The implementation is pure NumPy — fast enough for 10M-bar feeds, no
external deps.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Bucket vocabulary
# ---------------------------------------------------------------------------
TREND_BULL, TREND_BEAR, TREND_RANGE = 0, 1, 2
VOL_HI, VOL_LO = 0, 1

REGIME_LABELS = (
    "bull_lo",  # 0
    "bull_hi",  # 1
    "bear_lo",  # 2
    "bear_hi",  # 3
    "range_lo",  # 4
    "range_hi",  # 5
)


def _encode(trend: int, vol: int) -> int:
    """Map (trend, vol) → 0..5 in the order documented above."""
    base = {TREND_BULL: 0, TREND_BEAR: 2, TREND_RANGE: 4}[trend]
    return base + (1 if vol == VOL_HI else 0)


# ---------------------------------------------------------------------------
# Indicator helpers (intentionally small & dep-free)
# ---------------------------------------------------------------------------


def _sma(arr: np.ndarray, window: int) -> np.ndarray:
    """Simple moving average using a cumulative sum (no external deps).

    Returns an array the same length as ``arr`` with NaN for the first
    ``window − 1`` slots so it can be cleanly aligned with the price
    series for plotting / joining.
    """
    n = arr.size
    out = np.full(n, np.nan, dtype=float)
    if n < window:
        return out
    cs = np.cumsum(arr, dtype=float)
    out[window - 1] = cs[window - 1] / window
    out[window:] = (cs[window:] - cs[:-window]) / window
    return out


def _atr(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    window: int = 14,
) -> np.ndarray:
    """Wilder ATR (standard SMA-of-TR variant).

    ``True Range = max(high − low, |high − prev_close|, |low − prev_close|)``
    """
    n = high.size
    out = np.full(n, np.nan, dtype=float)
    if n == 0:
        return out
    prev_close = np.empty_like(close)
    prev_close[0] = close[0]
    prev_close[1:] = close[:-1]
    tr = np.maximum.reduce(
        [
            high - low,
            np.abs(high - prev_close),
            np.abs(low - prev_close),
        ]
    )
    if n < window:
        return out
    cs = np.cumsum(tr, dtype=float)
    out[window - 1] = cs[window - 1] / window
    out[window:] = (cs[window:] - cs[:-window]) / window
    return out


# ---------------------------------------------------------------------------
# Per-bar regime classification
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RegimeSeries:
    """Bar-aligned regime classifier output."""

    sma: np.ndarray  # SMA(close, sma_window)
    atr_norm: np.ndarray  # ATR / close (unitless)
    trend: np.ndarray  # int8: BULL / BEAR / RANGE
    vol: np.ndarray  # int8: HI / LO
    regime: np.ndarray  # int8: 0..5 (REGIME_LABELS index)
    sma_window: int
    atr_window: int
    slope_threshold_pct: float


def regime_per_bar(
    high: Iterable[float],
    low: Iterable[float],
    close: Iterable[float],
    *,
    sma_window: int = 200,
    atr_window: int = 14,
    slope_lookback: int = 50,
    slope_threshold_pct: float = 0.0005,
) -> RegimeSeries:
    """Classify every bar into one of the 6 regime buckets.

    Trend rule:
        slope = (sma[i] - sma[i - slope_lookback]) / sma[i - slope_lookback]
        slope >  threshold → BULL
        slope < -threshold → BEAR
        else               → RANGE

    Vol rule:
        atr_norm[i] = ATR(atr_window)[i] / close[i]
        atr_norm > median(atr_norm)  → HI
        else                          → LO

    Default thresholds replicate the values listed in PLAN.md §4.2.
    """
    h = np.asarray(list(high), dtype=float)
    low_arr = np.asarray(list(low), dtype=float)
    c = np.asarray(list(close), dtype=float)
    n = int(min(h.size, low_arr.size, c.size))
    h, low_arr, c = h[:n], low_arr[:n], c[:n]

    sma = _sma(c, sma_window)
    atr = _atr(h, low_arr, c, atr_window)

    # ATR-normalised — divide by close to make it comparable across
    # contracts whose absolute prices differ by orders of magnitude.
    safe_close = np.where(c > 0, c, np.nan)
    atr_norm = atr / safe_close

    trend = np.full(n, TREND_RANGE, dtype=np.int8)
    if n > slope_lookback and not np.all(np.isnan(sma)):
        ref = np.empty(n, dtype=float)
        ref[:slope_lookback] = np.nan
        ref[slope_lookback:] = sma[: n - slope_lookback]
        # Avoid divide-by-zero where the reference SMA isn't yet defined.
        safe_ref = np.where(np.isfinite(ref) & (np.abs(ref) > 1e-12), ref, np.nan)
        slope = (sma - safe_ref) / safe_ref
        bull = slope > float(slope_threshold_pct)
        bear = slope < -float(slope_threshold_pct)
        trend = np.where(bull, TREND_BULL, trend)
        trend = np.where(bear, TREND_BEAR, trend)

    finite_atr = atr_norm[np.isfinite(atr_norm)]
    median_atr = float(np.median(finite_atr)) if finite_atr.size else 0.0
    vol = np.where(atr_norm > median_atr, VOL_HI, VOL_LO).astype(np.int8)

    regime = np.empty(n, dtype=np.int8)
    for trend_val in (TREND_BULL, TREND_BEAR, TREND_RANGE):
        for vol_val in (VOL_HI, VOL_LO):
            mask = (trend == trend_val) & (vol == vol_val)
            regime[mask] = _encode(trend_val, vol_val)

    return RegimeSeries(
        sma=sma,
        atr_norm=atr_norm,
        trend=trend,
        vol=vol,
        regime=regime,
        sma_window=sma_window,
        atr_window=atr_window,
        slope_threshold_pct=slope_threshold_pct,
    )


# ---------------------------------------------------------------------------
# Trade attribution
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RegimeBreakdownRow:
    regime: str
    n_trades: int
    sum_pnl: float
    win_rate: float  # 0..1
    avg_pnl: float
    n_bars: int  # how many *bars* the regime occupied (denominator hint)


def classify_trades(
    trades: Iterable[Any],
    regime: RegimeSeries,
) -> List[Tuple[int, str]]:
    """Return ``(trade_id, regime_label)`` for each trade.

    The trade is attributed to the regime active at its **entry** bar
    (rather than the exit bar) because that's the regime the strategy
    was actually betting on when the position opened. A long trade that
    flips into a bear regime mid-way still gets credited as having been
    *opened* in the bull regime — that's what the P&L attribution
    question is asking.
    """
    out: List[Tuple[int, str]] = []
    n = regime.regime.size
    for t in trades:
        eb = getattr(t, "entry_bar_index", None)
        if eb is None:
            continue
        eb = int(eb)
        if eb < 0 or eb >= n:
            continue
        out.append(
            (int(getattr(t, "id", 0) or 0), REGIME_LABELS[int(regime.regime[eb])])
        )
    return out


def regime_breakdown(
    trades: Iterable[Any],
    regime: RegimeSeries,
) -> List[RegimeBreakdownRow]:
    """Aggregate per-regime PnL stats for the report's regime section.

    Includes regimes the strategy never traded in (count = 0) so the
    user can spot gaps — useful for confirming "yes my breakout
    strategy correctly avoids range markets".
    """
    n = regime.regime.size
    bins: Dict[str, List[float]] = {label: [] for label in REGIME_LABELS}
    for t in trades:
        eb = getattr(t, "entry_bar_index", None)
        if eb is None:
            continue
        eb = int(eb)
        if eb < 0 or eb >= n:
            continue
        label = REGIME_LABELS[int(regime.regime[eb])]
        bins[label].append(float(getattr(t, "pnl", 0.0) or 0.0))

    bar_counts = {label: 0 for label in REGIME_LABELS}
    if n > 0:
        unique, counts = np.unique(regime.regime, return_counts=True)
        for u, c in zip(unique.tolist(), counts.tolist()):
            bar_counts[REGIME_LABELS[int(u)]] = int(c)

    rows: List[RegimeBreakdownRow] = []
    for label in REGIME_LABELS:
        pnls = np.asarray(bins[label], dtype=float)
        if pnls.size == 0:
            rows.append(
                RegimeBreakdownRow(
                    regime=label,
                    n_trades=0,
                    sum_pnl=0.0,
                    win_rate=0.0,
                    avg_pnl=0.0,
                    n_bars=int(bar_counts[label]),
                )
            )
            continue
        wins = int((pnls > 0).sum())
        rows.append(
            RegimeBreakdownRow(
                regime=label,
                n_trades=int(pnls.size),
                sum_pnl=float(pnls.sum()),
                win_rate=float(wins / pnls.size),
                avg_pnl=float(pnls.mean()),
                n_bars=int(bar_counts[label]),
            )
        )
    return rows


# ---------------------------------------------------------------------------
# Convenience top-level dispatcher used by the report layer
# ---------------------------------------------------------------------------


def regime_report_payload(
    trades: Iterable[Any],
    df: Optional[pd.DataFrame],
) -> Optional[Dict[str, Any]]:
    """Compute the regime payload used by ``engine.report.tearsheet``.

    Returns None when the df is missing / too short (so the report
    section can be skipped gracefully).
    """
    if df is None or len(df) < 50:
        return None
    high = df["high"].to_numpy() if "high" in df.columns else df["High"].to_numpy()
    low = df["low"].to_numpy() if "low" in df.columns else df["Low"].to_numpy()
    close = df["close"].to_numpy() if "close" in df.columns else df["Close"].to_numpy()
    series = regime_per_bar(high, low, close)
    rows = regime_breakdown(trades, series)
    return {"series": series, "rows": rows}
