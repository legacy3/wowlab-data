#!/usr/bin/env python3
"""Regenerate every corpus of the combat-preparation research pass, in order.

The pass has three packages (``controlled_units``, ``weapon_combat``,
``character_prep``) whose corpora depend on each other and on a few world-DB
extracts.  This driver runs the documented commands in dependency order so a
reader does not have to reassemble them from three reports.

Stages (each can be selected with ``--stage``; default: probes + derived):

  tdb      world-DB extracts (needs ``--tdb`` = the pinned TDB dump; minutes)
  probes   ``make`` every differential C++ probe (needs the sibling TrinityCore)
  derived  every snapshot-derived corpus, the Trinity differential and the
           merged cross-track unknowns artifact

``--check`` hashes every corpus file of the pass before and after the run and
exits 1 if anything changed, i.e. it proves the committed corpora are exactly
what the committed code produces (given the same wowlab-data commit, which is
recorded in several provenance blocks).

Usage (from ``scripts/research``)::

    python3 tools/regen_combat_prep.py                       # probes + derived
    python3 tools/regen_combat_prep.py --check               # same, then compare
    python3 tools/regen_combat_prep.py --stage tdb --tdb ../../../../bag/tdb/TDB_full_world_1200.26021_2026_02_06.sql
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
PY = sys.executable

CORPUS_DIRS = [
    DOCS / "controlled-unit-corpora",
    DOCS / "weapon-combat-corpora",
    DOCS / "character-prep-corpora",
    DOCS / "world-db-corpora",
    DOCS / "combat-prep-corpora",
]
PROBES = ["tc_pet_probe", "tc_weapon_probe", "tc_swing_probe", "tc_prep_probe"]
WDB = "../../docs/research/world-db-corpora"
CREATURE_TEMPLATE_COLUMNS = (
    "entry,name,subname,RequiredExpansion,faction,npcflag,speed_walk,speed_run,scale,"
    "Classification,dmgschool,BaseAttackTime,RangeAttackTime,BaseVariance,RangeVariance,"
    "unit_class,unit_flags,unit_flags2,unit_flags3,family,type,VehicleId,AIName,MovementType,"
    "RegenHealth,CreatureImmunitiesId,flags_extra,ScriptName,VerifiedBuild")
CREATURE_IDS = f"{WDB}/creature-templates.ids.json"  # object with an "ids" list (accepted by --keep)


def tdb_steps(tdb: str) -> list[list[str]]:
    extract = [PY, "tools/tdb_world_extract.py", "--tdb", tdb]
    return [
        [PY, "controlled_units.py", "creature-ids"],
        extract + ["--tables", "player_classlevelstats,player_racestats,pet_levelstats",
                   "--out", f"{WDB}/player-base-stats.json"],
        extract + ["--tables",
                   "creature_template,creature_template_addon,creature_summoned_data,"
                   "creature_template_spell,creature_summon_groups,creature_classlevelstats,"
                   "creature_template_model",
                   "--project", f"creature_template={CREATURE_TEMPLATE_COLUMNS}",
                   "--keep", f"creature_template.entry=@{CREATURE_IDS}",
                   "--keep", f"creature_template_addon.entry=@{CREATURE_IDS}",
                   "--keep", f"creature_template_spell.CreatureID=@{CREATURE_IDS}",
                   "--keep", f"creature_summon_groups.summonerId=@{CREATURE_IDS}",
                   "--keep", f"creature_template_model.CreatureID=@{CREATURE_IDS}",
                   "--out", f"{WDB}/creature-templates.json"],
        [PY, "controlled_units.py", "world-b", "--tdb", tdb],
        extract + ["--tables", "creature_template_difficulty",
                   "--project", "creature_template_difficulty=Entry,DifficultyID,"
                                "LevelScalingDeltaMin,LevelScalingDeltaMax,ContentTuningID",
                   "--out", "../../docs/research/weapon-combat-corpora/creature-level-deltas.json"],
        [PY, "character_prep.py", "base-stat-gap", "--tdb", tdb, "--probe",
         "--out", "../../docs/research/character-prep-corpora/base-stat-gap.json"],
        [PY, "character_prep.py", "server-inputs", "--tdb", tdb,
         "--out", "../../docs/research/character-prep-corpora/server-inputs.json"],
    ]


def probe_steps() -> list[list[str]]:
    return [["make", "-s", "-C", f"tools/{name}"] for name in PROBES]


def derived_steps() -> list[list[str]]:
    return [
        # the committed --keep id set of creature-templates.json (derived; checked by --check)
        [PY, "controlled_units.py", "creature-ids"],
        [PY, "controlled_units.py", "all-a"],
        [PY, "controlled_units.py", "all-b"],
        [PY, "weapon_combat.py", "all-c"],
        [PY, "weapon_combat.py", "all-d", "--census", "--levels",
         "../../docs/research/weapon-combat-corpora/creature-level-deltas.json"],
        [PY, "character_prep.py", "all"],
        # the differential reads the compiled fixtures written by `all`
        [PY, "character_prep.py", "differential",
         "--out", "../../docs/research/character-prep-corpora/differential.json"],
        [PY, "tools/merge_unknowns.py"],
    ]


def snapshot() -> dict[str, str]:
    out: dict[str, str] = {}
    for directory in CORPUS_DIRS:
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
    result = subprocess.run(step, cwd=RESEARCH, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True)
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
    parser.add_argument("--stage", action="append", choices=["tdb", "probes", "derived"],
                        help="stage(s) to run (default: probes, derived)")
    parser.add_argument("--tdb", help="pinned TDB dump (required for --stage tdb)")
    parser.add_argument("--check", action="store_true",
                        help="fail if any corpus file changes (determinism / freshness check)")
    args = parser.parse_args()
    stages = args.stage or ["probes", "derived"]
    if "tdb" in stages and not args.tdb:
        parser.error("--stage tdb needs --tdb")

    before = snapshot() if args.check else {}
    steps: list[list[str]] = []
    if "tdb" in stages:
        steps += tdb_steps(args.tdb)
    if "probes" in stages:
        steps += probe_steps()
    if "derived" in stages:
        steps += derived_steps()
    for step in steps:
        run(step)
    if not args.check:
        return 0
    after = snapshot()
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
