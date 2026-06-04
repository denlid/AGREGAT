"""Chart render snapshot — immutable numpy-based frame state (Roadmap v2 / S3).

Replaces the DataFrame.copy() + reset_index() hot path in ChartTab.redraw_chart
with zero-copy numpy array slices. The snapshot holds the minimal data needed
for one chart redraw: OHLC arrays, time array, and metadata (visible range, LOD
level, active TF).

Expected gain: 2–3x on chart prep-side by eliminating pandas overhead in the
redraw hot path.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np


@dataclass(frozen=True)
class ChartRenderSnapshot:
    """Immutable snapshot of chart data for one redraw frame.

    Built from BacktestCore's numpy arrays (self._open, self._close, etc.) via
    zero-copy slicing. The arrays are views into the engine's storage, not
    copies, so construction is O(1) regardless of dataset size.

    Fields
    ------
    time : np.ndarray
        Datetime64[ns] array of bar timestamps, shape (n,).
    open : np.ndarray
        Open prices, shape (n,), dtype float64.
    high : np.ndarray
        High prices, shape (n,), dtype float64.
    low : np.ndarray
        Low prices, shape (n,), dtype float64.
    close : np.ndarray
        Close prices, shape (n,), dtype float64.
    x : np.ndarray
        Integer bar indices for plotting (0, 1, 2, ..., n-1), shape (n,),
        dtype float64 (pyqtgraph expects float).
    n : int
        Number of bars in this snapshot.
    source_start : int
        Start index in the source dataset (engine.df). For a tail slice
        (e.g., last 5000 bars), this is nonzero.
    source_end : int
        End index (exclusive) in the source dataset.
    active_tf : str
        Active timeframe ("1m", "5m", etc.) for this snapshot.
    lod_level : Optional[str]
        LOD pyramid level if applicable, else None.
    """

    time: np.ndarray
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    x: np.ndarray
    n: int
    source_start: int
    source_end: int
    active_tf: str
    lod_level: Optional[str] = None

    @classmethod
    def from_engine_arrays(
        cls,
        time: np.ndarray,
        open_: np.ndarray,
        high: np.ndarray,
        low: np.ndarray,
        close: np.ndarray,
        start: int,
        end: int,
        active_tf: str,
        lod_level: Optional[str] = None,
    ) -> ChartRenderSnapshot:
        """Build a snapshot from BacktestCore's numpy arrays via zero-copy slice.

        Args:
            time: Engine's self._time array.
            open_: Engine's self._open array.
            high: Engine's self._high array.
            low: Engine's self._low array.
            close: Engine's self._close array.
            start: Start index (inclusive) in the source arrays.
            end: End index (exclusive) in the source arrays.
            active_tf: Active timeframe string.
            lod_level: Optional LOD pyramid level.

        Returns:
            ChartRenderSnapshot with views into the source arrays (no copy).
        """
        # Slice arrays — these are views, not copies (O(1) operation).
        time_slice = time[start:end]
        open_slice = open_[start:end]
        high_slice = high[start:end]
        low_slice = low[start:end]
        close_slice = close[start:end]
        n = len(time_slice)
        x = np.arange(n, dtype=np.float64)

        return cls(
            time=time_slice,
            open=open_slice,
            high=high_slice,
            low=low_slice,
            close=close_slice,
            x=x,
            n=n,
            source_start=start,
            source_end=end,
            active_tf=active_tf,
            lod_level=lod_level,
        )

    def candle_array(self) -> np.ndarray:
        """Return (n, 5) candle array: [x, open, close, low, high].

        This is the format CandlestickItem.set_data expects. Built on-demand
        from the snapshot's arrays (cheap column_stack, no copy of OHLC data).
        """
        arr = np.column_stack((self.x, self.open, self.close, self.low, self.high))
        arr.setflags(write=False)
        return arr

    def time_int64(self) -> np.ndarray:
        """Return time as int64 (nanoseconds since epoch) for fast lookups.

        Crosshair/tooltip code needs integer timestamps for searchsorted-based
        O(log N) lookups. This avoids repeated datetime conversions.
        """
        return self.time.view(np.int64)

    def time_float_seconds(self) -> np.ndarray:
        """Return time as float seconds since epoch for axis labels."""
        return self.time_int64() / 1e9
