"""Derived character quantities: health, AP, SP, armor, crit, haste, mastery…

Mirrors, all in ``src/server/game/Entities/Unit/StatSystem.cpp`` unless noted:

    Unit::GetTotalStatValue            (Unit.cpp)
    Player::UpdateStats                dependency fan-out
    Player::UpdateAllStats             the full recalculation order
    Player::GetHealthBonusFromStamina  HpPerSta.txt
    Player::UpdateMaxHealth
    Player::UpdateMaxPower
    Player::UpdateArmor
    Player::GetPrimaryStat
    Player::UpdateAttackPowerAndDamage
    Player::UpdateAllCritPercentages / UpdateCritPercentage
    Player::UpdateSpellCritChance
    Player::UpdateMastery
    Player::UpdateVersatilityDamageDone / UpdateHealingDonePercentMod
    Unit::SpellBaseDamageBonusDone     (Unit.cpp)  spell power
    Player::CalculateMinMaxDamage      weapon damage assembly

This module models the **flat, item-driven** stages only.  Aura stages
(``GetTotalAuraModifier``, ``GetPctModifierValue`` on anything other than the
defaults, shapeshift forms, ``SPELL_AURA_OVERRIDE_*``) are ordinary spell
semantics and are deliberately left out; every function says where they would
enter.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import CharacterSourceError
from .identity import (
    MAX_STATS,
    STAT_AGILITY,
    STAT_INTELLECT,
    STAT_NAMES,
    STAT_STAMINA,
    STAT_STRENGTH,
    ClassInfo,
)
from gearing.tables import Tables

#: Mirrors: ``Player::UpdateAllCritPercentages`` -- the flat base for melee,
#: offhand and ranged -- and ``Player::UpdateSpellCritChance``.
BASE_CRIT_PERCENT = 5.0
#: Mirrors: ``Player::UpdateBlockPercentage``.
BASE_BLOCK_PERCENT = 5.0
#: Mirrors: ``Player::UpdateMeleeHitChances`` / ``UpdateRangedHitChances`` /
#: ``UpdateSpellHitChances``.
BASE_HIT_PERCENT = 7.5
#: Mirrors: ``Player::GetExpertiseDodgeOrParryReduction``.
BASE_EXPERTISE = 7.5


@dataclass
class StatStage:
    """One stat after the ``GetTotalStatValue`` pipeline."""

    stat: int
    create_value: int
    base_flat: float = 0.0
    base_pct_exclude_create: float = 0.0
    base_pct: float = 1.0
    total_flat: float = 0.0
    total_pct: float = 1.0

    @property
    def name(self) -> str:
        return STAT_NAMES[self.stat]

    def value(self) -> float:
        """Mirrors: ``Unit::GetTotalStatValue``.

        ``value = (BASE_VALUE * max(BASE_PCT_EXCLUDE_CREATE, -100%) + create)
                  * BASE_PCT + TOTAL_VALUE``, then ``* TOTAL_PCT``.
        Note ``create`` is added *after* the exclude-create percentage and
        *before* ``BASE_PCT``.
        """
        pct = max(self.base_pct_exclude_create, -100.0)
        value = self.base_flat * pct / 100.0
        value += self.create_value
        value *= self.base_pct
        value += self.total_flat
        value *= self.total_pct
        return value

    def rounded(self) -> int:
        """``SetStat(stat, int32(value))`` -- a truncating cast, not a round."""
        return int(self.value())

    def to_dict(self) -> dict[str, Any]:
        return {
            "stat": self.stat, "stat_name": self.name,
            "create_value": self.create_value,
            "base_flat": self.base_flat,
            "base_pct_exclude_create": self.base_pct_exclude_create,
            "base_pct": self.base_pct,
            "total_flat": self.total_flat,
            "total_pct": self.total_pct,
            "raw_value": self.value(),
            "value": self.rounded(),
            "formula": "((base_flat * base_pct_exclude_create) + create) "
                       "* base_pct + total_flat, then * total_pct, then int32()",
        }


@dataclass
class DerivedStats:
    stats: dict[int, StatStage]
    max_health: int
    health_from_stamina: float
    hp_per_sta: float
    max_mana: int
    armor: int
    bonus_armor: int
    attack_power: int
    attack_power_from_strength: float
    attack_power_from_agility: float
    ranged_attack_power: int
    spell_power: int
    spell_power_from_intellect: int
    provenance: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "stats": {s.name: s.to_dict() for s in self.stats.values()},
            "max_health": self.max_health,
            "health_from_stamina": self.health_from_stamina,
            "hp_per_sta": self.hp_per_sta,
            "max_mana": self.max_mana,
            "armor": self.armor,
            "bonus_armor": self.bonus_armor,
            "attack_power": self.attack_power,
            "attack_power_from_strength": self.attack_power_from_strength,
            "attack_power_from_agility": self.attack_power_from_agility,
            "ranged_attack_power": self.ranged_attack_power,
            "spell_power": self.spell_power,
            "spell_power_from_intellect": self.spell_power_from_intellect,
            "provenance": self.provenance,
        }


class DerivedStatEngine:
    def __init__(self, tables: Tables) -> None:
        self.tables = tables
        self._gt_hp_per_sta = tables.gametable("HpPerSta")

    # -- health ----------------------------------------------------------
    def hp_per_sta(self, level: int) -> float:
        """Mirrors: ``Player::GetHealthBonusFromStamina``.

        ``HpPerSta.txt[level].Health``, defaulting to ``10.0f`` when the row is
        missing.  The comment in the consumer says the ratio is taken from
        ``PaperDollFrame.lua``.
        """
        value = self._gt_hp_per_sta.column(level, 0)
        return 10.0 if value is None else value

    def max_health(self, stamina: float, level: int, *, create_health: int = 0,
                   base_flat: float = 0.0, base_pct: float = 1.0,
                   total_flat: float = 0.0, total_pct: float = 1.0
                   ) -> tuple[int, float]:
        """Mirrors: ``Player::UpdateMaxHealth``.

        ``value = (BASE_VALUE + CreateHealth) * BASE_PCT
                  + TOTAL_VALUE + stamina * HpPerSta[level]``, then
        ``* TOTAL_PCT``, then ``(uint32)`` -- a truncating cast.

        Note the stamina term is added *after* ``BASE_PCT`` and *before*
        ``TOTAL_PCT``, so a base-percentage health aura does not scale it but a
        total-percentage one does.  ``ITEM_MOD_HEALTH`` feeds ``BASE_VALUE``.
        """
        ratio = self.hp_per_sta(level)
        from_stamina = stamina * ratio
        value = (base_flat + create_health) * base_pct
        value += total_flat + from_stamina
        value *= total_pct
        return int(value), from_stamina

    # -- armor -----------------------------------------------------------
    def armor(self, base_flat: float, *, base_pct: float = 1.0,
              total_flat: float = 0.0, total_pct: float = 1.0,
              bonus_armor_pct: float = 1.0,
              armor_pct_from_stat: float = 0.0) -> tuple[int, int]:
        """Mirrors: ``Player::UpdateArmor``.

        ``base = BASE_VALUE * BASE_PCT + SPELL_AURA_MOD_ARMOR_PCT_FROM_STAT``
        then ``value = (base + TOTAL_VALUE) * TOTAL_PCT
                       * SPELL_AURA_MOD_BONUS_ARMOR_PCT``.
        ``SetArmor(int32(value), int32(value - base))`` -- the second argument
        is what the client shows as "bonus armor".

        Gear armor (``ItemTemplate::GetArmor``) arrives in ``TOTAL_VALUE``, so
        it is *not* multiplied by ``BASE_PCT`` and it *is* counted as bonus
        armor.
        """
        value = base_flat * base_pct
        value += armor_pct_from_stat
        base_value = value
        value += total_flat
        value *= total_pct
        value *= bonus_armor_pct
        return int(value), int(value - base_value)

    # -- attack power ----------------------------------------------------
    def attack_power(self, class_info: ClassInfo, strength: float,
                     agility: float, level: int, *, ranged: bool = False,
                     base_pct: float = 1.0, total_flat: float = 0.0,
                     total_pct: float = 1.0
                     ) -> tuple[int, float, float]:
        """Mirrors: ``Player::UpdateAttackPowerAndDamage``.

        Melee:  ``max(STR * AttackPowerPerStrength, 0)
                 + max(AGI * AttackPowerPerAgility, 0)``
        Ranged: ``(level + max(AGI, 0)) * RangedAttackPowerPerAgility``

        That sum becomes ``BASE_VALUE``; the reported attack power is
        ``int32(BASE_VALUE * BASE_PCT)``.  ``TOTAL_VALUE`` (which is where
        ``ITEM_MOD_ATTACK_POWER`` and the weapon's ``int32(dps*6)`` land) is
        reported *separately* as ``AttackPowerModPos`` and is not folded in
        here -- the combat side adds them through
        ``Unit::GetTotalAttackPowerValue``.

        The shapeshift branch (``SpellShapeshiftForm.Flags & 0x20`` adding
        agility at the strength coefficient) and the
        ``SPELL_AURA_OVERRIDE_ATTACK_POWER_BY_SP_PCT`` branch are aura state and
        are not modelled.
        """
        if ranged:
            from_agility = ((level + max(agility, 0.0))
                            * class_info.ranged_attack_power_per_agility)
            from_strength = 0.0
        else:
            from_strength = max(strength * class_info.attack_power_per_strength, 0.0)
            from_agility = max(agility * class_info.attack_power_per_agility, 0.0)
        base = (from_strength + from_agility) * base_pct
        _ = (total_flat, total_pct)
        return int(base), from_strength, from_agility

    # -- spell power -----------------------------------------------------
    @staticmethod
    def spell_power(intellect: float, *, primary_stat_is_intellect: bool,
                    base_spell_power_bonus: int = 0) -> tuple[int, int]:
        """Mirrors: ``Unit::SpellBaseDamageBonusDone`` / ``SpellBaseHealingBonusDone``.

        ``DoneAdvertisedBenefit = <SPELL_AURA_MOD_DAMAGE_DONE by school>
                                + Player::GetBaseSpellPowerBonus()
                                + (primary stat is Intellect
                                   ? max(0, int32(GetStat(INTELLECT))) : 0)``

        ``GetBaseSpellPowerBonus()`` is ``m_baseSpellPower``, which
        ``ITEM_MOD_SPELL_POWER`` feeds through ``ApplySpellPowerBonus``.

        Note this is *not* symmetric with attack power: intellect contributes
        1:1 and only for an Intellect-primary spec, whereas attack power uses
        per-class coefficients for two different stats.
        """
        from_intellect = max(0, int(intellect)) if primary_stat_is_intellect else 0
        return base_spell_power_bonus + from_intellect, from_intellect

    # -- crit ------------------------------------------------------------
    @staticmethod
    def crit_percentage(rating_bonus: float, *, flat_mod: float = 0.0,
                        pct_mod: float = BASE_CRIT_PERCENT) -> float:
        """Mirrors: ``Player::UpdateCritPercentage``.

        ``GetBaseModValue(<slot>, FLAT_MOD) + GetBaseModValue(<slot>, PCT_MOD)
          + GetRatingBonusValue(CR_CRIT_<melee|ranged>)``.

        ``UpdateAllCritPercentages`` sets the PCT_MOD of all three melee slots
        to a flat 5.0 first -- despite the name it is an additive percentage,
        not a multiplier.  There is no agility-to-crit conversion any more.
        """
        return flat_mod + pct_mod + rating_bonus

    @staticmethod
    def spell_crit_percentage(rating_bonus: float, *,
                              aura_bonus: float = 0.0) -> float:
        """Mirrors: ``Player::UpdateSpellCritChance`` -- 5.0 + auras + rating."""
        return BASE_CRIT_PERCENT + aura_bonus + rating_bonus
