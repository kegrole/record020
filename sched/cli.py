"""Command line entry point.

Usage:
    python -m sched.cli --sites demo/sites.json --budget 300 --out out/report.json
"""

import argparse
import sys

from .clock import SimClock
from .config import load_config
from .dispatcher import Dispatcher
from .report import build_report, write_report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sched.cli",
        description="Multi-site rate-limited request scheduler (simulated clock).",
    )
    parser.add_argument("--sites", required=True, help="path to the sites JSON config")
    parser.add_argument(
        "--budget", type=int, required=True, help="total number of requests to send"
    )
    parser.add_argument("--out", required=True, help="path to write the JSON report")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    config = load_config(args.sites)
    clock = SimClock()
    dispatcher = Dispatcher(config, clock)
    dispatcher.run(args.budget)
    report = build_report(dispatcher, args.budget)
    write_report(report, args.out)

    waited_sites = sum(1 for s in report["sites"] if s["waited_ms"] > 0)
    print(
        "summary: sites=%d requests_sent=%d waited_sites=%d"
        % (len(report["sites"]), report["requests_sent"], waited_sites)
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

