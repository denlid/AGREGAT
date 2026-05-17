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


# ---------------------------------------------------------------------------
# Phase 2.2 — time-aware resampler (LOD pyramid)
# ---------------------------------------------------------------------------

# Mapping of human-readable target timeframes to polars duration strings.
# Mirrors ``TimeframeAggregator.TF_MINUTES`` plus daily / weekly which the
# engine aggregator doesn't model but the chart's "show full history"
# mode would want.
_TF_TO_POLARS_DURATION = {
    "1m": "1m",
    "2m": "2m",
    "3m": "3m",
    "5m": "5m",
    "15m": "15m",
    "30m": "30m",
    "1h": "1h",
    "4h": "4h",
    "1d": "1d",
    "1w": "1w",
}


def downsample_ohlc_polars(df, target_tf: str):  # type: ignore[no-untyped-def]
    """Resample a 1m OHLC(V) ``polars.DataFrame`` to a higher timeframe.

    Phase 2.2 (`optimization_roadmap.md`) — provides the time-aware LOD
    pyramid the roadmap calls for. Where :func:`downsample_ohlc` collapses
    by *count* (``N → max_bars``), this collapses by *time* (``M1 → H1 /
    D1 / W1``) using polars' :py:meth:`group_by_dynamic`.

    Returned aggregates follow standard OHLC semantics:

    * ``open``  — first 1m open in the bucket
    * ``high``  — max 1m high
    * ``low``   — min 1m low
    * ``close`` — last 1m close
    * ``volume`` — sum (if present)
    * ``spread`` — mean (if present, matches the engine aggregator)

    Parameters
    ----------
    df
        ``polars.DataFrame`` with a ``time`` column (``Datetime``) and
        ``open``/``high``/``low``/``close`` columns. ``volume`` and
        ``spread`` are aggregated if present.
    target_tf
        One of ``"1m"``, ``"2m"``, ``"3m"``, ``"5m"``, ``"15m"``,
        ``"30m"``, ``"1h"``, ``"4h"``, ``"1d"``, ``"1w"``. Anything
        else raises ``ValueError`` — the caller is expected to pick
        a TF from the supported ladder.

    Returns
    -------
    polars.DataFrame
        Same column layout as the input, but each row is one
        higher-TF bar. Empty input returns an empty frame with the
        same schema.

    Notes
    -----
    This helper is intentionally *not* wired into ``ChartTab`` yet —
    the chart currently only feeds a 300-bar rolling window
    (``RENDER_WINDOW`` in :mod:`gui.chart_tab.tab`) and so doesn't
    benefit from time-aware LOD today. The function is here ready
    for the planned "show full history" viewer mode where the user
    zooms out across the entire dataset and the visible span dictates
    the target TF.

    The dependency on polars is local (import at call time) so unit
    tests for the rest of :mod:`gui.chart_tab.lod` keep running even
    on environments without polars installed.
    """
    if target_tf not in _TF_TO_POLARS_DURATION:
        raise ValueError(
            f"downsample_ohlc_polars: unsupported target_tf={target_tf!r}. "
            f"Allowed: {sorted(_TF_TO_POLARS_DURATION)}"
        )

    import polars as pl  # local import — keeps polars optional for the rest of the module

    if df.height == 0:
        return df

    if "time" not in df.columns:
        raise ValueError("downsample_ohlc_polars: input frame must have a 'time' column")

    duration = _TF_TO_POLARS_DURATION[target_tf]
    aggs = [
        pl.col("open").first().alias("open"),
        pl.col("high").max().alias("high"),
        pl.col("low").min().alias("low"),
        pl.col("close").last().alias("close"),
    ]
    if "volume" in df.columns:
        aggs.append(pl.col("volume").sum().alias("volume"))
    if "spread" in df.columns:
        # Mean spread per bucket — matches the engine TimeframeAggregator
        # behaviour at ``engine/data/tf_aggregator.py:262``.
        aggs.append(pl.col("spread").mean().alias("spread"))

    # ``group_by_dynamic`` requires the index column to be sorted; the
    # 1m loader guarantees this but be defensive — a hidden out-of-
    # order timestamp would silently produce wrong aggregates.
    sorted_df = df.sort("time") if not df["time"].is_sorted() else df

    return sorted_df.group_by_dynamic(
        index_column="time",
        every=duration,
        period=duration,
        closed="left",
        label="left",
    ).agg(aggs)


def choose_target_tf(visible_seconds: float, max_bars: int = DEFAULT_MAX_BARS) -> str:
    """Pick a target timeframe for the LOD pyramid given a visible
    time span and a maximum bar budget.

    Walks the supported TF ladder from finest to coarsest and returns
    the smallest TF whose bar count for ``visible_seconds`` fits under
    ``max_bars``. The caller then feeds that string to
    :func:`downsample_ohlc_polars`.

    Examples
    --------
    >>> # 10 years × 365 d × 24 h × 3600 s ≈ 3.15e8 s.
    >>> # 1m needs 5.25M bars > 2K → climb the ladder.
    >>> choose_target_tf(10 * 365 * 24 * 3600)
    '1w'
    """
    if visible_seconds <= 0:
        return "1m"
    ladder_seconds = [
        ("1m", 60),
        ("2m", 120),
        ("3m", 180),
        ("5m", 300),
        ("15m", 900),
        ("30m", 1800),
        ("1h", 3600),
        ("4h", 14400),
        ("1d", 86400),
        ("1w", 7 * 86400),
    ]
    budget = max(1, int(max_bars))
    for tf, secs in ladder_seconds:
        if visible_seconds / secs <= budget:
            return tf
    return "1w"


__all__ = [
    "downsample_ohlc",
    "downsample_xy",
    "downsample_ohlc_polars",
    "choose_target_tf",
    "DEFAULT_MAX_BARS",
]
