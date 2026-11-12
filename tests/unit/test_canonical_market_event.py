"""MarketEvent is the canonical event_bus enum (hb-market-simulator#61)."""

from decimal import Decimal

import event_bus

from market_simulator.core import events
from market_simulator.core.events import MarketEvent, SimulatorEvent
from market_simulator.core.types import OrderType
from market_simulator.hb_compat.event_bus_adapter import EventBusAdapter
from market_simulator.simulator.exchange import OrderFilledEvent
from market_simulator.simulator.matching_engine import ImmediateFillEngine
from tests.unit.test_exchange import _make_exchange


def test_market_event_is_canonical():
    assert events.MarketEvent is event_bus.MarketEvent
    assert "MarketEvent" in events.__all__


def test_parent_values():
    assert MarketEvent.OrderFilled.value == 107
    assert MarketEvent.BuyOrderCreated.value == 200


def test_order_triggered_value_does_not_collide():
    canonical_values = {m.value for m in MarketEvent}
    assert SimulatorEvent.OrderTriggered.value not in canonical_values
    assert SimulatorEvent.OrderTriggered.value not in {m.value for m in event_bus.OrderBookEvent}


def test_adapter_keys_raw_int_and_member_identically():
    bus = EventBusAdapter()
    received = []
    bus.add_listener(MarketEvent.OrderFilled, received.append)
    bus.trigger_event(107, "by-int")
    assert received == ["by-int"]


def test_simulated_fill_reaches_event_bus_subscriber():
    ex = _make_exchange(matching_engine=ImmediateFillEngine())
    received = []
    ex.add_listener(MarketEvent.OrderFilled, received.append)

    ex.buy("BTC-USDT", Decimal("1.0"), OrderType.MARKET, Decimal("0"))

    assert len(received) == 1
    assert isinstance(received[0], OrderFilledEvent)
