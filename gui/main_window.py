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

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
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
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Window)
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
        self.settings_tab = SettingsTab(self.engine, parent_window=self)
        self.strategy_editor_tab = StrategyEditorTab(self.engine, self)

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
        cp.addWidget(self.chart_splitter, stretch=1)

        for w, name in (
            (chart_page, "Chart"),
            (self.stats_tab, "Stats"),
            (self.trades_tab, "Trades"),
            (self.analytics_tab, "Analytics"),
            (self.quant_tab, "Quant"),
            (self.log_console, "Logs"),
            (self.settings_tab, "Settings"),
            (self.strategy_editor_tab, "Editor"),
        ):
            self.tabs.addTab(w, name)
        self.tabs.currentChanged.connect(self._on_tab)
        root.addWidget(self.tabs)

        self.setStatusBar(self._status_bar_ctrl.status_bar)
        self._advanced_resizer = AdvancedResizer(self, margin=6)

    def _wire_signals(self) -> None:
        e = self.engine
        e.step_completed.connect(self._replay_ctrl.on_step)
        e.stats_updated.connect(self._status_bar_ctrl.on_stats)
        e.data_loaded.connect(self._replay_ctrl.on_data_loaded)
        e.position_changed.connect(self.chart_tab.on_position_changed)
        e.simulation_finished.connect(self._replay_ctrl.on_simulation_finished)

    def _wire_shortcuts(self) -> None:
        sc = lambda k, fn: QShortcut(QKeySequence(k), self, fn)  # noqa: E731
        rc, mc = self._replay_ctrl, self._menu_ctrl
        sc(Qt.Key.Key_Right, lambda: self.chart_tab.scroll_chart(1))
        sc(Qt.Key.Key_Left, lambda: self.chart_tab.scroll_chart(-1))
        sc("Shift+Right", rc.step_visual_candle)
        sc("Shift+Left", rc.step_back_visual_candle)
        sc(Qt.Key.Key_Space, self.bar_replay.toggle_play)
        sc(Qt.Key.Key_Home, self.chart_tab._fit_all)
        sc(Qt.Key.Key_Escape, lambda: self.chart_tab._set_tool("None"))
        sc("Ctrl+=", lambda: self.chart_tab.zoom_chart(1.15))
        sc("Ctrl+-", lambda: self.chart_tab.zoom_chart(1 / 1.15))
        sc("Ctrl+Shift+=", lambda: self._zoom_y(1.15))
        sc("Ctrl+Shift+-", lambda: self._zoom_y(1 / 1.15))
        sc("Ctrl+Z", rc.undo_drawing)
        sc("Ctrl+Shift+Z", rc.redo_drawing)
        sc("Ctrl+Alt+R", self.bar_replay.run_to_end)
        sc("Ctrl+/", mc.show_cheat_sheet)
        sc("Ctrl+Shift+P", mc.show_command_palette)

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

    def _on_tab(self, idx: int) -> None:
        # 0=Chart, 1=Stats, 2=Trades, 3=Analytics, 4=Quant, 5=Logs, 6=Settings
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
        self.save_settings()
        super().closeEvent(e)

    def show_error(self, m: str) -> None:
        # Auto-fix loop interception
        if hasattr(self, "strategy_editor_tab") and self.tabs.currentWidget() == self.strategy_editor_tab:
            self.strategy_editor_tab.handle_backtest_error(m)
        else:
            QMessageBox.critical(self, "Error", m)
