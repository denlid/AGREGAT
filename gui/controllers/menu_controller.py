"""Sprint 3.5 — Menu-bar construction and action wiring.

Owns the *File* and *View* menus that ``MainWindow`` used to build
inline. Keeps the same actions, shortcuts and click targets so the
refactor is invisible to the user (and to the existing GUI tests).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PySide6.QtWidgets import QMenuBar

from gui.styling.theme import CHART_SCHEMES

if TYPE_CHECKING:
    from gui.main_window import MainWindow

logger = logging.getLogger("StrategyTester.gui.menu")


class MenuController:
    """Builds the application menu bar and connects actions to *host*."""

    def __init__(self, host: MainWindow) -> None:
        self._host = host
        self.menu_bar = QMenuBar()
        self._build(self.menu_bar)

    def _build(self, mb: QMenuBar) -> None:
        h = self._host

        fm = mb.addMenu("File")
        fm.addAction("Load Data...", h._session_ctrl.load_data, "Ctrl+O")
        fm.addAction("Load Strategy...", h._session_ctrl.load_strategy, "Ctrl+L")
        fm.addSeparator()
        fm.addAction("Save Session...", h._session_ctrl.save_session, "Ctrl+S")
        fm.addAction("Load Session...", h._session_ctrl.load_session, "Ctrl+Shift+O")
        fm.addSeparator()
        fm.addAction("Export Trades CSV...", lambda: h.trades_tab.export_csv(), "Ctrl+E")
        fm.addAction("Export Logs...", lambda: h.log_console.export_log(), "Ctrl+Shift+E")
        fm.addSeparator()
        fm.addAction("Reset", h._replay_ctrl.on_reset, "Ctrl+R")

        vm = mb.addMenu("View")
        vm.addAction("Zoom to Fit", lambda: h.chart_tab._fit_all(), "Home")
        for tn in ("dark", "light"):
            vm.addAction(f"Theme: {tn.capitalize()}", lambda n=tn: h._theme_ctrl.switch_theme(n))
        for cn in CHART_SCHEMES:
            vm.addAction(f"Colors: {cn}", lambda n=cn: h._theme_ctrl.switch_chart_colors(n))

        mb.adjustSize()
        mb.setFixedWidth(mb.sizeHint().width())

    # ------------------------------------------------------------------
    # Cheat sheet & command palette (Ctrl+/, Ctrl+Shift+P).
    # ------------------------------------------------------------------
    def show_cheat_sheet(self) -> None:
        from gui.dialogs.cheat_sheet import CheatSheetDialog

        CheatSheetDialog(self._host).exec()

    def show_command_palette(self) -> None:
        from gui.dialogs.cheat_sheet import CommandPaletteDialog

        h = self._host
        actions = [
            ("File: Load CSV data", h._session_ctrl.load_data),
            ("File: Load strategy", h._session_ctrl.load_strategy),
            ("File: Save .agbk session", h._session_ctrl.save_session),
            ("File: Load .agbk session", h._session_ctrl.load_session),
            ("File: Export trades CSV", lambda: h.trades_tab.export_csv()),
            ("File: Export logs", lambda: h.log_console.export_log()),
            ("Run: Reset simulation", h._replay_ctrl.on_reset),
            ("Run: Toggle play / pause", h.bar_replay.toggle_play),
            ("Run: Run to end", h.bar_replay.run_to_end),
            ("View: Show hotkey cheat sheet", self.show_cheat_sheet),
            ("View: Switch to Chart tab", lambda: h.tabs.setCurrentIndex(0)),
            ("View: Switch to Stats tab", lambda: h.tabs.setCurrentIndex(1)),
            ("View: Switch to Trades tab", lambda: h.tabs.setCurrentIndex(2)),
            ("View: Switch to Analytics tab", lambda: h.tabs.setCurrentIndex(3)),
            ("View: Switch to Quant tab", lambda: h.tabs.setCurrentIndex(4)),
            ("View: Switch to Logs tab", lambda: h.tabs.setCurrentIndex(5)),
            ("View: Switch to Settings tab", lambda: h.tabs.setCurrentIndex(6)),
            ("Chart: Fit all", h.chart_tab._fit_all),
            ("Chart: Undo drawing", h._replay_ctrl.undo_drawing),
            ("Chart: Redo drawing", h._replay_ctrl.redo_drawing),
        ]
        CommandPaletteDialog(actions, h).exec()
