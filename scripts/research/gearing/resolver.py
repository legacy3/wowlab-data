"""The facade that ties every stage together for one item instance.

Stage order, and where each stage is mirrored from:

1. ``ItemBonusMgr::GetBonusListsForItem``      -> :mod:`gearing.tree`
2. ``BonusData::Initialize`` + ``AddBonusList``-> :mod:`gearing.bonus`
3. ``Item::GetItemLevel``                      -> :mod:`gearing.scaling`
4. ``Item::GetItemStatValue``                  -> :mod:`gearing.scaling`
5. ``Player::_ApplyItemBonuses`` multipliers   -> here
6. ``ItemTemplate::GetArmor`` / ``GetDamage``  -> :mod:`gearing.scaling`
7. ``ItemXItemEffect`` roots                   -> :mod:`gearing.effects`
8. ``ItemSparse.ItemSet`` membership           -> :mod:`gearing.sets`
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

from . import SourceError
from .bonus import AppliedBonus, BonusData
from .curves import CurveEval, Curves, round_half_away
from .effects import ItemEffectRoot, classify_effects
from .enchants import EnchantEngine
from .enums import (
    DEFAULT_PLAYER_LEVEL,
    INVTYPE_NAMES,
    ITEM_CLASS_WEAPON,
    ITEM_CONTEXT_FORCE_TO_NONE,
    ITEM_CONTEXT_NONE,
    MAX_ITEM_PROTO_SOCKETS,
    MAX_ITEM_PROTO_STATS,
    MOD_STAMINA,
    MOD_TO_RATINGS,
    PRIMARY_MODS,
    QUALITY_NAMES,
    RATING_MULTIPLIED_MODS,
    context_name,
)
from .items import ItemStore, ItemTemplate
from .ratings import RatingEngine
from .scaling import ScalingEngine, StatValue
from .sets import SetEngine
from .tables import DEFAULT_TABLES, Tables
from .tree import (
    HARDCODED_SEQUENCE_TREES,
    BonusGenerationParams,
    BonusListSelection,
    BonusTreeResolver,
)


@dataclass(frozen=True)
class Variant:
    """A source-backed context/upgrade variant of one base item."""

    label: str
    context: int = ITEM_CONTEXT_NONE
    mythic_plus_keystone_level: int | None = None
    pvp_tier: int | None = None
    extra_bonus_lists: tuple[int, ...] = ()
    origin: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "context": self.context,
            "context_name": context_name(self.context),
            "mythic_plus_keystone_level": self.mythic_plus_keystone_level,
            "pvp_tier": self.pvp_tier,
            "extra_bonus_lists": list(self.extra_bonus_lists),
            "origin": self.origin,
        }


@dataclass
class GemSlot:
    """One socketed gem on an item instance."""

    socket_index: int
    gem_item_id: int
    bonus_list_ids: tuple[int, ...] = ()


@dataclass
class ResolvedItem:
    """Everything the pipeline derives for one (item, variant, player) triple."""

    item_id: int
    name: str
    variant: Variant
    player_level: int
    base_item_level: int
    base_quality: int
    quality: int
    inventory_type: int
    class_id: int
    subclass_id: int
    required_level: int
    effective_item_level: int
    item_level_provenance: list[dict[str, Any]]
    selection: BonusListSelection
    bonus_trace: list[AppliedBonus]
    unhandled_bonuses: list[AppliedBonus]
    applied_bonus_lists: list[int]
    stats: list[StatValue]
    sockets: list[int]
    gems: list[dict[str, Any]]
    armor: int
    weapon: dict[str, Any] | None
    effects: list[ItemEffectRoot]
    enchants: list[dict[str, Any]]
    item_set_id: int
    item_set: dict[str, Any] | None
    curve_evaluations: list[CurveEval]
    warnings: list[str]

    # -- aggregate views -------------------------------------------------
    @property
    def rating_contributions(self) -> dict[str, int]:
        """``CombatRatings`` slots this single item feeds, and by how much."""
        out: dict[str, int] = {}
        for stat in self.stats:
            if not stat.final_value:
                continue
            for rating in MOD_TO_RATINGS.get(stat.stat_type, ()):
                out[rating] = out.get(rating, 0) + stat.final_value
        return out

    @property
    def primary_stats(self) -> list[tuple[str, int]]:
        return [(s.stat_name, s.final_value) for s in self.stats
                if s.final_value and s.stat_type in PRIMARY_MODS]

    @property
    def stamina(self) -> int:
        return sum(s.final_value for s in self.stats if s.stat_type == MOD_STAMINA)

    @property
    def spell_roots(self) -> list[dict[str, Any]]:
        """Every SpellID this item makes reachable, with its acquisition route."""
        roots: list[dict[str, Any]] = []
        for effect in self.effects:
            if effect.spell_id:
                roots.append({"spell_id": effect.spell_id, "via": "ItemEffect",
                              "route": effect.route,
                              "trigger": effect.trigger_name,
                              "item_effect_id": effect.item_effect_id})
        for enchant in self.enchants:
            for spell_id, kind in enchant.get("spell_roots", ()):
                roots.append({"spell_id": spell_id, "via": "SpellItemEnchantment",
                              "route": kind,
                              "enchant_id": enchant["enchant_id"]})
        for gem in self.gems:
            enchant = gem.get("enchant")
            if not enchant:
                continue
            for spell_id, kind in enchant.get("spell_roots", ()):
                roots.append({"spell_id": spell_id, "via": "Gem",
                              "route": kind,
                              "gem_item_id": gem["gem_item_id"],
                              "enchant_id": enchant["enchant_id"]})
        return roots

    def to_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "name": self.name,
            "variant": self.variant.to_dict(),
            "player_level": self.player_level,
            "base_item_level": self.base_item_level,
            "base_quality": self.base_quality,
            "base_quality_name": QUALITY_NAMES.get(self.base_quality),
            "quality": self.quality,
            "quality_name": QUALITY_NAMES.get(self.quality),
            "inventory_type": self.inventory_type,
            "inventory_type_name": INVTYPE_NAMES.get(self.inventory_type),
            "class_id": self.class_id,
            "subclass_id": self.subclass_id,
            "required_level": self.required_level,
            "effective_item_level": self.effective_item_level,
            "item_level_provenance": self.item_level_provenance,
            "bonus_selection": self.selection.to_dict(),
            "applied_bonus_lists": self.applied_bonus_lists,
            "bonus_trace": [b.to_dict() for b in self.bonus_trace],
            "unhandled_bonuses": [b.to_dict() for b in self.unhandled_bonuses],
            "stats": [s.to_dict() for s in self.stats],
            "rating_contributions": self.rating_contributions,
            "sockets": self.sockets,
            "gems": self.gems,
            "armor": self.armor,
            "weapon": self.weapon,
            "effects": [e.to_dict() for e in self.effects],
            "enchants": self.enchants,
            "item_set_id": self.item_set_id,
            "item_set": self.item_set,
            "spell_roots": self.spell_roots,
            "curves_used": [c.to_dict() for c in self.curve_evaluations],
            "warnings": self.warnings,
        }


class GearResolver:
    """One loaded snapshot, reusable across many resolutions."""

    def __init__(self, tables: Tables | Path | str = DEFAULT_TABLES) -> None:
        self.tables = tables if isinstance(tables, Tables) else Tables(tables)
        t = self.tables
        self.items = ItemStore(t)
        self.curves = Curves(t)
        self.trees = BonusTreeResolver(t, self.curves)
        self.scaling = ScalingEngine(t, self.curves)
        self.ratings = RatingEngine(t, self.curves)
        self.enchants = EnchantEngine(t, self.curves)
        self.sets = SetEngine(t)
        self._item_effect = t("ItemEffect").by("ID")
        self._scaling_config = t("ItemScalingConfig").by("ID")
        self._item_offset_curve = t("ItemOffsetCurve").by("ID")
        self._bonus_list_group = t("ItemBonusListGroup").by("ID")

    # -- BonusSource protocol -------------------------------------------
    def bonuses_for_list(self, bonus_list_id: int) -> list[dict[str, Any]]:
        """Mirrors: ``ItemBonusMgr::GetItemBonuses``.

        Trinity keeps ItemBonus rows in DB2 load order (ascending ID); the CSV
        row order is the same, so no re-sort is applied.
        """
        return self.trees.item_bonus_by_list.get(bonus_list_id, [])

    def item_effect(self, effect_id: int) -> dict[str, Any] | None:
        return self._item_effect.get(effect_id)

    def scaling_config(self, config_id: int) -> dict[str, Any] | None:
        return self._scaling_config.get(config_id)

    def item_offset_curve(self, curve_id: int) -> dict[str, Any] | None:
        return self._item_offset_curve.get(curve_id)

    # -- resolution ------------------------------------------------------
    def resolve(
        self,
        item_id: int,
        variant: Variant | None = None,
        *,
        player_level: int = DEFAULT_PLAYER_LEVEL,
        extra_bonus_lists: Iterable[int] = (),
        gems: Sequence[GemSlot] = (),
        enchant_ids: Sequence[int] = (),
        fixed_level: int = 0,
        min_item_level: int = 0,
        min_item_level_cutoff: int = 0,
        max_item_level: int = 0,
        pvp_bonus: bool = False,
        current_build_patch: int | None = None,
        auto_bonus_lists: bool = True,
    ) -> ResolvedItem:
        proto = self.items.get(item_id)
        variant = variant or Variant(label="baseline (no context)",
                                     origin="caller default")
        warnings: list[str] = []
        curve_mark = self.curves.mark()

        params = BonusGenerationParams(
            context=variant.context,
            mythic_plus_keystone_level=variant.mythic_plus_keystone_level,
            pvp_tier=variant.pvp_tier,
        )
        if auto_bonus_lists:
            selection = self.trees.bonus_lists_for_item(proto, params)
        else:
            selection = BonusListSelection([], 0, [], [])

        bonus = BonusData(proto, self)
        seen: set[int] = set()
        requested = (list(selection.bonus_list_ids)
                     + list(variant.extra_bonus_lists)
                     + list(extra_bonus_lists))
        for bonus_list_id in requested:
            if bonus_list_id in seen:
                warnings.append(
                    f"bonus list {bonus_list_id} applied more than once; "
                    f"Trinity applies its rows twice, doubling additive types")
            seen.add(bonus_list_id)
            if not self.bonuses_for_list(bonus_list_id):
                warnings.append(
                    f"bonus list {bonus_list_id} has no ItemBonus rows in this snapshot")
            bonus.add_bonus_list(bonus_list_id)

        gem_details = self._apply_gems(bonus, gems, player_level, warnings)

        provenance: list[dict[str, Any]] = []
        item_level = self.scaling.effective_item_level(
            proto, bonus, player_level=player_level, fixed_level=fixed_level,
            min_item_level=min_item_level,
            min_item_level_cutoff=min_item_level_cutoff,
            max_item_level=max_item_level, pvp_bonus=pvp_bonus,
            current_build_patch=current_build_patch, provenance=provenance)

        for step in provenance:
            if step.get("out_of_domain"):
                warnings.append(
                    f"ItemLevelOffsetCurve {step['curve_id']} was evaluated at "
                    f"x={step['x']} which is {step['out_of_domain']} of its "
                    f"domain {step['curve_x_range']}; Item::GetItemLevel always "
                    f"uses BonusData::ItemLevelOffsetItemLevel, which "
                    f"ITEM_BONUS_SCALING_CONFIG forces to 0")

        stats = self._resolve_stats(proto, bonus, item_level)
        armor = self.scaling.armor(proto, bonus.Quality, item_level)
        weapon = self._resolve_weapon(proto, bonus, item_level)

        enchant_details = []
        for enchant_id in enchant_ids:
            enchant_details.append(
                self.enchants.resolve(enchant_id, player_level).to_dict())

        item_set = None
        if proto.item_set:
            if self.sets.has(proto.item_set):
                item_set = self.sets.get(proto.item_set).to_dict()
            else:
                warnings.append(
                    f"ItemSparse.ItemSet {proto.item_set} has no ItemSet row; "
                    f"AddItemsSetItem logs an error and skips the item")

        if proto.is_legacy:
            warnings.append(
                "ITEM_FLAG_LEGACY is set: ApplyItemEquipSpell skips every effect")
        if bonus.unhandled:
            kinds = sorted({b.type for b in bonus.unhandled})
            warnings.append(
                f"ItemBonus types {kinds} have no branch in BonusData::AddBonus; "
                f"recorded as unhandled")

        return ResolvedItem(
            item_id=item_id,
            name=proto.name,
            variant=variant,
            player_level=player_level,
            base_item_level=proto.base_item_level,
            base_quality=proto.quality,
            quality=bonus.Quality,
            inventory_type=proto.inventory_type,
            class_id=proto.class_id,
            subclass_id=proto.subclass_id,
            required_level=bonus.RequiredLevelOverride or bonus.RequiredLevel,
            effective_item_level=item_level,
            item_level_provenance=provenance,
            selection=selection,
            bonus_trace=bonus.trace,
            unhandled_bonuses=bonus.unhandled,
            applied_bonus_lists=bonus.applied_bonus_lists,
            stats=stats,
            sockets=list(bonus.SocketColor),
            gems=gem_details,
            armor=armor,
            weapon=weapon,
            effects=classify_effects(bonus.Effects),
            enchants=enchant_details,
            item_set_id=proto.item_set,
            item_set=item_set,
            curve_evaluations=self.curves.since(curve_mark),
            warnings=warnings,
        )

    def _apply_gems(self, bonus: BonusData, gems: Sequence[GemSlot],
                    player_level: int, warnings: list[str]) -> list[dict[str, Any]]:
        """Mirrors: ``Item::SetGem`` -- the host item's GemItemLevelBonus only.

        The gem's own stats arrive through ``Player::ApplyEnchantment`` on the
        socket enchantment slot, which is a separate path and is reported
        separately.
        """
        out: list[dict[str, Any]] = []
        for slot in gems:
            if not 0 <= slot.socket_index < MAX_ITEM_PROTO_SOCKETS:
                raise SourceError(
                    f"socket index {slot.socket_index} is outside "
                    f"0..{MAX_ITEM_PROTO_SOCKETS - 1}")
            gem_proto = self.items.get(slot.gem_item_id)
            detail = self.enchants.describe_gem(gem_proto, player_level)
            detail["socket_index"] = slot.socket_index
            socket_color = bonus.SocketColor[slot.socket_index]
            if socket_color:
                fits = self.enchants.gem_fits_socket(detail["gem_type_mask"],
                                                     socket_color)
                detail["socket_color"] = socket_color
                detail["fits_socket"] = fits
                if not fits:
                    warnings.append(
                        f"gem {slot.gem_item_id} type mask "
                        f"{detail['gem_type_mask']:#x} does not fit socket "
                        f"colour {socket_color}; Item::GemsFitSockets would fail")
            else:
                detail["socket_color"] = 0
                detail["fits_socket"] = None
                warnings.append(
                    f"socket {slot.socket_index} has no colour on this item; "
                    f"Trinity treats a colourless socket as prismatic and "
                    f"gates it on the PRISMATIC_ENCHANTMENT_SLOT enchant")

            gem_bonus = BonusData(gem_proto, self)
            for bonus_list_id in slot.bonus_list_ids:
                gem_bonus.add_bonus_list(bonus_list_id)
            enchant_id = detail["enchant_id"]
            if enchant_id:
                enchant = self.enchants.describe(enchant_id)
                gem_base_item_level = gem_bonus.ItemLevel
                if gem_bonus.PlayerLevelToItemLevelCurveId:
                    scaled = int(self.curves.value_at(
                        gem_bonus.PlayerLevelToItemLevelCurveId, player_level,
                        consumer="Item::SetGem/PlayerLevelToItemLevelCurve"))
                    if scaled:
                        gem_base_item_level = scaled
                delta, trail = self.enchants.gem_item_level_bonus(
                    enchant, gem_base_item_level + gem_bonus.ItemLevelBonus,
                    self.trees.level_delta_to_bonus_list.get,
                    self.bonuses_for_list)
                bonus.GemItemLevelBonus[slot.socket_index] += delta
                detail["host_item_level_bonus"] = delta
                detail["host_item_level_trail"] = trail
            out.append(detail)
        return out

    def _resolve_stats(self, proto: ItemTemplate, bonus: BonusData,
                       item_level: int) -> list[StatValue]:
        """Mirrors: the loop in ``Player::_ApplyItemBonuses``.

        The ilvl multipliers and the ``std::round`` happen here, *after*
        ``Item::GetItemStatValue``; the rounded value is then cast to ``int32``
        at each ``ApplyRatingMod`` / ``HandleStatFlatModifier`` call site.
        """
        rating_mult = self.scaling.ilvl_stat_multiplier(
            self.scaling.gt_combat_ratings_mult, item_level, proto.inventory_type)
        stamina_mult = self.scaling.ilvl_stat_multiplier(
            self.scaling.gt_stamina_mult, item_level, proto.inventory_type)

        out: list[StatValue] = []
        for index in range(MAX_ITEM_PROTO_STATS):
            stat_type = bonus.ItemStatType[index]
            if stat_type == -1:
                continue
            value = self.scaling.item_stat_value(proto, bonus, index, item_level)
            value.ratings = tuple(MOD_TO_RATINGS.get(stat_type, ()))
            value.is_primary = stat_type in PRIMARY_MODS
            if value.raw_value == 0:
                value.final_value = 0
                out.append(value)
                continue
            scaled = value.raw_value
            if stat_type == MOD_STAMINA and stamina_mult is not None:
                scaled *= stamina_mult
                value.stamina_mult_by_ilvl = stamina_mult
            elif stat_type in RATING_MULTIPLIED_MODS and rating_mult is not None:
                scaled *= rating_mult
                value.combat_ratings_mult_by_ilvl = rating_mult
            value.value_before_round = scaled
            value.final_value = round_half_away(scaled)
            out.append(value)
        return out

    def _resolve_weapon(self, proto: ItemTemplate, bonus: BonusData,
                        item_level: int) -> dict[str, Any] | None:
        if proto.class_id != ITEM_CLASS_WEAPON:
            return None
        min_dmg, max_dmg, dps = self.scaling.weapon_damage(
            proto, bonus.Quality, item_level)
        return {
            "damage_table": self.scaling.damage_table_name(proto),
            "is_caster_weapon": proto.is_caster_weapon,
            "dps": dps,
            "min_damage": min_dmg,
            "max_damage": max_dmg,
            "speed_ms": proto.delay,
            "dmg_variance": proto.dmg_variance,
            "weapon_attack_power": self.scaling.weapon_attack_power(dps),
            "note": "Player::_ApplyWeaponDamage sets weapon AP to int32(dps * 6)",
        }

    # -- variant discovery ------------------------------------------------
    def discover_variants(self, item_id: int) -> list[Variant]:
        """Enumerate every context/keystone variant derivable from source rows.

        Candidate contexts come only from the item's own reachable bonus trees:

        * ``ItemBonusTreeNode.ItemContext`` on any reachable node;
        * every ``ItemContext`` member of a referenced
          ``ItemCreationContextGroupID``;
        * the contexts named by the hardcoded sequence-level switch, but only
          when one of :data:`~gearing.tree.HARDCODED_SEQUENCE_TREES` is reachable;
        * ``NONE``, always, as the un-contextualised baseline.

        Keystone levels come from reachable ``Min``/``MaxMythicPlusLevel``
        bounds.  Nothing is fabricated.
        """
        proto = self.items.get(item_id)
        contexts: dict[int, str] = {ITEM_CONTEXT_NONE: "baseline (no context)"}
        keystone_levels: set[int] = set()
        hardcoded: set[int] = set()

        for tree_id in self.trees.reachable_trees(item_id):
            if tree_id in HARDCODED_SEQUENCE_TREES:
                hardcoded.add(tree_id)
            for node in self.trees.nodes_by_tree.get(tree_id, ()):
                ctx = int(node["ItemContext"])
                if ctx and ctx != ITEM_CONTEXT_FORCE_TO_NONE:
                    contexts.setdefault(
                        ctx, f"ItemBonusTreeNode {node['ID']}.ItemContext")
                group_id = int(node["ItemCreationContextGroupID"])
                for member in self.trees.contexts_by_creation_group.get(group_id, ()):
                    contexts.setdefault(
                        member,
                        f"ItemCreationContextGroup {group_id} via node {node['ID']}")
                for key in ("MinMythicPlusLevel", "MaxMythicPlusLevel"):
                    level = int(node[key])
                    if level:
                        keystone_levels.add(level)

        from .enums import (CONTEXT_DUNGEON_MYTHIC, CONTEXT_DUNGEON_NORMAL,
                            CONTEXT_RAID_HEROIC, CONTEXT_RAID_MYTHIC,
                            CONTEXT_RAID_NORMAL, CONTEXT_RAID_RAID_FINDER)
        if 4128 in hardcoded:
            for ctx in (CONTEXT_RAID_NORMAL, CONTEXT_RAID_RAID_FINDER,
                        CONTEXT_RAID_HEROIC, CONTEXT_RAID_MYTHIC):
                contexts.setdefault(ctx, "hardcoded sequence-level switch, tree 4128")
        if 4140 in hardcoded:
            for ctx in (CONTEXT_DUNGEON_NORMAL, CONTEXT_DUNGEON_MYTHIC):
                contexts.setdefault(ctx, "hardcoded sequence-level switch, tree 4140")

        variants: list[Variant] = []
        for ctx in sorted(contexts):
            variants.append(Variant(label=context_name(ctx), context=ctx,
                                    origin=contexts[ctx]))
            for level in sorted(keystone_levels):
                variants.append(Variant(
                    label=f"{context_name(ctx)}+M{level}", context=ctx,
                    mythic_plus_keystone_level=level,
                    origin="ItemBonusTreeNode Min/MaxMythicPlusLevel"))
        _ = proto
        return variants

    def discover_upgrade_steps(self, item_id: int) -> list[dict[str, Any]]:
        """List the ``ItemBonusListGroupEntry`` rows reachable from an item.

        Each row is one step of an upgrade track: a ``SequenceValue``, the bonus
        list applied at that step, and the item-level selector it points at.
        Live item instances carry the step's bonus list directly, so this is the
        source-backed enumeration of "upgrade state" for the item.
        """
        groups: dict[int, list[int]] = {}
        for tree_id in self.trees.reachable_trees(item_id):
            for node in self.trees.nodes_by_tree.get(tree_id, ()):
                group_id = int(node["ChildItemBonusListGroupID"])
                if group_id:
                    groups.setdefault(group_id, []).append(int(node["ID"]))

        steps: list[dict[str, Any]] = []
        for group_id, node_ids in sorted(groups.items()):
            group = self._bonus_list_group.get(group_id)
            for entry in self.trees.group_entries.get(group_id, ()):
                selector = self.trees.level_selector.get(
                    int(entry["ItemLevelSelectorID"]))
                steps.append({
                    "item_bonus_list_group_id": group_id,
                    "reached_from_nodes": node_ids,
                    "group_sequence_spell_id": int(group["SequenceSpellID"]) if group else None,
                    "group_entry_id": int(entry["ID"]),
                    "sequence_value": int(entry["SequenceValue"]),
                    "bonus_list_id": int(entry["ItemBonusListID"]),
                    "item_level_selector_id": int(entry["ItemLevelSelectorID"]),
                    "selector_min_item_level": (int(selector["MinItemLevel"])
                                                if selector else None),
                    "player_condition_id": int(entry["PlayerConditionID"]),
                    "flags": int(entry["Flags"]),
                })
        return steps


def distinct_key(resolved: ResolvedItem) -> tuple[Any, ...]:
    """Identity used to collapse variants that resolve identically."""
    return (
        resolved.effective_item_level,
        tuple(resolved.applied_bonus_lists),
        resolved.selection.item_level_selector_id,
        resolved.quality,
        tuple((s.stat_index, s.final_value) for s in resolved.stats),
        resolved.armor,
        None if resolved.weapon is None else resolved.weapon["dps"],
    )
