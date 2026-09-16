"""Content-roster discovery: journal tiers, season windows, difficulty contexts."""

from __future__ import annotations

import pytest

from gearing import SourceError
from gearing.content import (
    MYTHIC_PLUS_END_OF_RUN_CONTEXTS,
    MYTHIC_PLUS_VAULT_CONTEXTS,
    resolve_roster,
    split_mythic_plus_contexts,
)
from gearing.enums import (
    CONTEXT_RAID_HEROIC,
    CONTEXT_RAID_MYTHIC,
    CONTEXT_RAID_NORMAL,
    CONTEXT_RAID_RAID_FINDER,
    ITEM_CONTEXT_NONE,
    MAP_INSTANCE_TYPE_PARTY,
    MAP_INSTANCE_TYPE_RAID,
)
from gearing.roster import (
    CURRENT_SEASON_EXPANSION_SENTINEL,
    difficulty_mask_contains,
)

pytestmark = pytest.mark.snapshot


# -- journal tiers ---------------------------------------------------------

def test_journal_tiers_are_ordered_and_include_a_current_season_sentinel(discovery):
    tiers = discovery.journal_tiers()
    assert tiers == sorted(tiers, key=lambda t: -t["expansion"])
    sentinels = [t for t in tiers
                 if t["expansion"] == CURRENT_SEASON_EXPANSION_SENTINEL]
    assert len(sentinels) == 1
    assert discovery.current_season_tier_id() == sentinels[0]["journal_tier_id"]


def test_current_season_tier_is_a_source_fact_not_a_name_match(discovery):
    tier_id = discovery.current_season_tier_id()
    tiers = {t["journal_tier_id"]: t for t in discovery.journal_tiers()}
    # The selection is by Expansion sentinel; the name is corroboration only.
    assert tiers[tier_id]["expansion"] == CURRENT_SEASON_EXPANSION_SENTINEL


# -- season windows --------------------------------------------------------

def test_season_windows_expose_their_time_event_gate(discovery):
    windows = discovery.season_windows(discovery.current_season_tier_id())
    assert len(windows) >= 2
    gated = [w for w in windows if w.availability_condition_id]
    assert gated, "expected at least one AvailabilityCondition-gated window"
    for window in gated:
        # Each gate is a [start, end) pair of HasTimeEventPassed checks.
        assert window.modifier_tree_id
        assert window.start_time_event_ids
        assert window.end_time_event_ids
        assert "TimeEvent timestamps are absent" in window.provenance


def test_time_event_table_is_absent_so_activation_is_underivable(tables):
    """The fact that makes 'current' unprovable, pinned as a test."""
    assert tables.optional("TimeEvent") is None


def test_unknown_journal_tier_fails_closed(discovery):
    with pytest.raises(SourceError, match="does not exist"):
        discovery.season_windows(9999999)


# -- difficulty -> ItemContext --------------------------------------------

def test_raid_difficulties_resolve_to_the_four_raid_contexts(discovery):
    window = discovery.season_windows(discovery.current_season_tier_id())[0]
    roster = discovery.raid_roster(window)
    assert roster.instances, "no raids in the newest window"
    biggest = max(roster.instances,
                  key=lambda i: len(discovery.instance_loot(i.journal_instance_id)))
    contexts = {d.difficulty_id: d.resolved_context
                for d in discovery.difficulty_contexts(biggest.map_id)}
    assert contexts[14] == CONTEXT_RAID_NORMAL
    assert contexts[15] == CONTEXT_RAID_HEROIC
    assert CONTEXT_RAID_RAID_FINDER in contexts.values()
    assert CONTEXT_RAID_MYTHIC in contexts.values()


def test_story_difficulty_resolves_to_no_context(discovery):
    window = discovery.season_windows(discovery.current_season_tier_id())[0]
    roster = discovery.raid_roster(window)
    found = False
    for instance in roster.instances:
        for entry in discovery.difficulty_contexts(instance.map_id):
            if entry.difficulty_name == "Story":
                assert entry.resolved_context == ITEM_CONTEXT_NONE
                found = True
    assert found, "expected a Story difficulty somewhere in the current raids"


def test_dungeon_keystone_difficulty_resolves_to_end_of_run(discovery):
    window = discovery.season_windows(discovery.current_season_tier_id())[0]
    roster = discovery.dungeon_roster(window)
    assert roster.instances
    for instance in roster.instances:
        contexts = {d.difficulty_id: d.resolved_context
                    for d in discovery.difficulty_contexts(instance.map_id)}
        if 8 in contexts:      # Mythic Keystone
            assert contexts[8] in MYTHIC_PLUS_END_OF_RUN_CONTEXTS
            break
    else:
        pytest.fail("no dungeon in the newest window has a keystone difficulty")


def test_item_context_picker_alternatives_are_reported_not_chosen(discovery):
    """The picker depends on a PlayerCondition, so both branches are surfaced."""
    window = discovery.season_windows(discovery.current_season_tier_id())[0]
    roster = discovery.dungeon_roster(window)
    with_picker = [
        entry
        for instance in roster.instances
        for entry in discovery.difficulty_contexts(instance.map_id)
        if entry.item_context_picker_id]
    assert with_picker, "expected at least one MapDifficulty with a picker"
    for entry in with_picker:
        assert len(entry.picker_alternatives) >= 2
        assert all(a["player_condition_id"] for a in entry.picker_alternatives)
        # Alternatives are ordered by OrderIndex descending, matching the
        # consumer's `selected->OrderIndex < candidate->OrderIndex` rule.
        order = [a["order_index"] for a in entry.picker_alternatives]
        assert order == sorted(order, reverse=True)
    # At least one current picker uses the inverting flag, which is how a
    # single PlayerCondition produces two mutually exclusive contexts.
    assert any(a["inverts_condition"]
               for entry in with_picker for a in entry.picker_alternatives)


# -- instances and loot ----------------------------------------------------

def test_instance_split_is_by_map_instance_type(discovery):
    window = discovery.season_windows(discovery.current_season_tier_id())[0]
    raids = discovery.raid_roster(window)
    dungeons = discovery.dungeon_roster(window)
    assert all(i.instance_type == MAP_INSTANCE_TYPE_RAID for i in raids.instances)
    assert all(i.instance_type == MAP_INSTANCE_TYPE_PARTY
               for i in dungeons.instances)
    assert not ({i.journal_instance_id for i in raids.instances}
                & {i.journal_instance_id for i in dungeons.instances})


def test_every_loot_entry_carries_provenance(discovery):
    window = discovery.season_windows(discovery.current_season_tier_id())[0]
    roster = discovery.raid_roster(window)
    assert roster.loot
    for entry in roster.loot:
        assert "JournalEncounterItem" in entry.provenance
        assert "JournalInstance" in entry.provenance
        assert entry.map_id


def test_roster_records_that_journal_loot_is_not_server_authority(discovery):
    window = discovery.season_windows(discovery.current_season_tier_id())[0]
    roster = discovery.raid_roster(window)
    assert any("TrinityCore does not load it" in note for note in roster.notes)


def test_difficulty_mask_membership():
    assert difficulty_mask_contains(-1, 14) is True
    assert difficulty_mask_contains(1 << 13, 14) is True
    assert difficulty_mask_contains(1 << 13, 15) is False
    assert difficulty_mask_contains(0, 14) is False


# -- resolution ------------------------------------------------------------

def test_roster_resolution_only_uses_contexts_the_item_supports(resolver, discovery):
    window = discovery.season_windows(discovery.current_season_tier_id())[0]
    roster = discovery.raid_roster(window)
    resolved = resolve_roster(resolver, roster, contexts=[CONTEXT_RAID_MYTHIC],
                              player_level=90)
    assert resolved.items
    for member in resolved.items:
        for variant in member.variants:
            available = {v.context for v in resolver.discover_variants(member.item_id)}
            assert variant.variant.context in available


def test_every_roster_item_exists_in_item_and_item_sparse(resolver, discovery):
    window = discovery.season_windows(discovery.current_season_tier_id())[0]
    for roster in (discovery.raid_roster(window), discovery.dungeon_roster(window)):
        resolved = resolve_roster(resolver, roster, contexts=[], player_level=90)
        for member in resolved.items:
            if member.skipped_reason == "no Item/ItemSparse row in this snapshot":
                continue
            assert resolver.items.exists(member.item_id), member.item_id


def test_tier_only_filter_keeps_only_item_set_members(resolver, discovery):
    window = discovery.season_windows(discovery.current_season_tier_id())[0]
    roster = discovery.raid_roster(window)
    resolved = resolve_roster(resolver, roster, contexts=roster.contexts(),
                              player_level=90, item_set_only=True)
    assert resolved.items
    for member in resolved.items:
        assert member.item_set_id


def test_mythic_plus_population_buckets_are_disjoint():
    buckets = split_mythic_plus_contexts([1, 2, 16, 23, 35, 33])
    assert set(buckets["end_of_run"]) == {16, 33}
    assert set(buckets["vault"]) == {35}
    assert set(buckets["base"]) == {1, 2, 23}
    assert not (set(buckets["base"]) & set(buckets["end_of_run"]))
    assert not (set(buckets["base"]) & set(buckets["vault"]))


def test_keystone_level_changes_only_gated_items(resolver, discovery):
    window = discovery.season_windows(discovery.current_season_tier_id())[0]
    roster = discovery.dungeon_roster(window)
    contexts = list(MYTHIC_PLUS_END_OF_RUN_CONTEXTS)
    without = resolve_roster(resolver, roster, contexts=contexts, player_level=90)
    with_key = resolve_roster(resolver, roster, contexts=contexts, player_level=90,
                              mythic_plus_keystone_level=2)
    levels_without = {(m.item_id, v.variant.context): v.effective_item_level
                      for m in without.items for v in m.variants}
    levels_with = {(m.item_id, v.variant.context): v.effective_item_level
                   for m in with_key.items for v in m.variants}
    assert levels_without, "no end-of-run variants resolved"
    # The keystone gate exists in current data, so at least one item moves.
    assert levels_with != levels_without
    assert any("keystone level 2 supplied" in note for note in with_key.notes)
