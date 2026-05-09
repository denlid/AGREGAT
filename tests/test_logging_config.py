"""Sprint 2.7 — Structured logging foundation tests.

Asserts that:

* :class:`engine.logging_config.JSONFormatter` produces parseable
  JSON-line output with the schema we promise downstream consumers.
* ``trace_id`` survives the LoggerAdapter → Formatter pipeline.
* :func:`enable_json_logging` is idempotent — handlers don't double up.
"""

from __future__ import annotations

import io
import json
import logging
import re

import pytest

from engine.logging_config import (
    JSONFormatter,
    TraceContextAdapter,
    enable_json_logging,
    new_trace_id,
)


@pytest.fixture
def capture_handler() -> tuple[logging.StreamHandler, io.StringIO]:
    """Yield a (StreamHandler, sink) pair — flush after every emit."""
    sink = io.StringIO()
    handler = logging.StreamHandler(sink)
    handler.setFormatter(JSONFormatter())
    return handler, sink


def _read_lines(sink: io.StringIO) -> list[dict]:
    sink.seek(0)
    return [json.loads(line) for line in sink.read().splitlines() if line.strip()]


def test_json_formatter_emits_required_fields(capture_handler) -> None:
    handler, sink = capture_handler
    log = logging.getLogger("test.json.basic")
    log.handlers = [handler]
    log.setLevel(logging.INFO)
    log.propagate = False

    log.info("hello world")

    [record] = _read_lines(sink)
    assert record["msg"] == "hello world"
    assert record["level"] == "INFO"
    assert record["logger"] == "test.json.basic"
    assert record["module"] == "test_logging_config"
    assert isinstance(record["line"], int)
    # ts is ISO-8601 with millisecond precision and Z suffix
    assert re.match(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z", record["ts"])


def test_trace_id_propagates_via_adapter(capture_handler) -> None:
    handler, sink = capture_handler
    log = logging.getLogger("test.json.trace")
    log.handlers = [handler]
    log.setLevel(logging.INFO)
    log.propagate = False

    tid = new_trace_id()
    adapter = TraceContextAdapter(log, trace_id=tid)
    adapter.info("kicked off")
    adapter.warning("hit a snag")

    a, b = _read_lines(sink)
    assert a["trace_id"] == tid
    assert b["trace_id"] == tid
    # Adapter doesn't clobber per-call extras
    sink.truncate(0)
    sink.seek(0)
    adapter.info("with extras", extra={"step": "load"})
    [c] = _read_lines(sink)
    assert c["trace_id"] == tid
    assert c["step"] == "load"


def test_no_trace_id_when_un_adapted(capture_handler) -> None:
    handler, sink = capture_handler
    log = logging.getLogger("test.json.untraced")
    log.handlers = [handler]
    log.setLevel(logging.INFO)
    log.propagate = False
    log.info("no trace please")
    [record] = _read_lines(sink)
    assert "trace_id" not in record


def test_extra_fields_are_serialised(capture_handler) -> None:
    handler, sink = capture_handler
    log = logging.getLogger("test.json.extras")
    log.handlers = [handler]
    log.setLevel(logging.INFO)
    log.propagate = False
    log.info("processed", extra={"n_bars": 200_000, "asset": "ES"})
    [record] = _read_lines(sink)
    assert record["n_bars"] == 200000
    assert record["asset"] == "ES"


def test_non_serialisable_extra_is_repr_safe(capture_handler) -> None:
    handler, sink = capture_handler
    log = logging.getLogger("test.json.weird")
    log.handlers = [handler]
    log.setLevel(logging.INFO)
    log.propagate = False

    class _Weird:
        def __repr__(self) -> str:
            return "<weird>"

    log.info("weird payload", extra={"thing": _Weird()})
    [record] = _read_lines(sink)
    assert record["thing"] == "<weird>"


def test_exception_serialised_into_exc_field(capture_handler) -> None:
    handler, sink = capture_handler
    log = logging.getLogger("test.json.exc")
    log.handlers = [handler]
    log.setLevel(logging.INFO)
    log.propagate = False
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        log.exception("captured")
    [record] = _read_lines(sink)
    assert record["msg"] == "captured"
    assert "RuntimeError: boom" in record["exc"]


def test_new_trace_id_is_unique() -> None:
    a = {new_trace_id() for _ in range(100)}
    assert len(a) == 100


def test_enable_json_logging_is_idempotent() -> None:
    root = logging.getLogger()
    saved = list(root.handlers)
    saved_level = root.level
    try:
        h1 = enable_json_logging()
        h2 = enable_json_logging()
        # Two calls — only the second handler remains; never a stack-up.
        assert root.handlers == [h2]
        assert h1 is not h2
    finally:
        for h in list(root.handlers):
            root.removeHandler(h)
        for h in saved:
            root.addHandler(h)
        root.setLevel(saved_level)


def test_compute_worker_attaches_trace_id() -> None:
    """ComputeWorker must mint a trace_id and expose it as a public attr."""
    pytest.importorskip("PySide6")
    from gui.workers.workers import ComputeWorker

    w = ComputeWorker(lambda: 42)
    assert isinstance(w.trace_id, str)
    assert len(w.trace_id) == 32  # uuid4().hex
    # Explicit propagation works
    w2 = ComputeWorker(lambda: 1, trace_id=w.trace_id)
    assert w2.trace_id == w.trace_id
