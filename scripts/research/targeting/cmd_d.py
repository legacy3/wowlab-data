"""Track D commands: chain / smart-selection / RNG corpora, witnesses, probe runner.

``targeting.py chains --out docs/research/targeting-corpora/chains.json``
``targeting.py smart-selection --out docs/research/targeting-corpora/smart-selection.json``
``targeting.py rng --out docs/research/targeting-corpora/rng.json``
``targeting.py d-witnesses --out /path/witnesses.json``
``targeting.py d-probe`` (build + smoke-run ``tools/tc_target_chain_probe``)
"""

from __future__ import annotations

import json
import math
import re
import subprocess
from collections import Counter, defaultdict
from functools import cache
from pathlib import Path
from typing import Any

from . import PINS, RESEARCH, TC_ROOT, FailClosed
from .cli import emit

PROBE_DIR = RESEARCH / "tools" / "tc_target_chain_probe"
PROBE_BIN = PROBE_DIR / "probe"

MOD_AURAS = {107: "ADD_FLAT_MODIFIER", 108: "ADD_PCT_MODIFIER", 218: "ADD_PCT_MODIFIER_BY_SPELL_LABEL",
             219: "ADD_FLAT_MODIFIER_BY_SPELL_LABEL", 646: "ADD_FLAT_PVP_MODIFIER", 647: "ADD_PCT_PVP_MODIFIER",
             648: "ADD_FLAT_PVP_MODIFIER_BY_SPELL_LABEL", 649: "ADD_PCT_PVP_MODIFIER_BY_SPELL_LABEL"}
CHAIN_OPS = {17: "ChainTargets", 20: "ChainAmplitude", 35: "ChainJumpDistance"}   # SpellDefines.h:171/174/189
RANDOM_DIR_TARGETS = (72, 73, 74, 75, 86, 91, 149)   # SpellInfo.cpp:320-397 TARGET_DIR_RANDOM rows
RANDOM_RADIUS_TARGETS = (72, 74, 86)                 # SpellInfo.cpp:820-825
TARGET_HOOK_LISTS = ("OnObjectAreaTargetSelect", "OnObjectTargetSelect", "OnDestinationTargetSelect")


def _provenance(command: str) -> dict[str, Any]:
    return {"pins": PINS, "command": command, "generator": "scripts/research/targeting/cmd_d.py"}


# ---------------------------------------------------------------------------
# probe
# ---------------------------------------------------------------------------
def probe_available() -> bool:
    return (TC_ROOT / "src/server/game/Spells/Spell.cpp").exists()


def ensure_probe() -> Path:
    if not probe_available():
        raise FailClosed(f"probe: TrinityCore checkout not found at {TC_ROOT}")
    subprocess.run(["make", "-s", "-C", str(PROBE_DIR)], check=True, capture_output=True)
    return PROBE_BIN


def run_probe(cases: list[str]) -> list[dict[str, Any]]:
    """Run the probe on case texts (each ends with ``go``); one JSON result per case."""
    binary = ensure_probe()
    text = "".join(c if c.endswith("\n") else c + "\n" for c in cases)
    res = subprocess.run([str(binary)], input=text, capture_output=True, text=True, check=True)
    out = [json.loads(line) for line in res.stdout.splitlines() if line.strip()]
    if len(out) != len(cases):
        raise FailClosed(f"probe: {len(cases)} cases, {len(out)} results")
    return out


def hexf(v: float) -> str:
    return float(v).hex()


def chain_case(world, sv, eff, initial: str, chain_targets: int, is_heal: bool, candidates: list[str]) -> tuple[str, dict[str, int]]:
    """Encode a fixture as a probe ``chain`` case (caster = obj 0).

    The probe's stubbed ``SearchAreaTargets`` returns ``candidates`` as given (the Python
    pre-filter output), ``HasInArc`` returns the Python ``geometry.has_in_arc`` flag, LOS
    reads the fixture pairs.  Returns (case text, actor -> probe index).
    """
    from . import chain as ch
    from . import geometry as g
    names = [world.caster] + [a for a in world.actors if a != world.caster]
    idx = {a: i for i, a in enumerate(names)}
    caster = world.actor(world.caster)
    from .oracle import spell_mod_owner
    mod_owner = spell_mod_owner(world, world.caster)
    jm = world.modifiers.get("chain_jump_distance", 0) if mod_owner else 0
    jflat, jpct = (jm.get("flat", 0), jm.get("pct", 1.0)) if isinstance(jm, dict) else (jm, 1.0)
    rng_max = world.spell_value.get("chain_from_caster_max_range", 0.0)
    lines = [f"chain {int(is_heal)} {chain_targets} {sv.dmg_class} {int(sv.has_attr('SPELL_ATTR2_CHAIN_FROM_CASTER'))} "
             f"{int(sv.has_attr('SPELL_ATTR5_MELEE_CHAIN_TARGETING'))} {eff.attributes} {int(bool(mod_owner))} "
             f"{int(jflat)} {hexf(g.f32(jpct))} {hexf(g.f32(rng_max))}"]
    cpos = g.vec(caster.need("pos"))
    for a in names:
        x = world.actor(a)
        p = x.need("pos")
        front = g.has_in_arc(cpos, caster.orientation or 0.0, ch.M_PI_F, g.vec(p), same_object=(a == world.caster)) \
            if sv.has_attr("SPELL_ATTR5_MELEE_CHAIN_TARGETING") else True
        typ = 6 if x.kind == "player" else 5
        lines.append(f"obj {idx[a]} {typ} {hexf(g.f32(p[0]))} {hexf(g.f32(p[1]))} {hexf(g.f32(p[2]))} "
                     f"{hexf(g.f32(x.combat_reach or 0.0))} {int(x.health or 0)} {int(x.max_health or 0)} {int(front)} 0")
    lines.append(f"initial {idx[initial]}")
    lines.append("order " + " ".join(str(idx[c]) for c in candidates))
    for a in names:
        for b in names:
            if a < b:
                clear = ch.spell_los(world, sv, a, b) if b in world.actors else True
                if not clear:
                    lines.append(f"los {idx[a]} {idx[b]} 0")
    lines.append("go")
    return "\n".join(lines), idx


# ---------------------------------------------------------------------------
# census helpers
# ---------------------------------------------------------------------------
def _popcount(x: int) -> int:
    return bin(x).count("1")


@cache
def chain_modifiers() -> list[dict[str, Any]]:
    """Every current-player aura effect that modifies ChainTargets / ChainJumpDistance / ChainAmplitude."""
    from . import context
    ctx = context.get()
    out = []
    for s in sorted(ctx.scope.reach):
        info = ctx.catalog.get(s)
        if info is None:
            continue
        for e in ctx.data.effects(s):
            if e.is_effect and e.effect == 6 and e.aura in MOD_AURAS and e.misc0 in CHAIN_OPS:
                ei = info.effect(e.index)
                out.append({"spell": s, "effect": e.index, "name": ctx.name(s), "aura": MOD_AURAS[e.aura],
                            "op": CHAIN_OPS[e.misc0], "label": e.misc1 if e.aura in (218, 219, 648, 649) else None,
                            "family": info.family, "class_mask": ei.class_mask, "base_points": ei.base_points,
                            "build_skew": ctx.is_skew(s), "trinity_consumed": e.aura in (107, 108, 218, 219)})
    return out


def mod_matches(mod: dict[str, Any], info) -> int:
    """Mirrors: SpellInfo.cpp:1989-2019 ``IsAffectedBySpellMod`` -> application count (0 = no)."""
    if not mod["trinity_consumed"]:
        return 0
    from procs.enums import attr
    if info.has_attr(attr("SPELL_ATTR3_IGNORE_CASTER_MODIFIERS")):
        return 0
    if mod["label"] is not None:
        return 1 if mod["label"] in info.labels else 0
    if info.family != mod["family"]:
        return 0
    return _popcount(mod["class_mask"] & info.family_flags)


def _hooks(spell: int) -> list[dict[str, Any]]:
    from .oracle import _bindings
    out = []
    for b in _bindings().bindings(spell):
        for h in b.hooks:
            if h.list in TARGET_HOOK_LISTS and h.executes:
                out.append({"script": b.script_name, "list": h.list, "handler": h.handler, "target": h.eff_value,
                            "affected_mask": h.affected_mask, "file": (b.classes[0]["file"] if b.classes else None)})
    return out


def _plan(view) -> tuple[dict[int, int], str | None]:
    """effect -> the effect whose turn selected it (F's selection plan, static radius equality)."""
    from .recipients import script_effect_check, selection_plan
    effs = view.effects

    def radius_equal(i: int, j: int) -> bool:
        a, b = effs[i], effs[j]
        return (a.radius_a, a.radius_b) == (b.radius_a, b.radius_b) and not (
            {a.target_a, a.target_b, b.target_a, b.target_b} & set(RANDOM_RADIUS_TARGETS))
    try:
        plan = selection_plan(effs, None, script_effect_check(view), radius_equal=radius_equal)
    except FailClosed as exc:
        return {}, str(exc)
    leader = {}
    for step in plan:
        for k in range(len(effs)):
            if step.mask & (1 << k):
                leader[k] = step.effect
    return leader, None


def _view(spell: int):
    from .oracle import from_data
    try:
        return from_data(spell), None
    except FailClosed as exc:
        try:
            return from_data(spell, allow_corrections=True), f"corrections-not-applied: {exc}"
        except FailClosed as exc2:
            return None, str(exc2)


# ---------------------------------------------------------------------------
# chains.json
# ---------------------------------------------------------------------------
def build_chains() -> dict[str, Any]:
    from procs.enums import attr

    from . import chain as ch
    from . import context
    from .selectors import info as sel_info
    ctx = context.get()
    mods = chain_modifiers()
    rows: list[dict[str, Any]] = []
    classes: dict[str, dict[str, Any]] = {}
    inert = 0
    inert_rows: list[dict[str, Any]] = []
    for s in sorted(ctx.scope.reach):
        info = ctx.catalog.get(s)
        effects = ctx.data.effects(s)
        if info is None or not effects:
            continue
        chainers_any = [e for e in effects if e.is_effect and any(t and ch.chains_from(t) for t in (e.target_a, e.target_b))]
        if not chainers_any:
            continue
        spell_mods = []
        for m in mods:
            n = mod_matches(m, info)
            if n:
                spell_mods.append({"spell": m["spell"], "effect": m["effect"], "name": m["name"], "op": m["op"],
                                   "aura": m["aura"], "base_points": m["base_points"], "applications": n})
        ct_mods = [m for m in spell_mods if m["op"] == "ChainTargets"]
        view, view_note = None, None
        leader: dict[int, int] = {}
        plan_note = None
        if not ct_mods and all(x.chain_targets <= 1 for x in effects):
            inert += len(chainers_any)
            continue
        view, view_note = _view(s)
        if view is not None:
            leader, plan_note = _plan(view)
        flat = sum(int(m["base_points"]) * m["applications"] for m in ct_mods if "FLAT" in m["aura"])
        pct = 1.0
        for m in ct_mods:
            if "PCT" in m["aura"] and m["base_points"] > 0:
                pct *= 1.0 + m["base_points"] / 100.0
        for e in chainers_any:
            sels = [(ab, t) for ab, t in (("A", e.target_a), ("B", e.target_b)) if t and ch.chains_from(t)]
            lead = leader.get(e.index, e.index)
            lead_ct = next((x.chain_targets for x in effects if x.index == lead), e.chain_targets)
            # Spell.cpp:1834-1838: the leader's ChainTargets after SpellModOp::ChainTargets must exceed 1
            # (upper bound: every positive modifier active at once)
            max_after_mods = int((lead_ct + flat) * pct)
            if max_after_mods <= 1:
                inert += 1
                if ct_mods or lead_ct != e.chain_targets:
                    inert_rows.append({"spell": s, "effect": e.index, "name": ctx.name(s),
                                       "chain_targets_used": lead_ct, "max_after_mods": max_after_mods,
                                       "modifiers": [m["name"] for m in ct_mods]})
                continue
            hooks = [h for h in _hooks(s) if h["affected_mask"] and h["affected_mask"] & (1 << e.index)
                     and h["target"] in (e.target_a, e.target_b)]
            is_heal = any(t == ch.TARGET_UNIT_TARGET_CHAINHEAL_ALLY for _, t in sels)
            dc = info.dmg_class
            base = {3: 7.5, 2: 5.0}.get(dc, 12.5 if is_heal and dc in (0, 1) else (10.0 if dc in (0, 1) else 0.0))
            cfc = info.has_attr(attr("SPELL_ATTR2_CHAIN_FROM_CASTER"))
            row = {
                "spell": s, "effect": e.index, "name": ctx.name(s), "effect_type": e.effect,
                "selectors": [{"slot": ab, "target": t, "name": sel_info(t).name, "caller": ch.chains_from(t),
                               "object": sel_info(t).object, "check": sel_info(t).check} for ab, t in sels],
                "chain_targets_authored": e.chain_targets, "dmg_class": dc, "jump_radius_base": base,
                "chain_heal": is_heal,
                "chain_from_caster": cfc,
                "melee_chain_targeting": info.has_attr(attr("SPELL_ATTR5_MELEE_CHAIN_TARGETING")),
                "chain_from_initial_target": bool(e.attributes & ch.EFFECT_ATTR_CHAIN_FROM_INITIAL),
                "enforce_los_to_chain_targets": bool(e.attributes & ch.EFFECT_ATTR_ENFORCE_LOS_TO_CHAIN),
                "ignore_los": info.has_attr(attr("SPELL_ATTR2_IGNORE_LINE_OF_SIGHT")),
                "bouncy_chain_missiles": info.has_attr(attr("SPELL_ATTR4_BOUNCY_CHAIN_MISSILES")),
                "chain_amplitude": e.chain_amplitude,
                "modifiers": spell_mods,
                "selected_on_turn_of_effect": lead,
                "chain_targets_used": lead_ct,
                "max_chain_targets_after_mods": max_after_mods,
                "inherits_other_effect_chain": lead != e.index and lead_ct != e.chain_targets,
                "target_hooks": hooks,
                "view_note": view_note, "plan_note": plan_note,
                "build_skew": ctx.is_skew(s),
                "evidence": "db2-fact" if not hooks else "script-consumer",
                "consumer": "Spell.cpp:1832-1864, 2221-2327",
            }
            tags = ["chain-heal" if is_heal else "chain"]
            verdict = "understood" if is_heal else "understood-with-defect"
            unknowns = []
            if lead_ct <= 1:
                tags.append("chain-by-modifier")
            if cfc:
                tags.append("chain-from-caster")
            if row["inherits_other_effect_chain"]:
                tags.append("chain-grouping-inherited")
            if hooks:
                tags.append("script-adapter")
                verdict = "fixture-dependent"
            if plan_note:
                verdict = "fixture-dependent"
                unknowns.append("TG-D-03")
            if any(x["object"] not in ("UNIT", "UNIT_AND_DEST") for x in row["selectors"]):
                verdict = "blocked"
            tags.append("world-geometry")
            row["class"] = verdict
            rows.append(row)
            classes[f"{s}:{e.index}"] = {"class": verdict, "tags": sorted(set(tags)), "unknowns": unknowns,
                                         "build_skew": row["build_skew"]}
    counts = {
        "chain_effects": len(rows),
        "chain_effects_authored_gt1": sum(r["chain_targets_authored"] > 1 for r in rows),
        "chain_effects_by_modifier_only": sum(r["chain_targets_used"] <= 1 for r in rows),
        "chain_effects_by_grouping_only": sum(r["chain_targets_authored"] <= 1 < r["chain_targets_used"] for r in rows),
        "inert_chain_selectors_max_after_mods_le1": inert,
        "inert_despite_modifier_or_grouping": len(inert_rows),
        "chain_heal": sum(r["chain_heal"] for r in rows),
        "by_jump_radius_base": dict(sorted(Counter(str(r["jump_radius_base"]) for r in rows).items())),
        "chain_from_caster": sum(r["chain_from_caster"] for r in rows),
        "melee_chain_targeting": sum(r["melee_chain_targeting"] for r in rows),
        "chain_from_initial_target": sum(r["chain_from_initial_target"] for r in rows),
        "enforce_los_to_chain_targets": sum(r["enforce_los_to_chain_targets"] for r in rows),
        "with_target_hooks": sum(bool(r["target_hooks"]) for r in rows),
        "inherits_other_effect_chain": sum(r["inherits_other_effect_chain"] for r in rows),
        "chain_modifier_auras": len(mods),
        "by_class": dict(sorted(Counter(r["class"] for r in rows).items())),
    }
    return {
        "provenance": _provenance("targeting.py chains --out docs/research/targeting-corpora/chains.json"),
        "schema": "targeting-chains/1",
        "counts": counts,
        "findings": CHAIN_FINDINGS,
        "effects": rows,
        "inert_with_modifiers": inert_rows,
        "modifiers": mods,
        "effect_classes": dict(sorted(classes.items())),
        "unknowns": CHAIN_UNKNOWNS,
        "retail_experiments": CHAIN_EXPERIMENTS,
        "trinity_defects": CHAIN_DEFECTS,
    }


CHAIN_FINDINGS = [
    {"id": "TG-D-F01", "evidence": "trinity-consumer", "consumer": "Spell.cpp:1270, 1825",
     "statement": "Only SelectImplicitNearbyTargets and SelectImplicitTargetObjectTargets call the chain stage; "
                  "caster-object/area/cone/line/traj/dest selectors never chain. TargetObject chains even when "
                  "AddUnitTarget rejected the explicit target (the call sits outside the add)."},
    {"id": "TG-D-F02", "evidence": "trinity-consumer", "consumer": "Spell.cpp:1834-1838, 1847",
     "statement": "maxTargets = ChainTargets + SpellModOp::ChainTargets (Player::ApplySpellMod: T((double(v)+flat)*pct)); "
                  "chaining happens only when maxTargets > 1 and searches maxTargets-1 jumps. An authored 0/1 plus a flat "
                  "mod creates a chain."},
    {"id": "TG-D-F03", "evidence": "trinity-consumer", "consumer": "Spell.cpp:741-775",
     "statement": "Effect-mask grouping does not compare ChainTargets: a later effect with the same selectors, conditions, "
                  "PlayersOnly flag, script target hooks (and radius for NEARBY) is selected on the first effect's turn "
                  "with the first effect's ChainTargets and receives the same jump targets."},
    {"id": "TG-D-F04", "evidence": "trinity-consumer", "consumer": "Spell.cpp:2224-2247",
     "statement": "Jump radius by DmgClass: RANGED 7.5, MELEE 5, NONE/MAGIC 10, chain heal (selector 45) 12.5; then "
                  "SpellModOp::ChainJumpDistance (float)."},
    {"id": "TG-D-F05", "evidence": "trinity-consumer", "consumer": "Spell.cpp:2250-2265, 9418-9453",
     "statement": "Pre-filter population: SearchAreaTargets around the chain source (the initial target, or the caster "
                  "with ATTR2_CHAIN_FROM_CASTER) with radius jumpRadius*jumps (ChainFromInitialTarget: jumpRadius; "
                  "CHAIN_FROM_CASTER: Spell::GetMinMaxRange(false).Max); 2D radius + candidate combat reach, |dz| <= "
                  "radius, ChainTarget immunity, relation check with referer = caster. Only the initial target is "
                  "removed: the caster can be a jump target."},
    {"id": "TG-D-F06", "evidence": "trinity-probe", "consumer": "Spell.cpp:2283-2298",
     "statement": "Chain heal picks the largest uint32(maxHealth-health) among candidates within jumpRadius (3D, both "
                  "combat reaches, strict <) and in LOS of the chain source; ties keep the earliest list element; the "
                  "first candidate is not exempt from distance/LOS; a full-health unit is picked only if it is the "
                  "first acceptable candidate (a later positive deficit replaces it). EnforceLineOfSightToChainTargets "
                  "is ignored on this path."},
    {"id": "TG-D-F07", "evidence": "trinity-probe", "consumer": "Spell.cpp:2300-2314, Object.cpp:569",
     "statement": "Other chains pick the nearest candidate to the chain source by GetDistanceOrder (3D centre distance "
                  "squared, binary32, strict <: ties keep the earliest), but only the first accepted candidate is "
                  "checked against jumpRadius; replacements are only compared by distance order (defect TG-D-DEF-01)."},
    {"id": "TG-D-F08", "evidence": "trinity-consumer", "consumer": "Spell.cpp:2319-2325, 1850-1862",
     "statement": "The chain source moves to each chosen target unless CHAIN_FROM_CASTER / ChainFromInitialTarget; "
                  "chosen targets leave tempTargets; the loop ends at the first jump without a candidate (fewer than "
                  "cap is normal). The OnObjectAreaTargetSelect hook runs after all jumps are chosen; AddUnitTarget is "
                  "then called with checkIfValid=false and losPosition = previous list element (caster for "
                  "CHAIN_FROM_CASTER, initial for ChainFromInitialTarget), so CheckEffectTarget requires LOS from it."},
    {"id": "TG-D-F09", "evidence": "trinity-consumer", "consumer": "Spell.cpp:1840-1844, 8505-8519, 8626-8634",
     "statement": "Damage multipliers are armed for every grouped effect with index >= the chaining effect and applied in "
                  "m_UniqueTargetInfo order (first target x1, then *= ChainAmplitude (+SpellModOp::ChainAmplitude) per "
                  "target carrying that effect). The coupling to selection is only through that order."},
    {"id": "TG-D-F10", "evidence": "trinity-consumer", "consumer": "Spell.cpp:2269-2276",
     "statement": "ATTR5_MELEE_CHAIN_TARGETING removes candidates outside the caster's front half (HasInArc(pi), the caster "
                  "itself is always 'in arc')."},
]

CHAIN_UNKNOWNS = [
    {"id": "TG-D-01", "subject": "chain jump radius / nearest rule vs Retail", "evidence": "trinity-consumer",
     "known": "Trinity uses fixed DmgClass radii (7.5/5/10/12.5) and nearest-to-previous jumps.",
     "unknown": "Whether Retail uses the same radii/order for every current chain (e.g. modern Chain Lightning, Avenger's Shield).",
     "why_unresolved": "No DB2 column carries a jump radius; the value is a server constant.",
     "reopen_condition": "Retail experiment TG-D-X01/X02 or a newer Trinity/sniff-derived change.", "build_skew": "n/a"},
    {"id": "TG-D-02", "subject": "Spell::GetMinMaxRange(false) for CHAIN_FROM_CASTER search radius", "evidence": "trinity-consumer",
     "known": "Spell.cpp:7329-7387 (melee/ranged flags, combat reach of explicit target, movement bonus, ranged slot).",
     "unknown": "The value per cast (runtime); the oracle takes it as spell_value.chain_from_caster_max_range.",
     "why_unresolved": "Depends on explicit target, movement and equipment state.",
     "reopen_condition": "Track B/G port Spell::GetMinMaxRange.", "build_skew": False},
    {"id": "TG-D-03", "subject": "effect-mask grouping of chain effects with unresolved script hooks / corrections",
     "evidence": "unresolved", "known": "Spell.cpp:741-775 grouping rules.",
     "unknown": "Grouping when the script hook mask is unresolved or LoadSpellInfoCorrections touch the spell.",
     "why_unresolved": "Structural index cannot resolve the hook; corrections not applied by the view.",
     "reopen_condition": "Track E/F resolve the hook masks.", "build_skew": "n/a"},
]

CHAIN_EXPERIMENTS = [
    {"id": "TG-D-X01", "question": "Does Chain Heal jump to the largest absolute deficit (Trinity) or lowest health % ?",
     "model_a": "max uint32 deficit within 12.5y of the previous target, ties to earliest candidate",
     "model_b": "lowest health percentage (or Blizzard 'smart' weighting)",
     "setup": "Party of 3 near the primary: A 100k max at 60k (deficit 40k, 60%), B 50k max at 20k (deficit 30k, 40%); all within 12.5y of the primary, LOS clear.",
     "observable": "combat log SPELL_HEAL order/destGUID for the second hit", "fidelity": "exact"},
    {"id": "TG-D-X02", "question": "Is the jump reference the previous target (Trinity) or the primary/caster?",
     "model_a": "nearest to previous target", "model_b": "nearest to primary target",
     "setup": "Enemies P(0), X(8,0), Y(16,0), Z(-9,0) on a line; cast Chain Lightning at P with 3 jumps.",
     "observable": "SPELL_DAMAGE destGUID order: Trinity X, Y, Z? no: X(8) then Y(from X: 8) then Z (from Y: 25 > 10 -> stop); model_b X, Z, Y",
     "fidelity": "exact"},
    {"id": "TG-D-X03", "question": "Can a chain jump replace a candidate that is closer by centre distance but outside the jump radius (defect TG-D-DEF-01)?",
     "model_a": "Trinity: yes (replacement only compares distance order)", "model_b": "radius enforced for every candidate",
     "setup": "Needs a large-combat-reach unit as first in-range candidate and a small unit slightly closer by centre but beyond radius+reaches; hard to stage in Retail.",
     "observable": "whether the small unit is hit", "fidelity": "insufficient"},
]

CHAIN_DEFECTS = [
    {"id": "TG-D-DEF-01", "file_line": "Spell.cpp:2303",
     "description": "isBestDistanceMatch = foundItr != end ? GetDistanceOrder(itr, found) : IsWithinDist(itr, jumpRadius): "
                    "once a candidate is found, later candidates are accepted when merely closer by centre distance, without "
                    "the jump-radius check. IsWithinDist adds both combat reaches, GetDistanceOrder does not, so a closer "
                    "candidate with a smaller combat reach can be outside the jump radius and still be chosen.",
     "effect_on_recipients": "A jump may land on a unit beyond jumpRadius + reaches (still inside the pre-filter radius).",
     "oracle_behaviour": "Reproduced; the trace stage 'chain.jump' carries defect=TG-D-DEF-01 when it happens."},
    {"id": "TG-D-DEF-02", "file_line": "Spell.cpp:2291",
     "description": "uint32 deficit = GetMaxHealth() - GetHealth() truncates the uint64 difference.",
     "effect_on_recipients": "Deficits >= 2^32 wrap (irrelevant for current player health pools).",
     "oracle_behaviour": "Reproduced (& 0xFFFFFFFF)."},
    {"id": "TG-D-DEF-03", "file_line": "SpellInfo.cpp:825",
     "description": "Random-radius destinations use (Max - Min) * sqrt(rand_norm()) and never add Min back.",
     "effect_on_recipients": "Random destinations can fall inside RadiusMin.",
     "oracle_behaviour": "Reproduced in oracle.calc_radius (track G) with a trace note."},
]


# ---------------------------------------------------------------------------
# script site scan (smart-selection.json / rng.json)
# ---------------------------------------------------------------------------
SITE_PATTERN = re.compile(
    r"SelectRandomInjuredTargets|SortTargetsWithPriorityRules|RandomResize|SelectRandomContainerElement|"
    r"SelectRandomWeightedContainerElement|RandomShuffle|HealthPctOrderPred|PowerPctOrderPred|ObjectDistanceOrderPred|"
    r"std::sort|ranges::sort|\.sort\(|\burand\(|\birand\(|\bfrand\(|rand_norm|rand_chance|roll_chance|"
    r"GetNextRandomRaidMemberOrPet|SelectNearbyTarget|GetRandomPoint|GetRandomNearPosition|min_element|max_element|"
    r"ranges::min\b|ranges::max\b|DoRandomRoll|randtime|urandms|std::partition")

# (file basename, line) -> role, for every site whose role is not "non-targeting-rng".
SITE_ROLES: dict[tuple[str, int], str] = {
    ("pet_hunter.cpp", 97): "target", ("spell_azerite.cpp", 216): "target", ("spell_azerite.cpp", 228): "target",
    ("spell_dh.cpp", 1000): "spell-choice", ("spell_dh.cpp", 1082): "spell-choice", ("spell_dk.cpp", 271): "spell-choice",
    ("spell_druid.cpp", 772): "target", ("spell_druid.cpp", 1063): "target", ("spell_druid.cpp", 1619): "target",
    ("spell_druid.cpp", 1920): "target", ("spell_druid.cpp", 2196): "target-count", ("spell_druid.cpp", 2202): "target",
    ("spell_druid.cpp", 2274): "target", ("spell_druid.cpp", 2544): "target", ("spell_druid.cpp", 2882): "target",
    ("spell_druid.cpp", 3031): "target", ("spell_druid.cpp", 3105): "target",
    ("spell_evoker.cpp", 100): "target", ("spell_evoker.cpp", 311): "target",
    ("spell_generic.cpp", 2431): "payload-choice", ("spell_generic.cpp", 3098): "target",
    ("spell_generic.cpp", 3290): "cosmetic", ("spell_generic.cpp", 3612): "payload-choice",
    ("spell_generic.cpp", 5023): "spell-choice", ("spell_generic.cpp", 5479): "target",
    ("spell_item.cpp", 390): "spell-choice", ("spell_item.cpp", 631): "spell-choice", ("spell_item.cpp", 966): "target",
    ("spell_item.cpp", 1125): "spell-choice", ("spell_item.cpp", 1489): "spell-choice", ("spell_item.cpp", 2394): "dest",
    ("spell_item.cpp", 3055): "cosmetic", ("spell_item.cpp", 3625): "target", ("spell_item.cpp", 4036): "spell-choice",
    ("spell_item.cpp", 4097): "spell-choice",
    ("spell_mage.cpp", 491): "dest", ("spell_mage.cpp", 499): "timing", ("spell_mage.cpp", 520): "timing",
    ("spell_mage.cpp", 931): "timing", ("spell_mage.cpp", 949): "timing", ("spell_mage.cpp", 1642): "cosmetic",
    ("spell_monk.cpp", 116): "target",
    ("spell_paladin.cpp", 910): "target", ("spell_paladin.cpp", 1136): "target", ("spell_paladin.cpp", 1140): "target",
    ("spell_priest.cpp", 954): "target", ("spell_priest.cpp", 1246): "dest", ("spell_priest.cpp", 1595): "target",
    ("spell_priest.cpp", 1918): "target", ("spell_priest.cpp", 1965): "target", ("spell_priest.cpp", 2660): "value",
    ("spell_priest.cpp", 3369): "target", ("spell_priest.cpp", 3555): "target", ("spell_priest.cpp", 3766): "target",
    ("spell_priest.cpp", 3868): "target", ("spell_priest.cpp", 4037): "target", ("spell_priest.cpp", 5040): "target",
    ("spell_priest.cpp", 5053): "target", ("spell_priest.cpp", 5407): "target", ("spell_priest.cpp", 5422): "target",
    ("spell_rogue.cpp", 285): "draw-no-recipient", ("spell_rogue.cpp", 668): "dead", ("spell_rogue.cpp", 1014): "spell-choice",
    ("spell_rogue.cpp", 1020): "spell-choice",
    ("spell_shaman.cpp", 304): "target", ("spell_shaman.cpp", 779): "target", ("spell_shaman.cpp", 1066): "spell-choice",
    ("spell_shaman.cpp", 1069): "spell-choice", ("spell_shaman.cpp", 1418): "target", ("spell_shaman.cpp", 1432): "target",
    ("spell_shaman.cpp", 1940): "target", ("spell_shaman.cpp", 2260): "target", ("spell_shaman.cpp", 2265): "target",
    ("spell_shaman.cpp", 2389): "target", ("spell_warlock.cpp", 1140): "target", ("spell_warrior.cpp", 1945): "target",
}
TARGETING_ROLES = ("target", "target-count", "dest", "draw-no-recipient")
HOOK_METRIC_CALLEES = {
    "health": {"GetHealth", "GetMaxHealth", "GetHealthPct", "IsFullHealth", "HealthBelowPct", "HealthAbovePct",
               "HealthBelowPctDamaged", "HealthPctOrderPred", "SelectRandomInjuredTargets"},
    "aura": {"HasAura", "HasAuraEffect", "GetAura", "GetAuraEffect", "UnitAuraCheck", "HasAuraType", "HasAuraState"},
    "group": {"IsInRaidWith", "IsInPartyWith", "GetGroup", "SelectRandomInjuredTargets"},
    "distance": {"GetDistance", "GetExactDist", "GetExactDist2d", "IsWithinDist", "IsWithinDistInMap", "ObjectDistanceOrderPred",
                 "GetDistanceOrder", "IsInRange"},
    "random": {"RandomResize", "SelectRandomContainerElement", "RandomShuffle", "urand", "SelectRandomInjuredTargets",
               "SortTargetsWithPriorityRules", "frand", "rand_norm"},
    "rank": {"sort", "SortTargetsWithPriorityRules", "min_element", "max_element", "partition", "PowerPctOrderPred"},
    "role": {"GetPrimarySpecialization", "GetSpecializationId", "IsTank", "GetRole"},
    "unit-kind": {"IsPlayer", "IsPet", "IsGuardian", "IsTotem", "IsSummon", "IsCreature"},
    "explicit": {"GetExplTargetUnit", "GetExplTargetWorldObject"},
    "count": {"size", "resize"},
}


def _script_extended_reach(ctx) -> dict[int, int]:
    """spell -> hop count reached through script references (class refs / validate) from ``reach``.

    Structural navigation only (``script-referenced`` in the Dummy pass): a referenced
    SpellID is not proven to be cast.
    """
    from .oracle import _bindings
    bm = _bindings()
    depth = {s: 0 for s in ctx.scope.reach}
    frontier = set(ctx.scope.reach)
    hop = 0
    while frontier and hop < 6:
        hop += 1
        nxt: set[int] = set()
        for s in frontier:
            for b in bm.bindings(s):
                refs: set[int] = set(b.validate_spells)
                for h in b.hooks:
                    refs.update(int(v) for c in (h.facts or {}).get("calls", []) for v in c.get("ints", []))
                    refs.update(int(v) for v in (h.facts or {}).get("refs", {}).values())
                for r in refs:
                    if r not in depth and ctx.catalog.exists(r):
                        depth[r] = hop
                        nxt.add(r)
        frontier = nxt
    return depth


def script_sites() -> list[dict[str, Any]]:
    """Every RNG / ranking call site in player-class, item, generic and pet scripts, with bound spells."""
    from . import context
    ctx = context.get()
    idx = ctx.bundle.index
    if not probe_available():
        raise FailClosed("site scan needs the TrinityCore checkout (read-only)")
    names = ctx.bundle.script_names
    by_name: dict[str, list[int]] = defaultdict(list)
    for sp, ns in names.items():
        for n in ns:
            by_name[n].append(sp)
    cls_names: dict[str, set[str]] = defaultdict(set)
    for n in by_name:
        for c in idx.resolve_script_name(n).get("classes", []):
            cls_names[c.key].add(n)
    ext = _script_extended_reach(ctx)
    reach = ctx.scope.reach
    out = []
    classes = idx.classes
    for key, c in sorted(classes.items()):
        f = c.file
        if not f.startswith(("src/server/scripts/Spells", "src/server/scripts/Pet")):
            continue
        lines = (TC_ROOT / f).read_text(encoding="utf-8").splitlines()
        hooks = {h["handler"]: h for h in c.hooks}
        meths = sorted((m["line"], n) for n, m in c.methods.items())
        for ln in range(c.line, c.end_line + 1):
            text = lines[ln - 1]
            if text.strip().startswith("//") or not SITE_PATTERN.search(text):
                continue
            meth = None
            for ml, n in meths:
                if ml <= ln:
                    meth = n
            hook = hooks.get(meth)
            base = Path(f).name
            role = SITE_ROLES.get((base, ln))
            if role is None:
                role = "target" if hook and hook["list"] in TARGET_HOOK_LISTS else "non-targeting-rng"
            spells = sorted({s for n in cls_names.get(key, ()) for s in by_name[n]})
            out.append({
                "file": f, "line": ln, "class": c.name, "method": meth,
                "hook": hook["list"] if hook else None,
                "hook_effect": hook.get("eff_index") if hook else None,
                "hook_target": hook.get("eff_name") if hook else None,
                "call": SITE_PATTERN.search(text).group(0), "text": text.strip()[:160], "role": role,
                "spells": spells, "spells_in_reach": [s for s in spells if s in reach],
                "spells_script_extended": sorted(s for s in spells if s not in reach and s in ext),
                "evidence": "script-consumer",
            })
    return out


def target_hook_metrics() -> list[dict[str, Any]]:
    """Per current-player target hook: which runtime facts its handler reads (structural)."""
    from . import context
    from .oracle import _bindings
    ctx = context.get()
    bm = _bindings()
    rows = []
    for s in sorted(ctx.scope.reach):
        for b in bm.bindings(s):
            for h in b.hooks:
                if h.list not in TARGET_HOOK_LISTS or not h.executes:
                    continue
                callees = {c["callee"] for c in (h.facts or {}).get("calls", [])} | set((h.facts or {}).get("other_calls", {}))
                metrics = sorted(k for k, v in HOOK_METRIC_CALLEES.items() if callees & v)
                row = {"spell": s, "name": ctx.name(s), "script": b.script_name, "list": h.list,
                       "handler": h.handler, "target": h.eff_value, "affected_mask": h.affected_mask,
                       "metrics": metrics, "callees": sorted(callees)[:40], "evidence": "structural-inference",
                       "build_skew": ctx.is_skew(s)}
                if row not in rows:
                    rows.append(row)
    return rows


def build_smart() -> dict[str, Any]:
    from dataclasses import asdict

    from . import context, smart
    ctx = context.get()
    sites = [s for s in script_sites() if s["role"] in TARGETING_ROLES]
    metrics = target_hook_metrics()
    smart_rows = [m for m in metrics if set(m["metrics"]) & {"health", "aura", "group", "random", "rank", "distance", "role", "count"}]
    classes: dict[str, dict[str, Any]] = {}
    fam_by_spell = {s: f.id for f in smart.FAMILIES for s in f.spells_in_reach}
    for m in smart_rows:
        mask = m["affected_mask"] or 0
        for e in ctx.data.effects(m["spell"]):
            if not (mask & (1 << e.index)) or m["target"] not in (e.target_a, e.target_b):
                continue
            tags = {"script-adapter"}
            if {"health", "group"} & set(m["metrics"]):
                tags.add("smart")
            if "random" in m["metrics"]:
                tags.add("random")
            modelled = m["spell"] in fam_by_spell
            key = f"{m['spell']}:{e.index}"
            classes[key] = {"class": "understood" if modelled else "fixture-dependent",
                            "tags": sorted(tags), "unknowns": [] if modelled else ["TG-D-10"],
                            "build_skew": m["build_skew"], "family": fam_by_spell.get(m["spell"])}
    # chain heal belongs to the smart family too
    for s, e in ((1064, 0),):
        classes[f"{s}:{e}"] = {"class": "understood", "tags": ["chain-heal", "smart"], "unknowns": [],
                               "build_skew": ctx.is_skew(s), "family": "chain-heal-deficit"}
    in_reach_sites = [s for s in sites if s["spells_in_reach"]]
    ext_sites = [s for s in sites if not s["spells_in_reach"] and s["spells_script_extended"]]
    helper_calls = [s for s in sites if s["call"] in ("SelectRandomInjuredTargets", "SortTargetsWithPriorityRules")]
    return {
        "provenance": _provenance("targeting.py smart-selection --out docs/research/targeting-corpora/smart-selection.json"),
        "schema": "targeting-smart/1",
        "counts": {
            "targeting_script_sites": len(sites),
            "sites_bound_to_reach": len(in_reach_sites),
            "sites_bound_only_to_script_extended_reach": len(ext_sites),
            "sites_unbound_or_outside": len(sites) - len(in_reach_sites) - len(ext_sites),
            "families_by_status": dict(sorted(Counter(f.status.split(":")[0] for f in smart.FAMILIES).items())),
            "shared_helper_calls": len(helper_calls),
            "shared_helper_calls_in_reach": sum(bool(s["spells_in_reach"]) for s in helper_calls),
            "shared_helper_calls_script_extended": sum(bool(s["spells_script_extended"]) and not s["spells_in_reach"] for s in helper_calls),
            "current_player_target_hooks": len(metrics),
            "state_dependent_target_hooks": len(smart_rows),
            "by_metric": dict(sorted(Counter(k for m in smart_rows for k in m["metrics"]).items())),
            "effect_classes": len(classes),
        },
        "findings": SMART_FINDINGS,
        "families": [asdict(f) for f in smart.FAMILIES],
        "sites": sites,
        "state_dependent_hooks": smart_rows,
        "effect_classes": dict(sorted(classes.items())),
        "unknowns": SMART_UNKNOWNS,
        "retail_experiments": SMART_EXPERIMENTS,
        "trinity_defects": SMART_DEFECTS,
    }


SMART_FINDINGS = [
    {"id": "TG-D-S01", "evidence": "trinity-consumer", "consumer": "Spell.cpp:9513-9573",
     "statement": "No generic smart-heal attribute exists at the pin. The shared helper SelectRandomInjuredTargets ranks "
                  "by three binary bits, most significant first: full health, not a player/TreatAsRaidUnit creature, "
                  "not in raid with X (an injured stranger beats a full-health group member); injured "
                  "is IsFullHealth only -- no deficit or health-% ranking -- and ties inside the boundary class are "
                  "broken by RandomShuffle. size <= max: unchanged, no draws."},
    {"id": "TG-D-S02", "evidence": "trinity-probe", "consumer": "Spell.cpp:9559-9569, libstdc++13 stl_algo.h",
     "statement": "The boundary class is shuffled even when it exactly fills the cap (draws consumed, kept set fixed, order "
                  "changes); higher classes keep the std::ranges::sort order (libstdc++13: insertion sort, i.e. input "
                  "order, for <= 16 candidates; implementation-defined otherwise)."},
    {"id": "TG-D-S03", "evidence": "trinity-probe", "consumer": "Spell.cpp:9575-9613, spell_priest.cpp:3387-3397",
     "statement": "SortTargetsWithPriorityRules scores rule bits (earlier rules dominate), sorts descending and, when the "
                  "cutoff splits an equal-score group, shuffles the whole equal-score range (including already-kept "
                  "members). Power Word: Radiance: explicit target > missing own Atonement > injured > player/raid unit > "
                  "in raid; the explicit target only wins if the area search returned it."},
    {"id": "TG-D-S04", "evidence": "trinity-consumer", "consumer": "Spell.cpp:1436-1450",
     "statement": "Script area hooks run before the MaxAffectedTargets RandomResize; a hook that already reduced the list to "
                  "<= cap causes no further draws."},
    {"id": "TG-D-S05", "evidence": "trinity-consumer", "consumer": "Unit.cpp:10920-10949",
     "statement": "Unit::SelectNearbyTarget (Blade Flurry 13877) picks uniformly among alive, non-friendly (neutral "
                  "allowed), in-LOS, non-totem/critter/spirit-service units within 5y (+reaches), excluding the current "
                  "victim and the proc's action target; one draw during DoCheckProc, before the aura's proc roll. At the pin the "
                  "draw and the proc gate are live but the chosen unit receives nothing (TG-D-S08)."},
    {"id": "TG-D-S08", "evidence": "trinity-consumer", "consumer": "spell_rogue.cpp:280-301, 664-677, 711-735",
     "statement": "Script-layout drift (R3-01/R3-06): Blade Flurry 13877 still draws in DoCheckProc (SelectNearbyTarget) and "
                  "gates the proc, but its recipient-acting HandleProc has mask 0 on 12.1 rows, so no extra attack is cast "
                  "(draw-no-recipient). Killing Spree 51690's target list is filled by a SpellScript hook on EFFECT_1, which is "
                  "an aura effect on 12.0.7/12.1 rows (mask 0): the periodic picker never runs (dead)."},
    {"id": "TG-D-S06", "evidence": "structural-inference", "consumer": "dummy-corpora/script-index.json",
     "statement": "Most Trinity smart-heal hooks (Circle of Healing 204883, Healing Rain 73921, Prayer of Mending jump "
                  "155793, Efflorescence 81269, Healing Stream Totem 52042, Downpour 207778, ...) are bound to spells "
                  "reached only through scripts / area triggers / summons, i.e. outside ctx.scope.reach (authored reach). "
                  "They are listed with spells_script_extended; effect_classes only covers reach."},
    {"id": "TG-D-S07", "evidence": "trinity-consumer", "consumer": "CommonPredicates.cpp:39-46",
     "statement": "HealthPctOrderPred compares float(health)/float(maxHealth) (0 for non-units / max 0) and is used with "
                  "std::list::sort (stable); none of its three users is bound to a current-player reach spell."},
]

SMART_UNKNOWNS = [
    {"id": "TG-D-10", "subject": "current-player target hooks whose state-dependent filter is not ported to smart.py",
     "evidence": "structural-inference", "known": "handler callees (state_dependent_hooks)",
     "unknown": "exact recipient policy of those handlers", "why_unresolved": "Track E owns generic adapters; only the smart/random families are ported here.",
     "reopen_condition": "Track E adapter lands for the hook.", "build_skew": "n/a"},
    {"id": "TG-D-11", "subject": "Retail smart healing (Wild Growth, Healing Rain, Circle of Healing, Radiance)",
     "evidence": "unresolved", "known": "Trinity: binary injured bit + group bit + random; Radiance: 5 rule bits.",
     "unknown": "Retail ranking (deficit? health %? incoming damage?) and tie-breaks.",
     "why_unresolved": "Tooltips say 'injured allies'; no client data encodes the ranking.",
     "reopen_condition": "Retail experiment TG-D-X10.", "build_skew": "n/a"},
    {"id": "TG-D-12", "subject": "std::ranges::sort tie order for > 16 candidates / other standard libraries",
     "evidence": "trinity-probe", "known": "libstdc++13 insertion sort for <= 16 (stable).",
     "unknown": "Order of equal keys beyond 16 elements or with MSVC/libc++ builds.",
     "why_unresolved": "Implementation-defined.", "reopen_condition": "never semantic; oracle fails closed",
     "build_skew": "n/a"},
    {"id": "TG-D-13", "subject": "script-extended reach", "evidence": "structural-inference",
     "known": "Smart-heal payload spells are cast by scripts/area triggers of reach spells.",
     "unknown": "Whether the lead's census should include script-extended spells.",
     "why_unresolved": "ctx.scope.reach follows authored edges only.", "reopen_condition": "lead decision", "build_skew": False},
]

SMART_EXPERIMENTS = [
    {"id": "TG-D-X10", "question": "Wild Growth: with 8 party members in range, 3 injured and 5 full, which 6 are hit?",
     "model_a": "Trinity: all 3 injured + 3 random full-health (injured > player > grouped bit order)",
     "model_b": "lowest health % first, then nearest",
     "setup": "Pre-damage 3 members (different %), cast Wild Growth on a full-health member; repeat 20x.",
     "observable": "SPELL_AURA_APPLIED destGUIDs", "fidelity": "exact"},
    {"id": "TG-D-X11", "question": "Power Word: Radiance: does the explicit target always receive it and are Atonement-less players preferred?",
     "model_a": "Trinity rule order (explicit, no Atonement, injured, player, raid)", "model_b": "injured first",
     "setup": "5 allies, 2 with the caster's Atonement and injured, 3 full health without Atonement.",
     "observable": "SPELL_HEAL / Atonement applications", "fidelity": "exact"},
]

SMART_DEFECTS = [
    {"id": "TG-D-DEF-10", "file_line": "Spell.cpp:9559-9567",
     "description": "The boundary class is shuffled when found + count == max (no selection needed).",
     "effect_on_recipients": "Recipient set unchanged; AddUnitTarget order (hit-roll order, chain multipliers) and RNG consumption change.",
     "oracle_behaviour": "Reproduced."},
]


# ---------------------------------------------------------------------------
# rng.json
# ---------------------------------------------------------------------------
def build_rng() -> dict[str, Any]:
    from . import context, rng
    from .recipients import selection_plan
    from .selectors import info as sel_info
    ctx = context.get()
    classes: dict[str, dict[str, Any]] = {}
    random_dest_rows = []
    cap_rows = []
    grouping_rows = []
    for s in sorted(ctx.scope.reach):
        effects = ctx.data.effects(s)
        if not effects:
            continue
        rest = ctx.data.restrictions(s) or {}
        maxt = int(rest.get("MaxTargets", 0) or 0)
        for e in effects:
            if not e.is_effect:
                continue
            key = f"{s}:{e.index}"
            tags: set[str] = set()
            for slot, t in (("A", e.target_a), ("B", e.target_b)):
                if t in RANDOM_DIR_TARGETS:
                    tags.add("random-dest")
                    radius_draw, via = calc_radius_draws(ctx, e, slot)
                    order = ("angle" if not radius_draw else "radius, angle" if t in (72, 73, 149)
                             else "angle, radius")
                    random_dest_rows.append({"spell": s, "effect": e.index, "name": ctx.name(s), "slot": slot,
                                             "target": t, "target_name": sel_info(t).name,
                                             "draws": "radius+angle" if radius_draw else "angle",
                                             "radius_path": via, "order": order,
                                             "consumer": "SpellInfo.cpp:128-129, 783-829; Spell.cpp:1608-1609/1672-1673/1724-1725"})
                if t and sel_info(t).category in ("AREA", "CONE") and maxt:
                    tags.add("random-cap")
            if "random-cap" in tags:
                cap_rows.append({"spell": s, "effect": e.index, "max_targets": maxt, "name": ctx.name(s)})
            if tags:
                classes[key] = {"class": "fixture-dependent" if "random-dest" in tags else "understood",
                                "tags": sorted(tags | ({"world-geometry", "dest"} if "random-dest" in tags else set())),
                                "unknowns": ["TG-D-21"] if "random-dest" in tags else [], "build_skew": ctx.is_skew(s)}
        # grouping comparison draws
        view, _ = _view(s)
        if view is None:
            continue
        effs = view.effects
        calls: list[tuple[int, int, str]] = []

        def radius_fn(k: int, ab: str, _calls=calls) -> tuple[float, float]:
            # SpellInfo.cpp:788-795: TargetB entry only when it exists, else TargetA's target/entry
            e = effs[k]
            use_b = ab == "B" and e.radius_b is not None
            tgt = e.target_b if use_b else e.target_a
            entry = e.radius_b if use_b else e.radius_a
            _calls.append((k, tgt, ab))
            if entry is not None and tgt in RANDOM_RADIUS_TARGETS:
                return (entry.min, math.nan)  # a drawn radius: never equal (census stand-in, no value)
            return (entry.min, entry.max) if entry is not None else (0.0, 0.0)
        try:
            from .recipients import script_effect_check
            selection_plan(effs, radius_fn, script_effect_check(view))
        except FailClosed:
            continue
        drawing = [c for c in calls if c[1] in RANDOM_RADIUS_TARGETS
                   and (effs[c[0]].radius_b if (c[2] == "B" and effs[c[0]].radius_b is not None) else effs[c[0]].radius_a) is not None]
        if drawing:
            grouping_rows.append({"spell": s, "name": ctx.name(s), "calcradius_calls": len(calls), "drawing_calls": len(drawing),
                                  "effects": sorted({c[0] for c in drawing})})
            for k in sorted({c[0] for c in drawing}):
                cls = classes.setdefault(f"{s}:{k}", {"class": "fixture-dependent", "tags": [], "unknowns": [], "build_skew": ctx.is_skew(s)})
                cls["tags"] = sorted(set(cls["tags"]) | {"rng", "grouping-draw"})
                cls["unknowns"] = sorted(set(cls["unknowns"]) | {"TG-D-22"})
    sites = script_sites()
    rng_sites = [x for x in sites if x["spells_in_reach"]]
    return {
        "provenance": _provenance("targeting.py rng --out docs/research/targeting-corpora/rng.json"),
        "schema": "targeting-rng/1",
        "generator": {"file": "src/common/Utilities/Random.cpp", "engine": "thread_local SFMTRand via RandomEngine; "
                      "urand/irand = std::uniform_int_distribution, frand/rand_norm/rand_chance = uniform_real_distribution<float>",
                      "shared": True, "evidence": "trinity-consumer"},
        "cast_order": [{"phase": p, "consumer": c, "draws": d} for p, c, d in rng.CAST_ORDER],
        "spell_hit_order": rng.SPELL_HIT_ORDER,
        "engine_sites": list(rng.ENGINE_SITES),
        "worked_example": worked_example(),
        "launch_draw_census": launch_draw_census(ctx),
        "counts": {
            "random_dest_effects": len(random_dest_rows),
            "random_cap_effects": len(cap_rows),
            "grouping_draw_spells": len(grouping_rows),
            "script_rng_sites_total": len(sites),
            "script_rng_sites_bound_to_reach": len(rng_sites),
            "script_rng_sites_by_role_in_reach": dict(sorted(Counter(x["role"] for x in rng_sites).items())),
            "effect_classes": len(classes),
        },
        "random_dest_effects": random_dest_rows,
        "random_cap_effects": cap_rows,
        "grouping_draw_census": grouping_rows,
        "script_sites_in_reach": rng_sites,
        "effect_classes": dict(sorted(classes.items())),
        "unknowns": RNG_UNKNOWNS,
        "retail_experiments": RNG_EXPERIMENTS,
        "trinity_defects": RNG_DEFECTS,
    }


def calc_radius_draws(ctx, e, slot: str) -> tuple[bool, str]:
    """Does ``CalcRadius(caster, slot)`` of this effect consume ``rand_norm``?

    Mirrors: SpellInfo.cpp:783-829 -- TargetB uses its own entry/selector only when
    ``HasRadius(TargetB)``, otherwise TargetA's; no entry -> return before the random branch
    (800-801); the draw happens only when the *resolved* selector is 72/74/86.
    """
    radii = ctx.data.radii
    has_b = bool(e.radius_b) and e.radius_b in radii
    use_b = slot == "B" and has_b
    idx = e.radius_b if use_b else e.radius_a
    tgt = e.target_b if use_b else e.target_a
    if not idx or idx not in radii:
        return False, f"no radius entry on {'B' if use_b else 'A'} (SpellInfo.cpp:800-801)"
    return tgt in RANDOM_RADIUS_TARGETS, f"entry {idx} of {'B' if use_b else 'A'}, resolved selector {tgt}"


def launch_draw_census(ctx) -> dict[str, Any]:
    """Draw sites between the hit rolls and the crit rolls of one cast (R2-02), over current reach."""
    import csv
    from . import ROOT
    props = {}
    with open(ROOT / "data" / "tables" / "SummonProperties.csv", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            props[int(r["ID"])] = (int(r["Control"]), int(r["Title"]), int(r["Flags_0"]))
    multi = {64, 61, 1101, 66, 648, 2301, 1061, 1261, 629, 181, 715, 1562, 833, 1161, 713}  # SpellEffects.cpp:1915-1934
    summon_default_radius, summon_multi, transmitted = [], [], []
    for s in sorted(ctx.scope.reach):
        for e in ctx.data.effects(s):
            if e.effect == 28 and e.misc0:
                p = props.get(e.misc1)
                if p is None:
                    continue
                control, title, flags = p
                if e.misc1 in multi:
                    summon_multi.append({"spell": s, "effect": e.index})
                # SpellEffects.cpp:1941-1994: JoinSummonerSpawnGroup / Pet,Guardian,Runeblade,Minion -> SummonGuardian;
                # Vehicle,Mount,Lightwell,Totem,Companion -> own branches; anything else -> default (1996)
                other_branch = bool(flags & 0x200) or title in (1, 2, 3, 4, 5, 6, 9, 10, 11)
                if control in (0, 1) and not other_branch:
                    draws, _ = calc_radius_draws(ctx, e, "A")
                    if draws:
                        summon_default_radius.append({"spell": s, "effect": e.index})
            if e.effect == 50:
                transmitted.append({"spell": s, "effect": e.index})
    return {
        "summon_default_branch_calcradius_draw": summon_default_radius,
        "summon_multiple_units_getrandompoint": summon_multi,
        "transmitted_launch_draws": transmitted,
        "note": "EffectSummonType (SpellEffects.cpp:1867, LAUNCH mode): the WILD/ALLY default title branch calls "
                "CalcRadius() (1999; draws for TARGET 72/74/86 with an entry) and GetRandomPoint (2 draws) for every "
                "summon after the first; SummonGuardian (5035) uses GetRandomPoint(radius 5) per extra summon; "
                "numSummons > 1 only for the MiscValueB list at 1915-1934. EffectTransmitted (4454, LAUNCH) draws "
                "rand_norm/urand. Current reach hits none of them (lists empty).",
        "evidence": "trinity-consumer",
    }


def worked_example() -> dict[str, Any]:
    """Area cap 2 over 5 candidates, magic spell: 5 urand draws, 2 hit draws, then crit draws."""
    from .fixture import World
    from .rng import HitFacts, area_cap_cast
    w = World.from_dict({
        "schema": "targeting-fixture/1", "name": "rng-area-cap-order", "spell": {"synthetic": True},
        "caster": "c", "actors": [{"id": a, "kind": "creature"} for a in ("c", "t1", "t2", "t3", "t4", "t5")],
        "rng": {"draws": [5, 1, 3, 1, 1, 4200, 17, 55.5, 3.25]},
        "hit_outcomes": {"t2": "NONE", "t4": "NONE"}})
    facts = HitFacts(immune=False, damage_immune=False, positive_not_hostile=False, self_target=False, evading=False,
                     can_reflect=False, reflect_chance=0.0, always_hit=False, dmg_class=1, no_avoidance=False,
                     victim_dead_non_player=False)
    res = area_cap_cast(w, ["t1", "t2", "t3", "t4", "t5"], 2, {t: facts for t in ("t2", "t4")}, crit_chance=10.0)
    return {"fixture": "area cap 2 of [t1..t5]; draws 5,1,3,1,1 (RandomResize), 4200,17 (irand hit t2,t4), 55.5,3.25 (crit t2,t4)",
            "kept": res["kept"], "sequence": res["draws"], "consumed": res["consumed"], "evidence": "trinity-consumer",
            "consumers": ["Containers.h:67-87", "Spell.cpp:1448", "Spell.cpp:2485", "Object.cpp:1919", "Spell.cpp:8563"]}


RNG_UNKNOWNS = [
    {"id": "TG-D-20", "subject": "engine-word consumption of RandomShuffle / uniform_int_distribution", "evidence": "trinity-probe",
     "known": "libstdc++13 forward Fisher-Yates index sequence (probe-checked); floor(n/2) distribution calls.",
     "unknown": "How many engine words each distribution call costs (rejection sampling).",
     "why_unresolved": "Library implementation detail; Retail does not run this code.",
     "reopen_condition": "only if a bit-exact Trinity replay is required", "build_skew": "n/a"},
    {"id": "TG-D-21", "subject": "random destinations (TARGET_DIR_RANDOM, *_RANDOM radius)", "evidence": "trinity-consumer",
     "known": "draw count and order per selector path; Min not added back.",
     "unknown": "the final point: MovePositionToFirstCollision / height are world geometry.",
     "why_unresolved": "no VMAP/MMAP model", "reopen_condition": "fixture states the collided point", "build_skew": False},
    {"id": "TG-D-22", "subject": "grouping radius comparison with random-radius selectors", "evidence": "trinity-consumer",
     "known": "Spell.cpp:773-774 calls CalcRadius (drawing) for each compared pair; a drawn radius almost never compares equal.",
     "unknown": "evaluation order of the two operands of != (unspecified in C++), hence which draw feeds which effect.",
     "why_unresolved": "unspecified behaviour", "reopen_condition": "compiler-specific probe if ever needed", "build_skew": False},
    {"id": "TG-D-24", "subject": "standard-library dependence of the RandomShuffle index model", "evidence": "trinity-probe",
     "known": "libstdc++ 13: forward Fisher-Yates, ascending swap positions, floor(n/2) uniform_int_distribution calls "
              "(one per pair, one extra first when n is even); probe-logged swaps.",
     "unknown": "Swap order / index mapping under libc++ or MSVC builds of Trinity (different shuffle implementations).",
     "why_unresolved": "Implementation-defined; the oracle's fixture draws are swap indices for libstdc++ 13 only.",
     "reopen_condition": "a build of Trinity with another standard library must be replayed", "build_skew": "n/a"},
    {"id": "TG-D-23", "subject": "GetRandomNearPosition argument evaluation order", "evidence": "trinity-consumer",
     "known": "two rand_norm() calls as arguments of one MovePosition call (Object.cpp:2779).",
     "unknown": "which draw is the distance and which the angle (unspecified order).",
     "why_unresolved": "unspecified behaviour", "reopen_condition": "compiler-specific probe", "build_skew": "n/a"},
]

RNG_EXPERIMENTS = [
    {"id": "TG-D-X20", "question": "Are capped area recipients uniform over candidates (Trinity selection sampling)?",
     "model_a": "uniform k-subset (RandomResize)", "model_b": "nearest-first / priority-based cap",
     "setup": "Capped AoE (e.g. MaxTargets 5) over 10 stacked dummies at fixed distances, 50 casts.",
     "observable": "hit frequency per dummy (combat log)", "fidelity": "approximate"},
]

RNG_DEFECTS = [
    {"id": "TG-D-DEF-20", "file_line": "Spell.cpp:773-774",
     "description": "The effect-grouping radius comparison calls CalcRadius, which consumes rand_norm for random-radius selectors "
                    "and makes the comparison effectively random.",
     "effect_on_recipients": "Effects that would share a selection are selected separately (and draw extra RNG).",
     "oracle_behaviour": "Track F's selection_plan calls radius_fn lazily, so the draws are reproduced when a fixture supplies them."},
]


# ---------------------------------------------------------------------------
# witnesses
# ---------------------------------------------------------------------------
def _wa(aid: str, kind: str, pos, hp: int = 100, mhp: int = 100, group: str | None = "g1", owner=None, **facts) -> dict:
    a = {"id": aid, "kind": kind, "pos": list(pos), "orientation": 0.0, "combat_reach": 1.5 if kind == "player" else 1.0,
         "bounding_radius": 0.389, "alive": True, "health": hp, "max_health": mhp, "owner": owner, "charmer": None,
         "facts": {"profile": "combat-sim", "spell_other_immunity": [], "in_caster_phase": True, **facts}}
    if kind == "player":
        a["group"] = None if group is None else {"id": group, "subgroup": 0}
        a["facts"].setdefault("range_movement_bonus", False)
    else:
        a["facts"].setdefault("ignore_los_on_me", False)
        a["facts"].setdefault("faction", 7)
    return a


def _wfix(name: str, spell: int, caster: str, actors: list[dict], order: list[str], relations: list[dict],
          explicit: dict, draws: list, about: str, discriminates: str, override: dict | None = None,
          **extra: Any) -> dict[str, Any]:
    return {"schema": "targeting-fixture/1", "name": name, "about": about, "discriminates": discriminates,
            "spell": {"id": spell, "difficulty": 0, "triggered_by_aura": None,
                      "override": {"los_disabled": False, **(override or {})}},
            "caster": caster, "explicit": explicit, "actors": actors, "relations": relations, "visit_order": order,
            "los": {"default": "clear"}, "rng": {"draws": draws},
            "spell_value": {"radius_mod": 1.0},
            "modifiers": {"radius": 0, "range": 0, "chain_targets": 0, "chain_jump_distance": 0, "max_targets": 0},
            "cast": {"original_caster_is_go": False}, "immunity": {"default": []}, **extra}


def witnesses() -> list[dict[str, Any]]:
    """Track D witness proposals; expected recipients come from ``pipeline.evaluate`` where it runs."""
    out: list[dict[str, Any]] = []
    # 1. Chain Heal: deficit (not health %) and previous-target reference
    acts = [_wa("sham", "player", (0, 0, 0)), _wa("p", "player", (10, 0, 0), hp=90),
            _wa("a", "player", (20, 0, 0), hp=60_000, mhp=100_000), _wa("b", "player", (19, 3, 0), hp=20_000, mhp=50_000),
            _wa("c", "player", (26, 5, 0))]
    rel = [{"from": "sham", "to": t, "valid_assist": True} for t in ("sham", "p", "a", "b", "c")]
    out.append({"spell": 1064, "effect": 0, "shape": "chain-heal", "selectors": [45, 0],
                "consumer": "Spell.cpp:1825 -> 1832-1864, 2221-2327",
                "fixture": _wfix("witness-chain-heal-deficit", 1064, "sham", acts, ["p", "b", "a", "c", "sham"], rel,
                                 {"unit": "p"}, [], "Chain Heal (4 targets) on p: jumps pick the largest absolute deficit "
                                 "within 12.5y (+reaches) of the previous target; a full-health unit is taken when no "
                                 "injured one is in range.", "max-deficit (a: 40000 missing, 60%) vs lowest-health-% "
                                 "(b: 30000 missing, 40%): Trinity p,a,b,c; a %-model gives p,b,a,c",
                                 override={"positive": True}, hit={"draws": {"default": 0}}),
                "discriminates": "deficit vs health-% ranking; the caster (in the pre-filter) is never in 12.5y of a jump source here"})
    # 2. Chain Lightning: nearest to previous target
    acts = [_wa("sham", "player", (0, 0, 0))] + [_wa(n, "creature", pos) for n, pos in
                                                 (("P", (20, 0, 0)), ("X", (28, 0, 0)), ("Y", (36, 0, 0)), ("Z", (11.5, 0, 0)))]
    rel = [{"from": "sham", "to": t, "valid_attack": True} for t in ("P", "X", "Y", "Z")] + \
        [{"from": "sham", "to": "sham", "valid_attack": False}]
    out.append({"spell": 188443, "effect": 0, "shape": "chain-damage", "selectors": [6, 0],
                "consumer": "Spell.cpp:1825 -> 2300-2325",
                "fixture": _wfix("witness-chain-lightning-previous-target", 188443, "sham", acts, ["P", "Z", "X", "Y", "sham"],
                                 rel, {"unit": "P"}, [], "Chain Lightning (3 targets, no Elemental Shaman / Chaining Storms "
                                 "mods) on P: each jump is the nearest candidate to the previous target (10y + reaches).",
                                 "nearest-to-previous P,X,Y vs nearest-to-primary P,X,Z vs nearest-to-caster P,Z,X",
                                 override={"positive": False}, hit={"draws": {"default": 0}}),
                "discriminates": "jump reference point"})
    # 3. Wild Growth: binary injured priority + shuffle of the boundary class
    acts = [_wa("dru", "player", (0, 0, 0))] + [
        _wa("t", "player", (10, 0, 0)), _wa("h1", "player", (12, 0, 0), hp=99), _wa("h2", "player", (8, 2, 0), hp=5),
        _wa("o", "player", (11, 3, 0), hp=10, group="g2"), _wa("f1", "player", (9, -2, 0)), _wa("f2", "player", (13, 1, 0)),
        _wa("f3", "player", (10, 4, 0))]
    names = ["t", "h1", "h2", "o", "f1", "f2", "f3"]
    rel = [{"from": "dru", "to": x, "valid_assist": True, "friendly": True} for x in names + ["dru"]]
    from .fixture import World
    from .smart import select_random_injured_targets
    pre = ["t", "h1", "o", "f1", "h2", "f2", "f3", "dru"]
    wtmp = World.from_dict(_wfix("tmp", 48438, "dru", acts, pre, rel, {"unit": "t"}, [1, 0, 3, 2], "", ""))
    post = select_random_injured_targets(wtmp, pre, 5, True, "dru")
    out.append({"spell": 48438, "effect": 0, "shape": "smart-heal", "selectors": [63, 31],
                "consumer": "Spell.cpp:1436 -> spell_druid.cpp:3021-3032 -> Spell.cpp:9513-9573",
                "fixture": _wfix("witness-wild-growth-injured", 48438, "dru", acts, pre, rel, {"unit": "t"}, [1, 0, 3, 2],
                                 "Wild Growth (maxTargets 5 = EFFECT_1 value, no Tree of Life) over 8 allies in the area: "
                                 "the grouped injured players h1, h2 (priority 0) and the injured non-group player o "
                                 "(priority 1) always win -- injured dominates grouping; the 5 full-health group players "
                                 "(incl. caster and target, priority 4) are the boundary class, shuffled with FY indices "
                                 "[1, 0, 3, 2], and the first 2 are kept.",
                                 "binary priority (h1,h2,o + 2 of {t,f1,f2,f3,dru}) vs lowest-health-% or deficit "
                                 "ranking (h2 first) vs group-first (full-health group members before o)",
                                 override={"positive": True},
                                 script_results={"OnObjectAreaTargetSelect:0:31": {"result": post, "draws": 4}},
                                 smart_check={"function": "smart.select_random_injured_targets", "input": pre,
                                              "max_targets": 5, "prioritize_players": True, "group_of": "dru",
                                              "hook_draws": [1, 0, 3, 2], "output": post},
                                 hit={"draws": {"default": 0}}),
                "discriminates": "injured is a bit, not a ranking; bit order injured > player > grouped"})
    # 4. Starfall dummy: uniform random pick of 2 (selection sampling)
    acts = [_wa("dru", "player", (0, 0, 0))] + [_wa(f"e{i}", "creature", (2 * i, 1, 0)) for i in range(1, 4)]
    rel = [{"from": "dru", "to": f"e{i}", "valid_attack": True} for i in range(1, 4)] + \
        [{"from": "dru", "to": "dru", "valid_attack": False}]
    wtmp = World.from_dict(_wfix("tmp", 50286, "dru", acts, ["e1", "e2", "e3", "dru"], rel, {}, [3, 1, 1], "", ""))
    from .rng import random_resize
    post = random_resize(wtmp, ["e1", "e2", "e3"], 2)
    out.append({"spell": 50286, "effect": 0, "shape": "random-target", "selectors": [87, 16],
                "consumer": "spell_druid.cpp:2272-2275 -> Containers.h:67; Spell.cpp:1444-1448 (cap 20: no draw)",
                "fixture": _wfix("witness-starfall-random-two", 50286, "dru", acts, ["e1", "e2", "e3", "dru"], rel,
                                 {"dest": [2, 0, 0]}, [3, 1, 1], "Starfall dummy: the hook keeps 2 of 3 enemies by selection "
                                 "sampling (draws urand(1,3)=3, urand(1,2)=1, urand(1,1)=1 -> e2, e3); the spell's "
                                 "MaxAffectedTargets 20 cap then draws nothing.",
                                 "selection sampling (3 draws, order kept) vs first-N / nearest-N (e1,e2)",
                                 override={"positive": False},
                                 script_results={"OnObjectAreaTargetSelect:0:16": {"result": post, "draws": 3}},
                                 smart_check={"function": "rng.random_resize", "input": ["e1", "e2", "e3"], "n": 2,
                                              "hook_draws": [3, 1, 1], "output": post},
                                 hit={"draws": {"default": 0}}),
                "discriminates": "uniform subset with visit order kept; hook before cap"})
    for w in out:
        w["expected_recipients"], w["pipeline"] = _evaluate_witness(w["fixture"])
    return out


def _evaluate_witness(fx: dict[str, Any]) -> tuple[dict[str, list[str]] | None, dict[str, Any]]:
    from .fixture import World
    from .pipeline import evaluate
    try:
        res = evaluate(World.from_dict(fx))
    except FailClosed as exc:
        return None, {"status": "fail-closed", "reason": str(exc)}
    return ({str(k): v for k, v in sorted(res.recipients.items())},
            {"status": "evaluated", "cast_result": res.cast_result, "draws_consumed": res.draws_consumed,
             "defects": sorted(set(res.defects))})


def build_witnesses() -> list[dict[str, Any]]:
    return witnesses()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _out(p) -> None:
    p.add_argument("--out")


def _chains(args) -> int:
    emit(build_chains(), args.out)
    return 0


def _smart(args) -> int:
    emit(build_smart(), args.out)
    return 0


def _rng(args) -> int:
    emit(build_rng(), args.out)
    return 0


def _witnesses(args) -> int:
    emit(build_witnesses(), args.out)
    return 0


def _probe(args) -> int:
    res = run_probe(["resize 2 5\ndraws 3 1 2 1 1\ngo", "shuffle 5\nwords 1 2 3\ngo"])
    emit(res, args.out)
    return 0


COMMANDS = {
    "chains": ("Track D: chain-targeting census corpus", _out, _chains),
    "smart-selection": ("Track D: smart/injured/ranked selection corpus", _out, _smart),
    "rng": ("Track D: target-selection RNG inventory and ordering", _out, _rng),
    "d-witnesses": ("Track D: witness proposals (chain heal, chain damage, smart heal, random target)", _out, _witnesses),
    "d-probe": ("Track D: build and smoke-run tools/tc_target_chain_probe", _out, _probe),
}
