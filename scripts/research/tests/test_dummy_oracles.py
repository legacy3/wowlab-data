"""Pure oracle tests + differential probe for the Dummy-semantics research."""

from __future__ import annotations

import json
import struct
import subprocess
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from dummy_semantics import FailClosed
from dummy_semantics.bindings import EFFECT_ALL, EFFECT_FIRST_FOUND, SPELL_AURA_ANY, SPELL_EFFECT_ANY, affected_mask
from dummy_semantics.conditions import ObjectFacts, TYPEID_PLAYER, TYPEID_UNIT, meets, meets_list
from dummy_semantics.loaders import SPELL_LINK_AURA, SPELL_LINK_CAST, SPELL_LINK_HIT, SPELL_LINK_REMOVE
from dummy_semantics.oracles import (
    Action, SpellAreaRow, add_pct, aura_linked, calculate_pct, compare_values, count_pct_from_max_hp,
    forward_amount_as_basepoints, linked_spell_actions, pet_aura, proc_trigger_spell, spell_area_fits,
    trigger_spell_with_value,
)
from procs.spells import EffectInfo, SpellInfo

PROBE = Path(__file__).resolve().parents[1] / "tools" / "tc_dummy_probe" / "probe"


def mk_info(effects: list[tuple[int, int, int]], targets: list[tuple[int, int]] | None = None) -> SpellInfo:
    effs = []
    for i, (eff, aura, trig) in enumerate(effects):
        ta, tb = (targets[i] if targets else (0, 0))
        effs.append(EffectInfo(index=i, effect=eff, aura=aura, trigger_spell=trig, base_points=0.0, misc0=0, misc1=0,
                               class_mask=0, target_a=ta, target_b=tb, aura_period=0, mechanic=0, row_id=1000 + i, difficulty=0))
    return SpellInfo(id=1, difficulty=0, name="synthetic", attributes=tuple([0] * 17), attributes_cu=0, school_mask=0,
                     effects=tuple(effs), proc_flags=0, proc_chance=0, proc_charges=0, proc_cooldown=0, stack_amount=0,
                     ppm_id=0, base_ppm=0.0, ppm_flags=None, ppm_mods=(), family=0, family_flags=0, dmg_class=0, category=0,
                     charge_category=0, mechanic=0, recovery_time=0, category_recovery_time=0, start_recovery_time=0,
                     equipped_item_class=-1, equipped_item_subclass_mask=0, equipped_item_inventory_type_mask=0, labels=(),
                     duration_ms=None, provenance={})


# -- CalculatePct family ------------------------------------------------------

def test_calculate_pct_truncates_toward_zero_like_cpp():
    assert calculate_pct(1, 7) == 0
    assert calculate_pct(33, 33) == 10          # 10.89 -> 10
    assert calculate_pct(100, 150) == 150
    assert calculate_pct(-100, 50) == -50
    assert add_pct(100, 50) == 150
    assert count_pct_from_max_hp(1234567, 30) == calculate_pct(1234567, 30)


@given(st.integers(min_value=0, max_value=160000))
@settings(max_examples=300)
def test_calculate_pct_identity_at_100_for_exactly_representable_products(base: int):
    # base * 100 < 2^24 is exact in binary32, so /100.0f returns base exactly (source: CalculatePct formula)
    assert calculate_pct(base, 100) == base


@given(st.integers(min_value=0, max_value=1 << 30), st.integers(min_value=0, max_value=100))
@settings(max_examples=300)
def test_calculate_pct_never_exceeds_base_for_pct_le_100(base: int, pct: int):
    # binary32 rounding can round *up* by one ulp; the truncation keeps the result <= base only up to rounding.
    r = calculate_pct(base, pct)
    assert r <= base or (r - base) <= max(1, base >> 23)


def test_compare_values_all_types_and_fail_closed():
    assert compare_values(0, 50.0, 50.0) and not compare_values(1, 50.0, 50.0) and compare_values(3, 50.0, 50.0)
    assert compare_values(2, 49.0, 50.0) and compare_values(4, 50.0, 50.0)
    with pytest.raises(FailClosed):
        compare_values(9, 1.0, 1.0)


# -- differential probe -----------------------------------------------------

@pytest.fixture(scope="module")
def probe_data():
    if not PROBE.exists():
        pytest.skip(f"probe not built; run: make -C {PROBE.parent}")
    return json.loads(subprocess.run([str(PROBE)], capture_output=True, text=True, check=True).stdout)


def test_probe_calculate_pct_matches_oracle(probe_data):
    for c in probe_data["calculate_pct"]:
        if abs(calculate_pct(float(c["base"]), c["pct"], float)) >= 2**31:
            continue  # float -> int32 overflow is undefined behaviour in C++; the probe saturates to INT_MIN
        assert calculate_pct(c["base"], c["pct"], int) == c["int"], c
        assert struct.unpack("f", struct.pack("f", calculate_pct(float(c["base"]), c["pct"], float)))[0] == pytest.approx(c["float"], rel=1e-7, abs=1e-9), c
        if c["base"] >= 0 and c["pct"] >= 0:
            # uint64(negative float) is undefined behaviour in C++ (the probe wraps); compared only where defined
            assert calculate_pct(c["base"], c["pct"], int) == c["uint64"], c
        if abs(c["base"]) < 2**30:  # int32 overflow in the probe's AddPct is undefined; compared only where defined
            assert add_pct(c["base"], c["pct"]) == c["addpct"], c


def test_probe_compare_values_matches_oracle(probe_data):
    for c in probe_data["compare_values"]:
        assert compare_values(c["type"], struct.unpack("f", struct.pack("f", c["value"]))[0], 50.0) is c["result"], c


def test_probe_affected_mask_matches_bindings(probe_data):
    info = mk_info([(6, 4, 0), (3, 0, 0), (6, 42, 0), (77, 0, 0), (6, 4, 0)])
    for c in probe_data["affected_mask"]:
        idx = {254: EFFECT_ALL, 255: EFFECT_FIRST_FOUND}.get(c["index"], c["index"])
        if c["kind"] == "spell":
            v = SPELL_EFFECT_ANY if c["name"] == 0xFFFF else c["name"]
            def check(i, v=v):
                e = info.effect(i)
                return e is not None and (v == SPELL_EFFECT_ANY or e.effect == v)
        else:
            v = SPELL_AURA_ANY if c["name"] == 0xFFFF else c["name"]
            def check(i, v=v):
                e = info.effect(i)
                if e is None:
                    return False
                if not e.aura and v == 0:
                    return True
                if not e.aura:
                    return False
                return v == SPELL_AURA_ANY or e.aura == v
        assert affected_mask(info, idx, check) == c["mask"], c


# -- affected mask invariants (source: GetAffectedEffectsMask) ---------------

@given(st.lists(st.tuples(st.sampled_from([3, 6, 77, 2, 64]), st.sampled_from([0, 4, 42, 23]), st.just(0)), min_size=1, max_size=8),
       st.sampled_from([3, 6, 77, 2, SPELL_EFFECT_ANY]))
@settings(max_examples=200)
def test_first_found_is_lowest_bit_of_all(effects, name):
    info = mk_info(effects)

    def check(i):
        e = info.effect(i)
        return e is not None and (name == SPELL_EFFECT_ANY or e.effect == name)
    all_mask = affected_mask(info, EFFECT_ALL, check)
    first = affected_mask(info, EFFECT_FIRST_FOUND, check)
    assert bin(first).count("1") <= 1
    assert first == (all_mask & -all_mask if all_mask else 0)
    for i in range(len(effects)):
        assert bool(affected_mask(info, i, check)) == bool(all_mask & (1 << i))


# -- generic consumers -------------------------------------------------------

def test_trigger_spell_with_value_sets_every_child_effect():
    a = trigger_spell_with_value(1000, 3, 42.0, with_value=True)
    assert a.kind == "cast" and a.base_points == (42.0, 42.0, 42.0) and a.detail["delay_ms"] == 0
    b = trigger_spell_with_value(1000, 3, 42.0, with_value=False, delay_ms=500)
    assert b.base_points is None and b.detail["delay_ms"] == 500
    with pytest.raises(FailClosed):
        trigger_spell_with_value(0, 1, 1.0, with_value=False)


def test_proc_trigger_spell_caster_selection():
    a = proc_trigger_spell(5, 1, 7.0, with_value=True, triggered_by_caster=True)
    assert a.caster == "aura-caster" and a.base_points == (7.0,)
    b = proc_trigger_spell(5, 1, None, with_value=False, triggered_by_caster=False)
    assert b.caster == "aura-target" and b.base_points is None
    with pytest.raises(FailClosed):
        proc_trigger_spell(0, 1, None, False, True)


def test_aura_linked_apply_remove_reapply():
    assert aura_linked(9, 0.0, apply=True, reapply=False, triggered_by_caster=True)[0].base_points is None
    assert aura_linked(9, 15.0, apply=True, reapply=False, triggered_by_caster=True)[0].base_points == (15.0,)
    assert aura_linked(9, 0.0, apply=False, reapply=False, triggered_by_caster=False)[0].kind == "remove-aura"
    assert aura_linked(9, 0.0, apply=True, reapply=True, triggered_by_caster=True, own_stack=3, existing_child_stack=1)[0].detail["delta"] == 2
    assert aura_linked(0, 0.0, True, False, True) == []


def test_linked_spell_actions_mirror_the_four_consumers():
    linked = {(SPELL_LINK_CAST, 1): [2, -3], (SPELL_LINK_HIT, 1): [4], (SPELL_LINK_AURA, 1): [5, -6], (SPELL_LINK_REMOVE, 1): [7, -8]}
    cast = linked_spell_actions(linked, 1, "cast")
    assert [a.kind for a in cast] == ["cast", "remove-aura"] and cast[1].spell == 3
    hit = linked_spell_actions(linked, 1, "hit")
    assert hit[0].kind == "cast" and hit[0].target == "hit-unit"
    apply = linked_spell_actions(linked, 1, "aura", apply=True)
    assert [(a.kind, a.spell) for a in apply] == [("add-aura", 5), ("apply-immunity", 6)]
    remove = linked_spell_actions(linked, 1, "aura", apply=False)
    assert [(a.kind, a.spell) for a in remove] == [("cast", 7), ("remove-aura", 8), ("remove-aura", 5), ("remove-immunity", 6)]
    death = linked_spell_actions(linked, 1, "aura", apply=False, remove_mode_death=True)
    assert [(a.kind, a.spell) for a in death] == [("remove-aura", 8), ("remove-aura", 5), ("remove-immunity", 6)]
    reapply = linked_spell_actions(linked, 1, "aura", apply=True, reapply=True)
    assert [(a.kind, a.spell) for a in reapply] == [("mod-stacks", 5)]
    with pytest.raises(FailClosed):
        linked_spell_actions(linked, 1, "unknown")


def test_pet_aura_entry_specific_then_generic():
    assert pet_aura(100, 55, 55, None, 0).spell == 100
    assert pet_aura(100, 55, 56, 200, 25).spell == 200
    assert pet_aura(100, 55, 56, 200, 25).base_points == (25.0,)
    assert pet_aura(100, 55, 56, None, 0) is None


def test_spell_area_generic_rules_and_hardcoded_fail_closed():
    row = SpellAreaRow(spell=1, area=10, quest_start=0, quest_end=0, aura_spell=-50, race_mask=0, gender=2, flags=3,
                       quest_start_status=64, quest_end_status=11)
    assert spell_area_fits(row, {"auras": []}, zone=10, area=99)
    assert not spell_area_fits(row, {"auras": [50]}, zone=10, area=99)
    assert not spell_area_fits(row, {"auras": []}, zone=1, area=2)
    with pytest.raises(FailClosed):
        spell_area_fits(SpellAreaRow(91604, 0, 0, 0, 0, 0, 2, 3, 64, 11), None, 0, 0)


def test_forward_amount_declares_basepoints_only():
    a = forward_amount_as_basepoints(47753, 1234, (0,), target="proc-target", caster="actor")
    assert a.to_dict()["base_points"] == (1234.0,) and a.spell == 47753


# -- conditions --------------------------------------------------------------

def row(ctype: int, v1=0, v2=0, v3=0, target=0, negative=0, group=0, src=17):
    return {"SourceTypeOrReferenceId": src, "ConditionTypeOrReference": ctype, "ConditionValue1": v1, "ConditionValue2": v2,
            "ConditionValue3": v3, "ConditionTarget": target, "NegativeCondition": negative, "ElseGroup": group}


def test_condition_aura_class_level_and_negation():
    unit = ObjectFacts(type_id=TYPEID_PLAYER, level=70, class_mask=1 << 3, aura_effects={(36554, 0)}, spells_known={36554})
    assert meets(row(1, 36554, 0), [unit])
    assert not meets(row(1, 36554, 1), [unit])
    assert meets(row(1, 36554, 1, negative=1), [unit])
    assert meets(row(15, 1 << 3), [unit]) and not meets(row(15, 1 << 2), [unit])
    assert meets(row(27, 70, 0), [unit]) and meets(row(27, 60, 1), [unit]) and not meets(row(27, 60, 2), [unit])
    assert meets(row(25, 36554), [unit])
    assert not meets(row(25, 36554), [ObjectFacts(type_id=TYPEID_UNIT, spells_known={36554})])  # not a player
    assert meets(row(21, 0x8), [ObjectFacts(unit_state=0x8)])
    assert meets(row(38, 50, 1), [ObjectFacts(health=80, max_health=100)])


def test_condition_else_groups_are_or_of_ands():
    rows = [row(36, group=0), row(38, 50, 2, group=0), row(1, 999, 0, group=1)]
    healthy = ObjectFacts(alive=True, health=90, max_health=100)
    assert not meets_list(rows, [healthy])        # group0 fails (hp not < 50), group1 fails (no aura)
    healthy.aura_effects.add((999, 0))
    assert meets_list(rows, [healthy])            # group1 passes
    assert meets_list([], [healthy])              # empty list is true


def test_condition_unknown_type_fails_closed():
    with pytest.raises(FailClosed):
        meets(row(12, 1), [ObjectFacts()])
