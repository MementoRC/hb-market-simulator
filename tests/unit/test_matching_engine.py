"""Tests for matching engines."""

from decimal import Decimal

import pytest
from market_simulator.core.types import InFlightOrder, OrderStatus, OrderType, TradeType
from market_simulator.simulator.matching_engine import ImmediateFillEngine, LimitOrderEngine, OrderBookDepthEngine
from market_simulator.simulator.order_book import SimulatedOrderBook


@pytest.fixture
def book() -> SimulatedOrderBook:
    ob = SimulatedOrderBook("BTC-USDT")
    ob.apply_snapshot(
        bids=[
            (Decimal("50000"), Decimal("1.0")),
            (Decimal("49900"), Decimal("2.0")),
            (Decimal("49800"), Decimal("3.0")),
        ],
        asks=[
            (Decimal("50100"), Decimal("1.0")),
            (Decimal("50200"), Decimal("2.0")),
            (Decimal("50300"), Decimal("3.0")),
        ],
    )
    return ob


def _make_order(
    trade_type: TradeType = TradeType.BUY,
    order_type: OrderType = OrderType.LIMIT,
    amount: Decimal = Decimal("1.0"),
    price: Decimal = Decimal("50100"),
) -> InFlightOrder:
    return InFlightOrder(
        client_order_id="test-001",
        trading_pair="BTC-USDT",
        order_type=order_type,
        trade_type=trade_type,
        amount=amount,
        price=price,
        status=OrderStatus.OPEN,
    )


class TestImmediateFillEngine:
    def test_market_buy(self, book):
        engine = ImmediateFillEngine()
        order = _make_order(TradeType.BUY, OrderType.MARKET, price=Decimal("0"))
        result = engine.check_and_match(order, book)
        assert result is not None
        assert result.fill_price == Decimal("50100")  # Best ask
        assert result.fill_amount == Decimal("1.0")

    def test_market_sell(self, book):
        engine = ImmediateFillEngine()
        order = _make_order(TradeType.SELL, OrderType.MARKET, price=Decimal("0"))
        result = engine.check_and_match(order, book)
        assert result.fill_price == Decimal("50000")  # Best bid

    def test_limit_fills_at_limit_price(self, book):
        engine = ImmediateFillEngine()
        order = _make_order(TradeType.BUY, OrderType.LIMIT, price=Decimal("49500"))
        result = engine.check_and_match(order, book)
        assert result.fill_price == Decimal("49500")

    def test_empty_book_returns_none(self):
        engine = ImmediateFillEngine()
        empty = SimulatedOrderBook("BTC-USDT")
        order = _make_order(order_type=OrderType.MARKET)
        result = engine.check_and_match(order, empty)
        assert result is None


class TestLimitOrderEngine:
    def test_buy_limit_crosses(self, book):
        engine = LimitOrderEngine()
        # Limit at 50100, best ask is 50100 — should fill
        order = _make_order(TradeType.BUY, OrderType.LIMIT, price=Decimal("50100"))
        result = engine.check_and_match(order, book)
        assert result is not None
        assert result.fill_price == Decimal("50100")

    def test_buy_limit_no_cross(self, book):
        engine = LimitOrderEngine()
        # Limit at 50000, best ask is 50100 — should not fill
        order = _make_order(TradeType.BUY, OrderType.LIMIT, price=Decimal("50000"))
        result = engine.check_and_match(order, book)
        assert result is None

    def test_sell_limit_crosses(self, book):
        engine = LimitOrderEngine()
        # Limit at 50000, best bid is 50000 — should fill
        order = _make_order(TradeType.SELL, OrderType.LIMIT, price=Decimal("50000"))
        result = engine.check_and_match(order, book)
        assert result is not None
        assert result.fill_price == Decimal("50000")

    def test_sell_limit_no_cross(self, book):
        engine = LimitOrderEngine()
        # Limit at 50050, best bid is 50000 — should not fill
        order = _make_order(TradeType.SELL, OrderType.LIMIT, price=Decimal("50050"))
        result = engine.check_and_match(order, book)
        assert result is None

    def test_market_buy(self, book):
        engine = LimitOrderEngine()
        order = _make_order(TradeType.BUY, OrderType.MARKET, price=Decimal("0"))
        result = engine.check_and_match(order, book)
        assert result is not None
        assert result.fill_price == Decimal("50100")

    def test_limit_maker_buy_rejected_as_taker(self, book):
        engine = LimitOrderEngine()
        # Price at/above best ask — would be taker, reject
        order = _make_order(TradeType.BUY, OrderType.LIMIT_MAKER, price=Decimal("50100"))
        result = engine.check_and_match(order, book)
        assert result is None

    def test_limit_maker_sell_rejected_as_taker(self, book):
        engine = LimitOrderEngine()
        # Price at/below best bid — would be taker, reject
        order = _make_order(TradeType.SELL, OrderType.LIMIT_MAKER, price=Decimal("50000"))
        result = engine.check_and_match(order, book)
        assert result is None


class TestOrderBookDepthEngine:
    def test_market_buy_walks_depth(self, book):
        engine = OrderBookDepthEngine()
        # Buy 2.0: 1.0 @ 50100 + 1.0 @ 50200 = VWAP 50150
        order = _make_order(TradeType.BUY, OrderType.MARKET, amount=Decimal("2.0"), price=Decimal("0"))
        result = engine.check_and_match(order, book)
        assert result is not None
        assert result.fill_price == Decimal("50150")
        assert result.fill_amount == Decimal("2.0")
        assert not result.is_partial

    def test_market_sell_walks_depth(self, book):
        engine = OrderBookDepthEngine()
        # Sell 2.0: 1.0 @ 50000 + 1.0 @ 49900 = VWAP 49950
        order = _make_order(TradeType.SELL, OrderType.MARKET, amount=Decimal("2.0"), price=Decimal("0"))
        result = engine.check_and_match(order, book)
        assert result.fill_price == Decimal("49950")

    def test_partial_fill_insufficient_liquidity(self, book):
        engine = OrderBookDepthEngine()
        # Buy 10.0 but only 6.0 available (1+2+3)
        order = _make_order(TradeType.BUY, OrderType.MARKET, amount=Decimal("10.0"), price=Decimal("0"))
        result = engine.check_and_match(order, book)
        assert result is not None
        assert result.fill_amount == Decimal("6.0")
        assert result.is_partial

    def test_limit_buy_respects_price(self, book):
        engine = OrderBookDepthEngine()
        # Buy up to 50150 — only gets 50100 level
        order = _make_order(TradeType.BUY, OrderType.LIMIT, amount=Decimal("5.0"), price=Decimal("50150"))
        result = engine.check_and_match(order, book)
        assert result is not None
        assert result.fill_amount == Decimal("1.0")
        assert result.fill_price == Decimal("50100")

    def test_limit_sell_respects_price(self, book):
        engine = OrderBookDepthEngine()
        # Sell at >= 49950 — only gets 50000 level
        order = _make_order(TradeType.SELL, OrderType.LIMIT, amount=Decimal("5.0"), price=Decimal("49950"))
        result = engine.check_and_match(order, book)
        assert result is not None
        assert result.fill_amount == Decimal("1.0")
        assert result.fill_price == Decimal("50000")

    def test_limit_no_cross(self, book):
        engine = OrderBookDepthEngine()
        order = _make_order(TradeType.BUY, OrderType.LIMIT, amount=Decimal("1.0"), price=Decimal("50000"))
        result = engine.check_and_match(order, book)
        assert result is None

    def test_empty_book(self):
        engine = OrderBookDepthEngine()
        empty = SimulatedOrderBook("BTC-USDT")
        order = _make_order(order_type=OrderType.MARKET)
        result = engine.check_and_match(order, empty)
        assert result is None
