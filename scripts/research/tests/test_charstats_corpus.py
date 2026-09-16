"""The class/spec corpus, routing, mastery, acquisition and integration."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from charstats import CharacterSourceError
from charstats.acquisition import (
    SPELL_ATTR8_MASTERY_AFFECTS_POINTS,
    SPELL_ATTR8_REQUIRES_EQUIPPED_INV_TYPES,
    SPELL_AURA_MOD_TOTAL_STAT_PERCENTAGE,
    ARMOR_SPECIALIZATION_SLOTS,
)
from charstats.census import census
from charstats.character import Contributions
from charstats.identity import (
    STAT_AGILITY,
    STAT_INTELLECT,
    STAT_NAMES,
    STAT_STAMINA,
    STAT_STRENGTH,
)
from charstats.mastery import (
    CATEGORY_ORDINARY,
    CATEGORY_SCRIPTED,
    CATEGORY_UNSUPPORTED,
    build_profile,
)
from charstats.primary import (
    COMBINED_ITEM_MODS,
    ITEM_MOD_TO_STATS,
    MOD_AGI_INT,
    MOD_AGI_STR,
    MOD_AGI_STR_INT,
    MOD_STR_INT,
    routing_for,
)

pytestmark = pytest.mark.snapshot

RESEARCH_ROOT = Path(__file__).resolve().parents[1]
BASE_STATS_FIXTURE = RESEARCH_ROOT / "tests" / "fixtures" / "synthetic_base_stats.json"


# -- every class/spec at max level -----------------------------------------

def test_every_spec_constructs_naked_at_max_level(character_resolver):
    specs = character_resolver.identity.specs()
    assert len(specs) >= 38
    for spec in specs:
        character = character_resolver.resolve(11, spec.class_id, 90,
                                               spec.spec_id)
        assert character.level == 90
        assert character.max_health > 0
        assert character.stat(STAT_STAMINA) > 0
        assert character.armor > 0
        assert character.routing.primary_stat in (
            STAT_STRENGTH, STAT_AGILITY, STAT_INTELLECT)
        # A naked character has no ratings, so every conversion is zero.
        assert all(c["amount"] == 0 for c in character.rating_conversions)
        assert character.mastery_value == 0.0


def test_naked_attack_power_follows_the_class_coefficients(character_resolver):
    for spec in character_resolver.identity.specs():
        klass = character_resolver.identity.klass(spec.class_id)
        character = character_resolver.resolve(11, spec.class_id, 90,
                                               spec.spec_id)
        expected = (character.stat(STAT_STRENGTH) * klass.attack_power_per_strength
                    + character.stat(STAT_AGILITY) * klass.attack_power_per_agility)
        assert character.attack_power == int(expected), spec.name


def test_naked_spell_power_is_gated_on_the_primary_stat(character_resolver):
    for spec in character_resolver.identity.specs():
        character = character_resolver.resolve(11, spec.class_id, 90,
                                               spec.spec_id)
        if character.routing.primary_stat == STAT_INTELLECT:
            assert character.spell_power == character.stat(STAT_INTELLECT)
        else:
            assert character.spell_power == 0


def test_naked_health_is_exactly_stamina_times_the_ratio(character_resolver):
    for spec in character_resolver.identity.specs()[:8]:
        character = character_resolver.resolve(11, spec.class_id, 90,
                                               spec.spec_id)
        assert character.max_health == int(
            character.stat(STAT_STAMINA) * character.hp_per_sta)


def test_no_specialization_disables_mastery(character_resolver):
    character = character_resolver.resolve(11, 6, 90, None)
    assert character.mastery is None
    assert character.mastery_value == 0.0
    assert any("CanUseMastery" in w for w in character.warnings)


# -- combined ItemModType routing -------------------------------------------

def test_combined_item_mods_grant_every_named_stat():
    assert ITEM_MOD_TO_STATS[MOD_AGI_STR_INT] == (
        STAT_AGILITY, STAT_STRENGTH, STAT_INTELLECT)
    assert ITEM_MOD_TO_STATS[MOD_AGI_STR] == (STAT_AGILITY, STAT_STRENGTH)
    assert ITEM_MOD_TO_STATS[MOD_AGI_INT] == (STAT_AGILITY, STAT_INTELLECT)
    assert ITEM_MOD_TO_STATS[MOD_STR_INT] == (STAT_STRENGTH, STAT_INTELLECT)
    assert COMBINED_ITEM_MODS == {71, 72, 73, 74}


def test_combined_item_mod_adds_the_full_amount_to_each_stat(character_resolver):
    contributions = Contributions.from_item_mods({MOD_AGI_STR_INT: 1000})
    assert contributions.stats == {STAT_AGILITY: 1000, STAT_STRENGTH: 1000,
                                   STAT_INTELLECT: 1000}
    base = character_resolver.resolve(11, 6, 90, 250)
    withstat = character_resolver.resolve(11, 6, 90, 250, contributions)
    for stat in (STAT_AGILITY, STAT_STRENGTH, STAT_INTELLECT):
        assert withstat.stat(stat) - base.stat(stat) == 1000


def test_identical_gear_routes_differently_per_spec(character_resolver):
    """The information-dropping boundary, as a test."""
    contributions = Contributions.from_item_mods({MOD_AGI_INT: 1000})
    windwalker = character_resolver.resolve(11, 10, 90, 269, contributions)
    mistweaver = character_resolver.resolve(11, 10, 90, 270, contributions)
    # Same class, same gear, same stats granted.
    assert windwalker.stat(STAT_AGILITY) == mistweaver.stat(STAT_AGILITY)
    assert windwalker.stat(STAT_INTELLECT) == mistweaver.stat(STAT_INTELLECT)
    # Different primary stat -> different spell power.
    assert windwalker.spell_power == 0
    assert mistweaver.spell_power == mistweaver.stat(STAT_INTELLECT)
    # Same attack power, because the coefficients are class-level.
    assert windwalker.attack_power == mistweaver.attack_power


@pytest.mark.parametrize("item_mod", sorted(COMBINED_ITEM_MODS))
def test_routing_reports_which_granted_stats_contribute(character_resolver, item_mod):
    for spec in character_resolver.identity.specs():
        klass = character_resolver.identity.klass(spec.class_id)
        detail = routing_for(klass, spec).effective_stats(item_mod)
        granted = {d["stat_name"] for d in detail["detail"]}
        assert granted == {STAT_NAMES[s] for s in ITEM_MOD_TO_STATS[item_mod]}
        for entry in detail["detail"]:
            assert entry["why"]


def test_every_spec_has_at_least_one_contributing_stat(character_resolver):
    for spec in character_resolver.identity.specs():
        klass = character_resolver.identity.klass(spec.class_id)
        detail = routing_for(klass, spec).effective_stats(MOD_AGI_STR_INT)
        assert any(d["contributes"] for d in detail["detail"]), spec.name


# -- mastery ----------------------------------------------------------------

def test_every_spec_gets_a_mastery_classification(character_resolver):
    categories = set()
    for spec in character_resolver.identity.specs():
        profile = build_profile(spec,
                                character_resolver.acquisition.mastery_spells(spec))
        assert profile.category in {CATEGORY_ORDINARY, CATEGORY_SCRIPTED,
                                    CATEGORY_UNSUPPORTED}
        assert profile.reason
        assert profile.spells
        categories.add(profile.category)
    # All three buckets must be populated, or the classifier is degenerate.
    assert categories == {CATEGORY_ORDINARY, CATEGORY_SCRIPTED,
                          CATEGORY_UNSUPPORTED}


def test_every_mastery_spell_sets_the_attribute(character_resolver):
    for spec in character_resolver.identity.specs():
        for spell in character_resolver.acquisition.mastery_spells(spec):
            assert spell["mastery_affects_points"], \
                f"{spec.name} mastery {spell['spell_id']} lacks " \
                f"SPELL_ATTR8_MASTERY_AFFECTS_POINTS"
            assert spell["attributes_8"] & SPELL_ATTR8_MASTERY_AFFECTS_POINTS


def test_mastery_effect_amount_is_base_plus_mastery_times_coefficient(
        character_resolver):
    spec = character_resolver.identity.find_spec(6, "Blood")
    profile = build_profile(spec,
                            character_resolver.acquisition.mastery_spells(spec))
    effect = next(e for s in profile.spells for e in s.effects
                  if e.participates_in_mastery)
    assert effect.amount_at(0.0) == pytest.approx(effect.base_points)
    assert effect.amount_at(10.0) == pytest.approx(
        effect.base_points + 10.0 * effect.bonus_coefficient)


def test_mastery_value_is_the_rating_conversion(character_resolver):
    contributions = Contributions(ratings={"Mastery": 9000})
    character = character_resolver.resolve(11, 6, 90, 250, contributions)
    conversion = next(c for c in character.rating_conversions
                      if c["rating"] == "Mastery")
    assert character.mastery_value == pytest.approx(conversion["final_percent"])
    assert conversion["linear_percent"] > conversion["final_percent"] > 0


def test_blood_dk_mastery_is_script_dependent(character_resolver):
    spec = character_resolver.identity.find_spec(6, "Blood")
    profile = build_profile(spec,
                            character_resolver.acquisition.mastery_spells(spec))
    assert profile.category == CATEGORY_SCRIPTED
    assert "SPELL_AURA_DUMMY" in profile.reason


def test_mistweaver_mastery_is_script_dependent(character_resolver):
    spec = character_resolver.identity.find_spec(10, "Mistweaver")
    profile = build_profile(spec,
                            character_resolver.acquisition.mastery_spells(spec))
    assert profile.category == CATEGORY_SCRIPTED


def test_at_least_one_mastery_is_plain_ordinary(character_resolver):
    ordinary = [s for s in character_resolver.identity.specs()
                if build_profile(
                    s, character_resolver.acquisition.mastery_spells(s)
                ).category == CATEGORY_ORDINARY]
    assert len(ordinary) >= 5


# -- acquisition -------------------------------------------------------------

def test_spec_passives_come_from_specialization_spells(character_resolver):
    spec = character_resolver.identity.find_spec(6, "Blood")
    passives = character_resolver.acquisition.spec_spells(spec.spec_id, 90)
    assert passives
    ids = {p.spell_id for p in passives}
    # The spec's mastery spell is itself a SpecializationSpells row.
    assert spec.mastery_spell_ids[0] in ids


def test_spec_passives_are_level_gated(character_resolver):
    spec = character_resolver.identity.find_spec(6, "Blood")
    at_90 = character_resolver.acquisition.spec_spells(spec.spec_id, 90)
    at_1 = character_resolver.acquisition.spec_spells(spec.spec_id, 1)
    assert len(at_1) <= len(at_90)


def test_armor_specializations_are_discovered_not_hardcoded(character_resolver):
    found = character_resolver.acquisition.armor_specializations()
    assert len(found) >= 20
    for entry in found:
        assert entry.armor_subclasses
        assert entry.stat_names
        assert entry.percent > 0.0
        assert entry.requires_all_armor_slots
        # The required inventory types must cover every slot the consumer walks.
        assert set(entry.required_inventory_types) >= {1, 3, 5, 6, 7, 8, 9, 10}
    assert len(ARMOR_SPECIALIZATION_SLOTS) == 8


def test_armor_specialization_amount_is_source_derived(character_resolver):
    """The 5% is read from EffectBasePointsF, never hardcoded."""
    found = character_resolver.acquisition.armor_specializations()
    assert {round(e.percent, 3) for e in found} == {5.0}


def test_armor_specialization_multiplies_the_named_stat(character_resolver):
    spec = character_resolver.identity.find_spec(6, "Blood")
    plain = character_resolver.resolve(11, 6, 90, spec.spec_id)
    boosted = character_resolver.resolve(11, 6, 90, spec.spec_id,
                                         apply_armor_specialization=True)
    applied = boosted.armor_specialization_applied
    assert applied is not None
    assert applied.stat_names == ("Stamina",)
    assert boosted.stat(STAT_STAMINA) == int(
        plain.stat(STAT_STAMINA) * (1.0 + applied.percent / 100.0))
    # Other stats are untouched.
    assert boosted.stat(STAT_STRENGTH) == plain.stat(STAT_STRENGTH)


def test_racial_abilities_are_race_and_class_gated(character_resolver):
    orc_dk = character_resolver.acquisition.racial_abilities(2, 6)
    human_dk = character_resolver.acquisition.racial_abilities(1, 6)
    assert orc_dk and human_dk
    orc_spells = {r.spell_id for r in orc_dk}
    human_spells = {r.spell_id for r in human_dk}
    assert orc_spells != human_spells
    # 20572 Blood Fury is an Orc racial.
    assert 20572 in orc_spells
    assert 20572 not in human_spells


def test_racial_abilities_fail_closed_on_a_bad_race(character_resolver):
    with pytest.raises(CharacterSourceError):
        character_resolver.acquisition.racial_abilities(0)


# -- census ------------------------------------------------------------------

def test_census_reports_the_unsupported_boundary(character_resolver):
    payload = census(character_resolver)
    assert payload["populations"]["playable specializations (excluding Initial)"] >= 38
    assert payload["populations"]["armour-specialization passives discovered"] >= 20
    assert "player_classlevelstats" in payload["unsupported"]["base primary stats"]
    assert sum(payload["mastery_categories"].values()) == \
        payload["populations"]["playable specializations (excluding Initial)"]
    assert set(payload["primary_stat_routing_counts"]) <= {
        "Strength", "Agility", "Intellect"}


# -- integration proof --------------------------------------------------------

def test_gear_to_character_end_to_end(tmp_path):
    """Runs the committed research-only proof and checks its boundary payload."""
    out = tmp_path / "proof.json"
    result = subprocess.run(
        [sys.executable, str(RESEARCH_ROOT / "tools" / "gear_to_character.py"),
         "--base-stats", str(BASE_STATS_FIXTURE), "--json",
         "--output", str(out)],
        capture_output=True, text=True, timeout=600)
    assert result.returncode == 0, result.stderr
    payload = json.loads(out.read_text())
    cases = {c["label"]: c for c in payload["cases"]}
    assert len(cases) == 4

    for case in cases.values():
        boundary = case["boundary_payload"]
        # The character side must receive stats, ratings, armor and set roots --
        # and nothing that identifies an item.
        assert set(boundary) == {"stats", "ratings", "armor",
                                 "item_set_spell_roots"}
        assert boundary["ratings"]
        assert boundary["armor"] > 0
        assert boundary["item_set_spell_roots"]
        assert all(r["item_set_id"] for r in boundary["item_set_spell_roots"])
        blob = json.dumps(boundary)
        for forbidden in ("item_id", "bonus_list", "item_level", "curve"):
            assert forbidden not in blob

    strength = next(c for c in cases.values() if "Blood" in c["label"])
    agility = next(c for c in cases.values() if "Windwalker" in c["label"])
    healer = next(c for c in cases.values() if "Mistweaver" in c["label"])

    assert strength["character"]["primary_stat_routing"]["primary_stat_name"] == "Strength"
    assert agility["character"]["primary_stat_routing"]["primary_stat_name"] == "Agility"
    assert healer["character"]["primary_stat_routing"]["primary_stat_name"] == "Intellect"

    # Same gear, same class: the stat/rating/armor half of the payload is
    # identical, because nothing item-side knows about the spec.
    assert agility["gear"]["item_ids"] == healer["gear"]["item_ids"]
    for key in ("stats", "ratings", "armor"):
        assert agility["boundary_payload"][key] == healer["boundary_payload"][key]
    assert agility["character"]["spell_power"] == 0
    assert healer["character"]["spell_power"] > 0
    # ...and the set grants different spells to each spec.
    assert ({r["spell_id"] for r in agility["boundary_payload"]["item_set_spell_roots"]}
            != {r["spell_id"]
                for r in healer["boundary_payload"]["item_set_spell_roots"]})


def test_gear_to_character_requires_base_stats():
    result = subprocess.run(
        [sys.executable, str(RESEARCH_ROOT / "tools" / "gear_to_character.py")],
        capture_output=True, text=True, timeout=120)
    assert result.returncode != 0
    assert "--base-stats" in result.stderr
