"""
Engine Models вЂ” Data classes for positions, trades, orders, assets, and sessions.
"""

from datetime import time
from typing import Any, Optional

import numpy as np
import pandas as pd


# ==========================================
# Asset Configuration
# ==========================================
class AssetConfig:
    """Configuration for a single tradable asset.

    All execution costs default to 0 (idealised execution); set them via
    `config/assets.json` or via the Settings tab to model realistic backtests.
    """

    __slots__ = (
        "name",
        "point_value",
        "tick_size",
        "buffer",
        "max_lots_per_100k",
        "currency",
        "min_lot",
        "lot_step",
        "spread_points",
        "commission_per_lot",
        "slippage_points",
        "trading_hours_start",
        "trading_hours_end",
        "eod_close",
        "enforce_trading_hours",
        "enforce_eod_close",
        "close_on_weekend",
        # Phase 1.5 вЂ” per-asset session timezone and overrides.
        "session_tz",
        "sessions",
    )

    def __init__(
        self,
        name: str,
        point_value: float,
        tick_size: float,
        buffer: float,
        max_lots_per_100k: int,
        currency: str,
        min_lot: float = 0.01,
        lot_step: float = 0.01,
        spread_points: float = 0.0,
        commission_per_lot: float = 0.0,
        slippage_points: float = 0.0,
        trading_hours_start: time = time(9, 0),
        trading_hours_end: time = time(18, 0),
        eod_close: time = time(22, 0),
        enforce_trading_hours: bool = False,
        enforce_eod_close: bool = True,
        close_on_weekend: bool = True,
        session_tz: Optional[str] = None,
        sessions: Optional[list[tuple[str, time, time]]] = None,
    ) -> None:
        self.name = name
        self.point_value = point_value
        self.tick_size = tick_size
        self.buffer = buffer
        self.max_lots_per_100k = max_lots_per_100k
        self.currency = currency
        self.min_lot = min_lot
        self.lot_step = lot_step
        self.spread_points = spread_points
        self.commission_per_lot = commission_per_lot
        self.slippage_points = slippage_points
        self.trading_hours_start = trading_hours_start
        self.trading_hours_end = trading_hours_end
        self.eod_close = eod_close
        self.enforce_trading_hours = enforce_trading_hours
        self.enforce_eod_close = enforce_eod_close
        self.close_on_weekend = close_on_weekend
        # Phase 1.5 вЂ” IANA TZ name (e.g. "Europe/Kyiv", "UTC", "America/New_York").
        # ``None`` keeps the legacy implicit-UTC+3 behaviour where the
        # data timestamp is taken at face value. When supplied, the
        # raw timestamp is treated as UTC (or as already-localized if
        # the column is timezone-aware) and converted to the asset's
        # ``session_tz`` before session attribution / EOD checks.
        self.session_tz = session_tz
        # Optional override of the global :data:`SESSIONS` tuple list.
        # Same semantics: ``[(name, start_time, end_time), ...]``.
        self.sessions = sessions

    def __repr__(self) -> str:
        return (
            f"AssetConfig(name={self.name!r}, point_value={self.point_value}, "
            f"tick_size={self.tick_size}, buffer={self.buffer}, "
            f"max_lots_per_100k={self.max_lots_per_100k}, currency={self.currency!r}, "
            f"spread={self.spread_points}, commission={self.commission_per_lot}, "
            f"slippage={self.slippage_points})"
        )


# ==========================================
# Pending Order (entry on next candle's open)
# ==========================================
class PendingOrder:
    __slots__ = ("direction", "volume", "sl", "tp", "reason", "tag")

    def __init__(
        self,
        direction: str,
        volume: float,
        sl: Optional[float] = None,
        tp: Optional[float] = None,
        reason: str = "",
        tag: str = "",
    ) -> None:
        self.direction = direction
        self.volume = volume
        self.sl = sl
        self.tp = tp
        self.reason = reason
        self.tag = tag

    def __repr__(self) -> str:
        return (
            f"PendingOrder(direction={self.direction!r}, volume={self.volume}, "
            f"sl={self.sl}, tp={self.tp}, reason={self.reason!r})"
        )


# ==========================================
# Position (open trade)
# ==========================================
class Position:
    __slots__ = (
        "direction",
        "entry_price",
        "entry_index",
        "entry_time",
        "entry_time_np",
        "volume",
        "sl",
        "tp",
        "initial_sl",
        "mae",
        "mfe",
        "be_activated",
        "lock_activated",
        "tag",
    )

    def __init__(
        self,
        direction: str,
        entry_price: float,
        entry_index: int,
        entry_time: str,
        volume: float = 1.0,
        sl: Optional[float] = None,
        tp: Optional[float] = None,
        initial_sl: Optional[float] = None,
        mae: float = 0.0,
        mfe: float = 0.0,
        be_activated: bool = False,
        lock_activated: bool = False,
        tag: str = "",
        entry_time_np: Any = None,
    ) -> None:
        self.direction = direction
        self.entry_price = entry_price
        self.entry_index = entry_index
        self.entry_time = entry_time
        # Pre-converted ``np.datetime64`` of ``entry_time`` so the close path
        # can compute trade duration via plain numpy subtraction without
        # round-tripping through ``pd.to_datetime`` per trade. Adapters that
        # construct a ``Position`` from a non-canonical source can leave this
        # ``None`` вЂ” the engine will fall back to the slow path. (TZ Phase 2 / B2.)
        self.entry_time_np = entry_time_np
        self.volume = volume
        self.sl = sl
        self.tp = tp
        self.initial_sl = initial_sl
        self.mae = mae
        self.mfe = mfe
        self.be_activated = be_activated
        self.lock_activated = lock_activated
        self.tag = tag

    def __repr__(self) -> str:
        return (
            f"Position(direction={self.direction!r}, entry_price={self.entry_price}, "
            f"entry_index={self.entry_index}, sl={self.sl}, tp={self.tp})"
        )


# ==========================================
# Trade (closed position record)
# ==========================================
# Sentinel for Trade.__init__: distinguishes "caller did not supply a
# precomputed datetime64" (fall back to parsing ``entry_time``) from the
# legitimate value ``None`` (timestamp is genuinely absent). Callers may pass
# datetime64 values directly to skip the ``pd.to_datetime(str)`` round-trip.
_NP_TIME_UNSET = object()


class Trade:
    __slots__ = (
        "id",
        "direction",
        "entry_price",
        "exit_price",
        "pnl",
        "pnl_points",
        "entry_index",
        "exit_index",
        "entry_time",
        "exit_time",
        "entry_time_np",
        "exit_time_np",
        "volume",
        "duration_minutes",
        "exit_reason",
        "mae",
        "mfe",
        "mae_dollars",
        "mfe_dollars",
        "session",
        "initial_sl",
        "rr",
        "tag",
        "commission",
        "spread_cost",
        "slippage_cost",
        # Sprint 4.2 cross-cuts вЂ” populated at trade close.
        "dow",
        "hod",
        "regime",
    )

    def __init__(
        self,
        id: int,
        direction: str,
        entry_price: float,
        exit_price: float,
        pnl: float,
        pnl_points: float,
        entry_index: int,
        exit_index: int,
        entry_time: str,
        exit_time: str,
        volume: float,
        duration_minutes: int,
        exit_reason: str,
        mae: float,
        mfe: float,
        session: str,
        initial_sl: float = 0.0,
        rr: Optional[float] = None,
        mae_dollars: float = 0.0,
        mfe_dollars: float = 0.0,
        tag: str = "",
        commission: float = 0.0,
        spread_cost: float = 0.0,
        slippage_cost: float = 0.0,
        dow: str = "Unknown",
        hod: int = -1,
        regime: str = "unknown",
        entry_time_np: Any = _NP_TIME_UNSET,
        exit_time_np: Any = _NP_TIME_UNSET,
    ) -> None:
        self.id = id
        self.direction = direction
        self.entry_price = entry_price
        self.exit_price = exit_price
        self.pnl = pnl
        self.pnl_points = pnl_points
        self.entry_index = entry_index
        self.exit_index = exit_index
        self.entry_time = entry_time
        self.exit_time = exit_time
        # Pre-cache np.datetime64 for fast chart marker rendering.
        # Avoids repeated pd.to_datetime(str) on every redraw. Callers that
        # already hold the datetime64 pass it via ``entry_time_np`` /
        # ``exit_time_np`` to skip the costly string parse.
        if entry_time_np is _NP_TIME_UNSET:
            try:
                _et_ts = pd.to_datetime(entry_time, errors="coerce")
                self.entry_time_np = _et_ts.to_datetime64() if not pd.isna(_et_ts) else None
            except (ValueError, TypeError):
                self.entry_time_np = None
        else:
            self.entry_time_np = entry_time_np
        if exit_time_np is _NP_TIME_UNSET:
            try:
                _xt_ts = pd.to_datetime(exit_time, errors="coerce")
                self.exit_time_np = _xt_ts.to_datetime64() if not pd.isna(_xt_ts) else None
            except (ValueError, TypeError):
                self.exit_time_np = None
        else:
            self.exit_time_np = exit_time_np
        self.volume = volume
        self.duration_minutes = duration_minutes
        self.exit_reason = exit_reason
        self.mae = mae
        self.mfe = mfe
        self.mae_dollars = mae_dollars
        self.mfe_dollars = mfe_dollars
        self.session = session
        self.initial_sl = initial_sl
        self.rr = rr
        self.tag = tag
        self.commission = commission
        self.spread_cost = spread_cost
        self.slippage_cost = slippage_cost
        self.dow = dow
        self.hod = hod
        self.regime = regime

    def __repr__(self) -> str:
        return (
            f"Trade(id={self.id}, direction={self.direction!r}, "
            f"pnl={self.pnl:.2f}, exit_reason={self.exit_reason!r})"
        )


# ==========================================
# Session Definitions (UTC+3 Kyiv Time)
# ==========================================
SESSIONS = [
    ("Asia", time(2, 0), time(9, 0)),
    ("Frankfurt", time(9, 0), time(10, 0)),
    ("London_OTT", time(10, 0), time(12, 0)),
    ("London_Lunch", time(12, 0), time(14, 0)),
    ("Pre_NY", time(14, 0), time(15, 0)),
    ("US_Open", time(15, 0), time(15, 30)),
    ("US_Macro", time(15, 30), time(16, 30)),
    ("NYSE_Open", time(16, 30), time(19, 0)),
    ("US_Lunch", time(19, 0), time(20, 0)),
    ("Off_Hours", time(20, 0), time(2, 0)),  # Wraps midnight
]

# Trading hours: entries 09:00вЂ“18:00 by default, EOD close at 22:00
TRADING_HOURS_START = time(9, 0)
TRADING_HOURS_END = time(18, 0)
EOD_CLOSE_TIME = time(22, 0)


def _to_time_and_weekday(
    t: Any, asset: Optional[AssetConfig] = None
) -> tuple[Optional[time], Optional[int]]:
    """Normalise input to (datetime.time, weekday: int|None).

    Accepts datetime.time, str, pandas.Timestamp, datetime.datetime, np.datetime64.
    Returns (None, None) if conversion fails.

    Phase 1.5 вЂ” when ``asset.session_tz`` is set, the timestamp is
    converted to that zone before its time-of-day is extracted. Naive
    timestamps are first localized to UTC then converted; tz-aware
    inputs are converted directly. The legacy code path
    (``asset.session_tz is None``) is unchanged.
    """
    if isinstance(t, time):
        return t, None
    try:
        ts = pd.Timestamp(t)
        if asset is not None and getattr(asset, "session_tz", None):
            tz = asset.session_tz
            try:
                if ts.tzinfo is None:
                    ts = ts.tz_localize("UTC").tz_convert(tz)
                else:
                    ts = ts.tz_convert(tz)
            except Exception:  # pragma: no cover вЂ” invalid tz string
                pass
        return ts.time(), int(ts.weekday())
    except Exception:
        if hasattr(t, "time") and callable(t.time):
            try:
                return t.time(), getattr(t, "weekday", lambda: None)()
            except Exception:
                pass
        return None, None


def get_session(t: Any, asset: Optional[AssetConfig] = None) -> str:
    """Get session name for a given datetime / time / Timestamp / str.

    Saturday/Sunday always returns 'Weekend'.

    Phase 1.5 вЂ” when ``asset.sessions`` is supplied, that list is used
    instead of the global :data:`SESSIONS`. Combined with
    ``asset.session_tz``, the same raw timestamps can map to different
    session windows for different assets.
    """
    tt, wd = _to_time_and_weekday(t, asset=asset)
    if tt is None:
        return "Off_Hours"
    if wd is not None and wd >= 5:
        return "Weekend"

    sessions = (
        asset.sessions
        if asset is not None and getattr(asset, "sessions", None)
        else SESSIONS
    )
    for name, start, end in sessions:
        if start <= end:
            if start <= tt < end:
                return name
        else:  # Wraps midnight (Off_Hours: 20:00 в†’ 02:00)
            if tt >= start or tt < end:
                return name
    return "Off_Hours"


def is_trading_hours(t: Any, asset: Optional[AssetConfig] = None) -> bool:
    """Check if time is within configured trading hours (default 09:00вЂ“18:00 UTC+3).

    If an AssetConfig is supplied, its trading_hours_* and weekend rules win.
    Phase 1.5 вЂ” also honours ``asset.session_tz`` for the time-of-day check.
    """
    tt, wd = _to_time_and_weekday(t, asset=asset)
    if tt is None:
        return False
    start = asset.trading_hours_start if asset is not None else TRADING_HOURS_START
    end = asset.trading_hours_end if asset is not None else TRADING_HOURS_END
    weekend_block = (asset is None) or asset.close_on_weekend
    if weekend_block and wd is not None and wd >= 5:
        return False
    return start <= tt < end


def is_eod(t: Any, asset: Optional[AssetConfig] = None) -> bool:
    """Check if time is at or past EOD close (default 22:00 UTC+3)."""
    tt, _ = _to_time_and_weekday(t, asset=asset)
    if tt is None:
        return False
    eod = asset.eod_close if asset is not None else EOD_CLOSE_TIME
    return tt >= eod


def is_weekend(t: Any, asset: Optional[AssetConfig] = None) -> bool:
    """Check if timestamp falls on Saturday or Sunday."""
    _, wd = _to_time_and_weekday(t, asset=asset)
    return wd is not None and wd >= 5


__all__ = [
    "AssetConfig",
    "PendingOrder",
    "Position",
    "Trade",
    "SESSIONS",
    "TRADING_HOURS_START",
    "TRADING_HOURS_END",
    "EOD_CLOSE_TIME",
    "get_session",
    "is_trading_hours",
    "is_eod",
    "is_weekend",
]
