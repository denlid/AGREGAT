"""Visual ↔ vectorised parity for ``LinearLogicCore``.

The dual-engine architecture lets a strategy run in either the
step-by-step Visual engine (``BacktestCore.step_n``) or the vectorised
Fast Quant engine (``run_vectorized``). For LINEAR LOGIC CORE we
guarantee that **entry signals are identical**: every Visual entry at
1m bar ``e`` is matched by a non-zero ``signal`` at bar ``e - 1`` in
the frame returned by ``vectorized_signals`` (since the vectorised
engine opens the position on the bar following the signal, mirroring
``Context.buy_at_open``).

We do NOT assert full trade-list parity here because the Visual engine
runs ``on_15m_close`` Counter-CISD active management which can close
positions early (``COUNTER_CISD`` exits). That feature is intentionally
omitted from the vectorised path — the contract documented in
``LinearLogicCore.vectorized_signals`` is "entry signals only".
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pandas as pd

from engine.strategy.context import StrategyLoader
from engine.core.backtest_core import BacktestCore
from engine.execution.execution import ExecutionModel
from engine.models.models import AssetConfig
from engine.execution.vectorized_engine import run_vectorized


def _synthetic_1m_with_15m_gaps(n_15m: int = 200, seed: int = 7) -> pd.DataFrame:
    """1m frame engineered to produce CISD events on 15m aggregation.

    Each 15m bar's *open* deliberately gaps from the prior bar's *close*
    (``rng.normal(0, 1.5)``) so body extremes don't share values across
    consecutive bars — without these gaps the strict-inequality pivot
    rule produces almost no pivots on a smooth random walk.
    """
    rng = np.random.default_rng(seed)
    ho_15 = []
    hc_15 = []
    price = 18000.0
    for _ in range(n_15m):
        o = price + rng.normal(0, 1.5)
        c = o + rng.normal(0, 5.0)
        ho_15.append(o)
        hc_15.append(c)
        price = c
    ho_15 = np.array(ho_15)
    hc_15 = np.array(hc_15)

    n = n_15m * 15
    opens = np.zeros(n)
    closes = np.zeros(n)
    highs = np.zeros(n)
    lows = np.zeros(n)
    for k in range(n_15m):
        base = k * 15
        o15 = ho_15[k]
        c15 = hc_15[k]
        for j in range(15):
            f = j / 14.0
            c = o15 + (c15 - o15) * f + rng.normal(0, 0.3)
            closes[base + j] = c
            opens[base + j] = closes[base + j - 1] if (base + j) > 0 else o15
            highs[base + j] = max(opens[base + j], closes[base + j]) + abs(rng.normal(0, 0.5))
            lows[base + j] = min(opens[base + j], closes[base + j]) - abs(rng.normal(0, 0.5))
    for k in range(n_15m):
        opens[k * 15] = ho_15[k]
    times = pd.date_range("2024-01-02 09:00", periods=n, freq="1min")
    return pd.DataFrame(
        {
            "time": times,
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": np.full(n, 1.0),
        }
    )


def _asset() -> AssetConfig:
    return AssetConfig(
        name="SYNTH",
        point_value=1.0,
        tick_size=0.1,
        buffer=0.0,
        max_lots_per_100k=1000,
        currency="USD",
        min_lot=0.01,
        lot_step=0.01,
        spread_points=0.0,
        commission_per_lot=0.0,
        close_on_weekend=False,
        enforce_trading_hours=False,
        enforce_eod_close=False,
    )


def _params_no_active_mgmt() -> dict:
    return {
        "strict_lot_cap": False,
        "ott_filter": False,
        "force_eod_close": False,
        "counter_cisd_winning": "none",
    }


def _run_visual(df: pd.DataFrame):
    eng = BacktestCore()
    eng.asset_config = _asset()
    eng.execution_model = ExecutionModel(profile="Optimistic")
    eng.initial_equity = 100_000.0
    eng.equity = 100_000.0
    eng.max_equity = 100_000.0
    eng.risk_pct = 1.0
    eng.skipped_trades = []
    eng.load_data(df)
    s = StrategyLoader.load("strategies/linear_logic_core.py")
    s.apply_params(_params_no_active_mgmt())
    eng.load_strategy(s)
    eng.step_n(eng.total_bars + 5)
    return eng


# ----------------------------------------------------------------- tests
def test_vectorized_signals_schema():
    """The signal frame must have the columns the vectorised engine expects."""
    df = _synthetic_1m_with_15m_gaps(n_15m=20)
    s = StrategyLoader.load("strategies/linear_logic_core.py")
    s.apply_params(_params_no_active_mgmt())
    sigs = s.vectorized_signals(df)
    assert list(sigs.columns) == ["signal", "sl_points", "tp_points"]
    assert len(sigs) == len(df)
    assert sigs["signal"].dtype == np.int8
    assert sigs["sl_points"].dtype == np.float64
    assert sigs["tp_points"].dtype == np.float64


def test_vectorized_signals_does_not_crash_on_pascal_columns():
    """The vectorised engine passes Pascal-cased columns; we must accept either."""
    df = _synthetic_1m_with_15m_gaps(n_15m=20)
    df_pascal = df.rename(columns={c: c.capitalize() for c in df.columns})
    s = StrategyLoader.load("strategies/linear_logic_core.py")
    s.apply_params(_params_no_active_mgmt())
    out = s.vectorized_signals(df_pascal)
    assert list(out.columns) == ["signal", "sl_points", "tp_points"]


def test_visual_entries_are_subset_of_vectorized_signals():
    """Every Visual entry at bar ``e`` must have a non-zero matching
    signal at bar ``e-1`` in the vectorised frame.

    The vectorised path may emit additional signals during periods
    when the Visual engine has a position open (the vectorised engine
    ignores them via the "one position at a time" rule), so the test
    is a *subset* check, not strict equality.
    """
    df = _synthetic_1m_with_15m_gaps(n_15m=200, seed=7)
    eng = _run_visual(df)
    visual_entries = [(t.entry_index, +1 if t.direction == "long" else -1) for t in eng.trades]
    assert visual_entries, "test fixture is supposed to produce entries"

    s = StrategyLoader.load("strategies/linear_logic_core.py")
    s.apply_params(_params_no_active_mgmt())
    sigs = s.vectorized_signals(df)
    sig_arr = sigs["signal"].to_numpy()

    for entry_idx, direction in visual_entries:
        # Vectorised engine fires entry on bar after the signal, so the
        # signal lives at entry_idx - 1.
        sig_idx = entry_idx - 1
        assert sig_idx >= 0, f"unexpected entry at bar 0: {entry_idx}"
        assert sig_arr[sig_idx] == direction, (
            f"Visual entry at bar {entry_idx} (dir={direction}) has no "
            f"matching vectorised signal at bar {sig_idx}: "
            f"sig_arr[{sig_idx}]={sig_arr[sig_idx]}"
        )


def test_vectorized_engine_runs_linear_logic_core_end_to_end():
    """Sanity check that ``run_vectorized`` accepts the new strategy and
    produces at least one trade (proving the SL/TP path is wired)."""
    df = _synthetic_1m_with_15m_gaps(n_15m=200, seed=7)
    df_pascal = df.rename(columns={c: c.capitalize() for c in df.columns})
    s = StrategyLoader.load("strategies/linear_logic_core.py")
    s.apply_params(_params_no_active_mgmt())
    res = run_vectorized(
        df_pascal,
        s,
        asset=_asset(),
        execution_model=ExecutionModel(profile="Optimistic"),
        initial_equity=100_000.0,
        risk_pct=1.0,
    )
    assert len(res.trades) >= 1, "vectorised engine produced zero trades on the parity fixture"
    for t in res.trades:
        assert t.exit_reason in {"SL", "TP", "EOD"}, t.exit_reason
