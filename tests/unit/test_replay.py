"""Tests for the replay system: data loading, replay source, event application."""

import tempfile
from decimal import Decimal
from pathlib import Path

import pytest
from market_simulator.core.types import TradeType
from market_simulator.replay.data_loader import (
    auto_load,
    load_candles_csv,
    load_order_book_snapshots_csv,
    load_trades_csv,
)
from market_simulator.replay.data_types import ReplayEventType
from market_simulator.replay.replay_source import ReplayDataSource, apply_replay_event
from market_simulator.simulator.order_book import SimulatedOrderBook

# ---------------------------------------------------------------------------
# Fixtures: temporary CSV files
# ---------------------------------------------------------------------------

@pytest.fixture
def trades_csv(tmp_path) -> Path:
    p = tmp_path / "trades.csv"
    p.write_text(
        "timestamp,trading_pair,trade_id,price,amount,is_buyer_maker\n"
        "1000.0,BTC-USDT,t1,50100,0.5,false\n"
        "1001.0,BTC-USDT,t2,50050,1.0,true\n"
        "1002.0,BTC-USDT,t3,50200,0.2,false\n"
    )
    return p


@pytest.fixture
def candles_csv(tmp_path) -> Path:
    p = tmp_path / "candles.csv"
    p.write_text(
        "timestamp,trading_pair,open,high,low,close,volume,interval\n"
        "1000.0,BTC-USDT,50000,50500,49800,50100,100.5,1m\n"
        "1060.0,BTC-USDT,50100,50300,49900,50200,80.2,1m\n"
    )
    return p


@pytest.fixture
def order_book_csv(tmp_path) -> Path:
    p = tmp_path / "order_book.csv"
    p.write_text(
        "timestamp,trading_pair,side,price,quantity\n"
        "1000.0,BTC-USDT,bid,50000,1.0\n"
        "1000.0,BTC-USDT,bid,49900,2.0\n"
        "1000.0,BTC-USDT,ask,50100,1.0\n"
        "1000.0,BTC-USDT,ask,50200,2.0\n"
        "1060.0,BTC-USDT,bid,50050,1.5\n"
        "1060.0,BTC-USDT,ask,50150,1.5\n"
    )
    return p


# ---------------------------------------------------------------------------
# Data loader tests
# ---------------------------------------------------------------------------

class TestLoadTradesCsv:
    def test_load(self, trades_csv):
        trades = load_trades_csv(trades_csv)
        assert len(trades) == 3
        assert trades[0]["price"] == Decimal("50100")
        assert trades[0]["timestamp"] == 1000.0
        assert trades[0]["is_buyer_maker"] is False

    def test_sorted_by_timestamp(self, trades_csv):
        trades = load_trades_csv(trades_csv)
        timestamps = [t["timestamp"] for t in trades]
        assert timestamps == sorted(timestamps)

    def test_override_trading_pair(self, trades_csv):
        trades = load_trades_csv(trades_csv, trading_pair="ETH-USDT")
        assert all(t["trading_pair"] == "ETH-USDT" for t in trades)


class TestLoadCandlesCsv:
    def test_load(self, candles_csv):
        candles = load_candles_csv(candles_csv)
        assert len(candles) == 2
        assert candles[0]["open"] == Decimal("50000")
        assert candles[0]["close"] == Decimal("50100")
        assert candles[0]["interval"] == "1m"

    def test_sorted(self, candles_csv):
        candles = load_candles_csv(candles_csv)
        assert candles[0]["timestamp"] < candles[1]["timestamp"]


class TestLoadOrderBookSnapshotsCsv:
    def test_load(self, order_book_csv):
        snapshots = load_order_book_snapshots_csv(order_book_csv)
        assert len(snapshots) == 2

        # First snapshot at t=1000
        s0 = snapshots[0]
        assert s0["timestamp"] == 1000.0
        assert len(s0["bids"]) == 2
        assert len(s0["asks"]) == 2
        assert s0["bids"][0] == (Decimal("50000"), Decimal("1.0"))

        # Second snapshot at t=1060
        s1 = snapshots[1]
        assert len(s1["bids"]) == 1
        assert len(s1["asks"]) == 1


class TestAutoLoad:
    def test_auto_detect_csv_trades(self, trades_csv):
        data = auto_load(trades_csv, "trades")
        assert len(data) == 3

    def test_auto_detect_csv_candles(self, candles_csv):
        data = auto_load(candles_csv, "candles")
        assert len(data) == 2

    def test_unknown_format_raises(self, tmp_path):
        p = tmp_path / "data.xyz"
        p.write_text("dummy")
        with pytest.raises(ValueError, match="No loader"):
            auto_load(p, "trades")


# ---------------------------------------------------------------------------
# ReplayDataSource tests
# ---------------------------------------------------------------------------

class TestReplayDataSource:
    def test_add_trades(self, trades_csv):
        trades = load_trades_csv(trades_csv)
        source = ReplayDataSource()
        source.add_trades(trades)
        assert source.event_count == 3

    def test_events_ordered(self, trades_csv, candles_csv):
        trades = load_trades_csv(trades_csv)
        candles = load_candles_csv(candles_csv)
        source = ReplayDataSource()
        source.add_trades(trades)
        source.add_candles(candles, generate_order_book=False)

        events = list(source.events())
        timestamps = [e.timestamp for e in events]
        assert timestamps == sorted(timestamps)

    def test_time_range(self, trades_csv):
        trades = load_trades_csv(trades_csv)
        source = ReplayDataSource()
        source.add_trades(trades)
        assert source.time_range == (1000.0, 1002.0)

    def test_time_filter(self, trades_csv):
        trades = load_trades_csv(trades_csv)
        source = ReplayDataSource()
        source.add_trades(trades)

        events = list(source.events(start_time=1001.0, end_time=1001.5))
        assert len(events) == 1
        assert events[0].timestamp == 1001.0

    def test_empty_source(self):
        source = ReplayDataSource()
        assert source.event_count == 0
        assert source.time_range is None
        assert list(source.events()) == []

    def test_candle_generates_order_book(self, candles_csv):
        candles = load_candles_csv(candles_csv)
        source = ReplayDataSource()
        source.add_candles(candles, generate_order_book=True)

        # 2 candles + 2 synthetic order book snapshots = 4 events
        assert source.event_count == 4

        ob_events = [e for e in source.events() if e.event_type == ReplayEventType.ORDER_BOOK_SNAPSHOT]
        assert len(ob_events) == 2

        # Check synthetic book has bids and asks
        snapshot = ob_events[0]
        assert len(snapshot.data["bids"]) > 0
        assert len(snapshot.data["asks"]) > 0

    def test_add_order_book_snapshots(self, order_book_csv):
        snapshots = load_order_book_snapshots_csv(order_book_csv)
        source = ReplayDataSource()
        source.add_order_book_snapshots(snapshots)
        assert source.event_count == 2


# ---------------------------------------------------------------------------
# apply_replay_event tests
# ---------------------------------------------------------------------------

class TestApplyReplayEvent:
    def test_apply_snapshot(self):
        book = SimulatedOrderBook("BTC-USDT")
        books = {"BTC-USDT": book}

        source = ReplayDataSource()
        source.add_order_book_snapshots([{
            "timestamp": 1000.0,
            "trading_pair": "BTC-USDT",
            "bids": [(Decimal("50000"), Decimal("1.0"))],
            "asks": [(Decimal("50100"), Decimal("1.0"))],
        }])

        for event in source.events():
            apply_replay_event(event, books)

        assert book.best_bid == Decimal("50000")
        assert book.best_ask == Decimal("50100")

    def test_apply_trade(self):
        book = SimulatedOrderBook("BTC-USDT")
        book.apply_snapshot(
            bids=[(Decimal("50000"), Decimal("5.0"))],
            asks=[(Decimal("50100"), Decimal("5.0"))],
        )
        books = {"BTC-USDT": book}

        source = ReplayDataSource()
        source.add_trades([{
            "timestamp": 1000.0,
            "trading_pair": "BTC-USDT",
            "trade_id": "t1",
            "price": Decimal("50100"),
            "amount": Decimal("2.0"),
            "is_buyer_maker": False,  # buy aggressor consumes asks
        }])

        for event in source.events():
            apply_replay_event(event, books)

        assert book.last_trade_price == Decimal("50100")
        # 5.0 - 2.0 = 3.0 remaining at 50100
        depth = book.get_depth(TradeType.SELL, levels=1)
        assert depth[0] == (Decimal("50100"), Decimal("3.0"))

    def test_unknown_pair_ignored(self):
        books = {"ETH-USDT": SimulatedOrderBook("ETH-USDT")}
        source = ReplayDataSource()
        source.add_trades([{
            "timestamp": 1000.0,
            "trading_pair": "BTC-USDT",
            "trade_id": "t1",
            "price": Decimal("50100"),
            "amount": Decimal("1.0"),
            "is_buyer_maker": False,
        }])

        # Should not raise
        for event in source.events():
            apply_replay_event(event, books)

    def test_full_replay_flow(self, candles_csv):
        """End-to-end: load candles -> generate synthetic book -> apply to order book."""
        candles = load_candles_csv(candles_csv)
        source = ReplayDataSource()
        source.add_candles(candles, generate_order_book=True, spread_bps=Decimal("10"))

        book = SimulatedOrderBook("BTC-USDT")
        books = {"BTC-USDT": book}

        for event in source.events():
            apply_replay_event(event, books)

        # After replaying both candles, book should have prices around 50200 (last close)
        assert book.best_bid is not None
        assert book.best_ask is not None
        assert book.best_bid < book.best_ask
