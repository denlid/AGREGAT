"""Sprint 3.5 — Settings persistence (load / save ``settings.json``).

Reads / writes the on-disk JSON file that backs the GUI's lightweight
settings (last CSV path, last strategy, theme, asset, view range …).
``MainWindow`` keeps thin ``load_settings`` / ``save_settings`` methods
that delegate here.
"""

from __future__ import annotations

import json
import logging
import os
from typing import TYPE_CHECKING, Any, Dict

from PySide6.QtCore import QTimer

from engine.strategy.context import StrategyLoader
from engine.data.data_loader import DataLoader

if TYPE_CHECKING:
    from gui.main_window import MainWindow

logger = logging.getLogger("StrategyTester.gui.settings")


class SettingsController:
    """Owns the GUI's small JSON settings dict + on-disk file."""

    def __init__(self, host: MainWindow, settings_file: str) -> None:
        self._host = host
        self.settings_file = settings_file
        self.settings: Dict[str, Any] = {}

    def load(self) -> None:
        """Restore persisted asset, equity, last CSV/strategy and view range."""
        if not os.path.exists(self.settings_file):
            return
        h = self._host
        try:
            with open(self.settings_file) as f:
                self.settings = json.load(f)
            h.asset_cb.setCurrentText(self.settings.get("asset", "DE40"))
            h.engine.load_asset_config(h.asset_cb.currentText())
            eq = self.settings.get("initial_equity", 100000)
            h.settings_tab.equity_spin.setValue(eq)
            h.engine.initial_equity = float(eq)
            h.engine.equity = float(eq)
            h.engine.max_equity = float(eq)
            self._restore_last_csv()
            self._restore_last_strategy()
            for k, setter in (
                ("timeframe", h.chart_tab.visual_tf_cb.setCurrentText),
                ("chart_mode", h.chart_tab.chart_mode_cb.setCurrentText),
            ):
                if k in self.settings:
                    setter(self.settings[k])
            if "show_markers" in self.settings:
                h.chart_tab.show_markers_cb.setChecked(self.settings["show_markers"])
            cc = self.settings.get("chart_colors")
            if cc:
                h.chart_tab.set_color_scheme(cc)
                h.settings_tab.colors_cb.setCurrentText(cc)
            self._restore_step_position()
            self._restore_view_range()
        except Exception as e:
            logger.warning(f"Settings: {e}")

    def save(self, key: str | None = None, value: Any = None) -> None:
        """Persist a single key (when given) and the live UI snapshot to disk."""
        h = self._host
        if key:
            self.settings[key] = value
        self.settings.update(
            {
                "asset": h.asset_cb.currentText(),
                "initial_equity": h.settings_tab.equity_spin.value(),
                "timeframe": h.chart_tab.visual_tf_cb.currentText(),
                "chart_mode": h.chart_tab.chart_mode_cb.currentText(),
                "show_markers": h.chart_tab.show_markers_cb.isChecked(),
                "current_idx": h.engine.current_idx,
                "view_range": h.chart_tab.plot_widget.getViewBox().viewRange(),
                "chart_colors": h.chart_tab._color_scheme,
            }
        )
        try:
            with open(self.settings_file, "w") as f:
                json.dump(self.settings, f)
        except Exception:
            pass

    # ------------------------------------------------------------------
    def _restore_last_csv(self) -> None:
        h = self._host
        lc = self.settings.get("last_csv")
        if lc and os.path.exists(lc):
            h.engine.load_data(DataLoader.load_csv(lc))

    def _restore_last_strategy(self) -> None:
        h = self._host
        ls = self.settings.get("last_strat")
        if not (ls and os.path.exists(ls)):
            return
        try:
            strat = StrategyLoader.load(ls)
            h.engine.load_strategy(strat)
            if hasattr(h.settings_tab, "rebuild_strategy_form"):
                try:
                    h.settings_tab.rebuild_strategy_form(strat)
                except Exception as e:
                    logger.debug(f"rebuild_strategy_form failed: {e!r}")
        except Exception as e:
            logger.warning(f"Strategy: {e}")

    def _restore_step_position(self) -> None:
        h = self._host
        si = self.settings.get("current_idx", -1)
        if si >= 0 and h.engine.df is not None:
            # Sprint 3.5 — Fast startup cache
            # Attempt to load the pre-calculated session state instead of simulating from scratch
            cache_path = os.path.join(os.path.dirname(self.settings_file), ".auto_save.agbk")
            cache_loaded = False
            
            if os.path.exists(cache_path):
                try:
                    from engine import load_session
                    load_session(h.engine, cache_path)
                    logger.info("⚡ Restored state from fast cache (.auto_save.agbk)")
                    cache_loaded = True
                except Exception as exc:
                    logger.warning(f"Failed to load fast cache, falling back to simulation: {exc}")
            
            # Fall back to simulating everything from zero if cache missing or corrupted
            if not cache_loaded:
                h.engine.step_n(si + 1)
                
            if h.engine.current_idx >= 0:
                h.engine.step_completed.emit(
                    h.engine.current_idx,
                    h.engine.candle_at(h.engine.current_idx),
                )
                h.engine.update_stats()

    def _restore_view_range(self) -> None:
        h = self._host
        vr = self.settings.get("view_range")
        if not (vr and h.engine.current_idx >= 0):
            return

        def restore() -> None:
            try:
                if (vr[0][1] - vr[0][0]) > 1 and (vr[1][1] - vr[1][0]) > 10:
                    h.chart_tab._need_autorange = False
                    h.chart_tab.plot_widget.getViewBox().setRange(
                        xRange=vr[0], yRange=vr[1], padding=0
                    )
            except Exception:
                pass

        QTimer.singleShot(300, restore)
