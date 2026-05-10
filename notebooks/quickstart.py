"""AGREGAT SDK Quickstart — Sprint 3.8.

Run this script (or paste into a Jupyter notebook) to see how the
AGREGAT backtesting SDK works from Python:

    python notebooks/quickstart.py

The script demonstrates:
1. Loading OHLC data from CSV
2. Running a headless backtest with BacktestCore
3. Inspecting trades and equity
4. Running a grid search
5. Computing risk metrics
"""

from __future__ import annotations

import os
import sys
import tempfile

import numpy as np
import pandas as pd

# Ensure the project root is on sys.path so ``engine`` is importable
# when running from the ``notebooks/`` directory.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from engine.sdk import (
    BacktestCore,
    DataLoader,
    build_equity_curve,
    daily_returns,
    max_drawdown,
    sharpe_ratio,
)


def _make_sample_csv(path: str, n: int = 500) -> str:
    """Generate a small synthetic OHLC CSV for demonstration."""
    rng = np.random.default_rng(42)
    price = 100.0
    rows = []
    for i in range(n):
        dt = pd.Timestamp("2024-01-02 09:00") + pd.Timedelta(minutes=i)
        change = rng.normal(0, 0.5)
        o = price
        c = price + change
        h = max(o, c) + abs(rng.normal(0, 0.2))
        l = min(o, c) - abs(rng.normal(0, 0.2))
        v = int(rng.integers(100, 5000))
        rows.append({"time": dt, "open": o, "high": h, "low": l, "close": c, "volume": v})
        price = c
    df = pd.DataFrame(rows)
    df.to_csv(path, index=False)
    return path


def main() -> None:
    # 1. Create sample data
    tmp = tempfile.mkdtemp(prefix="agregat_quickstart_")
    csv_path = os.path.join(tmp, "sample.csv")
    _make_sample_csv(csv_path)
    print(f"[1] Sample CSV created: {csv_path}")

    # 2. Load data
    df = DataLoader.load_csv(csv_path)
    print(f"[2] Loaded {len(df)} candles from {df['time'].iloc[0]} to {df['time'].iloc[-1]}")

    # 3. Run headless backtest
    core = BacktestCore()
    core.load_data(df)
    while core.current_idx < len(df) - 1:
        core.step()
    print(f"[3] Backtest complete — {len(core.trades)} trades, equity={core.equity:.2f}")

    # 4. Equity curve & metrics
    if core.trades:
        eq = build_equity_curve(core.trades, core.initial_equity)
        dd = max_drawdown(eq)
        dr = daily_returns(eq)
        sr = sharpe_ratio(dr) if len(dr) > 1 else 0.0
        print(f"[4] Max drawdown: {dd:.2%}, Sharpe: {sr:.2f}")
    else:
        print("[4] No trades generated (default _Empty strategy — load a .py strategy for results)")

    print("\nDone! See engine/sdk.py for the full API surface.")


if __name__ == "__main__":
    main()
