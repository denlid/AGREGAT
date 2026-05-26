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

        # Jump directly to bar `ti` without resetting accumulated data
        # (trades, drawings, equity). TF aggregator is prebuilt —
        # advance_to() is O(1) per TF.
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

        # ─── Headless Visual — завжди, без діалогу ───
        self._run_headless_visual()

    def _run_headless_visual(self) -> None:
        """Запустити on_candle стратегію в фоновому потоці без Qt сигналів.
        
        Використовує engine.run_visual_headless() через ComputeWorker.
        Малюнки збираються, але не відображаються до завершення.
        Прогрес оновлюється кожні 200мс через QTimer (читає current_idx).
        Після завершення — оновлює графік та повідомляє про завершення.
        Кнопка Cancel — кооперативна зупинка через threading.Event.
        """
        import threading
        from gui.workers.workers import ComputeWorker

        total = self.engine.total_bars
        if total <= 0:
            return

        # threading.Event для кооперативного скасування з фонового потоку
        self._headless_cancel_event = threading.Event()

        # Обгортка, яка передає cancel_token в run_visual_headless
        def run_cancellable():
            return self.engine.run_visual_headless(cancel_token=self._headless_cancel_event)

        self.progress_dialog = QProgressDialog(
            "Running headless visual backtest…", "Cancel", 0, total, self.parent()
        )
        self.progress_dialog.setWindowTitle("Headless Run")
        self.progress_dialog.setWindowModality(Qt.WindowModality.WindowModal)
        self.progress_dialog.setMinimumDuration(0)
        self.progress_dialog.setValue(0)

        worker = ComputeWorker(run_cancellable, parent=self)
        worker.finished.connect(self._on_headless_finished)
        worker.error.connect(self._on_quant_error)
        # Cancel: одночасно зупиняємо фоновий потік через Event
        # і запобігаємо emit finished сигналу через worker.cancel()
        self.progress_dialog.canceled.connect(self._headless_cancel_event.set)
        
        self._quant_worker = worker

        # Таймер прогресу
        self._headless_progress_timer = QTimer()
        self._headless_progress_timer.timeout.connect(self._update_headless_progress)
        self._headless_progress_timer.start(200)

        worker.start()

    def _update_headless_progress(self) -> None:
        """Оновити прогрес з current_idx фонового потоку.
        
        QTimer зупиняється якщо діалог вже закритий або прогрес дійшов до кінця.
        """
        if self.progress_dialog is None:
            if self._headless_progress_timer:
                self._headless_progress_timer.stop()
                self._headless_progress_timer = None
            return
        if self.engine.df is None:
            return
        val = min(self.engine.current_idx + 1, self.engine.total_bars)
        self.progress_dialog.setValue(val)
        self.progress_dialog.setLabelText(
            f"Candle {val:,} / {self.engine.total_bars:,}"
        )
        # Якщо дійшли до кінця — зупинити таймер (worker сам закриє діалог)
        if val >= self.engine.total_bars:
            if self._headless_progress_timer:
                self._headless_progress_timer.stop()
                self._headless_progress_timer = None

    def _on_headless_finished(self, result) -> None:
        """Headless завершено — оновити графік та повідомити про завершення."""
        # Зупинити таймер прогресу — обов'язково до close()
        if self._headless_progress_timer:
            try:
                self._headless_progress_timer.stop()
            except Exception:
                pass
            self._headless_progress_timer = None

        # Закрити діалог
        if self.progress_dialog is not None:
            try:
                self.progress_dialog.close()
            except Exception:
                pass
            self.progress_dialog = None

        # Відновити прапорці пригнічення сигналів (були встановлені в run_visual_headless)
        self.engine._suppress_drawings = False
        self.engine._suppress_signals = False

        # Оновити статистику та сповістити про завершення (на головному потоці — безпечно)
        try:
            self.engine.update_stats()
            self.engine._fire(self.engine.on_simulation_finished)
        except Exception:
            pass

        # Re-emit step_completed so chart / status bar update
        if self.engine.current_idx >= 0 and self.engine.total_bars > 0:
            try:
                self.engine.step_completed.emit(
                    self.engine.current_idx,
                    self.engine.candle_at(self.engine.current_idx),
                )
            except Exception:
                pass
            try:
                win = self.window()
                if hasattr(win, 'chart_tab'):
                    win.chart_tab.redraw_chart()
            except Exception:
                pass

        n = len(result.trades) if result is not None else 0
        logger.info("🏁 Headless visual run done — %d trades", n)

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
