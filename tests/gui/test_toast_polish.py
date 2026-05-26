"""Polish pass — tests for ToastManager improvements.

* Default duration is now capped well below the previous 4 s.
* ``MAX_DURATION_MS`` clamps stray callers.
* ``MAX_STACK`` evicts the oldest toast when the cap is hit.
* Identical message + level is deduped instead of stacking a duplicate.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QWidget

from gui.components.toast import Toast, ToastLevel, ToastManager


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_default_duration_under_three_seconds(qapp: QApplication) -> None:
    parent = QWidget()
    mgr = ToastManager(parent)
    t = mgr.show("hi")
    assert t._duration_ms < 3000


def test_max_duration_clamps_to_ceiling(qapp: QApplication) -> None:
    parent = QWidget()
    t = Toast("hi", duration_ms=99_999, parent=parent)
    assert t._duration_ms == Toast.MAX_DURATION_MS


def test_stack_capped_at_max(qapp: QApplication) -> None:
    parent = QWidget()
    mgr = ToastManager(parent)
    toasts = [mgr.show(f"msg-{i}") for i in range(ToastManager.MAX_STACK + 2)]
    # The oldest should have been evicted as new ones arrive.
    assert len(mgr.stack) == ToastManager.MAX_STACK
    # Newest is at the top of the stack.
    assert mgr.stack[0] is toasts[-1]


def test_duplicate_message_is_deduped(qapp: QApplication) -> None:
    parent = QWidget()
    mgr = ToastManager(parent)
    a = mgr.show("same", level=ToastLevel.INFO)
    b = mgr.show("same", level=ToastLevel.INFO)
    assert a is b
    assert len(mgr.stack) == 1


def test_different_levels_not_deduped(qapp: QApplication) -> None:
    parent = QWidget()
    mgr = ToastManager(parent)
    a = mgr.show("same", level=ToastLevel.INFO)
    b = mgr.show("same", level=ToastLevel.WARNING)
    assert a is not b
    assert len(mgr.stack) == 2
