"""Identity, base stats and the fail-closed boundary around them."""

from __future__ import annotations

import json

import pytest

from charstats import CharacterSourceError, MissingBaseStats
from charstats.basestats import BaseStatEngine, BaseStatTable
from charstats.identity import (
    CLASS_NAMES,
    GAMETABLE_CLASS_COLUMNS,
    MAX_STATS,
    STAT_AGILITY,
    STAT_INTELLECT,
    STAT_STRENGTH,
    gametable_class_column,
    primary_stat_from_priority,
)

pytestmark = pytest.mark.snapshot


# -- PrimaryStatPriority --------------------------------------------------

@pytest.mark.parametrize("priority, expected", [
    (0, STAT_INTELLECT), (1, STAT_INTELLECT),
    (2, STAT_AGILITY), (3, STAT_AGILITY),
    (4, STAT_STRENGTH), (5, STAT_STRENGTH), (9, STAT_STRENGTH),
])
def test_primary_stat_priority_thresholds(priority, expected):
    assert primary_stat_from_priority(priority) == expected


# -- GameTable class columns ----------------------------------------------

def test_gametable_class_column_order_matches_the_consumer():
    # Mirrors GetGameTableColumnForClass; the .txt header order is the struct
    # field order, which is NOT the Classes enum order.
    assert GAMETABLE_CLASS_COLUMNS[0] == "Rogue"
    assert GAMETABLE_CLASS_COLUMNS[8] == "Warrior"
    assert gametable_class_column(4) == 0      # Rogue
    assert gametable_class_column(1) == 8      # Warrior
    assert gametable_class_column(13) == 12    # Evoker
    with pytest.raises(CharacterSourceError):
        gametable_class_column(99)


def test_gametable_headers_match_the_transcribed_order(tables):
    for name in ("BaseMp", "SpellScaling"):
        columns = tables.gametable(name).columns[:len(GAMETABLE_CLASS_COLUMNS)]
        normalised = [c.replace(" ", "") for c in columns]
        assert normalised == list(GAMETABLE_CLASS_COLUMNS), name


# -- identity store --------------------------------------------------------

def test_every_playable_class_is_present(character_resolver):
    ids = {c.class_id for c in character_resolver.identity.classes()}
    assert ids == set(CLASS_NAMES)


def test_specs_exclude_pet_and_initial_by_default(character_resolver):
    specs = character_resolver.identity.specs()
    assert all(s.class_id > 0 for s in specs)
    assert all(not s.is_initial for s in specs)
    with_initial = character_resolver.identity.specs(include_initial=True)
    assert len(with_initial) > len(specs)


def test_every_playable_spec_has_a_mastery_spell(character_resolver):
    for spec in character_resolver.identity.specs():
        assert any(spec.mastery_spell_ids), f"{spec.spec_id} {spec.name}"


def test_spec_lookup_by_name_is_class_scoped(character_resolver):
    identity = character_resolver.identity
    # "Frost" exists for both Death Knight (6) and Mage (8).
    assert identity.find_spec(6, "Frost").spec_id != identity.find_spec(8, "Frost").spec_id
    with pytest.raises(CharacterSourceError, match="no specialization named"):
        identity.find_spec(6, "Fire")


def test_unknown_identity_fails_closed(character_resolver):
    identity = character_resolver.identity
    with pytest.raises(CharacterSourceError, match="ChrRaces has no row"):
        identity.race(99999)
    with pytest.raises(CharacterSourceError, match="ChrClasses has no row"):
        identity.klass(99999)
    with pytest.raises(CharacterSourceError, match="no playable row"):
        identity.spec(99999)


# -- base stats ------------------------------------------------------------

def test_base_stats_fail_closed_without_a_table(bare_character_resolver):
    with pytest.raises(MissingBaseStats, match="player_classlevelstats"):
        bare_character_resolver.resolve(1, 6, 90, 250)


def test_base_mana_comes_from_the_gametable(character_resolver, tables):
    engine = BaseStatEngine(tables)
    # Warrior has no mana: the column is a real zero, not a missing row.
    assert engine.base_mana(1, 90) == 0
    # Mage does.
    assert engine.base_mana(8, 90) > 0
    with pytest.raises(CharacterSourceError, match="no row for level"):
        engine.base_mana(8, 999)


def test_base_stats_add_the_race_modifier(character_resolver):
    orc = character_resolver.resolve(2, 6, 90, 250)
    human = character_resolver.resolve(1, 6, 90, 250)
    # The fixture gives Orc +8 str / -4 agi and Human +5 str / -2 agi.
    assert orc.base.stats[STAT_STRENGTH] - human.base.stats[STAT_STRENGTH] == 3
    assert orc.base.stats[STAT_AGILITY] - human.base.stats[STAT_AGILITY] == -2


def test_base_stats_are_class_and_level_keyed_not_race_keyed(character_resolver):
    # Removing the race modifier must leave two races identical.
    a = character_resolver.resolve(11, 6, 90, 250)   # race 11, zero modifier
    b = character_resolver.resolve(11, 6, 80, 250)
    assert a.base.stats != b.base.stats               # level matters
    c = character_resolver.resolve(11, 1, 90, 71)
    assert a.base.stats != c.base.stats               # class matters


def test_create_health_is_zero(character_resolver):
    character = character_resolver.resolve(1, 6, 90, 250)
    assert character.base.create_health == 0
    assert any(p["step"] == "create-health" for p in character.base.provenance)


def test_base_armor_is_twice_create_agility(character_resolver):
    character = character_resolver.resolve(1, 6, 90, 250)
    assert character.base.base_armor == character.base.stats[STAT_AGILITY] * 2


def test_base_stat_table_validation(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(CharacterSourceError, match="not valid JSON"):
        BaseStatTable.from_json_file(path)

    path.write_text(json.dumps({"nope": 1}), encoding="utf-8")
    with pytest.raises(CharacterSourceError, match="class_level_stats"):
        BaseStatTable.from_json_file(path)

    path.write_text(json.dumps({"class_level_stats": {"1": {"90": [1, 2, 3]}}}),
                    encoding="utf-8")
    with pytest.raises(CharacterSourceError, match="5 integers"):
        BaseStatTable.from_json_file(path)


def test_missing_class_or_level_fails_closed():
    table = BaseStatTable(class_level_stats={1: {90: (1, 2, 3, 4, 5)}})
    assert table.lookup(1, 90, 1) == (1, 2, 3, 4, 5)
    with pytest.raises(MissingBaseStats, match="no class_level_stats for class"):
        table.lookup(2, 90, 1)
    with pytest.raises(MissingBaseStats, match="level"):
        table.lookup(1, 80, 1)


def test_spec_class_mismatch_fails_closed(character_resolver):
    with pytest.raises(CharacterSourceError, match="belongs to class"):
        character_resolver.resolve(1, 6, 90, 269)   # Windwalker Monk on a DK
