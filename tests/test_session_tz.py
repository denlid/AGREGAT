"""Phase 1.5 — Per-asset session timezone + sessions override."""

from __future__ import annotations

from datetime import time

import numpy as np
import pandas as pd
import pytest

from engine.models.models import (
    SESSIONS,
    AssetConfig,
    get_session,
    is_eod,
    is_trading_hours,
)
from engine.core.backtest_core import BacktestCore


def _asset(tz: str | None = None, sessions=None) -> AssetConfig:
    return AssetConfig(
        name="X",
        point_value=1.0,
        tick_size=1.0,
        buffer=0.0,
        max_lots_per_100k=1,
        currency="USD",
        session_tz=tz,
        sessions=sessions,
    )


def test_default_no_tz_is_legacy_behaviour() -> None:
    """Without session_tz, get_session reads the raw timestamp (UTC+3 implicit)."""
    # 11:00 falls in London_OTT (10:00–12:00) per default SESSIONS.
    ts = pd.Timestamp("2024-01-03 11:00")
    assert get_session(ts) == "London_OTT"
    assert get_session(ts, asset=_asset()) == "London_OTT"


def test_session_tz_shifts_attribution_utc_to_kyiv_summer() -> None:
    """A naive UTC timestamp at 06:00 in summer maps to 09:00 Kyiv (DST → +3)."""
    ts = pd.Timestamp("2024-07-03 06:00")  # Wednesday in EEST (UTC+3)
    asset = _asset(tz="Europe/Kyiv")
    assert get_session(ts, asset=asset) == "Frankfurt"


def test_np_datetime64_with_session_tz_keeps_timezone_semantics() -> None:
    ts = np.datetime64("2024-07-03T06:00:00")
    asset = _asset(tz="Europe/Kyiv")
    assert get_session(ts, asset=asset) == "Frankfurt"


def test_backtest_core_precomputes_asset_local_time_parts() -> None:
    core = BacktestCore()
    core.asset_config = _asset(tz="Europe/Kyiv")
    df = pd.DataFrame(
        {
            "time": pd.to_datetime(["2024-07-03 06:00", "2024-07-03 19:00"]),
            "open": [1.0, 1.0],
            "high": [1.0, 1.0],
            "low": [1.0, 1.0],
            "close": [1.0, 1.0],
            "volume": [1.0, 1.0],
        }
    )

    core.load_data(df)

    assert core._minute_of_day.tolist() == [9 * 60, 22 * 60]
    assert core._weekday.tolist() == [2, 2]


def test_session_tz_shifts_attribution_winter_offset() -> None:
    """Winter Kyiv = UTC+2, so 06:00 UTC = 08:00 Kyiv → Asia session."""
    ts = pd.Timestamp("2024-01-03 06:00")  # EET = UTC+2
    asset = _asset(tz="Europe/Kyiv")
    assert get_session(ts, asset=asset) == "Asia"


def test_session_tz_us_eastern_shifts_correctly() -> None:
    """06:00 UTC = 01:00 New York → Off_Hours (20:00 → 02:00 wraps midnight)."""
    ts = pd.Timestamp("2024-01-03 06:00")
    asset = _asset(tz="America/New_York")
    assert get_session(ts, asset=asset) == "Off_Hours"


def test_tz_aware_timestamp_converted_directly() -> None:
    """Already-localized timestamps bypass the localize-to-UTC step."""
    ts = pd.Timestamp("2024-01-03 14:00", tz="America/New_York")
    # 14:00 NY (EST = UTC-5) = 21:00 Kyiv (EET = UTC+2) → Off_Hours (20:00–02:00).
    asset = _asset(tz="Europe/Kyiv")
    assert get_session(ts, asset=asset) == "Off_Hours"


def test_sessions_override_uses_custom_windows() -> None:
    """Custom sessions list overrides the global SESSIONS for that asset."""
    custom = [
        ("AsiaCustom", time(0, 0), time(12, 0)),
        ("EveningCustom", time(12, 0), time(0, 0)),
    ]
    asset = _asset(sessions=custom)
    assert get_session(pd.Timestamp("2024-01-03 09:00"), asset=asset) == "AsiaCustom"
    assert get_session(pd.Timestamp("2024-01-03 15:00"), asset=asset) == "EveningCustom"


def test_is_trading_hours_respects_tz() -> None:
    """trading_hours_start/end run on the asset's local clock (summer DST)."""
    asset = AssetConfig(
        name="X",
        point_value=1.0,
        tick_size=1.0,
        buffer=0.0,
        max_lots_per_100k=1,
        currency="USD",
        trading_hours_start=time(9, 0),
        trading_hours_end=time(18, 0),
        enforce_trading_hours=True,
        session_tz="Europe/Kyiv",
    )
    # 06:00 UTC = 09:00 Kyiv (EEST) → in trading hours.
    assert is_trading_hours(pd.Timestamp("2024-07-03 06:00"), asset=asset)
    # 05:00 UTC = 08:00 Kyiv → out of trading hours.
    assert not is_trading_hours(pd.Timestamp("2024-07-03 05:00"), asset=asset)


def test_is_eod_respects_tz() -> None:
    """EOD close compared against asset-local time (summer DST)."""
    asset = AssetConfig(
        name="X",
        point_value=1.0,
        tick_size=1.0,
        buffer=0.0,
        max_lots_per_100k=1,
        currency="USD",
        eod_close=time(22, 0),
        session_tz="Europe/Kyiv",
    )
    # 19:00 UTC = 22:00 Kyiv (EEST) → EOD.
    assert is_eod(pd.Timestamp("2024-07-03 19:00"), asset=asset)
    # 18:30 UTC = 21:30 Kyiv → not yet.
    assert not is_eod(pd.Timestamp("2024-07-03 18:30"), asset=asset)


def test_invalid_tz_falls_back_silently() -> None:
    """A bogus tz string should not crash session lookup."""
    asset = _asset(tz="Mars/Olympus_Mons")
    ts = pd.Timestamp("2024-01-03 11:00")
    # Behaviour falls back to raw timestamp — same as no tz.
    assert get_session(ts, asset=asset) in {s[0] for s in SESSIONS} | {"Off_Hours"}


def test_global_sessions_unchanged_for_legacy_callers() -> None:
    """Sanity: pure ``get_session(t)`` (no asset) keeps old semantics."""
    ts = pd.Timestamp("2024-01-03 11:00")
    assert get_session(ts) == "London_OTT"


# Make sure round-trip with empty asset (no tz) doesn't blow up:
@pytest.mark.parametrize(
    "ts",
    [
        pd.Timestamp("2024-01-06 12:00"),  # Saturday
        pd.Timestamp("2024-01-07 12:00"),  # Sunday
    ],
)
def test_weekend_attribution_with_tz(ts) -> None:
    asset = _asset(tz="Europe/Kyiv")
    assert get_session(ts, asset=asset) == "Weekend"
