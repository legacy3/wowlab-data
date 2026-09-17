"""Track I commands: ``areatriggers`` and ``extended-scope``.

    cd scripts/research
    python3 targeting.py areatriggers   --out ../../docs/research/targeting-corpora/areatriggers.json
    python3 targeting.py extended-scope --out ../../docs/research/targeting-corpora/extended-scope.json

``areatriggers`` classifies every current-player effect that creates an AreaTrigger
(SPELL_EFFECT_CREATE_AREATRIGGER 179 / _2 353, SPELL_AURA_AREA_TRIGGER 395).
``extended-scope`` is population only: spells cast by script hooks, AreaTrigger
scripts/actions and ``spell_linked_spell`` of current-player spells that are not in
``ctx.scope.reach``, with their selector census.  Its per-effect map is published under
``extended_effect_classes`` so the main census is not silently widened.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from functools import cache
from typing import Any

from . import CORPORA, PINS
from . import areatriggers as at
from .cli import emit

SPELL_LINK_NAMES = {0: "cast", 1: "hit", 2: "aura", 3: "remove"}
LINK_TARGET = {   # trinity-consumer
    "cast": {"explicit": "m_targets unit target, else the caster", "caster": "parent caster",
             "consumer": "src/server/game/Spells/Spell.cpp:3959", "reselects": True},
    "hit": {"explicit": "the hit unit (casts on itself; original caster = parent caster)", "caster": "hit unit",
            "consumer": "src/server/game/Spells/Spell.cpp:3360", "reselects": True},
    "aura": {"explicit": "none: caster->AddAura(child, aura target) -- no target selection", "caster": "aura caster",
             "consumer": "src/server/game/Spells/Auras/SpellAuras.cpp:1414", "reselects": False},
    "remove": {"explicit": "the aura target (casts on itself) unless removed by death", "caster": "aura target",
               "consumer": "src/server/game/Spells/Auras/SpellAuras.cpp:1428", "reselects": True},
}
REACH_SELECTOR_CATEGORIES = ("AREA", "CONE", "NEARBY", "LINE", "TRAJ", "CHANNEL")

I_DEFECTS = [
    {"id": "TG-I-D01", "file_line": "src/server/game/Entities/AreaTrigger/AreaTrigger.cpp:890, 910, 921",
     "description": "HandleUnitEnter / HandleUnitExitInternal dereference GetTemplate() (ActionSetId, HasActionSetFlag) "
                    "without a null check, while UpdateTargetList/DoActions guard it; create properties with "
                    "AreaTriggerId 0 load with Template == nullptr (AreaTriggerDataStore.cpp:185-205).",
     "effect_on_recipients": "a player entering (or an AT expiring with units inside) a template-less AT is a null "
                             "dereference; none of the current-player create-properties rows is template-less.",
     "oracle_behaviour": "not reachable from the census (all loaded rows have a template); synthetic fixtures without "
                         "a template skip the filters as UpdateTargetList does"},
    {"id": "TG-I-D02", "file_line": "src/server/scripts/Spells/spell_hunter.cpp:1351-1360; Object.cpp:335; Map.cpp:2580; "
                                    "AreaTrigger.cpp:868-873",
     "description": "areatrigger_hun_tar_trap_activate::OnUnitEnter calls at->Remove(): AddObjectToRemoveList -> "
                    "CleanupsBeforeDelete -> RemoveFromWorld runs immediately, re-entering HandleUnitEnterExit({}, "
                    "ByExpire) from inside the outer enter loop. The outer loop still calls DoActions + OnUnitEnter for "
                    "the remaining entering units (IsInWorld is only checked after the AI hook).",
     "effect_on_recipients": "N hostile units entering Tar Trap on the same tick cast 187700 N times (N slow fields); "
                             "units that had not been 'entered' yet receive exit hooks first.",
     "oracle_behaviour": "reported (evaluate_tick lists every entering unit; the script table marks the defect); "
                         "trinity-consumer reading, not probed"},
    {"id": "TG-I-D03", "file_line": "src/server/game/Entities/AreaTrigger/AreaTrigger.cpp:178-192, 505-508",
     "description": "Create applies only the SpellMod Radius multiplier (flat mods ignored) and stores it as an "
                    "active OverrideScaleCurve; CalcCurrentScale then skips ScaleCurveId entirely (else-if).",
     "effect_on_recipients": "a percentage radius mod on an AT with a scale curve freezes its size at the modifier; "
                             "flat radius mods never change AT recipients.",
     "oracle_behaviour": "reproduced (calc_current_scale)"},
    {"id": "TG-I-D04", "file_line": "src/server/scripts/Spells/spell_priest.cpp:2259-2300 vs SpellEffect 120517/120644",
     "description": "spell_pri_halo_effect_selector expects CREATE_AREATRIGGER on effects 0/1/3/4 and caster auras on "
                    "2/5; the 12.1 data has 179 on 0/2 and aura 395 on 1/3, so the prevent hooks on 1/3 never match "
                    "and effect 2 is never prevented.",
     "effect_on_recipients": "with 12.1 data a Halo cast creates up to four areatrigger_pri_halo ATs (two static, two "
                             "attached), each casting the damage/heal on every entering unit.",
     "oracle_behaviour": "reported as script-layout drift (class unresolved); structural-inference from the "
                         "registration text; the tree-sitter index flattens the Register()-time `if (selectedEffect != ...)` "
                         "conditions (lead correction: spell_priest.cpp *is* indexed, 140 registrations / 197 hooks)"},
]


def _provenance(cmd: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    p = {"pins": PINS, "command": cmd,
         "sources": [("docs/research/dummy-corpora/trinity-server-overlay.json (areatrigger_create_properties, "
                      "areatrigger_template, areatrigger_template_actions, spell_linked_spell, spell_script_names)"),
                     ("docs/research/targeting-corpora/inputs/areatrigger-world.json (polygon vertices, spline points, orbits, "
                      "conditions source 28; tools/tdb_world_extract.py)"),
                     "data/tables SpellEffect / SpellMisc / SpellDuration (12.1.0.69497)"]}
    if extra:
        p.update(extra)
    return p


@cache
def _durations() -> dict[int, int | None]:
    """SpellInfo::GetDuration (SpellInfo.cpp:3986): no DurationEntry -> 0 (non-passive) / -1 (passive)."""
    from . import context
    src = context.get().bundle.source
    dur = {r[0]: r[1] for r in src.project("SpellDuration", ("ID", "Duration"))}
    out: dict[int, int | None] = {}
    for sid, diff, idx, attr0 in src.project("SpellMisc", ("SpellID", "DifficultyID", "DurationIndex", "Attributes_0")):
        if diff != 0:
            continue
        d = dur.get(idx)
        if d is None:   # no DurationEntry: SPELL_ATTR0_PASSIVE (0x40) -> -1, else 0
            out[sid] = -1 if int(attr0) & 0x40 else 0
        else:
            out[sid] = -1 if d == -1 else abs(d)
    return out


def _selector_names(eff) -> list[str | None]:
    from . import selectors
    return [selectors.info(eff.target_a).name, selectors.info(eff.target_b).name]


# ---------------------------------------------------------------------------
# areatriggers
# ---------------------------------------------------------------------------
UNKNOWNS_I = [
    {"id": "TG-I-01", "subject": "AreaTriggers without a Trinity consumer (no AI script, no template action, no external script)",
     "evidence": "trinity-consumer", "known": "Trinity selects units into _insideUnits every tick but nothing reads them.",
     "unknown": "What Retail applies to those units (silence, pull, absorb, knockback ...).",
     "why_unresolved": "no consumer in Trinity != no Retail behaviour", "reopen_condition": "a server-side script or template action appears",
     "build_skew": False},
    {"id": "TG-I-02", "subject": "missing areatrigger_create_properties rows",
     "evidence": "world-db-fact", "known": "AreaTrigger::Create fails (AreaTrigger.cpp:130): no AT, no recipients.",
     "unknown": "Shape, template and actions of the Retail AT.", "why_unresolved": "not in the pinned TDB (+522 updates)",
     "reopen_condition": "TDB adds the row (or a sniff provides it)", "build_skew": "mixed (per row)"},
    {"id": "TG-I-03", "subject": "exit order / GetInsideUnits iteration order",
     "evidence": "trinity-consumer", "known": "GuidUnorderedSet (std::unordered_set<ObjectGuid>) iteration.",
     "unknown": "a deterministic order (depends on hash + bucket history)", "why_unresolved": "implementation-defined container order",
     "reopen_condition": "never for Trinity; Retail order via combat-log experiment", "build_skew": "n/a"},
    {"id": "TG-I-04", "subject": "curve-driven shapes and moving ATs (MorphCurveId, ScaleCurveId, MoveCurveId, splines, PathGenerator)",
     "evidence": "structural-inference", "known": "radius = lerp(start, target, curve(progress)) * scale; position from spline/orbit.",
     "unknown": "DB2Manager::GetCurveValueAt and Movement::Spline evaluation are not ported",
     "why_unresolved": "out of the targeting pass scope (fixture states progress / tick pose)",
     "reopen_condition": "a curve/spline port with a probe", "build_skew": False},
    {"id": "TG-I-05", "subject": "box half-height (AreaTrigger.cpp:761: extentsZ / 2) vs full X/Y extents",
     "evidence": "trinity-probe", "known": "Trinity compares |dx|<=X, |dy|<=Y, |dz|<=Z/2.",
     "unknown": "whether Retail box extents are half-sizes in all three axes", "why_unresolved": "no Retail oracle",
     "reopen_condition": "retail experiment TG-I-X02", "build_skew": "n/a"},
    {"id": "TG-I-06", "subject": "Halo 120517/120644 effect layout vs spell_pri_halo_effect_selector",
     "evidence": "structural-inference", "known": "TG-I-D04", "unknown": "which Halo effects Retail executes",
     "why_unresolved": "script written for an older effect layout", "reopen_condition": "script update", "build_skew": False},
    {"id": "TG-I-07", "subject": "aura-created ATs (SPELL_AURA_AREA_TRIGGER 395): attached AT follows the aura target",
     "evidence": "trinity-consumer", "known": "SpellAuraEffects.cpp:6385 creates at the target's position, Attached flag, "
                                             "AreaTrigger::Update relocates to the target each tick (:371-384)",
     "unknown": "per-tick pose of the carrier (movement)", "why_unresolved": "fixture fact", "reopen_condition": "n/a",
     "build_skew": False},
]

RETAIL_EXPERIMENTS_I = [
    {"id": "TG-I-X01", "question": "Is a unit at exactly radius + its combat reach from a ground AT inside?",
     "model_a": "Trinity: centre distance < R + reach (strict), 3D for spheres, 2D + height band for cylinders",
     "model_b": "distance <= R, no reach", "setup": "Blizzard/Consecration on a training dummy of known reach, "
     "step the player/dummy distance", "observable": "debuff 204242 / 12486 application", "fidelity": "approximate"},
    {"id": "TG-I-X02", "question": "Box AT vertical extent (full vs half)",
     "model_a": "Trinity: |dz| <= Z/2", "model_b": "|dz| <= Z", "setup": "box AT spell, target on a ledge",
     "observable": "enter aura", "fidelity": "insufficient"},
    {"id": "TG-I-X03", "question": "Does Blizzard damage select by the AT cylinder or by 190357's own radius?",
     "model_a": "Trinity: 190357 TARGET_UNIT_DEST_AREA_ENEMY radius around the AT position (AT shape unused)",
     "model_b": "AT inside units", "setup": "unit just outside 190357's radius but inside the AT cylinder (height band)",
     "observable": "combat log 190357 hit", "fidelity": "approximate"},
    {"id": "TG-I-X04", "question": "Tar Trap with two enemies entering on the same tick",
     "model_a": "Trinity: two 187700 casts (TG-I-D02)", "model_b": "one activation", "setup": "two dummies pulled together",
     "observable": "number of 187700 SPELL_CAST_SUCCESS", "fidelity": "exact"},
]


def _script_for(cp: at.CreateProperties | None) -> dict[str, Any] | None:
    if cp is None or not cp.script_name:
        return None
    s = at.AT_SCRIPTS.get(cp.script_name)
    return {"name": cp.script_name, "verified": s is not None, **(s or {})}


def _consumer_uses(spell: int, cp: at.CreateProperties | None) -> str:
    if cp is None:
        return "no-areatrigger"
    uses = []
    s = at.AT_SCRIPTS.get(cp.script_name) if cp.script_name else None
    if s:
        uses.append(s["uses"])
    if cp.actions:
        uses.append("template-actions")
    ext = at.EXTERNAL_CONSUMERS.get(spell)
    if ext and ext["uses"] not in ("lifecycle", "ai-state"):
        uses.append(f"external:{ext['uses']}")
    return "+".join(uses) or "none"


def classify_at_effect(spell: int, eff, cp: at.CreateProperties | None, skew: bool, duration: int | None) -> dict[str, Any]:
    tags = ["areatrigger", "world-db"]
    unknowns: list[str] = []
    if eff.effect == 6:
        tags.append("aura-attached")
    if cp is None:
        tags.append("missing-create-properties")
        return {"class": "blocked", "tags": sorted(set(tags)), "unknowns": ["TG-I-02"], "build_skew": skew}
    tags.append(cp.shape.kind)
    uses = _consumer_uses(spell, cp)
    script = at.AT_SCRIPTS.get(cp.script_name) if cp.script_name else None
    if script:
        tags.append("script-adapter")
    if cp.actions:
        tags.append("template-action")
    if "position-only" in uses:
        tags.append("child-reselects")
    if "inside-units" in uses:
        tags.append("tick-driven")
    moving = cp.movement != "none" or cp.curves["MoveCurveId"] or cp.curves["FacingCurveId"]
    dynamic = cp.shape.is_dynamic or (cp.curves["MorphCurveId"] and cp.shape.is_dynamic) or cp.curves["ScaleCurveId"]
    if moving:
        tags.append("moving")
    if dynamic:
        tags.append("curve")
    if duration == 0 and eff.effect != 6:
        tags.append("zero-duration")
    klass = "understood"
    if spell in (120517, 120644):
        tags.append("script-layout-drift")
        return {"class": "unresolved", "tags": sorted(set(tags)), "unknowns": ["TG-I-04", "TG-I-06"], "build_skew": skew}
    if uses == "none":
        tags.append("no-consumer")
        klass, unknowns = "unresolved", ["TG-I-01"]
    elif uses in ("external:none",):
        tags.append("at-unused")
        klass = "understood"
    elif cp.script_name == "areatrigger_hun_tar_trap_activate":
        klass = "understood-with-defect"
    if klass == "understood" and "inside-units" in uses and (moving or dynamic):
        klass, unknowns = "fixture-dependent", ["TG-I-04"]
    order = unordered_order_verdict(spell, cp)
    if order["recipient_order_depends"]:
        tags.append("unordered")
        unknowns = unknowns + ["TG-I-03"]
        if klass == "understood":
            klass = "fixture-dependent"
    elif order["exit_hooks"]:
        tags.append("exit-order-irrelevant")
    if eff.effect == 6 and klass == "understood" and "inside-units" in uses:
        klass, unknowns = "fixture-dependent", unknowns + ["TG-I-07"]
    return {"class": klass, "tags": sorted(set(tags)), "unknowns": sorted(set(unknowns)), "build_skew": skew}


#: consumers that CAST something per element while iterating a GuidUnorderedSet (TG-I-03): the per-tick
#: recipient *order* (cast order, RNG draw order, proc order) is implementation-defined.
UNORDERED_CAST_ITERATION = {
    "external:5740": "spell_warlock.cpp:958-970: union of GetInsideUnits (GuidUnorderedSet) -> CastSpell(42223) per unit",
    "script:at_hun_binding_shot": "spell_hunter.cpp:261-273: 1s task iterates GetInsideUnits -> unit casts 117614",
}
#: exit hooks that only remove auras per unit (no cast, no RNG, no cross-unit state): order provably irrelevant
EXIT_ONLY_REMOVALS = {
    "areatrigger_pal_consecration": "RemoveAurasDueToSpell(188370/204242, caster) per unit",
    "areatrigger_dh_darkness": "RemoveAura(209426, caster) per unit",
}


def unordered_order_verdict(spell: int, cp: at.CreateProperties) -> dict[str, Any]:
    """Does the recipient order of this AT's consumer depend on an unordered container (TG-I-03)?

    Exit iteration (AreaTrigger.cpp:871, GuidUnorderedSet) exists for every AT; it matters only if an exit
    hook does something order-sensitive.  Exit hooks at the pin: UndoActions (aura removal by caster GUID,
    :1190) and the AI OnUnitExit bodies in EXIT_ONLY_REMOVALS -- all per-unit independent removals.
    """
    reasons = []
    if spell in at.EXTERNAL_CONSUMERS and f"external:{spell}" in UNORDERED_CAST_ITERATION:
        reasons.append(UNORDERED_CAST_ITERATION[f"external:{spell}"])
    if cp.script_name and f"script:{cp.script_name}" in UNORDERED_CAST_ITERATION:
        reasons.append(UNORDERED_CAST_ITERATION[f"script:{cp.script_name}"])
    s = at.AT_SCRIPTS.get(cp.script_name) if cp.script_name else None
    exit_hooks = bool(cp.actions) or bool(s and s.get("exit"))
    if s and s.get("exit") and cp.script_name not in EXIT_ONLY_REMOVALS:
        reasons.append(f"unverified exit hook {cp.script_name}")
    return {"recipient_order_depends": bool(reasons), "reasons": reasons, "exit_hooks": exit_hooks,
            "exit_note": (EXIT_ONLY_REMOVALS.get(cp.script_name) or ("UndoActions aura removal" if cp.actions else None))
            if exit_hooks else None}


def areatrigger_effects(ctx) -> list[tuple[int, Any]]:
    out = []
    for s in sorted(ctx.scope.reach):
        for e in ctx.data.effects(s):
            if e.effect in at.AT_EFFECTS or (e.effect and e.aura == at.SPELL_AURA_AREA_TRIGGER):
                out.append((s, e))
    return out


def build_areatriggers() -> dict[str, Any]:
    from . import context
    ctx = context.get()
    w = at.ATWorld(ctx.bundle.world)
    durations = _durations()
    spec_of: dict[int, list[int]] = defaultdict(list)
    for spec, spells in ctx.scope.specs_reach.items():
        for s in spells:
            spec_of[s].append(spec)
    rows = []
    classes = {}
    for s, e in areatrigger_effects(ctx):
        cp = w.create_properties(e.misc0)
        skew = ctx.is_skew(s)
        key = f"{s}:{e.index}"
        klass = classify_at_effect(s, e, cp, skew, durations.get(s))
        classes[key] = klass
        row: dict[str, Any] = {
            "key": key, "spell": s, "effect_index": e.index, "name": ctx.name(s),
            "effect": e.effect, "aura": e.aura,
            "creator": "aura-attached (SpellAuraEffects.cpp:6385)" if e.effect == 6 else "spell-effect (SpellEffects.cpp:5410)",
            "selectors": _selector_names(e), "create_properties_id": e.misc0, "build_skew": skew,
            "base_duration_ms": durations.get(s), "specs": sorted(spec_of.get(s, [])),
            "class": klass["class"], "tags": klass["tags"],
            "evidence": "world-db-fact",
        }
        if cp is None:
            raw = w.cp_rows.get((e.misc0, 0))
            row["create_properties"] = None
            row["missing_reason"] = ("row absent from areatrigger_create_properties" if raw is None
                                     else "row rejected at load (invalid template reference or shape)")
        else:
            row["create_properties"] = {
                "template_id": cp.template_id, "template_present": cp.template is not None,
                "action_set_flags": cp.action_set_flags,
                "actions": [{"type": at.ACTION_TYPES.get(int(a["ActionType"])), "param": int(a["ActionParam"]),
                             "target": at.ACTION_USER_TYPES.get(int(a["TargetType"]))} for a in cp.actions],
                "conditions": len(cp.conditions), "flags": cp.flag_names(), "curves": cp.curves,
                "time_to_target_scale": cp.time_to_target_scale, "movement": cp.movement,
                "spline_points": cp.spline_points, "shape": cp.shape.to_dict(),
                "dynamic_shape": cp.shape.is_dynamic, "max_search_radius": at.max_search_radius(cp.shape),
                "check3d": cp.shape.kind == "sphere",
                "script_name": cp.script_name or None,
            }
            row["script"] = _script_for(cp)
            row["consumer_uses"] = _consumer_uses(s, cp)
            row["unordered_iteration"] = dict(unordered_order_verdict(s, cp), evidence="script-consumer")
        ext = at.EXTERNAL_CONSUMERS.get(s)
        if ext:
            row["external_consumer"] = dict(ext, evidence="script-consumer")
        rows.append(row)
    rows.sort(key=lambda r: (r["spell"], r["effect_index"]))
    counts = {
        "effects": len(rows),
        "spells": len({r["spell"] for r in rows}),
        "by_creator": dict(Counter("aura-395" if r["effect"] == 6 else f"effect-{r['effect']}" for r in rows)),
        "by_class": dict(Counter(r["class"] for r in rows)),
        "missing_create_properties": sum(1 for r in rows if r["create_properties"] is None),
        "missing_create_properties_build_skew": sum(1 for r in rows if r["create_properties"] is None and r["build_skew"]),
        "by_shape": dict(Counter(r["create_properties"]["shape"]["kind"] for r in rows if r["create_properties"])),
        "by_consumer": dict(Counter(r.get("consumer_uses", "no-areatrigger") for r in rows)),
        "with_script": sum(1 for r in rows if r.get("script")),
        "with_template_actions": sum(1 for r in rows if r["create_properties"] and r["create_properties"]["actions"]),
        "with_conditions": sum(1 for r in rows if r["create_properties"] and r["create_properties"]["conditions"]),
        "with_action_set_flags": sum(1 for r in rows if r["create_properties"] and r["create_properties"]["action_set_flags"]),
        "zero_base_duration": sum(1 for r in rows if "zero-duration" in r["tags"]),
        "db2_create_properties_rows": sum(1 for _ in ctx.bundle.source.project("AreaTriggerCreateProperties", ("ID",))),
        "db2_create_properties_rows_matching_census": len({r["create_properties_id"] for r in rows} &
                                                          {x[0] for x in ctx.bundle.source.project("AreaTriggerCreateProperties", ("ID",))}),
    }
    return {
        "schema": "targeting-areatriggers/1",
        "provenance": _provenance("python3 targeting.py areatriggers --out ../../docs/research/targeting-corpora/areatriggers.json",
                                  {"world_extract_provenance": {k: w.extract_provenance[k] for k in
                                                                ("tool", "trinitycore_commit", "base_world_database_sha256",
                                                                 "update_files_total")}}),
        "pipeline": PIPELINE,
        "scripts": {k: dict(v, evidence="script-consumer") for k, v in sorted(at.AT_SCRIPTS.items())},
        "external_consumers": {str(k): dict(v, evidence="script-consumer") for k, v in sorted(at.EXTERNAL_CONSUMERS.items())},
        "counts": counts,
        "rows": rows,
        "effect_classes": dict(sorted(classes.items(), key=lambda kv: tuple(map(int, kv[0].split(":"))))),
        "unknowns": UNKNOWNS_I,
        "retail_experiments": RETAIL_EXPERIMENTS_I,
        "trinity_defects": I_DEFECTS,
    }


PIPELINE = [
    {"stage": "create", "consumer": "SpellEffects.cpp:5410 / SpellAuraEffects.cpp:6385 / AreaTrigger.cpp:117-316",
     "evidence": "trinity-consumer",
     "text": "LAUNCH-mode effect needs a unit caster and m_targets dst; create properties {MiscValue, custom=false}; "
             "duration = CalcDuration(caster) (0 when the spell has no duration entry); missing create properties => "
             "no AT. Aura 395 creates at the aura target's position with the Attached flag. Phase shift inherited from "
             "the caster; SpellMod Radius multiplier -> OverrideScaleCurve."},
    {"stage": "tick", "consumer": "AreaTrigger.cpp:361-400", "evidence": "trinity-consumer",
     "text": "movement (override > orbit > attached > spline > facing curve) -> duration: expiry removes the AT "
             "before any target update -> AI OnUpdate -> UpdateTargetList. Selection is re-run every map update."},
    {"stage": "search", "consumer": "AreaTrigger.cpp:641-659, 717-851; GridNotifiers.h:1319; Object.cpp:410",
     "evidence": "trinity-probe",
     "text": "AnyUnitInObjectRangeCheck(AT, radius, check3D, reqAlive=false): centre distance < radius + unit combat "
             "reach (AT reach 0), 3D only for spheres. Box: 2D radius sqrt(X^2+Y^2) then IsWithinBox(AT pose, X, Y, Z/2). "
             "Cylinder/Disk: 2D radius then z in [z-h, z+h] (h scaled; LocationZOffset unused); disk excludes "
             "IsInDist2d(inner) without reach. Polygon: 2D radius = BoundsRadius2D*scale, |dz|<=h, even-odd test on "
             "vertices rotated by the AT orientation. radius = lerp(start, target, progress[MorphCurve]) * scale. "
             "Candidates: players + creatures in the AT phase, grid visit order; dead units included."},
    {"stage": "filter", "consumer": "AreaTrigger.cpp:661-711", "evidence": "trinity-consumer",
     "text": "only with a template: NotTriggeredbyCaster / OnlyTriggeredByCaster / CreatorsPartyOnly "
             "(caster->IsInRaidWith), player DEAD/CORPSE unless AllowWhileGhost/AllowWhileDead, uninteractible "
             "unless CanAffectUninteractible, conditions (source 28). No current-player AT has flags or conditions."},
    {"stage": "enter-exit", "consumer": "AreaTrigger.cpp:853-942", "evidence": "trinity-consumer",
     "text": "entering = new list order minus previous inside set; all enter hooks (DoActions, then AI OnUnitEnter) "
             "run before exit hooks; exits iterate a GuidUnorderedSet (unspecified order); exit hooks: UndoActions "
             "(remove CAST/ADDAURA auras by caster), AI OnUnitExit (skipped for ByExpire with DontRunOnLeaveWhenExpiring)."},
    {"stage": "actions", "consumer": "AreaTrigger.cpp:1108-1188", "evidence": "trinity-consumer",
     "text": "per template action in load order: UnitFitToActionRequirement (FRIEND=IsValidAssistTarget(unit, "
             "spell), ENEMY=IsValidAttackTarget, RAID=IsInRaidWith, PARTY=IsInPartyWith, CASTER=unit is caster, ANY); "
             "CAST: caster->CastSpell(unit, param, TRIGGERED_FULL_MASK) (child re-selects from explicit unit); "
             "ADDAURA: caster->AddAura(param, unit) (no selection)."},
    {"stage": "script", "consumer": "AT_SCRIPTS / EXTERNAL_CONSUMERS", "evidence": "script-consumer",
     "text": "inside-units scripts act per entering unit (relation re-checked inside the hook); position-only scripts "
             "cast a child at the AT position so the child's implicit selectors choose recipients and the AT's own "
             "recipient list is irrelevant."},
]


# ---------------------------------------------------------------------------
# extended scope
# ---------------------------------------------------------------------------
_TARGET_PATTERNS = [
    ("nullptr", re.compile(r"^\s*nullptr\s*$")),
    ("destination", re.compile(r"(GetPosition|Position|Dest|dest|\bpos\b|Loc|loc\b|GetExplTargetDest|WorldLocation|SpellDestination)")),
    ("caster", re.compile(r"^\s*(GetCaster\(\)|caster|unitCaster|GetUnitOwner\(\)|owner|player|me)\s*$")),
    ("aura-target", re.compile(r"^\s*(GetTarget\(\)|target\s*=\s*GetTarget|aurApp->GetTarget\(\))\s*$")),
    ("hit-unit", re.compile(r"^\s*(GetHitUnit\(\)|hitUnit|GetHitCreature\(\)|GetHitPlayer\(\))\s*$")),
    ("explicit-unit", re.compile(r"^\s*(GetExplTargetUnit\(\)|GetExplTargetWorldObject\(\))\s*$")),
    ("proc-target", re.compile(r"(GetProcTarget|GetActionTarget|GetActor|eventInfo)")),
    ("iterated-unit", re.compile(r"^\s*(unit|target|u|tgt|ally|enemy|victim|\*itr|itr|member)\s*$")),
    ("self-call-receiver", re.compile(r"^\s*(this|at)\s*$")),
]


def classify_target_expr(expr: str | None) -> str:
    """Structural: what a ``CastSpell`` first argument names (evidence structural-inference)."""
    if not expr:
        return "unknown"
    for label, pat in _TARGET_PATTERNS:
        if pat.search(expr):
            return label
    return "other"


def _child_selectors(ctx, child: int) -> dict[str, Any]:
    from . import composition, selectors
    effs = []
    reselects = False
    explicit_unit_passthrough = False
    for e in ctx.data.effects(child):
        if not e.is_effect:
            continue
        a, b = selectors.info(e.target_a), selectors.info(e.target_b)
        cats = {a.category, b.category}
        rs = bool(cats & set(REACH_SELECTOR_CATEGORIES)) or "CHAIN" in cats or e.chain_targets > 1
        reselects |= rs
        explicit_unit_passthrough |= a.reference == "TARGET" and a.category == "DEFAULT"
        try:
            rel = composition.classify(e.target_a, e.target_b)["relation"]
        except Exception as exc:  # noqa: BLE001 -- recorded, not hidden
            rel = f"unclassified: {exc}"
        va, ta = selectors.understood(e.target_a)
        vb, tb = selectors.understood(e.target_b)
        effs.append({"index": e.index, "effect": e.effect, "aura": e.aura,
                     "selectors": [a.name, b.name], "categories": [a.category, b.category],
                     "composition": rel, "selector_verdicts": [va, vb],
                     "selector_tags": sorted(set(ta) | set(tb)), "reselects": rs,
                     "chain_targets": e.chain_targets})
    return {"effects": effs, "reselects": reselects, "explicit_unit_passthrough": explicit_unit_passthrough}


def _selector_class(effect_row: dict[str, Any], skew: bool) -> dict[str, Any]:
    order = ["blocked", "unresolved", "fixture-dependent", "understood-with-defect", "understood", "n/a"]
    verdicts = [v for v in effect_row["selector_verdicts"] if v != "n/a"] or ["understood"]
    klass = min(verdicts, key=order.index)
    tags = sorted(set(effect_row["selector_tags"]) | {"extended-scope"} | ({"reselects"} if effect_row["reselects"] else set()))
    return {"class": klass, "tags": tags, "unknowns": ["TG-I-10"], "build_skew": skew}


def _script_cast_edges(ctx, parents: set[int]) -> list[dict[str, Any]]:
    from .oracle import _bindings
    bm = _bindings()
    edges = []
    for parent in sorted(parents):
        for b in bm.bindings(parent):
            for h in b.hooks:
                if not h.facts:
                    continue
                base = {"parent": parent, "script": b.script_name, "hook": h.list, "handler": h.handler,
                        "hook_executes": bool(h.executes),
                        "file": (b.classes[0]["file"] if b.classes else None), "evidence": "structural-inference"}
                casts = [c for c in h.facts.get("calls", []) if c.get("cat") == "cast"]
                for c in casts:
                    args = c.get("args") or []
                    expr = args[0] if args else None
                    ids = [v for v in c.get("ints", []) if v >= 100 and ctx.catalog.exists(v)]
                    via = "script-hook"
                    if not ids and c["callee"] == "CastSpell" and len(args) > 1:
                        # spell id held in a variable/table: every SPELL_* constant the handler references
                        ids = sorted({int(v) for k, v in h.facts.get("refs", {}).items()
                                      if k.startswith("SPELL_") and int(v) >= 100 and ctx.catalog.exists(int(v))})
                        via = "script-hook-ref"
                    for child in ids:
                        edges.append({**base, "child": child, "via": via, "callee": c["callee"],
                                      "caster_expr": c.get("recv") or "(this script's caster)",
                                      "target_expr": expr, "explicit_target": classify_target_expr(expr),
                                      "line": c.get("line")})
    return edges


def _at_edges(ctx, w: at.ATWorld) -> list[dict[str, Any]]:
    edges = []
    for s, e in areatrigger_effects(ctx):
        cp = w.create_properties(e.misc0)
        if cp is None:
            continue
        script = at.AT_SCRIPTS.get(cp.script_name) if cp.script_name else None
        if script:
            for phase in ("enter", "update", "remove", "exit"):
                for item in script.get(phase, []):
                    if "cast" not in item:
                        continue
                    ids = [int(x) for x in re.findall(r"\d{3,}", str(item["cast"]))]
                    for child in ids:
                        edges.append({
                            "parent": s, "child": child, "via": f"areatrigger-script:{phase}",
                            "script": cp.script_name, "create_properties_id": cp.id,
                            "explicit_target": ("destination" if "at" in item and "position" in item["at"]
                                                else "caster" if item.get("on") == "caster"
                                                else "areatrigger-unit" if item.get("on") == "unit" else "other"),
                            "condition": item.get("if"), "file_line": script["file_line"], "evidence": "script-consumer",
                        })
        for a in cp.actions:
            if int(a["ActionType"]) in (0, 1):
                edges.append({
                    "parent": s, "child": int(a["ActionParam"]), "via": "areatrigger-template-action",
                    "create_properties_id": cp.id, "action": at.ACTION_TYPES[int(a["ActionType"])],
                    "requirement": at.ACTION_USER_TYPES[int(a["TargetType"])],
                    "explicit_target": "areatrigger-unit" if int(a["ActionType"]) == 0 else "none (AddAura)",
                    "file_line": "src/server/game/Entities/AreaTrigger/AreaTrigger.cpp:1140",
                    "evidence": "world-db-fact",
                })
    for s, ext in sorted(at.EXTERNAL_CONSUMERS.items()):
        if s not in ctx.scope.reach:
            continue
        for item in ext.get("tick", []):
            edges.append({
                "parent": s, "child": int(item["cast"]), "via": "areatrigger-external-script",
                "explicit_target": "destination" if "position" in item.get("at", "") else "areatrigger-unit",
                "condition": item.get("if"), "file_line": ext["file_line"], "evidence": "script-consumer",
            })
    return edges


def _linked_edges(ctx) -> list[dict[str, Any]]:
    linked = ctx.bundle.world.linked(ctx.catalog)
    edges = []
    for (typ, trigger), effects in sorted(linked.items()):
        if trigger not in ctx.scope.reach:
            continue
        name = SPELL_LINK_NAMES[typ]
        for child in effects:
            if child <= 0:
                continue      # negative: remove aura / immunity -- no cast
            edges.append({"parent": trigger, "child": child, "via": f"spell_linked_spell:{name}",
                          "explicit_target": LINK_TARGET[name]["explicit"],
                          "reselects_possible": LINK_TARGET[name]["reselects"],
                          "file_line": LINK_TARGET[name]["consumer"], "evidence": "world-db-fact"})
    return edges


def _d_script_extended() -> dict[str, Any]:
    path = CORPORA / "smart-selection.json"
    if not path.exists():
        return {"available": False, "spells": []}
    d = json.loads(path.read_text(encoding="utf-8"))
    by_spell: dict[int, list[str]] = defaultdict(list)
    for site in d.get("sites", []):
        for s in site.get("spells_script_extended", []):
            by_spell[s].append(f"{site['file']}:{site['line']}")
    return {"available": True, "unknown": "TG-D-13",
            "spells": {str(k): sorted(set(v)) for k, v in sorted(by_spell.items())}}


def build_extended_scope(max_hops: int = 3) -> dict[str, Any]:
    from . import context
    ctx = context.get()
    w = at.ATWorld(ctx.bundle.world)
    reach = set(ctx.scope.reach)
    edges = _script_cast_edges(ctx, reach) + _at_edges(ctx, w) + _linked_edges(ctx)
    for e in edges:
        e["hop"] = 1
    hop1 = {e["child"] for e in edges} - reach
    # bounded closure through script hooks + linked spells of the new children (population only)
    known = reach | hop1
    frontier = set(hop1)
    for hop in range(2, max_hops + 1):
        nxt_edges = [e for e in _script_cast_edges(ctx, frontier) if e["child"] not in known]
        linked = ctx.bundle.world.linked(ctx.catalog)
        for (typ, trig), effs in sorted(linked.items()):
            if trig in frontier:
                for c in effs:
                    if c > 0 and c not in known:
                        n = SPELL_LINK_NAMES[typ]
                        nxt_edges.append({"parent": trig, "child": c, "via": f"spell_linked_spell:{n}",
                                          "explicit_target": LINK_TARGET[n]["explicit"],
                                          "file_line": LINK_TARGET[n]["consumer"], "evidence": "world-db-fact"})
        for e in nxt_edges:
            e["hop"] = hop
        edges += nxt_edges
        frontier = {e["child"] for e in nxt_edges} - known
        known |= frontier
    children = sorted(known - reach)
    by_child: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for e in edges:
        if e["child"] in children:
            by_child[e["child"]].append(e)
    d_ext = _d_script_extended()
    d_spells = {int(k) for k in d_ext.get("spells", {})} if d_ext["available"] else set()
    rows = []
    ext_classes: dict[str, Any] = {}
    for child in children:
        srcs = sorted(by_child[child], key=lambda e: (e["hop"], e["parent"], e["via"], str(e.get("line")), str(e.get("target_expr"))))
        sel = _child_selectors(ctx, child)
        skew = ctx.is_skew(child)
        hop = min(e["hop"] for e in srcs)
        row = {"spell": child, "name": ctx.name(child), "hop": hop, "build_skew": skew,
               "sources": srcs, "via_kinds": sorted({e["via"].split(":")[0] for e in srcs}),
               "explicit_targets": sorted({e["explicit_target"] for e in srcs}),
               "reselects": sel["reselects"], "explicit_unit_passthrough": sel["explicit_unit_passthrough"],
               "effects": sel["effects"],
               "in_d_script_extended": child in d_spells}
        if child in d_spells:
            row["d_sites"] = d_ext["spells"][str(child)]
        rows.append(row)
        for er in sel["effects"]:
            ext_classes[f"{child}:{er['index']}"] = _selector_class(er, skew)
    counts = {
        "edges": len(edges),
        "children": len(children),
        "children_by_hop": dict(Counter(r["hop"] for r in rows)),
        "children_by_source": dict(Counter(k for r in rows for k in r["via_kinds"])),
        "hop1_by_source": dict(Counter(k for r in rows if r["hop"] == 1 for k in r["via_kinds"])),
        "explicit_target_kinds_hop1": dict(Counter(e["explicit_target"] for e in edges if e["hop"] == 1 and e["child"] in children)),
        "children_reselecting": sum(1 for r in rows if r["reselects"]),
        "children_only_via_dead_hooks": sum(1 for r in rows if r["sources"] and all(
            e.get("hook_executes") is False for e in r["sources"])),
        "children_with_effects": sum(1 for r in rows if r["effects"]),
        "extended_effects": len(ext_classes),
        "extended_effects_by_class": dict(Counter(v["class"] for v in ext_classes.values())),
        "build_skew_children": sum(1 for r in rows if r["build_skew"]),
        "d_script_extended_total": len(d_spells),
        "d_script_extended_covered": len(d_spells & set(children)),
        "d_script_extended_in_reach": len(d_spells & reach),
    }
    return {
        "schema": "targeting-extended-scope/1",
        "provenance": _provenance(f"python3 targeting.py extended-scope --out ../../docs/research/targeting-corpora/extended-scope.json (max_hops={max_hops})"),
        "note": ("Population only. Children are NOT added to ctx.scope.reach and their effects are published under "
                 "extended_effect_classes (selector-path verdicts only). Parent->child explicit-target statements carry "
                 "their evidence class per edge: script-hook edges are structural-inference (tree-sitter index facts, "
                 "the first CastSpell argument text), AreaTrigger edges are script-consumer (bodies read), linked/"
                 "template-action edges are world-db-fact with the Trinity consumer line."),
        "counts": counts,
        "d_cross_reference": {**d_ext, "missing_from_extended": sorted(d_spells - set(children) - reach)},
        "rows": rows,
        "extended_effect_classes": dict(sorted(ext_classes.items(), key=lambda kv: tuple(map(int, kv[0].split(":"))))),
        "unknowns": [
            {"id": "TG-I-10", "subject": "extended-scope child effects", "evidence": "structural-inference",
             "known": "selector-path verdict (targeting.selectors.understood) and the parent's explicit-target kind",
             "unknown": "whether the child is actually cast (runtime conditions in the hook) and the full recipient policy",
             "why_unresolved": "population pass only (brief question 3)", "reopen_condition": "lead promotes children into the census",
             "build_skew": "per row"},
            {"id": "TG-I-11", "subject": "Register()-time conditional hook registration (Halo 120517/120644 selector and 15 other classes)",
             "evidence": "structural-inference",
             "known": "spell_priest.cpp is fully indexed (140 registrations, 197 hooks; provenance.parse_errors marks local "
                      "ERROR nodes only), but the index records every `+=` inside Register() unconditionally; "
                      "spell_pri_halo_effect_selector registers its prevent hooks only for effects other than the one "
                      "selected from caster auras at Register() time",
             "unknown": "which hooks are live for a given caster aura state; hence which Halo ATs spawn",
             "why_unresolved": "the structural index has no control-flow model of Register()",
             "reopen_condition": "a Register()-aware index or a hand-read per-class table (lead unknown TG-H-03 lists the 16 classes)",
             "build_skew": False},
        ],
    }


# ---------------------------------------------------------------------------
def _out(p) -> None:
    p.add_argument("--out", help="write JSON here instead of stdout")


def _areatriggers(args) -> int:
    emit(build_areatriggers(), args.out)
    return 0


def _extended(args) -> int:
    emit(build_extended_scope(), args.out)
    return 0


COMMANDS = {
    "areatriggers": ("Track I: current-player AreaTrigger census + recipient pipeline", _out, _areatriggers),
    "extended-scope": ("Track I: script/areatrigger/linked children outside reach (population only)", _out, _extended),
}
