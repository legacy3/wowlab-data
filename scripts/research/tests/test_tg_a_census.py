"""Track A: current-player selector census invariants (real data, regenerable).

Rules out silent population drift: the corpora on disk must equal a fresh build,
every current-player IsEffect row has exactly one Track A effect class, and the
counts partition cleanly.
"""

from __future__ import annotations

import json
from collections import Counter

import pytest

from targeting import CORPORA
from targeting import cmd_a
from targeting import composition as C
from targeting import selectors as S

pytestmark = pytest.mark.snapshot


@pytest.fixture(scope="module")
def census(tg_ctx):
    return cmd_a._census()


def _population(ctx) -> set[str]:
    return {f"{s}:{e.index}" for s in ctx.scope.reach for e in ctx.data.effects(s) if e.is_effect}


def test_counts_partition(census, tg_ctx) -> None:
    allc = census["all"]
    assert allc["effects"] == len(_population(tg_ctx))
    assert sum(allc["pairs"].values()) == allc["effects"]
    assert sum(allc["target_a"].values()) == allc["effects"] == sum(allc["target_b"].values())
    core = census["core_admitted_pairs"]["player_effects"]
    assert sum(core.values()) == allc["effects"]
    fixed = census["all_after_load_corrections"]
    assert sum(fixed["pairs"].values()) == allc["effects"] - sum(1 for r in census["_rows"] if not r["corrected"])


def test_root_kinds_cover_every_spell(census, tg_ctx) -> None:
    spells = set()
    for kind, c in census["by_root_kind"].items():
        assert kind in set(S.ROOT_KIND_LABEL.values()) | {"triggered"}
        spells |= {r["spell"] for r in census["_rows"]}
    assert census["all"]["spells"] == len({r["spell"] for r in census["_rows"]})
    assert sum(c["effects"] for c in census["by_root_kind"].values()) >= census["all"]["effects"]


def test_per_spec_is_subset(census, tg_ctx) -> None:
    assert len(census["per_spec"]) == len(tg_ctx.scope.specs_reach) == 40
    total = census["all"]["pairs"]
    for spec, row in census["per_spec"].items():
        assert sum(row["pairs"].values()) == row["effects"]
        for pair, n in row["pairs"].items():
            assert n <= total[pair], (spec, pair)


def test_no_selector_outside_trinity(census) -> None:
    assert census["selector_ids_beyond_trinity"] == []
    for pair in census["all"]["pairs"]:
        a, b = map(int, pair.split(","))
        S.info(a), S.info(b)
        C.classify(a, b)


def test_controlled_unit_population_matches_corpus(census) -> None:
    doc = json.loads((CORPORA.parent / "controlled-unit-corpora" / "spells.json").read_text(encoding="utf-8"))
    assert census["controlled_units"]["spells_total"] == doc["ability_census"]["spells_total"]


def test_effect_classes_cover_population(tg_ctx) -> None:
    doc = cmd_a.build_selectors()
    classes = doc["effect_classes"]
    assert set(classes) == _population(tg_ctx)
    verdicts = {"understood", "understood-with-defect", "fixture-dependent", "blocked", "unresolved", "n/a"}
    for key, row in classes.items():
        assert row["class"] in verdicts, key
        assert isinstance(row["build_skew"], bool)
        assert row["tags"] == sorted(set(row["tags"]))
    assert sum(doc["effect_class_counts"].values()) == len(classes)
    # the one TARGET_UNK_151 effect stays unresolved
    assert classes["49028:0"]["class"] == "unresolved"
    assert Counter(r["class"] for r in classes.values())["blocked"] == 0


@pytest.mark.parametrize("name,builder", [("selectors.json", "build_selectors"),
                                          ("composition.json", "build_composition"),
                                          ("attributes.json", "build_attributes")])
def test_corpus_on_disk_is_regenerable(name, builder, tg_ctx) -> None:
    path = CORPORA / name
    if not path.is_file():
        pytest.skip(f"{name} not generated yet (python3 targeting.py track-a-all)")
    fresh = json.loads(json.dumps(getattr(cmd_a, builder)(), sort_keys=True))
    assert json.loads(path.read_text(encoding="utf-8")) == fresh


def test_relation_counts_sum_to_effects(tg_ctx, census) -> None:
    doc = cmd_a.build_composition()
    live = sum(1 for r in census["_rows"] if r["corrected"])
    assert sum(doc["relation_counts"].values()) == live
    assert sum(p["player_effects"] for p in doc["pairs"]) == live
