from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd

from engine.execution.execution import ExecutionModel
from engine.models.models import AssetConfig


@dataclass(frozen=True)
class PreparedDataset:
    """Array-first dataset bundle for vectorized runs and optimizer sweeps.

    Phase 2 introduces a reusable preparation boundary between:
    - pandas/DataFrame ingestion
    - session/time mask construction
    - hot-loop execution

    The bundle is immutable and can be shared across multiple strategy runs on
    the same dataset, eliminating repeated pandas->NumPy extraction and mask
    building work.
    """

    n: int
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    times: np.ndarray
    is_trading_hours: np.ndarray
    is_eod: np.ndarray
    is_weekend: np.ndarray
    enforce_trading_hours: bool
    enforce_eod: bool
    weekend_blocking: bool
    point_value: float
    spread: float
    slip: float
    commission: float
    slippage_model_int: int
    slippage_k: float
    meta: Dict[str, Any]

    def shared_arrays(self) -> Dict[str, np.ndarray]:
        """Return the numeric arrays suitable for SharedArrayBundle."""
        return {
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "times": self.times,
            "is_trading_hours": self.is_trading_hours,
            "is_eod": self.is_eod,
            "is_weekend": self.is_weekend,
        }


def prepare_vectorized_dataset(
    df: pd.DataFrame,
    *,
    asset: AssetConfig,
    execution_model: Optional[ExecutionModel] = None,
) -> PreparedDataset:
    """Precompute array/mask inputs reused by vectorized runs.

    This is the Phase-2 preparation boundary for optimizer workloads where the
    same market dataset is executed many times with different strategy params.
    """
    em = execution_model or ExecutionModel()
    n = len(df)

    if n == 0:
        return PreparedDataset(
            n=0,
            open=np.empty(0, dtype=np.float64),
            high=np.empty(0, dtype=np.float64),
            low=np.empty(0, dtype=np.float64),
            close=np.empty(0, dtype=np.float64),
            times=np.empty(0, dtype="datetime64[ns]"),
            is_trading_hours=np.empty(0, dtype=bool),
            is_eod=np.empty(0, dtype=bool),
            is_weekend=np.empty(0, dtype=bool),
            enforce_trading_hours=bool(asset.enforce_trading_hours),
            enforce_eod=bool(asset.enforce_eod_close),
            weekend_blocking=bool(asset.close_on_weekend),
            point_value=float(asset.point_value or 1.0),
            spread=em.spread_price(asset),
            slip=em.slippage_price(asset),
            commission=em._commission(asset),
            slippage_model_int=0,
            slippage_k=float(em.slippage_k),
            meta={"bars": 0},
        )

    high = (
        df["High"].to_numpy(dtype=np.float64)
        if "High" in df.columns
        else df["high"].to_numpy(dtype=np.float64)
    )
    low = (
        df["Low"].to_numpy(dtype=np.float64)
        if "Low" in df.columns
        else df["low"].to_numpy(dtype=np.float64)
    )
    close = (
        df["Close"].to_numpy(dtype=np.float64)
        if "Close" in df.columns
        else df["close"].to_numpy(dtype=np.float64)
    )
    open_ = (
        df["Open"].to_numpy(dtype=np.float64)
        if "Open" in df.columns
        else df["open"].to_numpy(dtype=np.float64)
    )
    if "time" in df.columns:
        times = df["time"].to_numpy()
    else:
        times = df.index.to_numpy()

    enforce_th = bool(asset.enforce_trading_hours)
    enforce_eod = bool(asset.enforce_eod_close)
    weekend_blocking = bool(asset.close_on_weekend)
    try:
        ts_index = pd.DatetimeIndex(pd.to_datetime(times))
        session_tz = getattr(asset, "session_tz", None)
        if session_tz:
            try:
                if ts_index.tz is None:
                    ts_index = ts_index.tz_localize("UTC").tz_convert(session_tz)
                else:
                    ts_index = ts_index.tz_convert(session_tz)
            except Exception:
                pass
        hm_arr = np.asarray(ts_index.hour, dtype=np.int64) * 60 + np.asarray(
            ts_index.minute, dtype=np.int64
        )
        weekday_arr = np.asarray(ts_index.weekday, dtype=np.int64)
    except (ValueError, TypeError):
        hm_arr = np.zeros(n, dtype=np.int64)
        weekday_arr = np.zeros(n, dtype=np.int64)
        enforce_th = False
        weekend_blocking = False

    th_start_min = asset.trading_hours_start.hour * 60 + asset.trading_hours_start.minute
    th_end_min = asset.trading_hours_end.hour * 60 + asset.trading_hours_end.minute
    eod_min = asset.eod_close.hour * 60 + asset.eod_close.minute

    is_weekend_arr = weekday_arr >= 5
    in_hours_mask = (hm_arr >= th_start_min) & (hm_arr < th_end_min)
    if weekend_blocking:
        in_hours_mask &= ~is_weekend_arr
    is_trading_hours_arr = in_hours_mask
    if enforce_eod:
        is_eod_arr = hm_arr >= eod_min
    else:
        is_eod_arr = np.zeros(n, dtype=bool)

    from engine.execution.execution import slippage_model_to_int

    return PreparedDataset(
        n=n,
        open=open_,
        high=high,
        low=low,
        close=close,
        times=times,
        is_trading_hours=is_trading_hours_arr,
        is_eod=is_eod_arr,
        is_weekend=is_weekend_arr,
        enforce_trading_hours=enforce_th,
        enforce_eod=enforce_eod,
        weekend_blocking=weekend_blocking,
        point_value=float(asset.point_value or 1.0),
        spread=em.spread_price(asset),
        slip=em.slippage_price(asset),
        commission=em._commission(asset),
        slippage_model_int=slippage_model_to_int(em.slippage_model),
        slippage_k=float(em.slippage_k),
        meta={"bars": int(n)},
    )
