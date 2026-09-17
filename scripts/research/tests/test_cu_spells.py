"""Track B (A4): controlled-unit spell acquisition, availability/AI and pet aura types."""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from controlled_units import CORPORA
from controlled_units import spells as sp

RESEARCH = CORPORA.parents[2] / "scripts" / "research"


@pytest.fixture(scope="module")
def corpus():
    return json.loads((CORPORA / "spells.json").read_text())


@pytest.fixture(scope="module")
def auras():
    return {a["aura"]: a for a in sp.pet_aura_types()}


def _paths(rec, applicable_only=True):
    return {p["path"] for p in rec["paths"] if not applicable_only or p.get("applicable", True)}


def test_autocast_predicates():
    growl = sp.spell_flags(2649)
    assert growl["exists"] and not growl["passive"] and growl["autocastable"]
    assert sp.pet_decide_state(growl) in ("ACT_ENABLED", "ACT_DISABLED")
    passive = sp.spell_flags(34902)  # "Hunter Pet"
    assert passive["passive"] and not passive["autocastable"] and sp.pet_decide_state(passive) == "ACT_PASSIVE"


def test_hunter_family_levelup_and_passives():
    ft = sp.family_tables()
    assert (1, 2649) in ft["levelup"][1]                  # Growl, level 1, family 1 (Wolf)
    assert {34902, 19581, 8875} <= ft["passives"][1]       # Hunter Pet, Pet Health, Pet Damage


def test_felstorm_has_no_trinity_acquisition_path():
    rec = sp.spell_paths(89751)
    assert rec["exists"] and rec["spell_level"] == 0 and not rec["passive"]
    assert rec["status"] == "unresolved" and not rec["paths"]


def test_legion_strike_is_levelup_for_felguard_family():
    rec = sp.spell_paths(30213)
    assert any(p["path"] == "levelup" and p["family"] == 29 for p in rec["paths"])


def test_ghoul_template_spells_are_default_for_pet():
    rec = sp.spell_paths(47468, entry=26125)  # Claw
    kinds = _paths(rec)
    assert "default" in kinds and "levelup" in kinds


def test_totem_path_applies_only_to_totems():
    hst = sp.spell_paths(5672, entry=3527)   # Healing Stream on Healing Stream Totem
    assert "totem" in _paths(hst)
    ghoul = sp.spell_paths(47468, entry=26125)
    assert "totem" not in _paths(ghoul) and "totem" in _paths(ghoul, applicable_only=False)


def test_mirror_image_frostbolt_script_and_template():
    rec = sp.spell_paths(59638, entry=31216)
    kinds = _paths(rec, applicable_only=False)
    assert {"script", "template-ai"} <= kinds


def test_pet_spec_spells_present():
    spell = int(sp._db2()["spec_spells"][74][0]["SpellID"])  # first Ferocity spec spell
    specs = [p for p in sp.spell_paths(spell)["paths"] if p["path"] == "spec"]
    assert specs and {p["spec"] for p in specs} <= {74, 79, 81, 535, 536, 537}


def test_entry_unit_classes():
    classes = sp.entry_unit_classes()
    assert "Pet" in classes[416] and "Guardian" in classes[416]
    assert "Totem" in classes[3527]


def test_aura_429_is_null_and_in_scope(auras):
    a = auras[429]
    assert a["handler"] == "HandleNULL" and not a["implemented"]
    assert a["spells_12_1"] == 393 and a["spells_in_player_scope"] == 63
    assert {e["spell"] for e in a["in_scope_examples"]} >= {76657, 77219}


def test_other_null_pet_auras(auras):
    assert auras[381]["handler"] == auras[382]["handler"] == auras[157]["handler"] == "HandleNULL"
    assert auras[381]["spells_in_player_scope"] == 7 and auras[382]["spells_in_player_scope"] == 6


def test_override_pet_specs_is_implemented(auras):
    assert auras[451]["handler"] == "HandleOverridePetSpecs" and auras[451]["implemented"]


def test_corpus_census(corpus):
    c = corpus["ability_census"]
    assert c["spells_total"] == sum(c["by_status"].values())
    assert {"has-path", "unresolved"} <= set(c["by_status"])
    names = {r["name"] for r in c["without_path"]}
    assert "Felstorm" in names


def test_corpus_provenance_and_paths(corpus):
    assert list(corpus)[0] == "provenance"
    assert set(corpus["acquisition_paths"]) >= {"levelup", "family-passive", "default", "spec", "charm", "totem", "pet-aura"}
    assert all(r["coordinates"] for r in corpus["execution_differences"])


def test_cli_spells_single():
    out = subprocess.run([sys.executable, "controlled_units.py", "spells", "--spell", "89751"], cwd=RESEARCH,
                         capture_output=True, text=True, check=True)
    assert json.loads(out.stdout)["status"] == "unresolved"


def test_cli_aura_types():
    out = subprocess.run([sys.executable, "controlled_units.py", "aura-types", "--pet"], cwd=RESEARCH,
                         capture_output=True, text=True, check=True)
    assert "SPELL_AURA_MOD_SUMMON_DAMAGE" in out.stdout and "HandleNULL" in out.stdout


def test_witnesses_corpus():
    doc = json.loads((CORPORA / "witnesses-b.json").read_text())
    by = {w["id"]: w for w in doc["witnesses"]}
    assert by["dk-ghoul-talent"]["link"]["status"] == "script-hook-inert"
    assert by["paladin-goak"]["entry_search"]["status"] == "unresolved"
    assert by["druid-treant"]["link"]["status"] == "authored-trigger"
    assert doc["interactions"]["pet_only_damage_multiplier"]["aura"]["aura"] == 429
