"""ReplayDataSource: feeds time-ordered market data to SimulatedExchange.

Given loaded historical data (trades, candles, order book snapshots),
produces a stream of events ordered by timestamp. The SandboxEngine
consumes these events to drive the simulation.
"""

from __future__ import annotations

import heapq
from decimal import Decimal
from typing import Iterator, List, Optional

from market_simulator.core.types import TradeType
from market_simulator.replay.data_types import (
    CandleRecord,
    OrderBookDiff,
    OrderBookSnapshot,
    ReplayEvent,
    ReplayEventType,
    TradeRecord,
)
from market_simulator.simulator.order_book import SimulatedOrderBook


class ReplayDataSource:
    """Merges multiple data streams into a single time-ordered event stream.

    Usage:
        source = ReplayDataSource()
        source.add_trades(trades_list)
        source.add_candles(candles_list, generate_order_book=True)
        source.add_order_book_snapshots(snapshots_list)

        for event in source.events(start_time=t0, end_time=t1):
            apply_event_to_exchange(event)
    """

    def __init__(self) -> None:
        self._events: List[ReplayEvent] = []
        self._sorted = False

    def add_trades(self, trades: List[dict]) -> None:
        """Add trade records to the replay stream.

        Each dict must have: timestamp, trading_pair, price, amount, is_buyer_maker
        Optional: trade_id
        """
        for t in trades:
            self._events.append(ReplayEvent(
                timestamp=t["timestamp"],
                event_type=ReplayEventType.TRADE,
                trading_pair=t["trading_pair"],
                data=t,
            ))
        self._sorted = False

    def add_candles(
        self,
        candles: List[dict],
        generate_order_book: bool = True,
        spread_bps: Decimal = Decimal("10"),
        depth_levels: int = 5,
        depth_quantity: Decimal = Decimal("1.0"),
    ) -> None:
        """Add candle records to the replay stream.

        If generate_order_book=True, synthetic order book snapshots are
        generated from each candle's close price with the given spread and depth.
        """
        for c in candles:
            self._events.append(ReplayEvent(
                timestamp=c["timestamp"],
                event_type=ReplayEventType.CANDLE,
                trading_pair=c["trading_pair"],
                data=c,
            ))

            if generate_order_book:
                close = Decimal(str(c["close"]))
                half_spread = close * spread_bps / Decimal("20000")
                mid = close
                bids = []
                asks = []
                for i in range(depth_levels):
                    offset = half_spread * (i + 1)
                    bids.append((mid - offset, depth_quantity))
                    asks.append((mid + offset, depth_quantity))

                self._events.append(ReplayEvent(
                    timestamp=c["timestamp"],
                    event_type=ReplayEventType.ORDER_BOOK_SNAPSHOT,
                    trading_pair=c["trading_pair"],
                    data={
                        "timestamp": c["timestamp"],
                        "trading_pair": c["trading_pair"],
                        "bids": bids,
                        "asks": asks,
                    },
                ))
        self._sorted = False

    def add_order_book_snapshots(self, snapshots: List[dict]) -> None:
        """Add order book snapshots to the replay stream.

        Each dict must have: timestamp, trading_pair, bids, asks
        """
        for s in snapshots:
            self._events.append(ReplayEvent(
                timestamp=s["timestamp"],
                event_type=ReplayEventType.ORDER_BOOK_SNAPSHOT,
                trading_pair=s["trading_pair"],
                data=s,
            ))
        self._sorted = False

    def events(
        self,
        start_time: Optional[float] = None,
        end_time: Optional[float] = None,
    ) -> Iterator[ReplayEvent]:
        """Yield events in timestamp order within the given time range."""
        if not self._sorted:
            self._events.sort(key=lambda e: (e.timestamp, e.event_type.value))
            self._sorted = True

        for event in self._events:
            if start_time is not None and event.timestamp < start_time:
                continue
            if end_time is not None and event.timestamp > end_time:
                break
            yield event

    @property
    def time_range(self) -> Optional[tuple[float, float]]:
        """Return (earliest, latest) timestamps, or None if empty."""
        if not self._events:
            return None
        if not self._sorted:
            self._events.sort(key=lambda e: (e.timestamp, e.event_type.value))
            self._sorted = True
        return self._events[0].timestamp, self._events[-1].timestamp

    @property
    def event_count(self) -> int:
        return len(self._events)


def apply_replay_event(
    event: ReplayEvent,
    order_books: dict[str, SimulatedOrderBook],
) -> None:
    """Apply a replay event to the appropriate order book.

    This is the standard event handler used by the simulation loop.
    """
    pair = event.trading_pair
    book = order_books.get(pair)
    if book is None:
        return

    if event.event_type == ReplayEventType.ORDER_BOOK_SNAPSHOT:
        book.apply_snapshot(
            bids=event.data["bids"],
            asks=event.data["asks"],
        )
    elif event.event_type == ReplayEventType.ORDER_BOOK_DIFF:
        book.apply_diffs(
            bid_diffs=event.data.get("bid_diffs", []),
            ask_diffs=event.data.get("ask_diffs", []),
        )
    elif event.event_type == ReplayEventType.TRADE:
        price = Decimal(str(event.data["price"]))
        amount = Decimal(str(event.data["amount"]))
        # is_buyer_maker=True means sell aggressor
        side = TradeType.SELL if event.data.get("is_buyer_maker", False) else TradeType.BUY
        book.apply_trade(price, amount, side)
