"""Failure backoff / cooldown tracking per site."""


class CooldownTracker:
    """Trips a site into cooldown after 'threshold' consecutive failures.

    While cooling down the site is skipped by the scheduler, but its token
    bucket keeps refilling (the bucket is time-based and independent).
    """

    def __init__(self, threshold: int, cooldown_ms: float):
        if threshold < 1:
            raise ValueError("cooldown threshold must be >= 1")
        if cooldown_ms < 0:
            raise ValueError("cooldown must be >= 0")
        self.threshold = int(threshold)
        self.cooldown_ms = float(cooldown_ms)
        self.consecutive_failures = 0
        self.until = 0.0
        self.times_triggered = 0

    def active(self, now_ms: float) -> bool:
        return now_ms < self.until

    def on_success(self) -> None:
        self.consecutive_failures = 0

    def on_failure(self, now_ms: float) -> bool:
        """Record a failure; returns True if cooldown was (re)triggered."""
        self.consecutive_failures += 1
        if self.consecutive_failures >= self.threshold:
            self.until = now_ms + self.cooldown_ms
            self.consecutive_failures = 0
            self.times_triggered += 1
            return True
        return False

