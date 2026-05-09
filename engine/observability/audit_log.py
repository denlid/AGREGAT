"""Sprint 4.7 — Append-only audit log.

A small SQLite journal under ``~/.agregat/audit.db`` that records
high-level user actions (file → load CSV, file → save session,
command-palette invocations, headless backtest runs) so we can
reconstruct what happened in a session even when the GUI is closed.

Schema (single ``events`` table)::

    id           INTEGER PRIMARY KEY AUTOINCREMENT
    ts           TEXT NOT NULL          -- ISO-8601 UTC
    action       TEXT NOT NULL
    payload_hash TEXT NOT NULL          -- sha256(json(payload))
    trace_id     TEXT NOT NULL
    payload_json TEXT                   -- optional verbose payload

The log is **append-only** by convention; we never expose ``DELETE``
or ``UPDATE`` from this module. The DB file is created on first call
to :meth:`AuditLog.log`. Callers that don't care about persistence
can use the global :func:`log_action` helper.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

logger = logging.getLogger(__name__)

AUDIT_DB_DEFAULT = Path.home() / ".agregat" / "audit.db"


@dataclass(frozen=True)
class AuditEvent:
    """One row in the ``events`` table."""

    id: int
    ts: str
    action: str
    payload_hash: str
    trace_id: str
    payload_json: Optional[str] = None


@dataclass
class AuditLog:
    """Open / lazily-create an append-only SQLite audit journal.

    The class is safe to instantiate even if the directory doesn't yet
    exist — it's created on first write. ``__enter__`` / ``__exit__``
    are provided for ``with`` blocks but a long-lived instance is the
    expected use.
    """

    path: Path = field(default_factory=lambda: AUDIT_DB_DEFAULT)
    _conn: Optional[sqlite3.Connection] = field(default=None, init=False, repr=False)

    # ------------------------------------------------------------------
    def __post_init__(self) -> None:
        # Allow callers to pass a string path.
        self.path = Path(self.path).expanduser()

    def __enter__(self) -> "AuditLog":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # ------------------------------------------------------------------
    @property
    def is_open(self) -> bool:
        return self._conn is not None

    def _ensure(self) -> sqlite3.Connection:
        if self._conn is not None:
            return self._conn
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.path))
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                action TEXT NOT NULL,
                payload_hash TEXT NOT NULL,
                trace_id TEXT NOT NULL,
                payload_json TEXT
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_events_action ON events(action)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_events_trace ON events(trace_id)")
        conn.commit()
        self._conn = conn
        return conn

    # ------------------------------------------------------------------
    def log(
        self,
        action: str,
        *,
        payload: Optional[dict[str, Any]] = None,
        trace_id: Optional[str] = None,
        store_payload: bool = False,
    ) -> AuditEvent:
        """Insert a single event and return the inserted row.

        Parameters
        ----------
        action
            Short kebab-case label (``"load-csv"`` etc.).
        payload
            Dict (must be JSON-serialisable) — its sha256 is recorded
            in ``payload_hash``. The full JSON is only stored when
            ``store_payload`` is true (off by default to keep the DB
            small and avoid ever persisting secrets accidentally).
        trace_id
            Trace correlator. Generated if not supplied.
        store_payload
            Whether to persist the full JSON payload.
        """
        if not action or not isinstance(action, str):
            raise ValueError("action must be a non-empty string")
        payload = payload or {}
        try:
            payload_text = json.dumps(payload, sort_keys=True, default=str)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"payload not JSON-serialisable: {exc}") from exc
        h = hashlib.sha256(payload_text.encode("utf-8")).hexdigest()
        tid = trace_id or uuid.uuid4().hex
        ts = datetime.now(timezone.utc).isoformat()
        conn = self._ensure()
        cursor = conn.execute(
            "INSERT INTO events (ts, action, payload_hash, trace_id, payload_json) "
            "VALUES (?, ?, ?, ?, ?)",
            (ts, action, h, tid, payload_text if store_payload else None),
        )
        conn.commit()
        rowid = int(cursor.lastrowid or 0)
        return AuditEvent(
            id=rowid,
            ts=ts,
            action=action,
            payload_hash=h,
            trace_id=tid,
            payload_json=payload_text if store_payload else None,
        )

    # ------------------------------------------------------------------
    def all(self, *, limit: Optional[int] = None) -> list[AuditEvent]:
        """Return events ordered by ``id`` ascending."""
        conn = self._ensure()
        sql = (
            "SELECT id, ts, action, payload_hash, trace_id, payload_json "
            "FROM events ORDER BY id ASC"
        )
        if limit is not None:
            sql += f" LIMIT {int(limit)}"
        rows = conn.execute(sql).fetchall()
        return [self._row_to_event(r) for r in rows]

    def by_action(self, action: str) -> list[AuditEvent]:
        conn = self._ensure()
        rows = conn.execute(
            "SELECT id, ts, action, payload_hash, trace_id, payload_json "
            "FROM events WHERE action = ? ORDER BY id ASC",
            (action,),
        ).fetchall()
        return [self._row_to_event(r) for r in rows]

    def by_trace(self, trace_id: str) -> list[AuditEvent]:
        conn = self._ensure()
        rows = conn.execute(
            "SELECT id, ts, action, payload_hash, trace_id, payload_json "
            "FROM events WHERE trace_id = ? ORDER BY id ASC",
            (trace_id,),
        ).fetchall()
        return [self._row_to_event(r) for r in rows]

    # ------------------------------------------------------------------
    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # ------------------------------------------------------------------
    @staticmethod
    def _row_to_event(row: Iterable[Any]) -> AuditEvent:
        rid, ts, action, h, tid, payload_json = tuple(row)
        return AuditEvent(
            id=int(rid),
            ts=str(ts),
            action=str(action),
            payload_hash=str(h),
            trace_id=str(tid),
            payload_json=str(payload_json) if payload_json is not None else None,
        )


# Module-level convenience: a default audit log instance used by the
# GUI controllers + SDK helpers. Instantiated lazily so import-time
# never touches the filesystem.
_DEFAULT: Optional[AuditLog] = None


def log_action(
    action: str,
    *,
    payload: Optional[dict[str, Any]] = None,
    trace_id: Optional[str] = None,
    store_payload: bool = False,
) -> AuditEvent:
    """Append an event using the process-wide default audit log."""
    global _DEFAULT
    if _DEFAULT is None:
        # Allow tests to redirect the location via env var.
        path_str = os.environ.get("AGREGAT_AUDIT_DB", "")
        path = Path(path_str) if path_str else AUDIT_DB_DEFAULT
        _DEFAULT = AuditLog(path=path)
    return _DEFAULT.log(
        action,
        payload=payload,
        trace_id=trace_id,
        store_payload=store_payload,
    )


__all__ = [
    "AUDIT_DB_DEFAULT",
    "AuditEvent",
    "AuditLog",
    "log_action",
]
