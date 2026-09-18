"""Track I: generation safety (holders census, facts, refresh-path census)."""

from __future__ import annotations

import pytest

from aura_lifecycle import TC_ROOT, generation, ordering


def _need_tc() -> None:
    if not (TC_ROOT / "src" / "server" / "game").is_dir():
        pytest.skip("sibling TrinityCore checkout absent")


def test_every_aura_pointer_member_is_classified() -> None:
    """A new Aura*/AuraEffect*/AuraApplication* member in Trinity fails this test until classified."""
    _need_tc()
    r = generation.scan_holders(TC_ROOT)
    assert r["unclassified"] == []
    assert r["classified_but_not_found"] == []
    risky = [m["key"] for m in r["members"] if m["risk"].startswith("stale")]
    assert risky == ["AreaTrigger.h:_aurEff"]


def test_is_removed_guard_sites() -> None:
    _need_tc()
    sites = generation.count_is_removed(TC_ROOT)
    assert sites["game/Spells/SpellEffects.cpp"] == 1          # EffectApplyAura guard on _spellAura
    assert sites["game/Spells/Auras/SpellAuras.cpp"] >= 5


def test_reapply_after_removal_is_new_generation() -> None:
    """Rules out object reuse: expiry at 1000 then reapply -> generation 2 with fresh timers."""
    r = ordering.simulate(ordering.SCENARIOS["reapply-after-expiry-new-generation"]["script"])
    assert r["auras"]["A"]["generations"] == 2
    creates = [e for e in r["log"] if e["ev"] == "create"]
    assert [c["gen"] for c in creates] == [1, 2]


def test_removed_aura_cannot_be_refreshed_in_same_tick() -> None:
    """Rules out 'a removed-but-undeleted aura can be found': the refresh misses and Create runs."""
    r = ordering.simulate(ordering.SCENARIOS["remove-then-refresh-same-tick"]["script"])
    evs = [e["ev"] for e in r["log"] if e["t"] == 500]
    assert evs == ["unapply", "refresh-miss", "create"]


def test_refresh_keeps_object() -> None:
    r = ordering.simulate(ordering.SCENARIOS["refresh-before-due-tick"]["script"])
    assert r["auras"]["A"]["generations"] == 1


def test_facts_reference_scenarios() -> None:
    for f in generation.FACTS:
        for s in f["scenarios"]:
            assert s in ordering.SCENARIOS


def test_refresh_path_census_equals_track_b(al_ctx) -> None:
    """R2-03: the pandemic split is Track B's definition; counts equal carryover.json census.carry."""
    import json
    from aura_lifecycle import CORPORA, overlays, providers
    pops = providers.populations(al_ctx, with_class_skills=True)
    rp = ordering.refresh_path_census(al_ctx.data, pops, providers.provider_effects(al_ctx.data),
                                      overlays.surface_flags(al_ctx))
    carry = CORPORA / "carryover.json"
    if carry.exists():
        b = json.loads(carry.read_text(encoding="utf-8"))["census"]["carry"]
        for pop in ("all", "player", "controlled"):
            for branch, v in rp["branches"].items():
                assert v["counts"][pop] == b[pop].get(branch, 0), (pop, branch)
    first = rp["branches"]["pandemic-reads-refreshed-duration"]
    assert 980 in first["player_spells"] and 980 not in first["player_spells_externally_touched"]
    assert first["channels_cancel_then_create"]["all"] == 0      # B classify already splits channels out


def test_death_survivor_census(al_ctx) -> None:
    from aura_lifecycle import providers
    pops = providers.populations(al_ctx, with_class_skills=True)
    r = ordering.death_survivor_census(al_ctx.data, pops, providers.provider_effects(al_ctx.data))
    assert "trigger-cast-on-corpse" in r["by_tick_kind"] and r["by_tick_kind"]["trigger-cast-on-corpse"]["all"] > 0


def test_survivor_still_ticks_after_death() -> None:
    """R2-11: a death-persistent aura with a higher SpellId than the killer still ticks at the death ms."""
    r = ordering.simulate(ordering.SCENARIOS["death-persistent-survivor-ticks"]["script"])
    evs = [(e["ev"], e["aura"]) for e in r["log"] if e["t"] == 1000]
    assert evs == [("tick", "A"), ("death", ""), ("unapply", "A"), ("tick", "B")]
