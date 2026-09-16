"""The facade: build a naked character, then fold in supplied contributions.

Nothing is implied.  Talents, buffs, consumables and gear are *inputs*: the
caller passes raw stat and rating amounts (typically the aggregate output of
``gearing.loadout.resolve_loadout``) and this module shows what they become.

Stage order follows ``Player::InitStatsForLevel`` then ``Player::UpdateAllStats``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from . import CharacterSourceError, MissingBaseStats
from .acquisition import AcquisitionEngine, ArmorSpecialization
from .basestats import BaseStatEngine, BaseStats, BaseStatTable
from .derived import (
    BASE_CRIT_PERCENT,
    DerivedStatEngine,
    StatStage,
)
from .identity import (
    MAX_STATS,
    STAT_AGILITY,
    STAT_INTELLECT,
    STAT_NAMES,
    STAT_STAMINA,
    STAT_STRENGTH,
    ClassInfo,
    IdentityStore,
    RaceInfo,
    SpecInfo,
)
from .mastery import MasteryProfile, build_profile
from .primary import ITEM_MOD_TO_STATS, PrimaryStatRouting, routing_for
from gearing.curves import Curves
from gearing.ratings import RatingEngine
from gearing.tables import DEFAULT_TABLES, Tables

#: Rating slots this tool reports.  Mirrors the ``CombatRating`` members that
#: ``Player::UpdateRating`` fans out to something observable.
REPORTED_RATINGS = (
    "CritMelee", "CritRanged", "CritSpell",
    "HasteMelee", "HasteRanged", "HasteSpell",
    "Mastery",
    "VersatilityDamageDone", "VersatilityHealingDone", "VersatilityDamageTaken",
    "Lifesteal", "Avoidance", "Speed",
    "Dodge", "Parry", "Block", "Expertise",
)


@dataclass
class Contributions:
    """Explicit inputs folded on top of the naked character."""

    stats: dict[int, int] = field(default_factory=dict)
    ratings: dict[str, int] = field(default_factory=dict)
    armor: int = 0
    attack_power: int = 0
    spell_power: int = 0
    health: int = 0
    source: str = "caller supplied"

    @classmethod
    def from_item_mods(cls, item_mod_amounts: Mapping[int, int],
                       ratings: Mapping[str, int] | None = None,
                       **kwargs: Any) -> "Contributions":
        """Route ``{ItemModType: amount}`` onto concrete stats.

        Mirrors: the ``HandleStatFlatModifier`` calls in
        ``Player::_ApplyItemBonuses``.  A combined ItemModType contributes its
        full amount to *every* stat it names.
        """
        stats: dict[int, int] = {}
        for item_mod, amount in item_mod_amounts.items():
            for stat in ITEM_MOD_TO_STATS.get(item_mod, ()):
                stats[stat] = stats.get(stat, 0) + amount
        return cls(stats=stats, ratings=dict(ratings or {}), **kwargs)

    def to_dict(self) -> dict[str, Any]:
        return {
            "stats": {STAT_NAMES[k]: v for k, v in sorted(self.stats.items())},
            "ratings": dict(sorted(self.ratings.items())),
            "armor": self.armor,
            "attack_power": self.attack_power,
            "spell_power": self.spell_power,
            "health": self.health,
            "source": self.source,
        }


@dataclass
class ResolvedCharacter:
    race: RaceInfo
    klass: ClassInfo
    spec: SpecInfo | None
    level: int
    base: BaseStats
    routing: PrimaryStatRouting
    contributions: Contributions
    stat_stages: dict[int, StatStage]
    max_health: int
    health_from_stamina: float
    hp_per_sta: float
    armor: int
    bonus_armor: int
    attack_power: int
    attack_power_from_strength: float
    attack_power_from_agility: float
    ranged_attack_power: int
    spell_power: int
    spell_power_from_intellect: int
    rating_conversions: list[dict[str, Any]]
    mastery: MasteryProfile | None
    mastery_value: float
    armor_specializations: list[ArmorSpecialization]
    armor_specialization_applied: ArmorSpecialization | None
    provenance: list[dict[str, Any]]
    warnings: list[str]

    def stat(self, stat: int) -> int:
        return self.stat_stages[stat].rounded()

    def to_dict(self) -> dict[str, Any]:
        return {
            "race": self.race.to_dict(),
            "class": self.klass.to_dict(),
            "spec": self.spec.to_dict() if self.spec else None,
            "level": self.level,
            "base": self.base.to_dict(),
            "primary_stat_routing": self.routing.to_dict(),
            "contributions": self.contributions.to_dict(),
            "stats": {s.name: s.to_dict() for s in self.stat_stages.values()},
            "max_health": self.max_health,
            "health_from_stamina": self.health_from_stamina,
            "hp_per_sta": self.hp_per_sta,
            "armor": self.armor,
            "bonus_armor": self.bonus_armor,
            "attack_power": self.attack_power,
            "attack_power_from_strength": self.attack_power_from_strength,
            "attack_power_from_agility": self.attack_power_from_agility,
            "ranged_attack_power": self.ranged_attack_power,
            "spell_power": self.spell_power,
            "spell_power_from_intellect": self.spell_power_from_intellect,
            "ratings": self.rating_conversions,
            "mastery": self.mastery.to_dict() if self.mastery else None,
            "mastery_value": self.mastery_value,
            "armor_specializations": [a.to_dict()
                                      for a in self.armor_specializations],
            "armor_specialization_applied": (
                self.armor_specialization_applied.to_dict()
                if self.armor_specialization_applied else None),
            "provenance": self.provenance,
            "warnings": self.warnings,
            "caveat": (
                "Aura stages, talents, buffs, consumables and shapeshift forms "
                "are NOT applied.  Supplied contributions are explicit inputs."),
        }


class CharacterResolver:
    def __init__(self, tables: Tables | Path | str = DEFAULT_TABLES,
                 base_stats: BaseStatTable | None = None) -> None:
        self.tables = tables if isinstance(tables, Tables) else Tables(tables)
        self.curves = Curves(self.tables)
        self.identity = IdentityStore(self.tables)
        self.base_stats = BaseStatEngine(self.tables, base_stats)
        self.derived = DerivedStatEngine(self.tables)
        self.ratings = RatingEngine(self.tables, self.curves)
        self.acquisition = AcquisitionEngine(self.tables)

    def mastery_profile(self, spec: SpecInfo) -> MasteryProfile:
        return build_profile(spec, self.acquisition.mastery_spells(spec))

    def resolve(
        self,
        race_id: int,
        class_id: int,
        level: int,
        spec_id: int | None = None,
        contributions: Contributions | None = None,
        *,
        apply_armor_specialization: bool = False,
    ) -> ResolvedCharacter:
        race = self.identity.race(race_id)
        klass = self.identity.klass(class_id)
        spec = self.identity.spec(spec_id) if spec_id is not None else None
        if spec is not None and spec.class_id != class_id:
            raise CharacterSourceError(
                f"specialization {spec_id} ({spec.name}) belongs to class "
                f"{spec.class_id}, not {class_id}")
        contributions = contributions or Contributions(source="none")
        warnings: list[str] = []
        provenance: list[dict[str, Any]] = []

        base = self.base_stats.resolve(class_id, race_id, level)
        provenance.extend(base.provenance)

        routing = routing_for(klass, spec)
        provenance.append({
            "step": "primary-stat-routing",
            "consumer": "Player::GetPrimaryStat + ChrClasses AP coefficients",
            "primary_stat": routing.primary_stat_name,
            "primary_stat_priority": routing.primary_stat_priority,
        })

        # -- armour specialisation (an equipment-gated aura, opt in) -------
        specializations = (self.acquisition.armor_specializations(spec.spec_id)
                           if spec is not None else [])
        applied = None
        if apply_armor_specialization:
            if not specializations:
                warnings.append(
                    "no armour-specialization passive is granted to this spec by "
                    "SpecializationSpells; nothing applied")
            elif len(specializations) > 1:
                warnings.append(
                    f"spec grants {len(specializations)} armour-specialization "
                    f"passives {[s.spell_id for s in specializations]}; "
                    f"refusing to choose")
            else:
                applied = specializations[0]
                provenance.append({
                    "step": "armor-specialization",
                    "consumer": "Player::ApplyItemDependentAuras -> "
                                "HasItemFitToSpellRequirements -> "
                                "SPELL_AURA_MOD_TOTAL_STAT_PERCENTAGE",
                    "spell_id": applied.spell_id,
                    "armor_subclasses": list(applied.armor_subclasses),
                    "stats": list(applied.stat_names),
                    "percent": applied.percent,
                    "note": "assumes every one of the eight required armour "
                            "slots holds a matching item; this tool does not "
                            "check the caller's gear",
                })

        # -- stats ---------------------------------------------------------
        stat_stages: dict[int, StatStage] = {}
        for stat in range(MAX_STATS):
            stage = StatStage(stat=stat, create_value=base.stats[stat])
            stage.total_flat = float(contributions.stats.get(stat, 0))
            if applied is not None and (applied.stat_mask & (1 << stat)):
                stage.total_pct = 1.0 + applied.percent / 100.0
            stat_stages[stat] = stage

        strength = float(stat_stages[STAT_STRENGTH].rounded())
        agility = float(stat_stages[STAT_AGILITY].rounded())
        stamina = float(stat_stages[STAT_STAMINA].rounded())
        intellect = float(stat_stages[STAT_INTELLECT].rounded())

        # -- health --------------------------------------------------------
        max_health, from_stamina = self.derived.max_health(
            stamina, level, create_health=base.create_health,
            base_flat=float(contributions.health))
        provenance.append({
            "step": "max-health",
            "consumer": "Player::UpdateMaxHealth + GetHealthBonusFromStamina",
            "stamina": stamina,
            "hp_per_sta": self.derived.hp_per_sta(level),
            "health_from_stamina": from_stamina,
            "create_health": base.create_health,
            "item_health_base_value": contributions.health,
            "max_health": max_health,
            "rounding": "(uint32) truncating cast",
        })

        # -- armor ---------------------------------------------------------
        armor, bonus_armor = self.derived.armor(
            float(base.base_armor), total_flat=float(contributions.armor))
        provenance.append({
            "step": "armor",
            "consumer": "Player::UpdateArmor",
            "base_value": base.base_armor,
            "item_armor_total_value": contributions.armor,
            "armor": armor, "bonus_armor": bonus_armor,
            "note": "gear armor arrives in TOTAL_VALUE, so it is reported as "
                    "bonus armor and is not scaled by BASE_PCT",
        })

        # -- attack power ---------------------------------------------------
        attack_power, ap_str, ap_agi = self.derived.attack_power(
            klass, strength, agility, level)
        ranged_attack_power, _, _ = self.derived.attack_power(
            klass, strength, agility, level, ranged=True)
        provenance.append({
            "step": "attack-power",
            "consumer": "Player::UpdateAttackPowerAndDamage",
            "attack_power_per_strength": klass.attack_power_per_strength,
            "attack_power_per_agility": klass.attack_power_per_agility,
            "from_strength": ap_str, "from_agility": ap_agi,
            "base_attack_power": attack_power,
            "item_attack_power_total_value": contributions.attack_power,
            "note": "ITEM_MOD_ATTACK_POWER and the weapon's int32(dps*6) land "
                    "in TOTAL_VALUE and are reported separately as "
                    "AttackPowerModPos, not folded into this number",
        })

        # -- spell power ----------------------------------------------------
        spell_power, sp_int = self.derived.spell_power(
            intellect,
            primary_stat_is_intellect=routing.spell_power_from_intellect,
            base_spell_power_bonus=contributions.spell_power)
        provenance.append({
            "step": "spell-power",
            "consumer": "Unit::SpellBaseDamageBonusDone",
            "primary_stat_is_intellect": routing.spell_power_from_intellect,
            "from_intellect": sp_int,
            "item_spell_power": contributions.spell_power,
            "spell_power": spell_power,
        })

        # -- ratings ---------------------------------------------------------
        rating_conversions: list[dict[str, Any]] = []
        for rating in REPORTED_RATINGS:
            amount = contributions.ratings.get(rating, 0)
            conversion = self.ratings.convert(rating, float(amount), level)
            payload = conversion.to_dict()
            if rating.startswith("Crit"):
                payload["effective_percentage_with_base"] = (
                    BASE_CRIT_PERCENT + conversion.final_percent)
                payload["base_percentage_source"] = (
                    "Player::UpdateAllCritPercentages / UpdateSpellCritChance")
            rating_conversions.append(payload)

        # -- mastery ----------------------------------------------------------
        mastery_profile = self.mastery_profile(spec) if spec else None
        mastery_value = 0.0
        if spec is None:
            warnings.append("no specialization supplied: Player::CanUseMastery "
                            "would be false and Mastery stays 0")
        elif mastery_profile and mastery_profile.spells:
            mastery_value = next(
                c["final_percent"] for c in rating_conversions
                if c["rating"] == "Mastery")
            provenance.append({
                "step": "mastery",
                "consumer": "Player::UpdateMastery",
                "mastery_rating": contributions.ratings.get("Mastery", 0),
                "mastery_value": mastery_value,
                "mastery_spell_ids": [s.spell_id for s in mastery_profile.spells],
                "category": mastery_profile.category,
                "note": "ActivePlayerData::Mastery; each mastery effect amount "
                        "is base_points + Mastery * EffectBonusCoefficient",
            })
        else:
            warnings.append(
                "this specialization has no MasterySpellID; CanUseMastery() is "
                "false and Mastery stays 0 regardless of rating")

        return ResolvedCharacter(
            race=race, klass=klass, spec=spec, level=level, base=base,
            routing=routing, contributions=contributions,
            stat_stages=stat_stages, max_health=max_health,
            health_from_stamina=from_stamina,
            hp_per_sta=self.derived.hp_per_sta(level),
            armor=armor, bonus_armor=bonus_armor,
            attack_power=attack_power,
            attack_power_from_strength=ap_str,
            attack_power_from_agility=ap_agi,
            ranged_attack_power=ranged_attack_power,
            spell_power=spell_power, spell_power_from_intellect=sp_int,
            rating_conversions=rating_conversions,
            mastery=mastery_profile, mastery_value=mastery_value,
            armor_specializations=specializations,
            armor_specialization_applied=applied,
            provenance=provenance, warnings=warnings)
