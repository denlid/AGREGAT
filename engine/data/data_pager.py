"""Asynchronous data pager — Phase 4 of ``chart_gpu_optimization_plan.md``.

The pager exists for one job: hide disk I/O from the GUI thread. The
chart declares a set of *tiles* — each tile names a file on disk plus
an optional time bound — and asks the pager to make sure the tiles
overlapping the current viewport are resident. The pager runs the
actual loader functions on a ``QThreadPool`` and only flips data into
the GUI's hot path *after* the load completes, so a pan or zoom never
stalls waiting on the filesystem.

Design choices worth calling out:

* **Loader is an injected callable.** We don't hard-code
  :class:`engine.data.data_loader.DataLoader` so the pager is
  trivially unit-testable with a stub that returns a synthetic frame.
  The default wiring uses ``DataLoader.load_csv`` / ``load_parquet``
  / ``load_feather`` picked by extension.
* **No globals, no module-level state.** Each pager owns its tile
  registry and resident cache, so multiple chart tabs can page
  independent histories.
* **LRU eviction keyed on max resident tiles.** When the resident
  set grows past :attr:`max_resident`, the least-recently-touched
  tile is dropped. Eviction is *idempotent* — touching the same
  tile twice doesn't double-bump it.
* **All signal emissions happen on the worker callback thread.**
  Qt's signal/slot machinery already marshals across threads via
  ``Qt.ConnectionType.QueuedConnection`` when sender / receiver
  live on different threads, so consumers can connect directly from
  the GUI thread without extra plumbing. Tests verify the queued-
  signal contract by using ``QSignalSpy`` + ``QThreadPool.waitForDone``.
* **The pager is opt-in.** ``ChartTab`` only constructs one when
  ``AGREGAT_DATA_PAGER=1``; otherwise the legacy single-file load
  path is unchanged. The off-by-default policy mirrors Phase 3's
  ``AGREGAT_GL_CANDLES`` — we ship the engine but don't flip the
  switch until smoke-tested on real datasets.
"""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Tuple

import pandas as pd

from PySide6.QtCore import (
    QObject,
    QRunnable,
    QThreadPool,
    Signal,
    Slot,
)

logger = logging.getLogger("StrategyTester.engine.data.data_pager")


# Default expansion applied to the requested viewport before we pick
# which tiles to load. A value of 0.25 means we prefetch a quarter-
# window on each side, so the user has to scroll 25 % past the edge
# before a tile actually needs to land synchronously. Configurable
# per-call via the ``prefetch_margin`` argument to
# :meth:`DataPager.ensure_visible`.
DEFAULT_PREFETCH_MARGIN = 0.25

# Default LRU capacity. Four 1-month M1 tiles ≈ 4 × 43 200 rows ≈
# 170 k rows ≈ 8 MB at OHLC float32 + int32 — comfortably small while
# still covering ±2 months of scroll without spinning up the pool.
DEFAULT_MAX_RESIDENT = 4

_TileId = str
_LoaderFn = Callable[[str], pd.DataFrame]


# ---------------------------------------------------------------------------
# Tile descriptor
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PagerTile:
    """One declarable tile.

    A tile is the unit the pager loads / evicts. Two tiles MUST NOT
    overlap in time — :meth:`DataPager.set_tiles` validates this and
    raises ``ValueError`` on overlap.

    Parameters
    ----------
    tile_id:
        Unique key. Used in signal payloads and as the LRU cache key.
        The pager never inspects the value — callers can use a path,
        UUID, or business-meaningful slug.
    filepath:
        Path passed to the loader function. The pager itself doesn't
        open the file; the loader does.
    time_min, time_max:
        Inclusive bounds on the tile's time column. May be ``None``
        when the bounds aren't known until after load — in that case
        the pager skips the overlap check and won't be able to do
        viewport-aware prefetch until ``time_min`` / ``time_max``
        land via :meth:`DataPager.set_tile_bounds`.
    """

    tile_id: _TileId
    filepath: str
    time_min: Optional[pd.Timestamp] = None
    time_max: Optional[pd.Timestamp] = None

    def overlaps(self, t_min: pd.Timestamp, t_max: pd.Timestamp) -> bool:
        """True when ``[t_min, t_max]`` intersects this tile's range.

        Tiles with unknown bounds (either ``time_min`` or
        ``time_max`` is ``None``) are treated as *always overlapping*
        so the pager errs on the side of loading. Callers that want
        precise bounds should set them on the tile descriptor before
        :meth:`DataPager.set_tiles`.
        """
        if self.time_min is None or self.time_max is None:
            return True
        return not (t_max < self.time_min or t_min > self.time_max)


# ---------------------------------------------------------------------------
# Internal cache entry — wraps a loaded DataFrame with bookkeeping the
# pager needs (last-touch timestamp for LRU, byte estimate for future
# memory-budget eviction policies).
# ---------------------------------------------------------------------------


@dataclass
class _ResidentChunk:
    tile_id: _TileId
    df: pd.DataFrame
    seq: int  # monotonically increasing touch counter for LRU
    bytes_estimate: int = field(default=0)

    def __post_init__(self) -> None:
        # ``memory_usage`` is cheap (sums column ``nbytes``) so we
        # cache it once at construction time. ``deep=False`` is fine
        # because OHLC columns are pure numeric — no object dtype to
        # walk.
        try:
            self.bytes_estimate = int(self.df.memory_usage(deep=False).sum())
        except Exception:  # pragma: no cover — defensive
            self.bytes_estimate = 0


# ---------------------------------------------------------------------------
# Worker that runs the loader call in a thread pool
# ---------------------------------------------------------------------------


class _LoaderRunnable(QRunnable):
    """QRunnable wrapping a single ``loader_fn(filepath)`` call.

    The worker doesn't emit Qt signals directly — it calls back into
    the pager (via a thread-safe ``_handle_loaded`` / ``_handle_error``
    pair) which is the only place we touch the resident cache. This
    keeps mutation behind a single mutex instead of spreading lock
    acquisition across multiple objects.
    """

    def __init__(
        self,
        pager: "DataPager",
        tile: PagerTile,
        loader_fn: _LoaderFn,
    ) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._pager = pager
        self._tile = tile
        self._loader_fn = loader_fn

    @Slot()
    def run(self) -> None:  # pragma: no cover — body delegates to pager
        try:
            df = self._loader_fn(self._tile.filepath)
        except Exception as exc:
            logger.exception(
                "DataPager: load failed for tile %s (%s)",
                self._tile.tile_id,
                self._tile.filepath,
            )
            self._pager._handle_error(self._tile, str(exc))
            return
        self._pager._handle_loaded(self._tile, df)


# ---------------------------------------------------------------------------
# Public pager
# ---------------------------------------------------------------------------


class DataPager(QObject):
    """Tile-based asynchronous data cache.

    Lifecycle
    ---------
    1. Construct with a ``loader_fn(filepath) -> DataFrame`` and an
       optional ``QThreadPool``. The default is the global pool.
    2. Call :meth:`set_tiles` once to declare the static tile layout.
    3. Call :meth:`ensure_visible(t_min, t_max)` whenever the chart
       viewport changes. The pager schedules any missing tiles and
       evicts surplus ones.
    4. Listen for :attr:`chunks_changed` to receive a fresh concat
       of resident tiles. Listen for :attr:`pager_error` for
       per-tile failures.
    5. Call :meth:`shutdown(wait=True)` from the host's teardown
       hook (or from test fixtures) — the pager refuses further
       schedules and blocks until in-flight workers finish.

    Threading contract
    ------------------
    Every public method except :meth:`get_dataframe` and the read-
    only inspectors is safe to call from any thread — internal state
    is guarded by a single ``RLock``. :meth:`get_dataframe` returns
    a *snapshot* of the resident chunks taken under the lock, so
    callers don't need to coordinate.
    """

    #: Emitted after each successful tile load. Payload is
    #: ``(tile_id, rows_loaded)``. ``tile_id`` matches the
    #: descriptor's ``tile_id``; ``rows_loaded`` is the number of
    #: rows that newly arrived in the resident cache.
    tile_loaded = Signal(str, int)

    #: Emitted whenever the resident cache changes (new tile, tile
    #: evicted, or :meth:`set_tiles` called). Payload is the total
    #: number of rows currently resident across all tiles. Consumers
    #: should call :meth:`get_dataframe` to fetch the actual data.
    chunks_changed = Signal(int)

    #: Emitted on loader failure. Payload is ``(tile_id, error_str)``.
    pager_error = Signal(str, str)

    def __init__(
        self,
        loader_fn: _LoaderFn,
        *,
        max_resident: int = DEFAULT_MAX_RESIDENT,
        max_resident_bytes: Optional[int] = None,
        thread_pool: Optional[QThreadPool] = None,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        if not callable(loader_fn):
            raise TypeError("loader_fn must be callable")
        if max_resident < 1:
            raise ValueError("max_resident must be >= 1")
        self._loader_fn = loader_fn
        self._max_resident = int(max_resident)
        self._max_resident_bytes = max_resident_bytes
        self._pool = thread_pool or QThreadPool()

        # All cache state is guarded by ``_lock``. The mutex is
        # re-entrant so the worker callback (running on a pool
        # thread) can call back into pager methods without
        # deadlocking against itself.
        self._lock = threading.RLock()
        self._tiles: dict[_TileId, PagerTile] = {}
        self._resident: dict[_TileId, _ResidentChunk] = {}
        self._in_flight: set[_TileId] = set()
        self._touch_counter: int = 0
        self._shut_down: bool = False

    # ------------------------------------------------------------------
    # Tile registry
    # ------------------------------------------------------------------

    def set_tiles(self, tiles: List[PagerTile]) -> None:
        """Replace the tile registry.

        Clears the resident cache because previous tiles may have
        come from a different file set. Emits
        :attr:`chunks_changed` with payload ``0`` so consumers
        re-read state.

        Raises ``ValueError`` on duplicate ``tile_id`` or overlapping
        time ranges between tiles whose bounds are both known.
        """
        if not isinstance(tiles, list):
            raise TypeError("tiles must be a list of PagerTile")
        seen: set[_TileId] = set()
        bounded: List[PagerTile] = []
        for t in tiles:
            if not isinstance(t, PagerTile):
                raise TypeError(f"expected PagerTile, got {type(t).__name__}")
            if t.tile_id in seen:
                raise ValueError(f"duplicate tile_id: {t.tile_id!r}")
            seen.add(t.tile_id)
            if t.time_min is not None and t.time_max is not None:
                if t.time_min > t.time_max:
                    raise ValueError(
                        f"tile {t.tile_id}: time_min ({t.time_min}) "
                        f"must be ≤ time_max ({t.time_max})"
                    )
                bounded.append(t)

        # Overlap check restricted to bounded tiles. Two tiles with
        # unknown bounds can't be checked at registration time, so
        # we trust the caller (or the post-load fixup via
        # :meth:`set_tile_bounds`).
        bounded.sort(key=lambda x: x.time_min)  # type: ignore[arg-type, return-value]
        for a, b in zip(bounded, bounded[1:]):
            # ``a.time_max`` and ``b.time_min`` are both not None.
            if a.time_max > b.time_min:  # type: ignore[operator]
                raise ValueError(
                    f"overlapping tiles: {a.tile_id} ({a.time_max}) "
                    f"and {b.tile_id} ({b.time_min})"
                )

        with self._lock:
            self._tiles = {t.tile_id: t for t in tiles}
            self._resident.clear()
            self._in_flight.clear()
            self._touch_counter = 0
            self._cached_concat = pd.DataFrame()
        # Signals must fire outside the lock to avoid re-entrant
        # slot calls re-acquiring it on the same thread.
        self.chunks_changed.emit(0)

    def set_tile_bounds(
        self,
        tile_id: _TileId,
        time_min: pd.Timestamp,
        time_max: pd.Timestamp,
    ) -> None:
        """Late-bind a tile's time bounds.

        Useful when bounds aren't known until the first load
        completes — the pager itself doesn't peek inside loaded
        DataFrames (different schemas, different time-column names),
        so the integration layer (ChartTab / DataLoader) is
        responsible for telling the pager what each tile spans.

        No-op when the tile_id is unknown.
        """
        with self._lock:
            existing = self._tiles.get(tile_id)
            if existing is None:
                return
            self._tiles[tile_id] = PagerTile(
                tile_id=existing.tile_id,
                filepath=existing.filepath,
                time_min=pd.Timestamp(time_min),
                time_max=pd.Timestamp(time_max),
            )

    # ------------------------------------------------------------------
    # Read-only inspectors (cheap, lock-protected)
    # ------------------------------------------------------------------

    def list_tiles(self) -> List[PagerTile]:
        """Return a snapshot of the declared tile registry."""
        with self._lock:
            return list(self._tiles.values())

    def resident_tile_ids(self) -> List[_TileId]:
        """Return the IDs of tiles currently in the cache, ordered
        by most-recent touch (LRU head first)."""
        with self._lock:
            return [
                tid
                for tid, _ in sorted(
                    self._resident.items(), key=lambda kv: -kv[1].seq
                )
            ]

    def is_loaded(self, tile_id: _TileId) -> bool:
        with self._lock:
            return tile_id in self._resident

    def is_in_flight(self, tile_id: _TileId) -> bool:
        with self._lock:
            return tile_id in self._in_flight

    def resident_rows(self) -> int:
        with self._lock:
            return sum(len(c.df) for c in self._resident.values())

    def resident_bytes(self) -> int:
        with self._lock:
            return sum(c.bytes_estimate for c in self._resident.values())

    @property
    def max_resident(self) -> int:
        return self._max_resident

    # ------------------------------------------------------------------
    # Scheduling
    # ------------------------------------------------------------------

    def ensure_visible(
        self,
        t_min: pd.Timestamp,
        t_max: pd.Timestamp,
        *,
        prefetch_margin: float = DEFAULT_PREFETCH_MARGIN,
    ) -> List[_TileId]:
        """Schedule loads for tiles overlapping ``[t_min, t_max]``.

        Expands the window by ``prefetch_margin`` (fraction of the
        window's span) on each side before picking tiles, so the
        pager has time to land neighbouring tiles before they
        actually scroll into view. ``prefetch_margin=0`` disables
        prefetch entirely (tiles only load when their range crosses
        the requested window).

        Returns the list of tile IDs that were scheduled this call —
        useful for tests and for chart code that wants to log
        "warming N tiles". Tiles already resident or in flight are
        skipped.

        No-op when the pager has been shut down.
        """
        if self._shut_down:
            return []
        if prefetch_margin < 0:
            raise ValueError("prefetch_margin must be >= 0")

        t_min = pd.Timestamp(t_min)
        t_max = pd.Timestamp(t_max)
        if t_min > t_max:
            t_min, t_max = t_max, t_min

        # Expand by the prefetch margin (fraction of the window).
        span = t_max - t_min
        if span.total_seconds() > 0 and prefetch_margin > 0:
            pad = span * prefetch_margin
            t_min -= pad
            t_max += pad

        scheduled: List[_TileId] = []
        with self._lock:
            for tile in self._tiles.values():
                if not tile.overlaps(t_min, t_max):
                    continue
                if tile.tile_id in self._resident:
                    self._touch(tile.tile_id)
                    continue
                if tile.tile_id in self._in_flight:
                    continue
                self._in_flight.add(tile.tile_id)
                scheduled.append(tile.tile_id)
                self._pool.start(_LoaderRunnable(self, tile, self._loader_fn))
        return scheduled

    def _touch(self, tile_id: _TileId) -> None:
        """Bump the LRU sequence number for ``tile_id``. Caller must
        hold ``_lock``."""
        self._touch_counter += 1
        chunk = self._resident.get(tile_id)
        if chunk is not None:
            chunk.seq = self._touch_counter

    # ------------------------------------------------------------------
    # Worker callbacks
    # ------------------------------------------------------------------

    def _handle_loaded(self, tile: PagerTile, df: pd.DataFrame) -> None:
        """Called by ``_LoaderRunnable`` on the pool thread.

        Inserts the loaded chunk under the lock, evicts surplus tiles
        if we're over capacity, and fires the post-load signals. The
        signals themselves marshal back to the GUI thread via Qt's
        queued connections — callers don't need to manually invoke
        ``QMetaObject.invokeMethod``.
        """
        evicted: List[_TileId] = []
        rows_total = 0
        with self._lock:
            self._in_flight.discard(tile.tile_id)
            if self._shut_down:
                return
            self._touch_counter += 1
            chunk = _ResidentChunk(
                tile_id=tile.tile_id, df=df, seq=self._touch_counter
            )
            self._resident[tile.tile_id] = chunk
            # LRU eviction — drop oldest until we're at capacity.
            while len(self._resident) > self._max_resident or (
                self._max_resident_bytes and sum(c.bytes_estimate for c in self._resident.values()) > self._max_resident_bytes
            ):
                victim_id = min(
                    self._resident.items(), key=lambda kv: kv[1].seq
                )[0]
                if victim_id == tile.tile_id:
                    # Pathological: the tile we just added is also
                    # the oldest. That can only happen when
                    # ``max_resident == 0`` (which we disallow) or
                    # when set_tiles ran between schedule and
                    # callback. Bail to avoid infinite loop.
                    break
                self._resident.pop(victim_id, None)
                evicted.append(victim_id)
            rows_total = sum(len(c.df) for c in self._resident.values())

            # Pre-compute the concatenated frame on the worker thread
            chunks = [c.df for c in self._resident.values()]
            if not chunks:
                self._cached_concat = pd.DataFrame()
            else:
                frame = pd.concat(chunks, ignore_index=True)
                if "time" in frame.columns:
                    frame = frame.sort_values("time", kind="mergesort").reset_index(drop=True)
                self._cached_concat = frame

        logger.debug(
            "DataPager: tile %s landed (rows=%d, resident=%d, evicted=%s)",
            tile.tile_id,
            len(df),
            rows_total,
            evicted,
        )
        self.tile_loaded.emit(tile.tile_id, int(len(df)))
        self.chunks_changed.emit(int(rows_total))

    def _handle_error(self, tile: PagerTile, msg: str) -> None:
        with self._lock:
            self._in_flight.discard(tile.tile_id)
        self.pager_error.emit(tile.tile_id, msg)

    # ------------------------------------------------------------------
    # Read-out
    # ------------------------------------------------------------------

    def get_dataframe(self) -> pd.DataFrame:
        """Return the pre-computed contiguous frame of all resident tiles.
        
        Returns an empty DataFrame (no columns) when nothing is
        resident. Callers should treat the returned frame as read-
        only — mutating it doesn't write back to the cache.
        """
        with self._lock:
            return getattr(self, "_cached_concat", pd.DataFrame())

    def get_dataframe_for_range(
        self, t_min: pd.Timestamp, t_max: pd.Timestamp
    ) -> pd.DataFrame:
        """Return only the rows that fall within ``[t_min, t_max]``.

        Convenience for chart code that wants to feed the visible
        window directly into a pyramid build without re-slicing.
        """
        df = self.get_dataframe()
        if df.empty or "time" not in df.columns:
            return df
        t_min = pd.Timestamp(t_min)
        t_max = pd.Timestamp(t_max)
        mask = (df["time"] >= t_min) & (df["time"] <= t_max)
        return df.loc[mask].reset_index(drop=True)

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------

    def shutdown(self, *, wait: bool = True, timeout_ms: int = 5_000) -> bool:
        """Refuse further schedules and wait for in-flight workers.

        Returns ``True`` when all workers finished within
        ``timeout_ms``. The pool is the global ``QThreadPool``
        instance unless the caller injected a custom one, so we
        only wait on *that* pool — other consumers' jobs aren't
        affected.
        """
        with self._lock:
            self._shut_down = True
        if not wait:
            return True
        return self._pool.waitForDone(timeout_ms)


# ---------------------------------------------------------------------------
# Convenience helpers — file-extension dispatch + tile builders
# ---------------------------------------------------------------------------


def default_loader_fn(filepath: str) -> pd.DataFrame:
    """Default loader: picks CSV / Parquet / Feather by extension.

    Used by :func:`build_pager_for_files` when no loader is passed
    explicitly. Imports :class:`engine.data.data_loader.DataLoader`
    lazily so this module doesn't depend on it at import time
    (handy for the unit tests, which inject a stub loader).
    """
    from engine.data.data_loader import DataLoader

    ext = os.path.splitext(filepath)[1].lower()
    if ext in (".parquet", ".pq"):
        return DataLoader.load_parquet(filepath)
    if ext in (".feather", ".ipc"):
        return DataLoader.load_feather(filepath)
    return DataLoader.load_csv(filepath)


def tiles_from_files(
    files: List[str],
    *,
    bounds_from_disk: bool = False,
) -> List[PagerTile]:
    """Build a list of :class:`PagerTile` descriptors from a list of
    file paths.

    Parameters
    ----------
    files:
        Paths to OHLCV files. Each gets a tile whose ``tile_id`` is
        the basename of the path. Duplicate basenames raise
        ``ValueError`` so the caller is forced to rename them.
    bounds_from_disk:
        When ``True``, do a one-time eager peek at each file to
        record the time bounds. This is expensive (it reads the
        whole file) so it defaults to ``False``; the pager then
        treats every tile as "always overlapping" until
        :meth:`DataPager.set_tile_bounds` is called.
    """
    seen: set[str] = set()
    tiles: List[PagerTile] = []
    for fp in files:
        tid = os.path.basename(fp)
        if tid in seen:
            raise ValueError(
                f"duplicate tile basename: {tid!r}; rename the file or "
                "use a custom tile_id"
            )
        seen.add(tid)
        if bounds_from_disk:
            try:
                df = default_loader_fn(fp)
                t_min = pd.Timestamp(df["time"].iloc[0])
                t_max = pd.Timestamp(df["time"].iloc[-1])
            except Exception:  # pragma: no cover — defensive
                logger.exception(
                    "tiles_from_files: bounds peek failed for %s", fp
                )
                t_min = t_max = None  # type: ignore[assignment]
        else:
            t_min = t_max = None  # type: ignore[assignment]
        tiles.append(
            PagerTile(tile_id=tid, filepath=fp, time_min=t_min, time_max=t_max)
        )
    return tiles


# ---------------------------------------------------------------------------
# Env-var / availability probe — same tri-state parser as the rest of
# the GPU plan (gpu_config, gl_candle).
# ---------------------------------------------------------------------------


from engine.utils.env_flag import truthy_env

_ENV_VAR = "AGREGAT_DATA_PAGER"

def is_pager_enabled() -> bool:
    """Read the ``AGREGAT_DATA_PAGER`` env var (default off)."""
    parsed = truthy_env(os.environ.get(_ENV_VAR))
    return bool(parsed) if parsed is not None else False


def make_pager(
    files: List[str],
    *,
    loader_fn: Optional[_LoaderFn] = None,
    max_resident: int = DEFAULT_MAX_RESIDENT,
    max_resident_bytes: Optional[int] = None,
    bounds_from_disk: bool = False,
    force: Optional[bool] = None,
) -> Optional[DataPager]:
    """Construct a :class:`DataPager` for the given files.

    Returns ``None`` when the file list is empty or when the env
    flag is off (so :class:`gui.chart_tab.tab.ChartTab` can ignore
    the return value and fall through to the legacy single-file
    path without conditional branches).
    """
    if not files:
        return None
        
    if force is None:
        if not is_pager_enabled():
            return None
    elif not force:
        return None
        
    fn = loader_fn or default_loader_fn
    pager = DataPager(fn, max_resident=max_resident, max_resident_bytes=max_resident_bytes)
    tiles = tiles_from_files(files, bounds_from_disk=bounds_from_disk)
    pager.set_tiles(tiles)
    return pager
