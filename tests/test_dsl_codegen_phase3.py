"""Sprint 4.8 — Tests for DSL codegen Phase 3 (filters / entries / exits)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from engine.ai.dsl.codegen import (
    CodegenError,
    codegen_entry,
    codegen_exit,
    codegen_filter,
    codegen_program,
)


def _exec(lines: list[str], scope: dict[str, object]) -> dict[str, object]:
    """Exec ``lines`` as Python source against ``scope`` and return it."""
    src = "\n".join(lines)
    exec(compile(src, "<test>", "exec"), scope)
    return scope


def _ohlc(n: int = 8) -> dict[str, np.ndarray]:
    """Synthetic OHLC arrays with a known cross at bar 4."""
    close = np.array([1.0, 1.1, 1.2, 1.3, 1.4, 1.3, 1.2, 1.1])
    high = close + 0.1
    low = close - 0.1
    open_ = close - 0.05
    volume = np.ones(n)
    return {"close": close, "high": high, "low": low, "open_": open_, "volume": volume}


def test_cross_above_detects_crossing_bar() -> None:
    a = np.array([0.0, 0.0, 1.0, 2.0])
    b = np.array([1.0, 1.0, 1.0, 1.0])
    scope = {"a": a, "b": b, "np": np}
    _exec(
        codegen_filter({"id": "x", "type": "cross_above", "a": "a", "b": "b"}),
        scope,
    )
    np.testing.assert_array_equal(scope["x"], np.array([False, False, False, True]))


def test_cross_below_detects_crossing_bar() -> None:
    a = np.array([2.0, 2.0, 0.5, 0.0])
    b = np.array([1.0, 1.0, 1.0, 1.0])
    scope = {"a": a, "b": b, "np": np}
    _exec(
        codegen_filter({"id": "x", "type": "cross_below", "a": "a", "b": "b"}),
        scope,
    )
    np.testing.assert_array_equal(scope["x"], np.array([False, False, True, False]))


def test_break_above_below() -> None:
    scope = {**_ohlc(), "np": np}
    _exec(
        codegen_filter({"id": "ba", "type": "break_above", "level": 1.25}),
        scope,
    )
    _exec(
        codegen_filter({"id": "bb", "type": "break_below", "level": 1.15}),
        scope,
    )
    np.testing.assert_array_equal(scope["ba"], scope["close"] > 1.25)
    np.testing.assert_array_equal(scope["bb"], scope["close"] < 1.15)


def test_in_zone_inclusive() -> None:
    scope = {**_ohlc(), "np": np}
    _exec(
        codegen_filter({"id": "z", "type": "in_zone", "low": 1.1, "high": 1.3}),
        scope,
    )
    np.testing.assert_array_equal(scope["z"], (scope["close"] >= 1.1) & (scope["close"] <= 1.3))


def test_time_of_day_window() -> None:
    times = pd.date_range("2024-01-01 09:00", periods=8, freq="1h").to_numpy()
    scope = {"time": times, "close": np.zeros(8), "np": np}
    _exec(
        codegen_filter({"id": "tod", "type": "time_of_day", "start": "10:00", "end": "12:00"}),
        scope,
    )
    # 09, 10, 11, 12, 13, 14, 15, 16  -> True at 10/11/12
    expect = np.array([False, True, True, True, False, False, False, False])
    np.testing.assert_array_equal(scope["tod"], expect)


def test_time_of_day_invalid_format_raises() -> None:
    with pytest.raises(CodegenError):
        codegen_filter({"id": "x", "type": "time_of_day", "start": "9am", "end": "10:00"})
    with pytest.raises(CodegenError):
        codegen_filter({"id": "x", "type": "time_of_day", "start": "25:00", "end": "23:59"})


def test_weekday_filter() -> None:
    times = pd.date_range("2024-01-01", periods=7, freq="1D").to_numpy()
    scope = {"time": times, "np": np}
    _exec(
        codegen_filter({"id": "w", "type": "weekday", "days": [0, 1, 2, 3, 4]}),
        scope,
    )
    # 2024-01-01 is Monday -> Mon, Tue, Wed, Thu, Fri, Sat, Sun
    np.testing.assert_array_equal(
        scope["w"],
        np.array([True, True, True, True, True, False, False]),
    )


def test_buy_and_sell_signals() -> None:
    when = np.array([False, True, False, True, False])
    scope = {"when": when, "np": np}
    _exec(codegen_entry({"id": "buy", "type": "buy_at_next_open", "when": "when"}), scope)
    _exec(codegen_entry({"id": "sell", "type": "sell_at_next_open", "when": "when"}), scope)
    np.testing.assert_array_equal(scope["buy"], np.array([0, 1, 0, 1, 0], dtype="int8"))
    np.testing.assert_array_equal(scope["sell"], np.array([0, -1, 0, -1, 0], dtype="int8"))


def test_move_to_be_and_trail_to_pivot() -> None:
    when = np.array([True, False, True])
    pivots = np.array([1.1, 1.2, 1.3])
    scope = {"when": when, "pivots": pivots, "np": np}
    _exec(codegen_entry({"id": "be", "type": "move_to_be", "when": "when"}), scope)
    _exec(
        codegen_entry({"id": "tr", "type": "trail_to_pivot", "pivots": "pivots"}),
        scope,
    )
    np.testing.assert_array_equal(scope["be"], when)
    np.testing.assert_array_equal(scope["tr"], pivots)


def test_exits() -> None:
    atr = np.array([0.5, 0.4, 0.6])
    scope = {"atr": atr, "np": np}
    _exec(codegen_exit({"id": "sl", "type": "fixed_pts_sl", "pts": 25}), scope)
    _exec(codegen_exit({"id": "tp", "type": "fixed_rr_tp", "rr": 2.5}), scope)
    _exec(codegen_exit({"id": "asl", "type": "atr_mult_sl", "atr": "atr", "mult": 2}), scope)
    assert scope["sl"] == 25.0
    assert scope["tp"] == 2.5
    np.testing.assert_array_almost_equal(scope["asl"], atr * 2)


def test_unknown_node_type_raises() -> None:
    with pytest.raises(CodegenError):
        codegen_filter({"id": "x", "type": "??"})
    with pytest.raises(CodegenError):
        codegen_entry({"id": "x", "type": "??"})
    with pytest.raises(CodegenError):
        codegen_exit({"id": "x", "type": "??"})


def test_codegen_program_combines_groups() -> None:
    src = codegen_program(
        indicators=[{"id": "e1", "type": "ema", "period": 5}],
        filters=[{"id": "f1", "type": "break_above", "level": 100}],
        entries=[{"id": "buy", "type": "buy_at_next_open", "when": "f1"}],
        exits=[{"id": "sl", "type": "fixed_pts_sl", "pts": 10}],
    )
    assert "e1" in src and "f1" in src and "buy" in src and "sl" in src
    assert "filter break_above" in src
    assert "entry buy_at_next_open" in src
    assert "exit fixed_pts_sl" in src
