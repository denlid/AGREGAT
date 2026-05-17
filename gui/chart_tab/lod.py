"""Phase 2.3 — Level-of-Detail (LOD) downsampling for the chart.

When the user zooms out to see thousands of candles, drawing every bar
individually scales linearly with bar count and turns pan/zoom into a
visible lag. This module provides a small, allocation-conscious
downsampler that bins consecutive OHLC bars into an *aggregate* OHLC
bar that preserves the visual envelope:

* ``open``  — the first bar's open in the bin (visual continuity)
* ``close`` — the last bar's close in the bin (visual continuity)
* ``high``  — the max high across the bin (peak preservation)
* ``low``   — the min low across the bin (trough preservation)
* ``time`` index — the *first* bar's idx (so the candle position stays
  on a stable grid as the user scrolls)

The transformation is a strict generalisation of pyqtgraph's
``setDownsampling(mode='peak')`` extended to OHLC tuples — the user
never loses sight of a wick because the downsampled bar always covers
[min(low), max(high)] of the source range.

The module is intentionally pure-Python (NumPy under the hood) — no Qt
dependency — so it can be unit-tested headless without a display.
"""

from __future__ import annotations

from typing import List, Sequence, Tuple

import numpy as np

# Default target — pyqtgraph's anti-aliased line draw stays smooth up
# to roughly 2 000 vertical lines per ViewBox; we hold that as the
# "max visible candles" upper bound. The chart tab passes its own
# value via ``max_bars`` if a different cap is wanted.
DEFAULT_MAX_BARS = 2_000

OhlcTuple = Tuple[float, float, float, float, float]
"""``(idx, open, close, low, high)`` as accepted by :class:`gui.chart_tab.items.CandlestickItem`."""


def downsample_ohlc(
    data: Sequence[OhlcTuple], max_bars: int = DEFAULT_MAX_BARS
) -> List[OhlcTuple]:
    """Return ``data`` collapsed to at most ``max_bars`` aggregate bars.

    Parameters
    ----------
    data
        Iterable of ``(idx, open, close, low, high)`` 5-tuples — same
        layout the candlestick item already accepts.
    max_bars
        Upper bound on the number of returned bars. Must be ≥ 1; the
        function quietly clamps to at least 1.

    Returns
    -------
    list of OhlcTuple
        The downsampled bars, length ≤ ``max_bars``. When the input is
        already small enough the input is returned untouched (cheap
        early-out — no copy).

    Notes
    -----
    The bin size is ``ceil(N / max_bars)``; the last bin may be
    smaller. Open / close are taken from the first / last bar of the
    bin so visual continuity is exact at bin boundaries.
    """
    if max_bars < 1:
        max_bars = 1
    n = len(data)
    if n <= max_bars:
        # Avoid the round-trip to NumPy in the common live-chart case
        # (RENDER_WINDOW = 300 ≪ 2 000), so the LOD path is genuinely
        # zero-cost when not needed.
        return list(data) if not isinstance(data, list) else data

    arr = np.asarray(data, dtype=np.float64)
    # arr columns: 0=idx, 1=open, 2=close, 3=low, 4=high
    bin_size = int(np.ceil(n / max_bars))
    n_bins = int(np.ceil(n / bin_size))

    out: List[OhlcTuple] = [(0.0, 0.0, 0.0, 0.0, 0.0)] * n_bins
    for k in range(n_bins):
        a = k * bin_size
        b = min(a + bin_size, n)
        chunk = arr[a:b]
        idx = float(chunk[0, 0])
        op = float(chunk[0, 1])
        cl = float(chunk[-1, 2])
        lo = float(chunk[:, 3].min())
        hi = float(chunk[:, 4].max())
        out[k] = (idx, op, cl, lo, hi)
    return out


def downsample_xy(
    x: np.ndarray, y: np.ndarray, max_points: int = DEFAULT_MAX_BARS
) -> Tuple[np.ndarray, np.ndarray]:
    """Decimate parallel ``(x, y)`` arrays to at most ``max_points``
    samples preserving local peaks/troughs.

    Used for indicator lines / equity curves / the price line so the
    same LOD threshold applies regardless of the underlying series.
    Returns ``(x, y)`` views/copies — never modifies inputs.
    """
    if max_points < 2:
        max_points = 2
    n = int(min(x.size, y.size))
    if n <= max_points:
        return x, y

    # Bin into groups of ``bin_size`` and emit the (min, max) pair from
    # each — same trick pyqtgraph uses for ``mode='peak'``. We emit two
    # samples per bin so the cap on bin count is ``max_points // 2``.
    half_bins = max(1, max_points // 2)
    bin_size = int(np.ceil(n / half_bins))
    # Clip to a multiple of bin_size for the vectorised path; trail
    # what's left as a final partial bin.
    full_n = (n // bin_size) * bin_size
    if full_n == 0:
        return x[:n], y[:n]

    y_full = y[:full_n].reshape(-1, bin_size)
    x_full = x[:full_n].reshape(-1, bin_size)
    mins = y_full.argmin(axis=1)
    maxs = y_full.argmax(axis=1)
    rows = np.arange(y_full.shape[0])
    # Interleave (min, max) per bin in original positional order so
    # the line shape is preserved (no zig-zag artefacts).
    pos_min = mins
    pos_max = maxs
    first = np.minimum(pos_min, pos_max)
    second = np.maximum(pos_min, pos_max)
    x_out_first = x_full[rows, first]
    y_out_first = y_full[rows, first]
    x_out_second = x_full[rows, second]
    y_out_second = y_full[rows, second]
    x_out = np.empty(2 * y_full.shape[0], dtype=x.dtype)
    y_out = np.empty_like(x_out, dtype=y.dtype)
    x_out[0::2] = x_out_first
    y_out[0::2] = y_out_first
    x_out[1::2] = x_out_second
    y_out[1::2] = y_out_second

    # Append the trailing partial bin (if any) verbatim — its size is
    # smaller than the LOD threshold, so emitting all its samples is
    # cheap and avoids edge-of-chart artefacts.
    if full_n < n:
        x_out = np.concatenate([x_out, x[full_n:n]])
        y_out = np.concatenate([y_out, y[full_n:n]])

    return x_out, y_out


__all__ = ["downsample_ohlc", "downsample_xy", "DEFAULT_MAX_BARS"]
