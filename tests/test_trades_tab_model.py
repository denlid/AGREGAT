"""Phase 2.2 — TradesTab model/view rewrite.

These tests exercise :class:`gui.tabs.trades_table_model.TradesTableModel`
and the surrounding :class:`TradesFilterProxy` directly so the suite
runs headless on every CI machine (``QT_QPA_PLATFORM=offscreen``).

Coverage:
* model wiring — row/column count, sort role, display formatting
* proxy filtering — filter type, search text, custom date range
* sort by raw float (PnL "$+10,000.00" sorts numerically, not as text)
* memory regression guard — populating 60 000 trades must finish in
  well under 1 s on the synthetic Trade objects below (the legacy
  QTableWidget path needed 5–10 s before this rewrite).
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import List, Optional

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import QApplication

from gui.tabs.trades_table_model import (  # noqa: E402
    COL_DIRECTION,
    COL_ID,
    COL_PNL_DOLLARS,
    COL_RR,
    COLUMNS,
    RawValueRole,
    TradesFilterProxy,
    TradesTableModel,
)


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


@dataclass
class _StubTrade:
    """Tiny stand-in for :class:`engine.models.models.Trade` — only the
    attributes the model reads. Keeps the test independent of the
    full Trade constructor."""

    id: int
    direction: str
    entry_time: str
    exit_time: str
    entry_price: float
    exit_price: float
    pnl: float
    pnl_points: float
    volume: float
    duration_minutes: int
    exit_reason: str
    mae: float
    mfe: float
    session: str
    initial_sl: float = 0.0
    rr: Optional[float] = None
    mae_dollars: float = 0.0
    mfe_dollars: float = 0.0
    entry_time_np: Optional[np.datetime64] = None
    exit_time_np: Optional[np.datetime64] = None


def _make_trades(n: int) -> List[_StubTrade]:
    base = np.datetime64("2024-01-01T00:00:00")
    out: List[_StubTrade] = []
    for i in range(n):
        is_long = i % 2 == 0
        pnl = (i % 7 - 3) * 100.0  # mix of +/-/0
        entry_np = base + np.timedelta64(i, "m")
        exit_np = entry_np + np.timedelta64(15, "m")
        out.append(
            _StubTrade(
                id=i + 1,
                direction="long" if is_long else "short",
                entry_time=str(entry_np),
                exit_time=str(exit_np),
                entry_price=100.0 + i * 0.01,
                exit_price=101.0 + i * 0.01,
                pnl=pnl,
                pnl_points=pnl / 10.0,
                volume=1.0,
                duration_minutes=15,
                exit_reason="TP" if pnl > 0 else "SL" if pnl < 0 else "BE",
                mae=2.0,
                mfe=3.0,
                session="London",
                initial_sl=99.5,
                mae_dollars=200.0,
                mfe_dollars=300.0,
                entry_time_np=entry_np,
                exit_time_np=exit_np,
            )
        )
    return out


def test_model_basic_shape(qapp: QApplication) -> None:
    m = TradesTableModel()
    assert m.columnCount() == len(COLUMNS)
    assert m.rowCount() == 0
    m.set_trades(_make_trades(10))
    assert m.rowCount() == 10
    assert m.columnCount() == len(COLUMNS)


def test_model_display_role_formatting(qapp: QApplication) -> None:
    m = TradesTableModel()
    m.set_trades(_make_trades(3))
    # ID
    assert m.data(m.index(0, COL_ID), Qt.ItemDataRole.DisplayRole) == "1"
    # Direction (LONG / SHORT)
    assert m.data(m.index(0, COL_DIRECTION), Qt.ItemDataRole.DisplayRole) == "LONG"
    assert m.data(m.index(1, COL_DIRECTION), Qt.ItemDataRole.DisplayRole) == "SHORT"
    # PnL dollars formatted with sign and commas
    val = m.data(m.index(0, COL_PNL_DOLLARS), Qt.ItemDataRole.DisplayRole)
    assert "$" in val and "," not in val[1:4]  # < 1000 doesn't get comma


def test_model_raw_role_returns_floats_for_sort(qapp: QApplication) -> None:
    m = TradesTableModel()
    m.set_trades(_make_trades(5))
    raw = m.data(m.index(0, COL_PNL_DOLLARS), RawValueRole)
    assert isinstance(raw, float)
    raw_id = m.data(m.index(0, COL_ID), RawValueRole)
    assert isinstance(raw_id, int)


def test_proxy_filter_wins_only(qapp: QApplication) -> None:
    m = TradesTableModel()
    m.set_trades(_make_trades(50))
    p = TradesFilterProxy()
    p.setSourceModel(m)
    p.set_filter_type("Wins")
    for r in range(p.rowCount()):
        src = p.mapToSource(p.index(r, 0))
        t = m.trade_at(src.row())
        assert t is not None and t.pnl > 0


def test_proxy_filter_long_only(qapp: QApplication) -> None:
    m = TradesTableModel()
    m.set_trades(_make_trades(20))
    p = TradesFilterProxy()
    p.setSourceModel(m)
    p.set_filter_type("Long")
    for r in range(p.rowCount()):
        src = p.mapToSource(p.index(r, 0))
        t = m.trade_at(src.row())
        assert t is not None and t.direction == "long"


def test_proxy_search_matches_id_or_reason(qapp: QApplication) -> None:
    m = TradesTableModel()
    trades = _make_trades(30)
    m.set_trades(trades)
    p = TradesFilterProxy()
    p.setSourceModel(m)
    p.set_search_text("17")  # matches trade.id == 17
    found = []
    for r in range(p.rowCount()):
        src = p.mapToSource(p.index(r, 0))
        t = m.trade_at(src.row())
        if t is not None:
            found.append(t.id)
    assert 17 in found, "search '17' should surface trade with id 17"


def test_proxy_search_then_clear(qapp: QApplication) -> None:
    m = TradesTableModel()
    m.set_trades(_make_trades(20))
    p = TradesFilterProxy()
    p.setSourceModel(m)
    p.set_search_text("nonexistent_match_token")
    assert p.rowCount() == 0
    p.set_search_text("")
    assert p.rowCount() == 20


def test_proxy_custom_date_range(qapp: QApplication) -> None:
    m = TradesTableModel()
    trades = _make_trades(60)  # one per minute starting 2024-01-01 00:00
    m.set_trades(trades)
    p = TradesFilterProxy()
    p.setSourceModel(m)
    p.set_filter_type("Custom Date")
    p.set_date_range(QDate(2024, 1, 1), QDate(2024, 1, 1))
    # All trades are on 2024-01-01 (between 00:00 and 00:59), so they
    # should all pass the inclusive bounds.
    assert p.rowCount() == 60


def test_proxy_sort_pnl_dollars_numeric(qapp: QApplication) -> None:
    m = TradesTableModel()
    m.set_trades(_make_trades(20))
    p = TradesFilterProxy()
    p.setSourceModel(m)
    # Sort ascending on PnL ($) — sorting must be numeric, not lexical.
    p.sort(COL_PNL_DOLLARS, Qt.SortOrder.AscendingOrder)
    pnls = []
    for r in range(p.rowCount()):
        src = p.mapToSource(p.index(r, 0))
        t = m.trade_at(src.row())
        if t is not None:
            pnls.append(t.pnl)
    assert pnls == sorted(pnls), "rows must be sorted by raw pnl ascending"


def test_set_trades_emits_single_reset(qapp: QApplication) -> None:
    m = TradesTableModel()
    counter = {"reset": 0}
    m.modelReset.connect(lambda: counter.__setitem__("reset", counter["reset"] + 1))
    m.set_trades(_make_trades(100))
    m.set_trades(_make_trades(200))
    # Two set_trades calls → exactly two modelReset signals.
    assert counter["reset"] == 2


def test_60k_population_under_500ms(qapp: QApplication) -> None:
    """Populating 60 000 trades through ``set_trades`` is the key
    user-facing scenario after a full backtest. The legacy widget path
    blocked the UI thread for 5–10 s; the model/view path must do it
    in well under 0.5 s."""
    trades = _make_trades(60_000)
    m = TradesTableModel()
    p = TradesFilterProxy()
    p.setSourceModel(m)

    t0 = time.perf_counter()
    m.set_trades(trades)
    elapsed = time.perf_counter() - t0
    assert elapsed < 0.5, (
        f"set_trades(60k) took {elapsed * 1000:.1f} ms, "
        "Phase 2.2 regression: must stay under 500 ms"
    )
    assert m.rowCount() == 60_000
    assert p.rowCount() == 60_000


def test_rr_role_and_format(qapp: QApplication) -> None:
    m = TradesTableModel()
    m.set_trades(_make_trades(5))
    # Display role contains the "R" suffix or "N/A"; raw role is float.
    disp = m.data(m.index(0, COL_RR), Qt.ItemDataRole.DisplayRole)
    raw = m.data(m.index(0, COL_RR), RawValueRole)
    assert isinstance(raw, float)
    assert "R" in disp or disp == "N/A"


def test_trade_at_out_of_range_returns_none(qapp: QApplication) -> None:
    m = TradesTableModel()
    m.set_trades(_make_trades(3))
    assert m.trade_at(-1) is None
    assert m.trade_at(99) is None
    assert m.trade_at(0) is not None


def test_clear_via_set_trades(qapp: QApplication) -> None:
    m = TradesTableModel()
    m.set_trades(_make_trades(5))
    m.set_trades([])
    assert m.rowCount() == 0
