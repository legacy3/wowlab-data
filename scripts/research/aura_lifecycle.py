#!/usr/bin/env python3
"""Aura lifecycle archaeology oracle.  See docs/research/aura-lifecycle-archaeology.md."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from aura_lifecycle.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
