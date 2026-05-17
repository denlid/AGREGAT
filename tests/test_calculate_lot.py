"""Risk-sizing tests for `Context.calculate_lot` (Phase 1).

Covers the new `absolute_max_lots` broker-side cap added to clamp the
calculated lot down to a realistic order book size, even when free margin
would permit far more.
"""

from __future__ import annotations

import math
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
    equity=100_000.0, max_lots_per_100k=10_000, point_value=1.0, min_lot=0.01, lot_step=0.01
):
    """Build an engine with a generous per-100k cap so the absolute cap drives clamping."""
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
    # Minimal candle so calculate_lot can record skipped trade rows.
    eng.ctx.candle = {"time": pd.Timestamp("2024-01-02 10:00")}
    return eng


def test_default_absolute_cap_clamps_oversized_lot(app):
    """High risk + tiny SL would size 1000 lots; default 100-lot cap clamps it."""
    eng = _build_engine(equity=100_000.0, max_lots_per_100k=10_000)
    # risk_pct=10% on $100k equity = $10,000 risk. SL=10 pts, point_value=1 →
    # raw lot = 10_000 / 10 = 1_000 lots.
    lot = eng.ctx.calculate_lot(sl_points=10.0, risk_pct=10.0)

    assert lot == pytest.approx(Context.DEFAULT_ABSOLUTE_MAX_LOTS)
    # Skip log must record the absolute-cap reason
    reasons = [s["reason"] for s in eng.skipped_trades]
    assert "ABS_LOT_LIMIT" in reasons


def test_absolute_cap_can_be_overridden_per_call(app):
    """Caller can pass a tighter cap (e.g. 5 lots) overriding the default."""
    eng = _build_engine(equity=100_000.0, max_lots_per_100k=10_000)
    lot = eng.ctx.calculate_lot(sl_points=10.0, risk_pct=10.0, absolute_max_lots=5.0)

    assert lot == pytest.approx(5.0)
    reasons = [s["reason"] for s in eng.skipped_trades]
    assert "ABS_LOT_LIMIT" in reasons


def test_absolute_cap_disabled_by_inf(app):
    """Passing `inf` disables the cap so per-100k clamping owns the limit."""
    eng = _build_engine(equity=100_000.0, max_lots_per_100k=10_000)
    # raw lot = 1000 lots; per-100k cap = 10_000 lots → no clamp; abs cap disabled
    lot = eng.ctx.calculate_lot(
        sl_points=10.0,
        risk_pct=10.0,
        absolute_max_lots=math.inf,
    )

    assert lot == pytest.approx(1000.0)
    reasons = [s["reason"] for s in eng.skipped_trades]
    assert "ABS_LOT_LIMIT" not in reasons


def test_per_100k_cap_runs_before_absolute_cap(app):
    """If per-100k clamp already shrinks lot below abs cap, abs cap is silent."""
    eng = _build_engine(equity=100_000.0, max_lots_per_100k=50)
    # raw lot = 1000; per-100k cap = 50; abs cap = 100 (default) → final = 50
    lot = eng.ctx.calculate_lot(sl_points=10.0, risk_pct=10.0)

    assert lot == pytest.approx(50.0)
    reasons = [s["reason"] for s in eng.skipped_trades]
    assert "LOT_LIMIT" in reasons
    assert "ABS_LOT_LIMIT" not in reasons


def test_normal_lot_under_both_caps_is_returned(app):
    """A modest lot below both caps passes through untouched."""
    eng = _build_engine(equity=100_000.0, max_lots_per_100k=10_000)
    # risk 1% on $100k = $1k; SL=100 pts → raw lot = 10
    lot = eng.ctx.calculate_lot(sl_points=100.0, risk_pct=1.0)

    assert lot == pytest.approx(10.0)
    assert eng.skipped_trades == []


# ---------------------------------------------------------------------------
# Visual ↔ vectorized parity for lot sizing
#
# The visual engine uses ``Context.calculate_lot`` while the vectorised
# engine uses ``engine.vectorized_engine._calc_lot``. The two
# implementations are duplicated today (Etap 6 will collapse them into a
# single ``engine/lot_sizing.py`` helper) — until then this test pins
# them to byte-equal behaviour so any future divergence is caught
# immediately.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "equity, sl_points, risk_pct, max_lots_per_100k, point_value, lot_step, min_lot",
    [
        # Normal case, no clamps
        (100_000.0, 100.0, 1.0, 10_000, 1.0, 0.01, 0.01),
        # Per-100k cap drives clamp
        (100_000.0, 10.0, 10.0, 50, 1.0, 0.01, 0.01),
        # Absolute cap drives clamp (raw lots = 1000, per-100k cap = 10000, abs = 100)
        (100_000.0, 10.0, 10.0, 10_000, 1.0, 0.01, 0.01),
        # min_lot floor — sub-min returns None / 0
        (100_000.0, 10_000.0, 0.001, 10_000, 1.0, 0.01, 0.01),
        # Different lot step (1.0)
        (100_000.0, 50.0, 2.0, 10_000, 1.0, 1.0, 1.0),
        # Larger point value
        (100_000.0, 100.0, 1.0, 10_000, 5.0, 0.01, 0.01),
    ],
)
def test_calculate_lot_parity_visual_vs_vectorized(
    app,
    equity,
    sl_points,
    risk_pct,
    max_lots_per_100k,
    point_value,
    lot_step,
    min_lot,
) -> None:
    """``Context.calculate_lot`` and ``vectorized_engine._calc_lot``
    must agree on every supported edge case.
    """
    from engine.execution.vectorized_engine import _calc_lot

    eng = _build_engine(
        equity=equity,
        max_lots_per_100k=max_lots_per_100k,
        point_value=point_value,
        lot_step=lot_step,
        min_lot=min_lot,
    )
    visual = eng.ctx.calculate_lot(sl_points=sl_points, risk_pct=risk_pct)
    vectorized = _calc_lot(
        equity=equity,
        risk_pct=risk_pct,
        sl_points=sl_points,
        asset=eng.asset_config,
        abs_max_lots=Context.DEFAULT_ABSOLUTE_MAX_LOTS,
    )
    # ``calculate_lot`` returns ``None`` when the trade should be skipped;
    # ``_calc_lot`` returns ``0.0`` for the same outcome. Normalise.
    visual_norm = 0.0 if visual is None else visual
    assert visual_norm == pytest.approx(vectorized), (
        f"Visual ({visual_norm}) / vectorized ({vectorized}) lot-sizing "
        f"disagree for equity={equity} sl={sl_points} risk={risk_pct}"
    )
