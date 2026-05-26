"""Phase 2 EPIC 2.3 — tests for the trades-table anomaly stripe."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from gui.tabs.trades_table_model import COL_ID, TradesTableModel


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


class _T:
    def __init__(self, idx: int, pnl: float, dur: float, direction: str = "long") -> None:
        self.id = idx
        self.entry_price = 100.0
        self.exit_price = 100.0 + pnl / 10
        self.pnl = pnl
        self.pnl_points = pnl / 10
        self.volume = 1.0
        self.duration_minutes = dur
        self.mae = 0.0
        self.mfe = 0.0
        self.mae_dollars = 0.0
        self.mfe_dollars = 0.0
        self.direction = direction
        self.exit_reason = "TP" if pnl >= 0 else "SL"
        self.session = "NY"
        self.entry_time = "2024-01-01 12:00:00"
        self.exit_time = "2024-01-01 13:00:00"


def test_no_outliers_on_uniform_data(qapp: QApplication) -> None:
    m = TradesTableModel()
    m.set_trades([_T(i, 50.0, 60.0) for i in range(50)])
    assert m._anomaly_kind.sum() == 0


def test_outlier_win_detected(qapp: QApplication) -> None:
    m = TradesTableModel()
    trades = [_T(i, 50.0, 60.0) for i in range(99)]
    trades.append(_T(100, 5000.0, 60.0))  # huge winner
    m.set_trades(trades)
    assert int(m._anomaly_kind[99]) == 2  # 2 = winner outlier
    bg = m.data(m.index(99, COL_ID), Qt.ItemDataRole.BackgroundRole)
    assert bg is not None
    assert bg.alpha() >= 100  # strong stripe


def test_outlier_loss_detected(qapp: QApplication) -> None:
    m = TradesTableModel()
    trades = [_T(i, 50.0, 60.0) for i in range(99)]
    trades.append(_T(100, -5000.0, 60.0))
    m.set_trades(trades)
    assert int(m._anomaly_kind[99]) == 3  # 3 = loss outlier
    tip = m.data(m.index(99, COL_ID), Qt.ItemDataRole.ToolTipRole)
    assert "Outlier loss" in tip


def test_outlier_duration_detected(qapp: QApplication) -> None:
    m = TradesTableModel()
    trades = [_T(i, 50.0, 60.0) for i in range(99)]
    trades.append(_T(100, 50.0, 5000.0))
    m.set_trades(trades)
    assert int(m._anomaly_kind[99]) == 1  # 1 = duration outlier
    tip = m.data(m.index(99, COL_ID), Qt.ItemDataRole.ToolTipRole)
    assert "Outlier duration" in tip


def test_tooltip_falls_back_to_header_for_other_columns(qapp: QApplication) -> None:
    m = TradesTableModel()
    trades = [_T(i, 50.0, 60.0) for i in range(99)]
    trades.append(_T(100, 5000.0, 60.0))
    m.set_trades(trades)
    # COL_ID has the outlier text; another column should fall back to the
    # generic header tooltip (or empty).
    tip_other = m.data(m.index(99, 4), Qt.ItemDataRole.ToolTipRole)
    assert "Outlier" not in (tip_other or "")
