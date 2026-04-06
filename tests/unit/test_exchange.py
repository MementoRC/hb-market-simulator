"""Tests for SimulatedExchange — full order lifecycle integration."""

from decimal import Decimal

import pytest

from market_simulator.core.events import MarketEvent
from market_simulator.core.types import (
    OrderStatus,
    OrderType,
    PriceType,
    TradeType,
    TradingRule,
)
from market_simulator.simulator.exchange import (
    OrderCancelledEvent,
    OrderCompletedEvent,
    OrderCreatedEvent,
    OrderFailureEvent,
    OrderFilledEvent,
    SimulatedExchange,
    SimulatedExchangeConfig,
)
from market_simulator.simulator.fee_model import FlatFeeModel, ZeroFeeModel
from market_simulator.simulator.matching_engine import ImmediateFillEngine


def _make_exchange(
    fee_model=None,
    matching_engine=None,
    initial_balances=None,
) -> SimulatedExchange:
    """Create a SimulatedExchange with standard BTC-USDT order book."""
    config = SimulatedExchangeConfig(
        name="test_exchange",
        trading_pairs=["BTC-USDT"],
        initial_balances=initial_balances or {"USDT": Decimal("100000"), "BTC": Decimal("10")},
    )
    ex = SimulatedExchange(
        config=config,
        matching_engine=matching_engine,
        fee_model=fee_model or ZeroFeeModel(),
    )
    # Apply a standard order book snapshot
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


class EventCollector:
    """Collects events for verification."""

    def __init__(self):
        self.events: list = []

    def __call__(self, event):
        self.events.append(event)

    def clear(self):
        self.events.clear()


class TestSimulatedExchangeProperties:
    def test_name_and_pairs(self):
        ex = _make_exchange()
        assert ex.name == "test_exchange"
        assert ex.trading_pairs == ["BTC-USDT"]

    def test_balances(self):
        ex = _make_exchange()
        assert ex.get_balance("USDT") == Decimal("100000")
        assert ex.get_available_balance("BTC") == Decimal("10")

    def test_price_queries(self):
        ex = _make_exchange()
        assert ex.get_price_by_type("BTC-USDT", PriceType.BestBid) == Decimal("50000")
        assert ex.get_price_by_type("BTC-USDT", PriceType.BestAsk) == Decimal("50100")

    def test_quantize(self):
        ex = _make_exchange()
        ex.trading_rules["BTC-USDT"] = TradingRule(
            "BTC-USDT",
            min_price_increment=Decimal("0.01"),
            min_base_amount_increment=Decimal("0.001"),
        )
        assert ex.quantize_order_price("BTC-USDT", Decimal("50000.123")) == Decimal("50000.12")
        assert ex.quantize_order_amount("BTC-USDT", Decimal("1.2345")) == Decimal("1.234")

    def test_unknown_pair_raises(self):
        ex = _make_exchange()
        with pytest.raises(ValueError, match="Unknown"):
            ex.get_order_book("ETH-USDT")


class TestMarketOrderBuy:
    def test_market_buy_immediate_fill(self):
        ex = _make_exchange(matching_engine=ImmediateFillEngine())
        collector = EventCollector()
        ex.add_listener(MarketEvent.BuyOrderCreated, collector)
        ex.add_listener(MarketEvent.OrderFilled, collector)
        ex.add_listener(MarketEvent.BuyOrderCompleted, collector)

        ex.buy("BTC-USDT", Decimal("1.0"), OrderType.MARKET, Decimal("0"))

        # Should get Created -> Filled -> Completed
        assert len(collector.events) == 3
        assert isinstance(collector.events[0], OrderCreatedEvent)
        assert isinstance(collector.events[1], OrderFilledEvent)
        assert isinstance(collector.events[2], OrderCompletedEvent)

        # Fill at best ask
        fill_event = collector.events[1]
        assert fill_event.fill_price == Decimal("50100")
        assert fill_event.fill_amount == Decimal("1.0")

        # Balance updated: spent 50100 USDT, got 1 BTC
        assert ex.get_balance("USDT") == Decimal("49900")
        assert ex.get_balance("BTC") == Decimal("11")

    def test_market_buy_with_fees(self):
        fee_model = FlatFeeModel(taker_rate=Decimal("0.001"))
        ex = _make_exchange(matching_engine=ImmediateFillEngine(), fee_model=fee_model)

        ex.buy("BTC-USDT", Decimal("1.0"), OrderType.MARKET, Decimal("0"))

        # Cost: 50100 + fee (50100 * 0.001 = 50.1)
        assert ex.get_balance("USDT") == Decimal("100000") - Decimal("50100") - Decimal("50.1")
        assert ex.get_balance("BTC") == Decimal("11")


class TestMarketOrderSell:
    def test_market_sell_immediate_fill(self):
        ex = _make_exchange(matching_engine=ImmediateFillEngine())
        collector = EventCollector()
        ex.add_listener(MarketEvent.SellOrderCreated, collector)
        ex.add_listener(MarketEvent.OrderFilled, collector)
        ex.add_listener(MarketEvent.SellOrderCompleted, collector)

        ex.sell("BTC-USDT", Decimal("1.0"), OrderType.MARKET, Decimal("0"))

        assert len(collector.events) == 3
        fill_event = collector.events[1]
        assert fill_event.fill_price == Decimal("50000")  # Best bid

        # Balance: sold 1 BTC, got 50000 USDT
        assert ex.get_balance("BTC") == Decimal("9")
        assert ex.get_balance("USDT") == Decimal("150000")


class TestLimitOrders:
    def test_limit_buy_not_immediately_filled(self):
        ex = _make_exchange()  # Default LimitOrderEngine
        collector = EventCollector()
        ex.add_listener(MarketEvent.BuyOrderCreated, collector)
        ex.add_listener(MarketEvent.OrderFilled, collector)

        # Place limit below best ask — should not fill
        order_id = ex.buy("BTC-USDT", Decimal("1.0"), OrderType.LIMIT, Decimal("50000"))

        assert len(collector.events) == 1  # Only Created, no Fill
        assert isinstance(collector.events[0], OrderCreatedEvent)

        # Collateral locked
        assert ex.get_available_balance("USDT") == Decimal("50000")  # 100000 - 50000

        # Order is open
        order = ex.get_in_flight_order(order_id)
        assert order.status == OrderStatus.OPEN

    def test_limit_buy_fills_on_tick(self):
        ex = _make_exchange()
        fill_collector = EventCollector()
        ex.add_listener(MarketEvent.OrderFilled, fill_collector)
        ex.add_listener(MarketEvent.BuyOrderCompleted, fill_collector)

        # Place limit at 50100 (at best ask)
        ex.buy("BTC-USDT", Decimal("1.0"), OrderType.LIMIT, Decimal("50100"))

        # Tick the exchange — should match
        ex.process_tick(1.0)

        assert len(fill_collector.events) == 2
        assert isinstance(fill_collector.events[0], OrderFilledEvent)
        assert isinstance(fill_collector.events[1], OrderCompletedEvent)

    def test_limit_sell_fills_on_tick(self):
        ex = _make_exchange()
        collector = EventCollector()
        ex.add_listener(MarketEvent.OrderFilled, collector)

        # Sell at 50000 (at best bid)
        ex.sell("BTC-USDT", Decimal("1.0"), OrderType.LIMIT, Decimal("50000"))
        ex.process_tick(1.0)

        assert len(collector.events) == 1
        assert collector.events[0].fill_price == Decimal("50000")


class TestOrderCancellation:
    def test_cancel_releases_collateral(self):
        ex = _make_exchange()
        order_id = ex.buy("BTC-USDT", Decimal("1.0"), OrderType.LIMIT, Decimal("49000"))

        # Collateral locked
        assert ex.get_available_balance("USDT") == Decimal("51000")  # 100000 - 49000

        cancel_collector = EventCollector()
        ex.add_listener(MarketEvent.OrderCancelled, cancel_collector)

        ex.cancel("BTC-USDT", order_id)

        assert len(cancel_collector.events) == 1
        assert isinstance(cancel_collector.events[0], OrderCancelledEvent)

        # Collateral released
        assert ex.get_available_balance("USDT") == Decimal("100000")

    def test_cancel_nonexistent_is_noop(self):
        ex = _make_exchange()
        ex.cancel("BTC-USDT", "nonexistent-id")  # Should not raise


class TestInsufficientBalance:
    def test_buy_insufficient_balance_emits_failure(self):
        ex = _make_exchange(
            initial_balances={"USDT": Decimal("100"), "BTC": Decimal("0")},
            matching_engine=ImmediateFillEngine(),
        )
        failure_collector = EventCollector()
        ex.add_listener(MarketEvent.OrderFailure, failure_collector)

        order_id = ex.buy("BTC-USDT", Decimal("1.0"), OrderType.MARKET, Decimal("50000"))

        assert len(failure_collector.events) == 1
        assert isinstance(failure_collector.events[0], OrderFailureEvent)

        order = ex.get_in_flight_order(order_id)
        assert order.status == OrderStatus.FAILED

    def test_sell_insufficient_balance(self):
        ex = _make_exchange(
            initial_balances={"USDT": Decimal("10000"), "BTC": Decimal("0")},
            matching_engine=ImmediateFillEngine(),
        )
        failure_collector = EventCollector()
        ex.add_listener(MarketEvent.OrderFailure, failure_collector)

        ex.sell("BTC-USDT", Decimal("1.0"), OrderType.MARKET, Decimal("0"))
        assert len(failure_collector.events) == 1


class TestMultipleOrders:
    def test_multiple_limit_orders(self):
        ex = _make_exchange()
        ex.buy("BTC-USDT", Decimal("0.5"), OrderType.LIMIT, Decimal("50100"))
        ex.buy("BTC-USDT", Decimal("0.5"), OrderType.LIMIT, Decimal("50200"))

        assert len(ex.in_flight_orders) == 2

        # Both should fill on tick
        fill_collector = EventCollector()
        ex.add_listener(MarketEvent.OrderFilled, fill_collector)
        ex.process_tick(1.0)

        assert len(fill_collector.events) == 2

    def test_cancel_one_keep_other(self):
        ex = _make_exchange()
        id1 = ex.buy("BTC-USDT", Decimal("1.0"), OrderType.LIMIT, Decimal("49000"))
        id2 = ex.buy("BTC-USDT", Decimal("1.0"), OrderType.LIMIT, Decimal("49000"))

        ex.cancel("BTC-USDT", id1)

        open_orders = ex.order_tracker.open_orders
        assert len(open_orders) == 1
        assert open_orders[0].client_order_id == id2


class TestEventSequence:
    def test_buy_event_sequence(self):
        """Verify exact event sequence: Created -> Filled -> Completed."""
        ex = _make_exchange(matching_engine=ImmediateFillEngine())
        events = []

        ex.add_listener(MarketEvent.BuyOrderCreated, lambda e: events.append(("created", e)))
        ex.add_listener(MarketEvent.OrderFilled, lambda e: events.append(("filled", e)))
        ex.add_listener(MarketEvent.BuyOrderCompleted, lambda e: events.append(("completed", e)))

        ex.buy("BTC-USDT", Decimal("1.0"), OrderType.MARKET, Decimal("0"))

        assert [tag for tag, _ in events] == ["created", "filled", "completed"]

    def test_cancel_event_sequence(self):
        """Verify: Created -> Cancelled."""
        ex = _make_exchange()
        events = []

        ex.add_listener(MarketEvent.BuyOrderCreated, lambda e: events.append(("created", e)))
        ex.add_listener(MarketEvent.OrderCancelled, lambda e: events.append(("cancelled", e)))

        order_id = ex.buy("BTC-USDT", Decimal("1.0"), OrderType.LIMIT, Decimal("49000"))
        ex.cancel("BTC-USDT", order_id)

        assert [tag for tag, _ in events] == ["created", "cancelled"]

    def test_failure_event_sequence(self):
        """Verify: OrderFailure (no Created for failed orders)."""
        ex = _make_exchange(
            initial_balances={"USDT": Decimal("10")},
            matching_engine=ImmediateFillEngine(),
        )
        events = []
        ex.add_listener(MarketEvent.BuyOrderCreated, lambda e: events.append("created"))
        ex.add_listener(MarketEvent.OrderFailure, lambda e: events.append("failure"))

        ex.buy("BTC-USDT", Decimal("1.0"), OrderType.MARKET, Decimal("50000"))

        # Failure happens before Created (insufficient balance blocks order opening)
        assert events == ["failure"]


class TestOrderBookImpact:
    def test_fill_consumes_liquidity(self):
        ex = _make_exchange(matching_engine=ImmediateFillEngine())

        # Buy 3.0 at best ask (50100 has 5.0 available)
        ex.buy("BTC-USDT", Decimal("3.0"), OrderType.MARKET, Decimal("0"))

        book = ex.get_order_book("BTC-USDT")
        # 5.0 - 3.0 = 2.0 remaining at 50100
        depth = book.get_depth(TradeType.SELL, levels=1)
        assert depth[0] == (Decimal("50100"), Decimal("2.0"))
