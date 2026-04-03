"""Integration tests for performance metrics in SandboxEngine.

Verifies that SimulationResult contains correct performance metrics after
full simulation runs with various trading patterns.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

pytest.importorskip("hummingbot", reason="hummingbot not installed (CI-safe skip)")

from market_simulator.core.types import OrderType as SimOrderType
from market_simulator.hb_compat.sandbox_engine import SandboxConfig, SandboxEngine
from market_simulator.replay.data_loader import load_candles_csv
from market_simulator.replay.replay_source import ReplayDataSource
from market_simulator.simulator.exchange import SimulatedExchangeConfig
from market_simulator.simulator.fee_model import FlatFeeModel, ZeroFeeModel
from market_simulator.simulator.matching_engine import ImmediateFillEngine

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def candles_csv(tmp_path) -> Path:
    """6 candles with a clear price pattern for predictable P&L."""
    p = tmp_path / "candles.csv"
    p.write_text(
        "timestamp,trading_pair,open,high,low,close,volume,interval\n"
        "1000.0,BTC-USDT,50000,50500,49800,50100,100.0,1m\n"
        "1060.0,BTC-USDT,50100,50300,49900,50200,80.0,1m\n"
        "1120.0,BTC-USDT,50200,50400,50000,50300,90.0,1m\n"
        "1180.0,BTC-USDT,50300,50500,50100,50400,75.0,1m\n"
        "1240.0,BTC-USDT,50400,50600,50200,50500,85.0,1m\n"
        "1300.0,BTC-USDT,50500,50700,50300,50600,70.0,1m\n"
    )
    return p


def _make_engine(candles_csv, fee_model=None) -> SandboxEngine:
    config = SandboxConfig(
        tick_interval=60.0,
        exchanges=[
            SimulatedExchangeConfig(
                name="sim_binance",
                trading_pairs=["BTC-USDT"],
                initial_balances={"USDT": Decimal("100000"), "BTC": Decimal("2.0")},
            )
        ],
        matching_engine=ImmediateFillEngine(),
        fee_model=fee_model or ZeroFeeModel(),
    )
    engine = SandboxEngine(config)
    candles = load_candles_csv(candles_csv, trading_pair="BTC-USDT")
    source = ReplayDataSource()
    source.add_candles(candles, generate_order_book=True, spread_bps=Decimal("10"))
    engine.add_replay_data("sim_binance", source)
    return engine


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestMetricsNoTrades:
    """When no trades occur, all metrics should be zero/default."""

    def test_no_trades_metrics_are_zero(self, candles_csv):
        engine = _make_engine(candles_csv)
        result = engine.run()

        assert result.total_trades == 0
        assert result.max_drawdown == Decimal("0")
        assert result.max_drawdown_pct == 0.0
        assert result.sharpe_ratio == 0.0
        assert result.profit_factor == 0.0
        assert result.total_pnl == Decimal("0")
        assert result.win_rate == 0.0


class TestMetricsRoundTrip:
    """Buy then sell round-trip should produce meaningful metrics."""

    def test_profitable_round_trip(self, candles_csv):
        """Buy at first tick, sell at last tick → positive P&L."""
        engine = _make_engine(candles_csv)
        state = {"phase": "buy"}

        def on_tick(timestamp, connectors):
            connector = connectors["sim_binance"]
            exchange = connector.sim_exchange
            if state["phase"] == "buy" and timestamp == 1000.0:
                exchange.place_order(
                    trading_pair="BTC-USDT",
                    order_type=SimOrderType.MARKET,
                    trade_type="BUY",
                    amount=Decimal("0.1"),
                    price=Decimal("50100"),
                )
                state["phase"] = "sell"
            elif state["phase"] == "sell" and timestamp == 1300.0:
                exchange.place_order(
                    trading_pair="BTC-USDT",
                    order_type=SimOrderType.MARKET,
                    trade_type="SELL",
                    amount=Decimal("0.1"),
                    price=Decimal("50600"),
                )
                state["phase"] = "done"

        result = engine.run(on_tick=on_tick)

        assert result.total_trades == 2
        assert result.orders_filled == 2
        # Net P&L should be positive (bought low, sold high)
        assert result.total_pnl > Decimal("0")
        # Win rate: the sell is a positive quote flow, the buy is negative
        assert result.win_rate == pytest.approx(0.5)

    def test_losing_round_trip(self, candles_csv):
        """Sell at first tick (low price), buy back at last tick (high price)."""
        engine = _make_engine(candles_csv)
        state = {"phase": "sell"}

        def on_tick(timestamp, connectors):
            connector = connectors["sim_binance"]
            exchange = connector.sim_exchange
            if state["phase"] == "sell" and timestamp == 1000.0:
                exchange.place_order(
                    trading_pair="BTC-USDT",
                    order_type=SimOrderType.MARKET,
                    trade_type="SELL",
                    amount=Decimal("0.1"),
                    price=Decimal("50100"),
                )
                state["phase"] = "buy"
            elif state["phase"] == "buy" and timestamp == 1300.0:
                exchange.place_order(
                    trading_pair="BTC-USDT",
                    order_type=SimOrderType.MARKET,
                    trade_type="BUY",
                    amount=Decimal("0.1"),
                    price=Decimal("50600"),
                )
                state["phase"] = "done"

        result = engine.run(on_tick=on_tick)

        assert result.total_trades == 2
        # Net P&L should be negative (sold low, bought high)
        assert result.total_pnl < Decimal("0")


class TestMetricsMultipleTrades:
    """Multiple trades should produce drawdown and profit factor values."""

    def test_alternating_buys_and_sells(self, candles_csv):
        """Buy and sell on alternating ticks."""
        engine = _make_engine(candles_csv)
        trade_count = [0]

        def on_tick(timestamp, connectors):
            connector = connectors["sim_binance"]
            exchange = connector.sim_exchange
            if trade_count[0] >= 4:
                return
            if trade_count[0] % 2 == 0:
                exchange.place_order(
                    trading_pair="BTC-USDT",
                    order_type=SimOrderType.MARKET,
                    trade_type="BUY",
                    amount=Decimal("0.05"),
                    price=Decimal("99999"),
                )
            else:
                exchange.place_order(
                    trading_pair="BTC-USDT",
                    order_type=SimOrderType.MARKET,
                    trade_type="SELL",
                    amount=Decimal("0.05"),
                    price=Decimal("1"),
                )
            trade_count[0] += 1

        result = engine.run(on_tick=on_tick)

        assert result.total_trades == 4
        # Should have some metrics computed
        assert result.total_pnl != Decimal("0")
        # Profit factor should be a finite positive number or 0
        assert result.profit_factor >= 0.0


class TestMetricsWithFees:
    """Fees should be reflected in P&L and metrics."""

    def test_fees_reduce_pnl(self, candles_csv):
        """Same round-trip with and without fees should differ in P&L."""
        # Without fees
        engine_no_fee = _make_engine(candles_csv, fee_model=ZeroFeeModel())
        state = {"phase": "buy"}

        def on_tick(timestamp, connectors):
            connector = connectors["sim_binance"]
            exchange = connector.sim_exchange
            if state["phase"] == "buy" and timestamp == 1000.0:
                exchange.place_order(
                    trading_pair="BTC-USDT",
                    order_type=SimOrderType.MARKET,
                    trade_type="BUY",
                    amount=Decimal("0.1"),
                    price=Decimal("50100"),
                )
                state["phase"] = "sell"
            elif state["phase"] == "sell" and timestamp == 1300.0:
                exchange.place_order(
                    trading_pair="BTC-USDT",
                    order_type=SimOrderType.MARKET,
                    trade_type="SELL",
                    amount=Decimal("0.1"),
                    price=Decimal("50600"),
                )
                state["phase"] = "done"

        result_no_fee = engine_no_fee.run(on_tick=on_tick)

        # With fees
        engine_with_fee = _make_engine(
            candles_csv, fee_model=FlatFeeModel(taker_rate=Decimal("0.001"))
        )
        state["phase"] = "buy"

        result_with_fee = engine_with_fee.run(on_tick=on_tick)

        assert result_with_fee.total_pnl < result_no_fee.total_pnl
