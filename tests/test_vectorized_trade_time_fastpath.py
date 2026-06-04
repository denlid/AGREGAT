"""Parity guard for the vectorised engine's trade-materialisation fast path.

The vectorised engine slices ``entry_time_np`` / ``exit_time_np`` straight
from the OHLC time array and hands them to ``Trade.__init__`` so the costly
``pd.to_datetime(str)`` round-trip (the dominant cost of a multi-year run —
see ``engine/execution/vectorized_engine.py``) is skipped.

These tests pin the invariant the optimisation relies on: the cached
datetime64 produced by the fast path must equal what the legacy parse path
(``pd.to_datetime(trade.entry_time).to_datetime64()``) would have computed.
If that ever drifts, chart markers / trades-tab time columns would silently
disagree between engines.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from engine.execution.execution import ExecutionModel
from engine.execution.vectorized_engine import run_vectorized
from engine.models.models import Trade, _NP_TIME_UNSET, AssetConfig


class _Strat:
    """Cheap deterministic signal stream (mirrors bench_vectorized_reuse)."""

    def vectorized_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        n = len(df)
        idx = np.arange(n)
        sig = np.zeros(n, dtype=int)
        sig[idx % 17 == 3] = 1
        sig[idx % 19 == 7] = -1
        return pd.DataFrame(
            {
                "signal": sig,
                "sl_points": np.full(n, 2.0),
                "tp_points": np.full(n, 4.0),
            },
            index=df.index,
        )


def _asset() -> AssetConfig:
    return AssetConfig(
        name="T",
        point_value=1.0,
        tick_size=0.01,
        buffer=0.0,
        max_lots_per_100k=10,
        currency="USD",
        min_lot=0.01,
        lot_step=0.01,
        spread_points=0.0,
        commission_per_lot=0.0,
        close_on_weekend=False,
        enforce_trading_hours=False,
    )


def _df(n_bars: int) -> pd.DataFrame:
    rng = np.random.default_rng(7)
    close = 100.0 + np.linspace(0.0, 20.0, n_bars) + rng.normal(0.0, 1.0, n_bars).cumsum()
    return pd.DataFrame(
        {
            "time": pd.date_range("2020-01-01", periods=n_bars, freq="min"),
            "open": np.r_[close[0], close[:-1]],
            "high": close + 1.0,
            "low": close - 1.0,
            "close": close,
            "volume": 1.0,
        }
    )


def test_trade_fastpath_uses_supplied_datetime64():
    """When entry_time_np is supplied, __init__ stores it verbatim (no parse)."""
    dt = np.datetime64("2020-01-01T09:30:00", "ns")
    t = Trade(
        id=1,
        direction="long",
        entry_price=1.0,
        exit_price=1.0,
        pnl=0.0,
        pnl_points=0.0,
        entry_index=0,
        exit_index=1,
        entry_time="garbage-not-a-date",  # would parse to NaT on the legacy path
        exit_time="also-garbage",
        volume=0.01,
        duration_minutes=1,
        exit_reason="SL",
        mae=0.0,
        mfe=0.0,
        session="Asia",
        entry_time_np=dt,
        exit_time_np=dt,
    )
    assert t.entry_time_np == dt
    assert t.exit_time_np == dt


def test_legacy_path_still_parses_when_unset():
    """Default (no datetime64 supplied) keeps the parse-from-string behaviour."""
    t = Trade(
        id=1,
        direction="long",
        entry_price=1.0,
        exit_price=1.0,
        pnl=0.0,
        pnl_points=0.0,
        entry_index=0,
        exit_index=1,
        entry_time="2020-01-01T09:30:00",
        exit_time="2020-01-01T09:31:00",
        volume=0.01,
        duration_minutes=1,
        exit_reason="SL",
        mae=0.0,
        mfe=0.0,
        session="Asia",
    )
    assert t.entry_time_np == pd.to_datetime("2020-01-01T09:30:00").to_datetime64()
    assert t.exit_time_np == pd.to_datetime("2020-01-01T09:31:00").to_datetime64()
    # Sentinel must be distinct from a real (possibly-None) value.
    assert _NP_TIME_UNSET is not None


def test_engine_fastpath_matches_legacy_parse_for_every_trade():
    """The cached np time the engine writes must equal the legacy parse of
    the trade's own entry_time / exit_time string — for every trade."""
    df = _df(40_000)
    res = run_vectorized(
        df,
        _Strat(),
        asset=_asset(),
        execution_model=ExecutionModel(),
        skip_lookahead_check=True,
    )
    assert res.summary["n_trades"] > 100  # the dataset actually trades
    for tr in res.trades:
        assert tr.entry_time_np == pd.to_datetime(tr.entry_time).to_datetime64()
        assert tr.exit_time_np == pd.to_datetime(tr.exit_time).to_datetime64()
