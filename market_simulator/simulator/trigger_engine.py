"""TriggerEngine: evaluates whether a conditional order should fire.

Provides a pluggable strategy interface so simulation users can swap in
custom trigger logic. The default StandardTriggerEngine handles stop-loss,
take-profit, and trailing-stop conditions based on the current order book.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from decimal import Decimal

from market_simulator.core.types import InFlightOrder, OrderType, TradeType

# ---------------------------------------------------------------------------
# Order book snapshot type
# ---------------------------------------------------------------------------

# A lightweight snapshot of the best bid/ask passed to trigger evaluation.
# Format: {"best_bid": Decimal | None, "best_ask": Decimal | None,
#           "last_trade": Decimal | None}
OrderBookSnapshot = dict[str, Decimal | None]


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------


class TriggerEngine(ABC):
    """Abstract trigger evaluation strategy.

    Implementors decide, given an open conditional order and the current
    market snapshot, whether the order's trigger condition is satisfied.
    """

    @abstractmethod
    def evaluate(
        self,
        order: InFlightOrder,
        snapshot: OrderBookSnapshot,
        timestamp: float,
    ) -> bool:
        """Return True if the conditional order should fire now.

        :param order: The open conditional in-flight order to evaluate.
        :param snapshot: Current market snapshot with best_bid, best_ask,
            and last_trade prices.
        :param timestamp: Current simulation timestamp (seconds).
        :return: True if the order trigger condition is met.
        """


# ---------------------------------------------------------------------------
# NullTriggerEngine — never fires
# ---------------------------------------------------------------------------


class NullTriggerEngine(TriggerEngine):
    """A no-op trigger engine that never fires any conditional order.

    Useful for unit tests that want to control triggering manually, or
    for exchange configurations where conditional orders are not desired.
    """

    def evaluate(
        self,
        order: InFlightOrder,
        snapshot: OrderBookSnapshot,
        timestamp: float,
    ) -> bool:
        """Always return False — no triggers fire."""
        return False


# ---------------------------------------------------------------------------
# StandardTriggerEngine — price-based trigger logic
# ---------------------------------------------------------------------------


class StandardTriggerEngine(TriggerEngine):
    """Standard price-based trigger evaluation for conditional order types.

    Supported order types and their trigger conditions:

    STOP_LOSS / STOP_LOSS_LIMIT:
      - BUY: fires when best_ask >= trigger_price  (price rose to stop level)
      - SELL: fires when best_bid <= trigger_price (price dropped to stop level)

    TAKE_PROFIT / TAKE_PROFIT_LIMIT:
      - BUY: fires when best_ask <= trigger_price  (price dropped to profit level)
      - SELL: fires when best_bid >= trigger_price (price rose to profit level)

    TRAILING_STOP:
      - Requires trail_amount to be set on the order.
      - The trailing high/low is tracked internally per order_id.
      - BUY:  trail tracks the lowest ask seen; fires when ask rises by
              trail_amount above the lowest seen ask.
      - SELL: trail tracks the highest bid seen; fires when bid drops by
              trail_amount below the highest seen bid.
    """

    def __init__(self) -> None:
        # Per-order trailing reference prices: order_id -> extreme_price
        self._trailing_references: dict[str, Decimal] = {}

    def evaluate(
        self,
        order: InFlightOrder,
        snapshot: OrderBookSnapshot,
        timestamp: float,
    ) -> bool:
        """Evaluate whether the conditional order's trigger condition is met.

        :param order: The open conditional in-flight order.
        :param snapshot: Market snapshot with best_bid, best_ask, last_trade.
        :param timestamp: Current simulation timestamp.
        :return: True if the trigger condition is satisfied.
        :raises ValueError: If the order has no trigger_price for a type that
            requires one, or no trail_amount for TRAILING_STOP.
        """
        order_type = order.order_type

        if order_type in (OrderType.STOP_LOSS, OrderType.STOP_LOSS_LIMIT):
            return self._evaluate_stop_loss(order, snapshot)

        if order_type in (OrderType.TAKE_PROFIT, OrderType.TAKE_PROFIT_LIMIT):
            return self._evaluate_take_profit(order, snapshot)

        if order_type == OrderType.TRAILING_STOP:
            return self._evaluate_trailing_stop(order, snapshot)

        return False

    def _evaluate_stop_loss(
        self,
        order: InFlightOrder,
        snapshot: OrderBookSnapshot,
    ) -> bool:
        """Fire stop-loss when price moves against the position to the stop level."""
        trigger_price = order.trigger_price
        if trigger_price is None:
            return False

        if order.trade_type == TradeType.BUY:
            # Protective buy stop: fires when ask rises to or above trigger
            best_ask = snapshot.get("best_ask")
            if best_ask is None:
                return False
            return best_ask >= trigger_price
        else:
            # Protective sell stop: fires when bid drops to or below trigger
            best_bid = snapshot.get("best_bid")
            if best_bid is None:
                return False
            return best_bid <= trigger_price

    def _evaluate_take_profit(
        self,
        order: InFlightOrder,
        snapshot: OrderBookSnapshot,
    ) -> bool:
        """Fire take-profit when price moves in favour of the position to the target."""
        trigger_price = order.trigger_price
        if trigger_price is None:
            return False

        if order.trade_type == TradeType.BUY:
            # Buy take-profit: fires when ask drops to or below trigger
            best_ask = snapshot.get("best_ask")
            if best_ask is None:
                return False
            return best_ask <= trigger_price
        else:
            # Sell take-profit: fires when bid rises to or above trigger
            best_bid = snapshot.get("best_bid")
            if best_bid is None:
                return False
            return best_bid >= trigger_price

    def _evaluate_trailing_stop(
        self,
        order: InFlightOrder,
        snapshot: OrderBookSnapshot,
    ) -> bool:
        """Fire trailing stop when price reverses by trail_amount from the extreme."""
        trail_amount = order.trail_amount
        if trail_amount is None or trail_amount <= Decimal("0"):
            return False

        order_id = order.client_order_id

        if order.trade_type == TradeType.BUY:
            # Track the lowest ask seen; fire when ask rises trail_amount above it
            best_ask = snapshot.get("best_ask")
            if best_ask is None:
                return False

            current_ref = self._trailing_references.get(order_id)
            if current_ref is None:
                # First evaluation — initialise reference from trigger_price or current price
                reference = order.trigger_price if order.trigger_price is not None else best_ask
                self._trailing_references[order_id] = reference
                current_ref = reference

            # Update reference downward (track the best / lowest ask)
            if best_ask < current_ref:
                self._trailing_references[order_id] = best_ask
                current_ref = best_ask

            return best_ask >= current_ref + trail_amount

        else:
            # Track the highest bid seen; fire when bid drops trail_amount below it
            best_bid = snapshot.get("best_bid")
            if best_bid is None:
                return False

            current_ref = self._trailing_references.get(order_id)
            if current_ref is None:
                reference = order.trigger_price if order.trigger_price is not None else best_bid
                self._trailing_references[order_id] = reference
                current_ref = reference

            # Update reference upward (track the best / highest bid)
            if best_bid > current_ref:
                self._trailing_references[order_id] = best_bid
                current_ref = best_bid

            return best_bid <= current_ref - trail_amount

    def clear_trailing_reference(self, order_id: str) -> None:
        """Remove the trailing reference for a completed or cancelled order.

        :param order_id: The client order ID to remove from trailing tracking.
        """
        self._trailing_references.pop(order_id, None)
