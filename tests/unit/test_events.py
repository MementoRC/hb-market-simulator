"""Tests for EventBus."""

from market_simulator.core.events import MarketEvent
from market_simulator.hb_compat.event_bus_adapter import EventBusAdapter as EventBus


class TestEventBus:
    def test_add_and_trigger(self):
        bus = EventBus()
        received = []
        bus.add_listener(MarketEvent.OrderFilled, lambda msg: received.append(msg))
        bus.trigger_event(MarketEvent.OrderFilled, "fill-data")
        assert received == ["fill-data"]

    def test_multiple_listeners(self):
        bus = EventBus()
        results_a = []
        results_b = []
        bus.add_listener(MarketEvent.OrderFilled, lambda msg: results_a.append(msg))
        bus.add_listener(MarketEvent.OrderFilled, lambda msg: results_b.append(msg))
        bus.trigger_event(MarketEvent.OrderFilled, "data")
        assert results_a == ["data"]
        assert results_b == ["data"]

    def test_remove_listener(self):
        bus = EventBus()
        received = []
        listener = lambda msg: received.append(msg)  # noqa: E731
        bus.add_listener(MarketEvent.OrderFilled, listener)
        bus.remove_listener(MarketEvent.OrderFilled, listener)
        bus.trigger_event(MarketEvent.OrderFilled, "data")
        assert received == []

    def test_no_duplicate_listeners(self):
        bus = EventBus()
        received = []
        listener = lambda msg: received.append(msg)  # noqa: E731
        bus.add_listener(MarketEvent.OrderFilled, listener)
        bus.add_listener(MarketEvent.OrderFilled, listener)
        bus.trigger_event(MarketEvent.OrderFilled, "data")
        assert received == ["data"]  # Only called once

    def test_different_event_tags(self):
        bus = EventBus()
        fills = []
        cancels = []
        bus.add_listener(MarketEvent.OrderFilled, lambda msg: fills.append(msg))
        bus.add_listener(MarketEvent.OrderCancelled, lambda msg: cancels.append(msg))
        bus.trigger_event(MarketEvent.OrderFilled, "filled")
        bus.trigger_event(MarketEvent.OrderCancelled, "cancelled")
        assert fills == ["filled"]
        assert cancels == ["cancelled"]

    def test_trigger_no_listeners(self):
        bus = EventBus()
        # Should not raise
        bus.trigger_event(MarketEvent.OrderFilled, "data")

    def test_listener_count(self):
        bus = EventBus()
        assert bus.listener_count(MarketEvent.OrderFilled) == 0
        bus.add_listener(MarketEvent.OrderFilled, lambda msg: None)
        assert bus.listener_count(MarketEvent.OrderFilled) == 1

    def test_clear_all_listeners(self):
        bus = EventBus()
        bus.add_listener(MarketEvent.OrderFilled, lambda msg: None)
        bus.add_listener(MarketEvent.OrderCancelled, lambda msg: None)
        bus.clear_all_listeners()
        assert bus.listener_count(MarketEvent.OrderFilled) == 0
        assert bus.listener_count(MarketEvent.OrderCancelled) == 0

    def test_has_listeners(self):
        bus = EventBus()
        assert not bus.has_listeners(MarketEvent.OrderFilled)
        bus.add_listener(MarketEvent.OrderFilled, lambda msg: None)
        assert bus.has_listeners(MarketEvent.OrderFilled)

    def test_listener_exception_does_not_break_dispatch(self):
        bus = EventBus()
        results = []

        def bad_listener(msg):
            raise RuntimeError("boom")

        bus.add_listener(MarketEvent.OrderFilled, bad_listener)
        bus.add_listener(MarketEvent.OrderFilled, lambda msg: results.append(msg))
        bus.trigger_event(MarketEvent.OrderFilled, "data")
        assert results == ["data"]  # Second listener still called
