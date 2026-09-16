"""Population, markers, proc cross-reference, per-spec census and reproducibility -- real corpora."""

from __future__ import annotations

import json

import pytest

from dummy_semantics import CORPORA
from dummy_semantics.census import BUCKETS, FinalCensus
from dummy_semantics.hardcoded import classify_domain, function_role, normalize_body
from dummy_semantics.procxref import ProcCrossReference
from dummy_semantics.specs import STATUS_ORDER, StatusResolver

pytestmark = pytest.mark.snapshot


def test_population_partitions_and_scope_nesting(dummy_ctx):
    ctx = dummy_ctx
    allc, player = ctx.pop.counts(), ctx.pop.counts(ctx.player)
    assert player["effects"] <= allc["effects"] and player["owners"] <= allc["owners"]
    for cls, v in player["by_class"].items():
        assert v["effects"] >= v["owners"] and v["effects"] <= allc["by_class"][cls]["effects"]
    assert all(r.difficulty == 0 for r in ctx.pop.records.values())
    assert ctx.player <= set(ctx.b.catalog.spell_ids)
    assert set(ctx.scope.roots.by_spell) <= ctx.player


def test_every_dummy_aura_owner_is_classified_and_fail_closed_unobserved_is_explicit(dummy_ctx):
    ctx = dummy_ctx
    c = ctx.mk.census(ctx.player)
    assert c["marker_consumed_owners"] + c["unobserved_and_unbound_owners"] <= c["dummy_aura_owners"]
    for sid in c["unobserved_examples"]:
        k = ctx.mk.classify(sid)
        assert not k["marker_consumed"] and not ctx.bm.bindings(sid)


def test_hardcoded_classifiers():
    assert classify_domain("(m_spellInfo->Id)", True) == "spell"
    assert classify_domain("(GetEntry())", False) == "creature"
    assert classify_domain("(spellInfo->Id)", False) == "spell-like-missing"
    assert function_role("Spell::EffectScriptEffect")[0] == "gameplay-semantic"
    assert function_role("SpellInfo::_LoadAuraState")[0] == "source-classification"
    assert normalize_body({"CastSpell": 1}, [1000]) == "CastSpell:child" and normalize_body({}, []) == "no-action"


def test_hardcoded_player_sites_are_mostly_classification_not_gameplay(dummy_ctx):
    c = dummy_ctx.hc.census(dummy_ctx.player)
    assert c["by_role"].get("gameplay-semantic", 0) <= 5
    assert all(s.domain == "spell" for s in dummy_ctx.hc.spell_sites(dummy_ctx.player))


def test_proc_xref_resolves_every_inert_mixed_and_script_provider(dummy_ctx):
    ctx = dummy_ctx
    x = ProcCrossReference(ctx.b, ctx.bm, ctx.fi, ctx.mk)
    res = x.resolve_all(player_only=True)
    s = res["summary"]
    assert s["proc_census_player_providers"] == 1195
    assert s["proc_buckets"] == {"inert-only": 550, "mixed": 78, "script": 130}
    assert sum(s["classification_total"].values()) == s["population"] == 758
    # strict partition: every provider has exactly one classification and one proc bucket
    assert sum(sum(v.values()) for v in s["classification_by_bucket"].values()) == 758
    for sid, r in res["providers"].items():
        assert r["classification"] in ("marker-only", "reusable-family", "target-adapter", "amount-adapter", "ordinary-trigger",
                                       "genuinely-unique", "unresolved-unbound", "build-skew", "unresolved-bound-no-executing-hook", "state-only-script")
        if r["classification"] == "build-skew":
            assert r["newer_than_trinity"] and not r["scripts"]
        if r["classification"] == "unresolved-unbound":
            assert not r["scripts"] and not r["marker_observers"] or set(r["marker_observers"]) <= {"trait-node", "proc-provider", "script-validate", "script-own-amount", "engine-classification"}


def test_final_census_buckets_partition_owners(dummy_ctx):
    ctx = dummy_ctx
    fc = FinalCensus(ctx.b, ctx.scope, ctx.pop, ctx.bm, ctx.fi, ctx.mk, ctx.hc, ctx.cd, ctx.corr)
    c = fc.run(ctx.player, "player")
    assert sum(c["owners_by_bucket"].values()) == c["owners"]
    assert set(c["owners_by_bucket"]) <= set(BUCKETS)
    assert c["unique_owner_count"] == len(c["unique_owner_ids"])
    assert c["unresolved_count"] == c["owners_by_bucket"].get("unresolved", 0) + c["owners_by_bucket"].get("unresolved-build-skew", 0)
    assert c["bound_owners_without_population_effect"] == c["owners"] - ctx.pop.counts(ctx.player)["owners"]


def test_spec_census_statuses_partition(dummy_ctx):
    ctx = dummy_ctx
    r = StatusResolver(ctx.b, ctx.pop, ctx.bm, ctx.fi, ctx.mk, ctx.hc, ctx.cd)
    spec, spells = next(iter(sorted(ctx.scope.specs_reach.items())))
    c = r.census_for(spells, "one spec")
    assert sum(c["owner_status"].values()) == c["owners_with_population_effects"]
    assert set(c["owner_status"]) <= set(STATUS_ORDER)


def test_committed_corpora_are_reproducible(dummy_ctx):
    """The committed census must equal a fresh run on the same snapshot/corpora."""
    ctx = dummy_ctx
    committed = json.loads((CORPORA / "census.json").read_text())
    fc = FinalCensus(ctx.b, ctx.scope, ctx.pop, ctx.bm, ctx.fi, ctx.mk, ctx.hc, ctx.cd, ctx.corr)
    fresh = fc.run(ctx.player, "player")
    for key in ("owners", "effects", "owners_by_bucket", "effects_by_bucket", "family_hook_counts", "unresolved_profile"):
        assert fresh[key] == committed["player"][key], key
    pop = json.loads((CORPORA / "population.json").read_text())
    assert pop["player"] == ctx.pop.counts(ctx.player)


def test_world_overlay_provenance_pins_the_trinity_commit(dummy_ctx):
    b = dummy_ctx.b
    assert b.world.provenance["trinitycore_commit"] == b.index.provenance["trinitycore_commit"] == b.dispatch.provenance["trinitycore_commit"]
    assert b.world.provenance["unparsed_statement_count"] == 0
    assert b.world.table("spell_scripts").row_count_total == 0  # base 93 rows, all deleted by updates; no loader in 7f3d43b
