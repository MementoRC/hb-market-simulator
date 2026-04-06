"""Deterministic simulated clock for market simulation."""

from __future__ import annotations


class SimulatedClock:
    """A clock that advances only when explicitly told to.

    Used to drive the simulation loop with deterministic timestamps.
    """

    def __init__(self, start_time: float = 0.0, tick_size: float = 1.0):
        self._current_time = start_time
        self._tick_size = tick_size
        self._tick_count = 0

    @property
    def current_time(self) -> float:
        return self._current_time

    @property
    def tick_size(self) -> float:
        return self._tick_size

    @property
    def tick_count(self) -> int:
        return self._tick_count

    def advance(self, seconds: float | None = None) -> float:
        """Advance the clock by the given number of seconds (default: tick_size).

        Returns the new current time.
        """
        delta = seconds if seconds is not None else self._tick_size
        if delta < 0:
            raise ValueError(f"Cannot advance clock by negative amount: {delta}")
        self._current_time += delta
        self._tick_count += 1
        return self._current_time

    def set_time(self, timestamp: float) -> None:
        """Jump to a specific timestamp (must be >= current)."""
        if timestamp < self._current_time:
            raise ValueError(f"Cannot set clock backwards: {timestamp} < {self._current_time}")
        self._current_time = timestamp

    def reset(self, start_time: float = 0.0) -> None:
        """Reset the clock to initial state."""
        self._current_time = start_time
        self._tick_count = 0
