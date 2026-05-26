"""
DataView — strategy-facing read-only window into the engine's NumPy
storage.

A :class:`DataView` exposes ``open``, ``high``, ``low``, ``close``,
``volume``, and ``time`` as read-only NumPy slices that span only the
candles already processed by the engine (``current_idx + 1`` bars).
Strategies that respect the Zero-Future Rule simply read
``ctx.data.close[-N:]`` and never see future data; any attempt to mutate
the array raises ``ValueError`` because the slice carries
``writeable=False``.

The view is cheap: each property returns an O(1) NumPy slice (a view,
not a copy) plus a one-shot ``setflags(write=False)``. Indicator helpers
in :mod:`engine.core.indicators` accept these slices directly.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional

import numpy as np


class DataView:
    """Read-only window into the engine's per-bar NumPy columns.

    The view is parameterised by a callable that returns the current
    1-minute bar index. Slicing is exclusive of the next bar — when
    ``current_idx == 5``, the view exposes bars 0..5 inclusive (six
    elements). Before the engine has processed any bar
    (``current_idx == -1``) the view is empty.
    """

    __slots__ = ("_arrays", "_idx_provider")

    def __init__(
        self,
        arrays: Dict[str, np.ndarray],
        idx_provider: Callable[[], int],
    ) -> None:
        self._arrays = arrays
        self._idx_provider = idx_provider

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _slice(self, name: str) -> np.ndarray:
        arr = self._arrays.get(name)
        if arr is None:
            raise AttributeError(
                f"DataView has no column {name!r}; available: {sorted(self._arrays)}"
            )
        idx = self._idx_provider()
        if idx < 0:
            view = arr[:0]
        else:
            view = arr[: idx + 1]
        # ``setflags(write=False)`` on a view also marks it read-only;
        # that prevents strategies from accidentally mutating engine
        # state (and from breaking the zero-future invariant by
        # back-filling future values).
        try:
            view.setflags(write=False)
        except (AttributeError, ValueError):
            # ``time`` is a datetime64 NumPy view; setflags works the
            # same way, but if a future caller passes a non-ndarray
            # we don't want to crash.
            pass
        return view

    # ------------------------------------------------------------------
    # Public surface
    # ------------------------------------------------------------------
    @property
    def open(self) -> np.ndarray:  # noqa: A003 — column name shadowing builtins is intentional
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
    def current_idx(self) -> int:
        """Index of the latest bar visible to the strategy (``-1`` before
        the engine has stepped). Equal to ``len(self) - 1``.
        """
        return self._idx_provider()

    def __len__(self) -> int:
        idx = self._idx_provider()
        return 0 if idx < 0 else idx + 1

    def __getattr__(self, name: str) -> np.ndarray:
        """Allow dynamic property access for pre-calculated indicators."""
        try:
            return self._slice(name)
        except AttributeError as e:
            raise AttributeError(f"'DataView' has no attribute or column '{name}'") from e

    # Convenience for legacy GUI / persistence code that wants a single
    # candle as a dict. Strategies should prefer the NumPy properties.
    def candle_at(self, idx: int) -> Dict[str, Any]:
        size = self._arrays["close"].shape[0]
        if idx < 0 or idx >= size:
            raise IndexError(f"candle_at: idx {idx} out of bounds for size {size}")
        candle: Dict[str, Any] = {}
        for name, arr in self._arrays.items():
            value = arr[idx]
            # Convert numpy scalars to plain Python types so downstream
            # code that does ``str(time)`` / ``float(close)`` keeps
            # working.
            if name == "time":
                candle[name] = value
            else:
                candle[name] = float(value)
        return candle

    def latest(self) -> Optional[Dict[str, Any]]:
        """Return the most recent candle as a dict, or ``None`` before
        the engine has stepped.
        """
        idx = self._idx_provider()
        if idx < 0:
            return None
        return self.candle_at(idx)


__all__ = ["DataView"]
