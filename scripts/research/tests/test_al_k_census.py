"""Track K: snapshot-backed navigation counts (fixture ``al_ctx``).

The counts only say how many provider spells carry the DB2 side of a Core
lifecycle trigger; they never imply Core admits them.
"""

from __future__ import annotations

import json

import pytest

from aura_lifecycle import CORPORA
from aura_lifecycle import coremap as k

CORPUS = CORPORA / "core-navigation.json"


@pytest.fixture(scope="module")
def table(al_ctx):
    try:
        return k.attribute_table(al_ctx)
    except k.FailClosed as exc:  # Core / Trinity absent
        pytest.skip(str(exc))


def test_population_counts_are_nested(table):
    for row in table:
        c = row["provider_spell_carriers"]
        assert c["player"] <= c["all"] and c["controlled"] <= c["all"], row["raw"]


def test_attribute_rows_are_sorted_and_named(table):
    assert [r["raw"] for r in table] == sorted(k.LIFECYCLE_ATTRIBUTES)
    assert all(r["trinity_name"] for r in table)


def test_pandemic_carriers_reach_player_population(table):
    """Raw 436 is the only DB2 input of Core CappedCarryover; it must reach the player population to matter."""
    row = next(r for r in table if r["raw"] == 436)
    assert row["core_support"] == "implemented"
    assert row["provider_spell_carriers"]["player"] > 0


def test_policy_trigger_proxies_are_consistent(al_ctx, table):
    triggers = k.policy_trigger_counts(al_ctx)
    by_raw = {r["raw"]: r["provider_spell_carriers"] for r in table}
    for pop in ("all", "player", "controlled"):
        assert (triggers["aura_unique_cumulative_0"][pop] + triggers["aura_unique_cumulative_positive"][pop]
                == by_raw[43][pop])
        assert (triggers["asynchronous_stacking_490_cumulative_gt1"][pop]
                + triggers["asynchronous_stacking_490_cumulative_le1"][pop] == by_raw[490][pop])
        assert triggers["periodic_refresh_extends_436"][pop] == by_raw[436][pop]


@pytest.mark.skipif(not CORPUS.exists(), reason="core-navigation.json not generated")
def test_committed_census_is_fresh(al_ctx, table):
    payload = json.loads(CORPUS.read_text(encoding="utf-8"))
    assert payload["lifecycle_attributes"] == json.loads(json.dumps(table))
    assert payload["subtype_coverage"] == k.subtype_coverage(al_ctx)
