"""Re-export of the canonical OrderType/TradeType enums from hb-data-type-primitives.

Isolating the external dependency import here keeps market_simulator.core.types
(and the rest of the package outside hb_compat) free of direct imports from
hb-data-type-primitives, following the same hb_compat adapter convention used
elsewhere in this package (see hb_compat/simulated_connector.py) and the
ADR 0001 Group D migration pattern (e.g. hb-strategy-framework's EventBusAdapter,
hb-market-connector's feat/compat-order-trade-type).

market_simulator.core.types.OrderType previously redeclared its own local
`is_conditional` property. The canonical OrderType exposes equivalent behavior
via the `is_conditional_type()` method; call sites use that directly instead of
a shimmed property, avoiding any need to monkeypatch or subclass the shared
canonical Enum (Python enums with members cannot be subclassed).
"""

from data_type_primitives import OrderType, TradeType

__all__ = ["OrderType", "TradeType"]
