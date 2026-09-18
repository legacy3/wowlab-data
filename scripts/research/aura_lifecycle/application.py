"""Track A -- initial application: source -> quote -> commit -> registration -> real apply.

Answers, for one authored spell and one unit recipient hit by the normal spell path
(``Spell::DoProcessTargetContainer``), *when* the aura object comes into existence,
*what* it is initialised with, and *where* that sits relative to the same spell's
sibling damage / healing effects and hit procs.

Trinity call path (pinned revision, each step proven by caller/callee, not source order):

    Spell::DoProcessTargetContainer                         Spell.cpp:3980
      1. for all targets: TargetInfo::PreprocessTarget        Spell.cpp:2749
           Spell::PreprocessSpellHit                          Spell.cpp:3109
             AuraBasePoints[i] = CalcBaseValue(...)           Spell.cpp:3175-3178   (amount input snapshot)
             DR level / IncrDiminishing                       Spell.cpp:3181-3190
             AuraDuration = Aura::CalcMaxDuration(...)        Spell.cpp:3212-3215   (duration quote -> track B)
           CallScriptOnHitHandlers                            Spell.cpp:2789
      2. for effect index ascending, for each target: TargetInfo::DoTargetSpellHit   Spell.cpp:2796
           Spell::DoSpellEffectHit                            Spell.cpp:3226
             (first unit-owned aura effect for this target)   Spell.cpp:3238-3301
               Aura::TryRefreshStackOrCreate(createInfo, false)  SpellAuras.cpp:350   (identity -> identity.py)
                 new: UnitAura ctor -> Aura ctor (duration/charges/stacks)  SpellAuras.cpp:476-501
                      LoadScripts; _InitEffects (AuraEffect ctor: CalculatePeriodic, CalculateAmount)  SpellAuras.cpp:2510-2511
                      Unit::_AddAura (owned map, no-stack removal, single-target)  Unit.cpp:3449
                      AddStaticApplication                     SpellAuras.cpp:2650
               ModSpellDuration / DurationMul / haste / ATTR13 pandemic; SetMaxDuration+SetDuration  Spell.cpp:3260-3298
             (later aura effects for this target)  AddStaticApplication  Spell.cpp:3304-3305
             HandleEffects(HIT_TARGET)                          Spell.cpp:3310
               EffectApplyAura -> Unit::_CreateAuraApplication (slot, lists, _ApplyForTarget)  SpellEffects.cpp:1110 / Unit.cpp:3488
               EffectSchoolDMG computes m_damage incl. SpellDamageBonusDone/Taken  SpellEffects.cpp:562-567
      3. for all targets: TargetInfo::DoDamageAndTriggers      Spell.cpp:2825
           HealBySpell                                         Spell.cpp:2896
           DealSpellDamage                                     Spell.cpp:2938
           ProcSkillsAndAuras(PROC_SPELL_PHASE_HIT)            Spell.cpp:2980
           Unit::_ApplyAura(aurApp, effMask)                   Spell.cpp:3040 -> Unit.cpp:3544
             _RemoveNoStackAurasDueToAura(owned=false); aura state; HandleAuraSpecificMods;
             per effect (index order): AuraApplication::_HandleEffect -> AuraEffect::HandleEffect
               _RegisterAuraEffect, ApplySpellMod, OnEffectApply scripts, handler, AfterEffectApply  SpellAuraEffects.cpp:1134-1178
           DoTriggersOnSpellHit                                 Spell.cpp:3051
           CallScriptAfterHitHandlers                           Spell.cpp:3058

Area-aura effects (APPLY_AREA_AURA_*) have ``EffectUnused`` handlers (SpellEffects.cpp:127/157/211/220/221/235/294/363):
the owner's aura object is created at hit but **no application** exists until the owner's next
``Aura::UpdateOwner`` runs ``UpdateTargetMap`` (``m_updateTargetMapInterval`` starts at 0;
SpellAuras.cpp:839-842), i.e. after that update's ``Aura::Update`` has already decremented duration.
Recipient-map semantics belong to track F.
"""

from __future__ import annotations

from typing import Any

from procs.enums import AREA_AURA_EFFECTS, aura, aura_name, effect, effect_name

from . import FailClosed
from .identity import APPLY_AURA, APPLY_AURA_ON_PET, PERSISTENT_AREA_AURA, Props

#: Effects whose HIT_TARGET handler only accumulates m_damage / m_healing (dealt in DoDamageAndTriggers).
DAMAGE_EFFECTS = frozenset(effect(n) for n in ("SCHOOL_DAMAGE", "WEAPON_DAMAGE_NOSCHOOL", "WEAPON_PERCENT_DAMAGE",
                                                "WEAPON_DAMAGE", "NORMALIZED_WEAPON_DMG", "HEALTH_LEECH",
                                                "DAMAGE_FROM_MAX_HEALTH_PCT"))
HEAL_EFFECTS = frozenset(effect(n) for n in ("HEAL", "HEAL_PCT", "HEAL_MECHANICAL", "HEAL_MAX_HEALTH"))
#: Aura types that change the damage the aura holder takes (for the same-cast ordering witness census).
DAMAGE_TAKEN_AURAS = frozenset(aura(n) for n in ("MOD_DAMAGE_PERCENT_TAKEN", "MOD_DAMAGE_TAKEN",
                                                  "MOD_SCHOOL_MASK_DAMAGE_FROM_CASTER", "MOD_SPELL_DAMAGE_FROM_CASTER"))

STEPS: list[dict[str, str]] = [
    {"step": "preprocess", "what": "aura base points snapshot (CalcBaseValue per effect), DR group/level, duration quote",
     "coords": "Spell.cpp:3174-3221 (PreprocessSpellHit)", "handoff": "B (duration quote), D (amount timing)"},
    {"step": "create-or-refresh", "what": "TryRefreshStackOrCreate at the FIRST unit-owned aura effect hitting this target",
     "coords": "Spell.cpp:3238-3256; SpellAuras.cpp:350-388", "handoff": "identity.py"},
    {"step": "construct", "what": "Aura ctor: m_maxDuration=m_duration=CalcMaxDuration(caster); m_procCharges=CalcMaxCharges; "
     "m_stackAmount=createInfo.StackAmount (unclamped)", "coords": "SpellAuras.cpp:476-501 (m_stackAmount 481, duration 496-497, charges 498-499)", "handoff": "B, C"},
    {"step": "scripts+effects", "what": "LoadScripts then _InitEffects: AuraEffect for EVERY unit-owned aura effect of the spell "
     "(CalculatePeriodic(create=true), CalculateAmount -> DoEffectCalc* hooks)", "coords": "SpellAuras.cpp:2510-2511, 511-523; "
     "SpellAuraEffects.cpp:736-747", "handoff": "D"},
    {"step": "register-owned", "what": "_AddAura: m_ownedAuras insert, no-stack removal (CanStackWith), single-target registration",
     "coords": "Unit.cpp:3449-3479", "handoff": "identity.py, E (single-target)"},
    {"step": "duration-commit", "what": "ModSpellDuration, DurationMul, channel/ATTR8 haste, ATTR13 refresh extension; SetMaxDuration+SetDuration "
     "only if different from CalcMaxDuration", "coords": "Spell.cpp:3260-3298", "handoff": "B"},
    {"step": "static-application", "what": "AddStaticApplication(target, APPLY_AURA bits of this effect)", "coords": "Spell.cpp:3300-3305; SpellAuras.cpp:2650-2662",
     "handoff": "F"},
    {"step": "create-application", "what": "EffectApplyAura (APPLY_AURA / APPLY_AURA_ON_PET only): _CreateAuraApplication or AddEffectToApplyEffectMask; "
     "visible slot allocation; dead target w/o ATTR3_ALLOW_AURA_WHILE_DEAD -> no application",
     "coords": "SpellEffects.cpp:1110-1124; Unit.cpp:3488-3528; SpellAuras.cpp:73-97", "handoff": ""},
    {"step": "sibling-effects", "what": "damage/heal effects compute m_damage/m_healing (done+taken bonuses) at their own index",
     "coords": "SpellEffects.cpp:562-567", "handoff": ""},
    {"step": "deal", "what": "HealBySpell / DealSpellDamage, then PROC_SPELL_PHASE_HIT procs", "coords": "Spell.cpp:2896, 2938, 2980", "handoff": ""},
    {"step": "real-apply", "what": "_ApplyAura: no-stack removal on recipient, aura state, HandleAuraSpecificMods, HandleEffect(REAL) per effect in index order "
     "(OnEffectApply -> handler -> AfterEffectApply)", "coords": "Spell.cpp:3029-3048; Unit.cpp:3544-3598; SpellAuraEffects.cpp:1134-1178", "handoff": ""},
    {"step": "after-hit", "what": "DoTriggersOnSpellHit (ADD_TARGET_TRIGGER), AfterHit scripts", "coords": "Spell.cpp:3051, 3058", "handoff": "H"},
]


def effect_role(e) -> str:
    if e.effect in AREA_AURA_EFFECTS:
        return "area-aura (no hit handler; recipients via UpdateTargetMap)"
    if e.effect in (APPLY_AURA, APPLY_AURA_ON_PET):
        return "unit-aura"
    if e.effect == PERSISTENT_AREA_AURA:
        return "dynobj-aura (new DynamicObject per cast, HANDLE_HIT)"
    if e.effect in DAMAGE_EFFECTS:
        return "damage (computed at index, dealt in DoDamageAndTriggers)"
    if e.effect in HEAL_EFFECTS:
        return "heal (computed at index, dealt in DoDamageAndTriggers)"
    if e.effect == 0:
        return "empty"
    return "other (HandleEffects at index)"


def recipient_groups(p: Props) -> list[dict[str, Any]]:
    """Unit-owned aura effects grouped by implicit targets (structural: one group ~ one recipient set).

    For each group the aura object is created at the group's first effect index (if that
    recipient was not already reached by an earlier aura effect), and it carries AuraEffects
    for **all** unit-owned aura effects of the spell (``allAuraEffectMask``, Spell.cpp:3241-3243),
    even those aimed at other recipients.
    """
    groups: dict[tuple[int, int], list[int]] = {}
    for e in p.effects:
        if e.is_unit_owned_aura:
            groups.setdefault((e.target_a, e.target_b), []).append(e.index)
    return [{"targets": list(k), "effects": v, "create_at_effect": v[0]} for k, v in groups.items()]


def initial_state(ctx, p: Props, proc_store=None) -> dict[str, Any]:
    """Initial duration/charges/stacks/amount inputs (data only; runtime mods named, not applied)."""
    misc = ctx.data.row("SpellMisc", p.spell) or {}
    dur = ctx.data.duration(misc.get("DurationIndex", 0)) if misc.get("DurationIndex") else None
    charges_src = "SpellAuraOptions.ProcCharges"
    charges = None
    info = ctx.catalog.get(p.spell)
    if info is not None:
        charges = int(info.proc_charges)
    if proc_store is not None:
        entry = proc_store.lookup(p.spell)
        if entry is not None:
            charges, charges_src = int(entry.charges), f"SpellProcEntry.Charges ({entry.origin})"
    return {
        "stacks": {"value": 1, "source": "SpellValue::AuraStackAmount = 1 (Spell.cpp:450)",
                   "runtime": ["SpellModOp::Doses (Spell.cpp:506)", "SPELLVALUE_AURA_STACK (Spell.cpp:8741, scripts)"],
                   "capacity_not_used": p.stack_amount,
                   "note": "creation does not clamp to CumulativeAura (SpellAuras.cpp:481 m_stackAmount(createInfo.StackAmount)); "
                           "only ModStackAmount clamps (SpellAuras.cpp:1099-1106)", "handoff": "C"},
        "charges": {"value": charges, "source": charges_src, "coords": "SpellAuras.cpp:1004-1015 CalcMaxCharges",
                    "runtime": ["SpellModOp::ProcCharges"], "handoff": "C"},
        "duration": {"index": misc.get("DurationIndex"), "row": dur,
                     "coords": "SpellAuras.cpp:496-497 (ctor) then Spell.cpp:3294-3298 (hit commit)", "handoff": "B"},
        "amount": {"timing": "AuraBasePoints computed in PreprocessSpellHit before any effect (Spell.cpp:3175-3178); "
                   "AuraEffect::CalculateAmount at construction (SpellAuraEffects.cpp:744)", "handoff": "D"},
    }


def plan(ctx, p: Props, proc_store=None) -> dict[str, Any]:
    """Construction path of spell *p* for one recipient on the normal spell-hit path."""
    effs = []
    for e in p.effects:
        effs.append({"index": e.index, "label": e.label(), "role": effect_role(e),
                     "targets": [e.target_a, e.target_b], "aura_effect_object": bool(p.unit_aura_mask() & (1 << e.index)),
                     "aura_name_zero": e.is_unit_owned_aura and not e.aura})
    unit = p.unit_aura_mask()
    out: dict[str, Any] = {
        "spell": p.spell, "name": p.name, "build_skew": ctx.is_skew(p.spell),
        "unit_aura_mask": unit, "dynobj_aura_mask": p.dynobj_aura_mask(), "effects": effs,
        "recipient_groups": recipient_groups(p),
        "steps": STEPS,
        "initial": initial_state(ctx, p, proc_store) if unit else None,
        "ordering": same_cast_ordering(p),
        "partial_failures": partial_failures(p),
        "scripts": sorted(ctx.bundle.script_names.get(p.spell, [])),
    }
    if any(e.is_area_aura for e in p.effects):
        out["area_note"] = ("area-aura effects: owner aura created at hit; first application (owner and recipients) at the owner's next "
                            "Aura::UpdateOwner -> UpdateTargetMap, after that update's duration decrement (SpellAuras.cpp:837-842)")
    return out


def same_cast_ordering(p: Props) -> dict[str, Any]:
    """Where each effect acts relative to the aura's real application, for one target (Spell.cpp:3980-3992)."""
    unit_idx = [e.index for e in p.effects if e.effect in (APPLY_AURA, APPLY_AURA_ON_PET)]
    dmg = [e.index for e in p.effects if e.effect in DAMAGE_EFFECTS]
    heal = [e.index for e in p.effects if e.effect in HEAL_EFFECTS]
    taken_mod = [e.index for e in p.effects if e.effect in (APPLY_AURA, APPLY_AURA_ON_PET) and e.aura in DAMAGE_TAKEN_AURAS]
    return {
        "aura_created_at_effect": unit_idx[0] if unit_idx else None,
        "damage_effects": dmg, "heal_effects": heal,
        "damage_before_aura_index": [i for i in dmg if unit_idx and i < unit_idx[0]],
        "damage_after_aura_index": [i for i in dmg if unit_idx and i > unit_idx[0]],
        "same_cast_damage_sees_own_aura": False if (dmg or heal) and unit_idx else None,
        "damage_taken_modifier_effects": taken_mod,
        "why": "damage/heal amounts (done+taken) are computed at their effect index (SpellEffects.cpp:562-567) and dealt in "
               "DoDamageAndTriggers (Spell.cpp:2896/2938) before _ApplyAura registers the aura's effects (Spell.cpp:3040); "
               "the aura object exists from its first aura effect but its effects are not in m_modAuras yet",
    }


def partial_failures(p: Props) -> list[dict[str, str]]:
    """Ways the constructed object differs from 'all aura effects applied on the recipient'."""
    out = [
        {"case": "immune effect", "result": "effect index never hits (target EffectMask); AuraEffect exists on the object but is not in the application",
         "coords": "Spell.cpp:3985-3988, 3031-3036"},
        {"case": "effect aimed at another recipient", "result": "object carries its AuraEffect (allAuraEffectMask) but this recipient's application lacks the bit",
         "coords": "Spell.cpp:3241-3243; SpellEffects.cpp:1119-1123"},
        {"case": "dead recipient without ATTR3_ALLOW_AURA_WHILE_DEAD", "result": "owned aura object exists, _CreateAuraApplication returns nullptr (no application)",
         "coords": "Unit.cpp:3507-3510", "handoff": "E"},
        {"case": "no-stack removal of the new aura during registration (IsHighestExclusiveAura false)", "result": "Create returns nullptr; nothing applied",
         "coords": "Unit.cpp:3454-3457, 3722-3726; SpellAuras.cpp:434-436"},
        {"case": "script removes aura during OnEffectApply / handler", "result": "remaining effects in index order are not applied (loop breaks on remove mode)",
         "coords": "Unit.cpp:3579-3587; SpellAuraEffects.cpp:1161-1171", "handoff": "H"},
        {"case": "visible slots exhausted (>= MAX_AURAS=300)", "result": "application exists and applies effects but is never sent to the client",
         "coords": "SpellAuras.cpp:84-96"},
        {"case": "single-target aura on owner outside world", "result": "Create returns nullptr unless self-cast", "coords": "SpellAuras.cpp:420-424"},
        {"case": "found aura removed by its own script during ModStackAmount", "result": "TryRefreshStackOrCreate returns nullptr", "coords": "SpellAuras.cpp:370-373"},
        {"case": "negative aura diminished to 0 duration and every effect is APPLY_AURA", "result": "whole hit becomes SPELL_MISS_IMMUNE",
         "coords": "Spell.cpp:3217-3220", "handoff": "B"},
        {"case": "effect-mask mismatch with existing aura", "result": "new object + removal of the old one (not a refresh)", "coords": "Unit.cpp:3402-3406"},
    ]
    if any(e.is_unit_owned_aura and not e.aura for e in p.effects):
        out.append({"case": "APPLY_AURA with EffectAura 0", "result": "still in BuildEffectMaskForOwner (IsUnitOwnedAuraEffect has no aura test): "
                    "an AuraEffect of type SPELL_AURA_NONE is created and applied", "coords": "SpellInfo.cpp:494-497; SpellAuras.cpp:320-345"})
    return out


# ---------------------------------------------------------------------------
# Synthetic timeline (single target, single cast) -- discriminates ordering models
# ---------------------------------------------------------------------------

def timeline(effects: list[dict[str, Any]], *, existing: bool = False, dead_target: bool = False,
             death_persistent: bool = False, immune: frozenset[int] = frozenset()) -> list[dict[str, Any]]:
    """Ordered events of one spell hit on one unit target.

    ``effects``: ``[{"index": i, "kind": "aura"|"area-aura"|"damage"|"heal"|"other"}]``.
    Mirrors: Spell.cpp:3980-3992 ``DoProcessTargetContainer`` + Spell.cpp:3226-3312 ``DoSpellEffectHit`` +
    Spell.cpp:2825-3059 ``DoDamageAndTriggers``.  Returns events with the aura state after each.
    Rules out: "effects resolve fully in index order" (aura effect applied before a later damage effect).
    """
    ev: list[dict[str, Any]] = []
    state = {"object": existing, "application": existing, "damage_pending": 0, "heal_pending": 0,
             "applied": {e["index"] for e in effects if existing and e["kind"] == "aura"}}

    def push(what: str, coords: str) -> None:
        ev.append({"n": len(ev), "event": what, "coords": coords,
                   "object": state["object"], "application": state["application"],
                   "applied_effects": sorted(state["applied"]), "damage_dealt": state.get("dealt", False)})

    push("PreprocessSpellHit: aura base points + duration quote", "Spell.cpp:3174-3215")
    hit_aura = False
    to_apply: set[int] = set()
    for e in sorted(effects, key=lambda x: x["index"]):
        i, kind = e["index"], e["kind"]
        if i in immune:
            continue
        if kind in ("aura", "area-aura"):
            if not hit_aura:
                hit_aura = True
                if existing:
                    push(f"effect {i}: TryRefreshStackOrCreate -> refresh existing object", "Spell.cpp:3254")
                else:
                    state["object"] = True
                    push(f"effect {i}: TryRefreshStackOrCreate -> new object (all aura effects constructed)", "Spell.cpp:3254")
            if kind == "aura":
                if not state["application"]:
                    if dead_target and not death_persistent:
                        push(f"effect {i}: EffectApplyAura -> _CreateAuraApplication refused (dead target)", "Unit.cpp:3507-3510")
                        continue
                    state["application"] = True
                    push(f"effect {i}: EffectApplyAura -> application created", "SpellEffects.cpp:1121")
                else:
                    push(f"effect {i}: EffectApplyAura -> effect added to application mask", "SpellEffects.cpp:1123")
                to_apply.add(i)
            else:
                push(f"effect {i}: area aura effect has no hit handler", "SpellEffects.cpp:127")
        elif kind == "damage":
            state["damage_pending"] += 1
            push(f"effect {i}: damage computed (done+taken bonuses read now)", "SpellEffects.cpp:562-567")
        elif kind == "heal":
            state["heal_pending"] += 1
            push(f"effect {i}: heal computed", "SpellEffects.cpp:HandleEffects")
        else:
            push(f"effect {i}: other effect handler", "Spell.cpp:5716")
    if state["heal_pending"]:
        push("HealBySpell", "Spell.cpp:2896")
    if state["damage_pending"]:
        state["dealt"] = True
        push("DealSpellDamage", "Spell.cpp:2938")
    push("ProcSkillsAndAuras (PROC_SPELL_PHASE_HIT)", "Spell.cpp:2980")
    if state["application"]:
        new = sorted(to_apply - state["applied"])
        for i in new:
            state["applied"].add(i)
            push(f"_ApplyAura: HandleEffect(REAL) effect {i}", "Spell.cpp:3040; Unit.cpp:3579-3587")
    push("DoTriggersOnSpellHit + AfterHit scripts", "Spell.cpp:3051-3058")
    return ev


def require_unit_aura(p: Props) -> None:
    if not p.unit_aura_mask():
        raise FailClosed(f"spell {p.spell} has no unit-owned aura effect")


__all__ = ["STEPS", "effect_role", "initial_state", "partial_failures", "plan", "recipient_groups",
           "require_unit_aura", "same_cast_ordering", "timeline", "aura_name", "effect_name"]
