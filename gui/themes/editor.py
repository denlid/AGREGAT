"""Sprint 3.6 — User theme editor widget for Settings → UI.

Provides :class:`ThemeEditorWidget` — a scrollable panel that lists
every palette role with an editable colour swatch and hex input.
Clicking a swatch opens a ``QColorDialog``; changes are previewed
live. A **Save as…** button persists the custom theme to
``gui/themes/<name>.qss`` + registers it in ``THEMES``, making it
available in the dropdown without an app restart.

The editor operates on a *working copy* of the palette dict so the
active theme isn't mutated until the user explicitly saves.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Dict, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QColorDialog,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger("StrategyTester.gui.themes.editor")

_THEMES_DIR = Path(__file__).resolve().parent
_USER_THEMES_FILE = _THEMES_DIR / "user_themes.json"


def _load_user_themes() -> Dict[str, Dict[str, str]]:
    """Read user-defined themes from disk."""
    if not _USER_THEMES_FILE.is_file():
        return {}
    try:
        return json.loads(_USER_THEMES_FILE.read_text(encoding="utf-8"))  # type: ignore[no-any-return]
    except Exception:
        logger.exception("Failed to read user_themes.json")
        return {}


def _save_user_themes(themes: Dict[str, Dict[str, str]]) -> None:
    """Persist user themes to disk."""
    _USER_THEMES_FILE.write_text(json.dumps(themes, indent=2), encoding="utf-8")


class ColorSwatchButton(QPushButton):
    """Small coloured square that opens a colour picker on click."""

    colorChanged = Signal(str, str)  # role, new_hex

    def __init__(self, role: str, hex_color: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.role = role
        self._hex = hex_color
        self.setFixedSize(28, 28)
        self._apply_color(hex_color)
        self.clicked.connect(self._pick)

    def _apply_color(self, hex_color: str) -> None:
        self._hex = hex_color
        self.setStyleSheet(
            f"background-color: {hex_color}; border: 1px solid #888; border-radius: 3px;"
        )
        self.setToolTip(f"{self.role}: {hex_color}")

    def _pick(self) -> None:
        color = QColorDialog.getColor(
            QColor(self._hex), self.parent(), f"Pick colour for '{self.role}'"
        )
        if color.isValid():
            new_hex = color.name()
            self._apply_color(new_hex)
            self.colorChanged.emit(self.role, new_hex)


class ThemeEditorWidget(QWidget):
    """Inline theme editor panel for the Settings tab."""

    themeApplied = Signal(str)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._working: Dict[str, str] = {}
        self._swatches: Dict[str, ColorSwatchButton] = {}
        self._hex_inputs: Dict[str, QLineEdit] = {}
        self._init_ui()

    def _init_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        # Header
        header = QHBoxLayout()
        header.addWidget(QLabel("<b>Theme Editor</b> — customise palette roles"))
        header.addStretch()

        btn_save = QPushButton("Save as…")
        btn_save.setToolTip("Save current edits as a named custom theme")
        btn_save.clicked.connect(self._on_save)
        header.addWidget(btn_save)

        btn_reset = QPushButton("Reset")
        btn_reset.setToolTip("Revert edits to the active theme's defaults")
        btn_reset.clicked.connect(self._reload_from_active)
        header.addWidget(btn_reset)

        root.addLayout(header)

        # Scroll area with colour roles
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll_content = QWidget()
        self._form = QFormLayout(self._scroll_content)
        self._form.setContentsMargins(4, 4, 4, 4)
        scroll.setWidget(self._scroll_content)
        root.addWidget(scroll)

        self._reload_from_active()

    def _reload_from_active(self) -> None:
        """Populate the editor from the currently active palette."""
        from gui.themes.service import ThemeService

        palette = ThemeService.palette()
        self._working = dict(palette)

        # Clear form
        while self._form.rowCount() > 0:
            self._form.removeRow(0)
        self._swatches.clear()
        self._hex_inputs.clear()

        for role, hex_val in sorted(palette.items()):
            row = QHBoxLayout()
            swatch = ColorSwatchButton(role, hex_val)
            swatch.colorChanged.connect(self._on_color_changed)
            self._swatches[role] = swatch
            row.addWidget(swatch)

            hex_input = QLineEdit(hex_val)
            hex_input.setFixedWidth(90)
            hex_input.setProperty("role", role)
            hex_input.editingFinished.connect(
                lambda r=role, inp=hex_input: self._on_hex_edited(r, inp)
            )
            self._hex_inputs[role] = hex_input
            row.addWidget(hex_input)
            row.addStretch()

            container = QWidget()
            container.setLayout(row)
            self._form.addRow(QLabel(role), container)

    def _on_color_changed(self, role: str, new_hex: str) -> None:
        self._working[role] = new_hex
        inp = self._hex_inputs.get(role)
        if inp:
            inp.setText(new_hex)

    def _on_hex_edited(self, role: str, inp: QLineEdit) -> None:
        text = inp.text().strip()
        if QColor(text).isValid():
            self._working[role] = text
            swatch = self._swatches.get(role)
            if swatch:
                swatch._apply_color(text)

    def _on_save(self) -> None:
        """Save the working palette as a named custom theme."""
        name, ok = QInputDialog.getText(self, "Save Theme", "Theme name (lowercase, no spaces):")
        if not ok or not name:
            return
        name = name.strip().lower().replace(" ", "_")
        if not name:
            return

        # Register in the THEMES dict so ThemeService sees it.
        from gui.styling.theme import THEMES

        THEMES[name] = dict(self._working)

        # Persist to user_themes.json
        user_themes = _load_user_themes()
        user_themes[name] = dict(self._working)
        _save_user_themes(user_themes)

        # Create a minimal .qss file so ThemeService.available_themes()
        # picks it up.
        qss_path = _THEMES_DIR / f"{name}.qss"
        if not qss_path.is_file():
            qss_path.write_text(
                f"/* Custom theme '{name}' — auto-generated */\n",
                encoding="utf-8",
            )

        self.themeApplied.emit(name)
        logger.info("Custom theme '%s' saved with %d roles.", name, len(self._working))
        QMessageBox.information(
            self,
            "Theme saved",
            f"Theme '{name}' saved. Select it in the theme dropdown.",
        )


def register_user_themes() -> None:
    """Load user themes from disk into ``THEMES`` at startup."""
    from gui.styling.theme import THEMES

    for name, palette in _load_user_themes().items():
        if name not in THEMES:
            THEMES[name] = palette
