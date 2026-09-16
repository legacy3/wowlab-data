"""Effective item level, stat generation, armor and weapon damage.

Mirrors:
* ``Item::GetItemLevel(ItemTemplate const*, BonusData const&, ...)`` and
  ``Item::GetItemStatValue`` (``src/server/game/Entities/Item/Item.cpp``);
* ``GetRandomPropertyPoints`` (``ItemEnchantmentMgr.cpp``);
* ``ItemTemplate::GetArmor`` / ``GetDPS`` / ``GetDamage`` (``ItemTemplate.cpp``);
* ``GetIlvlStatMultiplier`` (``GameTables.cpp``);
* the stat-multiplier and rounding stage of ``Player::_ApplyItemBonuses`` and
  ``Player::_ApplyWeaponDamage`` (``Player.cpp``).

The evaluation *order* inside :func:`effective_item_level` is the part most
likely to be got wrong, so every stage appends a provenance record naming
itself.  In particular: on the ItemLevelOffsetCurve branch, Trinity does **not**
add ``BonusData::ItemLevelBonus``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Sequence

from . import SourceError
from .bonus import BonusData
from .curves import Curves, round_half_away
from .enums import (
    ARMOR_SUBCLASS_COLUMNS,
    CONTENT_TUNING_FLAG_DISABLED_FOR_ITEM,
    CURRENT_EXPANSION,
    EXPANSION_MAX_LEVEL,
    INVTYPE_2HWEAPON,
    INVTYPE_AMMO,
    INVTYPE_BODY,
    INVTYPE_CHEST,
    INVTYPE_CLOAK,
    INVTYPE_FEET,
    INVTYPE_FINGER,
    INVTYPE_HANDS,
    INVTYPE_HEAD,
    INVTYPE_HOLDABLE,
    INVTYPE_LEGS,
    INVTYPE_NECK,
    INVTYPE_NON_EQUIP,
    INVTYPE_RANGED,
    INVTYPE_RANGEDRIGHT,
    INVTYPE_RELIC,
    INVTYPE_ROBE,
    INVTYPE_SHIELD,
    INVTYPE_SHOULDERS,
    INVTYPE_THROWN,
    INVTYPE_TRINKET,
    INVTYPE_WAIST,
    INVTYPE_WEAPON,
    INVTYPE_WEAPONMAINHAND,
    INVTYPE_WEAPONOFFHAND,
    INVTYPE_WRISTS,
    ITEM_CLASS_ARMOR,
    ITEM_CLASS_WEAPON,
    ITEM_SQUISH_ERA_FLAG_SKIP,
    ITEM_SUBCLASS_ARMOR_SHIELD,
    ITEM_SUBCLASS_WEAPON_BOW,
    ITEM_SUBCLASS_WEAPON_CROSSBOW,
    ITEM_SUBCLASS_WEAPON_GUN,
    ITEM_SUBCLASS_WEAPON_WAND,
    MAX_ITEM_LEVEL,
    MAX_LEVEL,
    MIN_ITEM_LEVEL,
    MOD_NAMES,
    QUALITY_ARTIFACT,
    QUALITY_EPIC,
    QUALITY_HEIRLOOM,
    QUALITY_LEGENDARY,
    QUALITY_RARE,
    QUALITY_UNCOMMON,
    UNSCALED_MODS,
)
from .items import ItemTemplate
from .tables import GameTable, Tables

#: Inventory type -> RandPropPoints column index.
#: Mirrors: the switch at the top of ``GetRandomPropertyPoints``.
_RAND_PROP_INDEX_BY_INVTYPE = {
    INVTYPE_HEAD: 0, INVTYPE_BODY: 0, INVTYPE_CHEST: 0, INVTYPE_LEGS: 0,
    INVTYPE_RANGED: 0, INVTYPE_2HWEAPON: 0, INVTYPE_ROBE: 0, INVTYPE_THROWN: 0,
    INVTYPE_WEAPON: 3, INVTYPE_WEAPONMAINHAND: 3, INVTYPE_WEAPONOFFHAND: 3,
    INVTYPE_SHOULDERS: 1, INVTYPE_WAIST: 1, INVTYPE_FEET: 1, INVTYPE_HANDS: 1,
    INVTYPE_TRINKET: 1,
    INVTYPE_NECK: 2, INVTYPE_WRISTS: 2, INVTYPE_FINGER: 2, INVTYPE_SHIELD: 2,
    INVTYPE_CLOAK: 2, INVTYPE_HOLDABLE: 2,
    INVTYPE_RELIC: 4,
}

#: ``GetIlvlStatMultiplier`` column order in both
#: CombatRatingsMultByILvl.txt and StaminaMultByILvl.txt.
ILVL_MULT_ARMOR = 0
ILVL_MULT_WEAPON = 1
ILVL_MULT_TRINKET = 2
ILVL_MULT_JEWELRY = 3

_ILVL_MULT_WEAPON_INVTYPES = frozenset({
    INVTYPE_WEAPON, INVTYPE_SHIELD, INVTYPE_RANGED, INVTYPE_2HWEAPON,
    INVTYPE_WEAPONMAINHAND, INVTYPE_WEAPONOFFHAND, INVTYPE_HOLDABLE,
    INVTYPE_RANGEDRIGHT,
})


def rand_prop_index(inventory_type: int, subclass: int) -> int | None:
    """Mirrors: ``GetRandomPropertyPoints``'s inventory-type switch.

    ``INVTYPE_RANGEDRIGHT`` splits on subclass: a wand shares the one-hand
    column, everything else shares the large/2H column.
    """
    if inventory_type == INVTYPE_RANGEDRIGHT:
        return 3 if subclass == ITEM_SUBCLASS_WEAPON_WAND else 0
    return _RAND_PROP_INDEX_BY_INVTYPE.get(inventory_type)


def rand_prop_quality_column(quality: int) -> str | None:
    """Mirrors: ``GetRandomPropertyPoints``'s quality switch.

    Poor/Common/WoWToken have no column and yield 0 points.
    """
    if quality == QUALITY_UNCOMMON:
        return "GoodF"
    if quality in (QUALITY_RARE, QUALITY_HEIRLOOM):
        return "SuperiorF"
    if quality in (QUALITY_EPIC, QUALITY_LEGENDARY, QUALITY_ARTIFACT):
        return "EpicF"
    return None


def ilvl_mult_column(inventory_type: int) -> int:
    """Mirrors: ``GetIlvlStatMultiplier`` (GameTables.cpp)."""
    if inventory_type in (INVTYPE_NECK, INVTYPE_FINGER):
        return ILVL_MULT_JEWELRY
    if inventory_type == INVTYPE_TRINKET:
        return ILVL_MULT_TRINKET
    if inventory_type in _ILVL_MULT_WEAPON_INVTYPES:
        return ILVL_MULT_WEAPON
    return ILVL_MULT_ARMOR


@dataclass
class StatValue:
    """One resolved item stat slot, with every ingredient recorded."""

    stat_index: int
    stat_type: int
    stat_name: str
    stat_allocation: int
    stat_percentage_of_socket: float
    path: str
    rand_prop_index: int | None = None
    rand_prop_quality_column: str | None = None
    rand_prop_row: int | None = None
    rand_prop_points: float | None = None
    value_before_socket_cost: float | None = None
    socket_cost_per_level: float | None = None
    raw_value: float = 0.0
    combat_ratings_mult_by_ilvl: float | None = None
    stamina_mult_by_ilvl: float | None = None
    value_before_round: float | None = None
    final_value: int = 0
    ratings: tuple[str, ...] = ()
    is_primary: bool = False

    def to_dict(self) -> dict[str, Any]:
        out = {k: v for k, v in self.__dict__.items() if v is not None}
        out["ratings"] = list(self.ratings)
        return out


class ScalingEngine:
    """Item-level and stat derivation over one table snapshot."""

    def __init__(self, tables: Tables, curves: Curves) -> None:
        self.tables = tables
        self.curves = curves
        self._content_tuning = tables("ContentTuning").by("ID")
        conditional = tables.optional("ConditionalContentTuning")
        self._conditional_content_tuning = (
            conditional.group("ParentContentTuningID") if conditional else {})
        self._rand_prop_points = tables("RandPropPoints").by("ID")
        self._item_squish_era = tables("ItemSquishEra").by("ID")
        self._squish_era_max_id = max(
            (int(r["ID"]) for r in tables("ItemSquishEra")), default=0)
        self._azerite_level_info = tables.optional("AzeriteLevelInfo")
        self._armor_quality = tables("ItemArmorQuality").by("ID")
        self._armor_total = tables("ItemArmorTotal").by("ID")
        self._armor_shield = tables("ItemArmorShield").by("ID")
        self._armor_location = tables("ArmorLocation").by("ID")
        self._damage_tables = {
            "OneHand": tables("ItemDamageOneHand").by("ID"),
            "OneHandCaster": tables("ItemDamageOneHandCaster").by("ID"),
            "TwoHand": tables("ItemDamageTwoHand").by("ID"),
            "TwoHandCaster": tables("ItemDamageTwoHandCaster").by("ID"),
            "Ammo": tables("ItemDamageAmmo").by("ID"),
        }
        self.gt_combat_ratings_mult = tables.gametable("CombatRatingsMultByILvl")
        self.gt_stamina_mult = tables.gametable("StaminaMultByILvl")
        self.gt_socket_cost = tables.gametable("ItemSocketCostPerLevel")

    # -- content tuning --------------------------------------------------
    def redirected_content_tuning_id(self, content_tuning_id: int,
                                     redirect_flags: Sequence[int] = ()) -> int:
        """Mirrors: ``DB2Manager::GetRedirectedContentTuningId``.

        Note the direct consumer compares ``flag & redirectFlag[block]`` where
        ``flag`` is ``RedirectEnum % 32`` -- a bit *index*, not ``1 << index``.
        Reproduced verbatim; recorded as a suspected consumer bug in the
        research document.
        """
        rows = self._conditional_content_tuning.get(content_tuning_id) if \
            self._conditional_content_tuning else None
        if not rows:
            return content_tuning_id
        # Mirrors: sorted by OrderIndex descending at load time.
        for row in sorted(rows, key=lambda r: -int(r["OrderIndex"])):
            block, flag = divmod(int(row["RedirectEnum"]), 32)
            if block >= len(redirect_flags):
                continue
            if flag & redirect_flags[block]:
                return int(row["RedirectContentTuningID"])
        return content_tuning_id

    def content_tuning_levels(self, content_tuning_id: int,
                              redirect_flags: Sequence[int] = (),
                              for_item: bool = False) -> tuple[int, int] | None:
        """Mirrors: ``DB2Manager::GetContentTuningData`` (MinLevel/MaxLevel only).

        Source-name conflict: Trinity's ``MinLevel``/``MaxLevel`` are the client
        columns ``MinLevelSquish``/``MaxLevelSquish``, and its
        ``MinLevelType``/``MaxLevelType`` are
        ``MinLevelScalingOffset``/``MaxLevelScalingOffset``.  Positional layout
        is identical; only the names differ.
        """
        row = self._content_tuning.get(
            self.redirected_content_tuning_id(content_tuning_id, redirect_flags))
        if row is None:
            return None
        if for_item and (int(row["Flags"]) & CONTENT_TUNING_FLAG_DISABLED_FOR_ITEM):
            return None

        def adjust(calc_type: int) -> int:
            # ContentTuningCalcType: 1 MinLevel, 2 MaxLevel, 3 PrevExpansionMaxLevel
            if calc_type == 1:
                return 1
            if calc_type == 2:
                return EXPANSION_MAX_LEVEL[CURRENT_EXPANSION]
            if calc_type == 3:
                return EXPANSION_MAX_LEVEL[max(CURRENT_EXPANSION - 1, 0)]
            return 0

        min_level = int(row["MinLevelSquish"]) + adjust(int(row["MinLevelScalingOffset"]))
        max_level = int(row["MaxLevelSquish"]) + adjust(int(row["MaxLevelScalingOffset"]))
        return (max(1, min(min_level, MAX_LEVEL)), max(1, min(max_level, MAX_LEVEL)))

    # -- effective item level -------------------------------------------
    def effective_item_level(
        self,
        proto: ItemTemplate,
        bonus: BonusData,
        *,
        player_level: int,
        fixed_level: int = 0,
        min_item_level: int = 0,
        min_item_level_cutoff: int = 0,
        max_item_level: int = 0,
        pvp_bonus: bool = False,
        azerite_level: int = 0,
        current_build_patch: int | None = None,
        provenance: list[dict[str, Any]] | None = None,
    ) -> int:
        """Mirrors: ``Item::GetItemLevel(ItemTemplate const*, BonusData const&, ...)``.

        ``current_build_patch`` selects which ItemSquishEra rows apply.  Trinity
        derives it from the *realm's* client build through
        ``ClientBuild::GetMinorMajorBugfixVersionForBuild`` (major*10000 +
        minor*100 + bugfix), not from the item, so it must be supplied.  Passing
        ``None`` reproduces a server with no current realm: no squish is applied.
        """
        steps = provenance if provenance is not None else []
        item_level = bonus.ItemLevel
        steps.append({"step": "base", "item_level": item_level,
                      "source": "BonusData::ItemLevel"})

        if azerite_level and self._azerite_level_info is not None:
            azerite_row = self._azerite_level_info.lookup(azerite_level)
            if azerite_row is not None:
                item_level = int(azerite_row["ItemLevel"])
                steps.append({"step": "azerite-level-info",
                              "item_level": item_level,
                              "azerite_level": azerite_level})

        if not bonus.ItemLevelOffsetCurveId:
            if bonus.PlayerLevelToItemLevelCurveId:
                level = player_level
                if fixed_level:
                    level = fixed_level
                    steps.append({"step": "fixed-level", "level": level,
                                  "source": "ITEM_MODIFIER_TIMEWALKER_LEVEL"})
                else:
                    levels = self.content_tuning_levels(bonus.ContentTuningId, (), True)
                    if levels is not None:
                        level = min(max(player_level, levels[0]), levels[1])
                        steps.append({"step": "content-tuning-level-clamp",
                                      "content_tuning_id": bonus.ContentTuningId,
                                      "min_level": levels[0],
                                      "max_level": levels[1],
                                      "clamped_level": level})
                raw = self.curves.value_at(
                    bonus.PlayerLevelToItemLevelCurveId, level,
                    consumer="Item::GetItemLevel/PlayerLevelToItemLevelCurve")
                item_level = round_half_away(raw)
                steps.append({"step": "player-level-to-item-level-curve",
                              "curve_id": bonus.PlayerLevelToItemLevelCurveId,
                              "x": level, "raw_y": raw, "item_level": item_level,
                              "rounding": "std::round then uint32"})
            if bonus.ItemLevelBonus:
                item_level += bonus.ItemLevelBonus
                steps.append({"step": "item-level-bonus",
                              "delta": bonus.ItemLevelBonus,
                              "item_level": item_level,
                              "source": "sum of ITEM_BONUS_ITEM_LEVEL (type 1)"})
        else:
            raw = self.curves.value_at(
                bonus.ItemLevelOffsetCurveId, bonus.ItemLevelOffsetItemLevel,
                consumer="Item::GetItemLevel/ItemLevelOffsetCurve")
            item_level = bonus.ItemLevelOffset + round_half_away(raw)
            evaluation = self.curves.evaluations[-1]
            step = {
                "step": "item-level-offset-curve",
                "curve_id": bonus.ItemLevelOffsetCurveId,
                "x": bonus.ItemLevelOffsetItemLevel, "raw_y": raw,
                "offset": bonus.ItemLevelOffset, "item_level": item_level,
                "rounding": "std::round then uint32",
                "note": "ITEM_BONUS_ITEM_LEVEL is NOT added on this branch"}
            if evaluation.clamped:
                # Fail loud rather than quietly returning a clamped endpoint:
                # ITEM_BONUS_SCALING_CONFIG (type 51) forces
                # ItemLevelOffsetItemLevel to 0, and several current offset
                # curves have a player-level domain, so x=0 lands outside it.
                step["out_of_domain"] = evaluation.clamped
                step["curve_x_range"] = list(
                    self.curves.x_range(bonus.ItemLevelOffsetCurveId))
            steps.append(step)

        gem_bonus = sum(bonus.GemItemLevelBonus)
        if gem_bonus:
            item_level += gem_bonus
            steps.append({"step": "gem-item-level-bonus", "delta": gem_bonus,
                          "item_level": item_level})

        item_level_before_upgrades = item_level

        if pvp_bonus:
            if bonus.PvpItemLevel:
                item_level = bonus.PvpItemLevel
                steps.append({"step": "pvp-item-level-base",
                              "item_level": item_level})
            if bonus.PvpItemLevelBonus:
                item_level += bonus.PvpItemLevelBonus
                steps.append({"step": "pvp-item-level-increment",
                              "delta": bonus.PvpItemLevelBonus,
                              "item_level": item_level})

        if not bonus.IgnoreSquish and current_build_patch is not None:
            item_level = self._apply_squish(bonus, item_level,
                                            current_build_patch, steps)

        if proto.inventory_type != INVTYPE_NON_EQUIP:
            if (min_item_level
                    and (not min_item_level_cutoff
                         or item_level_before_upgrades >= min_item_level_cutoff)
                    and item_level < min_item_level):
                item_level = min_item_level
                steps.append({"step": "unit-min-item-level-floor",
                              "item_level": item_level,
                              "source": "UnitData::MinItemLevel"})
            if max_item_level and item_level > max_item_level:
                item_level = max_item_level
                steps.append({"step": "unit-max-item-level-cap",
                              "item_level": item_level,
                              "source": "UnitData::MaxItemLevel"})

        clamped = min(max(item_level, MIN_ITEM_LEVEL), MAX_ITEM_LEVEL)
        if clamped != item_level:
            steps.append({"step": "global-clamp", "item_level": clamped,
                          "bounds": [MIN_ITEM_LEVEL, MAX_ITEM_LEVEL]})
        return clamped

    def _apply_squish(self, bonus: BonusData, item_level: int,
                      current_build_patch: int,
                      steps: list[dict[str, Any]]) -> int:
        """Mirrors: the ItemSquishEra loop inside ``Item::GetItemLevel``.

        Trinity iterates ``squishId`` from ``ItemSquishEraID + 1`` to
        ``sItemSquishEraStore.GetNumRows()``; that store is indexed by ID, so
        the bound is the maximum ID plus one.
        """
        for squish_id in range(bonus.ItemSquishEraID + 1, self._squish_era_max_id + 1):
            squish = self._item_squish_era.get(squish_id)
            if squish is None or (int(squish["Flags"]) & ITEM_SQUISH_ERA_FLAG_SKIP):
                continue
            if int(squish["Patch"]) > current_build_patch:
                break
            curve_id = int(squish["CurveID"])
            if not curve_id:
                continue
            raw = self.curves.value_at(
                curve_id, item_level, consumer="Item::GetItemLevel/ItemSquishEra")
            item_level = round_half_away(raw)
            steps.append({"step": "item-squish-era",
                          "item_squish_era_id": squish_id,
                          "patch": int(squish["Patch"]),
                          "curve_id": curve_id, "raw_y": raw,
                          "item_level": item_level,
                          "rounding": "std::round then uint32"})
        return item_level

    # -- stat generation -------------------------------------------------
    def random_property_points(self, item_level: int, quality: int,
                               inventory_type: int, subclass: int) -> float:
        """Mirrors: ``GetRandomPropertyPoints`` (ItemEnchantmentMgr.cpp)."""
        index = rand_prop_index(inventory_type, subclass)
        if index is None:
            return 0.0
        row = self._rand_prop_points.get(item_level)
        if row is None:
            return 0.0
        column = rand_prop_quality_column(quality)
        if column is None:
            return 0.0
        return float(row[f"{column}_{index}"])

    def item_stat_value(self, proto: ItemTemplate, bonus: BonusData,
                        index: int, item_level: int) -> StatValue:
        """Mirrors: ``Item::GetItemStatValue``.

        ``statValue = StatPercentEditor * randPropPoints * 0.0001``
        then ``-= StatPercentageOfSocket * ItemSocketCostPerLevel[itemLevel]``.
        Corruption stats bypass the whole thing and return the raw allocation.
        """
        stat_type = bonus.ItemStatType[index]
        value = StatValue(
            stat_index=index,
            stat_type=stat_type,
            stat_name=MOD_NAMES.get(stat_type, str(stat_type)),
            stat_allocation=bonus.StatPercentEditor[index],
            stat_percentage_of_socket=bonus.ItemStatSocketCostMultiplier[index],
            path="",
        )
        if stat_type in UNSCALED_MODS:
            value.path = "verbatim-StatPercentEditor"
            value.raw_value = float(value.stat_allocation)
            return value

        rpp = self.random_property_points(item_level, bonus.Quality,
                                          proto.inventory_type, proto.subclass_id)
        value.rand_prop_index = rand_prop_index(proto.inventory_type,
                                                proto.subclass_id)
        value.rand_prop_quality_column = rand_prop_quality_column(bonus.Quality)
        value.rand_prop_row = item_level
        value.rand_prop_points = rpp
        if not rpp:
            value.path = "no-rand-prop-points"
            value.raw_value = 0.0
            return value

        raw = float(value.stat_allocation * rpp) * 0.0001
        value.path = "allocation * randPropPoints * 0.0001 - socketCost"
        value.value_before_socket_cost = raw
        socket_cost = self.gt_socket_cost.column(item_level, 0)
        if socket_cost is not None:
            value.socket_cost_per_level = socket_cost
            raw -= float(value.stat_percentage_of_socket * socket_cost)
        value.raw_value = raw
        return value

    def ilvl_stat_multiplier(self, gt: GameTable, item_level: int,
                             inventory_type: int) -> float | None:
        """Mirrors: ``GetIlvlStatMultiplier``; ``None`` when the row is absent."""
        row = gt.row(item_level)
        if row is None:
            return None
        return row[ilvl_mult_column(inventory_type)]

    # -- armor / weapon --------------------------------------------------
    def armor(self, proto: ItemTemplate, quality: int, item_level: int) -> int:
        """Mirrors: ``ItemTemplate::GetArmor``.

        Heirloom is treated as Rare; anything above Artifact yields 0.  The
        final conversion is ``uint32(value + 0.5f)`` -- truncation of a biased
        float, not ``std::round``.
        """
        q = quality if quality != QUALITY_HEIRLOOM else QUALITY_RARE
        if q > QUALITY_ARTIFACT:
            return 0

        is_shield = (proto.class_id == ITEM_CLASS_ARMOR
                     and proto.subclass_id == ITEM_SUBCLASS_ARMOR_SHIELD)
        if not is_shield:
            armor_quality = self._armor_quality.get(item_level)
            armor_total = self._armor_total.get(item_level)
            if armor_quality is None or armor_total is None:
                return 0
            inv = proto.inventory_type
            if inv == INVTYPE_ROBE:
                inv = INVTYPE_CHEST
            location = self._armor_location.get(inv)
            if location is None:
                return 0
            columns = ARMOR_SUBCLASS_COLUMNS.get(proto.subclass_id)
            if columns is None:
                return 0
            total_column, modifier_column = columns
            return int(float(armor_quality[f"Qualitymod_{q}"])
                       * float(armor_total[total_column])
                       * float(location[modifier_column]) + 0.5)

        shield = self._armor_shield.get(item_level)
        if shield is None:
            return 0
        return int(float(shield[f"Quality_{q}"]) + 0.5)

    def damage_table_name(self, proto: ItemTemplate) -> str | None:
        """Mirrors: the inventory-type/subclass switch in ``ItemTemplate::GetDPS``."""
        inv = proto.inventory_type
        caster = proto.is_caster_weapon
        if inv == INVTYPE_AMMO:
            return "Ammo"
        if inv == INVTYPE_2HWEAPON:
            return "TwoHandCaster" if caster else "TwoHand"
        if inv in (INVTYPE_RANGED, INVTYPE_THROWN, INVTYPE_RANGEDRIGHT):
            if proto.subclass_id == ITEM_SUBCLASS_WEAPON_WAND:
                return "OneHandCaster"
            if proto.subclass_id in (ITEM_SUBCLASS_WEAPON_BOW,
                                     ITEM_SUBCLASS_WEAPON_GUN,
                                     ITEM_SUBCLASS_WEAPON_CROSSBOW):
                return "TwoHandCaster" if caster else "TwoHand"
            return None
        if inv in (INVTYPE_WEAPON, INVTYPE_WEAPONMAINHAND, INVTYPE_WEAPONOFFHAND):
            return "OneHandCaster" if caster else "OneHand"
        return None

    def dps(self, proto: ItemTemplate, quality: int, item_level: int) -> float:
        """Mirrors: ``ItemTemplate::GetDPS``."""
        q = quality if quality != QUALITY_HEIRLOOM else QUALITY_RARE
        if proto.class_id != ITEM_CLASS_WEAPON or q > QUALITY_ARTIFACT:
            return 0.0
        table = self.damage_table_name(proto)
        if table is None:
            return 0.0
        row = self._damage_tables[table].get(item_level)
        if row is None:
            # Trinity uses AssertEntry here, which would abort the server.
            raise SourceError(
                f"ItemDamage{table} has no row for item level {item_level} "
                f"(item {proto.item_id}); Trinity's AssertEntry would abort")
        return float(row[f"Quality_{q}"])

    def weapon_damage(self, proto: ItemTemplate, quality: int,
                      item_level: int) -> tuple[float, float, float]:
        """Mirrors: ``ItemTemplate::GetDamage``.  Returns ``(min, max, dps)``.

        ``max`` goes through ``floor(x + 0.5f)`` while ``min`` does not, so the
        two are asymmetric on purpose.
        """
        dps = self.dps(proto, quality, item_level)
        if dps <= 0.0:
            return (0.0, 0.0, 0.0)
        avg = dps * proto.delay * 0.001
        variance = proto.dmg_variance
        min_damage = (variance * -0.5 + 1.0) * avg
        max_damage = math.floor(avg * (variance * 0.5 + 1.0) + 0.5)
        return (min_damage, max_damage, dps)

    @staticmethod
    def weapon_attack_power(dps: float) -> int:
        """Mirrors: ``Player::_ApplyWeaponDamage`` -- ``int32(dps * 6.0f)``."""
        return int(dps * 6.0)
