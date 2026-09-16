"""Item effects: the ``ItemEffect`` roots an item contributes.

Mirrors:
* ``ObjectMgr::LoadItemTemplates`` -- ``ItemXItemEffect`` -> ``ItemEffect``
  assembly (see :mod:`gearing.items`);
* ``Player::ApplyItemEquipSpell`` / ``Player::ApplyEquipSpell`` -- which effects
  become auras on equip, and the ``ITEM_FLAG_LEGACY`` and
  ``ChrSpecializationID`` gates;
* ``Player::CastItemUseSpell`` and ``Player::CastItemCombatSpell`` -- the on-use
  and item-proc roots, which this package deliberately does **not** execute.

Acquisition is separated from executability throughout.  Reaching a SpellID
through gear says nothing about whether that spell will do anything.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from .enums import ITEM_SPELLTRIGGER_NAMES

TRIGGER_ON_USE = 0
TRIGGER_ON_EQUIP = 1
TRIGGER_ON_PROC = 2
TRIGGER_SUMMONED_BY_SPELL = 3
TRIGGER_ON_DEATH = 4
TRIGGER_ON_PICKUP = 5
TRIGGER_ON_LEARN = 6
TRIGGER_ON_LOOTED = 7
TRIGGER_TEACH_MOUNT = 8
TRIGGER_ON_PICKUP_FORCED = 9
TRIGGER_ON_LOOTED_FORCED = 10

#: Triggers that matter to an *equipped* item.  Everything else fires on
#: acquisition or is a tradeskill/teaching artefact.
COMBAT_RELEVANT_TRIGGERS = frozenset({
    TRIGGER_ON_USE, TRIGGER_ON_EQUIP, TRIGGER_ON_PROC})

#: What the direct consumer does with each trigger, for the acquisition graph.
TRIGGER_ROUTE = {
    TRIGGER_ON_USE: "on-use root: Player::CastItemUseSpell (needs item runtime policy)",
    TRIGGER_ON_EQUIP: "equip aura: Player::ApplyItemEquipSpell -> CastSpell(this, spell, item)",
    TRIGGER_ON_PROC: "item proc root: Player::CastItemCombatSpell (legacy path)",
    TRIGGER_SUMMONED_BY_SPELL: "not an equipped-item route",
    TRIGGER_ON_DEATH: "not an equipped-item route",
    TRIGGER_ON_PICKUP: "acquisition-time only",
    TRIGGER_ON_LEARN: "acquisition-time only",
    TRIGGER_ON_LOOTED: "acquisition-time only",
    TRIGGER_TEACH_MOUNT: "acquisition-time only",
    TRIGGER_ON_PICKUP_FORCED: "acquisition-time only",
    TRIGGER_ON_LOOTED_FORCED: "acquisition-time only",
}


@dataclass(frozen=True)
class ItemEffectRoot:
    item_effect_id: int
    spell_id: int
    trigger_type: int
    trigger_name: str
    route: str
    legacy_slot_index: int
    charges: int
    cooldown_ms: int
    category_cooldown_ms: int
    spell_category_id: int
    chr_specialization_id: int
    player_condition_id: int
    combat_relevant: bool

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def classify_effect(row: dict[str, Any]) -> ItemEffectRoot:
    """Turn one ``ItemEffect`` row into a classified acquisition root."""
    trigger = int(row["TriggerType"])
    return ItemEffectRoot(
        item_effect_id=int(row["ID"]),
        spell_id=int(row["SpellID"]),
        trigger_type=trigger,
        trigger_name=ITEM_SPELLTRIGGER_NAMES.get(trigger, str(trigger)),
        route=TRIGGER_ROUTE.get(trigger, "unknown trigger type in this snapshot"),
        legacy_slot_index=int(row["LegacySlotIndex"]),
        charges=int(row["Charges"]),
        cooldown_ms=int(row["CoolDownMSec"]),
        category_cooldown_ms=int(row["CategoryCoolDownMSec"]),
        spell_category_id=int(row["SpellCategoryID"]),
        chr_specialization_id=int(row["ChrSpecializationID"]),
        player_condition_id=int(row["PlayerConditionID"]),
        combat_relevant=trigger in COMBAT_RELEVANT_TRIGGERS,
    )


def classify_effects(rows: Iterable[dict[str, Any]]) -> list[ItemEffectRoot]:
    return [classify_effect(row) for row in rows]
