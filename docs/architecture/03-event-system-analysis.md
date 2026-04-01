# Event System Analysis -- PubSub, Events, and Simulation Requirements

This document analyzes the complete event system that connects connectors to executors.
Understanding this system is critical for `SimulatedConnector` because executors depend
on receiving events in the exact sequence and with the exact data classes that real
exchanges produce.

## 1. Event Class Hierarchy

The event system is built on Cython-backed PubSub infrastructure with Python event
forwarders that route typed events to executor handler methods.

```mermaid
classDiagram
    class PubSub {
        <<Cython>>
        +c_add_listener(event_tag: int64, listener: EventListener)
        +c_remove_listener(event_tag: int64, listener: EventListener)
        +c_trigger_event(event_tag: int64, message: object)
        +c_get_listeners(event_tag: int64)
        +c_remove_dead_listeners(event_tag: int64)
        -_events: Events (C++ map of int64 to set of PyRef)
    }

    class NetworkIterator {
        <<Cython>>
        +current_timestamp: float
        +start(clock: Clock, timestamp: float)
        +stop(clock: Clock)
        +tick(timestamp: float)
    }

    class ConnectorBase {
        <<Cython>>
        +buy(pair, amount, type, price) str
        +sell(pair, amount, type, price) str
        +cancel(pair, order_id)
        +get_balance(currency) Decimal
        +get_available_balance(currency) Decimal
    }

    class ExchangeBase {
        <<Cython>>
        +get_price_by_type(pair, price_type) Decimal
        +get_order_book(pair) OrderBook
        +trading_rules: Dict~str, TradingRule~
        +budget_checker: BudgetChecker
        +_order_tracker: ClientOrderTracker
    }

    class ExchangePyBase {
        +_create_order(trade_type, order_id, pair, amount, type, price)
        +_execute_cancel(pair, order_id)
        +_user_stream_event_listener()
    }

    class OrderBook {
        <<Cython>>
        +apply_diffs(bids, asks, update_id)
        +apply_snapshot(bids, asks)
        +apply_trade(event: OrderBookTradeEvent)
        +best_bid: float
        +best_ask: float
        +last_trade_price: float
    }

    class EventListener {
        <<Cython>>
        +current_event_tag: int64
        +current_event_caller: PubSub
        +__call__(arg)
    }

    class EventForwarder {
        +__call__(arg)
        -_to_function: Callable~any, None~
    }

    class SourceInfoEventForwarder {
        +__call__(arg)
        -_to_function: Callable~int, PubSub, any, None~
    }

    PubSub <|-- NetworkIterator
    NetworkIterator <|-- ConnectorBase
    ConnectorBase <|-- ExchangeBase
    ExchangeBase <|-- ExchangePyBase
    PubSub <|-- OrderBook
    EventListener <|-- EventForwarder
    EventListener <|-- SourceInfoEventForwarder
```

### Key Design Points

1. **PubSub is Cython** -- uses weak references to listeners and C++ maps for O(1) dispatch.
   SimulatedConnector needs a pure-Python compatible PubSub or must satisfy the same
   `add_listener`/`remove_listener`/`trigger_event` protocol.

2. **EventListener carries context** -- when `PubSub.c_trigger_event()` fires, it sets
   `listener.current_event_tag` and `listener.current_event_caller` before calling
   `listener.__call__(arg)`. This is how `SourceInfoEventForwarder` gets the event tag
   and source connector passed to the executor handler.

3. **ConnectorBase inherits PubSub** -- every connector IS a PubSub. Executors call
   `connector.add_listener(MarketEvent.OrderFilled, forwarder)` directly on the connector.

## 2. Event Flow: Connector to Executor

The complete chain from event emission to executor handler has three stages:
internal state update, PubSub dispatch, and forwarder routing.

```mermaid
sequenceDiagram
    participant Track as ClientOrderTracker
    participant Conn as ConnectorBase (PubSub)
    participant Fwd as SourceInfoEventForwarder
    participant Exec as ExecutorBase

    Note over Track: Exchange reports order filled
    Track->>Track: process_trade_update(TradeUpdate)
    Track->>Track: order.update_with_trade_update()

    Track->>Conn: trigger_event(MarketEvent.OrderFilled, OrderFilledEvent)

    Note over Conn: PubSub dispatch loop
    Conn->>Conn: c_trigger_event(107, event)
    loop For each listener registered for tag 107
        Conn->>Fwd: listener.current_event_tag = 107
        Conn->>Fwd: listener.current_event_caller = self
        Conn->>Fwd: listener.__call__(event)
    end

    Fwd->>Exec: _to_function(event_tag=107, market=connector, event=OrderFilledEvent)
    Note over Exec: Executor processes fill
    Exec->>Exec: process_order_filled_event(event_tag, market, event)
    Exec->>Exec: Update internal P&L, filled amounts, order state

    Note over Track: All fills processed, order complete
    Track->>Track: process_order_update(OrderUpdate(FILLED))
    Track->>Conn: trigger_event(MarketEvent.BuyOrderCompleted, BuyOrderCompletedEvent)
    Conn->>Fwd: dispatch to registered listener
    Fwd->>Exec: process_order_completed_event(event_tag, market, event)
```

### Registration Flow

When an executor starts, it registers 7 event forwarders on each connector:

```mermaid
sequenceDiagram
    participant Orch as ExecutorOrchestrator
    participant Exec as ExecutorBase
    participant Conn as ConnectorBase

    Orch->>Exec: start()
    Exec->>Exec: register_events()

    loop For each connector in self.connectors
        loop For each (event, forwarder) in _event_pairs
            Exec->>Conn: add_listener(MarketEvent.OrderCancelled, _cancel_order_forwarder)
            Exec->>Conn: add_listener(MarketEvent.BuyOrderCreated, _create_buy_order_forwarder)
            Exec->>Conn: add_listener(MarketEvent.SellOrderCreated, _create_sell_order_forwarder)
            Exec->>Conn: add_listener(MarketEvent.OrderFilled, _fill_order_forwarder)
            Exec->>Conn: add_listener(MarketEvent.BuyOrderCompleted, _complete_buy_order_forwarder)
            Exec->>Conn: add_listener(MarketEvent.SellOrderCompleted, _complete_sell_order_forwarder)
            Exec->>Conn: add_listener(MarketEvent.OrderFailure, _failed_order_forwarder)
        end
    end
```

### Deregistration Flow

When an executor stops (terminal state), it unregisters all forwarders:

```mermaid
sequenceDiagram
    participant Exec as ExecutorBase
    participant Conn as ConnectorBase

    Exec->>Exec: stop()
    Exec->>Exec: unregister_events()

    loop For each connector
        loop For each (event, forwarder) in _event_pairs
            Exec->>Conn: remove_listener(event, forwarder)
        end
    end
```

## 3. Complete MarketEvent Type Reference

All `MarketEvent` types, their integer tags, associated data classes, and executor
handler methods.

```mermaid
classDiagram
    class MarketEvent {
        <<enumeration>>
        ReceivedAsset = 101
        BuyOrderCompleted = 102
        SellOrderCompleted = 103
        WithdrawAsset = 105
        OrderCancelled = 106
        OrderFilled = 107
        OrderExpired = 108
        OrderUpdate = 109
        TradeUpdate = 110
        OrderFailure = 198
        TransactionFailure = 199
        BuyOrderCreated = 200
        SellOrderCreated = 201
        FundingPaymentCompleted = 202
        FundingInfo = 203
    }

    class BuyOrderCreatedEvent {
        +timestamp: float
        +order_type: OrderType
        +trading_pair: str
        +amount: Decimal
        +price: Decimal
        +order_id: str
        +creation_timestamp: float
        +exchange_order_id: str
        +leverage: int
        +position: str
    }

    class SellOrderCreatedEvent {
        +timestamp: float
        +order_type: OrderType
        +trading_pair: str
        +amount: Decimal
        +price: Decimal
        +order_id: str
        +creation_timestamp: float
        +exchange_order_id: str
        +leverage: int
        +position: str
    }

    class OrderFilledEvent {
        +timestamp: float
        +order_id: str
        +trading_pair: str
        +trade_type: TradeType
        +order_type: OrderType
        +price: Decimal
        +amount: Decimal
        +trade_fee: TradeFeeBase
        +exchange_trade_id: str
        +leverage: int
        +position: str
    }

    class BuyOrderCompletedEvent {
        +timestamp: float
        +order_id: str
        +base_asset: str
        +quote_asset: str
        +base_asset_amount: Decimal
        +quote_asset_amount: Decimal
        +order_type: OrderType
        +exchange_order_id: str
    }

    class SellOrderCompletedEvent {
        +timestamp: float
        +order_id: str
        +base_asset: str
        +quote_asset: str
        +base_asset_amount: Decimal
        +quote_asset_amount: Decimal
        +order_type: OrderType
        +exchange_order_id: str
    }

    class OrderCancelledEvent {
        +timestamp: float
        +order_id: str
        +exchange_order_id: str
    }

    class MarketOrderFailureEvent {
        +timestamp: float
        +order_id: str
        +order_type: OrderType
    }
```

### Event-to-Handler Mapping

| MarketEvent (tag) | Event Data Class | Executor Handler | Key Data Used by Executor |
|-------------------|-----------------|-----------------|---------------------------|
| `BuyOrderCreated` (200) | `BuyOrderCreatedEvent` | `process_order_created_event` | `order_id`, `timestamp`, `price`, `amount` |
| `SellOrderCreated` (201) | `SellOrderCreatedEvent` | `process_order_created_event` | `order_id`, `timestamp`, `price`, `amount` |
| `OrderFilled` (107) | `OrderFilledEvent` | `process_order_filled_event` | `order_id`, `price`, `amount`, `trade_fee`, `trade_type` |
| `BuyOrderCompleted` (102) | `BuyOrderCompletedEvent` | `process_order_completed_event` | `order_id`, `base_asset_amount`, `quote_asset_amount` |
| `SellOrderCompleted` (103) | `SellOrderCompletedEvent` | `process_order_completed_event` | `order_id`, `base_asset_amount`, `quote_asset_amount` |
| `OrderCancelled` (106) | `OrderCancelledEvent` | `process_order_canceled_event` | `order_id`, `timestamp` |
| `OrderFailure` (198) | `MarketOrderFailureEvent` | `process_order_failed_event` | `order_id`, `order_type` |

### Events NOT Used by ExecutorBase

These `MarketEvent` types exist but executors do not subscribe to them:

| MarketEvent | Used By | Notes |
|-------------|---------|-------|
| `ReceivedAsset` (101) | Legacy strategies | Not relevant to strategy_v2 |
| `WithdrawAsset` (105) | Legacy strategies | Not relevant to strategy_v2 |
| `OrderExpired` (108) | Not actively used | Could be useful for time-limited orders |
| `OrderUpdate` (109) | Internal tracker | ClientOrderTracker uses this internally |
| `TradeUpdate` (110) | Internal tracker | ClientOrderTracker uses this internally |
| `TransactionFailure` (199) | Gateway connectors | For blockchain transaction failures |
| `FundingPaymentCompleted` (202) | Perpetual MDP | MarketDataProvider tracks funding payments |
| `FundingInfo` (203) | Perpetual MDP | MarketDataProvider tracks funding rates |

## 4. Event Simulation Requirements

A `SimulatedConnector` must emit events in the exact sequence and with the exact data
classes shown below. The `ClientOrderTracker` handles most of this automatically when
driven by `TradeUpdate` and `OrderUpdate` objects, but the simulator must ensure correct
ordering.

### 4.1 Successful Market Order

```mermaid
sequenceDiagram
    participant SC as SimulatedConnector
    participant Track as ClientOrderTracker
    participant Exec as ExecutorBase

    Note over SC: Executor calls strategy.buy() -> connector.buy()

    SC->>Track: start_tracking_order(InFlightOrder(PENDING_CREATE))
    SC->>SC: Generate exchange_order_id (local counter)

    SC->>Track: process_order_update(OrderUpdate(OPEN))
    Track->>SC: trigger_event(BuyOrderCreated, BuyOrderCreatedEvent)
    SC-->>Exec: BuyOrderCreated event via PubSub

    Note over SC: Market order fills immediately at best price
    SC->>Track: process_trade_update(TradeUpdate(price, amount, fee))
    Track->>SC: trigger_event(OrderFilled, OrderFilledEvent)
    SC-->>Exec: OrderFilled event via PubSub

    SC->>Track: process_order_update(OrderUpdate(FILLED))
    Track->>SC: trigger_event(BuyOrderCompleted, BuyOrderCompletedEvent)
    SC-->>Exec: BuyOrderCompleted event via PubSub

    Note over Exec: Executor updates internal state:<br/>entry price, filled amount, P&L
```

### 4.2 Successful Limit Order (Deferred Fill)

```mermaid
sequenceDiagram
    participant SC as SimulatedConnector
    participant Track as ClientOrderTracker
    participant Exec as ExecutorBase
    participant ME as MatchingEngine

    Note over SC: Executor places limit buy at $100

    SC->>Track: start_tracking_order(InFlightOrder(PENDING_CREATE))
    SC->>Track: process_order_update(OrderUpdate(OPEN))
    Track->>SC: trigger_event(BuyOrderCreated, BuyOrderCreatedEvent)
    SC-->>Exec: BuyOrderCreated event

    Note over SC: Order enters matching engine queue

    loop On each tick / order book update
        ME->>ME: Check: best_ask <= $100?
        Note over ME: Not yet... price is $101
    end

    Note over SC: Price drops, best_ask = $99.50

    ME->>ME: best_ask <= $100 -- FILL!
    ME->>SC: Fill at $100 (limit price, not market price)

    SC->>Track: process_trade_update(TradeUpdate(price=$100, amount, fee))
    Track->>SC: trigger_event(OrderFilled, OrderFilledEvent)
    SC-->>Exec: OrderFilled event

    SC->>Track: process_order_update(OrderUpdate(FILLED))
    Track->>SC: trigger_event(BuyOrderCompleted, BuyOrderCompletedEvent)
    SC-->>Exec: BuyOrderCompleted event
```

### 4.3 Partial Fill (Multiple Fills)

```mermaid
sequenceDiagram
    participant SC as SimulatedConnector
    participant Track as ClientOrderTracker
    participant Exec as ExecutorBase

    Note over SC: Executor places limit buy for 10 BTC at $100

    SC->>Track: start_tracking_order(InFlightOrder(PENDING_CREATE))
    SC->>Track: process_order_update(OrderUpdate(OPEN))
    Track->>SC: trigger_event(BuyOrderCreated, BuyOrderCreatedEvent(amount=10))
    SC-->>Exec: BuyOrderCreated event

    Note over SC: First partial fill: 3 BTC at $100

    SC->>Track: process_trade_update(TradeUpdate(amount=3, price=$100))
    Track->>SC: trigger_event(OrderFilled, OrderFilledEvent(amount=3))
    SC-->>Exec: OrderFilled event (partial)

    Note over SC: Second partial fill: 7 BTC at $100

    SC->>Track: process_trade_update(TradeUpdate(amount=7, price=$100))
    Track->>SC: trigger_event(OrderFilled, OrderFilledEvent(amount=7))
    SC-->>Exec: OrderFilled event (partial)

    SC->>Track: process_order_update(OrderUpdate(FILLED))
    Track->>SC: trigger_event(BuyOrderCompleted, BuyOrderCompletedEvent(amount=10))
    SC-->>Exec: BuyOrderCompleted event (total)
```

### 4.4 Order Cancellation

```mermaid
sequenceDiagram
    participant SC as SimulatedConnector
    participant Track as ClientOrderTracker
    participant Exec as ExecutorBase

    Note over SC: Executor places limit order

    SC->>Track: start_tracking_order(InFlightOrder(PENDING_CREATE))
    SC->>Track: process_order_update(OrderUpdate(OPEN))
    Track->>SC: trigger_event(BuyOrderCreated, BuyOrderCreatedEvent)
    SC-->>Exec: BuyOrderCreated event

    Note over SC: Controller decides to stop executor
    Note over SC: Executor calls strategy.cancel()

    SC->>Track: process_order_update(OrderUpdate(CANCELED))
    Track->>SC: trigger_event(OrderCancelled, OrderCancelledEvent)
    SC-->>Exec: OrderCancelled event

    Note over Exec: Executor releases locked balance,<br/>transitions to SHUTTING_DOWN or TERMINATED
```

### 4.5 Order Failure

```mermaid
sequenceDiagram
    participant SC as SimulatedConnector
    participant Track as ClientOrderTracker
    participant Exec as ExecutorBase

    Note over SC: Executor tries to place order<br/>but validation fails (insufficient balance,<br/>below min size, etc.)

    SC->>Track: start_tracking_order(InFlightOrder(PENDING_CREATE))
    SC->>Track: process_order_update(OrderUpdate(FAILED))
    Track->>SC: trigger_event(OrderFailure, MarketOrderFailureEvent)
    SC-->>Exec: OrderFailure event

    Note over Exec: Executor increments retry counter,<br/>may retry or transition to TERMINATED
```

## 5. Critical Timing Constraint

The `ClientOrderTracker._process_order_update()` method has a critical timing
dependency: when it receives a `FILLED` state update, it waits up to **5 seconds**
for trade updates to arrive first:

```mermaid
sequenceDiagram
    participant Track as ClientOrderTracker

    Note over Track: Receives OrderUpdate(FILLED)

    alt Trade updates already processed
        Track->>Track: order.is_done = True (fills already recorded)
        Track->>Track: Emit BuyOrderCompleted immediately
    else Trade updates not yet received
        loop Wait up to 5 seconds
            Track->>Track: Check: order.is_filled?
            Note over Track: asyncio.sleep(0.5) between checks
        end
        Track->>Track: Emit BuyOrderCompleted (even if fills incomplete)
    end
```

**Simulation requirement:** The `SimulatedConnector` must always process
`TradeUpdate` objects **before** the final `OrderUpdate(FILLED)`. This avoids the
5-second wait and ensures fills are recorded before the completion event.

Correct ordering:
1. `process_trade_update(...)` -- records the fill
2. `process_order_update(OrderUpdate(FILLED))` -- emits completion

Incorrect ordering (causes 5s delay):
1. `process_order_update(OrderUpdate(FILLED))` -- waits for fills
2. `process_trade_update(...)` -- arrives during the wait

## 6. PubSub Implementation for SimulatedConnector

The `SimulatedConnector` needs PubSub-compatible event dispatch. Two options:

### Option A: Extend ExchangePyBase (uses existing Cython PubSub)

```mermaid
graph TD
    subgraph "Option A: Inheritance"
        PubSub_A["PubSub (Cython)"]
        NI_A["NetworkIterator"]
        CB_A["ConnectorBase"]
        EB_A["ExchangeBase"]
        EPB_A["ExchangePyBase"]
        SC_A["SimulatedConnector"]

        PubSub_A --> NI_A --> CB_A --> EB_A --> EPB_A --> SC_A
    end

    style SC_A fill:#6bcb77,color:#fff
    style PubSub_A fill:#ff6b6b,color:#fff
    style NI_A fill:#ff6b6b,color:#fff
```

**Pros:** Full compatibility; `ClientOrderTracker` works unmodified.
**Cons:** Pulls in Cython dependencies, `NetworkIterator` lifecycle, network stubs.

### Option B: Protocol-Based (pure Python PubSub)

```mermaid
graph TD
    subgraph "Option B: Protocols"
        Proto["EventSourceProtocol"]
        PyPubSub["PurePythonPubSub"]
        SC_B["SimulatedConnector"]
        SimTracker["SimulatedOrderTracker"]

        Proto -.->|"satisfies"| SC_B
        PyPubSub --> SC_B
        SimTracker --> SC_B
    end

    style SC_B fill:#6bcb77,color:#fff
    style PyPubSub fill:#6bcb77,color:#fff
    style SimTracker fill:#6bcb77,color:#fff
```

**Pros:** No Cython dependency; pure Python; standalone testable.
**Cons:** Must reimplement `ClientOrderTracker` event emission logic; must ensure
`SourceInfoEventForwarder` receives `current_event_tag` and `current_event_caller`.

### Recommended Approach

Start with **Option A** (extend `ExchangePyBase`) in the `hb_compat` layer. This
gets correct behavior immediately with minimal risk. In parallel, build a pure-Python
PubSub in `market_simulator/core/` for standalone testing. The `hb_compat` adapter
bridges the two worlds.

## 7. Event Filtering in Executors

A critical detail: multiple executors listen to the **same** connector's events
simultaneously. Each executor receives **all** events from its connectors, then
filters internally by checking the `order_id` against its own tracked orders.

```mermaid
graph TD
    Conn["ConnectorBase<br/>trigger_event(OrderFilled, event)"]

    Conn --> E1["Executor A<br/>process_order_filled_event()"]
    Conn --> E2["Executor B<br/>process_order_filled_event()"]
    Conn --> E3["Executor C<br/>process_order_filled_event()"]

    E1 --> Check1{"event.order_id<br/>in my orders?"}
    E2 --> Check2{"event.order_id<br/>in my orders?"}
    E3 --> Check3{"event.order_id<br/>in my orders?"}

    Check1 -->|Yes| Handle1["Update P&L, state"]
    Check1 -->|No| Ignore1["Ignore"]
    Check2 -->|Yes| Handle2["Update P&L, state"]
    Check2 -->|No| Ignore2["Ignore"]
    Check3 -->|Yes| Handle3["Update P&L, state"]
    Check3 -->|No| Ignore3["Ignore"]
```

**Simulation implication:** The `SimulatedConnector` does not need to route events
to specific executors. It fires events on the PubSub; all registered listeners
receive them; each executor filters for its own orders. This is the same pattern
as production.
