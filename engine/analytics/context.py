"""
AnalyticsContext — Single Source of Truth for trade-derived analytics.

Lives in `engine/` so both UI and headless callers can share one canonical
representation of "what the metrics see". Encapsulates:

* Per-trade NumPy arrays (PnL, RR, entry/exit times) materialised once.
* The daily equity curve and daily returns.
* A version counter that is bumped whenever the underlying trade list
  changes; downstream callers detect staleness by comparing `version`.
* An LRU-style metrics bundle cache keyed by (period, filters_token).

Why a fresh class rather than extending `gui.shared_data_cache.SharedTradesCache`?

  `SharedTradesCache` already does similar caching but it is GUI-scoped
  and grew organically around the legacy stats dict. `AnalyticsContext`
  is a smaller, stricter contract that the new metrics worker / cards
  build on. The two can coexist; the old cache continues to power
  legacy paths while the new context drives Phase 0+ work.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from functools import cached_property
from typing import Any, Dict, Hashable, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger("StrategyTester.engine.analytics.context")


# ---------------------------------------------------------------------------
# Filter / period descriptors
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Period:
    """A period selector for metric computation.

    `kind` is one of: 'all', 'last_n', 'date_range', 'rolling_days'.
    For 'last_n' use `n_trades`; for 'date_range' use `start`/`end`;
    for 'rolling_days' use `days` to mean "last N days of equity curve".
    """

    kind: str = "all"
    n_trades: Optional[int] = None
    start: Optional[pd.Timestamp] = None
    end: Optional[pd.Timestamp] = None
    days: Optional[int] = None

    def token(self) -> Tuple[Hashable, ...]:
        return (self.kind, self.n_trades, str(self.start), str(self.end), self.days)


@dataclass(frozen=True)
class Filters:
    """Trade-level filter selector. `None` means "no filter on this dim"."""

    direction: Optional[str] = None  # 'long' | 'short' | None
    sessions: Optional[Tuple[str, ...]] = None
    weekdays: Optional[Tuple[int, ...]] = None
    only_finite_rr: bool = False

    def token(self) -> Tuple[Hashable, ...]:
        return (
            self.direction,
            self.sessions,
            self.weekdays,
            self.only_finite_rr,
        )


# ---------------------------------------------------------------------------
# Metric bundle (immutable result snapshot)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MetricsBundle:
    """Frozen snapshot of metrics for one (period, filters) selection.

    Fields are intentionally a flat dict so callers do not need to know
    every metric name at compile time — keeps Phase-by-Phase additions
    cheap. Use `mb.get('sharpe')` etc.
    """

    period_token: Tuple[Hashable, ...]
    filter_token: Tuple[Hashable, ...]
    n_trades: int
    values: Dict[str, float] = field(default_factory=dict)

    def get(self, key: str, default: float = 0.0) -> float:
        return self.values.get(key, default)


# ---------------------------------------------------------------------------
# AnalyticsContext
# ---------------------------------------------------------------------------


class AnalyticsContext:
    """Single-Source-of-Truth analytics view over a list of `Trade` objects.

    The context is *immutable from the consumer's perspective*: once a
    caller has fetched one of the cached arrays, that array stays valid
    until the context's `bump()` is called (which invalidates everything).

    Lifecycle::

        ctx = AnalyticsContext(engine)
        ctx.refresh()              # snapshots current trades + initial_equity
        arr = ctx.pnl_np           # cached
        bundle = ctx.metrics(Period(), Filters())
        # ... user runs more trades ...
        ctx.bump()                 # marks all caches stale
        ctx.refresh()              # re-snapshots

    The `version` counter is the canonical staleness token — pass it to
    background workers and have them check `ctx.version` before publishing
    results.
    """

    _BUNDLE_LRU_MAX = 16

    def __init__(self, engine: Any) -> None:
        self._engine = engine
        self._lock = threading.RLock()
        self._version: int = 0
        # Snapshot fields — populated by refresh()
        self._trades: List[Any] = []
        self._initial_equity: float = 0.0
        # LRU of (period_token, filter_token) -> MetricsBundle
        self._bundles: Dict[Tuple, MetricsBundle] = {}
        self._bundle_order: List[Tuple] = []
        # Initial snapshot so a freshly-constructed context already
        # reflects whatever trades the engine has.
        self.refresh()

    # ------------------------------------------------------------------
    # Snapshot / invalidation
    # ------------------------------------------------------------------

    @property
    def version(self) -> int:
        return self._version

    @property
    def initial_equity(self) -> float:
        return self._initial_equity

    @property
    def trades(self) -> List[Any]:
        """Return the snapshot list (do not mutate)."""
        return self._trades

    def bump(self) -> None:
        """Mark all caches stale. Call when underlying trades change."""
        with self._lock:
            self._version += 1
            self._invalidate_caches()

    def refresh(self) -> None:
        """Re-snapshot the engine's trade list + initial equity.

        Idempotent if the engine state has not changed (we only bump
        version when the underlying list identity or length differs).
        """
        with self._lock:
            engine = self._engine
            trades = list(getattr(engine, "trades", []) or [])
            init_eq = float(getattr(engine, "initial_equity", 0.0) or 0.0)
            same_list = (
                len(trades) == len(self._trades)
                and init_eq == self._initial_equity
                and (not trades or trades[-1] is self._trades[-1])
            )
            self._trades = trades
            self._initial_equity = init_eq
            if not same_list:
                self._version += 1
                self._invalidate_caches()

    def _invalidate_caches(self) -> None:
        # cached_property values live in __dict__ — drop them all
        for key in (
            "pnl_np",
            "rr_np",
            "entry_time_np",
            "exit_time_np",
            "direction_np",
            "duration_np",
            "equity_series",
            "daily_returns",
        ):
            self.__dict__.pop(key, None)
        self._bundles.clear()
        self._bundle_order.clear()

    # ------------------------------------------------------------------
    # Cached NumPy views
    # ------------------------------------------------------------------

    @cached_property
    def pnl_np(self) -> np.ndarray:
        return np.asarray(
            [float(getattr(t, "pnl", 0.0) or 0.0) for t in self._trades],
            dtype="float64",
        )

    @cached_property
    def rr_np(self) -> np.ndarray:
        n = len(self._trades)
        out = np.empty(n, dtype="float64")
        for i, t in enumerate(self._trades):
            v = getattr(t, "rr", None)
            try:
                out[i] = float(v) if v is not None and np.isfinite(float(v)) else np.nan
            except Exception:
                out[i] = np.nan
        return out

    @cached_property
    def entry_time_np(self) -> np.ndarray:
        out = np.empty(len(self._trades), dtype="datetime64[ns]")
        for i, t in enumerate(self._trades):
            et = getattr(t, "entry_time_np", None)
            if et is None:
                try:
                    et = np.datetime64(pd.Timestamp(t.entry_time))
                except Exception:
                    et = np.datetime64("NaT")
            out[i] = et
        return out

    @cached_property
    def exit_time_np(self) -> np.ndarray:
        out = np.empty(len(self._trades), dtype="datetime64[ns]")
        for i, t in enumerate(self._trades):
            xt = getattr(t, "exit_time_np", None)
            if xt is None:
                try:
                    xt = np.datetime64(pd.Timestamp(t.exit_time))
                except Exception:
                    xt = np.datetime64("NaT")
            out[i] = xt
        return out

    @cached_property
    def direction_np(self) -> np.ndarray:
        return np.asarray(
            [str(getattr(t, "direction", "") or "").lower() for t in self._trades],
            dtype=object,
        )

    @cached_property
    def duration_np(self) -> np.ndarray:
        out = np.empty(len(self._trades), dtype="float64")
        for i, t in enumerate(self._trades):
            try:
                out[i] = float(getattr(t, "duration_minutes", 0.0) or 0.0)
            except Exception:
                out[i] = 0.0
        return out

    @cached_property
    def equity_series(self) -> pd.Series:
        from engine.analytics.equity_curve import build_equity_curve as _build

        return _build(self._trades, self._initial_equity)

    @cached_property
    def daily_returns(self) -> np.ndarray:
        from engine.analytics.equity_curve import daily_returns as _dr

        return _dr(self.equity_series)

    # ------------------------------------------------------------------
    # Filtered views
    # ------------------------------------------------------------------

    def _select(self, period: Period, filters: Filters) -> np.ndarray:
        """Return a boolean mask over `self._trades` for the given (period, filters)."""
        n = len(self._trades)
        if n == 0:
            return np.zeros(0, dtype=bool)
        mask = np.ones(n, dtype=bool)

        # Period
        if period.kind == "last_n" and period.n_trades is not None:
            k = max(0, n - int(period.n_trades))
            sel = np.zeros(n, dtype=bool)
            sel[k:] = True
            mask &= sel
        elif period.kind == "date_range":
            xt = self.exit_time_np
            if period.start is not None:
                mask &= xt >= np.datetime64(period.start)
            if period.end is not None:
                mask &= xt <= np.datetime64(period.end)
        elif period.kind == "rolling_days" and period.days is not None:
            xt = self.exit_time_np
            valid = xt[~np.isnat(xt)]
            if valid.size > 0:
                cutoff = valid.max() - np.timedelta64(int(period.days), "D")
                mask &= xt >= cutoff

        # Filters
        if filters.direction is not None:
            mask &= self.direction_np == filters.direction.lower()
        if filters.sessions is not None:
            sess = np.asarray(
                [str(getattr(t, "session", "") or "") for t in self._trades],
                dtype=object,
            )
            allowed = set(filters.sessions)
            sm = np.asarray([s in allowed for s in sess], dtype=bool)
            mask &= sm
        if filters.weekdays is not None:
            xt = self.exit_time_np
            dow = pd.DatetimeIndex(xt).dayofweek.to_numpy()
            allowed_dow = set(filters.weekdays)
            wm = np.asarray([int(d) in allowed_dow for d in dow], dtype=bool)
            mask &= wm
        if filters.only_finite_rr:
            mask &= np.isfinite(self.rr_np)

        return mask

    # ------------------------------------------------------------------
    # Metrics bundle (LRU)
    # ------------------------------------------------------------------

    def metrics(
        self,
        period: Optional[Period] = None,
        filters: Optional[Filters] = None,
    ) -> MetricsBundle:
        """Return a MetricsBundle for the given selection. LRU-cached."""
        period = period or Period()
        filters = filters or Filters()
        key = (period.token(), filters.token(), self._version)

        with self._lock:
            cached = self._bundles.get(key)
            if cached is not None:
                # Promote to MRU
                try:
                    self._bundle_order.remove(key)
                except ValueError:
                    pass
                self._bundle_order.append(key)
                return cached

        mask = self._select(period, filters)
        bundle = self._compute_bundle(mask, period, filters)

        with self._lock:
            self._bundles[key] = bundle
            self._bundle_order.append(key)
            while len(self._bundle_order) > self._BUNDLE_LRU_MAX:
                ev = self._bundle_order.pop(0)
                self._bundles.pop(ev, None)
        return bundle

    def _compute_bundle(
        self,
        mask: np.ndarray,
        period: Period,
        filters: Filters,
    ) -> MetricsBundle:
        pnl = self.pnl_np[mask] if mask.size else self.pnl_np[:0]
        rr = self.rr_np[mask] if mask.size else self.rr_np[:0]
        n = int(pnl.size)
        values: Dict[str, float] = {}

        if n == 0:
            return MetricsBundle(period.token(), filters.token(), 0, values)

        wins = pnl[pnl > 0]
        losses = pnl[pnl < 0]
        be = pnl[pnl == 0]
        n_w = int(wins.size)
        n_l = int(losses.size)
        n_be = int(be.size)
        gross_p = float(wins.sum()) if n_w else 0.0
        gross_l = float(abs(losses.sum())) if n_l else 0.0
        total_pnl = float(pnl.sum())

        values["n_trades"] = float(n)
        values["n_wins"] = float(n_w)
        values["n_losses"] = float(n_l)
        values["n_be"] = float(n_be)
        values["win_rate"] = (n_w / n * 100.0) if n else 0.0
        values["total_pnl"] = total_pnl
        values["gross_profit"] = gross_p
        values["gross_loss"] = gross_l
        values["avg_win"] = gross_p / n_w if n_w else 0.0
        values["avg_loss"] = gross_l / n_l if n_l else 0.0
        values["best"] = float(pnl.max())
        values["worst"] = float(pnl.min())
        values["expectancy"] = total_pnl / n if n else 0.0
        values["profit_factor"] = (
            gross_p / gross_l if gross_l > 0 else (float("inf") if gross_p > 0 else 0.0)
        )

        rr_finite = rr[np.isfinite(rr)] if rr.size else rr
        values["avg_rr"] = float(rr_finite.mean()) if rr_finite.size > 0 else 0.0

        # Equity-curve metrics (only when we cover the full snapshot)
        # For filtered subsets, equity-curve-based ratios would be wrong,
        # so we only attach them when mask is everything.
        if period.kind == "all" and bool(mask.all()):
            try:
                from engine.analytics.equity_curve import (
                    sharpe_ratio as _sh,
                    sortino_ratio as _so,
                    calmar_ratio as _ca,
                    max_drawdown as _md,
                )

                eq = self.equity_series
                values["sharpe"] = float(_sh(eq))
                values["sortino"] = float(_so(eq))
                values["calmar"] = float(_ca(eq, self._initial_equity))
                dd_abs, dd_pct = _md(eq)
                values["max_dd_abs"] = float(dd_abs)
                values["max_dd_pct"] = float(dd_pct)
            except Exception:
                logger.exception("Equity-curve metrics failed in bundle")

        return MetricsBundle(period.token(), filters.token(), n, values)


# ---------------------------------------------------------------------------
# Convenience: per-engine singleton accessor
# ---------------------------------------------------------------------------

_ATTR = "_analytics_context"


def get_analytics_context(engine: Any) -> AnalyticsContext:
    """Return (creating if needed) the AnalyticsContext attached to `engine`.

    Storing the context on the engine instance means UI tabs do not need a
    central registry — they get the same SSOT just by holding the engine
    reference they already have.
    """
    ctx = getattr(engine, _ATTR, None)
    if ctx is None:
        ctx = AnalyticsContext(engine)
        try:
            setattr(engine, _ATTR, ctx)
        except Exception:
            # Engine may use __slots__; fall back to a per-call instance.
            pass
    else:
        ctx.refresh()
    return ctx


__all__ = [
    "AnalyticsContext",
    "MetricsBundle",
    "Period",
    "Filters",
    "get_analytics_context",
]
