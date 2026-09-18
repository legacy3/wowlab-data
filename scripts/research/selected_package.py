#!/usr/bin/env python3
"""Selected-passive package generalization oracle.

See docs/research/selected-passive-package-generalization.md.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from selected_package.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
