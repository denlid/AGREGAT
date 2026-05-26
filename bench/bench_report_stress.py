"""Stress profiler for the institutional tear-sheet report (Phase 6 baseline).

Goal — measure where time is spent when the report is generated for a
worst-case backtest:

    * 50_000 closed trades
    * 10M-bar OHLC dataframe (drives Phase 4 regime / exposure sections)

Run with::

    python -m bench.bench_report_stress              # full 50k / 10M
    python -m bench.bench_report_stress --quick      # 5k / 200k for CI

The output is a JSON timing breakdown printed to stdout — do NOT commit
the result. Used for before/after comparisons in Phase 6 PRs.
"""

from __future__ import annotations

import argparse
import cProfile
import io
import json
import pstats
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Dict, List

import numpy as np
import pandas as pd

# Local imports happen lazily inside ``_run`` so we measure import overhead too.


@dataclass
class _Trade:
    """Mirror of the engine.models.Trade slot layout (subset)."""

    id: int
    direction: str
    entry_price: float
    exit_price: float
    pnl: float
    pnl_points: float
    entry_index: int
    exit_index: int
    entry_time: str
    exit_time: str
    volume: float
    duration_minutes: int
    exit_reason: str
    mae: float
    mfe: float
    session: str
    initial_sl: float
    rr: float
    mae_dollars: float = 0.0
    mfe_dollars: float = 0.0
    tag: str = ""
    commission: float = 0.0
    spread_cost: float = 0.0
    slippage_cost: float = 0.0
    dow: str = "Unknown"
    hod: int = -1
    regime: str = "unknown"
    entry_bar_index: int = 0
    exit_bar_index: int = 0


def _make_trades(n: int) -> List[_Trade]:
    rng = np.random.default_rng(0xA9B6)
    out: List[_Trade] = []
    for i in range(n):
        sess = ("london", "ny", "asia")[i % 3]
        reason = ("TP", "SL", "EOD", "GAP_SL")[i % 4]
        pnl = float(rng.normal(20.0, 80.0))
        out.append(
            _Trade(
                id=i + 1,
                direction="long" if i % 2 == 0 else "short",
                entry_price=100.0 + (i % 200) * 0.05,
                exit_price=100.5 + (i % 200) * 0.05,
                pnl=pnl,
                pnl_points=pnl,
                entry_index=i * 3,
                exit_index=i * 3 + 5,
                entry_time=f"2024-{((i // 720) % 12) + 1:02d}-{((i // 24) % 28) + 1:02d} {(i % 24):02d}:00:00",
                exit_time=f"2024-{((i // 720) % 12) + 1:02d}-{((i // 24) % 28) + 1:02d} {(i % 24):02d}:30:00",
                volume=1.0,
                duration_minutes=30,
                exit_reason=reason,
                mae=-1.0,
                mfe=2.0,
                session=sess,
                initial_sl=99.0,
                rr=1.5,
                entry_bar_index=i * 3,
                exit_bar_index=i * 3 + 5,
            )
        )
    return out


def _make_ohlc(n_bars: int) -> pd.DataFrame:
    rng = np.random.default_rng(1337)
    base = 100.0 + np.cumsum(rng.normal(0.0, 0.05, size=n_bars))
    return pd.DataFrame(
        {
            "open": base,
            "high": base + 0.5,
            "low": base - 0.5,
            "close": base + 0.05,
            "volume": np.full(n_bars, 1000.0),
        },
        index=pd.date_range("2020-01-01", periods=n_bars, freq="1min"),
    )


@contextmanager
def _timed(label: str, results: Dict[str, float]):
    t0 = time.perf_counter()
    yield
    results[label] = time.perf_counter() - t0


def _run(n_trades: int, n_bars: int, profile: bool) -> Dict[str, Any]:
    results: Dict[str, float] = {}
    with _timed("01_make_trades", results):
        trades = _make_trades(n_trades)
    with _timed("02_make_ohlc", results):
        df = _make_ohlc(n_bars)

    from engine.report.tearsheet import render_html_tearsheet

    meta = {"strategy": "stress", "asset": "DE40", "initial_equity": 100_000.0}
    summary = {"initial_equity": 100_000.0, "total_pnl": 12345.0, "trades": []}

    pr = cProfile.Profile() if profile else None
    if pr is not None:
        pr.enable()
    with _timed("03_render_html_tearsheet", results):
        html = render_html_tearsheet(meta, summary, trades, theme="dark", df=df)
    if pr is not None:
        pr.disable()

    return {
        "n_trades": n_trades,
        "n_bars": n_bars,
        "html_bytes": len(html),
        "timings_sec": results,
        "profile": _format_profile(pr) if pr is not None else None,
    }


def _format_profile(pr: cProfile.Profile, top: int = 25) -> str:
    s = io.StringIO()
    pstats.Stats(pr, stream=s).strip_dirs().sort_stats("cumulative").print_stats(top)
    return s.getvalue()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--quick", action="store_true", help="5k trades / 200k bars (CI-friendly, ~5s)."
    )
    ap.add_argument(
        "--profile",
        action="store_true",
        help="Enable cProfile and print the top 25 callers.",
    )
    ap.add_argument(
        "--json", action="store_true", help="Print JSON only (suppress profile text)."
    )
    args = ap.parse_args()

    if args.quick:
        n_trades, n_bars = 5_000, 200_000
    else:
        n_trades, n_bars = 50_000, 10_000_000

    out = _run(n_trades, n_bars, profile=args.profile)
    if args.json or not args.profile:
        print(json.dumps({k: v for k, v in out.items() if k != "profile"}, indent=2))
    if args.profile and out["profile"]:
        print("\n--- cProfile (top 25 cumulative) ---\n")
        print(out["profile"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
