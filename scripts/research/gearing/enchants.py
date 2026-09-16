"""Enchantments and gems.

Mirrors:
* ``Player::ApplyEnchantment`` (``src/server/game/Entities/Player/Player.cpp``)
  -- the dispatch over ``SpellItemEnchantment::Effect[0..2]`` and the
  ``ScalingClass`` amount derivation;
* ``GetSpellScalingColumnForClass`` (``GameTables.h``);
* the canonical half of ``Item::SetGem`` (``Item.cpp``) -- gem item ->
  ``ItemSparse.Gem_properties`` -> ``GemProperties.Enchant_ID`` ->
  ``SpellItemEnchantment``, plus the two gem-only enchant effect types that
  contribute item level to the *host* item.

Which enchant sits in which slot of a concrete item is item-instance state and
is not derivable from source data; this module only describes what an
enchantment id means once chosen.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from . import SourceError, UnsupportedSource
from .curves import Curves
from .enums import (
    ENCHANT_FLAG_SCALE_AS_A_GEM,
    ENCHANT_TYPE_NAMES,
    MAX_ITEM_ENCHANTMENT_EFFECTS,
    MOD_NAMES,
    MOD_TO_RATINGS,
    SOCKET_COLOR_TO_GEM_TYPE_MASK,
)
from .tables import Tables

# Enchant effect type ids (DBCEnums.h :: enum ItemEnchantmentType)
ENCHANT_NONE = 0
ENCHANT_COMBAT_SPELL = 1
ENCHANT_DAMAGE = 2
ENCHANT_EQUIP_SPELL = 3
ENCHANT_RESISTANCE = 4
ENCHANT_STAT = 5
ENCHANT_TOTEM = 6
ENCHANT_USE_SPELL = 7
ENCHANT_PRISMATIC_SOCKET = 8
ENCHANT_BONUS_LIST_ID = 11
ENCHANT_BONUS_LIST_CURVE = 12

#: Effect types whose ``EffectArg`` is a SpellID reached through gear.
ENCHANT_SPELL_EFFECT_TYPES = frozenset({
    ENCHANT_COMBAT_SPELL, ENCHANT_EQUIP_SPELL, ENCHANT_USE_SPELL})

#: How each effect type reaches the rest of the game.  Keep in step with the
#: switch in ``Player::ApplyEnchantment``.
ENCHANT_ACQUISITION = {
    ENCHANT_NONE: "inert",
    ENCHANT_COMBAT_SPELL: "proc root: handled in Player::CastItemCombatSpell",
    ENCHANT_DAMAGE: "weapon damage-done modifier (UpdateDamageDoneMods)",
    ENCHANT_EQUIP_SPELL: "equip aura: CastSpell(this, spell, item)",
    ENCHANT_RESISTANCE: "direct resistance addition (UNIT_MOD_RESISTANCE_*)",
    ENCHANT_STAT: "direct stat/rating addition",
    ENCHANT_TOTEM: "weapon damage-done modifier (UpdateDamageDoneMods)",
    ENCHANT_USE_SPELL: "on-use root: handled in Player::CastItemUseSpell",
    ENCHANT_PRISMATIC_SOCKET: "item mutation: adds a socket, no stats",
    9: "artifact power rank by type (legacy, no current consumer path)",
    10: "artifact power rank by id (legacy, no current consumer path)",
    ENCHANT_BONUS_LIST_ID: "gem only: adds ITEM_BONUS_ITEM_LEVEL to the host item",
    ENCHANT_BONUS_LIST_CURVE: "gem only: curve-selected item-level delta for the host item",
    13: "artifact power rank picker (legacy, no current consumer path)",
}

#: SpellScaling.txt column order after the Level key.  Matches
#: ``struct GtSpellScalingEntry`` (GameTables.h) field for field.
SPELL_SCALING_COLUMNS = (
    "Rogue", "Druid", "Hunter", "Mage", "Paladin", "Priest", "Shaman",
    "Warlock", "Warrior", "DeathKnight", "Monk", "DemonHunter", "Evoker",
    "Adventurer", "Traveler", "Item", "Consumable", "Gem1", "Gem2", "Gem3",
    "Health", "DamageReplaceStat", "DamageSecondary", "ManaConsumable",
)
_SPELL_SCALING_COLUMN_INDEX = {n: i for i, n in enumerate(SPELL_SCALING_COLUMNS)}

#: Mirrors: ``GetSpellScalingColumnForClass``.  Positive keys are Classes enum
#: ids; negative keys select the non-class columns (note -1 and -7 both map to
#: ``Item``).
SCALING_CLASS_TO_COLUMN = {
    1: "Warrior", 2: "Paladin", 3: "Hunter", 4: "Rogue", 5: "Priest",
    6: "DeathKnight", 7: "Shaman", 8: "Mage", 9: "Warlock", 10: "Monk",
    11: "Druid", 12: "DemonHunter", 13: "Evoker", 14: "Adventurer",
    15: "Traveler",
    -1: "Item", -7: "Item", -2: "Consumable", -3: "Gem1", -4: "Gem2",
    -5: "Gem3", -6: "Health", -8: "DamageReplaceStat",
    -9: "DamageSecondary", -10: "ManaConsumable",
}

#: The item-level bonus curve ``Item::SetGem`` hardcodes for
#: ``ITEM_ENCHANTMENT_TYPE_BONUS_LIST_CURVE``.  Consumer policy, not data.
CURVE_ID_ARTIFACT_RELIC_ITEM_LEVEL_BONUS = 1718


def spell_scaling_column(scaling_class: int) -> int | None:
    name = SCALING_CLASS_TO_COLUMN.get(scaling_class)
    return None if name is None else _SPELL_SCALING_COLUMN_INDEX[name]


@dataclass
class EnchantEffect:
    slot: int
    type: int
    type_name: str
    effect_arg: int
    effect_points_min: int
    effect_scaling_points: float
    acquisition: str
    stat_type: int | None = None
    stat_name: str | None = None
    ratings: tuple[str, ...] = ()
    spell_id: int | None = None
    bonus_list_id: int | None = None
    resolved_amount: int | None = None
    resolution: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        out = {k: v for k, v in self.__dict__.items() if v is not None}
        out["ratings"] = list(self.ratings)
        return out


@dataclass
class Enchant:
    enchant_id: int
    name: str
    scaling_class: int
    scaling_class_restricted: int
    min_level: int
    max_level: int
    required_skill_id: int
    required_skill_rank: int
    condition_id: int
    flags: int
    item_level: int
    min_item_level: int
    max_item_level: int
    effects: list[EnchantEffect]

    @property
    def scales_as_a_gem(self) -> bool:
        return bool(self.flags & ENCHANT_FLAG_SCALE_AS_A_GEM)

    @property
    def spell_roots(self) -> list[tuple[int, str]]:
        """``(SpellID, acquisition route)`` for every spell this enchant grants."""
        return [(e.spell_id, e.type_name) for e in self.effects
                if e.spell_id and e.type in ENCHANT_SPELL_EFFECT_TYPES]

    def to_dict(self) -> dict[str, Any]:
        out = {k: v for k, v in self.__dict__.items() if k != "effects"}
        out["effects"] = [e.to_dict() for e in self.effects]
        out["spell_roots"] = [list(x) for x in self.spell_roots]
        return out


class EnchantEngine:
    def __init__(self, tables: Tables, curves: Curves) -> None:
        self.tables = tables
        self.curves = curves
        self._enchant = tables("SpellItemEnchantment").by("ID")
        self._gem_properties = tables("GemProperties").by("ID")
        self._gt_spell_scaling = tables.gametable("SpellScaling")

    # -- description -----------------------------------------------------
    def describe(self, enchant_id: int) -> Enchant:
        row = self._enchant.get(enchant_id)
        if row is None:
            raise SourceError(
                f"unknown SpellItemEnchantment {enchant_id} in this snapshot")
        effects: list[EnchantEffect] = []
        for slot in range(MAX_ITEM_ENCHANTMENT_EFFECTS):
            effect_type = int(row[f"Effect_{slot}"])
            if effect_type == ENCHANT_NONE:
                continue
            arg = int(row[f"EffectArg_{slot}"])
            effect = EnchantEffect(
                slot=slot,
                type=effect_type,
                type_name=ENCHANT_TYPE_NAMES.get(effect_type, str(effect_type)),
                effect_arg=arg,
                effect_points_min=int(row[f"EffectPointsMin_{slot}"]),
                effect_scaling_points=float(row[f"EffectScalingPoints_{slot}"]),
                acquisition=ENCHANT_ACQUISITION.get(
                    effect_type, "no direct consumer in this TrinityCore checkout"),
            )
            if effect_type == ENCHANT_STAT:
                effect.stat_type = arg
                effect.stat_name = MOD_NAMES.get(arg, str(arg))
                effect.ratings = tuple(MOD_TO_RATINGS.get(arg, ()))
            elif effect_type in ENCHANT_SPELL_EFFECT_TYPES:
                effect.spell_id = arg
            elif effect_type == ENCHANT_BONUS_LIST_ID:
                effect.bonus_list_id = arg
            effects.append(effect)
        return Enchant(
            enchant_id=enchant_id,
            name=str(row["Name_lang"]),
            scaling_class=int(row["ScalingClass"]),
            scaling_class_restricted=int(row["ScalingClassRestricted"]),
            min_level=int(row["MinLevel"]),
            max_level=int(row["MaxLevel"]),
            required_skill_id=int(row["RequiredSkillID"]),
            required_skill_rank=int(row["RequiredSkillRank"]),
            condition_id=int(row["Condition_ID"]),
            flags=int(row["Flags"]),
            item_level=int(row["ItemLevel"]),
            min_item_level=int(row["ItemLevelMin"]),
            max_item_level=int(row["ItemLevelMax"]),
            effects=effects,
        )

    # -- amounts ---------------------------------------------------------
    def stat_amount(self, enchant: Enchant, slot: int, player_level: int,
                    *, restricted: bool = False) -> tuple[int, dict[str, Any]]:
        """Mirrors: the ScalingClass branch of ``Player::ApplyEnchantment``.

        ``restricted`` reproduces the ``(MinItemLevel || MaxItemLevel) &&
        ScalingClassRestricted`` swap, which only happens when the player is
        under a scaled item-level context (PvP or timewalking).

        The scaling level is clamped to ``[minLevel, maxLevel]`` where
        ``minLevel`` is 1 for a gem-scaled enchant and 60 otherwise, and
        ``maxLevel`` defaults to ``SpellScaling`` row count - 1.  The product is
        truncated by a ``uint32`` cast, then floored at 1.
        """
        effect = next((e for e in enchant.effects if e.slot == slot), None)
        if effect is None:
            raise SourceError(
                f"SpellItemEnchantment {enchant.enchant_id} has no effect in slot {slot}")
        amount = effect.effect_points_min
        detail: dict[str, Any] = {"effect_points_min": amount, "scaled": False}

        scaling_class = enchant.scaling_class
        if restricted and enchant.scaling_class_restricted:
            scaling_class = enchant.scaling_class_restricted
            detail["scaling_class_restricted_applied"] = True

        if scaling_class:
            min_level = 1 if enchant.scales_as_a_gem else 60
            max_level = enchant.max_level or (self._gt_spell_scaling.row_count() - 1)
            scaling_level = player_level
            if min_level > player_level:
                scaling_level = min_level
            elif max_level < player_level:
                scaling_level = max_level
            column = spell_scaling_column(scaling_class)
            if column is None:
                raise UnsupportedSource(
                    f"SpellItemEnchantment {enchant.enchant_id} uses ScalingClass "
                    f"{scaling_class}, which GetSpellScalingColumnForClass does "
                    f"not map to a SpellScaling column")
            value = self._gt_spell_scaling.column(scaling_level, column)
            if value is None:
                raise SourceError(
                    f"SpellScaling.txt has no row for level {scaling_level}")
            amount = int(effect.effect_scaling_points * value)
            detail.update({
                "scaled": True,
                "scaling_class": scaling_class,
                "spell_scaling_column": SPELL_SCALING_COLUMNS[column],
                "min_level": min_level,
                "max_level": max_level,
                "scaling_level": scaling_level,
                "spell_scaling_value": value,
                "effect_scaling_points": effect.effect_scaling_points,
                "rounding": "uint32 truncation",
            })

        # Mirrors: enchant_amount = std::max(enchant_amount, 1u)
        amount = max(amount, 1)
        detail["final_amount"] = amount
        return amount, detail

    def resolve(self, enchant_id: int, player_level: int,
                *, restricted: bool = False) -> Enchant:
        """Describe an enchant and fill in resolved amounts for stat/resist slots."""
        enchant = self.describe(enchant_id)
        for effect in enchant.effects:
            if effect.type in (ENCHANT_STAT, ENCHANT_RESISTANCE):
                amount, detail = self.stat_amount(
                    enchant, effect.slot, player_level, restricted=restricted)
                effect.resolved_amount = amount
                effect.resolution = detail
        return enchant

    # -- gems -------------------------------------------------------------
    def describe_gem(self, gem_proto, player_level: int) -> dict[str, Any]:
        """Mirrors: the canonical half of ``Item::SetGem``.

        Returns the gem's socket-colour mask and its enchantment payload.  The
        *instance* half of ``SetGem`` (which socket it went into, and the
        gem's own bonus lists) is item state and is supplied by the caller.
        """
        gem_props = self._gem_properties.get(gem_proto.gem_properties)
        if gem_props is None:
            raise SourceError(
                f"item {gem_proto.item_id} has Gem_properties="
                f"{gem_proto.gem_properties} with no GemProperties row; "
                f"Item::SetGem would contribute nothing")
        enchant_id = int(gem_props["Enchant_ID"])
        result: dict[str, Any] = {
            "gem_item_id": gem_proto.item_id,
            "name": gem_proto.name,
            "gem_item_level": gem_proto.base_item_level,
            "gem_properties_id": gem_proto.gem_properties,
            "gem_type_mask": int(gem_props["Type"]),
            "enchant_id": enchant_id,
        }
        if enchant_id:
            result["enchant"] = self.resolve(enchant_id, player_level).to_dict()
        return result

    @staticmethod
    def gem_fits_socket(gem_type_mask: int, socket_color: int) -> bool:
        """Mirrors: ``Item::GemsFitSockets`` -- ``GemColor & SocketColorToGemTypeMask[color]``."""
        if socket_color <= 0 or socket_color >= len(SOCKET_COLOR_TO_GEM_TYPE_MASK):
            raise UnsupportedSource(
                f"socket colour {socket_color} is outside "
                f"SocketColorToGemTypeMask[{len(SOCKET_COLOR_TO_GEM_TYPE_MASK)}]")
        return bool(gem_type_mask & SOCKET_COLOR_TO_GEM_TYPE_MASK[socket_color])

    def gem_item_level_bonus(self, enchant: Enchant, gem_base_item_level: int,
                             level_delta_to_bonus_list,
                             bonuses_for_list) -> tuple[int, list[dict[str, Any]]]:
        """Mirrors: the ``BONUS_LIST_ID`` / ``BONUS_LIST_CURVE`` loop in ``Item::SetGem``.

        Returns the item-level delta the gem contributes to its *host* item and
        a provenance trail.  Only these two enchant effect types participate.
        """
        total = 0
        trail: list[dict[str, Any]] = []
        for effect in enchant.effects:
            if effect.type == ENCHANT_BONUS_LIST_ID:
                delta = sum(int(b["Value_0"]) for b in bonuses_for_list(effect.effect_arg)
                            if int(b["Type"]) == 1)
                total += delta
                trail.append({"effect_slot": effect.slot, "type": "BonusListId",
                              "bonus_list_id": effect.effect_arg,
                              "item_level_delta": delta})
            elif effect.type == ENCHANT_BONUS_LIST_CURVE:
                x = gem_base_item_level
                y = self.curves.value_at(
                    CURVE_ID_ARTIFACT_RELIC_ITEM_LEVEL_BONUS, x,
                    consumer="Item::SetGem/BonusListCurve")
                bonus_list_id = level_delta_to_bonus_list(int(y))
                delta = sum(int(b["Value_0"]) for b in bonuses_for_list(bonus_list_id)
                            if int(b["Type"]) == 1) if bonus_list_id else 0
                total += delta
                trail.append({"effect_slot": effect.slot, "type": "BonusListCurve",
                              "curve_id": CURVE_ID_ARTIFACT_RELIC_ITEM_LEVEL_BONUS,
                              "x": x, "raw_y": y,
                              "bonus_list_id": bonus_list_id,
                              "item_level_delta": delta})
        return total, trail
