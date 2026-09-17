"""Adapter: the extracted TDB base-stat corpus -> ``charstats.basestats.BaseStatTable``.

Source corpus: ``docs/research/world-db-corpora/player-base-stats.json``
(``tools/tdb_world_extract.py`` over the pinned TDB + ``sql/updates/world/master``).
Rows are Trinity's world-database authoring.  They are ``world-db-fact``: the
best available consumer input, not Retail truth.

Two things live here, deliberately separated:

1. :func:`load` / :class:`TdbBaseStats` -- a faithful, gap-preserving view of
   the rows.  A (class, level) that has no row stays absent and
   ``BaseStatTable.lookup`` raises ``MissingBaseStats`` for it.

2. :meth:`TdbBaseStats.table` with ``fill_rule="trinity"`` -- an *explicit
   opt-in* reproduction of what ``ObjectMgr::LoadPlayerInfo`` does with the
   gaps at server start.  Every filled cell is tagged
   ``evidence_class = "trinity-consumer(fill-rule)"``; nothing is filled
   silently and nothing is extrapolated.

The Trinity rule (TrinityCore 7f3d43b, src/server/game/Globals/ObjectMgr.cpp):

* 4309-4317: a ``player_classlevelstats`` row whose ``level`` exceeds
  ``CONFIG_MAX_PLAYER_LEVEL`` is *ignored* (logged, not loaded).
* 4319-4330: ``levelInfo`` is an array of exactly ``CONFIG_MAX_PLAYER_LEVEL``
  ``PlayerLevelInfo`` (``int32 stats[MAX_STATS] = { }``, ObjectMgr.h:628-631,
  i.e. zero-initialised); the row's stats plus ``player_racestats`` modifier
  are stored at ``levelInfo[level - 1]`` for every (race, class) pair that
  ``playercreateinfo`` created.
* 4336-4356 "Fill gaps and check integrity": level 1 missing (``levelInfo``
  null or ``levelInfo[0].stats[0] == 0``) is ``ABORT()``; then for
  ``level = 1 .. CONFIG_MAX_PLAYER_LEVEL - 1`` (0-based index), a cell whose
  ``stats[0]`` (**strength**, after the race modifier) is 0 is overwritten with
  the previous cell and ``TC_LOG_ERROR("sql.sql", "Race {} Class {} Level {}
  does not have stats data. Using stats data of level {}.")`` is emitted.
* 4437-4450 ``GetPlayerLevelInfo``: ``level <= CONFIG_MAX_PLAYER_LEVEL`` reads
  ``levelInfo[level - 1]``; only a level *above* the config maximum reaches
  ``BuildPlayerLevelInfo`` (4452-4530, the Classic-era per-class switch).

``CONFIG_MAX_PLAYER_LEVEL`` defaults to ``GetMaxLevelForExpansion(CURRENT_EXPANSION)``
(World.cpp:750) = 90 for ``EXPANSION_MIDNIGHT`` (SharedDefines.h:109-137) and
``worldserver.conf.dist:912`` ships ``MaxPlayerLevel = 90``.  So with the pinned
TDB (rows only up to level 80) a level-90 character receives the level-80 row
copied forward through 81..90 -- ten copies, ten error lines per (race, class).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import SourceError, TRINITY_COMMIT, WORLD_DB_CORPORA
from charstats.basestats import BaseStatTable
from charstats.identity import MAX_STATS, STAT_NAMES

CORPUS_PATH = WORLD_DB_CORPORA / "player-base-stats.json"

FILL_RULE_NONE = "none"
FILL_RULE_TRINITY = "trinity"
FILL_RULES = (FILL_RULE_NONE, FILL_RULE_TRINITY)

#: worldserver.conf.dist:912 ``MaxPlayerLevel = 90``; World.cpp:750 default
#: ``GetMaxLevelForExpansion(CURRENT_EXPANSION)``; SharedDefines.h:109-137.
TRINITY_DEFAULT_MAX_PLAYER_LEVEL = 90

EVIDENCE_ROW = "world-db-fact"
EVIDENCE_FILLED = "trinity-consumer(fill-rule)"

FILL_RULE_COORDINATES = {
    "ignore_rows_above_max": "src/server/game/Globals/ObjectMgr.cpp:4309-4317",
    "level_info_array": "src/server/game/Globals/ObjectMgr.cpp:4319-4330",
    "level_info_struct": "src/server/game/Globals/ObjectMgr.h:628-631",
    "fill_gaps": "src/server/game/Globals/ObjectMgr.cpp:4336-4356",
    "get_player_level_info": "src/server/game/Globals/ObjectMgr.cpp:4437-4450",
    "build_player_level_info": "src/server/game/Globals/ObjectMgr.cpp:4452-4530",
    "max_player_level_default": "src/server/game/World/World.cpp:750",
    "max_player_level_conf": "src/server/worldserver/worldserver.conf.dist:912",
    "get_max_level_for_expansion": "src/server/game/Miscellaneous/SharedDefines.h:109-137",
}

TC_LOG_ERROR_FILL = ("Race {race} Class {class_} Level {level} does not have stats "
                     "data. Using stats data of level {source_level}.")


@dataclass(frozen=True)
class FilledCell:
    """One (class, level) cell that Trinity's gap fill would overwrite."""

    class_id: int
    level: int
    source_level: int
    stats: tuple[int, ...]
    evidence_class: str = EVIDENCE_FILLED
    coordinate: str = FILL_RULE_COORDINATES["fill_gaps"]

    def to_dict(self) -> dict[str, Any]:
        return {
            "class_id": self.class_id, "level": self.level,
            "source_level": self.source_level,
            "stats": {STAT_NAMES[i]: self.stats[i] for i in range(MAX_STATS)},
            "evidence_class": self.evidence_class,
            "coordinate": self.coordinate,
            "trinity_log": TC_LOG_ERROR_FILL.format(
                race="<race>", class_=self.class_id, level=self.level,
                source_level=self.source_level),
        }


class TaggedBaseStatTable(BaseStatTable):
    """``BaseStatTable`` that knows which of its cells are rows and which are fills."""

    def __init__(self, *args: Any, filled: dict[tuple[int, int], FilledCell] | None = None,
                 fill_rule: str = FILL_RULE_NONE, max_player_level: int | None = None,
                 **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.filled = dict(filled or {})
        self.fill_rule = fill_rule
        self.max_player_level = max_player_level

    def evidence(self, class_id: int, level: int) -> str:
        return EVIDENCE_FILLED if (class_id, level) in self.filled else EVIDENCE_ROW

    def describe(self, class_id: int, race_id: int, level: int) -> dict[str, Any]:
        """Lookup plus provenance; raises ``MissingBaseStats`` like the parent."""
        stats = self.lookup(class_id, level, race_id)
        cell = self.filled.get((class_id, level))
        out: dict[str, Any] = {
            "class_id": class_id, "race_id": race_id, "level": level,
            "stats": {STAT_NAMES[i]: stats[i] for i in range(MAX_STATS)},
            "class_row": {STAT_NAMES[i]: self.class_level_stats[class_id][level][i]
                          for i in range(MAX_STATS)},
            "race_modifier": {STAT_NAMES[i]: self.race_stat_modifiers.get(race_id, (0,) * MAX_STATS)[i]
                              for i in range(MAX_STATS)},
            "race_row_present": race_id in self.race_stat_modifiers,
            "evidence_class": self.evidence(class_id, level),
            "fill_rule": self.fill_rule,
            "source": self.source,
        }
        if cell is not None:
            out["filled_from"] = cell.to_dict()
            out["retail_truth"] = "unresolved"
        return out


@dataclass
class TdbBaseStats:
    """Faithful view of the extracted corpus."""

    provenance: dict[str, Any]
    class_level: dict[int, dict[int, tuple[int, ...]]]
    verified_build: dict[tuple[int, int], int]
    race_mods: dict[int, tuple[int, ...]]
    path: Path
    schemas: dict[str, Any] = field(default_factory=dict)

    # -- inspection ------------------------------------------------------
    def classes(self) -> list[int]:
        return sorted(self.class_level)

    def races(self) -> list[int]:
        return sorted(self.race_mods)

    def levels(self, class_id: int) -> list[int]:
        return sorted(self.class_level.get(class_id, {}))

    def max_level(self) -> int:
        return max((max(levels) for levels in self.class_level.values() if levels), default=0)

    def coverage(self, max_player_level: int = TRINITY_DEFAULT_MAX_PLAYER_LEVEL) -> dict[str, Any]:
        """Per-class level coverage against Trinity's array of ``max_player_level`` cells."""
        per_class: dict[str, Any] = {}
        for class_id in self.classes():
            levels = self.levels(class_id)
            present = set(levels)
            gaps_within = [l for l in range(1, max(levels) + 1) if l not in present]
            gaps_above = [l for l in range(max(levels) + 1, max_player_level + 1)]
            ignored_above_config = [l for l in levels if l > max_player_level]
            per_class[str(class_id)] = {
                "rows": len(levels),
                "min_level": min(levels),
                "max_level": max(levels),
                "gaps_within_rows": gaps_within,
                "gaps_above_rows_to_config_max": gaps_above,
                "rows_ignored_above_config_max": ignored_above_config,
                "level_1_present": 1 in present,
                "zero_strength_rows": [l for l in levels if self.class_level[class_id][l][0] == 0],
                "verified_build_values": sorted({self.verified_build[(class_id, l)] for l in levels}),
            }
        return {
            "config_max_player_level": max_player_level,
            "classes_present": self.classes(),
            "class_count": len(self.classes()),
            "row_count": sum(len(v) for v in self.class_level.values()),
            "per_class": per_class,
            "races_present": self.races(),
            "race_count": len(self.races()),
        }

    def monotonicity(self) -> dict[str, Any]:
        """Where a stat column decreases with level (authoring anomalies, reported not fixed)."""
        out: dict[str, list[dict[str, int]]] = {}
        for class_id in self.classes():
            levels = self.levels(class_id)
            for stat in range(MAX_STATS):
                for a, b in zip(levels, levels[1:]):
                    va = self.class_level[class_id][a][stat]
                    vb = self.class_level[class_id][b][stat]
                    if vb < va:
                        out.setdefault(STAT_NAMES[stat], []).append(
                            {"class_id": class_id, "level": a, "value": va,
                             "next_level": b, "next_value": vb})
        return out

    # -- adapter ---------------------------------------------------------
    def source_string(self, fill_rule: str, max_player_level: int) -> str:
        p = self.provenance
        rows = sum(len(v) for v in self.class_level.values())
        return (f"TDB world database {p.get('base_world_database')} "
                f"(sha256 {str(p.get('base_world_database_sha256', ''))[:12]}...), "
                f"updates {p.get('update_directory')} "
                f"{p.get('update_files_touching_tracked_tables')}; "
                f"TrinityCore {p.get('trinitycore_commit', TRINITY_COMMIT)}; "
                f"player_classlevelstats {rows} rows (levels {min(min(v) for v in self.class_level.values())}"
                f"..{self.max_level()}, VerifiedBuild {sorted(set(self.verified_build.values()))}), "
                f"player_racestats {len(self.race_mods)} rows; "
                f"fill_rule={fill_rule}; config_max_player_level={max_player_level}; "
                f"evidence: world-db-fact (rows) / trinity-consumer(fill-rule) (filled cells)")

    def zero_strength_crossings(self, max_player_level: int,
                                pairs: set[tuple[int, int]] | None = None
                                ) -> list[dict[str, int]]:
        """(race, class, level) cells whose race-modified strength would be 0.

        Trinity's gap test is ``levelInfo[level].stats[0] == 0`` on the
        race-modified value (ObjectMgr.cpp:4328, 4342, 4351), so such a cell
        would be treated as *missing* even though a row exists (and at level 1
        it would ``ABORT()`` the server).  ``pairs`` is the set of (race, class)
        that ``playercreateinfo`` creates (only those cells exist,
        ObjectMgr.cpp:4323); with ``None`` every race in ``player_racestats``
        is checked against every class, which is the conservative reading.
        """
        crossings = []
        for class_id, levels in self.class_level.items():
            for level, row in levels.items():
                if level > max_player_level:
                    continue
                for race_id, mods in self.race_mods.items():
                    if pairs is not None and (race_id, class_id) not in pairs:
                        continue
                    if row[0] + mods[0] == 0:
                        crossings.append({"race_id": race_id, "class_id": class_id, "level": level})
        return sorted(crossings, key=lambda c: (c["class_id"], c["level"], c["race_id"]))

    def table(self, fill_rule: str = FILL_RULE_NONE,
              max_player_level: int = TRINITY_DEFAULT_MAX_PLAYER_LEVEL,
              pairs: set[tuple[int, int]] | None = None
              ) -> TaggedBaseStatTable:
        """``pairs``: the (race, class) set from ``playercreateinfo`` (see
        :func:`playercreateinfo_pairs`); needed for the fill rule because the
        strength-zero sentinel is evaluated on race-modified values."""
        if fill_rule not in FILL_RULES:
            raise SourceError(f"unknown fill rule {fill_rule!r}; choose one of {FILL_RULES}")
        if fill_rule == FILL_RULE_NONE:
            return TaggedBaseStatTable(
                class_level_stats={c: dict(v) for c, v in self.class_level.items()},
                race_stat_modifiers=dict(self.race_mods),
                source=self.source_string(fill_rule, max_player_level),
                filled={}, fill_rule=fill_rule, max_player_level=max_player_level)

        crossings = self.zero_strength_crossings(max_player_level, pairs)
        if crossings:
            raise SourceError(
                "refusing to apply the Trinity fill rule: race-modified strength is 0 for "
                f"{len(crossings)} (race, class, level) cells, so Trinity would treat those "
                f"rows as gaps per race ({crossings[:5]}...); a (class, level) table cannot "
                "express that.  Pass pairs= (the playercreateinfo race/class set) if these "
                "pairs are not actually created by the pinned TDB.")

        filled: dict[tuple[int, int], FilledCell] = {}
        table: dict[int, dict[int, tuple[int, ...]]] = {}
        for class_id, levels in self.class_level.items():
            # ObjectMgr.cpp:4319-4330 -- array of max_player_level zero cells,
            # rows above the config max ignored (4309-4317).
            cells: list[tuple[int, ...] | None] = [None] * max_player_level
            for level, row in levels.items():
                if 1 <= level <= max_player_level:
                    cells[level - 1] = tuple(row)
            # ObjectMgr.cpp:4342-4346 -- level 1 missing is ABORT().
            if cells[0] is None or cells[0][0] == 0:
                raise SourceError(
                    f"class {class_id}: level 1 has no stats row; Trinity ABORT()s at "
                    f"{FILL_RULE_COORDINATES['fill_gaps']} ('Level 1 does not have stats data!')")
            # ObjectMgr.cpp:4348-4356 -- copy the previous cell forward.
            for index in range(1, max_player_level):
                cell = cells[index]
                if cell is None or cell[0] == 0:
                    previous = cells[index - 1]
                    assert previous is not None
                    cells[index] = previous
                    filled[(class_id, index + 1)] = FilledCell(
                        class_id=class_id, level=index + 1, source_level=index,
                        stats=previous)
            table[class_id] = {i + 1: c for i, c in enumerate(cells) if c is not None}
        return TaggedBaseStatTable(
            class_level_stats=table, race_stat_modifiers=dict(self.race_mods),
            source=self.source_string(fill_rule, max_player_level),
            filled=filled, fill_rule=fill_rule, max_player_level=max_player_level)


def playercreateinfo_pairs(path: Path | str | None = None) -> set[tuple[int, int]] | None:
    """(race, class) pairs recorded by ``character_prep.py base-stat-gap`` (or None)."""
    from . import CORPORA
    path = Path(path) if path is not None else CORPORA / "base-stat-gap.json"
    if not path.exists():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    block = raw.get("playercreateinfo_pairs")
    if not block or "pairs" not in block:
        return None
    return {(int(r), int(c)) for r, c in block["pairs"]}


def load(path: Path | str = CORPUS_PATH) -> TdbBaseStats:
    path = Path(path)
    if not path.exists():
        raise SourceError(
            f"{path} is missing; regenerate with tools/tdb_world_extract.py "
            "--tables player_classlevelstats,player_racestats,pet_levelstats")
    raw = json.loads(path.read_text(encoding="utf-8"))
    tables = raw.get("tables", {})
    try:
        cls_table = tables["player_classlevelstats"]
        race_table = tables["player_racestats"]
    except KeyError as error:
        raise SourceError(f"{path} lacks table {error}") from None
    ccols = cls_table["columns"]
    want = ["class", "level", "str", "agi", "sta", "inte", "spi"]
    if ccols[:7] != want:
        raise SourceError(f"player_classlevelstats columns {ccols} != expected {want}...")
    vb_index = ccols.index("VerifiedBuild") if "VerifiedBuild" in ccols else None
    class_level: dict[int, dict[int, tuple[int, ...]]] = {}
    verified: dict[tuple[int, int], int] = {}
    for row in cls_table["rows"]:
        class_id, level = int(row[0]), int(row[1])
        class_level.setdefault(class_id, {})[level] = tuple(int(v) for v in row[2:7])
        verified[(class_id, level)] = int(row[vb_index]) if vb_index is not None else -1
    rcols = race_table["columns"]
    if rcols[:6] != ["race", "str", "agi", "sta", "inte", "spi"]:
        raise SourceError(f"player_racestats columns {rcols} unexpected")
    race_mods = {int(r[0]): tuple(int(v) for v in r[1:6]) for r in race_table["rows"]}
    return TdbBaseStats(provenance=raw.get("provenance", {}), class_level=class_level,
                        verified_build=verified, race_mods=race_mods, path=path,
                        schemas=raw.get("schemas", {}))


def export_json(table: TaggedBaseStatTable) -> dict[str, Any]:
    """The ``charstats --base-stats FILE`` shape, with fill provenance attached."""
    return {
        "source": table.source,
        "fill_rule": table.fill_rule,
        "config_max_player_level": table.max_player_level,
        "class_level_stats": {str(c): {str(l): list(v) for l, v in sorted(levels.items())}
                              for c, levels in sorted(table.class_level_stats.items())},
        "race_stat_modifiers": {str(r): list(v) for r, v in sorted(table.race_stat_modifiers.items())},
        "filled_cells": [cell.to_dict() for _, cell in sorted(table.filled.items())],
    }


# -- CLI -------------------------------------------------------------------

def register(subparsers: Any) -> None:
    p = subparsers.add_parser(
        "basestats",
        help="base primary stats from the extracted TDB corpus (fill rule is opt-in)")
    p.add_argument("--corpus", type=Path, default=CORPUS_PATH)
    p.add_argument("--class", dest="class_id", type=int)
    p.add_argument("--race", dest="race_id", type=int, default=1)
    p.add_argument("--level", type=int)
    p.add_argument("--fill-rule", choices=FILL_RULES, default=FILL_RULE_NONE,
                   help="'trinity' reproduces ObjectMgr::LoadPlayerInfo's gap fill and tags "
                        "every filled cell trinity-consumer(fill-rule)")
    p.add_argument("--max-player-level", type=int, default=TRINITY_DEFAULT_MAX_PLAYER_LEVEL)
    p.add_argument("--export", type=Path, help="write a charstats --base-stats JSON file")
    p.add_argument("--coverage", action="store_true", help="print the coverage summary")
    p.add_argument("--pairs-from", type=Path, default=None,
                   help="base-stat-gap.json holding the playercreateinfo (race, class) pairs "
                        "(default: the committed corpus if present)")
    p.set_defaults(func=run)


def run(args: Any) -> int:
    corpus = load(args.corpus)
    table = corpus.table(args.fill_rule, args.max_player_level,
                         pairs=playercreateinfo_pairs(args.pairs_from))
    if args.coverage or (args.class_id is None and args.export is None):
        cov = corpus.coverage(args.max_player_level)
        cov["filled_cells"] = len(table.filled)
        cov["monotonicity_anomalies"] = corpus.monotonicity()
        print(json.dumps(cov, indent=1))
    if args.class_id is not None:
        if args.level is None:
            raise SystemExit("--level is required with --class")
        print(json.dumps(table.describe(args.class_id, args.race_id, args.level), indent=1))
    if args.export is not None:
        args.export.write_text(json.dumps(export_json(table), indent=1) + "\n", encoding="utf-8")
        print(f"wrote {args.export} ({len(table.filled)} filled cells, fill_rule={table.fill_rule})")
    return 0
