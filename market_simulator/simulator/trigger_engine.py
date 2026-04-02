"""Trigger evaluation engines for conditional order types."""

from __future__ import annotations

from abc import ABC, abstractmethod
from decimal import Decimal

from market_simulator.core.types import InFlightOrder, OrderType, TradeType


class TriggerEngine(ABC):
    """Base class for conditional order trigger evaluation."""

    @abstractmethod
    def evaluate(
        self,
        order: InFlightOrder,
        order_book: dict,
        timestamp: float,
    ) -> bool:
        """Evaluate whether a conditional order should be triggered.

        :param order: The conditional order to evaluate
        :param order_book: Current order book with 'bids' and 'asks' lists
        :param timestamp: Current simulation time
        :return: True if the order should fire
        """


class StandardTriggerEngine(TriggerEngine):
    """Standard trigger evaluation implementing exchange-like behavior."""

    def evaluate(
        self,
        order: InFlightOrder,
        order_book: dict,
        timestamp: float,
    ) -> bool:
        if not order.is_conditional:
            return False

        bids = order_book.get("bids", [])
        asks = order_book.get("asks", [])

        if not bids or not asks:
            return False

        best_bid = Decimal(str(bids[0][0]))
        best_ask = Decimal(str(asks[0][0]))

        if order.order_type == OrderType.STOP_LOSS:
            return self._evaluate_stop_loss(order, best_bid, best_ask)
        elif order.order_type == OrderType.TAKE_PROFIT:
            return self._evaluate_take_profit(order, best_bid, best_ask)
        elif order.order_type == OrderType.TRAILING_STOP:
            return self._evaluate_trailing_stop(order, best_bid, best_ask)
        return False

    def _evaluate_stop_loss(
        self, order: InFlightOrder, best_bid: Decimal, best_ask: Decimal
    ) -> bool:
        if order.trigger_price is None:
            return False
        if order.trade_type == TradeType.BUY:
            return best_ask >= order.trigger_price
        else:  # SELL
            return best_bid <= order.trigger_price

    def _evaluate_take_profit(
        self, order: InFlightOrder, best_bid: Decimal, best_ask: Decimal
    ) -> bool:
        if order.trigger_price is None:
            return False
        if order.trade_type == TradeType.BUY:
            return best_ask <= order.trigger_price
        else:  # SELL
            return best_bid >= order.trigger_price

    def _evaluate_trailing_stop(
        self, order: InFlightOrder, best_bid: Decimal, best_ask: Decimal
    ) -> bool:
        if order.trail_amount is None:
            return False

        # Initialize watermark on first evaluation
        if order.watermark is None:
            if order.trade_type == TradeType.BUY:
                order.watermark = best_ask  # Track min ask
            else:
                order.watermark = best_bid  # Track max bid
            return False

        if order.trade_type == TradeType.BUY:
            # Update watermark to lowest ask seen
            if best_ask < order.watermark:
                order.watermark = best_ask
            # Fire when price rises from the low by trail_amount
            return best_ask >= order.watermark + order.trail_amount
        else:  # SELL
            # Update watermark to highest bid seen
            if best_bid > order.watermark:
                order.watermark = best_bid
            # Fire when price drops from the high by trail_amount
            return best_bid <= order.watermark - order.trail_amount


class NullTriggerEngine(TriggerEngine):
    """Trigger engine that never fires — backward compatibility."""

    def evaluate(
        self,
        order: InFlightOrder,
        order_book: dict,
        timestamp: float,
    ) -> bool:
        return False
