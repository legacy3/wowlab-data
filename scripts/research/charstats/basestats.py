"""Base character stats, and the fail-closed boundary around them.

Mirrors: ``Player::InitStatsForLevel``, ``ObjectMgr::LoadPlayerLevelInfo``,
``ObjectMgr::GetPlayerLevelInfo``, ``ObjectMgr::GetPlayerClassLevelInfo``
(``src/server/game/Globals/ObjectMgr.cpp``, ``Entities/Player/Player.cpp``).

The important finding, and the reason this module exists separately:

    levelInfo.stats[i] = player_classlevelstats[class][level].stat[i]
                       + player_racestats[race].StatModifier[i]

Both tables live in TrinityCore's **world database**.  They are not DB2, they
are not in the checked-in snapshot, and the TrinityCore repository ships only
their ``CREATE TABLE`` statements (``sql/base/dev/world_database.sql``) with no
rows.  So base primary stats are **not derivable here**, and this module raises
:class:`~charstats.MissingBaseStats` unless the caller supplies them.

Base mana *is* derivable: ``GetPlayerClassLevelInfo`` reads ``BaseMp.txt``
through ``GetGameTableColumnForClass``.

Base health is 0: ``InitStatsForLevel`` calls ``SetCreateHealth(0)``, so a
Player's entire max health comes from stamina and from aura/item modifiers.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import CharacterSourceError, MissingBaseStats
from .identity import (
    MAX_STATS,
    STAT_AGILITY,
    STAT_INTELLECT,
    STAT_NAMES,
    STAT_SPIRIT,
    STAT_STAMINA,
    STAT_STRENGTH,
    gametable_class_column,
)
from gearing.tables import Tables

#: Order of the stat columns in both world-database tables.
WORLD_DB_STAT_ORDER = (STAT_STRENGTH, STAT_AGILITY, STAT_STAMINA,
                       STAT_INTELLECT, STAT_SPIRIT)

BASE_STATS_SCHEMA_HINT = """\
Supply a JSON file shaped like:

{
  "source": "TDB world database, player_classlevelstats + player_racestats",
  "class_level_stats": {"<class>": {"<level>": [str, agi, sta, inte, spi]}},
  "race_stat_modifiers": {"<race>": [str, agi, sta, inte, spi]}
}

race_stat_modifiers is optional and defaults to zeroes; TrinityCore adds it to
the class/level row per ObjectMgr::LoadPlayerLevelInfo."""


@dataclass
class BaseStatTable:
    """Caller-supplied replacement for the two world-database tables."""

    class_level_stats: dict[int, dict[int, tuple[int, ...]]]
    race_stat_modifiers: dict[int, tuple[int, ...]] = field(default_factory=dict)
    source: str = "caller supplied"

    @classmethod
    def from_json_file(cls, path: Path | str) -> "BaseStatTable":
        text = Path(path).read_text(encoding="utf-8")
        try:
            raw = json.loads(text)
        except json.JSONDecodeError as error:
            raise CharacterSourceError(
                f"{path} is not valid JSON: {error}\n{BASE_STATS_SCHEMA_HINT}") from None
        if "class_level_stats" not in raw:
            raise CharacterSourceError(
                f"{path} has no 'class_level_stats'\n{BASE_STATS_SCHEMA_HINT}")

        def stats(values: Any, where: str) -> tuple[int, ...]:
            if not isinstance(values, (list, tuple)) or len(values) != MAX_STATS:
                raise CharacterSourceError(
                    f"{where} must be {MAX_STATS} integers "
                    f"(str, agi, sta, inte, spi), got {values!r}")
            return tuple(int(v) for v in values)

        class_level = {
            int(class_id): {int(level): stats(v, f"class {class_id} level {level}")
                            for level, v in levels.items()}
            for class_id, levels in raw["class_level_stats"].items()}
        race_mods = {
            int(race_id): stats(v, f"race {race_id}")
            for race_id, v in raw.get("race_stat_modifiers", {}).items()}
        return cls(class_level_stats=class_level, race_stat_modifiers=race_mods,
                   source=str(raw.get("source", str(path))))

    def lookup(self, class_id: int, level: int, race_id: int) -> tuple[int, ...]:
        levels = self.class_level_stats.get(class_id)
        if not levels:
            raise MissingBaseStats(
                f"no class_level_stats for class {class_id}")
        row = levels.get(level)
        if row is None:
            raise MissingBaseStats(
                f"no class_level_stats for class {class_id} level {level}; "
                f"known levels: {sorted(levels)[:5]}...")
        modifier = self.race_stat_modifiers.get(race_id, (0,) * MAX_STATS)
        return tuple(row[i] + modifier[i] for i in range(MAX_STATS))


@dataclass
class BaseStats:
    """Everything ``InitStatsForLevel`` establishes before any modifier."""

    class_id: int
    race_id: int
    level: int
    stats: tuple[int, ...]
    stats_source: str
    create_health: int
    create_mana: int
    base_armor: int
    provenance: list[dict[str, Any]]

    def stat(self, stat: int) -> int:
        return self.stats[stat]

    def to_dict(self) -> dict[str, Any]:
        return {
            "class_id": self.class_id,
            "race_id": self.race_id,
            "level": self.level,
            "stats": {STAT_NAMES[i]: self.stats[i] for i in range(MAX_STATS)},
            "stats_source": self.stats_source,
            "create_health": self.create_health,
            "create_mana": self.create_mana,
            "base_armor": self.base_armor,
            "provenance": self.provenance,
        }


class BaseStatEngine:
    def __init__(self, tables: Tables,
                 base_stat_table: BaseStatTable | None = None) -> None:
        self.tables = tables
        self.base_stat_table = base_stat_table
        self._gt_base_mp = tables.gametable("BaseMp")

    def base_mana(self, class_id: int, level: int) -> int:
        """Mirrors: ``ObjectMgr::GetPlayerClassLevelInfo``.

        ``uint32(GetGameTableColumnForClass(sBaseMPGameTable.GetRow(level)))``.
        A class with no mana has a zero column, which is a real zero and not a
        missing row.
        """
        column = gametable_class_column(class_id)
        value = self._gt_base_mp.column(level, column)
        if value is None:
            raise CharacterSourceError(
                f"BaseMp.txt has no row for level {level}")
        return int(value)

    def resolve(self, class_id: int, race_id: int, level: int) -> BaseStats:
        """Mirrors: the base-value half of ``Player::InitStatsForLevel``."""
        provenance: list[dict[str, Any]] = []

        if self.base_stat_table is None:
            raise MissingBaseStats(
                "base primary stats come from the world-database tables "
                "player_classlevelstats and player_racestats, which are not in "
                "the checked-in snapshot and ship only as schema in "
                "TrinityCore.  Pass --base-stats FILE.\n"
                + BASE_STATS_SCHEMA_HINT)

        stats = self.base_stat_table.lookup(class_id, level, race_id)
        provenance.append({
            "step": "base-primary-stats",
            "source": self.base_stat_table.source,
            "consumer": "ObjectMgr::LoadPlayerLevelInfo -> "
                        "player_classlevelstats[class][level] + "
                        "player_racestats[race]",
            "stats": {STAT_NAMES[i]: stats[i] for i in range(MAX_STATS)},
        })

        mana = self.base_mana(class_id, level)
        provenance.append({
            "step": "create-mana", "value": mana,
            "source": "BaseMp.txt",
            "consumer": "ObjectMgr::GetPlayerClassLevelInfo"})

        # InitStatsForLevel: SetCreateHealth(0)
        provenance.append({
            "step": "create-health", "value": 0,
            "consumer": "Player::InitStatsForLevel -> SetCreateHealth(0)",
            "note": "a Player has no base health; all max health comes from "
                    "stamina and from item/aura modifiers"})

        # InitStatsForLevel: SetArmor(int32(m_createStats[STAT_AGILITY] * 2), 0)
        base_armor = int(stats[STAT_AGILITY]) * 2
        provenance.append({
            "step": "base-armor", "value": base_armor,
            "consumer": "Player::InitStatsForLevel -> "
                        "SetArmor(int32(createStats[AGILITY] * 2), 0)",
            "note": "this is the create-time value; UpdateArmor recomputes "
                    "armor from UNIT_MOD_ARMOR and does not re-add it"})

        return BaseStats(class_id=class_id, race_id=race_id, level=level,
                         stats=stats, stats_source=self.base_stat_table.source,
                         create_health=0, create_mana=mana,
                         base_armor=base_armor, provenance=provenance)
