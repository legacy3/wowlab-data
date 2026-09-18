"""Lead oracle / model tests (aura-lifecycle pass)."""

from __future__ import annotations

import json

import pytest

from aura_lifecycle import CORPORA, oracle, records


def test_merged_lists_validate_and_reconcile():
    m = oracle.merged()
    for kind, entries in m.items():
        records.validate(kind, [{k: v for k, v in e.items() if k not in ("corpus", "merged_into", "merged_from",
                                                                          "reconciliation_note")} for e in entries])
    ids = {e["id"] for entries in m.values() for e in entries}
    for canonical, rec in oracle.RECONCILIATION.items():
        assert canonical in ids and set(rec["merged"]) <= ids


def test_axis_unknowns_name_existing_records():
    ids = {u["id"] for u in oracle.merged()["unknowns"]}
    assert {u for _, _, us in oracle.AXIS_UNKNOWNS for u in us} <= ids


def test_axis_unknown_links_pandemic_hot():
    """Rules out 'explain only lists unknowns naming the spell id': a pandemic HoT gets the carry question."""
    axes = {"RefreshPolicy": "refresh|timer=preserved (ATTR13)|carry=pandemic-reads-refreshed-duration",
            "PeriodicPolicy": "finite:haste=spell-haste:tick-on-apply"}
    got = oracle.axis_unknown_ids(axes)
    assert {"AL-U-B-01", "AL-U-D-02", "AL-U-D-03"} <= set(got)


def test_lead_rules_statuses_match_corpus():
    payload = json.loads((CORPORA / "semantic-axes.json").read_text(encoding="utf-8"))
    status = {r["id"]: r["status"] for r in payload["rules"]}
    # "same SpellId refreshes" and "death == removal of every non-passive" must stay falsified on player data
    assert status["AL-R-X-01"] == "discarded"
    assert status["AL-R-X-05"] == "discarded"
    # passive auras never take the refresh path (Unit.cpp:3393 multi-slot): fixed by the classifier
    assert status["AL-R-X-02"] == "source-backed"


@pytest.mark.parametrize("spell,axis,value", [
    (1269312, "StackPolicy", "multislot-dormant-capacity"),     # Phalanx: capacity 2 never used
    (64844, "StackPolicy", "stacking-shared-across-casters"),   # Divine Hymn
    (980, "StackPolicy", "stacking-per-caster"),               # Agony (DOT_STACKING_RULE)
    (1126, "AuraIdentity", "per-caster/replace"),              # Mark of the Wild
])
def test_model_axes_witnesses(al_ctx, spell, axis, value):
    from aura_lifecycle import model
    assert model.axes(al_ctx, spell)[axis] == value


def test_explain_composes_every_section(al_ctx):
    out = oracle.explain(774)
    assert set(out["sections"]) == {s for s, _ in oracle.SECTIONS}
    assert not [s for s, v in out["sections"].items() if isinstance(v, dict) and "fail_closed" in v]
    assert "AL-U-B-01" in {u["id"] for u in out["unknowns"]}
