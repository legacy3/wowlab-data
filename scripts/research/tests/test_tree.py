"""Bonus-tree traversal, synthetic and against the real snapshot."""

from __future__ import annotations

from typing import Any

import pytest
from synthetic import (
    SnapshotBuilder,
    item_row,
    minimal_bonus_graph,
    minimal_curves,
    minimal_items,
    sparse_row,
)

from gearing import SourceError
from gearing.curves import Curves
from gearing.enums import FLAG2_CASTER_WEAPON, FLAG4_CC_TRINKET, QUALITY_RARE
from gearing.items import ItemStore
from gearing.tree import (
    HARDCODED_SEQUENCE_TREES,
    MAX_TREE_DEPTH,
    MYTHIC_PLUS_SEQUENCE_CURVES,
    BonusGenerationParams,
    BonusTreeResolver,
)


def node(node_id, parent, **kwargs):
    row = {"ID": node_id, "ParentItemBonusTreeID": parent, "ItemContext": 0,
           "ChildItemBonusTreeID": 0, "ChildItemBonusListID": 0,
           "ChildItemLevelSelectorID": 0, "ChildItemBonusListGroupID": 0,
           "IblGroupPointsModSetID": 0, "MinMythicPlusLevel": 0,
           "MaxMythicPlusLevel": 0, "ItemCreationContextGroupID": 0, "Flags": 0}
    row.update(kwargs)
    return row


def build(tmp_path, *, sparse=None, item=None, **graph):
    curves = graph.pop("curves", ())
    points = graph.pop("points", ())
    builder = SnapshotBuilder(tmp_path)
    minimal_items(
        builder,
        [item or item_row(ID=1, ClassID=4, SubclassID=2)],
        [sparse or sparse_row(ID=1, ItemLevel=200, InventoryType=1,
                              OverallQualityID=QUALITY_RARE)])
    minimal_bonus_graph(builder, **graph)
    minimal_curves(builder, curves, points)
    tables = builder.build()
    store = ItemStore(tables)
    return store.get(1), BonusTreeResolver(tables, Curves(tables))


def resolve(proto, resolver, **params):
    return resolver.bonus_lists_for_item(proto, BonusGenerationParams(**params))


# -- context matching ------------------------------------------------------

def test_context_match_selects_the_node(tmp_path):
    proto, resolver = build(
        tmp_path,
        trees=[{"ID": 10, "Flags": 0, "InventoryTypeSlotMask": 0}],
        nodes=[node(1, 10, ItemContext=6, ChildItemBonusListID=500),
               node(2, 10, ItemContext=3, ChildItemBonusListID=501)],
        x_bonus_tree=[{"ID": 1, "ItemBonusTreeID": 10, "ItemID": 1}])
    assert resolve(proto, resolver, context=6).bonus_list_ids == [500]
    assert resolve(proto, resolver, context=3).bonus_list_ids == [501]
    assert resolve(proto, resolver, context=0).bonus_list_ids == []


def test_context_none_node_always_matches(tmp_path):
    proto, resolver = build(
        tmp_path,
        trees=[{"ID": 10, "Flags": 4, "InventoryTypeSlotMask": 0}],
        nodes=[node(1, 10, ItemContext=0, ChildItemBonusListID=500)],
        x_bonus_tree=[{"ID": 1, "ItemBonusTreeID": 10, "ItemID": 1}])
    for context in (0, 3, 99):
        assert resolve(proto, resolver, context=context).bonus_list_ids == [500]


def test_inverted_context_node_matches_everything_else(tmp_path):
    proto, resolver = build(
        tmp_path,
        trees=[{"ID": 10, "Flags": 4, "InventoryTypeSlotMask": 0}],
        nodes=[node(1, 10, ItemContext=6, Flags=1, ChildItemBonusListID=500)],
        x_bonus_tree=[{"ID": 1, "ItemBonusTreeID": 10, "ItemID": 1}])
    assert resolve(proto, resolver, context=3).bonus_list_ids == [500]
    assert resolve(proto, resolver, context=6).bonus_list_ids == []


def test_force_to_none_context_requires_none(tmp_path):
    proto, resolver = build(
        tmp_path,
        trees=[{"ID": 10, "Flags": 4, "InventoryTypeSlotMask": 0}],
        nodes=[node(1, 10, ItemContext=21, ChildItemBonusListID=500)],
        x_bonus_tree=[{"ID": 1, "ItemBonusTreeID": 10, "ItemID": 1}])
    assert resolve(proto, resolver, context=0).bonus_list_ids == [500]
    assert resolve(proto, resolver, context=3).bonus_list_ids == []


def test_creation_context_group_membership(tmp_path):
    proto, resolver = build(
        tmp_path,
        trees=[{"ID": 10, "Flags": 4, "InventoryTypeSlotMask": 0}],
        nodes=[node(1, 10, ItemCreationContextGroupID=7,
                    ChildItemBonusListID=500)],
        x_bonus_tree=[{"ID": 1, "ItemBonusTreeID": 10, "ItemID": 1}],
        creation_contexts=[{"ID": 1, "ItemContext": 3,
                            "ItemCreationContextGroupID": 7},
                           {"ID": 2, "ItemContext": 6,
                            "ItemCreationContextGroupID": 7}])
    # `if (!!(Flags & 0x1) == hasContextFromGroup) continue;` with Flags=0
    # skips the node when the context is NOT in the group.
    assert resolve(proto, resolver, context=3).bonus_list_ids == [500]
    assert resolve(proto, resolver, context=6).bonus_list_ids == [500]
    assert resolve(proto, resolver, context=99).bonus_list_ids == []


def test_inverted_creation_context_group(tmp_path):
    proto, resolver = build(
        tmp_path,
        trees=[{"ID": 10, "Flags": 4, "InventoryTypeSlotMask": 0}],
        nodes=[node(1, 10, ItemCreationContextGroupID=7, Flags=1,
                    ChildItemBonusListID=500)],
        x_bonus_tree=[{"ID": 1, "ItemBonusTreeID": 10, "ItemID": 1}],
        creation_contexts=[{"ID": 1, "ItemContext": 3,
                            "ItemCreationContextGroupID": 7}])
    # Flags=0x1 inverts: the node applies only when the context is NOT a member.
    assert resolve(proto, resolver, context=3).bonus_list_ids == []
    assert resolve(proto, resolver, context=99).bonus_list_ids == [500]


# -- tree eligibility ------------------------------------------------------

def test_inventory_type_slot_mask(tmp_path):
    proto, resolver = build(
        tmp_path,
        trees=[{"ID": 10, "Flags": 0, "InventoryTypeSlotMask": 1 << 5}],
        nodes=[node(1, 10, ChildItemBonusListID=500)],
        x_bonus_tree=[{"ID": 1, "ItemBonusTreeID": 10, "ItemID": 1}])
    # Item is INVTYPE_HEAD (1); the mask only allows INVTYPE_CHEST (5).
    assert resolve(proto, resolver).bonus_list_ids == []

    proto, resolver = build(
        tmp_path / "b",
        sparse=sparse_row(ID=1, ItemLevel=200, InventoryType=5,
                          OverallQualityID=QUALITY_RARE),
        trees=[{"ID": 10, "Flags": 0, "InventoryTypeSlotMask": 1 << 5}],
        nodes=[node(1, 10, ChildItemBonusListID=500)],
        x_bonus_tree=[{"ID": 1, "ItemBonusTreeID": 10, "ItemID": 1}])
    assert resolve(proto, resolver).bonus_list_ids == [500]


@pytest.mark.parametrize("tree_flags, caster, expected", [
    (0x8, True, [500]), (0x8, False, []),
    (0x10, True, []), (0x10, False, [500]),
])
def test_caster_weapon_tree_flags(tmp_path, tree_flags, caster, expected):
    flags = [0, FLAG2_CASTER_WEAPON[1] if caster else 0, 0, 0, 0]
    proto, resolver = build(
        tmp_path / f"{tree_flags}-{caster}",
        sparse=sparse_row(ID=1, ItemLevel=200, InventoryType=13,
                          OverallQualityID=QUALITY_RARE, flags=flags),
        trees=[{"ID": 10, "Flags": tree_flags, "InventoryTypeSlotMask": 0}],
        nodes=[node(1, 10, ChildItemBonusListID=500)],
        x_bonus_tree=[{"ID": 1, "ItemBonusTreeID": 10, "ItemID": 1}])
    assert resolve(proto, resolver).bonus_list_ids == expected


@pytest.mark.parametrize("tree_flags, cc_trinket, expected", [
    (0x20, True, [500]), (0x20, False, []),
    (0x40, True, []), (0x40, False, [500]),
])
def test_cc_trinket_tree_flags(tmp_path, tree_flags, cc_trinket, expected):
    flags = [0, 0, 0, FLAG4_CC_TRINKET[1] if cc_trinket else 0, 0]
    proto, resolver = build(
        tmp_path / f"cc{tree_flags}-{cc_trinket}",
        sparse=sparse_row(ID=1, ItemLevel=200, InventoryType=12,
                          OverallQualityID=QUALITY_RARE, flags=flags),
        trees=[{"ID": 10, "Flags": tree_flags, "InventoryTypeSlotMask": 0}],
        nodes=[node(1, 10, ChildItemBonusListID=500)],
        x_bonus_tree=[{"ID": 1, "ItemBonusTreeID": 10, "ItemID": 1}])
    assert resolve(proto, resolver).bonus_list_ids == expected


def test_two_matching_context_nodes_reject_the_whole_tree(tmp_path):
    # CanApplyBonusTreeToItem returns false when more than one non-keystone
    # node matches the context and the tree does not set flag 0x4.
    proto, resolver = build(
        tmp_path,
        trees=[{"ID": 10, "Flags": 0, "InventoryTypeSlotMask": 0}],
        nodes=[node(1, 10, ItemContext=0, ChildItemBonusListID=500),
               node(2, 10, ItemContext=0, ChildItemBonusListID=501)],
        x_bonus_tree=[{"ID": 1, "ItemBonusTreeID": 10, "ItemID": 1}])
    selection = resolve(proto, resolver)
    assert selection.bonus_list_ids == []
    assert any(s.action == "tree-rejected" for s in selection.trace)


def test_always_apply_flag_bypasses_the_node_scan(tmp_path):
    proto, resolver = build(
        tmp_path,
        trees=[{"ID": 10, "Flags": 4, "InventoryTypeSlotMask": 0}],
        nodes=[node(1, 10, ChildItemBonusListID=500),
               node(2, 10, ChildItemBonusListID=501)],
        x_bonus_tree=[{"ID": 1, "ItemBonusTreeID": 10, "ItemID": 1}])
    assert resolve(proto, resolver).bonus_list_ids == [500, 501]


# -- children --------------------------------------------------------------

def test_child_tree_descends(tmp_path):
    proto, resolver = build(
        tmp_path,
        trees=[{"ID": 10, "Flags": 4, "InventoryTypeSlotMask": 0},
               {"ID": 11, "Flags": 4, "InventoryTypeSlotMask": 0}],
        nodes=[node(1, 10, ChildItemBonusTreeID=11),
               node(2, 11, ChildItemBonusListID=500)],
        x_bonus_tree=[{"ID": 1, "ItemBonusTreeID": 10, "ItemID": 1}])
    selection = resolve(proto, resolver)
    assert selection.bonus_list_ids == [500]
    assert selection.trees_visited == [10, 11]


def test_child_selector_is_last_write_wins(tmp_path):
    proto, resolver = build(
        tmp_path,
        trees=[{"ID": 10, "Flags": 4, "InventoryTypeSlotMask": 0}],
        nodes=[node(1, 10, ChildItemLevelSelectorID=70),
               node(2, 10, ChildItemLevelSelectorID=71)],
        x_bonus_tree=[{"ID": 1, "ItemBonusTreeID": 10, "ItemID": 1}],
        selectors=[{"ID": 70, "MinItemLevel": 300,
                    "ItemLevelSelectorQualitySetID": 0,
                    "AzeriteUnlockMappingSetID": 0},
                   {"ID": 71, "MinItemLevel": 400,
                    "ItemLevelSelectorQualitySetID": 0,
                    "AzeriteUnlockMappingSetID": 0}],
        level_deltas=[{"ItemLevelDelta": 100, "ID": 900},
                      {"ItemLevelDelta": 200, "ID": 901}])
    selection = resolve(proto, resolver)
    assert selection.item_level_selector_id == 71
    assert selection.bonus_list_ids == [901]


def test_selector_level_delta_and_quality(tmp_path):
    proto, resolver = build(
        tmp_path,
        trees=[{"ID": 10, "Flags": 4, "InventoryTypeSlotMask": 0}],
        nodes=[node(1, 10, ChildItemLevelSelectorID=70)],
        x_bonus_tree=[{"ID": 1, "ItemBonusTreeID": 10, "ItemID": 1}],
        selectors=[{"ID": 70, "MinItemLevel": 320,
                    "ItemLevelSelectorQualitySetID": 5,
                    "AzeriteUnlockMappingSetID": 0}],
        quality_sets=[{"ID": 5, "IlvlRare": 200, "IlvlEpic": 300}],
        qualities=[{"ID": 1, "QualityItemBonusListID": 800, "Quality": 2,
                    "ParentILSQualitySetID": 5},
                   {"ID": 2, "QualityItemBonusListID": 801, "Quality": 4,
                    "ParentILSQualitySetID": 5}],
        level_deltas=[{"ItemLevelDelta": 120, "ID": 900}])
    selection = resolve(proto, resolver)
    # MinItemLevel 320 >= IlvlEpic 300 -> Epic (4); lower_bound picks Quality 4.
    assert selection.bonus_list_ids == [900, 801]


def test_selector_missing_level_delta_is_recorded(tmp_path):
    proto, resolver = build(
        tmp_path,
        trees=[{"ID": 10, "Flags": 4, "InventoryTypeSlotMask": 0}],
        nodes=[node(1, 10, ChildItemLevelSelectorID=70)],
        x_bonus_tree=[{"ID": 1, "ItemBonusTreeID": 10, "ItemID": 1}],
        selectors=[{"ID": 70, "MinItemLevel": 320,
                    "ItemLevelSelectorQualitySetID": 0,
                    "AzeriteUnlockMappingSetID": 0}])
    selection = resolve(proto, resolver)
    assert selection.bonus_list_ids == []
    assert any(s.action == "selector-level-delta-missing" for s in selection.trace)


def test_azerite_unlock_picks_highest_qualifying_row(tmp_path):
    proto, resolver = build(
        tmp_path,
        trees=[{"ID": 10, "Flags": 4, "InventoryTypeSlotMask": 0}],
        nodes=[node(1, 10, ChildItemLevelSelectorID=70)],
        x_bonus_tree=[{"ID": 1, "ItemBonusTreeID": 10, "ItemID": 1}],
        selectors=[{"ID": 70, "MinItemLevel": 320,
                    "ItemLevelSelectorQualitySetID": 0,
                    "AzeriteUnlockMappingSetID": 4}],
        azerite=[{"ID": 1, "MinItemLevel": 200, "HeadBonus": 700,
                  "ShoulderBonus": 0, "ChestBonus": 0, "SetID": 4},
                 {"ID": 2, "MinItemLevel": 310, "HeadBonus": 701,
                  "ShoulderBonus": 0, "ChestBonus": 0, "SetID": 4},
                 {"ID": 3, "MinItemLevel": 900, "HeadBonus": 702,
                  "ShoulderBonus": 0, "ChestBonus": 0, "SetID": 4}],
        level_deltas=[{"ItemLevelDelta": 120, "ID": 900}])
    assert resolve(proto, resolver).bonus_list_ids == [900, 701]


# -- mythic plus gates -----------------------------------------------------

def test_min_and_max_mythic_plus_level(tmp_path):
    proto, resolver = build(
        tmp_path,
        trees=[{"ID": 10, "Flags": 4, "InventoryTypeSlotMask": 0}],
        nodes=[node(1, 10, MinMythicPlusLevel=2, MaxMythicPlusLevel=9,
                    ChildItemBonusListID=500),
               node(2, 10, MinMythicPlusLevel=10, ChildItemBonusListID=501)],
        x_bonus_tree=[{"ID": 1, "ItemBonusTreeID": 10, "ItemID": 1}])
    assert resolve(proto, resolver, mythic_plus_keystone_level=5).bonus_list_ids == [500]
    assert resolve(proto, resolver, mythic_plus_keystone_level=12).bonus_list_ids == [501]
    # No keystone level supplied: Trinity skips the gate entirely.
    assert resolve(proto, resolver).bonus_list_ids == [500, 501]


# -- bonus list groups -----------------------------------------------------

GROUP_ENTRIES = [
    {"ID": 1, "ItemBonusListGroupID": 40, "ItemBonusListID": 601,
     "ItemLevelSelectorID": 0, "SequenceValue": 1, "ItemExtendedCostID": 0,
     "PlayerConditionID": 0, "Flags": 0, "ItemLogicalCostGroupID": 0},
    {"ID": 2, "ItemBonusListGroupID": 40, "ItemBonusListID": 602,
     "ItemLevelSelectorID": 0, "SequenceValue": 2, "ItemExtendedCostID": 0,
     "PlayerConditionID": 0, "Flags": 0, "ItemLogicalCostGroupID": 0},
    {"ID": 3, "ItemBonusListGroupID": 40, "ItemBonusListID": 606,
     "ItemLevelSelectorID": 0, "SequenceValue": 6, "ItemExtendedCostID": 0,
     "PlayerConditionID": 0, "Flags": 0, "ItemLogicalCostGroupID": 0},
]


def _group_tree(tmp_path, tree_id, **kwargs):
    return build(
        tmp_path,
        trees=[{"ID": tree_id, "Flags": 4, "InventoryTypeSlotMask": 0}],
        nodes=[node(1, tree_id, ChildItemBonusListGroupID=40, **kwargs)],
        x_bonus_tree=[{"ID": 1, "ItemBonusTreeID": tree_id, "ItemID": 1}],
        group_entries=GROUP_ENTRIES)


def test_sequence_level_zero_takes_the_first_group_entry(tmp_path):
    """`(resolved > 0 || seq <= 0) && resolved != seq` is false for resolved=0.

    With no sequence level the left operand collapses to false for every
    positive SequenceValue, so the skip never fires and the *first* entry in
    ItemBonusListGroupEntry row order is taken.  That is a real property of the
    direct consumer, not an accident of the fixture.
    """
    proto, resolver = _group_tree(tmp_path, 9999)
    selection = resolve(proto, resolver)
    assert selection.bonus_list_ids == [601]


def test_no_matching_group_entry_is_recorded(tmp_path):
    proto, resolver = build(
        tmp_path / "nomatch",
        trees=[{"ID": 4125, "Flags": 4, "InventoryTypeSlotMask": 0}],
        nodes=[node(1, 4125, ChildItemBonusListGroupID=40)],
        x_bonus_tree=[{"ID": 1, "ItemBonusTreeID": 4125, "ItemID": 1}],
        group_entries=[GROUP_ENTRIES[0], GROUP_ENTRIES[2]])
    # Tree 4125 forces sequence level 2, which this fixture does not contain.
    selection = resolve(proto, resolver)
    assert selection.bonus_list_ids == []
    assert any(s.action == "bonus-list-group-no-match" for s in selection.trace)


def test_group_entry_with_sequence_zero_matches_when_resolved_is_zero(tmp_path):
    proto, resolver = build(
        tmp_path,
        trees=[{"ID": 9999, "Flags": 4, "InventoryTypeSlotMask": 0}],
        nodes=[node(1, 9999, ChildItemBonusListGroupID=40)],
        x_bonus_tree=[{"ID": 1, "ItemBonusTreeID": 9999, "ItemID": 1}],
        group_entries=[{"ID": 1, "ItemBonusListGroupID": 40,
                        "ItemBonusListID": 600, "ItemLevelSelectorID": 0,
                        "SequenceValue": 0, "ItemExtendedCostID": 0,
                        "PlayerConditionID": 0, "Flags": 0,
                        "ItemLogicalCostGroupID": 0}])
    assert resolve(proto, resolver).bonus_list_ids == [600]


# -- hardcoded consumer policy ---------------------------------------------

def test_hardcoded_tree_ids_are_exactly_those_in_the_consumer():
    assert HARDCODED_SEQUENCE_TREES == {4001, 4079, 4125, 4126, 4127, 4128, 4140}


@pytest.mark.parametrize("tree_id, expected_list", [
    (4001, 601),   # sequence level forced to 1
    (4125, 602),   # forced to 2
])
def test_hardcoded_constant_sequence_levels(tmp_path, tree_id, expected_list):
    proto, resolver = _group_tree(tmp_path / str(tree_id), tree_id)
    assert resolve(proto, resolver).bonus_list_ids == [expected_list]


@pytest.mark.parametrize("context, expected_list", [
    (3, 602), (4, 602), (5, 602),   # raid normal/LFR/heroic -> 2
    (6, 606),                       # raid mythic -> 6
    (99, 601),                      # anything else keeps sequence level 0,
                                    # which takes the first entry (see above)
])
def test_tree_4128_context_switch(tmp_path, context, expected_list):
    proto, resolver = _group_tree(tmp_path / f"4128-{context}", 4128)
    got = resolve(proto, resolver, context=context).bonus_list_ids
    assert got == [expected_list]


@pytest.mark.parametrize("context, expected", [(1, 602), (23, None)])
def test_tree_4140_context_switch(tmp_path, context, expected):
    # Dungeon_Normal -> 2 (present); Dungeon_Mythic -> 4 (absent from fixture).
    proto, resolver = _group_tree(tmp_path / f"4140-{context}", 4140)
    got = resolve(proto, resolver, context=context).bonus_list_ids
    assert got == ([expected] if expected else [])


def test_tree_4079_uses_a_keystone_curve(tmp_path):
    mod_set, curve_id = 2909, MYTHIC_PLUS_SEQUENCE_CURVES[2909]
    proto, resolver = build(
        tmp_path,
        trees=[{"ID": 4079, "Flags": 4, "InventoryTypeSlotMask": 0}],
        nodes=[node(1, 4079, ChildItemBonusListGroupID=40,
                    IblGroupPointsModSetID=mod_set)],
        x_bonus_tree=[{"ID": 1, "ItemBonusTreeID": 4079, "ItemID": 1}],
        group_entries=GROUP_ENTRIES,
        curves=[{"ID": curve_id, "Type": 0, "Flags": 0}],
        points=[{"Pos_0": 2.0, "Pos_1": 1.0, "PosPreSquish_0": 0,
                 "PosPreSquish_1": 0, "ID": 1, "CurveID": curve_id,
                 "OrderIndex": 0},
                {"Pos_0": 8.0, "Pos_1": 2.0, "PosPreSquish_0": 0,
                 "PosPreSquish_1": 0, "ID": 2, "CurveID": curve_id,
                 "OrderIndex": 1}])
    # curve(8) == 2 -> sequence level 2 -> bonus list 602
    assert resolve(proto, resolver,
                   mythic_plus_keystone_level=8).bonus_list_ids == [602]
    # Without a keystone level the switch falls through to sequence level 0,
    # which takes the first group entry.
    assert resolve(proto, resolver).bonus_list_ids == [601]


def test_hardcoded_policy_needs_the_original_tree_id(tmp_path):
    # Reaching tree 4125 as a *child* still uses 4125's policy, because
    # ApplyBonusTreeHelper captures originalItemBonusTreeId per invocation.
    proto, resolver = build(
        tmp_path,
        trees=[{"ID": 10, "Flags": 4, "InventoryTypeSlotMask": 0},
               {"ID": 4125, "Flags": 4, "InventoryTypeSlotMask": 0}],
        nodes=[node(1, 10, ChildItemBonusTreeID=4125),
               node(2, 4125, ChildItemBonusListGroupID=40)],
        x_bonus_tree=[{"ID": 1, "ItemBonusTreeID": 10, "ItemID": 1}],
        group_entries=GROUP_ENTRIES)
    assert resolve(proto, resolver).bonus_list_ids == [602]


# -- recursion -------------------------------------------------------------

def test_cyclic_tree_fails_closed(tmp_path):
    proto, resolver = build(
        tmp_path,
        trees=[{"ID": 10, "Flags": 4, "InventoryTypeSlotMask": 0},
               {"ID": 11, "Flags": 4, "InventoryTypeSlotMask": 0}],
        nodes=[node(1, 10, ChildItemBonusTreeID=11),
               node(2, 11, ChildItemBonusTreeID=10)],
        x_bonus_tree=[{"ID": 1, "ItemBonusTreeID": 10, "ItemID": 1}])
    with pytest.raises(SourceError, match="cyclic"):
        resolve(proto, resolver)


def test_reachable_trees_terminates_on_a_cycle(tmp_path):
    proto, resolver = build(
        tmp_path,
        trees=[{"ID": 10, "Flags": 4, "InventoryTypeSlotMask": 0},
               {"ID": 11, "Flags": 4, "InventoryTypeSlotMask": 0}],
        nodes=[node(1, 10, ChildItemBonusTreeID=11),
               node(2, 11, ChildItemBonusTreeID=10)],
        x_bonus_tree=[{"ID": 1, "ItemBonusTreeID": 10, "ItemID": 1}])
    assert resolver.reachable_trees(1) == [10, 11]


# -- real snapshot ---------------------------------------------------------

@pytest.mark.snapshot
def test_real_tier_piece_difficulty_contexts(resolver):
    """Raid difficulty contexts resolve to four distinct, ordered item levels."""
    from gearing.resolver import Variant
    levels = {}
    for context in (4, 3, 5, 6):
        got = resolver.resolve(271519, Variant(label="x", context=context))
        levels[context] = got.effective_item_level
    assert levels == {4: 279, 3: 292, 5: 305, 6: 318}


@pytest.mark.snapshot
def test_real_traversal_is_deterministic(resolver):
    from gearing.resolver import Variant
    first = resolver.resolve(271519, Variant(label="x", context=6))
    second = resolver.resolve(271519, Variant(label="x", context=6))
    assert first.applied_bonus_lists == second.applied_bonus_lists
    assert [s.to_dict() for s in first.selection.trace] == \
           [s.to_dict() for s in second.selection.trace]
