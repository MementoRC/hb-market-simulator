"""Fee models for trade cost calculation.

Pluggable fee calculation: zero, flat-rate, or tiered by volume.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from decimal import Decimal

from market_simulator.core.types import OrderType, TradeFee, TradeType


class FeeModel(ABC):
    """Base class for fee calculation."""

    @abstractmethod
    def calculate_fee(
        self,
        trading_pair: str,
        trade_type: TradeType,
        order_type: OrderType,
        amount: Decimal,
        price: Decimal,
    ) -> TradeFee:
        """Calculate the fee for a trade."""
        ...


class ZeroFeeModel(FeeModel):
    """No fees charged."""

    def calculate_fee(
        self,
        trading_pair: str,
        trade_type: TradeType,
        order_type: OrderType,
        amount: Decimal,
        price: Decimal,
    ) -> TradeFee:
        return TradeFee()


class FlatFeeModel(FeeModel):
    """Flat percentage fee with separate maker/taker rates.

    Maker rate applies to LIMIT_MAKER orders; taker rate to MARKET and LIMIT.
    Fee is charged in the quote currency.
    """

    def __init__(
        self,
        maker_rate: Decimal = Decimal("0.001"),
        taker_rate: Decimal = Decimal("0.001"),
    ):
        self._maker_rate = maker_rate
        self._taker_rate = taker_rate

    @property
    def maker_rate(self) -> Decimal:
        return self._maker_rate

    @property
    def taker_rate(self) -> Decimal:
        return self._taker_rate

    def calculate_fee(
        self,
        trading_pair: str,
        trade_type: TradeType,
        order_type: OrderType,
        amount: Decimal,
        price: Decimal,
    ) -> TradeFee:
        rate = self._maker_rate if order_type == OrderType.LIMIT_MAKER else self._taker_rate
        quote_currency = trading_pair.split("-")[1]
        fee_amount = amount * price * rate
        return TradeFee(
            percent=rate,
            flat_fees=[(quote_currency, fee_amount)],
        )
