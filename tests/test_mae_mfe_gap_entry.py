"""Phase 1.6 — Gap-on-entry MAE/MFE uses close-only excursion."""

from __future__ import annotations

import pandas as pd
import pytest

from engine.core.backtest_core import BacktestCore
from engine.execution.execution import ExecutionModel
from engine.models.models import AssetConfig
from engine.strategy.context import Strategy


class _BuyOnce(Strategy):
    def __init__(self) -> None:
        self._sent = False

    def on_candle(self, ctx) -> None:  # type: ignore[override]
        if not self._sent and ctx.position is None:
            ctx.buy_at_open(volume=1.0, sl=None, tp=None, reason="t", tag="t")
            self._sent = True


def _make_core() -> BacktestCore:
    core = BacktestCore()
    core.asset_config = AssetConfig(
        name="TEST",
        point_value=10.0,
        tick_size=1.0,
        buffer=0.0,
        max_lots_per_100k=10,
        currency="USD",
        spread_points=0.0,
        slippage_points=0.0,
        commission_per_lot=0.0,
    )
    core.execution_model = ExecutionModel(profile="Optimistic")
    core.initial_equity = 100_000.0
    core.equity = core.initial_equity
    core.max_equity = core.initial_equity
    return core


def _df_gap_up_entry() -> pd.DataFrame:
    """First bar (signal) is normal; entry bar gaps UP — High >> Low.

    bar 0: signal — close=100
    bar 1: gap-up — open=110, high=120, low=99, close=115 (entry bar)
    bar 2-...: stable
    """
    rows = [
        ("2024-01-02 10:00", 100, 100.5, 99.5, 100, 1.0),
        ("2024-01-02 10:01", 110, 120, 99, 115, 1.0),
        ("2024-01-02 10:02", 115, 116, 114, 115, 1.0),
        ("2024-01-02 10:03", 115, 116, 114, 115, 1.0),
    ]
    df = pd.DataFrame(rows, columns=["time", "open", "high", "low", "close", "volume"])
    df["time"] = pd.to_datetime(df["time"])
    return df


def test_gap_up_entry_does_not_count_pre_entry_low_as_mae() -> None:
    """Entry bar low (99) is below entry (110) but happened *before* entry.

    Old behaviour would record MAE = 110 - 99 = 11 ($110 in dollars).
    New behaviour records MAE = max(0, entry - close) = max(0, 110 - 115) = 0.
    """
    core = _make_core()
    core.load_data(_df_gap_up_entry())
    core.load_strategy(_BuyOnce())
    # Step exactly twice: bar 0 fires signal, bar 1 fills the pending
    # and runs the entry-bar MAE/MFE update — we want to assert state
    # *before* any subsequent bar's High/Low is folded in.
    core.step()
    core.step()
    assert core.position is not None, "Trade did not open"
    pos = core.position
    # Entry happened at bar 1 with entry_price = 110 (open of bar 1).
    assert pos.entry_index == 1
    assert pos.entry_price == pytest.approx(110.0)
    # On the entry bar, MAE should NOT include the (entry - low) excursion.
    # close = 115 ⇒ favorable_close = 115 - 110 = 5 ⇒ MFE = 5; MAE = 0.
    assert pos.mae == pytest.approx(0.0), (
        f"Entry-bar MAE leaked pre-entry low: pos.mae={pos.mae}"
    )
    assert pos.mfe == pytest.approx(5.0)


def test_gap_down_entry_for_short_does_not_count_pre_entry_high() -> None:
    """Mirror image: short on a gap-down entry must not count pre-entry High."""

    class _SellOnce(Strategy):
        def __init__(self) -> None:
            self._sent = False

        def on_candle(self, ctx) -> None:  # type: ignore[override]
            if not self._sent and ctx.position is None:
                ctx.sell_at_open(volume=1.0, sl=None, tp=None, reason="t", tag="t")
                self._sent = True

    rows = [
        ("2024-01-02 10:00", 100, 100.5, 99.5, 100, 1.0),
        ("2024-01-02 10:01", 90, 101, 80, 85, 1.0),  # gap-down entry
        ("2024-01-02 10:02", 85, 86, 84, 85, 1.0),
    ]
    df = pd.DataFrame(rows, columns=["time", "open", "high", "low", "close", "volume"])
    df["time"] = pd.to_datetime(df["time"])

    core = _make_core()
    core.load_data(df)
    core.load_strategy(_SellOnce())
    # Two steps only — assert entry-bar excursion before bar 2 modifies it.
    core.step()
    core.step()
    assert core.position is not None
    pos = core.position
    # Entry on bar 1 at open=90, short. close=85 ⇒ favorable for short = 5.
    # high=101 ⇒ if entry-bar logic was wrong, MAE = 101-90 = 11 (false alarm).
    assert pos.entry_price == pytest.approx(90.0)
    assert pos.mae == pytest.approx(0.0)
    assert pos.mfe == pytest.approx(5.0)


def test_subsequent_bar_uses_normal_high_low_envelope() -> None:
    """After the entry bar the MAE/MFE update is back to High/Low."""
    rows = [
        ("2024-01-02 10:00", 100, 100.5, 99.5, 100, 1.0),
        ("2024-01-02 10:01", 100, 101, 100, 100, 1.0),  # entry bar
        ("2024-01-02 10:02", 100, 105, 95, 100, 1.0),  # adverse low + favorable high
        ("2024-01-02 10:03", 100, 100.5, 99.5, 100, 1.0),
    ]
    df = pd.DataFrame(rows, columns=["time", "open", "high", "low", "close", "volume"])
    df["time"] = pd.to_datetime(df["time"])

    core = _make_core()
    core.load_data(df)
    core.load_strategy(_BuyOnce())
    for _ in range(6):
        core.step()
    pos = core.position
    assert pos is not None
    # Bar 2 has low=95, high=105, entry=100 ⇒ MAE=5, MFE=5.
    assert pos.mae == pytest.approx(5.0)
    assert pos.mfe == pytest.approx(5.0)
