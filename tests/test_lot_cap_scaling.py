"""Tests for the per-100k lot cap scaling rule.

The LINEAR LOGIC CORE specification says: "Lot Cap = 12 lots per
$100,000 of equity. So at $200,000 the cap is 24 lots, at $50k it's
6 lots." This is implemented in ``Context.calculate_lot`` as::

    max_lots = asset.max_lots_per_100k * (equity / 100_000.0)

and these tests lock the invariant in so a future refactor cannot
silently flip the formula to a fixed cap.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from engine.strategy.context import Context, Strategy
from engine.core.backtest_core import BacktestCore
from engine.execution.execution import ExecutionModel
from engine.models.models import AssetConfig


def _asset(*, max_lots_per_100k: int, absolute_max_lots: float | None = None) -> AssetConfig:
    a = AssetConfig(
        name="X",
        point_value=1.0,
        tick_size=1.0,
        buffer=0.0,
        max_lots_per_100k=max_lots_per_100k,
        currency="USD",
        min_lot=0.01,
        lot_step=0.01,
        spread_points=0.0,
        commission_per_lot=0.0,
    )
    if absolute_max_lots is not None:
        # Some asset configs expose an absolute broker cap as well.
        if hasattr(a, "absolute_max_lots"):
            a.absolute_max_lots = absolute_max_lots
    return a


def _ctx(*, equity: float, max_lots_per_100k: int) -> Context:
    eng = BacktestCore()
    eng.asset_config = _asset(max_lots_per_100k=max_lots_per_100k)
    eng.execution_model = ExecutionModel(profile="Optimistic")
    eng.equity = equity
    eng.initial_equity = equity
    eng.max_equity = equity
    eng.risk_pct = 1.0
    eng.skipped_trades = []
    eng.load_strategy(Strategy())
    return eng.ctx


def test_per100k_cap_scales_at_100k():
    """$100k equity with cap=12: a runaway 1pt SL → calc_lot=1000, clamps to 12."""
    ctx = _ctx(equity=100_000.0, max_lots_per_100k=12)
    lot = ctx.calculate_lot(sl_points=1.0, risk_pct=1.0)
    assert lot == 12.0, lot


def test_per100k_cap_scales_at_200k():
    """LINEAR LOGIC CORE spec example: $200k → cap = 24 lots."""
    ctx = _ctx(equity=200_000.0, max_lots_per_100k=12)
    lot = ctx.calculate_lot(sl_points=1.0, risk_pct=1.0)
    assert lot == 24.0, lot


def test_per100k_cap_scales_at_50k():
    """$50k equity with cap=12: half-account → 6 lots."""
    ctx = _ctx(equity=50_000.0, max_lots_per_100k=12)
    lot = ctx.calculate_lot(sl_points=1.0, risk_pct=1.0)
    assert lot == 6.0, lot


def test_per100k_cap_scales_at_350k():
    """Non-round equity: $350k → cap = 12 * 3.5 = 42 lots."""
    ctx = _ctx(equity=350_000.0, max_lots_per_100k=12)
    lot = ctx.calculate_lot(sl_points=1.0, risk_pct=1.0)
    assert lot == 42.0, lot


def test_per100k_cap_strict_cancels_at_200k_not_clamps():
    """Strict mode at $200k: same overflow → cancellation (return None)."""
    ctx = _ctx(equity=200_000.0, max_lots_per_100k=12)
    lot = ctx.calculate_lot(sl_points=1.0, risk_pct=1.0, strict=True)
    assert lot is None


def test_under_cap_returns_calculated_lot():
    """When the calculated lot is below the cap, return it unchanged.

    With $100k, 1% risk = $1000; 100pt SL @ $1/pt = $100 per lot → 10
    lots. Cap is 12, so the calculated 10 wins.
    """
    ctx = _ctx(equity=100_000.0, max_lots_per_100k=12)
    lot = ctx.calculate_lot(sl_points=100.0, risk_pct=1.0)
    assert lot == 10.0, lot
