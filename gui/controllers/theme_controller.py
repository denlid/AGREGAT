"""Sprint 3.5 — Theme switching and per-widget stylesheet refresh."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt

if TYPE_CHECKING:
    from gui.main_window import MainWindow

logger = logging.getLogger("StrategyTester.gui.theme_ctrl")


class ThemeController:
    """Owns theme switching and the ``_apply_theme`` callback."""

    def __init__(self, host: MainWindow) -> None:
        self._host = host

    def switch_theme(self, name: str) -> None:
        from PySide6.QtWidgets import QApplication

        from gui.styling.theme import apply_theme

        applied = name
        if name == "auto":
            try:
                scheme = QApplication.instance().styleHints().colorScheme()  # type: ignore[attr-defined]
                applied = "light" if scheme == Qt.ColorScheme.Light else "dark"
            except Exception:
                applied = "dark"
        apply_theme(QApplication.instance(), applied)
        self._host.chart_tab.apply_chart_theme()
        self._host.save_settings("theme", name)

    def switch_chart_colors(self, name: str) -> None:
        self._host.chart_tab.set_color_scheme(name)
        self._host.save_settings("chart_colors", name)

    def apply_theme(self) -> None:
        """Re-apply every locally-overridden stylesheet from the active palette."""
        from gui.styling import style_helpers as sh
        from gui.styling.theme import get_theme

        h = self._host
        t = get_theme()

        if hasattr(h, "custom_title_bar"):
            h.custom_title_bar._apply_style(t)

        if hasattr(h, "centralWidget"):
            cw = h.centralWidget()
            if cw:
                cw.setStyleSheet(
                    f"#main_container {{ border: 1px solid {sh.c(t, 'border')}; "
                    f"background: {sh.c(t, 'bg')}; }}"
                )

        if hasattr(h, "_tb_widget"):
            h._tb_widget.setStyleSheet(sh.main_toolbar(t))
        for s in getattr(h, "_toolbar_separators", []):
            s.setStyleSheet(sh.vline(t))
        if hasattr(h, "progress"):
            h.progress.setStyleSheet(sh.main_progress_bar(t))
        if hasattr(h, "lbl_info"):
            h.lbl_info.setStyleSheet(sh.info_label(t))
        if hasattr(h, "btn_reset"):
            h.btn_reset.setStyleSheet(sh.reset_button(t))
        if hasattr(h, "_status_bar"):
            h._status_bar.setStyleSheet(sh.status_bar(t))

        for attr, key in (
            ("st_eq", "accent_light"),
            ("st_pos", "text2"),
            ("st_tr", "text2"),
            ("st_pnl", "text2"),
            ("st_wr", "text2"),
        ):
            w = getattr(h, attr, None)
            if w is not None:
                w.setStyleSheet(sh.status_label(t, key))
        if hasattr(h, "st_ses"):
            h.st_ses.setStyleSheet(sh.status_label(t, "amber") + " border-right: none;")
