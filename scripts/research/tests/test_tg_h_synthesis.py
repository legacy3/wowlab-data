"""Lead-level invariants of the targeting pass: census join, witness corpus, merged unknowns.

The committed corpora must equal what the committed code produces (the byte-level proof is
``tools/regen_targeting.py --check``); these tests pin the cross-track invariants the report
quotes.
"""

from __future__ import annotations

import json

import pytest

from targeting import CORPORA, EVIDENCE_CLASSES, FailClosed

pytestmark = pytest.mark.snapshot


def _load(name: str) -> dict:
    path = CORPORA / name
    if not path.exists():
        pytest.skip(f"missing corpus {name}")
    return json.loads(path.read_text(encoding="utf-8"))


def test_census_matches_fresh_build(tg_ctx):
    from targeting import census
    fresh = json.loads(json.dumps(census.build(tg_ctx), sort_keys=True))
    assert fresh == _load("census.json")


def test_census_population_and_closure_partition(tg_ctx):
    """Every IsEffect row of the current-player scope is in the census exactly once, with one closure."""
    from targeting import census
    doc = _load("census.json")
    pop = census.population(tg_ctx)
    assert doc["global"]["effects"] == len(pop) == len(doc["effects"])
    assert sum(doc["global"]["by_closure"].values()) == len(pop)
    assert set(doc["global"]["by_closure"]) <= set(census.VERDICT_ORDER)
    # every track-A row exists (A covers the whole population)
    assert all("A" in row["verdicts"] for row in doc["effects"].values())
    # the extended layer never overlaps the main population
    assert not set(doc["extended"]["rows"]) & set(doc["effects"])


def test_census_simple_implies_understood_and_no_family():
    doc = _load("census.json")
    for key, row in doc["effects"].items():
        if row["simple"]:
            assert row["closure"] == "understood" and not row["families"], key


def test_census_per_spec_counts_are_subsets():
    doc = _load("census.json")
    assert len(doc["per_spec"]) == 40
    total = doc["global"]["effects"]
    for spec, row in doc["per_spec"].items():
        assert 0 < row["effects"] <= total, spec
        assert sum(row["by_closure"].values()) == row["effects"], spec


def test_census_worst_verdict_rule():
    """Rules out an 'any understood wins' join: a single blocked track verdict blocks the effect."""
    from targeting.census import _worst
    assert _worst(["understood", "blocked", "n/a"]) == "blocked"
    assert _worst(["understood-with-defect", "understood"]) == "understood-with-defect"
    assert _worst(["n/a"]) == "n/a"
    with pytest.raises(FailClosed):
        _worst(["understood", "probably-fine"])


def test_witness_corpus_all_pass_and_shapes_closed():
    doc = _load("witnesses.json")
    assert doc["summary"]["failed"] == 0
    assert all(c["closed"] for c in doc["shape_coverage"].values())
    assert doc["summary"]["shapes"] == 22
    for w in doc["witnesses"]:
        assert w["discriminates"], w["id"]
        if w["real_spell"]:
            assert w["spell"] > 0 and isinstance(w["selectors"], dict), w["id"]


def test_witness_areatrigger_expectations_are_pinned_not_recomputed():
    """The AreaTrigger witness files carry literal expectations; a changed oracle must fail, not silently agree."""
    from targeting.witnesses import AT_FIXTURES
    for path in sorted(AT_FIXTURES.glob("*.json")):
        doc = json.loads(path.read_text(encoding="utf-8"))
        assert doc["expect"], path.name
        assert "at_inside_units" in doc["expect"], path.name


def test_unknowns_merged_and_valid():
    from targeting.unknowns import REQUIRED
    doc = _load("unknowns.json")
    ids = [e["id"] for e in doc["entries"]]
    assert len(ids) == len(set(ids)) == doc["count"]
    for e in doc["entries"]:
        for key in REQUIRED:
            assert e.get(key) not in (None, "", []), (e["id"], key)
        evs = e["evidence"] if isinstance(e["evidence"], list) else [e["evidence"]]
        assert all(v in EVIDENCE_CLASSES for v in evs), e["id"]
        assert e["sources"], e["id"]


def test_census_unknown_ids_resolve():
    """Every unknown id an effect row cites exists in the merged artifact (no dangling reopen pointers)."""
    census = _load("census.json")
    entries = _load("unknowns.json")["entries"]
    known = {e["id"] for e in entries} | {a for e in entries for a in e.get("aliases", [])}
    cited = {u for row in census["effects"].values() for u in row["unknowns"]}
    cited |= {u for row in census["extended"]["rows"].values() for u in row.get("unknowns", [])}
    assert cited <= known, sorted(cited - known)
