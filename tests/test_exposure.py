"""Tests for engine.analytics.exposure (Phase 4.4)."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from engine.analytics.exposure import (
    exposure_heatmap_from_bar_array,
    exposure_heatmap_from_trades,
)


@dataclass
class _Trade:
    entry_time: str
    exit_time: str


def test_heatmap_shape_is_seven_by_twentyfour():
    trades = [_Trade("2024-01-08 10:00", "2024-01-08 12:00")]
    heat = exposure_heatmap_from_trades(trades)
    assert heat.fraction.shape == (7, 24)
    assert heat.in_position.shape == (7, 24)
    assert heat.total_bars.shape == (7, 24)


def test_single_trade_in_one_bucket_lights_only_that_bucket():
    """A 60-min trade on Monday at 10:00 → 60 minutes in (Mon, 10) only."""
    trades = [_Trade("2024-01-08 10:00", "2024-01-08 11:00")]
    heat = exposure_heatmap_from_trades(trades)
    # Monday = 0, hour 10
    assert heat.in_position[0, 10] == 60
    # Everything else is zero.
    mask = np.ones_like(heat.in_position, dtype=bool)
    mask[0, 10] = False
    assert heat.in_position[mask].sum() == 0


def test_heatmap_fraction_is_one_when_all_data_is_in_position():
    """Synthetic case: 30 minutes of bars, trade covers all of them."""
    idx = pd.date_range("2024-01-08 10:00", periods=30, freq="1min")
    in_pos = np.ones(30, dtype=bool)
    heat = exposure_heatmap_from_bar_array(idx, in_pos)
    # Mon 10:00 bucket should be 30/30 = 1.0
    assert heat.fraction[0, 10] == 1.0


def test_heatmap_fraction_is_partial_when_only_half_in_position():
    idx = pd.date_range("2024-01-08 10:00", periods=60, freq="1min")
    in_pos = np.zeros(60, dtype=bool)
    in_pos[:30] = True
    heat = exposure_heatmap_from_bar_array(idx, in_pos)
    assert abs(heat.fraction[0, 10] - 0.5) < 1e-9


def test_heatmap_zero_in_buckets_with_no_data():
    """Buckets that have zero ``total_bars`` must report fraction=0
    (not 1, not NaN) regardless of ``in_position`` values."""
    idx = pd.date_range("2024-01-08 10:00", periods=30, freq="1min")
    heat = exposure_heatmap_from_bar_array(idx, np.zeros(30, dtype=bool))
    # All non-Mon-10 buckets must be 0.
    assert (heat.fraction != 0).sum() == 0


def test_heatmap_handles_empty_trades_gracefully():
    heat = exposure_heatmap_from_trades([])
    assert heat.fraction.sum() == 0


def test_heatmap_handles_invalid_trade_timestamps():
    """Bad trades must be skipped, not crash the function."""
    bad = _Trade("invalid", "also invalid")
    heat = exposure_heatmap_from_trades([bad])
    assert heat.fraction.sum() == 0


def test_heatmap_fraction_is_clipped_to_unit_interval():
    """Even if duplicate timestamps cause the numerator to spike,
    the fraction must remain ≤ 1.0 (no rendering nasties)."""
    trades = [
        _Trade("2024-01-08 10:00", "2024-01-08 11:00"),
        _Trade("2024-01-08 10:00", "2024-01-08 11:00"),
    ]
    heat = exposure_heatmap_from_trades(trades)
    assert heat.fraction.max() <= 1.0
