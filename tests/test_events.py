"""Typed events — payload shape, dict round-trip, and computed properties."""

from __future__ import annotations

import math

import pytest

from engine.models.events import (
    CandleEvent,
    DrawingChangedEvent,
    PendingFilledEvent,
    PositionClosedEvent,
    PositionOpenedEvent,
    SimulationFinishedEvent,
    StatsUpdatedEvent,
    StepCompletedEvent,
    TradeCompletedEvent,
)
from engine.models.models import Position, Trade


def _trade(pnl: float, direction: str = "long") -> Trade:
    return Trade(
        id=1,
        direction=direction,
        entry_price=100.0,
        exit_price=100 + pnl,
        pnl=pnl,
        pnl_points=pnl,
        entry_index=0,
        exit_index=1,
        entry_time="2024-01-02 10:00",
        exit_time="2024-01-02 10:05",
        volume=1.0,
        duration_minutes=5,
        exit_reason="TP",
        mae=0.0,
        mfe=0.0,
        mae_dollars=0.0,
        mfe_dollars=0.0,
        session="London_OTT",
        initial_sl=99.0,
        rr=1.0,
        tag="",
    )


def _pos() -> Position:
    return Position(
        direction="long",
        entry_price=100.0,
        entry_index=0,
        entry_time="2024-01-02 10:00",
        volume=1.0,
        sl=99.0,
        tp=102.0,
        initial_sl=99.0,
        tag="t",
    )


def test_candle_event_round_trip():
    e = CandleEvent(index=5, time="2024-01-02 10:00", open=1, high=2, low=0.5, close=1.5)
    d = e.to_dict()
    assert d == {
        "index": 5,
        "time": "2024-01-02 10:00",
        "open": 1,
        "high": 2,
        "low": 0.5,
        "close": 1.5,
        "volume": 0.0,
    }


def test_step_completed_keeps_candle_dict_intact():
    candle = {"time": "t", "open": 1.0, "high": 1.5, "low": 0.5, "close": 1.0}
    e = StepCompletedEvent(index=10, candle=candle)
    assert e.to_dict()["candle"] is candle


def test_pending_filled_carries_position():
    pos = _pos()
    e = PendingFilledEvent(position=pos)
    assert e.to_dict()["position"] is pos


def test_position_opened_and_closed_events():
    pos = _pos()
    trd = _trade(+10.0)
    open_evt = PositionOpenedEvent(position=pos)
    close_evt = PositionClosedEvent(trade=trd)
    completed_evt = TradeCompletedEvent(trade=trd)
    assert open_evt.to_dict()["position"] is pos
    assert close_evt.to_dict()["trade"] is trd
    assert completed_evt.to_dict()["trade"] is trd


def test_stats_updated_computes_win_rate_and_pf():
    e = StatsUpdatedEvent(
        total_trades=4,
        wins=3,
        losses=1,
        longs=2,
        shorts=2,
        gross_profit=30.0,
        gross_loss=10.0,
        equity=100120.0,
        max_equity=100120.0,
        max_drawdown_abs=20.0,
        max_drawdown_pct=0.02,
    )
    assert e.win_rate == pytest.approx(75.0)
    assert e.profit_factor == pytest.approx(3.0)
    d = e.to_dict()
    assert d["win_rate"] == pytest.approx(75.0)
    assert d["profit_factor"] == pytest.approx(3.0)


def test_stats_updated_pf_inf_when_zero_gross_loss():
    e = StatsUpdatedEvent(
        total_trades=2,
        wins=2,
        losses=0,
        longs=2,
        shorts=0,
        gross_profit=20.0,
        gross_loss=0.0,
        equity=10020.0,
        max_equity=10020.0,
        max_drawdown_abs=0.0,
        max_drawdown_pct=0.0,
    )
    assert math.isinf(e.profit_factor)


def test_stats_updated_pf_zero_when_no_trades():
    e = StatsUpdatedEvent(
        total_trades=0,
        wins=0,
        losses=0,
        longs=0,
        shorts=0,
        gross_profit=0.0,
        gross_loss=0.0,
        equity=10000.0,
        max_equity=10000.0,
        max_drawdown_abs=0.0,
        max_drawdown_pct=0.0,
    )
    assert e.profit_factor == 0.0
    assert e.win_rate == 0.0


def test_simulation_finished_event_round_trip():
    e = SimulationFinishedEvent(
        total_candles=10_000,
        total_trades=42,
        final_equity=110_500.0,
        return_pct=10.5,
        max_drawdown_pct=2.7,
    )
    d = e.to_dict()
    assert d["total_trades"] == 42
    assert d["final_equity"] == 110_500.0


def test_drawing_changed_event_optional_payload():
    e1 = DrawingChangedEvent(drawing_id=3, action="added")
    e2 = DrawingChangedEvent(drawing_id=3, action="updated", payload={"x": 1})
    assert e1.to_dict()["payload"] is None
    assert e2.to_dict()["payload"] == {"x": 1}
