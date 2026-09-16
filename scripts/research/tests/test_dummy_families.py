"""Structural family classifier: synthetic facts per family, and the hand-verified witnesses."""

from __future__ import annotations

import pytest

from dummy_semantics.bindings import HookBinding
from dummy_semantics.families import classify_hook, extract_signals
from dummy_semantics.witnesses import WITNESSES


def hb(lst: str, calls: list[dict], tokens: list[str] | None = None, case_ints: list | None = None, **extra) -> HookBinding:
    facts = {"calls": calls, "other_calls": {}, "refs": {}, "tokens": tokens or [], "case_ints": case_ints or []}
    facts.update(extra)
    return HookBinding(list=lst, kind="SpellScript", handler="H", line=1, eff_index=0, eff_name=None, eff_value=None,
                       affected_mask=1, affected_effects=[0], executes=True, can_prevent_default=True, facts=facts)


def call(callee: str, cat: str, args: list[str] | None = None, ints: list[int] | None = None, recv: str = "") -> dict:
    d = {"callee": callee, "cat": cat, "args": args or [], "recv": recv, "line": 1}
    if ints:
        d["ints"] = ints
    return d


def test_cast_child_targets():
    assert classify_hook(hb("OnEffectHitTarget", [call("CastSpell", "cast", ["GetHitUnit()", "SPELL_X"], [1000])]))[:2] == ("cast-child", "hit-unit")
    assert classify_hook(hb("AfterCast", [call("CastSpell", "cast", ["GetCaster()", "SPELL_X"], [1000])]))[:2] == ("cast-child", "caster")
    assert classify_hook(hb("OnEffectProc", [call("CastSpell", "cast", ["eventInfo.GetProcTarget()", "SPELL_X"], [1000])]))[:2] == ("cast-child", "proc-target")


def test_amount_forwarding_and_sources():
    fam, sub, s = classify_hook(hb("OnEffectProc", [call("GetHeal", "amount", recv="eventInfo.GetHealInfo()"), call("GetAmount", "amount", recv="aurEff"),
                                                    call("CalculatePct", "amount"), call("AddSpellMod", "cast", ["SPELLVALUE_BASE_POINT0", "x"]),
                                                    call("CastSpell", "cast", ["eventInfo.GetActionTarget()", "SPELL_ABSORB", "args"], [47753])]))
    assert fam == "cast-child-with-amount" and "heal-copy" in sub and "aura-amount" in sub and s.cast_children == {47753}


def test_choose_random_delayed_pet_consume():
    two = [call("CastSpell", "cast", ["t", "A"], [1001]), call("CastSpell", "cast", ["t", "B"], [1002])]
    assert classify_hook(hb("OnEffectHitTarget", two))[0] == "choose-among-children"
    assert classify_hook(hb("OnEffectHit", [call("roll_chance", "rng"), call("CastSpell", "cast", ["GetCaster()", "A"], [1001])]))[0] == "random-child"
    assert classify_hook(hb("OnEffectHitTarget", [call("AddEventAtOffset", "delay"), call("CastSpell", "cast", ["t", "A"], [1001])]))[0] == "delayed-child"
    assert classify_hook(hb("OnEffectHitTarget", [call("GetGuardianPet", "summon"), call("CastSpell", "cast", ["GetHitUnit()", "A"], [1001], recv="pet")]))[0] == "pet-owner-forward-cast"
    assert classify_hook(hb("OnEffectProc", [call("DropCharge", "aura", recv="GetAura()"), call("CastSpell", "cast", ["GetCaster()", "A"], [1001])]))[0] == "consume-and-cast"


def test_non_cast_families():
    assert classify_hook(hb("AfterEffectRemove", [call("RemoveAurasDueToSpell", "aura", ["SPELL_X"], [5])]))[:2] == ("linked-aura-mutation", "remove")
    assert classify_hook(hb("AfterCast", [call("ModifyCooldown", "cooldown", ["X", "-5s"], [9])]))[0] == "cooldown-mutation"
    assert classify_hook(hb("OnEffectRemove", [call("ModifyPower", "power", ["POWER_ENERGY", "n"])]))[0] == "resource-mutation"
    assert classify_hook(hb("OnEffectLaunch", [call("PreventHitDefaultEffect", "prevent")]))[0] == "suppress-default"
    assert classify_hook(hb("OnEffectLaunchTarget", [call("GetHitUnit", "target"), call("GetGUID", "target")]))[0] == "state-only"
    assert classify_hook(hb("OnEffectHitTarget", [call("GetHitDamage", "amount"), call("AddPct", "amount"), call("SetHitDamage", "amount")]))[0] == "amount-adapter"


def test_hook_role_precedence():
    assert classify_hook(hb("OnObjectAreaTargetSelect", [call("remove_if", "target")]))[:2] == ("target-adapter", "filter")
    assert classify_hook(hb("DoCheckProc", [call("GetSpellInfo", "state", recv="eventInfo")], tokens=["SPELLFAMILY_PALADIN"]))[:2] == ("proc-filter-adapter", "spell-family")
    assert classify_hook(hb("OnCheckCast", [call("IsFriendlyTo", "target")]))[0] == "cast-gate"
    assert classify_hook(hb("DoEffectCalcAmount", [call("GetAmount", "amount", recv="aurEff"), call("AddPct", "amount")]))[:2] == ("amount-adapter", "aura-amount")


def test_helper_argument_ints_become_children():
    s = extract_signals({"calls": [call("HandleBuff", "other", ["A", "B"], [1010, 1011]), call("CastSpell", "cast", ["t", "spellToCast"])],
                         "other_calls": {}, "refs": {}, "tokens": [], "helper_arg_ints": [1010, 1011]})
    assert s.cast_children == {1010, 1011} and s.casts == 1


@pytest.mark.snapshot
def test_witnesses_agree_with_structural_index(dummy_ctx):
    expected_family = {
        (686, "spell_warl_shadow_bolt"): {"cast-child"}, (348, "spell_warl_immolate"): {"cast-child"},
        (774, "spell_dru_cultivation"): {"cast-child"}, (596, "spell_pri_prayerful_litany"): {"amount-adapter"},
        (32175, "spell_sha_stormblast_damage"): {"cast-child-with-amount"}, (47515, "spell_pri_divine_aegis"): {"proc-filter-adapter", "cast-child-with-amount"},
        (53651, "spell_pal_light_s_beacon"): {"proc-filter-adapter", "cast-child-with-amount"}, (8092, "spell_pri_dark_indulgence"): {"random-child"},
        (1784, "spell_rog_stealth"): {"choose-among-children", "linked-aura-mutation"}, (114165, "spell_pal_holy_prism"): {"choose-among-children"},
        (54149, "spell_pal_infusion_of_light"): {"proc-filter-adapter", "cast-child"}, (172, "spell_gen_trigger_exclude_caster_aura_spell"): {"cast-child"},
        (2050, "spell_pri_holy_word_salvation_cooldown_reduction"): {"cooldown-mutation"}, (14914, "spell_pri_empyreal_blaze_extend"): {"amount-adapter"},
        (86949, "spell_mage_cauterize"): {"suppress-default"}, (168534, "spell_sha_mastery_elemental_overload"): {"proc-filter-adapter", "delayed-child"},
        (8092, "spell_pri_inescapable_torment"): {"pet-owner-forward-cast"}, (1943, "spell_rog_rupture"): {"resource-mutation"},
    }
    for (sid, script), fams in expected_family.items():
        got = {h.family for h in dummy_ctx.fi.for_spell(sid) if h.executes and h.script == script}
        assert got == fams, (sid, script, got)
    assert {w["spell"] for w in WITNESSES} >= {k[0] for k in expected_family}


@pytest.mark.snapshot
def test_player_scope_unclassified_hooks_are_all_reported_with_a_reason(dummy_ctx):
    c = dummy_ctx.fi.census(dummy_ctx.player)
    assert c["hooks_by_family"].get("unclassified", 0) == len(c["unclassified_examples"])
    for h in dummy_ctx.fi.hooks:
        if h.executes and h.spell_id in dummy_ctx.player and h.family == "unclassified":
            assert h.sub, h  # the offending action is named


def test_movement_and_direct_damage_are_never_absorbed_into_a_family():
    assert classify_hook(hb("AfterEffectRemove", [call("SetHealth", "power"), call("NearTeleportTo", "movement"), call("CastSpell", "cast", ["unit", "V"], [1001])]))[0] == "unclassified"
    assert classify_hook(hb("OnEffectProc", [call("PreventDefaultAction", "prevent"), call("DealDamage", "direct-damage")]))[0] == "unclassified"


def test_secondary_actions_are_retained_on_the_hook():
    from dummy_semantics.families import action_kinds
    _, _, s = classify_hook(hb("OnEffectProc", [call("CastSpell", "cast", ["caster", "X"], [1001]), call("ResetCooldown", "cooldown", ["Y"], [1002])]))
    assert action_kinds(s) == {"cast", "cooldown"}


@pytest.mark.snapshot
def test_player_scope_unique_hooks_are_exactly_the_movement_and_direct_damage_scripts(dummy_ctx):
    uniq = {(h.spell_id, h.script) for h in dummy_ctx.fi.hooks if h.executes and h.spell_id in dummy_ctx.player and h.family == "unclassified"}
    assert uniq == {(48020, "spell_warl_demonic_circle_teleport"), (342246, "spell_mage_alter_time_aura"),
                    (392988, "spell_pri_divine_image"), (49028, "spell_dk_dancing_rune_weapon")}
