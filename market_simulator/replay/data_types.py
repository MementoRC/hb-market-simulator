"""Data types for the replay system."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum, auto


class ReplayEventType(Enum):
    """Types of events in a replay stream."""

    ORDER_BOOK_SNAPSHOT = auto()
    ORDER_BOOK_DIFF = auto()
    TRADE = auto()
    CANDLE = auto()


@dataclass(frozen=True)
class ReplayEvent:
    """A single timestamped event in a replay stream.

    All replay events are ordered by timestamp. The SimulatedExchange
    processes them in sequence during simulation.
    """

    timestamp: float  # epoch seconds
    event_type: ReplayEventType
    trading_pair: str
    data: dict  # event-specific payload


@dataclass(frozen=True)
class OrderBookSnapshot:
    """Full order book state at a point in time."""

    timestamp: float
    trading_pair: str
    bids: list[tuple[Decimal, Decimal]]  # (price, quantity)
    asks: list[tuple[Decimal, Decimal]]


@dataclass(frozen=True)
class OrderBookDiff:
    """Incremental order book update."""

    timestamp: float
    trading_pair: str
    bid_diffs: list[tuple[Decimal, Decimal]]  # (price, quantity) — 0 qty = remove
    ask_diffs: list[tuple[Decimal, Decimal]]


@dataclass(frozen=True)
class TradeRecord:
    """A single trade event."""

    timestamp: float
    trading_pair: str
    trade_id: str
    price: Decimal
    amount: Decimal
    is_buyer_maker: bool  # True = sell aggressor, False = buy aggressor


@dataclass(frozen=True)
class CandleRecord:
    """OHLCV candle data."""

    timestamp: float  # candle open time
    trading_pair: str
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    interval: str = "1m"  # e.g., "1m", "5m", "1h"
