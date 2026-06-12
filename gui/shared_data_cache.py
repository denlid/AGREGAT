"""
Shared UI data cache for trades-derived aggregates.

Goal:
- Provide a single source of truth for expensive "scan all trades" computations
  used across Stats / Analytics / Quant.
- Invalidate cheaply on `trade_completed` / `data_loaded` / reset.

This module intentionally lives in `gui/` (not `engine/`) because it is a UI
performance concern and may evolve with UI needs (tables, charts).
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from engine.analytics.trade_analytics import (
    resolve_r_multiple,
    hour_weekday_heatmap,
    month_year_heatmap,
    mae_mfe_arrays,
    r_multiples,
    exit_reason_attribution,
)

logger = logging.getLogger("StrategyTester.gui.shared_data_cache")


def _trade_cache_key(trades: List[Any], initial_equity: float) -> Tuple:
    """Stable cache key (AI-6). Detects appends, resets, and in-place edits.

    Summing all PnLs guards against in-place mutations or deletions
    that don't change the list length.
    """
    if not trades:
        return (0, None, None, float(initial_equity), 0.0)
    last = trades[-1]
    last_id = getattr(last, "id", None)
    last_xt = getattr(last, "exit_time_np", None) or getattr(last, "exit_time", None)

    # Detect in-place edits by summing all PnLs (O(N) but fast with float sum)
    try:
        pnl_checksum = sum(float(getattr(t, "pnl", 0.0) or 0.0) for t in trades)
    except Exception:
        pnl_checksum = 0.0

    return (len(trades), last_id, str(last_xt), float(initial_equity), pnl_checksum)


@dataclass
class SessionRow:
    session: str
    total: int
    wins: int
    losses: int
    wr: float
    avg_pnl: float
    total_pnl: float
    best_trade: float


class SharedTradesCache:
    """Caches trade-derived arrays and aggregates used by multiple tabs.

    Thread-safe via RLock (AI-12).
    """

    def __init__(self, engine: Any):
        self.engine = engine
        self._lock = threading.RLock()
        self._key: Optional[Tuple] = None
        self._sorted_trades: Optional[List[Any]] = None
        self._pnl: Optional[np.ndarray] = None
        self._rr: Optional[np.ndarray] = None
        self._dur: Optional[np.ndarray] = None
        self._dir: Optional[np.ndarray] = None
        self._sessions: Optional[List[SessionRow]] = None
        self._streaks: Optional[List[Tuple[str, int, int, int, float]]] = None
        self._stats: Optional[Dict[str, Any]] = None
        self._eq_peak: Optional[np.ndarray] = None
        self._eq_dd_abs: Optional[np.ndarray] = None
        self._eq_dd_pct: Optional[np.ndarray] = None
        self._eq_arr: Optional[np.ndarray] = None
        
        # Phase 4 "Grail" additions — unified analytics
        self._heatmaps: Optional[Dict[str, Any]] = None
        self._mae_mfe: Optional[Any] = None
        self._r_multiples: Optional[Any] = None
        self._attribution: Optional[List[Any]] = None

    # ------------------------------------------------------------------
    def invalidate(self) -> None:
        with self._lock:
            self._key = None
            self._sorted_trades = None
            self._pnl = None
            self._sessions = None
            self._streaks = None
            self._stats = None
            self._eq_arr = None
            self._eq_peak = None
            self._eq_dd_abs = None
            self._eq_dd_pct = None
            self._heatmaps = None
            self._mae_mfe = None
            self._r_multiples = None
            self._attribution = None

    def _ensure(self) -> None:
        # Internal helper, caller must hold lock
        trades = list(getattr(self.engine, "trades", []) or [])
        key = _trade_cache_key(trades, float(getattr(self.engine, "initial_equity", 0.0) or 0.0))
        if self._key == key:
            return
        self._key = key
        self.invalidate()
        self._key = key

    # ------------------------------------------------------------------
    def get_heatmaps(self) -> Dict[str, Any]:
        with self._lock:
            self._ensure()
            if self._heatmaps is not None:
                return self._heatmaps
            trades = self.get_sorted_trades()
            hw = hour_weekday_heatmap(trades)
            my = month_year_heatmap(trades)
            self._heatmaps = {"hw": hw, "my": my}
            return self._heatmaps

    def get_mae_mfe(self) -> Any:
        with self._lock:
            self._ensure()
            if self._mae_mfe is not None:
                return self._mae_mfe
            trades = self.get_sorted_trades()
            self._mae_mfe = mae_mfe_arrays(trades)
            return self._mae_mfe

    def get_r_multiples(self) -> Any:
        with self._lock:
            self._ensure()
            if self._r_multiples is not None:
                return self._r_multiples
            trades = self.get_sorted_trades()
            self._r_multiples = r_multiples(trades)
            return self._r_multiples

    def get_attribution(self) -> List[Any]:
        with self._lock:
            self._ensure()
            if self._attribution is not None:
                return self._attribution
            trades = self.get_sorted_trades()
            self._attribution = exit_reason_attribution(trades)
            return self._attribution

    def get_sorted_trades(self) -> List[Any]:
        """Trades sorted by entry time for stable chart/metrics."""
        with self._lock:
            self._ensure()
            if self._sorted_trades is not None:
                return list(self._sorted_trades)
            trades = list(getattr(self.engine, "trades", []) or [])

            def _key(t: Any) -> tuple:
                et = getattr(t, "entry_time_np", None)
                if et is None:
                    raw = getattr(t, "entry_time", None)
                    try:
                        ts = pd.to_datetime(raw, errors="coerce")
                        if not pd.isna(ts):
                            et = np.datetime64(ts)
                    except Exception:
                        et = None
                if et is None:
                    et = np.datetime64("1970-01-01")
                    miss = 1
                    logger.warning(
                        "SharedTradesCache: trade %r has null entry_time — using epoch fallback",
                        getattr(t, "id", None),
                    )
                else:
                    miss = 0
                try:
                    tid = int(getattr(t, "id", 0) or 0)
                except Exception:
                    tid = 0
                return (miss, et, tid)

            out = sorted(trades, key=_key)
            self._sorted_trades = list(out)
            return list(out)

    def get_pnl_array(self) -> np.ndarray:
        """Per-trade PnL dollars as NumPy array (sorted order)."""
        with self._lock:
            self._ensure()
            if self._pnl is not None:
                return self._pnl
            st = self.get_sorted_trades()
            arr = np.asarray([float(getattr(t, "pnl", 0.0) or 0.0) for t in st], dtype=float)
            self._pnl = arr
            return arr

    def get_rr_array(self) -> np.ndarray:
        """Per-trade R-multiples as NumPy array (sorted order)."""
        with self._lock:
            self._ensure()
            if self._rr is not None:
                return self._rr
            st = self.get_sorted_trades()
            arr = np.asarray([resolve_r_multiple(t) for t in st], dtype=float)
            self._rr = arr
            return arr

    def get_dur_array(self) -> np.ndarray:
        """Per-trade duration (minutes) as NumPy array (sorted order)."""
        with self._lock:
            self._ensure()
            if self._dur is not None:
                return self._dur
            st = self.get_sorted_trades()
            arr = np.asarray([int(getattr(t, "duration_minutes", 0) or 0) for t in st], dtype=int)
            self._dur = arr
            return arr

    def get_dir_array(self) -> np.ndarray:
        """Per-trade direction (lower-case) as NumPy array (sorted order)."""
        with self._lock:
            self._ensure()
            if self._dir is not None:
                return self._dir
            st = self.get_sorted_trades()
            arr = np.asarray([str(getattr(t, "direction", "") or "").lower() for t in st], dtype=object)
            self._dir = arr
            return arr

    def get_session_rows(self, session_names: List[str]) -> List[SessionRow]:
        """Aggregate trades by session (in engine order, not necessarily sorted)."""
        with self._lock:
            self._ensure()
            if self._sessions is not None:
                return list(self._sessions)

            session_data: Dict[str, List[Any]] = {s: [] for s in session_names}
            for t in (getattr(self.engine, "trades", []) or []):
                sn = getattr(t, "session", None)
                if sn in session_data:
                    session_data[sn].append(t)

            rows: List[SessionRow] = []
            for sn in session_names:
                trades = session_data.get(sn) or []
                if not trades:
                    continue
                total = len(trades)
                # Phase 1: use EPS for consistency (AI-13)
                EPS = 1e-9
                wins = sum(1 for t in trades if getattr(t, "pnl", 0.0) > EPS)
                losses = sum(1 for t in trades if getattr(t, "pnl", 0.0) < -EPS)
                wr = (wins / total * 100.0) if total else 0.0
                total_pnl = float(sum(getattr(t, "pnl", 0.0) for t in trades))
                avg_pnl = float(total_pnl / total) if total else 0.0
                best_trade = float(max((getattr(t, "pnl", 0.0) for t in trades), default=0.0))
                rows.append(
                    SessionRow(
                        session=sn,
                        total=total,
                        wins=wins,
                        losses=losses,
                        wr=float(wr),
                        avg_pnl=avg_pnl,
                        total_pnl=total_pnl,
                        best_trade=best_trade,
                    )
                )

            self._sessions = list(rows)
            return list(rows)

    def get_streaks(self) -> List[Tuple[str, int, int, int, float]]:
        """Return streak episodes.

        BE (pnl==0) resets streak (neither WIN nor LOSS).
        """
        with self._lock:
            self._ensure()
            if self._streaks is not None:
                return list(self._streaks)

            streaks: List[Tuple[str, int, int, int, float]] = []
            ct: Optional[str] = None
            sid: Optional[int] = None
            ln = 0
            spnl = 0.0

            trades = list(getattr(self.engine, "trades", []) or [])
            EPS = 1e-9
            for t in trades:
                pnl = float(getattr(t, "pnl", 0.0) or 0.0)
                if pnl > EPS:
                    tt = "WIN"
                elif pnl < -EPS:
                    tt = "LOSS"
                else:
                    # reset
                    if ct is not None and sid is not None:
                        streaks.append((ct, ln, sid, int(getattr(t, "id", sid)) - 1, float(spnl)))
                    ct = None
                    sid = None
                    ln = 0
                    spnl = 0.0
                    continue

                if tt == ct:
                    ln += 1
                    spnl += pnl
                else:
                    if ct is not None and sid is not None:
                        streaks.append((ct, ln, sid, int(getattr(t, "id", sid)) - 1, float(spnl)))
                    ct = tt
                    sid = int(getattr(t, "id", 0) or 0)
                    ln = 1
                    spnl = pnl

            if ct is not None and sid is not None and trades:
                streaks.append((ct, ln, sid, int(getattr(trades[-1], "id", sid)), float(spnl)))

            self._streaks = list(streaks)
            return list(streaks)

    def get_stats_dict(self) -> Dict[str, Any]:
        """Full stats dict for all trades (same schema as StatsCalculator._do_compute)."""
        with self._lock:
            self._ensure()
            if self._stats is not None:
                return self._stats

            trades = self.get_sorted_trades()
            init_eq = float(getattr(self.engine, "initial_equity", 0.0) or 0.0)
            
            # Phase 4 "Grail" — Delegate to centralized metrics logic
            from gui.analytics.stats_calculator import StatsCalculator
            self._stats = StatsCalculator._do_compute(
                trades, init_eq,
                pnl_arr=self.get_pnl_array(),
                rr_arr=self.get_rr_array(),
                dur_arr=self.get_dur_array(),
                dir_arr=self.get_dir_array()
            )
            return self._stats

    def get_equity_curve_arrays(self) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """Return (eq_arr, peak, dd_arr_abs, dd_arr_pct) for all trades.

        eq_arr has length n+1 (initial equity prepended at index 0).
        """
        with self._lock:
            self._ensure()
            if self._eq_arr is not None:
                return self._eq_arr, self._eq_peak, self._eq_dd_abs, self._eq_dd_pct

            pnl_arr = self.get_pnl_array()
            n = len(pnl_arr)
            init_eq = float(getattr(self.engine, "initial_equity", 0.0) or 0.0)
            eq_arr = np.empty(n + 1, dtype=float)
            eq_arr[0] = init_eq
            np.cumsum(pnl_arr, out=eq_arr[1:])
            eq_arr[1:] += init_eq
            peak = np.maximum.accumulate(eq_arr)
            dd_arr_abs = peak - eq_arr
            with np.errstate(divide="ignore", invalid="ignore"):
                dd_arr_pct = np.where(peak > 0, dd_arr_abs / peak * 100, 0)

            self._eq_arr = eq_arr
            self._eq_peak = peak
            self._eq_dd_abs = dd_arr_abs
            self._eq_dd_pct = dd_arr_pct
            return eq_arr, peak, dd_arr_abs, dd_arr_pct
