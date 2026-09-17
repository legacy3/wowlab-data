"""Part D -- swing scheduling (attack timers, haste re-timing, MH/OH interleave, auto-shot cadence).

``test_probe_*`` compare the binary32 emulation with TrinityCore's ApplyAttackTimePercentMod /
resetAttackTimer / parry-haste block / CalcMeleeAttackRageGain compiled in tools/tc_swing_probe.
"""

from __future__ import annotations

import math

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from weapon_combat.attack_table import f32
from weapon_combat.swing import (
    ATTACK_DISPLAY_DELAY, RULES, apply_attack_time_percent_mod, build_corpus, creature_offhand_offset,
    decrement, parry_haste, rage_gain, reset_timer, simulate,
)

from wc_d_helpers import probe_fixture

probe = pytest.fixture(scope="module")(probe_fixture)


def _swings(out, hand=None):
    return [x["t"] for x in out["timeline"] if "swing" in x and (hand is None or x["swing"] == hand)]


def test_rules_are_unique_and_cited():
    ids = [r["id"] for r in RULES]
    assert len(ids) == len(set(ids)) >= 30
    assert all(r["coord"] and r["class"] == "trinity-consumer" for r in RULES)


def test_reset_timer_truncates_float_product():
    assert reset_timer(2600, 1.0) == 2600
    assert reset_timer(2600, f32(1 / 1.2)) == 2166                  # 2166.6666 -> 2166
    assert reset_timer(3600, f32(100 / 110)) == 3272


def test_decrement_saturates_at_zero():
    assert decrement(150, 100) == 50
    assert decrement(50, 100) == 0
    assert decrement(0, 100) == 0


def test_haste_preserves_remaining_fraction_and_round_trip_drift():
    timer, pct, field = apply_attack_time_percent_mod(1300, 2600, 1.0, 30.0, True)
    assert field == 2000 and timer == 1000
    back, pct2, field2 = apply_attack_time_percent_mod(timer, 2600, pct, 30.0, False)
    assert field2 == 2600 and back == 1300
    # a slow (negative value) lengthens the swing
    t, p, f = apply_attack_time_percent_mod(1000, 2000, 1.0, -25.0, True)
    assert f == 2500 and t == 1250


def test_haste_apply_then_remove_can_shorten_the_period_by_one_ms():
    _, p1, _ = apply_attack_time_percent_mod(0, 1800, 1.0, 55.41, True)
    _, p2, field = apply_attack_time_percent_mod(0, 1800, p1, 55.41, False)
    assert p2 == f32(0.99999994) and field == 1799 and reset_timer(1800, p2) == 1799


def test_dual_wield_player_interleave_offhand_pushed_to_200():
    out = simulate(2600, 2600, 0.0, 6000, 100)
    assert _swings(out, "base") == [0, 2600, 5200]
    assert _swings(out, "off") == [200, 2800, 5400]


def test_same_deadline_main_hand_wins_and_hands_never_share_an_update():
    for mh, oh, tick in ((2600, 2600, 1), (2600, 1800, 100), (1500, 2600, 50), (3600, 1300, 7)):
        out = simulate(mh, oh, 0.0, 20000, tick)
        mh_t, oh_t = set(_swings(out, "base")), set(_swings(out, "off"))
        assert not mh_t & oh_t
        assert min(oh_t) - min(mh_t) == math.ceil(ATTACK_DISPLAY_DELAY / tick) * tick


def test_melee_period_is_ceil_of_tick():
    out = simulate(2650, None, 0.0, 12000, 100)
    diffs = {b - a for a, b in zip(_swings(out), _swings(out)[1:])}
    assert diffs == {2700}


def test_creature_offhand_offset_half_swing():
    assert creature_offhand_offset(0, 0, 2000) == 1000
    out = simulate(2000, 2000, 0.0, 5000, 100, is_player=False)
    assert _swings(out, "off")[0] == 900       # set at Attack() (t=0), then decremented in the same update


def test_auto_shot_cadence_is_ceil_plus_one_update():
    for tick in (1, 50, 100, 33):
        out = simulate(2000, None, 0.0, 20000, tick, ranged_speed=3000, melee=False)
        shots = [x["t"] for x in out["timeline"] if x.get("swing") == "ranged"]
        assert shots[0] == tick                                     # prepare at t=0, cast one update later
        assert {b - a for a, b in zip(shots, shots[1:])} == {(math.ceil(3000 / tick) + 1) * tick}


def test_cast_reset_and_pause_and_cast_window():
    reset = simulate(2600, 2600, 0.0, 6000, 100, events=[{"t": 1500, "type": "reset"}])
    assert _swings(reset, "base") == [0, 4000]  # reset precedes that update's decrement
    pause = simulate(2600, None, 0.0, 6000, 100, events=[{"t": 500, "type": "pause"}, {"t": 1500, "type": "unpause"}])
    assert _swings(pause) == [0, 3600]                              # frozen for 10 updates
    window = simulate(2600, None, 0.0, 6000, 100, events=[{"t": 2000, "type": "cast_start"}, {"t": 4000, "type": "cast_end"}])
    assert _swings(window) == [0, 4000]                             # timer ran out during the cast, swing waits


def test_extra_attacks_drain_before_decrement_without_touching_timer():
    out = simulate(2600, None, 0.0, 4000, 100, events=[{"t": 1000, "type": "extra_attack", "count": 2}])
    extra = [x for x in out["timeline"] if x.get("extra")]
    assert [x["t"] for x in extra] == [1000, 1000]
    assert _swings(out) == [0, 1000, 1000, 2600]


def test_parry_haste_and_rage():
    assert parry_haste(0, 1500, 2600, 2600, False) == (0, 520)      # (20%, 60%] -> 20%
    assert parry_haste(0, 2000, 2600, 2600, False) == (0, 960)      # > 60% -> minus 40%
    assert parry_haste(0, 2500, 2600, 2600, False) == (0, 1460)     # > 60% -> minus 40%
    assert parry_haste(0, 400, 2600, 2600, False) == (0, 400)
    assert parry_haste(1000, 2500, 2000, 2600, True) == (400, 2500)  # OH is the next swing
    assert rage_gain(3600, "base") == 6 and rage_gain(3600, "off") == 3 and rage_gain(3000, "ranged") == 0


def test_corpus_deterministic():
    assert build_corpus("g") == build_corpus("g")


# --------------------------------------------------------------------------- differential

@settings(max_examples=150, deadline=None)
@given(timer=st.integers(0, 6000), base=st.integers(100, 6000),
       pct=st.floats(0.25, 2.0, width=32), val=st.floats(-60, 120, width=32).filter(lambda v: abs(v) > 1e-3),
       apply=st.booleans())
def test_probe_attack_time_percent_mod(probe, timer, base, pct, val, apply):
    timer = min(timer, reset_timer(base, pct))
    got = probe.ask(f"atkmod {timer} {base} {pct!r} {val!r} {int(apply)}").split()
    t, p, f = apply_attack_time_percent_mod(timer, base, pct, val, apply)
    assert int(got[0]) == t
    assert float(got[1]) == pytest.approx(p, rel=1e-8) and f"{p:.9g}" == got[1]
    assert int(got[2]) == f


@settings(max_examples=80, deadline=None)
@given(base=st.integers(1, 10000), pct=st.floats(0.25, 3.0, width=32), att=st.sampled_from([0, 1, 2]))
def test_probe_reset_timer(probe, base, pct, att):
    assert int(probe.ask(f"reset {base} {pct!r} {att}")) == reset_timer(base, pct)


@settings(max_examples=80, deadline=None)
@given(off=st.integers(0, 4000), mh=st.integers(0, 4000), base_off=st.integers(1000, 4000), base_mh=st.integers(1000, 4000))
def test_probe_parry_haste_main_hand_path(probe, off, mh, base_off, base_mh):
    probe.ask("clear")          # victim without an off hand -> BASE path
    got = probe.ask(f"parry {off} {mh} {base_off} {base_mh}").split()
    assert (int(got[0]), int(got[1])) == parry_haste(off, mh, base_off, base_mh, False)


@settings(max_examples=80, deadline=None)
@given(base=st.sampled_from([1500, 1800, 2000, 2400, 2600, 3600]), val=st.floats(1, 60, width=32))
def test_probe_haste_round_trip_drift(probe, base, val):
    first = probe.ask(f"atkmod 0 {base} 1 {val!r} 1").split()
    second = probe.ask(f"atkmod 0 {base} {first[1]} {val!r} 0").split()
    _, p1, _ = apply_attack_time_percent_mod(0, base, 1.0, val, True)
    _, p2, f2 = apply_attack_time_percent_mod(0, base, p1, val, False)
    assert first[1] == f"{p1:.9g}" and second[1] == f"{p2:.9g}" and int(second[2]) == f2


def test_probe_parry_haste_offhand_path_and_rage(probe):
    probe.ask("clear")
    probe.ask("set v.offhand 1")
    for off, mh, bo, bm in ((1000, 2500, 2000, 2600), (1500, 2500, 2000, 2600), (300, 2500, 2000, 2600), (2000, 1000, 2000, 2600)):
        got = probe.ask(f"parry {off} {mh} {bo} {bm}").split()
        assert (int(got[0]), int(got[1])) == parry_haste(off, mh, bo, bm, True)
    for base in (1000, 1300, 2600, 3600, 3900):
        assert int(probe.ask(f"rage {base} 0")) == rage_gain(base, "base")
        assert int(probe.ask(f"rage {base} 1")) == rage_gain(base, "off")
