"""
Theme — Premium dark/light themes.

Phase 3 additions
-----------------
* :data:`THEMES` keeps the canonical palette; widgets MUST read colours via
  :func:`get_theme` rather than hard-coding hex literals so the Light /
  Dark switch is artefact-free.
* :data:`theme_changed` is a global Signal emitter. Any widget that paints
  itself with a custom stylesheet calls :func:`register_theme_listener`
  with a callback (typically ``self._apply_theme``) so the widget refreshes
  whenever the user flips the theme.
* :func:`apply_drop_shadow` is a small helper for the "premium flat with
  subtle shadow" look on cards / panels.
* :func:`apply_theme` now also enables ``pg.setConfigOptions(antialias=True)``
  and configures the modern font stack (Inter for prose, Roboto Mono for
  numbers / tables, with system fallbacks).
"""

from __future__ import annotations

import logging
from typing import Callable, List

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette
from PySide6.QtWidgets import QApplication, QGraphicsDropShadowEffect, QWidget

logger = logging.getLogger("StrategyTester.gui.theme")

# ==========================================
# Chart Color Schemes
# ==========================================
CHART_SCHEMES = {
    "Classic": {"bull": "#26A69A", "bear": "#EF5350", "line": "#2196F3"},
    "Green-Red": {"bull": "#00E676", "bear": "#FF1744", "line": "#3399FF"},
    "Yellow-Blue": {"bull": "#FFD600", "bear": "#2979FF", "line": "#FFD600"},
    "Black-White": {"bull": "#FFFFFF", "bear": "#666666", "line": "#FFFFFF"},
    "Monochrome": {"bull": "#B0BEC5", "bear": "#546E7A", "line": "#90A4AE"},
}

# ==========================================
# Theme Definitions
# ==========================================
THEMES = {
    "dark": {
        # Premium deep dark — layered greys instead of pure #000
        "bg": "#0E1114",
        "surface": "#161A1E",
        "elevated": "#1E2328",
        "elevated2": "#262C33",
        "hover": "#2E353D",
        "accent": "#2962FF",
        "accent_light": "#5C8AFF",
        "accent_dim": "#1A3A8F",
        "text": "#E8EAED",
        "text2": "#9AA0A6",
        "text3": "#606873",
        "text_dis": "#3D424A",
        "border": "#2D343B",
        "border2": "#22272E",
        # Premium P&L colours — muted, eye-friendly
        "green": "#26A69A",
        "green_bright": "#00C853",
        "red": "#E04B4B",
        "red_bright": "#FF5252",
        "amber": "#F5A623",
        "cyan": "#00BCD4",
        "purple": "#7C4DFF",
        "chart_bg": "#0E1114",
        "chart_grid": "#1E2328",
        "chart_axis": "#22272E",
        "chart_axis_text": "#606873",
        "chart_equity_line": "#2962FF",
        "chart_equity_fill": "#155A96",
        "chart_peak_line": "#F5A623",
        "chart_dd_line": "#E04B4B",
        "chart_dd_fill": "#4A2020",
        "chart_win_marker": "#26A69A",
        "chart_loss_marker": "#E04B4B",
    },
    "quant": {
        # Sprint 2.8 — research-desk style. Inherits dark base.
        "bg": "#06070A",
        "surface": "#0E0F14",
        "elevated": "#13141B",
        "elevated2": "#1A1B25",
        "hover": "#22232E",
        "accent": "#FF1E88",
        "accent_light": "#FF6FB1",
        "accent_dim": "#5C0D3A",
        "text": "#F5F0FF",
        "text2": "#9C97AE",
        "text3": "#5A546B",
        "text_dis": "#3A3548",
        "border": "#2A2A3C",
        "border2": "#1F1F2D",
        "green": "#1DE9B6",
        "green_bright": "#00E5A0",
        "red": "#FF3D71",
        "red_bright": "#FF5252",
        "amber": "#FFB300",
        "cyan": "#00E5FF",
        "purple": "#B388FF",
        "chart_bg": "#06070A",
        "chart_grid": "#13141B",
        "chart_axis": "#1F1F2D",
        "chart_axis_text": "#5A546B",
        "chart_equity_line": "#00E5FF",
        "chart_equity_fill": "#003D52",
        "chart_peak_line": "#FFB300",
        "chart_dd_line": "#FF3D71",
        "chart_dd_fill": "#3A0A1E",
        "chart_win_marker": "#1DE9B6",
        "chart_loss_marker": "#FF3D71",
    },
    "light": {
        "bg": "#F0F2F5",
        "surface": "#FFFFFF",
        "elevated": "#F8F9FB",
        "elevated2": "#EEF0F4",
        "hover": "#E4E6EA",
        "accent": "#1565C0",
        "accent_light": "#1976D2",
        "accent_dim": "#BBDEFB",
        "text": "#1A1A2E",
        "text2": "#5A5A6E",
        "text3": "#9A9AAE",
        "text_dis": "#C0C0CC",
        "border": "#DDE0E8",
        "border2": "#E8EAF0",
        "green": "#00897B",
        "green_bright": "#00C853",
        "red": "#C62828",
        "red_bright": "#D50000",
        "amber": "#E65100",
        "cyan": "#00838F",
        "purple": "#4527A0",
        "chart_bg": "#FFFFFF",
        "chart_grid": "#F0F2F5",
        "chart_axis": "#DDE0E8",
        "chart_axis_text": "#9A9AAE",
        "chart_equity_line": "#1976D2",
        "chart_equity_fill": "#BBDEFB",
        "chart_peak_line": "#E65100",
        "chart_dd_line": "#C62828",
        "chart_dd_fill": "#FFCDD2",
        "chart_win_marker": "#00897B",
        "chart_loss_marker": "#C62828",
    },
}

_current_theme = "dark"


def get_theme():
    return THEMES[_current_theme]


def get_theme_name():
    return _current_theme


# ==========================================
# Theme-change broadcast
# ==========================================
class _ThemeBroadcaster(QObject):
    """Singleton-ish QObject that emits whenever the active theme changes.

    Widgets register a re-style callback via :func:`register_theme_listener`;
    :func:`apply_theme` emits the signal so every listener repaints itself
    against the new palette without leaving stale hex artefacts.
    """

    changed = Signal(str)  # the new theme name


_broadcaster = _ThemeBroadcaster()
theme_changed: Signal = _broadcaster.changed
_listeners: List[Callable[[], None]] = []


def register_theme_listener(callback: Callable[[], None]) -> None:
    """Register a no-arg callable that re-applies the calling widget's
    stylesheet using :func:`get_theme`. Idempotent.
    """
    if callback not in _listeners:
        _listeners.append(callback)


def unregister_theme_listener(callback: Callable[[], None]) -> None:
    """Remove a previously registered listener — typically called from the
    widget's ``closeEvent`` so deleted widgets don't keep getting pinged.
    """
    try:
        _listeners.remove(callback)
    except ValueError:
        pass


def _broadcast_theme_change(name: str) -> None:
    """Notify every listener of the new theme.

    Listener errors are logged but never propagated so a single misbehaving
    widget can't break the whole repaint pass.  Dead listeners (widgets
    already destroyed by Qt) are automatically pruned from the list.
    """
    dead: list[Callable[[], None]] = []
    for cb in list(_listeners):
        try:
            cb()
        except RuntimeError:
            # C++ object already deleted — remove silently
            dead.append(cb)
        except Exception:
            logger.exception("Theme listener %r failed", cb)
    for cb in dead:
        try:
            _listeners.remove(cb)
        except ValueError:
            pass
    theme_changed.emit(name)


# ==========================================
# Premium look helpers
# ==========================================
def apply_drop_shadow(
    widget: QWidget,
    *,
    blur: int = 18,
    y_offset: int = 4,
    alpha: int = 90,
) -> None:
    """Attach a subtle drop-shadow effect — used on cards, KPI tiles, and
    floating panels for the Phase 3 "modern flat" aesthetic.

    Theme-agnostic: the colour comes from the active theme so the same call
    looks balanced in both Light and Dark modes.
    """
    eff = QGraphicsDropShadowEffect(widget)
    eff.setBlurRadius(blur)
    eff.setOffset(0, y_offset)
    # A black shadow with a low alpha works for both light/dark themes
    # because Qt blends against the actual underlying pixels.
    shadow = QColor(0, 0, 0)
    shadow.setAlpha(alpha)
    eff.setColor(shadow)
    widget.setGraphicsEffect(eff)


def font_text() -> str:
    """Stylesheet-friendly font stack for prose / labels / buttons.
    
    Premium tier: Inter (geometric, excellent legibility), fallback to system.
    """
    return "'Inter', 'Segoe UI Variable', 'Segoe UI', 'Helvetica Neue', Arial, sans-serif"


def font_mono() -> str:
    """Stylesheet-friendly font stack for tables / numbers / log output.
    
    Premium tier: JetBrains Mono (coding ligatures, crisp), then Roboto Mono.
    """
    return "'JetBrains Mono', 'Roboto Mono', 'Cascadia Code', 'Consolas', 'Courier New', monospace"


def apply_theme(app: QApplication, name: str = "dark"):
    global _current_theme
    _current_theme = name
    t = THEMES[name]

    app.setStyle("Fusion")

    # Use Inter when available, else fall back to Segoe UI (Windows) / system.
    available = set(QFontDatabase.families())
    base_family = "Inter" if "Inter" in available else "Segoe UI"
    font = QFont(base_family, 10)
    app.setFont(font)

    # Antialias for everything pyqtgraph paints (charts, axes, drawings).
    # Phase 2.3 — ``AGREGAT_GL=1`` opts the user into pyqtgraph's OpenGL
    # backend for the candlestick / line painter. The flag is off by
    # default because the GL path is known to mis-render axis labels
    # and antialias on a non-trivial fraction of GPU drivers; users
    # who *do* see a render slowdown on very large datasets can flip
    # the env var to trade some compatibility for hardware acceleration.
    try:
        import os as _os

        import pyqtgraph as pg

        use_gl = _os.environ.get("AGREGAT_GL", "0") == "1"
        pg.setConfigOptions(antialias=True, useOpenGL=use_gl)
        if use_gl:
            logger.info("Phase 2.3: pyqtgraph OpenGL backend enabled (AGREGAT_GL=1)")
    except Exception:
        # pyqtgraph is a hard dep of the app, but in test environments it
        # might be importable but minimal — never break theme apply on it.
        logger.debug("pg.setConfigOptions skipped — pyqtgraph not ready")

    p = QPalette()
    p.setColor(QPalette.ColorRole.Window, QColor(t["bg"]))
    p.setColor(QPalette.ColorRole.WindowText, QColor(t["text"]))
    p.setColor(QPalette.ColorRole.Base, QColor(t["surface"]))
    p.setColor(QPalette.ColorRole.AlternateBase, QColor(t["elevated"]))
    p.setColor(QPalette.ColorRole.Text, QColor(t["text"]))
    p.setColor(QPalette.ColorRole.Button, QColor(t["elevated"]))
    p.setColor(QPalette.ColorRole.ButtonText, QColor(t["text"]))
    p.setColor(QPalette.ColorRole.Highlight, QColor(t["accent"]))
    p.setColor(QPalette.ColorRole.HighlightedText, QColor("#FFFFFF"))
    p.setColor(QPalette.ColorRole.ToolTipBase, QColor(t["elevated2"]))
    p.setColor(QPalette.ColorRole.ToolTipText, QColor(t["text"]))
    p.setColor(QPalette.ColorRole.Link, QColor(t["accent_light"]))
    p.setColor(QPalette.ColorRole.PlaceholderText, QColor(t["text3"]))
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor(t["text_dis"]))
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor(t["text_dis"]))
    app.setPalette(p)

    app.setStyleSheet(f"""
        /* ── Base ── */
        QWidget {{
            background-color: {t["bg"]};
            color: {t["text"]};
            font-family: {font_text()};
        }}

        /* ── Buttons ── */
        QPushButton {{
            background: {t["elevated"]};
            border: 1px solid {t["border"]};
            border-radius: 5px;
            padding: 4px 12px;
            min-height: 22px;
            font-size: 11px;
            font-weight: 500;
            color: {t["text"]};
        }}
        QPushButton:hover {{
            border-color: {t["accent_light"]};
            background: {t["hover"]};
            color: {t["text"]};
        }}
        QPushButton:pressed {{
            background: {t["accent"]};
            border-color: {t["accent"]};
            color: #FFFFFF;
        }}
        QPushButton:checked {{
            background: {t["accent"]};
            border-color: {t["accent"]};
            color: #FFFFFF;
        }}
        QPushButton:disabled {{
            color: {t["text_dis"]};
            background: {t["surface"]};
            border-color: {t["border2"]};
        }}
        QPushButton#btnRun {{
            background: {t["accent_light"]};
            color: #FFFFFF;
            border-color: {t["accent"]};
            font-weight: bold;
        }}
        QPushButton#btnRun:hover {{
            background: {t["accent"]};
        }}

        /* ── Tool Buttons ── */
        QToolButton {{
            background: {t["elevated"]};
            border: 1px solid {t["border"]};
            border-radius: 5px;
            padding: 3px 6px;
            font-size: 12px;
            color: {t["text"]};
        }}
        QToolButton:hover {{
            border-color: {t["accent_light"]};
            background: {t["hover"]};
        }}
        QToolButton:checked, QToolButton:pressed {{
            background: {t["accent"]};
            border-color: {t["accent"]};
            color: #FFFFFF;
        }}
        QToolButton::menu-indicator {{ image: none; }}

        /* ── ComboBox ── */
        QComboBox {{
            background: {t["elevated"]};
            border: 1px solid {t["border"]};
            border-radius: 6px;
            padding: 4px 8px;
            min-height: 24px;
            font-size: 12px;
            color: {t["text"]};
        }}
        QComboBox:hover {{ border-color: {t["accent_light"]}; }}
        QComboBox:focus {{ border-color: {t["accent"]}; }}
        QComboBox::drop-down {{
            border: none;
            width: 20px;
        }}
        QComboBox::down-arrow {{
            width: 10px;
            height: 10px;
        }}
        QComboBox QAbstractItemView {{
            background: {t["elevated2"]};
            border: 1px solid {t["border"]};
            border-radius: 6px;
            selection-background-color: {t["accent"]};
            selection-color: #FFFFFF;
            padding: 4px;
            outline: none;
        }}
        QComboBox QAbstractItemView::item {{
            padding: 6px 10px;
            border-radius: 4px;
            min-height: 24px;
        }}
        QComboBox QAbstractItemView::item:hover {{
            background: {t["hover"]};
        }}

        /* ── SpinBox / DateEdit ── */
        QSpinBox, QDoubleSpinBox, QDateEdit {{
            background: {t["elevated"]};
            border: 1px solid {t["border"]};
            border-radius: 6px;
            padding: 4px 6px;
            min-height: 24px;
            font-size: 12px;
            color: {t["text"]};
        }}
        QSpinBox:hover, QDoubleSpinBox:hover, QDateEdit:hover {{
            border-color: {t["accent_light"]};
        }}
        QSpinBox:focus, QDoubleSpinBox:focus, QDateEdit:focus {{
            border-color: {t["accent"]};
        }}
        QSpinBox::up-button, QSpinBox::down-button,
        QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
            background: {t["hover"]};
            border: none;
            border-radius: 3px;
            width: 16px;
        }}

        /* ── LineEdit ── */
        QLineEdit {{
            background: {t["elevated"]};
            border: 1px solid {t["border"]};
            border-radius: 6px;
            padding: 4px 8px;
            min-height: 24px;
            font-size: 12px;
            color: {t["text"]};
        }}
        QLineEdit:hover {{ border-color: {t["accent_light"]}; }}
        QLineEdit:focus {{ border-color: {t["accent"]}; }}

        /* ── CheckBox ── */
        QCheckBox {{
            spacing: 6px;
            font-size: 12px;
            color: {t["text2"]};
        }}
        QCheckBox:hover {{ color: {t["text"]}; }}
        QCheckBox::indicator {{
            width: 16px;
            height: 16px;
            border: 1px solid {t["border"]};
            border-radius: 4px;
            background: {t["elevated"]};
        }}
        QCheckBox::indicator:hover {{ border-color: {t["accent_light"]}; }}
        QCheckBox::indicator:checked {{
            background: {t["accent"]};
            border-color: {t["accent"]};
        }}

        /* ── Tabs ── */
        QTabWidget::pane {{
            border: 1px solid {t["border"]};
            background: {t["surface"]};
            border-radius: 0px;
        }}
        QTabBar {{
            background: transparent;
        }}
        QTabBar::tab {{
            background: transparent;
            border: none;
            padding: 4px 10px;
            color: {t["text2"]};
            font-size: 10px;
            font-weight: 500;
            min-width: 0px;
            text-transform: uppercase;
            letter-spacing: 0.7px;
        }}
        QTabBar::tab:hover {{
            color: {t["text"]};
            background: {t["hover"]};
            border-radius: 2px 2px 0 0;
        }}
        QTabBar::tab:selected {{
            color: {t["accent_light"]};
            border-bottom: 2px solid {t["accent"]};
            background: transparent;
            font-weight: 600;
        }}
        /* West-positioned tabs (left-rail) — narrow vertical strip */
        QTabBar::tab:left {{
            padding: 6px 2px;
            margin: 0;
            min-height: 28px;
            min-width: 44px;
            border-right: 2px solid transparent;
            border-bottom: none;
            border-radius: 0;
            font-size: 10px;
            font-weight: 600;
            letter-spacing: 0.6px;
            text-transform: uppercase;
        }}
        QTabBar::tab:left:hover {{
            background: {t["hover"]};
            color: {t["text"]};
            border-radius: 0;
        }}
        QTabBar::tab:left:selected {{
            border-right: 2px solid {t["accent"]};
            border-bottom: none;
            color: {t["accent_light"]};
            background: {t["elevated"]};
        }}

        /* ── Tables ── */
        QTableWidget {{
            background: {t["surface"]};
            border: 1px solid {t["border"]};
            border-radius: 6px;
            gridline-color: {t["border2"]};
            alternate-background-color: {t["elevated"]};
            selection-background-color: {t["accent_dim"]};
            selection-color: {t["text"]};
            outline: none;
        }}
        QTableWidget::item {{
            padding: 5px 8px;
            border: none;
        }}
        QTableWidget::item:selected {{
            background: {t["accent_dim"]};
            color: {t["text"]};
        }}
        QHeaderView::section {{
            background: {t["elevated2"]};
            border: none;
            border-right: 1px solid {t["border"]};
            border-bottom: 1px solid {t["border"]};
            padding: 6px 8px;
            color: {t["text2"]};
            font-size: 11px;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }}
        QHeaderView::section:first {{ border-radius: 6px 0 0 0; }}
        QHeaderView::section:last  {{ border-right: none; border-radius: 0 6px 0 0; }}

        /* ── ScrollBars ── */
        QScrollBar:vertical {{
            background: transparent;
            width: 6px;
            margin: 0;
        }}
        QScrollBar::handle:vertical {{
            background: {t["hover"]};
            border-radius: 3px;
            min-height: 30px;
        }}
        QScrollBar::handle:vertical:hover {{ background: {t["accent"]}; }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        QScrollBar:horizontal {{
            background: transparent;
            height: 6px;
            margin: 0;
        }}
        QScrollBar::handle:horizontal {{
            background: {t["hover"]};
            border-radius: 3px;
            min-width: 30px;
        }}
        QScrollBar::handle:horizontal:hover {{ background: {t["accent"]}; }}
        QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}

        /* ── Text Edits ── */
        QPlainTextEdit, QTextEdit {{
            background: {t["surface"]};
            border: 1px solid {t["border"]};
            border-radius: 6px;
            color: {t["text"]};
            selection-background-color: {t["accent_dim"]};
        }}

        /* ── Splitter ── */
        QSplitter::handle {{
            background: {t["border"]};
        }}
        QSplitter::handle:horizontal {{ width: 1px; }}
        QSplitter::handle:vertical   {{ height: 1px; }}
        QSplitter::handle:hover {{ background: {t["accent"]}; }}

        /* ── Labels ── */
        QLabel {{ background: transparent; }}

        /* ── Tooltips ── */
        QToolTip {{
            background: {t["elevated2"]};
            border: 1px solid {t["border"]};
            border-radius: 6px;
            padding: 6px 10px;
            color: {t["text"]};
            font-size: 11px;
        }}

        /* ── Status Bar ── */
        QStatusBar {{
            background: {t["elevated"]};
            border-top: 1px solid {t["border"]};
            font-size: 11px;
        }}
        QStatusBar QLabel {{
            padding: 3px 8px;
            border-right: 1px solid {t["border"]};
            color: {t["text2"]};
        }}

        /* ── Menu Bar ── */
        QMenuBar {{
            background: {t["elevated"]};
            border-bottom: 1px solid {t["border"]};
            padding: 2px 4px;
            font-size: 12px;
        }}
        QMenuBar::item {{
            background: transparent;
            padding: 5px 10px;
            border-radius: 4px;
            color: {t["text2"]};
        }}
        QMenuBar::item:selected {{
            background: {t["hover"]};
            color: {t["text"]};
        }}

        /* ── Menus ── */
        QMenu {{
            background: {t["elevated2"]};
            border: 1px solid {t["border"]};
            border-radius: 8px;
            padding: 6px;
            font-size: 12px;
        }}
        QMenu::item {{
            padding: 7px 20px 7px 12px;
            border-radius: 4px;
            color: {t["text"]};
        }}
        QMenu::item:selected {{
            background: {t["accent"]};
            color: #FFFFFF;
        }}
        QMenu::separator {{
            height: 1px;
            background: {t["border"]};
            margin: 4px 8px;
        }}

        /* ── Group Box ── */
        QGroupBox {{
            border: 1px solid {t["border"]};
            border-radius: 8px;
            margin-top: 14px;
            padding-top: 14px;
            font-size: 12px;
            font-weight: 600;
            color: {t["text2"]};
        }}
        QGroupBox::title {{
            subcontrol-origin: margin;
            left: 12px;
            padding: 0 6px;
            color: {t["accent_light"]};
        }}

        /* ── Progress Bar ── */
        QProgressBar {{
            border: 1px solid {t["border"]};
            border-radius: 4px;
            background: {t["elevated"]};
            text-align: center;
            font-size: 10px;
            color: {t["text2"]};
            min-height: 6px;
        }}
        QProgressBar::chunk {{
            background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                stop:0 {t["accent"]}, stop:1 {t["accent_light"]});
            border-radius: 3px;
        }}

        /* ── Scroll Area ── */
        QScrollArea {{ border: none; background: transparent; }}

        /* ── Dialog ── */
        QDialog {{
            background: {t["surface"]};
            border: 1px solid {t["border"]};
            border-radius: 10px;
        }}
    """)

    # Notify every registered widget so it can re-apply its local stylesheet
    # against the new palette. Done after the global Qt stylesheet is in
    # place so listeners can override-or-extend on top of it.
    _broadcast_theme_change(name)
