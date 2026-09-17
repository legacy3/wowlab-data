"""Track C / agent F: server-authored inputs and the base-stat gap.

Facts pinned here are the pinned TDB rows (world-db-fact), Trinity's gap-fill
rule reproduced as an explicit opt-in (trinity-consumer(fill-rule)), and the
registry of server inputs.  Nothing here asserts Retail truth.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

RESEARCH_ROOT = Path(__file__).resolve().parents[1]
if str(RESEARCH_ROOT) not in sys.path:
    sys.path.insert(0, str(RESEARCH_ROOT))

from character_prep import CORPORA, SourceError  # noqa: E402
from character_prep import basestats_tdb as bt  # noqa: E402
from character_prep import server_inputs as si  # noqa: E402
from charstats import MissingBaseStats  # noqa: E402
from charstats.identity import STAT_AGILITY, STAT_INTELLECT, STAT_STAMINA, STAT_STRENGTH  # noqa: E402

BASE_STAT_GAP = CORPORA / "base-stat-gap.json"
SERVER_INPUTS = CORPORA / "server-inputs.json"


@pytest.fixture(scope="module")
def corpus() -> bt.TdbBaseStats:
    if not bt.CORPUS_PATH.exists():
        pytest.skip(f"missing {bt.CORPUS_PATH}")
    return bt.load()


@pytest.fixture(scope="module")
def pairs() -> set[tuple[int, int]]:
    p = bt.playercreateinfo_pairs()
    if p is None:
        pytest.skip("base-stat-gap.json with playercreateinfo pairs not generated")
    return p


@pytest.fixture(scope="module")
def gap() -> dict:
    if not BASE_STAT_GAP.exists():
        pytest.skip("base-stat-gap.json not generated")
    return json.loads(BASE_STAT_GAP.read_text(encoding="utf-8"))


# -- corpus shape --------------------------------------------------------------

def test_corpus_is_13_classes_levels_1_to_80(corpus):
    assert corpus.classes() == list(range(1, 14))
    assert corpus.max_level() == 80
    assert sum(len(v) for v in corpus.class_level.values()) == 1032
    for class_id in range(1, 13):
        assert corpus.levels(class_id) == list(range(1, 81))


def test_evoker_rows_skip_levels_2_to_9(corpus):
    levels = corpus.levels(13)
    assert len(levels) == 72
    assert [l for l in range(1, 81) if l not in levels] == list(range(2, 10))


def test_verified_build_is_zero_everywhere(corpus):
    assert set(corpus.verified_build.values()) == {0}


def test_no_zero_strength_rows_and_stamina_anomaly(corpus):
    cov = corpus.coverage()
    assert all(not c["zero_strength_rows"] for c in cov["per_class"].values())
    anomalies = corpus.monotonicity()
    assert set(anomalies) == {"Stamina"}
    assert {(a["level"], a["value"], a["next_value"]) for a in anomalies["Stamina"]} == {(69, 6655, 6397)}
    assert len(anomalies["Stamina"]) == 13


def test_race_rows_31_including_updated_haranir(corpus):
    assert corpus.races() == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 22, 24, 25, 26, 27, 28, 29, 30, 31, 32,
                              34, 35, 36, 37, 52, 70, 84, 85, 86, 91]
    assert "2026_03_07_01_world.sql" in corpus.provenance["update_files_touching_tracked_tables"]
    for race in (52, 70, 84, 85, 86, 91):
        assert corpus.race_mods[race] == (0, 0, 0, 0, 0)
    assert corpus.race_mods[2] == (3, -3, 1, -1, 0)


# -- adapter: no fill rule -------------------------------------------------------

def test_no_fill_rule_fails_closed_above_80(corpus):
    table = corpus.table(bt.FILL_RULE_NONE)
    assert table.lookup(1, 80, 1) == (17647, 12176, 86452, 12000, 0)
    assert table.lookup(1, 80, 2) == (17650, 12173, 86453, 11999, 0)
    with pytest.raises(MissingBaseStats):
        table.lookup(1, 81, 1)
    with pytest.raises(MissingBaseStats):
        table.lookup(13, 5, 52)
    assert table.evidence(1, 80) == bt.EVIDENCE_ROW
    assert "fill_rule=none" in table.source


# -- adapter: Trinity fill rule ---------------------------------------------------

def test_fill_rule_copies_level_80_to_90_and_tags_it(corpus, pairs):
    table = corpus.table(bt.FILL_RULE_TRINITY, pairs=pairs)
    assert len(table.filled) == 13 * 10 + 8
    for level in range(81, 91):
        assert table.lookup(1, level, 1) == table.lookup(1, 80, 1)
        assert table.evidence(1, level) == bt.EVIDENCE_FILLED
    cell = table.filled[(1, 90)]
    assert cell.source_level == 89 and cell.evidence_class == "trinity-consumer(fill-rule)"
    described = table.describe(1, 1, 90)
    assert described["retail_truth"] == "unresolved"
    assert described["filled_from"]["coordinate"] == bt.FILL_RULE_COORDINATES["fill_gaps"]


def test_fill_rule_evoker_levels_2_to_9_are_level_1(corpus, pairs):
    table = corpus.table(bt.FILL_RULE_TRINITY, pairs=pairs)
    for level in range(2, 10):
        assert table.lookup(13, level, 52) == (3, 4, 6, 6, 0)
        assert table.evidence(13, level) == bt.EVIDENCE_FILLED
    assert table.evidence(13, 10) == bt.EVIDENCE_ROW


def test_fill_rule_refuses_when_race_modified_strength_would_be_zero(corpus):
    crossings = corpus.zero_strength_crossings(90)
    assert {(c["race_id"], c["class_id"], c["level"]) for c in crossings} == {
        (7, 13, 1), (9, 13, 1), (10, 13, 1), (29, 13, 1), (35, 13, 1)}
    with pytest.raises(SourceError, match="race-modified strength is 0"):
        corpus.table(bt.FILL_RULE_TRINITY)          # all races x all classes: refuses
    assert corpus.zero_strength_crossings(90, {(52, 13), (70, 13)}) == []


def test_fill_rule_with_lower_config_max_ignores_rows_above(corpus, pairs):
    table = corpus.table(bt.FILL_RULE_TRINITY, max_player_level=70, pairs=pairs)
    assert table.lookup(1, 70, 1) == (2089, 1442, 6397, 1421, 0)
    with pytest.raises(MissingBaseStats):
        table.lookup(1, 71, 1)                       # BuildPlayerLevelInfo territory, not reproduced
    assert not any(level > 70 for (_, level) in table.filled)


def test_unknown_fill_rule_rejected(corpus):
    with pytest.raises(SourceError):
        corpus.table("extrapolate")


def test_export_shape_is_charstats_compatible(corpus, pairs, tmp_path):
    from charstats.basestats import BaseStatTable
    table = corpus.table(bt.FILL_RULE_TRINITY, pairs=pairs)
    path = tmp_path / "base.json"
    path.write_text(json.dumps(bt.export_json(table)), encoding="utf-8")
    loaded = BaseStatTable.from_json_file(path)
    assert loaded.lookup(5, 90, 1) == table.lookup(5, 90, 1)
    assert "trinity-consumer(fill-rule)" in loaded.source


# -- base-stat-gap corpus ------------------------------------------------------------

def test_gap_corpus_provenance_and_pairs(gap):
    assert gap["provenance"]["trinitycore_commit"] == "7f3d43b7c8dd1bbb413d7acbd057e58e9d048a8f"
    assert gap["playercreateinfo_pairs"]["count"] == 281
    assert gap["classes_in_chrclasses_without_rows"] == [14, 15]
    assert gap["filled_cells_total"] == 138
    assert gap["zero_strength_crossings_created_pairs"] == []


def test_gap_corpus_race_coverage(gap):
    rc = gap["race_coverage"]
    assert rc["playable_without_row"] == []
    assert rc["playable_race_bit_but_npc_only"] == [95, 96]
    assert rc["playercreateinfo_races_without_racestats_row"] == []
    assert len(rc["playable_by_db2"]) == 31


def test_gap_corpus_level_90_verdict_is_copy_of_80(gap):
    for class_id, entry in gap["level_90_verdict"].items():
        assert entry["level_90_trinity"] == entry["level_80_row"]
        assert entry["evidence_class"] == "trinity-consumer(fill-rule)"
        assert entry["retail_truth"] == "unresolved"
        if entry.get("probe"):
            assert entry["probe_matches_adapter"] is True
            expected_errors = 10 + (8 if class_id == "13" else 0)
            assert entry["probe"]["tc_log_error_count"] == expected_errors


def test_gap_corpus_evoker_pairs_only_dracthyr(gap):
    assert gap["class_coverage"]["13"]["playercreateinfo_races"] == [52, 70]
    assert gap["class_coverage"]["13"]["filled_cells_trinity_rule"] == list(range(2, 10)) + list(range(81, 91))


# -- server inputs registry / corpus -----------------------------------------------------

def test_registry_phases_and_coordinates():
    reg = si.registry()
    ids = {r.id for r in reg}
    assert {"player_classlevelstats", "player_racestats", "playercreateinfo", "MaxPlayerLevel"} <= ids
    for item in reg:
        assert item.phase in si.PHASES
        assert item.loader and item.consumers
    prepared = {r.id for r in reg if r.phase == "prepared"}
    assert prepared == {"player_classlevelstats", "player_racestats", "MaxPlayerLevel",
                        "Stats.Limits.Enable / Stats.Limits.Dodge / Parry / Block / Crit"}
    rates = next(r for r in reg if r.id.startswith("Rate.Health"))
    assert rates.phase == "runtime"


def test_conf_dist_values_pinned():
    conf = si.conf_dist_values()
    if conf["MaxPlayerLevel"]["value"] is None:
        pytest.skip("worldserver.conf.dist not available")
    assert conf["MaxPlayerLevel"] == {"value": "90", "line": 912,
                                      "coordinate": "src/server/worldserver/worldserver.conf.dist:912"}
    assert conf["Rate.Health"]["value"] == "1"
    assert conf["Stats.Limits.Enable"]["value"] == "0"
    assert conf["StartEvokerPlayerLevel"]["value"] == "10"


def test_server_inputs_corpus_row_counts():
    if not SERVER_INPUTS.exists():
        pytest.skip("server-inputs.json not generated")
    d = json.loads(SERVER_INPUTS.read_text(encoding="utf-8"))
    counts = d["tdb_row_counts"]
    assert counts["player_classlevelstats"] == 1032
    assert counts["player_racestats"] == 31
    assert counts["playercreateinfo"] == 281
    assert counts["player_xp_for_level"] == 0
    assert counts["item_random_bonus_list_template"] == 0
    assert d["item_template_addon_random_bonus_nonzero"] == 0
    assert d["tdb_extraction"]["unparsed_statement_count"] == 0


def test_identity_query_level_90_needs_fill_rule(corpus, pairs):
    result = si.identity_needs(corpus, 1, 1, 90, pairs)
    by = {n["input"]: n for n in result["needs"]}
    assert by["player_classlevelstats"]["status"] == "fill-rule"
    assert by["player_classlevelstats"]["evidence_class"] == "trinity-consumer(fill-rule)"
    assert by["playercreateinfo"]["status"] == "satisfied"
    assert result["unsatisfied"] == ["player_classlevelstats"]


def test_identity_query_absent_pair_and_npc_race(corpus, pairs):
    result = si.identity_needs(corpus, 13, 7, 80, pairs)          # Gnome Evoker: no pair
    by = {n["input"]: n for n in result["needs"]}
    assert by["playercreateinfo"]["status"] == "unsatisfied"
    assert by["player_classlevelstats"]["status"] == "satisfied"
    result = si.identity_needs(corpus, 1, 95, 80, pairs)          # TBD NPC Race 1
    by = {n["input"]: n for n in result["needs"]}
    assert by["player_racestats"]["status"] == "unsatisfied"
    assert by["ChrRaces (client)"]["status"] == "unsatisfied"
    result = si.identity_needs(corpus, 1, 1, 91, pairs)
    by = {n["input"]: n for n in result["needs"]}
    assert by["MaxPlayerLevel"]["status"] == "unsatisfied"
    assert "BuildPlayerLevelInfo" in by["player_classlevelstats"]["detail"]


def test_identity_query_unknown_pairs_is_reported_not_guessed(corpus):
    result = si.identity_needs(corpus, 1, 1, 80, None)
    by = {n["input"]: n for n in result["needs"]}
    assert by["playercreateinfo"]["status"] == "unknown"
    assert by["playercreateinfo"]["evidence_class"] == "unresolved"


# -- the cited Trinity lines still say what the corpus claims (7f3d43b) -------------

TRINITY = RESEARCH_ROOT.parents[2] / "TrinityCore"
PINNED_LINES = [
    ("src/server/game/Globals/ObjectMgr.cpp", 4287, "FROM player_classlevelstats"),
    ("src/server/game/Globals/ObjectMgr.cpp", 4309, "current_level > sWorld->getIntConfig(CONFIG_MAX_PLAYER_LEVEL)"),
    ("src/server/game/Globals/ObjectMgr.cpp", 4324, "std::make_unique<PlayerLevelInfo[]>(sWorld->getIntConfig(CONFIG_MAX_PLAYER_LEVEL))"),
    ("src/server/game/Globals/ObjectMgr.cpp", 4328, "levelInfo.stats[i] = fields[i + 2].GetInt32() + raceStats.StatModifier[i];"),
    ("src/server/game/Globals/ObjectMgr.cpp", 4342, "if (!playerInfo->levelInfo || playerInfo->levelInfo[0].stats[0] == 0)"),
    ("src/server/game/Globals/ObjectMgr.cpp", 4345, "ABORT();"),
    ("src/server/game/Globals/ObjectMgr.cpp", 4351, "if (playerInfo->levelInfo[level].stats[0] == 0)"),
    ("src/server/game/Globals/ObjectMgr.cpp", 4354, "playerInfo->levelInfo[level] = playerInfo->levelInfo[level - 1];"),
    ("src/server/game/Globals/ObjectMgr.cpp", 4447, "*info = pInfo->levelInfo[level - 1];"),
    ("src/server/game/Globals/ObjectMgr.cpp", 4449, "BuildPlayerLevelInfo(race, class_, level, info);"),
    ("src/server/game/World/World.cpp", 750, "GetMaxLevelForExpansion(CURRENT_EXPANSION)"),
    ("src/server/game/Miscellaneous/SharedDefines.h", 107, "#define CURRENT_EXPANSION EXPANSION_MIDNIGHT"),
    ("src/server/worldserver/worldserver.conf.dist", 912, "MaxPlayerLevel = 90"),
    ("src/server/game/Entities/Player/Player.cpp", 2332, "sObjectMgr->GetPlayerLevelInfo(GetRace(), GetClass(), GetLevel(), &info);"),
    ("src/server/game/Entities/Player/Player.cpp", 1748, "sWorld->getRate(RATE_HEALTH)"),
    ("src/server/game/Entities/Unit/StatSystem.cpp", 344, "SetMaxPower(power, (int32)std::lroundf(value));"),
    ("src/server/game/Entities/Unit/StatSystem.cpp", 549, "float value = GetTotalAuraModifier(SPELL_AURA_MASTERY);"),
    ("src/server/game/Entities/Unit/Unit.cpp", 5033, "return static_cast<float>(multiplier);"),
]


@pytest.mark.parametrize("rel,line,text", PINNED_LINES, ids=lambda x: str(x))
def test_pinned_trinity_coordinates(rel, line, text):
    path = TRINITY / rel
    if not path.exists():
        pytest.skip("TrinityCore sibling checkout absent")
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    assert text in lines[line - 1], f"{rel}:{line} moved: {lines[line - 1].strip()!r}"


def test_registry_ids_expand_to_every_counted_table():
    known = {"playercreateinfo_item": 1, "playercreateinfo_spell_custom": 0, "playercreateinfo_cast_spell": 31,
             "playercreateinfo_action": 1942, "spell_group": 297, "spell_group_stack_rules": 13, "skill_tiers": 59}
    assert si.id_tables("playercreateinfo_item / _spell_custom / _cast_spell / _action", known) == [
        "playercreateinfo_item", "playercreateinfo_spell_custom", "playercreateinfo_cast_spell", "playercreateinfo_action"]
    assert si.id_tables("spell_group(+stack_rules)", known) == ["spell_group", "spell_group_stack_rules"]
    assert si.id_tables("skill_tiers / skill_*", known) == ["skill_tiers"]


def test_server_inputs_corpus_multi_table_rows_complete():
    if not SERVER_INPUTS.exists():
        pytest.skip("server-inputs.json not generated")
    doc = json.loads(SERVER_INPUTS.read_text(encoding="utf-8"))
    by_id = {i["id"]: i for i in doc["inputs"]}
    create = by_id["playercreateinfo_item / _spell_custom / _cast_spell / _action"]["tdb"]
    assert {k: v["rows"] for k, v in create.items()} == {
        "playercreateinfo_item": 1, "playercreateinfo_spell_custom": 0,
        "playercreateinfo_cast_spell": 31, "playercreateinfo_action": 1942}
    covered = {t for i in doc["inputs"] for t in i["tdb"]}
    assert covered == set(doc["tdb_row_counts"])          # every counted table is attached to an input
