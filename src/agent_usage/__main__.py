"""Allow running `python3 -m agent_usage` directly."""

import sys
from .cli import main

if __name__ == "__main__":
    sys.exit(main())
