"""
Tests for :mod:`engine.core.data_view` and the engine ↔ DataView wiring.

These pin down the **Zero-Future Rule**: a strategy that calls
``ctx.data.close`` on bar ``k`` must never see bars ``> k``. We also
assert that the slices are read-only views, not copies, so a buggy
strategy can't mutate engine state by writing into ``ctx.data.close``.
"""

from __future__ import annotations

from typing import Dict

import numpy as np
import pandas as pd
import pytest

from engine.strategy.context import Strategy
from engine.core.backtest_core import BacktestCore
from engine.core.data_view import DataView
from engine.execution.execution import ExecutionModel
from engine.models.models import AssetConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _three_bar_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "time": pd.date_range("2024-01-02 09:00", periods=3, freq="1min"),
            "open": [100.0, 101.0, 102.0],
            "high": [101.0, 102.0, 103.0],
            "low": [99.0, 100.0, 101.0],
            "close": [101.0, 102.0, 103.0],
            "volume": [1000, 1000, 1000],
        }
    )


def _build_engine(df: pd.DataFrame) -> BacktestCore:
    eng = BacktestCore()
    eng.asset_config = AssetConfig(
        name="DE40",
        point_value=1.0,
        tick_size=0.1,
        buffer=0.0,
        max_lots_per_100k=12,
        currency="EUR",
        enforce_trading_hours=False,
        close_on_weekend=False,
    )
    eng.execution_model = ExecutionModel(profile="Optimistic")
    eng.execution_model.spread_override = 0.0
    eng.execution_model.commission_override = 0.0
    eng.execution_model.slippage_override = 0.0
    eng.load_data(df)
    return eng


class _Recorder(Strategy):
    """Strategy that records the slice length seen at each step."""

    def __init__(self) -> None:
        self.lengths: list[int] = []
        self.last_close: float = float("nan")

    def on_candle(self, ctx) -> None:  # type: ignore[override]
        self.lengths.append(len(ctx.data))
        if len(ctx.data) > 0:
            self.last_close = float(ctx.data.close[-1])


# ---------------------------------------------------------------------------
# Plain DataView unit tests
# ---------------------------------------------------------------------------
def test_data_view_empty_before_first_step():
    arrays = {
        "close": np.array([1.0, 2.0, 3.0], dtype=np.float64),
        "open": np.array([1.0, 2.0, 3.0], dtype=np.float64),
        "high": np.array([1.0, 2.0, 3.0], dtype=np.float64),
        "low": np.array([1.0, 2.0, 3.0], dtype=np.float64),
        "volume": np.zeros(3, dtype=np.float64),
        "time": np.zeros(3, dtype="datetime64[ns]"),
    }
    idx = -1
    view = DataView(arrays, lambda: idx)
    assert len(view) == 0
    assert view.close.size == 0


def test_data_view_grows_with_index():
    arrays = {
        "close": np.array([10.0, 20.0, 30.0, 40.0], dtype=np.float64),
        "open": np.zeros(4, dtype=np.float64),
        "high": np.zeros(4, dtype=np.float64),
        "low": np.zeros(4, dtype=np.float64),
        "volume": np.zeros(4, dtype=np.float64),
        "time": np.zeros(4, dtype="datetime64[ns]"),
    }
    idx = 0
    view = DataView(arrays, lambda: idx)
    for k in range(4):
        idx = k
        assert len(view) == k + 1
        assert view.close[-1] == pytest.approx(arrays["close"][k])


def test_data_view_slices_are_read_only():
    arrays = {
        "close": np.array([1.0, 2.0, 3.0], dtype=np.float64),
        "open": np.array([1.0, 2.0, 3.0], dtype=np.float64),
        "high": np.array([1.0, 2.0, 3.0], dtype=np.float64),
        "low": np.array([1.0, 2.0, 3.0], dtype=np.float64),
        "volume": np.zeros(3, dtype=np.float64),
        "time": np.zeros(3, dtype="datetime64[ns]"),
    }
    view = DataView(arrays, lambda: 2)
    closes = view.close
    assert closes.flags.writeable is False
    with pytest.raises(ValueError):
        closes[0] = 999.0


def test_data_view_candle_at_returns_dict_with_python_floats():
    arrays = {
        "close": np.array([10.0, 20.0, 30.0], dtype=np.float64),
        "open": np.array([1.0, 2.0, 3.0], dtype=np.float64),
        "high": np.array([1.0, 2.0, 3.0], dtype=np.float64),
        "low": np.array([1.0, 2.0, 3.0], dtype=np.float64),
        "volume": np.zeros(3, dtype=np.float64),
        "time": pd.date_range("2024-01-02 09:00", periods=3, freq="1min").to_numpy(),
    }
    view = DataView(arrays, lambda: 1)
    candle = view.candle_at(1)
    assert candle["close"] == pytest.approx(20.0)
    assert isinstance(candle["close"], float)
    assert candle["time"] == arrays["time"][1]


# ---------------------------------------------------------------------------
# Engine ↔ DataView integration
# ---------------------------------------------------------------------------
def test_ctx_data_zero_future_invariant():
    """At step k, ``ctx.data.close`` must end exactly at the current bar."""
    df = _three_bar_df()
    eng = _build_engine(df)
    rec = _Recorder()
    eng.load_strategy(rec)
    for _ in range(len(df)):
        eng.step()
    # After three steps the recorder must have seen lengths 1, 2, 3 and
    # the last close it observed must equal the third bar's close.
    assert rec.lengths == [1, 2, 3]
    assert rec.last_close == pytest.approx(103.0)


def test_data_view_survives_set_date_range():
    """Filtering the DataFrame by date must rebind the underlying arrays
    without invalidating the ``ctx.data`` reference held by the
    strategy."""
    df = _three_bar_df()
    eng = _build_engine(df)
    eng.load_strategy(_Recorder())
    view = eng.data
    assert len(view) == 0  # nothing stepped yet
    eng.set_date_range(
        pd.Timestamp("2024-01-02 09:01"),
        pd.Timestamp("2024-01-02 09:02"),
    )
    eng.reset()
    eng.step()
    eng.step()
    assert len(view) == 2
    # The slice should start from the post-filter bar.
    assert float(view.close[0]) == pytest.approx(102.0)


def test_engine_total_bars_matches_dataframe_length():
    df = _three_bar_df()
    eng = _build_engine(df)
    assert eng.total_bars == 3
    assert len(eng._candle_list) == 3
    candle = eng._candle_list[1]
    assert candle["close"] == pytest.approx(102.0)
    # And the lazy list is iterable (slow path but supported).
    closes = [c["close"] for c in eng._candle_list]
    assert closes == [101.0, 102.0, 103.0]


def test_engine_load_data_does_not_call_to_dict_on_full_frame():
    """Sanity check the perf goal: ``df.to_dict('records')`` is never
    called for the full dataset during ``load_data``. We monkey-patch
    pandas' DataFrame.to_dict to count invocations and assert it was
    only triggered by per-bar accesses (zero, since we use NumPy)."""
    df = _three_bar_df()
    counter = {"calls": 0}
    original = pd.DataFrame.to_dict

    def counting(self, *args, **kwargs):
        counter["calls"] += 1
        return original(self, *args, **kwargs)

    pd.DataFrame.to_dict = counting  # type: ignore[method-assign]
    try:
        _build_engine(df)
    finally:
        pd.DataFrame.to_dict = original  # type: ignore[method-assign]
    assert counter["calls"] == 0


# ---------------------------------------------------------------------------
# DataView surface coverage — properties + error / edge paths
# ---------------------------------------------------------------------------
def _full_arrays(n: int = 3) -> Dict[str, np.ndarray]:
    return {
        "open": np.arange(0.0, n, dtype=np.float64) + 1.0,
        "high": np.arange(0.0, n, dtype=np.float64) + 2.0,
        "low": np.arange(0.0, n, dtype=np.float64) + 0.5,
        "close": np.arange(0.0, n, dtype=np.float64) + 1.5,
        "volume": np.full(n, 100.0, dtype=np.float64),
        "time": pd.date_range("2024-01-02 09:00", periods=n, freq="1min").to_numpy(),
    }


def test_data_view_each_column_property_returns_correct_slice():
    arrays = _full_arrays(3)
    view = DataView(arrays, lambda: 2)
    for name in ("open", "high", "low", "close", "volume", "time"):
        slice_ = getattr(view, name)
        assert slice_.shape == (3,)
        # And content matches.
        assert (slice_ == arrays[name]).all()


def test_data_view_unknown_column_raises_attributeerror():
    view = DataView({"close": np.zeros(3, dtype=np.float64)}, lambda: 0)
    # No `oops` column → _slice raises AttributeError.
    with pytest.raises(AttributeError, match="DataView has no column"):
        view._slice("oops")


def test_data_view_current_idx_property_tracks_provider():
    idx = -1
    view = DataView(_full_arrays(5), lambda: idx)
    assert view.current_idx == -1
    idx = 3
    assert view.current_idx == 3
    assert len(view) == 4


def test_data_view_candle_at_out_of_bounds():
    view = DataView(_full_arrays(3), lambda: 2)
    with pytest.raises(IndexError):
        view.candle_at(-1)
    with pytest.raises(IndexError):
        view.candle_at(3)


def test_data_view_latest_returns_none_before_first_step():
    arrays = _full_arrays(3)
    idx = -1
    view = DataView(arrays, lambda: idx)
    assert view.latest() is None
    idx = 0
    candle = view.latest()
    assert candle is not None
    assert candle["close"] == pytest.approx(arrays["close"][0])


# ---------------------------------------------------------------------------
# Engine edge cases
# ---------------------------------------------------------------------------
def test_engine_step_on_empty_dataframe_is_safe_noop():
    """``load_data`` with a zero-row frame must not crash and ``step``
    should immediately fire ``on_simulation_finished`` without invoking
    the strategy."""
    df = pd.DataFrame(
        {
            "time": pd.to_datetime([]),
            "open": [],
            "high": [],
            "low": [],
            "close": [],
            "volume": [],
        }
    )
    eng = _build_engine(df)
    rec = _Recorder()
    eng.load_strategy(rec)
    finished = []
    eng.on_simulation_finished.append(lambda: finished.append(True))
    eng.step()
    assert eng.total_bars == 0
    assert rec.lengths == []
    assert finished == [True]


def test_engine_zero_future_when_step_n_used():
    """``step_n`` shares the same per-step pipeline; the recorded
    lengths must therefore still match ``[1, 2, 3]`` for a 3-bar frame."""
    df = _three_bar_df()
    eng = _build_engine(df)
    rec = _Recorder()
    eng.load_strategy(rec)
    eng.step_n(10)
    assert rec.lengths == [1, 2, 3]


def test_engine_volume_column_is_zero_when_dataframe_lacks_it():
    """Some external feeds drop the volume column. ``_refresh_numpy_columns``
    should fall back to a zero-filled array instead of crashing."""
    df = _three_bar_df().drop(columns=["volume"])
    eng = _build_engine(df)
    assert eng.total_bars == 3
    # Volume defaults to zeros and the candle dict still has the key.
    assert (eng._volume == 0.0).all()
    assert eng.candle_at(1)["volume"] == pytest.approx(0.0)


def test_engine_warns_on_nan_ohlc(caplog):
    """``load_data`` must emit a single warning when the dataframe
    contains NaN OHLC values — strategies relying on cumsum-based
    indicators would otherwise be silently poisoned forever."""
    df = _three_bar_df()
    df.loc[1, "close"] = float("nan")
    caplog.clear()
    with caplog.at_level("WARNING"):
        _build_engine(df)
    nan_warnings = [r for r in caplog.records if "NaN" in r.getMessage()]
    assert nan_warnings, "expected at least one NaN-warning record"


def test_engine_warns_on_inverted_high_low(caplog):
    """A high<low row indicates corrupt input. Warn the user rather
    than silently letting it through."""
    df = _three_bar_df()
    df.loc[1, "high"] = 50.0  # below the row's low
    df.loc[1, "low"] = 200.0
    caplog.clear()
    with caplog.at_level("WARNING"):
        _build_engine(df)
    msgs = " ".join(r.getMessage() for r in caplog.records)
    assert "high < low" in msgs
