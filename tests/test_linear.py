"""Unit tests for engine.core.linear — close-only structural primitives."""

from __future__ import annotations

import numpy as np
import pytest

from engine.core.linear import (
    FVG,
    Pivot,
    body_extremes,
    fvg_close_only,
    last_active_fvg,
    last_pivots,
    linear_cisd_last,
    linear_pivots,
)


def _arr(seq):
    return np.asarray(seq, dtype=np.float64)


# ---------------------------------------------------------------------------
# body_extremes
# ---------------------------------------------------------------------------


def test_body_extremes_basic():
    o = _arr([10, 12, 11, 13])
    c = _arr([11, 11, 13, 12])
    lo, hi = body_extremes(o, c)
    np.testing.assert_array_equal(lo, [10, 11, 11, 12])
    np.testing.assert_array_equal(hi, [11, 12, 13, 13])


def test_body_extremes_shape_mismatch():
    with pytest.raises(ValueError):
        body_extremes(_arr([1, 2]), _arr([1]))


# ---------------------------------------------------------------------------
# linear_pivots
# ---------------------------------------------------------------------------


def test_linear_pivots_clear_high():
    # Centre bar 6 is the obvious pivot high.
    o = _arr([10, 11, 12, 13, 14, 15, 16, 15, 14, 13, 12, 11, 10])
    c = _arr([11, 12, 13, 14, 15, 16, 17, 14, 13, 12, 11, 10, 9])
    is_hi, is_lo = linear_pivots(o, c, lookback=3)
    assert is_hi[6] is np.True_ or bool(is_hi[6]) is True
    # No pivot lows in this monotone-up-then-down sequence.
    assert not is_lo.any()


def test_linear_pivots_clear_low():
    # V-shape; centre bar 6 is the pivot low.
    o = _arr([16, 15, 14, 13, 12, 11, 10, 11, 12, 13, 14, 15, 16])
    c = _arr([15, 14, 13, 12, 11, 10, 9, 12, 13, 14, 15, 16, 17])
    is_hi, is_lo = linear_pivots(o, c, lookback=3)
    assert bool(is_lo[6]) is True
    assert not is_hi.any()


def test_linear_pivots_lookback_too_short():
    o = _arr([1, 2, 3])
    c = _arr([2, 3, 4])
    is_hi, is_lo = linear_pivots(o, c, lookback=2)
    assert not is_hi.any() and not is_lo.any()


def test_linear_pivots_strict_inequality():
    """Plateau (equal body extremes) must not count as a pivot."""
    o = _arr([10, 11, 12, 13, 13, 13, 12, 11, 10])
    c = _arr([11, 12, 13, 14, 14, 14, 11, 10, 9])
    is_hi, _ = linear_pivots(o, c, lookback=2)
    # body_hi sequence has 14 at indices 3, 4, 5 — equal — so none is a pivot.
    assert not is_hi.any()


# ---------------------------------------------------------------------------
# last_pivots
# ---------------------------------------------------------------------------


def test_last_pivots_returns_most_recent():
    o = _arr([10, 11, 12, 13, 18, 13, 12, 11, 10, 11, 12, 13, 19, 18, 17, 16])
    c = _arr([11, 12, 13, 14, 19, 12, 11, 10, 9, 10, 11, 12, 20, 17, 16, 15])
    ph, pl = last_pivots(o, c, lookback=3)
    assert isinstance(ph, Pivot)
    assert isinstance(pl, Pivot)
    # Two confirmed pivot highs are present (idx 4: body_high 19, idx 12:
    # body_high 20). last_pivots returns the most recent.
    assert ph.price == 20.0
    assert pl.price == 9.0


def test_last_pivots_empty():
    o = _arr([1.0, 2.0])
    c = _arr([2.0, 3.0])
    ph, pl = last_pivots(o, c, lookback=3)
    assert ph is None and pl is None


# ---------------------------------------------------------------------------
# linear_cisd_last
# ---------------------------------------------------------------------------


def test_linear_cisd_last_bullish_break():
    # Pivot high body=17 at index 4, then later close 18 above pivot.
    o = _arr([10, 11, 12, 13, 16, 13, 12, 11, 10, 11, 12, 13, 14])
    c = _arr([11, 12, 13, 14, 17, 12, 11, 10, 9, 10, 11, 12, 18])
    assert linear_cisd_last(o, c, lookback=3) == +1


def test_linear_cisd_last_bearish_break():
    o = _arr([16, 15, 14, 13, 10, 13, 14, 15, 16, 15, 14, 13, 12])
    c = _arr([15, 14, 13, 12, 9, 14, 15, 16, 17, 14, 13, 12, 8])
    assert linear_cisd_last(o, c, lookback=3) == -1


def test_linear_cisd_last_no_break():
    o = _arr([10, 11, 12, 13, 18, 13, 12, 11, 10, 11, 12, 13, 14])
    c = _arr([11, 12, 13, 14, 19, 12, 11, 10, 9, 10, 11, 12, 13])
    # last close 13 is between pivot_low=9 and pivot_high=19.
    assert linear_cisd_last(o, c, lookback=3) == 0


# ---------------------------------------------------------------------------
# fvg_close_only
# ---------------------------------------------------------------------------


def test_fvg_close_only_bull():
    # Body of bar 0: [10,10]; bar 2: [14,14] → bull gap [10..14] at index 2.
    o = _arr([10, 10, 14])
    c = _arr([10, 10, 14])
    out = fvg_close_only(o, c)
    assert out.tolist() == [0, 0, 1]


def test_fvg_close_only_bear():
    o = _arr([14, 10, 10])
    c = _arr([14, 10, 10])
    out = fvg_close_only(o, c)
    assert out.tolist() == [0, 0, -1]


def test_fvg_close_only_short_input():
    out = fvg_close_only(_arr([1.0, 2.0]), _arr([1.0, 2.0]))
    assert out.tolist() == [0, 0]


# ---------------------------------------------------------------------------
# last_active_fvg
# ---------------------------------------------------------------------------


def test_last_active_fvg_alive():
    # Single bull FVG between bars 0 and 2 (close 14 > body_high[0]=10).
    o = _arr([10, 10, 14, 14, 14])
    c = _arr([10, 10, 14, 14, 14])
    fvg = last_active_fvg(o, c, direction=+1)
    assert isinstance(fvg, FVG)
    assert fvg.direction == +1
    assert fvg.low == 10.0 and fvg.high == 14.0


def test_last_active_fvg_invalidated():
    # Bull gap at index 2, then close 8 (below gap_low=10) → invalidated.
    o = _arr([10, 10, 14, 8])
    c = _arr([10, 10, 14, 8])
    assert last_active_fvg(o, c, direction=+1) is None


def test_last_active_fvg_wrong_direction():
    o = _arr([10, 10, 14, 14, 14])
    c = _arr([10, 10, 14, 14, 14])
    assert last_active_fvg(o, c, direction=-1) is None


# ---------------------------------------------------------------------------
# linear_cisd_series — vectorised, per-bar equivalent of linear_cisd_last
# ---------------------------------------------------------------------------


def test_linear_cisd_series_matches_linear_cisd_last_per_bar():
    """The series at index ``i`` must equal ``linear_cisd_last(arr[: i+1])``
    for every ``i``. This is the contract the strategy refactor relies
    on; if it ever drifts, vectorised signals diverge from visual ones.
    """
    from engine.core.linear import linear_cisd_series

    rng = np.random.default_rng(123)
    for n in (50, 200, 1_000):
        for lookback in (1, 2, 3, 5):
            closes = 100.0 + np.cumsum(rng.normal(0, 0.3, size=n))
            opens = np.concatenate([[closes[0]], closes[:-1]]) + rng.normal(0, 0.05, size=n)
            series = linear_cisd_series(opens, closes, lookback=lookback)
            for i in range(n):
                expected = linear_cisd_last(opens[: i + 1], closes[: i + 1], lookback=lookback)
                assert int(series[i]) == expected, (
                    f"n={n} lookback={lookback} i={i}: got {int(series[i])} vs {expected}"
                )


def test_linear_cisd_series_short_input():
    from engine.core.linear import linear_cisd_series

    out = linear_cisd_series(_arr([10, 11]), _arr([11, 12]), lookback=3)
    assert out.dtype == np.int8
    assert (out == 0).all()
    assert out.shape == (2,)


def test_linear_cisd_series_complexity_is_linear():
    """Sanity check: the series helper must scale linearly. We don't
    pin a wall-clock number (CI noise), only that 10x size doesn't
    take materially more than 12x time — a quadratic implementation
    would be ~100x.
    """
    import time

    from engine.core.linear import linear_cisd_series

    rng = np.random.default_rng(0)

    def _bench(n: int) -> float:
        closes = 100.0 + np.cumsum(rng.normal(0, 0.1, size=n))
        opens = np.concatenate([[closes[0]], closes[:-1]])
        # Warm-up to avoid first-call jitter dominating tiny n.
        linear_cisd_series(opens, closes, lookback=2)
        t0 = time.perf_counter()
        linear_cisd_series(opens, closes, lookback=2)
        return time.perf_counter() - t0

    t_small = _bench(2_000)
    t_big = _bench(20_000)
    # Allow generous slack: 12x ratio still passes (truly O(n) is 10x).
    # A quadratic implementation would be ~100x and fail loudly.
    assert t_big <= max(0.05, 12 * t_small + 0.05), (
        f"linear_cisd_series scaled super-linearly: "
        f"n=2k {t_small * 1e3:.2f}ms vs n=20k {t_big * 1e3:.2f}ms"
    )
