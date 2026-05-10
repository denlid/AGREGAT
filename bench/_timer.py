"""Tiny timer utility — just :mod:`time.perf_counter` with a couple of
ergonomic helpers so the bench scripts read like the timing tables we
want to ship in PR descriptions.
"""

from __future__ import annotations

import statistics
import time
from typing import Callable, List


def time_call(fn: Callable[[], object], *, repeats: int = 3) -> dict:
    """Time ``fn`` ``repeats`` times and return summary stats.

    Returns a dict with keys ``min_s``, ``median_s``, ``mean_s``,
    ``stdev_s`` and ``runs_s`` so callers can write whatever subset of
    the data they want into ``baseline.json``.
    """
    samples: List[float] = []
    for _ in range(repeats):
        t0 = time.perf_counter()
        fn()
        samples.append(time.perf_counter() - t0)
    return {
        "runs_s": [round(s, 6) for s in samples],
        "min_s": round(min(samples), 6),
        "median_s": round(statistics.median(samples), 6),
        "mean_s": round(statistics.mean(samples), 6),
        "stdev_s": (round(statistics.stdev(samples), 6) if len(samples) > 1 else 0.0),
    }
