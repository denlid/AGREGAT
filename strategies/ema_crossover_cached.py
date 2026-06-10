"""EMA crossover strategy used to exercise the feature-cache contract.

This is intentionally small and deterministic: indicator periods affect the
built feature bundle, while SL/TP only affect cheap signal materialization.
"""

from __future__ import annotations

from typing import Any, Mapping

import numpy as np
import pandas as pd

from engine.strategy.context import Param, Strategy


class EMACrossoverFeatureCached(Strategy):
    """Vectorized EMA crossover with reusable indicator features."""

    PARAMS = [
        Param("fast_ema", "Fast EMA", "int", 12, min=1, max=500, step=1),
        Param("slow_ema", "Slow EMA", "int", 26, min=2, max=1000, step=1),
        Param("sl_pts", "Stop Loss Points", "float", 30.0, min=0.01, max=10_000.0, step=1.0),
        Param("tp_pts", "Take Profit Points", "float", 60.0, min=0.01, max=10_000.0, step=1.0),
    ]

    @staticmethod
    def _close(df: pd.DataFrame) -> np.ndarray:
        col = "close" if "close" in df.columns else "Close"
        return df[col].to_numpy(dtype=np.float64, copy=False)

    def feature_params(self) -> tuple[int, int]:
        return (int(self.params["fast_ema"]), int(self.params["slow_ema"]))

    def build_vectorized_features(self, df: pd.DataFrame, prepared: Any = None) -> dict[str, Any]:
        close = self._close(df)
        fast_period, slow_period = self.feature_params()
        fast = pd.Series(close).ewm(span=fast_period, adjust=False).mean().to_numpy()
        slow = pd.Series(close).ewm(span=slow_period, adjust=False).mean().to_numpy()
        return {
            "index": df.index,
            "n": len(df),
            "fast_ema": fast,
            "slow_ema": slow,
        }

    def vectorized_signals_from_features(
        self,
        features: Mapping[str, Any],
        params: Mapping[str, Any] | None = None,
    ) -> pd.DataFrame:
        p = self.params if params is None else params
        n = int(features["n"])
        fast = np.asarray(features["fast_ema"], dtype=np.float64)
        slow = np.asarray(features["slow_ema"], dtype=np.float64)

        diff = fast - slow
        signal = np.zeros(n, dtype=np.int8)
        if n > 1:
            prev = diff[:-1]
            curr = diff[1:]
            signal[1:][(prev <= 0.0) & (curr > 0.0)] = 1
            signal[1:][(prev >= 0.0) & (curr < 0.0)] = -1

        return pd.DataFrame(
            {
                "signal": signal,
                "sl_points": np.full(n, float(p["sl_pts"]), dtype=np.float64),
                "tp_points": np.full(n, float(p["tp_pts"]), dtype=np.float64),
            },
            index=features["index"],
        )

    def vectorized_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        features = self.build_vectorized_features(df, prepared=None)
        return self.vectorized_signals_from_features(features, params=self.params)


__all__ = ["EMACrossoverFeatureCached"]
