"""Reusable QSS builders for the Phase 3 theme refactor.

Every widget that paints itself with custom QSS calls a builder from this
module instead of inlining hex literals. Builders take the active palette
(:func:`gui.theme.get_theme`) so the result is theme-correct, and they
return plain f-strings ready for ``setStyleSheet``.

Pattern in a widget::

    from gui.styling import style_helpers as sh
    from gui.styling.theme import get_theme, register_theme_listener

    class MyTab(QWidget):
        def __init__(self):
            ...
            register_theme_listener(self._apply_theme)
            self._apply_theme()

        def _apply_theme(self):
            t = get_theme()
            self.top_bar.setStyleSheet(sh.top_bar(t))
            self.title.setStyleSheet(sh.label_title(t))
            ...

The builders are intentionally tiny — each is one or two QSS rules. Pulling
them into one place means a future palette tweak (e.g. swapping
``#0F0F12`` for ``surface``) lives in *one* file.
"""

from __future__ import annotations

from collections.abc import Mapping

# Colour fallbacks used when the active theme dict is missing a key
# (defensive — should never trigger in practice but keeps stylesheets valid).
_FALLBACK = {
<<<<<<< HEAD
    "bg": "#0B0B0D",
    "surface": "#131316",
    "elevated": "#1A1A1F",
    "elevated2": "#222228",
    "hover": "#2A2A32",
    "accent": "#2962FF",
    "accent_light": "#5C8AFF",
    "accent_dim": "#1A3A8F",
    "text": "#E8E8EE",
    "text2": "#8A8A96",
    "text3": "#55555F",
    "text_dis": "#3A3A42",
    "border": "#252530",
    "border2": "#1E1E28",
    "green": "#26A69A",
    "green_bright": "#00E676",
    "red": "#EF5350",
    "red_bright": "#FF1744",
    "amber": "#FF9800",
    "cyan": "#00BCD4",
    "purple": "#7C4DFF",
    "chart_bg": "#0B0B0D",
    "chart_grid": "#1A1A22",
=======
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
    "green": "#26A69A",
    "green_bright": "#00C853",
    "red": "#E04B4B",
    "red_bright": "#FF5252",
    "amber": "#F5A623",
    "cyan": "#00BCD4",
    "purple": "#7C4DFF",
    "chart_bg": "#0E1114",
    "chart_grid": "#1E2328",
>>>>>>> origin/main
}


def c(t: Mapping[str, str], key: str) -> str:
    """Resolve a colour from the theme dict with a hard-coded fallback."""
    return t.get(key) or _FALLBACK.get(key, "#000000")


# ---------------------------------------------------------------------- bars
def top_bar(t: Mapping[str, str]) -> str:
    return f"background: {c(t, 'surface')};border-bottom: 1px solid {c(t, 'border2')};"


def label_title(t: Mapping[str, str]) -> str:
    return f"color: {c(t, 'text')}; font-size: 14px; font-weight: 700; background: transparent;"


def label_subtitle(t: Mapping[str, str]) -> str:
    return f"color: {c(t, 'text2')}; font-size: 12px; background: transparent;"


# -------------------------------------------------------------------- panels
def panel(t: Mapping[str, str]) -> str:
    return f"background: {c(t, 'bg')};"


def card(t: Mapping[str, str]) -> str:
<<<<<<< HEAD
    """Solid card surface used for KPI tiles, summary blocks, etc."""
    return f"background: {c(t, 'surface')};border: 1px solid {c(t, 'border2')};border-radius: 6px;"
=======
    """Solid card surface used for KPI tiles, summary blocks, etc.

    Premium: rounded 10px with subtle border and hover accent.
    """
    return (
        f"background: {c(t, 'surface')};border: 1px solid {c(t, 'border2')};"
        f"border-radius: 10px;"
    )
>>>>>>> origin/main


def card_hover_accent(t: Mapping[str, str]) -> str:
    """Card with accent-coloured hover border (for clickable tiles)."""
    return (
        f"QFrame {{ background: {c(t, 'surface')}; "
<<<<<<< HEAD
        f"border: 1px solid {c(t, 'border2')}; border-radius: 6px; }}"
=======
        f"border: 1px solid {c(t, 'border2')}; border-radius: 10px; }}"
>>>>>>> origin/main
        f"QFrame:hover {{ border-color: {c(t, 'accent')}; }}"
    )


def splitter_handle(t: Mapping[str, str]) -> str:
    return f"QSplitter::handle {{ background: {c(t, 'border2')}; height: 3px; }}"


# ------------------------------------------------------------- analytics tables
def analytics_table(t: Mapping[str, str]) -> str:
    """Compact table styling used by Analytics / Quant / Stats panels.

<<<<<<< HEAD
    Matches the bg-tone of the surrounding panel so the table visually
    melts into the page. Different from the global ``QTableWidget`` rule
    (which is for full-width data grids in the main window).
    """
    return (
        f"QTableWidget {{ background: {c(t, 'bg')}; border: none;"
        f"  gridline-color: {c(t, 'chart_grid')}; }}"
        "QTableWidget::item { padding: 6px; }"
        f"QHeaderView::section {{"
        f"  background: {c(t, 'surface')}; color: {c(t, 'text3')};"
        "  font-size: 10px; font-weight: 700; letter-spacing: 0.5px;"
        f"  padding: 6px; border: none;"
        f"  border-bottom: 1px solid {c(t, 'border2')};"
=======
    Premium: right-aligned numeric columns, compact headers with accent underline.
    """
    from gui.styling.theme import font_mono
    return (
        f"QTableWidget, QTableView {{ background: {c(t, 'bg')}; border: none;"
        f"  gridline-color: {c(t, 'chart_grid')};"
        f"  font-family: {font_mono()}; font-size: 11px; }}"
        "QTableWidget::item, QTableView::item { padding: 4px 10px; }"
        "QTableWidget::item:selected, QTableView::item:selected {"
        f"  background: {c(t, 'accent_dim')}; color: {c(t, 'text')}; }}"
        f"QHeaderView::section {{"
        f"  background: {c(t, 'surface')}; color: {c(t, 'text3')};"
        "  font-family: 'Inter', 'Segoe UI', sans-serif;"
        "  font-size: 10px; font-weight: 700; letter-spacing: 0.5px;"
        f"  padding: 4px 10px; border: none;"
        f"  border-bottom: 2px solid {c(t, 'border2')};"
>>>>>>> origin/main
        "}"
    )


def info_block(t: Mapping[str, str]) -> str:
    """Boxed informational text — used for ``range_result``, ``adv_text``,
    ``ext_text`` blocks in Analytics."""
    return (
<<<<<<< HEAD
        f"background: {c(t, 'surface')}; padding: 14px; border-radius: 6px;"
=======
        f"background: {c(t, 'surface')}; padding: 14px; border-radius: 10px;"
>>>>>>> origin/main
        f" border: 1px solid {c(t, 'border2')}; color: {c(t, 'text')};"
    )


def info_block_muted(t: Mapping[str, str]) -> str:
    """Same as :func:`info_block` but with muted text for placeholder copy."""
    return (
<<<<<<< HEAD
        f"background: {c(t, 'surface')}; padding: 12px; border-radius: 6px;"
=======
        f"background: {c(t, 'surface')}; padding: 12px; border-radius: 10px;"
>>>>>>> origin/main
        f" border: 1px solid {c(t, 'border2')}; color: {c(t, 'text2')};"
        " font-size: 12px;"
    )


# ----------------------------------------------------------------- main toolbar
def main_toolbar(t: Mapping[str, str]) -> str:
    """Top-level toolbar of the main window — compact icon row above the
    chart with QPushButton / QToolButton / QComboBox / QSpinBox children."""
    return (
        f"QWidget#toolbar {{ background: {c(t, 'surface')}; "
        f"  border-bottom: 1px solid {c(t, 'border2')}; }}"
        f"QPushButton {{ min-height: 28px; max-height: 28px; min-width: 28px;"
        "  font-size: 13px; border-radius: 5px; padding: 0px 4px;"
        f"  background: transparent; border: 1px solid transparent;"
        f"  color: {c(t, 'text2')}; }}"
        f"QPushButton:hover {{ background: {c(t, 'elevated')}; "
        f"  border-color: {c(t, 'border')}; color: {c(t, 'text')}; }}"
        f"QPushButton:pressed, QPushButton:checked {{ background: {c(t, 'accent')};"
        f"  border-color: {c(t, 'accent')}; color: #fff; }}"
        f"QPushButton:disabled {{ color: {c(t, 'text_dis')}; }}"
        f"QToolButton {{ min-height: 28px; max-height: 28px; font-size: 13px;"
        "  border-radius: 5px; padding: 0px 4px; background: transparent;"
        f"  border: 1px solid transparent; color: {c(t, 'text2')}; }}"
        f"QToolButton:hover {{ background: {c(t, 'elevated')};"
        f"  border-color: {c(t, 'border')}; color: {c(t, 'text')}; }}"
        f"QToolButton:checked, QToolButton:pressed {{ background: {c(t, 'accent')};"
        f"  border-color: {c(t, 'accent')}; color: #fff; }}"
        "QToolButton::menu-indicator { image: none; }"
        f"QComboBox {{ min-height: 26px; max-height: 26px; font-size: 11px;"
        f"  background: {c(t, 'elevated')}; border: 1px solid {c(t, 'border')};"
        f"  border-radius: 5px; color: {c(t, 'text2')}; padding: 0 6px; }}"
        f"QComboBox:hover {{ border-color: {c(t, 'accent_light')};"
        f"  color: {c(t, 'text')}; }}"
        f"QSpinBox {{ min-height: 26px; max-height: 26px; font-size: 11px;"
        f"  background: {c(t, 'elevated')}; border: 1px solid {c(t, 'border')};"
        f"  border-radius: 5px; color: {c(t, 'text2')}; }}"
        f"QLabel {{ font-size: 11px; color: {c(t, 'text3')};"
        "  background: transparent; }"
    )


def vline(t: Mapping[str, str]) -> str:
    return f"background: {c(t, 'border')}; border: none;"


def main_progress_bar(t: Mapping[str, str]) -> str:
    return (
        "QProgressBar {"
        f"  background: {c(t, 'elevated')};"
        f"  border: 1px solid {c(t, 'border')};"
        "  border-radius: 3px;"
        "}"
        "QProgressBar::chunk {"
        "  background: qlineargradient(x1:0, y1:0, x2:1, y2:0,"
        f"    stop:0 {c(t, 'accent_dim')}, stop:1 {c(t, 'accent')});"
        "  border-radius: 2px;"
        "}"
    )


def info_label(t: Mapping[str, str]) -> str:
    """Small accent-coloured label used for transient status text."""
    from gui.styling.theme import font_mono

    return (
        f"color: {c(t, 'accent_light')}; font-size: 11px;"
        f" font-family: {font_mono()}; background: transparent;"
    )


def reset_button(t: Mapping[str, str]) -> str:
    return (
        "QPushButton {"
        "  background: transparent;"
        "  border: 1px solid transparent;"
        "  border-radius: 5px;"
        f"  color: {c(t, 'red')};"
        "  font-size: 16px;"
        "}"
        "QPushButton:hover {"
        f"  background: {c(t, 'elevated')};"
        f"  border-color: {c(t, 'red')};"
        f"  color: {c(t, 'red_bright')};"
        "}"
        f"QPushButton:pressed {{ background: {c(t, 'red')}; color: #fff; }}"
    )


def status_bar(t: Mapping[str, str]) -> str:
    from gui.styling.theme import font_mono

    return (
        f"QStatusBar {{ background: {c(t, 'surface')};"
        f"  border-top: 1px solid {c(t, 'border2')}; }}"
        "QStatusBar QLabel {"
        "  padding: 3px 12px;"
        f"  border-right: 1px solid {c(t, 'border2')};"
        "  font-size: 11px;"
        f"  font-family: {font_mono()};"
        "}"
    )


def status_label(t: Mapping[str, str], color_key: str) -> str:
    return f"color: {c(t, color_key)};"


def status_label_color(color: str, *, bold: bool = False) -> str:
    """Status bar label coloured by an *explicit* hex (used for PnL/WR
    values where the colour is derived from the live number)."""
    suffix = " font-weight: 600;" if bold else ""
    return f"color: {color};{suffix}"


# -------------------------------------------------------- semantic theme colors
def color_pnl_hex(t: Mapping[str, str], pnl: float) -> str:
    """Hex colour for PnL display (used on status bar where the QSS
    needs an explicit value)."""
    if pnl >= 0:
        return c(t, "green")
    return c(t, "red")


def trades_table(t: Mapping[str, str]) -> str:
<<<<<<< HEAD
    """Full-width trades data grid with selection + striped rows."""
    return (
        "QTableWidget {"
        f"  background: {c(t, 'elevated')};"
        f"  alternate-background-color: {c(t, 'elevated2')};"
        f"  gridline-color: {c(t, 'border')};"
        f"  border: 1px solid {c(t, 'border')};"
        "  border-radius: 4px;"
        "}"
        "QTableWidget::item { padding: 6px; }"
        "QTableWidget::item:selected {"
        f"  background: {c(t, 'border')};"
        "}"
        "QHeaderView::section {"
        f"  background: {c(t, 'border')};"
        f"  color: {c(t, 'text')};"
        "  padding: 8px;"
        "  border: none;"
        f"  border-right: 1px solid {c(t, 'elevated')};"
        f"  border-bottom: 1px solid {c(t, 'elevated')};"
        "  font-weight: bold;"
=======
    """Premium trades data grid with right-aligned numeric columns, compact headers."""
    from gui.styling.theme import font_mono
    return (
        "QTableWidget, QTableView {"
        f"  background: {c(t, 'surface')};"
        f"  alternate-background-color: {c(t, 'elevated')};"
        f"  gridline-color: {c(t, 'border2')};"
        f"  border: 1px solid {c(t, 'border')};"
        "  border-radius: 8px;"
        "  font-size: 11px;"
        f"  font-family: {font_mono()};"
        "}"
        "QTableWidget::item, QTableView::item {"
        "  padding: 4px 10px;"
        "  border: none;"
        "  border-bottom: 1px solid transparent;"
        "}"
        "QTableWidget::item:selected, QTableView::item:selected {"
        f"  background: {c(t, 'accent_dim')};"
        f"  color: {c(t, 'text')};"
        "}"
        "QHeaderView::section {"
        f"  background: {c(t, 'elevated2')};"
        f"  color: {c(t, 'text3')};"
        "  font-family: 'Inter', 'Segoe UI', sans-serif;"
        "  padding: 6px 10px;"
        "  border: none;"
        f"  border-right: 1px solid {c(t, 'surface')};"
        f"  border-bottom: 2px solid {c(t, 'border')};"
        "  font-size: 10px;"
        "  font-weight: 700;"
        "  letter-spacing: 0.5px;"
        "  text-transform: uppercase;"
        "}"
        "QHeaderView::section:first {"
        "  border-radius: 8px 0 0 0;"
        "}"
        "QHeaderView::section:last {"
        "  border-right: none;"
        "  border-radius: 0 8px 0 0;"
>>>>>>> origin/main
        "}"
    )


def section_caption(t: Mapping[str, str]) -> str:
    """Small uppercase section label (used in QuantTab heatmaps / tables)."""
    return (
        f"color: {c(t, 'text2')}; font-size: 11px; font-weight: 600;"
        " letter-spacing: 0.6px; text-transform: uppercase;"
    )


def summary_label(t: Mapping[str, str]) -> str:
    """Single-line monospace summary text under a chart."""
    from gui.styling.theme import font_mono

    return f"color: {c(t, 'text2')}; font-family: {font_mono()}; font-size: 11px;"


def code_text(t: Mapping[str, str]) -> str:
    """Read-only QTextEdit / QPlainTextEdit panel showing diff-style output."""
    from gui.styling.theme import font_mono

    return (
        f"background: {c(t, 'surface')};"
        f" border: 1px solid {c(t, 'border2')}; border-radius: 4px;"
        f" padding: 8px; font-family: {font_mono()}; font-size: 11px;"
        f" color: {c(t, 'text')};"
    )


def color_winrate_hex(t: Mapping[str, str], wr: float) -> str:
    if wr >= 50:
        return c(t, "green")
    if wr >= 40:
        return c(t, "amber")
    return c(t, "red")


# ------------------------------------------------------------------ sub-tabs
def sub_tabs(t: Mapping[str, str]) -> str:
<<<<<<< HEAD
    return (
        f"QTabWidget::pane {{ border: none; background: {c(t, 'bg')}; }}"
        "QTabBar::tab {"
        f"  background: {c(t, 'surface')}; color: {c(t, 'text2')};"
        "  padding: 6px 14px; font-size: 11px; font-weight: 600;"
        "  border-bottom: 2px solid transparent;"
        "}"
        f"QTabBar::tab:selected {{ color: {c(t, 'text')}; "
        f"  border-bottom-color: {c(t, 'accent')}; }}"
        f"QTabBar::tab:hover {{ color: {c(t, 'text')}; }}"
=======
    """Premium sub-tabs with larger 2.5px accent underline, rounded active tab bg."""
    return (
        f"QTabWidget::pane {{ border: none; background: {c(t, 'bg')}; }}"
        "QTabBar::tab {"
        f"  background: transparent; color: {c(t, 'text3')};"
        "  padding: 8px 16px; font-size: 11px; font-weight: 600;"
        "  border-bottom: 2.5px solid transparent;"
        "  border-radius: 0;"
        "}"
        f"QTabBar::tab:selected {{ color: {c(t, 'text')}; "
        f"  border-bottom-color: {c(t, 'accent')}; "
        f"  background: {c(t, 'surface')}; }}"
        f"QTabBar::tab:hover {{ color: {c(t, 'text2')}; "
        f"  background: {c(t, 'elevated')}; }}"
>>>>>>> origin/main
    )


# ------------------------------------------------------------------- buttons
<<<<<<< HEAD
=======
def btn_default(t: Mapping[str, str]) -> str:
    """Default flat button — theme-aware text, subtle hover."""
    return (
        f"QPushButton {{ background: {c(t, 'surface')}; color: {c(t, 'text')};"
        f"  border: 1px solid {c(t, 'border2')}; border-radius: 6px;"
        f"  padding: 4px 12px; font-weight: 500; font-size: 11px; }}"
        f"QPushButton:hover {{ background: {c(t, 'elevated')}; border-color: {c(t, 'accent')}; }}"
        f"QPushButton:pressed {{ background: {c(t, 'accent')}; color: #fff; }}"
    )


>>>>>>> origin/main
def btn_accent(t: Mapping[str, str]) -> str:
    """Pill-style accent button — used for primary CTAs (e.g. Run, Refresh)."""
    return (
        f"background: {c(t, 'accent')}; color: #FFFFFF;"
        f"border: 1px solid {c(t, 'accent')};"
        "border-radius: 6px; padding: 6px 14px;"
        "font-weight: 600; font-size: 11px;"
    )


# --------------------------------------------------------------- value styling
def value_label(t: Mapping[str, str], color_key: str = "text") -> str:
<<<<<<< HEAD
    """Numeric / KPI label — Roboto Mono, large weight."""
=======
    """Numeric / KPI label — JetBrains Mono, large weight."""
>>>>>>> origin/main
    from gui.styling.theme import font_mono

    return (
        f"color: {c(t, color_key)}; font-size: 16px; font-weight: 700; "
        f"font-family: {font_mono()}; background: transparent;"
    )


def value_label_color(color: str) -> str:
    """Numeric label coloured by an *explicit* hex (used when the value is
    semantic — e.g. green if positive — and is computed at render time)."""
    from gui.styling.theme import font_mono

    return (
        f"color: {color}; font-size: 16px; font-weight: 700; "
        f"font-family: {font_mono()}; background: transparent;"
    )


def caption(t: Mapping[str, str]) -> str:
    return (
        f"color: {c(t, 'text3')}; font-size: 9px; font-weight: 700; "
        "letter-spacing: 0.8px; background: transparent;"
    )


# ------------------------------------------------------------- semantic colors
def color_for_pnl(t: Mapping[str, str], pnl: float) -> str:
    """Theme-aware colour for PnL / return values (green positive, red negative)."""
    if pnl > 0:
        return c(t, "green")
    if pnl < 0:
        return c(t, "red")
    return c(t, "text2")


def color_for_signal(t: Mapping[str, str], score: float) -> str:
    """Cyan (positive), red (negative), grey (neutral) ladder for the
    misc heat / streak panels."""
    if score > 0.5:
        return c(t, "green_bright")
    if score > 0:
        return c(t, "green")
    if score < -0.5:
        return c(t, "red_bright")
    if score < 0:
        return c(t, "red")
<<<<<<< HEAD
    return c(t, "text2")
=======
    return c(t, "text2")
>>>>>>> origin/main
