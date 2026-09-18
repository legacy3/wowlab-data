"""Track F: real-data census, witnesses and corpus regeneration (snapshot 12.1.0.69497)."""

from __future__ import annotations

import json

import pytest

from aura_lifecycle import CORPORA, FailClosed, records
from aura_lifecycle import recipients as R


@pytest.fixture(scope="module")
def census(al_ctx):
    return R.census(al_ctx)


def test_player_population_matches_targeting_aura_map_census(census):
    """Same denominator as the targeting pass (35 aura-map effects on 28 current-player spells)."""
    p = census["by_population"]["player"]
    assert (p["effects"], p["spells"]) == (35, 28)
    assert p["by_pipeline"] == {"dynobj": 2, "unit-area": 33}


def test_no_current_player_periodic_area_effect(census):
    """Tick attribution has no current-player witness (AL-F-F-09); the all population has them."""
    assert census["by_population"]["player"]["periodic_effects"] == 0
    assert census["by_population"]["all"]["periodic_effects"] > 0


def test_passive_player_area_auras_lack_disable_while_dead(census):
    p = census["by_population"]["player"]
    assert p["by_flag"].get("passive") == 17
    assert p["by_flag"].get("disable_while_dead", 0) == 0
    assert p["passive_without_disable_while_dead"] == 17


def test_all_population_counts_are_consistent(census):
    a = census["by_population"]["all"]
    assert sum(a["by_pipeline"].values()) == a["effects"] == sum(a["by_type"].values())
    assert a["by_pipeline"]["dynobj"] == a["by_type"]["PERSISTENT_AREA_AURA"]


def test_devotion_aura_profile(al_ctx):
    prof = R.profile(al_ctx, 465)
    kinds = {(e["effect"], e["pipeline"]) for e in prof["effects"]}
    assert (0, "unit-area") in kinds and (1, "static") in kinds
    assert prof["attributes"]["death_persistent"] and not prof["attributes"]["passive"]
    assert prof["effects"][0]["owner_receives"] == "via-search"


def test_rain_of_fire_is_a_dynobj_parent(al_ctx):
    prof = R.profile(al_ctx, 5740)
    assert "dynobj" in prof["pipelines"]
    assert any(q["q"] == "expiry remove mode" for q in prof["lifecycle"])


def test_profile_fails_closed_without_aura_effects(al_ctx):
    with pytest.raises(FailClosed):
        R.profile(al_ctx, 999999999)


def test_corpus_is_regenerable_and_valid(al_ctx):
    path = CORPORA / "area-lifecycle.json"
    if not path.exists():
        pytest.skip("corpus not generated")
    built = json.loads(json.dumps(R.build_corpus(al_ctx), sort_keys=True))
    records.validate_corpus(built)
    assert built == json.loads(path.read_text(encoding="utf-8"))
