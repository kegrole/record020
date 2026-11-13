"""Token-bucket rate limiter, one bucket per site."""


class TokenBucket:
    """Continuously refilled token bucket.

    - Capacity equals one period's quota, so a full bucket allows a burst
      of at most 'capacity' requests.
    - Tokens refill linearly with simulated time: rate = capacity / period.
    - When the bucket is empty, callers are expected to wait until
      next_ready() instead of failing the request.
    """

    def __init__(self, capacity: float, period_ms: float):
        if capacity <= 0:
            raise ValueError("bucket capacity must be > 0")
        if period_ms <= 0:
            raise ValueError("bucket period must be > 0")
        self.capacity = float(capacity)
        self.period_ms = float(period_ms)
        self.rate_per_ms = self.capacity / self.period_ms
        self.tokens = float(capacity)
        self._last_refill = 0.0

    def _refill(self, now_ms: float) -> None:
        if now_ms > self._last_refill:
            elapsed = now_ms - self._last_refill
            self.tokens = min(self.capacity, self.tokens + self.rate_per_ms * elapsed)
            self._last_refill = now_ms

    def available(self, now_ms: float) -> float:
        """Tokens available at now_ms (refills lazily)."""
        self._refill(now_ms)
        return self.tokens

    def consume(self, now_ms: float, amount: float = 1.0) -> None:
        """Take 'amount' tokens. Caller must ensure availability."""
        self._refill(now_ms)
        if self.tokens < amount - 1e-9:
            raise RuntimeError("consume called on an empty bucket")
        self.tokens = max(0.0, self.tokens - amount)

    def next_ready(self, now_ms: float) -> float:
        """Absolute time at which at least one token will be available."""
        self._refill(now_ms)
        if self.tokens >= 1.0:
            return now_ms
        missing = 1.0 - self.tokens
        return now_ms + missing / self.rate_per_ms

