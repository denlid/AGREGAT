"""Sprint 4.2 — Tests for engine.metrics.cross_cuts."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

import pandas as pd
import pytest

from engine.metrics.cross_cuts import (
    cross_cut,
    derive_trade_attributes,
    trade_direction,
    trade_dow,
    trade_hod,
    trade_regime,
    trade_session,
)


@dataclass
class _T:
    """Minimal trade-like object for cross-cut tests."""

    id: int
    direction: str
    entry_time: datetime | None
    pnl: float
    duration_minutes: float = 0.0
    exit_time: datetime | None = None
    session: str | None = None
    slippage_cost: float = 0.0
    commission: float = 0.0


# -----------------------------------------------------------------------------
# Per-trade derivations
# -----------------------------------------------------------------------------
class TestPerTradeDerivations:
    def test_session_explicit_takes_precedence(self) -> None:
        t = _T(
            id=1,
            direction="long",
            entry_time=datetime(2024, 1, 1, 14, 30),
            pnl=10.0,
            session="Override",
        )
        assert trade_session(t) == "Override"

    def test_session_inferred_from_entry_time(self) -> None:
        # Entry at 14:30 UTC → US session in get_session() (or None if outside)
        t = _T(id=1, direction="long", entry_time=datetime(2024, 1, 1, 14, 30), pnl=10.0)
        # We don't pin the exact label — just confirm it's a string.
        assert isinstance(trade_session(t), str)

    def test_dow(self) -> None:
        # 2024-01-01 was a Monday
        t = _T(id=1, direction="long", entry_time=datetime(2024, 1, 1, 9, 30), pnl=0.0)
        assert trade_dow(t) == "Mon"

    def test_dow_unknown_for_missing_time(self) -> None:
        t = _T(id=1, direction="long", entry_time=None, pnl=0.0)
        assert trade_dow(t) == "Unknown"

    def test_hod(self) -> None:
        t = _T(id=1, direction="long", entry_time=datetime(2024, 1, 1, 14, 30), pnl=0.0)
        assert trade_hod(t) == 14

    def test_hod_minus_one_for_missing(self) -> None:
        t = _T(id=1, direction="long", entry_time=None, pnl=0.0)
        assert trade_hod(t) == -1

    def test_direction_normalisation(self) -> None:
        for d in ("long", "BUY", "+1", "1"):
            t = _T(id=1, direction=d, entry_time=None, pnl=0.0)
            assert trade_direction(t) == "long"
        for d in ("short", "SELL", "-1"):
            t = _T(id=1, direction=d, entry_time=None, pnl=0.0)
            assert trade_direction(t) == "short"
        t = _T(id=1, direction="??", entry_time=None, pnl=0.0)
        assert trade_direction(t) == "flat"

    def test_regime_unknown_without_map(self) -> None:
        t = _T(id=1, direction="long", entry_time=None, pnl=0.0)
        assert trade_regime(t) == "unknown"

    def test_regime_with_map(self) -> None:
        t = _T(id=42, direction="long", entry_time=None, pnl=0.0)
        assert trade_regime(t, regimes={"42": "trend"}) == "trend"


# -----------------------------------------------------------------------------
# DataFrame projection
# -----------------------------------------------------------------------------
class TestDeriveAttributes:
    def test_empty(self) -> None:
        df = derive_trade_attributes([])
        assert df.empty

    def test_columns(self) -> None:
        t = _T(id=1, direction="long", entry_time=datetime(2024, 1, 1, 14), pnl=5.0)
        df = derive_trade_attributes([t])
        # Required columns for the cross-cut UI
        for col in (
            "id",
            "direction",
            "session",
            "dow",
            "hod",
            "regime",
            "pnl",
            "duration_min",
        ):
            assert col in df.columns


# -----------------------------------------------------------------------------
# cross_cut API
# -----------------------------------------------------------------------------
class TestCrossCut:
    def _trades(self) -> list[_T]:
        return [
            _T(id=1, direction="long", entry_time=datetime(2024, 1, 1, 9, 30), pnl=10.0),
            _T(id=2, direction="long", entry_time=datetime(2024, 1, 1, 9, 45), pnl=-5.0),
            _T(id=3, direction="short", entry_time=datetime(2024, 1, 1, 14, 30), pnl=20.0),
            _T(id=4, direction="short", entry_time=datetime(2024, 1, 2, 14, 30), pnl=-2.0),
        ]

    def test_unknown_metric_raises(self) -> None:
        with pytest.raises(ValueError):
            cross_cut(self._trades(), metric="bogus", group_by="dow")

    def test_unknown_group_by_raises(self) -> None:
        with pytest.raises(ValueError):
            cross_cut(self._trades(), metric="sum_pnl", group_by="bogus")

    def test_sum_pnl_by_direction(self) -> None:
        res = cross_cut(self._trades(), metric="sum_pnl", group_by="direction")
        df = res.rows
        assert set(df["direction"]) == {"long", "short"}
        long_row = df[df["direction"] == "long"].iloc[0]
        short_row = df[df["direction"] == "short"].iloc[0]
        assert math.isclose(float(long_row["value"]), 5.0)  # 10 + -5
        assert math.isclose(float(short_row["value"]), 18.0)  # 20 + -2
        assert int(long_row["n"]) == 2
        assert int(short_row["n"]) == 2

    def test_win_rate_by_direction(self) -> None:
        res = cross_cut(self._trades(), metric="win_rate", group_by="direction")
        df = res.rows.set_index("direction")
        assert math.isclose(float(df.loc["long", "value"]), 0.5)
        assert math.isclose(float(df.loc["short", "value"]), 0.5)

    def test_filter_predicate(self) -> None:
        # Only trades with PnL > 0
        res = cross_cut(
            self._trades(),
            metric="count",
            group_by="direction",
            filter=lambda t: t.pnl > 0,
        )
        df = res.rows.set_index("direction")
        assert math.isclose(float(df.loc["long", "value"]), 1.0)
        assert math.isclose(float(df.loc["short", "value"]), 1.0)

    def test_empty_trades_returns_empty_df(self) -> None:
        res = cross_cut([], metric="sum_pnl", group_by="direction")
        assert isinstance(res.rows, pd.DataFrame)
        assert res.rows.empty
