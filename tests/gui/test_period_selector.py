"""Phase 1 EPIC 1.3 — Tests for gui.widgets.period_selector.PeriodSelector."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pandas as pd
import pytest
from PySide6.QtWidgets import QApplication

from gui.widgets.period_selector import PRESETS, PeriodSelector


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def _collect(selector: PeriodSelector) -> list[tuple[str, object, object]]:
    received: list[tuple[str, object, object]] = []
    selector.period_changed.connect(
        lambda kind, start, end: received.append((kind, start, end))
    )
    return received


def test_defaults_to_all(qapp: QApplication) -> None:
    s = PeriodSelector()
    assert s.kind == "ALL"
    assert s._buttons["ALL"].isChecked()


def test_set_kind_emits_signal(qapp: QApplication) -> None:
    s = PeriodSelector()
    received = _collect(s)
    s.set_kind("1M")
    assert s.kind == "1M"
    assert received == [("1M", None, None)]
    assert s._buttons["1M"].isChecked()
    assert not s._buttons["ALL"].isChecked()


def test_set_kind_unknown_is_ignored(qapp: QApplication) -> None:
    s = PeriodSelector()
    received = _collect(s)
    s.set_kind("BOGUS")
    assert received == []
    assert s.kind == "ALL"


def test_emit_flag_suppresses_signal(qapp: QApplication) -> None:
    s = PeriodSelector()
    received = _collect(s)
    s.set_kind("1W", emit=False)
    assert received == []
    assert s.kind == "1W"


def test_button_click_changes_state(qapp: QApplication) -> None:
    s = PeriodSelector()
    received = _collect(s)
    s._buttons["3M"].click()
    assert s.kind == "3M"
    assert received and received[-1] == ("3M", None, None)


def test_set_custom_range_emits_with_timestamps(qapp: QApplication) -> None:
    s = PeriodSelector()
    received = _collect(s)
    s.set_custom_range(pd.Timestamp("2024-01-01"), pd.Timestamp("2024-02-01"))
    assert s.kind == "CUSTOM"
    assert s._custom_btn.isChecked()
    assert received[-1][0] == "CUSTOM"
    assert received[-1][1] == pd.Timestamp("2024-01-01")
    assert received[-1][2] == pd.Timestamp("2024-02-01")


def test_save_restore_state_roundtrips_preset(qapp: QApplication) -> None:
    s = PeriodSelector()
    s.set_kind("YTD", emit=False)
    snapshot = s.save_state()
    assert snapshot["kind"] == "YTD"
    s2 = PeriodSelector()
    s2.restore_state(snapshot)
    assert s2.kind == "YTD"


def test_save_restore_state_roundtrips_custom(qapp: QApplication) -> None:
    s = PeriodSelector()
    s.set_custom_range(
        pd.Timestamp("2023-06-01"), pd.Timestamp("2023-12-31"), emit=False
    )
    snapshot = s.save_state()
    s2 = PeriodSelector()
    s2.restore_state(snapshot)
    assert s2.kind == "CUSTOM"
    assert s2._custom_start == pd.Timestamp("2023-06-01")
    assert s2._custom_end == pd.Timestamp("2023-12-31")


def test_restore_state_invalid_falls_back_to_all(qapp: QApplication) -> None:
    s = PeriodSelector()
    s.restore_state({"kind": "BOGUS"})
    assert s.kind == "ALL"


def test_presets_are_eight(qapp: QApplication) -> None:
    assert len(PRESETS) == 8
    assert "ALL" in PRESETS
