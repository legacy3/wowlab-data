"""Generic world-table extractor (tools/tdb_world_extract.py) on a synthetic dump.

The extractor is the shared TDB path of the combat-preparation research pass
(creature templates, pet level stats, player base stats), so its contract is
pinned here: pinned-database refusal, projection with key columns retained,
row filters, update replay, deterministic ordering and the provenance block.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

TOOL = Path(__file__).resolve().parents[1] / "tools" / "tdb_world_extract.py"
PINNED = "TDB_full_world_1200.26021_2026_02_06.sql"

DUMP = """
CREATE TABLE `player_racestats` (
  `race` tinyint unsigned NOT NULL,
  `str` smallint NOT NULL,
  `agi` smallint NOT NULL,
  `VerifiedBuild` int NOT NULL DEFAULT '0',
  PRIMARY KEY (`race`)
) ENGINE=InnoDB;
CREATE TABLE `pet_levelstats` (
  `creature_entry` int unsigned NOT NULL,
  `level` tinyint unsigned NOT NULL,
  `hp` smallint unsigned NOT NULL,
  `armor` int unsigned NOT NULL DEFAULT '0',
  PRIMARY KEY (`creature_entry`,`level`)
) ENGINE=InnoDB;
INSERT INTO `player_racestats` VALUES (2,3,-3,0),(1,0,0,0);
INSERT INTO `pet_levelstats` VALUES (1,2,55,21),(1,1,42,20),(416,1,10,0);
"""

UPDATE = """
DELETE FROM `pet_levelstats` WHERE `creature_entry`=416;
INSERT INTO `player_racestats` (`race`,`str`,`agi`) VALUES (3,2,-2);
"""


@pytest.fixture()
def fake_checkout(tmp_path: Path) -> tuple[Path, Path]:
    tc = tmp_path / "TrinityCore"
    (tc / "sql/updates/world/master").mkdir(parents=True)
    (tc / "revision_data.h.in.cmake").write_text(
        f'#define DATABASE_FULL_DATABASE      "{PINNED}"\n', encoding="utf-8")
    (tc / "sql/updates/world/master/2026_01_01_00_world.sql").write_text(UPDATE, encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(tc)], check=True)
    subprocess.run(["git", "-C", str(tc), "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-q", "--allow-empty", "-m", "x"], check=True)
    dump = tmp_path / PINNED
    dump.write_text(DUMP, encoding="utf-8")
    return tc, dump


def run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(TOOL), *args], capture_output=True, text=True)


def test_refuses_unpinned_dump(fake_checkout: tuple[Path, Path], tmp_path: Path) -> None:
    tc, dump = fake_checkout
    other = tmp_path / "TDB_full_world_9999.sql"
    other.write_text(DUMP, encoding="utf-8")
    result = run(["--tdb", str(other), "--tc-root", str(tc), "--tables", "player_racestats",
                  "--out", str(tmp_path / "o.json")])
    assert result.returncode == 2
    assert "refusing" in result.stderr


def test_extract_replays_updates_sorts_and_records_provenance(
        fake_checkout: tuple[Path, Path], tmp_path: Path) -> None:
    tc, dump = fake_checkout
    out = tmp_path / "out.json"
    result = run(["--tdb", str(dump), "--tc-root", str(tc),
                  "--tables", "player_racestats,pet_levelstats,not_a_table", "--out", str(out)])
    assert result.returncode == 0, result.stderr
    payload = json.loads(out.read_text(encoding="utf-8"))
    prov = payload["provenance"]
    assert prov["base_world_database"] == PINNED
    assert prov["tables_missing_from_dump"] == ["not_a_table"]
    assert prov["update_files_touching_tracked_tables"] == ["2026_01_01_00_world.sql"]
    assert prov["unparsed_statement_count"] == 0
    races = payload["tables"]["player_racestats"]
    assert races["columns"] == ["race", "str", "agi", "VerifiedBuild"]
    assert races["rows"] == [[1, 0, 0, 0], [2, 3, -3, 0], [3, 2, -2, 0]]  # sorted, default filled
    pets = payload["tables"]["pet_levelstats"]
    assert pets["rows"] == [[1, 1, 42, 20], [1, 2, 55, 21]]  # entry 416 deleted by the update
    assert pets["row_count_total"] == 2


def test_project_keeps_key_and_keep_filters_rows(
        fake_checkout: tuple[Path, Path], tmp_path: Path) -> None:
    tc, dump = fake_checkout
    ids = tmp_path / "ids.json"
    ids.write_text("[416]", encoding="utf-8")
    out = tmp_path / "out.json"
    result = run(["--tdb", str(dump), "--tc-root", str(tc), "--tables", "pet_levelstats",
                  "--project", "pet_levelstats=hp", "--keep", "pet_levelstats.creature_entry=@" + str(ids),
                  "--skip-updates", "--out", str(out)])
    assert result.returncode == 0, result.stderr
    pets = json.loads(out.read_text(encoding="utf-8"))["tables"]["pet_levelstats"]
    assert pets["columns"] == ["creature_entry", "level", "hp"]
    assert pets["projected"] is True
    assert pets["rows"] == [[416, 1, 10]]
    assert pets["row_count_total"] == 3 and pets["row_count_emitted"] == 1


def test_bad_project_column_fails_closed(fake_checkout: tuple[Path, Path], tmp_path: Path) -> None:
    tc, dump = fake_checkout
    result = run(["--tdb", str(dump), "--tc-root", str(tc), "--tables", "pet_levelstats",
                  "--project", "pet_levelstats=nope", "--out", str(tmp_path / "o.json")])
    assert result.returncode != 0
    assert "nope" in result.stderr


def test_indexed_state_matches_plain_scan() -> None:
    """IndexedTableState must be observably identical to tdb_replay.TableState."""
    import random

    sys.path.insert(0, str(TOOL.parent))
    from tdb_replay import Replay, parse_create_tables
    from tdb_world_extract import indexed

    dump = """
CREATE TABLE `t` (
  `Entry` int unsigned NOT NULL,
  `Diff` tinyint unsigned NOT NULL DEFAULT '0',
  `v` int NOT NULL DEFAULT '0',
  PRIMARY KEY (`Entry`,`Diff`)
) ENGINE=InnoDB;
"""
    rng = random.Random(7)
    statements = []
    for _ in range(20000):
        e, d, v = rng.randrange(12), rng.randrange(3), rng.randrange(100)
        kind = rng.randrange(7)
        if kind == 0:
            statements.append(f"INSERT IGNORE INTO `t` VALUES ({e},{d},{v})")
        elif kind == 1:
            statements.append(f"REPLACE INTO `t` VALUES ({e},{d},{v})")
        elif kind == 2:
            statements.append(f"DELETE FROM `t` WHERE `entry`={e} AND `Diff` IN ({d},{(d + 1) % 3})")
        elif kind == 3:
            statements.append(f"UPDATE `t` SET `v`={v} WHERE `Entry` IN ({e},{rng.randrange(12)})")
        elif kind == 4:  # key-changing update (collisions exercise row order)
            statements.append(f"UPDATE `t` SET `Diff`={d} WHERE `Entry`={e}")
        elif kind == 5:  # entry-changing update
            statements.append(f"UPDATE `t` SET `Entry`={rng.randrange(12)} WHERE `Entry`={e} AND `Diff`={d}")
        else:  # no key column in WHERE: full-scan fallback
            statements.append(f"UPDATE `t` SET `v`=`v`+1 WHERE `v`={v} OR `Diff`={d}")
    plain = Replay(parse_create_tables(dump, {"t"}))
    fast = indexed(Replay(parse_create_tables(dump, {"t"})))
    for stmt in statements:
        results = []
        for replay in (plain, fast):
            try:
                results.append(replay.apply(stmt))
            except ValueError as exc:
                results.append(("error", str(exc)))
        assert results[0] == results[1], stmt
        assert list(plain.state["t"].rows.items()) == list(fast.state["t"].rows.items()), stmt
    assert len(plain.state["t"].rows) > 0


def test_keep_file_object_uses_ids_list_and_rejects_other_objects(
        fake_checkout: tuple[Path, Path], tmp_path: Path) -> None:
    tc, dump = fake_checkout
    good = tmp_path / "ids.json"
    good.write_text(json.dumps({"provenance": {}, "ids": [416]}), encoding="utf-8")
    out = tmp_path / "out.json"
    result = run(["--tdb", str(dump), "--tc-root", str(tc), "--tables", "pet_levelstats",
                  "--keep", "pet_levelstats.creature_entry=@" + str(good), "--skip-updates",
                  "--out", str(out)])
    assert result.returncode == 0, result.stderr
    assert json.loads(out.read_text())["tables"]["pet_levelstats"]["rows"] == [[416, 1, 10, 0]]
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"416": True}), encoding="utf-8")
    result = run(["--tdb", str(dump), "--tc-root", str(tc), "--tables", "pet_levelstats",
                  "--keep", "pet_levelstats.creature_entry=@" + str(bad), "--out", str(out)])
    assert result.returncode != 0
    assert "'ids'" in result.stderr
