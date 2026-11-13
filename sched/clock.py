"""Injectable simulated clock.

The scheduler never sleeps in real time; every wait is performed by
advancing this clock, which makes runs fully deterministic and fast.
"""


class SimClock:
    """A monotonic clock measured in milliseconds, advanced manually."""

    def __init__(self, start_ms: float = 0.0):
        self._now = float(start_ms)

    def now(self) -> float:
        """Current simulated time in milliseconds."""
        return self._now

    def advance_to(self, target_ms: float) -> float:
        """Jump forward to target_ms. Refuses to go backwards."""
        if target_ms < self._now:
            raise ValueError(
                "cannot move clock backwards: now=%r target=%r" % (self._now, target_ms)
            )
        self._now = float(target_ms)
        return self._now

    def advance(self, delta_ms: float) -> float:
        """Move forward by delta_ms milliseconds."""
        if delta_ms < 0:
            raise ValueError("delta_ms must be >= 0")
        return self.advance_to(self._now + delta_ms)

