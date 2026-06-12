"""
Trade-level analytics (Phase 5).

Pure functions over a ``List[Trade]`` so the GUI layer can stay thin.

Provides:
- :func:`mae_mfe_arrays` — per-trade MAE/MFE arrays in $ and as a fraction of risk
- :func:`r_multiples` — per-trade R-multiple = signed ``Trade.rr``
  (``pnl_points / |entry_price - initial_sl|``); falls back to a manual
  recomputation when ``rr`` is missing (e.g. legacy AGBK loads).
- :func:`hour_weekday_heatmap` — 24×7 grids of trade count, win rate, and net PnL
- :func:`month_year_heatmap` — month×year grid of net PnL
- :func:`drawdown_durations` — list of (start_idx, trough_idx, recover_idx, depth_$, length_trades)
- :func:`exit_reason_attribution` — per-exit-reason breakdown of PnL contribution
- :func:`simulate_slippage` — apply a flat slippage shock and return adjusted PnL series

Risk-distance convention
------------------------
``Trade.initial_sl`` is the *absolute SL price* (e.g. ``17 850.0`` on DE40),
**not** a distance in points. This module converts that to a distance via
``risk_distance = abs(entry_price - initial_sl)`` whenever it needs to
normalise by per-trade risk. Earlier revisions of this file treated
``initial_sl`` as a distance which produced nonsense R-multiples on real
engine output (R ≈ pnl / 17 850 instead of pnl / 50). The fix landed in
Phase 1 of TZ_STABLE_GRAIL v4.

Functions are robust to empty inputs and never raise on edge cases (NaN-safe).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional

import numpy as np


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------
def _parse_time(s: str) -> Optional[datetime]:
    """Parse the ISO-8601-ish strings produced by the engine. Returns None on failure."""
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except (ValueError, TypeError):
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                return datetime.strptime(s, fmt)
            except ValueError:
                continue
    return None


def resolve_r_multiple(trade: Any) -> float:
    """Consolidated R-multiple resolver (AI-3).
    Prioritises pre-computed 'rr' from engine; falls back to manual pnl_points / risk_distance.
    Returns np.nan if risk is zero or unknown.
    """
    rr = getattr(trade, "rr", None)
    if rr is not None:
        try:
            val = float(rr)
            if np.isfinite(val):
                return val
        except (TypeError, ValueError):
            pass

    risk = _risk_distance(trade)
    if risk > 1e-9:
        try:
            pnl_points = float(getattr(trade, "pnl_points", 0.0) or 0.0)
            return pnl_points / risk
        except (TypeError, ValueError):
            pass

    return np.nan


def resolve_period_window(
    kind: str,
    custom_start: Optional[Any] = None,
    custom_end: Optional[Any] = None,
) -> tuple[Optional[np.datetime64], Optional[np.datetime64]]:
    """Convert period kind and custom dates to np.datetime64 window (AI-1)."""
    if kind == "ALL" or not kind:
        return None, None

    if kind == "CUSTOM":
        if custom_start is None or custom_end is None:
            return None, None
        try:
            import pandas as pd

            start = np.datetime64(pd.Timestamp(custom_start).normalize())
            end = np.datetime64(
                pd.Timestamp(custom_end).normalize() + pd.Timedelta(days=1)
            )
            return start, end
        except Exception:
            return None, None

    import pandas as pd

    now = pd.Timestamp.utcnow().tz_localize(None)
    if kind == "1D":
        start = np.datetime64(now.normalize())
        end = start + np.timedelta64(1, "D")
    elif kind == "1W":
        start = np.datetime64((now - pd.Timedelta(days=7)).normalize())
        end = np.datetime64(now.normalize() + pd.Timedelta(days=1))
    elif kind == "1M":
        start = np.datetime64((now - pd.Timedelta(days=30)).normalize())
        end = np.datetime64(now.normalize() + pd.Timedelta(days=1))
    elif kind == "3M":
        start = np.datetime64((now - pd.Timedelta(days=90)).normalize())
        end = np.datetime64(now.normalize() + pd.Timedelta(days=1))
    elif kind == "6M":
        start = np.datetime64((now - pd.Timedelta(days=180)).normalize())
        end = np.datetime64(now.normalize() + pd.Timedelta(days=1))
    elif kind == "YTD":
        start = np.datetime64(pd.Timestamp(now.year, 1, 1))
        end = np.datetime64(now.normalize() + pd.Timedelta(days=1))
    elif kind == "1Y":
        start = np.datetime64((now - pd.Timedelta(days=365)).normalize())
        end = np.datetime64(now.normalize() + pd.Timedelta(days=1))
    else:
        return None, None
    return start, end


def filter_trades_by_period(
    trades: List[Any],
    start: Optional[np.datetime64] = None,
    end: Optional[np.datetime64] = None,
) -> List[Any]:
    """Universal period filter using vectorized NumPy masks (AI-1)."""
    if not trades or (start is None and end is None):
        return trades

    n = len(trades)
    et_arr = np.full(n, np.datetime64("NaT"), dtype="datetime64[ns]")

    # Try fast path first (entry_time_np)
    for i, t in enumerate(trades):
        v = getattr(t, "entry_time_np", None)
        if v is not None:
            et_arr[i] = v

    # Fallback for NaT (parse entry_time strings)
    missing = np.where(et_arr == np.datetime64("NaT"))[0]
    if missing.size > 0:
        import pandas as pd
        for i in missing:
            v = getattr(trades[i], "entry_time", None)
            if v:
                try:
                    ts = pd.to_datetime(v, errors="coerce")
                    if pd.notna(ts):
                        et_arr[i] = ts.to_datetime64()
                except Exception:
                    pass

    mask = np.ones(n, dtype=bool)
    if start is not None:
        mask &= et_arr >= start
    if end is not None:
        mask &= et_arr < end

    # Exclude NaT if we are filtering by a specific window
    if start is not None or end is not None:
        mask &= et_arr != np.datetime64("NaT")

    return [trades[i] for i in np.where(mask)[0]]


# ----------------------------------------------------------------------------
# MAE / MFE
# ----------------------------------------------------------------------------
@dataclass(frozen=True)
class MAEMFEArrays:
    """Per-trade MAE/MFE in $ plus normalised-by-risk variants."""

    mae_dollars: np.ndarray
    mfe_dollars: np.ndarray
    mae_r: np.ndarray  # MAE / |risk_per_trade|
    mfe_r: np.ndarray  # MFE / |risk_per_trade|
    pnl_dollars: np.ndarray


def _risk_distance(t: Any) -> float:
    """Return ``|entry_price - initial_sl|`` (the per-trade risk in *price*
    units). Returns ``0.0`` when either field is missing/zero so callers
    can detect degenerate trades and emit ``NaN`` instead of dividing by 0.
    """
    try:
        entry = float(getattr(t, "entry_price", 0.0) or 0.0)
        sl = float(getattr(t, "initial_sl", 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0.0
    if entry <= 0 or sl <= 0:
        return 0.0
    return abs(entry - sl)


def mae_mfe_arrays(trades: Iterable[Any]) -> MAEMFEArrays:
    """Extract MAE/MFE arrays from trades; falls back to point-based MAE/MFE when $-form is zero.

    The per-trade R normalisation uses the *price-distance* form
    (``mae_points / |entry-initial_sl|``); the dollar form would give the
    same ratio but require ``point_value`` which is not stored on each
    Trade. When MAE/MFE are also reported in points (``mae`` / ``mfe``)
    we use those; if only the dollar form is available we approximate
    ``risk_$`` from the trade's R-multiple (``rr``) so the ratio stays
    in R units regardless of which form the engine populated.
    """
    mae_d: List[float] = []
    mfe_d: List[float] = []
    mae_r: List[float] = []
    mfe_r: List[float] = []
    pnls: List[float] = []
    for t in trades:
        md = float(getattr(t, "mae_dollars", 0.0) or 0.0)
        fd = float(getattr(t, "mfe_dollars", 0.0) or 0.0)
        if md == 0.0 and fd == 0.0:
            md = float(getattr(t, "mae", 0.0) or 0.0)
            fd = float(getattr(t, "mfe", 0.0) or 0.0)

        risk_pts = _risk_distance(t)
        mae_pts = float(getattr(t, "mae", 0.0) or 0.0)
        mfe_pts = float(getattr(t, "mfe", 0.0) or 0.0)
        if risk_pts > 0 and (mae_pts != 0.0 or mfe_pts != 0.0):
            mae_r_val = mae_pts / risk_pts
            mfe_r_val = mfe_pts / risk_pts
        else:
            # Fallback path: derive risk in $ from the trade's recorded R-multiple
            # so unit-test fixtures that only set ``mae_dollars`` still normalise.
            rr = getattr(t, "rr", None)
            pnl = float(getattr(t, "pnl", 0.0) or 0.0)
            if rr is not None and rr != 0.0 and pnl != 0.0:
                risk_dollars = abs(pnl / rr)
            else:
                # Last-resort legacy fallback: treat ``initial_sl`` as the
                # already-pre-multiplied $ risk per unit volume. Used only by
                # synthetic test fixtures and pre-v4 AGBK files.
                risk_dollars = abs(float(getattr(t, "initial_sl", 0.0) or 0.0)) * float(
                    getattr(t, "volume", 1.0) or 1.0
                )
            if risk_dollars > 0:
                mae_r_val = md / risk_dollars
                mfe_r_val = fd / risk_dollars
            else:
                mae_r_val = float("nan")
                mfe_r_val = float("nan")

        mae_d.append(md)
        mfe_d.append(fd)
        mae_r.append(mae_r_val)
        mfe_r.append(mfe_r_val)
        pnls.append(float(getattr(t, "pnl", 0.0) or 0.0))
    return MAEMFEArrays(
        mae_dollars=np.asarray(mae_d, dtype=float),
        mfe_dollars=np.asarray(mfe_d, dtype=float),
        mae_r=np.asarray(mae_r, dtype=float),
        mfe_r=np.asarray(mfe_r, dtype=float),
        pnl_dollars=np.asarray(pnls, dtype=float),
    )


# ----------------------------------------------------------------------------
# R-multiples
# ----------------------------------------------------------------------------
@dataclass(frozen=True)
class RMultipleArrays:
    """Per-trade R-multiple distribution."""

    r_values: np.ndarray
    cumulative_r: np.ndarray
    expectancy_r: float


def r_multiples(trades: Iterable[Any]) -> RMultipleArrays:
    """Compute the signed R-multiple per trade.

    Uses resolve_r_multiple() (AI-3) for consistent logic across Stats/Trades.
    """
    rs: List[float] = []
    for t in trades:
        val = resolve_r_multiple(t)
        if np.isnan(val):
            # Legacy fallback for FakeTrade-style fixtures where initial_sl
            # was already recorded as dollar risk.
            vol = float(getattr(t, "volume", 1.0) or 1.0)
            risk_dollars = abs(float(getattr(t, "initial_sl", 0.0) or 0.0)) * vol
            pnl = float(getattr(t, "pnl", 0.0) or 0.0)
            if risk_dollars > 0:
                rs.append(pnl / risk_dollars)
            else:
                rs.append(float("nan"))
        else:
            rs.append(val)

    arr = np.asarray(rs, dtype=float)
    if arr.size == 0:
        return RMultipleArrays(arr, arr, 0.0)
    finite = arr[np.isfinite(arr)]
    cum = np.cumsum(np.where(np.isfinite(arr), arr, 0.0))
    expectancy = float(finite.mean()) if finite.size else 0.0
    return RMultipleArrays(r_values=arr, cumulative_r=cum, expectancy_r=expectancy)


# ----------------------------------------------------------------------------
# Hour × weekday heatmap
# ----------------------------------------------------------------------------
@dataclass(frozen=True)
class HourWeekdayHeatmap:
    """24 hours × 7 weekdays grid (Mon=0 … Sun=6)."""

    counts: np.ndarray  # shape (24, 7) int
    win_rate: np.ndarray  # shape (24, 7) float; NaN where counts == 0
    net_pnl: np.ndarray  # shape (24, 7) float


def hour_weekday_heatmap(
    trades: Iterable[Any], cancel_token: Optional[Any] = None
) -> HourWeekdayHeatmap:
    """Aggregate trade entry-times into a 24×7 grid (AI-11)."""
    counts = np.zeros((24, 7), dtype=int)
    wins = np.zeros((24, 7), dtype=int)
    pnl = np.zeros((24, 7), dtype=float)
    for i, t in enumerate(trades):
        if i % 1000 == 0 and cancel_token and cancel_token.is_set():
            break
        dt = _parse_time(getattr(t, "entry_time", "") or "")
        if dt is None:
            continue
        h = dt.hour
        wd = dt.weekday()
        counts[h, wd] += 1
        val = float(getattr(t, "pnl", 0.0) or 0.0)
        if val > 0:
            wins[h, wd] += 1
        pnl[h, wd] += val
    with np.errstate(divide="ignore", invalid="ignore"):
        win_rate = np.where(counts > 0, wins / np.maximum(counts, 1), np.nan)
    return HourWeekdayHeatmap(counts=counts, win_rate=win_rate, net_pnl=pnl)


# ----------------------------------------------------------------------------
# Month × year heatmap
# ----------------------------------------------------------------------------
@dataclass(frozen=True)
class MonthYearHeatmap:
    """Year × month (Jan=1 … Dec=12) grid of net PnL."""

    years: List[int]
    net_pnl: np.ndarray  # shape (n_years, 12)
    counts: np.ndarray  # shape (n_years, 12) int


def month_year_heatmap(
    trades: Iterable[Any], cancel_token: Optional[Any] = None
) -> MonthYearHeatmap:
    """Aggregate trade entry-times into a (year × month) grid (AI-11)."""
    by_year: Dict[int, np.ndarray] = {}
    by_year_n: Dict[int, np.ndarray] = {}
    for i, t in enumerate(trades):
        if i % 1000 == 0 and cancel_token and cancel_token.is_set():
            break
        dt = _parse_time(getattr(t, "entry_time", "") or "")
        if dt is None:
            continue
        if dt.year not in by_year:
            by_year[dt.year] = np.zeros(12, dtype=float)
            by_year_n[dt.year] = np.zeros(12, dtype=int)
        val = float(getattr(t, "pnl", 0.0) or 0.0)
        by_year[dt.year][dt.month - 1] += val
        by_year_n[dt.year][dt.month - 1] += 1
    if not by_year:
        return MonthYearHeatmap(
            [], np.empty((0, 12), dtype=float), np.empty((0, 12), dtype=int)
        )
    years = sorted(by_year.keys())
    pnl_grid = np.stack([by_year[y] for y in years])
    n_grid = np.stack([by_year_n[y] for y in years])
    return MonthYearHeatmap(years=years, net_pnl=pnl_grid, counts=n_grid)


# ----------------------------------------------------------------------------
# Drawdown durations
# ----------------------------------------------------------------------------
@dataclass(frozen=True)
class DrawdownEpisode:
    """A complete underwater run: start → trough → recovery."""

    start_idx: int  # index of the prior peak
    trough_idx: int  # index of the deepest point
    recover_idx: Optional[int]  # None if equity never recovered
    depth_dollars: float
    depth_pct: float
    length_trades: int  # trough_idx - start_idx
    underwater_trades: int  # (recover_idx or end) - start_idx


def drawdown_durations(equity: np.ndarray) -> List[DrawdownEpisode]:
    """Find every underwater run on an equity curve.

    Each episode covers ``equity[start_idx]`` (the peak) → ``equity[trough_idx]``
    (deepest point) → ``equity[recover_idx]`` (first bar where the peak is regained,
    or None if still underwater at the end).
    """
    eq = np.asarray(equity, dtype=float)
    if eq.size == 0:
        return []
    peaks = np.maximum.accumulate(eq)
    underwater = eq < peaks
    episodes: List[DrawdownEpisode] = []
    n = eq.size
    i = 0
    last_peak_idx = 0
    while i < n:
        # Track the most recent peak (index where eq == running max).
        if eq[i] >= peaks[i]:
            last_peak_idx = i
            i += 1
            continue
        # We are underwater — start an episode rooted at last_peak_idx.
        peak_idx = last_peak_idx
        j = i
        local_trough_idx = i
        local_trough_val = eq[i]
        while j < n and underwater[j]:
            if eq[j] < local_trough_val:
                local_trough_val = eq[j]
                local_trough_idx = j
            j += 1
        recover_idx = j if j < n else None
        peak_value = float(eq[peak_idx])
        depth_d = peak_value - float(local_trough_val)
        depth_p = (depth_d / peak_value * 100.0) if peak_value > 0 else 0.0
        episodes.append(
            DrawdownEpisode(
                start_idx=peak_idx,
                trough_idx=local_trough_idx,
                recover_idx=recover_idx,
                depth_dollars=depth_d,
                depth_pct=depth_p,
                length_trades=local_trough_idx - peak_idx,
                underwater_trades=(recover_idx if recover_idx is not None else n) - peak_idx,
            )
        )
        i = j
    return episodes


# ----------------------------------------------------------------------------
# Exit-reason attribution
# ----------------------------------------------------------------------------
@dataclass(frozen=True)
class AttributionRow:
    reason: str
    count: int
    sum_pnl: float
    avg_pnl: float
    win_rate: float


def exit_reason_attribution(trades: Iterable[Any]) -> List[AttributionRow]:
    """Per-exit-reason aggregates."""
    bins: Dict[str, List[float]] = {}
    for t in trades:
        reason = str(getattr(t, "exit_reason", "") or "OTHER")
        bins.setdefault(reason, []).append(float(getattr(t, "pnl", 0.0) or 0.0))
    rows: List[AttributionRow] = []
    for reason, pnls in bins.items():
        arr = np.asarray(pnls, dtype=float)
        wins = int((arr > 0).sum())
        rows.append(
            AttributionRow(
                reason=reason,
                count=int(arr.size),
                sum_pnl=float(arr.sum()),
                avg_pnl=float(arr.mean()) if arr.size else 0.0,
                win_rate=(wins / arr.size) if arr.size else 0.0,
            )
        )
    rows.sort(key=lambda r: r.sum_pnl, reverse=True)
    return rows


# ----------------------------------------------------------------------------
# Slippage simulator
# ----------------------------------------------------------------------------
@dataclass(frozen=True)
class SlippageSimulationResult:
    base_pnls: np.ndarray
    perturbed_pnls: np.ndarray
    base_total: float
    perturbed_total: float
    pnl_delta: float
    avg_slip_per_trade: float


def simulate_slippage(
    trades: Iterable[Any],
    *,
    slip_per_trade_dollars: float,
) -> SlippageSimulationResult:
    """Apply a constant per-trade slippage cost and return the resulting series.

    ``slip_per_trade_dollars`` is subtracted from every trade's PnL (entry+exit
    one-way cost). Used to answer "what if my fills are 0.5 points worse on
    average?" without re-running the backtest.
    """
    base = np.asarray([float(getattr(t, "pnl", 0.0) or 0.0) for t in trades], dtype=float)
    if base.size == 0:
        return SlippageSimulationResult(
            base_pnls=base,
            perturbed_pnls=base.copy(),
            base_total=0.0,
            perturbed_total=0.0,
            pnl_delta=0.0,
            avg_slip_per_trade=0.0,
        )
    cost = float(slip_per_trade_dollars)
    perturbed = base - cost
    return SlippageSimulationResult(
        base_pnls=base,
        perturbed_pnls=perturbed,
        base_total=float(base.sum()),
        perturbed_total=float(perturbed.sum()),
        pnl_delta=float(perturbed.sum() - base.sum()),
        avg_slip_per_trade=cost,
    )


# ----------------------------------------------------------------------------
# Strategy-comparison helpers
# ----------------------------------------------------------------------------
@dataclass(frozen=True)
class StrategySummary:
    """A compact KPI snapshot used for comparing two backtest runs."""

    name: str
    n_trades: int
    total_pnl: float
    win_rate: float
    profit_factor: float
    expectancy: float
    max_dd_pct: float


def summarise_run(
    trades: Iterable[Any],
    *,
    name: str,
    initial_equity: float,
) -> StrategySummary:
    """Build a one-line KPI summary of a trade list."""
    pnls = np.asarray(
        [float(getattr(t, "pnl", 0.0) or 0.0) for t in trades],
        dtype=float,
    )
    if pnls.size == 0:
        return StrategySummary(name, 0, 0.0, 0.0, 0.0, 0.0, 0.0)
    wins = pnls[pnls > 0]
    losses = pnls[pnls < 0]
    gp = float(wins.sum())
    gl = float(-losses.sum())
    pf = (gp / gl) if gl > 0 else (float("inf") if gp > 0 else 0.0)
    eq = np.empty(pnls.size + 1, dtype=float)
    eq[0] = initial_equity
    eq[1:] = initial_equity + np.cumsum(pnls)
    peaks = np.maximum.accumulate(eq)
    safe = np.where(peaks > 0, peaks, 1.0)
    dd_pct = (peaks - eq) / safe * 100.0
    return StrategySummary(
        name=name,
        n_trades=int(pnls.size),
        total_pnl=float(pnls.sum()),
        win_rate=float((pnls > 0).sum()) / pnls.size,
        profit_factor=pf,
        expectancy=float(pnls.mean()),
        max_dd_pct=float(dd_pct.max()),
    )


def compare_runs(
    run_a: StrategySummary,
    run_b: StrategySummary,
) -> Dict[str, Dict[str, float]]:
    """Return a metric-by-metric diff suitable for direct rendering."""
    out: Dict[str, Dict[str, float]] = {}
    for metric in (
        "n_trades",
        "total_pnl",
        "win_rate",
        "profit_factor",
        "expectancy",
        "max_dd_pct",
    ):
        a = float(getattr(run_a, metric))
        b = float(getattr(run_b, metric))
        out[metric] = {
            "a": a,
            "b": b,
            "delta": b - a,
            "delta_pct": ((b - a) / abs(a) * 100.0) if a != 0 else float("nan"),
        }
    return out


__all__ = [
    "resolve_r_multiple",
    "resolve_period_window",
    "filter_trades_by_period",
    "MAEMFEArrays",
    "mae_mfe_arrays",
    "RMultipleArrays",
    "r_multiples",
    "HourWeekdayHeatmap",
    "hour_weekday_heatmap",
    "MonthYearHeatmap",
    "month_year_heatmap",
    "DrawdownEpisode",
    "drawdown_durations",
    "AttributionRow",
    "exit_reason_attribution",
    "SlippageSimulationResult",
    "simulate_slippage",
    "StrategySummary",
    "summarise_run",
    "compare_runs",
]
