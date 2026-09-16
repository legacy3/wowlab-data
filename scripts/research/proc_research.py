#!/usr/bin/env python3
"""Proc-system archaeology oracle.  See docs/research/proc-pipeline-archaeology.md."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from procs.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
