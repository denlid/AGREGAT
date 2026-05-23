"""Extra derived metrics for the institutional tear-sheet (Phase 3.1).

All helpers operate on the same `Trade`-like protocol used by
`engine.report_export` — duck-typed attributes (``pnl``, ``entry_time``,
``exit_time``, ``direction``, ``commission``, ``spread_cost``,
``slippage_cost``, ``exit_reason``, ``id``).

These functions return plain Python primitives / NumPy arrays so the
``charts`` and ``tearsheet`` modules can render them without re-deriving
state, and so unit tests can assert against them directly.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CostAttribution:
    """Dollar drag of each execution-cost component plus % of gross PnL.

    Returned in the order the report renders: spread, slippage, commission,
    plus a ``total`` so callers can show "what backtest paid the broker".
    The percentages are signed: positive = drag, so a strategy with $1k
    gross profit and $200 commission has ``commission_pct = 20.0``.
    """

    spread_total: float = 0.0
    slippage_total: float = 0.0
    commission_total: float = 0.0
    total_costs: float = 0.0
    gross_pnl: float = 0.0  # PnL *before* costs; equals total_pnl + total_costs.
    spread_pct: float = 0.0
    slippage_pct: float = 0.0
    commission_pct: float = 0.0
    total_pct: float = 0.0


@dataclass(frozen=True)
class ExitReasonRow:
    reason: str
    count: int
    sum_pnl: float
    avg_pnl: float
    win_rate: float  # 0..1


@dataclass(frozen=True)
class TopTradeRow:
    """One row of the Top-N tables (best/worst). All scalar so the renderer
    can format independently of the underlying ``Trade`` type."""

    id: int
    direction: str
    entry_time: str
    exit_time: str
    pnl: float
    exit_reason: str


@dataclass(frozen=True)
class MonthlyReturns:
    """Year × month grid (12 cols) plus per-year totals, ready for HTML."""

    years: List[int]
    pct_grid: np.ndarray  # (n_years, 12) percentage returns (compounded)
    abs_grid: np.ndarray  # (n_years, 12) absolute PnL
    year_totals_pct: np.ndarray  # (n_years,) compounded return per year
    year_totals_abs: np.ndarray  # (n_years,) sum PnL per year


@dataclass(frozen=True)
class RollingSharpe:
    """Equity-derived rolling Sharpe (window in trade count)."""

    window: int
    values: np.ndarray  # length = n_trades; first ``window-1`` are NaN
    mean_value: float
    last_value: float


# ---------------------------------------------------------------------------
# Cost attribution
# ---------------------------------------------------------------------------


def cost_attribution(
    trades: Iterable[Any],
) -> CostAttribution:
    """Sum per-trade ``spread_cost``, ``slippage_cost`` and ``commission``
    across all trades and express each as % of gross PnL.

    Trades that pre-date Phase 1's execution model may not carry these
    fields — `getattr(..., 0.0)` keeps the function backward-compatible
    (older trades just contribute zero, the report shows "$0.00" / "0.0%").
    """
    spread = slip = comm = 0.0
    pnl_after = 0.0
    for t in trades:
        spread += float(getattr(t, "spread_cost", 0.0) or 0.0)
        slip += float(getattr(t, "slippage_cost", 0.0) or 0.0)
        comm += float(getattr(t, "commission", 0.0) or 0.0)
        pnl_after += float(getattr(t, "pnl", 0.0) or 0.0)
    total = spread + slip + comm
    # Gross = PnL *before* costs. Trade.pnl is already net of all 3 costs in
    # the engine, so we add the costs back to get the pre-cost gross PnL.
    gross = pnl_after + total
    base = abs(gross) if abs(gross) > 1e-9 else 1.0
    return CostAttribution(
        spread_total=round(spread, 2),
        slippage_total=round(slip, 2),
        commission_total=round(comm, 2),
        total_costs=round(total, 2),
        gross_pnl=round(gross, 2),
        spread_pct=round(spread / base * 100.0, 2),
        slippage_pct=round(slip / base * 100.0, 2),
        commission_pct=round(comm / base * 100.0, 2),
        total_pct=round(total / base * 100.0, 2),
    )


# ---------------------------------------------------------------------------
# Exit-reason breakdown (used in tear-sheet pie + table)
# ---------------------------------------------------------------------------


def exit_reason_rows(trades: Iterable[Any]) -> List[ExitReasonRow]:
    """Group trades by ``exit_reason`` (TP / SL / EOD / GAP_SL / MANUAL …).

    Sorted by ``count`` descending so the pie chart's largest slice is
    rendered first (this also stabilizes the SVG legend ordering across
    runs with identical input).
    """
    bins: Dict[str, List[float]] = defaultdict(list)
    for t in trades:
        reason = str(getattr(t, "exit_reason", "") or "OTHER")
        bins[reason].append(float(getattr(t, "pnl", 0.0) or 0.0))
    rows: List[ExitReasonRow] = []
    for reason, pnls in bins.items():
        arr = np.asarray(pnls, dtype=float)
        wins = int((arr > 0).sum())
        rows.append(
            ExitReasonRow(
                reason=reason,
                count=int(arr.size),
                sum_pnl=float(arr.sum()),
                avg_pnl=float(arr.mean()) if arr.size else 0.0,
                win_rate=(wins / arr.size) if arr.size else 0.0,
            )
        )
    rows.sort(key=lambda r: r.count, reverse=True)
    return rows


# ---------------------------------------------------------------------------
# Top-N best / worst trades
# ---------------------------------------------------------------------------


def top_trades(
    trades: Iterable[Any], n: int = 10
) -> Tuple[List[TopTradeRow], List[TopTradeRow]]:
    """Return ``(best, worst)`` lists of length ``min(n, total trades)``.

    The "worst" list is reversed so the most painful trade is first — this
    matches the order users skim the report: best→top, worst→bottom-up.

    Phase 6.1 perf: only the top/bottom ``n`` trades are passed through
    the relatively expensive ``_format_dt`` (which calls ``pd.to_datetime``
    once per trade). On a 50k-trade backtest with ``n=10``, the formatter
    is invoked 20 times instead of 50_000.
    """
    trade_list = list(trades)
    total = len(trade_list)
    if total == 0:
        return [], []
    pnls = np.fromiter(
        (float(getattr(t, "pnl", 0.0) or 0.0) for t in trade_list),
        dtype=float,
        count=total,
    )
    k = min(n, total)
    # ``argpartition`` is O(n) vs full ``argsort``'s O(n log n); for 50k
    # trades that's a ~5× wall-clock win on the partition itself.
    if k < total:
        top_idx = np.argpartition(pnls, total - k)[total - k :]
        top_idx = top_idx[np.argsort(-pnls[top_idx])]
        bot_idx = np.argpartition(pnls, k)[:k]
        bot_idx = bot_idx[np.argsort(pnls[bot_idx])]
    else:
        order = np.argsort(-pnls)
        top_idx = order
        bot_idx = order[::-1]

    def _row(i: int) -> TopTradeRow:
        t = trade_list[i]
        return TopTradeRow(
            id=int(getattr(t, "id", 0) or 0),
            direction=str(getattr(t, "direction", "") or ""),
            entry_time=_format_dt(getattr(t, "entry_time", "")),
            exit_time=_format_dt(getattr(t, "exit_time", "")),
            pnl=float(pnls[i]),
            exit_reason=str(getattr(t, "exit_reason", "") or ""),
        )

    best = [_row(int(i)) for i in top_idx]
    worst = [_row(int(i)) for i in bot_idx]
    return best, worst


def _format_dt(v: Any) -> str:
    if v is None or v == "":
        return ""
    try:
        return pd.to_datetime(v).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return str(v)


# ---------------------------------------------------------------------------
# Monthly returns heatmap
# ---------------------------------------------------------------------------


def monthly_returns(
    trades: Iterable[Any],
    initial_equity: float,
) -> MonthlyReturns:
    """Compute year × month return grid.

    Strategy:
        1. Sort trades by entry_time, accumulate equity.
        2. For each (year, month) compute the equity_open / equity_close;
           pct return = (close / open - 1) * 100. This is the standard
           geometric / compounded monthly return — it composes across the
           year so ``year_total = (1 + r1/100) * (1 + r2/100) * … - 1``.
        3. Absolute PnL grid = sum of trade.pnl in that bucket.
    """
    trade_list = list(trades)
    if not trade_list:
        empty = np.empty((0, 12), dtype=float)
        return MonthlyReturns([], empty, empty.copy(), np.empty(0), np.empty(0))

    # Phase 6.1: bulk parse all entry timestamps in one shot. The previous
    # per-trade ``pd.to_datetime`` was the dominant hotspot in this
    # function (~4 s on 5k trades / ~40 s on 50k). One bulk call drops
    # that to <50 ms.
    raw_times = [getattr(t, "entry_time", None) for t in trade_list]
    raw_pnls = np.fromiter(
        (float(getattr(t, "pnl", 0.0) or 0.0) for t in trade_list),
        dtype=float,
        count=len(trade_list),
    )
    ts_arr = pd.to_datetime(raw_times, errors="coerce")
    valid = ~pd.isna(ts_arr)
    ts_arr = ts_arr[valid]
    raw_pnls = raw_pnls[valid.to_numpy() if hasattr(valid, "to_numpy") else valid]
    if len(ts_arr) == 0:
        empty = np.empty((0, 12), dtype=float)
        return MonthlyReturns([], empty, empty.copy(), np.empty(0), np.empty(0))
    order = np.argsort(ts_arr.to_numpy())
    ts_arr = ts_arr[order]
    raw_pnls = raw_pnls[order]

    init = float(initial_equity) if initial_equity else 1.0
    by_year_month_open: Dict[Tuple[int, int], float] = {}
    by_year_month_close: Dict[Tuple[int, int], float] = {}
    by_year_month_abs: Dict[Tuple[int, int], float] = defaultdict(float)
    years_arr = ts_arr.year.to_numpy()
    months_arr = ts_arr.month.to_numpy()
    eq = init
    for i in range(len(ts_arr)):
        key = (int(years_arr[i]), int(months_arr[i]))
        if key not in by_year_month_open:
            by_year_month_open[key] = eq
        eq += float(raw_pnls[i])
        by_year_month_close[key] = eq
        by_year_month_abs[key] += float(raw_pnls[i])

    years = sorted({y for (y, _m) in by_year_month_open.keys()})
    pct_grid = np.zeros((len(years), 12), dtype=float)
    abs_grid = np.zeros((len(years), 12), dtype=float)
    for i, y in enumerate(years):
        for m in range(1, 13):
            k = (y, m)
            if k in by_year_month_open:
                op = by_year_month_open[k]
                cl = by_year_month_close[k]
                pct_grid[i, m - 1] = ((cl / op) - 1.0) * 100.0 if op > 0 else 0.0
                abs_grid[i, m - 1] = float(by_year_month_abs[k])
    # Year totals — geometric compound across the 12 months.
    year_totals_pct = np.zeros(len(years), dtype=float)
    year_totals_abs = abs_grid.sum(axis=1)
    for i in range(len(years)):
        prod = 1.0
        for m in range(12):
            prod *= 1.0 + pct_grid[i, m] / 100.0
        year_totals_pct[i] = (prod - 1.0) * 100.0
    return MonthlyReturns(
        years=years,
        pct_grid=pct_grid,
        abs_grid=abs_grid,
        year_totals_pct=year_totals_pct,
        year_totals_abs=year_totals_abs,
    )


# ---------------------------------------------------------------------------
# Rolling Sharpe (trade-level window)
# ---------------------------------------------------------------------------


def rolling_sharpe(
    trades: Iterable[Any],
    *,
    window: int = 60,
) -> RollingSharpe:
    """Rolling Sharpe ratio over a window of ``window`` trades.

    Trade-level Sharpe is the simplest & most robust signal of regime
    drift inside a backtest — much better than the headline number for
    answering "is this strategy decaying?". Output is a NumPy array of
    length ``n_trades`` so the chart can plot it 1-1 against the equity
    curve. Bars before the window fills are NaN (matplotlib / pyqtgraph
    naturally skip these).
    """
    pnls = np.asarray(
        [float(getattr(t, "pnl", 0.0) or 0.0) for t in trades],
        dtype=float,
    )
    n = pnls.size
    out = np.full(n, np.nan, dtype=float)
    if n == 0 or window <= 1 or n < window:
        return RollingSharpe(window=window, values=out, mean_value=0.0, last_value=0.0)
    cumsum = np.cumsum(pnls)
    cumsum2 = np.cumsum(pnls * pnls)
    for i in range(window - 1, n):
        s = cumsum[i] - (cumsum[i - window] if i >= window else 0.0)
        s2 = cumsum2[i] - (cumsum2[i - window] if i >= window else 0.0)
        mean = s / window
        var = s2 / window - mean * mean
        std = float(np.sqrt(var)) if var > 0 else 0.0
        out[i] = (mean / std) * np.sqrt(window) if std > 0 else 0.0
    finite = out[np.isfinite(out)]
    mean_v = float(finite.mean()) if finite.size else 0.0
    last_v = float(finite[-1]) if finite.size else 0.0
    return RollingSharpe(
        window=window, values=out, mean_value=mean_v, last_value=last_v
    )


# ---------------------------------------------------------------------------
# Underwater (drawdown over time) series
# ---------------------------------------------------------------------------


def underwater_series(trades: Iterable[Any], initial_equity: float) -> np.ndarray:
    """Return a NumPy array of percent-below-peak values, one per trade close.

    Entry [0] is always 0. Each subsequent value is
    ``(equity[i] - peak[i]) / peak[i] * 100`` (≤ 0). This drives the
    "underwater chart" section of the tear-sheet.
    """
    pnls = [float(getattr(t, "pnl", 0.0) or 0.0) for t in trades]
    if not pnls:
        return np.zeros(1, dtype=float)
    eq = np.empty(len(pnls) + 1, dtype=float)
    eq[0] = float(initial_equity) if initial_equity else 0.0
    eq[1:] = eq[0] + np.cumsum(pnls)
    peaks = np.maximum.accumulate(eq)
    safe = np.where(peaks > 0, peaks, 1.0)
    return (eq - peaks) / safe * 100.0


# ---------------------------------------------------------------------------
# Monte Carlo confidence helper — pulls from existing module if available
# ---------------------------------------------------------------------------


def safe_monte_carlo_summary(
    trades: Iterable[Any],
    initial_equity: float,
    *,
    n_iter: int = 500,
    block_size: int = 0,  # 0 → block_auto picks n**(1/3)
    seed: Optional[int] = 42,
) -> Optional[Dict[str, Any]]:
    """Try to compute a small MC band; never raise — falls back to None.

    The tear-sheet renders a placeholder when this returns None so older
    saves (no monte_carlo module, or n_trades < 10) still produce a valid
    HTML document.
    """
    pnl_list = [float(getattr(t, "pnl", 0.0) or 0.0) for t in trades]
    if len(pnl_list) < 10:
        return None
    try:
        from engine.analytics.monte_carlo import bootstrap_pnl
    except Exception:
        return None
    try:
        method = "block_auto" if block_size <= 0 else "block"
        result = bootstrap_pnl(
            pnls=np.asarray(pnl_list, dtype=float),
            initial_equity=float(initial_equity),
            n_iter=int(n_iter),
            store_paths=True,
            block_size=max(1, int(block_size)),
            method=method,  # type: ignore[arg-type]
            seed=seed,
        )
    except Exception:
        return None
    paths = getattr(result, "equity_paths", None)
    summary = getattr(result, "summary", {}) or {}
    if paths is None or paths.size == 0:
        return None

    # Phase 6.1 perf: the band chart is downsampled to ~2k points
    # anyway, so computing percentiles over 50_000 columns is wasted
    # work. Pre-thin to ~_MC_THIN_TARGET columns before percentile —
    # ~25× faster for 50k-trade backtests with no visible quality loss
    # on the rendered band.
    _MC_THIN_TARGET = 2_000
    n_cols = paths.shape[1]
    if n_cols > _MC_THIN_TARGET:
        col_idx = np.linspace(0, n_cols - 1, _MC_THIN_TARGET, dtype=int)
        paths = paths[:, col_idx]
    # ``np.percentile`` with three calls walks the array three times —
    # ``np.quantile`` with a vector argument does it once.
    qs = np.quantile(paths, [0.05, 0.50, 0.95], axis=0)
    return {
        "p5": qs[0],
        "p50": qs[1],
        "p95": qs[2],
        "summary": summary,
        "n_paths": int(paths.shape[0]),
    }
