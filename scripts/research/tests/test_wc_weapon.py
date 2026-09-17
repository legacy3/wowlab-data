"""B1 prepared weapon model: pure mirrors, equip gates, real-item preparation, corpus shape."""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pytest

RESEARCH_ROOT = Path(__file__).resolve().parents[1]
if str(RESEARCH_ROOT) not in sys.path:
    sys.path.insert(0, str(RESEARCH_ROOT))

from procs.chance import add, div, f32, lit, mul  # noqa: E402
from weapon_combat import CORPORA, EVIDENCE_CLASSES  # noqa: E402
from weapon_combat.weapon import (  # noqa: E402
    AP_MULTIPLIER_FLOOR, BASE_ATTACK, BASE_ATTACK_TIME, BASE_MAXDAMAGE, BASE_MINDAMAGE, INVTYPE_2HWEAPON,
    INVTYPE_WEAPON, INVTYPE_WEAPONOFFHAND, ITEM_CLASS_WEAPON, MinMaxInputs, NORMALIZED_SPEED_BY_SUBCLASS,
    OFF_ATTACK, RANGED_ATTACK, WeaponItem, ap_multiplier, attack_by_slot, calculate_min_max, check_equip,
    is_ranged_weapon, load_weapon_item, prepare_hands, total_attack_power, urand_bounds, weapon_attack_power,
    default_skill_grants, weapon_sources_rows, CHARACTER_PREP_HOOK)


def make_item(item_id: int, inventory_type: int, subclass: int, *, class_id: int = ITEM_CLASS_WEAPON,
              delay: int = 2600, dps: float = 72.46403503418, variance: float = 0.5,
              always_dw: bool = False) -> WeaponItem:
    avg = dps * delay * 0.001
    mn = (variance * -0.5 + 1.0) * avg
    mx = math.floor(avg * (variance * 0.5 + 1.0) + 0.5)
    return WeaponItem(item_id=item_id, name=f"item {item_id}", class_id=class_id, subclass=subclass, subclass_name="x",
                      inventory_type=inventory_type, item_level=292, context=3, delay_ms=delay, dmg_variance=variance,
                      dps=f32(dps), min_damage_f32=f32(mn), max_damage_f32=f32(mx), min_damage_gearing=mn,
                      max_damage_gearing=mx, weapon_attack_power=weapon_attack_power(dps), damage_type=0, school_mask=1,
                      always_allow_dual_wield=always_dw, is_ranged_weapon=is_ranged_weapon(class_id, subclass))


# --- constants and pure mirrors -------------------------------------------------

def test_normalized_speed_table_matches_unit_cpp_11063_11085():
    assert NORMALIZED_SPEED_BY_SUBCLASS[15] == lit("1.7")          # dagger
    assert NORMALIZED_SPEED_BY_SUBCLASS[16] == lit("2.0")          # thrown
    for two_hand in (1, 5, 6, 8, 10, 20):
        assert NORMALIZED_SPEED_BY_SUBCLASS[two_hand] == lit("3.3")
    for one_hand in (0, 4, 7, 9, 11, 12, 13):
        assert NORMALIZED_SPEED_BY_SUBCLASS[one_hand] == lit("2.4")
    assert 2 not in NORMALIZED_SPEED_BY_SUBCLASS and 3 not in NORMALIZED_SPEED_BY_SUBCLASS   # bow/gun fall to delay/1000


def test_ap_multiplier_branches():
    assert ap_multiplier(is_player=True, feral=False, base_attack_time_ms=2000, weapon=None, normalized=False)[0] == 2.0
    v, branch = ap_multiplier(is_player=True, feral=False, base_attack_time_ms=2000, weapon=(2600, 15), normalized=False)
    assert v == div(f32(2600), lit("1000.0")) and "GetDelay" in branch
    assert ap_multiplier(is_player=True, feral=False, base_attack_time_ms=2000, weapon=(2600, 15), normalized=True)[0] == lit("1.7")
    assert ap_multiplier(is_player=True, feral=False, base_attack_time_ms=2000, weapon=(3000, 2), normalized=True)[0] == lit("3.0")
    # feral, non-normalized: base attack time wins even with a weapon
    assert ap_multiplier(is_player=True, feral=True, base_attack_time_ms=1000, weapon=(2600, 10), normalized=False)[0] == lit("1.0")
    assert ap_multiplier(is_player=True, feral=True, base_attack_time_ms=1000, weapon=(2600, 10), normalized=True)[0] == lit("3.3")
    assert ap_multiplier(is_player=False, feral=False, base_attack_time_ms=1500, weapon=(2600, 10), normalized=True)[0] == lit("1.5")


def test_total_attack_power_int_sum_then_float_and_offhand_halving():
    # int32 sum happens before the float conversion: 16777217 + 0 + 0 is exact as int, rounds as float
    assert total_attack_power(att_type=BASE_ATTACK, include_weapon=False, ap=16777216, mod_pos=1, mod_neg=0, multiplier=0.0) == 16777216.0
    assert total_attack_power(att_type=OFF_ATTACK, include_weapon=True, ap=1000, mod_pos=0, mod_neg=0, multiplier=0.0, weapon_ap_oh=200) == 600.0
    # includeWeapon=false: no halving for the off hand (StatSystem.cpp:447 passes false)
    assert total_attack_power(att_type=OFF_ATTACK, include_weapon=False, ap=1000, mod_pos=0, mod_neg=0, multiplier=0.0, weapon_ap_oh=200) == 1000.0
    assert total_attack_power(att_type=BASE_ATTACK, include_weapon=True, ap=1000, mod_pos=0, mod_neg=0, multiplier=0.0, weapon_ap_mh=300, weapon_ap_ranged=434) == 1434.0
    assert total_attack_power(att_type=BASE_ATTACK, include_weapon=False, ap=-5, mod_pos=0, mod_neg=0, multiplier=0.5) == 0.0
    assert total_attack_power(att_type=RANGED_ATTACK, include_weapon=False, ap=1000, mod_pos=0, mod_neg=0, multiplier=0.1) == mul(f32(1000), f32(1.1))


def test_calculate_min_max_plain_formula():
    inp = MinMaxInputs(att_type=BASE_ATTACK, normalized=False, add_total_pct=True, ap=10000, ap_mod_pos=500,
                       weapon_min=100.5, weapon_max=150.0, weapon=(2600, 15), versatility_rating_pct=3.25)
    r = calculate_min_max(inp)
    ap_term = mul(div(f32(10500), lit("3.5")), div(f32(2600), lit("1000.0")))
    versa = add(lit("1.0"), div(mul(lit("1.0"), f32(3.25)), lit("100.0")))     # AddPct<float,float>
    assert r["ap_term"] == ap_term and r["versatility_mod"] == versa
    base_value = add(0.0, ap_term)
    # StatSystem.cpp:478 ((wmin + baseValue) * basePct + totalValue) * totalPct * versaDmgMod, left to right in float
    expected = mul(mul(add(mul(add(f32(100.5), base_value), 1.0), 0.0), 1.0), versa)
    assert r["min"] == expected
    assert r["min"] != (100.5 + 10500 / 3.5 * 2.6) * 1.0325      # the double evaluation differs
    assert r["min"] < r["max"]


def test_calculate_min_max_ap_floor_and_no_weapon():
    fast = MinMaxInputs(att_type=BASE_ATTACK, normalized=False, add_total_pct=True, ap=3500, weapon=(200, 15), weapon_min=1, weapon_max=2)
    assert calculate_min_max(fast)["attack_power_mod"] == AP_MULTIPLIER_FLOOR
    none = MinMaxInputs(att_type=BASE_ATTACK, normalized=False, add_total_pct=True, ap=3500, weapon=None, weapon_min=1, weapon_max=2)
    r = calculate_min_max(none)
    assert r["ap_multiplier"] == 2.0 and r["ap_term"] == 2000.0 and r["min"] == 2001.0 and r["max"] == 2002.0


def test_calculate_min_max_offhand_without_weapon_reads_zero_range():
    inp = MinMaxInputs(att_type=OFF_ATTACK, normalized=False, add_total_pct=True, ap=3500, weapon=None,
                       weapon_min=999, weapon_max=999, have_offhand_weapon=False)
    r = calculate_min_max(inp)
    assert r["weapon_min_used"] == 0.0 and r["min"] == 2000.0


def test_calculate_min_max_disarm_and_form_branches():
    dis_off = MinMaxInputs(att_type=OFF_ATTACK, normalized=False, add_total_pct=True, can_use_attack_type=False, have_offhand_weapon=True, weapon=(2600, 15))
    assert calculate_min_max(dis_off)["min"] == 0.0
    dis_mh = MinMaxInputs(att_type=BASE_ATTACK, normalized=False, add_total_pct=True, can_use_attack_type=False, weapon=None, weapon_min=500, weapon_max=600)
    r = calculate_min_max(dis_mh)
    assert r["weapon_min_used"] == BASE_MINDAMAGE and r["weapon_max_used"] == BASE_MAXDAMAGE
    # cat form: apmod = CombatRoundTime/1000; weapon * CRT / 1000 / apmod == weapon (float arithmetic)
    cat = MinMaxInputs(att_type=BASE_ATTACK, normalized=False, add_total_pct=True, feral=True, base_attack_time_ms=1000,
                       form_combat_round_time=1000, weapon=(2600, 10), weapon_min=100.0, weapon_max=200.0)
    r = calculate_min_max(cat)
    assert r["branch"] == "shapeshift CombatRoundTime" and r["attack_power_mod"] == 1.0
    assert r["weapon_min_used"] == 100.0 and r["weapon_max_used"] == 200.0


def test_urand_bounds_clamp_swap_truncate():
    assert urand_bounds(141.9, 236.2) == (141, 236)
    assert urand_bounds(-3.0, 5.7) == (0, 5)
    assert urand_bounds(10.0, 4.0) == (4, 10)


def test_weapon_attack_power_truncates_float_product():
    assert weapon_attack_power(72.46403503418) == 434     # 434.78 -> int32
    assert weapon_attack_power(0.0) == 0


def test_attack_by_slot_and_ranged_weapon():
    assert attack_by_slot("main_hand", INVTYPE_2HWEAPON) == BASE_ATTACK
    assert attack_by_slot("main_hand", 15) == RANGED_ATTACK and attack_by_slot("main_hand", 26) == RANGED_ATTACK
    assert attack_by_slot("off_hand", INVTYPE_WEAPON) == OFF_ATTACK and attack_by_slot("head", 1) is None
    assert is_ranged_weapon(2, 2) and is_ranged_weapon(2, 19) and not is_ranged_weapon(2, 15) and not is_ranged_weapon(4, 2)


# --- equip gates -----------------------------------------------------------------

def test_check_equip_two_hand_main_hand_blocks_offhand_without_titan_grip():
    mh = make_item(1, INVTYPE_2HWEAPON, 8)
    oh = make_item(2, INVTYPE_WEAPON, 0)
    s = check_equip(mh, oh, dual_wield=True, titan_grip=False, titan_grip_subclass_mask=0)
    assert s.two_hand_used and any("EQUIP_ERR_2HANDED_EQUIPPED" in e for e in s.equip_errors) and not s.dual_wielding
    s2 = check_equip(mh, make_item(3, INVTYPE_2HWEAPON, 1), dual_wield=True, titan_grip=True, titan_grip_subclass_mask=(1 << 8) | (1 << 1))
    assert not s2.two_hand_used and s2.equip_errors == [] and s2.dual_wielding
    # Titan's Grip mask excludes polearms (subclass 6)
    s3 = check_equip(mh, make_item(4, INVTYPE_2HWEAPON, 6), dual_wield=True, titan_grip=True, titan_grip_subclass_mask=(1 << 8) | (1 << 1))
    assert any("CanTitanGrip" in e for e in s3.equip_errors)


def test_check_equip_dual_wield_and_flag_gates():
    mh = make_item(1, INVTYPE_WEAPON, 7)
    assert any("needs CanDualWield" in e for e in check_equip(mh, make_item(2, INVTYPE_WEAPON, 0), dual_wield=False, titan_grip=False, titan_grip_subclass_mask=0).equip_errors)
    assert check_equip(mh, make_item(2, INVTYPE_WEAPONOFFHAND, 0, always_dw=True), dual_wield=False, titan_grip=False, titan_grip_subclass_mask=0).equip_errors == []
    assert any("WRONG_SLOT" in e for e in check_equip(mh, make_item(2, INVTYPE_WEAPON, 6), dual_wield=True, titan_grip=False, titan_grip_subclass_mask=0).equip_errors)
    shield = make_item(9, 14, 6, class_id=4)
    s = check_equip(mh, shield, dual_wield=False, titan_grip=False, titan_grip_subclass_mask=0)
    assert s.equip_errors == [] and not s.dual_wielding


# --- prepared hands (synthetic) --------------------------------------------------

def test_prepare_hands_offhand_penalty_and_ranged_placeholder():
    mh = make_item(1, INVTYPE_WEAPON, 7)
    oh = make_item(2, INVTYPE_WEAPON, 0, delay=2600)
    state = check_equip(mh, oh, dual_wield=True, titan_grip=False, titan_grip_subclass_mask=0)
    hands = prepare_hands(state, level=90, form_id=None, form_combat_round_time=0, ap=3500, ap_mod_pos=0, ap_mod_neg=0,
                          ap_multiplier_value=0.0, versatility_pct=0.0, mods={})
    assert [h.attack_type for h in hands] == [BASE_ATTACK, OFF_ATTACK, RANGED_ATTACK]
    assert hands[OFF_ATTACK].min_max["total_pct"] == lit("0.5")
    assert hands[BASE_ATTACK].min_max["total_pct"] == 1.0
    assert hands[RANGED_ATTACK].weapon is None and hands[RANGED_ATTACK].weapon_min == BASE_MINDAMAGE
    assert hands[RANGED_ATTACK].base_attack_time_ms == BASE_ATTACK_TIME
    # AP term per point: 1/3.5 * 2.6 (main) ; the off hand keeps the full AP (includeWeapon=false path) before the 0.5 TOTAL_PCT
    assert hands[BASE_ATTACK].ap_term_per_point == mul(div(lit("1.0"), lit("3.5")), div(f32(2600), lit("1000.0")))
    assert hands[OFF_ATTACK].min_max["ap_term"] == hands[BASE_ATTACK].min_max["ap_term"]


def test_prepare_hands_cat_form_uses_combat_round_time_for_melee_only():
    mh = make_item(1, INVTYPE_2HWEAPON, 10, delay=3000)
    state = check_equip(mh, None, dual_wield=False, titan_grip=False, titan_grip_subclass_mask=0)
    hands = prepare_hands(state, level=90, form_id=1, form_combat_round_time=1000, ap=3500, ap_mod_pos=0, ap_mod_neg=0,
                          ap_multiplier_value=0.0, versatility_pct=0.0, mods={})
    assert hands[BASE_ATTACK].base_attack_time_ms == 1000 and hands[OFF_ATTACK].base_attack_time_ms == 1000
    assert hands[RANGED_ATTACK].base_attack_time_ms == BASE_ATTACK_TIME
    assert hands[BASE_ATTACK].ap_multiplier == 1.0 and hands[BASE_ATTACK].normalized_speed == lit("3.3")
    assert hands[BASE_ATTACK].min_max["branch"] == "shapeshift CombatRoundTime"


# --- real items (snapshot) -------------------------------------------------------

@pytest.mark.snapshot
def test_real_outlaw_pair_prepares_two_hands(resolver):
    mh = load_weapon_item(resolver, 268202, player_level=90, context=3)
    oh = load_weapon_item(resolver, 268208, player_level=90, context=3)
    assert mh.subclass == 7 and mh.inventory_type == INVTYPE_WEAPON and mh.delay_ms == 2600
    assert oh.subclass == 0 and oh.is_ranged_weapon is False
    assert mh.weapon_attack_power == int(math.trunc(mul(mh.dps, lit("6.0"))))
    state = check_equip(mh, oh, dual_wield=True, titan_grip=False, titan_grip_subclass_mask=0)
    hands = prepare_hands(state, level=90, form_id=None, form_combat_round_time=0, ap=0, ap_mod_pos=0, ap_mod_neg=0,
                          ap_multiplier_value=0.0, versatility_pct=0.0, mods={})
    assert hands[BASE_ATTACK].urand_bounds == (int(mh.min_damage_f32), int(mh.max_damage_f32))
    assert hands[OFF_ATTACK].urand_bounds == (int(mul(oh.min_damage_f32, lit("0.5"))), int(mul(oh.max_damage_f32, lit("0.5"))))
    assert hands[BASE_ATTACK].normalized_speed == lit("2.4")


@pytest.mark.snapshot
def test_real_bow_is_ranged_main_hand_with_melee_placeholder(resolver):
    bow = load_weapon_item(resolver, 268207, player_level=90, context=3)
    assert bow.is_ranged_weapon and bow.inventory_type == 15
    state = check_equip(bow, None, dual_wield=False, titan_grip=False, titan_grip_subclass_mask=0)
    assert state.two_hand_used and state.ranged_main_hand
    hands = prepare_hands(state, level=90, form_id=None, form_combat_round_time=0, ap=0, ap_mod_pos=0, ap_mod_neg=0,
                          ap_multiplier_value=0.0, versatility_pct=0.0, mods={})
    assert hands[RANGED_ATTACK].weapon is bow and hands[BASE_ATTACK].weapon is None
    assert hands[RANGED_ATTACK].normalized_speed == div(f32(bow.delay_ms), lit("1000.0"))


# --- corpus shape ----------------------------------------------------------------

def test_sources_rows_carry_valid_evidence_classes():
    rows = weapon_sources_rows()
    assert len(rows) >= 18
    assert all(r["evidence_class"] in EVIDENCE_CLASSES for r in rows)
    assert any(r["evidence_class"] == "unresolved" and "ItemLevelByLevel" in r["field"] for r in rows)


def test_weapon_sources_corpus_if_present():
    path = CORPORA / "weapon-sources.json"
    if not path.exists():
        pytest.skip("weapon-sources.json not generated")
    data = json.loads(path.read_text(encoding="utf-8"))
    assert list(data)[0] == "provenance" and data["provenance"]["trinitycore_commit"].startswith("7f3d43b")
    cap = data["capability_by_spec"]
    assert "72" in cap["titan_grip"] and "72" in cap["dual_wield"]
    assert "263" in cap["dual_wield"] and "269" in cap["dual_wield"]
    assert data["constants"]["AP_DIVISOR"] == 3.5


def test_two_hand_main_hand_rejects_any_off_hand_item_including_shield():
    # Player.cpp:10870-10871: IsTwoHandUsed() is checked for every off-hand item type
    mh = make_item(1, INVTYPE_2HWEAPON, 8)
    shield = make_item(9, 14, 6, class_id=4)
    s = check_equip(mh, shield, dual_wield=False, titan_grip=False, titan_grip_subclass_mask=0)
    assert any("EQUIP_ERR_2HANDED_EQUIPPED" in e for e in s.equip_errors)
    # an earlier gate returns first (only one error per CanEquipItem call)
    s2 = check_equip(mh, make_item(2, INVTYPE_WEAPON, 0), dual_wield=False, titan_grip=False, titan_grip_subclass_mask=0)
    assert len(s2.equip_errors) == 1 and "CanDualWield" in s2.equip_errors[0]


def test_non_feral_combat_round_time_form_still_scales_every_attack_type():
    # SpellShapeshiftForm 45 (Yu'lon) has CombatRoundTime 2000 but is not IsInFeralForm; StatSystem.cpp:459
    bow = make_item(5, 15, 2, delay=3000)
    state = check_equip(bow, None, dual_wield=False, titan_grip=False, titan_grip_subclass_mask=0)
    hands = prepare_hands(state, level=90, form_id=45, form_combat_round_time=2000, ap=0, ap_mod_pos=0, ap_mod_neg=0,
                          ap_multiplier_value=0.0, versatility_pct=0.0, mods={})
    ranged = hands[RANGED_ATTACK]
    assert ranged.min_max["branch"] == "shapeshift CombatRoundTime"
    # non-feral: apmod = weapon delay 3.0; weapon * 2000 / 1000 / 3.0
    assert ranged.min_max["weapon_min_used"] == div(div(mul(bow.min_damage_f32, f32(2000)), lit("1000.0")), lit("3.0"))


@pytest.mark.snapshot
def test_default_skill_grants_resolve_rogue_hunter_demon_hunter_dual_wield(tables):
    grants = default_skill_grants(tables)
    assert sorted(grants) == ["12", "3", "4"]
    rogue = grants["4"][0]
    assert (rogue["spell"], rogue["skill_line"], rogue["skill_race_class_info"], rogue["acquire_method"]) == \
        (674, 118, 131, "AutomaticCharLevel")
    assert grants["12"][0]["race_restricted"] and not rogue["race_restricted"]
    assert "1" not in grants          # warriors: 296087 is AcquireMethod 0 (Learned) on class line 840


def test_weapon_sources_capability_covers_every_melee_dual_wielder_if_present():
    path = CORPORA / "weapon-sources.json"
    if not path.exists():
        pytest.skip("weapon-sources.json not generated")
    cap = json.loads(path.read_text(encoding="utf-8"))["capability_by_spec"]["dual_wield"]
    for spec in ("259", "260", "261", "577", "581", "253"):
        assert any(r["scope"] == "default_skill" and r["spell"] == 674 for r in cap[spec]), spec
    for spec in ("62", "63", "64", "65", "66", "70", "102", "103", "104", "105"):
        assert spec not in cap, spec


@pytest.mark.snapshot
def test_character_prep_hook_prepares_outlaw_pair(resolver):
    if not (CORPORA / "weapon-sources.json").exists():
        pytest.skip("weapon-sources.json not generated")
    slots = {}
    for slot, item_id in (("MAINHAND", 268202), ("OFFHAND", 268208)):
        from gearing.resolver import Variant
        r = resolver.resolve(item_id, Variant(label="n", context=3), player_level=90)
        slots[slot] = {"item_id": item_id, "name": r.name, "effective_item_level": r.effective_item_level, **r.weapon}
    out = CHARACTER_PREP_HOOK({"identity": {"level": 90, "spec": {"spec_id": 260}, "class": {"class_id": 4}},
                               "section": {"slots": slots}})
    assert out["evidence_class"] == "trinity-probe" and out["equip_state"]["dual_wielding"]
    assert [h["attack_type"] for h in out["hands"]] == ["BASE_ATTACK", "OFF_ATTACK", "RANGED_ATTACK"]
    assert out["hands"][1]["total_pct"] == 0.5 and out["hands"][0]["urand_bounds_at_ap0"] == [141, 236]


# --- closeout review G2: AcquireMethod-0 class-line grants are character state -------------------------

@pytest.mark.snapshot
def test_learned_only_skill_line_spells_flags_296087_not_674(tables):
    from weapon_combat.weapon import learned_only_skill_line_spells
    got = learned_only_skill_line_spells(tables, {296087, 674, 231842})
    # SkillLineAbility 40461 (skill 840, AcquireMethod 0); 674 is SLA 610 AcquireMethod 2
    assert got == {296087: [40461]}


def test_learned_only_grant_needs_supplied_learned_spell():
    from weapon_combat.weapon import _dual_wield_from_capability
    cap = {"dual_wield": {"72": [{"spell": 231842}]}, "titan_grip": {},
           "learned_only": {"dual_wield": {"71": [{"spell": 296087}], "72": [{"spell": 296087}]}, "titan_grip": {}}}
    assert _dual_wield_from_capability(cap, "71", set())[0] is False
    assert _dual_wield_from_capability(cap, "71", {296087})[0] is True
    assert _dual_wield_from_capability(cap, "72", set())[0] is True
    assert _dual_wield_from_capability(cap, "64", {296087})[0] is False


def test_weapon_sources_arms_and_protection_have_no_automatic_dual_wield_if_present():
    path = CORPORA / "weapon-sources.json"
    if not path.exists():
        pytest.skip("weapon-sources.json not generated")
    data = json.loads(path.read_text(encoding="utf-8"))
    cap = data["capability_by_spec"]["dual_wield"]
    assert "71" not in cap and "73" not in cap
    assert [r["spell"] for r in cap["72"]] == [231842]
    learned = data["capability_by_spec_learned_only"]["dual_wield"]
    for spec in ("71", "72", "73"):
        assert [(r["spell"], r["acquire_method"], r["skill_line_ability"]) for r in learned[spec]] == [(296087, "Learned", [40461])]
    assert all(r["scope"] != "class_skill_lines" for rows in cap.values() for r in rows)


@pytest.mark.snapshot
def test_character_prep_hook_arms_dual_wield_needs_learned_296087(resolver):
    if not (CORPORA / "weapon-sources.json").exists():
        pytest.skip("weapon-sources.json not generated")
    from gearing.resolver import Variant
    slots = {}
    for slot, item_id in (("MAINHAND", 268208), ("OFFHAND", 268206)):
        r = resolver.resolve(item_id, Variant(label="n", context=3), player_level=90)
        slots[slot] = {"item_id": item_id, "name": r.name, "effective_item_level": r.effective_item_level, **r.weapon}
    identity = {"level": 90, "spec": {"spec_id": 71}, "class": {"class_id": 1}}
    fresh = CHARACTER_PREP_HOOK({"identity": identity, "section": {"slots": slots}})
    assert fresh["capability"]["dual_wield"] is False and fresh["equip_state"]["equip_errors"]
    learned = CHARACTER_PREP_HOOK({"identity": dict(identity, learned_spells=[296087]), "section": {"slots": slots}})
    assert learned["capability"]["dual_wield"] is True and learned["equip_state"]["dual_wielding"]
