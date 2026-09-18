"""Track G real-data tests (snapshot 12.1.0.69497 + TDB overlay).  One census per session (~20 s)."""

from __future__ import annotations

import json

import pytest

from aura_lifecycle import CORPORA, FailClosed
from aura_lifecycle import passive as pv


@pytest.fixture(scope="module")
def g_census(al_ctx):
    return pv.census(al_ctx)


def test_effective_passive_includes_trinity_icon_correction(al_ctx, g_census):
    """Rules out 'IsPassive == DB2 ATTR0_PASSIVE' (SpellMgr.cpp:5301 icon correction)."""
    rows = g_census["rows"]
    corrected = [s for s, f in rows.items() if f["passive_by_correction_only"]]
    assert corrected, "expected icon-corrected passives in the snapshot"
    ep = pv.effective_passive(al_ctx.data, corrected[0])
    assert ep["passive"] and not ep["db2_passive"]
    assert any("135754" in r for r in ep["reasons"])


def test_population_counts_are_nested(g_census):
    c = g_census["counts"]
    for pop in ("player", "controlled"):
        for k in pv.CENSUS_KEYS:
            assert c[pop][k] <= c["all"][k], (pop, k)
    for pop in c:
        assert c[pop]["passive"] + c[pop]["active"] + c[pop]["fail_closed"] == c[pop]["providers"]


def test_passive_is_not_immutable_in_player_scope(g_census):
    """Discards AL-R-G-08: player passives carry mutable state."""
    p = g_census["counts"]["player"]
    assert p["passive_any_mutable"] > 0 and p["passive_effective_proc_entry"] > 0


def test_pyrogenics_package_lifecycle(al_ctx):
    prof = pv.profile(al_ctx, 387095)
    assert prof["passive"]["passive"] and prof["max_duration"]["ms"] == -1
    carriers = {m["carrier"]: m for m in prof["mutable_state"]}
    assert carriers["effective-proc-entry"]["value"]["origin"] == "spell_proc"
    assert carriers["script-state"]["value"] == ["spell_warl_pyrogenics"]
    assert prof["reapplication_same_caster_no_item"]["outcome"] == "replace"
    child = pv.profile(al_ctx, 387096)
    assert not child["passive"]["passive"] and child["max_duration"]["kind"] == "finite"


def test_heart_of_the_crusader_proc_signal_is_inert(al_ctx):
    """Agrees with selected-package §12: ProcTypeMask 4 generates no proc entry."""
    carriers = {m["carrier"] for m in pv.profile(al_ctx, 406154)["mutable_state"]}
    assert "inert-proc-signal" in carriers and "effective-proc-entry" not in carriers


def test_phalanx_capacity_is_dormant(al_ctx):
    """CumulativeAura 2 on a passive: no MODIFY_AURA_STACKS writer; re-application replaces (never stacks)."""
    prof = pv.profile(al_ctx, 1269312)
    cap = next(m for m in prof["mutable_state"] if m["carrier"] == "stack-capacity")
    assert cap["value"] == 2 and cap["writers"] == []
    assert prof["reapplication_same_caster_no_item"]["outcome"] == "replace"


def test_veteran_of_the_third_war_login_interrupt(al_ctx):
    f = pv.facts(al_ctx, 48263)
    assert f.is_passive and "Login" in pv.interrupt_flag_names(f.aura_interrupt_flags)
    assert pv.removal_policy(f)["aura-interrupt"]["removed"] is True


def test_non_aura_spell_fails_closed(al_ctx):
    with pytest.raises(FailClosed):
        pv.profile(al_ctx, 133)  # Fireball: one SCHOOL_DAMAGE effect, no aura
    # sanity: the fail-closed reason is the missing aura effect, not a missing row
    assert pv.facts(al_ctx, 133).effects


def test_corpus_counts_regenerate(g_census):
    path = CORPORA / "passive-active.json"
    if not path.exists():
        pytest.skip("passive-active.json not generated")
    corpus = json.loads(path.read_text(encoding="utf-8"))
    assert corpus["census"]["counts"] == g_census["counts"]
