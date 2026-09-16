"""World-DB overlay interpreter, Trinity header parity, and the CLI."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from procs import enums as E
from procs.cli import main as cli_main
from procs.trinity import TrinityOverlay

RESEARCH = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RESEARCH / "tools"))
import tdb_proc_overlay as tdb

TC = RESEARCH.parents[2] / "TrinityCore"  # scripts/research -> wowlab-data -> pallet


# -- restricted SQL replay --------------------------------------------------------

def fresh():
    return {name: tdb.TableState(name) for name in tdb.TABLES}


def apply_all(sql, state):
    tdb.VARIABLES.clear()
    for stmt in tdb.split_statements(sql):
        tdb.apply_statement(stmt, state)


def test_split_statements_handles_comments_and_strings():
    sql = "-- c; x\nINSERT INTO a VALUES ('x;y'); /* ; */ DELETE FROM b WHERE c=1;# trailing;\n"
    assert tdb.split_statements(sql) == ["INSERT INTO a VALUES ('x;y')", "DELETE FROM b WHERE c=1"]


def test_insert_delete_update_replace():
    st = fresh()
    apply_all("""
        INSERT INTO `spell_script_names` (`spell_id`,`ScriptName`) VALUES (1,'a'),(2,"b"),(-3,'c');
        DELETE FROM `spell_script_names` WHERE `ScriptName` IN ('a');
        UPDATE `spell_script_names` SET `ScriptName` = 'bb' WHERE `ScriptName` = 'b';
        DELETE FROM `spell_script_names` WHERE (`spell_id` = -3 AND `ScriptName` = 'c') OR
        (`spell_id` = 99 AND `ScriptName` = 'zz');
    """, st)
    assert sorted(st["spell_script_names"].rows) == [(2, "bb")]


def test_spell_proc_hex_and_variables():
    st = fresh()
    apply_all("""
        SET @BASE := 100;
        INSERT INTO `spell_proc` (`SpellId`,`ProcFlags`,`HitMask`,`Chance`) VALUES (@BASE+1, 0x10, 2|8, 50.5);
    """, st)
    row = st["spell_proc"].rows[(101,)]
    assert (row["ProcFlags"], row["HitMask"], row["Chance"], row["Cooldown"]) == (16, 10, 50.5, 0)


def test_unsupported_shapes_raise():
    st = fresh()
    with pytest.raises(ValueError):
        tdb.apply_statement("UPDATE `conditions` SET `ConditionValue2`=`ConditionValue2`|4 WHERE `SourceGroup`=1", st)
    with pytest.raises(ValueError):
        tdb.apply_statement("DELETE FROM `spell_proc` WHERE `SpellId` > 5", st)
    tdb.apply_statement("INSERT INTO `spell_proc` (`SpellId`) VALUES (1)", st)
    with pytest.raises(ValueError, match="duplicate key"):
        tdb.apply_statement("INSERT INTO `spell_proc` (`SpellId`) VALUES (1)", st)


def test_untracked_tables_ignored():
    assert tdb.apply_statement("DELETE FROM `creature` WHERE `guid`=1", fresh()) is None


def test_committed_overlay_is_complete():
    ov = TrinityOverlay()
    prov = ov.provenance
    assert prov["unparsed_may_affect_output"] == 0
    assert prov["trinitycore_commit"] == "7f3d43b7c8dd1bbb413d7acbd057e58e9d048a8f"
    assert prov["base_world_database"] == "TDB_full_world_1200.26021_2026_02_06.sql"
    assert prov["final_row_counts"]["spell_proc"] == len(ov.spell_proc) == 1280
    assert all(u["may_affect_output"] is False for u in ov.unparsed)


# -- parity with the sibling TrinityCore headers ------------------------------------------

needs_tc = pytest.mark.skipif(not TC.exists(), reason="sibling TrinityCore checkout absent")


def enum_values(text, header):
    start = text.index(header)
    body = text[text.index("{", start):text.index("};", start)]
    return {m.group(1): int(m.group(2), 0) for m in re.finditer(r"(\w+)\s*=\s*(0x[0-9A-Fa-f]+|\d+)", body)}


@needs_tc
def test_proc_flag_enums_match_headers():
    h = (TC / "src/server/game/Spells/SpellMgr.h").read_text()
    flags = enum_values(h, "enum ProcFlags : uint32")
    flags2 = enum_values(h, "enum ProcFlags2 : int32")
    for name, value in flags.items():
        if name.startswith("PROC_FLAG_") and name != "PROC_FLAG_NONE":
            assert getattr(E, name) == value, name
    for name, value in flags2.items():
        if name != "PROC_FLAG_2_NONE":
            assert getattr(E, name) == value << 32, name
    for header, prefix in (("enum ProcFlagsHit : uint32", "PROC_HIT_"),
                           ("enum ProcAttributes : uint32", "PROC_ATTR_"),
                           ("enum ProcFlagsSpellType : uint32", "PROC_SPELL_TYPE_"),
                           ("enum ProcFlagsSpellPhase : uint32", "PROC_SPELL_PHASE_")):
        for name, value in enum_values(h, header).items():
            assert getattr(E, name) == value, name


@needs_tc
def test_trigger_aura_tables_match_load_spell_procs():
    cpp = (TC / "src/server/game/Spells/SpellMgr.cpp").read_text()
    names = re.findall(r"isTriggerAura\[SPELL_AURA_(\w+)\] = true;", cpp)
    always = re.findall(r"isAlwaysTriggeredAura\[SPELL_AURA_(\w+)\] = true;", cpp)
    types = dict(re.findall(r"spellTypeMask\[SPELL_AURA_(\w+)\] = ([^;]+);", cpp))
    assert tuple(names) == E.TRIGGER_AURA_NAMES
    assert tuple(always) == E.ALWAYS_TRIGGERED_AURA_NAMES
    assert set(types) == set(E.AURA_SPELL_TYPE_MASK_NAMES)


@needs_tc
def test_ppm_mod_types_match_dbcenums():
    h = (TC / "src/server/game/DataStores/DBCEnums.h").read_text()
    vals = enum_values(h, "enum SpellProcsPerMinuteModType")
    assert {v: k.removeprefix("SPELL_PPM_MOD_") for k, v in vals.items()} == E.PPM_MOD_NAMES


@needs_tc
def test_every_proc_skills_and_auras_call_site_is_listed():
    out = subprocess.run(["rg", "-c", r"ProcSkillsAndAuras\(", str(TC / "src/server/game")],
                         capture_output=True, text=True).stdout
    total = sum(int(line.rsplit(":", 1)[1]) for line in out.splitlines())
    # Unit.h declares it twice, Unit.cpp defines it once; everything else is a call.
    assert total - 3 == 25


# -- CLI --------------------------------------------------------------------------

@pytest.mark.snapshot
def test_cli_provider_and_event(capsys, tables):
    assert cli_main(["--tables", str(tables.root), "provider", "1250564"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["triggered_spells"] == [1254180] and out["lifecycle"]["lifecycle"] == "passive"
    assert cli_main(["--tables", str(tables.root), "event", "1250564", '{"kind": "melee"}']) == 0
    ev = json.loads(capsys.readouterr().out)
    assert ev["eligibility"]["verdict"] == "ineligible"


@pytest.mark.snapshot
def test_cli_rppm_and_errors(capsys, tables):
    assert cli_main(["--tables", str(tables.root), "rppm", "1237014", "--haste", "0.25"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["rate"]["chance_percent"] == pytest.approx(7.5, rel=1e-6)
    assert cli_main(["--tables", str(tables.root), "rppm", "16166"]) == 2


def test_cli_timeline(capsys, tmp_path, tables):
    spec = {
        "providers": [{"spell_id": 1307356, "holder": "actor"}],
        "stream": [
            {"time_ms": 0, "kind": "apply", "spell_id": 1307356},
            {"time_ms": 100, "kind": "event", "event": {"kind": "kill"}, "rolls": [5.0],
             "facts": {"action_target_gives_xp_or_honor": True, "actor_level": 90}},
            {"time_ms": 200, "kind": "event", "event": {"kind": "kill"},
             "facts": {"action_target_gives_xp_or_honor": True, "actor_level": 90}},
        ]}
    path = tmp_path / "t.json"
    path.write_text(json.dumps(spec))
    assert cli_main(["--tables", str(tables.root), "timeline", str(path)]) == 0
    log = json.loads(capsys.readouterr().out)
    assert log[1]["steps"][0]["success"] is True
    assert log[2]["steps"][0]["reason"] == "proc-cooldown"
