"""
Phase 5 — quant analytics tab.

Sub-tabs:
- MAE/MFE scatter + distribution
- R-multiples histogram + cumulative R curve
- Hour×weekday + month×year heatmaps
- Drawdown episodes table + exit-reason attribution
- Slippage simulator (interactive what-if)
- Strategy comparison (load .agbk vs current run)
"""

from __future__ import annotations

import logging
from typing import List, Optional

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from engine.persistence.persistence import load_session_raw
from engine.analytics.trade_analytics import (
    compare_runs,
    drawdown_durations,
    exit_reason_attribution,
    hour_weekday_heatmap,
    mae_mfe_arrays,
    month_year_heatmap,
    r_multiples,
    simulate_slippage,
    summarise_run,
)
from gui.styling import style_helpers as sh
from gui.styling.theme import get_theme, register_theme_listener
from gui.workers.workers import ComputeWorker, Spinner


def _compute_heatmaps(trades: list) -> dict:
    """Worker-thread helper: aggregate hour×weekday and year×month PnL grids.

    Pure NumPy/Python; never touches Qt so it is safe to call from a
    :class:`gui.workers.ComputeWorker`. The render step in
    :pymeth:`QuantTab._finalize_heatmaps` paints the returned grids onto the
    GUI heatmap widgets.
    """
    return {
        "hw": hour_weekday_heatmap(trades),
        "my": month_year_heatmap(trades),
    }


logger = logging.getLogger("StrategyTester.gui.quant_tab")
WEEKDAY_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
MONTH_LABELS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


# ----------------------------------------------------------------------------
# Heatmap widget — colour-mapped 2-D grid with text overlay
# ----------------------------------------------------------------------------
class _Heatmap(pg.GraphicsLayoutWidget):
    """Generic colour-mapped heatmap with cell labels."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent=parent)
        self.setBackground(QColor("#0D0D0F"))
        self._view = self.addPlot(row=0, col=0)
        self._view.setMenuEnabled(False)
        self._view.setMouseEnabled(False, False)
        self._view.hideButtons()
        self._image = pg.ImageItem(axisOrder="row-major")
        self._view.addItem(self._image)
        self._labels: List[pg.TextItem] = []

    def set_grid(
        self,
        data: np.ndarray,
        x_labels: List[str],
        y_labels: List[str],
        cmap: str = "RdYlGn",
        show_text: bool = True,
    ) -> None:
        for lbl in self._labels:
            self._view.removeItem(lbl)
        self._labels.clear()
        self._image.clear()
        if data.size == 0:
            return
        finite = data[np.isfinite(data)]
        if finite.size == 0:
            return
        vmax = max(abs(float(finite.max())), abs(float(finite.min())), 1e-9)
        # Symmetric colour scale around 0 → use diverging colormap.
        try:
            lut = pg.colormap.get(cmap).getLookupTable(0.0, 1.0, 256)
        except Exception:
            lut = pg.colormap.get("viridis").getLookupTable(0.0, 1.0, 256)
        self._image.setLookupTable(lut)
        self._image.setLevels((-vmax, vmax))
        self._image.setImage(np.where(np.isfinite(data), data, 0.0), autoLevels=False)
        # Axis ticks
        ax_b = self._view.getAxis("bottom")
        ax_l = self._view.getAxis("left")
        ax_b.setTicks([list(enumerate(x_labels))])
        ax_l.setTicks([list(enumerate(y_labels))])
        # Labels per cell
        if show_text:
            rows, cols = data.shape
            for r in range(rows):
                for c in range(cols):
                    val = data[r, c]
                    if not np.isfinite(val) or val == 0:
                        continue
                    txt = pg.TextItem(f"{val:.0f}", color="#0D0D0F", anchor=(0.5, 0.5))
                    txt.setPos(c, r)
                    txt.setFont(QFont("Consolas", 7))
                    self._view.addItem(txt)
                    self._labels.append(txt)


# ----------------------------------------------------------------------------
# Quant tab
# ----------------------------------------------------------------------------
class QuantTab(QWidget):
    """Phase 5 — advanced trade analytics dashboard."""

    def __init__(self, engine):
        super().__init__()
        self.engine = engine
        self._compare_trades: List = []
        self._compare_initial_equity: float = 0.0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Top bar — visually mirrors gui.analytics_tab.AnalyticsTab so both feel
        # like one cohesive analytics surface.
        self._top_bar = QWidget()
        self._top_bar.setFixedHeight(44)
        tb_l = QHBoxLayout(self._top_bar)
        tb_l.setContentsMargins(16, 0, 16, 0)
        tb_l.setSpacing(8)
        self._title = QLabel("Quant")
        tb_l.addWidget(self._title)
        tb_l.addStretch()
        btn_refresh = QPushButton("Refresh")
        btn_refresh.setFixedWidth(90)
        btn_refresh.clicked.connect(self.refresh_all)
        tb_l.addWidget(btn_refresh)
        layout.addWidget(self._top_bar)

        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)

        # Phase 3 — captions / summaries that need re-tinting on theme switch.
        self._section_captions: list = []
        self._summary_labels: list = []
        self._compare_text_widgets: list = []

        self._build_mae_mfe_tab()
        self._build_r_multiples_tab()
        self._build_heatmap_tab()
        self._build_attribution_tab()
        self._build_slippage_tab()
        self._build_compare_tab()

        # Phase 3 — re-apply local stylesheets every time the theme flips.
        self._apply_theme()
        register_theme_listener(self._apply_theme)

    def _apply_theme(self) -> None:
        t = get_theme()
        self._top_bar.setStyleSheet(sh.top_bar(t))
        self._title.setStyleSheet(sh.label_title(t))
        self.tabs.setStyleSheet(sh.sub_tabs(t))
        for cap in self._section_captions:
            cap.setStyleSheet(sh.section_caption(t))
        for s in self._summary_labels:
            s.setStyleSheet(sh.summary_label(t))
        for c_ in self._compare_text_widgets:
            c_.setStyleSheet(sh.code_text(t))
        # Plot backgrounds re-tint with the theme bg.
        for plot_attr in ("mae_mfe_plot", "mae_hist", "r_hist", "r_cum", "slip_plot"):
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
                            axis.setLabel(**axis.labelStyle)
                except Exception:
                    pass
        # Heatmaps have their own background widget.
        for hm_attr in ("hw_heatmap", "my_heatmap"):
            hm = getattr(self, hm_attr, None)
            if hm is not None:
                try:
                    hm.setBackground(QColor(sh.c(t, "chart_bg")))
                except Exception:
                    pass

    # ---------------------------------------------------------------- MAE/MFE
    def _build_mae_mfe_tab(self) -> None:
        w = QWidget()
        l = QVBoxLayout(w)
        l.setContentsMargins(12, 10, 12, 10)
        l.setSpacing(8)
        self.mae_mfe_plot = pg.PlotWidget()
        self.mae_mfe_plot.setLabel("left", "MFE ($)", size="9pt")
        self.mae_mfe_plot.setLabel("bottom", "MAE ($)", size="9pt")
        l.addWidget(self.mae_mfe_plot, stretch=1)

        self.mae_hist = pg.PlotWidget()
        self.mae_hist.setLabel("left", "Frequency", size="9pt")
        self.mae_hist.setLabel("bottom", "MAE / risk (R)", size="9pt")
        self.mae_hist.setMaximumHeight(180)
        l.addWidget(self.mae_hist)
        self.tabs.addTab(w, "MAE / MFE")

    # --------------------------------------------------------------- R-multiples
    def _build_r_multiples_tab(self) -> None:
        w = QWidget()
        l = QVBoxLayout(w)
        l.setContentsMargins(12, 10, 12, 10)
        l.setSpacing(8)
        self.r_hist = pg.PlotWidget()
        self.r_hist.setLabel("left", "Frequency", size="9pt")
        self.r_hist.setLabel("bottom", "R-multiple", size="9pt")
        l.addWidget(self.r_hist, stretch=1)

        self.r_cum = pg.PlotWidget()
        self.r_cum.setLabel("left", "Cumulative R", size="9pt")
        self.r_cum.setLabel("bottom", "Trade #", size="9pt")
        l.addWidget(self.r_cum, stretch=1)

        self.r_summary = QLabel("R-multiples summary will appear after a run.")
        self._summary_labels.append(self.r_summary)
        l.addWidget(self.r_summary)
        self.tabs.addTab(w, "R-multiples")

    # --------------------------------------------------------------- Heatmaps
    def _build_heatmap_tab(self) -> None:
        w = QWidget()
        l = QVBoxLayout(w)
        l.setContentsMargins(12, 10, 12, 10)
        l.setSpacing(6)
        cap1 = QLabel("Hour × Weekday — Net PnL ($)")
        self._section_captions.append(cap1)
        l.addWidget(cap1)
        self.hw_heatmap = _Heatmap()
        self.hw_heatmap.setMinimumHeight(280)
        l.addWidget(self.hw_heatmap, stretch=1)
        cap2 = QLabel("Year × Month — Net PnL ($)")
        self._section_captions.append(cap2)
        l.addWidget(cap2)
        self.my_heatmap = _Heatmap()
        self.my_heatmap.setMinimumHeight(220)
        l.addWidget(self.my_heatmap, stretch=1)
        self.tabs.addTab(w, "Heatmaps")

    # --------------------------------------------------------------- Attribution
    def _build_attribution_tab(self) -> None:
        w = QWidget()
        l = QVBoxLayout(w)
        l.setContentsMargins(12, 10, 12, 10)
        l.setSpacing(8)

        cap_dd = QLabel("Drawdown Episodes")
        self._section_captions.append(cap_dd)
        l.addWidget(cap_dd)
        self.dd_table = QTableWidget(0, 6)
        self.dd_table.setHorizontalHeaderLabels(
            ["#", "Peak idx", "Trough idx", "Recover idx", "Depth ($)", "Underwater (trades)"]
        )
        self.dd_table.horizontalHeader().setStretchLastSection(True)
        self.dd_table.setAlternatingRowColors(True)
        self.dd_table.verticalHeader().setVisible(False)
        l.addWidget(self.dd_table, stretch=1)

        cap_attr = QLabel("Exit-reason Attribution")
        self._section_captions.append(cap_attr)
        l.addWidget(cap_attr)
        self.attr_table = QTableWidget(0, 5)
        self.attr_table.setHorizontalHeaderLabels(
            ["Reason", "Count", "Sum PnL", "Avg PnL", "Win-rate"]
        )
        self.attr_table.horizontalHeader().setStretchLastSection(True)
        self.attr_table.setAlternatingRowColors(True)
        self.attr_table.verticalHeader().setVisible(False)
        l.addWidget(self.attr_table, stretch=1)
        self.tabs.addTab(w, "Attribution")

    # --------------------------------------------------------------- Slippage
    def _build_slippage_tab(self) -> None:
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

        self.slip_summary = QLabel("Adjust the slip cost — base vs perturbed PnL appears here.")
        self._summary_labels.append(self.slip_summary)
        l.addWidget(self.slip_summary)
        self.tabs.addTab(w, "Slippage")

    # --------------------------------------------------------------- Compare
    def _build_compare_tab(self) -> None:
        w = QWidget()
        l = QVBoxLayout(w)
        l.setContentsMargins(12, 10, 12, 10)
        l.setSpacing(8)
        ctrls = QHBoxLayout()
        btn_load = QPushButton("Load .agbk session …")
        btn_load.clicked.connect(self._load_compare_session)
        ctrls.addWidget(btn_load)
        btn_clear = QPushButton("Clear")
        btn_clear.clicked.connect(self._clear_compare)
        ctrls.addWidget(btn_clear)
        ctrls.addStretch()
        l.addLayout(ctrls)
        self.compare_text = QTextEdit()
        self.compare_text.setReadOnly(True)
        self._compare_text_widgets.append(self.compare_text)
        l.addWidget(self.compare_text, stretch=1)
        self.tabs.addTab(w, "Compare")

    # =====================================================================
    def refresh_all(self) -> None:
        try:
            self._update_mae_mfe()
            self._update_r_multiples()
            self._update_heatmaps()
            self._update_attribution()
            self._update_slippage()
            self._update_compare()
        except Exception:
            logger.exception("QuantTab.refresh_all failed")

    # ------------------------------------------------------- Update routines
    def _update_mae_mfe(self) -> None:
        self.mae_mfe_plot.clear()
        self.mae_hist.clear()
        if not getattr(self.engine, "trades", None):
            return
        arrays = mae_mfe_arrays(self.engine.trades)
        if arrays.mae_dollars.size == 0:
            return
        win_mask = arrays.pnl_dollars > 0
        loss_mask = ~win_mask
        t = get_theme()
        win_col = QColor(sh.c(t, "chart_win_marker"))
        win_col.setAlpha(200)
        loss_col = QColor(sh.c(t, "chart_loss_marker"))
        loss_col.setAlpha(200)

        if win_mask.any():
            self.mae_mfe_plot.plot(
                arrays.mae_dollars[win_mask],
                arrays.mfe_dollars[win_mask],
                pen=None,
                symbol="o",
                symbolSize=6,
                symbolBrush=win_col,
            )
        if loss_mask.any():
            self.mae_mfe_plot.plot(
                arrays.mae_dollars[loss_mask],
                arrays.mfe_dollars[loss_mask],
                pen=None,
                symbol="x",
                symbolSize=6,
                symbolBrush=loss_col,
            )
        finite_r = arrays.mae_r[np.isfinite(arrays.mae_r)]
        if finite_r.size > 0:
            hist_col = QColor(sh.c(t, "cyan"))
            hist_col.setAlpha(150)
            y, x = np.histogram(finite_r, bins=min(40, max(10, int(np.sqrt(finite_r.size)))))
            self.mae_hist.addItem(
                pg.BarGraphItem(
                    x0=x[:-1],
                    x1=x[1:],
                    height=y,
                    brush=hist_col,
                )
            )

    def _update_r_multiples(self) -> None:
        self.r_hist.clear()
        self.r_cum.clear()
        if not getattr(self.engine, "trades", None):
            self.r_summary.setText("No trades.")
            return
        res = r_multiples(self.engine.trades)
        finite = res.r_values[np.isfinite(res.r_values)]
        t = get_theme()
        if finite.size > 0:
            hist_col = QColor(sh.c(t, "amber"))
            hist_col.setAlpha(160)
            y, x = np.histogram(finite, bins=min(40, max(10, int(np.sqrt(finite.size)))))
            self.r_hist.addItem(
                pg.BarGraphItem(
                    x0=x[:-1],
                    x1=x[1:],
                    height=y,
                    brush=hist_col,
                )
            )
            self.r_hist.addItem(
                pg.InfiniteLine(
                    pos=0.0,
                    angle=90,
                    pen=pg.mkPen(sh.c(t, "text3"), width=1, style=Qt.PenStyle.DashLine),
                )
            )
        if res.cumulative_r.size > 0:
            self.r_cum.plot(
                np.arange(1, res.cumulative_r.size + 1),
                res.cumulative_r,
                pen=pg.mkPen(sh.c(t, "accent_light"), width=2),
            )
        self.r_summary.setText(
            f"N={finite.size}    Expectancy={res.expectancy_r:+.3f}R    "
            f"Best={(finite.max() if finite.size else 0):+.2f}R    "
            f"Worst={(finite.min() if finite.size else 0):+.2f}R"
        )

    def _update_heatmaps(self) -> None:
        """Refresh the hour×weekday and year×month heatmaps.

        Aggregation runs in a background :class:`gui.workers.ComputeWorker`
        because hour×weekday/month×year aggregation iterates over every
        trade — fine at 100 trades, visibly janky at 100k. The heatmaps are
        repainted from :pymeth:`_finalize_heatmaps` once the worker emits.
        """
        if not getattr(self.engine, "trades", None):
            return

        prev = getattr(self, "_heatmap_worker", None)
        if prev is not None and prev.isRunning():
            prev.cancel()
            prev.wait(50)

        # Lazily build a spinner that covers the whole heatmap sub-tab.
        if not hasattr(self, "_heatmap_spinner"):
            self._heatmap_spinner = Spinner(
                self.hw_heatmap.parentWidget() or self.hw_heatmap,
                "Computing heatmaps…",
            )
            self._heatmap_spinner.cover(self.hw_heatmap.parentWidget() or self.hw_heatmap)
        self._heatmap_spinner.start()

        worker = ComputeWorker(_compute_heatmaps, list(self.engine.trades), parent=self)
        worker.finished.connect(self._finalize_heatmaps)
        worker.error.connect(self._on_heatmap_error)
        self._heatmap_worker = worker
        worker.start()

    def _finalize_heatmaps(self, payload: dict) -> None:
        """Paint pre-aggregated heatmap grids onto the heatmap widgets."""
        if hasattr(self, "_heatmap_spinner"):
            self._heatmap_spinner.stop()
        hw = payload["hw"]
        my = payload["my"]
        # Show rows = hours (24), cols = weekdays (7).
        self.hw_heatmap.set_grid(
            hw.net_pnl,
            x_labels=WEEKDAY_LABELS,
            y_labels=[f"{h:02d}" for h in range(24)],
        )
        if my.years:
            self.my_heatmap.set_grid(
                my.net_pnl,
                x_labels=MONTH_LABELS,
                y_labels=[str(y) for y in my.years],
            )

    def _on_heatmap_error(self, exc: Exception) -> None:
        if hasattr(self, "_heatmap_spinner"):
            self._heatmap_spinner.stop()
        logger.exception("Heatmap worker failed", exc_info=exc)

    def _update_attribution(self) -> None:
        if not getattr(self.engine, "trades", None):
            self.dd_table.setRowCount(0)
            self.attr_table.setRowCount(0)
            return
        trades = self.engine.trades
        eq = np.empty(len(trades) + 1, dtype=float)
        eq[0] = self.engine.initial_equity
        eq[1:] = self.engine.initial_equity + np.cumsum(
            np.asarray([t.pnl for t in trades], dtype=float)
        )
        episodes = drawdown_durations(eq)
        self.dd_table.setRowCount(len(episodes))
        for r, e in enumerate(episodes):
            cells = [
                str(r + 1),
                str(e.start_idx),
                str(e.trough_idx),
                "—" if e.recover_idx is None else str(e.recover_idx),
                f"${e.depth_dollars:,.2f}",
                str(e.underwater_trades),
            ]
            for c, txt in enumerate(cells):
                self.dd_table.setItem(r, c, QTableWidgetItem(txt))

        rows = exit_reason_attribution(trades)
        self.attr_table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            cells = [
                row.reason,
                str(row.count),
                f"${row.sum_pnl:,.2f}",
                f"${row.avg_pnl:,.2f}",
                f"{row.win_rate * 100:.1f}%",
            ]
            for c, txt in enumerate(cells):
                item = QTableWidgetItem(txt)
                if c == 2:
                    item.setForeground(QColor("#26A69A") if row.sum_pnl >= 0 else QColor("#EF5350"))
                self.attr_table.setItem(r, c, item)

    def _update_slippage(self) -> None:
        self.slip_plot.clear()
        if not getattr(self.engine, "trades", None):
            self.slip_summary.setText("No trades.")
            return
        cost = float(self.slip_spin.value())
        res = simulate_slippage(self.engine.trades, slip_per_trade_dollars=cost)
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
            f"Δ: ${res.pnl_delta:,.2f}    "
            f"({res.avg_slip_per_trade:.2f}$ × {res.base_pnls.size} trades)"
        )

    # --------------------------------------------------------------- compare
    def _load_compare_session(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Load .agbk session", "", "AGREGAT backtest (*.agbk)"
        )
        if not path:
            return
        try:
            data = load_session_raw(path)
            self._compare_trades = list(data.get("trades", []))
            meta = data.get("meta", {})
            self._compare_initial_equity = float(meta.get("initial_equity", 0.0))
        except Exception as e:
            self.compare_text.setText(f"Failed to load session: {e!r}")
            return
        self._update_compare()

    def _clear_compare(self) -> None:
        self._compare_trades = []
        self._compare_initial_equity = 0.0
        self.compare_text.setText("Cleared.")

    def _update_compare(self) -> None:
        cur_trades = list(getattr(self.engine, "trades", []) or [])
        if not cur_trades and not self._compare_trades:
            self.compare_text.setText("No data — load a saved session and run a backtest.")
            return
        a = summarise_run(
            self._compare_trades,
            name="Saved",
            initial_equity=self._compare_initial_equity or float(self.engine.initial_equity),
        )
        b = summarise_run(
            cur_trades, name="Current", initial_equity=float(self.engine.initial_equity)
        )
        diff = compare_runs(a, b)

        def _fmt(metric: str, fmt: str) -> str:
            d = diff[metric]
            sign = "+" if d["delta"] >= 0 else ""
            return (
                f"  {metric:<16}  A={fmt.format(d['a'])}    "
                f"B={fmt.format(d['b'])}    "
                f"Δ={sign}{fmt.format(d['delta'])}"
            )

        text = (
            f"A = {a.name} ({a.n_trades} trades) | B = {b.name} ({b.n_trades} trades)\n"
            f"{'─' * 60}\n"
            f"{_fmt('total_pnl', '${:,.2f}')}\n"
            f"{_fmt('win_rate', '{:.2%}')}\n"
            f"{_fmt('profit_factor', '{:.2f}')}\n"
            f"{_fmt('expectancy', '${:.2f}')}\n"
            f"{_fmt('max_dd_pct', '{:.2f}%')}\n"
        )
        self.compare_text.setText(text)


__all__ = ["QuantTab"]
