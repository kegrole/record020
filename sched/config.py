"""Site / scheduler configuration loading and validation.

JSON schema:

{
  "global_max_concurrency": 4,
  "sites": [
    {
      "name": "A",
      "quota": 60,                     # requests allowed per period
      "period": "minute",              # "second" | "minute" | "hour"
      "priority": 3,                   # integer >= 1, higher = scheduled more often
      "max_concurrency": 2,            # per-site in-flight cap
      "duration_ms": 40,               # simulated request duration
      "window": {"start_s": 0, "end_s": 600},   # optional; omitted = always open
      "backoff": {"threshold": 3, "cooldown_s": 30}
    }
  ]
}
"""

import json
from dataclasses import dataclass, field
from typing import List, Optional

from .window import TimeWindow

PERIODS_MS = {
    "second": 1000.0,
    "minute": 60_000.0,
    "hour": 3_600_000.0,
}

DEFAULT_BACKOFF_THRESHOLD = 5
DEFAULT_BACKOFF_COOLDOWN_S = 10.0
DEFAULT_DURATION_MS = 50.0


@dataclass
class SiteConfig:
    name: str
    quota: int
    period_ms: float
    priority: int = 1
    max_concurrency: int = 1
    duration_ms: float = DEFAULT_DURATION_MS
    window: TimeWindow = field(default_factory=TimeWindow)
    backoff_threshold: int = DEFAULT_BACKOFF_THRESHOLD
    backoff_cooldown_ms: float = DEFAULT_BACKOFF_COOLDOWN_S * 1000.0

    @property
    def rate_per_ms(self) -> float:
        """Sustained request rate in requests per millisecond."""
        return self.quota / self.period_ms


@dataclass
class SchedConfig:
    sites: List[SiteConfig]
    global_max_concurrency: int = 4


def _parse_window(data: Optional[dict]) -> TimeWindow:
    if data is None:
        return TimeWindow()
    start_s = float(data.get("start_s", 0.0))
    end_s = data.get("end_s")
    return TimeWindow(
        start_ms=start_s * 1000.0,
        end_ms=None if end_s is None else float(end_s) * 1000.0,
    )


def _parse_site(data: dict) -> SiteConfig:
    name = data.get("name")
    if not name:
        raise ValueError("every site needs a non-empty 'name'")

    quota = data.get("quota")
    if not isinstance(quota, int) or quota <= 0:
        raise ValueError("site %r: 'quota' must be a positive integer" % name)

    period = data.get("period", "minute")
    if period not in PERIODS_MS:
        raise ValueError(
            "site %r: 'period' must be one of %s" % (name, sorted(PERIODS_MS))
        )

    priority = data.get("priority", 1)
    if not isinstance(priority, int) or priority < 1:
        raise ValueError("site %r: 'priority' must be an integer >= 1" % name)

    max_concurrency = data.get("max_concurrency", 1)
    if not isinstance(max_concurrency, int) or max_concurrency < 1:
        raise ValueError("site %r: 'max_concurrency' must be an integer >= 1" % name)

    duration_ms = float(data.get("duration_ms", DEFAULT_DURATION_MS))
    if duration_ms < 0:
        raise ValueError("site %r: 'duration_ms' must be >= 0" % name)

    backoff = data.get("backoff", {}) or {}
    threshold = backoff.get("threshold", DEFAULT_BACKOFF_THRESHOLD)
    cooldown_s = float(backoff.get("cooldown_s", DEFAULT_BACKOFF_COOLDOWN_S))

    return SiteConfig(
        name=name,
        quota=quota,
        period_ms=PERIODS_MS[period],
        priority=priority,
        max_concurrency=max_concurrency,
        duration_ms=duration_ms,
        window=_parse_window(data.get("window")),
        backoff_threshold=int(threshold),
        backoff_cooldown_ms=cooldown_s * 1000.0,
    )


def parse_config(data: dict) -> SchedConfig:
    """Build a SchedConfig from an already-decoded JSON dict."""
    if not isinstance(data, dict):
        raise ValueError("config root must be a JSON object")
    raw_sites = data.get("sites")
    if not isinstance(raw_sites, list) or not raw_sites:
        raise ValueError("config must contain a non-empty 'sites' list")

    sites = [_parse_site(s) for s in raw_sites]
    names = [s.name for s in sites]
    if len(set(names)) != len(names):
        raise ValueError("site names must be unique: %r" % names)

    global_max = data.get("global_max_concurrency", 4)
    if not isinstance(global_max, int) or global_max < 1:
        raise ValueError("'global_max_concurrency' must be an integer >= 1")

    return SchedConfig(sites=sites, global_max_concurrency=global_max)


def load_config(path: str) -> SchedConfig:
    """Load and validate a JSON config file."""
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    return parse_config(data)

