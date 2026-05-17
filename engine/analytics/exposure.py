"""Exposure heatmap — 24-hour × 7-weekday in-position grid (Phase 4.4).

For every closed trade we know ``entry_time`` and ``exit_time``. To
build the heatmap we need to count, for each (weekday, hour) bucket,
how many *bars* the strategy was sitting in a position. Dividing by the
total number of bars the underlying data series spent in that same
bucket yields a fraction in [0, 1] that we render as cell intensity.

Two modes are exposed:

* ``exposure_heatmap_from_trades`` — uses minute-level resolution by
  expanding each trade into a list of one-minute slots. Fast for the
  trade counts AGREGAT typically produces (≤ 50k); ``O(sum(durations))``
  but the constant is tiny.

* ``exposure_heatmap_from_bar_array`` — preferred when the engine has a
  bar-aligned ``in_position`` boolean array (Phase 1.1's bar-level
  equity already yields the same shape). Pure NumPy, ``O(n_bars)``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Optional

import numpy as np
import pandas as pd


WEEKDAY_LABELS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


@dataclass(frozen=True)
class ExposureHeatmap:
    """7×24 in-position fraction grid plus support arrays.

    Attributes
    ----------
    in_position
        ``(7, 24)`` array of bars where the strategy was holding a
        position. Sums to total in-position bar count.
    total_bars
        ``(7, 24)`` array of bars the data series spent in each bucket
        (denominator for the fraction). Empty hours have ``0`` so the
        corresponding fraction is also ``0`` (we don't generate
        misleading 100% on a single-trade bucket).
    fraction
        ``(7, 24)`` array in [0, 1] — the headline heatmap.
    n_bars_total
        Sum of ``total_bars`` (sanity check).
    n_bars_in_position
        Sum of ``in_position`` (matches sum of all trade durations
        ± rounding).
    """

    in_position: np.ndarray
    total_bars: np.ndarray
    fraction: np.ndarray
    n_bars_total: int
    n_bars_in_position: int


# ---------------------------------------------------------------------------
# Trade-driven implementation (no bar array required)
# ---------------------------------------------------------------------------


def _expand_trade_minutes(trade: Any) -> Optional[pd.DatetimeIndex]:
    """Return a minute-frequency datetime index covering the trade.

    None when timestamps are missing/invalid — caller skips the trade.

    .. note::
        Kept for backward compatibility / single-trade callers. The
        bulk path in :func:`exposure_heatmap_from_trades` no longer
        uses this helper because parsing each trade's timestamps via
        :func:`pd.to_datetime` is ~30× slower than a single bulk
        ``pd.to_datetime(list_of_strings)`` call (Phase 6.1 profiling
        showed ~9 s of the report's ~30 s went into per-trade parsing).
    """
    et = pd.to_datetime(getattr(trade, "entry_time", None), errors="coerce")
    xt = pd.to_datetime(getattr(trade, "exit_time", None), errors="coerce")
    if pd.isna(et) or pd.isna(xt) or xt < et:
        return None
    if xt == et:
        return pd.DatetimeIndex([et])
    return pd.date_range(start=et, end=xt, freq="min", inclusive="left")


def _bulk_parse_times(trades: list) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Parse ``entry_time`` / ``exit_time`` for *all* trades in one shot.

    Returns ``(entries, exits, valid_mask)`` where ``entries`` and
    ``exits`` are ``ndarray[datetime64[ns]]`` and ``valid_mask`` is the
    rows where both timestamps parsed successfully. ~30× faster than
    parsing each trade individually because pandas amortises format
    inference across the array.
    """
    entry_strs = [getattr(t, "entry_time", None) for t in trades]
    exit_strs = [getattr(t, "exit_time", None) for t in trades]
    et = pd.to_datetime(entry_strs, errors="coerce").to_numpy()
    xt = pd.to_datetime(exit_strs, errors="coerce").to_numpy()
    valid = ~(pd.isna(et) | pd.isna(xt)) & (xt >= et)
    return et, xt, valid


def exposure_heatmap_from_trades(
    trades: Iterable[Any],
    data_index: Optional[pd.DatetimeIndex] = None,
    *,
    freq: str = "min",
) -> ExposureHeatmap:
    """Build the 7×24 heatmap from trade timestamps.

    Parameters
    ----------
    trades
        Iterable of objects with ``entry_time`` / ``exit_time`` attrs.
    data_index
        Optional datetime index of the *full* data series (the
        denominator). If omitted, the denominator falls back to a
        synthetic span = ``(min entry, max exit)`` minute-wide grid;
        accurate enough for sanity-check views but biased toward over-
        counting hours the data didn't actually cover.
    freq
        Resolution of the in-position expansion. Default ``"min"`` — for
        higher-frequency tests pass ``"30s"`` etc.

    Performance
    -----------
    Phase 6.1 rewrote the inner loop to bulk-parse all timestamps once,
    then expand each trade's minute span via NumPy ``arange`` instead
    of ``pd.date_range``. On a 50k-trade synthetic load this dropped
    the section's runtime from ~10 s → ~0.6 s.
    """
    in_pos = np.zeros((7, 24), dtype=np.int64)
    trade_list = list(trades)
    if not trade_list:
        total = np.zeros((7, 24), dtype=np.int64)
        return ExposureHeatmap(in_pos, total, in_pos.astype(float), 0, 0)

    et_arr, xt_arr, valid = _bulk_parse_times(trade_list)
    if not valid.any():
        total = np.zeros((7, 24), dtype=np.int64)
        return ExposureHeatmap(in_pos, total, in_pos.astype(float), 0, 0)

    et_arr = et_arr[valid]
    xt_arr = xt_arr[valid]

    # Per-trade duration in minutes; clip equal-timestamp trades to 1.
    minute_unit = np.timedelta64(1, "m")
    durs = ((xt_arr - et_arr) / minute_unit).astype(np.int64)
    durs = np.where(durs <= 0, 1, durs)

    # Expand into a flat array of minute-stamps. ``np.repeat`` lets us
    # build the per-trade origin, ``cumcount`` (custom) gives the
    # offset within each trade.
    n_total = int(durs.sum())
    if n_total == 0:
        total = np.zeros((7, 24), dtype=np.int64)
        return ExposureHeatmap(in_pos, total, in_pos.astype(float), 0, 0)

    origins = np.repeat(et_arr, durs)
    offsets = np.arange(n_total, dtype=np.int64) - np.repeat(
        np.cumsum(durs) - durs, durs
    )
    minutes = origins + offsets * minute_unit
    minutes_idx = pd.DatetimeIndex(minutes)
    wd = minutes_idx.weekday.to_numpy()
    hr = minutes_idx.hour.to_numpy()
    np.add.at(in_pos, (wd, hr), 1)
    earliest = pd.Timestamp(et_arr.min())
    latest = pd.Timestamp(xt_arr.max())

    total = np.zeros((7, 24), dtype=np.int64)
    if data_index is not None and len(data_index) > 0:
        wd_full = data_index.weekday.to_numpy()
        hr_full = data_index.hour.to_numpy()
        np.add.at(total, (wd_full, hr_full), 1)
    elif earliest is not None and latest is not None:
        synthetic = pd.date_range(
            start=earliest, end=latest, freq=freq, inclusive="left"
        )
        wd_full = synthetic.weekday.to_numpy()
        hr_full = synthetic.hour.to_numpy()
        np.add.at(total, (wd_full, hr_full), 1)

    safe_total = np.where(total > 0, total, 1)
    fraction = in_pos / safe_total
    fraction = np.where(total > 0, fraction, 0.0)
    fraction = np.clip(fraction, 0.0, 1.0)
    return ExposureHeatmap(
        in_position=in_pos,
        total_bars=total,
        fraction=fraction,
        n_bars_total=int(total.sum()),
        n_bars_in_position=int(in_pos.sum()),
    )


# ---------------------------------------------------------------------------
# Bar-array implementation (preferred when the engine recorded it)
# ---------------------------------------------------------------------------


def exposure_heatmap_from_bar_array(
    timestamps: pd.DatetimeIndex,
    in_position: np.ndarray,
) -> ExposureHeatmap:
    """``in_position`` is a length-N boolean array aligned with ``timestamps``."""
    in_position = np.asarray(in_position).astype(bool)
    n = int(min(len(timestamps), in_position.size))
    if n == 0:
        zeros = np.zeros((7, 24), dtype=np.int64)
        return ExposureHeatmap(
            in_position=zeros,
            total_bars=zeros.copy(),
            fraction=zeros.astype(float),
            n_bars_total=0,
            n_bars_in_position=0,
        )
    ts = timestamps[:n]
    ip = in_position[:n]
    wd = ts.weekday.to_numpy()
    hr = ts.hour.to_numpy()
    in_pos = np.zeros((7, 24), dtype=np.int64)
    total = np.zeros((7, 24), dtype=np.int64)
    np.add.at(total, (wd, hr), 1)
    np.add.at(in_pos, (wd[ip], hr[ip]), 1)
    safe_total = np.where(total > 0, total, 1)
    fraction = np.where(total > 0, in_pos / safe_total, 0.0)
    return ExposureHeatmap(
        in_position=in_pos,
        total_bars=total,
        fraction=fraction.astype(float),
        n_bars_total=int(total.sum()),
        n_bars_in_position=int(in_pos.sum()),
    )
