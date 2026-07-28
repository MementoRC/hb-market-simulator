"""hb_compat — adapters bridging market_simulator's internal interfaces onto canonical L0
packages and hummingbot's runtime. This is the ONLY package that imports from hummingbot.
"""

from market_simulator.hb_compat.event_bus_adapter import EventBusAdapter

__all__ = ["EventBusAdapter"]
