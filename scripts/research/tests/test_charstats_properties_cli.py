"""Property/invariant tests and CLI behaviour for the character-stat tool."""

from __future__ import annotations

import json

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from charstats import CharacterSourceError
from charstats.character import Contributions
from charstats.cli import main
from charstats.derived import StatStage
from charstats.identity import (
    MAX_STATS,
    STAT_AGILITY,
    STAT_INTELLECT,
    STAT_STAMINA,
    STAT_STRENGTH,
)
from charstats.mastery import build_profile
from charstats.primary import COMBINED_ITEM_MODS, ITEM_MOD_TO_STATS

SNAPSHOT = pytest.mark.snapshot
NO_SCOPE_CHECK = settings(
    max_examples=50, deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture])


# -- properties over the stat pipeline ------------------------------------

@given(create=st.integers(min_value=0, max_value=100000),
       supplied=st.integers(min_value=-5000, max_value=100000))
@settings(max_examples=200, deadline=None)
def test_flat_contributions_are_additive_before_truncation(create, supplied):
    stage = StatStage(stat=STAT_STRENGTH, create_value=create,
                      total_flat=float(supplied))
    assert stage.value() == pytest.approx(create + supplied)
    assert stage.rounded() == int(create + supplied)


@given(create=st.integers(min_value=0, max_value=100000),
       pct=st.floats(min_value=1.0, max_value=2.0, allow_nan=False))
@settings(max_examples=200, deadline=None)
def test_total_pct_never_lowers_a_positive_stat(create, pct):
    stage = StatStage(stat=STAT_STAMINA, create_value=create, total_pct=pct)
    assert stage.value() >= create - 1e-9


@given(mastery=st.floats(min_value=0.0, max_value=200.0, allow_nan=False),
       base=st.floats(min_value=-100.0, max_value=100.0, allow_nan=False),
       coef=st.floats(min_value=-10.0, max_value=10.0, allow_nan=False))
@settings(max_examples=200, deadline=None)
def test_mastery_amount_is_affine_in_mastery(mastery, base, coef):
    from charstats.mastery import MasteryEffect
    effect = MasteryEffect(0, 6, 108, "SPELL_AURA_ADD_PCT_MODIFIER", base, coef,
                           0, 0)
    assert effect.amount_at(mastery) == pytest.approx(base + mastery * coef)
    assert effect.amount_at(0.0) == pytest.approx(base)


# -- snapshot invariants ---------------------------------------------------

@SNAPSHOT
def test_resolution_is_deterministic(character_resolver):
    for spec in character_resolver.identity.specs()[:10]:
        a = character_resolver.resolve(11, spec.class_id, 90, spec.spec_id)
        b = character_resolver.resolve(11, spec.class_id, 90, spec.spec_id)
        assert a.to_dict() == b.to_dict()


@SNAPSHOT
def test_resolution_serialises_round_trip(character_resolver):
    character = character_resolver.resolve(
        11, 6, 90, 250, Contributions.from_item_mods({71: 500}, {"Mastery": 100}))
    payload = json.loads(json.dumps(character.to_dict(), default=str))
    assert payload["max_health"] == character.max_health
    assert len(payload["stats"]) == MAX_STATS
    assert payload["caveat"]


@SNAPSHOT
def test_every_mastery_spell_root_resolves_to_a_source_row(character_resolver):
    spell_names = character_resolver.tables("SpellName").by("ID")
    for spec in character_resolver.identity.specs():
        for spell_id in spec.mastery_spell_ids:
            if spell_id:
                assert spell_id in spell_names, spec.name


@SNAPSHOT
def test_specialization_spells_dangling_references_are_pinned(character_resolver):
    """13 SpecializationSpells rows point at a SpellID with no SpellName row.

    That is a real integrity gap in this snapshot, not a tooling bug, so it is
    measured rather than asserted away.  A change in the count means the data
    changed and the research document needs revisiting.
    """
    spell_names = character_resolver.tables("SpellName").by("ID")
    rows = character_resolver.tables("SpecializationSpells")
    dangling = [int(r["SpellID"]) for r in rows
                if int(r["SpellID"]) not in spell_names]
    assert len(dangling) == 13
    assert len(set(dangling)) == 12


@SNAPSHOT
def test_every_mastery_spell_resolves_to_a_source_row(character_resolver):
    """Unlike spec passives, every mastery root does resolve."""
    spell_names = character_resolver.tables("SpellName").by("ID")
    for spec in character_resolver.identity.specs():
        for spell_id in spec.mastery_spell_ids:
            if spell_id:
                assert spell_id in spell_names, f"{spec.name} {spell_id}"


@SNAPSHOT
def test_every_armor_specialization_belongs_to_the_spec_that_grants_it(
        character_resolver):
    spec_spells = character_resolver.tables("SpecializationSpells").group("SpecID")
    for entry in character_resolver.acquisition.armor_specializations():
        granted = {int(r["SpellID"]) for r in spec_spells.get(entry.spec_id, ())}
        assert entry.spell_id in granted


@SNAPSHOT
def test_rating_conversion_is_monotonic_in_the_rating(character_resolver):
    """Proved by the diminishing curves themselves: they are non-decreasing."""
    previous = -1.0
    for amount in (0, 1000, 5000, 20000, 50000):
        character = character_resolver.resolve(
            11, 6, 90, 250, Contributions(ratings={"Mastery": amount}))
        assert character.mastery_value >= previous
        previous = character.mastery_value


@SNAPSHOT
def test_combined_item_mods_never_reduce_a_stat(character_resolver):
    base = character_resolver.resolve(11, 10, 90, 269)
    for item_mod in sorted(COMBINED_ITEM_MODS):
        boosted = character_resolver.resolve(
            11, 10, 90, 269, Contributions.from_item_mods({item_mod: 777}))
        for stat in range(MAX_STATS):
            assert boosted.stat(stat) >= base.stat(stat)
        for stat in ITEM_MOD_TO_STATS[item_mod]:
            assert boosted.stat(stat) == base.stat(stat) + 777


# -- CLI --------------------------------------------------------------------

@SNAPSHOT
def test_cli_naked_requires_base_stats(capsys):
    from charstats import MissingBaseStats
    with pytest.raises(MissingBaseStats):
        main(["naked", "--race-id", "1", "--class-id", "6", "--spec-id", "250"])


@SNAPSHOT
def test_cli_naked_with_base_stats(capsys, tmp_path):
    from conftest import FIXTURES
    assert main(["--base-stats", str(FIXTURES / "synthetic_base_stats.json"),
                 "--json", "naked", "--race", "Orc", "--class", "Death Knight",
                 "--spec", "Blood", "--level", "90"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["primary_stat_routing"]["primary_stat_name"] == "Strength"
    assert payload["max_health"] > 0


@SNAPSHOT
def test_cli_stats_folds_supplied_contributions(capsys):
    from conftest import FIXTURES
    assert main(["--base-stats", str(FIXTURES / "synthetic_base_stats.json"),
                 "--json", "stats", "--race-id", "1", "--class-id", "10",
                 "--spec", "Mistweaver", "--intellect", "5000",
                 "--mastery-rating", "9000", "--haste-rating", "4000"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["spell_power"] >= 5000
    mastery = next(c for c in payload["ratings"] if c["rating"] == "Mastery")
    assert mastery["amount"] == 9000
    assert 0 < mastery["final_percent"] < mastery["linear_percent"]


@SNAPSHOT
def test_cli_stats_consumes_a_gearing_loadout_payload(capsys, tmp_path):
    from conftest import FIXTURES
    from gearing.cli import main as gear_main
    loadout = tmp_path / "loadout.json"
    loadout.write_text(json.dumps({
        "player_level": 90, "chr_spec_id": 269,
        "items": [{"item_id": i, "context": 6}
                  for i in (271517, 271518, 271519, 271520, 271522)]}),
        encoding="utf-8")
    gear_out = tmp_path / "gear.json"
    assert gear_main(["--json", "loadout", "--json-file", str(loadout),
                      "--output", str(gear_out)]) == 0
    assert main(["--base-stats", str(FIXTURES / "synthetic_base_stats.json"),
                 "--json", "stats", "--race-id", "1", "--class-id", "10",
                 "--spec", "Windwalker", "--gear-json", str(gear_out)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["contributions"]["stats"]["Agility"] > 0
    assert payload["contributions"]["ratings"]["Mastery"] > 0
    assert "gearing loadout payload" in payload["contributions"]["source"]


@SNAPSHOT
def test_cli_rejects_a_non_loadout_gear_payload(tmp_path):
    from conftest import FIXTURES
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"nope": 1}), encoding="utf-8")
    with pytest.raises(CharacterSourceError, match="not a gearing"):
        main(["--base-stats", str(FIXTURES / "synthetic_base_stats.json"),
              "stats", "--race-id", "1", "--class-id", "10",
              "--spec", "Windwalker", "--gear-json", str(bad)])


@SNAPSHOT
def test_cli_specs_mastery_routing_census(capsys):
    for argv in (["specs"], ["mastery", "--class-id", "6"],
                 ["routing", "--class-id", "6"], ["census"],
                 ["corpus", "--format", "csv"]):
        assert main(argv) == 0
        assert capsys.readouterr().out.strip()


@SNAPSHOT
def test_cli_acquisition_reports_all_three_families(capsys):
    assert main(["--json", "acquisition", "--race", "Orc",
                 "--class", "Death Knight", "--spec", "Blood",
                 "--level", "90"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["specialization_spells"]
    assert payload["mastery_spells"]
    assert payload["armor_specializations"]
    assert payload["racial_abilities"]
    assert "acquisition only" in payload["note"]


@SNAPSHOT
def test_cli_requires_class_and_race():
    with pytest.raises(CharacterSourceError, match="--class"):
        main(["naked", "--race-id", "1"])
    with pytest.raises(CharacterSourceError, match="--race"):
        main(["naked", "--class-id", "6"])


@SNAPSHOT
def test_cli_missing_tables_fails_closed(tmp_path):
    from gearing import SourceError
    with pytest.raises(SourceError, match="table directory not found"):
        main(["--tables", str(tmp_path / "absent"), "census"])
