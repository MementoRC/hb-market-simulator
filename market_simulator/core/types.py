"""Core data types for market simulation.

These types are standalone — no hummingbot imports. The hb_compat layer maps
these to/from hummingbot's own types (OrderType, TradeType, PriceType, etc.).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum, IntEnum, auto

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class OrderType(Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    LIMIT_MAKER = "LIMIT_MAKER"


class TradeType(Enum):
    BUY = "BUY"
    SELL = "SELL"


class PriceType(IntEnum):
    MidPrice = 1
    BestBid = 2
    BestAsk = 3
    LastTrade = 4


class OrderStatus(Enum):
    PENDING_CREATE = auto()
    OPEN = auto()
    PARTIALLY_FILLED = auto()
    FILLED = auto()
    PENDING_CANCEL = auto()
    CANCELLED = auto()
    FAILED = auto()


class PositionAction(Enum):
    OPEN = "OPEN"
    CLOSE = "CLOSE"
    NIL = "NIL"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class TradingRule:
    """Trading constraints for a pair, mirroring hummingbot's TradingRule."""

    trading_pair: str
    min_order_size: Decimal = Decimal("0")
    max_order_size: Decimal = Decimal("1000000")
    min_price_increment: Decimal = Decimal("0.01")
    min_base_amount_increment: Decimal = Decimal("0.001")
    min_notional_size: Decimal = Decimal("0")
    min_quote_amount_increment: Decimal = Decimal("0.01")

    def quantize_order_amount(self, amount: Decimal) -> Decimal:
        """Quantize order amount to allowed increment."""
        if self.min_base_amount_increment <= 0:
            return amount
        return (amount // self.min_base_amount_increment) * self.min_base_amount_increment

    def quantize_order_price(self, price: Decimal) -> Decimal:
        """Quantize order price to allowed increment."""
        if self.min_price_increment <= 0:
            return price
        return (price // self.min_price_increment) * self.min_price_increment


@dataclass
class TradeFee:
    """Fee charged for a trade."""

    percent: Decimal = Decimal("0")
    flat_fees: list[tuple[str, Decimal]] = field(default_factory=list)

    @property
    def total_flat_fee(self) -> Decimal:
        return sum(amount for _, amount in self.flat_fees) if self.flat_fees else Decimal("0")


@dataclass
class InFlightOrder:
    """An order that has been placed but not yet fully resolved."""

    client_order_id: str
    trading_pair: str
    order_type: OrderType
    trade_type: TradeType
    amount: Decimal
    price: Decimal
    status: OrderStatus = OrderStatus.PENDING_CREATE
    filled_amount: Decimal = Decimal("0")
    filled_price: Decimal = Decimal("0")  # volume-weighted average fill price
    fee_paid: Decimal = Decimal("0")
    creation_timestamp: float = 0.0
    last_update_timestamp: float = 0.0
    position_action: PositionAction = PositionAction.NIL
    exchange_order_id: str | None = None

    @property
    def is_open(self) -> bool:
        return self.status in (
            OrderStatus.PENDING_CREATE,
            OrderStatus.OPEN,
            OrderStatus.PARTIALLY_FILLED,
        )

    @property
    def is_done(self) -> bool:
        return self.status in (OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.FAILED)

    @property
    def remaining_amount(self) -> Decimal:
        return self.amount - self.filled_amount

    @property
    def base_asset(self) -> str:
        return self.trading_pair.split("-")[0]

    @property
    def quote_asset(self) -> str:
        return self.trading_pair.split("-")[1]

    def update_with_fill(self, fill_price: Decimal, fill_amount: Decimal, fee: Decimal) -> None:
        """Apply a fill to this order, updating VWAP and amounts."""
        prev_value = self.filled_price * self.filled_amount
        new_value = fill_price * fill_amount
        self.filled_amount += fill_amount
        if self.filled_amount > 0:
            self.filled_price = (prev_value + new_value) / self.filled_amount
        self.fee_paid += fee
        if self.filled_amount >= self.amount:
            self.status = OrderStatus.FILLED
        else:
            self.status = OrderStatus.PARTIALLY_FILLED


@dataclass
class MatchResult:
    """Result of a matching engine evaluation."""

    fill_price: Decimal
    fill_amount: Decimal
    is_partial: bool = False
    fee: TradeFee = field(default_factory=TradeFee)
