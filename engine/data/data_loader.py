"""
Data Loader — Loads and validates OHLC data from CSV, Parquet, and Feather files.

CSV supports both combined 'time' column and separate 'date'+'time' columns
(MT5 format). Parquet and Feather share the same OHLC validation pipeline
as CSV; only the initial read step differs.

Phase 1 — Data Pipeline & Memory Management (см. ``optimization_roadmap.md``):

* **1.1 Aggressive downcasting** — OHLC columns are forced to ``float32`` and
  the tick-volume column to ``int32`` at load time. For a 11M-row dataset
  the RAM footprint drops from ~5.5 GB (float64 + int64) to ~1.2 GB.
* **1.2 Lazy CSV reading via Polars** — ``polars.scan_csv`` reads the file
  with a dtype schema applied at parse time. When an optional
  ``date_range`` is supplied, the time filter is pushed down to the scan
  so the engine never materialises rows outside the window. Pandas is
  used as a transparent fallback if Polars is unavailable or rejects the
  file (e.g. quoting edge cases).
* **1.3 LRU cache & RAM pool** — recent loads are memoised by
  ``(absolute_path, mtime, size, date_range)`` so reopening the same
  instrument is instant.
"""

from __future__ import annotations

import functools
import logging
import os
from datetime import datetime
from typing import Optional, Tuple, Union

import numpy as np
import pandas as pd

try:  # Polars is optional — the loader degrades gracefully when missing.
    import polars as pl  # type: ignore

    _HAS_POLARS = True
except Exception:  # pragma: no cover — exercised on platforms without polars
    pl = None  # type: ignore[assignment]
    _HAS_POLARS = False

logger = logging.getLogger("StrategyTester.data_loader")

# Phase 1.1 — final dtypes the loader downcasts numeric columns to. OHLC
# is float32 (24-bit mantissa — well above the ~7 sig-fig accuracy
# typical brokers quote at). Volume is int32 (range up to ~2.1 B is more
# than enough for any 1m tick volume).
OHLC_DTYPE = np.float32
VOLUME_DTYPE = np.int32
SPREAD_DTYPE = np.float32

# Phase 1.3 — how many distinct (file, mtime, size, date_range)
# combinations to keep memoised in the in-process RAM pool. 4 covers the
# common "compare two symbols + their resampled siblings" workflow
# without exploding memory on a 30-year dataset.
_LRU_CACHE_MAXSIZE = 4

DateLike = Union[str, datetime, pd.Timestamp, np.datetime64, None]
DateRange = Optional[Tuple[DateLike, DateLike]]


# ---------------------------------------------------------------------------
# Validation / dtype helpers
# ---------------------------------------------------------------------------


def _downcast_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    """Phase 1.1 — force OHLC → ``float32``, volume → ``int32``.

    Halves memory on the loaded DataFrame. Engine consumers that need
    ``float64`` precision (e.g. PnL accumulation) upcast on the
    ``to_numpy`` boundary, so this change is transparent downstream.
    """
    for col in ("open", "high", "low", "close"):
        if col in df.columns and df[col].dtype != OHLC_DTYPE:
            df[col] = df[col].astype(OHLC_DTYPE)

    if "volume" in df.columns and df["volume"].dtype != VOLUME_DTYPE:
        vol = pd.to_numeric(df["volume"], errors="coerce").fillna(0)
        # Clip into the int32 range — extreme tick volumes are clamped
        # rather than silently overflowing. The lower bound stays at 0
        # because OHLC volumes are non-negative by definition.
        vol = vol.clip(lower=0, upper=np.iinfo(VOLUME_DTYPE).max)
        df["volume"] = vol.astype(VOLUME_DTYPE)

    if "spread" in df.columns and df["spread"].dtype != SPREAD_DTYPE:
        df["spread"] = df["spread"].astype(SPREAD_DTYPE)

    return df


def _validate_ohlc(df: pd.DataFrame, *, source: str) -> pd.DataFrame:
    """Shared validation + normalisation for all loader backends.

    Returns a clean, sorted, de-duplicated DataFrame with lowercase
    columns ``time, open, high, low, close, volume`` and Phase 1.1
    dtype downcasting applied.
    """
    df.columns = df.columns.str.strip()
    cols_lower = {c.lower(): c for c in df.columns}

    required_base = {"time", "open", "high", "low", "close"}
    missing = required_base - set(cols_lower.keys())
    if missing:
        raise ValueError(f"Missing required columns in {source}: {missing}")

    df = df.rename(
        columns={cols_lower[c]: c for c in required_base if cols_lower.get(c) != c}
    )

    if "tickvol" in cols_lower:
        df["volume"] = df[cols_lower["tickvol"]]
    elif "volume" in cols_lower:
        df["volume"] = df[cols_lower["volume"]]
    else:
        df["volume"] = 0

    # Phase 1.4 — optional ``spread`` column (in *points*, broker
    # convention). When present it overrides the per-bar spread used
    # by ``ExecutionModel.spread_price`` so backtests can mirror the
    # bid/ask widening that happens in low-liquidity windows. Absent
    # column → behaviour unchanged. Negative values are clamped to 0.
    if "spread" in cols_lower:
        spread_col = cols_lower["spread"]
        try:
            df["spread"] = pd.to_numeric(df[spread_col], errors="coerce").fillna(0.0)
            df["spread"] = df["spread"].clip(lower=0.0)
        except Exception as exc:  # pragma: no cover — defensive parsing
            logger.warning(
                "Could not parse 'spread' column from %s: %s; ignoring.",
                source,
                exc,
            )
            df = df.drop(columns=["spread"], errors="ignore")

    if not pd.api.types.is_datetime64_any_dtype(df["time"]):
        try:
            df["time"] = pd.to_datetime(df["time"], errors="coerce")
        except Exception as e:
            raise ValueError(f"Failed to parse 'time' column: {e}") from e

    if df["time"].isna().any():
        invalid_count = df["time"].isna().sum()
        raise ValueError(f"Found {invalid_count} invalid date(s) in 'time' column")

    invalid_ohlc = (
        (df["high"] < df["low"])
        | (df["high"] < df["open"])
        | (df["high"] < df["close"])
        | (df["low"] > df["open"])
        | (df["low"] > df["close"])
    )
    if invalid_ohlc.any():
        invalid_count = invalid_ohlc.sum()
        raise ValueError(
            f"Found {invalid_count} candle(s) with invalid OHLC relationships "
            f"(H<L or H<O/C or L>O/C)"
        )

    if (df[["open", "high", "low", "close"]] <= 0).any().any():
        raise ValueError("Found candle(s) with zero or negative prices")

    df = df.sort_values("time").reset_index(drop=True)

    duplicates = df["time"].duplicated()
    if duplicates.any():
        dup_count = int(duplicates.sum())
        logger.warning(f"⚠️ Removed {dup_count} duplicate timestamp(s) from {source}")
        df = df[~duplicates].reset_index(drop=True)

    if len(df) == 0:
        raise ValueError(f"{source} contains no valid candles after validation")

    # Phase 1.1 — apply downcasting *after* validation. Range/order
    # checks above run on the original dtype to avoid mis-flagging
    # edge-case rounding (a float64 0.0 stays 0.0 once downcast, but
    # a near-tick-size value could acquire a sub-tick wobble).
    df = _downcast_ohlcv(df)

    return df


# ---------------------------------------------------------------------------
# Phase 1.2 — Polars-backed CSV reader
# ---------------------------------------------------------------------------


def _is_date_only(value: DateLike) -> bool:
    """True when ``value`` looks like a calendar date with no time part.

    Used to expand date-only end bounds to end-of-day — quants almost
    never write ``("2024-01-01", "2024-01-31")`` expecting Jan 31 to be
    truncated to midnight; they expect the full day to be included.
    """
    if isinstance(value, str):
        return "T" not in value and " " not in value
    if isinstance(value, datetime):
        # Bare ``date`` objects are also datetime-subclass instances?
        # No — ``datetime.date`` is a separate type. ``datetime`` carries
        # a time, which we treat as explicit.
        return False
    # ``datetime.date`` (and not ``datetime.datetime``) → date-only.
    try:
        from datetime import date as _date
    except ImportError:  # pragma: no cover - stdlib always present
        return False
    return isinstance(value, _date)


def _coerce_timestamp(value: DateLike) -> Optional[pd.Timestamp]:
    """Normalise a user-supplied date bound to a ``pd.Timestamp``.

    Returns ``None`` when the input is ``None`` so callers can use the
    result as an "absent" sentinel.
    """
    if value is None:
        return None
    try:
        ts = pd.Timestamp(value)
    except Exception as exc:
        raise ValueError(f"Invalid date_range bound {value!r}: {exc}") from exc
    if ts is pd.NaT:
        raise ValueError(f"Invalid date_range bound {value!r}")
    return ts


def _normalise_date_range(date_range: DateRange) -> Tuple[
    Optional[pd.Timestamp], Optional[pd.Timestamp]
]:
    if date_range is None:
        return (None, None)
    if not isinstance(date_range, tuple) or len(date_range) != 2:
        raise ValueError("date_range must be a (start, end) tuple or None")
    raw_start, raw_end = date_range
    start = _coerce_timestamp(raw_start)
    end = _coerce_timestamp(raw_end)

    # Expand a date-only end bound to "end of day" so that
    # ("2024-01-01", "2024-01-31") includes the whole of Jan 31.
    # Microsecond precision matches polars' default Datetime resolution
    # and avoids a "discarding nanoseconds" warning at filter time.
    if end is not None and _is_date_only(raw_end):
        end = end + pd.Timedelta(days=1) - pd.Timedelta(microseconds=1)

    if start is not None and end is not None and start > end:
        raise ValueError(
            f"date_range start ({start}) must be ≤ end ({end})"
        )
    return start, end


def _date_range_cache_key(
    date_range: Tuple[Optional[pd.Timestamp], Optional[pd.Timestamp]]
) -> Tuple[Optional[str], Optional[str]]:
    """Hashable form of a normalised date range for the LRU key."""
    start, end = date_range
    return (
        start.isoformat() if start is not None else None,
        end.isoformat() if end is not None else None,
    )


def _polars_read_csv(
    filepath: str,
    date_range: Tuple[Optional[pd.Timestamp], Optional[pd.Timestamp]],
) -> Optional[pd.DataFrame]:
    """Phase 1.2 — read a CSV via ``polars.scan_csv`` with dtype overrides.

    Returns a pandas DataFrame on success or ``None`` on any failure so
    the caller can fall back to ``pandas.read_csv`` (e.g. quoting
    quirks, exotic separators).

    The scan is lazy: when ``date_range`` is supplied, the time filter
    is pushed down into the scan so rows outside the window never
    materialise. OHLC and spread columns land in RAM as ``Float32``
    directly — no post-load cast. Volume is parsed as ``Int64`` (some
    feeds emit values that would overflow Int32) and downcast to
    ``int32`` later by :func:`_downcast_ohlcv`.
    """
    if not _HAS_POLARS:
        return None

    overrides: dict = {}
    # Cover lowercase, capitalised and MT5 naming conventions. Polars
    # ignores overrides for columns that aren't in the file, so listing
    # every variant is safe.
    for name in (
        "open", "high", "low", "close",
        "Open", "High", "Low", "Close",
        "OPEN", "HIGH", "LOW", "CLOSE",
    ):
        overrides[name] = pl.Float32
    for name in (
        "volume", "Volume", "VOLUME",
        "tickvol", "TickVol", "Tick_Vol",
        "TICKVOL", "tick_volume",
    ):
        # Parse wide and let _downcast_ohlcv clip + cast to int32.
        # Some FX feeds emit volumes that would overflow int32 mid-file;
        # int64 here keeps polars from raising on those rows.
        overrides[name] = pl.Int64
    for name in ("spread", "Spread", "SPREAD"):
        overrides[name] = pl.Float32

    try:
        lf = pl.scan_csv(filepath, schema_overrides=overrides, ignore_errors=False)
    except TypeError:
        # Older polars versions accept ``dtypes=`` instead of
        # ``schema_overrides=``. Try once with the legacy kwarg.
        try:
            lf = pl.scan_csv(filepath, dtypes=overrides, ignore_errors=False)  # type: ignore[call-arg]
        except Exception as exc:
            logger.debug("polars.scan_csv unavailable (%s); falling back", exc)
            return None
    except Exception as exc:
        logger.debug("polars.scan_csv failed (%s); falling back", exc)
        return None

    start, end = date_range
    try:
        schema = lf.collect_schema()
        time_col: Optional[str] = None
        for cand in ("time", "Time", "TIME"):
            if cand in schema.names():
                time_col = cand
                break
        if time_col is not None and (start is not None or end is not None):
            # Parse the time column as a Datetime so the filter pushes
            # down. ``str.to_datetime`` is the right call for string
            # columns (raw ``cast`` only works on numeric inputs).
            # ``strict=False`` keeps invalid rows as nulls.
            col_dtype = schema[time_col]
            if col_dtype == pl.String:
                # Defensive probe — if the format isn't ISO-ish,
                # ``str.to_datetime(strict=False)`` returns null for
                # every row and the filter silently produces an empty
                # frame. Bail to the pandas reader (whose
                # ``pd.to_datetime`` is far more lenient) instead of
                # masking a real format mismatch as "zero rows".
                probe = (
                    lf.select(
                        pl.col(time_col)
                        .str.to_datetime(strict=False)
                        .is_not_null()
                        .any()
                        .alias("ok")
                    )
                    .collect()
                )
                if not bool(probe[0, "ok"]):
                    logger.debug(
                        "polars couldn't parse time column %r; "
                        "falling back to pandas",
                        time_col,
                    )
                    return None
                time_expr = pl.col(time_col).str.to_datetime(strict=False)
            else:
                time_expr = pl.col(time_col).cast(pl.Datetime, strict=False)
            if start is not None:
                lf = lf.filter(time_expr >= pl.lit(start.to_pydatetime()))
            if end is not None:
                lf = lf.filter(time_expr <= pl.lit(end.to_pydatetime()))
        pl_df = lf.collect()
    except Exception as exc:
        logger.debug("polars collect failed (%s); falling back", exc)
        return None

    try:
        return pl_df.to_pandas()
    except Exception as exc:
        logger.debug("polars→pandas conversion failed (%s); falling back", exc)
        return None


def _pandas_read_csv(
    filepath: str,
    date_range: Tuple[Optional[pd.Timestamp], Optional[pd.Timestamp]],
) -> pd.DataFrame:
    """Pandas fallback reader. Applies an in-memory date-range filter
    when polars couldn't push it down at scan time."""
    df = pd.read_csv(filepath)
    start, end = date_range
    if start is None and end is None:
        return df

    cols_lower = {c.lower(): c for c in df.columns}
    if "date" in cols_lower and "time" in cols_lower:
        # MT5 split layout — the standalone ``Time`` column is just
        # ``HH:MM`` and can't be filtered before combination with
        # ``Date``. ``_load_csv_uncached`` applies the filter after
        # combining them.
        return df
    time_col = cols_lower.get("time")
    if time_col is None:
        return df
    parsed = pd.to_datetime(df[time_col], errors="coerce")
    mask = parsed.notna()
    if start is not None:
        mask &= parsed >= start
    if end is not None:
        mask &= parsed <= end
    return df.loc[mask].reset_index(drop=True)


def _load_csv_uncached(
    filepath: str,
    date_range: Tuple[Optional[pd.Timestamp], Optional[pd.Timestamp]],
) -> pd.DataFrame:
    """Read + validate a CSV (no caching). Used by the LRU wrapper and
    by callers that want to opt out of the cache."""
    try:
        df = _polars_read_csv(filepath, date_range)
        if df is None:
            df = _pandas_read_csv(filepath, date_range)
    except Exception as e:
        raise ValueError(f"Failed to read CSV: {e}") from e

    df.columns = df.columns.str.strip()
    cols_lower = {c.lower(): c for c in df.columns}

    # Handle MT5 format: separate 'date' and 'time' columns
    if "date" in cols_lower and "time" in cols_lower and "open" in cols_lower:
        date_col = cols_lower["date"]
        time_col = cols_lower["time"]
        try:
            df["time"] = pd.to_datetime(
                df[date_col].astype(str) + " " + df[time_col].astype(str),
                errors="coerce",
            )
            df = df.drop(columns=[date_col, time_col])
        except Exception as e:
            raise ValueError(f"Failed to combine Date+Time columns: {e}") from e

        # Apply the date range now that the time column exists. This
        # mirrors the pushdown that polars already did when the file
        # carried a combined ``time`` column.
        start, end = date_range
        if start is not None or end is not None:
            mask = df["time"].notna()
            if start is not None:
                mask &= df["time"] >= start
            if end is not None:
                mask &= df["time"] <= end
            df = df.loc[mask].reset_index(drop=True)

    return _validate_ohlc(df, source="CSV")


# ---------------------------------------------------------------------------
# Phase 1.3 — LRU cache for recently loaded instruments
# ---------------------------------------------------------------------------


@functools.lru_cache(maxsize=_LRU_CACHE_MAXSIZE)
def _load_csv_cached(
    filepath: str,
    mtime_ns: int,
    size: int,
    date_range_key: Tuple[Optional[str], Optional[str]],
) -> pd.DataFrame:
    """Memoised CSV load keyed on (path, mtime_ns, size, date_range).

    The mtime/size pair invalidates entries whenever the underlying
    file changes on disk, so a stale cache can never silently feed an
    outdated dataset into a backtest. Exceptions raised by the wrapped
    loader are *not* cached (functools.lru_cache only caches return
    values), so a bad CSV is re-attempted on every call.
    """
    start = pd.Timestamp(date_range_key[0]) if date_range_key[0] else None
    end = pd.Timestamp(date_range_key[1]) if date_range_key[1] else None
    return _load_csv_uncached(filepath, (start, end))


def clear_csv_cache() -> None:
    """Drop every entry from the CSV LRU cache. Exposed for tests and
    for the GUI's "Reload data" affordance."""
    _load_csv_cached.cache_clear()


def csv_cache_info() -> functools._CacheInfo:
    """Return the LRU cache statistics (hits / misses / size). Handy
    for observability and for regression-testing cache behaviour."""
    return _load_csv_cached.cache_info()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


class DataLoader:
    @staticmethod
    def load_csv(
        filepath: str,
        *,
        date_range: DateRange = None,
        use_cache: bool = True,
    ) -> pd.DataFrame:
        """Load an OHLCV CSV and return a clean, validated DataFrame.

        Parameters
        ----------
        filepath:
            Path to the CSV. Both combined ``time`` columns and MT5's
            split ``Date``/``Time`` pair are supported.
        date_range:
            Optional ``(start, end)`` tuple (inclusive on both bounds).
            When supplied, rows outside the window are dropped at scan
            time via polars, avoiding the cost of materialising the
            full file in RAM. Either bound may be ``None`` for an
            open-ended filter.
        use_cache:
            When ``True`` (default) the result is memoised on
            ``(path, mtime, size, date_range)``. Disable for ad-hoc
            tooling that doesn't want stale results across rewrites
            within the same nanosecond.
        """
        if not os.path.exists(filepath):
            raise FileNotFoundError(f"Data file not found: {filepath}")

        normalised = _normalise_date_range(date_range)

        if use_cache:
            stat = os.stat(filepath)
            cached = _load_csv_cached(
                os.path.abspath(filepath),
                stat.st_mtime_ns,
                stat.st_size,
                _date_range_cache_key(normalised),
            )
            # Defensive shallow copy — callers occasionally mutate the
            # returned frame (e.g. ``set_index``); a shared cached
            # object would propagate those changes back into the next
            # hit. Shallow is fine because columns are immutable from
            # the loader's POV.
            return cached.copy(deep=False)

        return _load_csv_uncached(filepath, normalised)

    @staticmethod
    def load_parquet(filepath: str) -> pd.DataFrame:
        """Load OHLC data from a Parquet file (pyarrow backend).

        Preserves the same column and validation contract as
        :meth:`load_csv`. Parquet files are typically 5-10× faster to
        read than equivalent CSVs on large datasets (200k+ bars).
        """
        if not os.path.exists(filepath):
            raise FileNotFoundError(f"Data file not found: {filepath}")

        try:
            df = pd.read_parquet(filepath, engine="pyarrow")
        except Exception as e:
            raise ValueError(f"Failed to read Parquet: {e}") from e

        return _validate_ohlc(df, source="Parquet")

    @staticmethod
    def load_feather(filepath: str) -> pd.DataFrame:
        """Load OHLC data from a Feather (IPC) file.

        Feather is Arrow's zero-copy columnar on-disk format — best for
        local caching where read speed is paramount and interoperability
        with non-Python tools isn't needed.
        """
        if not os.path.exists(filepath):
            raise FileNotFoundError(f"Data file not found: {filepath}")

        try:
            df = pd.read_feather(filepath, use_threads=True)
        except Exception as e:
            raise ValueError(f"Failed to read Feather: {e}") from e

        return _validate_ohlc(df, source="Feather")
