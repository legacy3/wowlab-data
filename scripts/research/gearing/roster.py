"""Discovering *which* items belong to a piece of current content.

What is and is not authoritative here matters, so it is stated up front.

Encounter loot
--------------
``JournalEncounterItem`` is the encounter-journal ("adventure guide") loot
display relationship.  TrinityCore loads ``JournalInstance``, ``JournalEncounter``
and ``JournalTier`` (for hyperlink validation and LFG) but **never loads
``JournalEncounterItem``**: server loot comes from its own ``world`` database,
which is not part of this snapshot.  So a roster built here is
"what the client's encounter journal lists for this encounter", which is a real
source relationship in the checked-in data but is *not* a server loot-table
authority.  Every roster entry records that provenance.

Difficulty -> ItemContext
-------------------------
This part *is* direct-consumer backed.  ``ItemBonusMgr::GetContextForPlayer``
reads ``Difficulty.ItemContext``, then ``MapDifficulty.ItemContext``, then
``MapDifficulty.ItemContextPickerID``, with ``Force_to_NONE`` collapsing to
``NONE``.  That is reproduced exactly, and the resulting ItemContext is the same
value the bonus-tree traversal consumes.

"Current"
---------
There is **no fact in this snapshot that says which season is live.**
``JournalTierXInstance.AvailabilityCondition`` points at a ``PlayerCondition``
whose ``ModifierTree`` is a pair of ``HasTimeEventPassed`` checks -- a
``[start, end)`` window over ``TimeEvent`` ids.  ``TimeEvent`` is not in the
snapshot, and TrinityCore only hardcodes timestamps for a handful of old ids
(``CriteriaHandler.cpp``, ``ModifierTreeType::HasTimeEventPassed``), defaulting
unknown ids to "now" -- which makes both a season's start and its end evaluate
as passed.  So season activation is underivable from data *and* from the direct
consumer.

Consequently every roster entry point requires an explicit selector.  A
``latest-in-source`` convenience exists, but it is an ordering heuristic over
``TimeEvent`` ids, it says so in its provenance, and it is never the default.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from . import SourceError
from .enums import (
    ITEM_CONTEXT_FORCE_TO_NONE,
    ITEM_CONTEXT_NONE,
    MAP_INSTANCE_TYPE_NAMES,
    MAP_INSTANCE_TYPE_PARTY,
    MAP_INSTANCE_TYPE_RAID,
    MODIFIER_TREE_OP_SINGLE_FALSE,
    MODIFIER_TREE_OP_SINGLE_TRUE,
    MODIFIER_TREE_TYPE_HAS_TIME_EVENT_PASSED,
    context_name,
)
from .tables import Tables

#: ``JournalTier.Expansion`` sentinel the client uses for the rolling
#: "Current Season" tier.  Real expansions use 100 * expansion number.
CURRENT_SEASON_EXPANSION_SENTINEL = 9000

#: Difficulty ids whose ``JournalEncounterItem.DifficultyMask`` bit is set.
#: The mask is a bitfield over ``Difficulty.ID``; -1 means "all".
DIFFICULTY_MASK_ALL = -1


@dataclass(frozen=True)
class DifficultyContext:
    """One (map, difficulty) pair resolved to the ItemContext it produces."""

    map_id: int
    map_name: str
    difficulty_id: int
    difficulty_name: str
    difficulty_item_context: int
    map_difficulty_item_context: int
    item_context_picker_id: int
    resolved_context: int
    resolved_context_name: str
    picker_alternatives: tuple[dict[str, Any], ...] = ()
    content_tuning_id: int = 0
    max_players: int = 0

    def to_dict(self) -> dict[str, Any]:
        out = dict(self.__dict__)
        out["picker_alternatives"] = [dict(p) for p in self.picker_alternatives]
        return out


@dataclass(frozen=True)
class SeasonWindow:
    """One ``AvailabilityCondition`` group inside a journal tier."""

    journal_tier_id: int
    journal_tier_name: str
    availability_condition_id: int
    start_time_event_ids: tuple[int, ...]
    end_time_event_ids: tuple[int, ...]
    modifier_tree_id: int
    journal_instance_ids: tuple[int, ...]
    provenance: str

    @property
    def sort_key(self) -> tuple[int, int]:
        """Ordering heuristic: newest start TimeEvent first, then condition id."""
        return (max(self.start_time_event_ids, default=0),
                self.availability_condition_id)

    def to_dict(self) -> dict[str, Any]:
        out = dict(self.__dict__)
        out["start_time_event_ids"] = list(self.start_time_event_ids)
        out["end_time_event_ids"] = list(self.end_time_event_ids)
        out["journal_instance_ids"] = list(self.journal_instance_ids)
        return out


@dataclass(frozen=True)
class JournalInstanceInfo:
    journal_instance_id: int
    name: str
    map_id: int
    map_name: str
    instance_type: int
    instance_type_name: str
    map_expansion_id: int
    flags: int

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


@dataclass(frozen=True)
class LootEntry:
    """One item the encounter journal lists for an encounter."""

    item_id: int
    journal_encounter_item_id: int
    journal_encounter_id: int
    encounter_name: str
    journal_instance_id: int
    instance_name: str
    map_id: int
    difficulty_mask: int
    display_season_id: int
    faction_mask: int
    flags: int

    @property
    def provenance(self) -> str:
        return (f"JournalEncounterItem {self.journal_encounter_item_id} -> "
                f"JournalEncounter {self.journal_encounter_id} "
                f"({self.encounter_name!r}) -> JournalInstance "
                f"{self.journal_instance_id} ({self.instance_name!r}) -> "
                f"Map {self.map_id}")

    def to_dict(self) -> dict[str, Any]:
        out = dict(self.__dict__)
        out["provenance"] = self.provenance
        return out


@dataclass
class ContentRoster:
    """A discovered roster plus the evidence for every membership claim."""

    selector: str
    instances: list[JournalInstanceInfo]
    difficulty_contexts: dict[int, list[DifficultyContext]]
    loot: list[LootEntry]
    notes: list[str] = field(default_factory=list)

    @property
    def item_ids(self) -> list[int]:
        seen: dict[int, None] = {}
        for entry in self.loot:
            seen.setdefault(entry.item_id, None)
        return list(seen)

    def contexts(self) -> list[int]:
        out: set[int] = set()
        for entries in self.difficulty_contexts.values():
            for entry in entries:
                if entry.resolved_context != ITEM_CONTEXT_NONE:
                    out.add(entry.resolved_context)
                for alt in entry.picker_alternatives:
                    out.add(int(alt["item_creation_context"]))
        return sorted(out)

    def to_dict(self) -> dict[str, Any]:
        return {
            "selector": self.selector,
            "instances": [i.to_dict() for i in self.instances],
            "difficulty_contexts": {
                str(k): [d.to_dict() for d in v]
                for k, v in self.difficulty_contexts.items()},
            "contexts": self.contexts(),
            "item_count": len(self.item_ids),
            "loot": [e.to_dict() for e in self.loot],
            "notes": self.notes,
        }


class RosterDiscovery:
    def __init__(self, tables: Tables) -> None:
        self.tables = tables
        self._journal_tier = tables("JournalTier").by("ID")
        self._tier_instances = tables("JournalTierXInstance")
        self._journal_instance = tables("JournalInstance").by("ID")
        self._journal_encounter = tables("JournalEncounter")
        self._journal_encounter_item = tables("JournalEncounterItem").group(
            "JournalEncounterID")
        self._map = tables("Map").by("ID")
        self._map_difficulty = tables("MapDifficulty").group("MapID")
        self._difficulty = tables("Difficulty").by("ID")
        self._picker_entries = tables("ItemContextPickerEntry").group(
            "ItemContextPickerID")
        self._player_condition = tables("PlayerCondition").by("ID")
        self._modifier_tree = tables("ModifierTree").by("ID")
        self._modifier_children = tables("ModifierTree").group("Parent")
        self._encounters_by_instance: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for row in self._journal_encounter:
            self._encounters_by_instance[int(row["JournalInstanceID"])].append(row)

    # -- journal tiers ---------------------------------------------------
    def journal_tiers(self) -> list[dict[str, Any]]:
        return sorted(
            ({"journal_tier_id": int(r["ID"]), "name": str(r["Name_lang"]),
              "expansion": int(r["Expansion"]),
              "player_condition_id": int(r["PlayerConditionID"])}
             for r in self.tables("JournalTier")),
            key=lambda r: -r["expansion"])

    def current_season_tier_id(self) -> int:
        """The journal tier whose ``Expansion`` is the current-season sentinel.

        This is a *source* fact (the sentinel value), not a claim about which
        season is live.  Raises if the snapshot has no such tier, or more than
        one, rather than picking.
        """
        matches = [int(r["ID"]) for r in self.tables("JournalTier")
                   if int(r["Expansion"]) == CURRENT_SEASON_EXPANSION_SENTINEL]
        if not matches:
            raise SourceError(
                f"no JournalTier has Expansion="
                f"{CURRENT_SEASON_EXPANSION_SENTINEL}; this snapshot has no "
                f"current-season tier, pass an explicit --journal-tier")
        if len(matches) > 1:
            raise SourceError(
                f"JournalTier rows {matches} all use the current-season "
                f"sentinel; pass an explicit --journal-tier")
        return matches[0]

    # -- season windows --------------------------------------------------
    def _time_event_window(self, player_condition_id: int
                           ) -> tuple[int, tuple[int, ...], tuple[int, ...]]:
        """Extract the ``[start, end)`` TimeEvent pair behind a PlayerCondition.

        Returns ``(modifier_tree_id, start_ids, end_ids)``.  A condition with no
        modifier tree yields ``(0, (), ())``.
        """
        condition = self._player_condition.get(player_condition_id)
        if condition is None:
            return (0, (), ())
        tree_id = int(condition["ModifierTreeID"])
        if not tree_id:
            return (0, (), ())
        starts: list[int] = []
        ends: list[int] = []
        for child in self._modifier_children.get(tree_id, ()):
            if int(child["Type"]) != MODIFIER_TREE_TYPE_HAS_TIME_EVENT_PASSED:
                continue
            operator = int(child["Operator"])
            asset = int(child["Asset"])
            if operator == MODIFIER_TREE_OP_SINGLE_TRUE:
                starts.append(asset)
            elif operator == MODIFIER_TREE_OP_SINGLE_FALSE:
                ends.append(asset)
        return (tree_id, tuple(sorted(starts)), tuple(sorted(ends)))

    def season_windows(self, journal_tier_id: int) -> list[SeasonWindow]:
        """Group a tier's instances by ``AvailabilityCondition``.

        Ordered by the heuristic in :attr:`SeasonWindow.sort_key`; callers must
        not treat the first element as "live" without saying so.
        """
        tier = self._journal_tier.get(journal_tier_id)
        if tier is None:
            raise SourceError(f"JournalTier {journal_tier_id} does not exist")
        grouped: dict[int, list[int]] = defaultdict(list)
        for row in self._tier_instances:
            if int(row["JournalTierID"]) != journal_tier_id:
                continue
            grouped[int(row["AvailabilityCondition"])].append(
                int(row["JournalInstanceID"]))

        windows: list[SeasonWindow] = []
        for condition_id, instance_ids in grouped.items():
            tree_id, starts, ends = self._time_event_window(condition_id)
            if condition_id == 0:
                provenance = ("JournalTierXInstance.AvailabilityCondition = 0: "
                              "no gate, always listed")
            elif tree_id:
                provenance = (
                    f"PlayerCondition {condition_id} -> ModifierTree {tree_id}: "
                    f"HasTimeEventPassed{list(starts)} AND NOT "
                    f"HasTimeEventPassed{list(ends)}; TimeEvent timestamps are "
                    f"absent from this snapshot and from the direct consumer")
            else:
                provenance = (
                    f"PlayerCondition {condition_id} has no ModifierTree; "
                    f"gate is not a time window")
            windows.append(SeasonWindow(
                journal_tier_id=journal_tier_id,
                journal_tier_name=str(tier["Name_lang"]),
                availability_condition_id=condition_id,
                start_time_event_ids=starts,
                end_time_event_ids=ends,
                modifier_tree_id=tree_id,
                journal_instance_ids=tuple(sorted(instance_ids)),
                provenance=provenance,
            ))
        windows.sort(key=lambda w: w.sort_key, reverse=True)
        return windows

    # -- instances -------------------------------------------------------
    def instance_info(self, journal_instance_id: int) -> JournalInstanceInfo:
        row = self._journal_instance.get(journal_instance_id)
        if row is None:
            raise SourceError(f"JournalInstance {journal_instance_id} does not exist")
        map_id = int(row["MapID"])
        map_row = self._map.get(map_id)
        instance_type = int(map_row["InstanceType"]) if map_row else 0
        return JournalInstanceInfo(
            journal_instance_id=journal_instance_id,
            name=str(row["Name_lang"]),
            map_id=map_id,
            map_name=str(map_row["MapName_lang"]) if map_row else "",
            instance_type=instance_type,
            instance_type_name=MAP_INSTANCE_TYPE_NAMES.get(instance_type,
                                                           str(instance_type)),
            map_expansion_id=int(map_row["ExpansionID"]) if map_row else -1,
            flags=int(row["Flags"]),
        )

    # -- difficulty -> ItemContext ---------------------------------------
    def difficulty_contexts(self, map_id: int) -> list[DifficultyContext]:
        """Mirrors: ``ItemBonusMgr::GetContextForPlayer``.

        ``evalContext(current, new)``: a ``NONE`` new value keeps the current
        one, ``Force_to_NONE`` resets to ``NONE``, anything else replaces.
        Applied first for ``Difficulty.ItemContext`` then for
        ``MapDifficulty.ItemContext``.

        The picker stage depends on a ``PlayerCondition`` per entry, which is
        player state.  Rather than guess, every picker entry is reported as an
        alternative with its condition id; the ``resolved_context`` field is the
        pre-picker value.
        """
        map_row = self._map.get(map_id)
        if map_row is None:
            raise SourceError(f"Map {map_id} does not exist")

        def eval_context(current: int, new: int) -> int:
            if new == ITEM_CONTEXT_NONE:
                return current
            if new == ITEM_CONTEXT_FORCE_TO_NONE:
                return ITEM_CONTEXT_NONE
            return new

        out: list[DifficultyContext] = []
        for md in self._map_difficulty.get(map_id, ()):
            difficulty_id = int(md["DifficultyID"])
            difficulty = self._difficulty.get(difficulty_id)
            d_ctx = int(difficulty["ItemContext"]) if difficulty else 0
            md_ctx = int(md["ItemContext"])
            context = eval_context(ITEM_CONTEXT_NONE, d_ctx)
            context = eval_context(context, md_ctx)
            picker_id = int(md["ItemContextPickerID"])
            alternatives: list[dict[str, Any]] = []
            if picker_id:
                for entry in self._picker_entries.get(picker_id, ()):
                    if int(entry["PVal"]) <= 0:
                        continue
                    alternatives.append({
                        "item_context_picker_entry_id": int(entry["ID"]),
                        "item_creation_context": int(entry["ItemCreationContext"]),
                        "item_creation_context_name": context_name(
                            int(entry["ItemCreationContext"])),
                        "order_index": int(entry["OrderIndex"]),
                        "flags": int(entry["Flags"]),
                        "inverts_condition": bool(int(entry["Flags"]) & 0x1),
                        "player_condition_id": int(entry["PlayerConditionID"]),
                        "label_id": int(entry["LabelID"]),
                    })
                alternatives.sort(key=lambda a: -a["order_index"])
            out.append(DifficultyContext(
                map_id=map_id,
                map_name=str(map_row["MapName_lang"]),
                difficulty_id=difficulty_id,
                difficulty_name=str(difficulty["Name_lang"]) if difficulty else "",
                difficulty_item_context=d_ctx,
                map_difficulty_item_context=md_ctx,
                item_context_picker_id=picker_id,
                resolved_context=context,
                resolved_context_name=context_name(context),
                picker_alternatives=tuple(alternatives),
                content_tuning_id=int(md["ContentTuningID"]),
                max_players=int(md["MaxPlayers"]),
            ))
        out.sort(key=lambda d: d.difficulty_id)
        return out

    # -- loot ------------------------------------------------------------
    def instance_loot(self, journal_instance_id: int) -> list[LootEntry]:
        info = self.instance_info(journal_instance_id)
        out: list[LootEntry] = []
        for encounter in sorted(self._encounters_by_instance.get(journal_instance_id, ()),
                                key=lambda e: (int(e["OrderIndex"]), int(e["ID"]))):
            encounter_id = int(encounter["ID"])
            for row in self._journal_encounter_item.get(encounter_id, ()):
                out.append(LootEntry(
                    item_id=int(row["ItemID"]),
                    journal_encounter_item_id=int(row["ID"]),
                    journal_encounter_id=encounter_id,
                    encounter_name=str(encounter["Name_lang"]),
                    journal_instance_id=journal_instance_id,
                    instance_name=info.name,
                    map_id=info.map_id,
                    difficulty_mask=int(row["DifficultyMask"]),
                    display_season_id=int(row["DisplaySeasonID"]),
                    faction_mask=int(row["FactionMask"]),
                    flags=int(row["Flags"]),
                ))
        return out

    # -- rosters ---------------------------------------------------------
    def roster_for_instances(self, journal_instance_ids: Sequence[int],
                             selector: str) -> ContentRoster:
        instances = [self.instance_info(i) for i in journal_instance_ids]
        contexts = {i.journal_instance_id: self.difficulty_contexts(i.map_id)
                    for i in instances if i.map_id}
        loot: list[LootEntry] = []
        for instance in instances:
            loot.extend(self.instance_loot(instance.journal_instance_id))
        notes = [
            "JournalEncounterItem is client encounter-journal display data; "
            "TrinityCore does not load it, so this is a navigation-grade loot "
            "relationship, not a server loot-table authority.",
            "Difficulty -> ItemContext is direct-consumer backed "
            "(ItemBonusMgr::GetContextForPlayer).",
        ]
        return ContentRoster(selector=selector, instances=instances,
                             difficulty_contexts=contexts, loot=loot, notes=notes)

    def roster_for_window(self, window: SeasonWindow, instance_type: int | None,
                          ) -> ContentRoster:
        ids = [i for i in window.journal_instance_ids]
        if instance_type is not None:
            ids = [i for i in ids
                   if self.instance_info(i).instance_type == instance_type]
        selector = (f"JournalTier {window.journal_tier_id} "
                    f"({window.journal_tier_name!r}) availability condition "
                    f"{window.availability_condition_id}"
                    + (f", instance type {instance_type}"
                       if instance_type is not None else ""))
        roster = self.roster_for_instances(ids, selector)
        roster.notes.insert(0, window.provenance)
        return roster

    def raid_roster(self, window: SeasonWindow) -> ContentRoster:
        return self.roster_for_window(window, MAP_INSTANCE_TYPE_RAID)

    def dungeon_roster(self, window: SeasonWindow) -> ContentRoster:
        return self.roster_for_window(window, MAP_INSTANCE_TYPE_PARTY)


def difficulty_mask_contains(mask: int, difficulty_id: int) -> bool:
    """``JournalEncounterItem.DifficultyMask`` membership.

    -1 means every difficulty.  Otherwise the mask is a bitfield indexed by
    ``Difficulty.ID``.  This is client display data; no TrinityCore consumer
    reads it, so the interpretation is labelled as such wherever it is used.
    """
    if mask == DIFFICULTY_MASK_ALL:
        return True
    if difficulty_id <= 0 or difficulty_id > 63:
        return False
    return bool(mask & (1 << (difficulty_id - 1)))
