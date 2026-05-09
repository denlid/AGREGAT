"""Sprint 4.1 — Pain-family metrics.

Tail Ratio and Gain-to-Pain are introduced here. Pain Index, Pain
Ratio and Ulcer Index already live in :mod:`engine.risk_metrics`; we
re-export them via :mod:`engine.metrics`.

Definitions
-----------
- **Tail Ratio** — ``|95th percentile gain| / |5th percentile loss|``
  on per-trade PnL (or daily returns). >1 means upside tail dominates.
- **Gain-to-Pain** — ``sum(monthly returns) / sum(|monthly losses|)``
  per Jack Schwager. We accept any per-period PnL or return array.
"""

from __future__ import annotations

from typing import Sequence, Union

import numpy as np
from numpy.typing import NDArray

PnLArray = Union[NDArray[np.floating], Sequence[float]]


def tail_ratio(pnls: PnLArray, *, upper: float = 0.95, lower: float = 0.05) -> float:
    """Ratio of the upper-tail gain to the lower-tail loss magnitude.

    Returns ``0.0`` when there are no losses below the lower percentile
    (avoiding ``inf``); ``NaN`` when the array is empty.
    """
    arr = np.asarray(pnls, dtype="float64")
    if arr.size == 0:
        return float("nan")
    hi = float(np.percentile(arr, upper * 100.0))
    lo = float(np.percentile(arr, lower * 100.0))
    if lo >= 0:
        # No losing percentile — ratio is undefined-ish; report 0.0
        # so downstream charts treat it as "no left tail".
        return 0.0
    return float(abs(hi) / abs(lo))


def gain_to_pain(pnls: PnLArray) -> float:
    """Schwager's Gain-to-Pain Ratio.

    Sum of all PnL values divided by the sum of the absolute values of
    losing periods. Returns ``0.0`` when there are no losses (no pain
    means the ratio is undefined; we report ``0.0`` so it sorts low
    among "real" track records).
    """
    arr = np.asarray(pnls, dtype="float64")
    if arr.size == 0:
        return float("nan")
    losses = arr[arr < 0]
    if losses.size == 0:
        return 0.0
    pain = float(-losses.sum())
    if pain <= 0:
        return 0.0
    return float(arr.sum() / pain)


__all__ = ["tail_ratio", "gain_to_pain"]
