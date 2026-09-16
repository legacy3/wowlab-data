"""Bonus-tree traversal: which bonus lists an item gets in a given context.

Mirrors: ``src/server/game/Entities/Item/ItemBonusMgr.cpp`` --
``ItemBonusMgr::Load``, ``CanApplyBonusTreeToItem``, ``GetBonusTreeIdOverride``,
``ApplyBonusTreeHelper``, ``GetAzeriteUnlockBonusList`` and
``GetBonusListsForItem``.

Two ordering hazards in the direct consumer are reproduced deterministically
here and flagged in the research document:

* ``_itemToBonusTree`` is an ``unordered_multimap``, so the order in which an
  item's trees are visited is implementation defined in Trinity.  This package
  visits them in ``ItemXBonusTree`` row order (ascending ID).
* ``_itemBonusTrees`` maps a tree to a ``std::set<ItemBonusTreeNodeEntry const*>``
  ordered by *pointer*, i.e. by position in the DB2 store, i.e. by ascending ID.
  This package visits nodes in ``ItemBonusTreeNode`` row order, which matches.

Because ``*itemLevelSelectorId`` is last-write-wins, both orderings can change
the result when more than one node in a single resolution sets a selector.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from . import SourceError
from .curves import Curves
from .enums import (
    BONUS_TREE_FLAG_ALWAYS_APPLY,
    BONUS_TREE_FLAG_REQUIRE_CASTER_WEAPON,
    BONUS_TREE_FLAG_REQUIRE_CC_TRINKET,
    BONUS_TREE_FLAG_REQUIRE_NON_CASTER_WEAPON,
    BONUS_TREE_FLAG_REQUIRE_NON_CC_TRINKET,
    BONUS_TREE_NODE_FLAG_INVERT_CONTEXT,
    CONTEXT_DUNGEON_MYTHIC,
    CONTEXT_DUNGEON_NORMAL,
    CONTEXT_RAID_HEROIC,
    CONTEXT_RAID_MYTHIC,
    CONTEXT_RAID_NORMAL,
    CONTEXT_RAID_RAID_FINDER,
    INVTYPE_CHEST,
    INVTYPE_HEAD,
    INVTYPE_ROBE,
    INVTYPE_SHOULDERS,
    ITEM_CONTEXT_FORCE_TO_NONE,
    ITEM_CONTEXT_NONE,
    QUALITY_EPIC,
    QUALITY_RARE,
    QUALITY_UNCOMMON,
    context_name,
)
from .items import ItemTemplate
from .tables import Tables

#: Max tree nesting.  Trinity recurses without a guard; this package fails
#: closed on a cyclic ``ChildItemBonusTreeID`` chain instead of hanging.
MAX_TREE_DEPTH = 32

#: Tree ids for which ``ApplyBonusTreeHelper`` hardcodes a sequence level.
#: This is *consumer policy*, not a source relationship: nothing in
#: ItemBonusTree/ItemBonusTreeNode/ItemBonusListGroup carries these numbers, so
#: they cannot be generalised into a data-driven rule without new evidence.
HARDCODED_SEQUENCE_TREES = frozenset({4001, 4079, 4125, 4126, 4127, 4128, 4140})

#: ``IblGroupPointsModSetID`` -> curve id, for tree 4079 only.  Same caveat.
MYTHIC_PLUS_SEQUENCE_CURVES = {
    2909: 62951,   # MythicPlus_End_of_Run levels 2-8
    2910: 62952,   # MythicPlus_End_of_Run levels 9-16
    2911: 62954,   # MythicPlus_End_of_Run levels 17-20
    3007: 64388,   # MythicPlus_Jackpot (weekly reward) levels 2-7
    3008: 64389,   # MythicPlus_Jackpot (weekly reward) levels 8-15
    3009: 64395,   # MythicPlus_Jackpot (weekly reward) levels 16-20
}


@dataclass(frozen=True)
class BonusGenerationParams:
    """Mirrors: ``ItemBonusMgr::ItemBonusGenerationParams``."""

    context: int = ITEM_CONTEXT_NONE
    mythic_plus_keystone_level: int | None = None
    pvp_tier: int | None = None


@dataclass(frozen=True)
class TreeStep:
    """One decision taken while walking an item's bonus trees."""

    tree_id: int
    node_id: int
    action: str
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"tree_id": self.tree_id, "node_id": self.node_id,
                "action": self.action, **self.detail}


@dataclass
class BonusListSelection:
    """Result of ``ItemBonusMgr::GetBonusListsForItem``."""

    bonus_list_ids: list[int]
    item_level_selector_id: int
    trace: list[TreeStep]
    trees_visited: list[int]

    def to_dict(self) -> dict[str, Any]:
        return {
            "bonus_list_ids": list(self.bonus_list_ids),
            "item_level_selector_id": self.item_level_selector_id,
            "trees_visited": list(self.trees_visited),
            "trace": [s.to_dict() for s in self.trace],
        }


class BonusTreeResolver:
    """Loads the bonus-tree graph once and resolves it per (item, context)."""

    def __init__(self, tables: Tables, curves: Curves) -> None:
        self.tables = tables
        self.curves = curves

        # -- ItemBonusMgr::Load -----------------------------------------
        self.item_bonus_by_list = tables("ItemBonus").group("ParentItemBonusListID")
        self.bonus_tree = tables("ItemBonusTree").by("ID")
        self.nodes_by_tree = tables("ItemBonusTreeNode").group("ParentItemBonusTreeID")
        self.item_to_trees: dict[int, list[int]] = defaultdict(list)
        for row in tables("ItemXBonusTree"):
            self.item_to_trees[row["ItemID"]].append(row["ItemBonusTreeID"])
        self.group_entries = tables("ItemBonusListGroupEntry").group("ItemBonusListGroupID")
        self.level_selector = tables("ItemLevelSelector").by("ID")
        self.level_selector_quality_set = tables("ItemLevelSelectorQualitySet").by("ID")
        self.level_selector_qualities: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for row in tables("ItemLevelSelectorQuality"):
            self.level_selector_qualities[row["ParentILSQualitySetID"]].append(row)
        for rows in self.level_selector_qualities.values():
            # Mirrors: std::set ordered by ItemLevelSelectorQualityEntryComparator.
            rows.sort(key=lambda r: r["Quality"])
        self.level_delta_to_bonus_list: dict[int, int] = {}
        for row in tables("ItemBonusListLevelDelta"):
            # Mirrors: plain map assignment, so a later row wins.
            self.level_delta_to_bonus_list[int(row["ItemLevelDelta"])] = int(row["ID"])
        self.contexts_by_creation_group: dict[int, list[int]] = defaultdict(list)
        for row in tables("ItemCreationContext"):
            self.contexts_by_creation_group[row["ItemCreationContextGroupID"]].append(
                int(row["ItemContext"]))
        self.azerite_unlock_mapping = tables("AzeriteUnlockMapping").group("SetID")

    # -- eligibility ----------------------------------------------------
    def can_apply_tree(self, proto: ItemTemplate, tree_id: int,
                       params: BonusGenerationParams) -> bool:
        """Mirrors: ``ItemBonusMgr::CanApplyBonusTreeToItem``.

        Note the odd tail: the node scan can only ever return ``False`` (when
        two or more nodes match the context); it never returns ``True`` early.
        Reproduced verbatim.
        """
        tree = self.bonus_tree.get(tree_id)
        if tree is not None:
            slot_mask = int(tree["InventoryTypeSlotMask"])
            if slot_mask and not ((1 << proto.inventory_type) & slot_mask):
                return False
            flags = int(tree["Flags"])
            if flags & BONUS_TREE_FLAG_REQUIRE_CASTER_WEAPON and not proto.is_caster_weapon:
                return False
            if flags & BONUS_TREE_FLAG_REQUIRE_NON_CASTER_WEAPON and proto.is_caster_weapon:
                return False
            if flags & BONUS_TREE_FLAG_REQUIRE_CC_TRINKET and not proto.is_cc_trinket:
                return False
            if flags & BONUS_TREE_FLAG_REQUIRE_NON_CC_TRINKET and proto.is_cc_trinket:
                return False
            if flags & BONUS_TREE_FLAG_ALWAYS_APPLY:
                return True

        nodes = self.nodes_by_tree.get(tree_id)
        if nodes:
            any_matched = False
            for node in nodes:
                if int(node["MinMythicPlusLevel"]) > 0:
                    continue
                node_context = int(node["ItemContext"])
                if node_context == ITEM_CONTEXT_NONE or node_context == params.context:
                    if any_matched:
                        return False
                    any_matched = True
        return True

    # -- sequence level -------------------------------------------------
    def resolve_sequence_level(self, original_tree_id: int, node: dict[str, Any],
                               params: BonusGenerationParams,
                               sequence_level: int) -> int:
        """Mirrors: the hardcoded tree-id switch inside ``ApplyBonusTreeHelper``.

        Consumer policy, not source data.  See :data:`HARDCODED_SEQUENCE_TREES`.
        """
        if original_tree_id == 4001:
            return 1
        if original_tree_id == 4079:
            if params.mythic_plus_keystone_level is not None:
                curve_id = MYTHIC_PLUS_SEQUENCE_CURVES.get(
                    int(node["IblGroupPointsModSetID"]))
                if curve_id is not None:
                    return int(self.curves.value_at(
                        curve_id, params.mythic_plus_keystone_level,
                        consumer="ItemBonusMgr::ApplyBonusTreeHelper/"
                                 "MythicPlusSequenceLevel"))
            return sequence_level
        if original_tree_id == 4125:
            return 2
        if original_tree_id == 4126:
            return 3
        if original_tree_id == 4127:
            return 4
        if original_tree_id == 4128:
            if params.context in (CONTEXT_RAID_NORMAL, CONTEXT_RAID_RAID_FINDER,
                                  CONTEXT_RAID_HEROIC):
                return 2
            if params.context == CONTEXT_RAID_MYTHIC:
                return 6
            return sequence_level
        if original_tree_id == 4140:
            if params.context == CONTEXT_DUNGEON_NORMAL:
                return 2
            if params.context == CONTEXT_DUNGEON_MYTHIC:
                return 4
            return sequence_level
        return sequence_level

    # -- traversal ------------------------------------------------------
    def _apply_tree(self, proto: ItemTemplate, tree_id: int,
                    params: BonusGenerationParams, sequence_level: int,
                    state: dict[str, Any], depth: int = 0) -> None:
        """Mirrors: ``ItemBonusMgr::ApplyBonusTreeHelper``.

        ``GetBonusTreeIdOverride`` is a no-op in the direct consumer: its
        ``passedTimeEvents`` is an empty local marked ``TODO: configure
        globally``, so ChallengeModeItemBonusOverride never fires.  That is
        reproduced, and the table is reported as unconsumed by the census.
        """
        if depth > MAX_TREE_DEPTH:
            raise SourceError(
                f"bonus tree nesting exceeded depth {MAX_TREE_DEPTH} at tree "
                f"{tree_id}; ChildItemBonusTreeID rows are cyclic")
        original_tree_id = tree_id
        state["trees"].append(tree_id)

        if not self.can_apply_tree(proto, tree_id, params):
            state["trace"].append(TreeStep(tree_id, 0, "tree-rejected"))
            return

        nodes = self.nodes_by_tree.get(tree_id)
        if not nodes:
            state["trace"].append(TreeStep(tree_id, 0, "tree-has-no-nodes"))
            return

        for node in nodes:
            node_id = int(node["ID"])
            node_context = int(node["ItemContext"])
            flags = int(node["Flags"])
            invert = bool(flags & BONUS_TREE_NODE_FLAG_INVERT_CONTEXT)
            required_context = (node_context
                                if node_context != ITEM_CONTEXT_FORCE_TO_NONE
                                else ITEM_CONTEXT_NONE)

            if node_context != ITEM_CONTEXT_NONE and params.context != required_context:
                if not invert:
                    state["trace"].append(TreeStep(
                        tree_id, node_id, "node-skipped-context",
                        {"node_context": node_context,
                         "node_context_name": context_name(node_context)}))
                    continue
            elif invert and node_context != ITEM_CONTEXT_NONE:
                state["trace"].append(TreeStep(
                    tree_id, node_id, "node-skipped-inverted-context",
                    {"node_context": node_context,
                     "node_context_name": context_name(node_context)}))
                continue

            group_id = int(node["ItemCreationContextGroupID"])
            if group_id:
                has_context = params.context in self.contexts_by_creation_group.get(
                    group_id, ())
                if invert == has_context:
                    state["trace"].append(TreeStep(
                        tree_id, node_id, "node-skipped-context-group",
                        {"item_creation_context_group_id": group_id,
                         "context_in_group": has_context,
                         "node_inverts": invert}))
                    continue

            if params.mythic_plus_keystone_level is not None:
                min_mp = int(node["MinMythicPlusLevel"])
                max_mp = int(node["MaxMythicPlusLevel"])
                if min_mp and params.mythic_plus_keystone_level < min_mp:
                    state["trace"].append(TreeStep(
                        tree_id, node_id, "node-skipped-min-mythic-plus",
                        {"min_mythic_plus_level": min_mp}))
                    continue
                if max_mp and params.mythic_plus_keystone_level > max_mp:
                    state["trace"].append(TreeStep(
                        tree_id, node_id, "node-skipped-max-mythic-plus",
                        {"max_mythic_plus_level": max_mp}))
                    continue

            child_tree = int(node["ChildItemBonusTreeID"])
            child_list = int(node["ChildItemBonusListID"])
            child_selector = int(node["ChildItemLevelSelectorID"])
            child_group = int(node["ChildItemBonusListGroupID"])

            if child_tree:
                state["trace"].append(TreeStep(tree_id, node_id, "descend",
                                               {"child_tree_id": child_tree}))
                self._apply_tree(proto, child_tree, params, sequence_level,
                                 state, depth + 1)
            elif child_list:
                state["bonus_list_ids"].append(child_list)
                state["trace"].append(TreeStep(tree_id, node_id, "bonus-list",
                                               {"bonus_list_id": child_list}))
            elif child_selector:
                state["item_level_selector_id"] = child_selector
                state["trace"].append(TreeStep(
                    tree_id, node_id, "item-level-selector",
                    {"item_level_selector_id": child_selector}))
            elif child_group:
                self._apply_bonus_list_group(
                    original_tree_id, tree_id, node, node_id, child_group,
                    params, sequence_level, state)

    def _apply_bonus_list_group(self, original_tree_id: int, tree_id: int,
                                node: dict[str, Any], node_id: int,
                                child_group: int, params: BonusGenerationParams,
                                sequence_level: int, state: dict[str, Any]) -> None:
        resolved = self.resolve_sequence_level(original_tree_id, node, params,
                                               sequence_level)
        picked = None
        for entry in self.group_entries.get(child_group, ()):
            seq = int(entry["SequenceValue"])
            # Mirrors: `(resolved > 0 || seq <= 0) && resolved != seq` -> skip.
            if (resolved > 0 or seq <= 0) and resolved != seq:
                continue
            picked = entry
            break
        if picked is None:
            state["trace"].append(TreeStep(
                tree_id, node_id, "bonus-list-group-no-match",
                {"item_bonus_list_group_id": child_group,
                 "resolved_sequence_level": resolved}))
            return
        selector = int(picked["ItemLevelSelectorID"])
        bonus_list = int(picked["ItemBonusListID"])
        state["item_level_selector_id"] = selector
        state["bonus_list_ids"].append(bonus_list)
        state["trace"].append(TreeStep(
            tree_id, node_id, "bonus-list-group",
            {"item_bonus_list_group_id": child_group,
             "resolved_sequence_level": resolved,
             "group_entry_id": int(picked["ID"]),
             "bonus_list_id": bonus_list,
             "item_level_selector_id": selector}))

    # -- selector tail --------------------------------------------------
    def azerite_unlock_bonus_list(self, set_id: int, min_item_level: int,
                                  inventory_type: int) -> int:
        """Mirrors: ``ItemBonusMgr::GetAzeriteUnlockBonusList``."""
        if not set_id:
            return 0
        selected = None
        for row in self.azerite_unlock_mapping.get(set_id, ()):
            if min_item_level < int(row["MinItemLevel"]):
                continue
            if selected is not None and int(selected["MinItemLevel"]) > int(row["MinItemLevel"]):
                continue
            selected = row
        if selected is None:
            return 0
        if inventory_type == INVTYPE_HEAD:
            return int(selected["HeadBonus"])
        if inventory_type == INVTYPE_SHOULDERS:
            return int(selected["ShoulderBonus"])
        if inventory_type in (INVTYPE_CHEST, INVTYPE_ROBE):
            return int(selected["ChestBonus"])
        return 0

    def bonus_lists_for_item(self, proto: ItemTemplate,
                             params: BonusGenerationParams) -> BonusListSelection:
        """Mirrors: ``ItemBonusMgr::GetBonusListsForItem``."""
        state: dict[str, Any] = {
            "bonus_list_ids": [], "item_level_selector_id": 0,
            "trace": [], "trees": [],
        }
        for tree_id in self.item_to_trees.get(proto.item_id, ()):
            self._apply_tree(proto, tree_id, params, 0, state)

        selector_id = state["item_level_selector_id"]
        selector = self.level_selector.get(selector_id)
        if selector is not None:
            self._apply_selector(proto, selector_id, selector, state)

        return BonusListSelection(
            bonus_list_ids=state["bonus_list_ids"],
            item_level_selector_id=selector_id,
            trace=state["trace"],
            trees_visited=state["trees"],
        )

    def _apply_selector(self, proto: ItemTemplate, selector_id: int,
                        selector: dict[str, Any], state: dict[str, Any]) -> None:
        min_item_level = int(selector["MinItemLevel"])
        delta = min_item_level - proto.base_item_level
        bonus = self.level_delta_to_bonus_list.get(delta, 0)
        if bonus:
            state["bonus_list_ids"].append(bonus)
            state["trace"].append(TreeStep(
                0, 0, "selector-level-delta",
                {"item_level_selector_id": selector_id,
                 "selector_min_item_level": min_item_level,
                 "base_item_level": proto.base_item_level,
                 "item_level_delta": delta,
                 "bonus_list_id": bonus}))
        else:
            # Trinity simply adds nothing; recorded so the gap is visible.
            state["trace"].append(TreeStep(
                0, 0, "selector-level-delta-missing",
                {"item_level_selector_id": selector_id,
                 "selector_min_item_level": min_item_level,
                 "base_item_level": proto.base_item_level,
                 "item_level_delta": delta}))

        quality_set_id = int(selector["ItemLevelSelectorQualitySetID"])
        quality_set = self.level_selector_quality_set.get(quality_set_id)
        qualities = self.level_selector_qualities.get(quality_set_id)
        if quality_set is not None and qualities:
            quality = QUALITY_UNCOMMON
            if min_item_level >= int(quality_set["IlvlEpic"]):
                quality = QUALITY_EPIC
            elif min_item_level >= int(quality_set["IlvlRare"]):
                quality = QUALITY_RARE
            # Mirrors: std::lower_bound over the Quality-ordered set.
            picked = next((q for q in qualities if int(q["Quality"]) >= quality), None)
            if picked is not None:
                state["bonus_list_ids"].append(int(picked["QualityItemBonusListID"]))
                state["trace"].append(TreeStep(
                    0, 0, "selector-quality",
                    {"item_level_selector_quality_set_id": quality_set_id,
                     "computed_quality": quality,
                     "matched_quality": int(picked["Quality"]),
                     "bonus_list_id": int(picked["QualityItemBonusListID"])}))

        azerite = self.azerite_unlock_bonus_list(
            int(selector["AzeriteUnlockMappingSetID"]), min_item_level,
            proto.inventory_type)
        if azerite:
            state["bonus_list_ids"].append(azerite)
            state["trace"].append(TreeStep(0, 0, "azerite-unlock",
                                           {"bonus_list_id": azerite}))

    # -- discovery helpers ----------------------------------------------
    def reachable_trees(self, item_id: int) -> list[int]:
        """Every tree reachable from an item, ignoring context gating."""
        seen: list[int] = []
        visited: set[int] = set()

        def walk(tree_id: int, depth: int) -> None:
            if tree_id in visited or depth > MAX_TREE_DEPTH:
                return
            visited.add(tree_id)
            seen.append(tree_id)
            for node in self.nodes_by_tree.get(tree_id, ()):
                child = int(node["ChildItemBonusTreeID"])
                if child:
                    walk(child, depth + 1)

        for tree_id in self.item_to_trees.get(item_id, ()):
            walk(tree_id, 0)
        return seen

    def reachable_nodes(self, item_id: int) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for tree_id in self.reachable_trees(item_id):
            out.extend(self.nodes_by_tree.get(tree_id, ()))
        return out
