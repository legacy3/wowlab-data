#!/usr/bin/env python3
"""Regenerate every corpus of the targeting research pass, in dependency order.

Stages (select with ``--stage``; default: probes + derived):

  tdb      world-DB inputs (needs ``--tdb`` = the pinned TDB dump; ~1 min)
  dummy    re-derive the Dummy-pass corpora that depend on the ported
           ``TargetHook::CheckEffect`` (``dummy_semantics.py all``, ~3 min)
  probes   ``make`` every targeting differential C++ probe (needs the sibling TrinityCore)
  derived  every targeting corpus: per-track corpora, differential, census,
           witnesses, merged unknowns

``--check`` hashes every file of ``docs/research/targeting-corpora`` (and, with
``--stage dummy``, ``docs/research/dummy-corpora``) before and after and exits 1
if anything changed: the committed corpora are exactly what the committed code
produces.

Usage (from ``scripts/research``)::

    python3 tools/regen_targeting.py                 # probes + derived
    python3 tools/regen_targeting.py --check         # same, then compare
    python3 tools/regen_targeting.py --stage dummy --stage probes --stage derived --check
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
DOCS = ROOT / "docs" / "research"
OUT = "../../docs/research/targeting-corpora"
PY = sys.executable

PROBES = ["tc_target_selector_probe", "tc_target_geom_probe", "tc_target_chain_probe", "tc_target_relation_probe",
          "tc_target_at_probe", "tc_target_positivity_probe", "tc_target_auramap_probe"]

#: (command, output-file) in dependency order; census/unknowns/witnesses read the track corpora
#: per-track "all" commands write their own corpora (no --out)
TRACK_ALL: list[str] = [
    "track-a-all",
    "e-all",
]
DERIVED: list[tuple[str, str]] = [
    ("relations --differential-worlds 1000", "relations.json"),
    ("explicit-census", "explicit-validation.json"),
    ("geometry", "geometry.json"),
    ("area", "area.json"),
    ("caps", "caps.json"),
    ("chains", "chains.json"),
    ("smart-selection", "smart-selection.json"),
    ("rng", "rng.json"),
    ("groups", "group-policy.json"),
    ("areatriggers", "areatriggers.json"),
    ("extended-scope", "extended-scope.json"),
    ("aura-targets", "aura-targets.json"),
    ("effect-recipients", "effect-recipients.json"),
]
FINAL: list[tuple[str, str]] = [
    ("differential", "differential.json"),
    ("census", "census.json"),
    ("witnesses", "witnesses.json"),
    ("unknowns", "unknowns.json"),
]


def probe_steps() -> list[list[str]]:
    return [["make", "-s", "-C", f"tools/{name}"] for name in PROBES if (RESEARCH / "tools" / name).is_dir()]


def derived_steps() -> list[list[str]]:
    return ([[PY, "targeting.py", cmd] for cmd in TRACK_ALL]
            + [[PY, "targeting.py", *cmd.split(), "--out", f"{OUT}/{out}"] for cmd, out in DERIVED + FINAL])


def tdb_steps(tdb: str) -> list[list[str]]:
    """World-DB inputs of track B (training-dummy templates + difficulty flags)."""
    inputs = f"{OUT}/inputs"
    extract = [PY, "tools/tdb_world_extract.py", "--tdb", tdb]
    return [
        # track B: full projected creature_template, filtered by relations.select_dummies (R1-02)
        extract + ["--tables", "creature_template", "--project",
                   "creature_template=entry,name,subname,faction,unit_flags,unit_flags2,unit_flags3,type,flags_extra,ScriptName",
                   "--out", f"{inputs}/.creature-template-full.json"],
        [PY, "targeting.py", "training-dummy-select", "--source", f"{inputs}/.creature-template-full.json",
         "--remove-source", "--out", f"{inputs}/training-dummies.json"],
        [PY, "targeting.py", "training-dummy-ids", "--out", f"{inputs}/training-dummies.ids.json"],
        extract + ["--tables", "creature_template_difficulty", "--project",
                   "creature_template_difficulty=Entry,DifficultyID,TypeFlags,StaticFlags1,StaticFlags4",
                   "--keep", f"creature_template_difficulty.Entry=@{inputs}/training-dummies.ids.json",
                   "--out", f"{inputs}/training-dummy-difficulty.json"],
        # track G: DisableMgr SPELL_DISABLE_LOS (oracle.SpellView.los_disabled)
        extract + ["--tables", "disables", "--out", f"{inputs}/disables.json"],
        # track I: AreaTrigger shape/path rows and conditions source 28 (absent from the Dummy-pass overlay)
        extract + ["--tables", "areatrigger_create_properties_polygon_vertex,areatrigger_create_properties_spline_point,"
                               "areatrigger_create_properties_orbit,conditions",
                   "--keep", "conditions.SourceTypeOrReferenceId=28",
                   "--out", f"{inputs}/areatrigger-world.json"],
    ]


def dummy_steps() -> list[list[str]]:
    return [[PY, "dummy_semantics.py", "all"]]


def snapshot(dirs: list[Path]) -> dict[str, str]:
    out: dict[str, str] = {}
    for directory in dirs:
        if not directory.is_dir():
            continue
        for path in sorted(directory.rglob("*")):
            if path.is_file():
                out[str(path.relative_to(ROOT))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


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
    parser.add_argument("--stage", action="append", choices=["tdb", "dummy", "probes", "derived"],
                        help="stage(s) to run (default: probes, derived)")
    parser.add_argument("--tdb", help="pinned TDB dump (required for --stage tdb)")
    parser.add_argument("--check", action="store_true",
                        help="fail if any corpus file changes (determinism / freshness check)")
    args = parser.parse_args()
    stages = args.stage or ["probes", "derived"]
    dirs = [DOCS / "targeting-corpora"] + ([DOCS / "dummy-corpora"] if "dummy" in stages else [])
    before = snapshot(dirs) if args.check else {}
    if "tdb" in stages and not args.tdb:
        parser.error("--stage tdb needs --tdb")
    steps: list[list[str]] = []
    if "tdb" in stages:
        steps += tdb_steps(args.tdb)
    if "dummy" in stages:
        steps += dummy_steps()
    if "probes" in stages:
        steps += probe_steps()
    if "derived" in stages:
        steps += derived_steps()
    for step in steps:
        run(step)
    if not args.check:
        return 0
    after = snapshot(dirs)
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
