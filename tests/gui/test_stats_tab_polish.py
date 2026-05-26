"""Polish pass — Stats tab loading spinner and caches.

These tests intentionally avoid driving a real engine; they only check
that the new public surface is wired and behaves sensibly.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from gui.tabs.stats_tab import StatsTab


class _DummyEngine:
    initial_equity = 1000.0
    equity = 1000.0
    trades: list = []


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_stats_tab_has_spinner(qapp: QApplication) -> None:
    tab = StatsTab(_DummyEngine())
    assert tab._spinner is not None
    # Hidden until _show_spinner is called.
    assert tab._spinner.isHidden()


def test_show_then_hide_spinner_round_trip(qapp: QApplication) -> None:
    tab = StatsTab(_DummyEngine())
    tab._show_spinner("Testing…")
    assert tab._spinner_used is True
    tab._hide_spinner()
    assert tab._spinner_used is False


def test_hide_without_show_is_noop(qapp: QApplication) -> None:
    tab = StatsTab(_DummyEngine())
    tab._hide_spinner()  # should not raise
    assert tab._spinner_used is False
