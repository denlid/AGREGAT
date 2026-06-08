"""Persist and restore last workspace state.

This controller owns startup/session state such as last CSV, last strategy,
chart range, and replay position. User preferences remain in
SettingsController.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any

from gui.persistence import settings_persist

if TYPE_CHECKING:
    from gui.main_window import MainWindow

logger = logging.getLogger("StrategyTester.gui.session_state")


class SessionStateController:
    def __init__(self, host: MainWindow, settings_file: str) -> None:
        self._host = host
        self.settings_file = settings_file
        self.state: dict[str, Any] = {}

    def load(self) -> dict[str, Any]:
        self.state = settings_persist.load_session_state()
        return dict(self.state)

    def save(self, key: str | None = None, value: Any = None) -> None:
        if key:
            self.state[key] = value
        snapshot = self.capture()
        snapshot.update({
            "last_csv": self.state.get("last_csv", snapshot.get("last_csv", "")),
            "last_strat": self.state.get("last_strat", snapshot.get("last_strat", "")),
        })
        settings_persist.save_session_state(snapshot)
        self.state = settings_persist.load_session_state()

    def capture(self) -> dict[str, Any]:
        h = self._host
        snapshot = dict(self.state or settings_persist.load_session_state())
        snapshot.update({
            "asset": h.asset_cb.currentText(),
            "timeframe": h.chart_tab.visual_tf_cb.currentText(),
            "chart_mode": h.chart_tab.chart_mode_cb.currentText(),
            "show_markers": h.chart_tab.show_markers_cb.isChecked(),
            "chart_scheme": getattr(h.chart_tab, "_color_scheme", "Classic"),
        })
        data_loaded = not hasattr(h.engine, "df") or getattr(h.engine, "df", None) is not None
        if data_loaded:
            snapshot.update({
                "current_idx": h.engine.current_idx,
                "view_range": h.chart_tab.plot_widget.getViewBox().viewRange(),
            })
        return snapshot

    def restore(self) -> None:
        logger.info("=== SessionStateController.restore() START ===")
        self.load()
        h = self._host
        try:
            h.asset_cb.setCurrentText(self.state.get("asset", "DE40"))
            h.engine.load_asset_config(h.asset_cb.currentText())

            logger.info("restore last CSV")
            self._restore_last_csv()
            logger.info(
                "after CSV: engine.df is None? %s, current_idx=%s",
                h.engine.df is None,
                h.engine.current_idx,
            )

            logger.info("restore last strategy")
            self._restore_last_strategy()
            logger.info("after strategy: %s", type(h.engine.strategy).__name__)

            for key, setter in (
                ("timeframe", h.chart_tab.visual_tf_cb.setCurrentText),
                ("chart_mode", h.chart_tab.chart_mode_cb.setCurrentText),
            ):
                if key in self.state:
                    setter(self.state[key])
            if "show_markers" in self.state:
                h.chart_tab.show_markers_cb.setChecked(self.state["show_markers"])
            scheme = self.state.get("chart_scheme")
            if scheme:
                h.chart_tab.set_color_scheme(scheme)
                h.settings_tab.colors_cb.setCurrentText(scheme)

            self._restore_step_position()
            logger.info(
                "after restore: current_idx=%s, trades=%s",
                h.engine.current_idx,
                len(h.engine.trades),
            )
            self._restore_view_range()
            logger.info("=== SessionStateController.restore() END ===")
        except Exception as exc:
            logger.warning("Session state restore failed: %s", exc, exc_info=True)

    def _restore_last_csv(self) -> None:
        h = self._host
        last_csv = self.state.get("last_csv")
        if last_csv and os.path.exists(last_csv):
            from engine.data.data_loader import DataLoader

            h.engine.load_data(DataLoader.load_csv(last_csv))

    def _restore_last_strategy(self) -> None:
        h = self._host
        last_strat = self.state.get("last_strat")
        if not (last_strat and os.path.exists(last_strat)):
            return
        if hasattr(h, "strategy_editor_tab"):
            h.strategy_editor_tab.bind_strategy_file(last_strat)
        try:
            from engine.strategy.context import StrategyLoader

            strategy = StrategyLoader.load(last_strat)
            h.engine.load_strategy(strategy)
            rebuild = getattr(h.settings_tab, "rebuild_strategy_form", None)
            if callable(rebuild):
                rebuild(strategy)
        except Exception as exc:
            logger.warning("Strategy restore failed: %s", exc)

    def _restore_step_position(self) -> None:
        h = self._host
        if h.engine.df is None:
            return

        cache_path = os.path.join(
            os.path.dirname(self.settings_file), ".auto_save.agbk"
        )
        cache_loaded = False
        if os.path.exists(cache_path):
            try:
                from engine import load_session

                load_session(h.engine, cache_path)
                logger.info("Restored state from fast cache (.auto_save.agbk)")
                cache_loaded = True
            except Exception as exc:
                logger.warning("Fast cache restore failed: %s", exc)

        if not cache_loaded:
            saved_idx = self.state.get("current_idx", -1)
            if saved_idx >= 0:
                h.engine.step_n(saved_idx + 1)
            else:
                h.engine.step_n(1)
        elif h.engine.current_idx < 0:
            h.engine.step_n(1)

        if h.engine.current_idx >= 0:
            h.engine.step_completed.emit(
                h.engine.current_idx,
                h.engine.candle_at(h.engine.current_idx),
            )
            h.engine.update_stats()
            from PySide6.QtCore import QTimer

            QTimer.singleShot(100, h.chart_tab.redraw_chart)

    def _restore_view_range(self) -> None:
        h = self._host
        view_range = self.state.get("view_range")
        if not (view_range and h.engine.current_idx >= 0):
            return

        def restore() -> None:
            try:
                if (
                    (view_range[0][1] - view_range[0][0]) > 1
                    and (view_range[1][1] - view_range[1][0]) > 10
                ):
                    h.chart_tab._need_autorange = False
                    h.chart_tab.plot_widget.getViewBox().setRange(
                        xRange=view_range[0],
                        yRange=view_range[1],
                        padding=0,
                    )
            except Exception:
                pass

        from PySide6.QtCore import QTimer

        QTimer.singleShot(300, restore)
