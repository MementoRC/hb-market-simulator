"""Unit tests for performance metrics.

Tests cover:
- Known-value calculations (hand-verified)
- Edge cases (empty, single value, all wins, all losses, zero std)
- Consistency between augmented and pure-python implementations
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from market_simulator.metrics.__pure_python__.performance import (
    calculate_all_metrics as pp_calculate_all_metrics,
)
from market_simulator.metrics.__pure_python__.performance import (
    calculate_max_drawdown as pp_calculate_max_drawdown,
)
from market_simulator.metrics.__pure_python__.performance import (
    calculate_max_drawdown_vectorized as pp_calculate_max_drawdown_vectorized,
)
from market_simulator.metrics.__pure_python__.performance import (
    calculate_profit_factor as pp_calculate_profit_factor,
)
from market_simulator.metrics.__pure_python__.performance import (
    calculate_sharpe_ratio as pp_calculate_sharpe_ratio,
)
from market_simulator.metrics.performance import (
    calculate_all_metrics,
    calculate_max_drawdown,
    calculate_max_drawdown_vectorized,
    calculate_profit_factor,
    calculate_sharpe_ratio,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _arr(values: list[float]) -> np.ndarray:
    return np.array(values, dtype=np.float64)


# ---------------------------------------------------------------------------
# calculate_max_drawdown
# ---------------------------------------------------------------------------


class TestCalculateMaxDrawdown:
    def test_monotonically_increasing(self):
        """No drawdown when P&L only goes up."""
        data = _arr([0.0, 1.0, 2.0, 3.0, 4.0])
        dd, dd_pct = calculate_max_drawdown(data, len(data))
        assert dd == 0.0
        assert dd_pct == 0.0

    def test_simple_drawdown(self):
        """Peak at 100, drops to 80 → dd=20, dd_pct=0.20."""
        data = _arr([0.0, 50.0, 100.0, 80.0, 90.0])
        dd, dd_pct = calculate_max_drawdown(data, len(data))
        assert dd == pytest.approx(20.0)
        assert dd_pct == pytest.approx(0.20)

    def test_multiple_drawdowns_returns_max(self):
        """Two drawdowns: 10 and 30 — max is 30."""
        data = _arr([0.0, 100.0, 90.0, 120.0, 90.0])
        dd, dd_pct = calculate_max_drawdown(data, len(data))
        assert dd == pytest.approx(30.0)
        assert dd_pct == pytest.approx(30.0 / 120.0)

    def test_single_value(self):
        data = _arr([42.0])
        dd, dd_pct = calculate_max_drawdown(data, 1)
        assert dd == 0.0
        assert dd_pct == 0.0

    def test_empty(self):
        data = _arr([])
        dd, dd_pct = calculate_max_drawdown(data, 0)
        assert dd == 0.0
        assert dd_pct == 0.0

    def test_negative_starting_value(self):
        """Peak never goes above zero → dd_pct stays 0."""
        data = _arr([-10.0, -5.0, -8.0])
        dd, dd_pct = calculate_max_drawdown(data, len(data))
        assert dd == pytest.approx(3.0)  # peak=-5, trough=-8
        assert dd_pct == pytest.approx(0.0)  # peak <= 0


# ---------------------------------------------------------------------------
# calculate_sharpe_ratio
# ---------------------------------------------------------------------------


class TestCalculateSharpeRatio:
    def test_zero_variance(self):
        """Constant returns → std=0 → Sharpe=0."""
        data = _arr([1.0, 1.0, 1.0, 1.0])
        assert calculate_sharpe_ratio(data, len(data)) == 0.0

    def test_positive_returns(self):
        """Known Sharpe: mean=0.01, std≈0.00816 → ~19.44 annualized."""
        data = _arr([0.01, 0.02, 0.01, 0.0, 0.01])
        n = len(data)
        mean = sum(data) / n
        var = sum((x - mean) ** 2 for x in data) / n
        expected = (mean / math.sqrt(var)) * math.sqrt(252.0)
        assert calculate_sharpe_ratio(data, n) == pytest.approx(expected, rel=1e-6)

    def test_single_return(self):
        """n < 2 → 0."""
        data = _arr([0.05])
        assert calculate_sharpe_ratio(data, 1) == 0.0

    def test_empty(self):
        data = _arr([])
        assert calculate_sharpe_ratio(data, 0) == 0.0

    def test_negative_returns(self):
        """Negative mean → negative Sharpe."""
        data = _arr([-0.01, -0.02, -0.01, -0.03])
        assert calculate_sharpe_ratio(data, len(data)) < 0.0


# ---------------------------------------------------------------------------
# calculate_profit_factor
# ---------------------------------------------------------------------------


class TestCalculateProfitFactor:
    def test_basic(self):
        """Wins=30, losses=10 → pf=3.0."""
        data = _arr([10.0, 20.0, -5.0, -5.0])
        assert calculate_profit_factor(data, len(data)) == pytest.approx(3.0)

    def test_all_wins(self):
        data = _arr([1.0, 2.0, 3.0])
        assert calculate_profit_factor(data, len(data)) == float("inf")

    def test_all_losses(self):
        data = _arr([-1.0, -2.0, -3.0])
        assert calculate_profit_factor(data, len(data)) == 0.0

    def test_empty(self):
        data = _arr([])
        assert calculate_profit_factor(data, 0) == 0.0

    def test_breakeven(self):
        """Wins == losses → pf = 1.0."""
        data = _arr([5.0, -5.0])
        assert calculate_profit_factor(data, len(data)) == pytest.approx(1.0)

    def test_zero_values_ignored(self):
        """Zero P&L entries count as neither win nor loss."""
        data = _arr([10.0, 0.0, -5.0])
        assert calculate_profit_factor(data, len(data)) == pytest.approx(2.0)


# ---------------------------------------------------------------------------
# calculate_all_metrics (single-pass combined)
# ---------------------------------------------------------------------------


class TestCalculateAllMetrics:
    def test_known_values(self):
        """Cumulative P&L: 0, 10, 5, 15, 12.

        Returns: +10, -5, +10, -3
        Drawdown: peak=15, trough=12 → dd=3 (but also peak=10, trough=5 → dd=5)
        Max dd=5, max dd_pct = 5/10 = 0.5
        Profit factor: (10+10) / (5+3) = 2.5
        """
        data = _arr([0.0, 10.0, 5.0, 15.0, 12.0])
        dd, dd_pct, sharpe, pf = calculate_all_metrics(data, len(data))

        assert dd == pytest.approx(5.0)
        assert dd_pct == pytest.approx(0.5)
        assert pf == pytest.approx(2.5)
        # Sharpe should be positive (net gain)
        assert sharpe > 0.0

    def test_empty(self):
        data = _arr([])
        assert calculate_all_metrics(data, 0) == (0.0, 0.0, 0.0, 0.0)

    def test_single_value(self):
        data = _arr([100.0])
        dd, dd_pct, sharpe, pf = calculate_all_metrics(data, 1)
        assert dd == 0.0
        assert sharpe == 0.0
        assert pf == 0.0

    def test_monotonic_increase(self):
        data = _arr([0.0, 1.0, 2.0, 3.0])
        dd, dd_pct, sharpe, pf = calculate_all_metrics(data, len(data))
        assert dd == 0.0
        assert dd_pct == 0.0
        assert pf == float("inf")  # all wins, no losses


# ---------------------------------------------------------------------------
# Consistency: augmented vs pure-python
# ---------------------------------------------------------------------------


class TestAugmentedVsPurePython:
    """Verify both implementations produce identical results."""

    @pytest.fixture
    def sample_data(self):
        rng = np.random.default_rng(42)
        cumulative = np.cumsum(rng.normal(0.5, 2.0, 200))
        return cumulative.astype(np.float64)

    @pytest.fixture
    def returns_data(self):
        rng = np.random.default_rng(99)
        return rng.normal(0.001, 0.02, 100).astype(np.float64)

    def test_max_drawdown_consistency(self, sample_data):
        n = len(sample_data)
        aug = calculate_max_drawdown(sample_data, n)
        pp = pp_calculate_max_drawdown(sample_data, n)
        assert aug[0] == pytest.approx(pp[0], rel=1e-10)
        assert aug[1] == pytest.approx(pp[1], rel=1e-10)

    def test_sharpe_consistency(self, returns_data):
        n = len(returns_data)
        aug = calculate_sharpe_ratio(returns_data, n)
        pp = pp_calculate_sharpe_ratio(returns_data, n)
        assert aug == pytest.approx(pp, rel=1e-10)

    def test_profit_factor_consistency(self, returns_data):
        n = len(returns_data)
        aug = calculate_profit_factor(returns_data, n)
        pp = pp_calculate_profit_factor(returns_data, n)
        assert aug == pytest.approx(pp, rel=1e-10)

    def test_all_metrics_consistency(self, sample_data):
        n = len(sample_data)
        aug = calculate_all_metrics(sample_data, n)
        pp = pp_calculate_all_metrics(sample_data, n)
        for a, p in zip(aug, pp, strict=True):
            assert a == pytest.approx(p, rel=1e-10)


# ---------------------------------------------------------------------------
# calculate_max_drawdown_vectorized
# ---------------------------------------------------------------------------


class TestCalculateMaxDrawdownVectorized:
    def test_matches_scalar_simple(self):
        """Vectorized result matches scalar on simple data."""
        data = _arr([0.0, 50.0, 100.0, 80.0, 90.0])
        scalar = calculate_max_drawdown(data, len(data))
        vec = calculate_max_drawdown_vectorized(data)
        assert vec[0] == pytest.approx(scalar[0])
        assert vec[1] == pytest.approx(scalar[1])

    def test_matches_scalar_random(self):
        """Vectorized result matches scalar on random walk data."""
        rng = np.random.default_rng(42)
        data = np.cumsum(rng.normal(0.5, 2.0, 500)).astype(np.float64)
        scalar = calculate_max_drawdown(data, len(data))
        vec = calculate_max_drawdown_vectorized(data)
        assert vec[0] == pytest.approx(scalar[0], rel=1e-10)
        assert vec[1] == pytest.approx(scalar[1], rel=1e-10)

    def test_empty(self):
        data = _arr([])
        assert calculate_max_drawdown_vectorized(data) == (0.0, 0.0)

    def test_monotonic_increase(self):
        data = _arr([1.0, 2.0, 3.0, 4.0])
        dd, dd_pct = calculate_max_drawdown_vectorized(data)
        assert dd == 0.0
        assert dd_pct == 0.0

    def test_augmented_vs_pure_python(self):
        """Augmented and pure-python vectorized produce identical results."""
        rng = np.random.default_rng(77)
        data = np.cumsum(rng.normal(0.0, 3.0, 300)).astype(np.float64)
        aug = calculate_max_drawdown_vectorized(data)
        pp = pp_calculate_max_drawdown_vectorized(data)
        assert aug[0] == pytest.approx(pp[0], rel=1e-10)
        assert aug[1] == pytest.approx(pp[1], rel=1e-10)
