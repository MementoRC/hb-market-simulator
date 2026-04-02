"""Protocol definitions for connector-like interfaces.

These protocols capture the surface area of ConnectorBase as consumed by
the strategy_v2 stack. They use only market_simulator.core types, keeping
the simulator free of hummingbot imports. The hb_compat layer bridges these
to hummingbot's actual types.
"""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from typing import Any, Protocol, runtime_checkable

from market_simulator.core.types import (
    InFlightOrder,
    OrderType,
    PriceType,
    TradingRule,
)


@runtime_checkable
class MarketDataProtocol(Protocol):
    """Read-only market data access."""

    def get_price_by_type(self, trading_pair: str, price_type: PriceType) -> Decimal: ...

    def get_order_book_snapshot(self, trading_pair: str) -> dict: ...

    @property
    def trading_rules(self) -> dict[str, TradingRule]: ...

    def quantize_order_price(self, trading_pair: str, price: Decimal) -> Decimal: ...

    def quantize_order_amount(self, trading_pair: str, amount: Decimal) -> Decimal: ...


@runtime_checkable
class BalanceProtocol(Protocol):
    """Balance queries."""

    def get_balance(self, currency: str) -> Decimal: ...

    def get_available_balance(self, currency: str) -> Decimal: ...

    def get_all_balances(self) -> dict[str, Decimal]: ...


@runtime_checkable
class OrderExecutionProtocol(Protocol):
    """Order lifecycle management."""

    def buy(
        self,
        trading_pair: str,
        amount: Decimal,
        order_type: OrderType,
        price: Decimal,
        **kwargs: Any,
    ) -> str: ...

    def sell(
        self,
        trading_pair: str,
        amount: Decimal,
        order_type: OrderType,
        price: Decimal,
        **kwargs: Any,
    ) -> str: ...

    def cancel(self, trading_pair: str, client_order_id: str) -> None: ...

    @property
    def in_flight_orders(self) -> dict[str, InFlightOrder]: ...

    def get_in_flight_order(self, client_order_id: str) -> InFlightOrder | None: ...


@runtime_checkable
class EventSourceProtocol(Protocol):
    """Event subscription and emission."""

    def add_listener(self, event_tag: int, listener: Callable) -> None: ...

    def remove_listener(self, event_tag: int, listener: Callable) -> None: ...

    def trigger_event(self, event_tag: int, message: Any) -> None: ...


@runtime_checkable
class ReadinessProtocol(Protocol):
    """Connector identity and readiness."""

    @property
    def ready(self) -> bool: ...

    @property
    def name(self) -> str: ...

    @property
    def trading_pairs(self) -> list[str]: ...
