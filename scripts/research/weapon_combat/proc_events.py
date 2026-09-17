"""Proc event production for weapon attacks -- joined to ``procs.events``.

Determines exactly which ``Unit::ProcSkillsAndAuras`` invocation a white
main-hand / off-hand swing, an extra attack, an auto-attack override and a
ranged auto-shot produce, per outcome, at TrinityCore ``7f3d43b``.  The
chance / state machinery is *not* rebuilt: where an event is representable
the :class:`procs.events.ProcEvent` builder is used and its masks are
cross-checked against the consumer text read here; a disagreement is
recorded as a contradiction instead of patched.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from typing import Any

from procs import enums as E
from procs.events import (BASE_ATTACK, OFF_ATTACK, ProcEvent, melee_hit_mask, melee_swing,
                          spell_hit_mask)

from . import CORPORA, SNAPSHOT_BUILD, TRINITY_COMMIT

WHITE_OUTCOMES = ("normal", "crit", "miss", "dodge", "parry", "block", "glancing", "crushing", "evade", "immune")

#: ``PROC_HIT_*`` names for printing.
HIT_NAMES = E.HIT_NAMES


def _flag_names(mask: int, table: dict[int, str]) -> list[str]:
    return [name for bit, name in sorted(table.items()) if mask & bit]


def _proc_flag_names(mask: int) -> list[str]:
    table = E.PROC_FLAG_NAMES
    return _flag_names(mask, table)


def _hit_names(mask: int) -> list[str]:
    table = E.HIT_NAMES
    return _flag_names(mask, table)


# ---------------------------------------------------------------------------
# consumer facts (each cited)
# ---------------------------------------------------------------------------

FACTS: list[dict[str, Any]] = [
    {"id": "P01", "fact": "White swing event site: Unit::AttackerStateUpdate -> Unit::ProcSkillsAndAuras(attacker, victim, ProcAttacker, ProcVictim, PROC_SPELL_TYPE_NONE, PROC_SPELL_PHASE_NONE, DamageInfo(CalcDamageInfo).GetHitMask(), spell=nullptr, &dmgInfo, healInfo=nullptr).",
     "coord": "Unit.cpp:2346-2347"},
    {"id": "P02", "fact": "ProcAttacker: BASE_ATTACK -> PROC_FLAG_DEAL_MELEE_SWING | PROC_FLAG_MAIN_HAND_WEAPON_SWING; OFF_ATTACK -> PROC_FLAG_DEAL_MELEE_SWING | PROC_FLAG_OFF_HAND_WEAPON_SWING (and HITINFO_OFFHAND). ProcVictim: PROC_FLAG_TAKE_MELEE_SWING; | PROC_FLAG_TAKE_ANY_DAMAGE when int32(Damage) > 0 after armor/outcome/resilience and BEFORE absorb (a fully absorbed hit still carries TAKE_ANY_DAMAGE). RANGED_ATTACK returns from CalculateMeleeDamage with PROC_FLAG_NONE and AttackerStateUpdate returns before it anyway.",
     "coord": "Unit.cpp:1356-1368, 1508-1510, 2285-2286"},
    {"id": "P03", "fact": "Hit mask for white swings is built by DamageInfo::DamageInfo(CalcDamageInfo const&): VICTIMSTATE_IS_IMMUNE -> IMMUNE; VICTIMSTATE_BLOCKS -> FULL_BLOCK (never produced by CalculateMeleeDamage, which sets VICTIMSTATE_HIT on block); HITINFO_PARTIAL/FULL_ABSORB -> ABSORB; HITINFO_FULL_RESIST -> FULL_RESIST; Blocked != 0 -> BLOCK; then outcome: MISS/DODGE/PARRY/EVADE map 1:1, BLOCK/CRUSHING/GLANCING/NORMAL -> NORMAL and CRIT -> CRITICAL unless damageNullified (FULL_ABSORB | FULL_RESIST | IMMUNE | FULL_BLOCK).",
     "coord": "Unit.cpp:142-192"},
    {"id": "P04", "fact": "PROC_HIT_NORMAL does not require Damage > 0: a blocked/partially-absorbed swing that ends at 0 damage still reports NORMAL|BLOCK / NORMAL|ABSORB; only FULL_ABSORB nullifies it.",
     "coord": "Unit.cpp:163-165, 181-191"},
    {"id": "P05", "fact": "Immune victim: CalculateMeleeDamage returns before the outcome roll with TargetState VICTIMSTATE_IS_IMMUNE while HitOutCome keeps its preset MELEE_HIT_EVADE, so the hit mask is IMMUNE | EVADE; ProcVictim has no TAKE_ANY_DAMAGE.",
     "coord": "Unit.cpp:1347, 1371-1380, 150-153, 178-180"},
    {"id": "P06", "fact": "spellTypeMask = PROC_SPELL_TYPE_NONE and spellPhaseMask = PROC_SPELL_PHASE_NONE for every white swing; ProcEventInfo::GetSpellInfo() is nullptr (DamageInfo m_spellInfo = nullptr); GetProcSpell() is nullptr.",
     "coord": "Unit.cpp:2347, 143"},
    {"id": "P07", "fact": "Extra attacks (HandleProcExtraAttackFor -> AttackerStateUpdate(victim, BASE_ATTACK, extra=true)) reach the same CalculateMeleeDamage/ProcSkillsAndAuras call with identical masks: `extra` only bypasses UNIT_STATE_CANNOT_AUTOATTACK and the CURRENT_MELEE_SPELL branch. No flag distinguishes an extra attack from a normal main-hand swing in the proc event.",
     "coord": "Unit.cpp:2365-2372, 2267, 2292"},
    {"id": "P08", "fact": "Suppression on white swings: spellInfo == nullptr so SPELL_ATTR3_SUPPRESS_CASTER_PROCS / SUPPRESS_TARGET_PROCS cannot apply; spell == nullptr so IsProcDisabled (TRIGGERED_DISALLOW_PROC_EVENTS) and ProcChainHardLimit cannot apply; Unit::CanProc (m_procDeep) is NOT consulted by AttackerStateUpdate (only Spell::TargetInfo::DoDamageAndTriggers checks unitTarget->CanProc()). SPELL_AURA_DISABLE_AUTOATTACK / DISABLE_ATTACKING_EXCEPT_ABILITIES / UNIT_FLAG_PACIFIED suppress the swing itself, hence the event.",
     "coord": "Unit.cpp:5569-5600 (:5574-5578, :5591, :5594), Spell.cpp:8300-8303, Unit.cpp:10620-10624, Spell.cpp:2851, Unit.cpp:2264-2274"},
    {"id": "P09", "fact": "Owner vs pet: TriggerAurasProcOnEvent visits the actor's own applied auras; the spell-mod owner's auras are visited ONLY when `spell` is non-null and only those auras contained in spell->m_appliedMods (\"needed for example for Cobra Strikes\"). For a pet white swing (spell == nullptr) the owner's aura list is never visited; the owner receives no event.",
     "coord": "Unit.cpp:10580-10617 (:10587-10604)"},
    {"id": "P10", "fact": "Weapon enchant / item ON_PROC effects on white swings are NOT proc events: Player::CastItemCombatSpell runs from DealMeleeDamage (before ProcSkillsAndAuras) gated on hitMask & (NORMAL | CRITICAL | ABSORB), with its own roll_chance per item/enchant (spell_enchant_proc_data).",
     "coord": "Unit.cpp:1596-1600, Player.cpp:8596-8760"},
    {"id": "P11", "fact": "Auto-attack override (SPELL_AURA_OVERRIDE_AUTOATTACK_WITH_MELEE_SPELL): CastSpell(victim, id, TRIGGERED_FULL_MASK) replaces the swing; no white event; the replacement spell's own DmgClass decides its flags (DEAL_MELEE_ABILITY | hand flag for MELEE; the hand flag comes from Spell::m_attackType, i.e. the spell's own attack type, not the swing hand).",
     "coord": "Unit.cpp:2354, Spell.cpp:2339-2357"},
    {"id": "P12", "fact": "Ranged auto-shot is a Spell: prepareDataForTriggerSystem gives DmgClass RANGED + SPELL_ATTR2_AUTO_REPEAT -> PROC_FLAG_DEAL_RANGED_ATTACK / PROC_FLAG_TAKE_RANGED_ATTACK; events: (1) Spell::_cast success (procAttacker | PROC_FLAG_2_CAST_SUCCESSFUL, PROC_SPELL_TYPE_MASK_ALL, PROC_SPELL_PHASE_CAST, hitMask | NORMAL unless CRITICAL), (2) per target DoDamageAndTriggers (phase HIT, spellType DAMAGE if damage else NO_DMG_HEAL, hit mask createProcHitMask(SpellNonMeleeDamage, MissCondition), | TAKE_ANY_DAMAGE when damage > 0 and not immune; CanProc gate; CastItemCombatSpell after unless SUPPRESS_WEAPON_PROCS / CANCELS_AUTO_ATTACK_COMBAT), (3) _handle_finish_phase (phase FINISH), (4) Spell::finish PROC_FLAG_CAST_ENDED.",
     "coord": "Spell.cpp:2339-2371, 3898-3915, 2825-2989, 4218-4224, 4401 (see procs.events.EVENT_PRODUCERS)"},
    {"id": "P13", "fact": "A blocked weapon spell (SPELL_MISS_BLOCK from MeleeSpellHitResult) still hits unless SPELL_ATTR3_COMPLETELY_BLOCKED (0x8): effects run and damage is dealt minus CalculatePct(damage, uint32(victim->GetBlockPercent())) (0 for player victims, 30% for creatures), but the proc hit mask is always PROC_HIT_BLOCK | PROC_HIT_FULL_BLOCK (createProcHitMask ignores the amount when missCondition != NONE), so NORMAL/CRITICAL are never set; the crit roll is skipped for a blocked target (IsCrit only when MissCondition == NONE), so a blocked weapon spell never crits despite the 'CAN BE crit & blocked' comment. TAKE_ANY_DAMAGE is still added when damage > 0. A blocked white swing is partial (BLOCK | NORMAL/CRITICAL).",
     "coord": "Spell.cpp:2763-2766, 4875, 8540-8563, 8570, 2924-2927; Unit.cpp:1235-1252, 10433-10438, 1455-1469; SharedDefines.h:551"},
    {"id": "P14", "fact": "No attribute turns an ability into a swing for proc purposes: PROC_FLAG_DEAL_MELEE_SWING is produced only by CalculateMeleeDamage. But every DmgClass MELEE spell carries PROC_FLAG_MAIN_HAND_WEAPON_SWING (or OFF_HAND_WEAPON_SWING when m_attackType == OFF_ATTACK) together with DEAL_MELEE_ABILITY, so a proc keyed only on the hand bits fires for white swings and melee abilities alike.",
     "coord": "rg PROC_FLAG_DEAL_MELEE_SWING src/server/game -> Unit.cpp:1359,1363 only; Spell.cpp:2350-2357"},
    {"id": "P16", "fact": "Spell-path damage events: procVictim |= TAKE_ANY_DAMAGE whenever m_damage > 0 and the target is not damage-immune, even when the damage is then fully absorbed (HITINFO_FULL_ABSORB -> hit mask ABSORB only); damage immunity sets ProcHitMask = IMMUNE with spell type DAMAGE and no TAKE_ANY_DAMAGE; a missed target (miss/dodge/parry/deflect/evade/resist/spell immunity) goes through the no-damage branch: createProcHitMask(missCondition), spell type NO_DMG_HEAL.",
     "coord": "Spell.cpp:2904-2950, Unit.cpp:1295, 10419-10470"},
    {"id": "P15", "fact": "Actor-side skills/reactives: ProcSkillsAndReactives(false, victim, typeMaskActor, hitMask, attType) for the actor and (true, actor, ...) for the target run before the aura procs (subject to the SUPPRESS_* attributes, which cannot apply to swings).",
     "coord": "Unit.cpp:5591-5595"},
]


# ---------------------------------------------------------------------------
# event construction
# ---------------------------------------------------------------------------

def white_swing_event(hand: str, outcome: str, *, damage: int = 1, absorb: int = 0, resist: int = 0,
                      blocked: int = 0, extra: bool = False) -> dict[str, Any]:
    """Build the ProcSkillsAndAuras arguments for a white swing.

    ``damage`` is the value after outcome/armor/resilience and BEFORE absorb
    (the TAKE_ANY_DAMAGE test at Unit.cpp:1508-1510); ``absorb``/``resist``
    are the amounts CalcAbsorbResist took; ``blocked`` is CalcDamageInfo::Blocked.
    """
    if hand not in (BASE_ATTACK, OFF_ATTACK):
        raise ValueError("white swings are base or off (Unit.cpp:1356-1368)")
    if outcome not in WHITE_OUTCOMES:
        raise ValueError(f"unknown outcome {outcome!r}")
    if outcome in ("miss", "dodge", "parry", "evade", "immune"):
        damage = 0
        absorb = resist = blocked = 0
    immune = outcome == "immune"
    eff_outcome = "evade" if immune else outcome
    full_absorb = damage > 0 and absorb > 0 and damage - absorb == 0
    full_resist = damage > 0 and resist > 0 and damage - resist == 0
    hit_mask = melee_hit_mask(eff_outcome, immune=immune, absorb=absorb > 0, full_absorb=full_absorb,
                              full_resist=full_resist, blocked_amount=blocked > 0)
    ev = melee_swing(hand, eff_outcome, damage=damage, immune=immune, absorb=absorb > 0, full_absorb=full_absorb,
                     full_resist=full_resist, blocked_amount=blocked > 0)
    post_absorb = max(damage - absorb - resist, 0)
    ev = ev.with_(damage=post_absorb, label=f"white swing {hand} {outcome}{' (extra attack)' if extra else ''}")
    return {
        "path": f"white_{'mh' if hand == BASE_ATTACK else 'oh'}{'_extra' if extra else ''}",
        "outcome": outcome,
        "actor_mask": ev.actor_mask, "actor_flags": _proc_flag_names(ev.actor_mask),
        "target_mask": ev.target_mask, "target_flags": _proc_flag_names(ev.target_mask),
        "spell_type_mask": ev.spell_type_mask, "spell_phase_mask": ev.spell_phase_mask,
        "hit_mask": hit_mask, "hit_flags": _hit_names(hit_mask),
        "spell": None, "damage_info": {"attack_type": hand, "damage_post_absorb": post_absorb, "absorb": absorb, "resist": resist, "block": blocked, "spell_info": None},
        "heal_info": None,
        "suppression": ["none reachable: spellInfo == nullptr, spell == nullptr, CanProc not consulted (P08)"],
        "coords": ["Unit.cpp:2346-2347", "Unit.cpp:1356-1368", "Unit.cpp:142-192", "Unit.cpp:1508-1510"] + (["Unit.cpp:2365-2372"] if extra else []),
        "proc_event": ev.to_dict(),
    }


def ranged_auto_event(outcome: str, *, damage: int = 1, absorb: int = 0, crit: bool = False, spell_id: int = 75,
                      immune: bool = False) -> dict[str, Any]:
    """The per-target HIT-phase event for an auto-repeat ranged spell (Auto Shot 75 / 467718).

    ``immune`` models damage immunity (IsImmunedToDamage, Spell.cpp:2912-2916); spell immunity is a
    miss condition (no-damage branch).  ``block`` with ``damage > 0`` is the partial weapon-spell block.
    """
    miss = {"normal": "none", "crit": "none", "miss": "miss", "dodge": "dodge", "parry": "parry", "block": "block",
            "evade": "evade", "immune": "immune", "deflect": "deflect", "resist": "resist"}.get(outcome)
    if miss is None:
        raise ValueError(f"unknown ranged outcome {outcome!r}")
    if outcome == "crit":
        crit = True
    actor = E.PROC_FLAG_DEAL_RANGED_ATTACK
    target = E.PROC_FLAG_TAKE_RANGED_ATTACK
    if miss not in ("none", "block"):
        damage = 0
    if miss == "block" and damage > 0:
        # partial block of a weapon spell: damage dealt, mask BLOCK|FULL_BLOCK, no crit (P13)
        hit_mask = spell_hit_mask("block")
        target |= E.PROC_FLAG_TAKE_ANY_DAMAGE
        spell_type = E.PROC_SPELL_TYPE_DAMAGE
    elif immune:
        hit_mask = E.PROC_HIT_IMMUNE
        damage = 0
        spell_type = E.PROC_SPELL_TYPE_DAMAGE  # m_damage was > 0 before immunity zeroed it (Spell.cpp:2912-2916)
    elif damage > 0:
        full_absorb = absorb > 0 and damage - absorb == 0
        hit_mask = spell_hit_mask("none", crit=crit, absorb=absorb > 0, full_absorb=full_absorb)
        target |= E.PROC_FLAG_TAKE_ANY_DAMAGE
        spell_type = E.PROC_SPELL_TYPE_DAMAGE
    else:
        hit_mask = spell_hit_mask(miss, crit=False)
        spell_type = E.PROC_SPELL_TYPE_NO_DMG_HEAL
    ev = ProcEvent(actor_mask=actor, target_mask=target, spell_type_mask=spell_type, spell_phase_mask=E.PROC_SPELL_PHASE_HIT,
                   hit_mask=hit_mask, spell_id=spell_id, has_proc_spell=True, has_damage_info=True,
                   damage=max(damage - absorb, 0), attack_type="ranged", label=f"ranged auto {spell_id} {outcome}")
    return {
        "path": "ranged_auto", "outcome": outcome, "spell": spell_id,
        "actor_mask": actor, "actor_flags": _proc_flag_names(actor), "target_mask": target, "target_flags": _proc_flag_names(target),
        "spell_type_mask": spell_type, "spell_phase_mask": E.PROC_SPELL_PHASE_HIT,
        "hit_mask": hit_mask, "hit_flags": _hit_names(hit_mask),
        "additional_events": [
            {"phase": "CAST", "actor": "DEAL_RANGED_ATTACK | 2_CAST_SUCCESSFUL", "target": "NONE", "spell_type": "MASK_ALL", "hit": "m_hitMask | NORMAL (or CRITICAL)", "coord": "Spell.cpp:3898-3915"},
            {"phase": "FINISH", "actor": "DEAL_RANGED_ATTACK", "target": "NONE", "spell_type": "accumulated", "hit": "accumulated", "coord": "Spell.cpp:4218-4224"},
            {"phase": "NONE", "actor": "CAST_ENDED", "target": "NONE", "spell_type": "MASK_ALL", "hit": "NONE", "coord": "Spell.cpp:4401 (Spell::finish)"},
        ],
        "suppression": ["unitTarget->CanProc() (m_procDeep == 0) else no HIT event (Spell.cpp:2851)",
                        "SPELL_ATTR3_SUPPRESS_CASTER_PROCS / SUPPRESS_TARGET_PROCS on the auto-shot spell (Unit.cpp:5591-5595)",
                        "TRIGGERED_DISALLOW_PROC_EVENTS (not set: _UpdateAutoRepeatSpell uses TRIGGERED_IGNORE_GCD, Unit.cpp:3052)",
                        "CastItemCombatSpell skipped for SPELL_ATTR0_CANCELS_AUTO_ATTACK_COMBAT / SPELL_ATTR4_SUPPRESS_WEAPON_PROCS (Spell.cpp:2985-2987)"],
        "coords": ["Spell.cpp:2339-2371", "Spell.cpp:2825-2989", "Unit.cpp:10419-10470"],
        "proc_event": ev.to_dict(),
    }


def override_event(spell_id: int, dmg_class: int, hand: str) -> dict[str, Any]:
    """The swing-replacement path (aura 361): what the replacement cast produces instead of a white event."""
    if dmg_class == 2:
        actor = E.PROC_FLAG_DEAL_MELEE_ABILITY | (E.PROC_FLAG_OFF_HAND_WEAPON_SWING if hand == OFF_ATTACK else E.PROC_FLAG_MAIN_HAND_WEAPON_SWING)
        target = E.PROC_FLAG_TAKE_MELEE_ABILITY
        note = "hand flag follows Spell::m_attackType of the replacement spell (Spell.cpp:2351-2355), which is derived from the spell, not from the swing hand"
    elif dmg_class == 3:
        actor, target = E.PROC_FLAG_DEAL_RANGED_ABILITY, E.PROC_FLAG_TAKE_RANGED_ABILITY
        note = "DmgClass RANGED without AUTO_REPEAT"
    else:
        actor = target = 0
        note = "DmgClass NONE/MAGIC: flags decided per target by positivity in DoDamageAndTriggers (HARMFUL_SPELL/ABILITY)"
    return {"path": "white_override", "replacement_spell": spell_id, "dmg_class": dmg_class,
            "white_event": "none (CalculateMeleeDamage/ProcSkillsAndAuras skipped, Unit.cpp:2352-2361)",
            "actor_mask": actor, "actor_flags": _proc_flag_names(actor), "target_mask": target, "target_flags": _proc_flag_names(target),
            "note": note, "coords": ["Unit.cpp:2299-2316", "Unit.cpp:2352-2361", "Spell.cpp:2339-2371"]}


# ---------------------------------------------------------------------------
# cross-check against procs.events
# ---------------------------------------------------------------------------

def cross_check() -> list[dict[str, Any]]:
    """Compare ``procs.events.melee_swing`` with the consumer text read here; list contradictions (expected: none)."""
    findings: list[dict[str, Any]] = []
    ev = melee_swing(BASE_ATTACK, "normal", damage=1)
    if ev.actor_mask != (E.PROC_FLAG_DEAL_MELEE_SWING | E.PROC_FLAG_MAIN_HAND_WEAPON_SWING):
        findings.append({"item": "melee_swing base actor mask", "oracle": ev.actor_mask, "consumer": "DEAL_MELEE_SWING | MAIN_HAND_WEAPON_SWING (Unit.cpp:1359)"})
    ev = melee_swing(OFF_ATTACK, "normal", damage=1)
    if ev.actor_mask != (E.PROC_FLAG_DEAL_MELEE_SWING | E.PROC_FLAG_OFF_HAND_WEAPON_SWING):
        findings.append({"item": "melee_swing off actor mask", "oracle": ev.actor_mask, "consumer": "DEAL_MELEE_SWING | OFF_HAND_WEAPON_SWING (Unit.cpp:1363)"})
    if ev.spell_type_mask != E.PROC_SPELL_TYPE_NONE or ev.spell_phase_mask != E.PROC_SPELL_PHASE_NONE:
        findings.append({"item": "melee_swing type/phase", "oracle": (ev.spell_type_mask, ev.spell_phase_mask), "consumer": "NONE/NONE (Unit.cpp:2347)"})
    if melee_swing(BASE_ATTACK, "normal", damage=0).target_mask & E.PROC_FLAG_TAKE_ANY_DAMAGE:
        findings.append({"item": "TAKE_ANY_DAMAGE at 0 damage", "oracle": "set", "consumer": "int32(Damage) > 0 (Unit.cpp:1508-1510)"})
    # hit-mask mapping table (Unit.cpp:142-192)
    expect = {"miss": E.PROC_HIT_MISS, "dodge": E.PROC_HIT_DODGE, "parry": E.PROC_HIT_PARRY, "evade": E.PROC_HIT_EVADE,
              "block": E.PROC_HIT_NORMAL, "crushing": E.PROC_HIT_NORMAL, "glancing": E.PROC_HIT_NORMAL, "normal": E.PROC_HIT_NORMAL, "crit": E.PROC_HIT_CRITICAL}
    for o, m in expect.items():
        got = melee_hit_mask(o)
        if got != m:
            findings.append({"item": f"melee_hit_mask({o})", "oracle": got, "consumer": m})
    if melee_hit_mask("normal", full_absorb=True) != E.PROC_HIT_ABSORB:
        findings.append({"item": "full absorb nullifies NORMAL", "oracle": melee_hit_mask("normal", full_absorb=True), "consumer": "ABSORB only (Unit.cpp:163-165, 184-186)"})
    if melee_hit_mask("evade", immune=True) != (E.PROC_HIT_IMMUNE | E.PROC_HIT_EVADE):
        findings.append({"item": "immune swing", "oracle": melee_hit_mask("evade", immune=True), "consumer": "IMMUNE | EVADE (P05)"})
    if melee_hit_mask("block", blocked_amount=True) != (E.PROC_HIT_BLOCK | E.PROC_HIT_NORMAL):
        findings.append({"item": "partial block", "oracle": melee_hit_mask("block", blocked_amount=True), "consumer": "BLOCK | NORMAL (Unit.cpp:160-161, 181-186)"})
    # the oracle's docstring says "spell_hit_mask block -> BLOCK|FULL_BLOCK" which matches createProcHitMask (Unit.cpp:10433-10438)
    if spell_hit_mask("block") != (E.PROC_HIT_BLOCK | E.PROC_HIT_FULL_BLOCK):
        findings.append({"item": "spell block", "oracle": spell_hit_mask("block"), "consumer": "BLOCK | FULL_BLOCK"})
    # ranged auto-shot HIT events vs procs.events.spell_hit (Spell::TargetInfo::DoDamageAndTriggers)
    from types import SimpleNamespace
    from procs.events import spell_hit
    info = SimpleNamespace(id=75, dmg_class=3, school_mask=1, equipped_item_class=2, equipped_item_subclass_mask=1 << 2,
                           has_attr=lambda a: a == E.ATTR2_AUTO_REPEAT)
    cases = {
        "normal": dict(damage=100), "crit": dict(damage=100, crit=True), "block": dict(damage=100, miss="block"),
        "immune": dict(damage=100, immune_to_damage=True),
        "miss": dict(miss="miss", all_hit_effects_positive=False), "deflect": dict(miss="deflect", all_hit_effects_positive=False),
        "evade": dict(miss="evade", all_hit_effects_positive=False), "resist": dict(miss="resist", all_hit_effects_positive=False),
    }
    for outcome, kw in cases.items():
        ours = ranged_auto_event(outcome, damage=kw.get("damage", 0), immune=kw.get("immune_to_damage", False))
        theirs = spell_hit(info, **kw)
        for field in ("actor_mask", "target_mask", "spell_type_mask", "hit_mask"):
            if ours[field] != getattr(theirs, field):
                findings.append({"item": f"ranged {outcome} {field}", "oracle": getattr(theirs, field), "consumer": ours[field]})
    return findings


# ---------------------------------------------------------------------------
# corpus
# ---------------------------------------------------------------------------

def build_corpus(generator: str) -> dict[str, Any]:
    from .attack_table import _git_head
    events: list[dict[str, Any]] = []
    for hand in (BASE_ATTACK, OFF_ATTACK):
        for outcome in WHITE_OUTCOMES:
            events.append(white_swing_event(hand, outcome, damage=100 if outcome not in ("miss", "dodge", "parry", "evade", "immune") else 0,
                                            blocked=30 if outcome == "block" else 0))
    events.append(white_swing_event(BASE_ATTACK, "normal", damage=100, absorb=100))
    events.append(white_swing_event(BASE_ATTACK, "crit", damage=200, absorb=50))
    events.append(white_swing_event(BASE_ATTACK, "normal", damage=100, extra=True))
    for outcome in ("normal", "crit", "miss", "block", "deflect", "resist", "evade", "immune"):
        events.append(ranged_auto_event(outcome, damage=100 if outcome in ("normal", "crit", "block", "immune") else 0, immune=(outcome == "immune")))
    events.append(ranged_auto_event("normal", damage=100, absorb=100))
    events.append(override_event(408385, 2, BASE_ATTACK))
    return {
        "provenance": {"snapshot_build": SNAPSHOT_BUILD, "trinitycore_commit": TRINITY_COMMIT, "generator": generator, "wowlab_data_commit": _git_head()},
        "facts": FACTS,
        "flag_values": {"PROC_FLAG_DEAL_MELEE_SWING": E.PROC_FLAG_DEAL_MELEE_SWING, "PROC_FLAG_TAKE_MELEE_SWING": E.PROC_FLAG_TAKE_MELEE_SWING,
                        "PROC_FLAG_MAIN_HAND_WEAPON_SWING": E.PROC_FLAG_MAIN_HAND_WEAPON_SWING, "PROC_FLAG_OFF_HAND_WEAPON_SWING": E.PROC_FLAG_OFF_HAND_WEAPON_SWING,
                        "PROC_FLAG_TAKE_ANY_DAMAGE": E.PROC_FLAG_TAKE_ANY_DAMAGE, "PROC_FLAG_DEAL_RANGED_ATTACK": E.PROC_FLAG_DEAL_RANGED_ATTACK,
                        "PROC_FLAG_TAKE_RANGED_ATTACK": E.PROC_FLAG_TAKE_RANGED_ATTACK, "PROC_FLAG_DEAL_MELEE_ABILITY": E.PROC_FLAG_DEAL_MELEE_ABILITY,
                        "PROC_HIT_NORMAL": E.PROC_HIT_NORMAL, "PROC_HIT_CRITICAL": E.PROC_HIT_CRITICAL, "PROC_HIT_MISS": E.PROC_HIT_MISS,
                        "PROC_HIT_DODGE": E.PROC_HIT_DODGE, "PROC_HIT_PARRY": E.PROC_HIT_PARRY, "PROC_HIT_BLOCK": E.PROC_HIT_BLOCK,
                        "PROC_HIT_EVADE": E.PROC_HIT_EVADE, "PROC_HIT_IMMUNE": E.PROC_HIT_IMMUNE, "PROC_HIT_ABSORB": E.PROC_HIT_ABSORB,
                        "PROC_HIT_FULL_BLOCK": E.PROC_HIT_FULL_BLOCK, "PROC_HIT_FULL_RESIST": E.PROC_HIT_FULL_RESIST, "PROC_HIT_DEFLECT": E.PROC_HIT_DEFLECT},
        "cross_check_contradictions": cross_check(),
        "events": events,
    }


def _cmd_event(args: argparse.Namespace) -> int:
    if args.hand == "ranged":
        out = ranged_auto_event(args.outcome, damage=args.damage, absorb=args.absorb, spell_id=args.spell, immune=(args.outcome == "immune"))
    else:
        hand = BASE_ATTACK if args.hand == "mh" else OFF_ATTACK
        out = white_swing_event(hand, args.outcome, damage=args.damage, absorb=args.absorb, blocked=args.blocked, extra=args.extra)
    print(json.dumps(out, indent=1))
    return 0


def _cmd_corpus(args: argparse.Namespace) -> int:
    corpus = build_corpus("python3 weapon_combat.py proc-events-corpus")
    CORPORA.mkdir(parents=True, exist_ok=True)
    path = CORPORA / "proc-events.json"
    path.write_text(json.dumps(corpus, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {path} ({len(corpus['events'])} events, {len(corpus['cross_check_contradictions'])} contradictions)")
    return 0


def register(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser("proc-event", help="ProcSkillsAndAuras arguments for a weapon attack outcome (via procs.events)")
    p.add_argument("--hand", choices=("mh", "oh", "ranged"), required=True)
    p.add_argument("--outcome", required=True, help="normal|crit|miss|dodge|parry|block|glancing|crushing|evade|immune (ranged: +deflect|resist)")
    p.add_argument("--extra", action="store_true", help="extra attack (HandleProcExtraAttackFor)")
    p.add_argument("--damage", type=int, default=1, help="CalcDamageInfo::Damage after outcome/armor/block/resilience, before absorb")
    p.add_argument("--absorb", type=int, default=0)
    p.add_argument("--blocked", type=int, default=0)
    p.add_argument("--spell", type=int, default=75, help="auto-repeat spell id (75 Auto Shot, 467718 Bleak Arrows)")
    p.set_defaults(func=_cmd_event)
    c = subparsers.add_parser("proc-events-corpus", help="write docs/research/weapon-combat-corpora/proc-events.json")
    c.set_defaults(func=_cmd_corpus)
