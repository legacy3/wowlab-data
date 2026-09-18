"""Track C: synthetic tests of the stack/charge state model.  Each test names the wrong model it rules out."""

from __future__ import annotations

import pytest

from aura_lifecycle import FailClosed
from aura_lifecycle import charges as C
from aura_lifecycle import stacks as S


def shape(cap: int, **kw) -> S.AuraShape:
    kw.setdefault("max_duration", 10000)
    return S.AuraShape.plain(cap, **kw)


def test_initial_stacks_ignore_capacity():
    """Rules out 'initial stacks = CumulativeAura' (Phalanx: capacity 2 is not initial 2)."""
    st = S.initial_state(shape(2))
    assert st.stacks == 1
    assert S.initial_stacks() == 1


def test_initial_stacks_override_replaces_doses_and_narrows():
    """Rules out 'Doses and SPELLVALUE_AURA_STACK add up' and 'initial stacks are clamped to capacity'."""
    assert S.initial_stacks(doses_mod=7) == 7                      # Improved Sweeping Strikes: 1 + 6
    assert S.initial_stacks(override=3, doses_mod=7) == 3          # override applied after the Spell ctor
    assert S.initial_stacks(doses_mod=-98) == 1                    # Therazane's Resilience: floor to 1
    assert S.initial_stacks(override=256) == 1                     # uint8(256) = 0 -> floor 1
    assert S.initial_stacks(doses_mod=256) == 0                    # int32 256 passes the floor, uint8 member -> 0
    assert S.initial_state(shape(0), 2).stacks == 2                # capacity 0 does not cap construction


def test_cap_applies_only_to_increases():
    """Rules out 'stacks are always clamped to CalcMaxStackAmount'."""
    sh = shape(5, max_stacks=3)
    st = S.initial_state(sh, 5)
    S.mod_stack_amount(st, sh, -1)
    assert st.stacks == 4                                          # decrease: no clamp to 3
    S.mod_stack_amount(st, sh, +1)
    assert st.stacks == 3                                          # increase: clamped (a drop!)


def test_zero_capacity_pins_to_one_and_lowering_does_not_refresh():
    """Rules out 'reapplication never lowers stacks' and 'reapplication always refreshes'."""
    sh = shape(0, max_charges=2)
    st = S.initial_state(sh, 2)                                    # e.g. old Stormkeeper + Doses +1
    st.charges, st.duration = 1, 4000
    ev = S.mod_stack_amount(st, sh, 2)
    assert st.stacks == 1 and st.duration == 4000 and st.charges == 1
    assert ev[-1]["op"] == "no_refresh"


def test_refresh_gate_unique_only_matters_at_capacity_zero():
    """Rules out 'ATTR1_AURA_UNIQUE always blocks refresh' and 'capacity 0 == capacity 1'."""
    for cap, refreshed in ((0, False), (1, True), (3, True)):
        sh = shape(cap, aura_unique=True)
        st = S.initial_state(sh)
        st.duration = 1
        S.mod_stack_amount(st, sh, 1)
        assert (st.duration == 10000) is refreshed, cap
    sh = shape(0, aura_unique_per_caster=True)
    st = S.initial_state(sh)
    st.duration = 1
    S.mod_stack_amount(st, sh, 1)
    assert st.duration == 1


def test_zero_change_refreshes():
    """Rules out 'ModStackAmount(0) is a no-op' (HandleAuraLinked syncs with a zero diff)."""
    sh = shape(4, max_charges=3)
    st = S.initial_state(sh, 2)
    st.duration, st.charges = 5, 1
    ev = S.mod_stack_amount(st, sh, 0)
    assert st.duration == 10000 and st.charges == 3 and ev[-1]["op"] == "refresh"


def test_removal_mode_passes_through_and_set_zero_does_not_remove():
    """Rules out 'reaching zero stacks always removes' (SetStackAmount(0) keeps a live aura)."""
    sh = shape(5)
    st = S.initial_state(sh, 2)
    S.mod_stack_amount(st, sh, -2, "AURA_REMOVE_BY_ENEMY_SPELL")
    assert st.removed and st.remove_mode == "AURA_REMOVE_BY_ENEMY_SPELL"
    st = S.initial_state(sh, 2)
    S.set_stack_amount(st, 0)
    assert st.stacks == 0 and not st.removed
    with pytest.raises(FailClosed):
        S.mod_stack_amount(S.StackState(1, 0, False, 1, 1, removed=True), sh, 1)


def test_uint8_wrap_and_negative_max():
    """Rules out 'stack counts are unbounded integers' and 'a negative max makes the aura non-stacking'."""
    sh = shape(300)
    st = S.initial_state(sh, 255)
    S.mod_stack_amount(st, sh, 1)
    assert st.stacks == 0 and not st.removed
    sh = shape(9, max_stacks=S.calc_max_stack_amount(9, (-99, 1.0)))
    st = S.initial_state(sh)
    ev = S.mod_stack_amount(st, sh, 1)
    assert st.stacks == 166 and ev[-1]["op"] == "no_refresh"


def test_pandemic_suppresses_periodic_reset_and_hit_reset_rule():
    """Rules out 'stacking reapplications reset the periodic timer' and 'pandemic resets it'."""
    assert S.reset_periodic_on_hit(1) and not S.reset_periodic_on_hit(2)
    assert not S.reset_periodic_on_hit(0, dont_reset_trigger_flag=True)
    ev = S.mod_stack_amount(S.initial_state(shape(0, pandemic=True)), shape(0, pandemic=True), 1, reset_periodic=True)
    assert [e for e in ev if e["op"] == "refresh_timers"][0]["reset_periodic_timer"] is False


def test_charges_model():
    """Rules out 'ModCharges works on charge-less auras', 'charges are capped on decrease' and
    'USE_STACKS_FOR_CHARGES drops a charge'."""
    sh = shape(0, max_charges=3)
    st = S.initial_state(sh)
    assert st.charges == 3 and st.using_charges
    C.mod_charges(st, sh, 5)
    assert st.charges == 3
    st.charges = 7                                                 # manual SetCharges above max
    C.mod_charges(st, sh, -1)
    assert st.charges == 6
    none = S.initial_state(shape(0))
    assert C.mod_charges(none, shape(0), -1)[0]["op"] == "mod_charges_ignored" and not none.removed
    st = S.initial_state(sh)
    C.consume_proc(st, sh, use_stacks_for_charges=True)
    assert st.removed and st.charges == 3                          # stack 1 -> 0 removed; charges untouched
    st = S.initial_state(sh)
    C.consume_proc(st, sh, use_stacks_for_charges=False, no_consume_event=True)
    assert st.charges == 3 and not st.removed                      # ATTR6_DO_NOT_CONSUME_RESOURCES event
    assert C.calc_max_charges(15, None) == 15 and C.calc_max_charges(0, 4) == 4
    assert C.calc_max_charges(999999, None) == 63                  # uint8 narrowing of a DB2 value
    assert C.calc_max_charges(0, None, (-2, 1.0)) == 254           # uint32(-2.0) (UB) as gcc produces it


def test_amount_scaling():
    """Rules out 'only periodic auras scale with stacks' and 'SuppressPointsStacking is per aura type'."""
    mod_stat = 29  # SPELL_AURA_MOD_STAT: not a periodic type
    assert S.scale_amount(10.0, 3, False, mod_stat) == 30.0
    assert S.scale_amount(10.0, 3, True, mod_stat) == 10.0
    assert S.scale_amount(2.5, 1, False, 3) == 3.0                 # PERIODIC_DAMAGE rounds half away from zero
    assert S.scale_amount(-2.5, 1, False, 3) == -3.0
    assert S.scale_amount(2.5, 1, False, mod_stat) == 2.5
    assert S.scale_amount(1.5e9, 3, False, mod_stat) == S.EFFECT_VALUE_MAX
    assert S.stack_amount_for_bonuses(4, False) == 4 and S.stack_amount_for_bonuses(4, True) == 1
    assert S.refresh_base_amount(5.0, 3.0, True) == 8.0 and S.refresh_base_amount(5.0, 3.0, False) == 3.0


def test_client_applications():
    """Rules out 'the client always shows stacks'."""
    assert S.client_applications(0, 1, 3) == 3
    assert S.client_applications(1, 1, 3) == 1
    assert S.client_applications(0, 2, 3) == 2


def test_reapply_paths():
    """Rules out 'passive auras stack by reapplication' and 'mask-mismatched reapply stacks'."""
    sh = shape(2)
    st = S.initial_state(sh)
    assert S.reapply(st, sh, S.Reapply(multislot=True))[0]["op"] == "create_new" and st.stacks == 1
    assert S.reapply(st, sh, S.Reapply(effect_mask_matches=False))[0]["op"] == "create_new"
    S.reapply(st, sh, S.Reapply(stack_amount=7))
    assert st.stacks == 2
    assert S.owned_aura_key(True, 11, False, 5) == (0, 0)
    assert S.owned_aura_key(False, 11, True, 5) == (11, 5)


def test_timeline_expiry_and_order():
    tl = S.run_timeline(shape(0), [{"t": 0, "op": "apply"}, {"t": 12000, "op": "reapply"}])
    assert tl[1]["op"] == "expired" and tl[1]["t"] == 10000
    assert tl[2]["events"][0]["op"] == "create_new"
    with pytest.raises(FailClosed):
        S.run_timeline(shape(0), [{"t": 5, "op": "apply"}, {"t": 1, "op": "reapply"}])
