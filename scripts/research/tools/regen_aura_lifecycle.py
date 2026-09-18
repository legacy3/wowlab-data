#!/usr/bin/env python3
"""Regenerate every corpus of the aura-lifecycle research pass, in dependency order.

Stages (select with ``--stage``; default: probes + derived):

  probes   ``make`` every aura-lifecycle differential C++ probe (needs the sibling TrinityCore)
  derived  every aura-lifecycle corpus: per-track corpora, then the census/signatures
           (which read the external-policy index), then the Retail experiment
           catalogue (which merges every track's candidates), then the lead's
           merged model/unknowns corpora

The build-drift corpus needs the dbc-resolver release snapshot; fetch it first
with ``tools/fetch_dbc_release.py`` (the regen fails closed without it).

``--check`` hashes every file of ``docs/research/aura-lifecycle-corpora`` before
and after and exits 1 if anything changed: the committed corpora are exactly
what the committed code produces.

Usage (from ``scripts/research``)::

    python3 tools/regen_aura_lifecycle.py            # probes + derived
    python3 tools/regen_aura_lifecycle.py --check    # same, then compare
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
CORPORA = ROOT / "docs" / "research" / "aura-lifecycle-corpora"
OUT = "../../docs/research/aura-lifecycle-corpora"
PY = sys.executable

PROBES = ["tc_aura_identity_probe", "tc_aura_duration_probe", "tc_aura_stack_probe", "tc_aura_periodic_probe",
          "tc_aura_removal_probe", "tc_aura_area_probe", "tc_aura_order_probe", "tc_aura_r2_probe"]

#: commands that write their own corpora (no --out)
SELF_WRITING: list[str] = [
    "d-corpora",
]
#: (command, output file) in dependency order
TRACKS: list[tuple[str, str]] = [
    ("identity --corpus", "identity.json"),
    ("application --corpus", "application.json"),
    ("duration", "duration.json"),
    ("refresh", "refresh.json"),
    ("refresh --carryover", "carryover.json"),
    ("stacks --census", "stacks.json"),
    ("charges --census", "charges.json"),
    ("removal --corpus", "removal.json"),
    ("dispel --corpus", "dispel.json"),
    ("lifetime --corpus", "lifetime.json"),
    ("recipients --corpus", "area-lifecycle.json"),
    ("passive --corpus", "passive-active.json"),
    ("overlays --corpus", "external-policy.json"),
    ("ordering", "ordering.json"),
    ("numeric", "numeric-boundaries.json"),
    ("generation", "generation.json"),
    ("coremap", "core-navigation.json"),
]
#: read other corpora / the external-policy index
DERIVED: list[tuple[str, str]] = [
    ("census", "provider-census.json"),
    ("signatures", "lifecycle-signatures.json"),
    ("drift", "drift.json"),
    ("experiments --scan", "retail-experiments.json"),
]
#: lead: merged model, unknowns and falsification history (read everything above)
FINAL: list[tuple[str, str]] = [
    ("model --corpus", "semantic-axes.json"),
    ("unknowns --corpus", "unknowns.json"),
    ("falsification --corpus", "falsification.json"),
]


def probe_steps() -> list[list[str]]:
    return [["make", "-s", "-C", f"tools/{name}"] for name in PROBES if (RESEARCH / "tools" / name).is_dir()]


def derived_steps() -> list[list[str]]:
    return ([[PY, "tools/fetch_dbc_release.py", "--check"]]
            + [[PY, "aura_lifecycle.py", cmd] for cmd in SELF_WRITING]
            + [[PY, "aura_lifecycle.py", *cmd.split(), "--out", f"{OUT}/{out}"]
               for cmd, out in TRACKS + DERIVED + FINAL])


def snapshot(directory: Path) -> dict[str, str]:
    if not directory.is_dir():
        return {}
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(directory.rglob("*")) if p.is_file()}


def run(step: list[str]) -> None:
    shown = " ".join(step).replace(PY, "python3")
    print(f"$ {shown}", flush=True)
    started = time.monotonic()
    result = subprocess.run(step, cwd=RESEARCH, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    tail = "\n".join(result.stdout.strip().splitlines()[-3:])
    if tail:
        print("  " + tail.replace("\n", "\n  "))
    print(f"  [{time.monotonic() - started:.1f} s, exit {result.returncode}]", flush=True)
    if result.returncode != 0:
        print(result.stdout, file=sys.stderr)
        raise SystemExit(f"step failed: {shown}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0],
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--stage", action="append", choices=["probes", "derived"],
                        help="stage(s) to run (default: probes, derived)")
    parser.add_argument("--check", action="store_true",
                        help="fail if any corpus file changes (determinism / freshness check)")
    args = parser.parse_args()
    stages = args.stage or ["probes", "derived"]
    before = snapshot(CORPORA) if args.check else {}
    steps: list[list[str]] = []
    if "probes" in stages:
        steps += probe_steps()
    if "derived" in stages:
        steps += derived_steps()
    for step in steps:
        run(step)
    if not args.check:
        return 0
    after = snapshot(CORPORA)
    changed = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
    if changed:
        print(f"{len(changed)} corpus file(s) changed:")
        for name in changed:
            print(f"  {name}")
        return 1
    print(f"ok: {len(after)} corpus files unchanged")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
