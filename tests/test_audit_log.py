"""Sprint 4.7 — Tests for engine.observability.audit_log."""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

import pytest

from engine.observability.audit_log import AuditEvent, AuditLog, log_action


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "audit.db"


def test_log_creates_file_and_table(db_path: Path) -> None:
    al = AuditLog(path=db_path)
    al.log("load-csv", payload={"file": "/data/x.csv"})
    assert db_path.exists()
    # Schema check
    with sqlite3.connect(str(db_path)) as conn:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    names = [r[0] for r in rows]
    assert "events" in names
    al.close()


def test_log_returns_event_with_hash(db_path: Path) -> None:
    al = AuditLog(path=db_path)
    ev = al.log("save-session", payload={"path": "session.agbk"})
    assert ev.action == "save-session"
    assert ev.id == 1
    assert len(ev.payload_hash) == 64  # sha256 hex
    assert ev.trace_id  # auto-generated
    al.close()


def test_payload_persisted_only_when_requested(db_path: Path) -> None:
    al = AuditLog(path=db_path)
    al.log("a", payload={"k": "v"}, store_payload=False)
    al.log("b", payload={"k": "v"}, store_payload=True)
    rows = al.all()
    assert rows[0].payload_json is None
    assert rows[1].payload_json is not None
    assert json.loads(rows[1].payload_json) == {"k": "v"}
    al.close()


def test_query_by_action_and_trace(db_path: Path) -> None:
    al = AuditLog(path=db_path)
    al.log("a", trace_id="trace-1")
    al.log("b", trace_id="trace-1")
    al.log("a", trace_id="trace-2")
    by_a = al.by_action("a")
    by_t = al.by_trace("trace-1")
    assert len(by_a) == 2
    assert len(by_t) == 2
    assert all(e.action == "a" for e in by_a)
    assert all(e.trace_id == "trace-1" for e in by_t)
    al.close()


def test_invalid_action_raises(db_path: Path) -> None:
    al = AuditLog(path=db_path)
    with pytest.raises(ValueError):
        al.log("")
    al.close()


def test_non_serialisable_payload_raises(db_path: Path) -> None:
    class _NotSerialisable:
        pass

    al = AuditLog(path=db_path)
    # The default=str fallback will coerce most things; use a circular
    # reference that can't be JSON-encoded even with default=str.
    cycle: dict[str, object] = {}
    cycle["self"] = cycle
    with pytest.raises(ValueError):
        al.log("x", payload=cycle)  # type: ignore[arg-type]
    al.close()


def test_global_helper_uses_env_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "test_audit.db"
    monkeypatch.setenv("AGREGAT_AUDIT_DB", str(target))
    # Reset module-level singleton
    import engine.observability.audit_log as mod

    monkeypatch.setattr(mod, "_DEFAULT", None)
    ev = log_action("smoke", payload={"x": 1})
    assert target.exists()
    assert ev.action == "smoke"


def test_context_manager_closes(db_path: Path) -> None:
    with AuditLog(path=db_path) as al:
        al.log("evt")
        assert al.is_open
    assert not al.is_open
