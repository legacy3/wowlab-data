"""How a race/class/spec identity acquires its baseline spells.

Mirrors:
    Player::LearnSpecializationSpells   (Player.cpp)  -> SpecializationSpells
    Player::UpdateMastery / CanUseMastery             -> ChrSpecialization.MasterySpellID
    Player::ApplyItemDependentAuras                   -> equipment-gated passives
    Player::HasItemFitToSpellRequirements             -> SpellEquippedItems
    Player::LearnDefaultSkills / LearnSkillRewardedSpells
                                                      -> SkillLineAbility + SkillRaceClassInfo
    Player::LearnCustomSpells                         -> playercreateinfo_spell_custom (world DB)

Acquisition is kept strictly separate from executability: reaching a SpellID
here says nothing about whether that spell does anything.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from . import CharacterSourceError
from .identity import SpecInfo
from gearing.tables import Tables

#: ``SpellMisc.Attributes_8`` bit -- Mirrors: ``SPELL_ATTR8_REQUIRES_EQUIPPED_INV_TYPES``.
SPELL_ATTR8_REQUIRES_EQUIPPED_INV_TYPES = 0x00100000
#: ``SpellMisc.Attributes_8`` bit -- Mirrors: ``SPELL_ATTR8_MASTERY_AFFECTS_POINTS``.
SPELL_ATTR8_MASTERY_AFFECTS_POINTS = 0x20000000

#: SpellEffect.Effect 6 = SPELL_EFFECT_APPLY_AURA.
SPELL_EFFECT_APPLY_AURA = 6
#: SpellAuraDefines: 137 = SPELL_AURA_MOD_TOTAL_STAT_PERCENTAGE.
SPELL_AURA_MOD_TOTAL_STAT_PERCENTAGE = 137
#: SpellAuraDefines: 29 = SPELL_AURA_MOD_STAT.
SPELL_AURA_MOD_STAT = 29
#: SpellAuraDefines: 262 = SPELL_AURA_MASTERY.
SPELL_AURA_MASTERY = 262

#: The eight armour slots ``HasItemFitToSpellRequirements`` demands when
#: ``SPELL_ATTR8_REQUIRES_EQUIPPED_INV_TYPES`` is set.  Mirrors the literal
#: initialiser list in the consumer.
ARMOR_SPECIALIZATION_SLOTS = ("HEAD", "SHOULDERS", "CHEST", "WAIST", "LEGS",
                              "FEET", "WRISTS", "HANDS")

#: ``ItemSubClassArmor`` bits used by SpellEquippedItems.EquippedItemSubclass.
ARMOR_SUBCLASS_BITS = {1: "Cloth", 2: "Leather", 3: "Mail", 4: "Plate"}


@dataclass(frozen=True)
class SpecSpell:
    spec_id: int
    spell_id: int
    overrides_spell_id: int
    display_order: int
    spell_level: int | None

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


@dataclass(frozen=True)
class ArmorSpecialization:
    spell_id: int
    spec_id: int
    armor_subclass_mask: int
    armor_subclasses: tuple[str, ...]
    required_inventory_types: tuple[int, ...]
    requires_all_armor_slots: bool
    stat_mask: int
    stat_names: tuple[str, ...]
    percent: float

    def to_dict(self) -> dict[str, Any]:
        out = dict(self.__dict__)
        out["armor_subclasses"] = list(self.armor_subclasses)
        out["required_inventory_types"] = list(self.required_inventory_types)
        out["stat_names"] = list(self.stat_names)
        return out


@dataclass(frozen=True)
class RacialAbility:
    race_id: int
    spell_id: int
    skill_line: int
    class_mask: int
    acquire_method: int

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


class AcquisitionEngine:
    def __init__(self, tables: Tables) -> None:
        self.tables = tables
        self._spec_spells = tables("SpecializationSpells").group("SpecID")
        self._spell_misc = tables("SpellMisc").by("SpellID")
        self._spell_effect = tables("SpellEffect").group("SpellID")
        self._spell_equipped = tables("SpellEquippedItems").by("SpellID")
        self._spell_name = tables("SpellName").by("ID")
        self._spell_levels = tables.optional("SpellLevels")
        self._skill_line_ability = tables("SkillLineAbility")
        self._skill_race_class = tables("SkillRaceClassInfo")

    # -- spec passives ---------------------------------------------------
    def spell_level(self, spell_id: int) -> int | None:
        """``SpellInfo::SpellLevel`` -- from ``SpellLevels``, difficulty 0."""
        if self._spell_levels is None:
            return None
        for row in self._spell_levels.group("SpellID").get(spell_id, ()):
            if int(row["DifficultyID"]) == 0:
                return int(row["SpellLevel"])
        return None

    def spec_spells(self, spec_id: int, level: int | None = None
                    ) -> list[SpecSpell]:
        """Mirrors: ``Player::LearnSpecializationSpells``.

        Every ``SpecializationSpells`` row for the spec whose
        ``SpellInfo::SpellLevel`` is not above the player's level is learned,
        and ``OverridesSpellID`` registers a spell override.
        """
        out: list[SpecSpell] = []
        for row in self._spec_spells.get(spec_id, ()):
            spell_id = int(row["SpellID"])
            spell_level = self.spell_level(spell_id)
            if level is not None and spell_level is not None and spell_level > level:
                continue
            out.append(SpecSpell(
                spec_id=spec_id, spell_id=spell_id,
                overrides_spell_id=int(row["OverridesSpellID"]),
                display_order=int(row["DisplayOrder"]),
                spell_level=spell_level))
        out.sort(key=lambda s: (s.display_order, s.spell_id))
        return out

    def spell_name(self, spell_id: int) -> str:
        row = self._spell_name.get(spell_id)
        return str(row["Name_lang"]) if row else ""

    # -- mastery ---------------------------------------------------------
    def mastery_spells(self, spec: SpecInfo) -> list[dict[str, Any]]:
        """``ChrSpecialization.MasterySpellID1/2``, with their effect shape.

        ``Player::CanUseMastery()`` is ``HasSpell(MasterySpellID[0]) ||
        HasSpell(MasterySpellID[1])``, so without one of these known the whole
        mastery rating produces nothing.
        """
        out: list[dict[str, Any]] = []
        for spell_id in spec.mastery_spell_ids:
            if not spell_id:
                continue
            misc = self._spell_misc.get(spell_id)
            attributes8 = int(misc["Attributes_8"]) if misc else 0
            effects = []
            for row in self._spell_effect.get(spell_id, ()):
                effects.append({
                    "effect_index": int(row["EffectIndex"]),
                    "effect": int(row["Effect"]),
                    "effect_aura": int(row["EffectAura"]),
                    "base_points": float(row["EffectBasePointsF"]),
                    "bonus_coefficient": float(row["EffectBonusCoefficient"]),
                    "misc_value_0": int(row["EffectMiscValue_0"]),
                    "misc_value_1": int(row["EffectMiscValue_1"]),
                })
            out.append({
                "spell_id": spell_id,
                "name": self.spell_name(spell_id),
                "attributes_8": attributes8,
                "mastery_affects_points": bool(
                    attributes8 & SPELL_ATTR8_MASTERY_AFFECTS_POINTS),
                "effects": effects,
            })
        return out

    # -- armour specialisation -------------------------------------------
    def armor_specializations(self, spec_id: int | None = None
                              ) -> list[ArmorSpecialization]:
        """Find the equipment-gated primary-stat passives, from source only.

        Discovery rule, entirely source-backed:

        * a ``SpecializationSpells`` row for the spec grants the spell;
        * ``SpellMisc.Attributes_8`` has ``SPELL_ATTR8_REQUIRES_EQUIPPED_INV_TYPES``,
          which is what makes ``HasItemFitToSpellRequirements`` demand every
          armour slot;
        * ``SpellEquippedItems.EquippedItemClass == 4`` (armour) with a
          subclass mask;
        * the spell applies ``SPELL_AURA_MOD_TOTAL_STAT_PERCENTAGE``, whose
          ``EffectMiscValue_1`` is a ``Stats`` bitmask and whose
          ``EffectBasePointsF`` is the percentage.

        No spell id, class, armour type or percentage is hardcoded.
        """
        out: list[ArmorSpecialization] = []
        spec_ids = ([spec_id] if spec_id is not None
                    else sorted({int(r["SpecID"])
                                 for r in self.tables("SpecializationSpells")}))
        effects_by_spell = self.tables("SpellEffect").group("SpellID")
        for sid in spec_ids:
            for row in self._spec_spells.get(sid, ()):
                spell_id = int(row["SpellID"])
                misc = self._spell_misc.get(spell_id)
                if not misc:
                    continue
                if not (int(misc["Attributes_8"])
                        & SPELL_ATTR8_REQUIRES_EQUIPPED_INV_TYPES):
                    continue
                equipped = self._spell_equipped.get(spell_id)
                if not equipped or int(equipped["EquippedItemClass"]) != 4:
                    continue
                stat_mask = 0
                percent = 0.0
                for effect in effects_by_spell.get(spell_id, ()):
                    if int(effect["Effect"]) != SPELL_EFFECT_APPLY_AURA:
                        continue
                    if int(effect["EffectAura"]) != SPELL_AURA_MOD_TOTAL_STAT_PERCENTAGE:
                        continue
                    amount = float(effect["EffectBasePointsF"])
                    if amount <= 0.0:
                        continue
                    stat_mask |= int(effect["EffectMiscValue_1"])
                    percent = max(percent, amount)
                if not stat_mask:
                    continue
                subclass_mask = int(equipped["EquippedItemSubclass"])
                inv_mask = int(equipped["EquippedItemInvTypes"])
                out.append(ArmorSpecialization(
                    spell_id=spell_id,
                    spec_id=sid,
                    armor_subclass_mask=subclass_mask,
                    armor_subclasses=tuple(
                        name for bit, name in ARMOR_SUBCLASS_BITS.items()
                        if subclass_mask & (1 << bit)),
                    required_inventory_types=tuple(
                        i for i in range(32) if inv_mask & (1 << i)),
                    requires_all_armor_slots=True,
                    stat_mask=stat_mask,
                    stat_names=tuple(
                        STAT_BIT_NAMES[bit] for bit in range(MAX_STAT_BITS)
                        if stat_mask & (1 << bit)),
                    percent=percent,
                ))
        out.sort(key=lambda a: (a.spec_id, a.spell_id))
        return out

    # -- racials ----------------------------------------------------------
    def racial_abilities(self, race_id: int, class_id: int | None = None
                         ) -> list[RacialAbility]:
        """Mirrors: ``Player::IsSpellFitByClassAndRace`` + ``LearnDefaultSkills``.

        A racial reaches a player through ``SkillLineAbility`` with a race mask,
        gated on ``SkillRaceClassInfo(SkillLine, race, class)`` existing.  The
        ``playercreateinfo_spell_custom`` path in ``LearnCustomSpells`` is a
        *world database* table and is not modelled.
        """
        if race_id < 1:
            raise CharacterSourceError(f"invalid race id {race_id}")
        bit = race_id - 1
        block, offset = divmod(bit, 32)
        if block > 1:
            raise CharacterSourceError(
                f"race {race_id} needs RaceMasks block {block}; "
                f"SkillLineAbility only has two")
        column = f"RaceMasks_{block}"
        class_mask = (1 << (class_id - 1)) if class_id else 0

        allowed_skills = set()
        for row in self._skill_race_class:
            rmask = int(row[f"RaceMasks_{block}"])
            if not (rmask & (1 << offset)):
                continue
            cmask = int(row["ClassMask"])
            if class_mask and cmask and not (cmask & class_mask):
                continue
            allowed_skills.add(int(row["SkillID"]))

        out: list[RacialAbility] = []
        for row in self._skill_line_ability:
            rmask = int(row[column])
            if not rmask or not (rmask & (1 << offset)):
                continue
            cmask = int(row["ClassMask"])
            if class_mask and cmask and not (cmask & class_mask):
                continue
            skill = int(row["SkillLine"])
            if skill not in allowed_skills:
                continue
            out.append(RacialAbility(
                race_id=race_id, spell_id=int(row["Spell"]),
                skill_line=skill, class_mask=cmask,
                acquire_method=int(row["AcquireMethod"])))
        out.sort(key=lambda r: r.spell_id)
        return out


#: Stats bitmask used by SPELL_AURA_MOD_TOTAL_STAT_PERCENTAGE's MiscValueB.
STAT_BIT_NAMES = {0: "Strength", 1: "Agility", 2: "Stamina", 3: "Intellect",
                  4: "Spirit"}
MAX_STAT_BITS = 5
