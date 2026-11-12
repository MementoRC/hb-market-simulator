"""Unit tests for SimulatedConnectorCore (hummingbot-free)."""

from __future__ import annotations

from decimal import Decimal

from data_type_primitives import (
    AddedToCostTradeFee,
    OrderState,
    OrderType,
    OrderUpdate,
    TradeType,
    TradeUpdate,
)

from market_simulator.connector import SimulatedConnectorCore
from market_simulator.protocols import SimulatedVenueProtocol
from market_simulator.simulator.exchange import SimulatedExchangeConfig
from market_simulator.simulator.fee_model import FlatFeeModel, ZeroFeeModel
from market_simulator.simulator.matching_engine import ImmediateFillEngine


def _make_core(
    fee_model=None,
    matching_engine=None,
    initial_balances=None,
) -> SimulatedConnectorCore:
    config = SimulatedExchangeConfig(
        name="test_sim",
        trading_pairs=["BTC-USDT"],
        initial_balances=initial_balances or {"USDT": Decimal("100000"), "BTC": Decimal("10")},
    )
    core = SimulatedConnectorCore(
        config=config,
        matching_engine=matching_engine or ImmediateFillEngine(),
        fee_model=fee_model or ZeroFeeModel(),
    )
    book = core.sim_exchange.get_order_book("BTC-USDT")
    book.apply_snapshot(
        bids=[(Decimal("50000"), Decimal("10.0")), (Decimal("49900"), Decimal("20.0"))],
        asks=[(Decimal("50100"), Decimal("10.0")), (Decimal("50200"), Decimal("20.0"))],
    )
    core.sim_exchange.ready = True
    return core


def _collect(core: SimulatedConnectorCore) -> tuple[list[OrderUpdate], list[TradeUpdate]]:
    orders: list[OrderUpdate] = []
    trades: list[TradeUpdate] = []
    core.add_order_update_listener(orders.append)
    core.add_trade_update_listener(trades.append)
    return orders, trades


class TestProtocol:
    def test_default_instance_satisfies_protocol(self):
        assert isinstance(SimulatedConnectorCore(), SimulatedVenueProtocol)

    def test_configured_instance_satisfies_protocol(self):
        assert isinstance(_make_core(), SimulatedVenueProtocol)


class TestIdentity:
    def test_name(self):
        assert _make_core().name == "test_sim"

    def test_trading_pairs(self):
        assert _make_core().trading_pairs == ["BTC-USDT"]

    def test_supported_order_types(self):
        types = _make_core().supported_order_types()
        assert OrderType.LIMIT in types
        assert OrderType.MARKET in types
        assert OrderType.LIMIT_MAKER in types
        assert OrderType.STOP_LOSS in types


class TestBalances:
    async def test_update_balances(self):
        core = _make_core()
        await core.update_balances()
        assert core.get_balance("USDT") == Decimal("100000")
        assert core.get_balance("BTC") == Decimal("10")

    async def test_available_balances(self):
        core = _make_core()
        await core.update_balances()
        assert core.get_available_balance("USDT") == Decimal("100000")
        assert core.get_all_available_balances()["BTC"] == Decimal("10")

    def test_unknown_asset_is_zero(self):
        assert _make_core().get_balance("ETH") == Decimal("0")


class TestTradingRules:
    async def test_update_trading_rules(self):
        core = _make_core()
        await core.update_trading_rules()
        assert "BTC-USDT" in core.trading_rules


class TestOrderPlacement:
    async def test_place_order_returns_exchange_order_id(self):
        core = _make_core()
        await core.update_balances()
        await core.update_trading_rules()

        exchange_order_id = await core.place_order(
            client_order_id="test-001",
            trading_pair="BTC-USDT",
            amount=Decimal("0.1"),
            trade_type=TradeType.BUY,
            order_type=OrderType.MARKET,
            price=Decimal("0"),
        )

        assert exchange_order_id.startswith("SIMEX-")
        assert isinstance(core.sim_exchange.current_timestamp, float)

    async def test_cancel_order_by_client_id(self):
        core = _make_core()
        await core.place_order(
            "cid-1", "BTC-USDT", Decimal("0.1"), TradeType.BUY, OrderType.LIMIT, Decimal("49000")
        )
        assert (await core.request_order_status("cid-1")).new_state == OrderState.OPEN

        assert await core.cancel_order("cid-1", "BTC-USDT") is True
        assert (await core.request_order_status("cid-1")).new_state == OrderState.CANCELED

    async def test_cancel_order_by_sim_order_id(self):
        core = _make_core()
        sim_order_id = core.sim_exchange.buy(
            "BTC-USDT", Decimal("0.1"), OrderType.LIMIT, Decimal("49000")
        )
        assert await core.cancel_order(sim_order_id, "BTC-USDT") is True
        assert core.sim_exchange.get_in_flight_order(sim_order_id) is None or (
            not core.sim_exchange.get_in_flight_order(sim_order_id).is_open
        )


class TestOrderStatus:
    async def test_unknown_order_is_failed(self):
        update = await _make_core().request_order_status("nope", "BTC-USDT")
        assert update.new_state == OrderState.FAILED
        assert update.client_order_id == "nope"
        assert update.trading_pair == "BTC-USDT"

    async def test_status_carries_ids(self):
        core = _make_core()
        exchange_id = await core.place_order(
            "cid-2", "BTC-USDT", Decimal("0.1"), TradeType.BUY, OrderType.LIMIT, Decimal("49000")
        )
        update = await core.request_order_status("cid-2")
        assert update.client_order_id == "cid-2"
        assert update.exchange_order_id == exchange_id

    async def test_all_trade_updates_for_order_is_empty(self):
        assert await _make_core().all_trade_updates_for_order("x") == []


class TestFees:
    def test_zero_fee(self):
        core = _make_core(fee_model=ZeroFeeModel())
        fee = core.get_fee("BTC", "USDT", OrderType.MARKET, TradeType.BUY, Decimal("1"))
        assert isinstance(fee, AddedToCostTradeFee)
        assert fee.percent == Decimal("0")

    def test_flat_fee(self):
        core = _make_core(
            fee_model=FlatFeeModel(maker_rate=Decimal("0.0005"), taker_rate=Decimal("0.001"))
        )
        fee = core.get_fee("BTC", "USDT", OrderType.MARKET, TradeType.BUY, Decimal("1"))
        assert fee.percent == Decimal("0.001")

        fee = core.get_fee("BTC", "USDT", OrderType.LIMIT_MAKER, TradeType.BUY, Decimal("1"))
        assert fee.percent == Decimal("0.0005")

        fee = core.get_fee(
            "BTC", "USDT", OrderType.LIMIT, TradeType.BUY, Decimal("1"), is_maker=True
        )
        assert fee.percent == Decimal("0.0005")


class TestUpdates:
    async def test_market_fill_delivers_trade_and_order_updates(self):
        core = _make_core()
        orders, trades = _collect(core)

        exchange_id = await core.place_order(
            "fill-1", "BTC-USDT", Decimal("0.1"), TradeType.BUY, OrderType.MARKET, Decimal("0")
        )

        assert len(trades) == 1
        trade = trades[0]
        assert isinstance(trade, TradeUpdate)
        assert trade.client_order_id == "fill-1"
        assert trade.exchange_order_id == exchange_id
        assert trade.trading_pair == "BTC-USDT"
        assert trade.fill_base_amount == Decimal("0.1")
        assert trade.fill_quote_amount == trade.fill_base_amount * trade.fill_price
        assert isinstance(trade.fee, AddedToCostTradeFee)

        assert len(orders) == 1
        assert isinstance(orders[0], OrderUpdate)
        assert orders[0].new_state == OrderState.FILLED
        assert orders[0].client_order_id == "fill-1"
        assert orders[0].exchange_order_id == exchange_id

    async def test_limit_fill_on_tick_delivers_updates_and_syncs_balances(self):
        core = _make_core()
        orders, trades = _collect(core)

        await core.place_order(
            "fill-2", "BTC-USDT", Decimal("0.1"), TradeType.BUY, OrderType.LIMIT, Decimal("50100")
        )
        core.tick(1.0)

        assert len(trades) == 1
        assert [o.new_state for o in orders] == [OrderState.FILLED]
        assert core.get_balance("BTC") == Decimal("10.1")

    async def test_fill_of_untracked_order_is_ignored(self):
        core = _make_core()
        orders, trades = _collect(core)

        core.sim_exchange.buy("BTC-USDT", Decimal("0.1"), OrderType.MARKET, Decimal("0"))

        assert trades == []
        assert orders == []

    async def test_triggered_conditional_order_emits_canceled(self):
        core = _make_core()
        orders, _ = _collect(core)

        await core.place_order(
            "stop-1",
            "BTC-USDT",
            Decimal("0.1"),
            TradeType.SELL,
            OrderType.STOP_LOSS,
            Decimal("0"),
            trigger_price=Decimal("50000"),
        )
        core.tick(1.0)

        canceled = [o for o in orders if o.new_state == OrderState.CANCELED]
        assert len(canceled) == 1
        assert canceled[0].client_order_id == "stop-1"
