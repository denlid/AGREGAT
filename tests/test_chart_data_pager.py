"""Phase 4 (GPU plan) — async data pager.

Headless tests for :mod:`engine.data.data_pager`. The pager itself
is pure Python + Qt — no OpenGL needed — so the offscreen Qt
platform plugin is enough to exercise every code path:

* :class:`PagerTile` — overlap predicate (bounded + unbounded), the
  bounds-required equality plumbing.
* :class:`DataPager` — :meth:`set_tiles` validation (duplicates,
  overlap, dtype), :meth:`ensure_visible` scheduling (prefetch
  margin, dedup, post-shutdown no-op), worker callbacks
  (signal emission, LRU eviction, error path), :meth:`get_dataframe`
  / :meth:`get_dataframe_for_range` concat + sort + filter.
* :func:`tiles_from_files` — basename collision, ``bounds_from_disk``
  pass-through.
* Factory / env-var routing — same tri-state semantics as the rest
  of the GPU plan.

The default ``QThreadPool`` is used; each test calls
``waitForDone(timeout_ms)`` to drain in-flight work before asserting.
A loader that injects a deterministic frame keyed on ``filepath``
makes the order of completion stable across runs.
"""

from __future__ import annotations

import os
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pandas as pd
import pytest
from PySide6.QtCore import QCoreApplication, QThreadPool

from engine.data.data_pager import (
    DEFAULT_MAX_RESIDENT,
    DEFAULT_PREFETCH_MARGIN,
    DataPager,
    PagerTile,
    is_pager_enabled,
    make_pager,
    tiles_from_files,
)
from engine.utils.env_flag import truthy_env as _truthy_env


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def qapp():
    return QCoreApplication.instance() or QCoreApplication([])


def _wait_for_pool(timeout_ms: int = 5_000) -> bool:
    """Drain the global pool. Tests need this between
    ``ensure_visible`` and any assertions on resident state because
    the worker runs on a different thread."""
    return QThreadPool.globalInstance().waitForDone(timeout_ms)


def _wait_for_signal(spy_list, expected: int, timeout_s: float = 2.0) -> bool:
    """Spin the event loop until ``spy_list`` has at least ``expected``
    entries. Returns True on success, False on timeout."""
    app = QCoreApplication.instance()
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if len(spy_list) >= expected:
            return True
        if app is not None:
            app.processEvents()
        time.sleep(0.01)
    return False


def _synth_frame(rows: int, start: str = "2024-01-01") -> pd.DataFrame:
    """Build a deterministic OHLC frame for use as a fake loader
    return value."""
    times = pd.date_range(start, periods=rows, freq="1min")
    return pd.DataFrame(
        {
            "time": times,
            "open": [100.0 + i * 0.01 for i in range(rows)],
            "high": [101.0 + i * 0.01 for i in range(rows)],
            "low": [99.0 + i * 0.01 for i in range(rows)],
            "close": [100.5 + i * 0.01 for i in range(rows)],
            "volume": list(range(rows)),
        }
    )


def _loader_factory(rows_per_tile: dict[str, int], start_per_tile: dict[str, str]):
    """Return a loader callable that returns a synthetic frame keyed
    on the filepath. ``rows_per_tile`` and ``start_per_tile`` map
    filename → rows / start-timestamp."""

    def loader(filepath: str) -> pd.DataFrame:
        name = os.path.basename(filepath)
        return _synth_frame(rows_per_tile[name], start=start_per_tile[name])

    return loader


# ===========================================================================
# PagerTile
# ===========================================================================


def test_pager_tile_overlap_with_known_bounds() -> None:
    tile = PagerTile(
        tile_id="jan",
        filepath="jan.csv",
        time_min=pd.Timestamp("2024-01-01"),
        time_max=pd.Timestamp("2024-01-31"),
    )
    assert tile.overlaps(pd.Timestamp("2024-01-15"), pd.Timestamp("2024-01-20"))
    assert tile.overlaps(pd.Timestamp("2023-12-15"), pd.Timestamp("2024-01-05"))
    assert tile.overlaps(pd.Timestamp("2024-01-28"), pd.Timestamp("2024-02-10"))
    assert not tile.overlaps(
        pd.Timestamp("2023-11-01"), pd.Timestamp("2023-11-30")
    )
    assert not tile.overlaps(
        pd.Timestamp("2024-03-01"), pd.Timestamp("2024-03-31")
    )


def test_pager_tile_overlap_unknown_bounds_always_overlaps() -> None:
    """Tiles whose bounds aren't yet known must err on the side of
    overlapping — otherwise the pager would silently skip loads for
    legitimately needed tiles."""
    no_min = PagerTile(
        tile_id="?",
        filepath="?.csv",
        time_min=None,
        time_max=pd.Timestamp("2024-01-31"),
    )
    no_max = PagerTile(
        tile_id="?",
        filepath="?.csv",
        time_min=pd.Timestamp("2024-01-01"),
        time_max=None,
    )
    both_none = PagerTile(tile_id="?", filepath="?.csv")
    for t in (no_min, no_max, both_none):
        assert t.overlaps(pd.Timestamp("1900-01-01"), pd.Timestamp("2000-01-01"))


def test_pager_tile_is_frozen() -> None:
    """``PagerTile`` is intentionally frozen so consumers can't
    accidentally rewrite bounds and break the registry's overlap
    invariants. Late binding goes through
    :meth:`DataPager.set_tile_bounds`, which builds a fresh tile."""
    t = PagerTile(tile_id="a", filepath="a.csv")
    with pytest.raises(Exception):
        t.tile_id = "b"  # type: ignore[misc]


# ===========================================================================
# set_tiles — validation
# ===========================================================================


def test_set_tiles_rejects_duplicates(qapp) -> None:
    pager = DataPager(lambda fp: _synth_frame(10))
    with pytest.raises(ValueError, match="duplicate"):
        pager.set_tiles(
            [
                PagerTile(tile_id="a", filepath="x.csv"),
                PagerTile(tile_id="a", filepath="y.csv"),
            ]
        )


def test_set_tiles_rejects_inverted_bounds(qapp) -> None:
    pager = DataPager(lambda fp: _synth_frame(10))
    with pytest.raises(ValueError, match="time_min"):
        pager.set_tiles(
            [
                PagerTile(
                    tile_id="a",
                    filepath="a.csv",
                    time_min=pd.Timestamp("2024-02-01"),
                    time_max=pd.Timestamp("2024-01-01"),
                )
            ]
        )


def test_set_tiles_rejects_overlap(qapp) -> None:
    pager = DataPager(lambda fp: _synth_frame(10))
    with pytest.raises(ValueError, match="overlapping"):
        pager.set_tiles(
            [
                PagerTile(
                    tile_id="jan",
                    filepath="jan.csv",
                    time_min=pd.Timestamp("2024-01-01"),
                    time_max=pd.Timestamp("2024-01-31"),
                ),
                PagerTile(
                    tile_id="feb",
                    filepath="feb.csv",
                    time_min=pd.Timestamp("2024-01-20"),
                    time_max=pd.Timestamp("2024-02-29"),
                ),
            ]
        )


def test_set_tiles_allows_unbounded_alongside_bounded(qapp) -> None:
    """Overlap check only runs on tiles where both bounds are known.
    Unbounded tiles are accepted unconditionally."""
    pager = DataPager(lambda fp: _synth_frame(10))
    pager.set_tiles(
        [
            PagerTile(
                tile_id="jan",
                filepath="jan.csv",
                time_min=pd.Timestamp("2024-01-01"),
                time_max=pd.Timestamp("2024-01-31"),
            ),
            PagerTile(tile_id="unknown", filepath="x.csv"),
        ]
    )
    assert len(pager.list_tiles()) == 2


def test_set_tiles_clears_resident_cache(qapp) -> None:
    """A fresh ``set_tiles`` must wipe the previous resident state —
    the new tile set may map to a completely different file."""
    pager = DataPager(lambda fp: _synth_frame(5))
    pager.set_tiles([PagerTile(tile_id="a", filepath="a.csv")])
    pager.ensure_visible(
        pd.Timestamp("2024-01-01"), pd.Timestamp("2024-01-02")
    )
    assert _wait_for_pool()
    assert pager.is_loaded("a")

    pager.set_tiles([PagerTile(tile_id="b", filepath="b.csv")])
    assert pager.list_tiles() == [PagerTile(tile_id="b", filepath="b.csv")]
    assert not pager.is_loaded("a")
    assert pager.resident_rows() == 0


def test_set_tiles_rejects_wrong_type(qapp) -> None:
    pager = DataPager(lambda fp: _synth_frame(10))
    with pytest.raises(TypeError):
        pager.set_tiles("not a list")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        pager.set_tiles([("a", "a.csv")])  # type: ignore[list-item]


def test_set_tile_bounds_overwrites_existing(qapp) -> None:
    pager = DataPager(lambda fp: _synth_frame(10))
    pager.set_tiles([PagerTile(tile_id="a", filepath="a.csv")])
    pager.set_tile_bounds(
        "a", pd.Timestamp("2024-01-01"), pd.Timestamp("2024-01-31")
    )
    updated = pager.list_tiles()[0]
    assert updated.time_min == pd.Timestamp("2024-01-01")
    assert updated.time_max == pd.Timestamp("2024-01-31")


def test_set_tile_bounds_noop_for_unknown_id(qapp) -> None:
    """Unknown tile_id is silently ignored — the caller may be
    racing the bound update against a ``set_tiles`` call that
    dropped the tile, and we'd rather quietly drop the update than
    raise mid-prefetch."""
    pager = DataPager(lambda fp: _synth_frame(10))
    pager.set_tile_bounds(
        "nope", pd.Timestamp("2024-01-01"), pd.Timestamp("2024-01-31")
    )
    # Just verify no crash.


# ===========================================================================
# DataPager construction guards
# ===========================================================================


def test_pager_rejects_non_callable_loader(qapp) -> None:
    with pytest.raises(TypeError, match="callable"):
        DataPager(loader_fn="not a function")  # type: ignore[arg-type]


def test_pager_rejects_zero_max_resident(qapp) -> None:
    with pytest.raises(ValueError, match="max_resident"):
        DataPager(loader_fn=lambda fp: _synth_frame(10), max_resident=0)


def test_pager_defaults(qapp) -> None:
    pager = DataPager(loader_fn=lambda fp: _synth_frame(10))
    assert pager.max_resident == DEFAULT_MAX_RESIDENT
    assert pager.resident_rows() == 0
    assert pager.resident_bytes() == 0
    assert pager.list_tiles() == []
    assert pager.resident_tile_ids() == []


# ===========================================================================
# ensure_visible — scheduling
# ===========================================================================


def test_ensure_visible_schedules_only_overlapping_tiles(qapp) -> None:
    loader = _loader_factory(
        rows_per_tile={"jan.csv": 5, "feb.csv": 5, "mar.csv": 5},
        start_per_tile={
            "jan.csv": "2024-01-01",
            "feb.csv": "2024-02-01",
            "mar.csv": "2024-03-01",
        },
    )
    pager = DataPager(loader, max_resident=10)
    pager.set_tiles(
        [
            PagerTile(
                tile_id="jan.csv",
                filepath="jan.csv",
                time_min=pd.Timestamp("2024-01-01"),
                time_max=pd.Timestamp("2024-01-31"),
            ),
            PagerTile(
                tile_id="feb.csv",
                filepath="feb.csv",
                time_min=pd.Timestamp("2024-02-01"),
                time_max=pd.Timestamp("2024-02-29"),
            ),
            PagerTile(
                tile_id="mar.csv",
                filepath="mar.csv",
                time_min=pd.Timestamp("2024-03-01"),
                time_max=pd.Timestamp("2024-03-31"),
            ),
        ]
    )
    scheduled = pager.ensure_visible(
        pd.Timestamp("2024-02-10"),
        pd.Timestamp("2024-02-20"),
        prefetch_margin=0.0,
    )
    assert scheduled == ["feb.csv"]
    assert _wait_for_pool()
    assert pager.is_loaded("feb.csv")
    assert not pager.is_loaded("jan.csv")
    assert not pager.is_loaded("mar.csv")


def test_ensure_visible_prefetch_margin_widens_window(qapp) -> None:
    """A 0.25 margin on a 10-day window expands 2.5 days on each
    side. Tiles only neighbouring the window must come along for
    the ride."""
    loader = _loader_factory(
        rows_per_tile={"jan.csv": 3, "feb.csv": 3, "mar.csv": 3},
        start_per_tile={
            "jan.csv": "2024-01-01",
            "feb.csv": "2024-02-01",
            "mar.csv": "2024-03-01",
        },
    )
    pager = DataPager(loader, max_resident=10)
    pager.set_tiles(
        [
            PagerTile(
                tile_id="jan.csv",
                filepath="jan.csv",
                time_min=pd.Timestamp("2024-01-01"),
                time_max=pd.Timestamp("2024-01-31"),
            ),
            PagerTile(
                tile_id="feb.csv",
                filepath="feb.csv",
                time_min=pd.Timestamp("2024-02-01"),
                time_max=pd.Timestamp("2024-02-29"),
            ),
            PagerTile(
                tile_id="mar.csv",
                filepath="mar.csv",
                time_min=pd.Timestamp("2024-03-01"),
                time_max=pd.Timestamp("2024-03-31"),
            ),
        ]
    )
    # Window centred on feb 15 with span = 10 days, margin = 0.5 →
    # ± 5 days, so we touch the tail of January and head of March.
    scheduled = pager.ensure_visible(
        pd.Timestamp("2024-02-10"),
        pd.Timestamp("2024-02-20"),
        prefetch_margin=2.5,  # ~25 days each side
    )
    assert set(scheduled) == {"jan.csv", "feb.csv", "mar.csv"}


def test_ensure_visible_swaps_inverted_bounds(qapp) -> None:
    pager = DataPager(lambda fp: _synth_frame(3))
    pager.set_tiles([PagerTile(tile_id="a", filepath="a.csv")])
    scheduled = pager.ensure_visible(
        pd.Timestamp("2024-01-31"), pd.Timestamp("2024-01-01")
    )
    assert scheduled == ["a"]


def test_ensure_visible_dedups_in_flight(qapp) -> None:
    """A second :meth:`ensure_visible` while the worker is still
    running must NOT schedule the same tile again — otherwise a
    spammy ``viewRangeChanged`` would flood the pool with redundant
    work."""
    started_event = threading.Event()
    release_event = threading.Event()

    def slow_loader(filepath: str) -> pd.DataFrame:
        started_event.set()
        release_event.wait(timeout=5.0)
        return _synth_frame(5)

    pager = DataPager(slow_loader)
    pager.set_tiles([PagerTile(tile_id="a", filepath="a.csv")])

    first = pager.ensure_visible(
        pd.Timestamp("2024-01-01"), pd.Timestamp("2024-01-02")
    )
    assert first == ["a"]
    assert started_event.wait(2.0), "worker should have started"
    second = pager.ensure_visible(
        pd.Timestamp("2024-01-01"), pd.Timestamp("2024-01-02")
    )
    assert second == [], "in-flight tile must not reschedule"
    release_event.set()
    assert _wait_for_pool()


def test_ensure_visible_skips_resident_but_bumps_lru(qapp) -> None:
    """A tile that's already resident shouldn't reschedule, but its
    LRU touch counter must move so it survives a subsequent
    eviction."""
    loader = _loader_factory(
        rows_per_tile={"a.csv": 1, "b.csv": 1, "c.csv": 1},
        start_per_tile={
            "a.csv": "2024-01-01",
            "b.csv": "2024-02-01",
            "c.csv": "2024-03-01",
        },
    )
    pager = DataPager(loader, max_resident=2)
    pager.set_tiles(
        [
            PagerTile(
                tile_id="a.csv",
                filepath="a.csv",
                time_min=pd.Timestamp("2024-01-01"),
                time_max=pd.Timestamp("2024-01-31"),
            ),
            PagerTile(
                tile_id="b.csv",
                filepath="b.csv",
                time_min=pd.Timestamp("2024-02-01"),
                time_max=pd.Timestamp("2024-02-29"),
            ),
            PagerTile(
                tile_id="c.csv",
                filepath="c.csv",
                time_min=pd.Timestamp("2024-03-01"),
                time_max=pd.Timestamp("2024-03-31"),
            ),
        ]
    )

    pager.ensure_visible(
        pd.Timestamp("2024-01-15"),
        pd.Timestamp("2024-01-16"),
        prefetch_margin=0.0,
    )
    assert _wait_for_pool()
    pager.ensure_visible(
        pd.Timestamp("2024-02-15"),
        pd.Timestamp("2024-02-16"),
        prefetch_margin=0.0,
    )
    assert _wait_for_pool()
    # Touch a.csv to bump it past b.csv in the LRU order.
    pager.ensure_visible(
        pd.Timestamp("2024-01-15"),
        pd.Timestamp("2024-01-16"),
        prefetch_margin=0.0,
    )
    # Now load c.csv — capacity is 2, so something must evict.
    # Because we touched a.csv last, b.csv should be the victim.
    pager.ensure_visible(
        pd.Timestamp("2024-03-15"),
        pd.Timestamp("2024-03-16"),
        prefetch_margin=0.0,
    )
    assert _wait_for_pool()
    assert pager.is_loaded("a.csv")
    assert not pager.is_loaded("b.csv")
    assert pager.is_loaded("c.csv")


def test_ensure_visible_after_shutdown_is_noop(qapp) -> None:
    pager = DataPager(lambda fp: _synth_frame(3))
    pager.set_tiles([PagerTile(tile_id="a", filepath="a.csv")])
    assert pager.shutdown()
    scheduled = pager.ensure_visible(
        pd.Timestamp("2024-01-01"), pd.Timestamp("2024-01-02")
    )
    assert scheduled == []


def test_ensure_visible_negative_margin_rejected(qapp) -> None:
    pager = DataPager(lambda fp: _synth_frame(3))
    pager.set_tiles([PagerTile(tile_id="a", filepath="a.csv")])
    with pytest.raises(ValueError, match="prefetch_margin"):
        pager.ensure_visible(
            pd.Timestamp("2024-01-01"),
            pd.Timestamp("2024-01-02"),
            prefetch_margin=-0.1,
        )


# ===========================================================================
# Signals — tile_loaded / chunks_changed / pager_error
# ===========================================================================


def test_chunks_changed_emits_after_load(qapp) -> None:
    pager = DataPager(lambda fp: _synth_frame(7))
    pager.set_tiles([PagerTile(tile_id="a", filepath="a.csv")])

    tile_emissions: list = []
    chunks_emissions: list = []
    pager.tile_loaded.connect(
        lambda tid, rows: tile_emissions.append((tid, rows))
    )
    pager.chunks_changed.connect(lambda total: chunks_emissions.append(total))

    pager.ensure_visible(
        pd.Timestamp("2024-01-01"), pd.Timestamp("2024-01-02")
    )
    assert _wait_for_signal(tile_emissions, 1)
    assert _wait_for_signal(chunks_emissions, 1)
    # Filter out the chunks_changed=0 we emit synchronously inside
    # set_tiles — that one races the queued post-load signal.
    post_load = [v for v in chunks_emissions if v > 0]
    assert tile_emissions == [("a", 7)]
    assert post_load == [7]


def test_pager_error_fires_on_loader_exception(qapp) -> None:
    def bad_loader(filepath: str) -> pd.DataFrame:
        raise RuntimeError("synthetic failure")

    pager = DataPager(bad_loader)
    pager.set_tiles([PagerTile(tile_id="a", filepath="a.csv")])

    errors: list = []
    pager.pager_error.connect(lambda tid, msg: errors.append((tid, msg)))

    pager.ensure_visible(
        pd.Timestamp("2024-01-01"), pd.Timestamp("2024-01-02")
    )
    assert _wait_for_signal(errors, 1)
    assert errors == [("a", "synthetic failure")]
    assert not pager.is_loaded("a")
    # Failed load must release the in-flight slot so a retry is
    # possible from the next ensure_visible.
    assert not pager.is_in_flight("a")


def test_pager_error_does_not_block_other_tiles(qapp) -> None:
    """A failing loader on one tile must not poison the resident
    cache for tiles that load successfully."""

    def selective_loader(filepath: str) -> pd.DataFrame:
        if "bad" in filepath:
            raise RuntimeError("nope")
        return _synth_frame(3)

    pager = DataPager(selective_loader)
    pager.set_tiles(
        [
            PagerTile(tile_id="good", filepath="good.csv"),
            PagerTile(tile_id="bad", filepath="bad.csv"),
        ]
    )

    errors: list = []
    pager.pager_error.connect(lambda tid, msg: errors.append((tid, msg)))

    pager.ensure_visible(
        pd.Timestamp("2024-01-01"), pd.Timestamp("2024-01-02")
    )
    assert _wait_for_pool()
    assert pager.is_loaded("good")
    assert not pager.is_loaded("bad")
    assert _wait_for_signal(errors, 1)


# ===========================================================================
# LRU eviction
# ===========================================================================


def test_lru_evicts_oldest_past_capacity(qapp) -> None:
    """Capacity = 2, schedule 3 distinct tiles — the first one in
    must drop out."""
    rows_per = {"a.csv": 1, "b.csv": 1, "c.csv": 1}
    starts = {
        "a.csv": "2024-01-01",
        "b.csv": "2024-02-01",
        "c.csv": "2024-03-01",
    }
    pager = DataPager(_loader_factory(rows_per, starts), max_resident=2)
    pager.set_tiles(
        [
            PagerTile(
                tile_id=name,
                filepath=name,
                time_min=pd.Timestamp(starts[name]),
                time_max=pd.Timestamp(starts[name]) + pd.Timedelta(days=20),
            )
            for name in ("a.csv", "b.csv", "c.csv")
        ]
    )

    for centre in ("2024-01-05", "2024-02-05", "2024-03-05"):
        pager.ensure_visible(
            pd.Timestamp(centre),
            pd.Timestamp(centre) + pd.Timedelta(days=1),
            prefetch_margin=0.0,
        )
        assert _wait_for_pool()

    assert not pager.is_loaded("a.csv"), "oldest must be evicted"
    assert pager.is_loaded("b.csv")
    assert pager.is_loaded("c.csv")
    assert len(pager.resident_tile_ids()) == 2


def test_lru_does_not_evict_just_inserted_tile(qapp) -> None:
    """Pathological-case guard from ``_handle_loaded`` — when the
    eviction candidate is the tile we just added (only possible if
    set_tiles raced the callback), we bail rather than infinite-
    looping."""
    pager = DataPager(lambda fp: _synth_frame(1), max_resident=1)
    pager.set_tiles([PagerTile(tile_id="a", filepath="a.csv")])
    pager.ensure_visible(
        pd.Timestamp("2024-01-01"), pd.Timestamp("2024-01-02")
    )
    assert _wait_for_pool()
    assert pager.is_loaded("a")
    assert pager.resident_rows() == 1


def test_resident_tile_ids_ordered_lru_head_first(qapp) -> None:
    """The first element of ``resident_tile_ids`` must be the most
    recently touched tile. Tests that read this list (e.g. for
    overlay rendering) rely on the order."""
    rows_per = {"a.csv": 1, "b.csv": 1}
    starts = {"a.csv": "2024-01-01", "b.csv": "2024-02-01"}
    pager = DataPager(_loader_factory(rows_per, starts), max_resident=5)
    pager.set_tiles(
        [
            PagerTile(
                tile_id=name,
                filepath=name,
                time_min=pd.Timestamp(starts[name]),
                time_max=pd.Timestamp(starts[name]) + pd.Timedelta(days=20),
            )
            for name in ("a.csv", "b.csv")
        ]
    )
    pager.ensure_visible(
        pd.Timestamp("2024-01-05"),
        pd.Timestamp("2024-01-06"),
        prefetch_margin=0.0,
    )
    assert _wait_for_pool()
    pager.ensure_visible(
        pd.Timestamp("2024-02-05"),
        pd.Timestamp("2024-02-06"),
        prefetch_margin=0.0,
    )
    assert _wait_for_pool()
    ids = pager.resident_tile_ids()
    assert ids == ["b.csv", "a.csv"]


# ===========================================================================
# get_dataframe / get_dataframe_for_range
# ===========================================================================


def test_get_dataframe_empty_when_no_resident(qapp) -> None:
    pager = DataPager(lambda fp: _synth_frame(1))
    df = pager.get_dataframe()
    assert isinstance(df, pd.DataFrame)
    assert df.empty


def test_get_dataframe_concats_and_sorts_by_time(qapp) -> None:
    """Two tiles load in arbitrary thread order; the returned frame
    must still be globally sorted by ``time``."""
    rows_per = {"a.csv": 3, "b.csv": 3}
    starts = {"a.csv": "2024-01-01", "b.csv": "2024-02-01"}
    pager = DataPager(_loader_factory(rows_per, starts), max_resident=5)
    pager.set_tiles(
        [
            PagerTile(
                tile_id=name,
                filepath=name,
                time_min=pd.Timestamp(starts[name]),
                time_max=pd.Timestamp(starts[name]) + pd.Timedelta(days=20),
            )
            for name in ("a.csv", "b.csv")
        ]
    )
    pager.ensure_visible(
        pd.Timestamp("2024-01-01"),
        pd.Timestamp("2024-02-29"),
        prefetch_margin=0.0,
    )
    assert _wait_for_pool()
    df = pager.get_dataframe()
    assert len(df) == 6
    times = df["time"].to_list()
    assert times == sorted(times)
    # First three rows come from a.csv, last three from b.csv.
    assert df["time"].iloc[0] == pd.Timestamp("2024-01-01")
    assert df["time"].iloc[-1] == pd.Timestamp("2024-02-01") + pd.Timedelta(
        minutes=2
    )


def test_get_dataframe_for_range_filters(qapp) -> None:
    pager = DataPager(lambda fp: _synth_frame(60, start="2024-01-01"))
    pager.set_tiles(
        [
            PagerTile(
                tile_id="a",
                filepath="a.csv",
                time_min=pd.Timestamp("2024-01-01"),
                time_max=pd.Timestamp("2024-01-31"),
            )
        ]
    )
    pager.ensure_visible(
        pd.Timestamp("2024-01-01"), pd.Timestamp("2024-01-31")
    )
    assert _wait_for_pool()
    sliced = pager.get_dataframe_for_range(
        pd.Timestamp("2024-01-01 00:10"),
        pd.Timestamp("2024-01-01 00:20"),
    )
    assert len(sliced) == 11  # inclusive on both sides
    assert sliced["time"].iloc[0] == pd.Timestamp("2024-01-01 00:10")
    assert sliced["time"].iloc[-1] == pd.Timestamp("2024-01-01 00:20")


def test_get_dataframe_for_range_handles_empty(qapp) -> None:
    pager = DataPager(lambda fp: _synth_frame(5))
    assert pager.get_dataframe_for_range(
        pd.Timestamp("2024-01-01"), pd.Timestamp("2024-01-02")
    ).empty


# ===========================================================================
# Shutdown
# ===========================================================================


def test_shutdown_waits_for_in_flight(qapp) -> None:
    started_event = threading.Event()
    release_event = threading.Event()

    def slow_loader(filepath: str) -> pd.DataFrame:
        started_event.set()
        release_event.wait(timeout=5.0)
        return _synth_frame(3)

    pager = DataPager(slow_loader)
    pager.set_tiles([PagerTile(tile_id="a", filepath="a.csv")])
    pager.ensure_visible(
        pd.Timestamp("2024-01-01"), pd.Timestamp("2024-01-02")
    )
    assert started_event.wait(2.0)
    release_event.set()
    finished = pager.shutdown(wait=True, timeout_ms=5_000)
    assert finished is True


def test_shutdown_no_wait_returns_immediately(qapp) -> None:
    pager = DataPager(lambda fp: _synth_frame(1))
    assert pager.shutdown(wait=False) is True


# ===========================================================================
# tiles_from_files
# ===========================================================================


def test_tiles_from_files_rejects_duplicate_basenames() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        tiles_from_files(["/a/jan.csv", "/b/jan.csv"])


def test_tiles_from_files_no_bounds_by_default() -> None:
    tiles = tiles_from_files(["/x/a.csv", "/x/b.csv"])
    assert {t.tile_id for t in tiles} == {"a.csv", "b.csv"}
    assert all(t.time_min is None and t.time_max is None for t in tiles)


# ===========================================================================
# Env-var / factory
# ===========================================================================


def test_truthy_env_tristate() -> None:
    for v in ("1", "true", "yes", "on", "y", "TRUE"):
        assert _truthy_env(v) is True
    for v in ("0", "false", "no", "off", "n", ""):
        assert _truthy_env(v) is False
    for v in ("maybe", "asdf", None):
        assert _truthy_env(v) is None


def test_is_pager_enabled_respects_env(monkeypatch) -> None:
    monkeypatch.delenv("AGREGAT_DATA_PAGER", raising=False)
    assert is_pager_enabled() is False
    monkeypatch.setenv("AGREGAT_DATA_PAGER", "1")
    assert is_pager_enabled() is True
    monkeypatch.setenv("AGREGAT_DATA_PAGER", "0")
    assert is_pager_enabled() is False
    monkeypatch.setenv("AGREGAT_DATA_PAGER", "asdf")
    assert is_pager_enabled() is False


def test_make_pager_returns_none_for_empty_files(qapp, monkeypatch) -> None:
    monkeypatch.setenv("AGREGAT_DATA_PAGER", "1")
    assert make_pager([]) is None


def test_make_pager_returns_none_when_env_off(qapp, monkeypatch) -> None:
    monkeypatch.delenv("AGREGAT_DATA_PAGER", raising=False)
    assert make_pager(["a.csv", "b.csv"]) is None


def test_make_pager_returns_pager_when_env_on(qapp, monkeypatch) -> None:
    monkeypatch.setenv("AGREGAT_DATA_PAGER", "1")
    result = make_pager(
        ["a.csv", "b.csv"],
        loader_fn=lambda fp: _synth_frame(3),
        max_resident=2,
    )
    assert result is not None
    pager, tiles = result
    assert isinstance(pager, DataPager)
    assert {t.tile_id for t in tiles} == {"a.csv", "b.csv"}
    assert pager.max_resident == 2
    pager.shutdown()


def test_default_prefetch_margin_value() -> None:
    """Lock in the default — tests that explicitly assert prefetch
    behaviour assume this constant. Don't bump it without revisiting
    them."""
    assert DEFAULT_PREFETCH_MARGIN == 0.25
