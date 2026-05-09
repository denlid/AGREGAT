"""Linear structure primitives — Pivots / CISD / FVG over candle bodies only.

The LINEAR LOGIC CORE family of strategies treats wicks as "noise" and
only acts on closes (bodies). This module exposes pure NumPy helpers
that the strategy layer can call from both the visual ``on_candle``
path and the vectorised one without re-implementing the same arithmetic
twice.

All functions are total (i.e. defined for every input length) and
allocate at most one array per call. They are intentionally
stateless — the strategy is responsible for caching the most recent
pivot / CISD / FVG that matters to its logic.

Glossary
========
* **Linear pivot** — a *closed* bar whose body extreme is the local
  extreme over a left and right window of body extremes. We work in
  "body space" (``min(open, close)``, ``max(open, close)``) so single
  long-wick spikes don't create spurious pivots.
* **Linear CISD** ("Close Inside Structural Detail") — the moment the
  most recent close prints **strictly past** the most recent pivot's
  body extreme on the opposite side. ``+1`` = bullish break (close
  above last bear pivot), ``-1`` = bearish break (close below last bull
  pivot).
* **FVG** ("Fair Value Gap") — three-bar pattern measured between
  bar ``i-2`` and bar ``i`` such that ``low[i] > high[i-2]`` (bullish)
  or ``high[i] < low[i-2]`` (bearish). The "Close Only" variant uses
  body extremes instead of full high/low.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

import numpy as np

from engine.core._numba_primitives import (
    HAS_NUMBA,
    _njit_ffill,
    _njit_first_active_fvg_after,
    _njit_last_active_fvg,
    _njit_pivots,
    _njit_pivots_close,
)


def _ffill_arr(a: np.ndarray) -> np.ndarray:
    """Forward-fill NaN in a float64 array. Uses Numba when available."""
    if HAS_NUMBA:
        return _njit_ffill(a)  # type: ignore[no-any-return]
    mask = ~np.isnan(a)
    if not mask.any():
        return a
    idx = np.where(mask, np.arange(len(a)), 0)
    np.maximum.accumulate(idx, out=idx)
    out_arr = a[idx]
    first_valid = int(np.argmax(mask))
    out_arr[:first_valid] = np.nan
    return out_arr


# ---------------------------------------------------------------------------
# Body extremes
# ---------------------------------------------------------------------------


def body_extremes(open_: np.ndarray, close: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """Return (body_low, body_high) per bar — wicks excluded.

    ``body_low`` = ``min(open, close)``, ``body_high`` = ``max(open, close)``.
    Both inputs must be 1-D ``float64`` of equal length; no NaN handling
    is done by design — pass cleaned data.
    """
    o = np.asarray(open_, dtype=np.float64)
    c = np.asarray(close, dtype=np.float64)
    if o.shape != c.shape:
        raise ValueError(f"body_extremes: shape mismatch {o.shape} vs {c.shape}")
    return np.minimum(o, c), np.maximum(o, c)


# ---------------------------------------------------------------------------
# Pivots
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Pivot:
    """A single confirmed pivot.

    ``index``     — bar index where the pivot prints.
    ``price``     — body extreme price (``body_high`` for ``kind="high"``,
                   ``body_low`` for ``kind="low"``).
    ``kind``      — ``"high"`` or ``"low"``.
    """

    index: int
    price: float
    kind: str  # "high" | "low"


def linear_pivots_close(
    close: np.ndarray,
    lookback: int = 1,
) -> Tuple[np.ndarray, np.ndarray]:
    """Return boolean masks of pivot-high / pivot-low bars over **close only**.

    LINEAR STRUCTURE & FVG ALIGNMENT spec — line-chart turning points
    read strictly off ``close`` values, ignoring ``open`` entirely.
    A bar at index ``i`` is a *close-pivot high* when ``close[i]`` is
    strictly greater than every ``close[j]`` with
    ``i-lookback <= j <= i+lookback`` and ``j != i``. ``lookback``
    is on each side, so a value of 1 means the minimal "3-bar pivot"
    (one bar context each side).
    """
    c = np.asarray(close, dtype=np.float64)
    n = c.shape[0]
    is_high = np.zeros(n, dtype=np.bool_)
    is_low = np.zeros(n, dtype=np.bool_)
    if n < 2 * lookback + 1 or lookback < 1:
        return is_high, is_low

    if HAS_NUMBA:
        _njit_pivots_close(c, lookback, is_high, is_low)
    else:
        for i in range(lookback, n - lookback):
            l = i - lookback
            r = i + lookback + 1
            centre = c[i]
            left = c[l:i]
            right = c[i + 1 : r]
            if centre > left.max() and centre > right.max():
                is_high[i] = True
            if centre < left.min() and centre < right.min():
                is_low[i] = True
    return is_high, is_low


def linear_cisd_series_close(
    close: np.ndarray,
    lookback: int = 1,
) -> np.ndarray:
    """Vectorised, per-bar close-only CISD signal as ``int8``.

    Line-chart variant of :func:`linear_cisd_series`: pivots and the
    comparison both use raw ``close``. ``out[i] = +1`` when the close
    at bar ``i`` strictly broke above the most recently confirmed
    close-pivot HIGH, ``-1`` for the bearish symmetry, ``0`` otherwise.

    A pivot at index ``j`` is "confirmed" once ``j + lookback`` bars
    have closed, mirroring :func:`linear_cisd_series` semantics.
    """
    c = np.asarray(close, dtype=np.float64)
    n = c.shape[0]
    out = np.zeros(n, dtype=np.int8)
    if n < 2 * lookback + 2 or lookback < 1:
        return out

    is_high, is_low = linear_pivots_close(c, lookback=lookback)

    last_high_price = np.full(n, np.nan, dtype=np.float64)
    last_low_price = np.full(n, np.nan, dtype=np.float64)

    high_idx = np.flatnonzero(is_high)
    if high_idx.size:
        confirm_pos = high_idx + lookback
        valid = confirm_pos < n
        last_high_price[confirm_pos[valid]] = c[high_idx[valid]]

    low_idx = np.flatnonzero(is_low)
    if low_idx.size:
        confirm_pos = low_idx + lookback
        valid = confirm_pos < n
        last_low_price[confirm_pos[valid]] = c[low_idx[valid]]

    last_high_price = _ffill_arr(last_high_price)
    last_low_price = _ffill_arr(last_low_price)

    bull = (~np.isnan(last_high_price)) & (c > last_high_price)
    bear = (~np.isnan(last_low_price)) & (c < last_low_price)
    out = np.where(bull, np.int8(1), np.where(bear, np.int8(-1), np.int8(0))).astype(np.int8)
    return out


def linear_last_close_pivots(
    close: np.ndarray,
    lookback: int = 1,
) -> Tuple[np.ndarray, np.ndarray]:
    """Return ``(last_high_idx, last_low_idx)`` arrays of length ``n``.

    For every bar ``i``, ``last_high_idx[i]`` is the index ``j`` of the
    most recently confirmed close-pivot HIGH with ``j + lookback <= i``,
    or ``-1`` if none. Symmetric for ``last_low_idx``. Strategies use
    these as O(1) lookups for "what's the latest confirmed structural
    pivot at this bar".
    """
    c = np.asarray(close, dtype=np.float64)
    n = c.shape[0]
    last_high_idx = np.full(n, -1, dtype=np.int64)
    last_low_idx = np.full(n, -1, dtype=np.int64)
    if n < 2 * lookback + 1 or lookback < 1:
        return last_high_idx, last_low_idx

    is_high, is_low = linear_pivots_close(c, lookback=lookback)

    high_pivots = np.flatnonzero(is_high)
    if high_pivots.size:
        confirm_pos = high_pivots + lookback
        valid = confirm_pos < n
        last_high_idx[confirm_pos[valid]] = high_pivots[valid]
    low_pivots = np.flatnonzero(is_low)
    if low_pivots.size:
        confirm_pos = low_pivots + lookback
        valid = confirm_pos < n
        last_low_idx[confirm_pos[valid]] = low_pivots[valid]

    # Forward-fill the last seen index.
    for k in range(1, n):
        if last_high_idx[k] == -1:
            last_high_idx[k] = last_high_idx[k - 1]
        if last_low_idx[k] == -1:
            last_low_idx[k] = last_low_idx[k - 1]
    return last_high_idx, last_low_idx


def linear_cisd_last_close(
    close: np.ndarray,
    lookback: int = 1,
) -> int:
    """Return ``+1`` / ``-1`` / ``0`` — close-only CISD signal at last bar.

    Visual-path counterpart of :func:`linear_cisd_series_close`. Reads
    only ``close``; ignores ``open`` entirely.
    """
    c = np.asarray(close, dtype=np.float64)
    n = c.shape[0]
    if n < 2 * lookback + 2 or lookback < 1:
        return 0
    is_high, is_low = linear_pivots_close(c, lookback=lookback)
    cutoff = n - lookback  # pivots at j with j + lookback <= n - 1
    if cutoff <= 0:
        return 0
    # Most recent confirmed pivot HIGH / LOW.
    last_high_price: Optional[float] = None
    last_low_price: Optional[float] = None
    for i in range(cutoff - 1, -1, -1):
        if last_high_price is None and is_high[i]:
            last_high_price = float(c[i])
        if last_low_price is None and is_low[i]:
            last_low_price = float(c[i])
        if last_high_price is not None and last_low_price is not None:
            break
    last_close = float(c[-1])
    if last_high_price is not None and last_close > last_high_price:
        return +1
    if last_low_price is not None and last_close < last_low_price:
        return -1
    return 0


def last_close_pivots(
    close: np.ndarray,
    lookback: int = 1,
) -> Tuple[Optional[Pivot], Optional[Pivot]]:
    """Return ``(last_pivot_high, last_pivot_low)`` confirmed at the last
    bar, working over **closes only** (line-chart structure).

    Mirrors :func:`last_pivots` but uses :func:`linear_pivots_close`.
    """
    c = np.asarray(close, dtype=np.float64)
    n = c.shape[0]
    if n < 2 * lookback + 1 or lookback < 1:
        return None, None
    is_high, is_low = linear_pivots_close(c, lookback=lookback)
    cutoff = n - lookback
    if cutoff <= 0:
        return None, None
    last_high: Optional[Pivot] = None
    for i in range(cutoff - 1, -1, -1):
        if is_high[i]:
            last_high = Pivot(index=int(i), price=float(c[i]), kind="high")
            break
    last_low: Optional[Pivot] = None
    for i in range(cutoff - 1, -1, -1):
        if is_low[i]:
            last_low = Pivot(index=int(i), price=float(c[i]), kind="low")
            break
    return last_high, last_low


def linear_pivots(
    open_: np.ndarray,
    close: np.ndarray,
    lookback: int = 3,
) -> Tuple[np.ndarray, np.ndarray]:
    """Return boolean masks of pivot-high / pivot-low bars over body extremes.

    A bar at index ``i`` is a *pivot high* when ``body_high[i]`` is
    strictly greater than every ``body_high[j]`` with
    ``i-lookback <= j <= i+lookback`` and ``j != i``. Pivot lows are the
    symmetric definition on ``body_low``. The first and last
    ``lookback`` bars are never pivots (insufficient context).

    ``lookback`` is on *each side*, so a value of 3 means "5-bar pivot"
    in TradingView nomenclature. The default of 3 mirrors the typical
    LINEAR LOGIC CORE structural granularity on 15m charts.
    """
    n = len(close)
    is_high = np.zeros(n, dtype=bool)
    is_low = np.zeros(n, dtype=bool)
    if n < 2 * lookback + 1 or lookback < 1:
        return is_high, is_low

    body_lo, body_hi = body_extremes(open_, close)

    window = 2 * lookback + 1
    if n < window:
        return is_high, is_low

    if HAS_NUMBA:
        _njit_pivots(body_hi, body_lo, lookback, is_high, is_low)
    else:
        for i in range(lookback, n - lookback):
            l = i - lookback
            r = i + lookback + 1
            bh_window = body_hi[l:r]
            bl_window = body_lo[l:r]
            centre_hi = body_hi[i]
            centre_lo = body_lo[i]
            left_hi = bh_window[:lookback]
            right_hi = bh_window[lookback + 1 :]
            if centre_hi > left_hi.max() and centre_hi > right_hi.max():
                is_high[i] = True
            left_lo = bl_window[:lookback]
            right_lo = bl_window[lookback + 1 :]
            if centre_lo < left_lo.min() and centre_lo < right_lo.min():
                is_low[i] = True

    return is_high, is_low


def last_pivots(
    open_: np.ndarray,
    close: np.ndarray,
    lookback: int = 3,
    *,
    confirm_offset: int = 0,
) -> Tuple[Optional[Pivot], Optional[Pivot]]:
    """Return ``(last_pivot_high, last_pivot_low)`` confirmed up to now.

    A pivot at index ``i`` only becomes *confirmed* once ``i + lookback``
    bars have closed (we needed ``lookback`` bars on the right side to
    rule out a higher body high). With ``confirm_offset=0`` (default)
    we use every confirmed pivot up to and including index
    ``len(close) - 1 - lookback``. Set ``confirm_offset=lookback`` if
    you want the strictest interpretation (no overlap with the current
    bar).
    """
    is_high, is_low = linear_pivots(open_, close, lookback=lookback)
    n = len(close)
    cutoff = n - max(lookback, 0) - max(confirm_offset, 0)
    if cutoff <= 0:
        return None, None

    body_lo, body_hi = body_extremes(open_, close)

    last_high: Optional[Pivot] = None
    for i in range(cutoff - 1, -1, -1):
        if is_high[i]:
            last_high = Pivot(index=int(i), price=float(body_hi[i]), kind="high")
            break

    last_low: Optional[Pivot] = None
    for i in range(cutoff - 1, -1, -1):
        if is_low[i]:
            last_low = Pivot(index=int(i), price=float(body_lo[i]), kind="low")
            break

    return last_high, last_low


# ---------------------------------------------------------------------------
# Linear CISD ("Close Inside Structural Detail")
# ---------------------------------------------------------------------------


def linear_cisd_last(
    open_: np.ndarray,
    close: np.ndarray,
    lookback: int = 3,
) -> int:
    """Return the latest CISD signal as ``+1`` / ``-1`` / ``0``.

    The signal fires on the **most recently closed bar**:

    * ``+1`` — the last close printed strictly above the most recently
      confirmed pivot-high body extreme (bullish break).
    * ``-1`` — the last close printed strictly below the most recently
      confirmed pivot-low body extreme (bearish break).
    * ``0``  — neither.

    Only the latest bar is inspected so this helper is O(N) once for
    pivot detection and O(1) thereafter; strategies call it once per
    higher-TF close.
    """
    n = len(close)
    if n == 0:
        return 0
    last_close = float(close[-1])
    last_high, last_low = last_pivots(open_, close, lookback=lookback)
    if last_high is not None and last_close > last_high.price:
        return +1
    if last_low is not None and last_close < last_low.price:
        return -1
    return 0


def linear_cisd_series(
    open_: np.ndarray,
    close: np.ndarray,
    lookback: int = 3,
) -> np.ndarray:
    """Vectorised, **per-bar** CISD signal as ``int8`` of length ``n``.

    For every bar ``i``, ``out[i]`` is what :func:`linear_cisd_last`
    would have returned if called on ``open_[: i + 1]`` /
    ``close[: i + 1]`` — i.e. the last-bar CISD signal as the bar
    closed. The semantics match :func:`linear_cisd_last` exactly:
    a pivot at index ``j`` becomes usable for the comparison once
    ``j + lookback`` bars have closed.

    Complexity: ``O(n × lookback)`` (one full pivot scan + linear
    forward-fill). Strategies that previously called
    ``linear_cisd_last(arr[: i + 1])`` inside a per-bar loop can drop
    to ``O(n)`` total by precomputing this series once and indexing
    by ``i``.
    """
    n = len(close)
    out = np.zeros(n, dtype=np.int8)
    if n < 2 * lookback + 2 or lookback < 1:
        return out

    is_high, is_low = linear_pivots(open_, close, lookback=lookback)
    body_lo, body_hi = body_extremes(open_, close)
    cl = np.asarray(close, dtype=np.float64)

    # Place each pivot's price at the bar where it becomes "usable" —
    # ``pivot_idx + lookback`` (need ``lookback`` bars on the right
    # side to confirm). Then forward-fill so every later bar carries
    # the latest usable pivot price.
    last_high_price = np.full(n, np.nan, dtype=np.float64)
    last_low_price = np.full(n, np.nan, dtype=np.float64)

    high_idx = np.flatnonzero(is_high)
    if high_idx.size:
        confirm_pos = high_idx + lookback
        valid = confirm_pos < n
        last_high_price[confirm_pos[valid]] = body_hi[high_idx[valid]]

    low_idx = np.flatnonzero(is_low)
    if low_idx.size:
        confirm_pos = low_idx + lookback
        valid = confirm_pos < n
        last_low_price[confirm_pos[valid]] = body_lo[low_idx[valid]]

    last_high_price = _ffill_arr(last_high_price)
    last_low_price = _ffill_arr(last_low_price)

    bull = (~np.isnan(last_high_price)) & (cl > last_high_price)
    bear = (~np.isnan(last_low_price)) & (cl < last_low_price)
    # Bullish takes precedence when both are true (rare on the same
    # bar but possible with extreme volatility); ``linear_cisd_last``
    # checks bullish first so we match.
    out = np.where(bull, np.int8(1), np.where(bear, np.int8(-1), np.int8(0))).astype(np.int8)
    return out


# ---------------------------------------------------------------------------
# Fair Value Gap
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FVG:
    """A single Fair Value Gap.

    ``index``      — bar index where the gap printed (i.e. ``i`` in the
                    three-bar definition; the gap exists between
                    ``i-2`` and ``i``).
    ``direction``  — ``+1`` bullish (gap above prior body), ``-1`` bearish.
    ``low``/``high``— price extremes of the gap zone (inclusive).
    """

    index: int
    direction: int  # +1 bullish, -1 bearish
    low: float
    high: float


def fvg_close_only(
    open_: np.ndarray,
    close: np.ndarray,
) -> np.ndarray:
    """Return per-bar FVG direction array (``+1`` / ``-1`` / ``0``).

    Computed in body space — wicks are ignored. A bar at index ``i``
    has a bullish FVG when ``body_low[i] > body_high[i-2]`` (strict)
    and a bearish FVG when ``body_high[i] < body_low[i-2]``. ``0``
    elsewhere. The first two bars are always ``0``.
    """
    n = len(close)
    out = np.zeros(n, dtype=np.int8)
    if n < 3:
        return out
    body_lo, body_hi = body_extremes(open_, close)
    bull = body_lo[2:] > body_hi[:-2]
    bear = body_hi[2:] < body_lo[:-2]
    out[2:] = np.where(bull, 1, np.where(bear, -1, 0))
    return out


def last_active_fvg(
    open_: np.ndarray,
    close: np.ndarray,
    *,
    direction: int,
    max_lookback: int = 80,
) -> Optional[FVG]:
    """Return the most recent FVG of the requested direction that has
    not yet been *invalidated* by a subsequent close inside the gap.

    The gap zone for a bullish FVG (``direction=+1``) is
    ``[body_high[i-2], body_low[i]]``; the FVG is considered alive as
    long as no subsequent body has closed below ``body_high[i-2]``
    (the lower edge). Symmetric for bearish.

    ``max_lookback`` caps how far back we search so a long-running
    backtest with thousands of historical FVGs doesn't scan from the
    start of time on every call.
    """
    if direction not in (+1, -1):
        raise ValueError("direction must be +1 or -1")
    n = len(close)
    if n < 3:
        return None
    body_lo, body_hi = body_extremes(open_, close)
    fvg_dir = fvg_close_only(open_, close)
    c = np.asarray(close, dtype=np.float64)

    start = max(2, n - max_lookback)

    if HAS_NUMBA:
        idx = _njit_last_active_fvg(body_hi, body_lo, c, fvg_dir, direction, start, n)
        if idx < 0:
            return None
        if direction == +1:
            return FVG(
                index=idx, direction=+1, low=float(body_hi[idx - 2]), high=float(body_lo[idx])
            )
        else:
            return FVG(
                index=idx, direction=-1, low=float(body_hi[idx]), high=float(body_lo[idx - 2])
            )

    for i in range(n - 1, start - 1, -1):
        if int(fvg_dir[i]) != direction:
            continue
        if direction == +1:
            gap_low = float(body_hi[i - 2])
            gap_high = float(body_lo[i])
            if gap_high <= gap_low:
                continue
            if i + 1 < n and (c[i + 1 :] < gap_low).any():
                continue
            return FVG(index=int(i), direction=+1, low=gap_low, high=gap_high)
        else:
            gap_high = float(body_lo[i - 2])
            gap_low = float(body_hi[i])
            if gap_high <= gap_low:
                continue
            if i + 1 < n and (c[i + 1 :] > gap_high).any():
                continue
            return FVG(index=int(i), direction=-1, low=gap_low, high=gap_high)
    return None


# ---------------------------------------------------------------------------
# Standard (high/low) 3-bar FVG helpers — wicks INCLUDED.
#
# The body-only variants above are used by LINEAR LOGIC CORE; the
# LINEAR STRUCTURE & FVG ALIGNMENT family wants the canonical SMC
# definition where the gap is measured over wicks (the impulsive
# imbalance that the structural close ignored). These helpers are
# additive — existing callers are untouched.
# ---------------------------------------------------------------------------


def fvg_high_low(high: np.ndarray, low: np.ndarray) -> np.ndarray:
    """Per-bar standard 3-bar FVG direction array (``+1`` / ``-1`` / ``0``).

    A bar at index ``i`` has a *bullish* FVG when ``low[i] > high[i-2]``
    (a gap above the prior bar's high), and a *bearish* FVG when
    ``high[i] < low[i-2]``. The first two bars are always ``0``.

    Inputs are 1-D ``float64`` of equal length (``high``/``low`` from
    the OHLC frame, **wicks included**). No NaN handling — caller
    passes cleaned data.
    """
    h = np.asarray(high, dtype=np.float64)
    lo = np.asarray(low, dtype=np.float64)
    n = len(h)
    if h.shape != lo.shape:
        raise ValueError(f"fvg_high_low: shape mismatch {h.shape} vs {lo.shape}")
    out = np.zeros(n, dtype=np.int8)
    if n < 3:
        return out
    bull = lo[2:] > h[:-2]
    bear = h[2:] < lo[:-2]
    out[2:] = np.where(bull, 1, np.where(bear, -1, 0))
    return out


def first_active_fvg_after(
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    *,
    direction: int,
    min_index: int,
    max_lookahead: int = 200,
    invalidate_on: str = "body",
) -> Optional[FVG]:
    """First not-yet-invalidated standard FVG forming **strictly after** ``min_index``.

    Walks indices ``i`` in ``(min_index, min(n-1, min_index + max_lookahead)]``
    *oldest-first* (the opposite of :func:`last_active_fvg`, which
    walks newest-first). The first FVG of the requested direction
    that has not yet been invalidated is returned; ``None`` if none.

    Invalidation rule (controlled by ``invalidate_on``):
      * ``invalidate_on="body"`` (default, back-compat) — the gap dies
        when ``max(open, close) < gap_low`` (bull) /
        ``min(open, close) > gap_high`` (bear). Wick-only spikes do
        not invalidate.
      * ``invalidate_on="close"`` (LINEAR STRUCTURE & FVG ALIGNMENT
        spec) — the gap dies when ``close < gap_low`` (bull) /
        ``close > gap_high`` (bear). Pure line-chart logic — open
        is ignored entirely.

    The returned :class:`FVG` carries the gap zone in ``low`` / ``high``
    fields regardless of direction (``low <= high`` always).
    """
    if direction not in (+1, -1):
        raise ValueError("direction must be +1 or -1")
    if invalidate_on not in ("body", "close"):
        raise ValueError("invalidate_on must be 'body' or 'close'")
    n = len(close)
    if n < 3:
        return None

    h = np.asarray(high, dtype=np.float64)
    lo = np.asarray(low, dtype=np.float64)
    o = np.asarray(open_, dtype=np.float64)
    c = np.asarray(close, dtype=np.float64)

    if invalidate_on == "body":
        bull_check = np.minimum(o, c)  # body_lo, used for bear (min above gap_high)
        bear_check = np.maximum(o, c)  # body_hi, used for bull (max below gap_low)
        # Note: bull invalidates when body_hi < gap_low; bear when body_lo > gap_high.
        bull_inv = bear_check  # max(o,c)
        bear_inv = bull_check  # min(o,c)
    else:  # "close"
        bull_inv = c
        bear_inv = c

    start = max(min_index + 1, 2)
    end = min(n - 1, min_index + max_lookahead)
    if end < start:
        return None

    if HAS_NUMBA:
        idx = _njit_first_active_fvg_after(h, lo, bull_inv, bear_inv, direction, start, end, n)
        if idx < 0:
            return None
        if direction == +1:
            return FVG(index=idx, direction=+1, low=float(h[idx - 2]), high=float(lo[idx]))
        else:
            return FVG(index=idx, direction=-1, low=float(h[idx]), high=float(lo[idx - 2]))

    for i in range(start, end + 1):
        if direction == +1:
            gap_low = float(h[i - 2])
            gap_high = float(lo[i])
            if gap_high <= gap_low:
                continue
            if i + 1 < n and (bull_inv[i + 1 :] < gap_low).any():
                continue
            return FVG(index=int(i), direction=+1, low=gap_low, high=gap_high)
        else:
            gap_high = float(lo[i - 2])
            gap_low = float(h[i])
            if gap_high <= gap_low:
                continue
            if i + 1 < n and (bear_inv[i + 1 :] > gap_high).any():
                continue
            return FVG(index=int(i), direction=-1, low=gap_low, high=gap_high)
    return None


__all__ = [
    "Pivot",
    "FVG",
    "body_extremes",
    "linear_pivots",
    "last_pivots",
    "linear_cisd_last",
    "linear_cisd_series",
    "linear_pivots_close",
    "last_close_pivots",
    "linear_cisd_last_close",
    "linear_cisd_series_close",
    "linear_last_close_pivots",
    "fvg_close_only",
    "last_active_fvg",
    "fvg_high_low",
    "first_active_fvg_after",
]
