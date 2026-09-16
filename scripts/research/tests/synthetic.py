"""Builders for tiny in-memory snapshots.

A synthetic snapshot lets a test pin one behaviour without a real item's other
bonus rows interfering, and lets ordering/rounding be made discriminating on
purpose.  Only the tables a given engine actually opens are written.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Iterable, Sequence

from gearing.tables import Tables


def write_csv(path: Path, header: Sequence[str], rows: Iterable[Sequence[Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(list(header))
        writer.writerows([list(r) for r in rows])


def write_gametable(path: Path, header: Sequence[str],
                    rows: Iterable[Sequence[Any]]) -> None:
    lines = ["\t".join(str(c) for c in header)]
    lines.extend("\t".join(str(c) for c in row) for row in rows)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


ITEM_COLUMNS = [
    "ID", "ClassID", "SubclassID", "Material", "InventoryType", "SheatheType",
    "Sound_override_subclassID", "IconFileDataID", "ItemGroupSoundsID",
    "ContentTuningID", "ModifiedCraftingReagentItemID",
    "Field_12_0_0_63534_010", "CraftingQualityID", "ItemSquishEraID",
    "RecraftReagentCountPercentage", "OrderSource",
]

SPARSE_SCALARS = [
    "ID", "ExpansionID", "DmgVariance", "LimitCategory", "ContentTuningID",
    "PlayerLevelToItemLevelCurveID", "ItemLevelOffsetCurveID",
    "ItemLevelOffsetItemLevel", "ItemSquishEraID", "Gem_properties",
    "Socket_match_enchantment_ID", "ItemSet", "ItemDelay", "ItemLevel",
    "Bonding", "DamageType", "RequiredLevel", "InventoryType",
    "OverallQualityID", "Display_lang",
]
SPARSE_ARRAYS = {
    "StatPercentageOfSocket_": 10,
    "StatPercentEditor_": 10,
    "StatModifier_bonusStat_": 10,
    "Flags_": 5,
    "SocketType_": 3,
}


def sparse_columns() -> list[str]:
    columns = list(SPARSE_SCALARS)
    for prefix, count in SPARSE_ARRAYS.items():
        columns.extend(f"{prefix}{i}" for i in range(count))
    return columns


def sparse_row(**kwargs: Any) -> dict[str, Any]:
    """An ItemSparse row with every column present and sane defaults."""
    row: dict[str, Any] = {c: 0 for c in sparse_columns()}
    row["Display_lang"] = kwargs.pop("name", "Synthetic Item")
    for i in range(10):
        row[f"StatModifier_bonusStat_{i}"] = -1
    stats = kwargs.pop("stats", ())          # [(stat_type, allocation), ...]
    for index, (stat_type, allocation) in enumerate(stats):
        row[f"StatModifier_bonusStat_{index}"] = stat_type
        row[f"StatPercentEditor_{index}"] = allocation
    sockets = kwargs.pop("sockets", ())
    for index, colour in enumerate(sockets):
        row[f"SocketType_{index}"] = colour
    flags = kwargs.pop("flags", ())
    for index, value in enumerate(flags):
        row[f"Flags_{index}"] = value
    row.update(kwargs)
    return row


def item_row(**kwargs: Any) -> dict[str, Any]:
    row: dict[str, Any] = {c: 0 for c in ITEM_COLUMNS}
    row.update(kwargs)
    return row


class SnapshotBuilder:
    """Accumulates rows then writes a directory :class:`Tables` can open."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._csv: dict[str, tuple[list[str], list[dict[str, Any]]]] = {}
        self._gt: dict[str, tuple[list[str], list[list[Any]]]] = {}

    def table(self, name: str, columns: Sequence[str],
              rows: Iterable[dict[str, Any]] = ()) -> "SnapshotBuilder":
        existing = self._csv.setdefault(name, (list(columns), []))
        existing[1].extend(rows)
        return self

    def gametable(self, name: str, columns: Sequence[str],
                  rows: Iterable[Sequence[Any]]) -> "SnapshotBuilder":
        self._gt[name] = (list(columns), [list(r) for r in rows])
        return self

    def build(self) -> Tables:
        for name, (columns, rows) in self._csv.items():
            write_csv(self.root / f"{name}.csv", columns,
                      [[row.get(c, 0) for c in columns] for row in rows])
        for name, (columns, rows) in self._gt.items():
            write_gametable(self.root / f"{name}.txt", columns, rows)
        return Tables(self.root)


def minimal_items(builder: SnapshotBuilder, items: Sequence[dict[str, Any]],
                  sparses: Sequence[dict[str, Any]],
                  effects: Sequence[dict[str, Any]] = (),
                  x_effects: Sequence[dict[str, Any]] = ()) -> SnapshotBuilder:
    builder.table("Item", ITEM_COLUMNS, items)
    builder.table("ItemSparse", sparse_columns(), sparses)
    builder.table("ItemEffect",
                  ["ID", "LegacySlotIndex", "TriggerType", "Charges",
                   "CoolDownMSec", "CategoryCoolDownMSec", "SpellCategoryID",
                   "SpellID", "ChrSpecializationID", "PlayerConditionID"],
                  effects)
    builder.table("ItemXItemEffect", ["ID", "ItemEffectID", "ItemID"], x_effects)
    return builder


def minimal_bonus_graph(
    builder: SnapshotBuilder,
    *,
    item_bonus: Sequence[dict[str, Any]] = (),
    trees: Sequence[dict[str, Any]] = (),
    nodes: Sequence[dict[str, Any]] = (),
    x_bonus_tree: Sequence[dict[str, Any]] = (),
    group_entries: Sequence[dict[str, Any]] = (),
    selectors: Sequence[dict[str, Any]] = (),
    quality_sets: Sequence[dict[str, Any]] = (),
    qualities: Sequence[dict[str, Any]] = (),
    level_deltas: Sequence[dict[str, Any]] = (),
    creation_contexts: Sequence[dict[str, Any]] = (),
    azerite: Sequence[dict[str, Any]] = (),
    bonus_list_groups: Sequence[dict[str, Any]] = (),
) -> SnapshotBuilder:
    builder.table("ItemBonus",
                  ["ID", "Value_0", "Value_1", "Value_2", "Value_3",
                   "ParentItemBonusListID", "Type", "OrderIndex"], item_bonus)
    builder.table("ItemBonusTree", ["ID", "Flags", "InventoryTypeSlotMask"], trees)
    builder.table("ItemBonusTreeNode",
                  ["ID", "ItemContext", "ChildItemBonusTreeID",
                   "ChildItemBonusListID", "ChildItemLevelSelectorID",
                   "ChildItemBonusListGroupID", "IblGroupPointsModSetID",
                   "MinMythicPlusLevel", "MaxMythicPlusLevel",
                   "ItemCreationContextGroupID", "Flags",
                   "ParentItemBonusTreeID"], nodes)
    builder.table("ItemXBonusTree", ["ID", "ItemBonusTreeID", "ItemID"],
                  x_bonus_tree)
    builder.table("ItemBonusListGroupEntry",
                  ["ID", "ItemBonusListGroupID", "ItemBonusListID",
                   "ItemLevelSelectorID", "SequenceValue", "ItemExtendedCostID",
                   "PlayerConditionID", "Flags", "ItemLogicalCostGroupID"],
                  group_entries)
    builder.table("ItemBonusListGroup",
                  ["ID", "SequenceSpellID", "PlayerConditionID",
                   "ItemExtendedCostID", "ItemLogicalCostGroupID",
                   "ItemGroupIlvlScalingID"], bonus_list_groups)
    builder.table("ItemLevelSelector",
                  ["ID", "MinItemLevel", "ItemLevelSelectorQualitySetID",
                   "AzeriteUnlockMappingSetID"], selectors)
    builder.table("ItemLevelSelectorQualitySet", ["ID", "IlvlRare", "IlvlEpic"],
                  quality_sets)
    builder.table("ItemLevelSelectorQuality",
                  ["ID", "QualityItemBonusListID", "Quality",
                   "ParentILSQualitySetID"], qualities)
    builder.table("ItemBonusListLevelDelta", ["ItemLevelDelta", "ID"], level_deltas)
    builder.table("ItemCreationContext",
                  ["ID", "ItemContext", "ItemCreationContextGroupID"],
                  creation_contexts)
    builder.table("AzeriteUnlockMapping",
                  ["ID", "MinItemLevel", "HeadBonus", "ShoulderBonus",
                   "ChestBonus", "SetID"], azerite)
    return builder


def minimal_scaling(builder: SnapshotBuilder, *,
                    rand_prop: Sequence[dict[str, Any]] = (),
                    content_tuning: Sequence[dict[str, Any]] = (),
                    squish_eras: Sequence[dict[str, Any]] = (),
                    armor_quality: Sequence[dict[str, Any]] = (),
                    armor_total: Sequence[dict[str, Any]] = (),
                    armor_shield: Sequence[dict[str, Any]] = (),
                    armor_location: Sequence[dict[str, Any]] = (),
                    damage: dict[str, Sequence[dict[str, Any]]] | None = None,
                    socket_cost: Sequence[Sequence[Any]] = (),
                    ratings_mult: Sequence[Sequence[Any]] = (),
                    stamina_mult: Sequence[Sequence[Any]] = (),
                    scaling_configs: Sequence[dict[str, Any]] = (),
                    offset_curves: Sequence[dict[str, Any]] = (),
                    ) -> SnapshotBuilder:
    rand_columns = ["ID", "DamageReplaceStatF", "DamageSecondaryF",
                    "DamageReplaceStat", "DamageSecondary"]
    for prefix in ("EpicF", "SuperiorF", "GoodF", "Epic", "Superior", "Good"):
        rand_columns.extend(f"{prefix}_{i}" for i in range(5))
    builder.table("RandPropPoints", rand_columns, rand_prop)
    builder.table("ContentTuning",
                  ["ID", "Flags", "ExpansionID", "HPScalingCurveID",
                   "DMGScalingCurveID", "HPPrimaryStatScalingCurveID",
                   "DMGPrimaryStatScalingCurveID",
                   "PrimaryStatScalingModPlayerDataElementCharacterID",
                   "PrimaryStatScalingModPlayerDataElementCharacterMultiplier",
                   "MinLevelSquish", "MaxLevelSquish", "MinLevelScalingOffset",
                   "MaxLevelScalingOffset", "AllowedMinOffset",
                   "AllowedMaxOffset", "LfgMinLevel", "LfgMaxLevel", "ILevel",
                   "XpMultQuest"], content_tuning)
    builder.table("ItemSquishEra", ["ID", "Patch", "CurveID", "Flags"], squish_eras)
    builder.table("ItemArmorQuality",
                  ["ID"] + [f"Qualitymod_{i}" for i in range(7)], armor_quality)
    builder.table("ItemArmorTotal",
                  ["ID", "ItemLevel", "Cloth", "Leather", "Mail", "Plate"],
                  armor_total)
    builder.table("ItemArmorShield",
                  ["ID"] + [f"Quality_{i}" for i in range(7)] + ["ItemLevel"],
                  armor_shield)
    builder.table("ArmorLocation",
                  ["ID", "Clothmodifier", "Leathermodifier", "Chainmodifier",
                   "Platemodifier", "Modifier"], armor_location)
    damage = damage or {}
    for name in ("ItemDamageOneHand", "ItemDamageOneHandCaster",
                 "ItemDamageTwoHand", "ItemDamageTwoHandCaster",
                 "ItemDamageAmmo"):
        builder.table(name, ["ID", "ItemLevel"] + [f"Quality_{i}" for i in range(7)],
                      damage.get(name, ()))
    builder.table("ItemScalingConfig",
                  ["ID", "ItemOffsetCurveID", "ItemLevel", "RequiredLevel",
                   "ItemSquishEraID", "Flags"], scaling_configs)
    builder.table("ItemOffsetCurve", ["ID", "CurveID", "Offset"], offset_curves)
    builder.gametable("ItemSocketCostPerLevel", ["5.0 Level", "Socket Cost"],
                      socket_cost or [[1, 0]])
    builder.gametable("CombatRatingsMultByILvl",
                      ["Item Level", "Armor Multiplier", "Weapon Multiplier",
                       "Trinket Multiplier", "Jewelry Multiplier"],
                      ratings_mult or [[1, 1, 1, 1, 1]])
    builder.gametable("StaminaMultByILvl",
                      ["Item Level", "Armor Multiplier", "Weapon Multiplier",
                       "Trinket Multiplier", "Jewelry Multiplier"],
                      stamina_mult or [[1, 1, 1, 1, 1]])
    return builder


def minimal_curves(builder: SnapshotBuilder,
                   curves: Sequence[dict[str, Any]] = (),
                   points: Sequence[dict[str, Any]] = ()) -> SnapshotBuilder:
    builder.table("Curve", ["ID", "Type", "Flags"], curves)
    builder.table("CurvePoint",
                  ["Pos_0", "Pos_1", "PosPreSquish_0", "PosPreSquish_1", "ID",
                   "CurveID", "OrderIndex"], points)
    return builder


def minimal_misc(builder: SnapshotBuilder,
                 enchants: Sequence[dict[str, Any]] = (),
                 gem_properties: Sequence[dict[str, Any]] = (),
                 item_sets: Sequence[dict[str, Any]] = (),
                 item_set_spells: Sequence[dict[str, Any]] = (),
                 global_curves: Sequence[dict[str, Any]] = (),
                 combat_ratings: Sequence[Sequence[Any]] = (),
                 spell_scaling: Sequence[Sequence[Any]] = ()) -> SnapshotBuilder:
    enchant_columns = ["ID", "Name_lang", "HordeName_lang", "Duration"]
    enchant_columns += [f"EffectArg_{i}" for i in range(3)]
    enchant_columns += ["Flags"]
    enchant_columns += [f"EffectScalingPoints_{i}" for i in range(3)]
    enchant_columns += ["IconFileDataID", "ItemLevelMin", "ItemLevelMax",
                        "TransmogUseConditionID", "TransmogCost"]
    enchant_columns += [f"EffectPointsMin_{i}" for i in range(3)]
    enchant_columns += ["ItemVisual", "RequiredSkillID", "RequiredSkillRank",
                        "ItemLevel", "Charges"]
    enchant_columns += [f"Effect_{i}" for i in range(3)]
    enchant_columns += ["ScalingClass", "ScalingClassRestricted", "Condition_ID",
                        "MinLevel", "MaxLevel"]
    builder.table("SpellItemEnchantment", enchant_columns, enchants)
    builder.table("GemProperties", ["ID", "Enchant_ID", "Type"], gem_properties)
    builder.table("ItemSet",
                  ["ID", "Name_lang", "SetFlags", "RequiredSkill",
                   "RequiredSkillRank"] + [f"ItemID_{i}" for i in range(17)],
                  item_sets)
    builder.table("ItemSetSpell",
                  ["ID", "ChrSpecID", "SpellID", "TraitSubTreeID", "Threshold",
                   "ItemSetID"], item_set_spells)
    builder.table("GlobalCurve", ["ID", "CurveID", "Type"], global_curves)
    builder.gametable("CombatRatings",
                      ["Level"] + [f"c{i}" for i in range(32)],
                      combat_ratings or [[1] + [1.0] * 32])
    builder.gametable("SpellScaling",
                      ["Level"] + [f"c{i}" for i in range(24)],
                      spell_scaling or [[1] + [1.0] * 24])
    return builder
