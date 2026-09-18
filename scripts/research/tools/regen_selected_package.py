#!/usr/bin/env python3
"""Regenerate every corpus of the selected-passive package pass, in dependency order.

Stages (select with ``--stage``; default: all):

  lead      the oracle's own corpora (census, authorities, core-admitted, owner-policy
            families, falsification ledger, false positives/negatives, unknowns)
  tracks    the per-track corpora whose modules own them (rank provenance, owner policy,
            proc policy, topology, sole authorities)

``--check`` hashes every file of ``docs/research/selected-package-corpora`` before and
after and exits 1 if anything changed: the committed corpora are exactly what the
committed code produces.

Usage (from ``scripts/research``)::

    python3 tools/regen_selected_package.py            # everything
    python3 tools/regen_selected_package.py --check    # everything, then compare
    python3 tools/regen_selected_package.py --stage lead --check
"""

from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
import time
from pathlib import Path

RESEARCH = Path(__file__).resolve().parents[1]
ROOT = RESEARCH.parents[1]
CORPORA = ROOT / "docs" / "research" / "selected-package-corpora"
PY = sys.executable

#: Modules that own their corpora and expose a ``__main__``.
TRACK_MODULES = ["selected_package.rankprov"]

#: Track corpora built through a driver script instead (their modules are libraries).
TRACK_SCRIPTS = [
    ("tools/gen_proc_policy_build.py", "proc-policy corpus"),
    ("tools/gen_proc_policy_ce.py", "proc counterexamples"),
    ("tools/gen_track_corpora.py", "topology / sidecars / sole authorities"),
]


def digest() -> dict[str, str]:
    return {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(CORPORA.glob("*.json"))}


def run(command: list[str], label: str) -> int:
    started = time.monotonic()
    print(f"==> {label}", flush=True)
    result = subprocess.run(command, cwd=RESEARCH)
    print(f"    {'ok' if result.returncode == 0 else 'FAILED'} "
          f"({time.monotonic() - started:.1f}s)", flush=True)
    return result.returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--stage", action="append", choices=("lead", "tracks"))
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    stages = args.stage or ["lead", "tracks"]

    before = digest() if args.check else {}
    failures = 0

    if "lead" in stages:
        failures += bool(run([PY, "selected_package.py", "corpora"], "oracle corpora"))

    if "tracks" in stages:
        for module in TRACK_MODULES:
            module_path = RESEARCH / Path(*module.split(".")).with_suffix(".py")
            if not module_path.exists():
                print(f"==> {module}\n    SKIPPED (module absent)", flush=True)
                continue
            failures += bool(run([PY, "-m", module], module))
        for script, label in TRACK_SCRIPTS:
            if not (RESEARCH / script).exists():
                print(f"==> {label}\n    SKIPPED (script absent)", flush=True)
                continue
            failures += bool(run([PY, script], label))

    if args.check:
        after = digest()
        changed = sorted(set(before) | set(after))
        drift = [name for name in changed if before.get(name) != after.get(name)]
        print(f"\n{len(after)} corpus files")
        if drift:
            print("CHANGED:")
            for name in drift:
                print(f"  {name}: {before.get(name, '(absent)')[:12]} -> "
                      f"{after.get(name, '(absent)')[:12]}")
            return 1
        print("byte-stable: every corpus matches what the committed code produces")

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
