"""Event tags for market simulation.

``MarketEvent`` is the canonical enum from hb-event-bus (v0.3.0+), re-exported
here with the parent hummingbot values (e.g. BuyOrderCreated=200,
OrderFilled=107); see hb-market-simulator issue #61. The event bus
implementation lives in market_simulator.hb_compat.event_bus_adapter, which
delegates to the canonical event_bus package.

``SimulatorEvent`` holds simulator-only tags that have no canonical
counterpart.
"""

from __future__ import annotations

from enum import Enum

from event_bus import MarketEvent

__all__ = ["MarketEvent", "SimulatorEvent"]


class SimulatorEvent(Enum):
    """Simulator-only event tags, absent from the canonical MarketEvent.

    900 is outside every canonical MarketEvent value (101-110, 198-203,
    300-303) and adjacent to, but distinct from, OrderBookEvent (901, 904).
    """

    OrderTriggered = 900
