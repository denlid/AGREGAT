"""Hotkey cheat sheet (Ctrl+/) and command palette (Ctrl+Shift+P)."""

from __future__ import annotations

from typing import Callable, List, Tuple

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
)

HOTKEYS: List[Tuple[str, str]] = [
    ("Space", "Play / pause bar replay"),
    ("Shift + → / ←", "Step one candle forward / back"),
    ("→ / ←", "Scroll chart"),
    ("Home", "Fit all candles"),
    ("Esc", "Cancel current drawing tool"),
    ("Ctrl + = / -", "Zoom X axis"),
    ("Ctrl + Shift + = / -", "Zoom Y axis"),
    ("Ctrl + Z / Ctrl + Shift + Z", "Undo / redo drawing"),
    ("Ctrl + O", "Load CSV data"),
    ("Ctrl + L", "Load strategy"),
    ("Ctrl + S / Ctrl + Shift + O", "Save / load .agbk session"),
    ("Ctrl + E", "Export trades CSV"),
    ("Ctrl + Shift + E", "Export logs"),
    ("Ctrl + Alt + R", "Run to end (fast simulation)"),
    ("Ctrl + R", "Reset simulation"),
    ("Ctrl + /", "This cheat sheet"),
    ("Ctrl + Shift + P", "Command palette (fuzzy actions)"),
]


class CheatSheetDialog(QDialog):
    """Read-only popup with all hotkeys."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Hotkeys")
        self.resize(520, 480)
        layout = QVBoxLayout(self)
        from gui.styling import style_helpers as sh
        from gui.styling.theme import font_mono, get_theme

        t = get_theme()
        title = QLabel("Hotkey reference")
        title.setStyleSheet(f"color: {sh.c(t, 'text')}; font-size: 14px; font-weight: 700;")
        layout.addWidget(title)

        listw = QListWidget()
        listw.setStyleSheet(
            "QListWidget {"
            f"  background: {sh.c(t, 'surface')};"
            f"  border: 1px solid {sh.c(t, 'border')};"
            f"  color: {sh.c(t, 'text')};"
            f"  font-family: {font_mono()};"
            "  font-size: 11px;}"
            "QListWidget::item{padding:4px 8px;}"
        )
        for keys, action in HOTKEYS:
            QListWidgetItem(f"  {keys:<32}  {action}", listw)
        layout.addWidget(listw)


class CommandPaletteDialog(QDialog):
    """Fuzzy-search dialog over a list of registered actions."""

    def __init__(self, actions: List[Tuple[str, Callable[[], None]]], parent=None):
        super().__init__(parent)
        self._actions = actions
        self.setWindowTitle("Command palette")
        self.resize(540, 420)
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, False)

        layout = QVBoxLayout(self)
        self.search = QLineEdit()
        from gui.styling import style_helpers as sh
        from gui.styling.theme import font_mono, get_theme

        t = get_theme()
        self.search.setPlaceholderText("Type to filter… (Enter to run)")
        self.search.setStyleSheet(
            "QLineEdit {"
            f"  background: {sh.c(t, 'elevated')};"
            f"  color: {sh.c(t, 'text')};"
            f"  border: 1px solid {sh.c(t, 'border')};"
            "  border-radius: 4px; padding: 6px 10px; font-size: 13px;}"
        )
        layout.addWidget(self.search)

        self.listw = QListWidget()
        self.listw.setStyleSheet(
            "QListWidget {"
            f"  background: {sh.c(t, 'surface')};"
            f"  border: 1px solid {sh.c(t, 'border')};"
            f"  color: {sh.c(t, 'text')};"
            f"  font-family: {font_mono()};"
            "  font-size: 12px;}"
            "QListWidget::item{padding:5px 10px;}"
            "QListWidget::item:selected {"
            f"  background: {sh.c(t, 'accent')};"
            "  color: #FFFFFF;}"
        )
        layout.addWidget(self.listw, stretch=1)

        self.search.textChanged.connect(self._refilter)
        self.search.returnPressed.connect(self._run_selected)
        self.listw.itemActivated.connect(lambda _: self._run_selected())

        self._refilter("")
        self.search.setFocus()

    def _refilter(self, query: str) -> None:
        q = query.strip().lower()
        self.listw.clear()
        for label, _ in self._actions:
            if not q or q in label.lower():
                QListWidgetItem(label, self.listw)
        if self.listw.count() > 0:
            self.listw.setCurrentRow(0)

    def _run_selected(self) -> None:
        item = self.listw.currentItem()
        if not item:
            self.accept()
            return
        label = item.text()
        for action_label, fn in self._actions:
            if action_label == label:
                self.accept()
                try:
                    fn()
                except Exception:
                    import logging

                    logging.getLogger("StrategyTester.gui.cheat_sheet").exception(
                        f"Command palette action failed: {label}"
                    )
                return
        self.accept()

    def keyPressEvent(self, event):  # type: ignore[override]
        # Pipe arrow keys to the list while typing in the search box.
        if event.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down):
            self.listw.keyPressEvent(event)
            return
        super().keyPressEvent(event)


__all__ = ["CheatSheetDialog", "CommandPaletteDialog", "HOTKEYS"]
