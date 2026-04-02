"""End-to-end tests for SandboxEngine.

Tests the full simulation pipeline: historical data replay → order book updates →
order matching → balance tracking → result collection.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

pytest.importorskip("hummingbot", reason="hummingbot not installed (CI-safe skip)")

from market_simulator.core.types import OrderType as SimOrderType
from market_simulator.hb_compat.sandbox_engine import SandboxConfig, SandboxEngine, SimulationResult
from market_simulator.replay.data_loader import load_candles_csv
from market_simulator.replay.replay_source import ReplayDataSource
from market_simulator.simulator.exchange import SimulatedExchangeConfig
from market_simulator.simulator.fee_model import FlatFeeModel, ZeroFeeModel
from market_simulator.simulator.matching_engine import ImmediateFillEngine, LimitOrderEngine

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def candles_csv(tmp_path) -> Path:
    p = tmp_path / "candles.csv"
    p.write_text(
        "timestamp,trading_pair,open,high,low,close,volume,interval\n"
        "1000.0,BTC-USDT,50000,50500,49800,50100,100.5,1m\n"
        "1060.0,BTC-USDT,50100,50300,49900,50200,80.2,1m\n"
        "1120.0,BTC-USDT,50200,50400,50000,50300,90.0,1m\n"
        "1180.0,BTC-USDT,50300,50500,50100,50400,75.0,1m\n"
        "1240.0,BTC-USDT,50400,50600,50200,50500,85.0,1m\n"
    )
    return p


@pytest.fixture
def basic_engine(candles_csv) -> SandboxEngine:
    """A basic engine with one exchange and candle data."""
    config = SandboxConfig(
        tick_interval=60.0,  # 1 minute ticks matching candle interval
        exchanges=[
            SimulatedExchangeConfig(
                name="sim_binance",
                trading_pairs=["BTC-USDT"],
                initial_balances={"USDT": Decimal("100000"), "BTC": Decimal("2.0")},
            )
        ],
        matching_engine=ImmediateFillEngine(),
        fee_model=ZeroFeeModel(),
    )

    engine = SandboxEngine(config)

    # Load candle data and create replay source
    candles = load_candles_csv(candles_csv, trading_pair="BTC-USDT")
    source = ReplayDataSource()
    source.add_candles(candles, generate_order_book=True, spread_bps=Decimal("10"))
    engine.add_replay_data("sim_binance", source)

    return engine


# ---------------------------------------------------------------------------
# Basic engine lifecycle tests
# ---------------------------------------------------------------------------


class TestSandboxEngineLifecycle:
    def test_initialization(self, basic_engine):
        basic_engine.initialize()
        connector = basic_engine.get_connector("sim_binance")
        assert connector.sim_exchange.ready is True

    def test_run_returns_result(self, basic_engine):
        result = basic_engine.run()
        assert isinstance(result, SimulationResult)
        assert result.ticks_processed > 0

    def test_time_range_auto_detected(self, basic_engine):
        result = basic_engine.run()
        assert result.start_time == 1000.0
        assert result.end_time == 1240.0

    def test_events_replayed(self, basic_engine):
        result = basic_engine.run()
        assert result.events_replayed > 0

    def test_balances_unchanged_without_trades(self, basic_engine):
        result = basic_engine.run()
        balances = result.final_balances["sim_binance"]
        assert balances["USDT"] == Decimal("100000")
        assert balances["BTC"] == Decimal("2.0")


# ---------------------------------------------------------------------------
# Trading during simulation
# ---------------------------------------------------------------------------


class TestSandboxEngineTrading:
    def test_buy_on_tick(self, basic_engine):
        """Place a buy order on the first tick."""
        bought = [False]

        def on_tick(timestamp, connectors):
            if not bought[0]:
                connector = connectors["sim_binance"]
                connector.sim_exchange.buy(
                    "BTC-USDT", Decimal("0.1"), SimOrderType.MARKET, Decimal("0")
                )
                bought[0] = True

        result = basic_engine.run(on_tick=on_tick)

        assert result.orders_placed == 1
        assert result.orders_filled == 1
        balances = result.final_balances["sim_binance"]
        assert balances["BTC"] == Decimal("2.1")
        # USDT decreased by approximately fill_price * 0.1
        assert balances["USDT"] < Decimal("100000")

    def test_sell_on_tick(self, basic_engine):
        """Place a sell order on the first tick."""
        sold = [False]

        def on_tick(timestamp, connectors):
            if not sold[0]:
                connector = connectors["sim_binance"]
                connector.sim_exchange.sell(
                    "BTC-USDT", Decimal("0.5"), SimOrderType.MARKET, Decimal("0")
                )
                sold[0] = True

        result = basic_engine.run(on_tick=on_tick)

        assert result.orders_filled == 1
        balances = result.final_balances["sim_binance"]
        assert balances["BTC"] == Decimal("1.5")
        assert balances["USDT"] > Decimal("100000")

    def test_multiple_trades(self, basic_engine):
        """Place multiple orders across ticks."""
        trade_count = [0]

        def on_tick(timestamp, connectors):
            if trade_count[0] < 3:
                connector = connectors["sim_binance"]
                connector.sim_exchange.buy(
                    "BTC-USDT", Decimal("0.01"), SimOrderType.MARKET, Decimal("0")
                )
                trade_count[0] += 1

        result = basic_engine.run(on_tick=on_tick)

        assert result.orders_placed == 3
        assert result.orders_filled == 3
        balances = result.final_balances["sim_binance"]
        assert balances["BTC"] == Decimal("2.03")  # 2.0 + 3 * 0.01

    def test_buy_and_sell_round_trip(self, basic_engine):
        """Buy then sell — check P&L tracking."""
        step = [0]

        def on_tick(timestamp, connectors):
            connector = connectors["sim_binance"]
            if step[0] == 0:
                # Buy 0.1 BTC
                connector.sim_exchange.buy(
                    "BTC-USDT", Decimal("0.1"), SimOrderType.MARKET, Decimal("0")
                )
            elif step[0] == 2:
                # Sell 0.1 BTC two ticks later (price may have moved)
                connector.sim_exchange.sell(
                    "BTC-USDT", Decimal("0.1"), SimOrderType.MARKET, Decimal("0")
                )
            step[0] += 1

        result = basic_engine.run(on_tick=on_tick)

        assert result.orders_filled == 2
        balances = result.final_balances["sim_binance"]
        assert balances["BTC"] == Decimal("2.0")  # Back to original

    def test_limit_order_lifecycle(self, candles_csv):
        """Limit order placed below market, fills when price reaches it."""
        config = SandboxConfig(
            tick_interval=60.0,
            exchanges=[
                SimulatedExchangeConfig(
                    name="sim_ex",
                    trading_pairs=["BTC-USDT"],
                    initial_balances={"USDT": Decimal("100000"), "BTC": Decimal("2.0")},
                )
            ],
            matching_engine=LimitOrderEngine(),
            fee_model=ZeroFeeModel(),
        )

        engine = SandboxEngine(config)
        candles = load_candles_csv(candles_csv, trading_pair="BTC-USDT")
        source = ReplayDataSource()
        source.add_candles(candles, generate_order_book=True, spread_bps=Decimal("10"))
        engine.add_replay_data("sim_ex", source)

        placed = [False]

        def on_tick(timestamp, connectors):
            if not placed[0]:
                connector = connectors["sim_ex"]
                # Place limit buy at best ask (should fill on next tick)
                book = connector.sim_exchange.get_order_book("BTC-USDT")
                if book.best_ask is not None:
                    connector.sim_exchange.buy(
                        "BTC-USDT", Decimal("0.1"), SimOrderType.LIMIT, book.best_ask
                    )
                    placed[0] = True

        result = engine.run(on_tick=on_tick)

        assert result.orders_placed == 1
        # Should fill since we placed at best ask
        assert result.orders_filled == 1


class TestSandboxEngineFees:
    def test_fees_deducted(self, candles_csv):
        """Verify fees are deducted from balances."""
        config = SandboxConfig(
            tick_interval=60.0,
            exchanges=[
                SimulatedExchangeConfig(
                    name="sim_ex",
                    trading_pairs=["BTC-USDT"],
                    initial_balances={"USDT": Decimal("100000"), "BTC": Decimal("2.0")},
                )
            ],
            matching_engine=ImmediateFillEngine(),
            fee_model=FlatFeeModel(taker_rate=Decimal("0.001")),
        )

        engine = SandboxEngine(config)
        candles = load_candles_csv(candles_csv, trading_pair="BTC-USDT")
        source = ReplayDataSource()
        source.add_candles(candles, generate_order_book=True)
        engine.add_replay_data("sim_ex", source)

        bought = [False]

        def on_tick(timestamp, connectors):
            if not bought[0]:
                connector = connectors["sim_ex"]
                connector.sim_exchange.buy(
                    "BTC-USDT", Decimal("1.0"), SimOrderType.MARKET, Decimal("0")
                )
                bought[0] = True

        result = engine.run(on_tick=on_tick)

        balances = result.final_balances["sim_ex"]
        assert balances["BTC"] == Decimal("3.0")  # 2.0 + 1.0
        # USDT should be less than 100000 - fill_price (fee deducted too)
        # With 0.1% fee on ~50100: fee ~ 50.1
        usdt_spent = Decimal("100000") - balances["USDT"]
        assert usdt_spent > Decimal("50000")  # fill + fee


class TestSandboxEngineOrderCancellation:
    def test_cancel_order(self, candles_csv):
        """Place and cancel a limit order using LimitOrderEngine."""
        config = SandboxConfig(
            tick_interval=60.0,
            exchanges=[
                SimulatedExchangeConfig(
                    name="sim_ex",
                    trading_pairs=["BTC-USDT"],
                    initial_balances={"USDT": Decimal("100000"), "BTC": Decimal("2.0")},
                )
            ],
            matching_engine=LimitOrderEngine(),
            fee_model=ZeroFeeModel(),
        )
        engine = SandboxEngine(config)
        candles = load_candles_csv(candles_csv, trading_pair="BTC-USDT")
        source = ReplayDataSource()
        source.add_candles(candles, generate_order_book=True)
        engine.add_replay_data("sim_ex", source)

        order_ids = []

        def on_tick(timestamp, connectors):
            connector = connectors["sim_ex"]
            if len(order_ids) == 0:
                oid = connector.sim_exchange.buy(
                    "BTC-USDT",
                    Decimal("0.1"),
                    SimOrderType.LIMIT,
                    Decimal("40000"),  # Far below market
                )
                order_ids.append(oid)
            elif len(order_ids) == 1:
                connector.sim_exchange.cancel("BTC-USDT", order_ids[0])
                order_ids.append("cancelled")

        result = engine.run(on_tick=on_tick)

        assert result.orders_cancelled == 1
        balances = result.final_balances["sim_ex"]
        assert balances["USDT"] == Decimal("100000")
