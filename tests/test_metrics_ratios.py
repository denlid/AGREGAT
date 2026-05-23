"""Sprint 4.1 — Tests for engine.metrics.ratios."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from engine.metrics.ratios import (
    cagr,
    information_ratio,
    mar_ratio,
    skew_adjusted_sharpe,
    sterling_ratio,
    treynor_ratio,
)


def _equity(values: list[float], freq: str = "1D") -> pd.Series:
    """Helper: build a pd.Series with a DatetimeIndex."""
    idx = pd.date_range("2024-01-01", periods=len(values), freq=freq)
    return pd.Series(values, index=idx)


# -----------------------------------------------------------------------------
# CAGR
# -----------------------------------------------------------------------------
class TestCAGR:
    def test_zero_for_short_series(self) -> None:
        assert cagr(_equity([100.0])) == 0.0

    def test_zero_for_non_positive_initial(self) -> None:
        eq = _equity([0.0, 100.0, 110.0])
        assert cagr(eq) == 0.0

    def test_nan_for_non_positive_final(self) -> None:
        eq = _equity([100.0, 50.0, 0.0])
        assert math.isnan(cagr(eq))

    def test_doubling_in_one_year(self) -> None:
        # 365 days → 1.0 year span → equity 100 → 200 → CAGR ≈ 1.0
        eq = _equity([100.0] + [200.0] * 365)
        g = cagr(eq)
        assert 0.95 < g < 1.05


# -----------------------------------------------------------------------------
# Skew-adjusted Sharpe
# -----------------------------------------------------------------------------
class TestSkewAdjustedSharpe:
    def test_zero_returns_for_flat_equity(self) -> None:
        eq = _equity([100.0] * 30)
        assert skew_adjusted_sharpe(eq) == 0.0

    def test_matches_sharpe_for_symmetric_returns(self) -> None:
        # Symmetric (~zero skew) should keep adjustment near-zero
        rng = np.random.default_rng(42)
        rets = rng.normal(0, 0.01, size=252)
        eq_vals = (1.0 + rets).cumprod() * 100.0
        eq = _equity(list(eq_vals))
        from engine.analytics.equity_curve import sharpe_ratio

        sr = sharpe_ratio(eq)
        adj = skew_adjusted_sharpe(eq)
        # Adjustment should be small for symmetric data
        assert abs(adj - sr) < 0.5


# -----------------------------------------------------------------------------
# Treynor + Information ratio
# -----------------------------------------------------------------------------
class TestTreynorAndInformation:
    def test_treynor_zero_for_short_series(self) -> None:
        eq = _equity([100.0])
        bm = _equity([100.0])
        assert treynor_ratio(eq, bm) == 0.0

    def test_information_ratio_zero_for_identical_series(self) -> None:
        # Identical equity & benchmark → active return is zero everywhere
        # → tracking error is zero → IR collapses to 0.0
        rng = np.random.default_rng(0)
        rets = rng.normal(0.0005, 0.01, size=252)
        vals = list((1.0 + rets).cumprod() * 100.0)
        eq = _equity(vals)
        bm = _equity(vals)
        assert information_ratio(eq, bm) == 0.0

    def test_information_ratio_positive_for_outperformer(self) -> None:
        rng = np.random.default_rng(1)
        bm_rets = rng.normal(0.0001, 0.01, size=252)
        # Strategy beats benchmark by ~2bps/day with same vol
        eq_rets = bm_rets + 0.0002
        eq = _equity(list((1.0 + eq_rets).cumprod() * 100.0))
        bm = _equity(list((1.0 + bm_rets).cumprod() * 100.0))
        assert information_ratio(eq, bm) > 0.0


# -----------------------------------------------------------------------------
# Sterling + MAR
# -----------------------------------------------------------------------------
class TestSterlingAndMAR:
    def test_sterling_zero_for_monotonic_growth(self) -> None:
        # No drawdowns → Sterling falls back to 0.0
        eq = _equity([100.0, 110.0, 120.0, 140.0])
        assert sterling_ratio(eq) == 0.0

    def test_mar_zero_for_monotonic_growth(self) -> None:
        eq = _equity([100.0, 110.0, 120.0, 140.0])
        assert mar_ratio(eq) == 0.0

    def test_mar_finite_for_drawdown(self) -> None:
        eq = _equity([100.0] * 200 + [50.0] * 200 + [110.0] * 200)
        m = mar_ratio(eq)
        assert math.isfinite(m)
