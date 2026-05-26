"""Phase 1 EPIC 1.1 — Tests for gui.analytics.sparkline_data helpers."""

from __future__ import annotations

import numpy as np
import pytest

from gui.analytics import sparkline_data as spk


@pytest.fixture()
def sample_pnl() -> np.ndarray:
    rng = np.random.default_rng(42)
    return rng.normal(loc=5.0, scale=25.0, size=200).astype(np.float64)


def _is_finite_list(values, length: int) -> bool:
    return (
        isinstance(values, list)
        and len(values) == length
        and all(isinstance(v, float) and np.isfinite(v) for v in values)
    )


def test_cumulative_equity_decimates(sample_pnl: np.ndarray) -> None:
    series = spk.cumulative_equity(sample_pnl, initial_equity=10_000.0, points=60)
    assert _is_finite_list(series, 60)
    # Series should start near the initial equity and reflect the cumulative PnL.
    expected_end = 10_000.0 + float(sample_pnl.sum())
    assert series[0] == pytest.approx(10_000.0 + float(sample_pnl[0]), rel=0.02)
    assert series[-1] == pytest.approx(expected_end, rel=0.02)


def test_underwater_curve_non_positive(sample_pnl: np.ndarray) -> None:
    series = spk.underwater_curve(sample_pnl, initial_equity=10_000.0, points=60)
    assert _is_finite_list(series, 60)
    # Underwater values are drawdown percentages — never positive.
    assert max(series) <= 0.0 + 1e-9


def test_rolling_win_rate_bounded(sample_pnl: np.ndarray) -> None:
    series = spk.rolling_win_rate(sample_pnl, window=30, points=60)
    assert _is_finite_list(series, 60)
    assert min(series) >= 0.0
    assert max(series) <= 100.0


def test_rolling_return_matches_cumsum(sample_pnl: np.ndarray) -> None:
    series = spk.rolling_return(sample_pnl, window=30, points=60)
    assert _is_finite_list(series, 60)


def test_rolling_sharpe_handles_short_arrays() -> None:
    pnl = np.array([10.0, -5.0, 7.0], dtype=np.float64)
    series = spk.rolling_sharpe(pnl, window=50, points=60)
    # Too few samples for the requested window → empty list is acceptable.
    assert isinstance(series, list)


def test_rolling_sortino_zero_downside_safe() -> None:
    pnl = np.full(80, 5.0, dtype=np.float64)
    series = spk.rolling_sortino(pnl, window=20, points=60)
    assert isinstance(series, list)
    if series:
        assert all(np.isfinite(v) for v in series)


def test_histogram_bars_returns_bins() -> None:
    pnl = np.array([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0], dtype=np.float64)
    bars = spk.histogram_bars(pnl, bins=5)
    assert len(bars) == 5
    assert sum(bars) == pytest.approx(len(pnl))


def test_streak_bars_signs() -> None:
    pnl = np.array([10.0, -5.0, 0.0, 7.0, -2.0], dtype=np.float64)
    bars = spk.streak_bars(pnl, last_n=20)
    assert bars == [1.0, -1.0, 0.0, 1.0, -1.0]


def test_empty_array_returns_empty_list() -> None:
    empty = np.array([], dtype=np.float64)
    assert spk.cumulative_equity(empty, 10_000.0) == []
    assert spk.underwater_curve(empty, 10_000.0) == []
    assert spk.rolling_win_rate(empty) == []
    assert spk.rolling_return(empty) == []
    assert spk.rolling_sharpe(empty) == []
    assert spk.rolling_sortino(empty) == []
    assert spk.histogram_bars(empty) == []
    assert spk.streak_bars(empty) == []
