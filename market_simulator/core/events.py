"""Market event types for market simulation.

MarketEvent tags used by SimulatedExchange's event dispatch. The event bus
implementation itself now lives in market_simulator.hb_compat.event_bus_adapter,
which delegates to the canonical event_bus package.
"""

from __future__ import annotations

from enum import IntEnum


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
