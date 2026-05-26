"""
PeriodSelector — TradingView-style period buttons.

A horizontal row of compact buttons ``1D | 1W | 1M | 3M | 6M | YTD | 1Y | ALL``
plus a ``⋮`` overflow that opens a custom date-range popup. The active button
is rendered with the theme's accent background; hover gives an accent border.

The widget emits a single signal:

    period_changed(kind: str, start: Optional[pd.Timestamp], end: Optional[pd.Timestamp])

``kind`` is one of:

    '1D', '1W', '1M', '3M', '6M', 'YTD', '1Y', 'ALL', 'CUSTOM'

For preset kinds (everything except ``CUSTOM``), ``start`` is ``None`` and
``end`` is ``None`` — the consumer is responsible for computing the actual
window from the kind. For ``CUSTOM``, both are non-``None`` ``pd.Timestamp``.

Hotkeys ``1..8`` switch between the eight preset kinds in order. Hotkeys are
installed via ``QShortcut`` with ``Qt.ApplicationShortcut`` context so they
trigger only while the host tab is visible.

Persistence: the host can call :pymeth:`save_state` / :pymeth:`restore_state`
to plumb the current selection through its own settings layer.
"""

from __future__ import annotations

import logging
from typing import Optional

import pandas as pd
from PySide6.QtCore import QDate, Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QWidget,
)

from gui.styling import style_helpers as sh
from gui.styling.theme import (
    get_theme,
    register_theme_listener,
    unregister_theme_listener,
)

logger = logging.getLogger("StrategyTester.gui.widgets.period_selector")


PRESETS = ("1D", "1W", "1M", "3M", "6M", "YTD", "1Y", "ALL")
"""Ordered preset kinds. Hotkeys 1..8 map to these in order."""


class _CustomRangeDialog(QDialog):
    """Tiny modal that asks for a from/to date pair."""

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        *,
        initial_from: Optional[pd.Timestamp] = None,
        initial_to: Optional[pd.Timestamp] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Custom range")
        self.setModal(True)
        form = QFormLayout(self)

        self._from = QDateEdit()
        self._from.setCalendarPopup(True)
        self._from.setDate(
            QDate(initial_from.year, initial_from.month, initial_from.day)
            if initial_from is not None
            else QDate(2021, 1, 1)
        )
        self._to = QDateEdit()
        self._to.setCalendarPopup(True)
        self._to.setDate(
            QDate(initial_to.year, initial_to.month, initial_to.day)
            if initial_to is not None
            else QDate.currentDate()
        )

        form.addRow(QLabel("From"), self._from)
        form.addRow(QLabel("To"), self._to)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def selected(self) -> tuple[pd.Timestamp, pd.Timestamp]:
        return (
            pd.Timestamp(self._from.date().toPython()),
            pd.Timestamp(self._to.date().toPython()),
        )


class PeriodSelector(QWidget):
    """TradingView-style horizontal preset selector.

    See module docstring for emit semantics.
    """

    period_changed = Signal(str, object, object)  # kind, start, end

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        *,
        default_kind: str = "ALL",
        enable_hotkeys: bool = True,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("periodSelector")
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

        self._kind: str = default_kind if default_kind in PRESETS else "ALL"
        self._custom_start: Optional[pd.Timestamp] = None
        self._custom_end: Optional[pd.Timestamp] = None

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)

        self._buttons: dict[str, QPushButton] = {}
        for kind in PRESETS:
            b = QPushButton(kind, self)
            b.setCheckable(True)
            b.setFixedHeight(26)
            b.setMinimumWidth(32)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setProperty("periodKind", kind)
            b.clicked.connect(self._on_preset_clicked)
            lay.addWidget(b)
            self._buttons[kind] = b

        self._custom_btn = QPushButton("\u22EE", self)  # ⋮
        self._custom_btn.setCheckable(True)
        self._custom_btn.setFixedHeight(26)
        self._custom_btn.setFixedWidth(28)
        self._custom_btn.setToolTip("Custom date range")
        self._custom_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._custom_btn.setProperty("periodKind", "CUSTOM")
        self._custom_btn.clicked.connect(self._on_custom_clicked)
        lay.addWidget(self._custom_btn)

        self._shortcuts: list[QShortcut] = []
        if enable_hotkeys:
            self._install_hotkeys()

        self._apply_theme()
        register_theme_listener(self._apply_theme)
        self._theme_registered = True

        # Reflect default selection
        self._set_active_button(self._kind)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def closeEvent(self, e):  # type: ignore[override]
        self._teardown()
        super().closeEvent(e)

    def destroy(self, destroyWindow=True, destroySubWindows=True):  # type: ignore[override]
        self._teardown()
        super().destroy(destroyWindow, destroySubWindows)

    def _teardown(self) -> None:
        if getattr(self, "_theme_registered", False):
            unregister_theme_listener(self._apply_theme)
            self._theme_registered = False
        for sc in self._shortcuts:
            try:
                sc.setParent(None)  # type: ignore[arg-type]
            except Exception:
                pass
        self._shortcuts.clear()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def kind(self) -> str:
        return self._kind

    @property
    def custom_range(self) -> tuple[Optional[pd.Timestamp], Optional[pd.Timestamp]]:
        return (self._custom_start, self._custom_end)

    def set_kind(self, kind: str, *, emit: bool = True) -> None:
        """Programmatic selection. Custom must be set via :pymeth:`set_custom_range`."""
        kind = (kind or "").upper()
        if kind not in PRESETS:
            logger.debug("PeriodSelector ignoring unknown kind %r", kind)
            return
        self._kind = kind
        self._custom_start = None
        self._custom_end = None
        self._set_active_button(kind)
        if emit:
            self.period_changed.emit(kind, None, None)

    def set_custom_range(
        self, start: pd.Timestamp, end: pd.Timestamp, *, emit: bool = True
    ) -> None:
        self._kind = "CUSTOM"
        self._custom_start = pd.Timestamp(start)
        self._custom_end = pd.Timestamp(end)
        self._set_active_button("CUSTOM")
        if emit:
            self.period_changed.emit("CUSTOM", self._custom_start, self._custom_end)

    def save_state(self) -> dict:
        """Return a JSON-serialisable snapshot for host persistence."""
        return {
            "kind": self._kind,
            "custom_start": (
                self._custom_start.isoformat() if self._custom_start is not None else None
            ),
            "custom_end": (
                self._custom_end.isoformat() if self._custom_end is not None else None
            ),
        }

    def restore_state(self, state: dict | None) -> None:
        if not isinstance(state, dict):
            return
        kind = str(state.get("kind", "ALL")).upper()
        if kind == "CUSTOM":
            try:
                s = pd.Timestamp(state["custom_start"])
                e = pd.Timestamp(state["custom_end"])
                self.set_custom_range(s, e, emit=False)
                return
            except Exception:
                logger.debug("PeriodSelector failed to restore custom range; falling back to ALL")
                kind = "ALL"
        if kind not in PRESETS:
            kind = "ALL"
        self.set_kind(kind, emit=False)

    # ------------------------------------------------------------------
    # Hotkeys
    # ------------------------------------------------------------------

    def _install_hotkeys(self) -> None:
        for i, kind in enumerate(PRESETS, start=1):
            sc = QShortcut(QKeySequence(str(i)), self)
            sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            sc.activated.connect(lambda k=kind: self.set_kind(k))
            self._shortcuts.append(sc)

    # ------------------------------------------------------------------
    # Internal handlers
    # ------------------------------------------------------------------

    def _on_preset_clicked(self) -> None:
        sender = self.sender()
        if sender is None:
            return
        kind = sender.property("periodKind")
        if isinstance(kind, str) and kind in PRESETS:
            self.set_kind(kind)
        else:
            # Re-sync button state if something weird happened.
            self._set_active_button(self._kind)

    def _on_custom_clicked(self) -> None:
        dlg = _CustomRangeDialog(
            self, initial_from=self._custom_start, initial_to=self._custom_end
        )
        if dlg.exec() == QDialog.DialogCode.Accepted:
            s, e = dlg.selected()
            if s > e:
                s, e = e, s
            self.set_custom_range(s, e)
        else:
            # User cancelled — restore previous selection's button state.
            self._set_active_button(self._kind)

    # ------------------------------------------------------------------
    # Visual state
    # ------------------------------------------------------------------

    def _set_active_button(self, kind: str) -> None:
        """Sync checked state of all buttons to ``kind``."""
        for k, btn in self._buttons.items():
            btn.setChecked(k == kind)
        self._custom_btn.setChecked(kind == "CUSTOM")
        # Theming uses checked state, so re-apply styles to refresh.
        self._apply_theme()

    def _apply_theme(self) -> None:
        t = get_theme()
        base_bg = sh.c(t, "surface")
        hover_border = sh.c(t, "accent")
        active_bg = sh.c(t, "accent")
        active_fg = "#FFFFFF"
        idle_fg = sh.c(t, "text2")
        border = sh.c(t, "border2")

        style = (
            "QPushButton {"
            f"  background: {base_bg}; color: {idle_fg};"
            f"  border: 1px solid {border}; border-radius: 4px;"
            "  padding: 0 8px; font-size: 11px; font-weight: 600;"
            "  letter-spacing: 0.3px;"
            "}"
            "QPushButton:hover {"
            f"  border-color: {hover_border}; color: {sh.c(t, 'text')};"
            "}"
            "QPushButton:checked {"
            f"  background: {active_bg}; color: {active_fg};"
            f"  border-color: {active_bg};"
            "}"
            "QPushButton:focus { outline: none; }"
        )
        for btn in self._buttons.values():
            btn.setStyleSheet(style)
        self._custom_btn.setStyleSheet(style)


__all__ = ["PeriodSelector", "PRESETS"]
