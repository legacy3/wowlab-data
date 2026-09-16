"""Schema-driven world-DB replay (tools/tdb_replay.py) unit tests."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from tdb_replay import Replay, Schema, parse_create_tables, split_statements  # noqa: E402

DUMP = """
CREATE TABLE `spell_linked_spell` (
  `spell_trigger` int NOT NULL,
  `spell_effect` int NOT NULL DEFAULT '0',
  `type` tinyint unsigned NOT NULL DEFAULT '0',
  `comment` mediumtext CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
  UNIQUE KEY `trigger_effect_type` (`spell_trigger`,`spell_effect`,`type`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='Spell System';
CREATE TABLE `creature_template` (
  `entry` int unsigned NOT NULL DEFAULT '0',
  `flags_extra` int unsigned NOT NULL DEFAULT '0',
  `AIName` char(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT '',
  `ScriptName` char(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT '',
  PRIMARY KEY (`entry`)
) ENGINE=InnoDB;
"""


def make() -> Replay:
    schemas = parse_create_tables(DUMP, {"spell_linked_spell", "creature_template"})
    assert schemas["spell_linked_spell"].key == ("spell_trigger", "spell_effect", "type")
    assert schemas["creature_template"].defaults["ScriptName"] == ""
    return Replay(schemas)


def test_insert_delete_update_and_case_insensitive_columns():
    r = make()
    r.apply("INSERT INTO `creature_template` VALUES (1, 130, 'SmartAI', '')")
    r.apply("INSERT INTO `creature_template` (`entry`, `ScriptName`) VALUES (2, 'npc_x')")
    assert r.state["creature_template"].rows[(2,)]["flags_extra"] == 0
    assert r.apply("UPDATE `creature_template` SET `ScriptName` = 'npc_y' WHERE `Entry`=2") == ("UPDATE", 1)
    assert r.state["creature_template"].rows[(2,)]["ScriptName"] == "npc_y"
    assert r.apply("DELETE FROM `creature_template` WHERE `entry` IN (1, 99)") == ("DELETE", 1)


def test_set_expressions_bit_ops_and_division():
    r = make()
    r.apply("INSERT INTO `creature_template` VALUES (1, 130, '', '')")
    r.apply("UPDATE `creature_template` SET `flags_extra` = `flags_extra` &~ (128|2), `ScriptName`='x' WHERE `entry`=1")
    assert r.state["creature_template"].rows[(1,)]["flags_extra"] == 0
    r.apply("UPDATE `creature_template` SET `flags_extra`=0x01000000|0x00002000 WHERE `entry` IN (1)")
    assert r.state["creature_template"].rows[(1,)]["flags_extra"] == 0x01002000
    r.apply("UPDATE `creature_template` SET `flags_extra`=`flags_extra`|32|64 WHERE `entry`=1")
    assert r.state["creature_template"].rows[(1,)]["flags_extra"] == 0x01002000 | 96


def test_duplicate_insert_raises_and_replace_wins():
    r = make()
    r.apply("INSERT INTO `spell_linked_spell` VALUES (-100, 200, 0, 'a')")
    with pytest.raises(ValueError):
        r.apply("INSERT INTO `spell_linked_spell` VALUES (-100, 200, 0, 'dup')")
    assert r.apply("INSERT IGNORE INTO `spell_linked_spell` VALUES (-100, 200, 0, 'dup')") == ("INSERT IGNORE", 0)
    assert r.apply("REPLACE INTO `spell_linked_spell` VALUES (-100, 200, 0, 'b')") == ("REPLACE", 1)
    assert r.state["spell_linked_spell"].rows[(-100, 200, 0)]["comment"] == "b"


def test_alter_on_tracked_table_is_unparsed_and_untracked_statements_ignored():
    r = make()
    with pytest.raises(ValueError):
        r.apply("ALTER TABLE `creature_template` ADD COLUMN `x` int")
    assert r.apply("INSERT INTO `other_table` VALUES (1)") is None
    assert r.apply("SET @CGUID := 12") is None
    assert r.session.variables["@CGUID"] == 12


def test_split_statements_handles_strings_and_comments():
    stmts = split_statements("INSERT INTO `t` VALUES (1, 'a;b'); -- c;\n/* x; */ DELETE FROM `t` WHERE `a`=1;")
    assert len(stmts) == 2 and stmts[0].endswith("'a;b')")
