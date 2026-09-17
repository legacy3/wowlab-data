#!/usr/bin/env python3
"""Research CLI: `python3 character_prep.py --help` (see character_prep/cli.py)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from character_prep.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
