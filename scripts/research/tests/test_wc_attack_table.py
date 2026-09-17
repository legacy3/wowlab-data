"""Part D -- attack outcome table (Unit::RollMeleeOutcomeAgainst / Unit::MeleeSpellHitResult).

Consumer facts are pinned against TrinityCore 7f3d43b; ``test_probe_*`` compare the Python
oracle with TrinityCore's own functions compiled in ``tools/tc_swing_probe`` (skipped when the
probe is not built: ``make -C scripts/research/tools/tc_swing_probe``).
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from weapon_combat.attack_table import (
    Attacker, SpellFacts, Victim, block_percent, creature_block_percent, crit_damage, dodge_chance,
    f32, glancing_damage, miss_chance, parry_chance, spell_block_value, spell_table, to_basis_points,
    white_blocked_amount, white_table,
)
from weapon_combat.witnesses_d import table_witness_inputs

from wc_d_helpers import probe_fixture, rle

probe = pytest.fixture(scope="module")(probe_fixture)

BOSS = Victim(is_player=False, level_for_target=93)
DW = Attacker(level_for_target=90, have_offhand_weapon=True, crit_done_pct=20.0)
TWO_HAND = Attacker(level_for_target=90, crit_done_pct=20.0)


def _probs(table):
    return table.probabilities()


# --------------------------------------------------------------------------- consumer facts

def test_band_order_is_miss_dodge_parry_glancing_block_crit_normal():
    t = white_table(Attacker(level_for_target=80, have_offhand_weapon=True, crit_done_pct=10.0), BOSS, "base")
    assert [b.outcome for b in t.bands] == ["miss", "dodge", "parry", "glancing", "block", "crit", "crushing", "normal"]
    lo = [b.lo for b in t.bands if b.eligible and b.lo is not None and b.outcome != "crushing"]
    assert lo == sorted(lo)


def test_dual_wield_penalty_is_19_on_both_hands_net_16_5():
    assert miss_chance(DW, BOSS, "base") == pytest.approx(16.5)
    assert miss_chance(DW, BOSS, "off") == pytest.approx(16.5)
    assert miss_chance(TWO_HAND, BOSS, "base") == 0.0          # 5 - 7.5 clamped (Unit.cpp:12487)


def test_dual_wield_penalty_removed_by_aura_458_queued_spell_feral_and_ranged():
    for a in (replace(DW, ignore_dual_wield_penalty=True), replace(DW, current_melee_spell=True), replace(DW, in_feral_form=True)):
        assert miss_chance(a, BOSS, "base") == 0.0
    assert miss_chance(DW, BOSS, "ranged") == 0.0
    assert miss_chance(DW, BOSS, "base", SpellFacts()) == 0.0     # spells never take the penalty


def test_npc_avoidance_at_plus_three_levels():
    assert dodge_chance(TWO_HAND, BOSS, "base") == 0.0             # 3 + 4.5 - 7.5
    assert parry_chance(TWO_HAND, BOSS, "base") == pytest.approx(3.0)   # 6 + 4.5 - 7.5
    p = _probs(white_table(TWO_HAND, BOSS, "base"))
    assert p == {"block": 0.075, "crit": 0.2, "normal": 0.695, "parry": 0.03}


def test_tank_expertise_aura_240_value_3():
    tank = replace(TWO_HAND, expertise_mainhand=3)
    assert parry_chance(tank, BOSS, "base") == pytest.approx(2.25)  # 10.5 - (7.5 + 3/4)


def test_ranged_attack_type_gets_no_expertise():
    assert parry_chance(TWO_HAND, BOSS, "ranged") == pytest.approx(10.5)


def test_glancing_unreachable_at_plus_three_reachable_at_plus_four():
    assert all(b.outcome != "glancing" or not b.eligible for b in white_table(TWO_HAND, BOSS).bands)
    t = white_table(TWO_HAND, replace(BOSS, level_for_target=94))
    g = next(b for b in t.bands if b.outcome == "glancing")
    assert g.eligible and g.chance_bp == 5000                      # (10 + 10 * 4) * 100, no cap


def test_glancing_only_for_player_or_pet_attackers_vs_non_player_non_pet():
    low = Victim(is_player=False, level_for_target=99)
    npc = Attacker(is_player=False, is_controlled_by_player=False, level_for_target=90)
    assert not next(b for b in white_table(npc, low).bands if b.outcome == "glancing").eligible
    pet = Attacker(is_player=False, is_pet=True, level_for_target=90)
    assert next(b for b in white_table(pet, low).bands if b.outcome == "glancing").eligible
    assert not next(b for b in white_table(TWO_HAND, replace(low, is_pet=True)).bands if b.outcome == "glancing").eligible


def test_crushing_is_eligible_but_arithmetically_dead():
    npc = Attacker(is_player=False, is_controlled_by_player=False, level_for_target=97, crit_done_pct=5.0)
    player = Victim(is_player=True, level_for_target=90)
    t = white_table(npc, player)
    band = next(b for b in t.bands if b.outcome == "crushing")
    assert band.eligible and band.chance_bp == 97 - 90 * 1000 - 1500 < 0
    assert "crushing" not in t.probabilities()


def test_sitting_player_victim_is_always_crit_after_miss():
    v = Victim(is_player=True, level_for_target=90, stand_state=False)
    assert _probs(white_table(DW, v)) == {"crit": pytest.approx(0.835), "miss": pytest.approx(0.165)}
    assert "crit" not in _probs(white_table(replace(DW, crit_done_pct=0.0), v))


def test_facing_rules_player_vs_creature_victims():
    behind_npc = replace(BOSS, facing_attacker=False, mod_dodge_percent_aura=10.0)
    p = _probs(white_table(TWO_HAND, behind_npc))
    assert "dodge" in p and "parry" not in p and "block" not in p   # creatures still dodge from behind
    behind_player = Victim(is_player=True, level_for_target=90, facing_attacker=False, dodge_percentage=20.0,
                           parry_percentage=20.0, can_parry=True)
    assert set(_probs(white_table(TWO_HAND, behind_player))) == {"crit", "normal"}
    blur = replace(behind_player, ignore_hit_direction=True)       # aura 288 (212800 Blur)
    assert "dodge" in _probs(white_table(TWO_HAND, blur))


def test_casting_or_controlled_victim_cannot_avoid():
    for v in (replace(BOSS, casting=True), replace(BOSS, controlled=True)):
        assert set(_probs(white_table(TWO_HAND, v))) == {"crit", "normal"}


def test_spell_table_no_active_defense_and_ranged_rules():
    assert set(spell_table(TWO_HAND, BOSS, SpellFacts(no_active_defense=True)).probabilities()) == {"none"}
    ranged = spell_table(TWO_HAND, BOSS, SpellFacts(dmg_class_ranged=True))
    assert ranged.probabilities() == {"block": 0.075, "none": 0.925}      # no dodge/parry for RANGED
    # deflect is gated by CONTROLLED only, not by casting (Unit.cpp:2670)
    casting = spell_table(TWO_HAND, replace(BOSS, casting=True, deflect_aura=10.0), SpellFacts(dmg_class_ranged=True))
    assert casting.probabilities().get("deflect") == pytest.approx(0.10)
    controlled = spell_table(TWO_HAND, replace(BOSS, controlled=True, deflect_aura=10.0), SpellFacts(dmg_class_ranged=True))
    assert "deflect" not in controlled.probabilities()


def test_spell_evade_precedes_always_hit_and_creature_crit_needs_mod_owner():
    t = spell_table(TWO_HAND, replace(BOSS, evading=True), SpellFacts(always_hit=True))
    assert t.short_circuit == "evade"
    npc = Attacker(is_player=False, is_controlled_by_player=False)
    assert spell_table(npc, Victim(is_player=True), SpellFacts()).crit["chance_pct"] == 0.0
    pet = replace(npc, is_pet=True, has_spell_mod_owner=True)
    assert spell_table(pet, Victim(is_player=True), SpellFacts()).crit["chance_pct"] == pytest.approx(5.0)


def test_block_percent_units_player_fraction_creature_percent():
    frac = block_percent(2000, 3430.0)
    assert 0.36 < frac < 0.37 and frac == f32(frac)
    assert white_blocked_amount(1000, frac) == 3                    # fraction consumed as a percent
    assert white_blocked_amount(1000, creature_block_percent()) == 300
    assert white_blocked_amount(1000, creature_block_percent(), True, 1.5) == 900
    assert spell_block_value(frac) == 0 and spell_block_value(creature_block_percent()) == 30
    assert block_percent(10**9, 3430.0) == f32(0.85)


def test_crit_and_glancing_damage_binary32():
    assert crit_damage(1000) == 2000
    assert crit_damage(1000, 1.3) == 2599                           # (1.3f - 1) * 100 = 29.9999962f
    assert glancing_damage(1000, 86, 93) == (700, 300)


def test_basis_point_truncation():
    assert to_basis_points(16.5) == 1650
    assert to_basis_points(f32(0.53)) == 52                         # 0.53f * 100.0f = 52.9999962 -> int32 52
    bad = [i for i in range(1, 5000) if to_basis_points(f32(i / 100)) != i]
    assert len(bad) == 282                                          # hundredths that lose one basis point


def test_probe_basis_point_truncation_in_band_edges(probe):
    v = replace(BOSS, mod_block_percent_aura=0.11)                  # 3 + 0.11 + 4.5 -> 7.6099997f -> 760 bp
    probe.configure(TWO_HAND, v)
    out = probe.ask("white 0")
    assert out == _white_rle(TWO_HAND, v, "base")
    assert "block@300 crit@1060" in out                             # parry 300 bp, block 760 bp (not 761)


# --------------------------------------------------------------------------- differential

def _white_rle(a, v, att):
    t = white_table(a, v, att)
    return rle(t.outcome_for_roll)


def _spell_rle(a, v, s):
    t = spell_table(a, v, s)
    return rle(t.result_for_roll)


@pytest.mark.parametrize("witness", [w for w in table_witness_inputs()], ids=lambda w: w["id"])
def test_probe_witness_tables_match_trinity(probe, witness):
    a, v = witness["attacker"], witness["victim"]
    probe.configure(a, v, witness.get("spell"))
    if witness.get("spell") is not None:
        assert probe.ask("spell") == _spell_rle(a, v, witness["spell"])
    else:
        for att in witness["hands"]:
            code = {"base": 0, "off": 1}[att]
            assert probe.ask(f"white {code}") == _white_rle(a, v, att), att


def test_probe_chance_getters_match(probe):
    a = replace(DW, expertise_mainhand=3, mod_hit_chance_aura=1.1, autoattack_crit_aura=20.0)
    v = replace(BOSS, mod_dodge_percent_aura=0.3, mod_parry_percent_aura=0.7, mod_block_percent_aura=5.1,
                attacker_melee_crit_aura=-6.0, attacker_melee_hit_aura=0.2)
    probe.configure(a, v)
    from weapon_combat.attack_table import block_chance, crit_chance_against
    for name, fn in (("dodge", dodge_chance), ("parry", parry_chance), ("block", block_chance)):
        assert float(probe.ask(f"chance {name} 0")) == pytest.approx(fn(a, v, "base"), abs=0, rel=1e-8)
    assert float(probe.ask("chance miss 1")) == pytest.approx(miss_chance(a, v, "off"), rel=1e-8)
    assert float(probe.ask("chance crit 0")) == pytest.approx(crit_chance_against(a, v, "base"), rel=1e-8)


def test_probe_block_crit_glancing_arithmetic(probe):
    probe.ask("clear")
    assert probe.ask("blockpct 2000 3430") == f"{block_percent(2000, 3430.0):.9g}"
    assert probe.ask("block 1000 1 2000 3430 0 1") == f"{1000 - 3} 3"
    assert probe.ask("block 1000 0 0 0 1 1.5") == "100 900"
    assert probe.ask("crit 1000 1.3") == str(crit_damage(1000, 1.3))
    assert probe.ask("glancing 1000 86 93") == "700 300"


_pct = st.floats(min_value=-5.0, max_value=40.0, allow_nan=False, width=32)


@settings(max_examples=60, deadline=None)
@given(dodge=_pct, parry=_pct, block=_pct, crit=_pct, hit=_pct, level=st.integers(85, 99),
       dw=st.booleans(), facing=st.booleans(), exp=st.integers(0, 40), victim_player=st.booleans())
def test_probe_random_white_tables(probe, dodge, parry, block, crit, hit, level, dw, facing, exp, victim_player):
    a = Attacker(level_for_target=90, have_offhand_weapon=dw, crit_done_pct=abs(crit), mod_hit_chance_aura=hit,
                 expertise_mainhand=exp)
    if victim_player:
        v = Victim(is_player=True, level_for_target=level, facing_attacker=facing, dodge_percentage=abs(dodge),
                   parry_percentage=abs(parry), block_percentage=abs(block), can_parry=True, can_block=True,
                   has_useable_shield=True)
    else:
        v = Victim(is_player=False, level_for_target=level, facing_attacker=facing, mod_dodge_percent_aura=dodge,
                   mod_parry_percent_aura=parry, mod_block_percent_aura=block)
    probe.configure(a, v)
    assert probe.ask("white 0") == _white_rle(a, v, "base")


@settings(max_examples=40, deadline=None)
@given(dodge=_pct, block=_pct, resist=st.floats(0, 20, width=32), ranged=st.booleans(), facing=st.booleans(),
       nd=st.booleans(), np_=st.booleans(), nb=st.booleans(), deflect=st.floats(0, 30, width=32), mod=_pct)
def test_probe_random_spell_tables(probe, dodge, block, resist, ranged, facing, nd, np_, nb, deflect, mod):
    a = TWO_HAND
    v = Victim(is_player=False, level_for_target=93, facing_attacker=facing, mod_dodge_percent_aura=dodge,
               mod_block_percent_aura=block, mechanic_resist_pct=resist, deflect_aura=deflect)
    s = SpellFacts(dmg_class_ranged=ranged, no_attack_dodge=nd, no_attack_parry=np_, no_attack_block=nb,
                   hit_chance_spellmod_pct=mod)
    probe.configure(a, v, s)
    assert probe.ask("spell") == _spell_rle(a, v, s)
