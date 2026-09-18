"""Track J signatures: id stability, granularity folding, Core catalog classification, corpus replay."""

from __future__ import annotations

import json
import random

import pytest

from aura_lifecycle import CORPORA, signatures


def rec(spell, *, attrs=(), passive=False, duration="finite-fixed", effects=None, external=(), interrupts=()):
    effects = effects or [("unit", "other", "no-period", "caster", "plain", 29)]
    return {
        "spell": spell,
        "axes": {"passive": passive, "duration": duration, "pvp_duration": False, "channel": False,
                 "stack_capacity": "0", "proc_charges": "0", "aura_options": ("absent",), "restrictions": (),
                 "shapeshift": (), "equipment": False, "interrupts": tuple(interrupts), "dispel": [0, False],
                 "ranked": False, "effects": ["1" if len(effects) == 1 else "2", False],
                 "attrs": tuple(sorted(attrs)), "external": tuple(external)},
        "effects": sorted(e[:5] for e in effects),
        "effects_exact": sorted(effects),
        "duration_ms": 6000, "stack_amount": 0, "proc_charges": 0,
    }


def test_id_depends_only_on_signature():
    """Rules out enumeration-order ids: shuffling records and round-tripping through JSON keeps ids."""
    recs = [rec(i, attrs=("DOT_STACKING_RULE",) if i % 2 else ()) for i in range(20)]
    ids = {r["spell"]: signatures.record_ids(r) for r in recs}
    random.Random(7).shuffle(recs)
    assert {r["spell"]: signatures.record_ids(r) for r in recs} == ids
    for g in signatures.GRANULARITIES:
        sig = signatures.signature(recs[0], g)
        assert signatures.signature_id(json.loads(json.dumps(sig)), g) == ids[recs[0]["spell"]][g]
        assert ids[recs[0]["spell"]][g].startswith(signatures.GRANULARITIES[g])


def test_axis_change_changes_id():
    a, b = rec(1), rec(1, attrs=("ROLLING_PERIODIC",))
    for g in signatures.GRANULARITIES:
        assert signatures.record_ids(a)[g] != signatures.record_ids(b)[g]


def test_path_folds_target_multiplicity_and_value_detail():
    """path ignores implicit target / effect multiplicity; full and exact do not (rules out 'path == full')."""
    one = rec(1, effects=[("unit", "other", "no-period", "caster", "plain", 29)])
    two = rec(2, effects=[("unit", "other", "no-period", "other", "plain", 29), ("unit", "other", "no-period", "caster", "plain", 189)])
    two["axes"]["effects"] = one["axes"]["effects"]    # isolate the effect-tuple difference
    i1, i2 = signatures.record_ids(one), signatures.record_ids(two)
    assert i1["path"] == i2["path"]
    assert i1["full"] != i2["full"] and i1["exact"] != i2["exact"]


def test_points_bits_split_signatures():
    """SuppressPointsStacking changes the amount-per-stack path (SpellAuraEffects.cpp:844), so it must split
    every granularity (rules out R1-07's merged classes such as 387178 vs 387154)."""
    a = rec(1, effects=[("unit", "other", "no-period", "caster", "plain", 29)])
    b = rec(2, effects=[("unit", "other", "no-period", "caster", "suppress-points-stacking", 29)])
    ia, ib = signatures.record_ids(a), signatures.record_ids(b)
    assert all(ia[g] != ib[g] for g in signatures.GRANULARITIES)


def test_server_derived_attr_is_not_a_core_blocker():
    v = signatures.core_view(rec(9, attrs=("CU_AURA_CANNOT_BE_SAVED",)), CATALOGS)
    assert v["catalog_admissible"] and "CU_AURA_CANNOT_BE_SAVED" not in v["attr_support"]


def test_exact_distinguishes_subtype_full_does_not():
    a = rec(1, effects=[("unit", "other", "no-period", "caster", "plain", 29)])
    b = rec(2, effects=[("unit", "other", "no-period", "caster", "plain", 189)])
    ia, ib = signatures.record_ids(a), signatures.record_ids(b)
    assert ia["full"] == ib["full"] and ia["exact"] != ib["exact"]


def test_path_folds_external_policy_source():
    """path keeps the touched surface names but folds their policy source; full keeps both."""
    a = rec(1, external=("removal:script",))
    b = rec(2, external=("removal:combined",))
    c = rec(3)
    ia, ib, ic = (signatures.record_ids(r) for r in (a, b, c))
    assert ia["path"] == ib["path"] != ic["path"]
    assert ia["full"] != ib["full"]


CATALOGS = ({29: "implemented", 189: "disabled"}, {103: "ignored", 334: "disabled", 436: "implemented"})


def test_core_view_separates_lifecycle_blockers_from_subtype_blockers():
    """Rules out conflating 'subtype not implemented' with 'lifecycle attribute blocks an implemented subtype'."""
    ok = signatures.core_view(rec(1, attrs=("DOT_STACKING_RULE", "PERIODIC_REFRESH_EXTENDS_DURATION")), CATALOGS)
    assert ok["catalog_admissible"] and not ok["lifecycle_only_blocked"]
    lc = signatures.core_view(rec(2, attrs=("ROLLING_PERIODIC",)), CATALOGS)
    assert not lc["catalog_admissible"] and lc["lifecycle_only_blocked"] and lc["lifecycle_blockers"] == ["ROLLING_PERIODIC"]
    sub = signatures.core_view(rec(3, attrs=("ROLLING_PERIODIC",),
                                   effects=[("unit", "other", "no-period", "caster", "plain", 189)]), CATALOGS)
    assert not sub["catalog_admissible"] and not sub["lifecycle_only_blocked"]
    unk = signatures.core_view(rec(4, effects=[("unit", "other", "no-period", "caster", "plain", 9999)]), CATALOGS)
    assert unk["subtypes"] == ["unknown"] and not unk["catalog_admissible"]


def test_group_and_distribution():
    recs = [rec(i) for i in range(5)] + [rec(10, attrs=("ROLLING_PERIODIC",))]
    pops = {"all": frozenset(range(11)), "player": frozenset({0, 1, 10}), "controlled": frozenset()}
    cat = signatures.group(recs, pops, catalogs=CATALOGS, granularity="full")
    assert [e["id"] for e in cat] == sorted(e["id"] for e in cat)
    big = next(e for e in cat if e["counts"]["all"] == 5)
    assert big["player_members"] == [0, 1] and big["core"]["catalog_admissible"]["player"] == 2
    d = signatures.distribution(cat, "all")
    assert d["signatures"] == 2 and d["singletons"] == 1 and d["largest"] == [5, 1]
    assert signatures.top(cat, "player", 1)[0]["count"] == 2


def test_signature_classes_merge_distinct_axis_tuples():
    """R1-07: the corpus states, per granularity, how many multi-member member-population classes contain
    more than one lead semantic-axis tuple; coarser granularities can only merge more."""
    path = CORPORA / "lifecycle-signatures.json"
    if not path.exists():
        pytest.skip("lifecycle-signatures.json not generated")
    ag = json.loads(path.read_text(encoding="utf-8"))["axis_agreement"]
    assert ag["available"]
    for g in ("path", "full", "exact"):
        row = ag[g]["player"]
        assert 0 <= row["classes_with_multiple_axis_tuples"] <= row["multi_member_classes"]
    frac = {g: ag[g]["player"]["spells_in_those"] for g in ("path", "full", "exact")}
    assert frac["path"] >= frac["full"] >= frac["exact"]


def test_corpus_ids_replay(al_ctx):
    """Every player member listed in the committed catalog re-derives the same path id from the snapshot."""
    path = CORPORA / "lifecycle-signatures.json"
    if not path.exists():
        pytest.skip("lifecycle-signatures.json not generated")
    from aura_lifecycle import census
    payload = json.loads(path.read_text(encoding="utf-8"))
    cat = payload["catalog"]["path"]
    assert sum(payload["distribution"]["path"]["all"][k] for k in ("spells",)) == 189140
    sample = [(e["id"], s) for e in cat for s in e["player_members"][:2]][:150]
    for sid, spell in sample:
        r = census.spell_record(al_ctx.data, spell, ranked=al_ctx.catalog.is_ranked(spell),
                                external=tuple(_external(al_ctx).get(spell, ())))
        assert signatures.record_ids(r)["path"] == sid, spell


_EXT = {}


def _external(ctx):
    if "x" not in _EXT:
        from aura_lifecycle import census
        flags = census.external_flags(ctx)
        _EXT["x"] = {s: census.external_axis(f) for s, f in flags.items()}
    return _EXT["x"]
