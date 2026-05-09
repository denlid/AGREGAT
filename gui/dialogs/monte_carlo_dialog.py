"""
Monte Carlo Dialog — Customizable Monte Carlo simulation with progress bar.
Optimized with NumPy vectorization and parallel processing.
"""

import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
)

logger = logging.getLogger("StrategyTester.gui.monte_carlo_dialog")


def _run_single_simulation(pnls, initial_equity):
    """Single Monte Carlo iteration (for parallel execution)."""
    shuffled = np.array(pnls)
    np.random.shuffle(shuffled)

    equity_curve = initial_equity + np.cumsum(shuffled)
    peak_curve = np.maximum.accumulate(equity_curve)

    # Max drawdown
    dd_curve = (peak_curve - equity_curve) / peak_curve * 100
    max_dd = np.max(dd_curve) if len(dd_curve) > 0 else 0

    # Final return
    final_equity = equity_curve[-1] if len(equity_curve) > 0 else initial_equity
    final_return = (final_equity - initial_equity) / initial_equity * 100

    return max_dd, final_return


class MonteCarloWorker(QThread):
    """Worker thread for Monte Carlo simulation with parallel processing."""

    progress = Signal(int)
    finished = Signal(dict)

    def __init__(self, trades, initial_equity, iterations, use_parallel=True):
        super().__init__()
        self.trades = trades
        self.initial_equity = initial_equity
        self.iterations = iterations
        self.use_parallel = use_parallel
        self._cancel_event = threading.Event()  # Thread-safe cancellation flag

    def cancel(self):
        self._cancel_event.set()

    def run(self):
        pnls = [t.pnl for t in self.trades]

        if self.use_parallel and self.iterations >= 1000:
            # Parallel execution for large iterations
            self._run_parallel(pnls)
        else:
            # Sequential execution for small iterations
            self._run_sequential(pnls)

    def _run_sequential(self, pnls):
        """Sequential Monte Carlo (for small iterations or debugging)."""
        worst_dds = []
        best_returns = []

        for i in range(self.iterations):
            if self._cancel_event.is_set():
                return

            if i % 100 == 0:
                self.progress.emit(i)

            max_dd, final_return = _run_single_simulation(pnls, self.initial_equity)
            worst_dds.append(max_dd)
            best_returns.append(final_return)

        if not self._cancel_event.is_set():
            self._emit_results(worst_dds, best_returns)

    def _run_parallel(self, pnls):
        """Parallel Monte Carlo using ThreadPoolExecutor."""
        import os

        max_workers = min(os.cpu_count() or 4, 8)  # Limit to 8 threads

        worst_dds = []
        best_returns = []
        completed = 0

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Submit all tasks
            futures = [
                executor.submit(_run_single_simulation, pnls, self.initial_equity)
                for _ in range(self.iterations)
            ]

            # Collect results as they complete
            for future in as_completed(futures):
                if self._cancel_event.is_set():
                    executor.shutdown(wait=False, cancel_futures=True)
                    return

                try:
                    max_dd, final_return = future.result()
                    worst_dds.append(max_dd)
                    best_returns.append(final_return)

                    completed += 1
                    if completed % 100 == 0:
                        self.progress.emit(completed)
                except Exception as e:
                    logger.error(f"Monte Carlo iteration failed: {e}")

        if not self._cancel_event.is_set():
            self._emit_results(worst_dds, best_returns)

    def _emit_results(self, worst_dds, best_returns):
        """Calculate and emit final results."""
        self.progress.emit(self.iterations)

        worst_dds = np.array(worst_dds)
        best_returns = np.array(best_returns)

        results = {
            "avg_dd": float(np.mean(worst_dds)),
            "max_dd": float(np.max(worst_dds)),
            "min_dd": float(np.min(worst_dds)),
            "median_dd": float(np.median(worst_dds)),
            "p95_dd": float(np.percentile(worst_dds, 95)),
            "p99_dd": float(np.percentile(worst_dds, 99)),
            "avg_return": float(np.mean(best_returns)),
            "best_return": float(np.max(best_returns)),
            "worst_return": float(np.min(best_returns)),
            "median_return": float(np.median(best_returns)),
            "p5_return": float(np.percentile(best_returns, 5)),
            "p95_return": float(np.percentile(best_returns, 95)),
            "prob_profit": float(np.sum(best_returns > 0) / len(best_returns) * 100),
            "worst_dds": worst_dds.tolist(),
            "returns": best_returns.tolist(),
        }
        self.finished.emit(results)


class MonteCarloDialog(QDialog):
    """Dialog for running customizable Monte Carlo simulations."""

    def __init__(self, engine, parent=None):
        super().__init__(parent)
        self.engine = engine
        self.worker = None
        self.setWindowTitle("Monte Carlo Simulation")
        self.resize(650, 550)

        layout = QVBoxLayout(self)

        # Settings
        settings_group = QGroupBox("Simulation Settings")
        settings_layout = QFormLayout(settings_group)

        self.iterations_spin = QSpinBox()
        self.iterations_spin.setRange(100, 100000)
        self.iterations_spin.setValue(10000)
        self.iterations_spin.setSingleStep(1000)
        settings_layout.addRow("Iterations:", self.iterations_spin)

        self.parallel_cb = QCheckBox("Use parallel processing (faster)")
        self.parallel_cb.setChecked(True)
        self.parallel_cb.setToolTip("Use multiple CPU cores for faster simulation")
        settings_layout.addRow("", self.parallel_cb)

        layout.addWidget(settings_group)

        # Progress
        self.progress_bar = QProgressBar()
        self.progress_bar.setTextVisible(True)
        layout.addWidget(self.progress_bar)

        # Results
        self.results_text = QTextEdit()
        self.results_text.setReadOnly(True)
        from gui.styling import style_helpers as sh
        from gui.styling.theme import get_theme

        self.results_text.setStyleSheet(sh.code_text(get_theme()))
        layout.addWidget(self.results_text)

        # Buttons
        btn_layout = QHBoxLayout()
        self.btn_run = QPushButton("🎲 Run Simulation")
        self.btn_run.clicked.connect(self.run_simulation)
        btn_layout.addWidget(self.btn_run)

        self.btn_cancel = QPushButton("❌ Cancel")
        self.btn_cancel.clicked.connect(self.cancel_simulation)
        self.btn_cancel.setEnabled(False)
        btn_layout.addWidget(self.btn_cancel)

        self.btn_close = QPushButton("Close")
        self.btn_close.clicked.connect(self.accept)
        btn_layout.addWidget(self.btn_close)

        layout.addLayout(btn_layout)

    def run_simulation(self):
        if not self.engine.trades:
            self.results_text.setText("❌ No trades available for simulation.")
            return

        iterations = self.iterations_spin.value()
        use_parallel = self.parallel_cb.isChecked()

        self.progress_bar.setMaximum(iterations)
        self.progress_bar.setValue(0)
        self.btn_run.setEnabled(False)
        self.btn_cancel.setEnabled(True)

        mode = "parallel" if use_parallel else "sequential"
        self.results_text.setText(
            f"🎲 Running Monte Carlo simulation ({mode} mode)...\n{iterations:,} iterations\n"
        )

        self.worker = MonteCarloWorker(
            self.engine.trades, self.engine.initial_equity, iterations, use_parallel
        )
        self.worker.progress.connect(self.on_progress)
        self.worker.finished.connect(self.on_finished)
        self.worker.start()

    def cancel_simulation(self):
        if self.worker:
            self.worker.cancel()
            self.results_text.append("\n⚠️ Simulation cancelled by user.")
            self.btn_run.setEnabled(True)
            self.btn_cancel.setEnabled(False)

    def on_progress(self, value):
        self.progress_bar.setValue(value)

    def on_finished(self, results):
        self.btn_run.setEnabled(True)
        self.btn_cancel.setEnabled(False)

        # Format results
        text = f"""
✅ Monte Carlo Simulation Complete!
{"=" * 60}

📉 DRAWDOWN ANALYSIS
{"─" * 60}
Average Max DD:        {results["avg_dd"]:.2f}%
Median Max DD:         {results["median_dd"]:.2f}%
Best Case DD:          {results["min_dd"]:.2f}%
Worst Case DD:         {results["max_dd"]:.2f}%
95th Percentile DD:    {results["p95_dd"]:.2f}%
99th Percentile DD:    {results["p99_dd"]:.2f}%

📈 RETURN DISTRIBUTION
{"─" * 60}
Average Return:        {results["avg_return"]:+.2f}%
Median Return:         {results["median_return"]:+.2f}%
Best Case Return:      {results["best_return"]:+.2f}%
Worst Case Return:     {results["worst_return"]:+.2f}%
5th Percentile:        {results["p5_return"]:+.2f}%
95th Percentile:       {results["p95_return"]:+.2f}%

🎯 PROBABILITY METRICS
{"─" * 60}
Probability of Profit: {results["prob_profit"]:.1f}%

💡 INTERPRETATION
{"─" * 60}
• 50% of simulations had DD ≤ {results["median_dd"]:.1f}%
• 95% of simulations had DD ≤ {results["p95_dd"]:.1f}%
• 95% of simulations had returns between {results["p5_return"]:+.1f}% and {results["p95_return"]:+.1f}%
• In worst case scenario, DD reached {results["max_dd"]:.1f}%
• In best case scenario, return reached {results["best_return"]:+.1f}%

{"=" * 60}
"""
        self.results_text.setText(text)
        logger.info(
            f"✅ Monte Carlo simulation complete: {self.iterations_spin.value():,} iterations"
        )
