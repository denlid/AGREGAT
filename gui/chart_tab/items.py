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

import time
from typing import List, Optional, Tuple

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QLineF, QRectF, QTimer
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPainterPathStroker, QPicture

from gui.chart_tab.lod import DEFAULT_MAX_BARS, downsample_ohlc

# Phase 2.3 — repaint debounce. Set to 0 to process on the next event
# loop tick, providing TradingView-like fluidity without duplicating work.
_RANGE_DEBOUNCE_MS = 16

# Build candle pictures for the visible window plus a bounded look-ahead.
# Small pans can then reuse the existing QPicture instead of rebuilding all
# visible QLineF/QRectF objects on every mouse-move event.
_VIEWPORT_OVERSCAN_FRACTION = 0.25
_VIEWPORT_OVERSCAN_MIN_BARS = 20.0
_VIEWPORT_OVERSCAN_MAX_BARS = 250.0


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
        # Cached QColor objects (OPT-2: avoid recreating on every _gen)
        self._bull_qcolor = QColor(self.bull_color)
        self._bear_qcolor = QColor(self.bear_color)
        self._doji_qcolor = QColor(200, 200, 200)
        self._last_w = 0.35  # cached candle half-width in data coords
        self._view = None
        # Single-shot debounce timer for ``sigRangeChanged`` repaints.
        # Created lazily on the first attach so headless callers (unit
        # tests without QApplication) don't hit a Qt event loop.
        self._range_timer = None
        # Phase 2.3 — telemetry counters consumed by the headless
        # tests. The production paint path doesn't read these; they
        # only exist so batching guarantees can be asserted in CI.
        # Phase 1 (GPU plan) — ``paint_time_ms`` records the last
        # :py:meth:`_gen` wall-clock cost so FPS regressions can be
        # caught in CI / surfaced in the debug overlay.
        self._paint_stats = {
            "draw_lines_calls": 0,
            "draw_rects_calls": 0,
            "bars_drawn": 0,
            "bars_culled": 0,
            "paint_time_ms": 0.0,
            "gen_time_ms": 0.0,
            "data_cache_hit": False,
            "viewport_cache_hit": False,
        }
        # Phase 2 (GPU plan) — optional LOD pyramid + active-level
        # state. When ``_pyramid`` is set, :py:meth:`_on_range_changed`
        # auto-swaps the rendered TF based on how many base-TF (M1)
        # bars currently sit inside the view. ``_active_tf_ratio`` is
        # the ratio of the active TF's minutes to the base TF's
        # minutes — the width heuristic in :py:meth:`_update_width`
        # multiplies its base width by this so candles widen
        # proportionally on coarser levels (M5 candles 5× wider than
        # M1 etc.).
        self._pyramid = None
        self._active_tf: Optional[str] = None
        self._active_tf_ratio: float = 1.0
        self._pyramid_max_bars: int = self._max_bars
        self._visible_slice_cache_key: Optional[Tuple[float, float, int]] = None
        self._visible_slice_cache_value: Tuple[int, int] = (0, 0)
        self._rendered_slice: Tuple[int, int] = (0, 0)
        self._rendered_x_bounds: Optional[Tuple[float, float]] = None
        self._rendered_half_width: float = self._last_w
        self._all_wicks = []
        self._geom_rects = []
        self._geom_doji_flats = []
        self._mask_bull = np.empty(0, dtype=bool)
        self._mask_bear = np.empty(0, dtype=bool)
        self._mask_doji = np.empty(0, dtype=bool)

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
        if self._arr.shape[0] == 0 and self._pyramid is None:
            return
        # Phase 2 (GPU plan) — LOD pyramid sensor. Runs *before* the
        # debounced repaint so a swap rebuilds geometry once instead
        # of paying for a repaint at the old level followed by
        # another at the new one. The swap itself ends in
        # :py:meth:`set_data`, which triggers its own ``_gen``; we
        # skip the debounced repaint in that case.
        if self._pyramid is not None and self._maybe_swap_pyramid_tf():
            return
        # Coalesce bursts of range changes into a single repaint.
        if self._range_timer is not None:
            self._range_timer.start()
        else:
            self._do_range_repaint()

    def _do_range_repaint(self) -> None:
        if self._arr.shape[0] == 0:
            return
        prev_w = self._last_w
        self._update_width()
        if abs(prev_w - self._last_w) <= 1e-5 and self._picture_covers_view():
            self._paint_stats["viewport_cache_hit"] = True
            self.update()
            return
        self._gen()
        self.update()

    def _picture_covers_view(self) -> bool:
        """Return True when the current QPicture already spans the ViewBox."""
        if self._view is None or self._arr.shape[0] == 0:
            return False
        bounds = self._rendered_x_bounds
        if bounds is None:
            return False
        try:
            xr = self._view.viewRange()[0]
        except Exception:
            return False
        x0 = float(min(xr[0], xr[1]))
        x1 = float(max(xr[0], xr[1]))
        return x0 >= bounds[0] and x1 <= bounds[1]

    # ------------------------------------------------------------------
    # Phase 2 (GPU plan) — LOD pyramid sensor
    # ------------------------------------------------------------------
    def set_pyramid(self, pyramid, max_bars: Optional[int] = None) -> None:  # type: ignore[no-untyped-def]
        """Attach a :class:`engine.data.lod_pyramid.LodPyramid` so the
        candle item auto-swaps timeframes when the user zooms out.

        Pass ``pyramid=None`` to detach and revert to plain
        single-level rendering. ``max_bars`` overrides the swap
        threshold (default: :attr:`_max_bars`).

        Calling ``set_pyramid(pyramid)`` does *not* immediately swap
        the rendered data — the caller is expected to follow up with
        :py:meth:`set_data` to seed the initial view. Once seeded,
        subsequent ``sigRangeChanged`` events run the sensor and swap
        levels as needed.
        """
        self._pyramid = pyramid
        self._active_tf = pyramid.base_tf if pyramid is not None else None
        self._active_tf_ratio = 1.0
        self._geom_rects = None
        if max_bars is not None:
            self._pyramid_max_bars = int(max_bars)

    @property
    def active_tf(self) -> Optional[str]:
        """The TF currently being rendered, or ``None`` when the
        pyramid is unattached. Exposed for tests / debug overlay."""
        return self._active_tf

    def _maybe_swap_pyramid_tf(self) -> bool:
        """Pick a pyramid level for the current view range and swap if
        it differs from the active level. Returns ``True`` if a swap
        happened (so the caller knows the repaint is already in
        flight and can skip its own).

        Safe to call without a linked view: returns ``False``
        immediately. Safe to call when the chosen level is the same
        as the active one: also returns ``False``.
        """
        if self._pyramid is None or self._view is None:
            return False
        # Visible width in base-TF units. ``viewRange`` returns
        # ``[[x0, x1], [y0, y1]]``; the candlestick item uses x in
        # base-TF (M1) indices throughout, so ``x1 - x0`` is the
        # base-bar count we'd be drawing on the finest level.
        xr = self._view.viewRange()[0]
        visible_base = max(0, int(round(xr[1] - xr[0])))
        try:
            new_tf = self._pyramid.for_visible_bars(
                visible_base, self._pyramid_max_bars
            )
        except Exception:
            return False
        if new_tf == self._active_tf:
            return False

        # Phase 2 - Hysteresis to prevent flapping on the threshold
        budget = self._pyramid_max_bars
        if self._active_tf is not None:
            if budget * 0.9 <= visible_base <= budget * 1.1:
                return False

        try:
            new_data = self._pyramid.to_chart_arrays(new_tf)
        except Exception:
            return False
        # Look up the new ratio so :py:meth:`_update_width` widens
        # candles to fill their bucket on the zoomed-out view.
        try:
            from engine.data.lod_pyramid import tf_minutes

            base_min = tf_minutes(self._pyramid.base_tf)
            new_min = tf_minutes(new_tf)
            ratio = max(1.0, float(new_min) / float(base_min))
        except Exception:
            ratio = 1.0
        self._active_tf = new_tf
        self._active_tf_ratio = ratio
        # Force the width cache to invalidate so the new ratio takes
        # effect on the next ``_gen``.
        self._geom_rects = None
        self.set_data(new_data)
        return True

    def _update_width(self) -> None:
        """Compute candle half-width as ~40% of the per-candle pixel slot.

        Always re-materialises ``_geom_rects`` / ``_geom_doji_flats`` when
        they are unset so a subsequent :py:meth:`_gen` never observes a
        ``None`` cache. Without a linked ViewBox we use the default
        half-width — ``_gen`` is still expected to run (e.g. in headless
        tests that call ``set_data`` without attaching a view).

        Phase 2 (GPU plan) — when an LOD pyramid level coarser than the
        base TF is active, the heuristic operates on the *candle-count*
        view (``visible / tf_ratio``) and then multiplies the chosen
        base width by the same ratio. The result: M5 candles 5× wider
        than M1, H1 candles 60× wider, etc. — each candle visually
        fills its bucket on the zoomed-out view.
        """
        if self._arr.shape[0] == 0:
            self._last_w = 0.35
            self._geom_rects = []
            self._geom_doji_flats = []
            return

        ratio = max(1.0, float(self._active_tf_ratio))
        if self._view is None:
            base_w = (self._last_w / ratio) if self._last_w else 0.35
            new_w = base_w * ratio
        else:
            xr = self._view.viewRange()[0]
            visible = max(1, xr[1] - xr[0]) / ratio
            if visible < 20:
                base_w = 0.42
            elif visible < 60:
                base_w = 0.40
            elif visible < 150:
                base_w = 0.38
            else:
                base_w = 0.35
            new_w = base_w * ratio

        if abs(self._last_w - new_w) > 1e-5 or getattr(self, "_geom_rects", None) is None:
            self._last_w = new_w
            if self._arr.shape[0] > 0:
                # Keep lightweight cache sentinels for compatibility with
                # tests/debug probes; actual QRectF/QLineF objects are built
                # only for the visible slice inside _gen().
                n = int(self._arr.shape[0])
                self._geom_rects = [None] * n
                self._geom_doji_flats = [None] * n
            else:
                self._geom_rects = []
                self._geom_doji_flats = []

    # ------------------------------------------------------------------
    # Data ingest
    # ------------------------------------------------------------------
    def set_data(self, data) -> None:  # type: ignore[no-untyped-def]
        # Phase 2: Accept np.ndarray directly to bypass slow tuple lists.
        # Read-only NumPy payloads are treated as immutable render caches,
        # so re-feeding the exact same array can skip the expensive wick /
        # mask / QPicture rebuild entirely.
        if isinstance(data, np.ndarray):
            if data is self._arr and not data.flags.writeable:
                self._paint_stats["data_cache_hit"] = True
                return
            self._paint_stats["data_cache_hit"] = False
            self._arr = data
            self.data = []  # No longer needed when _arr is populated
            self._raw_data = []
        else:
            self._paint_stats["data_cache_hit"] = False
            self._raw_data = list(data) if not isinstance(data, list) else data
            if len(self._raw_data) > self._max_bars:
                self.data = downsample_ohlc(self._raw_data, self._max_bars)
            else:
                self.data = self._raw_data
            if self.data:
                self._arr = np.asarray(self.data, dtype=np.float64)
            else:
                self._arr = np.empty((0, 5), dtype=np.float64)

        if self._arr.shape[0] > 0:
            cl = self._arr[:, 2]
            op = self._arr[:, 1]
            bh = np.abs(cl - op)
            self._mask_doji = bh < 1e-9
            self._mask_bull = (cl >= op) & ~self._mask_doji
            self._mask_bear = (cl < op) & ~self._mask_doji
        else:
            self._all_wicks = []
            self._mask_bull = np.empty(0, dtype=bool)
            self._mask_bear = np.empty(0, dtype=bool)
            self._mask_doji = np.empty(0, dtype=bool)

        self._all_wicks = []
        self._geom_rects = None
        self._geom_doji_flats = None
        self._visible_slice_cache_key = None
        self._visible_slice_cache_value = (0, 0)
        self._rendered_slice = (0, 0)
        self._rendered_x_bounds = None
        self._rendered_half_width = self._last_w
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
        if getattr(self, "_arr", None) is None or self._arr.shape[0] == 0:
            return None
        i = np.searchsorted(self._arr[:, 0], idx)
        if i < self._arr.shape[0] and self._arr[i, 0] == int(idx):
            return (self._arr[i, 1], self._arr[i, 4], self._arr[i, 3], self._arr[i, 2])
        # Fallback: search the full-resolution raw data (before LOD downsampling)
        # so tooltips still resolve when zoomed out.
        if self._raw_data:
            try:
                return (self._raw_data[int(idx)][1], self._raw_data[int(idx)][4], self._raw_data[int(idx)][3], self._raw_data[int(idx)][2])
            except Exception:
                for t in self._raw_data:
                    if int(t[0]) == int(idx):
                        return (t[1], t[4], t[3], t[2])
        return None

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
        x0 = float(xr[0])
        x1 = float(xr[1])
        cache_key = (round(x0, 3), round(x1, 3), n)
        if cache_key == self._visible_slice_cache_key:
            self._paint_stats["viewport_cache_hit"] = True
            return self._visible_slice_cache_value
        self._paint_stats["viewport_cache_hit"] = False
        # ``self._arr[:, 0]`` is the idx column. It's guaranteed
        # sorted by ``downsample_ohlc`` (bin-order) and by the caller
        # (chart_tab always feeds a monotonic ``range(n)`` — same
        # contract pyqtgraph's PlotCurveItem assumes for setData(x=)).
        xs = self._arr[:, 0]
        w = abs(x1 - x0)
        pad = min(
            _VIEWPORT_OVERSCAN_MAX_BARS,
            max(_VIEWPORT_OVERSCAN_MIN_BARS, w * _VIEWPORT_OVERSCAN_FRACTION),
        )
        x_min = min(x0, x1) - pad
        x_max = max(x0, x1) + pad
        lo = int(np.searchsorted(xs, x_min, side="left"))
        hi = int(np.searchsorted(xs, x_max, side="right"))
        out = (lo, hi)
        self._visible_slice_cache_key = cache_key
        self._visible_slice_cache_value = out
        return out

    # ------------------------------------------------------------------
    # Phase 2.3 — batched painter
    # ------------------------------------------------------------------
    def _gen(self) -> None:
        # Phase 1 (GPU plan) — track ``_gen`` wall-clock cost so the
        # plan's "<2 ms paint time" target is measurable in CI / the
        # debug overlay. ``time.perf_counter`` is monotonic and cheap
        # enough (~100 ns) to be safe on the hot paint path.
        t0 = time.perf_counter()
        self.picture = QPicture()
        self._paint_stats["draw_lines_calls"] = 0
        self._paint_stats["draw_rects_calls"] = 0
        self._paint_stats["bars_drawn"] = 0
        self._paint_stats["bars_culled"] = 0
        self._paint_stats["gen_time_ms"] = 0.0
        self._paint_stats["paint_time_ms"] = 0.0
        self._paint_stats["viewport_cache_hit"] = False
        n_total = self._arr.shape[0]
        if n_total == 0:
            self._paint_stats["gen_time_ms"] = (time.perf_counter() - t0) * 1000.0
            return

        # Phase 2.1 — clip to visible window once per repaint.
        lo, hi = self._visible_slice()
        self._rendered_slice = (lo, hi)
        if hi > lo:
            xs_all = self._arr[:, 0]
            self._rendered_x_bounds = (
                float(xs_all[lo]) - float(self._last_w),
                float(xs_all[hi - 1]) + float(self._last_w),
            )
        else:
            self._rendered_x_bounds = None
        self._rendered_half_width = self._last_w
        self._paint_stats["bars_culled"] = n_total - (hi - lo)
        if hi <= lo:
            self._paint_stats["gen_time_ms"] = (time.perf_counter() - t0) * 1000.0
            return

        # OPT-3: Use slice object to avoid redundant slicing operations
        slice_obj = slice(lo, hi)
        visible = self._arr[slice_obj]
        self._paint_stats["bars_drawn"] = visible.shape[0]

        is_bull = self._mask_bull[slice_obj]
        is_bear = self._mask_bear[slice_obj]
        is_doji = self._mask_doji[slice_obj]

        w = float(self._last_w)
        # OPT-2: Use cached QColor objects instead of creating new ones
        bull = self._bull_qcolor
        bear = self._bear_qcolor
        doji = self._doji_qcolor
        bull_pen = pg.mkPen(bull, width=1)
        bear_pen = pg.mkPen(bear, width=1)
        doji_pen = pg.mkPen(doji, width=1)
        bull_brush = pg.mkBrush(bull)
        bear_brush = pg.mkBrush(bear)
        null_brush = pg.mkBrush(None)

        p = QPainter(self.picture)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)

        x = visible[:, 0]
        op = visible[:, 1]
        cl = visible[:, 2]
        lo_arr = visible[:, 3]
        hi_arr = visible[:, 4]
        body_y = np.minimum(op, cl)
        body_h = np.abs(cl - op)

        def _lines(mask):
            return [
                QLineF(float(t), float(lo), float(t), float(hi))
                for t, lo, hi in zip(x[mask], lo_arr[mask], hi_arr[mask])
            ]

        def _rects(mask):
            return [
                QRectF(float(t) - w, float(y), w * 2.0, float(h))
                for t, y, h in zip(x[mask], body_y[mask], body_h[mask])
            ]

        # --- Bull bars -------------------------------------------------
        self._paint_batch(
            p,
            _lines(is_bull),
            _rects(is_bull),
            bull_pen,
            bull_brush,
        )
        # --- Bear bars -------------------------------------------------
        self._paint_batch(
            p,
            _lines(is_bear),
            _rects(is_bear),
            bear_pen,
            bear_brush,
        )
        # --- Doji ------------------------------------------------------
        doji_wicks = _lines(is_doji)
        if doji_wicks:
            p.setPen(doji_pen)
            p.setBrush(null_brush)
            p.drawLines(doji_wicks)
            self._paint_stats["draw_lines_calls"] += 1
        doji_flats = [
            QLineF(float(t) - w, float(o), float(t) + w, float(o))
            for t, o in zip(x[is_doji], op[is_doji])
        ]
        if doji_flats:
            p.setPen(doji_pen)
            p.drawLines(doji_flats)
            self._paint_stats["draw_lines_calls"] += 1

        p.end()
        self._paint_stats["gen_time_ms"] = (time.perf_counter() - t0) * 1000.0

    def _paint_batch(
        self,
        p: QPainter,
        wicks: list,
        rects: list,
        pen,  # type: ignore[no-untyped-def]
        brush,  # type: ignore[no-untyped-def]
    ) -> None:
        """Emit ONE :py:meth:`QPainter.drawLines` + ONE
        :py:meth:`QPainter.drawRects` call for the given side (bull or
        bear). No-op when the side has zero bars.
        """
        if not wicks and not rects:
            return

        p.setPen(pen)
        p.setBrush(brush)
        if wicks:
            p.drawLines(wicks)
            self._paint_stats["draw_lines_calls"] += 1
        if rects:
            p.drawRects(rects)
            self._paint_stats["draw_rects_calls"] += 1

    # ------------------------------------------------------------------
    # Public hooks unchanged from pre-Phase-2 implementation
    # ------------------------------------------------------------------
    def set_colors(self, bull, bear) -> None:  # type: ignore[no-untyped-def]
        self.bull_color = bull
        self.bear_color = bear
        # OPT-2: Update cached QColor objects
        self._bull_qcolor = QColor(bull)
        self._bear_qcolor = QColor(bear)
        if self._arr.shape[0] > 0:
            self._gen()
            self.update()

    def paint(self, p, *args) -> None:  # type: ignore[no-untyped-def]
        t0 = time.perf_counter()
        try:
            p.drawPicture(0, 0, self.picture)
        finally:
            self._paint_stats["paint_time_ms"] = (time.perf_counter() - t0) * 1000.0

    def boundingRect(self):  # type: ignore[no-untyped-def]
        return QRectF(self.picture.boundingRect())


class FilledRectROI(pg.RectROI):
    """RectROI that paints a filled brush behind the standard handles.
    Handles are hidden by default and shown only on hover.
    """

    def __init__(self, *args, **kwargs) -> None:
        self.brush = kwargs.pop("brush", None)
        super().__init__(*args, **kwargs)
        self._handles_visible = False
        self._set_handles_visible(False)

    def _set_handles_visible(self, visible: bool) -> None:
        self._handles_visible = visible
        for h in self.handles:
            item = h.get("item")
            if item is not None:
                item.setVisible(visible)

    def hoverEvent(self, ev) -> None:
        super().hoverEvent(ev)
        self._set_handles_visible(not ev.isExit())

    def shape(self) -> QPainterPath:
        rect = self.boundingRect()
        path = QPainterPath()
        path.addRect(rect)

        stroker = QPainterPathStroker()
        stroker.setWidth(max(8.0, float(self.currentPen.widthF()) + 6.0))
        return stroker.createStroke(path)

    def paint(self, p, opt, widget) -> None:  # type: ignore[no-untyped-def]
        if self.brush:
            p.setBrush(self.brush)
            p.setPen(pg.mkPen(None))
            p.drawRect(self.boundingRect())
        super().paint(p, opt, widget)


__all__ = ["CandlestickItem", "FilledRectROI"]
