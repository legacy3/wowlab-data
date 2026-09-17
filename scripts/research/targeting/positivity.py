"""Load-time spell positivity: ``SpellInfo::NegativeEffects`` / ``IsPositive``.

Targeting reads positivity in ``WorldObject::IsValidAttackTarget`` /
``IsValidAssistTarget`` (Object.cpp:2336, 2494) and in the nearby range choice
(Spell.cpp:1111).  Trinity computes it once at load:

* ``SpellMgr::LoadSpellInfoCorrections`` may pre-set bits (SpellMgr.cpp:5129, 5235);
* ``SpellMgr::LoadSpellInfoCustomAttributes`` (SpellMgr.cpp:3031-3239) iterates
  ``mSpellInfoMap`` -- a ``boost::multi_index`` **hashed_unique** container
  (SpellMgr.cpp:48-64), so the processing order of spells is unspecified -- and calls
  ``SpellInfo::_InitializeSpellPositivity`` (SpellInfo.cpp:5066) for each spell;
* ``_isPositiveEffectImpl`` (SpellInfo.cpp:4618-5063) recurses into *triggered* spells and
  reads their ``NegativeEffects`` / ``IsPositive()``, which are either still the correction
  seed (not yet processed) or final (processed earlier).

Load-order handling (fail closed): every evaluation is run under the two extreme
assumptions -- all other spells unprocessed (seed bits) / all other spells already
processed (final bits, computed recursively with the current spell forced
unprocessed).  More processed spells can only add negative bits and every reader
turns a negative bit into ``false``, so when both extremes agree every mixed order
agrees; otherwise :class:`targeting.FailClosed` (``load-order dependent``).

``CalcValue()`` at load (no caster, SpellInfo.cpp:521-660 / 662-752) is needed only for
its sign and is evaluated lazily: ``Scaling.Variance`` draws ``frand`` at load
(SpellInfo.cpp:560-564) -- accepted only when the variance cannot change the sign
(``|Variance| * 0.5 < 1``); item-level scaling (``ATTR11_SCALES_WITH_ITEM_LEVEL``) fails
closed.  ``tools/tc_target_positivity_probe`` compiles the verbatim implementation.
"""

from __future__ import annotations

import math
import struct
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import cache

# ---------------------------------------------------------------------------
# enum values (pinned 7f3d43b; resolved by Trinity name through procs.enums)
# ---------------------------------------------------------------------------
from procs.enums import attr as _attr
from procs.enums import aura as _aura
from procs.enums import effect as _effect

from . import FailClosed


def _E(*names: str) -> frozenset[int]:
    return frozenset(_effect(n) for n in names)


def _A(*names: str) -> frozenset[int]:
    return frozenset(_aura(n) for n in names)


SPELLFAMILY_GENERIC, SPELLFAMILY_WARRIOR, SPELLFAMILY_ROGUE = 0, 4, 8     # SharedDefines.h:7055-7063
MECHANIC_BANDAGE, MECHANIC_SHIELD, MECHANIC_MOUNT = 16, 19, 21             # SharedDefines.h:2883-2888
MECHANIC_INVULNERABILITY, MECHANIC_IMMUNE_SHIELD = 25, 29                  # SharedDefines.h:2892, 2896
DISPEL_STEALTH, DISPEL_INVISIBILITY, DISPEL_ENRAGE = 5, 6, 9               # SharedDefines.h:2927-2931
POWER_MANA = 0                                                              # SharedDefines.h:295
IS_HARMFUL = 0x1000                                                         # DBCEnums.h:2415
TARGET_CHECK_ENEMY = "ENEMY"
MAX_SPELL_EFFECTS = 32

# SpellDefines.h:152 SpellModOp
SMO = {"HealingAndDamage": 0, "Duration": 1, "Hate": 2, "PointsIndex0": 3, "ChangeCastTime": 10, "Cooldown": 11,
       "PointsIndex1": 12, "PowerCost0": 14, "ChainTargets": 17, "Period": 19, "ChainAmplitude": 20,
       "StartCooldown": 21, "PointsIndex2": 23, "Amplitude": 27, "PowerCostOnMiss": 30, "PointsIndex3": 32,
       "PointsIndex4": 33, "PowerCost1": 34, "PowerCost2": 39, "Points": 8, "CritChance": 7}

# SpellInfo.cpp:4652-4676
GENERIC_NEGATIVE_IDS = frozenset({40268, 61987, 61988, 64412, 72410, 71204})
GENERIC_POSITIVE_IDS = frozenset({24732, 30877, 61716, 61734, 62344, 50344, 61819, 61834, 73523})

OTHER_EFFECT_POSITIVE = _E("HEAL", "LEARN_SPELL", "SKILL_STEP", "HEAL_PCT")                     # 4712-4717
OTHER_AURA_POSITIVE = _A("MOD_STEALTH", "MOD_UNATTACKABLE")                                    # 4731-4733
OTHER_AURA_NEGATIVE = _A("SCHOOL_HEAL_ABSORB", "EMPATHY", "MOD_SPELL_DAMAGE_FROM_CASTER", "PREVENTS_FLEEING")
EFFECT_NEGATIVE = _E("WEAPON_DAMAGE", "WEAPON_DAMAGE_NOSCHOOL", "NORMALIZED_WEAPON_DMG", "WEAPON_PERCENT_DAMAGE",
                     "SCHOOL_DAMAGE", "ENVIRONMENTAL_DAMAGE", "HEALTH_LEECH", "INSTAKILL", "POWER_DRAIN",
                     "STEAL_BENEFICIAL_BUFF", "INTERRUPT_CAST", "PICKPOCKET", "GAMEOBJECT_DAMAGE",
                     "DURABILITY_DAMAGE", "DURABILITY_DAMAGE_PCT", "APPLY_AREA_AURA_ENEMY", "TAMECREATURE",
                     "DISTRACT")                                                                  # 4747-4767
EFFECT_POSITIVE = _E("ENERGIZE", "ENERGIZE_PCT", "HEAL_PCT", "HEAL_MAX_HEALTH", "HEAL_MECHANICAL")  # 4768-4773
EFFECT_CHECK_TARGET = _E("KNOCK_BACK", "CHARGE", "PERSISTENT_AREA_AURA", "ATTACK_ME", "POWER_BURN")  # 4774-4782
AURA_NEG_IF_BP_OR_LEVEL_NEG = _A(
    "MOD_STAT", "MOD_SKILL", "MOD_SKILL_2", "MOD_DODGE_PERCENT", "MOD_HEALING_DONE", "MOD_DAMAGE_DONE_CREATURE",
    "OBS_MOD_HEALTH", "OBS_MOD_POWER", "MOD_CRIT_PCT", "MOD_HIT_CHANCE", "MOD_SPELL_HIT_CHANCE",
    "MOD_SPELL_CRIT_CHANCE", "MOD_RANGED_HASTE", "MOD_MELEE_RANGED_HASTE", "MOD_CASTING_SPEED_NOT_STACK",
    "HASTE_SPELLS", "MOD_RECOVERY_RATE_BY_SPELL_LABEL", "MOD_DETECT_RANGE", "MOD_INCREASE_HEALTH_PERCENT",
    "MOD_TOTAL_STAT_PERCENTAGE", "MOD_INCREASE_SWIM_SPEED", "MOD_PERCENT_STAT", "MOD_INCREASE_HEALTH",
    "MOD_SPEED_ALWAYS")                                                                           # 4826-4851
AURA_NEG_IF_TARGET_OR_BP_NEG = _A(
    "MOD_ATTACKSPEED", "MOD_MELEE_HASTE", "MOD_DAMAGE_DONE", "MOD_RESISTANCE", "MOD_RESISTANCE_PCT", "MOD_RATING",
    "MOD_ATTACK_POWER", "MOD_RANGED_ATTACK_POWER", "MOD_DAMAGE_PERCENT_DONE", "MOD_SPEED_SLOW_ALL", "MELEE_SLOW",
    "MOD_ATTACK_POWER_PCT", "MOD_HEALING_DONE_PERCENT", "MOD_HEALING_PCT")                       # 4852-4868
AURA_NEG_IF_BP_POS = _A("MOD_DAMAGE_TAKEN", "MOD_MELEE_DAMAGE_TAKEN", "MOD_MELEE_DAMAGE_TAKEN_PCT",
                        "MOD_POWER_COST_SCHOOL", "MOD_POWER_COST_SCHOOL_PCT",
                        "MOD_MECHANIC_DAMAGE_TAKEN_PERCENT")                                     # 4869-4877
AURA_PCT_TAKEN = _A("MOD_DAMAGE_PERCENT_TAKEN")
AURA_REGEN_PCT = _A("MOD_HEALTH_REGEN_PERCENT")
AURA_ADD_TARGET_TRIGGER = _A("ADD_TARGET_TRIGGER")
AURA_PERIODIC_TRIGGER_VALUE = _A("PERIODIC_TRIGGER_SPELL_WITH_VALUE", "PERIODIC_TRIGGER_SPELL_FROM_CLIENT")
AURA_CHECK_TARGET = _A(
    "PERIODIC_TRIGGER_SPELL", "MOD_STUN", "TRANSFORM", "MOD_DECREASE_SPEED", "MOD_FEAR", "MOD_TAUNT",
    "MOD_PACIFY", "MOD_PACIFY_SILENCE", "MOD_DISARM", "MOD_DISARM_OFFHAND", "MOD_DISARM_RANGED", "MOD_CHARM",
    "AOE_CHARM", "MOD_POSSESS", "MOD_LANGUAGE", "DAMAGE_SHIELD", "PROC_TRIGGER_SPELL",
    "MOD_ATTACKER_MELEE_HIT_CHANCE", "MOD_ATTACKER_RANGED_HIT_CHANCE", "MOD_ATTACKER_SPELL_HIT_CHANCE",
    "MOD_ATTACKER_MELEE_CRIT_CHANCE", "MOD_ATTACKER_SPELL_AND_WEAPON_CRIT_CHANCE", "DUMMY", "PERIODIC_DUMMY",
    "MOD_HEALING", "MOD_WEAPON_CRIT_PERCENT", "POWER_BURN", "MOD_COOLDOWN", "MOD_CHARGE_RECOVERY_BY_TYPE_MASK",
    "MOD_INCREASE_SPEED", "MOD_PARRY_PERCENT", "SET_VEHICLE_ID", "PERIODIC_ENERGIZE", "EFFECT_IMMUNITY",
    "OVERRIDE_CLASS_SCRIPTS", "MOD_SHAPESHIFT", "MOD_THREAT", "PROC_TRIGGER_SPELL_WITH_VALUE")   # 4916-4957
AURA_NEGATIVE = _A(
    "MOD_CONFUSE", "CHANNEL_DEATH_ITEM", "MOD_ROOT", "MOD_ROOT_2", "MOD_SILENCE", "MOD_DETAUNT", "GHOST",
    "PERIODIC_LEECH", "PERIODIC_MANA_LEECH", "MOD_STALKED", "PREVENT_RESURRECTION", "PERIODIC_DAMAGE",
    "PERIODIC_WEAPON_PERCENT_DAMAGE", "PERIODIC_DAMAGE_PERCENT", "MELEE_ATTACK_POWER_ATTACKER_BONUS",
    "RANGED_ATTACK_POWER_ATTACKER_BONUS")                                                         # 4958-4974
AURA_MECHANIC_IMMUNITY = _A("MECHANIC_IMMUNITY")
AURA_SPELLMOD = _A("ADD_FLAT_MODIFIER", "ADD_PCT_MODIFIER", "ADD_FLAT_MODIFIER_BY_SPELL_LABEL",
                   "ADD_PCT_MODIFIER_BY_SPELL_LABEL")
POST_PASS_AURAS = _A("DUMMY", "MOD_STUN", "MOD_FEAR", "MOD_TAUNT", "TRANSFORM", "MOD_ATTACKSPEED",
                     "MOD_DECREASE_SPEED")                                                         # 5085-5091
EFFECT_THREAT = _E("THREAT", "MODIFY_THREAT_PERCENT")
EFFECT_DISPEL = _E("DISPEL")
EFFECT_DISPEL_MECHANIC = _E("DISPEL_MECHANIC")
EFFECT_INSTAKILL = _effect("INSTAKILL")

# SpellInfo.cpp:607-656 (CalcValue rounding) and 868-956 (GetScalingExpectedStat)
ROUND_EFFECTS = _E("SCHOOL_DAMAGE", "ENVIRONMENTAL_DAMAGE", "HEALTH_LEECH", "HEAL", "WEAPON_DAMAGE_NOSCHOOL",
                   "WEAPON_PERCENT_DAMAGE", "WEAPON_DAMAGE", "HEAL_MAX_HEALTH", "HEAL_MECHANICAL",
                   "NORMALIZED_WEAPON_DMG", "POWER_DRAIN", "ENERGIZE", "POWER_BURN")
AURA_APPLYING_EFFECTS = _E("APPLY_AURA", "PERSISTENT_AREA_AURA", "APPLY_AREA_AURA_PARTY", "APPLY_AREA_AURA_RAID",
                           "APPLY_AREA_AURA_PET", "APPLY_AREA_AURA_FRIEND", "APPLY_AREA_AURA_ENEMY",
                           "APPLY_AREA_AURA_OWNER", "APPLY_AURA_ON_PET", "APPLY_AREA_AURA_SUMMONS")
ROUND_AURAS = _A("PERIODIC_DAMAGE", "PERIODIC_HEAL", "PERIODIC_LEECH", "PERIODIC_HEALTH_FUNNEL",
                 "PERIODIC_WEAPON_PERCENT_DAMAGE", "DAMAGE_SHIELD", "PROC_TRIGGER_DAMAGE", "OBS_MOD_HEALTH",
                 "OBS_MOD_POWER", "PERIODIC_ENERGIZE", "PERIODIC_MANA_LEECH", "PERIODIC_DAMAGE_PERCENT", "POWER_BURN")
STAT_EFFECTS_DAMAGE = _E("SCHOOL_DAMAGE", "ENVIRONMENTAL_DAMAGE", "HEALTH_LEECH", "WEAPON_DAMAGE_NOSCHOOL",
                         "WEAPON_DAMAGE")
STAT_EFFECTS_HEALTH = _E("HEAL", "HEAL_MECHANICAL")
STAT_EFFECTS_MANA_IF = _E("ENERGIZE", "POWER_BURN")
STAT_EFFECTS_MANA = _E("POWER_DRAIN")
STAT_AURA_EFFECTS = AURA_APPLYING_EFFECTS | _E("APPLY_AREA_AURA_PARTY_NONRANDOM")
STAT_AURAS = {
    "CreatureSpellDamage": _A("PERIODIC_DAMAGE", "MOD_DAMAGE_DONE", "DAMAGE_SHIELD", "PROC_TRIGGER_DAMAGE",
                              "PERIODIC_LEECH", "MOD_DAMAGE_DONE_CREATURE", "PERIODIC_HEALTH_FUNNEL",
                              "MOD_MELEE_ATTACK_POWER_VERSUS", "MOD_RANGED_ATTACK_POWER_VERSUS",
                              "MOD_FLAT_SPELL_DAMAGE_VERSUS"),
    "PlayerHealth": _A("PERIODIC_HEAL", "MOD_DAMAGE_TAKEN", "MOD_INCREASE_HEALTH", "SCHOOL_ABSORB", "MOD_REGEN",
                       "MANA_SHIELD", "MOD_HEALING", "MOD_HEALING_DONE", "MOD_HEALTH_REGEN_IN_COMBAT",
                       "MOD_MAX_HEALTH", "MOD_INCREASE_HEALTH_2", "SCHOOL_HEAL_ABSORB"),
    "PlayerMana": _A("PERIODIC_MANA_LEECH"),
    "PlayerPrimaryStat": _A("MOD_STAT", "MOD_ATTACK_POWER", "MOD_RANGED_ATTACK_POWER"),
    "PlayerSecondaryStat": _A("MOD_RATING"),
    "ArmorConstant": _A("MOD_RESISTANCE", "MOD_BASE_RESISTANCE", "MOD_TARGET_RESISTANCE", "MOD_BONUS_ARMOR"),
}
STAT_AURAS_MANA_IF = _A("PERIODIC_ENERGIZE", "MOD_INCREASE_ENERGY", "MOD_POWER_COST_SCHOOL", "MOD_POWER_REGEN",
                        "POWER_BURN", "MOD_MAX_POWER")
#: DB2 ExpectedStat column per ExpectedStatType (DB2Stores.cpp:2506-2582); CREATURE_AUTO_ATTACK replaces the
#: stat under ATTR0_SCALES_WITH_CREATURE_LEVEL (SpellInfo.cpp:730-731)
STAT_COLUMN = {"CreatureSpellDamage": "CreatureSpellDamage", "PlayerHealth": "PlayerHealth",
               "PlayerMana": "PlayerMana", "PlayerPrimaryStat": "PlayerPrimaryStat",
               "PlayerSecondaryStat": "PlayerSecondaryStat", "ArmorConstant": "ArmorConstant",
               "CreatureAutoAttackDps": "CreatureAutoAttackDps"}


def f32(x: float) -> float:
    return struct.unpack("<f", struct.pack("<f", x))[0]


def _round(x: float) -> float:
    """std::round: half away from zero."""
    return math.copysign(math.floor(abs(x) + 0.5), x)


# ---------------------------------------------------------------------------
# spell facts
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class PEffect:
    index: int
    effect: int = 0
    aura: int = 0
    check_a: str = "DEFAULT"          # SpellImplicitTargetInfo::GetCheckType of TargetA
    check_b: str = "DEFAULT"
    target_a: int = 0
    target_b: int = 0
    attributes: int = 0
    real_points_per_level: float = 0.0
    misc0: int = 0
    trigger: int = 0
    base_points: float = 0.0
    coefficient: float = 0.0
    variance: float = 0.0
    scaling_class: int = 0

    @property
    def is_effect(self) -> bool:
        return self.effect != 0

    @property
    def is_aura(self) -> bool:
        """Mirrors: SpellInfo.cpp:465 ``SpellEffectInfo::IsAura``."""
        from procs.enums import is_aura_effect
        return is_aura_effect(self.effect, self.aura)


@dataclass(frozen=True)
class PSpell:
    id: int
    attributes: tuple[int, ...]
    family: int
    family_flags0: int
    mechanic: int
    effects: tuple[PEffect, ...]
    seed: frozenset[int] = frozenset()        # NegativeEffects set by LoadSpellInfoCorrections
    spell_level: int = 0
    base_level: int = 0
    min_scaling_level: int = 0
    max_scaling_level: int = 0
    expansion: int = -2                       # ContentTuning ExpansionID (or -2)
    notes: tuple[str, ...] = field(default_factory=tuple)

    def has_attr(self, name: str) -> bool:
        w, bit = _attr(name)
        return bool(self.attributes[w] & bit)


def _is_positive_target(e: PEffect) -> bool:
    """Mirrors: SpellInfo.cpp:4609 ``_isPositiveTarget``."""
    if not e.is_effect:
        return True
    return e.check_a != TARGET_CHECK_ENEMY and e.check_b != TARGET_CHECK_ENEMY


def scaling_expected_stat(e: PEffect) -> str | None:
    """Mirrors: SpellInfo.cpp:868 ``GetScalingExpectedStat`` (None = ExpectedStatType::None)."""
    if e.effect in STAT_EFFECTS_DAMAGE:
        return "CreatureSpellDamage"
    if e.effect in STAT_EFFECTS_HEALTH:
        return "PlayerHealth"
    if e.effect in STAT_EFFECTS_MANA_IF:
        return "PlayerMana" if e.misc0 == POWER_MANA else None
    if e.effect in STAT_EFFECTS_MANA:
        return "PlayerMana"
    if e.effect in STAT_AURA_EFFECTS:
        for stat, auras in STAT_AURAS.items():
            if e.aura in auras:
                return stat
        if e.aura in STAT_AURAS_MANA_IF:
            return "PlayerMana" if e.misc0 == POWER_MANA else None
    return None


class Loader:
    """Positivity over a spell provider ``get(spell_id) -> PSpell | None``."""

    def __init__(self, get: Callable[[int], PSpell | None], expected_stat: Callable[[str, int], float] | None = None,
                 spell_scaling: Callable[[int, int], float] | None = None, max_depth: int = 6) -> None:
        self.get = get
        self.expected_stat = expected_stat
        self.spell_scaling = spell_scaling
        self.max_depth = max_depth
        self._final: dict[tuple[int, frozenset[int]], frozenset[int]] = {}

    # -- CalcValue at load (sign-exact) -------------------------------------
    def calc_value(self, sp: PSpell, e: PEffect) -> float:
        """Mirrors: SpellInfo.cpp:521 ``CalcValue()`` with caster = nullptr (itemLevel -1)."""
        base = self.calc_base_value(sp, e)
        value = base
        if e.variance:
            delta = abs(f32(e.variance) * 0.5)
            if delta >= 1.0 and base != 0.0:
                raise FailClosed(f"positivity {sp.id}:{e.index}: load-time frand variance {e.variance} can flip the sign")
            # value += base * frand(-delta, delta): sign of the result is the sign of base (delta < 1)
        if e.effect in ROUND_EFFECTS or (e.effect in AURA_APPLYING_EFFECTS and e.aura in ROUND_AURAS):
            if e.variance and base != 0.0 and abs(base) * (1.0 + abs(f32(e.variance) * 0.5)) >= 0.5 > abs(base) * (1.0 - abs(f32(e.variance) * 0.5)):
                raise FailClosed(f"positivity {sp.id}:{e.index}: rounding after load-time variance may reach 0")
            value = _round(value)
        return min(max(value, -2000000000.0), 2000000000.0)

    def calc_base_value(self, sp: PSpell, e: PEffect) -> float:
        """Mirrors: SpellInfo.cpp:662 ``CalcBaseValue(nullptr, nullptr, 0, -1)``."""
        if f32(e.coefficient) != 0.0:
            level = sp.spell_level
            if sp.base_level and not sp.has_attr("SPELL_ATTR11_SCALES_WITH_ITEM_LEVEL") and \
                    sp.has_attr("SPELL_ATTR10_USE_SPELL_BASE_LEVEL_FOR_SCALING"):
                level = sp.base_level
            if sp.min_scaling_level and sp.min_scaling_level > level:
                level = sp.min_scaling_level
            if sp.max_scaling_level and sp.max_scaling_level < level:
                level = sp.max_scaling_level
            value = 0.0
            if level > 0:
                if not e.scaling_class:
                    return 0.0
                if sp.has_attr("SPELL_ATTR11_SCALES_WITH_ITEM_LEVEL"):
                    raise FailClosed(f"positivity {sp.id}:{e.index}: item-level scaling (RandPropPoints) not ported")
                if self.spell_scaling is None:
                    raise FailClosed("positivity: SpellScaling game table not supplied")
                value = self.spell_scaling(level, e.scaling_class)
                # -7 / -6 multiply only with an item (itemId 0 here)
            value = f32(value * f32(e.coefficient))
            if 0.0 < value < 1.0:
                value = 1.0
            if not sp.has_attr("SPELL_ATTR12_USE_FLOAT_VALUES_FOR_SCALING_AMOUNTS"):
                value = _round(value)
            return value
        value = f32(e.base_points)
        stat = scaling_expected_stat(e)
        if stat is not None:
            if sp.has_attr("SPELL_ATTR0_SCALES_WITH_CREATURE_LEVEL"):
                stat = "CreatureAutoAttackDps"
            if self.expected_stat is None:
                raise FailClosed("positivity: ExpectedStat table not supplied")
            ev = self.expected_stat(stat, sp.expansion)       # level 1, contentTuningId 0, CLASS_NONE
            value = f32(f32(ev * value) / 100.0)
            if not sp.has_attr("SPELL_ATTR12_USE_FLOAT_VALUES_FOR_SCALING_AMOUNTS"):
                value = float(_round(value))
        return value

    # -- _isPositiveEffectImpl ---------------------------------------------
    def _neg(self, sid: int, state: dict[int, set[int]], mode: str, later: frozenset[int], depth: int) -> set[int]:
        """NegativeEffects of ``sid`` as seen right now."""
        if sid in state:
            return state[sid]
        sp = self.get(sid)
        if sp is None:
            return set()
        if mode == "unprocessed" or sid in later:
            return set(sp.seed)
        return set(self.negative_effects(sid, later, depth + 1))

    def _impl(self, sp: PSpell, e: PEffect, visited: set, state: dict, mode: str, later: frozenset,
              depth: int) -> bool:
        if not e.is_effect:
            return True
        if e.index in self._neg(sp.id, state, mode, later, depth):          # 4623-4625
            return False
        if sp.has_attr("SPELL_ATTR0_PASSIVE"):                               # 4628
            return True
        if sp.has_attr("SPELL_ATTR0_AURA_IS_DEBUFF"):                        # 4632
            return False
        if sp.has_attr("SPELL_ATTR4_AURA_IS_BUFF"):                          # 4635
            return True
        if e.attributes & IS_HARMFUL:                                        # 4638
            return False
        visited.add((sp.id, e.index))                                        # 4641
        bp_cache: list[float] = []

        def bp() -> float:
            if not bp_cache:
                bp_cache.append(self.calc_value(sp, e))
            return bp_cache[0]
        bp_scale = f32(e.real_points_per_level)
        if sp.family == SPELLFAMILY_GENERIC:                                 # 4648-4677
            if sp.id in GENERIC_NEGATIVE_IDS:
                return False
            if sp.id in GENERIC_POSITIVE_IDS:
                return True
        elif sp.family == SPELLFAMILY_ROGUE:
            if sp.id == 32645:
                return True
            if sp.id == 40251:
                return False
        elif sp.family == SPELLFAMILY_WARRIOR:
            if sp.family_flags0 & 0x20200000:
                return False
        if sp.mechanic == MECHANIC_IMMUNE_SHIELD:                            # 4695-4701
            return True
        if sp.has_attr("SPELL_ATTR1_AURA_UNIQUE"):                           # 4704-4709
            if any(not _is_positive_target(o) for o in sp.effects):
                return False
        for o in sp.effects:                                                 # 4711-4742
            if o.effect in OTHER_EFFECT_POSITIVE:
                return True
            if o.effect == EFFECT_INSTAKILL and o.index != e.index and \
                    o.target_a == e.target_a and o.target_b == e.target_b:
                return False
            if o.is_aura:
                if o.aura in OTHER_AURA_POSITIVE:
                    return True
                if o.aura in OTHER_AURA_NEGATIVE:
                    return False
        if e.effect in EFFECT_NEGATIVE:                                      # 4744-4821
            return False
        if e.effect in EFFECT_POSITIVE:
            return True
        if e.effect in EFFECT_CHECK_TARGET:
            if not _is_positive_target(e):
                return False
        elif e.effect in EFFECT_DISPEL:
            if e.misc0 in (DISPEL_STEALTH, DISPEL_INVISIBILITY, DISPEL_ENRAGE):
                return False
            if not _is_positive_target(e):
                return False
        elif e.effect in EFFECT_DISPEL_MECHANIC:
            if not _is_positive_target(e) and e.misc0 in (MECHANIC_BANDAGE, MECHANIC_SHIELD, MECHANIC_MOUNT,
                                                         MECHANIC_INVULNERABILITY):
                return False
        elif e.effect in EFFECT_THREAT:
            if not _is_positive_target(e) and bp() > 0:
                return False
        if e.is_aura:                                                        # 4823-5037
            a = e.aura
            if a in AURA_NEG_IF_BP_OR_LEVEL_NEG:
                if bp() < 0 or bp_scale < 0:
                    return False
            elif a in AURA_NEG_IF_TARGET_OR_BP_NEG:
                if not _is_positive_target(e) or bp() < 0:
                    return False
            elif a in AURA_NEG_IF_BP_POS:
                if bp() > 0:
                    return False
            elif a in AURA_PCT_TAKEN:
                if not _is_positive_target(e) and bp() > 0:
                    return False
            elif a in AURA_REGEN_PCT:
                if not _is_positive_target(e) and bp() < 0:
                    return False
            elif a in AURA_ADD_TARGET_TRIGGER:
                return True
            elif a in AURA_PERIODIC_TRIGGER_VALUE:
                trig = self.get(e.trigger)
                if trig is not None:
                    for te in trig.effects:
                        if (trig.id, te.index) in visited or not te.is_effect:
                            continue
                        if _is_positive_target(te) and not self._impl(trig, te, visited, state, mode, later, depth):
                            return False
            elif a in AURA_CHECK_TARGET:
                if not _is_positive_target(e):
                    return False
            elif a in AURA_NEGATIVE:
                return False
            elif a in AURA_MECHANIC_IMMUNITY:
                if e.misc0 in (MECHANIC_BANDAGE, MECHANIC_SHIELD, MECHANIC_MOUNT, MECHANIC_INVULNERABILITY):
                    return False
            elif a in AURA_SPELLMOD:
                op = e.misc0
                if op in (SMO["ChangeCastTime"], SMO["Period"], SMO["PowerCostOnMiss"], SMO["StartCooldown"]):
                    if bp() > 0:
                        return False
                elif op in (SMO["Cooldown"], SMO["PowerCost0"], SMO["PowerCost1"], SMO["PowerCost2"]):
                    if self._neg(sp.id, state, mode, later, depth) and bp() > 0:
                        return False
                elif op in (SMO["PointsIndex0"], SMO["PointsIndex1"], SMO["PointsIndex2"], SMO["PointsIndex3"],
                            SMO["PointsIndex4"], SMO["Points"], SMO["Hate"], SMO["ChainAmplitude"],
                            SMO["Amplitude"]):
                    return True
                elif op in (SMO["Duration"], SMO["CritChance"], SMO["HealingAndDamage"], SMO["ChainTargets"]):
                    if self._neg(sp.id, state, mode, later, depth) and bp() < 0:
                        return False
                else:
                    if bp() < 0:
                        return False
        if not e.aura and e.trigger:                                         # 5040-5059
            trig = self.get(e.trigger)
            if trig is not None:
                for te in trig.effects:
                    if (trig.id, te.index) in visited or not te.is_effect:
                        continue
                    if not self._impl(trig, te, visited, state, mode, later, depth):
                        return False
        return True

    def _initialize(self, sp: PSpell, mode: str, later: frozenset, depth: int) -> frozenset[int]:
        """Mirrors: SpellInfo.cpp:5066 ``_InitializeSpellPositivity``."""
        neg = set(sp.seed)
        state = {sp.id: neg}
        visited: set = set()
        for e in sp.effects:
            if not self._impl(sp, e, visited, state, mode, later, depth):
                neg.add(e.index)
        for e in sp.effects:                                                 # 5075-5098
            if not e.is_effect or e.index in neg:
                continue
            if e.aura in POST_PASS_AURAS:
                for j in range(e.index + 1, len(sp.effects)):
                    o = sp.effects[j]
                    if j in neg and e.target_a == o.target_a and e.target_b == o.target_b:
                        neg.add(e.index)
        return frozenset(neg)

    def negative_effects(self, sid: int, later: frozenset[int] = frozenset(), depth: int = 0) -> frozenset[int]:
        """Final ``NegativeEffects`` of ``sid`` (FailClosed when load-order dependent)."""
        key = (sid, later)
        if key in self._final:
            return self._final[key]
        if depth > self.max_depth:
            raise FailClosed(f"positivity {sid}: triggered-spell chain deeper than {self.max_depth}")
        sp = self.get(sid)
        if sp is None:
            raise FailClosed(f"positivity: spell {sid} unknown")
        inner = later | {sid}
        low = self._initialize(sp, "unprocessed", inner, depth)
        high = self._initialize(sp, "processed", inner, depth)
        if low != high:
            raise FailClosed(f"positivity {sid}: load-order dependent (other spells unprocessed -> "
                             f"{sorted(low)}, processed -> {sorted(high)})")
        self._final[key] = low
        return low

    def is_positive(self, sid: int) -> bool:
        """Mirrors: SpellInfo.cpp:1879 ``IsPositive`` (``NegativeEffects.none()``)."""
        return not self.negative_effects(sid)


# ---------------------------------------------------------------------------
# DB2 provider
# ---------------------------------------------------------------------------
@cache
def data_loader() -> Loader:
    from . import context
    from .selectors import info
    ctx = context.get()
    src = ctx.data.source
    cols = ("SpellID", "DifficultyID", "EffectIndex", "Effect", "EffectAura", "ImplicitTarget_0", "ImplicitTarget_1",
            "EffectAttributes", "EffectRealPointsPerLevel", "EffectMiscValue_0", "EffectTriggerSpell",
            "EffectBasePointsF", "Coefficient", "Variance", "ScalingClass")
    rows: dict[int, dict[int, dict]] = {}
    for r in src.project("SpellEffect", cols):
        d = dict(zip(cols, r))
        if int(d["DifficultyID"]) != 0:
            continue
        rows.setdefault(int(d["SpellID"]), {})[int(d["EffectIndex"])] = d
    levels = {int(r[0]): (int(r[1]), int(r[2])) for r in src.project("SpellLevels", ("SpellID", "BaseLevel", "SpellLevel", "DifficultyID")) if int(r[3]) == 0}
    scaling = {int(r[0]): (int(r[1]), int(r[2])) for r in src.project("SpellScaling", ("SpellID", "MinScalingLevel", "MaxScalingLevel"))}
    ct_of = {int(r[0]): int(r[1]) for r in src.project("SpellMisc", ("SpellID", "ContentTuningID", "DifficultyID")) if int(r[2]) == 0}
    expansion = {int(r[0]): int(r[1]) for r in src.project("ContentTuning", ("ID", "ExpansionID"))}
    stat_cols = ("Lvl", "ExpansionID", "CreatureSpellDamage", "PlayerHealth", "PlayerMana", "PlayerPrimaryStat",
                 "PlayerSecondaryStat", "ArmorConstant", "CreatureAutoAttackDps")
    stats = {}
    for r in src.project("ExpectedStat", stat_cols):
        d = dict(zip(stat_cols, r))
        stats[(int(d["Lvl"]), int(d["ExpansionID"]))] = d          # later row wins (DB2Stores.cpp:1319)
    seeds = _correction_seeds(ctx)
    from gearing.enchants import spell_scaling_column
    gt = src.tables.gametable("SpellScaling")

    def expected_stat(stat: str, exp: int) -> float:
        row = stats.get((1, exp)) or stats.get((1, -2))
        if row is None:
            return 1.0                                                 # DB2Stores.cpp:2482-2483
        return f32(float(row[STAT_COLUMN[stat]]))

    def spell_scaling(level: int, cls: int) -> float:
        col = spell_scaling_column(cls)
        if gt.row(level) is None:
            raise FailClosed(f"positivity: SpellScaling.txt has no row {level} (GetRow -> nullptr dereference)")
        if col is None:
            return 0.0                                                 # GameTables.h:310 default
        return f32(float(gt.column(level, col)))

    unapplied = _unapplied_members(ctx)

    @cache
    def get(sid: int) -> PSpell | None:
        info_ = ctx.catalog.get(sid)
        effs = rows.get(sid)
        if info_ is None or effs is None:
            return None
        if sid in unapplied:
            raise FailClosed(f"positivity {sid}: LoadSpellInfoCorrections writes {sorted(unapplied[sid])} not applied")
        from .attributes import corrected_effects
        eff_rows = ctx.data.effects(sid)
        fixed = {r["index"]: r for r in corrected_effects(sid, eff_rows)[0]}
        dense = []
        for i in range(max(effs) + 1):
            d = effs.get(i)
            if d is None:
                dense.append(PEffect(index=i))
                continue
            fx = fixed.get(i, {"effect": int(d["Effect"]), "a": int(d["ImplicitTarget_0"]), "b": int(d["ImplicitTarget_1"])})
            dense.append(PEffect(
                index=i, effect=fx["effect"], aura=int(d["EffectAura"]), target_a=fx["a"], target_b=fx["b"],
                check_a=info(fx["a"]).check, check_b=info(fx["b"]).check,
                attributes=int(d["EffectAttributes"]), real_points_per_level=float(d["EffectRealPointsPerLevel"]),
                misc0=int(d["EffectMiscValue_0"]), trigger=int(d["EffectTriggerSpell"]),
                base_points=float(d["EffectBasePointsF"]), coefficient=float(d["Coefficient"]),
                variance=float(d["Variance"]), scaling_class=int(d["ScalingClass"])))
        base_level, spell_level = levels.get(sid, (0, 0))
        mn, mx = scaling.get(sid, (0, 0))
        return PSpell(id=sid, attributes=tuple(int(w) & 0xFFFFFFFF for w in info_.attributes),
                      family=int(info_.family), family_flags0=int(info_.family_flags) & 0xFFFFFFFF,
                      mechanic=int(info_.mechanic), effects=tuple(dense), seed=frozenset(seeds.get(sid, ())),
                      spell_level=spell_level, base_level=base_level, min_scaling_level=mn, max_scaling_level=mx,
                      expansion=expansion.get(ct_of.get(sid, 0), -2))

    return Loader(get, expected_stat, spell_scaling)


#: LoadSpellInfoCorrections members that change positivity inputs and are not applied here
POSITIVITY_MEMBERS = {"Attributes", "AttributesEx", "AttributesEx2", "AttributesEx3", "AttributesEx4",
                      "AttributesEx5", "AttributesEx6", "AttributesEx7", "AttributesEx8", "AttributesEx9",
                      "AttributesEx10", "AttributesEx11", "AttributesEx12", "AttributesEx13", "AttributesEx14",
                      "AttributesEx15", "AttributesEx16", "ApplyAuraName", "BasePoints", "MiscValue",
                      "TriggerSpell", "Mechanic", "SpellFamilyFlags", "SpellFamilyName", "Scaling",
                      "RealPointsPerLevel", "EffectAttributes"}


def _correction_writes(ctx) -> list[tuple[list[int], str, str]]:
    from dummy_semantics.corrections import Corrections
    out = []
    for fx in Corrections(ctx.bundle).fixes:
        for w in fx["writes"]:
            out.append((fx["ids"], w["member"], w["value"]))
    return out


def _unapplied_members(ctx) -> dict[int, set[str]]:
    out: dict[int, set[str]] = {}
    for ids, member, _ in _correction_writes(ctx):
        if member in POSITIVITY_MEMBERS or member == "unknown":
            for i in ids:
                out.setdefault(i, set()).add(member)
    return out


def _correction_seeds(ctx) -> dict[int, set[int]]:
    """``NegativeEffects[EFFECT_n] = true`` writes (SpellMgr.cpp:5129, 5235) from the script index."""
    import re
    raw = ctx.bundle.index.engine.get("src/server/game/Spells/SpellMgr.cpp", {}).get("corrections", [])
    out: dict[int, set[int]] = {}
    for fx in raw:
        for w in fx["writes"]:
            m = re.fullmatch(r"spellInfo->NegativeEffects\[EFFECT_(\d+)\]", w["target"])
            if not m:
                continue
            if w["value"].strip() != "true":
                raise FailClosed(f"positivity: unexpected NegativeEffects correction {w}")
            for i in fx["ids"]:
                if i["id"] is not None:
                    out.setdefault(i["id"], set()).add(int(m.group(1)))
    return out


def is_positive(spell: int) -> bool:
    return data_loader().is_positive(spell)


def negative_effects(spell: int) -> frozenset[int]:
    return data_loader().negative_effects(spell)


__all__ = ["Loader", "PEffect", "PSpell", "data_loader", "is_positive", "negative_effects", "scaling_expected_stat"]
