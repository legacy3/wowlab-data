"""Item identity: ``Item.db2`` + ``ItemSparse.db2`` + assembled ``ItemEffect`` list.

Mirrors: ``struct ItemTemplate`` (``src/server/game/Entities/Item/ItemTemplate.h``)
and the template assembly in ``ObjectMgr::LoadItemTemplates``
(``src/server/game/Globals/ObjectMgr.cpp``).

Trinity splits an item template across two DB2 rows: ``BasicData`` (Item.db2)
carries identity and class/subclass, ``ExtendedData`` (ItemSparse.db2) carries
the gameplay payload.  A few accessors have different names in Trinity than in
the client column headers; those are noted inline.
"""

from __future__ import annotations

from bisect import bisect_left
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from . import SourceError
from .enums import (
    ITEM_CLASS_ARMOR,
    ITEM_CLASS_WEAPON,
    MAX_ITEM_PROTO_SOCKETS,
    MAX_ITEM_PROTO_STATS,
    FLAG2_CASTER_WEAPON,
    FLAG3_IGNORE_ITEM_LEVEL_CAP_IN_PVP,
    FLAG4_CC_TRINKET,
    FLAG_LEGACY,
)
from .tables import Tables


@dataclass
class ItemTemplate:
    """One assembled item template."""

    item_id: int
    basic: dict[str, Any]
    sparse: dict[str, Any]
    effects: list[dict[str, Any]] = field(default_factory=list)

    # -- Item.db2 --------------------------------------------------------
    @property
    def class_id(self) -> int:
        return int(self.basic["ClassID"])

    @property
    def subclass_id(self) -> int:
        return int(self.basic["SubclassID"])

    # -- ItemSparse.db2 --------------------------------------------------
    @property
    def name(self) -> str:
        return str(self.sparse.get("Display_lang", "") or "")

    @property
    def quality(self) -> int:
        return int(self.sparse["OverallQualityID"])

    @property
    def inventory_type(self) -> int:
        return int(self.sparse["InventoryType"])

    @property
    def base_item_level(self) -> int:
        return int(self.sparse["ItemLevel"])

    @property
    def base_required_level(self) -> int:
        return int(self.sparse["RequiredLevel"])

    @property
    def delay(self) -> int:
        """Trinity's ``GetDelay()`` -> ``ExtendedData->ItemDelay``."""
        return int(self.sparse["ItemDelay"])

    @property
    def dmg_variance(self) -> float:
        return float(self.sparse["DmgVariance"])

    @property
    def item_set(self) -> int:
        return int(self.sparse["ItemSet"])

    @property
    def gem_properties(self) -> int:
        """Trinity's ``GetGemProperties()`` -> column ``Gem_properties``."""
        return int(self.sparse["Gem_properties"])

    @property
    def socket_bonus(self) -> int:
        """Trinity's ``GetSocketBonus()`` -> column ``Socket_match_enchantment_ID``."""
        return int(self.sparse["Socket_match_enchantment_ID"])

    @property
    def expansion_id(self) -> int:
        return int(self.sparse["ExpansionID"])

    @property
    def content_tuning_id(self) -> int:
        return int(self.sparse["ContentTuningID"])

    @property
    def player_level_to_item_level_curve_id(self) -> int:
        return int(self.sparse["PlayerLevelToItemLevelCurveID"])

    @property
    def item_level_offset_curve_id(self) -> int:
        return int(self.sparse["ItemLevelOffsetCurveID"])

    @property
    def item_level_offset_item_level(self) -> int:
        return int(self.sparse["ItemLevelOffsetItemLevel"])

    @property
    def item_squish_era_id(self) -> int:
        return int(self.sparse["ItemSquishEraID"])

    @property
    def limit_category(self) -> int:
        return int(self.sparse["LimitCategory"])

    @property
    def bonding(self) -> int:
        return int(self.sparse["Bonding"])

    @property
    def is_equippable(self) -> bool:
        return self.inventory_type != 0

    def flags(self, index: int) -> int:
        return int(self.sparse[f"Flags_{index}"])

    def has_flag(self, flag: tuple[int, int]) -> bool:
        index, mask = flag
        return (self.flags(index) & mask) != 0

    @property
    def is_caster_weapon(self) -> bool:
        return self.has_flag(FLAG2_CASTER_WEAPON)

    @property
    def is_cc_trinket(self) -> bool:
        return self.has_flag(FLAG4_CC_TRINKET)

    @property
    def ignores_pvp_item_level_cap(self) -> bool:
        return self.has_flag(FLAG3_IGNORE_ITEM_LEVEL_CAP_IN_PVP)

    @property
    def is_legacy(self) -> bool:
        """``ITEM_FLAG_LEGACY`` -- ``ApplyItemEquipSpell`` skips every effect."""
        return self.has_flag(FLAG_LEGACY)

    @property
    def is_weapon(self) -> bool:
        return self.class_id == ITEM_CLASS_WEAPON

    @property
    def is_armor(self) -> bool:
        return self.class_id == ITEM_CLASS_ARMOR

    def socket_color(self, index: int) -> int:
        return int(self.sparse[f"SocketType_{index}"])

    def stat_type(self, index: int) -> int:
        return int(self.sparse[f"StatModifier_bonusStat_{index}"])

    def stat_percent_editor(self, index: int) -> int:
        return int(self.sparse[f"StatPercentEditor_{index}"])

    def stat_percentage_of_socket(self, index: int) -> float:
        return float(self.sparse[f"StatPercentageOfSocket_{index}"])

    @property
    def has_any_stat(self) -> bool:
        return any(self.stat_type(i) != -1 for i in range(MAX_ITEM_PROTO_STATS))

    @property
    def has_any_socket(self) -> bool:
        return any(self.socket_color(i) for i in range(MAX_ITEM_PROTO_SOCKETS))


class ItemStore:
    """Assembles :class:`ItemTemplate` objects the way ``ObjectMgr`` does."""

    def __init__(self, tables: Tables) -> None:
        self.tables = tables
        self._item = tables("Item").by("ID")
        self._sparse = tables("ItemSparse").by("ID")
        self._effect = tables("ItemEffect").by("ID")
        self._x_effect = tables("ItemXItemEffect")
        self._effects_by_item: dict[int, list[dict[str, Any]]] | None = None
        self._cache: dict[int, ItemTemplate] = {}

    def _effect_index(self) -> dict[int, list[dict[str, Any]]]:
        if self._effects_by_item is None:
            index: dict[int, list[dict[str, Any]]] = defaultdict(list)
            for row in self._x_effect:
                effect = self._effect.get(row["ItemEffectID"])
                if effect is None:
                    # Trinity's lookup fails the same way and simply skips.
                    continue
                bucket = index[row["ItemID"]]
                # Mirrors: ObjectMgr::LoadItemTemplates inserts at
                # lower_bound(LegacySlotIndex).  For equal slot indices the
                # later cross-table row therefore lands *before* the earlier
                # one; that ordering is reproduced rather than normalised.
                keys = [e["LegacySlotIndex"] for e in bucket]
                bucket.insert(bisect_left(keys, effect["LegacySlotIndex"]), effect)
            self._effects_by_item = dict(index)
        return self._effects_by_item

    def exists(self, item_id: int) -> bool:
        return item_id in self._item and item_id in self._sparse

    def get(self, item_id: int) -> ItemTemplate:
        cached = self._cache.get(item_id)
        if cached is not None:
            return cached
        basic = self._item.get(item_id)
        sparse = self._sparse.get(item_id)
        if basic is None and sparse is None:
            raise SourceError(
                f"unknown ItemID {item_id}: absent from both Item.db2 and ItemSparse.db2")
        if basic is None:
            raise SourceError(
                f"ItemID {item_id} has an ItemSparse row but no Item row; "
                f"ObjectMgr::LoadItemTemplates would not build a template")
        if sparse is None:
            raise SourceError(
                f"ItemID {item_id} has an Item row but no ItemSparse row; "
                f"ObjectMgr::LoadItemTemplates would not build a template")
        template = ItemTemplate(item_id, basic, sparse,
                                list(self._effect_index().get(item_id, ())))
        self._cache[item_id] = template
        return template
