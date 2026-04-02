"""Matching engines for order fill simulation.

Pluggable strategies for determining when and at what price orders fill.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from decimal import Decimal

from market_simulator.core.types import InFlightOrder, MatchResult, OrderType, TradeType
from market_simulator.simulator.order_book import SimulatedOrderBook


class MatchingEngine(ABC):
    """Base class for order matching strategies."""

    @abstractmethod
    def check_and_match(
        self,
        order: InFlightOrder,
        order_book: SimulatedOrderBook,
    ) -> MatchResult | None:
        """Check if an order can be matched and return the result.

        Returns None if the order cannot be matched at this time.
        """
        ...


class ImmediateFillEngine(MatchingEngine):
    """Fills all orders immediately at the best available price.

    Use for: Unit tests where fill timing doesn't matter.
    - MARKET orders fill at best bid/ask.
    - LIMIT orders fill at limit price (assumes market is at that level).
    - Always fills the full amount.
    """

    def check_and_match(
        self,
        order: InFlightOrder,
        order_book: SimulatedOrderBook,
    ) -> MatchResult | None:
        if order.order_type == OrderType.MARKET:
            if order.trade_type == TradeType.BUY:
                price = order_book.best_ask
            else:
                price = order_book.best_bid
            if price is None:
                return None
        else:
            # LIMIT/LIMIT_MAKER: fill at limit price
            price = order.price

        return MatchResult(
            fill_price=price,
            fill_amount=order.remaining_amount,
            is_partial=False,
        )


class LimitOrderEngine(MatchingEngine):
    """Fills limit orders when market price crosses the limit level.

    Use for: Standard backtesting with realistic limit order behavior.
    - MARKET orders fill immediately at best price.
    - LIMIT BUY fills when best_ask <= limit price.
    - LIMIT SELL fills when best_bid >= limit price.
    - LIMIT_MAKER orders fill only when they would be maker (not crossing spread).
    """

    def check_and_match(
        self,
        order: InFlightOrder,
        order_book: SimulatedOrderBook,
    ) -> MatchResult | None:
        if order.order_type == OrderType.MARKET:
            return self._match_market(order, order_book)
        elif order.order_type == OrderType.LIMIT:
            return self._match_limit(order, order_book)
        elif order.order_type == OrderType.LIMIT_MAKER:
            return self._match_limit_maker(order, order_book)
        return None

    def _match_market(
        self, order: InFlightOrder, order_book: SimulatedOrderBook
    ) -> MatchResult | None:
        price = order_book.best_ask if order.trade_type == TradeType.BUY else order_book.best_bid
        if price is None:
            return None
        return MatchResult(
            fill_price=price,
            fill_amount=order.remaining_amount,
        )

    def _match_limit(
        self, order: InFlightOrder, order_book: SimulatedOrderBook
    ) -> MatchResult | None:
        if order.trade_type == TradeType.BUY:
            best_ask = order_book.best_ask
            if best_ask is None or best_ask > order.price:
                return None
            # Fill at limit price (price improvement in simulation)
            fill_price = order.price
        else:
            best_bid = order_book.best_bid
            if best_bid is None or best_bid < order.price:
                return None
            fill_price = order.price
        return MatchResult(
            fill_price=fill_price,
            fill_amount=order.remaining_amount,
        )

    def _match_limit_maker(
        self, order: InFlightOrder, order_book: SimulatedOrderBook
    ) -> MatchResult | None:
        """Maker-only: rejects if it would immediately cross the spread."""
        if order.trade_type == TradeType.BUY:
            best_ask = order_book.best_ask
            if best_ask is not None and order.price >= best_ask:
                return None  # Would be taker — reject
            # Wait for ask to come down to our level
            if best_ask is not None and best_ask <= order.price:
                return MatchResult(
                    fill_price=order.price,
                    fill_amount=order.remaining_amount,
                )
            return None
        else:
            best_bid = order_book.best_bid
            if best_bid is not None and order.price <= best_bid:
                return None  # Would be taker — reject
            if best_bid is not None and best_bid >= order.price:
                return MatchResult(
                    fill_price=order.price,
                    fill_amount=order.remaining_amount,
                )
            return None


class OrderBookDepthEngine(MatchingEngine):
    """Fills by walking order book depth for realistic slippage.

    Use for: High-fidelity simulation where order size vs liquidity matters.
    - Uses VWAP across order book levels.
    - Partial fills if insufficient liquidity.
    """

    def check_and_match(
        self,
        order: InFlightOrder,
        order_book: SimulatedOrderBook,
    ) -> MatchResult | None:
        if order.order_type == OrderType.MARKET:
            return self._match_with_depth(order, order_book)
        elif order.order_type == OrderType.LIMIT:
            return self._match_limit_with_depth(order, order_book)
        elif order.order_type == OrderType.LIMIT_MAKER:
            # Maker orders don't walk depth — they sit in the book
            return self._match_maker_passive(order, order_book)
        return None

    def _match_with_depth(
        self, order: InFlightOrder, order_book: SimulatedOrderBook
    ) -> MatchResult | None:
        """Walk depth for market orders."""
        side = order.trade_type
        remaining = order.remaining_amount
        levels = (
            order_book.get_depth(TradeType.SELL, levels=100)
            if side == TradeType.BUY
            else order_book.get_depth(TradeType.BUY, levels=100)
        )

        filled_amount = Decimal("0")
        total_cost = Decimal("0")

        for price, qty in levels:
            fill = min(remaining, qty)
            total_cost += fill * price
            filled_amount += fill
            remaining -= fill
            if remaining <= 0:
                break

        if filled_amount <= 0:
            return None

        vwap = total_cost / filled_amount
        return MatchResult(
            fill_price=vwap,
            fill_amount=filled_amount,
            is_partial=remaining > 0,
        )

    def _match_limit_with_depth(
        self, order: InFlightOrder, order_book: SimulatedOrderBook
    ) -> MatchResult | None:
        """Walk depth for limit orders, respecting the price limit."""
        if order.trade_type == TradeType.BUY:
            best_ask = order_book.best_ask
            if best_ask is None or best_ask > order.price:
                return None
            levels = order_book.get_depth(TradeType.SELL, levels=100)
            price_ok = lambda p: p <= order.price  # noqa: E731
        else:
            best_bid = order_book.best_bid
            if best_bid is None or best_bid < order.price:
                return None
            levels = order_book.get_depth(TradeType.BUY, levels=100)
            price_ok = lambda p: p >= order.price  # noqa: E731

        remaining = order.remaining_amount
        filled_amount = Decimal("0")
        total_cost = Decimal("0")

        for price, qty in levels:
            if not price_ok(price):
                break
            fill = min(remaining, qty)
            total_cost += fill * price
            filled_amount += fill
            remaining -= fill
            if remaining <= 0:
                break

        if filled_amount <= 0:
            return None

        vwap = total_cost / filled_amount
        return MatchResult(
            fill_price=vwap,
            fill_amount=filled_amount,
            is_partial=remaining > 0,
        )

    def _match_maker_passive(
        self, order: InFlightOrder, order_book: SimulatedOrderBook
    ) -> MatchResult | None:
        """Passive fill: only fills if price comes to us."""
        if order.trade_type == TradeType.BUY:
            best_ask = order_book.best_ask
            if best_ask is None or best_ask > order.price:
                return None
            # Price came to us — fill at our limit
            return MatchResult(
                fill_price=order.price,
                fill_amount=order.remaining_amount,
            )
        else:
            best_bid = order_book.best_bid
            if best_bid is None or best_bid < order.price:
                return None
            return MatchResult(
                fill_price=order.price,
                fill_amount=order.remaining_amount,
            )
