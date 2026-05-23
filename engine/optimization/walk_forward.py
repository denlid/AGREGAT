"""
Walk-forward optimisation framework.

Given a list of trades (or a parameter grid + per-window runner) walk-forward
splits the data into IS / OOS windows, optimises on IS, then evaluates on OOS.

Two split modes are supported:
- **rolling**: IS window slides forward by ``oos_size`` each step (constant IS length)
- **anchored**: IS window starts at index 0 and grows by ``oos_size`` each step

Both produce ``WalkForwardSplit`` boundaries that the engine consumes.

The high-level entrypoint :func:`walk_forward` accepts a callable ``run_window``
that takes ``(is_indices, oos_indices)`` and returns ``(is_metrics, oos_metrics,
oos_pnls)``. Concrete integration with BacktestEngine + optimizer is wired in
``gui/walk_forward_dialog.py``; the engine module stays storage-agnostic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Tuple

import numpy as np


@dataclass(frozen=True)
class WalkForwardSplit:
    """Index ranges for one IS / OOS window pair."""

    is_start: int
    is_end: int  # exclusive
    oos_start: int
    oos_end: int  # exclusive

    @property
    def is_length(self) -> int:
        return self.is_end - self.is_start

    @property
    def oos_length(self) -> int:
        return self.oos_end - self.oos_start


@dataclass
class WalkForwardWindowResult:
    split: WalkForwardSplit
    is_metrics: Dict[str, float]
    oos_metrics: Dict[str, float]
    oos_pnls: np.ndarray


@dataclass
class WalkForwardResult:
    windows: List[WalkForwardWindowResult] = field(default_factory=list)

    @property
    def oos_concat_pnls(self) -> np.ndarray:
        if not self.windows:
            return np.array([], dtype=float)
        return np.concatenate([w.oos_pnls for w in self.windows])

    def oos_summary(self, metric: str) -> Dict[str, float]:
        """Mean / median / min / max of the metric across OOS windows."""
        vals = [w.oos_metrics.get(metric, float("nan")) for w in self.windows]
        arr = np.asarray([v for v in vals if not (isinstance(v, float) and np.isnan(v))])
        if arr.size == 0:
            return {
                "mean": float("nan"),
                "median": float("nan"),
                "min": float("nan"),
                "max": float("nan"),
                "count": 0,
            }
        return {
            "mean": float(arr.mean()),
            "median": float(np.median(arr)),
            "min": float(arr.min()),
            "max": float(arr.max()),
            "count": int(arr.size),
        }

    def is_oos_correlation(self, metric: str) -> float:
        """Correlation between IS and OOS metric across windows. NaN if undefined."""
        if len(self.windows) < 2:
            return float("nan")
        is_vals = np.asarray([w.is_metrics.get(metric, float("nan")) for w in self.windows])
        oos_vals = np.asarray([w.oos_metrics.get(metric, float("nan")) for w in self.windows])
        mask = ~(np.isnan(is_vals) | np.isnan(oos_vals))
        if mask.sum() < 2:
            return float("nan")
        if is_vals[mask].std() == 0 or oos_vals[mask].std() == 0:
            return float("nan")
        return float(np.corrcoef(is_vals[mask], oos_vals[mask])[0, 1])


# ----------------------------------------------------------------------------
# Split generation
# ----------------------------------------------------------------------------
def make_splits(
    n: int,
    *,
    is_size: int,
    oos_size: int,
    mode: str = "rolling",
) -> List[WalkForwardSplit]:
    """Return walk-forward windows for a series of ``n`` items.

    Parameters
    ----------
    n
        Total number of items (e.g. trades or candles).
    is_size
        In-sample window size.
    oos_size
        Out-of-sample window size.
    mode
        ``"rolling"`` (constant-IS) or ``"anchored"`` (growing-IS).
    """
    if n <= 0 or is_size <= 0 or oos_size <= 0:
        return []
    if is_size + oos_size > n:
        return []
    splits: List[WalkForwardSplit] = []
    is_start = 0
    is_end = is_size
    while is_end + oos_size <= n:
        oos_start = is_end
        oos_end = is_end + oos_size
        splits.append(WalkForwardSplit(is_start, is_end, oos_start, oos_end))
        if mode == "rolling":
            is_start += oos_size
            is_end += oos_size
        elif mode == "anchored":
            is_end += oos_size
        else:
            raise ValueError(f"Unknown walk-forward mode: {mode!r}")
    return splits


# ----------------------------------------------------------------------------
# High-level driver
# ----------------------------------------------------------------------------
def walk_forward(
    n: int,
    run_window: Callable[
        [Tuple[int, int], Tuple[int, int]],
        Tuple[Dict[str, float], Dict[str, float], np.ndarray],
    ],
    *,
    is_size: int,
    oos_size: int,
    mode: str = "rolling",
) -> WalkForwardResult:
    """Drive a walk-forward run by calling ``run_window`` for each split.

    ``run_window`` receives ``((is_start, is_end), (oos_start, oos_end))`` and
    must return ``(is_metrics, oos_metrics, oos_pnls)`` where ``oos_pnls`` is
    a numpy array of per-trade PnL on the OOS window.
    """
    splits = make_splits(n, is_size=is_size, oos_size=oos_size, mode=mode)
    result = WalkForwardResult()
    for s in splits:
        is_metrics, oos_metrics, oos_pnls = run_window(
            (s.is_start, s.is_end), (s.oos_start, s.oos_end)
        )
        result.windows.append(
            WalkForwardWindowResult(
                split=s,
                is_metrics=dict(is_metrics),
                oos_metrics=dict(oos_metrics),
                oos_pnls=np.asarray(oos_pnls, dtype=float),
            )
        )
    return result


__all__ = [
    "WalkForwardSplit",
    "WalkForwardWindowResult",
    "WalkForwardResult",
    "make_splits",
    "walk_forward",
]
