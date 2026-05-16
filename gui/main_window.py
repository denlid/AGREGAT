"""Main Window — AGREGAT v7.78. Premium trading platform UI.

Sprint 3.5 refactor — UI construction is owned by controllers under
``gui/controllers/``. ``MainWindow`` is a thin orchestrator: it
instantiates the engine, tabs and controllers, wires engine signals to
controller methods, and registers keyboard shortcuts. Controllers
reference each other directly through host attributes
(``h._session_ctrl``, ``h._replay_ctrl`` …) so the host doesn't need
boilerplate delegation methods. A small set of public method shims
(``on_load_data``, ``on_save_session`` …) is kept for back-compat with
existing tests / external callers.
"""

from __future__ import annotations

import logging
import os
import json

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QMainWindow,
    QMessageBox,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from engine.backtest_engine import BacktestEngine
from engine.strategy.context import Strategy
from gui.tabs.analytics_tab import AnalyticsTab
from gui.chart_tab import ChartTab
from gui.controllers.asset_controller import AssetController
from gui.controllers.menu_controller import MenuController
from gui.controllers.replay_controller import ReplayController
from gui.controllers.session_lifecycle_controller import SessionLifecycleController
from gui.controllers.settings_controller import SettingsController
from gui.controllers.status_bar_controller import StatusBarController
from gui.controllers.theme_controller import ThemeController
from gui.controllers.toolbar_controller import ToolbarController
from gui.tabs.log_console import LogConsole
from gui.tabs.quant_tab import QuantTab
from gui.tabs.settings_tab import SettingsTab
from gui.tabs.stats_tab import StatsTab
from gui.styling.theme import register_theme_listener
from gui.tabs.trades_tab import TradesTab
from gui.tabs.strategy_editor_tab import StrategyEditorTab
from gui.widgets.ai_editor_web import AIAssistantEditor
from gui.workers.ai_worker import AIAssistantWorker

logger = logging.getLogger("StrategyTester.gui.main_window")
_APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SETTINGS_FILE = os.path.join(_APP_DIR, "saves", "settings.json")


class _Empty(Strategy):
    def on_candle(self, ctx):  # type: ignore[no-untyped-def]
        pass


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("AGREGAT")
        self.resize(1140, 720)
        self.setMinimumSize(1100, 600)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Window
            | Qt.WindowType.WindowMinimizeButtonHint
            | Qt.WindowType.WindowMaximizeButtonHint
        )
        self.setStyleSheet("QMenuBar { border: none; }")

        self.engine = BacktestEngine()
        self.engine.error_occurred.connect(self.show_error)
        self.engine.load_strategy(_Empty())

        self.chart_tab = ChartTab(self.engine)
        self.chart_tab_2: ChartTab | None = None
        self.stats_tab = StatsTab(self.engine)
        self.trades_tab = TradesTab(self.engine)
        self.analytics_tab = AnalyticsTab(self.engine)
        self.quant_tab = QuantTab(self.engine)
        self.log_console = LogConsole()
        self.strategy_editor_tab = StrategyEditorTab(self.engine, self)
        self.settings_tab = SettingsTab(self.engine, parent_window=self)
        self.global_ai_panel = AIAssistantEditor(self)
        self.global_ai_panel.setMinimumWidth(360)
        self.global_ai_panel.bridge.ready.connect(self._on_global_ai_panel_ready)
        self.global_ai_panel.hide()
        self._global_ai_worker = None
        
        # Wire mirror panels for shared chat history
        self.global_ai_panel.set_mirror_panel(self.strategy_editor_tab.editor)
        self.strategy_editor_tab.editor.set_mirror_panel(self.global_ai_panel)
        # Cross-panel chat tabs sync
        self.global_ai_panel.bridge.chat_tabs_state_changed.connect(self._on_chart_ai_tabs_changed)
        self.strategy_editor_tab.editor.bridge.chat_tabs_state_changed.connect(self._on_editor_tabs_changed)
        self._chart_selection_context = None
        self._chat_tabs_state: str = ""  # last known full state JSON for disk persistence
        self._chat_tabs_file = os.path.join(_APP_DIR, "saves", "chat_tabs.json")

        # Asset / session / replay first — toolbar build references them.
        self._asset_ctrl = AssetController(self)
        self._session_ctrl = SessionLifecycleController(self)
        self._replay_ctrl = ReplayController(self)
        self._settings_ctrl = SettingsController(self, SETTINGS_FILE)
        self.settings = self._settings_ctrl.settings
        self.settings_file = SETTINGS_FILE
        self._menu_ctrl = MenuController(self)
        self._toolbar_ctrl = ToolbarController(self)
        self._status_bar_ctrl = StatusBarController(self)
        self._theme_ctrl = ThemeController(self)

        self._build_layout()
        self._wire_signals()
        self._wire_shortcuts()
        self._restore_persisted_settings_tab()
        self.load_settings()

        register_theme_listener(self._theme_ctrl.apply_theme)
        logger.info(
            "AGREGAT v7.78 | Shift+Arrows=Step | Space=Play | Scroll=ZoomX | Shift+Scroll=ZoomY"
        )

    # -- construction ---------------------------------------------------
    def _build_layout(self) -> None:
        from gui.widgets.custom_title_bar import CustomTitleBar
        from gui.widgets.window_resizer import AdvancedResizer

        main_container = QWidget()
        main_container.setObjectName("main_container")
        self.setCentralWidget(main_container)
        main_vbox = QVBoxLayout(main_container)
        main_vbox.setContentsMargins(0, 0, 0, 0)
        main_vbox.setSpacing(0)

        self.custom_title_bar = CustomTitleBar(self, "AGREGAT", self._menu_ctrl.menu_bar)
        main_vbox.addWidget(self.custom_title_bar)

        central = QWidget()
        main_vbox.addWidget(central, stretch=1)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.tabs = QTabWidget()
        self.tabs.setTabPosition(QTabWidget.TabPosition.West)
        self.tabs.setDocumentMode(True)

        chart_page = QWidget()
        cp = QVBoxLayout(chart_page)
        cp.setContentsMargins(4, 2, 4, 0)
        cp.setSpacing(3)
        cp.addWidget(self._toolbar_ctrl.build_toolbar_widget())
        self.chart_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.chart_splitter.addWidget(self.chart_tab)
        self.chart_splitter.addWidget(self.global_ai_panel)
        self.chart_splitter.setSizes([920, 0])
        cp.addWidget(self.chart_splitter, stretch=1)

        for w, name in (
            (chart_page, "Chart"),
            (self.stats_tab, "Stats"),
            (self.trades_tab, "Trades"),
            (self.analytics_tab, "Analytics"),
            (self.quant_tab, "Quant"),
            (self.log_console, "Logs"),
            (self.strategy_editor_tab, "Editor"),
            (self.settings_tab, "Settings"),
        ):
            self.tabs.addTab(w, name)
        self.tabs.currentChanged.connect(self._on_tab)
        root.addWidget(self.tabs)

        self._strategy_editor_tab_index = self.tabs.indexOf(self.strategy_editor_tab)
        self._prev_tab_idx = self.tabs.currentIndex()

        self.setStatusBar(self._status_bar_ctrl.status_bar)
        self._advanced_resizer = AdvancedResizer(self, margin=6)

    def _wire_signals(self) -> None:
        e = self.engine
        e.step_completed.connect(self._replay_ctrl.on_step)
        e.stats_updated.connect(self._status_bar_ctrl.on_stats)
        e.data_loaded.connect(self._replay_ctrl.on_data_loaded)
        e.position_changed.connect(self.chart_tab.on_position_changed)
        e.simulation_finished.connect(self._replay_ctrl.on_simulation_finished)
        self.global_ai_panel.bridge.chat_requested_ext.connect(self._on_global_ai_chat_requested)
        self.global_ai_panel.bridge.stop_ai_requested.connect(self._on_global_ai_stop_requested)
        self.global_ai_panel.bridge.chart_selection_requested.connect(self._on_chart_selection_requested)
        self.global_ai_panel.bridge.attachment_requested.connect(self._on_ai_attachment_requested)
        self.chart_tab.sigAreaSelected.connect(self._on_chart_area_selected)

    def _is_editor_tab_active(self) -> bool:
        """Check if the Editor tab or AI panel has focus, blocking chart shortcuts."""
        current_idx = self.tabs.currentIndex()
        # 6 = Editor tab
        if current_idx == 6:
            return True
        # Also check if AI panel is visible and might have focus
        if self.global_ai_panel.isVisible():
            return True
        return False

    def _on_save_ctrl_s(self) -> None:
        """Context-aware Ctrl+S: save strategy if in Editor, otherwise save session."""
        if self._is_editor_tab_active():
            # Trigger save via the strategy editor using existing saveStrategy() function
            self.strategy_editor_tab.editor.page().runJavaScript("saveStrategy();")
        else:
            # Save session as usual
            self._session_ctrl.save_session()

    def _wire_shortcuts(self) -> None:
        sc = lambda k, fn: QShortcut(QKeySequence(k), self, fn)  # noqa: E731
        rc, mc = self._replay_ctrl, self._menu_ctrl
        
        # Wrap chart-related shortcuts to check if editor is active
        sc(Qt.Key.Key_Right, lambda: not self._is_editor_tab_active() and self.chart_tab.scroll_chart(1))
        sc(Qt.Key.Key_Left, lambda: not self._is_editor_tab_active() and self.chart_tab.scroll_chart(-1))
        sc("Shift+Right", lambda: not self._is_editor_tab_active() and rc.step_visual_candle())
        sc("Shift+Left", lambda: not self._is_editor_tab_active() and rc.step_back_visual_candle())
        sc(Qt.Key.Key_Space, lambda: not self._is_editor_tab_active() and self.bar_replay.toggle_play())
        sc(Qt.Key.Key_Home, lambda: not self._is_editor_tab_active() and self.chart_tab._fit_all())
        sc(Qt.Key.Key_Escape, lambda: not self._is_editor_tab_active() and self.chart_tab._set_tool("None"))
        sc("Ctrl+=", lambda: not self._is_editor_tab_active() and self.chart_tab.zoom_chart(1.15))
        sc("Ctrl+-", lambda: not self._is_editor_tab_active() and self.chart_tab.zoom_chart(1 / 1.15))
        sc("Ctrl+Shift+=", lambda: not self._is_editor_tab_active() and self._zoom_y(1.15))
        sc("Ctrl+Shift+-", lambda: not self._is_editor_tab_active() and self._zoom_y(1 / 1.15))
        sc("Ctrl+Z", lambda: not self._is_editor_tab_active() and rc.undo_drawing())
        sc("Ctrl+Shift+Z", lambda: not self._is_editor_tab_active() and rc.redo_drawing())
        sc("Ctrl+Alt+R", lambda: not self._is_editor_tab_active() and self.bar_replay.run_to_end())
        # These can be used in editor too, but still check
        sc("Ctrl+/", mc.show_cheat_sheet)
        sc("Ctrl+Shift+P", mc.show_command_palette)
        # Ctrl+S: Save Strategy (Editor) OR Save Session (Chart)
        sc("Ctrl+S", self._on_save_ctrl_s)

    def _restore_persisted_settings_tab(self) -> None:
        try:
            from gui.persistence import settings_persist

            self._persisted_settings = settings_persist.load()
            self.settings_tab.apply_persisted(self._persisted_settings)
        except Exception:
            pass

    # -- back-compat public shims (referenced by tests / external code) -
    def on_load_data(self) -> None:
        self._session_ctrl.load_data()

    def on_load_strategy(self) -> None:
        self._session_ctrl.load_strategy()

    def on_save_session(self) -> None:
        self._session_ctrl.save_session()

    def on_load_session(self) -> None:
        self._session_ctrl.load_session()

    def on_reset(self) -> None:
        self._replay_ctrl.on_reset()

    def step_visual_candle(self) -> None:
        self._replay_ctrl.step_visual_candle()

    def step_back_visual_candle(self) -> None:
        self._replay_ctrl.step_back_visual_candle()

    def load_settings(self) -> None:
        self._settings_ctrl.load()
        self.settings = self._settings_ctrl.settings

    def save_settings(self, key=None, value=None) -> None:  # type: ignore[no-untyped-def]
        self._settings_ctrl.save(key, value)

    def _start_async_csv_load(self, fp: str, save_last: bool) -> None:
        self._session_ctrl.start_async_csv_load(fp, save_last)

    def toggle_dual_view(self) -> None:
        self._toolbar_ctrl.toggle_dual_view()

    def _jump_to_end(self) -> None:
        self._toolbar_ctrl.jump_to_end()

    # -- small cross-cutting helpers ----------------------------------
    def _zoom_y(self, factor: float) -> None:
        vb = self.chart_tab.plot_widget.getViewBox()
        vb.scaleBy((1.0, 1.0 / factor))

    def _on_global_ai_panel_ready(self) -> None:
        self.global_ai_panel.set_chat_only_mode(True)
        self.global_ai_panel.set_strategy_header_title("AI assistant")
        # Push persisted chat tabs to the chart AI panel
        saved = self._load_chat_tabs_from_disk()
        if saved:
            self.global_ai_panel.apply_chat_tabs_state(saved)

    def _on_tab(self, idx: int) -> None:
        prev = self._prev_tab_idx
        self._prev_tab_idx = idx
        # Leaving Editor → Chart: push Monaco code into engine (see StrategyEditorTab.sync_engine_from_editor).
        if idx == 0 and prev == self._strategy_editor_tab_index and self.strategy_editor_tab.should_sync_editor_to_engine():
            self.strategy_editor_tab.sync_engine_from_editor()

        # 0=Chart, 1=Stats, 2=Trades, 3=Analytics, 4=Quant, 5=Logs, 6=Editor, 7=Settings
        if idx == 1:
            self.stats_tab._recalc()
        elif idx == 2:
            self.trades_tab._apply_filter()
        elif idx == 3:
            self.analytics_tab.refresh_all()
        elif idx == 4:
            self.quant_tab.refresh_all()

    def on_export_report(self) -> None:
        from gui.persistence.report_export import export_report_dialog

        try:
            path = export_report_dialog(self)
            if path:
                QMessageBox.information(self, "Report exported", f"Report written to:\n{path}")
        except Exception as e:
            logger.exception("export_report failed")
            QMessageBox.critical(self, "Export failed", str(e))

    # -- Qt event hooks -----------------------------------------------
    def keyPressEvent(self, e) -> None:  # type: ignore[no-untyped-def]
        if e.key() == Qt.Key.Key_Control:
            import pyqtgraph as pg

            self.chart_tab.plot_widget.getViewBox().setMouseMode(pg.ViewBox.RectMode)
        super().keyPressEvent(e)

    def keyReleaseEvent(self, e) -> None:  # type: ignore[no-untyped-def]
        if e.key() == Qt.Key.Key_Control:
            import pyqtgraph as pg

            self.chart_tab.plot_widget.getViewBox().setMouseMode(pg.ViewBox.PanMode)
        super().keyReleaseEvent(e)

    def closeEvent(self, e) -> None:  # type: ignore[no-untyped-def]
        self.bar_replay.stop_playback()

        # Sprint 3.5 — Fast startup cache
        # Save engine state FIRST so current_idx is captured before save_settings
        try:
            from engine import save_session
            cache_path = os.path.join(os.path.dirname(self.settings_file), ".auto_save.agbk")
            save_session(self.engine, cache_path)
        except Exception as exc:
            logger.debug(f"Failed to save startup cache: {exc}")

        # Persist chat tabs
        if self._chat_tabs_state:
            self._save_chat_tabs_to_disk(self._chat_tabs_state)

        self.save_settings()

        super().closeEvent(e)

    def show_error(self, m: str) -> None:
        # Auto-fix loop interception
        if hasattr(self, "strategy_editor_tab") and self.tabs.currentWidget() == self.strategy_editor_tab:
            self.strategy_editor_tab.handle_backtest_error(m)
            return

        # Show non-blocking toast notification on chart instead of modal dialog
        try:
            from PySide6.QtCore import QTimer
            from PySide6.QtWidgets import QLabel
            from PySide6.QtGui import QColor

            # Remove old toast if exists
            if hasattr(self, "_error_toast") and self._error_toast is not None:
                self._error_toast.deleteLater()
                self._error_toast = None

            toast = QLabel(self.chart_tab)
            toast.setText(f"⚠️ Strategy error — check Logs tab")
            toast.setToolTip(m)
            toast.setStyleSheet(
                "background: rgba(200, 50, 50, 180); color: white; "
                "padding: 8px 16px; border-radius: 6px; font-weight: bold; font-size: 13px;"
            )
            toast.adjustSize()
            toast.move(10, 10)
            toast.show()
            self._error_toast = toast

            # Auto-hide after 5 seconds
            QTimer.singleShot(5000, lambda: (toast.deleteLater(), setattr(self, '_error_toast', None) if hasattr(self, '_error_toast') else None))
        except Exception:
            pass

    def toggle_global_ai_panel(self) -> None:
        visible = self.global_ai_panel.isVisible()
        if visible:
            self.global_ai_panel.hide()
            self.chart_splitter.setSizes([1, 0])
            return
        self.global_ai_panel.show()
        total_w = max(1, self.chart_splitter.width())
        ai_w = max(320, min(420, int(total_w * 0.28)))
        chart_w = max(1, total_w - ai_w)
        self.chart_splitter.setSizes([chart_w, ai_w])

    def _read_strategy_code_for_ai(self, fallback_code: str) -> str:
        if fallback_code and fallback_code.strip():
            return fallback_code
        temp_path = os.path.join(_APP_DIR, "strategies", "temp_ai_strategy.py")
        if os.path.exists(temp_path):
            try:
                with open(temp_path, "r", encoding="utf-8") as f:
                    return f.read()
            except Exception:
                pass
        return ""

    def _on_global_ai_chat_requested(self, prompt: str, code_context: str, selection_ctx_json: str) -> None:
        extra_context = self._chart_selection_context or {}
        if selection_ctx_json:
            try:
                extra_context = json.loads(selection_ctx_json)
            except Exception:
                pass
        code = self._read_strategy_code_for_ai(code_context)
        kw = self.settings_tab.get_ai_worker_kwargs()
        
        # Forward user message to mirror panel (Editor)
        self.global_ai_panel.forward_user_message_to_mirror(prompt)
        
        self._global_ai_worker = AIAssistantWorker(
            prompt=prompt,
            code_context=code,
            error_traceback=None,
            extra_context=extra_context,
            **kw,
        )
        self._global_ai_worker.chunk_received.connect(self.global_ai_panel.stream_chunk)
        self._global_ai_worker.phase_changed.connect(self.global_ai_panel.set_ai_phase)
        self._global_ai_worker.error_occurred.connect(self.global_ai_panel.show_error)
        self._global_ai_worker.finished_generation.connect(
            lambda _full: self.global_ai_panel.finish_generation(_full or "")
        )
        self._global_ai_worker.start()

    def _on_global_ai_stop_requested(self) -> None:
        if self._global_ai_worker:
            self._global_ai_worker.stop()

    def _on_chart_selection_requested(self) -> None:
        self.tabs.setCurrentIndex(0)
        if not self.global_ai_panel.isVisible():
            self.toggle_global_ai_panel()
        self.chart_tab.enable_area_selection_mode()

    def _on_chart_area_selected(self, payload: dict) -> None:
        self._chart_selection_context = dict(payload)
        self._chart_selection_context["strategy_code"] = self._read_strategy_code_for_ai("")
        self.global_ai_panel.set_chart_selection_context(self._chart_selection_context)
        self.global_ai_panel.set_chat_input_text("Analyze this selected chart area and suggest strategy improvements.")

    def _restore_chat_tabs_to_editor(self) -> None:
        """Called by StrategyEditorTab when editor web is ready."""
        saved = self._load_chat_tabs_from_disk()
        if saved:
            self.strategy_editor_tab.editor.apply_chat_tabs_state(saved)

    def _on_chart_ai_tabs_changed(self, state_json: str) -> None:
        """Chat tabs changed in global AI panel → push to Editor panel + persist."""
        self._chat_tabs_state = state_json
        self._save_chat_tabs_to_disk(state_json)
        self.strategy_editor_tab.editor.apply_chat_tabs_state(state_json)

    def _on_editor_tabs_changed(self, state_json: str) -> None:
        """Chat tabs changed in Editor panel → push to global AI panel + persist."""
        self._chat_tabs_state = state_json
        self._save_chat_tabs_to_disk(state_json)
        self.global_ai_panel.apply_chat_tabs_state(state_json)

    def _save_chat_tabs_to_disk(self, state_json: str) -> None:
        try:
            os.makedirs(os.path.dirname(self._chat_tabs_file), exist_ok=True)
            with open(self._chat_tabs_file, "w", encoding="utf-8") as f:
                f.write(state_json)
        except Exception:
            pass

    def _load_chat_tabs_from_disk(self) -> str | None:
        try:
            if os.path.exists(self._chat_tabs_file):
                with open(self._chat_tabs_file, "r", encoding="utf-8") as f:
                    raw = f.read()
                    if raw.strip():
                        return raw
        except Exception:
            pass
        return None

    def _on_ai_attachment_requested(self, kind: str) -> None:
        if kind == "image":
            flt = "Images (*.png *.jpg *.jpeg *.webp *.bmp *.gif)"
            title = "Select image"
        else:
            flt = "Documents (*.pdf *.txt *.md *.csv *.json *.doc *.docx);;All files (*.*)"
            title = "Select document"
        path, _ = QFileDialog.getOpenFileName(self, title, "", flt)
        if not path:
            return
        payload = {
            "kind": kind,
            "name": os.path.basename(path),
            "path": path,
        }
        self.global_ai_panel.set_attachment(payload)
