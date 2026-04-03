"""Performance metrics for market simulation results.

Provides drawdown, Sharpe ratio, profit factor, and win rate calculations.
The augmented ``performance`` module includes Cython annotations for optional
compilation; a plain-Python fallback lives under ``__pure_python__/``.
"""

from market_simulator.metrics.performance import (
    calculate_all_metrics,
    calculate_max_drawdown,
    calculate_max_drawdown_vectorized,
    calculate_profit_factor,
    calculate_sharpe_ratio,
)

__all__ = [
    "calculate_all_metrics",
    "calculate_max_drawdown",
    "calculate_max_drawdown_vectorized",
    "calculate_profit_factor",
    "calculate_sharpe_ratio",
]
