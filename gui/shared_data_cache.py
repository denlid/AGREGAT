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
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from engine.analytics.trade_analytics import resolve_r_multiple

logger = logging.getLogger("StrategyTester.gui.shared_data_cache")


def _trade_cache_key(trades: List[Any], initial_equity: float) -> Tuple:
    """Stable cache key (AI-6). Detects appends, resets, and in-place edits.

    Checksumming the last 10 PnLs guards against some in-place mutations
    or deletions that don't change the list length.
    """
    if not trades:
        return (0, None, None, float(initial_equity), 0.0)
    last = trades[-1]
    last_id = getattr(last, "id", None)
    last_xt = getattr(last, "exit_time_np", None) or getattr(last, "exit_time", None)

    # Detect in-place edits by summing last few PnLs
    pnl_checksum = 0.0
    try:
        sample = trades[-10:]
        pnl_checksum = sum(float(getattr(t, "pnl", 0.0) or 0.0) for t in sample)
    except Exception:
        pass

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
    """Caches trade-derived arrays and aggregates used by multiple tabs."""

    def __init__(self, engine: Any):
        self.engine = engine
        self._key: Optional[Tuple] = None
        self._sorted_trades: Optional[List[Any]] = None
        self._pnl: Optional[np.ndarray] = None
        self._sessions: Optional[List[SessionRow]] = None
        self._streaks: Optional[List[Tuple[str, int, int, int, float]]] = None  # (type, len, start_id, end_id, pnl)
        self._stats: Optional[Dict[str, Any]] = None
        self._eq_peak: Optional[np.ndarray] = None
        self._eq_dd_abs: Optional[np.ndarray] = None
        self._eq_dd_pct: Optional[np.ndarray] = None
        self._eq_arr: Optional[np.ndarray] = None

    # ------------------------------------------------------------------
    def invalidate(self) -> None:
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

    def _ensure(self) -> None:
        trades = list(getattr(self.engine, "trades", []) or [])
        key = _trade_cache_key(trades, float(getattr(self.engine, "initial_equity", 0.0) or 0.0))
        if self._key == key:
            return
        self._key = key
        self._sorted_trades = None
        self._pnl = None
        self._sessions = None
        self._streaks = None
        self._stats = None
        self._eq_arr = None
        self._eq_peak = None
        self._eq_dd_abs = None
        self._eq_dd_pct = None

    # ------------------------------------------------------------------
    def get_sorted_trades(self) -> List[Any]:
        """Trades sorted by entry time for stable chart/metrics."""
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
                logger.warning("SharedTradesCache: trade %r has null entry_time — using epoch fallback", getattr(t, "id", None))
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
        self._ensure()
        if self._pnl is not None:
            return self._pnl
        st = self.get_sorted_trades()
        arr = np.asarray([float(getattr(t, "pnl", 0.0) or 0.0) for t in st], dtype=float)
        self._pnl = arr
        return arr

    def get_session_rows(self, session_names: List[str]) -> List[SessionRow]:
        """Aggregate trades by session (in engine order, not necessarily sorted)."""
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
            wins = sum(1 for t in trades if getattr(t, "pnl", 0.0) > 0)
            losses = sum(1 for t in trades if getattr(t, "pnl", 0.0) < 0)
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
        self._ensure()
        if self._streaks is not None:
            return list(self._streaks)

        streaks: List[Tuple[str, int, int, int, float]] = []
        ct: Optional[str] = None
        sid: Optional[int] = None
        ln = 0
        spnl = 0.0

        trades = list(getattr(self.engine, "trades", []) or [])
        for t in trades:
            pnl = float(getattr(t, "pnl", 0.0) or 0.0)
            if pnl > 0:
                tt = "WIN"
            elif pnl < 0:
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
        self._ensure()
        if self._stats is not None:
            return self._stats

        trades = self.get_sorted_trades()
        init_eq = float(getattr(self.engine, "initial_equity", 0.0) or 0.0)
        n = len(trades)
        if n == 0:
            self._stats = dict(total=0, n_w=0, n_l=0, n_be=0, wr=0.0, total_pnl=0.0,
                               return_pct=0.0, gross_profit=0.0, gross_loss=0.0,
                               avg_w=0.0, avg_l=0.0, best=0.0, worst=0.0, mo=0.0,
                               pf=0.0, avg_rr=0.0, max_ws=0, max_ls=0,
                               cur_streak="\u2014", cur_streak_color="text2",
                               max_dd_pct=0.0, max_dd_abs=0.0, sharpe=0.0,
                               recovery=0.0, avg_dur=0.0, n_longs=0, n_shorts=0,
                               expectancy=0.0,
                               avg_rr_breakdown=dict(pos_avg=0.0, pos_count=0, neg_avg=0.0, neg_count=0))
            return self._stats

        pnl_arr = self.get_pnl_array()
        n = len(pnl_arr)

        win_mask = pnl_arr > 0
        loss_mask = pnl_arr < 0
        be_mask = pnl_arr == 0
        n_w = int(win_mask.sum())
        n_l = int(loss_mask.sum())
        n_be = int(be_mask.sum())

        total_pnl = float(pnl_arr.sum())
        return_pct = (total_pnl / init_eq * 100) if init_eq != 0 else 0.0
        gross_profit = float(pnl_arr[win_mask].sum()) if n_w else 0.0
        gross_loss = float(abs(pnl_arr[loss_mask].sum())) if n_l else 0.0

        avg_w = gross_profit / n_w if n_w else 0.0
        avg_l = gross_loss / n_l if n_l else 0.0
        best = float(pnl_arr.max()) if n else 0.0
        worst = float(pnl_arr.min()) if n else 0.0

        n_non_be = n_w + n_l
        p_win = n_w / n_non_be if n_non_be else 0.0
        p_loss = n_l / n_non_be if n_non_be else 0.0
        mo = p_win * avg_w - p_loss * avg_l

        pf = (gross_profit / gross_loss if gross_loss > 0
              else (float("inf") if gross_profit > 0 else 0.0))

        rr_arr = np.empty(n, dtype=float)
        for i, t in enumerate(trades):
            # Phase 1 stabilization — use centralized R-multiple resolver (AI-3)
            rr_arr[i] = resolve_r_multiple(t)
        rr_finite = rr_arr[np.isfinite(rr_arr)]
        avg_rr = float(rr_finite.mean()) if rr_finite.size > 0 else 0.0
        pos_rr = rr_finite[rr_finite > 0]
        neg_rr = rr_finite[rr_finite < 0]
        avg_rr_breakdown = {
            "pos_avg": float(pos_rr.mean()) if pos_rr.size > 0 else 0.0,
            "pos_count": int(pos_rr.size),
            "neg_avg": float(neg_rr.mean()) if neg_rr.size > 0 else 0.0,
            "neg_count": int(neg_rr.size),
        }

        signs = np.where(win_mask, 1, np.where(loss_mask, -1, 0))
        diffs = np.diff(signs, prepend=0)
        streak_starts = np.where(diffs != 0)[0]
        if len(streak_starts) > 0:
            streak_lengths = np.diff(np.concatenate([streak_starts, [n]]))
            streak_types = signs[streak_starts]
            win_streak_lens = streak_lengths[streak_types == 1]
            loss_streak_lens = streak_lengths[streak_types == -1]
            max_ws = int(win_streak_lens.max()) if win_streak_lens.size > 0 else 0
            max_ls = int(loss_streak_lens.max()) if loss_streak_lens.size > 0 else 0
            last_sign = int(signs[-1]) if n > 0 else 0
            if last_sign == 1:
                cur_streak = f"{int(streak_lengths[-1])}W"
                cur_streak_color = "green"
            elif last_sign == -1:
                cur_streak = f"{int(streak_lengths[-1])}L"
                cur_streak_color = "red"
            elif n_be == n:
                cur_streak = "BE"
                cur_streak_color = "amber"
            else:
                cur_streak = "\u2014"
                cur_streak_color = "text2"
        else:
            max_ws = 0
            max_ls = 0
            cur_streak = "\u2014"
            cur_streak_color = "text2"

        eq_arr, peak, dd_arr_abs, dd_arr_pct = self.get_equity_curve_arrays()
        max_dd_pct = float(dd_arr_pct.max()) if dd_arr_pct.size > 0 else 0.0
        max_dd_abs = float(dd_arr_abs.max()) if dd_arr_abs.size > 0 else 0.0

        from engine.analytics.equity_curve import (
            build_equity_curve as _build_eq,
            sharpe_ratio as _eq_sharpe,
        )
        eq_series = _build_eq(trades, init_eq)
        sharpe = _eq_sharpe(eq_series)

        if max_dd_abs > 0:
            recovery = total_pnl / max_dd_abs
        elif total_pnl > 0:
            recovery = float("inf")
        else:
            recovery = 0.0
        expectancy = total_pnl / n if n else 0.0

        dur_arr = np.empty(n, dtype=int)
        dir_arr = np.empty(n, dtype=object)
        for i, t in enumerate(trades):
            try:
                dur_arr[i] = int(getattr(t, "duration_minutes", 0) or 0)
            except Exception:
                dur_arr[i] = 0
            dir_arr[i] = str(getattr(t, "direction", "") or "").lower()
        dur_pos = dur_arr[dur_arr > 0]
        avg_dur = float(dur_pos.mean()) if dur_pos.size > 0 else 0.0

        n_longs = int((dir_arr == "long").sum())
        n_shorts = int((dir_arr == "short").sum())

        wr = n_w / n * 100 if n else 0.0

        self._stats = dict(
            total=n, n_w=n_w, n_l=n_l, n_be=n_be, wr=wr,
            total_pnl=total_pnl, return_pct=return_pct,
            gross_profit=gross_profit, gross_loss=gross_loss,
            avg_w=avg_w, avg_l=avg_l, best=best, worst=worst,
            mo=mo, pf=pf, avg_rr=avg_rr,
            max_ws=max_ws, max_ls=max_ls,
            cur_streak=cur_streak, cur_streak_color=cur_streak_color,
            max_dd_pct=max_dd_pct, max_dd_abs=max_dd_abs,
            sharpe=sharpe, recovery=recovery,
            avg_dur=avg_dur, n_longs=n_longs, n_shorts=n_shorts,
            expectancy=expectancy,
            avg_rr_breakdown=avg_rr_breakdown,
        )
        return self._stats

    def get_equity_curve_arrays(self) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """Return (eq_arr, peak, dd_arr_abs, dd_arr_pct) for all trades.

        eq_arr has length n+1 (initial equity prepended at index 0).
        """
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
