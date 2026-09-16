"""SpellMgr::LoadSpellProcs: default generation and spell_proc merging."""

from __future__ import annotations

import pytest
from proc_synthetic import Eff, FakeOverlay, Spell, world

from procs import enums as E
from procs.definition import (
    CHANCE_CLASSIC_PPM,
    CHANCE_FIXED,
    CHANCE_GUARANTEED,
    CHANCE_RPPM,
    CHANCE_ZERO,
    generate_default_entry,
)
from procs.trinity import SpellProcRow

A = E.aura
DEAL_SPELL = E.PROC_FLAG_DEAL_HARMFUL_SPELL
TAKE_MELEE = E.PROC_FLAG_TAKE_MELEE_SWING


def gen(tmp_path, spell, **kw):
    cat, defs, _ = world(tmp_path, [spell], **kw)
    return generate_default_entry(cat.require(spell.id)), defs


def row(spell_id, **fields):
    base = dict(SpellId=spell_id, SchoolMask=0, SpellFamilyName=0, SpellFamilyMask0=0,
                SpellFamilyMask1=0, SpellFamilyMask2=0, SpellFamilyMask3=0, ProcFlags=0,
                ProcFlags2=0, SpellTypeMask=0, SpellPhaseMask=0, HitMask=0, AttributesMask=0,
                DisableEffectsMask=0, ProcsPerMinute=0.0, Chance=0.0, Cooldown=0, Charges=0)
    base.update(fields)
    return SpellProcRow.from_row(list(base), list(base.values()))


def test_no_proc_flags_generates_nothing(tmp_path):
    res, _ = gen(tmp_path, Spell(1, effects=[Eff(aura=42, trigger=2)]))
    assert res.entry is None and res.reason == "no-proc-flags"


def test_non_trigger_aura_only_needs_spell_proc(tmp_path):
    spell = Spell(1, proc_flags=DEAL_SPELL, effects=[Eff(aura=E.SPELL_AURA_ADD_PCT_MODIFIER)])
    res, _ = gen(tmp_path, spell)
    assert res.entry is None and res.reason == "no-trigger-aura"
    assert res.log and "spell_proc" in res.log[0]


def test_non_trigger_auras_are_disabled_effects(tmp_path):
    spell = Spell(1, proc_flags=DEAL_SPELL, proc_chance=100, effects=[
        Eff(index=0, aura=E.SPELL_AURA_ADD_PCT_MODIFIER),
        Eff(index=1, aura=E.SPELL_AURA_PROC_TRIGGER_SPELL, trigger=5),
        Eff(index=2, aura=A("MOD_DAMAGE_PERCENT_DONE"))])
    res, _ = gen(tmp_path, spell)
    e = res.entry
    assert e.disable_effects_mask == 0b001
    assert e.spell_type_mask == E.PROC_SPELL_TYPE_MASK_ALL
    assert e.spell_phase_mask == E.PROC_SPELL_PHASE_HIT
    assert e.hit_mask == 0 and e.school_mask == 0
    assert e.chance == 100.0 and e.procs_per_minute == 0.0


def test_spell_type_mask_is_union_over_trigger_auras(tmp_path):
    spell = Spell(1, proc_flags=DEAL_SPELL, effects=[
        Eff(index=0, aura=A("MOD_STUN")), Eff(index=1, aura=A("MOD_FEAR"))])
    res, _ = gen(tmp_path, spell)
    assert res.entry.spell_type_mask == E.PROC_SPELL_TYPE_DAMAGE
    assert res.entry.attributes_mask & E.PROC_ATTR_TRIGGERED_CAN_PROC  # always-triggered aura


def test_taken_hit_proc_trigger_spell_gets_triggered_can_proc(tmp_path):
    taken, _ = gen(tmp_path / "a", Spell(1, proc_flags=TAKE_MELEE, effects=[Eff(aura=42, trigger=9)]))
    done, _ = gen(tmp_path / "b", Spell(1, proc_flags=DEAL_SPELL, effects=[Eff(aura=42, trigger=9)]))
    assert taken.entry.attributes_mask & E.PROC_ATTR_TRIGGERED_CAN_PROC
    assert not done.entry.attributes_mask & E.PROC_ATTR_TRIGGERED_CAN_PROC


def test_taken_hit_flag_only_from_trigger_spell_or_damage_auras(tmp_path):
    res, _ = gen(tmp_path, Spell(1, proc_flags=TAKE_MELEE, effects=[Eff(aura=E.SPELL_AURA_DUMMY)]))
    assert not res.entry.attributes_mask & E.PROC_ATTR_TRIGGERED_CAN_PROC


def test_kill_flag_requires_exp_or_honor(tmp_path):
    res, _ = gen(tmp_path, Spell(1, proc_flags=E.PROC_FLAG_KILL, effects=[Eff(aura=42, trigger=2)]))
    assert res.entry.attributes_mask & E.PROC_ATTR_REQ_EXP_OR_HONOR


@pytest.mark.parametrize("aura,expected", [
    ("REFLECT_SPELLS", E.PROC_HIT_REFLECT), ("REFLECT_SPELLS_SCHOOL", E.PROC_HIT_REFLECT),
    ("MOD_WEAPON_CRIT_PERCENT", E.PROC_HIT_CRITICAL), ("MOD_BLOCK_PERCENT", E.PROC_HIT_BLOCK)])
def test_hit_mask_switch(tmp_path, aura, expected):
    res, _ = gen(tmp_path, Spell(1, proc_flags=DEAL_SPELL, effects=[Eff(aura=A(aura))]))
    assert res.entry.hit_mask == expected


def test_hit_chance_only_minus_100_selects_miss(tmp_path):
    miss, _ = gen(tmp_path / "a", Spell(1, proc_flags=DEAL_SPELL, effects=[Eff(aura=A("MOD_HIT_CHANCE"), base_points=-100)]))
    not_miss, _ = gen(tmp_path / "b", Spell(1, proc_flags=DEAL_SPELL, effects=[Eff(aura=A("MOD_HIT_CHANCE"), base_points=-99)]))
    assert miss.entry.hit_mask == E.PROC_HIT_MISS
    assert not_miss.entry.hit_mask == 0


def test_hit_mask_switch_stops_at_first_listed_aura(tmp_path):
    # The trailing `break` leaves the effect loop: a PROC_TRIGGER_SPELL first
    # means a later REFLECT effect never sets the hit mask.
    res, _ = gen(tmp_path, Spell(1, proc_flags=DEAL_SPELL, effects=[
        Eff(index=0, aura=42, trigger=5), Eff(index=1, aura=A("REFLECT_SPELLS"))]))
    assert res.entry.hit_mask == 0


def test_cast_successful_without_phase_flags_defaults_to_cast_phase(tmp_path):
    res, _ = gen(tmp_path, Spell(1, proc_flags=E.PROC_FLAG_2_CAST_SUCCESSFUL, effects=[Eff(aura=42, trigger=5)]))
    assert res.entry.spell_phase_mask == E.PROC_SPELL_PHASE_CAST
    both, _ = gen(tmp_path / "b", Spell(1, proc_flags=E.PROC_FLAG_2_CAST_SUCCESSFUL | DEAL_SPELL,
                                         effects=[Eff(aura=42, trigger=5)]))
    assert both.entry.spell_phase_mask == E.PROC_SPELL_PHASE_HIT


def test_family_mask_from_trigger_auras_only(tmp_path):
    res, _ = gen(tmp_path, Spell(1, proc_flags=DEAL_SPELL, family=8, effects=[
        Eff(index=0, aura=42, trigger=5, class_mask=(0x10, 0, 0, 0)),
        Eff(index=1, aura=E.SPELL_AURA_ADD_PCT_MODIFIER, class_mask=(0x20, 0, 0, 0))]))
    assert res.entry.family_mask == 0x10 and res.entry.family_name == 8
    unmasked, _ = gen(tmp_path / "b", Spell(1, proc_flags=DEAL_SPELL, family=8, effects=[Eff(aura=42, trigger=5)]))
    assert unmasked.entry.family_name == 0


def _loop(**kw):
    base = dict(proc_flags=DEAL_SPELL, proc_chance=100, attributes={3: E.ATTR3_CAN_PROC_FROM_PROCS[1]},
                effects=[Eff(aura=42, trigger=5)])
    base.update(kw)
    return Spell(1, **base)


def test_infinite_loop_guard(tmp_path):
    res, _ = gen(tmp_path, _loop())
    assert res.entry is None and res.reason == "infinite-loop-guard"


@pytest.mark.parametrize("change", [
    dict(proc_chance=99), dict(proc_cooldown=1), dict(proc_charges=1),
    dict(proc_flags=TAKE_MELEE), dict(effects=[Eff(aura=42, trigger=0)]),
    dict(attributes={}), dict(effects=[Eff(aura=42, trigger=5, class_mask=(1, 0, 0, 0))]),
])
def test_infinite_loop_guard_needs_every_condition(tmp_path, change):
    res, _ = gen(tmp_path, _loop(**change))
    assert res.entry is not None


def test_infinite_loop_guard_ignores_rppm(tmp_path):
    res, _ = gen(tmp_path, _loop(ppm_id=7), ppm=[(7, 2.0, 0)])
    assert res.entry is not None


def test_spell_magnet_clears_proc_flags(tmp_path):
    cat, defs, _ = world(tmp_path, [Spell(1, proc_flags=DEAL_SPELL, effects=[Eff(aura=E.SPELL_AURA_SPELL_MAGNET)])])
    assert cat.require(1).proc_flags == 0
    assert defs.get(1).entry is None


def test_spell_proc_row_takes_db2_defaults(tmp_path):
    spell = Spell(1, proc_flags=DEAL_SPELL, proc_chance=30, proc_charges=2, proc_cooldown=500,
                  effects=[Eff(aura=E.SPELL_AURA_ADD_PCT_MODIFIER)])
    ov = FakeOverlay(spell_proc={1: row(1, SpellPhaseMask=2)})
    _, defs, _ = world(tmp_path, [spell], overlay=ov)
    e = defs.get(1).entry
    assert e.origin == "spell_proc"
    assert (e.proc_flags, e.chance, e.charges, e.cooldown_ms) == (DEAL_SPELL, 30.0, 2, 500)
    assert e.disable_effects_mask == 0  # the row, not generation, decides


def test_spell_proc_ppm_suppresses_default_chance(tmp_path):
    spell = Spell(1, proc_flags=E.PROC_FLAG_DEAL_MELEE_SWING, proc_chance=30, effects=[Eff(aura=42, trigger=2)])
    _, defs, _ = world(tmp_path, [spell], overlay=FakeOverlay(spell_proc={1: row(1, ProcsPerMinute=3.0)}))
    d = defs.get(1)
    assert d.entry.chance == 0.0 and d.entry.procs_per_minute == 3.0
    assert d.chance["model"] == CHANCE_CLASSIC_PPM


def test_spell_proc_validation_corrections(tmp_path):
    spell = Spell(1, effects=[Eff(aura=42, trigger=2)])
    r = row(1, ProcFlags=DEAL_SPELL, Chance=-5.0, AttributesMask=0x60 | 0x2, ProcFlags2=4)
    _, defs, _ = world(tmp_path, [spell], overlay=FakeOverlay(spell_proc={1: r}))
    d = defs.get(1)
    assert d.entry.chance == 0.0
    assert d.entry.attributes_mask == E.PROC_ATTR_TRIGGERED_CAN_PROC
    assert d.entry.spell_phase_mask == 0  # REQ phase flags present -> no CAST default
    assert any("SpellPhaseMask required" in m for m in d.load_log)


def test_spell_proc_cast_successful_phase_default(tmp_path):
    spell = Spell(1, effects=[Eff(aura=42, trigger=2)])
    r = row(1, ProcFlags2=4)
    _, defs, _ = world(tmp_path, [spell], overlay=FakeOverlay(spell_proc={1: r}))
    assert defs.get(1).entry.spell_phase_mask == E.PROC_SPELL_PHASE_CAST


def test_spell_proc_row_blocks_generation_for_all_fallback_difficulties(tmp_path):
    spells = [Spell(1, proc_flags=DEAL_SPELL, effects=[Eff(aura=42, trigger=2),
                                                       Eff(aura=42, trigger=3, difficulty=2)])]
    _, defs, _ = world(tmp_path, spells, overlay=FakeOverlay(spell_proc={1: row(1, Chance=7.0)}))
    assert defs.get(1, 2).entry.origin == "spell_proc"
    assert defs.store.generation(1, 2).reason == "spell_proc-row-present"


def test_difficulty_generation_and_fallback(tmp_path):
    base = Spell(1, proc_flags=DEAL_SPELL, proc_chance=10,
                 effects=[Eff(aura=42, trigger=2), Eff(index=0, aura=42, trigger=3, difficulty=2)])
    cat, defs, _ = world(tmp_path, [base])
    assert cat.fallback_chain(2) == [2, 1, 0]
    d2 = defs.get(1, 2)
    assert d2.info.difficulty == 2 and d2.status == "generated"
    assert d2.triggered_spells == [3]
    d1 = defs.get(1, 1)            # no own key -> falls back to difficulty 0 SpellInfo
    assert d1.info.difficulty == 0 and d1.triggered_spells == [2]
    assert defs.get(1, 7) is None  # not a Difficulty row and no key: no fallback chain


@pytest.mark.parametrize("chance,ppm,base,model", [
    (100, 0, 0.0, CHANCE_GUARANTEED), (101, 0, 0.0, CHANCE_GUARANTEED), (0, 0, 0.0, CHANCE_ZERO),
    (40, 0, 0.0, CHANCE_FIXED), (40, 7, 2.0, CHANCE_RPPM)])
def test_chance_model_selection(tmp_path, chance, ppm, base, model):
    spell = Spell(1, proc_flags=DEAL_SPELL, proc_chance=chance, ppm_id=ppm, effects=[Eff(aura=42, trigger=2)])
    _, defs, _ = world(tmp_path, [spell], ppm=[(7, base, 1)] if ppm else [])
    assert defs.get(1).chance["model"] == model


def test_unknown_proc_flag_bits_are_reported(tmp_path):
    _, defs, _ = world(tmp_path, [Spell(1, proc_flags=DEAL_SPELL | (0x20 << 32), effects=[Eff(aura=42, trigger=2)])])
    assert defs.get(1).unknown_proc_flag_bits == 0x20 << 32


def test_negative_charges_become_255(tmp_path):
    cat, defs, _ = world(tmp_path, [Spell(1, proc_flags=DEAL_SPELL, proc_charges=-1, effects=[Eff(aura=42, trigger=2)])])
    from procs.state import Provider
    assert Provider(defs.get(1), "actor").max_charges() == 255


def test_rank_chain_and_all_ranks_rows(tmp_path):
    spells = [Spell(i, effects=[Eff(aura=42, trigger=9)]) for i in (1, 2, 3)]
    ov = FakeOverlay(spell_proc={1: row(-1, ProcFlags=DEAL_SPELL, SpellPhaseMask=2)})
    cat, defs, _ = world(tmp_path, spells, overlay=ov, ranks=[(2, 1), (3, 2)])
    assert cat.first_rank(3) == 1 and cat.next_rank(1) == 2
    assert all(defs.get(i).entry.origin == "spell_proc" for i in (1, 2, 3))
