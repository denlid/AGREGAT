"""HTFView — strategy-facing read-only NumPy view of higher-timeframe bars.

Counterpart of :class:`engine.core.data_view.DataView`, but for
higher-timeframe (HTF) candles aggregated from the 1m base via
:class:`engine.tf_aggregator.TimeframeAggregator`. The view is sliced
to the *completed* HTF bar count for the current 1m step (Zero-Future
Rule: a HTF bar is only visible once its last constituent 1m bar has
been processed).

The view never copies — every property returns a NumPy slice over the
same backing arrays the aggregator built once at ``prebuild`` time. The
slice carries ``writeable=False`` so strategies can't accidentally
mutate engine state. (TZ Phase 3 — Vectorize TimeframeAggregator.)
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional

import numpy as np


class HTFView:
    """Read-only window over the per-TF aggregated NumPy columns.

    Parameters
    ----------
    arrays
        Mapping of column name (``open``/``high``/``low``/``close``/
        ``volume``/``time``) to the per-TF NumPy array spanning *all*
        HTF bars built by ``TimeframeAggregator.prebuild`` for one
        timeframe.
    count_provider
        Zero-arg callable returning the current *completed* HTF bar
        count for this timeframe. Slicing is exclusive past this count
        — when the engine has not advanced past the first HTF
        boundary, the view is empty.
    forming_provider
        Optional zero-arg callable returning the current forming HTF
        candle as a dict, or ``None`` if there is no forming bar (e.g.
        before the engine has stepped, or when the 1m bar lands exactly
        on a bucket boundary). Strategies that explicitly want the
        in-progress bar can call :py:meth:`forming`.
    """

    __slots__ = ("_arrays", "_count_provider", "_forming_provider")

    def __init__(
        self,
        arrays: Dict[str, np.ndarray],
        count_provider: Callable[[], int],
        forming_provider: Optional[Callable[[], Optional[Dict[str, Any]]]] = None,
    ) -> None:
        self._arrays = arrays
        self._count_provider = count_provider
        self._forming_provider = forming_provider

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _slice(self, name: str) -> np.ndarray:
        arr = self._arrays.get(name)
        if arr is None:
            raise AttributeError(
                f"HTFView has no column {name!r}; available: {sorted(self._arrays)}"
            )
        cnt = self._count_provider()
        if cnt <= 0:
            view = arr[:0]
        else:
            view = arr[:cnt]
        try:
            view.setflags(write=False)
        except (AttributeError, ValueError):
            # ``time`` is a datetime64 view; setflags works the same
            # way, but if a future caller passes a non-ndarray we don't
            # want to crash.
            pass
        return view

    # ------------------------------------------------------------------
    # Public surface (mirrors DataView for strategy ergonomics)
    # ------------------------------------------------------------------
    @property
    def open(self) -> np.ndarray:  # noqa: A003 — column name, not the builtin
        return self._slice("open")

    @property
    def high(self) -> np.ndarray:
        return self._slice("high")

    @property
    def low(self) -> np.ndarray:
        return self._slice("low")

    @property
    def close(self) -> np.ndarray:
        return self._slice("close")

    @property
    def volume(self) -> np.ndarray:
        return self._slice("volume")

    @property
    def time(self) -> np.ndarray:
        return self._slice("time")

    @property
    def completed_count(self) -> int:
        """Number of HTF bars currently visible (Zero-Future safe)."""
        return max(0, int(self._count_provider()))

    def __len__(self) -> int:
        return self.completed_count

    def __getattr__(self, name: str) -> np.ndarray:
        try:
            return self._slice(name)
        except AttributeError as e:
            raise AttributeError(f"'HTFView' has no attribute or column '{name}'") from e

    def latest(self) -> Optional[Dict[str, Any]]:
        """Most recently completed HTF bar as a dict, or ``None`` before
        any HTF bar has completed.
        """
        cnt = self.completed_count
        if cnt == 0:
            return None
        i = cnt - 1
        return {
            "time": self._arrays["time"][i],
            "open": float(self._arrays["open"][i]),
            "high": float(self._arrays["high"][i]),
            "low": float(self._arrays["low"][i]),
            "close": float(self._arrays["close"][i]),
            "volume": float(self._arrays["volume"][i]),
        }

    def forming(self) -> Optional[Dict[str, Any]]:
        """Return the current in-progress (NOT Zero-Future safe) HTF
        candle. Equivalent to the previous
        ``ctx.get_tf_forming(tf)`` API and is meant for visualisation
        only — strategies should consume :py:attr:`close`,
        :py:attr:`high`, etc, which respect Zero-Future.
        """
        if self._forming_provider is None:
            return None
        return self._forming_provider()

    def candle_at(self, idx: int) -> Dict[str, Any]:
        """Return the HTF candle at ``idx`` as a dict (raises if out of
        the *completed* range to enforce Zero-Future).
        """
        cnt = self.completed_count
        if idx < 0 or idx >= cnt:
            raise IndexError(
                f"HTFView.candle_at: idx {idx} out of bounds for completed count {cnt}"
            )
        return {
            "time": self._arrays["time"][idx],
            "open": float(self._arrays["open"][idx]),
            "high": float(self._arrays["high"][idx]),
            "low": float(self._arrays["low"][idx]),
            "close": float(self._arrays["close"][idx]),
            "volume": float(self._arrays["volume"][idx]),
        }
