"""Attack outcome table: ``Unit::RollMeleeOutcomeAgainst`` and ``Unit::MeleeSpellHitResult``.

Research oracle only.  Every number here is either a constant read from the
pinned TrinityCore checkout (cited by ``file:line``) or an explicit input the
caller supplies (dodge/parry/block/crit percentages are *prepared* values that
``charstats``/``gearing`` own; this module never derives them).

Three things are kept apart, exactly as the brief asks:

* **eligibility** -- may the outcome occur at all (victim type, facing, shield,
  attack type, spell attributes);
* **probability construction** -- the ``float`` arithmetic, its clamps and its
  ``int32(x * 100.0f)`` conversion to basis points;
* **draw** -- which RNG call decides, in what order.

Trinity's chance functions return ``float`` (binary32).  Python floats are
binary64, so every intermediate is rounded through :func:`f32` before the
next operation, mirroring the C++ evaluation order one operation at a time
(``-ffp-contract=off`` build, as the probe uses).  The differential probe in
``tools/tc_swing_probe`` checks the emulation against compiled C++.

Coordinates are at TrinityCore ``7f3d43b``.
"""

from __future__ import annotations

import argparse
import json
import struct
from dataclasses import dataclass, field, asdict
from typing import Any

from . import CORPORA, SNAPSHOT_BUILD, TRINITY_COMMIT

# ---------------------------------------------------------------------------
# binary32 emulation
# ---------------------------------------------------------------------------

_F32 = struct.Struct("<f")


def f32(x: float) -> float:
    """Round a Python float to the nearest binary32 value (round-half-even)."""
    return _F32.unpack(_F32.pack(x))[0]


def f32_add(a: float, b: float) -> float:
    return f32(f32(a) + f32(b))


def f32_sub(a: float, b: float) -> float:
    return f32(f32(a) - f32(b))


def f32_mul(a: float, b: float) -> float:
    return f32(f32(a) * f32(b))


def f32_div(a: float, b: float) -> float:
    return f32(f32(a) / f32(b))


def f32_max0(a: float) -> float:
    """``std::max(chance, 0.0f)``."""
    return f32(a) if f32(a) > 0.0 else 0.0


def int32_trunc(x: float) -> int:
    """C++ ``int32(float)``: truncation toward zero.

    Out-of-range values are undefined behaviour in C++; the emulation raises
    instead of guessing (no Trinity chance ever exceeds a few thousand percent).
    """
    if not (-2147483648.0 <= x < 2147483648.0):
        raise OverflowError(f"int32() of {x!r} is undefined behaviour in C++")
    return int(x)  # Python int() truncates toward zero


def to_basis_points(chance_pct: float) -> int:
    """``int32(chance * 100.0f)`` -- Unit.cpp:2395-2402, 2635, 2643, 2726, 2737, 2748."""
    return int32_trunc(f32_mul(chance_pct, 100.0))


# ---------------------------------------------------------------------------
# constants read from the checkout (each cited)
# ---------------------------------------------------------------------------

CONSTANTS: dict[str, dict[str, Any]] = {
    "base_miss_pct": {"value": 5.0, "type": "float", "coord": "Unit.cpp:2836-2841 Unit::GetUnitMissChance"},
    "dual_wield_miss_penalty_pct": {"value": 19.0, "type": "float", "coord": "Unit.cpp:12463-12465 Unit::MeleeSpellMissChance"},
    "player_base_melee_hit_pct": {"value": 7.5, "type": "float", "coord": "StatSystem.cpp:753-756 Player::UpdateMeleeHitChances (7.5f + CR_HIT_MELEE bonus)"},
    "player_base_ranged_hit_pct": {"value": 7.5, "type": "float", "coord": "StatSystem.cpp:758-761 Player::UpdateRangedHitChances"},
    "creature_mod_melee_hit_pct": {"value": 0.0, "type": "float", "coord": "Unit.cpp:364-365 Unit::Unit (m_modMeleeHitChance = 0.0f, never updated for creatures)"},
    "player_base_expertise_pct": {"value": 7.5, "type": "float", "coord": "Player.cpp:5216-5229 Player::GetExpertiseDodgeOrParryReduction (7.5f + Expertise / 4.0f; 0 for RANGED)"},
    "creature_expertise_divisor": {"value": 4.0, "type": "float", "coord": "Unit.cpp:2792, 2832 (GetTotalAuraModifier(SPELL_AURA_MOD_EXPERTISE) / 4.0f)"},
    "npc_base_dodge_pct": {"value": 3.0, "type": "float", "coord": "Unit.cpp:2772-2776 Unit::GetUnitDodgeChance (non-totem creature victim)"},
    "npc_base_parry_pct": {"value": 6.0, "type": "float", "coord": "Unit.cpp:2816-2822 Unit::GetUnitParryChance (creature victim without CREATURE_FLAG_EXTRA_NO_PARRY)"},
    "npc_base_block_pct": {"value": 3.0, "type": "float", "coord": "Unit.cpp:2860-2866 Unit::GetUnitBlockChance (creature victim without CREATURE_FLAG_EXTRA_NO_BLOCK)"},
    "npc_level_bonus_per_level_pct": {"value": 1.5, "type": "float", "coord": "Unit.cpp:2776, 2822, 2866 (levelBonus = 1.5f * levelDiff, only when levelDiff > 0)"},
    "creature_base_crit_pct": {"value": 5.0, "type": "float", "coord": "Unit.cpp:2898-2902 Unit::GetUnitCriticalChanceDone (creature attacker without CREATURE_FLAG_EXTRA_NO_CRIT)"},
    "roll_range": {"value": [0, 9999], "type": "int32 (white) / uint32 (spell)", "coord": "Unit.cpp:2410 (int32 roll = urand(0, 9999)); Unit.cpp:2633 (uint32 roll = urand(0, 9999))"},
    "glancing_level_gap": {"value": 3, "type": "int32", "coord": "Unit.cpp:2456-2459 (attackerLevel + 3 < victimLevel)"},
    "glancing_chance_bp": {"value": "(10 + 10 * (victimLevel - attackerLevel)) * 100", "type": "int32", "coord": "Unit.cpp:2462 (no cap despite the 40% comment at :2456)"},
    "crushing_level_gap": {"value": 4, "type": "int32", "coord": "Unit.cpp:2483 (attackerLevel >= victimLevel + 4)"},
    "crushing_chance_bp": {"value": "attackerLevel - victimLevel * 1000 - 1500", "type": "int32",
                           "coord": "Unit.cpp:2489 -- operator precedence: victimLevel * 1000 binds first; the value is always negative for any level >= 2, so the roll test at :2491 can never pass"},
    "creature_block_percent": {"value": 30.0, "type": "float", "coord": "Unit.h:987 virtual Unit::GetBlockPercent (returns 30.0f; only Player overrides)"},
    "block_percent_cap": {"value": 0.85, "type": "float", "coord": "Player.cpp:26822-26831 Player::GetBlockPercent (std::min(blockArmor / (blockArmor + armorConstant), 0.85f))"},
    "crit_damage_multiplier_white": {"value": 2, "type": "uint32 (Damage *= 2)", "coord": "Unit.cpp:1424-1440 CalculateMeleeDamage MELEE_HIT_CRIT"},
    "crushing_damage": {"value": "Damage += Damage / 2", "type": "uint32", "coord": "Unit.cpp:1484-1490"},
    "glancing_damage": {"value": "uint32((1.f - min(leveldif,3) * 0.1f) * Damage)", "type": "float -> uint32", "coord": "Unit.cpp:1470-1483"},
}

#: ``MeleeHitOutcome`` (Unit.h:385-389) in enum order.
MELEE_HIT_OUTCOMES = ("evade", "miss", "dodge", "block", "parry", "glancing", "crit", "crushing", "normal")

#: ``SpellMissInfo`` values reachable from ``MeleeSpellHitResult``.
SPELL_MISS_RESULTS = ("none", "miss", "resist", "deflect", "dodge", "parry", "block")


# ---------------------------------------------------------------------------
# inputs
# ---------------------------------------------------------------------------

@dataclass
class Attacker:
    """Facts ``RollMeleeOutcomeAgainst``/``MeleeSpellHitResult`` read from ``this``."""

    is_player: bool = True
    is_pet: bool = False                     # Unit::IsPet (glancing eligibility)
    is_controlled_by_player: bool = True     # Unit::IsControlledByPlayer (crushing eligibility)
    level_for_target: int = 90               # GetLevelForTarget(victim)
    have_offhand_weapon: bool = False        # Unit::haveOffhandWeapon (Unit.cpp:525-531)
    in_feral_form: bool = False
    current_melee_spell: bool = False        # m_currentSpells[CURRENT_MELEE_SPELL] != nullptr
    ignore_dual_wield_penalty: bool = False  # SPELL_AURA_IGNORE_DUAL_WIELD_HIT_PENALTY (458)
    mod_melee_hit_chance: float | None = None   # m_modMeleeHitChance; None -> derived from is_player
    mod_ranged_hit_chance: float | None = None
    mod_hit_chance_aura: float = 0.0         # SPELL_AURA_MOD_HIT_CHANCE (54) on the attacker
    crit_done_pct: float = 5.0               # player: (Offhand/Ranged)CritPercentage field; creature: 5 + auras
    creature_no_crit: bool = False           # CREATURE_FLAG_EXTRA_NO_CRIT
    creature_weapon_crit_aura: float = 0.0   # SPELL_AURA_MOD_WEAPON_CRIT_PERCENT (creature attacker only)
    creature_crit_pct_aura: float = 0.0      # SPELL_AURA_MOD_CRIT_PCT (creature attacker only)
    autoattack_crit_aura: float = 0.0        # SPELL_AURA_MOD_AUTOATTACK_CRIT_CHANCE (334), white swings only
    expertise_mainhand: int = 0              # ActivePlayerData::MainhandExpertise (int32)
    expertise_offhand: int = 0
    creature_expertise_aura: float = 0.0     # SPELL_AURA_MOD_EXPERTISE on a creature attacker
    mod_combat_result_dodge_aura: float = 0.0  # SPELL_AURA_MOD_COMBAT_RESULT_CHANCE misc VICTIMSTATE_DODGE
    mod_enemy_dodge_aura: float = 0.0        # SPELL_AURA_MOD_ENEMY_DODGE
    creature_no_crushing: bool = False       # CREATURE_FLAG_EXTRA_NO_CRUSHING_BLOWS
    is_temp_summon: bool = False             # for SPELL_AURA_MOD_CRIT_CHANCE_FOR_CASTER_PET on the victim
    ignore_combat_result: tuple[str, ...] = ()  # SPELL_AURA_IGNORE_COMBAT_RESULT misc values affecting the spell
    has_spell_mod_owner: bool = False        # Unit::GetSpellModOwner() != nullptr for a creature (player pets/guardians)


@dataclass
class Victim:
    is_player: bool = False
    is_pet: bool = False
    is_totem: bool = False
    level_for_target: int = 93               # victim->GetLevelForTarget(attacker)
    evading: bool = False                    # Creature::IsEvadingAttacks
    stand_state: bool = True                 # Unit::IsStandState (player victims only)
    facing_attacker: bool = True             # victim->HasInArc(M_PI, attacker)
    ignore_hit_direction: bool = False       # SPELL_AURA_IGNORE_HIT_DIRECTION (288)
    casting: bool = False                    # victim->IsNonMeleeSpellCast(false, false, true)
    controlled: bool = False                 # victim->HasUnitState(UNIT_STATE_CONTROLLED)
    dodge_percentage: float = 0.0            # ActivePlayerData::DodgePercentage (player)
    parry_percentage: float = 0.0            # ActivePlayerData::ParryPercentage (player)
    block_percentage: float = 0.0            # ActivePlayerData::BlockPercentage (player)
    can_parry: bool = False                  # Player::CanParry (SPELL_EFFECT_PARRY learned)
    can_block: bool = False                  # Player::CanBlock (SPELL_EFFECT_BLOCK learned)
    has_useable_weapon: bool = True          # GetWeaponForAttack(BASE/OFF, true) (parry)
    has_useable_shield: bool = False         # offhand INVTYPE_SHIELD, not broken (block)
    creature_no_parry: bool = False          # CREATURE_FLAG_EXTRA_NO_PARRY
    creature_no_block: bool = False          # CREATURE_FLAG_EXTRA_NO_BLOCK
    mod_dodge_percent_aura: float = 0.0      # SPELL_AURA_MOD_DODGE_PERCENT (creature victim path)
    mod_parry_percent_aura: float = 0.0
    mod_block_percent_aura: float = 0.0
    attacker_melee_crit_aura: float = 0.0    # SPELL_AURA_MOD_ATTACKER_MELEE_CRIT_CHANCE (187), not for RANGED
    crit_vs_target_health_aura: float = 0.0  # SPELL_AURA_MOD_CRIT_CHANCE_VERSUS_TARGET_HEALTH (183) (already health-filtered)
    crit_for_caster_aura: float = 0.0        # SPELL_AURA_MOD_CRIT_CHANCE_FOR_CASTER (306) cast by this attacker
    crit_for_caster_pet_aura: float = 0.0    # SPELL_AURA_MOD_CRIT_CHANCE_FOR_CASTER_PET (339) cast by the summoner
    spell_and_weapon_crit_aura: float = 0.0  # SPELL_AURA_MOD_ATTACKER_SPELL_AND_WEAPON_CRIT_CHANCE (197)
    attacker_melee_hit_aura: float = 0.0     # SPELL_AURA_MOD_ATTACKER_MELEE_HIT_CHANCE (184)
    attacker_ranged_hit_aura: float = 0.0    # SPELL_AURA_MOD_ATTACKER_RANGED_HIT_CHANCE (185)
    mechanic_resist_pct: float = 0.0         # Unit::GetMechanicResistChance (spell path only)
    deflect_aura: float = 0.0                # SPELL_AURA_DEFLECT_SPELLS (287) (spell RANGED path only)
    block_crit_chance_aura: float = 0.0      # SPELL_AURA_MOD_BLOCK_CRIT_CHANCE (253)

    @property
    def casting_or_controlled(self) -> bool:
        """Unit.cpp:2422 / 2657 -- ``IsNonMeleeSpellCast(false, false, true) || HasUnitState(UNIT_STATE_CONTROLLED)``."""
        return self.casting or self.controlled


@dataclass
class SpellFacts:
    """Attributes ``MeleeSpellHitResult`` reads from the ``SpellInfo``."""

    spell_id: int = 0
    dmg_class_ranged: bool = False           # SPELL_DAMAGE_CLASS_RANGED -> attType RANGED_ATTACK
    no_avoidance: bool = False               # SPELL_ATTR3_NO_AVOIDANCE
    no_active_defense: bool = False          # SPELL_ATTR0_NO_ACTIVE_DEFENSE
    no_attack_dodge: bool = False            # SPELL_ATTR7_NO_ATTACK_DODGE
    no_attack_parry: bool = False            # SPELL_ATTR7_NO_ATTACK_PARRY
    no_attack_block: bool = False            # SPELL_ATTR8_NO_ATTACK_BLOCK
    no_attack_miss: bool = False             # SPELL_ATTR7_NO_ATTACK_MISS
    req_caster_behind_target: bool = False   # SPELL_ATTR0_CU_REQ_CASTER_BEHIND_TARGET
    hit_chance_spellmod_pct: float = 0.0     # SpellModOp::HitChance applied to resistMissChance (100 + x)
    always_hit: bool = False                 # SPELL_ATTR3_ALWAYS_HIT (WorldObject::SpellHitResult)
    can_crit: bool = True                    # SPELL_ATTR0_CU_CAN_CRIT
    positive: bool = False


# ---------------------------------------------------------------------------
# probability construction (binary32, operation by operation)
# ---------------------------------------------------------------------------

def _att_is_ranged(att: str) -> bool:
    return att == "ranged"


def miss_chance(a: Attacker, v: Victim, att: str, spell: SpellFacts | None = None) -> float:
    """``Unit::MeleeSpellMissChance`` -- Unit.cpp:12454-12489.  Returns binary32 percent."""
    if spell is not None and spell.no_attack_miss:
        return 0.0
    miss = f32(CONSTANTS["base_miss_pct"]["value"])                                     # :12460
    if (spell is None and a.have_offhand_weapon and not _att_is_ranged(att)
            and not a.current_melee_spell and not a.in_feral_form and not a.ignore_dual_wield_penalty):
        miss = f32_add(miss, 19.0)                                                       # :12463-12465
    resist_miss = f32(100.0)
    if spell is not None:
        resist_miss = f32_add(100.0, spell.hit_chance_spellmod_pct)                      # :12468-12471 (ApplySpellMod adds pct)
    miss = f32_sub(miss, f32_sub(resist_miss, 100.0))                                    # :12473
    if _att_is_ranged(att):
        mod = a.mod_ranged_hit_chance if a.mod_ranged_hit_chance is not None else (7.5 if a.is_player else 0.0)
    else:
        mod = a.mod_melee_hit_chance if a.mod_melee_hit_chance is not None else (7.5 if a.is_player else 0.0)
    miss = f32_sub(miss, mod)                                                            # :12475-12478
    miss = f32_sub(miss, a.mod_hit_chance_aura)                                          # :12481
    miss = f32_sub(miss, v.attacker_ranged_hit_aura if _att_is_ranged(att) else v.attacker_melee_hit_aura)  # :12482-12485
    return f32_max0(miss)                                                                # :12487


def _level_diff(a: Attacker, v: Victim) -> int:
    """``int32 const levelDiff = victim->GetLevelForTarget(this) - GetLevelForTarget(victim)``."""
    return int(v.level_for_target) - int(a.level_for_target)


def _expertise_reduction(a: Attacker, att: str) -> float:
    """Player: ``GetExpertiseDodgeOrParryReduction`` (Player.cpp:5216-5229); creature: aura sum / 4."""
    if a.is_player:
        if att == "base":
            return f32_add(7.5, f32_div(float(a.expertise_mainhand), 4.0))
        if att == "off":
            return f32_add(7.5, f32_div(float(a.expertise_offhand), 4.0))
        return 0.0
    return f32_div(a.creature_expertise_aura, 4.0)


def dodge_chance(a: Attacker, v: Victim, att: str) -> float:
    """``Unit::GetUnitDodgeChance`` -- Unit.cpp:2760-2794."""
    level_diff = _level_diff(a, v)
    chance = 0.0
    level_bonus = 0.0
    if v.is_player:
        chance = f32(v.dodge_percentage)                                                 # :2767
    elif not v.is_totem:
        chance = f32(3.0)                                                                # :2772
        chance = f32_add(chance, v.mod_dodge_percent_aura)                               # :2773
        if level_diff > 0:
            level_bonus = f32_mul(1.5, float(level_diff))                                # :2775-2776
    chance = f32_add(chance, level_bonus)                                                # :2780
    chance = f32_add(chance, a.mod_combat_result_dodge_aura)                             # :2783
    chance = f32_add(chance, a.mod_enemy_dodge_aura)                                     # :2786
    chance = f32_sub(chance, _expertise_reduction(a, att))                               # :2789-2792
    return f32_max0(chance)                                                              # :2793


def parry_chance(a: Attacker, v: Victim, att: str) -> float:
    """``Unit::GetUnitParryChance`` -- Unit.cpp:2796-2834."""
    level_diff = _level_diff(a, v)
    chance = 0.0
    level_bonus = 0.0
    if v.is_player:
        if v.can_parry and v.has_useable_weapon:                                         # :2804-2811
            chance = f32(v.parry_percentage)
    elif not v.is_totem and not v.creature_no_parry:                                     # :2816
        chance = f32(6.0)                                                                # :2818
        chance = f32_add(chance, v.mod_parry_percent_aura)                               # :2819
        if level_diff > 0:
            level_bonus = f32_mul(1.5, float(level_diff))                                # :2821-2822
    chance = f32_add(chance, level_bonus)                                                # :2826
    chance = f32_sub(chance, _expertise_reduction(a, att))                               # :2829-2832
    return f32_max0(chance)                                                              # :2833


def block_chance(a: Attacker, v: Victim, att: str) -> float:
    """``Unit::GetUnitBlockChance`` -- Unit.cpp:2843-2872 (attType unused)."""
    level_diff = _level_diff(a, v)
    chance = 0.0
    level_bonus = 0.0
    if v.is_player:
        if v.can_block and v.has_useable_shield:                                         # :2851-2856
            chance = f32(v.block_percentage)
    elif not v.is_totem and not v.creature_no_block:                                     # :2860
        chance = f32(3.0)                                                                # :2862
        chance = f32_add(chance, v.mod_block_percent_aura)                               # :2863
        if level_diff > 0:
            level_bonus = f32_mul(1.5, float(level_diff))                                # :2865-2866
    chance = f32_add(chance, level_bonus)                                                # :2870
    return f32_max0(chance)                                                              # :2871


def crit_chance_done(a: Attacker, att: str) -> float:
    """``Unit::GetUnitCriticalChanceDone`` -- Unit.cpp:2874-2907."""
    if a.is_player:
        return f32(a.crit_done_pct) if att in ("base", "off", "ranged") else 0.0        # :2879-2894
    if a.creature_no_crit:
        return 0.0                                                                       # :2898
    chance = f32(5.0)                                                                    # :2900
    chance = f32_add(chance, a.creature_weapon_crit_aura)                                # :2901
    chance = f32_add(chance, a.creature_crit_pct_aura)                                   # :2902
    return chance


def crit_chance_taken(a: Attacker, v: Victim, att: str, done: float) -> float:
    """``Unit::GetUnitCriticalChanceTaken`` -- Unit.cpp:2909-2938."""
    chance = f32(done)
    if not _att_is_ranged(att):
        chance = f32_add(chance, v.attacker_melee_crit_aura)                             # :2914-2915
    chance = f32_add(chance, v.crit_vs_target_health_aura)                               # :2917-2920
    chance = f32_add(chance, v.crit_for_caster_aura)                                     # :2922-2925
    if a.is_temp_summon:
        chance = f32_add(chance, v.crit_for_caster_pet_aura)                             # :2927-2933
    chance = f32_add(chance, v.spell_and_weapon_crit_aura)                               # :2935
    return f32_max0(chance)                                                              # :2937


def crit_chance_against(a: Attacker, v: Victim, att: str) -> float:
    """``Unit::GetUnitCriticalChanceAgainst`` -- Unit.cpp:2940-2944."""
    return crit_chance_taken(a, v, att, crit_chance_done(a, att))


def white_crit_chance(a: Attacker, v: Victim, att: str) -> float:
    """The white-swing crit term before ``int32(... * 100.0f)`` -- Unit.cpp:2398."""
    return f32_add(crit_chance_against(a, v, att), a.autoattack_crit_aura)


# ---------------------------------------------------------------------------
# white swing table (RollMeleeOutcomeAgainst)
# ---------------------------------------------------------------------------

@dataclass
class Band:
    outcome: str
    eligible: bool
    chance_pct: float          # binary32 percent that was converted
    chance_bp: int             # int32(chance * 100.0f)
    lo: int | None             # roll >= lo and roll < hi selects this outcome (None if never)
    hi: int | None
    reason: str = ""


@dataclass
class WhiteTable:
    attack_type: str
    roll: str
    bands: list[Band]
    sitting_auto_crit: bool
    evade: bool
    notes: list[str] = field(default_factory=list)

    def outcome_for_roll(self, roll: int) -> str:
        if self.evade:
            return "evade"
        if not 0 <= roll <= 9999:
            raise ValueError("roll must be in urand(0, 9999)")
        for b in self.bands:
            if b.outcome == "miss" and b.eligible and b.lo is not None and b.lo <= roll < b.hi:
                return "miss"
        if self.sitting_auto_crit:
            return "crit"
        for b in self.bands:
            if b.outcome == "miss":
                continue
            if b.eligible and b.lo is not None and b.lo <= roll < b.hi:
                return b.outcome
        return "normal"

    def probabilities(self) -> dict[str, float]:
        """Exact outcome probabilities under the uniform 10,000-point draw."""
        counts: dict[str, int] = {}
        for r in range(10000):
            o = self.outcome_for_roll(r)
            counts[o] = counts.get(o, 0) + 1
        return {k: v / 10000.0 for k, v in sorted(counts.items())}

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["probabilities"] = self.probabilities()
        return d


def white_table(a: Attacker, v: Victim, att: str = "base") -> WhiteTable:
    """``Unit::RollMeleeOutcomeAgainst`` -- Unit.cpp:2389-2499, one ``urand(0, 9999)`` draw."""
    if att not in ("base", "off"):
        # AttackerStateUpdate returns before rolling for RANGED (Unit.cpp:2285-2286); CalculateMeleeDamage
        # returns on the default switch branch (Unit.cpp:1367-1368).
        raise ValueError("white swings are BASE_ATTACK or OFF_ATTACK only")
    notes: list[str] = []
    if v.evading and not v.is_player:
        return WhiteTable(att, "none (MELEE_HIT_EVADE before any draw, Unit.cpp:2391-2392)", [], False, True)

    miss_bp = to_basis_points(miss_chance(a, v, att, None))                       # :2395
    crit_bp = to_basis_points(white_crit_chance(a, v, att))                       # :2398
    dodge_bp = to_basis_points(dodge_chance(a, v, att))                           # :2400
    block_bp = to_basis_points(block_chance(a, v, att))                           # :2401
    parry_bp = to_basis_points(parry_chance(a, v, att))                           # :2402

    attacker_level = int(a.level_for_target)
    victim_level = int(v.level_for_target)
    can_parry_or_block = v.facing_attacker or v.ignore_hit_direction              # :2416
    can_dodge = (not v.is_player) or can_parry_or_block                           # :2419
    if v.casting_or_controlled:                                                   # :2422-2426
        can_dodge = False
        can_parry_or_block = False

    bands: list[Band] = []
    sum_ = 0

    def push(outcome: str, eligible: bool, chance_pct: float, bp: int, reason: str, positive_only: bool = True) -> None:
        nonlocal sum_
        if eligible and (bp > 0 or not positive_only):
            lo = sum_
            sum_ += bp
            bands.append(Band(outcome, True, chance_pct, bp, lo, sum_, reason))
        else:
            bands.append(Band(outcome, False, chance_pct, bp, None, None, reason))

    push("miss", True, miss_chance(a, v, att, None), miss_bp, "always tested; tmp > 0 required (:2429-2431)")
    sitting = v.is_player and crit_bp > 0 and not v.stand_state                   # :2434-2435
    push("dodge", can_dodge, dodge_chance(a, v, att), dodge_bp,
         "canDodge: creature victim, or player victim facing / IGNORE_HIT_DIRECTION; not casting/controlled (:2437-2443)")
    push("parry", can_parry_or_block, parry_chance(a, v, att), parry_bp,
         "canParryOrBlock: victim HasInArc(pi) or IGNORE_HIT_DIRECTION; not casting/controlled (:2446-2452)")
    glancing_ok = ((a.is_player or a.is_pet) and not v.is_player and not v.is_pet
                   and attacker_level + 3 < victim_level)                          # :2456-2458
    glancing_bp = (10 + 10 * (victim_level - attacker_level)) * 100 if glancing_ok else 0
    push("glancing", glancing_ok, glancing_bp / 100.0, glancing_bp,
         "attacker player/pet, victim creature non-pet, attackerLevel + 3 < victimLevel (:2456-2464)")
    push("block", can_parry_or_block, block_chance(a, v, att), block_bp, "canParryOrBlock (:2467-2473)")
    push("crit", True, white_crit_chance(a, v, att), crit_bp, "always tested; tmp > 0 required (:2476-2479)")
    crushing_ok = (attacker_level >= victim_level + 4 and not a.is_controlled_by_player
                   and not (not a.is_player and a.creature_no_crushing))          # :2481-2486
    crushing_bp = attacker_level - victim_level * 1000 - 1500                     # :2489 (precedence as written)
    # the roll test has no tmp > 0 guard (:2491); a negative tmp lowers sum and can never be selected
    push("crushing", crushing_ok, crushing_bp / 100.0, crushing_bp,
         "attackerLevel >= victimLevel + 4, attacker not player-controlled, no NO_CRUSHING_BLOWS flag (:2483-2486); "
         "tmp = attackerLevel - victimLevel * 1000 - 1500 is negative for every level >= 2 (:2489) -> never selected",
         positive_only=False)
    if crushing_ok:
        notes.append("crushing eligible but arithmetically unreachable: tmp < 0 (Unit.cpp:2489)")
    bands.append(Band("normal", True, 0.0, 0, sum_ if sum_ < 10000 else None, 10000, "remainder (:2497-2498)"))
    if sum_ > 10000:
        notes.append(f"sum of eligible bands = {sum_} > 10000: later bands are partially/fully unreachable")
    return WhiteTable(att, "urand(0, 9999) once (Unit.cpp:2410); damage was drawn first by CalculateDamage (Unit.cpp:1383 -> :2550)",
                      bands, sitting, False, notes)


# ---------------------------------------------------------------------------
# melee/ranged spell table (MeleeSpellHitResult)
# ---------------------------------------------------------------------------

@dataclass
class SpellBand:
    result: str
    eligible: bool
    chance_pct: float
    chance_bp: int
    lo: int | None
    hi: int | None
    reason: str = ""


@dataclass
class SpellTable:
    attack_type: str
    roll: str
    bands: list[SpellBand]
    short_circuit: str | None
    crit: dict[str, Any]
    notes: list[str] = field(default_factory=list)

    def result_for_roll(self, roll: int) -> str:
        if self.short_circuit:
            return self.short_circuit
        for b in self.bands:
            if b.eligible and b.lo is not None and b.lo <= roll < b.hi:
                return b.result
        return "none"

    def probabilities(self) -> dict[str, float]:
        counts: dict[str, int] = {}
        for r in range(10000):
            o = self.result_for_roll(r)
            counts[o] = counts.get(o, 0) + 1
        return {k: v / 10000.0 for k, v in sorted(counts.items())}

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["probabilities"] = self.probabilities()
        return d


def spell_table(a: Attacker, v: Victim, spell: SpellFacts) -> SpellTable:
    """``Unit::MeleeSpellHitResult`` -- Unit.cpp:2621-2758, one ``urand(0, 9999)`` draw (uint32 sums).

    Entered from ``WorldObject::SpellHitResult`` (Object.cpp:1949-1993) for
    ``SPELL_DAMAGE_CLASS_MELEE``/``RANGED`` after immunity, positivity,
    self-cast, evade, reflect and ``SPELL_ATTR3_ALWAYS_HIT`` checks.
    """
    att = "ranged" if spell.dmg_class_ranged else "base"                         # :2626-2631
    crit = _spell_crit(a, v, spell, att)
    if v.evading and not v.is_player:
        return SpellTable(att, "none", [], "evade", crit, ["evade: Object.cpp:1968-1969 (checked before reflect and ALWAYS_HIT)"])
    if spell.always_hit:
        return SpellTable(att, "none", [], "none", crit, ["SPELL_ATTR3_ALWAYS_HIT short-circuits in WorldObject::SpellHitResult (Object.cpp:1980-1981), after the reflect roll"])
    if spell.no_avoidance:
        return SpellTable(att, "none", [], "none", crit, ["SPELL_ATTR3_NO_AVOIDANCE returns SPELL_MISS_NONE before any draw (Unit.cpp:2623-2624)"])

    bands: list[SpellBand] = []
    tmp = 0  # uint32

    def push(result: str, eligible: bool, chance_pct: float, bp: int, reason: str) -> None:
        nonlocal tmp
        if eligible:
            lo = tmp
            tmp = (tmp + bp) & 0xFFFFFFFF
            bands.append(SpellBand(result, True, chance_pct, bp, lo, tmp, reason))
        else:
            bands.append(SpellBand(result, False, chance_pct, bp, None, None, reason))

    miss = miss_chance(a, v, att, spell)
    push("miss", True, miss, int32_trunc(f32_mul(miss, 100.0)) & 0xFFFFFFFF, "uint32(MeleeSpellMissChance * 100.0f) (:2635-2640)")
    resist = f32_mul(v.mechanic_resist_pct, 100.0)
    push("resist", True, v.mechanic_resist_pct, int32_trunc(resist), "int32 resist_chance = GetMechanicResistChance * 100.0f (:2643-2646)")
    if spell.no_active_defense:
        return SpellTable(att, "urand(0, 9999) once (Unit.cpp:2633)", bands, None, crit,
                          ["SPELL_ATTR0_NO_ACTIVE_DEFENSE: returns SPELL_MISS_NONE after miss/resist (:2649-2650)"])
    can_dodge = not spell.no_attack_dodge                                        # :2652
    can_parry = not spell.no_attack_parry                                        # :2653
    can_block = not spell.no_attack_block                                        # :2654
    if v.casting_or_controlled:                                                  # :2657-2662
        can_dodge = can_parry = can_block = False
    notes: list[str] = []
    if att == "ranged":                                                          # :2665-2678
        can_parry = False
        can_dodge = False
        in_front = (not v.controlled) and (v.facing_attacker or v.ignore_hit_direction)   # only CONTROLLED gates deflect (:2670)
        deflect = f32_mul(v.deflect_aura, 100.0)
        push("deflect", in_front, v.deflect_aura, int32_trunc(deflect), "ranged only, victim in front (or IGNORE_HIT_DIRECTION), not UNIT_STATE_CONTROLLED -- casting does not remove deflect (:2670-2676)")
    if not v.facing_attacker:                                                    # :2680-2697
        if not v.ignore_hit_direction:
            if v.is_player:
                can_dodge = False
            can_parry = False
            can_block = False
        elif spell.req_caster_behind_target:
            can_parry = False
    for state in a.ignore_combat_result:                                         # :2700-2721 SPELL_AURA_IGNORE_COMBAT_RESULT
        if state == "dodge":
            can_dodge = False
        elif state == "block":
            can_block = False
        elif state == "parry":
            can_parry = False
    d = dodge_chance(a, v, att)
    push("dodge", can_dodge, d, max(to_basis_points(d), 0), "canDodge (:2724-2731)")
    p = parry_chance(a, v, att)
    push("parry", can_parry, p, max(to_basis_points(p), 0), "canParry (:2735-2743)")
    b = block_chance(a, v, att)
    push("block", can_block, b, max(to_basis_points(b), 0), "canBlock (:2746-2754); a blocked spell is a full block (createProcHitMask, Unit.cpp:10435-10437)")
    return SpellTable(att, "urand(0, 9999) once (Unit.cpp:2633)", bands, None, crit, notes)


def _spell_crit(a: Attacker, v: Victim, spell: SpellFacts, att: str) -> dict[str, Any]:
    """Crit for melee/ranged-class spells is a *separate* draw (Spell.cpp:8555-8563).

    ``SpellCritChanceDone`` (Unit.cpp:7120-7172) adds ``GetUnitCriticalChanceDone``;
    ``SpellCritChanceTaken`` (Unit.cpp:7174-7240) routes MELEE/RANGED through
    ``GetUnitCriticalChanceTaken``; ``roll_chance(float)`` is ``chance > rand_chance()``
    (Random.h:54-58) with ``rand_chance()`` a binary32 uniform in ``[0, 100)``
    (Random.cpp:81-85).
    """
    if not spell.can_crit:
        return {"chance_pct": 0.0, "draw": "none (SPELL_ATTR0_CU_CAN_CRIT missing -> 0.0f, Unit.cpp:7128-7129)"}
    if not a.is_player and not a.has_spell_mod_owner:
        return {"chance_pct": 0.0, "draw": "none (creature without GetSpellModOwner() -> 0.0f, Unit.cpp:7122-7123)"}
    done = crit_chance_done(a, att)
    taken = crit_chance_taken(a, v, att, done)
    return {"chance_pct": taken, "draw": "roll_chance(float critChance): critChance > rand_chance(), rand_chance() = std::uniform_real_distribution<float>(0, 100) (binary32) -- Spell.cpp:8563, Random.h:54-58, Random.cpp:81-85",
            "note": "SpellModOp::CritChance spellmods applied in SpellCritChanceDone (Unit.cpp:7168-7169) are a supplied input here"}


# ---------------------------------------------------------------------------
# outcome application (what the outcome does to CalcDamageInfo) -- Unit.cpp:1389-1490
# ---------------------------------------------------------------------------

OUTCOME_APPLICATION: dict[str, dict[str, Any]] = {
    "evade": {"hit_info": "HITINFO_MISS | HITINFO_SWINGNOHITSOUND", "target_state": "VICTIMSTATE_EVADES", "damage": "0", "clean_damage": "0", "returns_early": True, "coord": "Unit.cpp:1404-1411"},
    "miss": {"hit_info": "HITINFO_MISS", "target_state": "VICTIMSTATE_INTACT", "damage": "0", "clean_damage": "0", "coord": "Unit.cpp:1412-1419"},
    "normal": {"hit_info": "-", "target_state": "VICTIMSTATE_HIT", "damage": "unchanged", "coord": "Unit.cpp:1420-1423"},
    "crit": {"hit_info": "HITINFO_CRITICALHIT", "target_state": "VICTIMSTATE_HIT", "damage": "Damage *= 2; AddPct(Damage, (MOD_CRIT_DAMAGE_BONUS multiplier - 1) * 100) [uint32 AddPct]", "coord": "Unit.cpp:1424-1440"},
    "parry": {"hit_info": "-", "target_state": "VICTIMSTATE_PARRY", "damage": "0 (CleanDamage += Damage)", "coord": "Unit.cpp:1441-1447"},
    "dodge": {"hit_info": "-", "target_state": "VICTIMSTATE_DODGE", "damage": "0 (CleanDamage += Damage)", "coord": "Unit.cpp:1448-1454"},
    "block": {"hit_info": "HITINFO_BLOCK", "target_state": "VICTIMSTATE_HIT (never VICTIMSTATE_BLOCKS)", "damage": "Blocked = CalculatePct(Damage, victim->GetBlockPercent(attacker GetLevel())) [creature victim: 30.0f percent; player victim: a fraction <= 0.85 consumed as a percent]; if IsBlockCritical (roll_chance(float aura 253 sum)): Blocked *= 2, Blocked *= MOD_CRITICAL_BLOCK_AMOUNT multiplier (uint32 *= float); Damage -= Blocked", "coord": "Unit.cpp:1455-1469, Unit.h:987, Player.cpp:26822-26831, Unit.cpp:2575-2580"},
    "glancing": {"hit_info": "HITINFO_GLANCING", "target_state": "VICTIMSTATE_HIT", "damage": "uint32((1.f - min(victimLevel - attackerLevel, 3) * 0.1f) * Damage)", "coord": "Unit.cpp:1470-1483"},
    "crushing": {"hit_info": "HITINFO_CRUSHING", "target_state": "VICTIMSTATE_HIT", "damage": "Damage += Damage / 2", "coord": "Unit.cpp:1484-1490"},
}


def block_percent(shield_block: int, armor_constant: float) -> float:
    """``Player::GetBlockPercent`` -- Player.cpp:26822-26831 (binary32).

    ``shield_block`` is ``ActivePlayerData::ShieldBlock`` (int32); ``armor_constant``
    is ``EvaluateExpectedStat(ArmorConstant, attackerLevel, -2, 0, CLASS_NONE, 0)``
    (ExpectedStat.csv ``ArmorConstant`` for ``Lvl``, expansion -2 fallback).
    Returns a *fraction* (not percent) that ``CalculatePct`` then treats as percent
    (Unit.cpp:1459) -- see the report for that unit mismatch.
    """
    block_armor = f32(float(shield_block))
    armor_constant = f32(armor_constant)
    if f32_add(block_armor, armor_constant) == 0.0:
        return 0.0
    q = f32_div(block_armor, f32_add(block_armor, armor_constant))
    return q if q < f32(0.85) else f32(0.85)


def crit_damage(damage: int, crit_damage_multiplier: float = 1.0) -> int:
    """Unit.cpp:1424-1440 (white MELEE_HIT_CRIT): ``Damage *= 2``; ``mod = (multiplier - 1.0f) * 100``;
    ``if (mod != 0) AddPct(Damage, mod)`` with ``AddPct<uint32, float>`` = ``Damage += uint32(Damage * mod / 100.0f)``.

    ``crit_damage_multiplier`` is ``GetTotalAuraMultiplierByMiscMask(SPELL_AURA_MOD_CRIT_DAMAGE_BONUS, school)``
    (a binary32 product).  Example: multiplier 1.3f -> mod 29.9999962f -> 2000 * mod / 100 = 599.9999 -> +599.
    """
    damage = (damage * 2) & 0xFFFFFFFF
    mod = f32_mul(f32_sub(crit_damage_multiplier, 1.0), 100.0)
    if mod != 0.0:
        damage = (damage + calculate_pct_u32(damage, mod)) & 0xFFFFFFFF
    return damage


def glancing_damage(damage: int, attacker_level: int, victim_level: int) -> tuple[int, int]:
    """Unit.cpp:1470-1483: returns (Damage, CleanDamage increment).

    ``leveldif = int32(victim->GetLevel()) - int32(GetLevel())`` capped at 3 (raw levels, not
    GetLevelForTarget); ``reducePercent = 1.f - leveldif * 0.1f``; ``uint32(reducePercent * Damage)``.
    """
    leveldif = min(int(victim_level) - int(attacker_level), 3)
    reduce = f32_sub(1.0, f32_mul(float(leveldif), 0.1))
    reduced = int32_trunc(f32_mul(reduce, float(damage)))
    return reduced, damage - reduced


def creature_block_percent() -> float:
    """``Unit::GetBlockPercent`` default -- Unit.h:987 (``return 30.0f``), a percent."""
    return f32(30.0)


def white_blocked_amount(damage: int, block_pct: float, block_critical: bool = False, critical_block_multiplier: float = 1.0) -> int:
    """Unit.cpp:1459-1464 -- ``Blocked = CalculatePct(Damage, pct)``; critical: ``*= 2`` then ``*= multiplier``.

    ``Blocked`` is ``uint32``; ``Blocked *= float`` converts through float and truncates.
    """
    blocked = calculate_pct_u32(damage, block_pct)
    if block_critical:
        blocked *= 2
        blocked = int32_trunc(f32_mul(float(blocked), critical_block_multiplier))
    return blocked


def spell_block_value(block_pct: float) -> int:
    """Unit.cpp:1239 -- ``uint32 value = victim->GetBlockPercent(GetLevel())``: the percent is truncated first.

    For a player victim the fraction (< 1) becomes 0, so a blocked weapon-spell hit on a player
    blocks nothing; a creature victim yields 30.
    """
    return int32_trunc(f32(block_pct))


def calculate_pct_u32(base: int, pct: float) -> int:
    """``CalculatePct<uint32, float>`` -- Util.h:72-75: ``T(base * float(pct) / 100.0f)``."""
    return int32_trunc(f32_div(f32_mul(float(base), pct), 100.0))


# ---------------------------------------------------------------------------
# corpus
# ---------------------------------------------------------------------------

NUMERIC_RULES: list[dict[str, Any]] = [
    {"value": "miss/dodge/parry/block/crit chance", "source_type": "float (Unit member functions), player fields are UpdateField<float>",
     "intermediate_type": "float", "aggregation_precision": "binary32, left-to-right as written", "rounding": "std::max(x, 0.0f) clamp then int32(x * 100.0f) truncation",
     "integer_conversion": "Unit.cpp:2395-2402 (white), :2635-2646, :2726-2748 (spell) -- reachable on every swing", "units": "percent -> basis points (1/100 percent)"},
    {"value": "roll", "source_type": "urand(0, 9999) (Random.cpp:42-47, std::uniform_int_distribution<uint32>)", "intermediate_type": "int32 (white) / uint32 (spell)",
     "aggregation_precision": "exact integer cumulative sum", "rounding": "none", "integer_conversion": "n/a", "units": "basis points"},
    {"value": "level difference", "source_type": "uint8 GetLevelForTarget", "intermediate_type": "int32", "aggregation_precision": "exact",
     "rounding": "none", "integer_conversion": "Unit.cpp:2412-2413, :2762, :2798, :2845", "units": "levels"},
    {"value": "glancing chance", "source_type": "int32 arithmetic", "intermediate_type": "int32", "aggregation_precision": "exact", "rounding": "none",
     "integer_conversion": "n/a (already int)", "units": "basis points; (10 + 10 * diff) * 100"},
    {"value": "crushing chance", "source_type": "int32 arithmetic", "intermediate_type": "int32", "aggregation_precision": "exact", "rounding": "none",
     "integer_conversion": "n/a", "units": "basis points as written: attackerLevel - victimLevel * 1000 - 1500 (negative)"},
    {"value": "spell crit / block crit", "source_type": "float chance vs float rand_chance()", "intermediate_type": "float chance, float draw (uniform_real_distribution<float>(0, 100))",
     "aggregation_precision": "binary32 on both sides (Random.cpp:81-85)", "rounding": "none", "integer_conversion": "none", "units": "percent",
     "coord": "Spell.cpp:8563, Unit.cpp:2575-2580, Random.h:54-58"},
    {"value": "block percent", "source_type": "player: int32 ShieldBlock, float ArmorConstant (ExpectedStat); creature: constant 30.0f (Unit.h:987)", "intermediate_type": "float",
     "aggregation_precision": "binary32", "rounding": "player: std::min(q, 0.85f); white: CalculatePct<uint32,float> truncation (Unit.cpp:1459); spell: uint32 value = GetBlockPercent() truncation first (Unit.cpp:1239) then CalculatePct<int32?,uint32>",
     "integer_conversion": "Unit.cpp:1459 (white), Unit.cpp:1239 (spell; truncates a player's fraction to 0)", "units": "player: fraction returned, consumed as percent; creature: percent"},
    {"value": "expertise", "source_type": "int32 MainhandExpertise/OffhandExpertise (UpdateField)", "intermediate_type": "float", "aggregation_precision": "binary32",
     "rounding": "int32(GetRatingBonusValue(CR_EXPERTISE)) truncation at StatSystem.cpp:774; then / 4.0f", "integer_conversion": "StatSystem.cpp:774", "units": "expertise points -> percent"},
]


def build_corpus(generator: str) -> dict[str, Any]:
    from .witnesses_d import table_witness_inputs  # local import: witnesses own the witness configurations
    paths = {
        "white_mh": {"entry": "Unit::DoMeleeAttackIfReady -> AttackerStateUpdate(BASE_ATTACK) -> CalculateMeleeDamage -> RollMeleeOutcomeAgainst", "coords": ["Unit.cpp:2176-2251", "Unit.cpp:2262-2363", "Unit.cpp:1327-1527", "Unit.cpp:2389-2499"], "draw": "one urand(0,9999) (Unit.cpp:2410) after the damage draw urand(min,max) (Unit.cpp:2550; both skipped on physical immunity, Unit.cpp:1371-1380; only the outcome draw is skipped on evade); block-crit roll_chance(float) only on MELEE_HIT_BLOCK (Unit.cpp:2575-2580, 1460); later draws on the same engine in the same swing: CalcSpellResistedDamage rand_norm() only for a magic-school weapon (Unit.cpp:1777-1799), DealDamage durability roll_chance(float RATE_DURABILITY_LOSS_DAMAGE, default 0.5) for a player victim and again for a player attacker when damage > 0 and the victim survives, each + urand(0, EQUIPMENT_SLOT_END-1) on success (Unit.cpp:1090-1107, World.cpp:1015), creature daze roll_chance(float) (Unit.cpp:1573-1594), then item/aura proc rolls (CastItemCombatSpell, ProcSkillsAndAuras); counts are distribution calls, not engine outputs (uniform_int_distribution may consume more than one)"},
        "white_oh": {"entry": "same with OFF_ATTACK; HITINFO_OFFHAND (Unit.cpp:1365); miss adds 19% while dual wielding (Unit.cpp:12463-12465) regardless of hand", "coords": ["Unit.cpp:2231-2247"], "draw": "as white_mh"},
        "ranged_auto": {"entry": "Unit::_UpdateAutoRepeatSpell -> Spell::prepare -> Spell::SelectSpellTargets -> AddUnitTarget -> WorldObject::SpellHitResult -> Unit::MeleeSpellHitResult(RANGED_ATTACK)", "coords": ["Unit.cpp:3019-3055", "Spell.cpp:2478-2489", "Object.cpp:1949-1993", "Unit.cpp:2621-2758", "Spell.cpp:8555-8563"], "draw": "reflect roll_chance (only if canReflect and aura present), urand(0,9999) for miss/resist/deflect/block, roll_chance(float) for crit; damage rolled later by Spell::EffectWeaponDmg"},
        "melee_spell": {"entry": "as ranged_auto with DmgClass MELEE -> attType BASE_ATTACK (dodge/parry/block tested)", "coords": ["Unit.cpp:2626-2631"], "draw": "as ranged_auto"},
        "ranged_spell": {"entry": "as ranged_auto (any DmgClass RANGED spell)", "coords": ["Unit.cpp:2666-2679"], "draw": "as ranged_auto"},
    }
    examples = []
    for w in table_witness_inputs():
        a, v = w["attacker"], w["victim"]
        rec: dict[str, Any] = {"id": w["id"], "label": w["label"], "attacker": asdict(a), "victim": asdict(v)}
        if w.get("spell") is not None:
            rec["spell"] = asdict(w["spell"])
            rec["table"] = spell_table(a, v, w["spell"]).to_dict()
        else:
            rec["tables"] = {att: white_table(a, v, att).to_dict() for att in w.get("hands", ("base",))}
        examples.append(rec)
    return {
        "provenance": {"snapshot_build": SNAPSHOT_BUILD, "trinitycore_commit": TRINITY_COMMIT, "generator": generator,
                       "wowlab_data_commit": _git_head()},
        "order_white": ["evade (pre-roll)", "miss", "sitting player auto-crit", "dodge", "parry", "glancing", "block", "crit", "crushing", "normal"],
        "order_spell": ["immune/positive/self/evade/reflect/always-hit (SpellHitResult)", "no-avoidance", "miss", "resist", "no-active-defense", "deflect (ranged)", "dodge", "parry", "block", "none; crit separately"],
        "constants": CONSTANTS,
        "numeric_rules": NUMERIC_RULES,
        "paths": paths,
        "outcome_application": OUTCOME_APPLICATION,
        "examples": examples,
    }


def _git_head() -> str:
    import subprocess
    from . import ROOT
    try:
        return subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    except Exception:  # pragma: no cover
        return "unknown"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _cmd_table(args: argparse.Namespace) -> int:
    a = Attacker(is_player=True, is_pet=False, is_controlled_by_player=True, level_for_target=args.attacker_level,
                 have_offhand_weapon=args.dual_wield, crit_done_pct=args.crit, autoattack_crit_aura=args.autoattack_crit,
                 mod_hit_chance_aura=args.miss_mod, expertise_mainhand=args.expertise, expertise_offhand=args.expertise,
                 ignore_dual_wield_penalty=args.ignore_dw_penalty)
    v = Victim(is_player=(args.victim == "player"), level_for_target=args.victim_level, facing_attacker=(args.facing == "front"),
               dodge_percentage=args.dodge or 0.0, parry_percentage=args.parry or 0.0, block_percentage=args.block or 0.0,
               can_parry=args.parry is not None, can_block=args.block is not None, has_useable_shield=args.block is not None,
               mod_dodge_percent_aura=(args.dodge or 0.0) if args.victim == "npc" else 0.0,
               mod_parry_percent_aura=(args.parry or 0.0) if args.victim == "npc" else 0.0,
               mod_block_percent_aura=(args.block or 0.0) if args.victim == "npc" else 0.0,
               attacker_melee_crit_aura=args.victim_crit_mod, stand_state=not args.sitting,
               casting=args.victim_casting, controlled=args.victim_controlled, evading=args.evading)
    out: dict[str, Any] = {"attacker": asdict(a), "victim": asdict(v)}
    if args.path == "white":
        out["tables"] = {att: white_table(a, v, att).to_dict() for att in (("base", "off") if args.dual_wield else ("base",))}
    else:
        sp = SpellFacts(dmg_class_ranged=(args.path == "ranged-spell"))
        out["table"] = spell_table(a, v, sp).to_dict()
    print(json.dumps(out, indent=1))
    return 0


def _cmd_corpus(args: argparse.Namespace) -> int:
    corpus = build_corpus("python3 weapon_combat.py table-corpus")
    CORPORA.mkdir(parents=True, exist_ok=True)
    path = CORPORA / "attack-table.json"
    path.write_text(json.dumps(corpus, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {path} ({len(corpus['examples'])} examples)")
    return 0


def register(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser("table", help="construct Trinity's attack table for given inputs (binary32 emulation)")
    p.add_argument("--attacker-level", type=int, default=90)
    p.add_argument("--victim-level", type=int, default=93)
    p.add_argument("--victim", choices=("npc", "player"), default="npc")
    p.add_argument("--path", choices=("white", "melee-spell", "ranged-spell"), default="white")
    p.add_argument("--dodge", type=float, default=None, help="player: DodgePercentage; npc: MOD_DODGE_PERCENT aura sum")
    p.add_argument("--parry", type=float, default=None, help="player: ParryPercentage (implies CanParry+weapon); npc: MOD_PARRY_PERCENT")
    p.add_argument("--block", type=float, default=None, help="player: BlockPercentage (implies CanBlock+shield); npc: MOD_BLOCK_PERCENT")
    p.add_argument("--crit", type=float, default=5.0, help="attacker CritPercentage field")
    p.add_argument("--autoattack-crit", type=float, default=0.0, help="SPELL_AURA_MOD_AUTOATTACK_CRIT_CHANCE sum")
    p.add_argument("--victim-crit-mod", type=float, default=0.0, help="victim SPELL_AURA_MOD_ATTACKER_MELEE_CRIT_CHANCE sum")
    p.add_argument("--miss-mod", type=float, default=0.0, help="attacker SPELL_AURA_MOD_HIT_CHANCE sum")
    p.add_argument("--expertise", type=int, default=0, help="ActivePlayerData::MainhandExpertise")
    p.add_argument("--dual-wield", action="store_true")
    p.add_argument("--ignore-dw-penalty", action="store_true", help="SPELL_AURA_IGNORE_DUAL_WIELD_HIT_PENALTY present")
    p.add_argument("--facing", choices=("front", "behind"), default="front")
    p.add_argument("--sitting", action="store_true", help="player victim not in stand state")
    p.add_argument("--victim-casting", action="store_true", help="victim IsNonMeleeSpellCast(false, false, true)")
    p.add_argument("--victim-controlled", action="store_true", help="victim has UNIT_STATE_CONTROLLED")
    p.add_argument("--evading", action="store_true", help="creature victim IsEvadingAttacks")
    p.set_defaults(func=_cmd_table)
    c = subparsers.add_parser("table-corpus", help="write docs/research/weapon-combat-corpora/attack-table.json")
    c.set_defaults(func=_cmd_corpus)
