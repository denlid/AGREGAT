"""Phase 2.1 — TF aggregator lazy forming + searchsorted memory shrink.

These tests guard the three-stage rewrite of
:py:meth:`engine.data.tf_aggregator.TimeframeAggregator.prebuild`:

* **Stage A** — ``track_forming`` is opt-in and skipping it does not
  build the per-1m forming arrays.
* **Stage B** — ``advance_to`` returns the same ``{tf: bool}`` and
  per-TF completed counts as the legacy implementation, but backed by
  ``searchsorted`` over the compact ``_completion_points`` array.
* **Stage C** — the dead ``_bucket_idx_at_1m`` field is gone; total
  prebuild memory drops well below the legacy baseline on a
  representative 1m feed.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from engine.data.tf_aggregator import TimeframeAggregator


def _df(n: int) -> pd.DataFrame:
    """One-minute synthetic OHLCV with deterministic prices."""
    times = pd.date_range("2024-01-01 00:00", periods=n, freq="1min", tz="UTC")
    closes = 100.0 + np.sin(np.arange(n) / 50.0)
    return pd.DataFrame(
        {
            "time": times,
            "open": closes,
            "high": closes + 0.05,
            "low": closes - 0.05,
            "close": closes,
            "volume": np.full(n, 1.0),
        }
    )


def test_track_forming_default_keeps_legacy_behaviour() -> None:
    """``track_forming=None`` (default) builds forming for every TF."""
    agg = TimeframeAggregator()
    agg.prebuild(_df(180))
    for tf in agg.TF_MINUTES:
        assert tf in agg._forming_arrays, f"{tf} forming missing under default"
    assert agg._forming_tfs == set(agg.TF_MINUTES.keys())


def test_track_forming_empty_skips_all_forming() -> None:
    """Empty iterable → no forming arrays allocated; ``get_forming`` returns
    None for every TF."""
    agg = TimeframeAggregator()
    agg.prebuild(_df(180), track_forming=[])
    assert agg._forming_arrays == {}
    assert agg._forming_tfs == set()
    for tf in agg.TF_MINUTES:
        assert agg.get_forming(tf) is None
    # Completed accessors must still work — they don't depend on forming.
    agg.advance_to(120)
    assert agg.get_completed("5m"), "completed bars must exist regardless of forming"


def test_track_forming_subset_only_allocates_for_listed_tfs() -> None:
    """Only TFs in ``track_forming`` get forming arrays; others stay
    legacy-empty (``None``)."""
    agg = TimeframeAggregator()
    agg.prebuild(_df(180), track_forming={"5m", "1h"})
    assert set(agg._forming_arrays.keys()) == {"5m", "1h"}
    assert agg._forming_tfs == {"5m", "1h"}
    agg.advance_to(60)
    assert agg.get_forming("5m") is not None
    assert agg.get_forming("1h") is not None
    assert agg.get_forming("4h") is None
    assert agg.get_forming("2m") is None


def test_track_forming_unknown_tf_silently_filtered() -> None:
    """Unknown TF strings in ``track_forming`` are dropped — no crash."""
    agg = TimeframeAggregator()
    agg.prebuild(_df(120), track_forming={"5m", "bogus", "7m"})
    assert agg._forming_tfs == {"5m"}


def test_completion_points_replace_full_length_arrays() -> None:
    """``_completion_points`` is per-HTF (size N_htf-1), not per-1m."""
    n = 720  # 12 hours
    agg = TimeframeAggregator()
    agg.prebuild(_df(n))
    for tf, minutes in agg.TF_MINUTES.items():
        cp = agg._completion_points[tf]
        # N_htf = ceil(n / minutes); N_htf - 1 ≤ N_htf.
        assert cp.dtype == np.int64
        assert cp.size <= int(np.ceil(n / minutes))
        # Strictly increasing — completion points are unique 1m indices.
        assert np.all(np.diff(cp) > 0), f"{tf}: completion points not monotonic"
        # Every value must be within [1, n-1] (index 0 can't complete a bar).
        if cp.size:
            assert cp[0] >= 1 and cp[-1] <= n


def test_advance_to_matches_legacy_count_per_step() -> None:
    """``advance_to`` count for every 1m index matches the brute-force
    "count of completed buckets so far" reference."""
    n = 600
    df = _df(n)
    agg = TimeframeAggregator()
    agg.prebuild(df)

    # Reference: walk the time column and count buckets manually.
    ref_counts: dict[str, np.ndarray] = {}
    for tf, minutes in agg.TF_MINUTES.items():
        bucket_starts = pd.to_datetime(df["time"]).dt.floor(f"{minutes}min").values
        boundary = np.empty(n, dtype=bool)
        boundary[0] = True
        boundary[1:] = bucket_starts[1:] != bucket_starts[:-1]
        completes = boundary.copy()
        completes[0] = False
        ref_counts[tf] = np.cumsum(completes).astype(np.int64)

    for idx in range(0, n, 7):  # sample every 7 bars to keep test fast
        agg.advance_to(idx)
        for tf in agg.TF_MINUTES:
            expected = int(ref_counts[tf][idx])
            assert agg._completed_count[tf] == expected, (
                f"{tf}@{idx}: expected count {expected}, got {agg._completed_count[tf]}"
            )


def test_advance_to_completes_flag_matches_boundary() -> None:
    """The ``{tf: bool}`` returned by ``advance_to`` is True iff the
    current 1m bar is the first bar of a new bucket."""
    n = 240  # 4 hours
    df = _df(n)
    agg = TimeframeAggregator()
    agg.prebuild(df)

    for tf, minutes in agg.TF_MINUTES.items():
        bucket_starts = pd.to_datetime(df["time"]).dt.floor(f"{minutes}min").values
        boundary = np.empty(n, dtype=bool)
        boundary[0] = True
        boundary[1:] = bucket_starts[1:] != bucket_starts[:-1]
        boundary[0] = False  # the very first bar can't complete anything

        for idx in range(n):
            res = agg.advance_to(idx)
            assert res[tf] == bool(boundary[idx]), (
                f"{tf}@{idx}: boundary mismatch, "
                f"got {res[tf]} expected {bool(boundary[idx])}"
            )


def test_bucket_idx_at_1m_is_dropped() -> None:
    """``_bucket_idx_at_1m`` was dead code; the attribute is gone after
    Phase 2.1 (Stage C)."""
    agg = TimeframeAggregator()
    agg.prebuild(_df(60))
    assert not hasattr(agg, "_bucket_idx_at_1m"), (
        "_bucket_idx_at_1m must be dropped — it was unused and cost ~560 MB "
        "on a 10M-bar feed"
    )


def test_legacy_completed_count_arrays_are_dropped() -> None:
    """``_completed_count_at_1m`` and ``_completes_at_1m`` are gone — the
    information is now reconstructed from ``_completion_points``."""
    agg = TimeframeAggregator()
    agg.prebuild(_df(60))
    assert not hasattr(agg, "_completed_count_at_1m")
    assert not hasattr(agg, "_completes_at_1m")


def test_memory_footprint_smaller_than_legacy_baseline() -> None:
    """On a 60k-bar feed the new layout uses < 5 MB across all per-TF
    bookkeeping arrays. The legacy layout was N_1m * num_tfs * (8+1) ~=
    3.8 MB just for the *bookkeeping* (excluding forming = 8.4 MB
    extra). This guard fails if a regression reintroduces the
    full-length arrays."""
    n = 60_000
    agg = TimeframeAggregator()
    agg.prebuild(_df(n), track_forming=[])  # Stage A opt-out

    bookkeeping_bytes = 0
    for cp in agg._completion_points.values():
        bookkeeping_bytes += cp.nbytes
    # Each completion-points array is at most N_htf int64s; total well
    # under 1 MB for every supported TF combined.
    assert bookkeeping_bytes < 1_000_000, (
        f"per-TF bookkeeping ballooned to {bookkeeping_bytes:,} bytes — "
        "Phase 2.1 regression"
    )

    forming_bytes = 0
    for arrs in agg._forming_arrays.values():
        for v in arrs.values():
            if isinstance(v, np.ndarray):
                forming_bytes += v.nbytes
    assert forming_bytes == 0, (
        "track_forming=[] should produce zero forming-array allocations"
    )


def test_reset_clears_phase21_state() -> None:
    """``reset`` (and re-prebuild) must clear the new completion-points
    map so a new dataset gets a fresh state."""
    agg = TimeframeAggregator()
    agg.prebuild(_df(120))
    assert agg._completion_points
    agg.prebuild(pd.DataFrame(columns=["time", "open", "high", "low", "close"]))
    assert agg._completion_points == {}
    assert agg._forming_arrays == {}
    assert agg._forming_tfs == set()
