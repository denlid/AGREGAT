"""Run-mode picker dialog (Phase 2, step 4 of the dual-engine plan).

Shown when the user clicks ▶︎ Run-to-End on a strategy that supports the
new ``vectorized_signals(df)`` API. Lets the user pick between:

- 👁 **Visual / Debug** — the original step-by-step on_candle replay; the
  chart redraws bar-by-bar so SL/TP, drawings and BarReplay stay live.
- ⚡ **Quant / Optimisation** — the new
  :func:`engine.vectorized_engine.run_vectorized` path; SL / TP / equity
  are computed in a single NumPy pass with the chart hidden under a
  blur-style spinner overlay. Designed for parameter sweeps.

When the strategy only implements ``on_candle``, the GUI calls
:func:`run_visual_default` directly without showing the dialog so existing
strategies behave exactly like before.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class RunModeDialog(QDialog):
    """Asks the user to pick a Visual or Quant run mode.

    Returns ``"visual"`` or ``"quant"`` from :pymeth:`pick`. The legacy
    ``QDialog.exec()`` accept/reject pair is reused — accept = the chosen
    mode, reject = user cancelled.
    """

    def __init__(self, parent: Optional[QWidget] = None, has_vectorized: bool = True) -> None:
        super().__init__(parent)
        self.setWindowTitle("Choose Run Mode")
        self.setModal(True)
        self.setMinimumWidth(420)
        self._mode: Optional[str] = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 18, 20, 14)
        outer.setSpacing(12)

        title = QLabel("How would you like to run the simulation?")
        from gui.styling import style_helpers as sh
        from gui.styling.theme import get_theme

        title.setStyleSheet(
            f"font-size: 14px; font-weight: 700; color: {sh.c(get_theme(), 'text')};"
        )
        outer.addWidget(title)

        hint = QLabel(
            "👁  <b>Visual</b> redraws the chart candle-by-candle so you can debug "
            "drawings and SL/TP triggers live.<br>"
            "⚡  <b>Quant</b> runs the full backtest in a single NumPy pass — "
            "ideal for optimisation sweeps."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {sh.c(get_theme(), 'text2')}; font-size: 11px;")
        outer.addWidget(hint)

        row = QHBoxLayout()
        row.setSpacing(10)

        self.btn_visual = QPushButton("👁  Visual\n(Debug, step-by-step)")
        self.btn_visual.setMinimumHeight(72)
        self.btn_visual.clicked.connect(self._pick_visual)
        row.addWidget(self.btn_visual)

        self.btn_quant = QPushButton("⚡  Quant\n(Fast, vectorised)")
        self.btn_quant.setMinimumHeight(72)
        self.btn_quant.setEnabled(has_vectorized)
        if not has_vectorized:
            self.btn_quant.setToolTip(
                "This strategy does not implement vectorized_signals(df) — "
                "Quant mode is unavailable."
            )
        self.btn_quant.clicked.connect(self._pick_quant)
        row.addWidget(self.btn_quant)
        outer.addLayout(row)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons, alignment=Qt.AlignmentFlag.AlignRight)

    def _pick_visual(self) -> None:
        self._mode = "visual"
        self.accept()

    def _pick_quant(self) -> None:
        self._mode = "quant"
        self.accept()

    @property
    def mode(self) -> Optional[str]:
        return self._mode

    @classmethod
    def pick(
        cls,
        parent: Optional[QWidget] = None,
        has_vectorized: bool = True,
    ) -> Optional[str]:
        """Convenience wrapper — show the dialog modally and return the
        chosen mode (or ``None`` if cancelled)."""
        dlg = cls(parent=parent, has_vectorized=has_vectorized)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            return dlg.mode
        return None
