"""Turning a discovered roster into resolved item variants.

Keeps the three Mythic+ populations separate, because they are separate source
relationships and conflating them is exactly the mistake this pass is meant to
avoid:

``base``
    what the dungeon's own ``MapDifficulty`` rows resolve to (Normal, Heroic,
    Mythic, and the Mythic-Keystone difficulty's own ItemContext).
``end-of-run``
    the ``MythicPlus_End_of_Run`` family of ItemContexts, optionally with a
    keystone level so ``Min``/``MaxMythicPlusLevel`` node gates apply.
``vault``
    the ``MythicPlus_Jackpot`` / weekly-reward ItemContexts.

Membership of an item in any of these is only claimed when the item's *own*
bonus trees actually expose that context; a context is never forced onto an
item that has no node for it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from . import SourceError
from .enums import DEFAULT_PLAYER_LEVEL, context_name
from .resolver import GearResolver, ResolvedItem, Variant, distinct_key
from .roster import ContentRoster, LootEntry

#: ItemContext families, by the name prefix the enum uses.  Used only to split
#: an item's *discovered* contexts into reporting buckets.
MYTHIC_PLUS_END_OF_RUN_CONTEXTS = (16, 33, 34, 87)
MYTHIC_PLUS_VAULT_CONTEXTS = (35, 72, 73)


@dataclass
class RosterItem:
    """One roster member with its membership evidence and resolved variants."""

    item_id: int
    name: str
    provenance: list[str]
    loot_entries: list[LootEntry]
    variants: list[ResolvedItem] = field(default_factory=list)
    item_set_id: int = 0
    skipped_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "name": self.name,
            "item_set_id": self.item_set_id,
            "provenance": self.provenance,
            "loot_entries": [e.to_dict() for e in self.loot_entries],
            "variants": [v.to_dict() for v in self.variants],
            "skipped_reason": self.skipped_reason,
        }


@dataclass
class ResolvedRoster:
    roster: ContentRoster
    items: list[RosterItem]
    player_level: int
    requested_contexts: list[int]
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "selector": self.roster.selector,
            "player_level": self.player_level,
            "requested_contexts": self.requested_contexts,
            "requested_context_names": [context_name(c) for c in self.requested_contexts],
            "instances": [i.to_dict() for i in self.roster.instances],
            "difficulty_contexts": {
                str(k): [d.to_dict() for d in v]
                for k, v in self.roster.difficulty_contexts.items()},
            "notes": self.roster.notes + self.notes,
            "item_count": len(self.items),
            "variant_count": sum(len(i.variants) for i in self.items),
            "items": [i.to_dict() for i in self.items],
        }


def resolve_roster(
    resolver: GearResolver,
    roster: ContentRoster,
    *,
    contexts: Sequence[int] | None = None,
    player_level: int = DEFAULT_PLAYER_LEVEL,
    mythic_plus_keystone_level: int | None = None,
    equippable_only: bool = True,
    item_set_only: bool = False,
    distinct: bool = True,
) -> ResolvedRoster:
    """Resolve every roster item in every requested context it actually supports.

    ``contexts`` defaults to the contexts the roster's own ``MapDifficulty``
    rows produce.  An item is only resolved in a context that its own bonus
    trees expose (or in ``NONE``), so no variant is invented.
    """
    wanted = list(contexts) if contexts is not None else roster.contexts()
    by_item: dict[int, list[LootEntry]] = {}
    for entry in roster.loot:
        by_item.setdefault(entry.item_id, []).append(entry)

    notes: list[str] = []
    items: list[RosterItem] = []
    for item_id, entries in by_item.items():
        if not resolver.items.exists(item_id):
            items.append(RosterItem(
                item_id=item_id, name="", provenance=[e.provenance for e in entries],
                loot_entries=entries,
                skipped_reason="no Item/ItemSparse row in this snapshot"))
            continue
        proto = resolver.items.get(item_id)
        if equippable_only and not proto.is_equippable:
            items.append(RosterItem(
                item_id=item_id, name=proto.name,
                provenance=[e.provenance for e in entries], loot_entries=entries,
                item_set_id=proto.item_set,
                skipped_reason="InventoryType 0 (not equippable)"))
            continue
        if item_set_only and not proto.item_set:
            continue

        available = {v.context for v in resolver.discover_variants(item_id)}
        member = RosterItem(
            item_id=item_id, name=proto.name,
            provenance=[e.provenance for e in entries], loot_entries=entries,
            item_set_id=proto.item_set)
        seen: set[Any] = set()
        for context in wanted:
            if context not in available:
                continue
            variant = Variant(
                label=context_name(context), context=context,
                mythic_plus_keystone_level=mythic_plus_keystone_level,
                origin=f"MapDifficulty-derived ItemContext {context}")
            resolved = resolver.resolve(item_id, variant, player_level=player_level)
            if distinct:
                key = distinct_key(resolved)
                if key in seen:
                    continue
                seen.add(key)
            member.variants.append(resolved)
        if not member.variants:
            member.skipped_reason = (
                f"no reachable ItemBonusTreeNode exposes any of the requested "
                f"contexts {wanted}")
        items.append(member)

    if mythic_plus_keystone_level is not None:
        notes.append(
            f"keystone level {mythic_plus_keystone_level} supplied; it only "
            f"changes the result where a reachable ItemBonusTreeNode sets "
            f"MinMythicPlusLevel/MaxMythicPlusLevel")
    return ResolvedRoster(roster=roster, items=items, player_level=player_level,
                          requested_contexts=wanted, notes=notes)


def split_mythic_plus_contexts(available: Iterable[int]) -> dict[str, list[int]]:
    """Bucket an item's available contexts into base / end-of-run / vault."""
    available = list(available)
    end_of_run = [c for c in available if c in MYTHIC_PLUS_END_OF_RUN_CONTEXTS]
    vault = [c for c in available if c in MYTHIC_PLUS_VAULT_CONTEXTS]
    special = set(end_of_run) | set(vault)
    return {
        "base": [c for c in available if c not in special],
        "end_of_run": end_of_run,
        "vault": vault,
    }
