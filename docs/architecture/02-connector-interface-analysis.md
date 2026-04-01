# ConnectorBase Interface Analysis

This document maps the complete surface area of `ConnectorBase` as consumed by the
strategy_v2 stack, proposes a decomposition into composable Protocol interfaces, and
outlines a phased refactoring path.

## 1. Current ConnectorBase Surface Area

`ConnectorBase` is a Cython class (`connector_base.pyx`) extending `NetworkIterator`.
It is approximately 545 lines and provides a monolithic interface covering six distinct
concerns. Below is every method/property that strategy_v2 consumers actually call,
grouped by concern.

### 1.1 Price / Market Data

| Method/Property | Signature | Called By |
|----------------|-----------|----------|
| `get_price_by_type` | `(trading_pair: str, price_type: PriceType) -> Decimal` | ExecutorBase.get_price, MarketDataProvider.get_price_by_type |
| `get_order_book` | `(trading_pair: str) -> OrderBook` | ExecutorBase.get_order_book, MarketDataProvider.get_order_book |
| `get_price` | `(trading_pair: str, is_buy: bool, amount: Decimal) -> Decimal` | ConnectorBase internals |
| `get_quote_price` | `async (trading_pair: str, is_buy: bool, amount: Decimal) -> Decimal` | ArbitrageExecutor, XEMMExecutor |
| `get_funding_info` | `(trading_pair: str) -> FundingInfo` | MarketDataProvider.get_funding_info (perpetual only) |
| `trading_rules` | `@property -> Dict[str, TradingRule]` | ExecutorBase.get_trading_rules, DCAExecutor (min_order_size, min_notional_size) |

**Note:** `get_price_by_type` is actually defined on `ExchangeBase` (which extends
`ConnectorBase`), not on `ConnectorBase` itself. This is important -- it means a
SimulatedConnector must extend at least `ExchangeBase` level, or we must use protocols.

### 1.2 Balance Management

| Method/Property | Signature | Called By |
|----------------|-----------|----------|
| `get_balance` | `(currency: str) -> Decimal` | ExecutorBase.get_balance, MarketDataProvider.get_balance |
| `get_available_balance` | `(currency: str) -> Decimal` | ExecutorBase.get_available_balance, ArbitrageExecutor, MarketDataProvider |
| `get_all_balances` | `() -> Dict[str, Decimal]` | StrategyV2Base (status display) |
| `budget_checker` | `@property -> BudgetChecker` | ExecutorBase.adjust_order_candidates, lock/unlock_order_candidate |
| `available_balances` | `@property -> Dict[str, Decimal]` | BudgetChecker internals |

### 1.3 Order Management

| Method/Property | Signature | Called By |
|----------------|-----------|----------|
| `buy` | `(trading_pair, amount, order_type, price, **kwargs) -> str` | StrategyV2Base.buy -> connector.buy |
| `sell` | `(trading_pair, amount, order_type, price, **kwargs) -> str` | StrategyV2Base.sell -> connector.sell |
| `cancel` | `(trading_pair, client_order_id) -> None` | StrategyV2Base.cancel |
| `cancel_all` | `async (timeout_seconds) -> List[CancellationResult]` | StrategyV2Base on shutdown |
| `batch_order_create` | `(orders: List[LimitOrder/MarketOrder]) -> List[...]` | Not used by strategy_v2 directly |
| `batch_order_cancel` | `(orders: List[LimitOrder]) -> None` | Not used by strategy_v2 directly |
| `in_flight_orders` | `@property -> Dict[str, InFlightOrderBase]` | Balance calculations |
| `_order_tracker.fetch_order` | `(client_order_id) -> InFlightOrder` | ExecutorBase.get_in_flight_order |
| `stop_tracking_order` | `(order_id) -> None` | Internal cleanup |

**Critical detail:** ExecutorBase accesses `connectors[name]._order_tracker` directly
(private attribute). This is a tight coupling that must be addressed by either:
(a) adding `fetch_order(client_order_id)` to the public protocol, or
(b) keeping `_order_tracker` as an implementation detail of SimulatedConnector.

### 1.4 Event Emission

| Method | Signature | Called By |
|--------|-----------|----------|
| `add_listener` | `(event_tag: int, listener: EventForwarder) -> None` | ExecutorBase.register_events |
| `remove_listener` | `(event_tag: int, listener: EventForwarder) -> None` | ExecutorBase.unregister_events |
| `trigger_event` | `(event_tag: int, message) -> None` | Internal (emits MarketEvent.*) |

**Events emitted by ConnectorBase** (that executors listen to):

| MarketEvent | Event Class | Executor Usage |
|-------------|------------|----------------|
| `BuyOrderCreated` | `BuyOrderCreatedEvent` | ExecutorBase.process_order_created_event |
| `SellOrderCreated` | `SellOrderCreatedEvent` | ExecutorBase.process_order_created_event |
| `OrderFilled` | `OrderFilledEvent` | ExecutorBase.process_order_filled_event |
| `BuyOrderCompleted` | `BuyOrderCompletedEvent` | ExecutorBase.process_order_completed_event |
| `SellOrderCompleted` | `SellOrderCompletedEvent` | ExecutorBase.process_order_completed_event |
| `OrderCancelled` | `OrderCancelledEvent` | ExecutorBase.process_order_canceled_event |
| `OrderFailure` | `MarketOrderFailureEvent` | ExecutorBase.process_order_failed_event |

### 1.5 Trading Rules / Quantization

| Method | Signature | Called By |
|--------|-----------|----------|
| `quantize_order_price` | `(trading_pair, price) -> Decimal` | PositionExecutor, controllers |
| `quantize_order_amount` | `(trading_pair, amount) -> Decimal` | PositionExecutor |
| `get_order_price_quantum` | `(trading_pair, price) -> Decimal` | quantize_order_price impl |
| `get_order_size_quantum` | `(trading_pair, order_size) -> Decimal` | quantize_order_amount impl |

### 1.6 Readiness / Identity

| Method/Property | Signature | Called By |
|----------------|-----------|----------|
| `ready` | `@property -> bool` | MarketDataProvider.ready |
| `status_dict` | `@property -> Dict[str, bool]` | StrategyV2Base status display |
| `name` | `@property -> str` | Various (identity, logging) |
| `display_name` | `@property -> str` | Event logging |
| `trading_pairs` | `@property -> List[str]` | MarketDataProvider, initialization |

## 2. Proposed Protocol Decomposition

The following protocols capture the complete interface needed by strategy_v2 consumers.
Each protocol is minimal and independently implementable.

```python
from decimal import Decimal
from typing import Dict, List, Protocol, runtime_checkable

from hummingbot.connector.trading_rule import TradingRule
from hummingbot.core.data_type.common import OrderType, PriceType, TradeType, PositionAction
from hummingbot.core.data_type.order_book import OrderBook


@runtime_checkable
class MarketDataProtocol(Protocol):
    """Read-only market data access."""

    def get_price_by_type(self, trading_pair: str, price_type: PriceType) -> Decimal: ...

    def get_order_book(self, trading_pair: str) -> OrderBook: ...

    @property
    def trading_rules(self) -> Dict[str, TradingRule]: ...

    def quantize_order_price(self, trading_pair: str, price: Decimal) -> Decimal: ...

    def quantize_order_amount(self, trading_pair: str, amount: Decimal) -> Decimal: ...

    async def get_quote_price(
        self, trading_pair: str, is_buy: bool, amount: Decimal
    ) -> Decimal: ...


@runtime_checkable
class BalanceProtocol(Protocol):
    """Balance queries and order validation."""

    def get_balance(self, currency: str) -> Decimal: ...

    def get_available_balance(self, currency: str) -> Decimal: ...

    def get_all_balances(self) -> Dict[str, Decimal]: ...

    @property
    def budget_checker(self) -> "BudgetChecker": ...


@runtime_checkable
class OrderExecutionProtocol(Protocol):
    """Order lifecycle management."""

    def buy(
        self,
        trading_pair: str,
        amount: Decimal,
        order_type: OrderType,
        price: Decimal,
        **kwargs,
    ) -> str: ...

    def sell(
        self,
        trading_pair: str,
        amount: Decimal,
        order_type: OrderType,
        price: Decimal,
        **kwargs,
    ) -> str: ...

    def cancel(self, trading_pair: str, client_order_id: str) -> None: ...

    @property
    def in_flight_orders(self) -> Dict[str, "InFlightOrderBase"]: ...

    def get_in_flight_order(self, client_order_id: str) -> "InFlightOrder": ...


@runtime_checkable
class EventSourceProtocol(Protocol):
    """Event subscription and emission."""

    def add_listener(self, event_tag: int, listener) -> None: ...

    def remove_listener(self, event_tag: int, listener) -> None: ...

    def trigger_event(self, event_tag: int, message) -> None: ...


@runtime_checkable
class ReadinessProtocol(Protocol):
    """Connector identity and readiness."""

    @property
    def ready(self) -> bool: ...

    @property
    def name(self) -> str: ...

    @property
    def trading_pairs(self) -> List[str]: ...

    @property
    def status_dict(self) -> Dict[str, bool]: ...


@runtime_checkable
class PerpetualProtocol(Protocol):
    """Perpetual/derivative-specific operations (optional)."""

    def get_funding_info(self, trading_pair: str) -> "FundingInfo": ...

    def set_leverage(self, trading_pair: str, leverage: int) -> None: ...

    def set_position_mode(self, position_mode: "PositionMode") -> None: ...
```

## 3. Who Uses What

This matrix maps each strategy_v2 consumer to the protocols it actually requires.
The key finding is that **no single consumer needs all protocols**.

| Consumer | MarketData | Balance | OrderExec | EventSource | Readiness | Perpetual |
|----------|:----------:|:-------:|:---------:|:-----------:|:---------:|:---------:|
| **ExecutorBase** | YES | YES | YES (via strategy) | YES | no | no |
| **PositionExecutor** | YES | YES | YES | YES | no | OPTIONAL |
| **DCAExecutor** | YES | YES | YES | YES | no | OPTIONAL |
| **ArbitrageExecutor** | YES | YES | YES | YES | no | no |
| **XEMMExecutor** | YES | YES | YES | YES | no | OPTIONAL |
| **GridExecutor** | YES | YES | YES | YES | no | OPTIONAL |
| **OrderExecutor** | YES | YES | YES | YES | no | OPTIONAL |
| **TWAPExecutor** | YES | YES | YES | YES | no | no |
| **LPExecutor** | no | no | YES | YES | no | no |
| **ControllerBase** | YES (via MDP) | no | no | no | no | no |
| **MarketDataProvider** | YES | YES | no | no | YES | OPTIONAL |
| **StrategyV2Base** | no | YES | YES | no | YES | no |
| **ExecutorOrchestrator** | no | no | no | no | no | no |

**Key observations:**

1. **Controllers never touch connectors directly.** They go through `MarketDataProvider`.
   This means controllers are already decoupled and only need `MarketDataProtocol` (via MDP).

2. **Executors need MarketData + Balance + OrderExec + EventSource.** This is the core
   "simulated connector" interface. Readiness is not checked by executors.

3. **MarketDataProvider needs MarketData + Balance + Readiness.** It delegates order book
   and price queries to the connector and checks readiness for the controller loop.

4. **StrategyV2Base needs Balance + OrderExec + Readiness.** It provides `buy()`/`sell()`
   convenience methods that delegate to connectors, and displays balance info.

5. **Perpetual protocol is optional.** Only needed when the connector name contains
   "perpetual". Can be implemented as a separate mixin.

## 4. Refactoring Path

### Phase 1: Define Protocols Alongside ConnectorBase

**Goal:** Introduce protocol types without changing any existing code.

- Create `hummingbot/connector/protocols.py` with all protocol definitions.
- Verify that `ConnectorBase` (and `ExchangeBase`, `PerpetualDerivativePyBase`)
  structurally satisfy all protocols using `isinstance` checks in tests.
- No consumer code changes. ConnectorBase continues to work exactly as before.

**Risk:** None. This is additive only.
**Effort:** Small (1-2 days).

### Phase 2: Refactor Consumers to Accept Protocol Types

**Goal:** Loosen type annotations so consumers accept protocols instead of ConnectorBase.

- `ExecutorBase.__init__` changes `connectors: Dict[str, ConnectorBase]` to accept
  `Dict[str, MarketDataProtocol & BalanceProtocol & OrderExecutionProtocol & EventSourceProtocol]`
  (or a union type alias like `SimulatableConnector`).
- `MarketDataProvider.__init__` changes `connectors: Dict[str, ConnectorBase]` to
  `Dict[str, MarketDataProtocol & BalanceProtocol & ReadinessProtocol]`.
- Existing code continues to work because `ConnectorBase` satisfies all protocols.

**Risk:** Low. Type annotations are hints, not enforcement in CPython.
**Effort:** Medium (3-5 days). Requires careful testing.

### Phase 3: Create SimulatedConnector

**Goal:** Build the market simulator's core connector.

- `SimulatedConnector` implements `MarketDataProtocol`, `BalanceProtocol`,
  `OrderExecutionProtocol`, `EventSourceProtocol`, and `ReadinessProtocol`.
- Does NOT extend `ConnectorBase` (avoids Cython dependency, NetworkIterator, etc.).
- Contains a simulated order book, balance manager, and matching engine.
- Emits the same `MarketEvent` events as real connectors.
- Can be driven by a `SimulatedClock` for deterministic backtesting.

**Risk:** Medium. Must exactly replicate the event semantics that executors expect.
**Effort:** Large (2-4 weeks).

### Phase 4: Extract into hb-connector-protocols

**Goal:** Make protocols a standalone package for reuse.

- Move protocol definitions from `hummingbot/connector/protocols.py` to
  `hb-connector-protocols` sub-package.
- hb-market-simulator depends on hb-connector-protocols.
- hummingbot core depends on hb-connector-protocols.
- Other sub-packages (hb-order-management, etc.) can also depend on protocols
  without pulling in all of hummingbot.

**Risk:** Low (protocol-only package with no implementation).
**Effort:** Small (1-2 days).

## 5. SimulatedConnector vs ConnectorBase: Key Differences

| Aspect | ConnectorBase | SimulatedConnector |
|--------|--------------|-------------------|
| **Language** | Cython (.pyx) | Pure Python |
| **Base class** | NetworkIterator | None (protocol-based) |
| **Network** | REST + WebSocket | None |
| **Order matching** | Exchange-side | Local matching engine |
| **Balances** | Fetched from exchange | In-memory balance manager |
| **Events** | Triggered by exchange responses | Triggered by matching engine |
| **Clock** | Real time via NetworkIterator | Simulated clock, advance on command |
| **Trading rules** | Fetched from exchange | Configured at initialization |
| **Order book** | Built from exchange feed | Replayed from historical data or synthetic |

## 6. Critical Implementation Notes

### 6.1 The _order_tracker Problem

`ExecutorBase.get_in_flight_order()` accesses `self.connectors[name]._order_tracker.fetch_order()`.
This is a private attribute access. Solutions:

**Option A (Recommended):** Add `get_in_flight_order(client_order_id)` to `OrderExecutionProtocol`.
SimulatedConnector implements it natively. Add a compatibility shim to `ConnectorBase` that
delegates to `_order_tracker`.

**Option B:** SimulatedConnector exposes a compatible `_order_tracker` object. This preserves
backward compatibility but perpetuates the private-attribute pattern.

### 6.2 The buy()/sell() Routing

Executors do NOT call `connector.buy()` directly. They call:
```python
self._strategy.buy(connector_name, trading_pair, amount, order_type, price, position_action)
```
Which delegates to `strategy.connectors[connector_name].buy(...)`.

For simulation, we need either:
(a) A simulated `StrategyV2Base` that routes to `SimulatedConnector`, or
(b) Inject `SimulatedConnector` instances into `strategy.connectors` dict.

Option (b) is simpler and preserves executor code paths exactly.

### 6.3 Event Semantics

The exact sequence of events matters. A real exchange produces:
1. `BuyOrderCreated` / `SellOrderCreated` (order accepted)
2. `OrderFilled` (one or more partial fills)
3. `BuyOrderCompleted` / `SellOrderCompleted` (fully filled)

Or alternatively:
1. `BuyOrderCreated` / `SellOrderCreated`
2. `OrderCancelled` or `OrderFailure`

SimulatedConnector must replicate this sequence exactly, including the distinction between
`OrderFilled` (partial) and `OrderCompleted` (final). Executors rely on this sequencing
for state transitions.

### 6.4 BudgetChecker Compatibility

`BudgetChecker` is tightly coupled to `ExchangeBase`. It reads `_account_available_balances`
and `in_flight_orders` from the connector. SimulatedConnector needs either:
(a) Its own `SimulatedBudgetChecker` implementing the same `adjust_candidates()` interface, or
(b) Compatibility with the existing `BudgetChecker` by exposing the same internal state.

Option (a) is cleaner and allows the simulator to enforce balance constraints without
depending on the real BudgetChecker implementation.
