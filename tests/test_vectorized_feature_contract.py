from __future__ import annotations

import numpy as np
import pandas as pd

from engine.execution.execution import ExecutionModel
from engine.execution.prepared_dataset import prepare_vectorized_dataset
from engine.execution.vectorized_engine import run_vectorized
from engine.models.models import AssetConfig
from engine.strategy.context import Strategy


class _FeatureContractStrategy(Strategy):
    def __init__(self, n: int) -> None:
        super().__init__()
        self._n = n
        self.calls: list[str] = []

    def build_vectorized_features(self, df: pd.DataFrame, prepared=None):
        self.calls.append("build")
        close = prepared.close if prepared is not None else df["Close"].to_numpy(dtype=float)
        return {
            "close": close,
            "index": df.index,
        }

    def vectorized_signals_from_features(self, features, params=None) -> pd.DataFrame:
        self.calls.append("from_features")
        close = features["close"]
        sig = np.zeros(close.shape[0], dtype=int)
        if close.shape[0] > 1:
            sig[0] = 1
        return pd.DataFrame(
            {
                "signal": sig,
                "sl_points": np.full(close.shape[0], 2.0, dtype=float),
                "tp_points": np.full(close.shape[0], 4.0, dtype=float),
            },
            index=features["index"],
        )

    def vectorized_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        self.calls.append("plain")
        raise AssertionError("plain vectorized_signals should not be used when feature contract is implemented")


class _PlainStrategy(Strategy):
    def __init__(self, n: int) -> None:
        super().__init__()
        self._n = n
        self.calls: list[str] = []

    def vectorized_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        self.calls.append("plain")
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


def _df(n: int = 8) -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=n, freq="min")
    return pd.DataFrame(
        {
            "time": idx,
            "Open": np.linspace(100.0, 101.0, n),
            "High": np.linspace(101.0, 102.0, n),
            "Low": np.linspace(99.0, 100.0, n),
            "Close": np.linspace(100.0, 101.0, n),
            "Volume": 1.0,
        }
    )


def test_run_vectorized_uses_feature_contract_when_strategy_overrides_it() -> None:
    df = _df(8)
    asset = _asset()
    em = ExecutionModel("Optimistic")
    prepared = prepare_vectorized_dataset(df, asset=asset, execution_model=em)
    strat = _FeatureContractStrategy(len(df))

    result = run_vectorized(
        df,
        strat,
        asset=asset,
        execution_model=em,
        prepared=prepared,
        materialize_trades=False,
        summary_only=True,
        include_equity_curve=False,
    )

    assert strat.calls == ["build", "from_features"]
    assert result.perf["used_feature_cache_contract"] is True
    assert result.perf["prepared_reused"] is True
    assert result.summary["n_trades"] >= 0


def test_run_vectorized_falls_back_to_plain_vectorized_signals() -> None:
    df = _df(8)
    asset = _asset()
    em = ExecutionModel("Optimistic")
    strat = _PlainStrategy(len(df))

    result = run_vectorized(
        df,
        strat,
        asset=asset,
        execution_model=em,
        materialize_trades=False,
        summary_only=True,
        include_equity_curve=False,
    )

    assert strat.calls == ["plain"]
    assert result.perf["used_feature_cache_contract"] is False
    assert result.summary["n_trades"] >= 0
