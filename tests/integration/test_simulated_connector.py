"""Integration tests for SimulatedConnector.

These tests verify that SimulatedConnector correctly extends ExchangePyBase
and can be used with hummingbot's strategy_v2 infrastructure.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock

import pytest

pytest.importorskip("hummingbot", reason="hummingbot not installed (CI-safe skip)")

from hummingbot.connector.exchange_py_base import ExchangePyBase
from hummingbot.core.data_type.common import OrderType, TradeType

from market_simulator.hb_compat.simulated_connector import SimulatedConnector
from market_simulator.simulator.exchange import SimulatedExchangeConfig
from market_simulator.simulator.fee_model import FlatFeeModel, ZeroFeeModel
from market_simulator.simulator.matching_engine import ImmediateFillEngine


def _make_connector(
    fee_model=None,
    matching_engine=None,
    initial_balances=None,
) -> SimulatedConnector:
    config = SimulatedExchangeConfig(
        name="test_sim",
        trading_pairs=["BTC-USDT"],
        initial_balances=initial_balances or {"USDT": Decimal("100000"), "BTC": Decimal("10")},
    )
    connector = SimulatedConnector(
        config=config,
        matching_engine=matching_engine or ImmediateFillEngine(),
        fee_model=fee_model or ZeroFeeModel(),
    )

    # Apply order book snapshot to sim exchange
    book = connector.sim_exchange.get_order_book("BTC-USDT")
    book.apply_snapshot(
        bids=[
            (Decimal("50000"), Decimal("10.0")),
            (Decimal("49900"), Decimal("20.0")),
        ],
        asks=[
            (Decimal("50100"), Decimal("10.0")),
            (Decimal("50200"), Decimal("20.0")),
        ],
    )
    connector.sim_exchange.ready = True

    return connector


class TestSimulatedConnectorIsExchangePyBase:
    def test_isinstance(self):
        connector = _make_connector()
        assert isinstance(connector, ExchangePyBase)

    def test_name(self):
        connector = _make_connector()
        assert connector.name == "test_sim"

    def test_trading_pairs(self):
        connector = _make_connector()
        assert connector.trading_pairs == ["BTC-USDT"]

    def test_supported_order_types(self):
        connector = _make_connector()
        types = connector.supported_order_types()
        assert OrderType.LIMIT in types
        assert OrderType.MARKET in types


class TestSimulatedConnectorBalances:
    async def test_update_balances(self):
        connector = _make_connector()
        await connector._update_balances()
        assert connector.get_balance("USDT") == Decimal("100000")
        assert connector.get_balance("BTC") == Decimal("10")

    async def test_available_balances(self):
        connector = _make_connector()
        await connector._update_balances()
        assert connector.get_available_balance("USDT") == Decimal("100000")


class TestSimulatedConnectorTradingRules:
    async def test_update_trading_rules(self):
        connector = _make_connector()
        await connector._update_trading_rules()
        assert "BTC-USDT" in connector.trading_rules


class TestSimulatedConnectorOrderPlacement:
    async def test_place_order(self):
        connector = _make_connector()
        await connector._update_balances()
        await connector._update_trading_rules()

        exchange_order_id, timestamp = await connector._place_order(
            order_id="test-001",
            trading_pair="BTC-USDT",
            amount=Decimal("0.1"),
            trade_type=TradeType.BUY,
            order_type=OrderType.MARKET,
            price=Decimal("0"),
        )

        assert exchange_order_id.startswith("SIMEX-")
        assert isinstance(timestamp, float)

    async def test_place_cancel(self):
        connector = _make_connector()
        await connector._update_balances()
        await connector._update_trading_rules()

        # Place a limit order on the sim exchange
        from market_simulator.core.types import OrderType as SimOrderType

        sim_order_id = connector.sim_exchange.buy(
            "BTC-USDT", Decimal("0.1"), SimOrderType.LIMIT, Decimal("49000")
        )

        # Create a mock tracked order
        tracked_order = MagicMock()
        tracked_order.trading_pair = "BTC-USDT"
        tracked_order.client_order_id = sim_order_id

        result = await connector._place_cancel(sim_order_id, tracked_order)
        assert result is True


class TestSimulatedConnectorFees:
    def test_zero_fee(self):
        connector = _make_connector(fee_model=ZeroFeeModel())
        fee = connector._get_fee("BTC", "USDT", OrderType.MARKET, TradeType.BUY, Decimal("1"))
        assert fee.percent == Decimal("0")

    def test_flat_fee(self):
        connector = _make_connector(
            fee_model=FlatFeeModel(
                maker_rate=Decimal("0.0005"),
                taker_rate=Decimal("0.001"),
            )
        )
        # Taker fee for market order
        fee = connector._get_fee("BTC", "USDT", OrderType.MARKET, TradeType.BUY, Decimal("1"))
        assert fee.percent == Decimal("0.001")

        # Maker fee for limit maker
        fee = connector._get_fee("BTC", "USDT", OrderType.LIMIT_MAKER, TradeType.BUY, Decimal("1"))
        assert fee.percent == Decimal("0.0005")
