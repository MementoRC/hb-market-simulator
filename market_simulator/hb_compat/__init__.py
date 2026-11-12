"""hb_compat — adapters bridging market_simulator's internal interfaces onto canonical L0
packages. It does not import hummingbot: the ExchangePyBase adapter around
SimulatedConnectorCore lives in the parent repo (see hb-market-simulator#62).
"""

from market_simulator.hb_compat.event_bus_adapter import EventBusAdapter

__all__ = ["EventBusAdapter"]
