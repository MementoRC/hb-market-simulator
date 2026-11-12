"""SimulatedConnectorCore: L0 connector core backed by SimulatedExchange.

Implements SimulatedVenueProtocol with no hummingbot imports. It owns the
client-order-id to simulator-order bookkeeping and translates simulator fills and
triggers into hb-data-type-primitives OrderUpdate/TradeUpdate objects, delivered
to registered listeners. An outside adapter (the parent repo's ExchangePyBase shim)
registers listeners and feeds the updates into its own order tracker.
"""

from __future__ import annotations

import logging
from collections import OrderedDict
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from data_type_primitives import (
    AddedToCostTradeFee,
    OrderState,
    OrderType,
    OrderUpdate,
    TokenAmount,
    TradeType,
    TradeUpdate,
)

from market_simulator.core.events import MarketEvent, SimulatorEvent
from market_simulator.core.types import OrderStatus, TradingRule
from market_simulator.protocols.venue import OrderUpdateListener, TradeUpdateListener
from market_simulator.simulator.exchange import (
    OrderCancelledEvent,
    OrderFailureEvent,
    OrderFilledEvent,
    OrderTriggeredEvent,
    SimulatedExchange,
    SimulatedExchangeConfig,
)
from market_simulator.simulator.fee_model import FeeModel, FlatFeeModel, ZeroFeeModel
from market_simulator.simulator.matching_engine import LimitOrderEngine, MatchingEngine

logger = logging.getLogger(__name__)

_FINISHED_HISTORY = 1024

_SUPPORTED_ORDER_TYPES: tuple[OrderType, ...] = (
    OrderType.MARKET,
    OrderType.LIMIT,
    OrderType.LIMIT_MAKER,
    OrderType.STOP_LOSS,
    OrderType.TAKE_PROFIT,
    OrderType.TRAILING_STOP,
)

# SimulatedExchange OrderStatus -> DTP OrderState (kept as-is; see #59).
_STATE_MAP: dict[OrderStatus, OrderState] = {
    OrderStatus.PENDING_CREATE: OrderState.PENDING_CREATE,
    OrderStatus.OPEN: OrderState.OPEN,
    OrderStatus.PARTIALLY_FILLED: OrderState.PARTIALLY_FILLED,
    OrderStatus.FILLED: OrderState.FILLED,
    OrderStatus.CANCELLED: OrderState.CANCELED,
    OrderStatus.FAILED: OrderState.FAILED,
}


@dataclass
class _TrackedOrder:
    client_order_id: str
    sim_order_id: str
    trading_pair: str
    exchange_order_id: str


class SimulatedConnectorCore:
    """Venue core wrapping a SimulatedExchange (see SimulatedVenueProtocol)."""

    def __init__(
        self,
        config: SimulatedExchangeConfig | None = None,
        matching_engine: MatchingEngine | None = None,
        fee_model: FeeModel | None = None,
    ) -> None:
        self._config = config or SimulatedExchangeConfig()
        self._fee_model: FeeModel = fee_model or FlatFeeModel()
        self._sim_exchange = SimulatedExchange(
            config=self._config,
            matching_engine=matching_engine or LimitOrderEngine(),
            fee_model=self._fee_model,
        )
        self._trading_pairs = list(self._config.trading_pairs)
        self._exchange_order_id_counter = 0

        # client_order_id -> tracked order, and sim order id -> client_order_id
        self._orders: dict[str, _TrackedOrder] = {}
        self._sim_to_client: dict[str, str] = {}
        self._placing: _TrackedOrder | None = None
        # Bounded history of terminal updates, for request_order_status on finished orders
        self._finished: OrderedDict[str, OrderUpdate] = OrderedDict()

        self._order_listeners: list[OrderUpdateListener] = []
        self._trade_listeners: list[TradeUpdateListener] = []

        self._balances: dict[str, Decimal] = {}
        self._available_balances: dict[str, Decimal] = {}
        self._trading_rules: dict[str, TradingRule] = {}
        self._sync_balances()
        self._sync_trading_rules()

        self._sim_exchange.add_listener(MarketEvent.OrderFilled, self._on_sim_fill)
        self._sim_exchange.add_listener(SimulatorEvent.OrderTriggered, self._on_sim_triggered)
        self._sim_exchange.add_listener(MarketEvent.OrderCancelled, self._on_sim_cancelled)
        self._sim_exchange.add_listener(MarketEvent.OrderFailure, self._on_sim_failed)

    # -------------------------------------------------------------------
    # Identity
    # -------------------------------------------------------------------

    @property
    def name(self) -> str:
        return self._config.name

    @property
    def trading_pairs(self) -> list[str]:
        return self._trading_pairs

    def supported_order_types(self) -> list[OrderType]:
        return list(_SUPPORTED_ORDER_TYPES)

    @property
    def sim_exchange(self) -> SimulatedExchange:
        """Direct access to the underlying SimulatedExchange."""
        return self._sim_exchange

    # -------------------------------------------------------------------
    # Balances and trading rules
    # -------------------------------------------------------------------

    def get_balance(self, asset: str) -> Decimal:
        return self._balances.get(asset, Decimal("0"))

    def get_available_balance(self, asset: str) -> Decimal:
        return self._available_balances.get(asset, Decimal("0"))

    def get_all_balances(self) -> dict[str, Decimal]:
        return dict(self._balances)

    def get_all_available_balances(self) -> dict[str, Decimal]:
        return dict(self._available_balances)

    @property
    def trading_rules(self) -> dict[str, TradingRule]:
        return self._trading_rules

    def _sync_balances(self) -> None:
        manager = self._sim_exchange.balance_manager
        available = manager.get_all_available_balances()
        self._balances = dict(manager.get_all_balances())
        self._available_balances = {
            currency: available.get(currency, Decimal("0")) for currency in self._balances
        }

    def _sync_trading_rules(self) -> None:
        self._trading_rules = dict(self._sim_exchange.trading_rules)

    async def update_balances(self) -> None:
        """Refresh the balance snapshot from the SimulatedExchange."""
        self._sync_balances()

    async def update_trading_rules(self) -> None:
        """Refresh trading rules from the SimulatedExchange config."""
        self._sync_trading_rules()

    async def update_trading_fees(self) -> None:
        """No-op: fees are configured, not fetched."""

    # -------------------------------------------------------------------
    # Fees
    # -------------------------------------------------------------------

    def get_fee(
        self,
        base_currency: str,
        quote_currency: str,
        order_type: OrderType,
        order_side: TradeType,
        amount: Decimal,
        price: Decimal = Decimal("NaN"),
        is_maker: bool | None = None,
    ) -> AddedToCostTradeFee:
        """Calculate the expected fee using the simulator's fee model."""
        if isinstance(self._fee_model, ZeroFeeModel):
            return AddedToCostTradeFee(percent=Decimal("0"))

        if isinstance(self._fee_model, FlatFeeModel):
            if is_maker or order_type == OrderType.LIMIT_MAKER:
                rate = self._fee_model.maker_rate
            else:
                rate = self._fee_model.taker_rate
            return AddedToCostTradeFee(percent=rate)

        # Fallback: zero fee
        return AddedToCostTradeFee(percent=Decimal("0"))

    # -------------------------------------------------------------------
    # Orders
    # -------------------------------------------------------------------

    async def place_order(
        self,
        client_order_id: str,
        trading_pair: str,
        amount: Decimal,
        trade_type: TradeType,
        order_type: OrderType,
        price: Decimal,
        **kwargs: Any,
    ) -> str:
        """Place an order on the simulated exchange; return the exchange order id.

        The placement timestamp is ``sim_exchange.current_timestamp``.
        """
        self._exchange_order_id_counter += 1
        exchange_order_id = f"SIMEX-{self._exchange_order_id_counter:08d}"

        # Conditional order parameters forwarded from strategy callers.
        trigger_price: Decimal | None = kwargs.get("trigger_price")
        trail_amount: Decimal | None = kwargs.get("trail_amount")

        # Register before placing: MARKET orders fill inside buy()/sell(), before the
        # simulator order id is known, so fill handlers bind it via _placing.
        tracked = _TrackedOrder(client_order_id, "", trading_pair, exchange_order_id)
        self._orders[client_order_id] = tracked
        self._placing = tracked
        try:
            place = (
                self._sim_exchange.buy if trade_type == TradeType.BUY else self._sim_exchange.sell
            )
            sim_order_id = place(
                trading_pair,
                amount,
                order_type,
                price,
                trigger_price=trigger_price,
                trail_amount=trail_amount,
            )
        except Exception:
            self._orders.pop(client_order_id, None)
            if tracked.sim_order_id:
                self._sim_to_client.pop(tracked.sim_order_id, None)
            raise
        finally:
            self._placing = None

        # An order that already finished during placement (instant fill/failure) has been
        # released by _finish; only live orders keep their id mapping.
        if client_order_id in self._orders:
            tracked.sim_order_id = sim_order_id
            self._sim_to_client[sim_order_id] = client_order_id
        return exchange_order_id

    def _resolve(self, sim_order_id: str) -> _TrackedOrder | None:
        """Map a simulator order id to its tracked order (binding one mid-placement)."""
        client_id = self._sim_to_client.get(sim_order_id)
        if client_id is not None:
            return self._orders.get(client_id)
        placing = self._placing
        if placing is not None and not placing.sim_order_id:
            placing.sim_order_id = sim_order_id
            self._sim_to_client[sim_order_id] = placing.client_order_id
            return placing
        return None

    async def cancel_order(self, client_order_id: str, trading_pair: str) -> bool:
        """Cancel an open order placed through place_order.

        Returns False for unknown ids and for orders that are already finished. On
        success the simulator's OrderCancelled event delivers OrderUpdate(CANCELED).
        """
        tracked = self._orders.get(client_order_id)
        if tracked is None:
            return False
        sim_order = self._sim_exchange.get_in_flight_order(tracked.sim_order_id)
        if sim_order is None or not sim_order.is_open:
            return False
        self._sim_exchange.cancel(tracked.trading_pair, tracked.sim_order_id)
        return True

    async def request_order_status(
        self, client_order_id: str, trading_pair: str = ""
    ) -> OrderUpdate:
        """Report the current state of an order.

        - Live order: the simulator's current state.
        - Finished order (FILLED/CANCELED/FAILED): its recorded terminal update, kept in
          a bounded history of the last ``_FINISHED_HISTORY`` orders.
        - Unknown order (never placed here, or evicted from the history): FAILED,
          carrying the given ``trading_pair`` and no exchange order id.
        """
        tracked = self._orders.get(client_order_id)
        if tracked is not None:
            sim_order = self._sim_exchange.get_in_flight_order(tracked.sim_order_id)
            # see #59: PENDING_CANCEL (unmapped) falls back to OPEN
            new_state = (
                OrderState.FAILED
                if sim_order is None
                else _STATE_MAP.get(sim_order.status, OrderState.OPEN)
            )
            return OrderUpdate(
                trading_pair=tracked.trading_pair,
                update_timestamp=self._sim_exchange.current_timestamp,
                new_state=new_state,
                client_order_id=client_order_id,
                exchange_order_id=tracked.exchange_order_id,
            )

        finished = self._finished.get(client_order_id)
        if finished is not None:
            return finished

        return OrderUpdate(
            trading_pair=trading_pair,
            update_timestamp=self._sim_exchange.current_timestamp,
            new_state=OrderState.FAILED,
            client_order_id=client_order_id,
            exchange_order_id=None,
        )

    async def all_trade_updates_for_order(self, client_order_id: str) -> list[TradeUpdate]:
        return []  # Fills are pushed to trade listeners, no polling

    # -------------------------------------------------------------------
    # Listeners
    # -------------------------------------------------------------------

    def add_order_update_listener(self, listener: OrderUpdateListener) -> None:
        self._order_listeners.append(listener)

    def add_trade_update_listener(self, listener: TradeUpdateListener) -> None:
        self._trade_listeners.append(listener)

    def _emit_order_update(self, update: OrderUpdate) -> None:
        for listener in list(self._order_listeners):
            try:
                listener(update)
            except Exception:
                logger.exception("Order update listener failed for %s", update.client_order_id)

    def _emit_trade_update(self, update: TradeUpdate) -> None:
        for listener in list(self._trade_listeners):
            try:
                listener(update)
            except Exception:
                logger.exception("Trade update listener failed for %s", update.client_order_id)

    def _finish(self, tracked: _TrackedOrder, state: OrderState, timestamp: float) -> None:
        """Record a terminal state, release the id bookkeeping, then notify listeners."""
        update = OrderUpdate(
            trading_pair=tracked.trading_pair,
            update_timestamp=timestamp,
            new_state=state,
            client_order_id=tracked.client_order_id,
            exchange_order_id=tracked.exchange_order_id,
        )
        self._orders.pop(tracked.client_order_id, None)
        self._sim_to_client.pop(tracked.sim_order_id, None)
        self._finished[tracked.client_order_id] = update
        while len(self._finished) > _FINISHED_HISTORY:
            self._finished.popitem(last=False)
        self._emit_order_update(update)

    # -------------------------------------------------------------------
    # Simulation tick
    # -------------------------------------------------------------------

    def tick(self, timestamp: float) -> None:
        """Advance the simulation, process fills, and refresh balances."""
        self._sim_exchange.process_tick(timestamp)
        self._sync_balances()

    # -------------------------------------------------------------------
    # Simulator event handlers
    # -------------------------------------------------------------------

    def _on_sim_fill(self, fill_event: OrderFilledEvent) -> None:
        """Translate a simulator fill into TradeUpdate (+ terminal OrderUpdate)."""
        tracked = self._resolve(fill_event.order_id)
        if tracked is None:
            return

        trade_fee = fill_event.trade_fee
        fee = AddedToCostTradeFee(
            percent=trade_fee.percent if trade_fee else Decimal("0"),
            flat_fees=[
                TokenAmount(token=currency, amount=amount)
                for currency, amount in (trade_fee.flat_fees if trade_fee else [])
            ],
        )

        self._emit_trade_update(
            TradeUpdate(
                trade_id=fill_event.exchange_trade_id,
                client_order_id=tracked.client_order_id,
                exchange_order_id=tracked.exchange_order_id,
                trading_pair=fill_event.trading_pair,
                fill_timestamp=fill_event.timestamp,
                fill_price=fill_event.fill_price,
                fill_base_amount=fill_event.fill_amount,
                fill_quote_amount=fill_event.fill_amount * fill_event.fill_price,
                fee=fee,
            )
        )

        sim_order = self._sim_exchange.get_in_flight_order(fill_event.order_id)
        if sim_order and sim_order.is_done:
            state = (
                OrderState.FILLED if sim_order.status == OrderStatus.FILLED else OrderState.CANCELED
            )
            self._finish(tracked, state, fill_event.timestamp)

    def _on_sim_triggered(self, triggered_event: OrderTriggeredEvent) -> None:
        """Emit OrderUpdate(CANCELED) for a conditional order that has fired.

        The simulator has already cancelled the conditional order and placed a new
        MARKET order internally.
        """
        tracked = self._resolve(triggered_event.original_order_id)
        if tracked is None:
            return

        self._finish(tracked, OrderState.CANCELED, triggered_event.timestamp)

    def _on_sim_cancelled(self, cancelled_event: OrderCancelledEvent) -> None:
        """Emit OrderUpdate(CANCELED) for a cancelled tracked order."""
        tracked = self._resolve(cancelled_event.order_id)
        if tracked is not None:
            self._finish(tracked, OrderState.CANCELED, cancelled_event.timestamp)

    def _on_sim_failed(self, failure_event: OrderFailureEvent) -> None:
        """Emit OrderUpdate(FAILED) for a tracked order the simulator rejected."""
        tracked = self._resolve(failure_event.order_id)
        if tracked is not None:
            self._finish(tracked, OrderState.FAILED, failure_event.timestamp)
