"""Loadout parsing/aggregation and the CLI's fail-closed behaviour."""

from __future__ import annotations

import json

import pytest

from gearing import SourceError
from gearing.cli import main
from gearing.loadout import (
    Loadout,
    LoadoutEntry,
    parse_item_id_file,
    parse_item_id_spec,
    resolve_loadout,
)
from gearing.resolver import GemSlot


# -- input parsing ---------------------------------------------------------

def test_item_spec_forms():
    assert parse_item_id_spec("271519") == LoadoutEntry(item_id=271519)
    entry = parse_item_id_spec("271519:6")
    assert (entry.item_id, entry.context) == (271519, 6)
    entry = parse_item_id_spec("271519:6/12849,13335")
    assert entry.bonus_list_ids == (12849, 13335)


@pytest.mark.parametrize("spec", ["", "abc", "271519:x", "271519/abc"])
def test_bad_item_spec_fails_closed(spec):
    with pytest.raises(SourceError):
        parse_item_id_spec(spec)


def test_item_id_file_supports_comments(tmp_path):
    path = tmp_path / "items.txt"
    path.write_text("# a comment\n271519\n\n271520:6  # trailing\n", encoding="utf-8")
    assert parse_item_id_file(path) == [271519, 271520]


def test_item_id_file_rejects_garbage(tmp_path):
    path = tmp_path / "items.txt"
    path.write_text("271519\nnot-an-id\n", encoding="utf-8")
    with pytest.raises(SourceError, match="not an item id"):
        parse_item_id_file(path)


def test_loadout_json_shapes(tmp_path):
    path = tmp_path / "loadout.json"
    path.write_text(json.dumps({
        "player_level": 90, "chr_spec_id": 269,
        "items": [
            {"item_id": 271519, "context": 6, "slot": "head"},
            {"item_id": 270575, "gems": [241143]},
            {"item_id": 268213, "gems": [{"socket_index": 1,
                                          "gem_item_id": 241143,
                                          "bonus_list_ids": [1]}],
             "enchant_ids": [8157]},
        ]}), encoding="utf-8")
    loadout = Loadout.from_json_file(path)
    assert loadout.player_level == 90 and loadout.chr_spec_id == 269
    assert loadout.entries[0].label == "head"
    assert loadout.entries[1].gems == (GemSlot(socket_index=0,
                                               gem_item_id=241143),)
    assert loadout.entries[2].gems[0].socket_index == 1
    assert loadout.entries[2].enchant_ids == (8157,)


def test_loadout_json_bare_list(tmp_path):
    path = tmp_path / "loadout.json"
    path.write_text(json.dumps([{"item_id": 271519}]), encoding="utf-8")
    assert len(Loadout.from_json_file(path).entries) == 1


def test_loadout_rejects_unknown_keys(tmp_path):
    path = tmp_path / "loadout.json"
    path.write_text(json.dumps({"items": [{"item_id": 1, "ilvl": 300}]}),
                    encoding="utf-8")
    with pytest.raises(SourceError, match="unknown keys"):
        Loadout.from_json_file(path)


def test_loadout_requires_item_id(tmp_path):
    path = tmp_path / "loadout.json"
    path.write_text(json.dumps({"items": [{"context": 6}]}), encoding="utf-8")
    with pytest.raises(SourceError, match="missing item_id"):
        Loadout.from_json_file(path)


def test_loadout_rejects_invalid_json(tmp_path):
    path = tmp_path / "loadout.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(SourceError, match="not valid JSON"):
        Loadout.from_json_file(path)


# -- aggregation -----------------------------------------------------------

@pytest.mark.snapshot
def test_loadout_aggregates_stats_ratings_and_sets(resolver):
    entries = [LoadoutEntry(item_id=i, context=6)
               for i in (271517, 271518, 271519, 271520, 271522)]
    result = resolve_loadout(resolver, Loadout(entries=entries, chr_spec_id=269))
    assert result.stat_totals["Stamina"] > 0
    assert set(result.rating_totals) >= {"Mastery", "CritMelee"}
    assert sorted(b["threshold"] for b in result.set_bonuses) == [2, 4]
    payload = result.to_dict(resolver.ratings)
    assert "caveat" in payload
    assert payload["rating_conversions"]


@pytest.mark.snapshot
def test_loadout_enchant_stats_join_the_totals(resolver):
    plain = resolve_loadout(resolver, Loadout(
        entries=[LoadoutEntry(item_id=271519, context=6)]))
    enchanted = resolve_loadout(resolver, Loadout(
        entries=[LoadoutEntry(item_id=271519, context=6, enchant_ids=(8157,))]))
    assert enchanted.stat_totals["CritRating"] > plain.stat_totals["CritRating"]
    assert enchanted.rating_totals["CritMelee"] > plain.rating_totals["CritMelee"]


@pytest.mark.snapshot
def test_loadout_does_not_compute_character_stats(resolver):
    result = resolve_loadout(resolver, Loadout(
        entries=[LoadoutEntry(item_id=271519, context=6)]))
    payload = result.to_dict()
    for forbidden in ("health", "attack_power", "spell_power", "crit_chance"):
        assert forbidden not in payload


# -- CLI -------------------------------------------------------------------

@pytest.mark.snapshot
def test_cli_show_json(capsys):
    assert main(["--json", "show", "271519", "--context", "6"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["effective_item_level"] == 318


@pytest.mark.snapshot
def test_cli_items_and_file(tmp_path, capsys):
    path = tmp_path / "items.txt"
    path.write_text("271519\n271520\n", encoding="utf-8")
    assert main(["items", "268213:6", "--file", str(path),
                 "--format", "csv"]) == 0
    out = capsys.readouterr().out
    assert out.splitlines()[0].startswith("item_id,")
    assert len(out.strip().splitlines()) == 4


@pytest.mark.snapshot
def test_cli_seasons_requires_no_selector_but_says_so(capsys):
    assert main(["seasons"]) == 0
    out = capsys.readouterr().out
    assert "Activation is NOT derivable" in out


@pytest.mark.snapshot
def test_cli_raid_requires_a_season():
    with pytest.raises(SystemExit):
        main(["raid"])


@pytest.mark.snapshot
def test_cli_raid_rejects_an_unknown_season():
    with pytest.raises(SourceError, match="no availability condition"):
        main(["raid", "--season", "1"])


@pytest.mark.snapshot
def test_cli_latest_in_source_warns(capsys):
    assert main(["raid", "--season", "latest-in-source", "--tier-only"]) == 0
    captured = capsys.readouterr()
    assert "heuristic over source rows" in captured.err


@pytest.mark.snapshot
def test_cli_corpus_writes_json(tmp_path, capsys):
    out = tmp_path / "corpus.json"
    assert main(["corpus", "--season", "latest-in-source",
                 "--output", str(out)]) == 0
    payload = json.loads(out.read_text())
    assert payload["raid"]["item_count"] > 0
    assert payload["mythic_plus"]["item_count"] > 0
    assert payload["window"]["availability_condition_id"]


@pytest.mark.snapshot
def test_cli_unknown_item_fails_closed():
    with pytest.raises(SourceError, match="unknown ItemID"):
        main(["show", "999999999"])


@pytest.mark.snapshot
def test_cli_unknown_curve_fails_closed():
    with pytest.raises(SourceError, match="no CurvePoint rows"):
        main(["curve", "999999999"])


@pytest.mark.snapshot
def test_cli_unknown_enchant_fails_closed():
    with pytest.raises(SourceError, match="unknown SpellItemEnchantment"):
        main(["enchant", "999999999"])


@pytest.mark.snapshot
def test_cli_unknown_item_set_fails_closed():
    with pytest.raises(SourceError, match="ItemSet"):
        main(["item-set", "999999999"])


@pytest.mark.snapshot
def test_cli_missing_tables_directory_fails_closed(tmp_path):
    with pytest.raises(SourceError, match="table directory not found"):
        main(["--tables", str(tmp_path / "nope"), "census"])


@pytest.mark.snapshot
def test_cli_census_reports_unsupported_relationships(capsys):
    assert main(["--json", "census"]) == 0
    payload = json.loads(capsys.readouterr().out)
    unsupported = payload["unsupported"]
    assert unsupported["ItemBonus types with no BonusData::AddBonus branch"]
    assert unsupported["Curve.Type values with no DetermineCurveType case"] == [4, 5]
    assert unsupported["curves whose X is not monotonic in OrderIndex order"]
