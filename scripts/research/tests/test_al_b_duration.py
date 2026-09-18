"""Track B: authored duration + Trinity duration pipeline (aura_lifecycle.duration)."""

from __future__ import annotations

import json

import pytest

from aura_lifecycle import CORPORA, FailClosed, records
from aura_lifecycle.duration import (
    ATTR0_PASSIVE,
    ATTR1_IS_CHANNELLED,
    ATTR5_SPELL_HASTE_AFFECTS_PERIODIC,
    ATTR7_NO_TARGET_DURATION_MOD,
    Caster,
    DurationEntry,
    SpellFacts,
    SpellMods,
    TargetDurationMods,
    apply_diminishing,
    calc_max_duration,
    calc_spell_duration,
    calculate_pct,
    channel_duration,
    empower_spells,
    facts,
    family,
    haste_affects_duration,
    mod_spell_duration,
    spellinfo_get_duration,
)


def spell(dur, mx, per=0, *attrs, entry=True, empower=False):
    a = [0] * 17
    for w, m in attrs:
        a[w] |= m
    return SpellFacts(0, tuple(a), DurationEntry(0, dur, mx, per) if entry else None, empower=empower)


# ----------------------------------------------------------------------------------------------- synthetic
def test_only_exact_minus_one_is_permanent():
    """Rules out 'every negative duration is permanent' (record 427 = -600000/600000)."""
    assert spellinfo_get_duration(spell(-1, -1)) == -1
    assert spellinfo_get_duration(spell(-600000, 600000)) == 600000
    assert family(spell(-600000, 600000)) == "negative-non-sentinel"


def test_missing_record_passive_vs_active():
    assert spellinfo_get_duration(spell(0, 0, 0, ATTR0_PASSIVE, entry=False)) == -1
    assert spellinfo_get_duration(spell(0, 0, entry=False)) == 0
    assert calc_max_duration(spell(0, 0, 0, ATTR0_PASSIVE, entry=False), Caster()) == -1


def test_combo_points_only_scale_when_minimum_positive():
    """Rules out 'Duration != MaxDuration + per-resource => scales with resource' for minimum 0 (Envenom shape)."""
    rupture = spell(4000, 24000, 4000)
    assert [calc_spell_duration(rupture, cp) for cp in (None, 0, 1, 5, 7)] == [4000, 4000, 8000, 24000, 24000]
    envenom = spell(0, 5000, 1000)
    assert [calc_spell_duration(envenom, cp) for cp in (None, 1, 5)] == [0, 0, 0]


def test_spell_mod_formula_and_empower_hold():
    s = spell(10000, 10000)
    assert calc_max_duration(s, Caster(duration_mods=SpellMods(2000, 1.1))) == int((10000 + 2000) * 1.100000023841858)
    assert calc_max_duration(s, Caster(mod_owner=False, duration_mods=SpellMods(2000, 2.0))) == 10000
    assert calc_max_duration(spell(3000, 3000, empower=True), None) == 4000
    assert calc_max_duration(spell(-1, -1), Caster(duration_mods=SpellMods(5000, 1.0))) == -1


def test_target_duration_mods_negative_only_and_attr7():
    s = spell(10000, 10000)
    mods = TargetDurationMods(mechanic_total=-30, mechanic_not_stack=-50, dispel_total=-20)
    assert mod_spell_duration(s, mods, 10000, positive=False) == 4000   # -50% then -20%
    assert mod_spell_duration(s, mods, 10000, positive=True) == 10000
    assert mod_spell_duration(spell(10000, 10000, 0, ATTR7_NO_TARGET_DURATION_MOD), mods, 10000, positive=False) == 10000


def test_diminishing_default_group():
    assert apply_diminishing(8000, 2) == (4000, True)
    assert apply_diminishing(8000, "immune") == (0, False)
    assert apply_diminishing(8000, 1, limit_duration=6000, limit_applies=True) == (6000, True)
    with pytest.raises(FailClosed):
        apply_diminishing(8000, 7)


def test_attr8_floors_to_whole_hastened_periods():
    """Rules out 'haste scales the duration' when a period exists: 18000 with period 2400 -> 16800 (< 18000)."""
    assert haste_affects_duration(18000, (2400,), 0.8) == 16800
    assert haste_affects_duration(1000, (2400,), 0.8) == 2400            # at least one period
    assert haste_affects_duration(18000, (0,), 0.8) == 14400             # no period -> * casting speed
    assert haste_affects_duration(18000, (2400, 3000), 0.8) == 18000     # max over effects


def test_channel_duration_haste_only_with_periodic_haste_attr():
    plain = spell(6000, 6000, 0, ATTR1_IS_CHANNELLED)
    hasted = spell(6000, 6000, 0, ATTR1_IS_CHANNELLED, ATTR5_SPELL_HASTE_AFFECTS_PERIODIC)
    assert channel_duration(plain, Caster(mod_casting_speed=0.8)) == 6000
    assert channel_duration(hasted, Caster(mod_casting_speed=0.8)) == 4800
    with pytest.raises(FailClosed):
        channel_duration(spell(0, 3500, 500, ATTR1_IS_CHANNELLED), Caster())  # Killing Spree shape never channels


def test_calculate_pct_binary32():
    assert calculate_pct(18000, 130) == 23400
    assert calculate_pct(516249, 130) == (13 * 516249) // 10
    assert calculate_pct(516250, 130) == (13 * 516250) // 10 - 1


# ----------------------------------------------------------------------------------------------- real data
def test_real_witnesses(al_ctx):
    emp = empower_spells(al_ctx.bundle.source)
    rup = facts(al_ctx.data, 1943, emp)
    assert family(rup) == "ranged-per-resource"
    assert (rup.duration_entry.duration, rup.duration_entry.max_duration, rup.duration_entry.per_resource) == (4000, 24000, 4000)
    env = facts(al_ctx.data, 32645, emp)
    assert env.duration_entry.duration == 0 and env.duration_entry.per_resource > 0
    assert calc_max_duration(env, Caster(), 5) == 0
    assert family(facts(al_ctx.data, 101822, emp)) == "negative-non-sentinel"


def test_corpus_counts_regenerate(al_ctx):
    """duration.json family census equals a fresh census (byte-stable corpus)."""
    from aura_lifecycle import providers
    from aura_lifecycle.duration import census
    path = CORPORA / "duration.json"
    if not path.exists():
        pytest.skip("duration.json not generated")
    corpus = json.loads(path.read_text(encoding="utf-8"))
    records.validate_corpus(corpus)
    fresh = census(al_ctx, providers.populations(al_ctx))
    assert corpus["census"]["families"] == fresh["families"]
    for pop, n in corpus["populations"].items():
        assert sum(corpus["census"]["families"][pop].values()) == n
    assert corpus["census"]["per_resource_minimum_le_zero"]["player"] == [32645, 51690]
