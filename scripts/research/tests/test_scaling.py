"""Effective item level stage by stage, RandPropPoints, stats, armor, weapons."""

from __future__ import annotations

import pytest
from synthetic import (
    SnapshotBuilder,
    item_row,
    minimal_curves,
    minimal_items,
    minimal_scaling,
    sparse_row,
)

from gearing import SourceError
from gearing.bonus import BonusData
from gearing.curves import Curves, round_half_away
from gearing.enums import (
    INVTYPE_2HWEAPON,
    INVTYPE_CHEST,
    INVTYPE_CLOAK,
    INVTYPE_FEET,
    INVTYPE_FINGER,
    INVTYPE_HANDS,
    INVTYPE_HEAD,
    INVTYPE_HOLDABLE,
    INVTYPE_LEGS,
    INVTYPE_NECK,
    INVTYPE_RANGED,
    INVTYPE_RANGEDRIGHT,
    INVTYPE_RELIC,
    INVTYPE_ROBE,
    INVTYPE_SHIELD,
    INVTYPE_SHOULDERS,
    INVTYPE_TRINKET,
    INVTYPE_WAIST,
    INVTYPE_WEAPON,
    INVTYPE_WEAPONMAINHAND,
    INVTYPE_WEAPONOFFHAND,
    INVTYPE_WRISTS,
    MAX_ITEM_LEVEL,
    MIN_ITEM_LEVEL,
    MOD_CRIT_RATING,
    MOD_STAMINA,
    QUALITY_ARTIFACT,
    QUALITY_EPIC,
    QUALITY_HEIRLOOM,
    QUALITY_LEGENDARY,
    QUALITY_NORMAL,
    QUALITY_POOR,
    QUALITY_RARE,
    QUALITY_UNCOMMON,
    QUALITY_WOW_TOKEN,
)
from gearing.items import ItemStore
from gearing.scaling import (
    ILVL_MULT_ARMOR,
    ILVL_MULT_JEWELRY,
    ILVL_MULT_TRINKET,
    ILVL_MULT_WEAPON,
    ScalingEngine,
    ilvl_mult_column,
    rand_prop_index,
    rand_prop_quality_column,
)


class NullSource:
    def bonuses_for_list(self, _):
        return []

    def item_effect(self, _):
        return None

    def scaling_config(self, _):
        return None

    def item_offset_curve(self, _):
        return None


def curve_rows(curve_id, points, curve_type=0):
    curves = [{"ID": curve_id, "Type": curve_type, "Flags": 0}]
    rows = [{"Pos_0": x, "Pos_1": y, "PosPreSquish_0": 0, "PosPreSquish_1": 0,
             "ID": i + 1, "CurveID": curve_id, "OrderIndex": i}
            for i, (x, y) in enumerate(points)]
    return curves, rows


def make(tmp_path, sparse, *, curves=(), points=(), **scaling):
    builder = SnapshotBuilder(tmp_path)
    minimal_items(builder, [item_row(ID=1, ClassID=sparse.pop("_class", 4),
                                     SubclassID=sparse.pop("_subclass", 2))],
                  [sparse])
    minimal_curves(builder, curves, points)
    minimal_scaling(builder, **scaling)
    tables = builder.build()
    proto = ItemStore(tables).get(1)
    engine = ScalingEngine(tables, Curves(tables))
    return proto, engine, BonusData(proto, NullSource())


# ===================================================================
# effective item level, stage by stage
# ===================================================================

def test_base_stage_only(tmp_path):
    proto, engine, bonus = make(
        tmp_path, sparse_row(ID=1, ItemLevel=200, InventoryType=INVTYPE_HEAD))
    steps = []
    assert engine.effective_item_level(proto, bonus, player_level=90,
                                       provenance=steps) == 200
    assert [s["step"] for s in steps] == ["base"]


def test_player_level_curve_then_item_level_bonus(tmp_path):
    curves, points = curve_rows(500, [(1.0, 10.0), (100.0, 1000.0)])
    proto, engine, bonus = make(
        tmp_path,
        sparse_row(ID=1, ItemLevel=200, InventoryType=INVTYPE_HEAD,
                   PlayerLevelToItemLevelCurveID=500),
        curves=curves, points=points)
    bonus.ItemLevelBonus = 13
    steps = []
    level = engine.effective_item_level(proto, bonus, player_level=50,
                                        provenance=steps)
    raw = ((50 - 1) / 99) * 990 + 10
    assert level == round_half_away(raw) + 13
    assert [s["step"] for s in steps] == [
        "base", "player-level-to-item-level-curve", "item-level-bonus"]


def test_item_level_bonus_is_not_added_on_the_offset_curve_branch(tmp_path):
    """The rule this pass proved: type 1 is ignored once an offset curve is set."""
    curves, points = curve_rows(500, [(0.0, 0.0), (1300.0, 1300.0)])
    proto, engine, bonus = make(
        tmp_path,
        sparse_row(ID=1, ItemLevel=200, InventoryType=INVTYPE_HEAD),
        curves=curves, points=points)
    bonus.ItemLevelOffsetCurveId = 500
    bonus.ItemLevelOffsetItemLevel = 292
    bonus.ItemLevelBonus = 13
    steps = []
    assert engine.effective_item_level(proto, bonus, player_level=90,
                                       provenance=steps) == 292
    assert [s["step"] for s in steps] == ["base", "item-level-offset-curve"]


def test_offset_curve_adds_its_offset(tmp_path):
    curves, points = curve_rows(500, [(0.0, 0.0), (1300.0, 1300.0)])
    proto, engine, bonus = make(
        tmp_path, sparse_row(ID=1, ItemLevel=200, InventoryType=INVTYPE_HEAD),
        curves=curves, points=points)
    bonus.ItemLevelOffsetCurveId = 500
    bonus.ItemLevelOffsetItemLevel = 100
    bonus.ItemLevelOffset = 7
    assert engine.effective_item_level(proto, bonus, player_level=90) == 107


def test_offset_curve_out_of_domain_is_flagged(tmp_path):
    curves, points = curve_rows(500, [(1.0, 1.0), (80.0, 80.0)])
    proto, engine, bonus = make(
        tmp_path, sparse_row(ID=1, ItemLevel=200, InventoryType=INVTYPE_HEAD),
        curves=curves, points=points)
    bonus.ItemLevelOffsetCurveId = 500
    bonus.ItemLevelOffsetItemLevel = 0     # what ITEM_BONUS_SCALING_CONFIG sets
    steps = []
    assert engine.effective_item_level(proto, bonus, player_level=90,
                                       provenance=steps) == 1
    offset_step = steps[-1]
    assert offset_step["out_of_domain"] == "below-first-point"
    assert offset_step["curve_x_range"] == [1.0, 80.0]


def test_fixed_level_overrides_content_tuning_clamp(tmp_path):
    curves, points = curve_rows(500, [(1.0, 1.0), (100.0, 100.0)])
    tuning = [{"ID": 9, "Flags": 0, "MinLevelSquish": 70, "MaxLevelSquish": 80,
               "MinLevelScalingOffset": 0, "MaxLevelScalingOffset": 0}]
    proto, engine, bonus = make(
        tmp_path,
        sparse_row(ID=1, ItemLevel=200, InventoryType=INVTYPE_HEAD,
                   PlayerLevelToItemLevelCurveID=500, ContentTuningID=9),
        curves=curves, points=points, content_tuning=tuning)
    # Without a fixed level, player level 90 clamps to the tuning max of 80.
    assert engine.effective_item_level(proto, bonus, player_level=90) == 80
    # With one, the clamp is skipped entirely.
    assert engine.effective_item_level(proto, bonus, player_level=90,
                                       fixed_level=95) == 95


def test_content_tuning_disabled_for_item_skips_the_clamp(tmp_path):
    curves, points = curve_rows(500, [(1.0, 1.0), (100.0, 100.0)])
    tuning = [{"ID": 9, "Flags": 0x04, "MinLevelSquish": 70,
               "MaxLevelSquish": 80, "MinLevelScalingOffset": 0,
               "MaxLevelScalingOffset": 0}]
    proto, engine, bonus = make(
        tmp_path,
        sparse_row(ID=1, ItemLevel=200, InventoryType=INVTYPE_HEAD,
                   PlayerLevelToItemLevelCurveID=500, ContentTuningID=9),
        curves=curves, points=points, content_tuning=tuning)
    assert engine.effective_item_level(proto, bonus, player_level=90) == 90


def test_content_tuning_calc_types_resolve_against_expansion_caps(tmp_path):
    from gearing.enums import CURRENT_EXPANSION, EXPANSION_MAX_LEVEL
    tuning = [{"ID": 9, "Flags": 0, "MinLevelSquish": 0, "MaxLevelSquish": 0,
               "MinLevelScalingOffset": 1, "MaxLevelScalingOffset": 2}]
    proto, engine, _ = make(
        tmp_path, sparse_row(ID=1, ItemLevel=1, InventoryType=INVTYPE_HEAD),
        content_tuning=tuning)
    assert engine.content_tuning_levels(9) == (
        1, EXPANSION_MAX_LEVEL[CURRENT_EXPANSION])


def test_gem_item_level_bonus_is_added_after_both_branches(tmp_path):
    proto, engine, bonus = make(
        tmp_path, sparse_row(ID=1, ItemLevel=200, InventoryType=INVTYPE_HEAD))
    bonus.GemItemLevelBonus = [5, 3, 0]
    steps = []
    assert engine.effective_item_level(proto, bonus, player_level=90,
                                       provenance=steps) == 208
    assert steps[-1]["step"] == "gem-item-level-bonus"


def test_pvp_base_replaces_and_increment_adds(tmp_path):
    proto, engine, bonus = make(
        tmp_path, sparse_row(ID=1, ItemLevel=200, InventoryType=INVTYPE_HEAD))
    bonus.PvpItemLevel = 300
    bonus.PvpItemLevelBonus = 13
    assert engine.effective_item_level(proto, bonus, player_level=90) == 200
    assert engine.effective_item_level(proto, bonus, player_level=90,
                                       pvp_bonus=True) == 313


def test_squish_era_transforms_in_id_order_and_stops_at_the_patch(tmp_path):
    curves_a, points_a = curve_rows(600, [(0.0, 0.0), (1000.0, 500.0)])
    curves_b, points_b = curve_rows(601, [(0.0, 0.0), (1000.0, 100.0)])
    proto, engine, bonus = make(
        tmp_path,
        sparse_row(ID=1, ItemLevel=400, InventoryType=INVTYPE_HEAD),
        curves=curves_a + curves_b, points=points_a + points_b,
        squish_eras=[{"ID": 1, "Patch": 0, "CurveID": 0, "Flags": 0},
                     {"ID": 2, "Patch": 110000, "CurveID": 600, "Flags": 0},
                     {"ID": 3, "Patch": 120000, "CurveID": 601, "Flags": 0}])
    # Realm below both patches: nothing applies.
    assert engine.effective_item_level(proto, bonus, player_level=90,
                                       current_build_patch=100000) == 400
    # Realm at 11.x: era 2 only -> 400 * 0.5
    assert engine.effective_item_level(proto, bonus, player_level=90,
                                       current_build_patch=110000) == 200
    # Realm at 12.x: era 2 then era 3 -> 200 * 0.1
    assert engine.effective_item_level(proto, bonus, player_level=90,
                                       current_build_patch=120000) == 20


def test_squish_starts_after_the_items_own_era(tmp_path):
    curves, points = curve_rows(601, [(0.0, 0.0), (1000.0, 100.0)])
    proto, engine, bonus = make(
        tmp_path,
        sparse_row(ID=1, ItemLevel=400, InventoryType=INVTYPE_HEAD,
                   ItemSquishEraID=2),
        curves=curves, points=points,
        squish_eras=[{"ID": 1, "Patch": 0, "CurveID": 0, "Flags": 0},
                     {"ID": 2, "Patch": 120000, "CurveID": 601, "Flags": 0}])
    # The item is already in era 2, so era 2's curve must not re-apply.
    assert engine.effective_item_level(proto, bonus, player_level=90,
                                       current_build_patch=120100) == 400


def test_squish_era_skip_flag_and_ignore_squish(tmp_path):
    curves, points = curve_rows(601, [(0.0, 0.0), (1000.0, 100.0)])
    proto, engine, bonus = make(
        tmp_path,
        sparse_row(ID=1, ItemLevel=400, InventoryType=INVTYPE_HEAD),
        curves=curves, points=points,
        squish_eras=[{"ID": 1, "Patch": 0, "CurveID": 0, "Flags": 0},
                     {"ID": 2, "Patch": 120000, "CurveID": 601, "Flags": 1}])
    assert engine.effective_item_level(proto, bonus, player_level=90,
                                       current_build_patch=120100) == 400
    bonus.IgnoreSquish = True
    assert engine.effective_item_level(proto, bonus, player_level=90,
                                       current_build_patch=120100) == 400


def test_unit_min_and_max_item_level(tmp_path):
    proto, engine, bonus = make(
        tmp_path, sparse_row(ID=1, ItemLevel=200, InventoryType=INVTYPE_HEAD))
    assert engine.effective_item_level(proto, bonus, player_level=90,
                                       min_item_level=300) == 300
    assert engine.effective_item_level(proto, bonus, player_level=90,
                                       max_item_level=100) == 100
    # The cutoff gates the floor against the pre-upgrade level.
    assert engine.effective_item_level(proto, bonus, player_level=90,
                                       min_item_level=300,
                                       min_item_level_cutoff=250) == 200
    assert engine.effective_item_level(proto, bonus, player_level=90,
                                       min_item_level=300,
                                       min_item_level_cutoff=150) == 300


def test_non_equip_items_skip_the_unit_constraints(tmp_path):
    proto, engine, bonus = make(
        tmp_path, sparse_row(ID=1, ItemLevel=200, InventoryType=0))
    assert engine.effective_item_level(proto, bonus, player_level=90,
                                       min_item_level=300,
                                       max_item_level=100) == 200


def test_global_clamp(tmp_path):
    proto, engine, bonus = make(
        tmp_path, sparse_row(ID=1, ItemLevel=5000, InventoryType=INVTYPE_HEAD))
    assert engine.effective_item_level(proto, bonus, player_level=90) == MAX_ITEM_LEVEL
    bonus.ItemLevel = 0
    assert engine.effective_item_level(proto, bonus, player_level=90) == MIN_ITEM_LEVEL


def test_stage_order_is_discriminating(tmp_path):
    """Min-floor is applied before the max-cap, and both before the global clamp."""
    proto, engine, bonus = make(
        tmp_path, sparse_row(ID=1, ItemLevel=200, InventoryType=INVTYPE_HEAD))
    steps = []
    engine.effective_item_level(proto, bonus, player_level=90,
                               min_item_level=500, max_item_level=400,
                               provenance=steps)
    assert [s["step"] for s in steps] == [
        "base", "unit-min-item-level-floor", "unit-max-item-level-cap"]


def test_item_level_rounding_boundary(tmp_path):
    curves, points = curve_rows(500, [(0.0, 0.0), (2.0, 1.0)])
    proto, engine, bonus = make(
        tmp_path,
        sparse_row(ID=1, ItemLevel=1, InventoryType=INVTYPE_HEAD,
                   PlayerLevelToItemLevelCurveID=500),
        curves=curves, points=points)
    # curve(1) == 0.5 exactly -> std::round gives 1, Python round() gives 0.
    assert engine.effective_item_level(proto, bonus, player_level=1) == 1


# ===================================================================
# RandPropPoints
# ===================================================================

@pytest.mark.parametrize("inventory_type, expected", [
    (INVTYPE_HEAD, 0), (INVTYPE_CHEST, 0), (INVTYPE_LEGS, 0),
    (INVTYPE_ROBE, 0), (INVTYPE_2HWEAPON, 0), (INVTYPE_RANGED, 0),
    (INVTYPE_SHOULDERS, 1), (INVTYPE_WAIST, 1), (INVTYPE_FEET, 1),
    (INVTYPE_HANDS, 1), (INVTYPE_TRINKET, 1),
    (INVTYPE_NECK, 2), (INVTYPE_WRISTS, 2), (INVTYPE_FINGER, 2),
    (INVTYPE_SHIELD, 2), (INVTYPE_CLOAK, 2), (INVTYPE_HOLDABLE, 2),
    (INVTYPE_WEAPON, 3), (INVTYPE_WEAPONMAINHAND, 3), (INVTYPE_WEAPONOFFHAND, 3),
    (INVTYPE_RELIC, 4),
    (0, None), (18, None),
])
def test_rand_prop_index_families(inventory_type, expected):
    assert rand_prop_index(inventory_type, 0) == expected


def test_ranged_right_splits_on_wand_subclass():
    assert rand_prop_index(INVTYPE_RANGEDRIGHT, 19) == 3    # wand
    assert rand_prop_index(INVTYPE_RANGEDRIGHT, 2) == 0     # bow


@pytest.mark.parametrize("quality, column", [
    (QUALITY_UNCOMMON, "GoodF"),
    (QUALITY_RARE, "SuperiorF"), (QUALITY_HEIRLOOM, "SuperiorF"),
    (QUALITY_EPIC, "EpicF"), (QUALITY_LEGENDARY, "EpicF"),
    (QUALITY_ARTIFACT, "EpicF"),
    (QUALITY_POOR, None), (QUALITY_NORMAL, None), (QUALITY_WOW_TOKEN, None),
])
def test_rand_prop_quality_columns(quality, column):
    assert rand_prop_quality_column(quality) == column


def _rand_prop_row(item_level, **values):
    row = {"ID": item_level}
    for prefix in ("EpicF", "SuperiorF", "GoodF", "Epic", "Superior", "Good"):
        for i in range(5):
            row[f"{prefix}_{i}"] = values.get(f"{prefix}_{i}", 0)
    return row


def test_random_property_points_reads_the_right_cell(tmp_path):
    proto, engine, _ = make(
        tmp_path, sparse_row(ID=1, ItemLevel=200, InventoryType=INVTYPE_TRINKET),
        rand_prop=[_rand_prop_row(200, EpicF_1=123.5, SuperiorF_1=99.0,
                                  GoodF_1=50.0, EpicF_0=7.0)])
    assert engine.random_property_points(200, QUALITY_EPIC, INVTYPE_TRINKET, 0) == 123.5
    assert engine.random_property_points(200, QUALITY_RARE, INVTYPE_TRINKET, 0) == 99.0
    assert engine.random_property_points(200, QUALITY_UNCOMMON, INVTYPE_TRINKET, 0) == 50.0
    assert engine.random_property_points(200, QUALITY_POOR, INVTYPE_TRINKET, 0) == 0.0
    assert engine.random_property_points(201, QUALITY_EPIC, INVTYPE_TRINKET, 0) == 0.0
    assert engine.random_property_points(200, QUALITY_EPIC, 0, 0) == 0.0


# ===================================================================
# stat generation
# ===================================================================

def test_stat_formula_and_socket_cost(tmp_path):
    proto, engine, bonus = make(
        tmp_path,
        sparse_row(ID=1, ItemLevel=200, InventoryType=INVTYPE_HEAD,
                   OverallQualityID=QUALITY_EPIC,
                   stats=[(MOD_CRIT_RATING, 5000)]),
        rand_prop=[_rand_prop_row(200, EpicF_0=100.0)],
        socket_cost=[[200, 8.0]])
    bonus.ItemStatSocketCostMultiplier[0] = 0.25
    value = engine.item_stat_value(proto, bonus, 0, 200)
    # 5000 * 100 * 0.0001 = 50 ; minus 0.25 * 8 = 2 -> 48
    assert value.value_before_socket_cost == pytest.approx(50.0)
    assert value.raw_value == pytest.approx(48.0)
    assert value.socket_cost_per_level == 8.0


def test_unscaled_stats_bypass_rand_prop(tmp_path):
    from gearing.enums import MOD_CORRUPTION
    proto, engine, bonus = make(
        tmp_path,
        sparse_row(ID=1, ItemLevel=200, InventoryType=INVTYPE_HEAD,
                   OverallQualityID=QUALITY_EPIC,
                   stats=[(MOD_CORRUPTION, 17)]))
    value = engine.item_stat_value(proto, bonus, 0, 200)
    assert value.raw_value == 17.0
    assert value.path == "verbatim-StatPercentEditor"


def test_missing_socket_cost_row_leaves_value_untouched(tmp_path):
    proto, engine, bonus = make(
        tmp_path,
        sparse_row(ID=1, ItemLevel=200, InventoryType=INVTYPE_HEAD,
                   OverallQualityID=QUALITY_EPIC,
                   stats=[(MOD_CRIT_RATING, 5000)]),
        rand_prop=[_rand_prop_row(200, EpicF_0=100.0)],
        socket_cost=[[1, 0.0]])
    bonus.ItemStatSocketCostMultiplier[0] = 0.25
    value = engine.item_stat_value(proto, bonus, 0, 200)
    assert value.socket_cost_per_level is None
    assert value.raw_value == pytest.approx(50.0)


@pytest.mark.parametrize("inventory_type, column", [
    (INVTYPE_HEAD, ILVL_MULT_ARMOR), (INVTYPE_CHEST, ILVL_MULT_ARMOR),
    (INVTYPE_NECK, ILVL_MULT_JEWELRY), (INVTYPE_FINGER, ILVL_MULT_JEWELRY),
    (INVTYPE_TRINKET, ILVL_MULT_TRINKET),
    (INVTYPE_WEAPON, ILVL_MULT_WEAPON), (INVTYPE_SHIELD, ILVL_MULT_WEAPON),
    (INVTYPE_2HWEAPON, ILVL_MULT_WEAPON), (INVTYPE_HOLDABLE, ILVL_MULT_WEAPON),
    (INVTYPE_RANGEDRIGHT, ILVL_MULT_WEAPON),
])
def test_ilvl_multiplier_column_selection(inventory_type, column):
    assert ilvl_mult_column(inventory_type) == column


def test_ilvl_multiplier_missing_row_is_none(tmp_path):
    proto, engine, _ = make(
        tmp_path, sparse_row(ID=1, ItemLevel=200, InventoryType=INVTYPE_HEAD),
        ratings_mult=[[1, 1.5, 2.5, 3.5, 4.5]])
    assert engine.ilvl_stat_multiplier(engine.gt_combat_ratings_mult, 1,
                                       INVTYPE_TRINKET) == 3.5
    assert engine.ilvl_stat_multiplier(engine.gt_combat_ratings_mult, 999,
                                       INVTYPE_TRINKET) is None


# ===================================================================
# armor
# ===================================================================

ARMOR_LOCATIONS = [
    {"ID": INVTYPE_HEAD, "Clothmodifier": 1.0, "Leathermodifier": 1.0,
     "Chainmodifier": 1.0, "Platemodifier": 1.0, "Modifier": 1.0},
    {"ID": INVTYPE_CHEST, "Clothmodifier": 0.5, "Leathermodifier": 0.6,
     "Chainmodifier": 0.7, "Platemodifier": 0.8, "Modifier": 1.0},
]
ARMOR_QUALITY = [{"ID": 100, **{f"Qualitymod_{i}": 0.5 + 0.1 * i for i in range(7)}}]
ARMOR_TOTAL = [{"ID": 100, "ItemLevel": 100, "Cloth": 10.0, "Leather": 20.0,
                "Mail": 30.0, "Plate": 40.0}]


@pytest.mark.parametrize("subclass, total, modifier", [
    (1, 10.0, 0.5), (2, 20.0, 0.6), (3, 30.0, 0.7), (4, 40.0, 0.8)])
def test_armor_by_subclass_and_location(tmp_path, subclass, total, modifier):
    sparse = sparse_row(ID=1, ItemLevel=100, InventoryType=INVTYPE_CHEST,
                        OverallQualityID=QUALITY_EPIC)
    sparse["_class"] = 4
    sparse["_subclass"] = subclass
    proto, engine, _ = make(tmp_path / str(subclass), sparse,
                            armor_quality=ARMOR_QUALITY, armor_total=ARMOR_TOTAL,
                            armor_location=ARMOR_LOCATIONS)
    expected = int(0.9 * total * modifier + 0.5)     # Qualitymod_4 == 0.9
    assert engine.armor(proto, QUALITY_EPIC, 100) == expected


def test_robe_uses_the_chest_armor_location(tmp_path):
    sparse = sparse_row(ID=1, ItemLevel=100, InventoryType=INVTYPE_ROBE,
                        OverallQualityID=QUALITY_EPIC)
    sparse["_class"], sparse["_subclass"] = 4, 1
    proto, engine, _ = make(tmp_path, sparse, armor_quality=ARMOR_QUALITY,
                            armor_total=ARMOR_TOTAL,
                            armor_location=ARMOR_LOCATIONS)
    assert engine.armor(proto, QUALITY_EPIC, 100) == int(0.9 * 10.0 * 0.5 + 0.5)


def test_heirloom_armor_is_treated_as_rare(tmp_path):
    sparse = sparse_row(ID=1, ItemLevel=100, InventoryType=INVTYPE_CHEST,
                        OverallQualityID=QUALITY_HEIRLOOM)
    sparse["_class"], sparse["_subclass"] = 4, 1
    proto, engine, _ = make(tmp_path, sparse, armor_quality=ARMOR_QUALITY,
                            armor_total=ARMOR_TOTAL,
                            armor_location=ARMOR_LOCATIONS)
    rare = engine.armor(proto, QUALITY_RARE, 100)
    assert engine.armor(proto, QUALITY_HEIRLOOM, 100) == rare


def test_shield_uses_the_shield_table(tmp_path):
    sparse = sparse_row(ID=1, ItemLevel=100, InventoryType=INVTYPE_SHIELD,
                        OverallQualityID=QUALITY_EPIC)
    sparse["_class"], sparse["_subclass"] = 4, 6
    shield = [{"ID": 100, "ItemLevel": 100,
               **{f"Quality_{i}": 111.4 + i for i in range(7)}}]
    proto, engine, _ = make(tmp_path, sparse, armor_shield=shield,
                            armor_quality=ARMOR_QUALITY, armor_total=ARMOR_TOTAL,
                            armor_location=ARMOR_LOCATIONS)
    assert engine.armor(proto, QUALITY_EPIC, 100) == int(115.4 + 0.5)


def test_armor_zero_for_unsupported_quality_and_subclass(tmp_path):
    sparse = sparse_row(ID=1, ItemLevel=100, InventoryType=INVTYPE_CHEST,
                        OverallQualityID=QUALITY_EPIC)
    sparse["_class"], sparse["_subclass"] = 4, 9
    proto, engine, _ = make(tmp_path, sparse, armor_quality=ARMOR_QUALITY,
                            armor_total=ARMOR_TOTAL,
                            armor_location=ARMOR_LOCATIONS)
    assert engine.armor(proto, QUALITY_EPIC, 100) == 0
    assert engine.armor(proto, QUALITY_WOW_TOKEN, 100) == 0


# ===================================================================
# weapons
# ===================================================================

def _damage_rows(item_level, value):
    return [{"ID": item_level, "ItemLevel": item_level,
             **{f"Quality_{i}": value for i in range(7)}}]


@pytest.mark.parametrize("inventory_type, subclass, caster, expected", [
    (INVTYPE_WEAPON, 0, False, "OneHand"),
    (INVTYPE_WEAPON, 0, True, "OneHandCaster"),
    (INVTYPE_2HWEAPON, 1, False, "TwoHand"),
    (INVTYPE_2HWEAPON, 1, True, "TwoHandCaster"),
    (INVTYPE_RANGEDRIGHT, 19, False, "OneHandCaster"),   # wand
    (INVTYPE_RANGEDRIGHT, 2, False, "TwoHand"),          # bow
    (INVTYPE_RANGEDRIGHT, 2, True, "TwoHandCaster"),
    (INVTYPE_RANGED, 3, False, "TwoHand"),               # gun
    (INVTYPE_HEAD, 0, False, None),
])
def test_damage_table_selection(tmp_path, inventory_type, subclass, caster, expected):
    from gearing.enums import FLAG2_CASTER_WEAPON
    flags = [0, FLAG2_CASTER_WEAPON[1] if caster else 0, 0, 0, 0]
    sparse = sparse_row(ID=1, ItemLevel=100, InventoryType=inventory_type,
                        OverallQualityID=QUALITY_EPIC, flags=flags)
    sparse["_class"], sparse["_subclass"] = 2, subclass
    proto, engine, _ = make(tmp_path / f"{inventory_type}-{subclass}-{caster}",
                            sparse)
    assert engine.damage_table_name(proto) == expected


def test_dps_and_damage_math(tmp_path):
    sparse = sparse_row(ID=1, ItemLevel=100, InventoryType=INVTYPE_2HWEAPON,
                        OverallQualityID=QUALITY_EPIC, ItemDelay=3600,
                        DmgVariance=0.5)
    sparse["_class"], sparse["_subclass"] = 2, 1
    proto, engine, _ = make(tmp_path, sparse,
                            damage={"ItemDamageTwoHand": _damage_rows(100, 40.0)})
    low, high, dps = engine.weapon_damage(proto, QUALITY_EPIC, 100)
    avg = 40.0 * 3600 * 0.001
    assert dps == 40.0
    assert low == pytest.approx((0.5 * -0.5 + 1.0) * avg)
    assert high == pytest.approx(int(avg * (0.5 * 0.5 + 1.0) + 0.5))
    # max goes through floor(x + 0.5); min does not.
    assert float(high).is_integer()


def test_weapon_attack_power_truncates(tmp_path):
    assert ScalingEngine.weapon_attack_power(43.0660) == 258
    assert ScalingEngine.weapon_attack_power(0.99) == 5


def test_non_weapon_class_has_no_dps(tmp_path):
    sparse = sparse_row(ID=1, ItemLevel=100, InventoryType=INVTYPE_CHEST,
                        OverallQualityID=QUALITY_EPIC)
    sparse["_class"], sparse["_subclass"] = 4, 1
    proto, engine, _ = make(tmp_path, sparse)
    assert engine.dps(proto, QUALITY_EPIC, 100) == 0.0


def test_missing_damage_row_fails_closed(tmp_path):
    sparse = sparse_row(ID=1, ItemLevel=100, InventoryType=INVTYPE_2HWEAPON,
                        OverallQualityID=QUALITY_EPIC, ItemDelay=3600)
    sparse["_class"], sparse["_subclass"] = 2, 1
    proto, engine, _ = make(tmp_path, sparse,
                            damage={"ItemDamageTwoHand": _damage_rows(50, 40.0)})
    with pytest.raises(SourceError, match="AssertEntry"):
        engine.dps(proto, QUALITY_EPIC, 100)
