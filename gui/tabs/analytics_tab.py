"""
Analytics Tab — Premium analytics with improved equity curve (drawdown subplot),
session matrix, streak tracker, custom range WR, advanced stats.
"""

import logging

import numpy as np
import pandas as pd
import pyqtgraph as pg
from PySide6.QtCore import QDate, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QDateEdit,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from engine.models.models import SESSIONS
from engine.analytics.monte_carlo import bootstrap_pnl
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
)
from gui.workers.workers import ComputeWorker, Spinner


def _bootstrap_mc_summary(pnls: np.ndarray, initial_equity: float, n_iter: int) -> dict:
    """Pure-Python helper invoked from a worker thread — never touches Qt.

    Computes the Monte Carlo bootstrap once and returns the scalar summary
    that ``_update_advanced_stats`` paints into the UI. Splitting this out
    keeps the worker thread free of any GUI calls and lets unit tests
    exercise the aggregation independently of PySide.
    """
    mc = bootstrap_pnl(pnls, initial_equity=initial_equity, n_iter=n_iter)
    final_returns = (mc.final_equity - initial_equity) / initial_equity * 100.0
    worst_dds = mc.max_drawdown_pct
    return {
        "n_iter": int(n_iter),
        "avg_mc_dd": float(np.mean(worst_dds)),
        "max_mc_dd": float(np.max(worst_dds)),
        "min_mc_dd": float(np.min(worst_dds)),
        "p95_mc_dd": float(np.percentile(worst_dds, 95)),
        "p99_mc_dd": float(np.percentile(worst_dds, 99)),
        "avg_return": float(np.mean(final_returns)),
        "best_return": float(np.max(final_returns)),
        "worst_return": float(np.min(final_returns)),
        "p5_return": float(np.percentile(final_returns, 5)),
        "p95_return": float(np.percentile(final_returns, 95)),
        "prob_profit": float(np.sum(final_returns > 0) / n_iter * 100),
    }


logger = logging.getLogger("StrategyTester.gui.analytics_tab")


class MiniCard(QFrame):
    """Small metric card for analytics header.

    Phase 3: ``color`` may be either an explicit hex (legacy) or a theme key
    name (e.g. ``"green"``, ``"accent_light"``). Theme keys auto-track
    Light/Dark switches; explicit hex stays static.
    """

    def __init__(self, title, value="—", color="text", parent=None):
        super().__init__(parent)
        self.setObjectName("miniCard")
        self._title_text = title
        self._color = color
        self._value_text = value
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(56)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 6, 10, 6)
        lay.setSpacing(2)
        self._t = QLabel(title)
        self._v = QLabel(value)
        lay.addWidget(self._t)
        lay.addWidget(self._v)
        self._apply_theme()
        register_theme_listener(self._apply_theme)
        # Phase 3 — subtle drop shadow for the modern flat look.
        apply_drop_shadow(self, blur=14, y_offset=2, alpha=70)

    @staticmethod
    def _resolve_color(color: str) -> str:
        """Treat short tokens (no '#', not 7 chars) as theme keys."""
        t = get_theme()
        if isinstance(color, str) and color and not color.startswith("#"):
            return sh.c(t, color)
        return color

    def _apply_theme(self):
        t = get_theme()
        self.setStyleSheet(sh.card_hover_accent(t).replace("QFrame", "QFrame#miniCard"))
        self._t.setStyleSheet(sh.caption(t))
        self._v.setStyleSheet(
            f"color: {self._resolve_color(self._color)}; font-size: 16px; "
            f"font-weight: 700; font-family: {font_mono()}; background: transparent;"
        )

    def set_value(self, value, color=None):
        self._v.setText(value)
        self._value_text = value
        if color:
            self._color = color
        self._apply_theme()


class AnalyticsTab(QWidget):
    def __init__(self, engine):
        super().__init__()
        self.engine = engine
        self._last_trade_count: int = -1  # dirty-tracking for lazy refresh
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

        btn_monte_carlo = QPushButton("Monte Carlo")
        btn_monte_carlo.setToolTip("Bootstrap + shock-injection Monte Carlo")
        btn_monte_carlo.clicked.connect(self._open_monte_carlo)
        tb.addWidget(btn_monte_carlo)

        btn_refresh = QPushButton("Refresh")
        btn_refresh.setFixedWidth(90)
        btn_refresh.clicked.connect(self.refresh_all)
        tb.addWidget(btn_refresh)

        layout.addWidget(self._top_bar)

        # Sub-tabs
        self.sub_tabs = QTabWidget()
        layout.addWidget(self.sub_tabs)

        # Track sub-tab background panes so theme switch repaints them.
        self._panel_widgets: list = []
        self._tables_with_overrides: list = []
        self._range_result: QLabel | None = None
        self._adv_text: QLabel | None = None
        self._ext_text: QLabel | None = None

        self._create_equity_tab()
        self._create_session_tab()
        self._create_streak_tab()
        self._create_range_tab()
        self._create_advanced_tab()
        self._create_extended_tab()

        self._apply_theme()
        register_theme_listener(self._apply_theme)

    def _create_equity_tab(self):
        """Create equity curve tab with drawdown subplot."""
        eq_w = QWidget()
        self._panel_widgets.append(eq_w)
        eq_l = QVBoxLayout(eq_w)
        eq_l.setContentsMargins(12, 8, 12, 8)
        eq_l.setSpacing(6)

        # Mini summary cards
        cards_row = QHBoxLayout()
        cards_row.setSpacing(6)
        self.eq_card_return = MiniCard("TOTAL RETURN", "—", "text")
        self.eq_card_equity = MiniCard("FINAL EQUITY", "—", "accent_light")
        self.eq_card_maxdd = MiniCard("MAX DRAWDOWN", "—", "red")
        self.eq_card_sharpe = MiniCard("SHARPE RATIO", "—", "amber")
        self.eq_card_pf = MiniCard("PROFIT FACTOR", "—", "green")
        self.eq_card_trades = MiniCard("TOTAL TRADES", "—", "text")
        for c in [
            self.eq_card_return,
            self.eq_card_equity,
            self.eq_card_maxdd,
            self.eq_card_sharpe,
            self.eq_card_pf,
            self.eq_card_trades,
        ]:
            cards_row.addWidget(c)
        eq_l.addLayout(cards_row)

        # Splitter: equity on top, drawdown on bottom
        chart_splitter = QSplitter(Qt.Orientation.Vertical)
        self._splitters_with_overrides = getattr(self, "_splitters_with_overrides", [])
        self._splitters_with_overrides.append(chart_splitter)

        # Equity plot
        self.equity_plot = pg.PlotWidget()
        self.equity_plot.getAxis("left").setWidth(70)
        self.equity_plot.setLabel("left", "Equity ($)", size="10pt")
        self.equity_plot.setMinimumHeight(200)

        self.equity_curve = self.equity_plot.plot()
        self.equity_baseline = pg.PlotDataItem(pen=None)
        self.equity_plot.addItem(self.equity_baseline)
        self.equity_fill = pg.FillBetweenItem(self.equity_curve, self.equity_baseline)
        self.equity_plot.addItem(self.equity_fill)
        self.peak_curve = self.equity_plot.plot()

        # Trade markers
        self.eq_win_scatter = self.equity_plot.plot(symbol="t1", symbolSize=7, pen=None)
        self.eq_loss_scatter = self.equity_plot.plot(symbol="t", symbolSize=7, pen=None)

        chart_splitter.addWidget(self.equity_plot)

        # Drawdown plot
        self.dd_plot = pg.PlotWidget()
        self.dd_plot.getAxis("left").setWidth(70)
        self.dd_plot.setLabel("left", "Drawdown (%)", size="10pt")
        self.dd_plot.setMinimumHeight(100)
        self.dd_plot.setMaximumHeight(160)

        self.dd_curve_fill_top = pg.PlotDataItem(pen=None)
        self.dd_curve_fill_bot = pg.PlotDataItem(pen=None)
        self.dd_plot.addItem(self.dd_curve_fill_top)
        self.dd_plot.addItem(self.dd_curve_fill_bot)
        self.dd_fill = pg.FillBetweenItem(self.dd_curve_fill_top, self.dd_curve_fill_bot)
        self.dd_plot.addItem(self.dd_fill)
        self.dd_curve = self.dd_plot.plot()
        self.dd_plot.setXLink(self.equity_plot)

        chart_splitter.addWidget(self.dd_plot)
        chart_splitter.setSizes([300, 120])

        eq_l.addWidget(chart_splitter, stretch=1)
        self.sub_tabs.addTab(eq_w, "Equity Curve")

    def _create_session_tab(self):
        """Create session matrix tab."""
        sess_w = QWidget()
        self._panel_widgets.append(sess_w)
        sess_l = QVBoxLayout(sess_w)
        sess_l.setContentsMargins(12, 8, 12, 8)
        sess_l.setSpacing(8)

        sess_cards = QHBoxLayout()
        sess_cards.setSpacing(6)
        self.sess_card_best = MiniCard("BEST SESSION", "—", "green")
        self.sess_card_worst = MiniCard("WORST SESSION", "—", "red")
        self.sess_card_active = MiniCard("MOST ACTIVE", "—", "accent_light")
        for c in [self.sess_card_best, self.sess_card_worst, self.sess_card_active]:
            sess_cards.addWidget(c)
        sess_cards.addStretch()
        sess_l.addLayout(sess_cards)

        self.session_table = QTableWidget()
        self.session_table.setColumnCount(8)
        self.session_table.setHorizontalHeaderLabels(
            ["Session", "Trades", "Wins", "Losses", "WR%", "Avg PnL", "Total PnL", "Best Trade"]
        )
        self.session_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.session_table.setAlternatingRowColors(True)
        self._tables_with_overrides.append(self.session_table)
        sess_l.addWidget(self.session_table)

        self.session_plot = pg.PlotWidget()
        self.session_plot.setMaximumHeight(140)
        self.session_plot.setLabel("left", "Total PnL ($)", size="9pt")
        sess_l.addWidget(self.session_plot)

        self.sub_tabs.addTab(sess_w, "Sessions")

    def _create_streak_tab(self):
        """Create streak tracker tab."""
        str_w = QWidget()
        self._panel_widgets.append(str_w)
        str_l = QVBoxLayout(str_w)
        str_l.setContentsMargins(12, 8, 12, 8)
        str_l.setSpacing(8)

        streak_cards = QHBoxLayout()
        streak_cards.setSpacing(6)
        self.str_card_maxw = MiniCard("MAX WIN STREAK", "—", "green")
        self.str_card_maxl = MiniCard("MAX LOSS STREAK", "—", "red")
        self.str_card_cur = MiniCard("CURRENT STREAK", "—", "text")
        self.str_card_avgw = MiniCard("AVG WIN STREAK", "—", "green")
        self.str_card_avgl = MiniCard("AVG LOSS STREAK", "—", "red")
        for c in [
            self.str_card_maxw,
            self.str_card_maxl,
            self.str_card_cur,
            self.str_card_avgw,
            self.str_card_avgl,
        ]:
            streak_cards.addWidget(c)
        str_l.addLayout(streak_cards)

        self.streak_table = QTableWidget()
        self.streak_table.setColumnCount(5)
        self.streak_table.setHorizontalHeaderLabels(
            ["Type", "Length", "Start #", "End #", "Total PnL"]
        )
        self.streak_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.streak_table.setAlternatingRowColors(True)
        self._tables_with_overrides.append(self.streak_table)
        str_l.addWidget(self.streak_table)
        self.sub_tabs.addTab(str_w, "Streaks")

    def _create_range_tab(self):
        """Create custom range WR tab."""
        rng_w = QWidget()
        self._panel_widgets.append(rng_w)
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
        self.sub_tabs.addTab(rng_w, "Custom Range")

    def _create_advanced_tab(self):
        """Create advanced stats tab."""
        adv_w = QWidget()
        self._panel_widgets.append(adv_w)
        adv_l = QVBoxLayout(adv_w)
        adv_l.setContentsMargins(12, 8, 12, 8)
        adv_l.setSpacing(8)

        self.adv_text = QLabel("Advanced Statistics (Monte Carlo & Risk of Ruin)")
        self.adv_text.setTextFormat(Qt.TextFormat.RichText)
        self.adv_text.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.adv_text.setWordWrap(True)
        self._adv_text = self.adv_text
        adv_l.addWidget(self.adv_text)

        self.dist_plot = pg.PlotWidget()
        self.dist_plot.setLabel("left", "Frequency", size="9pt")
        self.dist_plot.setLabel("bottom", "PnL ($)", size="9pt")
        self.dist_plot.setMinimumHeight(160)
        adv_l.addWidget(self.dist_plot)

        self.sub_tabs.addTab(adv_w, "Advanced")

    def _create_extended_tab(self):
        """Create extended metrics tab."""
        ext_w = QWidget()
        self._panel_widgets.append(ext_w)
        ext_l = QVBoxLayout(ext_w)
        ext_l.setContentsMargins(12, 8, 12, 8)
        ext_l.setSpacing(8)

        self.ext_text = QLabel("Extended Quant Metrics")
        self.ext_text.setTextFormat(Qt.TextFormat.RichText)
        self.ext_text.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.ext_text.setWordWrap(True)
        self._ext_text = self.ext_text
        ext_l.addWidget(self.ext_text)

        self.duration_plot = pg.PlotWidget()
        self.duration_plot.setLabel("left", "Frequency", size="9pt")
        self.duration_plot.setLabel("bottom", "Duration (min)", size="9pt")
        self.duration_plot.setMinimumHeight(160)
        ext_l.addWidget(self.duration_plot)

        self.sub_tabs.addTab(ext_w, "Extended")

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
        self.sub_tabs.setStyleSheet(sh.sub_tabs(t))
        for w in self._panel_widgets:
            w.setStyleSheet(sh.panel(t))
        for sp in getattr(self, "_splitters_with_overrides", []):
            sp.setStyleSheet(sh.splitter_handle(t))
        for tbl in self._tables_with_overrides:
            tbl.setStyleSheet(sh.analytics_table(t))
        if self._range_result is not None:
            self._range_result.setStyleSheet(sh.info_block_muted(t))
        if self._adv_text is not None:
            self._adv_text.setStyleSheet(sh.info_block(t))
        if self._ext_text is not None:
            self._ext_text.setStyleSheet(sh.info_block(t))
        # Plot backgrounds re-tint with the theme bg.
        for plot_attr in ("equity_plot", "dd_plot", "session_plot", "dist_plot", "duration_plot"):
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

        # Update specific curves and items
        if hasattr(self, "equity_curve"):
            self.equity_curve.setPen(pg.mkPen(QColor(sh.c(t, "chart_equity_line")), width=2))
            brush_eq = QColor(sh.c(t, "chart_equity_fill"))
            brush_eq.setAlpha(40)
            self.equity_fill.setBrush(pg.mkBrush(brush_eq))
            self.peak_curve.setPen(
                pg.mkPen(QColor(sh.c(t, "chart_peak_line")), width=1, style=Qt.PenStyle.DotLine)
            )
            self.eq_win_scatter.setSymbolBrush(QColor(sh.c(t, "chart_win_marker")))
            self.eq_win_scatter.setSymbolPen(pg.mkPen(QColor(sh.c(t, "chart_win_marker"))))
            self.eq_loss_scatter.setSymbolBrush(QColor(sh.c(t, "chart_loss_marker")))
            self.eq_loss_scatter.setSymbolPen(pg.mkPen(QColor(sh.c(t, "chart_loss_marker"))))

            self.dd_curve.setPen(pg.mkPen(QColor(sh.c(t, "chart_dd_line")), width=1.5))
            brush_dd = QColor(sh.c(t, "chart_dd_fill"))
            brush_dd.setAlpha(60)
            self.dd_fill.setBrush(pg.mkBrush(brush_dd))

            # Re-draw charts with new colors
            self._update_equity_curve()
            self._update_session_matrix()

    def _open_monte_carlo(self):
        """Open the bootstrap + shock-injection Monte Carlo dialog."""
        from gui.dialogs.advanced_mc_dialog import AdvancedMonteCarloDialog

        dialog = AdvancedMonteCarloDialog(self.engine, self)
        dialog.exec()

    def refresh_all(self, force: bool = False) -> None:
        """Refresh all analytics widgets.

        Skips expensive recomputation if the trade list hasn't changed
        since the last refresh (dirty-tracking).  Pass ``force=True``
        to bypass the check (e.g. after reset).
        """
        tc = len(self.engine.trades)
        if not force and tc == self._last_trade_count:
            return
        self._last_trade_count = tc
        self._update_equity_curve()
        self._update_session_matrix()
        self._update_streak_tracker()
        self._update_advanced_stats()
        self._update_extended_metrics()

    def _update_equity_curve(self):
        if not self.engine.trades:
            self.equity_curve.setData([], [])
            self.dd_curve.setData([], [])
            return

        eq = [self.engine.initial_equity]
        for t in self.engine.trades:
            eq.append(eq[-1] + t.pnl)

        x_data = list(range(len(eq)))
        self.equity_curve.setData(x_data, eq)

        baseline = [self.engine.initial_equity] * len(eq)
        self.equity_baseline.setData(x_data, baseline)

        # Peak equity
        peak = []
        mx = eq[0]
        for v in eq:
            mx = max(mx, v)
            peak.append(mx)
        self.peak_curve.setData(x_data, peak)

        # Drawdown calculation
        eq_arr = np.array(eq)
        peak_arr = np.array(peak)
        dd_pct = np.where(peak_arr > 0, (peak_arr - eq_arr) / peak_arr * 100, 0)

        self.dd_curve.setData(x_data, -dd_pct)
        self.dd_curve_fill_top.setData(x_data, [0] * len(x_data))
        self.dd_curve_fill_bot.setData(x_data, -dd_pct)

        # Trade markers on equity curve
        win_x, win_y, loss_x, loss_y = [], [], [], []
        for i, t in enumerate(self.engine.trades):
            if t.pnl > 0:
                win_x.append(i + 1)
                win_y.append(eq[i + 1])
            elif t.pnl < 0:
                loss_x.append(i + 1)
                loss_y.append(eq[i + 1])

        self.eq_win_scatter.setData(win_x, win_y)
        self.eq_loss_scatter.setData(loss_x, loss_y)

        self.equity_plot.autoRange()
        self.dd_plot.autoRange()

        # Update cards
        final_eq = eq[-1]
        total_ret = (final_eq - eq[0]) / eq[0] * 100
        max_dd = float(dd_pct.max())

        # Sharpe annualised on daily equity-curve returns
        from engine.analytics.equity_curve import build_equity_curve as _b
        from engine.analytics.equity_curve import sharpe_ratio as _s

        sharpe = _s(_b(self.engine.trades, self.engine.initial_equity))

        # Calculate PF — guard against zero gross loss
        wins = [t for t in self.engine.trades if t.pnl > 0]
        losses = [t for t in self.engine.trades if t.pnl < 0]
        gp = sum(t.pnl for t in wins)
        gl = abs(sum(t.pnl for t in losses))
        if gl > 0:
            pf = gp / gl
        elif gp > 0:
            pf = float("inf")
        else:
            pf = 0.0

        ret_color = "#26A69A" if total_ret >= 0 else "#EF5350"
        eq_color = "#26A69A" if final_eq >= self.engine.initial_equity else "#EF5350"
        sharpe_color = "#26A69A" if sharpe > 1.0 else "#FF9800" if sharpe > 0.5 else "#EF5350"
        pf_color = "#26A69A" if pf >= 1.5 else "#FF9800" if pf >= 1.0 else "#EF5350"

        self.eq_card_return.set_value(f"{total_ret:+.2f}%", ret_color)
        self.eq_card_equity.set_value(f"${final_eq:,.0f}", eq_color)
        self.eq_card_maxdd.set_value(f"{max_dd:.2f}%", "#EF5350")
        self.eq_card_sharpe.set_value(f"{sharpe:.2f}", sharpe_color)
        pf_str = "∞" if pf == float("inf") else f"{pf:.2f}"
        self.eq_card_pf.set_value(pf_str, pf_color)
        self.eq_card_trades.set_value(str(len(self.engine.trades)), "#E8E8EE")

    def _update_session_matrix(self):
        self.session_table.setRowCount(0)
        self.session_plot.clear()
        if not self.engine.trades:
            return

        session_names = [s[0] for s in SESSIONS]
        session_data = {s: [] for s in session_names}
        for t in self.engine.trades:
            if t.session in session_data:
                session_data[t.session].append(t)

        best_sess, best_pnl = "", -float("inf")
        worst_sess, worst_pnl = "", float("inf")
        most_active, most_count = "", 0

        bar_x, bar_heights, bar_colors = [], [], []

        for idx, sn in enumerate(session_names):
            trades = session_data[sn]
            if not trades:
                continue
            row = self.session_table.rowCount()
            self.session_table.insertRow(row)

            total = len(trades)
            wins = sum(1 for t in trades if t.pnl > 0)
            losses = sum(1 for t in trades if t.pnl < 0)
            wr = (wins / total * 100) if total > 0 else 0
            total_pnl = sum(t.pnl for t in trades)
            avg_pnl = total_pnl / total if total > 0 else 0
            best_trade = max((t.pnl for t in trades), default=0)

            if total_pnl > best_pnl:
                best_pnl, best_sess = total_pnl, sn
            if total_pnl < worst_pnl:
                worst_pnl, worst_sess = total_pnl, sn
            if total > most_count:
                most_count, most_active = total, sn

            items = [
                QTableWidgetItem(sn),
                QTableWidgetItem(str(total)),
                QTableWidgetItem(str(wins)),
                QTableWidgetItem(str(losses)),
                QTableWidgetItem(f"{wr:.1f}%"),
                QTableWidgetItem(f"{avg_pnl:+.2f}"),
                QTableWidgetItem(f"{total_pnl:+.2f}"),
                QTableWidgetItem(f"{best_trade:+.2f}"),
            ]

            pc = (
                QColor("#00E676")
                if total_pnl > 0
                else QColor("#FF1744")
                if total_pnl < 0
                else QColor("white")
            )
            items[6].setForeground(pc)
            items[5].setForeground(pc)
            wrc = (
                QColor("#00E676")
                if wr >= 50
                else QColor("#FFAB00")
                if wr >= 40
                else QColor("#FF1744")
            )
            items[4].setForeground(wrc)

            for col, item in enumerate(items):
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.session_table.setItem(row, col, item)

            # Bar chart data
            bar_x.append(idx)
            bar_heights.append(total_pnl)

            _t = get_theme()
            c_win = QColor(sh.c(_t, "chart_win_marker"))
            c_loss = QColor(sh.c(_t, "chart_loss_marker"))
            bar_colors.append(c_win if total_pnl >= 0 else c_loss)

        # Update session cards
        self.sess_card_best.set_value(f"{best_sess}\n${best_pnl:+,.0f}", "#26A69A")
        self.sess_card_worst.set_value(f"{worst_sess}\n${worst_pnl:+,.0f}", "#EF5350")
        self.sess_card_active.set_value(f"{most_active}\n{most_count} trades", "#5C8AFF")

        # Draw bar chart
        if bar_x:
            for x, h, c in zip(bar_x, bar_heights, bar_colors):
                bg = pg.BarGraphItem(x=[x], height=[h], width=0.6, brush=c)
                self.session_plot.addItem(bg)
            self.session_plot.getAxis("bottom").setTicks(
                [[(i, sn) for i, sn in enumerate(session_names)]]
            )

    def _update_streak_tracker(self):
        self.streak_table.setRowCount(0)
        if not self.engine.trades:
            return

        streaks = []
        ct = None
        sid = None
        ln = 0
        streak_pnl = 0
        for t in self.engine.trades:
            tt = "WIN" if t.pnl > 0 else "LOSS"
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
            streaks.append((ct, ln, sid, self.engine.trades[-1].id, streak_pnl))

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
            cur_color = "#26A69A" if last_type == "WIN" else "#EF5350"
        else:
            cur_str, cur_color = "—", "#E8E8EE"

        self.str_card_maxw.set_value(f"{max_ws} trades", "#26A69A")
        self.str_card_maxl.set_value(f"{max_ls} trades", "#EF5350")
        self.str_card_cur.set_value(cur_str, cur_color)
        self.str_card_avgw.set_value(f"{avg_ws:.1f} trades", "#26A69A")
        self.str_card_avgl.set_value(f"{avg_ls:.1f} trades", "#EF5350")

        for st, sl, ss, se, spnl in streaks:
            if sl < 2:
                continue
            row = self.streak_table.rowCount()
            self.streak_table.insertRow(row)
            items = [
                QTableWidgetItem(st),
                QTableWidgetItem(str(sl)),
                QTableWidgetItem(f"#{ss}"),
                QTableWidgetItem(f"#{se}"),
                QTableWidgetItem(f"{spnl:+.2f}"),
            ]
            c = QColor("#00E676") if st == "WIN" else QColor("#FF1744")
            items[0].setForeground(c)
            items[1].setForeground(c)
            items[4].setForeground(c)
            for col, item in enumerate(items):
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.streak_table.setItem(row, col, item)

    def calc_custom_range(self):
        if not self.engine.trades:
            self.range_result.setText("No trades available.")
            return

        df = self.date_from.date().toPython()
        dt = self.date_to.date().toPython()
        filtered = []
        for t in self.engine.trades:
            try:
                ed = pd.to_datetime(t.entry_time).date()
                if df <= ed <= dt:
                    filtered.append(t)
            except Exception:
                continue

        if not filtered:
            self.range_result.setText(f"No trades between {df} and {dt}.")
            for c in [
                self.rng_card_trades,
                self.rng_card_wr,
                self.rng_card_pnl,
                self.rng_card_avg,
                self.rng_card_pf,
            ]:
                c.set_value("—", "#E8E8EE")
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

        wr_color = "#26A69A" if wr >= 50 else "#FF9800" if wr >= 40 else "#EF5350"
        pnl_color = "#26A69A" if total_pnl >= 0 else "#EF5350"
        avg_color = "#26A69A" if avg >= 0 else "#EF5350"
        pf_color = "#26A69A" if pf >= 1.5 else "#FF9800" if pf >= 1.0 else "#EF5350"

        self.rng_card_trades.set_value(str(total), "#E8E8EE")
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

    def _update_advanced_stats(self):
        """Refresh the Probabilistic Edge / MC panel.

        Heavy work — the Monte Carlo bootstrap — runs in a background
        :class:`gui.workers.ComputeWorker`. While it's running we paint a
        spinner over the panel so the UI thread stays responsive even on
        large trade counts. The post-MC HTML is rendered in
        :pymeth:`_finalize_advanced_stats` once the worker emits its result.
        """
        self.dist_plot.clear()
        if not self.engine.trades:
            self.adv_text.setText("No trades for advanced stats.")
            return

        # Cancel any in-flight worker so back-to-back refreshes don't queue.
        prev = getattr(self, "_mc_worker", None)
        if prev is not None and prev.isRunning():
            prev.cancel()
            prev.wait(50)

        # Always (re)paint the synchronous PnL distribution histogram + mean
        # line so the chart updates immediately even if MC is still running.
        pnls_orig = [t.pnl for t in self.engine.trades]
        if len(set(pnls_orig)) > 1:
            y, x = np.histogram(pnls_orig, bins=min(30, len(set(pnls_orig))))
            bg = pg.BarGraphItem(x0=x[:-1], x1=x[1:], height=y, brush=QColor(0, 120, 212, 150))
            self.dist_plot.addItem(bg)
        mean_pnl = float(np.mean(pnls_orig))
        mean_line = pg.InfiniteLine(
            pos=mean_pnl, angle=90, pen=pg.mkPen("#00E676", width=2, style=Qt.PenStyle.DashLine)
        )
        self.dist_plot.addItem(mean_line)

        pnls = np.array(pnls_orig)
        n_trades = len(pnls)
        n_iter = min(1_000, max(100, n_trades * 10))
        init_eq = float(self.engine.initial_equity)

        # Lazily attach a spinner the first time we kick off async work.
        if not hasattr(self, "_adv_spinner"):
            self._adv_spinner = Spinner(self.adv_text, "Running Monte Carlo simulation…")
            self._adv_spinner.cover(self.adv_text)
        self._adv_spinner.start()

        worker = ComputeWorker(_bootstrap_mc_summary, pnls, init_eq, n_iter, parent=self)
        worker.finished.connect(self._finalize_advanced_stats)
        worker.error.connect(self._on_advanced_stats_error)
        self._mc_worker = worker
        worker.start()

    def _on_advanced_stats_error(self, exc: Exception) -> None:
        """Surface a worker exception inline so the user isn't left with a stuck spinner."""
        if hasattr(self, "_adv_spinner"):
            self._adv_spinner.stop()
        logger.exception("Advanced stats MC worker failed", exc_info=exc)
        self.adv_text.setText(f"Advanced stats failed: {exc!s}")

    def _finalize_advanced_stats(self, mc: dict) -> None:
        """Render the MC summary returned by the background worker."""
        if hasattr(self, "_adv_spinner"):
            self._adv_spinner.stop()

        if not self.engine.trades:
            self.adv_text.setText("No trades for advanced stats.")
            return

        total = len(self.engine.trades)
        wins = [t for t in self.engine.trades if t.pnl > 0]
        losses = [t for t in self.engine.trades if t.pnl < 0]
        wr = len(wins) / total if total > 0 else 0
        avg_w = sum(t.pnl for t in wins) / len(wins) if wins else 0
        avg_l = abs(sum(t.pnl for t in losses) / len(losses)) if losses else 0

        ror_text = "N/A"
        if avg_w > 0 and avg_l > 0:
            edge = wr - (1 - wr) * (avg_l / avg_w)
            ror_text = "100% (Negative Edge)" if edge <= 0 else "< 1% (Positive Edge)"

        n_iter = mc["n_iter"]
        avg_mc_dd = mc["avg_mc_dd"]
        max_mc_dd = mc["max_mc_dd"]
        min_mc_dd = mc["min_mc_dd"]
        p95_mc_dd = mc["p95_mc_dd"]
        p99_mc_dd = mc["p99_mc_dd"]
        avg_return = mc["avg_return"]
        best_return = mc["best_return"]
        worst_return = mc["worst_return"]
        p5_return = mc["p5_return"]
        p95_return = mc["p95_return"]
        prob_profit = mc["prob_profit"]

        kelly = 0.0
        if avg_w > 0 and avg_l > 0:
            kelly = wr - ((1 - wr) / (avg_w / avg_l))

        mo = (wr * avg_w) - ((1 - wr) * avg_l)
        mo_color = "#00E676" if mo > 0 else "#FF1744"
        ror_color = "#00E676" if "< 1%" in ror_text else "#FF1744"
        prob_color = (
            "#00E676" if prob_profit >= 70 else "#FFAB00" if prob_profit >= 50 else "#FF1744"
        )

        html = f"""
        <div style="font-size: 13px; color: #E0E0E0;">
            <h3 style="color: #3399FF; margin-top: 0;">Probabilistic Edge</h3>
            <table width="100%" cellspacing="8">
                <tr><td width="35%"><b>Expected Payoff (MO):</b></td>
                    <td style="color: {mo_color};"><b>${mo:.2f}</b> per trade</td></tr>
                <tr><td><b>Risk of Ruin:</b></td>
                    <td style="color: {ror_color};"><b>{ror_text}</b></td></tr>
                <tr><td><b>Kelly Criterion:</b></td>
                    <td><b>{kelly * 100:.1f}%</b> (Suggested max risk per trade)</td></tr>
                <tr><td><b>Probability of Profit:</b></td>
                    <td style="color: {prob_color};"><b>{prob_profit:.1f}%</b> ({n_iter:,} simulations)</td></tr>
            </table>
            <hr style="border: 1px solid #2C2C32; margin: 12px 0;">
            <h3 style="color: #FF9800;">Monte Carlo Simulation ({n_iter:,} Iterations)</h3>
            <p style="color: #9E9EA6; font-size: 11px;">Randomised trade order to assess risk under different market sequences.</p>
            <h4 style="color: #FFAB00; margin-top: 12px;">📉 Drawdown Analysis</h4>
            <table width="100%" cellspacing="6">
                <tr><td width="35%"><b>Average Max DD:</b></td><td style="color: #FFAB00;"><b>{avg_mc_dd:.2f}%</b></td></tr>
                <tr><td><b>Best Case DD:</b></td><td style="color: #00E676;"><b>{min_mc_dd:.2f}%</b></td></tr>
                <tr><td><b>Worst Case DD:</b></td><td style="color: #FF1744;"><b>{max_mc_dd:.2f}%</b></td></tr>
                <tr><td><b>95th Percentile DD:</b></td><td style="color: #FF9800;"><b>{p95_mc_dd:.2f}%</b></td></tr>
                <tr><td><b>99th Percentile DD:</b></td><td style="color: #FF1744;"><b>{p99_mc_dd:.2f}%</b></td></tr>
            </table>
            <h4 style="color: #00E676; margin-top: 12px;">Return Distribution</h4>
            <table width="100%" cellspacing="6">
                <tr><td width="35%"><b>Average Return:</b></td><td style="color: #3399FF;"><b>{avg_return:+.2f}%</b></td></tr>
                <tr><td><b>Best Case Return:</b></td><td style="color: #00E676;"><b>{best_return:+.2f}%</b></td></tr>
                <tr><td><b>Worst Case Return:</b></td><td style="color: #FF1744;"><b>{worst_return:+.2f}%</b></td></tr>
                <tr><td><b>5th Percentile:</b></td><td style="color: #FF9800;"><b>{p5_return:+.2f}%</b></td></tr>
                <tr><td><b>95th Percentile:</b></td><td style="color: #00E676;"><b>{p95_return:+.2f}%</b></td></tr>
            </table>
            <p style="color: #9E9EA6; font-size: 10px; margin-top: 12px;">
                💡 <b>Interpretation:</b> 95% of simulations had DD ≤ {p95_mc_dd:.1f}% and returns between {p5_return:+.1f}% and {p95_return:+.1f}%.
            </p>
        </div>
        """
        self.adv_text.setText(html)

    def _update_extended_metrics(self):
        self.duration_plot.clear()

        if not self.engine.trades:
            self.ext_text.setText("No trades for extended metrics.")
            return

        trades = self.engine.trades

        calmar = calculate_calmar_ratio(trades, self.engine.initial_equity)
        sortino = calculate_sortino_ratio(trades, self.engine.initial_equity)
        var_95 = calculate_var(trades, 0.95)
        cvar_95 = calculate_cvar(trades, 0.95)
        omega = calculate_omega_ratio(trades)
        ulcer = calculate_ulcer_index(trades, self.engine.initial_equity)

        # Phase 4 — risk of ruin + time under water
        from engine.analytics.risk_metrics import risk_of_ruin, time_under_water

        wins = [t.pnl for t in trades if t.pnl > 0]
        losses = [-t.pnl for t in trades if t.pnl < 0]
        if wins and losses:
            wr = len(wins) / len(trades)
            avg_w = sum(wins) / len(wins)
            avg_l = sum(losses) / len(losses)
            risk_pct = max(0.01, (avg_l / max(1.0, self.engine.initial_equity)) * 100.0)
            ror = risk_of_ruin(wr, avg_w, avg_l, risk_per_trade_pct=risk_pct, ruin_pct=0.5)
        else:
            ror = float("nan")

        eq_arr = np.array(
            [self.engine.initial_equity]
            + [
                sum(t.pnl for t in trades[: i + 1]) + self.engine.initial_equity
                for i in range(len(trades))
            ]
        )
        tuw = time_under_water(eq_arr)

        dur_stats = analyze_trade_duration(trades)
        streak_stats = calculate_consecutive_wins_losses(trades)

        durations = [t.duration_minutes for t in trades]
        if len(set(durations)) > 1:
            y, x = np.histogram(durations, bins=min(20, len(set(durations))))
            bg = pg.BarGraphItem(x0=x[:-1], x1=x[1:], height=y, brush=QColor(0, 229, 255, 150))
            self.duration_plot.addItem(bg)

        mean_dur = dur_stats.get("mean_duration", 0)
        if mean_dur > 0:
            mean_line = pg.InfiniteLine(
                pos=mean_dur, angle=90, pen=pg.mkPen("#00E676", width=2, style=Qt.PenStyle.DashLine)
            )
            self.duration_plot.addItem(mean_line)

        calmar_color = "#00E676" if calmar > 1.0 else "#FFAB00" if calmar > 0.5 else "#EF5350"
        sortino_color = "#00E676" if sortino > 1.0 else "#FFAB00" if sortino > 0.5 else "#EF5350"
        omega_color = "#00E676" if omega > 1.0 else "#FF1744"

        calmar_str = "∞" if calmar == float("inf") else f"{calmar:.2f}"
        sortino_str = (
            "∞" if sortino == float("inf") else ("N/A" if sortino != sortino else f"{sortino:.2f}")
        )
        omega_str = "∞" if omega == float("inf") else f"{omega:.2f}"

        html = f"""
        <div style="font-size: 13px; color: #E0E0E0;">
            <h3 style="color: #3399FF; margin-top: 0;">Extended Quant Metrics</h3>

            <h4 style="color: #00E676; margin-top: 12px;">Risk-Adjusted Returns</h4>
            <table width="100%" cellspacing="6">
                <tr>
                    <td width="40%"><b>Calmar Ratio:</b></td>
                    <td style="color: {calmar_color};"><b>{calmar_str}</b></td>
                    <td style="color: #9E9EA6; font-size: 10px;">(Ann. Return / Max DD)</td>
                </tr>
                <tr>
                    <td><b>Sortino Ratio:</b></td>
                    <td style="color: {sortino_color};"><b>{sortino_str}</b></td>
                    <td style="color: #9E9EA6; font-size: 10px;">(Downside deviation only)</td>
                </tr>
                <tr>
                    <td><b>Omega Ratio:</b></td>
                    <td style="color: {omega_color};"><b>{omega_str}</b></td>
                    <td style="color: #9E9EA6; font-size: 10px;">(Gains / Losses)</td>
                </tr>
                <tr>
                    <td><b>Ulcer Index:</b></td>
                    <td style="color: #FFAB00;"><b>{ulcer:.2f}%</b></td>
                    <td style="color: #9E9EA6; font-size: 10px;">(DD depth & duration)</td>
                </tr>
            </table>

            <h4 style="color: #FF9800; margin-top: 12px;">Risk Metrics</h4>
            <table width="100%" cellspacing="6">
                <tr>
                    <td width="40%"><b>VaR (95%):</b></td>
                    <td style="color: #FF1744;"><b>${var_95:.2f}</b></td>
                    <td style="color: #9E9EA6; font-size: 10px;">(Max loss, 95% conf.)</td>
                </tr>
                <tr>
                    <td><b>CVaR (95%):</b></td>
                    <td style="color: #FF1744;"><b>${cvar_95:.2f}</b></td>
                    <td style="color: #9E9EA6; font-size: 10px;">(Avg tail loss)</td>
                </tr>
            </table>

            <h4 style="color: #5C8AFF; margin-top: 12px;">Trade Duration Analysis</h4>
            <table width="100%" cellspacing="6">
                <tr>
                    <td width="40%"><b>Mean Duration:</b></td>
                    <td><b>{dur_stats.get("mean_duration", 0):.1f} min</b></td>
                    <td style="color: #9E9EA6; font-size: 10px;">({dur_stats.get("mean_duration", 0) / 60:.2f} hours)</td>
                </tr>
                <tr>
                    <td><b>Median Duration:</b></td>
                    <td><b>{dur_stats.get("median_duration", 0):.1f} min</b></td>
                    <td style="color: #9E9EA6; font-size: 10px;">({dur_stats.get("median_duration", 0) / 60:.2f} hours)</td>
                </tr>
                <tr>
                    <td><b>Min / Max:</b></td>
                    <td><b>{dur_stats.get("min_duration", 0):.0f} / {dur_stats.get("max_duration", 0):.0f} min</b></td>
                    <td style="color: #9E9EA6; font-size: 10px;">(Range)</td>
                </tr>
                <tr>
                    <td><b>25th / 75th Percentile:</b></td>
                    <td><b>{dur_stats.get("p25_duration", 0):.0f} / {dur_stats.get("p75_duration", 0):.0f} min</b></td>
                    <td style="color: #9E9EA6; font-size: 10px;">(IQR)</td>
                </tr>
            </table>

            <h4 style="color: #26A69A; margin-top: 12px;">Streak Analysis</h4>
            <table width="100%" cellspacing="6">
                <tr>
                    <td width="40%"><b>Max Win Streak:</b></td>
                    <td style="color: #26A69A;"><b>{streak_stats.get("max_win_streak", 0)} trades</b></td>
                </tr>
                <tr>
                    <td><b>Max Loss Streak:</b></td>
                    <td style="color: #EF5350;"><b>{streak_stats.get("max_loss_streak", 0)} trades</b></td>
                </tr>
                <tr>
                    <td><b>Avg Win Streak:</b></td>
                    <td style="color: #26A69A;"><b>{streak_stats.get("avg_win_streak", 0):.1f} trades</b></td>
                </tr>
                <tr>
                    <td><b>Avg Loss Streak:</b></td>
                    <td style="color: #EF5350;"><b>{streak_stats.get("avg_loss_streak", 0):.1f} trades</b></td>
                </tr>
            </table>

            <h4 style="color: #FF1744; margin-top: 12px;">Tail Risk</h4>
            <table width="100%" cellspacing="6">
                <tr>
                    <td width="40%"><b>Risk of Ruin (50% DD):</b></td>
                    <td style="color: {"#EF5350" if (ror == ror and ror > 0.05) else "#00E676"};">
                        <b>{("N/A" if ror != ror else f"{ror * 100:.2f}%")}</b>
                    </td>
                    <td style="color: #9E9EA6; font-size: 10px;">(Vince closed-form)</td>
                </tr>
                <tr>
                    <td><b>% Bars Underwater:</b></td>
                    <td style="color: #FFAB00;"><b>{tuw.pct_bars_underwater * 100:.1f}%</b></td>
                    <td style="color: #9E9EA6; font-size: 10px;">(Time below peak)</td>
                </tr>
                <tr>
                    <td><b>Max DD Run:</b></td>
                    <td style="color: #EF5350;"><b>{tuw.max_run_bars} trades</b></td>
                    <td style="color: #9E9EA6; font-size: 10px;">(Longest underwater streak)</td>
                </tr>
                <tr>
                    <td><b>Avg DD Run:</b></td>
                    <td style="color: #FFAB00;"><b>{tuw.avg_run_bars:.1f} trades</b></td>
                    <td style="color: #9E9EA6; font-size: 10px;">(Across {tuw.n_runs} runs)</td>
                </tr>
            </table>
        </div>
        """
        self.ext_text.setText(html)
