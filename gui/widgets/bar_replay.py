"""Bar Replay — Compact play/step/speed controls + Run to End."""

import logging
import time as _time

import numpy as np
import pandas as pd
from PySide6.QtCore import QDate, Qt, QTimer
from PySide6.QtWidgets import (
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QProgressDialog,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger("StrategyTester.gui.bar_replay")


class JumpDialog(QDialog):
    """Small popup for date-jump."""

    def __init__(self, engine, parent=None):
        super().__init__(parent)
        self.engine = engine
        self.setWindowTitle("Jump to Date")
        self.setFixedSize(260, 100)
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        self.date_edit = QDateEdit()
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setDate(QDate.currentDate())
        if engine.df is not None and len(engine.df) > 0:
            f = pd.to_datetime(engine.df.iloc[0]["time"]).date()
            l = pd.to_datetime(engine.df.iloc[-1]["time"]).date()
            self.date_edit.setDateRange(
                QDate(f.year, f.month, f.day), QDate(l.year, l.month, l.day)
            )
        layout.addWidget(self.date_edit)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)


class BarReplayControls(QWidget):
    def __init__(self, engine, step_cb, back_cb=None, parent=None):
        super().__init__(parent)
        self.engine = engine
        self.step_cb = step_cb
        self.back_cb = back_cb

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)

        # ▶ Play/Pause
        self.btn_play = QPushButton("▶")
        self.btn_play.setCheckable(True)
        self.btn_play.setFixedSize(32, 26)
        self.btn_play.setToolTip("Play / Pause  (Space)")
        self.btn_play.clicked.connect(self._on_play)
        layout.addWidget(self.btn_play)

        # Speed (without arrows)
        self.speed_spin = QSpinBox()
        self.speed_spin.setRange(1, 200)
        self.speed_spin.setValue(10)
        self.speed_spin.setSuffix("×")
        self.speed_spin.setFixedWidth(58)
        self.speed_spin.setToolTip("Playback speed")
        self.speed_spin.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        self.speed_spin.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.speed_spin)

        # ⏩ Run to End
        self.btn_run_to_end = QPushButton("⏩")
        self.btn_run_to_end.setObjectName("btnRun")
        self.btn_run_to_end.setFixedSize(68, 26)
        self.btn_run_to_end.setToolTip("Run to End — fast simulation (Ctrl+Alt+R)")
        self.btn_run_to_end.clicked.connect(self.run_to_end)
        layout.addWidget(self.btn_run_to_end)

        # 📅 Jump to date
        self.btn_jump = QPushButton("📅")
        self.btn_jump.setFixedSize(28, 26)
        self.btn_jump.setToolTip("Jump to date")
        self.btn_jump.clicked.connect(self._on_jump)
        layout.addWidget(self.btn_jump)

        # Timer
        self.timer = QTimer()
        self.timer.timeout.connect(self._tick)
        self.engine.data_loaded.connect(lambda _: self._update_state())
        self.engine.simulation_finished.connect(self.stop_playback)

        self._run_to_end_timer = None
        self._run_to_end_batch = 50000
        self._run_to_end_total = 0
        self.progress_dialog = None

        # Initial state
        self._update_state()

    def _update_state(self, total=None):
        ok = self.engine.df is not None
        for btn in [self.btn_play, self.btn_jump, self.btn_run_to_end]:
            btn.setEnabled(ok)

    def _on_play(self, checked):
        if checked:
            self.btn_play.setText("⏸")
            self.timer.start(50)
        else:
            self.btn_play.setText("▶")
            self.timer.stop()

    def _tick(self):
        if self.engine.df is None or self.engine.current_idx + 1 >= len(self.engine.df):
            self.stop_playback()
            return
        s = self.speed_spin.value()
        if s > 1:
            self.engine.step_n(s - 1)
        self.step_cb()

    def _on_jump(self):
        if self.engine.df is None:
            return
        self.stop_playback()
        dlg = JumpDialog(self.engine, self.parent())
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        dt = pd.Timestamp(dlg.date_edit.date().toPython())
        ti = min(
            int(np.searchsorted(self.engine.df["time"].values, np.datetime64(dt), side="left")),
            len(self.engine.df) - 1,
        )
        if ti <= 0:
            return
        logger.info(f"⏭ Jumping to #{ti} ({dt.date()})...")

        # Teleport: reset engine state and jump directly to bar `ti`
        # without replaying every bar through strategy logic.
        # TF aggregator is prebuilt — advance_to() is O(1) per TF.
        self.engine.reset()
        self.engine.current_idx = ti
        self.engine.tf_aggregator.advance_to(ti)

        self.engine.step_completed.emit(
            self.engine.current_idx,
            self.engine.candle_at(self.engine.current_idx),
        )
        self.engine.update_stats()
        logger.info("✅ Jump complete")

    def run_to_end(self):
        if self.engine.df is None or self.engine.strategy is None:
            return
        if self.engine.current_idx >= self.engine.total_bars - 1:
            logger.info("⚠️ Already at end")
            return
        self.stop_playback()

        # Phase 2 — give the user a Visual / Quant choice when the strategy
        # supports the vectorised API. Strategies without
        # ``vectorized_signals(df)`` skip the dialog and run Visual as before
        # so legacy workflows are unaffected.
        from engine.strategy.context import has_vectorized_signals

        if has_vectorized_signals(self.engine.strategy):
            from gui.dialogs.run_mode_dialog import RunModeDialog

            mode = RunModeDialog.pick(self.parent(), has_vectorized=True)
            if mode is None:
                logger.info("Run cancelled by user")
                return
            if mode == "quant":
                self._run_quant()
                return

        total = self.engine.total_bars
        self._run_to_end_batch = 20000  # initial; adapts per tick
        self._run_to_end_total = total

        self.progress_dialog = QProgressDialog(
            "Running simulation...", "Cancel", self.engine.current_idx + 1, total, self.parent()
        )
        self.progress_dialog.setWindowTitle("Fast Run")
        self.progress_dialog.setWindowModality(Qt.WindowModality.WindowModal)
        self.progress_dialog.setMinimumDuration(0)
        self.progress_dialog.setValue(self.engine.current_idx + 1)
        self.progress_dialog.canceled.connect(self._cancel_run_to_end)

        self._run_to_end_timer = QTimer()
        self._run_to_end_timer.timeout.connect(self._run_to_end_tick)
        self._run_to_end_timer.start(0)

    def _run_to_end_tick(self):
        if self.engine.current_idx >= self._run_to_end_total - 1:
            self._finish_run_to_end()
            return
        if self.progress_dialog and self.progress_dialog.wasCanceled():
            self._cancel_run_to_end()
            return
        remaining = self._run_to_end_total - 1 - self.engine.current_idx
        batch = min(self._run_to_end_batch, remaining)
        t0 = _time.perf_counter()
        self.engine.step_n(batch)
        elapsed = _time.perf_counter() - t0
        # Adaptive batch: target ~100ms per tick for smooth UI updates
        if elapsed > 0:
            target_sec = 0.10
            self._run_to_end_batch = max(1000, min(200000, int(batch * target_sec / elapsed)))
        if self.progress_dialog:
            self.progress_dialog.setValue(self.engine.current_idx + 1)
            self.progress_dialog.setLabelText(
                f"Candle {self.engine.current_idx + 1:,} / {self._run_to_end_total:,}"
            )

    def _finish_run_to_end(self):
        if self._run_to_end_timer:
            self._run_to_end_timer.stop()
            self._run_to_end_timer = None
        if self.progress_dialog:
            self.progress_dialog.close()
            self.progress_dialog = None
        if self.engine.current_idx >= 0:
            self.engine.step_completed.emit(
                self.engine.current_idx,
                self.engine.candle_at(self.engine.current_idx),
            )
            self.engine.update_stats()
        logger.info("🏁 Fast run completed!")

    def _cancel_run_to_end(self):
        if self._run_to_end_timer:
            self._run_to_end_timer.stop()
            self._run_to_end_timer = None
        if self.progress_dialog:
            self.progress_dialog = None
        if self.engine.current_idx >= 0:
            self.engine.step_completed.emit(
                self.engine.current_idx,
                self.engine.candle_at(self.engine.current_idx),
            )
            self.engine.update_stats()
        logger.info("⚠️ Fast run cancelled")

    # ------------------------------------------------------------------
    # Phase 2 — Quant (vectorised) run mode
    # ------------------------------------------------------------------
    def _run_quant(self) -> None:
        """Run the loaded strategy through the vectorised engine.

        Heavy NumPy work runs on a :class:`gui.workers.ComputeWorker` so the
        UI remains responsive. A modal progress dialog with a cancel button
        is shown while the worker is in flight; completion auto-jumps the
        main window to the Analytics tab so the user immediately sees the
        results of the run.
        """
        from gui.workers.workers import ComputeWorker

        # Indeterminate progress dialog (busy bar) — Quant runs are typically
        # short so we don't need a per-bar tick.
        self.progress_dialog = QProgressDialog(
            "Running vectorised backtest…", "Cancel", 0, 0, self.parent()
        )
        self.progress_dialog.setWindowTitle("Quant Run")
        self.progress_dialog.setWindowModality(Qt.WindowModality.WindowModal)
        self.progress_dialog.setMinimumDuration(0)

        worker = ComputeWorker(self.engine.run_vectorized, parent=self)
        worker.finished.connect(self._on_quant_finished)
        worker.error.connect(self._on_quant_error)
        self.progress_dialog.canceled.connect(worker.cancel)
        self._quant_worker = worker
        worker.start()

    def _on_quant_finished(self, result) -> None:
        """Quant worker finished — close the spinner and jump to Analytics."""
        if self.progress_dialog:
            self.progress_dialog.close()
            self.progress_dialog = None
        # Re-emit step_completed so chart / status bar update like after Visual.
        if self.engine.current_idx >= 0 and self.engine.total_bars > 0:
            self.engine.step_completed.emit(
                self.engine.current_idx,
                self.engine.candle_at(self.engine.current_idx),
            )
        # Hop to the Analytics tab on the main window if we can find it.
        win = self.window()
        tabs = getattr(win, "tabs", None)
        if tabs is not None:
            for i in range(tabs.count()):
                if tabs.tabText(i).strip().lower() == "analytics":
                    tabs.setCurrentIndex(i)
                    break
        n = len(result.trades) if result is not None else 0
        logger.info("⚡ Quant run done — %d trades in vectorised pass", n)

    def _on_quant_error(self, exc: Exception) -> None:
        if self.progress_dialog:
            self.progress_dialog.close()
            self.progress_dialog = None
        logger.exception("Quant run failed", exc_info=exc)

    def toggle_play(self):
        self.btn_play.setChecked(not self.btn_play.isChecked())
        self._on_play(self.btn_play.isChecked())

    def stop_playback(self):
        if self.timer.isActive():
            self.btn_play.setChecked(False)
            self.btn_play.setText("▶")
            self.timer.stop()
