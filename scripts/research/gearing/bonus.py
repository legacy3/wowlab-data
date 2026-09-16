"""``BonusData``: the accumulator an item instance's bonus lists write into.

Mirrors: ``struct BonusData`` (``src/server/game/Entities/Item/Item.h``) plus
``BonusData::Initialize`` / ``BonusData::AddBonusList`` / ``BonusData::AddBonus``
(``src/server/game/Entities/Item/Item.cpp``).

Field names are kept identical to Trinity's so the port can be diffed against
the C++ case by case.  Ordering matters: several bonus types are
priority-selected (lowest ``Value[n]`` wins) and several are additive, so
applying the same rows in a different order does not always give the same
result.  That asymmetry is intentional and is covered by the tests.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Protocol

from . import SourceError
from .enums import (
    BONUS_APPEARANCE,
    BONUS_AZERITE_TIER_UNLOCK_SET,
    BONUS_BONDING,
    BONUS_BONDING_WITH_PRIORITY,
    BONUS_DISENCHANT_LOOT_ID,
    BONUS_ITEM_BONUS_LIST,
    BONUS_ITEM_EFFECT_ID,
    BONUS_ITEM_LEVEL,
    BONUS_ITEM_LEVEL_BASE,
    BONUS_ITEM_LIMIT_CATEGORY,
    BONUS_ITEM_OFFSET_CURVE,
    BONUS_OVERRIDE_CANNOT_TRADE_BOP,
    BONUS_OVERRIDE_CAN_DISENCHANT,
    BONUS_OVERRIDE_CAN_RECRAFT,
    BONUS_OVERRIDE_CAN_SALVAGE,
    BONUS_OVERRIDE_CAN_SCRAP,
    BONUS_OVERRIDE_REQUIRED_LEVEL,
    BONUS_PVP_ITEM_LEVEL_BASE,
    BONUS_PVP_ITEM_LEVEL_INCREMENT,
    BONUS_QUALITY,
    BONUS_RELIC_TYPE,
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
    BONUS_TYPE_NAMES,
    FLAG2_NO_TRADE_BIND_ON_ACQUIRE,
    FLAG4_NO_SALVAGE,
    FLAG4_RECRAFTABLE,
    FLAG4_SCRAPABLE,
    FLAG_NO_DISENCHANT,
    MAX_ITEM_PROTO_SOCKETS,
    MAX_ITEM_PROTO_STATS,
    SCALING_CONFIG_FLAG_IGNORE_SQUISH,
)
from .items import ItemTemplate

_INT_MAX = 2 ** 31 - 1

#: Max nesting for ``ITEM_BONUS_ITEM_BONUS_LIST`` (type 50).  Trinity has no
#: guard at all; a self-referential list would recurse forever there.  This
#: package fails closed instead.
MAX_BONUS_LIST_DEPTH = 16


class BonusSource(Protocol):
    """The small slice of the resolver that :class:`BonusData` needs."""

    def bonuses_for_list(self, bonus_list_id: int) -> list[dict[str, Any]]: ...
    def item_effect(self, effect_id: int) -> dict[str, Any] | None: ...
    def scaling_config(self, config_id: int) -> dict[str, Any] | None: ...
    def item_offset_curve(self, curve_id: int) -> dict[str, Any] | None: ...


@dataclass(frozen=True)
class AppliedBonus:
    """One ``ItemBonus`` row as it was applied, with its provenance."""

    bonus_list_id: int
    bonus_id: int
    type: int
    values: tuple[int, int, int, int]
    order_index: int
    handled: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "bonus_list_id": self.bonus_list_id,
            "item_bonus_id": self.bonus_id,
            "type": self.type,
            "type_name": BONUS_TYPE_NAMES.get(self.type, str(self.type)),
            "values": list(self.values),
            "order_index": self.order_index,
            "handled": self.handled,
        }


class BonusData:
    """Mutable accumulator for one item instance's bonus lists."""

    def __init__(self, proto: ItemTemplate, source: BonusSource) -> None:
        self._source = source
        self.proto = proto
        self.trace: list[AppliedBonus] = []
        self.unhandled: list[AppliedBonus] = []
        self.applied_bonus_lists: list[int] = []

        # -- BonusData::Initialize(ItemTemplate const*) -------------------
        self.Quality = proto.quality
        self.ItemLevel = proto.base_item_level
        self.ItemLevelBonus = 0
        self.RequiredLevel = proto.base_required_level
        self.ItemStatType = [proto.stat_type(i) for i in range(MAX_ITEM_PROTO_STATS)]
        self.StatPercentEditor = [
            proto.stat_percent_editor(i) for i in range(MAX_ITEM_PROTO_STATS)]
        self.ItemStatSocketCostMultiplier = [
            proto.stat_percentage_of_socket(i) for i in range(MAX_ITEM_PROTO_STATS)]
        self.SocketColor = [
            proto.socket_color(i) for i in range(MAX_ITEM_PROTO_SOCKETS)]
        self.Bonding = proto.bonding
        self.AppearanceModID = 0
        self.RepairCostMultiplier = 1.0
        self.ContentTuningId = proto.content_tuning_id
        self.PlayerLevelToItemLevelCurveId = proto.player_level_to_item_level_curve_id
        self.DisenchantLootId = 0
        self.GemItemLevelBonus = [0] * MAX_ITEM_PROTO_SOCKETS
        self.GemRelicType = [-1] * MAX_ITEM_PROTO_SOCKETS
        self.GemRelicRankBonus = [0] * MAX_ITEM_PROTO_SOCKETS
        self.RelicType = -1
        self.RequiredLevelOverride = 0
        # AzeriteEmpoweredItem lookup is deliberately not reproduced: no current
        # item reaches it and the table is a Battle for Azeroth remnant.
        self.AzeriteTierUnlockSetId = 0
        self.Suffix = 0
        self.RequiredLevelCurve = 0
        self.PvpItemLevel = 0
        self.PvpItemLevelBonus = 0
        self.ItemLevelOffsetCurveId = proto.item_level_offset_curve_id
        self.ItemLevelOffsetItemLevel = proto.item_level_offset_item_level
        self.ItemLevelOffset = 0
        self.ItemSquishEraID = proto.item_squish_era_id
        self.Effects: list[dict[str, Any]] = list(proto.effects)
        self.LimitCategory = proto.limit_category
        self.CanDisenchant = not proto.has_flag(FLAG_NO_DISENCHANT)
        self.CanScrap = proto.has_flag(FLAG4_SCRAPABLE)
        self.CanSalvage = not proto.has_flag(FLAG4_NO_SALVAGE)
        self.CanRecraft = proto.has_flag(FLAG4_RECRAFTABLE)
        self.HasFixedLevel = False
        self.CannotTradeBindOnPickup = proto.has_flag(FLAG2_NO_TRADE_BIND_ON_ACQUIRE)
        self.IgnoreSquish = False

        # BonusData::_state -- priority tracking for the "lowest wins" types.
        self._suffix_priority = _INT_MAX
        self._appearance_priority = _INT_MAX
        self._disenchant_priority = _INT_MAX
        self._scaling_priority = _INT_MAX
        self._azerite_priority = _INT_MAX
        self._required_level_curve_priority = _INT_MAX
        self._item_level_priority = _INT_MAX
        self._pvp_item_level_priority = _INT_MAX
        self._bonding_priority = _INT_MAX
        self._has_quality_bonus = False
        self._has_item_limit_category = False

    # -- application ----------------------------------------------------
    def add_bonus_list(self, bonus_list_id: int, *, depth: int = 0) -> None:
        """Mirrors: ``BonusData::AddBonusList``.

        Applying the same list twice applies its rows twice, which doubles the
        additive types.  That is Trinity's behaviour and is not deduplicated
        here; callers that care get a warning from the resolver.
        """
        if depth > MAX_BONUS_LIST_DEPTH:
            raise SourceError(
                f"bonus list nesting exceeded depth {MAX_BONUS_LIST_DEPTH} "
                f"at list {bonus_list_id}; source rows are cyclic")
        self.applied_bonus_lists.append(bonus_list_id)
        for row in self._source.bonuses_for_list(bonus_list_id):
            self.add_bonus(AppliedBonus(
                bonus_list_id=bonus_list_id,
                bonus_id=int(row["ID"]),
                type=int(row["Type"]),
                values=(int(row["Value_0"]), int(row["Value_1"]),
                        int(row["Value_2"]), int(row["Value_3"])),
                order_index=int(row["OrderIndex"]),
            ), depth=depth)

    def add_bonus(self, applied: AppliedBonus, *, depth: int = 0) -> None:
        """Mirrors: ``BonusData::AddBonus`` -- one branch per ``ItemBonusType``."""
        handler = _HANDLERS.get(applied.type)
        if handler is None:
            # Every remaining ItemBonusType is presentation-only or marked NYI
            # in the direct consumer.  Recorded, never guessed at.
            recorded = replace(applied, handled=False)
            self.trace.append(recorded)
            self.unhandled.append(recorded)
            return
        self.trace.append(applied)
        handler(self, applied.values, depth)

    # -- per-type handlers (kept one-to-one with the C++ switch) ---------
    def _item_level(self, v: tuple[int, ...], depth: int) -> None:
        self.ItemLevelBonus += v[0]

    def _stat(self, v: tuple[int, ...], depth: int) -> None:
        index = MAX_ITEM_PROTO_STATS
        for i in range(MAX_ITEM_PROTO_STATS):
            if self.ItemStatType[i] == v[0] or self.ItemStatType[i] == -1:
                index = i
                break
        if index < MAX_ITEM_PROTO_STATS:
            self.ItemStatType[index] = v[0]
            self.StatPercentEditor[index] += v[1]

    def _quality(self, v: tuple[int, ...], depth: int) -> None:
        if not self._has_quality_bonus:
            self.Quality = v[0]
            self._has_quality_bonus = True
        elif self.Quality < v[0]:
            self.Quality = v[0]

    def _suffix(self, v: tuple[int, ...], depth: int) -> None:
        if v[1] < self._suffix_priority:
            self.Suffix = v[0]
            self._suffix_priority = v[1]

    def _socket(self, v: tuple[int, ...], depth: int) -> None:
        remaining = v[0]
        for i in range(MAX_ITEM_PROTO_SOCKETS):
            if remaining <= 0:
                break
            if not self.SocketColor[i]:
                self.SocketColor[i] = v[1]
                remaining -= 1

    def _appearance(self, v: tuple[int, ...], depth: int) -> None:
        if v[1] < self._appearance_priority:
            self.AppearanceModID = v[0]
            self._appearance_priority = v[1]

    def _required_level(self, v: tuple[int, ...], depth: int) -> None:
        self.RequiredLevel += v[0]

    def _repair_cost(self, v: tuple[int, ...], depth: int) -> None:
        self.RepairCostMultiplier *= v[0] * 0.01

    def _scaling_stat_distribution(self, v: tuple[int, ...], depth: int) -> None:
        if v[1] < self._scaling_priority:
            self.ContentTuningId = v[2]
            self.PlayerLevelToItemLevelCurveId = v[3]
            self._scaling_priority = v[1]
            self.HasFixedLevel = False

    def _scaling_stat_distribution_fixed(self, v: tuple[int, ...], depth: int) -> None:
        if v[1] < self._scaling_priority:
            self.ContentTuningId = v[2]
            self.PlayerLevelToItemLevelCurveId = v[3]
            self._scaling_priority = v[1]
            self.HasFixedLevel = True

    def _disenchant_loot(self, v: tuple[int, ...], depth: int) -> None:
        if v[1] < self._disenchant_priority:
            self.DisenchantLootId = v[0]
            self._disenchant_priority = v[1]

    def _bonding(self, v: tuple[int, ...], depth: int) -> None:
        self.Bonding = v[0]

    def _relic_type(self, v: tuple[int, ...], depth: int) -> None:
        self.RelicType = v[0]

    def _override_required_level(self, v: tuple[int, ...], depth: int) -> None:
        self.RequiredLevelOverride = v[0]

    def _azerite_tier_unlock_set(self, v: tuple[int, ...], depth: int) -> None:
        if v[1] < self._azerite_priority:
            self.AzeriteTierUnlockSetId = v[0]
            self._azerite_priority = v[1]

    def _can_disenchant(self, v: tuple[int, ...], depth: int) -> None:
        self.CanDisenchant = v[0] != 0

    def _can_scrap(self, v: tuple[int, ...], depth: int) -> None:
        self.CanScrap = v[0] != 0

    def _item_effect_id(self, v: tuple[int, ...], depth: int) -> None:
        effect = self._source.item_effect(v[0])
        if effect is not None:
            self.Effects.append(effect)

    def _required_level_curve(self, v: tuple[int, ...], depth: int) -> None:
        if v[2] < self._required_level_curve_priority:
            self.RequiredLevelCurve = v[0]
            self._required_level_curve_priority = v[2]
            if v[1]:
                self.ContentTuningId = v[1]

    def _item_limit_category(self, v: tuple[int, ...], depth: int) -> None:
        if not self._has_item_limit_category:
            self.LimitCategory = v[0]
            self._has_item_limit_category = True

    def _pvp_item_level_increment(self, v: tuple[int, ...], depth: int) -> None:
        self.PvpItemLevelBonus += v[0]

    def _can_salvage(self, v: tuple[int, ...], depth: int) -> None:
        self.CanSalvage = v[0] != 0

    def _can_recraft(self, v: tuple[int, ...], depth: int) -> None:
        self.CanRecraft = v[0] != 0

    def _item_level_base(self, v: tuple[int, ...], depth: int) -> None:
        if v[1] < self._item_level_priority:
            self.ItemLevel = v[0]
            self._item_level_priority = v[1]

    def _pvp_item_level_base(self, v: tuple[int, ...], depth: int) -> None:
        if v[1] < self._pvp_item_level_priority:
            self.PvpItemLevel = v[0]
            self._pvp_item_level_priority = v[1]

    def _cannot_trade_bop(self, v: tuple[int, ...], depth: int) -> None:
        self.CannotTradeBindOnPickup = v[0] != 0

    def _bonding_with_priority(self, v: tuple[int, ...], depth: int) -> None:
        if v[1] < self._bonding_priority:
            self.Bonding = v[0]
            self._bonding_priority = v[1]

    def _item_offset_curve(self, v: tuple[int, ...], depth: int) -> None:
        # Note the priority field here is Value[3], not Value[1].
        if v[3] < self._scaling_priority:
            self.ItemLevelOffsetCurveId = v[0]
            self.ItemLevelOffsetItemLevel = v[1]
            self._scaling_priority = v[3]

    def _apply_scaling_config(self, config_id: int, *, with_item_level: bool,
                              priority: int, set_required_level: bool) -> None:
        config = self._source.scaling_config(config_id)
        if config is None:
            # Trinity's LookupEntry fails and the whole branch is skipped.
            return
        offset_curve = self._source.item_offset_curve(int(config["ItemOffsetCurveID"]))
        if offset_curve is not None:
            self.ItemLevelOffsetCurveId = int(offset_curve["CurveID"])
            self.ItemLevelOffset = int(offset_curve["Offset"])
        self.ItemLevelOffsetItemLevel = int(config["ItemLevel"]) if with_item_level else 0
        self.ItemSquishEraID = int(config["ItemSquishEraID"])
        if int(config["Flags"]) & SCALING_CONFIG_FLAG_IGNORE_SQUISH:
            self.IgnoreSquish = True
        if set_required_level and priority < self._required_level_curve_priority:
            self.RequiredLevelOverride = int(config["RequiredLevel"])
            self.RequiredLevelCurve = 0

    def _scaling_config_and_req_level(self, v: tuple[int, ...], depth: int) -> None:
        if v[1] < self._scaling_priority:
            self._apply_scaling_config(v[0], with_item_level=True, priority=v[1],
                                       set_required_level=True)

    def _item_bonus_list(self, v: tuple[int, ...], depth: int) -> None:
        self.add_bonus_list(v[0], depth=depth + 1)

    def _scaling_config(self, v: tuple[int, ...], depth: int) -> None:
        if v[1] < self._scaling_priority:
            self._apply_scaling_config(v[0], with_item_level=False, priority=v[1],
                                       set_required_level=False)


_HANDLERS = {
    BONUS_ITEM_LEVEL: BonusData._item_level,
    BONUS_STAT: BonusData._stat,
    BONUS_QUALITY: BonusData._quality,
    BONUS_SUFFIX: BonusData._suffix,
    BONUS_SOCKET: BonusData._socket,
    BONUS_APPEARANCE: BonusData._appearance,
    BONUS_REQUIRED_LEVEL: BonusData._required_level,
    BONUS_REPAIR_COST_MULTIPLIER: BonusData._repair_cost,
    BONUS_SCALING_STAT_DISTRIBUTION: BonusData._scaling_stat_distribution,
    BONUS_SCALING_STAT_DISTRIBUTION_FIXED: BonusData._scaling_stat_distribution_fixed,
    BONUS_DISENCHANT_LOOT_ID: BonusData._disenchant_loot,
    BONUS_BONDING: BonusData._bonding,
    BONUS_RELIC_TYPE: BonusData._relic_type,
    BONUS_OVERRIDE_REQUIRED_LEVEL: BonusData._override_required_level,
    BONUS_AZERITE_TIER_UNLOCK_SET: BonusData._azerite_tier_unlock_set,
    BONUS_OVERRIDE_CAN_DISENCHANT: BonusData._can_disenchant,
    BONUS_OVERRIDE_CAN_SCRAP: BonusData._can_scrap,
    BONUS_ITEM_EFFECT_ID: BonusData._item_effect_id,
    BONUS_REQUIRED_LEVEL_CURVE: BonusData._required_level_curve,
    BONUS_ITEM_LIMIT_CATEGORY: BonusData._item_limit_category,
    BONUS_PVP_ITEM_LEVEL_INCREMENT: BonusData._pvp_item_level_increment,
    BONUS_OVERRIDE_CAN_SALVAGE: BonusData._can_salvage,
    BONUS_OVERRIDE_CAN_RECRAFT: BonusData._can_recraft,
    BONUS_ITEM_LEVEL_BASE: BonusData._item_level_base,
    BONUS_PVP_ITEM_LEVEL_BASE: BonusData._pvp_item_level_base,
    BONUS_OVERRIDE_CANNOT_TRADE_BOP: BonusData._cannot_trade_bop,
    BONUS_BONDING_WITH_PRIORITY: BonusData._bonding_with_priority,
    BONUS_ITEM_OFFSET_CURVE: BonusData._item_offset_curve,
    BONUS_SCALING_CONFIG_AND_REQ_LEVEL: BonusData._scaling_config_and_req_level,
    BONUS_ITEM_BONUS_LIST: BonusData._item_bonus_list,
    BONUS_SCALING_CONFIG: BonusData._scaling_config,
}

#: Every ``ItemBonusType`` this package implements.  Anything else is recorded
#: as unhandled rather than silently ignored.
HANDLED_BONUS_TYPES = frozenset(_HANDLERS)
