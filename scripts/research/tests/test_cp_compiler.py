"""Fixture compiler: fail-closed validation, derivation, determinism and the TraitMgr / default-skill ports."""

from __future__ import annotations

import json

import pytest

from cp_e_helpers import FIXTURES_DIR, compile_raw, compiled, compiler, fixture_names, load_raw, raw_copy
from character_prep import EVIDENCE_CLASSES
from character_prep.compiler import TRINITY_RACE_BITS, TraitEngine, explain, observations_report
from character_prep.fixture import canonical_json

pytestmark = pytest.mark.snapshot

ARMS = "arms-warrior-plate-2h"
NO_FILL = "arms-warrior-plate-2h-no-fill-rule"
ARMS_DW = "arms-warrior-plate-dw-learned"


def errors(out: dict) -> list[str]:
    return out["validation"]["errors"]


# -- committed fixtures ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("name", fixture_names())
def test_fixture_compiles_cleanly(name):
    out = compiled(name)
    assert out["validation"]["ok"], out["validation"]["errors"]
    assert out["derived"]["armor_specialization"]["value"]["applied_spell_id"] is not None


@pytest.mark.parametrize("name", fixture_names())
def test_compiled_corpus_is_current(name):
    """The committed ``<name>.compiled.json`` is exactly what the compiler produces now (deterministic)."""
    assert (FIXTURES_DIR / f"{name}.compiled.json").read_text(encoding="utf-8") == canonical_json(compiled(name))


def test_compile_is_deterministic():
    a = canonical_json(compile_raw(raw_copy(ARMS)))
    b = canonical_json(compile_raw(raw_copy(ARMS)))
    assert a == b


def test_every_derived_node_carries_evidence_and_provenance():
    for name in fixture_names():
        for key, node in compiled(name)["derived"].items():
            assert node["evidence_class"] in EVIDENCE_CLASSES, (name, key)
            assert isinstance(node["provenance"], list) and node["provenance"], (name, key)
            assert isinstance(node["derived_from"], list), (name, key)
            assert all(p.startswith(("/fixture/", "/derived/", "/server_inputs/")) for p in node["derived_from"])


def test_fixture_plan_coverage():
    """C7: plate 2H, plate DW, BM hunter with pet, enhancement, leather agi DW, leather caster, cloth pet, cloth healer,
    multi-rank traits, 2/4-piece sets, gems, enchants, ratings, mastery."""
    names = fixture_names()
    assert len([n for n in names if not n.endswith("no-fill-rule")]) >= 8
    assert load_raw(ARMS_DW)["learned_spells"] == [296087]
    sets = set()
    multi_rank = gems = enchants = False
    for name in names:
        raw = load_raw(name)
        out = compiled(name)
        g = out["derived"]["gear"]["value"]
        sets |= {(b["item_set_id"], b["threshold"]) for b in g["set_bonuses"]}
        multi_rank |= any(e["rank"] > 1 for e in raw["traits"]["entries"])
        gems |= any(s.get("gems") for s in g["slots"].values())
        enchants |= any(e.get("enchant_ids") for e in raw["equipment"].values())
        if out["derived"]["stats"]["value"]:
            assert out["derived"]["stats"]["value"]["mastery_value"] > 0
            assert g["rating_totals"]
    assert multi_rank and gems and enchants
    assert {t for _, t in sets} >= {2, 4}
    assert load_raw("bm-hunter-mail-ranged-pet")["controlled_units"]["hunter_pet"]
    assert compiled("fury-warrior-plate-titan-grip")["derived"]["spells"]["value"]["gates"]["titan_grip"]["granted"]


def test_fixture_items_are_current_season():
    for name in fixture_names():
        for slot, item in compiled(name)["derived"]["gear"]["value"]["slots"].items():
            assert item["effective_item_level"] >= 259, (name, slot, item["item_id"])


# -- level-90 gap ---------------------------------------------------------------------------------------------------

def test_fixtures_are_level_90_because_their_items_require_it():
    """Every current-season item resolves to required level 90 (ItemScalingConfig via ItemBonus 49); the fixtures
    therefore sit at level 90 and opt into Trinity's base-stat fill rule explicitly (closeout review R3)."""
    for name in fixture_names():
        raw = load_raw(name)
        out = compiled(name)
        assert raw["identity"]["level"] == 90
        assert {s["required_level"] for s in out["derived"]["gear"]["value"]["slots"].values()} == {90}, name
        fill = raw["server_inputs"]["base_stats"].get("fill_rule", "none")
        assert (fill == "none") is (name == NO_FILL)
        if fill == "trinity":
            stats = out["derived"]["stats"]
            assert stats["evidence_class"] == "trinity-consumer"
            assert stats["value"]["base_stats_evidence"] == "trinity-consumer(fill-rule)"
            base = [u for u in out["unresolved"] if u["item"] == "/derived/stats/value/base"]
            assert len(base) == 1 and "level-80 row" in base[0]["reason"] and "unresolved" in base[0]["reason"]


def test_required_level_is_enforced():
    raw = raw_copy(ARMS)
    raw["identity"]["level"] = 80
    errs = errors(compile_raw(raw))
    assert len([e for e in errs if "requires level 90" in e and "CANT_EQUIP_LEVEL_I" in e]) == len(raw["equipment"])


def test_level_90_reports_base_stat_gap():
    out = compiled(NO_FILL)
    assert out["validation"]["ok"]
    stats = out["derived"]["stats"]
    assert stats["value"] is None and stats["evidence_class"] == "unresolved"
    assert [p["result"] for p in stats["provenance"] if p["step"] == "base-primary-stats"] == ["level-row-missing"]
    gap = [u for u in out["unresolved"] if u["item"] == "/derived/stats"]
    assert len(gap) == 1 and "level 90" in gap[0]["reason"] and "interpolated" in gap[0]["reason"]
    assert gap[0]["trinity_fallback"]["coordinates"].endswith("ObjectMgr.cpp:4349-4356")


def test_every_fixture_without_fill_rule_reports_the_gap():
    for name in fixture_names():
        raw = raw_copy(name)
        raw["server_inputs"]["base_stats"].pop("fill_rule", None)
        out = compile_raw(raw)
        assert out["validation"]["ok"], (name, errors(out))
        assert any(u["item"] == "/derived/stats" and "level 90" in u["reason"] for u in out["unresolved"]), name


def test_unsupplied_base_stats_is_unresolved():
    raw = raw_copy(ARMS)
    raw["server_inputs"]["base_stats"] = {"source": "unsupplied"}
    out = compile_raw(raw)
    assert out["derived"]["stats"]["value"] is None
    assert any("unsupplied" in u["reason"] for u in out["unresolved"])


# -- fail-closed validation -----------------------------------------------------------------------------------------

def test_inventory_type_illegal_for_slot():
    raw = raw_copy(ARMS)
    raw["equipment"]["FEET"] = raw["equipment"]["HEAD"]
    assert any("equipment.FEET" in e and "cannot go in FEET" in e for e in errors(compile_raw(raw)))


def test_unknown_item_rejected():
    raw = raw_copy(ARMS)
    raw["equipment"]["NECK"] = {"item_id": 999999999}
    assert any("not in the snapshot" in e for e in errors(compile_raw(raw)))


def test_armor_type_mask_enforced():
    raw = raw_copy("holy-priest-cloth-healer-tier4")
    raw["equipment"]["HEAD"] = load_raw(ARMS)["equipment"]["HEAD"]
    assert any("ArmorTypeMask" in e for e in errors(compile_raw(raw)))


def test_offhand_two_hander_needs_titan_grip():
    raw = raw_copy(ARMS)
    raw["equipment"]["OFFHAND"] = {"item_id": 268215, "context": 6}
    assert any("needs titan_grip" in e for e in errors(compile_raw(raw)))


def test_weapon_proficiency_enforced():
    raw = raw_copy("demonology-warlock-cloth-pet")
    raw["equipment"]["MAINHAND"] = {"item_id": 268213, "context": 6}     # two-handed axe
    raw["equipment"].pop("OFFHAND")
    assert any("PROFICIENCY_NEEDED" in e for e in errors(compile_raw(raw)))


def test_entry_from_another_class_tree_rejected():
    raw = raw_copy(ARMS)
    raw["traits"]["entries"].append(load_raw("demonology-warlock-cloth-pet")["traits"]["entries"][0])
    assert any("cannot be derived" in e for e in errors(compile_raw(raw)))


def test_unknown_trait_entry_rejected():
    raw = raw_copy(ARMS)
    raw["traits"]["entries"].append({"node_entry_id": 999999999, "rank": 1})
    assert any("not in the snapshot" in e for e in errors(compile_raw(raw)))


def test_rank_above_max_ranks_rejected():
    raw = raw_copy(ARMS)
    t = compiler().snap.traits
    for e in raw["traits"]["entries"]:
        if t["entry"][e["node_entry_id"]]["max_ranks"] == 1:
            e["rank"] = 2
            break
    assert any("IsValidEntry" in e and "> MaxRanks" in e for e in errors(compile_raw(raw)))


def test_granted_rank_is_not_double_counted():
    """The saved rank excludes granted ranks: listing a fully granted entry again is invalid (TraitMgr.cpp:832)."""
    granted = compiled(ARMS)["derived"]["traits"]["value"]["granted_entries"]
    assert granted and all(g["granted_ranks"] == 1 for g in granted)
    raw = raw_copy(ARMS)
    raw["traits"]["entries"].append({"node_entry_id": granted[0]["node_entry_id"], "rank": 1})
    assert any("rank 1 + granted 1 > MaxRanks 1" in e for e in errors(compile_raw(raw)))


def test_hero_subtree_not_selectable_by_spec():
    raw = raw_copy(ARMS)
    raw["traits"]["hero_subtree_id"] = 61     # Mountain Thane: Fury/Protection only
    assert any("hero subtree 61" in e for e in errors(compile_raw(raw)))


def test_level_90_build_is_over_budget_at_80():
    from character_prep.report import starter_traits
    entries, hero, _, _ = starter_traits(compiler(), 1, 71, 90)
    raw = raw_copy(ARMS)
    raw["identity"]["level"] = 80
    raw["traits"]["entries"] = entries
    raw["traits"]["hero_subtree_id"] = hero
    errs = errors(compile_raw(raw))
    assert any("spent" in e and "owned" in e for e in errs) or any("NodeMeetsTraitConditions" in e for e in errs)


def test_pvp_talents_listed_but_disabled_warn():
    raw = raw_copy(ARMS)
    raw["options"]["enable_pvp_talents"] = False
    out = compile_raw(raw)
    assert any("enable_pvp_talents is false" in w for w in out["warnings"])
    assert not any(r["kind"] == "pvp-talent" for a in out["derived"]["spells"]["value"]["acquired"] for r in a["roots"])


# -- trait engine / acquisition ports ---------------------------------------------------------------------------------

def test_one_trait_tree_per_class():
    snap = compiler().snap
    expected = {1: 850, 2: 790, 3: 774, 4: 852, 5: 795, 6: 750, 7: 786, 8: 658, 9: 720, 10: 1000, 11: 793, 12: 854, 13: 872}
    assert {c: snap.class_trait_trees(c)[0] for c in expected} == {c: [t] for c, t in expected.items()}


def test_race_bits_match_trinity():
    snap = compiler().snap
    assert {r: snap.race_bits[r] for r in TRINITY_RACE_BITS} == TRINITY_RACE_BITS


def test_owned_trait_currency_by_level():
    t = compiler().snap.traits
    own80 = TraitEngine(t, 71, 1, 80, [850]).owned_currencies([])
    own90 = TraitEngine(t, 71, 1, 90, [850]).owned_currencies([])
    assert {k: v["amount"] for k, v in own80.items()} == {2801: 31, 2800: 30, 2986: 10, 2987: 10, 2988: 10}
    assert {k: v["amount"] for k, v in own90.items()} == {2801: 34, 2800: 34, 2986: 13, 2987: 13, 2988: 13}
    assert not any(v["undecided_sources"] for v in own80.values())


def test_hero_entry_nodes_granted_from_level_71():
    t = compiler().snap.traits
    g70 = {g["entry"] for g in TraitEngine(t, 71, 1, 70, [850]).granted_entries()}
    g80 = {g["entry"] for g in TraitEngine(t, 71, 1, 80, [850]).granted_entries()}
    assert g70 == {112184, 114643}
    assert g80 == g70 | {117411, 117415}


def test_inactive_hero_granted_entries_are_not_applied():
    v = compiled(ARMS)["derived"]["traits"]["value"]
    assert v["hero_subtree"]["active"] and v["active_subtree_ids"] == [60]
    assert [x["subtree_id"] for x in v["not_applied_inactive_subtree"]] == [62]


def test_default_skills_dual_wield_and_mastery():
    snap = compiler().snap
    rogue = {s["spell_id"] for s in snap.default_skill_spells(4, 4, 80)["spells"]}
    warrior = {s["spell_id"] for s in snap.default_skill_spells(1, 1, 80)["spells"]}
    assert 674 in rogue and 674 not in warrior
    assert 114585 in rogue and 114585 in warrior
    assert compiled("subtlety-rogue-leather-dw")["derived"]["spells"]["value"]["gates"]["dual_wield"]["by"] == [674]


@pytest.mark.parametrize("name,spell", [("bm-hunter-mail-ranged-pet", 86538), ("subtlety-rogue-leather-dw", 86092),
                                        ("demonology-warlock-cloth-pet", 86091), ("holy-priest-cloth-healer-tier4", 89745),
                                        ("arms-warrior-plate-2h", 86101)])
def test_armor_specialization_from_whole_acquired_set(name, spell):
    a = compiled(name)["derived"]["armor_specialization"]["value"]
    assert a["applied_spell_id"] == spell
    via_spec = {c["spell_id"]: c["via_specialization_spells"] for c in a["candidates"]}
    assert via_spec[spell] is (name == "arms-warrior-plate-2h")
    assert compiled(name)["derived"]["stats"]["value"]["armor_specialization_applied"]["spell_id"] == spell


def test_caster_aura_state_classification():
    priest = {a["spell_id"]: a["initial_aura"] for a in compiled("holy-priest-cloth-healer-tier4")["derived"]["spells"]["value"]["acquired"]}
    fury = {a["spell_id"]: a["initial_aura"] for a in compiled("fury-warrior-plate-titan-grip")["derived"]["spells"]["value"]["acquired"]}
    assert priest[373456] == "active-after-first-update"
    assert fury[76856] == "gated:caster-aura-state"


def test_initial_powers():
    rage = compiled(ARMS)["derived"]["initial_state"]["value"]["powers"]["Rage"]
    rp = compiled("frost-dk-plate-dw-tier4")["derived"]["initial_state"]["value"]["powers"]["RunicPower"]
    assert rage["max_base"] == 1000 and rage["initial_on_init_stats_for_level"].startswith("clamped")
    assert rp["initial_on_init_stats_for_level"] == "0"
    assert compiled("frost-dk-plate-dw-tier4")["derived"]["initial_state"]["value"]["runes"]["max"] == 6


def test_weapon_hook_degrades_or_resolves():
    w = compiled("fury-warrior-plate-titan-grip")["derived"]["weapon"]
    assert set(w["value"]["gearing_weapon_facts"]) == {"MAINHAND", "OFFHAND"}
    assert w["value"]["hook"] == "ok" or w["evidence_class"] == "unresolved"
    cu = compiled("bm-hunter-mail-ranged-pet")["derived"]["controlled_units"]
    assert cu["value"]["fixture_section"]["hunter_pet"]["creature_family_id"] == 1
    if cu["value"]["hook"] != "ok":
        assert cu["evidence_class"] == "unresolved"


# -- explain / observations -----------------------------------------------------------------------------------------

def test_explain_returns_chain():
    out = explain(compiled(ARMS), "/derived/stats/value/stats/Strength")
    assert out["value"] == compiled(ARMS)["derived"]["stats"]["value"]["stats"]["Strength"]
    paths = [c["path"] for c in out["chain"]]
    assert paths[0] == "/derived/stats" and "/derived/gear" in paths and "/server_inputs/base_stats" in paths
    assert "/fixture/equipment" in paths


def test_observations_report_statuses():
    out = compiled(ARMS)
    strength = out["derived"]["stats"]["value"]["stats"]["Strength"]
    obs = [{"observer": "retail-client", "build": "12.1.0.69497", "path": "/derived/stats/value/stats/Strength", "value": strength},
           {"observer": "retail-client", "build": "12.1.0.69497", "path": "/derived/stats/value/stats/Strength",
            "value": strength + 3, "precision": 1},
           {"observer": "core", "build": "x", "path": "/derived/nope", "value": 1}]
    rep = observations_report(out, obs)
    assert [r["status"] for r in rep["rows"]] == ["match", "mismatch", "path-missing"]
    assert rep["rows"][1]["delta"] == 3.0


def test_observation_inside_fixture_is_reported():
    raw = raw_copy(ARMS)
    raw["observations"] = [{"observer": "trinity-probe", "build": "12.0.7.68453", "path": "/derived/identity/value/level",
                            "value": 90, "units": "count"}]
    rep = compile_raw(raw)["observations_report"]
    assert rep["status_counts"] == {"match": 1}


def test_index_records_level_90_gap():
    index = json.loads((FIXTURES_DIR / "index.json").read_text(encoding="utf-8"))
    gaps = {e["name"]: e["level_90_base_stat_gap"] for e in index["fixtures"]}
    assert gaps[NO_FILL] is True
    assert sum(gaps.values()) == 1
    filled = {e["name"]: e["base_stats_filled"] for e in index["fixtures"]}
    assert sum(filled.values()) == len(filled) - 1 and not filled[NO_FILL]


def test_default_skill_weapon_grants_match_track_c():
    """E's default-skill port and Track C's ``weapon_combat.weapon.default_skill_grants`` agree class by class."""
    weapon = pytest.importorskip("weapon_combat.weapon")
    from character_prep.compiler import SPELL_EFFECT_DUAL_WIELD, SPELL_EFFECT_TITAN_GRIP
    snap = compiler().snap
    theirs_by_class = weapon.default_skill_grants(snap.tables, level=80)
    race_for = {12: 10, 13: 52}
    for cls in range(1, 14):
        mine = {s["spell_id"] for s in snap.default_skill_spells(race_for.get(cls, 1), cls, 80)["spells"]
                if any(e[1] in (SPELL_EFFECT_DUAL_WIELD, SPELL_EFFECT_TITAN_GRIP) for e in snap.spell_effects.get(s["spell_id"], []))}
        theirs = {g["spell"] for g in theirs_by_class.get(str(cls), [])}
        assert mine == theirs, cls
    assert set(theirs_by_class) == {"3", "4", "12"}


# -- closeout review (G3) --------------------------------------------------------------------------------------------

def test_learned_spell_opens_the_arms_offhand_gate():
    out = compiled(ARMS_DW)
    assert out["validation"]["ok"], errors(out)
    spells = out["derived"]["spells"]["value"]
    assert spells["gates"]["dual_wield"] == {"granted": True, "by": [296087]}
    learned = [a for a in spells["acquired"] if a["spell_id"] == 296087]
    assert learned and [r["kind"] for r in learned[0]["roots"]] == ["learned-spell"]
    assert "/fixture/learned_spells" in out["derived"]["spells"]["derived_from"]
    hook = out["derived"]["weapon"]["value"].get("result")
    if hook:
        assert hook["capability"]["dual_wield"] is True and not hook["equip_state"]["equip_errors"]


def test_without_learned_spell_arms_cannot_dual_wield():
    raw = raw_copy(ARMS_DW)
    raw.pop("learned_spells")
    errs = errors(compile_raw(raw))
    assert any(e.startswith("equipment.OFFHAND") and "needs dual_wield" in e for e in errs)


def test_unknown_learned_spell_rejected_and_redundant_warned():
    raw = raw_copy(ARMS)
    raw["learned_spells"] = [999999999]
    assert any("learned_spells: spell 999999999" in e for e in errors(compile_raw(raw)))
    raw["learned_spells"] = [114585]
    out = compile_raw(raw)
    assert out["validation"]["ok"] and any("already acquired automatically" in w for w in out["warnings"])


def test_weapon_offhand_needs_dual_wield():
    """INVTYPE_WEAPONOFFHAND (22) needs CanDualWield unless ITEM_FLAG3_ALWAYS_ALLOW_DUAL_WIELD (Player.cpp:10860-10862)."""
    snap = compiler().snap
    # INVTYPE_WEAPONOFFHAND is rare in current data (one ExpansionID-10 row, none in 11): the gate is kept for completeness
    ids = sorted(r[0] for r in snap.source.project("ItemSparse", ("ID", "InventoryType", "Flags_2"))
                 if r[1] == 22 and not int(r[2]) & 0x80000)
    raw = raw_copy("holy-priest-cloth-healer-tier4")
    raw["equipment"]["OFFHAND"] = {"item_id": ids[0]}
    errs = errors(compile_raw(raw))
    assert any(e.startswith("equipment.OFFHAND") and "needs dual_wield" in e and "10861" in e for e in errs), errs


def test_offhand_blocked_while_two_hand_used():
    raw = raw_copy(ARMS)
    raw["equipment"]["OFFHAND"] = load_raw("demonology-warlock-cloth-pet")["equipment"]["OFFHAND"]    # held in off-hand
    errs = errors(compile_raw(raw))
    assert any("IsTwoHandUsed" in e for e in errs), errs


def test_allowable_class_and_unique_equip_enforced():
    raw = raw_copy(ARMS)
    raw["equipment"]["HEAD"] = load_raw("frost-dk-plate-dw-tier4")["equipment"]["HEAD"]           # DK tier piece
    raw["equipment"]["FINGER2"] = dict(raw["equipment"]["FINGER1"])
    errs = errors(compile_raw(raw))
    assert any(e.startswith("equipment.HEAD") and "AllowableClass" in e for e in errs), errs
    assert any(e.startswith("equipment.FINGER2") and "unique-equippable" in e for e in errs), errs


def test_max_charges_include_passive_aura_bonus():
    charges = {c["spell_id"]: c for c in compiled(ARMS)["derived"]["initial_state"]["value"]["charges"]["spells_with_charges"]}
    overpower = charges[7384]
    assert [b["spell_id"] for b in overpower["aura_bonus"]] == [385571]
    assert overpower["max_charges"] == overpower["category_max_charges"] + 1
    assert overpower["evidence_class"] == "structural-inference"


def test_acquire_method_4_reason_names_the_condition():
    undecided = {u["spell_id"]: u for u in compiler().snap.default_skill_spells(1, 1, 90)["undecided"]}
    assert undecided[54197]["show_future_spell_player_condition_id"] == 0 and "no row" in undecided[54197]["why"]
    assert undecided[163201]["show_future_spell_player_condition_id"] == 83446


def test_health_at_start_is_a_convention():
    health = compiled(ARMS)["derived"]["initial_state"]["value"]["health"]
    assert health["evidence_class"] == "structural-inference" and "convention" in health["rule"]


def test_compiled_output_embeds_no_host_path(tmp_path):
    path = tmp_path / "copy.json"
    path.write_text(json.dumps(load_raw(ARMS)), encoding="utf-8")
    from character_prep.fixture import Fixture
    out = compiler().compile(Fixture.from_file(path))
    text = canonical_json(out)
    assert str(tmp_path) not in text and out["provenance"]["fixture_path"] == "copy.json"


@pytest.mark.parametrize("name", fixture_names())
def test_weapon_hook_and_compiler_agree_on_dual_wield(name):
    out = compiled(name)
    hook = out["derived"]["weapon"]["value"].get("result")
    if not hook:
        pytest.skip("weapon hook unavailable")
    assert hook["capability"]["dual_wield"] is out["derived"]["spells"]["value"]["gates"]["dual_wield"]["granted"]
