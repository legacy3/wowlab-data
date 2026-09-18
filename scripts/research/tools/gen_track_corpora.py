#!/usr/bin/env python3
"""Regenerate the corpora owned by the per-track modules.

Each track module exposes plain builder functions rather than a side-effecting
``__main__``; this driver calls them so the whole pass has one regeneration path.
Driven by ``tools/regen_selected_package.py --stage tracks``.

A module that is absent is SKIPPED loudly, never silently: a missing track must not
look like a clean run.
"""

from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from selected_package import CORPORA


def write(name: str, payload) -> None:
    path = CORPORA / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=1, sort_keys=True, ensure_ascii=False) + "\n",
                    encoding="utf-8")
    print(f"    wrote {name}")


#: The five reference packages every track reports on, by entry id.
REFERENCE_ENTRIES = {
    "improved_vivify": 101510, "martial_expert": 117409, "heart_of_the_crusader": 115483,
    "phalanx": 137000, "ephemeral_bond": 136808,
}


def _population():
    """(provider spells, (entry, definition, spell, max_ranks)) over resolvable entries."""
    from selected_package.provenance import Provenance, TraitSpellError
    provenance = Provenance()
    spells, entries = set(), []
    for entry_id in sorted(provenance.traits.entries):
        entry = provenance.traits.entries[entry_id]
        if entry.max_ranks not in (1, 2):
            continue
        resolved = provenance.resolve(entry_id, 1)
        if isinstance(resolved, TraitSpellError):
            continue
        spells.add(resolved.spell)
        entries.append((entry_id, resolved.definition_id, resolved.spell, entry.max_ranks))
    return sorted(spells), entries


def topology() -> None:
    from selected_package.topology import (TopologyCensus, over_admission_corpus,
                                           topology_corpus)
    census = TopologyCensus()
    write("topology.json", topology_corpus(census, REFERENCE_ENTRIES))
    write("over-admission.json", over_admission_corpus(census))


def sidecars() -> None:
    from selected_package.sidecars import SidecarCensus, census_payload, negatives_payload
    from procs.spells import SpellCatalog
    census = SidecarCensus()
    spells, entries = _population()
    names = {}
    try:
        catalog = SpellCatalog(census.source)
        names = {s: (catalog.get(s).name if catalog.get(s) else "") for s in spells}
    except Exception:                               # noqa: BLE001 - names are cosmetic
        names = {}
    write("owner-policy.json", census_payload(census, spells, entries))
    write("owner-negatives.json", negatives_payload(census, spells, entries, names))


def sole_authorities() -> None:
    import subprocess, sys as _sys
    script = pathlib.Path(__file__).resolve().parent / "gen_sole_authorities.py"
    if not script.exists():
        raise ImportError("tools/gen_sole_authorities.py absent")
    subprocess.run([_sys.executable, str(script)], check=True)
    print("    wrote sole-authorities.json")


STAGES = {"topology": topology, "sidecars": sidecars, "sole_authorities": sole_authorities}


def main(argv: list[str] | None = None) -> int:
    failures = 0
    for name, builder in STAGES.items():
        print(f"  {name}")
        try:
            builder()
        except ImportError as exc:
            print(f"    SKIPPED (module absent: {exc})")
        except Exception as exc:                    # noqa: BLE001 - report, do not mask
            print(f"    FAILED: {type(exc).__name__}: {exc}")
            failures += 1
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
