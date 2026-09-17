"""Track F commands: group policy, effect-mask grouping, effect-local recipients.

    targeting.py groups [--out FILE]              group-policy.json
    targeting.py effect-groups SPELL              selection plan of one spell (static radius relation)
    targeting.py effect-recipients [--out FILE]   effect-recipients.json
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from functools import cache
from typing import Any

from . import CORPORA, CU_CORPORA, PINS, FailClosed
from .cli import emit
from .groups import GROUP_SELECTORS, RELATION_SELECTORS
from .oracle import RadiusEntry, f32
from .recipients import (
    RANDOM_RADIUS_TARGETS,
    category,
    implicit_condition_identity,
    script_effect_check,
    selection_plan,
)
from .selectors import TARGET_NAMES, info

LEVELS = range(1, 91)   # caster level range probed for the radius relation (MaxPlayerLevel 90)
TARGETING_MEMBERS = {"TargetA", "TargetB", "TargetARadiusEntry", "TargetBRadiusEntry", "ChainTargets", "Effect",
                     "EffectAttributes", "MaxAffectedTargets", "ConeAngle", "ExplicitTargetMask", "AttributesCu"}
FURTHEST = 115
PARTY_RAID_CHECKS = ("PARTY", "RAID", "RAID_CLASS")
#: DEST selectors whose selection does NOT read CalcRadius (Spell.cpp:1471-1596, 1666-1669, 1702-1709)
DEST_NO_RADIUS = {18, 9, 17, 39, 62, 125, 131, 53, 63, 132, 28, 29, 88, 87, 138}
#: DEST selectors reading SpellTargetPosition by the (lead) effect index (Spell.cpp:1477, 1587; 1160)
DEST_BY_EFFECT_INDEX = {17, 106, 142}


# ---------------------------------------------------------------------------
# light census view (no corrections refusal: corrections are tagged instead)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CEffect:
    index: int
    effect: int
    target_a: int
    target_b: int
    radius_a: RadiusEntry | None
    radius_b: RadiusEntry | None
    chain_targets: int
    attributes: int
    pos_facing: float
    conditions: int | None = None


@dataclass(frozen=True)
class CView:
    id: int
    effects: tuple[CEffect, ...]
    script_hooks: tuple[dict[str, Any], ...] | None
    max_affected_targets: int
    no_movement_bonus: bool
    unresolved_hooks: tuple[str, ...]


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


@cache
def _conditions() -> dict[int, list[dict]]:
    from . import context
    return dict(context.get().bundle.world.conditions_by_source().get(13, {}))


def view(spell: int) -> CView:
    from . import context
    ctx = context.get()
    d = ctx.data
    rows = d.effects(spell)
    if not rows:
        raise FailClosed(f"spell {spell}: no DIFFICULTY_NONE effect rows")

    def radius(idx: int) -> RadiusEntry | None:
        row = d.radii.get(idx) if idx else None
        return RadiusEntry.from_row(row) if row is not None else None

    by_index = {e.index: e for e in rows}
    dense = []
    for k in range(max(by_index) + 1):
        e = by_index.get(k)
        if e is None:
            dense.append(CEffect(k, 0, 0, 0, None, None, 0, 0, 0.0))
        else:
            dense.append(CEffect(k, e.effect, e.target_a, e.target_b, radius(e.radius_a), radius(e.radius_b),
                                 e.chain_targets, e.attributes, f32(e.pos_facing)))
    conds = _conditions().get(spell, [])
    if conds:
        ids = implicit_condition_identity(dense, [(int(c["SourceGroup"]), int(c["ConditionValue1"])
                                                   if int(c["ConditionTypeOrReference"]) == 31 else None) for c in conds])
        dense = [CEffect(**{**e.__dict__, "conditions": ids[e.index]}) for e in dense]
    hooks, unresolved = [], []
    for b in _bindings().bindings(spell):
        for h in b.hooks:
            if h.list in ("OnObjectAreaTargetSelect", "OnObjectTargetSelect", "OnDestinationTargetSelect"):
                hooks.append({"list": h.list, "target": h.eff_value, "affected_mask": h.affected_mask,
                              "script": b.script_name, "handler": h.handler})
                if h.affected_mask is None:
                    unresolved.append(f"{b.script_name}::{h.handler}")
    rest = d.restrictions(spell) or {}
    info_ = ctx.catalog.get(spell)
    from procs.enums import attr
    w, bit = attr("SPELL_ATTR9_NO_MOVEMENT_RADIUS_BONUS")
    no_move = bool(info_ is not None and info_.attributes[w] & bit)
    return CView(spell, tuple(dense), tuple(hooks), int(rest.get("MaxTargets", 0)), no_move, tuple(unresolved))


# ---------------------------------------------------------------------------
# static radius relation
# ---------------------------------------------------------------------------

def _radius_source(e: CEffect, idx: str) -> tuple[RadiusEntry | None, int]:
    """Mirrors: SpellInfo.cpp:788-794 (TargetB falls back to TargetA's entry *and* target)."""
    if idx == "B" and e.radius_b is not None:
        return e.radius_b, e.target_b
    return e.radius_a, e.target_a


def _radius_value(e: CEffect, idx: str, level: int, moving: bool) -> tuple[float, float] | str:
    """``CalcRadius(unit caster)`` without spell mods (identical for every effect of a spell).

    Mirrors: SpellInfo.cpp:783-828.  RANDOM targets return ``"rng"`` (a ``rand_norm`` draw).
    """
    entry, target = _radius_source(e, idx)
    if entry is None:
        return (0.0, 0.0)
    rmin = entry.min
    rmax = min(f32(entry.radius + f32(entry.per_level * level)), entry.max)
    if moving:
        rmin = max(f32(rmin - 2.0), 0.0)
        rmax = f32(rmax + 2.0)
    if target in RANDOM_RADIUS_TARGETS:
        return "rng"
    return (rmin, rmax)


def radius_relation(v: CView, i: int, j: int) -> str:
    """``equal`` / ``unequal`` / ``caster-dependent`` / ``rng`` for the grouping comparison of i and j."""
    ei, ej = v.effects[i], v.effects[j]
    seen = set()
    for level in LEVELS:
        for moving in ((False,) if v.no_movement_bonus else (False, True)):
            pairs = [(_radius_value(ei, x, level, moving), _radius_value(ej, x, level, moving)) for x in ("A", "B")]
            if pairs[0][0] == "rng" or pairs[0][1] == "rng":
                return "rng"
            if pairs[0][0] != pairs[0][1]:
                seen.add(False)
                continue
            if "rng" in pairs[1]:
                return "rng"
            seen.add(pairs[1][0] == pairs[1][1])
    if seen == {True}:
        return "equal"
    if seen == {False}:
        return "unequal"
    return "caster-dependent"


def static_plan(v: CView) -> tuple[list, dict[tuple[int, int], str], list[str]]:
    """Selection plan with the radius relation resolved statically.

    ``caster-dependent`` and ``rng`` pairs are planned as *unequal* (the RNG case is almost
    surely unequal) and recorded in the relation map for classification.
    """
    relations: dict[tuple[int, int], str] = {}
    notes: list[str] = []

    def radius_equal(i: int, j: int) -> bool:
        rel = relations.setdefault((i, j), radius_relation(v, i, j))
        return rel == "equal"

    try:
        check = script_effect_check(v)
    except FailClosed as exc:
        notes.append(f"script-check-unresolved: {exc}")

        def check(i: int, j: int) -> bool:
            return True
    plan = selection_plan(v.effects, None, check, radius_equal=radius_equal)
    return plan, relations, notes


# ---------------------------------------------------------------------------
# controlled-unit abilities (current player)
# ---------------------------------------------------------------------------

@cache
def controlled_unit_abilities() -> dict[int, list[str]]:
    """spell -> sources, for current-player controlled units (population.json default scope)."""
    from controlled_units import spells as cu
    pop = json.loads((CU_CORPORA / "population.json").read_text(encoding="utf-8"))
    entries = {r["creature_entry"] for r in pop["records"] if r.get("creature_entry")}
    cts = cu.creature_template_spells()
    out: dict[int, set[str]] = defaultdict(set)
    for entry in sorted(entries):
        for _, s in sorted(cts.get(entry, {}).items()):
            if s:
                out[s].add(f"creature_template_spell:{entry}")
    for r in cu.family_tables()["rows"]:
        out[r["spell"]].add(f"pet-family:{r['family']}")
    d = cu._db2()
    for spec_id, rows in d["spec_spells"].items():
        if d["specs"].get(spec_id, {}).get("ClassID") == "0":
            for r in rows:
                out[int(r["SpellID"])].add(f"pet-spec:{spec_id}")
    return {s: sorted(v) for s, v in out.items()}


# ---------------------------------------------------------------------------
# group policy corpus
# ---------------------------------------------------------------------------

POLICY = [
    {"subject": "TARGET_CHECK_PARTY", "rule": "CheckTarget -> referer is a Unit -> target not a totem -> caster.IsValidAssistTarget (skipped for corpses) -> referer.IsInPartyWith(target)",
     "consumer": "Spell.cpp:9306-9351", "evidence": "trinity-consumer"},
    {"subject": "TARGET_CHECK_RAID", "rule": "as PARTY with referer.IsInRaidWith",
     "consumer": "Spell.cpp:9358-9368", "evidence": "trinity-consumer"},
    {"subject": "TARGET_CHECK_RAID_CLASS", "rule": "referer is a Unit -> referer.GetClass() == unit.GetClass() (the REFERER's class: for 61 the explicit target) -> falls through to RAID",
     "consumer": "Spell.cpp:9352-9357", "evidence": "trinity-consumer"},
    {"subject": "TARGET_CHECK_SUMMONED", "rule": "unit.IsSummon() && SummonerGUID == m_caster (no totem exclusion, no assist test, referer unused); pets/guardians/totems/minions are TempSummons",
     "consumer": "Spell.cpp:9369-9374; TemporarySummon.cpp:37-45", "evidence": "trinity-consumer"},
    {"subject": "corpse targets", "rule": "a corpse stands for its owner player (FindPlayer, map-independent); no owner -> rejected; assist test skipped",
     "consumer": "Spell.cpp:9312-9319", "evidence": "trinity-consumer"},
    {"subject": "Unit::IsInPartyWith / IsInRaidWith", "rule": "self -> true; u = GetCharmerOrOwnerOrSelf (ONE level) of both; equal -> true; both players -> IsInSameGroupWith (same Group and same subgroup) / IsInSameRaidWith (same Group); player vs creature -> creature TREAT_AS_RAID_UNIT flag; creature vs creature -> same faction; else false",
     "consumer": "Unit.cpp:12184-12218; Player.cpp:2070-2080; Group.cpp:994-1003; Creature.h:235", "evidence": "trinity-consumer"},
    {"subject": "ungrouped player", "rule": "party = raid = self + units whose charmer-or-owner is the player (pets, guardians, totems -- totems then excluded by the check) + TREAT_AS_RAID_UNIT creatures; Player.GetGroup() is m_group only",
     "consumer": "Player.cpp:2070-2080", "evidence": "trinity-consumer"},
    {"subject": "pet caster", "rule": "m_caster is the pet; referer is the pet for CASTER/SRC/DEST/NEARBY selectors, so membership resolves through the pet's owner (one level); assist validity is from the pet; SUMMONED compares with the pet's GUID (units the pet summoned)",
     "consumer": "Spell.cpp:1328-1378, 9306-9374; Unit.cpp:12190-12192", "evidence": "trinity-consumer"},
    {"subject": "owner-of-owner", "rule": "a guardian owned by a pet resolves to the pet (a creature): vs players only TREAT_AS_RAID_UNIT applies -> not in party/raid with the player; TARGET_UNIT_MASTER from it is the pet",
     "consumer": "Object.cpp:1610-1624; Unit.cpp:12184-12218", "evidence": "trinity-consumer"},
    {"subject": "caps", "rule": "controlled units are ordinary candidates of the searched list: they count toward MaxAffectedTargets / RandomResize like players (no player priority in the generic area path)",
     "consumer": "Spell.cpp:1442-1452", "evidence": "trinity-consumer"},
    {"subject": "PlayersOnly effect attribute", "rule": "restricts the grid searcher to players and corpses (removes pets/guardians/totems) and is a grouping key",
     "consumer": "Spell.cpp:2150-2151, 754", "evidence": "trinity-consumer"},
    {"subject": "TARGET_UNIT_TARGET_PARTY(35) / TARGET_UNIT_TARGET_RAID(57) / TARGET_UNIT_TARGET_PASSENGER(95)", "rule": "DEFAULT category: the recipient is the explicit target without any membership filter at selection time; membership is enforced only through the explicit target mask (TARGET_FLAG_UNIT_PARTY/RAID/PASSENGER) in CheckExplicitTarget, where ALLY short-circuits and membership is tested before IsValidAssistTarget",
     "consumer": "Spell.cpp:1807-1830; SpellInfo.cpp:176-193, 2530-2562", "evidence": "trinity-consumer"},
    {"subject": "TARGET_UNIT_TARGET_ALLY_OR_RAID(118)", "rule": "no explicit unit -> nothing; caster not a Unit or !caster.IsInRaidWith(target) -> exactly the explicit target (no area/assist/cap filtering beyond AddUnitTarget); else RAID area search centred on the explicit target with referer = explicit target (membership from the target's raid), target not force-included",
     "consumer": "Spell.cpp:1393-1402, 1328-1378", "evidence": "trinity-consumer"},
    {"subject": "TARGET_UNIT_CASTER_AND_SUMMONS(120)", "rule": "caster pushed first (no check), then SUMMONED area search around the caster; list then goes through script hook and RandomResize like any area list (the caster can be dropped by the cap)",
     "consumer": "Spell.cpp:1403-1407, 1442-1452", "evidence": "trinity-consumer"},
    {"subject": "TARGET_UNIT_MASTER / PET / SUMMONER / OWN_CRITTER / VEHICLE", "rule": "MASTER = GetCharmerOrOwner (charmer first, one level); PET = PetGUID if UNIT_MASK_GUARDIAN (pets and SummonProperties Control==PET guardians only); SUMMONER = summoner unit of a TempSummon caster; all added with checkIfValid=true; CASTER without CheckTarget",
     "consumer": "Spell.cpp:1742-1806; Unit.cpp:6230-6243, 6275-6296; TemporarySummon.cpp:499-502", "evidence": "trinity-consumer"},
    {"subject": "m_caster vs original caster", "rule": "m_caster = charmer/owner iff SPELL_ATTR6_ORIGINATE_FROM_CONTROLLER; selection, checks and referers use m_caster; m_originalCaster (explicit GUID else m_caster) is used for channel selectors (1041-1054), hit results (2476) and GO LOS (8235)",
     "consumer": "Spell.cpp:474-521, 1041, 2476, 8235", "evidence": "trinity-consumer"},
    {"subject": "periodic trigger", "rule": "HandlePeriodicTriggerSpellAuraTick casts from the aura CASTER when NeedsToBeTriggeredByCaster (explicit unit target needed, or channeled trigger with non-caster unit targets), else from the HOLDER; explicit target = holder; OriginalCaster not set -> equals the casting unit",
     "consumer": "SpellAuraEffects.cpp:5583-5605; SpellInfo.cpp:1717-1751; SpellDefines.h:527", "evidence": "trinity-consumer"},
    {"subject": "triggered cast without unit target", "rule": "player caster -> its selection if CheckExplicitTarget passes; creature (pet) caster -> its victim when the mask wants ENEMY/UNIT; else self unless the mask needs ENEMY/DEAD/MINIPET/PASSENGER",
     "consumer": "Spell.cpp:640-662", "evidence": "trinity-consumer"},
]

DEFECTS = [
    {"id": "TG-F-D1", "file_line": "src/server/game/Spells/Spell.cpp:741-775",
     "description": "effect-mask grouping ignores ChainTargets (and PositionFacing, radius for non-area categories); grouped effects run with the lead effect's values",
     "effect_on_recipients": "a grouped effect with a different ChainTargets gets the lead's chain length",
     "oracle_behaviour": "reproduced: selection uses the lead effect; census tags chain-count-from-lead / radius-from-lead"},
    {"id": "TG-F-D2", "file_line": "src/server/game/Spells/Spell.cpp:772-775 + SpellInfo.cpp:822-825",
     "description": "the grouping radius comparison calls CalcRadius, which draws rand_norm for *_RANDOM selectors: grouping consumes RNG and compares two random radii (short-circuit makes the draw count value-dependent)",
     "effect_on_recipients": "effects that would share one selection are almost always split; extra RNG draws shift later draws",
     "oracle_behaviour": "reproduced: radius_fn is called lazily in consumer order; census tag rng-in-grouping"},
    {"id": "TG-F-D3", "file_line": "src/server/game/Spells/Spell.cpp:2458-2470",
     "description": "AddUnitTarget clears immune effect bits after the empty-mask test: a fully immune target is still inserted (or merged) with EffectMask 0",
     "effect_on_recipients": "an entry with mask 0 occupies the list (visible to REQUIRE_ALL_TARGETS none_of only through mask; hit processing skips it)",
     "oracle_behaviour": "reproduced in UniqueTargets.add"},
    {"id": "TG-F-D4", "file_line": "src/server/game/Conditions/ConditionMgr.cpp:1636-1680",
     "description": "implicit-target condition container sharing depends on unordered_map iteration order when SourceGroup masks overlap inconsistently; the overlapping row is silently ignored",
     "effect_on_recipients": "which effects share a selection (grouping key) can change between builds",
     "oracle_behaviour": "fails closed on order-dependent sharing; no current-player spell has such rows"},
]


def _selectors_of(e) -> list[int]:
    return [t for t in (e.target_a, e.target_b) if t]


def _cu_effects() -> tuple[dict, dict]:
    from . import context
    ctx = context.get()
    abilities = controlled_unit_abilities()
    by_sel: Counter = Counter()
    rel: Counter = Counter()
    spells_with: dict[str, set[int]] = defaultdict(set)
    existing = 0
    for s in sorted(abilities):
        rows = [e for e in ctx.data.effects(s) if e.is_effect]
        if not rows:
            continue
        existing += 1
        for e in rows:
            for t in _selectors_of(e):
                by_sel[TARGET_NAMES.get(t, str(t))] += 1
                si = info(t)
                if t in GROUP_SELECTORS or t in RELATION_SELECTORS and t != 1:
                    rel[TARGET_NAMES.get(t, str(t))] += 1
                    spells_with[TARGET_NAMES.get(t, str(t))].add(s)
                if si.check in PARTY_RAID_CHECKS and si.reference in ("CASTER", "SRC", "DEST") or si.category == "NEARBY" and si.check in PARTY_RAID_CHECKS:
                    spells_with["pet-caster-referer-membership"].add(s)
    return ({"population": "creature_template_spell of current-player population entries (population.json default) "
                           "+ pet-family SkillLineAbility spells + ClassID-0 SpecializationSpells",
             "spells": len(abilities), "spells_with_effect_rows": existing,
             "effect_selectors": dict(sorted(by_sel.items())), "relation_sensitive_effect_selectors": dict(sorted(rel.items()))},
            {k: sorted(v) for k, v in sorted(spells_with.items())})


def build_group_policy() -> dict[str, Any]:
    from . import context
    ctx = context.get()
    reach = ctx.scope.reach
    abilities = controlled_unit_abilities()
    by_sel: Counter = Counter()
    by_sel_skew: Counter = Counter()
    examples: dict[str, list[int]] = defaultdict(list)
    classes: dict[str, dict[str, Any]] = {}
    per_spec: dict[str, Counter] = {}
    for s in sorted(reach):
        for e in ctx.data.effects(s):
            if not e.is_effect:
                continue
            sels = [t for t in _selectors_of(e) if t in GROUP_SELECTORS or (t in RELATION_SELECTORS and t != 1)]
            if not sels:
                continue
            tags: set[str] = set()
            unknowns: list[str] = []
            verdict = "understood"
            for t in sels:
                name = TARGET_NAMES.get(t, str(t))
                by_sel[name] += 1
                if ctx.is_skew(s):
                    by_sel_skew[name] += 1
                if len(examples[name]) < 8 and s not in examples[name]:
                    examples[name].append(s)
                if t in GROUP_SELECTORS:
                    chk = GROUP_SELECTORS[t][3]
                    tags.add({"PARTY": "party", "RAID": "raid", "RAID_CLASS": "raid-class",
                              "SUMMONED": "caster-and-summons", "PASSENGER": "passenger"}[chk])
                    if t == 118:
                        tags.add("ally-or-raid")
                    if GROUP_SELECTORS[t][0] == "DEFAULT":
                        tags.add("explicit-mask-membership")
                    if chk in PARTY_RAID_CHECKS and GROUP_SELECTORS[t][0] != "DEFAULT":
                        tags.add("group")
                        unknowns.append("TG-F-U1")   # TREAT_AS_RAID_UNIT creature flag not extracted
                    if chk == "RAID_CLASS":
                        unknowns.append("TG-F-U2")
                        verdict = "fixture-dependent"
                else:
                    tags.update({"controlled-unit", RELATION_SELECTORS[t].split("-")[0]})
            if e.attributes & 0x4000:
                tags.add("players-only")
            if s in abilities:
                tags.add("controlled-unit-caster")
            classes[f"{s}:{e.index}"] = {"class": verdict, "tags": sorted(tags), "unknowns": sorted(set(unknowns)),
                                         "build_skew": ctx.is_skew(s)}
    for spec, spells in sorted(ctx.scope.specs_reach.items()):
        c: Counter = Counter()
        for key in classes:
            if int(key.split(":")[0]) in spells:
                for tag in classes[key]["tags"]:
                    c[tag] += 1
        per_spec[str(spec)] = dict(sorted(c.items()))
    cu_summary, cu_spells = _cu_effects()
    return {
        "provenance": {"pins": PINS, "command": "targeting.py groups --out docs/research/targeting-corpora/group-policy.json",
                       "population": "ctx.scope.reach (current player), DIFFICULTY_NONE, IsEffect, raw DB2 (corrections not applied)"},
        "policy": POLICY,
        "selectors": {str(t): {"name": TARGET_NAMES.get(t), "category": v[0], "reference": v[1], "object": v[2], "check": v[3]}
                      for t, v in sorted(GROUP_SELECTORS.items())},
        "relation_selectors": {str(t): {"name": TARGET_NAMES.get(t), "relation": v} for t, v in sorted(RELATION_SELECTORS.items())},
        "census": {"effect_selector_uses": dict(sorted(by_sel.items())),
                   "effect_selector_uses_build_skew": dict(sorted(by_sel_skew.items())),
                   "effects": len(classes),
                   "spells": len({k.split(":")[0] for k in classes}),
                   "examples": {k: v for k, v in sorted(examples.items())},
                   "tags_by_spec": per_spec},
        "controlled_unit_abilities": {**cu_summary, "spells_by_relation_selector": cu_spells},
        "effect_classes": dict(sorted(classes.items(), key=lambda kv: (int(kv[0].split(":")[0]), int(kv[0].split(":")[1])))),
        "unknowns": [
            {"id": "TG-F-U1", "subject": "CREATURE_STATIC_FLAG_4_TREAT_AS_RAID_UNIT_FOR_HELPFUL_SPELLS", "evidence": "trinity-consumer",
             "known": "player vs creature membership is true iff the creature has the static flag (Unit.cpp:12195-12197)",
             "unknown": "which world creatures (incl. current-player summons) carry the flag in the pinned TDB",
             "why_unresolved": "creature static flags (creature_template_difficulty.StaticFlags4) not extracted into the overlay",
             "reopen_condition": "extract StaticFlags4 for the population entries with tools/tdb_world_extract.py", "build_skew": "n/a"},
            {"id": "TG-F-U2", "subject": "TARGET_UNIT_TARGET_AREA_RAID_CLASS (61)", "evidence": "trinity-consumer",
             "known": "class compared is the referer's (explicit target), then RAID; controlled units compare their own unit_class",
             "unknown": "whether Retail compares the caster's class and whether pets can ever match",
             "why_unresolved": "no Retail observation; Trinity is the only consumer",
             "reopen_condition": "Retail experiment TG-F-X2", "build_skew": False},
            {"id": "TG-F-U3", "subject": "pet/guardian party membership in Retail", "evidence": "structural-inference",
             "known": "Trinity: owned units are in party/raid with their owner's group via one-level owner resolution",
             "unknown": "Retail behaviour for guardians and for guardians owned by pets",
             "why_unresolved": "consumer-only evidence", "reopen_condition": "Retail experiment TG-F-X1", "build_skew": "n/a"},
        ],
        "retail_experiments": [
            {"id": "TG-F-X1", "question": "Do party-area heals (TARGET_UNIT_CASTER_AREA_PARTY) hit the healer's guardians and other members' pets?",
             "model_a": "Trinity: yes (one-level owner resolution), totems excluded", "model_b": "players only",
             "setup": "2-player party, each with a pet/guardian, cast a CASTER_AREA_PARTY heal in range of all",
             "observable": "SPELL_HEAL / SPELL_AURA_APPLIED destGUIDs in the combat log", "fidelity": "exact"},
            {"id": "TG-F-X2", "question": "Whose class does TARGET_UNIT_TARGET_AREA_RAID_CLASS compare?",
             "model_a": "Trinity: the explicit target's", "model_b": "the caster's",
             "setup": "raid with two classes; cast a 61-selector spell on a member of the other class",
             "observable": "recipient classes in the combat log", "fidelity": "exact"},
            {"id": "TG-F-X3", "question": "TARGET_UNIT_TARGET_ALLY_OR_RAID on a raid member in another subgroup / out of range",
             "model_a": "Trinity: raid-wide area around the target, target not force-included",
             "model_b": "target always included", "setup": "raid of 10, cast on a distant member",
             "observable": "destGUID list", "fidelity": "exact"},
        ],
        "trinity_defects": [],
    }


# ---------------------------------------------------------------------------
# effect recipients corpus
# ---------------------------------------------------------------------------

def _dst_writer(t: int) -> bool:
    si = info(t)
    return si.object in ("DEST", "UNIT_AND_DEST") or si.category == "TRAJ"


def _dst_reader(t: int) -> bool:
    si = info(t)
    return si.reference == "DEST" or si.category in ("TRAJ", "LINE")


def _unit_selecting(e) -> bool:
    return any(info(t).object in ("UNIT", "UNIT_AND_DEST") for t in _selectors_of(e))


def analyse(v: CView) -> dict[str, Any]:
    """Per-spell grouping analysis (static)."""
    effects = [e for e in v.effects if e.effect]
    plan, rel, notes = static_plan(v)
    steps = [s for s in plan if s.mask]
    tags: dict[int, set[str]] = {e.index: set() for e in effects}
    unknowns: dict[int, set[str]] = {e.index: set() for e in effects}
    verdict: dict[int, str] = {e.index: "understood" for e in effects}
    order = ("understood", "understood-with-defect", "fixture-dependent", "blocked", "unresolved")

    def worse(k: int, cls: str) -> None:
        if order.index(cls) > order.index(verdict[k]):
            verdict[k] = cls

    if len(effects) == 1:
        tags[effects[0].index].add("single-effect")
    for key, r in rel.items():
        for k in key:
            if r == "caster-dependent":
                tags[k].add("radius-caster-dependent")
                worse(k, "fixture-dependent")
            elif r == "rng":
                tags[k].add("rng-in-grouping")
                worse(k, "understood-with-defect")
    if notes and len(effects) > 1:
        for e in effects:
            tags[e.index].add("script-adapter")
            unknowns[e.index].add("TG-F-U7")
            worse(e.index, "unresolved")
    groups = []
    for s in steps:
        members = [k for k in range(len(v.effects)) if s.mask & (1 << k)]
        lead = v.effects[s.effect]
        g = {"lead": s.effect, "mask": s.mask, "effects": members,
             "selectors": [lead.target_a, lead.target_b]}
        cats = {category(t) for t in _selectors_of(lead)}
        random_cap = v.max_affected_targets > 0 and (("AREA" in cats and FURTHEST not in _selectors_of(lead)) or "CONE" in cats)
        chains = (("NEARBY" in cats) or any(info(t).category == "DEFAULT" and info(t).reference == "TARGET" for t in _selectors_of(lead)))
        g["random_cap"] = random_cap
        g["shared_draw_set"] = random_cap and len(members) > 1
        if len(members) > 1 and not _selectors_of(lead):
            for k in members:
                tags[k].add("grouped-no-selector")
        elif len(members) > 1:
            for k in members:
                tags[k].add("grouped")
                if random_cap:
                    tags[k].add("shared-draw")
                    tags[k].add("random-cap")
            chain_counts = {v.effects[k].chain_targets for k in members}
            if chains and len(chain_counts) > 1 and max(chain_counts) > 1:
                g["chain_count_from_lead"] = lead.chain_targets
                for k in members:
                    if v.effects[k].chain_targets != lead.chain_targets:
                        tags[k].add("chain-count-from-lead")
                        worse(k, "understood-with-defect")
            if any(info(t).object == "DEST" and t in DEST_BY_EFFECT_INDEX for t in _selectors_of(lead)):
                for k in members:
                    if k != lead.index:
                        tags[k].add("target-position-from-lead")
                        worse(k, "understood-with-defect")
            if any(info(t).object == "DEST" and t not in DEST_NO_RADIUS for t in _selectors_of(lead)):
                for k in members:
                    ek = v.effects[k]
                    if (ek.radius_a, ek.radius_b) != (lead.radius_a, lead.radius_b):
                        tags[k].add("radius-from-lead")
                        worse(k, "understood-with-defect")
                    if ek.pos_facing != lead.pos_facing:
                        tags[k].add("facing-from-lead")
        elif random_cap:
            tags[s.effect].add("random-cap")
        groups.append(g)
    for s in plan:
        for j, reason in s.split.items():
            if reason in ("radius", "players-only", "conditions", "script-hooks") and j in tags:
                tags[j].add(f"split-{reason}")
                tags[s.effect].add(f"split-{reason}")
    unit_groups = [g for g in groups if _unit_selecting(v.effects[g["lead"]])]
    if len(unit_groups) > 1:
        for e in effects:
            if _unit_selecting(e):
                tags[e.index].add("multi-group-unit")
    # destination flow (turn order = effect order)
    wrote_before = False
    turns = [s for s in plan]
    for idx, s in enumerate(turns):
        e = v.effects[s.effect]
        if s.mask and any(_dst_reader(t) for t in _selectors_of(e)) and wrote_before:
            tags[s.effect].add("dest-read-after-earlier-write")
            tags[s.effect].add("dest")
        if s.mask and any(_dst_writer(t) for t in _selectors_of(e)):
            wrote_before = True
            later = [t for t in turns[idx + 1:] if t.mask and any(_dst_writer(x) for x in _selectors_of(v.effects[t.effect]))]
            if later:
                for prior in turns[:idx + 1]:
                    if any(_dst_writer(x) for x in _selectors_of(v.effects[prior.effect])):
                        tags[prior.effect].add("dest-snapshot-before-later-write")
                        tags[prior.effect].add("dest")
    for e in effects:
        if not e.target_a and not e.target_b:
            tags[e.index].add("effect-type-fallback")
    fixes = [f for f in _corrections().by_spell.get(v.id, []) if any(w.get("member") in TARGETING_MEMBERS for w in f.get("writes", []))]
    if fixes:
        for e in effects:
            tags[e.index].add("corrections-not-applied")
            unknowns[e.index].add("TG-F-U4")
            worse(e.index, "blocked")
    return {"plan": [s.to_json() for s in plan], "groups": groups, "radius_relations": {f"{a}:{b}": r for (a, b), r in sorted(rel.items())},
            "tags": {k: sorted(t) for k, t in tags.items()}, "verdict": verdict,
            "unknowns": {k: sorted(u) for k, u in unknowns.items()}, "notes": notes}


def build_effect_recipients() -> dict[str, Any]:
    from . import context
    ctx = context.get()
    reach = sorted(ctx.scope.reach)
    classes: dict[str, dict[str, Any]] = {}
    counts: Counter = Counter()
    split_reasons: Counter = Counter()
    shared_draw: list[dict[str, Any]] = []
    chain_lead: list[dict[str, Any]] = []
    rng_grouping: list[int] = []
    caster_dep: list[int] = []
    dest_after: list[int] = []
    group_count_hist: Counter = Counter()
    failures: list[dict[str, Any]] = []
    for s in reach:
        rows = [e for e in ctx.data.effects(s) if e.is_effect]
        if not rows:
            continue
        counts["spells"] += 1
        try:
            v = view(s)
            a = analyse(v)
        except FailClosed as exc:
            failures.append({"spell": s, "reason": str(exc)})
            for e in rows:
                classes[f"{s}:{e.index}"] = {"class": "unresolved", "tags": ["fail-closed"], "unknowns": [], "build_skew": ctx.is_skew(s)}
            continue
        for e in rows:
            classes[f"{s}:{e.index}"] = {"class": a["verdict"][e.index], "tags": a["tags"][e.index],
                                         "unknowns": a["unknowns"][e.index], "build_skew": ctx.is_skew(s)}
        if len(rows) < 2:
            continue
        counts["multi_effect_spells"] += 1
        n_groups = len(a["groups"])
        group_count_hist[n_groups] += 1
        if n_groups >= 2:
            counts["spells_with_2plus_selection_groups"] += 1
        if any(len(g["effects"]) > 1 and any(g["selectors"]) for g in a["groups"]):
            counts["spells_with_grouped_selecting_effects"] += 1
        unit_groups = [g for g in a["groups"] if _unit_selecting(v.effects[g["lead"]])]
        if len(unit_groups) >= 2:
            counts["spells_with_2plus_unit_selection_groups"] += 1
        reasons = {r for st in a["plan"] for r in st["split"].values()}
        same_sel_split = {r for r in reasons if r in ("radius", "players-only", "conditions", "script-hooks")}
        for r in same_sel_split:
            split_reasons[r] += 1
        if same_sel_split:
            counts["spells_split_despite_same_selectors"] += 1
        for g in a["groups"]:
            if g["shared_draw_set"]:
                shared_draw.append({"spell": s, "name": ctx.name(s), "effects": g["effects"],
                                    "selectors": [TARGET_NAMES.get(t, t) for t in g["selectors"]],
                                    "max_affected_targets": v.max_affected_targets, "build_skew": ctx.is_skew(s)})
            if "chain_count_from_lead" in g:
                chain_lead.append({"spell": s, "name": ctx.name(s), "effects": g["effects"],
                                   "chain_targets": {str(k): v.effects[k].chain_targets for k in g["effects"]},
                                   "used": g["chain_count_from_lead"], "build_skew": ctx.is_skew(s)})
        if any("rng" == r for r in a["radius_relations"].values()):
            rng_grouping.append(s)
        if any("caster-dependent" == r for r in a["radius_relations"].values()):
            caster_dep.append(s)
        if any("dest-read-after-earlier-write" in t for t in a["tags"].values()):
            dest_after.append(s)
    ordered = dict(sorted(classes.items(), key=lambda kv: (int(kv[0].split(":")[0]), int(kv[0].split(":")[1]))))
    tag_counts = Counter(t for c in ordered.values() for t in c["tags"])
    verdicts = Counter(c["class"] for c in ordered.values())
    return {
        "provenance": {"pins": PINS, "command": "targeting.py effect-recipients --out docs/research/targeting-corpora/effect-recipients.json",
                       "population": "ctx.scope.reach, DIFFICULTY_NONE, IsEffect; raw DB2 + world-DB implicit-target conditions + script target hooks (BindingMap); corrections tagged, not applied",
                       "radius_relation": f"CalcRadius over caster levels {LEVELS.start}-{LEVELS.stop - 1}, moving and not moving; spell mods assumed identical per effect"},
        "mechanics": {
            "grouping": {"consumer": "Spell.cpp:741-785", "evidence": "trinity-consumer",
                         "keys": ["TargetA id", "TargetB id", "ImplicitTargetConditions pointer", "PlayersOnly bit",
                                  "CheckScriptEffectImplicitTargets (OnObjectTargetSelect/OnObjectAreaTargetSelect function identity)",
                                  "CalcRadius(A) and CalcRadius(B) equality, only if the lead's A or B is NEARBY/CONE/AREA/LINE"],
                         "not_keys": ["ChainTargets", "PositionFacing", "radius for DEFAULT/CHANNEL/TRAJ categories", "effect type", "other EffectAttributes"],
                         "selection_reads": "the lead SpellEffectInfo only (Spell.cpp:787-788)"},
            "unique_list": {"consumer": "Spell.cpp:2443-2559", "evidence": "trinity-consumer",
                            "rules": ["one TargetInfo per GUID, EffectMask |= on re-add (hit result, delay and los position of the first add kept)",
                                      "CheckEffectTarget per effect clears bits before; empty -> not added; CheckTarget only when checkIfValid",
                                      "immune bits cleared after the empty test (mask-0 entries possible)",
                                      "insertion order is permanent: no sort; delayed processing uses stable remove_if (Spell.cpp:4129)",
                                      "hit order: effect index outer, list order inner (Spell.cpp:3980-3992)",
                                      "GetUnitTargetIndexForEffect = rank among MISS_NONE entries with the bit (Spell.cpp:2700)"]},
            "effect_type_fallback": {"consumer": "Spell.cpp:797, 2025-2123", "evidence": "trinity-consumer",
                                     "rule": "runs for every effect; adds explicit unit/caster with only that effect's bit when GetMissingTargetMask is non-zero"},
            "destinations": {"consumer": "Spell.cpp:799-800, 2695", "evidence": "trinity-consumer",
                             "rule": "m_destTargets[eff] = current m_targets dst at the end of the effect's turn; later turns may move m_targets dst (ModDst/SetDst), so earlier effects keep the older dst and later dst-referenced selectors read the newest"},
            "channel_mask": {"consumer": "Spell.cpp:824-842", "evidence": "trinity-consumer",
                             "rule": "per effect turn: bit set if any unique target already carries it (targets added by later turns are not seen for earlier bits)"},
            "require_all_targets": {"consumer": "Spell.cpp:802-820", "evidence": "trinity-consumer",
                                    "rule": "checked per non-empty turn against that turn's mask, after the effect-type fallback of the same effect"},
        },
        "census": {**dict(sorted(counts.items())),
                   "selection_groups_per_multi_effect_spell": {str(k): v for k, v in sorted(group_count_hist.items())},
                   "split_despite_same_selectors_by_reason": dict(sorted(split_reasons.items())),
                   "effect_tags": dict(sorted(tag_counts.items())), "effect_verdicts": dict(sorted(verdicts.items())),
                   "spells_rng_in_grouping": rng_grouping, "spells_radius_caster_dependent": caster_dep,
                   "spells_dest_read_after_earlier_write": dest_after, "fail_closed": failures},
        "shared_draw_sets": shared_draw,
        "chain_count_from_lead": chain_lead,
        "effect_classes": ordered,
        "unknowns": [
            {"id": "TG-F-U4", "subject": "spells touched by LoadSpellInfoCorrections (targeting members)", "evidence": "trinity-consumer",
             "known": "corrections rewrite targets/radius/chain/attributes before grouping", "unknown": "post-correction grouping",
             "why_unresolved": "corrections are not applied by this census", "reopen_condition": "apply corrections (track A/G)", "build_skew": "n/a"},
            {"id": "TG-F-U5", "subject": "Retail effect-mask grouping", "evidence": "structural-inference",
             "known": "Trinity comment: 'some spells appear to need this, however this requires more research' (Spell.cpp:747-748)",
             "unknown": "whether Retail shares one target list across effects with equal selectors",
             "why_unresolved": "no Retail consumer", "reopen_condition": "Retail experiment TG-F-X4", "build_skew": "n/a"},
            {"id": "TG-F-U7", "subject": "script target hooks with unresolved effect masks", "evidence": "script-consumer",
             "known": "CheckScriptEffectImplicitTargets compares hook function identity per affected effect",
             "unknown": "the hook's affected mask (unresolved EFFECT_/TARGET_ token)", "why_unresolved": "structural index could not resolve the token",
             "reopen_condition": "track E resolves the hook tokens", "build_skew": "n/a"},
            {"id": "TG-F-U6", "subject": "radius spell mods per effect", "evidence": "structural-inference",
             "known": "SpellModOp::Radius has no effect index; applied identically to every effect's Max",
             "unknown": "float collisions after mods (distinct raw radii becoming equal)", "why_unresolved": "needs concrete modifier values",
             "reopen_condition": "fixture with modifiers.radius", "build_skew": "n/a"},
        ],
        "retail_experiments": [
            {"id": "TG-F-X4", "question": "Do two effects of one spell with identical area selectors and a target cap hit the same random subset?",
             "model_a": "Trinity: one RandomResize draw set shared by the group", "model_b": "independent draws per effect",
             "setup": "pick a shared_draw_sets spell; cast into more candidates than the cap repeatedly",
             "observable": "per-effect destGUID sets per cast (combat log, aura applied vs damage)", "fidelity": "exact"},
        ],
        "trinity_defects": DEFECTS,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _out(p) -> None:
    p.add_argument("--out")


def _groups(args) -> int:
    emit(build_group_policy(), args.out)
    return 0


def _effect_recipients(args) -> int:
    emit(build_effect_recipients(), args.out)
    return 0


def _effect_groups(args) -> int:
    from . import context
    v = view(args.spell)
    a = analyse(v)
    emit({"spell": args.spell, "name": context.get().name(args.spell),
          "effects": [{"index": e.index, "effect": e.effect, "target_a": TARGET_NAMES.get(e.target_a, e.target_a),
                       "target_b": TARGET_NAMES.get(e.target_b, e.target_b), "chain_targets": e.chain_targets,
                       "radius_a": e.radius_a and e.radius_a.__dict__, "radius_b": e.radius_b and e.radius_b.__dict__}
                      for e in v.effects if e.effect],
          "max_affected_targets": v.max_affected_targets, **{k: a[k] for k in ("plan", "groups", "radius_relations", "tags", "verdict", "notes")}},
         args.out)
    return 0


def _spell(p) -> None:
    p.add_argument("spell", type=int)
    p.add_argument("--out")


COMMANDS = {
    "groups": ("party/raid/summoned/relationship policy + census (group-policy.json)", _out, _groups),
    "effect-groups": ("effect-mask selection plan of one spell", _spell, _effect_groups),
    "effect-recipients": ("effect-mask grouping census (effect-recipients.json)", _out, _effect_recipients),
}

__all__ = ["COMMANDS", "CORPORA", "analyse", "build_effect_recipients", "build_group_policy", "view"]
