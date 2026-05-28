"""Stats Tab — Premium statistics dashboard."""

import logging
import time

import numpy as np
import pandas as pd
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
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

from engine.analytics.context import MetricsBundle
from engine.analytics.equity_curve import (
    MIN_RETURNS_FOR_RATIO,
    build_equity_curve as _build_eq,
    daily_returns as _eq_daily_returns,
)
from gui.analytics import sparkline_data as spk
from gui.analytics.insights_engine import generate_insights
from gui.analytics.milestone_tracker import MilestoneTracker
from gui.analytics.stats_calculator import StatsCalculator
from gui.components.toast import ToastLevel, ToastManager
from gui.workers.workers import Spinner
from gui.styling import style_helpers as sh
from gui.styling.theme import (
    get_theme,
    register_theme_listener,
    unregister_theme_listener,
)
from gui.widgets.empty_state import EmptyState
from gui.widgets.insight_card import InsightCard
from gui.widgets.metric_card import MetricCard
from gui.widgets.period_selector import PRESETS, PeriodSelector

logger = logging.getLogger("StrategyTester.gui.stats_tab")


# ---------------------------------------------------------------------------
# Metric metadata for the Explain dialog (Phase 1 EPIC 1.4) and sparkline kind
# ---------------------------------------------------------------------------

_CARD_FORMULAS: dict[str, tuple[str, str]] = {
    "Total P&L": ("sum(trade.pnl)", "Total net realised PnL over the selected period."),
    "Return": ("100 * total_pnl / initial_equity", ""),
    "Max Drawdown": (
        "max(peak - equity) / peak * 100",
        "Largest peak-to-trough decline of the equity curve, in percent.",
    ),
    "Win Rate": ("100 * n_wins / n_trades", ""),
    "Avg Win": ("sum(pnl > 0) / count(pnl > 0)", ""),
    "Avg Loss": ("|sum(pnl < 0)| / count(pnl < 0)", ""),
    "Profit Factor": (
        "gross_profit / gross_loss",
        ">1.0 = profitable, >1.5 = robust, >2 = exceptional.",
    ),
    "Sharpe Ratio": (
        "mean(daily_returns) / std(daily_returns) * sqrt(252)",
        "Annualised excess return per unit of total volatility.",
    ),
    "Sortino Ratio": (
        "mean(daily_returns) / downside_std * sqrt(252)",
        "Like Sharpe but only downside deviation is penalised.",
    ),
    "Calmar Ratio": ("CAGR / max_drawdown", "Return per unit of worst-case drawdown."),
    "Ulcer Index": (
        "sqrt(mean(drawdown_pct ** 2))",
        "RMS of percentage drawdowns over time.",
    ),
    "Omega Ratio": (
        "sum(gains above threshold) / sum(losses below threshold)",
        "Probability-weighted gain/loss ratio.",
    ),
    "VaR 95%": (
        "5th percentile of per-trade PnL",
        "With 95% probability a single trade won't lose more than this $.",
    ),
    "CVaR 95%": ("mean of worst 5% of trades", "Average loss when things go bad."),
    "Payoff Ratio": ("avg_win / avg_loss", ""),
    "Tail Ratio": ("|p95(pnl)| / |p5(pnl)|", ""),
    "CAGR": (
        "(final_equity / initial_equity) ** (1 / years) - 1",
        "Compound annual growth rate.",
    ),
    "Kelly %": (
        "win_rate - (1 - win_rate) / payoff",
        "Optimal fraction of equity to risk per trade.",
    ),
    "K-Ratio": ("slope(equity) / stderr(slope)", "Consistency of equity-curve growth."),
    "Common Sense": ("profit_factor * tail_ratio", ""),
    "Skewness": ("third moment of PnL distribution", ""),
    "Kurtosis": (
        "fourth moment of PnL distribution - 3",
        "Excess kurtosis. 0 = normal.",
    ),
    "Pain Index": ("mean(drawdown_pct)", "Mean drawdown depth."),
    "Time Underwater": ("longest run with equity < peak", ""),
    "Expectancy": ("total_pnl / n_trades", "Average dollars earned per trade."),
    "Recovery Factor": ("total_pnl / max_dd_abs", ""),
    "Avg RR": ("mean(trade.rr)", "Average risk-multiple across all trades."),
}


# Sparkline kind per metric — used by _update_card_sparklines.
_CARD_SPARK_KIND: dict[str, str] = {
    "Total P&L": "cumulative",
    "Return": "roll_ret",
    "Equity": "cumulative",
    "Max Drawdown": "underwater",
    "Win Rate": "roll_wr",
    "Sharpe Ratio": "roll_sharpe",
    "Sortino Ratio": "roll_sortino",
    "Avg Win": "hist_wins",
    "Avg Loss": "hist_losses",
    "Current Streak": "streak",
}


# ---------------------------------------------------------------------------
# Section Header
# ---------------------------------------------------------------------------
class SectionHeader(QWidget):
    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 4)
        layout.setSpacing(8)

        line_l = QFrame()
        line_l.setFixedWidth(3)
        line_l.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)

        lbl = QLabel(title)

        line_r = QFrame()
        line_r.setFixedHeight(1)
        line_r.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        layout.addWidget(line_l)
        layout.addWidget(lbl)
        layout.addWidget(line_r)

        self._accent = line_l
        self._line_r = line_r
        self._lbl = lbl
        self._apply_theme()
        register_theme_listener(self._apply_theme)

    def closeEvent(self, e):
        unregister_theme_listener(self._apply_theme)
        super().closeEvent(e)

    def _apply_theme(self):
        t = get_theme()
        self._accent.setStyleSheet(f"background: {sh.c(t, 'accent')}; border-radius: 1.5px;")
        self._line_r.setStyleSheet(f"background: {sh.c(t, 'border2')};")
        self._lbl.setStyleSheet(
            f"color: {sh.c(t, 'accent_light')}; font-size: 11px;"
            " font-weight: 700; text-transform: uppercase;"
            " letter-spacing: 1px; background: transparent;"
        )


# ─────────────────────────────────────────────
# Stats Tab
# ─────────────────────────────────────────────
class StatsTab(QWidget):
    sigPeriodChanged = Signal(str, object, object)  # kind, start, end — cross-tab sync

    def __init__(self, engine):
        super().__init__()
        self.engine = engine
        self._cards: dict[str, MetricCard] = {}
        # Track user-driven pin/hide choices so rebuilds preserve them.
        self._pinned_cards: set[str] = set()
        self._hidden_cards: set[str] = set()
        self._avg_rr_breakdown: dict = {"pos_avg": 0.0, "pos_count": 0, "neg_avg": 0.0, "neg_count": 0}
        self._ui_built: bool = False
        self._has_data: bool = False
        self._state: str = "empty"
        self._stats_calc = StatsCalculator()
        self._last_recalc_key = None
        self._last_sparkline_update_ts = 0.0
        self._date_filter_warning: str | None = None
        self._period_warning_label: QLabel | None = None
        # Phase 2 EPIC 2.2 — auto-insights state.
        self._insight_container: QWidget | None = None
        self._insight_layout: QVBoxLayout | None = None
        self._insight_section_header: QWidget | None = None
        self._insight_cards: list[InsightCard] = []
        self._dismissed_insights: set[str] = set()
        # Phase 2 EPIC 2.5 — milestone toasts.
        self._milestone_tracker = MilestoneTracker()
        self._toast_manager: ToastManager | None = None

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

        self.period_selector = PeriodSelector(self, default_kind="ALL")
        self.period_selector.period_changed.connect(self._on_period_changed)
        tb.addWidget(self.period_selector)

        # Current period state — updated by period_selector.
        self._period_kind: str = "ALL"
        self._period_start: pd.Timestamp | None = None
        self._period_end: pd.Timestamp | None = None

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

        # Loading spinner that covers the scroll area while we compute the
        # first metrics bundle. Stays hidden after that — subsequent
        # recalcs are fast enough that flashing the overlay would be more
        # distracting than helpful.
        self._spinner = Spinner(self, caption="Loading statistics…")
        self._spinner.cover(self)
        self._spinner_used = False

        # 🛡️ FIX: Guard against missing signal on engine
        if hasattr(self.engine, "stats_updated") and callable(getattr(self.engine, "stats_updated", None)):
            try:
                self.engine.stats_updated.connect(self._on_stats_updated)
                self._stats_signal_connected = True
            except (RuntimeError, TypeError):
                self._stats_signal_connected = False
        else:
            self._stats_signal_connected = False
        self._recalc_debounce = QTimer(self)
        self._recalc_debounce.setSingleShot(True)
        self._recalc_debounce.setInterval(250)
        self._recalc_debounce.timeout.connect(self._recalc)

        self._apply_theme()
        register_theme_listener(self._apply_theme)

    def closeEvent(self, e):
        unregister_theme_listener(self._apply_theme)
        if getattr(self, "_stats_signal_connected", False):
            try:
                self.engine.stats_updated.disconnect(self._on_stats_updated)
            except (RuntimeError, TypeError, AttributeError):
                pass
        self._recalc_debounce.stop()
        super().closeEvent(e)


    def attach_mode_controller(self, mc) -> None:
        """Wire dashboard Retail/Quant toggle from MainWindow.

        Retail hides the Risk-Adjusted Returns + Quality & Distribution
        blocks so retail users see a focused KPI surface. Quant restores
        them. Idempotent — safe to call repeatedly.
        """
        self._mode_controller = mc
        try:
            mc.mode_changed.connect(self._apply_dashboard_mode)
        except Exception:
            pass
        self._apply_dashboard_mode(mc.mode)

    def _apply_dashboard_mode(self, mode: str) -> None:
        quant = (mode == "quant")
        for w in getattr(self, "_quant_only_widgets", ()) or ():
            if w is not None:
                try:
                    w.setVisible(quant)
                except Exception:
                    pass

    def _apply_theme(self) -> None:
        t = get_theme()
        self._top_bar.setStyleSheet(sh.top_bar(t))
        self._title.setStyleSheet(sh.label_title(t))
        self._scroll.setStyleSheet(f"QScrollArea {{ border: none; background: {sh.c(t, 'bg')}; }}")
        self._content.setStyleSheet(f"background: {sh.c(t, 'bg')};")
        # MetricCard v2 widgets handle their own theme via theme listeners.

    def _on_stats_updated(self, _):
        # Live update only when visible (Stats tab active), and debounce
        # to avoid rebuilding on every step/trade.
        try:
            if not self.isVisible():
                return
            self._recalc_debounce.start()
        except Exception:
            logger.exception("StatsTab signal handler failed")

    def _on_period_changed(self, kind: str, start, end) -> None:
        """Slot for :class:`PeriodSelector` — record state, re-calc, and sync other tabs."""
        self._period_kind = kind
        self._period_start = start
        self._period_end = end
        try:
            self.sigPeriodChanged.emit(kind, start, end)
        except Exception:
            pass
        try:
            self._recalc_debounce.start()
        except Exception:
            self._recalc()

    def _resolve_period_window(self) -> tuple[np.datetime64 | None, np.datetime64 | None]:
        """Convert ``self._period_kind`` (and custom dates) to a [start, end) window.

        Returns ``(None, None)`` for the ``ALL`` preset (no filtering).
        """
        kind = self._period_kind
        if kind == "ALL" or kind not in (*PRESETS, "CUSTOM"):
            return None, None
        if kind == "CUSTOM":
            if self._period_start is None or self._period_end is None:
                return None, None
            start = np.datetime64(pd.Timestamp(self._period_start).normalize())
            end = np.datetime64(
                pd.Timestamp(self._period_end).normalize() + pd.Timedelta(days=1)
            )
            return start, end

        now = pd.Timestamp.utcnow().tz_localize(None)
        if kind == "1D":
            start = np.datetime64(now.normalize())
            end = start + np.timedelta64(1, "D")
        elif kind == "1W":
            start = np.datetime64((now - pd.Timedelta(days=7)).normalize())
            end = np.datetime64(now.normalize() + pd.Timedelta(days=1))
        elif kind == "1M":
            start = np.datetime64((now - pd.Timedelta(days=30)).normalize())
            end = np.datetime64(now.normalize() + pd.Timedelta(days=1))
        elif kind == "3M":
            start = np.datetime64((now - pd.Timedelta(days=90)).normalize())
            end = np.datetime64(now.normalize() + pd.Timedelta(days=1))
        elif kind == "6M":
            start = np.datetime64((now - pd.Timedelta(days=180)).normalize())
            end = np.datetime64(now.normalize() + pd.Timedelta(days=1))
        elif kind == "YTD":
            start = np.datetime64(pd.Timestamp(now.year, 1, 1))
            end = np.datetime64(now.normalize() + pd.Timedelta(days=1))
        elif kind == "1Y":
            start = np.datetime64((now - pd.Timedelta(days=365)).normalize())
            end = np.datetime64(now.normalize() + pd.Timedelta(days=1))
        else:
            return None, None
        return start, end

    def _get_filtered_trades(self):
        self._date_filter_warning = None
        cache = getattr(self.engine, "_shared_cache", None)
        if cache is not None:
            trades = cache.get_sorted_trades()
        else:
            trades = list(self.engine.trades)
        if not trades:
            return trades

        start, end = self._resolve_period_window()
        if start is None or end is None:
            return trades

        n = len(trades)
        et_arr = np.full(n, np.datetime64("NaT"), dtype="datetime64[ns]")
        try:
            for i, t in enumerate(trades):
                v = getattr(t, "entry_time_np", None)
                if v is not None:
                    et_arr[i] = v
        except Exception:
            logger.exception("StatsTab entry_time_np preload failed")

        valid = et_arr != np.datetime64("NaT")
        if not valid.all():
            try:
                for i in np.where(~valid)[0]:
                    v = getattr(trades[i], "entry_time", None)
                    if v is not None:
                        ts = pd.to_datetime(v, errors="coerce")
                        if pd.notna(ts):
                            et_arr[i] = ts
                            valid[i] = True
            except Exception:
                logger.exception("StatsTab entry_time fallback parsing failed")

        if not valid.any():
            msg = (
                "Date filter could not be applied: trades do not have usable entry timestamps."
            )
            self._date_filter_warning = msg
            logger.warning("StatsTab %s period=%s", msg, self._period_kind)
            return trades
        missing = int((~valid).sum())
        if missing:
            self._date_filter_warning = (
                f"Date filter applied partially: {missing} trades have no usable entry timestamp."
            )
            logger.warning(
                "StatsTab date filter applied partially: %s/%s trades missing timestamps for period %s",
                missing,
                n,
                self._period_kind,
            )
        mask = (et_arr >= start) & (et_arr < end) & valid
        return [trades[i] for i in np.where(mask)[0]]

    def _make_recalc_key(self, trades) -> tuple:
        return (
            len(trades),
            id(trades[-1]) if trades else None,
            self._period_kind,
            str(self._period_start) if self._period_start is not None else None,
            str(self._period_end) if self._period_end is not None else None,
        )

    def _recalc(self):
        try:
            trades = self._get_filtered_trades()
            has_data = bool(trades)
            key = self._make_recalc_key(trades)

            first_compute = (not self._ui_built) and has_data
            if first_compute:
                self._show_spinner("Loading statistics…")

            if hasattr(self, "_last_recalc_key") and key == self._last_recalc_key and not first_compute:
                logger.debug("StatsTab _recalc skip key=%r state=%s", key, self._state)
                return

            if has_data:
                if (not self._ui_built) or (not self._has_data):
                    self._destroy_layout()
                    self._build_static_layout()
                    self._ui_built = True
                    self._has_data = True
                    self._state = "data"
            else:
                if (not self._ui_built) or self._has_data:
                    self._destroy_layout()
                    self._show_empty_state(error=False)
                    self._ui_built = True
                    self._has_data = False
                    self._state = "empty"
                self._last_recalc_key = key
                return

            cache = getattr(self.engine, "_shared_cache", None)
            m = self._stats_calc.compute(trades, self.engine.initial_equity, shared_cache=cache)
            self._avg_rr_breakdown = m.get("avg_rr_breakdown", {})
            G, R, A, W = "green", "red", "amber", "text"
            self._safe_section("period_warning", self._update_period_warning)
            cards_ok = self._safe_section("cards", self._update_cards, m, G=G, R=R, A=A, W=W)
            self._safe_section("sparklines", self._update_card_sparklines, trades)
            self._safe_section("confidence_badges", self._apply_confidence_badges, trades)
            self._safe_section("insights", self._update_insights, m, trades)
            self._safe_section("milestones", self._check_milestones, trades)
            if cards_ok:
                self._last_recalc_key = key
            else:
                logger.warning("StatsTab refresh not cached because card update failed key=%r", key)
        except Exception:
            logger.exception("StatsTab._recalc failed")
            self._destroy_layout()
            self._show_empty_state(error=True)
            self._ui_built = True
            self._has_data = False
            self._state = "error"
        finally:
            self._hide_spinner()

    def _safe_section(self, name: str, fn, *args, **kwargs) -> bool:
        try:
            fn(*args, **kwargs)
            return True
        except Exception:
            logger.exception("StatsTab section %r failed", name)
            return False

    # ------------------------------------------------------------------
    # Loading spinner helpers (Polish pass)
    # ------------------------------------------------------------------

    def _show_spinner(self, caption: str = "Loading statistics…") -> None:
        if self._spinner is None:
            return
        try:
            self._spinner.cover(self)
            self._spinner.start(caption)
            self._spinner_used = True
        except Exception:
            logger.debug("spinner.start failed", exc_info=True)

    def _hide_spinner(self) -> None:
        if self._spinner is None or not self._spinner_used:
            return
        try:
            self._spinner.stop()
        except Exception:
            logger.debug("spinner.stop failed", exc_info=True)
        self._spinner_used = False

    # ------------------------------------------------------------------
    # Phase 2 EPIC 2.4 — confidence badges
    # ------------------------------------------------------------------

    def _apply_confidence_badges(self, trades) -> None:
        cache_key = self._make_recalc_key(trades)
        cached = getattr(self, "_n_daily_cache", None)
        if cached is not None and cached[0] == cache_key:
            n_daily = cached[1]
        else:
            try:
                eq = _build_eq(trades, float(getattr(self.engine, "initial_equity", 0.0) or 0.0))
                n_daily = int(_eq_daily_returns(eq).size)
            except Exception:
                n_daily = 0
            self._n_daily_cache = (cache_key, n_daily)
        for title in ("Sharpe Ratio", "Sortino Ratio", "Calmar Ratio", "K-Ratio"):
            card = self._cards.get(title)
            if card is None:
                continue
            try:
                card.set_confidence(n_daily, minimum=MIN_RETURNS_FOR_RATIO)
            except Exception:
                logger.debug("set_confidence failed for %s", title, exc_info=True)

    # ------------------------------------------------------------------
    # Phase 2 EPIC 2.2 — auto-insights
    # ------------------------------------------------------------------

    def _build_metrics_bundle(self, m: dict) -> MetricsBundle:
        """Convert the legacy ``m`` dict into a :class:`MetricsBundle`."""
        values: dict[str, float] = {
            "n_trades": float(m.get("total", 0) or 0),
            "n_wins": float(m.get("n_w", 0) or 0),
            "n_losses": float(m.get("n_l", 0) or 0),
            "win_rate": float(m.get("wr", 0.0) or 0.0),
            "total_pnl": float(m.get("total_pnl", 0.0) or 0.0),
            "gross_profit": float(m.get("gross_profit", 0.0) or 0.0),
            "gross_loss": float(m.get("gross_loss", 0.0) or 0.0),
            "avg_win": float(m.get("avg_w", 0.0) or 0.0),
            "avg_loss": float(m.get("avg_l", 0.0) or 0.0),
            "best": float(m.get("best", 0.0) or 0.0),
            "worst": float(m.get("worst", 0.0) or 0.0),
            "expectancy": float(m.get("expectancy", 0.0) or 0.0),
            "profit_factor": float(m.get("pf", 0.0) or 0.0),
            "avg_rr": float(m.get("avg_rr", 0.0) or 0.0),
            "sharpe": float(m.get("sharpe", 0.0) or 0.0),
            "sortino": float(m.get("sortino", 0.0) or 0.0),
            "calmar": float(m.get("calmar", 0.0) or 0.0),
            "max_dd_abs": float(m.get("max_dd_abs", 0.0) or 0.0),
            "max_dd_pct": float(m.get("max_dd_pct", 0.0) or 0.0) / 100.0,
        }
        return MetricsBundle(
            period_token=(self._period_kind,),
            filter_token=("all",),
            n_trades=int(values["n_trades"]),
            values=values,
        )

    def _update_insights(self, m: dict, trades) -> None:
        if self._insight_layout is None or self._insight_container is None:
            return
        while self._insight_layout.count():
            item = self._insight_layout.takeAt(0)
            if item is not None and item.widget() is not None:
                item.widget().deleteLater()
        self._insight_cards.clear()

        bundle = self._build_metrics_bundle(m)
        try:
            insights = generate_insights(bundle, trades)
        except Exception:
            logger.exception("StatsTab insights generation failed")
            insights = []
        insights = [i for i in insights if i.rule_id not in self._dismissed_insights]
        if not insights:
            self._insight_container.setVisible(False)
            if self._insight_section_header is not None:
                self._insight_section_header.setVisible(False)
            return
        if self._insight_section_header is not None:
            self._insight_section_header.setVisible(True)
        self._insight_container.setVisible(True)
        for ins in insights[:6]:
            card = InsightCard(ins, parent=self._insight_container)
            card.dismissed.connect(self._on_insight_dismissed)
            try:
                card.setWordWrap(True)
            except Exception:
                logger.debug("InsightCard word-wrap unavailable", exc_info=True)
            self._insight_layout.addWidget(card)
            self._insight_cards.append(card)

    def _on_insight_dismissed(self, rule_id: str) -> None:
        self._dismissed_insights.add(rule_id)

    # ------------------------------------------------------------------
    # Phase 2 EPIC 2.5 — milestone toasts
    # ------------------------------------------------------------------

    def _ensure_toast_manager(self) -> ToastManager | None:
        if self._toast_manager is not None:
            return self._toast_manager
        try:
            win = self.window()
            if win is None:
                return None
            mgr = getattr(win, "_toast_manager", None)
            if mgr is None:
                mgr = ToastManager(win)
                win._toast_manager = mgr  # cache on the main window
            self._toast_manager = mgr
            return mgr
        except Exception:
            return None

    def _check_milestones(self, trades) -> None:
        mgr = self._ensure_toast_manager()
        if mgr is None:
            return
        try:
            milestones = self._milestone_tracker.evaluate(
                trades,
                initial_equity=float(getattr(self.engine, "initial_equity", 0.0) or 0.0),
            )
        except Exception:
            logger.exception("milestone evaluate failed")
            return
        for ms in milestones:
            level = ToastLevel.SUCCESS if ms.level == "success" else ToastLevel.INFO
            try:
                mgr.show(f"{ms.icon}  {ms.headline}\n{ms.detail}", level=level, duration_ms=1200)
            except Exception:
                logger.debug("toast show failed for milestone %s", ms.key, exc_info=True)

    def _update_card_sparklines(self, trades) -> None:
        """Recompute and attach sparklines for the cards listed in ``_CARD_SPARK_KIND``."""
        if not trades:
            return
        now = time.monotonic()
        if now - getattr(self, "_last_sparkline_update_ts", 0.0) < 0.12:
            return
        try:
            pnl_np = np.asarray([float(t.pnl) for t in trades], dtype=np.float64)
        except Exception:
            return
        if pnl_np.size == 0:
            return

        initial_equity = float(getattr(self.engine, "initial_equity", 0.0) or 0.0)
        pos = pnl_np[pnl_np > 0]
        neg = np.abs(pnl_np[pnl_np < 0])

        def _series_for(kind: str):
            try:
                if kind == "cumulative":
                    return spk.cumulative_curve(pnl_np, initial_equity=initial_equity)
                if kind == "underwater":
                    return spk.underwater_curve(pnl_np, initial_equity=initial_equity)
                if kind == "roll_wr":
                    return spk.rolling_win_rate(pnl_np)
                if kind == "roll_ret":
                    return spk.rolling_return(pnl_np)
                if kind == "roll_sharpe":
                    return spk.rolling_sharpe(pnl_np)
                if kind == "roll_sortino":
                    return spk.rolling_sortino(pnl_np)
                if kind == "hist_wins" and pos.size > 0:
                    return spk.histogram_bars(pos)
                if kind == "hist_losses" and neg.size > 0:
                    return spk.histogram_bars(neg)
                if kind == "streak":
                    return spk.streak_bars(pnl_np)
            except Exception:
                logger.exception("sparkline kind=%s failed", kind)
            return []

        for title, kind in _CARD_SPARK_KIND.items():
            card = self._cards.get(title)
            if card is None:
                continue
            try:
                card.set_sparkline(_series_for(kind))
            except Exception:
                logger.exception("set_sparkline failed for %s", title)
        self._last_sparkline_update_ts = now

    def _show_empty_state(self, error: bool = False):
        while self.main_layout.count():
            item = self.main_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._cards.clear()
        empty = EmptyState(
            icon="⚠" if error else "\U0001F4CA",
            headline="Statistics unavailable" if error else "No trades yet",
            subtitle="An error occurred while calculating statistics" if error else "Load data and run a strategy",
            cta_text="",
            parent=self._content,
        )
        self.main_layout.addWidget(empty, stretch=1)
        self._period_warning_label = None

    def _destroy_layout(self):
        while self.main_layout.count():
            item = self.main_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._cards.clear()
        self._period_warning_label = None

    def _build_static_layout(self) -> None:
        """Build the card layout once. Subsequent refreshes update values in-place."""
        # ── Section: Insights (Phase 2 EPIC 2.2) ──
        self._insight_section_header = SectionHeader("Insights")
        self._insight_section_header.setVisible(False)
        self.main_layout.addWidget(self._insight_section_header)
        self._insight_container = QWidget()
        self._insight_container.setStyleSheet("background: transparent;")
        self._insight_layout = QVBoxLayout(self._insight_container)
        self._insight_layout.setSpacing(6)
        self._insight_layout.setContentsMargins(0, 0, 0, 4)
        self._insight_container.setVisible(False)
        self.main_layout.addWidget(self._insight_container)

        self._period_warning_label = QLabel("")
        self._period_warning_label.setWordWrap(True)
        self._period_warning_label.hide()
        self.main_layout.addWidget(self._period_warning_label)

        # ── Section: Overview ─────────────────
        self.main_layout.addWidget(SectionHeader("Overview"))
        self._grid0 = self._grid()
        self._card(self._grid0, 0, 0, "Total P&L", "—", "text")
        self._card(self._grid0, 0, 1, "Return", "—", "text")
        self._card(self._grid0, 0, 2, "Equity", "—", "text")
        self._card(self._grid0, 0, 3, "Max Drawdown", "—", "text")
        self.main_layout.addWidget(self._grid0)

        # ── Section: Trade Stats ──────────────
        self.main_layout.addWidget(SectionHeader("Trade Statistics"))
        self._grid1 = self._grid()
        self._card(self._grid1, 0, 0, "Total Trades", "—", "text")
        self._card(self._grid1, 0, 1, "Win Rate", "—", "text")
        self._card(self._grid1, 0, 2, "Wins / Losses", "—", "text", sub="BE: —")
        self._card(self._grid1, 0, 3, "Longs / Shorts", "—", "text")
        self.main_layout.addWidget(self._grid1)

        # ── Section: P&L ─────────────────────
        self.main_layout.addWidget(SectionHeader("Profit & Loss"))
        self._grid2 = self._grid()
        self._card(self._grid2, 0, 0, "Avg Win", "—", "text")
        self._card(self._grid2, 0, 1, "Avg Loss", "—", "text")
        self._card(self._grid2, 0, 2, "Best Trade", "—", "text")
        self._card(self._grid2, 0, 3, "Worst Trade", "—", "text")
        self.main_layout.addWidget(self._grid2)

        # ── Section: Risk ─────────────────────
        self.main_layout.addWidget(SectionHeader("Risk Metrics"))
        self._grid3 = self._grid()
        self._card(self._grid3, 0, 0, "Profit Factor", "—", "text")
        self._card(self._grid3, 0, 1, "Sharpe Ratio", "—", "text")
        self._card(self._grid3, 0, 2, "Recovery Factor", "—", "text")
        self._card(self._grid3, 0, 3, "Expectancy", "—", "text")
        self.main_layout.addWidget(self._grid3)

        # ── Section: Risk-Adjusted Returns ────
        self._hdr_risk_adj = SectionHeader("Risk-Adjusted Returns")
        self.main_layout.addWidget(self._hdr_risk_adj)
        self._grid_adv1 = self._grid()
        self._card(self._grid_adv1, 0, 0, "Sortino Ratio", "—", "text")
        self._card(self._grid_adv1, 0, 1, "Calmar Ratio", "—", "text")
        self._card(self._grid_adv1, 0, 2, "Ulcer Index", "—", "text")
        self._card(self._grid_adv1, 0, 3, "Omega Ratio", "—", "text")
        self.main_layout.addWidget(self._grid_adv1)

        self._grid_adv2 = self._grid()
        self._card(self._grid_adv2, 0, 0, "VaR 95%", "—", "text")
        self._card(self._grid_adv2, 0, 1, "CVaR 95%", "—", "text")
        self._card(self._grid_adv2, 0, 2, "Payoff Ratio", "—", "text")
        self._card(self._grid_adv2, 0, 3, "Tail Ratio", "—", "text")
        self.main_layout.addWidget(self._grid_adv2)

        # ── Section: Quality & Distribution ───
        self._hdr_quality = SectionHeader("Quality & Distribution")
        self.main_layout.addWidget(self._hdr_quality)
        self._grid_adv3 = self._grid()
        self._card(self._grid_adv3, 0, 0, "CAGR", "—", "text")
        self._card(self._grid_adv3, 0, 1, "Kelly %", "—", "text")
        self._card(self._grid_adv3, 0, 2, "K-Ratio", "—", "text")
        self._card(self._grid_adv3, 0, 3, "Common Sense", "—", "text")
        self.main_layout.addWidget(self._grid_adv3)

        self._grid_adv4 = self._grid()
        self._card(self._grid_adv4, 0, 0, "Skewness", "—", "text")
        self._card(self._grid_adv4, 0, 1, "Kurtosis", "—", "text")
        self._card(self._grid_adv4, 0, 2, "Pain Index", "—", "text")
        self._card(self._grid_adv4, 0, 3, "Time Underwater", "—", "text", sub="—")
        self.main_layout.addWidget(self._grid_adv4)

        # Quant-only block — toggled by ModeController if attached.
        self._quant_only_widgets = (
            self._hdr_risk_adj,
            self._grid_adv1,
            self._grid_adv2,
            self._hdr_quality,
            self._grid_adv3,
            self._grid_adv4,
        )

        # ── Section: Analysis ─────────────────
        self.main_layout.addWidget(SectionHeader("Trade Analysis"))
        self._grid4 = self._grid()
        self._card(self._grid4, 0, 0, "Avg RR", "—", "text")
        self._card(self._grid4, 0, 1, "Avg Duration", "—", "text", sub="—")
        self._card(self._grid4, 0, 2, "Edge (MO)", "—", "text")
        self._card(self._grid4, 0, 3, "Gross Profit", "—", "text")
        self.main_layout.addWidget(self._grid4)

        # ── Section: Streaks ──────────────────
        self.main_layout.addWidget(SectionHeader("Streaks"))
        self._grid5 = self._grid()
        self._card(self._grid5, 0, 0, "Max Win Streak", "—", "text")
        self._card(self._grid5, 0, 1, "Max Loss Streak", "—", "text")
        self._card(self._grid5, 0, 2, "Current Streak", "—", "text")
        self._card(self._grid5, 0, 3, "Gross Loss", "—", "text")
        self.main_layout.addWidget(self._grid5)

        self.main_layout.addStretch()

    def _update_period_warning(self) -> None:
        label = self._period_warning_label
        if label is None:
            return
        msg = self._date_filter_warning
        if not msg:
            label.hide()
            return
        t = get_theme()
        label.setText(msg)
        label.setStyleSheet(
            f"color: {sh.c(t, 'amber')}; background: transparent; "
            "font-size: 11px; padding: 2px 0 6px 0;"
        )
        label.show()

    def _update_cards(self, m: dict, *, G: str, R: str, A: str, W: str) -> None:
        """Update values/colors for pre-built cards."""
        def u(title: str, value: str, color: str = None, sub: str = None) -> None:
            c = self._cards.get(title)
            if c is None:
                return
            try:
                c.set_value_text(value, color=color, sub=sub)
            except Exception:
                # Fallback for any unexpected v2 incompatibility.
                c.update(value, color=color, sub=sub)

        u("Total P&L", f"${m['total_pnl']:+,.2f}", G if m["total_pnl"] >= 0 else R)
        u("Return", f"{m['return_pct']:+.2f}%", G if m["return_pct"] >= 0 else R)
        u("Equity", f"${self.engine.equity:,.2f}", G if self.engine.equity >= self.engine.initial_equity else R)
        u("Max Drawdown", f"{m['max_dd_pct']:.2f}%", R)

        u("Total Trades", str(m["total"]), W)
        u("Win Rate", f"{m['wr']:.1f}%", G if m["wr"] >= 50 else A if m["wr"] >= 40 else R)
        u("Wins / Losses", f"{m['n_w']} / {m['n_l']}", W, sub=f"BE: {m['n_be']}")
        u("Longs / Shorts", f"{m['n_longs']} / {m['n_shorts']}", W)

        u("Avg Win", f"${m['avg_w']:,.2f}", G)
        u("Avg Loss", f"${m['avg_l']:,.2f}", R)
        u("Best Trade", f"${m['best']:+,.2f}", G)
        u("Worst Trade", f"${m['worst']:+,.2f}", R)

        pf_str = f"{m['pf']:.2f}" if m["pf"] != float("inf") else "∞"
        rf_str = (
            f"{m['recovery']:.2f}" if abs(m["recovery"]) < 1e9
            else ("∞" if m["recovery"] > 0 else "-∞")
        )
        u("Profit Factor", pf_str, G if m["pf"] >= 1.5 else A if m["pf"] >= 1.0 else R)
        u("Sharpe Ratio", f"{m['sharpe']:.2f}", G if m["sharpe"] > 1.0 else A if m["sharpe"] > 0.5 else R)
        u("Recovery Factor", rf_str, G if m["recovery"] > 2.0 else A if m["recovery"] > 1.0 else R)
        u("Expectancy", f"${m['expectancy']:,.2f}", G if m["expectancy"] > 0 else R)

        u("Avg RR", f"{m['avg_rr']:.2f}R", W)
        u("Avg Duration", f"{m['avg_dur']:.0f} min", W, sub=f"{m['avg_dur'] / 60:.1f} hours")
        u("Edge (MO)", f"${m['mo']:,.2f}", G if m["mo"] > 0 else R)
        u("Gross Profit", f"${m['gross_profit']:,.2f}", G)

        u("Max Win Streak", f"{m['max_ws']} trades", G)
        u("Max Loss Streak", f"{m['max_ls']} trades", R)
        u("Current Streak", m["cur_streak"], m["cur_streak_color"])
        u("Gross Loss", f"${m['gross_loss']:,.2f}", R)

        # ── Advanced metrics ────────────────────────────────────────────
        def _fmt_ratio(v: float) -> str:
            if v is None or not np.isfinite(v):
                return "∞" if v == float("inf") else "—"
            return f"{v:+.2f}" if abs(v) < 1e4 else "∞"

        sortino = m.get("sortino", 0.0) or 0.0
        calmar = m.get("calmar", 0.0) or 0.0
        ulcer = m.get("ulcer", 0.0) or 0.0
        omega = m.get("omega", 0.0) or 0.0
        var95 = m.get("var95", 0.0) or 0.0
        cvar95 = m.get("cvar95", 0.0) or 0.0
        payoff = m.get("payoff", 0.0) or 0.0
        tail_ratio = m.get("tail_ratio", 0.0) or 0.0
        cagr = m.get("cagr", 0.0) or 0.0
        kelly = m.get("kelly", 0.0) or 0.0
        k_ratio = m.get("k_ratio", 0.0) or 0.0
        common_sense = m.get("common_sense", 0.0) or 0.0
        skew = m.get("skew", 0.0) or 0.0
        kurt = m.get("kurtosis", 0.0) or 0.0
        pain = m.get("pain_index", 0.0) or 0.0
        max_tuw = int(m.get("max_tuw", 0) or 0)
        avg_tuw = m.get("avg_tuw", 0.0) or 0.0

        u("Sortino Ratio", _fmt_ratio(sortino),
          G if sortino > 1.0 else A if sortino > 0.5 else R)
        u("Calmar Ratio", _fmt_ratio(calmar),
          G if calmar > 1.0 else A if calmar > 0.5 else R)
        u("Ulcer Index", f"{ulcer:.2f}",
          G if ulcer < 5 else A if ulcer < 10 else R)
        u("Omega Ratio", _fmt_ratio(omega),
          G if omega > 1.5 else A if omega > 1.0 else R)
        u("VaR 95%", f"${var95:,.2f}", R if var95 < 0 else W)
        u("CVaR 95%", f"${cvar95:,.2f}", R if cvar95 < 0 else W)
        u("Payoff Ratio", _fmt_ratio(payoff),
          G if payoff > 1.5 else A if payoff > 1.0 else R)
        u("Tail Ratio", _fmt_ratio(tail_ratio),
          G if tail_ratio > 1.0 else R)

        cagr_str = "—" if not np.isfinite(cagr) else f"{cagr:+.2f}%"
        u("CAGR", cagr_str,
          G if np.isfinite(cagr) and cagr > 0 else R)
        u("Kelly %", f"{kelly:+.1f}%",
          G if 0 < kelly <= 25 else A if kelly > 25 else R if kelly < 0 else W)
        u("K-Ratio", _fmt_ratio(k_ratio),
          G if k_ratio > 1.0 else A if k_ratio > 0.0 else R)
        u("Common Sense", _fmt_ratio(common_sense),
          G if common_sense > 1.5 else A if common_sense > 1.0 else R)

        u("Skewness", f"{skew:+.2f}",
          G if skew > 0 else R if skew < -0.5 else W)
        u("Kurtosis", f"{kurt:+.2f}",
          A if abs(kurt) > 3 else W)
        u("Pain Index", f"{pain:.2f}",
          G if pain < 2 else A if pain < 5 else R)
        u("Time Underwater", f"{max_tuw} bars",
          R if max_tuw > 0 else W,
          sub=f"avg: {avg_tuw:.0f}")

    def _grid(self) -> QWidget:
        w = QWidget()
        w.setStyleSheet("background: transparent;")
        g = QGridLayout(w)
        g.setSpacing(6)
        g.setContentsMargins(0, 0, 0, 0)
        for col in range(4):
            g.setColumnStretch(col, 1)
        return w

    _METRIC_TOOLTIPS = {
        # Overview
        "Total P&L": "Sum of all per-trade PnL in account currency.\nGreen = profitable, Red = losing.",
        "Return": "Total PnL as a percentage of initial equity.\n>30% = strong, >100% = exceptional (over the full window).",
        "Equity": "Current account equity = initial equity + total PnL + open positions.",
        "Max Drawdown": "Largest peak-to-trough equity decline (%).\nLower is better. <10% = excellent, >25% = risky.",
        # Trade Statistics
        "Total Trades": "Total number of completed trades.\n>100 = statistically meaningful, <30 = noisy.",
        "Win Rate": "Percentage of trades closed with positive PnL.\nNot a quality metric on its own — pair with Payoff Ratio.",
        "Wins / Losses": "Number of winning vs losing trades. BE = breakeven (exactly zero PnL).",
        "Longs / Shorts": "Trade count by direction. Strong imbalance can hint at directional bias.",
        # P&L
        "Avg Win": "Average $ won on winning trades.",
        "Avg Loss": "Average $ lost on losing trades (positive value).",
        "Best Trade": "Largest single-trade win.\nIf this is >>5× avg win — likely an outlier carrying the strategy.",
        "Worst Trade": "Largest single-trade loss.\nCompare against stop-loss to check risk management.",
        # Risk
        "Profit Factor": "Gross profit ÷ gross loss.\n>1.0 = profitable, >1.5 = robust, >2 = exceptional.",
        "Sharpe Ratio": "Annualised excess return ÷ daily-return σ (×√252).\n>1 = good, >2 = excellent, >3 = world-class.",
        "Recovery Factor": "Total $ PnL ÷ absolute max drawdown $.\n>2 = robust capital efficiency.",
        "Expectancy": "Average $ earned per trade. >0 = positive edge.",
        # Risk-Adjusted Returns
        "Sortino Ratio": "Like Sharpe, but only downside volatility is penalised.\n>1 = good, >2 = excellent. Usually higher than Sharpe.",
        "Calmar Ratio": "Annualised return ÷ max drawdown.\n>1 = solid, >3 = outstanding.",
        "Ulcer Index": "RMS of % drawdowns over time — captures both depth AND duration of pain.\nLower is better. <5 = comfortable, >10 = stressful equity curve.",
        "Omega Ratio": "Σ gains above threshold ÷ Σ losses below threshold (threshold = 0).\nProbability-weighted edge. >1.5 = strong, >2 = exceptional.",
        "VaR 95%": "Value-at-Risk at 95% confidence: with 95% probability, a single trade won't lose more than this $.\nMore negative = riskier tails.",
        "CVaR 95%": "Conditional VaR (Expected Shortfall) at 95%: average loss of the worst 5% of trades.\nThe \"if it goes bad, how bad\" number.",
        "Payoff Ratio": "Avg Win ÷ Avg Loss.\nFor low-WR systems you need >2, for high-WR ones >0.5 may suffice.",
        "Tail Ratio": "|p95(PnL)| ÷ |p5(PnL)|.\n>1 = right-tail dominates (fat winners), <1 = left-tail risk.",
        # Quality & Distribution
        "CAGR": "Compound Annual Growth Rate. Time-normalised return.\nUseful for comparing across different backtest windows.",
        "Kelly %": "Optimal fraction of equity to risk per trade for max log-growth.\nKelly = WR − (1−WR)/Payoff. Above 25% is usually over-betting; many practitioners use ½-Kelly.",
        "K-Ratio": "Slope of equity-curve regression ÷ stderr of slope.\nMeasures CONSISTENCY of returns. >1 = smooth growth, <0 = unstable.",
        "Common Sense": "Profit Factor × Tail Ratio.\nA QuantStats favourite — penalises systems that win often but bleed on tails.",
        "Skewness": "Asymmetry of PnL distribution.\n>0 = a few big winners drag the mean up. <0 = a few big losers hide behind many small winners (dangerous).",
        "Kurtosis": "\"Tailedness\" of PnL distribution (excess kurtosis, 0 = normal).\n>3 = fat tails — black-swan exposure. Pay attention.",
        "Pain Index": "Mean of all % drawdown values. Average pain felt over the window.\nLower = more comfortable curve.",
        "Time Underwater": "Longest period (in bars) the equity curve spent below its previous peak.",
        # Trade Analysis
        "Avg RR": "Overall average R-multiple across ALL trades. 1R = your initial risk per trade.",
        "Avg Duration": "Average holding time of a closed trade, in minutes.",
        "Edge (MO)": "Mathematical Odds, excluding BE trades:\nP(win)·avgWin − P(loss)·avgLoss. >0 = positive edge.",
        "Gross Profit": "Sum of all winning trades' PnL.",
        # Streaks
        "Max Win Streak": "Longest consecutive sequence of winning trades.",
        "Max Loss Streak": "Longest consecutive sequence of losing trades.\nCompare to your psychological tolerance.",
        "Current Streak": "Active streak. Format: <N>W (wins) or <N>L (losses).",
        "Gross Loss": "Sum of all losing trades' PnL (absolute value).",
    }

    def _card(self, grid_widget, row, col, title, value, color, sub=""):
        formula, extra = _CARD_FORMULAS.get(title, ("", ""))
        explanation = self._METRIC_TOOLTIPS.get(title, "")
        if extra:
            explanation = f"{explanation}\n\n{extra}".strip()
        card = MetricCard(
            title,
            value,
            color=color,
            sub=sub,
            formula=formula,
            explanation=explanation,
        )
        try:
            card.setMinimumWidth(180)
            card.setMaximumWidth(320)
        except Exception:
            logger.debug("MetricCard sizing unavailable", exc_info=True)
        if title == "Avg RR":
            bd = self._avg_rr_breakdown
            card.setToolTip(
                f"Overall: {value}\n"
                f"Positive: {bd['pos_count']} trades, avg {bd['pos_avg']:.2f}R\n"
                f"Negative: {bd['neg_count']} trades, avg {bd['neg_avg']:.2f}R"
            )
        elif title in self._METRIC_TOOLTIPS:
            card.setToolTip(self._METRIC_TOOLTIPS[title])
        # Wire pin/hide signals — keep the layout slot but stash the card
        # so user choices are remembered across rebuilds.
        try:
            card.hide_requested.connect(lambda *_a, t=title: self._on_card_hide(t))
            card.pin_toggled.connect(lambda pinned, t=title: self._on_card_pin(t, pinned))
        except Exception:
            pass
        if title in self._hidden_cards:
            card.setVisible(False)
        if title in self._pinned_cards:
            try:
                card.set_pinned(True)
            except Exception:
                pass
        gl = grid_widget.layout()
        if gl is not None and isinstance(gl, QGridLayout):
            gl.addWidget(card, row, col)
        self._cards[title] = card
        # Premium: set card cursor to indicate interactivity
        card.setCursor(Qt.CursorShape.PointingHandCursor) if hasattr(card, "setCursor") else None
        return card

    def _on_card_hide(self, title: str) -> None:
        """Hide a card and remember the choice for the session."""
        self._hidden_cards.add(title)
        card = self._cards.get(title)
        if card is not None:
            try:
                card.setVisible(False)
            except Exception:
                pass

    def _on_card_pin(self, title: str, pinned: bool) -> None:
        if pinned:
            self._pinned_cards.add(title)
        else:
            self._pinned_cards.discard(title)



