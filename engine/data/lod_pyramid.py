"""Phase 2 — LOD pyramid for the chart.

Builds and caches a multi-timeframe view of an OHLCV dataset
(M1 → M5, M15, H1, D1, optionally W1) so the chart can swap to a
coarser timeframe when the user zooms out far enough that drawing
every 1m candle would tank the paint loop.

The pyramid is built once after :meth:`DataLoader.load_csv` via Polars'
:py:meth:`group_by_dynamic` (~30 ms on a 1M-bar dataset thanks to
Polars' Rust core). Memory cost is bounded because each level shrinks
by ~5× (M1→M5) / 15× (M1→M15) / 60× (M1→H1) / 1440× (M1→D1), so even a
10M-bar feed contributes ≲ 100 MB total across all four coarser levels.

Design notes
------------

* **Indices are base-TF aligned.** Each level's chart tuples emit
  ``idx`` values that are the index of the *first* base-TF bar in the
  bucket. M5 candles sit at ``idx = 0, 5, 10, …``; H1 at
  ``idx = 0, 60, 120, …``. This keeps the chart's view range — which
  is in base-TF units throughout — pointing at the same time span no
  matter which level is rendered. The CandlestickItem just needs to
  widen each candle by the TF ratio so they fill their bucket
  visually.

* **Polars-first, pandas fallback.** :py:meth:`from_base` tries Polars'
  ``group_by_dynamic`` (the call already wired up in
  :mod:`gui.chart_tab.lod`) and falls back to a pandas ``resample``
  loop when Polars is unavailable. The result is identical OHLC
  semantics (first/max/min/last + sum volume + mean spread).

* **No Qt dependency.** Pyramid build runs entirely in NumPy /
  pandas / polars so headless unit tests can exercise the full path
  without a ``QApplication`` or a display.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger("StrategyTester.engine.data.lod_pyramid")

# Default ladder. M1 is always the base of truth; everything else is
# derived. Daily / weekly are included so a 10-year zoom-out fits in
# under 600 daily candles or ~120 weekly candles.
DEFAULT_PYRAMID_LEVELS: Tuple[str, ...] = ("1m", "5m", "15m", "1h", "1d")

# Same TF → minutes table as ``TimeframeAggregator`` so callers that
# mix engine + chart code see consistent numbers. Daily / weekly are
# added on top because the chart's "show full history" mode reaches
# beyond the engine's HTF set.
_TF_TO_MINUTES: Dict[str, int] = {
    "1m": 1,
    "2m": 2,
    "3m": 3,
    "5m": 5,
    "15m": 15,
    "30m": 30,
    "1h": 60,
    "4h": 240,
    "1d": 1440,
    "1w": 10_080,
}


def tf_minutes(tf: str) -> int:
    """Look up the minute-length of a timeframe string.

    Unknown TF strings raise ``KeyError`` — the pyramid's level set is
    fixed at construction so a typo in :py:meth:`for_visible_bars`
    should fail loudly rather than silently default to 1.
    """
    return _TF_TO_MINUTES[tf]


@dataclass
class LodPyramid:
    """Container holding the OHLC pyramid for one instrument.

    Attributes
    ----------
    levels:
        Mapping from timeframe string (``"1m"``, ``"5m"``, …) to the
        per-TF :class:`pandas.DataFrame`. All frames share the same
        column schema (``time``, ``open``, ``high``, ``low``,
        ``close``, optional ``volume`` / ``spread``).
    base_tf:
        The source-of-truth timeframe. Defaults to ``"1m"``. Coarser
        levels are derived from this frame; the pyramid never derives
        the base from anything else.
    """

    levels: Dict[str, pd.DataFrame] = field(default_factory=dict)
    base_tf: str = "1m"

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------
    @classmethod
    def from_base(
        cls,
        df_1m: pd.DataFrame,
        levels: Sequence[str] = DEFAULT_PYRAMID_LEVELS,
        base_tf: str = "1m",
        parallel: bool = True,
    ) -> "LodPyramid":
        """Build a pyramid from the base-TF (typically 1m) frame.

        Parameters
        ----------
        df_1m:
            Source DataFrame with a ``time`` column plus the usual
            OHLC(V) columns. Missing optional columns (``volume``,
            ``spread``) are propagated through; they're not required.
        levels:
            Ordered iterable of TF strings to materialise. ``base_tf``
            is auto-included even if absent from the list.
        base_tf:
            The source-of-truth timeframe. Defaults to ``"1m"``.
        parallel:
            If True (default), build levels in parallel using ThreadPoolExecutor.
            Provides 1.5× speedup for large datasets (1M+ bars). Set to False
            for sequential build (useful for debugging).

        Returns
        -------
        LodPyramid
            Always returns a pyramid (never raises on empty input) so
            callers can blindly :py:meth:`for_visible_bars` without
            null-checking. Empty input produces empty frames for every
            requested level.
        """
        levels_list = list(levels)
        if base_tf not in levels_list:
            levels_list = [base_tf, *levels_list]

        # Always seed the base level so tuple lookups never KeyError.
        if df_1m is None:
            df_1m = pd.DataFrame()
        out: Dict[str, pd.DataFrame] = {base_tf: df_1m}

        if len(df_1m) == 0:
            for tf in levels_list:
                out.setdefault(tf, df_1m.copy())
            return cls(levels=out, base_tf=base_tf)

        # Try the polars fast path. The local import keeps polars
        # optional for environments / tests that don't have it.
        pl_base = _to_polars_or_none(df_1m)

        non_base_levels = [tf for tf in levels_list if tf != base_tf]

        # Sprint 2.2: Parallel LOD build for 1.5× speedup on large datasets
        if parallel and len(non_base_levels) > 1:
            from concurrent.futures import ThreadPoolExecutor

            def _resample_level(tf: str) -> tuple[str, pd.DataFrame]:
                if pl_base is not None:
                    resampled = _resample_polars(pl_base, tf)
                    if resampled is not None:
                        return tf, resampled
                return tf, _resample_pandas(df_1m, tf)

            with ThreadPoolExecutor(max_workers=4) as executor:
                futures = [executor.submit(_resample_level, tf) for tf in non_base_levels]
                for future in futures:
                    tf, resampled = future.result()
                    out[tf] = resampled
        else:
            # Sequential fallback (original implementation)
            for tf in non_base_levels:
                if pl_base is not None:
                    resampled = _resample_polars(pl_base, tf)
                    if resampled is not None:
                        out[tf] = resampled
                        continue
                # Polars unavailable or failed — pandas fallback.
                out[tf] = _resample_pandas(df_1m, tf)

        return cls(levels=out, base_tf=base_tf)

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------
    def get(self, tf: str) -> pd.DataFrame:
        """Return the frame for ``tf``. Raises ``KeyError`` if absent."""
        return self.levels[tf]

    def has(self, tf: str) -> bool:
        return tf in self.levels

    def list_levels(self) -> List[str]:
        """Levels sorted from finest to coarsest (by minute-length)."""
        return sorted(self.levels.keys(), key=lambda t: _TF_TO_MINUTES.get(t, 1))

    # ------------------------------------------------------------------
    # Sensor: pick a TF for the given visible width
    # ------------------------------------------------------------------
    def for_visible_bars(
        self,
        visible_base_bars: int,
        max_bars: int = 2_000,
    ) -> str:
        """Pick the finest level whose candle count over a
        ``visible_base_bars`` window stays under ``max_bars``.

        ``visible_base_bars`` is expressed in *base-TF* units (i.e. M1
        bars). The pyramid walks its levels from finest to coarsest
        and returns the first that fits the budget. Falls back to the
        coarsest available level when nothing fits — this guarantees
        the chart never tries to draw more than ``max_bars`` candles
        regardless of how far the user zooms out.
        """
        if visible_base_bars <= 0:
            return self.base_tf
        budget = max(1, max_bars)
        base_min = _TF_TO_MINUTES.get(self.base_tf, 1)
        sorted_levels = self.list_levels()
        for tf in sorted_levels:
            tf_min = _TF_TO_MINUTES.get(tf, base_min)
            ratio = max(1, tf_min // base_min)
            count = visible_base_bars / ratio
            if count <= budget:
                return tf
        # Nothing fits — return the coarsest level so the caller has
        # at least a usable (if dense) view.
        return sorted_levels[-1]

    # ------------------------------------------------------------------
    # Chart adapter
    # ------------------------------------------------------------------
    def to_chart_tuples(
        self,
        tf: str,
    ) -> List[Tuple[float, float, float, float, float]]:
        """Return ``(idx, open, close, low, high)`` tuples for ``tf``.

        ``idx`` is the *base-TF* index of the first bar in the bucket,
        so M5 candles end up at ``idx = 0, 5, 10, …`` and H1 at
        ``idx = 0, 60, 120, …``. The chart's view range — which is in
        base-TF units — stays valid no matter which level is rendered;
        the CandlestickItem just widens each candle by the TF ratio so
        they visually fill their bucket.

        Empty levels return an empty list. The base level always
        emits ``idx = 0..N-1`` regardless of timestamps.
        """
        if tf not in self.levels:
            raise KeyError(f"Pyramid has no level {tf!r}; have {self.list_levels()}")
        df = self.levels[tf]
        if len(df) == 0:
            return []
        if tf == self.base_tf:
            idx = np.arange(len(df), dtype=np.float64)
        else:
            base_df = self.levels[self.base_tf]
            if len(base_df) == 0:
                return []
            # Map every coarser-level bucket-start back to a base-TF
            # index via ``searchsorted`` on the (sorted) base time
            # column. Out-of-range entries (a level row whose first
            # base bar landed before the loaded window) clamp at 0;
            # that's the right behaviour — the row simply won't
            # contribute new geometry.
            base_times = pd.to_datetime(base_df["time"]).to_numpy("datetime64[ns]")
            level_times = pd.to_datetime(df["time"]).to_numpy("datetime64[ns]")
            idx = np.searchsorted(base_times, level_times).astype(np.float64)
        op = df["open"].to_numpy(dtype=np.float64)
        cl = df["close"].to_numpy(dtype=np.float64)
        lo = df["low"].to_numpy(dtype=np.float64)
        hi = df["high"].to_numpy(dtype=np.float64)
        return list(zip(idx, op, cl, lo, hi))

    def to_chart_arrays(
        self,
        tf: str,
    ) -> np.ndarray:
        """Return NumPy array of shape (N, 5) with columns (idx, open, close, low, high)."""
        if tf not in self.levels:
            raise KeyError(f"Pyramid has no level {tf!r}; have {self.list_levels()}")
        df = self.levels[tf]
        if len(df) == 0:
            return np.empty((0, 5), dtype=np.float64)
        if tf == self.base_tf:
            idx = np.arange(len(df), dtype=np.float64)
        else:
            base_df = self.levels[self.base_tf]
            if len(base_df) == 0:
                return np.empty((0, 5), dtype=np.float64)
            base_times = pd.to_datetime(base_df["time"]).to_numpy("datetime64[ns]")
            level_times = pd.to_datetime(df["time"]).to_numpy("datetime64[ns]")
            idx = np.searchsorted(base_times, level_times).astype(np.float64)
        op = df["open"].to_numpy(dtype=np.float64)
        cl = df["close"].to_numpy(dtype=np.float64)
        lo = df["low"].to_numpy(dtype=np.float64)
        hi = df["high"].to_numpy(dtype=np.float64)
        return np.column_stack((idx, op, cl, lo, hi))


# ---------------------------------------------------------------------------
# Internal helpers — polars + pandas resampling
# ---------------------------------------------------------------------------


def _to_polars_or_none(df_1m: pd.DataFrame):  # type: ignore[no-untyped-def]
    """Return a polars DataFrame copy of ``df_1m`` or ``None`` when
    polars is unavailable / conversion fails."""
    try:
        import polars as pl  # type: ignore
    except Exception:  # pragma: no cover — exercised in non-polars envs
        return None
    try:
        # Polars needs the time column to be a Datetime dtype for
        # ``group_by_dynamic``. ``from_pandas`` converts pandas
        # ``datetime64[ns]`` to ``pl.Datetime`` automatically; for
        # other dtypes we coerce via pandas first so polars sees the
        # right schema.
        if "time" in df_1m.columns and not pd.api.types.is_datetime64_any_dtype(
            df_1m["time"]
        ):
            df_1m = df_1m.copy()
            df_1m["time"] = pd.to_datetime(df_1m["time"], errors="coerce")
        return pl.from_pandas(df_1m)
    except Exception as exc:
        logger.debug("polars.from_pandas failed (%r); using pandas fallback", exc)
        return None


def _resample_polars(pl_base, target_tf: str) -> Optional[pd.DataFrame]:  # type: ignore[no-untyped-def]
    """Polars-backed resample. Returns a pandas DataFrame on success
    or ``None`` so the caller can fall back to pandas. Failures here
    are debug-level only (the pyramid stays valid via the fallback)."""
    try:
        # Re-use the duration mapping + aggregations that
        # :mod:`gui.chart_tab.lod` already encodes.
        from gui.chart_tab.lod import downsample_ohlc_polars

        out_pl = downsample_ohlc_polars(pl_base, target_tf)
        return out_pl.to_pandas()
    except Exception as exc:
        logger.debug("polars resample to %s failed (%r)", target_tf, exc)
        return None


# pandas-side ``resample`` rule strings. The keys mirror the TF
# strings the chart / engine use; the values are the offset aliases
# pandas accepts.
_PANDAS_RESAMPLE_RULE: Dict[str, str] = {
    "2m": "2min",
    "3m": "3min",
    "5m": "5min",
    "15m": "15min",
    "30m": "30min",
    "1h": "1h",
    "4h": "4h",
    "1d": "1D",
    "1w": "1W",
}


def _resample_pandas(df_1m: pd.DataFrame, target_tf: str) -> pd.DataFrame:
    """pandas fallback resample. Identical OHLC semantics to the
    polars path — first/max/min/last + sum volume + mean spread."""
    rule = _PANDAS_RESAMPLE_RULE.get(target_tf)
    if rule is None:
        # Unsupported TF → return the base frame unchanged so callers
        # don't crash. ``for_visible_bars`` will simply never pick
        # this level because it isn't in ``levels`` to begin with;
        # this is defensive in case a future caller passes a bogus
        # TF string directly to ``_resample_pandas``.
        return df_1m.copy()

    df = df_1m.copy()
    df["time"] = pd.to_datetime(df["time"])
    df = df.set_index("time")

    agg: Dict[str, str] = {
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
    }
    if "volume" in df.columns:
        agg["volume"] = "sum"
    if "spread" in df.columns:
        agg["spread"] = "mean"

    # ``label="left"`` + ``closed="left"`` matches the polars
    # ``group_by_dynamic`` semantics used by
    # :func:`gui.chart_tab.lod.downsample_ohlc_polars`. Buckets are
    # tagged by their *start* time so the searchsorted in
    # :py:meth:`LodPyramid.to_chart_tuples` lands on the right base
    # index.
    out = df.resample(rule, label="left", closed="left").agg(agg)
    out = out.dropna(subset=["open"])
    out = out.reset_index().rename(columns={"index": "time"})
    return out


__all__ = [
    "DEFAULT_PYRAMID_LEVELS",
    "LodPyramid",
    "tf_minutes",
]
