"""Resolving many items at once, and the aggregates that are safe to compute.

A loadout is a list of equipped item *instances*.  Every field except
``item_id`` is optional; nothing is inferred when a field is absent.

What is aggregated here is deliberately narrow: raw stat totals, raw combat
rating totals, satisfied item-set thresholds, and the gear-reachable spell
roots.  Character combat stats are **not** computed, because base stats,
class/spec multipliers and aura stages have not been proved by this pass, and
guessing them would make the output look more authoritative than it is.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

from . import SourceError
from .enums import (
    DEFAULT_PLAYER_LEVEL,
    MOD_NAMES,
    MOD_STAMINA,
    PRIMARY_MODS,
    context_name,
)
from .ratings import RatingConversion, RatingEngine
from .resolver import GearResolver, GemSlot, ResolvedItem, Variant


@dataclass
class LoadoutEntry:
    """One requested item instance."""

    item_id: int
    context: int = 0
    bonus_list_ids: tuple[int, ...] = ()
    gems: tuple[GemSlot, ...] = ()
    enchant_ids: tuple[int, ...] = ()
    mythic_plus_keystone_level: int | None = None
    pvp_tier: int | None = None
    label: str | None = None
    auto_bonus_lists: bool = True

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "LoadoutEntry":
        if "item_id" not in raw:
            raise SourceError(f"loadout entry is missing item_id: {raw!r}")
        unknown = set(raw) - {
            "item_id", "context", "bonus_list_ids", "gems", "enchant_ids",
            "mythic_plus_keystone_level", "pvp_tier", "label",
            "auto_bonus_lists", "slot",
        }
        if unknown:
            raise SourceError(
                f"loadout entry for item {raw['item_id']} has unknown keys "
                f"{sorted(unknown)}; refusing to guess what they mean")
        gems: list[GemSlot] = []
        for index, gem in enumerate(raw.get("gems", ())):
            if isinstance(gem, int):
                gems.append(GemSlot(socket_index=index, gem_item_id=gem))
            elif isinstance(gem, dict):
                gems.append(GemSlot(
                    socket_index=int(gem.get("socket_index", index)),
                    gem_item_id=int(gem["gem_item_id"]),
                    bonus_list_ids=tuple(gem.get("bonus_list_ids", ()))))
            else:
                raise SourceError(f"unsupported gem entry {gem!r}")
        return cls(
            item_id=int(raw["item_id"]),
            context=int(raw.get("context", 0)),
            bonus_list_ids=tuple(int(b) for b in raw.get("bonus_list_ids", ())),
            gems=tuple(gems),
            enchant_ids=tuple(int(e) for e in raw.get("enchant_ids", ())),
            mythic_plus_keystone_level=(
                int(raw["mythic_plus_keystone_level"])
                if raw.get("mythic_plus_keystone_level") is not None else None),
            pvp_tier=(int(raw["pvp_tier"])
                      if raw.get("pvp_tier") is not None else None),
            label=raw.get("label") or raw.get("slot"),
            auto_bonus_lists=bool(raw.get("auto_bonus_lists", True)),
        )

    def variant(self) -> Variant:
        return Variant(
            label=self.label or context_name(self.context),
            context=self.context,
            mythic_plus_keystone_level=self.mythic_plus_keystone_level,
            pvp_tier=self.pvp_tier,
            extra_bonus_lists=self.bonus_list_ids,
            origin="supplied in loadout input",
        )


@dataclass
class Loadout:
    entries: list[LoadoutEntry]
    player_level: int = DEFAULT_PLAYER_LEVEL
    chr_spec_id: int | None = None
    trait_sub_tree_id: int | None = None
    pvp_bonus: bool = False
    current_build_patch: int | None = None

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Loadout":
        if "items" not in raw:
            raise SourceError("loadout JSON must have an 'items' array")
        return cls(
            entries=[LoadoutEntry.from_dict(e) for e in raw["items"]],
            player_level=int(raw.get("player_level", DEFAULT_PLAYER_LEVEL)),
            chr_spec_id=(int(raw["chr_spec_id"])
                         if raw.get("chr_spec_id") is not None else None),
            trait_sub_tree_id=(int(raw["trait_sub_tree_id"])
                               if raw.get("trait_sub_tree_id") is not None else None),
            pvp_bonus=bool(raw.get("pvp_bonus", False)),
            current_build_patch=(int(raw["current_build_patch"])
                                 if raw.get("current_build_patch") is not None else None),
        )

    @classmethod
    def from_json_file(cls, path: Path | str) -> "Loadout":
        text = Path(path).read_text(encoding="utf-8")
        try:
            raw = json.loads(text)
        except json.JSONDecodeError as error:
            raise SourceError(f"{path} is not valid JSON: {error}") from None
        if isinstance(raw, list):
            raw = {"items": raw}
        return cls.from_dict(raw)

    @classmethod
    def from_item_ids(cls, item_ids: Iterable[int], **kwargs: Any) -> "Loadout":
        return cls(entries=[LoadoutEntry(item_id=int(i)) for i in item_ids], **kwargs)


def parse_item_id_file(path: Path | str) -> list[int]:
    """One item id per line; ``#`` starts a comment.

    Trailing ``:context`` is accepted for convenience, e.g. ``271519:6``.
    """
    out: list[int] = []
    for number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        text = line.split("#", 1)[0].strip()
        if not text:
            continue
        try:
            out.append(int(text.split(":", 1)[0]))
        except ValueError:
            raise SourceError(f"{path}:{number}: {line!r} is not an item id") from None
    return out


def parse_item_id_spec(spec: str) -> LoadoutEntry:
    """``<item>[:<context>][/<bonus>,<bonus>...]`` -> a loadout entry."""
    body, _, bonuses = spec.partition("/")
    item_text, _, context_text = body.partition(":")
    try:
        item_id = int(item_text)
    except ValueError:
        raise SourceError(f"{spec!r} does not start with an item id") from None
    context = 0
    if context_text:
        try:
            context = int(context_text)
        except ValueError:
            raise SourceError(f"{spec!r} has a non-numeric context") from None
    bonus_ids: tuple[int, ...] = ()
    if bonuses:
        try:
            bonus_ids = tuple(int(b) for b in bonuses.replace(",", " ").split())
        except ValueError:
            raise SourceError(f"{spec!r} has a non-numeric bonus list id") from None
    return LoadoutEntry(item_id=item_id, context=context, bonus_list_ids=bonus_ids)


@dataclass
class LoadoutResult:
    loadout: Loadout
    items: list[ResolvedItem]
    stat_totals: dict[str, int] = field(default_factory=dict)
    rating_totals: dict[str, int] = field(default_factory=dict)
    set_bonuses: list[dict[str, Any]] = field(default_factory=list)
    spell_roots: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def rating_conversions(self, ratings: RatingEngine) -> list[RatingConversion]:
        return [ratings.convert(name, amount, self.loadout.player_level)
                for name, amount in sorted(self.rating_totals.items())]

    def to_dict(self, ratings: RatingEngine | None = None) -> dict[str, Any]:
        out: dict[str, Any] = {
            "player_level": self.loadout.player_level,
            "chr_spec_id": self.loadout.chr_spec_id,
            "item_count": len(self.items),
            "items": [i.to_dict() for i in self.items],
            "stat_totals": self.stat_totals,
            "rating_totals": self.rating_totals,
            "set_bonuses": self.set_bonuses,
            "spell_roots": self.spell_roots,
            "warnings": self.warnings,
            "caveat": (
                "Totals are raw item contributions only.  No base stats, class "
                "or spec policy, aura stage or percentage modifier is applied; "
                "this is not a character stat sheet."),
        }
        if ratings is not None:
            out["rating_conversions"] = [
                c.to_dict() for c in self.rating_conversions(ratings)]
        return out


def resolve_loadout(resolver: GearResolver, loadout: Loadout) -> LoadoutResult:
    """Resolve every entry and aggregate the facts that are safe to aggregate."""
    items: list[ResolvedItem] = []
    warnings: list[str] = []
    for entry in loadout.entries:
        resolved = resolver.resolve(
            entry.item_id, entry.variant(),
            player_level=loadout.player_level,
            gems=entry.gems,
            enchant_ids=entry.enchant_ids,
            pvp_bonus=loadout.pvp_bonus,
            current_build_patch=loadout.current_build_patch,
            auto_bonus_lists=entry.auto_bonus_lists,
        )
        items.append(resolved)
        warnings.extend(f"item {entry.item_id}: {w}" for w in resolved.warnings)

    stat_totals: dict[str, int] = {}
    rating_totals: dict[str, int] = {}
    for resolved in items:
        for stat in resolved.stats:
            if not stat.final_value:
                continue
            stat_totals[stat.stat_name] = stat_totals.get(stat.stat_name, 0) + stat.final_value
        for rating, amount in resolved.rating_contributions.items():
            rating_totals[rating] = rating_totals.get(rating, 0) + amount
        for enchant in resolved.enchants:
            for effect in enchant.get("effects", ()):
                amount = effect.get("resolved_amount")
                if amount is None or effect.get("type") != 5:
                    continue
                name = effect.get("stat_name") or str(effect.get("stat_type"))
                stat_totals[name] = stat_totals.get(name, 0) + amount
                for rating in effect.get("ratings", ()):
                    rating_totals[rating] = rating_totals.get(rating, 0) + amount

    equipped = [(r.item_id, r.item_set_id) for r in items if r.item_set_id]
    set_bonuses = [b.to_dict() for b in resolver.sets.satisfied_bonuses(
        equipped, chr_spec_id=loadout.chr_spec_id,
        trait_sub_tree_id=loadout.trait_sub_tree_id)]

    spell_roots: list[dict[str, Any]] = []
    for resolved in items:
        for root in resolved.spell_roots:
            spell_roots.append({**root, "item_id": resolved.item_id})
    for bonus in set_bonuses:
        spell_roots.append({
            "spell_id": bonus["spell_id"], "via": "ItemSetSpell",
            "route": f"{bonus['threshold']}p threshold on set "
                     f"{bonus['item_set_id']} -> Player::ApplyEquipSpell(nullptr)",
            "item_set_id": bonus["item_set_id"]})

    return LoadoutResult(
        loadout=loadout, items=items, stat_totals=stat_totals,
        rating_totals=rating_totals, set_bonuses=set_bonuses,
        spell_roots=spell_roots, warnings=warnings)
