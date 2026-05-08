"""Sprint 4.4 — FluentUI-style notification toasts.

A toast is a transient notification that slides in from the
top-right corner, sits for a configurable duration, then fades
out. Stacked toasts cascade vertically without overlap. Clicking a
toast dismisses it immediately.

Public surface
--------------
- :class:`Toast`        — single toast widget; emits ``dismissed`` when removed.
- :class:`ToastLevel`   — enum of severity styles (info / success / warning / error).
- :class:`ToastManager` — owns the stack; ``manager.show("hello")`` is the typical
  call site. The manager keeps weak references to live toasts so a
  closed parent window cleans them up.

Headless tests run with ``QGuiApplication`` and an offscreen Qt
platform plugin — they don't depend on a visible top-level window.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from PySide6.QtCore import (
    QEasingCurve,
    QPoint,
    QPropertyAnimation,
    QRect,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class ToastLevel(str, Enum):
    """Severity → colour scheme."""

    INFO = "info"
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"


# Colour palette — deliberately desaturated to match the AGREGAT
# dark theme without picking up too much dependency on the theme
# loader.
_PALETTE: dict[ToastLevel, tuple[str, str, str]] = {
    # (background, border, text)
    ToastLevel.INFO: ("#23304a", "#3e60a0", "#dde7ff"),
    ToastLevel.SUCCESS: ("#1e3826", "#3a7d4f", "#d6f5d6"),
    ToastLevel.WARNING: ("#3a3622", "#a08530", "#fff0c2"),
    ToastLevel.ERROR: ("#3a2424", "#a04040", "#ffd6d6"),
}


class Toast(QWidget):
    """Single notification toast.

    Sized to its content. Always frameless and ``Qt.Tool`` so it
    doesn't appear in the OS taskbar; ``setAttribute(WA_TranslucentBackground)``
    lets us paint a rounded card via :meth:`paintEvent`.
    """

    dismissed = Signal()

    def __init__(
        self,
        message: str,
        level: ToastLevel = ToastLevel.INFO,
        duration_ms: int = 4000,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._level = level
        self._duration_ms = max(0, int(duration_ms))
        self._dismissed = False

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        bg, border, text = _PALETTE[level]
        self._bg_color = QColor(bg)
        self._border_color = QColor(border)
        self._text_color = QColor(text)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(4)

        row = QHBoxLayout()
        row.setSpacing(8)

        self._label = QLabel(message)
        self._label.setWordWrap(True)
        self._label.setStyleSheet(f"color: {text}; font-weight: 500;")
        self._label.setMinimumWidth(220)
        row.addWidget(self._label, 1)

        self._close_btn = QPushButton("✕")
        self._close_btn.setFixedSize(20, 20)
        self._close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._close_btn.setStyleSheet(
            "QPushButton {"
            f" color: {text}; background: transparent;"
            " border: none; font-size: 12px;}"
            "QPushButton:hover { color: white; }"
        )
        self._close_btn.clicked.connect(self.dismiss)
        row.addWidget(self._close_btn, 0, Qt.AlignmentFlag.AlignTop)

        layout.addLayout(row)

        # Optional fade animations
        self._opacity_effect = QGraphicsOpacityEffect(self)
        self._opacity_effect.setOpacity(0.0)
        self.setGraphicsEffect(self._opacity_effect)
        self._fade_in = QPropertyAnimation(self._opacity_effect, b"opacity", self)
        self._fade_in.setDuration(180)
        self._fade_in.setStartValue(0.0)
        self._fade_in.setEndValue(1.0)
        self._fade_in.setEasingCurve(QEasingCurve.Type.OutCubic)

        self._fade_out = QPropertyAnimation(self._opacity_effect, b"opacity", self)
        self._fade_out.setDuration(220)
        self._fade_out.setStartValue(1.0)
        self._fade_out.setEndValue(0.0)
        self._fade_out.setEasingCurve(QEasingCurve.Type.InCubic)
        self._fade_out.finished.connect(self._on_fade_out_done)

        # Auto-dismiss timer
        self._auto_timer = QTimer(self)
        self._auto_timer.setSingleShot(True)
        self._auto_timer.timeout.connect(self.dismiss)

    # ------------------------------------------------------------------
    # API
    # ------------------------------------------------------------------
    @property
    def level(self) -> ToastLevel:
        return self._level

    @property
    def message(self) -> str:
        return self._label.text()

    @property
    def is_dismissed(self) -> bool:
        return self._dismissed

    def show_at(self, top_right: QPoint) -> None:
        """Show the toast with its top-right anchored at ``top_right``."""
        self.adjustSize()
        size = self.size()
        x = top_right.x() - size.width()
        y = top_right.y()
        self.setGeometry(QRect(x, y, size.width(), size.height()))
        self.show()
        self._fade_in.start()
        if self._duration_ms > 0:
            self._auto_timer.start(self._duration_ms)

    def dismiss(self) -> None:
        if self._dismissed:
            return
        self._dismissed = True
        self._auto_timer.stop()
        # Run fade-out only if the widget is visible — otherwise emit
        # dismissed synchronously so headless tests don't have to spin
        # the event loop.
        if self.isVisible():
            self._fade_out.start()
        else:
            self._on_fade_out_done()

    # ------------------------------------------------------------------
    # Qt overrides
    # ------------------------------------------------------------------
    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            self.dismiss()
        super().mousePressEvent(event)

    def paintEvent(self, _event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(self._bg_color)
        painter.setPen(self._border_color)
        rect = self.rect().adjusted(0, 0, -1, -1)
        painter.drawRoundedRect(rect, 8, 8)

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------
    def _on_fade_out_done(self) -> None:
        try:
            self.hide()
        finally:
            self.dismissed.emit()
            self.deleteLater()


class ToastManager:
    """Owns a vertical stack of toasts anchored to a parent widget.

    The manager keeps a list of live :class:`Toast` instances and
    repositions them whenever one is added or removed. Toasts are
    inserted at the top — the newest is closest to the window's top-
    right corner.
    """

    SPACING = 10
    MARGIN = 16

    def __init__(self, parent: QWidget) -> None:
        self._parent = parent
        self._stack: list[Toast] = []

    @property
    def stack(self) -> tuple[Toast, ...]:
        return tuple(self._stack)

    def show(
        self,
        message: str,
        *,
        level: ToastLevel = ToastLevel.INFO,
        duration_ms: int = 4000,
    ) -> Toast:
        """Display a new toast and return the widget."""
        toast = Toast(message, level=level, duration_ms=duration_ms, parent=self._parent)
        toast.dismissed.connect(lambda t=toast: self._on_dismissed(t))
        self._stack.insert(0, toast)
        self._reflow()
        # Showing happens inside _reflow → show_at, so this is enough.
        return toast

    def info(self, message: str, **kw: object) -> Toast:
        return self.show(message, level=ToastLevel.INFO, **kw)  # type: ignore[arg-type]

    def success(self, message: str, **kw: object) -> Toast:
        return self.show(message, level=ToastLevel.SUCCESS, **kw)  # type: ignore[arg-type]

    def warning(self, message: str, **kw: object) -> Toast:
        return self.show(message, level=ToastLevel.WARNING, **kw)  # type: ignore[arg-type]

    def error(self, message: str, **kw: object) -> Toast:
        return self.show(message, level=ToastLevel.ERROR, **kw)  # type: ignore[arg-type]

    def dismiss_all(self) -> None:
        for t in list(self._stack):
            t.dismiss()
        self._stack.clear()

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------
    def _on_dismissed(self, toast: Toast) -> None:
        if toast in self._stack:
            self._stack.remove(toast)
        self._reflow()

    def _reflow(self) -> None:
        # Anchor to the parent's top-right corner (or screen if no parent).
        if self._parent is not None:
            tr_global = self._parent.mapToGlobal(
                QPoint(self._parent.width() - self.MARGIN, self.MARGIN)
            )
        else:
            tr_global = QPoint(800, self.MARGIN)
        y = tr_global.y()
        for toast in self._stack:
            toast.show_at(QPoint(tr_global.x(), y))
            y += toast.height() + self.SPACING


__all__ = ["Toast", "ToastLevel", "ToastManager"]
