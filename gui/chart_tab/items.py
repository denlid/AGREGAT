"""Custom pyqtgraph items used by the chart tab — candles and filled ROIs.

Phase 2 (`optimization_roadmap.md` §Sprint 2) — :class:`CandlestickItem`
implements the three rendering optimisations the roadmap calls for:

* **Viewport culling** — every :py:meth:`_gen` consults the linked
  ViewBox's current x-range and uses :func:`numpy.searchsorted` on a
  cached, sorted x-array to clip the painter loop to the bars
  intersecting the viewport (± a one-bar margin so a wick clipped at
  the edge still draws). When the user is zoomed in to 80 visible
  bars out of a 2 000-bar post-LOD set, ``_gen`` walks 80 bars —
  not 2 000.

* **Batched QPainter** — the painter loop is grouped by bull / bear /
  doji and emits a single :py:meth:`QPainter.drawLines` call per group
  for the wicks plus a single :py:meth:`QPainter.drawRects` call per
  group for the bodies. Replaces the previous ``4 × N`` per-bar calls
  with a constant ~6 batch draws, which is the main "TradingView-level"
  win from the roadmap.

* **LOD** — :func:`gui.chart_tab.lod.downsample_ohlc` is applied at
  :py:meth:`set_data` time to cap the post-LOD data set, so even a
  fully zoomed-out viewport never paints more than ``max_bars`` bars
  regardless of source size.
"""

from __future__ import annotations

from typing import List, Tuple

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QLineF, QRectF, QTimer
from PySide6.QtGui import QColor, QPainter, QPicture

from gui.chart_tab.lod import DEFAULT_MAX_BARS, downsample_ohlc

# Phase 2.3 — repaint debounce. Pyqtgraph emits ``sigRangeChanged``
# multiple times during a single drag; coalescing into one repaint per
# 50 ms keeps the UI thread idle between user gestures.
_RANGE_DEBOUNCE_MS = 50

# Phase 2.1 — viewport cull margin (in data-x units). Extended by one
# full bar on each side so a wick that crosses the viewport edge still
# paints. The constant is intentionally generous — at most ~2 extra
# bars per repaint, negligible cost.
_CULL_MARGIN_BARS = 1.0


class CandlestickItem(pg.GraphicsObject):
    """High-performance candlestick renderer.

    Redraws on every viewRange change so candle width always matches
    zoom level. When more than :data:`gui.chart_tab.lod.DEFAULT_MAX_BARS`
    candles are passed in, the geometry is collapsed via
    :func:`gui.chart_tab.lod.downsample_ohlc` so the QPainter loop in
    :py:meth:`_gen` stays bounded regardless of source size.
    """

    def __init__(self, max_bars: int = DEFAULT_MAX_BARS) -> None:
        super().__init__()
        self.data: List[Tuple[float, float, float, float, float]] = []
        # Post-LOD data. Tests inspect this attribute directly so we
        # keep it as a Python list of 5-tuples for backward compat.
        self._raw_data: list = []  # caller-supplied data (full resolution)
        self._max_bars = int(max_bars)
        self._ohlc_map: dict = {}
        # Phase 2.1 — parallel ``(N, 5)`` float64 NumPy cache built
        # from ``self.data`` at :py:meth:`set_data`. Used for
        # ``np.searchsorted``-driven viewport culling in :py:meth:`_gen`.
        # Kept in sync with ``self.data``; reset to an empty array
        # whenever ``self.data`` is empty.
        self._arr: np.ndarray = np.empty((0, 5), dtype=np.float64)
        self.picture = QPicture()
        self.bull_color = "#26A69A"
        self.bear_color = "#EF5350"
        self._last_w = 0.35  # cached candle half-width in data coords
        self._view = None
        # Single-shot debounce timer for ``sigRangeChanged`` repaints.
        # Created lazily on the first attach so headless callers (unit
        # tests without QApplication) don't hit a Qt event loop.
        self._range_timer = None
        # Phase 2.3 — telemetry counters consumed by the headless
        # tests. The production paint path doesn't read these; they
        # only exist so batching guarantees can be asserted in CI.
        self._paint_stats = {
            "draw_lines_calls": 0,
            "draw_rects_calls": 0,
            "bars_drawn": 0,
            "bars_culled": 0,
        }

    # ------------------------------------------------------------------
    # ViewBox wiring (Phase 2.1)
    # ------------------------------------------------------------------
    def set_view(self, vb) -> None:  # type: ignore[no-untyped-def]
        """Link to ViewBox so we can query current x-range for dynamic width."""
        self._view = vb
        if self._range_timer is None:
            self._range_timer = QTimer()
            self._range_timer.setSingleShot(True)
            self._range_timer.setInterval(_RANGE_DEBOUNCE_MS)
            self._range_timer.timeout.connect(self._do_range_repaint)
        vb.sigRangeChanged.connect(self._on_range_changed)

    def _on_range_changed(self, vb, ranges) -> None:  # type: ignore[no-untyped-def]
        if not self.data:
            return
        # Coalesce bursts of range changes into a single repaint.
        if self._range_timer is not None:
            self._range_timer.start()
        else:
            self._do_range_repaint()

    def _do_range_repaint(self) -> None:
        if not self.data:
            return
        self._update_width()
        self._gen()
        self.update()

    def _update_width(self) -> None:
        """Compute candle half-width as ~40% of the per-candle pixel slot."""
        if self._view is None or not self.data:
            self._last_w = 0.35
            return
        xr = self._view.viewRange()[0]
        visible = max(1, xr[1] - xr[0])
        self._last_w = max(0.05, min(0.45, 0.40))
        if visible < 20:
            self._last_w = 0.42
        elif visible < 60:
            self._last_w = 0.40
        elif visible < 150:
            self._last_w = 0.38
        else:
            self._last_w = 0.35

    # ------------------------------------------------------------------
    # Data ingest
    # ------------------------------------------------------------------
    def set_data(self, data) -> None:  # type: ignore[no-untyped-def]
        # Keep the full-resolution data for OHLC tooltip lookups; the
        # downsampled ``self.data`` is what the QPainter loop iterates.
        self._raw_data = list(data) if not isinstance(data, list) else data
        if len(self._raw_data) > self._max_bars:
            self.data = downsample_ohlc(self._raw_data, self._max_bars)
        else:
            self.data = self._raw_data
        # Tooltip / hover lookups still resolve to the original
        # full-resolution OHLC values.
        self._ohlc_map = {
            int(round(t)): (o, hi, lo, c) for (t, o, c, lo, hi) in self._raw_data
        }
        # Phase 2.1 — parallel NumPy view of ``self.data`` for fast
        # viewport culling. One ``np.asarray`` per set_data is cheap
        # (~2 ms / 10K bars) and amortises across every subsequent
        # range-change repaint.
        if self.data:
            self._arr = np.asarray(self.data, dtype=np.float64)
        else:
            self._arr = np.empty((0, 5), dtype=np.float64)
        self._update_width()
        self._gen()
        self.informViewBoundsChanged()
        self.update()

    def set_max_bars(self, max_bars: int) -> None:
        """Change the LOD cap and re-apply downsampling to the current
        data set in-place."""
        self._max_bars = max(1, int(max_bars))
        if self._raw_data:
            self.set_data(self._raw_data)

    def get_ohlc_at(self, idx):  # type: ignore[no-untyped-def]
        return self._ohlc_map.get(int(idx))

    # ------------------------------------------------------------------
    # Phase 2.1 — viewport culling
    # ------------------------------------------------------------------
    def _visible_slice(self) -> Tuple[int, int]:
        """Return ``(lo, hi)`` indices into :pyattr:`_arr` that fall in
        the current ViewBox x-range, extended by a one-bar margin so
        wicks at the edge still paint.

        Falls back to the full range when no ViewBox is linked or the
        cached array is empty.
        """
        n = self._arr.shape[0]
        if n == 0:
            return 0, 0
        if self._view is None:
            return 0, n
        try:
            xr = self._view.viewRange()[0]
        except Exception:
            return 0, n
        # ``self._arr[:, 0]`` is the idx column. It's guaranteed
        # sorted by ``downsample_ohlc`` (bin-order) and by the caller
        # (chart_tab always feeds a monotonic ``range(n)`` — same
        # contract pyqtgraph's PlotCurveItem assumes for setData(x=)).
        xs = self._arr[:, 0]
        x_min = float(xr[0]) - _CULL_MARGIN_BARS
        x_max = float(xr[1]) + _CULL_MARGIN_BARS
        lo = int(np.searchsorted(xs, x_min, side="left"))
        hi = int(np.searchsorted(xs, x_max, side="right"))
        return lo, hi

    # ------------------------------------------------------------------
    # Phase 2.3 — batched painter
    # ------------------------------------------------------------------
    def _gen(self) -> None:
        self.picture = QPicture()
        self._paint_stats["draw_lines_calls"] = 0
        self._paint_stats["draw_rects_calls"] = 0
        self._paint_stats["bars_drawn"] = 0
        self._paint_stats["bars_culled"] = 0
        n_total = self._arr.shape[0]
        if n_total == 0:
            return

        # Phase 2.1 — clip to visible window once per repaint.
        lo, hi = self._visible_slice()
        self._paint_stats["bars_culled"] = n_total - (hi - lo)
        if hi <= lo:
            return
        visible = self._arr[lo:hi]
        self._paint_stats["bars_drawn"] = visible.shape[0]

        # Columns: 0=idx, 1=open, 2=close, 3=low, 4=high.
        idx = visible[:, 0]
        op = visible[:, 1]
        cl = visible[:, 2]
        low = visible[:, 3]
        high = visible[:, 4]

        # Classify each bar — strictly disjoint masks so every bar
        # lands in exactly one batch.
        body_h = np.abs(cl - op)
        is_doji = body_h < 1e-9
        is_bull = (cl >= op) & ~is_doji
        is_bear = (cl < op) & ~is_doji

        w = float(self._last_w)
        bull = QColor(self.bull_color)
        bear = QColor(self.bear_color)
        doji = QColor(200, 200, 200)
        bull_pen = pg.mkPen(bull, width=1)
        bear_pen = pg.mkPen(bear, width=1)
        doji_pen = pg.mkPen(doji, width=1)
        bull_brush = pg.mkBrush(bull)
        bear_brush = pg.mkBrush(bear)
        null_brush = pg.mkBrush(None)

        p = QPainter(self.picture)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)

        # --- Bull bars -------------------------------------------------
        self._paint_batch(
            p,
            idx[is_bull],
            op[is_bull],
            cl[is_bull],
            low[is_bull],
            high[is_bull],
            w,
            bull_pen,
            bull_brush,
        )
        # --- Bear bars -------------------------------------------------
        self._paint_batch(
            p,
            idx[is_bear],
            op[is_bear],
            cl[is_bear],
            low[is_bear],
            high[is_bear],
            w,
            bear_pen,
            bear_brush,
        )
        # --- Doji ------------------------------------------------------
        # Doji bars have no body to fill — emit one drawLines for the
        # wicks and one for the flat "body" line at the open price.
        if bool(is_doji.any()):
            d_idx = idx[is_doji]
            d_op = op[is_doji]
            d_low = low[is_doji]
            d_high = high[is_doji]
            wicks = [
                QLineF(float(t), float(lo_v), float(t), float(hi_v))
                for t, lo_v, hi_v in zip(d_idx, d_low, d_high)
            ]
            if wicks:
                p.setPen(doji_pen)
                p.setBrush(null_brush)
                p.drawLines(wicks)
                self._paint_stats["draw_lines_calls"] += 1
            flats = [
                QLineF(float(t) - w, float(o), float(t) + w, float(o))
                for t, o in zip(d_idx, d_op)
            ]
            if flats:
                p.setPen(doji_pen)
                p.drawLines(flats)
                self._paint_stats["draw_lines_calls"] += 1

        p.end()

    def _paint_batch(
        self,
        p: QPainter,
        idx_arr: np.ndarray,
        op_arr: np.ndarray,
        cl_arr: np.ndarray,
        low_arr: np.ndarray,
        high_arr: np.ndarray,
        w: float,
        pen,  # type: ignore[no-untyped-def]
        brush,  # type: ignore[no-untyped-def]
    ) -> None:
        """Emit ONE :py:meth:`QPainter.drawLines` + ONE
        :py:meth:`QPainter.drawRects` call for the given side (bull or
        bear). No-op when the side has zero bars.
        """
        n = idx_arr.size
        if n == 0:
            return

        # Wicks — one QLineF per bar, single drawLines call. List
        # comprehension is the fastest pure-Python construction here.
        wicks = [
            QLineF(float(t), float(lo_v), float(t), float(hi_v))
            for t, lo_v, hi_v in zip(idx_arr, low_arr, high_arr)
        ]
        # Bodies — one QRectF per bar. ``min(o, c)`` puts the rect top
        # at the lower of open/close so positive/negative bodies render
        # the same way.
        by = np.minimum(op_arr, cl_arr)
        bh = np.abs(cl_arr - op_arr)
        rects = [
            QRectF(float(t) - w, float(y), w * 2.0, float(h))
            for t, y, h in zip(idx_arr, by, bh)
        ]

        p.setPen(pen)
        p.setBrush(brush)
        p.drawLines(wicks)
        self._paint_stats["draw_lines_calls"] += 1
        p.drawRects(rects)
        self._paint_stats["draw_rects_calls"] += 1

    # ------------------------------------------------------------------
    # Public hooks unchanged from pre-Phase-2 implementation
    # ------------------------------------------------------------------
    def set_colors(self, bull, bear) -> None:  # type: ignore[no-untyped-def]
        self.bull_color = bull
        self.bear_color = bear
        if self.data:
            self._gen()
            self.update()

    def paint(self, p, *args) -> None:  # type: ignore[no-untyped-def]
        p.drawPicture(0, 0, self.picture)

    def boundingRect(self):  # type: ignore[no-untyped-def]
        return QRectF(self.picture.boundingRect())


class FilledRectROI(pg.RectROI):
    """RectROI that paints a filled brush behind the standard handles."""

    def __init__(self, *args, **kwargs) -> None:
        self.brush = kwargs.pop("brush", None)
        super().__init__(*args, **kwargs)

    def paint(self, p, opt, widget) -> None:  # type: ignore[no-untyped-def]
        if self.brush:
            p.setBrush(self.brush)
            p.setPen(pg.mkPen(None))
            p.drawRect(self.boundingRect())
        super().paint(p, opt, widget)


__all__ = ["CandlestickItem", "FilledRectROI"]
