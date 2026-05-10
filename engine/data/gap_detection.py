"""Phase 5.1 (G2) — calendar-aware gap detection for OHLCV time series.

Bar-replay backtests assume every consecutive pair of candles is one
LTF interval apart. Real CSV exports often have:

* Vendor-side outages (a few missing 1-minute bars in the middle of a
  trading session).
* Weekend gaps that are *not* gaps — they're scheduled non-trading
  windows.
* Daylight-savings transitions where the timestamp jumps an hour.

This module exposes :func:`detect_gaps` — a pure function that returns
the list of *suspicious* gaps so the GUI / data-quality dialog can
surface them to the user *before* a backtest is started. We deliberately
filter out the regularly-scheduled weekend window (Friday close →
Sunday/Monday open) so the report only contains real outages.

The function works on any DataFrame that has a ``time`` column with
datetime-coercible values. It returns plain dicts so the result can be
serialised to JSON / pickled across the GUI worker thread.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd


def _delta_seconds(times: pd.DatetimeIndex) -> np.ndarray:
    """Return the consecutive delta-in-seconds vector for ``times``.

    Pandas 2.x defaults to ``datetime64[us]`` while 1.x produced
    ``datetime64[ns]`` — divide-by-fixed-1e9 is therefore wrong.
    Casting through ``np.timedelta64`` and ``total_seconds`` honours
    whatever resolution pandas picked.
    """
    if len(times) < 2:
        return np.empty(0, dtype=np.float64)
    raw = np.diff(times.values).astype("timedelta64[ns]")
    return raw.astype(np.float64) / 1_000_000_000.0


def _infer_interval_seconds(times: pd.DatetimeIndex) -> Optional[float]:
    """Return the modal interval in seconds, or None when undeterminable.

    Mode (rather than median) handles datasets where two-thirds of the
    candles are 1m apart but a long weekend skews the median upward.
    """
    deltas = _delta_seconds(times)
    if deltas.size == 0:
        return None
    # ``mode`` over a small set is fine; we don't need scipy.
    vals, counts = np.unique(deltas, return_counts=True)
    if vals.size == 0:
        return None
    return float(vals[counts.argmax()])


def _is_weekend_gap(prev: pd.Timestamp, curr: pd.Timestamp) -> bool:
    """True when the gap straddles a Friday close → Monday open window.

    We check whether *prev* is on a Friday (or late Sunday for crypto-ish
    schedules with a Saturday-only outage) and *curr* is the next
    business day. Tolerant on purpose — a real outage will almost always
    be intra-day, while a scheduled weekend outage matches this pattern
    by construction.
    """
    if prev.weekday() == 4 and curr.weekday() == 0:  # Fri -> Mon
        return True
    if prev.weekday() == 5 and curr.weekday() == 0:  # Sat -> Mon
        return True
    if prev.weekday() == 6 and curr.weekday() == 0:  # Sun -> Mon (crypto)
        return True
    return False


def detect_gaps(
    df: pd.DataFrame,
    *,
    interval_seconds: Optional[float] = None,
    min_gap_multiplier: float = 1.5,
    skip_weekend: bool = True,
) -> List[Dict[str, Any]]:
    """Return suspicious-gap descriptors for ``df``.

    Args:
        df: Must contain a ``time`` column (any datetime-coercible
            dtype). Other columns are ignored.
        interval_seconds: The expected per-bar interval. If ``None``
            (default) we infer it from the data via the modal delta.
        min_gap_multiplier: A delta is flagged when it exceeds
            ``interval_seconds * min_gap_multiplier``. Default 1.5
            catches missing single bars without false-positive flagging
            of jittery timestamps.
        skip_weekend: When True (default) Friday→Monday-style gaps are
            silently skipped — they're not real outages.

    Returns:
        A list of ``{"start", "end", "missing_bars", "gap_seconds"}``
        dicts, sorted by ``start``. Empty list when the dataset has no
        suspicious gaps.
    """
    if "time" not in df.columns or len(df) < 2:
        return []
    times = pd.to_datetime(df["time"], errors="coerce")
    times = times.dropna()
    if len(times) < 2:
        return []
    times = pd.DatetimeIndex(times.values).sort_values()

    interval = interval_seconds
    if interval is None:
        interval = _infer_interval_seconds(times)
    if not interval or interval <= 0:
        return []

    threshold_seconds = interval * min_gap_multiplier
    deltas = _delta_seconds(times)
    out: List[Dict[str, Any]] = []
    for i, gap_seconds in enumerate(deltas):
        if gap_seconds <= threshold_seconds:
            continue
        prev = times[i]
        curr = times[i + 1]
        if skip_weekend and _is_weekend_gap(prev, curr):
            continue
        # Round down so a 2.5x gap reports "missing 1" not "missing 1.5".
        missing = int(gap_seconds // interval) - 1
        if missing < 1:
            continue
        out.append(
            {
                "start": prev.to_pydatetime(),
                "end": curr.to_pydatetime(),
                "missing_bars": missing,
                "gap_seconds": float(gap_seconds),
            }
        )
    return out


__all__ = ["detect_gaps"]
