import logging

import numpy as np
import pandas as pd

from engine.analytics.equity_curve import (
    build_equity_curve as _build_eq,
    calmar_ratio as _eq_calmar,
    daily_returns as _eq_daily_returns,
    sharpe_ratio as _eq_sharpe,
    sortino_ratio as _eq_sortino,
    years_span as _eq_years_span,
)
from engine.analytics.risk_metrics import (
    conditional_var as _eng_cvar,
    omega_ratio as _eng_omega,
    pain_index as _eng_pain_index,
    time_under_water as _eng_tuw,
    ulcer_index as _eng_ulcer,
    value_at_risk as _eng_var,
)
from engine.analytics.trade_analytics import resolve_r_multiple
from gui.shared_data_cache import _trade_cache_key

logger = logging.getLogger("StrategyTester.gui.stats_calculator")


def _safe_div(num: float, den: float, default: float = 0.0) -> float:
    """Division that returns *default* when *den* is zero or non-finite."""
    try:
        if not np.isfinite(den) or den == 0:
            return default
        out = num / den
        return float(out) if np.isfinite(out) else default
    except Exception:
        return default


def _percentile(arr: np.ndarray, q: float, default: float = 0.0) -> float:
    """Percentile with safe handling for empty arrays."""
    if arr.size == 0:
        return default
    try:
        return float(np.percentile(arr, q))
    except Exception:
        return default


class StatsCalculator:
    def __init__(self):
        self._cache_key = None
        self._cached_result = None

    def compute(self, trades, initial_equity, shared_cache=None):
        key = _trade_cache_key(trades, initial_equity)
        if key == self._cache_key and self._cached_result is not None:
            return self._cached_result
        # Delegate to shared cache when computing for all trades
        if shared_cache is not None:
            all_trades = list(getattr(getattr(shared_cache, "engine", None), "trades", []) or [])
            if _trade_cache_key(all_trades, initial_equity) == key:
                return shared_cache.get_stats_dict()
        result = self._do_compute(trades, initial_equity)
        self._cache_key = key
        self._cached_result = result
        return result

    def invalidate(self):
        self._cache_key = None
        self._cached_result = None

    @staticmethod
    def _do_compute(trades, initial_equity):
        n = len(trades)
        if n == 0:
            return dict(total=0, n_w=0, n_l=0, n_be=0, wr=0.0, total_pnl=0.0,
                        return_pct=0.0, gross_profit=0.0, gross_loss=0.0,
                        avg_w=0.0, avg_l=0.0, best=0.0, worst=0.0, mo=0.0,
                        pf=0.0, avg_rr=0.0, max_ws=0, max_ls=0,
                        cur_streak="\u2014", cur_streak_color="text2",
                        max_dd_pct=0.0, max_dd_abs=0.0, sharpe=0.0,
                        recovery=0.0, avg_dur=0.0, n_longs=0, n_shorts=0,
                        expectancy=0.0,
                        avg_rr_breakdown=dict(pos_avg=0.0, pos_count=0, neg_avg=0.0, neg_count=0),
                        sortino=0.0, calmar=0.0, ulcer=0.0, omega=0.0,
                        var95=0.0, cvar95=0.0, payoff=0.0, tail_ratio=0.0,
                        cagr=0.0, kelly=0.0, k_ratio=0.0,
                        skew=0.0, kurtosis=0.0, pain_index=0.0,
                        max_tuw=0, avg_tuw=0.0, common_sense=0.0)

        pnl_arr = np.empty(n, dtype=float)
        rr_arr = np.empty(n, dtype=float)
        dur_arr = np.empty(n, dtype=int)
        dir_arr = np.empty(n, dtype=object)
        for i, t in enumerate(trades):
            try:
                pnl_arr[i] = float(getattr(t, "pnl", 0.0) or 0.0)
            except Exception:
                pnl_arr[i] = 0.0
            # Phase 1 stabilization — use centralized R-multiple resolver (AI-3)
            rr_arr[i] = resolve_r_multiple(t)
            try:
                dur_arr[i] = int(getattr(t, "duration_minutes", 0) or 0)
            except Exception:
                dur_arr[i] = 0
            dir_arr[i] = str(getattr(t, "direction", "") or "").lower()

        EPS = 1e-9
        win_mask = pnl_arr > EPS
        loss_mask = pnl_arr < -EPS
        be_mask = np.abs(pnl_arr) <= EPS
        n_w = int(win_mask.sum())
        n_l = int(loss_mask.sum())
        n_be = int(be_mask.sum())

        total_pnl = float(pnl_arr.sum())
        init_eq = float(initial_equity)
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

        eq_arr = init_eq + np.cumsum(pnl_arr)
        peak = np.maximum.accumulate(eq_arr)
        dd_arr_abs = peak - eq_arr
        dd_arr_pct = np.where(peak > 0, dd_arr_abs / peak * 100, 0)
        max_dd_pct = float(dd_arr_pct.max()) if dd_arr_pct.size > 0 else 0.0
        max_dd_abs = float(dd_arr_abs.max()) if dd_arr_abs.size > 0 else 0.0

        eq_series = _build_eq(trades, init_eq)
        sharpe = _eq_sharpe(eq_series)

        if max_dd_abs > 0:
            recovery = total_pnl / max_dd_abs
        elif total_pnl > 0:
            recovery = float("inf")
        else:
            recovery = 0.0
        expectancy = total_pnl / n if n else 0.0

        dur_pos = dur_arr[dur_arr > 0]
        avg_dur = float(dur_pos.mean()) if dur_pos.size > 0 else 0.0

        n_longs = int((dir_arr == "long").sum())
        n_shorts = int((dir_arr == "short").sum())

        wr = n_w / n * 100 if n else 0.0

        # -----------------------------------------------------------------
        # Advanced / risk-adjusted metrics (Phase 1 quick-win surface).
        # All computations share the already-built equity curve to avoid
        # re-traversing the trade list.
        # -----------------------------------------------------------------
        try:
            sortino = float(_eq_sortino(eq_series))
        except Exception:
            sortino = 0.0
        try:
            calmar = float(_eq_calmar(eq_series, init_eq))
        except Exception:
            calmar = 0.0
        try:
            eq_np = eq_series.to_numpy(dtype="float64")
            ulcer = float(_eng_ulcer(eq_np))
        except Exception:
            eq_np = eq_arr
            ulcer = 0.0
        try:
            pain_idx = float(_eng_pain_index(eq_np))
        except Exception:
            pain_idx = 0.0
        try:
            omega = float(_eng_omega(pnl_arr, 0.0))
        except Exception:
            omega = 0.0
        try:
            var95 = float(_eng_var(pnl_arr, 0.95))
            cvar95 = float(_eng_cvar(pnl_arr, 0.95))
        except Exception:
            var95 = 0.0
            cvar95 = 0.0
        try:
            tuw_stats = _eng_tuw(eq_np)
            max_tuw = int(getattr(tuw_stats, "max_underwater_periods", 0) or 0)
            avg_tuw = float(getattr(tuw_stats, "avg_underwater_periods", 0.0) or 0.0)
        except Exception:
            max_tuw = 0
            avg_tuw = 0.0

        payoff = _safe_div(avg_w, avg_l, default=0.0)

        # Tail Ratio: |p95| / |p5| of per-trade PnL.
        p95_abs = abs(_percentile(pnl_arr, 95.0))
        p5_abs = abs(_percentile(pnl_arr, 5.0))
        tail_ratio = _safe_div(p95_abs, p5_abs, default=0.0)

        common_sense = pf * tail_ratio if np.isfinite(pf) and np.isfinite(tail_ratio) else 0.0

        # CAGR — annualised return derived from elapsed wall-clock years.
        try:
            years = float(_eq_years_span(eq_series))
        except Exception:
            years = 0.0
        if years > 0 and init_eq > 0:
            final_eq = init_eq + total_pnl
            ratio = final_eq / init_eq
            if ratio > 0:
                cagr = float((ratio ** (1.0 / years) - 1.0) * 100.0)
            else:
                cagr = float("nan")
        else:
            cagr = 0.0

        # Kelly fraction = WR - (1-WR)/payoff, clipped to [-1, 1] for display.
        if payoff > 0:
            kelly_raw = (n_w / n_non_be) - (n_l / n_non_be) / payoff if n_non_be else 0.0
            kelly = float(np.clip(kelly_raw, -1.0, 1.0)) * 100.0
        else:
            kelly = 0.0

        # K-ratio (Lars Kestner): slope of equity regression / stderr-of-slope.
        # We use the per-trade equity points instead of daily resample to keep
        # the metric meaningful even on short windows.
        try:
            if eq_arr.size >= 3:
                x = np.arange(eq_arr.size, dtype="float64")
                slope, intercept = np.polyfit(x, eq_arr, 1)
                fit = slope * x + intercept
                resid = eq_arr - fit
                dof = max(eq_arr.size - 2, 1)
                sx2 = float(((x - x.mean()) ** 2).sum())
                if sx2 > 0:
                    se_slope = float(np.sqrt((resid ** 2).sum() / dof / sx2))
                    k_ratio = float(slope / se_slope) if se_slope > 0 else 0.0
                else:
                    k_ratio = 0.0
            else:
                k_ratio = 0.0
        except Exception:
            k_ratio = 0.0

        # Distribution shape — skew / excess kurtosis of per-trade PnL.
        try:
            if pnl_arr.size >= 3:
                mu = float(pnl_arr.mean())
                centered = pnl_arr - mu
                m2 = float((centered ** 2).mean())
                if m2 > 0:
                    m3 = float((centered ** 3).mean())
                    m4 = float((centered ** 4).mean())
                    skew = m3 / (m2 ** 1.5)
                    kurtosis = m4 / (m2 ** 2) - 3.0
                else:
                    skew = 0.0
                    kurtosis = 0.0
            else:
                skew = 0.0
                kurtosis = 0.0
        except Exception:
            skew = 0.0
            kurtosis = 0.0

        return dict(
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
            sortino=sortino, calmar=calmar, ulcer=ulcer, omega=omega,
            var95=var95, cvar95=cvar95, payoff=payoff,
            tail_ratio=tail_ratio, common_sense=common_sense,
            cagr=cagr, kelly=kelly, k_ratio=k_ratio,
            skew=skew, kurtosis=kurtosis,
            pain_index=pain_idx, max_tuw=max_tuw, avg_tuw=avg_tuw,
        )
