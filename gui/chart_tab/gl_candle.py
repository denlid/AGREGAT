"""Phase 3 (GPU plan) — direct GLSL rendering for candlesticks.

The legacy :class:`gui.chart_tab.items.CandlestickItem` issues two
small QPainter draw calls per candle (one for the wick line, one for
the body rect). Even with the Phase 1 OpenGL viewport that's a
QPainter loop with O(N) state changes. This module replaces the
QPainter loop with a single GPU-driven pipeline:

1. ``set_data(...)`` packs every candle into a single ``float32``
   instance array using a vectorised NumPy routine. Each instance
   contains OHLC data and color — no geometry is baked on the CPU.
2. ``paint(...)`` enters Qt's *native painting* block, binds a
   vertex shader that expands unit-templates into body+wick
   geometry on the GPU, and issues exactly two
   ``glDrawArraysInstanced`` calls per repaint (one for all bodies,
   one for all wicks). CPU work is O(1) in the paint path.

When the native-painting block is unavailable (offscreen test runs,
software fallback boxes, environments without
``PySide6.QtOpenGL``) the item transparently falls back to the
legacy QPainter path inherited from
:class:`gui.chart_tab.items.CandlestickItem` — so the chart still
renders, just at the slower software rate.

Public surface:

* :func:`make_candlestick_item` — factory honoured by ``ChartTab``.
  Returns a :class:`GLCandlestickItem` when ``AGREGAT_GL_CANDLES`` is
  truthy *and* PySide6's GL bindings import cleanly; otherwise falls
  back to :class:`gui.chart_tab.items.CandlestickItem`. Same env-var
  pattern as :mod:`gui.chart_tab.gpu_config` so ops can toggle the
  shader path with a single flag.
* :class:`GLCandlestickItem` — drop-in subclass of
  :class:`CandlestickItem`. Inherits the LOD pyramid sensor + range
  hooks + width heuristic from Phase 2, so the only thing that
  changes is the paint backend.

Plan reference: ``chart_gpu_optimization_plan.md`` Phase 3b (Instancing).
We use hardware instancing (glDrawArraysInstanced) to move geometry
expansion to the GPU. This is more portable than geometry shaders
(Phase 3a) as it only requires OpenGL 3.1 or the ARB_instanced_arrays
extension, which is widely supported even on older drivers that Qt 6
negotiates.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Optional, Tuple

import numpy as np
from PySide6.QtCore import QRectF
from PySide6.QtGui import QColor

from gui.chart_tab.items import CandlestickItem
from gui.chart_tab.lod import DEFAULT_MAX_BARS

logger = logging.getLogger("StrategyTester.gui.chart_tab.gl_candle")

# Env flag that ops/tests can flip to opt into the shader backend.
# Matches the ``AGREGAT_USE_GPU`` convention in :mod:`gpu_config`. We
# treat the GL candle path as *off by default* until the user
# validates it on a real GPU box — Phase 3 is the most invasive
# change in the plan, so we lock it behind an explicit opt-in.
_ENV_VAR = "AGREGAT_GL_CANDLES"


# ---------------------------------------------------------------------------
# GLSL sources
# ---------------------------------------------------------------------------
# GLSL 120 / OpenGL 2.1 is the lowest common denominator pyqtgraph's
# OpenGL viewport reliably negotiates. For Phase 3b (Instancing) we 
# use attributes for per-instance data and glVertexAttribDivisor.
VERTEX_SHADER_SRC = """\
#version 120
attribute vec2 a_pos;          // Template vertex (unit square/line)
attribute vec4 i_data1;        // [x, open, close, high]
attribute vec4 i_data2;        // [low, r, g, b]

uniform mat4 u_mvp;
uniform float u_half_width;
uniform float u_is_wick;       // 1.0 if drawing wicks, 0.0 for bodies

varying vec3 v_color;

void main() {
    float x = i_data1.x;
    float op = i_data1.y;
    float cl = i_data1.z;
    float hi = i_data1.w;
    float lo = i_data2.x;
    v_color = i_data2.yzw;

    vec2 pos = a_pos;
    if (u_is_wick > 0.5) {
        // Wick: a_pos.y is 0.0 or 1.0. Map to [lo, hi].
        pos.x = x;
        pos.y = lo + pos.y * (hi - lo);
    } else {
        // Body: a_pos.x is -0.5 or 0.5, a_pos.y is 0.0 or 1.0.
        // Map x to [x - hw, x + hw], y to [min(op, cl), max(op, cl)].
        pos.x = x + pos.x * u_half_width * 2.0;
        float b_lo = min(op, cl);
        float b_hi = max(op, cl);
        pos.y = b_lo + pos.y * (b_hi - b_lo);
    }

    gl_Position = u_mvp * vec4(pos, 0.0, 1.0);
}
"""

FRAGMENT_SHADER_SRC = """\
#version 120
varying vec3 v_color;
void main() {
    gl_FragColor = vec4(v_color, 1.0);
}
"""

# Template vertices for a unit candle.
# Body: 2 triangles (6 vertices) covering [-0.5, 0] to [0.5, 1]
UNIT_BODY_VERTS = np.array([
    -0.5, 0.0,
     0.5, 0.0,
     0.5, 1.0,
    -0.5, 0.0,
     0.5, 1.0,
    -0.5, 1.0,
], dtype=np.float32)

# Wick: 1 line (2 vertices) covering [0, 0] to [0, 1]
UNIT_WICK_VERTS = np.array([
    0.0, 0.0,
    0.0, 1.0,
], dtype=np.float32)

# Instance data layout: x, open, close, high, low, r, g, b (8 floats)
FLOATS_PER_INSTANCE = 8


# ---------------------------------------------------------------------------
# Vertex packer (pure NumPy — fully testable without a GL context).
# ---------------------------------------------------------------------------


def _qcolor_to_rgb(color) -> Tuple[float, float, float]:  # type: ignore[no-untyped-def]
    """Normalise a color spec (``"#RRGGBB"`` / ``QColor`` /
    ``(r,g,b)`` tuple) to a float-triple in ``[0, 1]``. Used by the
    vertex packer so the shader doesn't need to know about Qt
    palette types."""
    if isinstance(color, tuple) and len(color) == 3:
        return tuple(float(c) for c in color)  # type: ignore[return-value]
    if isinstance(color, str):
        color = QColor(color)
    if isinstance(color, QColor):
        return (color.redF(), color.greenF(), color.blueF())
    raise TypeError(f"unsupported color spec: {color!r}")


def pack_candle_instances(
    arr: np.ndarray,
    bull_rgb: Tuple[float, float, float],
    bear_rgb: Tuple[float, float, float],
) -> np.ndarray:
    """Vectorised instance data packer.

    ``arr`` is the ``(N, 5)`` float64 NumPy array the legacy
    candlestick item maintains in ``_arr`` — columns are
    ``(idx, open, close, low, high)``.

    Returns a contiguous ``float32`` array in shape
    ``(N, FLOATS_PER_INSTANCE)`` interleaved as:
    ``(x, open, close, high, low, r, g, b)``.

    The function is intentionally pure NumPy so it can be unit-
    tested without a Qt event loop or GL context.
    """
    if arr is None or arr.shape[0] == 0:
        return np.empty((0, FLOATS_PER_INSTANCE), dtype=np.float32)

    if arr.ndim != 2 or arr.shape[1] < 5:
        raise ValueError(
            f"pack_candle_instances expects (N, 5) array, got shape {arr.shape}"
        )

    n = arr.shape[0]
    res = np.empty((n, FLOATS_PER_INSTANCE), dtype=np.float32)

    # ISSUE-2: More efficient array assignment using fancy indexing
    # arr columns: [idx, open, close, low, high]
    # res columns: [x, open, close, high, low, r, g, b]
    res[:, 0] = arr[:, 0]  # x
    res[:, 1] = arr[:, 1]  # open
    res[:, 2] = arr[:, 2]  # close
    res[:, 3] = arr[:, 4]  # high
    res[:, 4] = arr[:, 3]  # low

    op = res[:, 1]
    cl = res[:, 2]
    is_bull = (cl >= op)

    bull = np.asarray(bull_rgb, dtype=np.float32)
    bear = np.asarray(bear_rgb, dtype=np.float32)
    res[:, 5:8] = np.where(
        is_bull[:, None], bull[None, :], bear[None, :]
    ).astype(np.float32)

    return res


# ---------------------------------------------------------------------------
# GLCandlestickItem
# ---------------------------------------------------------------------------


class GLCandlestickItem(CandlestickItem):
    """Drop-in GL-shader-backed candlestick renderer.

    Subclasses :class:`CandlestickItem` so every non-paint surface
    (LOD pyramid sensor, range hooks, width heuristic, OHLC lookups
    used by the crosshair label) is inherited unchanged. The only
    overrides are :py:meth:`set_data`, :py:meth:`_do_range_repaint`,
    and :py:meth:`paint` — and the first two only rebuild the CPU
    vertex caches; the GL upload is deferred to paint time so we
    never touch the GL context off the render thread.
    """

    def __init__(self, max_bars: int = DEFAULT_MAX_BARS) -> None:
        super().__init__(max_bars)

        # CPU instance cache — rebuilt by :py:meth:`_rebuild_gl_vertices`.
        self._gl_instances: np.ndarray = np.empty(
            (0, FLOATS_PER_INSTANCE), dtype=np.float32
        )

        # Lazy GL state — populated on the first successful native-
        # paint enter.
        self._gl_program = None  # QOpenGLShaderProgram
        self._gl_body_tpl_vbo = None  # QOpenGLBuffer (template)
        self._gl_wick_tpl_vbo = None  # QOpenGLBuffer (template)
        self._gl_instance_vbo = None  # QOpenGLBuffer (instance data)
        
        self._gl_attr_pos: int = -1
        self._gl_attr_data1: int = -1
        self._gl_attr_data2: int = -1
        self._gl_uniform_mvp: int = -1
        self._gl_uniform_hw: int = -1
        self._gl_uniform_is_wick: int = -1
        
        self._gl_initialised = False
        self._gl_init_attempts = 0

        # VBO Pool Management (Incremental updates)
        self._gl_vbo_capacity_bytes = 0
        self._last_uploaded_count = 0

        # Set whenever ``_gl_instances`` changes; paint clears it after
        # uploading.
        self._gl_verts_dirty = True

        self._paint_stats["gl_uploads"] = 0
        self._paint_stats["last_backend"] = "painter"

    # ------------------------------------------------------------------
    # Hooks that re-build the CPU vertex caches.
    # ------------------------------------------------------------------
    def set_data(self, data) -> None:  # type: ignore[no-untyped-def]
        super().set_data(data)
        self._rebuild_gl_vertices()

    def _do_range_repaint(self) -> None:
        if self._arr.shape[0] == 0:
            return

        # Keep the inherited QPicture path current as a reliable fallback.
        # The GL paint path can still fail at runtime when the widget has no
        # active native GL context; in that case paint() falls back to
        # CandlestickItem.paint(), which must not contain stale/empty geometry.
        super()._do_range_repaint()
        self.update()

    def set_colors(self, bull: str, bear: str) -> None:  # type: ignore[override]
        super().set_colors(bull, bear)
        self._rebuild_gl_vertices()

    def _rebuild_gl_vertices(self) -> None:
        """(Re)build :attr:`_gl_instances` from the legacy ``_arr`` cache."""
        t0 = time.perf_counter()
        try:
            bull = _qcolor_to_rgb(self.bull_color)
            bear = _qcolor_to_rgb(self.bear_color)
        except Exception:
            logger.debug("color resolve failed; using defaults", exc_info=True)
            bull = (0.149, 0.651, 0.604)  # #26A69A
            bear = (0.937, 0.325, 0.314)  # #EF5350
            
        # We pack the ENTIRE array into the instance buffer.
        self._gl_instances = pack_candle_instances(self._arr, bull, bear)
        self._gl_verts_dirty = True
        self._paint_stats["gen_time_ms"] = (time.perf_counter() - t0) * 1000.0

    # ------------------------------------------------------------------
    # Paint
    # ------------------------------------------------------------------
    def paint(self, painter, option=None, widget=None):  # type: ignore[no-untyped-def]
        t0 = time.perf_counter()
        if self._try_paint_gl(painter):
            self._paint_stats["last_backend"] = "gl"
            self._paint_stats["paint_time_ms"] = (time.perf_counter() - t0) * 1000.0
            return
        self._paint_stats["last_backend"] = "painter"
        super().paint(painter, option, widget)

    def _try_paint_gl(self, painter) -> bool:
        if self._gl_instances.shape[0] == 0:
            return False

        try:
            from PySide6.QtGui import (
                QOpenGLContext,
                QOpenGLExtraFunctions,
            )
            from PySide6.QtOpenGL import (
                QOpenGLBuffer,
                QOpenGLShader,
                QOpenGLShaderProgram,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("PySide6 GL bindings unavailable: %r", exc)
            return False

        ctx = QOpenGLContext.currentContext()
        if ctx is None:
            return False

        try:
            painter.beginNativePainting()
        except Exception:  # noqa: BLE001
            return False

        ok = False
        try:
            if not self._gl_initialised:
                if self._gl_init_attempts >= 3:
                    painter.endNativePainting()
                    return False
                self._gl_init_attempts += 1
                success = self._init_gl(QOpenGLShader, QOpenGLShaderProgram, QOpenGLBuffer)
                if success:
                    self._gl_initialised = True

            if self._gl_program is None:
                painter.endNativePainting()
                return False

            mvp = self._compute_mvp(painter)

            # Use ExtraFunctions for glDrawArraysInstanced and glVertexAttribDivisor
            gl = QOpenGLExtraFunctions(ctx)
            gl.initializeOpenGLFunctions()

            self._gl_program.bind()
            self._gl_program.setUniformValue(self._gl_uniform_mvp, mvp)
            self._gl_program.setUniformValue(self._gl_uniform_hw, float(self._last_w))

            if self._gl_verts_dirty:
                self._upload_vbos(QOpenGLBuffer)
                self._gl_verts_dirty = False

            # Instance data layout: (x, op, cl, hi, lo, r, g, b) = 8 floats
            stride = FLOATS_PER_INSTANCE * 4 # 32 bytes
            n_instances = self._gl_instances.shape[0]

            self._gl_instance_vbo.bind()
            
            # Attribute i_data1: [x, op, cl, hi]
            self._gl_program.enableAttributeArray(self._gl_attr_data1)
            self._gl_program.setAttributeBuffer(self._gl_attr_data1, 0x1406, 0, 4, stride)
            gl.glVertexAttribDivisor(self._gl_attr_data1, 1)
            
            # Attribute i_data2: [lo, r, g, b]
            self._gl_program.enableAttributeArray(self._gl_attr_data2)
            self._gl_program.setAttributeBuffer(self._gl_attr_data2, 0x1406, 4 * 4, 4, stride)
            gl.glVertexAttribDivisor(self._gl_attr_data2, 1)

            # --- Draw Bodies ---
            self._gl_program.setUniformValue(self._gl_uniform_is_wick, 0.0)
            self._gl_body_tpl_vbo.bind()
            self._gl_program.enableAttributeArray(self._gl_attr_pos)
            self._gl_program.setAttributeBuffer(self._gl_attr_pos, 0x1406, 0, 2, 0)
            gl.glVertexAttribDivisor(self._gl_attr_pos, 0) # Constant for all instances
            gl.glDrawArraysInstanced(0x0004, 0, 6, n_instances) # GL_TRIANGLES = 0x0004
            
            # --- Draw Wicks ---
            self._gl_program.setUniformValue(self._gl_uniform_is_wick, 1.0)
            self._gl_wick_tpl_vbo.bind()
            self._gl_program.enableAttributeArray(self._gl_attr_pos)
            self._gl_program.setAttributeBuffer(self._gl_attr_pos, 0x1406, 0, 2, 0)
            gl.glVertexAttribDivisor(self._gl_attr_pos, 0)
            gl.glDrawArraysInstanced(0x0001, 0, 2, n_instances) # GL_LINES = 0x0001

            self._gl_program.disableAttributeArray(self._gl_attr_pos)
            self._gl_program.disableAttributeArray(self._gl_attr_data1)
            self._gl_program.disableAttributeArray(self._gl_attr_data2)
            
            # Cleanup divisor state for other renderers
            gl.glVertexAttribDivisor(self._gl_attr_data1, 0)
            gl.glVertexAttribDivisor(self._gl_attr_data2, 0)
            
            self._gl_program.release()
            ok = True
        except Exception:  # noqa: BLE001
            logger.exception("GL paint failed; falling back to QPainter")
            ok = False
        finally:
            try:
                painter.endNativePainting()
            except Exception:  # noqa: BLE001
                pass
        return ok

    def _init_gl(
        self,
        QOpenGLShader,
        QOpenGLShaderProgram,
        QOpenGLBuffer,
    ) -> bool:
        program = QOpenGLShaderProgram()
        if not program.addShaderFromSourceCode(
            QOpenGLShader.ShaderTypeBit.Vertex, VERTEX_SHADER_SRC
        ):
            logger.warning("vertex shader compile failed: %s", program.log())
            return False
        if not program.addShaderFromSourceCode(
            QOpenGLShader.ShaderTypeBit.Fragment, FRAGMENT_SHADER_SRC
        ):
            logger.warning("fragment shader compile failed: %s", program.log())
            return False
        if not program.link():
            logger.warning("shader program link failed: %s", program.log())
            return False
        self._gl_program = program
        self._gl_attr_pos = program.attributeLocation("a_pos")
        self._gl_attr_data1 = program.attributeLocation("i_data1")
        self._gl_attr_data2 = program.attributeLocation("i_data2")
        self._gl_uniform_mvp = program.uniformLocation("u_mvp")
        self._gl_uniform_hw = program.uniformLocation("u_half_width")
        self._gl_uniform_is_wick = program.uniformLocation("u_is_wick")

        # Template VBOs
        self._gl_body_tpl_vbo = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
        self._gl_body_tpl_vbo.setUsagePattern(QOpenGLBuffer.UsagePattern.StaticDraw)
        self._gl_body_tpl_vbo.create()
        self._gl_body_tpl_vbo.bind()
        self._gl_body_tpl_vbo.allocate(UNIT_BODY_VERTS.tobytes(), UNIT_BODY_VERTS.nbytes)
        
        self._gl_wick_tpl_vbo = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
        self._gl_wick_tpl_vbo.setUsagePattern(QOpenGLBuffer.UsagePattern.StaticDraw)
        self._gl_wick_tpl_vbo.create()
        self._gl_wick_tpl_vbo.bind()
        self._gl_wick_tpl_vbo.allocate(UNIT_WICK_VERTS.tobytes(), UNIT_WICK_VERTS.nbytes)

        # Instance VBO
        self._gl_instance_vbo = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
        self._gl_instance_vbo.setUsagePattern(QOpenGLBuffer.UsagePattern.DynamicDraw)
        self._gl_instance_vbo.create()
        return True

    def _upload_vbos(self, QOpenGLBuffer) -> None:
        """Push the current CPU instance cache to the GPU using incremental updates."""
        if self._gl_instance_vbo is None:
            return

        n_instances = self._gl_instances.shape[0]
        needed_bytes = n_instances * FLOATS_PER_INSTANCE * 4
        
        self._gl_instance_vbo.bind()
        
        # If we need more than we have, reallocate with a 50% buffer
        if needed_bytes > self._gl_vbo_capacity_bytes:
            new_capacity = int(needed_bytes * 1.5) + (1024 * FLOATS_PER_INSTANCE * 4) # buffer
            # allocate(None, size) initializes storage without data
            self._gl_instance_vbo.allocate(None, new_capacity)
            self._gl_vbo_capacity_bytes = new_capacity
            
            # Full upload into the new buffer
            data_bytes = self._gl_instances.tobytes()
            self._gl_instance_vbo.write(0, data_bytes, len(data_bytes))
            self._last_uploaded_count = n_instances
            logger.debug("GL: reallocated VBO pool (capacity=%d bytes)", new_capacity)
        else:
            # We have space. Check if we can do an incremental append.
            # We only append if n_instances > last_count and the existing 
            # prefix matches (which is guaranteed by how ChartTab works).
            if n_instances > self._last_uploaded_count and self._last_uploaded_count > 0:
                new_slice = self._gl_instances[self._last_uploaded_count:]
                new_bytes = new_slice.tobytes()
                offset = self._last_uploaded_count * FLOATS_PER_INSTANCE * 4
                self._gl_instance_vbo.write(offset, new_bytes, len(new_bytes))
                self._last_uploaded_count = n_instances
            elif n_instances != self._last_uploaded_count:
                # Full rewrite (data shrunk or changed significantly)
                data_bytes = self._gl_instances.tobytes()
                self._gl_instance_vbo.write(0, data_bytes, len(data_bytes))
                self._last_uploaded_count = n_instances
                
        self._gl_instance_vbo.release()
        self._paint_stats["gl_uploads"] = int(self._paint_stats.get("gl_uploads", 0)) + 1

    def _compute_mvp(self, painter):  # type: ignore[no-untyped-def]
        """Convert the QPainter world transform into a 4x4 matrix the
        vertex shader can multiply against.

        The painter's world transform maps our data coordinates into
        the GL viewport's normalised device space; we read it via
        ``transform()`` and lift it to a ``QMatrix4x4``.
        """
        from PySide6.QtGui import QMatrix4x4

        if self._view is None:
            return QMatrix4x4()

        # Use the ViewBox's transform: it knows how to go from data
        # coordinates to widget pixel coordinates. We then add a
        # second stage that maps widget pixels to NDC ([-1, 1]).
        try:
            xr, yr = self._view.viewRange()
            x0, x1 = float(xr[0]), float(xr[1])
            y0, y1 = float(yr[0]), float(yr[1])
        except Exception:
            return QMatrix4x4()

        if x1 == x0 or y1 == y0:
            return QMatrix4x4()

        m = QMatrix4x4()
        # ortho(left, right, bottom, top, near, far): standard 2D
        # camera. Maps data-space rect (x0,y0)-(x1,y1) to NDC.
        m.ortho(x0, x1, y0, y1, -1.0, 1.0)
        return m

    # ------------------------------------------------------------------
    # Bounding rect — same as the legacy item (uses the data extents).
    # ``CandlestickItem.boundingRect`` reads from the QPicture, which
    # we no longer populate; recompute from ``_arr`` instead.
    # ------------------------------------------------------------------
    def boundingRect(self):  # type: ignore[override]
        if self._arr.shape[0] == 0:
            return QRectF()
        x = self._arr[:, 0]
        lo = self._arr[:, 3]
        hi = self._arr[:, 4]
        x_min = float(x.min()) - self._last_w
        x_max = float(x.max()) + self._last_w
        y_min = float(lo.min())
        y_max = float(hi.max())
        return QRectF(x_min, y_min, x_max - x_min, y_max - y_min)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def _truthy_env(value: Optional[str]) -> Optional[bool]:
    """Tri-state parser for ``AGREGAT_GL_CANDLES``. Same semantics as
    :func:`gui.chart_tab.gpu_config._truthy_env`."""
    if value is None:
        return None
    v = value.strip().lower()
    if v in {"1", "true", "yes", "on", "y"}:
        return True
    if v in {"0", "false", "no", "off", "n", ""}:
        return False
    return None


def is_gl_candle_available() -> bool:
    """Return ``True`` if the PySide6 GL bindings needed by
    :class:`GLCandlestickItem` are importable. The actual GL context
    check only happens at paint time, but this lets the factory skip
    constructing the subclass entirely on stripped PySide6 installs.
    """
    try:
        from PySide6.QtOpenGL import (  # noqa: F401
            QOpenGLBuffer,
            QOpenGLShader,
            QOpenGLShaderProgram,
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("PySide6 GL bindings unavailable: %r", exc)
        return False
    return True


def make_candlestick_item(
    max_bars: int = DEFAULT_MAX_BARS,
    *,
    force: Optional[bool] = None,
) -> CandlestickItem | GLCandlestickItem:
    """Return the candlestick item ChartTab should add to its plot.

    Decision matrix:

    * ``force=True`` — always return :class:`GLCandlestickItem` (still
      bails to :class:`CandlestickItem` if PySide6 GL bindings are
      missing — we never crash here).
    * ``force=False`` — always return the legacy
      :class:`CandlestickItem`.
    * ``force=None`` (default) — delegate to the unified Phase 5 policy
      :func:`gui.chart_tab.gpu_config.should_use_gl_candles`, so the factory
      and the telemetry/adaptive layer never disagree. That policy honours
      ``AGREGAT_GL_CANDLES`` when set and otherwise enables GL whenever the
      OpenGL backend is available. This is an **opt-out** default: the paint
      path falls back to the QPainter renderer on any GL failure (no context,
      shader init failure, …), so "on when available" can only swap the fast
      path in — it can never break rendering. Set ``AGREGAT_GL_CANDLES=0`` to
      force the legacy software path.

    Tests inject ``force=`` to exercise both branches without
    monkey-patching ``os.environ``.
    """
    if force is None:
        # Local import keeps the module import order independent of gpu_config.
        from gui.chart_tab.gpu_config import should_use_gl_candles

        want_gl = should_use_gl_candles()
    else:
        want_gl = bool(force)

    if want_gl and is_gl_candle_available():
        logger.info("ChartTab using GLCandlestickItem (Phase 3 shader path)")
        return GLCandlestickItem(max_bars)

    if want_gl:
        # User asked for GL but the bindings aren't there.
        logger.warning(
            "AGREGAT_GL_CANDLES set but PySide6 GL bindings unavailable; "
            "falling back to software CandlestickItem"
        )
    return CandlestickItem(max_bars)


__all__ = [
    "FLOATS_PER_INSTANCE",
    "FRAGMENT_SHADER_SRC",
    "GLCandlestickItem",
    "VERTEX_SHADER_SRC",
    "is_gl_candle_available",
    "make_candlestick_item",
    "pack_candle_instances",
]
