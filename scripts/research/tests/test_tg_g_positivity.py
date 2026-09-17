"""Track G: load-time positivity port (targeting/positivity.py).

Synthetic spells rule out simplified positivity models; real spells pin a few verified
values; the load-order fail-closed rule is checked against the compiled Trinity code.
"""

from __future__ import annotations

import pytest

from procs.enums import attr, aura, effect
from targeting import FailClosed
from targeting import differential as D
from targeting import positivity as P


def _loader(spells):
    return D._ValueLoader({s.id: s for s in spells})


def _eff(i, eff, au=0, check_a="DEFAULT", trigger=0, value=0.0, misc=0, target_a=1):
    return P.PEffect(index=i, effect=effect(eff), aura=aura(au) if au else 0, check_a=check_a, target_a=target_a,
                     trigger=trigger, base_points=value, misc0=misc)


def _spell(sid, *effs, attrs=(), seed=()):
    words = [0] * 17
    for n in attrs:
        w, b = attr(n)
        words[w] |= b
    return P.PSpell(id=sid, attributes=tuple(words), family=3, family_flags0=0, mechanic=0, effects=tuple(effs),
                    seed=frozenset(seed))


def test_damage_is_negative_heal_anywhere_is_positive() -> None:
    """Rules out 'per-effect positivity only': a HEAL effect anywhere makes every effect positive (4712-4717)."""
    dmg = _spell(1, _eff(0, "SCHOOL_DAMAGE", check_a="ENEMY"))
    both = _spell(2, _eff(0, "SCHOOL_DAMAGE", check_a="ENEMY"), _eff(1, "HEAL"))
    L = _loader([dmg, both])
    assert L.negative_effects(1) == {0}
    assert L.negative_effects(2) == frozenset()


def test_stat_aura_sign_rule() -> None:
    """MOD_STAT is negative only for a negative value (4851), MOD_DAMAGE_TAKEN only for a positive one (4876)."""
    L = _loader([_spell(1, _eff(0, "APPLY_AURA", "MOD_STAT", value=-5.0)),
                 _spell(2, _eff(0, "APPLY_AURA", "MOD_STAT", value=5.0)),
                 _spell(3, _eff(0, "APPLY_AURA", "MOD_DAMAGE_TAKEN", value=5.0)),
                 _spell(4, _eff(0, "APPLY_AURA", "MOD_DAMAGE_TAKEN", value=-5.0))])
    assert [bool(L.negative_effects(i)) for i in (1, 2, 3, 4)] == [True, False, True, False]


def test_post_pass_marks_dummy_with_same_targets() -> None:
    """Rules out 'effects are independent': a positive DUMMY aura followed by a negative effect on the same
    targets is marked negative afterwards (SpellInfo.cpp:5074-5098)."""
    sp = _spell(1, _eff(0, "APPLY_AURA", "DUMMY"), _eff(1, "APPLY_AURA", "PERIODIC_DAMAGE"))
    assert _loader([sp]).negative_effects(1) == {0, 1}
    other = _spell(2, _eff(0, "APPLY_AURA", "DUMMY"), _eff(1, "APPLY_AURA", "PERIODIC_DAMAGE", target_a=6))
    assert _loader([other]).negative_effects(2) == {1}


def test_triggered_negative_spell_makes_trigger_negative() -> None:
    """A non-aura effect with TriggerSpell inherits the triggered spell's negativity (5040-5059)."""
    child = _spell(20, _eff(0, "SCHOOL_DAMAGE", check_a="ENEMY"))
    parent = _spell(10, _eff(0, "TRIGGER_SPELL", trigger=20))
    assert _loader([parent, child]).negative_effects(10) == {0}


def test_load_order_dependence_fails_closed_and_is_real() -> None:
    """The triggered spell's post-pass bit (5074-5098) exists only once it is processed: a periodic-trigger
    parent inspects only the child's positive-target effects (4884-4902), so Trinity's answer for the parent
    depends on the container order -> FailClosed; the compiled code confirms both answers occur."""
    child = _spell(20, _eff(0, "APPLY_AURA", "DUMMY"),
                   P.PEffect(index=1, effect=effect("APPLY_AURA"), aura=aura("PERIODIC_DAMAGE"), target_a=1,
                             check_a="ENEMY"))
    parent = _spell(10, _eff(0, "APPLY_AURA", "PERIODIC_TRIGGER_SPELL_WITH_VALUE", trigger=20))
    with pytest.raises(FailClosed, match="load-order"):
        _loader([parent, child]).negative_effects(10)
    if not D.available():
        pytest.skip("probe unavailable")
    import json
    import subprocess
    exe = D.build("tc_target_positivity_probe")
    spells = {10: parent, 20: child}
    text = D._pos_lines(spells, [10, 20]) + ["O 10 20", "G", "O 20 10", "G"]
    out = subprocess.run([str(exe)], input="\n".join(text) + "\n", capture_output=True, text=True, check=True)
    runs = [json.loads(x)["neg"]["10"] for x in out.stdout.splitlines()]
    assert runs == [0, 1]


def test_correction_seed_is_respected(tg_ctx) -> None:
    """Earthquake 61882: LoadSpellInfoCorrections sets NegativeEffects[2] (SpellMgr.cpp:5129)."""
    assert 2 in P.negative_effects(61882)


@pytest.mark.parametrize("spell,positive", [(1126, True), (1064, True), (97462, True), (1160, False), (6343, False),
                                            (53385, False)])
def test_real_spells(tg_ctx, spell: int, positive: bool) -> None:
    assert P.is_positive(spell) is positive


def test_real_order_dependent_spell_fails_closed(tg_ctx) -> None:
    with pytest.raises(FailClosed, match="load-order"):
        P.negative_effects(190411)


def test_player_scope_mostly_decided(tg_ctx) -> None:
    decided = failed = 0
    for s in tg_ctx.scope.reach:
        try:
            P.negative_effects(s)
            decided += 1
        except FailClosed:
            failed += 1
    assert decided >= 4800 and failed <= 10


def test_probe_agrees_on_generated_worlds() -> None:
    if not D.available():
        pytest.skip("probe unavailable")
    out = D.positivity_probe(None, 400)
    assert out["checks"]["positivity_generated"]["failed"] == 0
    assert out["checks"]["positivity_generated"]["cases"] > 800
    lo = out["load_order"]
    assert lo["fail_closed_worlds"] == lo["fail_closed_confirmed_order_dependent"] + lo["fail_closed_order_independent_in_sample"]
