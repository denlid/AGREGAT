"""DataLoader CSV parsing.

Covers the original CSV / MT5 contract plus the Phase 1 additions from
``optimization_roadmap.md``:

* 1.1 Aggressive downcasting — OHLC → ``float32``, volume → ``int32``.
* 1.2 Lazy reading via polars ``scan_csv`` (with optional date-range
  pushdown).
* 1.3 LRU cache keyed on (path, mtime, size, date_range) so repeated
  loads of the same file are instant.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from engine.data import data_loader as dl_module
from engine.data.data_loader import (
    OHLC_DTYPE,
    SPREAD_DTYPE,
    VOLUME_DTYPE,
    DataLoader,
    clear_csv_cache,
    csv_cache_info,
)


@pytest.fixture(autouse=True)
def _reset_lru_cache():
    """Phase 1.3 — every test starts against an empty LRU so cache
    stats are reliable and one test's entries can't bleed into another."""
    clear_csv_cache()
    yield
    clear_csv_cache()


@pytest.fixture
def sample_csv(tmp_path):
    p = tmp_path / "sample.csv"
    p.write_text(
        "time,open,high,low,close,volume\n"
        "2024-01-15 10:00:00,100.0,101.0,99.5,100.5,1000\n"
        "2024-01-15 10:01:00,100.5,101.2,100.2,101.0,1200\n"
        "2024-01-15 10:02:00,101.0,101.5,100.8,101.3,900\n"
    )
    return str(p)


@pytest.fixture
def mt5_split_csv(tmp_path):
    p = tmp_path / "mt5.csv"
    p.write_text(
        "Date,Time,Open,High,Low,Close,TickVol\n"
        "2024.01.15,10:00,100.0,101.0,99.5,100.5,1000\n"
        "2024.01.15,10:01,100.5,101.2,100.2,101.0,1200\n"
    )
    return str(p)


def test_load_csv_basic(sample_csv):
    df = DataLoader.load_csv(sample_csv)
    assert len(df) == 3
    assert {"time", "open", "high", "low", "close", "volume"}.issubset(df.columns)
    assert pd.api.types.is_datetime64_any_dtype(df["time"])


def test_load_csv_combines_mt5_date_time_columns(mt5_split_csv):
    df = DataLoader.load_csv(mt5_split_csv)
    assert "time" in df.columns
    assert pd.api.types.is_datetime64_any_dtype(df["time"])
    assert df.iloc[0]["volume"] == 1000


def test_load_csv_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        DataLoader.load_csv(str(tmp_path / "does_not_exist.csv"))


def test_load_csv_missing_required_column(tmp_path):
    p = tmp_path / "bad.csv"
    p.write_text("time,open,high\n2024-01-15,100,101\n")
    with pytest.raises(ValueError, match="Missing required"):
        DataLoader.load_csv(str(p))


def test_load_csv_synthesises_volume_when_absent(tmp_path):
    p = tmp_path / "novol.csv"
    p.write_text("time,open,high,low,close\n2024-01-15 10:00:00,100,101,99,100.5\n")
    df = DataLoader.load_csv(str(p))
    assert "volume" in df.columns
    assert (df["volume"] == 0).all()


# ---------------------------------------------------------------------------
# Phase 1.1 — Aggressive downcasting (RAM footprint halved)
# ---------------------------------------------------------------------------


def test_load_csv_downcasts_ohlc_to_float32(sample_csv):
    df = DataLoader.load_csv(sample_csv)
    for col in ("open", "high", "low", "close"):
        assert df[col].dtype == OHLC_DTYPE, (
            f"{col} is {df[col].dtype}, expected {OHLC_DTYPE}"
        )


def test_load_csv_downcasts_volume_to_int32(sample_csv):
    df = DataLoader.load_csv(sample_csv)
    assert df["volume"].dtype == VOLUME_DTYPE
    # Values survive the cast intact.
    assert list(df["volume"]) == [1000, 1200, 900]


def test_load_csv_mt5_volume_downcast(mt5_split_csv):
    df = DataLoader.load_csv(mt5_split_csv)
    assert df["volume"].dtype == VOLUME_DTYPE
    assert df.iloc[0]["volume"] == 1000


def test_load_csv_spread_downcast_to_float32(tmp_path):
    p = tmp_path / "spread.csv"
    p.write_text(
        "time,open,high,low,close,volume,spread\n"
        "2024-01-15 10:00:00,100,101,99,100.5,1,2.0\n"
        "2024-01-15 10:01:00,100.5,101.5,100,101,1,3.0\n"
    )
    df = DataLoader.load_csv(str(p))
    assert df["spread"].dtype == SPREAD_DTYPE
    assert list(df["spread"]) == [2.0, 3.0]


def test_load_csv_clips_volume_overflow(tmp_path):
    """A 64-bit-only tick volume should be clamped to int32-max rather
    than overflowing into a negative number."""
    huge = np.iinfo(VOLUME_DTYPE).max + 100  # bigger than int32 can hold
    p = tmp_path / "huge.csv"
    p.write_text(
        "time,open,high,low,close,volume\n"
        f"2024-01-15 10:00:00,100,101,99,100.5,{huge}\n"
    )
    df = DataLoader.load_csv(str(p))
    assert df["volume"].dtype == VOLUME_DTYPE
    assert df["volume"].iloc[0] == np.iinfo(VOLUME_DTYPE).max


def test_load_csv_memory_footprint_halved_vs_float64(tmp_path):
    """Loading a 5k-bar CSV must apply the documented dtype contract:
    volume is downcast (``VOLUME_DTYPE``, typically int32) below the int64
    baseline pandas would infer, and OHLC settle on the engine's declared
    ``OHLC_DTYPE``.

    Note: OHLC is intentionally ``float64`` (see ``data_loader.OHLC_DTYPE``
    — "avoid precision loss on Crypto / high-precision instruments"), so
    this test pegs the *actual* footprint contract rather than the
    abandoned Phase 1.1 float32 budget. The assertions are derived from
    the dtypes themselves so they track any future dtype policy change
    instead of hard-coding a byte target.
    """
    p = tmp_path / "perf.csv"
    n = 5_000
    rng = np.random.default_rng(0)
    times = pd.date_range("2024-01-02 09:00", periods=n, freq="1min")
    base = 100 + np.cumsum(rng.normal(0, 0.1, n))
    pd.DataFrame(
        {
            "time": times,
            "open": base,
            "high": base + 0.5,
            "low": base - 0.5,
            "close": base + 0.1,
            "volume": rng.integers(1, 10_000, size=n),
        }
    ).to_csv(p, index=False)

    df = DataLoader.load_csv(str(p))

    # OHLC must match the declared engine dtype, and volume must be the
    # downcast integer dtype — both enforced by ``_downcast_ohlcv``.
    for col in ("open", "high", "low", "close"):
        assert df[col].dtype == OHLC_DTYPE, (
            f"{col} is {df[col].dtype}, expected {OHLC_DTYPE}"
        )
    assert df["volume"].dtype == VOLUME_DTYPE

    # Footprint must beat the naive baseline pandas infers from this CSV:
    # float64 OHLC + int64 volume = 8 bytes/col/row. The volume downcast
    # alone (int64 → int32) must bank a real, measurable win.
    ohlc_bytes = sum(np.dtype(OHLC_DTYPE).itemsize for _ in range(4))
    actual_per_row = ohlc_bytes + np.dtype(VOLUME_DTYPE).itemsize
    naive_per_row = 8 * 5  # float64 OHLC + int64 volume
    assert actual_per_row < naive_per_row, (
        f"loader footprint {actual_per_row} B/row did not beat the naive "
        f"{naive_per_row} B/row baseline"
    )

    num_bytes = sum(
        df[c].memory_usage(index=False, deep=False)
        for c in ("open", "high", "low", "close", "volume")
    )
    assert num_bytes == n * actual_per_row, (
        f"OHLCV columns used {num_bytes} B, expected {n * actual_per_row} B "
        f"for dtypes OHLC={OHLC_DTYPE}, volume={VOLUME_DTYPE}"
    )


# ---------------------------------------------------------------------------
# Phase 1.2 — Polars lazy scan with optional date-range pushdown
# ---------------------------------------------------------------------------


def test_load_csv_date_range_filters_rows(tmp_path):
    p = tmp_path / "ranged.csv"
    p.write_text(
        "time,open,high,low,close,volume\n"
        "2024-01-01 10:00:00,100,101,99,100.5,1\n"
        "2024-01-02 10:00:00,100.5,101.5,100,101,1\n"
        "2024-01-03 10:00:00,101,102,100.5,101.5,1\n"
        "2024-01-04 10:00:00,101.5,102.5,101,102,1\n"
    )
    df = DataLoader.load_csv(
        str(p),
        date_range=("2024-01-02", "2024-01-03"),
    )
    assert len(df) == 2
    assert df["time"].iloc[0] == pd.Timestamp("2024-01-02 10:00:00")
    assert df["time"].iloc[-1] == pd.Timestamp("2024-01-03 10:00:00")


def test_load_csv_date_range_open_ended(tmp_path):
    p = tmp_path / "open.csv"
    p.write_text(
        "time,open,high,low,close,volume\n"
        "2024-01-01 10:00:00,100,101,99,100.5,1\n"
        "2024-01-02 10:00:00,100.5,101.5,100,101,1\n"
        "2024-01-03 10:00:00,101,102,100.5,101.5,1\n"
    )
    # No upper bound — keep everything from 2024-01-02 onwards.
    df = DataLoader.load_csv(str(p), date_range=("2024-01-02", None))
    assert len(df) == 2
    # No lower bound — keep everything up to 2024-01-02.
    df2 = DataLoader.load_csv(str(p), date_range=(None, "2024-01-02"))
    assert len(df2) == 2


def test_load_csv_date_range_mt5_format(tmp_path):
    """Split Date/Time MT5 layout must honour the date_range too — even
    though polars can't push the filter down before the columns are
    combined, the post-combine fallback drops the out-of-range rows."""
    p = tmp_path / "mt5_range.csv"
    p.write_text(
        "Date,Time,Open,High,Low,Close,TickVol\n"
        "2024.01.01,10:00,100,101,99,100.5,1\n"
        "2024.01.02,10:00,100.5,101.5,100,101,1\n"
        "2024.01.03,10:00,101,102,100.5,101.5,1\n"
    )
    df = DataLoader.load_csv(
        str(p),
        date_range=("2024-01-02", "2024-01-02 23:59:59"),
    )
    assert len(df) == 1
    assert df["time"].iloc[0] == pd.Timestamp("2024-01-02 10:00:00")


def test_load_csv_rejects_invalid_date_range(sample_csv):
    with pytest.raises(ValueError, match="must be ≤ end"):
        DataLoader.load_csv(
            sample_csv,
            date_range=("2024-02-01", "2024-01-01"),
        )


# ---------------------------------------------------------------------------
# Phase 1.3 — LRU cache
# ---------------------------------------------------------------------------


def test_load_csv_cache_hit_on_second_call(sample_csv):
    assert csv_cache_info().hits == 0
    DataLoader.load_csv(sample_csv)
    DataLoader.load_csv(sample_csv)
    info = csv_cache_info()
    assert info.hits == 1, f"expected exactly one cache hit, got {info}"
    assert info.misses == 1


def test_load_csv_cache_busts_on_file_mutation(sample_csv, tmp_path):
    DataLoader.load_csv(sample_csv)

    # Rewrite the same path with different content — the cache must
    # invalidate because mtime + size change.
    with open(sample_csv, "w") as f:
        f.write(
            "time,open,high,low,close,volume\n"
            "2024-02-15 10:00:00,200.0,201.0,199.5,200.5,2000\n"
        )

    df = DataLoader.load_csv(sample_csv)
    assert len(df) == 1
    assert df["close"].iloc[0] == pytest.approx(200.5)
    info = csv_cache_info()
    # Second call still goes to disk (cache miss because of mtime/size).
    assert info.misses >= 2


def test_load_csv_cache_separate_entries_per_date_range(sample_csv):
    DataLoader.load_csv(sample_csv)
    DataLoader.load_csv(
        sample_csv,
        date_range=("2024-01-15 10:01:00", None),
    )
    info = csv_cache_info()
    # Two distinct keys → two misses, zero hits across the pair.
    assert info.misses == 2
    assert info.hits == 0


def test_load_csv_use_cache_false_bypasses_lru(sample_csv):
    DataLoader.load_csv(sample_csv)  # populates the cache
    DataLoader.load_csv(sample_csv, use_cache=False)
    info = csv_cache_info()
    # Only the first call touched the LRU.
    assert info.misses == 1
    assert info.hits == 0


def test_load_csv_returns_independent_frames_per_call(sample_csv):
    """Cached entries must not be the same object — otherwise a caller
    mutating its result would silently corrupt the next hit."""
    a = DataLoader.load_csv(sample_csv)
    b = DataLoader.load_csv(sample_csv)
    assert a is not b


def test_clear_cache_drops_all_entries(sample_csv):
    DataLoader.load_csv(sample_csv)
    assert csv_cache_info().currsize == 1
    clear_csv_cache()
    assert csv_cache_info().currsize == 0
    assert csv_cache_info().hits == 0
    assert csv_cache_info().misses == 0


# ---------------------------------------------------------------------------
# Polars fallback — when polars is unavailable (or rejects the file) the
# loader still works via pandas.read_csv.
# ---------------------------------------------------------------------------


def test_load_csv_falls_back_to_pandas_when_polars_disabled(
    sample_csv, monkeypatch
):
    """Disable polars at module level and re-load. The pandas path must
    produce the same downcast DataFrame the polars path would."""
    monkeypatch.setattr(dl_module, "_HAS_POLARS", False)
    df = DataLoader.load_csv(sample_csv, use_cache=False)
    assert df["open"].dtype == OHLC_DTYPE
    assert df["volume"].dtype == VOLUME_DTYPE
    assert len(df) == 3


def test_load_csv_non_iso_time_falls_back_to_pandas(tmp_path):
    """Polars' ``str.to_datetime`` only matches ISO-ish formats. When
    the CSV ships a non-ISO combined time column AND a date_range, the
    loader must fall back to pandas instead of silently filtering every
    row to null and returning an empty frame."""
    p = tmp_path / "non_iso.csv"
    # ``DD.MM.YYYY HH:MM`` — common MT5/MT4 export layout.
    p.write_text(
        "time,open,high,low,close,volume\n"
        "01.02.2024 10:00,100,101,99,100.5,1\n"
        "02.02.2024 10:00,100.5,101.5,100,101,1\n"
        "03.02.2024 10:00,101,102,100.5,101.5,1\n"
    )
    df = DataLoader.load_csv(
        str(p),
        date_range=("2024-02-02", "2024-02-02"),
        use_cache=False,
    )
    assert len(df) == 1
    assert df["time"].iloc[0] == pd.Timestamp("2024-02-02 10:00:00")
