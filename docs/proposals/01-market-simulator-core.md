# hb-market-simulator Implementation Design

This document is the detailed implementation plan for the `market_simulator` sub-package.
It covers the package architecture, component interactions, data flow, and phased
build plan.

## 1. Package Architecture

The package is organized into five modules, each with a clear responsibility boundary.
Only `hb_compat/` imports from hummingbot; all other modules are standalone.

```mermaid
graph TD
    subgraph "market_simulator"
        subgraph "protocols/"
            P1["MarketDataProtocol"]
            P2["BalanceProtocol"]
            P3["OrderExecutionProtocol"]
            P4["EventSourceProtocol"]
            P5["ReadinessProtocol"]
            P6["PerpetualProtocol"]
        end

        subgraph "core/"
            C1["SimulatedClock"]
            C2["OrderTypes / TradeTypes"]
            C3["TradingRule"]
            C4["EventBus (pure Python PubSub)"]
            C5["InFlightOrder"]
        end

        subgraph "simulator/"
            S1["SimulatedExchange"]
            S2["SimulatedOrderBook"]
            S3["BalanceManager"]
            S4["MatchingEngine"]
            S5["OrderTracker"]
            S6["FeeModel"]
        end

        subgraph "replay/"
            R1["ReplayDataSource"]
            R2["DataRecorder"]
            R3["HistoricalDataLoader"]
            R4["ReplayableCandleFeed"]
        end

        subgraph "hb_compat/"
            H1["SimulatedConnector"]
            H2["SimulatedMarketDataProvider"]
            H3["SandboxEngine"]
            H4["HbEventAdapter"]
        end
    end

    S1 -->|"implements"| P3
    S1 -->|"implements"| P4
    S2 -->|"implements"| P1
    S3 -->|"implements"| P2
    S1 --> S4
    S4 --> S2
    S1 --> S3
    S1 --> S5
    S1 --> S6
    S1 --> C4

    H1 -->|"wraps"| S1
    H1 --> S2
    H1 --> S3
    H2 -->|"wraps"| H1
    H3 --> H1
    H3 --> H2
    H4 --> C4

    R1 --> S2
    R3 --> R1
    R4 --> R3

    C1 --> S1
    C1 --> R1

    style P1 fill:#e1bee7,color:#000
    style P2 fill:#e1bee7,color:#000
    style P3 fill:#e1bee7,color:#000
    style P4 fill:#e1bee7,color:#000
    style P5 fill:#e1bee7,color:#000
    style P6 fill:#e1bee7,color:#000
    style S1 fill:#bbdefb,color:#000
    style S2 fill:#bbdefb,color:#000
    style S3 fill:#bbdefb,color:#000
    style S4 fill:#bbdefb,color:#000
    style S5 fill:#bbdefb,color:#000
    style S6 fill:#bbdefb,color:#000
    style H1 fill:#c8e6c9,color:#000
    style H2 fill:#c8e6c9,color:#000
    style H3 fill:#c8e6c9,color:#000
    style H4 fill:#c8e6c9,color:#000
    style R1 fill:#fff9c4,color:#000
    style R2 fill:#fff9c4,color:#000
    style R3 fill:#fff9c4,color:#000
    style R4 fill:#fff9c4,color:#000
    style C1 fill:#ffe0b2,color:#000
    style C2 fill:#ffe0b2,color:#000
    style C3 fill:#ffe0b2,color:#000
    style C4 fill:#ffe0b2,color:#000
    style C5 fill:#ffe0b2,color:#000
```

**Legend:**
| Color | Module | Dependency |
|-------|--------|-----------|
| Purple | `protocols/` | None (pure protocol definitions) |
| Orange | `core/` | None (standalone data types + utilities) |
| Blue | `simulator/` | `core/` and `protocols/` only |
| Yellow | `replay/` | `core/` only |
| Green | `hb_compat/` | `simulator/` + hummingbot imports |

### Module Responsibilities

| Module | Responsibility | Hummingbot Imports |
|--------|---------------|-------------------|
| `protocols/` | `@runtime_checkable` Protocol interfaces defining what a connector must provide | NONE |
| `core/` | Standalone data types: clock, order types, trading rules, pure-Python event bus | NONE |
| `simulator/` | Core simulation engine: exchange, order book, balance manager, matching engine | NONE |
| `replay/` | Historical data loading, recording, and replay | NONE |
| `hb_compat/` | Adapters that make simulator components work with hummingbot's runtime | YES (ConnectorBase, MarketEvent, etc.) |

## 2. Component Details

### 2.1 SimulatedExchange

The central component that coordinates order management, matching, balance tracking,
and event emission. It implements `OrderExecutionProtocol` and `EventSourceProtocol`.

```mermaid
classDiagram
    class SimulatedExchange {
        -_order_book: SimulatedOrderBook
        -_balance_manager: BalanceManager
        -_matching_engine: MatchingEngine
        -_order_tracker: OrderTracker
        -_fee_model: FeeModel
        -_event_bus: EventBus
        -_clock: SimulatedClock
        -_trading_rules: Dict~str, TradingRule~

        +buy(pair, amount, type, price) str
        +sell(pair, amount, type, price) str
        +cancel(pair, order_id) None
        +get_price_by_type(pair, price_type) Decimal
        +get_order_book(pair) SimulatedOrderBook
        +get_balance(currency) Decimal
        +get_available_balance(currency) Decimal
        +process_tick(timestamp) None
        +set_trading_rules(pair, rules) None
        +set_initial_balances(balances) None
    }

    class SimulatedOrderBook {
        -_bids: SortedDict~Decimal, Decimal~
        -_asks: SortedDict~Decimal, Decimal~
        -_last_trade_price: Decimal
        -_best_bid: Decimal
        -_best_ask: Decimal

        +apply_snapshot(bids, asks) None
        +apply_diffs(bids, asks) None
        +apply_trade(price, amount, side) None
        +get_price_by_type(price_type) Decimal
        +get_volume_at_price(side, price) Decimal
        +get_depth(side, levels) List
    }

    class BalanceManager {
        -_balances: Dict~str, TokenBalance~
        -_positions: Dict~str, PositionBalance~

        +get_balance(currency) Decimal
        +get_available_balance(currency) Decimal
        +lock_collateral(currency, amount) bool
        +release_collateral(currency, amount) None
        +apply_fill(base, quote, amount, price, fee, side) None
        +apply_cancel(currency, locked_amount) None
    }

    class MatchingEngine {
        <<interface>>
        +check_and_match(order, order_book) Optional~MatchResult~
    }

    class ImmediateFillEngine {
        +check_and_match(order, order_book) MatchResult
    }

    class LimitOrderEngine {
        +check_and_match(order, order_book) Optional~MatchResult~
    }

    class OrderBookDepthEngine {
        +check_and_match(order, order_book) Optional~MatchResult~
    }

    SimulatedExchange --> SimulatedOrderBook
    SimulatedExchange --> BalanceManager
    SimulatedExchange --> MatchingEngine
    MatchingEngine <|.. ImmediateFillEngine
    MatchingEngine <|.. LimitOrderEngine
    MatchingEngine <|.. OrderBookDepthEngine
```

### 2.2 MatchingEngine Strategies

The matching engine is pluggable, allowing different fill models for different testing needs.

```mermaid
graph TD
    subgraph "Matching Engine Strategies"
        IME["ImmediateFillEngine<br/>Fills at mid/best price<br/>instantly on placement"]
        LOE["LimitOrderEngine<br/>Fills when market price<br/>crosses limit level"]
        OBDE["OrderBookDepthEngine<br/>Walks order book depth<br/>for realistic slippage"]
        PFE["PartialFillEngine<br/>Fills in chunks based on<br/>available liquidity per level"]
    end

    subgraph "Use Cases"
        UT["Unit Tests<br/>(fast, deterministic)"]
        BT["Backtesting<br/>(standard accuracy)"]
        RS["Realistic Simulation<br/>(high fidelity)"]
        ST["Stress Testing<br/>(low liquidity scenarios)"]
    end

    IME --> UT
    LOE --> BT
    OBDE --> RS
    PFE --> ST

    style IME fill:#c8e6c9,color:#000
    style LOE fill:#bbdefb,color:#000
    style OBDE fill:#fff9c4,color:#000
    style PFE fill:#ffe0b2,color:#000
```

| Engine | Fill Logic | Speed | Fidelity |
|--------|-----------|-------|----------|
| `ImmediateFillEngine` | Fill at mid price immediately on `buy()`/`sell()` | Fastest | Lowest -- no order book interaction |
| `LimitOrderEngine` | Fill when candle high/low crosses limit price | Fast | Medium -- respects limit prices, no depth |
| `OrderBookDepthEngine` | Walk order book levels, compute volume-weighted fill price | Medium | High -- realistic slippage modeling |
| `PartialFillEngine` | Fill available liquidity per tick, remainder stays open | Slowest | Highest -- realistic partial fills |

### 2.3 FeeModel

```mermaid
classDiagram
    class FeeModel {
        <<interface>>
        +calculate_fee(pair, side, order_type, amount, price) TradeFee
    }

    class FlatFeeModel {
        -maker_rate: Decimal
        -taker_rate: Decimal
        +calculate_fee(...) TradeFee
    }

    class TieredFeeModel {
        -tiers: List~FeeTier~
        -trailing_volume: Decimal
        +calculate_fee(...) TradeFee
    }

    class ZeroFeeModel {
        +calculate_fee(...) TradeFee
    }

    FeeModel <|.. FlatFeeModel
    FeeModel <|.. TieredFeeModel
    FeeModel <|.. ZeroFeeModel
```

### 2.4 EventBus (Pure Python PubSub)

A lightweight, pure-Python replacement for hummingbot's Cython PubSub. Supports the
same `add_listener`/`remove_listener`/`trigger_event` interface.

```mermaid
classDiagram
    class EventBus {
        -_listeners: Dict~int, List~Callable~~
        +add_listener(event_tag: int, listener: Callable) None
        +remove_listener(event_tag: int, listener: Callable) None
        +trigger_event(event_tag: int, message: Any) None
        +clear_all_listeners() None
        +listener_count(event_tag: int) int
    }

    class SimulatedEventListener {
        +current_event_tag: int
        +current_event_caller: Any
        +__call__(arg: Any) None
    }

    EventBus --> SimulatedEventListener : dispatches to
```

In `hb_compat/`, the `HbEventAdapter` translates between the pure-Python `EventBus`
and hummingbot's Cython `PubSub` / `SourceInfoEventForwarder` system.

## 3. SimulatedExchange Order Flow

This sequence diagram shows the complete lifecycle of a simulated order, from
placement through matching to fill events and balance updates.

```mermaid
sequenceDiagram
    participant Caller as Caller (Executor via Strategy)
    participant SE as SimulatedExchange
    participant OT as OrderTracker
    participant BM as BalanceManager
    participant ME as MatchingEngine
    participant OB as SimulatedOrderBook
    participant EB as EventBus

    Note over Caller: Executor places buy order

    Caller->>SE: buy("BTC-USDT", 0.1, LIMIT, 50000)

    SE->>SE: Validate trading rules (min size, quantize)
    SE->>SE: Generate client_order_id

    SE->>BM: lock_collateral("USDT", 5000 + fee_reserve)
    alt Insufficient balance
        SE->>EB: trigger_event(OrderFailure, FailureEvent)
        SE-->>Caller: return order_id (but order will fail)
    end

    SE->>OT: track_order(InFlightOrder(PENDING_CREATE))

    SE->>OT: update_state(order_id, OPEN)
    OT->>EB: trigger_event(BuyOrderCreated, CreatedEvent)

    alt Market Order
        SE->>OB: get_price_by_type(BestAsk)
        SE->>ME: match(order, order_book)
        ME-->>SE: MatchResult(price=50100, amount=0.1)
        SE->>SE: Process fill immediately
    else Limit Order
        SE->>OT: add_to_open_orders(order)
        Note over SE: Order waits for matching on next tick
    end

    Note over SE: On tick: check open orders

    SE->>ME: check_and_match(order, order_book)
    ME->>OB: get best_ask price
    OB-->>ME: best_ask = 49900

    alt Price crosses limit (49900 <= 50000)
        ME-->>SE: MatchResult(price=50000, amount=0.1)

        SE->>SE: Calculate fee
        SE->>BM: apply_fill("BTC", "USDT", 0.1, 50000, fee, BUY)
        Note over BM: USDT: release locked, debit 5000<br/>BTC: credit 0.1<br/>Fee: debit from USDT

        SE->>OT: record_trade(TradeUpdate)
        OT->>EB: trigger_event(OrderFilled, FilledEvent)

        SE->>OT: update_state(order_id, FILLED)
        OT->>EB: trigger_event(BuyOrderCompleted, CompletedEvent)
    else Price has not crossed
        ME-->>SE: None (no match)
        Note over SE: Order remains open
    end
```

### Balance State Transitions

```mermaid
stateDiagram-v2
    state "Initial Balance" as IB
    state "Collateral Locked" as CL
    state "Fill Applied" as FA
    state "Cancel Released" as CR

    [*] --> IB: set_initial_balances()
    IB --> CL: buy() -- lock USDT collateral
    CL --> FA: OrderFilled -- debit USDT, credit BTC
    CL --> CR: cancel() -- release locked USDT
    FA --> CL: sell() -- lock BTC collateral
    CR --> CL: buy() again

    note right of IB
        USDT: total=10000, available=10000
        BTC: total=0, available=0
    end note

    note right of CL
        USDT: total=10000, available=5000
        BTC: total=0, available=0
        (5000 USDT locked for order)
    end note

    note right of FA
        USDT: total=4970, available=4970
        BTC: total=0.1, available=0.1
        (30 USDT fee deducted)
    end note
```

## 4. hb_compat Layer

The `hb_compat/` module bridges the standalone simulator with hummingbot's runtime.

```mermaid
graph TD
    subgraph "hb_compat/"
        SC["SimulatedConnector<br/>(extends ExchangePyBase)"]
        SMDP["SimulatedMarketDataProvider<br/>(subclass or wrapper)"]
        SEngine["SandboxEngine<br/>(orchestrates simulation)"]
        HbAdapter["HbEventAdapter<br/>(EventBus <-> PubSub)"]
    end

    subgraph "Standalone simulator/"
        SE["SimulatedExchange"]
        SOB["SimulatedOrderBook"]
        SBM["BalanceManager"]
        SME["MatchingEngine"]
    end

    subgraph "Hummingbot Runtime"
        SV2["StrategyV2Base"]
        ExecBase["ExecutorBase"]
        MDP["MarketDataProvider"]
        Orch["ExecutorOrchestrator"]
        COT["ClientOrderTracker"]
    end

    SC -->|"wraps"| SE
    SC -->|"uses"| COT
    SC -->|"inherits"| ExchangePyBase["ExchangePyBase"]

    SMDP -->|"wraps"| SC
    SMDP -->|"is-a"| MDP

    SEngine --> SC
    SEngine --> SMDP
    SEngine --> SimClock["SimulatedClock"]

    SV2 -->|"connectors[name]"| SC
    ExecBase -->|"connector.buy/sell"| SC
    ExecBase -->|"add_listener"| SC
    MDP -->|"delegate"| SC

    style SC fill:#c8e6c9,color:#000
    style SMDP fill:#c8e6c9,color:#000
    style SEngine fill:#c8e6c9,color:#000
```

### SimulatedConnector Strategy

The `SimulatedConnector` extends `ExchangePyBase` to inherit:
- `ClientOrderTracker` (handles order state machine and event emission)
- `BudgetChecker` (validates order sizes against available balance)
- Cython `PubSub` (event dispatch to executors)
- `NetworkIterator` lifecycle (clock integration)

It overrides the abstract methods that normally make REST API calls:
- `_place_order()` -- routes to `SimulatedExchange.buy/sell()` (synchronous)
- `_place_cancel()` -- routes to `SimulatedExchange.cancel()` (synchronous)
- `_update_balances()` -- reads from `BalanceManager` (no network)
- `_update_trading_rules()` -- returns pre-configured rules (no network)
- `_update_order_book()` -- fed by `ReplayDataSource` (no network)

This approach maximizes compatibility: the `ClientOrderTracker`'s event emission logic
runs unmodified, guaranteeing correct event sequences.

### SandboxEngine Orchestration

```mermaid
sequenceDiagram
    participant User as User Code
    participant SE as SandboxEngine
    participant Clock as SimulatedClock
    participant SC as SimulatedConnector
    participant MDP as MarketDataProvider
    participant Strat as StrategyV2Base
    participant Ctrl as ControllerBase
    participant Orch as ExecutorOrchestrator

    User->>SE: run(config, data_source, start, end)

    SE->>SC: create SimulatedConnector(trading_pairs, rules, balances)
    SE->>MDP: create MarketDataProvider({name: sc})
    SE->>Strat: create StrategyV2Base({name: sc}, config)
    Note over Strat: Creates controllers + orchestrator internally

    SE->>Clock: initialize(start_time)

    loop For each tick in [start..end]
        Clock->>Clock: advance(tick_interval)
        Clock->>SC: tick(timestamp)
        Note over SC: Feed order book update from replay
        Note over SC: Match pending orders against new book
        Clock->>Strat: tick(timestamp)
        Note over Strat: Controllers evaluate, create/stop executors
        Note over Strat: Executors place orders via SC
    end

    SE-->>User: results (executor reports, P&L, positions)
```

## 5. Implementation Phases

```mermaid
graph LR
    subgraph "Phase 1 (Week 1-2)"
        P1A["protocols/<br/>6 Protocol definitions"]
        P1B["core/<br/>SimulatedClock, EventBus,<br/>TradingRule, OrderTypes"]
        P1C["simulator/SimulatedOrderBook<br/>Bid/ask management,<br/>snapshot/diff, price queries"]
    end

    subgraph "Phase 2 (Week 2-3)"
        P2A["simulator/BalanceManager<br/>Lock/release/fill,<br/>spot + perpetual"]
        P2B["simulator/MatchingEngine<br/>ImmediateFill + LimitOrder<br/>engines"]
        P2C["simulator/FeeModel<br/>Flat + Zero fee models"]
        P2D["simulator/OrderTracker<br/>In-flight order management"]
    end

    subgraph "Phase 3 (Week 3-4)"
        P3A["simulator/SimulatedExchange<br/>Combines all simulator<br/>components"]
        P3B["Unit tests for<br/>SimulatedExchange"]
    end

    subgraph "Phase 4 (Week 4-5)"
        P4A["hb_compat/SimulatedConnector<br/>Extends ExchangePyBase"]
        P4B["hb_compat/HbEventAdapter<br/>EventBus <-> PubSub bridge"]
        P4C["Integration tests with<br/>real ExecutorBase"]
    end

    subgraph "Phase 5 (Week 5-6)"
        P5A["replay/HistoricalDataLoader<br/>Parquet/CSV loading"]
        P5B["replay/ReplayDataSource<br/>Time-ordered replay"]
        P5C["replay/DataRecorder<br/>Record live data for replay"]
        P5D["replay/ReplayableCandleFeed<br/>CandlesBase-compatible replay"]
    end

    subgraph "Phase 6 (Week 6-7)"
        P6A["hb_compat/SandboxEngine<br/>Full orchestration"]
        P6B["hb_compat/SimulatedMDP<br/>MDP wrapping SimConnector"]
        P6C["End-to-end tests:<br/>PositionExecutor lifecycle"]
        P6D["End-to-end tests:<br/>DCAExecutor lifecycle"]
    end

    P1A --> P2A
    P1B --> P2B
    P1C --> P2B
    P2A --> P3A
    P2B --> P3A
    P2C --> P3A
    P2D --> P3A
    P3A --> P4A
    P1B --> P4B
    P4A --> P6A
    P5A --> P5B
    P5B --> P6A
    P4A --> P6C
    P6A --> P6C
    P6C --> P6D
```

### Phase Deliverables

| Phase | Deliverable | Test Coverage | Dependencies |
|-------|------------|---------------|-------------|
| **1: Protocols + OrderBook** | Protocol defs, EventBus, SimulatedOrderBook, TradingRule | Unit tests for order book operations | None |
| **2: Balance + Matching** | BalanceManager, ImmediateFillEngine, LimitOrderEngine, FeeModel | Unit tests for balance lock/release/fill, matching logic | Phase 1 |
| **3: SimulatedExchange** | Combined exchange with order lifecycle | Integration tests for complete order flow | Phase 2 |
| **4: hb_compat Connector** | SimulatedConnector extending ExchangePyBase | Integration tests with real ExecutorBase event registration | Phase 3 |
| **5: Replay System** | HistoricalDataLoader, ReplayDataSource, DataRecorder | Unit tests for data loading and replay ordering | Phase 1 (core only) |
| **6: End-to-End** | SandboxEngine, full executor lifecycle tests | E2E: PositionExecutor + DCAExecutor full lifecycle | Phase 4 + 5 |

### Success Criteria Per Phase

**Phase 1 Complete When:**
- `SimulatedOrderBook` can apply snapshots, diffs, and return correct prices for all `PriceType` variants
- `EventBus` can register/deregister listeners and dispatch events
- All 6 protocols defined with `@runtime_checkable` decorators
- `isinstance(ConnectorBase_instance, MarketDataProtocol)` returns `True` (conformance check)

**Phase 2 Complete When:**
- `BalanceManager` correctly handles: initial deposit, lock on order, release on cancel, debit/credit on fill
- `BalanceManager` handles perpetual: margin lock, position open/close, realized PnL
- `ImmediateFillEngine` fills at best price for all order types
- `LimitOrderEngine` correctly identifies when limit price is crossed

**Phase 3 Complete When:**
- `SimulatedExchange.buy()` -> `OrderCreated` -> `OrderFilled` -> `OrderCompleted` event sequence verified
- `SimulatedExchange.cancel()` -> `OrderCancelled` event verified
- Balance correctly updated after fill (collateral released, assets exchanged)
- Multiple concurrent orders tracked independently

**Phase 4 Complete When:**
- `SimulatedConnector` passes `isinstance(sc, ExchangePyBase)` check
- `ExecutorBase` can `register_events()` on `SimulatedConnector`
- `ExecutorBase.place_order()` routes through `StrategyV2Base.buy()` to `SimulatedConnector`
- Events dispatched to executor correctly (verified via mock executor)

**Phase 5 Complete When:**
- `HistoricalDataLoader` loads Parquet files with OHLCV + trade data
- `ReplayDataSource` feeds data in timestamp order
- `DataRecorder` captures live order book + trade data to Parquet

**Phase 6 Complete When:**
- `PositionExecutor` completes full lifecycle: entry fill -> barrier evaluation -> exit fill -> TERMINATED
- `DCAExecutor` completes multi-level entry + exit lifecycle
- All events emitted in correct sequence (verified against production event logs)
- Balance tracking matches expected P&L calculations

## 6. Data Formats

### Historical Data (Parquet Schema)

**Order Book Snapshots:**
```
timestamp: int64 (epoch ms)
trading_pair: string
bids: list<struct<price: float64, amount: float64>>
asks: list<struct<price: float64, amount: float64>>
```

**Trades:**
```
timestamp: int64 (epoch ms)
trading_pair: string
trade_id: string
price: float64
amount: float64
side: string ("buy" | "sell")
```

**OHLCV Candles:**
```
timestamp: int64 (epoch ms)
open: float64
high: float64
low: float64
close: float64
volume: float64
```

### Recording Format

`DataRecorder` captures live data in the same Parquet schema, enabling:
1. Record a live trading session
2. Replay it offline for strategy tuning
3. Deterministic regression testing against known market conditions

## 7. Configuration

### SimulatedExchange Configuration

```python
@dataclass
class SimulatedExchangeConfig:
    """Configuration for a SimulatedExchange instance."""

    # Identity
    name: str = "simulated_exchange"
    trading_pairs: List[str] = field(default_factory=list)

    # Trading rules per pair
    trading_rules: Dict[str, TradingRuleConfig] = field(default_factory=dict)

    # Initial balances
    initial_balances: Dict[str, Decimal] = field(default_factory=dict)

    # Matching engine type
    matching_engine: str = "limit_order"  # "immediate", "limit_order", "order_book_depth"

    # Fee model
    fee_model: str = "flat"  # "flat", "tiered", "zero"
    maker_fee: Decimal = Decimal("0.001")
    taker_fee: Decimal = Decimal("0.001")

    # Perpetual-specific
    is_perpetual: bool = False
    default_leverage: int = 1
    funding_rate: Decimal = Decimal("0")
    funding_interval_hours: int = 8
```

### SandboxEngine Configuration

```python
@dataclass
class SandboxConfig:
    """Configuration for the SandboxEngine."""

    # Time range
    start_timestamp: int  # epoch seconds
    end_timestamp: int    # epoch seconds
    tick_interval: float = 1.0  # seconds between ticks

    # Exchanges (multiple for cross-exchange strategies)
    exchanges: List[SimulatedExchangeConfig] = field(default_factory=list)

    # Data sources
    data_dir: str = ""  # directory containing Parquet files

    # Controller config
    controller_config_path: str = ""  # YAML path

    # Replay mode
    replay_speed: float = 0.0  # 0 = as fast as possible, 1.0 = real-time
```
