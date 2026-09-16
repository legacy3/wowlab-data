"""Reusable server-side semantic families -- structural clustering of script handlers.

A *handler* is one method a hook list points at.  Its structural facts (calls
by category, referenced SpellIDs, tokens, RNG / delay / loop markers, prevent
default) come from ``script-index.json``.  This module turns them into
**signals** and assigns each executing hook a *family* with a fixed precedence.

The families are hypotheses from the brief, phrased as observable code shapes:

============================  ==============================================================
family                        shape (all signals are structural, verified per witness in the report)
============================  ==============================================================
target-adapter                target-select hook that filters / resizes / sorts / randomises the list
proc-filter-adapter           DoCheckProc / DoCheckEffectProc returning a predicate over the event
area-target-filter            DoCheckAreaTarget predicate
cast-gate                     OnCheckCast predicate
amount-adapter                calc hook (CalcAmount/CalcDamage/CalcHealing/Crit/Absorb/Periodic) that
                              rewrites an amount; sub-kind = where the scalar comes from
cast-child-with-amount        action hook casting an authored child with BasePoints/AddSpellMod set
                              from a computed scalar (sub-kind = scalar source)
choose-among-children         action hook casting one of >= 2 authored children by a branch
random-child                  action hook whose cast is gated / chosen by RNG
delayed-child                 action hook scheduling a cast (m_Events / scheduler)
pet-owner-forward-cast        cast whose caster/target is the pet, owner or summoner
consume-and-cast              consumes own stacks/charges/aura, then casts a child
cast-child                    plain cast of an authored child (target = hit unit / proc target / caster)
linked-aura-mutation          no cast; refreshes / extends / removes / restacks another aura
cooldown-mutation             SpellHistory cooldown / charge operations
resource-mutation             ModifyPower / EnergizeBySpell / SetPower / health changes
summon                        SummonCreature / CreateAreaTrigger / SummonGameObject
suppress-default              PreventDefaultAction / PreventHitDefaultEffect with no other action
state-only                    no engine action: sets script fields, reads state (marker bookkeeping)
unclassified                  executing hook whose shape matches no rule (reported, never defaulted)
============================  ==============================================================

Nothing here decides gameplay.  A family label is evidence that *this many*
handlers share *this* code shape; the report's "proved" column is set only for
families whose witnesses were read against the consumer.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

from .bindings import Binding, BindingMap, HookBinding, TARGET_LISTS
from .hooks import BY_LIST

CHECK_LISTS = {"DoCheckProc": "proc-filter-adapter", "DoCheckEffectProc": "proc-filter-adapter",
               "DoCheckAreaTarget": "area-target-filter", "OnCheckCast": "cast-gate"}
CALC_LISTS = {"DoEffectCalcAmount", "DoEffectCalcPeriodic", "DoEffectCalcSpellMod", "CalcDamage", "CalcHealing",
              "OnCalcCritChance", "DoEffectCalcCritChance", "DoEffectCalcDamageAndHealing", "OnCalculateResistAbsorb",
              "OnEffectAbsorb", "OnEffectAbsorbHeal", "OnEffectManaShield", "OnEffectSplit", "OnCalcCastTime"}
CAST_CALLEES = {"CastSpell", "CastCustomSpell", "AddAura"}
AMOUNT_FORWARD_TOKENS = re.compile(r"SPELLVALUE_BASE_POINT|SPELLVALUE_AURA_STACK|SPELLVALUE_DURATION|SPELLVALUE_MAX_TARGETS|SPELLVALUE_CRIT_CHANCE")
AMOUNT_FORWARD_CALLEES = {"AddSpellMod", "AddSpellBP"}
HIT_AMOUNT_WRITERS = {"SetHitDamage", "SetHitHeal", "SetEffectValue", "PreventHitDamage", "PreventHitHeal", "SetSpellValue",
                      "SetDamage", "SetHeal", "ModifyDamage", "AbsorbDamage", "ResistDamage"}
COSMETIC = {"SendPlaySpellVisualKit", "SendPlaySpellVisual", "PlayDirectSound", "PlayDistanceSound", "SendPlayOrphanSpellVisual"}
CAST_TARGET_PATTERNS = [
    ("hit-unit", re.compile(r"GetHitUnit|hitUnit|GetHitDest")),
    ("proc-target", re.compile(r"GetProcTarget|GetActionTarget|procTarget|actionTarget|GetVictim")),
    ("aura-target", re.compile(r"GetTarget\(\)|\btarget\b|GetUnitOwner")),
    ("caster", re.compile(r"GetCaster\(\)|\bcaster\b|GetActor\(\)|\bactor\b|GetOriginalCaster|\bowner\b|\bplayer\b|\bshaman\b|\bunit\b")),
    ("explicit-target", re.compile(r"GetExplTargetUnit|GetExplTargetWorldObject")),
    ("nullptr/none", re.compile(r"^nullptr$|^\{\}$")),
    ("destination", re.compile(r"Position|GetPosition|dest|Dest")),
    ("aura-spell-field", re.compile(r"GetSpellInfo\(\)->(Exclude)?(Caster|Target)AuraSpell")),
]
AURA_MUTATION = {"RemoveAura", "RemoveAurasDueToSpell", "RemoveAurasByType", "RemoveAuraFromStack", "RemoveOwnedAura",
                 "RemoveAppliedAuras", "RemoveAurasWithFamily", "RemoveAurasWithAttribute", "RemoveMovementImpairingAuras",
                 "RemoveAurasWithMechanic", "ModStackAmount", "SetStackAmount", "DropCharge", "ModCharges", "SetCharges",
                 "SetDuration", "RefreshDuration", "SetMaxDuration", "Remove", "RefreshTimers", "ResetPeriodic",
                 "SetPeriodicTimer", "ChangeAmount", "SetAmount", "RecalculateAmount", "ModDuration"}
OWN_AURA_CONSUME = {"DropCharge", "ModStackAmount", "SetStackAmount", "Remove", "ModCharges", "SetCharges", "RemoveAuraFromStack"}
COOLDOWN = {"ResetCooldown", "ModifyCooldown", "ModifyChargeRecoveryTime", "RestoreCharge", "ConsumeCharge", "ResetCharges",
            "StartCooldown", "AddCooldown", "ModifySpellCooldown", "ModifyCooldowns", "ResetAllCooldowns", "ResetAllCharges",
            "ModifyCoooldowns"}
RESOURCE = {"ModifyPower", "SetPower", "EnergizeBySpell", "SetHealth", "ModifyHealth", "HealBySpell", "DealDamage", "DealHeal",
            "Kill", "DealSpellDamage", "SetFullPower", "SetFullHealth", "ClearComboPoints", "AddComboPoints", "DealMeleeDamage"}
SUMMON = {"SummonCreature", "SummonGameObject", "CreateAreaTrigger", "SummonPet"}
MOVEMENT = {"NearTeleportTo", "JumpTo", "KnockbackFrom", "MoveJump", "MoveCharge", "TeleportTo", "MovePoint", "MoveFall",
            "MoveFollow", "MoveChase", "MoveTakeoff", "MoveLand", "MoveCirclePath", "MoveBackwards"}
DIRECT_DAMAGE = {"DealDamage", "DealHeal", "Kill", "DealSpellDamage", "DealMeleeDamage", "KillSelf"}
AREATRIGGER_QUERY = {"GetAreaTriggers", "GetAreaTrigger", "GetInsideUnits"}
#: action kinds each family is *declared* to cover; anything a hook does beyond these is a "secondary action"
FAMILY_ACTIONS = {
    "cast-child": {"cast"}, "cast-child-with-amount": {"cast", "amount"}, "choose-among-children": {"cast"},
    "random-child": {"cast", "rng"}, "delayed-child": {"cast", "delay"}, "pet-owner-forward-cast": {"cast", "pet-owner"},
    "consume-and-cast": {"cast", "aura"}, "linked-aura-mutation": {"aura"}, "cooldown-mutation": {"cooldown"},
    "resource-mutation": {"power"}, "summon": {"summon"}, "suppress-default": {"prevent"}, "state-only": set(),
    "target-adapter": {"target-ops", "rng"}, "proc-filter-adapter": {"rng"}, "area-target-filter": set(), "cast-gate": set(),
    "amount-adapter": {"amount", "cast"}, "unclassified": set(),
}
PET_OWNER = {"GetPet", "GetGuardianPet", "GetCharmerOrOwner", "GetOwner", "GetSummoner", "GetCharmerOrOwnerOrSelf",
             "GetCharmerOrOwnerPlayerOrPlayerItself", "GetFirstMinion", "GetAllMinionsByEntry", "GetSummonedCreatureByEntry",
             "GetMinionByEntry", "ToPet", "ToTempSummon"}
TARGET_OPS = {"remove_if", "resize", "sort", "RandomResize", "clear", "push_back", "erase", "SelectRandomContainerElement",
              "RandomShuffle", "SelectRandomWeightedContainerElement"}
AMOUNT_SOURCES = [
    ("damage-copy", {"GetDamage", "GetOriginalDamage", "GetHitDamage", "GetAbsorb"}),
    ("heal-copy", {"GetHeal", "GetEffectiveHeal", "GetOriginalHeal", "GetHitHeal"}),
    ("pct-of-health", {"GetMaxHealth", "CountPctFromMaxHealth", "CountPctFromCurHealth", "GetHealthPct", "GetHealth"}),
    ("pct-of-power", {"GetPower", "GetMaxPower", "GetPowerPct"}),
    ("ap-sp-scaled", {"GetTotalAttackPowerValue", "SpellBaseDamageBonusDone", "SpellBaseHealingBonusDone", "SpellDamageBonusDone",
                      "SpellHealingBonusDone", "GetSpellPowerModifier"}),
    ("stack-scaled", {"GetStackAmount", "GetCharges"}),
    ("aura-amount", {"GetAmount", "GetAmountAsInt", "GetBaseAmount", "GetEstimatedAmount"}),
    ("effect-value", {"GetEffectValue", "GetEffectValueAsInt", "CalcValue", "CalcValueAsInt"}),
    ("stat-scaled", {"GetStat", "GetArmor", "GetVersatilityBonus", "GetRatingBonusValue", "GetTotalAuraModifier", "GetTotalAuraMultiplier"}),
    ("ticks-scaled", {"GetRemainingTicks", "GetTotalTicks", "GetPeriod", "GetTickNumber"}),
]
COMBO_TOKENS = re.compile(r"POWER_COMBO_POINTS|GetComboPoints|GetFinishingMoveCPCost|IsFinishingMove")
PCT_CALLEES = {"CalculatePct", "AddPct", "ApplyPct"}
FAMILIES = ["target-adapter", "proc-filter-adapter", "area-target-filter", "cast-gate", "amount-adapter",
            "cast-child-with-amount", "choose-among-children", "random-child", "delayed-child", "pet-owner-forward-cast",
            "consume-and-cast", "cast-child", "linked-aura-mutation", "cooldown-mutation", "resource-mutation", "summon",
            "suppress-default", "state-only", "unclassified"]


@dataclass
class Signals:
    casts: int = 0
    cast_targets: list[str] = field(default_factory=list)
    hit_amount_write: bool = False
    cosmetic: bool = False
    cast_children: set[int] = field(default_factory=set)
    amount_forward: bool = False
    amount_sources: list[str] = field(default_factory=list)
    pct_math: bool = False
    combo: bool = False
    aura_mutation: set[str] = field(default_factory=set)
    own_consume: bool = False
    cooldown: set[str] = field(default_factory=set)
    resource: set[str] = field(default_factory=set)
    summon: set[str] = field(default_factory=set)
    pet_owner: bool = False
    target_ops: set[str] = field(default_factory=set)
    rng: bool = False
    delayed: bool = False
    loops: int = 0
    prevent: bool = False
    branchy: bool = False           # switch on ids / several distinct children
    movement: set[str] = field(default_factory=set)
    direct_damage: set[str] = field(default_factory=set)
    areatrigger: set[str] = field(default_factory=set)
    cross_script: list[str] = field(default_factory=list)
    cross_class: list[str] = field(default_factory=list)
    refs: set[int] = field(default_factory=set)
    runtime_reads: set[str] = field(default_factory=set)   # target/state callees = explicit runtime inputs
    queries_other_aura: set[int] = field(default_factory=set)  # HasAura/GetAura(X) with X != own spell


def extract_signals(facts: dict[str, Any], own_spell: int | None = None) -> Signals:
    s = Signals()
    if not facts:
        return s
    calls = facts.get("calls", [])
    tokens = set(facts.get("tokens", []))
    s.refs = set(facts.get("refs", {}).values())
    for c in calls:
        callee, cat = c["callee"], c.get("cat")
        ints = c.get("ints", [])
        if callee in CAST_CALLEES:
            s.casts += 1
            s.cast_children.update(v for v in ints if v >= 100)
            recv = c.get("recv", "")
            if any(k in recv for k in PET_OWNER):
                s.pet_owner = True
            args = c.get("args", [])
            first = args[0] if args else ""
            tgt = next((name for name, rx in CAST_TARGET_PATTERNS if rx.search(first)), "other")
            if tgt == "other" and any(k in first for k in PET_OWNER):
                tgt = "pet-or-owner"
            s.cast_targets.append(tgt)
        if callee in HIT_AMOUNT_WRITERS:
            s.hit_amount_write = True
        if callee in COSMETIC:
            s.cosmetic = True
        if callee in AMOUNT_FORWARD_CALLEES:
            s.amount_forward = True
        if callee in MOVEMENT:
            s.movement.add(callee)
        if callee in DIRECT_DAMAGE:
            s.direct_damage.add(callee)
        if callee in AREATRIGGER_QUERY:
            s.areatrigger.add(callee)
        if callee in AURA_MUTATION and not (callee == "Remove" and any(c2["callee"] in AREATRIGGER_QUERY for c2 in calls)
                                             and not c.get("recv", "").lower().startswith(("aura", "buff", "getaura", "aureff", "getbase"))):
            s.aura_mutation.add(callee)
            recv = c.get("recv", "")
            if callee in OWN_AURA_CONSUME and (recv in ("", "GetAura()", "aurEff->GetBase()", "GetTargetApplication()->GetBase()")
                                              or "GetAura()" in recv or recv.startswith(("aurEff", "aura", "GetBase"))):
                s.own_consume = True
        elif callee == "Remove":
            s.areatrigger.add("Remove")
        if callee in COOLDOWN:
            s.cooldown.add(callee)
        if callee in RESOURCE:
            s.resource.add(callee)
        if callee in SUMMON:
            s.summon.add(callee)
        if callee in PET_OWNER:
            s.pet_owner = True
        if callee in TARGET_OPS:
            s.target_ops.add(callee)
        if cat == "rng":
            s.rng = True
        if cat == "delay":
            s.delayed = True
        if cat == "prevent":
            s.prevent = True
        if cat in ("target", "state"):
            s.runtime_reads.add(callee)
        if callee in PCT_CALLEES:
            s.pct_math = True
        if callee in ("HasAura", "GetAura", "GetAuraEffect", "HasAuraEffect", "GetAuraOfRankedSpell", "GetAuraEffectOfRankedSpell", "GetAuraApplication", "GetAuraCount"):
            for v in ints:
                if v >= 100 and v != own_spell:
                    s.queries_other_aura.add(v)
        for name, callees in AMOUNT_SOURCES:
            if callee in callees and name not in s.amount_sources:
                s.amount_sources.append(name)
    if AMOUNT_FORWARD_TOKENS.search(" ".join(tokens)):
        s.amount_forward = True
    if COMBO_TOKENS.search(" ".join(tokens)) or any(c["callee"] in ("GetComboPoints", "GetFinishingMoveCPCost", "IsFinishingMove") for c in calls):
        s.combo = True
    s.cast_children.update(v for v in facts.get("helper_arg_ints", []) if v >= 100)
    if facts.get("delayed_by_event_class"):
        s.delayed = True
    s.cross_script = list(facts.get("cross_script", []))
    s.cross_class = list(facts.get("cross_class", []))
    if any(n.startswith("GetScript<") for n in facts.get("other_calls", {})):
        s.cross_script = s.cross_script or ["(unresolved GetScript<> target)"]
    s.loops = facts.get("loops", 0)
    s.branchy = bool(facts.get("case_ints")) or len(s.cast_children) >= 2
    return s


def action_kinds(s: Signals) -> set[str]:
    """Every action kind a hook performs (overlapping; a hook can do several)."""
    kinds: set[str] = set()
    if s.casts:
        kinds.add("cast")
    if s.amount_forward or s.hit_amount_write:
        kinds.add("amount")
    if s.aura_mutation:
        kinds.add("aura")
    if s.cooldown:
        kinds.add("cooldown")
    if s.resource:
        kinds.add("power")
    if s.summon:
        kinds.add("summon")
    if s.rng:
        kinds.add("rng")
    if s.delayed:
        kinds.add("delay")
    if s.prevent:
        kinds.add("prevent")
    if s.target_ops:
        kinds.add("target-ops")
    if s.pet_owner:
        kinds.add("pet-owner")
    if s.movement:
        kinds.add("movement")
    if s.direct_damage:
        kinds.add("direct-damage")
    if s.areatrigger:
        kinds.add("areatrigger")
    return kinds


def classify_hook(h: HookBinding, own_spell: int | None = None) -> tuple[str, str | None, Signals]:
    """(family, sub-kind, signals) for one executing hook binding.

    Hooks that move units (teleport/knockback) or deal damage outside the spell
    system are outside every family's declared action set and are returned as
    ``unclassified`` (genuinely spell-specific until proved otherwise).
    """
    s = extract_signals(h.facts, own_spell)
    lst = h.list
    if s.movement or s.direct_damage:
        return "unclassified", "+".join(sorted(s.movement | s.direct_damage)), s
    if lst in TARGET_LISTS:
        sub = "random" if s.rng else ("resize" if "resize" in s.target_ops or "RandomResize" in s.target_ops else
                                      ("filter" if "remove_if" in s.target_ops or "erase" in s.target_ops else
                                       ("sort" if "sort" in s.target_ops else ("replace" if "push_back" in s.target_ops or "clear" in s.target_ops else "inspect-only"))))
        return "target-adapter", sub, s
    if lst in CHECK_LISTS:
        sub = "+".join(sorted(k for k in ("aura-query", "spell-family", "damage-info", "spec-class", "actor-type", "proc-spell", "rng")
                              if (k == "aura-query" and (s.queries_other_aura or "HasAura" in s.runtime_reads or "GetAuraEffect" in s.runtime_reads))
                              or (k == "spell-family" and ("IsAffected" in h.facts.get("other_calls", {}) or "GetSpellInfo" in s.runtime_reads or "SPELLFAMILY" in " ".join(h.facts.get("tokens", []))))
                              or (k == "damage-info" and ("GetDamageInfo" in s.runtime_reads or "GetHealInfo" in s.runtime_reads))
                              or (k == "spec-class" and ("GetPrimarySpecialization" in s.runtime_reads or "GetClass" in s.runtime_reads))
                              or (k == "actor-type" and ("IsPlayer" in s.runtime_reads or "ToPlayer" in s.runtime_reads or "GetTypeId" in s.runtime_reads))
                              or (k == "proc-spell" and "GetProcSpell" in s.runtime_reads)
                              or (k == "rng" and s.rng))) or "other"
        return CHECK_LISTS[lst], sub, s
    if lst in CALC_LISTS:
        sub = "+".join(s.amount_sources[:3]) or ("aura-query" if s.queries_other_aura else ("pct-math" if s.pct_math else "constant-or-other"))
        if s.casts:
            sub += "+cast"
        return "amount-adapter", sub, s
    # action hooks
    if s.hit_amount_write and not s.casts:
        sub = "+".join(s.amount_sources[:3]) or ("aura-query" if s.queries_other_aura else ("pct-math" if s.pct_math else "computed"))
        return "amount-adapter", f"hit-amount:{sub}", s
    if s.casts:
        if s.amount_forward or (s.amount_sources and s.pct_math):
            src = "+".join(s.amount_sources[:3]) or ("combo" if s.combo else "computed")
            if s.combo:
                src += "+combo"
            return "cast-child-with-amount", src, s
        if s.pet_owner:
            return "pet-owner-forward-cast", None, s
        if s.own_consume:
            return "consume-and-cast", None, s
        if s.rng:
            return "random-child", None, s
        if s.delayed:
            return "delayed-child", None, s
        if len(s.cast_children) >= 2 and s.branchy:
            return "choose-among-children", f"{len(s.cast_children)}-children", s
        target = "+".join(sorted(set(s.cast_targets))) or "other"
        return "cast-child", target, s
    if s.aura_mutation:
        kinds = sorted({("refresh" if c in ("RefreshDuration", "RefreshTimers", "ResetPeriodic", "SetPeriodicTimer") else
                         "extend" if c in ("SetDuration", "SetMaxDuration", "ModDuration") else
                         "stack" if c in ("ModStackAmount", "SetStackAmount", "DropCharge", "ModCharges", "SetCharges", "RemoveAuraFromStack") else
                         "amount" if c in ("ChangeAmount", "SetAmount", "RecalculateAmount") else "remove") for c in s.aura_mutation})
        return "linked-aura-mutation", "+".join(kinds), s
    if s.cooldown:
        return "cooldown-mutation", "+".join(sorted(s.cooldown))[:60], s
    if s.resource:
        return "resource-mutation", "+".join(sorted(s.resource))[:60], s
    if s.summon:
        return "summon", "+".join(sorted(s.summon)), s
    if s.prevent:
        return "suppress-default", None, s
    if s.cosmetic and not s.runtime_reads - {"GetCaster", "GetHitUnit", "GetTarget"}:
        return "state-only", "cosmetic", s
    if not h.facts.get("calls") and not h.facts.get("other_calls"):
        return "state-only", "empty-or-field-only", s
    if s.runtime_reads and not (s.casts or s.aura_mutation or s.cooldown or s.resource):
        return "state-only", "reads-only", s
    return "unclassified", None, s


@dataclass
class ClassifiedHook:
    spell_id: int
    script: str
    cls: str
    file: str
    handler: str
    line: int
    list: str
    family: str
    sub: str | None
    executes: bool
    prevent_default: bool
    actions: list[str]
    secondary_actions: list[str]
    cross_script: list[str]
    cross_class: list[str]
    children: list[int]
    amount_sources: list[str]
    rng: bool
    delayed: bool
    loops: int
    runtime_reads: list[str]
    queries_other_aura: list[int]
    refs: list[int]

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


class FamilyIndex:
    def __init__(self, bindings: BindingMap) -> None:
        self.bm = bindings
        self.hooks: list[ClassifiedHook] = []
        for spell_id, lst in bindings.by_spell.items():
            for b in lst:
                for h in b.hooks:
                    fam, sub, s = classify_hook(h, spell_id) if h.executes else ("(not-executing)", None, extract_signals(h.facts, spell_id))
                    cls = next((c for c in b.classes if c["kind"] == h.kind), b.classes[0] if b.classes else {"name": "?", "file": "?"})
                    acts = action_kinds(s)
                    self.hooks.append(ClassifiedHook(
                        spell_id=spell_id, script=b.script_name, cls=cls["name"], file=cls["file"], handler=h.handler, line=h.line,
                        list=h.list, family=fam, sub=sub, executes=h.executes, prevent_default=s.prevent,
                        actions=sorted(acts), secondary_actions=sorted(acts - FAMILY_ACTIONS.get(fam, set())),
                        cross_script=s.cross_script, cross_class=s.cross_class,
                        children=sorted(s.cast_children), amount_sources=s.amount_sources, rng=s.rng, delayed=s.delayed,
                        loops=s.loops, runtime_reads=sorted(s.runtime_reads), queries_other_aura=sorted(s.queries_other_aura),
                        refs=sorted(s.refs)[:30]))

    def for_spell(self, spell_id: int) -> list[ClassifiedHook]:
        return [h for h in self.hooks if h.spell_id == spell_id]

    def spell_families(self, spell_id: int) -> dict[str, Any]:
        hs = [h for h in self.for_spell(spell_id) if h.executes]
        fams = Counter(h.family for h in hs)
        return {"families": dict(fams), "hooks": len(hs), "prevent_default": any(h.prevent_default for h in hs),
                "children": sorted({c for h in hs for c in h.children}), "rng": any(h.rng for h in hs),
                "delayed": any(h.delayed for h in hs), "runtime_reads": sorted({r for h in hs for r in h.runtime_reads})}

    def census(self, spells: set[int] | None = None) -> dict[str, Any]:
        hs = [h for h in self.hooks if h.executes and (spells is None or h.spell_id in spells)]
        fam = Counter(h.family for h in hs)
        sub = Counter(f"{h.family}/{h.sub}" for h in hs if h.sub)
        per_spell_fams: dict[int, set[str]] = defaultdict(set)
        for h in hs:
            per_spell_fams[h.spell_id].add(h.family)
        owners = Counter()
        for s_id, fs in per_spell_fams.items():
            for f in fs:
                owners[f] += 1
        # scripts (classes) per family
        scripts: dict[str, set[str]] = defaultdict(set)
        for h in hs:
            scripts[h.family].add(h.cls)
        act = Counter(a for h in hs for a in h.actions)
        secondary = Counter(a for h in hs for a in h.secondary_actions)
        return {
            "executing_hooks": len(hs), "spells": len(per_spell_fams),
            "action_kinds_overlapping": dict(act.most_common()),
            "hooks_with_multiple_action_kinds": sum(1 for h in hs if len(h.actions) > 1),
            "hooks_with_secondary_actions_outside_family": sum(1 for h in hs if h.secondary_actions),
            "secondary_action_counts": dict(secondary.most_common()),
            "hooks_with_cross_script_state": sum(1 for h in hs if h.cross_script),
            "hooks_with_cross_class_helpers": sum(1 for h in hs if h.cross_class),
            "hooks_by_family": {f: fam[f] for f in FAMILIES if fam[f]},
            "owner_spells_by_family": {f: owners[f] for f in FAMILIES if owners[f]},
            "scripts_by_family": {f: len(scripts[f]) for f in FAMILIES if scripts[f]},
            "sub_kinds": dict(sub.most_common(60)),
            "spells_single_family": sum(1 for fs in per_spell_fams.values() if len(fs) == 1),
            "spells_multi_family": sum(1 for fs in per_spell_fams.values() if len(fs) > 1),
            "family_combinations": dict(Counter("+".join(sorted(fs)) for fs in per_spell_fams.values()).most_common(25)),
            "rng_hooks": sum(1 for h in hs if h.rng), "delayed_hooks": sum(1 for h in hs if h.delayed),
            "prevent_default_hooks": sum(1 for h in hs if h.prevent_default),
            "unclassified_examples": [(h.spell_id, h.script, h.handler, f"{h.file}:{h.line}") for h in hs if h.family == "unclassified"][:40],
        }
