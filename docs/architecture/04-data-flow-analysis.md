# Data Flow Analysis: Exchange WebSocket to Strategy Execution

This document maps every data flow path from exchange WebSocket through to executor
action, with the exact types at each boundary. It serves as the specification for what
a `ReplayDataSource` must provide and what a `SimulatedConnector` must handle internally.

## 1. Inbound Data Flow: Market Data (Exchange -> Order Book -> Strategy)

### 1.1 WebSocket -> DataSource Message Queues

```
Exchange WebSocket
  |
  v
WSAssistant.iter_messages()                          # yields WSResponse(data: Dict)
  |
  v
OrderBookTrackerDataSource._process_websocket_messages(ws)
  |   for each ws_response:
  |     data: Dict[str, Any] = ws_response.data
  |     channel: str = self._channel_originating_message(data)  # subclass routes by channel
  |
  +---> self._message_queue["order_book_diff"].put_nowait(data)     # raw JSON dict
  +---> self._message_queue["trade"].put_nowait(data)               # raw JSON dict
  +---> self._message_queue["order_book_snapshot"].put_nowait(data)  # raw JSON dict
```

**Data type at this boundary:** Raw `Dict[str, Any]` (exchange-specific JSON).

### 1.2 DataSource Message Queues -> Parsed OrderBookMessage Streams

Three parallel listener tasks read from the internal queues and parse into
`OrderBookMessage` objects. These tasks are started by `OrderBookTracker.start()`.

```
_message_queue["order_book_diff"]
  |
  v
listen_for_order_book_diffs(ev_loop, output_queue)
  |   raw_message: Dict -> _parse_order_book_diff_message(raw_message, output_queue)
  |   [subclass creates OrderBookMessage(type=DIFF, content={...}, timestamp=...)]
  v
OrderBookTracker._order_book_diff_stream: asyncio.Queue[OrderBookMessage]


_message_queue["trade"]
  |
  v
listen_for_trades(ev_loop, output_queue)
  |   raw_message: Dict -> _parse_trade_message(raw_message, output_queue)
  |   [subclass creates OrderBookMessage(type=TRADE, content={...}, timestamp=...)]
  v
OrderBookTracker._order_book_trade_stream: asyncio.Queue[OrderBookMessage]


_message_queue["order_book_snapshot"]
  |
  v
listen_for_order_book_snapshots(ev_loop, output_queue)
  |   raw_message: Dict -> _parse_order_book_snapshot_message(raw_message, output_queue)
  |   [also periodically requests full snapshot via REST if no messages for 1 hour]
  v
OrderBookTracker._order_book_snapshot_stream: asyncio.Queue[OrderBookMessage]
```

**Data type at this boundary:** `OrderBookMessage` (named tuple with `type`, `content: Dict`, `timestamp: float`).

The `content` dict contains:
- For DIFF/SNAPSHOT: `update_id`, `bids`, `asks` (lists of `[price, amount, update_id]`)
- For TRADE: `trading_pair`, `trade_type`, `trade_id`, `update_id`, `price`, `amount`

### 1.3 OrderBookMessage Streams -> OrderBook State

```
_order_book_diff_stream
  |
  v
OrderBookTracker._order_book_diff_router()
  |   ob_message: OrderBookMessage
  |   trading_pair = ob_message.trading_pair
  |   [reject if order_book.snapshot_uid > ob_message.update_id]
  v
_tracking_message_queues[trading_pair]: asyncio.Queue[OrderBookMessage]
  |
  v
OrderBookTracker._track_single_book(trading_pair)
  |   message: OrderBookMessage
  |   if DIFF:  order_book.apply_diffs(message.bids, message.asks, message.update_id)
  |   if SNAPSHOT: order_book.restore_from_snapshot_and_diffs(message, past_diffs)
  v
OrderBook (Cython, C++ backed)
  |   _bid_book: set[OrderBookEntry]    # C++ ordered set, sorted by price desc
  |   _ask_book: set[OrderBookEntry]    # C++ ordered set, sorted by price asc
  |   _best_bid, _best_ask: float       # cached for fast access
  |   _last_trade_price: float
```

**Data type at this boundary:** `OrderBook.apply_diffs()` takes `bids` and `asks` as
C++ `vector[OrderBookEntry]` (price, amount, update_id triples). The Python layer
converts `OrderBookMessage.bids`/`.asks` (lists of `OrderBookRow`) to the C++ types.

### 1.4 Trade Events -> OrderBook

```
_order_book_trade_stream
  |
  v
OrderBookTracker._emit_trade_event_loop()
  |   trade_message: OrderBookMessage
  |   [reject if trading_pair not in _order_books]
  v
order_book.apply_trade(
    OrderBookTradeEvent(
        trading_pair=...,
        timestamp=trade_message.timestamp,
        price=float(trade_message.content["price"]),
        amount=float(trade_message.content["amount"]),
        trade_id=trade_message.trade_id,
        type=TradeType.BUY or TradeType.SELL
    )
)
  |
  v
OrderBook triggers PubSub event: ORDER_BOOK_TRADE_EVENT_TAG
  |
  v
[Any listeners on the OrderBook PubSub receive OrderBookTradeEvent]
```

**Data type at this boundary:** `OrderBookTradeEvent` (trading_pair, timestamp, price,
amount, trade_id, type).

### 1.5 OrderBook -> Strategy/Executor (Read Path)

Executors and controllers do not subscribe to OrderBook events directly. They poll:

```
ExecutorBase.get_price(connector_name, trading_pair, price_type)
  |
  v
self.connectors[connector_name].get_price_by_type(trading_pair, price_type)
  |   [ExchangeBase.get_price_by_type, defined in exchange_base.pyx]
  |   reads OrderBook._best_bid, _best_ask, _last_trade_price
  v
Returns Decimal (mid, bid, ask, or last trade price)


ExecutorBase.get_order_book(connector_name, trading_pair)
  |
  v
self.connectors[connector_name].get_order_book(trading_pair)
  |   [ExchangePyBase.get_order_book]
  |   reads OrderBookTracker.order_books[trading_pair]
  v
Returns OrderBook instance (full bid/ask book access)
```

**Data type at this boundary:** `Decimal` for prices, `OrderBook` for full book.

## 2. Inbound Data Flow: User Stream (Exchange -> Order Lifecycle -> Executor)

### 2.1 User WebSocket -> UserStreamTracker Queue

```
Exchange User Stream WebSocket
  |
  v
UserStreamTrackerDataSource.listen_for_user_stream(output: asyncio.Queue)
  |   [subclass connects to private WebSocket, parses messages]
  |   output.put_nowait(event_message: Dict[str, Any])
  v
UserStreamTracker._user_stream: asyncio.Queue[Dict[str, Any]]
```

**Data type at this boundary:** Raw `Dict[str, Any]` (exchange-specific JSON).

### 2.2 UserStreamTracker -> ClientOrderTracker

This is exchange-specific. Using Binance as a concrete example:

```
ExchangePyBase._iter_user_event_queue()          # yields from UserStreamTracker.user_stream
  |
  v
BinanceExchange._user_stream_event_listener()    # abstract method, each exchange implements
  |
  |   event_type = event_message.get("e")
  |
  +--- "executionReport" with execution_type == "TRADE":
  |     |
  |     v
  |     TradeUpdate(
  |         trade_id=str,
  |         client_order_id=str,
  |         exchange_order_id=str,
  |         trading_pair=str,
  |         fee=TradeFeeBase,
  |         fill_base_amount=Decimal,
  |         fill_quote_amount=Decimal,
  |         fill_price=Decimal,
  |         fill_timestamp=float,
  |     )
  |     |
  |     v
  |     self._order_tracker.process_trade_update(trade_update)
  |
  +--- "executionReport" (all execution types):
  |     |
  |     v
  |     OrderUpdate(
  |         trading_pair=str,
  |         update_timestamp=float,
  |         new_state=OrderState,       # OPEN, FILLED, CANCELED, FAILED, etc.
  |         client_order_id=str,
  |         exchange_order_id=str,
  |     )
  |     |
  |     v
  |     self._order_tracker.process_order_update(order_update)
  |
  +--- "outboundAccountPosition":
        |
        v
        self._process_balance_message(event_message)
        |   updates self._account_balances, self._account_available_balances
```

### 2.3 ClientOrderTracker -> Connector Events -> Executor

The `ClientOrderTracker` translates `TradeUpdate`/`OrderUpdate` into `MarketEvent` triggers:

```
process_trade_update(trade_update: TradeUpdate)
  |
  v
tracked_order.update_with_trade_update(trade_update)    # returns bool (new fill?)
  |   if new fill:
  v
_trigger_order_fills(tracked_order, fill_amount, fill_price, fill_fee, ...)
  |
  v
_trigger_filled_event(order, fill_amount, fill_price, fill_fee, trade_id, exchange_order_id)
  |
  v
connector.trigger_event(
    MarketEvent.OrderFilled,
    OrderFilledEvent(
        timestamp, order_id, trading_pair, trade_type, order_type,
        price, amount, trade_fee, exchange_trade_id, leverage, position
    )
)
  |
  v
[PubSub dispatches to all listeners registered for MarketEvent.OrderFilled]
  |
  v
ExecutorBase._fill_order_forwarder (SourceInfoEventForwarder)
  |
  v
ExecutorBase.process_order_filled_event(event_tag, market, event)
  |   [subclass handles: updates internal state, PnL tracking, etc.]


process_order_update(order_update: OrderUpdate)          # runs as safe_ensure_future
  |
  v
_process_order_update(order_update)
  |
  |   [if new_state == FILLED: waits up to 5s for trade fills to arrive]
  |
  v
tracked_order.update_with_order_update(order_update)     # returns bool (state changed?)
  |   if state changed from PENDING_CREATE to OPEN:
  |
  v
_trigger_order_creation(tracked_order, previous_state, new_state)
  |
  v
_trigger_created_event(order)
  |
  v
connector.trigger_event(
    MarketEvent.BuyOrderCreated / SellOrderCreated,
    BuyOrderCreatedEvent / SellOrderCreatedEvent(
        timestamp, order_type, trading_pair, amount, price,
        client_order_id, creation_timestamp, exchange_order_id,
        leverage, position
    )
)
  |
  v
ExecutorBase.process_order_created_event(event_tag, market, event)

  [similarly for CANCELED, FILLED (completed), FAILED]:

_trigger_order_completion(tracked_order, order_update)
  |
  +--- is_cancelled:
  |     connector.trigger_event(MarketEvent.OrderCancelled, OrderCancelledEvent(...))
  |     -> ExecutorBase.process_order_canceled_event(...)
  |
  +--- is_filled:
  |     connector.trigger_event(MarketEvent.BuyOrderCompleted / SellOrderCompleted, ...)
  |     -> ExecutorBase.process_order_completed_event(...)
  |
  +--- is_failure:
        connector.trigger_event(MarketEvent.OrderFailure, MarketOrderFailureEvent(...))
        -> ExecutorBase.process_order_failed_event(...)
```

## 3. Outbound Data Flow: Strategy -> Exchange (Order Placement)

### 3.1 Executor -> Strategy -> Connector

```
ExecutorBase.place_order(connector_name, trading_pair, order_type, side, amount, ...)
  |
  |   if side == TradeType.BUY:
  v
self._strategy.buy(connector_name, trading_pair, amount, order_type, price, position_action)
  |
  v
StrategyV2Base.buy(connector_name, trading_pair, amount, order_type, price, position_action)
  |   market_pair = self._market_trading_pair_tuple(connector_name, trading_pair)
  v
StrategyBase.buy_with_specific_market(market_pair, amount, order_type, price, ...)
  |
  v
StrategyBase.c_buy_with_specific_market(market_pair, ...)    # Cython
  |   [validates: not delegate-locked, amount/price are Decimal]
  |   market: ConnectorBase = market_trading_pair_tuple.market
  v
market.c_buy(trading_pair, amount, order_type, price, kwargs)  # ConnectorBase Cython
  |
  v
ExchangePyBase.buy(trading_pair, amount, order_type, price, **kwargs)
  |   order_id = get_new_client_order_id(is_buy=True, ...)
  |   safe_ensure_future(self._create_order(...))              # fire-and-forget
  |   return order_id                                          # returns immediately
```

### 3.2 _create_order: Validation, Tracking, and REST API Call

```
ExchangePyBase._create_order(trade_type, order_id, trading_pair, amount, order_type, price)
  |
  +--- Quantize price and amount per trading_rules
  |
  +--- start_tracking_order(order_id, None, trading_pair, order_type, trade_type, price, amount)
  |     |
  |     v
  |     _order_tracker.start_tracking_order(InFlightOrder(..., state=PENDING_CREATE))
  |
  +--- Validate: order_type supported, amount >= min_order_size, notional >= min_notional
  |     [on failure: _update_order_after_failure -> OrderUpdate(FAILED) -> trigger_event(OrderFailure)]
  |
  v
_place_order_and_process_update(order)
  |
  v
exchange_order_id, timestamp = await self._place_order(...)    # REST API call (abstract)
  |
  v
_order_tracker.process_order_update(
    OrderUpdate(client_order_id, exchange_order_id, trading_pair, timestamp, new_state=OPEN)
)
  |   [triggers _trigger_order_creation -> BuyOrderCreated/SellOrderCreated event]
  v
Returns exchange_order_id
```

### 3.3 Cancel Flow

```
ExecutorBase -> self._strategy.cancel(connector_name, trading_pair, order_id)
  |
  v
StrategyV2Base.cancel -> StrategyBase.cancel_order -> market.cancel(trading_pair, order_id)
  |
  v
ExchangePyBase.cancel(trading_pair, client_order_id)
  |   safe_ensure_future(self._execute_cancel(trading_pair, client_order_id))
  v
_execute_cancel -> _execute_order_cancel(order) -> _execute_order_cancel_and_process_update
  |
  v
await self._place_cancel(client_order_id, order)    # REST API call (abstract)
  |
  v
_order_tracker.process_order_update(
    OrderUpdate(..., new_state=CANCELED or PENDING_CANCEL)
)
  |   [triggers _trigger_order_completion -> OrderCancelled event]
```

## 4. Data Types at Each Boundary

| Boundary | Direction | Data Type |
|----------|-----------|-----------|
| Exchange WebSocket -> WSAssistant | inbound | `WSResponse(data: Dict[str, Any])` |
| WSAssistant -> DataSource queues | inbound | Raw `Dict[str, Any]` (exchange JSON) |
| DataSource queues -> Tracker streams | inbound | `OrderBookMessage(type, content: Dict, timestamp)` |
| Tracker diff router -> per-pair queue | inbound | `OrderBookMessage` (filtered by update_id) |
| Per-pair queue -> OrderBook | inbound | `bids: List[OrderBookRow]`, `asks: List[OrderBookRow]`, `update_id: int` |
| OrderBook -> Connector/Executor (read) | outbound | `Decimal` (prices), `OrderBook` (full book) |
| Trade stream -> OrderBook | inbound | `OrderBookTradeEvent(trading_pair, timestamp, price, amount, trade_id, type)` |
| User WebSocket -> UserStreamTracker | inbound | Raw `Dict[str, Any]` (exchange JSON) |
| UserStreamTracker -> connector listener | inbound | Raw `Dict[str, Any]` (exchange-specific) |
| Connector listener -> ClientOrderTracker | inbound | `TradeUpdate` or `OrderUpdate` |
| ClientOrderTracker -> Connector PubSub | outbound | `OrderFilledEvent`, `BuyOrderCreatedEvent`, `OrderCancelledEvent`, etc. |
| Connector PubSub -> ExecutorBase | outbound | Same event types via `SourceInfoEventForwarder` |
| Executor -> Strategy -> Connector | outbound | `buy(pair, amount, type, price)` -> `str` (client_order_id) |
| Connector -> Exchange REST | outbound | Abstract `_place_order(...)` -> `(exchange_order_id, timestamp)` |

## 5. What a ReplayDataSource Must Provide

A `ReplayDataSource` replaces `OrderBookTrackerDataSource` and feeds recorded market
data through the **standard pipeline** (OrderBookTracker -> OrderBook). It does NOT
need to replace the tracker itself.

### 5.1 Required Interface

```python
class ReplayDataSource(OrderBookTrackerDataSource):
    """Feeds recorded market data through the standard order book pipeline.

    Replaces the WebSocket connection with a file/database reader that emits
    OrderBookMessage objects into the standard queues.
    """

    def __init__(self, trading_pairs: List[str], data_provider: "HistoricalDataProvider"):
        super().__init__(trading_pairs)
        self._data_provider = data_provider

    # --- MUST IMPLEMENT: Data feeding ---

    async def listen_for_subscriptions(self):
        """Instead of connecting to WebSocket, reads from historical data source
        and feeds messages into _message_queue at appropriate timestamps.

        Controls replay speed (real-time, accelerated, or as-fast-as-possible).
        """
        ...

    async def _order_book_snapshot(self, trading_pair: str) -> OrderBookMessage:
        """Returns the initial order book snapshot from recorded data.

        This is called by OrderBookTracker._init_order_books() to create the
        initial OrderBook state before diffs start flowing.
        """
        ...

    async def get_last_traded_prices(
        self, trading_pairs: List[str], domain: Optional[str] = None
    ) -> Dict[str, float]:
        """Returns last traded prices from recorded data.

        Called periodically by OrderBookTracker._update_last_trade_prices_loop()
        as a fallback when no trade events arrive for 3+ minutes.
        """
        ...

    # --- MUST IMPLEMENT: Parse methods ---
    # These convert raw recorded data into OrderBookMessage objects.
    # For replay, the "raw_message" is already a pre-parsed dict from storage.

    async def _parse_order_book_diff_message(
        self, raw_message: Dict, message_queue: asyncio.Queue
    ):
        """Convert stored diff record to OrderBookMessage(type=DIFF)."""
        ...

    async def _parse_trade_message(
        self, raw_message: Dict, message_queue: asyncio.Queue
    ):
        """Convert stored trade record to OrderBookMessage(type=TRADE)."""
        ...

    async def _parse_order_book_snapshot_message(
        self, raw_message: Dict, message_queue: asyncio.Queue
    ):
        """Convert stored snapshot record to OrderBookMessage(type=SNAPSHOT)."""
        ...

    # --- MUST IMPLEMENT: Channel routing ---

    def _channel_originating_message(self, event_message: Dict) -> str:
        """Identify message type from stored data format.
        Returns one of: 'order_book_diff', 'trade', 'order_book_snapshot'.
        """
        ...

    # --- NOT NEEDED: WebSocket methods (no-op or raise) ---

    async def _connected_websocket_assistant(self) -> WSAssistant:
        """Not used in replay mode. listen_for_subscriptions is overridden."""
        raise NotImplementedError("ReplayDataSource does not use WebSocket")

    async def _subscribe_channels(self, ws: WSAssistant):
        """Not used in replay mode."""
        pass

    async def subscribe_to_trading_pair(self, trading_pair: str) -> bool:
        """Replay supports all pairs in the dataset."""
        self.add_trading_pair(trading_pair)
        return True

    async def unsubscribe_from_trading_pair(self, trading_pair: str) -> bool:
        """Remove pair from replay."""
        self.remove_trading_pair(trading_pair)
        return True
```

### 5.2 Data the ReplayDataSource Must Feed

| Queue Key | Required Data | Format |
|-----------|---------------|--------|
| `order_book_snapshot` | Initial L2 snapshot per pair | `OrderBookMessage(SNAPSHOT, {"update_id": int, "bids": [...], "asks": [...]}, ts)` |
| `order_book_diff` | Incremental L2 updates | `OrderBookMessage(DIFF, {"update_id": int, "bids": [...], "asks": [...]}, ts)` |
| `trade` | Public trades (for limit matching) | `OrderBookMessage(TRADE, {"trading_pair": str, "trade_type": float, "trade_id": int, "price": str, "amount": str}, ts)` |

### 5.3 What ReplayDataSource Does NOT Provide

- **User stream data** -- there is no user stream in simulation. Order lifecycle events
  (created, filled, completed, canceled) are generated internally by `SimulatedConnector`
  via its matching engine and `ClientOrderTracker`.

- **REST endpoints** -- trading rules, fee schedules, and symbol info are configured at
  initialization time. No REST calls are needed.

- **Balance updates** -- balances are managed internally by `SimulatedConnector`.

## 6. What a SimulatedConnector Must Handle Internally

The SimulatedConnector replaces the **user stream side** of the data flow. Instead of
receiving order lifecycle events from the exchange WebSocket, it generates them internally
from its matching engine.

### 6.1 Order Placement (Replaces REST API + User Stream)

```
executor.place_order(connector_name, trading_pair, ...)
  |
  v
strategy.buy/sell(connector_name, ...)
  |   [standard routing through StrategyBase Cython layer]
  v
SimulatedConnector.buy/sell(trading_pair, amount, order_type, price)
  |
  +--- Generate client_order_id
  +--- Quantize price/amount per trading rules
  +--- Validate: supported order type, min size, min notional
  |
  v
_order_tracker.start_tracking_order(InFlightOrder(..., state=PENDING_CREATE))
  |
  v
Synchronously (no REST call):
  generate exchange_order_id (e.g., incrementing counter)
  |
  v
_order_tracker.process_order_update(
    OrderUpdate(..., new_state=OPEN)
)
  |   -> _trigger_created_event -> BuyOrderCreated/SellOrderCreated
  |   -> ExecutorBase.process_order_created_event(...)
  |
  v
[Order enters the SimulatedConnector's matching engine queue]
```

### 6.2 Order Matching (Replaces Exchange Matching Engine)

```
SimulatedConnector._check_and_match_orders()
  |   [called on each tick or when order book updates arrive]
  |
  |   For each tracked OPEN order:
  |
  +--- LIMIT BUY: if best_ask <= order.price (or trade at/below order.price):
  |     |
  |     v
  |     _order_tracker.process_trade_update(
  |         TradeUpdate(
  |             trade_id=generated_id,
  |             client_order_id=order.client_order_id,
  |             fill_base_amount=fill_amount,
  |             fill_price=execution_price,
  |             fee=calculated_fee,
  |             ...
  |         )
  |     )
  |     |   -> _trigger_filled_event -> OrderFilled
  |     |   -> ExecutorBase.process_order_filled_event(...)
  |     |
  |     v
  |     if fully filled:
  |       _order_tracker.process_order_update(OrderUpdate(..., new_state=FILLED))
  |       |   -> _trigger_completed_event -> BuyOrderCompleted
  |       |   -> ExecutorBase.process_order_completed_event(...)
  |
  +--- LIMIT SELL: if best_bid >= order.price (or trade at/above order.price):
  |     [same as above, mirrored]
  |
  +--- MARKET order: execute immediately at current best price
  |     [TradeUpdate + OrderUpdate(FILLED) in sequence]
  |
  +--- LIMIT_MAKER: if would immediately match, reject (OrderUpdate(FAILED))
```

### 6.3 Order Cancellation (Replaces REST API)

```
SimulatedConnector.cancel(trading_pair, client_order_id)
  |
  v
_order_tracker.process_order_update(
    OrderUpdate(..., new_state=CANCELED)
)
  |   -> _trigger_cancelled_event -> OrderCancelled
  |   -> ExecutorBase.process_order_canceled_event(...)
```

### 6.4 Balance Management (Replaces Exchange Polling)

```
SimulatedConnector maintains internal balance state:
  _account_balances: Dict[str, Decimal]            # total balance per asset
  _account_available_balances: Dict[str, Decimal]   # available (unlocked)

On order placement:
  Lock collateral: available_balance[collateral_asset] -= required_amount

On fill:
  Debit collateral, credit received asset (minus fees)
  Update both total and available balances

On cancel:
  Release locked collateral: available_balance[collateral_asset] += locked_amount
```

### 6.5 Event Sequence Contract

The SimulatedConnector MUST emit events in the same order as real exchanges.
The `ClientOrderTracker` handles this automatically when driven by `OrderUpdate`
and `TradeUpdate` objects. The key sequences are:

**Successful limit order (full fill):**
1. `BuyOrderCreated` / `SellOrderCreated` (on `process_order_update(OPEN)`)
2. `OrderFilled` (on `process_trade_update(...)`) -- may repeat for partial fills
3. `BuyOrderCompleted` / `SellOrderCompleted` (on `process_order_update(FILLED)`)

**Successful market order:**
1. `BuyOrderCreated` / `SellOrderCreated` (on `process_order_update(OPEN)`)
2. `OrderFilled` (immediate fill)
3. `BuyOrderCompleted` / `SellOrderCompleted` (immediate)

**Canceled order:**
1. `BuyOrderCreated` / `SellOrderCreated`
2. `OrderCancelled` (on `process_order_update(CANCELED)`)

**Failed order:**
1. `OrderFailure` (on `process_order_update(FAILED)`)

**Critical:** The `ClientOrderTracker._process_order_update` method waits up to 5 seconds
for trade fills before processing a `FILLED` state update. In simulation, trade updates
should be processed **before** the final state update to avoid this timeout.

## 7. Complete Pipeline Diagram

```
                    MARKET DATA SIDE                    USER/ORDER SIDE
                    (ReplayDataSource)                  (SimulatedConnector)

Historical Data                                     Executor.place_order()
      |                                                     |
      v                                                     v
ReplayDataSource                                   SimulatedConnector.buy/sell()
      |                                                     |
      +---> _message_queue["diff"]                          v
      +---> _message_queue["trade"]                ClientOrderTracker
      +---> _message_queue["snapshot"]              .start_tracking_order()
      |                                             .process_order_update(OPEN)
      v                                                     |
listen_for_order_book_diffs()                              v
listen_for_trades()                               BuyOrderCreated event
listen_for_order_book_snapshots()                          |
      |                                                     v
      v                                            [Matching Engine checks
OrderBookTracker                                    order book on each tick]
  ._order_book_diff_router()                                |
  ._emit_trade_event_loop()                                v
  ._order_book_snapshot_router()                   .process_trade_update()
      |                                            .process_order_update(FILLED)
      v                                                     |
  ._track_single_book(pair)                                v
      |                                            OrderFilled event
      v                                            BuyOrderCompleted event
  OrderBook.apply_diffs()                                   |
  OrderBook.apply_trade()                                   v
      |                                            ExecutorBase callbacks:
      v                                              .process_order_filled_event()
  OrderBook state                                    .process_order_completed_event()
  (available for reads)
      |
      v
  ExecutorBase.get_price()
  ExecutorBase.get_order_book()
```

## 8. Timing and Synchronization Considerations

### 8.1 Real-Time vs Simulated Time

In production, all components run on real asyncio event loop time. In simulation:

- **OrderBookTracker** uses `time.time()` and `time.perf_counter()` for metrics and
  staleness checks. These need to be patched or the staleness logic disabled.
- **ClientOrderTracker** uses `connector.current_timestamp` (from NetworkIterator/Clock).
  The SimulatedConnector must provide a `current_timestamp` property driven by simulated time.
- **OrderBookTracker._update_last_trade_prices_loop()** polls every 3 minutes for stale
  prices. In replay mode this should be disabled (ReplayDataSource provides all trades).

### 8.2 Message Ordering

The replay must maintain temporal ordering across diff, trade, and snapshot streams.
In production these arrive on the same WebSocket and are naturally ordered by
`_process_websocket_messages`. The ReplayDataSource's `listen_for_subscriptions`
override must preserve this ordering.

### 8.3 Queue Backpressure

In production, asyncio queues are unbounded. In fast replay (as-fast-as-possible mode),
the replay can outpace the matching engine. The ReplayDataSource should optionally
yield control (`await asyncio.sleep(0)`) between messages to allow the matching
engine to process pending orders against updated book state.
