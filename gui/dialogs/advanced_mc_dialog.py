"""Advanced Monte Carlo dialog (Phase 4): bootstrap + shock injection."""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QPushButton,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
)

from engine.analytics.monte_carlo import MCResult, bootstrap_pnl, shock_pnl

logger = logging.getLogger("StrategyTester.gui.advanced_mc_dialog")


class _MCWorker(QThread):
    finished = Signal(object)  # MCResult
    failed = Signal(str)

    def __init__(
        self,
        pnls: np.ndarray,
        initial_equity: float,
        mode: str,
        n_iter: int,
        slippage_std_pct: float,
        missed_fill_pct: float,
        seed: Optional[int],
    ) -> None:
        super().__init__()
        self.pnls = pnls
        self.initial_equity = initial_equity
        self.mode = mode
        self.n_iter = n_iter
        self.slippage_std_pct = slippage_std_pct
        self.missed_fill_pct = missed_fill_pct
        self.seed = seed

    def run(self) -> None:
        try:
            if self.mode == "bootstrap":
                res = bootstrap_pnl(
                    self.pnls,
                    initial_equity=self.initial_equity,
                    n_iter=self.n_iter,
                    seed=self.seed,
                )
            else:
                res = shock_pnl(
                    self.pnls,
                    initial_equity=self.initial_equity,
                    slippage_std_pct=self.slippage_std_pct,
                    missed_fill_pct=self.missed_fill_pct,
                    n_iter=self.n_iter,
                    seed=self.seed,
                )
            self.finished.emit(res)
        except Exception as e:
            logger.exception("MC simulation failed")
            self.failed.emit(repr(e))


class AdvancedMonteCarloDialog(QDialog):
    """Run bootstrap or shock-injection MC on the current trade list."""

    def __init__(self, engine, parent=None):
        super().__init__(parent)
        self.engine = engine
        self.worker: Optional[_MCWorker] = None
        self.setWindowTitle("Advanced Monte Carlo (Phase 4)")
        self.resize(820, 620)

        layout = QVBoxLayout(self)

        # Settings
        cfg = QGroupBox("Simulation Configuration")
        form = QFormLayout(cfg)

        self.mode_combo = QComboBox()
        self.mode_combo.addItems(
            ["Bootstrap (resample trades)", "Shock injection (slippage + missed fills)"]
        )
        self.mode_combo.currentIndexChanged.connect(self._mode_changed)
        form.addRow("Mode:", self.mode_combo)

        self.iter_spin = QSpinBox()
        self.iter_spin.setRange(100, 100_000)
        self.iter_spin.setValue(2_000)
        self.iter_spin.setSingleStep(500)
        form.addRow("Iterations:", self.iter_spin)

        self.slip_spin = QDoubleSpinBox()
        self.slip_spin.setRange(0.0, 50.0)
        self.slip_spin.setSingleStep(1.0)
        self.slip_spin.setValue(5.0)
        self.slip_spin.setSuffix(" %")
        form.addRow("Slippage σ (% of PnL):", self.slip_spin)

        self.miss_spin = QDoubleSpinBox()
        self.miss_spin.setRange(0.0, 50.0)
        self.miss_spin.setSingleStep(0.5)
        self.miss_spin.setValue(2.0)
        self.miss_spin.setSuffix(" %")
        form.addRow("Missed-fill probability:", self.miss_spin)

        self.seed_spin = QSpinBox()
        self.seed_spin.setRange(0, 2**31 - 1)
        self.seed_spin.setValue(42)
        self.use_seed_cb = QCheckBox("Use deterministic seed")
        self.use_seed_cb.setChecked(True)
        seed_row = QHBoxLayout()
        seed_row.addWidget(self.use_seed_cb)
        seed_row.addWidget(self.seed_spin)
        form.addRow("Seed:", seed_row)

        layout.addWidget(cfg)
        self._mode_changed()

        # Histogram + summary
        self.hist = pg.PlotWidget()
        self.hist.setBackground(QColor("#0D0D0F"))
        self.hist.showGrid(x=True, y=True, alpha=0.12)
        self.hist.setLabel("left", "Frequency", color="#5A5A6E")
        self.hist.setLabel("bottom", "Final equity ($)", color="#5A5A6E")
        self.hist.setMinimumHeight(220)
        layout.addWidget(self.hist)

        self.summary = QTextEdit()
        self.summary.setReadOnly(True)
        from gui.styling import style_helpers as sh
        from gui.styling.theme import get_theme

        self.summary.setStyleSheet(sh.code_text(get_theme()))
        layout.addWidget(self.summary)

        # Buttons
        btn_row = QHBoxLayout()
        self.btn_run = QPushButton("Run")
        self.btn_run.clicked.connect(self._run)
        btn_row.addWidget(self.btn_run)
        self.btn_close = QPushButton("Close")
        self.btn_close.clicked.connect(self.accept)
        btn_row.addWidget(self.btn_close)
        layout.addLayout(btn_row)

    def _mode_changed(self) -> None:
        is_shock = self.mode_combo.currentIndex() == 1
        self.slip_spin.setEnabled(is_shock)
        self.miss_spin.setEnabled(is_shock)

    def _run(self) -> None:
        if not getattr(self.engine, "trades", None):
            self.summary.setText("No trades available for simulation.")
            return
        pnls = np.asarray([t.pnl for t in self.engine.trades], dtype=float)
        mode = "bootstrap" if self.mode_combo.currentIndex() == 0 else "shock"
        seed = self.seed_spin.value() if self.use_seed_cb.isChecked() else None

        self.btn_run.setEnabled(False)
        self.summary.setText(f"Running {mode} MC, {self.iter_spin.value()} iters...")
        self.worker = _MCWorker(
            pnls=pnls,
            initial_equity=float(self.engine.initial_equity),
            mode=mode,
            n_iter=int(self.iter_spin.value()),
            slippage_std_pct=float(self.slip_spin.value()),
            missed_fill_pct=float(self.miss_spin.value()),
            seed=seed,
        )
        self.worker.finished.connect(self._on_finished)
        self.worker.failed.connect(self._on_failed)
        self.worker.start()

    def _on_failed(self, err: str) -> None:
        self.btn_run.setEnabled(True)
        self.summary.setText(f"Simulation failed: {err}")

    def _on_finished(self, res: MCResult) -> None:
        self.btn_run.setEnabled(True)
        self._draw_hist(res.final_equity)
        s = res.summary
        text = (
            f"Iterations:               {s['iterations']:,}\n"
            f"Initial equity:           ${s['initial_equity']:,.2f}\n"
            f"\n--- Final equity ---\n"
            f"Mean:                     ${s['final_equity_mean']:,.2f}\n"
            f"Median:                   ${s['final_equity_median']:,.2f}\n"
            f"5th percentile:           ${s['final_equity_p5']:,.2f}\n"
            f"95th percentile:          ${s['final_equity_p95']:,.2f}\n"
            f"\n--- Max drawdown (%) ---\n"
            f"Mean:                     {s['max_dd_mean']:.2f}%\n"
            f"Median:                   {s['max_dd_median']:.2f}%\n"
            f"5th percentile:           {s['max_dd_p5']:.2f}%\n"
            f"95th percentile:          {s['max_dd_p95']:.2f}%\n"
            f"\n--- Risk indicators ---\n"
            f"P(drawdown > 20%):        {s['prob_drawdown_gt_20pct'] * 100:.2f}%\n"
            f"P(net loss):              {s['prob_loss'] * 100:.2f}%\n"
        )
        self.summary.setText(text)

    def _draw_hist(self, finals: np.ndarray) -> None:
        self.hist.clear()
        if finals.size == 0:
            return
        bins = min(60, max(10, int(np.sqrt(finals.size))))
        y, x = np.histogram(finals, bins=bins)
        bg = pg.BarGraphItem(x0=x[:-1], x1=x[1:], height=y, brush=QColor(0, 229, 255, 150))
        self.hist.addItem(bg)
        # Initial-equity guideline
        line = pg.InfiniteLine(
            pos=float(self.engine.initial_equity),
            angle=90,
            pen=pg.mkPen("#FFAB00", width=2, style=Qt.PenStyle.DashLine),
        )
        self.hist.addItem(line)


__all__ = ["AdvancedMonteCarloDialog"]
