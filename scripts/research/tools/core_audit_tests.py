#!/usr/bin/env python3
"""Run every Core-audit test: the Python registry/corpus tests and the Rust probe crate.

Usage (from ``scripts/research``)::

    python3 tools/core_audit_tests.py

Needs ``uv`` (pytest is not installed for system python) and the pinned Rust toolchain.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

RESEARCH = Path(__file__).resolve().parents[1]
PROBE = RESEARCH / "core_audit" / "probe"


def main() -> int:
    pytest = subprocess.run(
        [
            "uv", "run", "--no-project", "--with", "pytest==8.4.2", "python", "-m", "pytest",
            "tests/test_ca_registry.py", "-q", "-p", "no:cacheprovider",
        ],
        cwd=RESEARCH,
    )
    env = {**os.environ, "CARGO_TARGET_DIR": str(PROBE / "target")}
    cargo = subprocess.run(["cargo", "test", "--offline", "--quiet"], cwd=PROBE, env=env)
    return 1 if pytest.returncode or cargo.returncode else 0


if __name__ == "__main__":
    sys.exit(main())
