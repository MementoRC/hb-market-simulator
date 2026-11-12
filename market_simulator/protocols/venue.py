"""SimulatedVenueProtocol: the public surface an outside adapter drives.

An adapter (for example the parent repo's ExchangePyBase shim) talks to a simulated
venue exclusively through this Protocol. It is expressed purely in terms of
hb-data-type-primitives types and simulator-local models, so implementing or
consuming it requires no hummingbot import.
"""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from data_type_primitives import (
    AddedToCostTradeFee,
    OrderType,
    OrderUpdate,
    TradeType,
    TradeUpdate,
)

from market_simulator.core.types import TradingRule

if TYPE_CHECKING:
    from market_simulator.simulator.exchange import SimulatedExchange

__all__ = ["OrderUpdateListener", "SimulatedVenueProtocol", "TradeUpdateListener"]

OrderUpdateListener = Callable[[OrderUpdate], None]
TradeUpdateListener = Callable[[TradeUpdate], None]


@runtime_checkable
class SimulatedVenueProtocol(Protocol):
    """A simulated trading venue emitting DTP OrderUpdate/TradeUpdate objects."""

    # -- identity -----------------------------------------------------------

    @property
    def name(self) -> str: ...

    @property
    def trading_pairs(self) -> list[str]: ...

    def supported_order_types(self) -> list[OrderType]: ...

    @property
    def sim_exchange(self) -> SimulatedExchange: ...

    # -- balances -----------------------------------------------------------

    def get_balance(self, asset: str) -> Decimal: ...

    def get_available_balance(self, asset: str) -> Decimal: ...

    def get_all_balances(self) -> dict[str, Decimal]: ...

    def get_all_available_balances(self) -> dict[str, Decimal]: ...

    # -- trading rules ------------------------------------------------------

    @property
    def trading_rules(self) -> dict[str, TradingRule]: ...

    # -- fees ---------------------------------------------------------------

    def get_fee(
        self,
        base_currency: str,
        quote_currency: str,
        order_type: OrderType,
        order_side: TradeType,
        amount: Decimal,
        price: Decimal = ...,
        is_maker: bool | None = None,
    ) -> AddedToCostTradeFee: ...

    # -- orders -------------------------------------------------------------

    async def place_order(
        self,
        client_order_id: str,
        trading_pair: str,
        amount: Decimal,
        trade_type: TradeType,
        order_type: OrderType,
        price: Decimal,
        **kwargs: Any,
    ) -> str: ...

    async def cancel_order(self, client_order_id: str, trading_pair: str) -> bool: ...

    async def request_order_status(
        self, client_order_id: str, trading_pair: str = ""
    ) -> OrderUpdate: ...

    async def all_trade_updates_for_order(self, client_order_id: str) -> list[TradeUpdate]: ...

    # -- update listeners ---------------------------------------------------

    def add_order_update_listener(self, listener: OrderUpdateListener) -> None: ...

    def add_trade_update_listener(self, listener: TradeUpdateListener) -> None: ...

    # -- refresh hooks and clock --------------------------------------------

    async def update_balances(self) -> None: ...

    async def update_trading_rules(self) -> None: ...

    async def update_trading_fees(self) -> None: ...

    def tick(self, timestamp: float) -> None: ...
