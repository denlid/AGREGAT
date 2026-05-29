"""
Trades Tab — Professional trade journal with filters, search, and advanced export.
"""

from __future__ import annotations

import csv
import logging
import numpy as np
import pandas as pd
import pyqtgraph as pg
from PySide6.QtCore import QDate, QEvent, QModelIndex, QPoint, Qt, QSortFilterProxyModel, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication
from PySide6.QtWidgets import QApplication
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDateEdit,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QTableView,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from gui.styling import style_helpers as sh
from gui.styling.theme import get_theme, register_theme_listener, unregister_theme_listener
from gui.widgets.empty_state import EmptyState
from gui.tabs.trades_table_model import (
    COLUMNS,
    COL_DIRECTION,
    COL_DURATION,
    COL_ENTRY_TIME,
    COL_EXIT_REASON,
    COL_ID,
    COL_PNL_DOLLARS,
    COL_RR,
    SkippedTradesModel,
    TradesFilterProxy,
    TradesTableModel,
    _calc_rr,
)

logger = logging.getLogger("StrategyTester.gui.trades_tab")


MAX_POPUP_PLOT_POINTS = 2000
_UNSET = object()


def _compute_synthetic_pnl(pnl_end: float, trade_id: int) -> np.ndarray:
    if abs(pnl_end) < 1e-9:
        return np.array([0.0, 0.0])
    rng = np.random.default_rng(trade_id)
    bars = min(240, max(30, int(abs(pnl_end) * 10)))
    noise = rng.normal(0, abs(pnl_end) * 0.03, bars)
    path = np.linspace(0, pnl_end, bars) + np.cumsum(noise)
    lo = min(0, pnl_end) * 1.5
    hi = max(0, pnl_end) * 1.5
    path = np.clip(path, lo, hi)
    path[-1] = pnl_end
    return path


def _downsample(arr: np.ndarray, max_pts: int = MAX_POPUP_PLOT_POINTS) -> np.ndarray:
    if arr.size > max_pts:
        step = max(1, arr.size // max_pts)
        return arr[::step]
    return arr


class _TradePnlPopup(QWidget):
    show_on_chart = Signal(object)
    closed = Signal()
    _GEN_COUNTER = 0

    def __init__(self, trade, close_arr, parent_table):
        super().__init__(parent_table)
        self.setWindowFlags(Qt.WindowType.Popup)
        self._trade = trade
        self._close_arr = close_arr
        self._alive = True
        self._gen = _TradePnlPopup._GEN_COUNTER
        _TradePnlPopup._GEN_COUNTER += 1
        self._plot = None
        self._loading_label = None
        self._draw_completed = False
        self._curve_item = None
        self._zero_item = None
        self._mae_item = None
        self._mfe_item = None
        self._exit_item = None
        self.setMinimumSize(320, 260)
        self.resize(400, 320)
        self._build_ui(trade)
        self._apply_theme()
        QTimer.singleShot(50, lambda: self._safe_draw(self._gen))

    def _safe(self) -> bool:
        if not self._alive:
            return False
        try:
            self.windowFlags()
            return True
        except (RuntimeError, AttributeError):
            self._alive = False
            return False

    def _build_ui(self, trade):
        l = QVBoxLayout(self)
        l.setContentsMargins(10, 8, 10, 8)
        l.setSpacing(6)

        hdr = QHBoxLayout()
        self._title = QLabel("")
        hdr.addWidget(self._title, stretch=1)
        self._source_badge = QLabel("")
        self._source_badge.setObjectName("popupSourceBadge")
        self._source_badge.setToolTip(
            "REAL \u2014 PnL curve reconstructed from real OHLC close prices.\n"
            "SYNTH \u2014 Real prices unavailable for this trade; the curve is a "
            "deterministic random walk seeded by trade id and anchored on the "
            "final PnL. Use for shape intuition only \u2014 do NOT read off MAE/MFE."
        )
        self._source_badge.setVisible(False)
        hdr.addWidget(self._source_badge)
        btn_close = QPushButton("\u2715")
        btn_close.setFixedSize(22, 22)
        btn_close.setToolTip("Close (Esc)")
        btn_close.clicked.connect(self.close)
        btn_close.setObjectName("popupCloseBtn")
        hdr.addWidget(btn_close)
        l.addLayout(hdr)

        # Metrics row: exit time + RR
        self._metrics = QLabel("")
        self._metrics.setStyleSheet("font-size: 10px; padding: 0px; background: transparent;")
        l.addWidget(self._metrics)

        self._loading_label = QLabel("Loading chart data...")
        self._loading_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        l.addWidget(self._loading_label)
        _old_gl = pg.getConfigOption('useOpenGL')
        pg.setConfigOptions(useOpenGL=False)
        self._plot = pg.PlotWidget()
        pg.setConfigOptions(useOpenGL=_old_gl)
        self._plot.setMinimumHeight(150)
        self._plot.setMaximumHeight(220)
        self._plot.showGrid(x=True, y=True, alpha=0.12)
        self._plot.setLabel("left", "PnL (pts)", "8pt")
        self._plot.setLabel("bottom", "Bars", "8pt")
        l.addWidget(self._plot)

        b = QHBoxLayout()
        b.addStretch()
        self.btn = QPushButton("Show on Chart")
        self.btn.setFixedHeight(24)
        self.btn.setEnabled(False)
        self.btn.clicked.connect(self._on_view_on_chart_clicked)
        b.addWidget(self.btn)
        l.addLayout(b)
        # Shadow removed: QGraphicsDropShadowEffect + FramelessWindow + pg.PlotWidget = GPU crash
        # Border/shadow styling handled in _apply_theme via QSS

    def _on_view_on_chart_clicked(self):
        self.show_on_chart.emit(self._trade)
        self.close()

    def _assert_gen(self) -> bool:
        return self._alive and self._gen == _TradePnlPopup._GEN_COUNTER - 1

    def _safe_draw(self, gen=None):
        if gen is not None and self._gen != gen:
            return
        if not self._safe():
            return
        if self._plot is None:
            self._show_plot_ready(False)
            return
        try:
            self._draw_pnl_curve(self._trade, self._close_arr)
            self._show_plot_ready(True)
            self._draw_completed = True
        except Exception:
            logger.exception("Trade popup draw failed")
            self._show_plot_ready(False)

    def _reuse(self, trade, close_arr):
        """Reuse popup with new trade data without recreating."""
        self._gen = _TradePnlPopup._GEN_COUNTER
        _TradePnlPopup._GEN_COUNTER += 1
        self._trade = trade
        self._close_arr = close_arr
        self._safe_draw(self._gen)

    def _draw_pnl_curve(self, trade, close_arr) -> None:
        t = get_theme()
        try:
            trade_id = getattr(trade, "id", "\u2014")
            pnl_val = float(getattr(trade, "pnl", 0.0) or 0.0)
            et = str(getattr(trade, "entry_time", "") or "")
            xt = str(getattr(trade, "exit_time", "") or "")
            direction = str(getattr(trade, "direction", "") or "").upper() or "\u2014"
            pnl_color = sh.c(t, "green_bright") if pnl_val >= 0 else sh.c(t, "red_bright")
            self._title.setText(
                f"<b>#{trade_id}</b> {direction} "
                f'<span style="color:{pnl_color}">{pnl_val:+,.2f}$</span> '
                f"{et}"
            )
            self._title.setStyleSheet(f"font-size:10px;color:{sh.c(t,'text2')};")

            entry_price = float(getattr(trade, "entry_price", 0.0) or 0.0)
            exit_price = float(getattr(trade, "exit_price", 0.0) or 0.0)
            rr = float(getattr(trade, "rr", 0.0) or 0.0)
            sl = float(getattr(trade, "stop_loss", 0.0) or 0.0)
            if rr == 0.0 and entry_price and exit_price and sl:
                risk = abs(entry_price - sl)
                reward = abs(exit_price - entry_price)
                if risk > 0:
                    rr = reward / risk
            rr_text = f"RR: {rr:.2f}" if rr else ""
            parts = [f"Exit: {xt}", rr_text] if rr_text else [f"Exit: {xt}"]
            self._metrics.setText("  |  ".join(parts))
            self._metrics.setStyleSheet(f"font-size:10px;color:{sh.c(t,'text3')};padding:0px;background:transparent;")
        except Exception:
            pass

        pnl_pts = self._compute_pnl_points(trade, close_arr)
        if pnl_pts is not None and pnl_pts.size > 1:
            self._render_pnl_curve(t, trade, pnl_pts)
            return

        pnl_end = float(getattr(trade, "pnl", 0.0) or 0.0)
        if abs(pnl_end) < 1e-9:
            self._show_plot_ready(False)
            return
        trade_id = int(getattr(trade, "id", 0))
        pnl_pts = _compute_synthetic_pnl(pnl_end, trade_id)
        pnl_pts = _downsample(pnl_pts)
        if pnl_pts.size > 1:
            self._render_pnl_curve(t, trade, pnl_pts, is_fallback=True)
        else:
            self._show_plot_ready(False)

    @staticmethod
    def _compute_pnl_points(trade, close_arr):
        """Convert close_arr to PnL points. Handles both real close prices
        and pre-computed PnL arrays (synthetic fallback)."""
        if not isinstance(close_arr, np.ndarray) or close_arr.size <= 1:
            return None
        entry_price = float(getattr(trade, "entry_price", 0.0) or 0.0)
        median_val = float(np.median(close_arr))
        # Detect if close_arr is already a PnL path (values centered near 0)
        # vs real close prices (values near entry_price)
        is_pnl_space = abs(entry_price) < 1e-9 or abs(median_val) < abs(entry_price) * 0.1
        if is_pnl_space:
            pnl_pts = close_arr.astype(float)
        else:
            direction = str(getattr(trade, "direction", "") or "").lower()
            pnl_pts = (close_arr - entry_price) if direction != "short" else (entry_price - close_arr)
        pnl_pts = pnl_pts[np.isfinite(pnl_pts)]
        return _downsample(pnl_pts)

    def _ensure_item(self, name, pen=None, brush=None):
        """Lazy-init and return a named PlotDataItem for setData reuse."""
        if not self._safe() or self._plot is None:
            return None
        item = getattr(self, name, _UNSET)
        if item is _UNSET or item is None:
            item = self._plot.plot(pen=pen, brush=brush)
            setattr(self, name, item)
        return item

    def _render_pnl_curve(self, t, trade, pnl_pts, is_fallback=False) -> bool:
        if not self._safe() or self._plot is None:
            return False
        self._update_source_badge(is_fallback)
        x = np.arange(pnl_pts.size, dtype=float)
        last_val = float(pnl_pts[-1]) if np.isfinite(pnl_pts[-1]) else 0.0
        pen_col_str = sh.c(t, "green_bright") if last_val >= 0 else sh.c(t, "red_bright")
        pen_col = QColor(pen_col_str)
        if not pen_col.isValid():
            pen_col = QColor(sh.c(t, "accent_light"))

        curve = self._ensure_item("_curve_item", pen=pg.mkPen(pen_col, width=2))
        if curve is not None:
            curve.setData(x, pnl_pts)
            curve.setPen(pg.mkPen(pen_col, width=2))

        zcol = QColor(sh.c(t, "border2"))
        zcol.setAlpha(120)
        zero = self._ensure_item("_zero_item", pen=pg.mkPen(zcol, width=1))
        if zero is not None:
            zero.setData([0, float(x[-1])], [0, 0])

        if not is_fallback:
            mae_val = float(getattr(trade, "mae", 0.0) or 0.0)
            mfe_val = float(getattr(trade, "mfe", 0.0) or 0.0)
            if mae_val > 0:
                mae_color = QColor(sh.c(t, "red_bright"))
                mae_color.setAlpha(100)
                mae_item = self._ensure_item("_mae_item", pen=pg.mkPen(mae_color, width=1, style=Qt.PenStyle.DashLine))
                if mae_item is not None:
                    mae_item.setData([0, float(x[-1])], [-mae_val, -mae_val])
            if mfe_val > 0:
                mfe_color = QColor(sh.c(t, "green_bright"))
                mfe_color.setAlpha(100)
                mfe_item = self._ensure_item("_mfe_item", pen=pg.mkPen(mfe_color, width=1, style=Qt.PenStyle.DashLine))
                if mfe_item is not None:
                    mfe_item.setData([0, float(x[-1])], [mfe_val, mfe_val])
            exit_price = float(getattr(trade, "exit_price", 0.0) or 0.0)
            if exit_price > 0 and pnl_pts.size > 0:
                exit_color = QColor(sh.c(t, "amber"))
                exit_color.setAlpha(80)
                exit_item = self._ensure_item("_exit_item", pen=pg.mkPen(exit_color, width=2))
                if exit_item is not None:
                    exit_item.setData([float(x[-1]), float(x[-1])], [0, float(pnl_pts[-1])])
        if self._safe() and self._plot is not None:
            self._plot.setXRange(0, float(x[-1]), padding=0.05)
        return True

    def _update_source_badge(self, is_fallback: bool) -> None:
        """Show REAL/SYNTH badge so users know whether the curve was built
        from real close prices or from a deterministic random walk."""
        badge = getattr(self, "_source_badge", None)
        if badge is None:
            return
        t = get_theme()
        if is_fallback:
            badge.setText("SYNTH")
            color = sh.c(t, "amber")
        else:
            badge.setText("REAL")
            color = sh.c(t, "green_bright")
        badge.setStyleSheet(
            f"QLabel#popupSourceBadge {{"
            f" background: transparent;"
            f" color: {color};"
            f" border: 1px solid {color};"
            f" border-radius: 6px;"
            f" font-size: 9px;"
            f" font-weight: 700;"
            f" letter-spacing: 0.5px;"
            f" padding: 1px 6px;"
            f" margin-right: 4px;"
            f"}}"
        )
        badge.setVisible(True)

    def _show_plot_ready(self, has_data: bool) -> None:
        if self._loading_label is not None:
            self._loading_label.setText("" if has_data else "No chart data available for this trade")
            self._loading_label.setVisible(not has_data)
        self.btn.setEnabled(has_data)
        if not has_data:
            badge = getattr(self, "_source_badge", None)
            if badge is not None:
                badge.setVisible(False)

    def _apply_theme(self):
        if not self._safe():
            return
        t = get_theme()
        self.setStyleSheet(
            f"background:{sh.c(t,'surface')};border:1px solid {sh.c(t,'border2')};border-radius:10px;"
        )
        btn_close = self.findChild(QPushButton, "popupCloseBtn")
        if btn_close:
            btn_close.setStyleSheet(
                f"QPushButton#popupCloseBtn {{ border: none; font-size: 14px; color: {sh.c(t,'text3')}; }}"
                f"QPushButton#popupCloseBtn:hover {{ color: {sh.c(t,'text')}; }}"
            )
        try:
            if self._plot is not None:
                self._plot.setBackground(QColor(sh.c(t, "chart_bg")))
                for axis_name in ("left", "bottom"):
                    ax = self._plot.getAxis(axis_name)
                    if ax:
                        ax.setPen(pg.mkPen(QColor(sh.c(t, "chart_axis"))))
                        ax.setTextPen(pg.mkPen(QColor(sh.c(t, "chart_axis_text"))))
        except RuntimeError:
            self._alive = False
        except Exception:
            pass
        if self._loading_label is not None:
            self._loading_label.setStyleSheet(
                f"color: {sh.c(t, 'text_dis')}; font-size: 12px; padding: 20px; background: transparent;"
            )

    def show_popup(self, gp):
        if not self._safe():
            return
        self.adjustSize()
        try:
            scr = QGuiApplication.screenAt(gp) or QGuiApplication.primaryScreen()
            if scr is not None:
                geo = scr.availableGeometry()
                x = int(gp.x())
                y = int(gp.y())
                if x + self.width() > geo.right():
                    x = geo.right() - self.width() - 8
                if y + self.height() > geo.bottom():
                    y = geo.bottom() - self.height() - 8
                if x < geo.left():
                    x = geo.left() + 8
                if y < geo.top():
                    y = geo.top() + 8
                gp = QPoint(x, y)
        except Exception:
            pass
        self.move(gp)
        self.setWindowOpacity(1.0)
        self.show()
        self.activateWindow()
        self.setFocus()

    def closeEvent(self, e):
        self._alive = False
        self._plot = None
        self._loading_label = None
        self._metrics = None
        try:
            self.closed.emit()
        except (RuntimeError, TypeError):
            pass
        try:
            super().closeEvent(e)
        except RuntimeError:
            pass

    def event(self, e):
        if e.type() == QEvent.Type.KeyPress and e.key() == Qt.Key.Key_Escape:
            self.close()
            return True
        return super().event(e)


class TradesTab(QWidget):
    trade_selected = Signal(object)

    def __init__(self, engine):
        super().__init__()
        self.engine = engine
        outer = QVBoxLayout(self)
        outer.setContentsMargins(6,6,6,6)
        outer.setSpacing(8)

        hdr = QHBoxLayout()
        hdr.setSpacing(8)
        t = QLabel("Trade Journal")
        tf = QFont()
        tf.setPointSize(14)
        tf.setBold(True)
        t.setFont(tf)
        hdr.addWidget(t)
        hdr.addStretch()
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Search trades...")
        self.search_box.setMinimumWidth(140)
        self.search_box.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._sd = QTimer(self)
        self._sd.setSingleShot(True)
        self._sd.setInterval(200)
        self._sd.timeout.connect(self._apply_filter)
        self.search_box.textChanged.connect(self._on_search_text_changed)
        hdr.addWidget(self.search_box)
        self._popup_debounce = QTimer(self)
        self._popup_debounce.setSingleShot(True)
        self._popup_debounce.setInterval(150)
        self._popup_debounce.timeout.connect(self._do_show_trade_popup)
        self._pending_popup = None
        self._close_col = None
        outer.addLayout(hdr)

        self.sub_tabs = QTabWidget()
        self.sub_tabs.setDocumentMode(True)
        ep = QWidget()
        sp = QWidget()
        ly = QVBoxLayout(ep)
        ly.setContentsMargins(0,0,0,0)
        ly.setSpacing(8)

        filt = QHBoxLayout()
        filt.setSpacing(8)
        filt.addWidget(QLabel("Filter:"))
        self.filter_cb = QComboBox()
        self.filter_cb.addItems(["All","Wins","Losses","BE","Long","Short","Large Wins (>2R)","Large Losses (>2R)","Long Duration (>1h)","Short Duration (<5m)","Custom Date"])
        self.filter_cb.setMinimumWidth(120)
        self.filter_cb.setMaximumWidth(200)
        self.filter_cb.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.filter_cb.currentTextChanged.connect(self._on_filter)
        filt.addWidget(self.filter_cb)
        self.d_from = QDateEdit()
        self.d_from.setCalendarPopup(True)
        self.d_from.setDate(QDate(2021,1,1))
        self.d_from.setMinimumWidth(100)
        self.d_from.hide()
        filt.addWidget(self.d_from)
        lt = QLabel("to")
        lt.hide()
        self.lbl_to = lt
        filt.addWidget(lt)
        self.d_to = QDateEdit()
        self.d_to.setCalendarPopup(True)
        self.d_to.setDate(QDate.currentDate())
        self.d_to.setMinimumWidth(100)
        self.d_to.hide()
        filt.addWidget(self.d_to)
        self.bf = QPushButton("Apply")
        self.bf.setMinimumWidth(60)
        self.bf.hide()
        self.bf.clicked.connect(self._apply_filter)
        filt.addWidget(self.bf)
        filt.addStretch()
        # Period sync state (from cross-tab sync)
        self._period_kind: str = "ALL"
        self._period_start = None
        self._period_end = None
        self.lbl_period = QLabel("")
        self.lbl_period.setStyleSheet(sh.summary_label(get_theme()))
        self.lbl_period.hide()
        filt.addWidget(self.lbl_period)
        # Drill-down banner (from Quant heatmaps)
        self._drilldown_payload = None
        self.lbl_drill = QLabel("")
        self.lbl_drill.setStyleSheet(sh.summary_label(get_theme()))
        self.lbl_drill.hide()
        filt.addWidget(self.lbl_drill)
        self.btn_clear_drill = QPushButton("Clear drill-down")
        self.btn_clear_drill.setFixedWidth(120)
        self.btn_clear_drill.setFixedHeight(28)
        self.btn_clear_drill.clicked.connect(self.clear_drilldown)
        self.btn_clear_drill.hide()
        filt.addWidget(self.btn_clear_drill)

        self.lbl_summary = QLabel("")
        self.lbl_summary.setStyleSheet(sh.summary_label(get_theme()))
        filt.addWidget(self.lbl_summary)
        self.btn_expand = QPushButton("Expand Data")
        self.btn_expand.setFixedWidth(100)
        self.btn_expand.setCheckable(True)
        self.btn_expand.toggled.connect(self._on_toggle_columns)
        filt.addWidget(self.btn_expand)
        self.btn_excel = QPushButton("Excel")
        self.btn_excel.setFixedWidth(80)
        self.btn_excel.clicked.connect(lambda: self.export_csv("excel"))
        filt.addWidget(self.btn_excel)
        ly.addLayout(filt)

        self._model = TradesTableModel(self)
        self._proxy = TradesFilterProxy(self)
        self._proxy.setSourceModel(self._model)
        self.table = QTableView()
        self.table.setModel(self._proxy)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setStyleSheet(sh.trades_table(get_theme()))
        hv = self.table.horizontalHeader()
        hv.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        hv.setStretchLastSection(True)
        self._core = [COL_ID,COL_DIRECTION,COL_PNL_DOLLARS,COL_RR,COL_DURATION,COL_EXIT_REASON,COL_ENTRY_TIME]
        self._all = list(range(len(COLUMNS)))
        self._expanded = False
        self._apply_column_set()
        sel = self.table.selectionModel()
        if sel is not None:
            sel.currentRowChanged.connect(self._on_current_row_changed)
        self.table.doubleClicked.connect(self._on_row_double_clicked)
        self._popup = None
        self._popup_visible = False
        self._popup_in_flight = False
        ly.addWidget(self.table)

        # Phase 1 — empty-state placeholder shown when there are no trades at all.
        self._empty_state = EmptyState(
            icon="\U0001F4DC",  # 📜 scroll
            headline="No trades yet",
            subtitle="Run a strategy or load a journal to populate this table",
            cta_text="",
            parent=self,
        )
        self._empty_state.hide()
        ly.addWidget(self._empty_state)

        sly = QVBoxLayout(sp)
        sly.setContentsMargins(0,0,0,0)
        sly.setSpacing(6)
        sh2 = QHBoxLayout()
        self.lbl_skip = QLabel("0 skipped")
        self.lbl_skip.setStyleSheet(sh.summary_label(get_theme()))
        sh2.addWidget(self.lbl_skip)
        sh2.addStretch()
        sly.addLayout(sh2)
        self._skip_model = SkippedTradesModel(self)
        self._skip_proxy = QSortFilterProxyModel(self)
        self._skip_proxy.setSourceModel(self._skip_model)
        self.st = QTableView()
        self.st.setModel(self._skip_proxy)
        self.st.setAlternatingRowColors(True)
        self.st.setSortingEnabled(True)
        self.st.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        hh = self.st.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        hh.setStretchLastSection(True)
        self.st.setColumnWidth(0, 160)
        self.st.setColumnWidth(1, 160)
        self.st.setColumnWidth(2, 90)
        self.st.setColumnWidth(3, 90)
        self.st.setColumnWidth(4, 80)
        self.st.setStyleSheet(sh.trades_table(get_theme()))
        sly.addWidget(self.st)

        self.sub_tabs.addTab(ep,"Trades")
        self.sub_tabs.addTab(sp,"Skipped")
        outer.addWidget(self.sub_tabs, stretch=1)

        engine.trade_completed.connect(self._on_new_trade)
        engine.data_loaded.connect(self._on_data_loaded_signal)
        self._rt = QTimer()
        self._rt.setSingleShot(True)
        self._rt.setInterval(300)
        self._rt.timeout.connect(self._apply_filter)
        try:
            engine.step_completed.connect(self._on_step_signal)
        except:
            pass
        register_theme_listener(self._apply_theme)

    def closeEvent(self, e):
        unregister_theme_listener(self._apply_theme)
        self._dismiss()
        self._sd.stop()
        self._rt.stop()
        self._popup_debounce.stop()
        try:
            self.engine.trade_completed.disconnect(self._on_new_trade)
        except (RuntimeError, TypeError, AttributeError):
            pass
        try:
            self.engine.data_loaded.disconnect(self._on_data_loaded_signal)
        except (RuntimeError, TypeError, AttributeError):
            pass
        try:
            self.engine.step_completed.disconnect(self._on_step_signal)
        except (RuntimeError, TypeError, AttributeError):
            pass
        super().closeEvent(e)

    def apply_drilldown(self, payload: dict) -> None:
        """Apply Quant heatmap drill-down filters and show UI indicator."""
        if not isinstance(payload, dict):
            return
        self._drilldown_payload = dict(payload)
        try:
            # Ensure base filter is "All" (drill-down is orthogonal)
            try:
                self.filter_cb.blockSignals(True)
                self.filter_cb.setCurrentText("All")
            finally:
                self.filter_cb.blockSignals(False)

            self._update_drilldown_badge()
            self._apply_filter(force=True)
        except Exception:
            logger.exception("apply_drilldown failed")

    def clear_drilldown(self) -> None:
        """Clear active Quant drill-down filters."""
        self._drilldown_payload = None
        self._update_drilldown_badge()
        self._apply_filter(force=True)

    def _update_drilldown_badge(self) -> None:
        p = self._drilldown_payload or {}
        if not p:
            self.lbl_drill.hide()
            self.btn_clear_drill.hide()
            return
        # Compact text
        parts = []
        if p.get("hour") is not None or p.get("weekday") is not None:
            wd = p.get("weekday")
            wd_lbl = ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"][int(wd)] if wd is not None and 0 <= int(wd) <= 6 else str(wd)
            if p.get("hour") is not None:
                parts.append(f"H={int(p['hour']):02d}")
            if wd is not None:
                parts.append(f"W={wd_lbl}")
        if p.get("year") is not None or p.get("month") is not None:
            y = p.get("year")
            m = p.get("month")
            if y is not None and m is not None:
                parts.append(f"{int(y):04d}-{int(m):02d}")
            elif y is not None:
                parts.append(f"Y={int(y)}")
            elif m is not None:
                parts.append(f"M={int(m):02d}")
        txt = "Drill-down: " + (" ".join(parts) if parts else "Active")
        self.lbl_drill.setText(txt)
        self.lbl_drill.show()
        self.btn_clear_drill.show()

    def _apply_column_set(self):
        if not hasattr(self,"_model") or self._model is None:
            return
        cols = self._all if self._expanded else self._core
        for ci in range(len(COLUMNS)):
            self.table.setColumnHidden(ci, ci not in cols)
        if self._expanded:
            for ci in cols:
                self.table.resizeColumnToContents(ci)

    def _on_toggle_columns(self, checked):
        self._expanded = checked
        self.btn_expand.setText("Core Data" if checked else "Expand Data")
        self._apply_column_set()

    def _apply_theme(self):
        t = get_theme()
        if hasattr(self,"lbl_summary"):
            self.lbl_summary.setStyleSheet(sh.summary_label(t))
        if hasattr(self, "lbl_period"):
            self.lbl_period.setStyleSheet(sh.summary_label(t))
        if hasattr(self, "lbl_drill"):
            self.lbl_drill.setStyleSheet(sh.summary_label(t))
        if hasattr(self,"table"):
            self.table.setStyleSheet(sh.trades_table(t))
        if hasattr(self, "st"):
            self.st.setStyleSheet(sh.trades_table(t))
        if hasattr(self, "lbl_skip"):
            self.lbl_skip.setStyleSheet(sh.summary_label(t))
        if hasattr(self,"_model"):
            self._model.refresh_theme()

    def _clear(self):
        self._model.set_trades([])
        self._last = None
        self.lbl_summary.setText("")
        self._period_kind = "ALL"
        self._period_start = None
        self._period_end = None
        self._drilldown_payload = None
        self._update_period_badge()
        self._update_drilldown_badge()
        self.search_box.clear()
        if hasattr(self, "_skip_model"):
            self._skip_model.set_skipped([])
            self.lbl_skip.setText("0 skipped")

    def _get_close_array(self, trade=None):
        close_arr = self._try_get_real_close(trade)
        if close_arr is not None:
            return close_arr
        return self._synthetic_close_from_pnl(trade)

    _CLOSE_COL_CANDIDATES = {"close", "close_px", "close_price", "c", "adj close", "close_prx"}

    def _try_get_real_close(self, trade=None):
        try:
            df = getattr(self.engine, "df", None)
            if df is None or len(df) == 0:
                return None
            df_token = id(df)
            cache_token = getattr(self, "_close_cache_token", None)
            if cache_token != df_token:
                # df instance changed (new load / reset) — invalidate cache.
                self._close_col = None
                self._close_arr_cache = None
                self._close_cache_token = df_token
            if self._close_col is None:
                for col in df.columns:
                    if str(col).lower().strip() in self._CLOSE_COL_CANDIDATES:
                        self._close_col = col
                        break
            if self._close_col is None or self._close_col not in df:
                return None
            close_all = self._close_arr_cache
            if close_all is None:
                close_all = df[self._close_col].to_numpy(copy=False)
                self._close_arr_cache = close_all
            if trade is not None:
                entry_idx = int(getattr(trade, "entry_index", 0) or 0)
                exit_idx = int(getattr(trade, "exit_index", len(close_all) - 1) or 0)
                exit_idx = min(exit_idx, len(close_all) - 1)
                if entry_idx < 0 or exit_idx < entry_idx:
                    return None
                close_slice = close_all[entry_idx:exit_idx + 1]
                # Only copy when we have to downsample; otherwise return a view.
                if close_slice.size > MAX_POPUP_PLOT_POINTS:
                    return _downsample(close_slice.copy())
                return close_slice
            return close_all
        except Exception:
            logger.exception("Failed to get close array")
        return None

    @staticmethod
    def _synthetic_close_from_pnl(trade):
        pnl = float(getattr(trade, "pnl", 0.0) or 0.0)
        trade_id = int(getattr(trade, "id", 0) or 0)
        return _compute_synthetic_pnl(pnl, trade_id)

    def _on_current_row_changed(self, cur, _):
        self._pending_popup = None
        self._popup_debounce.stop()
        self._dismiss()
        if not cur.isValid():
            self.trade_selected.emit(None)
            return
        si = self._proxy.mapToSource(cur)
        tr = self._model.trade_at(si.row())
        if tr is None:
            return
        self.trade_selected.emit(tr)
        self._pending_popup = (tr, si.row())
        self._popup_debounce.start()

    def _do_show_trade_popup(self):
        pending = self._pending_popup
        self._pending_popup = None
        if pending is None:
            return
        tr, src_row = pending
        if tr is None or not (0 <= src_row < self._model.rowCount()):
            return
        src_idx = self._model.index(src_row, 0)
        proxy_idx = self._proxy.mapFromSource(src_idx)
        if not proxy_idx.isValid():
            return
        self._show_trade_popup(tr, proxy_idx)

    def _on_row_double_clicked(self, idx):
        src = self._proxy.mapToSource(idx)
        tr = self._model.trade_at(src.row())
        if tr is None:
            return
        self._pending_popup = (tr, src.row())
        self._popup_debounce.stop()
        self._do_show_trade_popup()

    def _show_trade_popup(self, trade: object, index: QModelIndex) -> None:
        if self._popup_visible or self._popup_in_flight:
            return
        self._popup_in_flight = True
        vr = self.table.visualRect(index)
        if not vr.isValid():
            self._popup_in_flight = False
            self._popup_visible = False
            return
        gp = self.table.mapToGlobal(vr.topLeft())
        close_arr = self._get_close_array(trade)

        try:
            if self._popup is not None and self._popup._alive:
                popup = self._popup
                try:
                    popup.closed.disconnect(self._on_popup_closed)
                except (RuntimeError, TypeError):
                    pass
                popup.closed.connect(self._on_popup_closed)
                popup._reuse(trade, close_arr)
                popup.show_popup(gp)
            else:
                popup = _TradePnlPopup(trade, close_arr, self.table)
                popup.show_on_chart.connect(self._on_popup_show_on_chart)
                popup.closed.connect(self._on_popup_closed)
                popup.show_popup(gp)
                self._popup = popup
        except Exception:
            logger.exception("Failed to show trade popup")
            self._popup_in_flight = False
            self._popup_visible = False
            return

        self._popup_visible = True
        QTimer.singleShot(200, lambda: self._release_popup_flight())

    def _release_popup_flight(self) -> None:
        self._popup_in_flight = False

    def _find_main_window(self) -> QWidget | None:
        p = self.parent()
        while p is not None:
            try:
                if hasattr(p, "chart_tab") and hasattr(p, "tabs"):
                    return p
            except Exception:
                pass
            try:
                p = p.parent()
            except Exception:
                break
        return None

    def _on_popup_show_on_chart(self, trade: object) -> None:
        mw = self._find_main_window()
        if mw is None:
            logger.warning("Cannot find main window for chart navigation")
            return

        # Safe tab switching
        if not hasattr(mw, 'tabs') or mw.tabs is None:
            logger.warning("Main window tabs not available")
            return

        try:
            idx = -1
            try:
                idx = mw.tabs.indexOf(mw.chart_tab)
            except Exception:
                idx = -1
            mw.tabs.setCurrentIndex(idx if idx >= 0 else 0)
        except Exception as e:
            logger.warning(f"Failed to switch to chart tab: {e}")
            return

        # Safe focus_trade call
        if not hasattr(mw, 'chart_tab') or mw.chart_tab is None:
            logger.warning("Chart tab not available")
            return

        try:
            mw.chart_tab.focus_trade(trade, force=True)
        except Exception as e:
            logger.exception(f"focus_trade failed: {e}")

    def _on_popup_closed(self) -> None:
        self._popup_visible = False
        self._popup_in_flight = False

    def _dismiss(self) -> None:
        self._pending_popup = None
        self._popup_debounce.stop()
        self._popup_visible = False
        self._popup_in_flight = False
        if self._popup is not None:
            p = self._popup
            self._popup = None
            p._alive = False
            try:
                p.show_on_chart.disconnect()
            except (RuntimeError, TypeError):
                pass
            try:
                p.setVisible(False)
                p.close()
            except RuntimeError:
                pass

    def _refresh_skip(self):
        if not hasattr(self, "_skip_model"):
            return
        skip = list(getattr(self.engine, "skipped_trades", []) or [])
        if len(self._skip_model._data) == len(skip):
            return
        self._skip_model.set_skipped(skip)
        self.lbl_skip.setText(f"{len(skip)} skipped")

    def _on_filter(self, f):
        c = f == "Custom Date"
        self.d_from.setVisible(c)
        self.d_to.setVisible(c)
        self.lbl_to.setVisible(c)
        self.bf.setVisible(c)
        if not c:
            self._apply_filter()

    def _on_search_text_changed(self):
        self._sd.start()

    def _apply_filter(self, force=False):
        ft = self.filter_cb.currentText()
        st = self.search_box.text()
        cs = (
            len(self.engine.trades),
            ft,
            st.lower(),
            self.d_from.date().toPython() if ft == "Custom Date" else None,
            self.d_to.date().toPython() if ft == "Custom Date" else None,
            self._period_kind,
            self._period_start,
            self._period_end,
            tuple(sorted((self._drilldown_payload or {}).items())),
        )
        prev = getattr(self,"_last",None)
        if not force and prev == cs:
            return
        self._last = cs
        pc = prev[0] if prev else None
        current_len = len(self.engine.trades)
        if pc is not None and current_len > pc:
            new_trades = list(self.engine.trades)[pc:]
            self._model.append_trades(new_trades)
        elif pc != current_len:
            self._model.set_trades(list(self.engine.trades))
        d_from = None
        d_to = None
        if ft == "Custom Date":
            d_from = self.d_from.date()
            d_to = self.d_to.date()
        elif self._period_kind not in ("ALL", ""):
            d_from = QDate(self._period_start.year, self._period_start.month, self._period_start.day) if self._period_start is not None else QDate(2000, 1, 1)
            d_to = QDate(self._period_end.year, self._period_end.month, self._period_end.day) if self._period_end is not None else QDate.currentDate()
        p = self._drilldown_payload or {}
        if hasattr(self._proxy, "apply_all_filters"):
            self._proxy.apply_all_filters(
                filter_type=ft,
                search_text=st,
                d_from=d_from,
                d_to=d_to,
                hour=p.get("hour"),
                weekday=p.get("weekday"),
                year=p.get("year"),
                month=p.get("month"),
            )
        else:
            self._proxy.apply_filters(filter_type=ft, search_text=st, d_from=d_from, d_to=d_to)
        try:
            self.table.scrollToTop()
        except Exception:
            pass
        self._update_period_badge()
        self._update_summary()

    def _update_summary(self):
        n = self._proxy.rowCount()
        total = len(self.engine.trades) if self.engine is not None else 0
        # Toggle the empty-state placeholder vs the trades table.
        show_empty = total == 0
        try:
            self._empty_state.setVisible(show_empty)
            self.table.setVisible(not show_empty)
        except Exception:
            pass
        if n == 0:
            if total == 0:
                self.lbl_summary.setText("")
            else:
                self.lbl_summary.setText("No trades match filter")
            return
        visible = getattr(self._proxy, "_visible", None)
        numeric_keys = getattr(self._model, "_numeric_keys", {})
        pnls = numeric_keys.get(COL_PNL_DOLLARS)
        if visible is not None and pnls is not None and len(visible) == n:
            sub = pnls[visible]
            w = int(np.sum(sub > 0))
            l = int(np.sum(sub < 0))
            tp = float(np.sum(sub))
            wr = (w / n * 100.0) if n else 0.0
            self.lbl_summary.setText(
                f"Showing {n} trades | Wins: {w} | Losses: {l} | WR: {wr:.1f}% | Total P&L: ${tp:,.2f}"
            )
        else:
            self.lbl_summary.setText(f"Showing {n} trades")

    def _on_data_loaded_signal(self, _=None):
        self._clear()
        self._close_col = None

    def _on_step_signal(self, *a):
        self._refresh_skip()

    def sync_period(self, kind: str, start, end) -> None:
        """Cross-tab period sync — record period and re-apply filter."""
        self._period_kind = kind
        self._period_start = start
        self._period_end = end
        try:
            self._apply_filter()
        except Exception:
            pass

    def _update_period_badge(self) -> None:
        if not hasattr(self, "lbl_period"):
            return
        if self._period_kind in ("ALL", ""):
            self.lbl_period.hide()
            return
        self.lbl_period.setText(f"Period: {self._period_kind}")
        self.lbl_period.show()

    def _on_new_trade(self, t):
        if not self.isVisible():
            return
        if not self._rt.isActive():
            self._rt.start()

    def export_csv(self, fmt="csv", filtered_only=False):
        if filtered_only:
            trades = []
            for r in range(self._proxy.rowCount()):
                src = self._proxy.mapToSource(self._proxy.index(r,0))
                t = self._model.trade_at(src.row())
                if t is not None:
                    trades.append(t)
        else:
            trades = list(self.engine.trades)
        if not trades:
            logger.warning("No trades")
            return
        if fmt == "excel":
            p,_ = QFileDialog.getSaveFileName(self,"Export","trades.xlsx","Excel (*.xlsx)")
        else:
            p,_ = QFileDialog.getSaveFileName(self,"Export","trades.csv","CSV (*.csv)")
        if not p:
            return
        # Precompute RR array from model cache (O(1) per trade)
        rr_cache = getattr(self._model, "_numeric_keys", {}).get(COL_RR, None)
        def _rr(t, idx):
            if rr_cache is not None and 0 <= idx < rr_cache.size:
                v = float(rr_cache[idx])
                return f"{v:+.2f}R" if np.isfinite(v) and v != 0 else "N/A"
            rv = _calc_rr(t)
            return f"{rv:+.2f}R" if np.isfinite(rv) and rv != 0 else "N/A"
        try:
            if fmt == "excel":
                try:
                    import openpyxl
                except ImportError:
                    from PySide6.QtWidgets import QMessageBox
                    QMessageBox.warning(self,"Missing","openpyxl required")
                    return
                df = pd.DataFrame([{
                    "ID":t.id,"Direction":t.direction.upper(),
                    "Entry Time":pd.Timestamp(t.entry_time).isoformat(),
                    "Exit Time":pd.Timestamp(t.exit_time).isoformat(),
                    "Entry Price":t.entry_price,"Exit Price":t.exit_price,
                    "PnL (pts)":t.pnl_points,"PnL ($)":t.pnl,
                    "Volume":t.volume,"Duration (min)":t.duration_minutes,
                    "Exit Reason":t.exit_reason,"MAE":t.mae,"MFE":t.mfe,
                    "MAE ($)":getattr(t,"mae_dollars",0.0),
                    "MFE ($)":getattr(t,"mfe_dollars",0.0),
                    "MAE%":(t.mae/t.entry_price*100) if t.entry_price>0 else 0,
                    "MFE%":(t.mfe/t.entry_price*100) if t.entry_price>0 else 0,
                    "Session":t.session,"RR":_rr(t, i),
                } for i, t in enumerate(trades)])
                df.to_excel(p,index=False,engine="openpyxl")
            else:
                with open(p,"w",newline="",encoding="utf-8") as f:
                    w = csv.writer(f)
                    w.writerow(COLUMNS)
                    for i, t in enumerate(trades):
                        rr_str = _rr(t, i)
                        mp = (t.mae/t.entry_price*100) if t.entry_price>0 else 0
                        mfp = (t.mfe/t.entry_price*100) if t.entry_price>0 else 0
                        ei = pd.Timestamp(t.entry_time).isoformat()
                        xi = pd.Timestamp(t.exit_time).isoformat()
                        w.writerow([t.id,t.direction.upper(),ei,xi,t.entry_price,
                            t.exit_price,t.pnl_points,t.pnl,t.volume,t.duration_minutes,
                            t.exit_reason,t.mae,t.mfe,f"{getattr(t,'mae_dollars',0):.2f}",
                            f"{getattr(t,'mfe_dollars',0):.2f}",f"{mp:.2f}%",f"{mfp:.2f}%",
                            t.session,rr_str])
            logger.info(f"Exported {len(trades)} trades")
        except Exception as e:
            logger.error(f"Export failed: {e}",exc_info=True)
