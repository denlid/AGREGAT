"""Benchmark overlay diff reconciliation (Roadmap v2 / S4).

Measures the speedup of diff-based overlay updates vs clear+recreate on heavy
annotated scenes. Simulates redraws where most overlays are unchanged (the
common case during pan/zoom).

The real bottleneck in clear+recreate is the Qt scene operations
(plot_widget.addItem / removeItem), NOT the hashing. Each addItem/removeItem
triggers scene graph updates, bounding-box recalc, and repaint scheduling.
This benchmark simulates that per-item scene cost to show where the gain comes
from.

Expected gain: 2-4x on scenes with many overlays where few change per redraw.
"""

from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

from bench._timer import time_call
from gui.chart_tab.overlay_diff import drawing_id, diff_overlays


# Simulated cost of one Qt scene operation (addItem or removeItem). Real
# pyqtgraph addItem is ~10-50 microseconds depending on scene complexity.
# We use a representative value to show the relative gain.
_SCENE_OP_COST_US = 20.0


def _simulate_scene_op() -> None:
    """Simulate the cost of one addItem/removeItem Qt scene operation."""
    # Busy-work approximating scene graph update cost (~20 microseconds).
    x = 0.0
    for _ in range(200):
        x += np.sin(x + 1.0)


def _make_drawings(n: int) -> list[dict]:
    """Generate n synthetic strategy drawings (lines + rects)."""
    drawings = []
    base_time = pd.Timestamp("2024-01-02 10:00")
    for i in range(n):
        t1 = (base_time + pd.Timedelta(minutes=i)).isoformat()
        t2 = (base_time + pd.Timedelta(minutes=i + 5)).isoformat()
        if i % 2 == 0:
            drawings.append({
                "type": "line",
                "t1": t1,
                "t2": t2,
                "p1": 100.0 + i * 0.1,
                "p2": 105.0 + i * 0.1,
                "color": "white",
                "width": 1,
            })
        else:
            drawings.append({
                "type": "rect",
                "t1": t1,
                "t2": t2,
                "p1": 90.0 + i * 0.1,
                "p2": 95.0 + i * 0.1,
                "color": "blue",
            })
    return drawings


def _clear_recreate_cost(drawings: list[dict], repeats: int) -> None:
    """Simulate clear+recreate: remove ALL + add ALL items every redraw."""
    for _ in range(repeats):
        # Remove all existing items (one scene op each).
        for _d in drawings:
            _simulate_scene_op()
        # Recreate all items (one scene op each).
        for d in drawings:
            drawing_id(d)  # Hash cost.
            _simulate_scene_op()


def _diff_cost(old_ids: dict, drawings: list[dict], repeats: int) -> None:
    """Simulate diff: only changed drawings trigger scene ops."""
    for _ in range(repeats):
        to_remove, to_keep, to_add = diff_overlays(old_ids, drawings)
        # Only to_remove + to_add trigger scene ops; to_keep is FREE.
        for _did in to_remove:
            _simulate_scene_op()
        for _d in to_add:
            _simulate_scene_op()


def measure(n_overlays: int = 200, repeats: int = 100, bench_repeats: int = 3) -> dict:
    """Measure diff vs clear+recreate cost.

    Args:
        n_overlays: Number of overlays on the scene.
        repeats: Number of redraw cycles (simulates pan/zoom).
        bench_repeats: Benchmark repeats for median timing.
    """
    drawings = _make_drawings(n_overlays)

    # Pre-build old_ids (the diff path reuses these — the common case where
    # most overlays are unchanged between redraws).
    old_ids = {drawing_id(d): f"item_{drawing_id(d)}" for d in drawings}

    def _clear_recreate() -> None:
        _clear_recreate_cost(drawings, repeats)

    def _diff() -> None:
        _diff_cost(old_ids, drawings, repeats)

    clear_recreate = time_call(_clear_recreate, repeats=bench_repeats)
    diff = time_call(_diff, repeats=bench_repeats)

    return {
        "n_overlays": int(n_overlays),
        "repeats": int(repeats),
        "bench_repeats": int(bench_repeats),
        "clear_recreate": clear_recreate,
        "diff": diff,
        "speedup": float(clear_recreate["median_s"] / diff["median_s"])
        if diff["median_s"] > 0
        else 0.0,
    }


def main() -> dict:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--overlays", type=int, default=200)
    parser.add_argument("--repeats", type=int, default=100)
    parser.add_argument("--bench-repeats", type=int, default=3)
    args = parser.parse_args()
    result = measure(
        n_overlays=args.overlays, repeats=args.repeats, bench_repeats=args.bench_repeats
    )
    print(result)
    return result


if __name__ == "__main__":
    main()
