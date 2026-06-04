"""
AGREGAT v7.78 — Algorithmic Graphical Reviewer Engine Genius Analytics Tester
Entry Point
"""

import sys

# CRITICAL: Import pandas BEFORE PySide6 to avoid shiboken/dateutil/six conflict
import numpy  # noqa: F401
import pandas  # noqa: F401
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication, QMessageBox

from engine.security.license_manager import verify_license
from gui.components.splash_screen import create_agregat_splash
from gui.dialogs.activation_dialog import show_activation_dialog
from gui.main_window import MainWindow
from gui.persistence.settings_persist import load as load_settings
from gui.styling.theme import apply_theme


def _resolve_theme(app: QApplication) -> str:
    """Resolve theme choice: persisted → system auto → dark."""
    theme_choice = "dark"
    try:
        s = load_settings() or {}
        theme_choice = str(s.get("theme", "dark"))
    except Exception:
        pass
    if theme_choice == "auto":
        try:
            scheme = app.styleHints().colorScheme()
            theme_choice = "light" if scheme == Qt.ColorScheme.Light else "dark"
        except Exception:
            theme_choice = "dark"
    if theme_choice not in ("dark", "light"):
        theme_choice = "dark"
    return theme_choice


def _enable_gpu_if_available() -> None:
    """Activate OpenGL chart backend (safe fallback on failure)."""
    try:
        from gui.chart_tab.gpu_config import configure_gpu_acceleration
        configure_gpu_acceleration()
    except Exception:
        pass  # Gracefully fall back to software rendering.


def _bg_license_check() -> None:
    """Periodic license re-validation (runs every 5 min)."""
    # Deferred import to avoid circular dependency with engine internals.
    from engine.security.license_manager import verify_license  # noqa: F811

    ok, _ = verify_license()
    if not ok:
        sys.exit(1)


def main() -> int:
    """Console-script entry point.

    Wrapped as a function so ``pyproject.toml`` can expose
    ``agregat-gui = "app:main"`` while preserving direct
    ``python app.py`` execution.
    """
    # Ensure CWD is the app directory (for relative paths)
    import os

    os.chdir(os.path.dirname(os.path.abspath(__file__)))

    # High-DPI scaling — must be set before QApplication is constructed.
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    app = QApplication(sys.argv)

    # --- Chart GPU acceleration ---
    _enable_gpu_if_available()

    # --- License Verification ---
    is_valid, msg = verify_license()
    if not is_valid:
        if msg.startswith("NO_LICENSE"):
            hwid = msg.split("|")[1]
            if not show_activation_dialog(hwid, app=app):
                sys.exit(0)
        else:
            QMessageBox.critical(None, "AGREGAT", msg)
            sys.exit(0)

    # --- Theme ---
    theme_choice = _resolve_theme(app)
    apply_theme(app, theme_choice)

    # --- Splash Screen ---
    splash = create_agregat_splash(theme_choice)
    splash.show()
    app.processEvents()

    # Create Main Window
    window = MainWindow()

    # --- Background License Validation ---
    _timer = QTimer(window)
    _timer.timeout.connect(_bg_license_check)
    _timer.start(300000)  # Every 5 minutes

    window.show()
    splash.finish(window)
    return int(app.exec())


if __name__ == "__main__":
    sys.exit(main())