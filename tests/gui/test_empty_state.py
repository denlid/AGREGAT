"""Phase 1 EPIC 1.5 — Tests for gui.widgets.empty_state.EmptyState."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from gui.widgets.empty_state import EmptyState


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_defaults_render_headline_only(qapp: QApplication) -> None:
    es = EmptyState()
    assert es._headline_lbl.text() == "Nothing here yet"
    # Subtitle and CTA hidden by default.
    assert not es._subtitle_lbl.isVisibleTo(None)
    assert not es._cta_btn.isVisibleTo(None)


def test_subtitle_and_cta_visibility_with_constructor_args(qapp: QApplication) -> None:
    es = EmptyState(
        icon="\U0001F4CA",
        headline="No trades",
        subtitle="Load data first",
        cta_text="Load",
    )
    assert es._subtitle_lbl.text() == "Load data first"
    assert es._cta_btn.text() == "Load"
    # Visibility flags reflect the supplied data.
    assert es._subtitle_lbl.isVisibleTo(es)
    assert es._cta_btn.isVisibleTo(es)


def test_cta_clicked_signal(qapp: QApplication) -> None:
    es = EmptyState(headline="x", cta_text="Go")
    received: list[int] = []
    es.cta_clicked.connect(lambda: received.append(1))
    es._cta_btn.click()
    assert received == [1]


def test_set_methods_update_labels(qapp: QApplication) -> None:
    es = EmptyState()
    es.set_icon("\U0001F4DC")
    es.set_headline("Updated headline")
    es.set_subtitle("New subtitle")
    es.set_cta("Click me")
    assert es._icon_lbl.text() == "\U0001F4DC"
    assert es._headline_lbl.text() == "Updated headline"
    assert es._subtitle_lbl.text() == "New subtitle"
    assert es._cta_btn.text() == "Click me"
    assert es._cta_btn.isVisibleTo(es)
    assert es._subtitle_lbl.isVisibleTo(es)


def test_set_subtitle_to_empty_hides_label(qapp: QApplication) -> None:
    es = EmptyState(headline="x", subtitle="visible")
    assert es._subtitle_lbl.isVisibleTo(es)
    es.set_subtitle("")
    assert not es._subtitle_lbl.isVisibleTo(es)


def test_set_cta_empty_hides_button(qapp: QApplication) -> None:
    es = EmptyState(headline="x", cta_text="Go")
    assert es._cta_btn.isVisibleTo(es)
    es.set_cta("")
    assert not es._cta_btn.isVisibleTo(es)
