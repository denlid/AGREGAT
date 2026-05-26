"""Phase 1.2 — Skipped-trade metric / Trade Selection HTML section."""

from __future__ import annotations

import os
import tempfile

import pandas as pd

from engine.analytics.skipped_trades import (
    SKIPPED_RATIO_WARN_PCT,
    aggregate_skipped,
)
from engine.core.backtest_core import BacktestCore
from engine.execution.execution import ExecutionModel
from engine.models.models import AssetConfig
from engine.report_export import _build_summary, export_report
from engine.strategy.context import Strategy


def test_aggregate_empty_returns_zero() -> None:
    out = aggregate_skipped([], n_executed_trades=0)
    assert out["count"] == 0
    assert out["ratio_pct"] == 0.0
    assert out["by_reason"] == {}
    assert out["warn"] is False


def test_aggregate_groups_by_reason_and_warns_above_threshold() -> None:
    skipped = [{"reason": "LOT_LIMIT_STRICT"}] * 9 + [{"reason": "INVALID_SL"}] * 1
    out = aggregate_skipped(skipped, n_executed_trades=10)
    assert out["count"] == 10
    assert out["by_reason"]["LOT_LIMIT_STRICT"] == 9
    assert out["by_reason"]["INVALID_SL"] == 1
    # 10 / (10 executed + 10 skipped) = 50% — well above 5%.
    assert out["ratio_pct"] == 50.0
    assert out["warn"] is True
    assert SKIPPED_RATIO_WARN_PCT == 5.0


def test_aggregate_unknown_reason_bucketed() -> None:
    out = aggregate_skipped([{"foo": "bar"}, {}], n_executed_trades=0)
    assert out["count"] == 2
    assert out["by_reason"]["UNKNOWN"] == 2


# -- Integration with the report exporter ------------------------------


class _StrictBuyOnce(Strategy):
    """Open one too-large lot under strict cap → skipped, then a normal trade."""

    def __init__(self) -> None:
        self._step = 0

    def on_candle(self, ctx) -> None:  # type: ignore[override]
        self._step += 1
        if self._step == 1:
            # Strict mode + ridiculous risk → cap kicks in → trade cancelled.
            lot = ctx.calculate_lot(sl_points=0.1, risk_pct=99.0, strict=True)
            assert lot is None  # cancelled
        elif self._step == 2 and ctx.position is None:
            # Normal trade afterwards so the report has at least one trade.
            ctx.buy_at_open(volume=0.1, sl=None, tp=None, reason="t", tag="t")
        elif self._step == 5 and ctx.position is not None:
            ctx.close()


def _df(n: int = 6) -> pd.DataFrame:
    rows = [
        (f"2024-01-02 10:{i:02d}", 100 + i, 100 + i + 1, 100 + i - 1, 100 + i)
        for i in range(n)
    ]
    df = pd.DataFrame(rows, columns=["time", "open", "high", "low", "close"])
    df["time"] = pd.to_datetime(df["time"])
    df["volume"] = 1.0
    return df


def _make_core() -> BacktestCore:
    core = BacktestCore()
    core.asset_config = AssetConfig(
        name="TEST",
        point_value=10.0,
        tick_size=1.0,
        buffer=0.0,
        max_lots_per_100k=1,  # tiny cap → easy to trip
        currency="USD",
    )
    core.execution_model = ExecutionModel(profile="Optimistic")
    core.initial_equity = 100_000.0
    core.equity = core.initial_equity
    core.max_equity = core.initial_equity
    return core


def test_summary_includes_skipped_trade_fields() -> None:
    core = _make_core()
    core.load_data(_df())
    core.load_strategy(_StrictBuyOnce())
    for _ in range(8):
        core.step()
    summary = _build_summary(core)
    assert "skipped_trade_count" in summary
    assert "skipped_trade_ratio_pct" in summary
    assert "skipped_by_reason" in summary
    assert summary["skipped_trade_count"] >= 1
    assert "LOT_LIMIT_STRICT" in summary["skipped_by_reason"]


def test_report_html_renders_trade_selection_section_when_skipped() -> None:
    core = _make_core()
    core.load_data(_df())
    core.load_strategy(_StrictBuyOnce())
    for _ in range(8):
        core.step()
    with tempfile.TemporaryDirectory() as td:
        folder = export_report(core, td, asset_name="TEST", strategy_name="strict")
        with open(os.path.join(folder, "analytics.html"), "r", encoding="utf-8") as fh:
            html = fh.read()
    assert "Trade Selection" in html
    assert "LOT_LIMIT_STRICT" in html


def test_report_html_omits_section_when_no_skipped() -> None:
    """Clean run with zero skips → section is hidden."""

    class _Buy(Strategy):
        def __init__(self) -> None:
            self._sent = False
            self._closed = False

        def on_candle(self, ctx) -> None:  # type: ignore[override]
            if not self._sent and ctx.position is None:
                ctx.buy_at_open(volume=0.1, sl=None, tp=None, reason="t", tag="t")
                self._sent = True
            elif (
                self._sent
                and not self._closed
                and ctx.position is not None
                and ctx._engine.current_idx >= 3
            ):
                ctx.close()
                self._closed = True

    core = _make_core()
    core.load_data(_df())
    core.load_strategy(_Buy())
    for _ in range(8):
        core.step()
    with tempfile.TemporaryDirectory() as td:
        folder = export_report(core, td, asset_name="TEST", strategy_name="clean")
        with open(os.path.join(folder, "analytics.html"), "r", encoding="utf-8") as fh:
            html = fh.read()
    assert "Trade Selection" not in html
