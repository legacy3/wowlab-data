"""Track B: trait / rank / effect-point provenance.

Pins the exact rank amounts of the five reference packages, the mixed-shaping witness,
the curve and effect-point defect taxonomy, and the identity collisions that prove
provider-spell identity is insufficient.  Everything here is a statement about the
pinned ``data/tables`` snapshot, so a drift in the snapshot must fail these loudly
rather than be absorbed.
"""

from __future__ import annotations

import pytest

from gearing.tables import DEFAULT_TABLES
from selected_package.provenance import TraitSpellError
from selected_package.rankprov import (
    FACT_BUCKET,
    FACT_BUCKETS,
    TRINITY_OPERATIONS,
    RankNegatives,
    RankProvenance,
)

pytestmark = pytest.mark.snapshot


@pytest.fixture(scope="module")
def rp() -> RankProvenance:
    if not DEFAULT_TABLES.is_dir():
        pytest.skip(f"no table snapshot at {DEFAULT_TABLES}")
    return RankProvenance()


@pytest.fixture(scope="module")
def census(rp: RankProvenance) -> dict:
    return rp.census()


# ---------------------------------------------------------------------------
# population
# ---------------------------------------------------------------------------

def test_population_matches_the_pinned_baseline(census):
    """The lead's baseline, reproduced through the audit path rather than resolve()."""
    population = census["population"]
    assert population["trait_node_entry_rows"] == 17139
    assert population["resolvable_entries"] == 8279
    assert population["entry_rank_variants"] == 8846
    assert population["refusals"] == {
        "MissingDefinition": 177,
        "MissingSpell": 7078,
        "UnsupportedCurvePoints": 22,
        "UnsupportedDefinition": 306,
        "UnsupportedEffectPoints": 205,
        "UnsupportedRank": 1072,
    }
    assert population["detached_entries"] == 3038
    assert population["detached_and_resolvable"] == 2143


# ---------------------------------------------------------------------------
# the five reference packages
# ---------------------------------------------------------------------------

#: entry -> {rank: [amount per 1-based effect index]}, plus the provenance ids.
REFERENCE = {
    # Improved Vivify: Set-shaped effect 1, unshaped sibling effect 2.
    101510: {"definition": 106512, "provider": 231602, "max_ranks": 2,
             "node_entry_type": 2, "kind": "set-only", "mixed": True,
             "amounts": {1: [20.0, 40.0], 2: [40.0, 40.0]}},
    # Martial Expert: one rank, no point rows, authored values kept.
    117409: {"definition": 122421, "provider": 429638, "max_ranks": 1,
             "node_entry_type": 2, "kind": "unshaped", "mixed": False,
             "amounts": {1: [10.0, 20.0]}},
    # Heart of the Crusader: four Set-shaped effects, two curves.
    115483: {"definition": 120495, "provider": 406154, "max_ranks": 2,
             "node_entry_type": 2, "kind": "set-only", "mixed": False,
             "amounts": {1: [10.0, 10.0, 10.0, 10.0], 2: [20.0, 20.0, 20.0, 20.0]}},
    # Phalanx: Multiply shaping, and NodeEntryType 13 rather than 2.
    137000: {"definition": 141763, "provider": 1269312, "max_ranks": 2,
             "node_entry_type": 13, "kind": "multiply-only", "mixed": False,
             "amounts": {1: [10.0, 10.0], 2: [20.0, 20.0]}},
    # Ephemeral Bond: one rank, three unshaped effects.
    136808: {"definition": 141571, "provider": 426563, "max_ranks": 1,
             "node_entry_type": 2, "kind": "unshaped", "mixed": False,
             "amounts": {1: [8.0, 8.0, 8.0]}},
}


@pytest.mark.parametrize("entry_id", sorted(REFERENCE))
def test_reference_packages_resolve_exact_rank_amounts(rp, entry_id):
    expected = REFERENCE[entry_id]
    for rank, amounts in expected["amounts"].items():
        audit = rp.audit(entry_id, rank)
        assert audit.admitted, audit.defects
        assert audit.definition_id == expected["definition"]
        assert audit.provider == expected["provider"]
        assert audit.max_ranks == expected["max_ranks"]
        assert audit.node_entry_type == expected["node_entry_type"]
        assert audit.shaping_kind == expected["kind"]
        assert audit.mixed_shaped_siblings is expected["mixed"]
        assert audit.selected_amounts() == amounts
        assert [e.effect_index for e in audit.effects] == list(range(1, len(amounts) + 1))


def test_reference_audits_agree_with_the_core_port(rp):
    """The audit must never disagree with :mod:`selected_package.provenance`."""
    for entry_id, expected in REFERENCE.items():
        for rank in expected["amounts"]:
            resolved = rp.provenance.resolve(entry_id, rank)
            assert not isinstance(resolved, TraitSpellError)
            audit = rp.audit(entry_id, rank)
            for effect in audit.effects:
                assert effect.amounts[rank] == resolved.amount_at_rank(effect.effect_index, rank)
                assert effect.authored == resolved.authored(effect.effect_index)
                assert effect.shaped == resolved.is_rank_shaped(effect.effect_index)


# ---------------------------------------------------------------------------
# shaping distribution + the mixed-shaping witness
# ---------------------------------------------------------------------------

def test_shaping_distribution(census):
    shaping = census["shaping"]
    assert shaping["kinds"] == {
        "unshaped": 7712,
        "set-only": 376,
        "multiply-only": 180,
        "mixed-set-multiply": 11,
    }
    assert sum(shaping["kinds"].values()) == census["population"]["resolvable_entries"]
    # Shaping only ever happens on a MaxRanks-2 entry.
    assert shaping["siblings"] == {"all-shaped": 403, "mixed-shaped-unshaped": 164}
    assert sum(shaping["siblings"].values()) == 567


def test_mixed_set_and_multiply_in_one_package(rp, census):
    """Eleven packages shape one effect with Set and another with Multiply."""
    entries = census["shaping"]["kind_examples"]["mixed-set-multiply"]
    assert 137069 in entries
    for entry_id in entries:
        audit = rp.audit(entry_id, 1)
        verdicts = {e.verdict for e in audit.effects if e.shaped}
        assert verdicts == {"Set", "Multiply"}


def test_improved_vivify_siblings_disagree_at_rank_one(rp):
    """The witness Core hardcodes as ``has_improved_vivify_amounts``.

    Effect 1 is Set-shaped 20 -> 40; effect 2 keeps its authored 40.  So the two
    siblings carry *different* values at rank 1 and the *same* value at rank 2, which
    no per-package scalar can express.
    """
    rank_one, rank_two = rp.audit(101510, 1), rp.audit(101510, 2)
    assert [e.authored for e in rank_one.effects] == [40.0, 40.0]
    assert [e.verdict for e in rank_one.effects] == ["Set", "none"]
    assert rank_one.selected_amounts() == [20.0, 40.0]
    assert rank_two.selected_amounts() == [40.0, 40.0]
    assert rank_one.mixed_shaped_siblings is True

    row = rank_one.point_rows[0]
    assert (row.row_id, row.source_effect_index, row.effect_index) == (21888, 0, 1)
    assert (row.operation, row.curve_id) == ("Set", 62007)
    assert row.curve.points == [(1.0, 20.0), (2.0, 40.0)]
    assert row.curve.defects == []


# ---------------------------------------------------------------------------
# curve defects
# ---------------------------------------------------------------------------

def test_curve_census(census):
    curves = census["curves"]
    assert curves["referenced_curves"] == 1356
    assert curves["type_distribution"] == {"0": 1354, "2": 2}
    # Curve.Flags is checked by amount.rs:19 but never exercised by the source.
    assert curves["flags_distribution"] == {"0": 1356}
    assert curves["point_count_distribution"]["2"] == 1287
    assert len(curves["unsupported_curve_point_entries"]) == 22
    assert len(curves["unsupported_curve_point_refusals"]) == 30


#: (curve id, MaxRanks, exact points, expected defect, verdict).
CURVE_DEFECTS = [
    # Rank-3 curve left on a MaxRanks-2 entry: both reachable ranks are still exact.
    (54420, 2, [(1.0, 15.0), (2.0, 30.0), (3.0, 30.0)],
     ["point-count-3-vs-max-ranks-2"], "conservative"),
    # Two points at Pos_0 == 1: ambiguous outright.
    (60382, 2, [(1.0, 15.0), (1.0, 15.0), (2.0, 30.0)],
     ["point-count-3-vs-max-ranks-2"], "semantically-right"),
    # Indexed from rank 0: there is no rank-1 point at all.
    (92965, 2, [(0.0, 1.0), (2.0, 2.0)], ["pos0-0-at-rank-1"], "semantically-right"),
    # Degenerate: both points at the origin.
    (58589, 2, [(0.0, 0.0), (0.0, 0.0)],
     ["pos0-0-at-rank-1", "pos0-0-at-rank-2"], "semantically-right"),
]


@pytest.mark.parametrize("curve_id,max_ranks,points,defects,_verdict", CURVE_DEFECTS)
def test_curve_defect_is_named_exactly(rp, curve_id, max_ranks, points, defects, _verdict):
    facts = rp.curve_facts(curve_id, max_ranks)
    assert facts.curve_type == 0 and facts.flags == 0
    assert facts.points == points
    assert facts.defects == defects
    assert facts.ok is False


@pytest.mark.parametrize("curve_id,_mr,_points,_defects,verdict", CURVE_DEFECTS)
def test_curve_refusal_verdicts(census, curve_id, _mr, _points, _defects, verdict):
    rows = [r for r in census["curves"]["unsupported_curve_point_refusals"]
            if r["curve"]["curve_id"] == curve_id]
    assert rows, f"curve {curve_id} is not among the refused rows"
    assert {r["verdict"] for r in rows} == {verdict}


def test_only_five_curve_refusals_are_conservative(census):
    conservative = {r["curve"]["curve_id"]
                    for r in census["curves"]["unsupported_curve_point_refusals"]
                    if r["verdict"] == "conservative"}
    assert conservative == {54420, 54421, 54425, 54426, 58141}


def test_non_rank_table_curves_are_bezier_control_hulls(census):
    rows = census["curves"]["non_rank_table_curves"]
    assert {r["curve_id"] for r in rows} == {82523, 82586}
    for row in rows:
        assert row["type"] == 2 and len(row["points"]) == 3


# ---------------------------------------------------------------------------
# effect-point defects
# ---------------------------------------------------------------------------

def test_unsupported_effect_points_decomposes_to_205(census):
    causes = census["effect_points"]["unsupported_effect_points_causes"]
    assert sum(causes.values()) == 205
    assert "unclassified" not in causes
    assert causes == {
        "duplicate-effect-index+point-rows-exceed-provider-effects": 2,
        "foreign-effect-index": 7,
        "foreign-effect-index+max-ranks-1-with-point-rows": 2,
        "foreign-effect-index+max-ranks-1-with-point-rows"
        "+point-rows-exceed-provider-effects": 4,
        "foreign-effect-index+point-rows-exceed-provider-effects": 32,
        "foreign-effect-index+point-rows-exceed-provider-effects"
        "+point-rows-exceed-retention": 2,
        "max-ranks-1-with-point-rows": 102,
        "max-ranks-1-with-point-rows+point-rows-exceed-retention": 3,
        "max-ranks-2-without-point-rows": 32,
        "operation-type": 7,
        "point-rows-exceed-retention": 12,
    }


def test_duplicate_effect_point_rows(rp, census):
    """Two point rows for the same EffectIndex; Trinity would take whichever comes first."""
    rows = census["effect_points"]["duplicate_effect_index_definitions"]
    assert {r["definition_id"] for r in rows} == {106806, 141760}
    by_definition = {r["definition_id"]: r for r in rows}
    assert by_definition[106806]["repeated_effect_indices"] == [0]
    assert by_definition[106806]["row_ids"] == [19371, 19372, 19373]
    assert by_definition[141760]["repeated_effect_indices"] == [1]
    assert by_definition[141760]["row_ids"] == [24387, 24392, 24393]
    for entry_id in (101906, 136997):
        audit = rp.audit(entry_id, 1)
        assert audit.refusal == "UnsupportedEffectPoints"
        assert any(d.startswith("duplicate-effect-index-") for d in audit.defects)


def test_foreign_effect_point_rows(rp, census):
    """A point row whose EffectIndex exceeds the provider's effect count."""
    rows = census["effect_points"]["foreign_row_definitions"]
    assert len(rows) == 48
    sample = next(r for r in rows if r["definition_id"] == 85239)
    assert sample["provider"] == 378406 and sample["provider_effect_count"] == 1
    assert sample["row_ids"] == [23653]
    entry_id = sample["entries"][0]
    audit = rp.audit(entry_id, 1)
    assert any(d.startswith("foreign-effect-index-") for d in audit.defects)


def test_more_point_rows_than_provider_effects(census):
    rows = census["effect_points"]["more_rows_than_effects_definitions"]
    assert len(rows) == 41
    sample = next(r for r in rows if r["definition_id"] == 96551)
    assert (sample["point_rows"], sample["provider_effect_count"]) == (5, 2)


def test_more_than_four_point_rows_is_a_retention_defect(rp, census):
    """Core keeps only four shaped effects (``MAX_RANK_SHAPED_EFFECTS``)."""
    causes = census["effect_points"]["unsupported_effect_points_causes"]
    assert causes["point-rows-exceed-retention"] == 12
    entry_id = census["effect_points"]["unsupported_effect_points_examples"][
        "point-rows-exceed-retention"][0]
    audit = rp.audit(entry_id, 1)
    assert len(audit.point_rows) > 4
    assert [row.retained_by_core for row in audit.point_rows][:4] == [True] * 4
    assert audit.point_rows[4].retained_by_core is False


# ---------------------------------------------------------------------------
# operation types
# ---------------------------------------------------------------------------

def test_operation_type_none_is_build_skew_core_refuses(census):
    operations = census["operation_types"]
    assert operations["distribution"] == {"-1": 7, "0": 907, "1": 492}
    rows = operations["beyond_set_and_multiply"]
    assert [r["row_id"] for r in rows] == [16589, 16601, 22307, 22335, 22348, 22351, 22362]
    assert {r["operation_type"] for r in rows} == {-1}
    assert {r["trinity_name"] for r in rows} == {"None"}
    assert TRINITY_OPERATIONS[-1] == "None"
    # `trinity` keeps the authored value; Core refuses instead.
    assert operations["trinity"]["core_verdict"].startswith("conservative")


# ---------------------------------------------------------------------------
# rank domain
# ---------------------------------------------------------------------------

def test_rank_domain_is_a_core_cap_not_a_source_boundary(census):
    domain = census["rank_domain"]
    assert domain["max_ranks_resolvable"] == {"1": 7712, "2": 567}
    assert domain["rows_with_max_ranks_ge_3"] == 1068
    assert domain["largest_max_ranks"] == 999
    would_pass = domain["would_pass_if_cap_lifted"]
    assert len(would_pass) == 24
    assert max(r["max_ranks"] for r in would_pass) == 90
    assert domain["verdict"].startswith("Core cap")


def test_a_three_rank_curve_is_an_exact_rank_table(rp):
    """Entry 123788 has MaxRanks 3 and a curve with exactly three integer-rank points."""
    audit = rp.audit(123788, 1)
    assert audit.refusal == "UnsupportedRank"
    assert audit.max_ranks == 3
    assert audit.generalized_ok is True
    curve = audit.point_rows[0].curve
    assert curve.points == [(1.0, 5.0), (2.0, 10.0), (3.0, 15.0)]
    assert curve.defects == []
    assert audit.effects[0].amounts == {1: 5.0, 2: 10.0, 3: 15.0}


def test_node_entry_type_is_never_read(census):
    """Admitted entries already span seven NodeEntryType values, including 13."""
    assert census["rank_domain"]["node_entry_type_resolvable"] == {
        "0": 266, "1": 1887, "2": 5142, "3": 38, "6": 58, "8": 770, "13": 118,
    }


# ---------------------------------------------------------------------------
# identity
# ---------------------------------------------------------------------------

def test_minimal_sufficient_identity_tuple(census):
    identity = census["identity"]
    assert identity["minimal_sufficient_tuple"] == "(entry, rank)"
    candidates = identity["candidates"]
    assert candidates["(entry,rank)"]["colliding_keys"] == 0
    assert candidates["(entry,rank)"]["keys"] == 8846
    assert candidates["(entry,rank)"]["lossy"] is False
    for name, expected in (("(entry)", 567), ("(definition)", 567),
                           ("(provider)", 424), ("(provider,rank)", 211)):
        assert candidates[name]["colliding_keys"] == expected
        assert candidates[name]["sufficient"] is False
        assert candidates[name]["witness"]
    # Collision-free but lossy: it merges the only definition with two entries.
    assert candidates["(definition,rank)"]["sufficient"] is True
    assert candidates["(definition,rank)"]["lossy"] is True
    assert candidates["(definition,rank)"]["keys"] == 8845
    # Adding the navigation ids to (entry, rank) buys nothing.
    for name in ("(entry,definition,rank)", "(entry,definition,provider,rank)"):
        assert candidates[name]["keys"] == candidates["(entry,rank)"]["keys"]


def test_only_one_definition_is_shared_by_two_entries(census):
    shared = census["identity"]["definitions_with_multiple_entries"]
    assert shared["count"] == 1
    row = shared["rows"][0]
    assert row["definition_id"] == 131125
    assert row["entries"] == [126299, 126300]
    # They agree on every entry-carried fact, and differ only in topology.
    assert row["differ_in"] == []
    assert row["nodes"] == {"126299": [], "126300": [102244]}


def test_provider_spell_alone_cannot_identify_a_package(rp, census):
    """Same provider, same rank, different amounts -- through different definitions."""
    shared = census["identity"]["providers_with_multiple_definitions"]
    assert shared["providers_with_multiple_definitions"] == 2028
    assert shared["same_shaping"] == 1726
    assert shared["differing_shaping"] == 302
    assert shared["differing_rank_one_amounts"] == 96

    set_shaped, multiply_shaped = rp.audit(80287, 1), rp.audit(124795, 1)
    assert set_shaped.provider == multiply_shaped.provider == 117216
    assert set_shaped.definition_id == 85290 and multiply_shaped.definition_id == 129633
    assert set_shaped.max_ranks == multiply_shaped.max_ranks == 2
    # Identical authored values ...
    assert [e.authored for e in set_shaped.effects] == [4.0, 10.0]
    assert [e.authored for e in multiply_shaped.effects] == [4.0, 10.0]
    # ... and a different amount at every rank.
    assert set_shaped.shaping_kind == "set-only"
    assert multiply_shaped.shaping_kind == "multiply-only"
    assert set_shaped.selected_amounts() == [7.0, 5.0]
    assert multiply_shaped.selected_amounts() == [4.0, 10.0]
    assert rp.audit(80287, 2).selected_amounts() == [15.0, 10.0]
    assert rp.audit(124795, 2).selected_amounts() == [8.0, 20.0]


def test_heart_of_the_crusader_same_provider_different_rank_domain(rp):
    """406154 through two definitions: one reaches 20, the other never can."""
    ranked, flat = rp.audit(115483, 1), rp.audit(115441, 1)
    assert ranked.provider == flat.provider == 406154
    assert (ranked.definition_id, ranked.max_ranks) == (120495, 2)
    assert (flat.definition_id, flat.max_ranks) == (120453, 1)
    assert ranked.selected_amounts() == flat.selected_amounts() == [10.0] * 4
    assert rp.audit(115483, 2).selected_amounts() == [20.0] * 4
    assert flat.node_ids == []          # the MaxRanks-1 twin is detached
    assert [e.amounts for e in flat.effects] == [{1: 10.0}] * 4


# ---------------------------------------------------------------------------
# fact buckets + negative witnesses
# ---------------------------------------------------------------------------

def test_every_fact_lands_in_exactly_one_bucket(census):
    buckets = census["fact_buckets"]
    assert set(buckets) == set(FACT_BUCKET)
    assert {v["bucket"] for v in buckets.values()} <= set(FACT_BUCKETS)
    for name, value in buckets.items():
        assert value["why"], name
    counts = {b: sum(1 for v in buckets.values() if v["bucket"] == b) for b in FACT_BUCKETS}
    assert all(counts[b] > 0 for b in FACT_BUCKETS)
    # The three facts that must be navigation, not amount or acquisition.
    assert buckets["definition.id"]["bucket"] == "navigation"
    assert buckets["entry.NodeEntryType"]["bucket"] == "navigation"
    assert buckets["topology.TraitNodeXTraitNodeEntry"]["bucket"] == "navigation"


def test_negative_witness_classes(rp):
    corpus = RankNegatives(rp).corpus()
    classes = corpus["classes"]
    assert set(classes) == {
        "detached-entry", "foreign-definition", "rank-outside-domain",
        "max-ranks-beyond-core-cap", "overrides-spell-id-nonzero",
        "visible-spell-id-nonzero", "trait-subtree-id-nonzero", "malformed-rank-curve",
        "non-rank-table-curve", "duplicate-effect-point-rows", "foreign-effect-point-rows",
        "more-point-rows-than-effects", "operation-type-none", "incomplete-rank-shaping",
    }
    for key, value in classes.items():
        assert value["verdict"] in corpus["verdict_legend"], key
        assert value["count"] > 0, key
        assert value["rows"], key
    assert classes["detached-entry"]["count"] == 3038
    assert classes["foreign-definition"]["count"] == 177
    assert classes["operation-type-none"]["verdict"] == "conservative"
    assert classes["max-ranks-beyond-core-cap"]["verdict"] == "conservative"
    assert classes["duplicate-effect-point-rows"]["verdict"] == "semantically-right"


def test_trait_subtree_branch_is_dead_in_this_snapshot(rp):
    """All 140 TraitSubTreeID != 0 entries die earlier, on TraitDefinitionID == 0."""
    subtree = [e for e, entry in rp.traits.entries.items() if entry.trait_subtree_id]
    assert len(subtree) == 140
    for entry_id in subtree:
        assert rp.traits.entries[entry_id].definition_id == 0
        assert rp.provenance.resolve(entry_id, 1) is TraitSpellError.MISSING_DEFINITION


@pytest.mark.parametrize("entry_id,rank", [(101510, 0), (101510, 3), (117409, 2), (137000, 0)])
def test_ranks_outside_the_domain_are_refused(rp, entry_id, rank):
    assert rp.provenance.resolve(entry_id, rank) is TraitSpellError.UNSUPPORTED_RANK
    assert rp.audit(entry_id, rank).refusal == "UnsupportedRank"
