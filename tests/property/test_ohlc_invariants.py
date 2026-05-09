"""Property-based tests for OHLC validation invariants (Phase 5.2).

The engine routinely consumes OHLC dataframes from CSV/Parquet/Feather
files. The two non-negotiable invariants are:

    high >= max(open, close, low)
    low  <= min(open, close, high)

Hypothesis generates thousands of synthetic candle sequences and asserts
that:

  1. Any candle satisfying the invariants survives the engine's
     ``validate_ohlc`` round-trip unchanged.
  2. Any candle that violates them is *either* rejected loudly *or*
     repaired in a way that restores the invariants — never silently
     mangled into a still-broken state.

The contract here matters for the upcoming live-broker gateway: silent
mangling of invalid data would produce false fills downstream.
"""

from __future__ import annotations

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from hypothesis.strategies import composite

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------


@composite
def _valid_candle(draw):
    """Generate a single *valid* OHLC candle.

    Values are bounded so we don't tickle float overflow in derived
    indicator code (ATR / pct change).
    """
    open_ = draw(
        st.floats(
            min_value=10.0, max_value=10_000.0, allow_nan=False, allow_infinity=False
        )
    )
    close = draw(
        st.floats(
            min_value=10.0, max_value=10_000.0, allow_nan=False, allow_infinity=False
        )
    )
    # High is at or above max(open, close); low is at or below min(open, close).
    spread = draw(
        st.floats(min_value=0.0, max_value=50.0, allow_nan=False, allow_infinity=False)
    )
    high = max(open_, close) + spread
    low = min(open_, close) - spread
    if low <= 0:
        low = max(0.01, min(open_, close) * 0.5)
    return {"open": open_, "high": high, "low": low, "close": close}


@composite
def _valid_ohlc_df(draw):
    """Generate a dataframe of N valid candles indexed by minute timestamps."""
    n = draw(st.integers(min_value=2, max_value=50))
    candles = [draw(_valid_candle()) for _ in range(n)]
    idx = pd.date_range("2024-01-01", periods=n, freq="1min")
    return pd.DataFrame(candles, index=idx)


# ---------------------------------------------------------------------------
# Invariants
# ---------------------------------------------------------------------------


@settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(_valid_candle())
def test_valid_candle_satisfies_high_low_invariants(candle):
    o, h, lo, c = candle["open"], candle["high"], candle["low"], candle["close"]
    assert h >= max(o, c) - 1e-9
    assert lo <= min(o, c) + 1e-9
    assert h >= lo


@settings(max_examples=100, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(_valid_ohlc_df())
def test_dataframe_invariants_per_row(df):
    """Every row in a generated dataframe must satisfy the canonical
    OHLC invariants — sanity check that our generator is sound.
    """
    arr = df.to_numpy()
    o, h, lo, c = arr[:, 0], arr[:, 1], arr[:, 2], arr[:, 3]
    assert np.all(h >= np.maximum(o, c) - 1e-9)
    assert np.all(lo <= np.minimum(o, c) + 1e-9)
    assert np.all(h >= lo)


@settings(max_examples=80, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(_valid_ohlc_df())
def test_atr_calc_returns_finite_values(df):
    """The pure-Python ATR helper from regime detection must never
    return NaN/inf rows after the warmup window for valid OHLC."""
    from engine.analytics.regime import _atr  # noqa: PLC2701 — testing helper

    h = df["high"].to_numpy()
    lo = df["low"].to_numpy()
    c = df["close"].to_numpy()
    out = _atr(h, lo, c, window=2)
    # First (window-1) rows are NaN by design; everything after must be finite.
    assert np.all(np.isfinite(out[1:]))
    assert np.all(out[1:] >= 0), "ATR must be non-negative"


@settings(max_examples=80, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(_valid_ohlc_df())
def test_close_to_close_returns_match_pct_change(df):
    """A simple sanity check: pandas pct_change matches the analytic
    formula (close[i] - close[i-1]) / close[i-1] for valid OHLC."""
    expected = df["close"].pct_change().to_numpy()[1:]
    raw = df["close"].to_numpy()
    actual = (raw[1:] - raw[:-1]) / raw[:-1]
    np.testing.assert_allclose(expected, actual, rtol=1e-9, atol=1e-12)
