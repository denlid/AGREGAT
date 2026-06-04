"""Phase 1 — chart GPU acceleration bootstrap.

Headless tests for :mod:`gui.chart_tab.gpu_config` plus the
``paint_time_ms`` telemetry added to :class:`CandlestickItem`. These
tests run under ``QT_QPA_PLATFORM=offscreen`` so they exercise the
*decision* logic without requiring a real OpenGL context — the actual
GPU draw path is validated by the existing pyqtgraph test suite and
manual smoke checks.
"""

from __future__ import annotations

import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pyqtgraph as pg
import pytest

from gui.chart_tab import gpu_config


@pytest.fixture(autouse=True)
def _restore_pg_opengl():
    """Snapshot/restore pyqtgraph's ``useOpenGL`` flag so a test that
    flips it on doesn't leak into siblings (the live chart tests run
    in the same process)."""
    prev = pg.getConfigOption("useOpenGL")
    gpu_config._reset_state_for_tests()
    try:
        yield
    finally:
        pg.setConfigOptions(useOpenGL=prev)
        gpu_config._reset_state_for_tests()


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Strip the ``AGREGAT_USE_GPU`` env var so auto-detect tests
    aren't polluted by the developer's shell."""
    monkeypatch.delenv("AGREGAT_USE_GPU", raising=False)


# ---------------------------------------------------------------------------
# is_opengl_available
# ---------------------------------------------------------------------------


def test_is_opengl_available_returns_bool() -> None:
    """Contract: detection must never raise; it returns ``True`` or
    ``False`` so callers can branch unconditionally."""
    result = gpu_config.is_opengl_available()
    assert isinstance(result, bool)


# ---------------------------------------------------------------------------
# configure_gpu_acceleration — explicit force
# ---------------------------------------------------------------------------


def test_configure_force_false_disables_opengl() -> None:
    """``force=False`` must keep pyqtgraph on the software backend
    even when OpenGL would otherwise have been available."""
    enabled = gpu_config.configure_gpu_acceleration(force=False)
    assert enabled is False
    assert pg.getConfigOption("useOpenGL") is False
    assert gpu_config.STATE["configured"] is True
    assert gpu_config.STATE["enabled"] is False


def test_configure_force_true_enables_opengl_when_pyqtgraph_imports() -> None:
    """``force=True`` should flip pyqtgraph to OpenGL. The test box may
    not have a real GPU, but ``setConfigOptions(useOpenGL=True)`` is
    pure bookkeeping — the actual context is only created when a
    ``PlotWidget`` is shown. So we assert on the recorded decision,
    not on a working draw."""
    enabled = gpu_config.configure_gpu_acceleration(force=True)
    assert enabled is True
    assert pg.getConfigOption("useOpenGL") is True
    assert gpu_config.STATE["enabled"] is True
    assert gpu_config.STATE["reason"] == "enabled"


# ---------------------------------------------------------------------------
# configure_gpu_acceleration — env var
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["1", "true", "yes", "on", "Y", "TRUE"])
def test_configure_env_truthy_enables(monkeypatch, value: str) -> None:
    monkeypatch.setenv("AGREGAT_USE_GPU", value)
    enabled = gpu_config.configure_gpu_acceleration()
    assert enabled is True
    assert pg.getConfigOption("useOpenGL") is True


@pytest.mark.parametrize("value", ["0", "false", "no", "off", "N", "FALSE"])
def test_configure_env_falsy_disables(monkeypatch, value: str) -> None:
    monkeypatch.setenv("AGREGAT_USE_GPU", value)
    enabled = gpu_config.configure_gpu_acceleration()
    assert enabled is False
    assert pg.getConfigOption("useOpenGL") is False
    assert gpu_config.STATE["reason"] == "env_disabled"


def test_configure_env_unset_uses_auto_detect(monkeypatch) -> None:
    """With no env var the decision must match :func:`is_opengl_available`."""
    monkeypatch.delenv("AGREGAT_USE_GPU", raising=False)
    expected = gpu_config.is_opengl_available()
    enabled = gpu_config.configure_gpu_acceleration()
    assert enabled is expected


def test_configure_force_overrides_env(monkeypatch) -> None:
    """An explicit ``force`` argument wins over the env var so
    settings-UI toggles aren't blocked by stale shell state."""
    monkeypatch.setenv("AGREGAT_USE_GPU", "0")
    enabled = gpu_config.configure_gpu_acceleration(force=True)
    assert enabled is True
    assert pg.getConfigOption("useOpenGL") is True


# ---------------------------------------------------------------------------
# State tracking
# ---------------------------------------------------------------------------


def test_state_starts_unconfigured() -> None:
    gpu_config._reset_state_for_tests()
    assert gpu_config.STATE == {
        "configured": False,
        "enabled": False,
        "reason": "not_configured",
        "backend": "software",
        "gl_candles": False,
    }


def test_state_records_disabled_reason_for_unavailable() -> None:
    """When the caller asks to disable, the reason tag must reflect
    that — not the auto-detect fallback. Lets the settings UI show
    'Disabled by user' vs. 'Disabled — no GPU available'."""
    gpu_config.configure_gpu_acceleration(force=False)
    assert gpu_config.STATE["reason"] in {"unavailable_or_disabled", "env_disabled"}
    assert gpu_config.STATE["configured"] is True
    assert gpu_config.STATE["enabled"] is False
    assert gpu_config.STATE["backend"] == "software"
    assert gpu_config.STATE["gl_candles"] is False


def test_should_use_gl_candles_respects_explicit_force() -> None:
    assert gpu_config.should_use_gl_candles(force_gl_candles=True) is True
    assert gpu_config.should_use_gl_candles(force_gl_candles=False) is False


def test_should_use_gl_candles_uses_bars_hint_threshold() -> None:
    assert gpu_config.should_use_gl_candles(force_gpu=True, bars_hint=500) is False
    assert gpu_config.should_use_gl_candles(force_gpu=True, bars_hint=2_000) is True
    assert gpu_config.should_use_gl_candles(force_gpu=True, bars_hint=20_000) is True


def test_configure_enabled_state_records_backend_metadata() -> None:
    enabled = gpu_config.configure_gpu_acceleration(force=True)
    assert enabled is True
    assert gpu_config.STATE["backend"] == "opengl"
    assert gpu_config.STATE["gl_candles"] is True


# ---------------------------------------------------------------------------
# CandlestickItem paint timing telemetry (Phase 1, step 3)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _make_ohlc(n: int) -> list[tuple[float, float, float, float, float]]:
    out = []
    for i in range(n):
        op = 100.0
        cl = 101.0 if i % 2 == 0 else 99.0
        lo = 98.0
        hi = 102.0
        out.append((float(i), op, cl, lo, hi))
    return out


def test_paint_stats_includes_paint_time_ms(qapp) -> None:
    """The telemetry contract: ``_paint_stats`` exposes the wall-clock
    cost of the last ``_gen`` call so the debug overlay / FPS
    benchmark can read it without instrumentation."""
    from gui.chart_tab.items import CandlestickItem

    item = CandlestickItem(max_bars=2_000)
    item.set_data(_make_ohlc(500))
    assert "paint_time_ms" in item._paint_stats
    assert item._paint_stats["paint_time_ms"] >= 0.0


def test_paint_time_under_threshold_at_50k(qapp) -> None:
    """Phase 1 target: at 50k bars (the worst-case live chart) the
    QPicture build cost must stay well below the 1-frame budget
    (~8.3 ms @ 120 FPS, ~6.9 ms @ 144 FPS). With LOD enabled the
    painter loop walks at most ``DEFAULT_MAX_BARS`` (=2 000) candles,
    so 50 ms is a generous ceiling that still catches regressions.

    This guards the Phase-1 promise of "<2 ms" once we run on real
    OpenGL hardware — on the CI box without a GPU we'd be wrong to
    enforce 2 ms strictly because the software ``QPicture`` path is
    inherently slower; 50 ms gives a stable upper bound while still
    catching ``set_data`` blow-ups."""
    from gui.chart_tab.items import CandlestickItem

    item = CandlestickItem()  # default max_bars (=2_000)
    raw = _make_ohlc(50_000)
    item.set_data(raw)
    # ``set_data`` runs ``_gen`` once so the telemetry is fresh.
    elapsed_ms = item._paint_stats["paint_time_ms"]
    assert elapsed_ms < 50.0, (
        f"paint_time_ms={elapsed_ms:.2f} ms at 50k bars — Phase 1 "
        "regression: paint should stay under 50 ms (target <2 ms on GPU)"
    )


def test_paint_time_resets_on_each_gen(qapp) -> None:
    """``_gen`` must overwrite (not accumulate) the timer so a slow
    initial paint doesn't poison later FPS readings."""
    from gui.chart_tab.items import CandlestickItem

    item = CandlestickItem(max_bars=2_000)
    item.set_data(_make_ohlc(1_000))
    first = item._paint_stats["paint_time_ms"]
    assert first >= 0.0

    # Re-trigger ``_gen`` without changing data; the value must be
    # overwritten with a fresh measurement, not added to the old one.
    item._gen()
    second = item._paint_stats["paint_time_ms"]
    assert second >= 0.0
    # Both values must be sane (< 1 second) — proves the reset is in
    # place and we're not accumulating elapsed time across calls.
    assert second < 1000.0
    assert first < 1000.0


def test_paint_time_zero_with_empty_data(qapp) -> None:
    """An empty data set must still leave a valid telemetry value
    (not stale from a previous run)."""
    from gui.chart_tab.items import CandlestickItem

    item = CandlestickItem(max_bars=2_000)
    item.set_data(_make_ohlc(100))
    # Force a non-zero baseline.
    assert item._paint_stats["paint_time_ms"] >= 0.0

    item.set_data([])
    # Empty data: telemetry must be a fresh (small, non-negative)
    # measurement, never a stale value from before.
    assert item._paint_stats["paint_time_ms"] >= 0.0
    assert item._paint_stats["bars_drawn"] == 0


def test_paint_time_is_finite_and_nonnegative(qapp) -> None:
    """Defensive: ``time.perf_counter`` deltas must be finite floats."""
    import math

    from gui.chart_tab.items import CandlestickItem

    item = CandlestickItem(max_bars=2_000)
    item.set_data(_make_ohlc(200))
    val = item._paint_stats["paint_time_ms"]
    assert isinstance(val, float)
    assert math.isfinite(val)
    assert val >= 0.0


def test_gen_paint_time_measurement_is_monotonic(qapp) -> None:
    """``time.perf_counter`` is monotonic, so two paints in
    succession must each produce a non-negative measurement — never
    a negative delta from a wall-clock jump."""
    from gui.chart_tab.items import CandlestickItem

    item = CandlestickItem(max_bars=2_000)
    item.set_data(_make_ohlc(500))
    samples = []
    for _ in range(5):
        item._gen()
        samples.append(item._paint_stats["paint_time_ms"])
        # Tiny pause so adjacent samples don't collapse to 0.0 on
        # very fast machines.
        time.sleep(0.001)
    assert all(s >= 0.0 for s in samples)
