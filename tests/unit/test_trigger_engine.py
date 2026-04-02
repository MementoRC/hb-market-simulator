"""Tests for TriggerEngine implementations.

Covers StandardTriggerEngine (stop-loss, take-profit, trailing-stop) and
NullTriggerEngine. Each test creates an InFlightOrder with the appropriate
fields and calls engine.evaluate() against a hand-crafted snapshot.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from market_simulator.core.types import (
    InFlightOrder,
    OrderStatus,
    OrderType,
    TradeType,
)
from market_simulator.simulator.trigger_engine import (
    NullTriggerEngine,
    StandardTriggerEngine,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_order(
    order_type: OrderType,
    trade_type: TradeType,
    trigger_price: str | None = None,
    trail_amount: str | None = None,
    order_id: str = "ORD-001",
) -> InFlightOrder:
    """Construct a minimal InFlightOrder for trigger evaluation."""
    return InFlightOrder(
        client_order_id=order_id,
        trading_pair="BTC-USDT",
        order_type=order_type,
        trade_type=trade_type,
        amount=Decimal("1.0"),
        price=Decimal("50000"),
        status=OrderStatus.OPEN,
        trigger_price=Decimal(trigger_price) if trigger_price is not None else None,
        trail_amount=Decimal(trail_amount) if trail_amount is not None else None,
    )


def _make_snapshot(best_bid: str, best_ask: str) -> dict:
    """Build a minimal OrderBookSnapshot with best_bid and best_ask."""
    return {
        "best_bid": Decimal(best_bid),
        "best_ask": Decimal(best_ask),
        "last_trade": None,
    }


def _make_empty_snapshot() -> dict:
    """Snapshot with no quotes available."""
    return {"best_bid": None, "best_ask": None, "last_trade": None}


# ---------------------------------------------------------------------------
# TestStopLoss
# ---------------------------------------------------------------------------


class TestStopLoss:
    """StandardTriggerEngine: STOP_LOSS trigger conditions."""

    def test_buy_stop_loss_fires_when_ask_at_trigger(self):
        """BUY stop-loss fires when best_ask reaches trigger_price."""
        engine = StandardTriggerEngine()
        order = _make_order(OrderType.STOP_LOSS, TradeType.BUY, trigger_price="51000")
        snapshot = _make_snapshot(best_bid="50900", best_ask="51000")
        assert engine.evaluate(order, snapshot, timestamp=1.0) is True

    def test_buy_stop_loss_fires_when_ask_above_trigger(self):
        """BUY stop-loss fires when best_ask exceeds trigger_price."""
        engine = StandardTriggerEngine()
        order = _make_order(OrderType.STOP_LOSS, TradeType.BUY, trigger_price="51000")
        snapshot = _make_snapshot(best_bid="50900", best_ask="51500")
        assert engine.evaluate(order, snapshot, timestamp=1.0) is True

    def test_buy_stop_loss_does_not_fire_below_trigger(self):
        """BUY stop-loss does NOT fire when best_ask is below trigger_price."""
        engine = StandardTriggerEngine()
        order = _make_order(OrderType.STOP_LOSS, TradeType.BUY, trigger_price="51000")
        snapshot = _make_snapshot(best_bid="49900", best_ask="50100")
        assert engine.evaluate(order, snapshot, timestamp=1.0) is False

    def test_sell_stop_loss_fires_when_bid_at_trigger(self):
        """SELL stop-loss fires when best_bid drops to trigger_price."""
        engine = StandardTriggerEngine()
        order = _make_order(OrderType.STOP_LOSS, TradeType.SELL, trigger_price="49000")
        snapshot = _make_snapshot(best_bid="49000", best_ask="49100")
        assert engine.evaluate(order, snapshot, timestamp=1.0) is True

    def test_sell_stop_loss_does_not_fire_above_trigger(self):
        """SELL stop-loss does NOT fire when best_bid is above trigger_price."""
        engine = StandardTriggerEngine()
        order = _make_order(OrderType.STOP_LOSS, TradeType.SELL, trigger_price="49000")
        snapshot = _make_snapshot(best_bid="50000", best_ask="50100")
        assert engine.evaluate(order, snapshot, timestamp=1.0) is False


# ---------------------------------------------------------------------------
# TestTakeProfit
# ---------------------------------------------------------------------------


class TestTakeProfit:
    """StandardTriggerEngine: TAKE_PROFIT trigger conditions."""

    def test_buy_take_profit_fires_when_ask_at_trigger(self):
        """BUY take-profit fires when best_ask drops to trigger_price."""
        engine = StandardTriggerEngine()
        order = _make_order(OrderType.TAKE_PROFIT, TradeType.BUY, trigger_price="49000")
        snapshot = _make_snapshot(best_bid="48900", best_ask="49000")
        assert engine.evaluate(order, snapshot, timestamp=1.0) is True

    def test_buy_take_profit_fires_when_ask_below_trigger(self):
        """BUY take-profit fires when best_ask is below trigger_price."""
        engine = StandardTriggerEngine()
        order = _make_order(OrderType.TAKE_PROFIT, TradeType.BUY, trigger_price="49000")
        snapshot = _make_snapshot(best_bid="48400", best_ask="48500")
        assert engine.evaluate(order, snapshot, timestamp=1.0) is True

    def test_buy_take_profit_does_not_fire_above_trigger(self):
        """BUY take-profit does NOT fire when best_ask is above trigger_price."""
        engine = StandardTriggerEngine()
        order = _make_order(OrderType.TAKE_PROFIT, TradeType.BUY, trigger_price="49000")
        snapshot = _make_snapshot(best_bid="50000", best_ask="50100")
        assert engine.evaluate(order, snapshot, timestamp=1.0) is False

    def test_sell_take_profit_fires_when_bid_at_trigger(self):
        """SELL take-profit fires when best_bid rises to trigger_price."""
        engine = StandardTriggerEngine()
        order = _make_order(OrderType.TAKE_PROFIT, TradeType.SELL, trigger_price="51000")
        snapshot = _make_snapshot(best_bid="51000", best_ask="51100")
        assert engine.evaluate(order, snapshot, timestamp=1.0) is True

    def test_sell_take_profit_does_not_fire_below_trigger(self):
        """SELL take-profit does NOT fire when best_bid is below trigger_price."""
        engine = StandardTriggerEngine()
        order = _make_order(OrderType.TAKE_PROFIT, TradeType.SELL, trigger_price="51000")
        snapshot = _make_snapshot(best_bid="50000", best_ask="50100")
        assert engine.evaluate(order, snapshot, timestamp=1.0) is False


# ---------------------------------------------------------------------------
# TestTrailingStop
# ---------------------------------------------------------------------------


class TestTrailingStop:
    """StandardTriggerEngine: TRAILING_STOP trigger conditions."""

    def test_buy_trailing_stop_initialises_watermark_from_trigger_price(self):
        """On first evaluation the watermark is set to trigger_price, not current ask."""
        engine = StandardTriggerEngine()
        # trigger_price = 50000, trail_amount = 500; ask is 50100 (above trigger)
        # Watermark init = 50000; ask (50100) >= 50000 + 500 = 50500? No → False
        order = _make_order(
            OrderType.TRAILING_STOP, TradeType.BUY,
            trigger_price="50000", trail_amount="500",
        )
        snapshot = _make_snapshot(best_bid="49900", best_ask="50100")
        assert engine.evaluate(order, snapshot, timestamp=1.0) is False

    def test_buy_trailing_stop_tracks_lower_ask(self):
        """Watermark moves down when ask drops below the current reference."""
        engine = StandardTriggerEngine()
        order = _make_order(
            OrderType.TRAILING_STOP, TradeType.BUY,
            trigger_price="50000", trail_amount="500",
            order_id="ORD-TRAIL-BUY",
        )
        # First tick: ask = 50000 → watermark stays 50000 (equal, not lower)
        engine.evaluate(order, _make_snapshot("49900", "50000"), timestamp=1.0)
        # Second tick: ask drops to 49500 → watermark moves down to 49500
        engine.evaluate(order, _make_snapshot("49400", "49500"), timestamp=2.0)
        # Fire threshold is now 49500 + 500 = 50000; ask = 49600 < 50000 → still False
        result = engine.evaluate(order, _make_snapshot("49500", "49600"), timestamp=3.0)
        assert result is False
        # Internal reference should be 49500
        assert engine._trailing_references["ORD-TRAIL-BUY"] == Decimal("49500")

    def test_buy_trailing_stop_fires_when_ask_rises_above_watermark_plus_trail(self):
        """BUY trailing stop fires when ask rises >= lowest_ask + trail_amount."""
        engine = StandardTriggerEngine()
        order = _make_order(
            OrderType.TRAILING_STOP, TradeType.BUY,
            trigger_price="49000", trail_amount="500",
            order_id="ORD-TRAIL-BUY2",
        )
        # First tick: ask 48500 → watermark moves to 48500 (lower than trigger 49000)
        engine.evaluate(order, _make_snapshot("48400", "48500"), timestamp=1.0)
        # Watermark = 48500; fire when ask >= 48500 + 500 = 49000
        result = engine.evaluate(order, _make_snapshot("48900", "49000"), timestamp=2.0)
        assert result is True

    def test_buy_trailing_stop_does_not_fire_below_watermark_plus_trail(self):
        """BUY trailing stop does not fire when ask has not risen enough."""
        engine = StandardTriggerEngine()
        order = _make_order(
            OrderType.TRAILING_STOP, TradeType.BUY,
            trigger_price="49000", trail_amount="1000",
            order_id="ORD-TRAIL-BUY3",
        )
        # Watermark initialised to trigger_price 49000; need ask >= 50000 to fire
        result = engine.evaluate(order, _make_snapshot("49500", "49800"), timestamp=1.0)
        assert result is False

    def test_sell_trailing_stop_initialises_watermark_from_trigger_price(self):
        """On first evaluation the watermark is set to trigger_price for SELL."""
        engine = StandardTriggerEngine()
        # trigger_price = 51000, trail_amount = 500; bid = 50000
        # Watermark = 51000; fire when bid <= 51000 - 500 = 50500? bid=50000 ≤ 50500 → True
        # Use a bid that does NOT cross: bid = 50600
        order = _make_order(
            OrderType.TRAILING_STOP, TradeType.SELL,
            trigger_price="51000", trail_amount="500",
            order_id="ORD-TRAIL-SELL1",
        )
        snapshot = _make_snapshot(best_bid="50600", best_ask="50700")
        assert engine.evaluate(order, snapshot, timestamp=1.0) is False

    def test_sell_trailing_stop_tracks_higher_bid(self):
        """Watermark moves up when bid rises above the current reference."""
        engine = StandardTriggerEngine()
        order = _make_order(
            OrderType.TRAILING_STOP, TradeType.SELL,
            trigger_price="51000", trail_amount="500",
            order_id="ORD-TRAIL-SELL2",
        )
        # First tick: bid = 51500 → watermark rises to 51500
        engine.evaluate(order, _make_snapshot("51500", "51600"), timestamp=1.0)
        assert engine._trailing_references["ORD-TRAIL-SELL2"] == Decimal("51500")
        # Second tick: bid = 52000 → watermark rises to 52000
        engine.evaluate(order, _make_snapshot("52000", "52100"), timestamp=2.0)
        assert engine._trailing_references["ORD-TRAIL-SELL2"] == Decimal("52000")

    def test_sell_trailing_stop_fires_when_bid_drops_by_trail_amount(self):
        """SELL trailing stop fires when bid drops >= trail_amount from high watermark."""
        engine = StandardTriggerEngine()
        order = _make_order(
            OrderType.TRAILING_STOP, TradeType.SELL,
            trigger_price="51000", trail_amount="500",
            order_id="ORD-TRAIL-SELL3",
        )
        # First tick: bid = 52000 → watermark = 52000
        engine.evaluate(order, _make_snapshot("52000", "52100"), timestamp=1.0)
        # Fire threshold = 52000 - 500 = 51500; bid = 51500 → should fire
        result = engine.evaluate(order, _make_snapshot("51500", "51600"), timestamp=2.0)
        assert result is True


# ---------------------------------------------------------------------------
# TestEdgeCases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Edge cases: non-conditional order types and missing quotes."""

    def test_non_conditional_order_returns_false(self):
        """StandardTriggerEngine returns False for plain LIMIT orders."""
        engine = StandardTriggerEngine()
        order = _make_order(OrderType.LIMIT, TradeType.BUY)
        snapshot = _make_snapshot("50000", "50100")
        assert engine.evaluate(order, snapshot, timestamp=1.0) is False

    def test_missing_best_ask_returns_false_for_buy_stop_loss(self):
        """Returns False when best_ask is absent from the snapshot."""
        engine = StandardTriggerEngine()
        order = _make_order(OrderType.STOP_LOSS, TradeType.BUY, trigger_price="50000")
        snapshot = {"best_bid": Decimal("49900"), "best_ask": None, "last_trade": None}
        assert engine.evaluate(order, snapshot, timestamp=1.0) is False

    def test_null_trigger_engine_always_returns_false(self):
        """NullTriggerEngine never fires regardless of order type or price."""
        engine = NullTriggerEngine()
        for order_type in (
            OrderType.STOP_LOSS,
            OrderType.TAKE_PROFIT,
            OrderType.TRAILING_STOP,
        ):
            order = _make_order(
                order_type, TradeType.SELL,
                trigger_price="1",
                trail_amount="1",
            )
            snapshot = _make_snapshot("1", "1")
            assert engine.evaluate(order, snapshot, timestamp=1.0) is False
