"""One merged, validated unknowns / reopen-conditions artifact (lead).

Inputs: the ``unknowns`` list of every targeting corpus plus :data:`LEAD_UNKNOWNS`.
A malformed entry fails the merge (never dropped).  Entries the lead judged to
be duplicates are kept once; ``MERGED`` maps a dropped id to the surviving id
and the survivor lists the dropped ids in ``aliases``.
"""

from __future__ import annotations

import json
from collections import Counter
from typing import Any

from . import CORPORA, EVIDENCE_CLASSES, PINS, FailClosed

REQUIRED = ("id", "subject", "evidence", "known", "unknown", "why_unresolved", "reopen_condition", "build_skew")
SOURCES = (
    "selectors.json", "composition.json", "attributes.json", "relations.json", "explicit-validation.json",
    "geometry.json", "area.json", "caps.json", "chains.json", "smart-selection.json", "rng.json",
    "script-adapters.json", "world-policy.json", "group-policy.json", "effect-recipients.json",
    "areatriggers.json", "extended-scope.json", "aura-targets.json", "differential.json", "witnesses.json",
)

#: lead-level unknowns (cross-track); filled during reconciliation
LEAD_UNKNOWNS: list[dict[str, Any]] = [
    {"id": "TG-H-01", "subject": "main census population excludes script/AreaTrigger/linked children",
     "evidence": "structural-inference",
     "known": "census.json `global`/`per_spec` count IsEffect rows of ctx.scope.reach (Dummy-pass Scope: authored edges only); "
              "children cast by script hooks, AreaTrigger scripts/actions and spell_linked_spell are counted in "
              "census.json `extended` (768 effects / 417 spells)",
     "unknown": "which extended children are cast at runtime (hook-internal conditions) and their full recipient policy",
     "why_unresolved": "script-cast edges are structural facts; bodies were read for target adapters only",
     "reopen_condition": "a future Core blocker names one of the extended children; read its parent hook body and "
                         "promote the child (TG-D-13, TG-I-10)",
     "build_skew": False},
    {"id": "TG-H-02", "subject": "per-cast shared RNG stream order beyond targeting (hit/crit/proc/payload)",
     "evidence": "trinity-consumer",
     "known": "targeting RandomResize/shuffle draws and SpellHitResult draws interleave per effect group inside "
              "SelectSpellTargets (Spell.cpp:2485); crit is rolled at launch (rng.json)",
     "unknown": "exact draw counts of every SpellHitResult branch combined with the proc pipeline for a whole cast",
     "why_unresolved": "outside targeting; needs the proc/attack-table RNG passes joined into one stream model",
     "reopen_condition": "Core models a shared RNG stream across targeting and hit resolution",
     "build_skew": "n/a"},
    {"id": "TG-H-04", "subject": "build-skew effects (SpellIDs added after the last client build the pinned Trinity supports)",
     "evidence": "build-skew",
     "known": "census.json marks 188 current-player effects build_skew (dummy-corpora/build-skew.json: IDs absent from "
              "12.0.7.68367); their targeting verdicts use the generic consumer paths only",
     "unknown": "whether Trinity would add scripts, corrections, spell_script_names, AreaTrigger properties or conditions "
                "for these spells once it supports 12.1",
     "why_unresolved": "pinned Trinity 7f3d43b supports client builds <= 12.0.7.68453",
     "reopen_condition": "a TrinityCore revision supporting 12.1.0.69497 (or later) plus its TDB; re-run "
                         "tools/regen_targeting.py with the new pins",
     "build_skew": True},
    {"id": "TG-H-05", "subject": "integration boundary between spell selection and the aura target map",
     "evidence": "trinity-consumer",
     "known": "the spell's own selection only chooses the aura owner(s) (Spell.cpp:3241); area-aura recipients come "
              "from Aura::UpdateTargetMap / FillTargetMap every 500 ms (targeting.auratargets, aura-targets.json); "
              "AreaTriggers are a third, tick-driven pipeline (targeting.areatriggers)",
     "unknown": "the oracle does not chain them: pipeline.evaluate does not hand its aura owners to "
                "auratargets.target_map, and neither models the update cadence against a timeline",
     "why_unresolved": "time-stepped aura/AT lifecycle belongs to the aura-lifecycle and scheduling passes",
     "reopen_condition": "a Core blocker needs area-aura or AreaTrigger recipients over time; compose "
                         "pipeline -> auratargets / areatriggers with an explicit tick schedule",
     "build_skew": "n/a"},
    {"id": "TG-H-03", "subject": "Register()-time conditional hook registration (17 classes)",
     "evidence": "structural-inference",
     "known": "the tree-sitter index records every `+=` in Register() unconditionally; these classes register hooks "
              "inside if/switch/for: spell_dh.cpp:550 collective_anguish, :1027 demonic_appetite_energize; "
              "spell_dk.cpp:1350 subduing_grasp; spell_druid.cpp:941 entangling_roots; spell_generic.cpp:911 clone, "
              ":1312 defend, :2357 mounted_charge, :4768 war_mode_enlisted; spell_paladin.cpp:1157 holy_prism_selector; "
              "spell_pet.cpp:182 pet_calculate; spell_priest.cpp:1769 entropic_rift, :2271 halo_effect_selector, "
              ":2318 halo_return_effect_selector; spell_rogue.cpp:301 blade_flurry; spell_shaman.cpp:684 "
              "deeply_rooted_elements, :2448 primordial_wave; plus spell_evoker.cpp:642 ruby_embers (hook *target type* "
              "chosen by a ternary; found by hostile review R3, outside scope). Hand-read and modelled per registration "
              "state in script-adapters.json: primordial_wave (spec-conditional), holy_prism_selector (targets 16/31 "
              "never registered), entangling_roots (area hook only for 102359), halo selectors (talent-conditional, "
              "mask 0 either way; TG-I-D04)",
     "unknown": "for the classes not hand-read (the other 12), which hooks are live for a given spell id / caster "
                "state; Dummy binding counts treat them as always registered",
     "why_unresolved": "no control-flow model of Register(); hand-reading done only where a hostile review flagged it",
     "reopen_condition": "a Core blocker touches one of these spells, or the index learns Register() conditions",
     "build_skew": False},
]

#: dropped duplicate id -> surviving id (reconciliation decisions)
MERGED: dict[str, str] = {
    "TG-I-11": "TG-H-03",   # same Register()-time conditional-registration gap
    "TG-D-13": "TG-H-01",   # the lead decision it asked for: children stay in census.json `extended`
}


def _evidence_ok(value: Any) -> bool:
    values = value if isinstance(value, list) else [value]
    return bool(values) and all(v in EVIDENCE_CLASSES for v in values)


def collect() -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    defects: list[str] = []
    for name in SOURCES:
        path = CORPORA / name
        if not path.exists():
            continue
        doc = json.loads(path.read_text(encoding="utf-8"))
        for e in doc.get("unknowns", []):
            entries.append({**e, "source": name})
    for e in LEAD_UNKNOWNS:
        entries.append({**e, "source": "lead"})
    seen: dict[str, dict[str, Any]] = {}
    unique: list[dict[str, Any]] = []
    for e in entries:
        for key in REQUIRED:
            if key not in e or e[key] in ("", None, []):
                defects.append(f"{e.get('id', '?')} ({e['source']}): missing {key}")
        if not _evidence_ok(e.get("evidence")):
            defects.append(f"{e.get('id')} ({e['source']}): bad evidence {e.get('evidence')!r}")
        prior = seen.get(e.get("id"))
        if prior is None:
            e = {**{k: v for k, v in e.items() if k != "source"}, "sources": [e["source"]]}
            seen[e.get("id")] = e
            unique.append(e)
            continue
        body = {k: v for k, v in e.items() if k != "source"}
        prior_body = {k: v for k, v in prior.items() if k not in ("source", "sources")}
        if body != prior_body:
            defects.append(f"id {e['id']} differs between {prior['sources']} and {e['source']}")
        else:
            prior["sources"].append(e["source"])  # the same unknown published by several corpora of one track
    entries = unique
    if defects:
        raise FailClosed("unknowns merge defects:\n  " + "\n  ".join(defects))
    for dropped, survivor in MERGED.items():
        if dropped not in seen or survivor not in seen:
            raise FailClosed(f"unknowns: MERGED names unknown id {dropped} -> {survivor}")
    kept = []
    for e in entries:
        if e["id"] in MERGED:
            continue
        aliases = sorted(d for d, s in MERGED.items() if s == e["id"])
        kept.append({**e, **({"aliases": aliases} if aliases else {})})
    return sorted(kept, key=lambda e: e["id"])


def build() -> dict[str, Any]:
    entries = collect()

    def ev(e):
        return e["evidence"] if isinstance(e["evidence"], str) else "+".join(e["evidence"])

    return {
        "provenance": {**PINS, "command": "python3 targeting.py unknowns --out ../../docs/research/targeting-corpora/unknowns.json",
                       "sources": list(SOURCES) + ["targeting/unknowns.py:LEAD_UNKNOWNS"],
                       "merged_duplicates": dict(sorted(MERGED.items()))},
        "count": len(entries),
        "by_source": dict(sorted(Counter(e["sources"][0] for e in entries).items())),
        "by_evidence": dict(sorted(Counter(ev(e) for e in entries).items())),
        "by_build_skew": dict(sorted(Counter(str(e["build_skew"]) for e in entries).items())),
        "entries": entries,
    }
