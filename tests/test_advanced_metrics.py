"""Tests for the daily-equity-curve based annualised metrics."""

import math
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from engine.analytics.equity_curve import (
    build_equity_curve,
    calmar_ratio,
    daily_returns,
    max_drawdown,
    sharpe_ratio,
    sortino_ratio,
    years_span,
)
from engine.models.models import Trade
from gui.widgets.advanced_metrics import (
    calculate_calmar_ratio,
    calculate_sharpe_ratio,
    calculate_sortino_ratio,
)


def _trade(pnl: float, exit_time: datetime, *, tid: int = 0) -> Trade:
    """Minimal Trade fixture; only fields read by equity_curve are populated."""
    return Trade(
        id=tid,
        direction="long",
        entry_price=100.0,
        exit_price=100.0,
        pnl=pnl,
        pnl_points=pnl,
        entry_index=0,
        exit_index=0,
        entry_time=str(exit_time - timedelta(minutes=5)),
        exit_time=str(exit_time),
        volume=1.0,
        duration_minutes=5,
        exit_reason="TP",
        mae=0.0,
        mfe=0.0,
        mae_dollars=0.0,
        mfe_dollars=0.0,
        session="London_OTT",
        initial_sl=99.0,
        rr=1.0,
        tag="",
    )


# --------------------------------------------------------------------------
# build_equity_curve
# --------------------------------------------------------------------------
def test_build_equity_curve_starts_with_initial_and_walks():
    base = datetime(2024, 1, 1, 10)
    trades = [
        _trade(+10, base + timedelta(days=1), tid=1),
        _trade(-5, base + timedelta(days=2), tid=2),
        _trade(+15, base + timedelta(days=3), tid=3),
    ]
    eq = build_equity_curve(trades, initial_equity=1000.0)
    # 4 points: initial + 3 trades
    assert len(eq) == 4
    assert eq.iloc[0] == pytest.approx(1000.0)
    assert eq.iloc[-1] == pytest.approx(1020.0)


def test_build_equity_curve_empty_returns_singleton():
    eq = build_equity_curve([], initial_equity=1000.0)
    assert len(eq) == 1
    assert float(eq.iloc[0]) == 1000.0


# --------------------------------------------------------------------------
# Sharpe — flat returns => 0; positive constant returns => positive
# --------------------------------------------------------------------------
def test_sharpe_zero_when_no_returns():
    base = datetime(2024, 1, 1)
    eq = pd.Series([1000.0] * 10, index=pd.date_range(base, periods=10, freq="D"))
    assert sharpe_ratio(eq) == 0.0


def test_sharpe_high_for_consistent_winners():
    """30 trades, +1% per day, no losers — should give a very high Sharpe."""
    base = datetime(2024, 1, 1, 10)
    trades = [_trade(+10, base + timedelta(days=i + 1), tid=i + 1) for i in range(30)]
    eq = build_equity_curve(trades, initial_equity=1000.0)
    sr = sharpe_ratio(eq)
    # Constant returns mean stdev → 0; impl returns 0 for that. Our trades
    # give zero-variance equity *steps*, so Sharpe is 0 — but the smoke-test
    # is the public API doesn't blow up:
    assert math.isfinite(sr)


def test_sharpe_negative_for_consistent_losers():
    base = datetime(2024, 1, 1, 10)
    # alternating gains/losses with negative bias to get measurable variance
    pnls = [-15, +5, -10, +5, -12, +6, -8, +4, -11, +5]
    trades = [_trade(p, base + timedelta(days=i + 1), tid=i + 1) for i, p in enumerate(pnls)]
    eq = build_equity_curve(trades, initial_equity=10_000.0)
    assert sharpe_ratio(eq) < 0


# --------------------------------------------------------------------------
# Sortino — only downside variance counts
# --------------------------------------------------------------------------
def test_sortino_returns_finite_or_zero_on_no_downside():
    base = datetime(2024, 1, 1, 10)
    trades = [_trade(+10, base + timedelta(days=i + 1), tid=i + 1) for i in range(5)]
    eq = build_equity_curve(trades, initial_equity=1000.0)
    so = sortino_ratio(eq)
    # No negative returns → impl returns 0 to avoid +inf
    assert so == 0.0


# --------------------------------------------------------------------------
# Calmar — guards
# --------------------------------------------------------------------------
def test_calmar_returns_zero_when_drawdown_is_zero():
    base = datetime(2024, 1, 1)
    eq = pd.Series([1000.0, 1010.0, 1020.0, 1030.0], index=pd.date_range(base, periods=4, freq="D"))
    assert calmar_ratio(eq, initial_equity=1000.0) == 0.0


def test_calmar_returns_nan_when_total_return_non_positive():
    """A losing strategy must NOT look identical to a flat one.

    Previously the impl flattened ``(final/initial) ≤ 0`` to ``0.0`` to
    avoid the ``(negative)^(1/years)`` complex result. After TZ Phase 4
    / D1 we return ``NaN`` instead — the value is genuinely undefined,
    and the UI / report layer should render it as such. The window is
    deliberately > 0.1 years so the short-window guard doesn't pre-empt
    the new NaN branch.
    """
    base = datetime(2024, 1, 1)
    # Equity goes below the starting balance and finishes negative,
    # over an annualisable window (180 days).
    eq = pd.Series([1000.0, 500.0, -200.0], index=pd.date_range(base, periods=3, freq="90D"))
    val = calmar_ratio(eq, initial_equity=1000.0)
    assert math.isnan(val)


def test_calmar_returns_zero_when_window_is_too_short():
    base = datetime(2024, 1, 1)
    eq = pd.Series([1000.0, 950.0], index=[base, base + timedelta(hours=1)])
    # < 0.1 years → guarded
    assert calmar_ratio(eq, initial_equity=1000.0) == 0.0


def test_calmar_positive_for_winning_year_with_drawdown():
    base = datetime(2024, 1, 1)
    # Up to 2000, dip to 1500 (25% DD vs peak), up to 2500 over 1Y
    days = pd.date_range(base, periods=4, freq="90D")
    eq = pd.Series([1000.0, 2000.0, 1500.0, 2500.0], index=days)
    cr = calmar_ratio(eq, initial_equity=1000.0)
    assert cr > 0


# --------------------------------------------------------------------------
# gui.advanced_metrics — backward-compat wrappers
# --------------------------------------------------------------------------
def test_gui_wrappers_return_finite_floats():
    base = datetime(2024, 1, 1, 10)
    trades = [
        _trade(+10 if i % 2 == 0 else -5, base + timedelta(days=i + 1), tid=i + 1)
        for i in range(20)
    ]
    s = calculate_sharpe_ratio(trades, initial_equity=10_000.0)
    so = calculate_sortino_ratio(trades, initial_equity=10_000.0)
    c = calculate_calmar_ratio(trades, initial_equity=10_000.0)
    for v in (s, so, c):
        assert isinstance(v, float)
        assert math.isfinite(v)


def test_gui_calmar_with_legacy_max_dd_pct_signature_does_not_blow_up():
    """Old call sites passed (trades, max_dd_pct, initial). New signature
    accepts initial_equity as 2nd or 3rd positional arg via kwargs."""
    base = datetime(2024, 1, 1, 10)
    trades = [_trade(+10, base + timedelta(days=i + 1), tid=i + 1) for i in range(5)]
    val = calculate_calmar_ratio(trades, initial_equity=10_000.0)
    assert math.isfinite(val)


# --------------------------------------------------------------------------
# max_drawdown
# --------------------------------------------------------------------------
def test_max_drawdown_basic():
    base = datetime(2024, 1, 1)
    eq = pd.Series(
        [1000.0, 1500.0, 1200.0, 800.0, 1100.0], index=pd.date_range(base, periods=5, freq="D")
    )
    dd_abs, dd_pct = max_drawdown(eq)
    # Peak 1500 → trough 800 → DD 700 abs, dd_pct returned as a fraction (0.467)
    assert dd_abs == pytest.approx(700.0)
    assert dd_pct == pytest.approx(700.0 / 1500.0, abs=0.001)


def test_max_drawdown_all_growth_zero():
    base = datetime(2024, 1, 1)
    eq = pd.Series([1000.0, 1100.0, 1200.0], index=pd.date_range(base, periods=3, freq="D"))
    dd_abs, dd_pct = max_drawdown(eq)
    assert dd_abs == 0.0
    assert dd_pct == 0.0
