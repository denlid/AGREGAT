"""Chart Tab — candlestick chart, drawing menu, time axis, trade arrows."""

import logging
import time

import numpy as np
import pandas as pd
import pyqtgraph as pg
from PySide6.QtCore import QEasingCurve, QEvent, QPointF, QVariantAnimation, Qt, QTimer, Signal, QRectF
from PySide6.QtGui import QColor, QPainterPath, QCursor
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QGraphicsPathItem,
    QGraphicsRectItem,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMenu,
    QPushButton,
    QToolButton,
    QWidget,
)

from gui.persistence import drawings_persist
from gui.styling import style_helpers as sh
from gui.chart_tab.axis import TimeAxisItem
from gui.chart_tab.gl_candle import make_candlestick_item
from gui.chart_tab.gpu_config import STATE as GPU_STATE, should_use_gl_candles
from gui.chart_tab.items import CandlestickItem, FilledRectROI  # noqa: F401 — re-export for callers
from gui.styling.theme import CHART_SCHEMES, get_theme, register_theme_listener

logger = logging.getLogger("StrategyTester.gui.chart_tab")


class ChartTab(QWidget):
    sigTimeRangeChanged = Signal(object, object)  # emits (t_start, t_end) for cross-chart sync
    sigAreaSelected = Signal(dict)

    def __init__(self, engine):
        super().__init__()
        self.engine = engine
        self._is_syncing = False  # prevent recursive sync loops
        self._chart_alive = True  # guard for timer callbacks after destruction
        self.destroyed.connect(lambda: setattr(self, "_chart_alive", False))
        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # ====== Detached controls (embedded in main_window toolbar) ======
        self.draw_btn = QToolButton()
        self.draw_btn.setText("✏️")
        self.draw_btn.setToolTip("Drawing Tools")
        self.draw_btn.setFixedSize(28, 28)
        self.draw_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        draw_menu = QMenu(self.draw_btn)
        draw_menu.addAction("➖ Line", lambda: self._set_tool("Line"))
        draw_menu.addAction("━ HLine", lambda: self._set_tool("HLine"))
        draw_menu.addAction("🟩 Box", lambda: self._set_tool("Rect"))
        draw_menu.addAction("📌 Marker", lambda: self._set_tool("Marker"))
        draw_menu.addAction("🖌️ Brush", lambda: self._set_tool("Brush"))
        draw_menu.addAction("🔤 Text", lambda: self._set_tool("Text"))
        draw_menu.addSeparator()
        # Line width sub-menu
        wm = draw_menu.addMenu("📏 Width")
        for w in [1, 2, 3, 4, 5]:
            wm.addAction(f"{w}px", lambda _w=w: setattr(self, "_draw_width", _w))
        draw_menu.addAction("🎨 Pick Color...", self._pick_draw_color)
        draw_menu.addSeparator()
        draw_menu.addAction("🗑️ Clear All Drawings", self.clear_user_drawings)
        draw_menu.addAction("🧹 Clear Strategy Drawings", self._clear_strategy_drawings)
        self.draw_btn.setMenu(draw_menu)

        self._draw_color = QColor("#FFFFFF")
        self._draw_width = 2

        self.cancel_btn = QPushButton("❌")
        self.cancel_btn.setFixedSize(28, 28)
        self.cancel_btn.setToolTip("Cancel drawing (Esc)")
        self.cancel_btn.clicked.connect(self._cancel_active_mode)
        self.cancel_btn.hide()

        self.fit_btn = QPushButton("🎯")
        self.fit_btn.setFixedSize(28, 28)
        self.fit_btn.setToolTip("Zoom to Fit (Home)")
        self.fit_btn.clicked.connect(self._fit_all)

        # ====== Detached controls (embedded in main_window toolbar) ======
        self.chart_mode_cb = QComboBox()
        self.chart_mode_cb.addItems(["📈 Line", "🕯️ Candles"])
        self.chart_mode_cb.setFixedWidth(85)
        self.chart_mode_cb.currentTextChanged.connect(self.on_mode_changed)

        self.visual_tf_cb = QComboBox()
        self.visual_tf_cb.addItems(["1m", "2m", "3m", "5m", "15m", "30m", "1h", "4h"])
        self.visual_tf_cb.setFixedWidth(65)
        self.visual_tf_cb.currentTextChanged.connect(self.on_tf_changed)

        self.show_markers_cb = QCheckBox("📍 Trades")
        self.show_markers_cb.setChecked(True)
        self.show_markers_cb.stateChanged.connect(self.on_markers_toggled)

        self.show_connections_cb = QCheckBox("🔗 Lines")
        self.show_connections_cb.setChecked(True)
        self.show_connections_cb.stateChanged.connect(lambda _: self.redraw_chart())

        # Layers
        self.layer_cbs = {}
        self.layers_btn = QToolButton()
        self.layers_btn.setText("🗐 Layers")
        self.layers_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._layers_menu = QMenu(self.layers_btn)
        # Default layer — always present
        self._add_layer("entry", "Entry", True)
        self.layers_btn.setMenu(self._layers_menu)
        # Connect dynamic layer registration from engine/strategy
        self.engine.layer_registered.connect(self._add_layer)

        # Drawing mode label
        self.tool_label = QLabel("")
        self.tool_label.setStyleSheet(
            f"color: {sh.c(get_theme(), 'accent_light')};"
            " font-weight: bold; background: transparent;"
        )
        register_theme_listener(self._apply_theme)

        # ====== Plot ======
        # Price axis on the RIGHT, time axis on the BOTTOM.
        self.time_axis = TimeAxisItem(orientation="bottom")
        self.plot_widget = pg.PlotWidget(axisItems={"bottom": self.time_axis})
        self.plot_widget.setBackground(QColor("#0D0D10"))
        self.plot_widget.showGrid(x=True, y=True, alpha=0.06)
        self.plot_widget.setMenuEnabled(False)
        self.plot_widget.hideButtons()
        # Move price axis to the right side; hide the left axis entirely
        self.plot_widget.showAxis("right")
        self.plot_widget.hideAxis("left")
        left_axis = self.plot_widget.getAxis("right")  # variable name kept for compat
        left_axis.setPen(pg.mkPen("#1E1E2A"))
        left_axis.setTextPen(pg.mkPen("#6A6A7A"))
        left_axis.setWidth(62)
        left_axis.setStyle(tickLength=-4)

        # Custom mouse-drag handler to scale price axis.
        # pyqtgraph binds LMB to zoom (scale) and RMB to pan on axes by default,
        # but the PlotWidget ViewBox uses the opposite convention (LMB=pan, RMB=zoom).
        # We swap the buttons here so the price axis zoom behavior matches the
        # main plot area: RMB drag = zoom Y, LMB double-click = auto-range Y.
        def price_mouseDragEvent(ev):
            if hasattr(ev, "button"):
                btn = ev.button()
                if btn == Qt.MouseButton.LeftButton:
                    ev.button = lambda: Qt.MouseButton.RightButton
                elif btn == Qt.MouseButton.RightButton:
                    ev.button = lambda: Qt.MouseButton.LeftButton
            pg.AxisItem.mouseDragEvent(left_axis, ev)

        def price_mouseDoubleClickEvent(ev):
            if ev.button() == Qt.MouseButton.LeftButton and left_axis.linkedView():
                left_axis.linkedView().enableAutoRange(y=True)
                ev.accept()
            else:
                pg.AxisItem.mouseDoubleClickEvent(left_axis, ev)

        left_axis.mouseDragEvent = price_mouseDragEvent
        left_axis.mouseDoubleClickEvent = price_mouseDoubleClickEvent

        self.time_axis.setPen(pg.mkPen("#1E1E2A"))
        self.time_axis.setTextPen(pg.mkPen("#6A6A7A"))

        # Axis fonts
        from PySide6.QtGui import QFont

        axis_font = QFont("Consolas", 9)
        self.time_axis.setTickFont(axis_font)
        left_axis.setTickFont(axis_font)

        # Enable smooth pan and zoom
        vb = self.plot_widget.getViewBox()
        vb.setMouseMode(pg.ViewBox.PanMode)
        self.plot_widget.setMouseEnabled(x=True, y=True)

        # Smooth pan with mouse drag
        vb.setLimits(xMin=None, xMax=None, yMin=None, yMax=None)

        # Connect range change for dual-chart time sync
        vb.sigRangeChanged.connect(self._on_my_range_changed)

        # Override wheel event for smooth independent axis zoom
        self._orig_wheelEvent = vb.wheelEvent
        vb.wheelEvent = self._smooth_wheel_zoom

        # Override plot_widget mouse events for precise drawing (Brush, etc)
        self._orig_plot_mousePressEvent = self.plot_widget.mousePressEvent
        self._orig_plot_mouseMoveEvent = self.plot_widget.mouseMoveEvent
        self._orig_plot_mouseReleaseEvent = self.plot_widget.mouseReleaseEvent
        self._orig_plot_mouseDoubleClickEvent = self.plot_widget.mouseDoubleClickEvent
        self.plot_widget.mousePressEvent = self._plot_mousePressEvent
        self.plot_widget.mouseMoveEvent = self._plot_mouseMoveEvent
        self.plot_widget.mouseReleaseEvent = self._plot_mouseReleaseEvent
        self.plot_widget.mouseDoubleClickEvent = self._plot_mouseDoubleClickEvent

        # Items
        self.price_curve = self.plot_widget.plot(pen=pg.mkPen("#2196F3", width=2))
        self.current_marker = self.plot_widget.plot(
            pen=None,
            symbol="o",
            symbolBrush="#FF9800",
            symbolPen=pg.mkPen("#FF9800", width=1),
            symbolSize=7,
        )
        self.price_hline = pg.InfiniteLine(
            angle=0, pen=pg.mkPen("#FF9800", style=Qt.PenStyle.DashLine, width=1)
        )
        self.plot_widget.addItem(self.price_hline)
        self.price_hline.hide()

        # Phase 3 (GPU plan) — the factory returns ``GLCandlestickItem``
        # when ``AGREGAT_GL_CANDLES`` is truthy *and* PySide6's GL
        # bindings import cleanly; otherwise the legacy QPainter-based
        # :class:`CandlestickItem` is used. The two share the same
        # public surface (set_data / set_view / set_pyramid / …) so the
        # rest of ChartTab is backend-agnostic.
        try:
            self.candlestick_item = make_candlestick_item()
        except Exception:
            logger.warning("GLCandlestickItem init failed; falling back to CandlestickItem")
            from gui.chart_tab.items import CandlestickItem
            self.candlestick_item = CandlestickItem()
        self.candlestick_item.bull_color = "#26A69A"
        self.candlestick_item.bear_color = "#EF5350"
        self.plot_widget.addItem(self.candlestick_item)
        self.candlestick_item.hide()
        # Link ViewBox so candle width updates on zoom
        self.candlestick_item.set_view(vb)

        # Trade markers — triangles pointing up/down, X for close
        self.buy_markers = self.plot_widget.plot(
            pen=None,
            symbol="t1",
            symbolBrush="#26A69A",
            symbolPen=pg.mkPen("#26A69A"),
            symbolSize=14,
        )
        self.sell_markers = self.plot_widget.plot(
            pen=None,
            symbol="t",
            symbolBrush="#EF5350",
            symbolPen=pg.mkPen("#EF5350"),
            symbolSize=14,
        )
        self.close_markers = self.plot_widget.plot(
            pen=None,
            symbol="x",
            symbolBrush=pg.mkBrush(None),
            symbolPen=pg.mkPen("#E8E8EE", width=2),
            symbolSize=10,
        )

        # Batched trade-connection lines — one curve for winners, one for
        # losers. Each trade contributes a (entry, exit, NaN)-triplet so
        # ``connect='finite'`` breaks the curve between trades. This
        # collapses what used to be one ``PlotDataItem`` per trade into
        # two scene items total. (TZ Phase 2b — batched markers.)
        self.win_lines = self.plot_widget.plot(
            pen=pg.mkPen("#00E676", width=1, style=Qt.PenStyle.DashLine),
            connect="finite",
        )
        self.loss_lines = self.plot_widget.plot(
            pen=pg.mkPen("#FF1744", width=1, style=Qt.PenStyle.DashLine),
            connect="finite",
        )

        # Crosshair — thin, subtle
        self.vline = pg.InfiniteLine(
            angle=90, pen=pg.mkPen(QColor(80, 80, 110, 180), style=Qt.PenStyle.DashLine, width=1)
        )
        self.hline = pg.InfiniteLine(
            angle=0, pen=pg.mkPen(QColor(80, 80, 110, 180), style=Qt.PenStyle.DashLine, width=1)
        )
        self.plot_widget.addItem(self.vline, ignoreBounds=True)  # type: ignore[call-arg]
        self.plot_widget.addItem(self.hline, ignoreBounds=True)  # type: ignore[call-arg]

        # Crosshair label
        self.cross_label = pg.TextItem(color="#B0B0C0", anchor=(0, 0.5))
        self.cross_label.setZValue(100)
        self.plot_widget.addItem(self.cross_label, ignoreBounds=True)  # type: ignore[call-arg]
        self.cross_label.hide()

        # OHLC info label — top-left corner
        self.ohlc_label = pg.TextItem(color="#9A9AAE", anchor=(0, 1))
        self.ohlc_label.setZValue(100)
        self.plot_widget.addItem(self.ohlc_label, ignoreBounds=True)  # type: ignore[call-arg]
        self.ohlc_label.hide()

        self.proxy = pg.SignalProxy(
            self.plot_widget.scene().sigMouseMoved, rateLimit=40, slot=self._on_mouse_move
        )

        main_layout.addWidget(self.plot_widget, stretch=1)

        # Trade focus breadcrumb overlay (hidden by default)
        self._focus_bar = QWidget(self.plot_widget)
        self._focus_bar.setVisible(False)
        focus_layout = QHBoxLayout(self._focus_bar)
        focus_layout.setContentsMargins(8, 2, 8, 2)
        self._focus_label = QLabel("", self._focus_bar)
        self._focus_label.setStyleSheet(
            "color: #FFD54F; font-weight: bold; font-size: 12px; background: rgba(20,20,30,180);"
            " padding: 2px 8px; border-radius: 3px;"
        )
        self._focus_clear_btn = QPushButton("\u2716 Clear Focus", self._focus_bar)
        self._focus_clear_btn.setFixedHeight(22)
        self._focus_clear_btn.setStyleSheet(
            "QPushButton { color: #B0B0C0; background: rgba(40,40,55,200); border: 1px solid #4A4A5A;"
            " border-radius: 3px; padding: 0 8px; font-size: 11px; }"
            "QPushButton:hover { background: rgba(60,60,80,220); color: #FFFFFF; }"
        )
        self._focus_clear_btn.clicked.connect(self._clear_trade_focus_highlight)
        focus_layout.addWidget(self._focus_label)
        focus_layout.addWidget(self._focus_clear_btn)
        self.plot_widget.installEventFilter(self)

        # State
        self.current_tool = "None"
        self.user_drawings = (
            drawings_persist.load()
        )  # persistent: list of dicts with time/price coords
        self.user_drawings_history = []  # undo history
        self.user_drawings_redo = []  # redo stack
        self.user_graphic_items = []  # rendered items (cleared on each redraw)
        self.strategy_graphic_items = []
        self.trade_graphic_items = []
        self.res_times = np.array([])
        self._need_autorange = True
        self._user_zoom_width = None  # User's X-zoom width (set by wheel zoom)
        self._tracked_xrange = None  # Our own X-range tracking (bypasses pyqtgraph)
        self._cache_key = None
        self._cached_res_df = None
        self._cached_chart_arrays_key = None
        self._cached_chart_arrays = None
        self._cached_trade_marker_key = None
        self._cached_trade_marker_payload = None
        self._color_scheme = "Classic"
        self._last_click_pos = None
        self._brush_path = None
        self._brush_item = None
        self._is_brushing = False
        self.autofit_y = True
        self._selection_mode = False
        self._selection_start = None
        self._selection_overlay = None
        self._selection_mask_items = []
        self._selection_rect_data = None

        self._redraw_pending = False
        self._redraw_timer = QTimer()
        self._redraw_timer.setSingleShot(True)
        self._redraw_timer.setInterval(60)
        self._redraw_timer.timeout.connect(self._do_deferred_redraw)
        self._last_perf_stats = {}
        self._perf_stats_history = []
        self._perf_log_threshold_ms = 30.0
        self._adaptive_lod_enabled = True
        self._adaptive_lod_threshold_ms = 24.0
        self._adaptive_lod_cooldown = 0
        self._render_backend = (
            "gl" if self.candlestick_item.__class__.__name__ == "GLCandlestickItem" else "painter"
        )

        self.plot_widget.scene().sigMouseClicked.connect(self.on_mouse_clicked)
        self.engine.data_loaded.connect(self.on_data_loaded)
        self.engine.step_completed.connect(self.on_step_completed)
        self.apply_chart_theme()

    def _fit_all(self):
        """Reset zoom and fit all data."""
        self.plot_widget.autoRange()
        vb = self.plot_widget.getViewBox()
        vb.enableAutoRange(x=False, y=False)
        xr = vb.viewRange()[0]
        self._tracked_xrange = list(xr)
        self._user_zoom_width = xr[1] - xr[0]
        self._need_autorange = False

    def _pick_draw_color(self):
        c = QColorDialog.getColor(self._draw_color, self, "Drawing Color")
        if c.isValid():
            self._draw_color = c

    def _add_layer(self, tag: str, label: str, default_on: bool = True):
        """Add a toggleable layer to the Layers menu. Idempotent."""
        if tag in self.layer_cbs:
            return
        act = self._layers_menu.addAction(label)
        act.setCheckable(True)
        act.setChecked(default_on)
        act.triggered.connect(lambda _: self.redraw_chart())
        self.layer_cbs[tag] = act

    def _reset_layers(self):
        """Remove all strategy-registered layers, keep only 'entry'."""
        tags_to_remove = [t for t in self.layer_cbs if t != "entry"]
        for tag in tags_to_remove:
            act = self.layer_cbs.pop(tag)
            self._layers_menu.removeAction(act)

    def set_color_scheme(self, name):
        """Apply a chart color scheme."""
        if name not in CHART_SCHEMES:
            return
        self._color_scheme = name
        cs = CHART_SCHEMES[name]
        self.candlestick_item.set_colors(cs["bull"], cs["bear"])
        self.price_curve.setPen(pg.mkPen(cs["line"], width=2))
        self._cache_key = None
        self.redraw_chart()

    def apply_chart_theme(self):
        """Re-apply chart background from current theme."""
        t = get_theme()
        bg = t["chart_bg"]
        self.plot_widget.setBackground(QColor(bg))
        self.plot_widget.getAxis("right").setPen(pg.mkPen(t["border"]))
        self.plot_widget.getAxis("right").setTextPen(pg.mkPen(t["text2"]))
        self.time_axis.setPen(pg.mkPen(t["border"]))
        self.time_axis.setTextPen(pg.mkPen(t["text2"]))

        # Labels and Grid
        self.cross_label.setColor(QColor(t["text2"]))
        self.ohlc_label.setColor(QColor(t["text2"]))
        from gui.styling.theme import get_theme_name

        alpha_val = 0.06 if get_theme_name() == "dark" else 0.15
        self.plot_widget.showGrid(x=True, y=True, alpha=alpha_val)

    def _apply_theme(self) -> None:
        """Phase 3 theme-listener entry — re-apply locally-overridden QSS
        plus the chart background. Called by :func:`gui.theme.apply_theme`
        whenever the active palette changes.
        """
        if hasattr(self, "tool_label"):
            self.tool_label.setStyleSheet(
                f"color: {sh.c(get_theme(), 'accent_light')};"
                " font-weight: bold; background: transparent;"
            )
        try:
            self.apply_chart_theme()
        except Exception:
            # During tear-down or partial init, the plot may already be gone.
            pass

    def toggle_tools(self):
        """Toggle sidebar visibility."""
        # The sidebar is always visible in the new layout (it's small)
        pass

    # --- Drawing tool ---
    def _set_tool(self, name):
        self.current_tool = name
        if name == "None":
            self.tool_label.setText("")
            self.cancel_btn.hide()
        else:
            self.tool_label.setText(f"Drawing: {name}")
            self.cancel_btn.show()

    def _cancel_active_mode(self):
        if self._selection_mode:
            self.cancel_area_selection_mode()
            return
        self._set_tool("None")

    def enable_area_selection_mode(self):
        self._selection_mode = True
        self._selection_start = None
        self._selection_rect_data = None
        self.tool_label.setText("AI Selection: drag to select area")
        self.cancel_btn.show()
        self.plot_widget.setCursor(QCursor(Qt.CursorShape.CrossCursor))
        self._create_selection_mask()

    def cancel_area_selection_mode(self):
        self._selection_mode = False
        self._selection_start = None
        if self._selection_overlay is not None:
            self.plot_widget.removeItem(self._selection_overlay)
            self._selection_overlay = None
        self._clear_selection_mask()
        self.tool_label.setText("")
        self.cancel_btn.hide()
        self.plot_widget.unsetCursor()

    def _create_selection_mask(self):
        self._clear_selection_mask()
        for _ in range(4):
            item = QGraphicsRectItem()
            item.setPen(pg.mkPen(None))
            c = QColor("#050507")
            c.setAlphaF(0.45)
            item.setBrush(pg.mkBrush(c))
            item.setZValue(39)
            self.plot_widget.addItem(item)
            self._selection_mask_items.append(item)

    def _clear_selection_mask(self):
        for item in self._selection_mask_items:
            self.plot_widget.removeItem(item)
        self._selection_mask_items.clear()

    def _update_selection_mask(self, x1, y1, x2, y2):
        if len(self._selection_mask_items) != 4:
            return
        vb = self.plot_widget.getViewBox()
        vr = vb.viewRect()
        left, right = vr.left(), vr.right()
        top, bottom = vr.top(), vr.bottom()
        sx1, sx2 = min(x1, x2), max(x1, x2)
        sy1, sy2 = min(y1, y2), max(y1, y2)
        self._selection_mask_items[0].setRect(QRectF(left, top, max(0.0, right - left), max(0.0, sy1 - top)))
        self._selection_mask_items[1].setRect(QRectF(left, sy2, max(0.0, right - left), max(0.0, bottom - sy2)))
        self._selection_mask_items[2].setRect(QRectF(left, sy1, max(0.0, sx1 - left), max(0.0, sy2 - sy1)))
        self._selection_mask_items[3].setRect(QRectF(sx2, sy1, max(0.0, right - sx2), max(0.0, sy2 - sy1)))

    # --- Chart scroll via arrow keys ---
    def scroll_chart(self, direction):
        """Scroll chart left/right. direction: -1=left, +1=right."""
        vb = self.plot_widget.getViewBox()
        vr = vb.viewRect()
        step = vr.width() * 0.15
        vb.translateBy(x=direction * step)
        # Update our tracking
        self._tracked_xrange = list(vb.viewRange()[0])

    def _smooth_wheel_zoom(self, ev, axis=None):
        """Smooth independent zoom: wheel=X axis, Shift+wheel=Y axis, Ctrl+wheel=both axes."""
        from PySide6.QtCore import Qt as _Qt

        delta = ev.delta()
        if delta == 0:
            ev.ignore()
            return

        # Smooth zoom factor (smaller steps for better control)
        factor = 1.08 if delta > 0 else 1.0 / 1.08

        vb = self.plot_widget.getViewBox()
        pos = ev.pos()
        center = vb.mapToView(pos)
        modifiers = ev.modifiers() if hasattr(ev, "modifiers") else _Qt.KeyboardModifier.NoModifier

        if modifiers & _Qt.KeyboardModifier.ControlModifier:
            # Ctrl+wheel = zoom both axes (proportional zoom)
            vb.scaleBy((1.0 / factor, 1.0 / factor), center=center)
        elif modifiers & _Qt.KeyboardModifier.ShiftModifier:
            # Shift+wheel = zoom Y (price) only
            vb.scaleBy((1.0, 1.0 / factor), center=center)
        else:
            # Default wheel = zoom X (time) only
            vb.scaleBy((1.0 / factor, 1.0), center=center)
        # Record the user's zoom width so stepping preserves it
        vr = vb.viewRange()
        self._user_zoom_width = vr[0][1] - vr[0][0]
        self._tracked_xrange = list(vr[0])
        ev.accept()

    def zoom_chart(self, factor):
        """Zoom in/out. factor>1 = zoom in, <1 = zoom out."""
        vb = self.plot_widget.getViewBox()
        vb.scaleBy((1.0 / factor, 1.0))
        vr = vb.viewRange()
        self._user_zoom_width = vr[0][1] - vr[0][0]
        self._tracked_xrange = list(vr[0])

    # --- Crosshair with OHLCV tooltip ---
    def _on_mouse_move(self, evt):
        pos = evt[0]
        if not self.plot_widget.sceneBoundingRect().contains(pos):
            self.cross_label.hide()
            self.ohlc_label.hide()
            return
        mp = self.plot_widget.plotItem.vb.mapSceneToView(pos)
        self.vline.setPos(mp.x())
        self.hline.setPos(mp.y())

        idx = int(round(mp.x()))
        if 0 <= idx < len(self.res_times):
            _ = self.candlestick_item.get_ohlc_at(idx)

        # Price label on right side of crosshair
        vr = self.plot_widget.getViewBox().viewRect()
        self.cross_label.setText(f" {mp.y():.2f}")
        self.cross_label.setPos(vr.right() - (vr.width() * 0.01), mp.y())
        self.cross_label.show()

        # OHLC overlay disabled — cleaner chart per UX preference.
        self.ohlc_label.hide()

    def _snap_to_candle(self, x, y):
        """Snap x to nearest candle index, y to nearest OHLC price."""
        sx = max(0, min(int(round(x)), len(self.res_times) - 1))
        sy = y
        return sx, sy

    def _idx_to_time_str(self, idx):
        """Convert candle index to time string for persistent storage."""
        idx = max(0, min(idx, len(self.res_times) - 1))
        return str(pd.to_datetime(self.res_times[idx]))

    def _plot_mousePressEvent(self, evt):
        if len(self.res_times) == 0:
            return self._orig_plot_mousePressEvent(evt)

        scene_pos = (
            evt.scenePos() if hasattr(evt, "scenePos") else self.plot_widget.mapToScene(evt.pos())
        )
        pos = self.plot_widget.plotItem.vb.mapSceneToView(scene_pos)
        x, y = pos.x(), pos.y()

        if self._selection_mode and evt.button() == Qt.MouseButton.LeftButton:
            sx = max(0, min(int(round(x)), max(0, len(self.res_times) - 1)))
            self._selection_start = (sx, y)
            if self._selection_overlay is None:
                self._selection_overlay = QGraphicsRectItem()
                self._selection_overlay.setPen(pg.mkPen("#7FA2FF", width=2, style=Qt.PenStyle.DashLine))
                fc = QColor("#2962FF")
                fc.setAlphaF(0.28)
                self._selection_overlay.setBrush(pg.mkBrush(fc))
                self._selection_overlay.setZValue(40)
                self.plot_widget.addItem(self._selection_overlay)
            self._selection_overlay.setRect(QRectF(float(sx), float(y), 0.0, 0.0))
            self._update_selection_mask(float(sx), float(y), float(sx), float(y))
            evt.accept()
            return

        if evt.button() == Qt.MouseButton.RightButton:
            idx = int(round(x))
            ts = ""
            if 0 <= idx < len(self.res_times):
                try:
                    ts = pd.to_datetime(self.res_times[idx]).strftime("%Y-%m-%d %H:%M")
                except Exception:
                    pass
            menu = QMenu()
            menu.addAction(f"Time: {ts}").setEnabled(False)
            menu.addAction(f"Price: {y:.2f}").setEnabled(False)
            menu.addSeparator()
            menu.addAction("Zoom to Fit", self.plot_widget.autoRange)
            if self._last_click_pos is not None:
                lx, ly = self._last_click_pos
                menu.addSeparator()
                menu.addAction(
                    f"Δ {abs(int(round(x)) - int(round(lx)))} candles, {abs(y - ly):.2f} pts"
                ).setEnabled(False)
            from PySide6.QtGui import QCursor

            menu.exec(QCursor.pos())
            evt.accept()
            return

        self._last_click_pos = (x, y)
        if self.current_tool == "None":
            return self._orig_plot_mousePressEvent(evt)

        sx, sy = self._snap_to_candle(x, y)
        t_str = self._idx_to_time_str(sx)
        w = self._draw_width
        color = self._draw_color.name()

        if evt.button() == Qt.MouseButton.LeftButton and self.current_tool in [
            "Brush",
            "Line",
            "Rect",
        ]:
            self._drawing_active = self.current_tool
            self._drawing_start_pt = (sx, sy)
            self._drawing_start_t = t_str
            self.plot_widget.getViewBox().setMouseEnabled(x=False, y=False)

            if self.current_tool == "Brush":
                self._brush_path = QPainterPath(QPointF(x, y))
                self._interactive_item = QGraphicsPathItem(self._brush_path)
            elif self.current_tool == "Line":
                from PySide6.QtWidgets import QGraphicsLineItem

                self._interactive_item = QGraphicsLineItem(sx, sy, sx, sy)
            elif self.current_tool == "Rect":
                from PySide6.QtCore import QRectF
                from PySide6.QtWidgets import QGraphicsRectItem

                self._interactive_item = QGraphicsRectItem(QRectF(sx, sy, 0, 0))
                fc = QColor(color)
                fc.setAlphaF(0.2)
                self._interactive_item.setBrush(pg.mkBrush(fc))

            self._interactive_item.setPen(pg.mkPen(color, width=w))
            self.plot_widget.addItem(self._interactive_item)
            evt.accept()
            return

        if evt.button() != Qt.MouseButton.LeftButton:
            return self._orig_plot_mousePressEvent(evt)

        if self.current_tool == "HLine":
            self._save_drawing_state()
            self.user_drawings.append({"type": "hline", "p": sy, "color": color, "width": w})
            drawings_persist.save(self.user_drawings)
        elif self.current_tool == "Marker":
            self._save_drawing_state()
            self.user_drawings.append({"type": "marker", "t": t_str, "p": sy, "color": color})
            drawings_persist.save(self.user_drawings)
        elif self.current_tool == "Text":
            text, ok = QInputDialog.getText(self, "Add Text", "Enter text:")
            if ok and text:
                self._save_drawing_state()
                self.user_drawings.append(
                    {"type": "text", "t": t_str, "p": sy, "text": text, "color": color}
                )
                drawings_persist.save(self.user_drawings)
            else:
                return self._orig_plot_mousePressEvent(evt)

        self._draw_user_items(self.res_times)
        evt.accept()

    def _plot_mouseDoubleClickEvent(self, evt):
        if getattr(self, "current_tool", "None") != "None":
            return self._plot_mousePressEvent(evt)
        return self._orig_plot_mouseDoubleClickEvent(evt)

    def on_mouse_clicked(self, evt):
        # We handle drawing inside mousePressEvent now, this handles native clicks if needed
        pass

    def _plot_mouseMoveEvent(self, evt):
        if self._selection_mode and self._selection_start is not None:
            scene_pos = (
                evt.scenePos()
                if hasattr(evt, "scenePos")
                else self.plot_widget.mapToScene(evt.pos())
            )
            pos = self.plot_widget.plotItem.vb.mapSceneToView(scene_pos)
            sx, sy = self._selection_start
            ex = max(0, min(int(round(pos.x())), max(0, len(self.res_times) - 1)))
            ey = pos.y()
            if self._selection_overlay is not None:
                self._selection_overlay.setRect(
                    QRectF(float(min(sx, ex)), float(min(sy, ey)), float(abs(ex - sx)), float(abs(ey - sy)))
                )
            self._update_selection_mask(float(sx), float(sy), float(ex), float(ey))
            evt.accept()
            return
        if getattr(self, "_drawing_active", None):
            scene_pos = (
                evt.scenePos()
                if hasattr(evt, "scenePos")
                else self.plot_widget.mapToScene(evt.pos())
            )
            pos = self.plot_widget.plotItem.vb.mapSceneToView(scene_pos)
            if self._drawing_active == "Brush":
                self._brush_path.lineTo(pos.x(), pos.y())
                self._interactive_item.setPath(self._brush_path)
            elif self._drawing_active == "Line":
                sx_curr = max(0, min(int(round(pos.x())), len(self.res_times) - 1))
                sy_curr = pos.y()
                self._interactive_item.setLine(
                    self._drawing_start_pt[0], self._drawing_start_pt[1], sx_curr, sy_curr
                )
            elif self._drawing_active == "Rect":
                sx_curr = max(0, min(int(round(pos.x())), len(self.res_times) - 1))
                sy_curr = pos.y()
                from PySide6.QtCore import QRectF

                x1, y1 = self._drawing_start_pt
                self._interactive_item.setRect(
                    QRectF(min(x1, sx_curr), min(y1, sy_curr), abs(sx_curr - x1), abs(sy_curr - y1))
                )
            evt.accept()
            return
        self._orig_plot_mouseMoveEvent(evt)

    def _plot_mouseReleaseEvent(self, evt):
        if self._selection_mode and self._selection_start is not None:
            scene_pos = (
                evt.scenePos()
                if hasattr(evt, "scenePos")
                else self.plot_widget.mapToScene(evt.pos())
            )
            pos = self.plot_widget.plotItem.vb.mapSceneToView(scene_pos)
            sx, sy = self._selection_start
            ex = max(0, min(int(round(pos.x())), max(0, len(self.res_times) - 1)))
            ey = pos.y()
            rect_data = {
                "x1": float(min(sx, ex)),
                "x2": float(max(sx, ex)),
                "y1": float(min(sy, ey)),
                "y2": float(max(sy, ey)),
            }
            self._selection_rect_data = rect_data
            payload = self._build_selection_payload(rect_data)
            self.sigAreaSelected.emit(payload)
            self._selection_start = None
            self._selection_mode = False
            self.tool_label.setText("")
            self.cancel_btn.hide()
            self.plot_widget.unsetCursor()
            self._clear_selection_mask()
            evt.accept()
            return
        if getattr(self, "_drawing_active", None):
            tool = self._drawing_active
            self._drawing_active = None
            self.plot_widget.getViewBox().setMouseEnabled(x=True, y=True)

            scene_pos = (
                evt.scenePos()
                if hasattr(evt, "scenePos")
                else self.plot_widget.mapToScene(evt.pos())
            )
            pos = self.plot_widget.plotItem.vb.mapSceneToView(scene_pos)
            sx_curr = max(0, min(int(round(pos.x())), len(self.res_times) - 1))
            sy_curr = pos.y()
            t2_str = self._idx_to_time_str(sx_curr)

            if tool == "Brush":
                pts = []
                for i in range(self._brush_path.elementCount()):
                    el = self._brush_path.elementAt(i)
                    tx = self._idx_to_time_str(int(el.x))
                    pts.append({"t": tx, "p": el.y})
                if len(pts) > 1:
                    self.user_drawings.append(
                        {
                            "type": "brush",
                            "points": pts,
                            "color": self._draw_color.name(),
                            "width": self._draw_width,
                        }
                    )
                    drawings_persist.save(self.user_drawings)
            elif tool == "Line":
                self._save_drawing_state()
                self.user_drawings.append(
                    {
                        "type": "line",
                        "t1": self._drawing_start_t,
                        "p1": self._drawing_start_pt[1],
                        "t2": t2_str,
                        "p2": sy_curr,
                        "color": self._draw_color.name(),
                        "width": self._draw_width,
                    }
                )
                drawings_persist.save(self.user_drawings)
            elif tool == "Rect":
                self._save_drawing_state()
                self.user_drawings.append(
                    {
                        "type": "rect",
                        "t1": self._drawing_start_t,
                        "p1": self._drawing_start_pt[1],
                        "t2": t2_str,
                        "p2": sy_curr,
                        "color": self._draw_color.name(),
                        "width": self._draw_width,
                    }
                )
                drawings_persist.save(self.user_drawings)

            if getattr(self, "_interactive_item", None):
                self.plot_widget.removeItem(self._interactive_item)
                self._interactive_item = None
            self._brush_path = None
            self._draw_user_items(self.res_times)
            evt.accept()
            return
        self._orig_plot_mouseReleaseEvent(evt)

        # Update the camera tracker upon mouse release (user manual panning)
        if hasattr(self, "_tracked_xrange") and self._tracked_xrange is not None:
            xr = self.plot_widget.getViewBox().viewRange()[0]
            self._tracked_xrange = list(xr)
            self._user_zoom_width = xr[1] - xr[0]

    def _build_selection_payload(self, rect_data):
        if self.engine.df is None or len(self.res_times) == 0:
            return {"selection_rect": rect_data}
        idx1 = int(max(0, min(rect_data["x1"], len(self.res_times) - 1)))
        idx2 = int(max(0, min(rect_data["x2"], len(self.res_times) - 1)))
        if idx2 < idx1:
            idx1, idx2 = idx2, idx1
        res_df = self._cached_res_df if self._cached_res_df is not None else pd.DataFrame()
        candles = []
        if not res_df.empty:
            for i in range(idx1, min(idx2 + 1, len(res_df))):
                row = res_df.iloc[i]
                candles.append(
                    {
                        "time": str(pd.to_datetime(row["time"])),
                        "open": float(row["open"]),
                        "high": float(row["high"]),
                        "low": float(row["low"]),
                        "close": float(row["close"]),
                    }
                )
        t_start = pd.to_datetime(self.res_times[idx1]).to_datetime64()
        t_end = pd.to_datetime(self.res_times[idx2]).to_datetime64()
        trades = []
        for tr in self.engine.trades:
            try:
                et = self.engine.df.iloc[tr.entry_index]["time"].to_datetime64()
                if t_start <= et <= t_end:
                    trades.append(
                        {
                            "entry_index": int(tr.entry_index),
                            "exit_index": int(getattr(tr, "exit_index", -1)),
                            "entry_price": float(tr.entry_price),
                            "exit_price": float(getattr(tr, "exit_price", tr.entry_price)),
                            "side": str(getattr(tr, "side", "")),
                            "pnl": float(getattr(tr, "pnl", 0.0)),
                        }
                    )
            except Exception:
                continue
        vb = self.plot_widget.getViewBox()
        view_range = vb.viewRange()
        visible_layers = [tag for tag, act in self.layer_cbs.items() if act.isChecked()]
        return {
            "selection_rect": rect_data,
            "selection_time": {
                "from": str(pd.to_datetime(self.res_times[idx1])),
                "to": str(pd.to_datetime(self.res_times[idx2])),
            },
            "candles": candles,
            "trades": trades,
            "timeframe": self.visual_tf_cb.currentText(),
            "visible_indicators": visible_layers,
            "chart_state": {
                "chart_mode": self.chart_mode_cb.currentText(),
                "show_markers": self.show_markers_cb.isChecked(),
                "show_connections": self.show_connections_cb.isChecked(),
                "x_range": [float(view_range[0][0]), float(view_range[0][1])],
                "y_range": [float(view_range[1][0]), float(view_range[1][1])],
                "render_window": -1,  # unlimited — full history available
            },
        }

    def _on_roi_changed(self, roi, d):
        """Update persistent drawing dict when ROI is resized/moved."""
        if len(self.res_times) == 0:
            return
        pos = roi.pos()
        sz = roi.size()
        i1 = max(0, min(int(pos.x()), len(self.res_times) - 1))

        if d["type"] == "rect":
            d["t1"] = str(pd.to_datetime(self.res_times[i1]))
            d["p1"] = pos.y()
            i2 = max(0, min(int(pos.x() + sz.x()), len(self.res_times) - 1))
            d["t2"] = str(pd.to_datetime(self.res_times[i2]))
            d["p2"] = pos.y() + sz.y()
        elif d["type"] == "line":
            handles = roi.getSceneHandlePositions()
            if len(handles) >= 2:
                vp1 = self.plot_widget.getViewBox().mapSceneToView(handles[0][1])
                vp2 = self.plot_widget.getViewBox().mapSceneToView(handles[1][1])
                i1_line = max(0, min(int(vp1.x()), len(self.res_times) - 1))
                d["t1"] = str(pd.to_datetime(self.res_times[i1_line]))
                d["p1"] = vp1.y()
                i2_line = max(0, min(int(vp2.x()), len(self.res_times) - 1))
                d["t2"] = str(pd.to_datetime(self.res_times[i2_line]))
                d["p2"] = vp2.y()

        drawings_persist.save(self.user_drawings)

        self._is_roi_dragging = False

    def _save_drawing_state(self):
        """Save current drawings to undo history (max 50 levels)."""
        self.user_drawings_history.append(list(self.user_drawings))
        if len(self.user_drawings_history) > 50:
            self.user_drawings_history.pop(0)
        self.user_drawings_redo.clear()  # Clear redo on new action

    def clear_user_drawings(self):
        for i in self.user_graphic_items:
            self.plot_widget.removeItem(i)
            if hasattr(i, "deleteLater"):
                i.deleteLater()
        self.user_graphic_items.clear()
        self.user_drawings.clear()
        drawings_persist.save(self.user_drawings)
        logger.info("🗑️ User drawings cleared")

    def _clear_strategy_drawings(self):
        """Clear all strategy-generated drawings from engine."""
        for i in self.strategy_graphic_items:
            self.plot_widget.removeItem(i)
            if hasattr(i, "deleteLater"):
                i.deleteLater()
        self.strategy_graphic_items.clear()
        self.engine.drawings.clear()
        logger.info("🧹 Strategy drawings cleared")
        self.redraw_chart()

    def _draw_user_items(self, rt):
        """Re-render user drawings from stored time/price data."""
        for i in self.user_graphic_items:
            self.plot_widget.removeItem(i)
            if hasattr(i, "deleteLater"):
                i.deleteLater()
        self.user_graphic_items.clear()
        if len(rt) == 0:
            return
        for d in self.user_drawings:
            try:
                color = QColor(d.get("color", "#FFFFFF"))
                w = d.get("width", 2)
                dtype = d["type"]
                if dtype == "hline":
                    it = pg.InfiniteLine(
                        pos=d["p"],
                        angle=0,
                        pen=pg.mkPen(color, width=w, style=Qt.PenStyle.DashDotLine),
                    )
                    lbl = pg.TextItem(f"{d['p']:.2f}", color=color, anchor=(0, 1))
                    lbl.setPos(0, d["p"])
                    self.plot_widget.addItem(it)
                    self.user_graphic_items.append(it)
                    self.plot_widget.addItem(lbl)
                    self.user_graphic_items.append(lbl)
                elif dtype == "marker":
                    t1 = pd.to_datetime(d["t"]).to_datetime64()
                    i1 = max(0, int(np.searchsorted(rt, t1, side="right")) - 1)
                    # Red exclamation mark above candle
                    ohlc = self.candlestick_item.get_ohlc_at(i1)
                    offset_y = d["p"]
                    if ohlc:
                        offset_y = max(ohlc) + (abs(ohlc[1] - ohlc[2]) * 0.1)
                    it = pg.TextItem("❗️", color=QColor("#FF1744"), anchor=(0.5, 1))
                    font = it.textItem.font()
                    font.setPointSize(18)
                    it.textItem.setFont(font)
                    it.setPos(i1, offset_y)
                    self.plot_widget.addItem(it)
                    self.user_graphic_items.append(it)
                elif dtype == "text":
                    t1 = pd.to_datetime(d["t"]).to_datetime64()
                    i1 = max(0, int(np.searchsorted(rt, t1, side="right")) - 1)
                    it = pg.TextItem(d["text"], color=color, anchor=(0.5, 0.5))
                    it.setPos(i1, d["p"])
                    self.plot_widget.addItem(it)
                    self.user_graphic_items.append(it)
                elif dtype == "line":
                    t1 = pd.to_datetime(d["t1"]).to_datetime64()
                    t2 = pd.to_datetime(d["t2"]).to_datetime64()
                    i1 = max(0, int(np.searchsorted(rt, t1, side="right")) - 1)
                    i2 = max(0, int(np.searchsorted(rt, t2, side="right")) - 1)
                    it = pg.LineSegmentROI(
                        [[i1, d["p1"]], [i2, d["p2"]]], pen=pg.mkPen(color, width=w), movable=True
                    )
                    it.sigRegionChangeStarted.connect(
                        lambda r: setattr(self, "_is_roi_dragging", True)
                    )
                    it.sigRegionChangeFinished.connect(
                        lambda roi, d_ref=d: self._on_roi_changed(roi, d_ref)
                    )
                    self.plot_widget.addItem(it)
                    self.user_graphic_items.append(it)
                elif dtype == "rect":
                    t1 = pd.to_datetime(d["t1"]).to_datetime64()
                    t2 = pd.to_datetime(d["t2"]).to_datetime64()
                    i1 = max(0, int(np.searchsorted(rt, t1, side="right")) - 1)
                    i2 = max(0, int(np.searchsorted(rt, t2, side="right")) - 1)
                    mnx, mxx = min(i1, i2), max(i1, i2)
                    mny, mxy = min(d["p1"], d["p2"]), max(d["p1"], d["p2"])
                    fc = QColor(color)
                    fc.setAlphaF(0.2)
                    it = FilledRectROI(
                        [mnx, mny],
                        [mxx - mnx or 1.0, mxy - mny or 1.0],
                        pen=pg.mkPen(color, width=w),
                        movable=True,
                        brush=pg.mkBrush(fc),
                    )
                    it.sigRegionChangeStarted.connect(
                        lambda r: setattr(self, "_is_roi_dragging", True)
                    )
                    it.sigRegionChangeFinished.connect(
                        lambda roi, d_ref=d: self._on_roi_changed(roi, d_ref)
                    )
                    self.plot_widget.addItem(it)
                    self.user_graphic_items.append(it)
                elif dtype == "brush":
                    pts = d["points"]
                    path = QPainterPath()
                    for idx, pt in enumerate(pts):
                        t_px = pd.to_datetime(pt["t"]).to_datetime64()
                        i_px = max(0, int(np.searchsorted(rt, t_px, side="right")) - 1)
                        if idx == 0:
                            path.moveTo(i_px, pt["p"])
                        else:
                            path.lineTo(i_px, pt["p"])
                    it = QGraphicsPathItem(path)
                    it.setPen(pg.mkPen(color, width=w))
                    self.plot_widget.addItem(it)
                    self.user_graphic_items.append(it)
            except Exception as e:
                logger.error(f"UserDraw: {e}")

    def on_position_changed(self):
        """Called when engine position opens/closes/SL changes — trigger chart redraw."""
        self._redraw_pending = True
        if not self._redraw_timer.isActive():
            self._redraw_timer.start()

    def on_markers_toggled(self, _):
        vis = self.show_markers_cb.isChecked()
        self.buy_markers.setVisible(vis)
        self.sell_markers.setVisible(vis)
        self.close_markers.setVisible(vis)
        # Batched connection lines + any per-position graphics in
        # ``trade_graphic_items`` (currently just the open-position SL
        # line) follow the markers + connection toggle.
        conn_vis = vis and self.show_connections_cb.isChecked()
        self.win_lines.setVisible(conn_vis)
        self.loss_lines.setVisible(conn_vis)
        for item in self.trade_graphic_items:
            item.setVisible(conn_vis)

    def on_mode_changed(self, mode):
        if "Line" in mode:
            self.price_curve.show()
            self.candlestick_item.hide()
        else:
            self.price_curve.hide()
            self.candlestick_item.show()

    def on_data_loaded(self, total):
        for p in [
            self.price_curve,
            self.current_marker,
            self.buy_markers,
            self.sell_markers,
            self.close_markers,
            self.win_lines,
            self.loss_lines,
        ]:
            p.setData([], [])
        self.candlestick_item.set_data([])
        # Phase 2 (GPU plan) — rebuild the LOD pyramid from the engine's
        # base-TF frame so subsequent zoom-outs hit the M5/M15/H1/D1
        # buckets instead of redrawing millions of M1 bars. Skipped
        # silently when no data is loaded (cold start) and on any
        # construction failure — chart still renders, just without LOD.
        self._attach_pyramid()
        self.price_hline.hide()
        self.ohlc_label.hide()
        self._clear_trade_lines()
        self._reset_layers()
        self._need_autorange = True
        self._cache_key = None
        self._cached_chart_arrays_key = None
        self._cached_chart_arrays = None
        self._cached_trade_marker_key = None
        self._cached_trade_marker_payload = None
        self._prev_res_times = None

    def _attach_pyramid(self) -> None:
        """Build (or rebuild) the LOD pyramid from ``engine.df`` and
        hand it to :attr:`candlestick_item`. Called from
        :py:meth:`on_data_loaded` so a fresh CSV / .agbk load picks
        up the pyramid; safe to call repeatedly — the candle item's
        ``set_pyramid`` swaps in the new pyramid and resets the active
        TF to base on each call.
        """
        try:
            df = getattr(self.engine, "df", None)
            if df is None or len(df) == 0:
                self.candlestick_item.set_pyramid(None)
                return
            from engine.data.lod_pyramid import LodPyramid

            pyramid = LodPyramid.from_base(df, base_tf="1m")
            self.candlestick_item.set_pyramid(pyramid)
            logger.info(
                "LOD pyramid attached (levels=%s, base=%d bars)",
                pyramid.list_levels(),
                len(df),
            )
        except Exception:
            logger.exception("LOD pyramid construction failed; chart still renders")
            try:
                self.candlestick_item.set_pyramid(None)
            except Exception:
                pass

    def on_tf_changed(self, tf):
        tf = str(tf)
        if self.visual_tf_cb.currentText() != tf:
            prev_block = self.visual_tf_cb.blockSignals(True)
            self.visual_tf_cb.setCurrentText(tf)
            self.visual_tf_cb.blockSignals(prev_block)
        self._cache_key = None
        self._cached_chart_arrays_key = None
        self._cached_chart_arrays = None
        self._cached_trade_marker_key = None
        self._cached_trade_marker_payload = None
        # Phase 0.2 (A3) — ensure forming arrays are built for the selected TF
        # so the chart can render the in-progress candle when switching away
        # from 1m after a headless run + reset + manual stepping.
        if hasattr(self.engine, 'set_visible_tf'):
            self.engine.set_visible_tf(tf)
        # Reset autorange so the viewport adapts to the new TF's bar count
        # (e.g. switching 1m→5m: 400 bars → ~80 bars on X axis).
        self._need_autorange = True
        self.redraw_chart()

    def on_step_completed(self, idx, candle):
        if idx < 0:
            self.on_data_loaded(0)
            return
        self._redraw_pending = True
        if not self._redraw_timer.isActive():
            self._redraw_timer.start()

    def _do_deferred_redraw(self):
        if self._redraw_pending:
            self._redraw_pending = False
            self.redraw_chart()

    def _get_tf_minutes(self):
        m = {"1m": 1, "2m": 2, "3m": 3, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240}
        return m.get(self.visual_tf_cb.currentText().lower(), 1)

    def _build_chart_arrays(self, res_df):
        """Build and cache NumPy chart payload for the current render frame."""
        key = (id(res_df), len(res_df))
        cached = self._cached_chart_arrays
        if self._cached_chart_arrays_key == key and cached is not None:
            return cached, True

        n = len(res_df)
        x = np.arange(n, dtype=np.float64)
        cl = res_df["close"].to_numpy(dtype=np.float64, copy=False)
        o = res_df["open"].to_numpy(dtype=np.float64, copy=False)
        l = res_df["low"].to_numpy(dtype=np.float64, copy=False)
        h = res_df["high"].to_numpy(dtype=np.float64, copy=False)
        candle_arr = np.column_stack((x, o, cl, l, h))
        candle_arr.setflags(write=False)
        chart_arrays = {
            "x": x,
            "close": cl,
            "candle_arr": candle_arr,
            "open": o,
            "low": l,
            "high": h,
        }
        self._cached_chart_arrays_key = key
        self._cached_chart_arrays = chart_arrays
        return chart_arrays, False

    def _build_trade_marker_payload(self, rt):
        """Precompute trade-marker arrays once per trade/timeframe state."""
        key = (
            id(rt),
            len(rt),
            len(self.engine.trades),
            bool(self.show_connections_cb.isChecked()),
        )
        cached = self._cached_trade_marker_payload
        if self._cached_trade_marker_key == key and cached is not None:
            return cached, True

        buy_x, buy_y, sell_x, sell_y, close_x, close_y = [], [], [], [], [], []
        win_x, win_y, loss_x, loss_y = [], [], [], []
        if len(rt) > 0:
            for tr in self.engine.trades:
                try:
                    ei = max(0, min(int(tr.entry_index), len(rt) - 1))
                    xi = max(0, min(int(getattr(tr, "exit_index", ei)), len(rt) - 1))
                    ep = float(tr.entry_price)
                    xp = float(getattr(tr, "exit_price", ep))
                    is_long = str(getattr(tr, "direction", getattr(tr, "side", ""))).lower().startswith("l")
                    if is_long:
                        buy_x.append(ei)
                        buy_y.append(ep)
                    else:
                        sell_x.append(ei)
                        sell_y.append(ep)
                    close_x.append(xi)
                    close_y.append(xp)
                    if self.show_connections_cb.isChecked():
                        target_x = win_x if float(getattr(tr, "pnl", 0.0)) >= 0 else loss_x
                        target_y = win_y if float(getattr(tr, "pnl", 0.0)) >= 0 else loss_y
                        target_x.extend([ei, xi, np.nan])
                        target_y.extend([ep, xp, np.nan])
                except Exception:
                    continue

        payload = {
            "buy_x": np.asarray(buy_x, dtype=np.float64),
            "buy_y": np.asarray(buy_y, dtype=np.float64),
            "sell_x": np.asarray(sell_x, dtype=np.float64),
            "sell_y": np.asarray(sell_y, dtype=np.float64),
            "close_x": np.asarray(close_x, dtype=np.float64),
            "close_y": np.asarray(close_y, dtype=np.float64),
            "win_x": np.asarray(win_x, dtype=np.float64),
            "win_y": np.asarray(win_y, dtype=np.float64),
            "loss_x": np.asarray(loss_x, dtype=np.float64),
            "loss_y": np.asarray(loss_y, dtype=np.float64),
        }
        self._cached_trade_marker_key = key
        self._cached_trade_marker_payload = payload
        return payload, False

    def _phase5_backend_state(self, bars_hint: int) -> dict:
        """Return the current backend policy snapshot used by Phase 5 adaptive rendering."""
        want_gl = should_use_gl_candles(
            force_gpu=bool(GPU_STATE.get("enabled", False)),
            bars_hint=bars_hint,
        )
        item_name = self.candlestick_item.__class__.__name__
        is_gl_item = item_name == "GLCandlestickItem"
        active_backend = str((getattr(self.candlestick_item, "_paint_stats", {}) or {}).get("last_backend", self._render_backend))
        return {
            "gpu_enabled": bool(GPU_STATE.get("enabled", False)),
            "gpu_backend": str(GPU_STATE.get("backend", "software")),
            "gl_candles_requested": bool(want_gl),
            "gl_candles_active": bool(is_gl_item),
            "render_backend": active_backend,
        }

    def _maybe_adapt_render_budget(self, perf_stats: dict) -> dict:
        """Phase 5 adaptive rendering guardrail.

        If redraw cost exceeds the frame budget, tighten the candle LOD budget.
        The change is sticky for a short cooldown so we do not thrash the setting.
        """
        decision = {
            "adaptive_lod_applied": False,
            "adaptive_lod_prev": int(getattr(self.candlestick_item, "_max_bars", 0)),
            "adaptive_lod_new": int(getattr(self.candlestick_item, "_max_bars", 0)),
            "adaptive_reason": "none",
        }
        if not self._adaptive_lod_enabled:
            decision["adaptive_reason"] = "disabled"
            return decision

        if self._adaptive_lod_cooldown > 0:
            self._adaptive_lod_cooldown -= 1
            decision["adaptive_reason"] = "cooldown"
            return decision

        total_ms = float(perf_stats.get("total_ms", 0.0))
        prev_budget = max(250, int(getattr(self.candlestick_item, "_max_bars", 2000)))
        if total_ms <= self._adaptive_lod_threshold_ms:
            decision["adaptive_reason"] = "within_budget"
            return decision

        if prev_budget <= 500:
            decision["adaptive_reason"] = "min_budget_reached"
            return decision

        new_budget = max(500, int(prev_budget * 0.75))
        if new_budget == prev_budget:
            decision["adaptive_reason"] = "unchanged"
            return decision

        try:
            self.candlestick_item.set_max_bars(new_budget)
            self._adaptive_lod_cooldown = 5
            decision.update(
                adaptive_lod_applied=True,
                adaptive_lod_prev=prev_budget,
                adaptive_lod_new=new_budget,
                adaptive_reason="frame_budget_exceeded",
            )
        except Exception:
            logger.exception("Phase 5 adaptive LOD update failed")
            decision["adaptive_reason"] = "error"
        return decision

    # ==============================
    # Main redraw
    # ==============================
    def redraw_chart(self):
        """Redraw the current rendering window of candles."""
        import time as _time
        t_total = _time.perf_counter()

        if getattr(self, "_is_roi_dragging", False):
            self._last_perf_stats = {"skip_reason": "roi_dragging"}
            return

        if self.engine.df is None:
            self._last_perf_stats = {"skip_reason": "no_data"}
            return
        idx = self.engine.current_idx
        if idx < 0:
            idx = 0
        tf = self.visual_tf_cb.currentText().lower()

        t0 = _time.perf_counter()
        ck = (idx, tf, id(self.engine.df))
        cache_hit = ck == self._cache_key and self._cached_res_df is not None
        if cache_hit:
            res_df = self._cached_res_df
        else:
            if tf != "1m" and hasattr(self.engine, "tf_aggregator"):
                # Sync tf_aggregator to the current 1m index so forming
                # candles are visible even before the engine has stepped
                # (after reset / fresh data load, _current_1m_idx == -1).
                self.engine.tf_aggregator.advance_to(idx)
                comp = self.engine.tf_aggregator.get_completed_tail(tf, 9999999)
                form = self.engine.tf_aggregator.get_forming(tf)
                agg = comp + ([form] if form else [])
                if agg:
                    res_df = pd.DataFrame(agg)
                else:
                    res_df = pd.DataFrame()
            else:
                # Pass the ENTIRE history up to the current index.
                # OpenGL + LodPyramid will handle the performance automatically.
                res_df = self.engine.df.iloc[0 : idx + 1].copy()

            # Phase 3 — Cap the render window to avoid lag when idx is at the
            # end of a huge dataset (e.g. after headless run with 200k+ candles).
            # We keep a generous buffer beyond the visible area for smooth
            # scrolling, but never the full history.
            if len(res_df) > 5_000:
                tail = res_df.iloc[-5_000:].reset_index(drop=True)
                res_df = tail

            if len(res_df) == 0:
                self._last_perf_stats = {
                    "skip_reason": "empty_res_df",
                    "cache_hit": cache_hit,
                    "tf": tf,
                    "idx": idx,
                }
                return
            self._cache_key = ck
            self._cached_res_df = res_df
        t_data = _time.perf_counter() - t0

        rt = res_df["time"].values

        shift = 0
        prev_rt = getattr(self, "_prev_res_times", None)
        if prev_rt is not None and len(prev_rt) > 0 and len(rt) > 0:
            prev_t = prev_rt[-1]
            new_i = int(np.searchsorted(rt, prev_t))
            if new_i < len(rt) and rt[new_i] == prev_t:
                shift = (len(prev_rt) - 1) - new_i
        self._prev_res_times = rt

        self.res_times = rt
        t0 = _time.perf_counter()
        # Cache floating point timestamps for instant O(1) lookups during mouse scrolling
        try:
            self._res_ts = pd.DatetimeIndex(rt).view(np.int64) / 10**9
        except Exception:
            self._res_ts = np.array([pd.to_datetime(t).timestamp() for t in rt])
        self.time_axis.set_times(rt)
        t_axis = _time.perf_counter() - t0

        n = len(res_df)
        t0 = _time.perf_counter()
        chart_arrays, arrays_cache_hit = self._build_chart_arrays(res_df)
        t_ohlc_pack = _time.perf_counter() - t0

        x = chart_arrays["x"]
        cl = chart_arrays["close"]
        candle_arr = chart_arrays["candle_arr"]

        vb = self.plot_widget.getViewBox()
        vb.enableAutoRange(x=False, y=False)

        t0 = _time.perf_counter()
        self.price_curve.setData(x, cl)
        self.current_marker.setData([n - 1], [cl[-1]])
        self.price_hline.setPos(cl[-1])
        self.price_hline.show()

        t0 = _time.perf_counter()
        self.candlestick_item.set_data(candle_arr)
        t_candle_set = _time.perf_counter() - t0
        t_candles = t_ohlc_pack + t_candle_set

        t0 = _time.perf_counter()
        trade_marker_payload, trade_markers_cache_hit = self._build_trade_marker_payload(rt)
        self._draw_trade_markers(rt, payload=trade_marker_payload)
        t_markers = _time.perf_counter() - t0

        t0 = _time.perf_counter()
        self._draw_strategy(rt)
        t_strategy = _time.perf_counter() - t0

        t0 = _time.perf_counter()
        self._draw_user_items(rt)
        t_user = _time.perf_counter() - t0

        t0 = _time.perf_counter()
        t_autofit = 0.0
        # ── Camera control ──
        cur_x = n - 1
        default_window = 150.0  # Sensible default zoom (number of bars)

        if self._need_autorange or self._tracked_xrange is None:
            self._need_autorange = False

            # Instead of auto-ranging everything, set a sensible default window
            # focused on the current (latest) candle.
            new_right = cur_x + 5.0
            new_left = max(-0.5, new_right - default_window)
            new_xrange = [new_left, new_right]

            vb.setRange(xRange=new_xrange, padding=0)
            self._tracked_xrange = new_xrange
            self._user_zoom_width = default_window

            if self.autofit_y:
                t_fit = _time.perf_counter()
                self._autofit_y(vb, res_df, n)
                t_autofit += _time.perf_counter() - t_fit
        else:
            xmin, xmax = self._tracked_xrange
            w = self._user_zoom_width if self._user_zoom_width is not None else (xmax - xmin)

            xmin -= shift
            xmax -= shift

            # If current index is near or past the right edge, or if we were already "following",
            # stick to the latest candle.
            # "Near edge" = within 10% of window width from the right.
            follow_threshold = xmax - (w * 0.1)

            if cur_x >= follow_threshold:
                new_right = cur_x + 5.0
                new_xrange = [new_right - w, new_right]
            else:
                new_xrange = [xmin, xmax]

            vb.setRange(xRange=new_xrange, padding=0)
            self._tracked_xrange = new_xrange
            if self.autofit_y:
                t_fit = _time.perf_counter()
                self._autofit_y(vb, res_df, n)
                t_autofit += _time.perf_counter() - t_fit
        t_camera = _time.perf_counter() - t0

        candle_stats = getattr(self.candlestick_item, "_paint_stats", {}) or {}
        visible_lo, visible_hi = (0, 0)
        try:
            visible_lo, visible_hi = self.candlestick_item._visible_slice()
        except Exception:
            pass
        visible_bars = max(0, int(visible_hi - visible_lo))
        t_all = _time.perf_counter() - t_total
        perf_stats = {
            "tf": tf,
            "idx": int(idx),
            "cache_hit": bool(cache_hit),
            "chart_arrays_cache_hit": bool(arrays_cache_hit),
            "trade_markers_cache_hit": bool(trade_markers_cache_hit),
            "bars_total": int(n),
            "bars_visible": int(visible_bars),
            "trades_total": int(len(self.engine.trades)),
            "drawings_total": int(len(self.engine.drawings)),
            "markers_enabled": bool(self.show_markers_cb.isChecked()),
            "connections_enabled": bool(self.show_connections_cb.isChecked()),
            "backend": str(candle_stats.get("last_backend", "painter")),
            "data_prepare_ms": round(t_data * 1000.0, 3),
            "axis_prepare_ms": round(t_axis * 1000.0, 3),
            "ohlc_pack_ms": round(t_ohlc_pack * 1000.0, 3),
            "candles_set_data_ms": round(t_candle_set * 1000.0, 3),
            "candles_total_ms": round(t_candles * 1000.0, 3),
            "markers_ms": round(t_markers * 1000.0, 3),
            "strategy_ms": round(t_strategy * 1000.0, 3),
            "user_draw_ms": round(t_user * 1000.0, 3),
            "camera_ms": round(t_camera * 1000.0, 3),
            "autofit_ms": round(t_autofit * 1000.0, 3),
            "candles_gen_ms": round(float(candle_stats.get("gen_time_ms", 0.0)), 3),
            "candles_paint_ms": round(float(candle_stats.get("paint_time_ms", 0.0)), 3),
            "candles_draw_lines_calls": int(candle_stats.get("draw_lines_calls", 0)),
            "candles_draw_rects_calls": int(candle_stats.get("draw_rects_calls", 0)),
            "candles_bars_drawn": int(candle_stats.get("bars_drawn", 0)),
            "candles_bars_culled": int(candle_stats.get("bars_culled", 0)),
            "candles_viewport_cache_hit": bool(candle_stats.get("viewport_cache_hit", False)),
            "total_ms": round(t_all * 1000.0, 3),
        }
        perf_stats.update(self._phase5_backend_state(int(n)))
        perf_stats.update(self._maybe_adapt_render_budget(perf_stats))
        self._last_perf_stats = perf_stats
        hist = self._perf_stats_history
        hist.append(perf_stats)
        if len(hist) > 120:
            del hist[:-120]

        if t_all * 1000.0 > self._perf_log_threshold_ms:
            logger.info("chart_perf %s", perf_stats)

    def _autofit_y(self, vb, res_df, n):
        """Auto-fit Y axis to the high/low range of visible candles."""
        vr = vb.viewRect()
        x_left = max(0, int(vr.left()))
        x_right = min(n - 1, int(vr.right()))
        if x_right < x_left:
            return
        visible_h = res_df["high"].values[x_left : x_right + 1]
        visible_l = res_df["low"].values[x_left : x_right + 1]
        if len(visible_h) == 0:
            return
        y_min = float(visible_l.min())
        y_max = float(visible_h.max())
        pad = (y_max - y_min) * 0.05 if y_max > y_min else 1.0
        vb.setRange(yRange=[y_min - pad, y_max + pad], padding=0)

    # ==============================
    # Time-based sync
    # ==============================
    def _get_time_at_idx(self, idx):
        """Interpolate timestamp for a fractional index."""
        if len(self.res_times) == 0:
            return 0.0
        idx = max(0, min(idx, len(self.res_times) - 1))
        i = int(idx)
        frac = idx - i
        t1 = float(self._res_ts[i])
        if frac > 0 and i + 1 < len(self._res_ts):
            t2 = float(self._res_ts[i + 1])
            return t1 + (t2 - t1) * frac
        return t1

    def _get_idx_at_time(self, ts):
        """Interpolate fractional index for a timestamp."""
        if len(self.res_times) == 0:
            return 0.0
        # Use the pre-computed _res_ts array for searchsorted
        i = max(0, int(np.searchsorted(self._res_ts, ts, side="right")) - 1)
        if i + 1 < len(self._res_ts):
            t1 = float(self._res_ts[i])
            t2 = float(self._res_ts[i + 1])
            if t2 > t1 and ts >= t1:
                return i + min(1.0, (ts - t1) / (t2 - t1))
        return float(i)

    def _on_my_range_changed(self, _, range_tuple):
        if self._is_syncing or len(self.res_times) == 0:
            return
        xr = range_tuple[0]
        t1 = self._get_time_at_idx(xr[0])
        t2 = self._get_time_at_idx(xr[1])
        if t2 > t1:
            self.sigTimeRangeChanged.emit(t1, t2)
            # Phase 4 (GPU plan) — when an async data pager is attached,
            # ping it with the new viewport time bounds so any tiles
            # we're scrolling toward can warm in the background. The
            # call is cheap (one lock acquisition + a dict scan) and
            # internally deduplicated, so wiring it into the rate-
            # limited range signal doesn't pile up worker submissions.
            pager = getattr(self, "_data_pager", None)
            if pager is not None:
                try:
                    pager.ensure_visible(
                        pd.Timestamp(t1, unit="s"),
                        pd.Timestamp(t2, unit="s"),
                    )
                except Exception:
                    logger.exception("DataPager.ensure_visible failed")

    # ------------------------------------------------------------------
    # Phase 4 (GPU plan) — optional async data pager
    # ------------------------------------------------------------------

    def attach_data_pager(self, pager) -> None:
        """Bind a :class:`engine.data.data_pager.DataPager` to the chart.

        Once attached, the chart re-pings the pager whenever the user
        scrolls / zooms (via :py:meth:`_on_my_range_changed`) so any
        tiles overlapping the new viewport are warmed in the
        background. When the pager emits ``chunks_changed`` the chart
        re-feeds the engine with the freshly-stitched frame via
        :py:meth:`engine.load_data`, which fires ``data_loaded`` and
        funnels through the existing :py:meth:`on_data_loaded` →
        :py:meth:`_attach_pyramid` rebuild path.

        Idempotent: calling twice with the same pager is safe; calling
        with a different pager swaps the binding and disconnects the
        previous one's signals. Pass ``None`` to detach entirely.
        """
        previous = getattr(self, "_data_pager", None)
        if previous is pager:
            return
        if previous is not None:
            try:
                previous.chunks_changed.disconnect(self._on_pager_chunks_changed)
            except (RuntimeError, TypeError):
                # Already disconnected or signal slot mismatch — both
                # are fine, we're just trying to clean up.
                pass
            try:
                previous.pager_error.disconnect(self._on_pager_error)
            except (RuntimeError, TypeError):
                pass
        self._data_pager = pager
        if pager is not None:
            pager.chunks_changed.connect(self._on_pager_chunks_changed)
            pager.pager_error.connect(self._on_pager_error)
            logger.info(
                "DataPager attached (max_resident=%d, tiles=%d)",
                pager.max_resident,
                len(pager.list_tiles()),
            )

    def _on_pager_chunks_changed(self, total_rows: int) -> None:
        """Slot for :py:attr:`DataPager.chunks_changed`.

        Pulls the stitched frame from the pager and feeds it into the
        engine. Zero-row events (empty cache after ``set_tiles``) are
        ignored so a teardown doesn't blow away the current dataset.
        """
        if total_rows <= 0:
            return
        
        timer = getattr(self, "_pager_debounce_timer", None)
        if timer is None:
            from PySide6.QtCore import QTimer
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.setInterval(200) # 200ms debounce
            timer.timeout.connect(self._do_pager_chunks_changed)
            self._pager_debounce_timer = timer
        
        timer.start()

    def _do_pager_chunks_changed(self) -> None:
        pager = getattr(self, "_data_pager", None)
        if pager is None:
            return
        try:
            df = pager.get_dataframe()
        except Exception:
            logger.exception("DataPager.get_dataframe failed")
            return
        if df is None or len(df) == 0:
            return
        try:
            self.engine.load_data(df)
        except Exception:
            logger.exception(
                "engine.load_data failed for paged frame (rows=%d)", len(df)
            )

    def _on_pager_error(self, tile_id: str, msg: str) -> None:
        """Slot for :py:attr:`DataPager.pager_error`. We only log —
        the chart keeps rendering whatever data is already resident."""
        logger.warning("DataPager error: tile=%s msg=%s", tile_id, msg)

    def sync_to_time_range(self, t1, t2):
        if len(self.res_times) == 0:
            return
        self._is_syncing = True
        try:
            i1 = self._get_idx_at_time(t1)
            i2 = self._get_idx_at_time(t2)
            if i2 > i1:
                self.plot_widget.getViewBox().setRange(xRange=[i1, i2], padding=0)
        finally:
            self._is_syncing = False

    # ==============================
    # MT5 trade arrows
    # ==============================
    def _clear_trade_lines(self):
        for item in self.trade_graphic_items:
            self.plot_widget.removeItem(item)
        self.trade_graphic_items.clear()

    def _draw_trade_markers(self, rt, payload=None):
        self._clear_trade_lines()
        show = self.show_markers_cb.isChecked()
        show_l = self.show_connections_cb.isChecked()
        if not show or len(rt) == 0:
            for p in [
                self.buy_markers,
                self.sell_markers,
                self.close_markers,
                self.win_lines,
                self.loss_lines,
            ]:
                p.setData([], [])
            return

        df = self.engine.df
        pos = self.engine.position
        cp = None
        if pos is not None:
            cp = self.engine.ctx.candle.get("close", pos.entry_price)

        if payload is None:
            from gui.chart_tab.markers import build_trade_marker_arrays

            arrays = build_trade_marker_arrays(
                rt=rt,
                trades=self.engine.trades,
                df_len=len(df),
                position=pos,
                position_close_price=cp,
                show_connections=show_l,
            )
        else:
            arrays = type(
                "_MarkerPayload",
                (),
                {
                    "bx": payload["buy_x"],
                    "by": payload["buy_y"],
                    "sx": payload["sell_x"],
                    "sy": payload["sell_y"],
                    "cx": payload["close_x"],
                    "cy": payload["close_y"],
                    "win_x": payload["win_x"],
                    "win_y": payload["win_y"],
                    "loss_x": payload["loss_x"],
                    "loss_y": payload["loss_y"],
                },
            )()

        # Open-position SL line is the only piece of per-position graphics
        # that still goes through ``trade_graphic_items``; everything else
        # is in the batched curves above. (TZ Phase 2b.)
        if pos and pos.sl and show_l and pos.entry_index < len(df):
            et = df.iloc[pos.entry_index]["time"]
            if hasattr(et, "to_datetime64"):
                et = et.to_datetime64()
            if et >= rt[0]:
                sl = pg.InfiniteLine(
                    pos=pos.sl,
                    angle=0,
                    pen=pg.mkPen("#FF1744", width=1, style=Qt.PenStyle.DashLine),
                )
                self.plot_widget.addItem(sl)
                self.trade_graphic_items.append(sl)

        self.buy_markers.setData(arrays.bx, arrays.by)
        self.sell_markers.setData(arrays.sx, arrays.sy)
        self.close_markers.setData(arrays.cx, arrays.cy)
        if show_l:
            self.win_lines.setData(arrays.win_x, arrays.win_y)
            self.loss_lines.setData(arrays.loss_x, arrays.loss_y)
        else:
            self.win_lines.setData([], [])
            self.loss_lines.setData([], [])

    # ==============================
    # Strategy drawings
    # ==============================
    def _draw_strategy(self, rt):
        for item in self.strategy_graphic_items:
            self.plot_widget.removeItem(item)
        self.strategy_graphic_items.clear()
        hidden = {tag for tag, act in self.layer_cbs.items() if not act.isChecked()}
        if len(rt) == 0:
            return
        vmin, vmax = rt[0], rt[-1]
        for d in self.engine.drawings:
            dtag = d.get("tag", "")
            if dtag and dtag in hidden:
                continue
            try:
                # Use pre-cached numpy datetime64 (set by engine.add_drawing)
                t1 = d.get("_t_np") or d.get("_t1_np")
                if t1 is None:
                    tk = "t" if "t" in d else "t1"
                    t1 = pd.to_datetime(d[tk]).to_datetime64()
                if "_t2_np" in d:
                    t2 = d["_t2_np"]
                    if t1 < vmin and t2 < vmin:
                        continue
                    if t1 > vmax and t2 > vmax:
                        continue
                elif "t2" in d:
                    t2 = pd.to_datetime(d["t2"]).to_datetime64()
                    if t1 < vmin and t2 < vmin:
                        continue
                    if t1 > vmax and t2 > vmax:
                        continue
                else:
                    if t1 < vmin or t1 > vmax:
                        continue
                i1 = max(0, int(np.searchsorted(rt, t1, side="right")) - 1)
                p1 = d.get("p", d.get("p1"))
                rc = d.get("color", "white")
                if isinstance(rc, str) and rc.startswith("rgba"):
                    try:
                        pts = [
                            float(p.strip())
                            for p in rc.replace("rgba(", "").replace(")", "").split(",")
                        ]
                        color = QColor(int(pts[0]), int(pts[1]), int(pts[2]), int(pts[3] * 255))
                    except (ValueError, IndexError, TypeError):
                        color = QColor("white")
                else:
                    color = QColor(rc)
                if not color.isValid():
                    color = QColor("white")

                if d["type"] == "line":
                    t2 = d.get("_t2_np")
                    if t2 is None:
                        t2 = pd.to_datetime(d["t2"]).to_datetime64()
                    i2 = max(0, int(np.searchsorted(rt, t2, side="right")) - 1)
                    it = pg.PlotDataItem(
                        [i1, i2], [p1, d["p2"]], pen=pg.mkPen(color, width=d.get("width", 1))
                    )
                    self.plot_widget.addItem(it)
                    self.strategy_graphic_items.append(it)
                elif d["type"] == "rect":
                    t2 = d.get("_t2_np")
                    if t2 is None:
                        t2 = pd.to_datetime(d["t2"]).to_datetime64()
                    i2 = max(0, int(np.searchsorted(rt, t2, side="right")) - 1)
                    p2 = d["p2"]
                    mnx, mxx = min(i1, i2), max(i1, i2)
                    mny, mxy = min(p1, p2), max(p1, p2)
                    brush = None
                    if d.get("fill", True):
                        fc = QColor(color)
                        fc.setAlphaF(d.get("opacity", 0.3))
                        brush = pg.mkBrush(fc)
                    it = QGraphicsRectItem(
                        float(mnx), float(mny), float(mxx - mnx) or 1.0, float(mxy - mny)
                    )
                    it.setPen(pg.mkPen(color))
                    if brush:
                        it.setBrush(brush)
                    self.plot_widget.addItem(it)
                    self.strategy_graphic_items.append(it)
                elif d["type"] == "text":
                    it = pg.TextItem(d["text"], color=color, anchor=(0.5, 0))
                    it.setPos(i1, p1)
                    self.plot_widget.addItem(it)
                    self.strategy_graphic_items.append(it)
            except Exception as e:
                logger.error(f"Draw: {e}")

    # ==============================
    # Trade focus navigation
    # ==============================
    def _animate_view_range(self, vb: pg.ViewBox, x1: float, x2: float, duration_ms: int = 300) -> None:
        cur = vb.viewRange()[0]
        anim = QVariantAnimation(self)
        anim.setDuration(duration_ms)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        def _update(t: float) -> None:
            xl = cur[0] + (x1 - cur[0]) * t
            xr = cur[1] + (x2 - cur[1]) * t
            vb.setRange(xRange=(xl, xr), padding=0.0)
        anim.valueChanged.connect(_update)
        anim.finished.connect(lambda: vb.setRange(xRange=(x1, x2), padding=0.02))
        anim.start(QVariantAnimation.DeletionPolicy.DeleteWhenStopped)

    def focus_trade(self, trade: object, pad_bars: int = 60, force: bool = False) -> None:
        """Best-effort: pan/zoom chart to a trade's entry/exit window.

        Intended for navigation from Trades tab popups; must never raise.
        If *force* is True, re-navigate even if the same trade was already focused.
        """
        trade_id = getattr(trade, "id", None)
        if not force and hasattr(self, "_last_focused_trade") and self._last_focused_trade == trade_id:
            return
        self._last_focused_trade = trade_id

        try:
            rt = getattr(self, "res_times", None)
            if rt is None or len(rt) == 0:
                logger.debug("focus_trade: res_times not available")
                return

            # Safe time extraction
            et = getattr(trade, "entry_time_np", None)
            if et is None:
                et_raw = getattr(trade, "entry_time", None)
                if not et_raw:
                    logger.debug("focus_trade: trade missing entry_time")
                    return
                try:
                    et_ts = pd.to_datetime(et_raw, errors="coerce")
                    if pd.isna(et_ts):
                        logger.debug("focus_trade: invalid entry_time format")
                        return
                    et = np.datetime64(et_ts)
                except Exception as e:
                    logger.debug(f"focus_trade: entry_time conversion failed: {e}")
                    return

            xt = getattr(trade, "exit_time_np", None)
            if xt is None:
                xt_raw = getattr(trade, "exit_time", None)
                if xt_raw:
                    try:
                        xt_ts = pd.to_datetime(xt_raw, errors="coerce")
                        if not pd.isna(xt_ts):
                            xt = np.datetime64(xt_ts)
                    except Exception:
                        pass
                if xt is None:
                    xt = et

            # Safe searchsorted with bounds checking
            try:
                ie = int(np.clip(np.searchsorted(rt, et, side="right") - 1, 0, len(rt) - 1))
                ix = int(np.clip(np.searchsorted(rt, xt, side="right") - 1, 0, len(rt) - 1))
            except (ValueError, IndexError, TypeError) as e:
                logger.debug(f"focus_trade: searchsorted failed: {e}")
                return

            x1 = max(0, min(ie, ix) - int(pad_bars))
            x2 = min(len(rt) - 1, max(ie, ix) + int(pad_bars))
            if x2 <= x1:
                logger.debug("focus_trade: invalid x range")
                return

            vb = self.plot_widget.getViewBox()
            if vb is None:
                logger.debug("focus_trade: ViewBox not available")
                return

            try:
                self._animate_view_range(vb, x1, x2)
            except Exception as e:
                logger.debug(f"focus_trade: setRange failed: {e}")
                return

            # Clear previous focus artifacts (region + lines)
            try:
                if hasattr(self, "_trade_focus_region") and self._trade_focus_region is not None:
                    self.plot_widget.removeItem(self._trade_focus_region)
                    self._trade_focus_region = None
                if hasattr(self, "_trade_focus_lines") and self._trade_focus_lines:
                    for ln in list(self._trade_focus_lines):
                        try:
                            self.plot_widget.removeItem(ln)
                        except Exception:
                            pass
                    self._trade_focus_lines = []
            except Exception:
                pass

            # Highlight region (subtle)
            try:
                t = get_theme()
                brush = QColor(sh.c(t, "accent_dim"))
                brush.setAlpha(45)
                region = pg.LinearRegionItem(values=(min(ie, ix), max(ie, ix)), movable=False)
                region.setBrush(brush)
                region.setZValue(-50)
                self.plot_widget.addItem(region)
                self._trade_focus_region = region
            except Exception:
                pass

            # Entry/Exit vertical markers (clear anchors)
            try:
                t = get_theme()
                self._trade_focus_lines = []
                col_entry = QColor(sh.c(t, "accent_light"))
                col_exit = QColor(sh.c(t, "amber"))
                col_entry.setAlpha(220)
                col_exit.setAlpha(220)
                le = pg.InfiniteLine(
                    pos=min(ie, ix),
                    angle=90,
                    movable=False,
                    pen=pg.mkPen(col_entry, width=2),
                )
                lx = pg.InfiniteLine(
                    pos=max(ie, ix),
                    angle=90,
                    movable=False,
                    pen=pg.mkPen(col_exit, width=2),
                )
                le.setZValue(50)
                lx.setZValue(50)
                self.plot_widget.addItem(le)
                self.plot_widget.addItem(lx)
                self._trade_focus_lines.extend([le, lx])
            except Exception:
                pass

            # Show breadcrumb overlay
            try:
                trade_label = getattr(trade, "id", None) or getattr(trade, "label", str(trade))
                self._focus_label.setText(f"Focused: Trade #{trade_label}")
                self._focus_bar.setVisible(True)
                self._reposition_focus_bar()
            except Exception:
                pass

            # Auto-clear after short delay (keeps chart clean)
            try:
                if not hasattr(self, "_trade_focus_clear_timer"):
                    self._trade_focus_clear_timer = QTimer(self)
                    self._trade_focus_clear_timer.setSingleShot(True)
                    self._trade_focus_clear_timer.timeout.connect(
                        self._clear_trade_focus_highlight
                    )
                elif self._trade_focus_clear_timer is not None and not self._chart_alive:
                    return
                self._trade_focus_clear_timer.start(10000)
            except Exception:
                pass
        except Exception:
            logger.exception("focus_trade failed")

    def _reposition_focus_bar(self) -> None:
        """Keep focus breadcrumb in top-right corner of the plot viewport."""
        if not self._chart_alive:
            return
        try:
            pw = self.plot_widget.width()
            fw = self._focus_bar.sizeHint().width()
            self._focus_bar.setGeometry(pw - fw - 8, 4, fw, 26)
        except Exception:
            pass

    def eventFilter(self, obj: object, event: QEvent) -> bool:
        if obj is self.plot_widget and event.type() == QEvent.Type.Resize:
            self._reposition_focus_bar()
        return super().eventFilter(obj, event)

    def _clear_trade_focus_highlight(self) -> None:
        """Remove trade focus region/lines (best-effort)."""
        if not self._chart_alive:
            return
        try:
            if hasattr(self, "_trade_focus_region") and self._trade_focus_region is not None:
                try:
                    self.plot_widget.removeItem(self._trade_focus_region)
                except Exception:
                    pass
                self._trade_focus_region = None
            if hasattr(self, "_trade_focus_lines") and self._trade_focus_lines:
                for ln in list(self._trade_focus_lines):
                    try:
                        self.plot_widget.removeItem(ln)
                    except Exception:
                        pass
                self._trade_focus_lines = []
            self._last_focused_trade = None
            if hasattr(self, "_focus_bar") and self._focus_bar is not None:
                self._focus_bar.setVisible(False)
        except Exception:
            pass
