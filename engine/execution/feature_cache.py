"""Engine-side feature cache for vectorized runs (Roadmap v2 / S1.5).

Caches built feature bundles across run_vectorized calls keyed on
(dataset_key, strategy_class, feature_params). This gives the feature-cache
contract a real speedup on optimizer sweeps: when params vary only execution
thresholds (SL/TP), the heavy indicator features are computed once and reused.

The cache is a bounded LRU to prevent memory blow-up — feature bundles can be
large (many indicator arrays × bars), so we cap the number of resident bundles.

Thread-safety: guarded by a lock since optimizer workers may share the cache.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from typing import Any, Optional, Tuple

# Default cap on resident feature bundles. Each bundle may hold several
# float64 arrays of length N, so for 1M-bar datasets a single bundle can be
# tens of MB. Keep the default conservative; callers can override.
_DEFAULT_MAX_ENTRIES = 8

# Cache key: (dataset_key, strategy_class_name, feature_params_repr).
FeatureCacheKey = Tuple[str, str, str]


class FeatureCache:
    """Bounded LRU cache for vectorized feature bundles.

    Keyed on (dataset_key, strategy_class, feature_params). Evicts the
    least-recently-used bundle when the entry cap is exceeded.
    """

    def __init__(self, max_entries: int = _DEFAULT_MAX_ENTRIES) -> None:
        self._max_entries = int(max_entries)
        self._store: "OrderedDict[FeatureCacheKey, Any]" = OrderedDict()
        self._lock = threading.RLock()
        self._hits = 0
        self._misses = 0

    def get(self, key: FeatureCacheKey) -> Optional[Any]:
        """Return cached bundle for key, or None on miss. Marks as recently used."""
        with self._lock:
            if key in self._store:
                self._store.move_to_end(key)  # Mark as recently used.
                self._hits += 1
                return self._store[key]
            self._misses += 1
            return None

    def put(self, key: FeatureCacheKey, value: Any) -> None:
        """Store bundle for key, evicting LRU entry if over capacity."""
        with self._lock:
            if key in self._store:
                self._store.move_to_end(key)
            self._store[key] = value
            while len(self._store) > self._max_entries:
                self._store.popitem(last=False)  # Evict oldest.

    def clear(self) -> None:
        """Drop all cached bundles."""
        with self._lock:
            self._store.clear()

    def stats(self) -> dict:
        """Return hit/miss counters and current size."""
        with self._lock:
            total = self._hits + self._misses
            return {
                "hits": self._hits,
                "misses": self._misses,
                "size": len(self._store),
                "max_entries": self._max_entries,
                "hit_rate": (self._hits / total) if total > 0 else 0.0,
            }


def make_feature_cache_key(
    dataset_key: Optional[str],
    strategy: Any,
    feature_params: Any,
) -> Optional[FeatureCacheKey]:
    """Build a cache key from dataset, strategy, and feature params.

    Returns None if caching cannot be keyed safely (missing dataset_key or
    feature_params is None — the strategy opted out of caching).

    Args:
        dataset_key: PreparedDataset.dataset_key (stable hash of OHLCV+config).
        strategy: The strategy instance (used for its class name).
        feature_params: Hashable representation of params that affect features.
            None means the strategy didn't declare feature_params → no caching.

    Returns:
        Cache key tuple, or None if caching should be skipped.
    """
    if dataset_key is None or feature_params is None:
        return None
    strategy_class = type(strategy).__name__
    # repr() gives a stable string for tuples/lists/dicts of params.
    return (dataset_key, strategy_class, repr(feature_params))


# Module-level shared cache instance. Optimizer runs in the same process share
# this; multiprocess workers each get their own (separate process memory).
_GLOBAL_FEATURE_CACHE = FeatureCache()


def get_global_feature_cache() -> FeatureCache:
    """Return the process-global feature cache instance."""
    return _GLOBAL_FEATURE_CACHE
