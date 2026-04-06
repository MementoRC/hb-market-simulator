"""Simulated order book with bid/ask management and price queries.

Uses sorted dicts for efficient price-level lookups. Supports snapshot
application, incremental diffs, and trade impact.
"""

from __future__ import annotations

from decimal import Decimal

from market_simulator.core.types import PriceType, TradeType


class SimulatedOrderBook:
    """In-memory order book for a single trading pair.

    Bids are stored descending (best bid first), asks ascending (best ask first).
    Internally uses dicts keyed by price level -> quantity.
    """

    def __init__(self, trading_pair: str) -> None:
        self._trading_pair = trading_pair
        self._bids: dict[Decimal, Decimal] = {}  # price -> quantity
        self._asks: dict[Decimal, Decimal] = {}  # price -> quantity
        self._last_trade_price: Decimal | None = None
        self._sorted_bids_cache: list[tuple[Decimal, Decimal]] | None = None
        self._sorted_asks_cache: list[tuple[Decimal, Decimal]] | None = None

    @property
    def trading_pair(self) -> str:
        return self._trading_pair

    @property
    def best_bid(self) -> Decimal | None:
        if not self._bids:
            return None
        return max(self._bids.keys())

    @property
    def best_ask(self) -> Decimal | None:
        if not self._asks:
            return None
        return min(self._asks.keys())

    @property
    def mid_price(self) -> Decimal | None:
        bid = self.best_bid
        ask = self.best_ask
        if bid is not None and ask is not None:
            return (bid + ask) / 2
        return None

    @property
    def spread(self) -> Decimal | None:
        bid = self.best_bid
        ask = self.best_ask
        if bid is not None and ask is not None:
            return ask - bid
        return None

    @property
    def last_trade_price(self) -> Decimal | None:
        return self._last_trade_price

    def get_price_by_type(self, price_type: PriceType) -> Decimal:
        """Get price by type. Raises ValueError if unavailable."""
        if price_type == PriceType.MidPrice:
            price = self.mid_price
        elif price_type == PriceType.BestBid:
            price = self.best_bid
        elif price_type == PriceType.BestAsk:
            price = self.best_ask
        elif price_type == PriceType.LastTrade:
            price = self._last_trade_price
        else:
            raise ValueError(f"Unknown price type: {price_type}")

        if price is None:
            raise ValueError(f"Price not available for type {price_type}")
        return price

    def apply_snapshot(
        self,
        bids: list[tuple[Decimal, Decimal]],
        asks: list[tuple[Decimal, Decimal]],
    ) -> None:
        """Replace the entire order book with a new snapshot.

        Args:
            bids: List of (price, quantity) tuples.
            asks: List of (price, quantity) tuples.
        """
        self._bids = {price: qty for price, qty in bids if qty > 0}
        self._asks = {price: qty for price, qty in asks if qty > 0}
        self._invalidate_cache()

    def apply_diffs(
        self,
        bid_diffs: list[tuple[Decimal, Decimal]],
        ask_diffs: list[tuple[Decimal, Decimal]],
    ) -> None:
        """Apply incremental updates to the order book.

        A quantity of 0 removes that price level.
        """
        for price, qty in bid_diffs:
            if qty <= 0:
                self._bids.pop(price, None)
            else:
                self._bids[price] = qty

        for price, qty in ask_diffs:
            if qty <= 0:
                self._asks.pop(price, None)
            else:
                self._asks[price] = qty

        self._invalidate_cache()

    def apply_trade(self, price: Decimal, amount: Decimal, side: TradeType) -> None:
        """Record a trade and reduce liquidity at the traded price level.

        Args:
            price: Trade price.
            amount: Trade amount.
            side: BUY means the aggressor bought (removes ask liquidity),
                  SELL means the aggressor sold (removes bid liquidity).
        """
        self._last_trade_price = price

        if side == TradeType.BUY:
            # Buyer aggressor consumes asks
            if price in self._asks:
                remaining = self._asks[price] - amount
                if remaining <= 0:
                    del self._asks[price]
                else:
                    self._asks[price] = remaining
        else:
            # Seller aggressor consumes bids
            if price in self._bids:
                remaining = self._bids[price] - amount
                if remaining <= 0:
                    del self._bids[price]
                else:
                    self._bids[price] = remaining

        self._invalidate_cache()

    def get_depth(self, side: TradeType, levels: int = 10) -> list[tuple[Decimal, Decimal]]:
        """Get order book depth for the given side.

        Args:
            side: BUY for bids, SELL for asks.
            levels: Maximum number of price levels to return.

        Returns:
            List of (price, quantity) sorted best-to-worst.
        """
        if side == TradeType.BUY:
            return self._sorted_bids[:levels]
        else:
            return self._sorted_asks[:levels]

    def get_volume_for_price_move(self, side: TradeType, price_limit: Decimal) -> Decimal:
        """Calculate total volume available up to a price limit.

        Args:
            side: BUY (walking up the ask side) or SELL (walking down the bid side).
            price_limit: The worst price we're willing to accept.

        Returns:
            Total volume available within the price limit.
        """
        total = Decimal("0")
        if side == TradeType.BUY:
            for price, qty in self._sorted_asks:
                if price > price_limit:
                    break
                total += qty
        else:
            for price, qty in self._sorted_bids:
                if price < price_limit:
                    break
                total += qty
        return total

    def get_vwap(self, side: TradeType, amount: Decimal) -> Decimal | None:
        """Calculate volume-weighted average price for a given order size.

        Args:
            side: BUY (walking asks) or SELL (walking bids).
            amount: Order size to fill.

        Returns:
            VWAP or None if insufficient liquidity.
        """
        remaining = amount
        total_cost = Decimal("0")
        levels = self._sorted_asks if side == TradeType.BUY else self._sorted_bids

        for price, qty in levels:
            fill = min(remaining, qty)
            total_cost += fill * price
            remaining -= fill
            if remaining <= 0:
                return total_cost / amount

        return None  # Insufficient liquidity

    @property
    def _sorted_bids(self) -> list[tuple[Decimal, Decimal]]:
        if self._sorted_bids_cache is None:
            self._sorted_bids_cache = sorted(self._bids.items(), key=lambda x: x[0], reverse=True)
        return self._sorted_bids_cache

    @property
    def _sorted_asks(self) -> list[tuple[Decimal, Decimal]]:
        if self._sorted_asks_cache is None:
            self._sorted_asks_cache = sorted(self._asks.items(), key=lambda x: x[0])
        return self._sorted_asks_cache

    def _invalidate_cache(self) -> None:
        self._sorted_bids_cache = None
        self._sorted_asks_cache = None

    def __repr__(self) -> str:
        return (
            f"SimulatedOrderBook({self._trading_pair}, "
            f"bids={len(self._bids)}, asks={len(self._asks)}, "
            f"best_bid={self.best_bid}, best_ask={self.best_ask})"
        )
