#!/usr/bin/env python3
"""Regenerate every corpus of the Core semantic boundary audit.

``--check`` hashes ``docs/research/core-audit-corpora`` before and after and exits 1
on any drift: the committed corpora are exactly what the committed registry and
the pinned Core tree produce. Fails closed if Core is not at the audited pin.

Usage (from ``scripts/research``)::

    python3 tools/regen_core_audit.py            # regenerate
    python3 tools/regen_core_audit.py --check    # regenerate, then fail on drift
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

RESEARCH = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RESEARCH))

from core_audit import CORPORA  # noqa: E402
from core_audit.corpora import build, write  # noqa: E402


def digest() -> dict[str, str]:
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(CORPORA.glob("*.json"))}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)

    before = digest() if args.check else {}
    files = build()
    if build() != files:
        print("NONDETERMINISTIC: two consecutive builds differ")
        return 1
    write(files)
    after = digest()
    print(f"{len(after)} corpus files")
    if not args.check:
        return 0
    drift = [n for n in sorted(set(before) | set(after)) if before.get(n) != after.get(n)]
    if drift:
        print("CHANGED:")
        for name in drift:
            print(f"  {name}: {before.get(name, '(absent)')[:12]} -> {after.get(name, '(absent)')[:12]}")
        return 1
    print("byte-stable: every corpus matches the committed registry and pinned Core")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
