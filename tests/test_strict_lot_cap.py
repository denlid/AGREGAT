"""Tests for ``Context.calculate_lot(strict=True)`` introduced in stage 2.

When ``strict=True``:

* A lot that exceeds ``max_lots_per_100k`` returns ``None`` (instead of being
  clamped) and logs ``LOT_LIMIT_STRICT`` to ``engine.skipped_trades``.
* Likewise the absolute broker-side cap returns ``None`` with reason
  ``ABS_LOT_LIMIT_STRICT``.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pandas as pd
import pytest

from engine.backtest_engine import BacktestEngine  # noqa: E402
from engine.strategy.context import Context  # noqa: E402
from engine.models.models import AssetConfig  # noqa: E402


@pytest.fixture
def app():
    from PySide6.QtCore import QCoreApplication

    inst = QCoreApplication.instance()
    return inst if inst is not None else QCoreApplication([])


def _build_engine(
    equity=100_000.0, max_lots_per_100k=12, point_value=1.0, min_lot=0.01, lot_step=0.01
):
    eng = BacktestEngine()
    eng.asset_config = AssetConfig(
        name="TEST",
        point_value=point_value,
        tick_size=0.01,
        buffer=0.0,
        max_lots_per_100k=max_lots_per_100k,
        currency="USD",
        min_lot=min_lot,
        lot_step=lot_step,
    )
    eng.initial_equity = equity
    eng.equity = equity
    eng.max_equity = equity
    eng.risk_pct = 1.0
    eng.skipped_trades = []
    eng.ctx.candle = {"time": pd.Timestamp("2024-01-02 10:00")}
    return eng


def test_strict_per100k_cap_cancels_trade(app):
    """Strict mode: per-100k cap → ``None`` + LOT_LIMIT_STRICT."""
    eng = _build_engine(equity=100_000.0, max_lots_per_100k=12)
    # 1% of 100k = $1k risk; 5pt SL → raw 200 lots, far above cap.
    lot = eng.ctx.calculate_lot(sl_points=5.0, risk_pct=1.0, strict=True)
    assert lot is None
    reasons = [s["reason"] for s in eng.skipped_trades]
    assert "LOT_LIMIT_STRICT" in reasons


def test_strict_absolute_cap_cancels_trade(app):
    """Strict mode: absolute cap → ``None`` + ABS_LOT_LIMIT_STRICT."""
    eng = _build_engine(equity=100_000.0, max_lots_per_100k=10_000)
    # 10% / 5pt = 2_000 lots → within per-100k cap, hits absolute cap.
    lot = eng.ctx.calculate_lot(
        sl_points=5.0,
        risk_pct=10.0,
        strict=True,
        absolute_max_lots=Context.DEFAULT_ABSOLUTE_MAX_LOTS,
    )
    assert lot is None
    reasons = [s["reason"] for s in eng.skipped_trades]
    assert "ABS_LOT_LIMIT_STRICT" in reasons


def test_strict_under_cap_returns_lot(app):
    """Strict mode under both caps still returns the calculated lot."""
    eng = _build_engine(equity=100_000.0, max_lots_per_100k=1000)
    # 1% / 100pt = 10 lots → well under 1000 cap.
    lot = eng.ctx.calculate_lot(sl_points=100.0, risk_pct=1.0, strict=True)
    assert lot == pytest.approx(10.0)
    assert eng.skipped_trades == []


def test_non_strict_clamps_as_before(app):
    """Default non-strict behaviour is unchanged: clamp + LOT_LIMIT."""
    eng = _build_engine(equity=100_000.0, max_lots_per_100k=12)
    lot = eng.ctx.calculate_lot(sl_points=5.0, risk_pct=1.0)
    # Should be clamped to 12 lots (per-100k cap).
    assert lot == pytest.approx(12.0)
    reasons = [s["reason"] for s in eng.skipped_trades]
    assert "LOT_LIMIT" in reasons
