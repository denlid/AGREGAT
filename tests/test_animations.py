"""Sprint 4.3 — Tests for gui.components.animations (headless)."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QDeadlineTimer, QPropertyAnimation
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QGraphicsOpacityEffect, QWidget


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance() or QApplication([])
    return app


def _wait_until(predicate, qapp, timeout_ms: int = 2000) -> None:
    deadline = QDeadlineTimer(timeout_ms)
    while not predicate():
        if deadline.hasExpired():
            return
        QTest.qWait(10)
        qapp.processEvents()


def test_fade_in_attaches_effect(qapp: QApplication) -> None:
    from gui.components.animations import fade_in

    w = QWidget()
    anim = fade_in(w, duration_ms=20)
    assert isinstance(anim, QPropertyAnimation)
    assert isinstance(w.graphicsEffect(), QGraphicsOpacityEffect)
    assert anim.startValue() == 0.0
    assert anim.endValue() == 1.0


def test_fade_out_animates_to_zero(qapp: QApplication) -> None:
    from gui.components.animations import fade_out

    w = QWidget()
    anim = fade_out(w, duration_ms=20)
    anim.start()
    _wait_until(lambda: anim.state() == QPropertyAnimation.State.Stopped, qapp)
    eff = w.graphicsEffect()
    assert isinstance(eff, QGraphicsOpacityEffect)
    assert eff.opacity() == pytest.approx(0.0, abs=1e-3)


def test_cross_fade_runs_both(qapp: QApplication) -> None:
    from gui.components.animations import cross_fade

    a = QWidget()
    b = QWidget()
    grp = cross_fade(a, b, duration_ms=20)
    grp.start()
    _wait_until(lambda: grp.state() == grp.State.Stopped, qapp)
    eff_a = a.graphicsEffect()
    eff_b = b.graphicsEffect()
    assert isinstance(eff_a, QGraphicsOpacityEffect)
    assert isinstance(eff_b, QGraphicsOpacityEffect)
    assert eff_a.opacity() == pytest.approx(0.0, abs=1e-3)
    assert eff_b.opacity() == pytest.approx(1.0, abs=1e-3)


def test_hover_fader_attaches(qapp: QApplication) -> None:
    from gui.components.animations import HoverFader

    w = QWidget()
    fader = HoverFader(w, hover=1.0, idle=0.5, duration_ms=10)
    eff = w.graphicsEffect()
    assert isinstance(eff, QGraphicsOpacityEffect)
    assert eff.opacity() == pytest.approx(0.5, abs=1e-3)
    assert fader.parent() is w  # ownership held by the widget
