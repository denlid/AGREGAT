"""Tests for engine.analytics.regime (Phase 4.2)."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from engine.analytics.regime import (
    REGIME_LABELS,
    classify_trades,
    regime_breakdown,
    regime_per_bar,
    regime_report_payload,
)


@dataclass
class _Trade:
    id: int
    entry_bar_index: int
    pnl: float


def _synthetic_bars(n: int, slope: float, noise: float = 0.0):
    """Build a smooth uptrend / downtrend / flat series of length n."""
    rng = np.random.default_rng(0)
    base = np.linspace(100.0, 100.0 + slope * n, n)
    if noise:
        base = base + rng.normal(0.0, noise, size=n)
    high = base + 0.5
    low = base - 0.5
    return high, low, base


def test_regime_per_bar_returns_aligned_arrays():
    h, low, c = _synthetic_bars(500, slope=0.05)
    series = regime_per_bar(h, low, c)
    assert series.regime.size == 500
    assert series.atr_norm.size == 500
    assert series.sma.size == 500


def test_uptrend_classifies_mostly_bull():
    """A clean uptrend should drive most bars into a BULL bucket."""
    h, low, c = _synthetic_bars(800, slope=0.10)
    series = regime_per_bar(h, low, c)
    # Look at the second half — first half is dominated by NaN SMA / RANGE.
    last_half = series.regime[400:]
    bull_mask = (last_half == 0) | (last_half == 1)  # bull_lo / bull_hi
    assert bull_mask.mean() > 0.6, "expected uptrend → predominantly bull"


def test_downtrend_classifies_mostly_bear():
    h, low, c = _synthetic_bars(800, slope=-0.10)
    series = regime_per_bar(h, low, c)
    last_half = series.regime[400:]
    bear_mask = (last_half == 2) | (last_half == 3)  # bear_lo / bear_hi
    assert bear_mask.mean() > 0.6, "expected downtrend → predominantly bear"


def test_classify_trades_attributes_at_entry_bar():
    h, low, c = _synthetic_bars(300, slope=0.05)
    series = regime_per_bar(h, low, c)
    trades = [_Trade(id=1, entry_bar_index=250, pnl=10.0)]
    out = classify_trades(trades, series)
    assert len(out) == 1 and out[0][0] == 1
    assert out[0][1] in REGIME_LABELS


def test_regime_breakdown_includes_zero_count_buckets():
    h, low, c = _synthetic_bars(300, slope=0.05)
    series = regime_per_bar(h, low, c)
    rows = regime_breakdown([], series)
    assert len(rows) == len(REGIME_LABELS)
    # Empty trades → all zero counts but bar counts populated.
    assert all(r.n_trades == 0 for r in rows)
    assert sum(r.n_bars for r in rows) == 300


def test_regime_breakdown_aggregates_pnl_correctly():
    h, low, c = _synthetic_bars(300, slope=0.05)
    series = regime_per_bar(h, low, c)
    trades = [
        _Trade(id=1, entry_bar_index=250, pnl=10.0),
        _Trade(id=2, entry_bar_index=255, pnl=-5.0),
        _Trade(id=3, entry_bar_index=260, pnl=20.0),
    ]
    rows = regime_breakdown(trades, series)
    total_trades = sum(r.n_trades for r in rows)
    total_pnl = sum(r.sum_pnl for r in rows)
    assert total_trades == 3
    assert abs(total_pnl - 25.0) < 1e-6


def test_regime_report_payload_returns_none_for_short_df():
    df = pd.DataFrame({"high": [1, 2, 3], "low": [0, 1, 2], "close": [1, 1, 2]})
    assert regime_report_payload([], df) is None


def test_regime_report_payload_basic_path():
    h, low, c = _synthetic_bars(300, slope=0.05)
    df = pd.DataFrame({"high": h, "low": low, "close": c})
    payload = regime_report_payload([], df)
    assert payload is not None
    assert "series" in payload and "rows" in payload
