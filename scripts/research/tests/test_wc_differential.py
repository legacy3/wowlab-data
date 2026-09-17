"""B7 differential: the Python oracle (binary32 via procs.chance) against tools/tc_weapon_probe.

The probe compiles Trinity's own text (extracted bodies and documented cuts,
see tools/tc_weapon_probe/extract.py); ``bonus``, ``taken`` and ``special`` are
re-typed there and are weaker evidence.  Skipped when the probe binary is absent:
``make -C tools/tc_weapon_probe``.
"""

from __future__ import annotations

import math
import subprocess
import sys
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

RESEARCH_ROOT = Path(__file__).resolve().parents[1]
if str(RESEARCH_ROOT) not in sys.path:
    sys.path.insert(0, str(RESEARCH_ROOT))

from procs.chance import add, f32, lit  # noqa: E402
from weapon_combat.damage import (  # noqa: E402
    OUTCOMES, ArmorInputs, DoneMods, OutcomeInputs, TakenMods, add_pct_f, apply_outcome, calc_armor_reduced_damage,
    calculate_pct_u32, melee_damage_bonus_done, melee_damage_bonus_taken, player_block_percent, special_weapon_damage)
from weapon_combat.weapon import (  # noqa: E402
    BASE_ATTACK, OFF_ATTACK, RANGED_ATTACK, MinMaxInputs, ap_multiplier, calculate_min_max, total_attack_power,
    urand_bounds)

PROBE = RESEARCH_ROOT / "tools" / "tc_weapon_probe" / "probe"
SETTINGS = settings(max_examples=150, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])


@pytest.fixture(scope="module")
def probe():
    if not PROBE.exists():
        pytest.skip(f"probe not built; run: make -C {PROBE.parent}")
    proc = subprocess.Popen([str(PROBE)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)

    def ask(request: str) -> str:
        proc.stdin.write(request + "\n")
        proc.stdin.flush()
        return proc.stdout.readline().strip()

    yield ask
    proc.stdin.close()
    proc.wait(timeout=10)


def hexf(text: str) -> float:
    return float.fromhex(text)


def fs(x: float) -> str:
    """Exact text for a binary32 value (repr of the double equal to it; strtof reads it back exactly)."""
    return repr(float(x))


# strategies: binary32 values built exactly
f32s = st.floats(min_value=0.0, max_value=5000.0, allow_nan=False).map(f32)
pcts = st.floats(min_value=0.0, max_value=3.0, allow_nan=False).map(f32)
subclasses = st.sampled_from([0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 13, 15, 16, 17, 18, 19, 20])


# --- GetAPMultiplier ----------------------------------------------------------------

@SETTINGS
@given(is_player=st.booleans(), feral=st.booleans(), bat=st.integers(0, 6000), has_weapon=st.booleans(),
       delay=st.integers(0, 6000), subclass=subclasses, normalized=st.booleans())
def test_ap_multiplier_matches_probe(probe, is_player, feral, bat, has_weapon, delay, subclass, normalized):
    got = hexf(probe(f"apmult {int(is_player)} {int(feral)} {bat} {int(has_weapon)} {delay} {subclass} {int(normalized)}"))
    want, _ = ap_multiplier(is_player=is_player, feral=feral, base_attack_time_ms=bat,
                            weapon=(delay, subclass) if has_weapon else None, normalized=normalized)
    assert got == want


# --- CalculateMinMaxDamage ------------------------------------------------------------

def minmax_request(inp: MinMaxInputs, form_present: bool) -> str:
    has_weapon = inp.weapon is not None
    delay, subclass = inp.weapon if has_weapon else (0, 0)
    return " ".join(str(x) for x in [
        "minmax", inp.att_type, int(inp.normalized), int(inp.add_total_pct), inp.ap, inp.ap_mod_pos, inp.ap_mod_neg,
        fs(inp.ap_multiplier), fs(inp.flat_base), fs(inp.base_pct), fs(inp.total_value), fs(inp.total_pct),
        fs(inp.weapon_min), fs(inp.weapon_max), int(inp.have_offhand_weapon), fs(inp.versatility_rating_pct),
        inp.versatility_aura, int(form_present), inp.form_combat_round_time, int(inp.can_use_attack_type),
        int(inp.feral), int(inp.is_player), inp.base_attack_time_ms, int(has_weapon), delay, subclass])


def check_minmax(probe, inp: MinMaxInputs) -> None:
    got = [hexf(x) for x in probe(minmax_request(inp, bool(inp.form_combat_round_time))).split()]
    want = calculate_min_max(inp)
    assert got == [want["min"], want["max"]], (inp, want)


def test_minmax_fixed_discriminating_inputs(probe):
    base = dict(normalized=False, add_total_pct=True, weapon=(2600, 7), base_attack_time_ms=2600)
    check_minmax(probe, MinMaxInputs(att_type=BASE_ATTACK, ap=12345, weapon_min=f32(141.9), weapon_max=f32(236.2),
                                     versatility_rating_pct=f32(3.25), **base))
    check_minmax(probe, MinMaxInputs(att_type=OFF_ATTACK, ap=12345, weapon_min=f32(61.5), weapon_max=f32(127.0),
                                     total_pct=lit("0.5"), have_offhand_weapon=True, **base))
    check_minmax(probe, MinMaxInputs(att_type=OFF_ATTACK, ap=3500, have_offhand_weapon=False, weapon_min=999.0, **base))
    check_minmax(probe, MinMaxInputs(att_type=OFF_ATTACK, ap=3500, can_use_attack_type=False, have_offhand_weapon=True, **base))
    check_minmax(probe, MinMaxInputs(att_type=BASE_ATTACK, ap=3500, can_use_attack_type=False, weapon=None,
                                     normalized=False, add_total_pct=True))
    # cat form (feral, CRT 1000) and a non-feral CRT form on the ranged hand
    check_minmax(probe, MinMaxInputs(att_type=BASE_ATTACK, normalized=False, add_total_pct=True, ap=20000, feral=True,
                                     form_combat_round_time=1000, base_attack_time_ms=1000, weapon=(3600, 10),
                                     weapon_min=f32(293.3), weapon_max=f32(397.7)))
    check_minmax(probe, MinMaxInputs(att_type=RANGED_ATTACK, normalized=True, add_total_pct=False, ap=20000,
                                     form_combat_round_time=2000, base_attack_time_ms=2000, weapon=(3000, 2),
                                     weapon_min=f32(244.0), weapon_max=f32(331.0)))


@SETTINGS
@given(att=st.sampled_from([BASE_ATTACK, OFF_ATTACK, RANGED_ATTACK]), normalized=st.booleans(), add_total_pct=st.booleans(),
       ap=st.integers(-2000, 200000), mod_pos=st.integers(0, 5000), mod_neg=st.integers(-5000, 0), ap_mult=pcts,
       total_value=f32s, total_pct=pcts, wmin=f32s, wmax=f32s, have_off=st.booleans(),
       versa=st.floats(min_value=0.0, max_value=40.0).map(f32), versa_aura=st.integers(0, 10),
       crt=st.sampled_from([0, 1, 1000, 2000, 2500]), can_use=st.booleans(), feral=st.booleans(),
       bat=st.integers(100, 4000), weapon=st.one_of(st.none(), st.tuples(st.integers(100, 4000), subclasses)))
def test_minmax_matches_probe(probe, att, normalized, add_total_pct, ap, mod_pos, mod_neg, ap_mult, total_value,
                              total_pct, wmin, wmax, have_off, versa, versa_aura, crt, can_use, feral, bat, weapon):
    check_minmax(probe, MinMaxInputs(
        att_type=att, normalized=normalized, add_total_pct=add_total_pct, ap=ap, ap_mod_pos=mod_pos, ap_mod_neg=mod_neg,
        ap_multiplier=ap_mult, total_value=total_value, total_pct=total_pct, weapon_min=wmin, weapon_max=wmax,
        have_offhand_weapon=have_off, versatility_rating_pct=versa, versatility_aura=versa_aura,
        form_combat_round_time=crt, can_use_attack_type=can_use, feral=feral, base_attack_time_ms=bat, weapon=weapon))


# --- CalculateDamage (UnitData read path) ---------------------------------------------

@SETTINGS
@given(att=st.sampled_from([BASE_ATTACK, OFF_ATTACK, RANGED_ATTACK]), feral=st.booleans(),
       vals=st.lists(st.floats(min_value=-50.0, max_value=5e6, allow_nan=False).map(f32), min_size=6, max_size=6))
def test_calculate_damage_bounds_match_probe(probe, att, feral, vals):
    mn, mx, ohmn, ohmx, rmn, rmx = vals
    got = tuple(int(x) for x in probe(f"calcdamage {att} 0 1 {int(feral)} " + " ".join(fs(v) for v in vals)).split())
    if att == RANGED_ATTACK:
        lo, hi = rmn, rmx
    elif att == OFF_ATTACK:
        lo, hi = ohmn, ohmx
    else:
        lo, hi = (add(mn, ohmn), add(mx, ohmx)) if feral else (mn, mx)      # Unit.cpp:2528-2532
    assert got == urand_bounds(lo, hi)


# --- GetTotalAttackPowerValue ---------------------------------------------------------

@SETTINGS
@given(att=st.sampled_from([BASE_ATTACK, OFF_ATTACK, RANGED_ATTACK]), include=st.booleans(),
       ap=st.integers(-100000, 2**24 + 50), pos=st.integers(0, 100000), neg=st.integers(-100000, 0), mult=pcts,
       mh=st.integers(0, 5000), oh=st.integers(0, 5000), r=st.integers(0, 5000))
def test_total_attack_power_matches_probe(probe, att, include, ap, pos, neg, mult, mh, oh, r):
    got = hexf(probe(f"apvalue {att} {int(include)} {ap} {pos} {neg} {fs(mult)} {mh} {oh} {r}"))
    want = total_attack_power(att_type=att, include_weapon=include, ap=ap, mod_pos=pos, mod_neg=neg, multiplier=mult,
                              weapon_ap_mh=mh, weapon_ap_oh=oh, weapon_ap_ranged=r)
    assert got == want


# --- CalcArmorReducedDamage ---------------------------------------------------------

@SETTINGS
@given(armor=st.integers(0, 150000), arp=st.floats(min_value=-10.0, max_value=120.0).map(f32), level=st.integers(1, 95))
def test_armor_penetration_cap_matches_probe(probe, armor, arp, level):
    got = hexf(probe(f"arpcap {armor} {fs(arp)} {level}"))
    r = calc_armor_reduced_damage(1000, ArmorInputs(victim_armor=armor, armor_penetration_aura_pct=arp,
                                                    victim_level_for_target=level, attacker_level_for_target=80))
    stage = [s for s in r["stages"] if s["stage"] == "armor penetration cap"][0]
    assert got == stage["armor"]


def armor_case(probe, damage, armor, constant, level, is_player, curve):
    got = int(probe(f"armor {damage} {armor} {fs(constant)} {level} {int(is_player)} 0 0 {fs(curve)} 1"))
    inp = ArmorInputs(victim_armor=armor, attacker_is_player=is_player, attacker_owner_is_player=is_player,
                      attacker_level_for_target=level, armor_constant=constant, attacker_avg_item_level=0.0,
                      item_level_by_level=0.0, diminishing_curve_value=curve)
    assert got == calc_armor_reduced_damage(damage, inp)["result"]


def test_armor_fixed_cases(probe):
    armor_case(probe, 100000, 5000, 3430.0, 90, True, 1.0)            # 40688
    armor_case(probe, 100000, 5000, 3430.0, 90, True, f32(1.065))     # curve 27400 point (30, 1.065)
    armor_case(probe, 100000, 10**6, 3430.0, 80, True, 1.0)           # 0.85f cap
    armor_case(probe, 4294967295 // 3, 1470, 1852.0, 70, False, 1.0) # big uint32 through float


@SETTINGS
@given(damage=st.integers(0, 2**32 - 1), armor=st.integers(0, 150000),
       constant=st.floats(min_value=0.0, max_value=20000.0).map(f32), level=st.integers(1, 90),
       is_player=st.booleans(), curve=st.floats(min_value=0.5, max_value=2.5).map(f32))
def test_armor_tail_matches_probe(probe, damage, armor, constant, level, is_player, curve):
    armor_case(probe, damage, armor, constant, level, is_player, curve)


# --- outcome switch -----------------------------------------------------------------

def outcome_case(probe, outcome, damage, al, vl, crit_mult, victim_player, shield_block, constant, block_crit, cb_mult):
    got = [int(x) for x in probe(f"outcome {OUTCOMES[outcome]} {damage} {al} {vl} {fs(crit_mult)} {int(victim_player)} "
                                 f"{shield_block} {fs(constant)} {int(block_crit)} {fs(cb_mult)}").split()]
    r = apply_outcome(damage, OutcomeInputs(outcome=outcome, attacker_level=al, victim_level=vl,
                                            crit_damage_bonus_mult=crit_mult, victim_is_player=victim_player,
                                            victim_shield_block=shield_block, armor_constant=constant,
                                            block_critical=block_crit, attacker_critical_block_mult=cb_mult))
    assert got == [r["damage"], r["clean_damage"], r["blocked"], r["original_damage"]], outcome


def test_outcome_fixed_cases(probe):
    outcome_case(probe, "block", 100000, 90, 90, 1.0, False, 0, 3430.0, False, 1.0)       # 70000/30000
    outcome_case(probe, "block", 100000, 90, 90, 1.0, True, 2000, 3430.0, False, 1.0)      # player fraction: 368
    outcome_case(probe, "block", 100000, 90, 90, 1.0, False, 0, 3430.0, True, f32(1.3))    # attacker multiplier
    outcome_case(probe, "crit", 100000, 90, 90, f32(1.2), False, 0, 0.0, False, 1.0)       # 240000
    outcome_case(probe, "glancing", 100000, 90, 95, 1.0, False, 0, 0.0, False, 1.0)        # 70000
    outcome_case(probe, "glancing", 100000, 95, 90, 1.0, False, 0, 0.0, False, 1.0)        # negative leveldif -> 150000
    outcome_case(probe, "crushing", 100001, 94, 90, 1.0, False, 0, 0.0, False, 1.0)        # 150001


@SETTINGS
@given(outcome=st.sampled_from(sorted(OUTCOMES)), damage=st.integers(0, 2**31 - 1), al=st.integers(1, 95),
       vl=st.integers(1, 95), crit_mult=st.floats(min_value=0.5, max_value=3.0).map(f32), victim_player=st.booleans(),
       shield_block=st.integers(0, 200000), constant=st.floats(min_value=0.0, max_value=20000.0).map(f32),
       block_crit=st.booleans(), cb_mult=st.floats(min_value=0.5, max_value=2.0).map(f32))
def test_outcome_matches_probe(probe, outcome, damage, al, vl, crit_mult, victim_player, shield_block, constant,
                               block_crit, cb_mult):
    if outcome == "glancing" and vl - al < -9:
        return   # 1.f - leveldif*0.1f >= 2 can overflow uint32 (UB); unreachable: glancing needs victim > attacker + 3
    if outcome == "crit" and damage * 2 * float(crit_mult) >= 2**32:
        return   # uint32 overflow is UB in the float -> uint32 conversion
    outcome_case(probe, outcome, damage, al, vl, crit_mult, victim_player, shield_block, constant, block_crit, cb_mult)


@SETTINGS
@given(shield=st.integers(0, 10**7), constant=st.floats(min_value=0.0, max_value=20000.0).map(f32))
def test_block_percent_matches_probe(probe, shield, constant):
    assert hexf(probe(f"blockpct {shield} {fs(constant)}")) == player_block_percent(shield, constant)


# --- percent helpers ----------------------------------------------------------------

@SETTINGS
@given(base=st.integers(0, 2**32 - 1), pct=st.floats(min_value=0.0, max_value=100.0).map(f32))
def test_calculate_pct_matches_probe(probe, base, pct):
    want = calculate_pct_u32(base, pct)
    if want > 2**32 - 1:
        return
    assert int(probe(f"pct {base} {fs(pct)}")) == want


def test_calculate_pct_discriminators(probe):
    assert int(probe("pct 16777219 30")) == calculate_pct_u32(16777219, 30.0) == 5033166
    assert int(probe("pct 123456789 30")) == calculate_pct_u32(123456789, 30.0) == 37037040


@SETTINGS
@given(base=st.floats(min_value=0.0, max_value=1e6).map(f32), pct=st.floats(min_value=-100.0, max_value=100.0).map(f32))
def test_add_pct_float_matches_probe(probe, base, pct):
    assert hexf(probe(f"addpctf {fs(base)} {fs(pct)}")) == add_pct_f(base, pct)


# --- bonus done / taken tails (re-typed in the probe) -----------------------------

@SETTINGS
@given(damage=st.integers(0, 10**7), flat=st.integers(-1000, 100000), mod=st.floats(min_value=0.0, max_value=5.0).map(f32))
def test_bonus_done_tail_matches_probe(probe, damage, flat, mod):
    if damage == 0:
        return
    got = int(probe(f"bonus {damage} {flat} {fs(mod)}"))
    want = melee_damage_bonus_done(damage, DoneMods(done_flat_creature=flat, versus_creature_mult=mod),
                                   att_type=BASE_ATTACK, ap_multiplier_value=2.6, school_mask=1)["result"]
    assert got == want


@SETTINGS
@given(damage=st.integers(1, 10**7), flat=st.integers(0, 100000), mod=st.floats(min_value=0.1, max_value=3.0).map(f32),
       versa=st.floats(min_value=0.0, max_value=30.0).map(f32), aura=st.integers(0, 20))
def test_bonus_taken_tail_matches_probe(probe, damage, flat, mod, versa, aura):
    got = int(probe(f"taken {damage} {flat} {fs(mod)} {fs(versa)} {aura}"))
    want = melee_damage_bonus_taken(damage, TakenMods(taken_flat_melee=flat, pct_taken_school=mod,
                                                      victim_has_spell_mod_owner=True, versatility_taken_pct=versa,
                                                      versatility_aura=aura))["result"]
    assert got == want


# --- Spell::EffectWeaponDmg (cut) ------------------------------------------------------

def wdmg_case(probe, *, att, attr6, school, ud_min, ud_max, ap, total_pct, wmin, wmax, delay, subclass, effects):
    req = (f"wdmg {att} {int(attr6)} {school} {fs(ud_min)} {fs(ud_max)} {ap} {fs(total_pct)} {fs(wmin)} {fs(wmax)} "
           f"{delay} {subclass} {len(effects)} " + " ".join(f"{e} {v!r}" for e, v in effects))
    out = probe(req).split()
    got_damage, got_norm, got_addpct = hexf(out[0]), bool(int(out[1])), bool(int(out[2]))
    normalized = any(e == 121 for e, _ in effects)
    add_pct = (not attr6) and bool(school & 1)
    assert (got_norm, got_addpct) == (normalized, add_pct)
    if add_pct and not normalized:
        roll = urand_bounds(ud_min, ud_max)[0]
    else:   # Unit.cpp:2506-2516 recompute (probe player is not feral)
        mm = calculate_min_max(MinMaxInputs(att_type=att, normalized=normalized, add_total_pct=add_pct, ap=ap,
                                            total_pct=total_pct, weapon_min=wmin, weapon_max=wmax,
                                            have_offhand_weapon=True, weapon=(delay, subclass),
                                            base_attack_time_ms=delay))
        roll = urand_bounds(mm["min"], mm["max"])[0]
    assert int(out[3]) == roll
    want = special_weapon_damage(weapon_roll=roll, effects=effects, weapon_total_pct=total_pct, add_pct_mods=add_pct)
    assert got_damage == want["weapon_damage_double"], (req, want)


def test_weapon_damage_effect_order_double_count_and_offhand_penalty(probe):
    common = dict(ap=3500, wmin=100.0, wmax=200.0, delay=2600, subclass=7, ud_min=1234.0, ud_max=1500.0)
    wdmg_case(probe, att=BASE_ATTACK, attr6=False, school=1, total_pct=1.0, effects=[(58, 0.5), (31, 150.0)], **common)
    wdmg_case(probe, att=BASE_ATTACK, attr6=False, school=1, total_pct=1.0, effects=[(31, 150.0), (58, 10.0)], **common)
    wdmg_case(probe, att=BASE_ATTACK, attr6=False, school=1, total_pct=1.0, effects=[(58, 10.0), (58, 10.0)], **common)
    wdmg_case(probe, att=BASE_ATTACK, attr6=False, school=1, total_pct=1.0, effects=[(31, 200.0), (31, 200.0)], **common)
    # off hand, ATTR6: CalculateDamage(OFF, false, false) -> totalPct 1: the 0.5 factor is gone
    wdmg_case(probe, att=OFF_ATTACK, attr6=True, school=1, total_pct=lit("0.5"), effects=[(58, 10.0)], **common)
    # Odyn's Fury OH shape: frost school, normalized + pct
    wdmg_case(probe, att=OFF_ATTACK, attr6=False, school=16, total_pct=lit("0.5"), effects=[(121, 0.0), (31, 91.0)], **common)
    # Apocalypse shape: normalized +1 then *306%
    wdmg_case(probe, att=BASE_ATTACK, attr6=False, school=1, total_pct=f32(1.1), effects=[(121, 1.0), (31, 306.0)], **common)


effect_lists = st.lists(st.tuples(st.sampled_from([17, 31, 58, 121, 2]),
                                  st.integers(-50, 400).map(float)), min_size=1, max_size=4)


@SETTINGS
@given(att=st.sampled_from([BASE_ATTACK, OFF_ATTACK, RANGED_ATTACK]), attr6=st.booleans(), school=st.sampled_from([1, 4, 32, 127]),
       ud_min=st.floats(min_value=0.0, max_value=1e5).map(f32), ud_max=st.floats(min_value=0.0, max_value=1e5).map(f32),
       ap=st.integers(0, 100000), total_pct=pcts, wmin=f32s, wmax=f32s, delay=st.integers(500, 4000), subclass=subclasses,
       effects=effect_lists)
def test_weapon_damage_matches_probe(probe, att, attr6, school, ud_min, ud_max, ap, total_pct, wmin, wmax, delay, subclass, effects):
    if att == RANGED_ATTACK and subclass not in (2, 3, 18, 19):
        subclass = 2   # the probe's GetWeaponForAttack ignores IsRangedWeapon; keep inputs Trinity-shaped
    wdmg_case(probe, att=att, attr6=attr6, school=school, ud_min=ud_min, ud_max=ud_max, ap=ap, total_pct=total_pct,
              wmin=wmin, wmax=wmax, delay=delay, subclass=subclass, effects=effects)


def test_single_effect_shorthand_matches_ordered_oracle(probe):
    assert probe("special 1234 0.5 150 1.0 1 1").split() == ["1852", "1852"]
    assert special_weapon_damage(weapon_roll=1234, effects=[(58, 0.5), (31, 150.0)])["int32_to_bonus_done"] == 1852
    assert math.isclose(float(probe("special 1234 0 0 1.0 1 0").split()[0]), 1234.0)
