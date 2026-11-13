"""Per-site scheduling time windows (simulated time, milliseconds)."""

from typing import Optional


class TimeWindow:
    """A half-open window [start_ms, end_ms).

    end_ms=None means the window never closes. A site only takes part
    in scheduling rounds while its window is open.
    """

    def __init__(self, start_ms: float = 0.0, end_ms: Optional[float] = None):
        if end_ms is not None and end_ms <= start_ms:
            raise ValueError("window end must be after window start")
        self.start_ms = float(start_ms)
        self.end_ms = None if end_ms is None else float(end_ms)

    def is_open(self, now_ms: float) -> bool:
        if now_ms < self.start_ms:
            return False
        if self.end_ms is not None and now_ms >= self.end_ms:
            return False
        return True

    def next_open(self, now_ms: float) -> Optional[float]:
        """Next time the window opens, or None if it never will again."""
        if now_ms < self.start_ms:
            return self.start_ms
        return None

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "TimeWindow(%r, %r)" % (self.start_ms, self.end_ms)

