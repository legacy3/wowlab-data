"""Proc enums and masks, transcribed from TrinityCore.

Every value here is copied from a header, with the header named next to it.
The *names* are Trinity's; the package never relies on a name to decide
behaviour.  Semantics live with the consumers that read these bits
(:mod:`procs.eligibility`, :mod:`procs.definition`), and the research document
records which runtime fact sets each bit (see ``EVENT_PRODUCERS`` in
:mod:`procs.events`).

Bits that exist in current data but have no Trinity name are *unknown* and are
reported, never silently accepted: :func:`unknown_bits`.
"""

from __future__ import annotations

from collections.abc import Iterable

from charstats._aura_names import AURA_TYPE_NAMES

from ._trinity_names import SPELL_ATTR_NAMES, SPELL_EFFECT_NAMES, TRINITY_COMMIT

# ---------------------------------------------------------------------------
# ProcFlags / ProcFlags2 -- src/server/game/Spells/SpellMgr.h
# In DB2 these are SpellAuraOptions.ProcTypeMask_0 / ProcTypeMask_1 and are
# carried together as ProcFlagsInit (FlagsArray<int32, 2>, SpellDefines.h).
# This package represents the pair as one 64-bit integer: word0 | word1 << 32.
# ---------------------------------------------------------------------------

PROC_FLAG_NONE = 0x00000000
PROC_FLAG_HEARTBEAT = 0x00000001
PROC_FLAG_KILL = 0x00000002
PROC_FLAG_DEAL_MELEE_SWING = 0x00000004
PROC_FLAG_TAKE_MELEE_SWING = 0x00000008
PROC_FLAG_DEAL_MELEE_ABILITY = 0x00000010
PROC_FLAG_TAKE_MELEE_ABILITY = 0x00000020
PROC_FLAG_DEAL_RANGED_ATTACK = 0x00000040
PROC_FLAG_TAKE_RANGED_ATTACK = 0x00000080
PROC_FLAG_DEAL_RANGED_ABILITY = 0x00000100
PROC_FLAG_TAKE_RANGED_ABILITY = 0x00000200
PROC_FLAG_DEAL_HELPFUL_ABILITY = 0x00000400
PROC_FLAG_TAKE_HELPFUL_ABILITY = 0x00000800
PROC_FLAG_DEAL_HARMFUL_ABILITY = 0x00001000
PROC_FLAG_TAKE_HARMFUL_ABILITY = 0x00002000
PROC_FLAG_DEAL_HELPFUL_SPELL = 0x00004000
PROC_FLAG_TAKE_HELPFUL_SPELL = 0x00008000
PROC_FLAG_DEAL_HARMFUL_SPELL = 0x00010000
PROC_FLAG_TAKE_HARMFUL_SPELL = 0x00020000
PROC_FLAG_DEAL_HARMFUL_PERIODIC = 0x00040000
PROC_FLAG_TAKE_HARMFUL_PERIODIC = 0x00080000
PROC_FLAG_TAKE_ANY_DAMAGE = 0x00100000
PROC_FLAG_DEAL_HELPFUL_PERIODIC = 0x00200000
PROC_FLAG_MAIN_HAND_WEAPON_SWING = 0x00400000
PROC_FLAG_OFF_HAND_WEAPON_SWING = 0x00800000
PROC_FLAG_DEATH = 0x01000000
PROC_FLAG_JUMP = 0x02000000
PROC_FLAG_PROC_CLONE_SPELL = 0x04000000
PROC_FLAG_ENTER_COMBAT = 0x08000000
PROC_FLAG_ENCOUNTER_START = 0x10000000
PROC_FLAG_CAST_ENDED = 0x20000000
PROC_FLAG_LOOTED = 0x40000000
PROC_FLAG_TAKE_HELPFUL_PERIODIC = 0x80000000

_W2 = 32
PROC_FLAG_2_TARGET_DIES = 0x00000001 << _W2
PROC_FLAG_2_KNOCKBACK = 0x00000002 << _W2
PROC_FLAG_2_CAST_SUCCESSFUL = 0x00000004 << _W2
PROC_FLAG_2_SUCCESSFUL_DISPEL = 0x00000010 << _W2
PROC_FLAG_2_DO_EMOTE = 0x00000040 << _W2

PROC_FLAG_NAMES: dict[int, str] = {
    PROC_FLAG_HEARTBEAT: "HEARTBEAT",
    PROC_FLAG_KILL: "KILL",
    PROC_FLAG_DEAL_MELEE_SWING: "DEAL_MELEE_SWING",
    PROC_FLAG_TAKE_MELEE_SWING: "TAKE_MELEE_SWING",
    PROC_FLAG_DEAL_MELEE_ABILITY: "DEAL_MELEE_ABILITY",
    PROC_FLAG_TAKE_MELEE_ABILITY: "TAKE_MELEE_ABILITY",
    PROC_FLAG_DEAL_RANGED_ATTACK: "DEAL_RANGED_ATTACK",
    PROC_FLAG_TAKE_RANGED_ATTACK: "TAKE_RANGED_ATTACK",
    PROC_FLAG_DEAL_RANGED_ABILITY: "DEAL_RANGED_ABILITY",
    PROC_FLAG_TAKE_RANGED_ABILITY: "TAKE_RANGED_ABILITY",
    PROC_FLAG_DEAL_HELPFUL_ABILITY: "DEAL_HELPFUL_ABILITY",
    PROC_FLAG_TAKE_HELPFUL_ABILITY: "TAKE_HELPFUL_ABILITY",
    PROC_FLAG_DEAL_HARMFUL_ABILITY: "DEAL_HARMFUL_ABILITY",
    PROC_FLAG_TAKE_HARMFUL_ABILITY: "TAKE_HARMFUL_ABILITY",
    PROC_FLAG_DEAL_HELPFUL_SPELL: "DEAL_HELPFUL_SPELL",
    PROC_FLAG_TAKE_HELPFUL_SPELL: "TAKE_HELPFUL_SPELL",
    PROC_FLAG_DEAL_HARMFUL_SPELL: "DEAL_HARMFUL_SPELL",
    PROC_FLAG_TAKE_HARMFUL_SPELL: "TAKE_HARMFUL_SPELL",
    PROC_FLAG_DEAL_HARMFUL_PERIODIC: "DEAL_HARMFUL_PERIODIC",
    PROC_FLAG_TAKE_HARMFUL_PERIODIC: "TAKE_HARMFUL_PERIODIC",
    PROC_FLAG_TAKE_ANY_DAMAGE: "TAKE_ANY_DAMAGE",
    PROC_FLAG_DEAL_HELPFUL_PERIODIC: "DEAL_HELPFUL_PERIODIC",
    PROC_FLAG_MAIN_HAND_WEAPON_SWING: "MAIN_HAND_WEAPON_SWING",
    PROC_FLAG_OFF_HAND_WEAPON_SWING: "OFF_HAND_WEAPON_SWING",
    PROC_FLAG_DEATH: "DEATH",
    PROC_FLAG_JUMP: "JUMP",
    PROC_FLAG_PROC_CLONE_SPELL: "PROC_CLONE_SPELL",
    PROC_FLAG_ENTER_COMBAT: "ENTER_COMBAT",
    PROC_FLAG_ENCOUNTER_START: "ENCOUNTER_START",
    PROC_FLAG_CAST_ENDED: "CAST_ENDED",
    PROC_FLAG_LOOTED: "LOOTED",
    PROC_FLAG_TAKE_HELPFUL_PERIODIC: "TAKE_HELPFUL_PERIODIC",
    PROC_FLAG_2_TARGET_DIES: "2_TARGET_DIES",
    PROC_FLAG_2_KNOCKBACK: "2_KNOCKBACK",
    PROC_FLAG_2_CAST_SUCCESSFUL: "2_CAST_SUCCESSFUL",
    PROC_FLAG_2_SUCCESSFUL_DISPEL: "2_SUCCESSFUL_DISPEL",
    PROC_FLAG_2_DO_EMOTE: "2_DO_EMOTE",
}
KNOWN_PROC_FLAGS = 0
for _bit in PROC_FLAG_NAMES:
    KNOWN_PROC_FLAGS |= _bit

# Masks, verbatim from the ProcFlags enum body.
AUTO_ATTACK_PROC_FLAG_MASK = (
    PROC_FLAG_DEAL_MELEE_SWING | PROC_FLAG_TAKE_MELEE_SWING
    | PROC_FLAG_DEAL_RANGED_ATTACK | PROC_FLAG_TAKE_RANGED_ATTACK)

MELEE_PROC_FLAG_MASK = (
    PROC_FLAG_DEAL_MELEE_SWING | PROC_FLAG_TAKE_MELEE_SWING
    | PROC_FLAG_DEAL_MELEE_ABILITY | PROC_FLAG_TAKE_MELEE_ABILITY
    | PROC_FLAG_MAIN_HAND_WEAPON_SWING | PROC_FLAG_OFF_HAND_WEAPON_SWING)

RANGED_PROC_FLAG_MASK = (
    PROC_FLAG_DEAL_RANGED_ATTACK | PROC_FLAG_TAKE_RANGED_ATTACK
    | PROC_FLAG_DEAL_RANGED_ABILITY | PROC_FLAG_TAKE_RANGED_ABILITY)

SPELL_PROC_FLAG_MASK = (
    PROC_FLAG_DEAL_MELEE_ABILITY | PROC_FLAG_TAKE_MELEE_ABILITY
    | PROC_FLAG_DEAL_RANGED_ATTACK | PROC_FLAG_TAKE_RANGED_ATTACK
    | PROC_FLAG_DEAL_RANGED_ABILITY | PROC_FLAG_TAKE_RANGED_ABILITY
    | PROC_FLAG_DEAL_HELPFUL_ABILITY | PROC_FLAG_TAKE_HELPFUL_ABILITY
    | PROC_FLAG_DEAL_HARMFUL_ABILITY | PROC_FLAG_TAKE_HARMFUL_ABILITY
    | PROC_FLAG_DEAL_HELPFUL_SPELL | PROC_FLAG_TAKE_HELPFUL_SPELL
    | PROC_FLAG_DEAL_HARMFUL_SPELL | PROC_FLAG_TAKE_HARMFUL_SPELL
    | PROC_FLAG_DEAL_HARMFUL_PERIODIC | PROC_FLAG_TAKE_HARMFUL_PERIODIC
    | PROC_FLAG_DEAL_HELPFUL_PERIODIC | PROC_FLAG_TAKE_HELPFUL_PERIODIC)

DONE_HIT_PROC_FLAG_MASK = (
    PROC_FLAG_DEAL_MELEE_SWING | PROC_FLAG_DEAL_RANGED_ATTACK
    | PROC_FLAG_DEAL_MELEE_ABILITY | PROC_FLAG_DEAL_RANGED_ABILITY
    | PROC_FLAG_DEAL_HELPFUL_ABILITY | PROC_FLAG_DEAL_HARMFUL_ABILITY
    | PROC_FLAG_DEAL_HELPFUL_SPELL | PROC_FLAG_DEAL_HARMFUL_SPELL
    | PROC_FLAG_DEAL_HARMFUL_PERIODIC | PROC_FLAG_DEAL_HELPFUL_PERIODIC
    | PROC_FLAG_MAIN_HAND_WEAPON_SWING | PROC_FLAG_OFF_HAND_WEAPON_SWING)

TAKEN_HIT_PROC_FLAG_MASK = (
    PROC_FLAG_TAKE_MELEE_SWING | PROC_FLAG_TAKE_RANGED_ATTACK
    | PROC_FLAG_TAKE_MELEE_ABILITY | PROC_FLAG_TAKE_RANGED_ABILITY
    | PROC_FLAG_TAKE_HELPFUL_ABILITY | PROC_FLAG_TAKE_HARMFUL_ABILITY
    | PROC_FLAG_TAKE_HELPFUL_SPELL | PROC_FLAG_TAKE_HARMFUL_SPELL
    | PROC_FLAG_TAKE_HARMFUL_PERIODIC | PROC_FLAG_TAKE_HELPFUL_PERIODIC
    | PROC_FLAG_TAKE_ANY_DAMAGE)

REQ_SPELL_PHASE_PROC_FLAG_MASK = SPELL_PROC_FLAG_MASK & DONE_HIT_PROC_FLAG_MASK

#: SpellMgr.h ``#define MELEE_BASED_TRIGGER_MASK`` -- consumed only by
#: Unit::ProcSkillsAndReactives (aura-state side effects, not aura procs).
MELEE_BASED_TRIGGER_MASK = (
    PROC_FLAG_DEAL_MELEE_SWING | PROC_FLAG_TAKE_MELEE_SWING
    | PROC_FLAG_DEAL_MELEE_ABILITY | PROC_FLAG_TAKE_MELEE_ABILITY
    | PROC_FLAG_DEAL_RANGED_ATTACK | PROC_FLAG_TAKE_RANGED_ATTACK
    | PROC_FLAG_DEAL_RANGED_ABILITY | PROC_FLAG_TAKE_RANGED_ABILITY)

#: The literal set Unit/SpellMgr.cpp's default generation tests against
#: SPELL_ATTR3_CAN_PROC_FROM_PROCS (``LoadSpellProcs``, infinite-loop guard).
INFINITE_LOOP_GUARD_FLAGS = (
    PROC_FLAG_DEAL_MELEE_ABILITY | PROC_FLAG_DEAL_RANGED_ATTACK
    | PROC_FLAG_DEAL_RANGED_ABILITY | PROC_FLAG_DEAL_HELPFUL_ABILITY
    | PROC_FLAG_DEAL_HARMFUL_ABILITY | PROC_FLAG_DEAL_HELPFUL_SPELL
    | PROC_FLAG_DEAL_HARMFUL_SPELL | PROC_FLAG_DEAL_HARMFUL_PERIODIC
    | PROC_FLAG_DEAL_HELPFUL_PERIODIC)

#: CanSpellTriggerProcOnEvent: "always trigger for these types".
ALWAYS_TRIGGER_EVENT_FLAGS = PROC_FLAG_HEARTBEAT | PROC_FLAG_KILL | PROC_FLAG_DEATH


def proc_flags_from_words(word0: int, word1: int) -> int:
    """``ProcFlagsInit`` from the two signed DB2 ints -> one unsigned 64-bit value."""
    return (int(word0) & 0xFFFFFFFF) | ((int(word1) & 0xFFFFFFFF) << 32)


def proc_flags_words(value: int) -> tuple[int, int]:
    return value & 0xFFFFFFFF, (value >> 32) & 0xFFFFFFFF


# ---------------------------------------------------------------------------
# ProcFlagsSpellType / SpellPhase / Hit / ProcAttributes -- SpellMgr.h
# ---------------------------------------------------------------------------

PROC_SPELL_TYPE_NONE = 0x0
PROC_SPELL_TYPE_DAMAGE = 0x1
PROC_SPELL_TYPE_HEAL = 0x2
PROC_SPELL_TYPE_NO_DMG_HEAL = 0x4
PROC_SPELL_TYPE_MASK_ALL = 0x7
SPELL_TYPE_NAMES = {0x1: "DAMAGE", 0x2: "HEAL", 0x4: "NO_DMG_HEAL"}

PROC_SPELL_PHASE_NONE = 0x0
PROC_SPELL_PHASE_CAST = 0x1
PROC_SPELL_PHASE_HIT = 0x2
PROC_SPELL_PHASE_FINISH = 0x4
PROC_SPELL_PHASE_MASK_ALL = 0x7
SPELL_PHASE_NAMES = {0x1: "CAST", 0x2: "HIT", 0x4: "FINISH"}

PROC_HIT_NONE = 0x0
PROC_HIT_NORMAL = 0x0000001
PROC_HIT_CRITICAL = 0x0000002
PROC_HIT_MISS = 0x0000004
PROC_HIT_FULL_RESIST = 0x0000008
PROC_HIT_DODGE = 0x0000010
PROC_HIT_PARRY = 0x0000020
PROC_HIT_BLOCK = 0x0000040
PROC_HIT_EVADE = 0x0000080
PROC_HIT_IMMUNE = 0x0000100
PROC_HIT_DEFLECT = 0x0000200
PROC_HIT_ABSORB = 0x0000400
PROC_HIT_REFLECT = 0x0000800
PROC_HIT_INTERRUPT = 0x0001000
PROC_HIT_FULL_BLOCK = 0x0002000
PROC_HIT_DISPEL = 0x0004000
PROC_HIT_MASK_ALL = 0x0007FFF
HIT_NAMES = {
    PROC_HIT_NORMAL: "NORMAL", PROC_HIT_CRITICAL: "CRITICAL", PROC_HIT_MISS: "MISS",
    PROC_HIT_FULL_RESIST: "FULL_RESIST", PROC_HIT_DODGE: "DODGE",
    PROC_HIT_PARRY: "PARRY", PROC_HIT_BLOCK: "BLOCK", PROC_HIT_EVADE: "EVADE",
    PROC_HIT_IMMUNE: "IMMUNE", PROC_HIT_DEFLECT: "DEFLECT",
    PROC_HIT_ABSORB: "ABSORB", PROC_HIT_REFLECT: "REFLECT",
    PROC_HIT_INTERRUPT: "INTERRUPT", PROC_HIT_FULL_BLOCK: "FULL_BLOCK",
    PROC_HIT_DISPEL: "DISPEL",
}

PROC_ATTR_NONE = 0x0
PROC_ATTR_REQ_EXP_OR_HONOR = 0x0000001
PROC_ATTR_TRIGGERED_CAN_PROC = 0x0000002
PROC_ATTR_REQ_POWER_COST = 0x0000004
PROC_ATTR_REQ_SPELLMOD = 0x0000008
PROC_ATTR_USE_STACKS_FOR_CHARGES = 0x0000010
PROC_ATTR_REDUCE_PROC_60 = 0x0000080
PROC_ATTR_CANT_PROC_FROM_ITEM_CAST = 0x0000100
PROC_ATTR_NAMES = {
    PROC_ATTR_REQ_EXP_OR_HONOR: "REQ_EXP_OR_HONOR",
    PROC_ATTR_TRIGGERED_CAN_PROC: "TRIGGERED_CAN_PROC",
    PROC_ATTR_REQ_POWER_COST: "REQ_POWER_COST",
    PROC_ATTR_REQ_SPELLMOD: "REQ_SPELLMOD",
    PROC_ATTR_USE_STACKS_FOR_CHARGES: "USE_STACKS_FOR_CHARGES",
    PROC_ATTR_REDUCE_PROC_60: "REDUCE_PROC_60",
    PROC_ATTR_CANT_PROC_FROM_ITEM_CAST: "CANT_PROC_FROM_ITEM_CAST",
}
#: SpellMgr.h ``#define PROC_ATTR_ALL_ALLOWED``; LoadSpellProcs masks the rest off.
PROC_ATTR_ALL_ALLOWED = (
    PROC_ATTR_REQ_EXP_OR_HONOR | PROC_ATTR_TRIGGERED_CAN_PROC
    | PROC_ATTR_REQ_POWER_COST | PROC_ATTR_REQ_SPELLMOD
    | PROC_ATTR_USE_STACKS_FOR_CHARGES | PROC_ATTR_REDUCE_PROC_60
    | PROC_ATTR_CANT_PROC_FROM_ITEM_CAST)

# EnchantProcAttributes -- SpellMgr.h (spell_enchant_proc_data.AttributesMask)
ENCHANT_PROC_ATTR_WHITE_HIT = 0x1
ENCHANT_PROC_ATTR_LIMIT_60 = 0x2
ENCHANT_PROC_ATTR_NAMES = {0x1: "WHITE_HIT", 0x2: "LIMIT_60"}

# SpellSchoolMask -- SharedDefines.h
SPELL_SCHOOL_MASK_ALL = 0x7F
SCHOOL_NAMES = {0x1: "PHYSICAL", 0x2: "HOLY", 0x4: "FIRE", 0x8: "NATURE",
                0x10: "FROST", 0x20: "SHADOW", 0x40: "ARCANE"}

# ---------------------------------------------------------------------------
# SpellProcsPerMinuteModType -- src/server/game/DataStores/DBCEnums.h
# ---------------------------------------------------------------------------
SPELL_PPM_MOD_HASTE = 1
SPELL_PPM_MOD_CRIT = 2
SPELL_PPM_MOD_CLASS = 3
SPELL_PPM_MOD_SPEC = 4
SPELL_PPM_MOD_RACE = 5
SPELL_PPM_MOD_ITEM_LEVEL = 6
SPELL_PPM_MOD_BATTLEGROUND = 7
SPELL_PPM_MOD_AURA = 8
PPM_MOD_NAMES = {1: "HASTE", 2: "CRIT", 3: "CLASS", 4: "SPEC", 5: "RACE",
                 6: "ITEM_LEVEL", 7: "BATTLEGROUND", 8: "AURA"}

# ---------------------------------------------------------------------------
# Spell attribute bits the proc pipeline reads.  Resolved *by Trinity name*
# from the generated SharedDefines table, as (word, bit) where word indexes
# SpellMisc.Attributes_<word>.
# ---------------------------------------------------------------------------
_ATTR_BY_NAME = {name: key for key, name in SPELL_ATTR_NAMES.items()}


def attr(name: str) -> tuple[int, int]:
    try:
        return _ATTR_BY_NAME[name]
    except KeyError:
        raise KeyError(f"{name} is not in SharedDefines.h at {TRINITY_COMMIT}") from None


def attr_name(key: tuple[int, int]) -> str:
    return SPELL_ATTR_NAMES.get(key, f"SPELL_ATTR{key[0]}_UNNAMED_0x{key[1]:08X}")


ATTR0_PROC_FAILURE_BURNS_CHARGE = attr("SPELL_ATTR0_PROC_FAILURE_BURNS_CHARGE")
ATTR0_IS_ABILITY = attr("SPELL_ATTR0_IS_ABILITY")
ATTR0_PASSIVE = attr("SPELL_ATTR0_PASSIVE")
ATTR0_WEARER_CASTS_PROC_TRIGGER = attr("SPELL_ATTR0_WEARER_CASTS_PROC_TRIGGER")
ATTR0_CANCELS_AUTO_ATTACK_COMBAT = attr("SPELL_ATTR0_CANCELS_AUTO_ATTACK_COMBAT")
ATTR2_AUTO_REPEAT = attr("SPELL_ATTR2_AUTO_REPEAT")
ATTR2_PROC_COOLDOWN_ON_FAILURE = attr("SPELL_ATTR2_PROC_COOLDOWN_ON_FAILURE")
ATTR3_NO_PROC_EQUIP_REQUIREMENT = attr("SPELL_ATTR3_NO_PROC_EQUIP_REQUIREMENT")
ATTR3_NOT_A_PROC = attr("SPELL_ATTR3_NOT_A_PROC")
ATTR3_SUPPRESS_CASTER_PROCS = attr("SPELL_ATTR3_SUPPRESS_CASTER_PROCS")
ATTR3_SUPPRESS_TARGET_PROCS = attr("SPELL_ATTR3_SUPPRESS_TARGET_PROCS")
ATTR3_INSTANT_TARGET_PROCS = attr("SPELL_ATTR3_INSTANT_TARGET_PROCS")
ATTR3_ONLY_PROC_OUTDOORS = attr("SPELL_ATTR3_ONLY_PROC_OUTDOORS")
ATTR3_TREAT_AS_PERIODIC = attr("SPELL_ATTR3_TREAT_AS_PERIODIC")
ATTR3_CAN_PROC_FROM_PROCS = attr("SPELL_ATTR3_CAN_PROC_FROM_PROCS")
ATTR3_ONLY_PROC_ON_CASTER = attr("SPELL_ATTR3_ONLY_PROC_ON_CASTER")
ATTR4_CLASS_TRIGGER_ONLY_ON_TARGET = attr("SPELL_ATTR4_CLASS_TRIGGER_ONLY_ON_TARGET")
ATTR4_REACTIVE_DAMAGE_PROC = attr("SPELL_ATTR4_REACTIVE_DAMAGE_PROC")
ATTR4_ALLOW_PROC_WHILE_SITTING = attr("SPELL_ATTR4_ALLOW_PROC_WHILE_SITTING")
ATTR4_PROC_SUPPRESS_SWING_ANIM = attr("SPELL_ATTR4_PROC_SUPPRESS_SWING_ANIM")
ATTR4_SUPPRESS_WEAPON_PROCS = attr("SPELL_ATTR4_SUPPRESS_WEAPON_PROCS")
ATTR6_DO_NOT_CONSUME_RESOURCES = attr("SPELL_ATTR6_DO_NOT_CONSUME_RESOURCES")
ATTR6_AURA_IS_WEAPON_PROC = attr("SPELL_ATTR6_AURA_IS_WEAPON_PROC")
ATTR7_CAN_PROC_FROM_SUPPRESSED_TARGET_PROCS = attr("SPELL_ATTR7_CAN_PROC_FROM_SUPPRESSED_TARGET_PROCS")
ATTR8_TARGET_PROCS_ON_CASTER = attr("SPELL_ATTR8_TARGET_PROCS_ON_CASTER")
ATTR12_ENABLE_PROCS_FROM_SUPPRESSED_CASTER_PROCS = attr("SPELL_ATTR12_ENABLE_PROCS_FROM_SUPPRESSED_CASTER_PROCS")
ATTR12_CAN_PROC_FROM_SUPPRESSED_CASTER_PROCS = attr("SPELL_ATTR12_CAN_PROC_FROM_SUPPRESSED_CASTER_PROCS")
ATTR12_ONLY_PROC_FROM_CLASS_ABILITIES = attr("SPELL_ATTR12_ONLY_PROC_FROM_CLASS_ABILITIES")
ATTR13_ALLOW_CLASS_ABILITY_PROCS = attr("SPELL_ATTR13_ALLOW_CLASS_ABILITY_PROCS")

#: Attribute bits with a *direct* consumer in the proc pipeline, and the role
#: that consumer gives them.  "provider" = read on the proccing aura's spell;
#: "event" = read on the spell that produced the event.  Attributes that only
#: carry a TITLE in SharedDefines and have no pipeline consumer are listed in
#: UNCONSUMED_PROC_NAMED_ATTRIBUTES instead.
PROC_ATTRIBUTE_CONSUMERS: dict[tuple[int, int], tuple[str, str, str]] = {
    ATTR0_PROC_FAILURE_BURNS_CHARGE: (
        "provider", "suppression/charges",
        "Unit::GetProcAurasTriggeredOnEvent: failed GetProcEffectMask still drops a charge"),
    ATTR2_PROC_COOLDOWN_ON_FAILURE: (
        "provider", "cooldown",
        "Unit::GetProcAurasTriggeredOnEvent: failed GetProcEffectMask still starts the proc cooldown"),
    ATTR3_CAN_PROC_FROM_PROCS: (
        "provider", "suppression",
        "Aura::GetProcEffectMask: provider may proc from triggered spells; "
        "SpellMgr::LoadSpellProcs: infinite-loop guard refuses generation"),
    ATTR3_NOT_A_PROC: (
        "event", "suppression",
        "Aura::GetProcEffectMask: triggered event spell still counts as a proc source"),
    ATTR3_SUPPRESS_CASTER_PROCS: (
        "event", "suppression",
        "Aura::GetProcEffectMask (non-taken events); Unit::ProcSkillsAndAuras (reactives only)"),
    ATTR3_SUPPRESS_TARGET_PROCS: (
        "event", "suppression",
        "Aura::GetProcEffectMask (taken events); Unit::ProcSkillsAndAuras (reactives only)"),
    ATTR7_CAN_PROC_FROM_SUPPRESSED_TARGET_PROCS: (
        "provider", "suppression",
        "Aura::GetProcEffectMask: ignore SUPPRESS_TARGET_PROCS"),
    ATTR12_ENABLE_PROCS_FROM_SUPPRESSED_CASTER_PROCS: (
        "event", "suppression",
        "Aura::GetProcEffectMask: event spell re-enables its own suppressed caster procs"),
    ATTR12_CAN_PROC_FROM_SUPPRESSED_CASTER_PROCS: (
        "provider", "suppression",
        "Aura::GetProcEffectMask: ignore SUPPRESS_CASTER_PROCS"),
    ATTR4_SUPPRESS_WEAPON_PROCS: (
        "event", "suppression",
        "Aura::GetProcEffectMask (with provider ATTR6_AURA_IS_WEAPON_PROC); "
        "Spell::TargetInfo::DoDamageAndTriggers (no CastItemCombatSpell)"),
    ATTR6_AURA_IS_WEAPON_PROC: (
        "provider", "suppression",
        "Aura::GetProcEffectMask: suppressed by event ATTR4_SUPPRESS_WEAPON_PROCS"),
    ATTR12_ONLY_PROC_FROM_CLASS_ABILITIES: (
        "provider", "event-matching",
        "Aura::GetProcEffectMask: event spell must carry ATTR13_ALLOW_CLASS_ABILITY_PROCS"),
    ATTR13_ALLOW_CLASS_ABILITY_PROCS: (
        "event", "event-matching",
        "Aura::GetProcEffectMask: pairs with provider ATTR12_ONLY_PROC_FROM_CLASS_ABILITIES"),
    ATTR3_NO_PROC_EQUIP_REQUIREMENT: (
        "provider", "actor-applicability",
        "Aura::GetProcEffectMask: skip passive equipped-item requirement"),
    ATTR3_ONLY_PROC_OUTDOORS: (
        "provider", "actor-applicability", "Aura::GetProcEffectMask: target->IsOutdoors()"),
    ATTR3_ONLY_PROC_ON_CASTER: (
        "provider", "actor-applicability",
        "Aura::GetProcEffectMask: aura target GUID == aura caster GUID"),
    ATTR4_ALLOW_PROC_WHILE_SITTING: (
        "provider", "actor-applicability", "Aura::GetProcEffectMask: skip IsStandState()"),
    ATTR6_DO_NOT_CONSUME_RESOURCES: (
        "event", "charges",
        "Aura::PrepareProcChargeDrop: event spell with this attribute does not drop a charge"),
    ATTR8_TARGET_PROCS_ON_CASTER: (
        "provider", "target",
        "AuraEffect::HandleProcTriggerSpell[WithValue]AuraProc: taken procs target the actor"),
    ATTR3_TREAT_AS_PERIODIC: (
        "event", "event-identity",
        "Spell::FinalizeDataForTriggerSystem: spell hits report *_PERIODIC flags"),
    ATTR0_IS_ABILITY: (
        "event", "event-identity",
        "Spell::FinalizeDataForTriggerSystem: DmgClass NONE/MAGIC spell reports *_ABILITY flags"),
    ATTR2_AUTO_REPEAT: (
        "event", "event-identity",
        "Spell::prepareDataForTriggerSystem: ranged/wand auto-repeat reports *_RANGED_ATTACK"),
    ATTR0_CANCELS_AUTO_ATTACK_COMBAT: (
        "event", "suppression",
        "Spell::TargetInfo::DoDamageAndTriggers: no CastItemCombatSpell"),
    ATTR4_CLASS_TRIGGER_ONLY_ON_TARGET: (
        "provider", "target",
        "Spell::CanExecuteTriggersOnHit (SPELL_AURA_ADD_TARGET_TRIGGER path only)"),
}

#: Named proc-looking attributes whose only Trinity use is outside the aura
#: proc pipeline (or nowhere).  Reported, never given semantics here.
UNCONSUMED_PROC_NAMED_ATTRIBUTES: dict[tuple[int, int], str] = {
    ATTR0_WEARER_CASTS_PROC_TRIGGER: "no consumer (SharedDefines: 'Just a marker attribute')",
    ATTR3_INSTANT_TARGET_PROCS: "no consumer in this checkout",
    ATTR4_REACTIVE_DAMAGE_PROC: "Unit::DealDamage aura-interrupt only, not proc eligibility",
    ATTR4_PROC_SUPPRESS_SWING_ANIM: "no consumer in this checkout",
}

# ---------------------------------------------------------------------------
# Aura types -- src/server/game/Spells/Auras/SpellAuraDefines.h, resolved by
# name through the charstats transcription of the same checkout.
# ---------------------------------------------------------------------------
_AURA_BY_NAME = {name: key for key, name in AURA_TYPE_NAMES.items()}


def aura(name: str) -> int:
    try:
        return _AURA_BY_NAME["SPELL_AURA_" + name]
    except KeyError:
        raise KeyError(f"SPELL_AURA_{name} is not in SpellAuraDefines.h") from None


def aura_name(value: int) -> str:
    return AURA_TYPE_NAMES.get(int(value), f"AURA_{value}")


SPELL_AURA_DUMMY = aura("DUMMY")
SPELL_AURA_PERIODIC_DUMMY = aura("PERIODIC_DUMMY")
SPELL_AURA_PROC_TRIGGER_SPELL = aura("PROC_TRIGGER_SPELL")
SPELL_AURA_PROC_TRIGGER_DAMAGE = aura("PROC_TRIGGER_DAMAGE")
SPELL_AURA_PROC_TRIGGER_SPELL_WITH_VALUE = aura("PROC_TRIGGER_SPELL_WITH_VALUE")
SPELL_AURA_PERIODIC_TRIGGER_SPELL = aura("PERIODIC_TRIGGER_SPELL")
SPELL_AURA_ADD_TARGET_TRIGGER = aura("ADD_TARGET_TRIGGER")
SPELL_AURA_SPELL_MAGNET = aura("SPELL_MAGNET")
SPELL_AURA_MOD_STEALTH = aura("MOD_STEALTH")
SPELL_AURA_MOD_HIT_CHANCE = aura("MOD_HIT_CHANCE")
SPELL_AURA_REFLECT_SPELLS = aura("REFLECT_SPELLS")
SPELL_AURA_REFLECT_SPELLS_SCHOOL = aura("REFLECT_SPELLS_SCHOOL")
SPELL_AURA_MOD_WEAPON_CRIT_PERCENT = aura("MOD_WEAPON_CRIT_PERCENT")
SPELL_AURA_MOD_BLOCK_PERCENT = aura("MOD_BLOCK_PERCENT")
SPELL_AURA_ADD_FLAT_MODIFIER = aura("ADD_FLAT_MODIFIER")
SPELL_AURA_ADD_PCT_MODIFIER = aura("ADD_PCT_MODIFIER")
SPELL_AURA_ADD_FLAT_MODIFIER_BY_SPELL_LABEL = aura("ADD_FLAT_MODIFIER_BY_SPELL_LABEL")
SPELL_AURA_ADD_PCT_MODIFIER_BY_SPELL_LABEL = aura("ADD_PCT_MODIFIER_BY_SPELL_LABEL")
SPELL_AURA_IGNORE_SPELL_COOLDOWN = aura("IGNORE_SPELL_COOLDOWN")

#: SpellMgr::LoadSpellProcs ``isTriggerAura[]`` -- in source order.
TRIGGER_AURA_NAMES = (
    "DUMMY", "PERIODIC_DUMMY", "MOD_CONFUSE", "MOD_THREAT", "MOD_STUN",
    "MOD_DAMAGE_DONE", "MOD_DAMAGE_TAKEN", "MOD_RESISTANCE", "MOD_STEALTH",
    "MOD_FEAR", "MOD_ROOT", "TRANSFORM", "REFLECT_SPELLS", "DAMAGE_IMMUNITY",
    "PROC_TRIGGER_SPELL", "PROC_TRIGGER_DAMAGE", "MOD_CASTING_SPEED_NOT_STACK",
    "SCHOOL_ABSORB", "MOD_POWER_COST_SCHOOL_PCT", "MOD_POWER_COST_SCHOOL",
    "REFLECT_SPELLS_SCHOOL", "MECHANIC_IMMUNITY", "MOD_DAMAGE_PERCENT_TAKEN",
    "SPELL_MAGNET", "MOD_ATTACK_POWER", "MOD_POWER_REGEN_PERCENT",
    "INTERCEPT_MELEE_RANGED_ATTACKS", "OVERRIDE_CLASS_SCRIPTS",
    "MOD_MECHANIC_RESISTANCE", "RANGED_ATTACK_POWER_ATTACKER_BONUS",
    "MOD_MELEE_HASTE", "MOD_MELEE_HASTE_3", "MOD_ATTACKER_MELEE_HIT_CHANCE",
    "PROC_TRIGGER_SPELL_WITH_VALUE", "MOD_SCHOOL_MASK_DAMAGE_FROM_CASTER",
    "MOD_SPELL_DAMAGE_FROM_CASTER", "MOD_SPELL_CRIT_CHANCE",
    "ABILITY_IGNORE_AURASTATE", "MOD_INVISIBILITY", "FORCE_REACTION",
    "MOD_TAUNT", "MOD_DETAUNT", "MOD_DAMAGE_PERCENT_DONE",
    "MOD_ATTACK_POWER_PCT", "MOD_HIT_CHANCE", "MOD_WEAPON_CRIT_PERCENT",
    "MOD_BLOCK_PERCENT", "MOD_ROOT_2", "IGNORE_SPELL_COOLDOWN",
)
#: ``isAlwaysTriggeredAura[]`` (MOD_STEALTH is listed twice in source).
ALWAYS_TRIGGERED_AURA_NAMES = (
    "OVERRIDE_CLASS_SCRIPTS", "MOD_STEALTH", "MOD_CONFUSE", "MOD_FEAR",
    "MOD_ROOT", "MOD_STUN", "TRANSFORM", "MOD_INVISIBILITY", "SPELL_MAGNET",
    "SCHOOL_ABSORB", "MOD_STEALTH", "MOD_ROOT_2",
)
#: ``spellTypeMask[]`` -- every aura not listed defaults to PROC_SPELL_TYPE_MASK_ALL.
AURA_SPELL_TYPE_MASK_NAMES = {
    "MOD_STEALTH": PROC_SPELL_TYPE_DAMAGE | PROC_SPELL_TYPE_NO_DMG_HEAL,
    "MOD_CONFUSE": PROC_SPELL_TYPE_DAMAGE,
    "MOD_FEAR": PROC_SPELL_TYPE_DAMAGE,
    "MOD_ROOT": PROC_SPELL_TYPE_DAMAGE,
    "MOD_ROOT_2": PROC_SPELL_TYPE_DAMAGE,
    "MOD_STUN": PROC_SPELL_TYPE_DAMAGE,
    "TRANSFORM": PROC_SPELL_TYPE_DAMAGE,
    "MOD_INVISIBILITY": PROC_SPELL_TYPE_DAMAGE,
}
TRIGGER_AURAS = frozenset(aura(n) for n in TRIGGER_AURA_NAMES)
ALWAYS_TRIGGERED_AURAS = frozenset(aura(n) for n in ALWAYS_TRIGGERED_AURA_NAMES)
AURA_SPELL_TYPE_MASK = {aura(n): m for n, m in AURA_SPELL_TYPE_MASK_NAMES.items()}

#: AuraEffect::HandleProc -- aura types with a generic proc action.
HANDLE_PROC_ACTIONS = {
    aura("MOD_CONFUSE"): "breakable-cc: reduce amount by damage, remove at <= 0",
    aura("MOD_FEAR"): "breakable-cc: reduce amount by damage, remove at <= 0",
    aura("MOD_STUN"): "breakable-cc: reduce amount by damage, remove at <= 0",
    aura("MOD_ROOT"): "breakable-cc: reduce amount by damage, remove at <= 0",
    aura("TRANSFORM"): "breakable-cc: reduce amount by damage, remove at <= 0",
    aura("MOD_ROOT_2"): "breakable-cc: reduce amount by damage, remove at <= 0",
    SPELL_AURA_DUMMY: "trigger-spell: cast EffectTriggerSpell if nonzero and it exists",
    SPELL_AURA_PROC_TRIGGER_SPELL: "trigger-spell: cast EffectTriggerSpell",
    SPELL_AURA_PROC_TRIGGER_SPELL_WITH_VALUE: "trigger-spell-with-value: cast EffectTriggerSpell with BasePoint0 = aura amount",
    SPELL_AURA_PROC_TRIGGER_DAMAGE: "direct-damage: deal aura amount as spell damage to the other party",
}
TRIGGER_SPELL_HANDLERS = frozenset({
    SPELL_AURA_DUMMY, SPELL_AURA_PROC_TRIGGER_SPELL,
    SPELL_AURA_PROC_TRIGGER_SPELL_WITH_VALUE})

#: AuraEffect::CheckEffectProc -- aura types with an extra per-effect gate.
CHECK_EFFECT_PROC_GATES = {
    aura("MOD_CONFUSE"): "needs DamageInfo with damage > 0; own spell at full duration is ignored",
    aura("MOD_FEAR"): "needs DamageInfo with damage > 0; own spell at full duration is ignored",
    aura("MOD_STUN"): "needs DamageInfo with damage > 0; own spell at full duration is ignored",
    aura("MOD_ROOT"): "needs DamageInfo with damage > 0; own spell at full duration is ignored",
    aura("TRANSFORM"): "needs DamageInfo with damage > 0; own spell at full duration is ignored",
    aura("MECHANIC_IMMUNITY"): "event spell mechanic mask must contain MiscValue",
    aura("MOD_MECHANIC_RESISTANCE"): "event spell mechanic mask must contain MiscValue",
    aura("MOD_CASTING_SPEED_NOT_STACK"): "event must be a Spell with nonzero cast time",
    aura("MOD_SCHOOL_MASK_DAMAGE_FROM_CASTER"): "event actor must be the aura caster",
    aura("MOD_SPELL_DAMAGE_FROM_CASTER"): "event actor must be the aura caster",
    aura("MOD_POWER_COST_SCHOOL"): "event spell school & MiscValue, and a positive power cost",
    aura("MOD_POWER_COST_SCHOOL_PCT"): "event spell school & MiscValue, and a positive power cost",
    aura("REFLECT_SPELLS_SCHOOL"): "event spell school & MiscValue",
    SPELL_AURA_PROC_TRIGGER_SPELL: "trigger spell with ADD_EXTRA_ATTACKS may not re-proc from itself",
    SPELL_AURA_PROC_TRIGGER_SPELL_WITH_VALUE: "trigger spell with ADD_EXTRA_ATTACKS may not re-proc from itself",
    aura("MOD_SPELL_CRIT_CHANCE"): "event spell must have SPELL_ATTR0_CU_CAN_CRIT",
}

#: SpellMgr::LoadSpellProcs REQ_SPELLMOD validation list.
SPELLMOD_AURAS = frozenset({
    SPELL_AURA_ADD_FLAT_MODIFIER, SPELL_AURA_ADD_PCT_MODIFIER,
    SPELL_AURA_ADD_FLAT_MODIFIER_BY_SPELL_LABEL,
    SPELL_AURA_ADD_PCT_MODIFIER_BY_SPELL_LABEL, SPELL_AURA_IGNORE_SPELL_COOLDOWN,
})

# ---------------------------------------------------------------------------
# Spell effects -- SharedDefines.h enum SpellEffects, by name.
# ---------------------------------------------------------------------------
_EFFECT_BY_NAME = {name: key for key, name in SPELL_EFFECT_NAMES.items()}


def effect(name: str) -> int:
    return _EFFECT_BY_NAME["SPELL_EFFECT_" + name]


def effect_name(value: int) -> str:
    return SPELL_EFFECT_NAMES.get(int(value), f"EFFECT_{value}")


SPELL_EFFECT_APPLY_AURA = effect("APPLY_AURA")
SPELL_EFFECT_PERSISTENT_AREA_AURA = effect("PERSISTENT_AREA_AURA")
SPELL_EFFECT_APPLY_AURA_ON_PET = effect("APPLY_AURA_ON_PET")
SPELL_EFFECT_ADD_EXTRA_ATTACKS = effect("ADD_EXTRA_ATTACKS")
SPELL_EFFECT_DUMMY = effect("DUMMY")
SPELL_EFFECT_TRIGGER_SPELL = effect("TRIGGER_SPELL")
#: SpellEffectInfo::IsAreaAuraEffect
AREA_AURA_EFFECTS = frozenset(effect(n) for n in (
    "APPLY_AREA_AURA_PARTY", "APPLY_AREA_AURA_RAID", "APPLY_AREA_AURA_FRIEND",
    "APPLY_AREA_AURA_ENEMY", "APPLY_AREA_AURA_PET", "APPLY_AREA_AURA_OWNER",
    "APPLY_AREA_AURA_SUMMONS", "APPLY_AREA_AURA_PARTY_NONRANDOM"))
#: LoadSpellInfoCustomAttributes: effects that set SPELL_ATTR0_CU_CAN_CRIT.
CAN_CRIT_EFFECTS = frozenset(effect(n) for n in (
    "SCHOOL_DAMAGE", "HEALTH_LEECH", "HEAL", "WEAPON_DAMAGE_NOSCHOOL",
    "WEAPON_PERCENT_DAMAGE", "WEAPON_DAMAGE", "POWER_BURN", "HEAL_MECHANICAL",
    "NORMALIZED_WEAPON_DMG", "HEAL_PCT", "DAMAGE_FROM_MAX_HEALTH_PCT"))


def is_unit_owned_aura_effect(effect_type: int) -> bool:
    """Mirrors: ``SpellEffectInfo::IsUnitOwnedAuraEffect``."""
    return (effect_type in AREA_AURA_EFFECTS
            or effect_type in (SPELL_EFFECT_APPLY_AURA, SPELL_EFFECT_APPLY_AURA_ON_PET))


def is_aura_effect(effect_type: int, aura_type: int) -> bool:
    """Mirrors: ``SpellEffectInfo::IsAura``."""
    return ((is_unit_owned_aura_effect(effect_type)
             or effect_type == SPELL_EFFECT_PERSISTENT_AREA_AURA)
            and aura_type != 0)


# ---------------------------------------------------------------------------
# Bit helpers
# ---------------------------------------------------------------------------

def bit_names(value: int, names: dict[int, str]) -> list[str]:
    out = []
    remaining = int(value)
    for bit, name in names.items():
        if remaining & bit == bit and bit:
            out.append(name)
            remaining &= ~bit
    bit = 1
    while remaining:
        if remaining & bit:
            out.append(f"UNKNOWN_0x{bit:X}")
            remaining &= ~bit
        bit <<= 1
    return out


def unknown_bits(value: int, known: int) -> int:
    return int(value) & ~known


def iter_bits(value: int) -> Iterable[int]:
    bit = 1
    value = int(value)
    while value:
        if value & bit:
            yield bit
            value &= ~bit
        bit <<= 1


def proc_flag_names(value: int) -> list[str]:
    return bit_names(value, PROC_FLAG_NAMES)
