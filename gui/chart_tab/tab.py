"""Chart Tab — candlestick chart, drawing menu, time axis, trade arrows."""

import logging

import numpy as np
import pandas as pd
import pyqtgraph as pg
from PySide6.QtCore import QPointF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainterPath
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
from gui.chart_tab.items import CandlestickItem, FilledRectROI
from gui.styling.theme import CHART_SCHEMES, get_theme, register_theme_listener

logger = logging.getLogger("StrategyTester.gui.chart_tab")
RENDER_WINDOW = 300


class ChartTab(QWidget):
    sigTimeRangeChanged = Signal(object, object)  # emits (t_start, t_end) for cross-chart sync

    def __init__(self, engine):
        super().__init__()
        self.engine = engine
        self._is_syncing = False  # prevent recursive sync loops
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
        self.cancel_btn.clicked.connect(lambda: self._set_tool("None"))
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

        # Custom mouse-drag handler to scale price axis
        def price_mouseDragEvent(ev):
            if hasattr(ev, "button"):
                from PySide6.QtCore import Qt

                btn = ev.button()
                if btn == Qt.MouseButton.LeftButton:
                    ev.button = lambda: Qt.MouseButton.RightButton
                elif btn == Qt.MouseButton.RightButton:
                    ev.button = lambda: Qt.MouseButton.LeftButton
            pg.AxisItem.mouseDragEvent(left_axis, ev)

        def price_mouseDoubleClickEvent(ev):
            from PySide6.QtCore import Qt

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
        self._color_scheme = "Classic"
        self._last_click_pos = None
        self._brush_path = None
        self._brush_item = None
        self._is_brushing = False
        self.autofit_y = True

        self._redraw_pending = False
        self._redraw_timer = QTimer()
        self._redraw_timer.setSingleShot(True)
        self._redraw_timer.setInterval(60)
        self._redraw_timer.timeout.connect(self._do_deferred_redraw)

        self.plot_widget.scene().sigMouseClicked.connect(self.on_mouse_clicked)
        self.engine.data_loaded.connect(self.on_data_loaded)
        self.engine.step_completed.connect(self.on_step_completed)
        self.apply_chart_theme()

    def _fit_all(self):
        """Reset zoom and fit all data."""
        self._user_zoom_width = None
        self._tracked_xrange = None
        self.plot_widget.autoRange()
        vb = self.plot_widget.getViewBox()
        vb.enableAutoRange(x=False, y=False)

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
        self.price_hline.hide()
        self.ohlc_label.hide()
        self._clear_trade_lines()
        self._reset_layers()
        self._need_autorange = True
        self._cache_key = None
        self._prev_res_times = None

    def on_tf_changed(self, tf):
        self._cache_key = None
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

    # ==============================
    # Main redraw
    # ==============================
    def redraw_chart(self):
        """Redraw the current rendering window of candles."""
        import time as _time
        t_total = _time.perf_counter()

        if getattr(self, "_is_roi_dragging", False):
            return

        idx = self.engine.current_idx
        if idx < 0 or self.engine.df is None:
            return
        tf = self.visual_tf_cb.currentText().lower()
        tf_min = self._get_tf_minutes()

        t0 = _time.perf_counter()
        ck = (idx, tf)
        if ck == self._cache_key and self._cached_res_df is not None:
            res_df = self._cached_res_df
        else:
            if tf != "1m" and hasattr(self.engine, "tf_aggregator"):
                comp = self.engine.tf_aggregator.get_completed_tail(tf, RENDER_WINDOW)
                form = self.engine.tf_aggregator.get_forming(tf)
                agg = comp + ([form] if form else [])
                if agg:
                    res_df = pd.DataFrame(agg)
                else:
                    res_df = pd.DataFrame()
            else:
                w = RENDER_WINDOW * tf_min
                s = max(0, idx + 1 - w)
                res_df = self.engine.df.iloc[s : idx + 1].copy()

            if len(res_df) == 0:
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
        # Cache floating point timestamps for instant O(1) lookups during mouse scrolling
        try:
            self._res_ts = pd.DatetimeIndex(rt).view(np.int64) / 10**9
        except Exception:
            self._res_ts = np.array([pd.to_datetime(t).timestamp() for t in rt])
            
        self.time_axis.set_times(rt)

        n = len(res_df)
        x = np.arange(n)
        cl = res_df["close"].values

        vb = self.plot_widget.getViewBox()
        vb.enableAutoRange(x=False, y=False)

        t0 = _time.perf_counter()
        self.price_curve.setData(x, cl)
        self.current_marker.setData([n - 1], [cl[-1]])
        self.price_hline.setPos(cl[-1])
        self.price_hline.show()

        o = res_df["open"].values
        c_vals = res_df["close"].values
        l = res_df["low"].values
        h = res_df["high"].values
        cd = list(zip(x, o, c_vals, l, h))
        self.candlestick_item.set_data(cd)
        t_candles = _time.perf_counter() - t0

        t0 = _time.perf_counter()
        self._draw_trade_markers(rt)
        t_markers = _time.perf_counter() - t0

        t0 = _time.perf_counter()
        self._draw_strategy(rt)
        t_strategy = _time.perf_counter() - t0

        t0 = _time.perf_counter()
        self._draw_user_items(rt)
        t_user = _time.perf_counter() - t0

        t0 = _time.perf_counter()
        # ── Camera control ──
        cur_x = n - 1
        if self._need_autorange or self._tracked_xrange is None:
            self._need_autorange = False
            self._user_zoom_width = None
            vb.enableAutoRange(x=True, y=True)
            vb.autoRange()
            vb.enableAutoRange(x=False, y=False)
            self._tracked_xrange = list(vb.viewRange()[0])
        else:
            xmin, xmax = self._tracked_xrange
            w = self._user_zoom_width if self._user_zoom_width is not None else (xmax - xmin)

            xmin -= shift
            xmax -= shift

            if cur_x > xmax - 3.0 and cur_x < xmax + 5.0 + max(0, shift):
                new_right = cur_x + 3.0
                new_xrange = [new_right - w, new_right]
            elif cur_x < xmin and cur_x > xmin - 5.0 - max(0, shift):
                new_xrange = [cur_x - 3.0, cur_x - 3.0 + w]
            else:
                new_xrange = [xmin, xmax]
            vb.setRange(xRange=new_xrange, padding=0)
            self._tracked_xrange = new_xrange
            if self.autofit_y:
                self._autofit_y(vb, res_df, n)
        t_camera = _time.perf_counter() - t0

        t_all = _time.perf_counter() - t_total
        if t_all > 0.030:  # Only log when redraw takes > 30ms
            logger.info(
                f"⏱ redraw: data={t_data*1000:.1f}ms "
                f"candles={t_candles*1000:.1f}ms "
                f"markers={t_markers*1000:.1f}ms "
                f"strategy={t_strategy*1000:.1f}ms "
                f"user_draw={t_user*1000:.1f}ms "
                f"camera={t_camera*1000:.1f}ms "
                f"TOTAL={t_all*1000:.1f}ms "
                f"(n={n}, trades={len(self.engine.trades)}, drawings={len(self.engine.drawings)})"
            )

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

    def _draw_trade_markers(self, rt):
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

        from gui.chart_tab.markers import build_trade_marker_arrays

        df = self.engine.df
        pos = self.engine.position
        cp = None
        if pos is not None:
            cp = self.engine.ctx.candle.get("close", pos.entry_price)

        arrays = build_trade_marker_arrays(
            rt=rt,
            trades=self.engine.trades,
            df_len=len(df),
            position=pos,
            position_close_price=cp,
            show_connections=show_l,
        )

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
                    it = pg.TextItem(d["text"], color=color, anchor=(0, 1))
                    it.setPos(i1, p1)
                    self.plot_widget.addItem(it)
                    self.strategy_graphic_items.append(it)
            except Exception as e:
                logger.error(f"Draw: {e}")
