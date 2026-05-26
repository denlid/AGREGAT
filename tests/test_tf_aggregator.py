"""TimeframeAggregator: Zero-Future rule, bucket boundaries, midnight wrap."""

from __future__ import annotations

import pandas as pd
import pytest

from engine.data.tf_aggregator import TimeframeAggregator


def _make_1m(t: str, o=100, h=101, l=99, c=100, v=1.0) -> dict:
    return {
        "time": pd.Timestamp(t),
        "open": o,
        "high": h,
        "low": l,
        "close": c,
        "volume": v,
    }


def test_zero_future_rule_no_completion_until_period_elapses():
    """A 5m candle is completed only after the *next* bucket starts."""
    agg = TimeframeAggregator()
    base = "2024-03-13 09:00"
    # 4 candles in same 5m bucket (09:00–09:04) → no 5m completion yet
    for i in range(4):
        ts = pd.Timestamp(base) + pd.Timedelta(minutes=i)
        result = agg.feed(_make_1m(ts))
        assert result["5m"] is False, f"5m must not complete inside its window (i={i})"
    assert agg.get_completed("5m") == [], "no completed 5m candles before bucket end"

    # 5th 1m candle is the 5th bucket constituent (09:04) — still same bucket
    result = agg.feed(_make_1m(pd.Timestamp(base) + pd.Timedelta(minutes=4)))
    assert result["5m"] is False

    # The first candle in the *new* bucket triggers completion of the previous
    result = agg.feed(_make_1m(pd.Timestamp(base) + pd.Timedelta(minutes=5)))
    assert result["5m"] is True
    assert len(agg.get_completed("5m")) == 1


def test_5m_bucket_floors_correctly():
    """09:03 → 09:00 bucket; 09:07 → 09:05 bucket."""
    agg = TimeframeAggregator()
    agg.feed(_make_1m("2024-03-13 09:03"))
    forming = agg.get_forming("5m")
    assert forming["time"] == pd.Timestamp("2024-03-13 09:00")

    agg.feed(_make_1m("2024-03-13 09:07"))
    completed = agg.get_completed("5m")
    assert len(completed) == 1
    assert completed[0]["time"] == pd.Timestamp("2024-03-13 09:00")


def test_4h_bucket_aligns_to_midnight():
    """4h buckets are 00:00, 04:00, 08:00, …, 20:00 UTC.
    23:30 → 20:00 bucket; 02:00 next day → 00:00 bucket → completes 20:00 bucket.
    """
    agg = TimeframeAggregator()
    agg.feed(_make_1m("2024-03-13 23:30"))
    forming = agg.get_forming("4h")
    assert forming["time"] == pd.Timestamp("2024-03-13 20:00")

    # First candle of new day in the 00:00 bucket
    agg.feed(_make_1m("2024-03-14 02:00"))
    completed = agg.get_completed("4h")
    assert len(completed) == 1
    assert completed[0]["time"] == pd.Timestamp("2024-03-13 20:00")


def test_high_low_aggregates_across_constituents():
    """Forming candle high/low should track max/min across all 1m candles in the bucket."""
    agg = TimeframeAggregator()
    agg.feed(_make_1m("2024-03-13 09:00", o=100, h=101, l=99, c=100))
    agg.feed(_make_1m("2024-03-13 09:01", o=100, h=105, l=98, c=102))  # new high+low
    agg.feed(_make_1m("2024-03-13 09:02", o=102, h=103, l=97, c=101))  # new low

    forming = agg.get_forming("5m")
    assert forming["open"] == 100, "open must come from the first 1m candle"
    assert forming["high"] == 105
    assert forming["low"] == 97
    assert forming["close"] == 101


def test_unsupported_timeframe_raises():
    agg = TimeframeAggregator()
    with pytest.raises(ValueError):
        agg.get_completed("7m")


def test_reset_clears_all_state():
    agg = TimeframeAggregator()
    for i in range(10):
        agg.feed(_make_1m(pd.Timestamp("2024-03-13 09:00") + pd.Timedelta(minutes=i)))
    assert len(agg.get_completed("5m")) > 0

    agg.reset()
    for tf in agg.TF_MINUTES:
        assert agg.get_completed(tf) == []
        assert agg.get_forming(tf) is None


def test_get_last_completed_returns_most_recent():
    agg = TimeframeAggregator()
    base = pd.Timestamp("2024-03-13 09:00")
    for i in range(20):  # spans 4 full 5m buckets
        agg.feed(_make_1m(base + pd.Timedelta(minutes=i)))
    last = agg.get_last_completed("5m")
    assert last is not None
    # 20 candles span buckets [09:00, 09:05, 09:10, 09:15]. The 09:15 bucket
    # is still forming (Zero-Future Rule), so the most recent COMPLETED bucket
    # is 09:10.
    assert last["time"] == pd.Timestamp("2024-03-13 09:10")
