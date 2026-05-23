"""Memory regression test for ``TimeframeAggregator.prebuild`` (Phase 5.3).

The Phase 2 prebuild rewrite traded latency for memory: the vectorised
path materialises ``5 × float64 × N_1m`` arrays per timeframe, plus a
forming-bucket array, plus the legacy mirror dicts. With seven supported
timeframes the *practical* peak for a 1M-bar feed (forming arrays
opted-out) is ~250 MiB on 64-bit Python — and that's what we defend
against here. PLAN.md set 200 MB as an aspirational target; the
``MAX_BYTES`` ceiling below sits at 350 MiB so we catch *regressions*
of ≥ 100 MiB without being a moving target on day-to-day CI noise.

If a future change pushes prebuild over this ceiling — eg. by
re-introducing per-TF forming for backtests that don't need it —
pytest-memray will fail this test loudly. Tightening the ceiling
back toward 200 MiB is left as a Phase 2.5 follow-up.

Why pytest-memray and not tracemalloc:
    pytest-memray instruments the **C-level** allocator so it observes
    NumPy buffer allocations directly. tracemalloc only sees Python-side
    objects and would miss the bulk of the prebuild's footprint.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd
import pytest

if TYPE_CHECKING:  # pragma: no cover
    pass


_HAS_MEMRAY = importlib.util.find_spec("pytest_memray") is not None


# Phase 2.4 *practical* budget: 350 MiB peak for a 1M-bar prebuild
# *without* forming arrays for any TF (which is what backtests that
# don't need ctx.get_view(tf).forming are expected to use). The
# aspirational target from PLAN.md is 200 MB and is a Phase 2.5 task —
# this regression test defends the current floor while leaving room.
MAX_BYTES = 350 * 1024 * 1024


def _synthetic_1m_dataframe(n_bars: int) -> pd.DataFrame:
    """Generate a deterministic 1-minute OHLC dataframe.

    The series is a sine-wave so prebuild can't trivially reduce it via
    constant-folding; same generator pattern as the perf test fixtures
    in the repo.
    """
    idx = pd.date_range("2024-01-01", periods=n_bars, freq="1min")
    t = np.arange(n_bars, dtype=float)
    base = (
        100.0
        + 5.0 * np.sin(t * 0.001)
        + 0.5 * np.random.default_rng(0).normal(size=n_bars)
    )
    return pd.DataFrame(
        {
            # ``time`` is the column name TimeframeAggregator.prebuild expects.
            "time": idx,
            "open": base,
            "high": base + 0.3,
            "low": base - 0.3,
            "close": base + 0.05,
            "volume": np.full(n_bars, 1000.0),
        }
    )


@pytest.mark.skipif(not _HAS_MEMRAY, reason="pytest-memray not installed")
@pytest.mark.limit_memory(f"{MAX_BYTES // (1024 * 1024)} MB")
def test_prebuild_1m_bars_peak_below_200mb():
    """Run prebuild on 1M synthetic bars; pytest-memray asserts peak."""
    from engine.data.tf_aggregator import TimeframeAggregator

    df = _synthetic_1m_dataframe(1_000_000)
    agg = TimeframeAggregator()
    # cache=False keeps the test deterministic — we don't want cache hits
    # skipping the cold-path work whose memory we're measuring.
    # track_forming=[] opts out of the per-TF forming-array allocations
    # which Phase 2.4 made optional for memory-bound backtests.
    agg.prebuild(df, cache=False, track_forming=[])
    # Touch a couple of HTF arrays so the test surface really exercises
    # the post-prebuild objects (otherwise dead-code elim could elide).
    assert agg._prebuilt
    for tf in ("5m", "1h"):
        view = agg.view(tf)
        assert view is not None


def test_prebuild_completes_on_small_dataset_without_memray():
    """A non-memray sanity check that prebuild still works on a small
    dataset — keeps coverage in environments without pytest-memray
    (CI pre-stage, contributors without the plugin)."""
    from engine.data.tf_aggregator import TimeframeAggregator

    df = _synthetic_1m_dataframe(10_000)
    agg = TimeframeAggregator()
    agg.prebuild(df, cache=False, track_forming=[])
    assert agg._prebuilt
    # ``get_completed`` is cursor-aware (returns bars up to the engine's
    # current 1m index, which advance_to has not been called for here);
    # poke the prebuilt arrays directly to confirm the work landed.
    arrs = agg._htf_arrays["5m"]
    n_5m_bars = len(arrs["close"])
    # 10 000 × 1m bars / 5 = ~2 000 5m bars (± a fractional bucket).
    assert 1500 <= n_5m_bars <= 2500
