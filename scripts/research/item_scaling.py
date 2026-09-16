#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Entry point for the gearing research package.

The package under ``scripts/research/gearing/`` is standard-library only, so
this runs identically under ``uv run --script`` and plain ``python3``.

    python3 scripts/research/item_scaling.py --help
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gearing.cli import run  # noqa: E402

if __name__ == "__main__":
    sys.exit(run())
