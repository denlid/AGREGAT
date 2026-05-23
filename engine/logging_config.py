"""Sprint 2.7 — Structured-logging foundation.

This module provides three building blocks the rest of the codebase
will adopt incrementally over Sprints 3–4:

* :class:`JSONFormatter` — a stdlib :class:`logging.Formatter` that
  serialises each :class:`logging.LogRecord` to a single line of
  canonical JSON. Output is one JSON object per line so downstream
  log shippers (Loki, Datadog, Filebeat) can ingest without a custom
  parser.
* :class:`TraceContextAdapter` — a :class:`logging.LoggerAdapter` that
  injects a ``trace_id`` extra into every record. ``trace_id``
  defaults to a fresh ``uuid4()`` per adapter instance; pass
  ``trace_id=...`` explicitly to propagate one across thread / process
  boundaries (e.g. from a GUI worker into the engine call it issues).
* :func:`new_trace_id` — convenience wrapper for ``uuid4().hex``.
* :func:`enable_json_logging` — opt-in helper that swaps the root
  logger's handlers for a single JSON-formatted ``StreamHandler``.
  We expose this as a function rather than wiring it into module
  import time so existing console-based dev workflows aren't
  disrupted; it'll be flipped on by the CLI launcher in Sprint 3
  once log-shipping is plumbed.

The current GUI logger keeps its human-readable formatter — Sprint
2.7 is the *foundation*, not the cut-over.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any, Dict, MutableMapping, Optional, Tuple

# Fields populated by the stdlib logger that we never want to ship — they're
# either redundant with the structured fields we emit or contain unstable
# memory addresses that would break log diffs in tests.
_RECORD_BUILTIN_FIELDS = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "message",
        "module",
        "msecs",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "thread",
        "threadName",
        "taskName",
    }
)


class JSONFormatter(logging.Formatter):
    """Emit each LogRecord as a single JSON line.

    The output schema is intentionally small and stable:

    .. code-block:: json

        {
          "ts": "2026-05-06T18:00:00.000Z",
          "level": "INFO",
          "logger": "StrategyTester.engine.optimizer",
          "msg": "Grid search complete",
          "trace_id": "1b2c3d4e",
          "module": "optimizer",
          "line": 42
        }

    Any ``extra={...}`` keys passed to the logger are merged in at the
    top level (after the schema fields above). Stack traces from
    ``logger.exception()`` land in an ``exc`` field so they survive a
    JSON-line consumer that splits on newlines.
    """

    # Time format for the ``ts`` field — strict ISO-8601 with millisecond
    # precision, suffixed with ``Z`` to disambiguate UTC.
    _TS_FORMAT = "%Y-%m-%dT%H:%M:%S"

    def format(self, record: logging.LogRecord) -> str:  # noqa: D401  (parent override)
        ts = time.strftime(self._TS_FORMAT, time.gmtime(record.created))
        ts = f"{ts}.{int(record.msecs):03d}Z"
        payload: Dict[str, Any] = {
            "ts": ts,
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "module": record.module,
            "line": record.lineno,
        }
        # ``trace_id`` is the only structured field we mandate; if it's
        # missing the record came from an un-adapted logger and we
        # leave the field absent rather than fabricate one.
        trace_id = getattr(record, "trace_id", None)
        if trace_id:
            payload["trace_id"] = trace_id
        # Merge any caller-supplied ``extra`` fields. We exclude the
        # builtin record attributes so we don't leak the internal
        # logging plumbing.
        for key, value in record.__dict__.items():
            if key in _RECORD_BUILTIN_FIELDS or key in payload or key == "trace_id":
                continue
            try:
                json.dumps(value, default=str)  # ensure JSON-serialisable
            except TypeError:
                value = repr(value)
            payload[key] = value

        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack"] = self.formatStack(record.stack_info)

        return json.dumps(payload, separators=(",", ":"), default=str)


class TraceContextAdapter(logging.LoggerAdapter):  # type: ignore[type-arg]
    """Tag every record with a ``trace_id`` extra.

    Use one adapter per logical operation (per backtest run, per GUI
    worker, per CLI invocation). ``trace_id`` defaults to a freshly
    generated ``uuid4().hex`` so two adapters never collide; pass an
    explicit ``trace_id=`` to *propagate* an existing trace through a
    deeper layer.

    Example::

        worker_log = TraceContextAdapter(logger, trace_id=new_trace_id())
        worker_log.info("starting compute")
        engine.run(trace_id=worker_log.extra["trace_id"])
    """

    def __init__(
        self,
        logger: logging.Logger,
        extra: Optional[MutableMapping[str, Any]] = None,
        *,
        trace_id: Optional[str] = None,
    ) -> None:
        merged: Dict[str, Any] = dict(extra) if extra else {}
        merged.setdefault("trace_id", trace_id or new_trace_id())
        super().__init__(logger, merged)

    def process(
        self, msg: str, kwargs: MutableMapping[str, Any]
    ) -> Tuple[str, MutableMapping[str, Any]]:
        # Merge our adapter-level ``extra`` into the per-call kwargs
        # without clobbering anything the caller passed.
        adapter_extra = dict(self.extra) if self.extra else {}
        if "extra" in kwargs and kwargs["extra"]:
            adapter_extra.update(kwargs["extra"])
        kwargs["extra"] = adapter_extra
        return msg, kwargs


def new_trace_id() -> str:
    """Return a short hex trace-id (32 chars) — wraps ``uuid.uuid4().hex``.

    Centralised so future versions can swap to a more compact format
    (e.g. base32 for log readability) without rippling through callers.
    """
    return uuid.uuid4().hex


def enable_json_logging(level: int = logging.INFO) -> logging.Handler:
    """Replace the root logger's handlers with a single JSON ``StreamHandler``.

    Returns the installed handler so callers can adjust its level or
    pipe it elsewhere.
    Idempotent: calling twice doesn't double-up handlers.
    """
    root = logging.getLogger()
    # Drop existing handlers — they'd otherwise duplicate every record.
    for h in list(root.handlers):
        root.removeHandler(h)
    handler = logging.StreamHandler()
    handler.setFormatter(JSONFormatter())
    handler.setLevel(level)
    root.addHandler(handler)
    root.setLevel(level)
    return handler


__all__ = [
    "JSONFormatter",
    "TraceContextAdapter",
    "enable_json_logging",
    "new_trace_id",
]
