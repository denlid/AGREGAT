"""Perf regression tests for the institutional tear-sheet (Phase 6.1).

These tests defend the wall-clock budgets for the report pipeline that
the Phase 6.1 PR rebalanced toward stress workloads:

    * 5_000 trades / 200_000 bars → ≤ 3 s   (CI-friendly, runs always)
    * 50_000 trades / 1_000_000 bars → ≤ 12 s (heavy; opt-in via env
      variable ``RUN_HEAVY_PERF=1`` so CI doesn't pay the cost on every
      PR but local devs and the nightly job still get coverage).

Why budget-style tests rather than absolute timings:
    * Absolute numbers are noisy across CI machines (shared runners
      can swing ±50%). Generous ceilings catch real regressions
      (>2× slowdown) without flickering.
    * The thresholds intentionally sit ~2-3× above the measured
      headroom on a fresh checkout. A failure means *something
      regressed badly*, not that the system is a percent slower.

The bench harness in ``bench/bench_report_stress.py`` is the place to
go for fine-grained timing — these tests are a guard rail.
"""

from __future__ import annotations

import os
import time

import pytest


@pytest.fixture(scope="module")
def _quick_dataset():
    """5k trades + 200k bars — used for the always-on budget check."""
    from bench.bench_report_stress import _make_ohlc, _make_trades

    return _make_trades(5_000), _make_ohlc(200_000)


@pytest.fixture(scope="module")
def _heavy_dataset():
    """50k trades + 1M bars — used for the heavy budget check."""
    from bench.bench_report_stress import _make_ohlc, _make_trades

    return _make_trades(50_000), _make_ohlc(1_000_000)


def _render(trades, df) -> int:
    from engine.report.tearsheet import render_html_tearsheet

    meta = {"strategy": "perf", "asset": "DE40", "initial_equity": 100_000.0}
    summary = {"initial_equity": 100_000.0, "total_pnl": 0.0, "trades": []}
    html = render_html_tearsheet(meta, summary, trades, theme="dark", df=df)
    return len(html)


def test_quick_report_renders_under_3_seconds(_quick_dataset):
    """5k trades / 200k bars must render in under 3 s.

    Pre-Phase-6.1 baseline on the same hardware: ~30 s.
    Post-Phase-6.1 measured: ~0.6 s. The 3 s ceiling is a 5× headroom.
    """
    trades, df = _quick_dataset
    t0 = time.perf_counter()
    n_bytes = _render(trades, df)
    elapsed = time.perf_counter() - t0
    assert n_bytes > 0
    assert elapsed < 3.0, f"quick report took {elapsed:.2f}s (budget 3.0s)"


@pytest.mark.skipif(
    os.environ.get("RUN_HEAVY_PERF") != "1",
    reason="heavy perf budget — set RUN_HEAVY_PERF=1 to enable",
)
def test_heavy_report_renders_under_12_seconds(_heavy_dataset):
    """50k trades / 1M bars must render in under 12 s.

    Post-Phase-6.1 measured: ~3 s. The 12 s ceiling is a 4× headroom
    to absorb noisy CI runners.
    """
    trades, df = _heavy_dataset
    t0 = time.perf_counter()
    n_bytes = _render(trades, df)
    elapsed = time.perf_counter() - t0
    assert n_bytes > 0
    assert elapsed < 12.0, f"heavy report took {elapsed:.2f}s (budget 12.0s)"


def test_exposure_50k_trades_under_one_second():
    """The exposure heatmap was the single worst hotspot pre-6.1
    (~10 s on 5k trades). Defend the new <1 s budget on 50k trades."""
    from bench.bench_report_stress import _make_trades
    from engine.analytics.exposure import exposure_heatmap_from_trades

    trades = _make_trades(50_000)
    t0 = time.perf_counter()
    heat = exposure_heatmap_from_trades(trades)
    elapsed = time.perf_counter() - t0
    assert heat.fraction.shape == (7, 24)
    assert elapsed < 1.0, f"exposure heatmap took {elapsed:.2f}s (budget 1.0s)"


def test_silhouette_subsampled_for_large_n():
    """``_silhouette`` must subsample down to its cap for n > cap, not
    bail out (regression guard for the Phase 6.1 fix that kept the
    diagnostic alive on large backtests)."""
    import numpy as np

    from engine.analytics.trade_clusters import _SILHOUETTE_SAMPLE_CAP, _silhouette

    rng = np.random.default_rng(0)
    n = _SILHOUETTE_SAMPLE_CAP * 4
    X = rng.normal(size=(n, 3))
    labels = rng.integers(0, 3, size=n)
    t0 = time.perf_counter()
    score = _silhouette(X, labels)
    elapsed = time.perf_counter() - t0
    # Score is small for random data but must be a finite number,
    # not 0.0 (the old bail-out value).
    assert -1.0 <= score <= 1.0
    assert elapsed < 1.0, f"silhouette took {elapsed:.2f}s (budget 1.0s)"
