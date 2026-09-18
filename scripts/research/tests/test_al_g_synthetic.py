"""Track G synthetic tests: passive/active lifecycle models over explicit fixtures (no snapshot).

Each test names the competing model it rules out.
"""

from __future__ import annotations

import pytest

from aura_lifecycle import FailClosed
from aura_lifecycle import passive as pv


def _facts(*, passive=True, duration_index=0, duration=None, stack_cap=0, charges=0, proc_entry=None,
           proc_type_mask=(0, 0), attrs=None, stances=0, caster_aura_state=0, equipped_item_class=-1,
           interrupts=(0, 0), effects=None, scripts=()):
    a = list(attrs or [0] * 17)
    if passive:
        a[0] |= pv.ATTR0_PASSIVE[1]
    effects = effects or [{"EffectIndex": 0, "Effect": pv.SPELL_EFFECT_APPLY_AURA, "EffectAura": 4,
                           "EffectAuraPeriod": 0, "EffectTriggerSpell": 0}]
    return pv.Facts(
        spell=1, name="synthetic", passive={"passive": passive, "db2_passive": passive,
                                            "reasons": ["db2:SPELL_ATTR0_PASSIVE"] if passive else []},
        effects=effects, attributes=tuple(a), duration_index=duration_index, duration=duration,
        stack_cap=stack_cap, proc_charges_db2=charges, proc_type_mask=proc_type_mask, spell_proc=None,
        proc_entry=proc_entry, scripts=list(scripts), stances=stances, caster_aura_state=caster_aura_state,
        equipped_item_class=equipped_item_class, aura_interrupt_flags=interrupts, build_skew=False)


# -- reapplication taxonomy ------------------------------------------------------

def test_passive_same_caster_no_item_replaces():
    """Rules out 'passive re-application refreshes in place' (IsMultiSlotAura short-circuit + CanStackWith 1651)."""
    r = pv.reapplication(passive=True, same_caster=True, same_spell=True, incoming_cast_item=False,
                         existing_cast_item=False, has_apply_aura_effect=True)
    assert r["outcome"] == "replace" and r["object"] == "new"


def test_passive_item_sourced_coexists():
    """Rules out 'one passive object per SpellId per caster' (rank-of branch SpellAuras.cpp:1760)."""
    r = pv.reapplication(passive=True, same_caster=True, same_spell=True, incoming_cast_item=True,
                         existing_cast_item=True, has_apply_aura_effect=True)
    assert r["outcome"] == "coexist"


def test_passive_without_apply_aura_coexists():
    """IsPassiveStackableWithRanks skips the no-stack pass entirely (Unit.cpp:3718)."""
    r = pv.reapplication(passive=True, same_caster=True, same_spell=True, incoming_cast_item=False,
                         existing_cast_item=False, has_apply_aura_effect=False)
    assert r["outcome"] == "coexist"


def test_active_same_caster_refreshes_existing_object():
    """Contrast: an active finds the existing aura (no multislot) -- rules out 'everything recreates'."""
    r = pv.reapplication(passive=False, same_caster=True, same_spell=True, incoming_cast_item=False,
                         existing_cast_item=False, has_apply_aura_effect=True)
    assert r["outcome"] == "refresh-or-stack" and r["object"] == "existing"


def test_active_mask_mismatch_recreates():
    r = pv.reapplication(passive=False, same_caster=True, same_spell=True, incoming_cast_item=False,
                         existing_cast_item=False, has_apply_aura_effect=True, effect_masks_equal=False)
    assert r["outcome"] == "recreate"


def test_not_highest_exclusive_removes_new():
    r = pv.reapplication(passive=True, same_caster=True, same_spell=True, incoming_cast_item=False,
                         existing_cast_item=False, has_apply_aura_effect=True, new_is_highest_exclusive=False)
    assert r["outcome"] == "new-removed"


@pytest.mark.parametrize("kw", [
    dict(passive=False, same_caster=False, same_spell=True),
    dict(passive=True, same_caster=False, same_spell=True),
    dict(passive=True, same_caster=True, same_spell=False),
])
def test_other_identity_questions_fail_closed(kw):
    with pytest.raises(FailClosed):
        pv.reapplication(incoming_cast_item=False, existing_cast_item=False, has_apply_aura_effect=True, **kw)


def test_item_sourced_passive_area_fails_closed():
    with pytest.raises(FailClosed):
        pv.reapplication(passive=True, same_caster=True, same_spell=True, incoming_cast_item=True,
                         existing_cast_item=True, has_apply_aura_effect=True, is_area=True)


# -- duration ------------------------------------------------------------------

def test_passive_without_duration_entry_is_permanent():
    assert pv.max_duration(_facts())["ms"] == -1


def test_passive_with_duration_entry_is_finite():
    """Rules out 'Passive == permanent': CalcMaxDuration forces -1 only without a DurationEntry."""
    md = pv.max_duration(_facts(duration_index=39, duration={"Duration": 15000, "MaxDuration": 15000}))
    assert md == {**md, "kind": "finite", "ms": 15000}


def test_active_without_duration_entry_is_zero():
    assert pv.max_duration(_facts(passive=False))["ms"] == 0


def test_dangling_duration_index_behaves_like_no_entry():
    md = pv.max_duration(_facts(duration_index=99999, duration=None))
    assert md["kind"] == "permanent" and "no SpellDuration row" in md["note"]


# -- removal -------------------------------------------------------------------

def test_death_keeps_passive_removes_plain_active():
    """Rules out 'death clears every aura'."""
    assert pv.removal_policy(_facts())["owner-death"]["removed"] is False
    assert pv.removal_policy(_facts(passive=False))["owner-death"]["removed"] is True
    persistent = [0] * 17
    persistent[3] = pv.ATTR3_ALLOW_AURA_WHILE_DEAD[1]
    assert pv.removal_policy(_facts(passive=False, attrs=persistent))["owner-death"]["removed"] is False


def test_interrupt_flags_have_no_passive_exemption():
    assert pv.removal_policy(_facts(interrupts=(0, 0x40)))["aura-interrupt"]["removed"] is True
    assert pv.removal_policy(_facts())["aura-interrupt"]["removed"] is False


def test_dispel_and_cancel_never_touch_passives():
    pol = pv.removal_policy(_facts())
    assert pol["dispel"]["removed"] == "never" and pol["player-cancel"]["removed"] == "never"
    assert pol["spell-steal"]["removed"] == "never"


def test_aura_state_loss_enraged_exemption_is_active_only():
    assert pv.removal_policy(_facts(caster_aura_state=pv.AURA_STATE_ENRAGED))["caster-aura-state-lost"]["removed"] is True
    assert pv.removal_policy(_facts(passive=False, caster_aura_state=pv.AURA_STATE_ENRAGED))["caster-aura-state-lost"]["removed"] is False
    assert pv.removal_policy(_facts(passive=False, caster_aura_state=5))["caster-aura-state-lost"]["removed"] is True


def test_shape_lost_needs_stances_and_no_exempting_attrs():
    assert pv.removal_policy(_facts(stances=1))["shapeshift-lost"]["removed"] is True
    a = [0] * 17
    a[2] = pv.ATTR2_ALLOW_WHILE_NOT_SHAPESHIFTED[1]
    assert pv.removal_policy(_facts(stances=1, attrs=a))["shapeshift-lost"]["removed"] is False


def test_charges_from_proc_entry_override_db2_even_when_zero():
    """CalcMaxCharges: the proc entry's Charges wins (SpellAuras.cpp:1007-1008)."""
    assert _facts(charges=3, proc_entry={"charges": 0}).proc_charges == 0
    assert _facts(charges=3).proc_charges == 3


# -- spellmod recalculation / learn ---------------------------------------------

def test_spellmod_recalc_passive_vs_temporary_active():
    assert pv.spellmod_recalculation(_facts())["input_timing"] == "explicit-recalculation"
    temp = _facts(passive=False, duration_index=1, duration={"Duration": 10000, "MaxDuration": 10000})
    assert pv.spellmod_recalculation(temp)["input_timing"] == "application-snapshot"


def test_learn_applies_passive_but_not_plain_active():
    assert pv.learn_applies(_facts())["applies"] is True
    assert pv.learn_applies(_facts(passive=False))["applies"] is False
    a = [0] * 17
    a[1] = pv.ATTR1_CAST_WHEN_LEARNED[1]
    assert pv.learn_applies(_facts(passive=False, attrs=a))["applies"] is True


def test_learn_gates_listed():
    gates = pv.learn_applies(_facts(stances=1, caster_aura_state=3, equipped_item_class=2))["gates"]
    assert len(gates) == 3


# -- interrupt net effect ---------------------------------------------------------

def test_change_spec_resets_trait_passives_but_loses_others():
    assert pv.interrupt_net_effect("ChangeSpec", ["class-trait"]) == "reset"
    assert pv.interrupt_net_effect("ChangeSpec", ["current-gear:on-equip"]) == "lost"
    assert pv.interrupt_net_effect("ChangeTalent", ["class-trait"]) == "not-fired-by-trait-edits"
    assert pv.interrupt_net_effect("StartOfEncounter", ["class-trait"]) == "lost-until-reacquisition"
    assert pv.interrupt_net_effect("SeamlessTransfer", []) == "not-fired-NYI"
    assert pv.interrupt_net_effect("flags1:bit30", []) == "consumer-not-located"


def test_interrupt_flag_names_decodes_both_words():
    assert pv.interrupt_flag_names((0x20000000, 0x40)) == ["Login", "ChangeSpec"]
    assert pv.interrupt_flag_names((0, 1 << 30)) == ["flags1:bit30"]


# -- timelines ----------------------------------------------------------------------

def _tl(tid):
    return next(t for t in pv.timelines() if t["id"] == tid)


def test_tl01_replace_resets_stacks():
    log = _tl("TL-G-01")["log"]
    assert log[1]["state"][0]["stacks"] == 2
    last = log[-1]
    assert last["replaced"] == [1] and [a["obj"] for a in last["state"]] == [2] and last["state"][0]["stacks"] == 1


def test_tl02_active_refresh_keeps_object():
    log = _tl("TL-G-02")["log"]
    assert [a["obj"] for a in log[-1]["state"]] == [1] and log[-1]["state"][0]["remaining_ms"] == 10000


def test_tl03_item_passives_coexist_then_one_removed():
    log = _tl("TL-G-03")["log"]
    assert len(log[1]["state"]) == 2 and [a["obj"] for a in log[-1]["state"]] == [2]


def test_tl04_finite_passive_expires_at_exact_ms_and_returns_only_on_login():
    log = _tl("TL-G-04")["log"]
    exp = next(e for e in log if e["event"] == "expire")
    assert exp["t_ms"] == 15000 and exp["state"] == []
    assert log[-1]["event"] == "create" and log[-1]["t_ms"] == 60000


def test_tl05_spellmod_and_death():
    log = _tl("TL-G-05")["log"]
    mod = next(e for e in log if e["event"] == "spellmod-change")
    assert mod["recalculated"] == [1]
    amounts = {a["obj"]: a["amount"] for a in mod["state"]}
    assert amounts == {1: 12, 2: 10}
    death = log[-1]
    assert death["removed"] == [2] and [a["obj"] for a in death["state"]] == [1]


def test_tl06_charges_exhausted_passive_absent_until_relearn():
    log = _tl("TL-G-06")["log"]
    assert log[1]["event"] == "charges-exhausted" and log[1]["t_ms"] == 700 and log[1]["state"] == []


def test_tl07_rank_change_resets_proc_cooldown():
    log = _tl("TL-G-07")["log"]
    assert log[1]["state"][0]["proc_cooldown_until"] == 13000
    assert log[-1]["state"][0]["obj"] == 2 and log[-1]["state"][0]["proc_cooldown_until"] == 0


def test_tl08_spec_change_resets_glyph_loses():
    log = _tl("TL-G-08")["log"]
    final = {a["spell"] for a in log[-1]["state"]}
    assert final == {900009}
