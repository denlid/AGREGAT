"""
AGREGAT v7.78 — Algorithmic Graphical Reviewer Engine Genius Analytics Tester
Entry Point
"""

import logging
import os
import sys

# Setup root logger FIRST
logger = logging.getLogger("StrategyTester.app")
logger.setLevel(logging.DEBUG)

# CRITICAL: Import pandas BEFORE PySide6 to avoid shiboken/dateutil/six conflict
import numpy  # noqa: F401
import pandas  # noqa: F401
from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication

# Backward compatibility for strategies that do `from app import Strategy, Context`
from gui.main_window import MainWindow
from gui.styling.theme import apply_theme


def main() -> int:
    """Console-script entry point.

    Wrapped as a function so ``pyproject.toml`` can expose
    ``agregat-gui = "app:main"`` while preserving direct
    ``python app.py`` execution.
    """
    # Ensure CWD is the app directory (for relative paths)
    os.chdir(os.path.dirname(os.path.abspath(__file__)))

    # High-DPI scaling — must be set before QApplication is constructed.
    # Qt6 auto-enables device-pixel-ratio handling; we just request rounded scaling
    # to avoid blurry text on fractional-DPI monitors.
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    app = QApplication(sys.argv)

    # --- License Verification ---
    from engine.security.license_manager import verify_license, get_hwid, save_license_key
    from PySide6.QtWidgets import (
        QMessageBox, QDialog, QVBoxLayout, QHBoxLayout,
        QLabel, QLineEdit, QPushButton, QTextEdit
    )
    from PySide6.QtGui import QFont as _QFont

    def _show_activation_dialog(hwid: str) -> bool:
        """Показує красиве вікно активації з полем для вводу ключа."""
        dlg = QDialog()
        dlg.setWindowTitle("AGREGAT — Активація")
        dlg.setFixedSize(520, 380)
        dlg.setStyleSheet("""
            QDialog { background: #0d1117; }
            QLabel { color: #c9d1d9; }
            QLineEdit, QTextEdit { 
                background: #161b22; color: #e6edf3; border: 1px solid #30363d;
                border-radius: 6px; padding: 8px; font-family: 'Consolas'; font-size: 13px;
            }
            QPushButton {
                background: #238636; color: white; border: none; border-radius: 6px;
                padding: 10px 24px; font-weight: bold; font-size: 14px;
            }
            QPushButton:hover { background: #2ea043; }
            QPushButton#copyBtn {
                background: #30363d; padding: 6px 16px; font-size: 12px;
            }
            QPushButton#copyBtn:hover { background: #484f58; }
        """)

        layout = QVBoxLayout(dlg)
        layout.setSpacing(12)

        title = QLabel("AGREGAT — Private Beta")
        title.setFont(_QFont("Segoe UI", 18, _QFont.Weight.Bold))
        title.setStyleSheet("color: #58a6ff;")
        layout.addWidget(title)

        layout.addWidget(QLabel("Ваш Hardware ID (надішліть розробнику):"))

        hwid_row = QHBoxLayout()
        hwid_field = QLineEdit(hwid)
        hwid_field.setReadOnly(True)
        hwid_row.addWidget(hwid_field)

        copy_btn = QPushButton("Копіювати")
        copy_btn.setObjectName("copyBtn")
        copy_btn.clicked.connect(lambda: (app.clipboard().setText(hwid), copy_btn.setText("✓ Скопійовано!")))
        hwid_row.addWidget(copy_btn)
        layout.addLayout(hwid_row)

        layout.addWidget(QLabel("Вставте ліцензійний ключ від розробника:"))

        key_input = QTextEdit()
        key_input.setPlaceholderText("Вставте ключ сюди...")
        key_input.setMaximumHeight(80)
        layout.addWidget(key_input)

        status_label = QLabel("")
        status_label.setStyleSheet("color: #f85149; font-weight: bold;")
        layout.addWidget(status_label)

        activate_btn = QPushButton("Активувати")

        def on_activate():
            raw_key = key_input.toPlainText().strip()
            if not raw_key:
                status_label.setText("Введіть ключ!")
                return
            if save_license_key(raw_key):
                ok, msg = verify_license()
                if ok:
                    dlg.accept()
                else:
                    if msg.startswith("NO_LICENSE"):
                        status_label.setText("Невірний ключ.")
                    else:
                        status_label.setText(msg)
            else:
                status_label.setText("Помилка збереження ключа.")

        activate_btn.clicked.connect(on_activate)
        layout.addWidget(activate_btn)

        return dlg.exec() == QDialog.DialogCode.Accepted

    is_valid, msg = verify_license()
    if not is_valid:
        if msg.startswith("NO_LICENSE"):
            hwid = msg.split("|")[1]
            if not _show_activation_dialog(hwid):
                sys.exit(0)
        else:
            QMessageBox.critical(None, "AGREGAT", msg)
            sys.exit(0)
    # ----------------------------

    # Theme resolution order:
    #   1. Persisted user preference from settings.json (if "auto", "dark", "light").
    #   2. System ColorScheme (Qt 6.5+); fall back to dark.
    theme_choice = "dark"
    try:
        from gui.persistence.settings_persist import load as _load_settings

        s = _load_settings() or {}
        theme_choice = str(s.get("theme", "dark"))
    except Exception:
        pass
    if theme_choice == "auto":
        try:
            from PySide6.QtGui import QStyleHints  # noqa: F401

            scheme = app.styleHints().colorScheme()  # type: ignore[attr-defined]
            theme_choice = "light" if scheme == Qt.ColorScheme.Light else "dark"
        except Exception:
            theme_choice = "dark"
    if theme_choice not in ("dark", "light"):
        theme_choice = "dark"
    apply_theme(app, theme_choice)

    # --- Splash Screen ---
    from PySide6.QtWidgets import QSplashScreen
    from PySide6.QtGui import QPixmap, QPainter, QFont, QColor
    
    splash_pix = QPixmap(600, 350)
    splash_pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(splash_pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    
    bg_color = QColor("#0d1117") if theme_choice == "dark" else QColor("#ffffff")
    painter.setBrush(bg_color)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(splash_pix.rect(), 24, 24)
    
    # Draw elegant text
    font_title = QFont("Segoe UI", 52, QFont.Weight.Black)
    font_title.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 105)
    font_title.setHintingPreference(QFont.HintingPreference.PreferFullHinting)
    
    font_sub = QFont("Segoe UI", 12, QFont.Weight.Bold)
    font_sub.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 110)
    font_sub.setHintingPreference(QFont.HintingPreference.PreferFullHinting)
    
    text_color = QColor("#c9d1d9") if theme_choice == "dark" else QColor("#24292f")
    accent_color = QColor("#e34c26") # Використаємо червонуватий акцент для бети, щоб привернути увагу
    
    painter.setFont(font_title)
    painter.setPen(text_color)
    painter.drawText(splash_pix.rect(), Qt.AlignmentFlag.AlignCenter, "AGREGAT")
    
    painter.setFont(font_sub)
    painter.setPen(accent_color)
    rect_sub = splash_pix.rect()
    rect_sub.setTop(rect_sub.height() // 2 + 50)
    painter.drawText(rect_sub, Qt.AlignmentFlag.AlignCenter, "INSTITUTIONAL QUANT PLATFORM • PRIVATE BETA PROTOTYPE")
    painter.end()

    splash = QSplashScreen(splash_pix, Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.FramelessWindowHint)
    splash.setMask(splash_pix.mask())
    splash.show()
    app.processEvents()

    # Create Main Window
    window = MainWindow()
    window.show()
    splash.finish(window)
    return int(app.exec())


if __name__ == "__main__":
    sys.exit(main())
