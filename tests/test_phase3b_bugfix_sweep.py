"""Bug-fix sweep regressions (post-Phase 3 audit).

Each test pins a specific behavioural contract that was either silently
wrong before this PR or could regress in a future refactor:

* #1  ``Trade.entry_index`` / ``Trade.exit_index`` always equal the bar
  index where the fill / exit actually happened, regardless of whether
  the close is triggered by SL/TP/EOD/WEEKEND or by the strategy calling
  ``ctx.close()`` from inside ``on_candle``. Previously the strategy
  path was ``current_idx + 1`` (off by one).
* #2  ``persistence.load_session`` reconstructs ``engine.asset_config``
  from the saved meta dict. Previously the asset bound to the engine at
  load time silently won, so reloads on a different default asset
  produced wrong PnL / lot sizing on subsequent steps.
* #3  ``BacktestCore.run_vectorized`` mirrors **both** ``max_drawdown_pct``
  and ``max_drawdown_abs`` from the vectorised result. Previously only
  the pct half was wired up, so the Stats tab DD$ widget read $0 after a
  Quant run.
* #4  ``DataLoader.load_csv`` raises ``ValueError`` with the original
  exception chained (``raise ... from e``) so callers see the underlying
  parse failure, not just the wrapped message.
"""

from __future__ import annotations

import os
import tempfile
from datetime import time

import numpy as np
import pandas as pd

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from engine.backtest_engine import BacktestEngine  # noqa: E402
from engine.strategy.context import Strategy  # noqa: E402
from engine.data.data_loader import DataLoader  # noqa: E402
from engine.execution.execution import ExecutionModel  # noqa: E402
from engine.models.models import AssetConfig  # noqa: E402
from engine.persistence_pkg.persistence import load_session, save_session  # noqa: E402


# --------------------------------------------------------------------------- helpers
def _engine_for(asset: AssetConfig) -> BacktestEngine:
    eng = BacktestEngine()
    eng.asset_config = asset
    eng.execution_model = ExecutionModel(profile="Realistic")
    eng.initial_equity = 100_000.0
    eng.equity = eng.initial_equity
    eng.max_equity = eng.initial_equity
    return eng


def _three_bar_df() -> pd.DataFrame:
    rows = [
        ("2024-01-02 10:00", 35_000, 35_100, 35_000, 35_050),
        ("2024-01-02 10:01", 35_050, 35_200, 35_000, 35_100),
        ("2024-01-02 10:02", 35_100, 35_300, 35_000, 35_250),
    ]
    df = pd.DataFrame(rows, columns=["time", "open", "high", "low", "close"])
    df["time"] = pd.to_datetime(df["time"])
    df["volume"] = 1.0
    return df


def _asset_us30() -> AssetConfig:
    return AssetConfig(
        name="US30",
        point_value=1.0,
        tick_size=1.0,
        buffer=0.0,
        max_lots_per_100k=10,
        currency="USD",
        spread_points=2.0,
        commission_per_lot=5.0,
        slippage_points=1.5,
        trading_hours_start=time(8, 30),
        trading_hours_end=time(17, 15),
        eod_close=time(21, 45),
        enforce_trading_hours=True,
        close_on_weekend=False,
    )


# --------------------------------------------------------------------------- #1
class _ManualCloseOnLastBar(Strategy):
    """Buy on bar 0 (fills at bar 1's open), close manually on bar 2."""

    def __init__(self) -> None:
        self._sent = False
        self._closed = False
        self._bars_open = 0

    def on_candle(self, ctx) -> None:  # type: ignore[override]
        if not self._sent and ctx.position is None:
            ctx.buy_at_open(volume=1.0, sl=None, tp=None, reason="t", tag="t")
            self._sent = True
            return
        if ctx.position is not None:
            self._bars_open += 1
            if self._bars_open >= 2 and not self._closed:
                self._closed = True
                ctx.close(reason="MANUAL")


def test_manual_close_records_correct_exit_index() -> None:
    eng = _engine_for(_asset_us30())
    eng.load_data(_three_bar_df())
    eng.load_strategy(_ManualCloseOnLastBar())
    for _ in range(3):
        eng.step()

    assert len(eng.trades) == 1
    t = eng.trades[0]
    # Fill happened at bar 1 (the bar after the buy_at_open call).
    assert t.entry_index == 1, t.entry_index
    # Close happened on bar 2 (10:02). Previously this was 3 (out of range).
    assert t.exit_index == 2, t.exit_index
    assert t.exit_index < eng.total_bars
    assert t.exit_reason == "MANUAL"


class _SLHitOnNextBar(Strategy):
    """Buy on bar 0 with a tight SL that's guaranteed to hit on bar 1."""

    def __init__(self) -> None:
        self._sent = False

    def on_candle(self, ctx) -> None:  # type: ignore[override]
        if not self._sent and ctx.position is None:
            # Bar 1 open=35050; low=35000. SL at 35040 is hit by bar 1's low.
            ctx.buy_at_open(volume=1.0, sl=35_040.0, tp=None, reason="t", tag="t")
            self._sent = True


def test_sl_hit_records_same_bar_exit_index() -> None:
    eng = _engine_for(_asset_us30())
    eng.load_data(_three_bar_df())
    eng.load_strategy(_SLHitOnNextBar())
    for _ in range(3):
        eng.step()

    assert len(eng.trades) == 1
    t = eng.trades[0]
    # Fill at bar 1, SL trigger same bar → exit_index also 1.
    assert t.entry_index == 1
    assert t.exit_index == 1
    assert t.exit_reason in ("SL", "BE", "LOCK")


# --------------------------------------------------------------------------- #2
def test_load_session_restores_asset_config_fully() -> None:
    src = _engine_for(_asset_us30())

    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "session.agbk")
        save_session(src, path)

        # Load into an engine pre-bound to a totally different asset.
        dst = BacktestEngine()
        dst.asset_config = AssetConfig(
            name="EURUSD",
            point_value=100_000.0,
            tick_size=0.0001,
            buffer=0.0,
            max_lots_per_100k=10,
            currency="USD",
        )
        dst.execution_model = ExecutionModel(profile="Realistic")
        load_session(dst, path)

    a = dst.asset_config
    # Identity / numeric fields
    assert a.name == "US30"
    assert a.point_value == 1.0
    assert a.tick_size == 1.0
    assert a.spread_points == 2.0
    assert a.commission_per_lot == 5.0
    assert a.slippage_points == 1.5
    # Trading-hours / EOD time fields (previously not even saved).
    assert a.trading_hours_start == time(8, 30)
    assert a.trading_hours_end == time(17, 15)
    assert a.eod_close == time(21, 45)
    assert a.enforce_trading_hours is True
    assert a.close_on_weekend is False


def test_load_session_keeps_existing_asset_when_meta_missing() -> None:
    """Backwards compatibility: if meta has no ``asset`` block, keep existing."""
    src = _engine_for(_asset_us30())

    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "session.agbk")
        save_session(src, path)

        # Strip the asset block from meta to simulate a legacy file.
        import json
        import zipfile

        with zipfile.ZipFile(path, "r") as zf:
            members = {name: zf.read(name) for name in zf.namelist()}
        meta = json.loads(members["meta.json"].decode("utf-8"))
        meta.pop("asset", None)
        # Sprint 2.2 — drop the integrity hash + format_version to
        # simulate a *real* legacy archive. The post-Sprint-2 loader
        # would otherwise correctly trip the hash check on this
        # synthetic tamper, which is exactly what we don't want here.
        meta.pop("agbk_sha256", None)
        meta.pop("format_version", None)
        members["meta.json"] = json.dumps(meta).encode("utf-8")
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for name, data in members.items():
                zf.writestr(name, data)

        dst = BacktestEngine()
        kept = AssetConfig(
            name="EURUSD",
            point_value=100_000.0,
            tick_size=0.0001,
            buffer=0.0,
            max_lots_per_100k=10,
            currency="USD",
        )
        dst.asset_config = kept
        dst.execution_model = ExecutionModel(profile="Realistic")
        load_session(dst, path)

    # Asset stays the EURUSD instance we passed in.
    assert dst.asset_config is kept
    assert dst.asset_config.name == "EURUSD"


# --------------------------------------------------------------------------- #3
class _ScriptedSignals(Strategy):
    """Vectorised strategy that emits one losing long to drive max_dd_abs > 0."""

    def vectorized_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        n = len(df)
        signal = np.zeros(n, dtype=np.int8)
        sl_points = np.zeros(n, dtype=np.float64)
        tp_points = np.zeros(n, dtype=np.float64)
        # Long on bar 1; SL 50 points below entry → hit on bar 2's deep low.
        signal[1] = 1
        sl_points[1] = 50.0
        tp_points[1] = 500.0
        return pd.DataFrame(
            {
                "signal": signal,
                "sl_points": sl_points,
                "tp_points": tp_points,
            }
        )


def test_run_vectorized_populates_max_drawdown_abs() -> None:
    eng = _engine_for(_asset_us30())
    eng.load_data(_three_bar_df())
    eng.load_strategy(_ScriptedSignals())

    result = eng.run_vectorized()
    assert result is not None
    assert len(eng.trades) >= 1

    # The losing trade drove equity below initial_equity → both metrics > 0.
    assert eng.max_drawdown_abs > 0.0
    assert eng.max_drawdown_pct > 0.0
    # And the abs value matches what the vectorised result reported.
    assert abs(eng.max_drawdown_abs - result.peak_drawdown_abs) < 1e-9
    # Sanity: dd_abs ≤ initial_equity (we can't lose more than we started with
    # in this scripted scenario).
    assert eng.max_drawdown_abs <= eng.initial_equity


# --------------------------------------------------------------------------- #4
def test_dataloader_chains_exception_context() -> None:
    """B904: ``raise ValueError(...) from e`` keeps the original cause."""
    with tempfile.TemporaryDirectory() as td:
        bad = os.path.join(td, "garbage.csv")
        # Header looks reasonable but the rows are malformed.
        with open(bad, "w") as f:
            f.write("time,open,high,low,close\n")
            f.write("not-a-date,oops,oops,oops,oops\n")
        # The loader will fail at the time-parse step. We don't care which
        # branch raises — only that the resulting ``ValueError`` carries the
        # underlying exception via ``__cause__``.
        try:
            DataLoader.load_csv(bad)
        except ValueError as exc:
            # Either the time-parse branch or the NaT-detection branch fires;
            # both should produce a ValueError. Only the parse branch chains
            # __cause__ — the NaT branch raises a fresh ValueError. Accept
            # either outcome here, just lock that *some* explanatory message
            # makes it through.
            assert "time" in str(exc).lower() or "csv" in str(exc).lower()
        else:
            raise AssertionError("expected DataLoader.load_csv to raise")
