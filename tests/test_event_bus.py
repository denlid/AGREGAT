"""EventBus pub/sub semantics."""

from __future__ import annotations

from engine.models.event_bus import EventBus


def test_subscribe_emit_invokes_callback():
    bus = EventBus()
    received = []
    bus.subscribe("ping", lambda d: received.append(d))
    bus.emit("ping", {"x": 1})
    assert received == [{"x": 1}]


def test_emit_with_no_subscribers_is_noop():
    bus = EventBus()
    # Should not raise.
    bus.emit("nobody")


def test_multiple_subscribers_called_in_order():
    bus = EventBus()
    order = []
    bus.subscribe("evt", lambda d: order.append("a"))
    bus.subscribe("evt", lambda d: order.append("b"))
    bus.emit("evt")
    assert order == ["a", "b"]


def test_unsubscribe_removes_callback():
    bus = EventBus()
    received = []

    def cb(_):
        received.append(1)

    bus.subscribe("evt", cb)
    bus.unsubscribe("evt", cb)
    bus.emit("evt")
    assert received == []


def test_unsubscribe_unknown_event_is_safe():
    bus = EventBus()
    bus.unsubscribe("never-subscribed", lambda d: None)


def test_clear_removes_all_subscribers():
    bus = EventBus()
    received = []
    bus.subscribe("a", lambda d: received.append("a"))
    bus.subscribe("b", lambda d: received.append("b"))
    bus.clear()
    bus.emit("a")
    bus.emit("b")
    assert received == []


def test_callback_exception_does_not_abort_others():
    bus = EventBus()
    seen = []
    bus.subscribe("evt", lambda d: (_ for _ in ()).throw(RuntimeError("boom")))
    bus.subscribe("evt", lambda d: seen.append("ok"))
    bus.emit("evt")
    assert seen == ["ok"]


def test_emit_default_payload_is_empty_dict():
    bus = EventBus()
    received = []
    bus.subscribe("evt", lambda d: received.append(d))
    bus.emit("evt")
    assert received == [{}]
