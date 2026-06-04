"""Tests for engine-side feature cache (Roadmap v2 / S1.5).

Verifies that the cross-run feature cache:
1. Reuses features when dataset + feature_params are unchanged.
2. Rebuilds features when feature_params change.
3. Respects LRU eviction.
4. Produces identical results (parity) to the no-cache path.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from engine.execution.execution import ExecutionModel
from engine.execution.feature_cache import (
    FeatureCache,
    make_feature_cache_key,
    get_global_feature_cache,
)
from engine.execution.prepared_dataset import prepare_vectorized_dataset
from engine.execution.vectorized_engine import run_vectorized
from engine.models.models import AssetConfig
from strategies.ema_crossover_cached import EMACrossoverFeatureCached


# ----------------------------------------------------------------------------
# FeatureCache unit tests
# ----------------------------------------------------------------------------
def test_feature_cache_get_miss_returns_none():
    cache = FeatureCache()
    assert cache.get(("ds1", "Strat", "(12, 26)")) is None


def test_feature_cache_put_and_get():
    cache = FeatureCache()
    key = ("ds1", "Strat", "(12, 26)")
    cache.put(key, {"data": [1, 2, 3]})
    assert cache.get(key) == {"data": [1, 2, 3]}


def test_feature_cache_lru_eviction():
    cache = FeatureCache(max_entries=2)
    cache.put(("ds", "S", "a"), 1)
    cache.put(("ds", "S", "b"), 2)
    cache.put(("ds", "S", "c"), 3)  # Evicts "a" (oldest).
    assert cache.get(("ds", "S", "a")) is None
    assert cache.get(("ds", "S", "b")) == 2
    assert cache.get(("ds", "S", "c")) == 3


def test_feature_cache_lru_marks_recently_used():
    cache = FeatureCache(max_entries=2)
    cache.put(("ds", "S", "a"), 1)
    cache.put(("ds", "S", "b"), 2)
    cache.get(("ds", "S", "a"))  # "a" now most recently used.
    cache.put(("ds", "S", "c"), 3)  # Evicts "b", not "a".
    assert cache.get(("ds", "S", "a")) == 1
    assert cache.get(("ds", "S", "b")) is None


def test_feature_cache_stats():
    cache = FeatureCache()
    key = ("ds", "S", "a")
    cache.get(key)  # miss
    cache.put(key, 1)
    cache.get(key)  # hit
    stats = cache.stats()
    assert stats["hits"] == 1
    assert stats["misses"] == 1
    assert stats["hit_rate"] == 0.5


def test_make_cache_key_none_when_no_dataset_key():
    s = EMACrossoverFeatureCached()
    assert make_feature_cache_key(None, s, (12, 26)) is None


def test_make_cache_key_none_when_no_feature_params():
    s = EMACrossoverFeatureCached()
    assert make_feature_cache_key("ds1", s, None) is None


def test_make_cache_key_stable():
    s = EMACrossoverFeatureCached()
    k1 = make_feature_cache_key("ds1", s, (12, 26))
    k2 = make_feature_cache_key("ds1", s, (12, 26))
    assert k1 == k2
    assert k1 == ("ds1", "EMACrossoverFeatureCached", "(12, 26)")


# ----------------------------------------------------------------------------
# Integration tests (engine + cache)
# ----------------------------------------------------------------------------
def _df(n: int = 1000) -> pd.DataFrame:
    idx = pd.date_range("2024-01-02 10:00", periods=n, freq="1min")
    trend = 100.0 + 10.0 * np.sin(np.arange(n) / 50.0)
    noise = np.cumsum(np.random.default_rng(42).normal(0, 0.5, n))
    close = trend + noise
    return pd.DataFrame(
        {
            "time": idx,
            "open": close - 0.3,
            "high": close + 0.5,
            "low": close - 0.5,
            "close": close,
            "volume": 1.0,
        }
    )


def _asset() -> AssetConfig:
    return AssetConfig(
        name="TEST", point_value=1.0, tick_size=1.0, buffer=0.0,
        max_lots_per_100k=10, currency="USD", min_lot=0.01, lot_step=0.01,
        spread_points=0.0, commission_per_lot=0.0, enforce_trading_hours=False,
    )


def test_engine_cache_reused_across_runs_same_feature_params():
    """Features cached when sl/tp vary but fast/slow_ema fixed."""
    df, asset, em = _df(), _asset(), ExecutionModel("Optimistic")
    prepared = prepare_vectorized_dataset(df, asset=asset, execution_model=em)
    cache = get_global_feature_cache()
    cache.clear()

    # First run: cache miss (builds features).
    s1 = EMACrossoverFeatureCached()
    s1.apply_params({"fast_ema": 12, "slow_ema": 26, "sl_pts": 30.0, "tp_pts": 60.0})
    run_vectorized(df, s1, asset=asset, execution_model=em, prepared=prepared)

    # Second run: same fast/slow_ema, different sl/tp → cache HIT.
    s2 = EMACrossoverFeatureCached()
    s2.apply_params({"fast_ema": 12, "slow_ema": 26, "sl_pts": 50.0, "tp_pts": 100.0})
    run_vectorized(df, s2, asset=asset, execution_model=em, prepared=prepared)

    stats = cache.stats()
    assert stats["hits"] >= 1, f"Expected cache hit, got stats={stats}"


def test_engine_cache_rebuilds_on_feature_param_change():
    """Features rebuilt when fast/slow_ema change."""
    df, asset, em = _df(), _asset(), ExecutionModel("Optimistic")
    prepared = prepare_vectorized_dataset(df, asset=asset, execution_model=em)
    cache = get_global_feature_cache()
    cache.clear()

    s1 = EMACrossoverFeatureCached()
    s1.apply_params({"fast_ema": 12, "slow_ema": 26, "sl_pts": 30.0, "tp_pts": 60.0})
    run_vectorized(df, s1, asset=asset, execution_model=em, prepared=prepared)

    # Different fast_ema → different cache key → miss (rebuild).
    s2 = EMACrossoverFeatureCached()
    s2.apply_params({"fast_ema": 5, "slow_ema": 26, "sl_pts": 30.0, "tp_pts": 60.0})
    run_vectorized(df, s2, asset=asset, execution_model=em, prepared=prepared)

    stats = cache.stats()
    assert stats["misses"] >= 2, f"Expected 2 misses (different params), got {stats}"


def test_engine_cache_no_caching_without_prepared():
    """No caching when prepared dataset not passed (no dataset_key)."""
    df, asset, em = _df(), _asset(), ExecutionModel("Optimistic")
    cache = get_global_feature_cache()
    cache.clear()

    s = EMACrossoverFeatureCached()
    s.apply_params({"fast_ema": 12, "slow_ema": 26, "sl_pts": 30.0, "tp_pts": 60.0})
    # No prepared → no caching.
    run_vectorized(df, s, asset=asset, execution_model=em)

    stats = cache.stats()
    assert stats["size"] == 0, f"Expected empty cache without prepared, got {stats}"


def test_engine_cache_parity_with_no_cache():
    """Cached run produces identical metrics to a fresh (no-cache) run."""
    df, asset, em = _df(), _asset(), ExecutionModel("Optimistic")
    prepared = prepare_vectorized_dataset(df, asset=asset, execution_model=em)
    cache = get_global_feature_cache()

    # Run twice with same params — second hits cache. Results must match.
    cache.clear()
    s1 = EMACrossoverFeatureCached()
    s1.apply_params({"fast_ema": 12, "slow_ema": 26, "sl_pts": 30.0, "tp_pts": 60.0})
    res1 = run_vectorized(df, s1, asset=asset, execution_model=em, prepared=prepared)

    s2 = EMACrossoverFeatureCached()
    s2.apply_params({"fast_ema": 12, "slow_ema": 26, "sl_pts": 30.0, "tp_pts": 60.0})
    res2 = run_vectorized(df, s2, asset=asset, execution_model=em, prepared=prepared)

    for key in ("total_return_pct", "final_equity", "n_trades"):
        assert res1.summary[key] == pytest.approx(res2.summary[key], rel=1e-9)
