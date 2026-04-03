# cython: language_level=3str
# cython: augmented_pure_python=True
# cython: boundscheck=False
# cython: wraparound=False
# cython: nonecheck=False
# cython: cdivision=True
# distutils: language=c
# distutils: define_macros=NPY_NO_DEPRECATED_API=NPY_1_7_API_VERSION
"""Augmented pure-Python performance metrics.

When run as plain Python these functions work normally.  When compiled with
Cython (via ``hb-cython-framework``) the ``@cython.*`` annotations produce
optimised C code with typed memoryviews and GIL-free inner loops.
"""

from __future__ import annotations

import math

import cython


@cython.ccall
def calculate_max_drawdown(pnl_series: cython.double[:], n: cython.int) -> tuple:
    """Calculate max drawdown and max drawdown percentage.

    Args:
        pnl_series: Cumulative P&L values as a typed memoryview.
        n: Length of the series.

    Returns:
        (max_drawdown_abs, max_drawdown_pct)
    """
    if n <= 0:
        return 0.0, 0.0

    peak: cython.double = pnl_series[0]
    max_dd: cython.double = 0.0
    max_dd_pct: cython.double = 0.0
    i: cython.int
    val: cython.double
    dd: cython.double
    dd_pct: cython.double

    for i in range(1, n):
        val = pnl_series[i]
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


@cython.ccall
def calculate_sharpe_ratio(
    returns: cython.double[:],
    n: cython.int,
    periods_per_year: cython.double = 252.0,
) -> cython.double:
    """Calculate annualized Sharpe ratio from a returns series.

    Uses single-pass variance (Welford-adjacent) for numerical stability.

    Args:
        returns: Per-period returns as a typed memoryview.
        n: Length of the series.
        periods_per_year: Annualization factor (default 252 trading days).

    Returns:
        Annualized Sharpe ratio.  Returns 0.0 when *n* < 2 or std == 0.
    """
    if n < 2:
        return 0.0

    total: cython.double = 0.0
    sq_total: cython.double = 0.0
    i: cython.int
    r: cython.double

    for i in range(n):
        r = returns[i]
        total += r
        sq_total += r * r

    mean: cython.double = total / n
    variance: cython.double = (sq_total / n) - (mean * mean)
    if variance <= 0.0:
        return 0.0

    std: cython.double = math.sqrt(variance)
    return (mean / std) * math.sqrt(periods_per_year)


@cython.ccall
def calculate_profit_factor(pnl_values: cython.double[:], n: cython.int) -> cython.double:
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

    gross_profit: cython.double = 0.0
    gross_loss: cython.double = 0.0
    i: cython.int
    val: cython.double

    for i in range(n):
        val = pnl_values[i]
        if val > 0.0:
            gross_profit += val
        elif val < 0.0:
            gross_loss += -val

    if gross_loss == 0.0:
        return float("inf") if gross_profit > 0.0 else 0.0
    return gross_profit / gross_loss


@cython.ccall
def calculate_all_metrics(pnl_series: cython.double[:], n: cython.int) -> tuple:
    """Calculate all performance metrics in a single pass.

    *pnl_series* is treated as **cumulative P&L** for drawdown and as the
    source of **per-period returns** (consecutive differences) for Sharpe
    and profit factor.

    Args:
        pnl_series: Cumulative P&L values as a typed memoryview.
        n: Length of the series.

    Returns:
        (max_drawdown, max_drawdown_pct, sharpe_ratio, profit_factor)
    """
    if n <= 0:
        return 0.0, 0.0, 0.0, 0.0

    # --- drawdown tracking ---
    peak: cython.double = pnl_series[0]
    max_dd: cython.double = 0.0
    max_dd_pct: cython.double = 0.0

    # --- returns accumulation ---
    returns_sum: cython.double = 0.0
    returns_sq_sum: cython.double = 0.0
    gross_profit: cython.double = 0.0
    gross_loss: cython.double = 0.0
    num_returns: cython.int = 0

    prev: cython.double = pnl_series[0]
    i: cython.int
    val: cython.double
    dd: cython.double
    dd_pct: cython.double
    ret: cython.double

    for i in range(1, n):
        val = pnl_series[i]

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

    # Sharpe ratio
    sharpe: cython.double = 0.0
    if num_returns >= 2:
        mean: cython.double = returns_sum / num_returns
        variance: cython.double = (returns_sq_sum / num_returns) - (mean * mean)
        if variance > 0.0:
            sharpe = (mean / math.sqrt(variance)) * math.sqrt(252.0)

    # Profit factor
    pf: cython.double
    if gross_loss == 0.0:
        pf = float("inf") if gross_profit > 0.0 else 0.0
    else:
        pf = gross_profit / gross_loss

    return max_dd, max_dd_pct, sharpe, pf
