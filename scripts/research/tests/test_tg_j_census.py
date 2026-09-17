"""Track J: real-data census of aura-target-map effects, corpus freshness and the pinned witnesses."""

from __future__ import annotations

import json

import pytest

from targeting import CORPORA
from targeting import auratargets as J
from targeting.oracle import from_data

CORPUS = CORPORA / "aura-targets.json"
CLASSES = {"understood", "understood-with-defect", "fixture-dependent", "blocked", "unresolved", "n/a"}


@pytest.fixture(scope="module")
def census(tg_ctx):
    return J.census(tg_ctx)


def test_census_counts_pinned(census):
    """Current-player aura-map effects on 12.1.0.69497 (R1-08 listed 31 of types 35/65/128/174/202; the full set is 33
    plus 2 persistent area auras)."""
    assert len(census["rows"]) == 35
    assert len({r["spell"] for r in census["rows"]}) == 28
    assert census["by_type"] == {"APPLY_AREA_AURA_FRIEND": 1, "APPLY_AREA_AURA_PARTY": 12, "APPLY_AREA_AURA_RAID": 13,
                                 "APPLY_AREA_AURA_SUMMONS": 1, "APPLY_AURA_ON_PET": 6, "PERSISTENT_AREA_AURA": 2}


def test_enum_values_match_pinned_shared_defines():
    """202 is AREA_AURA_SUMMONS and 271 PARTY_NONRANDOM at the pin (not 202 = PARTY_NONRANDOM)."""
    from procs.enums import effect
    for value, name in J.EFFECT.items():
        assert effect(name) == value


def test_load_rewrite_never_touches_174_or_dest_selectors(census, tg_ctx):
    """SpellMgr.cpp:5285-5290 only rewrites IsAreaAuraEffect with an area selector (none in current scope)."""
    rows = {(r["spell"], r["effect"]): r for r in census["rows"]}
    assert rows[(440040, 8)]["target_names"][0] == "TARGET_UNIT_CASTER_AND_SUMMONS"
    assert rows[(740, 1)]["target_names"][0] == "TARGET_DEST_DYNOBJ_ALLY"
    assert all(r["targets"] == r["targets_raw"] for r in census["rows"])


def test_real_spell_views_load_and_radius(census, tg_ctx):
    for spell in sorted({r["spell"] for r in census["rows"]}):
        sv = from_data(spell)
        assert any(e.effect in J.AURA_MAP_EFFECTS for e in sv.effects)
    sv = from_data(465)
    assert sv.effect(0).effect == 65 and sv.effect(0).radius_a.max == 40.0


def test_corpus_is_fresh():
    from targeting.cmd_j import build
    assert json.loads(json.dumps(build(), sort_keys=True)) == json.loads(CORPUS.read_text(encoding="utf-8"))


def test_effect_classes_cover_census_exactly(census):
    doc = json.loads(CORPUS.read_text(encoding="utf-8"))
    keys = {f"{r['spell']}:{r['effect']}" for r in census["rows"]}
    assert set(doc["effect_classes"]) == keys
    ids = {u["id"] for u in doc["unknowns"]}
    for v in doc["effect_classes"].values():
        assert v["class"] in CLASSES
        assert "aura-target-map" in v["tags"]
        assert set(v["unknowns"]) <= ids
        assert set(v["defects"]) <= {d["id"] for d in doc["trinity_defects"]}
    assert doc["effect_classes"]["360194:1"]["class"] == "understood-with-defect"
    assert doc["effect_classes"]["1291885:2"]["build_skew"] is True
    assert {"dynobj", "area"} <= set(doc["effect_classes"]["5740:0"]["tags"])
    assert "radius-zero" in doc["effect_classes"]["400129:0"]["tags"]
    for u in doc["unknowns"]:
        assert {"id", "subject", "evidence", "known", "unknown", "why_unresolved", "reopen_condition",
                "build_skew"} <= set(u)


@pytest.mark.parametrize("path", J.witness_paths(), ids=lambda p: p.name)
def test_witnesses_evaluate_to_pinned_expectation(path, tg_ctx):
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert set(J.WITNESS_KEYS) <= set(doc)
    assert doc["spell"] in tg_ctx.scope.reach and not doc["build_skew"]
    res = J.evaluate_witness(doc)
    assert res["ok"], (res["recipients"], doc["expect"]["recipients"])


def test_witness_expectations_literal():
    got = {p.name: json.loads(p.read_text(encoding="utf-8"))["expect"]["recipients"] for p in J.witness_paths()}
    assert got == {"witness-J-400129-0.json": ["p1", "p2"], "witness-J-465-0.json": ["p1", "p2", "p4", "pet2"],
                   "witness-J-5740-0.json": ["e1"]}
