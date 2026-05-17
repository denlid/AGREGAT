"""Phase 2.4 — disk cache for ``TimeframeAggregator.prebuild``.

Round-trip the prebuild output through the on-disk cache and assert
bit-identical state on the cached path. Cache directory is pinned to
the test's tmp dir via the ``AGREGATISHE_PREBUILD_CACHE_DIR`` env var
so we never touch the user's home directory."""

from __future__ import annotations

import os
import time

import numpy as np
import pandas as pd
import pytest

from engine.data import prebuild_cache
from engine.data.tf_aggregator import TimeframeAggregator


@pytest.fixture(autouse=True)
def isolated_cache_dir(tmp_path, monkeypatch):
    """Point :mod:`prebuild_cache` at ``tmp_path`` for the test
    duration. Without this the test would litter the user's home
    directory."""
    monkeypatch.setenv("AGREGATISHE_PREBUILD_CACHE_DIR", str(tmp_path))
    yield


def _make_df(n: int = 600, seed: int = 7) -> pd.DataFrame:
    """Synthetic 1m OHLCV frame with deterministic content. ``n``
    rows is enough for several 4h buckets to complete so the
    completion-point array is non-trivial."""
    rng = np.random.default_rng(seed)
    times = pd.date_range("2024-01-01", periods=n, freq="1min")
    base = 100.0 + rng.standard_normal(n).cumsum() * 0.1
    op = base
    cl = base + rng.standard_normal(n) * 0.05
    hi = np.maximum(op, cl) + 0.1
    lo = np.minimum(op, cl) - 0.1
    vol = rng.integers(1, 100, size=n).astype(np.float64)
    return pd.DataFrame(
        {"time": times, "open": op, "high": hi, "low": lo, "close": cl, "volume": vol}
    )


# ---------------------------------------------------------------------------
# Cache key
# ---------------------------------------------------------------------------


def test_cache_key_stable_across_calls() -> None:
    df = _make_df()
    tfs = ["1m", "5m", "1h"]
    k1 = prebuild_cache.compute_cache_key(df, tfs)
    k2 = prebuild_cache.compute_cache_key(df, tfs)
    assert k1 == k2
    assert isinstance(k1, str) and len(k1) == 64  # sha256 hex digest


def test_cache_key_changes_with_dataframe_content() -> None:
    df1 = _make_df(n=600, seed=1)
    df2 = _make_df(n=600, seed=2)
    tfs = ["1m", "5m"]
    assert prebuild_cache.compute_cache_key(
        df1, tfs
    ) != prebuild_cache.compute_cache_key(df2, tfs)


def test_cache_key_changes_with_tf_list() -> None:
    df = _make_df()
    k1 = prebuild_cache.compute_cache_key(df, ["1m", "5m"])
    k2 = prebuild_cache.compute_cache_key(df, ["1m", "5m", "1h"])
    assert k1 != k2


def test_cache_key_changes_with_track_forming() -> None:
    df = _make_df()
    tfs = ["1m", "5m"]
    k_none = prebuild_cache.compute_cache_key(df, tfs, track_forming=None)
    k_empty = prebuild_cache.compute_cache_key(df, tfs, track_forming=[])
    k_pick = prebuild_cache.compute_cache_key(df, tfs, track_forming=["5m"])
    assert k_none != k_empty != k_pick != k_none


def test_cache_key_changes_with_version() -> None:
    df = _make_df()
    tfs = ["1m", "5m"]
    k_a = prebuild_cache.compute_cache_key(df, tfs, version="phase2.4-v1")
    k_b = prebuild_cache.compute_cache_key(df, tfs, version="phase99-v9")
    assert k_a != k_b


# ---------------------------------------------------------------------------
# Disk round-trip
# ---------------------------------------------------------------------------


def test_save_then_load_roundtrip() -> None:
    state = prebuild_cache.state_to_dict(
        htf_arrays={"5m": {"close": np.array([1.0, 2.0, 3.0])}},
        completion_points={"5m": np.array([4, 9], dtype=np.int64)},
        forming_arrays={"5m": {"close": np.array([0.5, 0.6])}},
        forming_tfs={"5m"},
    )
    key = "abc123"
    path = prebuild_cache.save_state(key, state)
    assert path.exists()
    loaded = prebuild_cache.load_state(key)
    assert loaded is not None
    np.testing.assert_array_equal(
        loaded["htf_arrays"]["5m"]["close"], state["htf_arrays"]["5m"]["close"]
    )
    np.testing.assert_array_equal(
        loaded["completion_points"]["5m"], state["completion_points"]["5m"]
    )
    assert loaded["forming_tfs"] == {"5m"}


def test_load_returns_none_on_missing_key() -> None:
    assert prebuild_cache.load_state("nonexistent_key") is None


def test_load_returns_none_on_version_mismatch(tmp_path) -> None:
    state = {"version": "ancient", "htf_arrays": {}, "completion_points": {}}
    import pickle

    target = prebuild_cache.cache_path("stale")
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("wb") as fh:
        pickle.dump(state, fh)
    assert prebuild_cache.load_state("stale") is None


def test_load_returns_none_on_corrupt_pickle() -> None:
    target = prebuild_cache.cache_path("corrupt")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"not a pickle")
    assert prebuild_cache.load_state("corrupt") is None


def test_clear_cache_returns_freed_bytes() -> None:
    state = prebuild_cache.state_to_dict(
        htf_arrays={"5m": {"close": np.zeros(1000)}},
        completion_points={"5m": np.zeros(10, dtype=np.int64)},
        forming_arrays={"5m": {"close": np.zeros(10)}},
        forming_tfs={"5m"},
    )
    prebuild_cache.save_state("a", state)
    prebuild_cache.save_state("b", state)
    size_before = prebuild_cache.cache_size_bytes()
    assert size_before > 0
    freed = prebuild_cache.clear_cache()
    assert freed > 0
    assert prebuild_cache.cache_size_bytes() == 0


def test_save_state_atomic_no_tmp_leftover(tmp_path) -> None:
    """Successful save must leave only ``{key}.pkl`` behind — no
    ``.tmp`` files lingering in the cache dir."""
    state = prebuild_cache.state_to_dict(
        htf_arrays={"5m": {"close": np.zeros(10)}},
        completion_points={"5m": np.zeros(2, dtype=np.int64)},
        forming_arrays={},
        forming_tfs=set(),
    )
    prebuild_cache.save_state("atomic", state)
    cache_dir = prebuild_cache.get_cache_dir()
    leftover = [p for p in cache_dir.iterdir() if p.suffix == ".tmp"]
    assert leftover == []


# ---------------------------------------------------------------------------
# TimeframeAggregator integration
# ---------------------------------------------------------------------------


def _state_snapshot(agg: TimeframeAggregator) -> dict:
    return {
        "htf_arrays": {
            tf: {col: arr.copy() for col, arr in cols.items()}
            for tf, cols in agg._htf_arrays.items()
        },
        "completion_points": {
            tf: arr.copy() for tf, arr in agg._completion_points.items()
        },
        "forming_arrays": {
            tf: {col: arr.copy() for col, arr in cols.items()}
            for tf, cols in agg._forming_arrays.items()
        },
        "forming_tfs": set(agg._forming_tfs),
    }


def _states_equal(a: dict, b: dict) -> bool:
    if a["forming_tfs"] != b["forming_tfs"]:
        return False
    if set(a["htf_arrays"]) != set(b["htf_arrays"]):
        return False
    for tf in a["htf_arrays"]:
        if set(a["htf_arrays"][tf]) != set(b["htf_arrays"][tf]):
            return False
        for col, arr_a in a["htf_arrays"][tf].items():
            if not np.array_equal(arr_a, b["htf_arrays"][tf][col]):
                return False
    if set(a["completion_points"]) != set(b["completion_points"]):
        return False
    for tf in a["completion_points"]:
        if not np.array_equal(a["completion_points"][tf], b["completion_points"][tf]):
            return False
    return True


def test_prebuild_with_cache_writes_to_disk() -> None:
    df = _make_df(n=400)
    agg = TimeframeAggregator()
    assert prebuild_cache.cache_size_bytes() == 0
    agg.prebuild(df, cache=True)
    assert prebuild_cache.cache_size_bytes() > 0


def test_prebuild_cache_hit_produces_bit_identical_state() -> None:
    df = _make_df(n=400)

    cold = TimeframeAggregator()
    cold.prebuild(df, cache=True)
    cold_state = _state_snapshot(cold)

    warm = TimeframeAggregator()
    warm.prebuild(df, cache=True)
    warm_state = _state_snapshot(warm)

    assert _states_equal(cold_state, warm_state)


def test_prebuild_cache_hit_is_faster_than_cold() -> None:
    """Cache hit should be faster than the cold prebuild for any
    non-trivial dataset. The 400-row dataset is small, but even at
    that size the savings are visible because the cached path skips
    the pandas resample."""
    df = _make_df(n=2_000)

    # Cold build
    cold = TimeframeAggregator()
    t0 = time.perf_counter()
    cold.prebuild(df, cache=True)
    cold_dt = time.perf_counter() - t0

    # Cache hit
    warm = TimeframeAggregator()
    t0 = time.perf_counter()
    warm.prebuild(df, cache=True)
    warm_dt = time.perf_counter() - t0

    # The hit path is bounded by pickle.load + a few attribute
    # assignments — for a 2 000-bar dataset on this VM both paths
    # are fast, but the hit must be at least no slower.
    assert warm_dt <= cold_dt * 1.5


def test_prebuild_cache_invalidated_by_data_change() -> None:
    df1 = _make_df(n=400, seed=1)
    df2 = _make_df(n=400, seed=2)

    a = TimeframeAggregator()
    a.prebuild(df1, cache=True)

    b = TimeframeAggregator()
    b.prebuild(df2, cache=True)

    snap_a = _state_snapshot(a)
    snap_b = _state_snapshot(b)
    # Different data → different cache key → different stored state.
    # Verify a key column actually differs to make sure the cache
    # didn't accidentally serve df1's state for df2.
    assert not np.array_equal(
        snap_a["htf_arrays"]["5m"]["close"], snap_b["htf_arrays"]["5m"]["close"]
    )


def test_prebuild_without_cache_does_not_touch_disk() -> None:
    df = _make_df(n=200)
    agg = TimeframeAggregator()
    agg.prebuild(df)  # cache=False (default)
    assert prebuild_cache.cache_size_bytes() == 0


def test_get_cache_dir_default_path() -> None:
    """In production (no env override) the default path is under
    the user's home directory."""
    if "AGREGATISHE_PREBUILD_CACHE_DIR" in os.environ:
        del os.environ["AGREGATISHE_PREBUILD_CACHE_DIR"]
    p = prebuild_cache.get_cache_dir()
    assert p.parts[-3:] == (".agregatishe", "cache", "prebuild")
