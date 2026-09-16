"""BonusData as a state machine: every handled ItemBonusType, and ordering.

These tests use a stub :class:`BonusSource` so a single row can be applied in
isolation, plus hostile-ordering cases where applying the same rows in a
different order gives a different answer.
"""

from __future__ import annotations

from typing import Any

import pytest
from synthetic import SnapshotBuilder, item_row, minimal_items, sparse_row

from gearing import SourceError
from gearing.bonus import (
    HANDLED_BONUS_TYPES,
    MAX_BONUS_LIST_DEPTH,
    AppliedBonus,
    BonusData,
)
from gearing.enums import (
    BONUS_APPEARANCE,
    BONUS_BONDING,
    BONUS_BONDING_WITH_PRIORITY,
    BONUS_ITEM_BONUS_LIST,
    BONUS_ITEM_EFFECT_ID,
    BONUS_ITEM_LEVEL,
    BONUS_ITEM_LEVEL_BASE,
    BONUS_ITEM_LIMIT_CATEGORY,
    BONUS_ITEM_OFFSET_CURVE,
    BONUS_OVERRIDE_CAN_DISENCHANT,
    BONUS_OVERRIDE_REQUIRED_LEVEL,
    BONUS_PVP_ITEM_LEVEL_BASE,
    BONUS_PVP_ITEM_LEVEL_INCREMENT,
    BONUS_QUALITY,
    BONUS_REPAIR_COST_MULTIPLIER,
    BONUS_REQUIRED_LEVEL,
    BONUS_REQUIRED_LEVEL_CURVE,
    BONUS_SCALING_CONFIG,
    BONUS_SCALING_CONFIG_AND_REQ_LEVEL,
    BONUS_SCALING_STAT_DISTRIBUTION,
    BONUS_SCALING_STAT_DISTRIBUTION_FIXED,
    BONUS_SOCKET,
    BONUS_STAT,
    BONUS_SUFFIX,
    MOD_AGILITY,
    MOD_CRIT_RATING,
    MOD_STAMINA,
    QUALITY_EPIC,
    QUALITY_RARE,
    QUALITY_UNCOMMON,
)
from gearing.items import ItemStore


class StubSource:
    """Minimal BonusSource so one row can be applied without a real snapshot."""

    def __init__(self, lists=None, effects=None, configs=None, offsets=None):
        self.lists = lists or {}
        self.effects = effects or {}
        self.configs = configs or {}
        self.offsets = offsets or {}

    def bonuses_for_list(self, bonus_list_id: int) -> list[dict[str, Any]]:
        return self.lists.get(bonus_list_id, [])

    def item_effect(self, effect_id: int):
        return self.effects.get(effect_id)

    def scaling_config(self, config_id: int):
        return self.configs.get(config_id)

    def item_offset_curve(self, curve_id: int):
        return self.offsets.get(curve_id)


def row(bonus_id, bonus_list_id, type_, *values, order_index=0):
    padded = tuple(list(values) + [0, 0, 0, 0])[:4]
    return {"ID": bonus_id, "ParentItemBonusListID": bonus_list_id,
            "Type": type_, "Value_0": padded[0], "Value_1": padded[1],
            "Value_2": padded[2], "Value_3": padded[3],
            "OrderIndex": order_index}


@pytest.fixture
def proto(tmp_path):
    builder = SnapshotBuilder(tmp_path / "snap")
    minimal_items(
        builder,
        [item_row(ID=1, ClassID=4, SubclassID=2)],
        [sparse_row(ID=1, ItemLevel=200, RequiredLevel=70, InventoryType=1,
                    OverallQualityID=QUALITY_RARE, LimitCategory=5, Bonding=1,
                    stats=[(MOD_AGILITY, 1000), (MOD_STAMINA, 2000)],
                    sockets=[0, 0, 0])])
    return ItemStore(builder.build()).get(1)


def apply(proto, source, *rows, list_id=100):
    bonus = BonusData(proto, source)
    source.lists[list_id] = list(rows)
    bonus.add_bonus_list(list_id)
    return bonus


# -- coverage -------------------------------------------------------------

def test_handled_type_set_matches_switch():
    # A regression guard: if a type is added to the dispatch table the test
    # suite should be updated deliberately, not silently.
    assert HANDLED_BONUS_TYPES == frozenset({
        1, 2, 3, 5, 6, 7, 8, 10, 11, 12, 13, 16, 17, 18, 19, 21, 22, 23, 27,
        35, 36, 39, 41, 42, 43, 46, 47, 48, 49, 50, 51})


def test_unhandled_type_is_recorded_not_dropped(proto):
    source = StubSource()
    bonus = apply(proto, source, row(1, 100, 4, 42))   # NAME_SUBTITLE
    assert len(bonus.unhandled) == 1
    assert bonus.unhandled[0].type == 4
    assert bonus.trace[0].handled is False


# -- item level -----------------------------------------------------------

def test_item_level_bonus_is_additive(proto):
    source = StubSource()
    bonus = apply(proto, source,
                  row(1, 100, BONUS_ITEM_LEVEL, 13),
                  row(2, 100, BONUS_ITEM_LEVEL, 13))
    assert bonus.ItemLevelBonus == 26
    assert bonus.ItemLevel == 200   # base is untouched by type 1


def test_item_level_base_is_priority_selected(proto):
    source = StubSource()
    bonus = apply(proto, source,
                  row(1, 100, BONUS_ITEM_LEVEL_BASE, 300, 5),
                  row(2, 100, BONUS_ITEM_LEVEL_BASE, 400, 9),
                  row(3, 100, BONUS_ITEM_LEVEL_BASE, 350, 1))
    assert bonus.ItemLevel == 350   # lowest Value[1] wins regardless of order


def test_item_level_base_is_order_independent(proto):
    rows = [row(1, 100, BONUS_ITEM_LEVEL_BASE, 300, 5),
            row(2, 100, BONUS_ITEM_LEVEL_BASE, 350, 1)]
    a = apply(proto, StubSource(), *rows).ItemLevel
    b = apply(proto, StubSource(), *reversed(rows)).ItemLevel
    assert a == b == 350


def test_item_level_bonus_is_order_dependent_when_mixed_with_base(proto):
    # Hostile ordering: type 1 accumulates into ItemLevelBonus and type 42
    # replaces ItemLevel, so the two never interfere -- prove that explicitly
    # rather than assuming it.
    bonus = apply(proto, StubSource(),
                  row(1, 100, BONUS_ITEM_LEVEL, 10),
                  row(2, 100, BONUS_ITEM_LEVEL_BASE, 300, 0))
    assert (bonus.ItemLevel, bonus.ItemLevelBonus) == (300, 10)


# -- quality --------------------------------------------------------------

def test_quality_first_wins_then_max(proto):
    bonus = apply(proto, StubSource(),
                  row(1, 100, BONUS_QUALITY, QUALITY_UNCOMMON),
                  row(2, 100, BONUS_QUALITY, QUALITY_EPIC),
                  row(3, 100, BONUS_QUALITY, QUALITY_RARE))
    # First sets unconditionally; later rows only raise it.
    assert bonus.Quality == QUALITY_EPIC


def test_quality_first_row_can_lower_the_template_quality(proto):
    # proto is Rare (3); the first quality bonus wins even when lower.
    bonus = apply(proto, StubSource(), row(1, 100, BONUS_QUALITY, QUALITY_UNCOMMON))
    assert bonus.Quality == QUALITY_UNCOMMON


def test_quality_is_order_dependent(proto):
    rows = [row(1, 100, BONUS_QUALITY, QUALITY_EPIC),
            row(2, 100, BONUS_QUALITY, QUALITY_UNCOMMON)]
    forward = apply(proto, StubSource(), *rows).Quality
    backward = apply(proto, StubSource(), *reversed(rows)).Quality
    assert forward == QUALITY_EPIC and backward == QUALITY_EPIC


# -- stats ----------------------------------------------------------------

def test_stat_updates_existing_slot(proto):
    bonus = apply(proto, StubSource(), row(1, 100, BONUS_STAT, MOD_AGILITY, 250))
    assert bonus.ItemStatType[0] == MOD_AGILITY
    assert bonus.StatPercentEditor[0] == 1250


def test_stat_fills_first_free_slot(proto):
    bonus = apply(proto, StubSource(), row(1, 100, BONUS_STAT, MOD_CRIT_RATING, 500))
    assert bonus.ItemStatType[2] == MOD_CRIT_RATING
    assert bonus.StatPercentEditor[2] == 500


def test_stat_accumulates_on_repeat(proto):
    bonus = apply(proto, StubSource(),
                  row(1, 100, BONUS_STAT, MOD_CRIT_RATING, 500),
                  row(2, 100, BONUS_STAT, MOD_CRIT_RATING, 300))
    assert bonus.StatPercentEditor[2] == 800


def test_stat_overflow_is_dropped(proto, tmp_path):
    builder = SnapshotBuilder(tmp_path / "full")
    minimal_items(builder, [item_row(ID=2, ClassID=4, SubclassID=2)],
                  [sparse_row(ID=2, ItemLevel=200, InventoryType=1,
                              OverallQualityID=QUALITY_RARE,
                              stats=[(i, 100) for i in range(3, 13)])])
    full = ItemStore(builder.build()).get(2)
    bonus = apply(full, StubSource(), row(1, 100, BONUS_STAT, 99, 500))
    assert 99 not in bonus.ItemStatType


# -- sockets --------------------------------------------------------------

def test_socket_fills_only_empty_slots(proto):
    bonus = apply(proto, StubSource(), row(1, 100, BONUS_SOCKET, 2, 7))
    assert bonus.SocketColor == [7, 7, 0]


def test_socket_count_is_capped_at_three(proto):
    bonus = apply(proto, StubSource(), row(1, 100, BONUS_SOCKET, 9, 7))
    assert bonus.SocketColor == [7, 7, 7]


def test_socket_does_not_overwrite_a_coloured_socket(tmp_path):
    builder = SnapshotBuilder(tmp_path / "sock")
    minimal_items(builder, [item_row(ID=3, ClassID=4, SubclassID=2)],
                  [sparse_row(ID=3, ItemLevel=200, InventoryType=1,
                              OverallQualityID=QUALITY_RARE, sockets=[5, 0, 0])])
    proto = ItemStore(builder.build()).get(3)
    bonus = apply(proto, StubSource(), row(1, 100, BONUS_SOCKET, 1, 7))
    assert bonus.SocketColor == [5, 7, 0]


# -- priority-selected fields ---------------------------------------------

@pytest.mark.parametrize("bonus_type, attribute", [
    (BONUS_SUFFIX, "Suffix"),
    (BONUS_APPEARANCE, "AppearanceModID"),
])
def test_lowest_priority_value_wins(proto, bonus_type, attribute):
    bonus = apply(proto, StubSource(),
                  row(1, 100, bonus_type, 11, 5),
                  row(2, 100, bonus_type, 22, 2),
                  row(3, 100, bonus_type, 33, 8))
    assert getattr(bonus, attribute) == 22


def test_bonding_last_write_wins_but_priority_variant_does_not(proto):
    bonus = apply(proto, StubSource(),
                  row(1, 100, BONUS_BONDING, 2),
                  row(2, 100, BONUS_BONDING, 4))
    assert bonus.Bonding == 4
    bonus = apply(proto, StubSource(),
                  row(1, 100, BONUS_BONDING_WITH_PRIORITY, 2, 1),
                  row(2, 100, BONUS_BONDING_WITH_PRIORITY, 4, 9))
    assert bonus.Bonding == 2


def test_scaling_stat_distribution_priority_and_fixed_flag(proto):
    bonus = apply(proto, StubSource(),
                  row(1, 100, BONUS_SCALING_STAT_DISTRIBUTION, 0, 5, 111, 222),
                  row(2, 100, BONUS_SCALING_STAT_DISTRIBUTION_FIXED, 0, 1, 333, 444))
    assert (bonus.ContentTuningId, bonus.PlayerLevelToItemLevelCurveId) == (333, 444)
    assert bonus.HasFixedLevel is True


def test_item_limit_category_first_only(proto):
    bonus = apply(proto, StubSource(),
                  row(1, 100, BONUS_ITEM_LIMIT_CATEGORY, 77),
                  row(2, 100, BONUS_ITEM_LIMIT_CATEGORY, 88))
    assert bonus.LimitCategory == 77


def test_required_level_is_additive_and_override_is_separate(proto):
    bonus = apply(proto, StubSource(),
                  row(1, 100, BONUS_REQUIRED_LEVEL, -10),
                  row(2, 100, BONUS_REQUIRED_LEVEL, -5),
                  row(3, 100, BONUS_OVERRIDE_REQUIRED_LEVEL, 80))
    assert bonus.RequiredLevel == 55
    assert bonus.RequiredLevelOverride == 80


def test_required_level_curve_priority_uses_value_two(proto):
    bonus = apply(proto, StubSource(),
                  row(1, 100, BONUS_REQUIRED_LEVEL_CURVE, 111, 0, 9),
                  row(2, 100, BONUS_REQUIRED_LEVEL_CURVE, 222, 555, 1))
    assert bonus.RequiredLevelCurve == 222
    assert bonus.ContentTuningId == 555


def test_repair_cost_multiplier_is_multiplicative(proto):
    bonus = apply(proto, StubSource(),
                  row(1, 100, BONUS_REPAIR_COST_MULTIPLIER, 50),
                  row(2, 100, BONUS_REPAIR_COST_MULTIPLIER, 200))
    assert bonus.RepairCostMultiplier == pytest.approx(1.0)


def test_boolean_overrides(proto):
    bonus = apply(proto, StubSource(), row(1, 100, BONUS_OVERRIDE_CAN_DISENCHANT, 0))
    assert bonus.CanDisenchant is False


# -- pvp ------------------------------------------------------------------

def test_pvp_item_level_base_priority_and_increment_additive(proto):
    bonus = apply(proto, StubSource(),
                  row(1, 100, BONUS_PVP_ITEM_LEVEL_BASE, 300, 5),
                  row(2, 100, BONUS_PVP_ITEM_LEVEL_BASE, 320, 1),
                  row(3, 100, BONUS_PVP_ITEM_LEVEL_INCREMENT, 7),
                  row(4, 100, BONUS_PVP_ITEM_LEVEL_INCREMENT, 6))
    assert bonus.PvpItemLevel == 320
    assert bonus.PvpItemLevelBonus == 13


# -- item effects ---------------------------------------------------------

def test_bonus_added_item_effect_is_appended(proto):
    effect = {"ID": 9, "LegacySlotIndex": 0, "TriggerType": 1, "Charges": 0,
              "CoolDownMSec": 0, "CategoryCoolDownMSec": 0, "SpellCategoryID": 0,
              "SpellID": 4242, "ChrSpecializationID": 0, "PlayerConditionID": 0}
    source = StubSource(effects={9: effect})
    bonus = apply(proto, source, row(1, 100, BONUS_ITEM_EFFECT_ID, 9))
    assert [e["SpellID"] for e in bonus.Effects] == [4242]


def test_bonus_added_item_effect_missing_row_is_skipped(proto):
    bonus = apply(proto, StubSource(), row(1, 100, BONUS_ITEM_EFFECT_ID, 9))
    assert bonus.Effects == []


# -- scaling configs ------------------------------------------------------

CONFIG = {"ID": 7, "ItemOffsetCurveID": 3, "ItemLevel": 292, "RequiredLevel": 90,
          "ItemSquishEraID": 2, "Flags": 0}
OFFSET = {"ID": 3, "CurveID": 88583, "Offset": 4}


def test_scaling_config_and_req_level_sets_item_level_and_required(proto):
    source = StubSource(configs={7: CONFIG}, offsets={3: OFFSET})
    bonus = apply(proto, source,
                  row(1, 100, BONUS_SCALING_CONFIG_AND_REQ_LEVEL, 7, 0))
    assert bonus.ItemLevelOffsetCurveId == 88583
    assert bonus.ItemLevelOffset == 4
    assert bonus.ItemLevelOffsetItemLevel == 292
    assert bonus.ItemSquishEraID == 2
    assert bonus.RequiredLevelOverride == 90
    assert bonus.RequiredLevelCurve == 0


def test_scaling_config_forces_offset_item_level_to_zero(proto):
    source = StubSource(configs={7: CONFIG}, offsets={3: OFFSET})
    bonus = apply(proto, source, row(1, 100, BONUS_SCALING_CONFIG, 7, 0))
    assert bonus.ItemLevelOffsetItemLevel == 0
    assert bonus.RequiredLevelOverride == 0    # type 51 never sets it


def test_scaling_config_ignore_squish_flag(proto):
    config = dict(CONFIG, Flags=1)
    source = StubSource(configs={7: config}, offsets={3: OFFSET})
    bonus = apply(proto, source, row(1, 100, BONUS_SCALING_CONFIG, 7, 0))
    assert bonus.IgnoreSquish is True


def test_scaling_config_missing_row_changes_nothing(proto):
    bonus = apply(proto, StubSource(), row(1, 100, BONUS_SCALING_CONFIG, 7, 0))
    assert bonus.ItemLevelOffsetCurveId == 0


def test_item_offset_curve_uses_value_three_as_priority(proto):
    bonus = apply(proto, StubSource(),
                  row(1, 100, BONUS_ITEM_OFFSET_CURVE, 111, 50, 0, 9),
                  row(2, 100, BONUS_ITEM_OFFSET_CURVE, 222, 60, 0, 1))
    assert (bonus.ItemLevelOffsetCurveId, bonus.ItemLevelOffsetItemLevel) == (222, 60)


def test_scaling_priority_is_shared_between_types_11_48_49_51(proto):
    # All four write the same _scaling_priority slot, so a low-priority type 11
    # blocks a later type 48.
    bonus = apply(proto, StubSource(),
                  row(1, 100, BONUS_SCALING_STAT_DISTRIBUTION, 0, 0, 111, 222),
                  row(2, 100, BONUS_ITEM_OFFSET_CURVE, 333, 44, 0, 5))
    assert bonus.ItemLevelOffsetCurveId == 0
    assert bonus.PlayerLevelToItemLevelCurveId == 222


# -- nested lists and recursion -------------------------------------------

def test_nested_bonus_list_is_applied(proto):
    source = StubSource(lists={200: [row(9, 200, BONUS_ITEM_LEVEL, 7)]})
    bonus = apply(proto, source, row(1, 100, BONUS_ITEM_BONUS_LIST, 200))
    assert bonus.ItemLevelBonus == 7
    assert bonus.applied_bonus_lists == [100, 200]


def test_cyclic_bonus_lists_fail_closed(proto):
    source = StubSource()
    source.lists[100] = [row(1, 100, BONUS_ITEM_BONUS_LIST, 200)]
    source.lists[200] = [row(2, 200, BONUS_ITEM_BONUS_LIST, 100)]
    bonus = BonusData(proto, source)
    with pytest.raises(SourceError, match="cyclic"):
        bonus.add_bonus_list(100)


def test_recursion_guard_depth_is_generous_enough_for_real_data(proto):
    source = StubSource()
    for depth in range(MAX_BONUS_LIST_DEPTH):
        source.lists[100 + depth] = [
            row(depth, 100 + depth, BONUS_ITEM_BONUS_LIST, 101 + depth)]
    source.lists[100 + MAX_BONUS_LIST_DEPTH] = [
        row(99, 100 + MAX_BONUS_LIST_DEPTH, BONUS_ITEM_LEVEL, 1)]
    bonus = BonusData(proto, source)
    bonus.add_bonus_list(100)
    assert bonus.ItemLevelBonus == 1


# -- duplicate application ------------------------------------------------

def test_duplicate_bonus_list_doubles_additive_types(proto):
    source = StubSource(lists={100: [row(1, 100, BONUS_ITEM_LEVEL, 13)]})
    bonus = BonusData(proto, source)
    bonus.add_bonus_list(100)
    bonus.add_bonus_list(100)
    assert bonus.ItemLevelBonus == 26
    assert bonus.applied_bonus_lists == [100, 100]


def test_duplicate_bonus_list_does_not_double_priority_types(proto):
    source = StubSource(lists={100: [row(1, 100, BONUS_ITEM_LEVEL_BASE, 300, 0)]})
    bonus = BonusData(proto, source)
    bonus.add_bonus_list(100)
    bonus.add_bonus_list(100)
    assert bonus.ItemLevel == 300
