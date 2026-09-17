"""Stat sources of controlled units and the prepared-stat oracle (Track A3).

Two things live here:

1. ``CONSUMERS``: the consumer table -- for every Trinity unit class
   (``Pet`` SUMMON_PET / HUNTER_PET, ``Guardian`` created by
   ``Map::SummonCreature``, plain ``Minion``, ``Totem``, ``Puppet``, plain
   ``TempSummon``) and every stat family, which function in the pinned
   TrinityCore checkout produces the value, what its inputs are and how the
   value is classified (``independent`` creature value, ``creation-snapshot``,
   ``application-snapshot``, ``dynamic-owner-lookup``,
   ``explicit-recalculation``, ``legacy-only``, ``unresolved``).

2. The oracle: a Python transcription of ``Guardian::InitStatsForLevel``
   (Pet.cpp:839-1090) and the ``Guardian::Update*`` family
   (StatSystem.cpp:1136-1409) in Trinity's own statement order, with every
   ``float`` intermediate rounded to binary32 (``f32``) and every
   ``int32``/``uint32`` cast truncated exactly where Trinity casts.  The
   oracle takes *owner facts* and *world-db facts* as explicit inputs and
   fails closed (raises :class:`SourceError`) for any branch whose input
   Trinity derives from engine state the research pass does not compute
   (``ExpectedStat`` evaluations, ``ContentTuning`` levels).

Nothing here is a Retail claim.  ``absent from pinned Trinity != absent from
Retail``; the oracle is Trinity's consumer arithmetic and nothing more.
"""

from __future__ import annotations

import argparse
import json
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from controlled_units import CORPORA, SNAPSHOT_BUILD, TRINITY_COMMIT, WORLD_DB_CORPORA, SourceError

TC = "src/server/game/"

# ---------------------------------------------------------------------------
# binary32 emulation
# ---------------------------------------------------------------------------


def f32(value: float) -> float:
    """Round a Python float (binary64) to binary32, as a C++ ``float`` op does.

    For one IEEE basic operation (+ - * /) on binary32 operands, computing in
    binary64 and rounding to binary32 yields the correctly rounded binary32
    result (binary64 carries more than 2p+2 significant bits), so rounding
    after every operation reproduces ``-ffp-contract=off`` float arithmetic.
    """
    return struct.unpack("<f", struct.pack("<f", value))[0]


def i32(value: float) -> int:
    """``int32(value)``: truncation toward zero (value assumed in range)."""
    return int(value)


def u32(value: float) -> int:
    """``(uint32)value``: truncation.  A negative (or >= 2^32) float is undefined behaviour in C++
    ([conv.fpint]); callers that can reach it use :func:`u32_x86`."""
    if value < 0 or value >= 4294967296.0:
        raise SourceError(f"(uint32) cast of {value!r} is undefined behaviour in C++; refusing")
    return int(value)


def u32_x86(value: float) -> tuple[int, bool]:
    """``(uint32)value`` as g++ -O0 emits it on x86-64 (cvttss2si into a 64-bit register, low 32 bits kept).
    Returns (result, undefined_behaviour_flag).  Confirmed by tools/tc_pet_probe."""
    if 0 <= value < 4294967296.0:
        return int(value), False
    return int(value) & 0xFFFFFFFF, True


def calc_pct_f(base: float, pct: int) -> float:
    """``CalculatePct<float,int>``: ``T(base * float(pct) / 100.0f)`` (Util.h:72-75)."""
    return f32(f32(f32(base) * f32(float(pct))) / f32(100.0))


def calc_pct_i(base: int, pct: int) -> int:
    """``CalculatePct<int32|uint32,int>``: float product, float divide, integral truncation."""
    return int(f32(f32(f32(float(base)) * f32(float(pct))) / f32(100.0)))


# ---------------------------------------------------------------------------
# Trinity enums used by the arithmetic (SharedDefines.h / Unit.h)
# ---------------------------------------------------------------------------

STAT_STRENGTH, STAT_AGILITY, STAT_STAMINA, STAT_INTELLECT, STAT_SPIRIT = 0, 1, 2, 3, 4
MAX_STATS = 5  # SharedDefines.h:289 (spirit slot still allocated)
STAT_NAMES = ("strength", "agility", "stamina", "intellect", "spirit")

BASE_VALUE, BASE_PCT_EXCLUDE_CREATE, TOTAL_VALUE = 0, 1, 2  # UnitModifierFlatType Unit.h:155-161
BASE_PCT, TOTAL_PCT = 0, 1  # UnitModifierPctType Unit.h:163-168

# UnitMods (Unit.h enum UnitMods): only the members the oracle touches
UNIT_MOD_STAT_START = 0
UNIT_MOD_HEALTH = 5
UNIT_MOD_MANA = 6  # UNIT_MOD_POWER_START (Unit.h:183)
UNIT_MOD_ARMOR = 32  # UNIT_MOD_RESISTANCE_START = POWER_START + MAX_POWERS (Unit.h:209)
UNIT_MOD_ATTACK_POWER = 39
UNIT_MOD_DAMAGE_MAINHAND = 41
UNIT_MOD_END = 44  # Unit.h:222

MAX_SPELL_SCHOOL = 7
SPELL_SCHOOL_NORMAL, SPELL_SCHOOL_HOLY, SPELL_SCHOOL_FIRE, SPELL_SCHOOL_NATURE = 0, 1, 2, 3
SPELL_SCHOOL_FROST, SPELL_SCHOOL_SHADOW, SPELL_SCHOOL_ARCANE = 4, 5, 6

CLASS_WARRIOR, CLASS_PALADIN, CLASS_HUNTER, CLASS_ROGUE, CLASS_PRIEST = 1, 2, 3, 4, 5
CLASS_DEATH_KNIGHT, CLASS_SHAMAN, CLASS_MAGE, CLASS_WARLOCK, CLASS_MONK, CLASS_DRUID = 6, 7, 8, 9, 10, 11
CLASS_NAMES = {1: "WARRIOR", 2: "PALADIN", 3: "HUNTER", 4: "ROGUE", 5: "PRIEST", 6: "DEATH_KNIGHT",
               7: "SHAMAN", 8: "MAGE", 9: "WARLOCK", 10: "MONK", 11: "DRUID", 12: "DEMON_HUNTER", 13: "EVOKER"}

SUMMON_PET, HUNTER_PET, MAX_PET_TYPE = 0, 1, 2  # PetDefines.h:29-34
PET_TYPE_NAMES = {0: "SUMMON_PET", 1: "HUNTER_PET", 2: "MAX_PET_TYPE(none)"}

POWER_MANA, POWER_FOCUS, POWER_ENERGY = 0, 2, 3
MAX_POWERS = 26  # SharedDefines.h:319; sentinel returned by Creature::GetPowerIndex (StatSystem.cpp:1024)

BASE_ATTACK_TIME = 2000  # UnitDefines.h:35
BASE_MINDAMAGE, BASE_MAXDAMAGE = 1.0, 2.0  # UnitDefines.h:33-34

# Entry constants hardcoded in Trinity (StatSystem.cpp:1125-1134, TemporarySummon.h:24-39)
ENTRY_IMP, ENTRY_VOIDWALKER, ENTRY_SUCCUBUS, ENTRY_FELHUNTER, ENTRY_FELGUARD = 416, 1860, 1863, 417, 17252
ENTRY_WATER_ELEMENTAL, ENTRY_TREANT, ENTRY_FIRE_ELEMENTAL, ENTRY_GHOUL, ENTRY_BLOODWORM = 510, 1964, 15438, 26125, 28017
PET_GHOUL, PET_SPIRIT_WOLF = 26125, 29264

#: health multiplier per stamina point, Guardian::UpdateMaxHealth (StatSystem.cpp:1259-1268)
HEALTH_PER_STAMINA = {ENTRY_IMP: 8.4, ENTRY_VOIDWALKER: 11.0, ENTRY_SUCCUBUS: 9.1, ENTRY_FELHUNTER: 9.5,
                      ENTRY_FELGUARD: 11.0, ENTRY_BLOODWORM: 1.0}
HEALTH_PER_STAMINA_DEFAULT = 10.0

#: entries with a dedicated branch in Guardian::InitStatsForLevel's default (non-Pet-type) switch
INIT_ENTRY_SWITCH = {510: "mage Water Elemental (legacy entry; 12.1 summons 78116)",
                     1964: "force of nature treant (legacy entry; 12.1 Force of Nature summons 103822)",
                     15352: "earth elemental (legacy entry; 12.1 Earth Elemental summons 95072)",
                     15438: "fire elemental (legacy entry; 12.1 Fire Elemental summons 95061)",
                     19668: "Shadowfiend (current entry; 12.1 summons via 1280172)",
                     19833: "Snake Trap - Venomous Snake (legacy; no 12.1 player summon)",
                     19921: "Snake Trap - Viper (legacy; no 12.1 player summon)",
                     29264: "Feral Spirit wolf (current entry for 228562/363941)",
                     31216: "Mirror Image (current entry for 321686)",
                     27829: "Ebon Gargoyle (current entry for 49206)",
                     28017: "Bloodworms (no 12.1 player summon found)"}


# ---------------------------------------------------------------------------
# state
# ---------------------------------------------------------------------------


@dataclass
class OwnerFacts:
    """The owner-side values the Guardian arithmetic reads (all *inputs*)."""

    class_id: int
    level: int
    stats: tuple[int, int, int, int, int]  # UnitData::Stats (int32); GetStat() returns float(int32)
    armor: int  # GetArmor() -> uint32 Resistances[0]
    attack_power_melee: float  # GetTotalAttackPowerValue(BASE_ATTACK) (float, includes weapon AP)
    attack_power_ranged: float  # GetTotalAttackPowerValue(RANGED_ATTACK)
    mod_damage_done_pos: tuple[int, ...] = (0,) * MAX_SPELL_SCHOOL  # ActivePlayerData::ModDamageDonePos[school] int32
    mod_damage_done_neg: tuple[int, ...] = (0,) * MAX_SPELL_SCHOOL  # ActivePlayerData::ModDamageDoneNeg[school] int32
    spell_base_damage_bonus: dict[str, int] = field(default_factory=dict)  # SpellBaseDamageBonusDone(mask) by school name
    resistances: tuple[int, ...] = (0,) * MAX_SPELL_SCHOOL  # GetResistance(school) int32
    bonus_resistance_mods: tuple[int, ...] = (0,) * MAX_SPELL_SCHOOL  # GetBonusResistanceMod(school) int32

    def get_stat(self, stat: int) -> float:
        return f32(float(self.stats[stat]))


@dataclass
class CreatureFacts:
    """``CreatureTemplate`` columns read by InitStatsForLevel (world-db facts)."""

    entry: int
    unit_class: int
    dmgschool: int = 0
    resistance: tuple[int, ...] = (0,) * MAX_SPELL_SCHOOL  # cinfo->resistance[school]
    base_attack_time: int = BASE_ATTACK_TIME  # cinfo->BaseAttackTime (only read for 29264)
    calc_display_power: int = POWER_MANA  # CalculateDisplayPowerType() (pre-gate): indexes the power BASE_PCT (Pet.cpp:896, Creature.cpp:1648)
    display_power_type: int = POWER_MANA  # DisplayPower after SetPowerType's IsUsedByNPCs gate (Unit.cpp:5705-5708)
    max_base_power: int = 0  # PowerType.db2 MaxBasePower of the display power (Unit::GetCreatePowerValue StatSystem.cpp:90-98)


@dataclass
class PetLevelInfo:
    """One ``pet_levelstats`` row as ``PetLevelInfo`` (ObjectMgr.h:675-681, all uint16)."""

    health: int
    mana: int
    armor: int
    stats: tuple[int, int, int, int, int]


@dataclass
class EngineInputs:
    """Values Trinity reads from engine/world state rather than from the owner.

    Every field defaults to ``None`` = *not supplied*; the oracle raises
    :class:`SourceError` if a branch needs one.  :func:`engine_inputs_from_sources`
    derives them from the DB2 snapshot + world-db corpus where Trinity's
    arithmetic is fully known.
    """

    # Pet.cpp:911  EvaluateExpectedStat(CreatureHealth, petlevel, HealthScalingExpansion, ContentTuningID, class, 0)
    expected_health_at_petlevel: float | None = None
    health_modifier: float | None = None  # creature_template_difficulty.HealthModifier (float)
    health_rate: float = 1.0  # Creature::GetHealthMod(classification) = Rate.Creature.HP.* (worldserver.conf.dist default 1)
    base_mana: int | None = None  # creature_classlevelstats.basemana at (petlevel, unit_class); 0 if row absent (ObjectMgr.cpp:9990-10004)
    base_damage_for_level: float | None = None  # Creature::GetBaseDamageForLevel(petlevel) (Pet.cpp:1071)
    # Guardians only (Map::SummonCreature -> Creature::Create -> UpdateEntry -> SelectLevel -> UpdateLevelDependantStats)
    pre_health_base_value: float | None = None  # UNIT_MOD_HEALTH BASE_VALUE = float(uint32(ceil(ES*HealthModifier)*rate)) at SelectLevel level (Creature.cpp:1635-1643)
    pre_mana_base_pct: float | None = None  # UNIT_MOD_POWER BASE_PCT = ManaModifier (Creature.cpp:1648); overwritten to 1.0 only when pet_levelstats exists
    pre_weapon_damage: tuple[float, float] | None = None  # BASE_ATTACK weapon damage left by UpdateLevelDependantStats (entries 510/31216 keep it)
    provenance: dict[str, Any] = field(default_factory=dict)


@dataclass
class UnitState:
    """The subset of ``Unit``/``Creature``/``Guardian`` state the arithmetic touches."""

    entry: int
    is_pet: bool  # UNIT_MASK_PET (Pet class)
    is_guardian: bool = True  # UNIT_MASK_GUARDIAN
    is_hunter_pet: bool = False  # UNIT_MASK_HUNTER_PET (set inside InitStatsForLevel for hunters)
    can_modify_stats: bool = False  # Unit.cpp:340 default; never set true on a Map::SummonCreature guardian
    level: int = 0
    pet_type: int = MAX_PET_TYPE
    stats: list[int] = field(default_factory=lambda: [0] * MAX_STATS)  # UnitData::Stats int32
    stat_from_owner: list[float] = field(default_factory=lambda: [0.0] * MAX_STATS)
    create_stats: list[float] = field(default_factory=lambda: [0.0] * MAX_STATS)  # m_createStats (Unit.cpp:359)
    create_health: int = 0  # UnitData::BaseHealth uint32
    create_mana: int = 0  # UnitData::BaseMana uint32
    flat: list[list[float]] = field(default_factory=lambda: [[0.0, 100.0, 0.0] for _ in range(UNIT_MOD_END)])  # Unit.cpp:344-346
    pct: list[list[float]] = field(default_factory=lambda: [[1.0, 1.0] for _ in range(UNIT_MOD_END)])  # Unit.cpp:347-348
    weapon_damage: list[float] = field(default_factory=lambda: [BASE_MINDAMAGE, BASE_MAXDAMAGE])  # m_weaponDamage[BASE_ATTACK]
    base_attack_time: int = BASE_ATTACK_TIME
    power_type: int = POWER_MANA
    max_health: int = 0
    max_power: tuple[int, int] = (0, 0)  # (display power, MaxPower[0])
    max_health_ub: bool = False  # the last UpdateMaxHealth cast a negative float
    armor: tuple[int, int] = (0, 0)  # SetArmor(base, bonus)
    resistances: list[int] = field(default_factory=lambda: [0] * MAX_SPELL_SCHOOL)
    bonus_resistance_mods: list[int] = field(default_factory=lambda: [0] * MAX_SPELL_SCHOOL)
    attack_power: int = 0  # UnitData::AttackPower int32
    attack_power_mod_pos: int = 0
    attack_power_mod_neg: int = 0
    attack_power_multiplier: float = 0.0
    bonus_spell_damage: int = 0  # Guardian::m_bonusSpellDamage
    min_damage: float = 0.0
    max_damage: float = 0.0
    trace: list[str] = field(default_factory=list)

    # --- Unit accessors (verbatim semantics) ------------------------------
    def get_stat(self, stat: int) -> float:  # Unit.h:771 float(m_unitData->Stats[stat])
        return f32(float(self.stats[stat]))

    def set_stat(self, stat: int, value: int) -> None:  # Unit.h:772
        self.stats[stat] = value

    def get_create_stat(self, stat: int) -> float:  # Unit.h:1420
        return f32(self.create_stats[stat])

    def get_flat(self, mod: int, kind: int) -> float:  # Unit.cpp:9660
        return f32(self.flat[mod][kind])

    def get_pct(self, mod: int, kind: int) -> float:  # Unit.cpp:9671
        return f32(self.pct[mod][kind])

    def get_total_stat_value(self, stat: int) -> float:
        """``Unit::GetTotalStatValue`` (Unit.cpp:9828-9842), float throughout."""
        create_stat = self.get_create_stat(stat)
        mod = UNIT_MOD_STAT_START + stat
        # CalculatePct<float,float>: pct is the float BASE_PCT_EXCLUDE_CREATE itself (default 100.0f,
        # Unit.cpp:345); static_cast<float>(pct) is the identity, so value = base * pct / 100.0f.
        excl = max(self.get_flat(mod, BASE_PCT_EXCLUDE_CREATE), f32(-100.0))
        value = f32(f32(self.get_flat(mod, BASE_VALUE) * excl) / f32(100.0))
        value = f32(value + create_stat)
        value = f32(value * self.get_pct(mod, BASE_PCT))
        value = f32(value + self.get_flat(mod, TOTAL_VALUE))
        value = f32(value * self.get_pct(mod, TOTAL_PCT))
        return value

    def get_total_aura_mod_value(self, mod: int) -> float:
        """``Unit::GetTotalAuraModValue`` (Unit.cpp:9844-9858)."""
        excl = max(self.get_flat(mod, BASE_PCT_EXCLUDE_CREATE), -100.0)
        value = f32(f32(self.get_flat(mod, BASE_VALUE) * f32(excl)) / f32(100.0))
        value = f32(value * self.get_pct(mod, BASE_PCT))
        value = f32(value + self.get_flat(mod, TOTAL_VALUE))
        value = f32(value * self.get_pct(mod, TOTAL_PCT))
        return value

    def get_total_attack_power_value_no_weapon(self) -> float:
        """``Unit::GetTotalAttackPowerValue(BASE_ATTACK, false)`` (Unit.cpp:9930-9946)."""
        ap = f32(float(self.attack_power + self.attack_power_mod_pos + self.attack_power_mod_neg))
        if ap < 0:
            return 0.0
        return f32(ap * f32(1.0 + f32(self.attack_power_multiplier)))

    def get_power_index(self, power: int) -> int:  # Creature::GetPowerIndex StatSystem.cpp:1005-1025
        if power == self.power_type:
            return 0
        return MAX_POWERS


# ---------------------------------------------------------------------------
# Guardian arithmetic (Trinity statement order)
# ---------------------------------------------------------------------------


class GuardianOracle:
    """Transcription of the Guardian stat pipeline at TrinityCore 7f3d43b."""

    def __init__(self, unit: UnitState, owner: OwnerFacts, cinfo: CreatureFacts,
                 pinfo: PetLevelInfo | None, engine: EngineInputs) -> None:
        self.u = unit
        self.owner = owner
        self.cinfo = cinfo
        self.pinfo = pinfo
        self.engine = engine

    # -- modifier setters: Unit.cpp:9642-9658 + UpdateUnitMod 9682-9743 ----
    def set_stat_flat_modifier(self, mod: int, kind: int, val: float) -> None:
        val = f32(val)
        if self.u.flat[mod][kind] == val:
            return
        self.u.flat[mod][kind] = val
        self.update_unit_mod(mod)

    def set_stat_pct_modifier(self, mod: int, kind: int, val: float) -> None:
        val = f32(val)
        if self.u.pct[mod][kind] == val:
            return
        self.u.pct[mod][kind] = val
        self.update_unit_mod(mod)

    def update_unit_mod(self, mod: int) -> None:
        if not self.u.can_modify_stats:  # Unit.cpp:9684-9685
            return
        if UNIT_MOD_STAT_START <= mod < UNIT_MOD_STAT_START + MAX_STATS:
            self.update_stats(mod - UNIT_MOD_STAT_START)
        elif mod == UNIT_MOD_ARMOR:
            self.update_armor()
        elif mod == UNIT_MOD_HEALTH:
            self.update_max_health()
        elif UNIT_MOD_MANA <= mod < UNIT_MOD_ARMOR:
            self.update_max_power(mod - UNIT_MOD_MANA)
        elif mod == UNIT_MOD_ATTACK_POWER:
            self.update_attack_power_and_damage()
        elif mod == UNIT_MOD_DAMAGE_MAINHAND:
            self.update_damage_physical()
        else:
            raise SourceError(f"UpdateUnitMod({mod}) not modelled (resistances/ranged)")

    # -- Guardian::UpdateStats StatSystem.cpp:1136-1193 ---------------------
    def update_stats(self, stat: int) -> None:
        u, owner = self.u, self.owner
        value = u.get_total_stat_value(stat)
        owners_bonus = 0.0
        mod = f32(0.75)
        if u.entry == PET_GHOUL and stat in (STAT_STAMINA, STAT_STRENGTH):  # :1146 IsPetGhoul()
            mod = f32(0.3) if stat == STAT_STAMINA else f32(0.7)
            owners_bonus = f32(owner.get_stat(stat) * mod)  # :1153 float(owner->GetStat(stat)) * mod
            value = f32(value + owners_bonus)
        elif stat == STAT_STAMINA:  # :1156-1160
            owners_bonus = calc_pct_f(owner.get_stat(STAT_STAMINA), 30)
            value = f32(value + owners_bonus)
        elif stat == STAT_INTELLECT:  # :1162-1169
            if owner.class_id in (CLASS_WARLOCK, CLASS_MAGE):
                owners_bonus = calc_pct_f(owner.get_stat(stat), 30)
                value = f32(value + owners_bonus)
        u.set_stat(stat, i32(value))  # :1178
        u.stat_from_owner[stat] = owners_bonus  # :1179
        u.trace.append(f"UpdateStats({STAT_NAMES[stat]}): total={value!r} ownersBonus={owners_bonus!r} -> Stats[{stat}]={u.stats[stat]}")
        if stat == STAT_STRENGTH:  # :1182-1190
            self.update_attack_power_and_damage()
        elif stat == STAT_AGILITY:
            self.update_armor()
        elif stat == STAT_STAMINA:
            self.update_max_health()
        elif stat == STAT_INTELLECT:
            self.update_max_power(POWER_MANA)

    # -- Guardian::UpdateAllStats :1195-1208 --------------------------------
    def update_all_stats(self) -> None:
        self.update_max_health()
        for stat in range(STAT_STRENGTH, MAX_STATS):
            self.update_stats(stat)
        # Creature::GetPowerIndex also indexes combo points / alternate powers (StatSystem.cpp:1009-1021);
        # only the display power is modelled (the others do not feed any stat the oracle reports)
        self.update_max_power(self.u.power_type)
        self.update_all_resistances()

    def update_all_resistances(self) -> None:  # Unit::UpdateAllResistances -> UpdateResistances(school) for each school
        for school in range(SPELL_SCHOOL_NORMAL, MAX_SPELL_SCHOOL):
            self.update_resistances(school)

    # -- Guardian::UpdateResistances :1210-1229 -----------------------------
    def update_resistances(self, school: int) -> None:
        u = self.u
        if school > SPELL_SCHOOL_NORMAL:
            mod = UNIT_MOD_ARMOR + school
            base_value = u.get_flat(mod, BASE_VALUE)
            bonus_value = f32(u.get_total_aura_mod_value(mod) - base_value)
            if u.is_pet:  # :1218 "hunter and warlock pets gain 40% of owner's resistance"
                base_value = f32(base_value + f32(float(calc_pct_i(self.owner.resistances[school], 40))))
                bonus_value = f32(bonus_value + f32(float(calc_pct_i(self.owner.bonus_resistance_mods[school], 40))))
            u.resistances[school] = i32(base_value)
            u.bonus_resistance_mods[school] = i32(bonus_value)
        else:
            self.update_armor()

    # -- Guardian::UpdateArmor :1231-1251 -----------------------------------
    def update_armor(self) -> None:
        u = self.u
        bonus_armor = 0.0
        if u.is_hunter_pet:  # :1239-1240 (comment says 35%, code says 70)
            bonus_armor = f32(float(calc_pct_i(self.owner.armor, 70)))
        elif u.is_pet:  # :1241-1242
            bonus_armor = f32(float(self.owner.armor))
        value = u.get_flat(UNIT_MOD_ARMOR, BASE_VALUE)
        base_value = value
        value = f32(value * u.get_pct(UNIT_MOD_ARMOR, BASE_PCT))
        value = f32(value + f32(u.get_flat(UNIT_MOD_ARMOR, TOTAL_VALUE) + bonus_armor))
        value = f32(value * u.get_pct(UNIT_MOD_ARMOR, TOTAL_PCT))
        u.armor = (i32(base_value), i32(f32(value - base_value)))  # :1250
        u.resistances[SPELL_SCHOOL_NORMAL] = u.armor[0]
        u.bonus_resistance_mods[SPELL_SCHOOL_NORMAL] = u.armor[1]
        u.trace.append(f"UpdateArmor: bonus_armor={bonus_armor!r} base={base_value!r} value={value!r} -> armor={u.armor}")

    # -- Guardian::UpdateMaxHealth :1253-1276 -------------------------------
    def update_max_health(self) -> None:
        u = self.u
        stamina = f32(u.get_stat(STAT_STAMINA) - u.get_create_stat(STAT_STAMINA))
        multiplicator = f32(HEALTH_PER_STAMINA.get(u.entry, HEALTH_PER_STAMINA_DEFAULT))
        value = f32(u.get_flat(UNIT_MOD_HEALTH, BASE_VALUE) + f32(float(u.create_health)))
        value = f32(value * u.get_pct(UNIT_MOD_HEALTH, BASE_PCT))
        value = f32(value + f32(u.get_flat(UNIT_MOD_HEALTH, TOTAL_VALUE) + f32(stamina * multiplicator)))
        value = f32(value * u.get_pct(UNIT_MOD_HEALTH, TOTAL_PCT))
        max_health, ub = u32_x86(value)  # :1275 (uint32)value
        u.max_health = max_health if max_health else 1  # Unit::SetMaxHealth Unit.cpp:10018-10019
        u.max_health_ub = ub
        if ub:
            u.trace.append(f"UpdateMaxHealth: (uint32){value!r} is undefined behaviour; x86-64 result {max_health}")
        u.trace.append(f"UpdateMaxHealth: stamina={stamina!r} mult={multiplicator!r} value={value!r} -> {u.max_health}")

    # -- Guardian::UpdateMaxPower :1278-1291 --------------------------------
    def update_max_power(self, power: int) -> None:
        u = self.u
        if u.get_power_index(power) == MAX_POWERS:
            return
        mod = UNIT_MOD_MANA + power
        # Creature::GetCreatePowerValue (StatSystem.cpp:964-971): the display power passed the IsUsedByNPCs gate
        create = u.create_mana if power == POWER_MANA else self.cinfo.max_base_power
        value = f32(u.get_flat(mod, BASE_VALUE) + f32(float(create)))
        value = f32(value * u.get_pct(mod, BASE_PCT))
        value = f32(value + u.get_flat(mod, TOTAL_VALUE))
        value = f32(value * u.get_pct(mod, TOTAL_PCT))
        u.max_power = (power, i32(value))  # :1290 int32(value)
        u.trace.append(f"UpdateMaxPower({power}): value={value!r} -> {i32(value)}")

    # -- Guardian::UpdateAttackPowerAndDamage :1293-1359 -------------------
    def update_attack_power_and_damage(self) -> None:
        u, owner = self.u, self.owner
        bonus_ap = 0.0
        if u.entry == ENTRY_IMP:  # :1302-1303
            val = f32(u.get_stat(STAT_STRENGTH) - f32(10.0))
        else:  # :1305  2 * GetStat(STAT_STRENGTH) - 20.0f
            val = f32(f32(f32(2.0) * u.get_stat(STAT_STRENGTH)) - f32(20.0))
        branch = "none"
        if u.is_hunter_pet:  # :1310-1315
            mod = f32(1.0)
            bonus_ap = f32(f32(f32(owner.attack_power_ranged) * f32(0.22)) * mod)
            self.set_bonus_damage(i32(f32(f32(f32(owner.attack_power_ranged) * f32(0.1287)) * mod)))
            branch = "hunter: ranged AP * 0.22 -> AP; * 0.1287 -> bonus damage"
        elif u.entry == PET_GHOUL:  # :1316-1320
            bonus_ap = f32(f32(owner.attack_power_melee) * f32(0.22))
            self.set_bonus_damage(i32(f32(f32(owner.attack_power_melee) * f32(0.1287))))
            branch = "ghoul: melee AP * 0.22 -> AP; * 0.1287 -> bonus damage"
        elif u.entry == PET_SPIRIT_WOLF:  # :1321-1326
            dmg_multiplier = f32(0.31)
            bonus_ap = f32(f32(owner.attack_power_melee) * dmg_multiplier)
            self.set_bonus_damage(i32(f32(f32(owner.attack_power_melee) * dmg_multiplier)))
            branch = "spirit wolf: melee AP * 0.31 -> AP and bonus damage"
        elif u.is_pet:  # :1328-1337  (any other Pet: warlock demons, water elemental 78116, ...)
            fire = owner.mod_damage_done_pos[SPELL_SCHOOL_FIRE] - owner.mod_damage_done_neg[SPELL_SCHOOL_FIRE]
            shadow = owner.mod_damage_done_pos[SPELL_SCHOOL_SHADOW] - owner.mod_damage_done_neg[SPELL_SCHOOL_SHADOW]
            maximum = fire if fire > shadow else shadow
            if maximum < 0:
                maximum = 0
            self.set_bonus_damage(i32(f32(f32(float(maximum)) * f32(0.15))))  # int32(maximum * 0.15f)
            bonus_ap = f32(f32(float(maximum)) * f32(0.57))
            branch = "pet: max(fire,shadow) SP * 0.57 -> AP; * 0.15 -> bonus damage"
        elif u.entry == ENTRY_WATER_ELEMENTAL:  # :1339-1345 (legacy entry 510)
            frost = owner.mod_damage_done_pos[SPELL_SCHOOL_FROST] - owner.mod_damage_done_neg[SPELL_SCHOOL_FROST]
            if frost < 0:
                frost = 0
            self.set_bonus_damage(i32(f32(f32(float(frost)) * f32(0.4))))
            branch = "water elemental 510: frost SP * 0.4 -> bonus damage"
        self.set_stat_flat_modifier(UNIT_MOD_ATTACK_POWER, BASE_VALUE, f32(val + bonus_ap))  # :1348
        base_att_power = f32(u.get_flat(UNIT_MOD_ATTACK_POWER, BASE_VALUE) * u.get_pct(UNIT_MOD_ATTACK_POWER, BASE_PCT))
        att_power_multiplier = f32(u.get_pct(UNIT_MOD_ATTACK_POWER, TOTAL_PCT) - f32(1.0))
        u.attack_power = i32(base_att_power)  # :1354
        u.attack_power_multiplier = att_power_multiplier
        u.trace.append(f"UpdateAttackPowerAndDamage: val={val!r} bonusAP={bonus_ap!r} [{branch}] -> AP={u.attack_power} mult={att_power_multiplier!r}")
        self.update_damage_physical()  # :1358

    # -- Guardian::UpdateDamagePhysical :1361-1402 -------------------------
    def update_damage_physical(self) -> None:
        u, owner = self.u, self.owner
        bonus_damage = 0.0
        if u.entry == ENTRY_TREANT:  # :1370-1375 (legacy entry 1964)
            spell_dmg = owner.mod_damage_done_pos[SPELL_SCHOOL_NATURE] - owner.mod_damage_done_neg[SPELL_SCHOOL_NATURE]
            if spell_dmg > 0:
                bonus_damage = f32(f32(float(spell_dmg)) * f32(0.09))
        elif u.entry == ENTRY_FIRE_ELEMENTAL:  # :1377-1382 (legacy entry 15438)
            spell_dmg = owner.mod_damage_done_pos[SPELL_SCHOOL_FIRE] - owner.mod_damage_done_neg[SPELL_SCHOOL_FIRE]
            if spell_dmg > 0:
                bonus_damage = f32(f32(float(spell_dmg)) * f32(0.4))
        att_speed = f32(f32(float(u.base_attack_time)) / f32(1000.0))  # :1387
        ap_part = f32(f32(u.get_total_attack_power_value_no_weapon() / f32(3.5)) * att_speed)
        base_value = f32(f32(u.get_flat(UNIT_MOD_DAMAGE_MAINHAND, BASE_VALUE) + ap_part) + bonus_damage)  # :1389
        base_pct = u.get_pct(UNIT_MOD_DAMAGE_MAINHAND, BASE_PCT)
        total_value = u.get_flat(UNIT_MOD_DAMAGE_MAINHAND, TOTAL_VALUE)
        total_pct = u.get_pct(UNIT_MOD_DAMAGE_MAINHAND, TOTAL_PCT)
        weapon_min = f32(u.weapon_damage[0])
        weapon_max = f32(u.weapon_damage[1])
        mindamage = f32(f32(f32(f32(base_value + weapon_min) * base_pct) + total_value) * total_pct)  # :1397
        maxdamage = f32(f32(f32(f32(base_value + weapon_max) * base_pct) + total_value) * total_pct)  # :1398
        # SetUpdateFieldStatValue clamps at 0 (Entities/Object/BaseEntity.h:299-303)
        u.min_damage = max(mindamage, 0.0)
        u.max_damage = max(maxdamage, 0.0)
        u.trace.append(f"UpdateDamagePhysical: att_speed={att_speed!r} base_value={base_value!r} bonusDamage={bonus_damage!r} -> [{u.min_damage!r}, {u.max_damage!r}]")

    # -- Guardian::SetBonusDamage :1404-1409 --------------------------------
    def set_bonus_damage(self, damage: int) -> None:
        self.u.bonus_spell_damage = damage  # owner->SetPetSpellPower(damage) also writes ActivePlayerData::PetSpellPower

    # -- Guardian::InitStatsForLevel Pet.cpp:839-1090 -----------------------
    def init_stats_for_level(self, petlevel: int) -> None:
        u, owner, cinfo, pinfo, engine = self.u, self.owner, self.cinfo, self.pinfo, self.engine
        if u.is_guardian and not u.is_pet:
            # State left by Map::SummonCreature -> Creature::Create -> CreateFromProto (Creature.cpp:1151) ->
            # UpdateEntry (:647-648) -> SelectLevel -> UpdateLevelDependantStats (:1625-1671); the Guardian ctor
            # already set UNIT_MASK_GUARDIAN (TemporarySummon.cpp:516) so UpdateEntry skipped UpdateAllStats (:657).
            if engine.pre_health_base_value is None or engine.pre_mana_base_pct is None:
                raise SourceError(
                    "non-Pet Guardian: UNIT_MOD_HEALTH BASE_VALUE and power BASE_PCT were set by "
                    "Creature::UpdateLevelDependantStats at the SelectLevel level (Creature.cpp:1643/1648) and are "
                    "never reset by InitStatsForLevel; Guardian::UpdateMaxHealth adds BASE_VALUE to BaseHealth "
                    "(StatSystem.cpp:1270). creature_template_difficulty + ContentTuning rows are required")
            u.flat[UNIT_MOD_HEALTH][BASE_VALUE] = f32(engine.pre_health_base_value)
            u.pct[UNIT_MOD_MANA + cinfo.calc_display_power][BASE_PCT] = f32(engine.pre_mana_base_pct)
            if engine.pre_weapon_damage is not None:
                u.weapon_damage = [f32(engine.pre_weapon_damage[0]), f32(engine.pre_weapon_damage[1])]
        u.level = petlevel  # :844
        pet_type = MAX_PET_TYPE
        if u.is_pet:  # :848-866 (owner is a player in every witness)
            if owner.class_id in (CLASS_WARLOCK, CLASS_SHAMAN, CLASS_DEATH_KNIGHT):
                pet_type = SUMMON_PET
            elif owner.class_id == CLASS_HUNTER:
                pet_type = HUNTER_PET
                u.is_hunter_pet = True
            else:
                u.trace.append(f"InitStatsForLevel: TC_LOG_ERROR 'Unknown type pet {u.entry} is summoned by player class {owner.class_id}' (Pet.cpp:863) -> petType stays MAX_PET_TYPE")
        u.pet_type = pet_type
        creature_id = 1 if pet_type == HUNTER_PET else cinfo.entry  # :868
        self.set_stat_flat_modifier(UNIT_MOD_ARMOR, BASE_VALUE, f32(float(petlevel * 50)))  # :872
        u.base_attack_time = BASE_ATTACK_TIME  # :874-876
        if not u.is_hunter_pet:  # :883-885
            for school in range(SPELL_SCHOOL_HOLY, MAX_SPELL_SCHOOL):
                self.set_stat_flat_modifier(UNIT_MOD_ARMOR + school, BASE_VALUE, f32(float(cinfo.resistance[school])))
        power_type = cinfo.display_power_type  # :887 + SetPowerType gate (:920)
        calc_power = cinfo.calc_display_power
        u.power_type = power_type
        if pinfo is not None:  # :891-903
            u.create_health = pinfo.health
            u.create_mana = pinfo.mana
            self.set_stat_pct_modifier(UNIT_MOD_MANA + calc_power, BASE_PCT, f32(1.0))  # :896 (pre-gate power index)
            if pinfo.armor > 0:
                self.set_stat_flat_modifier(UNIT_MOD_ARMOR, BASE_VALUE, f32(float(pinfo.armor)))
            for stat in range(MAX_STATS):
                u.create_stats[stat] = f32(float(pinfo.stats[stat]))
            source = f"pet_levelstats(creature_entry={creature_id}, level={petlevel})"
        else:  # :905-917
            if engine.expected_health_at_petlevel is None or engine.health_modifier is None or engine.base_mana is None:
                raise SourceError(
                    f"no pet_levelstats row for creature_entry={creature_id}: Trinity falls back to "
                    "EvaluateExpectedStat(CreatureHealth, petlevel, ...) * HealthModifier * GetHealthMod (Pet.cpp:911) "
                    "and creature_classlevelstats.basemana (Pet.cpp:912); world-db creature_template_difficulty / "
                    "creature_classlevelstats rows for this entry are required")
            # float * float * float, std::max(.., 1.0f), implicit float -> uint32 in SetCreateHealth(uint32)
            hp = f32(f32(f32(engine.expected_health_at_petlevel) * f32(engine.health_modifier)) * f32(engine.health_rate))
            u.create_health = u32(max(hp, 1.0))
            u.create_mana = engine.base_mana
            u.create_stats[STAT_STRENGTH] = 22.0
            u.create_stats[STAT_AGILITY] = 22.0
            u.create_stats[STAT_STAMINA] = 25.0
            u.create_stats[STAT_INTELLECT] = 28.0
            source = "fake defaults (Pet.cpp:913-916) + ExpectedStat health"
        u.trace.append(f"InitStatsForLevel({petlevel}): petType={PET_TYPE_NAMES[pet_type]} createHealth={u.create_health} createMana={u.create_mana} createStats={u.create_stats} [{source}]")
        self.set_bonus_damage(0)  # :923
        if pet_type == SUMMON_PET:  # :926-942
            fire = owner.mod_damage_done_pos[SPELL_SCHOOL_FIRE]  # NOTE: Pos only, unlike UpdateAttackPowerAndDamage
            shadow = owner.mod_damage_done_pos[SPELL_SCHOOL_SHADOW]
            val = fire if fire > shadow else shadow
            if val < 0:
                val = 0
            self.set_bonus_damage(i32(f32(f32(float(val)) * f32(0.15))))  # :935 float -> int32 parameter
            u.weapon_damage = [f32(float(petlevel - petlevel // 4)), f32(float(petlevel + petlevel // 4))]  # :937-938
        elif pet_type == HUNTER_PET:  # :943-953
            u.weapon_damage = [f32(float(petlevel - petlevel // 4)), f32(float(petlevel + petlevel // 4))]
        else:  # :954-1082
            e = u.entry
            if e == 510:
                self.set_bonus_damage(i32(f32(f32(float(self._sbdb("frost"))) * f32(0.33))))
                # weapon damage untouched: stays whatever UpdateLevelDependantStats left (Guardian) or BASE_MIN/MAXDAMAGE (Pet)
                self._pre_weapon()
            elif e == 1964:
                if pinfo is None:
                    u.create_health = 30 + 30 * petlevel
                bonus_dmg = f32(f32(float(self._sbdb("nature"))) * f32(0.15))
                u.weapon_damage = [f32(f32(f32(f32(float(petlevel)) * f32(2.5)) - f32(float(petlevel // 2))) + bonus_dmg),
                                   f32(f32(f32(f32(float(petlevel)) * f32(2.5)) + f32(float(petlevel // 2))) + bonus_dmg)]
            elif e == 15352:
                if pinfo is None:
                    u.create_health = 100 + 120 * petlevel
                u.weapon_damage = [f32(float(petlevel - petlevel // 4)), f32(float(petlevel + petlevel // 4))]
            elif e == 15438:
                if pinfo is None:
                    u.create_health = 40 * petlevel
                    u.create_mana = 28 + 10 * petlevel
                self.set_bonus_damage(i32(f32(f32(float(self._sbdb("fire"))) * f32(0.5))))
                u.weapon_damage = [f32(float(petlevel * 4 - petlevel)), f32(float(petlevel * 4 + petlevel))]
            elif e == 19668:
                if pinfo is None:
                    u.create_mana = 28 + 10 * petlevel
                    u.create_health = 28 + 30 * petlevel
                bonus_dmg = i32(f32(f32(float(self._sbdb("shadow"))) * f32(0.3)))
                u.weapon_damage = [f32(float((petlevel * 4 - petlevel) + bonus_dmg)), f32(float((petlevel * 4 + petlevel) + bonus_dmg))]
            elif e == 19833:
                u.weapon_damage = [f32(float((petlevel // 2) - 25)), f32(float((petlevel // 2) - 18))]
            elif e == 19921:
                u.weapon_damage = [f32(float(petlevel // 2 - 10)), f32(float(petlevel // 2))]
            elif e == 29264:
                if pinfo is None:
                    u.create_health = 30 * petlevel
                u.base_attack_time = cinfo.base_attack_time  # :1023
                u.weapon_damage = [f32(float(petlevel * 4 - petlevel)), f32(float(petlevel * 4 + petlevel))]
                self.set_stat_flat_modifier(UNIT_MOD_ARMOR, BASE_VALUE, f32(f32(float(owner.armor)) * f32(0.35)))  # :1028
                self.set_stat_flat_modifier(UNIT_MOD_STAT_START + STAT_STAMINA, BASE_VALUE, f32(owner.get_stat(STAT_STAMINA) * f32(0.3)))  # :1029
                u.trace.append("InitStatsForLevel: AddAura(58877 Spirit Hunt) (Pet.cpp:1031)")
            elif e == 31216:
                self.set_bonus_damage(i32(f32(f32(float(self._sbdb("frost"))) * f32(0.33))))
                if pinfo is None:
                    u.create_mana = 28 + 30 * petlevel
                    u.create_health = 28 + 10 * petlevel
                self._pre_weapon()
            elif e == 27829:
                if pinfo is None:
                    u.create_mana = 28 + 10 * petlevel
                    u.create_health = 28 + 30 * petlevel
                self.set_bonus_damage(i32(f32(f32(owner.attack_power_melee) * f32(0.5))))
                u.weapon_damage = [f32(float(petlevel - petlevel // 4)), f32(float(petlevel + petlevel // 4))]
            elif e == 28017:
                u.create_health = 4 * petlevel
                self.set_bonus_damage(i32(f32(f32(owner.attack_power_melee) * f32(0.006))))
                u.weapon_damage = [f32(float(petlevel - 30 - petlevel // 4)), f32(float(petlevel - 30 + petlevel // 4))]
            else:  # :1065-1078 default: ExpectedStat CreatureAutoAttackDps at petlevel
                if engine.base_damage_for_level is None:
                    raise SourceError(
                        f"entry {e} takes InitStatsForLevel's default branch (Pet.cpp:1071): weapon damage = "
                        "Creature::GetBaseDamageForLevel(petlevel) = EvaluateExpectedStat(CreatureAutoAttackDps, ...) "
                        "(Creature.cpp:3095-3100); supply --base-damage")
                basedamage = f32(engine.base_damage_for_level)
                u.weapon_damage = [basedamage, f32(basedamage * f32(1.5))]
        self.update_all_stats()  # :1085
        u.trace.append("InitStatsForLevel: SetFullHealth(); SetFullPower(POWER_MANA) (Pet.cpp:1087-1088)")

    def _sbdb(self, school: str) -> int:
        try:
            return self.owner.spell_base_damage_bonus[school]
        except KeyError:
            raise SourceError(f"owner SpellBaseDamageBonusDone({school}) required by this entry branch; supply --owner-sp-{school}") from None

    def _pre_weapon(self) -> None:
        """Entries 510 / 31216 leave BASE_ATTACK weapon damage untouched (Pet.cpp:958-962, 1034-1044)."""
        if self.u.is_guardian and not self.u.is_pet and self.engine.pre_weapon_damage is None:
            raise SourceError("this entry branch keeps the BASE_ATTACK weapon damage set by Creature::UpdateLevelDependantStats "
                              "(Creature.cpp:1651-1655, ExpectedStat CreatureAutoAttackDps at the SelectLevel level); not derivable")
        # Pet: Pet::Create -> InitEntry only, so m_weaponDamage keeps BASE_MINDAMAGE/BASE_MAXDAMAGE (Unit.cpp:354-358)


# ---------------------------------------------------------------------------
# pet_levelstats access (world-db corpus already extracted this pass)
# ---------------------------------------------------------------------------

PLAYER_BASE_STATS = WORLD_DB_CORPORA / "player-base-stats.json"
MAX_PLAYER_LEVEL = 90  # worldserver.conf.dist:912 MaxPlayerLevel = 90 (CONFIG_MAX_PLAYER_LEVEL)


def load_pet_levelstats(path: Path = PLAYER_BASE_STATS) -> dict[int, dict[int, PetLevelInfo]]:
    """``pet_levelstats`` as ``{creature_entry: {level: PetLevelInfo}}`` (raw rows, no gap fill)."""
    if not path.exists():
        raise SourceError(f"missing corpus {path}")
    doc = json.loads(path.read_text(encoding="utf-8"))
    table = doc["tables"]["pet_levelstats"]
    cols = table["columns"]
    idx = {c: i for i, c in enumerate(cols)}
    out: dict[int, dict[int, PetLevelInfo]] = {}
    for row in table["rows"]:
        entry, level = int(row[idx["creature_entry"]]), int(row[idx["level"]])
        out.setdefault(entry, {})[level] = PetLevelInfo(
            health=int(row[idx["hp"]]), mana=int(row[idx["mana"]]), armor=int(row[idx["armor"]]),
            stats=(int(row[idx["str"]]), int(row[idx["agi"]]), int(row[idx["sta"]]), int(row[idx["inte"]]), int(row[idx["spi"]])))
    return out


def get_pet_level_info(store: dict[int, dict[int, PetLevelInfo]], creature_id: int, level: int,
                       max_player_level: int = MAX_PLAYER_LEVEL) -> tuple[PetLevelInfo | None, str]:
    """``ObjectMgr::GetPetLevelInfo`` with ``LoadPetLevelInfo``'s gap fill (ObjectMgr.cpp:3743-3779).

    Returns ``(info, provenance)``.  Levels above ``CONFIG_MAX_PLAYER_LEVEL``
    clamp (3771-3772); a missing level copies the previous one (3756-3762);
    levels > max player level in the table are ignored at load (3708-3718).
    """
    if level > max_player_level:
        level = max_player_level
    rows = store.get(creature_id)
    if not rows:
        return None, f"no pet_levelstats rows for creature_entry={creature_id} (GetPetLevelInfo returns nullptr, ObjectMgr.cpp:3775-3776)"
    if level < 1:
        raise SourceError("level < 1")
    if 1 not in rows or rows[1].health == 0:
        raise SourceError(f"creature {creature_id} has no level-1 pet stats: Trinity ABORT()s at load (ObjectMgr.cpp:3749-3753)")
    filled: dict[int, PetLevelInfo] = {}
    for lvl in range(1, max_player_level + 1):
        row = rows.get(lvl)
        if row is None or row.health == 0:
            row = filled[lvl - 1]
            note = f"gap-filled from level {lvl - 1}"
        else:
            note = "row"
        filled[lvl] = row
        if lvl == level:
            highest = max(r for r in rows if r <= max_player_level)
            prov = f"pet_levelstats creature_entry={creature_id} level={level}: {note}" + (
                f" (highest authored level {highest})" if note != "row" else "")
            return row, prov
    raise SourceError("unreachable")


# ---------------------------------------------------------------------------
# DB2-side engine evaluators (ExpectedStat, ContentTuning, display power)
# ---------------------------------------------------------------------------

import csv  # noqa: E402

from controlled_units import TABLES  # noqa: E402

CURRENT_EXPANSION = 11  # SharedDefines.h:101/107 EXPANSION_MIDNIGHT
EXPANSION_LEVEL_CURRENT = -1  # SharedDefines.h:89
CONFIG_EXPANSION = 11  # worldserver.conf.dist:718 Expansion = 11
MAX_LEVEL = 123  # DBCEnums.h:45
STRONG_MAX_LEVEL = 255  # DBCEnums.h:49
MAX_LEVEL_FOR_EXPANSION = {0: 30, 1: 30, 2: 30, 3: 35, 4: 35, 5: 40, 6: 45, 7: 50, 8: 60, 9: 70, 10: 80, 11: 90}  # SharedDefines.h:109-139
POWER_TYPE_FLAG_IS_USED_BY_NPCS = 0x80  # DBCEnums.h:2302
CLASS_MOD_EXPECTED_STAT = {CLASS_WARRIOR: 4, CLASS_PALADIN: 2, CLASS_ROGUE: 3, CLASS_MAGE: 1}  # DB2Stores.cpp:2486-2502
EXPECTED_STAT_FIELDS = {"CreatureHealth": "CreatureHealthMod", "CreatureAutoAttackDps": "CreatureAutoAttackDPSMod",
                        "CreatureArmor": "CreatureArmorMod"}
#: 12.1 ContentTuning.csv renames the columns Trinity's ContentTuningLoadInfo (DB2LoadInfo.h:1360-1383) reads
#: positionally; same count (19) and order -> positional mapping (structural-inference, see report)
CONTENT_TUNING_POSITIONAL = {"MinLevel": 9, "MaxLevel": 10, "MinLevelType": 11, "MaxLevelType": 12,
                             "TargetLevelDelta": 13, "TargetLevelMaxDelta": 14, "TargetLevelMin": 15, "TargetLevelMax": 16}


def _csv(name: str, tables: Path = TABLES) -> list[dict[str, str]]:
    path = tables / f"{name}.csv"
    if not path.exists():
        raise SourceError(f"missing DB2 table {path}")
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


class Db2Scaling:
    """``DB2Manager::EvaluateExpectedStat`` / ``GetContentTuningData`` over the 12.1 snapshot."""

    def __init__(self, tables: Path = TABLES) -> None:
        self.expected: dict[tuple[int, int], dict[str, str]] = {}
        for row in sorted(_csv("ExpectedStat", tables), key=lambda r: int(r["ID"])):  # store order; later rows overwrite (DB2Stores.cpp:1318-1319)
            self.expected[(int(row["Lvl"]), int(row["ExpansionID"]))] = row
        self.mods = {int(r["ID"]): r for r in _csv("ExpectedStatMod", tables)}
        self.seasons = {int(r["ID"]): r for r in _csv("MythicPlusSeason", tables)}
        self.ct_mods: dict[int, list[dict[str, str]]] = {}
        for row in sorted(_csv("ContentTuningXExpected", tables), key=lambda r: int(r["ID"])):
            if int(row["ExpectedStatModID"]) in self.mods:  # DB2Stores.cpp:1287-1289
                self.ct_mods.setdefault(int(row["ContentTuningID"]), []).append(row)
        ct_rows = _csv("ContentTuning", tables)
        header = list(ct_rows[0].keys()) if ct_rows else []
        if len(header) != 19:
            raise SourceError(f"ContentTuning.csv has {len(header)} columns; positional mapping to Trinity's 19-field layout invalid")
        self.content_tuning = {int(r["ID"]): {k: int(float(r[header[i]])) for k, i in CONTENT_TUNING_POSITIONAL.items()} for r in ct_rows}
        self.content_tuning_header = {k: header[i] for k, i in CONTENT_TUNING_POSITIONAL.items()}
        self.conditional = {int(r["ParentContentTuningID"]) for r in _csv("ConditionalContentTuning", tables)} if (tables / "ConditionalContentTuning.csv").exists() else set()
        self.chr_display_power = {int(r["ID"]): int(r["DisplayPower"]) for r in _csv("ChrClasses", tables)}
        self.power_types = {int(r["PowerTypeEnum"]): r for r in _csv("PowerType", tables)}

    def evaluate(self, stat: str, level: int, expansion: int, content_tuning_id: int, unit_class: int,
                 milestone_season: int = 0) -> tuple[float, str]:
        """``EvaluateExpectedStat`` (DB2Stores.cpp:2476-2580), float arithmetic."""
        row = self.expected.get((level, expansion))
        key = f"ExpectedStat(Lvl={level}, ExpansionID={expansion})"
        if row is None:
            row = self.expected.get((level, -2))
            key = f"ExpectedStat(Lvl={level}, ExpansionID=-2 fallback)"
        if row is None:
            return 1.0, f"no ExpectedStat row for level {level} -> 1.0f (DB2Stores.cpp:2482-2483)"
        value = f32(float(row[stat]))
        mod_field = EXPECTED_STAT_FIELDS[stat]
        mods = self.ct_mods.get(content_tuning_id)
        applied = []
        if mods:
            acc = f32(1.0)
            for m in mods:  # ExpectedStatModReducer DB2Stores.cpp:2451-2471
                lo, hi = int(m["MinMythicPlusSeasonID"]), int(m["MaxMythicPlusSeasonID"])
                if lo and lo in self.seasons and milestone_season < int(self.seasons[lo]["MilestoneSeason"]):
                    continue
                if hi and hi in self.seasons and milestone_season >= int(self.seasons[hi]["MilestoneSeason"]):
                    continue
                acc = f32(acc * f32(float(self.mods[int(m["ExpectedStatModID"])][mod_field])))
                applied.append(int(m["ExpectedStatModID"]))
            value = f32(value * acc)
        cls = CLASS_MOD_EXPECTED_STAT.get(unit_class)
        if cls is not None:
            value = f32(value * f32(float(self.mods[cls][mod_field])))
        return value, f"{key} ID={row['ID']} {stat}={row[stat]}; ContentTuningXExpected mods {applied}; class mod {cls}"

    def content_tuning_levels(self, content_tuning_id: int) -> dict[str, int] | None:
        """``GetContentTuningData(id, {})`` (DB2Stores.cpp:2192-2239); empty redirect span -> no redirect (:2173-2190)."""
        ct = self.content_tuning.get(content_tuning_id)
        if ct is None:
            return None

        def adj(kind: int) -> int:
            return {1: 1, 2: MAX_LEVEL_FOR_EXPANSION[CONFIG_EXPANSION], 3: MAX_LEVEL_FOR_EXPANSION[max(CONFIG_EXPANSION - 1, 0)]}.get(kind, 0)

        lo = ct["MinLevel"] + adj(ct["MinLevelType"])
        hi = ct["MaxLevel"] + adj(ct["MaxLevelType"])
        return {"MinLevel": min(max(lo, 1), MAX_LEVEL), "MaxLevel": min(max(hi, 1), MAX_LEVEL)}

    def display_power(self, unit_class: int, is_hunter_pet: bool) -> tuple[int, int, str]:
        """``Unit::CalculateDisplayPowerType`` (Unit.cpp:5761-5805) without shapeshift/MOD_POWER_DISPLAY/vehicle,
        then ``SetPowerType``'s IsUsedByNPCs gate (Unit.cpp:5705-5706): an NPC-unusable power leaves DisplayPower 0 (mana)."""
        power = self.chr_display_power.get(unit_class, POWER_MANA)
        if is_hunter_pet:
            power = POWER_FOCUS
        pt = self.power_types.get(power)
        if pt is None or not int(pt["Flags"]) & POWER_TYPE_FLAG_IS_USED_BY_NPCS:
            return power, POWER_MANA, f"power {power} lacks PowerTypeFlags::IsUsedByNPCs -> SetPowerType returns, DisplayPower stays 0 (mana)"
        return power, power, f"ChrClasses[{unit_class}].DisplayPower={self.chr_display_power.get(unit_class)}" + (" -> hunter pet focus" if is_hunter_pet else "")

    def max_base_power(self, power: int) -> int:
        pt = self.power_types.get(power)
        return int(pt["MaxBasePower"]) if pt else 0


# ---------------------------------------------------------------------------
# world-db rows (creature_template / _difficulty / creature_classlevelstats)
# ---------------------------------------------------------------------------

WORLD_CANDIDATES = (WORLD_DB_CORPORA / "creature-templates.json", WORLD_DB_CORPORA / "pet-witness-templates.json")


def _table_rows(doc: dict[str, Any], name: str) -> list[dict[str, Any]]:
    t = doc.get("tables", {}).get(name)
    if not t:
        return []
    cols = t["columns"]
    return [dict(zip(cols, r)) for r in t["rows"]]


class WorldRows:
    """World-db facts for creature entries; ``status`` says whether a corpus was found."""

    def __init__(self, paths: tuple[Path, ...] = WORLD_CANDIDATES) -> None:
        self.template: dict[int, dict[str, Any]] = {}
        self.difficulty: dict[int, dict[str, Any]] = {}
        self.classlevel: dict[tuple[int, int], dict[str, Any]] = {}
        self.template_spells: dict[int, list[dict[str, Any]]] = {}
        self.sources: list[str] = []
        for path in paths:
            if not path.exists():
                continue
            doc = json.loads(path.read_text(encoding="utf-8"))
            self.sources.append(str(path.relative_to(WORLD_DB_CORPORA.parents[2])))
            for r in _table_rows(doc, "creature_template"):
                self.template.setdefault(int(r["entry"]), r)
            for r in _table_rows(doc, "creature_template_difficulty"):
                if int(r["DifficultyID"]) == 0:
                    self.difficulty.setdefault(int(r["Entry"]), r)
            for r in _table_rows(doc, "creature_classlevelstats"):
                self.classlevel.setdefault((int(r["level"]), int(r["class"])), r)
            for r in _table_rows(doc, "creature_template_spell"):
                self.template_spells.setdefault(int(r["CreatureID"]), []).append(r)
        self.status = "present" if self.template else "corpus-missing"


def guardian_pre_state(eng: EngineInputs, es_health_sel: float, health_modifier: float, mana_modifier: float,
                       dps_sel: float) -> tuple[int, int]:
    """State ``Creature::UpdateLevelDependantStats`` leaves at the SelectLevel level (Creature.cpp:1625-1671)."""
    import math
    # GetMaxHealthByLevel: double baseHealth = float ES; ceil(baseHealth * HealthModifier) -> uint64 (Creature.cpp:3077-3083);
    # uint32 basehp = that (Creature.cpp:1635)
    basehp = int(math.ceil(float(f32(es_health_sel)) * float(f32(health_modifier)))) & 0xFFFFFFFF
    health = u32(f32(f32(float(basehp)) * f32(eng.health_rate)))  # uint32(basehp * healthmod) (Creature.cpp:1636)
    eng.pre_health_base_value = f32(float(health))  # (float)health (Creature.cpp:1643)
    eng.pre_mana_base_pct = f32(mana_modifier)  # Creature.cpp:1648
    eng.pre_weapon_damage = (f32(dps_sel), f32(f32(dps_sel) * f32(1.5)))  # Creature.cpp:1651-1655
    return basehp, health


def engine_inputs_from_sources(entry: int, is_pet: bool, petlevel: int, summoner_level: int, db2: Db2Scaling,
                               world: WorldRows) -> tuple[EngineInputs, CreatureFacts, dict[str, Any]]:
    """Derive every engine input Trinity computes from DB2 + world-db for one creature entry.

    Anything random (``irand`` level delta) or missing fails closed per field
    (field left ``None``; the oracle raises only if a branch reads it).
    """
    notes: dict[str, Any] = {"world_sources": world.sources}
    tmpl = world.template.get(entry)
    diff = world.difficulty.get(entry)
    if tmpl is None:
        raise SourceError(f"creature_template {entry} not in world-db corpus ({world.status})")
    unit_class = int(tmpl["unit_class"])
    cinfo = CreatureFacts(entry=entry, unit_class=unit_class, dmgschool=int(tmpl.get("dmgschool") or 0),
                          base_attack_time=int(tmpl.get("BaseAttackTime") or BASE_ATTACK_TIME))
    eng = EngineInputs()
    if diff is None:
        # CreatureTemplate::GetDifficulty (Creature.cpp:252-289): no DIFFICULTY_NONE row and no Difficulty.db2 ID 0
        # -> static DefaultCreatureDifficulty (deltas 0, ContentTuningID 0, HealthScalingExpansion 0, modifiers 1.0)
        diff = {"ContentTuningID": 0, "HealthScalingExpansion": 0, "HealthModifier": 1.0, "ManaModifier": 1.0,
                "LevelScalingDeltaMin": 0, "LevelScalingDeltaMax": 0}
        notes["creature_template_difficulty"] = "absent -> DefaultCreatureDifficulty (Creature.cpp:264-288)"
    ct_id = int(diff["ContentTuningID"])
    hse = int(diff["HealthScalingExpansion"])
    expansion = CURRENT_EXPANSION if hse == EXPANSION_LEVEL_CURRENT else hse  # CreatureData.h:470-473
    hm = f32(float(diff["HealthModifier"]))
    notes.update({"ContentTuningID": ct_id, "HealthScalingExpansion": hse, "HealthModifier": hm,
                  "ManaModifier": float(diff["ManaModifier"]), "DeltaLevelMin": int(diff["LevelScalingDeltaMin"]),
                  "DeltaLevelMax": int(diff["LevelScalingDeltaMax"]), "Classification": tmpl.get("Classification")})
    eng.health_modifier = hm
    es, prov = db2.evaluate("CreatureHealth", petlevel, expansion, ct_id, unit_class)
    eng.expected_health_at_petlevel = es
    bd, prov2 = db2.evaluate("CreatureAutoAttackDps", petlevel, expansion, ct_id, unit_class)
    eng.base_damage_for_level = bd
    notes["expected_stat_petlevel"] = {"CreatureHealth": prov, "CreatureAutoAttackDps": prov2}
    cls_row = world.classlevel.get((petlevel, unit_class))
    eng.base_mana = int(cls_row["basemana"]) if cls_row else 0  # defStats BaseMana 0 (ObjectMgr.cpp:9997-10003)
    notes["creature_classlevelstats"] = "row" if cls_row else f"absent for (level {petlevel}, class {unit_class}) -> 0"
    if not is_pet:
        levels = db2.content_tuning_levels(ct_id)
        dmin, dmax = int(diff["LevelScalingDeltaMin"]), int(diff["LevelScalingDeltaMax"])
        lo_d, hi_d = min(dmin, dmax), max(dmin, dmax)
        if lo_d != hi_d:
            notes["select_level"] = f"ScalingLevelDelta = irand({lo_d},{hi_d}) (Creature.cpp:3071): nondeterministic"
            return eng, cinfo, notes
        smin, smax = (levels["MinLevel"], levels["MaxLevel"]) if levels else (0, 0)
        select_level = min(max(smax + lo_d, 1), STRONG_MAX_LEVEL)  # RoundToInterval (Creature.cpp:1618-1619)
        stat_level = min(max(summoner_level, smin + lo_d), smax + lo_d)  # TempSummon::InitStats std::clamp (TemporarySummon.cpp:243-246)
        notes.update({"content_tuning_levels": levels, "select_level": select_level, "tempsummon_level": stat_level})
        es_sel, prov3 = db2.evaluate("CreatureHealth", select_level, expansion, ct_id, unit_class)
        dps_sel, prov4 = db2.evaluate("CreatureAutoAttackDps", select_level, expansion, ct_id, unit_class)
        basehp, health = guardian_pre_state(eng, es_sel, hm, f32(float(diff["ManaModifier"])), dps_sel)
        notes["expected_stat_select_level"] = {"CreatureHealth": prov3, "CreatureAutoAttackDps": prov4, "basehp": basehp, "health": health}
    return eng, cinfo, notes


# ---------------------------------------------------------------------------
# consumer table (report section 6)
# ---------------------------------------------------------------------------

UNIT_CLASSES = {
    "Pet/SUMMON_PET": "Pet class (Entities/Pet/Pet.cpp), PetType SUMMON_PET: created by Player::SummonPet (Player.cpp:30438) from SPELL_EFFECT_SUMMON_PET (SpellEffects.cpp:2656) or loaded by Pet::LoadPetFromDB (Pet.cpp:205); warlock demons, DK ghoul via 52150, mage 78116 (class-mismatch path)",
    "Pet/HUNTER_PET": "Pet class, PetType HUNTER_PET: Unit::CreateTamedPetFrom (Unit.cpp:11089/11113) or Pet::LoadPetFromDB for Call Pet (883, MiscValue 0)",
    "Guardian": "Guardian (TemporarySummon.h:129) allocated by Map::SummonCreature (Object.cpp:1251) for SummonProperties Control=PET or Control=WILD/ALLY with Title Minion/Guardian/Runeblade or JoinSummonerSpawnGroup; stats via Guardian::InitStats -> InitStatsForLevel (TemporarySummon.cpp:525-535)",
    "Minion": "Minion (TemporarySummon.h:95) for Control=VEHICLE/POSSESSED_VEHICLE or Title Companion (Object.cpp:1206-1230); no Guardian stat code, plain Creature stats",
    "Totem": "Totem (Entities/Totem) for Title Totem/Lightwell (Object.cpp:1220-1222); Creature stats, health overridden by the summon effect value when non-zero (SpellEffects.cpp:1978-1982)",
    "Puppet": "Puppet (TemporarySummon.h:155) for Control=PUPPET (Object.cpp:1203-1204); Minion::InitStats + possess; plain Creature stats",
    "TempSummon": "plain TempSummon (Object.cpp:1248-1249) for Title None/Vehicle/Mount without JoinSummonerSpawnGroup; plain Creature stats; owner set only for Control=ALLY (SpellEffects.cpp:2023-2024)",
}

# classification vocabulary (brief section 5 + task prompt)
CREATION_SNAPSHOT = "creation-snapshot"
APPLICATION_SNAPSHOT = "application-snapshot"
DYNAMIC = "dynamic-owner-lookup"
EXPLICIT = "explicit-recalculation"
INDEPENDENT = "independent-pet-value"
LEGACY = "legacy-only"
UNRESOLVED = "unresolved"

NUM = {  # numeric-behaviour record helpers
    "float": {"source_type": "float", "intermediate_type": "float (binary32)", "aggregation_precision": "binary32 per operation, -ffp-contract=off"},
}


def _row(unit_class: str, stat: str, consumer: str, coords: list[str], classification: str, inputs: str,
         arithmetic: str, numeric: dict[str, Any] | None = None, notes: str = "", evidence: str = "trinity-consumer") -> dict[str, Any]:
    return {"unit_class": unit_class, "stat": stat, "consumer": consumer, "coordinates": [TC + c for c in coords],
            "classification": classification, "inputs": inputs, "arithmetic": arithmetic,
            "numeric": numeric or {}, "notes": notes, "evidence_class": evidence}


def consumer_table() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    guardian_like = ("Pet/SUMMON_PET", "Pet/HUNTER_PET", "Guardian")
    creature_like = ("Minion", "Totem", "Puppet", "TempSummon")
    # --- level ---------------------------------------------------------------
    rows.append(_row("Pet/SUMMON_PET", "level", "Pet::LoadPetFromDB / Player::SummonPet / Pet::SynchronizeLevelWithOwner",
                     ["Entities/Pet/Pet.cpp:289-290", "Entities/Pet/Pet.cpp:309", "Entities/Player/Player.cpp:30491", "Entities/Pet/Pet.cpp:1784-1798"],
                     EXPLICIT, "owner->GetLevel()", "petlevel = owner level; SynchronizeLevelWithOwner -> GivePetLevel(owner level) -> InitStatsForLevel + InitLevelupSpellsForLevel (Pet.cpp:753-766); re-run on owner level change via Player::GiveLevel",
                     {"source_type": "uint8", "intermediate_type": "uint8", "rounding": "none", "units": "level"}))
    rows.append(_row("Pet/HUNTER_PET", "level", "Unit::CreateTamedPetFrom / Pet::GivePetXP / SynchronizeLevelWithOwner",
                     ["Entities/Unit/Unit.cpp:11102", "Entities/Pet/Pet.cpp:716-751", "Entities/Pet/Pet.cpp:1791-1793"],
                     EXPLICIT, "creature level for target, owner level, PetExperience",
                     "tame: level = max(targetLevel, ownerLevel-5) (Unit.cpp:11102); XP path: PET_XP_FACTOR 0.05f * GetXPForLevel (Pet.cpp:44/761); load: GivePetLevel(owner level) (Pet.cpp:1793)",
                     {"source_type": "uint8", "intermediate_type": "uint32 XP, uint8 level", "rounding": "uint32() of float XP product", "units": "level"}))
    rows.append(_row("Guardian", "level", "TempSummon::InitStats", ["Entities/Creature/TemporarySummon.cpp:241-247", "Entities/Creature/TemporarySummon.cpp:529"],
                     CREATION_SNAPSHOT, "summoner level, UnitData::ScalingLevelMin/Max/Delta (ContentTuning of creature_template_difficulty), SummonPropertiesFlags::UseCreatureLevel",
                     "level = clamp(summoner level, ScalingLevelMin+Delta, ScalingLevelMax+Delta) unless UseCreatureLevel; then Guardian::InitStats calls InitStatsForLevel(GetLevel()); never re-synchronised",
                     {"source_type": "uint8", "intermediate_type": "int32 clamp", "rounding": "none", "units": "level"}))
    for uc in creature_like:
        rows.append(_row(uc, "level", "Creature::SelectLevel then TempSummon::InitStats", ["Entities/Creature/Creature.cpp:1614-1623", "Entities/Creature/TemporarySummon.cpp:241-247"],
                         CREATION_SNAPSHOT, "ContentTuning levels + creature_template_difficulty delta; summoner level",
                         "SelectLevel sets level = ScalingLevelMax+Delta and computes stats at that level; TempSummon::InitStats then overwrites the level with the clamped summoner level WITHOUT recomputing stats (Unit::SetLevel only notifies players)",
                         {"source_type": "uint8", "intermediate_type": "int32", "rounding": "RoundToInterval(1..255)", "units": "level"},
                         notes="displayed level and stat level can differ below the ContentTuning maximum"))
    # --- primary stats -------------------------------------------------------
    for uc in guardian_like:
        rows.append(_row(uc, "strength/agility/intellect/spirit (base)", "Guardian::InitStatsForLevel -> SetCreateStat", ["Entities/Pet/Pet.cpp:890-917"],
                         INDEPENDENT, "pet_levelstats.str/agi/sta/inte/spi (uint16) for creature_entry (1 for HUNTER_PET) at petlevel, else fake 22/22/25/28",
                         "m_createStats[stat] = float(pInfo->stats[stat]); GetTotalStatValue = (CalculatePct(flat BASE_VALUE, BASE_PCT_EXCLUDE_CREATE) + create) * BASE_PCT + TOTAL_VALUE) * TOTAL_PCT (Unit.cpp:9828-9842)",
                         {"source_type": "uint16 (world db)", "intermediate_type": "float", "rounding": "int32(value) at SetStat (StatSystem.cpp:1178)", "units": "stat points"},
                         notes="pet_levelstats authored to level 85 (entry 1 rows 81-85 are all-1 placeholders; entry 26125 ends at 80); Trinity gap-fills 86-90 from the last row (ObjectMgr.cpp:3756-3762)", evidence="world-db-fact"))
        rows.append(_row(uc, "stamina (owner share)", "Guardian::UpdateStats", ["Entities/Unit/StatSystem.cpp:1146-1160"],
                         EXPLICIT, "owner->GetStat(STAT_STAMINA) (float of int32)",
                         "ghoul 26125: + float(ownerSta) * 0.3f; every other guardian: + CalculatePct(ownerSta, 30); recomputed only when Player::UpdateStats(STA) runs and only for GetPet() (StatSystem.cpp:114-119) or at InitStatsForLevel",
                         {"source_type": "int32 UnitData::Stats", "intermediate_type": "float", "rounding": "int32(value)", "units": "stat points"}))
        rows.append(_row(uc, "strength (owner share)", "Guardian::UpdateStats", ["Entities/Unit/StatSystem.cpp:1146-1155"],
                         EXPLICIT, "owner->GetStat(STAT_STRENGTH)", "ghoul 26125 only: + float(ownerStr) * 0.7f; all other entries: no owner share (the 0.3 ghoul branch at :1170-1176 is commented out)",
                         {"source_type": "int32", "intermediate_type": "float", "rounding": "int32(value)", "units": "stat points"}))
        rows.append(_row(uc, "intellect (owner share)", "Guardian::UpdateStats", ["Entities/Unit/StatSystem.cpp:1162-1169"],
                         EXPLICIT, "owner->GetStat(STAT_INTELLECT), owner class", "warlock or mage owner: + CalculatePct(ownerInt, 30); otherwise none",
                         {"source_type": "int32", "intermediate_type": "float", "rounding": "int32(value)", "units": "stat points"}))
    # --- health --------------------------------------------------------------
    for uc in guardian_like:
        rows.append(_row(uc, "health", "Guardian::UpdateMaxHealth", ["Entities/Unit/StatSystem.cpp:1253-1276", "Entities/Pet/Pet.cpp:893", "Entities/Pet/Pet.cpp:911"],
                         EXPLICIT, "BaseHealth (pet_levelstats.hp or ExpectedStat), Stats[STA] - createSta, entry multiplier, UNIT_MOD_HEALTH modifiers",
                         "(flat BASE_VALUE + BaseHealth) * BASE_PCT + (TOTAL_VALUE + (sta - createSta) * mult) then * TOTAL_PCT; mult = 8.4 imp, 11 voidwalker/felguard, 9.1 succubus, 9.5 felhunter, 1 bloodworm, 10 default; (uint32) truncation",
                         {"source_type": "uint16 hp / int32 stats", "intermediate_type": "float", "rounding": "(uint32)value truncation; 0 -> 1", "units": "health"},
                         notes="Guardian (non-Pet): flat BASE_VALUE is NOT zero -- Map::SummonCreature (Object.cpp:1265) -> Creature::Create -> CreateFromProto (Creature.cpp:1151) -> UpdateEntry -> SelectLevel -> UpdateLevelDependantStats stored uint32(ceil(ES*HealthModifier)) at the SelectLevel level (Creature.cpp:1643); the Guardian ctor already set UNIT_MASK_GUARDIAN (TemporarySummon.cpp:516) and InitStatsForLevel never resets it, so the ExpectedStat health is counted in addition to BaseHealth (confirmed by tools/tc_pet_probe, tests/test_cu_differential.py)" if uc == "Guardian" else "Pet::Create calls InitEntry only (Pet.cpp:1690): UNIT_MOD_HEALTH BASE_VALUE stays 0",
                         evidence="differential" if uc == "Guardian" else "trinity-consumer"))
    for uc in creature_like:
        rows.append(_row(uc, "health", "Creature::UpdateLevelDependantStats / Creature::UpdateMaxHealth", ["Entities/Creature/Creature.cpp:1632-1643", "Entities/Creature/Creature.cpp:3077-3083", "Entities/Unit/StatSystem.cpp:999-1003"],
                         INDEPENDENT, "ExpectedStat CreatureHealth at SelectLevel level, creature_template_difficulty.HealthModifier, Rate.Creature.*.HP, classification",
                         "health = uint32(ceil(ExpectedStat * HealthModifier) * healthmod); UNIT_MOD_HEALTH BASE_VALUE = health; MaxHealth = uint32(GetTotalAuraModValue(UNIT_MOD_HEALTH))",
                         {"source_type": "float ExpectedStat", "intermediate_type": "double ceil, float rate", "rounding": "ceil then uint32()", "units": "health"},
                         notes="Totem: SpellEffects.cpp:1978-1982 overrides MaxHealth/Health with the summon effect value when non-zero (Healing Stream Totem 5394 bp=5 -> 5 health)" if uc == "Totem" else ""))
    # --- armor ---------------------------------------------------------------
    for uc in guardian_like:
        rows.append(_row(uc, "armor", "Guardian::UpdateArmor + InitStatsForLevel", ["Entities/Unit/StatSystem.cpp:1231-1251", "Entities/Pet/Pet.cpp:872", "Entities/Pet/Pet.cpp:898-899", "Entities/Pet/Pet.cpp:1028"],
                         EXPLICIT, "petlevel*50 or pet_levelstats.armor, owner->GetArmor() (uint32)",
                         "HUNTER_PET: bonus = float(CalculatePct(ownerArmor, 70)) (comment says 35%); other Pet: bonus = ownerArmor; non-Pet Guardian: 0; value = BASE*BASE_PCT + TOTAL + bonus, * TOTAL_PCT; SetArmor(int32 base, int32(value-base)); recomputed by Player::UpdateArmor only for GetPet() (StatSystem.cpp:276-278)",
                         {"source_type": "uint32", "intermediate_type": "float", "rounding": "CalculatePct truncates to uint32 before float()", "units": "armor"},
                         notes="Feral Spirit 29264 InitStatsForLevel additionally sets BASE_VALUE = float(ownerArmor) * 0.35f (Pet.cpp:1028)"))
    for uc in creature_like:
        rows.append(_row(uc, "armor", "Creature::UpdateLevelDependantStats / Creature::UpdateArmor", ["Entities/Creature/Creature.cpp:1669-1670", "Entities/Creature/Creature.cpp:3112-3118", "Entities/Unit/StatSystem.cpp:992-997"],
                         INDEPENDENT, "ExpectedStat CreatureArmor * ArmorModifier", "BASE_VALUE = ExpectedStat(CreatureArmor) * creature_template_difficulty.ArmorModifier; SetArmor(int32(base), int32(total-base))",
                         {"source_type": "float", "intermediate_type": "float", "rounding": "int32()", "units": "armor"}))
    # --- attack power --------------------------------------------------------
    for uc in guardian_like:
        rows.append(_row(uc, "attack_power", "Guardian::UpdateAttackPowerAndDamage", ["Entities/Unit/StatSystem.cpp:1293-1355"],
                         EXPLICIT, "Stats[STR]; owner GetTotalAttackPowerValue(RANGED|BASE) for hunter/ghoul/spirit wolf; owner ModDamageDonePos/Neg fire/shadow for other Pets",
                         "val = STR-10 (imp 416) else 2*STR-20; bonusAP: hunter pet rangedAP*0.22, ghoul 26125 meleeAP*0.22, spirit wolf 29264 meleeAP*0.31, other Pet max(fire,shadow SP)*0.57, else 0; BASE_VALUE = val+bonusAP; AttackPower = int32(BASE_VALUE*BASE_PCT); multiplier = TOTAL_PCT-1",
                         {"source_type": "float/int32", "intermediate_type": "float", "rounding": "int32() at SetAttackPower", "units": "attack power"},
                         notes="triggers: Player::UpdateAttackPowerAndDamage (StatSystem.cpp:400-423) for GetPet() hunter (ranged) / ghoul (melee) and GetGuardianPet() spirit wolf; AuraEffect::HandleModDamageDone (SpellAuraEffects.cpp:4731-4732) for GetGuardianPet(); Guardian::UpdateStats(STR); NOT item spell power (Player::ApplySpellPowerBonus StatSystem.cpp:151-168 has no pet call)"))
    for uc in creature_like:
        rows.append(_row(uc, "attack_power", "Creature::UpdateAttackPowerAndDamage", ["Entities/Creature/Creature.cpp:1666-1667", "Entities/Unit/StatSystem.cpp:1042-1068"],
                         INDEPENDENT, "creature_classlevelstats.attackpower/rangedattackpower at SelectLevel level (default 0 when the row is missing, ObjectMgr.cpp:9997-10003)",
                         "AttackPower = int32(BASE_VALUE * BASE_PCT); multiplier = TOTAL_PCT - 1", {"source_type": "uint32", "intermediate_type": "float", "rounding": "int32()", "units": "attack power"}))
    # --- spell power / bonus damage -----------------------------------------
    for uc in guardian_like:
        rows.append(_row(uc, "spell_power (m_bonusSpellDamage)", "Guardian::SetBonusDamage <- UpdateAttackPowerAndDamage / InitStatsForLevel", ["Entities/Unit/StatSystem.cpp:1314", "Entities/Unit/StatSystem.cpp:1319", "Entities/Unit/StatSystem.cpp:1325", "Entities/Unit/StatSystem.cpp:1335", "Entities/Unit/StatSystem.cpp:1344", "Entities/Pet/Pet.cpp:929-935", "Entities/Unit/Unit.cpp:6845-6848"],
                         EXPLICIT, "owner AP or owner ModDamageDonePos/Neg (client fields), owner SpellBaseDamageBonusDone for entry branches",
                         "int32(rangedAP*0.1287) hunter; int32(meleeAP*0.1287) ghoul; int32(meleeAP*0.31) spirit wolf; int32(max(fire,shadow)*0.15) Pet; int32(frost*0.4) entry 510; consumed by Unit::SpellDamageBonusDone as DoneAdvertisedBenefit += GetBonusDamage() for UNIT_MASK_GUARDIAN (Unit.cpp:6847-6848)",
                         {"source_type": "int32 ModDamageDone*, float AP", "intermediate_type": "float", "rounding": "int32() truncation", "units": "spell power"},
                         notes="InitStatsForLevel's SUMMON_PET branch uses ModDamageDonePos only (Pet.cpp:929-930) whereas UpdateAttackPowerAndDamage subtracts ModDamageDoneNeg (:1330-1331); the Update value wins because UpdateAllStats runs last"))
    for uc in creature_like:
        rows.append(_row(uc, "spell_power", "Unit::SpellBaseDamageBonusDone", ["Entities/Unit/Unit.cpp:7083-7118", "Entities/Unit/Unit.cpp:6833-6836"],
                         INDEPENDENT if uc != "Totem" else DYNAMIC, "own SPELL_AURA_MOD_DAMAGE_DONE auras; Totem: owner",
                         "non-player: only GetTotalAuraModifierByMiscMask(SPELL_AURA_MOD_DAMAGE_DONE); Totem: SpellDamageBonusDone forwards the whole computation to the owner (Unit.cpp:6834-6836)",
                         {"source_type": "int32", "intermediate_type": "int32", "rounding": "none", "units": "spell power"}))
    # --- weapon damage -------------------------------------------------------
    for uc in guardian_like:
        rows.append(_row(uc, "weapon_damage (synthetic)", "Guardian::InitStatsForLevel + Guardian::UpdateDamagePhysical", ["Entities/Pet/Pet.cpp:937-938", "Entities/Pet/Pet.cpp:948-950", "Entities/Pet/Pet.cpp:956-1079", "Entities/Unit/StatSystem.cpp:1361-1402"],
                         EXPLICIT, "petlevel, entry branch, AP (no weapon), base attack time 2000ms, owner nature/fire SP for entries 1964/15438",
                         "SUMMON/HUNTER: min = level - level/4, max = level + level/4 (integer division); entry branches per Pet.cpp:956-1079; default: ExpectedStat(CreatureAutoAttackDps) x1 / x1.5; MinDamage = ((base + AP/3.5*speed + bonus + weaponMin) * BASE_PCT + TOTAL) * TOTAL_PCT",
                         {"source_type": "uint8 level / float", "intermediate_type": "float", "rounding": "integer division before float()", "units": "damage per swing (stored as float update field)"}))
    for uc in creature_like:
        rows.append(_row(uc, "weapon_damage", "Creature::UpdateLevelDependantStats + Creature::CalculateMinMaxDamage", ["Entities/Creature/Creature.cpp:1651-1664", "Entities/Unit/StatSystem.cpp:1070-1117"],
                         INDEPENDENT, "ExpectedStat CreatureAutoAttackDps at SelectLevel level, creature_template.BaseVariance, creature_template_difficulty.DamageModifier",
                         "weapon min = dps, max = dps*1.5; damage = ((weapon + BASE + AP/3.5*variance) * DamageModifier * BASE_PCT*speedMulti + TOTAL) * TOTAL_PCT", {"source_type": "float", "intermediate_type": "float", "rounding": "none", "units": "damage per swing"}))
    # --- haste / crit / mastery / versatility --------------------------------
    for uc in guardian_like + creature_like:
        rows.append(_row(uc, "haste", "AuraEffect::HandleModMeleeSpeedPct / HandleModCastingSpeed on the unit itself", ["Spells/Auras/SpellAuraEffects.cpp:4575-4592", "Spells/Auras/SpellAuraEffects.cpp:4483-4523", "Entities/Unit/Unit.cpp:10983-11007"],
                         UNRESOLVED, "auras applied to the unit (none of the 12.1 family passives carries a haste aura)",
                         "Trinity has no owner->pet haste propagation: haste is only m_modAttackSpeedPct/ModCastingSpeed changed by auras on the unit; Player::UpdateRating has no pet call",
                         {"source_type": "int32 aura amount", "intermediate_type": "float", "rounding": "ApplyPercentModFloatVar", "units": "percent"},
                         notes="Retail pets inherit owner haste; no consumer in the pinned checkout -> unresolved (absent from pinned Trinity != absent from Retail)"))
        rows.append(_row(uc, "crit", "Unit::GetUnitCriticalChanceDone / SpellCritChanceDone", ["Entities/Unit/Unit.cpp:2896-2903", "Entities/Unit/Unit.cpp:7124-7125", "Entities/Unit/Unit.cpp:7147", "Entities/Unit/Unit.cpp:2927-2933"],
                         INDEPENDENT, "5.0f melee base + SPELL_AURA_MOD_WEAPON_CRIT_PERCENT/MOD_CRIT_PCT; spell crit m_baseSpellCritChance 5.0f only when GetSpellModOwner() != nullptr (Pet/Totem); victim's SPELL_AURA_MOD_CRIT_CHANCE_FOR_CASTER_PET by summoner GUID",
                         "creature melee crit = 5 + auras; creature spell crit = 0 unless GetSpellModOwner() (Pet or Totem with player owner) then m_baseSpellCritChance (5.0f, Unit.cpp:367); no owner crit inheritance",
                         {"source_type": "float", "intermediate_type": "float", "rounding": "none", "units": "percent"}, notes="non-Pet Guardians cannot spell-crit at all in Trinity (Unit.cpp:7124-7125, GetSpellModOwner Object.cpp:1656 covers IsPet()||IsTotem() only)"))
        rows.append(_row(uc, "versatility", "Unit::SpellDamagePctDone / MeleeDamageBonusDone via GetSpellModOwner", ["Entities/Unit/Unit.cpp:6926-6928", "Entities/Unit/Unit.cpp:7018-7024", "Entities/Unit/Unit.cpp:8211-8217", "Entities/Object/Object.cpp:1648-1670"],
                         DYNAMIC if uc.startswith("Pet") or uc == "Totem" else UNRESOLVED, "owner CR_VERSATILITY_DAMAGE_DONE rating bonus + SPELL_AURA_MOD_VERSATILITY, read at damage time",
                         "AddPct(DoneTotalMod, modOwner->GetRatingBonusValue(CR_VERSATILITY_DAMAGE_DONE) + auras) where modOwner = GetSpellModOwner() -> player owner only for IsPet()||IsTotem()",
                         {"source_type": "float rating bonus", "intermediate_type": "float", "rounding": "none", "units": "percent"},
                         notes="non-Pet Guardians (Wild Imp, elementals, Mirror Image ...) get no owner versatility in Trinity"))
        rows.append(_row(uc, "mastery", "none", ["Spells/Auras/SpellAuraEffects.cpp:390"], UNRESOLVED, "n/a",
                         "SPELL_AURA_MASTERY handler is player-only (Player::UpdateMastery StatSystem.cpp:541); no creature/pet mastery consumer", {}, notes="pet-affecting mastery effects reach pets only as ordinary auras cast on them by scripts"))
        rows.append(_row(uc, "spell_mods (owner talents/SPELL_AURA_ADD_PCT_MODIFIER)", "WorldObject::GetSpellModOwner -> Player::ApplySpellMod", ["Entities/Object/Object.cpp:1648-1670", "Entities/Unit/Unit.cpp:6854-6858", "Entities/Unit/Unit.cpp:6895-6896", "Entities/Unit/Unit.cpp:7168-7169"],
                         DYNAMIC if uc.startswith("Pet") or uc == "Totem" else UNRESOLVED, "owner's spell mods keyed by the pet spell's SpellClassOptions family/flags",
                         "Pet and Totem casts apply the player's spellmods (coefficient, damage, crit chance ...) at cast/damage time; Guardian/Minion/TempSummon casts apply none",
                         {"source_type": "int32/float", "intermediate_type": "float", "rounding": "per ApplySpellMod", "units": "percent/flat"}))
        rows.append(_row(uc, "damage_percent_done", "Unit::SpellDamagePctDone / MeleeDamageBonusDone", ["Entities/Unit/Unit.cpp:6922-6924", "Entities/Unit/Unit.cpp:8078-8081"],
                         INDEPENDENT, "own SPELL_AURA_MOD_DAMAGE_PERCENT_DONE auras; Creature::GetSpellDamageMod(classification) for non-Pet creatures",
                         "non-Pet creature spell damage is multiplied by Rate.Creature.*.SpellDamage (Creature.cpp:1739-1760); Pet skips it (Unit.cpp:6923)", {"source_type": "float", "intermediate_type": "float", "rounding": "none", "units": "multiplier"}))
    # --- power ---------------------------------------------------------------
    for uc in guardian_like:
        rows.append(_row(uc, "mana/power", "Guardian::UpdateMaxPower", ["Entities/Unit/StatSystem.cpp:1278-1291", "Entities/Pet/Pet.cpp:894-896"],
                         INDEPENDENT, "pet_levelstats.mana or creature_classlevelstats.basemana; PowerType MaxBasePower for non-mana",
                         "(BASE_VALUE + create power) * BASE_PCT + TOTAL, * TOTAL_PCT; int32(); only for the display power (Creature::GetPowerIndex)",
                         {"source_type": "uint16/uint32", "intermediate_type": "float", "rounding": "int32()", "units": "power"}))
    # --- resistances ---------------------------------------------------------
    for uc in guardian_like:
        rows.append(_row(uc, "resistances", "Guardian::UpdateResistances", ["Entities/Unit/StatSystem.cpp:1210-1229", "Entities/Pet/Pet.cpp:883-885"],
                         EXPLICIT, "creature_template.resistance (non-hunter), owner GetResistance/GetBonusResistanceMod",
                         "Pet (UNIT_MASK_PET): base += CalculatePct(ownerRes, 40), bonus += CalculatePct(ownerBonusRes, 40); non-Pet Guardian: template only",
                         {"source_type": "int32", "intermediate_type": "float", "rounding": "CalculatePct int32 truncation, int32()", "units": "resistance"},
                         evidence="legacy-only", notes="no current-player-scope spell carries SPELL_AURA_MOD_RESISTANCE(22)/_PCT(101)/MOD_BASE_RESISTANCE(83)/_PCT(142) with a non-physical school bit (Scope include_class_skills=False); reopen with an item or in-scope aura granting fire..arcane resistance"))
    return rows


# ---------------------------------------------------------------------------
# recalculation trigger inventory (what re-runs Guardian::Update*)
# ---------------------------------------------------------------------------

TRIGGERS = [
    {"trigger": "Guardian::InitStatsForLevel -> UpdateAllStats", "coordinates": [TC + "Entities/Pet/Pet.cpp:1085"], "reaches": "any Guardian at creation / level change", "classification": CREATION_SNAPSHOT},
    {"trigger": "Player::UpdateStats(stat) -> GetPet()->UpdateStats(stat) for STA/INT/STR", "coordinates": [TC + "Entities/Unit/StatSystem.cpp:114-119"], "reaches": "Pet class only (GetPet())", "classification": EXPLICIT},
    {"trigger": "Player::UpdateArmor -> GetPet()->UpdateArmor", "coordinates": [TC + "Entities/Unit/StatSystem.cpp:276-278"], "reaches": "Pet class only", "classification": EXPLICIT},
    {"trigger": "Player::UpdateAttackPowerAndDamage(ranged) -> pet->UpdateAttackPowerAndDamage if IsHunterPet", "coordinates": [TC + "Entities/Unit/StatSystem.cpp:403-407"], "reaches": "HUNTER_PET", "classification": EXPLICIT},
    {"trigger": "Player::UpdateAttackPowerAndDamage(melee) -> pet->UpdateAttackPowerAndDamage if IsPetGhoul; guardian->... if IsSpiritWolf", "coordinates": [TC + "Entities/Unit/StatSystem.cpp:419-423"], "reaches": "Pet 26125; GetGuardianPet() 29264", "classification": EXPLICIT},
    {"trigger": "AuraEffect::HandleModDamageDone on the owner -> GetGuardianPet()->UpdateAttackPowerAndDamage", "coordinates": [TC + "Spells/Auras/SpellAuraEffects.cpp:4708-4733"], "reaches": "the current guardian pet (Pet or Guardian in the pet slot)", "classification": EXPLICIT},
    {"trigger": "Player::ApplySpellPowerBonus (item spell power)", "coordinates": [TC + "Entities/Unit/StatSystem.cpp:151-168"], "reaches": "no pet call: item SP changes are not propagated until another trigger fires", "classification": "no-trigger"},
    {"trigger": "Player::UpdateSpellDamageAndHealingBonus", "coordinates": [TC + "Entities/Unit/StatSystem.cpp:170-196"], "reaches": "no pet call", "classification": "no-trigger"},
    {"trigger": "Player::UpdateMaxHealth / UpdateRating / UpdateAllCritPercentages / UpdateMastery / UpdateVersatilityDamageDone", "coordinates": [TC + "Entities/Unit/StatSystem.cpp:314-324", TC + "Entities/Player/Player.cpp:5237"], "reaches": "no pet call", "classification": "no-trigger"},
    {"trigger": "Unit::UpdateUnitMod gate: CanModifyStats()", "coordinates": [TC + "Entities/Unit/Unit.cpp:9682-9685", TC + "Entities/Unit/Unit.cpp:340", TC + "Entities/Creature/Creature.cpp:657-670", TC + "Entities/Pet/Pet.cpp:326"],
     "reaches": "m_canModifyStats defaults false; Creature::UpdateEntry sets it true only for !IsGuardian(); Pet::LoadPetFromDB sets it true (after InitStatsForLevel); Player::SummonPet / CreateTamedPetFrom / Guardian::InitStats never set it -> aura stat modifiers on a Map::SummonCreature guardian or a freshly summoned Pet update m_auraFlatModifiersGroup without re-running Guardian::Update* until another trigger fires",
     "classification": "gate"},
]


# ---------------------------------------------------------------------------
# corpus + CLI
# ---------------------------------------------------------------------------


def provenance(generator: str) -> dict[str, Any]:
    import subprocess
    root = Path(__file__).resolve().parents[3]
    try:
        head = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    except Exception:  # pragma: no cover
        head = "unknown"
    return {"snapshot_build": SNAPSHOT_BUILD, "trinitycore_commit": TRINITY_COMMIT,
            "world_database": "TDB_full_world_1200.26021_2026_02_06.sql (via docs/research/world-db-corpora/player-base-stats.json)",
            "generator": generator, "wowlab_data_commit": head}


def build_stats_corpus() -> dict[str, Any]:
    return {"provenance": provenance("python3 controlled_units.py stats"),
            "unit_classes": UNIT_CLASSES,
            "classification_vocabulary": {CREATION_SNAPSHOT: "fixed when the unit is created (InitStats/InitStatsForLevel) and never revisited",
                                          APPLICATION_SNAPSHOT: "captured when an aura/spell is applied to the unit",
                                          DYNAMIC: "read from the owner at use time (cast/damage)",
                                          EXPLICIT: "recomputed by an explicit Update* call; see triggers for what fires it",
                                          INDEPENDENT: "the unit's own creature/world-db value, no owner dependency",
                                          LEGACY: "code exists but no 12.1 input reaches it",
                                          UNRESOLVED: "no consumer in the pinned checkout"},
            "consumers": consumer_table(),
            "recalculation_triggers": TRIGGERS,
            "constants": {"HEALTH_PER_STAMINA": {str(k): v for k, v in HEALTH_PER_STAMINA.items()}, "HEALTH_PER_STAMINA_DEFAULT": HEALTH_PER_STAMINA_DEFAULT,
                          "ENTRY_SWITCH_InitStatsForLevel": {str(k): v for k, v in INIT_ENTRY_SWITCH.items()},
                          "hardcoded_entries_StatSystem": {"ENTRY_IMP": 416, "ENTRY_VOIDWALKER": 1860, "ENTRY_SUCCUBUS": 1863, "ENTRY_FELHUNTER": 417, "ENTRY_FELGUARD": 17252, "ENTRY_WATER_ELEMENTAL": 510, "ENTRY_TREANT": 1964, "ENTRY_FIRE_ELEMENTAL": 15438, "ENTRY_GHOUL": 26125, "ENTRY_BLOODWORM": 28017},
                          "PET_GHOUL": 26125, "PET_SPIRIT_WOLF": 29264, "BASE_ATTACK_TIME_ms": BASE_ATTACK_TIME, "PET_XP_FACTOR": 0.05, "MAX_PLAYER_LEVEL": MAX_PLAYER_LEVEL}}


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=1, sort_keys=False) + "\n", encoding="utf-8")


def cmd_stats(args: argparse.Namespace) -> int:
    out = Path(args.out) if args.out else CORPORA / "stats.json"
    doc = build_stats_corpus()
    write_json(out, doc)
    print(f"wrote {out} ({len(doc['consumers'])} consumer rows, {len(doc['recalculation_triggers'])} triggers)")
    return 0


_DB2: Db2Scaling | None = None
_WORLD: WorldRows | None = None


def sources() -> tuple[Db2Scaling, WorldRows]:
    global _DB2, _WORLD
    if _DB2 is None:
        _DB2 = Db2Scaling()
    if _WORLD is None:
        _WORLD = WorldRows()
    return _DB2, _WORLD


def run_oracle(entry: int, unit_class: str, owner: OwnerFacts, petlevel: int | None = None,
               use_creature_level: bool = False,
               pet_store: dict[int, dict[int, PetLevelInfo]] | None = None,
               engine: EngineInputs | None = None, cinfo: CreatureFacts | None = None,
               pinfo_override: PetLevelInfo | None = None, db2: Db2Scaling | None = None,
               world: WorldRows | None = None) -> dict[str, Any]:
    """Run ``Guardian::InitStatsForLevel`` for one unit and return prepared stats + trace.

    ``unit_class``: ``Pet/SUMMON_PET``, ``Pet/HUNTER_PET`` or ``Guardian``.  For a
    Pet the level is the owner level (Player::SummonPet Player.cpp:30491,
    Pet::LoadPetFromDB Pet.cpp:289-290); for a Guardian it is
    ``TempSummon::InitStats``' clamp of the owner level into the ContentTuning
    range (TemporarySummon.cpp:241-247) unless ``petlevel`` is given.
    ``engine``/``cinfo`` default to :func:`engine_inputs_from_sources`.
    """
    if unit_class not in ("Pet/SUMMON_PET", "Pet/HUNTER_PET", "Guardian"):
        raise SourceError(f"unit class {unit_class!r} has no Guardian arithmetic (plain Creature stats); refusing to guess")
    is_pet = unit_class.startswith("Pet/")
    notes: dict[str, Any] = {}
    if engine is None or cinfo is None:
        d, w = (db2, world) if db2 is not None and world is not None else sources()
        if petlevel is None and not is_pet:
            _, _, pre = engine_inputs_from_sources(entry, False, owner.level, owner.level, d, w)
            if "tempsummon_level" not in pre:
                raise SourceError(f"Guardian level for entry {entry} not derivable: {pre.get('select_level')}")
            petlevel = pre["select_level"] if use_creature_level else pre["tempsummon_level"]
        lvl = owner.level if petlevel is None else petlevel
        try:
            engine, cinfo_d, notes = engine_inputs_from_sources(entry, is_pet, lvl, owner.level, d, w)
        except SourceError as exc:
            # no world row: every engine input stays None (branches that read one fail closed);
            # unit_class unknown -> display power unknown
            engine, cinfo_d, notes = EngineInputs(), CreatureFacts(entry=entry, unit_class=-1), {"world": str(exc)}
        cinfo = cinfo if cinfo is not None else cinfo_d
        hunter = is_pet and owner.class_id == CLASS_HUNTER
        if cinfo.unit_class >= 0:
            cinfo.calc_display_power, cinfo.display_power_type, notes["display_power"] = d.display_power(cinfo.unit_class, hunter)
            cinfo.max_base_power = d.max_base_power(cinfo.display_power_type)
        else:
            notes["display_power"] = "unresolved: creature_template.unit_class not available"
    if petlevel is None:
        petlevel = owner.level
    unit = UnitState(entry=entry, is_pet=is_pet, is_guardian=True, can_modify_stats=False,
                     is_hunter_pet=(unit_class == "Pet/HUNTER_PET"))  # Pet ctor sets UNIT_MASK_HUNTER_PET for HUNTER_PET (Pet.cpp:54-55)
    creature_id = 1 if (is_pet and owner.class_id == CLASS_HUNTER) else entry
    if pinfo_override is not None:
        pinfo, prov = pinfo_override, "override"
    else:
        store = pet_store if pet_store is not None else load_pet_levelstats()
        if petlevel < 1 and creature_id in store:
            raise SourceError(f"level {petlevel}: GetPetLevelInfo({creature_id}, 0) indexes [level-1] = [-1] "
                              "(ObjectMgr.cpp:3777), undefined behaviour")
        if petlevel < 1:
            pinfo, prov = None, f"level {petlevel}: no pet_levelstats rows for {creature_id} -> nullptr (ObjectMgr.cpp:3775-3776)"
        else:
            pinfo, prov = get_pet_level_info(store, creature_id, petlevel)
    oracle = GuardianOracle(unit, owner, cinfo, pinfo, engine)
    oracle.init_stats_for_level(petlevel)
    return {"entry": entry, "unit_class": unit_class, "level": unit.level, "pet_type": PET_TYPE_NAMES[unit.pet_type],
            "pet_levelstats": prov, "stats": dict(zip(STAT_NAMES, unit.stats)),
            "stat_from_owner": dict(zip(STAT_NAMES, unit.stat_from_owner)),
            "create_stats": dict(zip(STAT_NAMES, unit.create_stats)), "create_health": unit.create_health,
            "create_mana": unit.create_mana, "max_health": unit.max_health, "max_health_undefined_behaviour": unit.max_health_ub,
            "max_power": ({"power": unit.max_power[0], "value": unit.max_power[1]} if cinfo.unit_class >= 0
                          else {"status": "unresolved", "reason": notes.get("display_power")}),
            "armor": {"base": unit.armor[0], "bonus": unit.armor[1]},
            "attack_power": unit.attack_power, "attack_power_multiplier": unit.attack_power_multiplier,
            "bonus_spell_damage": unit.bonus_spell_damage, "weapon_damage": list(unit.weapon_damage),
            "min_damage": unit.min_damage, "max_damage": unit.max_damage, "base_attack_time_ms": unit.base_attack_time,
            "resistances": list(unit.resistances), "engine_inputs": {k: v for k, v in engine.__dict__.items() if k != "provenance"},
            "source_notes": notes, "trace": unit.trace}


def owner_from_args(args: argparse.Namespace, default_class: int) -> OwnerFacts:
    return OwnerFacts(class_id=args.owner_class if args.owner_class is not None else default_class,
                      level=args.owner_level, stats=(args.owner_str, args.owner_agi, args.owner_stamina, args.owner_int, 0),
                      armor=args.owner_armor, attack_power_melee=args.owner_ap, attack_power_ranged=args.owner_rap,
                      mod_damage_done_pos=tuple(args.owner_sp_pos) if args.owner_sp_pos else (args.owner_sp,) * MAX_SPELL_SCHOOL,
                      mod_damage_done_neg=tuple(args.owner_sp_neg),
                      spell_base_damage_bonus={k: v for k, v in (("fire", args.owner_sp_fire), ("frost", args.owner_sp_frost),
                                                                  ("nature", args.owner_sp_nature), ("shadow", args.owner_sp_shadow))
                                               if v is not None})


def cmd_inherit(args: argparse.Namespace) -> int:
    from controlled_units import inheritance
    try:
        res = inheritance.resolve_summon(args.spell, entry=args.entry, owner_class=args.owner_class)
    except SourceError as exc:
        print(json.dumps({"spell": args.spell, "status": "unresolved", "reason": str(exc)}, indent=1))
        return 2
    if res.get("status") != "ok":
        print(json.dumps(res, indent=1))
        return 2
    owner = owner_from_args(args, res["owner_class_default"])
    try:
        out = run_oracle(res["entry"], res["trinity_unit_class"], owner, petlevel=args.pet_level,
                         use_creature_level=bool(res.get("summon_effect", {}).get("use_creature_level")))
    except SourceError as exc:
        print(json.dumps({"spell": args.spell, "entry": res["entry"], "unit_class": res["trinity_unit_class"],
                          "status": "unresolved", "reason": str(exc), "resolution": res}, indent=1))
        return 2
    out = {"status": "ok", "spell": args.spell, **out, "resolution": res,
           "not_modelled": ["owner haste/crit/mastery (no Trinity consumer)", "auras applied after creation (CanModifyStats gate)",
                            "family passives / spell_pet_auras amounts (see spells.json)", "Rate.Creature.* != 1"]}
    if not args.trace:
        out.pop("trace")
    print(json.dumps(out, indent=1))
    return 0


def register(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser("stats", help="write controlled-unit-corpora/stats.json (consumer table + triggers)")
    p.add_argument("--out")
    p.set_defaults(func=cmd_stats)

    q = subparsers.add_parser("inherit", help="deterministic prepared-stat oracle for the unit summoned by --spell")
    q.add_argument("--spell", type=int, required=True)
    q.add_argument("--entry", type=int, help="pick one creature entry when the spell summons several")
    q.add_argument("--owner-class", type=int, dest="owner_class")
    q.add_argument("--owner-level", type=int, default=MAX_PLAYER_LEVEL, dest="owner_level")
    q.add_argument("--pet-level", type=int, dest="pet_level")
    q.add_argument("--owner-str", type=int, default=0, dest="owner_str")
    q.add_argument("--owner-agi", type=int, default=0, dest="owner_agi")
    q.add_argument("--owner-stamina", type=int, default=0, dest="owner_stamina")
    q.add_argument("--owner-int", type=int, default=0, dest="owner_int")
    q.add_argument("--owner-armor", type=int, default=0, dest="owner_armor")
    q.add_argument("--owner-ap", type=float, default=0.0, dest="owner_ap", help="GetTotalAttackPowerValue(BASE_ATTACK)")
    q.add_argument("--owner-rap", type=float, default=0.0, dest="owner_rap", help="GetTotalAttackPowerValue(RANGED_ATTACK)")
    q.add_argument("--owner-sp", type=int, default=0, dest="owner_sp", help="ModDamageDonePos for every school (shorthand)")
    q.add_argument("--owner-sp-pos", type=int, nargs=7, dest="owner_sp_pos", help="ActivePlayerData::ModDamageDonePos[7]")
    q.add_argument("--owner-sp-neg", type=int, nargs=7, default=[0] * 7, dest="owner_sp_neg", help="ActivePlayerData::ModDamageDoneNeg[7]")
    for school in ("fire", "frost", "nature", "shadow"):
        q.add_argument(f"--owner-sp-{school}", type=int, dest=f"owner_sp_{school}", help=f"SpellBaseDamageBonusDone({school}) for entry branches")
    q.add_argument("--trace", action="store_true")
    q.set_defaults(func=cmd_inherit)
