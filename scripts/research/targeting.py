#!/usr/bin/env python3
"""Spell targeting / recipient-policy archaeology oracle.  See docs/research/targeting-recipient-policy-archaeology.md."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from targeting.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
