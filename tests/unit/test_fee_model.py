"""Tests for fee models."""

from decimal import Decimal

from market_simulator.core.types import OrderType, TradeType
from market_simulator.simulator.fee_model import FlatFeeModel, ZeroFeeModel


class TestZeroFeeModel:
    def test_zero_fees(self):
        model = ZeroFeeModel()
        fee = model.calculate_fee("BTC-USDT", TradeType.BUY, OrderType.MARKET, Decimal("1"), Decimal("50000"))
        assert fee.percent == Decimal("0")
        assert fee.total_flat_fee == Decimal("0")


class TestFlatFeeModel:
    def test_taker_fee(self):
        model = FlatFeeModel(maker_rate=Decimal("0.001"), taker_rate=Decimal("0.002"))
        fee = model.calculate_fee(
            "BTC-USDT", TradeType.BUY, OrderType.MARKET, Decimal("0.1"), Decimal("50000")
        )
        assert fee.percent == Decimal("0.002")
        # 0.1 * 50000 * 0.002 = 10
        assert fee.flat_fees[0] == ("USDT", Decimal("10"))

    def test_maker_fee(self):
        model = FlatFeeModel(maker_rate=Decimal("0.0005"), taker_rate=Decimal("0.001"))
        fee = model.calculate_fee(
            "BTC-USDT", TradeType.BUY, OrderType.LIMIT_MAKER, Decimal("0.1"), Decimal("50000")
        )
        assert fee.percent == Decimal("0.0005")
        # 0.1 * 50000 * 0.0005 = 2.5
        assert fee.flat_fees[0] == ("USDT", Decimal("2.5"))

    def test_limit_order_uses_taker_rate(self):
        model = FlatFeeModel(maker_rate=Decimal("0.001"), taker_rate=Decimal("0.002"))
        fee = model.calculate_fee(
            "ETH-USDT", TradeType.SELL, OrderType.LIMIT, Decimal("10"), Decimal("3000")
        )
        assert fee.percent == Decimal("0.002")
