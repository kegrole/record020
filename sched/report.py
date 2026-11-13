"""JSON report building."""

import json
import os
from typing import List

from .dispatcher import Dispatcher

#: fields emitted for every site, in this order
SITE_FIELDS: List[str] = [
    "site",
    "priority",
    "requests_sent",
    "quota_used",
    "waited_ms",
    "skipped_for_cooldown",
    "peak_concurrency",
    "failures",
]


def build_report(dispatcher: Dispatcher, budget: int) -> dict:
    sites = []
    for s in dispatcher.sites:
        sites.append(
            {
                "site": s.name,
                "priority": s.cfg.priority,
                "requests_sent": s.sent,
                "quota_used": s.sent,  # one token consumed per sent request
                "waited_ms": round(s.waited_ms, 3),
                "skipped_for_cooldown": s.skipped_for_cooldown,
                "peak_concurrency": s.peak_concurrency,
                "failures": s.failures,
            }
        )
    return {
        "budget": budget,
        "requests_sent": sum(s["requests_sent"] for s in sites),
        "sim_duration_ms": round(dispatcher.clock.now(), 3),
        "global_peak_concurrency": dispatcher.global_peak_concurrency,
        "sites": sites,
    }


def write_report(report: dict, path: str) -> None:
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
