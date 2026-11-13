"""Explicit bridge between market_simulator core types and data_type_primitives (DTP).

core.types.InFlightOrder / TradeFee / OrderStatus stay simulator-specific on purpose
(hb-market-simulator #58). This module is the single, tested conversion point so the
same concept does not have two silent identities (#67). It reuses the connector's
exhaustive ``_STATE_MAP`` (OrderStatus -> OrderState); it does not redefine it.

Functions: ``in_flight_order_to_canonical``, ``in_flight_order_from_canonical``,
``trade_fee_to_canonical``, ``trade_fee_from_canonical``.

Lossy fields (sim -> DTP), and how each is handled
==================================================

=====================================  ===================================================
Field                                  Handling
=====================================  ===================================================
trigger_price, trail_amount,           Dropped (DTP has no conditional-order fields).
watermark                              Order type is kept; they come back as None.
filled_price (VWAP)                    Folded into ``executed_amount_quote`` (filled * VWAP);
                                       restored as quote / base (exact when the division
                                       is clean within Decimal context precision).
fee_paid                               Dropped: DTP keeps fees per fill (``order_fills``),
                                       which a sim order lacks. Back: 0 unless passed in.
OrderStatus vs OrderState              Forward via ``_STATE_MAP`` (CANCELLED -> CANCELED).
                                       PENDING_APPROVAL, APPROVED, CREATED, COMPLETED
                                       have no sim equivalent: from_canonical raises
                                       ValueError instead of guessing.
leverage                               DTP-only; default 1; ignored on the way back.
price None                             Back maps to Decimal("0"), the sim convention.
TradeFee.flat_fees                     (currency, amount) <-> TokenAmount list; lossless.
Fee class                              Always AddedToCostTradeFee (fee charged on top);
                                       DeductedFromReturnsTradeFee is rejected going back.
TradeFee percent_token / fee_asset     DTP-only; dropped on the way back.
=====================================  ===================================================
"""

from __future__ import annotations

from decimal import Decimal

from data_type_primitives import (
    AddedToCostTradeFee,
    OrderState,
    TokenAmount,
    TradeFeeBase,
)
from data_type_primitives import InFlightOrder as CanonicalInFlightOrder

from market_simulator.connector.core import _STATE_MAP
from market_simulator.core.types import InFlightOrder, OrderStatus, TradeFee

__all__ = [
    "in_flight_order_from_canonical",
    "in_flight_order_to_canonical",
    "trade_fee_from_canonical",
    "trade_fee_to_canonical",
]

_REVERSE_STATE_MAP: dict[OrderState, OrderStatus] = {v: k for k, v in _STATE_MAP.items()}


def in_flight_order_to_canonical(order: InFlightOrder) -> CanonicalInFlightOrder:
    """Convert a simulator InFlightOrder to a DTP InFlightOrder (see module docstring for loss)."""
    canonical = CanonicalInFlightOrder(
        client_order_id=order.client_order_id,
        trading_pair=order.trading_pair,
        order_type=order.order_type,
        trade_type=order.trade_type,
        amount=order.amount,
        creation_timestamp=order.creation_timestamp,
        price=order.price,
        exchange_order_id=order.exchange_order_id,
        initial_state=_STATE_MAP[order.status],
        position=order.position_action,
    )
    canonical.executed_amount_base = order.filled_amount
    canonical.executed_amount_quote = order.filled_amount * order.filled_price
    canonical.last_update_timestamp = order.last_update_timestamp
    return canonical


def in_flight_order_from_canonical(
    order: CanonicalInFlightOrder, fee_paid: Decimal = Decimal("0")
) -> InFlightOrder:
    """Convert a DTP InFlightOrder to a simulator one.

    Raises ValueError for OrderStates the simulator cannot represent.
    ``fee_paid`` is supplied by the caller because DTP stores fees per fill.
    """
    try:
        status = _REVERSE_STATE_MAP[order.current_state]
    except KeyError:
        raise ValueError(
            f"OrderState {order.current_state.name} has no simulator equivalent"
        ) from None
    base = order.executed_amount_base
    filled_price = order.executed_amount_quote / base if base > 0 else Decimal("0")
    return InFlightOrder(
        client_order_id=order.client_order_id,
        trading_pair=order.trading_pair,
        order_type=order.order_type,
        trade_type=order.trade_type,
        amount=order.amount,
        price=order.price if order.price is not None else Decimal("0"),
        status=status,
        filled_amount=base,
        filled_price=filled_price,
        fee_paid=fee_paid,
        creation_timestamp=order.creation_timestamp,
        last_update_timestamp=order.last_update_timestamp,
        position_action=order.position,
        exchange_order_id=order.exchange_order_id,
    )


def trade_fee_to_canonical(fee: TradeFee) -> AddedToCostTradeFee:
    """Convert a simulator TradeFee to a DTP AddedToCostTradeFee."""
    return AddedToCostTradeFee(
        percent=fee.percent,
        flat_fees=[TokenAmount(token=token, amount=amount) for token, amount in fee.flat_fees],
    )


def trade_fee_from_canonical(fee: TradeFeeBase) -> TradeFee:
    """Convert a DTP AddedToCostTradeFee to a simulator TradeFee.

    Raises ValueError for other fee classes (their semantics differ).
    """
    if not isinstance(fee, AddedToCostTradeFee):
        raise ValueError(f"Only AddedToCostTradeFee is supported, got {type(fee).__name__}")
    return TradeFee(
        percent=fee.percent,
        flat_fees=[(flat.token, flat.amount) for flat in fee.flat_fees],
    )
