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

# C-02: Legacy offline license_manager removed from production imports.
# Only server-authoritative online licensing is used in production.
# Offline fallback (XOR/HMAC) was a security bypass — removed per audit.
from engine.security.license_client import verify_license_online, get_license_client
from engine.security.anti_debug import quick_security_check
from gui.components.splash_screen import create_agregat_splash
from gui.dialogs.activation_dialog import show_activation_dialog_online_only
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
    """Periodic license + security re-validation (runs every 5 min).
    
    SECURITY (C-02): Server-authoritative only. No offline fallback.
    
    Checks:
    1. Anti-debug / anti-tamper
    2. Online license (server) — cached signed receipt if server unreachable
    """
    # Security check first
    sec_ok, sec_reason = quick_security_check()
    if not sec_ok:
        sys.exit(1)

    # C-02: Online verification only (signed receipt cache handles brief outages)
    ok, msg = verify_license_online()
    if not ok:
        # License invalid or expired — exit immediately
        # No offline fallback: the signed receipt cache provides
        # a 24-hour grace period via load_license_cache()
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

    # --- Anti-Debug / Security Check (runs before anything else) ---
    sec_ok, sec_reason = quick_security_check()
    if not sec_ok:
        # Silent exit — don't give attackers info
        sys.exit(1)

    # --- Chart GPU acceleration ---
    _enable_gpu_if_available()

    # --- License Verification (C-02: server-authoritative only) ---
    # No offline fallback. Online verification uses signed receipt cache
    # for brief server outages (24-hour grace period).
    is_valid, msg = verify_license_online()
    
    if not is_valid:
        # Show online-only activation dialog
        from engine.security.license_client import generate_device_fingerprint
        hwid = generate_device_fingerprint()[:16].upper()
        
        if not show_activation_dialog_online_only(hwid, app=app):
            sys.exit(0)
        
        # Re-verify after activation
        is_valid, msg = verify_license_online()
        if not is_valid:
            sys.exit(0)
    
    # Start heartbeat for active sessions
    client = get_license_client()
    if client.is_active:
        client.start_heartbeat()

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