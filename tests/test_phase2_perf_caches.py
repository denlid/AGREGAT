"""TZ Phase 2 / B1 + B2 — regression tests for the engine perf hot-spots.

These tests don't measure wall-clock — they only lock the *behavioural*
contract that lets the perf wins land:

* B1: ``ExecutionModel.spread_price`` memoises its result against every
  input that affects the output. Reading the same value twice in a row
  must hit the cache; mutating any input must bust it.
* B2: ``BacktestCore`` populates ``Position.entry_time_np`` on order
  fill so the duration calc in ``execute_close`` doesn't need to round
  trip through ``pd.to_datetime``.
"""

from __future__ import annotations

import os

import numpy as np
import pandas as pd

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from engine.strategy.context import Strategy  # noqa: E402
from engine.core.backtest_core import BacktestCore  # noqa: E402
from engine.execution.execution import ExecutionModel  # noqa: E402
from engine.models.models import AssetConfig  # noqa: E402


# ----------------------------------------------------------------------- B1
def _asset(spread=2.0, tick=0.5):
    return AssetConfig(
        name="TEST",
        point_value=10.0,
        tick_size=tick,
        buffer=0.0,
        max_lots_per_100k=10,
        currency="USD",
        spread_points=spread,
        commission_per_lot=0.0,
        slippage_points=0.0,
    )


def test_spread_price_cached_for_same_inputs() -> None:
    em = ExecutionModel(profile="Realistic")
    a = _asset()

    first = em.spread_price(a)
    cached = em._spread_price_cache
    assert cached is not None and cached[1] == first

    # Second call with the same asset must reuse the cached tuple.
    second = em.spread_price(a)
    assert em._spread_price_cache is cached
    assert second == first


def test_spread_price_cache_busts_on_input_change() -> None:
    em = ExecutionModel(profile="Realistic")
    a = _asset(spread=2.0, tick=0.5)
    em.spread_price(a)
    cached_first = em._spread_price_cache

    # Mutating the asset's spread changes the cache key — recompute.
    a2 = _asset(spread=4.0, tick=0.5)
    em.spread_price(a2)
    assert em._spread_price_cache is not cached_first

    # Mutating the profile recomputes too.
    em2 = ExecutionModel(profile="Realistic", spread_points=10.0)
    val = em2.spread_price(a)
    em2.profile = "Optimistic"  # Optimistic ignores spread overrides
    em2.invalidate_caches()
    assert em2.spread_price(a) == 0.0
    assert val != 0.0


def test_optimistic_profile_returns_zero_spread_price() -> None:
    em = ExecutionModel(profile="Optimistic", spread_points=99.0)
    a = _asset(spread=99.0, tick=0.5)
    # Optimistic always returns 0 regardless of the override or asset cost.
    assert em.spread_price(a) == 0.0
    assert em.spread_price(a) == 0.0  # cached path


# ----------------------------------------------------------------------- B2
class _BuyOnce(Strategy):
    def __init__(self) -> None:
        self._sent = False

    def on_candle(self, ctx) -> None:  # type: ignore[override]
        if not self._sent and ctx.position is None:
            ctx.buy_at_open(volume=1.0, sl=None, tp=None, reason="t", tag="t")
            self._sent = True


def _df_5m() -> pd.DataFrame:
    """Five-minute bars so duration_minutes != bar count and so we can
    cleanly assert the timestamp-based path is in use.
    """
    rows = [
        ("2024-01-02 09:00", 100, 105, 99, 101),
        ("2024-01-02 09:05", 101, 106, 100, 102),
        ("2024-01-02 09:10", 102, 107, 101, 103),
        ("2024-01-02 09:15", 103, 108, 102, 104),
    ]
    df = pd.DataFrame(rows, columns=["time", "open", "high", "low", "close"])
    df["time"] = pd.to_datetime(df["time"])
    df["volume"] = 1.0
    return df


def _make_core() -> BacktestCore:
    core = BacktestCore()
    core.asset_config = _asset(spread=0.0, tick=0.01)
    core.execution_model = ExecutionModel(profile="Optimistic")
    core.initial_equity = 100_000.0
    core.equity = core.initial_equity
    core.max_equity = core.initial_equity
    return core


def test_position_caches_entry_time_np_on_fill() -> None:
    core = _make_core()
    core.load_data(_df_5m())
    core.load_strategy(_BuyOnce())

    core.step()  # bar 0
    core.step()  # bar 1: order from bar 0 fills here

    pos = core.position
    assert pos is not None
    assert pos.entry_time_np is not None
    assert isinstance(pos.entry_time_np, np.datetime64)


def test_execute_close_uses_timestamp_for_duration() -> None:
    """Force a close on bar 3 — entry on bar 1 (after order from bar 0
    fills). Duration must be 10 minutes (2 × 5m bars), not the bar-count
    fallback (which would give 2).
    """
    core = _make_core()
    core.load_data(_df_5m())
    core.load_strategy(_BuyOnce())

    # bar 0: signal
    core.step()
    # bar 1: fill at open of bar 1
    core.step()
    # bar 2: hold
    core.step()
    # Manually close at bar 3.
    candle_3 = core.candle_at(3)
    core.execute_close(price=None, reason="MANUAL", exit_candle=candle_3)

    assert len(core.trades) == 1
    trade = core.trades[0]
    # Entry at bar 1 (09:05), exit at bar 3 (09:15) → 10 minutes.
    assert trade.duration_minutes == 10
