"""SimulatedConnector: ExchangePyBase adapter wrapping SimulatedExchange.

This connector extends ExchangePyBase so it can be injected into
StrategyV2Base.connectors and used by real ExecutorBase instances.
It overrides all network-dependent abstract methods to route through
the local SimulatedExchange instead.

Event emission is handled by ClientOrderTracker — we feed it TradeUpdate
and OrderUpdate objects and it fires the correct MarketEvent events.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Any

from bidict import bidict
from hummingbot.connector.exchange_py_base import ExchangePyBase
from hummingbot.connector.trading_rule import TradingRule
from hummingbot.core.data_type.common import OrderType, TradeType
from hummingbot.core.data_type.in_flight_order import (
    InFlightOrder,
    OrderState,
    OrderUpdate,
    TradeUpdate,
)
from hummingbot.core.data_type.order_book_tracker_data_source import OrderBookTrackerDataSource
from hummingbot.core.data_type.trade_fee import AddedToCostTradeFee, TokenAmount
from hummingbot.core.data_type.user_stream_tracker_data_source import UserStreamTrackerDataSource
from hummingbot.core.web_assistant.web_assistants_factory import WebAssistantsFactory

from market_simulator.core.events import MarketEvent as SimMarketEvent
from market_simulator.core.types import (
    OrderType as SimOrderType,
)
from market_simulator.core.types import (
    TradeType as SimTradeType,
)
from market_simulator.simulator.exchange import (
    OrderFilledEvent,
    SimulatedExchange,
    SimulatedExchangeConfig,
)
from market_simulator.simulator.fee_model import FeeModel, FlatFeeModel, ZeroFeeModel
from market_simulator.simulator.matching_engine import LimitOrderEngine, MatchingEngine

# ---------------------------------------------------------------------------
# Type mapping helpers
# ---------------------------------------------------------------------------

_HB_TO_SIM_ORDER_TYPE = {
    OrderType.MARKET: SimOrderType.MARKET,
    OrderType.LIMIT: SimOrderType.LIMIT,
    OrderType.LIMIT_MAKER: SimOrderType.LIMIT_MAKER,
}

_HB_TO_SIM_TRADE_TYPE = {
    TradeType.BUY: SimTradeType.BUY,
    TradeType.SELL: SimTradeType.SELL,
}


# ---------------------------------------------------------------------------
# Stub data sources (no network activity)
# ---------------------------------------------------------------------------


class _StubOrderBookDataSource(OrderBookTrackerDataSource):
    """Minimal stub — order book data comes from the SimulatedExchange."""

    def __init__(self, trading_pairs: list[str]):
        super().__init__(trading_pairs)

    async def get_last_traded_prices(
        self, trading_pairs: list[str], domain: str | None = None
    ) -> dict[str, float]:
        return {pair: 0.0 for pair in trading_pairs}

    async def get_new_order_book_dict(self, trading_pair: str) -> dict[str, Any]:
        return {"trading_pair": trading_pair, "bids": [], "asks": [], "update_id": 0}

    async def listen_for_subscriptions(self):
        await asyncio.sleep(1e9)  # Never returns

    async def listen_for_order_book_diffs(self, ev_loop, output):
        await asyncio.sleep(1e9)

    async def listen_for_order_book_snapshots(self, ev_loop, output):
        await asyncio.sleep(1e9)

    async def listen_for_trades(self, ev_loop, output):
        await asyncio.sleep(1e9)

    async def subscribe_to_trading_pair(self, trading_pair: str) -> bool:
        return True

    async def unsubscribe_from_trading_pair(self, trading_pair: str) -> bool:
        return True


class _StubUserStreamDataSource(UserStreamTrackerDataSource):
    """Minimal stub — no user stream for simulated exchange."""

    def __init__(self):
        super().__init__()

    @property
    def last_recv_time(self) -> float:
        # Return current time so is_user_stream_initialized returns True
        import time

        return time.time()

    async def listen_for_user_stream(self, output):
        await asyncio.sleep(1e9)


class _StubWebAssistantsFactory(WebAssistantsFactory):
    """Stub factory — no REST/WS calls needed."""

    def __init__(self):
        # WebAssistantsFactory normally requires throttler/auth — skip it
        pass


# ---------------------------------------------------------------------------
# SimulatedConnector
# ---------------------------------------------------------------------------


class SimulatedConnector(ExchangePyBase):
    """ExchangePyBase-compatible connector backed by SimulatedExchange.

    Usage:
        config = SimulatedExchangeConfig(
            name="sim_binance",
            trading_pairs=["BTC-USDT"],
            initial_balances={"USDT": Decimal("10000"), "BTC": Decimal("1")},
        )
        connector = SimulatedConnector(config)

    Then inject into StrategyV2Base:
        strategy.connectors["sim_binance"] = connector
    """

    def __init__(
        self,
        config: SimulatedExchangeConfig,
        matching_engine: MatchingEngine | None = None,
        fee_model: FeeModel | None = None,
    ):
        self._sim_config = config
        self._sim_exchange = SimulatedExchange(
            config=config,
            matching_engine=matching_engine or LimitOrderEngine(),
            fee_model=fee_model or FlatFeeModel(),
        )
        self._sim_trading_pairs = list(config.trading_pairs)
        self._sim_fee_model = fee_model or FlatFeeModel()
        self._exchange_order_id_counter = 0

        # Wire up sim exchange fill events to feed ClientOrderTracker
        self._sim_exchange.add_listener(SimMarketEvent.OrderFilled, self._on_sim_fill)

        # ExchangePyBase.__init__(balance_asset_limit, rate_limits_share_pct)
        # calls abstract properties immediately, so they must be ready above
        super().__init__()

    # -------------------------------------------------------------------
    # Abstract properties (required by ExchangePyBase)
    # -------------------------------------------------------------------

    @property
    def name(self) -> str:
        return self._sim_config.name

    @property
    def authenticator(self):
        return None

    @property
    def rate_limits_rules(self) -> list:
        return []

    @property
    def domain(self) -> str:
        return self._sim_config.name

    @property
    def client_order_id_max_length(self) -> int:
        return 64

    @property
    def client_order_id_prefix(self) -> str:
        return "SIM"

    @property
    def trading_rules_request_path(self) -> str:
        return ""

    @property
    def trading_pairs_request_path(self) -> str:
        return ""

    @property
    def check_network_request_path(self) -> str:
        return ""

    @property
    def trading_pairs(self) -> list[str]:
        return self._sim_trading_pairs

    @property
    def is_cancel_request_in_exchange_synchronous(self) -> bool:
        return True

    @property
    def is_trading_required(self) -> bool:
        return True

    # -------------------------------------------------------------------
    # Abstract methods (required by ExchangePyBase)
    # -------------------------------------------------------------------

    def supported_order_types(self) -> list[OrderType]:
        return [OrderType.MARKET, OrderType.LIMIT, OrderType.LIMIT_MAKER]

    def _is_request_exception_related_to_time_synchronizer(
        self, request_exception: Exception
    ) -> bool:
        return False

    def _is_order_not_found_during_status_update_error(
        self, status_update_exception: Exception
    ) -> bool:
        return False

    def _is_order_not_found_during_cancelation_error(
        self, cancelation_exception: Exception
    ) -> bool:
        return False

    def _create_web_assistants_factory(self) -> WebAssistantsFactory:
        return _StubWebAssistantsFactory()

    def _create_order_book_data_source(self) -> OrderBookTrackerDataSource:
        return _StubOrderBookDataSource(self._sim_trading_pairs)

    def _create_user_stream_data_source(self) -> UserStreamTrackerDataSource:
        return _StubUserStreamDataSource()

    async def _format_trading_rules(self, exchange_info_dict: dict[str, Any]) -> list[TradingRule]:
        return []

    def _initialize_trading_pair_symbols_from_exchange_info(self, exchange_info: dict[str, Any]):
        pass

    # -------------------------------------------------------------------
    # Order placement (routes to SimulatedExchange)
    # -------------------------------------------------------------------

    async def _place_order(
        self,
        order_id: str,
        trading_pair: str,
        amount: Decimal,
        trade_type: TradeType,
        order_type: OrderType,
        price: Decimal,
        **kwargs,
    ) -> tuple[str, float]:
        """Place order on simulated exchange, return (exchange_order_id, timestamp)."""
        self._exchange_order_id_counter += 1
        exchange_order_id = f"SIMEX-{self._exchange_order_id_counter:08d}"

        sim_order_type = _HB_TO_SIM_ORDER_TYPE[order_type]

        # Place on sim exchange (synchronous)
        if trade_type == TradeType.BUY:
            self._sim_exchange.buy(
                trading_pair,
                amount,
                sim_order_type,
                price,
            )
        else:
            self._sim_exchange.sell(
                trading_pair,
                amount,
                sim_order_type,
                price,
            )

        timestamp = self._sim_exchange.current_timestamp
        return exchange_order_id, timestamp

    async def _place_cancel(self, order_id: str, tracked_order: InFlightOrder) -> bool:
        """Cancel order on simulated exchange."""
        self._sim_exchange.cancel(tracked_order.trading_pair, order_id)
        return True

    # -------------------------------------------------------------------
    # Fee calculation
    # -------------------------------------------------------------------

    def _get_fee(
        self,
        base_currency: str,
        quote_currency: str,
        order_type: OrderType,
        order_side: TradeType,
        amount: Decimal,
        price: Decimal = Decimal("NaN"),
        is_maker: bool | None = None,
    ) -> AddedToCostTradeFee:
        """Calculate fee using the sim exchange's fee model."""
        if isinstance(self._sim_fee_model, ZeroFeeModel):
            return AddedToCostTradeFee(percent=Decimal("0"))

        if isinstance(self._sim_fee_model, FlatFeeModel):
            if is_maker or order_type == OrderType.LIMIT_MAKER:
                rate = self._sim_fee_model.maker_rate
            else:
                rate = self._sim_fee_model.taker_rate
            return AddedToCostTradeFee(percent=rate)

        # Fallback: zero fee
        return AddedToCostTradeFee(percent=Decimal("0"))

    # -------------------------------------------------------------------
    # Balance and trading rules updates (no network needed)
    # -------------------------------------------------------------------

    async def _update_balances(self):
        """Sync balances from SimulatedExchange to ConnectorBase dicts."""
        all_balances = self._sim_exchange.balance_manager.get_all_balances()
        all_available = self._sim_exchange.balance_manager.get_all_available_balances()

        local_assets = set(self._account_balances.keys())
        remote_assets = set()

        for currency, total in all_balances.items():
            self._account_balances[currency] = total
            self._account_available_balances[currency] = all_available.get(currency, Decimal("0"))
            remote_assets.add(currency)

        for stale in local_assets - remote_assets:
            del self._account_balances[stale]
            del self._account_available_balances[stale]

    async def _update_trading_rules(self):
        """Populate trading rules from SimulatedExchange config."""
        for pair, sim_rule in self._sim_exchange.trading_rules.items():
            self._trading_rules[pair] = TradingRule(
                trading_pair=pair,
                min_order_size=sim_rule.min_order_size,
                max_order_size=sim_rule.max_order_size,
                min_price_increment=sim_rule.min_price_increment,
                min_base_amount_increment=sim_rule.min_base_amount_increment,
                min_notional_size=sim_rule.min_notional_size,
            )

        # Initialize symbol map (1:1 for simulated)
        mapping = bidict({pair: pair for pair in self._trading_rules})
        self._set_trading_pair_symbol_map(mapping)

    async def _update_trading_fees(self):
        pass  # Fees are configured, not fetched

    # -------------------------------------------------------------------
    # Order status (handled by sim exchange events, no polling needed)
    # -------------------------------------------------------------------

    async def _all_trade_updates_for_order(self, order: InFlightOrder) -> list[TradeUpdate]:
        return []  # Fills are pushed via _on_sim_fill, no polling

    async def _request_order_status(self, tracked_order: InFlightOrder) -> OrderUpdate:
        """Check sim exchange for order status."""
        sim_order = self._sim_exchange.get_in_flight_order(tracked_order.client_order_id)
        if sim_order is None:
            return OrderUpdate(
                trading_pair=tracked_order.trading_pair,
                update_timestamp=self._sim_exchange.current_timestamp,
                new_state=OrderState.FAILED,
                client_order_id=tracked_order.client_order_id,
                exchange_order_id=tracked_order.exchange_order_id,
            )

        from market_simulator.core.types import OrderStatus as SimOrderStatus

        state_map = {
            SimOrderStatus.PENDING_CREATE: OrderState.PENDING_CREATE,
            SimOrderStatus.OPEN: OrderState.OPEN,
            SimOrderStatus.PARTIALLY_FILLED: OrderState.PARTIALLY_FILLED,
            SimOrderStatus.FILLED: OrderState.FILLED,
            SimOrderStatus.CANCELLED: OrderState.CANCELED,
            SimOrderStatus.FAILED: OrderState.FAILED,
        }

        return OrderUpdate(
            trading_pair=tracked_order.trading_pair,
            update_timestamp=self._sim_exchange.current_timestamp,
            new_state=state_map.get(sim_order.status, OrderState.OPEN),
            client_order_id=tracked_order.client_order_id,
            exchange_order_id=tracked_order.exchange_order_id,
        )

    async def _user_stream_event_listener(self):
        """No user stream for simulated exchange."""
        await asyncio.sleep(1e9)

    # -------------------------------------------------------------------
    # Simulation tick
    # -------------------------------------------------------------------

    def tick(self, timestamp: float):
        """Advance simulation and process fills."""
        super().tick(timestamp)
        # Advance sim exchange and match orders
        self._sim_exchange.process_tick(timestamp)
        # Sync balances after fills
        asyncio.ensure_future(self._update_balances())

    # -------------------------------------------------------------------
    # Sim exchange fill event handler
    # -------------------------------------------------------------------

    def _on_sim_fill(self, fill_event: OrderFilledEvent) -> None:
        """Called when SimulatedExchange fills an order.

        Feeds TradeUpdate to ClientOrderTracker which fires the correct
        MarketEvent events on this connector.
        """
        tracked_order = self._order_tracker.fetch_tracked_order(fill_event.order_id)
        if tracked_order is None:
            return

        fee = AddedToCostTradeFee(
            percent=fill_event.trade_fee.percent if fill_event.trade_fee else Decimal("0"),
            flat_fees=[
                TokenAmount(token=currency, amount=amount)
                for currency, amount in (
                    fill_event.trade_fee.flat_fees if fill_event.trade_fee else []
                )
            ],
        )

        trade_update = TradeUpdate(
            trade_id=fill_event.exchange_trade_id,
            client_order_id=fill_event.order_id,
            exchange_order_id=tracked_order.exchange_order_id or "",
            trading_pair=fill_event.trading_pair,
            fill_timestamp=fill_event.timestamp,
            fill_price=fill_event.fill_price,
            fill_base_amount=fill_event.fill_amount,
            fill_quote_amount=fill_event.fill_amount * fill_event.fill_price,
            fee=fee,
        )
        self._order_tracker.process_trade_update(trade_update)

        # Check if order is now fully filled
        sim_order = self._sim_exchange.get_in_flight_order(fill_event.order_id)
        if sim_order and sim_order.is_done:
            from market_simulator.core.types import OrderStatus as SimOrderStatus

            state = (
                OrderState.FILLED
                if sim_order.status == SimOrderStatus.FILLED
                else OrderState.CANCELED
            )
            order_update = OrderUpdate(
                trading_pair=fill_event.trading_pair,
                update_timestamp=fill_event.timestamp,
                new_state=state,
                client_order_id=fill_event.order_id,
                exchange_order_id=tracked_order.exchange_order_id or "",
            )
            self._order_tracker.process_order_update(order_update)

    # -------------------------------------------------------------------
    # Access to underlying sim exchange
    # -------------------------------------------------------------------

    @property
    def sim_exchange(self) -> SimulatedExchange:
        """Direct access to the underlying SimulatedExchange."""
        return self._sim_exchange
