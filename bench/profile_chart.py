"""Performance profiling script for chart redraw hot path.

Identifies bottlenecks in the chart rendering pipeline using cProfile.
Run this to find optimization opportunities.
"""

import cProfile
import pstats
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import numpy as np
import pandas as pd


def profile_chart_redraw():
    """Profile full chart redraw cycle."""
    # Setup
    from engine.core.backtest_core import BacktestCore
    from gui.chart_tab.render_snapshot import ChartRenderSnapshot
    from gui.chart_tab.markers import build_trade_marker_arrays
    from collections import namedtuple

    # Create engine with realistic data
    engine = BacktestCore()
    df = pd.DataFrame({
        "time": pd.date_range("2024-01-01", periods=100_000, freq="1min"),
        "open": 100.0 + np.cumsum(np.random.randn(100_000) * 0.5),
        "high": 100.0 + np.cumsum(np.random.randn(100_000) * 0.5) + 0.5,
        "low": 100.0 + np.cumsum(np.random.randn(100_000) * 0.5) - 0.5,
        "close": 100.0 + np.cumsum(np.random.randn(100_000) * 0.5),
    })
    engine.load_data(df)

    # Create trades
    Trade = namedtuple('Trade', ['entry_time', 'exit_time', 'entry_price', 'exit_price',
                                   'entry_index', 'exit_index', 'direction', 'pnl',
                                   'entry_time_np', 'exit_time_np'])
    trades = []
    for i in range(10_000):
        entry_idx = np.random.randint(0, 99_900)
        exit_idx = entry_idx + np.random.randint(10, 100)
        entry_time = engine._time[entry_idx]
        exit_time = engine._time[exit_idx]
        entry_price = engine._close[entry_idx]
        exit_price = engine._close[exit_idx]
        direction = "long" if np.random.rand() > 0.5 else "short"
        pnl = (exit_price - entry_price) if direction == "long" else (entry_price - exit_price)

        trades.append(Trade(
            entry_time=entry_time,
            exit_time=exit_time,
            entry_price=entry_price,
            exit_price=exit_price,
            entry_index=entry_idx,
            exit_index=exit_idx,
            direction=direction,
            pnl=pnl,
            entry_time_np=entry_time,
            exit_time_np=exit_time,
        ))

    engine.trades = trades

    # Profile redraw cycle
    def redraw_cycle():
        """Simulate full redraw."""
        # 1. Snapshot
        snapshot = ChartRenderSnapshot.from_engine_arrays(
            time=engine._time,
            open_=engine._open,
            high=engine._high,
            low=engine._low,
            close=engine._close,
            start=max(0, 100_000 - 5000),
            end=100_000,
            active_tf="1m",
        )

        # 2. Candle array
        candle_arr = snapshot.candle_array()

        # 3. Trade markers
        arrays = build_trade_marker_arrays(
            rt=snapshot.time,
            trades=trades,
            df_len=100_000,
            position=None,
            position_close_price=None,
            show_connections=True,
        )

        return snapshot, candle_arr, arrays

    # Run profiling
    profiler = cProfile.Profile()
    profiler.enable()

    # Execute 100 redraws
    for _ in range(100):
        redraw_cycle()

    profiler.disable()

    # Print results
    s = io.StringIO()
    ps = pstats.Stats(profiler, stream=s).sort_stats('cumulative')
    ps.print_stats(30)  # Top 30 functions

    print("=" * 80)
    print("CHART REDRAW PROFILING RESULTS (100 iterations)")
    print("=" * 80)
    print(s.getvalue())

    # Also print by total time
    s = io.StringIO()
    ps = pstats.Stats(profiler, stream=s).sort_stats('tottime')
    ps.print_stats(20)  # Top 20 by total time

    print("\n" + "=" * 80)
    print("TOP FUNCTIONS BY TOTAL TIME")
    print("=" * 80)
    print(s.getvalue())


if __name__ == "__main__":
    profile_chart_redraw()
