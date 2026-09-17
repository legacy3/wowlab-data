"""Track E: script target adapter inventory invariants and runtime adapters."""

from __future__ import annotations

import json
import struct
from types import SimpleNamespace

import pytest

from targeting import CORPORA, FailClosed
from targeting import adapters as A
from targeting.fixture import World
from targeting.trace import Trace


@pytest.fixture(scope="module")
def inv(tg_ctx):
    return A.build_inventory(tg_ctx)


def test_every_in_scope_registration_has_a_read_verdict(inv):
    """build_inventory fails closed on a registration without a hand-read verdict; every verdict is used."""
    assert {(r["class"], r["handler"]) for r in inv.adapters} == set(A.VERDICTS)
    loaded = {(r["class"], r["handler"]) for r in inv.helpers}
    not_loaded = {(r["class"], r["handler"]) for r in inv.not_loaded_helpers}
    assert loaded | not_loaded == set(A.HELPER_VERDICTS)
    for v in A.VERDICTS.values():
        assert v["family"] and v["summary"]


def test_inventory_counts(inv):
    rows = inv.adapters
    assert len(rows) == 69
    assert sum(r["executes"] for r in rows) == 58
    assert sum(r["effective_executes"] for r in rows) == 53
    assert sum(r["registration_state"] != "not-registered" for r in rows) == 67
    assert {(r["spell"], r["target"]) for r in rows if r["registration_state"] == "not-registered"} == {(114852, 16), (114871, 31)}
    assert all(r["list"] != A.AURA_AREA_LIST for r in rows)       # no DoCheckAreaTarget in scope
    assert len(inv.helpers) == 128                                   # pattern-based scan: a lower bound
    assert {r["class"] for r in inv.not_loaded_helpers} == {"spell_warl_seduction", "spell_warl_devour_magic"}


def test_review_r3_reclassifications(inv):
    """R3-01/02/06: dead-by-drift writers and register-time gates are not recipient selections."""
    v = {(r["class"], r["handler"]): r["verdict"] for r in inv.helpers}
    assert v[("spell_rog_blade_flurry", "CheckProc")]["kind"] == "proc-gate+rng"
    assert not v[("spell_rog_blade_flurry", "CheckProc")]["changes_recipients"]
    assert v[("spell_rog_killing_spree_aura", "HandleEffectPeriodic")]["kind"] == "dead-by-drift"
    assert "Elemental" in v[("spell_sha_primordial_wave", "PreventLavaSurge")]["summary"]
    for key in [("spell_pri_trail_of_light", "HandleOnProc"), ("spell_sha_earthen_rage_proc_aura", "HandleEffectPeriodic"),
                ("spell_pal_light_s_beacon", "HandleProc"), ("spell_warl_rain_of_fire", "HandleDummyTick")]:
        assert v[key]["kind"] == "script-target-selection", key
    assert v[("spell_pal_consecration", "HandleEffectPeriodic")]["kind"] == "dest-consumer"


def test_mechanical_validate_matches_review(tg_ctx, inv):
    classes = {(e["spell"], e["class"]) for e in inv.validate_erratum}
    for item in [(450347, "spell_dru_natures_grace"), (235450, "spell_mage_prismatic_barrier"),
                 (389579, "spell_monk_save_them_all"), (980, "spell_warl_deaths_embrace_dots"),
                 (48517, "spell_dru_eclipse_aura"), (740, "spell_dru_inner_peace")]:
        assert item in classes, item
    assert (48438, "spell_dru_wild_growth") not in classes
    from targeting.adapters import mechanical_validate
    sc = tg_ctx.bundle.index.by_name["spell_mage_flame_on"][0]
    assert not mechanical_validate(tg_ctx, sc, 205029)["ok"]      # EFFECT_2 missing (outside the executing-hook set)


def test_validate_failures_are_detected(inv):
    """Structural 'executes' is not 'loaded': Validate() predicates on the 12.1 rows."""
    by = {(r["spell"], r["class"]): r["validate"] for r in inv.adapters}
    iv = by[(740, "spell_dru_inner_peace")]
    assert (iv["validate_ok_12_1"], iv["validate_ok_12_0_7"], iv["validate_spells_missing_12_1"]) == (False, True, [])
    assert not by[(184362, "spell_warr_powerful_enrage")]["validate_ok_12_1"]
    assert not by[(184362, "spell_warr_powerful_enrage")]["validate_ok_12_0_7"]
    assert by[(184362, "spell_warr_frenzied_enrage")]["validate_ok_12_1"]
    assert not by[(357209, "spell_evo_fire_breath_damage")]["validate_ok_12_1"]
    assert by[(357209, "spell_evo_scouring_flame")]["validate_ok_12_1"]


def test_template_instantiations_are_distinct_functions(inv):
    fns = {r["eff_index"]: r["function"] for r in inv.adapters if r["class"] == "spell_dh_enduring_torment_buff"}
    assert fns[0] == fns[1] != fns[2] == fns[3]


def test_holy_prism_masks_depend_on_the_bound_spell(inv):
    m = {(r["spell"], r["handler"], r["target_token"]): r["mask"] for r in inv.adapters if r["class"] == "spell_pal_holy_prism_selector"}
    assert m[(114871, "FilterTargets", "TARGET_UNIT_DEST_AREA_ALLY")] == 0
    assert m[(114871, "FilterTargets", "TARGET_UNIT_DEST_AREA_ENEMY")] == 2
    assert m[(114871, "ShareTargets", "TARGET_UNIT_DEST_AREA_ENTRY")] == 0
    assert m[(114852, "ShareTargets", "TARGET_UNIT_DEST_AREA_ENTRY")] == 4


def test_committed_corpus_matches_generator(tg_ctx):
    path = CORPORA / "script-adapters.json"
    if not path.exists():
        pytest.skip("corpus not generated")
    committed = json.loads(path.read_text())
    fresh = json.loads(json.dumps(A.corpus(tg_ctx, committed["provenance"]["command"]), sort_keys=True))
    assert fresh["totals"] == committed["totals"]
    assert fresh["effect_classes"] == committed["effect_classes"]


def test_effect_classes_are_reach_only_and_well_formed(tg_ctx):
    path = CORPORA / "script-adapters.json"
    if not path.exists():
        pytest.skip("corpus not generated")
    doc = json.loads(path.read_text())
    reach = tg_ctx.scope.reach
    for key, row in doc["effect_classes"].items():
        spell, eff = map(int, key.split(":"))
        assert spell in reach
        assert row["class"] in ("understood", "understood-with-defect", "fixture-dependent", "blocked", "unresolved")
        assert "script-adapter" in row["tags"] and isinstance(row["build_skew"], bool)
    for row in doc["effect_classes_extended"].values():
        assert row["tier"] in ("controlled-unit", "script-reach")


# -- runtime adapters (synthetic) ---------------------------------------------------------------

def _world(actors, explicit=None, draws=None, visit=None):
    return World.from_dict({
        "schema": "targeting-fixture/1", "name": "e", "spell": {"id": 0}, "caster": "caster",
        "explicit": {"unit": explicit} if explicit else {},
        "actors": [{"id": "caster", "kind": "player"}] + actors,
        "visit_order": visit, "rng": {"draws": draws or []},
    })


SEED = 27243


def test_seed_prefers_unseeded_explicit_target():
    """Rules out 'random among all candidates': explicit target without caster's Seed wins with no draws."""
    w = _world([{"id": "t1", "kind": "creature"}, {"id": "t2", "kind": "creature"}], explicit="t1")
    assert A.seed_of_corruption_select(w, ["t2", "t1"], SEED, Trace()) == ["t1"]
    assert w.draws_consumed == 0


def test_seed_rerolls_to_unseeded_candidate():
    """Rules out 'always explicit target' and 'other caster's Seed counts'."""
    w = _world([{"id": "t1", "kind": "creature", "auras": [{"spell": SEED, "caster": "caster"}]},
                {"id": "t2", "kind": "creature", "auras": [{"spell": SEED, "caster": "other"}]},
                {"id": "t3", "kind": "creature"}], explicit="t1", draws=[2, 1])
    # t1 removed (own seed); t2 kept (other caster's seed); RandomResize([t2,t3],1): urand(1,2)=2 -> drop t2; urand(1,1)=1 -> keep t3
    assert A.seed_of_corruption_select(w, ["t1", "t2", "t3"], SEED, Trace()) == ["t3"]
    assert w.draws_consumed == 2


def test_seed_falls_back_to_explicit_when_all_seeded():
    w = _world([{"id": "t1", "kind": "creature", "auras": [{"spell": SEED, "caster": "caster"}]},
                {"id": "t2", "kind": "creature", "auras": [{"spell": SEED, "caster": "caster"}]}], explicit="t1")
    assert A.seed_of_corruption_select(w, ["t1", "t2"], SEED, Trace()) == ["t1"]


def test_seed_without_explicit_unit_fails_closed():
    w = _world([{"id": "t1", "kind": "creature"}, {"id": "t2", "kind": "creature"}])
    with pytest.raises(FailClosed):
        A.seed_of_corruption_select(w, ["t1", "t2"], SEED, Trace())


def test_keep_with_caster_aura_keeps_non_units():
    """UnitAuraCheck(WorldObject*) is false for non-units, so remove_if keeps them (GridNotifiers.h:2018)."""
    w = _world([{"id": "u1", "kind": "creature", "auras": [{"spell": 348, "caster": "caster"}]},
                {"id": "u2", "kind": "creature", "auras": [{"spell": 348, "caster": "other"}]},
                {"id": "go", "kind": "gameobject"}])
    assert A.keep_with_caster_aura(w, ["u1", "u2", "go"], 348, Trace()) == ["u1", "go"]


def test_path_of_flames_removes_explicit_and_filters_by_absence():
    w = _world([{"id": "e", "kind": "creature"},
                {"id": "a", "kind": "creature", "auras": [{"spell": 188389, "caster": "caster"}]},
                {"id": "b", "kind": "creature"}, {"id": "go", "kind": "gameobject"}], explicit="e")
    # filtered = [b] (a has caster FS, go is not a unit, e is explicit) -> size 1 <= 1: no draws
    assert A.path_of_flames_select(w, ["e", "a", "b", "go"], 188389, Trace()) == ["b"]
    assert w.draws_consumed == 0


def test_explicit_only_and_remove_explicit():
    w = _world([{"id": "e", "kind": "creature"}, {"id": "x", "kind": "creature"}], explicit="e")
    assert A.explicit_only(w, ["x"], Trace()) == ["e"]                 # inserted even if the search missed it
    assert A.remove_explicit(w, ["e", "x", "e"], Trace()) == ["x"]      # list::remove drops every copy
    w2 = _world([{"id": "x", "kind": "creature"}])
    assert A.explicit_only(w2, ["x"], Trace()) == []


def test_random_cap_draw_count():
    w = _world([], draws=[1, 2, 1])
    assert A.random_cap(w, ["a", "b", "c"], 2, Trace()) == ["a", "c"]
    assert w.draws_consumed == 3


def test_suppress_and_clear():
    w = _world([])
    assert A.suppress_object(w, "t", True, Trace()) is None
    assert A.suppress_object(w, "t", False, Trace()) == "t"
    assert A.clear_area(w, ["a"], True, Trace()) == [] and A.clear_area(w, ["a"], False, Trace()) == ["a"]


def test_dest_offset_is_binary32_z_shift():
    out = A.dest_offset((1.0, 2.0, 0.1, 0.5), (0.0, 0.0, 5.0), Trace())
    f32 = lambda v: struct.unpack("f", struct.pack("f", v))[0]  # noqa: E731
    assert out[2] == f32(f32(0.1) + f32(5.0))
    with pytest.raises(FailClosed):
        A.dest_offset((0.0, 0.0, 0.0, 0.0), (1.0, 0.0, 0.0), Trace())


# -- hook runner ---------------------------------------------------------------------------------

def _hook(script, handler, mask=1, lst="OnObjectAreaTargetSelect", target=16):
    return {"list": lst, "target": target, "affected_mask": mask, "script": script, "handler": handler}


def test_runner_respects_load_gate():
    """Storm Bolt's Load() rejects the script when the caster HAS Storm Bolts: list unchanged."""
    eff = SimpleNamespace(index=0)
    w = _world([{"id": "e", "kind": "creature"}, {"id": "x", "kind": "creature"}], explicit="e")
    hooks = [_hook("spell_warr_storm_bolts", "FilterTargets")]
    assert A.run_target_hook(w, None, eff, 16, "OnObjectAreaTargetSelect", hooks, ["x"], Trace()) == ["e"]
    w2 = World.from_dict({"schema": "targeting-fixture/1", "caster": "caster", "explicit": {"unit": "e"},
                          "actors": [{"id": "caster", "kind": "player", "auras": [{"spell": A.A_STORM_BOLTS, "caster": "caster"}]},
                                     {"id": "e", "kind": "creature"}, {"id": "x", "kind": "creature"}]})
    assert A.run_target_hook(w2, None, eff, 16, "OnObjectAreaTargetSelect", hooks, ["x"], Trace()) == ["x"]


def test_runner_skips_validate_failures_and_fails_closed_on_unmodelled():
    eff = SimpleNamespace(index=4)
    w = _world([])
    hooks = [_hook("spell_dru_inner_peace", "PreventEffect", 16, "OnObjectTargetSelect", 1)]
    assert A.run_target_hook(w, None, eff, 1, "OnObjectTargetSelect", hooks, "caster", Trace()) == "caster"
    with pytest.raises(FailClosed):
        A.run_target_hook(w, None, SimpleNamespace(index=0), 16, "OnObjectAreaTargetSelect",
                          [_hook("spell_pri_purge_the_wicked_dummy", "FilterTargets")], [], Trace())


def test_runner_enduring_torment_uses_effect_index_for_the_template_argument():
    w = World.from_dict({"schema": "targeting-fixture/1", "caster": "caster",
                         "actors": [{"id": "caster", "kind": "player", "facts": {"primary_specialization": 577}}]})
    hook = _hook("spell_dh_enduring_torment_buff", "PreventEffect", 0b1111, "OnObjectTargetSelect", 1)
    run = lambda i: A.run_target_hook(w, None, SimpleNamespace(index=i), 1, "OnObjectTargetSelect", [hook], "caster", Trace())  # noqa: E731
    assert [run(i) for i in range(4)] == ["caster", "caster", None, None]


def test_every_modelled_hook_is_an_inventoried_function():
    classes = {k for k in A.VERDICTS}
    for script, handler in A.HOOK_ADAPTERS:
        assert (A._SCRIPT_CLASS.get(script, script), handler) in classes, (script, handler)


def test_witness_proposals_evaluate_through_the_pipeline(tg_ctx):
    """The track-E witness fixtures (bag handoff) evaluate to their recorded recipients with the hook runner."""
    from targeting import ROOT
    path = ROOT.parents[1] / "bag" / "targeting-pass" / "witnesses" / "E.json"   # workspace scratch (pallet/../bag)
    if not path.exists():
        pytest.skip("witness proposals not present on this machine")
    pipeline = pytest.importorskip("targeting.pipeline")
    for w in json.loads(path.read_text()):
        world = World.from_dict(w["fixture"])
        res = pipeline.evaluate(world) if hasattr(pipeline, "evaluate") else None
        if res is None:
            pytest.skip("pipeline.evaluate not available")
        rec = res["recipients"] if isinstance(res, dict) else res.recipients
        assert {str(k): v for k, v in rec.items()} == w["expected_recipients"], w["fixture"]["name"]
