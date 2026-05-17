"""Phase 5.1 (G2) — gap-detection unit tests."""

from __future__ import annotations

import pandas as pd

from engine.data.gap_detection import detect_gaps


def _df(times):
    return pd.DataFrame({"time": pd.to_datetime(list(times))})


def test_no_gaps_returns_empty():
    df = _df(pd.date_range("2024-01-01 09:00", periods=20, freq="1min"))
    assert detect_gaps(df) == []


def test_detects_single_missing_bar():
    times = list(pd.date_range("2024-01-01 09:00", periods=10, freq="1min"))
    # Drop the 5th bar — should be flagged as missing 1.
    times.pop(5)
    out = detect_gaps(_df(times))
    assert len(out) == 1
    assert out[0]["missing_bars"] == 1


def test_detects_multi_minute_outage():
    times = list(pd.date_range("2024-01-01 09:00", periods=10, freq="1min"))
    # Drop 4 consecutive bars — single 5-minute hole.
    del times[3:7]
    out = detect_gaps(_df(times))
    assert len(out) == 1
    # Only the gap descriptor itself, not 4 separate entries.
    assert out[0]["missing_bars"] == 4


def test_skips_weekend_by_default():
    """Friday close → Monday open must NOT be flagged."""
    fri = pd.date_range("2024-01-05 14:30", "2024-01-05 16:00", freq="1min")  # Fri
    mon = pd.date_range("2024-01-08 09:00", "2024-01-08 10:00", freq="1min")  # Mon
    times = list(fri) + list(mon)
    assert detect_gaps(_df(times)) == []


def test_does_not_skip_weekend_when_disabled():
    """skip_weekend=False reveals the calendar gap as expected."""
    fri = pd.date_range("2024-01-05 14:30", "2024-01-05 16:00", freq="1min")
    mon = pd.date_range("2024-01-08 09:00", "2024-01-08 10:00", freq="1min")
    times = list(fri) + list(mon)
    out = detect_gaps(_df(times), skip_weekend=False)
    assert len(out) == 1


def test_explicit_interval_overrides_inference():
    """Intervals come from the caller when provided — useful when the
    dataset has too many holes for inference to be reliable."""
    times = pd.date_range("2024-01-01 09:00", periods=5, freq="5min")
    # Modal delta is 5 min; explicit 1 min should flag every gap.
    out = detect_gaps(_df(times), interval_seconds=60.0)
    assert len(out) == 4
    for gap in out:
        # Each contiguous 5-minute span -> 4 missing 1-minute bars.
        assert gap["missing_bars"] == 4


def test_empty_or_single_row_dataframe_returns_empty():
    assert detect_gaps(pd.DataFrame()) == []
    assert detect_gaps(_df(["2024-01-01 09:00"])) == []


def test_handles_missing_time_column():
    df = pd.DataFrame({"open": [1.0, 2.0]})
    assert detect_gaps(df) == []


def test_min_gap_multiplier_filters_jitter():
    """A single 1.05x bar should NOT be flagged with the default 1.5x cutoff."""
    times = list(pd.date_range("2024-01-01 09:00", periods=10, freq="1min"))
    # Push one timestamp 3 seconds late — within jitter tolerance.
    times[5] = times[5] + pd.Timedelta(seconds=3)
    assert detect_gaps(_df(times)) == []
