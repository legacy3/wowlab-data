"""Track H: external lifecycle policy -- world overlays, SpellInfo corrections,
engine spell-id branches and spell/aura scripts.

The question: for an aura-producing spell, which *lifecycle surfaces*
(identity, application, duration, refresh, stacks, charges, periodic, amount,
removal, dispel, recipients, proc, persistence, combat-state, heartbeat) are
decided by something other than the spell's own DB2 rows interpreted by
Trinity's generic consumer?

Everything is reported as a **lifecycle fact** ("removal of X casts Y unless
the remove mode is DEATH"), never as a Trinity SQL row.  The originating table
or code site is kept as provenance (``kind`` + ``coords``) only.

Policy-source vocabulary (``aura_lifecycle.POLICY_SOURCE``) as used here:

``db2``              the surface is decided by the spell's own DB2 fields through the
                     generic Trinity consumer; no per-spell server policy touches it
``trinity-default``  the surface has no per-spell authored input at all (death,
                     persistence, combat transitions, heartbeat); generic engine rule
``world-overlay``    per-spell *data* authored by the server: TDB world tables
                     (spell_linked_spell, spell_area, spell_group(+stack rules),
                     spell_proc, spell_custom_attr, conditions, spell_pet_auras,
                     serverside_spell) and data-patch writes of
                     ``SpellMgr::LoadSpellInfoCorrections`` (C++ medium, data shape)
``script``           per-spell *code*: SpellScript/AuraScript handlers bound by
                     spell_script_names, runtime-code correction writes, and engine
                     branches keyed on a hardcoded SpellID / SpellFamilyFlags
``combined``         the default path plus at least one augmenting external touch, or
                     touches from more than one external class
``unknown``          a bound ScriptName resolves to no script class

A touch ``mode`` is ``replace`` (the external value/handler supersedes the
default for that surface), ``augment`` (adds behaviour, default still runs),
``gate`` (decides *whether* the lifecycle event happens / who receives it) or
``cascade`` (this aura's lifecycle event drives another aura's lifecycle).

Absence of a touch here is **not** proof of absence of external behaviour:
coverage is exactly the sources listed in :data:`SOURCE_KINDS` (e.g. creature
AI scripts, SmartAI, areatrigger scripts, instance scripts, and engine branches
keyed on anything other than a literal SpellID/family flag are not indexed).

Standard library only.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import Any

from . import FailClosed, POLICY_SOURCE

# ---------------------------------------------------------------------------
# vocabulary
# ---------------------------------------------------------------------------

#: surface -> (owning track, baseline policy when untouched, meaning)
SURFACES: dict[str, tuple[str, str, str]] = {
    "identity": ("A", "db2", "which applications coexist / replace / refresh (CanStackWith, groups, exclusivity)"),
    "application": ("A", "db2", "whether/when the aura is created and applied, co-applied auras, apply-time actions"),
    "duration": ("B", "db2", "initial / max duration and explicit duration mutation"),
    "refresh": ("B", "db2", "what reapplication (refresh / stack reapply) executes"),
    "stacks": ("C", "db2", "stack capacity, stack mutation, stack-coupled auras"),
    "charges": ("C", "db2", "initial proc charges and charge consumption"),
    "periodic": ("D", "db2", "period, tick action, periodic timer mutation"),
    "amount": ("D", "db2", "effect amount computation and explicit amount changes"),
    "removal": ("E", "combined", "what removes the aura and what removal executes (DB2 interrupt flags/duration + engine rules)"),
    "dispel": ("E", "db2", "dispel type / mechanic and dispel-time actions"),
    "recipients": ("F", "db2", "who receives the aura (initial targets, area-aura recipient map)"),
    "proc": ("C", "db2", "proc eligibility / consumption policy (SpellProcEntry)"),
    "persistence": ("E", "trinity-default", "save/load of the aura across logout"),
    "combat-state": ("E", "trinity-default", "reaction to owner entering/leaving combat"),
    "heartbeat": ("D", "trinity-default", "5 s unit-aura heartbeat actions"),
}
assert all(v[1] in POLICY_SOURCE for v in SURFACES.values())

#: source kind -> (policy source, coords of the consumer / loader)
SOURCE_KINDS: dict[str, tuple[str, str]] = {
    "spell_linked_spell": ("world-overlay", "SpellMgr.cpp:2074 LoadSpellLinked; SpellAuras.cpp:1401-1455; Spell.cpp:3353,3949"),
    "spell_area": ("world-overlay", "SpellMgr.cpp:2296 LoadSpellAreas; SpellAuras.cpp:1380-1398; Player.cpp:26483-26511,16040-16060"),
    "spell_group": ("world-overlay", "SpellMgr.cpp:1266,1345,447; SpellAuras.cpp:1674; Unit.cpp:14266,14301"),
    "spell_proc": ("world-overlay", "SpellMgr.cpp:1497 LoadSpellProcs; SpellAuras.cpp:1004 CalcMaxCharges, 1820 ConsumeProcCharges"),
    "spell_custom_attr": ("world-overlay", "SpellMgr.cpp:2981-3021; SpellAuras.cpp:1193,1763; Unit.cpp:3401,4339"),
    "conditions": ("world-overlay", "ConditionMgr (source 13 implicit target -> SpellAuras.cpp:2579,2726 area-aura maps; 17 cast; 24 proc SpellAuras.cpp:1903)"),
    "spell_pet_auras": ("world-overlay", "SpellMgr.cpp:1965; SpellEffects.cpp:582; SpellAuraEffects.cpp:4873; Player.cpp:3201"),
    "serverside_spell": ("world-overlay", "SpellMgr.cpp:2735 LoadSpellInfoServerside (cannot override DB2 spells)"),
    "spellinfo_correction": ("world-overlay", "SpellMgr.cpp:3390 LoadSpellInfoCorrections (data-patch writes)"),
    "spellinfo_correction_code": ("script", "SpellMgr.cpp:3390 LoadSpellInfoCorrections (runtime-code writes)"),
    "engine_spell_id": ("script", "engine branch keyed on a literal SpellID (dummy-corpora hardcoded index)"),
    "engine_family_flag": ("script", "engine branch keyed on SpellFamilyName+SpellFamilyFlags"),
    "aura-script": ("script", "AuraScript hook (SpellScript.h hook lists; call sites per dummy-corpora/hooks.json)"),
    "spell-script": ("script", "SpellScript hook bound to an aura effect or touching the hit aura"),
    "other-spell-script": ("script", "a script bound to a *different* spell mutates/removes/applies this aura"),
    "script-unresolved": ("unknown", "spell_script_names name with no resolvable SpellScript/AuraScript class"),
}
assert all(v[0] in POLICY_SOURCE for v in SOURCE_KINDS.values())

MODES = ("replace", "augment", "gate", "cascade")

#: AuraScript hook list -> surfaces its *registration* touches (independent of body).
AURA_HOOK_SURFACES: dict[str, tuple[str, ...]] = {
    "OnEffectApply": ("application",), "AfterEffectApply": ("application",),
    "OnEffectRemove": ("removal",), "AfterEffectRemove": ("removal",),
    "OnEffectPeriodic": ("periodic",), "OnEffectUpdatePeriodic": ("periodic",),
    "DoEffectCalcPeriodic": ("periodic",), "DoEffectCalcAmount": ("amount",),
    "DoEffectCalcSpellMod": ("amount",), "DoEffectCalcCritChance": ("amount",),
    "DoEffectCalcDamageAndHealing": ("amount",),
    "DoCheckAreaTarget": ("recipients",),
    "OnDispel": ("dispel",), "AfterDispel": ("dispel",),
    "OnEffectAbsorb": ("amount",), "AfterEffectAbsorb": ("amount",), "OnEffectAbsorbHeal": ("amount",),
    "AfterEffectAbsorbHeal": ("amount",), "OnEffectManaShield": ("amount",), "AfterEffectManaShield": ("amount",),
    "OnEffectSplit": ("amount",),
    "DoCheckProc": ("proc",), "DoCheckEffectProc": ("proc",), "DoPrepareProc": ("proc", "charges"),
    "OnProc": ("proc",), "OnEffectProc": ("proc",), "AfterEffectProc": ("proc",), "AfterProc": ("proc",),
    "OnHeartbeat": ("heartbeat",), "OnEnterLeaveCombat": ("combat-state",),
}
#: hooks whose PreventDefaultAction replaces the default for the surface.
REPLACEABLE = {"OnEffectApply": "application", "OnEffectRemove": "removal", "OnEffectPeriodic": "periodic",
               "OnEffectAbsorb": "amount", "OnEffectSplit": "amount", "OnEffectManaShield": "amount",
               "OnEffectAbsorbHeal": "amount", "DoPrepareProc": "charges", "OnProc": "proc", "OnEffectProc": "proc"}
#: hooks whose return value / out-parameter *is* the surface value (always replace-capable).
VALUE_HOOKS = {"DoEffectCalcAmount": "amount", "DoEffectCalcPeriodic": "periodic", "DoCheckAreaTarget": "recipients",
               "DoCheckProc": "proc", "DoCheckEffectProc": "proc", "DoEffectCalcSpellMod": "amount"}

#: handler call -> surface when the call mutates an aura (receiver decides self/other).
ACTION_SURFACES: dict[str, str] = {
    "SetDuration": "duration", "SetMaxDuration": "duration", "RefreshDuration": "duration",
    "ModStackAmount": "stacks", "SetStackAmount": "stacks", "RemoveAuraFromStack": "stacks",
    "SetCharges": "charges", "ModCharges": "charges", "DropCharge": "charges", "DropChargeDelayed": "charges",
    "ModChargesDelayed": "charges",
    "ChangeAmount": "amount", "SetAmount": "amount", "RecalculateAmount": "amount",
    "RecalculateAmountOfEffects": "amount",
    "SetPeriodicTimer": "periodic", "ResetPeriodic": "periodic", "SetPeriodic": "periodic",
    "CalculatePeriodic": "periodic", "ResetTicks": "periodic",
    "Remove": "removal", "RemoveAura": "removal", "RemoveAurasDueToSpell": "removal", "RemoveOwnedAura": "removal",
}
APPLY_CALLS = {"CastSpell", "AddAura"}
#: receivers that denote the script's own aura / aura effect.
_SELF_RECV = re.compile(r"^(|GetAura\(\)|GetBase\(\)|aurEff|aurEff->GetBase\(\)|GetEffect\(\s*EFFECT_\d+\s*\)|"
                        r"const_cast<AuraEffect\s*\*>\(aurEff\)|GetAura\(\)->GetEffect\(\s*EFFECT_\d+\s*\)|"
                        r"GetHitAura\(\)|GetHitAura\(\)->GetEffect\(\s*EFFECT_\d+\s*\))$")
REMOVE_MODES = ("AURA_REMOVE_BY_DEFAULT", "AURA_REMOVE_BY_INTERRUPT", "AURA_REMOVE_BY_CANCEL",
                "AURA_REMOVE_BY_ENEMY_SPELL", "AURA_REMOVE_BY_EXPIRE", "AURA_REMOVE_BY_DEATH")

#: AuraEffectHandleModes (SpellAuraDefines.h:40-53)
HANDLE_MODES = {"AURA_EFFECT_HANDLE_DEFAULT": 0x0, "AURA_EFFECT_HANDLE_REAL": 0x01,
                "AURA_EFFECT_HANDLE_SEND_FOR_CLIENT": 0x02, "AURA_EFFECT_HANDLE_CHANGE_AMOUNT": 0x04,
                "AURA_EFFECT_HANDLE_REAPPLY": 0x08, "AURA_EFFECT_HANDLE_STAT": 0x10, "AURA_EFFECT_HANDLE_SKILL": 0x20,
                "AURA_EFFECT_HANDLE_SEND_FOR_CLIENT_MASK": 0x03, "AURA_EFFECT_HANDLE_CHANGE_AMOUNT_MASK": 0x05,
                "AURA_EFFECT_HANDLE_CHANGE_AMOUNT_SEND_FOR_CLIENT_MASK": 0x07,
                "AURA_EFFECT_HANDLE_REAL_OR_REAPPLY_MASK": 0x09}
REAL, CHANGE_AMOUNT, REAPPLY = 0x01, 0x04, 0x08

#: LoadSpellInfoCorrections member -> surfaces (Attributes* handled by value tokens).
CORRECTION_MEMBER_SURFACES: dict[str, tuple[str, ...]] = {
    "DurationEntry": ("duration",), "StackAmount": ("stacks",), "ProcCharges": ("charges",),
    "ProcFlags": ("proc",), "ProcChance": ("proc",), "ProcCooldown": ("proc",), "ProcBasePPM": ("proc",),
    "ApplyAuraPeriod": ("periodic",), "Amplitude": ("periodic",),
    "BasePoints": ("amount",), "BonusCoefficient": ("amount",), "BonusCoefficientFromAP": ("amount",),
    "RealPointsPerLevel": ("amount",), "PointsPerResource": ("amount",), "Scaling": ("amount",),
    "MiscValue": ("amount",), "MiscValueB": ("amount",),
    "AuraInterruptFlags": ("removal",), "AuraInterruptFlags2": ("removal",),
    "ChannelInterruptFlags": ("removal",), "ChannelInterruptFlags2": ("removal",),
    "Dispel": ("dispel",), "Mechanic": ("dispel",), "EffectMechanic": ("dispel",),
    "MaxAffectedTargets": ("recipients",), "TargetA": ("recipients",), "TargetB": ("recipients",),
    "TargetARadiusEntry": ("recipients",), "TargetBRadiusEntry": ("recipients",), "RadiusEntry": ("recipients",),
    "MaxRadiusEntry": ("recipients",), "ChainTargets": ("recipients",),
    "Effect": ("application",), "ApplyAuraName": ("application",), "TriggerSpell": ("periodic", "proc"),
    "SpellClassMask": ("proc", "amount"), "AttributesCu": ("application",), "NegativeEffects": ("dispel", "identity"),
    "Stances": ("removal",), "StancesNot": ("removal",),
}
#: attribute-name fragments (value tokens of Attributes* correction writes) -> surfaces.
ATTR_TOKEN_SURFACES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (r"DOT_STACKING_RULE|AURA_UNIQUE|STACK_FOR_DIFF_CASTERS|ONLY_ONE_PER_CASTER", ("identity",)),
    (r"PERIODIC_REFRESH_EXTENDS_DURATION|AURA_DOES_NOT_REFRESH", ("refresh",)),
    (r"HASTE_AFFECTS_DURATION|DURATION", ("duration",)),
    (r"HASTE_AFFECTS_PERIODIC|ROLLING_PERIODIC|EXTRA_INITIAL_PERIOD|PERIODIC", ("periodic",)),
    (r"PASSIVE|DO_NOT_DISPLAY|HIDDEN", ("application",)),
    (r"DEATH_PERSISTENT|ALLOW_WHILE_DEAD|NO_AURA_CANCEL|NOT_BREAK|DISMOUNT|CANCEL|INTERRUPT|REMOVE|CHANNEL",
     ("removal",)),
    (r"DISPEL|NO_IMMUNITIES|UNAFFECTED_BY_INVULNERABILITY|IMMUN", ("dispel",)),
    (r"PROC|TRIGGERED", ("proc",)),
    (r"TARGET|AREA|RAID|PARTY|ALLOW_ENEMY", ("recipients",)),
)

#: engine function (hardcoded spell-id site) -> surfaces.
ENGINE_FUNCTION_SURFACES: dict[str, tuple[str, ...]] = {
    "SpellInfo::_LoadSpellSpecific": ("identity",), "SpellInfo::IsMultiSlotAura": ("identity",),
    "Aura::HandleAuraSpecificMods": ("application", "removal", "refresh"),
    "AuraEffect::HandleAuraDummy": ("application", "removal"),
    "AuraEffect::HandleShapeshiftBoosts": ("application", "removal"),
    "AuraEffect::HandleAuraModShapeshift": ("application", "removal"),
    "AuraEffect::HandleAuraTransform": ("application", "removal"),
    "AuraEffect::HandleAuraControlVehicle": ("application", "removal"),
    "AuraEffect::HandleChannelDeathItem": ("removal",),
    "AuraEffect::HandleLearnSpell": ("application", "removal"),
    "AuraEffect::HandleAuraModPacifyAndSilence": ("application", "removal"),
    "AuraEffect::HandleAuraModIncreaseFlightSpeed": ("application", "removal"),
    "AuraEffect::HandlePeriodicDamageAurasTick": ("periodic", "amount"),
    "AuraEffect::HandlePeriodicManaLeechAuraTick": ("periodic", "amount"),
    "SpellInfo::_LoadSpellDiminishInfo": ("duration",),
    "SpellInfo::IsUpdatingTemporaryAuraValuesBySpellMod": ("amount",),
    "Aura::ConsumeProcCharges": ("charges",),
    "Pet::CastPetAura": ("application",),
    "Player::_LoadAuras": ("persistence",),
    "WorldSession::HandleCancelAuraOpcode": ("removal",),
    "WorldSession::HandlePetCancelAuraOpcode": ("removal",),
    "Player::ResurrectPlayer": ("removal",), "Player::BuildPlayerRepop": ("application", "removal"),
    "Spell::EffectDispelMechanic": ("dispel",),
}

#: two-argument aura hook macros -> implied aura type (SpellScript.h:1744-1784)
IMPLIED_AURA_MACROS = {"AuraEffectCalcAbsorbFn": "SCHOOL_ABSORB", "AuraEffectAbsorbFn": "SCHOOL_ABSORB",
                       "AuraEffectAbsorbOverkillFn": "SCHOOL_ABSORB_OVERKILL",
                       "AuraEffectAbsorbHealFn": "SCHOOL_HEAL_ABSORB", "AuraEffectManaShieldFn": "MANA_SHIELD",
                       "AuraEffectSplitFn": "SPLIT_DAMAGE_PCT"}

# spell_custom_attr bits consumed by the aura lifecycle (SpellInfo.h:144-169)
CU_ENCHANT_PROC, CU_AURA_CC, CU_AURA_CANNOT_BE_SAVED = 0x00000001, 0x00000020, 0x01000000
CU_SURFACES = {CU_ENCHANT_PROC: ("identity", "SPELL_ATTR0_CU_ENCHANT_PROC", "cast-item GUID joins the stacking key",
                                 "Unit.cpp:3401; SpellAuras.cpp:1763"),
               CU_AURA_CC: ("removal", "SPELL_ATTR0_CU_AURA_CC", "exempt from RemoveAurasByShapeShift mechanic removal",
                            "Unit.cpp:4339"),
               CU_AURA_CANNOT_BE_SAVED: ("persistence", "SPELL_ATTR0_CU_AURA_CANNOT_BE_SAVED", "not saved on logout",
                                         "SpellAuras.cpp:1193")}

# SpellLinkedType (SpellMgr.h)
LINK_CAST, LINK_HIT, LINK_AURA, LINK_REMOVE = 0, 1, 2, 3
SPELL_AREA_FLAG_AUTOCAST, SPELL_AREA_FLAG_AUTOREMOVE = 0x1, 0x2
PROC_ATTR_USE_STACKS_FOR_CHARGES = 0x10
SPELL_GROUP_STACK_RULES = ("DEFAULT", "EXCLUSIVE", "EXCLUSIVE_FROM_SAME_CASTER", "EXCLUSIVE_SAME_EFFECT",
                           "EXCLUSIVE_HIGHEST")
SPELLFAMILY_WARRIOR, SPELLFAMILY_PRIEST, SPELLFAMILY_DRUID = 4, 6, 7
CONDITION_SOURCE_SPELL_IMPLICIT_TARGET, CONDITION_SOURCE_SPELL, CONDITION_SOURCE_SPELL_PROC = 13, 17, 24


def touch(surface: str, kind: str, mode: str, fact: str, coords: str | list[str], evidence: str | list[str],
          **extra: Any) -> dict[str, Any]:
    if surface not in SURFACES and surface != "any":
        raise ValueError(surface)
    if kind not in SOURCE_KINDS or mode not in MODES:
        raise ValueError((kind, mode))
    return {"surface": surface, "kind": kind, "policy_source": SOURCE_KINDS[kind][0], "mode": mode, "fact": fact,
            "coords": [coords] if isinstance(coords, str) else list(coords),
            "evidence": [evidence] if isinstance(evidence, str) else list(evidence), **extra}


def classify(surface: str, touches: list[dict[str, Any]]) -> str:
    """Policy source of one surface given its external touches (module docstring)."""
    if not touches:
        return SURFACES[surface][1]
    if any(t["policy_source"] == "unknown" for t in touches):
        return "unknown"
    sources = {t["policy_source"] for t in touches}
    if len(sources) == 1 and all(t["mode"] == "replace" for t in touches):
        return next(iter(sources))
    return "combined"


# ---------------------------------------------------------------------------
# pure mirrors (fixtures in, facts out) -- used by the timelines and tests
# ---------------------------------------------------------------------------

def parse_handle_mode(token: str) -> int:
    """``AuraEffectHandleModes`` expression of a hook registration -> mask.  Unknown token -> FailClosed."""
    mask = 0
    for part in re.split(r"[|()\s]+", token.replace("AuraEffectHandleModes", "")):
        if not part:
            continue
        if part not in HANDLE_MODES:
            raise FailClosed(f"unknown AuraEffectHandleModes token {part!r}")
        mask |= HANDLE_MODES[part]
    return mask


def apply_remove_hook_fires(registered: int, event: str, amount_changed: bool = False) -> bool:
    """Does an OnEffectApply/Remove-family handler registered with ``registered`` run for ``event``?

    Mirrors: SpellScript.h:1389 (``if (!(_mode & mode)) return;``) with the modes passed by
    AuraApplication::_HandleEffect SpellAuras.cpp:176,182 (``real``: REAL),
    AuraEffect::ChangeAmount SpellAuraEffects.cpp:1094-1098 (``stack-or-refresh``: REAPPLY, plus
    CHANGE_AMOUNT when the recomputed amount differs; ``amount-change``: CHANGE_AMOUNT only).
    """
    if event == "real":
        mode = REAL
    elif event == "stack-or-refresh":
        mode = REAPPLY | (CHANGE_AMOUNT if amount_changed else 0)
    elif event == "amount-change":
        if not amount_changed:
            return False  # ChangeAmount returns early (handleMask == 0) SpellAuraEffects.cpp:1100
        mode = CHANGE_AMOUNT
    else:
        raise FailClosed(f"unknown aura effect handle event {event!r}")
    return bool(registered & mode)


def specific_mods_blocks(apply: bool, on_reapply: bool) -> list[str]:
    """Blocks of ``Aura::HandleAuraSpecificMods`` that execute for (apply, onReapply).

    Mirrors: SpellAuras.cpp:1375-1602 (block guards: spell_area 1380 unguarded; spell_linked_spell
    1401 ``!onReapply`` / 1446 ``else if (apply)``; family apply 1458 ``if (apply)``; family remove
    1492 ``else``; family apply-or-remove 1555 unguarded; CreatureAI 1590-1601 apply/else).
    """
    out = ["spell_area"]
    if not on_reapply:
        out.append("linked_apply" if apply else "linked_remove")
    elif apply:
        out.append("linked_stack_sync")
    out.append("family_apply" if apply else "family_remove")
    out.append("family_apply_or_remove")
    out.append("creature_ai_on_aura_applied" if apply else "creature_ai_on_aura_removed")
    return out


def specific_mods_site_surfaces(line: int) -> tuple[str, ...]:
    """Surfaces of a hardcoded-SpellID site inside ``Aura::HandleAuraSpecificMods`` by its block.

    Mirrors: SpellAuras.cpp:1457 (``// mods at aura apply``, runs on apply and on every stack
    change apply-half), 1493 (``// mods at aura remove``, runs on removal and on every stack change
    remove-half: see :func:`specific_mods_blocks`), 1553 (``// mods at aura apply or remove``).
    """
    if 1457 <= line < 1493:
        return ("application", "refresh")
    if 1493 <= line < 1553:
        return ("removal", "refresh")
    if 1553 <= line < 1592:
        return ("application", "removal", "refresh")
    raise FailClosed(f"HandleAuraSpecificMods site at line {line} outside the pinned blocks")


def stack_change_sequence(effects: list[dict[str, Any]], amount_changed: dict[int, bool] | None = None
                          ) -> list[str]:
    """Ordered callbacks of one ``Aura::SetStackAmount`` (every refresh via ModStackAmount and every
    stack change).  ``effects``: ``[{"index": i, "hooks": [(list_name, mode_mask), ...]}]``.

    Mirrors: SpellAuras.cpp:1056-1076 (SpecificMods(false,true) for all applications, then per effect
    ChangeAmount(..., onStackOrReapply=true), then SpecificMods(true,true)); ChangeAmount
    SpellAuraEffects.cpp:1103-1128 (all remove halves, then all apply halves, per effect);
    HandleEffect SpellAuraEffects.cpp:1156-1177 (On* script, default handler, After* script).
    """
    amount_changed = amount_changed or {}
    seq = [f"specific_mods(remove-half):{b}" for b in specific_mods_blocks(False, True)]
    for eff in sorted(effects, key=lambda e: e["index"]):
        changed = amount_changed.get(eff["index"], False)
        for apply in (False, True):
            prefix = "Apply" if apply else "Remove"
            for lst in (f"OnEffect{prefix}", "default_handler", f"AfterEffect{prefix}"):
                if lst == "default_handler":
                    seq.append(f"eff{eff['index']}:{'apply' if apply else 'remove'}:default_handler")
                    continue
                for name, mode in eff.get("hooks", []):
                    if name == lst and apply_remove_hook_fires(mode, "stack-or-refresh", changed):
                        seq.append(f"eff{eff['index']}:{lst}")
    seq += [f"specific_mods(apply-half):{b}" for b in specific_mods_blocks(True, True)]
    return seq


def linked_actions(links: dict[tuple[int, int], list[int]], spell: int, event: str,
                   remove_mode: str = "AURA_REMOVE_BY_DEFAULT", caster_present: bool = True,
                   parent_stacks: int | None = None) -> list[dict[str, Any]]:
    """Lifecycle actions ``spell_linked_spell`` attaches to aura ``spell`` for ``event``.

    ``links`` is the loaded map (after ``LoadSpellLinked`` normalisation: negative trigger => type
    REMOVE) keyed ``(type, trigger)``.  ``event``: ``apply`` | ``remove`` | ``stack-change``.

    Mirrors: SpellAuras.cpp:1401-1455 (Aura::HandleAuraSpecificMods, spell_linked_spell block).
    """
    if remove_mode not in REMOVE_MODES and remove_mode != "AURA_REMOVE_NONE":
        raise FailClosed(f"unknown remove mode {remove_mode!r}")
    out: list[dict[str, Any]] = []
    if event == "apply":
        for x in links.get((LINK_AURA, spell), []):
            if x < 0:
                out.append({"action": "apply-immunity", "spell": -x, "line": 1411})
            elif caster_present:
                out.append({"action": "caster-add-aura", "spell": x, "own_duration": True, "line": 1413})
            else:
                out.append({"action": "skipped-no-caster", "spell": x, "line": 1412})
    elif event == "remove":
        for x in links.get((LINK_REMOVE, spell), []):
            if x < 0:
                out.append({"action": "target-remove-auras-due-to-spell", "spell": -x, "any_caster": True, "line": 1425})
            elif remove_mode != "AURA_REMOVE_BY_DEATH":
                out.append({"action": "target-cast", "spell": x, "original_caster": "aura caster", "line": 1427})
            else:
                out.append({"action": "suppressed-by-death", "spell": x, "line": 1426})
        for x in links.get((LINK_AURA, spell), []):
            if x < 0:
                out.append({"action": "remove-immunity", "spell": -x, "line": 1437})
            else:
                out.append({"action": "target-remove-aura", "spell": x, "same_caster_only": True,
                            "remove_mode": remove_mode, "line": 1439})
    elif event == "stack-change":
        for x in links.get((LINK_AURA, spell), []):
            if x > 0:
                out.append({"action": "sync-stack-amount", "spell": x, "to": parent_stacks, "same_caster_only": True,
                            "line": 1451})
    else:
        raise FailClosed(f"unknown linked event {event!r}")
    return out


def group_members(rows: list[tuple[int, int]]) -> dict[int, set[int]]:
    """spell_group (id, spell_id) rows -> group -> member spells (nested groups expanded).

    Mirrors: SpellMgr.cpp:1266-1340 LoadSpellGroups (DB range filter, negative = nested group,
    GetSetOfSpellsInSpellGroup SpellMgr.cpp:~390). Rank filtering (rank > 1 dropped) is applied by
    the caller, which has the catalog.
    """
    raw: dict[int, list[int]] = defaultdict(list)
    for gid, sid in rows:
        if 1000 >= gid >= 5:     # group_id <= SPELL_GROUP_DB_RANGE_MIN && group_id >= SPELL_GROUP_CORE_RANGE_MAX
            continue
        raw[gid].append(sid)
    out: dict[int, set[int]] = {}

    def expand(g: int, used: set[int]) -> set[int]:
        if g in used:
            return set()
        used.add(g)
        found: set[int] = set()
        for s in raw.get(g, []):
            if s < 0:
                if -s in raw:
                    found |= expand(-s, used)
            else:
                found.add(s)
        return found
    for g in raw:
        out[g] = expand(g, set())
    return out


def group_stack_rule(groups_of: dict[int, set[int]], members: dict[int, set[int]], rules: dict[int, int],
                     nested: dict[int, set[int]], s1: int, s2: int) -> str:
    """Mirrors: SpellMgr.cpp:447-492 CheckSpellGroupStackRules (first-rank ids passed by the caller).

    ``nested[g]`` = groups referenced negatively inside ``g``; a common group whose nested subgroup
    contains both spells is skipped.  First common group (std::set order) with a nonzero rule wins.
    """
    common = []
    for g in sorted(groups_of.get(s1, set())):
        if s2 in members.get(g, set()):
            if any(s1 in members.get(n, set()) and s2 in members.get(n, set()) for n in nested.get(g, set())):
                continue
            common.append(g)
    rule = 0
    for g in common:
        rule = rules.get(g, rule)
        if rule:
            break
    return SPELL_GROUP_STACK_RULES[rule]


# ---------------------------------------------------------------------------
# the index over the loaded context
# ---------------------------------------------------------------------------

def _is_aura_effect(e) -> bool:
    return bool(e.is_aura)


class ExternalIndex:
    """Every external lifecycle touch, keyed by the touched spell.  Built once per context."""

    def __init__(self, ctx) -> None:
        from dummy_semantics.bindings import BindingMap
        from dummy_semantics.corrections import Corrections
        from dummy_semantics.hardcoded import HardcodedIndex
        from procs.definition import ProcEntryStore

        self.ctx = ctx
        b = ctx.bundle
        self.cat = b.catalog
        self.touches: dict[int, list[dict[str, Any]]] = defaultdict(list)
        self.bindings = BindingMap(b)
        self.corrections = Corrections(b)
        self.hardcoded = HardcodedIndex(b)
        self.proc_store = ProcEntryStore(b.catalog, b.proc_overlay)
        self.links = b.world.linked(b.catalog)
        self.remove_mode_handlers: list[dict[str, Any]] = []
        self.hook_rows: list[dict[str, Any]] = []   # one row per executing lifecycle hook binding
        self.cross_edges: list[dict[str, Any]] = []
        self.implied_aura_hooks = 0
        self._linked()
        self._spell_area()
        self._groups()
        self._proc()
        self._custom_attr()
        self._conditions()
        self._pet_auras()
        self._serverside()
        self._corrections()
        self._engine()
        self._scripts()

    # -- helpers -------------------------------------------------------------
    def _add(self, spell: int, t: dict[str, Any]) -> None:
        self.touches[int(spell)].append(t)

    def _info(self, spell: int):
        return self.cat.get(spell)

    def aura_mask(self, spell: int) -> int:
        info = self._info(spell)
        if info is None:
            return 0
        return sum(1 << e.index for e in info.effects if _is_aura_effect(e))

    # -- spell_linked_spell --------------------------------------------------
    def _linked(self) -> None:
        name = {LINK_CAST: "cast", LINK_HIT: "hit", LINK_AURA: "aura", LINK_REMOVE: "remove"}
        for (typ, trig), targets in sorted(self.links.items()):
            for x in targets:
                if typ == LINK_AURA:
                    if x < 0:
                        self._add(trig, touch("application", "spell_linked_spell", "augment",
                                              f"while applied, owner is immune to spell {-x} (lifted on removal)",
                                              "SpellAuras.cpp:1411,1437", "trinity-consumer", target=-x))
                        continue
                    self._add(trig, touch("application", "spell_linked_spell", "cascade",
                                          f"apply adds aura {x} from the same caster (caster must exist; {x} keeps its own duration)",
                                          "SpellAuras.cpp:1407-1415", "trinity-consumer", target=x))
                    self._add(trig, touch("removal", "spell_linked_spell", "cascade",
                                          f"removal removes aura {x} of the same caster with the same remove mode",
                                          "SpellAuras.cpp:1433-1440", "trinity-consumer", target=x))
                    self._add(trig, touch("stacks", "spell_linked_spell", "cascade",
                                          f"stack change / refresh sets aura {x}'s stacks to this aura's stacks",
                                          "SpellAuras.cpp:1446-1453", "trinity-consumer", target=x))
                    self._add(x, touch("application", "spell_linked_spell", "gate",
                                       f"applied by caster->AddAura when aura {trig} is applied", "SpellAuras.cpp:1413",
                                       "trinity-consumer", source=trig))
                    self._add(x, touch("removal", "spell_linked_spell", "augment",
                                       f"removed (same caster, parent's remove mode) when aura {trig} is removed",
                                       "SpellAuras.cpp:1439", "trinity-consumer", source=trig))
                    self._add(x, touch("stacks", "spell_linked_spell", "replace",
                                       f"stack amount forced to aura {trig}'s on its stack change/refresh",
                                       "SpellAuras.cpp:1451", "trinity-consumer", source=trig))
                elif typ == LINK_REMOVE:
                    if x < 0:
                        self._add(trig, touch("removal", "spell_linked_spell", "cascade",
                                              f"removal (any mode, incl. death) removes all auras {-x} on the target",
                                              "SpellAuras.cpp:1424-1425", "trinity-consumer", target=-x))
                        self._add(-x, touch("removal", "spell_linked_spell", "augment",
                                            f"removed (all casters) when aura {trig} is removed", "SpellAuras.cpp:1425",
                                            "trinity-consumer", source=trig))
                    else:
                        self._add(trig, touch("removal", "spell_linked_spell", "cascade",
                                              f"removal casts {x} on the target unless remove mode is DEATH",
                                              "SpellAuras.cpp:1426-1429", "trinity-consumer", target=x,
                                              remove_mode_conditional=["AURA_REMOVE_BY_DEATH"]))
                        self._add(x, touch("application", "spell_linked_spell", "gate",
                                           f"cast when aura {trig} is removed by anything but death",
                                           "SpellAuras.cpp:1426-1429", "trinity-consumer", source=trig))
                else:  # cast / hit links: not bound to aura lifecycle of trig, but they apply/remove x
                    coords = "Spell.cpp:3949-3962" if typ == LINK_CAST else "Spell.cpp:3353-3364"
                    if x < 0:
                        self._add(-x, touch("removal", "spell_linked_spell", "augment",
                                            f"removed when spell {trig} is {name[typ]} ({'caster' if typ == LINK_CAST else 'hit unit'})",
                                            coords, "trinity-consumer", source=trig))
                    else:
                        self._add(x, touch("application", "spell_linked_spell", "gate",
                                           f"cast (triggered) on spell {trig} {name[typ]}", coords, "trinity-consumer",
                                           source=trig))

    # -- spell_area ----------------------------------------------------------
    def _spell_area(self) -> None:
        for r in self.ctx.bundle.world.table("spell_area").dicts():
            spell, flags, aura_spell = int(r["spell"]), int(r["flags"]), int(r["aura_spell"])
            if not self.cat.exists(spell):
                continue
            ctx_desc = f"area {r['area']} quest_start {r['quest_start']} quest_end {r['quest_end']} aura_spell {aura_spell}"
            if flags & SPELL_AREA_FLAG_AUTOCAST:
                self._add(spell, touch("application", "spell_area", "gate",
                                       f"auto-cast when the player meets spell_area requirements ({ctx_desc})",
                                       "Player.cpp:26483-26511,16053; SpellAuras.cpp:1392-1397", "trinity-consumer"))
            if flags & SPELL_AREA_FLAG_AUTOREMOVE:
                self._add(spell, touch("removal", "spell_area", "augment",
                                       f"auto-removed when spell_area requirements stop holding ({ctx_desc})",
                                       "Player.cpp:16051; SpellAuras.cpp:1389-1390", "trinity-consumer"))
            if aura_spell and self.cat.exists(abs(aura_spell)):
                a = abs(aura_spell)
                self._add(a, touch("application", "spell_area", "cascade",
                                   f"apply/remove (and every stack change) re-evaluates spell_area spell {spell}",
                                   "SpellAuras.cpp:1380-1398; SpellMgr.cpp:2486", "trinity-consumer", target=spell))
                self._add(a, touch("removal", "spell_area", "cascade",
                                   f"removal may auto-remove spell {spell} (flags {flags})",
                                   "SpellAuras.cpp:1389-1390", "trinity-consumer", target=spell))

    # -- spell_group / stack rules -----------------------------------------
    def _groups(self) -> None:
        w = self.ctx.bundle.world
        rows = [(int(r["id"]), int(r["spell_id"])) for r in w.table("spell_group").dicts()]
        raw_members = group_members(rows)
        # LoadSpellGroups: drop missing spells and rank > 1
        members = {g: {s for s in ss if self.cat.exists(s) and self.cat.first_rank(s) == s}
                   for g, ss in raw_members.items()}
        self.group_members = members
        self.group_nested = {g: {-s for gid, s in rows if gid == g and s < 0} for g in members}
        self.group_rules = {int(r["group_id"]): int(r["stack_rule"]) for r in w.table("spell_group_stack_rules").dicts()
                            if int(r["stack_rule"]) < len(SPELL_GROUP_STACK_RULES) and members.get(int(r["group_id"]))}
        self.groups_of: dict[int, set[int]] = defaultdict(set)
        for g, ss in members.items():
            for s in ss:
                self.groups_of[s].add(g)
        for s, gs in sorted(self.groups_of.items()):
            ruled = sorted(g for g in gs if self.group_rules.get(g))
            if not ruled:
                continue
            rules = {g: SPELL_GROUP_STACK_RULES[self.group_rules[g]] for g in ruled}
            # ranks > 1 inherit through GetFirstRankSpell
            chain = [s]
            nxt = self.cat.next_rank(s)
            while nxt is not None:
                chain.append(nxt)
                nxt = self.cat.next_rank(nxt)
            for sp in chain:
                self._add(sp, touch("identity", "spell_group", "replace",
                                    f"stacking with other members of groups {rules} decided by the group rule "
                                    f"(applied in Aura::CanStackWith before per-spell rules)",
                                    "SpellAuras.cpp:1674-1690; SpellMgr.cpp:447-492", "trinity-consumer",
                                    groups=rules))

    # -- spell_proc ----------------------------------------------------------
    def _proc(self) -> None:
        for (spell, diff), entry in sorted(self.proc_store.db.items()):
            if diff != 0:
                continue
            info = self._info(spell)
            if info is None:
                continue
            self._add(spell, touch("proc", "spell_proc", "replace",
                                   "proc eligibility/chance/cooldown from a server-authored proc entry "
                                   "(DB2 defaults fill zero fields)", "SpellMgr.cpp:1497-1560", "trinity-consumer"))
            if entry.charges != info.proc_charges:
                self._add(spell, touch("charges", "spell_proc", "replace",
                                       f"initial/max proc charges {entry.charges} (DB2 ProcCharges {info.proc_charges})",
                                       "SpellAuras.cpp:1004-1015 CalcMaxCharges", "trinity-consumer",
                                       value=entry.charges, db2=info.proc_charges))
            if entry.attributes_mask & PROC_ATTR_USE_STACKS_FOR_CHARGES:
                self._add(spell, touch("stacks", "spell_proc", "replace",
                                       "a consumed proc removes one stack (ModStackAmount(-1)) instead of a charge",
                                       "SpellAuras.cpp:1813,1820-1826", "trinity-consumer"))
                self._add(spell, touch("charges", "spell_proc", "replace",
                                       "charges are not consumed on proc (stacks are)", "SpellAuras.cpp:1813",
                                       "trinity-consumer"))

    # -- spell_custom_attr ---------------------------------------------------
    def _custom_attr(self) -> None:
        for spell, bits in sorted(self.ctx.bundle.proc_overlay.custom_attributes.items()):
            for bit, (surface, name, fact, coords) in CU_SURFACES.items():
                if bits & bit and self.cat.exists(spell):
                    self._add(spell, touch(surface, "spell_custom_attr", "replace", f"{name}: {fact}", coords,
                                           "trinity-consumer"))
        # LoadSpellInfoCustomAttributes family/id-derived AURA_CC (SpellMgr.cpp:3249-3263)
        # is attributed in _engine via engine_family_flag.

    # -- conditions ----------------------------------------------------------
    def _conditions(self) -> None:
        by_src = self.ctx.bundle.world.conditions_by_source()
        for spell, rows in sorted(by_src.get(CONDITION_SOURCE_SPELL_IMPLICIT_TARGET, {}).items()):
            amask = self.aura_mask(spell)
            groups = {int(r["SourceGroup"]) for r in rows}
            hit = sorted(g for g in groups if g & amask)
            if hit:
                self._add(spell, touch("recipients", "conditions", "gate",
                                       f"implicit-target conditions filter recipients of aura effects (mask {hit}); "
                                       "area-aura recipient maps re-check them every map update",
                                       "Spell target selection; SpellAuras.cpp:2579-2622,2726", "trinity-consumer",
                                       effect_masks=hit))
        for spell, rows in sorted(by_src.get(CONDITION_SOURCE_SPELL, {}).items()):
            if self.aura_mask(spell):
                self._add(spell, touch("application", "conditions", "gate",
                                       f"cast gated by {len(rows)} spell conditions (evaluated at CheckCast)",
                                       "Spell::CheckCast (ConditionMgr source 17)", "trinity-consumer"))
        for spell, rows in sorted(by_src.get(CONDITION_SOURCE_SPELL_PROC, {}).items()):
            if self.aura_mask(spell):
                self._add(spell, touch("proc", "conditions", "gate", "proc gated by spell-proc conditions",
                                       "SpellAuras.cpp:1903", "trinity-consumer"))

    # -- spell_pet_auras -----------------------------------------------------
    def _pet_auras(self) -> None:
        for r in self.ctx.bundle.world.table("spell_pet_auras").dicts():
            spell, aura = int(r["spell"]), int(r["aura"])
            self._add(spell, touch("application", "spell_pet_auras", "cascade",
                                   f"owner aura applies/removes pet aura {aura} (pet entry {r['pet']})",
                                   "SpellAuraEffects.cpp:4873; SpellEffects.cpp:582; Player.cpp:3201", "trinity-consumer",
                                   target=aura))
            self._add(aura, touch("application", "spell_pet_auras", "gate",
                                  f"applied to the pet while owner has aura {spell}", "SpellAuraEffects.cpp:4873",
                                  "trinity-consumer", source=spell))

    # -- serverside spells ---------------------------------------------------
    def _serverside(self) -> None:
        from procs.enums import is_aura_effect
        self.serverside_providers: dict[int, dict[str, Any]] = {}
        self.serverside_rejected: list[int] = []
        for (sid, diff), row in sorted(self.ctx.bundle.world.serverside_spells().items()):
            if diff != 0:
                continue
            if (sid, diff) in self.cat.keys:
                # "Serverside spell ... is already loaded from file. Overriding existing spells is not allowed."
                self.serverside_rejected.append(sid)
                continue
            auras = [e for e in row["_effects"] if is_aura_effect(int(e["Effect"]), int(e["EffectAura"]))]
            if not auras:
                continue
            self.serverside_providers[sid] = {
                "name": row.get("SpellName"), "duration_index": int(row["DurationIndex"]),
                "stack_amount": int(row["StackAmount"]), "proc_charges": int(row["ProcCharges"]),
                "aura_effects": [(int(e["EffectIndex"]), int(e["Effect"]), int(e["EffectAura"])) for e in auras]}
            self._add(sid, touch("application", "serverside_spell", "replace",
                                 "whole spell (incl. duration/stack/charge fields) exists only in the world DB",
                                 "SpellMgr.cpp:2735", "world-db-fact"))

    # -- LoadSpellInfoCorrections -------------------------------------------
    def _correction_surfaces(self, w: dict[str, Any]) -> tuple[str, ...]:
        m = w["member"]
        if m.startswith("Attributes") and m != "AttributesCu":
            out: list[str] = []
            for pat, surfaces in ATTR_TOKEN_SURFACES:
                if re.search(pat, w["value"]):
                    out.extend(s for s in surfaces if s not in out)
            return tuple(out)
        return CORRECTION_MEMBER_SURFACES.get(m, ())

    def _corrections(self) -> None:
        for spell, fixes in sorted(self.corrections.by_spell.items()):
            for fx in fixes:
                for w in fx["writes"]:
                    kind = "spellinfo_correction" if w["patch_shape"] == "data-patch" else "spellinfo_correction_code"
                    mode = "replace" if w["op"] == "=" else "augment"
                    for surface in self._correction_surfaces(w):
                        eff = f" {w['effect']}" if w.get("effect") else ""
                        self._add(spell, touch(surface, kind, mode,
                                               f"server overrides {w['member']}{eff} {w['op']} {w['value']}",
                                               f"SpellMgr.cpp:{w['line']}", "trinity-consumer",
                                               member=w["member"], correction_class=w["class"]))

    # -- engine hardcoded spell ids + family-flag branches -------------------
    def _engine(self) -> None:
        for spell, sites in sorted(self.hardcoded.by_spell().items()):
            for s in sites:
                surfaces = ENGINE_FUNCTION_SURFACES.get(s.function.split("#")[0])
                if s.function == "Aura::HandleAuraSpecificMods":
                    surfaces = specific_mods_site_surfaces(s.line)
                if not surfaces:
                    continue
                for surface in surfaces:
                    self._add(spell, touch(surface, "engine_spell_id", "augment",
                                           f"engine branch on SpellID in {s.function}",
                                           f"{s.file.split('/')[-1]}:{s.line}", "trinity-consumer", function=s.function))

    def family_branches(self, info) -> list[dict[str, Any]]:
        """Engine branches keyed on SpellFamilyName + SpellFamilyFlags that touch the lifecycle.

        Mirrors: SpellAuras.cpp:1476-1488 (DRUID flags[0] & 0x10 apply: caster aura 64760 casts 64801),
        SpellAuras.cpp:1509-1546 (PRIEST flags[0] & 0x1 remove with AURA_REMOVE_BY_ENEMY_SPELL:
        caster's ranked 47535 casts 47755), SpellMgr.cpp:3249-3263 (WARRIOR flags[0] & 0x20000,
        DRUID flags[0] & 0x8, id 5729 -> SPELL_ATTR0_CU_AURA_CC -> exempt from
        RemoveAurasByShapeShift Unit.cpp:4339).
        """
        out = []
        f0 = info.family_flags & 0xFFFFFFFF
        if info.family == SPELLFAMILY_DRUID and f0 & 0x10:
            out.append(touch("application", "engine_family_flag", "augment",
                             "apply: caster with aura 64760 casts 64801 on the target", "SpellAuras.cpp:1476-1488",
                             "trinity-consumer"))
        if info.family == SPELLFAMILY_PRIEST and f0 & 0x1:
            out.append(touch("removal", "engine_family_flag", "augment",
                             "removal by ENEMY_SPELL (dispel/absorb destroy): caster with ranked 47535 casts 47755",
                             "SpellAuras.cpp:1509-1546", "trinity-consumer",
                             remove_mode_conditional=["AURA_REMOVE_BY_ENEMY_SPELL"]))
        if (info.family == SPELLFAMILY_WARRIOR and f0 & 0x20000) or (info.family == SPELLFAMILY_DRUID and f0 & 0x8) \
                or info.id == 5729:
            if any(e.is_aura for e in info.effects):
                out.append(touch("removal", "engine_family_flag", "augment",
                                 "derived SPELL_ATTR0_CU_AURA_CC: exempt from shapeshift mechanic removal",
                                 "SpellMgr.cpp:3249-3263; Unit.cpp:4339", "trinity-consumer"))
        return out

    # -- scripts -------------------------------------------------------------
    def _hook_mode(self, hook: dict[str, Any]) -> int | None:
        args = hook.get("args") or []
        if hook["list"] not in ("OnEffectApply", "AfterEffectApply", "OnEffectRemove", "AfterEffectRemove") or not args:
            return None
        return parse_handle_mode(args[-1].replace("…", ""))

    @staticmethod
    def _is_self(recv: str) -> bool:
        return bool(_SELF_RECV.match(recv.strip()))

    def _actions(self, spell: int, facts: dict[str, Any], kind: str) -> list[dict[str, Any]]:
        """Lifecycle-mutating calls in one merged handler body."""
        calls = facts.get("calls", []) if facts else []
        aura_ids = sorted({v for c in calls if c["callee"] in ("GetAura", "GetAuraEffect", "GetOwnedAura")
                           for v in c.get("ints", []) if v >= 100})
        out = []
        for c in calls:
            callee = c["callee"]
            recv = c.get("recv", "") or ""
            ints = [v for v in c.get("ints", []) if v >= 100]
            if callee in APPLY_CALLS:
                for x in ints:
                    if x != spell:
                        out.append({"callee": callee, "surface": "application", "target": "other", "spells": [x],
                                    "line": c.get("line"), "evidence": "script-consumer"})
                continue
            surface = ACTION_SURFACES.get(callee)
            if surface is None:
                continue
            if callee in ("RemoveAura", "RemoveAurasDueToSpell", "RemoveOwnedAura", "RemoveAuraFromStack"):
                args = " ".join(c.get("args", []))
                if ints:
                    selfish = spell in ints
                    others = [x for x in ints if x != spell]
                    if selfish:
                        out.append({"callee": callee, "surface": surface, "target": "self", "spells": [spell],
                                    "line": c.get("line"), "evidence": "script-consumer"})
                    if others:
                        out.append({"callee": callee, "surface": surface, "target": "other", "spells": others,
                                    "line": c.get("line"), "evidence": "script-consumer"})
                elif re.search(r"GetId\(\)|GetSpellInfo\(\)->Id|GetAura\(\)|m_scriptSpellId", args):
                    out.append({"callee": callee, "surface": surface, "target": "self", "spells": [spell],
                                "line": c.get("line"), "evidence": "script-consumer"})
                else:
                    out.append({"callee": callee, "surface": surface, "target": "unresolved", "spells": [],
                                "line": c.get("line"), "evidence": "structural-inference"})
                continue
            if kind == "AuraScript" and self._is_self(recv) or kind == "SpellScript" and recv.startswith("GetHitAura()"):
                out.append({"callee": callee, "surface": surface, "target": "self", "spells": [spell],
                            "line": c.get("line"), "evidence": "script-consumer"})
            elif callee == "Remove" and not aura_ids:
                continue  # receiver is not an aura we can name (area triggers, motion master, containers ...)
            else:
                out.append({"callee": callee, "surface": surface, "target": "other",
                            "spells": [x for x in aura_ids if x != spell], "line": c.get("line"),
                            "evidence": "structural-inference"})
        return out

    def _scripts(self) -> None:
        from dummy_semantics.hooks import BY_LIST
        idx = self.ctx.bundle.index
        for spell, blist in sorted(self.bindings.by_spell.items()):
            info = self._info(spell)
            if info is None:
                continue
            amask = self.aura_mask(spell)
            for bnd in blist:
                if not bnd.resolved:
                    self._add(spell, touch("any", "script-unresolved", "augment",
                                           f"ScriptName {bnd.script_name} binds no resolvable script class",
                                           "ObjectMgr::LoadSpellScriptNames", "unresolved", script=bnd.script_name))
                    continue
                res = idx.resolve_script_name(bnd.script_name)
                for sc in res["classes"]:
                    for hook in sc.hooks:
                        hb = self.bindings._hook_binding(sc, hook, info)
                        if not hb.executes and hb.note and hb.note.startswith("unresolved"):
                            hb = self._implied_aura_binding(sc, hook, hb, info)
                            if hb is None:
                                for surface in AURA_HOOK_SURFACES.get(hook["list"], ("application",)):
                                    self._add(spell, touch(surface, "script-unresolved", "augment",
                                                           f"{hook['list']} registration tokens {hook.get('args')} unresolved",
                                                           f"{sc.file.split('/')[-1]}:{hook['line']}", "unresolved",
                                                           script=bnd.script_name))
                                continue
                        if not hb.executes:
                            continue
                        self._script_hook(spell, info, amask, bnd.script_name, sc, hook, hb)

    def _implied_aura_binding(self, sc, hook, hb, info):
        """Two-argument absorb/split registration macros imply the aura type.

        Mirrors: SpellScript.h:1744-1784 (``AuraEffectCalcAbsorbFn(F, I)`` ->
        ``EffectCalcDamageAndHealingHandler(&F, I, SPELL_AURA_SCHOOL_ABSORB)`` etc.) and
        AuraScript::EffectBase::CheckEffect SpellScript.cpp:943-953.  The Dummy-pass BindingMap leaves
        these as "unresolved hook tokens"; resolved here and counted in ``implied_aura_hooks``.
        """
        from dataclasses import replace
        from dummy_semantics.bindings import affected_mask, parse_eff_index
        from procs.enums import aura as aura_value
        name = IMPLIED_AURA_MACROS.get(hook.get("fn", ""))
        args = hook.get("args") or []
        if name is None or len(args) != 1:
            return None
        eff_index = parse_eff_index(args[0])
        if eff_index is None:
            return None
        want = aura_value(name)

        def check(i: int) -> bool:
            e = info.effect(i)
            return e is not None and bool(e.aura) and e.aura == want
        mask = affected_mask(info, eff_index, check)
        self.implied_aura_hooks += 1
        return replace(hb, eff_index=eff_index, eff_value=want, affected_mask=mask,
                       affected_effects=[i for i in range(32) if mask & (1 << i)], executes=bool(mask),
                       note=f"aura type implied by {hook.get('fn')}")

    def _script_hook(self, spell, info, amask, script, sc, hook, hb) -> None:
        kind = sc.kind
        lst = hb.list
        facts = hb.facts or {}
        coords = f"{sc.file.split('/')[-1]}:{hook['line']}"
        prevents = any(c["cat"] == "prevent" for c in facts.get("calls", []))
        actions = self._actions(spell, facts, kind)
        tokens = set(facts.get("tokens", []))
        modes_read = sorted(t for t in tokens if t in REMOVE_MODES)
        reads_mode = modes_read or any(c["callee"] == "GetRemoveMode" for c in facts.get("calls", []))
        base = {"script": script, "class": sc.name, "hook": lst, "handler": hb.handler}
        surfaces: list[tuple[str, str, str]] = []   # (surface, mode, fact)
        if kind == "AuraScript":
            for s in AURA_HOOK_SURFACES.get(lst, ()):
                if REPLACEABLE.get(lst) == s and prevents:
                    surfaces.append((s, "replace", f"{lst} handler {hb.handler} prevents the default action"))
                elif VALUE_HOOKS.get(lst) == s:
                    surfaces.append((s, "replace", f"{lst} handler {hb.handler} computes the value"))
                else:
                    surfaces.append((s, "augment", f"{lst} handler {hb.handler} runs"))
            mode_mask = None
            try:
                mode_mask = self._hook_mode(hook)
            except FailClosed:
                surfaces.append(("refresh", "augment", f"{lst} registered with unparsed mode {hook.get('args')}"))
            if mode_mask is not None:
                base["handle_mode"] = mode_mask
                if mode_mask & REAPPLY:
                    surfaces.append(("refresh", "augment",
                                     f"{lst} handler {hb.handler} also runs on every refresh / stack change (REAPPLY mode)"))
                    surfaces.append(("stacks", "augment", f"{lst} handler {hb.handler} runs on stack change (REAPPLY)"))
                if mode_mask & CHANGE_AMOUNT:
                    surfaces.append(("amount", "augment", f"{lst} handler {hb.handler} runs on amount change"))
        else:  # SpellScript: lifecycle-relevant only when bound to an aura effect or touching the hit aura
            eff_hit = hb.affected_mask is not None and bool(hb.affected_mask & amask)
            if lst in ("OnEffectHitTarget", "OnEffectHit", "OnEffectLaunchTarget", "OnEffectLaunch") and eff_hit:
                surfaces.append(("application", "replace" if prevents else "augment",
                                 f"{lst} on aura effect(s) {hb.affected_effects}" + (" prevents default (no aura)" if prevents else "")))
            elif lst in ("OnObjectAreaTargetSelect", "OnObjectTargetSelect") and eff_hit:
                surfaces.append(("recipients", "replace", f"{lst} filters initial recipients of aura effect(s) {hb.affected_effects}"))
            elif lst == "OnCheckCast" and amask:
                surfaces.append(("application", "gate", "OnCheckCast gates the cast"))
            callees = {c["callee"] for c in facts.get("calls", [])}
            if "PreventHitAura" in callees:
                surfaces.append(("application", "replace", f"{lst} calls PreventHitAura"))
        for a in actions:
            if a["target"] == "self":
                surfaces.append((a["surface"], "replace" if a["surface"] in ("duration", "stacks", "charges") else "augment",
                                 f"{lst} handler calls {a['callee']} on its own aura"))
        seen = set()
        for surface, mode, fact in surfaces:
            if (surface, mode, fact) in seen:
                continue
            seen.add((surface, mode, fact))
            self._add(spell, touch(surface, "aura-script" if kind == "AuraScript" else "spell-script", mode, fact,
                                   coords, "script-consumer", **base))
        if reads_mode and lst in ("OnEffectRemove", "AfterEffectRemove", "OnDispel", "AfterDispel", "OnEffectApply",
                                  "AfterEffectApply"):
            rec = dict(base, spell=spell, file=sc.file, line=hook["line"], modes=modes_read,
                       reads_get_remove_mode=any(c["callee"] == "GetRemoveMode" for c in facts.get("calls", [])),
                       actions=sorted({a["callee"] for a in actions}) + sorted(
                           {c["callee"] for c in facts.get("calls", []) if c["cat"] == "cast"}))
            self.remove_mode_handlers.append(rec)
            self._add(spell, touch("removal", "aura-script" if kind == "AuraScript" else "spell-script", "augment",
                                   f"{lst} behaviour depends on the remove mode {modes_read or '(GetRemoveMode read)'}",
                                   coords, "script-consumer", remove_mode_conditional=modes_read, **base))
        self.hook_rows.append(dict(base, spell=spell, kind=kind, prevents=prevents,
                                   surfaces=sorted({s for s, _, _ in surfaces}), handle_mode=base.get("handle_mode"),
                                   remove_mode_conditional=bool(reads_mode)))
        for a in actions:
            if a["target"] != "other":
                continue
            for x in a["spells"]:
                edge = {"target": x, "by_spell": spell, "script": script, "hook": lst, "callee": a["callee"],
                        "surface": a["surface"], "coords": f"{sc.file.split('/')[-1]}:{a['line']}",
                        "evidence": a["evidence"]}
                self.cross_edges.append(edge)
                mode = "gate" if a["surface"] == "application" else "augment"
                self._add(x, touch(a["surface"], "other-spell-script", mode,
                                   f"script {script} of spell {spell} ({lst}) calls {a['callee']} on this aura",
                                   edge["coords"], a["evidence"], source=spell, script=script))

    # -- query ---------------------------------------------------------------
    def surfaces(self, spell: int) -> dict[str, list[dict[str, Any]]]:
        out: dict[str, list[dict[str, Any]]] = defaultdict(list)
        ts = list(self.touches.get(spell, []))
        info = self._info(spell)
        if info is not None:
            ts += self.family_branches(info)
        for t in ts:
            if t["surface"] == "any":
                for s in SURFACES:
                    out[s].append(t)
            else:
                out[t["surface"]].append(t)
        return {k: sorted(v, key=_touch_key) for k, v in sorted(out.items())}

    def touched_spells(self) -> set[int]:
        out = set(self.touches)
        return out


def _touch_key(t: dict[str, Any]) -> tuple:
    return (t["kind"], t["mode"], t["fact"], ",".join(t["coords"]))


_INDEX: dict[int, ExternalIndex] = {}


def index(ctx) -> ExternalIndex:
    key = id(ctx)
    if key not in _INDEX:
        _INDEX[key] = ExternalIndex(ctx)
    return _INDEX[key]


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------

def external_surfaces(ctx, spell: int) -> dict[str, list[dict[str, Any]]]:
    """Per-spell externally touched lifecycle surfaces: ``{surface: [touch, ...]}``.

    Only touched surfaces appear.  Each touch: ``surface, kind, policy_source, mode, fact, coords,
    evidence`` (+ kind-specific keys).  Absence is not proof of absence (module docstring).
    For bulk use see :func:`surface_flags`.
    """
    return index(ctx).surfaces(spell)


def surface_flags(ctx) -> dict[int, dict[str, str]]:
    """Bulk form for census: spell -> {touched surface: policy source}, every touched spell."""
    idx = index(ctx)
    out: dict[int, dict[str, str]] = {}
    for spell in sorted(idx.touched_spells() | _family_spells(ctx)):
        surf = idx.surfaces(spell)
        if surf:
            out[spell] = {s: classify(s, ts) for s, ts in surf.items()}
    return out


def _family_spells(ctx) -> set[int]:
    idx = index(ctx)
    from .providers import provider_spells
    out = set()
    for s in provider_spells(ctx.data):
        info = idx._info(s)
        if info is not None and idx.family_branches(info):
            out.add(s)
    return out


def policy(ctx, spell: int) -> dict[str, str]:
    """Every surface -> policy source for ``spell`` (untouched surfaces get their baseline)."""
    surf = external_surfaces(ctx, spell)
    return {s: classify(s, surf.get(s, [])) for s in SURFACES}


def explain(ctx, spell: int) -> dict[str, Any]:
    """``aura_lifecycle.py overlays <spell>``: lifecycle facts from every external source."""
    from .providers import populations
    idx = index(ctx)
    info = idx._info(spell)
    if info is None and spell not in idx.serverside_providers:
        raise FailClosed(f"spell {spell} is neither in the DB2 snapshot nor a serverside spell")
    pops = populations(ctx)
    surf = idx.surfaces(spell)
    scripts = []
    for bnd in idx.bindings.by_spell.get(spell, []):
        rows = [r for r in idx.hook_rows if r["spell"] == spell and r["script"] == bnd.script_name]
        scripts.append({"script": bnd.script_name, "resolved": bnd.resolved,
                        "classes": [f"{c['name']} ({c['kind']}) {c['file'].split('/')[-1]}:{c['line']}" for c in bnd.classes],
                        "lifecycle_hooks": [{k: r[k] for k in ("hook", "handler", "surfaces", "prevents", "handle_mode",
                                                               "remove_mode_conditional")} for r in rows]})
    groups = {g: SPELL_GROUP_STACK_RULES[idx.group_rules.get(g, 0)] for g in sorted(idx.groups_of.get(
        idx.cat.first_rank(spell) if info else spell, set()))}
    return {
        "spell": spell, "name": ctx.name(spell) or (idx.serverside_providers.get(spell) or {}).get("name"),
        "build_skew": ctx.is_skew(spell),
        "populations": sorted(p for p, s in pops.items() if spell in s),
        "aura_effect_mask": idx.aura_mask(spell),
        "policy": {s: classify(s, surf.get(s, [])) for s in SURFACES},
        "surfaces": surf,
        "scripts": scripts,
        "spell_groups": groups,
        "serverside": idx.serverside_providers.get(spell),
        "linked_timelines": _linked_timelines(idx, spell),
        "coverage_note": "touches cover SOURCE_KINDS only; absence is not proof of absence",
    }


def _linked_timelines(idx: ExternalIndex, spell: int) -> dict[str, list[dict[str, Any]]]:
    out = {}
    for label, ev, mode in (("apply", "apply", "AURA_REMOVE_NONE"),
                            ("remove:EXPIRE", "remove", "AURA_REMOVE_BY_EXPIRE"),
                            ("remove:DEATH", "remove", "AURA_REMOVE_BY_DEATH"),
                            ("stack-change", "stack-change", "AURA_REMOVE_NONE")):
        acts = linked_actions(idx.links, spell, ev, mode)
        if acts:
            out[label] = acts
    return out


# ---------------------------------------------------------------------------
# census (external-policy corpus)
# ---------------------------------------------------------------------------

APPLY_REMOVE_HOOKS = ("OnEffectApply", "AfterEffectApply", "OnEffectRemove", "AfterEffectRemove")


def _mode_class(mask: int | None) -> str:
    if mask is None:
        return "n/a"
    bits = []
    if mask & REAL:
        bits.append("REAL")
    if mask & REAPPLY:
        bits.append("REAPPLY")
    if mask & CHANGE_AMOUNT:
        bits.append("CHANGE_AMOUNT")
    if mask & 0x02:
        bits.append("SEND_FOR_CLIENT")
    return "+".join(bits) or "NONE"


def class_skill_population(ctx) -> frozenset[int]:
    """Supplementary population ``player+class-skills`` (definition: :mod:`aura_lifecycle.providers`)."""
    from .providers import populations
    return populations(ctx, with_class_skills=True)["player+class-skills"]


def census(ctx, with_class_skills: bool = True) -> dict[str, Any]:
    """Counts per surface / source kind / policy class for each population, the AuraScript
    hook census by lifecycle surface, remove-mode-conditional handlers and per-provider rows."""
    from .providers import populations
    idx = index(ctx)
    pops = dict(populations(ctx))
    if with_class_skills:
        pops["player+class-skills"] = class_skill_population(ctx)
    flags_all = {}
    for spell in sorted(idx.touched_spells() | _family_spells(ctx)):
        surf = idx.surfaces(spell)
        if surf:
            flags_all[spell] = surf
    out: dict[str, Any] = {"populations": {}, "hook_census": {}, "remove_mode_handlers": {}}
    for pname, members in sorted(pops.items()):
        touched = {s: flags_all[s] for s in sorted(members) if s in flags_all}
        per_surface = {}
        for surface in SURFACES:
            hit = {s: v[surface] for s, v in touched.items() if surface in v}
            per_surface[surface] = {
                "touched_spells": len(hit),
                "by_policy": dict(sorted(Counter(classify(surface, ts) for ts in hit.values()).items())),
                "by_kind": dict(sorted(Counter(k for ts in hit.values() for k in {t["kind"] for t in ts}).items())),
                "by_mode": dict(sorted(Counter(m for ts in hit.values() for m in {t["mode"] for t in ts}).items())),
            }
        out["populations"][pname] = {
            "providers": len(members), "touched_any_surface": len(touched),
            "untouched": len(members) - len(touched),
            "touched_by_kind": dict(sorted(Counter(k for v in touched.values()
                                                  for k in {t["kind"] for ts in v.values() for t in ts}).items())),
            "surfaces": per_surface,
        }
        rows = [r for r in idx.hook_rows if r["spell"] in members]
        hooks = defaultdict(lambda: {"bindings": 0, "spells": set()})
        for r in rows:
            h = hooks[r["hook"]]
            h["bindings"] += 1
            h["spells"].add(r["spell"])
        by_surface = defaultdict(set)
        for r in rows:
            for s in r["surfaces"]:
                by_surface[s].add(r["spell"])
        out["hook_census"][pname] = {
            "executing_hook_bindings": len(rows),
            "spells_with_executing_hooks": len({r["spell"] for r in rows}),
            "by_hook": {k: {"bindings": v["bindings"], "spells": len(v["spells"]),
                            "lifecycle_surfaces": AURA_HOOK_SURFACES.get(k, ())}
                        for k, v in sorted(hooks.items())},
            "spells_by_surface": {k: len(v) for k, v in sorted(by_surface.items())},
            "apply_remove_handle_modes": dict(sorted(Counter(
                f"{r['hook']}:{_mode_class(r['handle_mode'])}" for r in rows if r["hook"] in APPLY_REMOVE_HOOKS).items())),
            "prevent_default_bindings": sum(1 for r in rows if r["prevents"]),
        }
        rm = [r for r in idx.remove_mode_handlers if r["spell"] in members]
        out["remove_mode_handlers"][pname] = {
            "handlers": len(rm), "spells": len({r["spell"] for r in rm}),
            "modes_compared": dict(sorted(Counter(m for r in rm for m in r["modes"]).items())),
        }
    # per-provider rows (player, player+class-skills, controlled): every touched provider
    rows_out = {}
    for pname in ("player", "player+class-skills", "controlled"):
        if pname not in pops:
            continue
        rows_out[pname] = [
            {"spell": s, "name": ctx.name(s), "build_skew": ctx.is_skew(s),
             "policy": {k: classify(k, v) for k, v in flags_all[s].items()},
             "kinds": {k: sorted({t["kind"] for t in v}) for k, v in flags_all[s].items()}}
            for s in sorted(pops[pname]) if s in flags_all]
    out["providers"] = rows_out
    out["providers_player_ids"] = sorted(pops["player"])
    # all population: compact map (J joins on it)
    out["all_touched"] = {str(s): sorted(v) for s, v in flags_all.items() if s in pops["all"]}
    out["remove_mode_handler_rows"] = sorted(
        ({k: r[k] for k in ("spell", "script", "class", "hook", "handler", "modes", "reads_get_remove_mode", "actions",
                            "line")} | {"file": r["file"].split("/")[-1], "player": r["spell"] in pops["player"]}
         for r in idx.remove_mode_handlers), key=lambda r: (r["spell"], r["script"], r["line"]))
    out["reapply_hook_rows"] = sorted(
        ({k: r[k] for k in ("spell", "script", "class", "hook", "handler")} |
         {"handle_mode": _mode_class(r["handle_mode"]), "player": r["spell"] in pops["player"],
          "player_class_skills": r["spell"] in pops.get("player+class-skills", frozenset())}
         for r in idx.hook_rows if r["handle_mode"] is not None and r["handle_mode"] & REAPPLY),
        key=lambda r: (r["spell"], r["script"], r["hook"]))
    out["serverside_aura_providers"] = {
        "count": len(idx.serverside_providers),
        "referenced_by_player_scripts_or_links": sorted(
            {e["target"] for e in idx.cross_edges if e["target"] in idx.serverside_providers
             and e["by_spell"] in pops["player"]} |
            {x for (typ, trig), xs in idx.links.items() if trig in pops["player"] for x in xs
             if abs(x) in idx.serverside_providers}),
    }
    out["cross_spell_script_edges"] = {
        "edges": len(idx.cross_edges),
        "by_surface": dict(sorted(Counter(e["surface"] for e in idx.cross_edges).items())),
        "by_evidence": dict(sorted(Counter(e["evidence"] for e in idx.cross_edges).items())),
        "targets_in_player": len({e["target"] for e in idx.cross_edges if e["target"] in pops["player"]}),
    }
    out["never_executing_hooks_on_providers"] = _dead_hooks(idx, pops)
    return out


def _dead_hooks(idx: ExternalIndex, pops) -> dict[str, Any]:
    """Registered hooks whose effect mask is 0 on the current snapshot (never execute)."""
    out = {}
    for pname in ("player", "player+class-skills", "all"):
        members = pops.get(pname)
        if members is None:
            continue
        dead = []
        for spell, blist in idx.bindings.by_spell.items():
            if spell not in members:
                continue
            for b in blist:
                for h in b.hooks:
                    if h.affected_mask == 0 or (not h.executes and h.note):
                        dead.append((spell, b.script_name, h.list, h.note or "mask 0"))
        out[pname] = {"hooks": len(dead), "spells": len({d[0] for d in dead}),
                      "unresolved_token_hooks": sum(1 for d in dead if d[3].startswith("unresolved"))}
    return out


# ---------------------------------------------------------------------------
# timelines, rules, falsification, unknowns, experiments, defects
# ---------------------------------------------------------------------------

def _dur(ctx, spell: int) -> int | None:
    """Unmodified max duration of a spell (no caster mods / haste / DR).

    Mirrors: SpellInfo::GetMaxDuration SpellInfo.cpp:3993-3998 (no DurationEntry -> passive ? -1 : 0;
    -1 stays -1; otherwise abs(MaxDuration)).  Everything else is track B's.
    """
    info = ctx.catalog.get(spell)
    misc = ctx.data.row("SpellMisc", spell)
    if info is None or misc is None:
        return None
    d = ctx.data.duration(misc["DurationIndex"]) if misc["DurationIndex"] else None
    if d is None:
        return -1 if info.is_passive else 0
    return -1 if int(d["MaxDuration"]) == -1 else abs(int(d["MaxDuration"]))


def timelines(ctx) -> list[dict[str, Any]]:
    """Millisecond timelines for the discriminating external-policy questions (real witnesses)."""
    idx = index(ctx)
    out = []
    # T1 linked aura: Beacon of Light 53563 -> Light's Beacon 53651
    parent, child = 53563, 53651
    if (LINK_AURA, parent) in idx.links:
        dp, dc = _dur(ctx, parent), _dur(ctx, child)
        ev = [{"t_ms": 0, "event": f"aura {parent} applied (caster present)",
               "order": ["Unit::_ApplyAura -> HandleAuraSpecificMods(apply) Unit.cpp:3576",
                         "effect handlers + OnEffectApply/AfterEffectApply scripts Unit.cpp:3579-3587"],
               "actions": linked_actions(idx.links, parent, "apply"),
               "state_after": {str(parent): {"max_duration_ms": dp}, str(child): {
                   "max_duration_ms": dc, "note": "own duration via AddAura (-1 = permanent)"}}}]
        if dp is not None and dc is not None and dc > 0 and (dp == -1 or dc < dp):
            ev.append({"t_ms": dc, "event": f"aura {child} expires on its own duration", "actions": [],
                       "state_after": {str(parent): {"remaining_ms": dp if dp == -1 else dp - dc},
                                       str(child): "removed (EXPIRE)"}})
        first = "AURA_REMOVE_BY_EXPIRE" if dp and dp > 0 else "AURA_REMOVE_BY_CANCEL"
        for mode in (first, "AURA_REMOVE_BY_DEATH"):
            ev.append({"t_ms": dp if mode.endswith("EXPIRE") else ("t_cancel" if mode.endswith("CANCEL") else "t_death"),
                       "event": f"aura {parent} removed with {mode}",
                       "order": ["_UnapplyAura: SetRemoveMode Unit.cpp:3610", "effect remove handlers Unit.cpp:3659-3661",
                                 "HandleAuraSpecificMods(remove) Unit.cpp:3680"],
                       "actions": linked_actions(idx.links, parent, "remove", mode)})
        out.append({"id": "AL-T-H-01", "question": "Does a spell_linked_spell aura (LINK_AURA) share its parent's lifetime?",
                    "witness": {"parent": parent, "child": child, "parent_name": ctx.name(parent),
                                "child_name": ctx.name(child), "parent_duration_ms": dp, "child_duration_ms": dc},
                    "events": ev,
                    "rules_out": ["linked aura inherits parent duration (it is created by AddAura with its own duration)",
                                  "linked aura removal ignores remove mode (parent's mode is forwarded)"],
                    "evidence": ["world-db-fact", "trinity-consumer"]})
    # T2 refresh of Rejuvenation 774 with the Abundance AuraScript
    spell = 774
    rows = [r for r in idx.hook_rows if r["spell"] == spell and r["hook"] in APPLY_REMOVE_HOOKS]
    if rows:
        effects = [{"index": 0, "hooks": [(r["hook"], r["handle_mode"]) for r in rows]}]
        out.append({
            "id": "AL-T-H-02",
            "question": "What runs when an existing aura is refreshed by reapplication (same caster)?",
            "witness": {"spell": spell, "name": ctx.name(spell),
                        "hooks": [{"hook": r["hook"], "script": r["script"], "mode": _mode_class(r["handle_mode"])}
                                  for r in rows]},
            "events": [
                {"t_ms": 0, "event": "first application",
                 "callbacks": [f"eff0:{r['hook']}" for r in rows if r["hook"].endswith("Apply")
                               and apply_remove_hook_fires(r["handle_mode"], "real")]},
                {"t_ms": 3000, "event": "reapplied by same caster -> _TryStackingOrRefreshingExistingAura Unit.cpp:3441 "
                                        "-> ModStackAmount -> SetStackAmount SpellAuras.cpp:1056",
                 "callbacks": stack_change_sequence(effects)},
            ],
            "rules_out": ["refresh executes no apply/remove callbacks",
                          "refresh is symmetric remove+apply of the scripted handlers (REAL-only remove handler does not run)"],
            "evidence": ["script-consumer", "trinity-consumer"]})
    # T3 remove-mode conditioned external behaviour (Lifebloom, Rupture)
    lb = [r for r in idx.remove_mode_handlers if r["spell"] in (33763, 1943)]
    if lb:
        out.append({
            "id": "AL-T-H-03",
            "question": "Is death just another natural expiry for external (script / link) removal behaviour?",
            "witness": [{k: r[k] for k in ("spell", "script", "hook", "modes", "line")} | {"file": r["file"].split("/")[-1]}
                        for r in sorted(lb, key=lambda r: r["spell"])],
            "events": [
                {"t_ms": 0, "event": "Lifebloom 33763 applied"},
                {"t_ms": "D (expiry)", "event": "removed AURA_REMOVE_BY_EXPIRE",
                 "actions": ["AfterEffectRemove casts final heal 33778 (spell_druid.cpp:1506-1507)"]},
                {"t_ms": "t < D (target dies)", "event": "removed AURA_REMOVE_BY_DEATH",
                 "actions": ["no final heal (mode not EXPIRE/ENEMY_SPELL)"]},
                {"t_ms": "t < D (Rupture 1943 target dies)", "event": "removed AURA_REMOVE_BY_DEATH",
                 "actions": ["OnEffectRemove refunds energy * remaining/max duration read at removal (spell_rogue.cpp:1051-1073)"]},
            ],
            "rules_out": ["death == natural expiry for removal callbacks"],
            "evidence": ["script-consumer"]})
    # T4 stack change re-runs the removal half of HandleAuraSpecificMods
    out.append({
        "id": "AL-T-H-04",
        "question": "Which Aura::HandleAuraSpecificMods blocks run on a stack change / refresh?",
        "events": [
            {"t_ms": 0, "event": "apply", "blocks": specific_mods_blocks(True, False)},
            {"t_ms": 1000, "event": "stack change / refresh (SetStackAmount)",
             "blocks": [f"remove-half:{b}" for b in specific_mods_blocks(False, True)] +
                       [f"apply-half:{b}" for b in specific_mods_blocks(True, True)]},
            {"t_ms": 2000, "event": "removal", "blocks": specific_mods_blocks(False, False)},
        ],
        "rules_out": ["only the linked-aura stack sync runs on a stack change"],
        "evidence": ["trinity-consumer"]})
    return out


def findings(ctx, cen: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    idx = index(ctx)
    pl = cen["populations"]["player"]
    al = cen["populations"]["all"]
    link_remove_pos = sorted(trig for (typ, trig), xs in idx.links.items() if typ == LINK_REMOVE and any(x > 0 for x in xs))
    link_aura = sorted(trig for (typ, trig), xs in idx.links.items() if typ == LINK_AURA and any(x > 0 for x in xs))
    reapply = cen["reapply_hook_rows"]
    override = {s for s, ts in idx.touches.items() for t in ts if t["kind"] == "spell_proc" and "value" in t}
    use_stacks = {s for s, ts in idx.touches.items() for t in ts
                  if t["kind"] == "spell_proc" and t["surface"] == "stacks"}
    rm_all = cen["remove_mode_handlers"]["all"]
    rules = [
        {"id": "AL-R-H-01", "name": "linked-on-remove-cast-suppressed-by-death",
         "definition": "A spell_linked_spell on-remove cast (negative trigger, positive target) runs for every remove "
                       "mode except AURA_REMOVE_BY_DEATH; an on-remove removal (negative target) runs for all modes.",
         "population": {"name": "all: triggers with positive on-remove targets", "count": len(link_remove_pos)},
         "counterexamples": [], "status": "trinity-only",
         "evidence": ["trinity-consumer", "world-db-fact"], "coords": ["SpellAuras.cpp:1419-1430", "SpellMgr.cpp:2126-2133"]},
        {"id": "AL-R-H-02", "name": "linked-aura-bounded-by-parent-not-sharing-lifetime",
         "definition": "A LINK_AURA child is created by caster->AddAura with its own duration (only if the caster exists), "
                       "is removed with the parent's remove mode (same caster GUID only), and has its stack amount "
                       "forced to the parent's on every parent stack change/refresh.",
         "population": {"name": "all: LINK_AURA triggers with positive children", "count": len(link_aura)},
         "counterexamples": [], "status": "trinity-only", "evidence": ["trinity-consumer", "world-db-fact"],
         "coords": ["SpellAuras.cpp:1407-1415,1433-1440,1446-1453"]},
        {"id": "AL-R-H-03", "name": "external-cascade-ordering",
         "definition": "On apply, spell_area/linked cascades run before the aura's own effect handlers and "
                       "OnEffectApply/AfterEffectApply scripts; on removal, the aura's effect handlers and "
                       "OnEffectRemove/AfterEffectRemove scripts run first and the cascades last.",
         "population": {"name": "every aura application/removal", "count": None},
         "counterexamples": [], "status": "trinity-only", "evidence": ["trinity-consumer"],
         "coords": ["Unit.cpp:3576-3587", "Unit.cpp:3610,3659-3661,3680"]},
        {"id": "AL-R-H-04", "name": "reapply-mode-handlers-run-on-refresh",
         "definition": "An AuraScript OnEffectApply/AfterEffectApply/OnEffectRemove/AfterEffectRemove handler runs on a "
                       "refresh or stack change iff its registration mask contains AURA_EFFECT_HANDLE_REAPPLY "
                       "(or CHANGE_AMOUNT and the recomputed amount differs); REAL-only handlers do not.",
         "population": {"name": "all: apply/remove hook bindings with REAPPLY", "count": len(reapply),
                        "player": sum(1 for r in reapply if r["player"]),
                        "player+class-skills": sum(1 for r in reapply if r["player_class_skills"])},
         "counterexamples": [], "status": "trinity-only", "evidence": ["trinity-consumer", "script-consumer"],
         "coords": ["SpellScript.h:1387-1393", "SpellAuras.cpp:1056-1076", "SpellAuraEffects.cpp:1091-1128"]},
        {"id": "AL-R-H-05", "name": "spell-proc-charges-override",
         "definition": "Initial and max proc charges are spell_proc.Charges when that column is nonzero, else DB2 "
                       "ProcCharges (then ProcCharges spell mods).",
         "population": {"name": "all: spells whose merged spell_proc charges differ from DB2 ProcCharges",
                        "count": len(override), "player": len(override & set(cen["providers_player_ids"])),
                        "use_stacks_for_charges_all": len(use_stacks),
                        "use_stacks_for_charges_player": len(use_stacks & set(cen["providers_player_ids"]))},
         "counterexamples": [], "status": "trinity-only", "evidence": ["trinity-consumer", "world-db-fact"],
         "coords": ["SpellMgr.cpp:1497-1560 (take defaults from dbcs)", "SpellAuras.cpp:1004-1015"]},
        {"id": "AL-R-H-06", "name": "sparse-external-touch",
         "definition": "Most aura providers have no external lifecycle touch in any indexed source; for those, every "
                       "surface is decided by DB2 through the generic consumer (subject to the coverage caveat).",
         "population": {"name": "player", "count": pl["providers"], "touched": pl["touched_any_surface"],
                        "all": al["providers"], "all_touched": al["touched_any_surface"]},
         "counterexamples": ["not a proof: creature/instance/areatrigger scripts and non-literal engine branches are "
                             "not indexed (AL-U-H-02)"],
         "status": "holds-on-census", "evidence": ["world-db-fact", "script-consumer", "structural-inference"]},
        {"id": "AL-R-H-07", "name": "remove-mode-is-observable-to-external-policy",
         "definition": "External removal behaviour may branch on the remove mode (EXPIRE, DEATH, ENEMY_SPELL, CANCEL, "
                       "INTERRUPT, DEFAULT); death is a distinct input, never equal to expiry.",
         "population": {"name": "all: remove-mode-conditional script handlers", "count": rm_all["handlers"],
                        "modes": rm_all["modes_compared"],
                        "player": cen["remove_mode_handlers"]["player"]["handlers"]},
         "counterexamples": [], "status": "holds-on-census", "evidence": ["script-consumer"],
         "coords": ["SpellAuraDefines.h:55-64", "spell_druid.cpp:1506", "spell_rogue.cpp:1053"]},
    ]
    falsification = [
        {"id": "AL-F-H-01", "rule": "negative spell_linked_spell trigger = remove at apply time",
         "attempt": "read LoadSpellLinked and the consumer",
         "result": "refuted: trigger<0 is rewritten to type SPELL_LINK_REMOVE (SpellMgr.cpp:2126-2133) and consumed "
                   "only in the removal branch (SpellAuras.cpp:1419)", "action": "rule AL-R-H-01 uses the loader normalisation"},
        {"id": "AL-F-H-02", "rule": "script apply/remove hooks run only on real apply/remove",
         "attempt": "search registrations with AURA_EFFECT_HANDLE_REAPPLY and trace SetStackAmount -> ChangeAmount",
         "result": f"refuted: {len(reapply)} bindings (all) carry REAPPLY and run on every refresh/stack change",
         "action": "AL-R-H-04; refresh surface marked touched for them"},
        {"id": "AL-F-H-03", "rule": "a refresh never executes removal-side external code",
         "attempt": "block guards of Aura::HandleAuraSpecificMods under SetStackAmount(apply=false,onReapply=true)",
         "result": "refuted: spell_area, family 'mods at aura remove' and CreatureAI::OnAuraRemoved run (AL-D-H-01)",
         "action": "specific_mods_blocks(); timeline AL-T-H-04"},
        {"id": "AL-F-H-04", "rule": "no DB2 aura provider => no aura",
         "attempt": "count serverside_spell aura providers absent from the DB2 snapshot",
         "result": f"refuted: {len(idx.serverside_providers)} serverside aura spells exist only in the world DB "
                   f"({len(idx.serverside_rejected)} serverside rows collide with DB2 ids and are rejected by Trinity)",
         "action": "serverside providers listed separately; never counted in DB2 populations"},
        {"id": "AL-F-H-05", "rule": "spell_proc only affects proc eligibility, not aura lifetime",
         "attempt": "trace spell_proc Charges / AttributesMask into Aura",
         "result": "refuted: Charges sets initial/max charges (CalcMaxCharges) and PROC_ATTR_USE_STACKS_FOR_CHARGES turns "
                   "consumption into stack loss (ConsumeProcCharges)", "action": "AL-R-H-05; charges/stacks surfaces"},
        {"id": "AL-F-H-06", "rule": "a linked aura lives exactly as long as its parent",
         "attempt": "AddAura path + removal path for LINK_AURA", "result": "refuted: own duration, removal only cascades "
         "(AL-T-H-01)", "action": "AL-R-H-02"},
        {"id": "AL-F-H-07", "rule": "every AuraScript hook registered on a spell executes",
         "attempt": "GetAffectedEffectsMask against the 12.1 snapshot",
         "result": f"refuted: {cen['never_executing_hooks_on_providers']['all']['hooks']} hook bindings on providers have "
                   "mask 0 or unresolvable tokens (build skew / script drift)", "action": "only executing hooks count as touches"},
        {"id": "AL-F-H-08", "rule": "Dummy-pass BindingMap resolves every aura hook registration",
         "attempt": "inspect 'unresolved hook tokens' bindings",
         "result": f"refuted: two-argument absorb/split macros imply the aura type; {idx.implied_aura_hooks} bindings "
                   "resolved here (SpellScript.h:1744-1784)", "action": "resolved locally; request to lead to fold into dummy_semantics"},
    ]
    unknowns = [
        {"id": "AL-U-H-01", "subject": "spell_linked_spell / spell_proc / spell_group semantics in Retail",
         "question": "Do the server-authored couplings (e.g. Beacon of Light 53563 -> 53651 LINK_AURA, Clearcasting "
                     "16870 stacks-for-charges) exist with the same lifecycle in Retail?",
         "known": "they are TrinityCore world-DB rows, not client data", "why_unresolved": "no Retail server source",
         "evidence": ["world-db-fact", "retail-unknown"], "coords": ["SpellAuras.cpp:1401-1455", "SpellAuras.cpp:1820"],
         "blocker": "Retail observation", "reopen_condition": "AL-X-H-01/02/04 results", "build_skew": False},
        {"id": "AL-U-H-02", "subject": "coverage of external sources",
         "question": "Which unindexed sources mutate player aura lifecycles (creature AI, SmartAI, instance scripts, "
                     "AreaTriggerAI, engine branches keyed on non-literal ids)?",
         "known": "indexed: SOURCE_KINDS", "why_unresolved": "not parsed per spell",
         "evidence": ["unresolved"], "coords": ["dummy-corpora/script-index.json engine/classes"],
         "blocker": "index scope", "reopen_condition": "index AI/areatrigger/instance classes by referenced SpellIDs",
         "build_skew": False},
        {"id": "AL-U-H-03", "subject": "cross-spell script receivers",
         "question": "Which aura is mutated when a handler calls SetDuration/ModStackAmount/Remove on a local variable?",
         "known": f"{cen['cross_spell_script_edges']['by_evidence'].get('structural-inference', 0)} edges attributed by "
                  "structural inference (GetAura(X) in the same handler)",
         "why_unresolved": "no dataflow in the structural index", "evidence": ["structural-inference"],
         "coords": ["aura_lifecycle/overlays.py ExternalIndex._actions"], "blocker": "dataflow",
         "reopen_condition": "per-handler dataflow or manual review", "build_skew": False},
        {"id": "AL-U-H-04", "subject": "runtime-evaluated gates",
         "question": "Which recipients/casts do conditions (sources 13/17/24) admit for a concrete application?",
         "known": "presence and effect masks only", "why_unresolved": "ConditionMgr not ported",
         "evidence": ["world-db-fact", "unresolved"], "coords": ["SpellAuras.cpp:2579-2622,2726"],
         "blocker": "condition evaluation", "reopen_condition": "track F / conditions port", "build_skew": False},
        {"id": "AL-U-H-05", "subject": "script build skew",
         "question": "Do scripts written for <= 12.0.7 still describe 12.1 spells whose effects moved?",
         "known": f"{cen['never_executing_hooks_on_providers']['player']['hooks']} player-provider hook bindings never "
                  "execute on the 12.1 snapshot", "why_unresolved": "no 12.1 Trinity",
         "evidence": ["build-skew"], "coords": ["SpellScript.cpp:290-330 (_Validate)"],
         "blocker": "Trinity support for 12.1", "reopen_condition": "Trinity bump", "build_skew": True},
        {"id": "AL-U-H-06", "subject": "family-flag engine branches outside HandleAuraSpecificMods",
         "question": "Which other engine branches keyed on SpellFamilyFlags change lifecycle for current spells?",
         "known": "only HandleAuraSpecificMods and LoadSpellInfoCustomAttributes family branches are mirrored",
         "why_unresolved": "no family-flag index of the engine", "evidence": ["unresolved"],
         "coords": ["SpellAuras.cpp:1476-1546", "SpellMgr.cpp:3249-3263"], "blocker": "index scope",
         "reopen_condition": "index flag128 comparisons in engine files", "build_skew": False},
    ]
    experiments = [
        {"id": "AL-X-H-01", "question": "Does an on-remove effect distinguish death from expiry (Lifebloom bloom)?",
         "models": [{"name": "trinity-remove-mode", "prediction": "bloom heal 33778 on expiry/dispel, none when target dies"},
                    {"name": "any-removal", "prediction": "bloom on every removal including death"}],
         "setup": "Lifebloom on a friendly target that dies before expiry; control: same target survives to expiry",
         "observable": "combat log SPELL_HEAL 33778 presence", "discriminates": "death vs expiry for external removal code",
         "fidelity": "approximate", "related": ["AL-R-H-07", "AL-T-H-03"]},
        {"id": "AL-X-H-02", "question": "Is Light's Beacon (53651) bounded by Beacon of Light (53563) but with its own timer?",
         "models": [{"name": "trinity-link-aura", "prediction": "53651 removed when 53563 removed; may expire earlier on own duration"},
                    {"name": "shared-lifetime", "prediction": "53651 always has 53563's remaining duration"}],
         "setup": "cast Beacon of Light; read both auras' remaining durations; cancel/let expire the beacon",
         "observable": "aura remaining time (UnitAura) of both spells", "discriminates": "AL-R-H-02",
         "fidelity": "exact", "related": ["AL-R-H-02", "AL-T-H-01"]},
        {"id": "AL-X-H-03", "question": "Does a refresh re-run apply-side effects (Rejuvenation + Abundance)?",
         "models": [{"name": "trinity-reapply-handler", "prediction": "refresh refreshes the Abundance stack's duration, no new stack"},
                    {"name": "real-only", "prediction": "refresh does not touch Abundance"}],
         "setup": "with Abundance talented, cast Rejuvenation on A, then refresh on A", "observable": "Abundance stacks/remaining",
         "discriminates": "AL-R-H-04", "fidelity": "approximate", "related": ["AL-R-H-04", "AL-T-H-02"]},
        {"id": "AL-X-H-04", "question": "Does consuming a proc from a stacked aura drop a stack or the aura (Clearcasting 16870)?",
         "models": [{"name": "stacks-for-charges", "prediction": "one stack removed per consumed proc"},
                    {"name": "charges", "prediction": "whole aura removed at first consumption"}],
         "setup": "build 2 Clearcasting stacks, cast one consumer", "observable": "stack count after consumption",
         "discriminates": "AL-R-H-05 companion (PROC_ATTR_USE_STACKS_FOR_CHARGES)", "fidelity": "exact",
         "related": ["AL-F-H-05"]},
    ]
    defects = [
        {"id": "AL-D-H-01", "coords": ["SpellAuras.cpp:1056-1076", "SpellAuras.cpp:1380-1398,1492-1553,1597-1604"],
         "description": "Aura::SetStackAmount calls HandleAuraSpecificMods(apply=false, onReapply=true) for every "
                        "stack change/refresh; the spell_area block, the family 'mods at aura remove' branch and "
                        "CreatureAI::OnAuraRemoved are not guarded by onReapply, so they run although nothing is removed "
                        "(followed by OnAuraApplied).",
         "lifecycle_effect": "refresh/stack change can auto-remove spell_area auras and emits spurious remove/apply AI "
                             "callbacks; family remove branches see removeMode AURA_REMOVE_NONE",
         "oracle_behaviour": "reproduced by specific_mods_blocks(False, True); not corrected"},
        {"id": "AL-D-H-02", "coords": ["SpellMgr.cpp:2126-2133"],
         "description": "LoadSpellLinked logs 'changed to 0' for a negative trigger with type != 0 but sets "
                        "SPELL_LINK_REMOVE (3).",
         "lifecycle_effect": "none (log text only); the stored type is REMOVE",
         "oracle_behaviour": "loader normalisation reproduced (ServerOverlay.linked)"},
    ]
    return {"rules": rules, "falsification": falsification, "unknowns": unknowns,
            "retail_experiments": experiments, "trinity_defects": defects, "core_navigation": []}


def corpus(ctx, command: str) -> dict[str, Any]:
    from . import records
    cen = census(ctx)
    f = findings(ctx, cen)
    payload = {
        "provenance": records.provenance(command, coverage=sorted(SOURCE_KINDS)),
        "vocabulary": {
            "surfaces": {k: {"owner": v[0], "baseline_policy": v[1], "meaning": v[2]} for k, v in SURFACES.items()},
            "source_kinds": {k: {"policy_source": v[0], "coords": v[1]} for k, v in SOURCE_KINDS.items()},
            "modes": list(MODES),
            "aura_hook_surfaces": {k: list(v) for k, v in AURA_HOOK_SURFACES.items()},
        },
        "census": {k: v for k, v in cen.items() if k not in ("providers", "all_touched", "remove_mode_handler_rows",
                                                             "reapply_hook_rows", "providers_player_ids")},
        "providers": cen["providers"],
        "all_touched": cen["all_touched"],
        "remove_mode_handler_rows": cen["remove_mode_handler_rows"],
        "reapply_hook_rows": cen["reapply_hook_rows"],
        "timelines": timelines(ctx),
        "implied_aura_hooks": index(ctx).implied_aura_hooks,
        **f,
    }
    records.validate_corpus(payload)
    return payload
