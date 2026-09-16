"""Rating accumulation, conversion and diminishing returns."""

from __future__ import annotations

import pytest
from synthetic import SnapshotBuilder, minimal_curves, minimal_misc

from gearing.curves import Curves
from gearing.enums import (
    CR_INDEX,
    CR_NAMES,
    MOD_CRIT_RATING,
    MOD_HASTE_RATING,
    MOD_MASTERY_RATING,
    MOD_TO_RATINGS,
    MOD_VERSATILITY,
    RATING_DIMINISHING_GLOBAL_CURVE,
)
from gearing.ratings import RatingEngine, accumulate_ratings


def build(tmp_path, combat_ratings, global_curves=(), curves=(), points=()):
    builder = SnapshotBuilder(tmp_path)
    minimal_curves(builder, curves, points)
    minimal_misc(builder, global_curves=global_curves,
                 combat_ratings=combat_ratings)
    tables = builder.build()
    return RatingEngine(tables, Curves(tables))


def linear_curve(curve_id, points):
    curves = [{"ID": curve_id, "Type": 0, "Flags": 0}]
    rows = [{"Pos_0": x, "Pos_1": y, "PosPreSquish_0": 0, "PosPreSquish_1": 0,
             "ID": curve_id * 100 + i, "CurveID": curve_id, "OrderIndex": i}
            for i, (x, y) in enumerate(points)]
    return curves, rows


# -- CombatRatings column mapping ----------------------------------------

def test_combat_ratings_column_order_matches_the_enum():
    # CombatRatings.txt column N (after Level) is CombatRating N.
    assert CR_INDEX["Amplify"] == 0
    assert CR_INDEX["DefenseSkill"] == 1
    assert CR_INDEX["Mastery"] == 25
    assert CR_INDEX["VersatilityDamageDone"] == 28
    assert CR_INDEX["VersatilityDamageTaken"] == 30
    assert len(CR_NAMES) == 32


def test_rating_multiplier_is_the_reciprocal(tmp_path):
    row = [90] + [0.0] * 32
    row[1 + CR_INDEX["Mastery"]] = 46.0
    engine = build(tmp_path, [row])
    assert engine.rating_multiplier("Mastery", 90) == pytest.approx(1 / 46.0)


def test_rating_multiplier_falls_back_to_one(tmp_path):
    engine = build(tmp_path, [[90] + [0.0] * 32])
    # Zero column -> Trinity's "minimum coefficient" fallback.
    assert engine.rating_multiplier("Mastery", 90) == 1.0
    # Missing level row -> same fallback.
    assert engine.rating_multiplier("Mastery", 1) == 1.0


# -- diminishing ---------------------------------------------------------

def test_diminishing_curve_is_selected_by_global_curve_type(tmp_path):
    curves, points = linear_curve(7000, [(0.0, 0.0), (100.0, 50.0)])
    row = [90] + [0.0] * 32
    row[1 + CR_INDEX["Mastery"]] = 10.0
    engine = build(tmp_path, [row],
                   global_curves=[{"ID": 1, "CurveID": 7000, "Type": 1}],
                   curves=curves, points=points)
    conversion = engine.convert("Mastery", 1000.0, 90)
    assert conversion.linear_percent == pytest.approx(100.0)
    assert conversion.diminishing_curve_id == 7000
    assert conversion.final_percent == pytest.approx(50.0)
    assert conversion.curve_evaluation.consumer.endswith("/Mastery")


def test_rating_without_a_diminishing_curve_is_linear(tmp_path):
    row = [90] + [0.0] * 32
    row[1 + CR_INDEX["HitMelee"]] = 25.0
    engine = build(tmp_path, [row])
    conversion = engine.convert("HitMelee", 500.0, 90)
    assert conversion.diminishing_curve_id is None
    assert conversion.final_percent == pytest.approx(20.0)


def test_missing_global_curve_row_skips_diminishing(tmp_path):
    row = [90] + [0.0] * 32
    row[1 + CR_INDEX["Mastery"]] = 10.0
    engine = build(tmp_path, [row])   # no GlobalCurve rows at all
    conversion = engine.convert("Mastery", 1000.0, 90)
    assert conversion.diminishing_curve_id == 0
    assert conversion.final_percent == pytest.approx(100.0)


def test_resilience_player_damage_has_an_extra_exponential_stage(tmp_path):
    row = [90] + [0.0] * 32
    row[1 + CR_INDEX["ResiliencePlayerDamage"]] = 10.0
    engine = build(tmp_path, [row])
    conversion = engine.convert("ResiliencePlayerDamage", 100.0, 90)
    assert conversion.linear_percent == pytest.approx(10.0)
    assert conversion.final_percent == pytest.approx((1 - 0.99 ** 10.0) * 100.0)


@pytest.mark.parametrize("rating, curve_type", sorted(
    RATING_DIMINISHING_GLOBAL_CURVE.items()))
def test_every_diminishing_rating_has_a_global_curve_type(rating, curve_type):
    assert rating in CR_INDEX
    assert isinstance(curve_type, int)


def test_versatility_done_and_taken_use_different_curves():
    assert (RATING_DIMINISHING_GLOBAL_CURVE["VersatilityDamageDone"]
            != RATING_DIMINISHING_GLOBAL_CURVE["VersatilityDamageTaken"])


# -- accumulation --------------------------------------------------------

def test_one_item_mod_can_feed_several_rating_slots():
    assert MOD_TO_RATINGS[MOD_VERSATILITY] == (
        "VersatilityDamageDone", "VersatilityDamageTaken",
        "VersatilityHealingDone")
    assert MOD_TO_RATINGS[MOD_HASTE_RATING] == (
        "HasteMelee", "HasteRanged", "HasteSpell")
    assert MOD_TO_RATINGS[MOD_CRIT_RATING] == (
        "CritMelee", "CritRanged", "CritSpell")
    assert MOD_TO_RATINGS[MOD_MASTERY_RATING] == ("Mastery",)


def test_accumulate_ratings_fans_out_and_sums():
    totals = accumulate_ratings([
        (MOD_VERSATILITY, 100), (MOD_VERSATILITY, 50),
        (MOD_MASTERY_RATING, 7)])
    assert totals == {
        "VersatilityDamageDone": 150, "VersatilityDamageTaken": 150,
        "VersatilityHealingDone": 150, "Mastery": 7}


def test_accumulate_ignores_non_rating_stats():
    from gearing.enums import MOD_STAMINA
    assert accumulate_ratings([(MOD_STAMINA, 5000)]) == {}


# -- snapshot ------------------------------------------------------------

@pytest.mark.snapshot
def test_real_snapshot_modern_ratings_all_convert(resolver):
    for name in ("CritMelee", "HasteMelee", "Mastery", "VersatilityDamageDone",
                 "VersatilityDamageTaken", "Lifesteal", "Avoidance", "Speed"):
        conversion = resolver.ratings.convert(name, 20000.0, 90)
        assert conversion.per_point > 0.0
        assert conversion.diminishing_curve_id, f"{name} has no diminishing curve"
        # Past the first breakpoint the curve must actually bend the value down.
        assert conversion.final_percent < conversion.linear_percent, name


@pytest.mark.snapshot
def test_real_snapshot_diminishing_is_identity_near_zero(resolver):
    conversion = resolver.ratings.convert("Mastery", 1.0, 90)
    assert conversion.final_percent == pytest.approx(conversion.linear_percent,
                                                     rel=1e-6)


@pytest.mark.snapshot
def test_mastery_stops_at_the_generic_boundary(resolver, tables):
    """ChrSpecialization carries the spec's mastery spells; conversion does not.

    This pins the hand-off: everything up to ActivePlayerData::Mastery is
    generic, and the spec-specific part is ordinary spell-effect semantics
    (SPELL_ATTR8_MASTERY_AFFECTS_POINTS -> value += Mastery * BonusCoefficient).
    """
    specs = tables("ChrSpecialization")
    with_mastery = [r for r in specs
                    if int(r["MasterySpellID_0"]) or int(r["MasterySpellID_1"])]
    assert with_mastery, "no ChrSpecialization row carries a mastery spell"
    conversion = resolver.ratings.convert("Mastery", 1000.0, 90)
    assert conversion.rating == "Mastery"
    # The rating engine knows nothing about spec identity.
    assert not hasattr(conversion, "chr_specialization_id")
