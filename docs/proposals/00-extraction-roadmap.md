# Sub-Package Extraction Roadmap

This document tracks all proposed and completed sub-package extractions from the
hummingbot monolith. Packages are listed in extraction priority order.

## Completed Extractions

### hb-candles-feed (DONE)

**Status:** Published
**Repository:** MementoRC/hb-candles-feed
**Scope:** Candle data feed abstraction with exchange-specific implementations.
**What it provides:**
- `CandlesFactory` for creating candle feeds by exchange name
- `CandlesBase` abstract class for feed implementations
- `CandlesConfig`, `HistoricalCandlesConfig` data types
- `hb_compat` module for seamless hummingbot integration

**Dependencies:** None (standalone)
**Enabled:** MarketDataProvider uses it via `hb_compat.CandlesFactory` with fallback
to native `hummingbot.data_feed.candles_feed`.

---

### hb-liquidations-feed (DONE)

**Status:** Published
**Repository:** MementoRC/hb-liquidations-feed
**Scope:** Real-time liquidation data feeds from exchanges.
**What it provides:**
- Liquidation event streaming
- Exchange-specific liquidation parsers

**Dependencies:** None (standalone)
**Enabled:** Standalone data feed for liquidation-based strategies.

---

### hb-rate-oracle Phase 1 (DONE)

**Status:** Published
**Repository:** sub-packages/rate-oracle
**Scope:** Price conversion rate engine (Phase 1: core oracle + static sources).
**What it provides:**
- `RateOracle` singleton for cross-pair price conversion
- Static rate injection for testing/simulation
- `set_price()` / `get_price()` API

**Dependencies:** None (standalone)
**Enabled:** MarketDataProvider and controllers use RateOracle for conversion rates.

---

## Active Extraction

### hb-market-simulator (Priority 1 -- IN PROGRESS)

**Status:** Scaffolded, architecture analysis complete
**Location:** sub-packages/market-simulator
**Scope:** Simulation and sandboxing framework for offline strategy testing.

**What it provides:**
- `SimulatedConnector` implementing the ConnectorBase interface via protocols
- Simulated balance management (spot + perpetual + margin)
- Local order matching engine with configurable fill models
- Event emission matching real exchange event semantics
- Simulated clock for deterministic backtesting
- Historical data replay from candle/trade/order-book recordings
- `hb_compat` module for seamless hummingbot integration

**Package structure:**
```
market_simulator/
  core/          # Core data types, clock, base abstractions
  protocols/     # Protocol definitions (MarketData, Balance, OrderExec, etc.)
  simulator/     # SimulatedConnector, matching engine, balance manager
  replay/        # Historical data replay, recording/playback
  hb_compat/     # Hummingbot integration layer
```

**Dependencies:**
- hb-candles-feed (for candle replay)
- hb-rate-oracle (for simulated conversion rates)

**What it enables:**
- Full executor-level backtesting (all 8 executor types)
- Balance-aware simulation (margin, collateral, fees)
- Deterministic regression testing
- Offline paper trading

**Extraction complexity:** HIGH
- Must replicate exact MarketEvent emission semantics
- BudgetChecker compatibility layer needed
- _order_tracker private API must be shimmed
- Prior work in dev/sandboxing provides ~40% of balance management

---

## Proposed Extractions

### hb-connector-protocols (Priority 2)

**Status:** PROPOSED
**Scope:** Protocol interfaces decomposing ConnectorBase into composable types.

**What it provides:**
- `MarketDataProtocol` -- price queries, order book, trading rules, quantization
- `BalanceProtocol` -- balance queries, budget checker
- `OrderExecutionProtocol` -- buy/sell/cancel, in-flight orders
- `EventSourceProtocol` -- event listener registration and emission
- `ReadinessProtocol` -- connector identity and readiness
- `PerpetualProtocol` -- funding info, leverage, position mode

**Dependencies:** None (pure protocol definitions + hummingbot types)

**What it enables:**
- Type-safe connector mocking in tests
- SimulatedConnector validation via protocol conformance
- Consumer code that accepts protocol types instead of ConnectorBase
- Foundation for all subsequent connector-related extractions

**Extraction complexity:** LOW
- Protocol definitions are additive (no existing code changes)
- Phase 1: Define alongside ConnectorBase
- Phase 2: Refactor consumers to accept protocol types
- Phase 3: Extract into standalone package

**Depends on:** hb-market-simulator (protocols emerge from simulation needs)

---

### hb-ws-data-feed (Priority 3)

**Status:** PROPOSED
**Scope:** Standalone WebSocket market data feeds (order books, trades, ticker).

**What it provides:**
- Exchange-agnostic WebSocket client for market data
- Order book construction and maintenance
- Trade stream processing
- Ticker/best-bid-ask feeds
- Recording and replay capability

**Dependencies:**
- hb-connector-protocols (MarketDataProtocol)

**What it enables:**
- Market data consumption without full connector setup
- Order book recording for simulation replay
- Standalone market monitoring tools
- Decoupled market data from order execution

**Extraction complexity:** MEDIUM
- WebSocket infrastructure exists in `hummingbot.core.web_assistant`
- Order book tracker is coupled to exchange connector lifecycle
- Must handle reconnection, rate limiting, subscription management
- Each exchange has unique WebSocket protocol

**Key files:**
- `hummingbot/core/data_type/order_book_tracker.py`
- `hummingbot/core/web_assistant/ws_assistant.py`
- `hummingbot/connector/exchange_py_base.py` (order book integration)

---

### hb-order-management (Priority 4)

**Status:** PROPOSED
**Scope:** Order lifecycle management, tracking, and state machines.

**What it provides:**
- `InFlightOrder` and `InFlightOrderBase` state management
- `ClientOrderTracker` for order ID tracking and reconciliation
- Order state machine (Created -> Filled/Cancelled/Failed)
- Trade fill processing and deduplication
- Order event types and their emission logic

**Dependencies:**
- hb-connector-protocols (OrderExecutionProtocol)
- hb-event-system (MarketEvent types)

**What it enables:**
- Order management without full connector dependency
- Reusable order tracking for SimulatedConnector
- Cleaner separation of order lifecycle from exchange communication
- Testable order state machines

**Extraction complexity:** HIGH
- `InFlightOrder` is deeply integrated with exchange-specific update logic
- `ClientOrderTracker` has complex reconciliation with exchange order IDs
- Order events are tightly coupled with connector event emission
- Must handle partial fills, amendments, and edge cases

**Key files:**
- `hummingbot/core/data_type/in_flight_order.py`
- `hummingbot/connector/client_order_tracker.py`
- `hummingbot/connector/in_flight_order_base.py`

---

### hb-event-system (Priority 5)

**Status:** PROPOSED
**Scope:** PubSub event infrastructure, event types, and forwarders.

**What it provides:**
- `EventListener`, `EventForwarder`, `SourceInfoEventForwarder`
- `EventLogger`, `EventReporter`
- `MarketEvent` enum and all event dataclasses
- `PubSub` mixin (currently on `NetworkIterator`)
- Generic typed event bus for decoupled communication

**Dependencies:** None (foundational)

**What it enables:**
- Event-driven architecture without hummingbot core dependency
- Reusable pub/sub for any component
- Type-safe event definitions independent of connectors
- Foundation for event replay in simulation

**Extraction complexity:** MEDIUM
- Event infrastructure is spread across multiple modules
- `NetworkIterator` mixes event handling with network lifecycle
- Cython implementations (`EventListener`, `EventForwarder`) need pure-Python equivalents
- Must preserve event tag integer system for backward compatibility

**Key files:**
- `hummingbot/core/event/events.py` (MarketEvent enum + event dataclasses)
- `hummingbot/core/event/event_forwarder.py`
- `hummingbot/core/event/event_logger.py`
- `hummingbot/core/pubsub.pyx` (Cython pub/sub implementation)

---

### hb-trading-rules (Priority 6)

**Status:** PROPOSED
**Scope:** Exchange trading rules, order quantization, and validation.

**What it provides:**
- `TradingRule` dataclass (min_order_size, min_notional, price_quantum, etc.)
- Quantization functions (price quantum, size quantum)
- Order candidate validation
- `BudgetChecker` and `PerpetualBudgetChecker`

**Dependencies:**
- hb-connector-protocols (uses TradingRule in MarketDataProtocol)

**What it enables:**
- Trading rule enforcement without full connector
- Reusable quantization for SimulatedConnector
- Budget checking as a standalone concern
- Exchange-specific rule loading from static config

**Extraction complexity:** MEDIUM
- `TradingRule` is a simple dataclass (easy to extract)
- `BudgetChecker` is coupled to `ExchangeBase` internal state
- Quantization logic is in Cython (`ConnectorBase.pyx`)
- Must handle perpetual-specific budget checking (margin, leverage)

**Key files:**
- `hummingbot/connector/trading_rule.py`
- `hummingbot/checker/budget_checker.py`
- `hummingbot/checker/perpetual_budget_checker.py`

---

### hb-rate-oracle Phase 2 (Priority 7)

**Status:** PROPOSED
**Scope:** Exchange-backed rate sources for the rate oracle.

**What it provides:**
- Exchange connector-based rate fetching (Binance, Coinbase, etc.)
- Aggregated rate sources (multiple exchanges)
- Gateway/DEX rate sources
- Automatic fallback chains

**Dependencies:**
- hb-rate-oracle Phase 1 (core oracle)
- hb-ws-data-feed (for live price feeds)
- hb-connector-protocols (for exchange interface)

**What it enables:**
- Production-grade rate oracle with live exchange data
- Rate source selection and failover
- Complete replacement of `RateOracle.get_instance()` singleton

**Extraction complexity:** MEDIUM
- Rate source implementations are straightforward
- Gateway integration adds complexity
- Must handle rate staleness and refresh intervals

---

## Dependency Graph

```
hb-event-system (standalone)
    |
    v
hb-connector-protocols (standalone, uses hummingbot types)
    |
    +---> hb-market-simulator
    |       |-- hb-candles-feed
    |       +-- hb-rate-oracle Phase 1
    |
    +---> hb-ws-data-feed
    |
    +---> hb-order-management
    |       +-- hb-event-system
    |
    +---> hb-trading-rules
    |
    +---> hb-rate-oracle Phase 2
            |-- hb-rate-oracle Phase 1
            +-- hb-ws-data-feed
```

## Extraction Principles

1. **Protocol-first:** Define protocols before extracting implementations. Protocols
   are additive and risk-free.

2. **hb_compat pattern:** Every sub-package includes an `hb_compat` module that provides
   seamless integration with hummingbot's existing code. This allows gradual migration
   without breaking changes.

3. **No Cython dependency:** Sub-packages are pure Python. Where hummingbot uses Cython
   (ConnectorBase, EventForwarder, etc.), the sub-package provides pure-Python
   protocol-compatible implementations.

4. **Standalone testability:** Each sub-package must be fully testable without hummingbot
   installed. The `hb_compat` module is the only part that imports from hummingbot.

5. **Minimal dependencies:** Sub-packages depend on each other only through protocols.
   Circular dependencies are not allowed.
