"""Neutral fixture format: structural, fail-closed validation (no snapshot needed except for the corpus checks)."""

from __future__ import annotations

import copy
import hashlib
import json

import pytest

from cp_e_helpers import FIXTURES_DIR, fixture_names, load_raw
from character_prep import CORPORA
from character_prep.fixture import (
    EQUIPMENT_SLOTS,
    FIXTURE_SCHEMA,
    FIXTURE_VERSION,
    Fixture,
    FixtureError,
    canonical_json,
    empty_fixture,
    load_fixture_json,
    schema_document,
    validate_fixture,
)


def base() -> dict:
    raw = empty_fixture(1, 1, 71, 80)
    raw["traits"]["entries"] = [{"node_entry_id": 112183, "rank": 1}]
    raw["equipment"] = {"HEAD": {"item_id": 268230, "context": 6}}
    return raw


def errors_of(raw: dict) -> list[str]:
    return validate_fixture(raw)


def test_empty_fixture_is_valid():
    assert errors_of(base()) == []
    fx = Fixture.from_dict(base())
    assert fx.level == 80 and fx.trait_entries == [(112183, 1)] and len(fx.sha256) == 64


def test_unknown_top_level_key_rejected():
    raw = base()
    raw["talents"] = []
    assert any("unknown keys ['talents']" in e for e in errors_of(raw))


def test_unknown_equipment_key_rejected():
    raw = base()
    raw["equipment"]["HEAD"]["item_level"] = 700      # derived values are never accepted as input
    assert any("$.equipment.HEAD" in e and "item_level" in e for e in errors_of(raw))


def test_duplicate_json_key_rejected():
    text = json.dumps(base())[:-1] + ', "identity": {}}'
    with pytest.raises(FixtureError, match="duplicate JSON key 'identity'"):
        load_fixture_json(text)


def test_bad_slot_name_rejected():
    raw = base()
    raw["equipment"]["WEAPON"] = {"item_id": 1}
    assert any("not a Trinity EquipmentSlots name" in e for e in errors_of(raw))


def test_missing_identity_input_rejected():
    raw = base()
    del raw["identity"]["spec_id"]
    assert any("$.identity.spec_id: required identity input is missing" in e for e in errors_of(raw))


def test_bool_is_not_an_integer():
    raw = base()
    raw["identity"]["level"] = True
    assert any("$.identity.level: expected an integer" in e for e in errors_of(raw))


def test_rng_must_be_null():
    raw = base()
    raw["options"] = {"rng": 42}
    assert any("options.rng" in e for e in errors_of(raw))


def test_observation_path_must_be_under_derived():
    raw = base()
    raw["observations"] = [{"observer": "retail-client", "build": "12.1.0.69497", "path": "/fixture/identity", "value": 1}]
    assert any("must be a JSON pointer under /derived/" in e for e in errors_of(raw))


def test_observation_observer_enum():
    raw = base()
    raw["observations"] = [{"observer": "wowhead", "build": "x", "path": "/derived/stats", "value": 1}]
    assert any("is not one of" in e for e in errors_of(raw))


def test_duplicate_trait_entry_rejected():
    raw = base()
    raw["traits"]["entries"].append({"node_entry_id": 112183, "rank": 1})
    assert any("listed twice" in e for e in errors_of(raw))


def test_rank_below_one_rejected():
    raw = base()
    raw["traits"]["entries"][0]["rank"] = 0
    assert any("below the minimum 1" in e for e in errors_of(raw))


def test_duplicate_pvp_slot_rejected():
    raw = base()
    raw["pvp_traits"] = [{"pvp_talent_id": 34, "slot": 0}, {"pvp_talent_id": 35, "slot": 0}]
    assert any("slot 0 used twice" in e for e in errors_of(raw))


def test_inline_base_stats_need_values():
    raw = base()
    raw["server_inputs"]["base_stats"] = {"source": "inline"}
    assert any("class_level_stats: required" in e for e in errors_of(raw))


def test_base_stats_source_enum():
    raw = base()
    raw["server_inputs"]["base_stats"] = {"source": "guess"}
    assert any("is not one of" in e for e in errors_of(raw))


def test_fixture_version_pinned():
    raw = base()
    raw["fixture_version"] = FIXTURE_VERSION + 1
    assert any("fixture_version" in e for e in errors_of(raw))


def test_controlled_units_values_are_objects():
    raw = base()
    raw["controlled_units"] = {"hunter_pet": 1}
    assert any("$.controlled_units" in e for e in errors_of(raw))


def test_from_dict_raises_with_every_problem():
    raw = base()
    raw["identity"]["race_id"] = "human"
    raw["bogus"] = 1
    with pytest.raises(FixtureError) as info:
        Fixture.from_dict(raw)
    assert "race_id" in str(info.value) and "bogus" in str(info.value)


def _classes(node, out):
    if isinstance(node, dict):
        if "class" in node:
            out.add(node["class"])
        for v in node.values():
            _classes(v, out)


def test_schema_field_classes_are_documented():
    doc = schema_document()
    used: set[str] = set()
    _classes(FIXTURE_SCHEMA, used)
    assert used <= set(doc["field_classes"])
    assert doc["equipment_slots"] == list(EQUIPMENT_SLOTS)


@pytest.mark.snapshot
def test_committed_schema_is_current():
    assert (CORPORA / "fixture-schema.json").read_text(encoding="utf-8") == canonical_json(schema_document())


@pytest.mark.snapshot
@pytest.mark.parametrize("name", fixture_names())
def test_committed_fixture_is_structurally_valid(name):
    raw = load_raw(name)
    assert validate_fixture(raw) == []
    assert raw["provenance"]["source_kind"] == "synthetic"
    assert any("SYNTHETIC" in n for n in raw["provenance"]["notes"])


@pytest.mark.snapshot
def test_index_sha256_matches_files():
    index = json.loads((FIXTURES_DIR / "index.json").read_text(encoding="utf-8"))
    assert len(index["fixtures"]) == len(fixture_names()) >= 8
    for e in index["fixtures"]:
        assert hashlib.sha256((FIXTURES_DIR / e["fixture"]).read_bytes()).hexdigest() == e["fixture_sha256"]
        assert hashlib.sha256((FIXTURES_DIR / e["compiled"]).read_bytes()).hexdigest() == e["compiled_sha256"]
        assert e["validation_ok"] is True


def test_fixture_copy_is_independent():
    raw = base()
    fx = Fixture.from_dict(copy.deepcopy(raw))
    raw["identity"]["level"] = 1
    assert fx.level == 80


# -- closeout review (G3): fail-closed gaps ----------------------------------------------------------------------------

@pytest.mark.parametrize("mutate,needle", [
    (lambda d: d.__setitem__("fixture_version", True), "fixture_version"),
    (lambda d: d.__setitem__("fixture_version", 1.0), "fixture_version"),
    (lambda d: d.__setitem__("observations", [{"observer": "core", "build": "x", "path": "/derived/", "value": 1}]), "path"),
    (lambda d: d.__setitem__("observations", [{"observer": "core", "build": "x", "path": "/derived/stats", "value": 1,
                                                "precision": True}]), "precision"),
    (lambda d: d.__setitem__("observations", [{"observer": "core", "build": "x", "path": "/derived/stats", "value": 1,
                                                "units": 5}]), "units"),
    (lambda d: d["provenance"].__setitem__("captured_at", 5), "captured_at"),
    (lambda d: d["server_inputs"].__setitem__("base_stats", {"source": "inline", "class_level_stats": {"x": 5}}), "class_level_stats"),
    (lambda d: d["server_inputs"].__setitem__("base_stats", {"source": "world-db-corpus", "path": "/etc/passwd"}), "repository-relative"),
    (lambda d: d["server_inputs"].__setitem__("base_stats", {"source": "world-db-corpus", "path": "../x.json"}), "repository-relative"),
    (lambda d: d["server_inputs"].__setitem__("base_stats", {"source": "world-db-corpus", "fill_rule": "interpolate"}), "fill_rule"),
    (lambda d: d["server_inputs"].__setitem__("base_stats", {"source": "unsupplied", "fill_rule": "trinity"}), "fill_rule"),
    (lambda d: d.__setitem__("learned_spells", [296087, 296087]), "listed twice"),
    (lambda d: d.__setitem__("learned_spells", [True]), "learned_spells[0]"),
    (lambda d: d.__setitem__("learned_spells", {"296087": 1}), "learned_spells"),
])
def test_fail_closed_review_cases(mutate, needle):
    raw = base()
    mutate(raw)
    errs = validate_fixture(raw)
    assert errs and any(needle in e for e in errs), errs


def test_non_finite_json_constant_rejected():
    with pytest.raises(FixtureError):
        load_fixture_json('{"a": NaN}')


def test_learned_spells_accessor():
    raw = base()
    raw["learned_spells"] = [296087]
    assert Fixture.from_dict(raw).learned_spells == [296087]
