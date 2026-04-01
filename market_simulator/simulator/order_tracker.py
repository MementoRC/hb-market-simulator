"""Order tracker for managing in-flight orders.

Tracks open orders, records trade fills, and manages order state transitions.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Dict, List, Optional

from market_simulator.core.types import InFlightOrder, OrderStatus, OrderType, PositionAction, TradeType


class OrderTracker:
    """Tracks all in-flight orders and their state transitions."""

    def __init__(self) -> None:
        self._orders: Dict[str, InFlightOrder] = {}
        self._order_counter: int = 0

    def generate_order_id(self, prefix: str = "SIM") -> str:
        """Generate a unique client order ID."""
        self._order_counter += 1
        return f"{prefix}-{self._order_counter:08d}"

    def create_order(
        self,
        trading_pair: str,
        order_type: OrderType,
        trade_type: TradeType,
        amount: Decimal,
        price: Decimal,
        timestamp: float = 0.0,
        position_action: PositionAction = PositionAction.NIL,
        client_order_id: Optional[str] = None,
    ) -> InFlightOrder:
        """Create and track a new order."""
        order_id = client_order_id or self.generate_order_id()
        order = InFlightOrder(
            client_order_id=order_id,
            trading_pair=trading_pair,
            order_type=order_type,
            trade_type=trade_type,
            amount=amount,
            price=price,
            status=OrderStatus.PENDING_CREATE,
            creation_timestamp=timestamp,
            last_update_timestamp=timestamp,
            position_action=position_action,
        )
        self._orders[order_id] = order
        return order

    def get_order(self, client_order_id: str) -> Optional[InFlightOrder]:
        """Get an order by client order ID."""
        return self._orders.get(client_order_id)

    def open_order(self, client_order_id: str, timestamp: float = 0.0) -> None:
        """Transition order from PENDING_CREATE to OPEN."""
        order = self._orders.get(client_order_id)
        if order and order.status == OrderStatus.PENDING_CREATE:
            order.status = OrderStatus.OPEN
            order.last_update_timestamp = timestamp

    def cancel_order(self, client_order_id: str, timestamp: float = 0.0) -> Optional[InFlightOrder]:
        """Cancel an open order. Returns the order if found and cancellable."""
        order = self._orders.get(client_order_id)
        if order and order.is_open:
            order.status = OrderStatus.CANCELLED
            order.last_update_timestamp = timestamp
            return order
        return None

    def fail_order(self, client_order_id: str, timestamp: float = 0.0) -> Optional[InFlightOrder]:
        """Mark an order as failed."""
        order = self._orders.get(client_order_id)
        if order and order.is_open:
            order.status = OrderStatus.FAILED
            order.last_update_timestamp = timestamp
            return order
        return None

    @property
    def open_orders(self) -> List[InFlightOrder]:
        """Get all currently open orders."""
        return [o for o in self._orders.values() if o.is_open]

    @property
    def open_buy_orders(self) -> List[InFlightOrder]:
        return [o for o in self.open_orders if o.trade_type == TradeType.BUY]

    @property
    def open_sell_orders(self) -> List[InFlightOrder]:
        return [o for o in self.open_orders if o.trade_type == TradeType.SELL]

    @property
    def all_orders(self) -> Dict[str, InFlightOrder]:
        """Get all tracked orders."""
        return dict(self._orders)

    @property
    def in_flight_orders(self) -> Dict[str, InFlightOrder]:
        """Get orders that are still in-flight (not done)."""
        return {oid: o for oid, o in self._orders.items() if not o.is_done}

    def cleanup_done_orders(self) -> int:
        """Remove completed/cancelled/failed orders from tracking.

        Returns the number of orders removed.
        """
        done_ids = [oid for oid, o in self._orders.items() if o.is_done]
        for oid in done_ids:
            del self._orders[oid]
        return len(done_ids)
