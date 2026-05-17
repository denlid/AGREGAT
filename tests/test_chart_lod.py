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
