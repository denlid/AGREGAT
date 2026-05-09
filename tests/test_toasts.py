"""Sprint 4.4 — Headless tests for the toast widget + manager."""

from __future__ import annotations

import os

import pytest

# Force the offscreen Qt platform plugin before importing QtWidgets so
# the tests can run on a CI box without an X server.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtCore import QDeadlineTimer  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from gui.components.toast import Toast, ToastLevel, ToastManager  # noqa: E402


def _wait_for(predicate, qapp, timeout_ms: int = 1500) -> None:
    """Pump events until ``predicate()`` is true or ``timeout_ms`` elapses."""
    deadline = QDeadlineTimer(timeout_ms)
    while not predicate():
        if deadline.hasExpired():
            return
        QTest.qWait(10)
        qapp.processEvents()


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance() or QApplication([])
    return app


def test_toast_construction_defaults(qapp: QApplication) -> None:
    t = Toast("hello")
    assert t.message == "hello"
    assert t.level == ToastLevel.INFO
    assert not t.is_dismissed
    t.dismiss()
    assert t.is_dismissed


def test_toast_dismiss_emits_signal(qapp: QApplication) -> None:
    t = Toast("bye", duration_ms=0)
    received: list[bool] = []
    t.dismissed.connect(lambda: received.append(True))
    t.dismiss()
    qapp.processEvents()
    assert received == [True]


def test_toast_levels_have_distinct_palette(qapp: QApplication) -> None:
    a = Toast("a", level=ToastLevel.INFO)
    b = Toast("b", level=ToastLevel.ERROR)
    assert a._bg_color != b._bg_color
    a.dismiss()
    b.dismiss()


def test_manager_show_inserts_toast(qapp: QApplication) -> None:
    parent = QWidget()
    parent.resize(800, 600)
    mgr = ToastManager(parent)
    t = mgr.show("hi", duration_ms=0)
    assert t in mgr.stack
    t.dismiss()
    _wait_for(lambda: t not in mgr.stack, qapp)
    assert t not in mgr.stack
    parent.deleteLater()


def test_manager_stack_order_newest_first(qapp: QApplication) -> None:
    parent = QWidget()
    parent.resize(800, 600)
    mgr = ToastManager(parent)
    a = mgr.show("first")
    b = mgr.show("second")
    # Newest first
    assert mgr.stack[0] is b
    assert mgr.stack[1] is a
    mgr.dismiss_all()
    _wait_for(lambda: not mgr.stack, qapp)
    parent.deleteLater()


def test_manager_severity_helpers(qapp: QApplication) -> None:
    parent = QWidget()
    parent.resize(800, 600)
    mgr = ToastManager(parent)
    assert mgr.success("ok").level == ToastLevel.SUCCESS
    assert mgr.warning("careful").level == ToastLevel.WARNING
    assert mgr.error("boom").level == ToastLevel.ERROR
    mgr.dismiss_all()
    _wait_for(lambda: not mgr.stack, qapp)
    parent.deleteLater()
