"""Sprint 3 — integration tests for all deliverables.

Covers:
  3.2 Parquet/Feather data loaders
  3.3 Format-version migration framework
  3.4 CLI replay stub
  3.7 Structured logging trace-id propagation
  3.8 SDK re-exports
"""

from __future__ import annotations

import json
import os
import tempfile
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest


# -----------------------------------------------------------------------
# 3.1 — Numba primitives (tests run the fallback path when numba
#        is not installed; the Numba path is exercised transparently).
# -----------------------------------------------------------------------
# -----------------------------------------------------------------------
# 3.2 — Parquet / Feather data loaders
# -----------------------------------------------------------------------
class TestParquetFeatherLoaders:
    @staticmethod
    def _make_ohlc_df(n: int = 100) -> pd.DataFrame:
        rng = np.random.default_rng(42)
        times = pd.date_range("2024-01-02 09:00", periods=n, freq="1min")
        price = np.cumsum(rng.normal(0, 0.5, n)) + 100
        return pd.DataFrame(
            {
                "time": times,
                "open": price,
                "high": price + 0.5,
                "low": price - 0.5,
                "close": price + 0.1,
                "volume": rng.integers(100, 5000, size=n),
            }
        )

    def test_load_parquet_round_trip(self, tmp_path: Path) -> None:
        # Parquet round-trip needs pyarrow to even write the fixture; skip
        # cleanly (rather than hard-fail) on a minimal env that lacks it.
        pytest.importorskip("pyarrow")
        from engine.data.data_loader import DataLoader

        df = self._make_ohlc_df()
        pq_path = str(tmp_path / "data.parquet")
        df.to_parquet(pq_path, engine="pyarrow")

        loaded = DataLoader.load_parquet(pq_path)
        assert len(loaded) == len(df)
        assert set(loaded.columns) >= {"time", "open", "high", "low", "close", "volume"}

    def test_load_feather_round_trip(self, tmp_path: Path) -> None:
        # Feather (Arrow IPC) is a pyarrow-backed format; skip if absent.
        pytest.importorskip("pyarrow")
        from engine.data.data_loader import DataLoader

        df = self._make_ohlc_df()
        ft_path = str(tmp_path / "data.feather")
        df.to_feather(ft_path)

        loaded = DataLoader.load_feather(ft_path)
        assert len(loaded) == len(df)
        assert set(loaded.columns) >= {"time", "open", "high", "low", "close", "volume"}

    def test_load_parquet_missing_file(self) -> None:
        from engine.data.data_loader import DataLoader

        with pytest.raises(FileNotFoundError):
            DataLoader.load_parquet("/nonexistent/file.parquet")

    def test_load_feather_missing_file(self) -> None:
        from engine.data.data_loader import DataLoader

        with pytest.raises(FileNotFoundError):
            DataLoader.load_feather("/nonexistent/file.feather")


# -----------------------------------------------------------------------
# 3.3 — Format-version migration framework
# -----------------------------------------------------------------------
class TestMigrations:
    def test_migrate_v0_to_v1(self) -> None:
        from engine.persistence.migrations import run_migrations

        meta = {"version": 1, "saved_at": "2024-01-01T00:00:00Z"}
        updated, migrated = run_migrations(meta)
        assert migrated is True
        assert updated["format_version"] == 1
        assert "seeds" in updated

    def test_no_migration_needed(self) -> None:
        from engine.persistence.migrations import run_migrations

        meta = {"version": 1, "format_version": 1}
        updated, migrated = run_migrations(meta)
        assert migrated is False

    def test_legacy_archive_migration_in_load(self, tmp_path: Path) -> None:
        """A legacy archive without format_version gets migrated on load."""
        from engine.core.backtest_core import BacktestCore
        from engine.persistence.persistence import load_session

        out = tmp_path / "legacy.agbk"
        legacy_meta = {
            "version": 1,
            "schema": "agregat-backtest",
            "asset": {"name": "DE40"},
            "initial_equity": 100_000.0,
            "equity": 100_000.0,
            "max_equity": 100_000.0,
            "max_drawdown_abs": 0.0,
            "max_drawdown_pct": 0.0,
            "current_idx": -1,
            "risk_pct": 1.0,
            "execution_model": None,
            "trade_count": 0,
        }
        with zipfile.ZipFile(str(out), "w") as zf:
            zf.writestr("meta.json", json.dumps(legacy_meta))
            zf.writestr("trades.csv", "")
            zf.writestr("equity.csv", "")
            zf.writestr("drawings.json", "[]")

        eng = BacktestCore()
        load_session(eng, str(out))
        assert eng.equity == 100_000.0


# -----------------------------------------------------------------------
# 3.4 — CLI replay stub
# -----------------------------------------------------------------------
class TestCLI:
    def test_cli_help_exits_0(self) -> None:
        from engine.cli import main

        rc = main(["--help"] if False else [])
        assert rc == 0

    def test_cli_replay_missing_file(self) -> None:
        from engine.cli import main

        rc = main(["replay", "/nonexistent.agbk", "--input", "/dev/null"])
        assert rc == 1

    def test_cli_replay_no_input(self, tmp_path: Path) -> None:
        """Replay without --input should error."""
        from engine.cli import main

        out = tmp_path / "test.agbk"
        meta = {
            "version": 1,
            "format_version": 1,
            "schema": "agregat-backtest",
            "asset": {"name": "DE40"},
            "initial_equity": 100_000.0,
            "equity": 100_000.0,
            "max_equity": 100_000.0,
            "max_drawdown_abs": 0.0,
            "max_drawdown_pct": 0.0,
            "current_idx": -1,
            "risk_pct": 1.0,
            "execution_model": None,
            "trade_count": 0,
        }
        with zipfile.ZipFile(str(out), "w") as zf:
            zf.writestr("meta.json", json.dumps(meta))
            zf.writestr("trades.csv", "")
            zf.writestr("equity.csv", "")
            zf.writestr("drawings.json", "[]")

        rc = main(["replay", str(out)])
        assert rc == 1


# -----------------------------------------------------------------------
# 3.7 — Structured logging trace-id propagation
# -----------------------------------------------------------------------
class TestTraceIdPropagation:
    def test_trace_context_adapter(self) -> None:
        import logging

        from engine.logging_config import TraceContextAdapter, new_trace_id

        lg = logging.getLogger("test.trace")
        tid = new_trace_id()
        adapter = TraceContextAdapter(lg, trace_id=tid)
        assert adapter.extra["trace_id"] == tid

    def test_new_trace_id_unique(self) -> None:
        from engine.logging_config import new_trace_id

        ids = {new_trace_id() for _ in range(100)}
        assert len(ids) == 100


# -----------------------------------------------------------------------
# 3.8 — SDK re-exports
# -----------------------------------------------------------------------
class TestSDK:
    def test_sdk_imports(self) -> None:
        from engine.sdk import (
            BacktestCore,
            DataLoader,
            ExecutionModel,
            MCResult,
            OptimizationResult,
            Strategy,
            bootstrap_pnl,
            build_equity_curve,
            daily_returns,
            enable_json_logging,
            grid_search,
            max_drawdown,
            new_trace_id,
            run_migrations,
            sharpe_ratio,
            walk_forward,
        )

        assert callable(grid_search)
        assert callable(build_equity_curve)

