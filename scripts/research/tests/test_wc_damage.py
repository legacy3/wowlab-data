"""B4/B5 damage arithmetic: pure mirrors of the white-swing and special-attack stages."""

from __future__ import annotations

import io
import json
import math
import sys
from contextlib import redirect_stdout
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

RESEARCH_ROOT = Path(__file__).resolve().parents[1]
if str(RESEARCH_ROOT) not in sys.path:
    sys.path.insert(0, str(RESEARCH_ROOT))

from procs.chance import add, div, f32, lit, mul  # noqa: E402
from weapon_combat import CORPORA, EVIDENCE_CLASSES  # noqa: E402
from weapon_combat.damage import (  # noqa: E402
    ArmorInputs, DoneMods, OutcomeInputs, TakenMods, add_pct_u32, apply_outcome, calc_armor_reduced_damage,
    calculate_pct_u32, damage_arithmetic_rows, melee_damage_bonus_done, melee_damage_bonus_taken,
    player_block_percent, special_weapon_damage, spell_weapon_damage_taken, std_round, white_swing)
from weapon_combat.weapon import BASE_ATTACK  # noqa: E402


def curve1(**kw) -> ArmorInputs:
    base = dict(attacker_avg_item_level=0.0, item_level_by_level=0.0, diminishing_curve_value=1.0)
    base.update(kw)
    return ArmorInputs(**base)


# --- percent helpers --------------------------------------------------------------

def test_calculate_pct_rounds_uint32_base_through_float():
    # Util.h:74 T(base * float(pct) / 100.0f): the uint32 base becomes float before the multiply
    assert calculate_pct_u32(16777219, 30.0) == 5033166       # binary32 (probe: 5033166)
    assert int(16777219 * 30.0 / 100.0) == 5033165            # a double oracle would say this
    assert calculate_pct_u32(123456789, 30.0) == 37037040 != int(123456789 * 30.0 / 100.0)
    assert calculate_pct_u32(16777217, 30.0) == 5033165       # not every base above 2**24 discriminates
    assert calculate_pct_u32(100000, 30.0) == 30000
    assert add_pct_u32(1000, 12.5) == 1125


# --- outcome switch (Unit.cpp:1400-1495) ------------------------------------------

def test_crit_doubles_then_add_pct_of_aura_multiplier():
    assert apply_outcome(1000, OutcomeInputs(outcome="crit"))["damage"] == 2000
    r = apply_outcome(100000, OutcomeInputs(outcome="crit", crit_damage_bonus_mult=1.2))
    assert r["damage"] == 240000 and r["crit_mod_pct"] == mul(add(f32(1.2), -1.0), f32(100))


def test_block_creature_percent_vs_player_fraction():
    creature = apply_outcome(100000, OutcomeInputs(outcome="block"))
    assert (creature["damage"], creature["blocked"], creature["clean_damage"]) == (70000, 30000, 30000)
    frac = player_block_percent(2000, 3430.0)
    assert 0.368 < frac < 0.369
    player = apply_outcome(100000, OutcomeInputs(outcome="block", victim_is_player=True, victim_shield_block=2000))
    assert (player["damage"], player["blocked"]) == (99632, 368)          # fraction fed to CalculatePct
    assert player_block_percent(10**9, 3430.0) == lit("0.85")
    assert player_block_percent(0, 0.0) == 0.0


def test_critical_block_doubles_then_multiplies_and_truncates():
    r = apply_outcome(100000, OutcomeInputs(outcome="block", block_critical=True, attacker_critical_block_mult=1.15))
    assert r["blocked"] == int(math.trunc(mul(f32(60000), f32(1.15))))
    assert r["damage"] == 100000 - r["blocked"]


def test_glancing_caps_level_difference_and_crushing_halves_in_integers():
    g = apply_outcome(100000, OutcomeInputs(outcome="glancing", attacker_level=90, victim_level=95))
    assert (g["damage"], g["clean_damage"]) == (70000, 30000)
    g2 = apply_outcome(100000, OutcomeInputs(outcome="glancing", attacker_level=90, victim_level=91))
    reduce = add(lit("1.0"), -mul(f32(1), lit("0.1")))
    assert g2["damage"] == int(math.trunc(mul(reduce, f32(100000))))
    assert apply_outcome(100001, OutcomeInputs(outcome="crushing"))["damage"] == 150001


def test_avoidance_outcomes_zero_damage_and_move_clean_damage():
    for o in ("miss", "evade"):
        assert apply_outcome(500, OutcomeInputs(outcome=o))["damage"] == 0
    for o in ("parry", "dodge"):
        r = apply_outcome(500, OutcomeInputs(outcome=o))
        assert (r["damage"], r["clean_damage"]) == (0, 500)


# --- armor (Unit.cpp:1684-1774) ---------------------------------------------------

def test_armor_reduction_with_supplied_curve_factor():
    assert calc_armor_reduced_damage(100000, curve1(victim_armor=5000))["result"] == 40688
    # mitigation cap 0.85f
    capped = calc_armor_reduced_damage(100000, curve1(victim_armor=10**7))
    assert capped["mitigation"] == lit("0.85") and capped["result"] == int(mul(f32(100000), 1.0 - float(lit("0.85"))))


def test_armor_at_max_level_fails_closed_without_item_level_table():
    r = calc_armor_reduced_damage(100000, ArmorInputs(victim_armor=5000))
    assert r["result"] is None and "ItemLevelByLevel" in r["unresolved"]
    # below max level the curve is not consulted
    ok = calc_armor_reduced_damage(100000, ArmorInputs(victim_armor=5000, attacker_level_for_target=80, armor_constant=3000.0))
    assert ok["result"] is not None


def test_armor_curve_gate_follows_owner_player_not_type_id():
    # Unit.cpp:1747: a player-owned pet also takes the item-level path; a plain creature does not
    pet = ArmorInputs(victim_armor=5000, attacker_is_player=False, attacker_owner_is_player=True)
    assert calc_armor_reduced_damage(1000, pet)["result"] is None
    creature = ArmorInputs(victim_armor=5000, attacker_is_player=False, attacker_owner_is_player=False)
    mitigation = min(div(f32(5000), add(f32(5000), f32(3430))), lit("0.85"))
    assert calc_armor_reduced_damage(1000, creature)["result"] == int(math.trunc(mul(f32(1000), add(lit("1.0"), -mitigation))))


def test_armor_zero_or_bypassed_returns_damage_unchanged():
    assert calc_armor_reduced_damage(1234, curve1(victim_armor=0))["result"] == 1234
    assert calc_armor_reduced_damage(1234, curve1(victim_armor=5000, bypass_armor_pct=150.0))["result"] == 1234
    assert calc_armor_reduced_damage(1234, curve1(victim_armor=5000, mod_target_resistance=-6000))["result"] == 1234


def test_armor_penetration_cap_formula_above_level_60():
    r = calc_armor_reduced_damage(100000, curve1(victim_armor=5000, armor_penetration_aura_pct=20.0))
    stage = [s for s in r["stages"] if s["stage"] == "armor penetration cap"][0]
    # 400 + 85*90 + 4.5f*85*31 = 19907.5 ; min((5000 + 19907.5)/3, 5000) = 5000 ; 5000 - 20% = 4000
    assert stage["max_armor_pen"] == 5000.0 and stage["armor"] == 4000.0


# --- bonus done / taken -----------------------------------------------------------

def test_bonus_done_float_product_truncates_to_int32():
    r = melee_damage_bonus_done(1000, DoneMods(done_flat_creature=5, versus_creature_mult=1.1), att_type=BASE_ATTACK,
                                ap_multiplier_value=2.6, school_mask=1)
    assert r["result"] == 1105
    ap = melee_damage_bonus_done(1000, DoneMods(ap_bonus=350), att_type=BASE_ATTACK, ap_multiplier_value=2.6, school_mask=1)
    assert ap["ap_flat_term"] == int(math.trunc(mul(div(f32(350), lit("3.5")), f32(2.6)))) == 260
    assert ap["result"] == 1260
    assert melee_damage_bonus_done(0, DoneMods(done_flat_creature=5), att_type=BASE_ATTACK, ap_multiplier_value=2.6, school_mask=1)["result"] == 0
    # school pct applies only for non-physical white swings
    assert melee_damage_bonus_done(1000, DoneMods(school_pct_done=1.5), att_type=BASE_ATTACK, ap_multiplier_value=2.6, school_mask=1)["result"] == 1000
    assert melee_damage_bonus_done(1000, DoneMods(school_pct_done=1.5), att_type=BASE_ATTACK, ap_multiplier_value=2.6, school_mask=16)["result"] == 1500


def test_bonus_taken_versatility_gate_and_flat_floor():
    base = TakenMods(versatility_taken_pct=2.5)
    assert melee_damage_bonus_taken(1000, base)["result"] == 1000                     # no spell-mod owner
    owned = TakenMods(versatility_taken_pct=2.5, victim_has_spell_mod_owner=True)
    assert melee_damage_bonus_taken(1000, owned)["result"] == 975
    assert melee_damage_bonus_taken(10, TakenMods(taken_flat_melee=-20))["result"] == 0
    # ignore-resist rewrite only when the multiplier is below 1
    halved = TakenMods(pct_taken_school=0.5, ignore_resist_pcts=(100.0,))
    assert melee_damage_bonus_taken(1000, halved)["result"] == 1000


# --- special weapon attacks (SpellEffects.cpp:2860-2944) ---------------------------

def test_special_effect_order_and_double_count():
    assert special_weapon_damage(weapon_roll=1234, effects=[(58, 0.5), (31, 150)])["weapon_damage_double"] == 1852.0
    assert special_weapon_damage(weapon_roll=1234, effects=[(31, 150), (58, 10)])["weapon_damage_double"] == 1861.0
    assert special_weapon_damage(weapon_roll=1234, effects=[(58, 10), (31, 150)])["weapon_damage_double"] == 1866.0
    assert special_weapon_damage(weapon_roll=1234, effects=[(58, 10), (58, 10)])["weapon_damage_double"] == 1274.0
    assert special_weapon_damage(weapon_roll=100, effects=[(31, 200), (31, 200)])["weapon_damage_double"] == 1600.0
    # fixed bonus scaled by float TOTAL_PCT only with addPctMods
    assert special_weapon_damage(weapon_roll=100, effects=[(121, 10)], weapon_total_pct=0.5)["weapon_damage_double"] == 105.0
    assert special_weapon_damage(weapon_roll=100, effects=[(121, 10)], weapon_total_pct=0.5,
                                 add_pct_mods=False)["weapon_damage_double"] == 110.0
    # non-weapon effects are skipped
    assert special_weapon_damage(weapon_roll=100, effects=[(2, 999), (31, 50)])["weapon_damage_double"] == 50.0


def test_std_round_is_half_away_from_zero_and_exact():
    assert std_round(0.49999999999999994) == 0.0
    assert math.floor(0.49999999999999994 + 0.5) == 1          # the naive formula is wrong here
    assert (std_round(2.5), std_round(-2.5), std_round(3.4999)) == (3.0, -3.0, 3.0)


@settings(max_examples=300, deadline=None)
@given(st.floats(min_value=-1e15, max_value=1e15, allow_nan=False))
def test_std_round_matches_decimal_half_up(x):
    expected = float(Decimal(x).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    assert std_round(x) == expected


# --- pipeline + corpus + CLI --------------------------------------------------------

def test_white_swing_skips_armor_for_non_physical_school_and_propagates_unresolved():
    fire = white_swing(roll=500, att_type=BASE_ATTACK, ap_multiplier_value=2.6, school_mask=4, done=DoneMods(),
                       taken=TakenMods(), armor=ArmorInputs(victim_armor=5000), outcome=OutcomeInputs(outcome="normal"))
    assert fire["damage"] == 500 and "skipped" in fire["trace"][3]
    phys = white_swing(roll=500, att_type=BASE_ATTACK, ap_multiplier_value=2.6, school_mask=1, done=DoneMods(),
                       taken=TakenMods(), armor=ArmorInputs(victim_armor=5000), outcome=OutcomeInputs(outcome="normal"))
    assert "unresolved" in phys and "damage" not in phys
    crit = white_swing(roll=500, att_type=BASE_ATTACK, ap_multiplier_value=2.6, school_mask=1, done=DoneMods(),
                       taken=TakenMods(), armor=curve1(victim_armor=1470), outcome=OutcomeInputs(outcome="crit"))
    mitig = calc_armor_reduced_damage(500, curve1(victim_armor=1470))["result"]
    assert crit["damage"] == 2 * mitig and crit["clean_damage"] == 500 - mitig


def test_damage_rows_are_ordered_and_classified():
    rows = damage_arithmetic_rows()
    assert all(r["evidence_class"] in EVIDENCE_CLASSES for r in rows)
    numbered = [r["order"] for r in rows if isinstance(r["order"], int)]
    assert numbered == sorted(numbered) and numbered[0] == 1 and numbered[-1] == 9
    assert {r["stage"] for r in rows if r["evidence_class"] == "legacy-only"} >= {"glancing", "crushing", "resilience"}


def test_damage_cli_prints_every_stage():
    from weapon_combat.cli import main
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = main(["damage", "--hand", "mh", "--roll", "1000", "--outcome", "crit", "--armor", "1470",
                   "--level", "90", "--armor-curve-factor", "1.0"])
    out = json.loads(buf.getvalue())
    assert rc == 0 and [t["stage"] for t in out["trace"]] == list(range(1, 10))
    assert out["damage"] == 2 * calc_armor_reduced_damage(1000, curve1(victim_armor=1470))["result"]
    buf2 = io.StringIO()
    with redirect_stdout(buf2):
        rc2 = main(["damage", "--hand", "mh", "--roll", "1000", "--outcome", "normal", "--armor", "1470"])
    assert rc2 == 2 and "unresolved" in json.loads(buf2.getvalue())


def test_damage_arithmetic_corpus_if_present():
    path = CORPORA / "damage-arithmetic.json"
    if not path.exists():
        pytest.skip("damage-arithmetic.json not generated")
    data = json.loads(path.read_text(encoding="utf-8"))
    assert list(data)[0] == "provenance"
    assert any(d["case"] == "special double count" for d in data["discriminating_inputs"])


# --- special weapon attack: CalculateSpellDamageTaken (Unit.cpp:1193-1257) -----------

def test_crit_multiplier_1_3_truncates_to_2599_on_both_paths():
    # (1.3f - 1.0f) * 100 == 29.9999962f -> CalculatePct(2000, .) == 599 (agent D pins the same)
    assert apply_outcome(1000, OutcomeInputs(outcome="crit", crit_damage_bonus_mult=1.3))["damage"] == 2599
    assert spell_weapon_damage_taken(1000, crit=True, crit_damage_bonus_mult=1.3)["result"] == 2599


def test_special_block_truncates_percent_first_player_victim_blocks_nothing():
    creature = spell_weapon_damage_taken(1000, blocked=True)
    assert (creature["result"], creature["blocked"], creature["full_block"]) == (700, 300, False)
    player = spell_weapon_damage_taken(1000, blocked=True, victim_is_player=True, victim_shield_block=10**9)
    assert player["block_percent_uint32"] == 0 and player["result"] == 1000          # Unit.cpp:1239
    crit_block = spell_weapon_damage_taken(10, blocked=True, block_critical=True, attacker_critical_block_mult=2.0)
    assert crit_block["block_percent_uint32"] == 120 and crit_block["full_block"] and crit_block["result"] == 0
    assert spell_weapon_damage_taken(-1)["result"] is None
    assert spell_weapon_damage_taken(500, ignore_damage_taken_modifiers=True, blocked=True)["result"] == 500
