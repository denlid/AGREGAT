"""
EmptyState — uniform empty-state placeholder.

A composable QWidget that renders the standard "nothing to show yet" UI:

    +---------------------------+
    |          \U0001F4CA           |   <- 36px icon
    |       Headline             |   <- 15px, text2
    |       Subtitle             |   <- 11px, text_dis (optional)
    |       [ CTA Button ]       |   <- accent button (optional)
    +---------------------------+

Replaces ad-hoc "No trades…" QLabel sequences across Stats / Analytics /
Trades / Quant tabs.

Example
-------
::

    empty = EmptyState(
        icon="\U0001F4CA",
        headline="No trades yet",
        subtitle="Load data and run a strategy",
        cta_text="Load Data",
        parent=self,
    )
    empty.cta_clicked.connect(self._on_load_data)
    layout.addWidget(empty)
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from gui.styling import style_helpers as sh
from gui.styling.theme import (
    get_theme,
    register_theme_listener,
    unregister_theme_listener,
)


class EmptyState(QWidget):
    """Reusable empty-state placeholder with optional CTA.

    Emits :pyattr:`cta_clicked` when the optional call-to-action button is
    pressed. If no ``cta_text`` is supplied the button is hidden.
    """

    cta_clicked = Signal()

    def __init__(
        self,
        *,
        icon: str = "\U0001F4CA",
        headline: str = "Nothing here yet",
        subtitle: str = "",
        cta_text: str = "",
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("emptyState")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 24, 24, 24)
        outer.setSpacing(6)
        outer.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._icon_lbl = QLabel(icon)
        self._icon_lbl.setObjectName("emptyStateIcon")
        self._icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        outer.addWidget(self._icon_lbl, alignment=Qt.AlignmentFlag.AlignCenter)

        self._headline_lbl = QLabel(headline)
        self._headline_lbl.setObjectName("emptyStateHeadline")
        self._headline_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._headline_lbl.setWordWrap(True)
        outer.addWidget(self._headline_lbl)

        self._subtitle_lbl = QLabel(subtitle)
        self._subtitle_lbl.setObjectName("emptyStateSubtitle")
        self._subtitle_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._subtitle_lbl.setWordWrap(True)
        self._subtitle_lbl.setVisible(bool(subtitle))
        outer.addWidget(self._subtitle_lbl)

        self._cta_btn = QPushButton(cta_text or "")
        self._cta_btn.setObjectName("emptyStateCta")
        self._cta_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._cta_btn.setFixedHeight(30)
        self._cta_btn.setMinimumWidth(120)
        self._cta_btn.clicked.connect(self.cta_clicked.emit)
        self._cta_btn.setVisible(bool(cta_text))
        outer.addWidget(self._cta_btn, alignment=Qt.AlignmentFlag.AlignCenter)

        self._apply_theme()
        register_theme_listener(self._apply_theme)
        self._theme_registered = True

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def closeEvent(self, e):  # type: ignore[override]
        self._teardown()
        super().closeEvent(e)

    def destroy(self, destroyWindow=True, destroySubWindows=True):  # type: ignore[override]
        self._teardown()
        super().destroy(destroyWindow, destroySubWindows)

    def _teardown(self) -> None:
        if getattr(self, "_theme_registered", False):
            unregister_theme_listener(self._apply_theme)
            self._theme_registered = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_icon(self, icon: str) -> None:
        self._icon_lbl.setText(icon)

    def set_headline(self, text: str) -> None:
        self._headline_lbl.setText(text)

    def set_subtitle(self, text: str) -> None:
        self._subtitle_lbl.setText(text)
        self._subtitle_lbl.setVisible(bool(text))

    def set_cta(self, text: str) -> None:
        self._cta_btn.setText(text)
        self._cta_btn.setVisible(bool(text))

    # ------------------------------------------------------------------
    # Theming
    # ------------------------------------------------------------------

    def _apply_theme(self) -> None:
        t = get_theme()
        self.setStyleSheet(f"QWidget#emptyState {{ background: {sh.c(t, 'bg')}; }}")
        self._icon_lbl.setStyleSheet(
            "QLabel#emptyStateIcon { font-size: 36px; background: transparent; }"
        )
        self._headline_lbl.setStyleSheet(
            "QLabel#emptyStateHeadline {"
            f"  color: {sh.c(t, 'text2')}; font-size: 15px;"
            "  font-weight: 600; background: transparent;"
            "}"
        )
        self._subtitle_lbl.setStyleSheet(
            "QLabel#emptyStateSubtitle {"
            f"  color: {sh.c(t, 'text_dis')}; font-size: 11px;"
            "  background: transparent;"
            "}"
        )
        accent = sh.c(t, "accent")
        accent_hover = sh.c(t, "accent_light")
        self._cta_btn.setStyleSheet(
            "QPushButton#emptyStateCta {"
            f"  background: {accent}; color: #FFFFFF;"
            f"  border: 1px solid {accent}; border-radius: 6px;"
            "  padding: 6px 14px; font-weight: 600; font-size: 11px;"
            "}"
            "QPushButton#emptyStateCta:hover {"
            f"  background: {accent_hover}; border-color: {accent_hover};"
            "}"
            "QPushButton#emptyStateCta:pressed {"
            f"  background: {accent};"
            "}"
        )


__all__ = ["EmptyState"]
