"""Track F: effect-mask grouping, unique target list, per-effect destinations."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from targeting import FailClosed
from targeting import recipients as r
from targeting.fixture import World
from targeting.rng import random_resize
from targeting.trace import Trace

# selectors (SharedDefines.h)
CASTER, SRC_CASTER, SRC_AREA_ALLY, DEST_CASTER, DEST_TARGET_ANY, DEST_AREA_ALLY = 1, 22, 30, 18, 63, 31
TARGET_ENEMY, CASTER_AREA_RAID, DEST_CASTER_RANDOM = 6, 56, 72


@dataclass(frozen=True)
class E:
    index: int
    effect: int = 6
    target_a: int = 0
    target_b: int = 0
    attributes: int = 0
    conditions: object = None
    radius: float = 0.0
    chain_targets: int = 0


@dataclass(frozen=True)
class SV:
    effects: tuple
    script_hooks: tuple = ()
    id: int = 900000


def radius_of(effects):
    return lambda k, idx: (0.0, effects[k].radius)


def no_scripts(i, j):
    return True


def masks(effects, check=no_scripts):
    return [s.mask for s in r.selection_plan(effects, radius_of(effects), check) if s.mask]


# ---------------------------------------------------------------------------
# grouping keys
# ---------------------------------------------------------------------------

def test_grouping_keys():
    eff = (E(0, target_a=SRC_CASTER, target_b=SRC_AREA_ALLY, radius=40),
           E(1, target_a=SRC_CASTER, target_b=SRC_AREA_ALLY, radius=40),
           E(2, target_a=SRC_CASTER, target_b=SRC_AREA_ALLY, radius=30),
           E(3, target_a=SRC_CASTER, target_b=SRC_AREA_ALLY, radius=40, attributes=0x4000),
           E(4, target_a=SRC_CASTER, target_b=SRC_AREA_ALLY, radius=40, conditions="c1"),
           E(5, target_a=CASTER))
    assert masks(eff) == [0b11, 0b100, 0b1000, 0b10000, 0b100000]


def test_radius_not_compared_for_default_categories():
    """Rules out 'radius always a key': DEFAULT-category selectors group despite different radii."""
    eff = (E(0, target_a=DEST_CASTER, radius=5), E(1, target_a=DEST_CASTER, radius=10))
    assert masks(eff) == [0b11]


def test_chain_targets_not_a_key():
    """Rules out 'ChainTargets is a key' (Avenger's Shield shape): grouped, lead's chain count used."""
    eff = (E(0, target_a=TARGET_ENEMY, chain_targets=3), E(1, target_a=TARGET_ENEMY, chain_targets=-10))
    plan = r.selection_plan(eff, radius_of(eff), no_scripts)
    assert plan[0].mask == 0b11 and plan[1].mask == 0


def test_processed_mask_and_non_transitive_script_check():
    """``&= ~processed``: a later lead never re-selects effects grouped earlier, even if it would match more."""
    eff = tuple(E(k, target_a=CASTER) for k in range(3))
    # 0~1 and 1~2 but not 0~2 (non-transitive script identity)
    pairs = {(0, 1), (1, 2)}
    plan = r.selection_plan(eff, radius_of(eff), lambda i, j: (i, j) in pairs)
    assert [(s.effect, s.mask, s.raw_mask) for s in plan] == [(0, 0b011, 0b011), (1, 0b100, 0b110), (2, 0, 0b100)]


def test_gaps_are_not_effects():
    eff = (E(0, target_a=CASTER), E(1, effect=0), E(2, target_a=CASTER))
    assert masks(eff) == [0b101]


def test_radius_rng_draw_order():
    """RNG-consuming radius: called lazily with short-circuit (TG-F-D2)."""
    calls = []
    eff = (E(0, target_a=DEST_CASTER_RANDOM, target_b=SRC_AREA_ALLY), E(1, target_a=DEST_CASTER_RANDOM, target_b=SRC_AREA_ALLY))
    draws = iter([0.25, 0.5])

    def radius_fn(k, idx):
        calls.append((k, idx))
        return (0.0, next(draws))
    plan = r.selection_plan(eff, radius_fn, no_scripts)
    assert calls == [(0, "A"), (1, "A")]          # unequal A -> B never evaluated
    assert plan[0].mask == 0b01 and plan[0].split == {1: "radius"}


def test_script_check_function_identity():
    # synthetic hooks state `loads` (track G, hostile review R1-07: only loaded scripts are compared)
    hooks = ({"list": "OnObjectAreaTargetSelect", "script": "s", "handler": "Filter", "affected_mask": 0b01, "loads": True},
             {"list": "OnObjectAreaTargetSelect", "script": "s", "handler": "Filter", "affected_mask": 0b10, "loads": True},
             {"list": "OnDestinationTargetSelect", "script": "s", "handler": "Dest", "affected_mask": 0b01, "loads": True})
    check = r.script_effect_check(SV((), hooks))
    assert check(0, 1)
    hooks2 = hooks[:1] + ({"list": "OnObjectAreaTargetSelect", "script": "s", "handler": "Other", "affected_mask": 0b10,
                           "loads": True},)
    assert not r.script_effect_check(SV((), hooks2))(0, 1)
    assert not r.script_effect_check(SV((), hooks[:1]))(0, 1)       # hook only on effect 0
    unloaded = ({**hooks[0], "loads": False},)                         # Validate() failed: not in m_loadedScripts
    assert r.script_effect_check(SV((), unloaded))(0, 1)
    with pytest.raises(FailClosed):
        r.script_effect_check(SV((), None))


def test_condition_identity():
    eff = tuple(E(k) for k in range(3))
    assert r.implicit_condition_identity(eff, []) == [None, None, None]
    assert r.implicit_condition_identity(eff, [(0b011, None), (0b011, None)]) == [1, 1, None]
    assert r.implicit_condition_identity(eff, [(0b001, None), (0b010, None)]) == [1, 2, None]
    with pytest.raises(FailClosed):                                  # order-dependent (TG-F-D4)
        r.implicit_condition_identity(eff, [(0b011, None), (0b001, None)])


# ---------------------------------------------------------------------------
# synthetic selection runs
# ---------------------------------------------------------------------------

def _world(draws, visit=("u1", "u2", "u3", "u4")):
    actors = [{"id": "caster", "kind": "player", "pos": [0, 0, 0]}] + [
        {"id": u, "kind": "player", "pos": [i + 1.0, 0, 0]} for i, u in enumerate(visit)]
    return World.from_dict({"schema": "targeting-fixture/1", "name": "f", "spell": {}, "caster": "caster",
                            "actors": actors, "visit_order": list(visit), "rng": {"draws": list(draws)}})


def _unique(n):
    return r.UniqueTargets(n, lambda k: True, lambda t, k, los: True, lambda t, imp: True, lambda t, k: False)


def _run_area(effects, world, cap, candidates_for=None):
    """Mini SelectSpellTargets: SRC_CASTER + SRC_AREA_ALLY with RandomResize(cap)."""
    targets = _unique(len(effects))
    plan = r.selection_plan(effects, radius_of(effects), no_scripts)

    def select(e, idx, mask, state):
        t = e.target_a if idx == "A" else e.target_b
        if t == SRC_CASTER:
            state["src"] = "caster"
        elif t == SRC_AREA_ALLY:
            cands = [u for u in world.enumeration() if candidates_for is None or u in candidates_for(e)]
            kept = random_resize(world, cands, cap)
            for u in kept:
                targets.add(u, mask, False, True)

    res = r.select_spell_targets(effects, plan, targets, select, lambda e, s: None, {"dst": None})
    return res


def test_a_grouped_effects_share_one_draw_set():
    """Rules out per-effect independent draws: 4 candidates, cap 2 -> exactly 4 urand draws, one set for both effects."""
    eff = (E(0, effect=10, target_a=SRC_CASTER, target_b=SRC_AREA_ALLY, radius=40),
           E(1, effect=6, target_a=SRC_CASTER, target_b=SRC_AREA_ALLY, radius=40))
    w = _world([1, 3, 1, 1])      # keep u1 (1<=2), drop u2 (3>1), keep u3 (1<=1), last not kept (keep=0)
    res = _run_area(eff, w, 2)
    assert w.draws_consumed == 4
    assert res.targets.order() == ["u1", "u3"]
    assert res.targets.masks() == {"u1": 0b11, "u3": 0b11}
    assert res.targets.recipients(0) == res.targets.recipients(1)


def test_b_split_effects_draw_independently():
    """Rules out 'same selectors => shared set': a PlayersOnly difference splits the draw sets (8 draws)."""
    eff = (E(0, effect=10, target_a=SRC_CASTER, target_b=SRC_AREA_ALLY, radius=40),
           E(1, effect=6, target_a=SRC_CASTER, target_b=SRC_AREA_ALLY, radius=40, attributes=0x4000))
    w = _world([1, 3, 1, 1, 3, 1, 1, 1])
    res = _run_area(eff, w, 2)
    assert w.draws_consumed == 8
    assert res.targets.recipients(0) == ["u1", "u3"]
    assert res.targets.recipients(1) == ["u3", "u2"]   # list order, not effect-1 draw order
    # u2 first appears in effect 1's turn -> appended after effect 0's recipients; u3 merged
    assert res.targets.order() == ["u1", "u3", "u2"]
    assert res.targets.masks() == {"u1": 0b01, "u3": 0b11, "u2": 0b10}
    assert res.targets.target_index_for_effect("u3", 1) == 0
    assert res.targets.target_index_for_effect("u3", 0) == 1
    assert res.targets.hit_order() == [(0, "u1"), (0, "u3"), (1, "u3"), (1, "u2")]


def test_c_unit_in_two_groups_appears_once_with_merged_mask():
    """Rallying Cry shape (97462): CASTER_AREA_RAID (eff0) then UNIT_CASTER (eff1); caster listed once, mask 0b11,
    at its eff0 enumeration position (rules out 'caster first' and duplicate entries)."""
    targets = _unique(2)
    eff = (E(0, effect=3, target_a=CASTER_AREA_RAID, radius=40), E(1, effect=3, target_a=CASTER))
    plan = r.selection_plan(eff, radius_of(eff), no_scripts)
    area_order = ["p2", "caster", "p3"]

    def select(e, idx, mask, state):
        t = e.target_a if idx == "A" else e.target_b
        if t == CASTER_AREA_RAID:
            for u in area_order:
                targets.add(u, mask, False, True)
        elif t == CASTER:
            targets.add("caster", mask, False)

    r.select_spell_targets(eff, plan, targets, select, lambda e, s: None, {"dst": None})
    assert targets.order() == ["p2", "caster", "p3"]
    assert targets.masks() == {"p2": 0b01, "caster": 0b11, "p3": 0b01}
    assert [t.adds for t in targets.items] == [1, 2, 1]


def test_d_later_effects_see_dst_changed_by_earlier_turns():
    """Rules out per-effect private destinations: m_targets dst is spell-wide, recorded per effect at its turn."""
    eff = (E(0, effect=28, target_a=DEST_CASTER), E(1, effect=28, target_a=DEST_TARGET_ANY),
           E(2, effect=3, target_a=DEST_AREA_ALLY, radius=8))
    plan = r.selection_plan(eff, radius_of(eff), no_scripts)
    assert [s.mask for s in plan] == [1, 2, 4]
    centres = []

    def select(e, idx, mask, state):
        t = e.target_a if idx == "A" else e.target_b
        if t == DEST_CASTER:
            state["dst"] = "caster-pos"
        elif t == DEST_TARGET_ANY:
            state["dst"] = "target-pos"
        elif t == DEST_AREA_ALLY:
            centres.append(state["dst"])

    targets = _unique(3)
    res = r.select_spell_targets(eff, plan, targets, select, lambda e, s: None, {"dst": None})
    assert res.dests == {0: "caster-pos", 1: "target-pos", 2: "target-pos"}
    assert centres == ["target-pos"]


def test_add_unit_target_rules():
    """CheckEffectTarget clears bits first; CheckTarget only with checkIfValid; immunity after the empty test."""
    t = Trace()
    ut = r.UniqueTargets(2, lambda k: True, lambda u, k, los: not (u == "blocked" and k == 0),
                         lambda u, imp: u != "invalid", lambda u, k: u == "immune", t)
    assert ut.add("blocked", 0b01) == "rejected-effects"
    assert ut.add("invalid", 0b01, check_if_valid=False) == "added"
    assert ut.add("invalid2", 0b01) == "added"
    assert ut.add("invalid", 0b10) == "rejected-check"             # re-add is re-validated; list entry untouched
    assert ut.masks()["invalid"] == 0b01
    assert ut.add("invalid", 0b10, check_if_valid=False) == "merged"
    assert ut.masks()["invalid"] == 0b11
    ut2 = r.UniqueTargets(1, lambda k: True, lambda u, k, los: True, lambda u, imp: False, lambda u, k: True)
    assert ut2.add("x", 0b1, check_if_valid=True) == "rejected-check"
    assert ut2.add("x", 0b1, check_if_valid=False) == "added"       # TG-F-D3: fully immune, mask 0 entry
    assert ut2.masks() == {"x": 0}
    assert any(s.name == "recipients.add_unit_target" for s in t.stages)


def _analyse(spell: int) -> dict:
    from targeting.cmd_f import analyse, view
    return analyse(view(spell))


def test_real_mark_of_the_wild_grouped(tg_ctx):
    a = _analyse(1126)
    assert [g["effects"] for g in a["groups"]] == [[0, 1]]


def test_real_divine_hymn_shared_draw(tg_ctx):
    a = _analyse(64844)
    assert a["groups"][0]["effects"] == [0, 1] and a["groups"][0]["shared_draw_set"]


def test_real_avengers_shield_chain_from_lead(tg_ctx):
    a = _analyse(31935)
    assert a["groups"][0]["effects"] == [0, 1, 2]
    assert a["groups"][0]["chain_count_from_lead"] == 3
    assert "chain-count-from-lead" in a["tags"][1]


def test_real_earthquake_split_by_radius(tg_ctx):
    a = _analyse(61882)
    assert [g["effects"] for g in a["groups"]] == [[0], [1], [2], [3]]
    assert a["plan"][2]["split"]["3"] == "radius"
    assert "dest-read-after-earlier-write" in a["tags"][2]


def test_real_rallying_cry_two_unit_groups(tg_ctx):
    a = _analyse(97462)
    assert [g["effects"] for g in a["groups"]] == [[0], [1]]
    assert "multi-group-unit" in a["tags"][0]


def test_corpus_counts_are_current(tg_ctx):
    import json

    from targeting import CORPORA
    from targeting.cmd_f import build_effect_recipients, build_group_policy
    for name, build in (("effect-recipients.json", build_effect_recipients), ("group-policy.json", build_group_policy)):
        path = CORPORA / name
        if not path.exists():
            pytest.skip(f"{name} not generated")
        on_disk = json.loads(path.read_text(encoding="utf-8"))
        fresh = json.loads(json.dumps(build()))
        assert fresh == on_disk, f"{name} is stale: rerun targeting.py"
