"""Chance calculators: binary32 arithmetic, classic PPM, RPPM, modifiers, item paths."""

from __future__ import annotations

import math
import struct

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from procs import UnsupportedSource
from procs.chance import (
    RppmInputs,
    aura_proc_chance,
    classic_ppm_chance,
    enchant_combat_spell_chance,
    item_on_proc_chance,
    lit,
    mul,
    race_mask_has_race,
    reduce_proc_60,
    roll_succeeds,
    round_f32,
    rppm_chance,
    rppm_rate,
    weapon_proc_chance,
)


def cfloat(x: float) -> float:
    return struct.unpack("f", struct.pack("f", x))[0]


@given(st.floats(allow_nan=False, allow_infinity=False, width=64))
def test_round_f32_matches_c_conversion(x):
    if abs(x) >= 3.4028235677973366e38:
        return
    assert round_f32(x) == cfloat(x)


@given(st.floats(width=32, allow_nan=False, allow_infinity=False),
       st.floats(width=32, allow_nan=False, allow_infinity=False))
def test_mul_is_closed_in_binary32(a, b):
    r = mul(a, b)
    if not math.isinf(r):
        assert cfloat(r) == r


def test_literals():
    assert lit("0.01") == cfloat(0.01)
    assert lit("1.8") == cfloat(1.8)


@pytest.mark.parametrize("speed,ppm,expected", [
    (2600, 6.0, 26.0), (3600, 1.0, 6.0), (1500, 3.3, 8.0), (0, 5.0, 0.0), (2000, 0.0, 0.0),
    (2000, -1.0, 0.0)])
def test_classic_ppm_floors_whole_percent(speed, ppm, expected):
    assert classic_ppm_chance(speed, ppm).chance_percent == expected


def test_classic_ppm_spellmod_hook():
    assert classic_ppm_chance(2000, 3.0, ppm_after_spellmods=6.0).chance_percent == 20.0
    assert classic_ppm_chance(2000, 0.0, ppm_after_spellmods=6.0).chance_percent == 0.0  # early return


def test_rppm_first_attempt_after_application():
    # fresh aura: attempt 10s ago, success 120s ago
    res = rppm_chance(1.0, 10.0, 120.0)
    # interval 60, bad-luck = 1 + (2 - 1.5) * 3 = 2.5, * 1 * 10 / 60
    assert res.chance_percent == pytest.approx(41.6666, abs=1e-3)


def test_rppm_elapsed_clamps():
    assert rppm_chance(2.0, 11.0, 5.0).chance_percent == rppm_chance(2.0, 10.0, 5.0).chance_percent
    assert rppm_chance(0.01, 10.0, 5000.0).chance_percent == rppm_chance(0.01, 10.0, 1000.0).chance_percent


def test_rppm_bad_luck_floor_is_one():
    # sinceProc small -> 1 + negative -> max(1, ...) = 1
    res = rppm_chance(6.0, 1.0, 0.0)
    assert res.chance_percent == pytest.approx(10.0, rel=1e-6)


def test_rppm_clamped_to_100():
    assert rppm_chance(60.0, 10.0, 1000.0).chance_percent == 100.0


def test_rppm_zero_rate():
    assert rppm_chance(0.0, 10.0, 120.0).chance_percent == 0.0


@settings(max_examples=300)
@given(st.floats(0.0, 60.0), st.floats(0.0, 20.0), st.floats(0.0, 2000.0))
def test_rppm_bounds_and_monotonicity(ppm, attempt, proc):
    c = rppm_chance(ppm, attempt, proc).chance_percent
    assert 0.0 <= c <= 100.0
    assert rppm_chance(ppm, min(attempt + 1, 20.0), proc).chance_percent >= c
    assert rppm_chance(ppm, attempt, proc + 1).chance_percent >= c


def mod(t, p, c, i=1):
    return {"id": i, "type": t, "param": p, "coeff": c}


def test_rppm_haste_params():
    inputs = RppmInputs(mod_haste=1 / 1.2, mod_ranged_haste=1 / 1.1, mod_spell_haste=1 / 1.3, mod_haste_regen=1.0)
    assert rppm_rate(2.0, [mod(1, 1, 1.0)], inputs).chance_percent == pytest.approx(2.4, rel=1e-6)
    assert rppm_rate(2.0, [mod(1, 3, 1.0)], inputs).chance_percent == pytest.approx(2.6, rel=1e-6)
    assert rppm_rate(2.0, [mod(1, 5, 1.0)], inputs).chance_percent == pytest.approx(2.6, rel=1e-6)
    assert rppm_rate(2.0, [mod(1, 9, 1.0)], inputs).chance_percent == 2.0  # unknown param -> term 0


def test_rppm_crit_requires_player():
    inputs = RppmInputs(crit_pct=20.0, spell_crit_pct=30.0, ranged_crit_pct=10.0)
    assert rppm_rate(1.0, [mod(2, 4, 1.0)], inputs).chance_percent == pytest.approx(1.1, rel=1e-6)
    assert rppm_rate(1.0, [mod(2, 1, 1.0)], RppmInputs(is_player=False, crit_pct=50)).chance_percent == 1.0


def test_rppm_class_spec_race_bg_aura():
    base = dict(class_id=4, primary_spec=259, race_id=34, in_battleground_or_arena=True, auras=frozenset({77}))
    i = RppmInputs(**base)
    assert rppm_rate(2.0, [mod(3, 1 << 3, 0.5)], i).chance_percent == 3.0
    assert rppm_rate(2.0, [mod(3, 1 << 2, 0.5)], i).chance_percent == 2.0
    assert rppm_rate(2.0, [mod(4, 259, -0.5)], i).chance_percent == 1.0
    assert rppm_rate(2.0, [mod(5, 1 << 11, 1.0)], i).chance_percent == 4.0   # Dark Iron = bit 11
    assert rppm_rate(2.0, [mod(7, 0, -1.0)], i).chance_percent == 0.0
    assert rppm_rate(2.0, [mod(8, 77, 1.0)], i).chance_percent == 4.0
    assert rppm_rate(2.0, [mod(4, 259, -0.5)], RppmInputs(is_player=False)).chance_percent == 2.0


def test_rppm_modifiers_compose_multiplicatively_in_order():
    i = RppmInputs(class_id=1, primary_spec=71)
    r = rppm_rate(2.0, [mod(3, 1, 0.5, 1), mod(4, 71, 1.0, 2)], i)
    assert r.chance_percent == 6.0
    assert [s.value for s in r.stages] == [2.0, 3.0, 6.0]


def test_rppm_no_caster_skips_modifiers():
    assert rppm_rate(2.0, [mod(3, 1, 1.0)], RppmInputs(has_caster=False)).chance_percent == 2.0


def test_rppm_fails_closed_on_missing_actor_facts():
    for m in (mod(3, 1, 1.0), mod(4, 1, 1.0), mod(5, 1, 1.0), mod(7, 0, 1.0), mod(8, 1, 1.0)):
        with pytest.raises(UnsupportedSource):
            rppm_rate(1.0, [m], RppmInputs())


def test_rppm_unknown_type_reported_not_hidden():
    r = rppm_rate(1.0, [mod(99, 0, 1.0)], RppmInputs())
    assert r.chance_percent == 1.0 and any("99" in x for x in r.external)


def test_rppm_item_level_modifier():
    points = {528: 100.0, 600: 150.0}.get
    i = RppmInputs(item_level=600)
    assert rppm_rate(2.0, [mod(6, 528, 1.0)], i, points).chance_percent == 3.0
    assert rppm_rate(2.0, [mod(6, 528, 1.0)], RppmInputs(item_level=528), points).chance_percent == 2.0
    with pytest.raises(UnsupportedSource):
        rppm_rate(2.0, [mod(6, 528, 1.0)], i)


def test_race_mask_bits():
    assert race_mask_has_race(1 << 0, 1)
    assert race_mask_has_race(1 << 15, 70) and race_mask_has_race(1 << 16, 52)
    assert not race_mask_has_race(-1, 12)    # removed race id -> bit -1


def test_aura_proc_chance_branches():
    assert aura_proc_chance(101, 0, 0, has_caster=True, has_damage_info=False).chance_percent == 101
    # classic PPM only with DamageInfo
    assert aura_proc_chance(5, 6.0, 0, has_caster=True, has_damage_info=False).chance_percent == 5
    assert aura_proc_chance(5, 6.0, 0, has_caster=True, has_damage_info=True,
                            weapon_speed_ms=2000).chance_percent == 20
    # RPPM overrides classic PPM
    r = rppm_chance(1.0, 10, 120)
    assert aura_proc_chance(5, 6.0, 1.0, has_caster=True, has_damage_info=True, weapon_speed_ms=2000,
                            rppm=r).chance_percent == r.chance_percent
    # no caster: only fixed chance
    assert aura_proc_chance(5, 6.0, 1.0, has_caster=False, has_damage_info=True).chance_percent == 5
    with pytest.raises(UnsupportedSource):
        aura_proc_chance(5, 0, 1.0, has_caster=True, has_damage_info=False)


def test_reduce_proc_60():
    assert reduce_proc_60(30.0, 60) == 30.0
    assert reduce_proc_60(30.0, 75) == pytest.approx(15.0)
    assert reduce_proc_60(30.0, 90) == 0.0
    assert reduce_proc_60(30.0, 120) == 0.0


def test_roll_boundary():
    assert roll_succeeds(100.0, 99.99999)
    assert roll_succeeds(101.0, 0.0)
    assert not roll_succeeds(0.0, 0.0)
    assert not roll_succeeds(30.0, 30.0)
    assert roll_succeeds(30.0, 29.999998)
    with pytest.raises(ValueError):
        roll_succeeds(50.0, 100.0)


def test_weapon_proc_chance():
    assert weapon_proc_chance(True, 2600, False, False, 0) == pytest.approx(4.68, rel=1e-6)
    assert weapon_proc_chance(False, 2600, True, True, 1500) == pytest.approx(2.4, rel=1e-6)
    assert weapon_proc_chance(False, 2600, True, False, 1500) == 0.0


def test_item_and_enchant_chance_sources():
    assert item_on_proc_chance(20, 0.0, 2000, None).chance_percent == 20
    assert item_on_proc_chance(20, 3.0, 2000, None).chance_percent == 10
    with pytest.raises(UnsupportedSource):
        item_on_proc_chance(101, 0.0, 2000, None)
    assert item_on_proc_chance(101, 0.0, 2000, 4.68).model == "item-weapon-proc-chance"
    assert enchant_combat_spell_chance(15, None, 2600, None).chance_percent == 15
    assert enchant_combat_spell_chance(15, {"Chance": 40, "ProcsPerMinute": 0}, 2600, None).chance_percent == 40
    assert enchant_combat_spell_chance(0, {"Chance": 0, "ProcsPerMinute": 6}, 2600, 1.0).chance_percent == 26
    with pytest.raises(UnsupportedSource):
        enchant_combat_spell_chance(0, None, 2600, None)
