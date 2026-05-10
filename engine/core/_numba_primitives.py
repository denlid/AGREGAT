"""Sprint 3.1 — Numba-accelerated inner loops for linear-structure primitives.

This module provides ``@njit``-compiled implementations of the heavy
inner loops used by :mod:`engine.core.linear`. Each function has a
pure-NumPy fallback that is semantically identical, so ``numba`` is a
*soft* dependency: if it's not installed the public helpers in
``linear.py`` transparently fall back to the pure-NumPy versions.

The module is designed for import-time JIT compilation via
``numba.njit(cache=True)`` so the first call incurs ~200 ms of
compilation but subsequent calls (even across interpreter restarts, if
the cache directory is stable) run at native speed.

Functions exported
------------------
* ``_njit_pivots_close`` — inner loop of :func:`linear_pivots_close`.
* ``_njit_pivots`` — inner loop of :func:`linear_pivots`.
* ``_njit_ffill`` — forward-fill NaN values in a float64 array.
* ``_njit_last_active_fvg`` — inner loop of :func:`last_active_fvg`.
* ``_njit_first_active_fvg_after`` — inner loop of :func:`first_active_fvg_after`.

The ``HAS_NUMBA`` flag is ``True`` when the JIT'd versions are
available.
"""

from __future__ import annotations

import numpy as np

HAS_NUMBA = False

try:
    from numba import njit  # type: ignore[import-untyped]

    HAS_NUMBA = True
except ImportError:  # pragma: no cover
    # Numba not installed — define a no-op decorator so the decorated
    # functions still work as plain Python (slower but correct).
    def njit(*args, **kwargs):  # type: ignore[no-redef]
        """Identity decorator when numba is not available."""
        if args and callable(args[0]):
            return args[0]
        return lambda fn: fn


# -----------------------------------------------------------------------
# linear_pivots_close — close-only pivot detection
# -----------------------------------------------------------------------
@njit(cache=True)
def _njit_pivots_close(
    close: np.ndarray,
    lookback: int,
    is_high: np.ndarray,
    is_low: np.ndarray,
) -> None:
    """Write pivot-high / pivot-low booleans into pre-allocated arrays."""
    n = close.shape[0]
    for i in range(lookback, n - lookback):
        centre = close[i]
        is_h = True
        is_l = True
        for j in range(i - lookback, i):
            if close[j] >= centre:
                is_h = False
            if close[j] <= centre:
                is_l = False
        for j in range(i + 1, i + lookback + 1):
            if close[j] >= centre:
                is_h = False
            if close[j] <= centre:
                is_l = False
        is_high[i] = is_h
        is_low[i] = is_l


# -----------------------------------------------------------------------
# linear_pivots — body-extreme pivot detection
# -----------------------------------------------------------------------
@njit(cache=True)
def _njit_pivots(
    body_hi: np.ndarray,
    body_lo: np.ndarray,
    lookback: int,
    is_high: np.ndarray,
    is_low: np.ndarray,
) -> None:
    """Write pivot-high / pivot-low booleans over body extremes."""
    n = body_hi.shape[0]
    for i in range(lookback, n - lookback):
        centre_hi = body_hi[i]
        centre_lo = body_lo[i]
        h_ok = True
        l_ok = True
        for j in range(i - lookback, i):
            if body_hi[j] >= centre_hi:
                h_ok = False
            if body_lo[j] <= centre_lo:
                l_ok = False
        for j in range(i + 1, i + lookback + 1):
            if body_hi[j] >= centre_hi:
                h_ok = False
            if body_lo[j] <= centre_lo:
                l_ok = False
        is_high[i] = h_ok
        is_low[i] = l_ok


# -----------------------------------------------------------------------
# _ffill — forward-fill NaN in float64 arrays
# -----------------------------------------------------------------------
@njit(cache=True)
def _njit_ffill(a: np.ndarray) -> np.ndarray:
    """Return a copy of *a* with NaN values forward-filled."""
    n = a.shape[0]
    out = np.empty(n, dtype=np.float64)
    last = np.nan
    for i in range(n):
        if not np.isnan(a[i]):
            last = a[i]
        out[i] = last
    return out


# -----------------------------------------------------------------------
# last_active_fvg — newest active FVG scan
# -----------------------------------------------------------------------
@njit(cache=True)
def _njit_last_active_fvg(
    body_hi: np.ndarray,
    body_lo: np.ndarray,
    close: np.ndarray,
    fvg_dir: np.ndarray,
    direction: int,
    start: int,
    n: int,
) -> int:
    """Return the bar index of the last active FVG, or ``-1``."""
    for i in range(n - 1, start - 1, -1):
        if fvg_dir[i] != direction:
            continue
        if direction == 1:
            gap_low = body_hi[i - 2]
            gap_high = body_lo[i]
            if gap_high <= gap_low:
                continue
            invalidated = False
            for k in range(i + 1, n):
                if close[k] < gap_low:
                    invalidated = True
                    break
            if invalidated:
                continue
            return i
        else:
            gap_high = body_lo[i - 2]
            gap_low = body_hi[i]
            if gap_high <= gap_low:
                continue
            invalidated = False
            for k in range(i + 1, n):
                if close[k] > gap_high:
                    invalidated = True
                    break
            if invalidated:
                continue
            return i
    return -1


# -----------------------------------------------------------------------
# first_active_fvg_after — oldest active FVG after a given bar
# -----------------------------------------------------------------------
@njit(cache=True)
def _njit_first_active_fvg_after(
    high: np.ndarray,
    low: np.ndarray,
    bull_inv: np.ndarray,
    bear_inv: np.ndarray,
    direction: int,
    start: int,
    end: int,
    n: int,
) -> int:
    """Return the bar index of the first active FVG after ``start``, or ``-1``."""
    for i in range(start, end + 1):
        if direction == 1:
            gap_low = high[i - 2]
            gap_high = low[i]
            if gap_high <= gap_low:
                continue
            invalidated = False
            for k in range(i + 1, n):
                if bull_inv[k] < gap_low:
                    invalidated = True
                    break
            if invalidated:
                continue
            return i
        else:
            gap_high = low[i - 2]
            gap_low = high[i]
            if gap_high <= gap_low:
                continue
            invalidated = False
            for k in range(i + 1, n):
                if bear_inv[k] > gap_high:
                    invalidated = True
                    break
            if invalidated:
                continue
            return i
    return -1
