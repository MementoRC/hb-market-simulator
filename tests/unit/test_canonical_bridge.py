"""Tests for the core <-> data_type_primitives bridge (hb-market-simulator #67)."""

from decimal import Decimal

import pytest
from data_type_primitives import (
    AddedToCostTradeFee,
    DeductedFromReturnsTradeFee,
    OrderState,
    TokenAmount,
)
from data_type_primitives import InFlightOrder as CanonicalInFlightOrder

from market_simulator.connector.core import _STATE_MAP
from market_simulator.core.types import (
    InFlightOrder,
    OrderStatus,
    OrderType,
    PositionAction,
    TradeFee,
    TradeType,
)
from market_simulator.hb_compat import canonical
from market_simulator.hb_compat.canonical import (
    in_flight_order_from_canonical,
    in_flight_order_to_canonical,
    trade_fee_from_canonical,
    trade_fee_to_canonical,
)


def _order(status: OrderStatus = OrderStatus.OPEN, **overrides) -> InFlightOrder:
    fields = {
        "client_order_id": "c1",
        "trading_pair": "BTC-USDT",
        "order_type": OrderType.LIMIT,
        "trade_type": TradeType.BUY,
        "amount": Decimal("2"),
        "price": Decimal("100"),
        "status": status,
        "filled_amount": Decimal("1"),
        "filled_price": Decimal("99.5"),
        "creation_timestamp": 10.0,
        "last_update_timestamp": 12.5,
        "position_action": PositionAction.OPEN,
        "exchange_order_id": "ex1",
    }
    fields.update(overrides)
    return InFlightOrder(**fields)


@pytest.mark.parametrize("status", list(OrderStatus))
def test_to_canonical_maps_every_status(status):
    sim = _order(status)
    out = in_flight_order_to_canonical(sim)
    assert isinstance(out, CanonicalInFlightOrder)
    assert out.current_state is _STATE_MAP[status]
    assert out.client_order_id == "c1"
    assert out.trading_pair == "BTC-USDT"
    assert out.order_type is OrderType.LIMIT
    assert out.trade_type is TradeType.BUY
    assert out.amount == Decimal("2")
    assert out.price == Decimal("100")
    assert out.executed_amount_base == Decimal("1")
    assert out.executed_amount_quote == Decimal("99.5")
    assert out.creation_timestamp == 10.0
    assert out.last_update_timestamp == 12.5
    assert out.position is PositionAction.OPEN
    assert out.exchange_order_id == "ex1"


@pytest.mark.parametrize("status", list(OrderStatus))
def test_round_trip_sim_to_canonical_to_sim(status):
    sim = _order(status)
    assert in_flight_order_from_canonical(in_flight_order_to_canonical(sim)) == sim


@pytest.mark.parametrize("status", list(OrderStatus))
def test_round_trip_canonical_to_sim_to_canonical(status):
    canon = in_flight_order_to_canonical(_order(status))
    assert in_flight_order_to_canonical(in_flight_order_from_canonical(canon)) == canon


def test_unfilled_order_round_trip_has_zero_vwap():
    sim = _order(OrderStatus.PENDING_CREATE, filled_amount=Decimal("0"), filled_price=Decimal("0"))
    assert in_flight_order_from_canonical(in_flight_order_to_canonical(sim)) == sim


@pytest.mark.parametrize(
    "state",
    [OrderState.PENDING_APPROVAL, OrderState.APPROVED, OrderState.CREATED, OrderState.COMPLETED],
)
def test_from_canonical_rejects_states_without_sim_equivalent(state):
    canon = in_flight_order_to_canonical(_order())
    canon.current_state = state
    with pytest.raises(ValueError, match=state.name):
        in_flight_order_from_canonical(canon)


def test_lossy_conditional_fields_dropped_without_crash():
    sim = _order(
        order_type=OrderType.TRAILING_STOP,
        trigger_price=Decimal("90"),
        trail_amount=Decimal("5"),
        watermark=Decimal("110"),
    )
    canon = in_flight_order_to_canonical(sim)
    assert canon.order_type is OrderType.TRAILING_STOP
    for name in ("trigger_price", "trail_amount", "watermark"):
        assert not hasattr(canon, name)
    back = in_flight_order_from_canonical(canon)
    assert (back.trigger_price, back.trail_amount, back.watermark) == (None, None, None)


def test_lossy_fee_paid_dropped_and_restorable():
    sim = _order(fee_paid=Decimal("0.3"))
    back = in_flight_order_from_canonical(in_flight_order_to_canonical(sim))
    assert back.fee_paid == Decimal("0")
    assert (
        in_flight_order_from_canonical(in_flight_order_to_canonical(sim), fee_paid=Decimal("0.3"))
        == sim
    )


def test_market_order_without_price_maps_to_zero():
    canon = in_flight_order_to_canonical(_order(order_type=OrderType.MARKET))
    canon.price = None
    assert in_flight_order_from_canonical(canon).price == Decimal("0")


def test_lossy_fields_are_documented():
    doc = canonical.__doc__
    for term in (
        "trigger_price",
        "trail_amount",
        "watermark",
        "VWAP",
        "OrderStatus vs OrderState",
        "PENDING_APPROVAL",
        "APPROVED",
        "CREATED",
        "COMPLETED",
        "fee_paid",
    ):
        assert term in doc


def test_fee_percent_only():
    out = trade_fee_to_canonical(TradeFee(percent=Decimal("0.001")))
    assert isinstance(out, AddedToCostTradeFee)
    assert out.percent == Decimal("0.001")
    assert out.flat_fees == []


def test_fee_flat_fees_round_trip():
    fee = TradeFee(
        percent=Decimal("0.002"), flat_fees=[("USDT", Decimal("1.5")), ("BNB", Decimal("0.01"))]
    )
    out = trade_fee_to_canonical(fee)
    assert out.flat_fees == [
        TokenAmount(token="USDT", amount=Decimal("1.5")),
        TokenAmount(token="BNB", amount=Decimal("0.01")),
    ]
    assert trade_fee_from_canonical(out) == fee


def test_fee_canonical_round_trip():
    canon = AddedToCostTradeFee(
        percent=Decimal("0.01"), flat_fees=[TokenAmount(token="USDT", amount=Decimal("2"))]
    )
    back = trade_fee_to_canonical(trade_fee_from_canonical(canon))
    assert back.percent == canon.percent
    assert back.flat_fees == canon.flat_fees


def test_fee_from_canonical_rejects_deducted_from_returns():
    with pytest.raises(ValueError, match="AddedToCostTradeFee"):
        trade_fee_from_canonical(DeductedFromReturnsTradeFee(percent=Decimal("0.01")))
