"""Pure pre-RNG eligibility evaluator.

Mirrors, in order:
* ``Unit::ProcSkillsAndAuras`` -- the proc-chain hard limit;
* ``Unit::TriggerAurasProcOnEvent`` -- which side's mask a holder receives and
  whether the action-target side exists at all;
* ``Aura::GetProcEffectMask`` up to (not including) ``roll_chance``;
* ``SpellMgr::CanSpellTriggerProcOnEvent`` (called from the above);
* ``AuraEffect::CheckEffectProc`` (per effect, called from the above).

The evaluator takes an immutable :class:`~procs.definition.ProcDefinition`,
one :class:`~procs.events.ProcEvent`, the holder side, a :class:`HolderState`
snapshot (the mutable facts ``GetProcEffectMask`` *reads*: charges, cooldown)
and :class:`ActorFacts` (actor/holder facts combat must supply).  It returns
every check it performed, in consumer order, and stops at the first failure --
exactly as the consumer returns ``0``.  It **never** draws randomness and never
mutates state; the RNG boundary is the end of this function.

Any fact the consumer needs that the caller left as ``None`` yields verdict
``unknown`` at that step (fail closed).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .definition import ProcDefinition
from .enums import (
    ALWAYS_TRIGGER_EVENT_FLAGS,
    ATTR0_PASSIVE,
    ATTR3_CAN_PROC_FROM_PROCS,
    ATTR3_NO_PROC_EQUIP_REQUIREMENT,
    ATTR3_NOT_A_PROC,
    ATTR3_ONLY_PROC_ON_CASTER,
    ATTR3_ONLY_PROC_OUTDOORS,
    ATTR3_SUPPRESS_CASTER_PROCS,
    ATTR3_SUPPRESS_TARGET_PROCS,
    ATTR4_ALLOW_PROC_WHILE_SITTING,
    ATTR4_SUPPRESS_WEAPON_PROCS,
    ATTR6_AURA_IS_WEAPON_PROC,
    ATTR7_CAN_PROC_FROM_SUPPRESSED_TARGET_PROCS,
    ATTR12_CAN_PROC_FROM_SUPPRESSED_CASTER_PROCS,
    ATTR12_ENABLE_PROCS_FROM_SUPPRESSED_CASTER_PROCS,
    ATTR12_ONLY_PROC_FROM_CLASS_ABILITIES,
    ATTR13_ALLOW_CLASS_ABILITY_PROCS,
    AUTO_ATTACK_PROC_FLAG_MASK,
    DONE_HIT_PROC_FLAG_MASK,
    PROC_ATTR_CANT_PROC_FROM_ITEM_CAST,
    PROC_ATTR_REQ_EXP_OR_HONOR,
    PROC_ATTR_REQ_POWER_COST,
    PROC_ATTR_REQ_SPELLMOD,
    PROC_ATTR_TRIGGERED_CAN_PROC,
    PROC_ATTR_USE_STACKS_FOR_CHARGES,
    PROC_HIT_ABSORB,
    PROC_HIT_CRITICAL,
    PROC_HIT_NORMAL,
    PROC_SPELL_PHASE_CAST,
    REQ_SPELL_PHASE_PROC_FLAG_MASK,
    SPELL_AURA_MOD_STEALTH,
    SPELL_EFFECT_ADD_EXTRA_ATTACKS,
    SPELL_PROC_FLAG_MASK,
    TAKEN_HIT_PROC_FLAG_MASK,
    aura,
)
from .events import OFF_ATTACK, ProcEvent
from .spells import (
    SPELL_ATTR0_CU_CAN_CRIT,
    SPELL_ATTR0_CU_DONT_BREAK_STEALTH,
    SpellCatalog,
)

PROC_CHAIN_HARD_LIMIT = 10  # Unit::ProcSkillsAndAuras

ITEM_CLASS_WEAPON = 2  # ItemTemplate.h
ITEM_CLASS_ARMOR = 4

_CC_AURAS = {aura(n) for n in ("MOD_CONFUSE", "MOD_FEAR", "MOD_STUN", "MOD_ROOT", "TRANSFORM")}
_MECHANIC_AURAS = {aura("MECHANIC_IMMUNITY"), aura("MOD_MECHANIC_RESISTANCE")}
_CAST_TIME_AURAS = {aura("MOD_CASTING_SPEED_NOT_STACK")}
_FROM_CASTER_AURAS = {aura("MOD_SCHOOL_MASK_DAMAGE_FROM_CASTER"), aura("MOD_SPELL_DAMAGE_FROM_CASTER")}
_POWER_COST_SCHOOL_AURAS = {aura("MOD_POWER_COST_SCHOOL"), aura("MOD_POWER_COST_SCHOOL_PCT")}
_REFLECT_SCHOOL_AURAS = {aura("REFLECT_SPELLS_SCHOOL")}
_PROC_TRIGGER_SPELL_AURAS = {aura("PROC_TRIGGER_SPELL"), aura("PROC_TRIGGER_SPELL_WITH_VALUE")}
_SPELL_CRIT_AURAS = {aura("MOD_SPELL_CRIT_CHANCE")}


@dataclass(frozen=True)
class HolderState:
    """Mutable aura facts read (not written) before RNG."""

    charges: int = 0              # Aura::m_procCharges
    using_charges: bool = False   # Aura::m_isUsingCharges
    proc_cooldown_until_ms: int = -1  # Aura::m_procCooldown (TimePoint::min() at creation)
    now_ms: int = 0
    effect_mask: int | None = None    # AuraApplication::GetEffectMask(); None = every aura effect


@dataclass(frozen=True)
class ActorFacts:
    """Actor / holder facts the consumer reads.  ``None`` = unknown."""

    holder_is_player: bool | None = True
    actor_is_player: bool | None = True
    actor_level: int | None = None
    action_target_gives_xp_or_honor: bool | None = None
    holder_is_aura_caster: bool | None = True
    event_actor_is_aura_caster: bool | None = None
    holder_outdoors: bool | None = None
    holder_standing: bool | None = True
    holder_in_feral_form: bool | None = False
    equipped_item_fits: bool | None = None  # usable, unbroken, IsFitToSpellRequirements
    actor_last_extra_attack_spell: int = 0
    conditions_met: bool | None = None
    script_check_proc: bool | None = None
    script_check_effect: dict[int, bool] | None = None
    aura_own_spell_at_full_duration: bool | None = None


@dataclass
class Check:
    step: str
    consumer: str
    outcome: str  # pass | fail | skip | unknown
    detail: str = ""

    def to_dict(self) -> dict[str, str]:
        return dict(self.__dict__)


@dataclass
class Eligibility:
    verdict: str  # eligible | ineligible | unknown
    reason: str
    effect_mask: int
    checks: list[Check] = field(default_factory=list)
    boundary: str = "stops before roll_chance(CalcProcChance(...)) in Aura::GetProcEffectMask"

    def to_dict(self) -> dict[str, Any]:
        return {"verdict": self.verdict, "reason": self.reason,
                "effect_mask": self.effect_mask, "boundary": self.boundary,
                "checks": [c.to_dict() for c in self.checks]}


class _Stop(Exception):
    def __init__(self, verdict: str, reason: str) -> None:
        self.verdict = verdict
        self.reason = reason


def can_spell_trigger_proc_on_event(entry, type_mask: int, spell_type_mask: int,
                                    spell_phase_mask: int, hit_mask: int, school_mask: int | None, *,
                                    has_proc_spell: bool, power_cost_positive: bool | None,
                                    event_family: tuple[int, int] | None,
                                    actor_is_player: bool | None, has_action_target: bool,
                                    honor_target: bool | None):
    """Mirrors ``SpellMgr::CanSpellTriggerProcOnEvent`` step by step.

    Yields ``(step, ok, detail)``; ``ok`` is ``True``/``False``/``None``
    (unknown fact) or ``"skip"``.  The caller stops at the first ``False``;
    :func:`can_trigger` collapses the sequence to the consumer's bool.
    """
    tm = type_mask
    yield ("proc-flags-intersect", bool(tm & entry.proc_flags),
           f"event 0x{tm:016X} & entry 0x{entry.proc_flags:016X}")
    if entry.attributes_mask & PROC_ATTR_REQ_EXP_OR_HONOR:
        if actor_is_player is None:
            yield ("req-exp-or-honor", None, "actor_is_player")
        if actor_is_player and has_action_target:
            yield ("req-exp-or-honor", honor_target, "Player::isHonorOrXPTarget")
    if entry.attributes_mask & PROC_ATTR_REQ_POWER_COST:
        yield ("req-power-cost-spell", has_proc_spell, "requires a Spell object")
        yield ("req-power-cost-positive", power_cost_positive, "any SpellPowerCost.Amount > 0")
    if tm & ALWAYS_TRIGGER_EVENT_FLAGS:
        yield ("always-trigger-types", True,
               "HEARTBEAT/KILL/DEATH short-circuit: school, family, type, phase, hit skipped")
        return
    if entry.school_mask:
        if school_mask is None:
            yield ("school-mask", None, "event school mask")
        yield ("school-mask", bool(school_mask & entry.school_mask),
               f"event 0x{school_mask:X} & entry 0x{entry.school_mask:X}")
    if tm & SPELL_PROC_FLAG_MASK:
        if event_family is not None:
            fam, flags = event_family
            affected = (not entry.family_name) or (
                entry.family_name == fam and not (entry.family_mask and not (entry.family_mask & flags)))
            yield ("spell-family", affected,
                   f"entry family {entry.family_name} mask 0x{entry.family_mask:X}; "
                   f"event family {fam} flags 0x{flags:X}")
        else:
            yield ("spell-family", "skip", "no event SpellInfo: family check not evaluated")
        if entry.spell_type_mask:
            yield ("spell-type-mask", bool(spell_type_mask & entry.spell_type_mask),
                   f"event 0x{spell_type_mask:X} & entry 0x{entry.spell_type_mask:X}")
    if tm & REQ_SPELL_PHASE_PROC_FLAG_MASK:
        yield ("spell-phase-mask", bool(spell_phase_mask & entry.spell_phase_mask),
               f"event 0x{spell_phase_mask:X} & entry 0x{entry.spell_phase_mask:X}")
    if (tm & TAKEN_HIT_PROC_FLAG_MASK) or (
            (tm & DONE_HIT_PROC_FLAG_MASK) and not (spell_phase_mask & PROC_SPELL_PHASE_CAST)):
        hit = entry.hit_mask
        default = ""
        if not hit:
            if tm & TAKEN_HIT_PROC_FLAG_MASK:
                hit = PROC_HIT_NORMAL | PROC_HIT_CRITICAL
                default = " (default taken: NORMAL|CRITICAL)"
            else:
                hit = PROC_HIT_NORMAL | PROC_HIT_CRITICAL | PROC_HIT_ABSORB
                default = " (default done: NORMAL|CRITICAL|ABSORB)"
        yield ("hit-mask", bool(hit_mask & hit), f"event 0x{hit_mask:X} & 0x{hit:X}{default}")


def can_trigger(entry, *args, **kwargs) -> bool | None:
    """The consumer's bool (``None`` if a needed fact is unknown)."""
    for _, ok, _ in can_spell_trigger_proc_on_event(entry, *args, **kwargs):
        if ok is None:
            return None
        if ok is False:
            return False
    return True


class Evaluator:
    def __init__(self, catalog: SpellCatalog) -> None:
        self.catalog = catalog

    def evaluate(self, definition: ProcDefinition, event: ProcEvent, holder: str,
                 state: HolderState | None = None, facts: ActorFacts | None = None) -> Eligibility:
        state = state or HolderState()
        facts = facts or ActorFacts()
        checks: list[Check] = []
        try:
            mask = self._run(definition, event, holder, state, facts, checks)
        except _Stop as stop:
            return Eligibility(stop.verdict, stop.reason, 0, checks)
        return Eligibility("eligible", "all pre-RNG checks passed", mask, checks)

    # ------------------------------------------------------------------
    def _run(self, d: ProcDefinition, ev: ProcEvent, holder: str, state: HolderState,
             facts: ActorFacts, checks: list[Check]) -> int:
        info = d.info

        def check(step: str, consumer: str, ok: bool | None, detail: str = "", *,
                  skip: bool = False) -> None:
            if skip:
                checks.append(Check(step, consumer, "skip", detail))
                return
            if ok is None:
                checks.append(Check(step, consumer, "unknown", detail))
                raise _Stop("unknown", f"{step}: required fact unknown ({detail})")
            checks.append(Check(step, consumer, "pass" if ok else "fail", detail))
            if not ok:
                raise _Stop("ineligible", step)

        # -- Unit::ProcSkillsAndAuras ------------------------------------
        check("proc-chain-hard-limit", "Unit::ProcSkillsAndAuras",
              not (ev.has_proc_spell and ev.proc_chain_length >= PROC_CHAIN_HARD_LIMIT),
              f"spell chain length {ev.proc_chain_length} vs limit {PROC_CHAIN_HARD_LIMIT}")
        side = ev.side(holder)
        check("side-receives-event", "Unit::TriggerAurasProcOnEvent",
              bool(side.type_mask) and (holder == "actor" or ev.has_action_target),
              f"{holder} mask 0x{side.type_mask:016X}")

        # -- Aura::GetProcEffectMask ---------------------------------------
        entry = d.entry
        check("has-spell-proc-entry", "Aura::GetProcEffectMask", entry is not None,
              d.status if entry else f"no SpellProcEntry ({d.generation_reason})")
        assert entry is not None

        ev_info = self.catalog.get(ev.spell_id) if ev.spell_id else None
        if ev.spell_id and ev_info is None:
            check("event-spell-known", "ProcEventInfo::GetSpellInfo", None,
                  f"event spell {ev.spell_id} not in snapshot")

        if ev.has_proc_spell:
            assert ev_info is not None
            check("not-triggered-by-this-aura", "Spell::IsTriggeredByAura",
                  ev.spell_triggered_by_aura != info.id,
                  f"event spell triggered by aura {ev.spell_triggered_by_aura}")
            allow_triggered = (info.has_attr(ATTR3_CAN_PROC_FROM_PROCS)
                               or entry.attributes_mask & PROC_ATTR_TRIGGERED_CAN_PROC
                               or side.type_mask & AUTO_ATTACK_PROC_FLAG_MASK)
            if allow_triggered:
                check("triggered-spell-gate", "Aura::GetProcEffectMask", True,
                      "provider allows triggered sources (CAN_PROC_FROM_PROCS / "
                      "TRIGGERED_CAN_PROC / auto-attack flags)")
            else:
                ok = not (ev.spell_is_triggered and not ev_info.has_attr(ATTR3_NOT_A_PROC))
                check("triggered-spell-gate", "Aura::GetProcEffectMask", ok,
                      f"event triggered={ev.spell_is_triggered}, "
                      f"NOT_A_PROC={ev_info.has_attr(ATTR3_NOT_A_PROC)}")
            check("cant-proc-from-item-cast", "Aura::GetProcEffectMask",
                  not (ev.spell_cast_item and entry.attributes_mask & PROC_ATTR_CANT_PROC_FROM_ITEM_CAST),
                  f"cast item={ev.spell_cast_item}")
            check("suppress-weapon-procs", "Aura::GetProcEffectMask",
                  not (ev_info.has_attr(ATTR4_SUPPRESS_WEAPON_PROCS)
                       and info.has_attr(ATTR6_AURA_IS_WEAPON_PROC)))
            check("only-class-abilities", "Aura::GetProcEffectMask",
                  not (info.has_attr(ATTR12_ONLY_PROC_FROM_CLASS_ABILITIES)
                       and not ev_info.has_attr(ATTR13_ALLOW_CLASS_ABILITY_PROCS)))
            if side.type_mask & TAKEN_HIT_PROC_FLAG_MASK:
                check("suppress-target-procs", "Aura::GetProcEffectMask",
                      not (ev_info.has_attr(ATTR3_SUPPRESS_TARGET_PROCS)
                           and not info.has_attr(ATTR7_CAN_PROC_FROM_SUPPRESSED_TARGET_PROCS)))
            else:
                check("suppress-caster-procs", "Aura::GetProcEffectMask",
                      not (ev_info.has_attr(ATTR3_SUPPRESS_CASTER_PROCS)
                           and not ev_info.has_attr(ATTR12_ENABLE_PROCS_FROM_SUPPRESSED_CASTER_PROCS)
                           and not info.has_attr(ATTR12_CAN_PROC_FROM_SUPPRESSED_CASTER_PROCS)))
        else:
            check("spell-object-gates", "Aura::GetProcEffectMask", True,
                  "no Spell attached: triggered/suppression/item-cast gates are not evaluated",
                  skip=True)

        if info.has_aura(SPELL_AURA_MOD_STEALTH):
            check("dont-break-stealth", "Aura::GetProcEffectMask",
                  not (ev_info is not None and ev_info.attributes_cu & SPELL_ATTR0_CU_DONT_BREAK_STEALTH))

        check("has-charges", "Aura::GetProcEffectMask",
              not (state.using_charges and state.charges == 0),
              f"using_charges={state.using_charges} charges={state.charges}")

        if entry.attributes_mask & PROC_ATTR_REQ_SPELLMOD and (
                state.using_charges or entry.attributes_mask & PROC_ATTR_USE_STACKS_FOR_CHARGES):
            if ev.has_proc_spell:
                check("req-spellmod-applied", "Aura::GetProcEffectMask",
                      info.id in ev.spell_applied_mod_auras,
                      "event Spell::m_appliedMods must contain this aura")

        check("proc-cooldown", "Aura::IsProcOnCooldown",
              not (state.proc_cooldown_until_ms > state.now_ms),
              f"cooldown until {state.proc_cooldown_until_ms} ms, now {state.now_ms} ms")

        # -- SpellMgr::CanSpellTriggerProcOnEvent --------------------------
        school = None
        if ev.has_proc_spell and ev_info is not None:
            school = ev_info.school_mask
        elif ev.school_mask is not None:
            school = ev.school_mask
        family = (ev_info.family, ev_info.family_flags) if ev_info is not None else None
        for step, ok, detail in can_spell_trigger_proc_on_event(
                entry, side.type_mask, ev.spell_type_mask, ev.spell_phase_mask, ev.hit_mask,
                school, has_proc_spell=ev.has_proc_spell,
                power_cost_positive=ev.spell_power_cost_positive, event_family=family,
                actor_is_player=facts.actor_is_player, has_action_target=ev.has_action_target,
                honor_target=facts.action_target_gives_xp_or_honor):
            check(step, "SpellMgr::CanSpellTriggerProcOnEvent", ok, detail, skip=ok == "skip")

        # -- conditions and scripts ---------------------------------------
        if d.conditions:
            check("conditions", "ConditionMgr (CONDITION_SOURCE_TYPE_SPELL_PROC)",
                  facts.conditions_met, f"{len(d.conditions)} condition rows")
        hooks = (d.scripts or {}).get("proc_hooks", [])
        if "DoCheckProc" in hooks:
            check("script-check-proc", "Aura::CallScriptCheckProcHandlers",
                  facts.script_check_proc, ", ".join((d.scripts or {}).get("script_names", [])))

        # -- effect mask ----------------------------------------------------
        mask = 0
        for role in d.effects:
            if not role.is_aura:
                continue
            bit = 1 << role.index
            if state.effect_mask is not None and not state.effect_mask & bit:
                continue
            if role.disabled:
                checks.append(Check(f"effect-{role.index}", "SpellProcEntry::DisableEffectsMask",
                                    "fail", "disabled"))
                continue
            ok = self._effect_gate(d, role, ev, facts, checks)
            if ok:
                mask |= bit
        check("any-effect-remains", "Aura::GetProcEffectMask", bool(mask), f"effect mask 0x{mask:X}")

        # -- equipment, outdoors, caster, standing ---------------------------
        if info.has_attr(ATTR0_PASSIVE) and facts.holder_is_player and info.equipped_item_class != -1:
            if info.has_attr(ATTR3_NO_PROC_EQUIP_REQUIREMENT):
                check("equipment", "Aura::GetProcEffectMask", True,
                      "SPELL_ATTR3_NO_PROC_EQUIP_REQUIREMENT", skip=True)
            else:
                if info.equipped_item_class == ITEM_CLASS_WEAPON:
                    check("not-in-feral-form", "Player::IsInFeralForm",
                          None if facts.holder_in_feral_form is None else not facts.holder_in_feral_form)
                    if not ev.has_damage_info:
                        check("equipment", "Aura::GetProcEffectMask", False,
                              "weapon requirement but event has no DamageInfo -> no item")
                    slot = "off hand" if ev.attack_type == OFF_ATTACK else "main hand"
                    check("equipment", "Item::IsFitToSpellRequirements", facts.equipped_item_fits,
                          f"{slot} weapon usable, unbroken, fits class/subclass/invtype")
                elif info.equipped_item_class == ITEM_CLASS_ARMOR:
                    check("equipment", "Item::IsFitToSpellRequirements", facts.equipped_item_fits,
                          "off hand (shield) usable, unbroken, fits")
                else:
                    check("equipment", "Aura::GetProcEffectMask", False,
                          f"EquippedItemClass {info.equipped_item_class}: item stays null")
        if info.has_attr(ATTR3_ONLY_PROC_OUTDOORS):
            check("only-outdoors", "Unit::IsOutdoors", facts.holder_outdoors)
        if info.has_attr(ATTR3_ONLY_PROC_ON_CASTER):
            check("only-on-caster", "Aura::GetProcEffectMask", facts.holder_is_aura_caster)
        if not info.has_attr(ATTR4_ALLOW_PROC_WHILE_SITTING):
            check("standing", "Unit::IsStandState", facts.holder_standing)
        return mask

    def _effect_gate(self, d: ProcDefinition, role, ev: ProcEvent, facts: ActorFacts,
                     checks: list[Check]) -> bool:
        """``AuraEffect::CheckEffectProc`` for one effect (script hook first)."""
        step = f"effect-{role.index}"
        if any(b.startswith(f"EFFECT_{role.index}:") or b.startswith("EFFECT_ALL:")
               for b in role.script_bindings) and "DoCheckEffectProc" in (d.scripts or {}).get("proc_hooks", []):
            result = (facts.script_check_effect or {}).get(role.index)
            if result is None:
                checks.append(Check(step, "AuraScript DoCheckEffectProc", "unknown",
                                    "script result not supplied"))
                raise _Stop("unknown", f"{step}: DoCheckEffectProc result unknown")
            if not result:
                checks.append(Check(step, "AuraScript DoCheckEffectProc", "fail"))
                return False
        ev_info = self.catalog.get(ev.spell_id) if ev.spell_id else None
        a = role.aura
        ok: bool | None = True
        detail = role.aura_name
        if a in _CC_AURAS:
            if not ev.has_damage_info or ev.damage <= 0:
                ok = False
                detail += ": no damage"
            elif ev_info is not None and ev_info.id == d.spell_id:
                ok = None if facts.aura_own_spell_at_full_duration is None else not facts.aura_own_spell_at_full_duration
        elif a in _MECHANIC_AURAS:
            misc = d.info.effect(role.index).misc0
            ok = ev_info is not None and bool(ev_info.mechanic_mask() & (1 << misc))
        elif a in _CAST_TIME_AURAS:
            ok = False if not ev.has_proc_spell else ev.spell_cast_time_nonzero
        elif a in _FROM_CASTER_AURAS:
            ok = facts.event_actor_is_aura_caster
        elif a in _POWER_COST_SCHOOL_AURAS:
            misc = d.info.effect(role.index).misc0
            if ev_info is None or not (ev_info.school_mask & misc) or not ev.has_proc_spell:
                ok = False
            else:
                ok = ev.spell_power_cost_positive
        elif a in _REFLECT_SCHOOL_AURAS:
            misc = d.info.effect(role.index).misc0
            ok = ev_info is not None and bool(ev_info.school_mask & misc)
        elif a in _PROC_TRIGGER_SPELL_AURAS:
            trig = self.catalog.get(role.trigger_spell, d.difficulty) if role.trigger_spell else None
            if trig is not None and trig.has_effect(SPELL_EFFECT_ADD_EXTRA_ATTACKS):
                ok = facts.actor_last_extra_attack_spell != role.trigger_spell
        elif a in _SPELL_CRIT_AURAS:
            ok = ev_info is not None and bool(ev_info.attributes_cu & SPELL_ATTR0_CU_CAN_CRIT)
        if ok is None:
            checks.append(Check(step, "AuraEffect::CheckEffectProc", "unknown", detail))
            raise _Stop("unknown", f"{step}: CheckEffectProc fact unknown")
        checks.append(Check(step, "AuraEffect::CheckEffectProc", "pass" if ok else "fail", detail))
        return bool(ok)
