"""Tests for core data types."""

from decimal import Decimal

from market_simulator.core.types import (
    InFlightOrder,
    MatchResult,
    OrderStatus,
    OrderType,
    TradeFee,
    TradeType,
    TradingRule,
)


class TestTradingRule:
    def test_quantize_amount(self):
        rule = TradingRule("BTC-USDT", min_base_amount_increment=Decimal("0.001"))
        assert rule.quantize_order_amount(Decimal("1.23456")) == Decimal("1.234")

    def test_quantize_price(self):
        rule = TradingRule("BTC-USDT", min_price_increment=Decimal("0.01"))
        assert rule.quantize_order_price(Decimal("50123.456")) == Decimal("50123.45")

    def test_quantize_zero_increment(self):
        rule = TradingRule("BTC-USDT", min_base_amount_increment=Decimal("0"))
        assert rule.quantize_order_amount(Decimal("1.23456")) == Decimal("1.23456")


class TestTradeFee:
    def test_total_flat_fee(self):
        fee = TradeFee(flat_fees=[("USDT", Decimal("1.5")), ("BNB", Decimal("0.01"))])
        assert fee.total_flat_fee == Decimal("1.51")

    def test_empty_flat_fees(self):
        fee = TradeFee()
        assert fee.total_flat_fee == Decimal("0")


class TestInFlightOrder:
    def _make_order(self, **kwargs) -> InFlightOrder:
        defaults = {
            "client_order_id": "test-001",
            "trading_pair": "BTC-USDT",
            "order_type": OrderType.LIMIT,
            "trade_type": TradeType.BUY,
            "amount": Decimal("1.0"),
            "price": Decimal("50000"),
        }
        defaults.update(kwargs)
        return InFlightOrder(**defaults)

    def test_initial_state(self):
        order = self._make_order()
        assert order.is_open
        assert not order.is_done
        assert order.remaining_amount == Decimal("1.0")

    def test_base_quote_assets(self):
        order = self._make_order()
        assert order.base_asset == "BTC"
        assert order.quote_asset == "USDT"

    def test_partial_fill(self):
        order = self._make_order()
        order.update_with_fill(Decimal("49900"), Decimal("0.5"), Decimal("0.1"))
        assert order.status == OrderStatus.PARTIALLY_FILLED
        assert order.filled_amount == Decimal("0.5")
        assert order.remaining_amount == Decimal("0.5")
        assert order.filled_price == Decimal("49900")

    def test_full_fill(self):
        order = self._make_order()
        order.update_with_fill(Decimal("50000"), Decimal("1.0"), Decimal("0.5"))
        assert order.status == OrderStatus.FILLED
        assert order.is_done
        assert not order.is_open

    def test_vwap_on_multiple_fills(self):
        order = self._make_order()
        order.update_with_fill(Decimal("49000"), Decimal("0.5"), Decimal("0"))
        order.update_with_fill(Decimal("51000"), Decimal("0.5"), Decimal("0"))
        assert order.filled_amount == Decimal("1.0")
        assert order.filled_price == Decimal("50000")  # VWAP

    def test_cancelled_state(self):
        order = self._make_order(status=OrderStatus.CANCELLED)
        assert order.is_done
        assert not order.is_open


class TestMatchResult:
    def test_basic(self):
        result = MatchResult(fill_price=Decimal("50000"), fill_amount=Decimal("0.1"))
        assert result.fill_price == Decimal("50000")
        assert not result.is_partial
