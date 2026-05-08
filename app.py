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

    window = MainWindow()
    window.show()
    return int(app.exec())


if __name__ == "__main__":
    sys.exit(main())
