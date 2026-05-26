"""
Phase 5 — quant analytics tab.

Sub-tabs:
- MAE/MFE scatter + distribution
- R-multiples histogram + cumulative R curve
- Hour×weekday + month×year heatmaps
- Drawdown episodes table + exit-reason attribution
- Slippage simulator (interactive what-if)
<<<<<<< HEAD
- Strategy comparison (load .agbk vs current run)
=======
>>>>>>> origin/main
"""

from __future__ import annotations

import logging
from typing import List, Optional

import numpy as np
import pyqtgraph as pg
<<<<<<< HEAD
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QFileDialog,
=======
from PySide6.QtCore import QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QDoubleSpinBox,
>>>>>>> origin/main
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
<<<<<<< HEAD
    QTableWidget,
    QTableWidgetItem,
=======
    QTableView,
>>>>>>> origin/main
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

<<<<<<< HEAD
from engine.persistence.persistence import load_session_raw
from engine.analytics.trade_analytics import (
    compare_runs,
=======
from engine.analytics.trade_analytics import (
>>>>>>> origin/main
    drawdown_durations,
    exit_reason_attribution,
    hour_weekday_heatmap,
    mae_mfe_arrays,
    month_year_heatmap,
    r_multiples,
    simulate_slippage,
<<<<<<< HEAD
    summarise_run,
)
from gui.styling import style_helpers as sh
from gui.styling.theme import get_theme, register_theme_listener
from gui.workers.workers import ComputeWorker, Spinner
=======
)
from gui.styling import style_helpers as sh
from gui.styling.theme import font_mono, get_theme, register_theme_listener
from gui.workers.workers import ComputeWorker, Spinner
from gui.widgets.simple_table_model import SimpleTableModel
>>>>>>> origin/main


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
<<<<<<< HEAD
    """Generic colour-mapped heatmap with cell labels."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent=parent)
        self.setBackground(QColor("#0D0D0F"))
=======
    """Premium heatmap with modern glass-morphism styling, smooth gradients, and subtle animations."""

    # Signal emitted when a cell is clicked: (row, col)
    cell_clicked = Signal(int, int)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent=parent)
        self._rows = 0
        self._cols = 0
        self._data: Optional[np.ndarray] = None
        self._x_labels: List[str] = []
        self._y_labels: List[str] = []
        self._lut = None
        self._vmax = 1.0
        self._selected: Optional[tuple[int, int]] = None

>>>>>>> origin/main
        self._view = self.addPlot(row=0, col=0)
        self._view.setMenuEnabled(False)
        self._view.setMouseEnabled(False, False)
        self._view.hideButtons()
<<<<<<< HEAD
        self._image = pg.ImageItem(axisOrder="row-major")
        self._view.addItem(self._image)
        self._labels: List[pg.TextItem] = []

=======
        self._view.invertY(True)
        self._image = pg.ImageItem(axisOrder="row-major")
        self._image.setOpts(axisOrder="row-major")
        self._view.addItem(self._image)
        self._labels: List[pg.TextItem] = []

        self._cb_view = self.addPlot(row=0, col=1)
        self._cb_view.setMenuEnabled(False)
        self._cb_view.setMouseEnabled(False, False)
        self._cb_view.hideButtons()
        self._cb_view.setMaximumWidth(64)
        self._cb_view.setMinimumWidth(64)
        for ax in ("left", "bottom", "top"):
            self._cb_view.hideAxis(ax)
        self._cb_view.showAxis("right")
        self._cb_axis = self._cb_view.getAxis("right")
        self._cb_image = pg.ImageItem(axisOrder="row-major")
        self._cb_view.addItem(self._cb_image)

        # Connect mouse events on image item
        self._image.mouseClickEvent = self._on_image_click
        self._image.hoverEvent = self._on_image_hover
        self._highlight = pg.RectROI(pos=(0, 0), size=(1, 1), pen=pg.mkPen(width=2), movable=False)
        self._highlight.setZValue(10)
        self._view.addItem(self._highlight)
        self._highlight.hide()
        self._tooltip = pg.TextItem(text="", anchor=(0.5, 0.5))
        self._tooltip.setZValue(20)
        self._view.addItem(self._tooltip)
        self._tooltip.hide()
        self._apply_theme()
        register_theme_listener(self._apply_theme)

    def _apply_theme(self) -> None:
        t = get_theme()
        bg = QColor(sh.c(t, "chart_bg"))
        self.setBackground(bg)
        # PlotItem does not have setBackground; set it on the ViewBox instead
        if hasattr(self._view, 'setBackground'):
            self._view.setBackground(bg)
        elif hasattr(self._view, 'getViewBox'):
            vb = self._view.getViewBox()
            if vb is not None:
                vb.setBackgroundColor(bg)
        if hasattr(self._cb_view, 'setBackground'):
            self._cb_view.setBackground(bg)
        elif hasattr(self._cb_view, 'getViewBox'):
            cb_vb = self._cb_view.getViewBox()
            if cb_vb is not None:
                cb_vb.setBackgroundColor(bg)

        ax_b = self._view.getAxis("bottom")
        ax_l = self._view.getAxis("left")
        for axis in (ax_b, ax_l, self._cb_axis):
            if axis is None:
                continue
            axis.setStyle(tickTextOffset=6, tickLength=0)
            axis.setPen(pg.mkPen(QColor(sh.c(t, "chart_axis_text")), width=1))
            axis.setTextPen(QColor(sh.c(t, "chart_axis_text")))

        pen = pg.mkPen(QColor(sh.c(t, "accent_light")), width=2)
        self._highlight.setPen(pen)

        # TextItem does not support setFill/setBorder; use a simple color instead
        text_color = QColor(sh.c(t, "text"))
        self._tooltip.setColor(text_color)
        # For background, we can use HTML formatting in setText
        # Or create a custom QGraphicsItem, but for now just set text color

    def clear_selection(self) -> None:
        self._selected = None
        self._highlight.hide()
        self._tooltip.hide()

    @staticmethod
    def _fmt_money(v: float) -> str:
        av = abs(float(v))
        if av >= 1_000_000:
            return f"${v/1_000_000:+.1f}M"
        if av >= 10_000:
            return f"${v/1_000:+.0f}k"
        if av >= 1_000:
            return f"${v:+,.0f}"
        return f"${v:+.0f}"

    def _map_pos_to_cell(self, pos) -> tuple[int, int]:
        col = int(np.floor(float(pos.x()) + 0.5))
        row = int(np.floor(float(pos.y()) + 0.5))
        return row, col

    def _on_image_click(self, ev) -> None:
        """Handle mouse clicks on the heatmap image."""
        if ev.button() != 1:  # Left click only
            return
        row, col = self._map_pos_to_cell(ev.pos())
        if 0 <= row < self._rows and 0 <= col < self._cols:
            self._selected = (row, col)
            self._highlight.setPos(col - 0.5, row - 0.5)
            self._highlight.setSize((1, 1))
            self._highlight.show()
            self.cell_clicked.emit(row, col)

    def _on_image_hover(self, ev) -> None:
        """Handle mouse hover over heatmap cells — show premium tooltip."""
        if ev.isExit():
            self._tooltip.hide()
            if self._selected is None:
                self._highlight.hide()
            else:
                r, c = self._selected
                self._highlight.setPos(c - 0.5, r - 0.5)
                self._highlight.setSize((1, 1))
                self._highlight.show()
            return
        row, col = self._map_pos_to_cell(ev.pos())
        if 0 <= row < self._rows and 0 <= col < self._cols and self._data is not None:
            val = self._data[row, col]
            x_label = self._x_labels[col] if col < len(self._x_labels) else str(col)
            y_label = self._y_labels[row] if row < len(self._y_labels) else str(row)
            self._highlight.setPos(col - 0.5, row - 0.5)
            self._highlight.setSize((1, 1))
            self._highlight.show()
            if np.isfinite(val):
                t = get_theme()
                color = sh.c(t, "green_bright") if val >= 0 else sh.c(t, "red_bright")
                self._tooltip.setText(
                    f"{y_label} × {x_label}<br>"
                    f"<span style='color:{color};font-weight:bold;font-size:14px;'>"
                    f"{self._fmt_money(float(val))}"
                    f"</span>"
                )
            else:
                self._tooltip.setText(
                    f"{y_label} × {x_label}<br><span style='color:{sh.c(get_theme(),'text3')};'>No data</span>"
                )
            tip_y = row - 1.2
            if tip_y < -0.2:
                tip_y = row + 1.2
            self._tooltip.setPos(col, tip_y)
            self._tooltip.show()

>>>>>>> origin/main
    def set_grid(
        self,
        data: np.ndarray,
        x_labels: List[str],
        y_labels: List[str],
<<<<<<< HEAD
        cmap: str = "RdYlGn",
=======
        cmap: str = "turbo",
>>>>>>> origin/main
        show_text: bool = True,
    ) -> None:
        for lbl in self._labels:
            self._view.removeItem(lbl)
        self._labels.clear()
        self._image.clear()
<<<<<<< HEAD
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
=======
        self._data = None
        if data.size == 0:
            self._rows = 0
            self._cols = 0
            return
        finite = data[np.isfinite(data)]
        if finite.size == 0:
            self._rows = 0
            self._cols = 0
            return
        # Store grid data for hover/click
        self._data = data.copy()
        self._x_labels = x_labels
        self._y_labels = y_labels
        # Store grid dimensions for click mapping
        self._rows, self._cols = data.shape
        vmax = max(abs(float(finite.max())), abs(float(finite.min())), 1e-9)
        self._vmax = float(vmax)
        try:
            if cmap == "theme_div":
                t = get_theme()
                c0 = QColor(sh.c(t, "red_bright"))
                c1 = QColor(sh.c(t, "surface"))
                c2 = QColor(sh.c(t, "green_bright"))
                cmap_obj = pg.ColorMap(
                    pos=np.array([0.0, 0.5, 1.0], dtype=float),
                    color=np.array([c0.getRgb()[:3], c1.getRgb()[:3], c2.getRgb()[:3]], dtype=np.ubyte),
                )
            else:
                cmap_obj = pg.colormap.get(cmap, pg.colormap.get("Turbo"))
            lut = cmap_obj.getLookupTable(0.0, 1.0, 256)
        except Exception:
            lut = pg.colormap.get("viridis").getLookupTable(0.0, 1.0, 256)
        self._lut = lut
        self._image.setLookupTable(lut)
        self._image.setLevels((-vmax, vmax))
        self._image.setImage(np.where(np.isfinite(data), data, 0.0), autoLevels=False)
        self._image.setRect(QRectF(-0.5, -0.5, float(self._cols), float(self._rows)))

        grad = np.linspace(-vmax, vmax, 256, dtype=float).reshape(256, 1)
        self._cb_image.setLookupTable(lut)
        self._cb_image.setLevels((-vmax, vmax))
        self._cb_image.setImage(grad, autoLevels=False)
        self._cb_image.setRect(QRectF(0.0, -vmax, 1.0, 2.0 * vmax))
        self._cb_view.setXRange(0.0, 1.0, padding=0.0)
        self._cb_view.setYRange(-vmax, vmax, padding=0.0)
        self._cb_axis.setTicks(
            [[(-vmax, self._fmt_money(-vmax)), (0.0, "0"), (vmax, self._fmt_money(vmax))]]
        )

>>>>>>> origin/main
        ax_b = self._view.getAxis("bottom")
        ax_l = self._view.getAxis("left")
        ax_b.setTicks([list(enumerate(x_labels))])
        ax_l.setTicks([list(enumerate(y_labels))])
<<<<<<< HEAD
        # Labels per cell
=======
        t = get_theme()
        for axis in [ax_b, ax_l]:
            axis.setStyle(tickTextOffset=6, tickLength=0)
            axis.setPen(pg.mkPen(QColor(sh.c(t, "chart_axis_text")), width=1))
            axis.setTextPen(QColor(sh.c(t, "chart_axis_text")))

        self._view.setXRange(-0.5, float(self._cols) - 0.5, padding=0.02)
        self._view.setYRange(-0.5, float(self._rows) - 0.5, padding=0.02)

        if self._selected is not None:
            r, c = self._selected
            if 0 <= r < self._rows and 0 <= c < self._cols:
                self._highlight.setPos(c - 0.5, r - 0.5)
                self._highlight.setSize((1, 1))
                self._highlight.show()
            else:
                self._selected = None
                self._highlight.hide()

>>>>>>> origin/main
        if show_text:
            rows, cols = data.shape
            for r in range(rows):
                for c in range(cols):
                    val = data[r, c]
                    if not np.isfinite(val) or val == 0:
                        continue
<<<<<<< HEAD
                    txt = pg.TextItem(f"{val:.0f}", color="#0D0D0F", anchor=(0.5, 0.5))
                    txt.setPos(c, r)
                    txt.setFont(QFont("Consolas", 7))
=======
                    # Contrast-aware text color
                    norm_val = (val + vmax) / (2 * vmax) if vmax > 0 else 0.5
                    text_color = "#0A0A0C" if norm_val > 0.5 else "#FFFFFF"
                    txt = pg.TextItem(
                        f"{self._fmt_money(float(val)).replace('$','')}", color=text_color, anchor=(0.5, 0.5), fill=None
                    )
                    txt.setPos(c, r)
                    txt.setFont(QFont(font_mono(), 8, QFont.Weight.Bold))
>>>>>>> origin/main
                    self._view.addItem(txt)
                    self._labels.append(txt)


# ----------------------------------------------------------------------------
# Quant tab
# ----------------------------------------------------------------------------
class QuantTab(QWidget):
    """Phase 5 — advanced trade analytics dashboard."""

<<<<<<< HEAD
    def __init__(self, engine):
        super().__init__()
        self.engine = engine
        self._compare_trades: List = []
        self._compare_initial_equity: float = 0.0
=======
    # Emitted when user clicks heatmap cell and wants to drill down to Trades.
    drill_down_requested = Signal(dict)

    def __init__(self, engine):
        super().__init__()
        self.engine = engine
        self._active_drilldown: dict = {}
>>>>>>> origin/main

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
<<<<<<< HEAD
        self._compare_text_widgets: list = []
=======
>>>>>>> origin/main

        self._build_mae_mfe_tab()
        self._build_r_multiples_tab()
        self._build_heatmap_tab()
        self._build_attribution_tab()
        self._build_slippage_tab()
<<<<<<< HEAD
        self._build_compare_tab()
=======

        # Debounce slider value-changed events: slippage simulation is O(N)
        # over the trade list, so we coalesce rapid changes into one recompute.
        self._slip_debounce = QTimer(self)
        self._slip_debounce.setSingleShot(True)
        self._slip_debounce.setInterval(120)
        self._slip_debounce.timeout.connect(self._update_slippage)
>>>>>>> origin/main

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
<<<<<<< HEAD
        for c_ in self._compare_text_widgets:
            c_.setStyleSheet(sh.code_text(t))
=======
        # Tables (Model/View)
        for tbl in (getattr(self, "dd_table", None), getattr(self, "attr_table", None)):
            if tbl is not None:
                try:
                    tbl.setStyleSheet(sh.analytics_table(t))
                except Exception:
                    pass
>>>>>>> origin/main
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
<<<<<<< HEAD
        l.setSpacing(6)
=======
        l.setSpacing(8)
        # Drill-down status row
        top = QHBoxLayout()
        cap0 = QLabel("Heatmaps — click cells to drill down into Trades")
        self._section_captions.append(cap0)
        top.addWidget(cap0)
        top.addStretch()
        self._drill_lbl = QLabel("")
        self._summary_labels.append(self._drill_lbl)
        self._drill_lbl.hide()
        top.addWidget(self._drill_lbl)
        self._btn_clear_drill = QPushButton("Clear drill-down")
        self._btn_clear_drill.setFixedHeight(28)
        self._btn_clear_drill.clicked.connect(self._clear_drilldown)
        self._btn_clear_drill.hide()
        top.addWidget(self._btn_clear_drill)
        l.addLayout(top)

>>>>>>> origin/main
        cap1 = QLabel("Hour × Weekday — Net PnL ($)")
        self._section_captions.append(cap1)
        l.addWidget(cap1)
        self.hw_heatmap = _Heatmap()
        self.hw_heatmap.setMinimumHeight(280)
<<<<<<< HEAD
=======
        # Phase 6.3 — connect heatmap cell clicks to drill-down navigation
        self.hw_heatmap.cell_clicked.connect(self._on_hw_heatmap_click)
>>>>>>> origin/main
        l.addWidget(self.hw_heatmap, stretch=1)
        cap2 = QLabel("Year × Month — Net PnL ($)")
        self._section_captions.append(cap2)
        l.addWidget(cap2)
        self.my_heatmap = _Heatmap()
        self.my_heatmap.setMinimumHeight(220)
<<<<<<< HEAD
        l.addWidget(self.my_heatmap, stretch=1)
        self.tabs.addTab(w, "Heatmaps")

=======
        # Phase 6.3 — connect heatmap cell clicks to drill-down navigation
        self.my_heatmap.cell_clicked.connect(self._on_my_heatmap_click)
        l.addWidget(self.my_heatmap, stretch=1)
        self.tabs.addTab(w, "Heatmaps")

    def _set_active_drilldown(self, payload: dict) -> None:
        self._active_drilldown = dict(payload or {})
        # Update UI indicator
        if not hasattr(self, "_drill_lbl"):
            return
        if not self._active_drilldown:
            self._drill_lbl.hide()
            if hasattr(self, "_btn_clear_drill"):
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
        if hasattr(self, "_btn_clear_drill"):
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
        # Send empty payload => clear on the Trades side.
        self.drill_down_requested.emit({})

    # ---------------------------------------------------------- Heatmap clicks
    def _on_hw_heatmap_click(self, row: int, col: int) -> None:
        """Handle hour×weekday heatmap cell click — drill down to Trades tab.

        row = hour (0-23), col = weekday (0=Mon..6=Sun).
        """
        payload = {"hour": int(row), "weekday": int(col)}
        self._set_active_drilldown(payload)
        self.drill_down_requested.emit(payload)

    def _on_my_heatmap_click(self, row: int, col: int) -> None:
        """Handle year×month heatmap cell click — drill down to Trades tab.

        row = year index (into the years list), col = month (0=Jan..11=Dec).
        """
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

>>>>>>> origin/main
    # --------------------------------------------------------------- Attribution
    def _build_attribution_tab(self) -> None:
        w = QWidget()
        l = QVBoxLayout(w)
        l.setContentsMargins(12, 10, 12, 10)
        l.setSpacing(8)

        cap_dd = QLabel("Drawdown Episodes")
        self._section_captions.append(cap_dd)
        l.addWidget(cap_dd)
<<<<<<< HEAD
        self.dd_table = QTableWidget(0, 6)
        self.dd_table.setHorizontalHeaderLabels(
            ["#", "Peak idx", "Trough idx", "Recover idx", "Depth ($)", "Underwater (trades)"]
        )
        self.dd_table.horizontalHeader().setStretchLastSection(True)
        self.dd_table.setAlternatingRowColors(True)
        self.dd_table.verticalHeader().setVisible(False)
=======
        self.dd_table = QTableView()
        self.dd_table.horizontalHeader().setStretchLastSection(True)
        self.dd_table.setAlternatingRowColors(True)
        self._dd_model = SimpleTableModel(
            ["#", "Peak idx", "Trough idx", "Recover idx", "Depth ($)", "Underwater (trades)"],
            [],
            parent=self,
        )
        self.dd_table.setModel(self._dd_model)
>>>>>>> origin/main
        l.addWidget(self.dd_table, stretch=1)

        cap_attr = QLabel("Exit-reason Attribution")
        self._section_captions.append(cap_attr)
        l.addWidget(cap_attr)
<<<<<<< HEAD
        self.attr_table = QTableWidget(0, 5)
        self.attr_table.setHorizontalHeaderLabels(
            ["Reason", "Count", "Sum PnL", "Avg PnL", "Win-rate"]
        )
        self.attr_table.horizontalHeader().setStretchLastSection(True)
        self.attr_table.setAlternatingRowColors(True)
        self.attr_table.verticalHeader().setVisible(False)
=======
        def _attr_style(r: int, c: int, v: str) -> dict:
            # Sum PnL column color
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
>>>>>>> origin/main
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
<<<<<<< HEAD
        self.slip_spin.valueChanged.connect(self._update_slippage)
=======
        self.slip_spin.valueChanged.connect(self._schedule_slippage_update)
>>>>>>> origin/main
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

<<<<<<< HEAD
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

=======
>>>>>>> origin/main
    # =====================================================================
    def refresh_all(self) -> None:
        try:
            self._update_mae_mfe()
            self._update_r_multiples()
            self._update_heatmaps()
            self._update_attribution()
            self._update_slippage()
<<<<<<< HEAD
            self._update_compare()
=======
>>>>>>> origin/main
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
        if prev is not None:
            prev.cancel()

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
<<<<<<< HEAD
        )
        if my.years:
=======
            cmap="theme_div",
        )
        if my.years:
            # Store years for drill-down click handling
            self._last_my_years = list(my.years)
>>>>>>> origin/main
            self.my_heatmap.set_grid(
                my.net_pnl,
                x_labels=MONTH_LABELS,
                y_labels=[str(y) for y in my.years],
<<<<<<< HEAD
            )
=======
                cmap="theme_div",
            )
        else:
            self._last_my_years = []
>>>>>>> origin/main

    def _on_heatmap_error(self, exc: Exception) -> None:
        if hasattr(self, "_heatmap_spinner"):
            self._heatmap_spinner.stop()
        logger.exception("Heatmap worker failed", exc_info=exc)

    def _update_attribution(self) -> None:
        if not getattr(self.engine, "trades", None):
<<<<<<< HEAD
            self.dd_table.setRowCount(0)
            self.attr_table.setRowCount(0)
=======
            self._dd_model.set_rows([])
            self._attr_model.set_rows([])
>>>>>>> origin/main
            return
        trades = self.engine.trades
        eq = np.empty(len(trades) + 1, dtype=float)
        eq[0] = self.engine.initial_equity
        eq[1:] = self.engine.initial_equity + np.cumsum(
            np.asarray([t.pnl for t in trades], dtype=float)
        )
        episodes = drawdown_durations(eq)
<<<<<<< HEAD
        self.dd_table.setRowCount(len(episodes))
        for r, e in enumerate(episodes):
            cells = [
=======
        dd_rows = []
        for r, e in enumerate(episodes):
            dd_rows.append([
>>>>>>> origin/main
                str(r + 1),
                str(e.start_idx),
                str(e.trough_idx),
                "—" if e.recover_idx is None else str(e.recover_idx),
                f"${e.depth_dollars:,.2f}",
                str(e.underwater_trades),
<<<<<<< HEAD
            ]
            for c, txt in enumerate(cells):
                self.dd_table.setItem(r, c, QTableWidgetItem(txt))

        rows = exit_reason_attribution(trades)
        self.attr_table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            cells = [
=======
            ])
        self._dd_model.set_rows(dd_rows)

        rows = exit_reason_attribution(trades)
        attr_rows = []
        for row in rows:
            attr_rows.append([
>>>>>>> origin/main
                row.reason,
                str(row.count),
                f"${row.sum_pnl:,.2f}",
                f"${row.avg_pnl:,.2f}",
                f"{row.win_rate * 100:.1f}%",
<<<<<<< HEAD
            ]
            for c, txt in enumerate(cells):
                item = QTableWidgetItem(txt)
                if c == 2:
                    item.setForeground(QColor("#26A69A") if row.sum_pnl >= 0 else QColor("#EF5350"))
                self.attr_table.setItem(r, c, item)
=======
            ])
        self._attr_model.set_rows(attr_rows)

    def _schedule_slippage_update(self, *_args) -> None:
        """Debounce rapid slider movements before kicking off the O(N)
        slippage simulation. Coalesces ~30 events/sec into ~8/sec."""
        try:
            self._slip_debounce.start()
        except Exception:
            self._update_slippage()
>>>>>>> origin/main

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

<<<<<<< HEAD
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


=======
>>>>>>> origin/main
__all__ = ["QuantTab"]
