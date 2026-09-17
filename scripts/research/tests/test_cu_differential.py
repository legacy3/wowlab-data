"""Differential: Python Guardian/Pet stat oracle vs Trinity's own code (tools/tc_pet_probe, bounded)."""

from __future__ import annotations

import struct

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as hs

from controlled_units import stats as st
from cu_b_helpers import PROBE, Case, comparable_probe, comparable_python, parse_probe, probe_line, run_probe, run_python

pytestmark = pytest.mark.skipif(not PROBE.exists(), reason="build tools/tc_pet_probe first (make -C tools/tc_pet_probe)")

P = st.PetLevelInfo
ES90_HP, ES90_DPS = 141482.015625, 6855.703125
STD_OWNER = dict(owner_stats=(1000, 1000, 20000, 1000, 0), owner_armor=5000, owner_ap=10000, owner_rap=10000)

CASES = {
    "imp-pet-binary32": Case(416, "Pet/SUMMON_PET", st.CLASS_WARLOCK, 80, creature_unit_class=8,
                             pinfo=P(2129, 3228, 3191, (175, 54, 119, 402, 319)), owner_stats=(100, 100, 50000, 40000, 0),
                             owner_armor=3000, pos=(0,) + (40000,) * 6),
    "hunter-pet-placeholder-lvl90": Case(0, "Pet/HUNTER_PET", st.CLASS_HUNTER, 90, calc_power=2, max_base_power=100,
                                         pinfo=P(1, 1, 1, (1, 1, 1, 1, 1)), **STD_OWNER),
    "ghoul-pet": Case(26125, "Pet/SUMMON_PET", st.CLASS_DEATH_KNIGHT, 80, creature_unit_class=1, calc_power=1,
                      power_npc_ok=False, max_base_power=1000, pinfo=P(4551, 2134, 4513, (331, 95, 92, 99, 87)), **STD_OWNER),
    "ghoul-guardian-double-health": Case(26125, "Guardian", st.CLASS_DEATH_KNIGHT, 90, creature_unit_class=1, calc_power=1,
                                         power_npc_ok=False, max_base_power=1000, pinfo=P(4551, 2134, 4513, (331, 95, 92, 99, 87)),
                                         health_modifier=1.0, mana_modifier=1.0, select_level=90,
                                         es_health_sel=ES90_HP, es_dps_sel=ES90_DPS, **STD_OWNER),
    "felguard-guardian": Case(17252, "Guardian", st.CLASS_WARLOCK, 90, creature_unit_class=1, calc_power=1, power_npc_ok=False,
                              pinfo=P(7497, 5823, 13219, (189, 144, 482, 314, 171)), health_modifier=2.0, mana_modifier=1.0,
                              select_level=90, es_health_sel=ES90_HP, es_dps_sel=ES90_DPS, **STD_OWNER),
    "spirit-wolf-guardian": Case(29264, "Guardian", st.CLASS_SHAMAN, 90, calc_power=1, power_npc_ok=False, max_base_power=1000,
                                 base_attack_time=1500, health_modifier=0.5, mana_modifier=3.0, es_health_pet=ES90_HP,
                                 es_dps_pet=ES90_DPS, select_level=90, es_health_sel=ES90_HP, es_dps_sel=ES90_DPS, base_mana=100,
                                 **STD_OWNER),
    "wild-imp-default-branch": Case(55659, "Guardian", st.CLASS_WARLOCK, 90, creature_unit_class=4, calc_power=3, max_base_power=100,
                                    health_modifier=0.06, es_health_pet=ES90_HP, es_dps_pet=ES90_DPS, select_level=90,
                                    es_health_sel=ES90_HP, es_dps_sel=ES90_DPS, **STD_OWNER),
    "mirror-image-level0": Case(31216, "Guardian", st.CLASS_MAGE, 0, creature_unit_class=8, health_modifier=0.3,
                                sbdb=(10000, 0, 0, 0), select_level=1, es_health_sel=78.0, es_dps_sel=4.75353193283, **STD_OWNER),
    "water-elemental-mage-pet": Case(78116, "Pet/SUMMON_PET", st.CLASS_MAGE, 90, creature_unit_class=8, health_modifier=1.2,
                                     es_health_pet=ES90_HP, es_dps_pet=ES90_DPS, base_mana=5000, pos=(0, 100, 300, 0, 9000, 200, 0),
                                     **STD_OWNER),
    "shadowfiend-guardian": Case(19668, "Guardian", st.CLASS_PRIEST, 85, creature_unit_class=8,
                                 pinfo=P(3205, 6011, 0, (241, 172, 95, 380, 248)), sbdb=(0, 0, 0, 12345), health_modifier=1.0,
                                 select_level=90, es_health_sel=ES90_HP, es_dps_sel=ES90_DPS, **STD_OWNER),
    "negative-sp-sign-quirk": Case(417, "Pet/SUMMON_PET", st.CLASS_WARLOCK, 85, creature_unit_class=8,
                                   pinfo=P(2000, 3000, 1000, (100, 100, 100, 100, 100)), pos=(0, 0, 500, 0, 0, 500, 0),
                                   neg=(0, 0, -300, 0, 0, 0, 0), **STD_OWNER),
    "gargoyle-guardian": Case(27829, "Guardian", st.CLASS_DEATH_KNIGHT, 90, creature_unit_class=1, health_modifier=1.5,
                              es_health_pet=ES90_HP, es_dps_pet=ES90_DPS, select_level=90, es_health_sel=ES90_HP,
                              es_dps_sel=ES90_DPS, **STD_OWNER),
    "treant-legacy-entry": Case(1964, "Guardian", st.CLASS_DRUID, 60, creature_unit_class=1, sbdb=(0, 777, 0, 0),
                                pos=(0, 0, 0, 900, 0, 0, 0), select_level=60, es_health_sel=5000.0, es_dps_sel=100.0,
                                es_health_pet=5000.0, es_dps_pet=100.0, **STD_OWNER),
    "bloodworm-legacy-entry": Case(28017, "Guardian", st.CLASS_DEATH_KNIGHT, 80, creature_unit_class=1, select_level=80,
                                   es_health_sel=29552.177734375, es_dps_sel=1154.8696289062, **STD_OWNER),
}


@pytest.fixture(scope="module")
def probe_results():
    names = list(CASES)
    outs = run_probe([probe_line(CASES[n]) for n in names])
    assert len(outs) == len(names), outs
    return {n: parse_probe(o) for n, o in zip(names, outs)}


@pytest.mark.parametrize("name", list(CASES))
def test_oracle_matches_probe(name, probe_results):
    py = comparable_python(run_python(CASES[name]))
    pr = comparable_probe(probe_results[name])
    assert py == pr


def test_binary32_discriminates(probe_results):
    """MaxHealth 128128 (binary32 8.4f) vs 128129 in exact decimal arithmetic."""
    assert probe_results["imp-pet-binary32"]["max_health"] == 128128
    assert int(2129 + (15119 - 119) * 8.4) == 128129  # what a double/decimal port would store


def test_guardian_counts_health_twice(probe_results):
    """ghoul Guardian: UNIT_MOD_HEALTH BASE_VALUE (level-90 ExpectedStat health) + pet_levelstats hp."""
    g = probe_results["ghoul-guardian-double-health"]
    p = probe_results["ghoul-pet"]
    assert g["max_health"] - (141483 + 4551) == p["max_health"] - 4551


def test_negative_owner_sp_increases_pet_bonus(probe_results):
    r = probe_results["negative-sp-sign-quirk"]
    assert r["bonus_spell_damage"] == int(800 * 0.15)  # fire = 500 - (-300)


def test_negative_float_to_uint32_platform_result():
    out = run_probe(["ucast " + float(-9.0).hex(), "ucast " + float(-1.5).hex()])
    assert [int(x) for x in out] == [st.u32_x86(-9.0)[0], st.u32_x86(-1.5)[0]] == [4294967287, 4294967295]


def _f(x: float) -> float:
    return struct.unpack("<f", struct.pack("<f", x))[0]


ENTRIES = [416, 417, 1860, 1863, 17252, 26125, 29264, 510, 1964, 15438, 15352, 19668, 19833, 19921, 31216, 27829, 28017, 55659, 0]


@settings(max_examples=300, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(entry=hs.sampled_from(ENTRIES), kind=hs.sampled_from(["Pet/SUMMON_PET", "Pet/HUNTER_PET", "Guardian"]),
       owner_class=hs.sampled_from([3, 5, 6, 7, 8, 9, 11]), level=hs.integers(1, 90),
       sta=hs.integers(0, 200000), inte=hs.integers(0, 200000), strn=hs.integers(0, 50000),
       armor=hs.integers(0, 60000), ap=hs.integers(0, 300000), sp=hs.integers(0, 200000), neg=hs.integers(-5000, 0),
       has_pinfo=hs.booleans(), php=hs.integers(1, 65535), pst=hs.integers(0, 3000),
       hm=hs.floats(0.25, 100.0, width=32), es=hs.floats(1.0, 200000.0, width=32), calc_power=hs.sampled_from([0, 1, 2, 3]))
def test_random_cases(entry, kind, owner_class, level, sta, inte, strn, armor, ap, sp, neg, has_pinfo, php, pst, hm, es, calc_power):
    npc_ok = calc_power != 1  # rage lacks IsUsedByNPCs in 12.1 PowerType.db2
    c = Case(entry, kind, owner_class, level, calc_power=calc_power, power_npc_ok=npc_ok,
             max_base_power={0: 0, 1: 1000, 2: 100, 3: 100}[calc_power],
             pinfo=P(php, php // 2, pst, (pst, pst // 2, pst // 3, pst // 4, 7)) if has_pinfo else None,
             owner_stats=(strn, 10, sta, inte, 0), owner_armor=armor, owner_ap=ap, owner_rap=ap // 2,
             pos=(0,) + (sp,) * 6, neg=(0, 0, neg, 0, 0, neg, 0), sbdb=(sp, sp // 2, sp // 3, sp // 4),
             health_modifier=_f(hm), mana_modifier=_f(hm / 2), base_mana=php, es_health_pet=_f(es), es_dps_pet=_f(es / 20),
             select_level=level, es_health_sel=_f(es), es_dps_sel=_f(es / 20))
    try:
        py = comparable_python(run_python(c))
    except st.SourceError:
        pytest.skip("oracle fails closed for this input")
    pr = comparable_probe(parse_probe(run_probe([probe_line(c)])[0]))
    assert py == pr
