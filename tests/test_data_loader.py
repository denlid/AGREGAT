"""DataLoader CSV parsing."""

from __future__ import annotations

import pandas as pd
import pytest

from engine.data.data_loader import DataLoader


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
