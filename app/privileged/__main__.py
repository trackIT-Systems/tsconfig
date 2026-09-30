"""Entry point for `python3 -I -m app.privileged <op> ...`."""

import sys

from app.privileged.cli import main

if __name__ == "__main__":
    sys.exit(main())
