"""Phase 2 EPIC 2.2 — tests for the InsightCard widget."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from gui.analytics.insights_engine import Insight
from gui.widgets.insight_card import InsightCard


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def _ins(severity: str = "info", action: str | None = None) -> Insight:
    return Insight(
        rule_id=f"rule_{severity}",
        severity=severity,
        headline="Test headline",
        detail="Some detail text.",
        section="risk",
        action=action,
    )


def test_insight_card_renders_headline_and_detail(qapp: QApplication) -> None:
    card = InsightCard(_ins("warning"))
    assert "Test headline" == card._headline_lbl.text()
    assert "Some detail text." == card._detail_lbl.text()
    assert card.rule_id == "rule_warning"


def test_insight_card_action_button_shown_only_when_provided(qapp: QApplication) -> None:
    no_action = InsightCard(_ins(action=None))
    assert no_action._action_btn is None
    with_action = InsightCard(_ins(action="Do it"))
    assert with_action._action_btn is not None
    assert with_action._action_btn.text() == "Do it"


def test_insight_card_compact_hides_icon(qapp: QApplication) -> None:
    card = InsightCard(_ins("info"), compact=True)
    # In compact mode the icon label is explicitly hidden.
    assert card._compact is True
    assert card._icon_lbl.isHidden() is True
    # Switching back to non-compact unhides it (the next show() would
    # display it). Verify the explicit hidden flag is cleared.
    card.set_compact(False)
    assert card._compact is False
    assert card._icon_lbl.isHidden() is False


def test_insight_card_dismiss_emits_rule_id(qapp: QApplication) -> None:
    card = InsightCard(_ins("warning"))
    seen: list[str] = []
    card.dismissed.connect(lambda rid: seen.append(rid))
    card._on_dismiss()
    assert seen == ["rule_warning"]
    assert card.isVisible() is False
