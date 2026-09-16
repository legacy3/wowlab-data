"""Race / class / specialization identity, straight from DB2.

Mirrors: ``ChrRacesEntry``, ``ChrClassesEntry``, ``ChrSpecializationEntry``
(``src/server/game/DataStores/DB2Structure.h``), plus
``DB2Manager::GetChrSpecializationByIndex`` and ``Player::GetPrimaryStat``
(``src/server/game/Entities/Unit/StatSystem.cpp``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterator

from . import CharacterSourceError
from gearing.tables import Tables

# src/server/game/Miscellaneous/SharedDefines.h :: enum Stats
STAT_STRENGTH = 0
STAT_AGILITY = 1
STAT_STAMINA = 2
STAT_INTELLECT = 3
STAT_SPIRIT = 4
MAX_STATS = 5
STAT_NAMES = {STAT_STRENGTH: "Strength", STAT_AGILITY: "Agility",
              STAT_STAMINA: "Stamina", STAT_INTELLECT: "Intellect",
              STAT_SPIRIT: "Spirit"}

# src/server/game/Miscellaneous/SharedDefines.h :: enum Classes
CLASS_NAMES = {
    1: "Warrior", 2: "Paladin", 3: "Hunter", 4: "Rogue", 5: "Priest",
    6: "DeathKnight", 7: "Shaman", 8: "Mage", 9: "Warlock", 10: "Monk",
    11: "Druid", 12: "DemonHunter", 13: "Evoker", 14: "Adventurer",
    15: "Traveler",
}

#: GameTable class-column order, shared by BaseMp.txt and SpellScaling.txt.
#: Mirrors: ``GetGameTableColumnForClass`` (GameTables.h).
GAMETABLE_CLASS_COLUMNS = (
    "Rogue", "Druid", "Hunter", "Mage", "Paladin", "Priest", "Shaman",
    "Warlock", "Warrior", "DeathKnight", "Monk", "DemonHunter", "Evoker",
    "Adventurer", "Traveler",
)
_CLASS_TO_GAMETABLE_COLUMN = {
    1: "Warrior", 2: "Paladin", 3: "Hunter", 4: "Rogue", 5: "Priest",
    6: "DeathKnight", 7: "Shaman", 8: "Mage", 9: "Warlock", 10: "Monk",
    11: "Druid", 12: "DemonHunter", 13: "Evoker", 14: "Adventurer",
    15: "Traveler",
}


def gametable_class_column(class_id: int) -> int:
    """Zero-based column index (after the Level key) for a class."""
    name = _CLASS_TO_GAMETABLE_COLUMN.get(class_id)
    if name is None:
        raise CharacterSourceError(
            f"class {class_id} has no GameTable column; "
            f"GetGameTableColumnForClass returns 0.0f for it")
    return GAMETABLE_CLASS_COLUMNS.index(name)


# ChrSpecialization.Role
ROLE_NAMES = {0: "Tank", 1: "Healer", 2: "Damage"}


@dataclass(frozen=True)
class RaceInfo:
    race_id: int
    name: str
    flags: int
    faction_id: int
    playable_race_bit: int

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


@dataclass(frozen=True)
class ClassInfo:
    class_id: int
    name: str
    primary_stat_priority: int
    attack_power_per_strength: int
    attack_power_per_agility: int
    ranged_attack_power_per_agility: int
    display_power: int
    armor_type_mask: int
    default_spec: int
    has_strength_attack_bonus: int
    damage_bonus_stat: int
    spell_class_set: int
    roles_mask: int

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


@dataclass(frozen=True)
class SpecInfo:
    spec_id: int
    name: str
    class_id: int
    order_index: int
    primary_stat_priority: int
    role: int
    flags: int
    mastery_spell_ids: tuple[int, ...]

    @property
    def role_name(self) -> str:
        return ROLE_NAMES.get(self.role, str(self.role))

    @property
    def is_initial(self) -> bool:
        """The per-class "Initial" pseudo-spec carries no mastery spell."""
        return not any(self.mastery_spell_ids)

    def to_dict(self) -> dict[str, Any]:
        out = dict(self.__dict__)
        out["mastery_spell_ids"] = list(self.mastery_spell_ids)
        out["role_name"] = self.role_name
        return out


def primary_stat_from_priority(priority: int) -> int:
    """Mirrors: ``Player::GetPrimaryStat``.

    ``>= 4`` -> Strength, ``>= 2`` -> Agility, otherwise Intellect.  This is
    the only source-backed rule that picks *one* primary stat, and it is used
    for spell power from intellect and for
    ``SPELL_AURA_MOD_ARMOR_PCT_FROM_STAT`` with miscValue -2.
    """
    if priority >= 4:
        return STAT_STRENGTH
    if priority >= 2:
        return STAT_AGILITY
    return STAT_INTELLECT


class IdentityStore:
    def __init__(self, tables: Tables) -> None:
        self.tables = tables
        self._races = tables("ChrRaces")
        self._classes = tables("ChrClasses")
        self._specs = tables("ChrSpecialization")

    # -- races -----------------------------------------------------------
    def races(self) -> list[RaceInfo]:
        return [RaceInfo(race_id=int(r["ID"]), name=str(r["Name_lang"]),
                         flags=int(r["Flags"]), faction_id=int(r["FactionID"]),
                         playable_race_bit=int(r["PlayableRaceBit"]))
                for r in self._races]

    def race(self, race_id: int) -> RaceInfo:
        for info in self.races():
            if info.race_id == race_id:
                return info
        raise CharacterSourceError(f"ChrRaces has no row {race_id}")

    # -- classes ---------------------------------------------------------
    def classes(self) -> list[ClassInfo]:
        return [ClassInfo(
            class_id=int(r["ID"]),
            name=str(r["Name_lang"]),
            primary_stat_priority=int(r["PrimaryStatPriority"]),
            attack_power_per_strength=int(r["AttackPowerPerStrength"]),
            attack_power_per_agility=int(r["AttackPowerPerAgility"]),
            ranged_attack_power_per_agility=int(r["RangedAttackPowerPerAgility"]),
            display_power=int(r["DisplayPower"]),
            armor_type_mask=int(r["ArmorTypeMask"]),
            default_spec=int(r["DefaultSpec"]),
            has_strength_attack_bonus=int(r["HasStrengthAttackBonus"]),
            damage_bonus_stat=int(r["DamageBonusStat"]),
            spell_class_set=int(r["SpellClassSet"]),
            roles_mask=int(r["RolesMask"]),
        ) for r in self._classes]

    def klass(self, class_id: int) -> ClassInfo:
        for info in self.classes():
            if info.class_id == class_id:
                return info
        raise CharacterSourceError(f"ChrClasses has no row {class_id}")

    # -- specs -----------------------------------------------------------
    def specs(self, class_id: int | None = None,
              include_initial: bool = False) -> list[SpecInfo]:
        out: list[SpecInfo] = []
        for r in self._specs:
            spec = SpecInfo(
                spec_id=int(r["ID"]),
                name=str(r["Name_lang"]),
                class_id=int(r["ClassID"]),
                order_index=int(r["OrderIndex"]),
                primary_stat_priority=int(r["PrimaryStatPriority"]),
                role=int(r["Role"]),
                flags=int(r["Flags"]),
                mastery_spell_ids=(int(r["MasterySpellID_0"]),
                                   int(r["MasterySpellID_1"])),
            )
            if spec.class_id == 0:
                continue                      # pet specialisations
            if class_id is not None and spec.class_id != class_id:
                continue
            if spec.is_initial and not include_initial:
                continue
            out.append(spec)
        out.sort(key=lambda s: (s.class_id, s.order_index, s.spec_id))
        return out

    def spec(self, spec_id: int) -> SpecInfo:
        for info in self.specs(include_initial=True):
            if info.spec_id == spec_id:
                return info
        raise CharacterSourceError(f"ChrSpecialization has no playable row {spec_id}")

    def find_spec(self, class_id: int, name: str) -> SpecInfo:
        wanted = name.strip().lower()
        matches = [s for s in self.specs(class_id, include_initial=True)
                   if s.name.lower() == wanted]
        if not matches:
            known = ", ".join(s.name for s in self.specs(class_id))
            raise CharacterSourceError(
                f"class {class_id} has no specialization named {name!r}; "
                f"known: {known}")
        if len(matches) > 1:
            raise CharacterSourceError(
                f"class {class_id} has {len(matches)} specializations named "
                f"{name!r}: {[s.spec_id for s in matches]}")
        return matches[0]

    def find_class(self, name: str) -> ClassInfo:
        wanted = name.strip().lower().replace(" ", "")
        for info in self.classes():
            if info.name.lower().replace(" ", "") == wanted:
                return info
        raise CharacterSourceError(f"ChrClasses has no class named {name!r}")

    def find_race(self, name: str) -> RaceInfo:
        wanted = name.strip().lower()
        matches = [r for r in self.races() if r.name.lower() == wanted]
        if not matches:
            raise CharacterSourceError(f"ChrRaces has no race named {name!r}")
        if len(matches) > 1:
            raise CharacterSourceError(
                f"{len(matches)} races are named {name!r}: "
                f"{[r.race_id for r in matches]}; pass --race-id")
        return matches[0]
