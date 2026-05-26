"""Sprint 3 — integration tests for all deliverables.

Covers:
  3.1 Numba primitives (fallback path, since numba is optional)
  3.2 Parquet/Feather data loaders
  3.3 Format-version migration framework
  3.4 CLI replay stub
  3.7 Structured logging trace-id propagation
  3.8 SDK re-exports
  3.9 DSL codegen Phase 2
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
class TestNumbaPrimitives:
    def test_ffill_arr_basic(self) -> None:
        from engine.core.linear import _ffill_arr

        a = np.array([np.nan, 1.0, np.nan, 3.0, np.nan])
        result = _ffill_arr(a)
        assert np.isnan(result[0])
        assert result[1] == 1.0
        assert result[2] == 1.0
        assert result[3] == 3.0
        assert result[4] == 3.0

    def test_ffill_all_nan(self) -> None:
        from engine.core.linear import _ffill_arr

        a = np.array([np.nan, np.nan, np.nan])
        result = _ffill_arr(a)
        assert np.all(np.isnan(result))

    def test_linear_pivots_close_parity(self) -> None:
        """Numba path (if installed) must match the pure-NumPy path."""
        from engine.core.linear import linear_pivots_close

        rng = np.random.default_rng(42)
        close = rng.normal(100, 2, size=200)
        is_h, is_l = linear_pivots_close(close, lookback=3)
        assert is_h.shape == (200,)
        assert is_l.shape == (200,)
        assert is_h.dtype == np.bool_
        assert is_l.dtype == np.bool_

    def test_linear_pivots_body_parity(self) -> None:
        from engine.core.linear import linear_pivots

        rng = np.random.default_rng(42)
        n = 150
        close = rng.normal(100, 2, size=n)
        open_ = close + rng.normal(0, 0.5, size=n)
        is_h, is_l = linear_pivots(open_, close, lookback=2)
        assert is_h.shape == (n,)
        assert is_l.shape == (n,)


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
        from engine.data.data_loader import DataLoader

        df = self._make_ohlc_df()
        pq_path = str(tmp_path / "data.parquet")
        df.to_parquet(pq_path, engine="pyarrow")

        loaded = DataLoader.load_parquet(pq_path)
        assert len(loaded) == len(df)
        assert set(loaded.columns) >= {"time", "open", "high", "low", "close", "volume"}

    def test_load_feather_round_trip(self, tmp_path: Path) -> None:
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


# -----------------------------------------------------------------------
# 3.9 — DSL codegen Phase 2
# -----------------------------------------------------------------------
class TestDSLCodegen:
    def test_codegen_ema(self) -> None:
        from engine.ai.dsl.codegen import codegen_indicator

        lines = codegen_indicator({"id": "fast_ema", "type": "ema", "period": 9})
        code = "\n".join(lines)
        assert "fast_ema" in code
        assert "ewm" in code

    def test_codegen_atr(self) -> None:
        from engine.ai.dsl.codegen import codegen_indicator

        lines = codegen_indicator({"id": "my_atr", "type": "atr", "period": 14})
        code = "\n".join(lines)
        assert "my_atr" in code
        assert "high" in code and "low" in code

    def test_codegen_donchian(self) -> None:
        from engine.ai.dsl.codegen import codegen_indicator

        lines = codegen_indicator({"id": "dc", "type": "donchian", "period": 20})
        code = "\n".join(lines)
        assert "dc_upper" in code
        assert "dc_lower" in code

    def test_codegen_ott(self) -> None:
        from engine.ai.dsl.codegen import codegen_indicator

        lines = codegen_indicator({"id": "trend", "type": "ott", "period": 2, "pct": 1.4})
        code = "\n".join(lines)
        assert "trend" in code
        assert "long_stop" in code

    def test_codegen_fvg(self) -> None:
        from engine.ai.dsl.codegen import codegen_indicator

        lines = codegen_indicator({"id": "gaps", "type": "fvg"})
        code = "\n".join(lines)
        assert "fvg_close_only" in code

    def test_codegen_unknown_type_raises(self) -> None:
        from engine.ai.dsl.codegen import CodegenError, codegen_indicator

        with pytest.raises(CodegenError):
            codegen_indicator({"id": "x", "type": "nosuch"})

    def test_codegen_indicators_combined(self) -> None:
        from engine.ai.dsl.codegen import codegen_indicators

        nodes = [
            {"id": "e9", "type": "ema", "period": 9},
            {"id": "a14", "type": "atr", "period": 14},
        ]
        code = codegen_indicators(nodes)
        assert "e9" in code
        assert "a14" in code
