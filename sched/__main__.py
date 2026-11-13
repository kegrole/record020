"""Allow running as 'python -m sched' as well as 'python -m sched.cli'."""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())

