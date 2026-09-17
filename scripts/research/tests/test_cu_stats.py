"""Track B (A3): controlled-unit stat sources, inheritance classification and the prepared-stat oracle."""

from __future__ import annotations

import json
import subprocess
import sys

import pytest
from hypothesis import given, settings
from hypothesis import strategies as hs

from controlled_units import CORPORA, SourceError
from controlled_units import inheritance as inh
from controlled_units import stats as st

RESEARCH = CORPORA.parents[2] / "scripts" / "research"
_STORE = st.load_pet_levelstats()


@pytest.fixture(scope="module")
def sources():
    return st.sources()


@pytest.fixture(scope="module")
def pet_store():
    return st.load_pet_levelstats()


def _owner(cls: int, level: int = 90, **kw) -> st.OwnerFacts:
    base = dict(stats=(1000, 1000, 20000, 1000, 0), armor=5000, attack_power_melee=10000.0, attack_power_ranged=10000.0,
                mod_damage_done_pos=(10000,) * 7, spell_base_damage_bonus={"frost": 10000, "fire": 10000, "nature": 10000, "shadow": 10000})
    base.update(kw)
    return st.OwnerFacts(class_id=cls, level=level, **base)


# --- binary32 helpers -------------------------------------------------------

def test_calculate_pct_is_float_arithmetic():
    # CalculatePct<float,int>(x, 30) = float(x * 30.0f / 100.0f)
    assert st.calc_pct_f(20000.0, 30) == 6000.0
    assert st.calc_pct_i(5000, 70) == 3500
    assert st.f32(8.4) != 8.4


def test_u32_undefined_behaviour_is_flagged():
    with pytest.raises(SourceError):
        st.u32(-1.0)
    assert st.u32_x86(-9.0) == (4294967287, True)
    assert st.u32_x86(12.9) == (12, False)


@settings(max_examples=200, deadline=None)
@given(hs.floats(-1e6, 1e6, allow_nan=False))
def test_f32_idempotent(x):
    assert st.f32(st.f32(x)) == st.f32(x)


# --- pet_levelstats ---------------------------------------------------------

def test_pet_levelstats_entries(pet_store):
    assert len(pet_store) == 32
    assert sum(len(v) for v in pet_store.values()) == 2715


def test_hunter_pet_levels_81_to_90_are_placeholders(pet_store):
    for lvl in (81, 85, 88, 90):
        info, prov = st.get_pet_level_info(pet_store, 1, lvl)
        assert (info.health, info.armor, info.stats) == (1, 1, (1, 1, 1, 1, 1)), prov


def test_ghoul_gap_filled_from_80(pet_store):
    info, prov = st.get_pet_level_info(pet_store, 26125, 90)
    assert "gap-filled" in prov and info.health == 4551 and info.stats[0] == 331


def test_level_clamps_to_max_player_level(pet_store):
    a, _ = st.get_pet_level_info(pet_store, 416, 90)
    b, _ = st.get_pet_level_info(pet_store, 416, 120)
    assert a == b


# --- DB2 engine inputs --------------------------------------------------------

def test_expected_stat_level_90(sources):
    db2, _ = sources
    value, prov = db2.evaluate("CreatureHealth", 90, 11, 0, 1)
    assert value == 141482.015625 and "ID=475" in prov
    assert db2.evaluate("CreatureHealth", 0, 11, 0, 1)[0] == 1.0  # no row -> 1.0f


def test_content_tuning_max_level_type(sources):
    db2, _ = sources
    assert db2.content_tuning_levels(482) == {"MinLevel": 1, "MaxLevel": 90}
    assert db2.content_tuning_levels(0) is None


def test_display_power_gate(sources):
    db2, _ = sources
    assert db2.display_power(1, False)[:2] == (1, 0)   # rage lacks IsUsedByNPCs -> mana stays
    assert db2.display_power(4, False)[:2] == (3, 3)   # energy
    assert db2.display_power(1, True)[:2] == (2, 2)    # hunter pet focus


# --- summon routing -------------------------------------------------------------

@pytest.mark.parametrize("control,title,flags,expected", [
    (2, 0, 0, "Guardian"), (1, 2, 0, "Guardian"), (1, 1, 0, "TempSummon"), (1, 1, 0x200, "Guardian"),
    (1, 4, 0, "Totem"), (1, 11, 0, "Totem"), (1, 5, 0, "Minion"), (3, 0, 0, "Puppet"), (5, 0, 0, "Minion"),
    (0, 0, 0, "TempSummon"), (7, 0, 0, "none"),
])
def test_routing_matches_map_summon_creature(control, title, flags, expected):
    assert inh.route_summon_effect(control, title, flags)["trinity_unit_class"] == expected


@pytest.mark.parametrize("spell,entry,cls", [
    (688, 416, "Pet/SUMMON_PET"), (111859, 416, "Guardian"), (30146, 17252, "Pet/SUMMON_PET"), (104317, 55659, "Guardian"),
    (52150, 26125, "Pet/SUMMON_PET"), (46585, 26125, "Guardian"), (31687, 78116, "Pet/SUMMON_PET"),
    (1280172, 19668, "Guardian"), (228562, 29264, "Guardian"), (883, 0, "Pet/HUNTER_PET"),
])
def test_resolve_summon_witnesses(spell, entry, cls):
    r = inh.resolve_summon(spell)
    assert (r["status"], r["entry"], r["trinity_unit_class"]) == ("ok", entry, cls)


def test_resolve_summon_fails_closed():
    assert inh.resolve_summon(46584)["status"] == "unresolved"      # trigger -> aura dummy, no summon effect
    assert inh.resolve_summon(86659)["status"] == "unresolved"      # GoAK: no summon effect in 12.1
    assert inh.resolve_summon(5394)["trinity_unit_class"] == "Totem"
    assert inh.resolve_summon(321686)["status"] == "unresolved"     # no SpellClassOptions -> owner class unknown
    assert inh.resolve_summon(321686, owner_class=8)["status"] == "ok"


# --- oracle --------------------------------------------------------------------

def test_imp_pet_known_values(pet_store):
    o = st.run_oracle(416, "Pet/SUMMON_PET", _owner(9, 80, stats=(100, 100, 50000, 40000, 0), armor=3000,
                                                    attack_power_melee=0.0, attack_power_ranged=0.0,
                                                    mod_damage_done_pos=(0,) + (40000,) * 6),
                      pet_store=pet_store)
    assert o["max_health"] == 128128 and o["attack_power"] == 22965 and o["bonus_spell_damage"] == 6000
    assert o["stat_from_owner"]["stamina"] == 15000.0 and o["stat_from_owner"]["intellect"] == 12000.0
    assert o["armor"] == {"base": 3191, "bonus": 3000}


def test_hunter_pet_armor_and_ap(pet_store):
    o = st.run_oracle(0, "Pet/HUNTER_PET", _owner(3), pet_store=pet_store)
    assert o["pet_type"] == "HUNTER_PET"
    assert o["armor"]["bonus"] == 3500            # 70% (comment says 35%)
    assert o["bonus_spell_damage"] == 1287        # int32(10000 * 0.1287f)
    assert any("undefined behaviour" in t for t in o["trace"])  # first UpdateMaxHealth: 1 + (0-1)*10 < 0


def test_mage_water_elemental_is_max_pet_type(sources, pet_store):
    db2, world = sources
    o = st.run_oracle(78116, "Pet/SUMMON_PET", _owner(8), pet_store=pet_store, db2=db2, world=world)
    assert o["pet_type"] == "MAX_PET_TYPE(none)"
    assert o["stat_from_owner"]["intellect"] == 300.0  # mage owner gets the 30% INT share
    assert o["armor"]["bonus"] == 5000                  # IsPet -> 100% owner armor


def test_guardian_health_counts_expected_stat_twice(sources, pet_store):
    db2, world = sources
    pet = st.run_oracle(26125, "Pet/SUMMON_PET", _owner(6), pet_store=pet_store, db2=db2, world=world)
    grd = st.run_oracle(26125, "Guardian", _owner(6), pet_store=pet_store, db2=db2, world=world)
    assert grd["max_health"] - pet["max_health"] == int(grd["engine_inputs"]["pre_health_base_value"])
    assert grd["armor"]["bonus"] == 0 and pet["armor"]["bonus"] == 5000


def test_guardian_without_content_tuning_is_level_zero(sources, pet_store):
    db2, world = sources
    o = st.run_oracle(31216, "Guardian", _owner(8), pet_store=pet_store, db2=db2, world=world)
    assert o["level"] == 0 and o["source_notes"]["select_level"] == 1


def test_oracle_refuses_non_guardian_classes():
    with pytest.raises(SourceError):
        st.run_oracle(3527, "Totem", _owner(7))


def test_oracle_fails_closed_without_world_rows(pet_store):
    eng = st.EngineInputs()
    cinfo = st.CreatureFacts(entry=55659, unit_class=4)
    with pytest.raises(SourceError):
        st.run_oracle(55659, "Guardian", _owner(9), petlevel=90, engine=eng, cinfo=cinfo, pet_store=pet_store)


@settings(max_examples=50, deadline=None)
@given(sta=hs.integers(0, 10**6), sta2=hs.integers(0, 10**6))
def test_pet_health_monotone_in_owner_stamina(sta, sta2):
    store = _STORE
    lo, hi = sorted((sta, sta2))
    a = st.run_oracle(417, "Pet/SUMMON_PET", _owner(9, 85, stats=(0, 0, lo, 0, 0)), pet_store=store)["max_health"]
    b = st.run_oracle(417, "Pet/SUMMON_PET", _owner(9, 85, stats=(0, 0, hi, 0, 0)), pet_store=store)["max_health"]
    assert a <= b


# --- corpora & CLI ---------------------------------------------------------------

def test_stats_corpus_shape():
    doc = json.loads((CORPORA / "stats.json").read_text())
    assert list(doc)[0] == "provenance" and doc["provenance"]["trinitycore_commit"] == st.TRINITY_COMMIT
    classes = {r["unit_class"] for r in doc["consumers"]}
    assert classes == {"Pet/SUMMON_PET", "Pet/HUNTER_PET", "Guardian", "Minion", "Totem", "Puppet", "TempSummon"}
    vocab = {st.CREATION_SNAPSHOT, st.APPLICATION_SNAPSHOT, st.DYNAMIC, st.EXPLICIT, st.INDEPENDENT, st.LEGACY, st.UNRESOLVED}
    assert {r["classification"] for r in doc["consumers"]} <= vocab
    haste = [r for r in doc["consumers"] if r["stat"] == "haste"]
    assert haste and all(r["classification"] == "unresolved" for r in haste)


def test_inheritance_corpus_witnesses():
    doc = json.loads((CORPORA / "inheritance.json").read_text())
    by = {w["label"]: w for w in doc["witness_resolutions"]}
    assert by["Warlock Imp"]["oracle"]["status"] == "ok"
    assert by["Paladin Guardian of Ancient Kings"]["resolution"]["status"] == "unresolved"
    assert doc["pet_scaling_spells"]["count"] >= 1


def test_cli_inherit_imp():
    out = subprocess.run([sys.executable, "controlled_units.py", "inherit", "--spell", "688", "--owner-level", "80",
                          "--owner-stamina", "50000", "--owner-int", "40000", "--owner-armor", "3000",
                          "--owner-sp-pos", "0", "40000", "40000", "40000", "40000", "40000", "40000"],
                         cwd=RESEARCH, capture_output=True, text=True, check=True)
    doc = json.loads(out.stdout)
    assert doc["status"] == "ok" and doc["max_health"] == 128128


def test_cli_inherit_fails_closed_for_goak():
    out = subprocess.run([sys.executable, "controlled_units.py", "inherit", "--spell", "86659"],
                         cwd=RESEARCH, capture_output=True, text=True)
    assert out.returncode == 2 and json.loads(out.stdout)["status"] == "unresolved"
