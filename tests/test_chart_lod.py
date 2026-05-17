"""Phase 2.3 — Chart Level-of-Detail (LOD) downsampling.

Pure-Python unit tests for :mod:`gui.chart_tab.lod`. No Qt event loop
is required because the LOD module deliberately does not depend on
PySide6 — the smoke test for :class:`CandlestickItem`'s integration
with LOD lives further down and uses the offscreen Qt platform.
"""

from __future__ import annotations

import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest

from gui.chart_tab.lod import (
    DEFAULT_MAX_BARS,
    downsample_ohlc,
    downsample_xy,
)


def _make_ohlc(n: int) -> list[tuple[float, float, float, float, float]]:
    """``(idx, open, close, low, high)`` synthetic series. Highs and
    lows are deterministic functions of ``idx`` so tests can verify
    peak preservation analytically."""
    out = []
    for i in range(n):
        op = 100.0 + (i % 7)
        cl = 100.0 + ((i + 1) % 7)
        lo = 100.0 + (i % 11) - 5
        hi = 100.0 + (i % 13) + 5
        out.append((float(i), op, cl, lo, hi))
    return out


# ---------------------------------------------------------------------------
# downsample_ohlc
# ---------------------------------------------------------------------------


def test_ohlc_below_threshold_returns_input_untouched() -> None:
    data = _make_ohlc(10)
    out = downsample_ohlc(data, max_bars=100)
    assert out == data


def test_ohlc_above_threshold_caps_length() -> None:
    data = _make_ohlc(10_000)
    out = downsample_ohlc(data, max_bars=500)
    assert len(out) <= 500


def test_ohlc_preserves_global_high_and_low() -> None:
    """The downsampled bars must contain the source's global high
    and low — that's the point of peak-preserving LOD."""
    data = _make_ohlc(5_000)
    src_high = max(t[4] for t in data)
    src_low = min(t[3] for t in data)
    out = downsample_ohlc(data, max_bars=200)
    out_high = max(t[4] for t in out)
    out_low = min(t[3] for t in out)
    assert out_high == src_high
    assert out_low == src_low


def test_ohlc_preserves_first_open_and_last_close() -> None:
    data = _make_ohlc(5_000)
    out = downsample_ohlc(data, max_bars=200)
    # First aggregate bar's open == first source bar's open.
    assert out[0][1] == data[0][1]
    # Last aggregate bar's close == last source bar's close.
    assert out[-1][2] == data[-1][2]


def test_ohlc_idx_is_monotonic() -> None:
    data = _make_ohlc(2_500)
    out = downsample_ohlc(data, max_bars=100)
    idxs = [t[0] for t in out]
    assert idxs == sorted(idxs)
    assert len(set(idxs)) == len(idxs), "indices must be unique"


def test_ohlc_max_bars_lower_bound() -> None:
    """``max_bars < 1`` is silently clamped to 1."""
    data = _make_ohlc(50)
    out = downsample_ohlc(data, max_bars=0)
    assert len(out) == 1
    # That single bar must still envelope the entire source.
    assert out[0][3] == min(t[3] for t in data)
    assert out[0][4] == max(t[4] for t in data)


def test_ohlc_default_max_bars_is_2000() -> None:
    assert DEFAULT_MAX_BARS == 2_000


# ---------------------------------------------------------------------------
# downsample_xy
# ---------------------------------------------------------------------------


def test_xy_below_threshold_returns_input_untouched() -> None:
    x = np.arange(50, dtype=np.float64)
    y = np.sin(x / 5.0)
    out_x, out_y = downsample_xy(x, y, max_points=200)
    assert out_x is x and out_y is y  # zero-cost early-out


def test_xy_above_threshold_caps_length() -> None:
    x = np.arange(50_000, dtype=np.float64)
    y = np.sin(x / 50.0).astype(np.float64)
    out_x, out_y = downsample_xy(x, y, max_points=2_000)
    # The trailing-bin tail can push slightly above the cap, but
    # never by more than one bin worth of samples.
    assert out_x.size <= 2_100
    assert out_y.size == out_x.size


def test_xy_preserves_global_extrema() -> None:
    n = 30_000
    x = np.arange(n, dtype=np.float64)
    rng = np.random.default_rng(123)
    y = rng.standard_normal(n)
    src_max = float(y.max())
    src_min = float(y.min())
    _, out_y = downsample_xy(x, y, max_points=1_000)
    assert float(out_y.max()) == src_max
    assert float(out_y.min()) == src_min


def test_xy_preserves_x_monotonic() -> None:
    n = 8_000
    x = np.arange(n, dtype=np.float64)
    y = np.linspace(-1.0, 1.0, n)
    out_x, _ = downsample_xy(x, y, max_points=400)
    diffs = np.diff(out_x)
    assert np.all(diffs >= 0), "downsampled x must be non-decreasing"


# ---------------------------------------------------------------------------
# CandlestickItem integration (offscreen Qt)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def test_candlestick_item_applies_lod_internally(qapp) -> None:
    from gui.chart_tab.items import CandlestickItem

    item = CandlestickItem(max_bars=100)
    raw = _make_ohlc(5_000)
    item.set_data(raw)
    # The internal painter loop must work over the downsampled set,
    # but tooltips still resolve to the full-resolution OHLC.
    assert len(item.data) <= 100
    # Pick an arbitrary index that exists in raw data.
    idx_to_check = 1234
    assert item.get_ohlc_at(idx_to_check) is not None


def test_candlestick_item_no_lod_below_threshold(qapp) -> None:
    from gui.chart_tab.items import CandlestickItem

    item = CandlestickItem(max_bars=2_000)
    raw = _make_ohlc(300)
    item.set_data(raw)
    # Below the threshold the item must not allocate a downsampled
    # copy — ``self.data is self._raw_data`` guarantees zero overhead.
    assert item.data is item._raw_data


def test_candlestick_item_set_data_under_500ms_at_50k(qapp) -> None:
    """A 50 000-bar set_data call (the worst case the live chart will
    ever see — even backtests beyond that route through the engine's
    HTF aggregator) must finish in well under 0.5 s with LOD enabled.

    Without LOD the QPainter loop walks the full 50K bars on every
    range-change, which on a typical workstation is 1-2 s — visible
    pan/zoom lag. LOD reduces the painter loop to ``DEFAULT_MAX_BARS``
    bars regardless of source size.
    """
    from gui.chart_tab.items import CandlestickItem

    item = CandlestickItem()  # default max_bars
    raw = _make_ohlc(50_000)
    t0 = time.perf_counter()
    item.set_data(raw)
    elapsed = time.perf_counter() - t0
    assert elapsed < 0.5, (
        f"set_data(50k) took {elapsed * 1000:.1f} ms — Phase 2.3 LOD "
        "regression: must stay under 500 ms"
    )
    assert len(item.data) <= DEFAULT_MAX_BARS


def test_candlestick_item_set_max_bars_reapplies_lod(qapp) -> None:
    from gui.chart_tab.items import CandlestickItem

    item = CandlestickItem(max_bars=2_000)
    raw = _make_ohlc(8_000)
    item.set_data(raw)
    assert len(item.data) <= 2_000
    item.set_max_bars(200)
    assert len(item.data) <= 200
    item.set_max_bars(20_000)
    # Raising the cap above source-length should turn off LOD.
    assert item.data is item._raw_data


# ---------------------------------------------------------------------------
# Phase 2.3 — batched QPainter (drawLines / drawRects)
# ---------------------------------------------------------------------------

# Minimal stand-in for a pyqtgraph ViewBox. ``CandlestickItem`` only
# reads ``viewRange()[0]`` in the culling path; tests bypass the
# ``set_view`` wiring (which would also connect a QTimer) and assign
# ``_view`` directly.
class _StubView:
    def __init__(self, xmin: float, xmax: float) -> None:
        self._xr = [float(xmin), float(xmax)]

    def viewRange(self):  # noqa: N802 — pyqtgraph API mirror
        return [self._xr, [0.0, 1.0]]


def _make_alternating_ohlc(n: int) -> list[tuple[float, float, float, float, float]]:
    """``(idx, open, close, low, high)`` with strictly alternating
    bull / bear bodies so the painter exercises *both* batch branches.
    Doji-free — body height is always ≥ 0.5.
    """
    out = []
    for i in range(n):
        op = 100.0
        cl = 101.0 if i % 2 == 0 else 99.0
        lo = 98.0
        hi = 102.0
        out.append((float(i), op, cl, lo, hi))
    return out


def test_candlestick_paint_is_batched_at_2000_bars(qapp) -> None:
    """Phase 2.3 — painter must emit O(1) batch calls regardless of
    bar count. With 2 000 mixed bull/bear bars we expect at most 4
    drawLines calls (bull wicks + bear wicks + doji wicks + doji flats)
    and at most 2 drawRects calls (bull bodies + bear bodies).
    """
    from gui.chart_tab.items import CandlestickItem

    item = CandlestickItem(max_bars=2_000)
    item.set_data(_make_alternating_ohlc(2_000))

    stats = item._paint_stats
    assert stats["bars_drawn"] == 2_000, (
        f"All 2k bars must paint when no view is linked (drew {stats['bars_drawn']})"
    )
    assert stats["draw_lines_calls"] <= 4, (
        f"Too many drawLines calls: {stats['draw_lines_calls']} — "
        "Phase 2.3 batching regression"
    )
    assert stats["draw_rects_calls"] <= 2, (
        f"Too many drawRects calls: {stats['draw_rects_calls']} — "
        "Phase 2.3 batching regression"
    )


def test_candlestick_paint_batch_count_independent_of_bars(qapp) -> None:
    """The number of batch draws must be invariant across set sizes —
    that's what makes the painter "TradingView-level"."""
    from gui.chart_tab.items import CandlestickItem

    item_small = CandlestickItem(max_bars=10_000)
    item_small.set_data(_make_alternating_ohlc(100))

    item_big = CandlestickItem(max_bars=10_000)
    item_big.set_data(_make_alternating_ohlc(5_000))

    assert (
        item_small._paint_stats["draw_lines_calls"]
        == item_big._paint_stats["draw_lines_calls"]
    )
    assert (
        item_small._paint_stats["draw_rects_calls"]
        == item_big._paint_stats["draw_rects_calls"]
    )


# ---------------------------------------------------------------------------
# Phase 2.1 — viewport culling (np.searchsorted)
# ---------------------------------------------------------------------------


def test_viewport_culling_no_view_draws_all_bars(qapp) -> None:
    """Without a linked ViewBox the painter must fall back to drawing
    everything (the chart side decides whether to cull)."""
    from gui.chart_tab.items import CandlestickItem

    item = CandlestickItem(max_bars=10_000)
    item.set_data(_make_alternating_ohlc(500))
    assert item._paint_stats["bars_drawn"] == 500
    assert item._paint_stats["bars_culled"] == 0


def test_viewport_culling_clips_to_visible_range(qapp) -> None:
    """A narrow view must trigger ``np.searchsorted`` culling. With
    500 bars at idx 0..499 and a view of [100.0, 200.0] we expect
    ~102 bars drawn (100..200 inclusive + 1-bar margin on each
    side), and ~398 bars culled.
    """
    from gui.chart_tab.items import CandlestickItem

    item = CandlestickItem(max_bars=10_000)
    item._view = _StubView(100.0, 200.0)
    item.set_data(_make_alternating_ohlc(500))

    drawn = item._paint_stats["bars_drawn"]
    culled = item._paint_stats["bars_culled"]
    assert drawn + culled == 500
    # Visible window is 100 bars wide + 1 bar margin on each side =
    # at most 103 bars (101 inside + 2 margins). Lower bound: 100.
    assert 100 <= drawn <= 103, (
        f"Culling slice off — drew {drawn} bars, expected ~101-103"
    )
    assert culled >= 395


def test_viewport_culling_view_wider_than_data_draws_all(qapp) -> None:
    """A view that exceeds the data range on both sides must not
    accidentally drop bars — ``searchsorted`` saturates at the array
    bounds."""
    from gui.chart_tab.items import CandlestickItem

    item = CandlestickItem(max_bars=10_000)
    item._view = _StubView(-1000.0, 1_000_000.0)
    item.set_data(_make_alternating_ohlc(300))
    assert item._paint_stats["bars_drawn"] == 300
    assert item._paint_stats["bars_culled"] == 0


def test_viewport_culling_view_outside_data_draws_nothing(qapp) -> None:
    """A view fully outside the data range must produce a no-op
    painter — important for the case where the user scrolls past
    the data tail."""
    from gui.chart_tab.items import CandlestickItem

    item = CandlestickItem(max_bars=10_000)
    item._view = _StubView(5_000.0, 6_000.0)
    item.set_data(_make_alternating_ohlc(300))
    assert item._paint_stats["bars_drawn"] == 0
    assert item._paint_stats["bars_culled"] == 300
    # No batch calls when nothing's visible.
    assert item._paint_stats["draw_lines_calls"] == 0
    assert item._paint_stats["draw_rects_calls"] == 0


def test_viewport_culling_after_view_change_reflects_new_range(qapp) -> None:
    """Changing the linked view's range and re-running ``_gen`` must
    re-apply culling against the new range — i.e. the cull is keyed
    to the *current* viewRange, not snapshotted at set_data time."""
    from gui.chart_tab.items import CandlestickItem

    item = CandlestickItem(max_bars=10_000)
    view = _StubView(0.0, 500.0)
    item._view = view
    item.set_data(_make_alternating_ohlc(500))
    assert item._paint_stats["bars_drawn"] == 500

    # User zooms in: tighten the range and re-run paint generation.
    view._xr = [200.0, 250.0]
    item._gen()
    drawn = item._paint_stats["bars_drawn"]
    assert 50 <= drawn <= 53, (
        f"After zoom-in cull window mis-sized — drew {drawn}, expected ~51-53"
    )


# ---------------------------------------------------------------------------
# Phase 2.2 — polars time-aware LOD pyramid
# ---------------------------------------------------------------------------


def test_choose_target_tf_climbs_ladder_as_span_grows() -> None:
    from gui.chart_tab.lod import choose_target_tf

    # 5 minutes of 1m bars fit at 1m (5 bars ≤ 2000).
    assert choose_target_tf(5 * 60) == "1m"
    # 10 hours: 600 1m bars still under 2000 → 1m.
    assert choose_target_tf(10 * 3600) == "1m"
    # 10 days: 14 400 1m bars > 2000 → climbs.
    assert choose_target_tf(10 * 86400) in {"5m", "15m"}
    # 10 years: must reach the top of the ladder.
    assert choose_target_tf(10 * 365 * 86400) == "1w"


def test_choose_target_tf_handles_zero_and_negative_span() -> None:
    from gui.chart_tab.lod import choose_target_tf

    assert choose_target_tf(0) == "1m"
    assert choose_target_tf(-5) == "1m"


def test_downsample_ohlc_polars_rejects_unknown_tf() -> None:
    pl = pytest.importorskip("polars")
    from gui.chart_tab.lod import downsample_ohlc_polars

    df = pl.DataFrame(
        {
            "time": pl.datetime_range(
                start=pl.datetime(2024, 1, 1),
                end=pl.datetime(2024, 1, 1, 0, 5),
                interval="1m",
                eager=True,
                closed="left",
            ),
            "open": [1.0, 2.0, 3.0, 4.0, 5.0],
            "high": [1.5, 2.5, 3.5, 4.5, 5.5],
            "low": [0.5, 1.5, 2.5, 3.5, 4.5],
            "close": [1.2, 2.2, 3.2, 4.2, 5.2],
        }
    )
    with pytest.raises(ValueError):
        downsample_ohlc_polars(df, "42d")


def test_downsample_ohlc_polars_empty_input_returns_empty() -> None:
    pl = pytest.importorskip("polars")
    from gui.chart_tab.lod import downsample_ohlc_polars

    empty = pl.DataFrame(
        schema={
            "time": pl.Datetime,
            "open": pl.Float64,
            "high": pl.Float64,
            "low": pl.Float64,
            "close": pl.Float64,
        }
    )
    out = downsample_ohlc_polars(empty, "1h")
    assert out.height == 0


def test_downsample_ohlc_polars_m1_to_h1_ohlc_semantics() -> None:
    """Aggregate 60 1m bars into one 1h bar. Verifies the four OHLC
    invariants: first-open, max-high, min-low, last-close."""
    pl = pytest.importorskip("polars")
    from gui.chart_tab.lod import downsample_ohlc_polars

    times = pl.datetime_range(
        start=pl.datetime(2024, 1, 1),
        end=pl.datetime(2024, 1, 1, 1),
        interval="1m",
        eager=True,
        closed="left",
    )
    n = times.len()
    # Synthetic OHLC where extrema land at known indices.
    open_arr = [100.0 + i * 0.1 for i in range(n)]
    high_arr = [open_arr[i] + (5.0 if i == 17 else 0.5) for i in range(n)]
    low_arr = [open_arr[i] - (5.0 if i == 41 else 0.5) for i in range(n)]
    close_arr = [open_arr[i] + 0.05 for i in range(n)]
    df = pl.DataFrame(
        {
            "time": times,
            "open": open_arr,
            "high": high_arr,
            "low": low_arr,
            "close": close_arr,
        }
    )
    out = downsample_ohlc_polars(df, "1h")
    assert out.height == 1
    row = out.row(0, named=True)
    assert row["open"] == open_arr[0]
    assert row["close"] == close_arr[-1]
    assert row["high"] == max(high_arr)
    assert row["low"] == min(low_arr)


def test_downsample_ohlc_polars_d1_volume_sum_spread_mean() -> None:
    """Aggregate to 1d and assert volume = sum, spread = mean — same
    semantics as ``engine/data/tf_aggregator.py``."""
    pl = pytest.importorskip("polars")
    from gui.chart_tab.lod import downsample_ohlc_polars

    times = pl.datetime_range(
        start=pl.datetime(2024, 1, 1),
        end=pl.datetime(2024, 1, 2),
        interval="1m",
        eager=True,
        closed="left",
    )
    n = times.len()
    df = pl.DataFrame(
        {
            "time": times,
            "open": [100.0] * n,
            "high": [101.0] * n,
            "low": [99.0] * n,
            "close": [100.5] * n,
            "volume": [2.0] * n,
            "spread": [0.5] * n,
        }
    )
    out = downsample_ohlc_polars(df, "1d")
    assert out.height == 1
    row = out.row(0, named=True)
    assert row["volume"] == 2.0 * n
    assert abs(row["spread"] - 0.5) < 1e-9


def test_downsample_ohlc_polars_h1_buckets_3h_input() -> None:
    """3 hours of 1m bars → exactly 3 1h buckets, each summarising
    the right slice. Catches bucket-boundary off-by-one errors."""
    pl = pytest.importorskip("polars")
    from gui.chart_tab.lod import downsample_ohlc_polars

    times = pl.datetime_range(
        start=pl.datetime(2024, 1, 1),
        end=pl.datetime(2024, 1, 1, 3),
        interval="1m",
        eager=True,
        closed="left",
    )
    n = times.len()
    assert n == 180
    open_arr = [float(i) for i in range(n)]
    df = pl.DataFrame(
        {
            "time": times,
            "open": open_arr,
            "high": open_arr,
            "low": open_arr,
            "close": open_arr,
        }
    )
    out = downsample_ohlc_polars(df, "1h").sort("time")
    assert out.height == 3
    # Each bucket's open = first index of that hour.
    opens = out["open"].to_list()
    assert opens == [0.0, 60.0, 120.0]
    # Each bucket's close = last index of that hour.
    closes = out["close"].to_list()
    assert closes == [59.0, 119.0, 179.0]
