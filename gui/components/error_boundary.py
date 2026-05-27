"""ErrorBoundary — catch tab render crashes and show fallback UI."""

from __future__ import annotations

import logging
import traceback

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QLabel, QPushButton, QSizePolicy, QStackedWidget, QVBoxLayout, QWidget

from gui.styling import style_helpers as sh
from gui.styling.theme import get_theme, register_theme_listener, unregister_theme_listener

logger = logging.getLogger("StrategyTester.gui.error_boundary")


class ErrorBoundary(QWidget):
    retry = Signal()

    def __init__(self, inner: QWidget, fallback_text: str = "Something went wrong", parent=None):
        super().__init__(parent)
        self._inner = inner
        self._stack = QStackedWidget(self)

        # Fallback page
        self._fallback = QWidget()
        fb_layout = QVBoxLayout(self._fallback)
        fb_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        fb_layout.setSpacing(12)

        self._fb_icon = QLabel("\u26A0\uFE0F")
        self._fb_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._fb_icon.setStyleSheet("font-size: 48px; background: transparent;")
        fb_layout.addWidget(self._fb_icon)

        self._fb_text = QLabel(fallback_text)
        self._fb_text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._fb_text.setWordWrap(True)
        fb_layout.addWidget(self._fb_text)

        self._fb_detail = QLabel("")
        self._fb_detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._fb_detail.setWordWrap(True)
        self._fb_detail.setVisible(False)
        fb_layout.addWidget(self._fb_detail)

        self._fb_retry = QPushButton("Retry")
        self._fb_retry.setFixedWidth(120)
        self._fb_retry.setFixedHeight(30)
        self._fb_retry.setCursor(Qt.CursorShape.PointingHandCursor)
        self._fb_retry.clicked.connect(self._on_retry)
        fb_layout.addWidget(self._fb_retry, alignment=Qt.AlignmentFlag.AlignCenter)

        self._stack.addWidget(self._inner)
        self._stack.addWidget(self._fallback)
        self._stack.setCurrentWidget(self._inner)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(self._stack)

        self._apply_theme()
        register_theme_listener(self._apply_theme)

    def closeEvent(self, e: QCloseEvent) -> None:
        unregister_theme_listener(self._apply_theme)
        super().closeEvent(e)

    def show_fallback(self, error_msg: str = "") -> None:
        logger.warning("ErrorBoundary fallback: %s", error_msg)
        self._fb_detail.setText(error_msg[:500] if error_msg else "")
        self._fb_detail.setVisible(bool(error_msg))
        self._stack.setCurrentWidget(self._fallback)

    def reset(self) -> None:
        self._stack.setCurrentWidget(self._inner)

    def inner(self) -> QWidget:
        return self._inner

    def _on_retry(self) -> None:
        self._stack.setCurrentWidget(self._inner)
        self.retry.emit()

    def _apply_theme(self) -> None:
        t = get_theme()
        self._fb_text.setStyleSheet(
            f"color: {sh.c(t, 'text2')}; font-size: 15px; font-weight: 600; background: transparent;"
        )
        self._fb_detail.setStyleSheet(
            f"color: {sh.c(t, 'text3')}; font-size: 11px; background: transparent;"
        )
        self._fb_retry.setStyleSheet(
            f"QPushButton {{ background: {sh.c(t, 'accent')}; color: #FFFFFF;"
            f" border: none; border-radius: 6px; padding: 6px 14px; font-weight: 600; }}"
            f"QPushButton:hover {{ background: {sh.c(t, 'accent_light')}; }}"
        )
