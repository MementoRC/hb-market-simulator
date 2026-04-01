# Connector Protocol Definitions

This document defines the actual Python Protocol classes that decompose `ConnectorBase`
into composable interfaces. These protocols are implementable by both the existing
`ConnectorBase` (via structural subtyping) and a new `SimulatedConnector` class.

This builds on the analysis in `docs/architecture/02-connector-interface-analysis.md`
and refines it into production-ready protocol definitions with full type hints,
consumer mappings, simulated implementation sketches, and migration paths.

## 1. Protocol Definitions

### 1.1 MarketDataProtocol

```python
from decimal import Decimal
from typing import Dict, List, Protocol, runtime_checkable

from hummingbot.connector.trading_rule import TradingRule
from hummingbot.core.data_type.common import PriceType
from hummingbot.core.data_type.order_book import OrderBook


@runtime_checkable
class MarketDataProtocol(Protocol):
    """Provides market prices, order books, and trading rules.

    This protocol covers the read-only market data surface that executors,
    controllers (via MarketDataProvider), and other consumers use to make
    trading decisions.

    Used by:
        - ExecutorBase.get_price()
        - ExecutorBase.get_order_book()
        - ExecutorBase.get_trading_rules()
        - MarketDataProvider.get_price_by_type()
        - MarketDataProvider.get_order_book()
        - ControllerBase (indirectly, via MarketDataProvider)
        - ArbitrageExecutor, XEMMExecutor (get_quote_price)
    """

    def get_price_by_type(self, trading_pair: str, price_type: PriceType) -> Decimal:
        """Get current price by type (MidPrice, BestBid, BestAsk, LastTrade).

        Implementation notes:
        - Real: reads from OrderBook C++ backing store (_best_bid, _best_ask, _last_trade_price)
        - Simulated: reads from the same OrderBook (fed by ReplayDataSource)
        """
        ...

    def get_order_book(self, trading_pair: str) -> OrderBook:
        """Get current order book for trading pair.

        Returns the full OrderBook instance with bid/ask access.
        Raises ValueError if trading pair is not tracked.
        """
        ...

    @property
    def trading_rules(self) -> Dict[str, TradingRule]:
        """Exchange-enforced trading rules per pair.

        TradingRule contains: min_order_size, min_price_increment,
        min_base_amount_increment, min_notional_size, etc.

        Real connector: fetched from exchange REST API, updated periodically.
        SimulatedConnector: configured at initialization from recorded rules.
        """
        ...

    def quantize_order_price(self, trading_pair: str, price: Decimal) -> Decimal:
        """Round price to exchange tick size.

        Uses trading_rules[trading_pair].min_price_increment.
        Defined on ExchangeBase (not ConnectorBase).
        """
        ...

    def quantize_order_amount(self, trading_pair: str, amount: Decimal) -> Decimal:
        """Round amount to exchange lot size.

        Uses trading_rules[trading_pair].min_base_amount_increment.
        Defined on ExchangeBase (not ConnectorBase).
        """
        ...

    def get_order_price_quantum(self, trading_pair: str, price: Decimal) -> Decimal:
        """Get minimum price increment (tick size).

        Returns trading_rules[trading_pair].min_price_increment.
        Used by quantize_order_price() internally and by some executors directly.
        """
        ...

    def get_order_size_quantum(self, trading_pair: str, order_size: Decimal) -> Decimal:
        """Get minimum size increment (lot size).

        Returns trading_rules[trading_pair].min_base_amount_increment.
        Used by quantize_order_amount() internally.
        """
        ...

    async def get_quote_price(
        self, trading_pair: str, is_buy: bool, amount: Decimal
    ) -> Decimal:
        """Get quote price for a given amount (walks the order book).

        Used by ArbitrageExecutor and XEMMExecutor for cross-exchange pricing.

        Real connector: calls get_price() which walks the C++ order book.
        SimulatedConnector: same implementation, using replayed order book.
        """
        ...
```

**Satisfied by:** `ExchangeBase` (Cython), `ExchangePyBase`, all concrete exchange classes.

**Simulated implementation:** Delegates to `OrderBookTracker.order_books[pair]` for prices,
stores `TradingRule` objects from initialization config. The `quantize_*` methods use the
same math as `ExchangeBase` (rounding to quantum).

**Migration path:** No changes needed. `ExchangeBase` already implements all methods.
Create a standalone `quantize_order_price`/`quantize_order_amount` utility function
that both `ExchangeBase` and `SimulatedConnector` can call, to avoid code duplication.


### 1.2 BalanceProtocol

```python
from decimal import Decimal
from typing import Dict, Protocol, runtime_checkable


@runtime_checkable
class BalanceProtocol(Protocol):
    """Provides balance information and budget checking.

    Used by:
        - ExecutorBase.get_balance()
        - ExecutorBase.get_available_balance()
        - ExecutorBase.adjust_order_candidates()
        - ExecutorBase.lock_order_candidate()
        - ExecutorBase.unlock_order_candidate()
        - ExecutorBase.validate_sufficient_balance()
        - MarketDataProvider.get_balance()
        - MarketDataProvider.get_available_balance()
        - StrategyV2Base (status display via get_all_balances)
    """

    def get_balance(self, currency: str) -> Decimal:
        """Total balance including locked/in-order amounts.

        Real connector: read from _account_balances, updated by REST polling
                        and user stream balance events.
        SimulatedConnector: managed internally, debited/credited on fills.
        """
        ...

    def get_available_balance(self, currency: str) -> Decimal:
        """Available (unlocked) balance for new orders.

        Equal to total balance minus amounts locked in open orders.

        Real connector: _account_available_balances (may be real-time from
                        exchange or computed from in-flight orders).
        SimulatedConnector: computed as total - sum(locked_for_open_orders).
        """
        ...

    def get_all_balances(self) -> Dict[str, Decimal]:
        """All asset balances. Used for status display.

        Returns _account_balances dict.
        """
        ...

    @property
    def budget_checker(self) -> "BudgetChecker":
        """Budget checker for order candidate validation.

        The BudgetChecker itself depends on connector internals
        (see Section 5: BudgetChecker Dependency).
        """
        ...
```

**Satisfied by:** `ConnectorBase` (Cython).

**Simulated implementation:** Maintains `Dict[str, Decimal]` for total and available
balances. On order placement, locks collateral. On fill, transfers assets. On cancel,
releases locked collateral. See Section 5 for BudgetChecker discussion.

**Migration path:** `ConnectorBase` already has `get_balance`, `get_available_balance`,
`get_all_balances`. The `budget_checker` property is on `ExchangeBase`. Add it to
`ConnectorBase` or ensure SimulatedConnector provides its own.


### 1.3 OrderExecutionProtocol

```python
from decimal import Decimal
from typing import Dict, Optional, Protocol, runtime_checkable

from hummingbot.core.data_type.common import OrderType, TradeType
from hummingbot.core.data_type.in_flight_order import InFlightOrder


@runtime_checkable
class OrderExecutionProtocol(Protocol):
    """Places, cancels, and tracks orders.

    Used by:
        - ExecutorBase.place_order() (via strategy.buy/sell routing)
        - ExecutorBase.get_in_flight_order() (via _order_tracker)
        - ExecutorBase.get_active_orders() (via strategy)
        - StrategyV2Base.buy(), .sell(), .cancel()
        - ExecutorOrchestrator (cancel on shutdown)
    """

    def buy(
        self,
        trading_pair: str,
        amount: Decimal,
        order_type: OrderType = OrderType.LIMIT,
        price: Decimal = ...,
        **kwargs,
    ) -> str:
        """Place buy order, return client_order_id.

        This is a fire-and-forget call. The actual order placement happens
        asynchronously. The returned client_order_id can be used to track
        the order via in_flight_orders or event callbacks.

        Real connector: generates order_id, calls safe_ensure_future(_create_order),
                        which calls _place_order (REST API).
        SimulatedConnector: generates order_id, synchronously starts tracking,
                            adds to matching engine queue.
        """
        ...

    def sell(
        self,
        trading_pair: str,
        amount: Decimal,
        order_type: OrderType = OrderType.LIMIT,
        price: Decimal = ...,
        **kwargs,
    ) -> str:
        """Place sell order, return client_order_id."""
        ...

    def cancel(self, trading_pair: str, client_order_id: str) -> None:
        """Cancel an order.

        Real connector: calls safe_ensure_future(_execute_cancel) which sends
                        REST cancel request.
        SimulatedConnector: synchronously updates order state to CANCELED via
                            _order_tracker.process_order_update().
        """
        ...

    @property
    def in_flight_orders(self) -> Dict[str, InFlightOrder]:
        """Currently active (in-flight) orders.

        Returns _order_tracker.active_orders.
        """
        ...

    def get_in_flight_order(self, client_order_id: str) -> Optional[InFlightOrder]:
        """Fetch a tracked order by client order ID.

        This method surfaces what ExecutorBase currently accesses via the
        private _order_tracker.fetch_order(). See Section 3 for details.

        Returns the InFlightOrder if found in active, cached, or lost orders.
        Returns None if the order is not tracked.
        """
        ...
```

**Satisfied by:** `ExchangePyBase` (has `buy`, `sell`, `cancel`, `in_flight_orders`).
Does NOT currently have `get_in_flight_order` -- see Section 3.

**Simulated implementation:** `buy`/`sell` skip the REST call. Instead: quantize,
validate, start tracking, immediately process `OrderUpdate(OPEN)`, then add order
to matching engine. `cancel` immediately processes `OrderUpdate(CANCELED)`.

**Migration path:**
1. Add `get_in_flight_order(client_order_id)` to `ExchangePyBase` as a public method
   that delegates to `self._order_tracker.fetch_order(client_order_id=client_order_id)`.
2. Update `ExecutorBase.get_in_flight_order()` to call the new public method instead
   of accessing `_order_tracker` directly.
3. SimulatedConnector implements the same method natively.


### 1.4 EventSourceProtocol

```python
from typing import Protocol, runtime_checkable


@runtime_checkable
class EventSourceProtocol(Protocol):
    """PubSub event emission and subscription.

    Used by:
        - ExecutorBase.register_events() / unregister_events()
        - StrategyBase event tracking (order created, completed, etc.)
        - EventLogger, EventReporter (ConnectorBase.__init__)

    The PubSub system uses integer event tags (MarketEvent enum values)
    and dispatches to registered listener objects.
    """

    def add_listener(self, event_tag: int, listener: object) -> None:
        """Register a listener for an event type.

        listener is typically a SourceInfoEventForwarder that wraps a callback.
        The event_tag is a MarketEvent enum value (e.g., MarketEvent.OrderFilled.value).
        """
        ...

    def remove_listener(self, event_tag: int, listener: object) -> None:
        """Unregister a listener from an event type."""
        ...

    def trigger_event(self, event_tag: int, message: object) -> None:
        """Emit an event to all registered listeners.

        message is one of: BuyOrderCreatedEvent, SellOrderCreatedEvent,
        OrderFilledEvent, BuyOrderCompletedEvent, SellOrderCompletedEvent,
        OrderCancelledEvent, MarketOrderFailureEvent.

        Real connector: called by ClientOrderTracker._trigger_*_event methods.
        SimulatedConnector: same -- ClientOrderTracker is reused.
        """
        ...
```

**Satisfied by:** `ConnectorBase` (inherits from `NetworkIterator` -> `TimeIterator` ->
`PubSub`, which is Cython). The Cython PubSub provides `c_add_listener`,
`c_remove_listener`, `c_trigger_event` with Python wrappers.

**Simulated implementation:** A pure-Python PubSub implementation. The existing
`hummingbot.core.pubsub` module may already provide this (needs verification), or
a minimal implementation:

```python
from collections import defaultdict

class SimplePubSub:
    def __init__(self):
        self._listeners = defaultdict(list)

    def add_listener(self, event_tag: int, listener) -> None:
        self._listeners[event_tag].append(listener)

    def remove_listener(self, event_tag: int, listener) -> None:
        try:
            self._listeners[event_tag].remove(listener)
        except ValueError:
            pass

    def trigger_event(self, event_tag: int, message) -> None:
        for listener in self._listeners.get(event_tag, []):
            try:
                listener(event_tag, self, message)
            except Exception:
                pass  # log but don't propagate
```

**Migration path:** No changes to existing code. SimulatedConnector includes its own
PubSub implementation or inherits from a pure-Python PubSub base class.


### 1.5 ReadinessProtocol

```python
from typing import Dict, List, Protocol, runtime_checkable


@runtime_checkable
class ReadinessProtocol(Protocol):
    """Connector identity and readiness.

    Used by:
        - MarketDataProvider (ready check before serving data)
        - StrategyV2Base (status display, connector identification)
        - ExecutorBase.is_perpetual_connector() (checks name)
    """

    @property
    def ready(self) -> bool:
        """All subsystems initialized and connected.

        Real connector: checks order_books_initialized, balances loaded,
                        trading rules loaded, user stream connected.
        SimulatedConnector: True once initial snapshots are loaded and
                            trading rules are configured.
        """
        ...

    @property
    def name(self) -> str:
        """Connector name (e.g., 'binance', 'kraken_perpetual').

        Used for logging, connector identification, and to determine
        whether this is a perpetual connector (via 'perpetual' in name).

        SimulatedConnector: configured at init (e.g., 'simulated_binance').
        """
        ...

    @property
    def trading_pairs(self) -> List[str]:
        """Currently subscribed trading pairs.

        Returns the list of trading pairs this connector is tracking.
        """
        ...

    @property
    def status_dict(self) -> Dict[str, bool]:
        """Detailed readiness status per subsystem.

        Real connector: {'symbols_mapping_initialized': bool,
                         'order_books_initialized': bool,
                         'account_balance': bool,
                         'trading_rule_initialized': bool,
                         'user_stream_initialized': bool}
        SimulatedConnector: all True once configured, or subset as applicable.
        """
        ...
```

**Satisfied by:** `ExchangePyBase` (has `ready`, `name`, `trading_pairs`, `status_dict`).

**Simulated implementation:** `ready` returns True after initialization. `name` is a
configured string. `trading_pairs` returns the list provided at construction. `status_dict`
returns all-True after initialization.

**Migration path:** None needed.


### 1.6 PerpetualProtocol

```python
from typing import Dict, Protocol, runtime_checkable


@runtime_checkable
class PerpetualProtocol(Protocol):
    """Perpetual/derivative-specific operations.

    Used by:
        - ExecutorBase (position management, leverage)
        - MarketDataProvider.get_funding_info()
        - PositionExecutor, DCAExecutor, GridExecutor (position_action parameter)

    This protocol is OPTIONAL. Only connectors with 'perpetual' in their name
    implement it. Consumer code checks `is_perpetual_connector(name)` before
    calling these methods.
    """

    def get_funding_info(self, trading_pair: str) -> "FundingInfo":
        """Get current funding rate information.

        Returns FundingInfo with: trading_pair, index_price, mark_price,
        next_funding_utc, rate.

        SimulatedConnector: returns configured/replayed funding info.
        """
        ...

    def set_position_mode(self, position_mode: "PositionMode") -> None:
        """Set hedge mode vs one-way mode.

        PositionMode.HEDGE or PositionMode.ONEWAY.
        SimulatedConnector: stores the mode for position tracking.
        """
        ...

    def set_leverage(self, trading_pair: str, leverage: int) -> None:
        """Set leverage for a trading pair.

        SimulatedConnector: stores leverage, uses it for margin calculations.
        """
        ...

    @property
    def account_positions(self) -> Dict[str, "Position"]:
        """Current open positions.

        Returns Dict mapping position key to Position object with:
        trading_pair, position_side, unrealized_pnl, entry_price, amount, leverage.

        SimulatedConnector: tracks positions from simulated fills.
        """
        ...
```

**Satisfied by:** `PerpetualDerivativePyBase` (extends `ExchangePyBase`).

**Simulated implementation:** Stores positions in memory, updates on fills based on
position_action (OPEN/CLOSE). Funding info either replayed from historical data or
configured as a constant rate.

**Migration path:** None needed for existing code. SimulatedConnector for perpetuals
implements this protocol as an additional mixin.


### 1.7 ConnectorProtocol (Composite)

```python
@runtime_checkable
class ConnectorProtocol(
    MarketDataProtocol,
    BalanceProtocol,
    OrderExecutionProtocol,
    EventSourceProtocol,
    ReadinessProtocol,
    Protocol,
):
    """Full connector interface -- existing ConnectorBase satisfies this.

    This composite protocol represents the complete interface that a fully-featured
    connector provides. It is useful as a type alias for code that genuinely needs
    all capabilities (e.g., the strategy layer).

    Most consumers should accept the narrowest protocol that covers their needs:
    - Controllers: MarketDataProtocol (via MarketDataProvider)
    - MarketDataProvider: MarketDataProtocol + BalanceProtocol + ReadinessProtocol
    - Executors: MarketDataProtocol + BalanceProtocol + OrderExecutionProtocol + EventSourceProtocol
    - Strategy: BalanceProtocol + OrderExecutionProtocol + ReadinessProtocol
    """
    ...


@runtime_checkable
class PerpetualConnectorProtocol(ConnectorProtocol, PerpetualProtocol, Protocol):
    """Full perpetual connector interface."""
    ...
```


## 2. Consumer-Protocol Matrix

This matrix shows which protocols each strategy_v2 consumer actually requires.
The key insight: **no single consumer needs all protocols**.

| Consumer | MarketData | Balance | OrderExec | EventSource | Readiness | Perpetual |
|----------|:----------:|:-------:|:---------:|:-----------:|:---------:|:---------:|
| **ExecutorBase** | YES | YES | YES* | YES | -- | -- |
| **PositionExecutor** | YES | YES | YES* | YES | -- | optional |
| **DCAExecutor** | YES | YES | YES* | YES | -- | optional |
| **ArbitrageExecutor** | YES | YES | YES* | YES | -- | -- |
| **XEMMExecutor** | YES | YES | YES* | YES | -- | optional |
| **GridExecutor** | YES | YES | YES* | YES | -- | optional |
| **OrderExecutor** | YES | YES | YES* | YES | -- | optional |
| **TWAPExecutor** | YES | YES | YES* | YES | -- | -- |
| **LPExecutor** | -- | -- | YES* | YES | -- | -- |
| **ControllerBase** | via MDP | -- | -- | -- | -- | -- |
| **MarketDataProvider** | YES | YES | -- | -- | YES | optional |
| **StrategyV2Base** | -- | YES | YES | -- | YES | -- |
| **ExecutorOrchestrator** | -- | -- | -- | -- | -- | -- |
| **BudgetChecker** | -- | YES** | -- | -- | -- | -- |

*\* OrderExec is accessed via `self._strategy.buy/sell()`, not directly on the connector.*
*\*\* BudgetChecker accesses connector internals -- see Section 5.*


## 3. The _order_tracker Problem

### Current Situation

`ExecutorBase.get_in_flight_order()` directly accesses a private attribute:

```python
# ExecutorBase, line 255
def get_in_flight_order(self, connector_name: str, order_id: str):
    return self.connectors[connector_name]._order_tracker.fetch_order(
        client_order_id=order_id
    )
```

This is the **only** place outside of `ExchangePyBase` that accesses `_order_tracker`
directly. The `ClientOrderTracker.fetch_order()` method searches across active orders,
cached orders, and lost orders.

### Solution: Surface Through OrderExecutionProtocol

Add `get_in_flight_order` to the protocol (already included in Section 1.3 above)
and implement it on `ExchangePyBase`:

```python
# Add to ExchangePyBase
def get_in_flight_order(self, client_order_id: str) -> Optional[InFlightOrder]:
    """Public accessor for tracked orders (active, cached, or lost)."""
    return self._order_tracker.fetch_order(client_order_id=client_order_id)
```

Then update `ExecutorBase`:

```python
# ExecutorBase -- before
def get_in_flight_order(self, connector_name: str, order_id: str):
    return self.connectors[connector_name]._order_tracker.fetch_order(
        client_order_id=order_id
    )

# ExecutorBase -- after
def get_in_flight_order(self, connector_name: str, order_id: str):
    return self.connectors[connector_name].get_in_flight_order(
        client_order_id=order_id
    )
```

**SimulatedConnector implementation:** SimulatedConnector reuses `ClientOrderTracker`
(it has no exchange-specific dependencies beyond the `connector` reference for
`trigger_event` and `current_timestamp`). So `get_in_flight_order` delegates to the
same `_order_tracker.fetch_order()`.

**Risk:** Low. Single call site, backward compatible (can keep `_order_tracker` as
implementation detail).


## 4. The strategy.buy() Routing Problem

### Current Situation

Executors do NOT call `connector.buy()` directly. The call chain is:

```
ExecutorBase.place_order(connector_name, pair, order_type, side, amount, price)
  -> self._strategy.buy(connector_name, pair, amount, order_type, price, position_action)
      -> StrategyV2Base.buy(connector_name, ...)
          -> market_pair = self._market_trading_pair_tuple(connector_name, pair)
          -> self.buy_with_specific_market(market_pair, amount, ...)
              -> StrategyBase.c_buy_with_specific_market(market_pair, ...)  # Cython
                  -> market = market_trading_pair_tuple.market  # ConnectorBase
                  -> market.c_buy(pair, amount, order_type, price, kwargs)
                      -> ExchangePyBase.buy(pair, amount, order_type, price, **kwargs)
```

The strategy layer:
1. Resolves `connector_name` to a `ConnectorBase` instance via `_market_trading_pair_tuple`
2. Validates the market is in the whitelisted set (`_sb_markets`)
3. Calls the Cython `c_buy` on the connector

### Options for Simulation

**Option A (Recommended): Inject SimulatedConnector into strategy.connectors**

The simplest approach that preserves all existing code paths:

```python
# Setup for simulation
strategy = StrategyV2Base(connectors={"simulated_binance": simulated_connector}, ...)

# ExecutorBase.place_order works unchanged:
#   self._strategy.buy("simulated_binance", ...)
#   -> resolves to simulated_connector
#   -> calls simulated_connector.buy(...)
```

Requirements for this to work:
- `SimulatedConnector` must be passable as a `ConnectorBase` to the Cython strategy layer.
  The `c_buy_with_specific_market` casts `market_trading_pair_tuple.market` to
  `ConnectorBase`. This means SimulatedConnector must either:
  - (a) Extend `ConnectorBase` (Cython dependency, undesirable), OR
  - (b) The strategy routing must be patched to bypass the Cython layer.

**Option B: Patch StrategyV2Base.buy() to accept protocol types**

Override `buy`/`sell` in `StrategyV2Base` (pure Python) to skip the Cython
`c_buy_with_specific_market` when the connector satisfies `OrderExecutionProtocol`
but is not a `ConnectorBase`:

```python
class StrategyV2Base:
    def buy(self, connector_name, trading_pair, amount, order_type, price, position_action):
        connector = self.connectors[connector_name]
        # If it's a standard ConnectorBase, use the normal Cython path
        if isinstance(connector, ConnectorBase):
            market_pair = self._market_trading_pair_tuple(connector_name, trading_pair)
            return self.buy_with_specific_market(market_pair, amount, order_type, price,
                                                  position_action=position_action)
        # Otherwise, call the protocol method directly
        return connector.buy(trading_pair, amount, order_type, price,
                            position_action=position_action)
```

This is minimally invasive: the Cython path is preserved for real connectors, and
the protocol path is used for simulated connectors.

**Option C: SimulatedConnector wraps a minimal Cython stub**

Create a thin Cython `ConnectorBase` subclass that delegates all calls to a
pure-Python implementation:

```python
# simulated_connector_bridge.pyx
cdef class SimulatedConnectorBridge(ConnectorBase):
    cdef object _impl  # pure Python SimulatedConnector

    def __init__(self, impl):
        self._impl = impl
        super().__init__()

    def buy(self, trading_pair, amount, order_type, price, **kwargs):
        return self._impl.buy(trading_pair, amount, order_type, price, **kwargs)
    # ... delegate everything
```

This is the most compatible but adds Cython compilation to the simulator package.

**Recommendation:** Option B. It requires a 10-line change to `StrategyV2Base.buy()`
and `sell()`, keeps the existing Cython path untouched, and allows `SimulatedConnector`
to be pure Python. The `isinstance(connector, ConnectorBase)` check cleanly separates
the two code paths.


## 5. BudgetChecker Dependency

### Current Coupling

`BudgetChecker` is initialized with an `ExchangeBase` reference and accesses:

```python
class BudgetChecker:
    def __init__(self, exchange: ExchangeBase):
        self._exchange = exchange

    # Internally accesses:
    #   self._exchange.get_available_balance(currency)
    #   self._exchange.in_flight_orders
    #   self._exchange.get_price(trading_pair, is_buy)
    #   self._exchange.quantize_order_amount(trading_pair, amount)
    #   self._exchange.trading_rules[trading_pair]
```

ExecutorBase uses it via:
```python
self.connectors[exchange].budget_checker.adjust_candidates(order_candidates)
self.connectors[exchange].budget_checker.adjust_candidate_and_lock_available_collateral(oc)
self.connectors[exchange].budget_checker.release_locked_collateral(oc)
```

### Options

**Option A (Recommended): BudgetChecker accepts protocols**

Modify `BudgetChecker.__init__` to accept a union of protocols instead of `ExchangeBase`:

```python
from typing import Union

# Type alias for what BudgetChecker actually needs
BudgetCheckerConnector = Union[
    # Must have: get_available_balance, in_flight_orders, get_price,
    #            quantize_order_amount, trading_rules
    "MarketDataProtocol & BalanceProtocol & OrderExecutionProtocol"
]

class BudgetChecker:
    def __init__(self, exchange: BudgetCheckerConnector):
        self._exchange = exchange
```

Since Python protocols use structural subtyping, `SimulatedConnector` would satisfy
this automatically if it implements the required methods.

**Option B: SimulatedBudgetChecker**

Create a separate `SimulatedBudgetChecker` that implements the same public API
(`adjust_candidates`, `adjust_candidate_and_lock_available_collateral`,
`release_locked_collateral`) but is backed by the simulated balance state:

```python
class SimulatedBudgetChecker:
    """BudgetChecker for simulated connectors.

    Same public interface as BudgetChecker but operates on simulated balances.
    """
    def __init__(self, connector: "SimulatedConnector"):
        self._connector = connector
        self._locked_collateral: Dict[str, Decimal] = defaultdict(lambda: Decimal("0"))

    def adjust_candidates(self, order_candidates, all_or_none=True):
        # Same logic as BudgetChecker but using self._connector methods
        ...
```

**Recommendation:** Option A is cleaner long-term. The existing `BudgetChecker` already
only calls public methods on `ExchangeBase`. Changing the type hint from `ExchangeBase`
to a protocol alias is a no-op at runtime (Python duck typing) and makes the dependency
explicit. SimulatedConnector can reuse the existing `BudgetChecker` directly.


## 6. Detailed Simulated Implementation Sketch

```python
from collections import defaultdict
from decimal import Decimal
from typing import Dict, List, Optional

from hummingbot.connector.budget_checker import BudgetChecker
from hummingbot.connector.client_order_tracker import ClientOrderTracker
from hummingbot.connector.trading_rule import TradingRule
from hummingbot.core.data_type.common import OrderType, PriceType, TradeType
from hummingbot.core.data_type.in_flight_order import InFlightOrder, OrderState, OrderUpdate, TradeUpdate
from hummingbot.core.data_type.order_book import OrderBook
from hummingbot.core.data_type.order_book_tracker import OrderBookTracker


class SimulatedConnector:
    """Pure-Python connector satisfying all protocols for market simulation.

    Implements: MarketDataProtocol, BalanceProtocol, OrderExecutionProtocol,
                EventSourceProtocol, ReadinessProtocol.

    Does NOT extend ConnectorBase (no Cython dependency).
    Reuses ClientOrderTracker for order state management and event emission.
    Reuses OrderBookTracker for order book management (fed by ReplayDataSource).
    """

    def __init__(
        self,
        name: str,
        trading_pairs: List[str],
        trading_rules: Dict[str, TradingRule],
        initial_balances: Dict[str, Decimal],
        order_book_tracker: OrderBookTracker,
        fee_rate: Decimal = Decimal("0.001"),  # 0.1% default
    ):
        self._name = name
        self._trading_pairs = trading_pairs
        self._trading_rules = trading_rules
        self._fee_rate = fee_rate

        # Balance state
        self._account_balances = dict(initial_balances)
        self._account_available_balances = dict(initial_balances)

        # PubSub (pure Python)
        self._listeners: Dict[int, list] = defaultdict(list)

        # Order tracking (reuse existing ClientOrderTracker)
        self._order_tracker = ClientOrderTracker(connector=self)

        # Order book (fed by ReplayDataSource via standard OrderBookTracker)
        self._order_book_tracker = order_book_tracker

        # Budget checker (reuse existing, works via protocol methods)
        self._budget_checker = BudgetChecker(exchange=self)

        # Simulated time (must be set externally)
        self._current_timestamp: float = 0.0

        # Order ID counter
        self._next_exchange_order_id = 1

        # Ready state
        self._ready = False

    # --- ReadinessProtocol ---

    @property
    def ready(self) -> bool:
        return self._ready and self._order_book_tracker.ready

    @property
    def name(self) -> str:
        return self._name

    @property
    def trading_pairs(self) -> List[str]:
        return self._trading_pairs

    @property
    def status_dict(self) -> Dict[str, bool]:
        return {
            "order_books_initialized": self._order_book_tracker.ready,
            "trading_rule_initialized": len(self._trading_rules) > 0,
            "balances_initialized": len(self._account_balances) > 0,
        }

    # --- MarketDataProtocol ---

    def get_price_by_type(self, trading_pair: str, price_type: PriceType) -> Decimal:
        order_book = self.get_order_book(trading_pair)
        if price_type == PriceType.MidPrice:
            return Decimal(str((order_book.get_price(True) + order_book.get_price(False)) / 2))
        elif price_type == PriceType.BestBid:
            return Decimal(str(order_book.get_price(True)))
        elif price_type == PriceType.BestAsk:
            return Decimal(str(order_book.get_price(False)))
        elif price_type == PriceType.LastTrade:
            return Decimal(str(order_book.last_trade_price))
        return Decimal("NaN")

    def get_order_book(self, trading_pair: str) -> OrderBook:
        if trading_pair not in self._order_book_tracker.order_books:
            raise ValueError(f"No order book for '{trading_pair}'")
        return self._order_book_tracker.order_books[trading_pair]

    @property
    def trading_rules(self) -> Dict[str, TradingRule]:
        return self._trading_rules

    def quantize_order_price(self, trading_pair: str, price: Decimal) -> Decimal:
        quantum = self.get_order_price_quantum(trading_pair, price)
        return (price // quantum) * quantum

    def quantize_order_amount(self, trading_pair: str, amount: Decimal) -> Decimal:
        quantum = self.get_order_size_quantum(trading_pair, amount)
        return (amount // quantum) * quantum

    def get_order_price_quantum(self, trading_pair: str, price: Decimal) -> Decimal:
        return Decimal(self._trading_rules[trading_pair].min_price_increment)

    def get_order_size_quantum(self, trading_pair: str, order_size: Decimal) -> Decimal:
        return Decimal(self._trading_rules[trading_pair].min_base_amount_increment)

    async def get_quote_price(self, trading_pair: str, is_buy: bool, amount: Decimal) -> Decimal:
        return Decimal(str(self.get_order_book(trading_pair).get_price_for_volume(is_buy, float(amount)).result_price))

    # --- BalanceProtocol ---

    def get_balance(self, currency: str) -> Decimal:
        return self._account_balances.get(currency, Decimal("0"))

    def get_available_balance(self, currency: str) -> Decimal:
        return self._account_available_balances.get(currency, Decimal("0"))

    def get_all_balances(self) -> Dict[str, Decimal]:
        return dict(self._account_balances)

    @property
    def budget_checker(self) -> BudgetChecker:
        return self._budget_checker

    # --- OrderExecutionProtocol ---

    def buy(self, trading_pair, amount, order_type=OrderType.LIMIT, price=Decimal("NaN"), **kwargs) -> str:
        return self._create_simulated_order(TradeType.BUY, trading_pair, amount, order_type, price, **kwargs)

    def sell(self, trading_pair, amount, order_type=OrderType.LIMIT, price=Decimal("NaN"), **kwargs) -> str:
        return self._create_simulated_order(TradeType.SELL, trading_pair, amount, order_type, price, **kwargs)

    def cancel(self, trading_pair: str, client_order_id: str) -> None:
        tracked = self._order_tracker.fetch_tracked_order(client_order_id)
        if tracked is not None:
            self._order_tracker.process_order_update(OrderUpdate(
                client_order_id=client_order_id,
                trading_pair=trading_pair,
                update_timestamp=self._current_timestamp,
                new_state=OrderState.CANCELED,
            ))
            self._release_order_collateral(tracked)

    @property
    def in_flight_orders(self) -> Dict[str, InFlightOrder]:
        return self._order_tracker.active_orders

    def get_in_flight_order(self, client_order_id: str) -> Optional[InFlightOrder]:
        return self._order_tracker.fetch_order(client_order_id=client_order_id)

    # --- EventSourceProtocol ---

    def add_listener(self, event_tag: int, listener) -> None:
        self._listeners[event_tag].append(listener)

    def remove_listener(self, event_tag: int, listener) -> None:
        try:
            self._listeners[event_tag].remove(listener)
        except ValueError:
            pass

    def trigger_event(self, event_tag, message) -> None:
        for listener in self._listeners.get(event_tag, []):
            try:
                listener(event_tag, self, message)
            except Exception:
                pass

    # --- Simulation internals ---

    @property
    def current_timestamp(self) -> float:
        return self._current_timestamp

    def _create_simulated_order(self, trade_type, trading_pair, amount, order_type, price, **kwargs) -> str:
        """Create and immediately open a simulated order."""
        # ... order creation, tracking, validation, matching engine registration
        # See docs/architecture/04-data-flow-analysis.md Section 6
        ...

    def check_and_match_orders(self):
        """Called on each tick to match pending orders against current order book."""
        # ... matching engine logic
        ...

    def _release_order_collateral(self, order: InFlightOrder):
        """Release locked collateral for a canceled/failed order."""
        ...
```


## 7. Migration Roadmap

### Phase 1: Protocol Definitions (No Code Changes)

**Scope:** Create `hummingbot/connector/protocols.py` with all protocol definitions.

**Validation:**
```python
# In a test file
from hummingbot.connector.protocols import ConnectorProtocol
from hummingbot.connector.exchange.binance.binance_exchange import BinanceExchange

# Structural subtyping check
assert isinstance(BinanceExchange(...), ConnectorProtocol)
```

**Risk:** None. Purely additive.
**Effort:** 1-2 days.

### Phase 2: Surface _order_tracker Access

**Scope:** Add `get_in_flight_order(client_order_id)` to `ExchangePyBase`. Update
`ExecutorBase.get_in_flight_order()` to use it.

**Changes:**
- `hummingbot/connector/exchange_py_base.py`: Add method (3 lines)
- `hummingbot/strategy_v2/executors/executor_base.py`: Update call (1 line)

**Risk:** Low. Single call site.
**Effort:** 1 hour + testing.

### Phase 3: Strategy Buy/Sell Protocol Path

**Scope:** Add protocol-aware path to `StrategyV2Base.buy()` and `.sell()` (Option B
from Section 4).

**Changes:**
- `hummingbot/strategy/strategy_v2_base.py`: Add isinstance check + protocol path (10 lines each)

**Risk:** Low. Existing Cython path unchanged. New path only activates for non-ConnectorBase.
**Effort:** 1 day + testing.

### Phase 4: BudgetChecker Protocol Compatibility

**Scope:** Change `BudgetChecker.__init__` type hint from `ExchangeBase` to a protocol
type alias. Verify it works with both real and simulated connectors.

**Changes:**
- `hummingbot/connector/budget_checker.py`: Type hint change (1 line, no runtime effect)
- Verify all methods called on `self._exchange` are part of the protocol

**Risk:** None (type hints only).
**Effort:** 1 hour.

### Phase 5: SimulatedConnector Implementation

**Scope:** Build `SimulatedConnector` as a pure-Python class implementing all protocols.
This is the core of the market-simulator sub-package.

**Dependencies:** Phases 1-4 must be complete.
**Effort:** 2-4 weeks.

### Phase 6: Extract Protocols to Sub-Package

**Scope:** Move protocol definitions from `hummingbot/connector/protocols.py` to
`hb-connector-protocols` sub-package. Update imports across codebase.

**Risk:** Low (import path changes only).
**Effort:** 1-2 days.


## 8. Open Questions

1. **Cython PubSub compatibility**: The `SourceInfoEventForwarder` callback signature is
   `(event_tag: int, market: ConnectorBase, event: EventType)`. The `market` parameter
   is typed as `ConnectorBase`. Will executors break if they receive a `SimulatedConnector`
   instead? Current executor code does not use the `market` parameter directly (it's
   passed through but ignored), so this should be safe.

2. **NetworkIterator.current_timestamp**: Several components use `connector.current_timestamp`
   (via NetworkIterator -> TimeIterator -> Clock). SimulatedConnector must provide this
   property, driven by simulated time. This is straightforward but must be wired to the
   simulation clock.

3. **Order ID generation**: Real connectors use `get_new_client_order_id()` which includes
   exchange-specific prefixes and length limits. SimulatedConnector should use the same
   function with a simulated prefix to ensure order IDs are globally unique and compatible
   with executor tracking.

4. **Trade fee calculation**: Real connectors fetch fee schedules from the exchange.
   SimulatedConnector needs configurable fee rates (maker/taker) provided at initialization.
   The `TradeFeeBase` / `AddedToCostTradeFee` classes can be reused.

5. **Tick mechanism**: Real connectors receive `tick(timestamp)` calls from the Clock via
   NetworkIterator. SimulatedConnector needs an equivalent mechanism to trigger the
   matching engine. This could be a direct `advance_time(new_timestamp)` method or
   integration with a `SimulatedClock`.
