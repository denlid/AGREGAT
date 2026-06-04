"""Tests for :mod:`engine.vectorized_engine` — Phase 2 fast-quant engine.

These tests pin down the contract of ``run_vectorized``:

* The engine refuses strategies without ``vectorized_signals``.
* SL / TP triggers fire on the same bar as the move.
* Long fills clamp to the Bid side, short SL/TP fire on the Ask side
  (Phase 1 spread-correctness rule).
* Costs (commission per lot) flow through into the per-trade ``pnl``.
* End-of-data force-closes any open position with the ``EOD`` reason.

The fixture builds tiny synthetic OHLCV frames so the assertions can be
read off directly without reproducing the full simulation by hand.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from engine.strategy.context import Strategy, has_vectorized_signals
from engine.execution.execution import ExecutionModel
from engine.models.models import AssetConfig
from engine.execution.prepared_dataset import prepare_vectorized_dataset
from engine.execution.vectorized_engine import run_vectorized


def _df(rows):
    df = pd.DataFrame(rows, columns=["time", "Open", "High", "Low", "Close"])
    df["time"] = pd.to_datetime(df["time"])
    df["Volume"] = 1.0
    return df


def _asset(point_value=1.0, tick=1.0, spread=0.0, commission=0.0):
    return AssetConfig(
        name="TEST",
        point_value=point_value,
        tick_size=tick,
        buffer=0.0,
        max_lots_per_100k=10,
        currency="USD",
        min_lot=0.01,
        lot_step=0.01,
        spread_points=spread,
        commission_per_lot=commission,
    )


# ---------------------------------------------------------------------- helpers
class FixedLongOnce(Strategy):
    """Emit a single +1 long signal on bar `at`, then sit still."""

    def __init__(self, at: int, sl_pts: float, tp_pts: float, n_bars: int):
        self._at = at
        self._sl = sl_pts
        self._tp = tp_pts
        self._n = n_bars

    def vectorized_signals(self, df):
        sig = np.zeros(self._n, dtype=int)
        if self._n:
            sig[self._at] = +1
        return pd.DataFrame(
            {
                "signal": sig,
                "sl_points": np.full(self._n, self._sl, dtype=float),
                "tp_points": np.full(self._n, self._tp, dtype=float),
            },
            index=df.index,
        )


class FixedShortOnce(Strategy):
    def __init__(self, at: int, sl_pts: float, tp_pts: float, n_bars: int):
        self._at = at
        self._sl = sl_pts
        self._tp = tp_pts
        self._n = n_bars

    def vectorized_signals(self, df):
        sig = np.zeros(self._n, dtype=int)
        if self._n:
            sig[self._at] = -1
        return pd.DataFrame(
            {
                "signal": sig,
                "sl_points": np.full(self._n, self._sl, dtype=float),
                "tp_points": np.full(self._n, self._tp, dtype=float),
            },
            index=df.index,
        )


class VisualOnly(Strategy):
    """Strategy implementing only the on_candle path. Should NOT report
    vectorized capability."""

    def on_candle(self, ctx):  # pragma: no cover — never invoked here
        pass


# ---------------------------------------------------------------------- tests
def test_capability_detection_distinguishes_visual_only_from_vectorized():
    assert not has_vectorized_signals(VisualOnly())
    assert has_vectorized_signals(FixedLongOnce(0, 1.0, 0.0, 1))


def test_run_refuses_strategy_without_vectorized_signals():
    df = _df([("2024-01-02 10:00", 100, 101, 99, 100)])

    class NoSignals:
        pass

    with pytest.raises(AttributeError, match="vectorized_signals"):
        run_vectorized(
            df, NoSignals(), asset=_asset(), execution_model=ExecutionModel("Optimistic")
        )


def test_long_tp_hit_same_bar_optimistic_profile():
    rows = [
        ("2024-01-02 10:00", 100, 100, 100, 100),
        ("2024-01-02 10:01", 100, 105, 100, 102),
        ("2024-01-02 10:02", 102, 103, 101, 101),
    ]
    df = _df(rows)
    res = run_vectorized(
        df,
        FixedLongOnce(at=0, sl_pts=2.0, tp_pts=4.0, n_bars=len(rows)),
        asset=_asset(),
        execution_model=ExecutionModel("Optimistic"),
        initial_equity=100_000.0,
        risk_pct=1.0,
    )
    assert len(res.trades) == 1
    t = res.trades[0]
    assert t.direction == "long"
    assert t.exit_reason == "TP"
    assert t.entry_price == pytest.approx(100.0)
    assert t.exit_price == pytest.approx(104.0)
    assert t.pnl == pytest.approx(4.0 * t.volume)


def test_long_sl_takes_precedence_on_same_bar_collision():
    rows = [
        ("2024-01-02 10:00", 100, 100, 100, 100),
        ("2024-01-02 10:01", 100, 105, 97, 102),
        ("2024-01-02 10:02", 102, 103, 101, 101),
    ]
    df = _df(rows)
    res = run_vectorized(
        df,
        FixedLongOnce(at=0, sl_pts=2.0, tp_pts=4.0, n_bars=len(rows)),
        asset=_asset(),
        execution_model=ExecutionModel("Optimistic"),
    )
    assert len(res.trades) == 1
    assert res.trades[0].exit_reason == "SL"
    assert res.trades[0].exit_price == pytest.approx(98.0)


def test_short_sl_uses_ask_side_with_spread():
    rows = [
        ("2024-01-02 10:00", 100, 100, 100, 100),
        ("2024-01-02 10:01", 100, 101, 100, 100),
        ("2024-01-02 10:02", 100, 100, 100, 100),
    ]
    df = _df(rows)
    res = run_vectorized(
        df,
        FixedShortOnce(at=0, sl_pts=2.0, tp_pts=0.0, n_bars=len(rows)),
        asset=_asset(spread=2.0),
        execution_model=ExecutionModel("Realistic"),
    )
    assert len(res.trades) == 1
    t = res.trades[0]
    assert t.direction == "short"
    assert t.exit_reason == "SL"


def test_eod_close_when_position_open_at_last_bar():
    rows = [
        ("2024-01-02 10:00", 100, 100, 100, 100),
        ("2024-01-02 10:01", 100, 101, 99, 100),
        ("2024-01-02 10:02", 100, 102, 100, 102),
    ]
    df = _df(rows)
    res = run_vectorized(
        df,
        FixedLongOnce(at=0, sl_pts=10.0, tp_pts=10.0, n_bars=len(rows)),
        asset=_asset(),
        execution_model=ExecutionModel("Optimistic"),
    )
    assert len(res.trades) == 1
    t = res.trades[0]
    assert t.exit_reason == "EOD"
    assert t.exit_price == pytest.approx(102.0)


def test_commission_reduces_pnl_realistic_profile():
    rows = [
        ("2024-01-02 10:00", 100, 100, 100, 100),
        ("2024-01-02 10:01", 100, 105, 100, 102),
        ("2024-01-02 10:02", 102, 103, 101, 101),
    ]
    df = _df(rows)
    asset = _asset(commission=5.0)
    res = run_vectorized(
        df,
        FixedLongOnce(at=0, sl_pts=2.0, tp_pts=4.0, n_bars=len(rows)),
        asset=asset,
        execution_model=ExecutionModel("Realistic", commission_per_lot=5.0),
    )
    assert len(res.trades) == 1
    t = res.trades[0]
    assert t.commission == pytest.approx(5.0 * t.volume)
    assert t.pnl == pytest.approx(4.0 * t.volume - 5.0 * t.volume)


def test_returns_empty_result_for_empty_dataframe():
    res = run_vectorized(
        pd.DataFrame(columns=["time", "Open", "High", "Low", "Close"]),
        FixedLongOnce(at=0, sl_pts=2.0, tp_pts=4.0, n_bars=0),
        asset=_asset(),
        execution_model=ExecutionModel("Optimistic"),
        initial_equity=42_000.0,
    )
    assert res.trades == []
    assert res.equity_curve.tolist() == [42_000.0]
    assert res.peak_drawdown_pct == 0.0
    assert res.perf["prepared_reused"] is False
    assert res.perf["materialize_trades"] is False
    assert res.perf["signal_memory_mb"] == 0.0
    assert res.perf["strategy_name"] == "FixedLongOnce"


def test_signal_after_position_is_open_is_ignored():
    n = 4
    rows = [
        ("2024-01-02 10:00", 100, 100, 100, 100),
        ("2024-01-02 10:01", 100, 100, 100, 100),
        ("2024-01-02 10:02", 100, 105, 100, 102),
        ("2024-01-02 10:03", 100, 100, 100, 100),
    ]
    df = _df(rows)

    class TwoLongs(Strategy):
        def vectorized_signals(self, df_):
            sig = np.zeros(n, dtype=int)
            sig[0] = +1
            sig[1] = +1
            return pd.DataFrame(
                {
                    "signal": sig,
                    "sl_points": np.full(n, 2.0),
                    "tp_points": np.full(n, 4.0),
                },
                index=df_.index,
            )

    res = run_vectorized(
        df,
        TwoLongs(),
        asset=_asset(),
        execution_model=ExecutionModel("Optimistic"),
    )
    assert len(res.trades) == 1


def test_summary_only_disables_trade_materialization_and_can_skip_equity_curve():
    rows = [
        ("2024-01-02 10:00", 100, 100, 100, 100),
        ("2024-01-02 10:01", 100, 105, 100, 102),
        ("2024-01-02 10:02", 102, 103, 101, 101),
    ]
    df = _df(rows)
    res = run_vectorized(
        df,
        FixedLongOnce(at=0, sl_pts=2.0, tp_pts=4.0, n_bars=len(rows)),
        asset=_asset(),
        execution_model=ExecutionModel("Optimistic"),
        summary_only=True,
        compact_result=True,
        include_equity_curve=False,
    )
    assert res.trades == []
    assert res.summary["n_trades"] == 1
    assert res.equity_curve.tolist() == [100_000.0]
    assert res.perf["summary_only"] is True
    assert res.perf["materialize_trades"] is False
    assert res.perf["compact_result"] is True
    assert res.perf["include_equity_curve"] is False
    assert res.perf["signal_memory_mb"] >= 0.0


def test_prepared_dataset_reuse_is_reported_in_perf():
    rows = [
        ("2024-01-02 10:00", 100, 100, 100, 100),
        ("2024-01-02 10:01", 100, 105, 100, 102),
        ("2024-01-02 10:02", 102, 103, 101, 101),
    ]
    df = _df(rows)
    asset = _asset()
    em = ExecutionModel("Optimistic")
    prepared = prepare_vectorized_dataset(df, asset=asset, execution_model=em)
    res = run_vectorized(
        df,
        FixedLongOnce(at=0, sl_pts=2.0, tp_pts=4.0, n_bars=len(rows)),
        asset=asset,
        execution_model=em,
        prepared=prepared,
        materialize_trades=False,
    )
    assert res.perf["prepared_reused"] is True
    assert res.perf["materialize_trades"] is False
    assert res.perf["strategy_name"] == "FixedLongOnce"
