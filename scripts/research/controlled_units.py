#!/usr/bin/env python3
"""Research CLI: `python3 controlled_units.py --help` (see controlled_units/cli.py)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from controlled_units.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
