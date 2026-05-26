"""
Log Console — Thread-safe GUI log handler with auto-save functionality.

<<<<<<< HEAD
Phase 3 changes:
=======
Phase 1 changes:
>>>>>>> origin/main
  • The auto-save file is held open for the lifetime of the LogConsole
    instead of opened/closed on every `append_log` call (was 100s of syscalls/sec
    during step_n bulk runs).
  • The buffered handle is flushed eagerly so logs survive crashes.
  • Max-lines is configurable from Settings via `set_max_lines()`.
<<<<<<< HEAD
  • All log levels (DEBUG, INFO, WARNING, ERROR) are collected into an
    in-memory ring buffer. A level threshold (set via Settings → Log Level)
    controls which entries are rendered in the UI.
  • Search highlights matches in-place (VS Code style), with up/down
    navigation and a match counter — non-matching lines remain visible.
=======
>>>>>>> origin/main
"""

from __future__ import annotations

import logging
import os
<<<<<<< HEAD
from collections import deque
=======
>>>>>>> origin/main
from datetime import datetime
from typing import IO, Optional

from PySide6.QtCore import QObject, Signal
<<<<<<< HEAD
from PySide6.QtGui import QColor, QTextCursor
=======
>>>>>>> origin/main
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
<<<<<<< HEAD
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QTextEdit,
=======
    QPlainTextEdit,
    QPushButton,
>>>>>>> origin/main
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger("StrategyTester.gui.log_console")

<<<<<<< HEAD
# Numeric ordering — higher = more severe.
_LOG_ORDER = {"DEBUG": 0, "INFO": 1, "WARNING": 2, "ERROR": 3}

=======
>>>>>>> origin/main

class _LogBridge(QObject):
    """Bridge object that emits a Qt signal from any thread — safe for cross-thread logging."""

<<<<<<< HEAD
    log_message = Signal(str, str)  # level_name, formatted_message


class GUILogHandler(logging.Handler):
    """Routes log messages to a GUI callback via a Qt signal (thread-safe).

    Collects ALL levels — the UI decides what to display.
    """
=======
    log_message = Signal(str)


class GUILogHandler(logging.Handler):
    """Routes log messages to a GUI callback via a Qt signal (thread-safe)."""
>>>>>>> origin/main

    def __init__(self, bridge: _LogBridge):
        super().__init__()
        self.bridge = bridge
        self.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%H:%M:%S"))

    def emit(self, record):
        msg = self.format(record)
<<<<<<< HEAD
        self.bridge.log_message.emit(record.levelname, msg)


_SEARCH_HIGHLIGHT_COLOR = "#448AFFE0"  # vibrant blue (tone: 400, alpha: 88%) — stands out sharply
_SEARCH_CURRENT_COLOR = "#FF6D00EE"    # deep orange (tone: 900, alpha: 93%) — unmistakable current match
=======
        self.bridge.log_message.emit(msg)
>>>>>>> origin/main


class LogConsole(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)

<<<<<<< HEAD
        # ── In-memory ring buffer (all entries, never filtered) ──
        self._max_entries = 5000
        self._entries: deque[tuple[str, str]] = deque(maxlen=self._max_entries)  # (level, message)

        # ── Level threshold: only entries at or above this numeric level are shown ──
        self._level_threshold: int = _LOG_ORDER.get("INFO", 1)

        # ── Search state ──
        self._current_search_idx: int = -1
        self._search_match_count: int = 0
        self._search_matches: list[QTextCursor] = []  # list of cursor positions per match
        self._all_highlight_selections: list[QTextEdit.ExtraSelection] = []

        # ── Header with controls ──
=======
        # Header with controls
>>>>>>> origin/main
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 4)

        header.addWidget(QLabel("📋 Logs"))

        self.auto_save_cb = QCheckBox("Auto-save")
        self.auto_save_cb.setChecked(True)
        self.auto_save_cb.setToolTip("Automatically save logs to logs/ folder")
        header.addWidget(self.auto_save_cb)

        btn_clear = QPushButton("Clear")
<<<<<<< HEAD
        btn_clear.setFixedWidth(60)
=======
        btn_clear.setFixedWidth(70)
>>>>>>> origin/main
        btn_clear.clicked.connect(self.clear_log)
        header.addWidget(btn_clear)

        btn_export = QPushButton("Export...")
<<<<<<< HEAD
        btn_export.setFixedWidth(70)
=======
        btn_export.setFixedWidth(80)
>>>>>>> origin/main
        btn_export.clicked.connect(self.export_log)
        header.addWidget(btn_export)

        header.addStretch()
<<<<<<< HEAD

        # ── Search area (right side) — entry + ▲/▼ buttons ──
        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("Search…")
        self._search_input.setClearButtonEnabled(True)
        self._search_input.setFixedWidth(150)
        self._search_input.textChanged.connect(self._on_search_text_changed)
        self._search_input.setStyleSheet(
            "QLineEdit { padding: 2px 4px; border: 1px solid #404050; border-radius: 3px; "
            "background: #1A1A22; color: #E0E0E8; font-size: 12px; }"
        )
        header.addWidget(self._search_input)

        self._search_prev_btn = QPushButton("▲")
        self._search_prev_btn.setFixedHeight(30)
        self._search_prev_btn.setFixedWidth(20)
        self._search_prev_btn.setToolTip("Previous match")
        self._search_prev_btn.clicked.connect(self._search_prev)
        self._search_prev_btn.setEnabled(False)
        self._search_prev_btn.setStyleSheet(
            "QPushButton { border: 1px solid #404050; border-radius: 3px; "
            "background: #1E1E28; color: #C0C0D0; font-size: 10px; padding: 0; }"
            "QPushButton:hover { background: #30304A; }"
            "QPushButton:disabled { color: #505060; }"
        )
        header.addWidget(self._search_prev_btn)

        self._search_next_btn = QPushButton("▼")
        self._search_next_btn.setFixedHeight(30)
        self._search_next_btn.setFixedWidth(20)
        self._search_next_btn.setToolTip("Next match")
        self._search_next_btn.clicked.connect(self._search_next)
        self._search_next_btn.setEnabled(False)
        self._search_next_btn.setStyleSheet(
            "QPushButton { border: 1px solid #404050; border-radius: 3px; "
            "background: #1E1E28; color: #C0C0D0; font-size: 10px; padding: 0; }"
            "QPushButton:hover { background: #30304A; }"
            "QPushButton:disabled { color: #505060; }"
        )
        header.addWidget(self._search_next_btn)

        layout.addLayout(header)

        # ── Text display ──
        self.text_edit = QPlainTextEdit()
        self.text_edit.setReadOnly(True)
        # We manage filtering ourselves — disable built-in max block filter
        self.text_edit.setMaximumBlockCount(0)
=======
        layout.addLayout(header)

        self.text_edit = QPlainTextEdit()
        self.text_edit.setReadOnly(True)
        self.text_edit.setMaximumBlockCount(5000)  # Prevent unbounded memory growth
>>>>>>> origin/main
        from gui.styling import style_helpers as sh
        from gui.styling.theme import font_mono, get_theme, register_theme_listener

        self._sh = sh
        self._get_theme = get_theme
        self._font_mono = font_mono
        self._apply_theme()
        register_theme_listener(self._apply_theme)

        layout.addWidget(self.text_edit)

<<<<<<< HEAD
        # ── Thread-safe bridge ──
        self._bridge = _LogBridge()
        self._bridge.log_message.connect(self.append_log)
        handler = GUILogHandler(self._bridge)
        handler.setLevel(logging.DEBUG)  # Collect ALL levels into the ring buffer
        # Attach to the *root* logger so ALL messages (regardless of the
        # StrategyTester logger's level) reach the ring buffer. The root
        # logger is always active — its effective level defaults to WARNING
        # but we set it to DEBUG so every record passes through.
        root_logger = logging.getLogger()
        root_logger.setLevel(logging.DEBUG)
        root_logger.handlers = [h for h in root_logger.handlers if not isinstance(h, GUILogHandler)]
        root_logger.addHandler(handler)

        # ── Auto-save setup — keep file handle open for the lifetime of the console ──
=======
        # Thread-safe bridge
        self._bridge = _LogBridge()
        self._bridge.log_message.connect(self.append_log)
        handler = GUILogHandler(self._bridge)
        handler.setLevel(logging.DEBUG)
        # Attach to the *root* StrategyTester logger so all child loggers
        # (engine.*, gui.*, strategies.*) propagate up and surface in the console.
        root_logger = logging.getLogger("StrategyTester")
        root_logger.handlers = [h for h in root_logger.handlers if not isinstance(h, GUILogHandler)]
        root_logger.addHandler(handler)

        # Auto-save setup — keep file handle open for the lifetime of the console
>>>>>>> origin/main
        self.log_dir = "logs"
        os.makedirs(self.log_dir, exist_ok=True)
        self.current_log_file = os.path.join(
            self.log_dir,
            f"agregat_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log",
        )
        self._log_fh: Optional[IO[str]] = None
        self._open_log_file()

        logger.info(f"📝 Auto-save enabled: {self.current_log_file}")
<<<<<<< HEAD
        logger.info(f"📝 Log level threshold: INFO (1) — set via Settings → Log Level")

    # ------------------------------------------------------------------
    # Level threshold (called from Settings → Log Level)
    # ------------------------------------------------------------------
    def set_level_threshold(self, level_name: str) -> None:
        """Set the UI display threshold from a level name.

        Only entries at or above this numeric level are rendered.
        The underlying ring buffer always collects everything.
        """
        old = self._level_threshold
        self._level_threshold = _LOG_ORDER.get(level_name, 1)
        if self._level_threshold != old:
            self._reapply_filters()
            self._clear_search_state()
=======
>>>>>>> origin/main

    # ------------------------------------------------------------------
    # File handle management
    # ------------------------------------------------------------------
    def _open_log_file(self) -> None:
        """Open the log file for buffered append. Idempotent."""
        if self._log_fh is not None:
            return
        try:
            self._log_fh = open(self.current_log_file, "a", encoding="utf-8")
            self._log_fh.write(
                f"=== AGREGAT log session at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===\n\n"
            )
            self._log_fh.flush()
        except OSError as e:
            self._log_fh = None
            print(f"[LogConsole] Cannot open log file {self.current_log_file}: {e}")

    def set_max_lines(self, n: int) -> None:
<<<<<<< HEAD
        """Update the max line limit on the ring buffer."""
        self._max_entries = max(100, int(n))
        # Resize the deque
        old = self._entries
        self._entries = deque(maxlen=self._max_entries)
        self._entries.extend(old)
        self._reapply_filters()
        self._clear_search_state()
=======
        """Update the max line limit on the log display."""
        self.text_edit.setMaximumBlockCount(max(100, int(n)))
>>>>>>> origin/main

    def closeEvent(self, e):  # noqa: D401 — Qt naming
        if self._log_fh is not None:
            try:
                self._log_fh.flush()
                self._log_fh.close()
            except Exception:
                pass
            self._log_fh = None
        super().closeEvent(e)

    # ------------------------------------------------------------------
    # Log handling
    # ------------------------------------------------------------------
    def _apply_theme(self) -> None:
        sh = self._sh
        t = self._get_theme()
        self.text_edit.setStyleSheet(
            "QPlainTextEdit {"
            f"  background: {sh.c(t, 'chart_bg')};"
            f"  color: {sh.c(t, 'text')};"
            f"  font-family: {self._font_mono()};"
            "  font-size: 11px;"
            f"  border: 1px solid {sh.c(t, 'border')};"
            "  border-radius: 4px;}"
        )

<<<<<<< HEAD
    def append_log(self, level: str, msg: str) -> None:
        """Append log message to ring buffer and optionally to the persistent file."""
        self._entries.append((level, msg))

        # Persist unconditionally (all levels go to file)
        if self.auto_save_cb.isChecked() and self._log_fh is not None:
            try:
                self._log_fh.write(msg + "\n")
            except (OSError, ValueError):
                self._log_fh = None
                self._open_log_file()

        # Only render if above threshold
        level_val = _LOG_ORDER.get(level, 0)
        if level_val >= self._level_threshold:
            self._render_entry(msg)

    def _render_entry(self, msg: str) -> None:
        """Append a single entry to the text edit (no filtering by search)."""
=======
    def append_log(self, msg: str) -> None:
        """Append log message to display and optionally to the persistent file."""
>>>>>>> origin/main
        self.text_edit.appendPlainText(msg)
        scrollbar = self.text_edit.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

<<<<<<< HEAD
    # ------------------------------------------------------------------
    # Search — highlight + navigation (VS Code style)
    # ------------------------------------------------------------------
    def _clear_search_state(self) -> None:
        self._current_search_idx = -1
        self._search_match_count = 0
        self._search_matches.clear()
        self._search_prev_btn.setEnabled(False)
        self._search_next_btn.setEnabled(False)

    def _on_search_text_changed(self, text: str) -> None:
        """User changed search text — re-highlight and re-scan matches."""
        self._clear_search_state()
        query = text.strip()

        if not query:
            self._clear_highlights()
            return

        # Find every match occurrence (not per-block — per-cursor)
        doc = self.text_edit.document()
        cursor = QTextCursor(doc)
        self._search_matches.clear()

        while True:
            c = doc.find(query, cursor)
            if c.isNull():
                break
            # Store a copy of the cursor at this match
            match_cursor = QTextCursor(c)
            self._search_matches.append(match_cursor)
            cursor.setPosition(c.selectionEnd())

        self._search_match_count = len(self._search_matches)
        self._apply_highlights(query)

        if self._search_match_count > 0:
            self._current_search_idx = 0
            self._update_search_ui()
            self._jump_to_match(0)
        else:
            self._search_prev_btn.setEnabled(False)
            self._search_next_btn.setEnabled(False)

    def _clear_highlights(self) -> None:
        """Remove all search highlight formatting."""
        doc = self.text_edit.document()
        cursor = QTextCursor(doc)
        cursor.select(QTextCursor.SelectionType.Document)
        extra_selections = []
        self.text_edit.setExtraSelections(extra_selections)

    def _apply_highlights(self, query: str) -> None:
        """Highlight all occurrences of `query` in the document."""
        if not query:
            return

        doc = self.text_edit.document()
        cursor = QTextCursor(doc)
        selections = []

        while True:
            c = doc.find(query, cursor)
            if c.isNull():
                break
            sel = QTextEdit.ExtraSelection()
            sel.format.setBackground(QColor(_SEARCH_HIGHLIGHT_COLOR))
            sel.cursor = c
            selections.append(sel)
            cursor.setPosition(c.selectionEnd())

        self._all_highlight_selections = selections
        self.text_edit.setExtraSelections(selections)

    def _update_search_ui(self) -> None:
        """Update the button enabled state based on match count."""
        total = self._search_match_count
        self._search_prev_btn.setEnabled(total > 1)
        self._search_next_btn.setEnabled(total > 1)

    def _jump_to_match(self, idx: int) -> None:
        """Scroll to and select the idx-th match (per-cursor, not per-block)."""
        if idx < 0 or idx >= self._search_match_count:
            return

        match_cursor = self._search_matches[idx]
        self.text_edit.setTextCursor(match_cursor)
        self.text_edit.centerCursor()

        # Re-apply all highlights, then overlay the current match in orange
        self._apply_highlights(self._search_input.text().strip())
        all_selections = list(getattr(self, '_all_highlight_selections', []))
        extra = QTextEdit.ExtraSelection()
        extra.format.setBackground(QColor(_SEARCH_CURRENT_COLOR))
        extra.cursor = QTextCursor(match_cursor)
        all_selections.append(extra)
        self.text_edit.setExtraSelections(all_selections)

    def _search_prev(self) -> None:
        if self._search_match_count == 0:
            return
        self._current_search_idx = (self._current_search_idx - 1) % self._search_match_count
        self._update_search_ui()
        self._jump_to_match(self._current_search_idx)

    def _search_next(self) -> None:
        if self._search_match_count == 0:
            return
        self._current_search_idx = (self._current_search_idx + 1) % self._search_match_count
        self._update_search_ui()
        self._jump_to_match(self._current_search_idx)

    # ------------------------------------------------------------------
    # Full re-render (on level threshold change)
    # ------------------------------------------------------------------
    def _reapply_filters(self) -> None:
        """Re-render entire display from the ring buffer based on active level threshold."""
        search_text = self._search_input.text().strip()
        self.text_edit.clear()

        for level, msg in self._entries:
            level_val = _LOG_ORDER.get(level, 0)
            if level_val < self._level_threshold:
                continue
            self.text_edit.appendPlainText(msg)

        # Re-apply search highlights after re-render
        if search_text:
            self._apply_highlights(search_text)

        # Auto-scroll to bottom after re-render
        scrollbar = self.text_edit.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def clear_log(self) -> None:
        """Clear the log display and ring buffer."""
        self._entries.clear()
        self.text_edit.clear()
        self._clear_search_state()
        self._all_highlight_selections = []
=======
        if self.auto_save_cb.isChecked() and self._log_fh is not None:
            try:
                self._log_fh.write(msg + "\n")
                # Don't flush every line — let buffered IO win.
                # The file is flushed on closeEvent and on critical errors.
            except (OSError, ValueError):
                # File closed under us; reopen on next call
                self._log_fh = None
                self._open_log_file()

    def clear_log(self) -> None:
        """Clear the log display."""
        self.text_edit.clear()
>>>>>>> origin/main
        logger.info("🗑️ Log display cleared")

    def export_log(self) -> None:
        """Export log to file."""
        from PySide6.QtWidgets import QFileDialog

        fp, _ = QFileDialog.getSaveFileName(
            self,
            "Export Logs",
            f"agregat_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt",
            "Text Files (*.txt);;All Files (*)",
        )
        if fp:
            try:
                with open(fp, "w", encoding="utf-8") as f:
                    f.write(self.text_edit.toPlainText())
                logger.info(f"✅ Log exported to: {fp}")
            except OSError as e:
<<<<<<< HEAD
                logger.error(f"❌ Failed to export log: {e!r}")
=======
                logger.error(f"❌ Failed to export log: {e!r}")
>>>>>>> origin/main
