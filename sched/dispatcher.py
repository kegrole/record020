"""Event-driven multi-site request dispatcher.

Scheduling model
----------------
- The total budget is split up-front into per-site target shares,
  proportional to each site's quota rate (largest-remainder rounding,
  ties broken by priority then name). When the budget allows it, every
  site is guaranteed a share of at least one request (anti-starvation).
- Each simulated request occupies one global and one per-site concurrency
  slot for the site's configured duration; completions are processed from
  an event heap as the simulated clock advances.
- A site takes part in a scheduling round only when: it has remaining
  share, its window is open, it is not in cooldown, and both concurrency
  caps have room.
- Among eligible sites that hold at least one token, the pick is made by
  smooth weighted round-robin with the priority as weight, so higher
  priority sites are scheduled more often while every ready site keeps
  moving.
- If no eligible site holds a token, the clock jumps to the next event
  (token refill, window opening, cooldown end, or request completion).
  Requests wait for tokens; they never fail because of rate limiting.
- If a site's remaining share can never be served (its window closed for
  good), the share is redistributed to sites that can still run.
"""

import heapq
import math
from typing import Callable, List, Optional, Tuple

from .clock import SimClock
from .config import SchedConfig, SiteConfig
from .cooldown import CooldownTracker
from .ratelimit import TokenBucket

#: should_fail(site_name, request_index_for_site) -> True if the request fails
FailureHook = Callable[[str, int], bool]


class SiteState:
    """Mutable per-site runtime state used by the dispatcher."""

    def __init__(self, cfg: SiteConfig):
        self.cfg = cfg
        self.name = cfg.name
        self.weight = cfg.priority
        self.bucket = TokenBucket(cfg.quota, cfg.period_ms)
        self.cooldown = CooldownTracker(cfg.backoff_threshold, cfg.backoff_cooldown_ms)
        self.remaining = 0
        self.sent = 0
        self.in_flight = 0
        self.peak_concurrency = 0
        self.waited_ms = 0.0
        self.skipped_for_cooldown = 0
        self.failures = 0
        self.enqueue_time = 0.0
        self.smooth_weight = 0.0

    @property
    def window(self):
        return self.cfg.window


def allocate_shares(sites: List[SiteState], budget: int) -> List[int]:
    """Split 'budget' proportionally to each site's quota rate.

    Uses largest-remainder rounding; ties are broken by higher priority,
    then by name. Every site gets at least one request when budget >=
    number of sites, so no site can be starved by rounding.
    """
    rates = [s.cfg.rate_per_ms for s in sites]
    total_rate = sum(rates)
    raw = [budget * r / total_rate for r in rates]
    shares = [int(math.floor(x)) for x in raw]
    leftover = budget - sum(shares)
    order = sorted(
        range(len(sites)),
        key=lambda i: (-(raw[i] - shares[i]), -sites[i].cfg.priority, sites[i].name),
    )
    for i in order[:leftover]:
        shares[i] += 1
    if budget >= len(sites):
        for i in range(len(sites)):
            if shares[i] == 0:
                donor = max(range(len(sites)), key=lambda k: shares[k])
                if shares[donor] > 1:
                    shares[donor] -= 1
                    shares[i] = 1
    return shares


class Dispatcher:
    def __init__(
        self,
        config: SchedConfig,
        clock: Optional[SimClock] = None,
        should_fail: Optional[FailureHook] = None,
    ):
        self.config = config
        self.clock = clock if clock is not None else SimClock()
        self.should_fail = should_fail
        self.sites = [SiteState(c) for c in config.sites]
        self.global_in_flight = 0
        self.global_peak_concurrency = 0
        #: chronological dispatch log of (site_name, dispatch_time_ms)
        self.log: List[Tuple[str, float]] = []
        self._completions: List[Tuple[float, int, SiteState, int]] = []
        self._seq = 0

    # ------------------------------------------------------------------ run

    def run(self, budget: int) -> int:
        """Dispatch up to 'budget' requests; returns the number sent."""
        if budget < 0:
            raise ValueError("budget must be >= 0")
        shares = allocate_shares(self.sites, budget)
        for site, share in zip(self.sites, shares):
            site.remaining = share

        sent = 0
        while sent < budget:
            self._process_completions()
            now = self.clock.now()
            global_ok = self.global_in_flight < self.config.global_max_concurrency

            candidates = []
            for site in self.sites:
                if site.remaining <= 0:
                    continue
                if not global_ok or site.in_flight >= site.cfg.max_concurrency:
                    continue
                if not site.window.is_open(now):
                    continue
                if site.cooldown.active(now):
                    site.skipped_for_cooldown += 1
                    continue
                candidates.append(site)

            ready = [s for s in candidates if s.bucket.available(now) >= 1.0]
            if ready:
                self._dispatch(self._pick(ready))
                sent += 1
                continue

            next_times = [s.bucket.next_ready(now) for s in candidates]
            for site in self.sites:
                if site.remaining <= 0:
                    continue
                if site.cooldown.active(now):
                    next_times.append(site.cooldown.until)
                elif not site.window.is_open(now):
                    nxt = site.window.next_open(now)
                    if nxt is not None:
                        next_times.append(nxt)
            if self._completions:
                next_times.append(self._completions[0][0])

            future = [t for t in next_times if t > now]
            if not future:
                if not self._redistribute():
                    break
                continue
            self.clock.advance_to(max(min(future), now + 1e-9))

        self._process_completions()
        return sent

    # -------------------------------------------------------------- helpers

    def _pick(self, ready: List[SiteState]) -> SiteState:
        """Smooth weighted round-robin over the ready sites."""
        total_weight = sum(s.weight for s in ready)
        best = None
        for site in ready:
            site.smooth_weight += site.weight
            if best is None or site.smooth_weight > best.smooth_weight:
                best = site
        best.smooth_weight -= total_weight
        return best

    def _dispatch(self, site: SiteState) -> None:
        now = self.clock.now()
        site.bucket.consume(now)
        index = site.sent
        site.sent += 1
        site.remaining -= 1
        site.in_flight += 1
        self.global_in_flight += 1
        site.peak_concurrency = max(site.peak_concurrency, site.in_flight)
        self.global_peak_concurrency = max(
            self.global_peak_concurrency, self.global_in_flight
        )
        site.waited_ms += now - site.enqueue_time
        site.enqueue_time = now
        self.log.append((site.name, now))
        self._seq += 1
        heapq.heappush(
            self._completions,
            (now + site.cfg.duration_ms, self._seq, site, index),
        )

    def _process_completions(self) -> None:
        now = self.clock.now()
        while self._completions and self._completions[0][0] <= now:
            finished_at, _, site, index = heapq.heappop(self._completions)
            site.in_flight -= 1
            self.global_in_flight -= 1
            failed = (
                self.should_fail(site.name, index) if self.should_fail else False
            )
            if failed:
                site.failures += 1
                site.cooldown.on_failure(finished_at)
            else:
                site.cooldown.on_success()

    def _redistribute(self) -> bool:
        """Move unreachable remaining shares to sites that can still run.

        Returns False when nothing can ever be scheduled again.
        """
        now = self.clock.now()
        hopeless, runnable = [], []
        for site in self.sites:
            can_run_later = site.window.is_open(now) or (
                site.window.next_open(now) is not None
            )
            if site.remaining > 0 and not can_run_later:
                hopeless.append(site)
            if can_run_later:
                runnable.append(site)
        if not hopeless or not runnable:
            return False
        extra = sum(s.remaining for s in hopeless)
        for site in hopeless:
            site.remaining = 0
        total_rate = sum(s.cfg.rate_per_ms for s in runnable)
        granted = 0
        for site in runnable[:-1]:
            share = int(extra * site.cfg.rate_per_ms / total_rate)
            site.remaining += share
            granted += share
        runnable[-1].remaining += extra - granted
        return True

