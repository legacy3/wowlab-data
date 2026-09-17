"""Track A commands: selector vocabulary, TargetA/TargetB composition, targeting attributes.

    targeting.py selectors   [--out docs/research/targeting-corpora/selectors.json]
    targeting.py selector ID
    targeting.py composition [--out docs/research/targeting-corpora/composition.json]
    targeting.py attributes  [--out docs/research/targeting-corpora/attributes.json]
    targeting.py track-a-all        (writes all three corpora to docs/research/targeting-corpora/)
"""

from __future__ import annotations

from collections import Counter
from functools import lru_cache
from typing import Any

from . import CORPORA, PINS
from .cli import emit, write_json

PROBE = "scripts/research/tools/tc_target_selector_probe"


def _provenance(command: str) -> dict[str, Any]:
    return {"pins": PINS, "command": f"cd scripts/research && python3 targeting.py {command}",
            "probe": PROBE, "track": "A"}


@lru_cache(maxsize=1)
def _census():
    from . import context, selectors
    return selectors.census(context.get())


def _public(c: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in c.items() if not k.startswith("_")}


def _cone_defaults(ctx, rows) -> dict[str, Any]:
    """Cone-category effects whose ConeDegrees is 0 get 90 degrees from SpellMgr.cpp:5282."""
    from .selectors import info
    zero, nonzero = set(), set()
    for r in rows:
        if not r["corrected"]:
            continue
        if any(t < 153 and info(t).category == "CONE" for t in r["corrected"]):
            restr = ctx.data.restrictions(r["spell"])
            (nonzero if restr and float(restr["ConeDegrees"]) != 0.0 else zero).add(r["spell"])
    return {"consumer": "SpellMgr.cpp:5281-5283 (ConeAngle fuzzyEq 0 -> 90); Spell.cpp:1281-1287",
            "cone_spells_with_cone_degrees": len(nonzero), "cone_spells_defaulted_to_90": len(zero),
            "defaulted_witnesses": sorted(s for s in zero if not ctx.is_skew(s))[:10],
            "evidence": "trinity-consumer"}


UNKNOWNS_A = [
    {"id": "TG-A-01", "subject": "selector ids >= TOTAL_SPELL_TARGETS (153)", "evidence": "db2-fact",
     "known": "no current-player or controlled-unit effect uses an id >= 153 in 12.1.0.69497",
     "unknown": "behaviour of any future id beyond Trinity's _data table (Trinity indexes out of bounds)",
     "why_unresolved": "no consumer", "reopen_condition": "census selector_ids_beyond_trinity non-empty", "build_skew": "n/a"},
    {"id": "TG-A-02", "subject": "selector 151 (TARGET_UNK_151) on 49028:0 Dancing Rune Weapon (SPELL_EFFECT_SUMMON)",
     "evidence": "trinity-consumer",
     "known": "Trinity _data row: UNIT/CASTER/AREA/ENEMY -> SelectImplicitAreaTargets adds enemies within radius 7 as "
              "unit targets carrying effect bit 0; EffectSummonType runs in LAUNCH mode (SpellEffects.cpp:1869) so the "
              "summon count does not depend on them",
     "unknown": "Retail meaning of id 151 (unnamed in Trinity) and whether the summon position follows an enemy",
     "why_unresolved": "no Trinity name/comment; structural similarity only",
     "reopen_condition": "Trinity names id 151 or a sniff of 49028 shows target GUIDs", "build_skew": False},
    {"id": "TG-A-03", "subject": "CONE_CASTER_TO_DEST (104, 136) and RECT (128-130) orientation",
     "evidence": "trinity-consumer",
     "known": "Trinity builds every cone from *m_caster (position and orientation) and ignores m_targets dst "
              "(Spell.cpp:1301, 9455); direction axis unused",
     "unknown": "whether Retail orients the cone towards the destination for CASTER_TO_DEST ids",
     "why_unresolved": "no Retail consumer; the name is not semantics",
     "reopen_condition": "retail experiment RX-A-01", "build_skew": False},
    {"id": "TG-A-04", "subject": "SpellEffectAttributes AddTargetCombatReachToAOE (0x800), AreaEffectsUseTargetRadius (0x20000), "
                              "PositionIsFacingRelative (0x2), ExcludeOwnParty (0x10) on player effects",
     "evidence": "db2-fact",
     "known": "flags present on player effects (counts in attributes.json effect_attributes); Trinity marks them NYI and has no reader",
     "unknown": "their effect on recipient sets (area radius / facing / party exclusion)",
     "why_unresolved": "no consumer in Trinity != no Retail behaviour",
     "reopen_condition": "a consumer appears in Trinity or retail experiment RX-A-02", "build_skew": False},
    {"id": "TG-A-05", "subject": "EffectAttributes bits 0x00400000 and 0x10000000 in player data", "evidence": "db2-fact",
     "known": "present in current-player SpellEffect.EffectAttributes, absent from DBCEnums.h:2400",
     "unknown": "name and semantics", "why_unresolved": "not in the pinned enum",
     "reopen_condition": "Trinity/client enum update", "build_skew": "n/a"},
    {"id": "TG-A-06", "subject": "SpellTargetRestrictions.MaxTargetLevel", "evidence": "trinity-consumer",
     "known": "loaded (SpellInfo.cpp:1510), never read by Trinity; 0 current-player spells set it",
     "unknown": "Retail semantics", "why_unresolved": "no consumer, no current data",
     "reopen_condition": "a current-player spell sets it", "build_skew": "n/a"},
    {"id": "TG-A-07", "subject": "SpellCastingRequirements.FacingCasterFlags value 6", "evidence": "db2-fact",
     "known": "Trinity only tests SPELL_FACING_FLAG_INFRONT (0x1) in CheckRange (Spell.cpp:7300)",
     "unknown": "meaning of bits 0x2/0x4", "why_unresolved": "no consumer",
     "reopen_condition": "enum update", "build_skew": "n/a"},
    {"id": "TG-A-08", "subject": "cone effects with ConeDegrees == 0", "evidence": "trinity-consumer",
     "known": "Trinity forces 90 degrees at load (SpellMgr.cpp:5282), including TARGET_UNIT_CONE_180_DEG_ENEMY",
     "unknown": "Retail default cone angle when ConeDegrees is 0",
     "why_unresolved": "Trinity value is a server-authored default", "reopen_condition": "retail experiment RX-A-03",
     "build_skew": False},
]

RETAIL_A = [
    {"id": "RX-A-01", "question": "Is a TARGET_UNIT_CONE_CASTER_TO_DEST_ENEMY cone oriented by caster facing or towards the dest?",
     "model_a": "Trinity: caster position + caster orientation, dst ignored (Spell.cpp:1301)",
     "model_b": "cone axis = caster -> m_targets dst",
     "setup": "Wake of Ashes (405345:0, pair 18,104) or Shockwave (46968:0): turn the caster 90 degrees away from a dummy "
              "placed in front of the camera-facing direction while the client supplies a dst (if any)",
     "observable": "combat-log hits on dummies inside the facing cone vs inside the dst-oriented cone",
     "fidelity": "approximate"},
    {"id": "RX-A-02", "question": "Does AddTargetCombatReachToAOE / AreaEffectsUseTargetRadius enlarge the area test by target size?",
     "model_a": "Trinity: no reader; plain cylinder IsInRange2d (Spell.cpp:9432)",
     "model_b": "distance test adds the candidate's combat reach",
     "setup": "cast an area effect carrying 0x800 (see attributes.json witnesses) with a large-reach dummy just outside the radius",
     "observable": "hit / no hit in the combat log", "fidelity": "approximate"},
    {"id": "RX-A-03", "question": "Default cone angle for cone selectors with ConeDegrees 0",
     "model_a": "Trinity: 90 degrees (SpellMgr.cpp:5282)", "model_b": "180 degrees (enum comment for id 54) or other",
     "setup": "a current-player cone spell listed in selectors.json cone_defaults.defaulted_witnesses; dummies at 50 and 80 degrees off-axis",
     "observable": "which dummies are hit", "fidelity": "approximate"},
    {"id": "RX-A-04", "question": "(6,16) Mass Entanglement: is the B area centred on the explicit target or a client ground point?",
     "model_a": "Trinity: effect 0 (53) already set dst at the target, so B reads that dst (cross-effect)",
     "model_b": "independent client dst", "setup": "102359 on a target with a second enemy nearby",
     "observable": "rooted set", "fidelity": "exact"},
]

DEFECTS_A = [
    {"id": "TG-A-D1", "file_line": "Spell.cpp:1284-1287 vs SpellMgr.cpp:5281-5283",
     "description": "TARGET_UNIT_CONE_180_DEG_ENEMY 'ConeAngle 0 -> 180' is unreachable: load corrections already turned 0 into 90",
     "effect_on_recipients": "such cones use 90 degrees, not 180 (0 current-player effects use id 54)",
     "oracle_behaviour": "reproduce 90 and mark defect"},
    {"id": "TG-A-D2", "file_line": "Spell.cpp:1617-1619",
     "description": "TARGET_DEST_CASTER_RANDOM: dist = objSize + (dist - objSize) is an arithmetic no-op (lost rand factor)",
     "effect_on_recipients": "dest distance = CalcRadius max (already sqrt(rand_norm)-scaled at SpellInfo.cpp:822-826) "
                             "clamped to combat reach; 4 current-player effects",
     "oracle_behaviour": "reproduce; RNG draws only from CalcRadius and CalcDirectionAngle"},
    {"id": "TG-A-D3", "file_line": "Spell.cpp:1426-1435 with SpellInfo.cpp:140-244, Spell.cpp:377-381",
     "description": "TARGET_UNIT_AND_DEST_LAST_ENEMY sets dstSet without requesting DEST_LOCATION, then ModDst ASSERTs HasDst",
     "effect_on_recipients": "worldserver assertion unless an earlier selector wrote dst (0 current-player uses)",
     "oracle_behaviour": "FailClosed with defect note"},
    {"id": "TG-A-D4", "file_line": "Spell.cpp:1021",
     "description": "NYI debug log passes (Id, EffectIndex, target) to a (target, spell, effect) format", "effect_on_recipients": "none",
     "oracle_behaviour": "n/a"},
    {"id": "TG-A-D5", "file_line": "Spell.cpp:1301, 9455-9480",
     "description": "CASTER_TO_DEST cone ids never read m_targets dst (likely incomplete port; structural-inference)",
     "effect_on_recipients": "cone follows caster facing; 50+ current-player effects use id 104",
     "oracle_behaviour": "reproduce caster-facing cone, mark likely defect (RX-A-01)"},
]


def build_selectors() -> dict[str, Any]:
    from collections import defaultdict

    from . import context, selectors as S
    ctx = context.get()
    c = _census()
    rows = c["_rows"]
    used = defaultdict(lambda: {"as_a": 0, "as_b": 0, "witnesses": []})
    for r in rows:
        if not r["corrected"]:
            continue
        for slot, t in zip(("as_a", "as_b"), r["corrected"]):
            if t == 0:
                continue
            u = used[t]
            u[slot] += 1
            if not r["build_skew"] and len(u["witnesses"]) < 5:
                u["witnesses"].append(f"{r['spell']}:{r['effect']}")
    usage = []
    for t in sorted(used):
        v, tags = S.understood(t)
        usage.append({"id": t, "name": S.info(t).name, **used[t], "selector_path_verdict": v, "tags": tags,
                      "handler": S.handler(t)["function"]})
    effect_classes = {f"{r['spell']}:{r['effect']}": S.effect_class(r) for r in rows}
    cu_classes = {f"{r['spell']}:{r['effect']}": S.effect_class(r) for r in c["_cu_rows"]}
    for k in cu_classes:
        cu_classes[k]["tags"] = sorted(set(cu_classes[k]["tags"]) | {"controlled-unit"})
    # specific overrides from unknowns
    for key, cls in effect_classes.items():
        if 151 in cls["selectors"]:
            cls["unknowns"] = sorted(set(cls["unknowns"]) | {"TG-A-02"})
            cls["class"] = "unresolved"
        if any(t in (104, 136, 128, 129, 130) for t in cls["selectors"]):
            cls["unknowns"] = sorted(set(cls["unknowns"]) | {"TG-A-03"})
    ecount = Counter(v["class"] for v in effect_classes.values())
    return {
        "provenance": _provenance("selectors --out docs/research/targeting-corpora/selectors.json"),
        "statement": ("A known selector id is not a complete target policy: the id only selects a Spell::SelectImplicit* routine. "
                      "Recipients also depend on effect-mask grouping, the other selector, m_targets state from earlier "
                      "selectors/effects, explicit-target validation, relation/per-candidate/per-effect checks, caps, chains, "
                      "conditions and scripts (other tracks)."),
        "constants": {"TOTAL_SPELL_TARGETS": S.TOTAL_SPELL_TARGETS, "TOTAL_SPELL_EFFECTS": S.TOTAL_SPELL_EFFECTS,
                      "core_catalog_rows": 153, "evidence": "trinity-probe"},
        "axes": {"object": S.OBJECT, "reference": S.REFERENCE, "category": S.CATEGORY, "check": S.CHECK,
                 "direction": S.DIRECTION, "effect_implicit_target_type": S.EFFECT_IMPLICIT},
        "catalog": S.catalog(),
        "global_effect_dependence": S.GLOBAL_EFFECT_DEPENDENCE,
        "usage": usage,
        "census": _public(c),
        "cone_defaults": _cone_defaults(ctx, rows),
        "core_catalog_crosscheck": S.core_catalog_crosscheck(),
        "effect_classes": effect_classes,
        "effect_class_counts": dict(sorted(ecount.items())),
        "controlled_unit_effect_classes": cu_classes,
        "unknowns": UNKNOWNS_A,
        "retail_experiments": RETAIL_A,
        "trinity_defects": DEFECTS_A,
    }


def build_composition() -> dict[str, Any]:
    from . import composition, context
    doc = composition.build_corpus(context.get(), _census())
    doc["provenance"] = _provenance("composition --out docs/research/targeting-corpora/composition.json")
    doc["unknowns"] = [u for u in UNKNOWNS_A if u["id"] in ("TG-A-03",)]
    doc["retail_experiments"] = [x for x in RETAIL_A if x["id"] in ("RX-A-01", "RX-A-04")]
    doc["trinity_defects"] = [d for d in DEFECTS_A if d["id"] in ("TG-A-D3", "TG-A-D5")]
    return doc


def build_attributes() -> dict[str, Any]:
    from . import attributes, context
    doc = attributes.build_corpus(context.get(), _census())
    doc["provenance"] = _provenance("attributes --out docs/research/targeting-corpora/attributes.json")
    doc["unknowns"] = [u for u in UNKNOWNS_A if u["id"] in ("TG-A-04", "TG-A-05", "TG-A-06", "TG-A-07", "TG-A-08")]
    doc["retail_experiments"] = [x for x in RETAIL_A if x["id"] in ("RX-A-02", "RX-A-03")]
    doc["trinity_defects"] = [d for d in DEFECTS_A if d["id"] in ("TG-A-D1",)]
    return doc


def _out(p) -> None:
    p.add_argument("--out")


def _selector_args(p) -> None:
    p.add_argument("id", type=int)
    p.add_argument("--out")


def _selector(args) -> int:
    from . import composition, selectors as S
    info = S.info(args.id)
    v, tags = S.understood(args.id)
    row = next(r for r in S.catalog() if r["id"] == args.id)
    row.update({"verdict": v, "tags": tags, "reads_writes": composition.reads_writes(args.id)})
    emit(row, args.out)
    return 0


def _all(args) -> int:
    write_json(CORPORA / "selectors.json", build_selectors())
    write_json(CORPORA / "composition.json", build_composition())
    write_json(CORPORA / "attributes.json", build_attributes())
    return 0


COMMANDS = {
    "selectors": ("Track A: implicit-target catalog + current-player census", _out,
                  lambda a: emit(build_selectors(), a.out) or 0),
    "selector": ("Track A: one implicit-target id", _selector_args, _selector),
    "composition": ("Track A: TargetA/TargetB composition", _out, lambda a: emit(build_composition(), a.out) or 0),
    "attributes": ("Track A: targeting-relevant attributes and restrictions", _out,
                   lambda a: emit(build_attributes(), a.out) or 0),
    "track-a-all": ("Track A: write selectors/composition/attributes corpora", lambda p: None, _all),
}
