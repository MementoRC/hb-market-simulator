"""Tests for SimulatedOrderBook."""

from decimal import Decimal

import pytest
from market_simulator.core.types import PriceType, TradeType
from market_simulator.simulator.order_book import SimulatedOrderBook


@pytest.fixture
def book() -> SimulatedOrderBook:
    """An order book with a standard BTC-USDT snapshot."""
    ob = SimulatedOrderBook("BTC-USDT")
    ob.apply_snapshot(
        bids=[
            (Decimal("50000"), Decimal("1.0")),
            (Decimal("49900"), Decimal("2.0")),
            (Decimal("49800"), Decimal("3.0")),
        ],
        asks=[
            (Decimal("50100"), Decimal("1.0")),
            (Decimal("50200"), Decimal("2.0")),
            (Decimal("50300"), Decimal("3.0")),
        ],
    )
    return ob


class TestSimulatedOrderBook:
    def test_empty_book(self):
        ob = SimulatedOrderBook("ETH-USDT")
        assert ob.best_bid is None
        assert ob.best_ask is None
        assert ob.mid_price is None
        assert ob.spread is None

    def test_snapshot_prices(self, book: SimulatedOrderBook):
        assert book.best_bid == Decimal("50000")
        assert book.best_ask == Decimal("50100")
        assert book.mid_price == Decimal("50050")
        assert book.spread == Decimal("100")

    def test_get_price_by_type(self, book: SimulatedOrderBook):
        assert book.get_price_by_type(PriceType.BestBid) == Decimal("50000")
        assert book.get_price_by_type(PriceType.BestAsk) == Decimal("50100")
        assert book.get_price_by_type(PriceType.MidPrice) == Decimal("50050")

    def test_get_price_last_trade_unavailable(self, book: SimulatedOrderBook):
        with pytest.raises(ValueError, match="not available"):
            book.get_price_by_type(PriceType.LastTrade)

    def test_apply_diffs_add_level(self, book: SimulatedOrderBook):
        book.apply_diffs(
            bid_diffs=[(Decimal("50050"), Decimal("0.5"))],
            ask_diffs=[],
        )
        assert book.best_bid == Decimal("50050")

    def test_apply_diffs_remove_level(self, book: SimulatedOrderBook):
        book.apply_diffs(
            bid_diffs=[(Decimal("50000"), Decimal("0"))],
            ask_diffs=[],
        )
        assert book.best_bid == Decimal("49900")

    def test_apply_diffs_update_quantity(self, book: SimulatedOrderBook):
        book.apply_diffs(
            bid_diffs=[(Decimal("50000"), Decimal("5.0"))],
            ask_diffs=[],
        )
        assert book.best_bid == Decimal("50000")
        depth = book.get_depth(TradeType.BUY, levels=1)
        assert depth[0] == (Decimal("50000"), Decimal("5.0"))

    def test_apply_trade_buy_consumes_asks(self, book: SimulatedOrderBook):
        book.apply_trade(Decimal("50100"), Decimal("0.5"), TradeType.BUY)
        assert book.last_trade_price == Decimal("50100")
        # Ask at 50100 should have 0.5 remaining
        depth = book.get_depth(TradeType.SELL, levels=1)
        assert depth[0] == (Decimal("50100"), Decimal("0.5"))

    def test_apply_trade_sell_consumes_bids(self, book: SimulatedOrderBook):
        book.apply_trade(Decimal("50000"), Decimal("1.0"), TradeType.SELL)
        # Bid at 50000 fully consumed
        assert book.best_bid == Decimal("49900")

    def test_apply_trade_overconsume(self, book: SimulatedOrderBook):
        book.apply_trade(Decimal("50100"), Decimal("5.0"), TradeType.BUY)
        # Ask at 50100 fully consumed (had 1.0, traded 5.0)
        assert book.best_ask == Decimal("50200")

    def test_get_depth_bids(self, book: SimulatedOrderBook):
        depth = book.get_depth(TradeType.BUY, levels=2)
        assert len(depth) == 2
        assert depth[0][0] == Decimal("50000")  # Best bid first
        assert depth[1][0] == Decimal("49900")

    def test_get_depth_asks(self, book: SimulatedOrderBook):
        depth = book.get_depth(TradeType.SELL, levels=2)
        assert len(depth) == 2
        assert depth[0][0] == Decimal("50100")  # Best ask first
        assert depth[1][0] == Decimal("50200")

    def test_vwap_buy(self, book: SimulatedOrderBook):
        # Buy 2.0 BTC: 1.0 @ 50100 + 1.0 @ 50200 = VWAP 50150
        vwap = book.get_vwap(TradeType.BUY, Decimal("2.0"))
        assert vwap == Decimal("50150")

    def test_vwap_sell(self, book: SimulatedOrderBook):
        # Sell 2.0 BTC: 1.0 @ 50000 + 1.0 @ 49900 = VWAP 49950
        vwap = book.get_vwap(TradeType.SELL, Decimal("2.0"))
        assert vwap == Decimal("49950")

    def test_vwap_insufficient_liquidity(self, book: SimulatedOrderBook):
        vwap = book.get_vwap(TradeType.BUY, Decimal("100.0"))
        assert vwap is None

    def test_volume_for_price_move(self, book: SimulatedOrderBook):
        # Volume available on ask side up to 50200
        vol = book.get_volume_for_price_move(TradeType.BUY, Decimal("50200"))
        assert vol == Decimal("3.0")  # 1.0 @ 50100 + 2.0 @ 50200

    def test_snapshot_replaces_previous(self, book: SimulatedOrderBook):
        book.apply_snapshot(
            bids=[(Decimal("60000"), Decimal("1.0"))],
            asks=[(Decimal("60100"), Decimal("1.0"))],
        )
        assert book.best_bid == Decimal("60000")
        assert book.best_ask == Decimal("60100")
        assert len(book.get_depth(TradeType.BUY, levels=100)) == 1

    def test_repr(self, book: SimulatedOrderBook):
        r = repr(book)
        assert "BTC-USDT" in r
        assert "bids=3" in r
        assert "asks=3" in r
