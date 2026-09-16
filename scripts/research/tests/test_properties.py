"""Property and invariant tests.

Only properties the source actually proves are asserted here.  In particular
there is deliberately **no** monotonicity property over item level or stats:
nothing in the source guarantees that a higher context or a later upgrade step
always yields a higher item level, and several current items disprove it.
"""

from __future__ import annotations

import json

import pytest
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

from gearing.curves import (
    BEZIER,
    BEZIER3,
    BEZIER4,
    CATMULL_ROM,
    CONSTANT,
    COSINE,
    LINEAR,
    interpolate,
    round_half_away,
)
from gearing.effects import COMBAT_RELEVANT_TRIGGERS
from gearing.enums import MAX_ITEM_LEVEL, MIN_ITEM_LEVEL
from gearing.loadout import Loadout, LoadoutEntry, resolve_loadout
from gearing.resolver import Variant, distinct_key

SLOW = settings(max_examples=60, deadline=None,
                suppress_health_check=[HealthCheck.function_scoped_fixture])


# -- curve properties ------------------------------------------------------

finite = st.floats(min_value=-1e6, max_value=1e6, allow_nan=False,
                   allow_infinity=False)


@st.composite
def monotonic_points(draw, min_size=2, max_size=8):
    xs = draw(st.lists(st.floats(min_value=-1000, max_value=1000,
                                 allow_nan=False, allow_infinity=False),
                       min_size=min_size, max_size=max_size, unique=True))
    xs.sort()
    ys = draw(st.lists(finite, min_size=len(xs), max_size=len(xs)))
    return list(zip(xs, ys))


@given(points=monotonic_points(), x=finite)
@settings(max_examples=200, deadline=None)
def test_linear_never_leaves_the_y_hull(points, x):
    y, bracket, _ = interpolate(LINEAR, points, x)
    lo = min(p[1] for p in points)
    hi = max(p[1] for p in points)
    assert lo - 1e-6 <= y <= hi + 1e-6
    assert all(p in points for p in bracket)


@given(points=monotonic_points(), x=finite)
@settings(max_examples=200, deadline=None)
def test_linear_clamps_outside_the_domain(points, x):
    assume(x < points[0][0])
    assert interpolate(LINEAR, points, x)[0] == points[0][1]
    assert interpolate(LINEAR, points, points[-1][0] + 1e6)[0] == points[-1][1]


@given(points=monotonic_points(min_size=2, max_size=2), x=finite)
@settings(max_examples=200, deadline=None)
def test_cosine_and_linear_agree_at_the_endpoints(points, x):
    for probe in (points[0][0], points[-1][0] + 1.0):
        assert interpolate(COSINE, points, probe)[0] == \
            pytest.approx(interpolate(LINEAR, points, probe)[0])


@given(points=monotonic_points(min_size=3, max_size=3))
@settings(max_examples=200, deadline=None)
def test_bezier3_hits_its_first_and_last_control_point(points):
    assert interpolate(BEZIER3, points, points[0][0])[0] == pytest.approx(points[0][1])
    assert interpolate(BEZIER3, points, points[2][0])[0] == pytest.approx(points[2][1])


@given(points=monotonic_points(min_size=4, max_size=4))
@settings(max_examples=200, deadline=None)
def test_bezier4_hits_its_first_and_last_control_point(points):
    assert interpolate(BEZIER4, points, points[0][0])[0] == pytest.approx(points[0][1])
    assert interpolate(BEZIER4, points, points[3][0])[0] == pytest.approx(points[3][1])


@given(points=monotonic_points(min_size=5, max_size=8))
@settings(max_examples=200, deadline=None)
def test_n_point_bezier_hits_its_first_and_last_control_point(points):
    # De Casteljau accumulates rounding proportional to the largest control
    # point, so the tolerance is scaled rather than absolute.
    scale = max(abs(p[1]) for p in points) or 1.0
    tolerance = scale * 1e-9 + 1e-9
    assert interpolate(BEZIER, points, points[0][0])[0] == pytest.approx(
        points[0][1], abs=tolerance)
    assert interpolate(BEZIER, points, points[-1][0])[0] == pytest.approx(
        points[-1][1], abs=tolerance)


@given(value=st.floats(min_value=-1e9, max_value=1e9, allow_nan=False,
                       allow_infinity=False))
@settings(max_examples=500, deadline=None)
def test_round_half_away_never_moves_more_than_half(value):
    assert abs(round_half_away(value) - value) <= 0.5 + 1e-9


@given(value=st.floats(min_value=0.0, max_value=1e9, allow_nan=False,
                       allow_infinity=False))
@settings(max_examples=500, deadline=None)
def test_round_half_away_is_symmetric_about_zero(value):
    assert round_half_away(-value) == -round_half_away(value)


# -- snapshot invariants ---------------------------------------------------

pytestmark_snapshot = pytest.mark.snapshot


@pytest.mark.snapshot
def test_every_recorded_curve_evaluation_refers_to_a_real_curve(resolver, witnesses):
    known = resolver.curves.curve_ids_with_points()
    for witness in witnesses["items"]:
        got = resolver.resolve(
            witness["item_id"],
            Variant(label="x", context=witness["context"], origin="property"),
            current_build_patch=witness.get("squish_patch"))
        for evaluation in got.curve_evaluations:
            assert evaluation.curve_id in known, evaluation
            assert evaluation.point_count == len(
                resolver.curves.points(evaluation.curve_id))
            assert evaluation.consumer, "every evaluation must name its consumer"


@pytest.mark.snapshot
def test_effective_item_level_is_always_inside_the_global_clamp(resolver, witnesses):
    for witness in witnesses["items"]:
        for context in (0, 1, 3, 6, 16, 35):
            got = resolver.resolve(
                witness["item_id"], Variant(label="x", context=context,
                                            origin="property"))
            assert MIN_ITEM_LEVEL <= got.effective_item_level <= MAX_ITEM_LEVEL


@pytest.mark.snapshot
def test_resolution_is_deterministic(resolver, witnesses):
    for witness in witnesses["items"][:6]:
        variant = Variant(label="x", context=witness["context"], origin="property")
        a = resolver.resolve(witness["item_id"], variant)
        b = resolver.resolve(witness["item_id"], variant)
        assert distinct_key(a) == distinct_key(b)
        assert a.to_dict() == b.to_dict()


@pytest.mark.snapshot
def test_variant_discovery_is_deterministic(resolver, witnesses):
    for witness in witnesses["items"][:6]:
        a = resolver.discover_variants(witness["item_id"])
        b = resolver.discover_variants(witness["item_id"])
        assert [v.to_dict() for v in a] == [v.to_dict() for v in b]


@pytest.mark.snapshot
def test_resolution_serialises_round_trip(resolver, witnesses):
    for witness in witnesses["items"][:6]:
        got = resolver.resolve(witness["item_id"],
                               Variant(label="x", context=witness["context"],
                                       origin="property"))
        payload = json.loads(json.dumps(got.to_dict(), default=str))
        assert payload["effective_item_level"] == got.effective_item_level
        assert len(payload["stats"]) == len(got.stats)
        assert len(payload["curves_used"]) == len(got.curve_evaluations)


@pytest.mark.snapshot
def test_every_emitted_item_effect_resolves_to_a_source_row(resolver, witnesses):
    effects = resolver.tables("ItemEffect").by("ID")
    for witness in witnesses["item_effects"]:
        got = resolver.resolve(witness["item_id"])
        for effect in got.effects:
            assert effect.item_effect_id in effects
            row = effects[effect.item_effect_id]
            assert int(row["SpellID"]) == effect.spell_id
            assert int(row["TriggerType"]) == effect.trigger_type


@pytest.mark.snapshot
def test_every_set_threshold_references_the_same_item_set(resolver, witnesses):
    expected = witnesses["item_sets"][0]
    equipped = [(i, expected["item_set_id"]) for i in expected["member_item_ids"]]
    for bonus in resolver.sets.satisfied_bonuses(equipped):
        assert bonus.item_set_id == expected["item_set_id"]
        assert bonus.threshold <= bonus.equipped_count
        assert set(bonus.contributing_item_ids) <= set(expected["member_item_ids"])


@pytest.mark.snapshot
def test_duplicate_bonus_list_warns_and_doubles_only_additive_types(resolver):
    single = resolver.resolve(271519, Variant(label="x", context=6,
                                              origin="property"))
    doubled = resolver.resolve(
        271519, Variant(label="x", context=6, origin="property"),
        extra_bonus_lists=single.applied_bonus_lists)
    assert any("applied more than once" in w for w in doubled.warnings)
    # Every current bonus list on this item sets item level by priority, so the
    # level must NOT double; that is the property worth pinning.
    assert doubled.effective_item_level == single.effective_item_level


@pytest.mark.snapshot
def test_loadout_totals_equal_the_sum_of_their_items(resolver):
    entries = [LoadoutEntry(item_id=i, context=6)
               for i in (271517, 271518, 271519, 271520, 271522)]
    result = resolve_loadout(resolver, Loadout(entries=entries, chr_spec_id=269))
    for name, total in result.stat_totals.items():
        expected = sum(s.final_value for item in result.items
                       for s in item.stats if s.stat_name == name)
        assert total == expected
    for rating, total in result.rating_totals.items():
        expected = sum(item.rating_contributions.get(rating, 0)
                       for item in result.items)
        assert total == expected


@pytest.mark.snapshot
def test_loadout_spell_roots_are_all_attributable(resolver):
    entries = [LoadoutEntry(item_id=i, context=6)
               for i in (271517, 271518, 271519, 271520, 271522)]
    result = resolve_loadout(resolver, Loadout(entries=entries, chr_spec_id=269))
    for root in result.spell_roots:
        assert root["spell_id"]
        assert root["via"] in {"ItemEffect", "SpellItemEnchantment", "Gem",
                               "ItemSetSpell"}
        assert root["route"]


@pytest.mark.snapshot
def test_combat_relevant_triggers_cover_every_equipped_route(resolver):
    """Nothing outside the three combat triggers claims an equipped-item route."""
    from gearing.effects import TRIGGER_ROUTE
    for trigger, route in TRIGGER_ROUTE.items():
        if trigger in COMBAT_RELEVANT_TRIGGERS:
            assert "root" in route or "aura" in route
        else:
            assert "root" not in route
