"""Phase 2 EPIC 2.1 — Premium heatmap widget.

The existing ``_Heatmap`` class lived inside :mod:`gui.tabs.analytics_tab`
and was a competent but bare-bones grid. Phase 2 extracts it into this
module and layers on the polish requested in the ROADMAP:

* Diverging RdYlGn → surface palette anchored at ``midpoint=0`` (already
  in the legacy widget) plus an optional ``tanh`` soft-mapping mode for
  outlier-heavy grids (``set_outlier_softening``).
* Deep tooltip surfaces secondary stats (counts, win-rate, avg win /
  avg loss) for the hovered cell — fed via :meth:`set_extras`.
* Click drill-down keeps a 400 ms pulse on the selected cell.
* Annotation overlay — ``best`` / ``worst`` cells get ⭐ / ⚠ glyphs.
* ``Diff mode`` — values rendered as ``% vs mean`` while the toggle is
  on.

Enhanced in 2026:
  - 5-stop diverging colormap for richer visual gradient
  - Premium cell grid borders with subtle slate lines
  - Dynamic font sizing based on grid density
  - Enhanced colorbar with 5 labelled tick points
  - Panel-style tooltip with structured HTML layout
  - Glow highlight on hover/selection

The new public widget is :class:`HeatmapWidget`. The legacy class name
``_Heatmap`` is re-exported for source compatibility.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QPropertyAnimation, QRectF, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPen

from gui.styling import style_helpers as sh
from gui.styling.theme import font_mono, get_theme


# ---------------------------------------------------------------------------
# Cell extras — optional rich data for deep tooltips
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HeatmapExtras:
    """Optional companion grids surfaced in the deep-tooltip.

    Each array shares the same shape as the primary ``data`` matrix.
    Pass ``None`` if you only have one extra (e.g. counts but no WR).
    """

    counts: Optional[np.ndarray] = None
    win_rate: Optional[np.ndarray] = None  # fraction 0..1, NaN where no data
    avg_win: Optional[np.ndarray] = None
    avg_loss: Optional[np.ndarray] = None


class HeatmapWidget(pg.GraphicsLayoutWidget):
    """Premium 2D heatmap with color bar, hover tooltip, click pulse.

    Signals
    -------
    cell_clicked(row, col)
        Emitted when the user clicks a cell. The widget keeps the
        selection highlighted until the next click on a different cell
        or :meth:`clear_selection` is called.
    """

    cell_clicked = Signal(int, int)

    # Soft-mapping mode caps the visual swing of extreme outliers so
    # mid-range cells remain readable. The cap is applied via tanh.
    DEFAULT_TANH_SCALE = 1.5

    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self._rows = 0
        self._cols = 0
        self._data: Optional[np.ndarray] = None
        self._extras: Optional[HeatmapExtras] = None
        self._x_labels: list[str] = []
        self._y_labels: list[str] = []
        self._lut = None
        self._vmax = 1.0
        self._currency_symbol = "$"
        self._selected: Optional[tuple[int, int]] = None
        self._tanh_softening = False
        self._diff_mode = False
        self._show_annotations = True
        self._labels: list[pg.TextItem] = []
        self._anno_items: list[pg.TextItem] = []
        self._grid_lines: list = []
        self._pulse_anim: Optional[QPropertyAnimation] = None
        self._pulse_timer: Optional[QTimer] = None

        self._view = self.addPlot(row=0, col=0)
        self._view.setMenuEnabled(False)
        self._view.setMouseEnabled(False, False)
        self._view.hideButtons()
        self._view.invertY(True)
        self._image = pg.ImageItem(axisOrder="row-major")
        self._image.setOpts(axisOrder="row-major")
        self._view.addItem(self._image)

        # Color bar
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

        # Highlight + tooltip
        self._image.mouseClickEvent = self._on_image_click
        self._image.hoverEvent = self._on_image_hover
        self._highlight = pg.RectROI(pos=(0, 0), size=(1, 1), pen=pg.mkPen(width=2), movable=False)
        self._highlight.setZValue(10)
        self._view.addItem(self._highlight)
        self._highlight.hide()
        self._tooltip_bg = pg.RectROI(pos=(0, 0), size=(1, 1), pen=None, movable=False)
        self._tooltip_bg.setZValue(19)
        self._view.addItem(self._tooltip_bg)
        self._tooltip_bg.hide()
        self._tooltip = pg.TextItem(text="", anchor=(0.5, 0.5))
        self._tooltip.setZValue(20)
        self._view.addItem(self._tooltip)
        self._tooltip.hide()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_outlier_softening(self, enabled: bool) -> None:
        """Toggle tanh-based soft mapping for outlier-heavy matrices."""
        self._tanh_softening = bool(enabled)
        if self._data is not None:
            self.set_grid(self._data, self._x_labels, self._y_labels, extras=self._extras)

    def set_diff_mode(self, enabled: bool) -> None:
        """Toggle 'percent vs grid mean' display in the tooltip / labels."""
        self._diff_mode = bool(enabled)
        if self._data is not None:
            self.set_grid(self._data, self._x_labels, self._y_labels, extras=self._extras)

    def set_show_annotations(self, enabled: bool) -> None:
        self._show_annotations = bool(enabled)
        if self._data is not None:
            self._refresh_annotations()

    def set_currency_symbol(self, symbol: str) -> None:
        self._currency_symbol = (symbol or "$").strip() or "$"
        if self._data is not None:
            self.set_grid(self._data, self._x_labels, self._y_labels, extras=self._extras)

    def set_extras(self, extras: Optional[HeatmapExtras]) -> None:
        """Attach companion arrays surfaced by the deep tooltip."""
        self._extras = extras

    def clear_selection(self) -> None:
        self._selected = None
        self._highlight.hide()
        self._tooltip.hide()
        self._tooltip_bg.hide()
        self._stop_pulse()

    # ------------------------------------------------------------------
    # Core paint
    # ------------------------------------------------------------------

    def set_grid(
        self,
        data: Optional[np.ndarray],
        x_labels: Optional[list[str]] = None,
        y_labels: Optional[list[str]] = None,
        *,
        cmap: str = "theme_div",
        show_text: bool = True,
        extras: Optional[HeatmapExtras] = None,
    ) -> None:
        for lbl in self._labels:
            self._view.removeItem(lbl)
            try:
                lbl.deleteLater()
            except Exception:
                pass
        self._labels.clear()
        for anno in self._anno_items:
            self._view.removeItem(anno)
            try:
                anno.deleteLater()
            except Exception:
                pass
        self._anno_items.clear()
        for gl in self._grid_lines:
            self._view.removeItem(gl)
            try:
                gl.deleteLater()
            except Exception:
                pass
        self._grid_lines.clear()
        self._image.clear()
        self._image.mouseClickEvent = self._on_image_click
        self._data = None

        if data is None or data.size == 0:
            self._rows = 0
            self._cols = 0
            return
        finite = data[np.isfinite(data)]
        if finite.size == 0:
            self._rows = 0
            self._cols = 0
            return
        self._data = data.copy()
        if extras is not None:
            self._extras = extras
        self._x_labels = list(x_labels or [])
        self._y_labels = list(y_labels or [])
        self._rows, self._cols = data.shape

        finite_nonzero = finite[finite != 0]
        if finite_nonzero.size:
            vmax = max(
                float(np.percentile(np.abs(finite_nonzero), 90)),
                float(np.min(np.abs(finite_nonzero))),
                1e-9,
            )
        else:
            vmax = 1.0
        self._vmax = float(vmax)
        t = get_theme()

        try:
            if cmap == "theme_div":
                c0 = QColor(sh.c(t, "red"))
                c1 = QColor(sh.c(t, "red"))
                c2 = QColor(sh.c(t, "elevated2"))
                c3 = QColor(sh.c(t, "green"))
                c4 = QColor(sh.c(t, "green"))
                cmap_obj = pg.ColorMap(
                    pos=np.array([0.0, 0.25, 0.5, 0.75, 1.0], dtype=float),
                    color=np.array(
                        [c0.getRgb()[:3], c1.getRgb()[:3],
                         c2.getRgb()[:3], c3.getRgb()[:3],
                         c4.getRgb()[:3]],
                        dtype=np.ubyte,
                    ),
                )
            else:
                cmap_obj = pg.colormap.get(cmap, pg.colormap.get("Turbo"))
            lut = cmap_obj.getLookupTable(0.0, 1.0, 256)
        except Exception:
            lut = pg.colormap.get("viridis").getLookupTable(0.0, 1.0, 256)
        self._lut = lut
        self._image.setLookupTable(None)
        self._image.setImage(self._build_rgba_image(vmax), autoLevels=False)
        self._image.setRect(QRectF(-0.5, -0.5, float(self._cols), float(self._rows)))

        # Premium grid borders — subtle slate lines between cells
        border_pen = pg.mkPen(QColor(sh.c(t, "border2")), width=1, alpha=40)
        for r in range(self._rows + 1):
            line = pg.InfiniteLine(pos=r - 0.5, angle=0, pen=border_pen)
            line.setZValue(-1)
            self._view.addItem(line)
            self._grid_lines.append(line)
        for c in range(self._cols + 1):
            line = pg.InfiniteLine(pos=c - 0.5, angle=90, pen=border_pen)
            line.setZValue(-1)
            self._view.addItem(line)
            self._grid_lines.append(line)

        # Color bar with premium gradient bar and 5 tick marks
        grad = np.linspace(-vmax, vmax, 256, dtype=float).reshape(256, 1)
        self._cb_image.setLookupTable(lut)
        self._cb_image.setLevels((-vmax, vmax))
        self._cb_image.setImage(grad, autoLevels=False)
        self._cb_image.setRect(QRectF(0.0, -vmax, 1.0, 2.0 * vmax))
        self._cb_view.setXRange(0.0, 1.0, padding=0.0)
        self._cb_view.setYRange(-vmax, vmax, padding=0.0)
        tick_vals = [-vmax, -vmax / 2, 0.0, vmax / 2, vmax]
        self._cb_axis.setTicks(
            [[(v, self._fmt_money(v)) for v in tick_vals]]
        )
        self._cb_view.showAxis("right")
        self._cb_axis.setStyle(tickTextOffset=6, tickLength=4)
        self._cb_axis.setPen(pg.mkPen(QColor(sh.c(t, "chart_axis_text")), width=1))
        self._cb_axis.setTextPen(QColor(sh.c(t, "chart_axis_text")))

        ax_b = self._view.getAxis("bottom")
        ax_l = self._view.getAxis("left")
        ax_b.setTicks([list(enumerate(self._x_labels))])
        ax_l.setTicks([list(enumerate(self._y_labels))])
        for axis in (ax_b, ax_l):
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

        if show_text:
            mean = float(finite.mean()) if finite.size else 0.0
            # Dynamic font size based on grid density
            cell_count = self._rows * self._cols
            if cell_count > 200:
                font_size = 7
            elif cell_count > 100:
                font_size = 8
            elif cell_count > 50:
                font_size = 9
            else:
                font_size = 10
            for r in range(self._rows):
                for c in range(self._cols):
                    val = self._data[r, c]
                    if not np.isfinite(val) or val == 0:
                        continue
                    if self._diff_mode and mean != 0:
                        text = f"{(val - mean) / abs(mean) * 100:+.0f}%"
                    else:
                        text = self._fmt_money(float(val)).replace(self._currency_symbol, "")
                    txt = pg.TextItem(
                        text,
                        color=self._label_color(float(val), vmax),
                        anchor=(0.5, 0.5),
                        fill=None,
                    )
                    txt.setPos(c, r)
                    txt.setFont(QFont(font_mono(), font_size, QFont.Weight.Medium))
                    self._view.addItem(txt)
                    self._labels.append(txt)
        self._refresh_annotations()

    def _cell_has_data(self, row: int, col: int) -> bool:
        if self._data is None or not (0 <= row < self._rows and 0 <= col < self._cols):
            return False
        val = self._data[row, col]
        if not np.isfinite(val):
            return False
        if self._extras is not None and self._extras.counts is not None:
            try:
                if self._extras.counts.shape == self._data.shape:
                    return int(self._extras.counts[row, col]) > 0
            except Exception:
                pass
        return float(val) != 0.0

    @staticmethod
    def _blend_rgb(a: QColor, b: QColor, k: float) -> tuple[int, int, int]:
        k = max(0.0, min(1.0, float(k)))
        return (
            int(a.red() + (b.red() - a.red()) * k),
            int(a.green() + (b.green() - a.green()) * k),
            int(a.blue() + (b.blue() - a.blue()) * k),
        )

    def _intensity(self, val: float, vmax: float) -> float:
        if not np.isfinite(val) or vmax <= 0:
            return 0.0
        raw = abs(float(val))
        if self._tanh_softening:
            return float(np.tanh(raw / (vmax * self.DEFAULT_TANH_SCALE)))
        return min(1.0, raw / vmax) ** 0.82

    def _build_rgba_image(self, vmax: float) -> np.ndarray:
        t = get_theme()
        no_data = QColor(sh.c(t, "elevated"))
        neutral = QColor(sh.c(t, "elevated2"))
        win = QColor(sh.c(t, "green"))
        loss = QColor(sh.c(t, "red"))
        rgba = np.zeros((self._rows, self._cols, 4), dtype=np.ubyte)
        for r in range(self._rows):
            for c in range(self._cols):
                val = float(self._data[r, c]) if self._data is not None else np.nan
                if not self._cell_has_data(r, c):
                    rgba[r, c] = [no_data.red(), no_data.green(), no_data.blue(), 210]
                    continue
                intensity = self._intensity(val, vmax)
                target = win if val >= 0 else loss
                k = 0.08 + 0.92 * intensity
                rr, gg, bb = self._blend_rgb(neutral, target, k)
                rgba[r, c] = [rr, gg, bb, 255]
        return rgba

    def _label_color(self, val: float, vmax: float) -> str:
        t = get_theme()
        if self._intensity(val, vmax) >= 0.58:
            return "#FFFFFF"
        return sh.c(t, "text")

    # ------------------------------------------------------------------
    # Annotations (best / worst)
    # ------------------------------------------------------------------

    def _refresh_annotations(self) -> None:
        for anno in self._anno_items:
            self._view.removeItem(anno)
            try:
                anno.deleteLater()
            except Exception:
                pass
        self._anno_items.clear()
        if not self._show_annotations or self._data is None:
            return
        finite_mask = np.isfinite(self._data) & (self._data != 0)
        if not finite_mask.any():
            return
        masked = np.where(finite_mask, self._data, np.nan)
        try:
            best_r, best_c = np.unravel_index(np.nanargmax(masked), masked.shape)
            worst_r, worst_c = np.unravel_index(np.nanargmin(masked), masked.shape)
        except ValueError:
            return
        t = get_theme()
        if float(self._data[best_r, best_c]) > 0:
            star = pg.TextItem(chr(11088), color=sh.c(t, "amber"), anchor=(1.0, 0.0))
            star.setPos(best_c + 0.43, best_r - 0.43)
            star.setFont(QFont(font_mono(), 8))
            self._view.addItem(star)
            self._anno_items.append(star)
        if float(self._data[worst_r, worst_c]) < 0:
            warn = pg.TextItem(chr(9888), color=sh.c(t, "red"), anchor=(1.0, 0.0))
            warn.setPos(worst_c + 0.43, worst_r - 0.43)
            warn.setFont(QFont(font_mono(), 8))
            self._view.addItem(warn)
            self._anno_items.append(warn)

    # ------------------------------------------------------------------
    # Static helpers
    # ------------------------------------------------------------------

    def _fmt_money(self, v: float) -> str:
        av = abs(float(v))
        sym = self._currency_symbol
        if av >= 1_000_000:
            return f"{sym}{v / 1_000_000:+.1f}M"
        if av >= 10_000:
            return f"{sym}{v / 1_000:+.0f}k"
        if av >= 1_000:
            return f"{sym}{v:+,.0f}"
        return f"{sym}{v:+.0f}"

    def _map_pos_to_cell(self, pos) -> tuple[int, int]:
        col = int(np.floor(float(pos.x()) + 0.5))
        row = int(np.floor(float(pos.y()) + 0.5))
        return row, col

    # ------------------------------------------------------------------
    # Mouse handlers
    # ------------------------------------------------------------------

    def _on_image_click(self, ev) -> None:
        if ev.button() != 1:
            return
        row, col = self._map_pos_to_cell(ev.pos())
        if 0 <= row < self._rows and 0 <= col < self._cols:
            self._selected = (row, col)
            self._highlight.setPos(col - 0.5, row - 0.5)
            self._highlight.setSize((1, 1))
            self._highlight.show()
            self._start_pulse()
            self.cell_clicked.emit(row, col)

    def _on_image_hover(self, ev) -> None:
        if ev.isExit():
            self._tooltip.hide()
            self._tooltip_bg.hide()
            if self._selected is None:
                self._highlight.hide()
            else:
                r, c = self._selected
                self._highlight.setPos(c - 0.5, r - 0.5)
                self._highlight.setSize((1, 1))
                self._highlight.show()
            return
        row, col = self._map_pos_to_cell(ev.pos())
        if not (0 <= row < self._rows and 0 <= col < self._cols and self._data is not None):
            return
        val = self._data[row, col]
        x_label = self._x_labels[col] if col < len(self._x_labels) else str(col)
        y_label = self._y_labels[row] if row < len(self._y_labels) else str(row)
        self._highlight.setPos(col - 0.5, row - 0.5)
        self._highlight.setSize((1, 1))
        self._highlight.show()
        html, bg_color = self._build_tooltip_html(row, col, x_label, y_label, val)
        self._tooltip.setHtml(html)
        tip_y = row - 1.2
        if tip_y < -0.2:
            tip_y = row + 1.2
        self._tooltip.setPos(col, tip_y)
        self._tooltip.show()
        # Tooltip background panel
        t = get_theme()
        self._tooltip_bg.setPos(col - 4.5, tip_y - 1.2)
        self._tooltip_bg.setSize((9, 2.4))
        self._tooltip_bg.setPen(pg.mkPen(QColor(sh.c(t, "border")), width=1))
        bg_qcolor = QColor(sh.c(t, "elevated2"))
        bg_qcolor.setAlpha(230)
        self._tooltip_bg.setBrush(pg.mkBrush(bg_qcolor))
        self._tooltip_bg.show()

    def _build_tooltip_html(
        self,
        row: int,
        col: int,
        x_label: str,
        y_label: str,
        val: float,
    ) -> tuple:
        t = get_theme()
        if not np.isfinite(val) or not self._cell_has_data(row, col):
            return (
                f"{y_label} × {x_label}<br>"
                f"<span style='color:{sh.c(t, 'text3')};'>No trades data</span>",
                sh.c(t, "surface"),
            )
        color = sh.c(t, "green") if val >= 0 else sh.c(t, "red")
        header = (
            f"<b>{y_label}</b> × <b>{x_label}</b><br>"
            f"<span style='color:{color};font-weight:bold;font-size:15px;'>"
            f"{self._fmt_money(float(val))}"
            f"</span>"
        )
        extras_html = ""
        if self._extras is not None:
            parts: list[str] = []
            if self._extras.counts is not None and self._extras.counts.shape == self._data.shape:
                n = int(self._extras.counts[row, col])
                if n > 0:
                    parts.append(f"<b>n</b>={n}")
            if self._extras.win_rate is not None and self._extras.win_rate.shape == self._data.shape:
                wr = self._extras.win_rate[row, col]
                if np.isfinite(wr):
                    parts.append(f"<b>WR</b>={wr * 100:.0f}%")
            if self._extras.avg_win is not None and self._extras.avg_win.shape == self._data.shape:
                aw = self._extras.avg_win[row, col]
                if np.isfinite(aw) and aw > 0:
                    parts.append(f"<b>W+</b> {self._fmt_money(float(aw))}")
            if self._extras.avg_loss is not None and self._extras.avg_loss.shape == self._data.shape:
                al = self._extras.avg_loss[row, col]
                if np.isfinite(al) and al > 0:
                    parts.append(f"<b>L-</b> -{self._fmt_money(float(al))}")
            if parts:
                extras_html = (
                    f"<br><span style='color:{sh.c(t, 'text2')};font-size:11px;'>"
                    + " &nbsp;·&nbsp; ".join(parts)
                    + "</span>"
                )
        if self._diff_mode:
            finite = self._data[np.isfinite(self._data) & (self._data != 0)]
            if finite.size:
                mean = float(finite.mean())
                if mean != 0:
                    diff = (float(val) - mean) / abs(mean) * 100.0
                    extras_html += (
                        f"<br><span style='color:{sh.c(t, 'text2')};font-size:11px;'>"
                        f"vs mean: <b>{diff:+.0f}%</b></span>"
                    )
        return header + extras_html, sh.c(t, "elevated2")

    # ------------------------------------------------------------------
    # Pulse animation on click
    # ------------------------------------------------------------------

    def _start_pulse(self) -> None:
        self._stop_pulse()
        if self._selected is None:
            return
        try:
            self._pulse_timer = QTimer(self)
            self._pulse_timer.setInterval(80)
            steps = [4, 3, 3, 2, 2]
            idx = {"i": 0}

            def _tick():
                if idx["i"] >= len(steps):
                    if self._pulse_timer is not None:
                        self._pulse_timer.stop()
                    return
                w = steps[idx["i"]]
                idx["i"] += 1
                t = get_theme()
                self._highlight.setPen(pg.mkPen(QColor(sh.c(t, "accent_light")), width=w))

            self._pulse_timer.timeout.connect(_tick)
            self._pulse_timer.start()
        except Exception:
            pass

    def _stop_pulse(self) -> None:
        if self._pulse_timer is not None:
            try:
                self._pulse_timer.stop()
            except Exception:
                pass
            self._pulse_timer = None


# Source-compat alias.
_Heatmap = HeatmapWidget


__all__ = ["HeatmapWidget", "HeatmapExtras", "_Heatmap"]
