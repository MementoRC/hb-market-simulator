# Review: dev/sandboxing Branch (commit 147a81a)

This document reviews the prior sandboxing work on the `dev/sandboxing` branch
(committed January 24, 2025) and assesses what is reusable, what needs redesign,
and what architectural decisions to preserve or change.

## Overview

The branch adds 33 files totaling ~3,583 lines (including tests and docs):

| Category | Files | LOC | Assessment |
|----------|-------|-----|------------|
| Protocol layer | 5 | 330 | Good foundation, reusable with updates |
| Sandbox implementations | 10 | 1,295 | Mixed; balance manager excellent, others need redesign |
| Live implementations | 5 | 481 | Reference implementations for protocol conformance |
| Tests | 4 | 862 | Good coverage for live implementations |
| Documentation | 5 | 305 | Architectural docs, useful as reference |
| Other | 4 | 310 | Bybit exchange patch, backtesting interfaces |

## 1. What Was Built and What's Reusable

### 1.1 Protocol Layer (5 files, 330 LOC) -- REUSABLE

**Files:**
- `balance_interface_protocol.py` (134 LOC)
- `market_interface_protocol.py` (76 LOC)
- `order_interface_protocol.py` (71 LOC)
- `event_interface_protocol.py` (25 LOC)
- `position_interface_protocol.py` (24 LOC)

**What's good:**
- Uses `typing.Protocol` with `@runtime_checkable` (correct pattern)
- Clean separation of concerns: balance, market, order, event, position
- `BaseBalanceInterfaceProtocol` covers get_balance, get_available_balance, get_all_balances
- `OrderCandidateBalanceInterfaceProtocol` adds validate/adjust order candidate
- `LockableBalanceInterfaceProtocol` adds lock/release semantics
- `LeverageBalanceInterfaceProtocol` for perpetual margin
- `MarketInterfaceProtocol` covers price, trading_rules, order_book, quantization
- `OrderInterfaceProtocol` covers place_order, cancel_order, get_order_status, get_active_orders
- `CandleInterfaceProtocol` and `RateInterfaceProtocol` as separate concerns

**What needs updating:**
- Uses `dict[str, Decimal]` (Python 3.10+ syntax) instead of `Dict[str, Decimal]`
  (hummingbot still supports 3.10+ so this is acceptable, but inconsistent with codebase style)
- `list[str]` and `dict[str, ...]` lower-case generics throughout
- Missing some methods that executors actually call (e.g., `get_quote_price`,
  `budget_checker` property, `_order_tracker.fetch_order`)
- `EventInterfaceProtocol` uses `MarketEvent` enum directly but executors use
  integer event tags
- `OrderInterfaceProtocol.place_order` has `trade_type` parameter but executors
  call `buy()`/`sell()` as separate methods

**Recommendation:** Use as the starting point for `market_simulator/protocols/`.
Align method signatures with what ExecutorBase actually calls (see
`02-connector-interface-analysis.md` Section 1). Add missing methods.

### 1.2 SandboxBalanceManager (536 LOC) -- MOST PRODUCTION-READY

**File:** `sandbox/balance_manager_sandbox.py`

**What's good:**
- Clean dataclass-based state: `TokenBalance(total, available, locked)` and
  `PositionBalance(amount, entry_price, leverage, unrealized_pnl)`
- Full spot trading flow: lock on order -> unlock+transfer on fill
- Full perpetual trading flow: margin calculation, position open/close, PnL tracking
- Average entry price calculation on position additions
- Realized PnL on position close with correct direction handling
- Fee deduction support
- Leverage and position mode management
- Per-connector isolation of balances
- Comprehensive logging

**What needs updating:**
- No Pydantic models (uses raw dataclasses) -- fine for internal state
- No integration with `BudgetChecker` interface
- Missing order cancellation flow (unlock balance when order is cancelled)
- Missing partial fill handling (currently assumes full fills)
- No cross-exchange balance checks (the protocol defines it but manager doesn't implement)
- `_unlock_and_increase_balance` method exists but `update_balance_on_fill` for
  close doesn't properly handle the margin release math in all edge cases

**Recommendation:** Adopt as the core of `market_simulator/simulator/balance_manager.py`.
Add: order cancellation balance release, partial fill support, BudgetChecker-compatible
wrapper. The existing tests (462 LOC in `test_live_balance_manager.py`) provide good
coverage to validate the port.

### 1.3 SandboxOrderBook (185 LOC) -- MOSTLY COMPLETE

**File:** `sandbox/order_book_sandbox.py`

**What's good:**
- Clean bid/ask management with `Dict[Decimal, OrderBookRow]`
- `get_price_by_type` supporting all PriceType variants (MidPrice, BestBid, BestAsk,
  LastTrade, LastOwnTrade, InventoryCost, Custom)
- Snapshot and diff application (mirrors real order book tracker behavior)
- Volume-at-price queries
- Best price tracking

**What's missing:**
- No order matching engine (`_should_fill_order` referenced in ExchangeSandbox but not implemented)
- No `bid_entries()` / `ask_entries()` iteration methods (referenced but not defined)
- No depth aggregation
- No market impact calculation (placeholder in MarketDynamicsSandbox)

**Recommendation:** Adopt structure. Add matching engine as a separate concern
(`market_simulator/simulator/matching_engine.py`). The order book should be a data
structure; matching logic should be pluggable.

### 1.4 SandboxCandleFeed (133 LOC) -- COMPLETE

**File:** `sandbox/candle_feed_sandbox.py`

**What's good:**
- Historical replay from DataFrame (generator-based)
- Random walk price generation for synthetic testing
- OHLCV candle structure matching real CandlesBase format
- `get_historical_candles` with time range filtering
- Simulated WebSocket message processing

**What needs changing:**
- Subclasses `CandlesBase` from hummingbot -- should be standalone
- `_process_websocket_messages` / `_sleep` depend on CandlesBase internals
- `interval_in_seconds` property from CandlesBase assumed available

**Recommendation:** Since `hb-candles-feed` is already extracted as a standalone package,
the sandbox candle feed should use `candles_feed` types rather than subclassing
`hummingbot.data_feed.candles_feed.candles_base.CandlesBase`. Build a standalone
`ReplayableCandleFeed` that implements the `CandlesBase`-compatible interface via
protocol conformance.

### 1.5 ControllerSandbox (67 LOC) -- PARTIAL

**File:** `sandbox/controller_sandbox.py`

**What's good:**
- Core concept is correct: patch `controller.market_data_provider` with sandbox version
- Patches `actions_queue` and `executors_info` for sandbox routing
- `process_actions()` drains the queue for processing

**What's missing:**
- No integration with SimulatedConnector (patches MDP but not connectors)
- Candle initialization patch is fragile (monkey-patches `initialize_candles` method)
- No clock integration

**Recommendation:** The patching approach is valid but should be part of the `hb_compat`
integration layer, not the core simulator. The core simulator should work with
`MarketDataProvider`-compatible objects natively.

### 1.6 ExchangeSandbox (69 LOC) -- MOSTLY STUBS

**File:** `sandbox/exchange_sandbox.py`

**What's good:**
- Correct structure: order placement -> event emission -> tick-based matching
- Event listener registration/removal mirrors ConnectorBase
- `process_tick` iterates active orders and checks for fills

**What's missing/stubbed:**
- `_should_fill_order` -- the core matching logic -- not implemented
- `_get_fill_price` -- not implemented
- `_create_in_flight_order` -- not implemented
- `_emit_order_created`, `_emit_order_filled`, `_emit_order_completed`, `_emit_order_cancelled` -- not implemented
- No trading rules enforcement
- No partial fill support
- Uses string-typed `event_type` keys instead of `MarketEvent` enum values

**Recommendation:** Do not adopt this implementation. Instead, build `SimulatedConnector`
as a new class that implements the full protocol suite. The exchange sandbox tried to
be a thin wrapper; we need a complete ConnectorBase replacement.

### 1.7 ExecutorSandbox (73 LOC) -- MOSTLY STUBS

**File:** `sandbox/executor_sandbox.py`

**What's good:**
- Correct composition: delegates to balance_manager, market_data, order_book, event_system
- `simulate_execution` method shows the intended flow

**What's stubbed:**
- `SandboxEventSystem` is an empty class
- `order_book.apply_order(...)` called with literal `...` (not implemented)
- `event_system.simulate_order_fill(...)` called with literal `...` (not implemented)
- Return value `ExecutorSimulation(...)` with literal `...`

**Recommendation:** This routing layer is not needed in the new architecture. With
`SimulatedConnector` implementing the full ConnectorBase protocol, executors run
unmodified -- no executor sandbox wrapper is needed. The SimulatedConnector IS the
sandbox.

### 1.8 Other Components

**SandboxMarketDataProvider (140 LOC):** Dictionary-backed price/orderbook/trading-rules
store. Useful as reference but will be replaced by a `MarketDataProvider` that wraps
`SimulatedConnector` instances (just like production MDP wraps real connectors).

**MarketDynamicsSandbox (27 LOC):** Price impact placeholder. Useful concept for future
enhancement but too minimal to adopt. Keep as a design note.

**BacktestingSandbox (28 LOC):** Patches ConnectorBase with protocol objects. Broken
imports. Not usable.

**NetworkSandbox (20 LOC):** Not in the diff reviewed but listed in file tree. Likely minimal.

## 2. Live Implementations (Reference)

The branch also includes "live implementations" that wrap real `ConnectorBase` to
satisfy the protocol interfaces:

| File | LOC | Purpose |
|------|-----|---------|
| `live_balance_manager.py` | 190 | Wraps ConnectorBase balance methods |
| `live_event_manager.py` | 42 | Wraps ConnectorBase event methods |
| `live_market_manager.py` | 112 | Wraps ConnectorBase market data methods |
| `live_order_manager.py` | 114 | Wraps ConnectorBase order methods |
| `live_position_manager.py` | 23 | Wraps ConnectorBase position methods |

These are useful as **conformance references** -- they show exactly which ConnectorBase
methods need to be called for each protocol. They also have tests (862 LOC total)
that validate the wrapping behavior.

**Recommendation:** Use as test fixtures / conformance validators. When building
`SimulatedConnector`, its behavior should match these live wrappers for equivalent
inputs.

## 3. Architectural Decisions to PRESERVE

### 3.1 Protocol-First Design

The use of `typing.Protocol` (not `abc.ABC`) is the correct choice:
- Structural subtyping: `ConnectorBase` satisfies protocols without modification
- No inheritance required: `SimulatedConnector` doesn't need to extend ConnectorBase
- Runtime checkable: `isinstance(obj, MarketDataProtocol)` works for validation
- Composable: consumers can require exactly the protocols they need

### 3.2 Separation of Concerns

The decomposition into balance, market data, order, event, and position concerns is
well-conceived. Each concern can be implemented independently:
- Balance manager doesn't know about order book structure
- Event system doesn't know about balance management
- Market data provider doesn't know about order execution

### 3.3 Controller Patching Approach

Injecting a sandbox `market_data_provider` into controllers is the right approach.
Controllers interact with the world exclusively through their MDP reference. By
replacing it, we capture all controller-to-market interactions.

### 3.4 Per-Connector Balance Isolation

The `SandboxBalanceManager` correctly isolates balances per connector name. This
matches production behavior where each exchange connector maintains independent
balances.

## 4. Architectural Decisions to CHANGE

### 4.1 Do NOT Subclass CandlesBase for Sandbox Candles

The `SandboxCandleFeed` extends `CandlesBase`, pulling in hummingbot Cython
dependencies. Instead:
- Create a standalone `ReplayableCandleFeed` class
- Implement protocol conformance with `CandlesBase` interface
- Use `hb-candles-feed` package types where possible
- Integration with hummingbot happens in `hb_compat/` module only

### 4.2 Do NOT Patch ConnectorBase Methods

The `BacktestingSandbox` approach of patching `connector.market_proto = ...` is
fragile and violates the principle of least surprise. Instead:
- Create `SimulatedConnector` as a complete ConnectorBase replacement
- Inject `SimulatedConnector` instances into `strategy.connectors` dict
- All executor and MDP code runs unmodified against the new connector

### 4.3 Use hb_compat Pattern for Hummingbot Integration

The dev/sandboxing branch directly imports from hummingbot everywhere. The sub-package
should:
- Have zero hummingbot imports in `core/`, `protocols/`, `simulator/`, `replay/`
- Concentrate ALL hummingbot imports in `hb_compat/`
- Define standalone types that mirror hummingbot types where needed
- Provide adapter/bridge classes in `hb_compat/` that translate between worlds

### 4.4 SimulatedConnector Instead of ExchangeSandbox Wrapper

The ExchangeSandbox tried to be a thin layer around sandbox components. This creates
an extra abstraction that executors don't expect. Instead:
- `SimulatedConnector` directly implements the full protocol suite
- It contains a balance manager, matching engine, and event emitter internally
- From the executor's perspective, it IS a connector -- not a wrapper around one

### 4.5 Matching Engine as a Pluggable Strategy

The dev/sandboxing branch has `_should_fill_order` as a method on `ExchangeSandbox`.
Instead, the matching engine should be pluggable:
- `ImmediateFillEngine`: fills at mid price immediately (fastest, for unit tests)
- `LimitOrderEngine`: fills when price crosses limit level (standard backtesting)
- `OrderBookEngine`: fills against simulated order book depth (realistic simulation)
- `PartialFillEngine`: fills in chunks based on available liquidity

This allows the same SimulatedConnector to be used for different fidelity levels.

## 5. Reuse Summary

| Component | Action | Target Location |
|-----------|--------|----------------|
| Protocol definitions | **Port + update** | `market_simulator/protocols/` |
| SandboxBalanceManager | **Adopt + extend** | `market_simulator/simulator/balance_manager.py` |
| SandboxOrderBook | **Adopt structure** | `market_simulator/simulator/order_book.py` |
| SandboxCandleFeed | **Rewrite standalone** | `market_simulator/replay/candle_replay.py` |
| ControllerSandbox | **Move to hb_compat** | `market_simulator/hb_compat/controller_patch.py` |
| ExchangeSandbox | **Do not adopt** | Replace with SimulatedConnector |
| ExecutorSandbox | **Do not adopt** | Not needed with SimulatedConnector |
| SandboxMarketDataProvider | **Reference only** | MDP wraps SimulatedConnector directly |
| MarketDynamicsSandbox | **Future enhancement** | Design note for v2 |
| BacktestingSandbox | **Do not adopt** | Replace with SimulatedConnector injection |
| Live implementations | **Use as test fixtures** | Conformance validation |
| Tests | **Port applicable ones** | `tests/unit/` |

## 6. Migration Path

1. **Phase 1:** Port protocol definitions to `market_simulator/protocols/`. Update
   method signatures to match actual ConnectorBase usage (per connector interface
   analysis). Pure addition, no risk.

2. **Phase 2:** Port SandboxBalanceManager to `market_simulator/simulator/balance_manager.py`.
   Add missing features: cancellation balance release, partial fills, BudgetChecker
   compatibility wrapper.

3. **Phase 3:** Build matching engine (`market_simulator/simulator/matching_engine.py`)
   with pluggable fill strategies. Start with `ImmediateFillEngine` for testing.

4. **Phase 4:** Build `SimulatedConnector` combining balance manager, matching engine,
   order book, and event emission. Verify protocol conformance against all 5 protocols.

5. **Phase 5:** Build `hb_compat/` integration: inject SimulatedConnector into
   StrategyV2Base, patch MarketDataProvider, provide simulated Clock.

6. **Phase 6:** End-to-end test: run a PositionExecutor through its complete lifecycle
   against SimulatedConnector. Validate event sequence matches production.
