"""Track G: the executable SelectSpellTargets oracle, its SpellView and the fixture library.

Every synthetic test states the competing model it rules out.  Run from ``scripts/research``::

    uv run --with pytest==8.4.2 --with hypothesis==6.140.3 python -m pytest tests/test_tg_g_*.py -q -p no:cacheprovider
"""

from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

from targeting import FailClosed, oracle
from targeting.cmd_g import check_library, library
from targeting.fixture import World
from targeting.pipeline import Evaluation, evaluate
from targeting.trace import Trace

RESEARCH = Path(__file__).resolve().parents[1]


def _load(name: str) -> dict:
    return json.loads((RESEARCH / "targeting" / "fixtures" / f"{name}.json").read_text(encoding="utf-8"))


def _run(data: dict):
    return evaluate(World.from_dict(copy.deepcopy(data)))


# ---------------------------------------------------------------------------
# the curated library
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("path", library(), ids=lambda p: p.stem)
def test_fixture_library(path: Path) -> None:
    """Each curated fixture reproduces its stated expectation (recipients, masks, draws, dests, or FailClosed)."""
    data = json.loads(path.read_text(encoding="utf-8"))
    exp = data["expect"]
    assert data.get("discriminates"), "every library fixture must say which models it separates"
    if exp.get("fail_closed"):
        with pytest.raises(FailClosed, match=exp["fail_closed"]):
            _run(data)
        return
    res = _run(data).to_json(with_trace=False)
    for key in ("recipients", "unique_targets", "cast_result", "draws_consumed", "dests"):
        if key in exp:
            assert res[key] == exp[key], key


def test_library_summary_all_pass() -> None:
    out = check_library()
    assert out["total"] >= 10
    assert out["passed"] == out["total"], [r for r in out["fixtures"] if not r["ok"]]


def test_library_is_byte_stable_json() -> None:
    """Fixture files are sorted-key or generator-stable JSON (no timestamps, no absolute paths)."""
    for path in library():
        text = path.read_text(encoding="utf-8")
        assert "/home/" not in text and text.endswith("\n"), path.name
        json.loads(text)


# ---------------------------------------------------------------------------
# pipeline-level discriminations
# ---------------------------------------------------------------------------
def test_grouped_effects_share_one_draw_set() -> None:
    """Rules out 'each effect runs its own RandomResize': 3 draws total and identical subsets."""
    data = _load("grouped-effects-share-random-resize")
    res = _run(data)
    assert res.draws_consumed == 3
    assert res.recipients[0] == res.recipients[1] == ["t2", "t3"]
    split = _load("radius-split-draws-twice")
    assert _run(split).draws_consumed == 6   # the radius split re-runs the search and the cap


def test_merge_keeps_first_insertion_position() -> None:
    """Rules out 'recipients in the effect's own search order': effect 1 lists t1 before t2 although
    the area visit order is [t2, ..., t1], because t1 was inserted by effect 0 (Spell.cpp:2464-2470)."""
    data = _load("merge-duplicate-recipient")
    assert data["visit_order"].index("t2") < data["visit_order"].index("t1")
    assert _run(data).recipients[1] == ["t1", "t2"]


def test_require_all_targets_counts_only_the_current_turn() -> None:
    """Rules out 'any unique target satisfies REQUIRE_ALL_TARGETS'."""
    data = _load("require-all-targets-per-turn")
    res = _run(data)
    assert res.unique_targets == [{"id": "caster", "mask": 1}]
    assert res.cast_result == "SPELL_FAILED_BAD_IMPLICIT_TARGETS"
    data["spell"]["attributes"] = []
    assert _run(data).cast_result == "SPELL_CAST_OK"


def test_hit_rolls_interleave_with_later_selection() -> None:
    """Rules out 'all selection RNG before any hit RNG': with the SpellHitResult draw of t1 consumed
    first, effect 1's RandomResize keeps t2 (stream [1,2,1,1]); read without the hit draw the same
    stream would start the resize with urand(1,2)=1 and keep t1."""
    data = _load("hit-draws-interleave-with-cap")
    res = _run(data)
    assert res.recipients[1] == ["t2"] and res.draws == [1, 2, 1, 1]
    no_hit = copy.deepcopy(data)
    no_hit["hit"] = {"draws": 0}
    no_hit["rng"]["draws"] = [1, 1]
    assert _run(no_hit).recipients[1] == ["t1"]


def test_selection_rng_after_add_needs_hit_model() -> None:
    """Without `hit.draws` the oracle refuses to guess the interleaving (Spell.cpp:2486)."""
    data = _load("hit-draws-interleave-with-cap")
    del data["hit"]
    with pytest.raises(FailClosed, match="hit.draws"):
        _run(data)


def test_immune_target_is_inserted_with_empty_mask() -> None:
    """Rules out 'immune targets are never inserted' (Spell.cpp:2450 precedes 2457-2459)."""
    res = _run(_load("immune-target-kept-with-empty-mask"))
    assert res.unique_targets[0] == {"id": "t1", "mask": 0}
    assert res.recipients[0] == ["t2"]


def test_per_effect_dest_snapshot() -> None:
    """Rules out 'one final m_targets dst for every effect' (AddDestTarget per effect, Spell.cpp:799-800)."""
    res = _run(_load("per-effect-dest-snapshot"))
    assert res.dests[0][:2] == [0.0, 0.0] and res.dests[1][:2] == [10.0, 5.0]


def test_targetb_uses_targeta_dest_not_caster() -> None:
    """Rules out 'DEST area centred on the caster': the near-caster enemy t3 is not selected."""
    res = _run(_load("targetb-area-around-targeta-dest"))
    assert "t3" not in res.recipients[0]
    assert set(res.recipients[0]) == {"t1", "t2"}


def test_effect_type_fallback_adds_caster() -> None:
    """Rules out 'no selector -> no recipient' for EFFECT_IMPLICIT_TARGET_EXPLICIT effects."""
    res = _run(_load("effect-type-caster-fallback"))
    assert res.recipients == {0: ["caster"]}


def test_missing_visit_order_fails_closed() -> None:
    with pytest.raises(FailClosed, match="visit_order"):
        _run(_load("fail-closed-missing-visit-order"))


def test_stated_redirect_is_applied() -> None:
    """A stated redirect replaces the explicit unit before any selector reads it (Spell.cpp:715)."""
    data = _load("merge-duplicate-recipient")
    data["explicit"]["redirect"] = "t2"
    res = _run(data)
    assert res.recipients[0] == ["t2"]


def test_unstated_redirect_uses_track_b() -> None:
    """Without a stated outcome the redirect runs through explicit.select_explicit_targets (magnet facts)."""
    data = _load("merge-duplicate-recipient")
    del data["explicit"]["redirect"]
    for a in data["actors"]:
        a["facts"]["magnet_auras"] = []
    t = Trace()
    ev = Evaluation(World.from_dict(data), trace=t)
    res = ev.run()
    assert res.recipients[0] == ["t1"]
    assert any(s.name == "explicit.redirect" and s.mirrors == "Spell.cpp:691" for s in t.stages)


def test_script_target_hook_fails_closed_or_uses_stated_result() -> None:
    """Rules out 'skip unknown script hooks': a bound OnObjectAreaTargetSelect on the lead effect
    needs an adapter or a stated post-hook list."""
    data = _load("grouped-effects-share-random-resize")
    data["spell"]["script_hooks"] = [{"list": "OnObjectAreaTargetSelect", "target": "TARGET_UNIT_SRC_AREA_ENEMY",
                                      "affected_mask": 3, "script": "spell_x", "handler": "Filter"}]
    with pytest.raises(FailClosed, match="script target hook"):
        _run(data)
    data["script_results"] = {"OnObjectAreaTargetSelect:0:15": ["t3"]}
    with pytest.raises(FailClosed, match="R2-05"):     # a stated hook result must state its RNG use
        _run(data)
    data["script_results"] = {"OnObjectAreaTargetSelect:0:15": {"result": ["t3"], "rng_free": True}}
    res = _run(data)
    assert res.recipients == {0: ["t3"], 1: ["t3"]}
    assert res.draws_consumed == 0   # one candidate <= cap: RandomResize draws nothing


def test_hook_on_non_lead_effect_splits_group() -> None:
    """A hook bound only to effect 1 fails CheckScriptEffectImplicitTargets (Spell.cpp:9066): two turns."""
    data = _load("grouped-effects-share-random-resize")
    data["spell"]["script_hooks"] = [{"list": "OnObjectAreaTargetSelect", "target": "TARGET_UNIT_SRC_AREA_ENEMY",
                                      "affected_mask": 2, "script": "spell_x", "handler": "Filter"}]
    data["script_results"] = {"OnObjectAreaTargetSelect:1:15": {"result": ["t1"], "rng_free": True}}
    data["hit"] = {"draws": 0}
    res = _run(data)
    assert res.recipients[1] == ["t1"] and res.draws_consumed == 3


# ---------------------------------------------------------------------------
# SpellView and shared arithmetic
# ---------------------------------------------------------------------------
def _world(**over) -> World:
    base = {"schema": "targeting-fixture/1", "name": "w", "spell": {}, "caster": "c",
            "actors": [{"id": "c", "kind": "player", "pos": [0, 0, 0], "orientation": 0.0,
                        "facts": {"level": 80, "range_movement_bonus": False}},
                       {"id": "g", "kind": "guardian", "pos": [0, 0, 0], "orientation": 0.0, "owner": "c",
                        "facts": {"level": 80, "range_movement_bonus": False}}],
            "modifiers": {"radius": {"flat": 2, "pct": 1.5}}}
    base.update(over)
    return World.from_dict(base)


def _sv(targets=("TARGET_UNIT_SRC_AREA_ENEMY", 0), radius=None, attrs=()):
    return oracle.from_fixture({"synthetic": True, "range": None, "attributes": list(attrs), "effects": [
        {"index": 0, "effect": "SCHOOL_DAMAGE", "target_a": targets[0], "target_b": targets[1],
         "radius_a": radius or {"radius": 5, "per_level": 0.5, "min": 0, "max": 30}}]})


def test_calc_radius_level_mods_and_owner() -> None:
    """min(Radius + PerLevel*level, Max), then ApplySpellMod ((base + int flat) * pct) for a mod owner only."""
    w = _world()
    sv = _sv()
    assert oracle.calc_radius(w, sv, sv.effect(0), "A", "c") == (0.0, oracle.f32((30.0 + 2) * 1.5))
    # guardians are not pets: no spell-mod owner (Object.cpp:1648-1660)
    assert oracle.calc_radius(w, sv, sv.effect(0), "A", "g") == (0.0, 30.0)
    # CalcRadius(nullptr): RadiusMax only (Spell.cpp:1673)
    assert oracle.calc_radius(w, sv, sv.effect(0), "A", None) == (0.0, 30.0)


def test_calc_radius_level_needed_only_with_per_level() -> None:
    w = _world(actors=[{"id": "c", "kind": "player", "pos": [0, 0, 0], "orientation": 0.0,
                        "facts": {"range_movement_bonus": False}}], modifiers={"radius": 0})
    flat = _sv(radius={"radius": 5, "per_level": 0, "min": 0, "max": 30})
    assert oracle.calc_radius(w, flat, flat.effect(0), "A", "c") == (0.0, 5.0)
    with pytest.raises(FailClosed, match="level"):
        sv = _sv()
        oracle.calc_radius(w, sv, sv.effect(0), "A", "c")


def test_calc_radius_movement_bonus() -> None:
    """+2/-2 when moving (Spell::CanIncreaseRangeByMovement) unless ATTR9_NO_MOVEMENT_RADIUS_BONUS."""
    w = _world(actors=[{"id": "c", "kind": "player", "pos": [0, 0, 0], "orientation": 0.0,
                        "facts": {"level": 80, "range_movement_bonus": True}}], modifiers={"radius": 0})
    sv = _sv(radius={"radius": 8, "per_level": 0, "min": 1, "max": 8})
    assert oracle.calc_radius(w, sv, sv.effect(0), "A", "c") == (0.0, 10.0)
    sv2 = _sv(radius={"radius": 8, "per_level": 0, "min": 1, "max": 8}, attrs=["SPELL_ATTR9_NO_MOVEMENT_RADIUS_BONUS"])
    assert oracle.calc_radius(w, sv2, sv2.effect(0), "A", "c") == (1.0, 8.0)


def test_calc_radius_random_consumes_a_float_draw() -> None:
    """*_RANDOM: Max = (Max - Min) * sqrt(rand_norm) -- Min is not added back (SpellInfo.cpp:825-827)."""
    w = _world(rng={"draws": [0.25]}, modifiers={"radius": 0})
    sv = _sv(targets=("TARGET_DEST_CASTER_RANDOM", 0), radius={"radius": 10, "per_level": 0, "min": 4, "max": 10})
    assert oracle.calc_radius(w, sv, sv.effect(0), "A", "c") == (4.0, 3.0)
    assert w.draws_consumed == 1


def test_target_b_radius_falls_back_to_a() -> None:
    """CalcRadius(TargetB) uses TargetA's entry (and TargetA's target id) when B has none (SpellInfo.cpp:790)."""
    w = _world(modifiers={"radius": 0})
    sv = _sv(targets=("TARGET_DEST_CASTER", "TARGET_UNIT_DEST_AREA_ENEMY"),
             radius={"radius": 7, "per_level": 0, "min": 0, "max": 7})
    assert oracle.calc_radius(w, sv, sv.effect(0), "B", "c") == (0.0, 7.0)


def test_unstated_mod_fails_closed() -> None:
    w = _world(modifiers={})
    sv = _sv()
    with pytest.raises(FailClosed, match="modifiers.radius"):
        oracle.calc_radius(w, sv, sv.effect(0), "A", "c")


def test_real_view_positivity_and_cu(tg_ctx) -> None:
    sv = oracle.from_data(1064)
    assert sv.name == "Chain Heal" and sv.effects[0].target_a == 45 and sv.effects[0].chain_targets == 4
    assert sv.range is not None and sv.range.max == (40.0, 40.0)
    assert sv.is_positive is True                     # targeting.positivity (load-time port)
    with pytest.raises(FailClosed):
        _ = oracle.from_data(190411).is_positive      # load-order dependent in Trinity -> unknown
    with pytest.raises(FailClosed):
        sv.has_cu("SPELL_ATTR0_CU_ENCHANT_PROC")      # not a bit the view models
    assert sv.has_cu("SPELL_ATTR0_CU_CONE_BACK") is False


def test_corrected_spell_fails_closed(tg_ctx) -> None:
    """Rules out 'DB2 row == SpellInfo': a LoadSpellInfoCorrections target is refused (38310 Multi-Shot cap fix)."""
    with pytest.raises(FailClosed, match="LoadSpellInfoCorrections"):
        oracle.from_data(38310)


def test_unknown_spell_fails_closed(tg_ctx) -> None:
    with pytest.raises(FailClosed):
        oracle.from_data(999999999)


def test_lazy_group_masks_match_track_f_plan(tg_ctx) -> None:
    """The pipeline's per-lead grouping equals track F's up-front selection_plan on every current-player
    spell with a deterministic radius (the two differ only in when RNG-consuming radii draw)."""
    from targeting.recipients import script_effect_check, selection_plan
    w = _world(modifiers={"radius": 0})
    checked = 0
    for spell in sorted(tg_ctx.scope.reach):
        try:
            sv = oracle.from_data(spell)
        except FailClosed:
            continue
        if not sv.effects or any(e.target_a in (72, 74, 86) or e.target_b in (72, 74, 86) for e in sv.effects):
            continue
        ev = Evaluation(w, sv)
        try:
            plan = selection_plan(sv.effects, lambda k, i, ev=ev, sv=sv: ev.radius(sv.effects[k], i),
                                  script_effect_check(sv))
            processed, lazy = 0, []
            for e in sv.effects:
                if not e.is_effect:
                    continue
                m = ev.group_mask(e) & ~processed
                processed |= m
                lazy.append((e.index, m))
        except FailClosed:
            continue
        assert lazy == [(s.effect, s.mask) for s in plan], spell
        checked += 1
    assert checked > 4000


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "targeting.py", *args], cwd=RESEARCH, capture_output=True, text=True,
                          check=False)


def test_cli_evaluate_and_fail_closed_exit_code() -> None:
    ok = _cli("evaluate", "targeting/fixtures/merge-duplicate-recipient.json")
    assert ok.returncode == 0 and json.loads(ok.stdout)["expect"]["ok"]
    bad = _cli("evaluate", "targeting/fixtures/fail-closed-missing-visit-order.json")
    assert bad.returncode == 3 and "visit_order" in bad.stderr


def test_cli_explain_lists_every_stage() -> None:
    out = _cli("explain", "targeting/fixtures/grouped-effects-share-random-resize.json")
    assert out.returncode == 0
    for token in ("group", "area.search", "RandomResize", "add_unit_target", "effect 1: ['t2', 't3']"):
        assert token in out.stdout, token


def test_effect_description_fails_soft(tg_ctx) -> None:
    from targeting.cmd_g import describe_effect
    d = describe_effect(1064, 0)
    assert d["selectors"]["A"]["handler"]["function"] == "SelectImplicitTargetObjectTargets"
    assert d["group"]["mask"] == 1
    assert describe_effect(999999999, 0)["unknown"]
    assert describe_effect(1064, 7)["unknown"]


def test_selector_finish_does_not_stop_selection() -> None:
    """Hostile review R1-04: rules out 'finish() inside a selector aborts SelectSpellTargets'.
    The nearby selector fails (Spell.cpp:1196-1200) but effect 1 still selects and consumes 3 urand draws;
    the cast result is the first finish() result (Spell.cpp:4358)."""
    res = _run(_load("nearby-finish-continues-selection"))
    assert res.cast_result == "SPELL_FAILED_BAD_IMPLICIT_TARGETS"
    assert res.draws_consumed == 3
    assert res.recipients[1] == ["t2", "t3"] and res.recipients[0] == []


def test_require_all_after_selector_finish_still_returns() -> None:
    """802-820 still `return`s from SelectSpellTargets after an earlier finish(); the first result is kept."""
    data = _load("nearby-finish-continues-selection")
    data["spell"]["attributes"] = ["SPELL_ATTR1_REQUIRE_ALL_TARGETS"]
    res = _run(data)
    assert res.cast_result == "SPELL_FAILED_BAD_IMPLICIT_TARGETS"
    assert res.draws_consumed == 0          # effect 0's turn returned from SelectSpellTargets


def test_not_loaded_script_does_not_split_group(tg_ctx) -> None:
    """Hostile review R1-07: spell_dru_inner_peace fails Validate() on 12.1 rows, so Tranquility 740
    forms one group {0,2,3,4,5,6} (Spell.cpp:741-782 with an empty m_loadedScripts)."""
    sv = oracle.from_data(740)
    w = _world(modifiers={"radius": 0})
    ev = Evaluation(w, sv)
    masks, processed = [], 0
    for e in sv.effects:
        if e.is_effect:
            m = ev.group_mask(e) & ~processed
            processed |= m
            if m:
                masks.append(m)
    assert masks[0] == 0b1111101


def test_reproduced_defects_are_flagged() -> None:
    """Hostile review R2-06: reproduced Trinity defects surface in Result.defects when they decide the outcome."""
    assert "TG-F-D1" in _run(_load("grouped-effects-inherit-lead-chain")).defects
    assert "TG-F-D3" in _run(_load("immune-target-kept-with-empty-mask")).defects
    assert "TG-F-D1" not in _run(_load("merge-duplicate-recipient")).defects
    assert "TG-F-D3" not in _run(_load("merge-duplicate-recipient")).defects


def test_random_radius_grouping_is_flagged() -> None:
    """TG-G-D01 / TG-F-D2 / TG-D-DEF-20: grouping draws rand_norm for *_RANDOM selectors."""
    data = _load("grouped-effects-share-random-resize")
    for e in data["spell"]["effects"]:
        e["target_a"] = "TARGET_DEST_CASTER_RANDOM"
        e["target_b"] = "TARGET_UNIT_DEST_AREA_ENEMY"
        e["radius_a"] = {"radius": 8, "per_level": 0, "min": 2, "max": 8}
    data["rng"]["draws"] = [0.25, 0.5]
    data["dest_positions"] = {"0:A": [1, 0, 0]}
    with pytest.raises(FailClosed):          # more draws needed later; the grouping stage ran first
        _run(data)
    from targeting.pipeline import Evaluation as Ev
    ev = Ev(World.from_dict(copy.deepcopy(data)))
    try:
        ev.run()
    except FailClosed:
        pass
    assert any(s.defect == "TG-G-D01/TG-F-D2/TG-D-DEF-20" for s in ev.t.stages)
    assert any((s.defect or "").startswith("TG-C-D06") for s in ev.t.stages)


def test_stated_hook_draws_are_consumed() -> None:
    """Hostile review R2-05: a stated hook result consumes its stated draws from the stream."""
    data = _load("grouped-effects-share-random-resize")
    data["spell"]["script_hooks"] = [{"list": "OnObjectAreaTargetSelect", "target": "TARGET_UNIT_SRC_AREA_ENEMY",
                                      "affected_mask": 3, "script": "spell_x", "handler": "Filter"}]
    data["script_results"] = {"OnObjectAreaTargetSelect:0:15": {"result": ["t1", "t2", "t3"], "draws": 2}}
    data["rng"]["draws"] = [7, 7, 3, 1, 1]
    res = _run(data)
    assert res.draws_consumed == 5 and res.recipients[0] == ["t2", "t3"]


def test_target_dest_radius_max_is_flagged() -> None:
    """TG-C-D08: TARGET_DEST_TARGET_* offsets use RadiusMax (CalcRadius(nullptr)) even when the caster radius differs."""
    data = _load("per-effect-dest-snapshot")
    data["spell"]["effects"][1]["target_a"] = "TARGET_DEST_TARGET_FRONT"
    data["spell"]["effects"][1]["radius_a"] = {"radius": 5, "per_level": 0, "min": 0, "max": 10}
    data["dest_positions"] = {"1:A": [15, 5, 0]}
    assert "TG-C-D08" in _run(data).defects
    data["spell"]["effects"][1]["radius_a"] = {"radius": 10, "per_level": 0, "min": 0, "max": 10}
    assert "TG-C-D08" not in _run(data).defects


def test_tranquility_hooks_do_not_load(tg_ctx) -> None:
    from targeting.recipients import hook_loads
    assert hook_loads(740, {"script": "spell_dru_inner_peace"}) is False
