"""Tests for engine.report_export — folder structure, file contents."""

from __future__ import annotations

import csv
import json
import os
from dataclasses import dataclass

import pytest

from engine.report_export import export_report


@dataclass
class _FakeTrade:
    id: int
    direction: str
    entry_price: float
    exit_price: float
    pnl: float
    duration_minutes: int = 0
    exit_reason: str = "TP"


class _FakeAssetConfig:
    name = "TESTASSET"


class _FakeStrategy:
    pass


class _FakeEngine:
    def __init__(self, trades, equity_track):
        self.trades = trades
        self.equity_track = equity_track
        self.asset_config = _FakeAssetConfig()
        self.strategy = _FakeStrategy()
        self.initial_equity = 100_000.0
        self.equity = 100_000.0 + sum(t.pnl for t in trades)
        self.max_drawdown_pct = 1.5
        self.max_drawdown_abs = 250.0
        self._date_range = (None, None)


def _make_engine() -> _FakeEngine:
    trades = [
        _FakeTrade(id=1, direction="BUY", entry_price=100, exit_price=102, pnl=200.0),
        _FakeTrade(id=2, direction="SELL", entry_price=102, exit_price=101, pnl=-50.0),
        _FakeTrade(id=3, direction="BUY", entry_price=101, exit_price=104, pnl=300.0),
    ]
    equity_track = [100_000, 100_200, 100_150, 100_450]
    return _FakeEngine(trades, equity_track)


def test_export_report_creates_all_files(tmp_path):
    engine = _make_engine()
    folder = export_report(engine, str(tmp_path))
    assert os.path.isdir(folder)
    expected = {"trades.csv", "equity.csv", "stats.json", "meta.json", "analytics.html"}
    assert expected.issubset(set(os.listdir(folder)))


def test_trades_csv_has_one_row_per_trade(tmp_path):
    engine = _make_engine()
    folder = export_report(engine, str(tmp_path))
    with open(os.path.join(folder, "trades.csv"), encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        rows = list(reader)
    assert len(rows) == 3
    assert {r["direction"] for r in rows} == {"BUY", "SELL"}


def test_stats_json_has_kpi_fields(tmp_path):
    engine = _make_engine()
    folder = export_report(engine, str(tmp_path))
    with open(os.path.join(folder, "stats.json"), encoding="utf-8") as fh:
        stats = json.load(fh)
    assert stats["n_trades"] == 3
    assert stats["wins"] == 2
    assert stats["losses"] == 1
    assert stats["win_rate_pct"] == pytest.approx(66.6666, rel=1e-3)
    assert stats["total_pnl"] == pytest.approx(450.0)
    assert stats["profit_factor"] == pytest.approx(10.0)


def test_meta_json_records_engine_state(tmp_path):
    engine = _make_engine()
    folder = export_report(engine, str(tmp_path), strategy_name="MyStrat", asset_name="DE40")
    with open(os.path.join(folder, "meta.json"), encoding="utf-8") as fh:
        meta = json.load(fh)
    assert meta["asset"] == "DE40"
    assert meta["strategy"] == "MyStrat"
    assert meta["n_trades"] == 3
    assert meta["date_range_start"] is None


def test_equity_csv_matches_track(tmp_path):
    engine = _make_engine()
    folder = export_report(engine, str(tmp_path))
    with open(os.path.join(folder, "equity.csv"), encoding="utf-8") as fh:
        rows = list(csv.reader(fh))
    assert rows[0] == ["trade_idx", "equity"]
    assert len(rows) == 5  # header + 4 equity points
    assert float(rows[-1][1]) == pytest.approx(100_450.0)


def test_export_raises_on_empty_trades(tmp_path):
    engine = _FakeEngine(trades=[], equity_track=[100_000.0])
    with pytest.raises(RuntimeError, match="No trades"):
        export_report(engine, str(tmp_path))


def test_html_report_contains_summary_keys(tmp_path):
    engine = _make_engine()
    folder = export_report(engine, str(tmp_path))
    with open(os.path.join(folder, "analytics.html"), encoding="utf-8") as fh:
        html = fh.read()
    assert "AGREGAT" in html
    assert "n_trades" in html
    assert "win_rate_pct" in html
