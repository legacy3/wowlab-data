"""Pre-RNG eligibility: flag, mask, phase, hit, side and suppression semantics."""

from __future__ import annotations

import itertools
from dataclasses import replace

import pytest
from proc_synthetic import Eff, FakeOverlay, Spell, world

from procs import enums as E
from procs.definition import ProcEntry
from procs.eligibility import ActorFacts, HolderState, can_trigger
from procs.events import (
    melee_hit_mask,
    melee_swing,
    periodic_damage,
    periodic_heal,
    reflect_event,
    simple_event,
    spell_cast,
    spell_finish,
    spell_hit,
    spell_hit_mask,
)

ALL_NAMED_FLAGS = list(E.PROC_FLAG_NAMES)
PROVIDER = 100
HARMFUL_SPELL = 200     # DmgClass MAGIC, damaging
HELPFUL_SPELL = 201     # heal
MELEE_ABILITY = 202     # DmgClass MELEE
TRIGGERED = 203         # NOT_A_PROC variant is 204
NOT_A_PROC = 204
SUPPRESSOR = 205        # SUPPRESS_CASTER_PROCS
ABILITY = 206           # IS_ABILITY, magic
PERIODIC_SPELL = 207    # TREAT_AS_PERIODIC


def entry(**kw) -> ProcEntry:
    base = dict(school_mask=0, family_name=0, family_mask=0, proc_flags=0,
                spell_type_mask=0, spell_phase_mask=0, hit_mask=0, attributes_mask=0,
                disable_effects_mask=0, procs_per_minute=0.0, chance=100.0, cooldown_ms=0,
                charges=0, origin="generated", keyed_difficulty=0)
    base.update(kw)
    return ProcEntry(**base)


def trig(e, tm, **kw):
    args = dict(spell_type_mask=kw.pop("spell_type", 7), spell_phase_mask=kw.pop("phase", 2),
                hit_mask=kw.pop("hit", E.PROC_HIT_NORMAL), school_mask=kw.pop("school", 1),
                has_proc_spell=kw.pop("has_spell", True), power_cost_positive=kw.pop("cost", True),
                event_family=kw.pop("family", (0, 0)), actor_is_player=kw.pop("player", True),
                has_action_target=kw.pop("target", True), honor_target=kw.pop("honor", True))
    return can_trigger(e, tm, **args)


# -- CanSpellTriggerProcOnEvent, pure ----------------------------------------

@pytest.mark.parametrize("flag", ALL_NAMED_FLAGS)
def test_every_named_flag_matches_itself_only(flag):
    e = entry(proc_flags=flag, spell_phase_mask=7)
    assert trig(e, flag)
    for other in ALL_NAMED_FLAGS:
        if other != flag:
            assert not trig(e, other), (E.PROC_FLAG_NAMES[flag], E.PROC_FLAG_NAMES[other])


def test_overlapping_flags_match_on_any_intersection():
    e = entry(proc_flags=E.PROC_FLAG_DEAL_MELEE_SWING | E.PROC_FLAG_DEAL_HARMFUL_SPELL, spell_phase_mask=2)
    assert trig(e, E.PROC_FLAG_DEAL_MELEE_SWING | E.PROC_FLAG_MAIN_HAND_WEAPON_SWING)
    assert trig(e, E.PROC_FLAG_DEAL_HARMFUL_SPELL)
    assert not trig(e, E.PROC_FLAG_TAKE_MELEE_SWING)


def test_phase_check_uses_the_whole_event_mask_not_the_intersection():
    # Entry only listens for MAIN_HAND_WEAPON_SWING (not a phase flag) but the
    # event also carries DEAL_MELEE_ABILITY, so the phase check applies.
    e = entry(proc_flags=E.PROC_FLAG_MAIN_HAND_WEAPON_SWING, spell_phase_mask=0)
    ev = E.PROC_FLAG_DEAL_MELEE_ABILITY | E.PROC_FLAG_MAIN_HAND_WEAPON_SWING
    assert not trig(e, ev, phase=E.PROC_SPELL_PHASE_HIT)
    assert trig(e, E.PROC_FLAG_DEAL_MELEE_SWING | E.PROC_FLAG_MAIN_HAND_WEAPON_SWING, phase=0)


@pytest.mark.parametrize("entry_phase,event_phase,ok", [
    (2, 2, True), (2, 1, False), (2, 4, False), (1, 1, True), (4, 4, True), (7, 4, True), (0, 2, False)])
def test_spell_phase(entry_phase, event_phase, ok):
    e = entry(proc_flags=E.PROC_FLAG_DEAL_HARMFUL_SPELL, spell_phase_mask=entry_phase)
    assert trig(e, E.PROC_FLAG_DEAL_HARMFUL_SPELL, phase=event_phase) is ok


def test_default_hit_mask_done_vs_taken():
    done = entry(proc_flags=E.PROC_FLAG_DEAL_HARMFUL_SPELL, spell_phase_mask=2)
    taken = entry(proc_flags=E.PROC_FLAG_TAKE_HARMFUL_SPELL)
    for bit in E.HIT_NAMES:
        exp_done = bit in (E.PROC_HIT_NORMAL, E.PROC_HIT_CRITICAL, E.PROC_HIT_ABSORB)
        exp_taken = bit in (E.PROC_HIT_NORMAL, E.PROC_HIT_CRITICAL)
        assert trig(done, E.PROC_FLAG_DEAL_HARMFUL_SPELL, hit=bit) is exp_done, E.HIT_NAMES[bit]
        assert trig(taken, E.PROC_FLAG_TAKE_HARMFUL_SPELL, hit=bit) is exp_taken, E.HIT_NAMES[bit]


@pytest.mark.parametrize("bit", list(E.HIT_NAMES))
def test_explicit_hit_mask_is_any_match(bit):
    e = entry(proc_flags=E.PROC_FLAG_TAKE_MELEE_SWING, hit_mask=bit | E.PROC_HIT_DODGE)
    assert trig(e, E.PROC_FLAG_TAKE_MELEE_SWING, hit=bit, phase=0)
    assert not trig(e, E.PROC_FLAG_TAKE_MELEE_SWING, hit=E.PROC_HIT_MASK_ALL & ~(bit | E.PROC_HIT_DODGE), phase=0)


def test_cast_phase_skips_hit_mask_for_done_events():
    e = entry(proc_flags=E.PROC_FLAG_DEAL_HARMFUL_SPELL, spell_phase_mask=1, hit_mask=E.PROC_HIT_CRITICAL)
    assert trig(e, E.PROC_FLAG_DEAL_HARMFUL_SPELL, phase=1, hit=E.PROC_HIT_NORMAL)
    assert not trig(e, E.PROC_FLAG_DEAL_HARMFUL_SPELL, phase=2, hit=E.PROC_HIT_NORMAL)


def test_melee_swing_has_no_phase_but_is_hit_checked():
    e = entry(proc_flags=E.PROC_FLAG_DEAL_MELEE_SWING)
    assert trig(e, E.PROC_FLAG_DEAL_MELEE_SWING, phase=0, hit=E.PROC_HIT_NORMAL)
    assert not trig(e, E.PROC_FLAG_DEAL_MELEE_SWING, phase=0, hit=E.PROC_HIT_DODGE)


@pytest.mark.parametrize("flag", [E.PROC_FLAG_HEARTBEAT, E.PROC_FLAG_KILL, E.PROC_FLAG_DEATH])
def test_always_trigger_types_skip_later_checks(flag):
    e = entry(proc_flags=flag, school_mask=0x4, hit_mask=E.PROC_HIT_CRITICAL, spell_type_mask=2)
    assert trig(e, flag, school=0x1, hit=0, spell_type=1)


def test_always_trigger_does_not_skip_attribute_checks():
    e = entry(proc_flags=E.PROC_FLAG_KILL, attributes_mask=E.PROC_ATTR_REQ_EXP_OR_HONOR)
    assert not trig(e, E.PROC_FLAG_KILL, honor=False)
    assert trig(e, E.PROC_FLAG_KILL, honor=False, player=False)
    assert trig(e, E.PROC_FLAG_KILL, honor=False, target=False)


def test_school_and_type_masks():
    e = entry(proc_flags=E.PROC_FLAG_DEAL_HARMFUL_SPELL, spell_phase_mask=2, school_mask=0x4,
              spell_type_mask=E.PROC_SPELL_TYPE_HEAL)
    assert not trig(e, E.PROC_FLAG_DEAL_HARMFUL_SPELL, school=0x2)
    assert not trig(e, E.PROC_FLAG_DEAL_HARMFUL_SPELL, school=0x4, spell_type=1)
    assert trig(e, E.PROC_FLAG_DEAL_HARMFUL_SPELL, school=0x6, spell_type=3)


def test_type_mask_only_applies_to_spell_flags():
    e = entry(proc_flags=E.PROC_FLAG_DEAL_MELEE_SWING, spell_type_mask=E.PROC_SPELL_TYPE_HEAL)
    assert trig(e, E.PROC_FLAG_DEAL_MELEE_SWING, spell_type=0, phase=0)


def test_family_matching():
    e = entry(proc_flags=E.PROC_FLAG_DEAL_HARMFUL_SPELL, spell_phase_mask=2, family_name=8, family_mask=0x10)
    assert trig(e, E.PROC_FLAG_DEAL_HARMFUL_SPELL, family=(8, 0x30))
    assert not trig(e, E.PROC_FLAG_DEAL_HARMFUL_SPELL, family=(8, 0x20))
    assert not trig(e, E.PROC_FLAG_DEAL_HARMFUL_SPELL, family=(9, 0x10))
    assert trig(e, E.PROC_FLAG_DEAL_HARMFUL_SPELL, family=None)  # no SpellInfo: skipped
    any_family = entry(proc_flags=E.PROC_FLAG_DEAL_HARMFUL_SPELL, spell_phase_mask=2, family_name=8)
    assert trig(any_family, E.PROC_FLAG_DEAL_HARMFUL_SPELL, family=(8, 0))


def test_power_cost_requirement():
    e = entry(proc_flags=E.PROC_FLAG_DEAL_HARMFUL_SPELL, spell_phase_mask=2,
              attributes_mask=E.PROC_ATTR_REQ_POWER_COST)
    assert not trig(e, E.PROC_FLAG_DEAL_HARMFUL_SPELL, has_spell=False)
    assert not trig(e, E.PROC_FLAG_DEAL_HARMFUL_SPELL, cost=False)
    assert trig(e, E.PROC_FLAG_DEAL_HARMFUL_SPELL, cost=True)
    assert trig(e, E.PROC_FLAG_DEAL_HARMFUL_SPELL, cost=None) is None


# -- event builders -------------------------------------------------------------

def test_melee_hit_masks():
    assert melee_hit_mask("normal") == E.PROC_HIT_NORMAL
    assert melee_hit_mask("crit") == E.PROC_HIT_CRITICAL
    assert melee_hit_mask("glancing") == E.PROC_HIT_NORMAL
    assert melee_hit_mask("crit", full_absorb=True) == E.PROC_HIT_ABSORB
    assert melee_hit_mask("normal", absorb=True) == E.PROC_HIT_NORMAL | E.PROC_HIT_ABSORB
    assert melee_hit_mask("block", blocked_amount=True) == E.PROC_HIT_NORMAL | E.PROC_HIT_BLOCK
    assert melee_hit_mask("normal", immune=True) == E.PROC_HIT_IMMUNE
    assert melee_hit_mask("dodge") == E.PROC_HIT_DODGE


def test_spell_hit_masks():
    assert spell_hit_mask("block") == E.PROC_HIT_BLOCK | E.PROC_HIT_FULL_BLOCK
    assert spell_hit_mask("none", crit=True, absorb=True) == E.PROC_HIT_CRITICAL | E.PROC_HIT_ABSORB
    assert spell_hit_mask("none", full_resist=True) == E.PROC_HIT_FULL_RESIST
    assert spell_hit_mask("reflect") == E.PROC_HIT_REFLECT


def test_melee_swing_sides():
    ev = melee_swing("off", damage=0)
    assert ev.actor_mask == E.PROC_FLAG_DEAL_MELEE_SWING | E.PROC_FLAG_OFF_HAND_WEAPON_SWING
    assert ev.target_mask == E.PROC_FLAG_TAKE_MELEE_SWING
    assert melee_swing(damage=5).target_mask & E.PROC_FLAG_TAKE_ANY_DAMAGE
    assert not ev.has_proc_spell and ev.has_damage_info and ev.spell_id is None


# -- whole evaluator on a synthetic world ---------------------------------------

def spells(provider: Spell) -> list[Spell]:
    A3 = E.ATTR3_NOT_A_PROC[1]
    return [
        provider,
        Spell(HARMFUL_SPELL, dmg_class=1, family=8, family_flags=(0x10, 0, 0, 0),
              effects=[Eff(effect=2, aura=0)], school=0x4),
        Spell(HELPFUL_SPELL, dmg_class=1, effects=[Eff(effect=10, aura=0)], school=0x2),
        Spell(MELEE_ABILITY, dmg_class=2, effects=[Eff(effect=2, aura=0)]),
        Spell(TRIGGERED, dmg_class=1, effects=[Eff(effect=2, aura=0)]),
        Spell(NOT_A_PROC, dmg_class=1, attributes={3: A3}, effects=[Eff(effect=2, aura=0)]),
        Spell(SUPPRESSOR, dmg_class=1, attributes={3: E.ATTR3_SUPPRESS_CASTER_PROCS[1] | E.ATTR3_SUPPRESS_TARGET_PROCS[1]},
              effects=[Eff(effect=2, aura=0)]),
        Spell(ABILITY, dmg_class=1, attributes={0: E.ATTR0_IS_ABILITY[1]}, effects=[Eff(effect=2, aura=0)]),
        Spell(PERIODIC_SPELL, dmg_class=1, attributes={3: E.ATTR3_TREAT_AS_PERIODIC[1]},
              effects=[Eff(effect=2, aura=0)]),
    ]


def make(tmp_path, flags, overlay=None, **provider_kw):
    base = dict(proc_flags=flags, proc_chance=100, effects=[Eff(aura=42, trigger=HELPFUL_SPELL)])
    base.update(provider_kw)
    cat, defs, ev = world(tmp_path, spells(Spell(PROVIDER, **base)), overlay=overlay)
    return cat, defs.get(PROVIDER), ev


FACTS = ActorFacts(actor_level=80, action_target_gives_xp_or_honor=True, holder_outdoors=True)


def verdict(ev, d, event, holder="actor", state=None, facts=FACTS):
    return ev.evaluate(d, event, holder, state, facts)


def test_direct_spell_crit_vs_periodic_heal(tmp_path):
    cat, d, ev = make(tmp_path, E.PROC_FLAG_DEAL_HARMFUL_SPELL)
    hit = spell_hit(cat.require(HARMFUL_SPELL), damage=10, crit=True)
    assert verdict(ev, d, hit).verdict == "eligible"
    heal = periodic_heal(cat.require(HELPFUL_SPELL), crit=True)
    assert verdict(ev, d, heal).reason == "proc-flags-intersect"


def test_periodic_heal_provider(tmp_path):
    cat, d, ev = make(tmp_path, E.PROC_FLAG_DEAL_HELPFUL_PERIODIC)
    heal = periodic_heal(cat.require(HELPFUL_SPELL))
    res = verdict(ev, d, heal)
    assert res.verdict == "eligible"
    assert any(c.step == "spell-object-gates" and c.outcome == "skip" for c in res.checks)


def test_white_melee_hit_vs_melee_ability(tmp_path):
    cat, d, ev = make(tmp_path, E.PROC_FLAG_DEAL_MELEE_SWING)
    assert verdict(ev, d, melee_swing()).verdict == "eligible"
    ability = spell_hit(cat.require(MELEE_ABILITY), damage=5)
    assert ability.actor_mask == E.PROC_FLAG_DEAL_MELEE_ABILITY | E.PROC_FLAG_MAIN_HAND_WEAPON_SWING
    assert verdict(ev, d, ability).reason == "proc-flags-intersect"
    _, d2, ev2 = make(tmp_path / "mh", E.PROC_FLAG_MAIN_HAND_WEAPON_SWING)
    assert verdict(ev2, d2, melee_swing()).verdict == "eligible"
    assert verdict(ev2, d2, ability).verdict == "eligible"
    assert verdict(ev2, d2, melee_swing("off")).reason == "proc-flags-intersect"


def test_absorbed_incoming_spell(tmp_path):
    cat, d, ev = make(tmp_path, E.PROC_FLAG_TAKE_HARMFUL_SPELL)
    absorbed = spell_hit(cat.require(HARMFUL_SPELL), damage=10).with_(hit_mask=E.PROC_HIT_ABSORB)
    assert verdict(ev, d, absorbed, "target").reason == "hit-mask"   # taken default excludes ABSORB
    d.entry = replace(d.entry, hit_mask=E.PROC_HIT_ABSORB)   # as a spell_proc row could author
    assert verdict(ev, d, absorbed, "target").verdict == "eligible"
    assert verdict(ev, d, absorbed, "actor").reason == "proc-flags-intersect"


def test_done_vs_taken_side(tmp_path):
    cat, d, ev = make(tmp_path, E.PROC_FLAG_TAKE_HARMFUL_SPELL)
    hit = spell_hit(cat.require(HARMFUL_SPELL), damage=10)
    assert verdict(ev, d, hit, "target").verdict == "eligible"
    assert verdict(ev, d, hit, "actor").reason == "proc-flags-intersect"
    cast = spell_cast(cat.require(HARMFUL_SPELL), positive=False)
    assert verdict(ev, d, cast, "target").reason == "side-receives-event"


def test_damage_vs_healing_type(tmp_path):
    cat, d, ev = make(tmp_path, E.PROC_FLAG_DEAL_HARMFUL_SPELL | E.PROC_FLAG_DEAL_HELPFUL_SPELL)
    d.entry = replace(d.entry, spell_type_mask=E.PROC_SPELL_TYPE_HEAL)
    heal = spell_hit(cat.require(HELPFUL_SPELL), healing=10)
    dmg = spell_hit(cat.require(HARMFUL_SPELL), damage=10)
    assert heal.actor_mask == E.PROC_FLAG_DEAL_HELPFUL_SPELL
    assert verdict(ev, d, heal).verdict == "eligible"
    assert verdict(ev, d, dmg).reason == "spell-type-mask"


def test_ability_and_treat_as_periodic_identity(tmp_path):
    cat, _, _ = make(tmp_path, E.PROC_FLAG_DEAL_HARMFUL_SPELL)
    assert spell_hit(cat.require(ABILITY), damage=1).actor_mask == E.PROC_FLAG_DEAL_HARMFUL_ABILITY
    per = spell_hit(cat.require(PERIODIC_SPELL), damage=1)
    assert per.actor_mask == E.PROC_FLAG_DEAL_HARMFUL_PERIODIC and per.has_proc_spell
    with pytest.raises(ValueError):
        spell_hit(cat.require(ABILITY))  # no damage/heal: positivity is external
    assert spell_hit(cat.require(ABILITY), all_hit_effects_positive=True).actor_mask == E.PROC_FLAG_DEAL_HELPFUL_ABILITY


def test_cast_and_finish_phases(tmp_path):
    cat, d, ev = make(tmp_path, E.PROC_FLAG_DEAL_HARMFUL_SPELL)
    info = cat.require(HARMFUL_SPELL)
    cast = spell_cast(info, positive=False)
    finish = spell_finish(info, positive=False, spell_type_mask=None, hit_mask=None)
    assert cast.actor_mask & E.PROC_FLAG_2_CAST_SUCCESSFUL
    assert verdict(ev, d, cast).reason == "spell-phase-mask"     # generated entries use HIT
    assert verdict(ev, d, finish).reason == "spell-phase-mask"


def test_triggered_child_spell(tmp_path):
    cat, d, ev = make(tmp_path, E.PROC_FLAG_DEAL_HARMFUL_SPELL)
    trig_hit = spell_hit(cat.require(TRIGGERED), damage=5, spell_is_triggered=True)
    assert verdict(ev, d, trig_hit).reason == "triggered-spell-gate"
    nap = spell_hit(cat.require(NOT_A_PROC), damage=5, spell_is_triggered=True)
    assert verdict(ev, d, nap).verdict == "eligible"
    # a periodic tick of an aura applied by a triggered spell carries no Spell object
    tick = periodic_damage(cat.require(TRIGGERED), damage=5)
    _, dp, evp = make(tmp_path / "p", E.PROC_FLAG_DEAL_HARMFUL_PERIODIC)
    assert verdict(evp, dp, tick).verdict == "eligible"


def test_triggered_can_proc_attribute(tmp_path):
    # Without a cooldown this provider trips the LoadSpellProcs infinite-loop guard.
    _, guarded, _ = make(tmp_path / "g", E.PROC_FLAG_DEAL_HARMFUL_SPELL,
                         attributes={3: E.ATTR3_CAN_PROC_FROM_PROCS[1]})
    assert guarded.entry is None and guarded.generation_reason == "infinite-loop-guard"
    cat, d, ev = make(tmp_path, E.PROC_FLAG_DEAL_HARMFUL_SPELL, proc_cooldown=1,
                      attributes={3: E.ATTR3_CAN_PROC_FROM_PROCS[1]})
    trig_hit = spell_hit(cat.require(TRIGGERED), damage=5, spell_is_triggered=True)
    assert verdict(ev, d, trig_hit).verdict == "eligible"


def test_self_triggered_is_suppressed_even_with_can_proc_from_procs(tmp_path):
    cat, d, ev = make(tmp_path, E.PROC_FLAG_DEAL_HARMFUL_SPELL, proc_cooldown=1,
                      attributes={3: E.ATTR3_CAN_PROC_FROM_PROCS[1]})
    own = spell_hit(cat.require(TRIGGERED), damage=5, spell_is_triggered=True,
                    spell_triggered_by_aura=PROVIDER)
    assert verdict(ev, d, own).reason == "not-triggered-by-this-aura"


def test_suppress_caster_and_target_procs(tmp_path):
    cat, d, ev = make(tmp_path, E.PROC_FLAG_DEAL_HARMFUL_SPELL | E.PROC_FLAG_TAKE_HARMFUL_SPELL)
    hit = spell_hit(cat.require(SUPPRESSOR), damage=5)
    assert verdict(ev, d, hit, "actor").reason == "suppress-caster-procs"
    assert verdict(ev, d, hit, "target").reason == "suppress-target-procs"
    _, d2, ev2 = make(tmp_path / "b", E.PROC_FLAG_TAKE_HARMFUL_SPELL,
                      attributes={7: E.ATTR7_CAN_PROC_FROM_SUPPRESSED_TARGET_PROCS[1]})
    assert verdict(ev2, d2, hit, "target").verdict == "eligible"


def test_charges_cooldown_and_chain_limit(tmp_path):
    cat, d, ev = make(tmp_path, E.PROC_FLAG_DEAL_HARMFUL_SPELL)
    hit = spell_hit(cat.require(HARMFUL_SPELL), damage=5)
    assert verdict(ev, d, hit, state=HolderState(using_charges=True, charges=0)).reason == "has-charges"
    assert verdict(ev, d, hit, state=HolderState(proc_cooldown_until_ms=1000, now_ms=999)).reason == "proc-cooldown"
    assert verdict(ev, d, hit, state=HolderState(proc_cooldown_until_ms=1000, now_ms=1000)).verdict == "eligible"
    assert verdict(ev, d, hit.with_(proc_chain_length=10)).reason == "proc-chain-hard-limit"
    assert verdict(ev, d, periodic_damage(cat.require(HARMFUL_SPELL), damage=1).with_(
        actor_mask=E.PROC_FLAG_DEAL_HARMFUL_SPELL, proc_chain_length=50)).verdict == "eligible"


def test_class_ability_only(tmp_path):
    cat, d, ev = make(tmp_path, E.PROC_FLAG_DEAL_HARMFUL_SPELL,
                      attributes={12: E.ATTR12_ONLY_PROC_FROM_CLASS_ABILITIES[1]})
    assert verdict(ev, d, spell_hit(cat.require(HARMFUL_SPELL), damage=5)).reason == "only-class-abilities"


def test_scripts_and_conditions_are_unknown_until_supplied(tmp_path):
    ov = FakeOverlay(scripts={PROVIDER: {"script_names": ["x"], "proc_hooks": ["DoCheckProc"]}},
                     conditions={PROVIDER: [{"ConditionTypeOrReference": 1}]})
    cat, d, ev = make(tmp_path, E.PROC_FLAG_DEAL_HARMFUL_SPELL, overlay=ov)
    hit = spell_hit(cat.require(HARMFUL_SPELL), damage=5)
    assert verdict(ev, d, hit).verdict == "unknown"
    facts = ActorFacts(actor_level=80, conditions_met=True, script_check_proc=False)
    assert verdict(ev, d, hit, facts=facts).reason == "script-check-proc"


def test_disabled_and_gated_effects(tmp_path):
    cat, d, ev = make(tmp_path, E.PROC_FLAG_DEAL_HARMFUL_SPELL,
                      effects=[Eff(index=0, aura=E.SPELL_AURA_ADD_PCT_MODIFIER),
                               Eff(index=1, aura=E.aura("MOD_STUN"))])
    hit = spell_hit(cat.require(HARMFUL_SPELL), damage=5)
    res = verdict(ev, d, hit)
    assert res.verdict == "eligible" and res.effect_mask == 0b10
    no_damage = hit.with_(damage=0)
    assert verdict(ev, d, no_damage).reason == "any-effect-remains"


def test_passive_weapon_requirement(tmp_path):
    cat, d, ev = make(tmp_path, E.PROC_FLAG_DEAL_MELEE_SWING,
                      attributes={0: E.ATTR0_PASSIVE[1]}, equipped_class=2)
    swing = melee_swing()
    assert verdict(ev, d, swing).verdict == "unknown"
    ok = ActorFacts(actor_level=80, equipped_item_fits=True)
    assert verdict(ev, d, swing, facts=ok).verdict == "eligible"
    assert verdict(ev, d, swing, facts=ActorFacts(equipped_item_fits=True, holder_in_feral_form=True)).reason == "not-in-feral-form"


def test_exhaustive_side_and_flag_matrix(tmp_path):
    """Every named flag x every builder event: eligibility equals the flag test."""
    cat, _, _ = make(tmp_path, 0)
    events = [melee_swing(), melee_swing("off"), spell_hit(cat.require(HARMFUL_SPELL), damage=3),
              spell_hit(cat.require(HELPFUL_SPELL), healing=3), spell_hit(cat.require(MELEE_ABILITY), damage=3),
              periodic_damage(cat.require(HARMFUL_SPELL), damage=2), periodic_heal(cat.require(HELPFUL_SPELL)),
              reflect_event()] + [simple_event(k) for k in ("heartbeat", "kill", "death", "jump", "dispel", "knockback")]
    for flag in ALL_NAMED_FLAGS:
        _, d, ev = make(tmp_path / f"f{flag:x}", flag)
        for event, holder in itertools.product(events, ("actor", "target")):
            res = verdict(ev, d, event, holder)
            mask = event.actor_mask if holder == "actor" else event.target_mask
            if not mask & flag or (holder == "target" and not event.has_action_target):
                assert res.verdict == "ineligible", (event.label, holder, flag)
