"""Phase 2 EPIC 2.2 — InsightCard.

Compact widget that surfaces a single :class:`gui.analytics.insights_engine.Insight`.

Layout (Retail mode)
--------------------
    [icon]  HEADLINE                       [×]
            One- to two-sentence detail.
            [Suggested action button]   (optional)

Severity drives the left-edge stripe colour and the icon:

* ``info``     — soft blue stripe, 💡
* ``warning``  — amber stripe, ⚠
* ``critical`` — red stripe, 🔥

In Quant mode the icon is hidden via :meth:`set_compact`.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from gui.analytics.insights_engine import Insight
from gui.styling import style_helpers as sh
from gui.styling.theme import get_theme, register_theme_listener, unregister_theme_listener


_SEVERITY_ICON = {
    "info": "💡",
    "warning": "⚠",
    "critical": "🔥",
}

_SEVERITY_THEME_KEY = {
    "info": "accent_light",
    "warning": "amber",
    "critical": "red_bright",
}


class InsightCard(QFrame):
    """Single-insight surface.

    Emits :pyattr:`action_clicked` when the suggested action button is
    pressed and :pyattr:`dismissed` when the close button is pressed.
    """

    action_clicked = Signal()
    dismissed = Signal(str)  # rule_id

    def __init__(
        self,
        insight: Insight,
        *,
        compact: bool = False,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._insight = insight
        self._compact = bool(compact)
        self.setObjectName("insightCard")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

        outer = QHBoxLayout(self)
        outer.setContentsMargins(12, 10, 12, 10)
        outer.setSpacing(10)

        self._icon_lbl = QLabel(_SEVERITY_ICON.get(insight.severity, "💡"))
        self._icon_lbl.setVisible(not self._compact)
        outer.addWidget(self._icon_lbl, 0, Qt.AlignmentFlag.AlignTop)

        body = QVBoxLayout()
        body.setSpacing(2)

        top_row = QHBoxLayout()
        top_row.setSpacing(6)
        self._headline_lbl = QLabel(insight.headline)
        self._headline_lbl.setWordWrap(True)
        top_row.addWidget(self._headline_lbl, 1)
        self._close_btn = QPushButton("✕")
        self._close_btn.setFixedSize(18, 18)
        self._close_btn.setFlat(True)
        self._close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._close_btn.clicked.connect(self._on_dismiss)
        top_row.addWidget(self._close_btn, 0, Qt.AlignmentFlag.AlignTop)
        body.addLayout(top_row)

        self._detail_lbl = QLabel(insight.detail)
        self._detail_lbl.setWordWrap(True)
        body.addWidget(self._detail_lbl)

        if insight.action:
            self._action_btn = QPushButton(insight.action)
            self._action_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self._action_btn.clicked.connect(self.action_clicked.emit)
            body.addWidget(self._action_btn, 0, Qt.AlignmentFlag.AlignLeft)
        else:
            self._action_btn = None

        outer.addLayout(body, 1)

        self._apply_theme()
        register_theme_listener(self._apply_theme)
        self._theme_registered = True

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def insight(self) -> Insight:
        return self._insight

    @property
    def rule_id(self) -> str:
        return self._insight.rule_id

    def set_compact(self, compact: bool) -> None:
        self._compact = bool(compact)
        self._icon_lbl.setVisible(not self._compact)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def closeEvent(self, e):  # type: ignore[override]
        if getattr(self, "_theme_registered", False):
            try:
                unregister_theme_listener(self._apply_theme)
            except Exception:
                pass
            self._theme_registered = False
        super().closeEvent(e)

    def _on_dismiss(self) -> None:
        self.dismissed.emit(self._insight.rule_id)
        self.setVisible(False)

    def _apply_theme(self) -> None:
        t = get_theme()
        sev_key = _SEVERITY_THEME_KEY.get(self._insight.severity, "accent_light")
        accent = sh.c(t, sev_key)
        # Tinted background derived from accent.
        c = QColor(accent)
        c.setAlpha(32)
        bg = f"rgba({c.red()}, {c.green()}, {c.blue()}, {c.alpha() / 255:.3f})"
        self.setStyleSheet(
            f"#insightCard {{"
            f"  background: {bg};"
            f"  border: 1px solid {accent};"
            f"  border-left: 4px solid {accent};"
            f"  border-radius: 8px;"
            f"}}"
            f"QLabel {{ color: {sh.c(t, 'text')}; }}"
            f"QPushButton {{ background: transparent; color: {sh.c(t, 'text2')}; "
            f"               border: none; }}"
            f"QPushButton:hover {{ color: {sh.c(t, 'text')}; }}"
        )
        head_color = accent
        self._headline_lbl.setStyleSheet(
            f"font-weight: 600; font-size: 12px; color: {head_color};"
        )
        self._detail_lbl.setStyleSheet(
            f"color: {sh.c(t, 'text2')}; font-size: 11px;"
        )
        if self._action_btn is not None:
            self._action_btn.setStyleSheet(
                f"QPushButton {{"
                f"  color: {accent}; background: transparent;"
                f"  border: 1px solid {accent}; border-radius: 4px;"
                f"  padding: 3px 10px; font-size: 11px; font-weight: 500;"
                f"}}"
                f"QPushButton:hover {{"
                f"  background: {bg};"
                f"}}"
            )
        self._icon_lbl.setStyleSheet(f"font-size: 16px; color: {accent};")


__all__ = ["InsightCard"]
