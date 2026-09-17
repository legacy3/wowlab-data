"""Track D: target-selection RNG helpers and their position in the shared stream."""

from __future__ import annotations

import json

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from targeting import CORPORA, FailClosed, rng
from targeting.fixture import World


def w_(draws):
    return World.from_dict({"schema": "targeting-fixture/1", "name": "rng", "spell": {"synthetic": True},
                            "caster": "c", "actors": [{"id": "c", "kind": "creature"}], "rng": {"draws": list(draws)}})


def test_random_resize_draws_every_element():
    """Rules out 'stop drawing once the quota is filled': 5 candidates, keep 2 -> exactly 5 draws."""
    w = w_([1, 1, 3, 2, 1])
    assert rng.random_resize(w, list("abcde"), 2) == ["a", "b"]
    assert w.draws_consumed == 5 == rng.random_resize_draw_count(5, 2)


def test_random_resize_shortcut_and_order():
    w = w_([])
    assert rng.random_resize(w, list("abc"), 3) == list("abc") and w.draws_consumed == 0
    w = w_([3, 2, 1])       # keeps only the last; relative order of kept elements is preserved
    assert rng.random_resize(w, list("abc"), 1) == ["c"]
    with pytest.raises(FailClosed):
        rng.random_resize(w_([9]), list("ab"), 1)          # out-of-range draw


def test_random_resize_pred_requested_zero_filters_only():
    w = w_([])
    assert rng.random_resize_pred(w, [1, 2, 3, 4], lambda x: x % 2 == 0, 0) == [2, 4]
    assert w.draws_consumed == 0


def test_select_random_single_element_still_draws():
    w = w_([0])
    assert rng.select_random(w, ["only"]) == "only" and w.draws_consumed == 1
    with pytest.raises(FailClosed):
        rng.select_random(w_([]), [])


def test_shuffle_forward_fisher_yates():
    w = w_([0, 0, 1])
    assert rng.random_shuffle(w, list("abcd")) == list("cdba")
    w = w_([])
    assert rng.random_shuffle(w, ["x"]) == ["x"] and w.draws_consumed == 0


def test_hit_draw_plan_gates():
    base = dict(immune=False, damage_immune=False, positive_not_hostile=False, self_target=False, evading=False,
                can_reflect=False, reflect_chance=0.0, always_hit=False, dmg_class=1, no_avoidance=False,
                victim_dead_non_player=False)
    assert [p["draw"] for p in rng.hit_draw_plan(rng.HitFacts(**base))] == ["irand(0,9999)"]
    assert rng.hit_draw_plan(rng.HitFacts(**{**base, "positive_not_hostile": True})) == []
    assert rng.hit_draw_plan(rng.HitFacts(**{**base, "self_target": True})) == []
    assert rng.hit_draw_plan(rng.HitFacts(**{**base, "dmg_class": 0})) == []
    assert rng.hit_draw_plan(rng.HitFacts(**{**base, "victim_dead_non_player": True})) == []
    melee = rng.hit_draw_plan(rng.HitFacts(**{**base, "dmg_class": 2, "can_reflect": True, "reflect_chance": 5.0}))
    assert [p["draw"] for p in melee] == ["rand_chance", "urand(0,9999)"] and melee[1]["if"] == "reflect-failed"
    assert [p["draw"] for p in rng.hit_draw_plan(rng.HitFacts(**{**base, "always_hit": True, "can_reflect": True,
                                                                     "reflect_chance": 5.0}))] == ["rand_chance"]
    assert rng.hit_draw_plan(rng.HitFacts(**{**base, "immune": True}), can_immune=True) == []
    assert rng.hit_draw_plan(rng.HitFacts(**{**base, "immune": True})) != []   # AddUnitTarget passes canImmune=false


def test_worked_example_order():
    """Selection draws precede every hit draw; crit draws come after all hit draws."""
    from targeting.cmd_d import worked_example
    ex = worked_example()
    phases = [d["phase"] for d in ex["sequence"]]
    assert phases == ["select"] * 5 + ["select/AddUnitTarget"] * 2 + ["launch/PreprocessSpellLaunch"] * 2
    assert ex["kept"] == ["t2", "t4"] and ex["consumed"] == 9
    assert [d["target"] for d in ex["sequence"][5:]] == ["t2", "t4", "t2", "t4"]


# ---------------------------------------------------------------------------
# probe differentials (Containers.h compiled from the checkout)
# ---------------------------------------------------------------------------
def _probe():
    from targeting import cmd_d
    if not cmd_d.probe_available():
        pytest.skip("TrinityCore checkout absent")
    cmd_d.ensure_probe()
    return cmd_d


@st.composite
def resize_case(draw):
    size = draw(st.integers(0, 12))
    n = draw(st.integers(0, 12))
    draws = [draw(st.integers(1, size - k)) for k in range(size)] if size > n else []
    return size, n, draws


@settings(max_examples=150, deadline=None)
@given(case=resize_case(), container=st.sampled_from(["resize", "resize_list"]))
def test_probe_random_resize(case, container):
    cmd_d = _probe()
    size, n, draws = case
    res = cmd_d.run_probe([f"{container} {n} {size}\ndraws {' '.join(map(str, draws))}\ngo"])[0]
    assert "error" not in res, res
    w = w_(draws)
    assert rng.random_resize(w, list(range(size)), n) == res["kept"]
    assert len(res["log"]) == w.draws_consumed == rng.random_resize_draw_count(size, n)


@settings(max_examples=60, deadline=None)
@given(size=st.integers(1, 9), data=st.data())
def test_probe_select_random(size, data):
    cmd_d = _probe()
    d = data.draw(st.integers(0, size - 1))
    res = cmd_d.run_probe([f"select {size}\ndraws {d}\ngo"])[0]
    assert res["pick"] == rng.select_random(w_([d]), list(range(size)))
    assert res["log"] == [f"urand(0,{size - 1})={d}"]


@settings(max_examples=150, deadline=None)
@given(n=st.integers(0, 20), words=st.lists(st.integers(0, 2 ** 32 - 1), min_size=200, max_size=200))
def test_probe_shuffle_index_model(n, words):
    """The compiled std::ranges::shuffle swaps (i, j_i) for i = 1..n-1 ascending with j_i <= i."""
    cmd_d = _probe()
    res = cmd_d.run_probe([f"shuffle {n}\nwords {' '.join(map(str, words))}\ngo"])[0]
    # degenerate engine words (e.g. all zero) make uniform_int_distribution reject forever
    assume("exhausted" not in res.get("error", ""))
    assert "error" not in res, res
    swaps = res["swaps"]
    assert [a for a, _ in swaps] == list(range(1, n))
    assert all(0 <= b <= a for a, b in swaps)
    js = [b for _, b in swaps]
    assert rng.random_shuffle(w_(js), list(range(n))) == res["perm"]
    # engine consumption: libstdc++ pairs positions -> at most ceil((n-1)/2)+1 words (rejection may add more)
    used = len(words) - res["words_left"]
    assert n <= 1 and used == 0 or used >= (n - 1 + 1) // 2


# ---------------------------------------------------------------------------
# real data
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def corpus(tg_ctx):
    from targeting import cmd_d
    return cmd_d.build_rng()


def test_rng_corpus_regenerates(corpus):
    committed = json.loads((CORPORA / "rng.json").read_text())
    assert committed == json.loads(json.dumps(corpus, sort_keys=True)), \
        "regenerate: targeting.py rng --out docs/research/targeting-corpora/rng.json"


def test_rng_real_rows(corpus):
    assert corpus["generator"]["shared"] is True
    assert corpus["counts"]["random_dest_effects"] == len(corpus["random_dest_effects"])
    for row in corpus["random_dest_effects"]:
        key = f"{row['spell']}:{row['effect']}"
        assert "random-dest" in corpus["effect_classes"][key]["tags"]
    ids = {s["id"] for s in corpus["engine_sites"]}
    assert {"area-cap", "cone-cap", "smart-injured", "priority-rules", "select-nearby-target", "grouping-radius-compare"} <= ids
    roles = {(s["file"].rsplit("/", 1)[-1], s["line"]): s["role"] for s in corpus["script_sites_in_reach"]}
    assert roles[("spell_rogue.cpp", 285)] == "draw-no-recipient"  # Blade Flurry: draw live, HandleProc mask 0 (R3-01)
    assert roles[("spell_rogue.cpp", 668)] == "dead"               # Killing Spree list never filled (R3-06)


def test_random_dest_draws_follow_calc_radius_path(corpus):
    """R2-01: 1263077:0 (TARGET_DEST_TARGET_RANDOM, no radius entry) draws only the angle."""
    rows = {(r["spell"], r["effect"], r["slot"]): r for r in corpus["random_dest_effects"]}
    row = rows[(1263077, 0, "A")]
    assert row["draws"] == "angle" and row["order"] == "angle"
    assert rows[(458101, 0, "A")]["draws"] == "radius+angle"


def test_launch_draw_census_empty_for_current_players(corpus):
    ld = corpus["launch_draw_census"]
    assert ld["summon_default_branch_calcradius_draw"] == [] and ld["summon_multiple_units_getrandompoint"] == []
    assert ld["transmitted_launch_draws"] == []
    assert any("CallScriptOnCastHandlers" in p["consumer"] for p in corpus["cast_order"])
