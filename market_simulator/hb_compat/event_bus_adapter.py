"""EventBusAdapter — bridges MarketEvent-tagged pub/sub onto canonical event_bus.EventBus.

Preserves the add_listener/remove_listener/trigger_event surface required by
EventSourceProtocol (market_simulator/protocols/connector.py), delegating dispatch
to the canonical event_bus package. Event tags (Enum members or raw ints) are
mapped to str(tag value) since event_bus keys subscriptions by string event_type.
Keying on the value (not str(member)) keeps a raw int and its enum member on the
same key, and keeps keys stable across enum classes.
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import TYPE_CHECKING, Any

from event_bus import EventBus as _CanonicalEventBus
from event_bus import Subscription

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)


def _tag_key(event_tag: Enum | int) -> str:
    """Return the bus key for an event tag: str of its underlying value."""
    return str(event_tag.value if isinstance(event_tag, Enum) else event_tag)


class EventBusAdapter:
    """Adapts event_bus.EventBus to the tag-based add_listener/remove_listener/
    trigger_event interface.

    Maintains its own (event_type, listener) -> Subscription registry since the
    canonical bus removes subscribers by opaque Subscription handle rather than
    by listener identity, and to preserve the legacy dedup-on-add behavior.
    """

    def __init__(self) -> None:
        self._bus = _CanonicalEventBus()
        self._subs: dict[tuple[str, Callable], Subscription] = {}

    def add_listener(self, event_tag: Enum | int, listener: Callable) -> None:
        """Register a listener for the given event tag. Duplicate adds are no-ops."""
        key = (_tag_key(event_tag), listener)
        if key not in self._subs:
            self._subs[key] = self._bus.subscribe(key[0], listener)

    def remove_listener(self, event_tag: Enum | int, listener: Callable) -> None:
        """Unregister a listener for the given event tag. Safe to call if not registered."""
        key = (_tag_key(event_tag), listener)
        sub = self._subs.pop(key, None)
        if sub is not None:
            self._bus.unsubscribe(sub)

    def trigger_event(self, event_tag: Enum | int, message: Any) -> None:
        """Dispatch an event to all registered listeners for the tag."""
        self._bus.publish(_tag_key(event_tag), message)

    def clear_all_listeners(self) -> None:
        """Remove all listeners for all event tags."""
        for sub in self._subs.values():
            self._bus.unsubscribe(sub)
        self._subs.clear()

    def listener_count(self, event_tag: Enum | int) -> int:
        """Return the number of listeners registered for the given event tag."""
        return len(self._bus.get_subscribers(_tag_key(event_tag)))

    def has_listeners(self, event_tag: Enum | int) -> bool:
        """Check if any listeners are registered for the given event tag."""
        return self.listener_count(event_tag) > 0
