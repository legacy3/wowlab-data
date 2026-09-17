"""Track C commands: ``geometry``, ``area``, ``caps`` (current-player census corpora).

    cd scripts/research
    python3 targeting.py geometry --out ../../docs/research/targeting-corpora/geometry.json
    python3 targeting.py area     --out ../../docs/research/targeting-corpora/area.json
    python3 targeting.py caps     --out ../../docs/research/targeting-corpora/caps.json
"""

from __future__ import annotations

from collections import Counter, defaultdict
from functools import cache
from typing import Any

from . import PINS, FailClosed
from .cli import emit

SEARCH_CATEGORIES = ("NEARBY", "CONE", "AREA", "LINE")
GEOM_CATEGORIES = ("CONE", "LINE", "TRAJ")
# SelectImplicitCasterDestTargets branches that do not move by radius/direction (Spell.cpp:1471-1611)
CASTER_DEST_FIXED = {18, 9, 17, 39, 62, 125, 131, 106}
TARGET_DEST_FIXED = {53, 63, 132}               # Spell.cpp:1666-1669
DEST_DEST_FIXED = {28, 29, 88, 87}              # Spell.cpp:1700-1705
DEST_DEST_SPECIAL = {138: "height", 148: "towards-caster"}
RANDOM_DEST = {72, 74, 86}                      # SpellInfo.cpp:823-827
SPECIAL_AREA = {105: "caster-and-passengers", 118: "ally-or-raid", 120: "caster-and-summons",
                122: "threat-list", 123: "tap-list", 115: "furthest"}
TARGETING_MEMBERS = {"MaxAffectedTargets", "ConeAngle", "Width", "RadiusEntry", "TargetARadiusEntry",
                     "TargetBRadiusEntry", "TargetA", "TargetB", "ChainTargets"}

C_DEFECTS = [
    {"id": "TG-C-D01", "file_line": "src/server/game/Spells/SpellMgr.cpp:5281-5283 vs Spell.cpp:1284-1286",
     "description": "LoadSpellInfoCorrections sets ConeAngle=90 for every spell with a cone-category effect and "
                    "ConeAngle~0, so the TARGET_UNIT_CONE_180_DEG_ENEMY 0->180 default in SelectImplicitConeTargets is dead.",
     "effect_on_recipients": "CONE_180 effects with ConeDegrees 0 select a 90-degree cone, not 180.",
     "oracle_behaviour": "reproduced (area.cone_angle_degrees)"},
    {"id": "TG-C-D02", "file_line": "src/server/game/Spells/Spell.cpp:9505-9511, 1996",
     "description": "WorldObjectSpellLineTargetCheck::operator() calls WorldObjectSpellTargetCheck directly: no radius "
                    "Max/Min, no vertical bound, no AoETarget immunity; SearchTargets radius (Max, no +40) only chooses cells.",
     "effect_on_recipients": "units beyond the line length but inside visited grid cells (and AoE-untargetable units) are selected.",
     "oracle_behaviour": "reproduced (area.select_line); the fixture's visit_order must list exactly the visited-cell candidates"},
    {"id": "TG-C-D03", "file_line": "src/server/game/Entities/Object/Object.h:645, Spell.cpp:1440",
     "description": "ObjectDistanceOrderPred(ref, false) returns !(d1 < d2): not a strict weak ordering (UB for list::sort).",
     "effect_on_recipients": "FURTHEST_ENEMY ties are reordered by libstdc++ merge behaviour (probe: equal distances reversed), "
                             "changing which tied unit survives the truncating cap.",
     "oracle_behaviour": "reproduced with a libstdc++ list::sort emulation (caps.list_sort), probe-verified"},
    {"id": "TG-C-D04", "file_line": "src/server/game/Entities/Object/Position.cpp:180, Util.cpp:882",
     "description": "HasInArc normalises the arc with NormalizeOrientation; DegToRad(360) == 2*float(M_PI) exactly, "
                    "which normalises to 0.",
     "effect_on_recipients": "a 360-degree cone only keeps targets at relative angle exactly 0 or within the caster's "
                             "boundary radius (max(bounding radius, 2) yd, 3D).",
     "oracle_behaviour": "reproduced (geometry.has_in_arc), probe-verified"},
    {"id": "TG-C-D05", "file_line": "src/server/game/Spells/Spell.cpp:1716,1728; Object.cpp:2835; PathGenerator.cpp:91",
     "description": "SelectImplicitDestDestTargets moves the destination with MovePosition(pos=dst, from=m_caster): the angle is "
                    "offset by the caster's orientation and the MMAP raycast starts at the caster's position, not at dst.",
     "effect_on_recipients": "directional DEST_DEST offsets depend on caster facing/location, collision tested on the wrong segment.",
     "oracle_behaviour": "pre-collision XY candidate only (geometry.move_position_2d); collision is a fixture fact"},
    {"id": "TG-C-D06", "file_line": "src/server/game/Spells/SpellInfo.cpp:825-827",
     "description": "Random dest radius: Max = (Max - Min) * sqrt(rand_norm()) -- Min is subtracted but never added back.",
     "effect_on_recipients": "random offsets fall in [0, Max-Min] instead of [Min, Max].",
     "oracle_behaviour": "reproduced in oracle.calc_radius (Track G)"},
    {"id": "TG-C-D07", "file_line": "src/server/game/Spells/Spell.cpp:1618-1619",
     "description": "TARGET_DEST_CASTER_RANDOM: dist = objSize + (dist - objSize) is an arithmetic no-op.",
     "effect_on_recipients": "none beyond float rounding; the intended min distance is not applied.",
     "oracle_behaviour": "reproduced (no-op)"},
    {"id": "TG-C-D09", "file_line": "src/server/game/Spells/SpellMgr.cpp:3649-3655 (vs 5440-5444)",
     "description": "Legacy (WotLK) correction sets MaxAffectedTargets=4 on Divine Storm 53385, a current-player spell with no "
                    "DB2 MaxTargets; LoadSpellInfoTargetCaps gives the same spell a sqrt limit of 5.",
     "effect_on_recipients": "Trinity RandomResizes Divine Storm damage to 4 of the enemies in range (one draw set for the grouped "
                             "effects 0 and 1).",
     "oracle_behaviour": "oracle.from_data refuses corrected spells unless allow_corrections; caps reads the corrected value from "
                         "the fixture (spell override max_affected_targets=4)"},
    {"id": "TG-C-D08", "file_line": "src/server/game/Spells/SpellInfo.cpp:800-805, Spell.cpp:1673",
     "description": "CalcRadius without a caster returns RadiusMax (not Radius); TargetDest offsets call CalcRadius(nullptr).",
     "effect_on_recipients": "DEST_TARGET_* offsets ignore Radius/RadiusPerLevel/spell mods/movement bonus.",
     "oracle_behaviour": "reproduced in oracle.calc_radius(caster_id=None)"},
]


# ---------------------------------------------------------------------------
# shared scope enumeration
# ---------------------------------------------------------------------------
@cache
def _bindings():
    from dummy_semantics.bindings import BindingMap

    from . import context
    return BindingMap(context.get().bundle)


@cache
def _corrections():
    from dummy_semantics.corrections import Corrections

    from . import context
    return Corrections(context.get().bundle)


def _sel(tid: int) -> dict[str, Any]:
    from . import selectors
    try:
        s = selectors.info(tid)
    except FailClosed as exc:
        return {"id": tid, "name": None, "category": "INVALID", "reference": None, "object": None,
                "check": None, "direction": None, "error": str(exc)}
    return s.to_json()


def _radius(ctx, idx: int) -> dict[str, Any] | None:
    if not idx:
        return None
    row = ctx.data.radii.get(idx)
    if row is None:
        return {"index": idx, "missing": True}
    return {"index": idx, "radius": float(row["Radius"]), "per_level": float(row["RadiusPerLevel"]),
            "min": float(row["RadiusMin"]), "max": float(row["RadiusMax"])}


def radius_shape(r: dict[str, Any] | None) -> str:
    """Classify a SpellRadius row against CalcRadius (SpellInfo.cpp:797-806)."""
    if r is None:
        return "none"
    if r.get("missing"):
        return "missing-row"
    tags = []
    tags.append("per-level" if r["per_level"] else "flat")
    if r["max"] < r["radius"]:
        tags.append("max-clamps")
    elif r["max"] > r["radius"]:
        tags.append("max-exceeds-radius")
    if r["min"] > 0:
        tags.append("annulus-min")
    return "+".join(tags)


@cache
def scope_effects() -> tuple[dict[str, Any], ...]:
    """Every IsEffect DIFFICULTY_NONE effect of a current-player spell, with its selector facts."""
    from procs.enums import attr

    from . import context
    ctx = context.get()
    d = ctx.data
    conds = ctx.bundle.world.conditions_by_source().get(13, {})
    corr = _corrections()
    rows: list[dict[str, Any]] = []
    for sid in sorted(ctx.scope.reach):
        effs = [e for e in d.effects(sid) if e.is_effect]
        if not effs:
            continue
        info = ctx.catalog.get(sid)
        rest = d.restrictions(sid) or {}
        hooks = defaultdict(list)
        for b in _bindings().bindings(sid):
            for h in b.hooks:
                if h.list in ("OnObjectAreaTargetSelect", "OnObjectTargetSelect", "OnDestinationTargetSelect"):
                    hooks[h.list].append({"script": b.script_name, "handler": h.handler, "target": h.eff_value,
                                          "mask": h.affected_mask})
        fix_members = sorted({w["member"] for f in corr.by_spell.get(sid, []) for w in f["writes"]}
                             & TARGETING_MEMBERS)
        corrected_cap = None
        for f in corr.by_spell.get(sid, []):
            for w in f["writes"]:
                if w["member"] == "MaxAffectedTargets" and w["value"].strip().isdigit():
                    corrected_cap = int(w["value"])
        fix_lines = sorted({f["line"] for f in corr.by_spell.get(sid, [])
                            if {w["member"] for w in f["writes"]} & TARGETING_MEMBERS})

        def has(name: str) -> bool:
            return bool(info is not None and info.has_attr(attr(name)))

        spell_attrs = {k: has(k) for k in (
            "SPELL_ATTR8_CAN_HIT_AOE_UNTARGETABLE", "SPELL_ATTR3_ONLY_ON_PLAYER", "SPELL_ATTR5_NOT_ON_PLAYER",
            "SPELL_ATTR3_ONLY_ON_GHOSTS", "SPELL_ATTR4_USE_FACING_FROM_SPELL", "SPELL_ATTR9_NO_MOVEMENT_RADIUS_BONUS",
            "SPELL_ATTR9_FORCE_DEST_LOCATION", "SPELL_ATTR3_IGNORE_CASTER_MODIFIERS")}
        cond_masks = sorted({int(c["SourceGroup"]) for c in conds.get(sid, [])})
        for e in effs:
            a, b = _sel(e.target_a), _sel(e.target_b)
            hit_hooks = {lst: [h for h in hs if h["mask"] is None or h["mask"] & (1 << e.index)]
                         for lst, hs in hooks.items()}
            rows.append({
                "spell": sid, "effect": e.index, "effect_type": e.effect, "aura": e.aura,
                "build_skew": ctx.is_skew(sid), "target_a": a, "target_b": b,
                "radius_a": _radius(ctx, e.radius_a), "radius_b": _radius(ctx, e.radius_b),
                "cone_degrees": float(rest.get("ConeDegrees", 0.0) or 0.0), "width": float(rest.get("Width", 0.0) or 0.0),
                "max_targets": corrected_cap if corrected_cap is not None else int(rest.get("MaxTargets", 0) or 0),
                "max_targets_db2": int(rest.get("MaxTargets", 0) or 0), "players_only": bool(e.attributes & 0x4000),
                "attrs": spell_attrs, "pos_facing": e.pos_facing,
                "conditions": any(m & (1 << e.index) for m in cond_masks),
                "hooks": {k: v for k, v in hit_hooks.items() if v},
                "correction_members": fix_members, "correction_lines": fix_lines,
                "effect_attributes": e.attributes, "radius_idx": (e.radius_a, e.radius_b),
                "conditions_mask": cond_masks,
            })
    _annotate_groups(rows)
    return tuple(rows)


def _annotate_groups(rows: list[dict[str, Any]]) -> None:
    """Approximate the effect-mask grouping (Spell.cpp:741-784) for the census.

    Structural: CheckScriptEffectImplicitTargets is approximated by "no bound target hooks",
    CalcRadius equality by equal radius rows (level-independent rows only), conditions
    identity by equal SourceGroup sets.
    """
    by_spell: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by_spell[r["spell"]].append(r)
    for effs in by_spell.values():
        effs.sort(key=lambda r: r["effect"])
        processed: set[int] = set()
        for i, r in enumerate(effs):
            members = [r["effect"]]
            for o in effs[i + 1:]:
                if (o["target_a"]["id"], o["target_b"]["id"]) != (r["target_a"]["id"], r["target_b"]["id"]):
                    continue
                if o["conditions"] != r["conditions"] or o["players_only"] != r["players_only"]:
                    continue
                if r["hooks"] or o["hooks"]:
                    continue
                if (r["target_a"]["category"] in SEARCH_CATEGORIES or r["target_b"]["category"] in SEARCH_CATEGORIES) \
                        and (_rv(r["radius_a"]), _rv(r["radius_b"])) != (_rv(o["radius_a"]), _rv(o["radius_b"])):
                    continue
                members.append(o["effect"])
            members = [m for m in members if m not in processed]
            r["group"] = members
            r["group_leader"] = bool(members) and members[0] == r["effect"]
            r["group_size"] = len(members)
            processed.update(members)


def _rv(r):
    if r is None:
        return None
    if r.get("missing"):
        return ("missing",)
    return (r["radius"], r["per_level"], r["min"], r["max"])


def _cats(r) -> set[str]:
    return {r["target_a"]["category"], r["target_b"]["category"]}


def dest_kind(sel: dict[str, Any]) -> str | None:
    """Which destination routine a DEFAULT-category DEST selector uses and whether it moves."""
    if sel["category"] != "DEFAULT" or sel["object"] != "DEST":
        return None
    tid, ref = sel["id"], sel["reference"]
    if ref == "CASTER":
        if tid in CASTER_DEST_FIXED:
            return "caster-fixed" if tid not in (62, 125) else "caster-ground-height"
        return "caster-offset"
    if ref == "TARGET":
        return "target-fixed" if tid in TARGET_DEST_FIXED else "target-offset"
    if ref == "DEST":
        if tid in DEST_DEST_FIXED:
            return "dest-fixed"
        return "dest-" + DEST_DEST_SPECIAL.get(tid, "offset")
    return f"dest-ref-{ref}"


# ---------------------------------------------------------------------------
# per-effect classification (BRIEF §10)
# ---------------------------------------------------------------------------
def classify(r: dict[str, Any]) -> dict[str, Any] | None:
    cats = _cats(r)
    sels = [r["target_a"], r["target_b"]]
    dests = [dest_kind(s) for s in sels]
    applies = bool(cats & {"AREA", "CONE", "LINE", "TRAJ", "NEARBY"}) or any(
        k and k not in ("caster-fixed", "target-fixed", "dest-fixed") for k in dests) or r["max_targets"] > 0
    if not applies:
        return None
    tags: set[str] = set()
    unknowns: set[str] = set()
    verdict = "understood"
    worst = {"understood": 0, "understood-with-defect": 1, "fixture-dependent": 2, "unresolved": 3, "blocked": 4}

    def bump(v: str) -> None:
        nonlocal verdict
        if worst[v] > worst[verdict]:
            verdict = v

    for s, dk in zip(sels, dests):
        cat = s["category"]
        if cat == "INVALID":
            tags.add("invalid-selector")
            bump("blocked")
            continue
        if cat == "AREA":
            tags.add("area")
            tags.add("ref-" + (s["reference"] or "none").lower())
            if s["id"] in SPECIAL_AREA:
                tags.add(SPECIAL_AREA[s["id"]])
                if s["id"] in (105, 122, 123):
                    bump("fixture-dependent")
                if s["id"] == 115:
                    bump("understood-with-defect")
            if s["object"] == "GOBJ" or s["object"] == "GOBJ_ITEM":
                tags.add("gameobject")
                bump("fixture-dependent")
            if s["object"] == "UNIT_AND_DEST":
                tags.add("unit-and-dest")
            if s["reference"] == "LAST":
                tags.add("last-target")
        elif cat == "CONE":
            tags.add("cone")
            if r["width"]:
                tags.add("cone-line")
            deg = r["cone_degrees"] if r["cone_degrees"] else 90.0
            if deg >= 360.0:
                tags.add("cone-360")
                unknowns.add("TG-C-U03")
                bump("understood-with-defect")
            if s["id"] == 54 and not r["cone_degrees"]:
                tags.add("cone-180-default-dead")
                bump("understood-with-defect")
            if r["cone_degrees"] < 0:
                tags.add("cone-back")
        elif cat == "LINE":
            tags.add("line")
            unknowns.add("TG-C-U04")
            bump("understood-with-defect")
        elif cat == "TRAJ":
            tags.update({"traj", "world-geometry"})
            bump("fixture-dependent")
        elif cat == "NEARBY":
            tags.add("nearby")
            if s["object"] in ("DEST", "GOBJ"):
                bump("fixture-dependent")
        if dk and dk not in ("caster-fixed", "target-fixed", "dest-fixed"):
            tags.update({"dest", "world-geometry"})
            if s["direction"] and s["direction"] not in ("NONE", "FRONT"):
                tags.add("dir-" + s["direction"].lower())
            if s["id"] in RANDOM_DEST or s["direction"] == "RANDOM":
                tags.add("random-dest")
                unknowns.add("TG-C-U05")
            if dk.startswith("dest-offset"):
                bump("understood-with-defect")
            bump("fixture-dependent")
    for key in ("radius_a", "radius_b"):
        shape = radius_shape(r[key])
        if "annulus-min" in shape and cats & {"AREA", "CONE"}:
            tags.add("radius-min-annulus")
            unknowns.add("TG-C-U01")
        if "per-level" in shape:
            tags.add("radius-per-level")
            bump("fixture-dependent")
        if shape == "none" and ((key == "radius_a" and {"AREA", "CONE"} & {r["target_a"]["category"]})
                                or (key == "radius_b" and r["radius_a"] is None
                                    and {"AREA", "CONE"} & {r["target_b"]["category"]})):
            tags.add("radius-none")
        if shape == "missing-row":
            tags.add("radius-missing-row")
    if r["max_targets"]:
        tags.add("random-cap" if cats & {"AREA", "CONE"} and not {115} & {s["id"] for s in sels} else "cap")
        if not cats & {"AREA", "CONE", "LINE"}:
            tags.add("cap-unused-by-selector")
    if r.get("group_size", 1) > 1:
        tags.add("grouped")
    if r["conditions"]:
        tags.add("condition")
        unknowns.add("TG-C-U06")
        bump("blocked")
    if r["hooks"]:
        tags.add("script-adapter")
        bump("fixture-dependent")
    if r["correction_members"]:
        tags.add("correction")
        if "MaxAffectedTargets" in r["correction_members"] and r["max_targets"] != r["max_targets_db2"]:
            tags.add("legacy-cap-correction")
            bump("understood-with-defect")
    if r["players_only"] or r["attrs"]["SPELL_ATTR3_ONLY_ON_PLAYER"]:
        tags.add("players-only")
    if r["attrs"]["SPELL_ATTR8_CAN_HIT_AOE_UNTARGETABLE"]:
        tags.add("hits-aoe-untargetable")
    return {"class": verdict, "tags": sorted(tags), "unknowns": sorted(unknowns), "build_skew": r["build_skew"]}


def effect_classes() -> dict[str, Any]:
    out = {}
    for r in scope_effects():
        c = classify(r)
        if c is not None:
            out[f"{r['spell']}:{r['effect']}"] = c
    return dict(sorted(out.items(), key=lambda kv: tuple(int(x) for x in kv[0].split(":"))))


def _provenance(cmd: str) -> dict[str, Any]:
    return {"pins": PINS, "command": f"cd scripts/research && python3 targeting.py {cmd}",
            "track": "C (area targeting, geometry, target caps)"}


def _per_spec(pred) -> dict[str, int]:
    from . import context
    ctx = context.get()
    rows = scope_effects()
    hits = {r["spell"] for r in rows if pred(r)}
    return {str(spec): sum(1 for s in spells if s in hits) for spec, spells in sorted(ctx.scope.specs_reach.items())}


# ---------------------------------------------------------------------------
# unknowns / experiments (shared text)
# ---------------------------------------------------------------------------
UNKNOWNS = [
    {"id": "TG-C-U01", "subject": "SpellRadius.RadiusMin on AREA/CONE effects", "evidence": "trinity-consumer",
     "known": "Trinity uses RadiusMin as an inner exclusion radius (IsInRange2d minRange, Spell.cpp:9429; SpellInfo.cpp:797).",
     "unknown": "Whether Retail treats RadiusMin as an annulus or as a clamp floor for the level-scaled radius.",
     "why_unresolved": "No client consumer text; 254 of 375 SpellRadius rows have RadiusMin>0 with Radius==RadiusMax, which is "
                       "more consistent with a clamp than with rings.",
     "reopen_condition": "Retail experiment TG-C-X01 or a client disassembly of the radius read.", "build_skew": "n/a"},
    {"id": "TG-C-U02", "subject": "area vertical bound", "evidence": "trinity-consumer",
     "known": "Trinity: |dz| <= RadiusMax (no reach), 2D radius + target combat reach (Spell.cpp:9429).",
     "unknown": "Retail vertical policy (sphere vs cylinder, reach inclusion).",
     "why_unresolved": "no Retail consumer", "reopen_condition": "TG-C-X02", "build_skew": "n/a"},
    {"id": "TG-C-U03", "subject": "360-degree cones", "evidence": "trinity-probe",
     "known": "DegToRad(360) normalises to arc 0 (TG-C-D04).", "unknown": "Retail: presumably full circle.",
     "why_unresolved": "Trinity defect; Retail observation needed", "reopen_condition": "TG-C-X03", "build_skew": "n/a"},
    {"id": "TG-C-U04", "subject": "LINE selectors", "evidence": "trinity-consumer",
     "known": "Line length unbounded except by visited cells (TG-C-D02).",
     "unknown": "Retail line length (radius Max) and width semantics (Width + target reach, strict).",
     "why_unresolved": "which cells Cell::Visit covers is map data; Retail unobserved",
     "reopen_condition": "TG-C-X04", "build_skew": "n/a"},
    {"id": "TG-C-U05", "subject": "random / directional destinations", "evidence": "structural-inference",
     "known": "pre-collision XY candidate reproduced; height/VMAP/MMAP steps are world data.",
     "unknown": "final dest position", "why_unresolved": "no world geometry model (by design)",
     "reopen_condition": "fixture supplies the post-collision dest", "build_skew": "n/a"},
    {"id": "TG-C-U06", "subject": "implicit-target conditions on searched selectors", "evidence": "world-db-fact",
     "known": "conditions narrow GetSearcherTypeMask and filter candidates (Spell.cpp:2157-2158, 9392).",
     "unknown": "GetSearcherTypeMaskForConditionList not ported", "why_unresolved": "ConditionMgr out of scope (BRIEF §7)",
     "reopen_condition": "a ConditionMgr searcher-mask port", "build_skew": "n/a"},
    {"id": "TG-C-U07", "subject": "grid visit order", "evidence": "structural-inference",
     "known": "world container before grid container; standing cell first; newest-first within a cell (area.py docstring).",
     "unknown": "the concrete order for a scene (cell membership, insertion times)",
     "why_unresolved": "map-internal state", "reopen_condition": "never from static data; fixtures state visit_order",
     "build_skew": "n/a"},
]
EXPERIMENTS = [
    {"id": "TG-C-X01", "question": "Does RadiusMin exclude close targets from an area effect?",
     "model_a": "Trinity: targets closer than RadiusMin+reach are excluded", "model_b": "RadiusMin is only a clamp floor",
     "setup": "cast a current-player AoE whose radius row has RadiusMin>0 (see area.json radius_min_examples) with one dummy "
              "adjacent to the centre and one at mid radius", "observable": "combat log hits on the adjacent dummy",
     "fidelity": "exact"},
    {"id": "TG-C-X02", "question": "Vertical extent of ground AoE",
     "model_a": "cylinder |dz| <= Max", "model_b": "3D sphere with reach",
     "setup": "dummies on a ledge at dz = 0.9*R, horizontal distance 0.5*R", "observable": "combat log hit",
     "fidelity": "approximate"},
    {"id": "TG-C-X03", "question": "Does a 360-degree cone hit targets behind the caster?",
     "model_a": "Trinity: only exact-front / within boundary radius", "model_b": "full circle",
     "setup": "current-player cone spell with ConeDegrees 360 (geometry.json cone_360), dummy behind caster at 5 yd",
     "observable": "combat log hit", "fidelity": "exact"},
    {"id": "TG-C-X04", "question": "Line effect length and width inclusion",
     "model_a": "Trinity: unbounded length within visited cells, width + target reach, strict",
     "model_b": "length = radius Max, width = Width/2 each side",
     "setup": "dummies along the facing line at R+5 and laterally at Width/2 + 0.5",
     "observable": "combat log hits", "fidelity": "approximate"},
    {"id": "TG-C-X05", "question": "Area radius boundary inclusion and combat-reach addition",
     "model_a": "Trinity: centre distance < R + target reach (strict, 2D)", "model_b": "distance <= R (no reach)",
     "setup": "large-reach dummy (e.g. reach 5) at centre distance R + 3", "observable": "combat log hit",
     "fidelity": "approximate"},
    {"id": "TG-C-X06", "question": "Cap selection: random among all in range vs nearest-first",
     "model_a": "Trinity: RandomResize (uniform, order preserving) for AREA/CONE; nearest-first for LINE",
     "model_b": "nearest-first (Retail 'prioritise' behaviour for some capped AoEs)",
     "setup": "capped AoE (caps.json top capped spells) against cap+3 dummies at graded distances, 50 casts",
     "observable": "hit frequency per dummy", "fidelity": "approximate"},
]


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------
def _geometry(args) -> int:
    from . import context
    from . import geometry as g
    ctx = context.get()
    rows = scope_effects()
    cone = [r for r in rows if "CONE" in _cats(r)]
    line = [r for r in rows if "LINE" in _cats(r)]
    traj = [r for r in rows if "TRAJ" in _cats(r)]
    dest_rows = [(r, s, dest_kind(s)) for r in rows for s in (r["target_a"], r["target_b"]) if dest_kind(s)]
    cone_deg = Counter(r["cone_degrees"] for r in cone)
    cone_sel = Counter(s["name"] for r in cone for s in (r["target_a"], r["target_b"]) if s["category"] == "CONE")
    line_sel = Counter(s["name"] for r in line for s in (r["target_a"], r["target_b"]) if s["category"] == "LINE")
    dest_counts = Counter((k, s["name"]) for _, s, k in dest_rows)

    def ids(rs):
        return sorted({f"{r['spell']}:{r['effect']}" for r in rs}, key=lambda k: tuple(map(int, k.split(":"))))

    probe_constants = {
        "M_PI_F": g.M_PI_F.hex(), "TWO_PI_F": g.TWO_PI_F.hex(), "DegToRad(360)": g.deg_to_rad(360.0).hex(),
        "NormalizeOrientation(DegToRad(360))": g.normalize_orientation(g.deg_to_rad(360.0)).hex(),
        "DegToRad(90)": g.deg_to_rad(90.0).hex(), "DegToRad(180)": g.deg_to_rad(180.0).hex(),
        "libm_float_functions": g.LIBM,
    }
    payload = {
        "provenance": _provenance("geometry --out ../../docs/research/targeting-corpora/geometry.json"),
        "semantics": {
            "angles": "radians, binary32; orientations normalised to [0, 2pi) (Position.cpp:207); HasInArc takes the FULL arc and "
                      "accepts [-arc/2, +arc/2] inclusive after normalising the arc (Position.cpp:173-190)",
            "cone": "apex = caster position, orientation = caster orientation (Spell.cpp:1301); half-angle = ConeAngle/2; "
                    "ConeAngle<0 selects the back arc via != fuzzyGe(angle,0); units within caster->IsWithinBoundaryRadius "
                    "(3D, max(bounding,2), strict) bypass the arc; then the area cylinder around the caster (Spell.cpp:9459-9479)",
            "cone_line": "Width != 0 sets SPELL_ATTR0_CU_CONE_LINE (SpellMgr.cpp:3244): HasInLine(target, target reach, Width), "
                         "front half-plane, |sin| * dist2d < Width + reach (strict), plus the area cylinder",
            "line": "src = caster, orientation = caster->GetAbsoluteAngle(dst) unless *src == *dst (fuzzy, incl. orientation); "
                    "HasInLine(target, reach, Width or caster reach); NO radius test (TG-C-D02)",
            "traj": "HasInLine from caster with TRAJECTORY_MISSILE_SIZE 3 and 2D exact dist <= dist2d; only clips the destination "
                    "(Spell.cpp:1878-1961), world height/pitch dependent",
            "dest_offsets": "MovePosition / MovePositionToFirstCollision: angle += anchor orientation; XY candidate = pos + "
                            "dist*(cos,sin) in float; height, MMAP raycast, VMAP hit pos, ground are world data (fixture facts)",
            "2d_3d": {"area_units": "2D radius + |dz| <= Max cylinder", "area_gameobjects": "GameObject::IsInRange geobox (world data)",
                      "nearby": "3D GetDistance minus target reach", "boundary_radius": "3D", "sort": "3D squared",
                      "line/cone arc": "2D"},
            "boundaries": {"area max": "<", "area min": "not <", "vertical": "<=", "arc": "<= both ends", "line width": "<",
                           "traj length": "not >", "nearby": "<"},
        },
        "probe_constants": probe_constants,
        "census": {
            "effects_with_cone": len(cone), "effects_with_line": len(line), "effects_with_traj": len(traj),
            "cone_degrees": {str(k): v for k, v in sorted(cone_deg.items())},
            "cone_selectors": dict(sorted(cone_sel.items())), "line_selectors": dict(sorted(line_sel.items())),
            "cone_with_width": sum(1 for r in cone if r["width"]),
            "cone_360": ids([r for r in cone if r["cone_degrees"] >= 360]),
            "cone_180_default_dead": ids([r for r in cone if not r["cone_degrees"] and any(
                s["id"] == 54 for s in (r["target_a"], r["target_b"]))]),
            "cone_negative": ids([r for r in cone if r["cone_degrees"] < 0]),
            "dest_selectors": [{"kind": k, "selector": n, "effects": c} for (k, n), c in sorted(dest_counts.items())],
            "dest_moving_effects": len({(r["spell"], r["effect"]) for r, _, k in dest_rows
                                        if k not in ("caster-fixed", "target-fixed", "dest-fixed")}),
            "force_dest_location": ids([r for r, _, k in dest_rows if r["attrs"]["SPELL_ATTR9_FORCE_DEST_LOCATION"]]),
            "use_facing_from_spell": ids([r for r in rows if r["attrs"]["SPELL_ATTR4_USE_FACING_FROM_SPELL"]
                                          and (dest_kind(r["target_a"]) or dest_kind(r["target_b"])
                                               or _cats(r) & {"AREA", "TRAJ"})]),
            "per_spec_cone_spells": _per_spec(lambda r: "CONE" in _cats(r)),
            "per_spec_line_spells": _per_spec(lambda r: "LINE" in _cats(r)),
            "cones": ids(cone), "lines": ids(line), "trajs": ids(traj),
        },
        "trinity_defects": [d for d in C_DEFECTS if d["id"] in ("TG-C-D01", "TG-C-D02", "TG-C-D04", "TG-C-D05", "TG-C-D07", "TG-C-D08")],
        "unknowns": [u for u in UNKNOWNS if u["id"] in ("TG-C-U03", "TG-C-U04", "TG-C-U05")],
        "retail_experiments": [x for x in EXPERIMENTS if x["id"] in ("TG-C-X03", "TG-C-X04")],
        "effect_classes": {k: v for k, v in effect_classes().items()
                           if {"cone", "line", "traj", "dest"} & set(v["tags"])},
    }
    del ctx
    emit(payload, args.out)
    return 0


def _area(args) -> int:
    rows = scope_effects()
    area = [r for r in rows if "AREA" in _cats(r)]
    ref = Counter(s["reference"] for r in area for s in (r["target_a"], r["target_b"]) if s["category"] == "AREA")
    checks = Counter(s["check"] for r in area for s in (r["target_a"], r["target_b"]) if s["category"] == "AREA")
    sels = Counter(s["name"] for r in area for s in (r["target_a"], r["target_b"]) if s["category"] == "AREA")
    shapes = Counter()
    min_examples = []
    for r in area:
        for s, key in ((r["target_a"], "radius_a"), (r["target_b"], "radius_b")):
            if s["category"] != "AREA":
                continue
            # CalcRadius falls back to the A entry when B has none (SpellInfo.cpp:790-794)
            entry = r[key] if (key == "radius_a" or r["radius_b"] is not None) else r["radius_a"]
            shp = radius_shape(entry)
            shapes[shp] += 1
            if "annulus-min" in shp:
                min_examples.append({"effect": f"{r['spell']}:{r['effect']}", "selector": s["name"],
                                     "radius": entry, "max_targets": r["max_targets"]})
    from procs.enums import effect as eff_id
    healing = {eff_id(n) for n in ("HEAL", "HEAL_PCT", "HEAL_MAX_HEALTH")}
    damage = {eff_id(n) for n in ("SCHOOL_DAMAGE", "HEALTH_LEECH", "ENVIRONMENTAL_DAMAGE", "WEAPON_DAMAGE",
                                  "NORMALIZED_WEAPON_DMG", "WEAPON_PERCENT_DAMAGE", "WEAPON_DAMAGE_NOSCHOOL")}
    payload = {
        "provenance": _provenance("area --out ../../docs/research/targeting-corpora/area.json"),
        "semantics": {
            "reference": {"CASTER": "referer=caster, centre=caster", "TARGET": "referer=explicit unit (none -> no targets), centre=it",
                          "SRC": "referer=caster, centre=m_targets src", "DEST": "referer=caster, centre=m_targets dst",
                          "LAST": "referer=last m_UniqueTargetInfo entry carrying this effect (else caster), centre=it"},
            "radius": "SpellEffectInfo::CalcRadius(m_caster, targetIndex) * RadiusMod; Min=RadiusMin, Max=min(Radius+PerLevel*level, "
                      "RadiusMax) for unit casters, SpellModOp::Radius on Max, +/-2 movement bonus unless "
                      "SPELL_ATTR9_NO_MOVEMENT_RADIUS_BONUS; B uses A's entry (and A's target for the RANDOM test) when B has none",
            "predicate": "units/corpses: IsInRange2d(centre, Min, Max) with the TARGET's combat reach added to both bounds, strict "
                         "squared-float compare, and |dz| <= Max; AoETarget immunity unless SPELL_ATTR8_CAN_HIT_AOE_UNTARGETABLE; "
                         "then CheckTarget(implicit) + relation + corpse alive test + conditions",
            "phase": "no phase filter (AlwaysVisible searcher)",
            "caster_inclusion": "no special exclusion: the caster is a candidate like any other (relation/CheckTarget decide; "
                                "SPELL_ATTR1_EXCLUDE_CASTER in CheckTarget)",
            "order": "search (visit order) -> UNIT_AND_DEST ModDst(referer) -> OnObjectAreaTargetSelect -> FURTHEST sort -> cap -> "
                     "AddUnitTarget(checkIfValid=false, losPosition=centre) (dedup by GUID: masks OR-ed)",
            "search_radius": "Max + 40 when Max > 0 (cells only)",
        },
        "census": {
            "effects_with_area": len(area), "area_references": dict(sorted(ref.items(), key=lambda kv: str(kv[0]))),
            "area_checks": dict(sorted(ref_items(checks))), "area_selectors": dict(sorted(sels.items(), key=lambda kv: str(kv[0]))),
            "radius_shapes": dict(sorted(shapes.items())), "radius_min_examples": min_examples[:40],
            "radius_min_effects": len(min_examples),
            "special_area_selectors": {v: sorted(f"{r['spell']}:{r['effect']}" for r in area
                                                 if k in (r["target_a"]["id"], r["target_b"]["id"]))
                                       for k, v in SPECIAL_AREA.items()},
            "unit_and_dest": sum(1 for r in area if "UNIT_AND_DEST" in (r["target_a"]["object"], r["target_b"]["object"])),
            "with_conditions": sum(1 for r in area if r["conditions"]),
            "with_area_hooks": sum(1 for r in area if "OnObjectAreaTargetSelect" in r["hooks"]),
            "players_only": sum(1 for r in area if r["players_only"] or r["attrs"]["SPELL_ATTR3_ONLY_ON_PLAYER"]),
            "can_hit_aoe_untargetable": sum(1 for r in area if r["attrs"]["SPELL_ATTR8_CAN_HIT_AOE_UNTARGETABLE"]),
            "damage_area_effects": sum(1 for r in area if r["effect_type"] in damage),
            "healing_area_effects": sum(1 for r in area if r["effect_type"] in healing),
            "grouped_area_effects": sum(1 for r in area if r.get("group_size", 1) > 1),
            "build_skew_area_effects": sum(1 for r in area if r["build_skew"]),
            "per_spec_area_spells": _per_spec(lambda r: "AREA" in _cats(r)),
            "nearby_effects": sum(1 for r in rows if "NEARBY" in _cats(r)),
        },
        "trinity_defects": [d for d in C_DEFECTS if d["id"] in ("TG-C-D03", "TG-C-D06")],
        "unknowns": [u for u in UNKNOWNS if u["id"] in ("TG-C-U01", "TG-C-U02", "TG-C-U06", "TG-C-U07")],
        "retail_experiments": [x for x in EXPERIMENTS if x["id"] in ("TG-C-X01", "TG-C-X02", "TG-C-X05")],
        "effect_classes": {k: v for k, v in effect_classes().items()
                           if {"area", "nearby"} & set(v["tags"])},
    }
    emit(payload, args.out)
    return 0


def ref_items(counter: Counter):
    return [(str(k), v) for k, v in counter.items()]


def _caps(args) -> int:
    from . import caps, context
    ctx = context.get()
    rows = scope_effects()
    flat = []
    for r in rows:
        cats = _cats(r)
        cat = next((c for c in ("AREA", "CONE", "LINE", "CHAIN", "NEARBY", "TRAJ") if c in cats), "OTHER")
        flat.append({"spell": r["spell"], "effect": r["effect"], "category": cat,
                     "group_size": r.get("group_size", 1), "group_leader": r.get("group_leader", False)})
    cen = caps.census(ctx, flat)
    capped_rows = [r for r in rows if r["max_targets"]]
    cap_rule = Counter()
    for r in capped_rows:
        ids = {r["target_a"]["id"], r["target_b"]["id"]}
        cats = _cats(r)
        if 115 in ids:
            cap_rule["furthest-truncate"] += 1
        elif cats & {"AREA", "CONE"}:
            cap_rule["random-resize"] += 1
        elif "LINE" in cats:
            cap_rule["nearest-truncate"] += 1
        else:
            cap_rule["no-capped-selector"] += 1
    payload = {
        "provenance": _provenance("caps --out ../../docs/research/targeting-corpora/caps.json"),
        "semantics": {
            "value": "SpellValue::MaxAffectedTargets (per cast): SpellTargetRestrictions.MaxTargets -> SpellModOp::MaxTargets "
                     "in the Spell ctor (Spell.cpp:507, flat mods add per matched family-flag bit, SpellInfo.cpp:2008) -> "
                     "SPELLVALUE_MAX_TARGETS replaces (Spell.cpp:8738). 0 = no cap. A flat mod on an uncapped spell creates a cap.",
            "scope": "one value per spell cast; applied independently at each AREA/CONE/LINE selector call (TargetA and TargetB, "
                     "each effect group); one draw set per group because grouped effects share one selection "
                     "(Spell.cpp:741-788); not shared or decremented across selectors",
            "order": {"AREA": "candidates -> filters (predicate in search) -> [ModDst] -> script hook -> [FURTHEST sort] -> cap -> AddUnitTarget",
                      "CONE": "candidates -> filters -> script hook -> RandomResize -> AddUnitTarget",
                      "LINE": "candidates -> filters -> script hook -> (max < size: sort nearest, truncate) -> AddUnitTarget",
                      "NEARBY": "single nearest; no cap", "CHAIN": "ChainTargets (+SpellModOp::ChainTargets); MaxAffectedTargets unused (Track D)",
                      "DEFAULT/unit": "no cap"},
            "random_resize": "Containers.h:67: size<=n -> no draws; else exactly size urand(1,remaining) draws, order preserved",
            "not_caps": ["LoadSpellInfoTargetCaps (SpellMgr.cpp:5407) = sqrt damage/heal diminishing (_LoadSqrtTargetLimit), "
                         "payload dependency, not recipient selection",
                         "Unit.cpp:3477 MaxAffectedTargets-1 = single-target aura limit (aura lifecycle)"],
            "difficulty": "only DIFFICULTY_NONE rows modelled; SpellInfo difficulty variants copy restrictions per difficulty "
                          "(current-player census is difficulty 0)",
        },
        "census": {**cen, "capped_effects": len(capped_rows), "cap_rule_effects": dict(sorted(cap_rule.items())),
                   "per_spec_capped_spells": _per_spec(lambda r: r["max_targets"] > 0)},
        "trinity_defects": [d for d in C_DEFECTS if d["id"] in ("TG-C-D03", "TG-C-D09")],
        "unknowns": [
            {"id": "TG-C-U08", "subject": "scripted cap writers/readers", "evidence": "script-consumer",
             "known": "script_sites lists every structural cap-touching line in Spells/Pet scripts",
             "unknown": "per-script semantics (Track E classifies)", "why_unresolved": "owned by Track E",
             "reopen_condition": "script-adapters.json", "build_skew": "n/a"},
            {"id": "TG-C-U09", "subject": "SpellModOp::MaxTargets aggregate", "evidence": "trinity-consumer",
             "known": "flat mods count once per matched family-flag bit; pct multiplies; uint32 truncation",
             "unknown": "which mod auras are active on a character (talent/state)", "why_unresolved": "runtime state",
             "reopen_condition": "fixture modifiers.max_targets", "build_skew": "n/a"},
        ],
        "retail_experiments": [x for x in EXPERIMENTS if x["id"] == "TG-C-X06"],
        "effect_classes": {k: v for k, v in effect_classes().items() if {"cap", "random-cap"} & set(v["tags"])},
    }
    emit(payload, args.out)
    return 0


def _out(p) -> None:
    p.add_argument("--out")


COMMANDS = {
    "geometry": ("Track C: cone/line/traj/dest geometry census + semantics", _out, _geometry),
    "area": ("Track C: area selection census + semantics", _out, _area),
    "caps": ("Track C: target-cap sources census + semantics", _out, _caps),
}
