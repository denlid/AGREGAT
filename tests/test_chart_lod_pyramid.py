"""Phase 2 — LOD pyramid + chart viewport sensor.

Headless tests for :mod:`engine.data.lod_pyramid` and the pyramid
hook on :class:`gui.chart_tab.items.CandlestickItem`. All tests run
under ``QT_QPA_PLATFORM=offscreen`` so they don't need a display.
"""

from __future__ import annotations

import os
import time
from typing import List

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pandas as pd
import pytest

from engine.data.lod_pyramid import (
    DEFAULT_PYRAMID_LEVELS,
    LodPyramid,
    tf_minutes,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_m1_df(n: int, *, start: str = "2024-01-01") -> pd.DataFrame:
    """Build a deterministic synthetic 1m OHLCV frame.

    Prices form a smooth zig-zag so OHLC max/min are easy to reason
    about (max = open + 1, min = open - 1) regardless of bar count.
    """
    times = pd.date_range(start=start, periods=n, freq="1min")
    op = 100.0 + np.arange(n, dtype=np.float64) * 0.01
    cl = op + np.where(np.arange(n) % 2 == 0, 0.5, -0.5)
    hi = op + 1.0
    lo = op - 1.0
    vol = np.full(n, 10, dtype=np.int64)
    return pd.DataFrame(
        {
            "time": times,
            "open": op,
            "high": hi,
            "low": lo,
            "close": cl,
            "volume": vol,
        }
    )


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


# ===========================================================================
# LodPyramid — construction
# ===========================================================================


def test_pyramid_from_base_emits_requested_levels() -> None:
    """Every level in the requested ladder must be present even when
    the base TF was omitted — the constructor auto-includes it."""
    df = _make_m1_df(1_440)  # one day
    pyramid = LodPyramid.from_base(df, levels=("5m", "15m", "1h", "1d"))
    for tf in ("1m", "5m", "15m", "1h", "1d"):
        assert pyramid.has(tf), f"missing level {tf!r}"


def test_pyramid_m5_has_correct_bar_count() -> None:
    """100 minutes of 1m data → 20 bars at M5 (100 / 5)."""
    df = _make_m1_df(100)
    pyramid = LodPyramid.from_base(df, levels=("5m",))
    m5 = pyramid.get("5m")
    assert len(m5) == 20, f"expected 20 M5 bars from 100 M1, got {len(m5)}"


def test_pyramid_m15_h1_d1_bar_counts() -> None:
    """One day = 1 440 1m bars → 96 M15, 24 H1, 1 D1."""
    df = _make_m1_df(1_440)
    pyramid = LodPyramid.from_base(df, levels=("15m", "1h", "1d"))
    assert len(pyramid.get("15m")) == 96
    assert len(pyramid.get("1h")) == 24
    assert len(pyramid.get("1d")) == 1


def test_pyramid_ohlc_semantics_match_expectations() -> None:
    """Aggregate OHLC must follow first/max/min/last semantics."""
    df = _make_m1_df(15)
    pyramid = LodPyramid.from_base(df, levels=("15m",))
    m15 = pyramid.get("15m")
    assert len(m15) == 1
    row = m15.iloc[0]
    assert row["open"] == pytest.approx(df["open"].iloc[0])
    assert row["close"] == pytest.approx(df["close"].iloc[-1])
    assert row["high"] == pytest.approx(df["high"].max())
    assert row["low"] == pytest.approx(df["low"].min())


def test_pyramid_handles_empty_input() -> None:
    """Empty frame in → empty frames out for every level. Must not raise."""
    empty = pd.DataFrame(columns=["time", "open", "high", "low", "close", "volume"])
    pyramid = LodPyramid.from_base(empty, levels=("5m", "15m"))
    assert pyramid.has("1m") and len(pyramid.get("1m")) == 0
    assert pyramid.has("5m") and len(pyramid.get("5m")) == 0
    assert pyramid.has("15m") and len(pyramid.get("15m")) == 0


def test_pyramid_handles_none_input() -> None:
    """``None`` is normalised to an empty DataFrame — same as empty."""
    pyramid = LodPyramid.from_base(None, levels=("5m",))  # type: ignore[arg-type]
    assert len(pyramid.get("1m")) == 0
    assert len(pyramid.get("5m")) == 0


def test_pyramid_levels_sorted_finest_to_coarsest() -> None:
    df = _make_m1_df(60)
    pyramid = LodPyramid.from_base(df, levels=("1h", "5m", "15m"))
    assert pyramid.list_levels() == ["1m", "5m", "15m", "1h"]


# ===========================================================================
# LodPyramid — viewport sensor
# ===========================================================================


def test_for_visible_bars_picks_base_when_small() -> None:
    """≤ 2 000 visible base bars → render at the base TF."""
    df = _make_m1_df(10_000)
    pyramid = LodPyramid.from_base(df)
    assert pyramid.for_visible_bars(500) == "1m"
    assert pyramid.for_visible_bars(1_999) == "1m"
    assert pyramid.for_visible_bars(2_000) == "1m"


def test_for_visible_bars_climbs_ladder() -> None:
    """Beyond 2 000 base bars the pyramid hops to the next level
    that fits the budget."""
    df = _make_m1_df(100_000)
    pyramid = LodPyramid.from_base(df)
    # 2 001 M1 bars would draw > 2 000, so the sensor must skip M1.
    # M5 fits (2 001 / 5 ≈ 400) — that's the expected pick.
    assert pyramid.for_visible_bars(2_001) == "5m"
    # 30 000 base bars: 6 000 M5 (no), 2 000 M15 (yes).
    assert pyramid.for_visible_bars(30_000) == "15m"
    # 100 000 base bars: 6 666 M15 (no), 1 666 H1 (yes).
    assert pyramid.for_visible_bars(100_000) == "1h"
    # 200 000 base bars: 3 333 H1 (no), 138 D1 (yes).
    assert pyramid.for_visible_bars(200_000) == "1d"
    # 10 years worth — only D1 fits (no W1 in default ladder), so D1.
    assert pyramid.for_visible_bars(5_000_000) == "1d"


def test_for_visible_bars_falls_back_to_coarsest() -> None:
    """When nothing fits the budget, return the coarsest available."""
    df = _make_m1_df(1_440)
    pyramid = LodPyramid.from_base(df, levels=("5m",))
    # 1 000 000 base bars, only M1 + M5 available, max_bars=10 — neither
    # fits the budget, so the coarsest (M5) wins.
    assert pyramid.for_visible_bars(1_000_000, max_bars=10) == "5m"


def test_for_visible_bars_zero_or_negative_returns_base() -> None:
    df = _make_m1_df(60)
    pyramid = LodPyramid.from_base(df)
    assert pyramid.for_visible_bars(0) == "1m"
    assert pyramid.for_visible_bars(-1) == "1m"


def test_for_visible_bars_respects_custom_max_bars() -> None:
    """A tighter ``max_bars`` budget pushes the sensor coarser."""
    df = _make_m1_df(10_000)
    pyramid = LodPyramid.from_base(df)
    # At default 2 000 budget, 1 500 base bars fits M1. At 500 budget
    # it must climb to M5 (1 500 / 5 = 300 ≤ 500).
    assert pyramid.for_visible_bars(1_500, max_bars=2_000) == "1m"
    assert pyramid.for_visible_bars(1_500, max_bars=500) == "5m"


# ===========================================================================
# LodPyramid — chart adapter
# ===========================================================================


def test_to_chart_tuples_base_uses_sequential_indices() -> None:
    df = _make_m1_df(50)
    pyramid = LodPyramid.from_base(df, levels=("5m",))
    tuples = pyramid.to_chart_tuples("1m")
    assert len(tuples) == 50
    # First-column indices are 0..49.
    idxs = [t[0] for t in tuples]
    assert idxs == list(range(50))


def test_to_chart_tuples_m5_indices_are_base_aligned() -> None:
    """M5 candle indices land at 0, 5, 10, … (the first 1m bar of
    each 5-min bucket)."""
    df = _make_m1_df(20)
    pyramid = LodPyramid.from_base(df, levels=("5m",))
    tuples = pyramid.to_chart_tuples("5m")
    assert len(tuples) == 4
    expected_idx = [0.0, 5.0, 10.0, 15.0]
    assert [t[0] for t in tuples] == expected_idx


def test_to_chart_tuples_h1_indices_are_base_aligned() -> None:
    df = _make_m1_df(180)  # 3 hours
    pyramid = LodPyramid.from_base(df, levels=("1h",))
    tuples = pyramid.to_chart_tuples("1h")
    assert len(tuples) == 3
    assert [t[0] for t in tuples] == [0.0, 60.0, 120.0]


def test_to_chart_tuples_layout_matches_candlestick_item() -> None:
    """The returned tuples must be the 5-tuple layout
    ``(idx, open, close, low, high)`` the CandlestickItem accepts."""
    df = _make_m1_df(5)
    pyramid = LodPyramid.from_base(df, levels=())
    tuples = pyramid.to_chart_tuples("1m")
    for t in tuples:
        assert len(t) == 5
        idx, op, cl, lo, hi = t
        assert isinstance(idx, float)
        assert lo <= op <= hi
        assert lo <= cl <= hi


def test_to_chart_tuples_unknown_tf_raises() -> None:
    df = _make_m1_df(5)
    pyramid = LodPyramid.from_base(df, levels=("5m",))
    with pytest.raises(KeyError):
        pyramid.to_chart_tuples("nope")


def test_to_chart_tuples_empty_returns_empty_list() -> None:
    empty = pd.DataFrame(columns=["time", "open", "high", "low", "close"])
    pyramid = LodPyramid.from_base(empty, levels=("5m",))
    assert pyramid.to_chart_tuples("1m") == []
    assert pyramid.to_chart_tuples("5m") == []


# ===========================================================================
# tf_minutes helper
# ===========================================================================


def test_tf_minutes_known_values() -> None:
    assert tf_minutes("1m") == 1
    assert tf_minutes("5m") == 5
    assert tf_minutes("15m") == 15
    assert tf_minutes("1h") == 60
    assert tf_minutes("1d") == 1_440


def test_tf_minutes_unknown_raises() -> None:
    with pytest.raises(KeyError):
        tf_minutes("nonesuch")


# ===========================================================================
# Pyramid build performance
# ===========================================================================


def test_pyramid_build_fast_at_100k_bars() -> None:
    """Phase 2 target: pyramid build for the default ladder must
    finish well under 1 s at 100 k M1 bars. On the Polars fast path
    we'd expect ≪ 100 ms; we set a generous 1 s ceiling so CI on the
    pandas-only fallback also passes."""
    df = _make_m1_df(100_000)
    t0 = time.perf_counter()
    pyramid = LodPyramid.from_base(df)
    elapsed = time.perf_counter() - t0
    assert elapsed < 1.0, f"pyramid build took {elapsed:.3f}s (> 1s ceiling)"
    # Sanity: the default ladder produced something at every level.
    for tf in DEFAULT_PYRAMID_LEVELS:
        assert pyramid.has(tf)


# ===========================================================================
# CandlestickItem.set_pyramid + viewport sensor
# ===========================================================================


class _StubView:
    """Minimal stand-in for a pyqtgraph ViewBox — implements just
    enough surface area for :class:`CandlestickItem` to query the
    visible x-range without needing a real Qt scene graph."""

    def __init__(self, x0: float = 0.0, x1: float = 100.0) -> None:
        self._x = (x0, x1)

    def viewRange(self):
        return [list(self._x), [0.0, 1.0]]

    def set_x(self, x0: float, x1: float) -> None:
        self._x = (x0, x1)

    # ``sigRangeChanged`` is monkey-patched by ``set_view``; we don't
    # need a real signal for the unit tests because they call the
    # private sensor directly.
    class _NoSignal:
        def connect(self, *_args, **_kwargs) -> None:
            pass

    sigRangeChanged = _NoSignal()


def test_candlestick_item_active_tf_defaults_to_none_without_pyramid(
    qapp,
) -> None:
    from gui.chart_tab.items import CandlestickItem

    item = CandlestickItem()
    assert item.active_tf is None


def test_candlestick_item_set_pyramid_seeds_active_tf(qapp) -> None:
    """``set_pyramid`` parks ``active_tf`` on the pyramid's base level
    — the first ``_maybe_swap_pyramid_tf`` call decides whether to
    climb the ladder."""
    from gui.chart_tab.items import CandlestickItem

    df = _make_m1_df(1_000)
    pyramid = LodPyramid.from_base(df, levels=("5m", "15m"))

    item = CandlestickItem()
    item.set_pyramid(pyramid)
    assert item.active_tf == "1m"


def test_candlestick_item_swaps_to_coarser_tf_on_zoom_out(qapp) -> None:
    """When the visible x-range exceeds the budget, the sensor must
    swap to the smallest coarser TF that fits."""
    from gui.chart_tab.items import CandlestickItem

    df = _make_m1_df(50_000)
    pyramid = LodPyramid.from_base(df)

    item = CandlestickItem(max_bars=2_000)
    item.set_data(pyramid.to_chart_tuples("1m"))
    view = _StubView(0, 100)
    item._view = view
    item.set_pyramid(pyramid, max_bars=2_000)

    # Zoom out: 30 000 visible base bars → M15 fits (30 000 / 15 = 2 000).
    view.set_x(0, 30_000)
    swapped = item._maybe_swap_pyramid_tf()
    assert swapped is True
    assert item.active_tf == "15m"
    assert item._active_tf_ratio == pytest.approx(15.0)


def test_candlestick_item_swap_idempotent_at_same_zoom(qapp) -> None:
    """Calling the sensor twice at the same zoom must swap once."""
    from gui.chart_tab.items import CandlestickItem

    df = _make_m1_df(50_000)
    pyramid = LodPyramid.from_base(df)

    item = CandlestickItem(max_bars=2_000)
    item.set_data(pyramid.to_chart_tuples("1m"))
    view = _StubView(0, 30_000)
    item._view = view
    item.set_pyramid(pyramid, max_bars=2_000)

    assert item._maybe_swap_pyramid_tf() is True
    assert item._maybe_swap_pyramid_tf() is False  # idempotent
    assert item.active_tf == "15m"


def test_candlestick_item_swaps_back_to_finer_on_zoom_in(qapp) -> None:
    """Zooming back in must drop the chart back to the finest level
    whose count fits the budget."""
    from gui.chart_tab.items import CandlestickItem

    df = _make_m1_df(50_000)
    pyramid = LodPyramid.from_base(df)

    item = CandlestickItem(max_bars=2_000)
    item.set_data(pyramid.to_chart_tuples("1m"))
    view = _StubView(0, 30_000)
    item._view = view
    item.set_pyramid(pyramid, max_bars=2_000)

    item._maybe_swap_pyramid_tf()  # → 15m
    assert item.active_tf == "15m"

    view.set_x(0, 500)
    item._maybe_swap_pyramid_tf()
    assert item.active_tf == "1m"
    assert item._active_tf_ratio == pytest.approx(1.0)


def test_candlestick_item_no_swap_without_view(qapp) -> None:
    """Sensor is a no-op when no ViewBox is linked."""
    from gui.chart_tab.items import CandlestickItem

    df = _make_m1_df(10_000)
    pyramid = LodPyramid.from_base(df)
    item = CandlestickItem(max_bars=2_000)
    item.set_data(pyramid.to_chart_tuples("1m"))
    item.set_pyramid(pyramid, max_bars=2_000)
    assert item._view is None
    assert item._maybe_swap_pyramid_tf() is False
    assert item.active_tf == "1m"


def test_candlestick_item_no_swap_when_pyramid_unattached(qapp) -> None:
    """Without ``set_pyramid``, the sensor never fires regardless of
    view-range churn."""
    from gui.chart_tab.items import CandlestickItem

    item = CandlestickItem(max_bars=2_000)
    item.set_data(list(zip(range(50), [100.0] * 50, [100.0] * 50, [99.0] * 50, [101.0] * 50)))
    view = _StubView(0, 50_000)
    item._view = view
    assert item._maybe_swap_pyramid_tf() is False
    assert item.active_tf is None


def test_candlestick_item_swap_invalidates_geom_cache(qapp) -> None:
    """After a swap the geometry cache must be rebuilt for the new
    level's bar set so subsequent paints don't draw stale rects."""
    from gui.chart_tab.items import CandlestickItem

    df = _make_m1_df(50_000)
    pyramid = LodPyramid.from_base(df)

    item = CandlestickItem(max_bars=2_000)
    item.set_data(pyramid.to_chart_tuples("1m"))
    view = _StubView(0, 30_000)
    item._view = view
    item.set_pyramid(pyramid, max_bars=2_000)

    rects_before = item._geom_rects
    item._maybe_swap_pyramid_tf()
    rects_after = item._geom_rects
    assert rects_after is not rects_before
    # New rect count matches the new level's bar count.
    new_data: List = item._arr.tolist()
    assert len(rects_after) == len(new_data)


def test_candlestick_item_pyramid_clears_on_detach(qapp) -> None:
    """``set_pyramid(None)`` must drop the active level so the next
    ``_maybe_swap_pyramid_tf`` no-ops."""
    from gui.chart_tab.items import CandlestickItem

    df = _make_m1_df(1_000)
    pyramid = LodPyramid.from_base(df)
    item = CandlestickItem(max_bars=2_000)
    item.set_data(pyramid.to_chart_tuples("1m"))
    view = _StubView(0, 30_000)
    item._view = view
    item.set_pyramid(pyramid, max_bars=2_000)
    assert item.active_tf == "1m"

    item.set_pyramid(None)
    assert item.active_tf is None
    assert item._maybe_swap_pyramid_tf() is False


# ===========================================================================
# DataLoader.load_with_pyramid integration
# ===========================================================================


def test_data_loader_load_with_pyramid_returns_pair(tmp_path) -> None:
    """End-to-end: write a CSV, load it via the loader's pyramid
    wrapper, and confirm we get back both the base frame and a
    pyramid with at least M1/M5/M15/H1/D1."""
    from engine.data.data_loader import DataLoader

    df = _make_m1_df(2_880)  # two days
    csv = tmp_path / "synthetic.csv"
    df.to_csv(csv, index=False)

    base_df, pyramid = DataLoader.load_with_pyramid(str(csv))

    assert len(base_df) == 2_880
    assert isinstance(pyramid, LodPyramid)
    for tf in ("1m", "5m", "15m", "1h", "1d"):
        assert pyramid.has(tf), f"missing level {tf!r}"
    # M15 over two days = 192 bars; H1 = 48; D1 = 2.
    assert len(pyramid.get("15m")) == 192
    assert len(pyramid.get("1h")) == 48
    assert len(pyramid.get("1d")) == 2


def test_data_loader_load_with_pyramid_custom_levels(tmp_path) -> None:
    """A custom ``pyramid_levels`` override must be honoured —
    levels outside the override are absent."""
    from engine.data.data_loader import DataLoader

    df = _make_m1_df(120)
    csv = tmp_path / "synthetic.csv"
    df.to_csv(csv, index=False)

    _, pyramid = DataLoader.load_with_pyramid(
        str(csv), pyramid_levels=("5m",)
    )
    assert pyramid.has("1m")
    assert pyramid.has("5m")
    # D1 wasn't requested → not present.
    assert not pyramid.has("1d")
