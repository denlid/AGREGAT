"""Sprint 3.5 — Session persistence (save / load `.agbk`) + data / strategy loaders.

Owns every "open the file system" interaction the chart toolbar can
trigger: load CSV (async), load strategy, save / load `.agbk` session.
``MainWindow`` keeps thin public entry points (``on_load_data``,
``on_save_session`` …) that simply delegate here.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFileDialog, QMessageBox, QProgressDialog

if TYPE_CHECKING:
    from gui.main_window import MainWindow
    from gui.workers.workers import ComputeWorker

logger = logging.getLogger("StrategyTester.gui.session")


class SessionLifecycleController:
    """Owns the file dialogs and async loader for the chart toolbar."""

    def __init__(self, host: MainWindow) -> None:
        self._host = host
        # Async-load state for ``load_data`` so the GUI thread never sits
        # on a multi-second ``pd.read_csv`` call. Lifecycle: set when a
        # load is in flight, cleared in the finished/error/cancelled
        # handlers. Only one in-flight load at a time.
        self._load_worker: Optional[ComputeWorker] = None
        self._load_progress: Optional[QProgressDialog] = None

    # ------------------------------------------------------------------
    # Save / load .agbk
    # ------------------------------------------------------------------
    def save_session(self) -> None:
        from engine import AGBK_EXTENSION, save_session

        h = self._host
        if not h.engine.trades:
            QMessageBox.information(h, "No trades", "Run a simulation before saving a session.")
            return
        fp, _ = QFileDialog.getSaveFileName(
            h, "Save Session", "", f"AGREGAT Backtest (*{AGBK_EXTENSION})"
        )
        if not fp:
            return
        try:
            save_session(h.engine, fp)
            logger.info(f"💾 Session saved: {fp}")
            h.lbl_info.setText("💾 Session saved")
        except Exception as e:
            logger.error(f"Save failed: {e!r}", exc_info=True)
            h.show_error(str(e))

    def load_session(self) -> None:
        from engine import AGBK_EXTENSION, load_session

        h = self._host
        fp, _ = QFileDialog.getOpenFileName(
            h, "Load Session", "", f"AGREGAT Backtest (*{AGBK_EXTENSION})"
        )
        if not fp:
            return
        try:
            meta = load_session(h.engine, fp)
            logger.info(f"📂 Session loaded: {fp} ({meta.get('trade_count', '?')} trades)")
            h.lbl_info.setText("📂 Session loaded")
            try:
                h.engine.update_stats()
            except Exception:
                pass
            h.engine.position_changed.emit()
        except Exception as e:
            logger.error(f"Load failed: {e!r}", exc_info=True)
            h.show_error(str(e))

    # ------------------------------------------------------------------
    # Strategy loader
    # ------------------------------------------------------------------
    def create_strategy(self) -> None:
        from engine.strategy.context import StrategyLoader

        h = self._host
        default_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "strategies")
        default_path = os.path.join(default_dir, "new_strategy.py")

        fp, _ = QFileDialog.getSaveFileName(h, "New Strategy", default_path, "Python (*.py)")
        if not fp:
            return

        template = """from engine.api import Strategy
import numpy as np

strategy = Strategy(
    name="New Strategy",
    risk=1.0
)

@strategy.on_candle()
def _(c):
    pass
"""
        try:
            with open(fp, "w", encoding="utf-8") as f:
                f.write(template)
        except Exception as e:
            logger.error(f"❌ Failed to create strategy file: {e!r}", exc_info=True)
            h.show_error(f"Failed to create file: {e}")
            return

        # Load file into editor FIRST
        if hasattr(h, "strategy_editor_tab"):
            h.strategy_editor_tab.bind_strategy_file(fp)
            if hasattr(h, "tabs"):
                h.tabs.setCurrentWidget(h.strategy_editor_tab)

        try:
            logger.info(f"📜 Loading new strategy from: {fp}")
            strategy = StrategyLoader.load(fp)
            h.engine.load_strategy(strategy)
            logger.info(f"✅ Strategy loaded: {strategy.__class__.__name__}")
            h._settings_ctrl.save("last_strat", fp)
            if hasattr(h, "settings_tab") and hasattr(h.settings_tab, "rebuild_strategy_form"):
                try:
                    h.settings_tab.rebuild_strategy_form(strategy)
                except Exception as e:
                    logger.debug(f"rebuild_strategy_form failed: {e!r}")
        except Exception as e:
            logger.error(f"❌ Strategy load failed: {e!r}", exc_info=True)
            h.show_error(str(e))

    def load_strategy(self) -> None:
        from engine.strategy.context import StrategyLoader

        h = self._host
        fp, _ = QFileDialog.getOpenFileName(h, "Load Strategy", "", "Python (*.py)")
        if not fp:
            return
        # Load file into editor FIRST, regardless of compilation errors
        if hasattr(h, "strategy_editor_tab"):
            h.strategy_editor_tab.bind_strategy_file(fp)
        try:
            logger.info(f"📜 Loading strategy from: {fp}")
            strategy = StrategyLoader.load(fp)
            h.engine.load_strategy(strategy)
            logger.info(f"✅ Strategy loaded: {strategy.__class__.__name__}")
            h._settings_ctrl.save("last_strat", fp)
            if hasattr(h, "settings_tab") and hasattr(h.settings_tab, "rebuild_strategy_form"):
                try:
                    h.settings_tab.rebuild_strategy_form(strategy)
                except Exception as e:
                    logger.debug(f"rebuild_strategy_form failed: {e!r}")
        except Exception as e:
            logger.error(f"❌ Strategy load failed: {e!r}", exc_info=True)
            h.show_error(str(e))

    # ------------------------------------------------------------------
    # CSV data loader (async, TZ Phase 2b)
    # ------------------------------------------------------------------
    def load_data(self) -> None:
        h = self._host
        fps, _ = QFileDialog.getOpenFileNames(h, "Load Data", "", "Data Files (*.csv *.parquet *.pq *.feather *.ipc)")
        if not fps:
            return
        
        # Phase 4 DataPager autowire:
        try:
            from engine.data.data_pager import make_pager
            pager = make_pager(fps)
            if pager is not None:
                h.chart_tab.attach_data_pager(pager)
                # The DataPager handles async loading on demand.
                # We can just request the first tile's bounds.
                tiles = pager.list_tiles()
                if tiles:
                    # Let's ensure the first tile is loaded right away
                    # We can't know bounds if bounds_from_disk=False, so just request it.
                    # Pager handles it.
                    import pandas as pd
                    pager.ensure_visible(pd.Timestamp("1900-01-01"), pd.Timestamp("2100-01-01"))
                return
        except Exception as e:
            logger.debug(f"DataPager creation skipped or failed: {e}")

        # Fallback to legacy single file
        self.start_async_csv_load(fps[0], save_last=True)

    def start_async_csv_load(self, fp: str, save_last: bool) -> None:
        """Run ``DataLoader.load_csv`` on a background ``QThread`` so the
        GUI thread doesn't freeze on multi-second ``pd.read_csv`` calls.
        """
        from engine.data.data_loader import DataLoader
        from gui.workers.workers import ComputeWorker

        h = self._host
        if self._load_worker is not None:
            logger.info("Load already in flight — ignoring new request")
            return

        h.lbl_info.setText("⏳ Loading data…")
        h.lbl_info.repaint()
        logger.info(f"📂 Loading data from: {fp}")

        progress = QProgressDialog(
            f"Loading {os.path.basename(fp)}…",
            "Cancel",
            0,
            0,
            h,
        )
        progress.setWindowTitle("Load CSV")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.setValue(0)
        self._load_progress = progress

        worker = ComputeWorker(DataLoader.load_csv, fp)
        worker.finished.connect(
            lambda df, _fp=fp, _save=save_last: self._on_csv_loaded(df, _fp, _save)
        )
        worker.error.connect(self._on_csv_load_error)
        progress.canceled.connect(worker.cancel)
        self._load_worker = worker
        worker.start()

    def _close_load_progress(self) -> None:
        if self._load_progress is not None:
            self._load_progress.close()
            self._load_progress = None
        self._load_worker = None

    def _on_csv_loaded(self, df, fp: str, save_last: bool) -> None:
        # Honour a late cancel — the worker had already finished but the
        # user clicked cancel before this slot ran.
        h = self._host
        worker = self._load_worker
        cancelled = worker is not None and worker.is_cancelled()
        self._close_load_progress()
        if cancelled:
            h.lbl_info.setText("⚠️ Load cancelled")
            return
        try:
            logger.info(
                f"✅ Data loaded: {len(df):,} candles | "
                f"Timespan: {df['time'].iloc[0]} → {df['time'].iloc[-1]}"
            )
            h.engine.load_data(df)
            # Sprint 2.3 — record the input file hash on the engine
            # so it ends up in ``meta.json`` when the session is saved.
            csv_sha: Optional[str] = None
            try:
                h.engine.set_input_hash(csv_path=fp)
                csv_sha = getattr(h.engine, "input_csv_sha256", None)
            except Exception:
                logger.warning("Could not hash input CSV for meta.json", exc_info=True)
            h.lbl_info.setText(f"✅ {os.path.basename(fp)} ({len(df):,} candles)")
            if csv_sha:
                h.lbl_info.setToolTip(f"File: {fp}\nSHA-256: {csv_sha}\nCandles: {len(df):,}")
            if save_last:
                h._settings_ctrl.save("last_csv", fp)
                # Phase 2.6 (C9) — push to the MRU list so the
                # File → Recent menu picks it up next launch.
                try:
                    from gui.persistence import recent_files

                    recent_files.push(fp)
                except Exception:
                    logger.debug("recent_files.push failed", exc_info=True)
            # Phase 2.4 (C4) — auto-select the asset when the
            # filename matches a registered symbol. Only fires if
            # the user hasn't manually picked something other than
            # the previous default.
            try:
                from engine.asset_registry import detect_asset_from_filename

                detected = detect_asset_from_filename(fp)
                if detected and detected != h.asset_cb.currentText():
                    h.asset_cb.setCurrentText(detected)
            except Exception:
                logger.debug("asset auto-detect failed", exc_info=True)
        except Exception as e:
            logger.error(f"❌ Engine load failed: {e}", exc_info=True)
            h.show_error(str(e))
            h.lbl_info.setText("❌ Load failed")

    def _on_csv_load_error(self, exc: Exception) -> None:
        h = self._host
        self._close_load_progress()
        logger.error(f"❌ Data load failed: {exc}", exc_info=exc)
        h.show_error(str(exc))
        h.lbl_info.setText("❌ Load failed")
