"""Gems, enchantments, ItemEffect classification and ItemSet thresholds."""

from __future__ import annotations

import pytest
from synthetic import (
    SnapshotBuilder,
    item_row,
    minimal_curves,
    minimal_items,
    minimal_misc,
    sparse_row,
)

from gearing import SourceError, UnsupportedSource
from gearing.curves import Curves
from gearing.effects import (
    COMBAT_RELEVANT_TRIGGERS,
    TRIGGER_ON_EQUIP,
    TRIGGER_ON_LOOTED,
    TRIGGER_ON_PROC,
    TRIGGER_ON_USE,
    classify_effect,
)
from gearing.enchants import (
    ENCHANT_BONUS_LIST_CURVE,
    ENCHANT_BONUS_LIST_ID,
    ENCHANT_COMBAT_SPELL,
    ENCHANT_EQUIP_SPELL,
    ENCHANT_PRISMATIC_SOCKET,
    ENCHANT_STAT,
    ENCHANT_USE_SPELL,
    SCALING_CLASS_TO_COLUMN,
    SPELL_SCALING_COLUMNS,
    EnchantEngine,
    spell_scaling_column,
)
from gearing.enums import MOD_CRIT_RATING, MOD_VERSATILITY, QUALITY_EPIC
from gearing.items import ItemStore
from gearing.sets import SetEngine


def enchant_row(enchant_id, **kwargs):
    row = {"ID": enchant_id, "Name_lang": f"Enchant {enchant_id}",
           "HordeName_lang": "", "Duration": 0, "Flags": 0,
           "IconFileDataID": 0, "ItemLevelMin": 0, "ItemLevelMax": 0,
           "TransmogUseConditionID": 0, "TransmogCost": 0, "ItemVisual": 0,
           "RequiredSkillID": 0, "RequiredSkillRank": 0, "ItemLevel": 0,
           "Charges": 0, "ScalingClass": 0, "ScalingClassRestricted": 0,
           "Condition_ID": 0, "MinLevel": 0, "MaxLevel": 0}
    for i in range(3):
        row[f"EffectArg_{i}"] = 0
        row[f"EffectScalingPoints_{i}"] = 0.0
        row[f"EffectPointsMin_{i}"] = 0
        row[f"Effect_{i}"] = 0
    row.update(kwargs)
    return row


def build(tmp_path, enchants=(), gem_properties=(), items=(), sparses=(),
          spell_scaling=None, curves=(), points=()):
    builder = SnapshotBuilder(tmp_path)
    minimal_items(builder, items or [item_row(ID=1)],
                  sparses or [sparse_row(ID=1)])
    minimal_curves(builder, curves, points)
    minimal_misc(builder, enchants=enchants, gem_properties=gem_properties,
                 spell_scaling=spell_scaling)
    tables = builder.build()
    return tables, EnchantEngine(tables, Curves(tables)), ItemStore(tables)


# ===================================================================
# enchant classification
# ===================================================================

def test_direct_stat_enchant(tmp_path):
    _, engine, _ = build(tmp_path, enchants=[enchant_row(
        1, Effect_0=ENCHANT_STAT, EffectArg_0=MOD_CRIT_RATING,
        EffectPointsMin_0=120)])
    enchant = engine.describe(1)
    effect = enchant.effects[0]
    assert effect.stat_type == MOD_CRIT_RATING
    assert effect.ratings == ("CritMelee", "CritRanged", "CritSpell")
    assert effect.acquisition == "direct stat/rating addition"
    assert enchant.spell_roots == []


def test_two_stat_slots_are_independent(tmp_path):
    _, engine, _ = build(tmp_path, enchants=[enchant_row(
        1, Effect_0=ENCHANT_STAT, EffectArg_0=MOD_CRIT_RATING,
        EffectPointsMin_0=120,
        Effect_1=ENCHANT_STAT, EffectArg_1=MOD_VERSATILITY,
        EffectPointsMin_1=60)])
    enchant = engine.resolve(1, 90)
    assert [(e.stat_name, e.resolved_amount) for e in enchant.effects] == [
        ("CritRating", 120), ("Versatility", 60)]


@pytest.mark.parametrize("effect_type, expected_route", [
    (ENCHANT_EQUIP_SPELL, "equip aura"),
    (ENCHANT_COMBAT_SPELL, "proc root"),
    (ENCHANT_USE_SPELL, "on-use root"),
])
def test_spell_granting_enchants(tmp_path, effect_type, expected_route):
    _, engine, _ = build(tmp_path / str(effect_type), enchants=[enchant_row(
        1, Effect_0=effect_type, EffectArg_0=12345)])
    enchant = engine.describe(1)
    assert enchant.effects[0].spell_id == 12345
    assert enchant.effects[0].acquisition.startswith(expected_route)
    assert enchant.spell_roots[0][0] == 12345


def test_socket_mutation_enchant_grants_nothing(tmp_path):
    _, engine, _ = build(tmp_path, enchants=[enchant_row(
        1, Effect_0=ENCHANT_PRISMATIC_SOCKET, EffectArg_0=1)])
    enchant = engine.describe(1)
    assert enchant.spell_roots == []
    assert "socket" in enchant.effects[0].acquisition


def test_bonus_list_enchant_is_flagged_as_gem_only(tmp_path):
    _, engine, _ = build(tmp_path, enchants=[enchant_row(
        1, Effect_0=ENCHANT_BONUS_LIST_ID, EffectArg_0=777)])
    effect = engine.describe(1).effects[0]
    assert effect.bonus_list_id == 777
    assert "gem only" in effect.acquisition


def test_unknown_enchant_fails_closed(tmp_path):
    _, engine, _ = build(tmp_path)
    with pytest.raises(SourceError, match="unknown SpellItemEnchantment"):
        engine.describe(999)


# ===================================================================
# SpellScaling
# ===================================================================

def test_spell_scaling_column_map_matches_the_consumer():
    assert SPELL_SCALING_COLUMNS[15] == "Item"
    assert spell_scaling_column(-1) == spell_scaling_column(-7) == 15
    assert spell_scaling_column(-2) == SPELL_SCALING_COLUMNS.index("Consumable")
    assert spell_scaling_column(1) == SPELL_SCALING_COLUMNS.index("Warrior")
    assert spell_scaling_column(13) == SPELL_SCALING_COLUMNS.index("Evoker")
    assert spell_scaling_column(-10) == SPELL_SCALING_COLUMNS.index("ManaConsumable")
    assert spell_scaling_column(99) is None


def test_scaled_stat_amount_truncates(tmp_path):
    scaling = [[level] + [10.0 * level] * 24 for level in range(1, 91)]
    _, engine, _ = build(
        tmp_path,
        enchants=[enchant_row(1, Effect_0=ENCHANT_STAT,
                              EffectArg_0=MOD_CRIT_RATING,
                              EffectScalingPoints_0=0.6839, ScalingClass=-7)],
        spell_scaling=scaling)
    enchant = engine.describe(1)
    amount, detail = engine.stat_amount(enchant, 0, 90)
    # MaxLevel is 0, so the cap comes from the table row count - 1 == 90.
    assert detail["scaling_level"] == 90
    assert detail["spell_scaling_value"] == 900.0
    assert amount == int(0.6839 * 900.0)     # uint32 truncation, not round


def test_scaling_level_is_clamped_to_min_and_max(tmp_path):
    scaling = [[level] + [float(level)] * 24 for level in range(1, 91)]
    _, engine, _ = build(
        tmp_path,
        enchants=[enchant_row(1, Effect_0=ENCHANT_STAT,
                              EffectArg_0=MOD_CRIT_RATING,
                              EffectScalingPoints_0=1.0, ScalingClass=-7,
                              MaxLevel=80)],
        spell_scaling=scaling)
    enchant = engine.describe(1)
    # minLevel is 60 for a non-gem enchant, maxLevel comes from MaxLevel.
    assert engine.stat_amount(enchant, 0, 10)[1]["scaling_level"] == 60
    assert engine.stat_amount(enchant, 0, 70)[1]["scaling_level"] == 70
    assert engine.stat_amount(enchant, 0, 90)[1]["scaling_level"] == 80


def test_scale_as_a_gem_lowers_the_min_level(tmp_path):
    scaling = [[level] + [float(level)] * 24 for level in range(1, 91)]
    _, engine, _ = build(
        tmp_path,
        enchants=[enchant_row(1, Effect_0=ENCHANT_STAT,
                              EffectArg_0=MOD_CRIT_RATING,
                              EffectScalingPoints_0=1.0, ScalingClass=-7,
                              Flags=0x4)],
        spell_scaling=scaling)
    enchant = engine.describe(1)
    assert engine.stat_amount(enchant, 0, 10)[1]["scaling_level"] == 10


def test_amount_is_floored_at_one(tmp_path):
    scaling = [[level] + [0.0] * 24 for level in range(1, 91)]
    _, engine, _ = build(
        tmp_path,
        enchants=[enchant_row(1, Effect_0=ENCHANT_STAT,
                              EffectArg_0=MOD_CRIT_RATING,
                              EffectScalingPoints_0=1.0, ScalingClass=-7)],
        spell_scaling=scaling)
    assert engine.stat_amount(engine.describe(1), 0, 90)[0] == 1


def test_restricted_scaling_class_swap(tmp_path):
    scaling = [[level] + [float(i) for i in range(24)] for level in range(1, 91)]
    _, engine, _ = build(
        tmp_path,
        enchants=[enchant_row(1, Effect_0=ENCHANT_STAT,
                              EffectArg_0=MOD_CRIT_RATING,
                              EffectScalingPoints_0=1.0, ScalingClass=-7,
                              ScalingClassRestricted=-2)],
        spell_scaling=scaling)
    enchant = engine.describe(1)
    assert engine.stat_amount(enchant, 0, 90)[1]["spell_scaling_column"] == "Item"
    assert engine.stat_amount(enchant, 0, 90, restricted=True
                              )[1]["spell_scaling_column"] == "Consumable"


def test_unmappable_scaling_class_fails_closed(tmp_path):
    scaling = [[level] + [1.0] * 24 for level in range(1, 91)]
    _, engine, _ = build(
        tmp_path,
        enchants=[enchant_row(1, Effect_0=ENCHANT_STAT,
                              EffectArg_0=MOD_CRIT_RATING,
                              EffectScalingPoints_0=1.0, ScalingClass=99)],
        spell_scaling=scaling)
    with pytest.raises(UnsupportedSource, match="ScalingClass"):
        engine.stat_amount(engine.describe(1), 0, 90)


# ===================================================================
# gems
# ===================================================================

def test_gem_chain_item_to_enchant(tmp_path):
    _, engine, store = build(
        tmp_path,
        enchants=[enchant_row(50, Effect_0=ENCHANT_STAT,
                              EffectArg_0=MOD_CRIT_RATING,
                              EffectPointsMin_0=99)],
        gem_properties=[{"ID": 40, "Enchant_ID": 50, "Type": 14}],
        items=[item_row(ID=2)],
        sparses=[sparse_row(ID=2, name="Test Gem", Gem_properties=40,
                            ItemLevel=610)])
    described = engine.describe_gem(store.get(2), 90)
    assert described["gem_properties_id"] == 40
    assert described["enchant_id"] == 50
    assert described["gem_type_mask"] == 14
    assert described["enchant"]["effects"][0]["resolved_amount"] == 99


def test_gem_without_gem_properties_fails_closed(tmp_path):
    _, engine, store = build(
        tmp_path, items=[item_row(ID=2)],
        sparses=[sparse_row(ID=2, Gem_properties=40)])
    with pytest.raises(SourceError, match="GemProperties"):
        engine.describe_gem(store.get(2), 90)


def test_gem_fits_socket_uses_the_colour_mask(tmp_path):
    _, engine, _ = build(tmp_path)
    # Socket colour 7 is prismatic: Red|Yellow|Blue == 0xE.
    assert engine.gem_fits_socket(0x2, 7) is True
    assert engine.gem_fits_socket(0x1, 7) is False   # meta gem in a prismatic
    assert engine.gem_fits_socket(0x1, 1) is True    # meta gem in a meta socket
    with pytest.raises(UnsupportedSource):
        engine.gem_fits_socket(0x2, 99)


def test_gem_item_level_bonus_from_bonus_list(tmp_path):
    _, engine, _ = build(tmp_path, enchants=[enchant_row(
        50, Effect_0=ENCHANT_BONUS_LIST_ID, EffectArg_0=777)])
    bonuses = {777: [{"ID": 1, "Type": 1, "Value_0": 6},
                     {"ID": 2, "Type": 3, "Value_0": 4}]}
    delta, trail = engine.gem_item_level_bonus(
        engine.describe(50), 610, lambda d: 0, lambda l: bonuses.get(l, []))
    assert delta == 6              # only ITEM_BONUS_ITEM_LEVEL counts
    assert trail[0]["type"] == "BonusListId"


def test_gem_item_level_bonus_from_curve(tmp_path):
    curves = [{"ID": 1718, "Type": 0, "Flags": 0}]
    points = [{"Pos_0": 0.0, "Pos_1": 0.0, "PosPreSquish_0": 0,
               "PosPreSquish_1": 0, "ID": 1, "CurveID": 1718, "OrderIndex": 0},
              {"Pos_0": 1000.0, "Pos_1": 100.0, "PosPreSquish_0": 0,
               "PosPreSquish_1": 0, "ID": 2, "CurveID": 1718, "OrderIndex": 1}]
    _, engine, _ = build(
        tmp_path,
        enchants=[enchant_row(50, Effect_0=ENCHANT_BONUS_LIST_CURVE)],
        curves=curves, points=points)
    bonuses = {900: [{"ID": 1, "Type": 1, "Value_0": 11}]}
    delta, trail = engine.gem_item_level_bonus(
        engine.describe(50), 500, lambda d: 900 if d == 50 else 0,
        lambda l: bonuses.get(l, []))
    assert trail[0]["raw_y"] == pytest.approx(50.0)
    assert trail[0]["bonus_list_id"] == 900
    assert delta == 11


# ===================================================================
# ItemEffect classification
# ===================================================================

def effect_row(**kwargs):
    row = {"ID": 1, "LegacySlotIndex": 0, "TriggerType": 0, "Charges": 0,
           "CoolDownMSec": 0, "CategoryCoolDownMSec": 0, "SpellCategoryID": 0,
           "SpellID": 0, "ChrSpecializationID": 0, "PlayerConditionID": 0}
    row.update(kwargs)
    return row


@pytest.mark.parametrize("trigger, name, relevant", [
    (TRIGGER_ON_USE, "OnUse", True),
    (TRIGGER_ON_EQUIP, "OnEquip", True),
    (TRIGGER_ON_PROC, "OnProc", True),
    (TRIGGER_ON_LOOTED, "OnLooted", False),
])
def test_effect_trigger_classification(trigger, name, relevant):
    effect = classify_effect(effect_row(TriggerType=trigger, SpellID=42))
    assert effect.trigger_name == name
    assert effect.combat_relevant is relevant
    assert effect.route


def test_combat_relevant_trigger_set():
    assert COMBAT_RELEVANT_TRIGGERS == {TRIGGER_ON_USE, TRIGGER_ON_EQUIP,
                                        TRIGGER_ON_PROC}


def test_effect_carries_its_gates():
    effect = classify_effect(effect_row(
        TriggerType=1, SpellID=42, ChrSpecializationID=269,
        PlayerConditionID=7, CoolDownMSec=120000, SpellCategoryID=1141))
    assert (effect.chr_specialization_id, effect.player_condition_id) == (269, 7)
    assert (effect.cooldown_ms, effect.spell_category_id) == (120000, 1141)


def test_item_effects_are_ordered_by_legacy_slot_index(tmp_path):
    builder = SnapshotBuilder(tmp_path)
    minimal_items(
        builder, [item_row(ID=1)], [sparse_row(ID=1)],
        effects=[effect_row(ID=10, LegacySlotIndex=2, SpellID=102),
                 effect_row(ID=11, LegacySlotIndex=0, SpellID=100),
                 effect_row(ID=12, LegacySlotIndex=1, SpellID=101)],
        x_effects=[{"ID": 1, "ItemEffectID": 10, "ItemID": 1},
                   {"ID": 2, "ItemEffectID": 11, "ItemID": 1},
                   {"ID": 3, "ItemEffectID": 12, "ItemID": 1}])
    store = ItemStore(builder.build())
    assert [e["SpellID"] for e in store.get(1).effects] == [100, 101, 102]


def test_equal_legacy_slot_indices_insert_before(tmp_path):
    """lower_bound insertion puts the later cross-table row first."""
    builder = SnapshotBuilder(tmp_path)
    minimal_items(
        builder, [item_row(ID=1)], [sparse_row(ID=1)],
        effects=[effect_row(ID=10, LegacySlotIndex=0, SpellID=100),
                 effect_row(ID=11, LegacySlotIndex=0, SpellID=101)],
        x_effects=[{"ID": 1, "ItemEffectID": 10, "ItemID": 1},
                   {"ID": 2, "ItemEffectID": 11, "ItemID": 1}])
    store = ItemStore(builder.build())
    assert [e["SpellID"] for e in store.get(1).effects] == [101, 100]


# ===================================================================
# item sets
# ===================================================================

def set_row(set_id, members, flags=0, name="Set"):
    row = {"ID": set_id, "Name_lang": name, "SetFlags": flags,
           "RequiredSkill": 0, "RequiredSkillRank": 0}
    for i in range(17):
        row[f"ItemID_{i}"] = members[i] if i < len(members) else 0
    return row


def build_sets(tmp_path, item_sets, spells):
    builder = SnapshotBuilder(tmp_path)
    minimal_items(builder, [item_row(ID=1)], [sparse_row(ID=1)])
    minimal_misc(builder, item_sets=item_sets, item_set_spells=spells)
    return SetEngine(builder.build())


SPELLS = [
    {"ID": 1, "ChrSpecID": 269, "SpellID": 1000, "TraitSubTreeID": 0,
     "Threshold": 2, "ItemSetID": 50},
    {"ID": 2, "ChrSpecID": 269, "SpellID": 1001, "TraitSubTreeID": 0,
     "Threshold": 4, "ItemSetID": 50},
    {"ID": 3, "ChrSpecID": 270, "SpellID": 2000, "TraitSubTreeID": 0,
     "Threshold": 2, "ItemSetID": 50},
    {"ID": 4, "ChrSpecID": 0, "SpellID": 3000, "TraitSubTreeID": 77,
     "Threshold": 2, "ItemSetID": 50},
]


def test_canonical_membership_and_thresholds(tmp_path):
    engine = build_sets(tmp_path, [set_row(50, [11, 12, 13, 14, 15])], SPELLS)
    item_set = engine.get(50)
    assert item_set.member_item_ids == (11, 12, 13, 14, 15)
    assert item_set.thresholds == (2, 4)
    assert {s.spell_id for s in item_set.spells} == {1000, 1001, 2000, 3000}


def test_threshold_counting_is_per_instance(tmp_path):
    engine = build_sets(tmp_path, [set_row(50, [11, 12])], SPELLS)
    # Two instances of the same ItemID count twice, as in ItemSetEffect.
    bonuses = engine.satisfied_bonuses([(11, 50), (11, 50)], chr_spec_id=269)
    # Spell 1000 (spec 269) and spell 3000 (spec 0, unfiltered subtree) both
    # reach their 2-piece threshold; the 4-piece one does not.
    assert sorted(b.spell_id for b in bonuses) == [1000, 3000]
    assert all(b.equipped_count == 2 for b in bonuses)


def test_spec_gate_filters_the_cast_not_the_count(tmp_path):
    engine = build_sets(tmp_path, [set_row(50, [11, 12, 13, 14])], SPELLS)
    equipped = [(11, 50), (12, 50), (13, 50), (14, 50)]
    all_bonuses = engine.satisfied_bonuses(equipped)
    assert {b.spell_id for b in all_bonuses} == {1000, 1001, 2000, 3000}
    spec_269 = engine.satisfied_bonuses(equipped, chr_spec_id=269)
    assert {b.spell_id for b in spec_269} == {1000, 1001, 3000}
    # Every reported bonus still sees the full equipped count.
    assert all(b.equipped_count == 4 for b in spec_269)


def test_trait_sub_tree_gate(tmp_path):
    engine = build_sets(tmp_path, [set_row(50, [11, 12])], SPELLS)
    equipped = [(11, 50), (12, 50)]
    got = engine.satisfied_bonuses(equipped, chr_spec_id=269,
                                   trait_sub_tree_id=1)
    assert {b.spell_id for b in got} == {1000}


def test_threshold_not_reached(tmp_path):
    engine = build_sets(tmp_path, [set_row(50, [11, 12])], SPELLS)
    assert engine.satisfied_bonuses([(11, 50)], chr_spec_id=269) == []


def test_legacy_inactive_set_grants_nothing(tmp_path):
    engine = build_sets(tmp_path, [set_row(50, [11, 12], flags=1)], SPELLS)
    assert engine.satisfied_bonuses([(11, 50), (12, 50)]) == []
    assert engine.get(50).legacy_inactive is True


def test_missing_item_set_row_fails_closed(tmp_path):
    engine = build_sets(tmp_path, [], SPELLS)
    with pytest.raises(SourceError, match="AddItemsSetItem"):
        engine.get(50)


def test_all_reported_bonuses_belong_to_the_counted_set(tmp_path):
    engine = build_sets(tmp_path, [set_row(50, [11, 12])], SPELLS)
    for bonus in engine.satisfied_bonuses([(11, 50), (12, 50)]):
        assert bonus.item_set_id == 50
        assert set(bonus.contributing_item_ids) <= {11, 12}
