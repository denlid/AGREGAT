"""Phase 1.3 — Volume-aware slippage models (fixed / linear / sqrt)."""

from __future__ import annotations

import math

import pandas as pd
import pytest

from engine.core.backtest_core import BacktestCore
from engine.execution.execution import (
    SLIPPAGE_MODEL_FIXED,
    SLIPPAGE_MODEL_LINEAR,
    SLIPPAGE_MODEL_SQRT,
    ExecutionModel,
    compute_slippage_multiplier,
    slippage_model_to_int,
)
from engine.models.models import AssetConfig
from engine.strategy.context import Strategy


def test_compute_slippage_multiplier_fixed_is_one() -> None:
    assert compute_slippage_multiplier(SLIPPAGE_MODEL_FIXED, 100.0, 0.5) == 1.0


def test_compute_slippage_multiplier_zero_k_returns_one() -> None:
    assert compute_slippage_multiplier(SLIPPAGE_MODEL_LINEAR, 100.0, 0.0) == 1.0
    assert compute_slippage_multiplier(SLIPPAGE_MODEL_SQRT, 100.0, 0.0) == 1.0


def test_compute_slippage_multiplier_linear_scales_with_volume() -> None:
    one = compute_slippage_multiplier(SLIPPAGE_MODEL_LINEAR, 1.0, 0.1)
    hundred = compute_slippage_multiplier(SLIPPAGE_MODEL_LINEAR, 100.0, 0.1)
    assert one == pytest.approx(1.1)
    assert hundred == pytest.approx(11.0)


def test_compute_slippage_multiplier_sqrt_scales_with_root_volume() -> None:
    one = compute_slippage_multiplier(SLIPPAGE_MODEL_SQRT, 1.0, 0.1)
    hundred = compute_slippage_multiplier(SLIPPAGE_MODEL_SQRT, 100.0, 0.1)
    assert one == pytest.approx(1.1)
    assert hundred == pytest.approx(1.0 + 0.1 * math.sqrt(100.0))


def test_slippage_model_to_int_round_trips() -> None:
    assert slippage_model_to_int("fixed") == SLIPPAGE_MODEL_FIXED
    assert slippage_model_to_int("linear") == SLIPPAGE_MODEL_LINEAR
    assert slippage_model_to_int("sqrt") == SLIPPAGE_MODEL_SQRT
    with pytest.raises(ValueError):
        slippage_model_to_int("nope")  # type: ignore[arg-type]


def test_execution_model_persistence_round_trips_slippage_fields() -> None:
    em = ExecutionModel(
        profile="Custom",
        spread_points=2.0,
        slippage_points=0.5,
        slippage_model="sqrt",
        slippage_k=0.05,
    )
    out = em.to_dict()
    assert out["slippage_model"] == "sqrt"
    assert out["slippage_k"] == 0.05
    rebuilt = ExecutionModel.from_dict(out)
    assert rebuilt.slippage_model == "sqrt"
    assert rebuilt.slippage_k == 0.05


def test_execution_model_strict_rejects_unknown_model() -> None:
    with pytest.raises(ValueError):
        ExecutionModel.from_dict({"slippage_model": "exponential"}, strict=True)


def test_execution_model_lenient_falls_back_to_fixed() -> None:
    em = ExecutionModel.from_dict({"slippage_model": "exponential"})
    assert em.slippage_model == "fixed"


def test_adjust_fill_price_volume_aware_long() -> None:
    asset = AssetConfig(
        name="X",
        point_value=10.0,
        tick_size=1.0,
        buffer=0.0,
        max_lots_per_100k=100,
        currency="USD",
        spread_points=2.0,
        slippage_points=1.0,
        commission_per_lot=0.0,
    )
    em_fixed = ExecutionModel(profile="Custom")
    em_linear = ExecutionModel(
        profile="Custom", slippage_model="linear", slippage_k=0.1
    )
    quoted = 100.0
    p_fixed = em_fixed.adjust_fill_price(quoted, "long", asset, volume=10.0)
    p_linear = em_linear.adjust_fill_price(quoted, "long", asset, volume=10.0)
    # Linear with k=0.1 + volume=10 ⇒ slip multiplier = 2.0 (1 + 1)
    # base slip price = 1pt * tick=1 = 1.0
    # spread price = 2pt * tick=1 = 2.0
    # fixed long: quoted + spread + slip = 100 + 2 + 1 = 103
    # linear long: quoted + spread + slip*2 = 100 + 2 + 2 = 104
    assert p_fixed == pytest.approx(103.0)
    assert p_linear == pytest.approx(104.0)


# --- Engine integration: visual loop ---------------------------------


class _BuyLargeOnce(Strategy):
    def __init__(self, vol: float) -> None:
        self._sent = False
        self._vol = vol

    def on_candle(self, ctx) -> None:  # type: ignore[override]
        if not self._sent and ctx.position is None:
            ctx.buy_at_open(volume=self._vol, sl=None, tp=None, reason="t", tag="t")
            self._sent = True
        elif self._sent and ctx.position is not None and ctx._engine.current_idx >= 5:
            ctx.close()


def _df(n: int = 8) -> pd.DataFrame:
    rows = [
        (f"2024-01-02 10:{i:02d}", 100 + i, 100 + i + 1, 100 + i - 1, 100 + i)
        for i in range(n)
    ]
    df = pd.DataFrame(rows, columns=["time", "open", "high", "low", "close"])
    df["time"] = pd.to_datetime(df["time"])
    df["volume"] = 1.0
    return df


def _make_core(em: ExecutionModel) -> BacktestCore:
    core = BacktestCore()
    core.asset_config = AssetConfig(
        name="TEST",
        point_value=10.0,
        tick_size=1.0,
        buffer=0.0,
        max_lots_per_100k=100,
        currency="USD",
        spread_points=2.0,
        slippage_points=1.0,
        commission_per_lot=0.0,
    )
    core.execution_model = em
    core.initial_equity = 1_000_000.0
    core.equity = core.initial_equity
    core.max_equity = core.initial_equity
    return core


def test_engine_fixed_vs_sqrt_slippage_high_volume_pays_more() -> None:
    """A 10-lot trade with sqrt slippage should net less than fixed slip."""
    em_fixed = ExecutionModel(profile="Custom")
    em_sqrt = ExecutionModel(profile="Custom", slippage_model="sqrt", slippage_k=0.5)

    pnl_fixed = None
    pnl_sqrt = None
    for em, label in ((em_fixed, "fixed"), (em_sqrt, "sqrt")):
        core = _make_core(em)
        core.load_data(_df())
        core.load_strategy(_BuyLargeOnce(vol=10.0))
        for _ in range(20):
            core.step()
        assert len(core.trades) >= 1, f"{label}: no trade closed"
        net_pnl = core.equity - core.initial_equity
        if label == "fixed":
            pnl_fixed = net_pnl
        else:
            pnl_sqrt = net_pnl
    assert pnl_fixed is not None and pnl_sqrt is not None
    # sqrt slippage at volume=10 ⇒ multiplier = 1 + 0.5*sqrt(10) ≈ 2.58 ⇒
    # entry pays more, exit pays more ⇒ net PnL strictly worse than fixed.
    assert pnl_sqrt < pnl_fixed - 1e-9


def test_engine_fixed_default_unchanged() -> None:
    """Slippage model = fixed + k = 0 must reproduce legacy behaviour bit-exact."""
    em_legacy = ExecutionModel(profile="Custom")
    em_explicit = ExecutionModel(
        profile="Custom", slippage_model="fixed", slippage_k=0.0
    )
    core1 = _make_core(em_legacy)
    core2 = _make_core(em_explicit)
    for c in (core1, core2):
        c.load_data(_df())
        c.load_strategy(_BuyLargeOnce(vol=1.0))
        for _ in range(20):
            c.step()
    assert core1.equity == core2.equity
