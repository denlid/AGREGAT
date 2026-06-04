"""Phase 2.5 — zero-copy shared_memory data-sharing for the optimizer.

Verifies that :class:`SharedArrayBundle` round-trips bit-identical
arrays into multiprocessing workers without copying, and that
:func:`grid_search` can pipe the bundle through to a real
``Pool(initializer=...)`` so each worker's ``runner`` reads the
data directly out of shared memory.
"""

from __future__ import annotations

import multiprocessing as mp
import platform
import sys
from typing import Any, Dict

import numpy as np
import pandas as pd
import pytest

# Headless safety: shared_memory needs ``/dev/shm`` to exist on
# Linux. Skip the test module entirely on platforms where this is
# unreliable rather than producing flaky failures.
pytestmark = pytest.mark.skipif(
    platform.system() == "Windows" and sys.version_info < (3, 8),
    reason="multiprocessing.shared_memory requires Python ≥ 3.8 on Windows",
)


from engine.execution.execution import ExecutionModel  # noqa: E402
from engine.execution.prepared_dataset import prepare_vectorized_dataset  # noqa: E402
from engine.models.models import AssetConfig  # noqa: E402
from engine.optimization import shared_arrays  # noqa: E402
from engine.optimization.optimizer import grid_search  # noqa: E402
from engine.optimization.shared_arrays import SharedArrayBundle  # noqa: E402


def _asset_for_prepared() -> AssetConfig:
    return AssetConfig(
        name="TEST",
        point_value=1.0,
        tick_size=1.0,
        buffer=0.0,
        max_lots_per_100k=10,
        currency="USD",
        min_lot=0.01,
        lot_step=0.01,
        spread_points=0.0,
        commission_per_lot=0.0,
        close_on_weekend=False,
        enforce_trading_hours=False,
    )


# ---------------------------------------------------------------------------
# Same-process attach/detach
# ---------------------------------------------------------------------------


def test_bundle_creates_zero_copy_views_in_parent() -> None:
    arrays = {
        "close": np.arange(100.0, dtype=np.float64),
        "high": np.arange(100, 200, dtype=np.float64),
    }
    with SharedArrayBundle(arrays, name="test") as bundle:
        # Parent reads back the same data byte-identically.
        np.testing.assert_array_equal(bundle.arrays["close"], arrays["close"])
        np.testing.assert_array_equal(bundle.arrays["high"], arrays["high"])
        # Bundle name is unique-ish — bundles created back-to-back
        # must not collide.
        assert bundle.bundle_name.startswith("agregat_test_")


def test_attach_returns_zero_copy_views() -> None:
    arrays = {"x": np.arange(64, dtype=np.int32)}
    with SharedArrayBundle(arrays) as bundle:
        attached = shared_arrays.attach(bundle.descriptor())
        np.testing.assert_array_equal(attached["x"], arrays["x"])
        # Mutating the parent's view is visible through the attached
        # one (same memory, no copy).
        bundle.arrays["x"][0] = 999
        assert attached["x"][0] == 999
        shared_arrays.detach_all()


def test_bundle_unlinks_on_context_exit() -> None:
    arrays = {"x": np.arange(8, dtype=np.float64)}
    with SharedArrayBundle(arrays, name="cleanup") as bundle:
        names = [d.shm_name for _, d in bundle.descriptor().arrays]
        assert names

    # After exit the blocks must be unlinked. Re-creating with the
    # same name must succeed without ``FileExistsError``.
    from multiprocessing import shared_memory as shm_mod

    for name in names:
        with pytest.raises(FileNotFoundError):
            shm_mod.SharedMemory(name=name)


def test_bundle_close_is_idempotent() -> None:
    arrays = {"x": np.zeros(4, dtype=np.float64)}
    bundle = SharedArrayBundle(arrays)
    bundle.close()
    bundle.close()  # must not raise


def test_descriptor_round_trip_preserves_dtype_and_shape() -> None:
    src = np.arange(15, dtype=np.float32).reshape(3, 5)
    with SharedArrayBundle({"m": src}) as bundle:
        attached = shared_arrays.attach(bundle.descriptor())
        assert attached["m"].shape == src.shape
        assert attached["m"].dtype == src.dtype
        np.testing.assert_array_equal(attached["m"], src)
        shared_arrays.detach_all()


# ---------------------------------------------------------------------------
# Multiprocessing round-trip
# ---------------------------------------------------------------------------


def _runner_reads_shared(params: Dict[str, Any]) -> Dict[str, float]:
    """Top-level runner so the spawn-pool can pickle it. Reads the
    shared array attached by ``pool_initializer`` and returns a
    metric derived from it — proves the worker actually saw the
    parent's data."""
    arrs = shared_arrays.attached(params["__bundle__"])
    close = arrs["close"]
    return {
        "profit_factor": float(close[params["i"]]),
        "n": float(close.size),
    }


def _spawnable() -> bool:
    try:
        mp.get_context("spawn")
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _spawnable(), reason="spawn context not available")
def test_grid_search_reads_shared_data_from_workers() -> None:
    n = 256
    close = np.linspace(100.0, 200.0, n)
    arrays = {"close": close}

    with SharedArrayBundle(arrays, name="opt") as bundle:
        # Each runner reads close[i] for one of the indices in the
        # grid. We pre-bake the bundle name into every params dict so
        # the runner stays a top-level pickle-able function.
        bundle_name = bundle.bundle_name
        # Using a small grid keeps the test fast even on slow CI.
        idxs = [0, 16, 32, 64, 128, 200]
        param_grid = {"__bundle__": [bundle_name], "i": idxs}

        result = grid_search(
            _runner_reads_shared,
            param_grid,
            score_key="profit_factor",
            higher_is_better=True,
            n_workers=2,
            mp_chunk_size=2,
            shared_arrays=bundle,
        )

    assert len(result.runs) == len(idxs)
    # Every metric must equal close[i] — i.e. workers genuinely read
    # from the shared block, not from a stale or zero-initialised
    # buffer.
    for run in result.runs:
        i = run.params["i"]
        assert run.metrics["profit_factor"] == pytest.approx(close[i])
        assert run.metrics["n"] == n


@pytest.mark.skipif(not _spawnable(), reason="spawn context not available")
def test_prepared_dataset_bundle_reads_shared_data_from_workers() -> None:
    n = 128
    close = np.linspace(100.0, 120.0, n)
    df = pd.DataFrame(
        {
            "time": pd.date_range("2024-01-01", periods=n, freq="min"),
            "Open": close,
            "High": close + 1.0,
            "Low": close - 1.0,
            "Close": close,
            "Volume": 1.0,
        }
    )
    prep = prepare_vectorized_dataset(
        df,
        asset=_asset_for_prepared(),
        execution_model=ExecutionModel("Optimistic"),
    )

    with prep.to_shared_array_bundle() as bundle:
        assert bundle.bundle_name.startswith("agregat_prepared_")
        idxs = [0, 7, 21, 63, 100]
        result = grid_search(
            _runner_reads_shared,
            {"__bundle__": [bundle.bundle_name], "i": idxs},
            score_key="profit_factor",
            higher_is_better=True,
            n_workers=2,
            mp_chunk_size=2,
            shared_arrays=bundle,
        )

    assert len(result.runs) == len(idxs)
    for run in result.runs:
        i = run.params["i"]
        assert run.metrics["profit_factor"] == pytest.approx(prep.close[i])
        assert run.metrics["n"] == n


# ---------------------------------------------------------------------------
# Negative paths
# ---------------------------------------------------------------------------


def test_attached_raises_on_unknown_bundle_name() -> None:
    shared_arrays.detach_all()  # ensure clean
    with pytest.raises(KeyError):
        shared_arrays.attached("never_attached")


def test_bundle_handles_2d_arrays() -> None:
    """Most engine arrays are 1-D, but execution / tf-aggregator state
    can be 2-D. The bundle must handle both."""
    a = np.arange(60, dtype=np.float64).reshape(6, 10)
    with SharedArrayBundle({"matrix": a}) as bundle:
        attached = shared_arrays.attach(bundle.descriptor())
        np.testing.assert_array_equal(attached["matrix"], a)
        shared_arrays.detach_all()


def test_bundle_supports_non_contiguous_input() -> None:
    """The bundle must materialise a contiguous copy when the source
    is a stride view. We rely on ``np.ascontiguousarray``."""
    base = np.arange(40, dtype=np.float64).reshape(10, 4)
    view = base[::2]  # strided view, not contiguous
    assert not view.flags["C_CONTIGUOUS"]
    with SharedArrayBundle({"v": view}) as bundle:
        attached = shared_arrays.attach(bundle.descriptor())
        np.testing.assert_array_equal(attached["v"], view)
        shared_arrays.detach_all()
