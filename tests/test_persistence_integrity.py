"""Sprint 2.2 / 2.3 / 2.4 — Persistence integrity, input hash, seeds.

The ``.agbk`` archive grew three new ``meta.json`` fields in Sprint 2:

* ``format_version`` — schema version gate (Sprint 3 will add a
  migration framework keyed by it).
* ``agbk_sha256`` — SHA-256 over the canonical bytes of the data
  members + a stable subset of ``meta.json`` itself. On load the hash
  is recomputed and compared; mismatches raise
  :class:`engine.IntegrityError`.
* ``input_csv_sha256`` / ``seeds`` — provenance fields used by the
  upcoming CLI replay (Sprint 2.4).

This module asserts each contract in turn, plus the legacy fallback
where archives saved before Sprint 2 (no hash, no format_version)
still load — just with a warning instead of a refusal.
"""

from __future__ import annotations

import json
import os
import zipfile
from pathlib import Path

import pandas as pd
import pytest

from engine import (
    AGBK_FORMAT_VERSION,
    AGBK_VERSION,
    BacktestCore,
    IntegrityError,
    Trade,
    load_session,
    load_session_raw,
    save_session,
)


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------
def _make_engine_with_one_trade() -> BacktestCore:
    """Build a tiny BacktestCore engine with a single closed trade.

    We don't need a full simulation — ``save_session`` only reads from
    a handful of attributes. A single hand-crafted ``Trade`` is enough
    to exercise the full save/load round-trip including the hash check.
    """
    eng = BacktestCore()
    eng.initial_equity = 100_000.0
    eng.equity = 100_250.0
    eng.max_equity = 100_500.0
    eng.max_drawdown_abs = 200.0
    eng.max_drawdown_pct = 0.2
    eng.current_idx = 99
    eng.risk_pct = 1.0
    eng.trades = [
        Trade(
            id=1,
            direction="long",
            entry_price=1500.0,
            exit_price=1505.0,
            pnl=250.0,
            pnl_points=5.0,
            entry_index=10,
            exit_index=20,
            entry_time=pd.Timestamp("2020-01-01 09:00", tz="UTC"),
            exit_time=pd.Timestamp("2020-01-01 10:00", tz="UTC"),
            volume=1.0,
            duration_minutes=60,
            exit_reason="tp_hit",
            mae=10.0,
            mfe=15.0,
            session="London",
            initial_sl=1495.0,
            rr=2.0,
        )
    ]
    eng.equity_track = [eng.initial_equity, eng.equity]
    eng.drawings = []
    return eng


# ---------------------------------------------------------------------------
# Sprint 2.2 — format_version + agbk_sha256
# ---------------------------------------------------------------------------
def test_save_session_writes_format_version_and_hash(tmp_path: Path) -> None:
    """A freshly saved archive carries both ``format_version`` and ``agbk_sha256``."""
    eng = _make_engine_with_one_trade()
    out = tmp_path / "session.agbk"
    save_session(eng, str(out))

    assert out.exists(), "save_session did not create the archive"
    with zipfile.ZipFile(out, "r") as zf:
        meta = json.loads(zf.read("meta.json").decode("utf-8"))

    assert meta["format_version"] == AGBK_FORMAT_VERSION
    assert meta["version"] == AGBK_VERSION  # legacy field still present
    sha = meta["agbk_sha256"]
    assert isinstance(sha, str)
    assert len(sha) == 64
    assert all(c in "0123456789abcdef" for c in sha)


def test_save_then_load_preserves_state(tmp_path: Path) -> None:
    """Round-trip save+load — engine state must come back unchanged."""
    eng_a = _make_engine_with_one_trade()
    out = tmp_path / "session.agbk"
    save_session(eng_a, str(out))

    eng_b = BacktestCore()
    meta = load_session(eng_b, str(out))

    # The integrity check must have run silently (no exception).
    assert meta["format_version"] == AGBK_FORMAT_VERSION
    assert len(eng_b.trades) == 1
    t_a, t_b = eng_a.trades[0], eng_b.trades[0]
    assert t_a.id == t_b.id
    assert t_a.direction == t_b.direction
    assert t_a.pnl == t_b.pnl
    assert t_a.exit_reason == t_b.exit_reason


def test_tampered_archive_raises_integrity_error(tmp_path: Path) -> None:
    """Editing any data member after save must trip the hash check on load."""
    eng = _make_engine_with_one_trade()
    out = tmp_path / "session.agbk"
    save_session(eng, str(out))

    # Open the zip, swap one byte in trades.csv, write back. We rebuild
    # the zip wholesale because zipfile doesn't support in-place edits.
    with zipfile.ZipFile(out, "r") as zf:
        members = {name: zf.read(name) for name in zf.namelist()}
    members["trades.csv"] = members["trades.csv"].replace(b"long", b"shrt", 1)
    out.unlink()
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, data in members.items():
            zf.writestr(name, data)

    eng_b = BacktestCore()
    with pytest.raises(IntegrityError) as excinfo:
        load_session(eng_b, str(out))
    assert "integrity check failed" in str(excinfo.value).lower()


def test_legacy_archive_without_hash_still_loads(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Pre-Sprint-2 archives (no agbk_sha256, no format_version) load with a warning."""
    # Build a minimal archive that mimics the legacy v1 layout
    out = tmp_path / "legacy.agbk"
    legacy_meta = {
        "version": 1,
        "schema": "agregat-backtest",
        "asset": {"name": "DE40"},
        "initial_equity": 100_000.0,
        "equity": 100_250.0,
        "max_equity": 100_500.0,
        "max_drawdown_abs": 200.0,
        "max_drawdown_pct": 0.2,
        "current_idx": 99,
        "risk_pct": 1.0,
        "execution_model": None,
        "trade_count": 0,
    }
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("meta.json", json.dumps(legacy_meta))
        zf.writestr("trades.csv", "")
        zf.writestr("equity.csv", "")
        zf.writestr("drawings.json", "[]")

    eng = BacktestCore()
    with caplog.at_level("INFO"):
        load_session(eng, str(out))
    # Sprint 3.3 — the migration framework upgrades v0 → v1 and logs it.
    assert any(
        "Migrated .agbk" in r.getMessage() or "legacy .agbk" in r.getMessage()
        for r in caplog.records
    )


# ---------------------------------------------------------------------------
# Sprint 2.3 — input_csv_sha256
# ---------------------------------------------------------------------------
def test_input_csv_sha256_round_trips(tmp_path: Path) -> None:
    """``set_input_hash`` → save → load must round-trip the digest into meta."""
    csv = tmp_path / "input.csv"
    csv.write_bytes(b"time,open,high,low,close,volume\n2020-01-01,1,2,0.5,1.5,100\n")
    eng = _make_engine_with_one_trade()
    eng.set_input_hash(csv_path=str(csv))
    assert eng.input_csv_sha256 is not None

    out = tmp_path / "session.agbk"
    save_session(eng, str(out))

    raw = load_session_raw(str(out))
    assert raw["meta"]["input_csv_sha256"] == eng.input_csv_sha256


def test_set_input_hash_is_deterministic(tmp_path: Path) -> None:
    """Same bytes → same SHA, regardless of OS / pandas version."""
    from engine.persistence_pkg.integrity import hash_file

    csv = tmp_path / "input.csv"
    csv.write_bytes(b"hello world")
    sha = hash_file(str(csv))
    # Known SHA-256 of "hello world"
    assert sha == "b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9"


# ---------------------------------------------------------------------------
# Sprint 2.4 — seeds round-trip
# ---------------------------------------------------------------------------
def test_seeds_persist_in_meta(tmp_path: Path) -> None:
    eng = _make_engine_with_one_trade()
    eng.set_seeds(monte_carlo=42, optimizer=123)
    out = tmp_path / "session.agbk"
    save_session(eng, str(out))

    raw = load_session_raw(str(out))
    assert raw["meta"]["seeds"] == {"monte_carlo": 42, "optimizer": 123}


def test_set_seeds_clears_with_none() -> None:
    eng = BacktestCore()
    eng.set_seeds(monte_carlo=42)
    eng.set_seeds(monte_carlo=None)
    assert "monte_carlo" not in eng.seeds


def test_no_seeds_means_no_seeds_field(tmp_path: Path) -> None:
    """Don't pollute meta.json with empty seed dicts."""
    eng = _make_engine_with_one_trade()
    assert eng.seeds == {}
    out = tmp_path / "session.agbk"
    save_session(eng, str(out))

    raw = load_session_raw(str(out))
    assert "seeds" not in raw["meta"]
