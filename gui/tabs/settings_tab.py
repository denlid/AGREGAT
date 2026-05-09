"""Settings Tab — Comprehensive preferences and configuration."""

import logging

from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from gui.persistence import settings_persist
from gui.styling.theme import CHART_SCHEMES, register_theme_listener

logger = logging.getLogger("StrategyTester.gui.settings_tab")


class SettingsTab(QWidget):
    def __init__(self, engine, parent_window=None):
        super().__init__()
        self.engine = engine
        self.pw = parent_window
        self._groups = []

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(6, 6, 6, 6)
        main_layout.setSpacing(8)

        # Header
        header = QHBoxLayout()
        title = QLabel("⚙️ Settings & Preferences")
        title_font = QFont()
        title_font.setPointSize(14)
        title_font.setBold(True)
        title.setFont(title_font)
        header.addWidget(title)
        header.addStretch()

        btn_reset = QPushButton("🔄 Reset to Defaults")
        btn_reset.setFixedWidth(150)
        btn_reset.clicked.connect(self._reset_defaults)
        btn_reset.setToolTip("Reset all settings to default values")
        header.addWidget(btn_reset)

        main_layout.addLayout(header)

        # Sub-tabs (horizontal): Appearance / Chart / Trading / Execution / Performance / Logs / Hotkeys
        self.sub_tabs = QTabWidget()
        self.sub_tabs.setDocumentMode(True)
        self.sub_tabs.setTabPosition(QTabWidget.TabPosition.North)

        def _new_pane() -> QVBoxLayout:
            """Returns a layout you can add groups to; the pane is a QScrollArea wrapping a widget."""
            scroll_p = QScrollArea()
            scroll_p.setWidgetResizable(True)
            scroll_p.setStyleSheet("QScrollArea { border: none; background: transparent; }")
            content_p = QWidget()
            lay_p = QVBoxLayout(content_p)
            lay_p.setSpacing(12)
            scroll_p.setWidget(content_p)
            scroll_p._lay = lay_p
            return scroll_p

        appearance_pane = _new_pane()
        chart_pane = _new_pane()
        trading_pane = _new_pane()
        strategy_pane = _new_pane()
        execution_pane = _new_pane()
        performance_pane = _new_pane()
        logs_pane = _new_pane()
        hotkeys_pane = _new_pane()
        ai_pane = _new_pane()
        # Container for strategy-specific widgets (rebuilt whenever a
        # new strategy is loaded). Settings → Strategy auto-discovers
        # the loaded strategy's PARAMS list and builds a form from it.
        self._strategy_pane = strategy_pane
        self._strategy_widgets: dict = {}
        self._strategy_form_holder: QGroupBox | None = None

        layout = appearance_pane._lay  # initial layout — appearance groups go here

        # --- Appearance ---
        grp1 = self._create_group("🎨 Appearance")
        f1 = QFormLayout()
        f1.setSpacing(8)

        # Sprint 2.8 — themes come from ThemeService.available_themes()
        # so dropping a new ``custom.qss`` next to ``THEMES["custom"]``
        # registers it without code changes here.
        from gui.themes import ThemeService

        self.theme_cb = QComboBox()
        theme_choices = list(ThemeService.available_themes()) + ["auto"]
        self.theme_cb.addItems(theme_choices)
        self.theme_cb.currentTextChanged.connect(self._on_theme)
        self.theme_cb.setToolTip(
            "Choose UI theme. 'auto' follows the system's color scheme on the "
            "next app launch (falls back to dark if the OS does not expose one)."
        )
        f1.addRow("Theme:", self.theme_cb)

        self.colors_cb = QComboBox()
        self.colors_cb.addItems(list(CHART_SCHEMES.keys()))
        self.colors_cb.setCurrentText("Classic")
        self.colors_cb.currentTextChanged.connect(self._on_colors)
        self.colors_cb.setToolTip("Choose chart color scheme")
        f1.addRow("Chart Preset:", self.colors_cb)

        # Custom colors
        color_row = QHBoxLayout()
        self.bg_color_btn = QPushButton("BG Color")
        self.bg_color_btn.clicked.connect(lambda: self._pick_custom_color("bg"))
        self.bg_color_btn.setFixedWidth(90)
        color_row.addWidget(self.bg_color_btn)

        self.bull_color_btn = QPushButton("Bull Candle")
        self.bull_color_btn.clicked.connect(lambda: self._pick_custom_color("bull"))
        self.bull_color_btn.setFixedWidth(90)
        color_row.addWidget(self.bull_color_btn)

        self.bear_color_btn = QPushButton("Bear Candle")
        self.bear_color_btn.clicked.connect(lambda: self._pick_custom_color("bear"))
        self.bear_color_btn.setFixedWidth(90)
        color_row.addWidget(self.bear_color_btn)
        color_row.addStretch()

        f1.addRow("Custom Colors:", color_row)

        # Font size
        self.font_size_spin = QSpinBox()
        self.font_size_spin.setRange(8, 16)
        self.font_size_spin.setValue(11)
        self.font_size_spin.setSuffix(" pt")
        self.font_size_spin.setToolTip("UI text font size")
        self.font_size_spin.valueChanged.connect(self._on_font_size)
        f1.addRow("Font Size:", self.font_size_spin)

        grp1.setLayout(f1)
        layout.addWidget(grp1)
        layout.addStretch()

        # --- Chart Display ---
        layout = chart_pane._lay
        grp2 = self._create_group("📊 Chart Display")
        f2 = QFormLayout()
        f2.setSpacing(8)

        self.tz_spin = QSpinBox()
        self.tz_spin.setRange(-12, 14)
        self.tz_spin.setValue(0)
        self.tz_spin.setSuffix(" hours (UTC offset)")
        self.tz_spin.valueChanged.connect(self._on_tz_changed)
        self.tz_spin.setToolTip("Adjust displayed time zone")
        f2.addRow("Timezone:", self.tz_spin)

        self.render_win = QSpinBox()
        self.render_win.setRange(50, 1000)
        self.render_win.setValue(300)
        self.render_win.setSuffix(" candles")
        self.render_win.setToolTip("Number of candles to render (lower = faster)")
        self.render_win.valueChanged.connect(self._on_render_win_changed)
        f2.addRow("Render Window:", self.render_win)

        self.show_grid_cb = QCheckBox("Show Grid")
        self.show_grid_cb.setChecked(True)
        self.show_grid_cb.setToolTip("Display chart grid lines")
        self.show_grid_cb.toggled.connect(self._on_show_grid)
        f2.addRow("Grid:", self.show_grid_cb)

        self.crosshair_cb = QCheckBox("Crosshair")
        self.crosshair_cb.setChecked(True)
        self.crosshair_cb.setToolTip("Show crosshair on mouse hover")
        self.crosshair_cb.toggled.connect(self._on_crosshair)
        f2.addRow("Crosshair:", self.crosshair_cb)

        self.autofit_y_cb = QCheckBox("Auto-fit Y Axis")
        self.autofit_y_cb.setChecked(True)
        self.autofit_y_cb.setToolTip("Automatically adjust the Y axis to fit the visible candles")
        self.autofit_y_cb.toggled.connect(self._on_autofit_y)
        f2.addRow("Auto-fit Y:", self.autofit_y_cb)

        grp2.setLayout(f2)
        layout.addWidget(grp2)
        layout.addStretch()

        # --- Trading Parameters ---
        layout = trading_pane._lay
        grp3 = self._create_group("💰 Trading Parameters")
        f3 = QFormLayout()
        f3.setSpacing(8)

        self.equity_spin = QDoubleSpinBox()
        self.equity_spin.setRange(1000, 10_000_000)
        self.equity_spin.setValue(100_000)
        self.equity_spin.setSingleStep(10_000)
        self.equity_spin.setPrefix("$ ")
        self.equity_spin.setDecimals(0)
        self.equity_spin.valueChanged.connect(self._on_equity)
        self.equity_spin.setToolTip("Starting capital for backtesting")
        f3.addRow("Initial Equity:", self.equity_spin)

        self.risk_pct_spin = QDoubleSpinBox()
        self.risk_pct_spin.setRange(0.01, 10.0)
        self.risk_pct_spin.setValue(getattr(self.engine, "risk_pct", 1.0))
        self.risk_pct_spin.setSingleStep(0.1)
        self.risk_pct_spin.setSuffix(" %")
        self.risk_pct_spin.setDecimals(2)
        self.risk_pct_spin.setToolTip("Risk per trade as % of equity (used by ctx.calculate_lot)")
        self.risk_pct_spin.valueChanged.connect(self._on_risk_pct)
        f3.addRow("Risk per Trade:", self.risk_pct_spin)

        self.max_lots_spin = QDoubleSpinBox()
        self.max_lots_spin.setRange(1, 1000)
        self.max_lots_spin.setValue(self.engine.asset_config.max_lots_per_100k)
        self.max_lots_spin.setSingleStep(1)
        self.max_lots_spin.setDecimals(0)
        self.max_lots_spin.setToolTip("Maximum lots per 100k equity (clamps ctx.calculate_lot)")
        self.max_lots_spin.valueChanged.connect(self._on_max_lots)
        f3.addRow("Max Lots/100k:", self.max_lots_spin)

        grp3.setLayout(f3)
        layout.addWidget(grp3)
        layout.addStretch()

        # --- Strategy (auto-built from Strategy.PARAMS) ---
        layout = strategy_pane._lay
        # Hint group: shown when no strategy is loaded.
        self._strategy_empty = self._create_group("🧠 Strategy")
        empty_lay = QVBoxLayout()
        empty_lay.addWidget(
            QLabel("Load a strategy via the Strategy menu to expose its parameters here.")
        )
        self._strategy_empty.setLayout(empty_lay)
        layout.addWidget(self._strategy_empty)
        # Real form is created on demand by ``rebuild_strategy_form``.
        layout.addStretch()

        # --- Execution Model ---
        layout = execution_pane._lay
        grp_exec = self._create_group("📉 Execution Model")
        fexec = QFormLayout()
        fexec.setSpacing(8)

        self.exec_profile_cb = QComboBox()
        self.exec_profile_cb.addItems(["Optimistic", "Realistic", "Custom"])
        self.exec_profile_cb.setCurrentText(
            getattr(self.engine.execution_model, "profile", "Optimistic")
        )
        self.exec_profile_cb.setToolTip(
            "Optimistic = no costs (upper bound). Realistic = use AssetConfig defaults. "
            "Custom = override with the values below."
        )
        self.exec_profile_cb.currentTextChanged.connect(self._on_exec_profile)
        fexec.addRow("Profile:", self.exec_profile_cb)

        self.spread_spin = QDoubleSpinBox()
        self.spread_spin.setRange(0.0, 100.0)
        self.spread_spin.setValue(self.engine.asset_config.spread_points or 0.0)
        self.spread_spin.setSingleStep(0.1)
        self.spread_spin.setDecimals(2)
        self.spread_spin.setSuffix(" pts")
        self.spread_spin.setToolTip(
            "Bid-ask spread (points). Charts are Bid-priced: long entries pay "
            "the full spread (= Ask), short SL/TP triggers fire spread above "
            "the chart, while long SL/TP and short entries fill exactly on the "
            "chart Bid."
        )
        self.spread_spin.valueChanged.connect(self._on_spread)
        fexec.addRow("Spread:", self.spread_spin)

        self.commission_spin = QDoubleSpinBox()
        self.commission_spin.setRange(0.0, 1000.0)
        self.commission_spin.setValue(self.engine.asset_config.commission_per_lot or 0.0)
        self.commission_spin.setSingleStep(0.5)
        self.commission_spin.setDecimals(2)
        self.commission_spin.setSuffix(" $")
        self.commission_spin.setToolTip("Commission per lot per round-trip ($).")
        self.commission_spin.valueChanged.connect(self._on_commission)
        fexec.addRow("Commission/Lot:", self.commission_spin)

        self.slippage_spin = QDoubleSpinBox()
        self.slippage_spin.setRange(0.0, 100.0)
        self.slippage_spin.setValue(self.engine.asset_config.slippage_points or 0.0)
        self.slippage_spin.setSingleStep(0.1)
        self.slippage_spin.setDecimals(2)
        self.slippage_spin.setSuffix(" pts")
        self.slippage_spin.setToolTip(
            "Avg slippage per leg (points). Applied to fills and non-stop closes."
        )
        self.slippage_spin.valueChanged.connect(self._on_slippage)
        fexec.addRow("Slippage:", self.slippage_spin)

        grp_exec.setLayout(fexec)
        layout.addWidget(grp_exec)
        layout.addStretch()

        # --- Performance ---
        layout = performance_pane._lay
        grp4 = self._create_group("⚡ Performance")
        f4 = QFormLayout()
        f4.setSpacing(8)

        self.fast_mode_cb = QCheckBox("Fast Mode")
        self.fast_mode_cb.setChecked(False)
        self.fast_mode_cb.setToolTip(
            "Skip per-candle chart redraws during bulk run — helpful for huge datasets."
        )
        self.fast_mode_cb.toggled.connect(self._on_fast_mode)
        f4.addRow("Optimization:", self.fast_mode_cb)

        self.cache_size_spin = QSpinBox()
        self.cache_size_spin.setRange(100, 10000)
        self.cache_size_spin.setValue(1000)
        self.cache_size_spin.setSuffix(" candles")
        self.cache_size_spin.setToolTip("Cache size for faster rendering")
        f4.addRow("Cache Size:", self.cache_size_spin)

        self.batch_size_spin = QSpinBox()
        self.batch_size_spin.setRange(100, 10000)
        self.batch_size_spin.setValue(5000)
        self.batch_size_spin.setSuffix(" candles")
        self.batch_size_spin.setToolTip("Batch size for Run to End")
        self.batch_size_spin.valueChanged.connect(self._on_batch_size_changed)
        f4.addRow("Batch Size:", self.batch_size_spin)

        grp4.setLayout(f4)
        layout.addWidget(grp4)
        layout.addStretch()

        # --- Logging ---
        layout = logs_pane._lay
        grp5 = self._create_group("📝 Logging")
        f5 = QFormLayout()
        f5.setSpacing(8)

        self.log_level_cb = QComboBox()
        self.log_level_cb.addItems(["DEBUG", "INFO", "WARNING", "ERROR"])
        self.log_level_cb.setCurrentText("INFO")
        self.log_level_cb.currentTextChanged.connect(self._on_log_level)
        self.log_level_cb.setToolTip("Logging verbosity level")
        f5.addRow("Log Level:", self.log_level_cb)

        self.auto_save_logs_cb = QCheckBox("Auto-save Logs")
        self.auto_save_logs_cb.setChecked(True)
        self.auto_save_logs_cb.setToolTip("Automatically save logs to file")
        self.auto_save_logs_cb.toggled.connect(self._on_auto_save_logs)
        f5.addRow("Auto-save:", self.auto_save_logs_cb)

        self.max_log_lines_spin = QSpinBox()
        self.max_log_lines_spin.setRange(100, 100000)
        self.max_log_lines_spin.setValue(5000)
        self.max_log_lines_spin.setSuffix(" lines")
        self.max_log_lines_spin.setToolTip("Maximum lines retained in the log console")
        self.max_log_lines_spin.valueChanged.connect(self._on_max_log_lines)
        f5.addRow("Max Lines:", self.max_log_lines_spin)

        grp5.setLayout(f5)
        layout.addWidget(grp5)
        layout.addStretch()

        # --- Shortcuts ---
        layout = hotkeys_pane._lay
        grp6 = self._create_group("⌨️ Keyboard Shortcuts")
        self.shortcuts_text = QLabel("""
<b>Navigation:</b><br>
• Space — Play/Pause<br>
• Shift+Right — Step Forward<br>
• Shift+Left — Step Back<br>
• Home — Zoom to Fit<br>
• End — Jump to End<br>
<br>
<b>File Operations:</b><br>
• Ctrl+O — Load Data<br>
• Ctrl+L — Load Strategy<br>
• Ctrl+R — Reset<br>
• Ctrl+E — Export Trades<br>
<br>
<b>Drawing:</b><br>
• Ctrl+Z — Undo<br>
• Ctrl+Shift+Z — Redo<br>
• Escape — Cancel Tool<br>
<br>
<b>Quick Actions:</b><br>
• Ctrl+Alt+R — Run to End<br>
        """)
        from gui.styling import style_helpers as sh
        from gui.styling.theme import get_theme as _gt

        self.shortcuts_text.setStyleSheet(
            f"color: {sh.c(_gt(), 'text2')}; font-size: 11px; padding: 8px;"
        )
        self.shortcuts_text.setWordWrap(True)

        shortcuts_layout = QVBoxLayout()
        shortcuts_layout.addWidget(self.shortcuts_text)
        grp6.setLayout(shortcuts_layout)
        layout.addWidget(grp6)

        layout.addStretch()

        # --- AI Settings ---
        layout = ai_pane._lay
        grp7 = self._create_group("🤖 AI Provider")
        f7 = QFormLayout()
        f7.setSpacing(8)

        self.ai_provider_cb = QComboBox()
        self.ai_provider_cb.addItems(["OpenAI (GPT-4o)", "Anthropic (Claude 3.5)", "Mistral (Large)"])
        self.ai_provider_cb.setCurrentText("OpenAI (GPT-4o)")
        self.ai_provider_cb.setToolTip("Select the LLM provider for strategy generation")
        f7.addRow("Provider:", self.ai_provider_cb)

        from PySide6.QtWidgets import QLineEdit
        self.ai_api_key_le = QLineEdit()
        self.ai_api_key_le.setEchoMode(QLineEdit.EchoMode.Password)
        self.ai_api_key_le.setPlaceholderText("sk-...")
        self.ai_api_key_le.setToolTip("API Key for the selected provider")
        f7.addRow("API Key:", self.ai_api_key_le)

        grp7.setLayout(f7)
        layout.addWidget(grp7)
        layout.addStretch()

        # Assemble sub-tabs
        self.sub_tabs.addTab(appearance_pane, "Appearance")
        self.sub_tabs.addTab(chart_pane, "Chart")
        self.sub_tabs.addTab(trading_pane, "Trading")
        self.sub_tabs.addTab(strategy_pane, "Strategy")
        self.sub_tabs.addTab(execution_pane, "Execution")
        self.sub_tabs.addTab(performance_pane, "Performance")
        self.sub_tabs.addTab(logs_pane, "Logs")
        self.sub_tabs.addTab(hotkeys_pane, "Hotkeys")
        self.sub_tabs.addTab(ai_pane, "AI")
        main_layout.addWidget(self.sub_tabs)

        # Wire persistence — every relevant signal saves the full snapshot.
        self._wire_persistence_signals()

        register_theme_listener(self._apply_theme)

    def _wire_persistence_signals(self) -> None:
        widgets_text = [
            self.theme_cb,
            self.colors_cb,
            self.exec_profile_cb,
            self.log_level_cb,
            self.ai_provider_cb,
        ]
        self.ai_api_key_le.textChanged.connect(self._persist)
        widgets_value = [
            self.font_size_spin,
            self.equity_spin,
            self.risk_pct_spin,
            self.max_lots_spin,
            self.tz_spin,
            self.render_win,
            self.cache_size_spin,
            self.batch_size_spin,
            self.max_log_lines_spin,
            self.spread_spin,
            self.commission_spin,
            self.slippage_spin,
        ]
        widgets_toggle = [
            self.fast_mode_cb,
            self.auto_save_logs_cb,
            self.show_grid_cb,
            self.crosshair_cb,
            self.autofit_y_cb,
        ]
        for w in widgets_text:
            w.currentTextChanged.connect(self._persist)
        for w in widgets_value:
            w.valueChanged.connect(self._persist)
        for w in widgets_toggle:
            w.toggled.connect(self._persist)

    # ---- Persistence (Phase 2) ----
    def _capture_settings(self) -> dict:
        """Return current widget state as a settings dict."""
        em = getattr(self.engine, "execution_model", None)
        snapshot = {
            "theme": self.theme_cb.currentText(),
            "chart_scheme": self.colors_cb.currentText(),
            "font_size": self.font_size_spin.value(),
            "initial_equity": self.equity_spin.value(),
            "risk_pct": self.risk_pct_spin.value(),
            "max_lots_per_100k": self.max_lots_spin.value(),
            "timezone": self.tz_spin.value(),
            "render_window": self.render_win.value(),
            "cache_size": self.cache_size_spin.value(),
            "batch_size": self.batch_size_spin.value(),
            "log_level": self.log_level_cb.currentText(),
            "ai_provider": self.ai_provider_cb.currentText(),
            "ai_api_key": self.ai_api_key_le.text(),
            "fast_mode": self.fast_mode_cb.isChecked(),
            "auto_save_logs": self.auto_save_logs_cb.isChecked(),
            "max_log_lines": self.max_log_lines_spin.value(),
            "show_grid": self.show_grid_cb.isChecked(),
            "crosshair": self.crosshair_cb.isChecked(),
            "autofit_y": self.autofit_y_cb.isChecked(),
            "last_asset": getattr(self.engine.asset_config, "name", "DE40"),
            "exec_profile": self.exec_profile_cb.currentText(),
            "exec_spread_points": em.spread_points if em else None,
            "exec_commission_per_lot": em.commission_per_lot if em else None,
            "exec_slippage_points": em.slippage_points if em else None,
        }
        # Strategy params — persisted under the strategy class name so
        # multiple strategies can be remembered independently.
        strategy = getattr(self.engine, "strategy", None)
        if strategy is not None and self._strategy_widgets:
            try:
                existing = (settings_persist.load() or {}).get("strategy_params", {})
            except Exception:
                existing = {}
            existing[type(strategy).__name__] = self._strategy_form_values()
            snapshot["strategy_params"] = existing
        return snapshot

    def _persist(self) -> None:
        """Save current settings to disk (called after every user change)."""
        try:
            settings_persist.save(self._capture_settings())
        except Exception as e:
            logger.debug(f"Settings persistence failed: {e!r}")

    def apply_persisted(self, settings: dict) -> None:
        """Apply a settings dict to the UI (call after `__init__`)."""
        try:
            self.theme_cb.setCurrentText(settings.get("theme", "dark"))
            self.colors_cb.setCurrentText(settings.get("chart_scheme", "Classic"))
            self.font_size_spin.setValue(int(settings.get("font_size", 11)))
            self.equity_spin.setValue(int(settings.get("initial_equity", 100_000)))
            self.risk_pct_spin.setValue(float(settings.get("risk_pct", 1.0)))
            self.max_lots_spin.setValue(float(settings.get("max_lots_per_100k", 12)))
            self.tz_spin.setValue(int(settings.get("timezone", 0)))
            self.render_win.setValue(int(settings.get("render_window", 300)))
            self.cache_size_spin.setValue(int(settings.get("cache_size", 1000)))
            self.batch_size_spin.setValue(int(settings.get("batch_size", 5000)))
            self.log_level_cb.setCurrentText(settings.get("log_level", "INFO"))
            self.ai_provider_cb.setCurrentText(settings.get("ai_provider", "OpenAI (GPT-4o)"))
            self.ai_api_key_le.setText(settings.get("ai_api_key", ""))
            self.fast_mode_cb.setChecked(bool(settings.get("fast_mode", False)))
            self.auto_save_logs_cb.setChecked(bool(settings.get("auto_save_logs", True)))
            self.max_log_lines_spin.setValue(int(settings.get("max_log_lines", 5000)))
            self.show_grid_cb.setChecked(bool(settings.get("show_grid", True)))
            self.crosshair_cb.setChecked(bool(settings.get("crosshair", True)))
            self.autofit_y_cb.setChecked(bool(settings.get("autofit_y", True)))
            self.exec_profile_cb.setCurrentText(settings.get("exec_profile", "Optimistic"))
            em = getattr(self.engine, "execution_model", None)
            if em is not None:
                em.spread_points = settings.get("exec_spread_points")
                em.commission_per_lot = settings.get("exec_commission_per_lot")
                em.slippage_points = settings.get("exec_slippage_points")
        except Exception as e:
            logger.debug(f"Could not fully apply persisted settings: {e!r}")

    # ------------------------------------------------------------------
    # Strategy → PARAMS auto-form (Etap 4 of LINEAR LOGIC CORE plan)
    # ------------------------------------------------------------------
    def rebuild_strategy_form(self, strategy=None) -> None:
        """Rebuild the Strategy sub-tab to match the loaded strategy's PARAMS.

        Called by :class:`gui.main_window.MainWindow` whenever a new
        strategy is loaded. If ``strategy`` is ``None`` (no strategy
        loaded), the form is hidden and a placeholder is shown.

        The form mirrors the ordering of ``strategy.PARAMS`` and uses
        widget kinds keyed off ``Param.kind``:

        * ``"bool"``    — ``QCheckBox``
        * ``"int"``     — ``QSpinBox``
        * ``"float"``   — ``QDoubleSpinBox``
        * ``"choice"``  — ``QComboBox`` over ``Param.choices``

        Persistence is wired to the same disk file as the rest of
        Settings via :meth:`_persist`. The strategy's
        ``apply_params(values)`` method is called on every change so
        the next backtest run picks up the new values immediately.
        """
        from PySide6.QtWidgets import QLineEdit

        # Tear down the previous form (if any) — Qt deletes the
        # widgets, but we also drop our references so the wiring is
        # clean.
        if self._strategy_form_holder is not None:
            self._strategy_form_holder.setParent(None)
            self._strategy_form_holder.deleteLater()
            self._strategy_form_holder = None
        self._strategy_widgets = {}

        params = list(getattr(strategy, "PARAMS", []) or [])
        # Toggle empty-state placeholder visibility.
        self._strategy_empty.setVisible(not params)
        if not params:
            return

        title = "🧠 " + getattr(strategy, "name", type(strategy).__name__)
        grp = self._create_group(title)
        form = QFormLayout()
        form.setSpacing(8)

        for p in params:
            w = None
            if p.kind == "bool":
                cb = QCheckBox()
                cb.setChecked(bool(p.default))
                cb.toggled.connect(lambda _v, _p=p: self._on_strategy_param_changed(_p))
                w = cb
            elif p.kind == "int":
                sb = QSpinBox()
                sb.setRange(
                    int(p.min if p.min is not None else -10_000_000),
                    int(p.max if p.max is not None else 10_000_000),
                )
                sb.setSingleStep(int(p.step if p.step is not None else 1))
                sb.setValue(int(p.default))
                sb.valueChanged.connect(lambda _v, _p=p: self._on_strategy_param_changed(_p))
                w = sb
            elif p.kind == "float":
                ds = QDoubleSpinBox()
                ds.setRange(
                    float(p.min if p.min is not None else -1e9),
                    float(p.max if p.max is not None else 1e9),
                )
                ds.setSingleStep(float(p.step if p.step is not None else 0.1))
                ds.setDecimals(4 if (p.step is not None and p.step < 1) else 2)
                ds.setValue(float(p.default))
                ds.valueChanged.connect(lambda _v, _p=p: self._on_strategy_param_changed(_p))
                w = ds
            elif p.kind == "choice":
                co = QComboBox()
                for c in p.choices or []:
                    co.addItem(str(c))
                if p.default is not None:
                    idx = co.findText(str(p.default))
                    if idx >= 0:
                        co.setCurrentIndex(idx)
                co.currentTextChanged.connect(lambda _v, _p=p: self._on_strategy_param_changed(_p))
                w = co
            else:
                # Fallback: text input
                le = QLineEdit(str(p.default if p.default is not None else ""))
                le.editingFinished.connect(lambda _p=p: self._on_strategy_param_changed(_p))
                w = le

            if p.tooltip:
                w.setToolTip(p.tooltip)
            form.addRow(p.label or p.name, w)
            self._strategy_widgets[p.name] = (p, w)

        grp.setLayout(form)
        self._strategy_form_holder = grp

        # Insert above the bottom stretch in the strategy pane.
        pane_layout = self._strategy_pane._lay
        pane_layout.insertWidget(pane_layout.count() - 1, grp)

        # Apply any previously persisted strategy params on top of
        # defaults.
        try:
            persisted = settings_persist.load() or {}
        except Exception:
            persisted = {}
        sp = persisted.get("strategy_params", {})
        # Strategy class identity is encoded into a single key so two
        # different strategies persist independently.
        cls = type(strategy).__name__
        for name, val in (sp.get(cls, {}) or {}).items():
            entry = self._strategy_widgets.get(name)
            if entry is None:
                continue
            _p, w = entry
            try:
                if _p.kind == "bool":
                    w.setChecked(bool(val))
                elif _p.kind == "int":
                    w.setValue(int(val))
                elif _p.kind == "float":
                    w.setValue(float(val))
                elif _p.kind == "choice":
                    idx = w.findText(str(val))
                    if idx >= 0:
                        w.setCurrentIndex(idx)
            except Exception:
                pass

        # Push current values into the strategy *and* the engine
        # (force-eod / ott toggles also need to mirror onto AssetConfig).
        self._on_strategy_param_changed(None)

    def _strategy_form_values(self) -> dict:
        """Read the current strategy form into a ``{name: value}`` dict."""
        out: dict = {}
        for name, (p, w) in self._strategy_widgets.items():
            try:
                if p.kind == "bool":
                    out[name] = bool(w.isChecked())
                elif p.kind == "int":
                    out[name] = int(w.value())
                elif p.kind == "float":
                    out[name] = float(w.value())
                elif p.kind == "choice":
                    out[name] = str(w.currentText())
                else:
                    out[name] = str(w.text())
            except Exception:
                pass
        return out

    def _on_strategy_param_changed(self, _p) -> None:
        """Apply the current form to the engine + strategy and persist."""
        values = self._strategy_form_values()
        strategy = getattr(self.engine, "strategy", None)
        if strategy is not None and hasattr(strategy, "apply_params"):
            try:
                strategy.apply_params(values)
            except Exception as e:
                logger.debug(f"strategy.apply_params failed: {e!r}")

        # Mirror specific toggles onto AssetConfig so the engine flags
        # actually take effect.
        ac = getattr(self.engine, "asset_config", None)
        if ac is not None:
            if "force_eod_close" in values:
                ac.enforce_eod_close = bool(values["force_eod_close"])
            if "ott_filter" in values:
                ac.enforce_trading_hours = bool(values["ott_filter"])

        self._persist()

    def _create_group(self, title):
        """Create a styled group box using theme tokens."""
        from gui.styling.theme import get_theme

        t = get_theme()
        grp = QGroupBox(title)
        grp.setStyleSheet(f"""
            QGroupBox {{
                font-weight: bold;
                border: 1px solid {t["border"]};
                border-radius: 6px;
                margin-top: 12px;
                padding-top: 12px;
                background: {t["surface"]};
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px;
                color: {t["accent"]};
            }}
        """)
        self._groups.append(grp)
        return grp

    def _apply_theme(self):
        from gui.styling import style_helpers as sh
        from gui.styling.theme import get_theme

        t = get_theme()
        qss = f"""
            QGroupBox {{
                font-weight: bold;
                border: 1px solid {t["border"]};
                border-radius: 6px;
                margin-top: 12px;
                padding-top: 12px;
                background: {t["surface"]};
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px;
                color: {t["accent"]};
            }}
        """
        for grp in self._groups:
            grp.setStyleSheet(qss)

        if hasattr(self, "shortcuts_text"):
            self.shortcuts_text.setStyleSheet(
                f"color: {sh.c(t, 'text2')}; font-size: 11px; padding: 8px;"
            )

    def _on_theme(self, name):
        if self.pw:
            self.pw._switch_theme(name)
            logger.info(f"🎨 Theme changed to: {name}")

    def _on_colors(self, name):
        if self.pw and name != "Custom":
            self.pw.chart_tab.set_color_scheme(name)
            if getattr(self.pw, "chart_tab_2", None) is not None:
                self.pw.chart_tab_2.set_color_scheme(name)
            logger.info(f"🎨 Chart colors changed to: {name}")

    def _pick_custom_color(self, target):
        if not self.pw:
            return
        c = QColorDialog.getColor()
        if c.isValid():
            if target == "bg":
                self.pw.chart_tab.plot_widget.setBackground(c)
                logger.info(f"🎨 Background color changed to: {c.name()}")
            elif target == "bull":
                self.pw.chart_tab.candlestick_item.bull_color = c.name()
                self.pw.chart_tab.redraw_chart()
                logger.info(f"🎨 Bull candle color changed to: {c.name()}")
            elif target == "bear":
                self.pw.chart_tab.candlestick_item.bear_color = c.name()
                self.pw.chart_tab.redraw_chart()
                logger.info(f"🎨 Bear candle color changed to: {c.name()}")

    def _on_tz_changed(self, v):
        if self.pw:
            self.pw.chart_tab.time_axis.tz_offset = v
            self.pw.chart_tab.redraw_chart()
            logger.info(f"🌍 Timezone offset changed to: UTC{v:+d}")

    def _on_equity(self, v):
        self.engine.initial_equity = v
        if self.engine.current_idx < 0:
            self.engine.equity = float(v)
            self.engine.max_equity = float(v)
        logger.info(f"💰 Initial equity changed to: ${v:,.0f}")

    def _on_log_level(self, level):
        """Change logging level for all StrategyTester loggers."""
        level_map = {
            "DEBUG": logging.DEBUG,
            "INFO": logging.INFO,
            "WARNING": logging.WARNING,
            "ERROR": logging.ERROR,
        }
        target_level = level_map.get(level, logging.INFO)
        logging.getLogger("StrategyTester").setLevel(target_level)
        logger.info(f"📝 Log level changed to: {level}")

    def _on_render_win_changed(self, v):
        """Update chart render window size."""
        # The runtime constant lives in gui/chart_tab/tab.py — patch it there so
        # ChartTab.redraw_chart picks up the new window size.
        import gui.chart_tab.tab as tab_module

        tab_module.RENDER_WINDOW = v
        if self.pw:
            self.pw.chart_tab._cache_key = None
            self.pw.chart_tab.redraw_chart()
            if getattr(self.pw, "chart_tab_2", None) is not None:
                self.pw.chart_tab_2._cache_key = None
                self.pw.chart_tab_2.redraw_chart()
        logger.info(f"📊 Render window changed to: {v} candles")

    def _on_batch_size_changed(self, v):
        """Update batch size for Run to End."""
        if self.pw and hasattr(self.pw, "bar_replay"):
            self.pw.bar_replay._run_to_end_batch = v
        logger.info(f"⚡ Batch size changed to: {v} candles")

    # ---- New wired callbacks (Phase 1) ----
    def _on_risk_pct(self, v: float) -> None:
        self.engine.risk_pct = float(v)
        logger.info(f"💰 Risk per trade: {v:.2f}%")

    def _on_max_lots(self, v: float) -> None:
        self.engine.asset_config.max_lots_per_100k = int(v)
        logger.info(f"💰 Max lots per 100k: {int(v)}")

    def _apply_to_charts(self, fn) -> None:
        """Apply `fn(chart_tab)` to every visible chart_tab on the parent window."""
        if not self.pw:
            return
        for attr in ("chart_tab", "chart_tab_2"):
            ct = getattr(self.pw, attr, None)
            if ct is not None:
                try:
                    fn(ct)
                except Exception as e:
                    logger.debug(f"Chart setting apply failed on {attr}: {e!r}")

    def _on_show_grid(self, on: bool) -> None:
        def apply(ct):
            ct.plot_widget.showGrid(x=on, y=on, alpha=0.06 if on else 0.0)

        self._apply_to_charts(apply)
        logger.info(f"📊 Grid: {'ON' if on else 'OFF'}")

    def _on_crosshair(self, on: bool) -> None:
        def apply(ct):
            for attr in ("vline", "hline"):
                line = getattr(ct, attr, None)
                if line is not None:
                    line.setVisible(on)

        self._apply_to_charts(apply)
        logger.info(f"🎯 Crosshair: {'ON' if on else 'OFF'}")

    def _on_autofit_y(self, on: bool) -> None:
        def apply(ct):
            ct.autofit_y = on
            if on:
                ct.redraw_chart()

        self._apply_to_charts(apply)
        logger.info(f"📊 Auto-fit Y: {'ON' if on else 'OFF'}")

    def _on_fast_mode(self, on: bool) -> None:
        self.engine.fast_mode = bool(on)
        logger.info(f"⚡ Fast mode: {'ON' if on else 'OFF'}")

    def _on_auto_save_logs(self, on: bool) -> None:
        if self.pw and hasattr(self.pw, "log_console"):
            self.pw.log_console.auto_save_cb.setChecked(on)
        logger.info(f"📁 Log auto-save: {'ON' if on else 'OFF'}")

    def _on_max_log_lines(self, n: int) -> None:
        if self.pw and hasattr(self.pw, "log_console"):
            self.pw.log_console.set_max_lines(int(n))
        logger.info(f"📝 Log max lines: {int(n)}")

    # ---- Execution Model (Phase 2) ----
    def _on_exec_profile(self, name: str) -> None:
        em = self.engine.execution_model
        em.profile = name  # type: ignore[assignment]
        logger.info(f"📉 Execution profile: {name}")

    def _on_spread(self, v: float) -> None:
        self.engine.asset_config.spread_points = float(v)
        em = self.engine.execution_model
        if em.profile == "Custom":
            em.spread_points = float(v)
        logger.info(f"📉 Spread: {v:.2f} pts")

    def _on_commission(self, v: float) -> None:
        self.engine.asset_config.commission_per_lot = float(v)
        em = self.engine.execution_model
        if em.profile == "Custom":
            em.commission_per_lot = float(v)
        logger.info(f"📉 Commission/lot: ${v:.2f}")

    def _on_slippage(self, v: float) -> None:
        self.engine.asset_config.slippage_points = float(v)
        em = self.engine.execution_model
        if em.profile == "Custom":
            em.slippage_points = float(v)
        logger.info(f"📉 Slippage: {v:.2f} pts")

    def _on_font_size(self, n: int) -> None:
        from PySide6.QtGui import QFont
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance()
        if app is None:
            return
        f = QFont("Segoe UI", int(n))
        app.setFont(f)
        logger.info(f"🔤 Font size: {int(n)} pt")

    def _reset_defaults(self):
        """Reset all settings to defaults."""
        self.theme_cb.setCurrentText("dark")
        self.colors_cb.setCurrentText("Classic")
        self.tz_spin.setValue(0)
        self.render_win.setValue(300)
        self.equity_spin.setValue(100_000)
        self.risk_pct_spin.setValue(1.0)
        self.max_lots_spin.setValue(12)
        self.font_size_spin.setValue(11)
        self.show_grid_cb.setChecked(True)
        self.crosshair_cb.setChecked(True)
        self.autofit_y_cb.setChecked(True)
        self.fast_mode_cb.setChecked(False)
        self.cache_size_spin.setValue(1000)
        self.batch_size_spin.setValue(5000)
        self.log_level_cb.setCurrentText("INFO")
        self.auto_save_logs_cb.setChecked(True)
        self.max_log_lines_spin.setValue(5000)
        self.exec_profile_cb.setCurrentText("Optimistic")
        self.spread_spin.setValue(0.0)
        self.commission_spin.setValue(0.0)
        self.slippage_spin.setValue(0.0)
        logger.info("🔄 Settings reset to defaults")
