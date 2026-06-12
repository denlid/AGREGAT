"""
Analytics Tab — Premium analytics with improved equity curve (drawdown subplot),
session matrix, streak tracker, custom range WR, advanced stats.
"""

import logging
import threading

import numpy as np
import pandas as pd
import pyqtgraph as pg
from PySide6.QtCore import QDate, QPropertyAnimation, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QFont
from PySide6.QtWidgets import (
    QComboBox,
    QDateEdit,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGraphicsRectItem,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTableView,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from engine.models.models import SESSIONS
from engine.analytics.equity_curve import (
    build_equity_curve as _build_eq,
    sharpe_ratio as _eq_sharpe,
)
from engine.analytics.risk_metrics import risk_of_ruin as _risk_of_ruin, time_under_water as _time_under_water
from engine.analytics.trade_analytics import (
    drawdown_durations,
    exit_reason_attribution,
    filter_trades_by_period,
    hour_weekday_heatmap,
    mae_mfe_arrays,
    month_year_heatmap,
    resolve_period_window,
    r_multiples,
    simulate_slippage,
)
from gui.components.animations import fade_in
from gui.styling import style_helpers as sh
from gui.widgets.advanced_metrics import (
    analyze_trade_duration,
    calculate_calmar_ratio,
    calculate_consecutive_wins_losses,
    calculate_cvar,
    calculate_omega_ratio,
    calculate_sortino_ratio,
    calculate_ulcer_index,
    calculate_var,
)
from gui.styling.theme import (
    apply_drop_shadow,
    font_mono,
    get_theme,
    register_theme_listener,
    unregister_theme_listener,
)
from gui.workers.workers import ComputeWorker, Spinner
from gui.widgets.empty_state import EmptyState
from gui.widgets.metric_card import MetricCard
from gui.widgets.simple_table_model import SimpleTableModel
from gui.shared_data_cache import SessionRow, _trade_cache_key
from gui.analytics import sparkline_data as spk
from gui.analytics.heatmap_widget import HeatmapExtras, HeatmapWidget as _Heatmap



logger = logging.getLogger("StrategyTester.gui.analytics_tab")


WEEKDAY_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
MONTH_LABELS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

def _compute_heatmaps(data, cancel_token=None) -> dict:
    """Worker-thread helper: aggregate hour×weekday and year×month PnL grids."""
    if hasattr(data, "get_heatmaps"): # Is SharedTradesCache
        return data.get_heatmaps()
    hw = hour_weekday_heatmap(data, cancel_token=cancel_token)
    my = month_year_heatmap(data, cancel_token=cancel_token)
    return {"hw": hw, "my": my}





class MiniCard(MetricCard):
    """Backwards-compatible alias for the legacy analytics mini-card.

    Phase 1 migrated MiniCard onto the shared :class:`MetricCard` v2.
    The constructor keeps the historical ``(title, value, color, parent)``
    signature so the dozens of existing call-sites in ``analytics_tab``
    remain untouched. The sparkline / pin / explain features of v2 are
    available out-of-the-box.
    """

    def __init__(self, title: str, value: str = "—", color: str = "text", parent=None):
        super().__init__(title, value, color=color, parent=parent)
        formula, explanation = _ANALYTICS_CARD_HELP.get(title, ("", ""))
        if formula or explanation:
            self.set_formula(formula, explanation=explanation)


_ANALYTICS_CARD_HELP: dict[str, tuple[str, str]] = {
    "TOTAL RETURN": ("(final_equity / initial_equity - 1) * 100", "Shows net account growth. Use it with Max Drawdown; high return with deep drawdowns may be hard to trade live."),
    "FINAL EQUITY": ("initial_equity + cumulative_pnl", "Ending account value after closed trades. Compare with the equity curve shape, not only the final number."),
    "MAX DRAWDOWN": ("max(peak - equity) / peak * 100", "Worst equity decline from a prior high. Lower is better; large values are both capital and psychological risk."),
    "SHARPE RATIO": ("mean(returns) / std(returns)", "Risk-adjusted consistency estimate. Needs enough samples; use as directional evidence, not a standalone verdict."),
    "CALMAR RATIO": ("annualized_return / max_drawdown", "Return per unit of drawdown. Useful for comparing strategies with different volatility profiles."),
    "PROFIT FACTOR": ("gross_profit / gross_loss", "Above 1 means gross wins exceed gross losses. Watch for a high value caused by too few trades."),
    "TOTAL TRADES": ("count(closed_trades)", "Sample size. More trades usually makes the other metrics more reliable."),
    "BEST SESSION": ("max(session_total_pnl)", "Session with strongest net PnL. Use it to find where the strategy has real market fit."),
    "WORST SESSION": ("min(session_total_pnl)", "Session with weakest net PnL. Consider filtering or reducing risk in this regime."),
    "MOST ACTIVE": ("argmax(session_trade_count)", "Where the strategy trades most often. Activity without edge can increase costs."),
    "MAX WIN STREAK": ("max(consecutive winning trades)", "Best run length. Useful for expectation setting, not for extrapolating future wins."),
    "MAX LOSS STREAK": ("max(consecutive losing trades)", "Worst losing run. Use it for position sizing and psychological tolerance."),
    "CURRENT STREAK": ("latest consecutive wins/losses", "Current run state. Use it as context, not as a signal to chase or revenge trade."),
    "AVG WIN STREAK": ("mean(win streak lengths)", "Typical winning sequence length. Helps compare smoothness across strategies."),
    "AVG LOSS STREAK": ("mean(loss streak lengths)", "Typical losing sequence length. Higher values require lower risk per trade."),
    "TRADES": ("count(trades in selected range)", "Sample size for the custom range."),
    "WIN RATE": ("wins / trades * 100", "Win frequency. Pair it with payoff ratio or average PnL."),
    "TOTAL PnL": ("sum(pnl)", "Net profit/loss for the selected scope."),
    "AVG PnL": ("mean(pnl)", "Average expectancy in currency. Positive is the minimum viable condition."),
}


def _compute_r_multiples(data, cancel_token=None) -> dict:
    """Worker helper: compute R-multiples (AI-8)."""
    if hasattr(data, "get_r_multiples"):
        res = data.get_r_multiples()
    else:
        res = r_multiples(data)
    return {"res": res}


def _compute_attribution(data, cancel_token=None) -> dict:
    """Worker helper: compute attribution (AI-8)."""
    if hasattr(data, "get_attribution"):
        rows = data.get_attribution()
    else:
        rows = exit_reason_attribution(data)
    return {"rows": rows}


def _compute_mae_mfe(data, cancel_token=None) -> dict:
    """Worker helper: compute MAE/MFE (AI-8)."""
    if hasattr(data, "get_mae_mfe"):
        res = data.get_mae_mfe()
    else:
        res = mae_mfe_arrays(data)
    return {"res": res}


class AnalyticsTab(QWidget):
    drill_down_requested = Signal(dict)

    def __init__(self, engine):
        super().__init__()
        self.engine = engine
        self._refresh_active = True
        self._last_trade_key = None
        self._light_trade_key = None
        self._eq_has_data = False  # 🛡️ INIT: prevent AttributeError on first refresh
        self._period_kind: str = "ALL"
        self._period_start = None
        self._period_end = None

        self._empty_analytics_widget: QWidget | None = None
        self._empty_analytics_card: QLabel | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Top bar
        self._top_bar = QWidget()
        self._top_bar.setFixedHeight(44)
        tb = QHBoxLayout(self._top_bar)
        tb.setContentsMargins(16, 0, 16, 0)
        tb.setSpacing(8)

        self._title_lbl = QLabel("Analytics")
        tb.addWidget(self._title_lbl)
        tb.addStretch()

        self.btn_monte_carlo = QPushButton("Monte Carlo")
        self.btn_monte_carlo.setToolTip("Bootstrap + shock-injection Monte Carlo")
        self.btn_monte_carlo.setFixedHeight(30)
        apply_drop_shadow(self.btn_monte_carlo, blur=10, y_offset=1, alpha=60)
        self.btn_monte_carlo.clicked.connect(self._open_monte_carlo)
        tb.addWidget(self.btn_monte_carlo)

        self.btn_refresh = QPushButton("Refresh")
        self.btn_refresh.setFixedWidth(90)
        self.btn_refresh.clicked.connect(self.refresh_all)
        tb.addWidget(self.btn_refresh)

        layout.addWidget(self._top_bar)

        # Sub-tabs
        self.sub_tabs = QTabWidget()
        layout.addWidget(self.sub_tabs)

        # Empty state placeholder — Phase 1 Stabilization Step 1
        self._empty_state = EmptyState(
            icon="\U0001F52E",
            headline="The crystal ball is empty",
            subtitle="Backtest a strategy or upload your journal to unlock deep quantitative insights",
            cta_text="",
            parent=self,
        )
        self._empty_state.hide()
        layout.addWidget(self._empty_state)

        self._spinner = Spinner(self.sub_tabs, caption="Loading analytics…")
        self._spinner.cover(self.sub_tabs)

        # Track sub-tab background panes so theme switch repaints them.
        self._panel_widgets: list = []
        self._tables_with_overrides: list = []
        self._separator_frames: list[QFrame] = []
        self._range_result: QLabel | None = None
        self._merged_text_ref: QLabel | None = None

        self._active_drilldown: dict = {}
        self._section_captions: list = []
        self._summary_labels: list = []
        self._heatmap_worker = None
        self._heatmap_gen = 0
        self._heatmap_lock = threading.Lock()

        self._r_worker = None
        self._r_gen = 0
        self._r_lock = threading.Lock()

        self._attr_worker = None
        self._attr_gen = 0
        self._attr_lock = threading.Lock()

        self._mae_worker = None
        self._mae_gen = 0
        self._mae_lock = threading.Lock()

        # Lazy tab factories — only create heavy content on first activation
        self._tab_factories: dict = {}
        self._loaded_tabs: set = set()

        # First tab (Equity Curve) is always created upfront (visible by default)
        self._create_equity_tab()
        # All other tabs are lazy-initialized
        lazy_tabs = [
            ("Rolling", self._create_rolling_tab),
            ("Patterns", self._create_session_streak_tab),
            ("Advanced", self._create_advanced_tab),
            ("Heatmaps", self._create_heatmap_tab),
            ("Timeline", self._create_timeline_tab),
            ("MAE / MFE", self._create_mae_mfe_tab),
            ("R-multiples", self._create_r_multiples_tab),
            ("Attribution", self._create_attribution_tab),
            ("Slippage", self._create_slippage_tab),
            ("Custom Range", self._create_range_tab),
        ]
        for name, factory in lazy_tabs:
            placeholder = QWidget()
            ph_layout = QVBoxLayout(placeholder)
            ph_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
            ph_layout.setSpacing(8)
            ph_label = QLabel("Натисніть для завантаження…")
            ph_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            ph_label.setProperty("lazyPlaceholder", True)
            ph_label.setStyleSheet("color: #666; font-size: 13px;")
            ph_hint = QLabel(f"{name} will load on first open")
            ph_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
            ph_hint.setStyleSheet("color: #666; font-size: 11px;")
            ph_layout.addWidget(ph_label)
            ph_layout.addWidget(ph_hint)
            self.sub_tabs.addTab(placeholder, name)
            self._tab_factories[name] = factory

        self.sub_tabs.currentChanged.connect(self._lazy_load_tab)

        self._eq_trade_labels = []

        # Live (light) refresh during backtest — avoid re-running MC each trade.
        self._light_refresh_timer = QTimer(self)
        self._light_refresh_timer.setSingleShot(True)
        self._light_refresh_timer.setInterval(250)
        self._light_refresh_timer.timeout.connect(self.refresh_light)
        try:
            self.engine.trade_completed.connect(self._on_any_trade_completed)
        except Exception:
            pass

        self._apply_theme()
        register_theme_listener(self._apply_theme)

    def _on_any_trade_completed(self, *args):
        self._light_refresh_timer.start()

    def _currency_symbol(self) -> str:
        asset = getattr(self.engine, "asset_config", None)
        code = str(getattr(asset, "currency", "USD") or "USD").upper()
        return {"EUR": "€", "USD": "$", "GBP": "£", "JPY": "¥"}.get(code, "$")

    def _money(self, value: float, decimals: int = 0, compact: bool = False) -> str:
        sym = self._currency_symbol()
        v = float(value)
        av = abs(v)
        if compact and av >= 1_000_000:
            return f"{sym}{v / 1_000_000:+.{1 if decimals == 0 else decimals}f}M"
        if compact and av >= 10_000:
            return f"{sym}{v / 1_000:+.0f}k"
        return f"{sym}{v:+,.{decimals}f}"

    def _lazy_load_tab(self, index: int) -> None:
        widget = self.sub_tabs.widget(index)
        if widget is None:
            return
        tab_name = self.sub_tabs.tabText(index)
        if tab_name in self._loaded_tabs:
            return
        placeholder_label = widget.findChild(QLabel)
        if placeholder_label is not None:
            placeholder_label.setText("Loading…")
        self._loaded_tabs.add(tab_name)
        factory = self._tab_factories.get(tab_name)
        if factory is None:
            return
        real_tab = factory()
        self.sub_tabs.removeTab(index)
        self.sub_tabs.insertTab(index, real_tab, tab_name)
        self.sub_tabs.setCurrentIndex(index)
        self._panel_widgets.append(real_tab)
        self._apply_theme()
        try:
            self._refresh_loaded_tab(tab_name)
        except Exception:
            logger.exception("Analytics lazy tab refresh failed for %s", tab_name)

    def closeEvent(self, e):
        self._refresh_active = False
        unregister_theme_listener(self._apply_theme)
        self._light_refresh_timer.stop()
        try:
            self.engine.trade_completed.disconnect(self._on_any_trade_completed)
        except (RuntimeError, TypeError, AttributeError):
            pass
        prev = getattr(self, "_heatmap_worker", None)
        if prev is not None:
            try:
                slot = getattr(prev, "_heatmap_slot", None)
                if slot is not None:
                    prev.finished.disconnect(slot)
                prev.error.disconnect(self._on_heatmap_error)
            except (RuntimeError, TypeError):
                pass
            prev.cancel()
            prev.deleteLater()
            self._heatmap_worker = None
        if hasattr(self, "_heatmap_spinner") and self._heatmap_spinner is not None:
            try:
                self._heatmap_spinner.stop()
            except Exception:
                pass
            self._heatmap_spinner = None
        super().closeEvent(e)

    def _create_equity_tab(self):
        """Create equity curve tab with drawdown overlay (scrollable)."""
        eq_w = QWidget()
        self._panel_widgets.append(eq_w)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        inner = QWidget()
        l = QVBoxLayout(inner)
        l.setContentsMargins(12, 8, 12, 8)
        l.setSpacing(6)

        # Mini summary cards
        cards_row = QHBoxLayout()
        cards_row.setSpacing(6)
        self.eq_card_return = MiniCard("TOTAL RETURN", "\u2014", "text")
        self.eq_card_equity = MiniCard("FINAL EQUITY", "\u2014", "accent_light")
        self.eq_card_maxdd = MiniCard("MAX DRAWDOWN", "\u2014", "red")
        self.eq_card_sharpe = MiniCard("SHARPE RATIO", "\u2014", "amber")
        self.eq_card_calmar = MiniCard("CALMAR RATIO", "\u2014", "amber")
        self.eq_card_pf = MiniCard("PROFIT FACTOR", "\u2014", "green")
        self.eq_card_trades = MiniCard("TOTAL TRADES", "\u2014", "text")
        for c in [
            self.eq_card_return,
            self.eq_card_equity,
            self.eq_card_maxdd,
            self.eq_card_sharpe,
            self.eq_card_calmar,
            self.eq_card_pf,
            self.eq_card_trades,
        ]:
            cards_row.addWidget(c)
        l.addLayout(cards_row)

        cap_eq = QLabel("\u25C6  Equity Curve")
        self._section_captions.append(cap_eq)
        l.addWidget(cap_eq)

        # Equity plot
        self.equity_plot = pg.PlotWidget()
        self.equity_plot.getAxis("left").setWidth(70)
        self.equity_plot.setLabel("left", "Equity ($)", size="10pt")
        self.equity_plot.setMinimumHeight(280)

        # Holographic equity curve — 3px gradient stroke + dual-layer glow fill
        self.equity_curve = self.equity_plot.plot()
        self.equity_baseline = pg.PlotDataItem(pen=None)
        self.equity_plot.addItem(self.equity_baseline)
        # Premium gradient fill — two layers for depth
        self.equity_fill = pg.FillBetweenItem(self.equity_curve, self.equity_baseline)
        self.equity_plot.addItem(self.equity_fill)
        # Second gradient fill — lighter, for ambient glow near the line
        self._eq_fill_glow = pg.FillBetweenItem(self.equity_curve, self.equity_baseline)
        self._eq_fill_glow.setZValue(-5)
        self.equity_plot.addItem(self._eq_fill_glow)
        # Third holographic fill — outermost soft glow (alpha ~8%)
        self._eq_fill_halo = pg.FillBetweenItem(self.equity_curve, self.equity_baseline)
        self._eq_fill_halo.setZValue(-10)
        self.equity_plot.addItem(self._eq_fill_halo)

        # Peak curve
        self.peak_curve = self.equity_plot.plot()

        # Initial equity reference line
        self._eq_initial_line = pg.InfiniteLine(pos=0, angle=0, pen=None)
        self.equity_plot.addItem(self._eq_initial_line)

        # Crosshair
        self._eq_cross_v = pg.InfiniteLine(angle=90, movable=False)
        self._eq_cross_v.setZValue(30)
        self._eq_cross_v.hide()
        self.equity_plot.addItem(self._eq_cross_v)
        self._eq_cross_h = pg.InfiniteLine(angle=0, movable=False)
        self._eq_cross_h.setZValue(30)
        self._eq_cross_h.hide()
        self.equity_plot.addItem(self._eq_cross_h)
        self._eq_mouse_throttle = QTimer(self)
        self._eq_mouse_throttle.setSingleShot(True)
        self._eq_mouse_throttle.setInterval(16)
        self._eq_mouse_throttle.timeout.connect(self._flush_eq_mouse)
        self._eq_mouse_pending = None
        self.equity_plot.scene().sigMouseMoved.connect(self._queue_eq_mouse)

        l.addWidget(self.equity_plot)

        self.drawdown_plot = pg.PlotWidget()
        self.drawdown_plot.getAxis("left").setWidth(70)
        self.drawdown_plot.setLabel("left", "Drawdown (%)", size="9pt")
        self.drawdown_plot.setLabel("bottom", "Trade #", size="9pt")
        self.drawdown_plot.setMinimumHeight(140)
        self.drawdown_plot.setMaximumHeight(210)
        self.drawdown_curve = self.drawdown_plot.plot()
        self.drawdown_zero = pg.PlotDataItem(pen=None)
        self.drawdown_plot.addItem(self.drawdown_zero)
        self.drawdown_fill = pg.FillBetweenItem(self.drawdown_curve, self.drawdown_zero)
        self.drawdown_plot.addItem(self.drawdown_fill)
        l.addWidget(self.drawdown_plot)
        l.addStretch()
        scroll.setWidget(inner)
        wl = QVBoxLayout(eq_w)
        wl.setContentsMargins(0, 0, 0, 0)
        wl.addWidget(scroll)
        self.sub_tabs.addTab(eq_w, "Equity Curve")

    def _create_session_streak_tab(self):
        """Create merged Sessions + Streaks tab (scrollable)."""
        w = QWidget()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        inner = QWidget()
        l = QVBoxLayout(inner)
        l.setContentsMargins(12, 8, 12, 8)
        l.setSpacing(8)

        # ── Combined cards row (sessions + streaks) ──
        cards = QHBoxLayout()
        cards.setSpacing(6)
        self.sess_card_best = MiniCard("BEST SESSION", "\u2014", "green")
        self.sess_card_worst = MiniCard("WORST SESSION", "\u2014", "red")
        self.sess_card_active = MiniCard("MOST ACTIVE", "\u2014", "accent_light")
        self.str_card_maxw = MiniCard("MAX WIN STREAK", "\u2014", "green")
        self.str_card_maxl = MiniCard("MAX LOSS STREAK", "\u2014", "red")
        self.str_card_cur = MiniCard("CURRENT STREAK", "\u2014", "text")
        self.str_card_avgw = MiniCard("AVG WIN STREAK", "\u2014", "green")
        self.str_card_avgl = MiniCard("AVG LOSS STREAK", "\u2014", "red")
        for c in [
            self.sess_card_best, self.sess_card_worst, self.sess_card_active,
            self.str_card_maxw, self.str_card_maxl, self.str_card_cur,
            self.str_card_avgw, self.str_card_avgl,
        ]:
            c.setMinimumWidth(150)
            c.setMinimumHeight(88)
            cards.addWidget(c)
        l.addLayout(cards)

        # ── Plot ──
        self.session_plot = pg.PlotWidget()
        self.session_plot.setMaximumHeight(170)
        self.session_plot.setLabel("left", "Total PnL ($)", size="9pt")
        l.addWidget(self.session_plot)

        # ── Separator ──
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        l.addWidget(sep)

        # ── Toggle buttons for tables ──
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        self._btn_sessions = QPushButton("\u25bc Session Details")
        self._btn_sessions.setCheckable(True)
        self._btn_sessions.setFixedHeight(30)
        self._btn_streaks = QPushButton("\u25bc Streak Details")
        self._btn_streaks.setCheckable(True)
        self._btn_streaks.setFixedHeight(30)
        apply_drop_shadow(self._btn_sessions, blur=8, y_offset=1, alpha=50)
        apply_drop_shadow(self._btn_streaks, blur=8, y_offset=1, alpha=50)
        for btn in [self._btn_sessions, self._btn_streaks]:
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn_row.addWidget(btn)
        btn_row.addStretch()
        l.addLayout(btn_row)

        # ── Session table (togglable) ──
        self._session_table_wrapper = QWidget()
        self._session_table_wrapper.setVisible(False)
        stl = QVBoxLayout(self._session_table_wrapper)
        stl.setContentsMargins(0, 0, 0, 0)
        self.session_table = QTableView()
        self.session_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.session_table.setAlternatingRowColors(True)
        self.session_table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._tables_with_overrides.append(self.session_table)
        self._session_model = SimpleTableModel(
            ["Session", "Trades", "Wins", "Losses", "WR%", "Avg PnL", "Total PnL", "Best Trade"],
            [],
            parent=self,
        )
        self.session_table.setModel(self._session_model)
        stl.addWidget(self.session_table)
        l.addWidget(self._session_table_wrapper)

        # ── Streak table (togglable) ──
        self._streak_table_wrapper = QWidget()
        self._streak_table_wrapper.setVisible(False)
        skl = QVBoxLayout(self._streak_table_wrapper)
        skl.setContentsMargins(0, 0, 0, 0)
        self.streak_table = QTableView()
        self.streak_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.streak_table.setAlternatingRowColors(True)
        self.streak_table.setMaximumHeight(220)
        self._tables_with_overrides.append(self.streak_table)

        def _streak_style(r: int, c: int, v: str) -> dict:
            if c in (0, 1, 4):
                if str(v).startswith("WIN") or str(v).startswith("W"):
                    return {"fg": sh.c(get_theme(), "green_bright")}
                if str(v).startswith("LOSS") or str(v).startswith("L"):
                    return {"fg": sh.c(get_theme(), "red_bright")}
            return {}

        self._streak_model = SimpleTableModel(
            ["Type", "Length", "Start #", "End #", "Total PnL"],
            [],
            parent=self,
            cell_style_fn=_streak_style,
        )
        self.streak_table.setModel(self._streak_model)
        skl.addWidget(self.streak_table)
        l.addWidget(self._streak_table_wrapper)

        # ── Wire toggle buttons ──
        self._btn_sessions.toggled.connect(self._toggle_session_table)
        self._btn_streaks.toggled.connect(self._toggle_streak_table)

        l.addStretch()
        scroll.setWidget(inner)
        wl = QVBoxLayout(w)
        wl.setContentsMargins(0, 0, 0, 0)
        wl.addWidget(scroll)
        return w

    def _toggle_session_table(self, checked: bool):
        self._session_table_wrapper.setVisible(checked)
        self._btn_sessions.setText("\u25b2 Session Details" if checked else "\u25bc Session Details")
        self._style_toggle_btn(self._btn_sessions, checked)
        if checked:
            self.session_table.setMaximumHeight(16777215)

    def _toggle_streak_table(self, checked: bool):
        self._streak_table_wrapper.setVisible(checked)
        self._btn_streaks.setText("\u25b2 Streak Details" if checked else "\u25bc Streak Details")
        self._style_toggle_btn(self._btn_streaks, checked)
        if checked:
            self.streak_table.setMaximumHeight(220)

    @staticmethod
    def _style_toggle_btn(btn: QPushButton, checked: bool) -> None:
        t = get_theme()
        if checked:
            btn.setStyleSheet(
                f"QPushButton {{ background: {sh.c(t, 'elevated')}; color: {sh.c(t, 'accent_light')}; "
                f"font-weight: 700; border: 1px solid {sh.c(t, 'accent')}; border-radius: 4px; padding: 0 12px; }}"
            )
        else:
            btn.setStyleSheet(
                f"QPushButton {{ background: {sh.c(t, 'surface2')}; color: {sh.c(t, 'text')}; "
                f"font-weight: 600; border: 1px solid {sh.c(t, 'border2')}; border-radius: 4px; padding: 0 12px; }}"
                f"QPushButton:hover {{ background: {sh.c(t, 'surface_hover')}; }}"
            )

    def _create_range_tab(self):
        """Create custom range WR tab."""
        rng_w = QWidget()
        rng_l = QVBoxLayout(rng_w)
        rng_l.setContentsMargins(12, 8, 12, 8)
        rng_l.setSpacing(8)

        dl = QHBoxLayout()
        dl.addWidget(QLabel("From:"))
        self.date_from = QDateEdit()
        self.date_from.setCalendarPopup(True)
        self.date_from.setDate(QDate(2021, 1, 1))
        dl.addWidget(self.date_from)
        dl.addWidget(QLabel("To:"))
        self.date_to = QDateEdit()
        self.date_to.setCalendarPopup(True)
        self.date_to.setDate(QDate.currentDate())
        dl.addWidget(self.date_to)
        btn_calc = QPushButton("Calculate")
        btn_calc.clicked.connect(self.calc_custom_range)
        dl.addWidget(btn_calc)
        dl.addStretch()
        rng_l.addLayout(dl)

        self.range_cards_row = QHBoxLayout()
        self.range_cards_row.setSpacing(6)
        self.rng_card_trades = MiniCard("TRADES", "—", "text")
        self.rng_card_wr = MiniCard("WIN RATE", "—", "green")
        self.rng_card_pnl = MiniCard("TOTAL PnL", "—", "accent_light")
        self.rng_card_avg = MiniCard("AVG PnL", "—", "text")
        self.rng_card_pf = MiniCard("PROFIT FACTOR", "—", "amber")
        for c in [
            self.rng_card_trades,
            self.rng_card_wr,
            self.rng_card_pnl,
            self.rng_card_avg,
            self.rng_card_pf,
        ]:
            self.range_cards_row.addWidget(c)
        self.range_cards_row.addStretch()
        rng_l.addLayout(self.range_cards_row)

        self.range_result = QLabel("Select a date range and click Calculate.")
        self._range_result = self.range_result
        self.range_result.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.range_result.setWordWrap(True)
        rng_l.addWidget(self.range_result)
        rng_l.addStretch()
        return rng_w

    def _create_advanced_tab(self):
        """Advanced tab: two compact plots + toggleable merged analytics."""
        adv_w = QWidget()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        inner = QWidget()
        l = QVBoxLayout(inner)
        l.setContentsMargins(12, 8, 12, 8)
        l.setSpacing(8)

        cap_dist = QLabel("\u25A6  PnL Distribution")
        self._section_captions.append(cap_dist)
        l.addWidget(cap_dist)

        self.dist_plot = pg.PlotWidget()
        self.dist_plot.setLabel("left", "Frequency", size="9pt")
        self.dist_plot.setLabel("bottom", "PnL ($)", size="9pt")
        self.dist_plot.setMaximumHeight(160)
        l.addWidget(self.dist_plot)

        cap_dur = QLabel("\u25A6  Duration Distribution")
        self._section_captions.append(cap_dur)
        l.addWidget(cap_dur)

        self.duration_plot = pg.PlotWidget()
        self.duration_plot.setLabel("left", "Frequency", size="9pt")
        self.duration_plot.setLabel("bottom", "Duration (min)", size="9pt")
        self.duration_plot.setMaximumHeight(160)
        l.addWidget(self.duration_plot)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        l.addWidget(sep)

        self._btn_adv = QPushButton("\u25bc Advanced Trade Analytics")
        self._btn_adv.setCheckable(True)
        self._btn_adv.setFixedHeight(30)
        self._btn_adv.setCursor(Qt.CursorShape.PointingHandCursor)
        apply_drop_shadow(self._btn_adv, blur=8, y_offset=1, alpha=50)
        l.addWidget(self._btn_adv)

        self._adv_wrapper = QWidget()
        self._adv_wrapper.setVisible(False)
        wl_adv = QVBoxLayout(self._adv_wrapper)
        wl_adv.setContentsMargins(0, 0, 0, 0)
        self.merged_text = QLabel("Advanced analytics will appear after a run.")
        self.merged_text.setTextFormat(Qt.TextFormat.RichText)
        self.merged_text.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.merged_text.setWordWrap(True)
        self._merged_text_ref = self.merged_text
        wl_adv.addWidget(self.merged_text)
        l.addWidget(self._adv_wrapper)

        self._btn_adv.toggled.connect(self._toggle_advanced_text)

        l.addStretch()
        scroll.setWidget(inner)
        wl = QVBoxLayout(adv_w)
        wl.setContentsMargins(0, 0, 0, 0)
        wl.addWidget(scroll)
        return adv_w

    def _toggle_advanced_text(self, checked: bool):
        self._adv_wrapper.setVisible(checked)
        self._btn_adv.setText("\u25b2 Advanced Trade Analytics" if checked else "\u25bc Advanced Trade Analytics")
        self._style_toggle_btn(self._btn_adv, checked)

    # ═══════════════════════════════════════════════════════════════ Feature 4
    def _create_rolling_tab(self):
        w = QWidget()
        l = QVBoxLayout(w)
        l.setContentsMargins(12, 8, 12, 8)
        l.setSpacing(8)

        ctrl = QHBoxLayout()
        ctrl.addWidget(QLabel("Window:"))
        self.roll_window_cb = QComboBox()
        self.roll_window_cb.addItems(["20", "50", "100", "200"])
        self.roll_window_cb.setCurrentText("50")
        self.roll_window_cb.currentTextChanged.connect(lambda _: self._update_rolling_metrics())
        ctrl.addWidget(self.roll_window_cb)
        ctrl.addStretch()
        l.addLayout(ctrl)

        self.roll_sharpe_plot = pg.PlotWidget()
        self.roll_sharpe_plot.setLabel("left", "Rolling Sharpe", size="9pt")
        self.roll_sharpe_plot.setLabel("bottom", "Trade #", size="9pt")
        l.addWidget(self.roll_sharpe_plot, stretch=1)

        self.roll_wr_plot = pg.PlotWidget()
        self.roll_wr_plot.setLabel("left", "Rolling Win Rate", size="9pt")
        self.roll_wr_plot.setLabel("bottom", "Trade #", size="9pt")
        self.roll_wr_plot.setMaximumHeight(200)
        l.addWidget(self.roll_wr_plot)

        return w

    def _create_heatmap_tab(self):
        """Create merged heatmap tab: Calendar + Hour×Weekday + Year×Month."""
        w = QWidget()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        inner = QWidget()
        l = QVBoxLayout(inner)
        l.setContentsMargins(12, 8, 12, 8)
        l.setSpacing(10)

        # ── Calendar section ──
        cal_cap = QLabel("▣  Daily PnL Calendar  ·  Terminal Style")
        self._section_captions.append(cal_cap)
        l.addWidget(cal_cap)

        nav = QHBoxLayout()
        nav.setSpacing(8)
        self.cal_prev_btn = QPushButton("\u25C0  Prev")
        self.cal_prev_btn.setFixedHeight(28)
        self.cal_prev_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        apply_drop_shadow(self.cal_prev_btn, blur=6, y_offset=1, alpha=40)
        self.cal_prev_btn.clicked.connect(self._cal_nav_prev)
        nav.addWidget(self.cal_prev_btn)
        self.cal_year_lbl = QLabel("")
        self.cal_year_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.cal_year_lbl.setStyleSheet("font-weight: 700; font-size: 14px;")
        nav.addWidget(self.cal_year_lbl, stretch=1)
        self.cal_next_btn = QPushButton("Next  \u25B6")
        self.cal_next_btn.setFixedHeight(28)
        self.cal_next_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        apply_drop_shadow(self.cal_next_btn, blur=6, y_offset=1, alpha=40)
        self.cal_next_btn.clicked.connect(self._cal_nav_next)
        nav.addWidget(self.cal_next_btn)
        l.addLayout(nav)

        self.cal_plot = pg.PlotWidget()
        self.cal_plot.setLabel("left", "Month", size="9pt")
        self.cal_plot.setLabel("bottom", "Day", size="9pt")
        self.cal_plot.setMouseEnabled(False, False)
        self.cal_plot.hideButtons()
        self.cal_plot.setMinimumHeight(300)
        self.cal_plot.setMaximumHeight(400)
        self.cal_image = pg.ImageItem(axisOrder="row-major")
        self.cal_plot.addItem(self.cal_image)
        l.addWidget(self.cal_plot)

        self.cal_summary = QLabel("")
        self._summary_labels.append(self.cal_summary)
        l.addWidget(self.cal_summary)

        self._cal_year = None

        # ── Quant Heatmaps section ──
        sep1 = QFrame()
        sep1.setFrameShape(QFrame.Shape.HLine)
        self._separator_frames.append(sep1)
        l.addWidget(sep1)

        drill_row = QHBoxLayout()
        cap_hm = QLabel("▣  Heatmaps  —  click any cell to drill down into Trades")
        self._section_captions.append(cap_hm)
        drill_row.addWidget(cap_hm)
        drill_row.addStretch()
        self._drill_lbl = QLabel("")
        self._summary_labels.append(self._drill_lbl)
        self._drill_lbl.hide()
        drill_row.addWidget(self._drill_lbl)
        self._btn_clear_drill = QPushButton("\u2716  Clear drill-down")
        self._btn_clear_drill.setFixedHeight(28)
        self._btn_clear_drill.setCursor(Qt.CursorShape.PointingHandCursor)
        apply_drop_shadow(self._btn_clear_drill, blur=6, y_offset=1, alpha=40)
        self._btn_clear_drill.clicked.connect(self._clear_drilldown)
        self._btn_clear_drill.hide()
        drill_row.addWidget(self._btn_clear_drill)
        l.addLayout(drill_row)

        sym = self._currency_symbol()
        cap_hw = QLabel(f"\u25B6  Hour \u00D7 Weekday  —  Net PnL ({sym})")
        self._section_captions.append(cap_hw)
        l.addWidget(cap_hw)
        self.hw_heatmap = _Heatmap()
        self.hw_heatmap.setMinimumHeight(280)
        self.hw_heatmap.set_outlier_softening(True)
        self.hw_heatmap.set_currency_symbol(sym)
        self.hw_heatmap.cell_clicked.connect(self._on_hw_heatmap_click)
        l.addWidget(self.hw_heatmap)

        cap_my = QLabel(f"\u25B6  Year \u00D7 Month  —  Net PnL ({sym})")
        self._section_captions.append(cap_my)
        l.addWidget(cap_my)
        self.my_heatmap = _Heatmap()
        self.my_heatmap.setMinimumHeight(220)
        self.my_heatmap.set_outlier_softening(True)
        self.my_heatmap.set_currency_symbol(sym)
        self.my_heatmap.cell_clicked.connect(self._on_my_heatmap_click)
        l.addWidget(self.my_heatmap)

        l.addStretch()
        scroll.setWidget(inner)
        scroll_l = QVBoxLayout(w)
        scroll_l.setContentsMargins(0, 0, 0, 0)
        scroll_l.addWidget(scroll)
        self._heatmap_tab_w = w
        return w

    # ═══════════════════════════════════════════════════════════════ Feature 8
    def _create_timeline_tab(self):
        w = QWidget()
        l = QVBoxLayout(w)
        l.setContentsMargins(12, 8, 12, 8)
        l.setSpacing(8)

        self.timeline_plot = pg.PlotWidget()
        self.timeline_plot.setLabel("left", "PnL ($)", size="9pt")
        self.timeline_plot.setLabel("bottom", "Trade", size="9pt")
        l.addWidget(self.timeline_plot, stretch=1)

        self.timeline_dur_plot = pg.PlotWidget()
        self.timeline_dur_plot.setLabel("left", "Duration (min)", size="9pt")
        self.timeline_dur_plot.setLabel("bottom", "Trade", size="9pt")
        self.timeline_dur_plot.setMaximumHeight(180)
        l.addWidget(self.timeline_dur_plot)

        return w

    def _apply_theme(self):
        """Re-apply all custom stylesheets from the active palette.

        Called once at construction and again whenever the user flips the
        theme (via :func:`gui.theme.register_theme_listener`). Builds every
        QSS string from :func:`get_theme` so the Light/Dark switch is fully
        artefact-free.
        """
        t = get_theme()
        self._top_bar.setStyleSheet(sh.top_bar(t))
        self._title_lbl.setStyleSheet(sh.label_title(t))
        for btn_name in ("btn_monte_carlo", "btn_refresh", "cal_prev_btn", "cal_next_btn", "_btn_clear_drill"):
            btn = getattr(self, btn_name, None)
            if btn is not None:
                btn.setStyleSheet(sh.btn_default(t))
        self.sub_tabs.setStyleSheet(sh.sub_tabs(t))
        for w in self._panel_widgets:
            w.setStyleSheet(sh.panel(t))
        for tbl in self._tables_with_overrides:
            tbl.setStyleSheet(sh.analytics_table(t))
        if self._range_result is not None:
            self._range_result.setStyleSheet(sh.info_block_muted(t))
        if self._merged_text_ref is not None:
            self._merged_text_ref.setStyleSheet(sh.info_block(t))
        # Plot backgrounds re-tint with the theme bg.
        for cap in self._section_captions:
            cap.setStyleSheet(
                f"color: {sh.c(t,'text2')}; font-size: 11px; font-weight: 600;"
                f" border-left: 3px solid {sh.c(t,'accent')};"
                f" padding-left: 8px; background: transparent;"
            )
        for s in self._summary_labels:
            s.setStyleSheet(sh.summary_label(t))
        sep_color = QColor(sh.c(t, "border"))
        sep_color.setAlpha(80)
        for sep in self._separator_frames:
            sep.setStyleSheet(
                "QFrame { border: none; border-top: 1px solid "
                f"rgba({sep_color.red()}, {sep_color.green()}, {sep_color.blue()}, {sep_color.alpha()}); }}"
            )
        for plot_attr in (
            "equity_plot", "session_plot",
            "drawdown_plot",
            "dist_plot", "duration_plot",
            "roll_sharpe_plot", "roll_wr_plot",
            "timeline_plot", "timeline_dur_plot",
            "cal_plot",
            "mae_mfe_plot", "mae_hist", "r_hist", "r_cum", "slip_plot",
        ):
            plot = getattr(self, plot_attr, None)
            if plot is not None:
                try:
                    plot.setBackground(QColor(sh.c(t, "chart_bg")))
                    plot.showGrid(x=True, y=True, alpha=0.12)
                    for axis_name in ["left", "bottom"]:
                        axis = plot.getAxis(axis_name)
                        if axis:
                            axis.setPen(pg.mkPen(QColor(sh.c(t, "chart_axis"))))
                            axis.setTextPen(pg.mkPen(QColor(sh.c(t, "chart_axis_text"))))
                            axis.labelStyle = {"color": sh.c(t, "chart_axis_text")}
                            axis.setLabel(**axis.labelStyle)  # trigger update
                except Exception:
                    pass

        for hm_attr in ("cal_plot", "hw_heatmap", "my_heatmap"):
            hm = getattr(self, hm_attr, None)
            if hm is not None:
                try:
                    hm.setBackground(QColor(sh.c(t, "chart_bg")))
                    # Update color bar axis pen if available
                    if hasattr(hm, "_cb_axis") and hm._cb_axis is not None:
                        hm._cb_axis.setPen(pg.mkPen(QColor(sh.c(t, "chart_axis_text")), width=1))
                        hm._cb_axis.setTextPen(QColor(sh.c(t, "chart_axis_text")))
                    # Update highlight pen if available
                    if hasattr(hm, "_highlight"):
                        hm._highlight.setPen(pg.mkPen(QColor(sh.c(t, "accent_light")), width=2))
                except Exception:
                    pass

        # Update specific curves and items
        if hasattr(self, "equity_curve"):
            self.equity_curve.setPen(pg.mkPen(QColor(sh.c(t, "chart_equity_line")), width=2.5))
            brush_eq = QColor(sh.c(t, "chart_equity_fill"))
            brush_eq.setAlpha(40)
            self.equity_fill.setBrush(pg.mkBrush(brush_eq))
            # Premium: ambient glow fill — lighter, fading to just above baseline
            if hasattr(self, "_eq_fill_glow"):
                glow_col = QColor(sh.c(t, "chart_equity_line"))
                glow_col.setAlpha(18)
                self._eq_fill_glow.setBrush(pg.mkBrush(glow_col))
            self.peak_curve.setPen(
                pg.mkPen(QColor(sh.c(t, "chart_peak_line")), width=1, style=Qt.PenStyle.DotLine)
            )
            dd_line = pg.mkPen(QColor(sh.c(t, "chart_dd_line")), width=1.8)
            brush_dd = QColor(sh.c(t, "chart_dd_fill"))
            brush_dd.setAlpha(70)
            if hasattr(self, "drawdown_curve"):
                self.drawdown_curve.setPen(dd_line)
                self.drawdown_fill.setBrush(pg.mkBrush(brush_dd))
            if hasattr(self, "drawdown_zero"):
                zc = QColor(sh.c(t, "text3"))
                zc.setAlpha(110)
                self.drawdown_zero.setPen(pg.mkPen(zc, width=1, style=Qt.PenStyle.DashLine))
            # Crosshair
            if hasattr(self, "_eq_cross_v"):
                pen_cross = pg.mkPen(QColor(sh.c(t, "chart_axis_text")), width=1, style=Qt.PenStyle.DotLine)
                self._eq_cross_v.setPen(pen_cross)
                self._eq_cross_h.setPen(pen_cross)
            # Initial equity reference line
            if hasattr(self, "_eq_initial_line"):
                self._eq_initial_line.setPen(pg.mkPen(QColor(sh.c(t, "text3")), width=1, style=Qt.PenStyle.DashLine))
            # Theme colors are updated above via setPen/setBrush — no data recompute needed

    def _open_monte_carlo(self):
        """Open the bootstrap + shock-injection Monte Carlo dialog."""
        from gui.dialogs.advanced_mc_dialog import AdvancedMonteCarloDialog

        dialog = AdvancedMonteCarloDialog(self.engine, self)
        dialog.exec()

    def refresh_all(self, force: bool = False) -> None:
        if not self._is_safe_for_refresh():
            return
        trades = list(getattr(self.engine, "trades", []) or [])
        key = _trade_cache_key(trades, float(getattr(self.engine, "initial_equity", 0.0) or 0.0))
        if not force and hasattr(self, "_last_trade_key") and key == self._last_trade_key:
            logger.debug("Analytics refresh_all skip key=%r", key)
            return

        if self._spinner is not None and not self._lazy_spinner_active():
            self._spinner.start()
        ok = True
        try:
            ok &= self._safe_update("equity_curve", self._update_equity_curve)
            ok &= self._safe_update("session_matrix", self._update_session_matrix, "session_plot")
            ok &= self._safe_update("streak_tracker", self._update_streak_tracker, "str_card_maxw")
            ok &= self._safe_update("calendar_heatmap", self._update_calendar_heatmap, "cal_plot")
            ok &= self._safe_update("trade_timeline", self._update_trade_timeline, "timeline_plot")

            pnl_arr = np.array([t.pnl for t in trades], dtype=float) if trades else None
            ok &= self._safe_update("advanced_stats", lambda: self._update_advanced_stats(pnl_arr), "dist_plot")
            ok &= self._safe_update("extended_metrics", lambda: self._update_extended_metrics(pnl_arr), "duration_plot")
            ok &= self._safe_update("rolling_metrics", self._update_rolling_metrics, "roll_sharpe_plot")
            ok &= self._safe_update("attribution", self._update_attribution, "_dd_model")
            ok &= self._safe_update("slippage", self._update_slippage, "slip_plot")
            ok &= self._safe_update("mae_mfe", self._update_mae_mfe, "mae_mfe_plot")
            ok &= self._safe_update("r_multiples", self._update_r_multiples, "r_hist")
            ok &= self._safe_update("heatmaps", self._update_heatmaps)
            if ok:
                self._last_trade_key = key
            else:
                logger.warning("Analytics refresh_all not cached because a loaded panel failed key=%r", key)
        finally:
            if self._spinner is not None:
                self._spinner.stop()

    def _is_safe_for_refresh(self) -> bool:
        try:
            return self._refresh_active and self.isVisible()
        except RuntimeError:
            return False

    def _safe_update(self, name: str, fn, guard_attr: str | None = None) -> bool:
        if guard_attr is not None and not hasattr(self, guard_attr):
            return True  # sub-tab not yet lazy-loaded
        try:
            fn()
            return True
        except Exception:
            logger.exception("Analytics panel %r failed - skipping", name)
            return False

    def _refresh_loaded_tab(self, tab_name: str) -> None:
        trades = list(getattr(self.engine, "trades", []) or [])
        pnl_arr = np.array([t.pnl for t in trades], dtype=float) if trades else None
        dispatch = {
            "Rolling": ("rolling_metrics", self._update_rolling_metrics),
            "Patterns": ("patterns", lambda: (
                self._update_session_matrix(),
                self._update_streak_tracker(),
            )),
            "Advanced": ("advanced", lambda: (
                self._update_advanced_stats(pnl_arr),
                self._update_extended_metrics(pnl_arr),
            )),
            "Heatmaps": ("heatmaps", lambda: (
                self._update_calendar_heatmap(),
                self._update_heatmaps(),
            )),
            "Timeline": ("timeline", self._update_trade_timeline),
            "MAE / MFE": ("mae_mfe", self._update_mae_mfe),
            "R-multiples": ("r_multiples", self._update_r_multiples),
            "Attribution": ("attribution", self._update_attribution),
            "Slippage": ("slippage", self._update_slippage),
            "Custom Range": ("custom_range", self.calc_custom_range),
        }
        item = dispatch.get(tab_name)
        if item is None:
            return
        name, fn = item
        self._safe_update(name, fn)

    def _lazy_spinner_active(self) -> bool:
        sp = getattr(self, "_heatmap_spinner", None)
        try:
            return bool(sp is not None and sp.isVisible())
        except Exception:
            return sp is not None

    def refresh_light(self, force: bool = False) -> None:
        """Lightweight refresh used during live backtest.

        Updates only fast panels; skips histograms, heatmaps, MC.
        Uses separate key so it does NOT block full refresh_all.
        """
        trades = list(getattr(self.engine, "trades", []) or [])
        key = _trade_cache_key(trades, float(getattr(self.engine, "initial_equity", 0.0) or 0.0))
        if not force and hasattr(self, "_light_trade_key") and key == self._light_trade_key:
            logger.debug("Analytics refresh_light skip key=%r", key)
            return
        if self._spinner is not None and self.isVisible() and not self._lazy_spinner_active():
            self._spinner.start("Updating analytics...")
        ok = True
        try:
            ok &= self._safe_update("equity_curve", self._update_equity_curve)
            ok &= self._safe_update("session_matrix", self._update_session_matrix, "session_plot")
            ok &= self._safe_update("streak_tracker", self._update_streak_tracker, "str_card_maxw")
            ok &= self._safe_update("calendar_heatmap", self._update_calendar_heatmap, "cal_plot")
            ok &= self._safe_update("trade_timeline", self._update_trade_timeline, "timeline_plot")
            ok &= self._safe_update("attribution", self._update_attribution, "_dd_model")
            if ok:
                self._light_trade_key = key
            else:
                logger.warning("Analytics refresh_light not cached because a loaded panel failed key=%r", key)
        finally:
            if self._spinner is not None:
                self._spinner.stop()

    def _resolve_period_window(self) -> tuple[np.datetime64 | None, np.datetime64 | None]:
        """Phase 1 stabilization — use centralized period resolver with historical anchoring (AI-1, AI-16)."""
        trades = list(getattr(self.engine, "trades", []) or [])
        anchor = None
        if trades:
            try:
                # Use max entry time as anchor for historical relative presets
                ets = [getattr(t, "entry_time_np", None) or pd.to_datetime(t.entry_time) for t in trades]
                anchor = np.max(ets)
                if pd.isna(anchor):
                    anchor = None
                else:
                    anchor = np.datetime64(anchor)
            except Exception:
                pass
        return resolve_period_window(self._period_kind, self._period_start, self._period_end, anchor=anchor)

    def _sorted_trades(self):
        """Centralised access to engine trades, sorted and period-filtered (AI-1)."""
        cache = getattr(self.engine, "_shared_cache", None)
        if cache is not None:
            trades = cache.get_sorted_trades()
        else:
            trades = list(getattr(self.engine, "trades", []) or [])
            if not trades:
                return []
            try:
                ets = [getattr(t, "entry_time_np", None) or pd.to_datetime(t.entry_time) for t in trades]
                order = np.argsort(ets)
                trades = [trades[i] for i in order]
            except Exception:
                trades = sorted(trades, key=lambda t: str(getattr(t, "entry_time", "")))

        start, end = self._resolve_period_window()
        return filter_trades_by_period(trades, start, end)

    def _show_no_trades_state(self) -> None:
        """Unify empty-state UX across analytics sub-panels.

        Target UX text:
          - Title: No trades yet
          - Subtitle: Run a strategy or load a journal to populate this table
        """
        trades = list(getattr(self.engine, "trades", []) or [])
        if trades:
            self._empty_state.hide()
            self.sub_tabs.show()
            return

        self.sub_tabs.hide()
        self._empty_state.show()

        # Ensure all visible mini-cards are reset.
        for c in [
            getattr(self, "eq_card_return", None),
            getattr(self, "eq_card_equity", None),
            getattr(self, "eq_card_maxdd", None),
            getattr(self, "eq_card_sharpe", None),
            getattr(self, "eq_card_calmar", None),
            getattr(self, "eq_card_pf", None),
            getattr(self, "eq_card_trades", None),
            getattr(self, "sess_card_best", None),
            getattr(self, "sess_card_worst", None),
            getattr(self, "sess_card_active", None),
            getattr(self, "str_card_maxw", None),
            getattr(self, "str_card_maxl", None),
            getattr(self, "str_card_cur", None),
            getattr(self, "str_card_avgw", None),
            getattr(self, "str_card_avgl", None),
        ]:
            if c is not None:
                try:
                    c.set_value("No trades yet", "text")
                except Exception:
                    try:
                        c.set_value("—", "text")
                    except Exception:
                        pass

        # Reset plots.
        for plot_attr in (
            "equity_curve", "equity_baseline", "peak_curve",
            "drawdown_curve", "drawdown_zero",
            "session_plot", "roll_sharpe_plot", "roll_wr_plot",
            "dist_plot", "duration_plot", "timeline_plot", "timeline_dur_plot",
            "cal_plot", "mae_mfe_plot", "mae_hist", "r_hist", "r_cum",
            "slip_plot",
        ):
            plot_or_item = getattr(self, plot_attr, None)
            try:
                if plot_or_item is not None and hasattr(plot_or_item, "clear"):
                    plot_or_item.clear()
                elif plot_or_item is not None and hasattr(plot_or_item, "setData"):
                    plot_or_item.setData([], [])
            except Exception:
                pass

        # Reset QTable models when present.
        try:
            if hasattr(self, "_dd_model"):
                self._dd_model.set_rows([])
        except Exception:
            pass
        try:
            if hasattr(self, "_attr_model"):
                self._attr_model.set_rows([])
        except Exception:
            pass
        try:
            if hasattr(self, "_session_model"):
                self._session_model.set_rows([])
        except Exception:
            pass
        try:
            if hasattr(self, "_streak_model"):
                self._streak_model.set_rows([])
        except Exception:
            pass

        # Empty label overlay on equity plot (fallback if we cannot inject a widget).
        if hasattr(self, "equity_plot"):
            try:
                if not hasattr(self, "_empty_eq_label"):
                    self._empty_eq_label = pg.TextItem(anchor=(0.5, 0.5))
                    self.equity_plot.addItem(self._empty_eq_label)
                self._empty_eq_label.setHtml(
                    "<div style='text-align:center'>"
                    "<div style='font-size:12px;font-weight:700'>No trades yet</div>"
                    "<div style='font-size:11px;color:#999'>"
                    "Run a strategy or load a journal to populate this table"
                    "</div>"
                    "</div>"
                )
                self._empty_eq_label.setPos(0.5, 0.0)
                self._empty_eq_label.show()
            except Exception:
                pass

        # Reset heatmap widgets if already lazy-loaded.
        for hm_attr in ("hw_heatmap", "my_heatmap"):
            hm = getattr(self, hm_attr, None)
            try:
                if hm is not None and hasattr(hm, "set_grid"):
                    # Provide empty grids (if widget supports it).
                    hm.set_grid(
                        np.zeros((0, 0), dtype=float),
                        x_labels=[],
                        y_labels=[],
                        cmap="theme_div",
                    )
            except Exception:
                pass

        # Reset custom range label.
        if getattr(self, "_range_result", None) is not None:
            try:
                self._range_result.setText("No trades yet")
            except Exception:
                pass

        # Also hide drill-down UI.
        if hasattr(self, "_drill_lbl") and self._drill_lbl is not None:
            try:
                self._drill_lbl.hide()
            except Exception:
                pass
        if hasattr(self, "_btn_clear_drill") and self._btn_clear_drill is not None:
            try:
                self._btn_clear_drill.hide()
            except Exception:
                pass

        self._eq_has_data = False

    def _update_equity_curve(self):
        self._show_no_trades_state()
        sorted_trades = self._sorted_trades()
        if not sorted_trades:
            return

        is_all = self._period_kind in ("ALL", "")
        cache = getattr(self.engine, "_shared_cache", None)

        if is_all and cache is not None:
            eq_all, peak, dd_arr_abs, dd_arr_pct = cache.get_equity_curve_arrays()
            pnls = cache.get_pnl_array()
            stats = cache.get_stats_dict()
            n = len(pnls)
            init_eq = float(self.engine.initial_equity)
        else:
            n = len(sorted_trades)
            pnls = np.empty(n, dtype=float)
            for i, t in enumerate(sorted_trades):
                try:
                    pnls[i] = float(getattr(t, "pnl", 0.0) or 0.0)
                except Exception:
                    pnls[i] = 0.0
            
            # AI-18: Sub-period equity baseline.
            # Start from equity at period_start, not initial_equity.
            full_sorted = cache.get_sorted_trades() if cache else []
            start_dt, _ = self._resolve_period_window()
            if start_dt is not None and full_sorted:
                prior_pnl = sum(float(getattr(t, "pnl", 0.0) or 0.0) for t in full_sorted 
                                if (getattr(t, "entry_time_np", None) or pd.to_datetime(t.entry_time)) < start_dt)
                init_eq = float(self.engine.initial_equity) + prior_pnl
            else:
                init_eq = float(self.engine.initial_equity)

            eq_all = np.empty(n + 1, dtype=float)
            eq_all[0] = init_eq
            np.cumsum(pnls, out=eq_all[1:])
            eq_all[1:] += init_eq
            peak = np.maximum.accumulate(eq_all)
            dd_arr_pct = np.where(peak > 0, (peak - eq_all) / peak * 100, 0)
            dd_arr_abs = peak - eq_all
            stats = None

        if hasattr(self, "_empty_eq_label"):
            self._empty_eq_label.hide()

        x_data = np.arange(len(eq_all))
        self.equity_curve.setData(x_data, eq_all)
        baseline = np.full_like(eq_all, init_eq)
        self.equity_baseline.setData(x_data, baseline)

        if hasattr(self, "_eq_initial_line"):
            self._eq_initial_line.setPos(init_eq)

        self.peak_curve.setVisible(True)
        self.peak_curve.setData(x_data, peak)

        self._dd_data = (x_data, dd_arr_pct, dd_arr_abs)

        win_mask = pnls > 0
        loss_mask = pnls < 0
        self.drawdown_curve.setData(x_data, -dd_arr_pct)
        self.drawdown_zero.setData(x_data, np.zeros_like(dd_arr_pct, dtype=float))

        # ── Best / worst trade annotations ──
        for lbl in getattr(self, "_eq_trade_labels", []):
            try:
                self.equity_plot.removeItem(lbl)
            except RuntimeError:
                pass
        self._eq_trade_labels = []
        if n > 0:
            t = get_theme()
            best_i = int(np.argmax(pnls)) + 1
            worst_i = int(np.argmin(pnls)) + 1
            for idx, label, ckey in [
                (best_i, "BEST", "green_bright"),
                (worst_i, "WORST", "red_bright"),
            ]:
                if 0 <= idx < len(eq_all):
                    lbl = pg.TextItem(
                        text=label,
                        color=QColor(sh.c(t, ckey)),
                        anchor=(0.5, 1.2),
                    )
                    lbl.setPos(float(idx), eq_all[idx])
                    self.equity_plot.addItem(lbl)
                    self._eq_trade_labels.append(lbl)

        if not getattr(self, "_eq_has_data", False):
            self.equity_plot.autoRange()
            self.drawdown_plot.autoRange()
            self._eq_has_data = True
            fade_in(self.equity_plot, duration_ms=400).start(
                QPropertyAnimation.DeletionPolicy.DeleteWhenStopped
            )

        if stats is not None:
            total_ret = stats["return_pct"]
            max_dd = stats["max_dd_pct"]
            sharpe = stats["sharpe"]
            pf = stats["pf"]
            final_eq = float(eq_all[-1])
        else:
            final_eq = float(eq_all[-1])
            total_ret = ((final_eq - init_eq) / init_eq * 100) if init_eq else 0.0
            gp = float(pnls[win_mask].sum()) if win_mask.any() else 0.0
            gl = float(abs(pnls[loss_mask].sum())) if loss_mask.any() else 0.0
            max_dd = float(dd_arr_pct.max())
            sharpe = _eq_sharpe(_build_eq(self._sorted_trades(), init_eq))
            if gl > 0:
                pf = gp / gl
            elif gp > 0:
                pf = float("inf")
            else:
                pf = 0.0

        # Calmar ratio = annualized return / max drawdown
        calmar = total_ret / max_dd if max_dd > 0 else 0.0

        ret_color = "green_bright" if total_ret >= 0 else "red_bright"
        eq_color = "green_bright" if final_eq >= init_eq else "red_bright"
        sharpe_color = "green_bright" if sharpe > 1.0 else "amber" if sharpe > 0.5 else "red_bright"
        pf_color = "green_bright" if pf >= 1.5 else "amber" if pf >= 1.0 else "red_bright"
        calmar_color = "text" if abs(calmar) < 1e-9 else "green_bright" if calmar > 1.0 else "amber" if calmar > 0 else "red_bright"

        self.eq_card_return.set_value(f"{total_ret:+.2f}%", ret_color)
        self.eq_card_equity.set_value(f"${final_eq:,.0f}", eq_color)
        self.eq_card_maxdd.set_value(f"{max_dd:.2f}%", "red_bright")
        self.eq_card_sharpe.set_value(f"{sharpe:.2f}", sharpe_color)
        self.eq_card_calmar.set_value(f"{calmar:.2f}", calmar_color)
        pf_str = "∞" if pf == float("inf") else f"{pf:.2f}"
        self.eq_card_pf.set_value(pf_str, pf_color)
        self.eq_card_trades.set_value(str(n), "text")
        # Attach sparklines to the equity sub-tab cards (Phase 1 EPIC 1.1).
        try:
            self.eq_card_return.set_sparkline(spk.rolling_return(pnls))
            self.eq_card_equity.set_sparkline(spk.cumulative_equity(pnls, init_eq))
            self.eq_card_maxdd.set_sparkline(spk.underwater_curve(pnls, init_eq))
            self.eq_card_sharpe.set_sparkline(spk.rolling_sharpe(pnls))
            self.eq_card_calmar.set_sparkline(spk.rolling_sortino(pnls))
            wins_arr = pnls[win_mask] if win_mask.any() else None
            losses_arr = pnls[loss_mask] if loss_mask.any() else None
            if wins_arr is not None and wins_arr.size > 0 and losses_arr is not None and losses_arr.size > 0:
                # PF sparkline = histogram of all trades shaped around zero.
                self.eq_card_pf.set_sparkline(spk.histogram_bars(pnls))
            else:
                self.eq_card_pf.set_sparkline(None)
            self.eq_card_trades.set_sparkline(spk.streak_bars(pnls))
        except Exception:
            logger.exception("equity sub-tab sparkline computation failed")
        self._add_drawdown_regions(x_data, dd_arr_pct)

    def _queue_eq_mouse(self, pos) -> None:
        self._eq_mouse_pending = pos
        if not self._eq_mouse_throttle.isActive():
            self._eq_mouse_throttle.start()

    def _flush_eq_mouse(self) -> None:
        pos = self._eq_mouse_pending
        if pos is None:
            return
        if not hasattr(self, "_eq_cross_v"):
            return
        vb = self.equity_plot.getViewBox()
        if vb is None or not vb.sceneBoundingRect().contains(pos):
            self._eq_cross_v.hide()
            self._eq_cross_h.hide()
            return
        mouse_point = vb.mapSceneToView(pos)
        self._eq_cross_v.setPos(mouse_point.x())
        self._eq_cross_h.setPos(mouse_point.y())
        self._eq_cross_v.show()
        self._eq_cross_h.show()

    def _update_session_matrix(self):
        self.session_plot.clear()
        trades = list(getattr(self.engine, "trades", []) or [])
        if not trades:
            self._show_no_trades_state()
            return

        session_names = [s[0] for s in SESSIONS]
        cache = getattr(self.engine, "_shared_cache", None)
        if cache is not None:
            rows_obj = cache.get_session_rows(session_names)
            rows = [
                dict(
                    session=r.session, total=r.total, wins=r.wins,
                    losses=r.losses, wr=r.wr, avg_pnl=r.avg_pnl,
                    total_pnl=r.total_pnl, best_trade=r.best_trade,
                ) for r in rows_obj
            ]
        else:
            session_data = {s: [] for s in session_names}
            for t in trades:
                if t.session in session_data:
                    session_data[t.session].append(t)
            rows = []
            for sn in session_names:
                st = session_data[sn]
                if not st:
                    continue
                total = len(st)
                wins = sum(1 for t in st if t.pnl > 0)
                losses = sum(1 for t in st if t.pnl < 0)
                total_pnl = sum(t.pnl for t in st)
                rows.append(dict(
                    session=sn, total=total, wins=wins, losses=losses,
                    wr=(wins / total * 100) if total else 0,
                    avg_pnl=total_pnl / total if total else 0,
                    total_pnl=total_pnl,
                    best_trade=max((t.pnl for t in st), default=0),
                ))

        best_sess, best_pnl = "", -float("inf")
        worst_sess, worst_pnl = "", float("inf")
        most_active, most_count = "", 0

        bar_x, bar_heights, bar_colors = [], [], []

        # Render from pre-aggregated rows
        rows_by_name = {r["session"]: r for r in rows}
        model_rows = []
        for idx, sn in enumerate(session_names):
            row_data = rows_by_name.get(sn)
            if not row_data:
                continue

            total = row_data["total"]
            wins = row_data["wins"]
            losses = row_data["losses"]
            wr = row_data["wr"]
            total_pnl = row_data["total_pnl"]
            avg_pnl = row_data["avg_pnl"]
            best_trade = row_data["best_trade"]

            if total_pnl > best_pnl:
                best_pnl, best_sess = total_pnl, sn
            if total_pnl < worst_pnl:
                worst_pnl, worst_sess = total_pnl, sn
            if total > most_count:
                most_count, most_active = total, sn

            model_rows.append([
                sn,
                total,
                wins,
                losses,
                f"{wr:.1f}%",
                f"{avg_pnl:+.2f}",
                f"{total_pnl:+.2f}",
                f"{best_trade:+.2f}",
            ])

            # Bar chart data
            bar_x.append(idx)
            bar_heights.append(total_pnl)

            _t = get_theme()
            c_win = QColor(sh.c(_t, "chart_win_marker"))
            c_loss = QColor(sh.c(_t, "chart_loss_marker"))
            bar_colors.append(c_win if total_pnl >= 0 else c_loss)

        # Update session cards
        self.sess_card_best.set_value(f"{best_sess}\n${best_pnl:+,.0f}", "green_bright")
        self.sess_card_worst.set_value(f"{worst_sess}\n${worst_pnl:+,.0f}", "red_bright")
        self.sess_card_active.set_value(f"{most_active}\n{most_count} trades", "accent_light")

        # Push rows to model once (single reset)
        self._session_model.set_rows(model_rows)

        # Draw bar chart (batched)
        if bar_x:
            bg = pg.BarGraphItem(
                x=np.array(bar_x, dtype=float),
                height=np.array(bar_heights, dtype=float),
                width=0.6,
                brushes=bar_colors,
            )
            self.session_plot.addItem(bg)
            self.session_plot.getAxis("bottom").setTicks(
                [[(i, sn.replace("_", " ")) for i, sn in enumerate(session_names)]]
            )
            t = get_theme()
            zc = QColor(sh.c(t, "text3"))
            zc.setAlpha(80)
            self.session_plot.addItem(
                pg.InfiniteLine(pos=0, angle=0, pen=pg.mkPen(zc, width=1, style=Qt.PenStyle.DashLine))
            )
        self.session_plot.autoRange()

    def _update_streak_tracker(self):
        trades = list(getattr(self.engine, "trades", []) or [])
        if not trades:
            self._show_no_trades_state()
            return

        # Use shared cache if available
        cache = getattr(self.engine, "_shared_cache", None)
        if cache is not None:
            streaks = cache.get_streaks()
        else:
            streaks = []
            ct = None
            sid = None
            ln = 0
            streak_pnl = 0
            for t in trades:
                if t.pnl > 0:
                    tt = "WIN"
                elif t.pnl < 0:
                    tt = "LOSS"
                else:
                    if ct is not None:
                        streaks.append((ct, ln, sid, t.id - 1, streak_pnl))
                    ct = None
                    sid = None
                    ln = 0
                    streak_pnl = 0
                    continue
                if tt == ct:
                    ln += 1
                    streak_pnl += t.pnl
                else:
                    if ct is not None:
                        streaks.append((ct, ln, sid, t.id - 1, streak_pnl))
                    ct = tt
                    sid = t.id
                    ln = 1
                    streak_pnl = t.pnl
            if ct is not None:
                streaks.append((ct, ln, sid, trades[-1].id, streak_pnl))

        # Calculate streak stats
        win_streaks = [s[1] for s in streaks if s[0] == "WIN"]
        loss_streaks = [s[1] for s in streaks if s[0] == "LOSS"]
        max_ws = max(win_streaks) if win_streaks else 0
        max_ls = max(loss_streaks) if loss_streaks else 0
        avg_ws = sum(win_streaks) / len(win_streaks) if win_streaks else 0
        avg_ls = sum(loss_streaks) / len(loss_streaks) if loss_streaks else 0

        # Current streak
        if streaks:
            last_type, last_len, _, _, _ = streaks[-1]
            cur_str = f"{last_len}{last_type[0]}"
            cur_color = "green_bright" if last_type == "WIN" else "red_bright"
        else:
            cur_str, cur_color = "—", "text"

        self.str_card_maxw.set_value(f"{max_ws} trades", "green_bright")
        self.str_card_maxl.set_value(f"{max_ls} trades", "red_bright")
        self.str_card_cur.set_value(cur_str, cur_color)
        self.str_card_avgw.set_value(f"{avg_ws:.1f} trades", "green_bright")
        self.str_card_avgl.set_value(f"{avg_ls:.1f} trades", "red_bright")

        model_rows = []
        for st, sl, ss, se, spnl in streaks:
            if sl < 2:
                continue
            model_rows.append([st, str(sl), f"#{ss}", f"#{se}", f"{spnl:+.2f}"])
        self._streak_model.set_rows(model_rows)

    def calc_custom_range(self):
        trades = list(getattr(self.engine, "trades", []) or [])
        if not trades:
            self.range_result.setText("No trades available.")
            return

        df = self.date_from.date().toPython()
        dt = self.date_to.date().toPython()
        n = len(trades)
        times_np = np.empty(n, dtype="datetime64[D]")
        for i, t in enumerate(trades):
            et = getattr(t, "entry_time_np", None)
            if et is not None:
                times_np[i] = et.astype("datetime64[D]")
            else:
                try:
                    times_np[i] = np.datetime64(pd.to_datetime(t.entry_time).date())
                except Exception:
                    times_np[i] = np.datetime64("1970-01-01", "D")
        start_np = np.datetime64(df, "D")
        end_np = np.datetime64(dt, "D")
        mask = (times_np >= start_np) & (times_np <= end_np)
        filtered = [trades[i] for i in np.where(mask)[0]]

        if not filtered:
            self.range_result.setText(f"No trades between {df} and {dt}.")
            for c in [
                self.rng_card_trades,
                self.rng_card_wr,
                self.rng_card_pnl,
                self.rng_card_avg,
                self.rng_card_pf,
            ]:
                c.set_value("—", "text")
            return

        total = len(filtered)
        wins = sum(1 for t in filtered if t.pnl > 0)
        losses = sum(1 for t in filtered if t.pnl < 0)
        be = total - wins - losses
        wr = (wins / total * 100) if total > 0 else 0
        total_pnl = sum(t.pnl for t in filtered)
        avg = total_pnl / total if total > 0 else 0
        gp = sum(t.pnl for t in filtered if t.pnl > 0)
        gl = abs(sum(t.pnl for t in filtered if t.pnl < 0))
        if gl > 0:
            pf = gp / gl
        elif gp > 0:
            pf = float("inf")
        else:
            pf = 0.0

        wr_color = "green_bright" if wr >= 50 else "amber" if wr >= 40 else "red_bright"
        pnl_color = "green_bright" if total_pnl >= 0 else "red_bright"
        avg_color = "green_bright" if avg >= 0 else "red_bright"
        pf_color = "green_bright" if pf >= 1.5 else "amber" if pf >= 1.0 else "red_bright"

        self.rng_card_trades.set_value(str(total), "text")
        self.rng_card_wr.set_value(f"{wr:.1f}%", wr_color)
        self.rng_card_pnl.set_value(f"${total_pnl:+,.0f}", pnl_color)
        self.rng_card_avg.set_value(f"${avg:+,.2f}", avg_color)
        pf_str = "∞" if pf == float("inf") else f"{pf:.2f}"
        self.rng_card_pf.set_value(pf_str, pf_color)

        self.range_result.setText(
            f"<b>Range:</b> {df} → {dt}<br><br>"
            f"<b>Total Trades:</b> {total}  (W: {wins}  L: {losses}  BE: {be})<br>"
            f"<b>Win Rate:</b> {wr:.1f}%<br>"
            f"<b>Total PnL:</b> ${total_pnl:+,.2f}<br>"
            f"<b>Avg PnL:</b> ${avg:+,.2f}<br>"
            f"<b>Profit Factor:</b> {pf:.2f}<br>"
            f"<b>Gross Profit:</b> ${gp:+,.2f}  |  <b>Gross Loss:</b> ${gl:,.2f}"
        )

    def _update_advanced_stats(self, pnl_arr=None):
        """Draw PnL histogram with green/red bins and skew/kurtosis annotation."""
        self.dist_plot.clear()
        
        is_all = self._period_kind in ("ALL", "")
        cache = getattr(self.engine, "_shared_cache", None)
        
        if pnl_arr is None:
            if is_all and cache:
                pnls_orig = cache.get_pnl_array()
            else:
                trades = self._sorted_trades()
                if not trades:
                    self._show_no_trades_state()
                    return
                pnls_orig = np.array([t.pnl for t in trades], dtype=float)
        else:
            pnls_orig = np.asarray(pnl_arr, dtype=float)
        
        if pnls_orig.size == 0:
            return
        t = get_theme()
        unique = np.unique(pnls_orig)
        if unique.size > 1:
            n_bins = min(30, unique.size)
            y, x = np.histogram(pnls_orig, bins=n_bins)
            c_win = QColor(sh.c(t, "chart_win_marker"))
            c_loss = QColor(sh.c(t, "chart_loss_marker"))
            c_win.setAlpha(180)
            c_loss.setAlpha(180)
            bin_centers = (x[:-1] + x[1:]) / 2
            brushes = [c_win if bc >= 0 else c_loss for bc in bin_centers]
            bg = pg.BarGraphItem(x0=x[:-1], x1=x[1:], height=y, brushes=brushes)
            self.dist_plot.addItem(bg)
        mean_pnl = float(np.mean(pnls_orig))
        pen = QColor(sh.c(t, "green_bright"))
        pen.setAlpha(230)
        mean_line = pg.InfiniteLine(
            pos=mean_pnl, angle=90, pen=pg.mkPen(pen, width=2, style=Qt.PenStyle.DashLine)
        )
        self.dist_plot.addItem(mean_line)
        zc = QColor(sh.c(t, "text3"))
        zc.setAlpha(60)
        self.dist_plot.addItem(pg.InfiniteLine(pos=0, angle=90, pen=pg.mkPen(zc, width=1, style=Qt.PenStyle.DotLine)))
        self.dist_plot.autoRange()
        skew_val = float(np.mean(((pnls_orig - mean_pnl) / max(float(np.std(pnls_orig)), 1e-9)) ** 3)) if len(pnls_orig) > 2 else 0.0
        kurt_val = float(np.mean(((pnls_orig - mean_pnl) / max(float(np.std(pnls_orig)), 1e-9)) ** 4) - 3) if len(pnls_orig) > 3 else 0.0
        vb = self.dist_plot.getViewBox()
        if vb:
            xr, yr = vb.viewRange()
            info_txt = pg.TextItem(
                text=f"μ={mean_pnl:+.2f}  Skew={skew_val:+.2f}  Kurt={kurt_val:+.2f}",
                color=QColor(sh.c(t, "text3")),
                anchor=(1, 1)
            )
            info_txt.setPos(xr[1], yr[1])
            self.dist_plot.addItem(info_txt)

    def _update_extended_metrics(self, pnl_arr=None):
        """Draw duration histogram and render merged analytics HTML."""
        self.duration_plot.clear()

        is_all = self._period_kind in ("ALL", "")
        cache = getattr(self.engine, "_shared_cache", None)

        if is_all and cache:
            stats = cache.get_stats_dict()
            pnl_arr = cache.get_pnl_array()
            durations = cache.get_dur_array()
            # Map cached stats to legacy context for HTML renderer
            ctx = {
                "calmar": stats["calmar"],
                "sortino": stats["sortino"],
                "omega": stats["omega"],
                "ulcer": stats["ulcer"],
                "var_95": stats["var95"],
                "cvar_95": stats["cvar95"],
                "mo": stats["mo"],
                "kelly": stats["kelly"] / 100.0,
                "tuw": type("TUW", (), {
                    "pct_bars_underwater": stats["pain_index"], 
                    "max_run_bars": stats["max_tuw"],
                    "avg_run_bars": stats["avg_tuw"],
                    "n_runs": int(stats["total"] * 0.1) # estimate
                })(),
                "dur_stats": {"mean_duration": stats["avg_dur"], "std_duration": 0.0},
                "streak_stats": {"max_win_streak": stats["max_ws"], "max_loss_streak": stats["max_ls"]},
                "ror": stats.get("ror", 0.0),
            }
        else:
            trades = self._sorted_trades()
            if not trades:
                self._show_no_trades_state()
                return
            if pnl_arr is None:
                pnl_arr = np.array([t.pnl for t in trades], dtype="float64")
            else:
                pnl_arr = np.asarray(pnl_arr, dtype="float64")
            
            durations = np.array([getattr(t, "duration_minutes", 0) for t in trades], dtype=float)
            
            _eq_curve = _build_eq(trades, self.engine.initial_equity)
            _pnl_arr = pnl_arr
            calmar = calculate_calmar_ratio(trades, self.engine.initial_equity, equity_curve=_eq_curve)
            sortino = calculate_sortino_ratio(trades, self.engine.initial_equity, equity_curve=_eq_curve)
            var_95 = calculate_var(trades, 0.95, pnl_array=_pnl_arr)
            cvar_95 = calculate_cvar(trades, 0.95, pnl_array=_pnl_arr)
            omega = calculate_omega_ratio(trades, pnl_array=_pnl_arr)
            ulcer = calculate_ulcer_index(trades, self.engine.initial_equity, equity_curve=_eq_curve)

            wins = pnl_arr[pnl_arr > 0]
            losses = -pnl_arr[pnl_arr < 0]
            total = len(pnl_arr)
            wr = len(wins) / total if total > 0 else 0
            if wins.size > 0 and losses.size > 0:
                avg_w = wins.mean()
                avg_l = losses.mean()
                risk_pct = max(0.01, (avg_l / max(1.0, self.engine.initial_equity)) * 100.0)
                ror = _risk_of_ruin(wr, avg_w, avg_l, risk_per_trade_pct=risk_pct, ruin_pct=0.5)
            else:
                ror = float("nan")

            mo = float((wr * wins.mean() if wins.size else 0) - ((1 - wr) * losses.mean() if losses.size else 0))
            kelly = wr - ((1 - wr) / (wins.mean() / losses.mean())) if wins.size and losses.size else 0.0

            eq_arr = self.engine.initial_equity + np.cumsum(pnl_arr)
            tuw = _time_under_water(np.concatenate([[self.engine.initial_equity], eq_arr]))
            dur_stats = analyze_trade_duration(trades)
            streak_stats = calculate_consecutive_wins_losses(trades)
            
            ctx = locals()

        t_dur = get_theme()
        if durations.size > 1:
            y, x = np.histogram(durations, bins=min(20, len(np.unique(durations))))
            c_dur = QColor(sh.c(t_dur, "cyan"))
            c_dur.setAlpha(160)
            bg = pg.BarGraphItem(x0=x[:-1], x1=x[1:], height=y, brush=c_dur)
            self.duration_plot.addItem(bg)

        mean_dur = ctx["dur_stats"].get("mean_duration", 0)
        if mean_dur > 0:
            pen = QColor(sh.c(t_dur, "green_bright"))
            pen.setAlpha(230)
            mean_line = pg.InfiniteLine(pos=mean_dur, angle=90, pen=pg.mkPen(pen, width=2, style=Qt.PenStyle.DashLine))
            self.duration_plot.addItem(mean_line)
        
        self.duration_plot.autoRange()
        
        t = get_theme()
        zc_dur = QColor(sh.c(t, "text3"))
        zc_dur.setAlpha(60)
        self.duration_plot.addItem(pg.InfiniteLine(pos=0, angle=90, pen=pg.mkPen(zc_dur, width=1, style=Qt.PenStyle.DotLine)))
        
        # Duration annotation
        vb = self.duration_plot.getViewBox()
        if vb and mean_dur > 0:
            xr, yr = vb.viewRange()
            dur_txt = pg.TextItem(
                text=f"μ={mean_dur:.0f}m  σ={ctx['dur_stats'].get('std_duration',0):.0f}m",
                color=QColor(sh.c(t, "text3")),
                anchor=(1, 1)
            )
            dur_txt.setPos(xr[1], yr[1])
            self.duration_plot.addItem(dur_txt)

        self._render_merged_html(ctx)

    def _render_merged_html(self, ctx: dict) -> None:
        tt = get_theme()
        text = sh.c(tt, "text")
        muted = sh.c(tt, "text2")
        accent = sh.c(tt, "accent_light")
        amber = sh.c(tt, "amber")
        green = sh.c(tt, "green_bright")
        red = sh.c(tt, "red_bright")

        calmar = ctx.get("calmar", 0)
        sortino = ctx.get("sortino", 0)
        omega = ctx.get("omega", 0)
        ulcer = ctx.get("ulcer", 0)
        var_95 = ctx.get("var_95", 0)
        cvar_95 = ctx.get("cvar_95", 0)
        mo = ctx.get("mo", 0)
        kelly = ctx.get("kelly", 0)
        ror = ctx.get("ror", float("nan"))
        tuw = ctx.get("tuw", None)
        dur_stats = ctx.get("dur_stats", {})
        streak_stats = ctx.get("streak_stats", {})

        calmar_ck = "green_bright" if calmar > 1.0 else "amber" if calmar > 0.5 else "red_bright"
        sortino_ck = "green_bright" if sortino > 1.0 else "amber" if sortino > 0.5 else "red_bright"
        omega_ck = "green_bright" if omega > 1.0 else "red_bright"

        calmar_str = "\u221e" if calmar == float("inf") else f"{calmar:.2f}"
        sortino_str = "\u221e" if sortino == float("inf") else ("N/A" if sortino != sortino else f"{sortino:.2f}")
        omega_str = "\u221e" if omega == float("inf") else f"{omega:.2f}"

        mo_color = green if mo >= 0 else red
        ror_cell = red if (ror == ror and ror > 0.05) else green
        ror_text = "N/A" if ror != ror else f"{ror * 100:.2f}%"
        tuw_pct = (tuw.pct_bars_underwater * 100) if tuw is not None else 0
        tuw_max = tuw.max_run_bars if tuw is not None else 0
        tuw_avg = tuw.avg_run_bars if tuw is not None else 0
        tuw_n = tuw.n_runs if tuw is not None else 0

        html = f"""
        <div style="font-size: 13px; color: {text};">
            <h3 style="color: {accent}; margin-top: 0;">Advanced Trade Analytics</h3>

            <h4 style="color: {green}; margin-top: 12px;">Risk-Adjusted Returns</h4>
            <table width="100%" cellspacing="6">
                <tr><td width="40%"><b>Calmar Ratio:</b></td>
                    <td style="color: {sh.c(tt, calmar_ck)};"><b>{calmar_str}</b></td>
                    <td style="color: {muted}; font-size: 10px;">(Ann. Return / Max DD)</td></tr>
                <tr><td><b>Sortino Ratio:</b></td>
                    <td style="color: {sh.c(tt, sortino_ck)};"><b>{sortino_str}</b></td>
                    <td style="color: {muted}; font-size: 10px;">(Downside deviation only)</td></tr>
                <tr><td><b>Omega Ratio:</b></td>
                    <td style="color: {sh.c(tt, omega_ck)};"><b>{omega_str}</b></td>
                    <td style="color: {muted}; font-size: 10px;">(Gains / Losses)</td></tr>
                <tr><td><b>Ulcer Index:</b></td>
                    <td style="color: {amber};"><b>{ulcer:.2f}%</b></td>
                    <td style="color: {muted}; font-size: 10px;">(DD depth & duration)</td></tr>
            </table>

            <h4 style="color: {amber}; margin-top: 12px;">Risk Metrics</h4>
            <table width="100%" cellspacing="6">
                <tr><td width="40%"><b>Expected Payoff (MO):</b></td>
                    <td style="color: {mo_color};"><b>${mo:.2f}</b></td>
                    <td style="color: {muted}; font-size: 10px;">(Mean edge per trade)</td></tr>
                <tr><td><b>Kelly Criterion:</b></td>
                    <td><b>{kelly * 100:.1f}%</b></td>
                    <td style="color: {muted}; font-size: 10px;">(Suggested max risk)</td></tr>
                <tr><td><b>VaR (95%):</b></td>
                    <td style="color: {red};"><b>${var_95:.2f}</b></td>
                    <td style="color: {muted}; font-size: 10px;">(Max loss, 95% conf.)</td></tr>
                <tr><td><b>CVaR (95%):</b></td>
                    <td style="color: {red};"><b>${cvar_95:.2f}</b></td>
                    <td style="color: {muted}; font-size: 10px;">(Avg tail loss)</td></tr>
            </table>

            <h4 style="color: {green}; margin-top: 12px;">Streak Analysis</h4>
            <table width="100%" cellspacing="6">
                <tr><td width="40%"><b>Max Win Streak:</b></td>
                    <td style="color: {green};"><b>{streak_stats.get("max_win_streak", 0)} trades</b></td></tr>
                <tr><td><b>Max Loss Streak:</b></td>
                    <td style="color: {red};"><b>{streak_stats.get("max_loss_streak", 0)} trades</b></td></tr>
                <tr><td><b>Avg Win Streak:</b></td>
                    <td style="color: {green};"><b>{streak_stats.get("avg_win_streak", 0):.1f} trades</b></td></tr>
                <tr><td><b>Avg Loss Streak:</b></td>
                    <td style="color: {red};"><b>{streak_stats.get("avg_loss_streak", 0):.1f} trades</b></td></tr>
            </table>

            <h4 style="color: {accent}; margin-top: 12px;">Trade Duration Analysis</h4>
            <table width="100%" cellspacing="6">
                <tr><td width="40%"><b>Mean Duration:</b></td>
                    <td><b>{dur_stats.get("mean_duration", 0):.1f} min</b></td>
                    <td style="color: {muted}; font-size: 10px;">({dur_stats.get("mean_duration", 0) / 60:.2f} hours)</td></tr>
                <tr><td><b>Median Duration:</b></td>
                    <td><b>{dur_stats.get("median_duration", 0):.1f} min</b></td>
                    <td style="color: {muted}; font-size: 10px;">({dur_stats.get("median_duration", 0) / 60:.2f} hours)</td></tr>
                <tr><td><b>Min / Max:</b></td>
                    <td><b>{dur_stats.get("min_duration", 0):.0f} / {dur_stats.get("max_duration", 0):.0f} min</b></td>
                    <td style="color: {muted}; font-size: 10px;">(Range)</td></tr>
                <tr><td><b>25th / 75th Percentile:</b></td>
                    <td><b>{dur_stats.get("p25_duration", 0):.0f} / {dur_stats.get("p75_duration", 0):.0f} min</b></td>
                    <td style="color: {muted}; font-size: 10px;">(IQR)</td></tr>
            </table>

            <h4 style="color: {red}; margin-top: 12px;">Tail Risk</h4>
            <table width="100%" cellspacing="6">
                <tr><td width="40%"><b>Risk of Ruin (50% DD):</b></td>
                    <td style="color: {ror_cell};"><b>{ror_text}</b></td>
                    <td style="color: {muted}; font-size: 10px;">(Vince closed-form)</td></tr>
                <tr><td><b>% Bars Underwater:</b></td>
                    <td style="color: {amber};"><b>{tuw_pct:.1f}%</b></td>
                    <td style="color: {muted}; font-size: 10px;">(Time below peak)</td></tr>
                <tr><td><b>Max DD Run:</b></td>
                    <td style="color: {red};"><b>{tuw_max} trades</b></td>
                    <td style="color: {muted}; font-size: 10px;">(Longest underwater streak)</td></tr>
                <tr><td><b>Avg DD Run:</b></td>
                    <td style="color: {amber};"><b>{tuw_avg:.1f} trades</b></td>
                    <td style="color: {muted}; font-size: 10px;">(Across {tuw_n} runs)</td></tr>
            </table>
        </div>
        """
        self.merged_text.setText(html)

    # ═══════════════════════════════════════════════════════════════ Feature 4
    def _update_rolling_metrics(self):
        self.roll_sharpe_plot.clear()
        self.roll_wr_plot.clear()
        trades = list(getattr(self.engine, "trades", []) or [])
        if len(trades) < 5:
            return
        try:
            window = int(self.roll_window_cb.currentText())
        except Exception:
            window = 50
        window = max(5, min(window, len(trades)))
        pnls = np.array([t.pnl for t in trades], dtype=float)
        t = get_theme()
        x = np.arange(len(trades))

        # Vectorized rolling Sharpe via sliding_window_view
        windows = np.lib.stride_tricks.sliding_window_view(pnls, window)
        means = windows.mean(axis=1)
        stds = windows.std(axis=1, ddof=1)
        valid = stds > 1e-10
        roll_sharpe = np.full(len(trades), np.nan, dtype=float)
        roll_sharpe_vals = np.where(valid, means / np.maximum(stds, 1e-10) * np.sqrt(window), 0.0)
        roll_sharpe[window - 1:] = roll_sharpe_vals

        self.roll_sharpe_plot.plot(x, roll_sharpe, pen=pg.mkPen(sh.c(t, "accent_light"), width=2))
        zc = QColor(sh.c(t, "text3"))
        zc.setAlpha(100)
        self.roll_sharpe_plot.addItem(pg.InfiniteLine(pos=0, angle=0, pen=pg.mkPen(zc, width=1, style=Qt.PenStyle.DashLine)))
        if np.isfinite(roll_sharpe).any():
            avg_s = float(np.nanmean(roll_sharpe))
            self.roll_sharpe_plot.addItem(pg.InfiniteLine(pos=avg_s, angle=0, pen=pg.mkPen(QColor(sh.c(t, "amber")), width=1, style=Qt.PenStyle.DotLine)))
        self.roll_sharpe_plot.autoRange()

        # Vectorized rolling WR via convolution
        win_mask = (pnls > 0).astype(float)
        win_sums = np.convolve(win_mask, np.ones(window), mode='valid')
        roll_wr = np.full(len(trades), np.nan, dtype=float)
        roll_wr[window - 1:] = win_sums / window
        wc = QColor(sh.c(t, "green_bright"))
        wc.setAlpha(200)
        self.roll_wr_plot.plot(x, roll_wr * 100, pen=pg.mkPen(wc, width=2))
        fc = QColor(sh.c(t, "text3"))
        fc.setAlpha(100)
        self.roll_wr_plot.addItem(pg.InfiniteLine(pos=50, angle=0, pen=pg.mkPen(fc, width=1, style=Qt.PenStyle.DashLine)))
        if np.isfinite(roll_wr).any():
            avg_wr = float(np.nanmean(roll_wr) * 100)
            self.roll_wr_plot.addItem(pg.InfiniteLine(pos=avg_wr, angle=0, pen=pg.mkPen(QColor(sh.c(t, "amber")), width=1, style=Qt.PenStyle.DotLine)))
        self.roll_wr_plot.autoRange()

    # ═══════════════════════════════════════════════════════════════ Feature 5
    # ── Calendar Heatmap — Bloomberg/QuantConnect Terminal Style ──
    def _interpolate_color(self, c1: tuple, c2: tuple, t: float) -> tuple:
        return tuple(int(a + (b - a) * t) for a, b in zip(c1, c2))

    def _rgb(self, key: str) -> tuple[int, int, int]:
        qc = QColor(sh.c(get_theme(), key))
        return qc.red(), qc.green(), qc.blue()

    def _pnl_to_rgba(self, pnl: float, vmax: float) -> tuple:
        """Map a P&L value to a muted theme-aware calendar cell color."""
        if vmax <= 0:
            return (*self._rgb("elevated2"), 70)
        intensity = min(1.0, abs(pnl) / vmax) ** 0.78
        alpha = int(120 + 120 * intensity)
        neutral = self._rgb("elevated2")
        target = self._rgb("green" if pnl >= 0 else "red")
        rgb = self._interpolate_color(neutral, target, 0.18 + 0.82 * intensity)
        return (*rgb, alpha)

    def _build_calendar_grid(self, daily: dict, max_days: list, vmax: float) -> tuple:
        """Build a premium terminal-grade calendar using ImageItem for the 
        background grid + scatter overlay for P&L cells.
        
        Returns (bg_rgba: np.ndarray, cell_data: list, pnl_texts: list).
        """
        max_cols = max(max_days)
        bg_rgba = np.full((12, max_cols, 4), 0, dtype=np.ubyte)
        nr, ng, nb = self._rgb("elevated2")
        
        # Draw empty-day grid — each day is a 1x1 cell with a micro-thin border
        for r in range(12):
            n_cols = max_days[r]
            for c in range(n_cols):
                bg_rgba[r, c] = [nr, ng, nb, 135]
        
        cell_data = []
        pnl_texts = []
        
        for day, pnl in daily.items():
            m = day.month - 1
            d = day.day - 1
            if not (0 <= m < 12 and 0 <= d < max_days[m]):
                continue
            
            pr, pg_, pb_, pa = self._pnl_to_rgba(pnl, vmax)
            bg_rgba[m, d] = [pr, pg_, pb_, pa]
            cell_data.append({
                "x": float(d),
                "y": float(m),
                "color": QColor(pr, pg_, pb_, pa),
                "pnl": pnl,
            })
            
            # PnL label — only for significant cells
            if abs(pnl) > vmax * 0.08:
                intensity = min(1.0, abs(pnl) / max(vmax, 1e-9))
                text_color = QColor("#FFFFFF" if intensity >= 0.45 else sh.c(get_theme(), "text"))
                label = self._money(pnl, compact=True)
                pnl_texts.append({
                    "x": float(d),
                    "y": float(m),
                    "text": label,
                    "color": text_color,
                })
        
        return bg_rgba, cell_data, pnl_texts

    def _render_calendar_layer(self, cell_data: list, cal_plot, pnl_texts: list, max_cols: int):
        """Render all calendar layers — cells + PnL numbers."""
        from collections import defaultdict
        
        cell_size = 8
        
        for item in pnl_texts:
            txt = pg.TextItem(
                text=item["text"],
                color=item["color"],
                anchor=(0.5, 0.5),
            )
            txt.setPos(item["x"], item["y"])
            txt.setFont(QFont(font_mono(), 7, QFont.Weight.Medium))
            txt.setZValue(5)
            cal_plot.addItem(txt)

    def _update_calendar_heatmap(self):
        self.cal_plot.clear()
        trades = list(getattr(self.engine, "trades", []) or [])
        if not trades:
            self.cal_summary.setText("No trades for calendar view.")
            return
        
        if self._cal_year is None:
            years = sorted(set(
                pd.to_datetime(t.entry_time).year
                for t in trades if hasattr(t, "entry_time") and t.entry_time
            ), reverse=True)
            self._cal_year = years[0] if years else pd.Timestamp.now().year
        self.cal_year_lbl.setText(str(self._cal_year))
        
        daily = {}
        for t in trades:
            try:
                ts = pd.to_datetime(t.entry_time)
                if ts.year != self._cal_year:
                    continue
                day = ts.date()
                daily[day] = daily.get(day, 0.0) + float(t.pnl)
            except Exception:
                continue
        
        if not daily:
            self.cal_summary.setText(f"No trades in {self._cal_year}.")
            import calendar as _cal
            max_days = [_cal.monthrange(self._cal_year, m + 1)[1] for m in range(12)]
            max_cols = max(max_days)
            nr, ng_, nb_ = self._rgb("elevated2")
            bg_rgba = np.zeros((12, max_cols, 4), dtype=np.ubyte)
            for r, n_cols in enumerate(max_days):
                for c in range(n_cols):
                    bg_rgba[r, c] = [nr, ng_, nb_, 135]
            self.cal_image = pg.ImageItem(axisOrder="row-major")
            self.cal_plot.addItem(self.cal_image)
            self.cal_image.setImage(bg_rgba)
            self.cal_image.setRect(QRectF(-0.5, -0.5, float(max_cols), 12.0))
            self.cal_plot.setXRange(-0.9, max_cols + 0.5, padding=0.0)
            self.cal_plot.setYRange(-0.9, 11.9, padding=0.0)
            return
        
        import calendar as _cal
        max_days = [_cal.monthrange(self._cal_year, m + 1)[1] for m in range(12)]
        self._cal_max_days = max_days
        max_cols = max(max_days)
        
        vmax = float(max(abs(v) for v in daily.values()))
        if vmax <= 0 or not np.isfinite(vmax):
            vmax = 1.0
        
        t = get_theme()
        
        # ── Build grid background (empty cells with micro-thin slate borders) ──
        bg_rgba = np.full((12, max_cols, 4), 0, dtype=np.ubyte)
        nr, ng_, nb_ = self._rgb("elevated2")
        for r in range(12):
            n_cols = max_days[r]
            for c in range(n_cols):
                bg_rgba[r, c] = [nr, ng_, nb_, 135]
        
        self.cal_image = pg.ImageItem(axisOrder="row-major")
        self.cal_image.setImage(bg_rgba)
        self.cal_plot.addItem(self.cal_image)
        
        # ── Build and render P&L cells ──
        bg_rgba_full, cell_data, pnl_texts = self._build_calendar_grid(daily, max_days, vmax)
        self.cal_image.setImage(bg_rgba_full)
        self.cal_image.setRect(QRectF(-0.5, -0.5, float(max_cols), 12.0))
        self._render_calendar_layer(cell_data, self.cal_plot, pnl_texts, max_cols)
        
        # ── Surgical terminal-style axis labels ──
        months_short = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]
        left_axis = self.cal_plot.getAxis("left")
        left_axis.setTicks([[(i, m) for i, m in enumerate(months_short)]])
        left_axis.setStyle(tickTextOffset=4, tickLength=0)
        left_axis.setTextPen(QColor(sh.c(t, "chart_axis_text")))
        
        bottom_axis = self.cal_plot.getAxis("bottom")
        step = max(1, max_days[0] // 5)
        day_ticks = [(d, str(d + 1)) for d in range(0, max_cols, step)]
        bottom_axis.setTicks([day_ticks] if day_ticks else [])
        bottom_axis.setStyle(tickTextOffset=4, tickLength=0)
        bottom_axis.setTextPen(QColor(sh.c(t, "chart_axis_text")))
        
        # Remove all grid lines — terminal aesthetic
        self.cal_plot.showGrid(x=False, y=False)
        
        # ── Year P&L legend (top-right, Bloomberg-style) ──
        year_pnl = sum(daily.values())
        win_days = sum(1 for v in daily.values() if v > 0)
        loss_days = sum(1 for v in daily.values() if v < 0)
        pnl_color = sh.c(t, "green") if year_pnl >= 0 else sh.c(t, "red")
        
        legend_items = [
            pg.TextItem(
                text=f"Y {self._cal_year}",
                color=QColor(sh.c(t, "text")),
                anchor=(1, 0),
            ),
            pg.TextItem(
                text=f"P&L {self._money(year_pnl)}",
                color=QColor(pnl_color),
                anchor=(1, 0),
            ),
        ]
        legend_x = max_cols + 4.2
        legend_items[0].setPos(legend_x, -0.5)
        legend_items[1].setPos(legend_x, 0.3)
        for li in legend_items:
            li.setFont(QFont(font_mono(), 9, QFont.Weight.Bold))
            li.setZValue(10)
            self.cal_plot.addItem(li)
        
        # ── Summary stats bar ──
        best_day = max(daily.values())
        worst_day = min(daily.values())
        mean_day = year_pnl / max(len(daily), 1)
        
        self.cal_summary.setText(
            f'<span style="font-family:{font_mono()};color:{pnl_color};font-weight:600;">'
            f"Y {self._cal_year}: {self._money(year_pnl)}</span>"
            f'<span style="font-family:{font_mono()};color:{sh.c(t,"text3")};">'
            f"  |  W {win_days}  L {loss_days}  ·  {len(daily)} sessions"
            f"  |  Best {self._money(best_day)}  Worst {self._money(worst_day)}  Avg {self._money(mean_day)}</span>"
        )
        
        # ── Range ──
        self.cal_plot.setXRange(-0.9, max_cols + 7.0, padding=0.0)
        self.cal_plot.setYRange(-0.9, 11.9, padding=0.0)

    def _cal_nav_prev(self):
        if self._cal_year is not None:
            self._cal_year -= 1
            self._update_calendar_heatmap()

    def _cal_nav_next(self):
        if self._cal_year is not None:
            self._cal_year += 1
            self._update_calendar_heatmap()

    # ═══════════════════════════════════════════════════════════════ Feature 8
    def _update_trade_timeline(self):
        self.timeline_plot.clear()
        self.timeline_dur_plot.clear()
        trades = list(getattr(self.engine, "trades", []) or [])
        if not trades:
            self._show_no_trades_state()
            return
        t = get_theme()
        n = len(trades)
        x = np.arange(n, dtype=float)
        pnls = np.fromiter((float(getattr(tr, "pnl", 0.0) or 0.0) for tr in trades), dtype=float, count=n)
        win_mask = pnls >= 0
        loss_mask = ~win_mask
        # Premium: low opacity (25%) creates natural heatmap density effect
        green = QColor(sh.c(t, "green_bright"))
        green.setAlpha(65)
        red = QColor(sh.c(t, "red_bright"))
        red.setAlpha(65)

        if win_mask.any():
            self.timeline_plot.plot(x[win_mask], pnls[win_mask],
                pen=None, symbol="o", symbolSize=3, symbolBrush=green)
        if loss_mask.any():
            self.timeline_plot.plot(x[loss_mask], pnls[loss_mask],
                pen=None, symbol="o", symbolSize=3, symbolBrush=red)

        zc = QColor(sh.c(t, "text3"))
        zc.setAlpha(80)
        self.timeline_plot.addItem(pg.InfiniteLine(pos=0, angle=0, pen=pg.mkPen(zc, width=1, style=Qt.PenStyle.DashLine)))
        self.timeline_plot.autoRange()

        durs = np.fromiter((float(getattr(tr, "duration_minutes", 0) or 0) for tr in trades), dtype=float, count=n)
        if durs.max() > 0:
            dur_win = durs > 0
            green_dur = QColor(sh.c(t, "green_bright"))
            green_dur.setAlpha(65)
            red_dur = QColor(sh.c(t, "red_bright"))
            red_dur.setAlpha(65)
            if dur_win.any():
                self.timeline_dur_plot.plot(x[dur_win], durs[dur_win],
                    pen=None, symbol="o", symbolSize=3, symbolBrush=green_dur)
            if (~dur_win).any():
                self.timeline_dur_plot.plot(x[~dur_win], durs[~dur_win],
                    pen=None, symbol="o", symbolSize=3, symbolBrush=red_dur)
            self.timeline_dur_plot.autoRange()

    # ═══════════════════════════════════════════════════════════════ Feature 9
    def _add_drawdown_regions(self, x_data, dd_pct):
        try:
            for item in getattr(self, "_dd_region_items", []):
                try:
                    self.equity_plot.removeItem(item)
                except (RuntimeError, AttributeError):
                    pass
            self._dd_region_items = []
            if dd_pct is None or len(dd_pct) < 2:
                return
            t = get_theme()
            col = QColor(sh.c(t, "red_bright"))
            col.setAlpha(30)
            brush = QBrush(col)
            in_dd = False
            start = 0
            for i in range(len(dd_pct)):
                if dd_pct[i] > 0.5 and not in_dd:
                    in_dd = True
                    start = i
                elif (dd_pct[i] <= 0.5 or i == len(dd_pct) - 1) and in_dd:
                    in_dd = False
                    top = pg.PlotCurveItem(
                        x=[x_data[start], x_data[i]], y=[0, 0],
                        pen=None,
                    )
                    bot = pg.PlotCurveItem(
                        x=[x_data[start], x_data[i]], y=[-dd_pct[start], -dd_pct[i]],
                        pen=None,
                    )
                    fill = pg.FillBetweenItem(top, bot, brush=brush)
                    fill.setZValue(-100)
                    self.drawdown_plot.addItem(fill)
                    self._dd_region_items.append(fill)
        except Exception:
            pass


    # --------------------------------------------------------------- MAE/MFE
    def _create_mae_mfe_tab(self) -> None:
        w = QWidget()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        inner = QWidget()
        l = QVBoxLayout(inner)
        l.setContentsMargins(12, 10, 12, 10)
        l.setSpacing(8)

        cap_scatter = QLabel("◈  MAE vs MFE — Scatter (win/loss)")
        self._section_captions.append(cap_scatter)
        l.addWidget(cap_scatter)

        self.mae_mfe_plot = pg.PlotWidget()
        self.mae_mfe_plot.setLabel("left", "MFE ($)", size="9pt")
        self.mae_mfe_plot.setLabel("bottom", "MAE ($)", size="9pt")
        l.addWidget(self.mae_mfe_plot, stretch=1)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        l.addWidget(sep)

        cap_hist = QLabel("◈  MAE / Risk (R) Distribution")
        self._section_captions.append(cap_hist)
        l.addWidget(cap_hist)

        self.mae_hist = pg.PlotWidget()
        self.mae_hist.setLabel("left", "Frequency", size="9pt")
        self.mae_hist.setLabel("bottom", "MAE / risk (R)", size="9pt")
        self.mae_hist.setMaximumHeight(220)
        l.addWidget(self.mae_hist)

        l.addStretch()
        scroll.setWidget(inner)
        wl = QVBoxLayout(w)
        wl.setContentsMargins(0, 0, 0, 0)
        wl.addWidget(scroll)
        return w

    # --------------------------------------------------------------- R-multiples
    def _create_r_multiples_tab(self) -> None:
        w = QWidget()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        inner = QWidget()
        l = QVBoxLayout(inner)
        l.setContentsMargins(12, 10, 12, 10)
        l.setSpacing(8)

        cap_cum = QLabel("◈  Cumulative R-multiple")
        self._section_captions.append(cap_cum)
        l.addWidget(cap_cum)

        self.r_cum = pg.PlotWidget()
        self.r_cum.setLabel("left", "Cumulative R", size="9pt")
        self.r_cum.setLabel("bottom", "Trade #", size="9pt")
        l.addWidget(self.r_cum, stretch=1)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        l.addWidget(sep)

        cap_hist = QLabel("◈  R-multiple Distribution")
        self._section_captions.append(cap_hist)
        l.addWidget(cap_hist)

        self.r_hist = pg.PlotWidget()
        self.r_hist.setLabel("left", "Frequency", size="9pt")
        self.r_hist.setLabel("bottom", "R-multiple", size="9pt")
        self.r_hist.setMaximumHeight(220)
        l.addWidget(self.r_hist)

        self.r_summary = QLabel("R-multiples summary will appear after a run.")
        self._summary_labels.append(self.r_summary)
        l.addWidget(self.r_summary)

        l.addStretch()
        scroll.setWidget(inner)
        wl = QVBoxLayout(w)
        wl.setContentsMargins(0, 0, 0, 0)
        wl.addWidget(scroll)
        return w

    # --------------------------------------------------------------- Attribution
    def _create_attribution_tab(self) -> None:
        w = QWidget()
        l = QVBoxLayout(w)
        l.setContentsMargins(12, 10, 12, 10)
        l.setSpacing(8)

        cap_attr = QLabel("▤  Exit-reason Attribution")
        self._section_captions.append(cap_attr)
        l.addWidget(cap_attr)

        def _attr_style(r, c, v):
            if c == 2:
                try:
                    s = str(v).replace("$", "").replace(",", "")
                    val = float(s)
                    t = get_theme()
                    return {"fg": sh.c(t, "green_bright") if val >= 0 else sh.c(t, "red_bright")}
                except Exception:
                    return {}
            return {}

        self.attr_table = QTableView()
        self.attr_table.horizontalHeader().setStretchLastSection(True)
        self.attr_table.setAlternatingRowColors(True)
        self._attr_model = SimpleTableModel(
            ["Reason", "Count", "Sum PnL", "Avg PnL", "Win-rate"],
            [],
            parent=self,
            cell_style_fn=_attr_style,
        )
        self.attr_table.setModel(self._attr_model)
        self._tables_with_overrides.append(self.attr_table)
        cap_dd = QLabel("▤  Drawdown Episodes")
        self._section_captions.append(cap_dd)
        l.addWidget(cap_dd)
        self.dd_table = QTableView()
        self.dd_table.horizontalHeader().setStretchLastSection(True)
        self.dd_table.setAlternatingRowColors(True)
        self._dd_model = SimpleTableModel(
            ["#", "Peak idx", "Trough idx", "Recover idx", "Depth ($)", "Underwater (trades)"],
            [],
            parent=self,
        )
        self.dd_table.setModel(self._dd_model)
        self._tables_with_overrides.append(self.dd_table)
        l.addWidget(self.dd_table, stretch=1)
        return w

    # --------------------------------------------------------------- Slippage
    def _create_slippage_tab(self) -> None:
        w = QWidget()
        l = QVBoxLayout(w)
        l.setContentsMargins(12, 10, 12, 10)
        l.setSpacing(8)
        cfg = QGroupBox("What-if Slippage")
        form = QFormLayout(cfg)
        self.slip_spin = QDoubleSpinBox()
        self.slip_spin.setRange(0.0, 1000.0)
        self.slip_spin.setSingleStep(0.5)
        self.slip_spin.setValue(2.0)
        self.slip_spin.setSuffix(" $/trade")
        self.slip_spin.valueChanged.connect(self._update_slippage)
        form.addRow("Per-trade extra cost:", self.slip_spin)
        l.addWidget(cfg)

        self.slip_plot = pg.PlotWidget()
        self.slip_plot.setLabel("left", "Equity ($)", size="9pt")
        self.slip_plot.setLabel("bottom", "Trade #", size="9pt")
        l.addWidget(self.slip_plot, stretch=1)

        self.slip_summary = QLabel("Adjust the slip cost.")
        self._summary_labels.append(self.slip_summary)
        l.addWidget(self.slip_summary)
        return w

    # ------------------------------------------------------- Heatmap clicks
    def _on_hw_heatmap_click(self, row: int, col: int) -> None:
        payload = {"hour": int(row), "weekday": int(col)}
        self._set_active_drilldown(payload)
        self.drill_down_requested.emit(payload)

    def _on_my_heatmap_click(self, row: int, col: int) -> None:
        years = getattr(self, "_last_my_years", None) or []
        if not years:
            return
        if not (0 <= int(row) < len(years)):
            return
        if not (0 <= int(col) <= 11):
            return
        year = years[int(row)]
        month = int(col) + 1
        payload = {"year": int(year), "month": int(month)}
        self._set_active_drilldown(payload)
        self.drill_down_requested.emit(payload)

    def _set_active_drilldown(self, payload: dict) -> None:
        self._active_drilldown = dict(payload or {})
        if not self._active_drilldown:
            self._drill_lbl.hide()
            self._btn_clear_drill.hide()
            return
        parts = []
        if "hour" in self._active_drilldown or "weekday" in self._active_drilldown:
            h = self._active_drilldown.get("hour")
            wd = self._active_drilldown.get("weekday")
            if h is not None:
                parts.append(f"H={int(h):02d}")
            if wd is not None:
                parts.append(f"W={int(wd)}")
        if "year" in self._active_drilldown or "month" in self._active_drilldown:
            y = self._active_drilldown.get("year")
            m = self._active_drilldown.get("month")
            if y is not None and m is not None:
                parts.append(f"{int(y):04d}-{int(m):02d}")
            elif y is not None:
                parts.append(f"Y={int(y)}")
            elif m is not None:
                parts.append(f"M={int(m):02d}")
        self._drill_lbl.setText("Drill-down: " + (" ".join(parts) if parts else "Active"))
        self._drill_lbl.show()
        self._btn_clear_drill.show()

    def _clear_drilldown(self) -> None:
        self._set_active_drilldown({})
        try:
            if hasattr(self, "hw_heatmap") and self.hw_heatmap is not None:
                self.hw_heatmap.clear_selection()
            if hasattr(self, "my_heatmap") and self.my_heatmap is not None:
                self.my_heatmap.clear_selection()
        except Exception:
            pass
        self.drill_down_requested.emit({})

    # ---------------------------------------------------------- Update MAE/MFE
    def _update_mae_mfe(self) -> None:
        trades = self._sorted_trades()
        if not trades:
            self._show_no_trades_state()
            return
        if not self.isVisible():
            return

        with self._mae_lock:
            self._mae_gen += 1
            gen = self._mae_gen

        prev = getattr(self, "_mae_worker", None)
        if prev is not None:
            try:
                prev.finished.disconnect()
                prev.cancel()
            except Exception:
                pass
            self._mae_worker = None

        is_all = self._period_kind in ("ALL", "")
        cache = getattr(self.engine, "_shared_cache", None)
        data = cache if (is_all and cache) else trades

        worker = ComputeWorker(_compute_mae_mfe, data, parent=self)

        def _done(payload, g=gen):
            with self._mae_lock:
                if g != self._mae_gen:
                    return
            self._finalize_mae_mfe(payload)

        worker.finished.connect(_done)
        worker.error.connect(lambda e: logger.error(f"MAE/MFE worker failed: {e}"))
        self._mae_worker = worker
        worker.start()

    def _finalize_mae_mfe(self, payload: dict) -> None:
        if not self.isVisible():
            return
        res = payload.get("res")
        if res is None or res.mae_dollars.size == 0:
            return

        self.mae_mfe_plot.clear()
        self.mae_hist.clear()
        win_mask = res.pnl_dollars > 0
        loss_mask = ~win_mask
        t = get_theme()

        win_col = QColor(sh.c(t, "chart_win_marker"))
        win_col.setAlpha(100)
        loss_col = QColor(sh.c(t, "chart_loss_marker"))
        loss_col.setAlpha(100)

        if win_mask.any():
            self.mae_mfe_plot.plot(
                res.mae_dollars[win_mask], res.mfe_dollars[win_mask],
                pen=None, symbol="o", symbolSize=6, symbolBrush=win_col,
            )
        if loss_mask.any():
            self.mae_mfe_plot.plot(
                res.mae_dollars[loss_mask], res.mfe_dollars[loss_mask],
                pen=None, symbol="x", symbolSize=6, symbolBrush=loss_col,
            )
        zc = QColor(sh.c(t, "text3"))
        zc.setAlpha(80)
        self.mae_mfe_plot.addItem(pg.InfiniteLine(pos=0, angle=0, pen=pg.mkPen(zc, width=1, style=Qt.PenStyle.DashLine)))
        self.mae_mfe_plot.addItem(pg.InfiniteLine(pos=0, angle=90, pen=pg.mkPen(zc, width=1, style=Qt.PenStyle.DashLine)))

        finite_r = res.mae_r[np.isfinite(res.mae_r)]
        if finite_r.size > 0:
            hist_col = QColor(sh.c(t, "cyan"))
            hist_col.setAlpha(150)
            y, x = np.histogram(finite_r, bins=min(40, max(10, int(np.sqrt(finite_r.size)))))
            self.mae_hist.addItem(
                pg.BarGraphItem(x0=x[:-1], x1=x[1:], height=y, brush=hist_col)
            )
        self.mae_mfe_plot.autoRange()
        self.mae_hist.autoRange()

    def _update_r_multiples(self) -> None:
        trades = self._sorted_trades()
        if not trades:
            self._show_no_trades_state()
            return
        if not self.isVisible():
            return

        with self._r_lock:
            self._r_gen += 1
            gen = self._r_gen

        prev = getattr(self, "_r_worker", None)
        if prev is not None:
            try:
                prev.finished.disconnect()
                prev.cancel()
            except Exception:
                pass
            self._r_worker = None

        is_all = self._period_kind in ("ALL", "")
        cache = getattr(self.engine, "_shared_cache", None)
        data = cache if (is_all and cache) else trades

        is_all = self._period_kind in ("ALL", "")
        cache = getattr(self.engine, "_shared_cache", None)
        data = cache if (is_all and cache) else trades

        worker = ComputeWorker(_compute_r_multiples, data, parent=self)

        def _done(payload, g=gen):
            with self._r_lock:
                if g != self._r_gen:
                    return
            self._finalize_r_multiples(payload)

        worker.finished.connect(_done)
        worker.error.connect(lambda e: logger.error(f"R-multiples worker failed: {e}"))
        self._r_worker = worker
        worker.start()

    def _finalize_r_multiples(self, payload: dict) -> None:
        if not self.isVisible():
            return
        res = payload.get("res")
        if res is None:
            return
        self.r_hist.clear()
        self.r_cum.clear()
        finite = res.r_values[np.isfinite(res.r_values)]
        t = get_theme()
        if finite.size > 0:
            hist_col = QColor(sh.c(t, "amber"))
            hist_col.setAlpha(160)
            y, x = np.histogram(finite, bins=min(40, max(10, int(np.sqrt(finite.size)))))
            self.r_hist.addItem(
                pg.BarGraphItem(x0=x[:-1], x1=x[1:], height=y, brush=hist_col)
            )
            self.r_hist.addItem(
                pg.InfiniteLine(pos=0.0, angle=90,
                                pen=pg.mkPen(sh.c(t, "text3"), width=1, style=Qt.PenStyle.DashLine))
            )
        if res.cumulative_r.size > 0:
            self.r_cum.plot(
                np.arange(1, res.cumulative_r.size + 1), res.cumulative_r,
                pen=pg.mkPen(sh.c(t, "accent_light"), width=2),
            )
            zc = QColor(sh.c(t, "text3"))
            zc.setAlpha(80)
            self.r_cum.addItem(pg.InfiniteLine(pos=0, angle=0, pen=pg.mkPen(zc, width=1, style=Qt.PenStyle.DashLine)))
        self.r_summary.setText(
            f"N = {finite.size}   |   Expectancy = {res.expectancy_r:+.3f}R   |   "
            f"Best = {(finite.max() if finite.size else 0):+.2f}R   |   "
            f"Worst = {(finite.min() if finite.size else 0):+.2f}R"
        )
        self.r_hist.autoRange()
        self.r_cum.autoRange()

    # ---------------------------------------------------------- Update Heatmaps
    def _make_heatmap_slot(self, gen):
        slot = lambda p: self._finalize_heatmaps(p, gen)
        slot._gen = gen
        return slot

    def _update_heatmaps(self) -> None:
        trades = self._sorted_trades()
        if not trades:
            self._show_no_trades_state()
            return
        if not self.isVisible():
            return
        with self._heatmap_lock:
            self._heatmap_gen += 1
            gen = self._heatmap_gen
        prev = getattr(self, "_heatmap_worker", None)
        if prev is not None:
            try:
                prev.finished.disconnect(getattr(prev, "_heatmap_slot", None))
                prev.error.disconnect(self._on_heatmap_error)
            except (RuntimeError, TypeError):
                pass
            prev.cancel()
            prev.deleteLater()
        if not hasattr(self, "_heatmap_spinner") or self._heatmap_spinner is None:
            try:
                hm_parent = getattr(self, "_heatmap_tab_w", self)
                self._heatmap_spinner = Spinner(hm_parent, "Computing heatmaps\u2026")
                self._heatmap_spinner.cover(hm_parent)
            except RuntimeError:
                return
        self._heatmap_spinner.start()
        is_all = self._period_kind in ("ALL", "")
        cache = getattr(self.engine, "_shared_cache", None)
        data = cache if (is_all and cache) else trades

        worker = ComputeWorker(_compute_heatmaps, data, parent=self)
        slot = self._make_heatmap_slot(gen)
        worker.finished.connect(slot)
        worker._heatmap_slot = slot
        worker.error.connect(self._on_heatmap_error)
        self._heatmap_worker = worker
        worker.start()

    def _finalize_heatmaps(self, payload: dict, gen: int) -> None:
        with self._heatmap_lock:
            if gen != self._heatmap_gen:
                return
        if not self.isVisible():
            return
        if hasattr(self, "_heatmap_spinner"):
            self._heatmap_spinner.stop()
        # Guard: heatmap widgets don't exist until the Heatmaps tab is lazy-loaded
        if not hasattr(self, "hw_heatmap") or not hasattr(self, "my_heatmap"):
            return
        hw = payload.get("hw") if isinstance(payload, dict) else None
        my = payload.get("my") if isinstance(payload, dict) else None
        if hw is None or my is None:
            logger.warning("Heatmap worker returned incomplete payload")
            return
        sym = self._currency_symbol()
        try:
            self.hw_heatmap.set_currency_symbol(sym)
            self.hw_heatmap.set_grid(
                hw.net_pnl,
                x_labels=WEEKDAY_LABELS,
                y_labels=[f"{h:02d}" for h in range(24)],
                cmap="theme_div",
                extras=HeatmapExtras(counts=hw.counts, win_rate=hw.win_rate),
            )
        except RuntimeError:
            return
        if my.years:
            self._last_my_years = list(my.years)
            try:
                self.my_heatmap.set_currency_symbol(sym)
                self.my_heatmap.set_grid(
                    my.net_pnl,
                    x_labels=MONTH_LABELS,
                    y_labels=[str(y) for y in my.years],
                    cmap="theme_div",
                    extras=HeatmapExtras(counts=my.counts),
                )
            except RuntimeError:
                return
        else:
            self._last_my_years = []

    def _on_heatmap_error(self, exc: Exception) -> None:
        if hasattr(self, "_heatmap_spinner"):
            self._heatmap_spinner.stop()
        logger.exception("Heatmap worker failed", exc_info=exc)

    # ---------------------------------------------------------- Update Attribution
    def _update_attribution(self) -> None:
        trades = self._sorted_trades()
        if not trades:
            self._show_no_trades_state()
            return
        if not self.isVisible():
            return

        with self._attr_lock:
            self._attr_gen += 1
            gen = self._attr_gen

        prev = getattr(self, "_attr_worker", None)
        if prev is not None:
            try:
                prev.finished.disconnect()
                prev.cancel()
            except Exception:
                pass
            self._attr_worker = None

        is_all = self._period_kind in ("ALL", "")
        cache = getattr(self.engine, "_shared_cache", None)
        data = cache if (is_all and cache) else trades

        worker = ComputeWorker(_compute_attribution, data, parent=self)

        def _done(payload, g=gen):
            with self._attr_lock:
                if g != self._attr_gen:
                    return
            self._finalize_attribution(payload, trades)

        worker.finished.connect(_done)
        worker.error.connect(lambda e: logger.error(f"Attribution worker failed: {e}"))
        self._attr_worker = worker
        worker.start()

    def _finalize_attribution(self, payload: dict, trades: list) -> None:
        if not self.isVisible():
            return
        rows = payload.get("rows")
        if rows is None:
            return

        eq = np.empty(len(trades) + 1, dtype=float)
        eq[0] = self.engine.initial_equity
        np.cumsum([float(getattr(t, "pnl", 0.0) or 0.0) for t in trades], out=eq[1:])
        eq[1:] += self.engine.initial_equity
        episodes = drawdown_durations(eq)

        dd_rows = []
        for r, e in enumerate(episodes):
            dd_rows.append([
                str(r + 1), str(e.start_idx), str(e.trough_idx),
                "—" if e.recover_idx is None else str(e.recover_idx),
                f"${e.depth_dollars:,.2f}", str(e.underwater_trades),
            ])
        self._dd_model.set_rows(dd_rows)

        attr_rows = [[
            row.reason, str(row.count), f"${row.sum_pnl:,.2f}",
            f"${row.avg_pnl:,.2f}", f"{row.win_rate * 100:.1f}%",
        ] for row in rows]
        self._attr_model.set_rows(attr_rows)

    # ---------------------------------------------------------- Update Slippage
    def _update_slippage(self) -> None:
        self.slip_plot.clear()
        trades = list(getattr(self.engine, "trades", []) or [])
        if not trades:
            self._show_no_trades_state()
            return
        cost = float(self.slip_spin.value())
        res = simulate_slippage(trades, slip_per_trade_dollars=cost)
        eq_base = np.cumsum(res.base_pnls) + self.engine.initial_equity
        eq_pert = np.cumsum(res.perturbed_pnls) + self.engine.initial_equity
        x = np.arange(1, eq_base.size + 1)
        t = get_theme()
        self.slip_plot.plot(
            x, eq_base, pen=pg.mkPen(sh.c(t, "chart_win_marker"), width=2), name="Base"
        )
        self.slip_plot.plot(
            x, eq_pert, pen=pg.mkPen(sh.c(t, "chart_loss_marker"), width=2), name="Perturbed"
        )
        self.slip_summary.setText(
            f"Base total: ${res.base_total:,.2f}    "
            f"Perturbed total: ${res.perturbed_total:,.2f}    "
            f"\u0394: ${res.pnl_delta:,.2f}    "
            f"({res.avg_slip_per_trade:.2f}$ \u00d7 {res.base_pnls.size} trades)"
        )
        self.slip_plot.autoRange()

    def sync_period(self, kind: str, start, end) -> None:
        """Cross-tab period sync — record period and refresh panels."""
        self._period_kind = kind
        self._period_start = start
        self._period_end = end

        # AI-26: Update period badge
        if hasattr(self, "lbl_period"):
            if kind in ("ALL", ""):
                self.lbl_period.hide()
            else:
                self.lbl_period.setText(f"Period: {kind}")
                self.lbl_period.show()

        try:
            self.refresh_all(force=True)
        except Exception:
            pass
