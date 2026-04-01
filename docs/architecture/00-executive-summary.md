# Executive Summary: hb-market-simulator

## The Problem

Hummingbot has no way to test strategies offline with full executor fidelity.

The current backtesting system (`BacktestingEngineBase` + `BacktestingDataProvider`) operates
at the **controller level only**: it replays candle data through controllers to generate
`CreateExecutorAction` / `StopExecutorAction` signals, then simulates executor outcomes using
simplified math (the `ExecutorSimulation` dataclass). This means:

1. **Executor code never runs.** The 8 executor types (`PositionExecutor`, `DCAExecutor`,
   `ArbitrageExecutor`, `XEMMExecutor`, `GridExecutor`, `OrderExecutor`, `TWAPExecutor`,
   `LPExecutor`) contain complex order management, partial fill handling, and state machines
   that are entirely bypassed during backtesting.

2. **Paper trading requires live WebSocket connections.** The paper trade connector wraps a
   real exchange connector and needs a live data feed to function. There is no offline
   paper-trade mode.

3. **No balance tracking in simulation.** Backtesting does not simulate portfolio balances,
   margin requirements, or available collateral. A strategy that would be rejected for
   insufficient funds in production gets perfect fills in backtesting.

4. **Most executor types have no simulator at all.** The `ExecutorSimulation` path only
   covers `PositionExecutor` and `DCAExecutor`. The remaining 6 executor types
   (`ArbitrageExecutor`, `XEMMExecutor`, `GridExecutor`, `OrderExecutor`, `TWAPExecutor`,
   `LPExecutor`) cannot be backtested.

5. **No order-book-level simulation.** All backtesting uses candle close prices. There is
   no modeling of slippage, partial fills, order book depth, or queue position.

## The Key Insight

**`ConnectorBase` is the ONLY seam between live exchange APIs and the pure-Python
strategy_v2 stack.**

Everything above `ConnectorBase` -- controllers, executors, the executor orchestrator,
`MarketDataProvider` -- runs unmodified if given a proper simulated connector. The entire
strategy_v2 architecture was designed around this abstraction:

- Executors call `self.connectors[name].buy()`, `self.connectors[name].sell()`,
  `self.connectors[name].get_price_by_type()`, etc.
- Controllers interact with connectors exclusively through `MarketDataProvider`.
- The orchestrator creates/stops executors based on controller actions.

If we implement a `SimulatedConnector` that satisfies the same interface as `ConnectorBase`
-- emitting the same `MarketEvent` events, maintaining the same balance/order tracking
properties -- then the entire strategy_v2 stack runs unmodified against simulated data.

## Six Simulation Seams

Analysis of the strategy_v2 architecture reveals six injection points where simulation
components can replace live infrastructure:

### Seam 1: ConnectorBase (PRIMARY)

The connector provides prices, balances, order execution, trading rules, and event emission.
This is the most critical seam -- it is what executors interact with directly.

**Consumers:** ExecutorBase (8 subclasses), MarketDataProvider, StrategyV2Base

**Key methods used:**
- `buy()`, `sell()`, `cancel()` -- order lifecycle
- `get_price_by_type()`, `get_order_book()` -- market data
- `get_balance()`, `get_available_balance()` -- balance queries
- `budget_checker.adjust_candidates()` -- order validation
- `trading_rules[pair]` -- quantization rules
- `add_listener()`, `remove_listener()` -- event subscription
- `_order_tracker.fetch_order()` -- in-flight order queries

### Seam 2: CandlesFactory / CandlesFeeds

Historical candle data injection. Controllers and `MarketDataProvider` use candle feeds
for technical analysis. The `hb-candles-feed` sub-package (already extracted) provides
the `CandlesFactory` interface.

**Consumers:** MarketDataProvider, ControllerBase subclasses

### Seam 3: MarketDataProvider.time()

`MarketDataProvider.time()` returns `time.time()` in production. For simulation, this
must return simulated clock time. `BacktestingDataProvider` already overrides this.

**Consumers:** ControllerBase (via `market_data_provider.time()`)

### Seam 4: MarketsRecorder (Persistence)

`MarketsRecorder` is a singleton that persists orders, trades, and executor state to
SQLite via SQLAlchemy. In simulation, this should be either in-memory or a no-op.

**Consumers:** StrategyV2Base, ExecutorOrchestrator

### Seam 5: RateOracle (Conversion Rates)

`RateOracle` provides cross-pair conversion rates. In simulation, these can be
statically injected or computed from the simulated price feed.

**Consumers:** MarketDataProvider (rate source initialization), controllers

### Seam 6: Clock

Hummingbot already has a `Clock` abstraction (`hummingbot.core.clock`). The clock drives
`tick()` calls on `StrategyV2Base` and all connectors. For simulation, we need a
deterministic clock that advances on command rather than in real time.

## Current Architecture Dependencies

```
Clock.tick() -> StrategyV2Base.tick()
                  |
                  +-- MarketDataProvider
                  |     |-- connectors: Dict[str, ConnectorBase]   <-- SEAM 1
                  |     |-- candles_feeds: Dict[str, CandleFeed]   <-- SEAM 2
                  |     |-- time() -> float                        <-- SEAM 3
                  |     |-- get_price_by_type(connector, pair, type)
                  |     |-- get_order_book(connector, pair)
                  |     |-- get_balance(connector, asset)
                  |     +-- get_funding_info(connector, pair)
                  |
                  +-- ControllerBase x N
                  |     |-- market_data_provider (ref to above)
                  |     |-- determine_executor_actions() -> List[ExecutorAction]
                  |     +-- update_processed_data()
                  |
                  +-- ExecutorOrchestrator
                  |     +-- ExecutorBase x M
                  |           |-- connectors[name].buy()/sell()           <-- SEAM 1
                  |           |-- connectors[name].get_price_by_type()    <-- SEAM 1
                  |           |-- connectors[name].trading_rules[pair]    <-- SEAM 1
                  |           |-- connectors[name].budget_checker.*       <-- SEAM 1
                  |           |-- connectors[name]._order_tracker.*       <-- SEAM 1
                  |           +-- listens to MarketEvent.* on connector   <-- SEAM 1
                  |
                  +-- MarketsRecorder (singleton)                         <-- SEAM 4
                        |-- store_executor()
                        |-- get_executors_by_controller()
                        +-- get_positions_by_controller()
```

## Prior Work: dev/sandboxing Branch

The `dev/sandboxing` branch (commit `147a81a`, Jan 2025) contains approximately 2,500 LOC
of protocol abstractions and sandbox implementations. Key components:

| Component | LOC | Completeness | Notes |
|-----------|-----|-------------|-------|
| Protocol layer (5 files) | 330 | ~80% | Good foundation; needs Pydantic v2 update |
| SandboxBalanceManager | 536 | ~90% | Most production-ready; handles spot + perp + margin |
| SandboxOrderBook | 185 | ~70% | Structure complete; matching engine stubbed |
| SandboxCandleFeed | 133 | ~85% | Historical replay + random walk modes |
| SandboxMarketDataProvider | 140 | ~60% | Price/orderbook storage; incomplete API |
| ControllerSandbox | 67 | ~50% | Patches controller's market_data_provider |
| ExchangeSandbox | 69 | ~30% | Order placement; fill logic stubbed |
| ExecutorSandbox | 73 | ~20% | Routing layer; mostly stubs |
| BacktestingSandbox | 28 | ~10% | Broken imports; placeholder |
| MarketDynamicsSandbox | 27 | ~10% | Price impact placeholder |

**Assessment:** The protocol definitions and balance manager are reusable. The exchange
sandbox and executor sandbox need to be redesigned around the SimulatedConnector approach
(implementing ConnectorBase's interface directly rather than patching it).

## Proposed Sub-Package Roadmap

These sub-packages decompose hummingbot's monolithic architecture into composable,
independently testable units. Listed in extraction priority order:

| Priority | Package | Status | Description |
|----------|---------|--------|-------------|
| DONE | hb-candles-feed | Published | Candle data feeds |
| DONE | hb-liquidations-feed | Published | Liquidation data feeds |
| DONE | hb-rate-oracle (Phase 1) | Published | Price conversion rates |
| 1 | **hb-market-simulator** | IN PROGRESS | Simulation/sandboxing framework |
| 2 | hb-connector-protocols | PROPOSED | Protocol decomposition of ConnectorBase |
| 3 | hb-ws-data-feed | PROPOSED | Standalone WebSocket market data |
| 4 | hb-order-management | PROPOSED | Order lifecycle, tracking, events |
| 5 | hb-event-system | PROPOSED | PubSub, event types, forwarders |
| 6 | hb-trading-rules | PROPOSED | Exchange trading rules, quantization |
| 7 | hb-rate-oracle Phase 2 | PROPOSED | Exchange-backed rate sources |

**hb-market-simulator** is the current focus because it unlocks:
- Offline strategy testing with full executor fidelity
- Deterministic regression testing for strategy changes
- Balance-aware backtesting (no more infinite-funds simulations)
- A clean protocol interface that drives hb-connector-protocols extraction

See `docs/proposals/00-extraction-roadmap.md` for detailed scope of each package.
