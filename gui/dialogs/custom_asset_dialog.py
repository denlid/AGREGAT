"""Dialog that lets the user define a new trading instrument on the fly.

The new symbol is persisted to ``config/assets.json`` via
:func:`engine.asset_registry.save_asset` so it survives across sessions and
is picked up by both the asset selector and :meth:`BacktestEngine.load_asset_config`.

Required fields are kept to a minimum (point value, tick size, currency); the
rest (commissions, slippage, trading hours) default to sane values that mimic
the bundled top-10 indices.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, Tuple

from PySide6.QtCore import QRegularExpression, Qt, QTime
from PySide6.QtGui import QRegularExpressionValidator
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QSpinBox,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

_NAME_RE = QRegularExpression(r"[A-Za-z][A-Za-z0-9_]{0,15}")
_COMMON_CURRENCIES = ["USD", "EUR", "GBP", "JPY", "AUD", "CAD", "CHF", "HKD", "CNY"]


class CustomAssetDialog(QDialog):
    """Form for adding a single new asset entry to ``config/assets.json``."""

    def __init__(self, existing: Iterable[str] = (), parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Add Custom Asset")
        self.setMinimumWidth(380)
        self._existing = {str(n).upper() for n in existing}

        layout = QVBoxLayout(self)
        intro = QLabel(
            "Define a new instrument. It will be added to "
            "<code>config/assets.json</code> and become available across sessions."
        )
        intro.setWordWrap(True)
        from gui.styling import style_helpers as sh
        from gui.styling.theme import get_theme

        intro.setStyleSheet(f"color: {sh.c(get_theme(), 'text2')}; font-size: 11px;")
        layout.addWidget(intro)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        layout.addLayout(form)

        self.ed_name = QLineEdit()
        self.ed_name.setPlaceholderText("e.g. SPX500_FUT")
        self.ed_name.setValidator(QRegularExpressionValidator(_NAME_RE, self))
        self.ed_name.setMaxLength(16)
        form.addRow("Symbol*", self.ed_name)

        self.ed_display = QLineEdit()
        self.ed_display.setPlaceholderText("Optional — shown in the UI")
        form.addRow("Display name", self.ed_display)

        self.cb_ccy = QComboBox()
        self.cb_ccy.setEditable(True)
        self.cb_ccy.addItems(_COMMON_CURRENCIES)
        self.cb_ccy.setCurrentText("USD")
        form.addRow("Currency", self.cb_ccy)

        self.sp_point_value = QDoubleSpinBox()
        self.sp_point_value.setRange(0.0001, 10_000.0)
        self.sp_point_value.setDecimals(4)
        self.sp_point_value.setValue(1.0)
        self.sp_point_value.setToolTip("$ profit per 1 lot per 1 point of price movement.")
        form.addRow("Point value*", self.sp_point_value)

        self.sp_tick_size = QDoubleSpinBox()
        self.sp_tick_size.setRange(0.0001, 1_000.0)
        self.sp_tick_size.setDecimals(4)
        self.sp_tick_size.setValue(0.25)
        self.sp_tick_size.setToolTip("Smallest price increment in the broker's quote.")
        form.addRow("Tick size*", self.sp_tick_size)

        self.sp_buffer = QDoubleSpinBox()
        self.sp_buffer.setRange(0.0, 50.0)
        self.sp_buffer.setDecimals(2)
        self.sp_buffer.setValue(1.0)
        form.addRow("Stop buffer (pts)", self.sp_buffer)

        self.sp_max_lots = QSpinBox()
        self.sp_max_lots.setRange(1, 1_000)
        self.sp_max_lots.setValue(12)
        self.sp_max_lots.setToolTip("Risk-cap: max lots per $100k of equity.")
        form.addRow("Max lots / $100k", self.sp_max_lots)

        self.sp_min_lot = QDoubleSpinBox()
        self.sp_min_lot.setRange(0.001, 100.0)
        self.sp_min_lot.setDecimals(3)
        self.sp_min_lot.setValue(0.1)
        form.addRow("Min lot", self.sp_min_lot)

        self.sp_lot_step = QDoubleSpinBox()
        self.sp_lot_step.setRange(0.001, 100.0)
        self.sp_lot_step.setDecimals(3)
        self.sp_lot_step.setValue(0.1)
        form.addRow("Lot step", self.sp_lot_step)

        self.sp_spread = QDoubleSpinBox()
        self.sp_spread.setRange(0.0, 1_000.0)
        self.sp_spread.setDecimals(2)
        self.sp_spread.setValue(1.0)
        form.addRow("Spread (pts)", self.sp_spread)

        self.sp_commission = QDoubleSpinBox()
        self.sp_commission.setRange(0.0, 1_000.0)
        self.sp_commission.setDecimals(2)
        self.sp_commission.setValue(4.0)
        form.addRow("Commission / lot ($)", self.sp_commission)

        self.sp_slippage = QDoubleSpinBox()
        self.sp_slippage.setRange(0.0, 1_000.0)
        self.sp_slippage.setDecimals(2)
        self.sp_slippage.setValue(0.5)
        form.addRow("Slippage (pts)", self.sp_slippage)

        self.tm_open = QTimeEdit(QTime(9, 0))
        self.tm_open.setDisplayFormat("HH:mm")
        form.addRow("Trading hours start", self.tm_open)

        self.tm_close = QTimeEdit(QTime(18, 0))
        self.tm_close.setDisplayFormat("HH:mm")
        form.addRow("Trading hours end", self.tm_close)

        self.tm_eod = QTimeEdit(QTime(22, 0))
        self.tm_eod.setDisplayFormat("HH:mm")
        form.addRow("EOD close", self.tm_eod)

        self.cb_enforce_hours = QCheckBox("Enforce trading hours")
        form.addRow("", self.cb_enforce_hours)

        self.cb_close_weekend = QCheckBox("Close positions on weekend")
        self.cb_close_weekend.setChecked(True)
        form.addRow("", self.cb_close_weekend)

        # Buttons
        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        bb.button(QDialogButtonBox.StandardButton.Save).setText("Save && Use")
        bb.accepted.connect(self._on_accept)
        bb.rejected.connect(self.reject)
        layout.addWidget(bb)

        self._result: Tuple[str, Dict[str, Any]] | None = None

    def _on_accept(self) -> None:
        name = self.ed_name.text().strip().upper()
        if not name or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,15}", name):
            QMessageBox.warning(
                self,
                "Invalid symbol",
                "Symbol must start with a letter and contain only letters, "
                "digits or underscores (max 16 chars).",
            )
            return
        if name in self._existing:
            QMessageBox.warning(
                self,
                "Symbol already exists",
                f"'{name}' is already defined. Pick a different symbol or "
                f"edit config/assets.json directly.",
            )
            return
        cfg: Dict[str, Any] = {
            "point_value": float(self.sp_point_value.value()),
            "tick_size": float(self.sp_tick_size.value()),
            "buffer": float(self.sp_buffer.value()),
            "max_lots_per_100k": int(self.sp_max_lots.value()),
            "currency": self.cb_ccy.currentText().strip().upper() or "USD",
            "min_lot": float(self.sp_min_lot.value()),
            "lot_step": float(self.sp_lot_step.value()),
            "spread_points": float(self.sp_spread.value()),
            "commission_per_lot": float(self.sp_commission.value()),
            "slippage_points": float(self.sp_slippage.value()),
            "trading_hours_start": self.tm_open.time().toString("HH:mm"),
            "trading_hours_end": self.tm_close.time().toString("HH:mm"),
            "eod_close": self.tm_eod.time().toString("HH:mm"),
            "enforce_trading_hours": bool(self.cb_enforce_hours.isChecked()),
            "close_on_weekend": bool(self.cb_close_weekend.isChecked()),
        }
        display = self.ed_display.text().strip()
        if display:
            cfg["display_name"] = display
        self._result = (name, cfg)
        self.accept()

    def result(self) -> Tuple[str, Dict[str, Any]]:
        """Return ``(symbol, config_dict)`` selected by the user.

        Only valid after ``exec()`` returned ``DialogCode.Accepted``.
        """
        if self._result is None:
            raise RuntimeError("Dialog was not accepted; result is undefined.")
        return self._result


__all__ = ["CustomAssetDialog"]
