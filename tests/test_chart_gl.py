"""Phase 3 (GPU plan) — GLSL candlestick renderer.

Headless tests for :mod:`gui.chart_tab.gl_candle`. The GL paint path
needs a live OpenGL context which we don't have under
``QT_QPA_PLATFORM=offscreen``; instead these tests exercise:

* :func:`pack_candle_instances` — pure NumPy instance data packer. Verifies
  OHLC layout, color routing (bull vs bear), doji fallback,
  and float32 contiguity for direct VBO upload.
* :func:`make_candlestick_item` — factory env-var routing + safe
  fallback when GL bindings are missing.
* :class:`GLCandlestickItem` — class plumbing only (no GL): instance
  rebuilds on ``set_data`` / ``set_colors`` / ``_do_range_repaint``,
  dirty-flag bookkeeping, fallback to the legacy QPainter path via
  the ``last_backend`` telemetry counter.
* Shader sources — sanity checks they're non-empty GLSL 120 strings
  with the attribute / uniform names the Python side expects.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest

from gui.chart_tab.gl_candle import (
    FLOATS_PER_INSTANCE,
    FRAGMENT_SHADER_SRC,
    GLCandlestickItem,
    VERTEX_SHADER_SRC,
    _qcolor_to_rgb,
    is_gl_candle_available,
    make_candlestick_item,
    pack_candle_instances,
)
from gui.chart_tab.items import CandlestickItem
from engine.utils.env_flag import truthy_env as _truthy_env


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _bull_bear():
    """Default chart colors as float-triples (matches CandlestickItem
    defaults: #26A69A bull, #EF5350 bear)."""
    return (
        (0.149, 0.651, 0.604),
        (0.937, 0.325, 0.314),
    )


# ===========================================================================
# Instance packer — layout & shape
# ===========================================================================


def test_pack_instances_empty_returns_empty_array() -> None:
    bull, bear = _bull_bear()
    arr = np.empty((0, 5), dtype=np.float64)
    res = pack_candle_instances(arr, bull, bear)
    assert res.shape == (0, FLOATS_PER_INSTANCE)
    assert res.dtype == np.float32


def test_pack_instances_none_returns_empty_array() -> None:
    bull, bear = _bull_bear()
    res = pack_candle_instances(None, bull, bear)  # type: ignore[arg-type]
    assert res.shape == (0, FLOATS_PER_INSTANCE)


def test_pack_instances_count_matches_input() -> None:
    """N candles → N instances."""
    bull, bear = _bull_bear()
    arr = np.array(
        [[0, 100, 102, 99, 103], [1, 102, 101, 100, 103]], dtype=np.float64
    )
    res = pack_candle_instances(arr, bull, bear)
    assert res.shape[0] == 2


def test_pack_instances_float32_contiguous_for_vbo() -> None:
    bull, bear = _bull_bear()
    arr = np.array([[0, 100, 102, 99, 103]], dtype=np.float64)
    res = pack_candle_instances(arr, bull, bear)
    assert res.dtype == np.float32
    assert res.flags["C_CONTIGUOUS"]


def test_pack_instances_invalid_shape_raises() -> None:
    bull, bear = _bull_bear()
    bad = np.zeros((3, 3), dtype=np.float64)
    with pytest.raises(ValueError):
        pack_candle_instances(bad, bull, bear)


# ===========================================================================
# Instance packer — geometry correctness
# ===========================================================================


def test_pack_instances_values_aligned() -> None:
    """Instance data: [x, op, cl, hi, lo, r, g, b]."""
    bull, bear = _bull_bear()
    arr = np.array([[10.0, 100.0, 102.0, 99.0, 103.0]], dtype=np.float64)
    res = pack_candle_instances(arr, bull, bear)
    assert res[0, 0] == 10.0 # x
    assert res[0, 1] == 100.0 # open
    assert res[0, 2] == 102.0 # close
    assert res[0, 3] == 103.0 # high
    assert res[0, 4] == 99.0 # low


# ===========================================================================
# Instance packer — color routing
# ===========================================================================


def test_pack_instances_bull_candle_uses_bull_color() -> None:
    bull, bear = _bull_bear()
    arr = np.array([[0, 100, 102, 99, 103]], dtype=np.float64)  # close > open
    res = pack_candle_instances(arr, bull, bear)
    assert (float(res[0, 5]), float(res[0, 6]), float(res[0, 7])) == pytest.approx(bull, abs=1e-3)


def test_pack_instances_bear_candle_uses_bear_color() -> None:
    bull, bear = _bull_bear()
    arr = np.array([[0, 102, 100, 99, 103]], dtype=np.float64)  # close < open
    res = pack_candle_instances(arr, bull, bear)
    assert (float(res[0, 5]), float(res[0, 6]), float(res[0, 7])) == pytest.approx(bear, abs=1e-3)


def test_pack_instances_doji_uses_bull_color() -> None:
    bull, bear = _bull_bear()
    arr = np.array([[0, 100, 100, 99, 101]], dtype=np.float64)
    res = pack_candle_instances(arr, bull, bear)
    assert (float(res[0, 5]), float(res[0, 6]), float(res[0, 7])) == pytest.approx(bull, abs=1e-3)


def test_pack_instances_mixed_colors_per_candle() -> None:
    """N=3 mixed: bull, bear, bull. Each instance has its own color."""
    bull, bear = _bull_bear()
    arr = np.array(
        [
            [0, 100, 101, 99, 102],  # bull
            [1, 101, 100, 99, 102],  # bear
            [2, 100, 100, 99, 102],  # doji → bull
        ],
        dtype=np.float64,
    )
    res = pack_candle_instances(arr, bull, bear)
    expected = [bull, bear, bull]
    for i in range(3):
        color = (float(res[i, 5]), float(res[i, 6]), float(res[i, 7]))
        assert color == pytest.approx(expected[i], abs=1e-3), f"candle {i}"


def test_qcolor_to_rgb_accepts_strings_and_tuples() -> None:
    """The helper accepts both hex strings and (r, g, b) tuples so
    callers can pass either pyqtgraph theme colors or pre-normalised
    floats."""
    r, g, b = _qcolor_to_rgb("#FF0000")
    assert r == pytest.approx(1.0) and g == pytest.approx(0.0) and b == pytest.approx(0.0)
    r, g, b = _qcolor_to_rgb((0.5, 0.25, 0.75))
    assert (r, g, b) == (0.5, 0.25, 0.75)


def test_qcolor_to_rgb_rejects_unsupported() -> None:
    with pytest.raises(TypeError):
        _qcolor_to_rgb(42)  # type: ignore[arg-type]


# ===========================================================================
# Factory + env-var routing
# ===========================================================================


def test_truthy_env_parses_known_values() -> None:
    for v in ("1", "true", "yes", "on", "Y", "TRUE"):
        assert _truthy_env(v) is True, v
    for v in ("0", "false", "no", "off", "n", ""):
        assert _truthy_env(v) is False, v
    for v in ("maybe", "asdf", None):
        assert _truthy_env(v) is None, v


def test_make_candlestick_item_default_uses_gl_when_available(monkeypatch, qapp) -> None:
    """Opt-out default: env unset + OpenGL available → GL subclass.

    The paint path still falls back to the QPainter renderer if a live GL
    context can't be created (see ``GLCandlestickItem._try_paint_gl``), so this
    only swaps in the fast path — it never risks a blank chart."""
    if not is_gl_candle_available():
        pytest.skip("PySide6 GL bindings not available")
    monkeypatch.delenv("AGREGAT_GL_CANDLES", raising=False)
    monkeypatch.delenv("AGREGAT_USE_GPU", raising=False)
    monkeypatch.setattr("gui.chart_tab.gpu_config.is_opengl_available", lambda: True)
    item = make_candlestick_item()
    assert isinstance(item, GLCandlestickItem)


def test_make_candlestick_item_default_falls_back_when_gl_unavailable(monkeypatch, qapp) -> None:
    """Opt-out default: env unset + OpenGL NOT available → legacy item."""
    monkeypatch.delenv("AGREGAT_GL_CANDLES", raising=False)
    monkeypatch.delenv("AGREGAT_USE_GPU", raising=False)
    monkeypatch.setattr("gui.chart_tab.gpu_config.is_opengl_available", lambda: False)
    item = make_candlestick_item()
    assert isinstance(item, CandlestickItem)
    assert not isinstance(item, GLCandlestickItem)


def test_make_candlestick_item_force_true_returns_gl(qapp) -> None:
    """``force=True`` always returns the GL subclass when bindings
    are available — bypasses env detection."""
    if not is_gl_candle_available():
        pytest.skip("PySide6 GL bindings not available")
    item = make_candlestick_item(force=True)
    assert isinstance(item, GLCandlestickItem)


def test_make_candlestick_item_force_false_returns_legacy(qapp) -> None:
    """``force=False`` always returns the legacy item even if env
    asks for GL — explicit override wins."""
    item = make_candlestick_item(force=False)
    assert isinstance(item, CandlestickItem)
    assert not isinstance(item, GLCandlestickItem)


def test_make_candlestick_item_env_truthy_returns_gl(monkeypatch, qapp) -> None:
    """``AGREGAT_GL_CANDLES=1`` flips the default."""
    if not is_gl_candle_available():
        pytest.skip("PySide6 GL bindings not available")
    monkeypatch.setenv("AGREGAT_GL_CANDLES", "1")
    item = make_candlestick_item()
    assert isinstance(item, GLCandlestickItem)


def test_make_candlestick_item_env_falsy_returns_legacy(monkeypatch, qapp) -> None:
    monkeypatch.setenv("AGREGAT_GL_CANDLES", "0")
    item = make_candlestick_item()
    assert not isinstance(item, GLCandlestickItem)


def test_make_candlestick_item_force_overrides_env(monkeypatch, qapp) -> None:
    """When ``force`` is set explicitly the env var is ignored."""
    monkeypatch.setenv("AGREGAT_GL_CANDLES", "1")
    item = make_candlestick_item(force=False)
    assert not isinstance(item, GLCandlestickItem)


def test_make_candlestick_item_unrecognised_env_treated_as_unset(
    monkeypatch, qapp
) -> None:
    """An unrecognised value is treated as unset → the policy decides.
    With OpenGL unavailable that resolves to the legacy software item."""
    monkeypatch.setenv("AGREGAT_GL_CANDLES", "perhaps")
    monkeypatch.delenv("AGREGAT_USE_GPU", raising=False)
    monkeypatch.setattr("gui.chart_tab.gpu_config.is_opengl_available", lambda: False)
    item = make_candlestick_item()
    assert not isinstance(item, GLCandlestickItem)


# ===========================================================================
# GLCandlestickItem — vertex caches & dirty-flag bookkeeping
# ===========================================================================


def test_gl_item_inherits_candlestick_surface(qapp) -> None:
    """GLCandlestickItem IS-A CandlestickItem so ChartTab callers
    don't need to know about the swap."""
    item = GLCandlestickItem()
    assert isinstance(item, CandlestickItem)


def test_gl_item_rebuilds_vertices_on_set_data(qapp) -> None:
    item = GLCandlestickItem()
    assert item._gl_instances.shape == (0, FLOATS_PER_INSTANCE)
    item.set_data([(0, 100, 102, 99, 103), (1, 102, 100, 99, 103)])
    assert item._gl_instances.shape[0] == 2
    assert item._gl_verts_dirty is True


def test_gl_item_set_data_empty_zeros_vertex_caches(qapp) -> None:
    item = GLCandlestickItem()
    item.set_data([(0, 100, 102, 99, 103)])
    assert item._gl_instances.shape[0] > 0
    item.set_data([])
    assert item._gl_instances.shape == (0, FLOATS_PER_INSTANCE)


def test_gl_item_set_colors_rebuilds_vertices(qapp) -> None:
    """Changing the chart palette must propagate into the instance
    color attribute."""
    item = GLCandlestickItem()
    item.set_data([(0, 100, 102, 99, 103)])  # bull candle
    first_color = tuple(item._gl_instances[0, 5:8])

    item.set_colors("#000000", "#FFFFFF")  # black bull, white bear
    new_color = tuple(item._gl_instances[0, 5:8])
    assert new_color != first_color
    assert new_color == pytest.approx((0.0, 0.0, 0.0))


def test_gl_item_bounding_rect_covers_data_extent(qapp) -> None:
    item = GLCandlestickItem()
    item.set_data([(0, 100, 102, 99, 103), (10, 95, 98, 90, 100)])
    rect = item.boundingRect()
    # Spans x=0..10 (± width) and y=90..103 minimum.
    assert rect.left() <= 0.0
    assert rect.right() >= 10.0
    assert rect.top() <= 90.0
    assert rect.bottom() >= 103.0


def test_gl_item_bounding_rect_empty_when_no_data(qapp) -> None:
    from PySide6.QtCore import QRectF

    item = GLCandlestickItem()
    assert item.boundingRect() == QRectF()


# ===========================================================================
# GLCandlestickItem — paint fallback
# ===========================================================================


def test_gl_item_paint_falls_back_to_painter_when_no_gl_context(qapp) -> None:
    """Without an active QOpenGLContext, ``_try_paint_gl`` must
    return False and the legacy QPainter path must run. The
    ``last_backend`` counter is how we verify which branch fired."""
    from PySide6.QtGui import QPainter, QPicture

    item = GLCandlestickItem()
    item.set_data([(0, 100, 102, 99, 103), (1, 102, 100, 99, 103)])

    pic = QPicture()
    painter = QPainter(pic)
    try:
        item.paint(painter)
    finally:
        painter.end()

    assert item._paint_stats["last_backend"] == "painter"


def test_gl_item_try_paint_gl_returns_false_for_empty_data(qapp) -> None:
    """No vertices means nothing to draw — the GL helper short-
    circuits before touching the painter so the QPainter fallback
    can run a clean no-op render."""
    item = GLCandlestickItem()
    item.set_data([])
    # We pass None for painter; the early-exit happens before the
    # painter is ever used.
    assert item._try_paint_gl(None) is False  # type: ignore[arg-type]


def test_gl_item_paint_stats_seeded(qapp) -> None:
    """``gl_uploads`` and ``last_backend`` must exist as soon as the
    item is constructed so the debug overlay can read them without
    KeyError."""
    item = GLCandlestickItem()
    assert "gl_uploads" in item._paint_stats
    assert "last_backend" in item._paint_stats
    assert item._paint_stats["gl_uploads"] == 0
    assert item._paint_stats["last_backend"] == "painter"


# ===========================================================================
# GLCandlestickItem — pyramid integration (Phase 2 plumbing inherited)
# ===========================================================================


class _StubView:
    """Headless stand-in for pyqtgraph's ViewBox. Only needs
    ``viewRange`` and a no-op ``sigRangeChanged.connect`` so the
    GLCandlestickItem's ``set_view`` doesn't crash."""

    def __init__(self, x0=0.0, x1=100.0):
        self._x = (x0, x1)

    def viewRange(self):
        return [list(self._x), [0.0, 1.0]]

    def set_x(self, x0, x1):
        self._x = (x0, x1)

    class _NoSig:
        def connect(self, *args, **kwargs):
            pass

    sigRangeChanged = _NoSig()


def test_gl_item_swaps_pyramid_levels_like_legacy(qapp) -> None:
    """Phase 2 pyramid swap logic is inherited verbatim — make sure
    the GL subclass also rebuilds instance cache when the active
    TF changes."""
    import pandas as pd

    from engine.data.lod_pyramid import LodPyramid

    times = pd.date_range("2024-01-01", periods=50_000, freq="1min")
    df = pd.DataFrame(
        {
            "time": times,
            "open": np.full(50_000, 100.0),
            "high": np.full(50_000, 101.0),
            "low": np.full(50_000, 99.0),
            "close": np.full(50_000, 100.5),
            "volume": np.full(50_000, 10),
        }
    )
    pyramid = LodPyramid.from_base(df)

    item = GLCandlestickItem(max_bars=2_000)
    item.set_data(pyramid.to_chart_tuples("1m"))
    initial_count = item._gl_instances.shape[0]

    view = _StubView(0, 30_000)
    item._view = view
    item.set_pyramid(pyramid, max_bars=2_000)
    swapped = item._maybe_swap_pyramid_tf()
    assert swapped is True
    assert item.active_tf == "15m"

    new_count = item._gl_instances.shape[0]
    assert new_count != initial_count, "instance cache should change after TF swap"
    assert new_count > 0


# ===========================================================================
# Shader sources
# ===========================================================================


def test_shader_sources_are_non_empty() -> None:
    assert isinstance(VERTEX_SHADER_SRC, str)
    assert isinstance(FRAGMENT_SHADER_SRC, str)
    assert "void main()" in VERTEX_SHADER_SRC
    assert "void main()" in FRAGMENT_SHADER_SRC


def test_vertex_shader_declares_python_side_names() -> None:
    """The Python side queries these attribute / uniform names by
    string. Any rename here without updating the shader breaks the
    binding silently — lock the contract in."""
    assert "attribute vec2 a_pos" in VERTEX_SHADER_SRC
    assert "attribute vec4 i_data1" in VERTEX_SHADER_SRC
    assert "attribute vec4 i_data2" in VERTEX_SHADER_SRC
    assert "uniform mat4 u_mvp" in VERTEX_SHADER_SRC
    assert "uniform float u_half_width" in VERTEX_SHADER_SRC
    assert "uniform float u_is_wick" in VERTEX_SHADER_SRC


def test_shaders_use_glsl_120() -> None:
    """GLSL 120 is the lowest common denominator the OpenGL viewport
    reliably negotiates. Higher versions need core profile contexts
    pyqtgraph doesn't ask for."""
    assert VERTEX_SHADER_SRC.startswith("#version 120")
    assert FRAGMENT_SHADER_SRC.startswith("#version 120")


def test_is_gl_candle_available_returns_bool() -> None:
    """The probe should never raise — it's used by the factory's
    fallback decision tree."""
    assert isinstance(is_gl_candle_available(), bool)
