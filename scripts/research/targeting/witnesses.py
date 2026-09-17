"""Witness corpus (lead): every curated fixture, evaluated, with its discrimination claim.

Sources (all tracked):

* ``targeting/fixtures/*.json`` -- the pipeline fixture library (track G + the A-F witness
  proposals it ingested); evaluated by ``cmd_g.check_library`` (``Spell::SelectSpellTargets``);
* ``targeting/fixtures/areatrigger/*.json`` -- track I's AreaTrigger witnesses; evaluated by
  ``areatriggers.evaluate_tick`` (``AreaTrigger::UpdateTargetList``);
* ``targeting/fixtures/auramap/*.json`` -- track J's area-aura witnesses; evaluated by
  ``auratargets.evaluate_witness`` (``Aura::UpdateTargetMap``).

Every row pins spell/effect, the source selectors (from the snapshot), the direct consumers
(the ``Spell::SelectImplicit*`` routine of each selector, or the fixture's own ``consumer``),
the synthetic population (fixture path), expected and actual recipients per effect, and what
competing model the witness rules out.  :data:`REQUIRED_SHAPES` maps the brief's witness
shapes (question 18) to witness ids; a shape without a passing witness fails the build.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import PINS, FailClosed

FIXTURES = Path(__file__).resolve().parent / "fixtures"
AT_FIXTURES = FIXTURES / "areatrigger"

#: brief question 18 shape -> fixture names (any passing one closes the shape)
REQUIRED_SHAPES: dict[str, list[str]] = {
    "hostile single target": ["witness-B-133-0", "witness-B-53-0"],
    "friendly single target": ["witness-B-3411-0", "witness-A-1022-0"],
    "self": ["witness-F-871-0", "witness-B-2061-0"],
    "hostile AoE": ["witness-C-6343-0", "merge-duplicate-recipient"],
    "friendly AoE": ["witness-C-64844-0"],
    "party/raid heal": ["witness-F-1219209-0", "witness-C-97462-0", "witness-F-97462-0"],
    "smart heal": ["witness-D-48438-0"],
    "chain heal": ["witness-D-1064-0", "grouped-effects-inherit-lead-chain"],
    "chain damage": ["witness-D-188443-0", "witness-F-31935-1"],
    "cone": ["witness-C-228478-0", "witness-C-384391-0", "witness-A-46968-0"],
    "explicit-target-centred area": ["witness-C-1126-0", "witness-A-1714-0", "targetb-area-around-targeta-dest"],
    "caster-centred area": ["witness-C-6343-0", "witness-A-1160-0"],
    "target cap": ["witness-C-5484-0", "witness-C-53385-0", "witness-C-5484-0-2"],
    "random target": ["witness-D-50286-0", "witness-F-413984-0", "grouped-effects-share-random-resize"],
    "scripted target filter": ["witness-E-404358-0", "witness-E-27243-1"],
    "pet/owner relation": ["witness-F-264735-1"],
    "dead/corpse target": ["consecration-26573-sphere"],
    "training-dummy relation (legacy + current faction rewrite)": ["witness-B-133-0", "witness-B-133-0-2",
                                                                    "witness-B-133-0-3"],
    "ground/destination effect": ["witness-C-2120-0", "witness-A-61882-2", "blizzard-190356-cylinder"],
    "multi-effect spell with different recipient sets": ["witness-F-97462-0", "merge-duplicate-recipient",
                                                        "per-effect-dest-snapshot", "witness-E-45438-0"],
    "area-aura recipients (aura target map)": ["witness-J-465-0", "witness-J-400129-0", "witness-J-5740-0"],
    "areatrigger recipients": ["consecration-26573-sphere", "pw-barrier-62618-action", "tar-trap-187699-double-entry"],
}

#: documentation-only keys of AreaTrigger witness expectations
AT_DOC_KEYS = {"note", "damage"}


def _selectors(spell: int) -> dict[str, Any]:
    from . import context, selectors
    ctx = context.get()
    out = {}
    for e in ctx.data.effects(spell):
        if not e.is_effect:
            continue
        pair = []
        for t in (e.target_a, e.target_b):
            info = selectors.info(t)
            pair.append({"id": t, "name": info.name, "handler": selectors.handler(t) if t else None})
        out[str(e.index)] = pair
    return out


def _pipeline_rows() -> list[dict[str, Any]]:
    from . import context
    from .cmd_g import check_library
    ctx = context.get()
    lib = check_library()
    rows = []
    for r in lib["fixtures"]:
        name = Path(r["fixture"]).stem
        path = FIXTURES / f"{name}.json"
        doc = json.loads(path.read_text(encoding="utf-8"))
        spell = int(doc.get("spell", {}).get("id", 0) or 0)
        real = spell in ctx.scope.reach
        row = {
            "id": name, "kind": "pipeline", "fixture": f"scripts/research/targeting/fixtures/{name}.json",
            "shape": doc.get("about", ""), "discriminates": doc.get("discriminates", ""),
            "spell": spell, "real_spell": real, "build_skew": ctx.is_skew(spell) if real else False,
            "selectors": _selectors(spell) if real else "synthetic spell block (see fixture)",
            "consumer": doc.get("consumer", "Spell.cpp:720-878 SelectSpellTargets + the handler of each selector"),
            "expected": doc.get("expect", {}),
            "actual": (r["result"] if isinstance(r.get("result"), str) else
                       {k: v for k, v in (r.get("result") or {}).items()
                        if k in ("recipients", "cast_result", "dests", "draws_consumed")}),
            "passed": bool(r["ok"]),
        }
        if not r["ok"]:
            row["diffs"] = r.get("diffs") or r.get("message")
        rows.append(row)
    return rows


def _at_rows() -> list[dict[str, Any]]:
    from . import areatriggers as at
    from . import context
    from .fixture import World
    from .trace import Trace
    ctx = context.get()
    atw = at.ATWorld(ctx.bundle.world)
    rows = []
    for path in sorted(AT_FIXTURES.glob("*.json")):
        doc = json.loads(path.read_text(encoding="utf-8"))
        world = World.from_dict(doc["fixture"])
        res = at.evaluate_tick(world, "at", Trace(), atw, (lambda s: None) if doc["spell"] == 62618 else None)
        entering = res.get("entering", [])

        def attack(u: str) -> bool:
            try:
                return bool(world.relation(world.caster, u, "valid_attack"))
            except FailClosed:
                return False

        derived: dict[str, Any] = {}
        for key in doc["expect"]:
            if key in AT_DOC_KEYS:
                continue
            if key == "at_inside_units":
                derived[key] = res["targets"]
            elif key == "entering_order":
                derived[key] = entering
            elif key.startswith("aura_"):
                derived[key] = [u for u, acts in res["actions"].items() if acts]
            elif key == "debuff_204242":   # spell_paladin.cpp:445: cast per entering unit if IsValidAttackTarget
                derived[key] = [u for u in entering if attack(u)]
            elif key == "casts_187700":    # spell_hunter.cpp:1351-1360: one cast per hostile entering (TG-I-D02)
                derived[key] = len([u for u in entering if attack(u)])
            else:
                raise FailClosed(f"witnesses: no derivation for AreaTrigger expectation {key!r} ({path.name})")
        expected = {k: v for k, v in doc["expect"].items() if k not in AT_DOC_KEYS}
        rows.append({
            "id": path.stem, "kind": "areatrigger", "fixture": f"scripts/research/targeting/fixtures/areatrigger/{path.name}",
            "shape": doc["shape"], "discriminates": doc["discriminates"], "spell": doc["spell"], "effect": doc["effect"],
            "real_spell": doc["spell"] in ctx.scope.reach, "build_skew": doc["build_skew"],
            "selectors": _selectors(doc["spell"]), "consumer": doc["consumer"],
            "expected": doc["expect"], "actual": derived, "passed": derived == expected,
            **({"defect": doc["defect"]} if "defect" in doc else {}),
        })
    return rows


def _auramap_rows() -> list[dict[str, Any]]:
    """Track J witnesses: ``auratargets.evaluate_witness`` (``Aura::UpdateTargetMap`` / ``FillTargetMap``)."""
    from . import auratargets, context
    ctx = context.get()
    rows = []
    for path in auratargets.witness_paths():
        doc = json.loads(path.read_text(encoding="utf-8"))
        res = auratargets.evaluate_witness(doc)
        rows.append({
            "id": path.stem, "kind": "aura-target-map",
            "fixture": f"scripts/research/targeting/fixtures/auramap/{path.name}",
            "shape": doc["shape"], "discriminates": doc["discriminates"], "spell": doc["spell"], "effect": doc["effect"],
            "real_spell": doc["spell"] in ctx.scope.reach, "build_skew": doc["build_skew"],
            "selectors": _selectors(doc["spell"]), "consumer": doc["consumer"],
            "expected": doc["expect"], "actual": {"recipients": res["recipients"]}, "passed": bool(res["ok"]),
        })
    return rows


def build() -> dict[str, Any]:
    rows = sorted(_pipeline_rows() + _at_rows() + _auramap_rows(), key=lambda r: r["id"])
    by_id = {r["id"]: r for r in rows}
    coverage = {}
    for shape, names in REQUIRED_SHAPES.items():
        missing = [n for n in names if n not in by_id]
        if missing:
            raise FailClosed(f"witnesses: shape {shape!r} names unknown witnesses {missing}")
        passing = [n for n in names if by_id[n]["passed"]]
        coverage[shape] = {"witnesses": names, "passing": passing, "closed": bool(passing)}
    failed = [r["id"] for r in rows if not r["passed"]]
    return {
        "provenance": {**PINS, "command": "python3 targeting.py witnesses --out ../../docs/research/targeting-corpora/witnesses.json",
                       "sources": ["scripts/research/targeting/fixtures/*.json (targeting.pipeline)",
                                   "scripts/research/targeting/fixtures/areatrigger/*.json (targeting.areatriggers)"]},
        "summary": {"witnesses": len(rows), "passed": len(rows) - len(failed), "failed": len(failed),
                    "real_spell_witnesses": sum(r["real_spell"] for r in rows),
                    "distinct_real_spells": len({r["spell"] for r in rows if r["real_spell"]}),
                    "shapes_closed": sum(c["closed"] for c in coverage.values()), "shapes": len(coverage),
                    "failed_ids": failed},
        "shape_coverage": coverage,
        "witnesses": rows,
    }
