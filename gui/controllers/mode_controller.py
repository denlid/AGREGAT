"""
ModeController — Retail / Quant dashboard toggle.

Owns one piece of UI state: which audience the dashboard is rendering for.
Tabs subscribe to :pyattr:`mode_changed` and toggle the visibility of
quant-only sections (Sortino/Calmar/Omega/Ulcer, etc.) so retail users see
a focused tile-set and quant users see the full instrument panel.

The signal payload is one of two strings: ``"retail"`` or ``"quant"``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Iterable

from PySide6.QtCore import QObject, Signal

logger = logging.getLogger("StrategyTester.gui.mode_ctrl")

if TYPE_CHECKING:
    from gui.main_window import MainWindow


_RETAIL = "retail"
_QUANT = "quant"
_VALID = (_RETAIL, _QUANT)


class ModeController(QObject):
    """Dashboard audience toggle controller.

    A single instance lives on `MainWindow.mode_controller`. Tabs that
    want to react simply do::

        host.mode_controller.mode_changed.connect(self._on_mode_changed)

    and gate their quant-only widgets with :pymeth:`is_quant` on first
    paint.
    """

    mode_changed = Signal(str)

    def __init__(self, host: "MainWindow") -> None:
        super().__init__(host)
        self._host = host
        self._mode: str = _RETAIL
        # Restore saved preference if available.
        try:
            settings = getattr(host, "settings", {}) or {}
            if not settings:
                from gui.persistence import settings_persist

                settings = settings_persist.load_preferences()
            saved = settings.get("dashboard_mode", _RETAIL)
        except Exception:
            saved = _RETAIL
        if saved in _VALID:
            self._mode = saved

    # ------------------------------------------------------------------
    @property
    def mode(self) -> str:
        return self._mode

    def is_retail(self) -> bool:
        return self._mode == _RETAIL

    def is_quant(self) -> bool:
        return self._mode == _QUANT

    def set_mode(self, mode: str) -> None:
        if mode not in _VALID:
            logger.warning("Ignored unknown mode %r", mode)
            return
        if mode == self._mode:
            return
        self._mode = mode
        try:
            self._host.save_settings("dashboard_mode", mode)  # type: ignore[attr-defined]
        except Exception:
            pass
        logger.info("Dashboard mode -> %s", mode)
        self.mode_changed.emit(mode)

    def toggle(self) -> None:
        self.set_mode(_QUANT if self._mode == _RETAIL else _RETAIL)

    # ------------------------------------------------------------------
    def apply_to(self, retail_widgets: Iterable, quant_widgets: Iterable) -> None:
        """Helper for tabs — show/hide widget groups based on current mode.

        Both arguments are iterables of ``QWidget``. Retail widgets are
        always visible; quant widgets are only visible in quant mode.
        Robust against `None` entries.
        """
        quant = self.is_quant()
        for w in retail_widgets:
            if w is not None:
                try:
                    w.setVisible(True)
                except Exception:
                    pass
        for w in quant_widgets:
            if w is not None:
                try:
                    w.setVisible(quant)
                except Exception:
                    pass


__all__ = ["ModeController"]
