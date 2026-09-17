"""Part D -- proc events produced by weapon attacks (white MH/OH, extra attacks, auto-shot)."""

from __future__ import annotations

import pytest

from procs import enums as E
from procs.events import BASE_ATTACK, OFF_ATTACK
from weapon_combat.proc_events import (
    FACTS, WHITE_OUTCOMES, build_corpus, cross_check, override_event, ranged_auto_event, white_swing_event,
)

MH_ACTOR = E.PROC_FLAG_DEAL_MELEE_SWING | E.PROC_FLAG_MAIN_HAND_WEAPON_SWING
OH_ACTOR = E.PROC_FLAG_DEAL_MELEE_SWING | E.PROC_FLAG_OFF_HAND_WEAPON_SWING


def test_hand_flags_differ_only_in_the_hand_bit():
    mh = white_swing_event(BASE_ATTACK, "normal", damage=100)
    oh = white_swing_event(OFF_ATTACK, "normal", damage=100)
    assert mh["actor_mask"] == MH_ACTOR and oh["actor_mask"] == OH_ACTOR
    assert mh["actor_mask"] ^ oh["actor_mask"] == E.PROC_FLAG_MAIN_HAND_WEAPON_SWING | E.PROC_FLAG_OFF_HAND_WEAPON_SWING
    for key in ("target_mask", "spell_type_mask", "spell_phase_mask", "hit_mask"):
        assert mh[key] == oh[key]
    assert mh["spell_type_mask"] == E.PROC_SPELL_TYPE_NONE and mh["spell_phase_mask"] == E.PROC_SPELL_PHASE_NONE


@pytest.mark.parametrize("outcome,mask", [
    ("normal", E.PROC_HIT_NORMAL), ("crit", E.PROC_HIT_CRITICAL), ("glancing", E.PROC_HIT_NORMAL),
    ("crushing", E.PROC_HIT_NORMAL), ("miss", E.PROC_HIT_MISS), ("dodge", E.PROC_HIT_DODGE),
    ("parry", E.PROC_HIT_PARRY), ("evade", E.PROC_HIT_EVADE),
])
def test_white_outcome_hit_masks(outcome, mask):
    dmg = 100 if outcome in ("normal", "crit", "glancing", "crushing") else 0
    ev = white_swing_event(BASE_ATTACK, outcome, damage=dmg)
    assert ev["hit_mask"] == mask
    assert bool(ev["target_mask"] & E.PROC_FLAG_TAKE_ANY_DAMAGE) == (dmg > 0)


def test_partial_block_is_block_plus_normal():
    ev = white_swing_event(OFF_ATTACK, "block", damage=97, blocked=3)
    assert ev["hit_mask"] == E.PROC_HIT_BLOCK | E.PROC_HIT_NORMAL
    assert ev["target_mask"] & E.PROC_FLAG_TAKE_ANY_DAMAGE


def test_immune_swing_is_immune_plus_evade_without_damage_flag():
    ev = white_swing_event(BASE_ATTACK, "immune", damage=100)
    assert ev["hit_mask"] == E.PROC_HIT_IMMUNE | E.PROC_HIT_EVADE
    assert ev["target_mask"] == E.PROC_FLAG_TAKE_MELEE_SWING


def test_full_absorb_nullifies_normal_and_crit_but_keeps_take_any_damage():
    for outcome in ("normal", "crit"):
        ev = white_swing_event(BASE_ATTACK, outcome, damage=100, absorb=100)
        assert ev["hit_mask"] == E.PROC_HIT_ABSORB
        assert ev["target_mask"] & E.PROC_FLAG_TAKE_ANY_DAMAGE
        assert ev["damage_info"]["damage_post_absorb"] == 0
    partial = white_swing_event(BASE_ATTACK, "crit", damage=200, absorb=50)
    assert partial["hit_mask"] == E.PROC_HIT_ABSORB | E.PROC_HIT_CRITICAL


def test_extra_attack_event_is_indistinguishable():
    a = white_swing_event(BASE_ATTACK, "normal", damage=100)
    b = white_swing_event(BASE_ATTACK, "normal", damage=100, extra=True)
    for key in ("actor_mask", "target_mask", "spell_type_mask", "spell_phase_mask", "hit_mask"):
        assert a[key] == b[key]


def test_ranged_auto_shot_masks():
    hit = ranged_auto_event("normal", damage=100)
    assert hit["actor_mask"] == E.PROC_FLAG_DEAL_RANGED_ATTACK
    assert hit["target_mask"] == E.PROC_FLAG_TAKE_RANGED_ATTACK | E.PROC_FLAG_TAKE_ANY_DAMAGE
    assert hit["spell_phase_mask"] == E.PROC_SPELL_PHASE_HIT and hit["spell_type_mask"] == E.PROC_SPELL_TYPE_DAMAGE
    assert ranged_auto_event("crit", damage=100)["hit_mask"] == E.PROC_HIT_CRITICAL
    miss = ranged_auto_event("miss", damage=0)
    assert miss["hit_mask"] == E.PROC_HIT_MISS and miss["spell_type_mask"] == E.PROC_SPELL_TYPE_NO_DMG_HEAL
    assert ranged_auto_event("deflect", damage=0)["hit_mask"] == E.PROC_HIT_DEFLECT
    assert len(hit["additional_events"]) == 3


def test_blocked_weapon_spell_reports_full_block_while_dealing_damage():
    ev = ranged_auto_event("block", damage=100)
    assert ev["hit_mask"] == E.PROC_HIT_BLOCK | E.PROC_HIT_FULL_BLOCK
    assert ev["target_mask"] & E.PROC_FLAG_TAKE_ANY_DAMAGE
    assert ev["spell_type_mask"] == E.PROC_SPELL_TYPE_DAMAGE


def test_damage_immune_ranged_is_damage_type_without_take_any_damage():
    ev = ranged_auto_event("immune", damage=100, immune=True)
    assert ev["hit_mask"] == E.PROC_HIT_IMMUNE
    assert ev["spell_type_mask"] == E.PROC_SPELL_TYPE_DAMAGE
    assert not ev["target_mask"] & E.PROC_FLAG_TAKE_ANY_DAMAGE


def test_override_autoattack_replaces_white_event_with_ability_flags():
    ev = override_event(408385, 2, OFF_ATTACK)
    assert ev["actor_mask"] == E.PROC_FLAG_DEAL_MELEE_ABILITY | E.PROC_FLAG_OFF_HAND_WEAPON_SWING
    assert not ev["actor_mask"] & E.PROC_FLAG_DEAL_MELEE_SWING
    assert ev["white_event"].startswith("none")


def test_procs_events_oracle_has_no_contradictions():
    assert cross_check() == []


def test_facts_and_corpus():
    assert len({f["id"] for f in FACTS}) == len(FACTS) >= 16
    corpus = build_corpus("g")
    assert corpus == build_corpus("g")
    assert corpus["cross_check_contradictions"] == []
    assert len(corpus["events"]) == 2 * len(WHITE_OUTCOMES) + 3 + 8 + 1 + 1


def test_unknown_inputs_fail_closed():
    with pytest.raises(ValueError):
        white_swing_event("ranged", "normal")
    with pytest.raises(ValueError):
        white_swing_event(BASE_ATTACK, "wobble")
    with pytest.raises(ValueError):
        ranged_auto_event("glancing")
