"""Track D: chain targeting (Spell::SelectImplicitChainTargets / SearchChainTargets).

Synthetic fixtures discriminate competing chain policies; the probe differential
compiles Trinity's own SearchChainTargets and compares jump lists.
"""

from __future__ import annotations

import json

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from targeting import CORPORA, FailClosed
from targeting import chain as ch
from targeting.fixture import World
from targeting.oracle import from_fixture
from targeting.trace import Trace



def allow(*_a, **_k) -> bool:
    return True


def spell(chain=3, dmg=1, target="TARGET_UNIT_TARGET_ENEMY", attrs=(), eff_attrs=0, effects=None):
    return {"synthetic": True, "id": 900100, "dmg_class": dmg, "range": None, "attributes": list(attrs),
            "script_hooks": [], "los_disabled": False,
            "effects": effects or [{"index": 0, "effect": "SCHOOL_DAMAGE", "target_a": target,
                                    "chain_targets": chain, "attributes": eff_attrs}]}


def unit(aid, pos, kind="creature", reach=1.0, hp=100, mhp=100, **facts):
    return {"id": aid, "kind": kind, "pos": list(pos), "orientation": 0.0, "alive": True, "health": hp,
            "max_health": mhp, "combat_reach": reach,
            "facts": {"spell_other_immunity": [], "ignore_los_on_me": False, **facts}}


def world(actors, order, sp=None, caster="c", los=None, modifiers=None, spell_value=None, caster_kind="creature"):
    acts = [unit(caster, (0, 0, 0), kind=caster_kind)] + actors
    return World.from_dict({"schema": "targeting-fixture/1", "name": "t", "spell": sp or spell(), "caster": caster,
                            "actors": acts, "visit_order": order, "los": los or {"default": "clear"},
                            "modifiers": modifiers or {}, "spell_value": spell_value or {}})


def run(w, initial="p", jumps=None, heal=False):
    sv = from_fixture(w.spell)
    eff = sv.effect(0)
    sel = 45 if heal else 6
    n = jumps if jumps is not None else ch.max_chain_targets(w, sv, eff) - 1
    return ch.search_chain_targets(w, sv, eff, sel, initial, n, heal, Trace(), check=allow)


# ---------------------------------------------------------------------------
# selection rules
# ---------------------------------------------------------------------------
def test_jump_reference_is_previous_target():
    """Rules out nearest-to-initial and nearest-to-caster: P(20,0) X(28,0) Y(36,0) Z(12.5,0)."""
    actors = [unit("p", (20, 0, 0)), unit("x", (28, 0, 0)), unit("y", (36, 0, 0)), unit("z", (11.5, 0, 0))]
    w = world(actors, ["p", "z", "x", "y"])
    got = run(w, jumps=3)
    assert got[:2] == ["x", "y"]
    assert ch.naive_model(w, ["z", "x", "y"], "p", 2, reference="initial") == ["x", "z"]
    assert ch.naive_model(w, ["z", "x", "y"], "p", 2, reference="caster") == ["z", "x"]


def test_distance_tie_keeps_first_in_visit_order():
    """GetDistanceOrder is strict <: equal centre distances keep the earlier candidate (not lowest id)."""
    actors = [unit("p", (0, 10, 0)), unit("b", (5, 10, 0)), unit("a", (-5, 10, 0))]
    w = world(actors, ["p", "b", "a"], sp=spell(chain=2))
    assert run(w) == ["b"]
    assert ch.naive_model(w, ["b", "a"], "p", 1, tie="lowest-id") == ["a"]


def test_caster_can_be_a_jump_target():
    """tempTargets.remove(target) removes only the initial target (Spell.cpp:2266)."""
    actors = [unit("p", (3, 0, 0))]
    w = world(actors, ["c", "p"], sp=spell(chain=2))
    assert run(w) == ["c"]


def test_first_candidate_needs_jump_radius_and_fewer_than_cap_is_normal():
    actors = [unit("p", (0, 30, 0)), unit("far", (0, 42.5, 0), reach=1.0)]
    w = world(actors, ["p", "far"], sp=spell(chain=3))
    # 12.5 >= 10 + 1 + 1 -> outside the magic jump radius
    assert run(w) == []


def test_exact_boundary_is_exclusive():
    """IsInDist is strict ``<``: distance == jump + both reaches is rejected."""
    actors = [unit("p", (0, 30, 0), reach=1.0), unit("q", (0, 42, 0), reach=1.0)]
    assert run(world(actors, ["p", "q"], sp=spell(chain=3))) == []  # pre-filter 20y keeps q
    actors[1] = unit("q", (0, 41.999, 0), reach=1.0)
    assert run(world(actors, ["p", "q"], sp=spell(chain=3))) == ["q"]


def test_replacement_skips_jump_radius_check_defect():
    """TG-D-DEF-01: a closer (by centre) candidate with small reach replaces an in-range one."""
    # jump 10, p reach 0: big at 14.9 (reach 5 -> in range: 14.9 < 15), small at 12 (reach 0 -> 12 >= 10 out of range)
    actors = [unit("p", (0, 0, 20), reach=0.0), unit("big", (14.9, 0, 20), reach=5.0),
              unit("small", (12, 0, 20), reach=0.0)]
    w = world(actors, ["p", "big", "small"], sp=spell(chain=3))
    tr = Trace()
    sv = from_fixture(w.spell)
    got = ch.search_chain_targets(w, sv, sv.effect(0), 6, "p", 2, False, tr, check=allow)
    assert got == ["small", "big"]
    assert any(s.defect == "TG-D-DEF-01" for s in tr.stages)
    # visit order matters: small first is rejected by the radius check, big accepted
    w2 = world(actors, ["p", "small", "big"], sp=spell(chain=3))
    assert run(w2) == ["big", "small"]


def test_los_from_previous_target_and_enforce_los_from_caster():
    actors = [unit("p", (0, 20, 0)), unit("a", (0, 24, 0)), unit("b", (0, 26, 0))]
    w = world(actors, ["p", "a", "b"], sp=spell(chain=2), los={"default": "clear", "blocked": [["p", "a"]]})
    assert run(w) == ["b"]
    w = world(actors, ["p", "a", "b"], sp=spell(chain=2, eff_attrs=0x10000),
              los={"default": "clear", "blocked": [["c", "a"]]})
    assert run(w) == ["b"]
    w = world(actors, ["p", "a", "b"], sp=spell(chain=2), los={"default": "clear", "blocked": [["c", "a"]]})
    assert run(w) == ["a"]


def test_melee_chain_front_arc_and_melee_radius():
    sp = spell(chain=3, dmg=2, attrs=["SPELL_ATTR5_MELEE_CHAIN_TARGETING"])
    actors = [unit("p", (3, 0, 0)), unit("behind", (-2, 1, 0)), unit("front", (4, 3, 0))]
    w = world(actors, ["p", "behind", "front"], sp=sp)
    assert run(w) == ["front"]


def test_chain_from_caster_and_from_initial_target():
    # chain from caster: every jump measured from the caster, search radius = GetMinMaxRange(false).Max
    sp = spell(chain=3, attrs=["SPELL_ATTR2_CHAIN_FROM_CASTER"])
    actors = [unit("p", (20, 0, 0)), unit("a", (8, 0, 0)), unit("b", (9, 0, 0)), unit("d", (30, 0, 0))]
    w = world(actors, ["p", "d", "b", "a"], sp=sp, spell_value={"chain_from_caster_max_range": 40.0})
    assert run(w) == ["a", "b"]
    with pytest.raises(FailClosed):
        run(world(actors, ["p", "d", "b", "a"], sp=sp))
    # chain from initial target: source stays the initial target, radius = jumpRadius
    sp2 = spell(chain=3, eff_attrs=0x80)
    actors = [unit("p", (0, 0, 0)), unit("a", (0, 5, 0)), unit("b", (0, 13, 0)), unit("e", (0, -7, 0))]
    w = world(actors, ["p", "a", "b", "e"], sp=sp2, caster="cc")
    assert run(w) == ["a", "e"]


def test_chain_heal_picks_largest_absolute_deficit():
    """Rules out lowest-health-percent: A deficit 40 (60%), B deficit 30 (40%)."""
    actors = [unit("p", (0, 10, 0)), unit("a", (0, 15, 0), hp=60, mhp=100),
              unit("b", (0, 14, 0), hp=20, mhp=50)]
    w = world(actors, ["p", "b", "a"], sp=spell(chain=2, dmg=1, target="TARGET_UNIT_TARGET_CHAINHEAL_ALLY",
                                                 effects=None))
    assert run(w, heal=True) == ["a"]
    assert ch.naive_model(w, ["b", "a"], "p", 1, metric="lowest-health-pct") == ["b"]


def test_chain_heal_ties_zero_deficit_and_range():
    actors = [unit("p", (0, 10, 0)), unit("full", (0, 12, 0)), unit("t1", (0, 13, 0), hp=90),
              unit("t2", (0, 14, 0), hp=90), unit("far", (0, 25, 0), hp=1)]
    w = world(actors, ["p", "far", "full", "t2", "t1"])
    # far (largest deficit) is in the 25y pre-filter but 15 >= 12.5+1+1 from p even as the first
    # candidate; full is accepted first, t2 (deficit 10) replaces it, t1 (equal deficit) does not;
    # the second jump is measured from t2, where far is in range.
    assert run(w, jumps=2, heal=True) == ["t2", "far"]
    # nothing injured: the first acceptable full-health unit is chosen, later ones never replace it
    actors = [unit("p", (0, 10, 0)), unit("f1", (0, 12, 0)), unit("f2", (0, 11, 0))]
    assert run(world(actors, ["p", "f1", "f2"]), jumps=2, heal=True) == ["f1", "f2"]


def test_chain_heal_ignores_enforce_los_attribute():
    actors = [unit("p", (0, 10, 0)), unit("a", (0, 12, 0), hp=50)]
    w = world(actors, ["p", "a"], sp=spell(chain=2, eff_attrs=0x10000),
              los={"default": "clear", "blocked": [["c", "a"]]})
    assert run(w, jumps=1, heal=True) == ["a"]


def test_uint32_deficit_truncation():
    actors = [unit("p", (0, 10, 0)), unit("huge", (0, 11, 0), hp=0, mhp=2 ** 32),
              unit("small", (0, 12, 0), hp=0, mhp=5)]
    # huge's deficit wraps to 0: it is found first (deficit 0), small (5) replaces it
    assert run(world(actors, ["p", "huge", "small"]), jumps=1, heal=True) == ["small"]


def test_jump_radius_by_dmg_class_and_mods():
    for dmg, heal, want in ((3, False, 7.5), (2, False, 5.0), (1, False, 10.0), (0, True, 12.5), (1, True, 12.5)):
        assert ch.jump_radius_base(from_fixture(spell(dmg=dmg)), heal) == want
    w = world([unit("p", (0, 1, 0))], ["p"], caster="pl", caster_kind="player",
              modifiers={"chain_targets": {"flat": 2, "pct": 1.0}, "chain_jump_distance": {"flat": 0, "pct": 1.5}})
    sv = from_fixture(spell(chain=1))
    assert ch.max_chain_targets(w, sv, sv.effect(0)) == 3        # authored 1 + flat 2 -> chains
    assert ch.jump_radius(w, sv, False) == 15.0
    with pytest.raises(FailClosed):
        ch.max_chain_targets(world([unit("p", (0, 1, 0))], ["p"], caster="pl", caster_kind="player"), sv, sv.effect(0))


def test_select_implicit_chain_targets_hook_after_jumps_and_los_positions():
    actors = [unit("p", (0, 10, 0)), unit("a", (0, 14, 0)), unit("b", (0, 18, 0))]
    w = world(actors, ["p", "a", "b"], sp=spell(chain=3))
    sv = from_fixture(w.spell)
    seen = []

    def hook(targets):
        seen.append(list(targets))
        return list(reversed(targets))
    res = ch.select_implicit_chain_targets(w, sv, sv.effect(0), 6, "p", 0b1, Trace(), hook, check=allow)
    assert seen == [["a", "b"]]
    assert res.jumps == ["a", "b"]
    assert res.add_calls == [("b", "p"), ("a", "b")]   # losPosition follows the (reordered) list
    assert res.applied_multiplier_mask == 0b1
    w1 = world(actors, ["p", "a", "b"], sp=spell(chain=1))
    assert ch.select_implicit_chain_targets(w1, from_fixture(w1.spell), from_fixture(w1.spell).effect(0), 6,
                                            "p", 1, Trace(), check=allow).jumps == []


def test_chain_callers():
    assert ch.chains_from(6) and ch.chains_from(45) and ch.chains_from(2)
    assert ch.chains_from(1) is None and ch.chains_from(16) is None and ch.chains_from(24) is None


# ---------------------------------------------------------------------------
# probe differential
# ---------------------------------------------------------------------------
def _probe_or_skip():
    from targeting import cmd_d
    if not cmd_d.probe_available():
        pytest.skip("TrinityCore checkout absent")
    try:
        cmd_d.ensure_probe()
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"probe build failed: {exc}")
    return cmd_d


coord = st.sampled_from([0.0, 1.0, 2.5, 5.0, 7.5, 9.0, 10.0, 11.0, 12.5, -3.0, -10.0, 0.1, 13.999999])
unit_st = st.tuples(coord, coord, st.sampled_from([0.0, 0.0, 1.0]), st.sampled_from([0.0, 0.5, 1.0, 1.5, 3.0]),
                    st.integers(0, 4), st.booleans())


@settings(max_examples=150, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(units=st.lists(unit_st, min_size=1, max_size=7), jumps=st.integers(1, 4), heal=st.booleans(),
       dmg=st.sampled_from([0, 1, 2, 3]), flags=st.sampled_from([0, 0x80, 0x10000]),
       cfc=st.booleans(), melee=st.booleans(), blocked=st.lists(st.tuples(st.integers(0, 7), st.integers(0, 7)), max_size=4))
def test_probe_differential_chain(units, jumps, heal, dmg, flags, cfc, melee, blocked):
    cmd_d = _probe_or_skip()
    attrs = (["SPELL_ATTR2_CHAIN_FROM_CASTER"] if cfc else []) + (["SPELL_ATTR5_MELEE_CHAIN_TARGETING"] if melee else [])
    actors = [unit("p", (4.0, 4.0, 0.0), hp=90)]
    names = []
    for i, (x, y, z, reach, deficit, _) in enumerate(units):
        names.append(f"u{i}")
        actors.append(unit(f"u{i}", (x, y, z), reach=reach, hp=10 - deficit, mhp=10))
    allnames = ["c", "p"] + names
    los = {"default": "clear", "blocked": [[allnames[a], allnames[b]] for a, b in blocked
                                           if a < len(allnames) and b < len(allnames) and a != b]}
    w = world(actors, ["p"] + names + ["c"], sp=spell(chain=jumps + 1, dmg=dmg, attrs=attrs, eff_attrs=flags), los=los,
              spell_value={"chain_from_caster_max_range": 30.0})
    sv = from_fixture(w.spell)
    eff = sv.effect(0)
    tr = Trace()
    got = ch.search_chain_targets(w, sv, eff, 45 if heal else 6, "p", jumps, heal, tr, check=allow)
    cand_stage = next(s for s in tr.stages if s.name == "area.search")
    case, idx = cmd_d.chain_case(w, sv, eff, "p", jumps, heal, cand_stage.output)
    res = cmd_d.run_probe([case])[0]
    assert "error" not in res, res
    inv = {v: k for k, v in idx.items()}
    assert [inv[i] for i in res["targets"]] == got
    radius = next(s for s in tr.stages if s.name == "chain.search_radius").output
    assert float.fromhex(res["search_radius"]) == radius
    assert inv[res["source"]] == ("c" if cfc else "p")


# ---------------------------------------------------------------------------
# real data
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def chains(tg_ctx):
    from targeting import cmd_d
    return cmd_d.build_chains()


def test_corpus_matches_regeneration(chains):
    path = CORPORA / "chains.json"
    assert json.loads(path.read_text()) == json.loads(json.dumps(chains, sort_keys=True)), \
        "regenerate: targeting.py chains --out docs/research/targeting-corpora/chains.json"


def test_real_chain_rows(chains):
    rows = {(r["spell"], r["effect"]): r for r in chains["effects"]}
    heal = rows[(1064, 0)]
    assert heal["chain_heal"] and heal["jump_radius_base"] == 12.5 and heal["chain_targets_authored"] == 4
    assert {m["name"] for m in heal["modifiers"]} >= {"Ancestral Reach", "Tidebringer"}
    hs = rows[(206930, 1)]
    assert hs["melee_chain_targeting"] and hs["jump_radius_base"] == 5.0
    avs = rows[(31935, 1)]
    assert avs["chain_targets_authored"] < 1 and avs["chain_targets_used"] == 3 and avs["inherits_other_effect_chain"]
    assert chains["effect_classes"]["1064:0"]["class"] == "understood"
    assert "chain-by-modifier" in chains["effect_classes"]["12294:0"]["tags"]   # Sweeping Strikes via SpellMod
    assert chains["counts"]["chain_heal"] == 1
    for key, row in chains["effect_classes"].items():
        s, e = map(int, key.split(":"))
        assert row["class"] in ("understood", "understood-with-defect", "fixture-dependent", "blocked", "unresolved")


def test_chain_census_excludes_max_le1_after_mods(chains):
    """R2-04: Spell.cpp:1838 -- effects whose ChainTargets after every modifier is <= 1 never chain."""
    keys = {(r["spell"], r["effect"]) for r in chains["effects"]}
    assert (204157, 0) not in keys and (44425, 0) not in keys and (44425, 1) not in keys
    inert = {(r["spell"], r["effect"]) for r in chains["inert_with_modifiers"]}
    assert {(204157, 0), (44425, 0), (44425, 1)} <= inert
    assert all(r["max_chain_targets_after_mods"] > 1 for r in chains["effects"])


def test_chain_heal_deficit_truncation_marked():
    actors = [unit("p", (0, 10, 0)), unit("huge", (0, 11, 0), hp=0, mhp=2 ** 32)]
    w = world(actors, ["p", "huge"])
    sv = from_fixture(w.spell)
    tr = Trace()
    ch.search_chain_targets(w, sv, sv.effect(0), 45, "p", 1, True, tr, check=allow)
    assert any(s.defect == "TG-D-DEF-02" for s in tr.stages)
