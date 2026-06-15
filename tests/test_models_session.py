"""Tests for engine.models session helpers and AssetConfig defaults."""

from datetime import datetime, time, timedelta

import numpy as np
import pandas as pd
import pytest

from engine.models.models import (
    SESSIONS,
    AssetConfig,
    get_session,
    is_eod,
    is_trading_hours,
    is_weekend,
)

# A typical Wednesday so weekend/weekday rules don't clash with session lookup
WED = pd.Timestamp("2024-03-13 ")  # Wed


# --------------------------------------------------------------------------
# get_session
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "t,expected",
    [
        (time(0, 30), "Off_Hours"),  # midnight wrap
        (time(2, 0), "Asia"),
        (time(8, 59), "Asia"),
        (time(9, 0), "Frankfurt"),
        (time(10, 0), "London_OTT"),
        (time(12, 0), "London_Lunch"),
        (time(14, 0), "Pre_NY"),
        (time(15, 0), "US_Open"),
        (time(15, 30), "US_Macro"),
        (time(16, 30), "NYSE_Open"),
        (time(19, 0), "US_Lunch"),
        (time(20, 0), "Off_Hours"),
        (time(23, 59), "Off_Hours"),
    ],
)
def test_get_session_covers_all_buckets(t, expected):
    assert get_session(WED + timedelta(hours=t.hour, minutes=t.minute)) == expected


def test_get_session_returns_weekend_for_saturday_and_sunday():
    sat = pd.Timestamp("2024-03-16 14:00")  # Sat
    sun = pd.Timestamp("2024-03-17 03:00")  # Sun
    assert get_session(sat) == "Weekend"
    assert get_session(sun) == "Weekend"


def test_get_session_handles_datetime_time_directly():
    # datetime.time alone has no weekday → fall through session lookup
    assert get_session(time(11, 0)) == "London_OTT"


def test_get_session_off_hours_midnight_wrap():
    # 00:30 — must hit the wrapping Off_Hours range, not crash
    assert get_session(time(0, 30)) == "Off_Hours"


@pytest.mark.parametrize(
    "value",
    [
        np.datetime64("2024-03-13T00:30:00"),
        np.datetime64("2024-03-13T09:00:00"),
        np.datetime64("2024-03-13T15:30:00"),
        np.datetime64("2024-03-16T14:00:00"),
    ],
)
def test_np_datetime64_matches_timestamp_session_lookup(value):
    assert get_session(value) == get_session(pd.Timestamp(value))


def test_sessions_table_is_consistent():
    # Every session should be a (name, time, time) tuple
    for entry in SESSIONS:
        assert len(entry) == 3
        name, start, end = entry
        assert isinstance(name, str) and name
        assert isinstance(start, time) and isinstance(end, time)


# --------------------------------------------------------------------------
# is_trading_hours
# --------------------------------------------------------------------------
def test_default_trading_hours_block_outside_window():
    assert is_trading_hours(WED + timedelta(hours=10)) is True
    assert is_trading_hours(WED + timedelta(hours=8, minutes=59)) is False
    assert is_trading_hours(WED + timedelta(hours=18)) is False
    assert is_trading_hours(WED + timedelta(hours=22)) is False


def test_np_datetime64_matches_timestamp_trading_hours():
    values = [
        np.datetime64("2024-03-13T08:59:00"),
        np.datetime64("2024-03-13T09:00:00"),
        np.datetime64("2024-03-13T17:59:00"),
        np.datetime64("2024-03-13T18:00:00"),
        np.datetime64("2024-03-16T12:00:00"),
    ]
    for value in values:
        assert is_trading_hours(value) is is_trading_hours(pd.Timestamp(value))


def test_trading_hours_blocked_on_weekend_when_close_on_weekend():
    asset = AssetConfig(
        name="TEST",
        point_value=1.0,
        tick_size=0.1,
        buffer=0.0,
        max_lots_per_100k=10,
        currency="USD",
        close_on_weekend=True,
    )
    sat_noon = pd.Timestamp("2024-03-16 12:00")
    assert is_trading_hours(sat_noon, asset) is False


def test_trading_hours_open_on_weekend_when_close_on_weekend_false():
    asset = AssetConfig(
        name="TEST",
        point_value=1.0,
        tick_size=0.1,
        buffer=0.0,
        max_lots_per_100k=10,
        currency="USD",
        close_on_weekend=False,
    )
    sat_noon = pd.Timestamp("2024-03-16 12:00")
    assert is_trading_hours(sat_noon, asset) is True


def test_trading_hours_uses_asset_window_when_provided():
    asset = AssetConfig(
        name="TEST",
        point_value=1.0,
        tick_size=0.1,
        buffer=0.0,
        max_lots_per_100k=10,
        currency="USD",
        trading_hours_start=time(7, 0),
        trading_hours_end=time(20, 0),
    )
    assert is_trading_hours(WED + timedelta(hours=7), asset) is True
    assert is_trading_hours(WED + timedelta(hours=20), asset) is False


# --------------------------------------------------------------------------
# is_eod
# --------------------------------------------------------------------------
def test_is_eod_default_22_00():
    assert is_eod(WED + timedelta(hours=21, minutes=59)) is False
    assert is_eod(WED + timedelta(hours=22)) is True
    assert is_eod(WED + timedelta(hours=23)) is True


def test_np_datetime64_matches_timestamp_eod():
    values = [
        np.datetime64("2024-03-13T21:59:00"),
        np.datetime64("2024-03-13T22:00:00"),
        np.datetime64("2024-03-13T23:00:00"),
    ]
    for value in values:
        assert is_eod(value) is is_eod(pd.Timestamp(value))


def test_is_eod_uses_asset_eod_when_provided():
    asset = AssetConfig(
        name="TEST",
        point_value=1.0,
        tick_size=0.1,
        buffer=0.0,
        max_lots_per_100k=10,
        currency="USD",
        eod_close=time(20, 0),
    )
    assert is_eod(WED + timedelta(hours=19), asset) is False
    assert is_eod(WED + timedelta(hours=20), asset) is True


# --------------------------------------------------------------------------
# is_weekend
# --------------------------------------------------------------------------
def test_is_weekend_basic():
    assert is_weekend(pd.Timestamp("2024-03-16 12:00")) is True  # Sat
    assert is_weekend(pd.Timestamp("2024-03-17 12:00")) is True  # Sun
    assert is_weekend(pd.Timestamp("2024-03-13 12:00")) is False  # Wed
    assert is_weekend(time(12, 0)) is False  # no weekday info


def test_np_datetime64_matches_timestamp_weekend():
    values = [
        np.datetime64("2024-03-13T12:00:00"),
        np.datetime64("2024-03-16T12:00:00"),
        np.datetime64("2024-03-17T12:00:00"),
        np.datetime64("2024-03-18T00:00:00"),
    ]
    for value in values:
        assert is_weekend(value) is is_weekend(pd.Timestamp(value))


def test_is_weekend_handles_invalid_input_gracefully():
    assert is_weekend("not a timestamp") is False
    assert is_weekend(None) is False
