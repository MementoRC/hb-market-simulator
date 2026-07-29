"""SimulatedExchange: the central component that coordinates order management,
matching, balance tracking, and event emission.

This is the pure-Python simulation engine. It has no hummingbot imports.
The hb_compat layer wraps it as a ConnectorBase-compatible object.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from market_simulator.core.events import MarketEvent
from market_simulator.core.types import (
    InFlightOrder,
    OrderStatus,
    OrderType,
    PositionAction,
    PriceType,
    TradeFee,
    TradeType,
    TradingRule,
)
from market_simulator.hb_compat.event_bus_adapter import EventBusAdapter
from market_simulator.simulator.balance_manager import BalanceManager
from market_simulator.simulator.fee_model import FeeModel, FlatFeeModel
from market_simulator.simulator.matching_engine import LimitOrderEngine, MatchingEngine
from market_simulator.simulator.order_book import SimulatedOrderBook
from market_simulator.simulator.order_tracker import OrderTracker
from market_simulator.simulator.trigger_engine import (
    StandardTriggerEngine,
    TriggerEngine,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Event payload dataclasses (mirrors hummingbot event classes)
# ---------------------------------------------------------------------------


@dataclass
class OrderCreatedEvent:
    timestamp: float
    order_id: str
    trading_pair: str
    order_type: OrderType
    trade_type: TradeType
    amount: Decimal
    price: Decimal


@dataclass
class OrderFilledEvent:
    timestamp: float
    order_id: str
    trading_pair: str
    trade_type: TradeType
    order_type: OrderType
    fill_price: Decimal
    fill_amount: Decimal
    trade_fee: TradeFee
    exchange_trade_id: str = ""


@dataclass
class OrderCompletedEvent:
    timestamp: float
    order_id: str
    trading_pair: str
    order_type: OrderType
    trade_type: TradeType
    base_amount: Decimal
    quote_amount: Decimal
    fee_amount: Decimal
    fee_currency: str


@dataclass
class OrderCancelledEvent:
    timestamp: float
    order_id: str
    trading_pair: str


@dataclass
class OrderFailureEvent:
    timestamp: float
    order_id: str
    order_type: OrderType


@dataclass
class OrderTriggeredEvent:
    timestamp: float
    original_order_id: str
    trading_pair: str
    order_type: OrderType
    trade_type: TradeType
    trigger_price: Decimal | None


# ---------------------------------------------------------------------------
# Exchange configuration
# ---------------------------------------------------------------------------


@dataclass
class SimulatedExchangeConfig:
    name: str = "simulated_exchange"
    trading_pairs: list[str] = field(default_factory=list)
    trading_rules: dict[str, TradingRule] = field(default_factory=dict)
    initial_balances: dict[str, Decimal] = field(default_factory=dict)
    is_perpetual: bool = False


# ---------------------------------------------------------------------------
# SimulatedExchange
# ---------------------------------------------------------------------------


class SimulatedExchange:
    """Pure-Python simulated exchange.

    Coordinates:
      - SimulatedOrderBook (per trading pair)
      - BalanceManager (collateral locking)
      - MatchingEngine (pluggable fill strategy)
      - OrderTracker (in-flight order management)
      - FeeModel (trade cost calculation)
      - EventBus (event emission)
    """

    def __init__(
        self,
        config: SimulatedExchangeConfig | None = None,
        matching_engine: MatchingEngine | None = None,
        fee_model: FeeModel | None = None,
        trigger_engine: TriggerEngine | None = None,
    ) -> None:
        self._config = config or SimulatedExchangeConfig()
        self._name = self._config.name
        self._trading_pairs = list(self._config.trading_pairs)

        # Components
        self._order_books: dict[str, SimulatedOrderBook] = {}
        self._balance_manager = BalanceManager()
        self._matching_engine = matching_engine or LimitOrderEngine()
        self._order_tracker = OrderTracker()
        self._fee_model = fee_model or FlatFeeModel()
        self._event_bus = EventBusAdapter()
        self._trigger_engine: TriggerEngine = trigger_engine or StandardTriggerEngine()

        # Trading rules
        self._trading_rules: dict[str, TradingRule] = dict(self._config.trading_rules)

        # State
        self._current_timestamp: float = 0.0
        self._ready = False
        self._trade_counter = 0

        # Initialize
        self._initialize()

    def _initialize(self) -> None:
        """Set up order books and balances from config."""
        for pair in self._trading_pairs:
            self._order_books[pair] = SimulatedOrderBook(pair)
            if pair not in self._trading_rules:
                self._trading_rules[pair] = TradingRule(trading_pair=pair)

        if self._config.initial_balances:
            self._balance_manager.set_initial_balances(self._config.initial_balances)

    # -------------------------------------------------------------------
    # Properties
    # -------------------------------------------------------------------

    @property
    def name(self) -> str:
        return self._name

    @property
    def trading_pairs(self) -> list[str]:
        return self._trading_pairs

    @property
    def ready(self) -> bool:
        return self._ready

    @ready.setter
    def ready(self, value: bool) -> None:
        self._ready = value

    @property
    def trading_rules(self) -> dict[str, TradingRule]:
        return self._trading_rules

    @property
    def in_flight_orders(self) -> dict[str, InFlightOrder]:
        return self._order_tracker.in_flight_orders

    @property
    def event_bus(self) -> EventBusAdapter:
        return self._event_bus

    @property
    def balance_manager(self) -> BalanceManager:
        return self._balance_manager

    @property
    def order_tracker(self) -> OrderTracker:
        return self._order_tracker

    @property
    def current_timestamp(self) -> float:
        return self._current_timestamp

    # -------------------------------------------------------------------
    # Market data
    # -------------------------------------------------------------------

    def get_order_book(self, trading_pair: str) -> SimulatedOrderBook:
        if trading_pair not in self._order_books:
            raise ValueError(f"Unknown trading pair: {trading_pair}")
        return self._order_books[trading_pair]

    def get_price_by_type(self, trading_pair: str, price_type: PriceType) -> Decimal:
        return self.get_order_book(trading_pair).get_price_by_type(price_type)

    def quantize_order_price(self, trading_pair: str, price: Decimal) -> Decimal:
        rule = self._trading_rules.get(trading_pair)
        if rule:
            return rule.quantize_order_price(price)
        return price

    def quantize_order_amount(self, trading_pair: str, amount: Decimal) -> Decimal:
        rule = self._trading_rules.get(trading_pair)
        if rule:
            return rule.quantize_order_amount(amount)
        return amount

    # -------------------------------------------------------------------
    # Balance
    # -------------------------------------------------------------------

    def get_balance(self, currency: str) -> Decimal:
        return self._balance_manager.get_balance(currency)

    def get_available_balance(self, currency: str) -> Decimal:
        return self._balance_manager.get_available_balance(currency)

    def get_all_balances(self) -> dict[str, Decimal]:
        return self._balance_manager.get_all_balances()

    def set_initial_balances(self, balances: dict[str, Decimal]) -> None:
        self._balance_manager.set_initial_balances(balances)

    # -------------------------------------------------------------------
    # Event delegation
    # -------------------------------------------------------------------

    def add_listener(self, event_tag: int, listener: Callable) -> None:
        self._event_bus.add_listener(event_tag, listener)

    def remove_listener(self, event_tag: int, listener: Callable) -> None:
        self._event_bus.remove_listener(event_tag, listener)

    def trigger_event(self, event_tag: int, message: Any) -> None:
        self._event_bus.trigger_event(event_tag, message)

    # -------------------------------------------------------------------
    # Order placement
    # -------------------------------------------------------------------

    def buy(
        self,
        trading_pair: str,
        amount: Decimal,
        order_type: OrderType,
        price: Decimal,
        position_action: PositionAction = PositionAction.NIL,
        trigger_price: Decimal | None = None,
        trail_amount: Decimal | None = None,
        **kwargs: Any,
    ) -> str:
        return self._place_order(
            trading_pair,
            TradeType.BUY,
            order_type,
            amount,
            price,
            position_action,
            trigger_price=trigger_price,
            trail_amount=trail_amount,
        )

    def sell(
        self,
        trading_pair: str,
        amount: Decimal,
        order_type: OrderType,
        price: Decimal,
        position_action: PositionAction = PositionAction.NIL,
        trigger_price: Decimal | None = None,
        trail_amount: Decimal | None = None,
        **kwargs: Any,
    ) -> str:
        return self._place_order(
            trading_pair,
            TradeType.SELL,
            order_type,
            amount,
            price,
            position_action,
            trigger_price=trigger_price,
            trail_amount=trail_amount,
        )

    def cancel(self, trading_pair: str, client_order_id: str) -> None:
        """Cancel an open order."""
        order = self._order_tracker.cancel_order(client_order_id, timestamp=self._current_timestamp)
        if order is None:
            logger.warning("Cannot cancel order %s: not found or already done", client_order_id)
            return

        # Release locked collateral.
        # For conditional BUY orders the collateral was locked at trigger_price,
        # so we must release the same amount.
        if order.trade_type == TradeType.BUY:
            locked_currency = order.quote_asset
            collateral_price = (
                order.trigger_price
                if (order.order_type.is_conditional_type() and order.trigger_price is not None)
                else order.price
            )
            locked_amount = order.remaining_amount * collateral_price
        else:
            locked_currency = order.base_asset
            locked_amount = order.remaining_amount

        self._balance_manager.apply_cancel(locked_currency, locked_amount)

        # Clean up any trailing stop reference for cancelled conditional orders.
        if order.order_type.is_conditional_type() and isinstance(
            self._trigger_engine, StandardTriggerEngine
        ):
            self._trigger_engine.clear_trailing_reference(client_order_id)

        self._event_bus.trigger_event(
            MarketEvent.OrderCancelled,
            OrderCancelledEvent(
                timestamp=self._current_timestamp,
                order_id=client_order_id,
                trading_pair=trading_pair,
            ),
        )

    def get_in_flight_order(self, client_order_id: str) -> InFlightOrder | None:
        return self._order_tracker.get_order(client_order_id)

    # -------------------------------------------------------------------
    # Tick processing
    # -------------------------------------------------------------------

    def process_tick(self, timestamp: float) -> None:
        """Advance the exchange to the given timestamp.

        Processing order:
        1. Update current timestamp.
        2. Evaluate all open conditional orders via TriggerEngine; fire any
           that have met their trigger condition (cancel + spawn market order).
        3. Match remaining open non-conditional orders via the MatchingEngine.
        """
        self._current_timestamp = timestamp

        # Step 2: Evaluate conditional orders.
        # Snapshot the list to avoid mutation issues while iterating.
        for order in list(self._order_tracker.conditional_orders):
            order_book = self._order_books.get(order.trading_pair)
            if order_book is None:
                continue

            snapshot = self._build_order_book_snapshot(order_book)
            if self._trigger_engine.evaluate(order, snapshot, timestamp):
                self._fire_conditional_order(order, timestamp)

        # Step 3: Match non-conditional open orders.
        for order in list(self._order_tracker.non_conditional_orders):
            order_book = self._order_books.get(order.trading_pair)
            if order_book is None:
                continue

            result = self._matching_engine.check_and_match(order, order_book)
            if result is None:
                continue

            self._process_fill(order, result.fill_price, result.fill_amount)

    # -------------------------------------------------------------------
    # Internal methods
    # -------------------------------------------------------------------

    def _place_order(
        self,
        trading_pair: str,
        trade_type: TradeType,
        order_type: OrderType,
        amount: Decimal,
        price: Decimal,
        position_action: PositionAction = PositionAction.NIL,
        trigger_price: Decimal | None = None,
        trail_amount: Decimal | None = None,
    ) -> str:
        """Internal order placement with validation, collateral locking, and events."""
        # Quantize
        rule = self._trading_rules.get(trading_pair)
        if rule:
            amount = rule.quantize_order_amount(amount)
            if order_type != OrderType.MARKET:
                price = rule.quantize_order_price(price)
            if trigger_price is not None:
                trigger_price = rule.quantize_order_price(trigger_price)

        # Create tracked order
        order = self._order_tracker.create_order(
            trading_pair=trading_pair,
            order_type=order_type,
            trade_type=trade_type,
            amount=amount,
            price=price,
            timestamp=self._current_timestamp,
            position_action=position_action,
            trigger_price=trigger_price,
            trail_amount=trail_amount,
        )

        # Lock collateral.
        # For conditional BUY orders, lock at trigger_price * amount when a
        # trigger_price is set (worst-case execution price); otherwise fall back
        # to the order price.  SELL orders always lock the base amount.
        if trade_type == TradeType.BUY:
            lock_currency = trading_pair.split("-")[1]
            collateral_price = (
                trigger_price
                if (order_type.is_conditional_type() and trigger_price is not None)
                else price
            )
            lock_amount = amount * collateral_price
        else:
            lock_currency = trading_pair.split("-")[0]
            lock_amount = amount

        if not self._balance_manager.lock_collateral(lock_currency, lock_amount):
            self._order_tracker.fail_order(order.client_order_id, timestamp=self._current_timestamp)
            self._event_bus.trigger_event(
                MarketEvent.OrderFailure,
                OrderFailureEvent(
                    timestamp=self._current_timestamp,
                    order_id=order.client_order_id,
                    order_type=order_type,
                ),
            )
            return order.client_order_id

        # Transition to OPEN and emit created event
        self._order_tracker.open_order(order.client_order_id, timestamp=self._current_timestamp)

        event_tag = (
            MarketEvent.BuyOrderCreated
            if trade_type == TradeType.BUY
            else MarketEvent.SellOrderCreated
        )
        self._event_bus.trigger_event(
            event_tag,
            OrderCreatedEvent(
                timestamp=self._current_timestamp,
                order_id=order.client_order_id,
                trading_pair=trading_pair,
                order_type=order_type,
                trade_type=trade_type,
                amount=amount,
                price=price,
            ),
        )

        # For market orders, attempt immediate matching
        if order_type == OrderType.MARKET:
            order_book = self._order_books.get(trading_pair)
            if order_book:
                result = self._matching_engine.check_and_match(order, order_book)
                if result:
                    self._process_fill(order, result.fill_price, result.fill_amount)

        return order.client_order_id

    def _build_order_book_snapshot(
        self, order_book: SimulatedOrderBook
    ) -> dict[str, Decimal | None]:
        """Build a snapshot dict suitable for TriggerEngine.evaluate().

        :param order_book: The SimulatedOrderBook for the trading pair.
        :return: Dict with keys best_bid, best_ask, last_trade.
        """
        return {
            "best_bid": order_book.best_bid,
            "best_ask": order_book.best_ask,
            "last_trade": order_book.last_trade_price,
        }

    def _fire_conditional_order(self, order: InFlightOrder, timestamp: float) -> None:
        """Fire a triggered conditional order.

        Steps:
        1. Cancel the conditional order (updates status, releases collateral).
        2. Emit OrderTriggered event.
        3. Place a new MARKET order with the same trading_pair, amount, and
           trade_type — the new order gets a fresh order ID.

        :param order: The conditional order whose trigger condition is met.
        :param timestamp: Current simulation timestamp.
        """
        original_order_id = order.client_order_id
        trading_pair = order.trading_pair

        # Step 1: Cancel the conditional order and release its locked collateral.
        # Re-use the public cancel() path which handles collateral release and
        # emits OrderCancelled.  However, we do NOT want an OrderCancelled event
        # here — instead we emit OrderTriggered.  So we cancel at the tracker
        # level directly and release collateral manually.
        cancelled = self._order_tracker.cancel_order(original_order_id, timestamp=timestamp)
        if cancelled is None:
            # Already gone (race condition or double evaluation) — skip.
            logger.warning(
                "Attempted to fire conditional order %s but it was no longer open",
                original_order_id,
            )
            return

        # Release locked collateral for the cancelled conditional order.
        if order.trade_type == TradeType.BUY:
            locked_currency = order.quote_asset
            collateral_price = (
                order.trigger_price
                if (order.order_type.is_conditional_type() and order.trigger_price is not None)
                else order.price
            )
            locked_amount = order.remaining_amount * collateral_price
        else:
            locked_currency = order.base_asset
            locked_amount = order.remaining_amount

        self._balance_manager.apply_cancel(locked_currency, locked_amount)

        # Clean up trailing reference if the trigger engine supports it.
        if isinstance(self._trigger_engine, StandardTriggerEngine):
            self._trigger_engine.clear_trailing_reference(original_order_id)

        # Step 2: Emit OrderTriggered event.
        self._event_bus.trigger_event(
            MarketEvent.OrderTriggered,
            OrderTriggeredEvent(
                timestamp=timestamp,
                original_order_id=original_order_id,
                trading_pair=trading_pair,
                order_type=order.order_type,
                trade_type=order.trade_type,
                trigger_price=order.trigger_price,
            ),
        )

        # Step 3: Place a new MARKET order for the same pair/amount/direction.
        if order.trade_type == TradeType.BUY:
            self.buy(
                trading_pair=trading_pair,
                amount=order.amount,
                order_type=OrderType.MARKET,
                price=Decimal("0"),  # price unused for MARKET; collateral locked at ask
            )
        else:
            self.sell(
                trading_pair=trading_pair,
                amount=order.amount,
                order_type=OrderType.MARKET,
                price=Decimal("0"),
            )

        logger.debug(
            "Fired conditional order %s (%s %s %s) → new MARKET order placed",
            original_order_id,
            order.order_type.name,
            order.trade_type.name,
            trading_pair,
        )

    def _process_fill(
        self,
        order: InFlightOrder,
        fill_price: Decimal,
        fill_amount: Decimal,
    ) -> None:
        """Process a fill: update balance, order state, emit events."""
        # Calculate fee
        fee = self._fee_model.calculate_fee(
            order.trading_pair,
            order.trade_type,
            order.order_type,
            fill_amount,
            fill_price,
        )
        fee_amount = fee.total_flat_fee
        fee_currency = order.quote_asset  # Default to quote

        if fee.flat_fees:
            fee_currency = fee.flat_fees[0][0]

        # Apply fill to balance
        self._balance_manager.apply_fill(
            base_currency=order.base_asset,
            quote_currency=order.quote_asset,
            amount=fill_amount,
            price=fill_price,
            fee_amount=fee_amount,
            fee_currency=fee_currency,
            is_buy=order.trade_type == TradeType.BUY,
        )

        # Update order state
        order.update_with_fill(fill_price, fill_amount, fee_amount)
        order.last_update_timestamp = self._current_timestamp

        # Generate trade ID
        self._trade_counter += 1
        trade_id = f"TRADE-{self._trade_counter:08d}"

        # Emit OrderFilled
        self._event_bus.trigger_event(
            MarketEvent.OrderFilled,
            OrderFilledEvent(
                timestamp=self._current_timestamp,
                order_id=order.client_order_id,
                trading_pair=order.trading_pair,
                trade_type=order.trade_type,
                order_type=order.order_type,
                fill_price=fill_price,
                fill_amount=fill_amount,
                trade_fee=fee,
                exchange_trade_id=trade_id,
            ),
        )

        # If fully filled, emit OrderCompleted
        if order.status == OrderStatus.FILLED:
            event_tag = (
                MarketEvent.BuyOrderCompleted
                if order.trade_type == TradeType.BUY
                else MarketEvent.SellOrderCompleted
            )
            self._event_bus.trigger_event(
                event_tag,
                OrderCompletedEvent(
                    timestamp=self._current_timestamp,
                    order_id=order.client_order_id,
                    trading_pair=order.trading_pair,
                    order_type=order.order_type,
                    trade_type=order.trade_type,
                    base_amount=order.filled_amount,
                    quote_amount=order.filled_amount * order.filled_price,
                    fee_amount=order.fee_paid,
                    fee_currency=fee_currency,
                ),
            )

        # Apply trade to order book (consume liquidity)
        order_book = self._order_books.get(order.trading_pair)
        if order_book:
            order_book.apply_trade(fill_price, fill_amount, order.trade_type)
