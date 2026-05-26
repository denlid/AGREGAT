"""Phase 1 EPIC 1.4 — Tests for MetricCard v2 features (pin/hide/explain, tween, fade)."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from gui.widgets.metric_card import MetricCard, Threshold, _try_parse_number


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_try_parse_number_handles_common_formats() -> None:
    assert _try_parse_number("1234") == 1234.0
    assert _try_parse_number("-12.50") == -12.50
    assert _try_parse_number("+12.50") == 12.50
    assert _try_parse_number("$1,234.50") == 1234.50
    assert _try_parse_number("-$1,234.50") == -1234.50
    assert _try_parse_number("12.5%") == 12.5
    assert _try_parse_number("—") is None
    assert _try_parse_number("12W") == 12.0


def test_threshold_evaluate() -> None:
    th = Threshold(good=1.5, amber=1.0)
    assert th.evaluate(2.0) == "green"
    assert th.evaluate(1.2) == "amber"
    assert th.evaluate(0.5) == "red"


def test_threshold_inverted() -> None:
    # Lower-is-better metric (e.g. Max DD).
    th = Threshold(good=10.0, amber=20.0, inverted=True)
    assert th.evaluate(5.0) == "green"
    assert th.evaluate(15.0) == "amber"
    assert th.evaluate(25.0) == "red"


def test_pin_toggles_signal_and_state(qapp: QApplication) -> None:
    card = MetricCard("Sharpe", "1.23")
    received: list[bool] = []
    card.pin_toggled.connect(lambda p: received.append(p))
    assert not card.is_pinned()
    card.set_pinned(True)
    assert card.is_pinned()
    assert received == [True]
    card.set_pinned(False)
    assert not card.is_pinned()
    assert received == [True, False]


def test_pin_idempotent(qapp: QApplication) -> None:
    card = MetricCard("Sharpe", "1.23")
    received: list[bool] = []
    card.pin_toggled.connect(lambda p: received.append(p))
    card.set_pinned(False)
    card.set_pinned(False)
    assert received == []


def test_hide_requested_signal(qapp: QApplication) -> None:
    card = MetricCard("Sharpe", "1.23")
    received: list[int] = []
    card.hide_requested.connect(lambda: received.append(1))
    card.hide_requested.emit()
    assert received == [1]


def test_set_formula_attaches_metadata(qapp: QApplication) -> None:
    card = MetricCard("Sharpe", "1.23")
    card.set_formula(
        "mean / std * sqrt(252)",
        explanation="Annualised Sharpe.",
        reference="https://example.com",
    )
    assert card._formula == "mean / std * sqrt(252)"
    assert card._explanation == "Annualised Sharpe."
    assert card._reference == "https://example.com"


def test_set_metric_with_threshold_sets_color(qapp: QApplication) -> None:
    th = Threshold(good=1.5, amber=1.0)
    card = MetricCard("Sharpe", "0.00", threshold=th)
    card.set_metric(2.0, formatted="2.00", animate=False)
    assert card._color == "green"
    card.set_metric(1.2, formatted="1.20", animate=False)
    assert card._color == "amber"
    card.set_metric(0.5, formatted="0.50", animate=False)
    assert card._color == "red"


def test_set_value_text_numeric_uses_tween(qapp: QApplication) -> None:
    card = MetricCard("PnL", "$0.00")
    card.set_value_text("$1,234.50", animate=False)
    assert "1,234" in card._value_lbl.text() or "1234" in card._value_lbl.text()


def test_set_value_text_non_numeric_fade(qapp: QApplication) -> None:
    card = MetricCard("Streak", "5W")
    card.set_value_text("12W", animate=False)
    assert card._value_lbl.text() == "12W"


def test_format_tween_value_preserves_prefix_suffix(qapp: QApplication) -> None:
    card = MetricCard("PnL", "$0.00")
    rendered = card._format_tween_value(1234.5, "$1,234.50", "")
    # Should include $ prefix.
    assert rendered.startswith("$")
    rendered_pct = card._format_tween_value(5.32, "5.32%", "")
    assert rendered_pct.endswith("%")


def test_constructor_with_formula(qapp: QApplication) -> None:
    card = MetricCard(
        "Sharpe",
        "1.23",
        formula="m / s * sqrt(252)",
        explanation="Annualised Sharpe.",
        reference="https://example.com",
    )
    assert card._formula == "m / s * sqrt(252)"
    assert card._explanation == "Annualised Sharpe."


def test_legacy_set_value_alias(qapp: QApplication) -> None:
    """Legacy MiniCard call-sites use ``set_value`` — verify the alias works."""
    card = MetricCard("Trades", "0")
    card.set_value("42", color="green")
    assert "42" in card._value_lbl.text()
