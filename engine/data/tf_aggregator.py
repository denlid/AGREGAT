"""Timeframe Aggregator — builds higher-TF candles from a 1m base.

Two execution paths share this class:

* **Vectorized fast path** (TZ Phase 3 — used by the live engine):
  :py:meth:`prebuild` walks the entire 1m DataFrame once with a vector
  ``floor`` + ``groupby`` and stores the per-TF aggregated columns as
  NumPy arrays. The hot loop in
  :py:meth:`engine.core.backtest_core.BacktestCore._process_one_candle`
  then calls :py:meth:`advance_to` per 1m step — O(num_tfs) array reads
  and zero dict allocations per step.

* **Legacy per-step path** (kept for back-compat):
  :py:meth:`feed` builds HTF candles incrementally from a stream of
  1m candle dicts. Used directly by older tests and any external
  caller that wires up the aggregator without an engine. When the
  vectorized path has run, :py:meth:`get_completed` /
  :py:meth:`get_forming` materialise dicts on demand from the cached
  NumPy arrays so legacy consumers (ChartTab redraw, persistence
  snapshotting) keep working.

Zero-Future Rule: a higher-TF candle is only considered *completed* —
and therefore visible to strategies — once its last constituent 1m
candle has been processed. The forming (in-progress) candle is
exposed separately and explicitly flagged as not-Zero-Future-safe.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, List, Optional, Set

import numpy as np
import pandas as pd

from engine.data.htf_view import HTFView
from engine.data import prebuild_cache

logger = logging.getLogger("StrategyTester.engine.data.tf_aggregator")


class TimeframeAggregator:
    """Aggregate 1m candles into higher timeframes.

    Supported TFs (the offset-mapping is the same as the legacy class):
    ``2m``, ``3m``, ``5m``, ``15m``, ``30m``, ``1h``, ``4h``.

    Phase 2.1 (PERF) — memory layout reworked from full-length
    per-1m bookkeeping arrays to compact per-HTF data + ``searchsorted``
    on the hot path. The semantics are unchanged; only the storage
    cost shrinks (5 GB → ~1.5 GB peak on a 10M-bar feed).
    """

    TF_MINUTES: Dict[str, int] = {
        "2m": 2,
        "3m": 3,
        "5m": 5,
        "15m": 15,
        "30m": 30,
        "1h": 60,
        "4h": 240,
    }

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def __init__(self) -> None:
        # --- Vectorized fast-path state (populated by ``prebuild``) ---
        self._prebuilt = False
        # Per-TF dict of {column: numpy array of length N_htf}.
        self._htf_arrays: Dict[str, Dict[str, np.ndarray]] = {}
        # Phase 2.1 (Stage B) — completion points: per-TF int64 array
        # of size N_htf-1 holding the 1m index at which *each* HTF bar
        # completed (i.e. the first-1m-of-the-next-bucket index).
        # Replaces the previous N_1m-sized ``_completed_count_at_1m``
        # and ``_completes_at_1m`` arrays. Lookup is a single
        # ``np.searchsorted`` (~100ns) which the hot-loop step amortises
        # against the rest of the per-bar Python work.
        self._completion_points: Dict[str, np.ndarray] = {}
        # Phase 2.1 (Stage A) — per-TF "forming" arrays. Built only for
        # TFs the caller listed in ``track_forming``. TFs absent from
        # the dict have no forming candle exposed to strategies / chart
        # — :py:meth:`get_forming` returns ``None`` in that case, which
        # is semantically equivalent to "no in-progress data available".
        self._forming_arrays: Dict[str, Dict[str, np.ndarray]] = {}
        self._forming_tfs: Set[str] = set()
        # Phase 0.2 (A3) — retained reference to the 1m DataFrame so
        # ``set_forming_tfs`` can lazily build forming arrays for newly
        # requested TFs (e.g. when the GUI switches the visual TF) without
        # rerunning the full HTF resample. Cleared on ``reset``.
        self._df_for_forming: Optional[pd.DataFrame] = None
        # How many HTF bars currently visible per TF — advanced by
        # ``advance_to``. Reset to zero on ``reset``.
        self._completed_count: Dict[str, int] = {tf: 0 for tf in self.TF_MINUTES}
        # Index of the most recently advanced-to 1m bar (``-1`` when
        # the engine hasn't stepped yet).
        self._current_1m_idx: int = -1

        # --- Legacy per-step path state (used by ``feed``) ---
        self._legacy_completed: Dict[str, List[Dict[str, Any]]] = {
            tf: [] for tf in self.TF_MINUTES
        }
        self._legacy_forming: Dict[str, Optional[Dict[str, Any]]] = {
            tf: None for tf in self.TF_MINUTES
        }
        self._legacy_current_bucket: Dict[str, Optional[pd.Timestamp]] = {
            tf: None for tf in self.TF_MINUTES
        }

    # ------------------------------------------------------------------
    # Vectorized fast path (TZ Phase 3)
    # ------------------------------------------------------------------
    def prebuild(
        self,
        df: pd.DataFrame,
        *,
        track_forming: Optional[Iterable[str]] = None,
        cache: bool = False,
    ) -> None:
        """Resample ``df`` (1m base) into HTF NumPy arrays for every TF.

        Run once after the engine binds a new dataset. Cost is
        O(N_1m × num_tfs) but the constants are tiny because everything
        runs in NumPy / pandas C — no Python-level per-bar dict work.
        After this returns, :py:meth:`advance_to` is the only call
        :py:meth:`engine.core.backtest_core.BacktestCore._process_one_candle`
        needs to make per step.

        Parameters
        ----------
        df
            1m OHLCV DataFrame with a ``time`` column.
        track_forming
            Phase 2.1 (Stage A) — iterable of TF strings whose
            *forming* (in-progress) candle data should be precomputed.
            Forming arrays are the largest memory consumer in
            ``prebuild`` (5 cols × float64 × N_1m per TF), so backtests
            that don't need ``ctx.get_view(tf).forming`` can opt out
            per-TF.

            **Phase 0.2 (A3) —** ``None`` (the default) now means *no*
            forming TFs. Callers that need forming on a specific TF
            should pass an explicit iterable. The GUI (chart toolbar)
            opts in to the visible TF only via
            :py:meth:`set_forming_tfs`, keeping the cold-load memory
            footprint under ≈1.5 GB for 8 M 1m bars.
        cache
            Phase 2.4 — when ``True``, try to load the prebuild output
            from the on-disk cache (``~/.agregatishe/cache/prebuild``)
            keyed by a SHA-256 of the dataset content + TF list +
            ``track_forming``. On a cache miss the cold prebuild runs
            normally and the result is written back to the cache. The
            default is ``False`` so existing callers see no I/O.
        """
        # Phase 0.2 (A3) — default ``track_forming=None`` now means
        # "track *no* forming TFs". Memory: forming arrays are 5
        # float64 columns × N_1m per TF (≈40 MB / TF on 1M bars).
        # The previous default (track every TF) cost ~320 MB on the
        # supported TF set; the GUI now opts in to a single TF via
        # :py:meth:`set_forming_tfs` when the user switches charts.
        if track_forming is None:
            forming_tfs: Set[str] = set()
        else:
            forming_tfs = {tf for tf in track_forming if tf in self.TF_MINUTES}
        self._forming_tfs = forming_tfs

        n = len(df)
        if n == 0:
            self._reset_vector_state()
            self._prebuilt = True
            return

        # Phase 2.4 — opportunistic disk cache. Compute the key once;
        # we'll use it for both the load and the save.
        cache_key: Optional[str] = None
        if cache:
            try:
                cache_key = prebuild_cache.compute_cache_key(
                    df,
                    self.TF_MINUTES.keys(),
                    track_forming=(
                        sorted(forming_tfs) if track_forming is not None else None
                    ),
                )
                state = prebuild_cache.load_state(cache_key)
            except Exception as exc:
                logger.warning("Prebuild cache key/load failed: %s", exc)
                state = None

            if state is not None:
                self._reset_vector_state()
                self._htf_arrays = state["htf_arrays"]
                self._completion_points = state["completion_points"]
                self._forming_arrays = state["forming_arrays"]
                self._forming_tfs = set(state["forming_tfs"])
                self._completed_count = {tf: 0 for tf in self.TF_MINUTES}
                self._current_1m_idx = -1
                self._prebuilt = True
                logger.debug("Prebuild cache HIT (%s)", cache_key[:12])
                return

        time_col = pd.to_datetime(df["time"])
        open_arr = df["open"].to_numpy(dtype=np.float64, copy=False)
        high_arr = df["high"].to_numpy(dtype=np.float64, copy=False)
        low_arr = df["low"].to_numpy(dtype=np.float64, copy=False)
        close_arr = df["close"].to_numpy(dtype=np.float64, copy=False)
        if "volume" in df.columns:
            volume_arr = df["volume"].to_numpy(dtype=np.float64, copy=False)
        else:
            volume_arr = np.zeros(n, dtype=np.float64)
        # Phase 1.4 — propagate per-bar spread into HTF aggregates as a
        # *mean* (the appropriate aggregation for an instantaneous
        # price-quote field). Strategies that read ``ctx.get_view(tf)``
        # then see a spread series sampled at the HTF resolution.
        has_spread = "spread" in df.columns
        if has_spread:
            spread_arr = df["spread"].to_numpy(dtype=np.float64, copy=False)
        else:
            spread_arr = np.zeros(n, dtype=np.float64)

        for tf, minutes in self.TF_MINUTES.items():
            # Bucket key for each 1m bar — vectorized floor on the
            # timestamp column. Using the underlying ``np.datetime64``
            # values ensures the same alignment the legacy ``feed``
            # path produced (both rely on ``pd.Timestamp.floor``).
            bucket_starts = time_col.dt.floor(
                f"{minutes}min"
            ).values  # ndarray[datetime64]

            # Bucket index per 1m bar: each new bucket bumps the index
            # by one. ``cumsum`` over a "boundary changed" mask gives
            # us 0-based ints. The mask is True at every position
            # where the bucket differs from the previous bar.
            boundary = np.empty(n, dtype=bool)
            boundary[0] = True
            boundary[1:] = bucket_starts[1:] != bucket_starts[:-1]
            bucket_idx = np.cumsum(boundary) - 1  # int array, 0..N_htf-1
            n_htf = int(bucket_idx[-1]) + 1

            # Per-bucket aggregates via pandas groupby (C-fast).
            df_local_data: Dict[str, Any] = {
                "_b": bucket_idx,
                "_t": bucket_starts,
                "open": open_arr,
                "high": high_arr,
                "low": low_arr,
                "close": close_arr,
                "volume": volume_arr,
            }
            if has_spread:
                df_local_data["spread"] = spread_arr
            df_local = pd.DataFrame(df_local_data)
            agg_kwargs: Dict[str, Any] = dict(
                time=("_t", "first"),
                open=("open", "first"),
                high=("high", "max"),
                low=("low", "min"),
                close=("close", "last"),
                volume=("volume", "sum"),
            )
            if has_spread:
                agg_kwargs["spread"] = ("spread", "mean")
            agg = df_local.groupby("_b", sort=False).agg(**agg_kwargs)
            # ``groupby(sort=False)`` preserves first-seen order, which
            # matches ``bucket_idx`` because bucket_idx is monotonic.
            # Phase 1.9 / §7 fix #8 — replace ``assert`` (no-op under -O)
            # with a real ValueError so corrupt data fails loudly even
            # in production builds.
            if len(agg) != n_htf:
                raise ValueError(
                    f"prebuild[{tf}]: groupby produced {len(agg)} buckets, "
                    f"expected {n_htf}"
                )

            arrays = {
                "time": agg["time"].to_numpy(),
                "open": agg["open"].to_numpy(dtype=np.float64),
                "high": agg["high"].to_numpy(dtype=np.float64),
                "low": agg["low"].to_numpy(dtype=np.float64),
                "close": agg["close"].to_numpy(dtype=np.float64),
                "volume": agg["volume"].to_numpy(dtype=np.float64),
            }
            if has_spread:
                arrays["spread"] = agg["spread"].to_numpy(dtype=np.float64)
            self._htf_arrays[tf] = arrays

            # Phase 2.1 (Stage B) — completion points: the 1m indices
            # where each new bucket starts (excluding index 0). The
            # bucket whose last bar is at index ``cp[k]-1`` completes
            # AT 1m bar ``cp[k]``. Size N_htf-1, replaces the previous
            # N_1m-sized ``completes_at_1m`` and ``completed_count_at_1m``
            # arrays. Hot-loop lookups are a single ``searchsorted``.
            #
            # Stage C — the previous ``_bucket_idx_at_1m`` (full N_1m
            # int64 array) is *gone* — it was never read once set. The
            # 560 MB it cost on a 10M-bar feed comes back for free.
            self._completion_points[tf] = np.flatnonzero(boundary[1:]) + 1
            # ``boundary[1:]`` shifts so index ``i`` of the result is
            # truthy iff the bucket changed between 1m bars ``i`` and
            # ``i+1`` — i.e. bar ``i+1`` is the first of a new bucket.
            # Adding 1 maps back to the absolute 1m index.

            # Forming arrays — running aggregates within each bucket,
            # built with groupby cum* in C. ``transform('first')`` is
            # used for the open / time so they stay constant across
            # the bucket. Phase 2.1 (Stage A) — only built for TFs the
            # caller opted into via ``track_forming``; otherwise these
            # arrays are simply not allocated, which is the lion's
            # share of the prebuild memory budget.
            if tf in forming_tfs:
                grp = df_local.groupby("_b", sort=False)
                forming_open = grp["open"].transform("first").to_numpy(dtype=np.float64)
                forming_high = grp["high"].cummax().to_numpy(dtype=np.float64)
                forming_low = grp["low"].cummin().to_numpy(dtype=np.float64)
                forming_close = (
                    close_arr  # close of current 1m bar is bucket's running close
                )
                forming_volume = grp["volume"].cumsum().to_numpy(dtype=np.float64)
                forming_time = grp["_t"].transform("first").to_numpy()
                self._forming_arrays[tf] = {
                    "time": forming_time,
                    "open": forming_open,
                    "high": forming_high,
                    "low": forming_low,
                    "close": forming_close,
                    "volume": forming_volume,
                }

        self._completed_count = {tf: 0 for tf in self.TF_MINUTES}
        self._current_1m_idx = -1
        self._prebuilt = True
        # Keep a reference to the 1m source so ``set_forming_tfs`` can
        # build forming arrays on demand without a full re-prebuild.
        self._df_for_forming = df

        # Phase 2.4 — write back to the disk cache after a cold build.
        # Errors here are non-fatal: the in-memory state is already
        # ready to use, the cache miss next time is the only penalty.
        if cache and cache_key is not None:
            try:
                state = prebuild_cache.state_to_dict(
                    htf_arrays=self._htf_arrays,
                    completion_points=self._completion_points,
                    forming_arrays=self._forming_arrays,
                    forming_tfs=self._forming_tfs,
                )
                prebuild_cache.save_state(cache_key, state)
                logger.debug("Prebuild cache WRITE (%s)", cache_key[:12])
            except Exception as exc:
                logger.warning("Prebuild cache write failed: %s", exc)

    def advance_to(self, idx_1m: int) -> Dict[str, bool]:
        """Update completed counts to reflect bars 0..``idx_1m`` having
        been processed. Returns ``{tf: bool}`` indicating which TFs
        completed *exactly* at this step.

        Pure NumPy reads — no per-step dict allocations for HTF candle
        data. The returned dict (small, fixed-size) is the only
        per-step allocation, and the engine forwards it straight to
        ``ctx._completed_tfs`` without copying.

        Phase 2.1 (Stage B) — uses ``np.searchsorted`` against the
        compact ``_completion_points`` array (size N_htf-1) instead of
        random-access into the previous N_1m-long bookkeeping arrays.
        Both calls are O(log N_htf), well under a microsecond per TF.
        """
        if not self._prebuilt:
            return {tf: False for tf in self.TF_MINUTES}
        result: Dict[str, bool] = {}
        for tf in self.TF_MINUTES:
            cp = self._completion_points.get(tf)
            if cp is None:
                self._completed_count[tf] = 0
                result[tf] = False
                continue
            if idx_1m < 0:
                self._completed_count[tf] = 0
                result[tf] = False
                continue
            # ``searchsorted(side='right')`` returns the count of
            # completion points ≤ idx_1m, which is exactly the number
            # of HTF bars whose final 1m bar has been processed.
            cnt = int(np.searchsorted(cp, idx_1m, side="right"))
            self._completed_count[tf] = cnt
            # ``completes`` flag fires iff the current 1m bar *is* a
            # completion point — i.e. the previous bucket completed
            # at the previous 1m bar.
            if cnt > 0 and cp[cnt - 1] == idx_1m:
                result[tf] = True
            else:
                result[tf] = False
        self._current_1m_idx = idx_1m
        return result

    def view(self, tf: str) -> HTFView:
        """Return a read-only :class:`HTFView` over the completed bars
        for ``tf``. The view tracks ``advance_to`` automatically — the
        backing arrays don't move and the slice length is read from
        :py:attr:`_completed_count` on every access.
        """
        if tf not in self.TF_MINUTES:
            raise ValueError(
                f"Unsupported timeframe: {tf}. Supported: {list(self.TF_MINUTES.keys())}"
            )
        if not self._prebuilt:
            # Build a placeholder view backed by empty arrays so
            # callers never see ``None``. Once ``prebuild`` runs the
            # arrays will be in place but the view object created here
            # would still point at the stale empty dict — by design
            # we always create a fresh view via ``view()``.
            empty: Dict[str, np.ndarray] = {
                "time": np.empty(0, dtype="datetime64[ns]"),
                "open": np.empty(0, dtype=np.float64),
                "high": np.empty(0, dtype=np.float64),
                "low": np.empty(0, dtype=np.float64),
                "close": np.empty(0, dtype=np.float64),
                "volume": np.empty(0, dtype=np.float64),
            }
            return HTFView(empty, lambda: 0, None)
        captured_tf = tf

        def _count() -> int:
            return self._completed_count[captured_tf]

        def _forming() -> Optional[Dict[str, Any]]:
            return self._forming_dict(captured_tf)

        return HTFView(
            self._htf_arrays[tf],
            count_provider=_count,
            forming_provider=_forming,
        )

    # ------------------------------------------------------------------
    # Legacy per-step path (kept for tests / external callers)
    # ------------------------------------------------------------------
    def _get_bucket_start(
        self, candle_time: pd.Timestamp, minutes: int
    ) -> pd.Timestamp:
        """Floor ``candle_time`` to the bucket-start of a TF window."""
        return candle_time.floor(f"{minutes}min")

    def feed(self, candle_1m: Dict[str, Any]) -> Dict[str, bool]:
        """Legacy per-step API — incremental aggregation from a stream
        of 1m candle dicts. The vectorized path
        (:py:meth:`prebuild` + :py:meth:`advance_to`) supersedes this in
        the live engine; kept here for direct tests and external
        callers that don't drive the aggregator through ``BacktestCore``.

        Returns a ``{tf: bool}`` dict for which TFs completed at this
        step.
        """
        raw_time = candle_1m["time"]
        candle_time: pd.Timestamp = pd.Timestamp(raw_time)
        result: Dict[str, bool] = {}
        for tf, minutes in self.TF_MINUTES.items():
            bucket_start = self._get_bucket_start(candle_time, minutes)
            current = self._legacy_current_bucket[tf]
            if current is None or bucket_start != current:
                prev = self._legacy_forming[tf]
                if prev is not None:
                    self._legacy_completed[tf].append(prev)
                    result[tf] = True
                else:
                    result[tf] = False
                self._legacy_forming[tf] = {
                    "time": bucket_start,
                    "open": candle_1m["open"],
                    "high": candle_1m["high"],
                    "low": candle_1m["low"],
                    "close": candle_1m["close"],
                    "volume": candle_1m.get("volume", 0),
                }
                self._legacy_current_bucket[tf] = bucket_start
            else:
                forming = self._legacy_forming[tf]
                assert forming is not None  # invariant: bucket exists ⇒ forming set
                forming["high"] = max(forming["high"], candle_1m["high"])
                forming["low"] = min(forming["low"], candle_1m["low"])
                forming["close"] = candle_1m["close"]
                forming["volume"] += candle_1m.get("volume", 0)
                result[tf] = False
        return result

    # ------------------------------------------------------------------
    # Read API (unchanged contract — back-compat with ChartTab + tests)
    # ------------------------------------------------------------------
    def get_completed(self, tf: str) -> List[Dict[str, Any]]:
        """Completed HTF candles as a list of dicts. Zero-Future safe.

        Vectorized path: dicts are built lazily from the cached NumPy
        arrays sliced to :py:attr:`_completed_count`. ChartTab calls
        this on every redraw (~30 ms) but the slice is bounded — a
        full 5m year is ~100 000 bars — and we only build dicts when
        the caller asks for them.
        """
        if tf not in self.TF_MINUTES:
            raise ValueError(
                f"Unsupported timeframe: {tf}. Supported: {list(self.TF_MINUTES.keys())}"
            )
        if not self._prebuilt:
            return self._legacy_completed[tf]
        cnt = self._completed_count[tf]
        if cnt <= 0:
            return []
        arrs = self._htf_arrays[tf]
        return [
            {
                "time": arrs["time"][i],
                "open": float(arrs["open"][i]),
                "high": float(arrs["high"][i]),
                "low": float(arrs["low"][i]),
                "close": float(arrs["close"][i]),
                "volume": float(arrs["volume"][i]),
            }
            for i in range(cnt)
        ]

    def get_completed_tail(self, tf: str, n: int) -> List[Dict[str, Any]]:
        """Return the last ``n`` completed HTF candles as dicts.

        Avoids materializing the entire history when the caller only
        needs a sliding window (e.g. ChartTab with ``RENDER_WINDOW``).
        """
        if tf not in self.TF_MINUTES:
            raise ValueError(
                f"Unsupported timeframe: {tf}. Supported: {list(self.TF_MINUTES.keys())}"
            )
        if not self._prebuilt:
            return self._legacy_completed[tf][-n:]
        cnt = self._completed_count[tf]
        if cnt <= 0:
            return []
        start = max(0, cnt - n)
        arrs = self._htf_arrays[tf]
        return [
            {
                "time": arrs["time"][i],
                "open": float(arrs["open"][i]),
                "high": float(arrs["high"][i]),
                "low": float(arrs["low"][i]),
                "close": float(arrs["close"][i]),
                "volume": float(arrs["volume"][i]),
            }
            for i in range(start, cnt)
        ]

    def get_forming(self, tf: str) -> Optional[Dict[str, Any]]:
        """Current in-progress HTF candle. NOT Zero-Future safe."""
        if tf not in self.TF_MINUTES:
            raise ValueError(f"Unsupported timeframe: {tf}")
        if not self._prebuilt:
            return self._legacy_forming[tf]
        return self._forming_dict(tf)

    def get_last_completed(self, tf: str) -> Optional[Dict[str, Any]]:
        """Most recently completed HTF bar, or ``None`` before any
        bucket has rolled over.
        """
        completed = self.get_completed(tf)
        return completed[-1] if completed else None

    def reset(self) -> None:
        """Reset the *runtime* state without dropping the prebuilt
        arrays — re-running a backtest from idx=-1 doesn't change the
        underlying dataset, so we only zero the per-TF counts.
        """
        self._completed_count = {tf: 0 for tf in self.TF_MINUTES}
        self._current_1m_idx = -1
        for tf in self.TF_MINUTES:
            self._legacy_completed[tf] = []
            self._legacy_forming[tf] = None
            self._legacy_current_bucket[tf] = None

    # ------------------------------------------------------------------
    # Phase 0.2 (A3) — runtime forming-TF management
    # ------------------------------------------------------------------
    def set_forming_tfs(self, tfs: Iterable[str]) -> None:
        """Update the set of TFs whose forming arrays should be tracked.

        Builds new forming arrays for any requested TF that doesn't
        already have them and drops arrays for TFs that are no longer
        requested. Cheap relative to a full :py:meth:`prebuild` because
        only the per-bucket ``cumsum`` / ``cummax`` / ``cummin``
        transforms run; the HTF resample (the bulk of the work) is
        already cached.

        Used by the chart toolbar so switching the visible TF brings
        the in-progress candle online without paying the memory cost
        of tracking every supported TF up front (Phase 0.2 (A3)).
        """
        if not self._prebuilt:
            self._forming_tfs = {tf for tf in tfs if tf in self.TF_MINUTES}
            return
        requested = {tf for tf in tfs if tf in self.TF_MINUTES}
        # Drop arrays for TFs we no longer track — instant memory
        # release for unused datasets.
        for tf in list(self._forming_arrays.keys()):
            if tf not in requested:
                self._forming_arrays.pop(tf, None)
        self._forming_tfs = requested
        df = self._df_for_forming
        if df is None or len(df) == 0:
            return
        time_col = pd.to_datetime(df["time"])
        open_arr = df["open"].to_numpy(dtype=np.float64, copy=False)
        high_arr = df["high"].to_numpy(dtype=np.float64, copy=False)
        low_arr = df["low"].to_numpy(dtype=np.float64, copy=False)
        close_arr = df["close"].to_numpy(dtype=np.float64, copy=False)
        if "volume" in df.columns:
            volume_arr = df["volume"].to_numpy(dtype=np.float64, copy=False)
        else:
            volume_arr = np.zeros(len(df), dtype=np.float64)
        for tf in requested:
            if tf in self._forming_arrays:
                continue  # already built
            minutes = self.TF_MINUTES[tf]
            bucket_starts = time_col.dt.floor(f"{minutes}min").values
            n = len(df)
            boundary = np.empty(n, dtype=bool)
            boundary[0] = True
            boundary[1:] = bucket_starts[1:] != bucket_starts[:-1]
            bucket_idx = np.cumsum(boundary) - 1
            df_local = pd.DataFrame(
                {
                    "_b": bucket_idx,
                    "_t": bucket_starts,
                    "open": open_arr,
                    "high": high_arr,
                    "low": low_arr,
                    "close": close_arr,
                    "volume": volume_arr,
                }
            )
            grp = df_local.groupby("_b", sort=False)
            self._forming_arrays[tf] = {
                "time": grp["_t"].transform("first").to_numpy(),
                "open": grp["open"].transform("first").to_numpy(dtype=np.float64),
                "high": grp["high"].cummax().to_numpy(dtype=np.float64),
                "low": grp["low"].cummin().to_numpy(dtype=np.float64),
                "close": close_arr,
                "volume": grp["volume"].cumsum().to_numpy(dtype=np.float64),
            }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _reset_vector_state(self) -> None:
        self._htf_arrays = {}
        self._completion_points = {}
        self._forming_arrays = {}
        self._forming_tfs = set()
        self._completed_count = {tf: 0 for tf in self.TF_MINUTES}
        self._current_1m_idx = -1
        self._df_for_forming = None

    def _forming_dict(self, tf: str) -> Optional[Dict[str, Any]]:
        """Build the in-progress HTF candle dict from the precomputed
        per-1m forming arrays. Returns ``None`` when there's no bar in
        progress (engine hasn't stepped yet, or 1m bar landed exactly
        on a bucket boundary that *just* completed).
        """
        if not self._prebuilt:
            return None
        i = self._current_1m_idx
        forming = self._forming_arrays.get(tf)
        if forming is None or i < 0 or i >= len(forming["time"]):
            return None
        # If the *current* 1m bar is itself the last constituent of a
        # bucket — i.e. the bucket would complete on the *next* 1m bar
        # — there is still a forming candle (the running aggregate up
        # to and including this bar). Hence we always return a dict
        # while the engine has data; the only case ``None`` makes
        # sense is "before stepping".
        return {
            "time": forming["time"][i],
            "open": float(forming["open"][i]),
            "high": float(forming["high"][i]),
            "low": float(forming["low"][i]),
            "close": float(forming["close"][i]),
            "volume": float(forming["volume"][i]),
        }
