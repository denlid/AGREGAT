"""Sprint 4.2 — Cross-cuts data layer.

Trade-level derivations (regime / session / day-of-week / hour-of-day
/ direction) plus a single ``cross_cut(metric, group_by, filter)`` API
the upcoming pivot UI will dispatch through.

The functions are deliberately defensive: they accept the existing
:class:`engine.models.Trade` (slot-based, no ``__dict__``) without
mutating it, and they accept any object exposing the same set of
attributes (``entry_time``, ``exit_time``, ``pnl``, ``direction`` …).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, time
from typing import Any, Callable, Iterable, Mapping, Optional

import numpy as np
import pandas as pd

from engine.models.models import get_session


def _has_attr(obj: Any, name: str) -> bool:
    """``hasattr`` + non-``None`` value — typed for mypy strict."""
    return hasattr(obj, name) and getattr(obj, name) is not None


# -----------------------------------------------------------------------------
# Per-trade derivations
# -----------------------------------------------------------------------------
def trade_session(t: Any) -> str:
    """Trading-session bucket using the entry timestamp.

    Falls through to :func:`engine.models.get_session` so the bucket
    matches the rest of the codebase. Trades whose entry time can't be
    parsed return ``"Off_Hours"``.
    """
    if _has_attr(t, "session") and t.session:
        return str(t.session)
    return get_session(getattr(t, "entry_time", None))


def trade_dow(t: Any) -> str:
    """Day-of-week label (``"Mon"`` … ``"Sun"``)."""
    ts = _to_timestamp(getattr(t, "entry_time", None))
    if ts is None:
        return "Unknown"
    return ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][int(ts.weekday())]


def trade_hod(t: Any) -> int:
    """Hour-of-day bucket (0–23). Returns ``-1`` when the time can't be parsed."""
    ts = _to_timestamp(getattr(t, "entry_time", None))
    if ts is None:
        return -1
    return int(ts.hour)


def trade_direction(t: Any) -> str:
    """Normalised direction (``"long"`` / ``"short"`` / ``"flat"``)."""
    d = getattr(t, "direction", "")
    if not isinstance(d, str):
        return "flat"
    d = d.strip().lower()
    if d in ("buy", "long", "+1", "1"):
        return "long"
    if d in ("sell", "short", "-1"):
        return "short"
    return "flat"


def trade_regime(t: Any, regimes: Optional[Mapping[str, str]] = None) -> str:
    """Optional market-regime tag.

    The trade itself does not yet carry regime info — until that
    pipeline lands the caller can pass a ``regimes`` mapping keyed by
    trade id (``str(trade.id) → "trend" | "range" | …``). Without the
    map this returns ``"unknown"``.
    """
    if regimes is None:
        return "unknown"
    key = str(getattr(t, "id", ""))
    return str(regimes.get(key, "unknown"))


def derive_trade_attributes(
    trades: Iterable[Any],
    *,
    regimes: Optional[Mapping[str, str]] = None,
) -> pd.DataFrame:
    """Project the iterable into a tidy DataFrame for cross-cut grouping.

    Columns: ``id, direction, session, dow, hod, regime, pnl,
    duration_min, entry_time, exit_time, slippage_cost, commission``.
    """
    rows: list[dict[str, Any]] = []
    for t in trades:
        ts_in = _to_timestamp(getattr(t, "entry_time", None))
        ts_out = _to_timestamp(getattr(t, "exit_time", None))
        rows.append(
            {
                "id": getattr(t, "id", -1),
                "direction": trade_direction(t),
                "session": trade_session(t),
                "dow": trade_dow(t),
                "hod": trade_hod(t),
                "regime": trade_regime(t, regimes),
                "pnl": float(getattr(t, "pnl", 0.0)),
                "duration_min": float(getattr(t, "duration_minutes", 0.0)),
                "entry_time": ts_in,
                "exit_time": ts_out,
                "slippage_cost": float(getattr(t, "slippage_cost", 0.0)),
                "commission": float(getattr(t, "commission", 0.0)),
            }
        )
    return pd.DataFrame(rows)


# -----------------------------------------------------------------------------
# cross_cut API
# -----------------------------------------------------------------------------
@dataclass(frozen=True)
class CrossCutResult:
    """Return value of :func:`cross_cut`.

    ``rows`` is a tidy DataFrame ``[group_by, value, n]`` so the
    pivot UI can sort / format without reimplementing aggregation.
    """

    metric: str
    group_by: str
    rows: pd.DataFrame


def _profit_factor(arr: np.ndarray) -> float:
    if arr.size == 0:
        return 0.0
    gains = float(arr[arr > 0].sum())
    losses = float(-arr[arr < 0].sum())
    if losses <= 0:
        return float("inf") if gains > 0 else 0.0
    return float(gains / losses) if gains > 0 else 0.0


# Built-in metrics over a per-bucket PnL array. The signatures are
# kept simple so callers can extend the registry from outside.
_METRIC_FNS: dict[str, Callable[[np.ndarray], float]] = {
    "count": lambda a: float(a.size),
    "sum_pnl": lambda a: float(a.sum()) if a.size else 0.0,
    "mean_pnl": lambda a: float(a.mean()) if a.size else 0.0,
    "median_pnl": lambda a: float(np.median(a)) if a.size else 0.0,
    "win_rate": lambda a: float((a > 0).mean()) if a.size else 0.0,
    "profit_factor": _profit_factor,
}


def cross_cut(
    trades: Iterable[Any],
    *,
    metric: str = "sum_pnl",
    group_by: str = "session",
    filter: Optional[Callable[[Any], bool]] = None,
    regimes: Optional[Mapping[str, str]] = None,
) -> CrossCutResult:
    """Group trades by ``group_by`` and compute ``metric`` per bucket.

    Parameters
    ----------
    trades
        Any iterable of trade-like objects (slot-based ``Trade`` works).
    metric
        Key into the metric registry (``"sum_pnl"``, ``"mean_pnl"``,
        ``"median_pnl"``, ``"win_rate"``, ``"profit_factor"``,
        ``"count"``).
    group_by
        Trade attribute / derivation: ``"session"``, ``"dow"``, ``"hod"``,
        ``"direction"``, ``"regime"`` or any column emitted by
        :func:`derive_trade_attributes`.
    filter
        Optional predicate evaluated on the *raw* trade — only trades
        for which ``filter(t)`` is truthy contribute.
    regimes
        Mapping of trade-id → regime label, forwarded to
        :func:`trade_regime`.
    """
    if metric not in _METRIC_FNS:
        raise ValueError(f"unknown metric '{metric}' — available: {sorted(_METRIC_FNS)}")
    selected = [t for t in trades if (filter is None or filter(t))]
    df = derive_trade_attributes(selected, regimes=regimes)
    if df.empty:
        return CrossCutResult(
            metric=metric,
            group_by=group_by,
            rows=pd.DataFrame({group_by: [], "value": [], "n": []}),
        )
    if group_by not in df.columns:
        raise ValueError(f"unknown group_by '{group_by}' — available: {sorted(df.columns)}")
    fn = _METRIC_FNS[metric]
    grouped: dict[Any, list[float]] = defaultdict(list)
    for _, row in df.iterrows():
        grouped[row[group_by]].append(float(row["pnl"]))
    rows = []
    for key, vals in grouped.items():
        arr = np.asarray(vals, dtype="float64")
        rows.append({group_by: key, "value": float(fn(arr)), "n": int(arr.size)})
    out = pd.DataFrame(rows).sort_values(group_by).reset_index(drop=True)
    return CrossCutResult(metric=metric, group_by=group_by, rows=out)


# -----------------------------------------------------------------------------
# helpers
# -----------------------------------------------------------------------------
def _to_timestamp(t: Any) -> Optional[pd.Timestamp]:
    if t is None:
        return None
    if isinstance(t, pd.Timestamp):
        return t
    if isinstance(t, datetime):
        return pd.Timestamp(t)
    if isinstance(t, time):
        return None
    try:
        return pd.Timestamp(t)
    except (TypeError, ValueError):
        return None


__all__ = [
    "trade_session",
    "trade_dow",
    "trade_hod",
    "trade_direction",
    "trade_regime",
    "derive_trade_attributes",
    "cross_cut",
    "CrossCutResult",
]
