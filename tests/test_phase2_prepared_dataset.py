from __future__ import annotations

import numpy as np
import pandas as pd

from engine.execution.execution import ExecutionModel
from engine.execution.prepared_dataset import PreparedDataset, prepare_vectorized_dataset
from engine.execution.vectorized_engine import run_vectorized
from engine.models.models import AssetConfig
from engine.strategy.context import Strategy


class _VecOnce(Strategy):
    def __init__(self, n: int) -> None:
        self._n = n

    def vectorized_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        sig = np.zeros(self._n, dtype=int)
        if self._n > 1:
            sig[0] = 1
        return pd.DataFrame(
            {
                "signal": sig,
                "sl_points": np.full(self._n, 2.0, dtype=float),
                "tp_points": np.full(self._n, 4.0, dtype=float),
            },
            index=df.index,
        )


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
        close_on_weekend=False,
        enforce_trading_hours=False,
    )


def _df(n: int = 4) -> pd.DataFrame:
    rows = []
    for i in range(n):
        rows.append((f"2024-01-02 10:0{i}", 100.0, 101.0, 99.0, 100.0 + i))
    df = pd.DataFrame(rows, columns=["time", "Open", "High", "Low", "Close"])
    df["time"] = pd.to_datetime(df["time"])
    df["Volume"] = 1.0
    return df


def test_prepare_vectorized_dataset_extracts_arrays_and_masks() -> None:
    df = _df(4)
    prep = prepare_vectorized_dataset(
        df,
        asset=_asset(),
        execution_model=ExecutionModel("Optimistic"),
    )
    assert prep.n == len(df)
    assert prep.open.shape == (len(df),)
    assert prep.high.shape == (len(df),)
    assert prep.low.shape == (len(df),)
    assert prep.close.shape == (len(df),)
    assert prep.is_trading_hours.shape == (len(df),)
    assert prep.is_eod.shape == (len(df),)
    assert prep.is_weekend.shape == (len(df),)
    assert isinstance(prep, PreparedDataset)
    assert prep.dataset_key
    assert prep.memory_bytes > 0
    assert prep.memory_mb > 0
    assert prep.meta["dataset_key"] == prep.dataset_key
    assert prep.meta["memory_bytes"] == prep.memory_bytes


def test_run_vectorized_accepts_prepared_dataset() -> None:
    df = _df(4)
    asset = _asset()
    exec_model = ExecutionModel("Optimistic")
    prep = prepare_vectorized_dataset(df, asset=asset, execution_model=exec_model)

    res_plain = run_vectorized(df, _VecOnce(len(df)), asset=asset, execution_model=exec_model)
    res_prepared = run_vectorized(
        df,
        _VecOnce(len(df)),
        asset=asset,
        execution_model=exec_model,
        prepared=prep,
    )

    assert res_plain.summary == res_prepared.summary
    assert res_plain.perf["trade_count"] == res_prepared.perf["trade_count"]
    assert len(res_plain.trades) == len(res_prepared.trades)


def test_run_vectorized_can_skip_trade_materialization() -> None:
    df = _df(4)
    asset = _asset()
    exec_model = ExecutionModel("Optimistic")
    prep = prepare_vectorized_dataset(df, asset=asset, execution_model=exec_model)

    res = run_vectorized(
        df,
        _VecOnce(len(df)),
        asset=asset,
        execution_model=exec_model,
        prepared=prep,
        materialize_trades=False,
    )

    assert res.trades == []
    assert res.summary["n_trades"] >= 0
    assert res.perf["trade_count"] == res.summary["n_trades"]


def test_prepare_vectorized_dataset_key_is_stable_for_same_input() -> None:
    df = _df(4)
    asset = _asset()
    exec_model = ExecutionModel("Optimistic")
    prep_a = prepare_vectorized_dataset(df, asset=asset, execution_model=exec_model)
    prep_b = prepare_vectorized_dataset(df.copy(), asset=asset, execution_model=exec_model)

    assert prep_a.dataset_key == prep_b.dataset_key
    assert prep_a.memory_bytes == prep_b.memory_bytes


def test_prepare_vectorized_dataset_key_changes_when_prices_change() -> None:
    df = _df(4)
    asset = _asset()
    exec_model = ExecutionModel("Optimistic")
    prep_a = prepare_vectorized_dataset(df, asset=asset, execution_model=exec_model)
    df2 = df.copy()
    df2.loc[df2.index[0], "Close"] = 999.0
    prep_b = prepare_vectorized_dataset(df2, asset=asset, execution_model=exec_model)

    assert prep_a.dataset_key != prep_b.dataset_key


def test_prepare_vectorized_dataset_empty_has_empty_key_and_zero_memory() -> None:
    df = pd.DataFrame(columns=["time", "Open", "High", "Low", "Close"])
    prep = prepare_vectorized_dataset(df, asset=_asset(), execution_model=ExecutionModel("Optimistic"))

    assert prep.dataset_key == "empty"
    assert prep.memory_bytes == 0
    assert prep.memory_mb == 0.0
    assert prep.memory_meta()["bars"] == 0
