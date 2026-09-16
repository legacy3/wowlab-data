#!/usr/bin/env python3
"""Dummy / server-side spell semantics archaeology oracle.  See docs/research/dummy-server-semantics-archaeology.md."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dummy_semantics.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
