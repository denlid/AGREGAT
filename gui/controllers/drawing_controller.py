"""DrawingController — manages all user-placed drawings on the chart.

Owns: tool state, drawing data, undo/redo, persistence, rendering, mouse handling.
Strategy drawings (engine.drawings) remain in ChartTab — they are read-only and
engine-owned, not interactive.
"""

import copy
import logging
import sys
from typing import List, Optional

import numpy as np
import pandas as pd
import pyqtgraph as pg
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtCore import QObject
from PySide6.QtGui import QColor, QKeySequence, QPainterPath, QPainterPathStroker, QTransform, QTextCursor
from PySide6.QtWidgets import QColorDialog, QGraphicsPathItem, QInputDialog

from gui.persistence import drawings_persist

logger = logging.getLogger("StrategyTester.drawing_controller")

_MAX_UNDO = 50


class DraggablePathItem(QGraphicsPathItem):
    """Custom graphics item for brush paths that supports dragging and hovering effects."""

    def __init__(self, path: QPainterPath, d_ref: dict, rt: np.ndarray, controller) -> None:
        super().__init__(path)
        self.d_ref = d_ref
        self.rt = rt
        self.controller = controller

        self.setAcceptHoverEvents(True)
        self.setAcceptedMouseButtons(Qt.MouseButton.LeftButton | Qt.MouseButton.RightButton)

        self.dragging = False
        self.last_mouse_pos = None
        self.mouseHovering = False

    @property
    def pen(self):
        return super().pen()

    @pen.setter
    def pen(self, val):
        super().setPen(val)

    def shape(self) -> QPainterPath:
        path = self.path()
        try:
            scene = self.scene()
            if scene is None:
                return path
            views = scene.views()
            if not views:
                return path

            view = views[0]
            trans = self.deviceTransform(view.viewportTransform())
            view_path = trans.map(path)

            stroker = QPainterPathStroker()
            stroker.setWidth(15.0)
            stroker.setCapStyle(Qt.PenCapStyle.RoundCap)
            stroker.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            view_stroke_path = stroker.createStroke(view_path)

            inv_trans, ok = trans.inverted()
            if ok:
                return inv_trans.map(view_stroke_path)
            return path
        except Exception as e:
            logger.error("DraggablePathItem.shape error: %s", e, exc_info=True)
            return path

    def hoverEnterEvent(self, event) -> None:
        self.mouseHovering = True
        if self.controller.current_tool == "None":
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            if not hasattr(self, "original_pen") or self.original_pen is None:
                self.original_pen = self.pen
            c = self.original_pen.color()
            hover_pen = pg.mkPen(c, width=self.original_pen.width() + 1.5)
            self.setPen(hover_pen)
            self.update()
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event) -> None:
        self.mouseHovering = False
        self.unsetCursor()
        if hasattr(self, "original_pen") and self.original_pen is not None:
            self.setPen(self.original_pen)
            self.update()
        super().hoverLeaveEvent(event)

    def mousePressEvent(self, event) -> None:
        if self.controller.current_tool == "None" and event.button() == Qt.MouseButton.LeftButton:
            self.dragging = True
            self.last_mouse_pos = event.scenePos()
            self.controller.is_roi_dragging = True
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self.dragging and self.last_mouse_pos is not None:
            curr_mouse_pos = event.scenePos()

            vb = self.controller._pw.getViewBox()
            p_curr = vb.mapSceneToView(curr_mouse_pos)
            p_last = vb.mapSceneToView(self.last_mouse_pos)

            dx = p_curr.x() - p_last.x()
            dy = p_curr.y() - p_last.y()

            self.moveBy(dx, dy)
            self.last_mouse_pos = curr_mouse_pos
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self.dragging:
            self.dragging = False
            self.controller.is_roi_dragging = False
            self.last_mouse_pos = None

            dx = self.pos().x()
            dy = self.pos().y()

            if dx != 0 or dy != 0:
                pts = self.d_ref.get("points", [])
                if pts:
                    new_pts = []
                    for pt in pts:
                        i_px = self.controller._time_to_idx(pt["t"], self.rt)
                        i_px_new = i_px + dx

                        t_new = self.controller._idx_to_time(int(round(i_px_new)), self.rt)
                        p_new = pt["p"] + dy
                        new_pts.append({"t": t_new, "p": p_new})

                    self.controller._save_state()
                    self.d_ref["points"] = new_pts
                    self.controller.save()

                    self.setPos(0, 0)
                    self.controller.render(self.rt)
                    self.controller.drawings_changed.emit()

            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def mouseClickEvent(self, event) -> None:
        if self.controller.current_tool == "None" and event.button() == Qt.MouseButton.RightButton:
            self.controller._show_context_menu(self, self.d_ref, self.rt)
            event.accept()

    def contextMenuEvent(self, event) -> None:
        if self.controller.current_tool == "None":
            self.controller._show_context_menu(self, self.d_ref, self.rt)
            event.accept()
        else:
            super().contextMenuEvent(event)


class DraggableTextItem(pg.TextItem):
    """Custom TextItem that supports dragging, hovering effects, and double-click to edit."""

    def __init__(self, text: str, color: QColor, anchor: tuple, d_ref: dict, rt: np.ndarray, controller) -> None:
        super().__init__(text=text, color=color, anchor=anchor)
        self.d_ref = d_ref
        self.rt = rt
        self.controller = controller
        self.original_color = color

        self.setAcceptHoverEvents(True)
        self.setAcceptedMouseButtons(Qt.MouseButton.LeftButton | Qt.MouseButton.RightButton)

        self.dragging = False
        self.last_mouse_pos = None
        self.mouseHovering = False

        # In-place editing configuration
        self.textItem.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)

        orig_focus_out = self.textItem.focusOutEvent
        def new_focus_out(event, _orig=orig_focus_out):
            _orig(event)
            # Clear text selection highlight
            cursor = self.textItem.textCursor()
            cursor.clearSelection()
            self.textItem.setTextCursor(cursor)

            self.textItem.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
            new_text = self.textItem.toPlainText()
            if new_text != self.d_ref.get("text", ""):
                self.controller._save_state()
                self.d_ref["text"] = new_text
                self.controller.save()
                self.controller.drawings_changed.emit()
        self.textItem.focusOutEvent = new_focus_out

        orig_key_press = self.textItem.keyPressEvent
        def new_key_press(event, _orig=orig_key_press):
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self.textItem.clearFocus()
                event.accept()
            elif event.key() == Qt.Key.Key_Escape:
                self.textItem.setPlainText(self.d_ref.get("text", ""))
                self.textItem.clearFocus()
                event.accept()
            else:
                _orig(event)
        self.textItem.keyPressEvent = new_key_press

    def start_inplace_editing(self) -> None:
        self.textItem.setTextInteractionFlags(Qt.TextInteractionFlag.TextEditorInteraction)
        self.textItem.setFocus()
        cursor = self.textItem.textCursor()
        cursor.select(QTextCursor.SelectionType.Document)
        self.textItem.setTextCursor(cursor)

    def hoverEnterEvent(self, event) -> None:
        self.mouseHovering = True
        # Only highlight if not currently editing in-place
        if self.controller.current_tool == "None" and not (self.textItem.textInteractionFlags() & Qt.TextInteractionFlag.TextEditorInteraction):
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            self.setColor(QColor("#2196F3"))
            self.update()
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event) -> None:
        self.mouseHovering = False
        self.unsetCursor()
        self.setColor(self.original_color)
        self.update()
        super().hoverLeaveEvent(event)

    def mousePressEvent(self, event) -> None:
        # Ignore drag mouse presses if we are editing
        if self.textItem.textInteractionFlags() & Qt.TextInteractionFlag.TextEditorInteraction:
            super().mousePressEvent(event)
            return

        if self.controller.current_tool == "None" and event.button() == Qt.MouseButton.LeftButton:
            self.dragging = True
            self.last_mouse_pos = event.scenePos()
            self.controller.is_roi_dragging = True
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self.dragging and self.last_mouse_pos is not None:
            curr_mouse_pos = event.scenePos()

            vb = self.controller._pw.getViewBox()
            p_curr = vb.mapSceneToView(curr_mouse_pos)
            p_last = vb.mapSceneToView(self.last_mouse_pos)

            dx = p_curr.x() - p_last.x()
            dy = p_curr.y() - p_last.y()

            self.moveBy(dx, dy)
            self.last_mouse_pos = curr_mouse_pos
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self.dragging:
            self.dragging = False
            self.controller.is_roi_dragging = False
            self.last_mouse_pos = None

            initial_x = self.controller._time_to_idx(self.d_ref["t"], self.rt)
            initial_y = self.d_ref["p"]

            dx = self.pos().x() - initial_x
            dy = self.pos().y() - initial_y

            if dx != 0 or dy != 0:
                i_px_new = initial_x + dx
                t_new = self.controller._idx_to_time(int(round(i_px_new)), self.rt)
                p_new = initial_y + dy

                self.controller._save_state()
                self.d_ref["t"] = t_new
                self.d_ref["p"] = p_new
                self.controller.save()

                self.controller.render(self.rt)
                self.controller.drawings_changed.emit()

            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        if self.controller.current_tool == "None" and event.button() == Qt.MouseButton.LeftButton:
            self.start_inplace_editing()
            event.accept()
        else:
            super().mouseDoubleClickEvent(event)

    def mouseClickEvent(self, event) -> None:
        if self.controller.current_tool == "None" and event.button() == Qt.MouseButton.RightButton:
            self.controller._show_context_menu(self, self.d_ref, self.rt)
            event.accept()

    def contextMenuEvent(self, event) -> None:
        if self.controller.current_tool == "None":
            self.controller._show_context_menu(self, self.d_ref, self.rt)
            event.accept()
        else:
            super().contextMenuEvent(event)


class DrawingController(QObject):
    tool_changed = Signal(str)      # emits tool name or "None"
    drawings_changed = Signal()     # emits when list mutates

    def __init__(self, plot_widget: pg.PlotWidget, persist_key: str = "default") -> None:
        super().__init__()
        self._pw = plot_widget
        self._persist_key = persist_key  # reserved for future per-symbol support

        # Tool config
        self.current_tool: str = "None"
        self._draw_color = QColor("#FFFFFF")
        self._draw_width: int = 2

        # Data
        self.user_drawings: List[dict] = []
        self._history: List[List[dict]] = []
        self._redo: List[List[dict]] = []

        # Rendered Qt items (parallel to user_drawings order not guaranteed)
        self._graphic_items: list = []

        # Dirty flag — render() skips rebuild when False (set True on any mutation)
        self._dirty: bool = True
        self._last_rt: Optional[np.ndarray] = None
        self._ts_cache: dict = {}

        # Drag-in-progress state
        self._drawing_active: Optional[str] = None
        self._drawing_start_pt: Optional[tuple] = None
        self._drawing_start_t: Optional[str] = None
        self._interactive_item = None
        self._brush_path: Optional[QPainterPath] = None
        self._is_roi_dragging: bool = False

        # Copy/Paste state
        self.copied_drawing: Optional[dict] = None

        self.load()

    @property
    def is_roi_dragging(self) -> bool:
        return self._is_roi_dragging

    @is_roi_dragging.setter
    def is_roi_dragging(self, val: bool) -> None:
        self._is_roi_dragging = val
        try:
            from PySide6.QtWidgets import QApplication
            if val:
                if not getattr(self, "_cursor_overridden", False):
                    QApplication.setOverrideCursor(Qt.CursorShape.ClosedHandCursor)
                    self._cursor_overridden = True
            else:
                if getattr(self, "_cursor_overridden", False):
                    QApplication.restoreOverrideCursor()
                    self._cursor_overridden = False
        except Exception as e:
            logger.error("Error setting is_roi_dragging cursor: %s", e)

    def __del__(self) -> None:
        # Prevent stuck ClosedHandCursor by restoring on destruction
        if getattr(self, "_cursor_overridden", False):
            try:
                from PySide6.QtWidgets import QApplication
                QApplication.restoreOverrideCursor()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Tool configuration
    # ------------------------------------------------------------------

    def set_plot_widget(self, plot_widget: pg.PlotWidget) -> None:
        """Assign the PlotWidget reference to the controller."""
        self._pw = plot_widget

    def set_symbol(self, symbol: str) -> None:
        """Set the active asset symbol and load its drawings."""
        self._persist_key = symbol
        self.load()
        self._mark_dirty()

    def reset_for_new_data(self, symbol: str) -> None:
        """Reset drawings for a new data load — clears all drawings
        instead of loading old ones from disk, then updates the persist key."""
        self._clear_items()
        self.user_drawings.clear()
        self._history.clear()
        self._redo.clear()
        self._dirty = False
        self._last_rt = None
        self._persist_key = symbol
        drawings_persist.save(self.user_drawings, self._persist_key)
        self.drawings_changed.emit()
        logger.info("User drawings reset for new data symbol=%s", symbol)

    def set_tool(self, name: str) -> None:
        self.current_tool = name
        self.tool_changed.emit(name)

    def cancel(self) -> None:
        self._finish_drag(cancelled=True)
        self.set_tool("None")
        self.is_roi_dragging = False

    def pick_color(self, parent=None) -> None:
        c = QColorDialog.getColor(self._draw_color, parent, "Drawing Color")
        if c.isValid():
            self._draw_color = c

    def set_width(self, w: int) -> None:
        self._draw_width = w

    def _mark_dirty(self) -> None:
        self._dirty = True

    # ------------------------------------------------------------------
    # Undo / Redo
    # ------------------------------------------------------------------

    def undo(self) -> None:
        if not self._history:
            return
        self._redo.append(copy.deepcopy(self.user_drawings))
        self.user_drawings = self._history.pop()
        drawings_persist.save(self.user_drawings, self._persist_key)
        self._mark_dirty()
        self.drawings_changed.emit()

    def redo(self) -> None:
        if not self._redo:
            return
        self._history.append(copy.deepcopy(self.user_drawings))
        self.user_drawings = self._redo.pop()
        drawings_persist.save(self.user_drawings, self._persist_key)
        self._mark_dirty()
        self.drawings_changed.emit()

    def _save_state(self) -> None:
        self._history.append(copy.deepcopy(self.user_drawings))
        if len(self._history) > _MAX_UNDO:
            self._history.pop(0)
        self._redo.clear()
        self._mark_dirty()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def load(self) -> None:
        self.user_drawings = drawings_persist.load(self._persist_key)

    def save(self) -> None:
        drawings_persist.save(self.user_drawings, self._persist_key)

    # ------------------------------------------------------------------
    # Clear
    # ------------------------------------------------------------------

    def clear_all(self) -> None:
        self._clear_items()
        self.user_drawings.clear()
        self._history.clear()
        self._redo.clear()
        self._dirty = False  # items already cleared, no rebuild needed
        self._last_rt = None
        drawings_persist.save(self.user_drawings, self._persist_key)
        self.drawings_changed.emit()
        logger.info("User drawings cleared")

    # ------------------------------------------------------------------
    # Coordinate helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _snap(x: float, y: float, rt: np.ndarray):
        sx = max(0, min(int(round(x)), len(rt) - 1)) if len(rt) else 0
        return sx, y

    @staticmethod
    def _rdp(points: list, epsilon: float) -> list:
        """Ramer-Douglas-Peucker polyline simplification."""
        if len(points) < 3:
            return points
        # Find point with max distance from line start→end
        x1, y1 = points[0]["ix"], points[0]["iy"]
        x2, y2 = points[-1]["ix"], points[-1]["iy"]
        dx, dy = x2 - x1, y2 - y1
        line_len = (dx * dx + dy * dy) ** 0.5
        max_dist, max_idx = 0.0, 0
        for i in range(1, len(points) - 1):
            px, py = points[i]["ix"], points[i]["iy"]
            if line_len > 0:
                dist = abs(dy * px - dx * py + x2 * y1 - y2 * x1) / line_len
            else:
                dist = ((px - x1) ** 2 + (py - y1) ** 2) ** 0.5
            if dist > max_dist:
                max_dist, max_idx = dist, i
        if max_dist > epsilon:
            left = DrawingController._rdp(points[:max_idx + 1], epsilon)
            right = DrawingController._rdp(points[max_idx:], epsilon)
            return left[:-1] + right
        return [points[0], points[-1]]

    def _get_ts(self, t_str: str) -> float:
        ts = self._ts_cache.get(t_str)
        if ts is None:
            ts = pd.to_datetime(t_str).timestamp()
            self._ts_cache[t_str] = ts
        return ts

    def _idx_to_time(self, idx: int, rt: np.ndarray) -> str:
        if len(rt) == 0:
            return "1970-01-01"
        if len(rt) >= 2:
            if idx >= len(rt) or idx < 0:
                try:
                    if hasattr(self, "_cached_rt") and self._cached_rt is rt and hasattr(self, "_cached_rt_ts"):
                        rt_ts = self._cached_rt_ts
                    else:
                        try:
                            dt_idx = pd.to_datetime(rt)
                            if dt_idx.tz is not None:
                                dt_idx = dt_idx.tz_convert('UTC').tz_localize(None)
                            rt_ts = dt_idx.astype('datetime64[s]').astype(np.int64).to_numpy()
                        except Exception:
                            rt_ts = np.array([pd.to_datetime(x).timestamp() for x in rt])
                        self._cached_rt = rt
                        self._cached_rt_ts = rt_ts

                    dt_sec = rt_ts[-1] - rt_ts[-2]
                    if dt_sec != 0:
                        if idx >= len(rt):
                            delta = idx - (len(rt) - 1)
                            t_ts = rt_ts[-1] + delta * dt_sec
                        else:
                            delta = 0 - idx
                            t_ts = rt_ts[0] - delta * dt_sec
                        return str(pd.to_datetime(t_ts, unit="s"))
                except Exception:
                    pass

        idx = max(0, min(idx, len(rt) - 1))
        return str(pd.to_datetime(rt[idx]))

    def _time_to_idx(self, t_str: str, rt: np.ndarray) -> int:
        if len(rt) == 0:
            return 0
        try:
            t_ts = self._get_ts(t_str)
            if hasattr(self, "_cached_rt") and self._cached_rt is rt and hasattr(self, "_cached_rt_ts"):
                rt_ts = self._cached_rt_ts
            else:
                try:
                    dt_idx = pd.to_datetime(rt)
                    if dt_idx.tz is not None:
                        dt_idx = dt_idx.tz_convert('UTC').tz_localize(None)
                    rt_ts = dt_idx.astype('datetime64[s]').astype(np.int64).to_numpy()
                except Exception:
                    rt_ts = np.array([pd.to_datetime(x).timestamp() for x in rt])
                self._cached_rt = rt
                self._cached_rt_ts = rt_ts

            ts_first = rt_ts[0]
            ts_last = rt_ts[-1]
            if len(rt) >= 2:
                dt_sec = rt_ts[-1] - rt_ts[-2]
                if dt_sec != 0:
                    if t_ts > ts_last:
                        delta_idx = (t_ts - ts_last) / dt_sec
                        return len(rt) - 1 + int(round(delta_idx))
                    elif t_ts < ts_first:
                        delta_idx = (ts_first - t_ts) / dt_sec
                        return -int(round(delta_idx))

            return max(0, int(np.searchsorted(rt_ts, t_ts, side="right")) - 1)
        except Exception:
            try:
                t = pd.to_datetime(t_str).to_datetime64()
                return max(0, int(np.searchsorted(rt, t, side="right")) - 1)
            except Exception:
                return 0

    def _scene_to_view(self, pos) -> QPointF:
        return self._pw.plotItem.vb.mapSceneToView(pos)

    # ------------------------------------------------------------------
    # Mouse event handlers — called from ChartTab
    # Returns True if event was consumed (ChartTab should not process further)
    # ------------------------------------------------------------------

    def handle_mouse_press(
        self,
        scene_pos: QPointF,
        button: Qt.MouseButton,
        rt: np.ndarray,
    ) -> bool:
        if len(rt) == 0 or self.current_tool == "None":
            return False
        if button != Qt.MouseButton.LeftButton:
            return False

        pos = self._scene_to_view(scene_pos)
        x, y = pos.x(), pos.y()
        sx, sy = self._snap(x, y, rt)
        t_str = self._idx_to_time(sx, rt)
        color = self._draw_color.name()
        w = self._draw_width

        if self.current_tool in ("Brush", "Line", "Rect"):
            self._drawing_active = self.current_tool
            self._drawing_start_pt = (sx, sy)
            self._drawing_start_t = t_str
            self._pw.getViewBox().setMouseEnabled(x=False, y=False)

            if self.current_tool == "Brush":
                self._brush_path = QPainterPath(QPointF(x, y))
                self._interactive_item = QGraphicsPathItem(self._brush_path)
            elif self.current_tool == "Line":
                from PySide6.QtWidgets import QGraphicsLineItem
                self._interactive_item = QGraphicsLineItem(sx, sy, sx, sy)
            elif self.current_tool == "Rect":
                from PySide6.QtWidgets import QGraphicsRectItem
                self._interactive_item = QGraphicsRectItem(QRectF(sx, sy, 0, 0))
                fc = QColor(color)
                fc.setAlphaF(0.2)
                self._interactive_item.setBrush(pg.mkBrush(fc))

            self._interactive_item.setPen(pg.mkPen(color, width=w))
            self._pw.addItem(self._interactive_item)
            return True

        # Single-click tools
        if self.current_tool == "HLine":
            self._save_state()
            self.user_drawings.append({"type": "hline", "p": sy, "color": color, "width": w})
            self.save()
        elif self.current_tool == "Marker":
            self._save_state()
            self.user_drawings.append({"type": "marker", "t": t_str, "p": sy, "color": color})
            self.save()
        elif self.current_tool == "Text":
            text, ok = QInputDialog.getText(None, "Add Text", "Enter text:")
            if ok and text:
                self._save_state()
                self.user_drawings.append(
                    {"type": "text", "t": t_str, "p": sy, "text": text, "color": color}
                )
                self.save()
            else:
                return False

        self._deselect_tool_if_needed()
        self.drawings_changed.emit()
        return True

    def handle_mouse_move(self, scene_pos: QPointF, rt: np.ndarray) -> bool:
        if not self._drawing_active:
            return False

        pos = self._scene_to_view(scene_pos)
        sx_curr = max(0, min(int(round(pos.x())), max(0, len(rt) - 1)))
        sy_curr = pos.y()

        if self._drawing_active == "Brush" and self._brush_path is not None:
            self._brush_path.lineTo(pos.x(), pos.y())
            self._interactive_item.setPath(self._brush_path)
        elif self._drawing_active == "Line" and self._interactive_item is not None:
            self._interactive_item.setLine(
                self._drawing_start_pt[0], self._drawing_start_pt[1], sx_curr, sy_curr
            )
        elif self._drawing_active == "Rect" and self._interactive_item is not None:
            x1, y1 = self._drawing_start_pt
            self._interactive_item.setRect(
                QRectF(min(x1, sx_curr), min(y1, sy_curr), abs(sx_curr - x1), abs(sy_curr - y1))
            )
        return True

    def handle_mouse_release(self, scene_pos: QPointF, rt: np.ndarray) -> bool:
        if not self._drawing_active:
            return False

        tool = self._drawing_active
        self._finish_drag(cancelled=False)

        pos = self._scene_to_view(scene_pos)
        sx_curr = max(0, min(int(round(pos.x())), max(0, len(rt) - 1)))
        sy_curr = pos.y()
        t2_str = self._idx_to_time(sx_curr, rt)
        color = self._draw_color.name()
        w = self._draw_width

        if tool == "Brush" and self._brush_path is not None:
            raw_pts = []
            for i in range(self._brush_path.elementCount()):
                el = self._brush_path.elementAt(i)
                raw_pts.append({"t": el.t if hasattr(el, "t") else None, "ix": el.x, "iy": el.y})
            if len(raw_pts) > 1:
                # RDP simplification: epsilon = 0.1% of visible Y range
                epsilon = 1.0
                try:
                    yr = self._pw.getViewBox().viewRange()[1]
                    epsilon = max(1e-9, abs(yr[1] - yr[0]) * 0.001)
                except Exception:
                    pass
                simplified = self._rdp(raw_pts, epsilon)
                pts = [
                    {"t": self._idx_to_time(int(p["ix"]), rt) if len(rt) else "0", "p": p["iy"]}
                    for p in simplified
                ]
                self._save_state()
                self.user_drawings.append(
                    {"type": "brush", "points": pts, "color": color, "width": w}
                )
                self.save()
            self._brush_path = None
        elif tool == "Line":
            self._save_state()
            self.user_drawings.append(
                {
                    "type": "line",
                    "t1": self._drawing_start_t,
                    "p1": self._drawing_start_pt[1],
                    "t2": t2_str,
                    "p2": sy_curr,
                    "color": color,
                    "width": w,
                }
            )
            self.save()
        elif tool == "Rect":
            self._save_state()
            self.user_drawings.append(
                {
                    "type": "rect",
                    "t1": self._drawing_start_t,
                    "p1": self._drawing_start_pt[1],
                    "t2": t2_str,
                    "p2": sy_curr,
                    "color": color,
                    "width": w,
                }
            )
            self.save()

        self._deselect_tool_if_needed()
        self.drawings_changed.emit()
        return True

    def _finish_drag(self, cancelled: bool = False) -> None:
        self._drawing_active = None
        self._pw.getViewBox().setMouseEnabled(x=True, y=True)
        if self._interactive_item is not None:
            try:
                self._pw.removeItem(self._interactive_item)
            except Exception:
                pass
            self._interactive_item = None
        if cancelled:
            self._brush_path = None

    def _deselect_tool_if_needed(self) -> None:
        from PySide6.QtWidgets import QApplication
        if not (QApplication.keyboardModifiers() & Qt.KeyboardModifier.ShiftModifier):
            self.set_tool("None")

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def _clear_items(self) -> None:
        for item in self._graphic_items:
            try:
                # Disconnect signals to prevent reference cycles and memory leaks
                for sig_name in (
                    "sigPositionChanged",
                    "sigPositionChangeFinished",
                    "sigRegionChangeStarted",
                    "sigRegionChangeFinished",
                ):
                    if hasattr(item, sig_name):
                        try:
                            getattr(item, sig_name).disconnect()
                        except Exception:
                            pass

                self._pw.removeItem(item)
                if hasattr(item, "deleteLater"):
                    item.deleteLater()
            except Exception:
                pass
        self._graphic_items.clear()

    def render(self, rt: np.ndarray) -> None:
        # Skip full rebuild when nothing changed and rt is the same object
        if not self._dirty and self._last_rt is rt:
            return

        self._clear_items()
        self._dirty = False
        self._last_rt = rt

        if len(rt) == 0:
            return

        vb = self._pw.getViewBox()

        for d in self.user_drawings:
            try:
                self._render_one(d, rt, vb)
            except Exception as e:
                logger.error("UserDraw render: %s", e)

    def _render_one(self, d: dict, rt: np.ndarray, vb) -> None:
        color = QColor(d.get("color", "#FFFFFF"))
        w = d.get("width", 2)
        dtype = d["type"]
        z = d.get("z", 10)

        if dtype == "hline":
            it = pg.InfiniteLine(
                pos=d["p"],
                angle=0,
                pen=pg.mkPen(color, width=w, style=Qt.PenStyle.DashDotLine),
                movable=True,
                label="{value:.2f}",
                labelOpts={
                    'color': color,
                    'position': 0.98,
                    'anchors': [(1, 1), (1, 1)],
                    'movable': False
                }
            )
            it.d_ref = d
            it.setZValue(z)
            self._pw.addItem(it)
            self._graphic_items.append(it)
            self._attach_context_menu(it, d, rt)
            self._attach_hover_tracking(it)

            # Drag tracking
            it.sigPositionChanged.connect(lambda _: setattr(self, "is_roi_dragging", True))

            # Save state on drag finish
            def _on_hline_dragged_finished(line_item, d_ref=d):
                self.is_roi_dragging = False
                self._save_state()
                d_ref["p"] = float(line_item.value())
                self.save()
                self.drawings_changed.emit()

            it.sigPositionChangeFinished.connect(_on_hline_dragged_finished)

        elif dtype == "marker":
            i1 = self._time_to_idx(d["t"], rt)
            from gui.chart_tab.items import CandlestickItem as _CI  # noqa: F401 type only
            candle_item = next(
                (x for x in self._pw.items() if hasattr(x, "get_ohlc_at")),
                None,
            )
            offset_y = d["p"]
            if candle_item:
                ohlc = candle_item.get_ohlc_at(i1)
                if ohlc:
                    offset_y = max(ohlc) + abs(ohlc[1] - ohlc[2]) * 0.1
            it = pg.TextItem("❗️", color=QColor("#FF1744"), anchor=(0.5, 1))
            it.d_ref = d
            font = it.textItem.font()
            font.setPointSize(18)
            it.textItem.setFont(font)
            it.setPos(i1, offset_y)
            it.setZValue(z)
            self._pw.addItem(it)
            self._graphic_items.append(it)
            self._attach_context_menu(it, d, rt)
            self._attach_hover_tracking(it)

        elif dtype == "text":
            i1 = self._time_to_idx(d["t"], rt)
            it = DraggableTextItem(d["text"], color=color, anchor=(0.5, 0.5), d_ref=d, rt=rt, controller=self)
            font = it.textItem.font()
            font.setPointSize(d.get("size", 12))
            it.textItem.setFont(font)
            it.setPos(i1, d["p"])
            it.setZValue(z)
            self._pw.addItem(it)
            self._graphic_items.append(it)
            self._attach_context_menu(it, d, rt)

        elif dtype == "line":
            i1 = self._time_to_idx(d["t1"], rt)
            i2 = self._time_to_idx(d["t2"], rt)
            it = pg.LineSegmentROI(
                [[i1, d["p1"]], [i2, d["p2"]]],
                pen=pg.mkPen(color, width=w),
                movable=True,
            )
            it.d_ref = d
            it.setAcceptedMouseButtons(Qt.MouseButton.LeftButton | Qt.MouseButton.RightButton)
            self._attach_hover_handles(it)
            it.sigRegionChangeStarted.connect(lambda _: setattr(self, "is_roi_dragging", True))
            it.sigRegionChangeFinished.connect(lambda roi, d_ref=d: self._on_roi_changed(roi, d_ref, rt))
            it.setZValue(z)
            self._pw.addItem(it)
            self._graphic_items.append(it)
            self._attach_context_menu(it, d, rt)

        elif dtype == "rect":
            from gui.chart_tab.items import FilledRectROI
            i1 = self._time_to_idx(d["t1"], rt)
            i2 = self._time_to_idx(d["t2"], rt)
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
            it.d_ref = d
            # Add all 4 corner handles (default RectROI only has 1)
            for hx, hy, cx, cy in [
                (0, 0, 1, 1), (1, 0, 0, 1), (0, 1, 1, 0),
            ]:
                it.addScaleHandle([hx, hy], [cx, cy])
            # Add 4 mid-side handles for edge dragging
            for hx, hy, cx, cy in [
                (0.5, 0, 0.5, 1), (0.5, 1, 0.5, 0),
                (0, 0.5, 1, 0.5), (1, 0.5, 0, 0.5),
            ]:
                it.addScaleHandle([hx, hy], [cx, cy])
            it.setAcceptedMouseButtons(Qt.MouseButton.LeftButton | Qt.MouseButton.RightButton)
            self._attach_hover_handles(it)
            it.sigRegionChangeStarted.connect(lambda _: setattr(self, "is_roi_dragging", True))
            it.sigRegionChangeFinished.connect(lambda roi, d_ref=d: self._on_roi_changed(roi, d_ref, rt))
            it.setZValue(z)
            self._pw.addItem(it)
            self._graphic_items.append(it)
            self._attach_context_menu(it, d, rt)

        elif dtype == "brush":
            pts = d["points"]
            path = QPainterPath()
            for idx, pt in enumerate(pts):
                i_px = self._time_to_idx(pt["t"], rt)
                if idx == 0:
                    path.moveTo(i_px, pt["p"])
                else:
                    path.lineTo(i_px, pt["p"])
            it = DraggablePathItem(path, d, rt, self)
            it.setPen(pg.mkPen(color, width=w))
            it.setZValue(z)
            self._pw.addItem(it)
            self._graphic_items.append(it)
            self._attach_context_menu(it, d, rt)

    # ------------------------------------------------------------------
    # ROI drag-finish callback
    # ------------------------------------------------------------------

    def _on_roi_changed(self, roi, d: dict, rt: np.ndarray) -> None:
        self.is_roi_dragging = False
        if len(rt) == 0:
            return
        try:
            if d["type"] == "rect":
                pos = roi.pos()
                sz = roi.size()
                i1 = int(round(pos.x()))
                i2 = int(round(pos.x() + sz.x()))
                d["t1"] = self._idx_to_time(i1, rt)
                d["p1"] = float(pos.y())
                d["t2"] = self._idx_to_time(i2, rt)
                d["p2"] = float(pos.y() + sz.y())
            elif d["type"] == "line":
                handles = roi.getSceneHandlePositions()
                if len(handles) >= 2:
                    vb = self._pw.getViewBox()
                    vp1 = vb.mapSceneToView(handles[0][1])
                    vp2 = vb.mapSceneToView(handles[1][1])
                    i1 = int(round(vp1.x()))
                    i2 = int(round(vp2.x()))
                    d["t1"] = self._idx_to_time(i1, rt)
                    d["p1"] = float(vp1.y())
                    d["t2"] = self._idx_to_time(i2, rt)
                    d["p2"] = float(vp2.y())
            self.save()
        except Exception as e:
            logger.error("ROI update: %s", e)

    # ------------------------------------------------------------------
    # Per-item context menu (right-click → Delete / Change Color)
    # ------------------------------------------------------------------

    def _attach_hover_tracking(self, item) -> None:
        item.mouseHovering = False
        item.setAcceptHoverEvents(True)
        orig_hover = getattr(item, "hoverEvent", None)
        def _he(ev):
            visible = not ev.isExit()
            item.mouseHovering = visible
            
            if visible:
                d_ref = getattr(item, "d_ref", None)
                dtype = d_ref.get("type") if isinstance(d_ref, dict) else None
                if dtype == "hline":
                    item.setCursor(Qt.CursorShape.SizeVerCursor)
                else:
                    item.setCursor(Qt.CursorShape.PointingHandCursor)
            else:
                item.unsetCursor()

            if orig_hover is not None and (
                hasattr(ev, "acceptDrags") or hasattr(ev, "acceptClicks")
            ):
                orig_hover(ev)
        item.hoverEvent = _he

    @staticmethod
    def _attach_hover_handles(roi) -> None:
        """Hide ROI handles by default; show them only on hover."""
        roi.mouseHovering = False
        for h in roi.handles:
            item = h.get("item")
            if item is not None:
                item.setVisible(False)

        orig_hover = roi.hoverEvent

        def _hover(ev, _orig=orig_hover):
            if hasattr(ev, "acceptDrags") or hasattr(ev, "acceptClicks"):
                _orig(ev)
            visible = not ev.isExit()
            
            roi.mouseHovering = visible
            
            # Set cursor to drag style when hovering over the figure body
            if visible:
                roi.setCursor(Qt.CursorShape.PointingHandCursor)
            else:
                roi.unsetCursor()

            for h in roi.handles:
                item = h.get("item")
                if item is not None:
                    item.setVisible(visible)

        roi.hoverEvent = _hover

    def _show_context_menu(self, item, d: dict, rt: np.ndarray) -> None:
        from PySide6.QtWidgets import QMenu
        from PySide6.QtGui import QCursor

        # Highlight ROI before showing menu
        original_pen = None
        if hasattr(item, "pen"):
            if hasattr(item, "original_pen") and item.original_pen is not None:
                original_pen = item.original_pen
            else:
                original_pen = item.pen
            highlight_color = QColor(d.get("color", "#FFFFFF"))
            highlight_color.setAlphaF(1.0)
            item.setPen(pg.mkPen(highlight_color, width=d.get("width", 2) + 1))
            item.update()

        menu = QMenu()
        edit_act = None
        size_act = None
        if d.get("type") == "text":
            edit_act = menu.addAction("✏️ Edit Text...")
            size_act = menu.addAction("📏 Change Size...")
        
        copy_act = menu.addAction("📋 Copy (Ctrl+C)")
        dup_act = menu.addAction("📂 Duplicate (Ctrl+D)")
        
        paste_act = None
        if self.copied_drawing is not None:
            paste_act = menu.addAction("📥 Paste (Ctrl+V)")

        menu.addSeparator()
        del_act = menu.addAction("🗑 Delete (Del)")
        color_act = menu.addAction("🎨 Change Color...")

        # Submenu for Z-Order in the end of the list
        order_menu = menu.addMenu("🥞 Order")
        front_act = order_menu.addAction("🔼 Bring to Front")
        up_act = order_menu.addAction("⬆️ Step Up")
        down_act = order_menu.addAction("⬇️ Step Down")
        back_act = order_menu.addAction("🔽 Send to Back")

        chosen = menu.exec(QCursor.pos())

        # Restore original pen
        if original_pen is not None:
            item.setPen(original_pen)
            item.update()

        if edit_act is not None and chosen == edit_act:
            self._edit_text_drawing(d, rt)
        elif size_act is not None and chosen == size_act:
            self._change_text_size(d, rt)
        elif chosen == copy_act:
            self.copy_drawing(d)
        elif chosen == dup_act:
            self.duplicate_drawing(d, rt)
        elif paste_act is not None and chosen == paste_act:
            self.paste_drawing(rt)
        elif chosen == front_act:
            self._bring_to_front(d, rt)
        elif chosen == up_act:
            self._step_up(d, rt)
        elif chosen == down_act:
            self._step_down(d, rt)
        elif chosen == back_act:
            self._send_to_back(d, rt)
        elif chosen == del_act:
            self._delete_drawing(d, rt)
        elif chosen == color_act:
            self._change_color(d, rt)

    def _attach_context_menu(self, item, d: dict, rt: np.ndarray) -> None:
        if hasattr(item, "mouseClickEvent"):
            # pyqtgraph items: InfiniteLine, TextItem, LineSegmentROI, RectROI
            orig = item.mouseClickEvent

            def _mce(ev, _orig=orig):
                if ev.button() == Qt.MouseButton.RightButton:
                    self._show_context_menu(item, d, rt)
                    ev.accept()
                else:
                    _orig(ev)

            item.mouseClickEvent = _mce
        else:
            # Qt Graphics items (QGraphicsPathItem for Brush)
            item.contextMenuEvent = lambda ev: (self._show_context_menu(item, d, rt), ev.accept())
            item.setAcceptedMouseButtons(
                Qt.MouseButton.LeftButton | Qt.MouseButton.RightButton
            )

    def _delete_drawing(self, d: dict, rt: np.ndarray) -> None:
        self._save_state()
        try:
            self.user_drawings.remove(d)
        except ValueError:
            pass
        self.save()
        self.render(rt)
        self.drawings_changed.emit()

    def _change_color(self, d: dict, rt: np.ndarray) -> None:
        old_color = QColor(d.get("color", "#FFFFFF"))
        new_color = QColorDialog.getColor(old_color, None, "Drawing Color")
        if new_color.isValid():
            self._save_state()
            d["color"] = new_color.name()
            self.save()
            self.render(rt)
            self.drawings_changed.emit()

    def _edit_text_drawing(self, d: dict, rt: np.ndarray) -> None:
        for item in self._graphic_items:
            if getattr(item, "d_ref", None) is d and isinstance(item, DraggableTextItem):
                item.start_inplace_editing()
                break

    def _change_text_size(self, d: dict, rt: np.ndarray) -> None:
        from PySide6.QtWidgets import QInputDialog
        old_size = d.get("size", 12)
        new_size, ok = QInputDialog.getInt(
            None, "Font Size", "Enter font size (pt):", old_size, 6, 72, 1
        )
        if ok:
            self._save_state()
            d["size"] = new_size
            self.save()
            self._mark_dirty()
            self.render(rt)
            self.drawings_changed.emit()

    def _bring_to_front(self, d: dict, rt: np.ndarray) -> None:
        self._save_state()
        max_z = 10
        for x in self.user_drawings:
            max_z = max(max_z, x.get("z", 10))
        d["z"] = max_z + 1
        self.save()
        self._mark_dirty()
        self.render(rt)
        self.drawings_changed.emit()

    def _send_to_back(self, d: dict, rt: np.ndarray) -> None:
        self._save_state()
        min_z = 10
        for x in self.user_drawings:
            min_z = min(min_z, x.get("z", 10))
        d["z"] = min_z - 1
        self.save()
        self._mark_dirty()
        self.render(rt)
        self.drawings_changed.emit()

    def _normalize_z_values(self) -> list:
        sorted_drawings = sorted(self.user_drawings, key=lambda x: x.get("z", 10))
        for idx, x in enumerate(sorted_drawings):
            x["z"] = 10 + idx
        return sorted_drawings

    def _step_up(self, d: dict, rt: np.ndarray) -> None:
        self._save_state()
        sorted_drawings = self._normalize_z_values()
        try:
            idx = sorted_drawings.index(d)
            if idx < len(sorted_drawings) - 1:
                above_item = sorted_drawings[idx + 1]
                d["z"], above_item["z"] = above_item["z"], d["z"]
        except ValueError:
            pass
        self.save()
        self._mark_dirty()
        self.render(rt)
        self.drawings_changed.emit()

    def _step_down(self, d: dict, rt: np.ndarray) -> None:
        self._save_state()
        sorted_drawings = self._normalize_z_values()
        try:
            idx = sorted_drawings.index(d)
            if idx > 0:
                below_item = sorted_drawings[idx - 1]
                d["z"], below_item["z"] = below_item["z"], d["z"]
        except ValueError:
            pass
        self.save()
        self._mark_dirty()
        self.render(rt)
        self.drawings_changed.emit()

    # ------------------------------------------------------------------
    # Copy, Paste & Duplicate Logic
    # ------------------------------------------------------------------

    def copy_drawing(self, d: dict) -> None:
        import copy
        self.copied_drawing = copy.deepcopy(d)
        logger.info("📋 Copied drawing: %s", self.copied_drawing.get("type"))

    def paste_drawing(self, rt: np.ndarray) -> None:
        if self.copied_drawing is None:
            logger.info("Nothing to paste")
            return

        import copy
        new_d = copy.deepcopy(self.copied_drawing)
        self._offset_drawing_coordinates(new_d, rt)

        self._save_state()
        self.user_drawings.append(new_d)
        self.save()

        self.render(rt)
        self.drawings_changed.emit()
        logger.info("📥 Pasted drawing: %s", new_d.get("type"))

    def duplicate_drawing(self, d: dict, rt: np.ndarray) -> None:
        import copy
        new_d = copy.deepcopy(d)
        self._offset_drawing_coordinates(new_d, rt)

        self._save_state()
        self.user_drawings.append(new_d)
        self.save()

        self.render(rt)
        self.drawings_changed.emit()
        logger.info("📂 Duplicated drawing: %s", new_d.get("type"))

    def _offset_drawing_coordinates(self, d: dict, rt: np.ndarray) -> None:
        y_offset = 0.0
        try:
            yr = self._pw.getViewBox().viewRange()[1]
            y_offset = abs(yr[1] - yr[0]) * 0.03  # 3% of visible Y range
        except Exception:
            if "p" in d:
                y_offset = d["p"] * 0.01
            elif "p1" in d:
                y_offset = d["p1"] * 0.01
            else:
                y_offset = 1.0

        shift_bars = 5
        dtype = d.get("type")
        if dtype == "hline":
            d["p"] = d["p"] + y_offset
        elif dtype in ("marker", "text"):
            if "t" in d:
                idx = self._time_to_idx(d["t"], rt)
                d["t"] = self._idx_to_time(idx + shift_bars, rt)
            if "p" in d:
                d["p"] = d["p"] + y_offset
        elif dtype in ("line", "rect"):
            if "t1" in d:
                idx1 = self._time_to_idx(d["t1"], rt)
                d["t1"] = self._idx_to_time(idx1 + shift_bars, rt)
            if "t2" in d:
                idx2 = self._time_to_idx(d["t2"], rt)
                d["t2"] = self._idx_to_time(idx2 + shift_bars, rt)
            if "p1" in d:
                d["p1"] = d["p1"] + y_offset
            if "p2" in d:
                d["p2"] = d["p2"] + y_offset
        elif dtype == "brush":
            pts = d.get("points", [])
            new_pts = []
            for pt in pts:
                idx = self._time_to_idx(pt["t"], rt)
                new_t = self._idx_to_time(idx + shift_bars, rt)
                new_p = pt["p"] + y_offset
                new_pts.append({"t": new_t, "p": new_p})
            d["points"] = new_pts
