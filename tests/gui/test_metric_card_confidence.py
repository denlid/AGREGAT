"""Phase 2 EPIC 2.4 — tests for MetricCard confidence "?" badge."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from gui.widgets.metric_card import MetricCard


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_confidence_badge_hidden_when_sample_sufficient(qapp: QApplication) -> None:
    card = MetricCard("Sharpe Ratio")
    card.set_confidence(50, minimum=30)
    assert card._confidence_lbl.isVisible() is False


def test_confidence_badge_shown_when_sample_small(qapp: QApplication) -> None:
    card = MetricCard("Sharpe Ratio")
    card.set_confidence(15, minimum=30)
    assert card._confidence_lbl.text() == "?"
    assert "15 daily returns" in card._confidence_lbl.toolTip()


def test_confidence_badge_hidden_when_zero(qapp: QApplication) -> None:
    card = MetricCard("Sharpe Ratio")
    card.set_confidence(15, minimum=30)
    # Now reset to zero — should disappear.
    card.set_confidence(0, minimum=30)
    assert card._confidence_lbl.isVisible() is False


def test_confidence_badge_custom_minimum_and_kind(qapp: QApplication) -> None:
    card = MetricCard("Custom")
    card.set_confidence(5, minimum=10, kind="bars")
    assert "5 bars" in card._confidence_lbl.toolTip()
    assert "< 10 minimum" in card._confidence_lbl.toolTip()
