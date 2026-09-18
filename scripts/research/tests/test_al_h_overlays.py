"""Track H real-data tests: external lifecycle surfaces of current witnesses (fixture ``al_ctx``)."""

from __future__ import annotations

import pytest

from aura_lifecycle import POLICY_SOURCE, FailClosed, records
from aura_lifecycle import overlays as ov


def kinds(surf, surface):
    return {t["kind"] for t in surf.get(surface, [])}


def test_beacon_link_aura_cascade(al_ctx):
    """LINK_AURA couples removal and stacks, not duration (rules out shared lifetime)."""
    parent = ov.external_surfaces(al_ctx, 53563)
    child = ov.external_surfaces(al_ctx, 53651)
    assert any(t["mode"] == "cascade" and t.get("target") == 53651 for t in parent["removal"])
    assert any(t["mode"] == "cascade" and t.get("target") == 53651 for t in parent["stacks"])
    assert any(t["mode"] == "replace" and t.get("source") == 53563 for t in child["stacks"])
    assert "duration" not in child or "spell_linked_spell" not in kinds(child, "duration")


def test_negative_trigger_is_on_remove(al_ctx):
    """Arcane Missiles 5143 -> -36032: the removal of 5143 removes 36032 (loader rewrites to LINK_REMOVE)."""
    idx = ov.index(al_ctx)
    assert -36032 in idx.links.get((ov.LINK_REMOVE, 5143), [])
    surf = ov.external_surfaces(al_ctx, 36032)
    assert any(t.get("source") == 5143 and t["kind"] == "spell_linked_spell" for t in surf["removal"])


def test_rejuvenation_refresh_is_script_touched(al_ctx):
    """REAPPLY-registered AfterEffectApply makes refresh externally touched; family-flag branch on apply."""
    surf = ov.external_surfaces(al_ctx, 774)
    assert "aura-script" in kinds(surf, "refresh")
    assert "engine_family_flag" in kinds(surf, "application")
    assert ov.policy(al_ctx, 774)["refresh"] == "combined"


def test_remove_mode_conditional_witnesses(al_ctx):
    """Death is a distinct remove mode for external code (Lifebloom, Rupture)."""
    idx = ov.index(al_ctx)
    by_spell = {}
    for r in idx.remove_mode_handlers:
        by_spell.setdefault(r["spell"], set()).update(r["modes"])
    assert {"AURA_REMOVE_BY_EXPIRE", "AURA_REMOVE_BY_ENEMY_SPELL"} <= by_spell[33763]
    assert "AURA_REMOVE_BY_DEATH" in by_spell[1943]


def test_spell_proc_lifecycle_overrides(al_ctx):
    """spell_proc decides charges (Rime 59052: 1 vs DB2 0) and stack consumption (Clearcasting 16870)."""
    rime = ov.external_surfaces(al_ctx, 59052)["charges"]
    assert any(t["kind"] == "spell_proc" and t.get("value") == 1 and t.get("db2") == 0 for t in rime)
    cc = ov.external_surfaces(al_ctx, 16870)
    assert "spell_proc" in kinds(cc, "stacks")
    assert ov.policy(al_ctx, 16870)["charges"] == "world-overlay"


def test_implied_absorb_macro_hooks_execute(al_ctx):
    """2-arg absorb macros imply SCHOOL_ABSORB: PW:S 17 AfterEffectAbsorb is a live amount hook."""
    idx = ov.index(al_ctx)
    assert idx.implied_aura_hooks > 0
    rows = [r for r in idx.hook_rows if r["spell"] == 17 and r["hook"] == "AfterEffectAbsorb"]
    assert rows and "amount" in rows[0]["surfaces"]


def test_explain_shape_and_policy_vocabulary(al_ctx):
    out = ov.explain(al_ctx, 774)
    assert set(out["policy"]) == set(ov.SURFACES)
    assert set(out["policy"].values()) <= set(POLICY_SOURCE)
    assert "player" in out["populations"]
    assert any(s["script"] == "spell_dru_abundance" for s in out["scripts"])


def test_explain_fails_closed_on_unknown_spell(al_ctx):
    with pytest.raises(FailClosed):
        ov.explain(al_ctx, 2_000_000_000)


def test_serverside_providers_are_not_db2(al_ctx):
    """Absence in DB2 is not absence: serverside aura spells exist; colliding rows are rejected."""
    idx = ov.index(al_ctx)
    assert idx.serverside_providers
    assert not any((s, 0) in al_ctx.catalog.keys for s in idx.serverside_providers)
    sid = min(idx.serverside_providers)
    assert ov.explain(al_ctx, sid)["serverside"]["aura_effects"]


@pytest.fixture(scope="module")
def cen(al_ctx):
    return ov.census(al_ctx, with_class_skills=False)


def test_census_counts_consistent(cen, al_ctx):
    from aura_lifecycle.providers import populations
    pops = populations(al_ctx)
    for name in ("all", "player", "controlled"):
        p = cen["populations"][name]
        assert p["providers"] == len(pops[name])
        assert p["touched_any_surface"] + p["untouched"] == p["providers"]
        for s in p["surfaces"].values():
            assert s["touched_spells"] <= p["touched_any_surface"]
            assert sum(s["by_policy"].values()) == s["touched_spells"]
    assert cen["populations"]["player"]["touched_any_surface"] < cen["populations"]["player"]["providers"]


def test_untouched_provider_gets_baseline(cen, al_ctx):
    from aura_lifecycle.providers import populations
    touched = {int(s) for s in cen["all_touched"]}
    spell = min(s for s in populations(al_ctx)["player"] if s not in touched)
    assert ov.policy(al_ctx, spell) == {k: v[1] for k, v in ov.SURFACES.items()}


def test_findings_and_timelines_validate(cen, al_ctx):
    cen = dict(cen)
    cen["populations"] = dict(cen["populations"])
    f = ov.findings(al_ctx, cen)
    records.validate_corpus(f)
    tl = {t["id"]: t for t in ov.timelines(al_ctx)}
    assert set(tl) == {"AL-T-H-01", "AL-T-H-02", "AL-T-H-03", "AL-T-H-04"}
    refresh = tl["AL-T-H-02"]["events"][1]["callbacks"]
    assert "eff0:AfterEffectApply" in refresh and "eff0:AfterEffectRemove" not in refresh
