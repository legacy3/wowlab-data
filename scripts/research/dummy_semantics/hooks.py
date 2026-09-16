"""Exact SpellScript / AuraScript hook vocabulary and its position in the generic pipeline.

Every row below was read from TrinityCore ``7f3d43b``:

* hook lists and their handler types: ``SpellScript.h`` (``HookList<...>`` members);
* call sites and enclosing functions: ``dispatch-tables.json`` (``tools/tc_dispatch_tables.py``);
* default-prevention rules: ``SpellScript.cpp`` (``PreventHitDefaultEffect`` requires
  ``IsInHitPhase() || IsInEffectHook()``; ``AuraScript::PreventDefaultAction`` only
  takes effect in EFFECT_APPLY/REMOVE/PERIODIC/ABSORB/SPLIT/PREPARE_PROC/PROC/EFFECT_PROC);
* mutability rules: ``SpellScript::IsInModifiableHook`` (SetHitDamage/SetHitHeal only in
  EFFECT_LAUNCH_TARGET, EFFECT_HIT_TARGET, BEFORE_HIT, HIT), ``IsInTargetHook``
  (GetHitUnit et al.), ``IsInEffectHook`` (GetEffectValue/SetEffectValue).

``phase`` is the ordinal position in one cast / one aura lifetime, so two hooks
can be compared for "which runs first".  Ties share a phase; within a phase
the enclosing function's statement order in the source decides and is given
in ``order_note``.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any

from .loaders import Dispatch


@dataclass(frozen=True)
class HookSpec:
    list: str                    # HookList member name (what scripts += into)
    kind: str                    # SpellScript | AuraScript
    enum: str                    # SpellScriptHookType / AuraScriptHookType value
    fn_macro: str                # registration macro
    effect_matched: bool         # handler carries (effIndex, effName/auraName/target) and is validated against SpellEffect
    call_site: str               # enclosing engine function
    phase: int                   # ordering key inside one cast / aura event
    order_note: str
    prevent_default: str         # "none" | "PreventHitDefaultEffect" | "PreventDefaultAction" | "return value"
    default_action: str          # what the generic engine does if not prevented
    reads: str                   # runtime facts available
    mutates: str                 # what the hook may change in engine state
    produces: str                # typical action classes observed (navigation only)


SPELL_HOOKS: list[HookSpec] = [
    HookSpec("OnCalcCastTime", "SpellScript", "SPELL_SCRIPT_HOOK_CALC_CAST_TIME", "(virtual CalcCastTime)", False,
             "Spell::prepare", 5, "before OnPrecast", "none", "cast time from SpellCastTimes + mods",
             "caster, spell info", "cast time (int32)", "amount adapter"),
    HookSpec("OnPrecast", "SpellScript", "SPELL_SCRIPT_HOOK_ON_PRECAST", "(virtual OnPrecast)", False,
             "Spell::prepare", 6, "after cast time, before CheckCast", "none", "n/a",
             "caster, explicit targets", "explicit targets, spell values", "target adapter / setup"),
    HookSpec("OnCheckCast", "SpellScript", "SPELL_SCRIPT_HOOK_CHECK_CAST", "SpellCheckCastFn", False,
             "Spell::CheckCast", 10, "after every generic CheckCast rule (conditions source 17 run earlier in CheckCast)", "return value",
             "SPELL_CAST_OK", "caster, explicit target, cast item", "cast result (SpellCastResult), custom error", "cast gate"),
    HookSpec("BeforeCast", "SpellScript", "SPELL_SCRIPT_HOOK_BEFORE_CAST", "SpellCastFn", False,
             "Spell::_cast", 20, "before SelectSpellTargets", "none", "n/a", "caster, explicit targets",
             "spell values, explicit targets", "setup / marker"),
    HookSpec("OnObjectAreaTargetSelect", "SpellScript", "SPELL_SCRIPT_HOOK_OBJECT_AREA_TARGET_SELECT", "SpellObjectAreaTargetSelectFn", True,
             "Spell::SelectImplicit{Cone,Area,Chain,Line}Targets", 30, "after generic search+conditions, before target list is committed", "none",
             "generic implicit target list", "caster, candidate list, spell values", "target list (remove_if/resize/sort)", "target adapter"),
    HookSpec("OnObjectTargetSelect", "SpellScript", "SPELL_SCRIPT_HOOK_OBJECT_TARGET_SELECT", "SpellObjectTargetSelectFn", True,
             "Spell::SelectImplicit{Channel,Nearby,CasterObject,TargetObject}Targets / SelectEffectTypeImplicitTargets", 30,
             "single-object variant", "none", "generic implicit target", "caster, chosen object", "the target object", "target adapter"),
    HookSpec("OnDestinationTargetSelect", "SpellScript", "SPELL_SCRIPT_HOOK_DESTINATION_TARGET_SELECT", "SpellDestinationTargetSelectFn", True,
             "Spell::SelectImplicit{Channel,Nearby,Area,CasterDest,TargetDest,DestDest,Traj}Targets", 30, "destination variant", "none",
             "generic destination", "caster, destination", "the destination", "target adapter"),
    HookSpec("OnCast", "SpellScript", "SPELL_SCRIPT_HOOK_ON_CAST", "SpellCastFn", False,
             "Spell::_cast", 40, "after targets selected, before HandleLaunchPhase / handle_immediate", "none", "n/a",
             "caster, targets, spell values", "spell values; may cast other spells", "child cast / state"),
    HookSpec("OnEffectLaunch", "SpellScript", "SPELL_SCRIPT_HOOK_EFFECT_LAUNCH", "SpellEffectFn", True,
             "Spell::HandleEffects (SPELL_EFFECT_HANDLE_LAUNCH)", 50, "HandleLaunchPhase, once per effect", "PreventHitDefaultEffect",
             "generic effect handler in LAUNCH mode", "caster, effect value", "effect value", "amount adapter"),
    HookSpec("OnEffectLaunchTarget", "SpellScript", "SPELL_SCRIPT_HOOK_EFFECT_LAUNCH_TARGET", "SpellEffectFn", True,
             "Spell::HandleEffects (SPELL_EFFECT_HANDLE_LAUNCH_TARGET)", 51, "HandleLaunchPhase, per target per effect; damage/heal amounts are computed here (EffectSchoolDMG/EffectHeal)",
             "PreventHitDefaultEffect", "generic effect handler in LAUNCH_TARGET mode", "caster, hit unit, effect value",
             "effect value, hit damage/heal (IsInModifiableHook)", "amount adapter"),
    HookSpec("OnEffectHit", "SpellScript", "SPELL_SCRIPT_HOOK_EFFECT_HIT", "SpellEffectFn", True,
             "Spell::HandleEffects (SPELL_EFFECT_HANDLE_HIT) from Spell::_handle_immediate_phase", 60,
             "once per effect, before unit targets are processed", "PreventHitDefaultEffect", "generic handler in HIT mode (dest/no-target effects)",
             "caster, destination, effect value", "effect value", "child cast / summon"),
    HookSpec("BeforeHit", "SpellScript", "SPELL_SCRIPT_HOOK_BEFORE_HIT", "BeforeSpellHitFn", False,
             "Spell::PreprocessSpellHit (TargetInfo::PreprocessTarget)", 70, "per unit target, before immunity/DR/aura preparation of that hit", "none",
             "n/a", "caster, hit unit, miss info", "hit damage/heal (IsInModifiableHook)", "amount adapter / marker"),
    HookSpec("OnHit", "SpellScript", "SPELL_SCRIPT_HOOK_HIT", "SpellHitFn", False,
             "Spell::TargetInfo::PreprocessTarget", 71, "per unit target, after PreprocessSpellHit, before any effect HIT_TARGET handler", "none", "n/a",
             "caster, hit unit", "hit damage/heal (IsInModifiableHook)", "amount adapter / child cast"),
    HookSpec("OnEffectHitTarget", "SpellScript", "SPELL_SCRIPT_HOOK_EFFECT_HIT_TARGET", "SpellEffectFn", True,
             "Spell::HandleEffects (SPELL_EFFECT_HANDLE_HIT_TARGET) from Spell::DoSpellEffectHit", 72,
             "per unit target per effect, after the aura for this hit was created (Aura::TryRefreshStackOrCreate), before damage is dealt",
             "PreventHitDefaultEffect", "generic handler in HIT_TARGET mode (EffectDummy, EffectScriptEffect, EffectEnergize, ...)",
             "caster, hit unit, hit aura, effect value", "effect value, hit damage/heal, hit aura (PreventHitAura)", "child cast / amount / aura mutation"),
    HookSpec("OnEffectSuccessfulDispel", "SpellScript", "SPELL_SCRIPT_HOOK_EFFECT_SUCCESSFUL_DISPEL", "SpellEffectFn", True,
             "Spell::EffectDispel / EffectStealBeneficialBuff", 73, "inside the dispel handler after a successful dispel", "none", "n/a",
             "caster, hit unit, dispelled auras", "may cast", "child cast"),
    HookSpec("OnCalcCritChance", "SpellScript", "SPELL_SCRIPT_HOOK_CALC_CRIT_CHANCE", "SpellOnCalcCritChanceFn", False,
             "Unit::SpellCritChanceDone", 74, "inside damage/heal dealing (DoDamageAndTriggers)", "none", "generic crit chance", "caster, victim",
             "crit chance (float&)", "amount adapter"),
    HookSpec("CalcDamage", "SpellScript", "SPELL_SCRIPT_HOOK_CALC_DAMAGE", "SpellCalcDamageFn", False,
             "Unit::SpellDamageBonusDone / MeleeDamageBonusDone", 74, "inside damage dealing, after generic flat/pct mods are gathered", "none",
             "generic damage bonus pipeline", "caster, victim, effect", "damage, flatMod, pctMod", "amount adapter"),
    HookSpec("CalcHealing", "SpellScript", "SPELL_SCRIPT_HOOK_CALC_HEALING", "SpellCalcHealingFn", False,
             "Unit::SpellHealingBonusDone", 74, "inside heal dealing", "none", "generic healing bonus pipeline", "caster, victim, effect",
             "healing, flatMod, pctMod", "amount adapter"),
    HookSpec("OnCalculateResistAbsorb", "SpellScript", "SPELL_SCRIPT_HOOK_ON_RESIST_ABSORB_CALCULATION", "SpellOnResistAbsorbCalculateFn", False,
             "Unit::CalcAbsorbResist", 75, "after resist/absorb computed", "none", "generic absorb/resist", "damage info",
             "resist amount, absorb amount", "amount adapter"),
    HookSpec("AfterHit", "SpellScript", "SPELL_SCRIPT_HOOK_AFTER_HIT", "SpellHitFn", False,
             "Spell::TargetInfo::DoDamageAndTriggers (end)", 80, "per unit target after damage/heal, aura apply, procs and spell_linked_spell HIT", "none",
             "n/a", "caster, hit unit, hit aura (GetHitAura)", "hit aura (duration/stacks), may cast", "aura mutation / child cast"),
    HookSpec("AfterCast", "SpellScript", "SPELL_SCRIPT_HOOK_AFTER_CAST", "SpellCastFn", False,
             "Spell::_cast", 90, "after handle_immediate (all immediate hits) and before spell_linked_spell CAST", "none", "n/a",
             "caster, explicit targets", "may cast, modify cooldowns/charges", "child cast / cooldown / resource"),
    HookSpec("OnEmpowerStageCompleted", "SpellScript", "SPELL_SCRIPT_HOOK_EMPOWER_STAGE_COMPLETED", "SpellOnEmpowerStageCompletedFn", False,
             "Spell::update (empower channel)", 45, "during empowered channel", "none", "n/a", "caster, stage", "may cast", "child cast"),
    HookSpec("OnEmpowerCompleted", "SpellScript", "SPELL_SCRIPT_HOOK_EMPOWER_COMPLETED", "SpellOnEmpowerCompletedFn", False,
             "Spell::update (empower channel)", 46, "when the empower is released", "none", "n/a", "caster, completed stages", "may cast", "child cast"),
]

AURA_HOOKS: list[HookSpec] = [
    HookSpec("DoCheckAreaTarget", "AuraScript", "AURA_SCRIPT_HOOK_CHECK_AREA_TARGET", "AuraCheckAreaTargetFn", False,
             "Aura::CheckAreaTarget", 5, "when an area aura selects targets", "return value", "target accepted", "aura, candidate unit",
             "accept/reject", "target adapter"),
    HookSpec("DoEffectCalcAmount", "AuraScript", "AURA_SCRIPT_HOOK_EFFECT_CALC_AMOUNT", "AuraEffectCalcAmountFn", True,
             "AuraEffect::CalculateAmount", 10, "after generic CalcValue and hardcoded aura-type amount rules, before stack multiplication", "none",
             "generic amount", "caster, base amount", "amount, canBeRecalculated", "amount adapter"),
    HookSpec("DoEffectCalcPeriodic", "AuraScript", "AURA_SCRIPT_HOOK_EFFECT_CALC_PERIODIC", "AuraEffectCalcPeriodicFn", True,
             "AuraEffect::CalculatePeriodic", 11, "after generic period (haste) computation", "none", "generic period", "caster",
             "isPeriodic, amplitude", "amount adapter"),
    HookSpec("DoEffectCalcSpellMod", "AuraScript", "AURA_SCRIPT_HOOK_EFFECT_CALC_SPELLMOD", "AuraEffectCalcSpellModFn", True,
             "AuraEffect::CalculateSpellMod", 12, "after generic spell mod construction", "none", "generic SpellModifier", "aura effect",
             "the SpellModifier", "amount adapter"),
    HookSpec("OnEffectApply", "AuraScript", "AURA_SCRIPT_HOOK_EFFECT_APPLY", "AuraEffectApplyFn", True,
             "AuraEffect::HandleEffect (apply)", 20, "after _RegisterAuraEffect/ApplySpellMod, before the AuraEffectHandler", "PreventDefaultAction",
             "AuraEffectHandler[aura type] (e.g. HandleAuraDummy)", "aura, target, caster, mode", "may cast/remove/mutate", "full replacement / augmentation"),
    HookSpec("AfterEffectApply", "AuraScript", "AURA_SCRIPT_HOOK_EFFECT_AFTER_APPLY", "AuraEffectApplyFn", True,
             "AuraEffect::HandleEffect (apply)", 21, "after the AuraEffectHandler", "none", "n/a", "aura, target, caster, mode",
             "may cast/remove/mutate", "augmentation / child cast"),
    HookSpec("OnEffectUpdatePeriodic", "AuraScript", "AURA_SCRIPT_HOOK_EFFECT_UPDATE_PERIODIC", "AuraEffectUpdatePeriodicFn", True,
             "AuraEffect::Update", 30, "each update tick of a periodic effect, before the timer is checked", "none", "n/a", "aura effect",
             "periodic timer/amount", "state"),
    HookSpec("OnEffectPeriodic", "AuraScript", "AURA_SCRIPT_HOOK_EFFECT_PERIODIC", "AuraEffectPeriodicFn", True,
             "AuraEffect::PeriodicTick", 31, "first statement of PeriodicTick", "PreventDefaultAction",
             "PeriodicTick switch (PERIODIC_DUMMY: nothing; PERIODIC_TRIGGER_SPELL: cast trigger; damage/heal ticks)",
             "aura, target, caster, tick number", "may cast / deal damage / mutate", "full replacement (dummy) / augmentation"),
    HookSpec("OnEffectAbsorb", "AuraScript", "AURA_SCRIPT_HOOK_EFFECT_ABSORB", "AuraEffectAbsorbFn", True,
             "Unit::CalcAbsorbResist", 40, "per absorb aura while damage is being absorbed", "PreventDefaultAction",
             "generic absorb: amount -= absorbed", "damage info, absorb amount", "absorb amount", "amount adapter"),
    HookSpec("AfterEffectAbsorb", "AuraScript", "AURA_SCRIPT_HOOK_EFFECT_AFTER_ABSORB", "AuraEffectAbsorbFn", True,
             "Unit::CalcAbsorbResist", 41, "after the absorb was applied", "none", "n/a", "damage info, absorb amount", "may cast", "child cast"),
    HookSpec("OnEffectAbsorbHeal", "AuraScript", "AURA_SCRIPT_HOOK_EFFECT_ABSORB", "AuraEffectAbsorbHealFn", True,
             "Unit::CalcHealAbsorb", 40, "heal absorb variant", "PreventDefaultAction", "generic heal absorb", "heal info", "absorb amount", "amount adapter"),
    HookSpec("AfterEffectAbsorbHeal", "AuraScript", "AURA_SCRIPT_HOOK_EFFECT_AFTER_ABSORB", "AuraEffectAbsorbHealFn", True,
             "Unit::CalcHealAbsorb", 41, "after the heal absorb was applied", "none", "n/a", "heal info, absorb amount", "may cast", "child cast"),
    HookSpec("AfterEffectManaShield", "AuraScript", "AURA_SCRIPT_HOOK_EFFECT_AFTER_MANASHIELD", "AuraEffectManaShieldFn", True,
             "Unit::CalcAbsorbResist", 41, "after the mana shield was applied", "none", "n/a", "damage info, absorb amount", "may cast", "child cast"),
    HookSpec("OnEffectManaShield", "AuraScript", "AURA_SCRIPT_HOOK_EFFECT_MANASHIELD", "AuraEffectManaShieldFn", True,
             "Unit::CalcAbsorbResist", 40, "mana shield variant", "PreventDefaultAction", "generic mana shield", "damage info", "absorb amount", "amount adapter"),
    HookSpec("OnEffectSplit", "AuraScript", "AURA_SCRIPT_HOOK_EFFECT_SPLIT", "AuraEffectSplitFn", True,
             "Unit::CalcAbsorbResist", 42, "split damage variant", "PreventDefaultAction", "generic split", "damage info", "split amount", "amount adapter"),
    HookSpec("DoCheckProc", "AuraScript", "AURA_SCRIPT_HOOK_CHECK_PROC", "AuraCheckProcFn", False,
             "Aura::GetProcEffectMask", 50, "after generic eligibility (flags, conditions source 24), before the RNG roll", "return value",
             "eligible", "proc event (actor, target, spell, damage/heal info)", "accept/reject", "proc filter adapter"),
    HookSpec("DoCheckEffectProc", "AuraScript", "AURA_SCRIPT_HOOK_CHECK_EFFECT_PROC", "AuraCheckEffectProcFn", True,
             "AuraEffect::CheckEffectProc", 51, "per effect, before the aura-type specific gates (CC break, mechanic, school...)", "return value",
             "effect eligible", "proc event", "accept/reject per effect", "proc filter adapter"),
    HookSpec("DoPrepareProc", "AuraScript", "AURA_SCRIPT_HOOK_PREPARE_PROC", "AuraProcFn", False,
             "Aura::PrepareProcToTrigger", 52, "after the roll succeeded, before charge drop / cooldown", "PreventDefaultAction",
             "PrepareProcChargeDrop + AddProcCooldown", "proc event", "charge/cooldown handling", "state"),
    HookSpec("OnProc", "AuraScript", "AURA_SCRIPT_HOOK_PROC", "AuraProcFn", False,
             "Aura::TriggerProcOnEvent", 53, "before per-effect HandleProc", "PreventDefaultAction",
             "per-effect HandleProc loop", "proc event", "may cast / mutate", "full replacement / child cast"),
    HookSpec("OnEffectProc", "AuraScript", "AURA_SCRIPT_HOOK_EFFECT_PROC", "AuraEffectProcFn", True,
             "AuraEffect::HandleProc", 54, "first statement of HandleProc", "PreventDefaultAction",
             "HandleProc switch (DUMMY/PROC_TRIGGER_SPELL -> cast TriggerSpell; WITH_VALUE; PROC_TRIGGER_DAMAGE; CC drop)",
             "proc event, effect amount", "may cast / mutate", "full replacement (dummy) / child cast / amount"),
    HookSpec("AfterEffectProc", "AuraScript", "AURA_SCRIPT_HOOK_EFFECT_AFTER_PROC", "AuraEffectProcFn", True,
             "AuraEffect::HandleProc (end)", 55, "after the generic action", "none", "n/a", "proc event", "may cast / mutate", "augmentation"),
    HookSpec("AfterProc", "AuraScript", "AURA_SCRIPT_HOOK_AFTER_PROC", "AuraProcFn", False,
             "Aura::TriggerProcOnEvent (end)", 56, "after all effects; then ConsumeProcCharges", "none", "n/a", "proc event", "may cast / mutate", "augmentation"),
    HookSpec("OnEffectRemove", "AuraScript", "AURA_SCRIPT_HOOK_EFFECT_REMOVE", "AuraEffectRemoveFn", True,
             "AuraEffect::HandleEffect (remove)", 60, "before the AuraEffectHandler (remove)", "PreventDefaultAction",
             "AuraEffectHandler[aura type] remove branch", "aura, target, caster, remove mode", "may cast / mutate", "full replacement / augmentation"),
    HookSpec("AfterEffectRemove", "AuraScript", "AURA_SCRIPT_HOOK_EFFECT_AFTER_REMOVE", "AuraEffectRemoveFn", True,
             "AuraEffect::HandleEffect (remove)", 61, "after the AuraEffectHandler (remove)", "none", "n/a", "aura, target, caster, remove mode",
             "may cast / mutate", "child cast on expire / cleanup"),
    HookSpec("OnDispel", "AuraScript", "AURA_SCRIPT_HOOK_DISPEL", "AuraDispelFn", False,
             "Aura::CallScriptDispel (Unit::RemoveAurasDueToSpellByDispel)", 62, "before dispel removal", "none", "n/a", "dispel info", "may cast", "child cast"),
    HookSpec("AfterDispel", "AuraScript", "AURA_SCRIPT_HOOK_AFTER_DISPEL", "AuraDispelFn", False,
             "Aura::CallScriptAfterDispel", 63, "after dispel removal", "none", "n/a", "dispel info", "may cast", "child cast"),
    HookSpec("OnHeartbeat", "AuraScript", "AURA_SCRIPT_HOOK_ON_HEARTBEAT", "AuraHeartbeatFn", False,
             "UnitAura::Heartbeat", 35, "every heartbeat (5 s) of a unit aura", "none", "n/a", "aura, owner", "may cast / mutate", "state"),
    HookSpec("OnEnterLeaveCombat", "AuraScript", "AURA_SCRIPT_HOOK_ENTER_LEAVE_COMBAT", "AuraEnterLeaveCombatFn", False,
             "Aura::CallScriptEnterLeaveCombatHandlers (Unit combat state change)", 36, "on combat state change", "none", "n/a", "aura, owner, isNowInCombat",
             "may cast / mutate", "state"),
    HookSpec("DoEffectCalcCritChance", "AuraScript", "AURA_SCRIPT_HOOK_EFFECT_CALC_CRIT_CHANCE", "AuraEffectCalcCritChanceFn", True,
             "AuraEffect::GetCritChanceFor", 13, "periodic crit chance", "none", "generic crit chance", "caster, victim", "crit chance", "amount adapter"),
    HookSpec("DoEffectCalcDamageAndHealing", "AuraScript", "AURA_SCRIPT_HOOK_EFFECT_CALC_DAMAGE_AND_HEALING", "AuraEffectCalcDamageFn/AuraEffectCalcHealingFn", True,
             "Unit::SpellDamageBonusDone / SpellHealingBonusDone (periodic)", 14, "periodic tick amount pipeline", "none", "generic bonus pipeline",
             "caster, victim, amount", "damageOrHealing, flatMod, pctMod", "amount adapter"),
]

BY_LIST: dict[str, HookSpec] = {h.list: h for h in SPELL_HOOKS + AURA_HOOKS}
#: hook lists whose handler can prevent the generic default (the effect/aura handler)
PREVENTING_LISTS = {h.list for h in SPELL_HOOKS + AURA_HOOKS if h.prevent_default in ("PreventHitDefaultEffect", "PreventDefaultAction")}


def vocabulary(dispatch: Dispatch | None = None) -> dict[str, Any]:
    """The hook table plus a cross-check against the generated dispatch corpus."""
    out: dict[str, Any] = {"spell": [asdict(h) for h in SPELL_HOOKS], "aura": [asdict(h) for h in AURA_HOOKS]}
    if dispatch is not None:
        listed = {h["list"] for h in dispatch.hook_lists}
        known = set(BY_LIST)
        out["cross_check"] = {
            "hook_lists_in_header_not_modelled": sorted(listed - known),
            "modelled_not_in_header": sorted(known - listed - {"OnCalcCastTime", "OnPrecast"}),
            "spell_hook_enum_count": len(dispatch.spell_hook_enum),
            "aura_hook_enum_count": len(dispatch.aura_hook_enum),
            "call_sites": len(dispatch.call_sites),
        }
    return out
