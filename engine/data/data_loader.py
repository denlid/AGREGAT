"""
Data Loader — Loads and validates OHLC data from CSV, Parquet, and Feather files.

CSV supports both combined 'time' column and separate 'date'+'time' columns
(MT5 format). Parquet and Feather share the same OHLC validation pipeline
as CSV; only the initial read step differs.
"""

import logging
import os

import pandas as pd

logger = logging.getLogger("StrategyTester.data_loader")


def _validate_ohlc(df: pd.DataFrame, *, source: str) -> pd.DataFrame:
    """Shared validation + normalisation for all loader backends.

    Returns a clean, sorted, de-duplicated DataFrame with lowercase
    columns ``time, open, high, low, close, volume``.
    """
    df.columns = df.columns.str.strip()
    cols_lower = {c.lower(): c for c in df.columns}

    required_base = {"time", "open", "high", "low", "close"}
    missing = required_base - set(cols_lower.keys())
    if missing:
        raise ValueError(f"Missing required columns in {source}: {missing}")

    df = df.rename(columns={cols_lower[c]: c for c in required_base if cols_lower.get(c) != c})

    if "tickvol" in cols_lower:
        df["volume"] = df[cols_lower["tickvol"]]
    elif "volume" in cols_lower:
        df["volume"] = df[cols_lower["volume"]]
    else:
        df["volume"] = 0

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

    return df


class DataLoader:
    @staticmethod
    def load_csv(filepath: str) -> pd.DataFrame:
        if not os.path.exists(filepath):
            raise FileNotFoundError(f"Data file not found: {filepath}")

        try:
            df = pd.read_csv(filepath)
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
                    df[date_col].astype(str) + " " + df[time_col].astype(str), errors="coerce"
                )
                df = df.drop(columns=[date_col, time_col])
            except Exception as e:
                raise ValueError(f"Failed to combine Date+Time columns: {e}") from e

        return _validate_ohlc(df, source="CSV")

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
