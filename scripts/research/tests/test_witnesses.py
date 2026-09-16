"""The committed verification corpus of real current items.

Every expected value in ``fixtures/witnesses.json`` was derived by
``fixtures/derive_witnesses.py``, which reads the raw source rows and redoes the
TrinityCore arithmetic without importing the package under test.
"""

from __future__ import annotations

import pytest

from gearing.enchants import ENCHANT_STAT
from gearing.resolver import GemSlot, Variant

pytestmark = pytest.mark.snapshot


def _resolve(resolver, witness, player_level):
    variant = Variant(label=witness["role"], context=witness["context"],
                      origin="verification corpus")
    return resolver.resolve(witness["item_id"], variant,
                            player_level=player_level,
                            current_build_patch=witness.get("squish_patch"))


def _ids(witnesses):
    return [f"{w['item_id']}-{w['context']}-{w['effective_item_level']}"
            for w in witnesses["items"]]


def test_corpus_covers_the_required_paths(witnesses):
    roles = " ".join(w["role"] for w in witnesses["items"]).lower()
    for required in ("tier", "trinket", "weapon", "shield", "socketed",
                     "m+ pool", "raid", "squish"):
        assert required in roles, f"corpus is missing a {required} witness"
    assert witnesses["gems"] and witnesses["enchants"]
    assert witnesses["item_sets"] and witnesses["upgrade_tracks"]
    assert witnesses["item_effects"] and witnesses["curves"]


def test_corpus_is_pinned_to_this_snapshot(witnesses, tables):
    # A data refresh must invalidate the corpus loudly, not silently pass.
    assert witnesses["data_build"] == "12.1.0.69497"
    assert len(witnesses["items"]) >= 12


@pytest.mark.parametrize("index", range(15))
def test_item_witness(resolver, witnesses, index):
    witness = witnesses["items"][index]
    got = _resolve(resolver, witness, witnesses["player_level"])

    assert got.effective_item_level == witness["effective_item_level"], witness["role"]
    assert got.quality == witness["quality"], witness["role"]
    assert got.inventory_type == witness["inventory_type"]
    assert got.class_id == witness["class_id"]
    assert got.subclass_id == witness["subclass_id"]

    if "expected_bonus_lists" in witness:
        assert got.applied_bonus_lists == witness["expected_bonus_lists"], \
            witness["role"]
    if "expected_sockets" in witness:
        assert got.sockets == witness["expected_sockets"]
    if witness.get("item_set_id"):
        assert got.item_set_id == witness["item_set_id"]

    expected_stats = {(s["index"], s["stat_type"]): s["value"]
                      for s in witness["stats"]}
    got_stats = {(s.stat_index, s.stat_type): s.final_value for s in got.stats}
    assert got_stats == expected_stats, witness["role"]

    assert got.armor == witness["armor"], witness["role"]

    if witness["weapon"] is None:
        assert got.weapon is None or got.weapon["dps"] == 0.0
    else:
        assert got.weapon is not None
        assert got.weapon["dps"] == pytest.approx(witness["weapon"]["dps"])
        assert got.weapon["min_damage"] == pytest.approx(
            witness["weapon"]["min_damage"])
        assert got.weapon["max_damage"] == pytest.approx(
            witness["weapon"]["max_damage"])
        assert got.weapon["weapon_attack_power"] == \
            witness["weapon"]["weapon_attack_power"]


@pytest.mark.parametrize("index", range(15))
def test_item_witness_multiplier_ingredients(resolver, witnesses, index):
    """The intermediate ingredients must match too, not just the final number."""
    witness = witnesses["items"][index]
    got = _resolve(resolver, witness, witnesses["player_level"])
    for stat in got.stats:
        if stat.rand_prop_points is None:
            continue
        assert stat.rand_prop_points == pytest.approx(
            witness["rand_prop_points"]), witness["role"]
        assert stat.rand_prop_index == witness["rand_prop_index"]
        assert stat.rand_prop_quality_column == witness["rand_prop_column"]
        if stat.combat_ratings_mult_by_ilvl is not None:
            assert stat.combat_ratings_mult_by_ilvl == pytest.approx(
                witness["combat_ratings_mult_by_ilvl"])
        if stat.stamina_mult_by_ilvl is not None:
            assert stat.stamina_mult_by_ilvl == pytest.approx(
                witness["stamina_mult_by_ilvl"])


def test_curve_witnesses(resolver, witnesses):
    for curve in witnesses["curves"]:
        curve_id = curve["curve_id"]
        assert resolver.curves.curve_type(curve_id) == curve["curve_type"]
        assert resolver.curves.mode(curve_id) == curve["interpolation"]
        for sample in curve["samples"]:
            assert resolver.curves.value_at(curve_id, sample["x"]) == \
                pytest.approx(sample["y"]), f"curve {curve_id} at {sample['x']}"


def test_item_set_witnesses(resolver, witnesses):
    for expected in witnesses["item_sets"]:
        item_set = resolver.sets.get(expected["item_set_id"])
        assert item_set.name == expected["name"]
        assert sorted(item_set.member_item_ids) == sorted(expected["member_item_ids"])
        assert list(item_set.thresholds) == expected["thresholds"]
        got = {(s.threshold, s.spell_id, s.chr_spec_id) for s in item_set.spells}
        want = {(s["threshold"], s["spell_id"], s["chr_spec_id"])
                for s in expected["spells"]}
        assert got == want


def test_set_threshold_acquisition_for_a_full_tier_set(resolver, witnesses):
    expected = witnesses["item_sets"][0]
    equipped = [(item_id, expected["item_set_id"])
                for item_id in expected["member_item_ids"][:4]]
    bonuses = resolver.sets.satisfied_bonuses(equipped, chr_spec_id=269)
    assert sorted(b.threshold for b in bonuses) == [2, 4]
    assert all(b.item_set_id == expected["item_set_id"] for b in bonuses)


def test_gem_witnesses(resolver, witnesses):
    for expected in witnesses["gems"]:
        proto = resolver.items.get(expected["gem_item_id"])
        got = resolver.enchants.describe_gem(proto, witnesses["player_level"])
        assert got["gem_properties_id"] == expected["gem_properties_id"]
        assert got["enchant_id"] == expected["enchant_id"]
        assert got["gem_type_mask"] == expected["gem_type_mask"]
        effects = {e["slot"]: e for e in got["enchant"]["effects"]}
        for want in expected["effects"]:
            effect = effects[want["slot"]]
            assert effect["type"] == want["type"]
            if "stat_type" in want:
                assert effect["stat_type"] == want["stat_type"]
            if "spell_id" in want:
                assert effect["spell_id"] == want["spell_id"]
            if "resolved_amount" in want:
                assert effect["resolved_amount"] == want["resolved_amount"]


def test_enchant_witnesses(resolver, witnesses):
    for expected in witnesses["enchants"]:
        enchant = resolver.enchants.resolve(expected["enchant_id"],
                                            witnesses["player_level"])
        assert enchant.scaling_class == expected["scaling_class"]
        effects = {e.slot: e for e in enchant.effects}
        for want in expected["effects"]:
            effect = effects[want["slot"]]
            assert effect.type == want["type"]
            if "stat_type" in want:
                assert effect.stat_type == want["stat_type"]
            if "spell_id" in want:
                assert effect.spell_id == want["spell_id"]
            if effect.type == ENCHANT_STAT:
                assert effect.resolved_amount is not None
                assert effect.resolved_amount >= 1


def test_item_effect_witnesses(resolver, witnesses):
    for expected in witnesses["item_effects"]:
        got = resolver.resolve(expected["item_id"])
        roots = {(e.trigger_type, e.spell_id) for e in got.effects}
        for want in expected["roots"]:
            assert (want["trigger_type"], want["spell_id"]) in roots, \
                f"{expected['name']} is missing {want}"


def test_upgrade_track_witness(resolver, witnesses):
    expected = witnesses["upgrade_tracks"][0]
    steps = [s for s in resolver.discover_upgrade_steps(expected["item_id"])
             if s["item_bonus_list_group_id"] == expected["item_bonus_list_group_id"]]
    assert [s["sequence_value"] for s in steps] == expected["sequence_values"]
    assert [s["bonus_list_id"] for s in steps] == expected["bonus_list_ids"]
    levels = []
    for step in steps:
        variant = Variant(label=f"seq{step['sequence_value']}",
                          extra_bonus_lists=(step["bonus_list_id"],),
                          origin="upgrade track")
        levels.append(resolver.resolve(expected["item_id"], variant,
                                       player_level=witnesses["player_level"],
                                       auto_bonus_lists=False
                                       ).effective_item_level)
    assert levels == expected["expected_item_levels"]


def test_socketed_witness_accepts_a_matching_gem(resolver, witnesses):
    ring = next(w for w in witnesses["items"] if "socketed" in w["role"])
    gem = witnesses["gems"][0]
    got = resolver.resolve(
        ring["item_id"],
        Variant(label="socketed", context=ring["context"], origin="corpus"),
        player_level=witnesses["player_level"],
        gems=[GemSlot(socket_index=0, gem_item_id=gem["gem_item_id"])])
    assert got.gems[0]["fits_socket"] is True
    assert got.gems[0]["socket_color"] == ring["expected_sockets"][0]
    # Modern gems carry no BonusListId/BonusListCurve effect, so they add no
    # item level to the host item.
    assert got.gems[0].get("host_item_level_bonus", 0) == 0
    assert got.effective_item_level == ring["effective_item_level"]
