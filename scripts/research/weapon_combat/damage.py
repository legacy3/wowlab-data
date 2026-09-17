"""B4 -- white-swing damage arithmetic (Trinity ``7f3d43b``), every type boundary pinned.

The path is ``Unit::AttackerStateUpdate`` -> ``Unit::CalculateMeleeDamage``
(Unit.cpp:1327-1527) -> ``Unit::DealMeleeDamage`` (Unit.cpp:1529) ->
``Unit::DealDamage`` (Unit.cpp:1571).  This module reproduces the arithmetic
of ``CalculateMeleeDamage`` from a *supplied* weapon roll and a *supplied*
outcome (agent D owns the outcome probability); every stage records its
input type, arithmetic type, rounding and integer conversion.  Nothing here
draws randomness.

Stage order inside ``CalculateMeleeDamage`` (the order is load-bearing):

  1. ``CalculateDamage(attType, false, true)``        uint32 roll           Unit.cpp:1381
  2. ``MeleeDamageBonusDone``                          int32 -> float -> int32  Unit.cpp:1383, 8020-8127
  3. ``MeleeDamageBonusTaken``                         int32 -> float -> int32  Unit.cpp:1384, 8132-8240
  4. ``CalcArmorReducedDamage`` (physical only)        uint32 -> float -> uint32 Unit.cpp:1390-1396, 1684-1774
  5. ``RollMeleeOutcomeAgainst``                       (agent D)             Unit.cpp:1398
  6. outcome mutation (crit x2 + AddPct, block, glancing, crushing, parry/dodge/miss) Unit.cpp:1402-1494
  7. resilience (player-owned attackers only; player victims only)         Unit.cpp:1499-1505
  8. ``CalcAbsorbResist``                              victim auras          Unit.cpp:1510-1524
  9. ``DealMeleeDamage`` -> ``DealDamage(uint32)``                            Unit.cpp:1570-1571
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass
from typing import Any

from procs.chance import add, div, f32, lit, mul

from . import CORPORA, SourceError
from .weapon import (ATTACK_BY_NAME, ATTACK_NAMES, BASE_ATTACK, MAX_LEVEL_MIDNIGHT, OFF_ATTACK,
                     RANGED_ATTACK, ap_multiplier, provenance, sha256_file, sub)

OUTCOMES = {  # Unit.h:385-389 enum MeleeHitOutcome : uint8
    "evade": 0, "miss": 1, "dodge": 2, "block": 3, "parry": 4, "glancing": 5, "crit": 6, "crushing": 7, "normal": 8,
}
OUTCOME_NAMES = {v: k for k, v in OUTCOMES.items()}

CREATURE_BLOCK_PERCENT = lit("30.0")      # Unit.h:987 `virtual float GetBlockPercent(uint8) const { return 30.0f; }`
BLOCK_CAP = lit("0.85")                   # Player.cpp:26830 `std::min(blockArmor / (blockArmor + armorConstant), 0.85f)`
ARMOR_MITIGATION_CAP = lit("0.85")        # Unit.cpp:1773
GLANCING_STEP = lit("0.1")                # Unit.cpp:1478 `1.f - leveldif * 0.1f`
FUZZY_EPSILON64 = 0.0000005               # g3dmath.h:132, used by G3D::fuzzyLe(double, double) (g3dmath.h:878)
ARMOR_CONSTANT_LEVEL_90 = 3430.0          # ExpectedStat ID 475 (Lvl 90, ExpansionID -2).ArmorConstant

U32_MAX = 0xFFFFFFFF


def u32(value: int) -> int:
    return int(value) & U32_MAX


def i32(value: int) -> int:
    value = int(value) & U32_MAX
    return value - (1 << 32) if value & 0x80000000 else value


def trunc_u32(x: float) -> int:
    """``uint32(float)``: truncation toward zero; negative is UB in C++ and
    never reached because every site clamps at 0 first."""
    if x < 0:
        raise SourceError("uint32 conversion of a negative float (unreachable: every site clamps first)")
    return u32(int(math.trunc(x)))


def trunc_i32(x: float) -> int:
    return i32(int(math.trunc(x)))


def trunc_u32_x86(x: float) -> int:
    """``uint32(float)`` for a *negative* in-range value: undefined behaviour in C++.
    g++ 13 on x86-64 converts through a 64-bit ``cvttss2si`` and keeps the low 32 bits,
    i.e. two's-complement wrap (observed with tools/tc_weapon_probe at -O0)."""
    return int(math.trunc(x)) & U32_MAX


def calculate_pct_u32(base: int, pct: float) -> int:
    """``CalculatePct<uint32, float>`` (Util.h:71-75): ``T(base * float(pct) / 100.0f)``.
    ``base`` (uint32) is converted to float **before** the multiply (rounds above 2**24).
    A negative ``pct`` (e.g. a SPELL_AURA_MOD_CRIT_DAMAGE_BONUS product below 1) hits the
    UB conversion; the x86-64 wrap is reproduced and ``AddPct`` then subtracts."""
    x = div(mul(f32(base), f32(pct)), lit("100.0"))
    return trunc_u32_x86(x) if x < 0 else trunc_u32(x)


def add_pct_u32(base: int, pct: float) -> int:
    """``AddPct<uint32, float>`` (Util.h:84-88): ``base += CalculatePct(base, pct)``."""
    return u32(base + calculate_pct_u32(base, pct))


def std_round(x: float) -> float:
    """``std::round(double)``: half away from zero, exact (``floor(|x| + 0.5)`` is wrong
    for 0.49999999999999994 because the addition itself rounds)."""
    if math.isnan(x) or math.isinf(x):
        return x
    ax = abs(x)
    r = float(math.floor(ax))
    if ax - r >= 0.5:          # exact: ax and floor(ax) share an exponent range
        r += 1.0
    return math.copysign(r, x)


def add_pct_f(base: float, pct: float) -> float:
    return add(base, div(mul(base, f32(pct)), lit("100.0")))


# ---------------------------------------------------------------------------
# stage 2/3: bonus done / taken
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DoneMods:
    """Aura totals feeding ``Unit::MeleeDamageBonusDone`` (Unit.cpp:8020-8127) for a
    *white swing* (``spellProto == nullptr``)."""
    done_flat_creature: int = 0            # SPELL_AURA_MOD_DAMAGE_DONE_CREATURE by creature type mask       :8030
    ap_bonus: int = 0                      # MELEE/RANGED_ATTACK_POWER_ATTACKER_BONUS + MOD_*_ATTACK_POWER_VERSUS :8036-8050
    school_pct_done: float = 1.0           # non-physical school only: ModDamageDonePercent[max over schools] :8065-8079
    autoattack_pct: tuple[float, ...] = () # SPELL_AURA_MOD_AUTOATTACK_DAMAGE amounts, AddPct each            :8085-8089
    versus_creature_mult: float = 1.0      # SPELL_AURA_MOD_DAMAGE_DONE_VERSUS (multiplier)                   :8091
    versus_aurastate_mult: float = 1.0     # SPELL_AURA_MOD_DAMAGE_DONE_VERSUS_AURASTATE                      :8094
    target_mechanic_mult: float = 1.0      # SPELL_AURA_MOD_DAMAGE_PERCENT_DONE_BY_TARGET_AURA_MECHANIC       :8102


def melee_damage_bonus_done(damage: int, mods: DoneMods, *, att_type: int, ap_multiplier_value: float,
                            school_mask: int) -> dict[str, Any]:
    """Mirrors ``Unit::MeleeDamageBonusDone`` with ``spellProto == nullptr``."""
    damage = i32(damage)                                                     # parameter is int32 (uint32 roll converted)
    if damage == 0:
        return {"result": 0, "branch": "damage == 0 -> 0 (Unit.cpp:8022-8023)"}
    flat = int(mods.done_flat_creature)                                      # int32 DoneFlatBenefit
    ap_term = 0
    if mods.ap_bonus != 0:                                                   # :8052-8056 normalized=false for white swings
        ap_term = trunc_i32(mul(div(f32(mods.ap_bonus), lit("3.5")), f32(ap_multiplier_value)))
        flat += ap_term
    total = lit("1.0")                                                       # float DoneTotalMod
    if not (school_mask & 1):                                                # :8062 physical mods live in TOTAL_PCT already
        total = mul(total, f32(mods.school_pct_done))
    for amount in mods.autoattack_pct:                                       # :8085-8089
        total = add_pct_f(total, amount)
    total = mul(total, f32(mods.versus_creature_mult))
    total = mul(total, f32(mods.versus_aurastate_mult))
    total = mul(total, f32(mods.target_mechanic_mult))
    damage_f = mul(f32(damage + flat), total)                                # :8118 float(int32 + int32) * float
    result = trunc_i32(max(damage_f, 0.0))                                   # :8126 int32(std::max(damageF, 0.0f))
    return {"result": result, "flat_benefit": flat, "ap_flat_term": ap_term, "total_mod": total, "damage_f": damage_f}


@dataclass(frozen=True)
class TakenMods:
    """Victim-side totals for ``Unit::MeleeDamageBonusTaken`` (Unit.cpp:8132-8240), white swing."""
    taken_flat_school: int = 0             # SPELL_AURA_MOD_DAMAGE_TAKEN by attacker melee school mask  :8140
    taken_flat_melee: int = 0              # SPELL_AURA_MOD_MELEE_DAMAGE_TAKEN / _RANGED_                :8142-8145
    pct_taken_school: float = 1.0          # SPELL_AURA_MOD_DAMAGE_PERCENT_TAKEN                          :8154
    from_caster_mult: float = 1.0          # SPELL_AURA_MOD_MELEE_DAMAGE_FROM_CASTER (this attacker)      :8196
    cheat_death_pct: float | None = None   # aura 45182 effect 0 amount                                    :8203-8204
    melee_taken_pct_mult: float = 1.0      # SPELL_AURA_MOD_MELEE_DAMAGE_TAKEN_PCT / _RANGED_             :8206-8209
    victim_has_spell_mod_owner: bool = False  # GetSpellModOwner() != nullptr (player, or pet via its owner) gates versatility :8212
    versatility_taken_pct: float = 0.0     # CR_VERSATILITY_DAMAGE_TAKEN rating pct
    versatility_aura: int = 0              # SPELL_AURA_MOD_VERSATILITY (halved)
    ignore_resist_pcts: tuple[float, ...] = ()  # attacker SPELL_AURA_MOD_IGNORE_TARGET_RESIST matching school :8223-8233


def melee_damage_bonus_taken(damage: int, mods: TakenMods) -> dict[str, Any]:
    damage = i32(damage)
    if damage == 0:
        return {"result": 0, "branch": "pdamage == 0 -> 0 (Unit.cpp:8134-8135)"}
    flat = int(mods.taken_flat_school) + int(mods.taken_flat_melee)
    if flat < 0 and damage < -flat:                                          # :8147-8148
        return {"result": 0, "branch": "flat penalty exceeds damage -> 0 (Unit.cpp:8147-8148)", "flat_benefit": flat}
    total = lit("1.0")
    total = mul(total, f32(mods.pct_taken_school))
    total = mul(total, f32(mods.from_caster_mult))
    if mods.cheat_death_pct is not None:
        total = add_pct_f(total, mods.cheat_death_pct)
    total = mul(total, f32(mods.melee_taken_pct_mult))
    versa_applied = None
    if mods.victim_has_spell_mod_owner:                                      # :8212-8217
        versa_bonus = div(f32(mods.versatility_aura), lit("2.0"))
        versa_applied = add(f32(mods.versatility_taken_pct), versa_bonus)
        total = add_pct_f(total, -versa_applied)
    if total < lit("1.0"):                                                   # :8220-8235 "Sanctified Wrath"
        reduction = sub(lit("1.0"), total)
        for pct in mods.ignore_resist_pcts:
            reduction = add_pct_f(reduction, -pct)
        total = sub(lit("1.0"), reduction)
    tmp = mul(f32(damage + flat), total)                                     # :8238
    return {"result": trunc_i32(max(tmp, 0.0)), "flat_benefit": flat, "total_mod": total,
            "versatility_applied_pct": versa_applied, "damage_f": tmp}


# ---------------------------------------------------------------------------
# stage 4: armor
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ArmorInputs:
    victim_armor: int                      # uint32 GetArmor()
    armor_multiplier_for_target: float = 1.0   # Creature::GetArmorMultiplierForTarget (scalable levels) Creature.cpp:3120
    bypass_armor_pct: float = 0.0          # SPELL_AURA_BYPASS_ARMOR_FOR_CASTER (victim auras from this attacker), SpellEffectValue=double
    mod_target_resistance: int = 0         # attacker SPELL_AURA_MOD_TARGET_RESISTANCE physical (int32, negative ignores armor)
    ignore_target_resist_pcts: tuple[float, ...] = ()  # SPELL_AURA_MOD_IGNORE_TARGET_RESIST physical: armor = floor(AddPct(armor, -amt))
    attacker_is_player: bool = True        # TYPEID_PLAYER gate of the ArP block (Unit.cpp:1715)
    attacker_owner_is_player: bool | None = None   # GetCharmerOrOwnerPlayerOrPlayerItself() != nullptr gate of the item-level curve (Unit.cpp:1747); None = attacker_is_player
    armor_penetration_rating_pct: float = 0.0   # CR_ARMOR_PENETRATION (legacy-only: no current rating source)
    armor_penetration_aura_pct: float = 0.0     # SPELL_AURA_MOD_ARMOR_PENETRATION_PCT fitting the weapon
    victim_level_for_target: int = 90
    attacker_level_for_target: int = 90
    armor_constant: float = ARMOR_CONSTANT_LEVEL_90   # ExpectedStat.ArmorConstant(Lvl=attackerLevel, -2)
    attacker_avg_item_level: float | None = None     # PlayerData::AvgItemLevel[EquippedBase]
    item_level_by_level: float | None = None         # GameTable ItemLevelByLevel[90] -- ABSENT from snapshot
    diminishing_curve_value: float | None = None     # GetCurveValueAt(27400, itemLevelDelta) -- supplied when the delta is


def calc_armor_reduced_damage(damage: int, inp: ArmorInputs) -> dict[str, Any]:
    """Mirrors ``Unit::CalcArmorReducedDamage`` (Unit.cpp:1684-1774) for a white swing
    (``spellInfo == nullptr``)."""
    damage = u32(damage)
    armor = f32(inp.victim_armor)                                                    # :1686 float(uint32)
    stages: list[dict[str, Any]] = []
    armor = mul(armor, f32(inp.armor_multiplier_for_target))                         # :1690
    bypass = min(float(inp.bypass_armor_pct), 100.0)                                 # :1693-1698 SpellEffectValue (double) sum
    armor = div(mul(armor, f32(100.0 - bypass)), lit("100.0"))                       # CalculatePct<float,double>: static_cast<float>(pct)
    stages.append({"stage": "bypass armor for caster", "armor": armor, "coordinates": "Unit.cpp:1693-1698"})
    armor = add(armor, f32(int(inp.mod_target_resistance)))                          # :1701 float + int32
    for pct in inp.ignore_target_resist_pcts:                                        # :1707-1712
        armor = f32(math.floor(add_pct_f(armor, -pct)))
    stages.append({"stage": "target resistance / ignore resist", "armor": armor, "coordinates": "Unit.cpp:1701-1712"})
    if inp.attacker_is_player:                                                       # :1715-1738
        arp = add(f32(inp.armor_penetration_rating_pct), f32(inp.armor_penetration_aura_pct))
        arp = min(max(arp, 0.0), 100.0)                                              # RoundToInterval(arpPct, 0.f, 100.f)
        vl = int(inp.victim_level_for_target)
        if vl < 60:
            max_pen = f32(400 + 85 * vl)                                             # :1729 float(int)
        else:
            # 400 + 85 * vl + 4.5f * 85 * (vl - 59): int + float(4.5f*85 = 382.5f) * int(vl-59)
            max_pen = add(f32(400 + 85 * vl), mul(mul(lit("4.5"), f32(85)), f32(vl - 59)))   # :1731
        max_pen = min(div(add(armor, max_pen), lit("3.0")), armor)                   # :1734
        armor = sub(armor, div(mul(max_pen, arp), lit("100.0")))                     # :1736 CalculatePct<float,float>
        stages.append({"stage": "armor penetration cap", "arp_pct": arp, "max_armor_pen": max_pen, "armor": armor,
                       "coordinates": "Unit.cpp:1715-1738"})
    # G3D::fuzzyLe(armor, 0.0f): double compare  armor < 0 + 5e-7 * (|armor| + 1)
    if float(armor) < 0.0 + FUZZY_EPSILON64 * (abs(float(armor)) + 1.0):            # :1741
        return {"result": damage, "branch": "armor fuzzy <= 0 -> damage unchanged (Unit.cpp:1741-1742)", "stages": stages}
    armor_constant = f32(inp.armor_constant)                                         # :1755 (class mod only for creatures: attackerClass = CLASS_NONE for players, :1749-1752)
    owner_is_player = inp.attacker_is_player if inp.attacker_owner_is_player is None else inp.attacker_owner_is_player
    if owner_is_player:                                                              # :1747-1748 player or player-owned pet
        if inp.attacker_level_for_target == MAX_LEVEL_MIDNIGHT:                      # :1758-1760
            if inp.item_level_by_level is None or inp.attacker_avg_item_level is None or inp.diminishing_curve_value is None:
                return {"result": None, "unresolved": "ItemLevelByLevel.txt GameTable is absent from the snapshot; "
                        "itemLevelDelta and the ArmorItemLevelDiminishing curve factor (GlobalCurve 18 -> Curve 27400) "
                        "cannot be evaluated (Unit.cpp:1762-1764). Supply --armor-curve-factor to proceed.",
                        "stages": stages, "armor": armor, "armor_constant_before_curve": armor_constant}
            armor_constant = mul(armor_constant, f32(inp.diminishing_curve_value))   # :1764
            stages.append({"stage": "item-level diminishing", "curve_value": inp.diminishing_curve_value,
                           "armor_constant": armor_constant, "coordinates": "Unit.cpp:1758-1765"})
    if add(armor, armor_constant) == 0.0:                                            # :1768
        return {"result": damage, "branch": "armor + constant == 0 -> damage unchanged", "stages": stages}
    mitigation = min(div(armor, add(armor, armor_constant)), ARMOR_MITIGATION_CAP)  # :1772
    reduced = trunc_u32(max(mul(f32(damage), sub(lit("1.0"), mitigation)), 0.0))     # :1773 damage(uint32)*float -> uint32
    return {"result": reduced, "armor": armor, "armor_constant": armor_constant, "mitigation": mitigation,
            "stages": stages, "clean_damage_delta": damage - reduced}


# ---------------------------------------------------------------------------
# stage 6: outcome mutation
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class OutcomeInputs:
    outcome: str
    attacker_level: int = 90
    victim_level: int = 90
    crit_damage_bonus_mult: float = 1.0    # GetTotalAuraMultiplierByMiscMask(SPELL_AURA_MOD_CRIT_DAMAGE_BONUS, school)
    victim_is_player: bool = False
    victim_shield_block: int = 0           # ActivePlayerData::ShieldBlock = int32(shield armor * 2.5f) (Player.cpp:8127)
    armor_constant: float = ARMOR_CONSTANT_LEVEL_90
    block_critical: bool = False           # Unit::IsBlockCritical (roll vs SPELL_AURA_MOD_BLOCK_CRIT_CHANCE), supplied
    attacker_critical_block_mult: float = 1.0  # GetTotalAuraMultiplier(SPELL_AURA_MOD_CRITICAL_BLOCK_AMOUNT) on the ATTACKER (`this`, Unit.cpp:1463)


def player_block_percent(shield_block: int, armor_constant: float) -> float:
    """Mirrors ``Player::GetBlockPercent`` (Player.cpp:26822-26831).  NOTE: returns a
    **fraction** (0..0.85) while ``Unit::GetBlockPercent`` returns **30.0f percent**;
    the caller feeds either into ``CalculatePct`` unchanged (Unit.cpp:1459)."""
    block_armor = f32(int(shield_block))
    constant = f32(armor_constant)
    if add(block_armor, constant) == 0.0:
        return 0.0
    return min(div(block_armor, add(block_armor, constant)), BLOCK_CAP)


def apply_outcome(damage: int, inp: OutcomeInputs) -> dict[str, Any]:
    """Mirrors the ``switch (damageInfo->HitOutCome)`` of ``Unit::CalculateMeleeDamage``
    (Unit.cpp:1402-1494) on ``uint32 Damage``.  Returns Damage/CleanDamage/Blocked/OriginalDamage."""
    damage = u32(damage)
    clean = 0
    blocked = 0
    original = damage
    o = inp.outcome
    if o not in OUTCOMES:
        raise SourceError(f"unknown outcome {o!r}; one of {sorted(OUTCOMES)}")
    if o in ("evade", "miss"):
        return {"damage": 0, "clean_damage": 0, "blocked": 0, "original_damage": damage,
                "coordinates": "Unit.cpp:1404-1419"}
    if o == "normal":
        return {"damage": damage, "clean_damage": 0, "blocked": 0, "original_damage": damage, "coordinates": "Unit.cpp:1420-1423"}
    if o == "crit":
        damage = u32(damage * 2)                                                     # :1430 uint32 *= 2
        mod = mul(sub(f32(inp.crit_damage_bonus_mult), lit("1.0")), f32(100))        # :1433 float
        if mod != 0.0:
            damage = add_pct_u32(damage, mod)                                        # :1436 AddPct<uint32,float>
        return {"damage": damage, "clean_damage": 0, "blocked": 0, "original_damage": damage, "crit_mod_pct": mod,
                "coordinates": "Unit.cpp:1424-1440"}
    if o in ("parry", "dodge"):
        return {"damage": 0, "clean_damage": damage, "blocked": 0, "original_damage": damage, "coordinates": "Unit.cpp:1441-1454"}
    if o == "block":
        pct = player_block_percent(inp.victim_shield_block, inp.armor_constant) if inp.victim_is_player else CREATURE_BLOCK_PERCENT
        blocked = calculate_pct_u32(damage, pct)                                     # :1459
        if inp.block_critical:                                                       # :1460-1464
            blocked = u32(blocked * 2)
            blocked = trunc_u32(mul(f32(blocked), f32(inp.attacker_critical_block_mult)))  # uint32 *= float (attacker aura!) -> truncated
        return {"damage": u32(damage - blocked), "clean_damage": blocked, "blocked": blocked, "original_damage": damage,
                "block_percent_argument": pct, "block_argument_unit": "fraction (player victim)" if inp.victim_is_player else "percent (creature victim)",
                "coordinates": "Unit.cpp:1455-1469"}
    if o == "glancing":
        leveldif = min(int(inp.victim_level) - int(inp.attacker_level), 3)           # :1474-1476 int32
        reduce = sub(lit("1.0"), mul(f32(leveldif), GLANCING_STEP))                  # :1478 float
        new = trunc_u32(mul(reduce, f32(damage)))                                    # :1480 uint32(float * uint32)
        return {"damage": new, "clean_damage": u32(damage - new), "blocked": 0, "original_damage": damage,   # uint32 wrap when leveldif < 0
                "reduce_percent": reduce, "coordinates": "Unit.cpp:1470-1483"}
    if o == "crushing":
        damage = u32(damage + damage // 2)                                           # :1487 uint32 integer division
        return {"damage": damage, "clean_damage": 0, "blocked": 0, "original_damage": damage, "coordinates": "Unit.cpp:1484-1490"}
    raise SourceError(o)


# ---------------------------------------------------------------------------
# special weapon attacks: the arithmetic after the effect loop of Spell::EffectWeaponDmg
# ---------------------------------------------------------------------------

WEAPON_FIXED_EFFECTS = (58, 17, 121)   # SPELL_EFFECT_WEAPON_DAMAGE, _NOSCHOOL, NORMALIZED_WEAPON_DMG
WEAPON_PCT_EFFECT = 31                 # SPELL_EFFECT_WEAPON_PERCENT_DAMAGE


def special_weapon_damage(*, weapon_roll: int, effects: list[tuple[int, float]], weapon_total_pct: float = 1.0,
                          add_pct_mods: bool = True) -> dict[str, Any]:
    """Mirrors the two effect loops and the tail of ``Spell::EffectWeaponDmg``
    (SpellEffects.cpp:2860-2944) after ``CalculateDamage`` returned ``weapon_roll``.

    ``effects`` is the ordered ``(Effect, CalcValue)`` list of the effects in the
    target's mask (non-weapon effects are ignored as in the source).  Pinned
    behaviour:

    * loop 1 sums every 58/17/121 value into ``fixed_bonus`` (``double``) and
      applies ``ApplyPct`` for every 31 value into ``float weaponDamagePercentMod``
      (``CalculatePct<float,double>`` casts the percent to float, Util.h:72);
    * ``fixed_bonus *= float weapon_total_pct`` when ``addPctMods`` (:2900-2901);
    * loop 2 walks the effects **again in index order**: ``+= fixed_bonus`` once per
      58/17/121 effect and ``*= weaponDamagePercentMod`` once per 31 effect
      (:2911-2935) -- the whole sum/product each time, so a spell with two fixed
      (or two pct) effects double-counts and the relative order of 31 vs 58
      matters;
    * ``std::round`` (half away from zero) then ``max(.., 0.0)`` (:2944); the
      ``double`` is then passed to ``MeleeDamageBonusDone(int32 damage)`` -- an
      implicit truncating conversion (:2947).
    """
    fixed = 0.0
    pct_mod = lit("1.0")
    for effect, value in effects:
        if effect in WEAPON_FIXED_EFFECTS:
            fixed += float(value)                                                     # :2869-2875 double +=
        elif effect == WEAPON_PCT_EFFECT:
            pct_mod = div(mul(pct_mod, f32(value)), lit("100.0"))                     # :2877 ApplyPct(float, double)
    if add_pct_mods:
        if fixed:
            fixed = fixed * float(f32(weapon_total_pct))                              # :2900-2901 double * float
    weapon_damage = float(u32(weapon_roll))                                           # :2908 uint32 -> double
    applied: list[str] = []
    for effect, _value in effects:
        if effect in WEAPON_FIXED_EFFECTS:
            weapon_damage += fixed                                                    # :2921-2924
            applied.append(f"+{fixed!r}")
        elif effect == WEAPON_PCT_EFFECT:
            weapon_damage = weapon_damage * float(pct_mod)                            # :2925-2927 double * float
            applied.append(f"*{float(pct_mod)!r}")
    weapon_damage = weapon_damage + 0.0                                               # :2940 += spell_bonus (0)
    weapon_damage = weapon_damage * float(lit("1.0"))                                 # :2941 *= totalDamagePercentMod (1.0f)
    rounded = max(std_round(weapon_damage), 0.0)                                     # :2944 std::round
    return {"weapon_damage_double": rounded, "int32_to_bonus_done": trunc_i32(rounded), "pct_mod_f32": pct_mod,
            "fixed_bonus_scaled": fixed, "applied_in_order": applied,
            "fixed_effect_count": sum(1 for e, _ in effects if e in WEAPON_FIXED_EFFECTS),
            "pct_effect_count": sum(1 for e, _ in effects if e == WEAPON_PCT_EFFECT)}


def spell_weapon_damage_taken(damage: int, *, crit: bool = False, blocked: bool = False,
                              crit_bonus_after_spellmod: int | None = None, crit_damage_bonus_mult: float = 1.0,
                              victim_is_player: bool = False, victim_shield_block: int = 0,
                              armor_constant: float = ARMOR_CONSTANT_LEVEL_90, block_critical: bool = False,
                              attacker_critical_block_mult: float = 1.0, armor: "ArmorInputs | None" = None,
                              school_mask: int = 1, ignore_damage_taken_modifiers: bool = False) -> dict[str, Any]:
    """Mirrors the MELEE/RANGED branch of ``Unit::CalculateSpellDamageTaken`` (Unit.cpp:1193-1257),
    which ``Spell`` calls with ``m_damage`` after ``EffectWeaponDmg`` (Spell.cpp:2924, blocked =
    ``MissCondition == SPELL_MISS_BLOCK``).  Differences from the white-swing switch:

    * crit: ``uint32 crit_bonus = damage`` then the ``CritDamageAndHealing`` spellmod (supplied via
      ``crit_bonus_after_spellmod``), ``damage += crit_bonus``, then ``AddPct<int32,float>`` of
      the MOD_CRIT_DAMAGE_BONUS product (:1222-1232);
    * block: ``uint32 value = victim->GetBlockPercent(GetLevel())`` **truncates first** -- a
      creature gives 30, a player's fraction (< 1) gives **0**; a critical block doubles the
      *percent* and multiplies it by the ATTACKER's MOD_CRITICAL_BLOCK_AMOUNT (uint32 *= float);
      ``blocked = CalculatePct<int32,uint32>(damage, value)``; ``damage <= blocked`` -> full block
      (:1236-1252).  A partial block still deals damage; only SPELL_ATTR3_COMPLETELY_BLOCKED
      stops the hit (Spell.cpp:2763).
    """
    if damage < 0:
        return {"result": None, "branch": "damage < 0 -> return (Unit.cpp:1195-1196)"}
    damage = i32(damage)
    out: dict[str, Any] = {}
    if ignore_damage_taken_modifiers:
        return {"result": damage, "branch": "SPELL_ATTR4_IGNORE_DAMAGE_TAKEN_MODIFIERS (Unit.cpp:1205)"}
    if armor is not None and school_mask & 1:
        a = calc_armor_reduced_damage(u32(damage), armor)                               # :1207-1208 (spell mods not modelled)
        if a.get("result") is None:
            return {"result": None, "unresolved": a["unresolved"]}
        damage = i32(a["result"])
        out["after_armor"] = damage
    if crit:
        crit_bonus = u32(damage) if crit_bonus_after_spellmod is None else u32(crit_bonus_after_spellmod)   # :1222-1225
        damage = i32(u32(damage + crit_bonus))                                          # :1226 int32 += uint32
        mod = mul(sub(f32(crit_damage_bonus_mult), lit("1.0")), f32(100))              # :1229
        if mod != 0.0:
            damage = i32(damage + trunc_i32(div(mul(f32(damage), f32(mod)), lit("100.0"))))   # :1232 AddPct<int32,float>
        out["after_crit"] = damage
    blocked_amount = 0
    full_block = False
    if blocked:
        pct = player_block_percent(victim_shield_block, armor_constant) if victim_is_player else CREATURE_BLOCK_PERCENT
        value = trunc_u32(pct)                                                          # :1239 uint32 value = float
        if block_critical:
            value = u32(value * 2)                                                      # :1242
            value = trunc_u32(mul(f32(value), f32(attacker_critical_block_mult)))     # :1243 attacker aura
        blocked_amount = trunc_u32(div(mul(f32(damage), f32(value)), lit("100.0")))   # :1246 CalculatePct<int32,uint32> -> int32 -> uint32 field
        if damage <= i32(blocked_amount):                                               # :1247-1251
            blocked_amount = u32(damage)
            full_block = True
        damage = i32(damage - blocked_amount)                                           # :1252
        out.update({"block_percent_float": pct, "block_percent_uint32": value})
    out.update({"result": damage, "blocked": blocked_amount, "full_block": full_block,
                "resilience": "CanApplyResilience -> ApplyResilience (Unit.cpp:1255-1256): legacy-only/PvP, not applied"})
    return out


# ---------------------------------------------------------------------------
# full pipeline
# ---------------------------------------------------------------------------

def white_swing(*, roll: int, att_type: int, ap_multiplier_value: float, school_mask: int, done: DoneMods,
                taken: TakenMods, armor: ArmorInputs | None, outcome: OutcomeInputs,
                attacker_is_player_owned: bool = True) -> dict[str, Any]:
    """``Unit::CalculateMeleeDamage`` from the roll onward, every intermediate printed."""
    trace: list[dict[str, Any]] = []
    damage = u32(roll)
    trace.append({"stage": 1, "name": "CalculateDamage roll", "value": damage, "type": "uint32", "coordinates": "Unit.cpp:1380-1381, 2550"})
    d = melee_damage_bonus_done(damage, done, att_type=att_type, ap_multiplier_value=ap_multiplier_value, school_mask=school_mask)
    damage = u32(d["result"])
    trace.append({"stage": 2, "name": "MeleeDamageBonusDone", "value": damage, "type": "int32 -> uint32 (implicit)", "detail": d, "coordinates": "Unit.cpp:1383, 8020-8127"})
    t = melee_damage_bonus_taken(damage, taken)
    damage = u32(t["result"])
    trace.append({"stage": 3, "name": "MeleeDamageBonusTaken", "value": damage, "type": "int32 -> uint32 (implicit)", "detail": t, "coordinates": "Unit.cpp:1384, 8132-8240"})
    clean = 0
    if school_mask & 1 and armor is not None:                                        # IsDamageReducedByArmor Unit.cpp:1675-1682
        a = calc_armor_reduced_damage(damage, armor)
        if a.get("result") is None:
            trace.append({"stage": 4, "name": "CalcArmorReducedDamage", "unresolved": a["unresolved"], "detail": a})
            return {"trace": trace, "unresolved": a["unresolved"]}
        clean += damage - a["result"]
        damage = a["result"]
        trace.append({"stage": 4, "name": "CalcArmorReducedDamage", "value": damage, "type": "uint32", "detail": a, "coordinates": "Unit.cpp:1390-1396, 1684-1774"})
    else:
        trace.append({"stage": 4, "name": "CalcArmorReducedDamage", "value": damage, "skipped": "non-physical school mask (Unit.cpp:1678) or no victim armor supplied"})
    trace.append({"stage": 5, "name": "RollMeleeOutcomeAgainst", "value": outcome.outcome, "owner": "agent D (probability)", "coordinates": "Unit.cpp:1398, 2389-2499"})
    o = apply_outcome(damage, outcome)
    damage = o["damage"]
    clean += o["clean_damage"]
    trace.append({"stage": 6, "name": "outcome mutation", "value": damage, "type": "uint32", "detail": o})
    resilience_note = ("Unit::CanApplyResilience = !IsVehicle() && GetOwnerGUID().IsPlayer(); a Player's GetOwnerGUID() is "
                       "UnitData::SummonedBy (Unit.h:1190) which is empty for a player, so a player attacker never applies "
                       "resilience (Unit.cpp:1499-1505, 12416-12419); ApplyResilience is also a no-op for a creature victim without a player owner (Unit.cpp:12421-12438)")
    trace.append({"stage": 7, "name": "resilience", "value": damage, "reachability": "legacy-only/PvP", "note": resilience_note})
    trace.append({"stage": 8, "name": "CalcAbsorbResist", "value": damage, "note": "victim absorb auras; DamageInfo copy; absorbed amount subtracted (Unit.cpp:1510-1524); not modelled (no input)"})
    trace.append({"stage": 9, "name": "DealMeleeDamage -> DealDamage", "value": damage, "type": "uint32", "coordinates": "Unit.cpp:1570-1571"})
    return {"trace": trace, "damage": damage, "clean_damage": clean, "blocked": o["blocked"], "original_damage": o["original_damage"]}


# ---------------------------------------------------------------------------
# corpus
# ---------------------------------------------------------------------------

def damage_arithmetic_rows() -> list[dict[str, Any]]:
    tc = "trinity-consumer"
    return [
        {"order": 1, "stage": "weapon roll", "function": "Unit::CalculateDamage", "coordinates": ["Unit.cpp:2501-2551"], "input_type": "float min/max (UnitData::MinDamage/MaxDamage, or CalculateMinMaxDamage for normalized/!addTotalPct)", "arithmetic_type": "float clamp/swap", "rounding": "truncation", "integer_conversion": "urand(uint32(minDamage), uint32(maxDamage)) -> uint32 uniform_int_distribution (Random.cpp:42-47)", "units": "damage", "evidence_class": "trinity-probe", "notes": "feral BASE_ATTACK adds the off-hand range (Unit.cpp:2509-2516 / 2528-2532)"},
        {"order": 2, "stage": "bonus done", "function": "Unit::MeleeDamageBonusDone(victim, int32 damage, attType, DIRECT_DAMAGE, nullptr, nullptr, MECHANIC_NONE, school)", "coordinates": ["Unit.cpp:1383", "Unit.cpp:8020-8127"], "input_type": "uint32 roll -> int32 parameter (implicit)", "arithmetic_type": "int32 flat (DoneFlatBenefit incl. int32(APbonus / 3.5f * GetAPMultiplier)); float DoneTotalMod product; float(damage + flat) * mod", "rounding": "truncation", "integer_conversion": "int32(std::max(damageF, 0.0f)) Unit.cpp:8126", "aggregation_precision": "GetTotalAuraMultiplier* = float product; GetTotalAuraModifier* = int32 sum; AddPct per SPELL_AURA_MOD_AUTOATTACK_DAMAGE", "units": "damage", "evidence_class": tc, "notes": "physical SPELL_AURA_MOD_DAMAGE_PERCENT_DONE is NOT here (already in TOTAL_PCT of the prepared min/max, Unit.cpp:8062-8064); versatility is NOT here for white swings (StatSystem.cpp:457)"},
        {"order": 3, "stage": "bonus taken", "function": "Unit::MeleeDamageBonusTaken", "coordinates": ["Unit.cpp:1384", "Unit.cpp:8132-8240"], "input_type": "int32", "arithmetic_type": "int32 flat; float TakenTotalMod product; AddPct versatility (player victims only) ; 'Sanctified Wrath' reduction rewrite", "rounding": "truncation", "integer_conversion": "int32(std::max(tmpDamage, 0.0f)) Unit.cpp:8239", "units": "damage", "evidence_class": tc},
        {"order": 4, "stage": "armor", "function": "Unit::CalcArmorReducedDamage(attacker, victim, uint32 damage, nullptr, attType)", "coordinates": ["Unit.cpp:1390-1396", "Unit.cpp:1684-1774"], "input_type": "uint32", "arithmetic_type": "float armor pipeline; mitigation = min(armor/(armor+constant), 0.85f); damage * (1.0f - mitigation)", "rounding": "truncation; std::floor per MOD_IGNORE_TARGET_RESIST", "integer_conversion": "uint32(std::max(damage * (1.0f - mitigation), 0.0f)) Unit.cpp:1773", "units": "damage", "evidence_class": "trinity-probe", "notes": "armorConstant = ExpectedStat.ArmorConstant(attackerLevel, -2) = 3430 at 90 (db2-fact); at max level the constant is scaled by Curve 27400 of (AvgItemLevel - ItemLevelByLevel[90]) -- GameTable absent from snapshot: unresolved; G3D::fuzzyLe compares in double with eps 5e-7*(|a|+1); CR_ARMOR_PENETRATION rating: legacy-only (no current rating source)"},
        {"order": 5, "stage": "outcome roll", "function": "Unit::RollMeleeOutcomeAgainst", "coordinates": ["Unit.cpp:1398", "Unit.cpp:2389-2499"], "owner": "agent D", "evidence_class": tc, "notes": "glancing requires attackerLevel + 3 < victimLevel and a creature victim (Unit.cpp:2458-2466); crushing requires !IsControlledByPlayer() (Unit.cpp:2485-2497): both legacy-only for a player attacker at 90 unless a creature answers GetLevelForTarget >= 94"},
        {"order": 6, "stage": "crit", "function": "case MELEE_HIT_CRIT", "coordinates": ["Unit.cpp:1424-1440"], "input_type": "uint32", "arithmetic_type": "Damage *= 2 (uint32); mod = (GetTotalAuraMultiplierByMiscMask(SPELL_AURA_MOD_CRIT_DAMAGE_BONUS, school) - 1.0f) * 100 (float); AddPct<uint32,float>", "rounding": "truncation (CalculatePct casts float(base)*pct/100.0f back to uint32)", "integer_conversion": "uint32(float) inside CalculatePct", "units": "damage", "evidence_class": "trinity-probe", "notes": "2.0 multiplier for everyone: no player/NPC distinction here (SPELL_AURA_MOD_CRIT_DAMAGE_BONUS is the only modifier)"},
        {"order": 6, "stage": "block", "function": "case MELEE_HIT_BLOCK", "coordinates": ["Unit.cpp:1455-1469", "Unit.h:987", "Player.cpp:26822-26831"], "input_type": "uint32", "arithmetic_type": "Blocked = CalculatePct(Damage, Target->GetBlockPercent(GetLevel())); if Target->IsBlockCritical(): Blocked *= 2; Blocked *= GetTotalAuraMultiplier(SPELL_AURA_MOD_CRITICAL_BLOCK_AMOUNT) -- called on the ATTACKER (this), not the blocking victim", "rounding": "truncation", "integer_conversion": "uint32(float) in CalculatePct and in the *= float", "units": "creature victim: percent 30.0f; player victim: FRACTION min(ShieldBlock/(ShieldBlock+ArmorConstant), 0.85f) fed to CalculatePct unchanged", "evidence_class": "trinity-probe", "notes": "consumer quirk: a player victim blocks <= 0.85% of the hit under this code (fraction passed where percent is expected); Trinity is the consumer oracle, not Retail truth"},
        {"order": 6, "stage": "glancing", "function": "case MELEE_HIT_GLANCING", "coordinates": ["Unit.cpp:1470-1483"], "input_type": "uint32", "arithmetic_type": "leveldif = min(victim - attacker, 3) int32; reducePercent = 1.f - leveldif * 0.1f; Damage = uint32(reducePercent * Damage)", "rounding": "truncation", "integer_conversion": "uint32(float)", "evidence_class": "legacy-only", "notes": "reachable only via RollMeleeOutcomeAgainst glancing branch (attacker+3 < victim, creature victim)"},
        {"order": 6, "stage": "crushing", "function": "case MELEE_HIT_CRUSHING", "coordinates": ["Unit.cpp:1484-1490"], "input_type": "uint32", "arithmetic_type": "Damage += Damage / 2 (uint32 division)", "rounding": "integer division", "evidence_class": "legacy-only", "notes": "creature attackers only (!IsControlledByPlayer())"},
        {"order": 6, "stage": "parry/dodge/miss/evade", "function": "cases", "coordinates": ["Unit.cpp:1404-1454"], "arithmetic_type": "Damage = 0; parry/dodge move Damage into CleanDamage (rage only)", "evidence_class": tc},
        {"order": 7, "stage": "resilience", "function": "Unit::CanApplyResilience / ApplyResilience", "coordinates": ["Unit.cpp:1499-1505", "Unit.cpp:12416-12438", "Unit.h:1190"], "evidence_class": "legacy-only", "notes": "player attacker: GetOwnerGUID() empty -> never applied; creature victim without player owner -> no-op"},
        {"order": 8, "stage": "absorb/resist", "function": "Unit::CalcAbsorbResist(DamageInfo)", "coordinates": ["Unit.cpp:1510-1524"], "input_type": "uint32 via DamageInfo copy", "evidence_class": tc, "notes": "victim auras; out of the weapon model (no input modelled)"},
        {"order": 9, "stage": "deal", "function": "Unit::DealMeleeDamage -> Unit::DealDamage(this, victim, uint32 Damage, &cleanDamage, DIRECT_DAMAGE, school, nullptr, durabilityLoss)", "coordinates": ["Unit.cpp:1529-1571"], "input_type": "uint32", "evidence_class": tc},
        {"order": "special-taken", "stage": "special weapon attack: damage taken", "function": "Unit::CalculateSpellDamageTaken (DmgClass MELEE/RANGED branch) via Spell.cpp:2924", "coordinates": ["Unit.cpp:1193-1257", "Spell.cpp:2924", "Spell.cpp:2763"], "input_type": "int32 m_damage", "arithmetic_type": "armor (with spellInfo: TargetResistance spellmod, ATTR0_CU_IGNORE_ARMOR); crit: uint32 crit_bonus = damage (+CritDamageAndHealing spellmod), damage += crit_bonus, AddPct<int32,float>(MOD_CRIT_DAMAGE_BONUS); block: uint32 value = GetBlockPercent(attacker level) [creature 30, player fraction -> 0], critical block: value *= 2, value *= ATTACKER MOD_CRITICAL_BLOCK_AMOUNT; blocked = CalculatePct(damage, value); damage <= blocked -> fullBlock", "rounding": "truncation at uint32 value (Unit.cpp:1239) before the percent is used", "integer_conversion": "Unit.cpp:1239, 1243, 1246", "units": "percent (uint32)", "evidence_class": "trinity-consumer", "notes": "a player victim never blocks any damage of a weapon special under this consumer (fraction truncates to 0); a partial block still deals damage unless SPELL_ATTR3_COMPLETELY_BLOCKED (Spell.cpp:2763); cross-checked with agent D attack-table.json numeric_rules (Unit.cpp:1239)"},
        {"order": "school", "stage": "school conversion", "function": "Player::GetMeleeDamageSchoolMask(attType) = 1 << ItemSparse.DamageType", "coordinates": ["Player.cpp:8183-8189", "Unit.cpp:1332"], "evidence_class": tc, "notes": "no SPELL_AURA_MOD_MELEE_DAMAGE_SCHOOL consumer in CalculateMeleeDamage at 7f3d43b; non-physical school skips armor (Unit.cpp:1678) and enables ModDamageDonePercent[school] in MeleeDamageBonusDone (Unit.cpp:8062-8079)"},
        {"order": "special", "stage": "special weapon attacks", "function": "Spell::EffectWeaponDmg", "coordinates": ["SpellEffects.cpp:2812-2949"], "input_type": "SpellEffectValue = double (SpellDefines.h:490)", "arithmetic_type": "fixed_bonus (double) += CalcValue; ApplyPct(float weaponDamagePercentMod, double); weaponDamage = double(CalculateDamage(attType, normalized, addPctMods)) ; (+fixed) (* pct) ; std::round; then int32 parameter of MeleeDamageBonusDone", "rounding": "std::round (half away from zero) on double then int32 truncation at the call", "integer_conversion": "MeleeDamageBonusDone(int32 damage) call site SpellEffects.cpp:2947 and MeleeDamageBonusTaken SpellEffects.cpp:2948; m_damage is int32 (Spell.h:818)", "evidence_class": "trinity-probe", "notes": "effect loop 2 runs in effect-index order and re-applies the SUMMED fixed_bonus once per 58/17/121 effect and the PRODUCT pct once per 31 effect (SpellEffects.cpp:2911-2935): multi-effect spells double-count; addPctMods=false (ATTR6_IGNORE_CASTER_DAMAGE_MODIFIERS or non-physical school, :2883) recomputes the roll with totalPct=1, which also drops the 0.5 off-hand factor; CalcValue already std::round-ed each effect value and clamped to Min/MaxValue (SpellInfo.cpp:622, 659)"},
    ]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def cmd_damage(args: argparse.Namespace) -> int:
    mods = json.loads(args.mods) if args.mods else {}
    att = ATTACK_BY_NAME[args.hand]
    if args.speed_ms is None and att != RANGED_ATTACK and not mods.get("ap_multiplier"):
        speed = 2600
    else:
        speed = args.speed_ms or 2600
    apm = float(mods.get("ap_multiplier", speed / 1000.0))
    done = DoneMods(**{k: v for k, v in mods.get("done", {}).items()})
    taken = TakenMods(**{k: v for k, v in mods.get("taken", {}).items()})
    armor_kwargs = dict(mods.get("armor", {}))
    armor_kwargs.setdefault("victim_armor", int(args.armor))
    armor_kwargs.setdefault("attacker_level_for_target", int(args.level))
    if args.armor_curve_factor is not None:
        armor_kwargs.setdefault("diminishing_curve_value", float(args.armor_curve_factor))
        armor_kwargs.setdefault("item_level_by_level", 0.0)
        armor_kwargs.setdefault("attacker_avg_item_level", 0.0)
    armor = ArmorInputs(**armor_kwargs)
    outcome = OutcomeInputs(outcome=args.outcome, attacker_level=int(args.level), **mods.get("outcome", {}))
    result = white_swing(roll=int(args.roll), att_type=att, ap_multiplier_value=apm, school_mask=int(mods.get("school_mask", 1)),
                         done=done, taken=taken, armor=armor, outcome=outcome)
    out = {"provenance": provenance("weapon_combat.py damage " + " ".join(sys.argv[2:])),
           "inputs": {"hand": ATTACK_NAMES[att], "roll": int(args.roll), "outcome": args.outcome, "ap": args.ap,
                      "ap_note": "AP enters the prepared min/max (weapon subcommand), not this post-roll pipeline; only MELEE_ATTACK_POWER_ATTACKER_BONUS auras enter here (mods.done.ap_bonus)",
                      "armor": args.armor, "level": args.level, "ap_multiplier": apm, "mods": mods},
           **result}
    print(json.dumps(out, indent=1, default=str))
    return 0 if "unresolved" not in result else 2


def cmd_arithmetic(args: argparse.Namespace) -> int:
    out = {"provenance": provenance("python3 weapon_combat.py arithmetic"),
           "stages": damage_arithmetic_rows(),
           "type_boundaries": [
               {"boundary": "float -> uint32", "sites": ["Unit.cpp:2550 urand bounds", "Unit.cpp:1773 armor", "Util.h:74 CalculatePct<uint32>", "Unit.cpp:1480 glancing", "Unit.cpp:1463 critical block *= float"], "rule": "truncation toward zero after a max(.., 0) clamp"},
               {"boundary": "uint32 -> int32", "sites": ["Unit.cpp:1383 MeleeDamageBonusDone(int32 damage)", "Unit.cpp:1384 MeleeDamageBonusTaken(int32)"], "rule": "implicit; values < 2**31 in practice"},
               {"boundary": "int32 -> float", "sites": ["Unit.cpp:8118 float(damage + DoneFlatBenefit)", "Unit.cpp:8238", "Unit.cpp:9923/9932 int32 AP sum -> float", "Unit.cpp:1686 float(GetArmor())"], "rule": "exact below 2**24, round-to-nearest above"},
               {"boundary": "float -> double", "sites": ["Unit.cpp:1741 G3D::fuzzyLe(armor, 0.0f)", "SpellEffects.cpp:2908 SpellEffectValue weaponDamage = uint32 CalculateDamage(...)"], "rule": "exact widening"},
               {"boundary": "double -> int32", "sites": ["SpellEffects.cpp:2947 MeleeDamageBonusDone(unitTarget, weaponDamage, ...)"], "rule": "std::round first (SpellEffects.cpp:2944), then truncating conversion"},
               {"boundary": "int16 -> float", "sites": ["StatSystem.cpp:461-462 CombatRoundTime"], "rule": "exact"},
           ],
           "discriminating_inputs": [
               {"case": "uint32 above 2**24 into CalculatePct", "input": {"base": 16777219, "pct": 30.0}, "why": "binary32 gives 5033166, a double oracle 5033165 (123456789 at 30: 37037040 vs 37037036); 16777217 at 30 does NOT discriminate (5033164.8 rounds to 5033165.0f)"},
               {"case": "versatility AddPct in float", "input": {"base": 1.0, "pct": 3.25}, "why": "1 + 0.0325 in binary32 is 1.0325000286..; min*max products differ from double in the last ulp and can flip a uint32 truncation"},
               {"case": "AP term", "input": {"ap": 12345, "speed": 2.6}, "why": "12345/3.5f*2.6f in float vs double differs by ~1e-3, enough to move uint32(minDamage) when the fraction is near .0"},
               {"case": "armor mitigation", "input": {"damage": 100000, "armor": 5000, "constant": 3430}, "why": "5000/8430 in float = 0.5931198 vs double 0.59311981..; damage*(1-m) truncation can differ by 1"},
               {"case": "crit multiplier 1.3 truncation", "input": {"damage": 1000, "crit_damage_bonus_mult": 1.3}, "why": "(1.3f - 1.0f) * 100 = 29.9999962f; CalculatePct(2000, 29.9999962f) = 599 -> 2599, not 2600 (probe; agent D pins the same at Unit.cpp:1433)"},
               {"case": "special effect order", "input": {"roll": 1234, "effects": [[31, 150], [58, 10]]}, "why": "(1234*1.5)+10 = 1861 vs (1234+10)*1.5 = 1866"},
               {"case": "special double count", "input": {"roll": 1234, "effects": [[58, 10], [58, 10]]}, "why": "summed fixed_bonus 20 re-added per effect: 1274, not 1254"},
               {"case": "off-hand special without pct mods", "input": {"attType": "OFF_ATTACK", "attr6_ignore_caster_damage_modifiers": True, "total_pct": 0.5}, "why": "the recompute passes addTotalPct=false: the 0.5 factor is lost (probe: 2710 for wmin 100, AP 3500, 2.6 s, +10)"},
               {"case": "std::round tie below 0.5", "input": {"x": 0.49999999999999994}, "why": "floor(x + 0.5) = 1 in double; std::round = 0"},
           ]}
    CORPORA.mkdir(parents=True, exist_ok=True)
    path = CORPORA / "damage-arithmetic.json"
    path.write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {path} sha256={sha256_file(path)} stages={len(out['stages'])}")
    return 0


def register(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser("damage", help="deterministic white-swing damage from a supplied roll and outcome (B4)")
    p.add_argument("--hand", choices=["mh", "oh", "ranged"], required=True)
    p.add_argument("--roll", type=int, required=True, help="the uint32 urand result of Unit::CalculateDamage")
    p.add_argument("--outcome", choices=sorted(OUTCOMES), required=True)
    p.add_argument("--ap", type=int, default=0, help="recorded only; AP enters the prepared min/max")
    p.add_argument("--armor", type=int, required=True, help="victim GetArmor()")
    p.add_argument("--level", type=int, default=90, help="attacker level")
    p.add_argument("--speed-ms", type=int, default=None, help="weapon delay for the APbonus term (default 2600)")
    p.add_argument("--armor-curve-factor", type=float, default=None, help="supplied ArmorItemLevelDiminishing factor (GameTable absent)")
    p.add_argument("--mods", default=None, help='JSON {"done":{...},"taken":{...},"armor":{...},"outcome":{...},"school_mask":1,"ap_multiplier":2.6}')
    p.set_defaults(func=cmd_damage)

    a = subparsers.add_parser("arithmetic", help="write damage-arithmetic.json (ordered stage list with types)")
    a.set_defaults(func=cmd_arithmetic)
