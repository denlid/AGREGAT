"""
AGREGAT Backtest Cache — Sprint 4.7

LRU cache for backtest results.
"""
from __future__ import annotations
import hashlib, json, logging
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import numpy as np
import pandas as pd
logger = logging.getLogger("StrategyTester.engine.cache")


# ---------------------------------------------------------------------------
# Cache key computation
# ---------------------------------------------------------------------------
def _hash_strategy(strategy: Any) -> str:
    import inspect
    cls = strategy.__class__
    try:
        source = inspect.getsource(cls)
    except (OSError, TypeError):
        source = f"{cls.__module__}.{cls.__name__}"
    params = getattr(strategy, "params", {})
    params_str = json.dumps(params, sort_keys=True, default=str)
    raw = f"{source}|||{params_str}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()

def _hash_dataframe(df: pd.DataFrame) -> str:
    cols = [c for c in ["time","open","high","low","close","volume"] if c in df.columns]
    n = len(df)
    if n <= 200:
        subset = df[cols].to_csv(index=False)
    else:
        head = df[cols].head(100).to_csv(index=False)
        tail = df[cols].tail(100).to_csv(index=False)
        subset = f"{n}|||{head}|||{tail}"
    return hashlib.sha256(subset.encode("utf-8")).hexdigest()

def _hash_asset(asset: Any) -> str:
    """Hash for AssetConfig (__slots__) or regular objects."""
    d = {}
    for k in getattr(asset, "__slots__", []):
        d[k] = str(getattr(asset, k, ""))
    if hasattr(asset, "__dict__"):
        d.update({k: str(v) for k, v in vars(asset).items() if not k.startswith("_")})
    return hashlib.sha256(json.dumps(d, sort_keys=True, default=str).encode()).hexdigest()

def _hash_params(**extra: Any) -> str:
    return hashlib.sha256(json.dumps(extra, sort_keys=True, default=str).encode()).hexdigest()

def make_cache_key(strategy: Any, data: pd.DataFrame, asset: Any, **params: Any) -> str:
    parts = [_hash_strategy(strategy), _hash_dataframe(data), _hash_asset(asset), _hash_params(**params)]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Cache entry
# ---------------------------------------------------------------------------
@dataclass
class CacheEntry:
    key: str = ""
    trades: List[Dict[str, Any]] = field(default_factory=list)
    equity_curve_values: List[float] = field(default_factory=list)
    metrics: Dict[str, float] = field(default_factory=dict)
    equity_track: List[float] = field(default_factory=list)
    created_at: Optional[str] = None
    hit_count: int = 0

@dataclass
class BacktestCacheStats:
    size: int = 0
    maxsize: int = 0
    hits: int = 0
    misses: int = 0
    evictions: int = 0

    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return (self.hits / total * 100) if total > 0 else 0.0


# ---------------------------------------------------------------------------
# LRU cache
# ---------------------------------------------------------------------------
class BacktestCache:
    def __init__(self, maxsize: int = 32) -> None:
        self._maxsize = max(int(maxsize), 0)
        self._entries: OrderedDict[str, CacheEntry] = OrderedDict()
        self._hits = 0
        self._misses = 0
        self._evictions = 0
        logger.info("BacktestCache initialised (maxsize=%d, enabled=%s)", self._maxsize, self._maxsize > 0)

    @property
    def enabled(self) -> bool:
        return self._maxsize > 0

    @property
    def stats(self) -> BacktestCacheStats:
        return BacktestCacheStats(
            size=len(self._entries), maxsize=self._maxsize,
            hits=self._hits, misses=self._misses, evictions=self._evictions,
        )

    def get(self, key: str) -> Optional[CacheEntry]:
        if not self.enabled:
            return None
        entry = self._entries.get(key)
        if entry is None:
            self._misses += 1
            return None
        self._entries.move_to_end(key)
        entry.hit_count += 1
        self._hits += 1
        return entry

    def put(self, key: str, entry: CacheEntry) -> None:
        if not self.enabled:
            return
        if key in self._entries:
            self._entries[key] = entry
            self._entries.move_to_end(key)
            return
        if len(self._entries) >= self._maxsize:
            evicted_key, _ = self._entries.popitem(last=False)
            self._evictions += 1
            logger.debug("Cache evicted: %s…", evicted_key[:12])
        self._entries[key] = entry
        logger.debug("Cache stored: %s… (size=%d)", key[:12], len(self._entries))

    def clear(self) -> None:
        self._entries.clear()
        self._hits = self._misses = self._evictions = 0
        logger.info("BacktestCache cleared")

    def invalidate(self, key: str) -> bool:
        if key in self._entries:
            del self._entries[key]
            return True
        return False

    def __len__(self) -> int:
        return len(self._entries)

    def __repr__(self) -> str:
        return (
            f"BacktestCache(size={len(self._entries)}/{self._maxsize}, "
            f"hits={self._hits}, misses={self._misses}, evictions={self._evictions})"
        )


# ---------------------------------------------------------------------------
# Integration helpers: serialise / deserialise backtest results
# ---------------------------------------------------------------------------
def _trade_to_dict(trade: Any) -> Dict[str, Any]:
    d = {}
    items = getattr(trade, '__dict__', None) or vars(trade) if hasattr(trade, '__dict__') else {}
    for k, v in items.items():
        if k.startswith('_'):
            continue
        if v is None or isinstance(v, (bool, int, float, str)):
            d[k] = v
        elif isinstance(v, (pd.Timestamp, pd.Period)):
            d[k] = str(v)
        elif isinstance(v, np.generic):
            d[k] = v.item()
        else:
            d[k] = str(v)
    return d

def build_cache_entry(result: Any, key: str) -> CacheEntry:
    trades = [_trade_to_dict(t) for t in getattr(result, 'trades', [])]
    eq_curve = getattr(result, 'equity_curve', None)
    if isinstance(eq_curve, pd.Series):
        eq_values = eq_curve.tolist()
    elif isinstance(eq_curve, (list, tuple)):
        eq_values = list(eq_curve)
    else:
        eq_values = []
    metrics = {k: float(v) if not isinstance(v, str) else v for k, v in (getattr(result, 'metrics', {}) or {}).items()}
    equity_track = list(getattr(result, 'equity_track', []))
    return CacheEntry(
        key=key, trades=trades, equity_curve_values=eq_values,
        metrics=metrics, equity_track=equity_track,
        created_at=datetime.now(timezone.utc).isoformat(),
    )

def rebuild_result(entry: CacheEntry) -> Any:
    from engine.sdk import BacktestResult
    return BacktestResult(
        trades=entry.trades,
        equity_curve=pd.Series(entry.equity_curve_values, dtype='float64'),
        metrics=entry.metrics,
        equity_track=entry.equity_track,
    )

__all__ = [
    'BacktestCache', 'BacktestCacheStats', 'CacheEntry',
    'build_cache_entry', 'make_cache_key', 'rebuild_result',
]
