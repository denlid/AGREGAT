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

from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from engine.data.htf_view import HTFView


class TimeframeAggregator:
    """Aggregate 1m candles into higher timeframes.

    Supported TFs (the offset-mapping is the same as the legacy class):
    ``2m``, ``3m``, ``5m``, ``15m``, ``30m``, ``1h``, ``4h``.
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
        # Per-TF int array of length N_1m. Position ``i`` = number of
        # *completed* HTF bars after processing 1m bar ``i`` (so the
        # last completed HTF index is value-1, or ``-1`` if none).
        self._completed_count_at_1m: Dict[str, np.ndarray] = {}
        # Per-TF bool array of length N_1m. Position ``i`` = True iff a
        # HTF bar completed *exactly* at this 1m step (used by the
        # ``completed_tfs`` dict the engine forwards to strategies).
        self._completes_at_1m: Dict[str, np.ndarray] = {}
        # Per-TF "forming" arrays: at 1m index ``i``, the
        # in-progress HTF candle's running aggregates.
        self._forming_arrays: Dict[str, Dict[str, np.ndarray]] = {}
        # Per-TF: index of the *last* HTF bar each 1m bar belongs to
        # (i.e. its bucket index). Used to compute "is forming?".
        self._bucket_idx_at_1m: Dict[str, np.ndarray] = {}
        # How many HTF bars currently visible per TF — advanced by
        # ``advance_to``. Reset to zero on ``reset``.
        self._completed_count: Dict[str, int] = {tf: 0 for tf in self.TF_MINUTES}
        # Index of the most recently advanced-to 1m bar (``-1`` when
        # the engine hasn't stepped yet).
        self._current_1m_idx: int = -1

        # --- Legacy per-step path state (used by ``feed``) ---
        self._legacy_completed: Dict[str, List[Dict[str, Any]]] = {tf: [] for tf in self.TF_MINUTES}
        self._legacy_forming: Dict[str, Optional[Dict[str, Any]]] = {
            tf: None for tf in self.TF_MINUTES
        }
        self._legacy_current_bucket: Dict[str, Optional[pd.Timestamp]] = {
            tf: None for tf in self.TF_MINUTES
        }

    # ------------------------------------------------------------------
    # Vectorized fast path (TZ Phase 3)
    # ------------------------------------------------------------------
    def prebuild(self, df: pd.DataFrame) -> None:
        """Resample ``df`` (1m base) into HTF NumPy arrays for every TF.

        Run once after the engine binds a new dataset. Cost is
        O(N_1m × num_tfs) but the constants are tiny because everything
        runs in NumPy / pandas C — no Python-level per-bar dict work.
        After this returns, :py:meth:`advance_to` is the only call
        :py:meth:`engine.core.backtest_core.BacktestCore._process_one_candle`
        needs to make per step.
        """
        n = len(df)
        if n == 0:
            self._reset_vector_state()
            self._prebuilt = True
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

        for tf, minutes in self.TF_MINUTES.items():
            # Bucket key for each 1m bar — vectorized floor on the
            # timestamp column. Using the underlying ``np.datetime64``
            # values ensures the same alignment the legacy ``feed``
            # path produced (both rely on ``pd.Timestamp.floor``).
            bucket_starts = time_col.dt.floor(f"{minutes}min").values  # ndarray[datetime64]

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
            agg = df_local.groupby("_b", sort=False).agg(
                time=("_t", "first"),
                open=("open", "first"),
                high=("high", "max"),
                low=("low", "min"),
                close=("close", "last"),
                volume=("volume", "sum"),
            )
            # ``groupby(sort=False)`` preserves first-seen order, which
            # matches ``bucket_idx`` because bucket_idx is monotonic.
            assert len(agg) == n_htf, (
                f"prebuild[{tf}]: groupby produced {len(agg)} buckets, expected {n_htf}"
            )

            self._htf_arrays[tf] = {
                "time": agg["time"].to_numpy(),
                "open": agg["open"].to_numpy(dtype=np.float64),
                "high": agg["high"].to_numpy(dtype=np.float64),
                "low": agg["low"].to_numpy(dtype=np.float64),
                "close": agg["close"].to_numpy(dtype=np.float64),
                "volume": agg["volume"].to_numpy(dtype=np.float64),
            }
            self._bucket_idx_at_1m[tf] = bucket_idx.astype(np.int64, copy=False)

            # ``completes_at_1m[i]`` is True iff at 1m bar ``i`` the
            # bucket changed *and* there was at least one previous
            # bucket. Equivalent to ``boundary`` but with index 0
            # excluded — the very first bar can't complete anything.
            completes = boundary.copy()
            completes[0] = False
            self._completes_at_1m[tf] = completes

            # ``completed_count_at_1m[i]`` = number of HTF bars whose
            # last constituent 1m bar is at index <= i-1. That's the
            # cumulative count of "completes" up through index i.
            self._completed_count_at_1m[tf] = np.cumsum(completes).astype(
                np.int64,
                copy=False,
            )

            # Forming arrays — running aggregates within each bucket,
            # built with groupby cum* in C. ``transform('first')`` is
            # used for the open / time so they stay constant across
            # the bucket.
            grp = df_local.groupby("_b", sort=False)
            forming_open = grp["open"].transform("first").to_numpy(dtype=np.float64)
            forming_high = grp["high"].cummax().to_numpy(dtype=np.float64)
            forming_low = grp["low"].cummin().to_numpy(dtype=np.float64)
            forming_close = close_arr  # close of current 1m bar is the bucket's running close
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

    def advance_to(self, idx_1m: int) -> Dict[str, bool]:
        """Update completed counts to reflect bars 0..``idx_1m`` having
        been processed. Returns ``{tf: bool}`` indicating which TFs
        completed *exactly* at this step.

        Pure NumPy reads — no per-step dict allocations for HTF candle
        data. The returned dict (small, fixed-size) is the only
        per-step allocation, and the engine forwards it straight to
        ``ctx._completed_tfs`` without copying.
        """
        if not self._prebuilt:
            return {tf: False for tf in self.TF_MINUTES}
        result: Dict[str, bool] = {}
        for tf in self.TF_MINUTES:
            cnt_arr = self._completed_count_at_1m[tf]
            if idx_1m < 0:
                self._completed_count[tf] = 0
            elif idx_1m >= len(cnt_arr):
                self._completed_count[tf] = int(cnt_arr[-1])
            else:
                self._completed_count[tf] = int(cnt_arr[idx_1m])
            completes_arr = self._completes_at_1m[tf]
            if 0 <= idx_1m < len(completes_arr):
                result[tf] = bool(completes_arr[idx_1m])
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
    def _get_bucket_start(self, candle_time: pd.Timestamp, minutes: int) -> pd.Timestamp:
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
    # Internal helpers
    # ------------------------------------------------------------------
    def _reset_vector_state(self) -> None:
        self._htf_arrays = {}
        self._completed_count_at_1m = {}
        self._completes_at_1m = {}
        self._forming_arrays = {}
        self._bucket_idx_at_1m = {}
        self._completed_count = {tf: 0 for tf in self.TF_MINUTES}
        self._current_1m_idx = -1

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
