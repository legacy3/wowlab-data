"""Population counts used to estimate architectural leverage.

These are not vanity counts: each one answers "how much of the gear surface
does a given mechanism actually cover in the current snapshot", which is what
decides whether a future production importer needs to model it generically or
can treat it as an exception.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from .bonus import HANDLED_BONUS_TYPES
from .effects import TRIGGER_ON_EQUIP, TRIGGER_ON_PROC, TRIGGER_ON_USE
from .enchants import (
    ENCHANT_BONUS_LIST_CURVE,
    ENCHANT_BONUS_LIST_ID,
    ENCHANT_COMBAT_SPELL,
    ENCHANT_EQUIP_SPELL,
    ENCHANT_PRISMATIC_SOCKET,
    ENCHANT_STAT,
    ENCHANT_USE_SPELL,
)
from .enums import (
    MAX_ITEM_ENCHANTMENT_EFFECTS,
    MAX_ITEM_PROTO_SOCKETS,
    MAX_ITEM_PROTO_STATS,
)
from .resolver import GearResolver


def census(resolver: GearResolver) -> dict[str, Any]:
    t = resolver.tables
    sparse = t("ItemSparse")
    equippable = sum(1 for row in sparse if int(row["InventoryType"]) != 0)
    stat_bearing = sum(
        1 for row in sparse
        if any(int(row[f"StatModifier_bonusStat_{i}"]) != -1
               for i in range(MAX_ITEM_PROTO_STATS)))
    socketed = sum(1 for row in sparse
                   if any(int(row[f"SocketType_{i}"])
                          for i in range(MAX_ITEM_PROTO_SOCKETS)))

    effects = t("ItemEffect")
    trigger_counts: dict[int, int] = defaultdict(int)
    for row in effects:
        trigger_counts[int(row["TriggerType"])] += 1

    enchant_effect_types: dict[int, int] = defaultdict(int)
    gear_spells: set[int] = set()
    for row in t("SpellItemEnchantment"):
        for i in range(MAX_ITEM_ENCHANTMENT_EFFECTS):
            value = int(row[f"Effect_{i}"])
            if not value:
                continue
            enchant_effect_types[value] += 1
            if value in (ENCHANT_COMBAT_SPELL, ENCHANT_EQUIP_SPELL,
                         ENCHANT_USE_SPELL) and int(row[f"EffectArg_{i}"]):
                gear_spells.add(int(row[f"EffectArg_{i}"]))
    for row in effects:
        if int(row["SpellID"]):
            gear_spells.add(int(row["SpellID"]))
    for row in t("ItemSetSpell"):
        if int(row["SpellID"]):
            gear_spells.add(int(row["SpellID"]))

    bonus_types: dict[int, int] = defaultdict(int)
    for row in t("ItemBonus"):
        bonus_types[int(row["Type"])] += 1
    unhandled_types = sorted(set(bonus_types) - HANDLED_BONUS_TYPES)
    unhandled_rows = sum(bonus_types[x] for x in unhandled_types)

    curve_types: dict[int, int] = defaultdict(int)
    for row in t("Curve"):
        curve_types[int(row["Type"])] += 1
    unsupported_curve_types = sorted(x for x in curve_types if x not in (0, 1, 2, 3))

    return {
        "populations": {
            "Item rows": len(t("Item")),
            "ItemSparse rows": len(sparse),
            "equippable items (InventoryType != 0)": equippable,
            "stat-bearing items": stat_bearing,
            "items with a template socket": socketed,
            "ItemEffect rows": len(effects),
            "ItemXItemEffect edges": len(t("ItemXItemEffect")),
            "on-use roots (TriggerType 0)": trigger_counts[TRIGGER_ON_USE],
            "equip roots (TriggerType 1)": trigger_counts[TRIGGER_ON_EQUIP],
            "item-proc roots (TriggerType 2)": trigger_counts[TRIGGER_ON_PROC],
            "SpellItemEnchantment rows": len(t("SpellItemEnchantment")),
            "enchant stat effects": enchant_effect_types[ENCHANT_STAT],
            "enchant equip-spell effects": enchant_effect_types[ENCHANT_EQUIP_SPELL],
            "enchant combat/proc effects": enchant_effect_types[ENCHANT_COMBAT_SPELL],
            "enchant use-spell effects": enchant_effect_types[ENCHANT_USE_SPELL],
            "enchant socket-mutation effects": enchant_effect_types[ENCHANT_PRISMATIC_SOCKET],
            "enchant gem item-level effects": (
                enchant_effect_types[ENCHANT_BONUS_LIST_ID]
                + enchant_effect_types[ENCHANT_BONUS_LIST_CURVE]),
            "GemProperties rows": len(t("GemProperties")),
            "ItemSet rows": len(t("ItemSet")),
            "ItemSetSpell rows": len(t("ItemSetSpell")),
            "distinct gear-reachable spell ids": len(gear_spells),
            "ItemBonus rows": len(t("ItemBonus")),
            "distinct bonus lists": len(resolver.trees.item_bonus_by_list),
            "distinct ItemBonus types in use": len(bonus_types),
            "ItemBonusTree rows": len(t("ItemBonusTree")),
            "ItemBonusTreeNode rows": len(t("ItemBonusTreeNode")),
            "ItemXBonusTree edges": len(t("ItemXBonusTree")),
            "ItemBonusListGroupEntry rows": len(t("ItemBonusListGroupEntry")),
            "ItemLevelSelector rows": len(t("ItemLevelSelector")),
            "ItemBonusListLevelDelta rows": len(t("ItemBonusListLevelDelta")),
            "ItemScalingConfig rows": len(t("ItemScalingConfig")),
            "ItemOffsetCurve rows": len(t("ItemOffsetCurve")),
            "ItemSquishEra rows": len(t("ItemSquishEra")),
            "Curve rows": len(t("Curve")),
            "CurvePoint rows": len(t("CurvePoint")),
            "curves with at least one point": len(resolver.curves.curve_ids_with_points()),
            "GlobalCurve rows": len(t("GlobalCurve")),
        },
        "unsupported": {
            "ItemBonus types with no BonusData::AddBonus branch": unhandled_types,
            "ItemBonus rows using those types": unhandled_rows,
            "Curve.Type values with no DetermineCurveType case": unsupported_curve_types,
            "curves whose X is not monotonic in OrderIndex order":
                resolver.curves.non_monotonic_curves(),
        },
        "bonus_type_row_counts": dict(sorted(bonus_types.items())),
        "item_effect_trigger_counts": dict(sorted(trigger_counts.items())),
        "enchant_effect_type_counts": dict(sorted(enchant_effect_types.items())),
        "curve_type_counts": dict(sorted(curve_types.items())),
    }
