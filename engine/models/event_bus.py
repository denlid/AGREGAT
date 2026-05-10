"""
Event Bus — Simple pub/sub event system for engine events.
"""

from typing import Any, Callable, Dict, List, Optional

EventCallback = Callable[[Dict[str, Any]], None]


class EventBus:
    """
    Simple pub/sub event bus.

    Events:
        'new_1m_candle'       → {candle: dict}
        'tf_completed'        → {tf: str, candle: dict}
        'pending_filled'      → {position: Position}
        'position_updated'    → {position: Position, field: str}
        'position_closed'     → {trade: Trade}
        'eod_trigger'         → {time: str}
        'trading_hours_start' → {}
        'trading_hours_end'   → {}
    """

    def __init__(self) -> None:
        self._subscribers: Dict[str, List[EventCallback]] = {}

    def subscribe(self, event: str, callback: EventCallback) -> None:
        """Subscribe to an event."""
        if event not in self._subscribers:
            self._subscribers[event] = []
        self._subscribers[event].append(callback)

    def unsubscribe(self, event: str, callback: EventCallback) -> None:
        """Unsubscribe from an event."""
        if event in self._subscribers:
            self._subscribers[event] = [cb for cb in self._subscribers[event] if cb != callback]

    def emit(self, event: str, data: Optional[Dict[str, Any]] = None) -> None:
        """Emit an event to all subscribers."""
        if data is None:
            data = {}
        for callback in self._subscribers.get(event, []):
            try:
                callback(data)
            except Exception as e:
                import logging

                logging.getLogger("StrategyTester.engine.event_bus").error(
                    f"EventBus error on '{event}': {e}", exc_info=True
                )

    def clear(self) -> None:
        """Remove all subscribers."""
        self._subscribers.clear()
