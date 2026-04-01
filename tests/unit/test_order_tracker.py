"""Tests for OrderTracker."""

from decimal import Decimal

from market_simulator.core.types import OrderStatus, OrderType, TradeType
from market_simulator.simulator.order_tracker import OrderTracker


class TestOrderTracker:
    def _make_tracker(self) -> OrderTracker:
        return OrderTracker()

    def test_generate_order_id(self):
        tracker = self._make_tracker()
        id1 = tracker.generate_order_id()
        id2 = tracker.generate_order_id()
        assert id1 != id2
        assert id1.startswith("SIM-")

    def test_create_order(self):
        tracker = self._make_tracker()
        order = tracker.create_order(
            trading_pair="BTC-USDT",
            order_type=OrderType.LIMIT,
            trade_type=TradeType.BUY,
            amount=Decimal("0.1"),
            price=Decimal("50000"),
        )
        assert order.status == OrderStatus.PENDING_CREATE
        assert order.trading_pair == "BTC-USDT"
        assert tracker.get_order(order.client_order_id) is order

    def test_open_order(self):
        tracker = self._make_tracker()
        order = tracker.create_order(
            trading_pair="BTC-USDT",
            order_type=OrderType.LIMIT,
            trade_type=TradeType.BUY,
            amount=Decimal("0.1"),
            price=Decimal("50000"),
        )
        tracker.open_order(order.client_order_id, timestamp=100.0)
        assert order.status == OrderStatus.OPEN
        assert order.last_update_timestamp == 100.0

    def test_cancel_order(self):
        tracker = self._make_tracker()
        order = tracker.create_order(
            trading_pair="BTC-USDT",
            order_type=OrderType.LIMIT,
            trade_type=TradeType.BUY,
            amount=Decimal("0.1"),
            price=Decimal("50000"),
        )
        tracker.open_order(order.client_order_id)
        cancelled = tracker.cancel_order(order.client_order_id, timestamp=200.0)
        assert cancelled is order
        assert order.status == OrderStatus.CANCELLED

    def test_cancel_done_order_returns_none(self):
        tracker = self._make_tracker()
        order = tracker.create_order(
            trading_pair="BTC-USDT",
            order_type=OrderType.LIMIT,
            trade_type=TradeType.BUY,
            amount=Decimal("0.1"),
            price=Decimal("50000"),
        )
        tracker.open_order(order.client_order_id)
        tracker.cancel_order(order.client_order_id)
        assert tracker.cancel_order(order.client_order_id) is None

    def test_fail_order(self):
        tracker = self._make_tracker()
        order = tracker.create_order(
            trading_pair="BTC-USDT",
            order_type=OrderType.MARKET,
            trade_type=TradeType.BUY,
            amount=Decimal("1"),
            price=Decimal("0"),
        )
        failed = tracker.fail_order(order.client_order_id)
        assert failed is order
        assert order.status == OrderStatus.FAILED

    def test_open_orders(self):
        tracker = self._make_tracker()
        o1 = tracker.create_order("BTC-USDT", OrderType.LIMIT, TradeType.BUY, Decimal("1"), Decimal("50000"))
        o2 = tracker.create_order("ETH-USDT", OrderType.LIMIT, TradeType.SELL, Decimal("10"), Decimal("3000"))
        tracker.open_order(o1.client_order_id)
        tracker.open_order(o2.client_order_id)
        tracker.cancel_order(o2.client_order_id)

        opens = tracker.open_orders
        assert len(opens) == 1
        assert opens[0].client_order_id == o1.client_order_id

    def test_open_buy_sell_orders(self):
        tracker = self._make_tracker()
        buy = tracker.create_order("BTC-USDT", OrderType.LIMIT, TradeType.BUY, Decimal("1"), Decimal("50000"))
        sell = tracker.create_order("BTC-USDT", OrderType.LIMIT, TradeType.SELL, Decimal("1"), Decimal("55000"))
        tracker.open_order(buy.client_order_id)
        tracker.open_order(sell.client_order_id)

        assert len(tracker.open_buy_orders) == 1
        assert len(tracker.open_sell_orders) == 1

    def test_in_flight_orders(self):
        tracker = self._make_tracker()
        o1 = tracker.create_order("BTC-USDT", OrderType.LIMIT, TradeType.BUY, Decimal("1"), Decimal("50000"))
        o2 = tracker.create_order("BTC-USDT", OrderType.LIMIT, TradeType.BUY, Decimal("1"), Decimal("49000"))
        tracker.open_order(o1.client_order_id)
        tracker.open_order(o2.client_order_id)
        tracker.cancel_order(o2.client_order_id)

        in_flight = tracker.in_flight_orders
        assert o1.client_order_id in in_flight
        assert o2.client_order_id not in in_flight

    def test_cleanup_done_orders(self):
        tracker = self._make_tracker()
        o1 = tracker.create_order("BTC-USDT", OrderType.LIMIT, TradeType.BUY, Decimal("1"), Decimal("50000"))
        o2 = tracker.create_order("BTC-USDT", OrderType.LIMIT, TradeType.BUY, Decimal("1"), Decimal("49000"))
        tracker.open_order(o1.client_order_id)
        tracker.open_order(o2.client_order_id)
        tracker.cancel_order(o2.client_order_id)

        removed = tracker.cleanup_done_orders()
        assert removed == 1
        assert tracker.get_order(o2.client_order_id) is None
        assert tracker.get_order(o1.client_order_id) is not None

    def test_custom_order_id(self):
        tracker = self._make_tracker()
        order = tracker.create_order(
            "BTC-USDT", OrderType.LIMIT, TradeType.BUY,
            Decimal("1"), Decimal("50000"),
            client_order_id="my-custom-id",
        )
        assert order.client_order_id == "my-custom-id"
        assert tracker.get_order("my-custom-id") is order
