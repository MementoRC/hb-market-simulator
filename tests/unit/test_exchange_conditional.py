"""Tests for conditional order types in SimulatedExchange.

Exercises stop-loss, take-profit, and trailing-stop orders through the full
exchange lifecycle: placement, trigger evaluation on process_tick(), OrderTriggered
event emission, and MARKET child order fill.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from market_simulator.core.events import MarketEvent
from market_simulator.core.types import OrderStatus, OrderType, TradeType
from market_simulator.simulator.exchange import (
    OrderCancelledEvent,
    OrderCompletedEvent,
    OrderCreatedEvent,
    OrderFailureEvent,
    OrderFilledEvent,
    OrderTriggeredEvent,
    SimulatedExchange,
    SimulatedExchangeConfig,
)
from market_simulator.simulator.fee_model import ZeroFeeModel
from market_simulator.simulator.matching_engine import ImmediateFillEngine
from market_simulator.simulator.trigger_engine import (
    NullTriggerEngine,
    StandardTriggerEngine,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class EventCollector:
    """Collects events for verification — identical to test_exchange.py pattern."""

    def __init__(self):
        self.events: list = []

    def __call__(self, event):
        self.events.append(event)

    def clear(self):
        self.events.clear()


def _make_exchange(
    initial_balances: dict | None = None,
    trigger_engine=None,
) -> SimulatedExchange:
    """Create a SimulatedExchange suitable for conditional-order tests.

    Uses ImmediateFillEngine so that the MARKET child order spawned by a fired
    conditional always fills in the same tick, and ZeroFeeModel to keep balance
    arithmetic simple.

    The order book is set with bid=50000 / ask=50100, providing a baseline
    where most trigger prices can be set above or below these levels.
    """
    config = SimulatedExchangeConfig(
        name="test_exchange",
        trading_pairs=["BTC-USDT"],
        initial_balances=initial_balances
        or {"USDT": Decimal("100000"), "BTC": Decimal("10")},
    )
    ex = SimulatedExchange(
        config=config,
        matching_engine=ImmediateFillEngine(),
        fee_model=ZeroFeeModel(),
        trigger_engine=trigger_engine or StandardTriggerEngine(),
    )
    book = ex.get_order_book("BTC-USDT")
    book.apply_snapshot(
        bids=[
            (Decimal("50000"), Decimal("5.0")),
            (Decimal("49900"), Decimal("10.0")),
        ],
        asks=[
            (Decimal("50100"), Decimal("5.0")),
            (Decimal("50200"), Decimal("10.0")),
        ],
    )
    ex.ready = True
    return ex


def _register_all_conditional_events(ex: SimulatedExchange) -> EventCollector:
    """Register a single EventCollector for all conditional + fill events."""
    collector = EventCollector()
    for tag in (
        MarketEvent.OrderTriggered,
        MarketEvent.OrderFilled,
        MarketEvent.BuyOrderCompleted,
        MarketEvent.SellOrderCompleted,
    ):
        ex.add_listener(tag, collector)
    return collector


# ---------------------------------------------------------------------------
# TestConditionalOrderFiring
# ---------------------------------------------------------------------------


class TestConditionalOrderFiring:
    """Stop-loss and take-profit fire-and-fill for both BUY and SELL."""

    def test_buy_stop_loss_fires_and_fills_on_tick(self):
        """BUY STOP_LOSS fires when ask >= trigger and MARKET child fills immediately."""
        # Best ask is already 50100; set trigger at 50100 so it fires on next tick.
        ex = _make_exchange()
        collector = _register_all_conditional_events(ex)

        order_id = ex.buy(
            "BTC-USDT",
            Decimal("1.0"),
            OrderType.STOP_LOSS,
            price=Decimal("50100"),
            trigger_price=Decimal("50100"),
        )

        ex.process_tick(1.0)

        # Expect: OrderTriggered, OrderFilled (child MARKET), BuyOrderCompleted
        event_types = [type(e) for e in collector.events]
        assert OrderTriggeredEvent in event_types
        assert OrderFilledEvent in event_types
        assert OrderCompletedEvent in event_types

        # Original conditional order should now be CANCELLED (replaced by child)
        original = ex.get_in_flight_order(order_id)
        assert original is not None
        assert original.status == OrderStatus.CANCELLED

    def test_sell_stop_loss_fires_and_fills_on_tick(self):
        """SELL STOP_LOSS fires when bid <= trigger and MARKET child fills immediately."""
        # Best bid is 50000; set trigger at 50000 so it fires immediately.
        ex = _make_exchange()
        collector = _register_all_conditional_events(ex)

        order_id = ex.sell(
            "BTC-USDT",
            Decimal("1.0"),
            OrderType.STOP_LOSS,
            price=Decimal("50000"),
            trigger_price=Decimal("50000"),
        )

        ex.process_tick(1.0)

        event_types = [type(e) for e in collector.events]
        assert OrderTriggeredEvent in event_types
        assert OrderFilledEvent in event_types

        original = ex.get_in_flight_order(order_id)
        assert original.status == OrderStatus.CANCELLED

    def test_buy_take_profit_fires_when_ask_drops_to_trigger(self):
        """BUY TAKE_PROFIT fires when ask <= trigger_price."""
        # Best ask is 50100; set trigger at 50100 so the condition is immediately met.
        ex = _make_exchange()
        collector = _register_all_conditional_events(ex)

        order_id = ex.buy(
            "BTC-USDT",
            Decimal("1.0"),
            OrderType.TAKE_PROFIT,
            price=Decimal("50100"),
            trigger_price=Decimal("50100"),
        )

        ex.process_tick(1.0)

        event_types = [type(e) for e in collector.events]
        assert OrderTriggeredEvent in event_types
        assert OrderFilledEvent in event_types

    def test_sell_take_profit_fires_when_bid_rises_to_trigger(self):
        """SELL TAKE_PROFIT fires when bid >= trigger_price."""
        # Best bid is 50000; set trigger at 50000.
        ex = _make_exchange()
        collector = _register_all_conditional_events(ex)

        order_id = ex.sell(
            "BTC-USDT",
            Decimal("1.0"),
            OrderType.TAKE_PROFIT,
            price=Decimal("50000"),
            trigger_price=Decimal("50000"),
        )

        ex.process_tick(1.0)

        event_types = [type(e) for e in collector.events]
        assert OrderTriggeredEvent in event_types
        assert OrderFilledEvent in event_types


# ---------------------------------------------------------------------------
# TestTrailingStopExchange
# ---------------------------------------------------------------------------


class TestTrailingStopExchange:
    """Trailing-stop integration: watermark advance then retrace fires the order."""

    def test_buy_trailing_stop_fires_after_retrace(self):
        """BUY TRAILING_STOP: ask drops then rises by trail_amount → fires."""
        ex = _make_exchange()
        collector = _register_all_conditional_events(ex)

        # Set up order book with a lower ask to establish the watermark
        book = ex.get_order_book("BTC-USDT")

        # Place trailing stop: trigger_price sets initial watermark at 50000,
        # trail_amount = 200. Fires when ask >= watermark + 200.
        order_id = ex.buy(
            "BTC-USDT",
            Decimal("1.0"),
            OrderType.TRAILING_STOP,
            price=Decimal("50000"),
            trigger_price=Decimal("50000"),
            trail_amount=Decimal("200"),
        )

        # First tick: ask at 50100 (below 50000+200=50200) — should NOT fire
        ex.process_tick(1.0)
        assert not any(isinstance(e, OrderTriggeredEvent) for e in collector.events)

        # Simulate ask dropping to 49500 to move watermark down
        book.apply_snapshot(
            bids=[(Decimal("49400"), Decimal("5.0"))],
            asks=[(Decimal("49500"), Decimal("5.0"))],
        )
        ex.process_tick(2.0)
        # ask=49500 < watermark 50000 → watermark drops to 49500; fire level = 49700
        assert not any(isinstance(e, OrderTriggeredEvent) for e in collector.events)

        # Now retrace: ask rises to 49700 (= 49500 + 200) → should fire
        book.apply_snapshot(
            bids=[(Decimal("49600"), Decimal("5.0"))],
            asks=[(Decimal("49700"), Decimal("5.0"))],
        )
        ex.process_tick(3.0)

        assert any(isinstance(e, OrderTriggeredEvent) for e in collector.events)
        assert any(isinstance(e, OrderFilledEvent) for e in collector.events)

    def test_sell_trailing_stop_fires_after_retrace(self):
        """SELL TRAILING_STOP: bid rises then drops by trail_amount → fires."""
        ex = _make_exchange()
        collector = _register_all_conditional_events(ex)
        book = ex.get_order_book("BTC-USDT")

        # trigger_price=50000, trail_amount=200; fires when bid <= watermark - 200
        order_id = ex.sell(
            "BTC-USDT",
            Decimal("1.0"),
            OrderType.TRAILING_STOP,
            price=Decimal("50000"),
            trigger_price=Decimal("50000"),
            trail_amount=Decimal("200"),
        )

        # First tick: bid=50000, fire level = 50000-200=49800; bid > 49800 → no fire
        ex.process_tick(1.0)
        assert not any(isinstance(e, OrderTriggeredEvent) for e in collector.events)

        # Bid rises to 51000 → watermark = 51000; fire level = 50800
        book.apply_snapshot(
            bids=[(Decimal("51000"), Decimal("5.0"))],
            asks=[(Decimal("51100"), Decimal("5.0"))],
        )
        ex.process_tick(2.0)
        assert not any(isinstance(e, OrderTriggeredEvent) for e in collector.events)

        # Bid drops to 50800 (= 51000 - 200) → fires
        book.apply_snapshot(
            bids=[(Decimal("50800"), Decimal("5.0"))],
            asks=[(Decimal("50900"), Decimal("5.0"))],
        )
        ex.process_tick(3.0)

        assert any(isinstance(e, OrderTriggeredEvent) for e in collector.events)
        assert any(isinstance(e, OrderFilledEvent) for e in collector.events)


# ---------------------------------------------------------------------------
# TestConditionalOrderManagement
# ---------------------------------------------------------------------------


class TestConditionalOrderManagement:
    """Lifecycle management: collateral, events, IDs, and balance failure."""

    def test_cancel_conditional_releases_collateral(self):
        """Cancelling a conditional BUY order releases the locked quote collateral."""
        ex = _make_exchange()
        # trigger_price = 51000; collateral locked = 1.0 * 51000 = 51000 USDT
        order_id = ex.buy(
            "BTC-USDT",
            Decimal("1.0"),
            OrderType.STOP_LOSS,
            price=Decimal("51000"),
            trigger_price=Decimal("51000"),
        )
        available_after_lock = ex.get_available_balance("USDT")
        assert available_after_lock == Decimal("100000") - Decimal("51000")

        cancel_collector = EventCollector()
        ex.add_listener(MarketEvent.OrderCancelled, cancel_collector)

        ex.cancel("BTC-USDT", order_id)

        # Collateral fully released
        assert ex.get_available_balance("USDT") == Decimal("100000")
        # Cancel event emitted
        assert len(cancel_collector.events) == 1
        assert isinstance(cancel_collector.events[0], OrderCancelledEvent)

    def test_order_triggered_event_has_correct_fields(self):
        """OrderTriggeredEvent carries original_order_id, pair, type, and trade_type."""
        ex = _make_exchange()
        triggered_collector = EventCollector()
        ex.add_listener(MarketEvent.OrderTriggered, triggered_collector)

        order_id = ex.buy(
            "BTC-USDT",
            Decimal("1.0"),
            OrderType.STOP_LOSS,
            price=Decimal("50100"),
            trigger_price=Decimal("50100"),
        )

        ex.process_tick(1.0)

        assert len(triggered_collector.events) == 1
        evt: OrderTriggeredEvent = triggered_collector.events[0]
        assert isinstance(evt, OrderTriggeredEvent)
        assert evt.original_order_id == order_id
        assert evt.trading_pair == "BTC-USDT"
        assert evt.order_type == OrderType.STOP_LOSS
        assert evt.trade_type == TradeType.BUY
        assert evt.trigger_price == Decimal("50100")

    def test_market_child_order_has_different_id_from_original(self):
        """The MARKET child spawned by firing must have a new client_order_id."""
        ex = _make_exchange()
        created_collector = EventCollector()
        ex.add_listener(MarketEvent.BuyOrderCreated, created_collector)

        # First created event = the conditional order itself; second = the child MARKET
        order_id = ex.buy(
            "BTC-USDT",
            Decimal("1.0"),
            OrderType.STOP_LOSS,
            price=Decimal("50100"),
            trigger_price=Decimal("50100"),
        )

        ex.process_tick(1.0)

        assert len(created_collector.events) == 2
        child_id = created_collector.events[1].order_id
        assert child_id != order_id
        # Child is a MARKET order
        assert created_collector.events[1].order_type == OrderType.MARKET

    def test_conditional_order_fails_when_insufficient_balance(self):
        """Placing a conditional order with insufficient collateral emits OrderFailure."""
        # Only 100 USDT; trigger_price=50100 requires 50100 USDT collateral → fails
        ex = _make_exchange(
            initial_balances={"USDT": Decimal("100"), "BTC": Decimal("0")}
        )
        failure_collector = EventCollector()
        ex.add_listener(MarketEvent.OrderFailure, failure_collector)

        order_id = ex.buy(
            "BTC-USDT",
            Decimal("1.0"),
            OrderType.STOP_LOSS,
            price=Decimal("50100"),
            trigger_price=Decimal("50100"),
        )

        assert len(failure_collector.events) == 1
        assert isinstance(failure_collector.events[0], OrderFailureEvent)

        order = ex.get_in_flight_order(order_id)
        assert order.status == OrderStatus.FAILED


# ---------------------------------------------------------------------------
# TestNullTriggerEngine
# ---------------------------------------------------------------------------


class TestNullTriggerEngine:
    """With NullTriggerEngine, conditional orders never fire regardless of price."""

    def test_conditional_orders_never_fire_with_null_engine(self):
        """NullTriggerEngine prevents stop-loss from firing even when ask >= trigger."""
        ex = _make_exchange(trigger_engine=NullTriggerEngine())
        triggered_collector = EventCollector()
        fill_collector = EventCollector()
        ex.add_listener(MarketEvent.OrderTriggered, triggered_collector)
        ex.add_listener(MarketEvent.OrderFilled, fill_collector)

        # Trigger at 50100, ask is already 50100 → would fire with StandardTriggerEngine
        order_id = ex.buy(
            "BTC-USDT",
            Decimal("1.0"),
            OrderType.STOP_LOSS,
            price=Decimal("50100"),
            trigger_price=Decimal("50100"),
        )

        ex.process_tick(1.0)
        ex.process_tick(2.0)
        ex.process_tick(3.0)

        # No trigger and no fill events
        assert len(triggered_collector.events) == 0
        assert len(fill_collector.events) == 0

        # Order remains open
        order = ex.get_in_flight_order(order_id)
        assert order.status == OrderStatus.OPEN
