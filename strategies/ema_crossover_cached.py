"""EMA Crossover strategy with feature-cache contract (Roadmap v2 / S1).

This is the pilot strategy for Sprint 1 — it demonstrates how to split heavy
indicator computation (EMA calculation) from cheap signal logic (crossover
detection) so optimizer sweeps can reuse the expensive features across param
combos that only vary SL/TP thresholds.

The key insight: when sweeping `sl_pts` and `tp_pts`, the EMA arrays stay
constant — recomputing them on every combo is pure waste. The feature-cache
contract lets the engine call `build_vectorized_features` once per dataset,
then `vectorized_signals_from_features` for each param combo, skipping the
redundant EMA recalculation.

Expected gain: 1.5–2x on sweeps where indicator params (fast_ema, slow_ema)
are fixed and only execution params (sl_pts, tp_pts) vary.
"""

from __future__ import annotations

from typing import Any, Optional

import numpy as np
import pandas as pd

from engine.strategy.context import Param, Strategy


class EMACrossoverFeatureCached(Strategy):
    """EMA crossover with feature-cache: heavy EMAs computed once, signals cheap.

    Params
    ------
    fast_ema : int
        Fast EMA period (default 12). When this varies across a sweep, the
        feature cache cannot be reused — each combo rebuilds features.
    slow_ema : int
        Slow EMA period (default 26). Same caveat as fast_ema.
    sl_pts : float
        Stop-loss distance in points (default 50). Varying this does NOT
        invalidate the feature cache — signals recompute is cheap.
    tp_pts : float
        Take-profit distance in points (default 100). Same as sl_pts.
    """

    PARAMS = [
        Param(name="fast_ema", label="Fast EMA", kind="int", default=12, min=2, max=100),
        Param(name="slow_ema", label="Slow EMA", kind="int", default=26, min=5, max=200),
        Param(name="sl_pts", label="SL Points", kind="float", default=50.0, min=10.0, max=500.0),
        Param(name="tp_pts", label="TP Points", kind="float", default=100.0, min=20.0, max=1000.0),
    ]

    def build_vectorized_features(self, df: Any, prepared: Any = None) -> dict:
        """Compute heavy EMA arrays once — the reuse boundary for sweeps.

        This is called ONCE per dataset (or once per unique (fast_ema, slow_ema)
        combo if those params vary). The returned dict is passed to
        `vectorized_signals_from_features` on every param combo, so the EMAs
        are not recomputed when only sl_pts/tp_pts change.
        """
        n = len(df)
        col = "close" if "close" in df.columns else "Close"
        close = df[col].to_numpy(dtype=float)

        fast_period = int(self.params["fast_ema"])
        slow_period = int(self.params["slow_ema"])

        # Exponential moving average via pandas (production-grade, handles NaN).
        fast_ema = pd.Series(close).ewm(span=fast_period, adjust=False).mean().to_numpy()
        slow_ema = pd.Series(close).ewm(span=slow_period, adjust=False).mean().to_numpy()

        return {
            "n": n,
            "close": close,
            "fast_ema": fast_ema,
            "slow_ema": slow_ema,
            "index": df.index,
        }

    def vectorized_signals_from_features(self, features: dict, params: Optional[dict] = None) -> pd.DataFrame:
        """Generate signals from pre-built features — the cheap path.

        This is called on EVERY param combo. When only sl_pts/tp_pts vary, the
        features dict (containing the heavy EMA arrays) is reused, and this
        method just detects crossovers and fills the signal frame — O(N) scan,
        no indicator recalculation.
        """
        n = features["n"]
        close = features["close"]
        fast = features["fast_ema"]
        slow = features["slow_ema"]
        index = features["index"]

        # Crossover detection: fast crosses above slow → +1, below → -1.
        sig = np.zeros(n, dtype=int)
        # Avoid index 0 (no prior bar to compare).
        for i in range(1, n):
            if fast[i - 1] <= slow[i - 1] and fast[i] > slow[i]:
                sig[i] = +1  # bullish crossover
            elif fast[i - 1] >= slow[i - 1] and fast[i] < slow[i]:
                sig[i] = -1  # bearish crossover

        # SL/TP from params (these are the swept variables in the target workload).
        sl_pts = float(self.params["sl_pts"])
        tp_pts = float(self.params["tp_pts"])

        return pd.DataFrame(
            {
                "signal": sig,
                "sl_points": np.full(n, sl_pts, dtype=float),
                "tp_points": np.full(n, tp_pts, dtype=float),
            },
            index=index,
        )

    def vectorized_signals(self, df: Any) -> pd.DataFrame:
        """Fallback monolithic path for non-optimizer runs or old callers.

        When the engine detects that both feature-cache methods are overridden,
        it uses them; otherwise it falls back to this. We implement it by
        chaining the two methods so the strategy works in both modes.
        """
        features = self.build_vectorized_features(df, prepared=None)
        return self.vectorized_signals_from_features(features, params=self.params)
