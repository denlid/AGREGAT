from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pandas as pd
import pytest
from PySide6.QtWidgets import QApplication

from engine.execution.execution import ExecutionModel
from engine.execution.vectorized_engine import run_vectorized
from engine.models.models import AssetConfig
from engine.strategy.context import Strategy
from gui.chart_tab.items import CandlestickItem


class _VecOnce(Strategy):
    def __init__(self, n: int) -> None:
        self._n = n

    def vectorized_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        sig = [0] * self._n
        if self._n > 1:
            sig[0] = 1
        return pd.DataFrame(
            {
                "signal": sig,
                "sl_points": [2.0] * self._n,
                "tp_points": [4.0] * self._n,
            },
            index=df.index,
        )


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _asset() -> AssetConfig:
    return AssetConfig(
        name="TEST",
        point_value=1.0,
        tick_size=1.0,
        buffer=0.0,
        max_lots_per_100k=10,
        currency="USD",
        min_lot=0.01,
        lot_step=0.01,
        spread_points=0.0,
        commission_per_lot=0.0,
    )


def _df(n: int = 4) -> pd.DataFrame:
    rows = []
    for i in range(n):
        rows.append((f"2024-01-02 10:0{i}", 100.0, 101.0, 99.0, 100.0 + i))
    df = pd.DataFrame(rows, columns=["time", "Open", "High", "Low", "Close"])
    df["time"] = pd.to_datetime(df["time"])
    df["Volume"] = 1.0
    return df


def test_vectorized_result_exposes_phase0_perf_metrics() -> None:
    df = _df(4)
    res = run_vectorized(
        df,
        _VecOnce(len(df)),
        asset=_asset(),
        execution_model=ExecutionModel("Optimistic"),
    )
    perf = res.perf
    expected = {
        "bars",
        "signals_gen_ms",
        "signals_validate_ms",
        "numpy_extract_ms",
        "mask_build_ms",
        "numba_loop_ms",
        "python_loop_ms",
        "trade_materialize_ms",
        "summary_ms",
        "total_ms",
        "used_numba",
        "trade_count",
    }
    assert expected.issubset(perf.keys())
    assert perf["bars"] == len(df)
    assert perf["trade_count"] == len(res.trades)
    assert perf["total_ms"] >= 0.0


def test_empty_vectorized_result_still_has_perf_metrics() -> None:
    df = pd.DataFrame(columns=["time", "Open", "High", "Low", "Close"])
    res = run_vectorized(
        df,
        _VecOnce(0),
        asset=_asset(),
        execution_model=ExecutionModel("Optimistic"),
    )
    assert res.perf["bars"] == 0
    assert res.perf["trade_count"] == 0
    assert res.perf["total_ms"] == 0.0


def test_candlestick_item_perf_stats_contract_contains_phase0_keys(qapp) -> None:
    item = CandlestickItem(max_bars=100)
    raw = [(float(i), 100.0, 101.0, 99.0, 102.0) for i in range(200)]
    item.set_data(raw)
    stats = item._paint_stats
    expected = {
        "draw_lines_calls",
        "draw_rects_calls",
        "bars_drawn",
        "bars_culled",
        "paint_time_ms",
        "gen_time_ms",
    }
    assert expected.issubset(stats.keys())
    assert stats["bars_drawn"] >= 0
    assert stats["bars_culled"] >= 0
