"""TZ Phase 4 — Metrics centralisation + risk refinements.

Covers:
  * 4.1 ``DrawdownTracker`` is the single source of truth for peak / max DD,
    used by both the visual and the vectorised engine.
  * 4.2 Visual ↔ Quant stats parity: ``engine.stats`` and ``engine.drawdown``
    after ``run_vectorized()`` match what the on-trade accumulators would
    produce if fed the same trade list one by one (the visual loop's
    contract).
  * 4.3 / D1 ``calmar_ratio`` returns ``NaN`` for ``total_return ≤ 0``.
  * 4.4 / D2 ``sharpe_ratio`` / ``sortino_ratio`` log a warning at
    ``len(daily_returns) < 30``.
  * 4.5 / D4 ``monte_carlo.bootstrap_pnl`` accepts ``block_size`` and
    delivers wider DD-tail CIs than the iid baseline on autocorrelated PnL.
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from engine.strategy.context import Strategy
from engine.core.backtest_core import BacktestCore
from engine.analytics.equity_curve import (
    MIN_RETURNS_FOR_RATIO,
    build_equity_curve,
    calmar_ratio,
    sharpe_ratio,
    sortino_ratio,
)
from engine.execution.execution import ExecutionModel
from engine.models.models import AssetConfig, Trade
from engine.analytics.monte_carlo import bootstrap_pnl
from engine.analytics.stats_accumulator import DrawdownTracker, StatsAccumulator


# ---------------------------------------------------------------------------
# 4.1 DrawdownTracker — unit semantics
# ---------------------------------------------------------------------------
def test_drawdown_tracker_resets_seed_peak_to_initial() -> None:
    """A losing first trade still books DD against the *starting* bankroll."""
    dd = DrawdownTracker()
    dd.reset(1_000.0)
    dd.update(900.0)
    assert dd.peak_equity == pytest.approx(1_000.0)
    assert dd.max_drawdown_abs == pytest.approx(100.0)
    assert dd.max_drawdown_pct == pytest.approx(10.0)


def test_drawdown_tracker_widens_only_on_deeper_trough() -> None:
    dd = DrawdownTracker()
    dd.reset(1_000.0)
    dd.update(800.0)  # DD = 200 (20%)
    dd.update(900.0)  # DD widens? No — partial recovery.
    assert dd.max_drawdown_abs == pytest.approx(200.0)
    assert dd.max_drawdown_pct == pytest.approx(20.0)
    dd.update(1_500.0)  # New peak
    assert dd.peak_equity == pytest.approx(1_500.0)
    dd.update(1_200.0)  # DD = 300 / 1500 = 20% — same %, larger abs.
    assert dd.max_drawdown_abs == pytest.approx(300.0)
    assert dd.max_drawdown_pct == pytest.approx(20.0)


def test_drawdown_tracker_no_negative_dd_on_new_high() -> None:
    dd = DrawdownTracker()
    dd.reset(1_000.0)
    dd.update(1_500.0)
    dd.update(2_000.0)
    assert dd.max_drawdown_abs == 0.0
    assert dd.max_drawdown_pct == 0.0


# ---------------------------------------------------------------------------
# 4.2 Visual ↔ Quant parity (stats + drawdown)
# ---------------------------------------------------------------------------
class _AlternatingTrades(Strategy):
    """Long every 3rd bar with tight SL/TP — the same vectorised contract
    used in ``test_run_vectorized_stats``. Deterministic mix of wins
    and losses so we can compare accumulators byte-for-byte.
    """

    def __init__(self, n_bars: int) -> None:
        self._n = n_bars

    def vectorized_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        sig = np.zeros(self._n, dtype=int)
        sl = np.full(self._n, np.nan, dtype=float)
        tp = np.full(self._n, np.nan, dtype=float)
        for i in range(1, self._n, 3):
            sig[i] = +1
            sl[i] = 2.0
            tp[i] = 4.0
        return pd.DataFrame(
            {"signal": sig, "sl_points": sl, "tp_points": tp},
            index=df.index,
        )


def _bars() -> pd.DataFrame:
    rows = [
        ("2024-01-02 09:30", 100, 100, 100, 100),
        ("2024-01-02 09:31", 100, 110, 100, 110),
        ("2024-01-02 09:32", 110, 110, 100, 100),  # red
        ("2024-01-02 09:33", 100, 110, 100, 110),  # green → TP
        ("2024-01-02 09:34", 110, 110, 100, 100),
        ("2024-01-02 09:35", 100, 110, 100, 110),
        ("2024-01-02 09:36", 110, 110, 100, 100),  # red → SL
    ]
    df = pd.DataFrame(rows, columns=["time", "open", "high", "low", "close"])
    df["time"] = pd.to_datetime(df["time"])
    df["volume"] = 1.0
    return df


def _core() -> BacktestCore:
    c = BacktestCore()
    c.asset_config = AssetConfig(
        name="TEST",
        point_value=10.0,
        tick_size=1.0,
        buffer=0.0,
        max_lots_per_100k=10,
        currency="USD",
        spread_points=0.0,
        commission_per_lot=0.0,
        slippage_points=0.0,
    )
    c.execution_model = ExecutionModel(profile="Optimistic")
    c.initial_equity = 100_000.0
    c.equity = c.initial_equity
    c.max_equity = c.initial_equity
    c.drawdown.reset(c.initial_equity)
    return c


def test_run_vectorized_stats_match_visual_replay() -> None:
    """The Quant engine must populate ``engine.stats`` and ``engine.drawdown``
    with byte-identical numbers to the visual engine's on-trade contract.
    """
    core = _core()
    df = _bars()
    core.load_data(df)
    core.load_strategy(_AlternatingTrades(n_bars=len(df)))

    core.run_vectorized()
    assert core.trades, "vectorised run produced no trades"

    # Reference accumulators populated in the on-trade order — this is
    # exactly what BacktestCore.execute_close does inside the visual loop.
    ref_stats = StatsAccumulator()
    ref_dd = DrawdownTracker()
    ref_dd.reset(core.initial_equity)
    eq = core.initial_equity
    for t in core.trades:
        ref_stats.update(t)
        eq += t.pnl
        ref_dd.update(eq)

    assert core.stats.wins == ref_stats.wins
    assert core.stats.losses == ref_stats.losses
    assert core.stats.break_evens == ref_stats.break_evens
    assert core.stats.longs == ref_stats.longs
    assert core.stats.shorts == ref_stats.shorts
    assert core.stats.gross_profit == pytest.approx(ref_stats.gross_profit)
    assert core.stats.gross_loss == pytest.approx(ref_stats.gross_loss)

    # Drawdown — both engines drive the SAME DrawdownTracker class, so the
    # numbers must match exactly (modulo equity-curve floating prefix sum).
    assert core.drawdown.peak_equity == pytest.approx(ref_dd.peak_equity)
    assert core.drawdown.max_drawdown_abs == pytest.approx(ref_dd.max_drawdown_abs)
    assert core.drawdown.max_drawdown_pct == pytest.approx(ref_dd.max_drawdown_pct)
    # Legacy mirror fields stay in lock-step (Stats-tab DD widget).
    assert core.max_drawdown_abs == pytest.approx(core.drawdown.max_drawdown_abs)
    assert core.max_drawdown_pct == pytest.approx(core.drawdown.max_drawdown_pct)
    assert core.max_equity == pytest.approx(core.drawdown.peak_equity)


# ---------------------------------------------------------------------------
# 4.3 / D1 — calmar_ratio NaN guard
# ---------------------------------------------------------------------------
def test_calmar_nan_for_negative_total_return_long_window() -> None:
    """Equity ends below zero over a 6-month window → NaN, not 0.0.

    The window must be long enough that the ``years < 0.1`` short-window
    guard doesn't pre-empt the new NaN branch (TZ Phase 4 / D1).
    """
    base = datetime(2024, 1, 1)
    eq = pd.Series(
        [10_000.0, 5_000.0, -1_000.0],
        index=pd.date_range(base, periods=3, freq="90D"),
    )
    val = calmar_ratio(eq, initial_equity=10_000.0)
    assert math.isnan(val)


def test_calmar_finite_for_winning_window() -> None:
    """The new NaN branch must not poison winning strategies."""
    base = datetime(2024, 1, 1)
    eq = pd.Series(
        [10_000.0, 12_000.0, 11_000.0, 14_000.0],
        index=pd.date_range(base, periods=4, freq="90D"),
    )
    val = calmar_ratio(eq, initial_equity=10_000.0)
    assert math.isfinite(val) and val > 0


# ---------------------------------------------------------------------------
# 4.4 / D2 — sharpe / sortino warnings
# ---------------------------------------------------------------------------
def _trade(pnl: float, exit_time: datetime, *, tid: int = 0) -> Trade:
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


def test_sharpe_warns_when_sample_smaller_than_threshold(caplog) -> None:
    base = datetime(2024, 1, 1, 10)
    # 10 daily trades → 9 daily returns ≪ 30. Mix wins/losses so the
    # function reaches the `_warn_small_sample` branch (it short-circuits
    # to 0.0 only when len(rets) < 2).
    trades = [
        _trade(+10 if i % 2 == 0 else -5, base + timedelta(days=i + 1), tid=i + 1)
        for i in range(10)
    ]
    eq = build_equity_curve(trades, initial_equity=10_000.0)
    with caplog.at_level(logging.WARNING, logger="StrategyTester.equity_curve"):
        sharpe_ratio(eq)
    assert any("sharpe_ratio" in r.message and "daily returns" in r.message for r in caplog.records)


def test_sortino_warns_when_sample_smaller_than_threshold(caplog) -> None:
    base = datetime(2024, 1, 1, 10)
    trades = [
        _trade(+10 if i % 2 == 0 else -5, base + timedelta(days=i + 1), tid=i + 1)
        for i in range(10)
    ]
    eq = build_equity_curve(trades, initial_equity=10_000.0)
    with caplog.at_level(logging.WARNING, logger="StrategyTester.equity_curve"):
        sortino_ratio(eq)
    assert any(
        "sortino_ratio" in r.message and "daily returns" in r.message for r in caplog.records
    )


def test_sharpe_silent_when_sample_meets_threshold(caplog) -> None:
    base = datetime(2024, 1, 1, 10)
    n = MIN_RETURNS_FOR_RATIO + 5  # safely above the threshold
    trades = [
        _trade(+10 if i % 2 == 0 else -5, base + timedelta(days=i + 1), tid=i + 1) for i in range(n)
    ]
    eq = build_equity_curve(trades, initial_equity=10_000.0)
    with caplog.at_level(logging.WARNING, logger="StrategyTester.equity_curve"):
        sharpe_ratio(eq)
    assert not any(
        "sharpe_ratio" in r.message and "daily returns" in r.message for r in caplog.records
    ), "warning fired even though sample size meets the threshold"


# ---------------------------------------------------------------------------
# 4.5 / D4 — block-bootstrap parameter
# ---------------------------------------------------------------------------
def test_bootstrap_block_size_one_matches_legacy_iid() -> None:
    """Default ``block_size=1`` must reproduce the exact legacy iid path,
    so existing call-sites remain bit-identical (back-compat)."""
    rng_pnls = np.array([+10.0, -7.0, +5.0, -3.0, +2.0, +8.0, -4.0, +1.0], dtype=float)
    res_default = bootstrap_pnl(rng_pnls, initial_equity=1_000.0, n_iter=200, seed=42)
    res_b1 = bootstrap_pnl(rng_pnls, initial_equity=1_000.0, n_iter=200, seed=42, block_size=1)
    np.testing.assert_array_equal(
        res_default.final_equity,
        res_b1.final_equity,
        err_msg="block_size=1 must be a no-op vs the implicit default",
    )
    np.testing.assert_array_equal(
        res_default.max_drawdown_pct,
        res_b1.max_drawdown_pct,
    )


def test_bootstrap_invalid_block_size_raises() -> None:
    pnls = np.array([+1.0, -1.0, +1.0], dtype=float)
    with pytest.raises(ValueError):
        bootstrap_pnl(pnls, initial_equity=1_000.0, n_iter=10, seed=0, block_size=0)


def test_block_bootstrap_widens_dd_tail_on_streaky_pnl() -> None:
    """Block-bootstrap preserves win/loss-streak autocorrelation, which
    iid resampling destroys. On a deliberately streaky PnL series the
    p95 DD must be *strictly* larger than the iid baseline (TZ Phase 4 /
    D4).
    """
    # 6 wins followed by 6 losses, repeated → strong serial correlation.
    pnls = np.array(
        [+10.0] * 6 + [-9.0] * 6 + [+10.0] * 6 + [-9.0] * 6,
        dtype=float,
    )
    iid = bootstrap_pnl(pnls, initial_equity=1_000.0, n_iter=4_000, seed=2024, block_size=1)
    block = bootstrap_pnl(pnls, initial_equity=1_000.0, n_iter=4_000, seed=2024, block_size=6)
    p95_iid = float(np.percentile(iid.max_drawdown_pct, 95))
    p95_block = float(np.percentile(block.max_drawdown_pct, 95))
    assert p95_block > p95_iid, (
        f"block-bootstrap p95 DD ({p95_block:.2f}%) was not wider than iid "
        f"({p95_iid:.2f}%) — autocorrelation preservation appears broken"
    )


def test_block_bootstrap_caps_block_size_at_n() -> None:
    """``block_size > n`` is treated as "one block of size n" — the resampler
    just repeats the source series, which gives the source DD exactly."""
    pnls = np.array([+5.0, -2.0, +3.0, -1.0], dtype=float)
    res = bootstrap_pnl(pnls, initial_equity=1_000.0, n_iter=50, seed=7, block_size=99)
    # Every iteration is just a circular rotation of the source, so all
    # final-equity values must equal initial + sum(pnls).
    expected_final = 1_000.0 + float(pnls.sum())
    np.testing.assert_allclose(res.final_equity, expected_final, atol=1e-9)
