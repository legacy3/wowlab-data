"""Binding resolution, hook registration, default prevention -- against the real corpora."""

from __future__ import annotations

import pytest

from dummy_semantics.bindings import parse_aura_name, parse_eff_index, parse_effect_name, parse_target
from dummy_semantics.hooks import BY_LIST, PREVENTING_LISTS, vocabulary

pytestmark = pytest.mark.snapshot


def test_hook_token_parsers():
    assert parse_eff_index("EFFECT_0") == 0 and parse_eff_index("EFFECT_ALL") == "EFFECT_ALL" and parse_eff_index("foo") is None
    assert parse_effect_name("SPELL_EFFECT_DUMMY") == 3 and parse_effect_name("SPELL_EFFECT_ANY") == "SPELL_EFFECT_ANY"
    assert parse_aura_name("SPELL_AURA_DUMMY") == 4 and parse_aura_name("SPELL_AURA_PERIODIC_DUMMY") == 226
    assert parse_aura_name("SPELL_AURA_NOT_A_THING") is None
    assert parse_target("TARGET_UNIT_CASTER") == 1


def test_hook_vocabulary_covers_every_header_hook_list(dummy_ctx):
    v = vocabulary(dummy_ctx.b.dispatch)
    assert v["cross_check"]["hook_lists_in_header_not_modelled"] == []
    assert v["cross_check"]["modelled_not_in_header"] == []
    # prevent-default rule matches SpellScript.cpp: only these aura hooks honour PreventDefaultAction
    aura_prevent = {h.list for h in __import__("dummy_semantics.hooks", fromlist=["AURA_HOOKS"]).AURA_HOOKS if h.prevent_default == "PreventDefaultAction"}
    assert aura_prevent == {"OnEffectApply", "OnEffectRemove", "OnEffectPeriodic", "OnEffectAbsorb", "OnEffectAbsorbHeal",
                            "OnEffectManaShield", "OnEffectSplit", "DoPrepareProc", "OnProc", "OnEffectProc"}
    assert "AfterEffectRemove" not in PREVENTING_LISTS and "OnEffectHitTarget" in PREVENTING_LISTS


def test_rank_expansion_mirrors_load_spell_script_names(dummy_ctx):
    b = dummy_ctx.b
    negative = [r for r in b.world.table("spell_script_names").rows if r[0] < 0]
    assert negative, "corpus should carry all-ranks rows"
    for spell_id, name in negative:
        first = -spell_id
        if not b.catalog.exists(first) or b.catalog.first_rank(first) != first:
            assert any(err.startswith(name) for err in b.world.script_binding_errors)
            continue
        cur = first
        while cur is not None:
            assert name in b.script_names.get(cur, []), (name, cur)
            cur = b.catalog.next_rank(cur)


def test_holy_prism_binding_executes_on_dummy_effect(dummy_ctx):
    bs = dummy_ctx.bm.bindings(114165)
    assert [x.script_name for x in bs] == ["spell_pal_holy_prism"]
    hook = bs[0].hooks[0]
    assert hook.list == "OnEffectHitTarget" and hook.eff_value == 3 and hook.affected_mask == 1 and hook.executes
    assert hook.can_prevent_default and BY_LIST[hook.list].call_site.startswith("Spell::HandleEffects")


def test_avatar_binding_never_executes_because_effect_layout_differs(dummy_ctx):
    bs = dummy_ctx.bm.bindings(107574)
    hook = bs[0].hooks[0]
    assert hook.eff_index == 5 and hook.eff_value == 77 and hook.affected_mask == 0 and not hook.executes


def test_unresolved_script_names_are_reported_not_guessed(dummy_ctx):
    s = dummy_ctx.bm.summary()
    assert s["unresolved_bindings"] == sum(dummy_ctx.bm.unresolved_names.values())
    for name in dummy_ctx.bm.unresolved_names:
        assert not dummy_ctx.b.index.resolve_script_name(name)["resolved"]


def test_load_gate_extracted_for_talent_gated_scripts(dummy_ctx):
    bs = {b.script_name: b for b in dummy_ctx.bm.bindings(596)}  # Prayer of Healing
    lit = bs["spell_pri_prayerful_litany"]
    assert "HasAuraEffect" in lit.load_gate["callees"] and 391209 in lit.load_gate["spells"] or lit.load_gate["spells"]


def test_generic_exclude_aura_scripts_are_db2_parameterised(dummy_ctx):
    fam = [h for h in dummy_ctx.fi.hooks if h.script in ("spell_gen_trigger_exclude_caster_aura_spell", "spell_gen_trigger_exclude_target_aura_spell") and h.executes]
    assert fam and all(h.family == "cast-child" and h.sub == "aura-spell-field" for h in fam)


def test_inheritance_resolves_scripts_derived_from_helper_bases(dummy_ctx):
    idx = dummy_ctx.b.index
    for name in ("spell_dru_berserk", "spell_dru_incapacitating_roar", "spell_dru_stampeding_roar"):
        sc = idx.resolve_class(name)
        assert sc.kind == "SpellScript" and "spell_dru_base_transformer" in sc.resolved_bases and sc.hooks
    assert dummy_ctx.bm.unresolved_names.get("spell_dru_berserk", 0) == 0


def test_namespace_helpers_and_cross_class_statics_are_merged(dummy_ctx):
    idx = dummy_ctx.b.index
    sc = idx.resolve_class("spell_gen_major_healing_cooldown_modifier")
    m = idx.merged_facts(sc, "CalculateHealingBonus")
    assert "GetBonusMultiplier" in m["via"] and any(c["callee"] == "GetAuraEffect" for c in m["calls"])
    sc = idx.resolve_class("spell_dh_shattered_souls_devourer")
    m = idx.merged_facts(sc, "HandleProc")
    assert m["cross_class"] == ["spell_dh_shattered_souls_base_lesser"] and any(c["callee"] == "CastSpell" for c in m["calls"])
    sc = idx.resolve_class("spell_rog_killing_spree")
    assert idx.merged_facts(sc, "HandleDummy")["cross_script"] == ["spell_rog_killing_spree_aura"]
    sc = idx.resolve_class("spell_pri_halo_effect_selector")
    assert idx.merged_facts(sc, "PreventHitDefaultEffect")["calls"][0]["cat"] == "prevent"
