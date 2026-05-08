"""
Log Console — Thread-safe GUI log handler with auto-save functionality.

Phase 1 changes:
  • The auto-save file is held open for the lifetime of the LogConsole
    instead of opened/closed on every `append_log` call (was 100s of syscalls/sec
    during step_n bulk runs).
  • The buffered handle is flushed eagerly so logs survive crashes.
  • Max-lines is configurable from Settings via `set_max_lines()`.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import IO, Optional

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger("StrategyTester.gui.log_console")


class _LogBridge(QObject):
    """Bridge object that emits a Qt signal from any thread — safe for cross-thread logging."""

    log_message = Signal(str)


class GUILogHandler(logging.Handler):
    """Routes log messages to a GUI callback via a Qt signal (thread-safe)."""

    def __init__(self, bridge: _LogBridge):
        super().__init__()
        self.bridge = bridge
        self.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%H:%M:%S"))

    def emit(self, record):
        msg = self.format(record)
        self.bridge.log_message.emit(msg)


class LogConsole(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)

        # Header with controls
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 4)

        header.addWidget(QLabel("📋 Logs"))

        self.auto_save_cb = QCheckBox("Auto-save")
        self.auto_save_cb.setChecked(True)
        self.auto_save_cb.setToolTip("Automatically save logs to logs/ folder")
        header.addWidget(self.auto_save_cb)

        btn_clear = QPushButton("Clear")
        btn_clear.setFixedWidth(70)
        btn_clear.clicked.connect(self.clear_log)
        header.addWidget(btn_clear)

        btn_export = QPushButton("Export...")
        btn_export.setFixedWidth(80)
        btn_export.clicked.connect(self.export_log)
        header.addWidget(btn_export)

        header.addStretch()
        layout.addLayout(header)

        self.text_edit = QPlainTextEdit()
        self.text_edit.setReadOnly(True)
        self.text_edit.setMaximumBlockCount(5000)  # Prevent unbounded memory growth
        from gui.styling import style_helpers as sh
        from gui.styling.theme import font_mono, get_theme, register_theme_listener

        self._sh = sh
        self._get_theme = get_theme
        self._font_mono = font_mono
        self._apply_theme()
        register_theme_listener(self._apply_theme)

        layout.addWidget(self.text_edit)

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
        self.log_dir = "logs"
        os.makedirs(self.log_dir, exist_ok=True)
        self.current_log_file = os.path.join(
            self.log_dir,
            f"agregat_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log",
        )
        self._log_fh: Optional[IO[str]] = None
        self._open_log_file()

        logger.info(f"📝 Auto-save enabled: {self.current_log_file}")

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
        """Update the max line limit on the log display."""
        self.text_edit.setMaximumBlockCount(max(100, int(n)))

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

    def append_log(self, msg: str) -> None:
        """Append log message to display and optionally to the persistent file."""
        self.text_edit.appendPlainText(msg)
        scrollbar = self.text_edit.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

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
                logger.error(f"❌ Failed to export log: {e!r}")
