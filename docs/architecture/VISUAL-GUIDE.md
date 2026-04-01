# Hummingbot Architecture — Visual Guide

> One-page reference for reviewing the market-simulator plan and hummingbot re-architecture.

---

## 1. The Big Picture: What We're Building

```mermaid
graph LR
    subgraph "TODAY"
        direction TB
        A1["🔴 Strategy can only run LIVE"]
        A2["🟡 Backtesting skips executor code"]
        A3["🔴 No offline testing possible"]
        A4["🟡 Paper trade needs live WebSocket"]
    end

    subgraph "GOAL"
        direction TB
        B1["🟢 Strategy runs in ANY mode"]
        B2["🟢 Real executors in simulation"]
        B3["🟢 Fully offline testing"]
        B4["🟢 Record & replay market data"]
    end

    TODAY -->|"hb-market-simulator"| GOAL

    style TODAY fill:#2d2d2d,color:#fff
    style GOAL fill:#1a472a,color:#fff
```

---

## 2. Current Architecture — Component Ownership

```mermaid
graph TD
    Clock["⏰ Clock<br/><i>tick driver</i>"]
    Strategy["📋 StrategyV2Base<br/><i>orchestration hub</i>"]
    MDP["📊 MarketDataProvider<br/><i>prices, candles, rates</i>"]
    Controllers["🎯 Controllers × N<br/><i>signal generation</i>"]
    Orchestrator["🔧 ExecutorOrchestrator<br/><i>lifecycle management</i>"]
    Executors["⚡ Executors × M<br/><i>order management</i>"]
    Queue["📬 actions_queue<br/><i>async channel</i>"]

    Connectors["🔌 ConnectorBase × N<br/><i>exchange interface</i>"]
    Candles["📈 CandlesFeeds × N<br/><i>OHLCV data</i>"]
    Oracle["💱 RateOracle<br/><i>price conversion</i>"]
    DB["💾 MarketsRecorder<br/><i>persistence</i>"]

    Clock --> Strategy
    Strategy --> MDP
    Strategy --> Controllers
    Strategy --> Orchestrator
    Strategy --> Queue

    MDP --> Connectors
    MDP --> Candles
    MDP --> Oracle

    Controllers -->|"reads"| MDP
    Controllers -->|"writes"| Queue

    Orchestrator --> Executors
    Orchestrator --> DB

    Executors -->|"events from"| Connectors
    Executors -->|"orders via"| Strategy

    style Connectors fill:#e74c3c,color:#fff,stroke:#c0392b
    style Candles fill:#f39c12,color:#fff,stroke:#e67e22
    style Oracle fill:#3498db,color:#fff,stroke:#2980b9
    style DB fill:#3498db,color:#fff,stroke:#2980b9

    style Strategy fill:#27ae60,color:#fff,stroke:#1e8449
    style Controllers fill:#27ae60,color:#fff,stroke:#1e8449
    style Orchestrator fill:#27ae60,color:#fff,stroke:#1e8449
    style Executors fill:#27ae60,color:#fff,stroke:#1e8449
    style MDP fill:#27ae60,color:#fff,stroke:#1e8449
    style Queue fill:#27ae60,color:#fff,stroke:#1e8449
    style Clock fill:#9b59b6,color:#fff,stroke:#8e44ad
```

**Legend:**
- 🔴 **Red** = Live exchange dependency (must be simulated)
- 🟡 **Yellow** = Data feed dependency (can be replayed)
- 🔵 **Blue** = Utility/persistence (can be stubbed)
- 🟢 **Green** = Pure Python (runs unmodified in simulation)
- 🟣 **Purple** = Time driver (BacktestClock exists)

---

## 3. The Six Simulation Seams

```mermaid
graph TB
    subgraph "PURE PYTHON - Runs Unmodified"
        Strategy["StrategyV2Base"]
        Ctrl["Controllers"]
        Orch["Orchestrator"]
        Exec["Executors"]
    end

    subgraph "SEAM 1: ConnectorBase"
        direction LR
        S1_Events["📢 Events"]
        S1_Orders["📝 Orders"]
        S1_Prices["💰 Prices"]
        S1_Balance["💳 Balances"]
        S1_Rules["📏 Trading Rules"]
    end

    subgraph "SEAM 2: CandlesFactory"
        S2["📈 Historical Data"]
    end

    subgraph "SEAM 3: Time"
        S3["⏰ MDP.time()"]
    end

    subgraph "SEAM 4: Persistence"
        S4["💾 MarketsRecorder"]
    end

    subgraph "SEAM 5: RateOracle"
        S5["💱 Conversion Rates"]
    end

    subgraph "SEAM 6: Clock"
        S6["🔄 BacktestClock"]
    end

    Exec --> S1_Events
    Exec --> S1_Orders
    Strategy --> S1_Prices
    Exec --> S1_Balance
    Strategy --> S1_Rules
    Ctrl --> S2
    Ctrl --> S3
    Orch --> S4
    Strategy --> S5
    S6 --> Strategy

    style S1_Events fill:#e74c3c,color:#fff
    style S1_Orders fill:#e74c3c,color:#fff
    style S1_Prices fill:#e74c3c,color:#fff
    style S1_Balance fill:#e74c3c,color:#fff
    style S1_Rules fill:#e74c3c,color:#fff
    style S2 fill:#f39c12,color:#fff
    style S3 fill:#2ecc71,color:#fff
    style S4 fill:#3498db,color:#fff
    style S5 fill:#3498db,color:#fff
    style S6 fill:#9b59b6,color:#fff
```

| Seam | Component | Simulation Strategy | Complexity |
|------|-----------|-------------------|------------|
| ① | ConnectorBase | SimulatedConnector with matching engine | 🔴 High |
| ② | CandlesFactory | Pre-loaded DataFrame injection | 🟢 Low |
| ③ | MDP.time() | Return simulated clock time | 🟢 Low |
| ④ | MarketsRecorder | In-memory or no-op | 🟢 Low |
| ⑤ | RateOracle | Static rate dict | 🟢 Low |
| ⑥ | Clock | BacktestClock (already exists!) | 🟢 Done |

---

## 4. ConnectorBase Protocol Decomposition

```mermaid
classDiagram
    class MarketDataProtocol {
        <<protocol>>
        +get_price_by_type(pair, type) Decimal
        +get_order_book(pair) OrderBook
        +trading_rules Dict
        +quantize_order_price(pair, price) Decimal
        +quantize_order_amount(pair, amount) Decimal
    }

    class BalanceProtocol {
        <<protocol>>
        +get_balance(currency) Decimal
        +get_available_balance(currency) Decimal
        +budget_checker BudgetChecker
    }

    class OrderExecutionProtocol {
        <<protocol>>
        +buy(pair, amount, type, price) str
        +sell(pair, amount, type, price) str
        +cancel(pair, order_id)
        +in_flight_orders Dict
        +get_in_flight_order(order_id) InFlightOrder
    }

    class EventSourceProtocol {
        <<protocol>>
        +add_listener(tag, listener)
        +remove_listener(tag, listener)
        +trigger_event(tag, message)
    }

    class ReadinessProtocol {
        <<protocol>>
        +ready bool
        +name str
        +trading_pairs List
    }

    class PerpetualProtocol {
        <<protocol>>
        +get_funding_info(pair) FundingInfo
        +set_leverage(pair, leverage)
        +account_positions Dict
    }

    class ConnectorBase {
        <<existing>>
        implements ALL protocols
    }

    class SimulatedConnector {
        <<new>>
        implements ALL protocols
        +matching_engine MatchingEngine
        +balance_manager BalanceManager
    }

    MarketDataProtocol <|.. ConnectorBase
    BalanceProtocol <|.. ConnectorBase
    OrderExecutionProtocol <|.. ConnectorBase
    EventSourceProtocol <|.. ConnectorBase
    ReadinessProtocol <|.. ConnectorBase
    PerpetualProtocol <|.. ConnectorBase

    MarketDataProtocol <|.. SimulatedConnector
    BalanceProtocol <|.. SimulatedConnector
    OrderExecutionProtocol <|.. SimulatedConnector
    EventSourceProtocol <|.. SimulatedConnector
    ReadinessProtocol <|.. SimulatedConnector
    PerpetualProtocol <|.. SimulatedConnector
```

### Who Uses What?

```mermaid
graph LR
    subgraph "Consumers"
        MDP["📊 MarketDataProvider"]
        ExecB["⚡ ExecutorBase"]
        CtrlB["🎯 ControllerBase"]
        Orch["🔧 Orchestrator"]
        Strat["📋 StrategyV2Base"]
    end

    subgraph "Protocols"
        P1["MarketData"]
        P2["Balance"]
        P3["OrderExecution"]
        P4["EventSource"]
        P5["Readiness"]
        P6["Perpetual"]
    end

    MDP --> P1
    MDP --> P5

    ExecB --> P1
    ExecB --> P2
    ExecB --> P3
    ExecB --> P4

    CtrlB -.->|"via MDP"| P1

    Orch --> P3

    Strat --> P3
    Strat --> P5

    style P1 fill:#e74c3c,color:#fff
    style P2 fill:#e67e22,color:#fff
    style P3 fill:#f39c12,color:#fff
    style P4 fill:#27ae60,color:#fff
    style P5 fill:#3498db,color:#fff
    style P6 fill:#9b59b6,color:#fff
```

**Key Insight:** No single consumer needs ALL 6 protocols. ExecutorBase needs 4, MarketDataProvider needs 2, ControllerBase needs 0 directly (goes through MDP).

---

## 5. Data Flow: Exchange → Strategy

```mermaid
sequenceDiagram
    participant WS as 🌐 Exchange WS
    participant DS as 📡 DataSource
    participant OBT as 📚 OrderBookTracker
    participant OB as 📖 OrderBook
    participant Conn as 🔌 ConnectorBase
    participant Exec as ⚡ Executor

    Note over WS,Exec: Inbound Market Data Flow
    WS->>DS: raw JSON message
    DS->>OBT: OrderBookMessage(diff)
    OBT->>OB: apply_diffs(bids, asks)

    Note over WS,Exec: Inbound User Stream Flow
    WS->>Conn: order update JSON
    Conn->>Conn: process_trade_update()
    Conn->>Exec: 📢 OrderFilled event

    Note over WS,Exec: Outbound Order Flow
    Exec->>Conn: strategy.buy(pair, amount, price)
    Conn->>WS: REST API call
    Conn->>Exec: 📢 OrderCreated event
```

### What SimulatedConnector Replaces

```mermaid
sequenceDiagram
    participant Exec as ⚡ Executor
    participant SC as 🎮 SimulatedConnector
    participant ME as ⚙️ MatchingEngine
    participant BM as 💳 BalanceManager

    Exec->>SC: buy(pair, amount, price)
    SC->>BM: lock_collateral(amount × price)
    SC->>SC: create InFlightOrder
    SC->>Exec: 📢 OrderCreated event

    Note over ME: On each simulated tick...
    ME->>ME: check order vs order book
    alt Price crosses limit
        ME->>BM: execute_fill(amount, price, fee)
        ME->>Exec: 📢 OrderFilled event
        ME->>Exec: 📢 OrderCompleted event
    end
```

---

## 6. Executor Lifecycle State Machine

```mermaid
stateDiagram-v2
    [*] --> NOT_STARTED: create()

    NOT_STARTED --> RUNNING: start()
    NOT_STARTED --> EXPIRED: time_limit before entry

    RUNNING --> SHUTTING_DOWN: stop_loss 🛑
    RUNNING --> SHUTTING_DOWN: take_profit 🎯
    RUNNING --> SHUTTING_DOWN: trailing_stop 📉
    RUNNING --> SHUTTING_DOWN: time_limit ⏰
    RUNNING --> SHUTTING_DOWN: early_stop 🚫
    RUNNING --> FAILED: max_retries ❌

    SHUTTING_DOWN --> TERMINATED: close order filled ✅
    SHUTTING_DOWN --> FAILED: max_retries ❌

    TERMINATED --> [*]
    FAILED --> [*]
    EXPIRED --> [*]

    note right of RUNNING
        🔄 control_task() loop
        📝 Places orders
        👂 Listens to events
        📊 Controls barriers
    end note
```

---

## 7. Current vs Proposed Backtesting

```mermaid
graph TD
    subgraph "❌ Current Backtesting"
        direction TB
        CE1["BacktestingEngine"]
        CE2["BacktestingDataProvider"]
        CE3["PositionExecutorSimulator<br/><i>vectorized P&L only</i>"]
        CE4["🔴 Live REST calls<br/><i>candles + trading rules</i>"]

        CE1 --> CE2
        CE1 --> CE3
        CE2 --> CE4

        style CE3 fill:#e74c3c,color:#fff
        style CE4 fill:#e74c3c,color:#fff
    end

    subgraph "✅ Proposed Sandbox"
        direction TB
        PE1["SandboxEngine"]
        PE2["SimulatedMDP"]
        PE3["Real Executors<br/><i>full async control loop</i>"]
        PE4["SimulatedConnector<br/><i>matching engine</i>"]
        PE5["🟢 Local Data<br/><i>pre-downloaded</i>"]

        PE1 --> PE2
        PE1 --> PE3
        PE3 --> PE4
        PE2 --> PE5

        style PE3 fill:#27ae60,color:#fff
        style PE4 fill:#27ae60,color:#fff
        style PE5 fill:#27ae60,color:#fff
    end
```

### Executor Coverage Comparison

| Executor | Current | Proposed | Gap |
|----------|:-------:|:--------:|:---:|
| PositionExecutor | 🟡 Vectorized | ✅ Full | P&L only → real state machine |
| DCAExecutor | 🟡 Maker only | ✅ Full | No taker support |
| GridExecutor | ❌ None | ✅ Full | Completely unsupported |
| TWAPExecutor | ❌ None | ✅ Full | Completely unsupported |
| XEMMExecutor | ❌ None | ✅ Full | Needs 2 connectors |
| ArbitrageExecutor | ❌ None | ✅ Full | Needs 2 connectors |
| ProgressiveExecutor | ❌ None | ✅ Full | Trailing stop logic untested |
| OrderExecutor | ❌ None | ✅ Full | Simple but unsupported |
| LPExecutor | ❌ None | ✅ Full | AMM-specific |

---

## 8. hb-market-simulator Package Architecture

```mermaid
graph TD
    subgraph "market_simulator"
        subgraph "📐 protocols/"
            P1["MarketDataProtocol"]
            P2["BalanceProtocol"]
            P3["OrderExecutionProtocol"]
            P4["EventSourceProtocol"]
            P5["ReadinessProtocol"]
            P6["PerpetualProtocol"]
        end

        subgraph "🎮 simulator/"
            S1["SimulatedExchange"]
            S2["SimulatedOrderBook"]
            S3["BalanceManager"]
            S4["MatchingEngine"]
            S5["FeeModel"]
        end

        subgraph "⏪ replay/"
            R1["ReplayDataSource"]
            R2["DataRecorder"]
            R3["HistoricalDataLoader"]
        end

        subgraph "🔌 hb_compat/"
            H1["SimulatedConnector"]
            H2["SimulatedMDP"]
            H3["SandboxEngine"]
        end
    end

    S1 --> S2
    S1 --> S3
    S1 --> S4
    S4 --> S5
    S4 --> S2

    H1 --> S1
    H2 --> H1
    H3 --> H2
    H3 --> H1

    R1 --> S2
    R2 -.->|"records"| R3

    S1 -.-> P3
    S1 -.-> P4
    S2 -.-> P1
    S3 -.-> P2

    style P1 fill:#9b59b6,color:#fff
    style P2 fill:#9b59b6,color:#fff
    style P3 fill:#9b59b6,color:#fff
    style P4 fill:#9b59b6,color:#fff
    style P5 fill:#9b59b6,color:#fff
    style P6 fill:#9b59b6,color:#fff

    style S1 fill:#e74c3c,color:#fff
    style S2 fill:#e74c3c,color:#fff
    style S3 fill:#e74c3c,color:#fff
    style S4 fill:#e74c3c,color:#fff
    style S5 fill:#e74c3c,color:#fff

    style R1 fill:#f39c12,color:#fff
    style R2 fill:#f39c12,color:#fff
    style R3 fill:#f39c12,color:#fff

    style H1 fill:#27ae60,color:#fff
    style H2 fill:#27ae60,color:#fff
    style H3 fill:#27ae60,color:#fff
```

---

## 9. Implementation Phases

```mermaid
gantt
    title hb-market-simulator Implementation Roadmap
    dateFormat YYYY-MM
    axisFormat %b %Y

    section Phase 1 — Protocols
    Define 6 Protocol interfaces       :p1, 2026-04, 1w
    Type stubs for hummingbot types     :p1b, after p1, 1w

    section Phase 2 — Core Simulation
    SimulatedOrderBook                  :p2a, after p1b, 2w
    BalanceManager (from dev/sandboxing):p2b, after p1b, 2w
    FeeModel                           :p2c, after p2a, 1w

    section Phase 3 — Matching Engine
    MatchingEngine (limit + market)     :p3, after p2a, 2w
    SimulatedExchange (combines all)    :p3b, after p3, 1w

    section Phase 4 — hb_compat
    SimulatedConnector                  :p4a, after p3b, 2w
    SimulatedMarketDataProvider         :p4b, after p4a, 1w

    section Phase 5 — Replay
    DataRecorder                        :p5a, after p4b, 1w
    ReplayDataSource                    :p5b, after p5a, 1w
    HistoricalDataLoader                :p5c, after p5b, 1w

    section Phase 6 — Integration
    SandboxEngine                       :p6a, after p5c, 2w
    Integration tests with executors    :p6b, after p6a, 2w
    Documentation                       :p6c, after p6b, 1w
```

---

## 10. Sub-Package Extraction Roadmap

```mermaid
graph TD
    subgraph "✅ COMPLETED"
        SP1["📈 hb-candles-feed<br/><i>OHLCV data feeds</i>"]
        SP2["💥 hb-liquidations-feed<br/><i>Exchange liquidations</i>"]
        SP3["💱 hb-rate-oracle<br/><i>Price conversion (Phase 1)</i>"]
    end

    subgraph "🔨 IN PROGRESS"
        SP4["🎮 hb-market-simulator<br/><i>Sandboxing framework</i>"]
    end

    subgraph "📋 PROPOSED"
        SP5["🔌 hb-connector-protocols<br/><i>ConnectorBase decomposition</i>"]
        SP6["🌐 hb-ws-data-feed<br/><i>WebSocket market data</i>"]
        SP7["📝 hb-order-management<br/><i>Order lifecycle</i>"]
        SP8["📢 hb-event-system<br/><i>PubSub, events</i>"]
        SP9["📏 hb-trading-rules<br/><i>Exchange rules, quantization</i>"]
    end

    SP4 -->|"defines"| SP5
    SP5 -->|"enables"| SP6
    SP5 -->|"enables"| SP7
    SP7 -->|"uses"| SP8
    SP6 -->|"uses"| SP8

    SP1 -.->|"pattern for"| SP6
    SP2 -.->|"pattern for"| SP6
    SP3 -.->|"Phase 2 needs"| SP5

    style SP1 fill:#27ae60,color:#fff
    style SP2 fill:#27ae60,color:#fff
    style SP3 fill:#27ae60,color:#fff
    style SP4 fill:#f39c12,color:#fff
    style SP5 fill:#3498db,color:#fff
    style SP6 fill:#3498db,color:#fff
    style SP7 fill:#3498db,color:#fff
    style SP8 fill:#3498db,color:#fff
    style SP9 fill:#3498db,color:#fff
```

---

## 11. The Refactoring Journey

```mermaid
graph LR
    subgraph "Phase 1: Protocols"
        A1["Define Protocol interfaces<br/>alongside ConnectorBase"]
    end

    subgraph "Phase 2: Consumers"
        A2["Refactor consumers to<br/>accept Protocol types"]
    end

    subgraph "Phase 3: Simulation"
        A3["Create SimulatedConnector<br/>implementing protocols"]
    end

    subgraph "Phase 4: Extraction"
        A4["Extract protocols into<br/>hb-connector-protocols"]
    end

    A1 -->|"non-breaking"| A2
    A2 -->|"gradual"| A3
    A3 -->|"validated"| A4

    style A1 fill:#27ae60,color:#fff
    style A2 fill:#f39c12,color:#fff
    style A3 fill:#e74c3c,color:#fff
    style A4 fill:#3498db,color:#fff
```

| Phase | Risk | Effort | Breaking Changes |
|-------|------|--------|-----------------|
| 1. Define Protocols | 🟢 None | 1 week | None — additive only |
| 2. Refactor Consumers | 🟡 Low | 2-3 weeks | Type hints only |
| 3. SimulatedConnector | 🟡 Medium | 3-4 weeks | None — new code |
| 4. Extract Package | 🟢 Low | 1 week | Import paths |

---

*This visual guide is auto-generated from the architecture analysis. See individual documents in `docs/architecture/` and `docs/proposals/` for full details.*
