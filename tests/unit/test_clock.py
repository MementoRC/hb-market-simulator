"""Tests for SimulatedClock."""

import pytest
from market_simulator.core.clock import SimulatedClock


class TestSimulatedClock:
    def test_initial_state(self):
        clock = SimulatedClock(start_time=1000.0, tick_size=0.5)
        assert clock.current_time == 1000.0
        assert clock.tick_size == 0.5
        assert clock.tick_count == 0

    def test_advance_default(self):
        clock = SimulatedClock(start_time=0.0, tick_size=1.0)
        new_time = clock.advance()
        assert new_time == 1.0
        assert clock.current_time == 1.0
        assert clock.tick_count == 1

    def test_advance_custom(self):
        clock = SimulatedClock(start_time=100.0)
        clock.advance(seconds=5.0)
        assert clock.current_time == 105.0

    def test_advance_multiple(self):
        clock = SimulatedClock(tick_size=2.0)
        clock.advance()
        clock.advance()
        clock.advance()
        assert clock.current_time == 6.0
        assert clock.tick_count == 3

    def test_advance_negative_raises(self):
        clock = SimulatedClock()
        with pytest.raises(ValueError, match="negative"):
            clock.advance(seconds=-1.0)

    def test_set_time(self):
        clock = SimulatedClock(start_time=10.0)
        clock.set_time(50.0)
        assert clock.current_time == 50.0

    def test_set_time_backwards_raises(self):
        clock = SimulatedClock(start_time=10.0)
        with pytest.raises(ValueError, match="backwards"):
            clock.set_time(5.0)

    def test_reset(self):
        clock = SimulatedClock(start_time=100.0)
        clock.advance()
        clock.advance()
        clock.reset(start_time=0.0)
        assert clock.current_time == 0.0
        assert clock.tick_count == 0
