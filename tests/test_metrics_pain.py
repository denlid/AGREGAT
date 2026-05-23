"""Sprint 4.1 — Tests for engine.metrics.pain (Tail Ratio, Gain-to-Pain)."""

from __future__ import annotations

import math

import numpy as np

from engine.metrics.pain import gain_to_pain, tail_ratio


class TestTailRatio:
    def test_empty_array_is_nan(self) -> None:
        assert math.isnan(tail_ratio([]))

    def test_no_losses_is_zero(self) -> None:
        assert tail_ratio([1.0, 2.0, 3.0]) == 0.0

    def test_symmetric_distribution_around_one(self) -> None:
        rng = np.random.default_rng(0)
        pnls = rng.normal(0.0, 1.0, size=10_000)
        # Symmetric → ratio close to 1.0
        assert 0.85 < tail_ratio(pnls) < 1.15

    def test_right_skew_above_one(self) -> None:
        # 100-item asymmetric distribution: small symmetric body
        # (1s and -1s) with a heavy right tail (top 5 items > 5).
        # 5th-pctile lands at −1, 95th-pctile at 5 → ratio = 5.0.
        pnls = np.concatenate(
            [
                np.full(50, 1.0),
                np.full(45, -1.0),
                np.array([5.0, 6.0, 8.0, 10.0, 15.0]),
            ]
        )
        assert tail_ratio(pnls) > 1.0


class TestGainToPain:
    def test_empty_array_is_nan(self) -> None:
        assert math.isnan(gain_to_pain([]))

    def test_no_losses_is_zero(self) -> None:
        assert gain_to_pain([1.0, 2.0, 3.0]) == 0.0

    def test_symmetric_zero(self) -> None:
        # Sum of gains = sum of losses → ratio is 0 over abs(losses)
        pnls = [1.0, 2.0, -1.0, -2.0]
        assert gain_to_pain(pnls) == 0.0

    def test_basic(self) -> None:
        # Gains 6, losses -2 → net 4 / pain 2 = 2.0
        pnls = [3.0, 3.0, -1.0, -1.0]
        assert gain_to_pain(pnls) == 2.0
