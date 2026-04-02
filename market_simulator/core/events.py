"""Pure-Python event bus for market simulation.

Lightweight pub/sub replacing hummingbot's Cython PubSub. Supports the same
add_listener / remove_listener / trigger_event interface so the hb_compat
adapter layer is thin.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Callable
from enum import IntEnum
from typing import Any

logger = logging.getLogger(__name__)


class MarketEvent(IntEnum):
    """Events emitted by the simulated exchange.

    Values match hummingbot's MarketEvent enum for compatibility.
    """

    BuyOrderCreated = 2
    SellOrderCreated = 3
    OrderFilled = 4
    BuyOrderCompleted = 5
    SellOrderCompleted = 6
    OrderCancelled = 7
    OrderFailure = 8
    OrderExpired = 9
    FundingPaymentCompleted = 10
    OrderTriggered = 11


class EventBus:
    """Synchronous event dispatch with integer event tags.

    Designed to match the interface of hummingbot's Cython PubSub:
      - add_listener(event_tag, listener)
      - remove_listener(event_tag, listener)
      - trigger_event(event_tag, message)
    """

    def __init__(self) -> None:
        self._listeners: dict[int, list[Callable]] = defaultdict(list)

    def add_listener(self, event_tag: int, listener: Callable) -> None:
        """Register a listener for the given event tag."""
        listeners = self._listeners[event_tag]
        if listener not in listeners:
            listeners.append(listener)

    def remove_listener(self, event_tag: int, listener: Callable) -> None:
        """Unregister a listener for the given event tag."""
        listeners = self._listeners.get(event_tag)
        if listeners and listener in listeners:
            listeners.remove(listener)

    def trigger_event(self, event_tag: int, message: Any) -> None:
        """Dispatch an event to all registered listeners.

        Listeners receive (message,) as argument — matching hummingbot convention
        where listeners are callables that accept the event payload.
        """
        listeners = self._listeners.get(event_tag)
        if not listeners:
            return
        for listener in listeners.copy():  # copy to allow modification during iteration
            try:
                listener(message)
            except Exception:
                logger.exception("Error in event listener for tag %s: %s", event_tag, listener)

    def clear_all_listeners(self) -> None:
        """Remove all listeners for all event tags."""
        self._listeners.clear()

    def listener_count(self, event_tag: int) -> int:
        """Return the number of listeners registered for the given event tag."""
        return len(self._listeners.get(event_tag, []))

    def has_listeners(self, event_tag: int) -> bool:
        """Check if any listeners are registered for the given event tag."""
        return self.listener_count(event_tag) > 0
