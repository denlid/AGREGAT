"""Run every bench script and refresh ``bench/baseline.json``.

Usage::

    python -m bench.run_all                 # full 200k-bar baseline
    python -m bench.run_all --quick         # tiny 5k-bar smoke run

The ``--quick`` mode exists for CI and for local sanity checks; do not
commit a baseline emitted in quick mode.
"""

from __future__ import annotations

import argparse
import json
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path

from bench import bench_grid, bench_llc, bench_lsf, bench_vectorized_reuse

REPO_ROOT = Path(__file__).resolve().parent.parent
BASELINE_PATH = REPO_ROOT / "bench" / "baseline.json"


def _env_block() -> dict:
    return {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor() or "unknown",
        "captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--quick",
        action="store_true",
        help="Use tiny datasets for a fast smoke run (do NOT commit the result).",
    )
    parser.add_argument(
        "--repeats",
        type=int,
        default=3,
        help="Per-bench timing repeats (default 3).",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=BASELINE_PATH,
        help=f"Output path (default {BASELINE_PATH}).",
    )
    args = parser.parse_args()

    n_bars = 5_000 if args.quick else 200_000
    grid_n = 4 if args.quick else 8

    payload = {
        "schema_version": 2,
        "quick_mode": bool(args.quick),
        "env": _env_block(),
        "lsf": bench_lsf.measure(n_bars=n_bars, repeats=args.repeats),
        "llc": bench_llc.measure(n_bars=n_bars, repeats=args.repeats),
        "grid": bench_grid.measure(grid_n=grid_n, repeats=args.repeats),
        "vectorized_reuse": bench_vectorized_reuse.measure(
            n_bars=n_bars,
            runs=3 if args.quick else 10,
            repeats=args.repeats,
        ),
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(f"Wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
