"""Plain-Python performance metrics (no Cython dependency).

Every function here has the same signature and semantics as the augmented
version in ``market_simulator.metrics.performance``.  This module is used
when Cython is not installed or when the augmented module has not been
compiled.
"""

from __future__ import annotations

import math

import numpy as np


def calculate_max_drawdown(pnl_series: list[float] | object, n: int) -> tuple[float, float]:
    """Calculate max drawdown and max drawdown percentage.

    Args:
        pnl_series: Cumulative P&L values (list, numpy array, or any indexable).
        n: Length of the series.

    Returns:
        (max_drawdown_abs, max_drawdown_pct) — both are non-negative values
        representing the largest peak-to-trough decline.
    """
    if n <= 0:
        return 0.0, 0.0

    peak: float = float(pnl_series[0])
    max_dd: float = 0.0
    max_dd_pct: float = 0.0

    for i in range(1, n):
        val = float(pnl_series[i])
        if val > peak:
            peak = val
        dd = peak - val
        if dd > max_dd:
            max_dd = dd
        if peak > 0.0:
            dd_pct = dd / peak
            if dd_pct > max_dd_pct:
                max_dd_pct = dd_pct

    return max_dd, max_dd_pct


def calculate_max_drawdown_vectorized(
    cumulative_returns: list[float] | object,
) -> tuple[float, float]:
    """Calculate max drawdown using NumPy vectorized operations.

    Faster than the scalar loop for large arrays.  Uses
    ``np.maximum.accumulate`` for peak tracking.

    Args:
        cumulative_returns: Cumulative P&L values.

    Returns:
        (max_drawdown_abs, max_drawdown_pct)
    """
    arr = np.asarray(cumulative_returns, dtype=np.float64)
    if len(arr) == 0:
        return 0.0, 0.0

    peak = np.maximum.accumulate(arr)
    drawdown = peak - arr

    max_dd = float(np.max(drawdown))

    # Percentage drawdown only where peak > 0
    positive_peak = peak.copy()
    positive_peak[positive_peak <= 0.0] = np.inf
    dd_pct = drawdown / positive_peak
    max_dd_pct = float(np.max(dd_pct))

    return max_dd, max_dd_pct


def calculate_sharpe_ratio(
    returns: list[float] | object, n: int, periods_per_year: float = 252.0
) -> float:
    """Calculate annualized Sharpe ratio from a returns series.

    Uses Welford-adjacent single-pass variance for numerical stability.

    Args:
        returns: Per-period returns (list, numpy array, or any indexable).
        n: Length of the series.
        periods_per_year: Annualization factor (default 252 trading days).

    Returns:
        Annualized Sharpe ratio.  Returns 0.0 when *n* < 2 or std == 0.
    """
    if n < 2:
        return 0.0

    total: float = 0.0
    sq_total: float = 0.0

    for i in range(n):
        r = float(returns[i])
        total += r
        sq_total += r * r

    mean = total / n
    variance = (sq_total / n) - (mean * mean)
    if variance <= 0.0:
        return 0.0

    std = math.sqrt(variance)
    return (mean / std) * math.sqrt(periods_per_year)


def calculate_profit_factor(pnl_values: list[float] | object, n: int) -> float:
    """Calculate profit factor (sum of wins / abs sum of losses).

    Args:
        pnl_values: Individual trade P&L values (not cumulative).
        n: Length of the series.

    Returns:
        Profit factor.  Returns ``inf`` when there are no losses, or 0.0
        when there are no wins.
    """
    if n <= 0:
        return 0.0

    gross_profit: float = 0.0
    gross_loss: float = 0.0

    for i in range(n):
        val = float(pnl_values[i])
        if val > 0.0:
            gross_profit += val
        elif val < 0.0:
            gross_loss += -val

    if gross_loss == 0.0:
        return float("inf") if gross_profit > 0.0 else 0.0
    return gross_profit / gross_loss


def calculate_all_metrics(
    pnl_series: list[float] | object, n: int
) -> tuple[float, float, float, float]:
    """Calculate all performance metrics in a single pass.

    *pnl_series* is treated as **cumulative P&L** for drawdown and as the
    source of **per-period returns** (consecutive differences) for Sharpe
    and profit factor.

    Args:
        pnl_series: Cumulative P&L values.
        n: Length of the series.

    Returns:
        (max_drawdown, max_drawdown_pct, sharpe_ratio, profit_factor)
    """
    if n <= 0:
        return 0.0, 0.0, 0.0, 0.0

    # --- drawdown (over cumulative P&L) ---
    peak: float = float(pnl_series[0])
    max_dd: float = 0.0
    max_dd_pct: float = 0.0

    # --- returns accumulation (for Sharpe + profit factor) ---
    returns_sum: float = 0.0
    returns_sq_sum: float = 0.0
    gross_profit: float = 0.0
    gross_loss: float = 0.0
    num_returns: int = 0

    prev: float = float(pnl_series[0])
    for i in range(1, n):
        val = float(pnl_series[i])

        # drawdown
        if val > peak:
            peak = val
        dd = peak - val
        if dd > max_dd:
            max_dd = dd
        if peak > 0.0:
            dd_pct = dd / peak
            if dd_pct > max_dd_pct:
                max_dd_pct = dd_pct

        # per-period return
        ret = val - prev
        returns_sum += ret
        returns_sq_sum += ret * ret
        if ret > 0.0:
            gross_profit += ret
        elif ret < 0.0:
            gross_loss += -ret
        num_returns += 1
        prev = val

    # Sharpe
    sharpe: float = 0.0
    if num_returns >= 2:
        mean = returns_sum / num_returns
        variance = (returns_sq_sum / num_returns) - (mean * mean)
        if variance > 0.0:
            sharpe = (mean / math.sqrt(variance)) * math.sqrt(252.0)

    # profit factor
    if gross_loss == 0.0:
        pf = float("inf") if gross_profit > 0.0 else 0.0
    else:
        pf = gross_profit / gross_loss

    return max_dd, max_dd_pct, sharpe, pf
