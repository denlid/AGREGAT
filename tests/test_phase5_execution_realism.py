"""TZ Phase 5 — Execution Realism (slippage on stops + GAP_SL).

Covers:
  * 5.1 (C1+A4): SL/TP fills widen by ``slippage_price`` in the adverse
    direction in BOTH engines (visual + vectorised).
  * 5.2 (A4): ``Trade.slippage_cost`` reflects the round-trip slippage
    expected by the broker rather than ``0.0`` (vectorised path) or an
    informational figure that bore no relationship to fills (visual
    path now matches because both legs do, in fact, slip).
  * 5.3 (C2): When the bar opens beyond the stop in the adverse
    direction, the close fills at the open and uses the ``GAP_SL``
    reason — distinguishable from a normal stop-out for analytics.

Note on slippage *materialisation* between engines:
  In the **visual** engine a strategy supplies an absolute ``sl=`` price
  via :py:meth:`Context.buy_at_open` /``sell_at_open``. Slippage on
  entry shifts the *fill* but not the user-anchored stop, so both legs
  of slippage land in PnL points (full round-trip).

  In the **vectorised** engine the strategy supplies a ``sl_points``
  *distance* via :py:meth:`Strategy.vectorized_signals`. The stop is
  anchored relative to the slipped entry, so the entry-side slippage
  is absorbed into the stop's absolute level and only the exit-side
  leg materialises in PnL points. The reported ``Trade.slippage_cost``
  remains the broker's full round-trip cost in both engines.

  Phase 6 (engine parity) will revisit this asymmetry.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from engine.strategy.context import Strategy
from engine.core.backtest_core import BacktestCore
from engine.execution.execution import ExecutionModel
from engine.models.models import AssetConfig
from engine.execution.vectorized_engine import run_vectorized


# ---------------------------------------------------------------------------
# Test scaffolding
# ---------------------------------------------------------------------------
def _df(rows):
    df = pd.DataFrame(rows, columns=["time", "Open", "High", "Low", "Close"])
    df["time"] = pd.to_datetime(df["time"])
    df["Volume"] = 1.0
    return df


def _df_lower(rows):
    """Lowercased-column variant — what BacktestCore.load_data expects."""
    df = _df(rows)
    df.columns = ["time", "open", "high", "low", "close", "volume"]
    return df


def _asset(point_value=1.0, tick=1.0, spread=0.0, commission=0.0, slippage=0.0):
    return AssetConfig(
        name="TEST",
        point_value=point_value,
        tick_size=tick,
        buffer=0.0,
        max_lots_per_100k=10,
        currency="USD",
        min_lot=0.01,
        lot_step=0.01,
        spread_points=spread,
        commission_per_lot=commission,
        slippage_points=slippage,
    )


class _FixedLongOnce(Strategy):
    def __init__(self, at: int, sl_pts: float, tp_pts: float, n: int):
        self._at, self._sl, self._tp, self._n = at, sl_pts, tp_pts, n

    def vectorized_signals(self, df):
        sig = np.zeros(self._n, dtype=int)
        sig[self._at] = +1
        return pd.DataFrame(
            {
                "signal": sig,
                "sl_points": np.full(self._n, self._sl, dtype=float),
                "tp_points": np.full(self._n, self._tp, dtype=float),
            },
            index=df.index,
        )


class _FixedShortOnce(Strategy):
    def __init__(self, at: int, sl_pts: float, tp_pts: float, n: int):
        self._at, self._sl, self._tp, self._n = at, sl_pts, tp_pts, n

    def vectorized_signals(self, df):
        sig = np.zeros(self._n, dtype=int)
        sig[self._at] = -1
        return pd.DataFrame(
            {
                "signal": sig,
                "sl_points": np.full(self._n, self._sl, dtype=float),
                "tp_points": np.full(self._n, self._tp, dtype=float),
            },
            index=df.index,
        )


class _BuyAtOpenOnce(Strategy):
    """Visual-engine strategy: queue a single long ``buy_at_open`` with
    absolute SL / TP prices, then idle.
    """

    def __init__(self, sl: float | None, tp: float | None, volume: float = 1.0):
        self._sl = sl
        self._tp = tp
        self._volume = volume
        self._sent = False

    def on_candle(self, ctx):
        if self._sent or ctx.position is not None:
            return
        ctx.buy_at_open(volume=self._volume, sl=self._sl, tp=self._tp)
        self._sent = True


# ---------------------------------------------------------------------------
# 5.1 / C1+A4 — vectorised: SL/TP fills slip from the *anchor*
# ---------------------------------------------------------------------------
def _run_long_sl_vec(slippage: float):
    """Long entry at bar 0 close, SL hit on bar 1 low. Returns the trade."""
    rows = [
        ("2024-01-02 10:00", 100, 100, 100, 100),
        ("2024-01-02 10:01", 100, 100, 97, 100),  # bar low sweeps SL
        ("2024-01-02 10:02", 100, 100, 100, 100),
    ]
    res = run_vectorized(
        _df(rows),
        _FixedLongOnce(at=0, sl_pts=2.0, tp_pts=0.0, n=len(rows)),
        asset=_asset(point_value=10.0, slippage=slippage),
        execution_model=ExecutionModel("Realistic" if slippage > 0 else "Optimistic"),
    )
    assert len(res.trades) == 1
    return res.trades[0]


def test_vectorized_long_sl_exit_slips_below_stop_anchor() -> None:
    """The SL fill is exactly ``initial_sl − slippage`` (1 leg of slip
    visible in the exit price). Asserting against ``initial_sl``
    sidesteps the entry-side anchor shift discussed in the module
    docstring.
    """
    t = _run_long_sl_vec(slippage=0.5)
    assert t.exit_reason == "SL"
    assert t.exit_price == pytest.approx(t.initial_sl - 0.5)


def test_vectorized_long_sl_pnl_drops_relative_to_legacy_no_slip() -> None:
    no_slip = _run_long_sl_vec(slippage=0.0)
    with_slip = _run_long_sl_vec(slippage=0.5)
    # After Phase 6 / A7, the SL anchor is signal-bar close (independent
    # of the slipped entry), so BOTH legs of slippage materialise in
    # PnL points. Extra loss = 2 × slip × point_value × volume.
    expected_extra_loss = 2 * 0.5 * 1.0 * 10.0 * with_slip.volume
    assert no_slip.pnl - with_slip.pnl == pytest.approx(expected_extra_loss, abs=0.011)
    assert with_slip.exit_reason == "SL"


def test_vectorized_short_sl_exit_slips_above_stop_anchor() -> None:
    rows = [
        ("2024-01-02 10:00", 100, 100, 100, 100),
        ("2024-01-02 10:01", 100, 103, 100, 100),  # high sweeps sl
        ("2024-01-02 10:02", 100, 100, 100, 100),
    ]
    res = run_vectorized(
        _df(rows),
        _FixedShortOnce(at=0, sl_pts=2.0, tp_pts=0.0, n=len(rows)),
        asset=_asset(point_value=10.0, slippage=0.5),
        execution_model=ExecutionModel("Realistic"),
    )
    t = res.trades[0]
    assert t.exit_reason == "SL"
    # Short SL slips upwards (worse for the short).
    assert t.exit_price == pytest.approx(t.initial_sl + 0.5)


def test_vectorized_long_tp_exit_slips_below_target_anchor() -> None:
    """TP fill is widened by slippage in the unfavourable direction
    (long TP fills *below* the limit). C1 is conservative on TP too.
    """
    rows = [
        ("2024-01-02 10:00", 100, 100, 100, 100),
        ("2024-01-02 10:01", 100, 105, 100, 100),  # high sweeps tp
        ("2024-01-02 10:02", 100, 100, 100, 100),
    ]
    res = run_vectorized(
        _df(rows),
        _FixedLongOnce(at=0, sl_pts=2.0, tp_pts=4.0, n=len(rows)),
        asset=_asset(point_value=10.0, slippage=0.5),
        execution_model=ExecutionModel("Realistic"),
    )
    t = res.trades[0]
    assert t.exit_reason == "TP"
    # After Phase 6 / A7 the TP anchor is the signal-bar close (= 100)
    # + tp_pts (= 4), independent of the slipped entry. Exit fill
    # = TP_anchor - slip = 104 - 0.5 = 103.5. (Pre-Phase 6 the anchor
    # was entry + tp_pts so exit was 104.)
    assert t.exit_price == pytest.approx(103.5)


# ---------------------------------------------------------------------------
# 5.2 / A4 — slippage_cost reports the broker's round-trip cost
# ---------------------------------------------------------------------------
def test_vectorized_slippage_cost_reflects_round_trip_dollars() -> None:
    """Used to be hard-coded at 0.0 — now matches the visual engine's
    ``costs_for_round_trip`` figure: ``slip_pts × 2 × point_value × vol``.
    """
    rows = [
        ("2024-01-02 10:00", 100, 100, 100, 100),
        ("2024-01-02 10:01", 100, 105, 100, 100),
        ("2024-01-02 10:02", 100, 100, 100, 100),
    ]
    res = run_vectorized(
        _df(rows),
        _FixedLongOnce(at=0, sl_pts=2.0, tp_pts=4.0, n=len(rows)),
        asset=_asset(point_value=10.0, slippage=0.5),
        execution_model=ExecutionModel("Realistic"),
    )
    t = res.trades[0]
    expected = 0.5 * 2.0 * 10.0 * t.volume
    assert t.slippage_cost == pytest.approx(round(expected, 2))


def test_vectorized_slippage_cost_zero_under_optimistic() -> None:
    """Optimistic profile zeros all costs — slippage_cost included
    (asset-level slippage_points are ignored under Optimistic).
    """
    rows = [
        ("2024-01-02 10:00", 100, 100, 100, 100),
        ("2024-01-02 10:01", 100, 105, 100, 100),
        ("2024-01-02 10:02", 100, 100, 100, 100),
    ]
    res = run_vectorized(
        _df(rows),
        _FixedLongOnce(at=0, sl_pts=2.0, tp_pts=4.0, n=len(rows)),
        asset=_asset(slippage=0.5),
        execution_model=ExecutionModel("Optimistic"),
    )
    t = res.trades[0]
    assert t.slippage_cost == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# 5.3 / C2 — GAP_SL reason for bar-open-beyond-stop
# ---------------------------------------------------------------------------
def test_vectorized_long_gap_through_sl_emits_gap_sl_reason() -> None:
    """Bar opens at 95 with sl≈98 ⇒ gap-through. Reason ``GAP_SL``,
    fill at the open price (worse than the legacy "fill at sl" rule).
    """
    rows = [
        ("2024-01-02 10:00", 100, 100, 100, 100),
        # Gap down past SL=98 — open=95 is *below* the stop.
        ("2024-01-02 10:01", 95, 96, 90, 95),
        ("2024-01-02 10:02", 95, 95, 95, 95),
    ]
    res = run_vectorized(
        _df(rows),
        _FixedLongOnce(at=0, sl_pts=2.0, tp_pts=0.0, n=len(rows)),
        asset=_asset(),
        execution_model=ExecutionModel("Optimistic"),
    )
    t = res.trades[0]
    assert t.exit_reason == "GAP_SL"
    assert t.exit_price == pytest.approx(95.0)


def test_vectorized_short_gap_through_sl_emits_gap_sl_reason() -> None:
    """Short, sl=102, bar opens at 105 (Ask=105 with spread=0) ⇒ gap."""
    rows = [
        ("2024-01-02 10:00", 100, 100, 100, 100),
        ("2024-01-02 10:01", 105, 110, 105, 110),
        ("2024-01-02 10:02", 110, 110, 110, 110),
    ]
    res = run_vectorized(
        _df(rows),
        _FixedShortOnce(at=0, sl_pts=2.0, tp_pts=0.0, n=len(rows)),
        asset=_asset(),
        execution_model=ExecutionModel("Optimistic"),
    )
    t = res.trades[0]
    assert t.exit_reason == "GAP_SL"
    assert t.exit_price == pytest.approx(105.0)


def test_vectorized_no_gap_when_bar_opens_at_stop_exactly() -> None:
    """Bar open exactly at the stop ⇒ normal SL fill, NOT GAP_SL.

    Stricter `<` / `>` boundaries protect against floating-point noise
    accidentally tagging benign trades as gap-through.
    """
    rows = [
        ("2024-01-02 10:00", 100, 100, 100, 100),
        # Open exactly at SL=98 — no gap.
        ("2024-01-02 10:01", 98, 99, 95, 98),
        ("2024-01-02 10:02", 98, 98, 98, 98),
    ]
    res = run_vectorized(
        _df(rows),
        _FixedLongOnce(at=0, sl_pts=2.0, tp_pts=0.0, n=len(rows)),
        asset=_asset(),
        execution_model=ExecutionModel("Optimistic"),
    )
    t = res.trades[0]
    assert t.exit_reason == "SL"
    assert t.exit_price == pytest.approx(98.0)


# ---------------------------------------------------------------------------
# Visual engine — same contract through BacktestCore._check_sl_tp.
# Visual SL is *absolute* (from buy_at_open(sl=…)), so both legs of
# slippage materialise in PnL — see module docstring.
# ---------------------------------------------------------------------------
def _visual_core(asset: AssetConfig, model: ExecutionModel) -> BacktestCore:
    c = BacktestCore()
    c.asset_config = asset
    c.execution_model = model
    c.initial_equity = 100_000.0
    c.equity = c.initial_equity
    c.max_equity = c.initial_equity
    c.drawdown.reset(c.initial_equity)
    return c


def _run_visual_long_sl(slippage: float):
    """Visual: bar 0 sends signal, bar 1 fills @ open=100, bar 2 hits sl=98.
    Returns the closed trade.
    """
    rows = [
        ("2024-01-02 10:00", 100, 100, 100, 100),  # signal bar
        ("2024-01-02 10:01", 100, 100, 100, 100),  # fill at open=100
        ("2024-01-02 10:02", 100, 100, 97, 99),  # SL=98 hit on low
        ("2024-01-02 10:03", 99, 99, 99, 99),
    ]
    df = _df_lower(rows)
    core = _visual_core(
        _asset(point_value=10.0, slippage=slippage),
        ExecutionModel("Realistic" if slippage > 0 else "Optimistic"),
    )
    core.load_data(df)
    core.load_strategy(_BuyAtOpenOnce(sl=98.0, tp=None, volume=1.0))
    for _ in range(len(rows)):
        core.step()
    assert core.trades, "expected the SL hit to materialise a trade"
    return core.trades[-1]


def test_visual_long_sl_exit_slips_below_absolute_stop() -> None:
    """Visual engine uses absolute sl, so the exit is exactly
    ``sl − slippage``. Asserting that pins down the C1 fix on the
    on-candle path.
    """
    t = _run_visual_long_sl(slippage=0.5)
    assert t.exit_reason == "SL"
    assert t.exit_price == pytest.approx(98.0 - 0.5)


def test_visual_long_sl_pnl_includes_both_slippage_legs() -> None:
    """Visual / absolute-sl: full round-trip slippage materialises in
    PnL, since the entry-side slip does NOT shift the user-anchored
    stop.
    """
    no_slip = _run_visual_long_sl(slippage=0.0)
    with_slip = _run_visual_long_sl(slippage=0.5)
    expected_extra_loss = 0.5 * 2.0 * 10.0 * with_slip.volume
    assert no_slip.pnl - with_slip.pnl == pytest.approx(expected_extra_loss, abs=0.011)


def test_visual_long_gap_through_sl_emits_gap_sl_reason() -> None:
    """Bar 2 opens at 95 with sl=98 ⇒ visual engine tags the trade
    ``GAP_SL`` and fills at the open price (further widened by
    slippage).
    """
    rows = [
        ("2024-01-02 10:00", 100, 100, 100, 100),  # signal
        ("2024-01-02 10:01", 100, 100, 100, 100),  # fill at 100
        ("2024-01-02 10:02", 95, 96, 90, 95),  # gap-through sl=98
        ("2024-01-02 10:03", 95, 95, 95, 95),
    ]
    df = _df_lower(rows)
    core = _visual_core(
        _asset(point_value=10.0),
        ExecutionModel("Optimistic"),
    )
    core.load_data(df)
    core.load_strategy(_BuyAtOpenOnce(sl=98.0, tp=None))
    for _ in range(len(rows)):
        core.step()
    t = core.trades[-1]
    assert t.exit_reason == "GAP_SL"
    assert t.exit_price == pytest.approx(95.0)


def test_visual_slippage_cost_matches_round_trip_under_realistic() -> None:
    """Round-trip ``slippage_cost`` in the visual engine corresponds to
    the actually-deducted slippage (both legs filled at slipped
    prices).
    """
    t = _run_visual_long_sl(slippage=0.5)
    expected = 0.5 * 2.0 * 10.0 * t.volume
    assert t.slippage_cost == pytest.approx(round(expected, 2))
