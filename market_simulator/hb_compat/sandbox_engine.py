"""SandboxEngine: orchestrates a complete offline simulation.

Wires together:
  - SimulatedConnector (ExchangePyBase-compatible)
  - ReplayDataSource (historical data feed)
  - SimulatedClock (deterministic time)

And drives a simulation loop that can run real controllers + executors
against replayed market data.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Callable, Dict, List, Optional

from market_simulator.core.clock import SimulatedClock
from market_simulator.hb_compat.simulated_connector import SimulatedConnector
from market_simulator.replay.replay_source import ReplayDataSource, apply_replay_event
from market_simulator.simulator.exchange import SimulatedExchangeConfig
from market_simulator.simulator.fee_model import FeeModel, FlatFeeModel
from market_simulator.simulator.matching_engine import LimitOrderEngine, MatchingEngine

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class SandboxConfig:
    """Configuration for a sandbox simulation run."""

    # Time range
    start_timestamp: float = 0.0
    end_timestamp: float = 0.0
    tick_interval: float = 1.0  # seconds between ticks

    # Exchange configs (one per simulated exchange)
    exchanges: List[SimulatedExchangeConfig] = field(default_factory=list)

    # Matching and fee models (shared defaults, can override per exchange)
    matching_engine: Optional[MatchingEngine] = None
    fee_model: Optional[FeeModel] = None


# ---------------------------------------------------------------------------
# Simulation results
# ---------------------------------------------------------------------------

@dataclass
class SimulationResult:
    """Results from a sandbox simulation run."""

    start_time: float
    end_time: float
    ticks_processed: int
    events_replayed: int
    final_balances: Dict[str, Dict[str, Decimal]]  # exchange_name -> {currency: balance}
    orders_placed: int
    orders_filled: int
    orders_cancelled: int


# ---------------------------------------------------------------------------
# SandboxEngine
# ---------------------------------------------------------------------------

class SandboxEngine:
    """Orchestrates an offline simulation.

    Usage:
        config = SandboxConfig(
            start_timestamp=1000.0,
            end_timestamp=2000.0,
            tick_interval=1.0,
            exchanges=[SimulatedExchangeConfig(
                name="sim_binance",
                trading_pairs=["BTC-USDT"],
                initial_balances={"USDT": Decimal("10000"), "BTC": Decimal("1")},
            )],
        )

        engine = SandboxEngine(config)
        engine.add_replay_data("sim_binance", replay_source)

        # Option 1: Run with a tick callback (no hummingbot strategy)
        result = engine.run(on_tick=my_callback)

        # Option 2: Get connectors dict for injecting into StrategyV2Base
        connectors = engine.connectors
    """

    def __init__(self, config: SandboxConfig) -> None:
        self._config = config
        self._clock = SimulatedClock(
            start_time=config.start_timestamp,
            tick_size=config.tick_interval,
        )

        # Create connectors
        self._connectors: Dict[str, SimulatedConnector] = {}
        self._replay_sources: Dict[str, ReplayDataSource] = {}

        for ex_config in config.exchanges:
            connector = SimulatedConnector(
                config=ex_config,
                matching_engine=config.matching_engine or LimitOrderEngine(),
                fee_model=config.fee_model or FlatFeeModel(),
            )
            self._connectors[ex_config.name] = connector

        # Track replay position per exchange to avoid re-applying events
        self._replay_cursors: Dict[str, float] = {}

        # Stats
        self._events_replayed = 0

    # -------------------------------------------------------------------
    # Properties
    # -------------------------------------------------------------------

    @property
    def clock(self) -> SimulatedClock:
        return self._clock

    @property
    def connectors(self) -> Dict[str, SimulatedConnector]:
        """Get connectors dict — inject into StrategyV2Base.connectors."""
        return dict(self._connectors)

    def get_connector(self, name: str) -> SimulatedConnector:
        return self._connectors[name]

    # -------------------------------------------------------------------
    # Data setup
    # -------------------------------------------------------------------

    def add_replay_data(self, exchange_name: str, source: ReplayDataSource) -> None:
        """Add replay data for a specific exchange."""
        if exchange_name not in self._connectors:
            raise ValueError(f"Unknown exchange: {exchange_name}")
        self._replay_sources[exchange_name] = source

        # Auto-detect time range if not configured
        if self._config.start_timestamp == 0 and self._config.end_timestamp == 0:
            time_range = source.time_range
            if time_range:
                self._config.start_timestamp = time_range[0]
                self._config.end_timestamp = time_range[1]
                self._clock.reset(start_time=time_range[0])

    # -------------------------------------------------------------------
    # Initialization
    # -------------------------------------------------------------------

    def initialize(self) -> None:
        """Initialize all connectors: sync balances, trading rules, set ready."""
        loop = asyncio.new_event_loop()
        try:
            for connector in self._connectors.values():
                loop.run_until_complete(connector._update_balances())
                loop.run_until_complete(connector._update_trading_rules())
                connector.sim_exchange.ready = True

            # Apply initial replay events (order book snapshots at start)
            self._apply_events_up_to(self._config.start_timestamp)
        finally:
            loop.close()

    # -------------------------------------------------------------------
    # Simulation loop
    # -------------------------------------------------------------------

    def run(
        self,
        on_tick: Optional[Callable[[float, Dict[str, SimulatedConnector]], None]] = None,
    ) -> SimulationResult:
        """Run the simulation from start to end.

        Args:
            on_tick: Optional callback called each tick with (timestamp, connectors).
                     Use this to implement custom strategy logic without needing
                     StrategyV2Base.

        Returns:
            SimulationResult with final state.
        """
        self.initialize()

        start = self._config.start_timestamp
        end = self._config.end_timestamp
        tick_interval = self._config.tick_interval

        ticks = 0
        timestamp = start

        while timestamp <= end:
            # 1. Apply replay events up to this timestamp
            self._apply_events_up_to(timestamp)

            # 2. Call user tick callback (place/cancel orders)
            if on_tick:
                on_tick(timestamp, self._connectors)

            # 3. Tick all connectors (processes order matching AFTER user actions)
            for connector in self._connectors.values():
                connector.sim_exchange.process_tick(timestamp)

            ticks += 1
            timestamp += tick_interval

        # Collect results
        return self._build_result(start, end, ticks)

    # -------------------------------------------------------------------
    # Internal
    # -------------------------------------------------------------------

    def _apply_events_up_to(self, timestamp: float) -> None:
        """Apply replay events from last cursor position up to timestamp."""
        for exchange_name, source in self._replay_sources.items():
            connector = self._connectors[exchange_name]
            order_books = {
                pair: connector.sim_exchange.get_order_book(pair)
                for pair in connector.trading_pairs
            }

            cursor = self._replay_cursors.get(exchange_name, float("-inf"))

            for event in source.events(end_time=timestamp):
                if event.timestamp <= cursor:
                    continue
                if event.timestamp > timestamp:
                    break
                apply_replay_event(event, order_books)
                self._events_replayed += 1

            self._replay_cursors[exchange_name] = timestamp

    def _build_result(
        self, start: float, end: float, ticks: int
    ) -> SimulationResult:
        """Collect final simulation state."""
        final_balances = {}
        total_orders = 0
        total_filled = 0
        total_cancelled = 0

        for name, connector in self._connectors.items():
            final_balances[name] = connector.sim_exchange.balance_manager.get_all_balances()

            tracker = connector.sim_exchange.order_tracker
            for order in tracker.all_orders.values():
                total_orders += 1
                from market_simulator.core.types import OrderStatus
                if order.status == OrderStatus.FILLED:
                    total_filled += 1
                elif order.status == OrderStatus.CANCELLED:
                    total_cancelled += 1

        return SimulationResult(
            start_time=start,
            end_time=end,
            ticks_processed=ticks,
            events_replayed=self._events_replayed,
            final_balances=final_balances,
            orders_placed=total_orders,
            orders_filled=total_filled,
            orders_cancelled=total_cancelled,
        )
