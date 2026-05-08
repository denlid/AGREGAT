"""Stats Tab — Premium statistics dashboard."""

import numpy as np
import pandas as pd
from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDateEdit,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from engine.analytics.equity_curve import (
    build_equity_curve as _build_eq,
)
from engine.analytics.equity_curve import (
    sharpe_ratio as _eq_sharpe,
)
from gui.styling import style_helpers as sh
from gui.styling.theme import (
    apply_drop_shadow,
    font_mono,
    get_theme,
    register_theme_listener,
)


# ─────────────────────────────────────────────
# Metric Card Widget
# ─────────────────────────────────────────────
class MetricCard(QFrame):
    """Single metric card with label, value, and optional sub-value.

    Phase 3: ``color`` may be either a literal hex (legacy) or a theme key
    (e.g. ``'green'``). Theme keys auto-track Light/Dark switches.
    """

    def __init__(
        self, title: str, value: str = "—", color: str = "text", sub: str = "", parent=None
    ):
        super().__init__(parent)
        self.setObjectName("metricCard")
        self.setMinimumHeight(72)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._color = color

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(3)

        self._title_lbl = QLabel(title)
        self._value_lbl = QLabel(value)
        layout.addWidget(self._title_lbl)
        layout.addWidget(self._value_lbl)

        if sub:
            self._sub_lbl = QLabel(sub)
            layout.addWidget(self._sub_lbl)
        else:
            self._sub_lbl = None

        self._apply_theme()
        register_theme_listener(self._apply_theme)
        # Phase 3 — subtle shadow for the modern flat look.
        apply_drop_shadow(self, blur=14, y_offset=2, alpha=70)

    @staticmethod
    def _resolve_color(color: str) -> str:
        t = get_theme()
        if isinstance(color, str) and color and not color.startswith("#"):
            return sh.c(t, color)
        return color

    def _apply_theme(self):
        t = get_theme()
        # Hover-style frame.
        self.setStyleSheet(
            "QFrame#metricCard {"
            f"  background: {sh.c(t, 'surface')};"
            f"  border: 1px solid {sh.c(t, 'border2')};"
            "  border-radius: 8px;}"
            "QFrame#metricCard:hover {"
            f"  border-color: {sh.c(t, 'accent')};"
            f"  background: {sh.c(t, 'elevated')};}}"
        )
        self._title_lbl.setStyleSheet(
            f"color: {sh.c(t, 'text3')}; font-size: 10px; font-weight: 600;"
            " text-transform: uppercase; letter-spacing: 0.8px;"
            " background: transparent;"
        )
        self._value_lbl.setStyleSheet(
            f"color: {self._resolve_color(self._color)}; font-size: 20px;"
            f" font-weight: 700; font-family: {font_mono()};"
            " background: transparent;"
        )
        if self._sub_lbl is not None:
            self._sub_lbl.setStyleSheet(
                f"color: {sh.c(t, 'text_dis')}; font-size: 10px; background: transparent;"
            )

    def update(self, value: str, color: str = None, sub: str = None):
        self._value_lbl.setText(value)
        if color:
            self._color = color
        if sub is not None and self._sub_lbl:
            self._sub_lbl.setText(sub)
        self._apply_theme()


# ─────────────────────────────────────────────
# Section Header
# ─────────────────────────────────────────────
class SectionHeader(QWidget):
    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 4)
        layout.setSpacing(8)

        line_l = QFrame()
        line_l.setFixedHeight(1)
        line_l.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        line_l.setFixedWidth(4)

        lbl = QLabel(title)

        line_r = QFrame()
        line_r.setFixedHeight(1)
        line_r.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        layout.addWidget(line_l)
        layout.addWidget(lbl)
        layout.addWidget(line_r)

        self._line_l = line_l
        self._line_r = line_r
        self._lbl = lbl
        self._apply_theme()
        register_theme_listener(self._apply_theme)

    def _apply_theme(self):
        t = get_theme()
        line_qss = f"background: {sh.c(t, 'border2')};"
        self._line_l.setStyleSheet(line_qss)
        self._line_r.setStyleSheet(line_qss)
        self._lbl.setStyleSheet(
            f"color: {sh.c(t, 'accent_light')}; font-size: 11px;"
            " font-weight: 700; text-transform: uppercase;"
            " letter-spacing: 1px; background: transparent;"
        )


# ─────────────────────────────────────────────
# Stats Tab
# ─────────────────────────────────────────────
class StatsTab(QWidget):
    def __init__(self, engine):
        super().__init__()
        self.engine = engine
        self._cards: dict[str, MetricCard] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Top bar ──────────────────────────────
        self._top_bar = QWidget()
        self._top_bar.setFixedHeight(44)
        tb = QHBoxLayout(self._top_bar)
        tb.setContentsMargins(16, 0, 16, 0)
        tb.setSpacing(8)

        self._title = QLabel("Statistics")
        tb.addWidget(self._title)
        tb.addStretch()

        tb.addWidget(QLabel("Period:"))
        self.period_cb = QComboBox()
        self.period_cb.addItems(["All Time", "Today", "This Week", "This Month", "Custom"])
        self.period_cb.setFixedWidth(120)
        self.period_cb.currentTextChanged.connect(self._on_period)
        tb.addWidget(self.period_cb)

        self.date_from = QDateEdit()
        self.date_from.setCalendarPopup(True)
        self.date_from.setDate(QDate(2021, 1, 1))
        self.date_from.setFixedWidth(110)
        self.date_from.hide()
        tb.addWidget(self.date_from)

        self.lbl_to = QLabel("→")
        self.lbl_to.hide()
        tb.addWidget(self.lbl_to)

        self.date_to = QDateEdit()
        self.date_to.setCalendarPopup(True)
        self.date_to.setDate(QDate.currentDate())
        self.date_to.setFixedWidth(110)
        self.date_to.hide()
        tb.addWidget(self.date_to)

        self.btn_apply = QPushButton("Apply")
        self.btn_apply.setFixedWidth(60)
        self.btn_apply.clicked.connect(self._recalc)
        self.btn_apply.hide()
        tb.addWidget(self.btn_apply)

        btn_refresh = QPushButton("↻")
        btn_refresh.setFixedSize(32, 28)
        btn_refresh.setToolTip("Refresh statistics")
        btn_refresh.clicked.connect(self._recalc)
        tb.addWidget(btn_refresh)

        root.addWidget(self._top_bar)

        # ── Scrollable content ────────────────────
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)

        self._content = QWidget()
        self.main_layout = QVBoxLayout(self._content)
        self.main_layout.setSpacing(4)
        self.main_layout.setContentsMargins(16, 12, 16, 16)

        self._scroll.setWidget(self._content)
        root.addWidget(self._scroll)

        self._empty_state_labels: list = []

        self.engine.stats_updated.connect(self._on_stats_updated)

        # Phase 3 — listen for theme switches and re-apply local QSS.
        self._apply_theme()
        register_theme_listener(self._apply_theme)

    def _apply_theme(self) -> None:
        t = get_theme()
        self._top_bar.setStyleSheet(sh.top_bar(t))
        self._title.setStyleSheet(sh.label_title(t))
        self._scroll.setStyleSheet(f"QScrollArea {{ border: none; background: {sh.c(t, 'bg')}; }}")
        self._content.setStyleSheet(f"background: {sh.c(t, 'bg')};")
        for lbl in self._empty_state_labels:
            lbl.setStyleSheet(
                f"color: {sh.c(t, 'text_dis')}; font-size: 14px;"
                " padding: 60px; background: transparent;"
            )

    # ─────────────────────────────────────────
    def _on_stats_updated(self, _):
        """Called on every stats_updated signal — only recalc if tab is visible."""
        # Avoid expensive recalc during fast simulation; main_window calls _recalc on tab switch
        pass

    def _on_period(self, p):
        custom = p == "Custom"
        self.date_from.setVisible(custom)
        self.date_to.setVisible(custom)
        self.lbl_to.setVisible(custom)
        self.btn_apply.setVisible(custom)
        if not custom:
            self._recalc()

    def _get_filtered_trades(self):
        trades = list(self.engine.trades)
        if not trades:
            return trades
        period = self.period_cb.currentText()
        if period == "All Time":
            return trades
        now = pd.Timestamp.now()
        if period == "Today":
            start = now.normalize()
        elif period == "This Week":
            start = (now - pd.Timedelta(days=now.dayofweek)).normalize()
        elif period == "This Month":
            start = now.replace(day=1).normalize()
        elif period == "Custom":
            start = pd.Timestamp(self.date_from.date().toPython())
            end = pd.Timestamp(self.date_to.date().toPython()) + pd.Timedelta(days=1)
            start_np, end_np = start.to_datetime64(), end.to_datetime64()
            return [t for t in trades if t.entry_time_np is not None and start_np <= t.entry_time_np < end_np]
        else:
            return trades
        start_np = start.to_datetime64()
        return [t for t in trades if t.entry_time_np is not None and t.entry_time_np >= start_np]

    # ─────────────────────────────────────────
    def _recalc(self):
        # Clear
        while self.main_layout.count():
            item = self.main_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._cards.clear()

        trades = self._get_filtered_trades()

        if not trades:
            empty = QLabel("No trades yet — load data and run a strategy")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._empty_state_labels.append(empty)
            empty.setStyleSheet(
                f"color: {sh.c(get_theme(), 'text_dis')}; font-size: 14px;"
                " padding: 60px; background: transparent;"
            )
            self.main_layout.addWidget(empty)
            return

        m = self._calc(trades)
        # Theme-keyed shortcuts that the MiniCard color resolver expands at
        # render time so light/dark switches stay consistent.
        G, R, A, W = "green", "red", "amber", "text"

        # ── Section: Overview ─────────────────
        self.main_layout.addWidget(SectionHeader("Overview"))
        grid0 = self._grid()
        self._card(
            grid0, 0, 0, "Total P&L", f"${m['total_pnl']:+,.2f}", G if m["total_pnl"] >= 0 else R
        )
        self._card(
            grid0, 0, 1, "Return", f"{m['return_pct']:+.2f}%", G if m["return_pct"] >= 0 else R
        )
        self._card(
            grid0,
            0,
            2,
            "Equity",
            f"${self.engine.equity:,.2f}",
            G if self.engine.equity >= self.engine.initial_equity else R,
        )
        self._card(grid0, 0, 3, "Max Drawdown", f"{m['max_dd_pct']:.2f}%", R)
        self.main_layout.addWidget(grid0)

        # ── Section: Trade Stats ──────────────
        self.main_layout.addWidget(SectionHeader("Trade Statistics"))
        grid1 = self._grid()
        self._card(grid1, 0, 0, "Total Trades", str(m["total"]), W)
        self._card(
            grid1,
            0,
            1,
            "Win Rate",
            f"{m['wr']:.1f}%",
            G if m["wr"] >= 50 else A if m["wr"] >= 40 else R,
        )
        self._card(
            grid1, 0, 2, "Wins / Losses", f"{m['n_w']} / {m['n_l']}", W, sub=f"BE: {m['n_be']}"
        )
        self._card(grid1, 0, 3, "Longs / Shorts", f"{m['n_longs']} / {m['n_shorts']}", W)
        self.main_layout.addWidget(grid1)

        # ── Section: P&L ─────────────────────
        self.main_layout.addWidget(SectionHeader("Profit & Loss"))
        grid2 = self._grid()
        self._card(grid2, 0, 0, "Avg Win", f"${m['avg_w']:,.2f}", G)
        self._card(grid2, 0, 1, "Avg Loss", f"${m['avg_l']:,.2f}", R)
        self._card(grid2, 0, 2, "Best Trade", f"${m['best']:+,.2f}", G)
        self._card(grid2, 0, 3, "Worst Trade", f"${m['worst']:+,.2f}", R)
        self.main_layout.addWidget(grid2)

        # ── Section: Risk ─────────────────────
        self.main_layout.addWidget(SectionHeader("Risk Metrics"))
        grid3 = self._grid()
        pf_str = f"{m['pf']:.2f}" if m["pf"] != float("inf") else "∞"
        rf_str = (
            f"{m['recovery']:.2f}"
            if abs(m["recovery"]) < 1e9
            else ("∞" if m["recovery"] > 0 else "-∞")
        )
        self._card(
            grid3,
            0,
            0,
            "Profit Factor",
            pf_str,
            G if m["pf"] >= 1.5 else A if m["pf"] >= 1.0 else R,
        )
        self._card(
            grid3,
            0,
            1,
            "Sharpe Ratio",
            f"{m['sharpe']:.2f}",
            G if m["sharpe"] > 1.0 else A if m["sharpe"] > 0.5 else R,
        )
        self._card(
            grid3,
            0,
            2,
            "Recovery Factor",
            rf_str,
            G if m["recovery"] > 2.0 else A if m["recovery"] > 1.0 else R,
        )
        self._card(
            grid3, 0, 3, "Expectancy", f"${m['expectancy']:,.2f}", G if m["expectancy"] > 0 else R
        )
        self.main_layout.addWidget(grid3)

        # ── Section: Analysis ─────────────────
        self.main_layout.addWidget(SectionHeader("Trade Analysis"))
        grid4 = self._grid()
        self._card(grid4, 0, 0, "Avg RR", f"{m['avg_rr']:.2f}R", W)
        self._card(
            grid4,
            0,
            1,
            "Avg Duration",
            f"{m['avg_dur']:.0f} min",
            W,
            sub=f"{m['avg_dur'] / 60:.1f} hours",
        )
        self._card(grid4, 0, 2, "Edge (MO)", f"${m['mo']:,.2f}", G if m["mo"] > 0 else R)
        self._card(grid4, 0, 3, "Gross Profit", f"${m['gross_profit']:,.2f}", G)
        self.main_layout.addWidget(grid4)

        # ── Section: Streaks ──────────────────
        self.main_layout.addWidget(SectionHeader("Streaks"))
        grid5 = self._grid()
        self._card(grid5, 0, 0, "Max Win Streak", f"{m['max_ws']} trades", G)
        self._card(grid5, 0, 1, "Max Loss Streak", f"{m['max_ls']} trades", R)
        self._card(grid5, 0, 2, "Current Streak", m["cur_streak"], m["cur_streak_color"])
        self._card(grid5, 0, 3, "Gross Loss", f"${m['gross_loss']:,.2f}", R)
        self.main_layout.addWidget(grid5)

        self.main_layout.addStretch()

    # ─────────────────────────────────────────
    def _grid(self) -> QWidget:
        w = QWidget()
        w.setStyleSheet("background: transparent;")
        g = QGridLayout(w)
        g.setSpacing(6)
        g.setContentsMargins(0, 0, 0, 0)
        for col in range(4):
            g.setColumnStretch(col, 1)
        w._grid = g
        return w

    _METRIC_TOOLTIPS = {
        "Total Trades": "Total number of completed trades.",
        "Win Rate": "Percentage of trades closed with positive PnL.",
        "Total PnL": "Sum of all per-trade PnL in account currency.",
        "Total Return": "Total PnL as a percentage of initial equity.",
        "Profit Factor": "Gross profit ÷ gross loss. >1.5 = robust, >2 = exceptional.",
        "Sharpe Ratio": "Annualised excess return ÷ daily-return volatility (×√252). >1 = good, >2 = excellent. Computed on equity curve, not per-trade returns.",
        "Sortino Ratio": "Like Sharpe but penalises only downside volatility. Better for skewed strategies.",
        "Calmar Ratio": "Annualised return ÷ max drawdown. >1 = solid, >3 = outstanding.",
        "Recovery Factor": "Total $ PnL ÷ absolute max drawdown $. >2 = robust capital efficiency.",
        "Max Drawdown": "Largest peak-to-trough equity decline (%).",
        "Avg Win": "Average $ won on winning trades.",
        "Avg Loss": "Average $ lost on losing trades.",
        "Best Trade": "Largest single-trade win.",
        "Worst Trade": "Largest single-trade loss.",
        "Mathematical Expectation": "P(win)·avgWin − P(loss)·avgLoss. Expected $ per trade. >0 = positive edge.",
        "Avg RR": "Average reward:risk ratio of winning trades. Negative RRs (losses) are excluded.",
    }

    def _card(self, grid_widget, row, col, title, value, color, sub=""):
        card = MetricCard(title, value, color, sub)
        if title in self._METRIC_TOOLTIPS:
            card.setToolTip(self._METRIC_TOOLTIPS[title])
        grid_widget._grid.addWidget(card, row, col)
        self._cards[title] = card
        return card

    # ─────────────────────────────────────────
    def _calc(self, trades) -> dict:
        total = len(trades)
        wins = [t for t in trades if t.pnl > 0]
        losses = [t for t in trades if t.pnl < 0]
        be = [t for t in trades if t.pnl == 0]
        n_w, n_l, n_be = len(wins), len(losses), len(be)

        wr = n_w / total * 100 if total else 0
        total_pnl = sum(t.pnl for t in trades)
        return_pct = (
            total_pnl / self.engine.initial_equity * 100 if self.engine.initial_equity else 0
        )
        gross_profit = sum(t.pnl for t in wins)
        gross_loss = abs(sum(t.pnl for t in losses))

        avg_w = gross_profit / n_w if n_w else 0
        avg_l = gross_loss / n_l if n_l else 0
        best = max((t.pnl for t in trades), default=0)
        worst = min((t.pnl for t in trades), default=0)

        p_win = n_w / total if total else 0
        p_loss = n_l / total if total else 0
        mo = p_win * avg_w - p_loss * avg_l

        pf = (
            gross_profit / gross_loss
            if gross_loss > 0
            else (float("inf") if gross_profit > 0 else 0)
        )

        # RR is now signed (negative on losses); average only the winning RRs
        rr_vals = [t.rr for t in trades if t.rr is not None and t.rr > 0]
        avg_rr = sum(rr_vals) / len(rr_vals) if rr_vals else 0

        # Streaks
        max_ws = max_ls = cur_ws = cur_ls = 0
        for t in trades:
            if t.pnl > 0:
                cur_ws += 1
                cur_ls = 0
                max_ws = max(max_ws, cur_ws)
            elif t.pnl < 0:
                cur_ls += 1
                cur_ws = 0
                max_ls = max(max_ls, cur_ls)
            else:
                cur_ws = cur_ls = 0

        if trades:
            last = trades[-1]
            if last.pnl > 0:
                cur_streak = f"{cur_ws}W"
                cur_streak_color = "green"
            elif last.pnl < 0:
                cur_streak = f"{cur_ls}L"
                cur_streak_color = "red"
            else:
                cur_streak = "BE"
                cur_streak_color = "amber"
        else:
            cur_streak = "—"
            cur_streak_color = "text2"

        # Max DD via NumPy (% and absolute $)
        pnl_arr = np.array([t.pnl for t in trades])
        eq_arr = self.engine.initial_equity + np.cumsum(pnl_arr)
        peak = np.maximum.accumulate(eq_arr)
        dd_arr_abs = peak - eq_arr
        dd_arr_pct = np.where(peak > 0, dd_arr_abs / peak * 100, 0)
        max_dd_pct = float(dd_arr_pct.max()) if len(dd_arr_pct) else 0
        max_dd_abs = float(dd_arr_abs.max()) if len(dd_arr_abs) else 0

        # Sharpe annualised on **daily equity-curve returns** — never multiply
        # a per-trade return by 252.
        eq_series = _build_eq(trades, self.engine.initial_equity)
        sharpe = _eq_sharpe(eq_series)

        # Recovery factor uses absolute drawdown ($)
        if max_dd_abs > 0:
            recovery = total_pnl / max_dd_abs
        elif total_pnl > 0:
            recovery = float("inf")
        else:
            recovery = 0.0
        expectancy = total_pnl / total if total else 0.0

        # Duration — use pre-computed duration_minutes from Trade (avoids pd.to_datetime)
        durations = [t.duration_minutes for t in trades if t.duration_minutes > 0]
        avg_dur = sum(durations) / len(durations) if durations else 0

        n_longs = sum(1 for t in trades if t.direction == "long")
        n_shorts = sum(1 for t in trades if t.direction == "short")

        return dict(
            total=total,
            n_w=n_w,
            n_l=n_l,
            n_be=n_be,
            wr=wr,
            total_pnl=total_pnl,
            return_pct=return_pct,
            gross_profit=gross_profit,
            gross_loss=gross_loss,
            avg_w=avg_w,
            avg_l=avg_l,
            best=best,
            worst=worst,
            mo=mo,
            pf=pf,
            avg_rr=avg_rr,
            max_ws=max_ws,
            max_ls=max_ls,
            cur_streak=cur_streak,
            cur_streak_color=cur_streak_color,
            max_dd_pct=max_dd_pct,
            max_dd_abs=max_dd_abs,
            sharpe=sharpe,
            recovery=recovery,
            avg_dur=avg_dur,
            n_longs=n_longs,
            n_shorts=n_shorts,
            expectancy=expectancy,
        )
