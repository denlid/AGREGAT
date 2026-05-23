"""ExecutionModel — fill/close price adjustments and round-trip costs."""

from __future__ import annotations

import pytest

from engine.execution.execution import ExecutionModel
from engine.models.models import AssetConfig


def _asset(
    spread: float = 1.0,
    commission: float = 5.0,
    slippage: float = 0.5,
    point_value: float = 1.0,
    tick_size: float = 1.0,
) -> AssetConfig:
    return AssetConfig(
        name="TEST",
        point_value=point_value,
        tick_size=tick_size,
        buffer=0.0,
        max_lots_per_100k=10,
        currency="USD",
        spread_points=spread,
        commission_per_lot=commission,
        slippage_points=slippage,
    )


# --------------------------------------------------------------------------
# Optimistic profile — all costs zero regardless of asset config
# --------------------------------------------------------------------------
def test_optimistic_profile_returns_quoted_price():
    em = ExecutionModel(profile="Optimistic")
    asset = _asset()  # asset has costs but profile zeroes them
    assert em.adjust_fill_price(100.0, "long", asset) == 100.0
    assert em.adjust_fill_price(100.0, "short", asset) == 100.0
    assert em.adjust_close_price(100.0, "long", asset) == 100.0
    assert em.adjust_close_price(100.0, "short", asset) == 100.0


def test_optimistic_costs_are_zero():
    em = ExecutionModel(profile="Optimistic")
    asset = _asset()
    costs = em.costs_for_round_trip(volume=2.0, asset=asset)
    assert costs.spread_cost == 0.0
    assert costs.slippage_cost == 0.0
    assert costs.commission_cost == 0.0
    assert costs.total == 0.0


# --------------------------------------------------------------------------
# Realistic profile — pulls from AssetConfig
# Bid convention: charts are Bid-priced, Ask = Bid + spread.
# --------------------------------------------------------------------------
def test_realistic_long_pays_full_spread_short_hits_bid():
    asset = _asset(spread=2.0, slippage=0.0)
    em = ExecutionModel(profile="Realistic")
    # Long entry buys the Ask: quoted + full spread = 100 + 2
    assert em.adjust_fill_price(100.0, "long", asset) == pytest.approx(102.0)
    # Short entry sells at Bid (= chart): no spread applied
    assert em.adjust_fill_price(100.0, "short", asset) == pytest.approx(100.0)
    # Long close sells at Bid (= chart): no spread
    assert em.adjust_close_price(100.0, "long", asset) == pytest.approx(100.0)
    # Short close buys the Ask: quoted + full spread = 100 + 2
    assert em.adjust_close_price(100.0, "short", asset) == pytest.approx(102.0)


def test_realistic_slippage_compounds_with_spread():
    asset = _asset(spread=1.0, slippage=0.5)
    em = ExecutionModel(profile="Realistic")
    # Long entry: 100 + 1.0 (full spread) + 0.5 (slip) = 101.5
    assert em.adjust_fill_price(100.0, "long", asset) == pytest.approx(101.5)
    # Short close: 100 + 1.0 (full spread) + 0.5 (slip) = 101.5
    assert em.adjust_close_price(100.0, "short", asset) == pytest.approx(101.5)


def test_tick_size_scales_the_bump():
    """Slippage/spread are in *points*; multiplied by `tick_size` for the price bump."""
    asset = _asset(spread=2.0, slippage=0.0, tick_size=0.1)
    em = ExecutionModel(profile="Realistic")
    # Long fill: 100 + 2.0 (full spread in points) * 0.1 (tick_size) = 100.2
    assert em.adjust_fill_price(100.0, "long", asset) == pytest.approx(100.2)


def test_long_close_and_short_entry_are_chart_exact():
    """Bid model invariant: long exits and short entries fill at the chart price
    (only slippage adds to/subtracts from the quoted Bid; no spread)."""
    asset = _asset(spread=4.0, slippage=0.0)
    em = ExecutionModel(profile="Realistic")
    assert em.adjust_fill_price(100.0, "short", asset) == pytest.approx(100.0)
    assert em.adjust_close_price(100.0, "long", asset) == pytest.approx(100.0)

    asset_with_slip = _asset(spread=4.0, slippage=0.25)
    em2 = ExecutionModel(profile="Realistic")
    # Short entry: 100 - 0.25 (slip only)
    assert em2.adjust_fill_price(100.0, "short", asset_with_slip) == pytest.approx(99.75)
    # Long close: 100 - 0.25 (slip only)
    assert em2.adjust_close_price(100.0, "long", asset_with_slip) == pytest.approx(99.75)


def test_realistic_round_trip_costs_sum_correctly():
    asset = _asset(spread=2.0, slippage=0.5, commission=4.0, point_value=10.0)
    em = ExecutionModel(profile="Realistic")
    costs = em.costs_for_round_trip(volume=2.0, asset=asset)
    # spread_cost = 2 pts * 2 vol * 10 pv = 40
    assert costs.spread_cost == pytest.approx(40.0)
    # slip 0.5 pts * 2 (both legs) * 2 vol * 10 pv = 20
    assert costs.slippage_cost == pytest.approx(20.0)
    # commission 4 / lot * 2 vol = 8
    assert costs.commission_cost == pytest.approx(8.0)
    assert costs.total == pytest.approx(68.0)


# --------------------------------------------------------------------------
# Custom profile — overrides take priority
# --------------------------------------------------------------------------
def test_custom_overrides_beat_asset_config():
    asset = _asset(spread=10.0, slippage=10.0, commission=100.0)
    em = ExecutionModel(
        profile="Custom",
        spread_points=2.0,
        commission_per_lot=1.0,
        slippage_points=0.0,
    )
    # Long entry: 100 + 2.0 (full custom spread) = 102.0
    assert em.adjust_fill_price(100.0, "long", asset) == pytest.approx(102.0)
    costs = em.costs_for_round_trip(volume=1.0, asset=asset)
    assert costs.commission_cost == pytest.approx(1.0)
    assert costs.slippage_cost == pytest.approx(0.0)


def test_custom_with_none_falls_back_to_asset():
    asset = _asset(spread=4.0, slippage=1.0, commission=2.0)
    em = ExecutionModel(
        profile="Custom", spread_points=None, commission_per_lot=None, slippage_points=None
    )
    # all None → use asset values
    costs = em.costs_for_round_trip(volume=1.0, asset=asset)
    assert costs.commission_cost == pytest.approx(2.0)


# --------------------------------------------------------------------------
# Persistence (round-trip)
# --------------------------------------------------------------------------
def test_to_dict_and_from_dict_round_trip():
    em = ExecutionModel(
        profile="Custom", spread_points=3.0, commission_per_lot=2.5, slippage_points=0.25
    )
    data = em.to_dict()
    em2 = ExecutionModel.from_dict(data)
    assert em2.profile == "Custom"
    assert em2.spread_points == 3.0
    assert em2.commission_per_lot == 2.5
    assert em2.slippage_points == 0.25


def test_from_dict_with_unknown_profile_falls_back_to_optimistic():
    em = ExecutionModel.from_dict({"profile": "Foo"})
    assert em.profile == "Optimistic"
