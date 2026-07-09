"""Entry point: `python -m cqd` or `cqd` (after install)."""

import sys

from cqd.app import run


def main() -> int:
    return run()


if __name__ == "__main__":
    sys.exit(main())
