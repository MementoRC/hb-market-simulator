"""hb_compat — adapters bridging market_simulator's internal interfaces onto canonical L0
packages. It does not import hummingbot: the ExchangePyBase adapter around
SimulatedConnectorCore lives in the parent repo (see hb-market-simulator#62).
"""

from market_simulator.hb_compat.event_bus_adapter import EventBusAdapter

# The core<->DTP bridge lives in market_simulator.hb_compat.canonical. It is NOT re-exported
# here: it imports connector.core, which imports core.types, which imports hb_compat.common,
# so an eager import from this package __init__ would be circular.
__all__ = ["EventBusAdapter"]
