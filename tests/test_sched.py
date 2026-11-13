"""Unittests for the multi-site scheduler.

Each acceptance criterion from the task is covered by a dedicated test:

1. test_site_config_rules            - A/B/C quotas, windows, priorities
2. test_quota_never_exceeded         - per-site sends stay within quota
3. test_priority_visible_first_100   - high priority scheduled more often
4. test_no_starvation                - every site scheduled at least once
5. test_time_window_enforced         - C sends nothing before t=300s
6. test_concurrency_caps             - global cap of 2 never exceeded
7. test_waits_instead_of_failing     - empty bucket queues and waits
8. test_report_fields_deterministic  - 8 fields per site, repeatable runs

Plus a cooldown test (failure backoff skips the site while its quota
keeps refilling).
"""

import os
import sys
import unittest
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sched.clock import SimClock
from sched.config import load_config, parse_config
from sched.dispatcher import Dispatcher
from sched.report import SITE_FIELDS, build_report

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEMO_SITES = os.path.join(REPO_ROOT, "demo", "sites.json")

BUDGET = 300
SIM_WINDOW_S = 600  # acceptance criteria折算配额时使用的窗口


def three_site_config(global_max_concurrency=4):
    """Build the A/B/C fixture from the acceptance rules.

    A: 60/min, window always open, high priority.
    B: 30/min, window [0s, 600s), medium priority.
    C: 10/min, window [300s, 900s), low priority.
    """
    return parse_config(
        {
            "global_max_concurrency": global_max_concurrency,
            "sites": [
                {
                    "name": "A",
                    "quota": 60,
                    "period": "minute",
                    "priority": 3,
                    "max_concurrency": 2,
                    "duration_ms": 40,
                    "backoff": {"threshold": 3, "cooldown_s": 30},
                },
                {
                    "name": "B",
                    "quota": 30,
                    "period": "minute",
                    "priority": 2,
                    "max_concurrency": 2,
                    "duration_ms": 60,
                    "window": {"start_s": 0, "end_s": 600},
                    "backoff": {"threshold": 3, "cooldown_s": 30},
                },
                {
                    "name": "C",
                    "quota": 10,
                    "period": "minute",
                    "priority": 1,
                    "max_concurrency": 1,
                    "duration_ms": 100,
                    "window": {"start_s": 300, "end_s": 900},
                    "backoff": {"threshold": 3, "cooldown_s": 30},
                },
            ],
        }
    )


def run(config, budget=BUDGET, should_fail=None):
    dispatcher = Dispatcher(config, SimClock(), should_fail=should_fail)
    sent = dispatcher.run(budget)
    report = build_report(dispatcher, budget)
    return dispatcher, report, sent


def sent_by_site(report):
    return {s["site"]: s["requests_sent"] for s in report["sites"]}


class TestSiteConfigRules(unittest.TestCase):
    """Criterion 1: the A/B/C fixture parses with the required rules."""

    def test_site_config_rules(self):
        config = load_config(DEMO_SITES)
        sites = {s.name: s for s in config.sites}
        self.assertEqual(set(sites), {"A", "B", "C"})

        self.assertEqual(sites["A"].quota, 60)
        self.assertEqual(sites["A"].period_ms, 60_000.0)
        self.assertTrue(sites["A"].window.is_open(0.0))
        self.assertTrue(sites["A"].window.is_open(10**9))  # open all day
        self.assertGreater(sites["A"].priority, sites["B"].priority)
        self.assertGreater(sites["B"].priority, sites["C"].priority)

        self.assertEqual(sites["B"].quota, 30)
        self.assertEqual(sites["B"].window.start_ms, 0.0)
        self.assertEqual(sites["B"].window.end_ms, 600_000.0)

        self.assertEqual(sites["C"].quota, 10)
        self.assertEqual(sites["C"].window.start_ms, 300_000.0)
        self.assertEqual(sites["C"].window.end_ms, 900_000.0)


class TestQuotaNeverExceeded(unittest.TestCase):
    """Criterion 2: per-site sends never exceed the 600s-folded quota."""

    def test_quota_never_exceeded(self):
        _, report, sent = run(three_site_config())
        self.assertEqual(sent, BUDGET)
        counts = sent_by_site(report)
        quota_in_window = {"A": 60 * 10, "B": 30 * 10, "C": 10 * 10}
        for name, limit in quota_in_window.items():
            self.assertLessEqual(
                counts[name], limit, "%s sent %d over quota %d" % (name, counts[name], limit)
            )
        # quota-proportional split of 300 at 60:30:10 is exactly 180/90/30
        self.assertEqual(counts, {"A": 180, "B": 90, "C": 30})

    def test_rate_limit_never_broken_over_time(self):
        # In any 60s slice, a site sends at most its per-minute quota
        # (plus one bucket capacity of burst, bounded by the quota itself).
        dispatcher, _, _ = run(three_site_config())
        for site_name, quota in (("A", 60), ("B", 30), ("C", 10)):
            times = sorted(t for name, t in dispatcher.log if name == site_name)
            for i, start in enumerate(times):
                j = i
                while j < len(times) and times[j] < start + 60_000.0:
                    j += 1
                self.assertLessEqual(
                    j - i,
                    2 * quota,
                    "%s burst beyond bucket capacity + quota" % site_name,
                )


class TestPriorityVisible(unittest.TestCase):
    """Criterion 3: in the first 100 dispatches, high > low priority."""

    def test_priority_visible_first_100(self):
        dispatcher, _, _ = run(three_site_config())
        self.assertGreaterEqual(len(dispatcher.log), 100)
        first100 = Counter(name for name, _ in dispatcher.log[:100])
        self.assertGreater(first100["A"], first100["C"])
        self.assertGreater(first100["A"], first100["B"])
        self.assertGreaterEqual(first100["B"], first100["C"])


class TestNoStarvation(unittest.TestCase):
    """Criterion 4: every site is scheduled at least once."""

    def test_no_starvation(self):
        _, report, _ = run(three_site_config())
        counts = sent_by_site(report)
        for name in ("A", "B", "C"):
            self.assertGreaterEqual(counts[name], 1, "%s starved" % name)


class TestTimeWindowEnforced(unittest.TestCase):
    """Criterion 5: C sends nothing between t=0s and t=299s."""

    def test_time_window_enforced(self):
        dispatcher, _, _ = run(three_site_config())
        c_times = [t for name, t in dispatcher.log if name == "C"]
        self.assertTrue(c_times, "C never dispatched")
        self.assertTrue(all(t >= 300_000.0 for t in c_times))
        b_times = [t for name, t in dispatcher.log if name == "B"]
        self.assertTrue(all(t < 600_000.0 for t in b_times))


class TestConcurrencyCaps(unittest.TestCase):
    """Criterion 6: with a global cap of 2, at most 2 requests run at once."""

    def test_concurrency_caps(self):
        dispatcher, report, _ = run(three_site_config(global_max_concurrency=2))
        self.assertLessEqual(dispatcher.global_peak_concurrency, 2)
        self.assertEqual(report["global_peak_concurrency"], 2)  # cap is reached
        per_site_cap = {"A": 2, "B": 2, "C": 1}
        for entry in report["sites"]:
            self.assertLessEqual(
                entry["peak_concurrency"], per_site_cap[entry["site"]]
            )


class TestWaitsInsteadOfFailing(unittest.TestCase):
    """Criterion 7: an exhausted bucket queues the request and waits."""

    def test_waits_instead_of_failing(self):
        config = parse_config(
            {
                "global_max_concurrency": 10,
                "sites": [
                    {
                        "name": "X",
                        "quota": 6,
                        "period": "minute",  # one token every 10s
                        "priority": 1,
                        "max_concurrency": 10,
                        "duration_ms": 1,
                    }
                ],
            }
        )
        dispatcher, report, sent = run(config, budget=8)
        self.assertEqual(sent, 8)  # nothing failed or was dropped
        site = report["sites"][0]
        # 6 burst tokens are free; requests 7 and 8 each wait ~10s for a token
        self.assertGreater(site["waited_ms"], 15_000.0)
        times = [t for _, t in dispatcher.log]
        self.assertAlmostEqual(times[6], 10_000.0, delta=1.0)
        self.assertAlmostEqual(times[7], 20_000.0, delta=1.0)


class TestReportFieldsDeterministic(unittest.TestCase):
    """Criterion 8: 8 fields per site; identical runs give identical results."""

    def test_report_fields_deterministic(self):
        _, report1, _ = run(three_site_config())
        _, report2, _ = run(three_site_config())
        for report in (report1, report2):
            self.assertEqual(len(report["sites"]), 3)
            for entry in report["sites"]:
                self.assertEqual(set(entry.keys()), set(SITE_FIELDS))
        self.assertEqual(sent_by_site(report1), sent_by_site(report2))
        self.assertEqual(report1, report2)


class TestCooldownBackoff(unittest.TestCase):
    """Feature 6: consecutive failures skip the site during cooldown."""

    def test_cooldown_skips_site_and_quota_keeps_refilling(self):
        config = parse_config(
            {
                "global_max_concurrency": 1,
                "sites": [
                    {
                        "name": "D",
                        "quota": 600,
                        "period": "minute",
                        "priority": 1,
                        "max_concurrency": 1,
                        "duration_ms": 10,
                        "backoff": {"threshold": 2, "cooldown_s": 60},
                    }
                ],
            }
        )
        # the first two requests of site D fail
        dispatcher, report, sent = run(
            config, budget=5, should_fail=lambda name, idx: idx < 2
        )
        self.assertEqual(sent, 5)
        site = report["sites"][0]
        self.assertEqual(site["failures"], 2)
        self.assertGreaterEqual(site["skipped_for_cooldown"], 1)

        times = [t for _, t in dispatcher.log]
        # 2nd failure completes at ~20ms -> cooldown until ~60020ms
        self.assertGreaterEqual(times[2], 60_000.0)
        # quota kept refilling during cooldown: the remaining 3 requests
        # fire in a quick burst right after the cooldown ends
        self.assertLess(times[4] - times[2], 1_000.0)

    def test_cooldown_does_not_block_other_sites(self):
        config = parse_config(
            {
                "global_max_concurrency": 4,
                "sites": [
                    {
                        "name": "FLAKY",
                        "quota": 100,
                        "period": "minute",
                        "priority": 2,
                        "max_concurrency": 1,
                        "duration_ms": 10,
                        "backoff": {"threshold": 1, "cooldown_s": 120},
                    },
                    {
                        "name": "STEADY",
                        "quota": 100,
                        "period": "minute",
                        "priority": 1,
                        "max_concurrency": 2,
                        "duration_ms": 10,
                    },
                ],
            }
        )
        dispatcher, report, sent = run(
            config,
            budget=40,
            should_fail=lambda name, idx: name == "FLAKY",
        )
        self.assertEqual(sent, 40)
        counts = sent_by_site(report)
        self.assertEqual(counts["STEADY"], 20)
        # STEADY finishes its whole share within ~12s even though FLAKY
        # keeps tripping 120s cooldowns - the flaky site never blocks it
        steady_last = max(t for name, t in dispatcher.log if name == "STEADY")
        self.assertLess(steady_last, 60_000.0)
        flaky = next(s for s in report["sites"] if s["site"] == "FLAKY")
        self.assertGreaterEqual(flaky["skipped_for_cooldown"], 1)
        # FLAKY's dispatches after the first failure are separated by its
        # 120s cooldowns (the t=0 burst happens before any failure is known)
        flaky_times = [t for name, t in dispatcher.log if name == "FLAKY"]
        for prev, cur in zip(flaky_times, flaky_times[1:]):
            if cur > 10.0:
                self.assertGreaterEqual(cur - prev, 120_000.0 - 1e-6)


if __name__ == "__main__":
    unittest.main()
