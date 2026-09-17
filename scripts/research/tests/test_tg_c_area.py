"""Track C: area / cone / line / nearby selection stages on synthetic fixtures.

The relation / CheckTarget part is injected (``check=``) so each test isolates the
geometric, ordering and cap policy it discriminates; one test runs the real Track B
check end to end.  Every test names the wrong model it rules out.
"""

from __future__ import annotations


import pytest

from targeting import FailClosed, area, caps, oracle
from targeting.fixture import World
from targeting.trace import Trace


def unit(aid, x, y, z=0.0, kind="creature", reach=1.5, **facts):
    base = {"spell_other_immunity": [], "in_caster_phase": True}
    base.update(facts)
    return {"id": aid, "kind": kind, "pos": [x, y, z], "orientation": 0.0, "alive": True,
            "combat_reach": reach, "bounding_radius": 0.5, "facts": base}


def spell(target_a, target_b=0, radius=8.0, rmin=0.0, **kw):
    block = {"synthetic": True, "id": 900100, "range": {"min": [0, 0], "max": [40, 40]},
             "attributes": kw.pop("attributes", []), "attributes_cu": kw.pop("attributes_cu", []),
             "script_hooks": kw.pop("script_hooks", []), "cone_angle": kw.pop("cone_angle", 0.0),
             "width": kw.pop("width", 0.0), "max_affected_targets": kw.pop("max_targets", 0),
             "effects": [{"index": 0, "effect": "SCHOOL_DAMAGE", "target_a": target_a, "target_b": target_b,
                          "radius_a": {"radius": radius, "min": rmin, "max": radius},
                          "radius_b": kw.pop("radius_b", None), "conditions": kw.pop("conditions", None)}]}
    block.update(kw)
    return block


def world(actors, order=None, sp=None, draws=None, explicit=None, caster_facts=None, **extra):
    caster = {"id": "caster", "kind": "player", "pos": [0.0, 0.0, 0.0], "orientation": 0.0, "alive": True,
              "combat_reach": 1.5, "bounding_radius": 0.389, "facts": {"level": 80, "range_movement_bonus": False,
                                                                      "spell_other_immunity": [], "in_caster_phase": True}}
    caster["facts"].update(caster_facts or {})
    data = {"schema": "targeting-fixture/1", "name": "c", "spell": sp or spell("TARGET_UNIT_DEST_AREA_ENEMY"),
            "caster": "caster", "actors": [caster] + actors,
            "visit_order": order if order is not None else [a["id"] for a in actors],
            "explicit": explicit if explicit is not None else {"dest": [0.0, 0.0, 0.0]},
            "rng": {"draws": draws or []}, "spell_value": extra.pop("spell_value", {}),
            "modifiers": extra.pop("modifiers", {"radius": 0, "max_targets": 0})}
    data.update(extra)
    w = World.from_dict(data)
    return w, oracle.from_fixture(w.spell)


def everyone(world, sv, caster, target, sel, referer, trace):
    """Injected relation check: every non-caster candidate passes."""
    return target != caster


# ---------------------------------------------------------------------------
# area
# ---------------------------------------------------------------------------
def test_area_keeps_visit_order_and_uses_reach():
    """Rules out 'area results are sorted by distance' and 'radius excludes combat reach'."""
    w, sv = world([unit("far", 9.4, 0), unit("near", 1, 0), unit("out", 9.5, 0), unit("mid", 0, 5)],
                  order=["far", "out", "near", "mid"])
    r = area.select_area(w, sv, 0, "A", Trace(), check=everyone)
    assert r["targets"] == ["far", "near", "mid"]
    assert r["center"] == [0.0, 0.0, 0.0] and r["referer"] == "caster"


def test_area_does_not_invent_order():
    """Fails closed without visit_order (no grid model)."""
    w, sv = world([unit("a", 1, 0)], order=None)
    w.visit_order = None
    with pytest.raises(FailClosed):
        area.select_area(w, sv, 0, "A", None, check=everyone)


def test_area_cylinder_vertical_uses_max_without_reach():
    """Rules out the sphere model and 'reach extends the vertical bound'."""
    w, sv = world([unit("ledge", 3, 0, 8.0), unit("above", 3, 0, 8.5), unit("sphere_out", 8, 0, 7.5)])
    assert area.select_area(w, sv, 0, "A", None, check=everyone)["targets"] == ["ledge", "sphere_out"]


def test_area_radius_min_annulus():
    """Trinity RadiusMin is an inner exclusion (TG-C-U01): the clamp-floor model would keep 'close'."""
    w, sv = world([unit("close", 3, 0), unit("ring", 6, 0)], sp=spell("TARGET_UNIT_DEST_AREA_ENEMY", rmin=4.0))
    assert area.select_area(w, sv, 0, "A", None, check=everyone)["targets"] == ["ring"]


def test_area_centre_per_reference():
    """DEST centre is m_targets dst, CASTER centre is the caster, TARGET centre/referer is the explicit unit."""
    actors = [unit("t", 20, 0), unit("n", 21, 0), unit("c", 2, 0)]
    w, sv = world(actors, explicit={"dest": [20.0, 0.0, 0.0], "unit": "t"})
    assert area.select_area(w, sv, 0, "A", None, check=everyone)["targets"] == ["t", "n"]
    w, sv = world(actors, sp=spell("TARGET_UNIT_CASTER_AREA_RAID"), explicit={"unit": "t"})
    assert area.select_area(w, sv, 0, "A", None, check=everyone)["targets"] == ["c"]
    w, sv = world(actors, sp=spell("TARGET_UNIT_TARGET_AREA_RAID_CLASS"), explicit={"unit": "t"})
    r = area.select_area(w, sv, 0, "A", None, check=everyone)
    assert r["referer"] == "t" and r["targets"] == ["t", "n"]


def test_area_target_reference_without_unit_selects_nothing():
    """Spell.cpp:1359: no referer -> return before any search (no caster fallback)."""
    w, sv = world([unit("a", 1, 0)], sp=spell("TARGET_UNIT_TARGET_AREA_RAID_CLASS"), explicit={})
    assert area.select_area(w, sv, 0, "A", None, check=everyone)["targets"] == []


def test_area_dest_reference_requires_dest():
    w, sv = world([unit("a", 1, 0)], explicit={})
    with pytest.raises(FailClosed):
        area.select_area(w, sv, 0, "A", None, check=everyone)


def test_aoe_untargetable_needs_attr8():
    """AoETarget immunity drops the unit unless SPELL_ATTR8_CAN_HIT_AOE_UNTARGETABLE."""
    actors = [unit("imm", 2, 0, spell_other_immunity=["AoETarget"]), unit("chain_imm", 3, 0,
                                                                          spell_other_immunity=["ChainTarget"])]
    w, sv = world(actors)
    assert area.select_area(w, sv, 0, "A", None, check=everyone)["targets"] == ["chain_imm"]
    w, sv = world(actors, sp=spell("TARGET_UNIT_DEST_AREA_ENEMY", attributes=["SPELL_ATTR8_CAN_HIT_AOE_UNTARGETABLE"]))
    assert area.select_area(w, sv, 0, "A", None, check=everyone)["targets"] == ["imm", "chain_imm"]
    # Chain reason uses the other bit
    w, sv = world(actors)
    got = area.search_area(w, sv, sv.effect(0), 16, [0.0, 0.0, 0.0], "caster", (0.0, 8.0), "Chain", None, check=everyone)
    assert got == ["imm"]


def test_area_ignores_phase_but_cone_does_not():
    """Area searcher uses the AlwaysVisible phase shift; cone uses the caster's."""
    w, sv = world([unit("ph", 3, 0, in_caster_phase=False)])
    assert area.select_area(w, sv, 0, "A", None, check=everyone)["targets"] == ["ph"]
    w, sv = world([unit("ph", 3, 0, in_caster_phase=False)], sp=spell("TARGET_UNIT_CONE_CASTER_TO_DEST_ENEMY", cone_angle=90))
    assert area.select_cone(w, sv, 0, "A", None, check=everyone) == []


def test_players_only_mask():
    """PlayersOnly / ATTR3_ONLY_ON_PLAYER drop creatures before any check."""
    actors = [unit("npc", 1, 0), unit("pl", 2, 0, kind="player")]
    w, sv = world(actors, sp=spell("TARGET_UNIT_DEST_AREA_ENEMY", attributes=["SPELL_ATTR3_ONLY_ON_PLAYER"]))
    assert area.select_area(w, sv, 0, "A", None, check=everyone)["targets"] == ["pl"]


def test_conditions_and_hooks_fail_closed():
    w, sv = world([unit("a", 1, 0)], sp=spell("TARGET_UNIT_DEST_AREA_ENEMY", conditions="spell:groups[1]"))
    with pytest.raises(FailClosed):
        area.select_area(w, sv, 0, "A", None, check=everyone)
    hook = {"list": "OnObjectAreaTargetSelect", "target": "TARGET_UNIT_DEST_AREA_ENEMY", "affected_mask": 1}
    w, sv = world([unit("a", 1, 0)], sp=spell("TARGET_UNIT_DEST_AREA_ENEMY", script_hooks=[hook]))
    with pytest.raises(FailClosed):
        area.select_area(w, sv, 0, "A", None, check=everyone)
    # with an adapter the hook runs before the cap
    w, sv = world([unit("a", 1, 0), unit("b", 2, 0), unit("c", 3, 0)],
                  sp=spell("TARGET_UNIT_DEST_AREA_ENEMY", script_hooks=[hook], max_targets=1), draws=[1, 1])
    r = area.select_area(w, sv, 0, "A", None, check=everyone, script_hook=lambda ts, hooks: ts[1:])
    assert r["targets"] == ["b"] and w.draws_consumed == 2


# ---------------------------------------------------------------------------
# caps
# ---------------------------------------------------------------------------
def test_random_resize_draws_every_element():
    """Rules out 'stop drawing once the quota is full' and 'shuffle' models."""
    w, sv = world([unit(f"u{i}", i, 0) for i in range(1, 5)], sp=spell("TARGET_UNIT_DEST_AREA_ENEMY", max_targets=2),
                  draws=[3, 1, 2, 1])
    t = Trace()
    r = area.select_area(w, sv, 0, "A", t, check=everyone)
    # u1: 3<=2? no; u2: 1<=2 keep; u3: 2<=1? no; u4: 1<=1 keep
    assert r["targets"] == ["u2", "u4"]
    assert w.draws_consumed == 4


def test_no_draws_when_under_cap():
    w, sv = world([unit("a", 1, 0), unit("b", 2, 0)], sp=spell("TARGET_UNIT_DEST_AREA_ENEMY", max_targets=2))
    assert area.select_area(w, sv, 0, "A", None, check=everyone)["targets"] == ["a", "b"]
    assert w.draws_consumed == 0


def test_furthest_sorts_before_truncating_and_reverses_ties():
    """Rules out cap-before-sort (would keep visit-order heads) and stable-tie models (TG-C-D03)."""
    actors = [unit("near", 1, 0), unit("tieA", 6, 0), unit("tieB", 0, 6), unit("mid", 3, 0)]
    w, sv = world(actors, sp=spell("TARGET_UNIT_SRC_AREA_FURTHEST_ENEMY", max_targets=1),
                  explicit={"src": [0.0, 0.0, 0.0]})
    t = Trace()
    r = area.select_area(w, sv, 0, "A", t, check=everyone)
    assert r["targets"] == ["tieB"]            # visit order tieA, tieB; descending sort reverses the tie
    assert w.draws_consumed == 0               # truncation, no RandomResize
    assert any(s.defect and "TG-C-D03" in s.defect for s in t.stages)


def test_furthest_uses_referer_not_centre():
    """ObjectDistanceOrderPred(referer): referer is the caster even though the centre is the src position."""
    actors = [unit("a", 10, 0), unit("b", 14, 0)]
    w, sv = world(actors, sp=spell("TARGET_UNIT_SRC_AREA_FURTHEST_ENEMY", radius=8.0, max_targets=1),
                  explicit={"src": [12.0, 0.0, 0.0]})
    w.actors["caster"].pos = (0.0, 0.0, 0.0)
    assert area.select_area(w, sv, 0, "A", None, check=everyone)["targets"] == ["b"]
    w.actors["caster"].pos = (30.0, 0.0, 0.0)
    assert area.select_area(w, sv, 0, "A", None, check=everyone)["targets"] == ["a"]


def test_line_cap_sorts_nearest_only_when_over_cap():
    """LINE: nearest-first truncate; when max >= size the visit order is kept (no sort)."""
    sp = spell("TARGET_UNIT_LINE_CASTER_TO_DEST_ENEMY", width=2.0, max_targets=2)
    actors = [unit("far", 20, 0), unit("near", 2, 0), unit("mid", 10, 0)]
    w, sv = world(actors, sp=sp, explicit={"dest": [30.0, 0.0, 0.0]})
    w.explicit["dest_orientation"] = 0.0
    assert area.select_line(w, sv, 0, "A", None, check=everyone) == ["near", "mid"]
    sp["max_affected_targets"] = 3
    w, sv = world(actors, sp=sp, explicit={"dest": [30.0, 0.0, 0.0], "dest_orientation": 0.0})
    assert area.select_line(w, sv, 0, "A", None, check=everyone) == ["far", "near", "mid"]


def test_line_has_no_length_bound():
    """TG-C-D02: a 5-yd line still takes a unit 25 yd away if the fixture lists it as visited."""
    sp = spell("TARGET_UNIT_LINE_CASTER_TO_DEST_ENEMY", radius=5.0, width=2.0)
    w, sv = world([unit("way_out", 25, 0), unit("side", 3, 5)], sp=sp,
                  explicit={"dest": [5.0, 0.0, 0.0], "dest_orientation": 0.0})
    t = Trace()
    assert area.select_line(w, sv, 0, "A", t, check=everyone) == ["way_out"]
    assert any(s.defect and "TG-C-D02" in s.defect for s in t.stages)


def test_spell_value_override_replaces_spellmod():
    """SPELLVALUE_MAX_TARGETS replaces (not adds to) the modded value."""
    w, sv = world([unit("a", 1, 0)], sp=spell("TARGET_UNIT_DEST_AREA_ENEMY", max_targets=5),
                  spell_value={"max_affected_targets": 2}, modifiers={"max_targets": {"flat": 3}})
    assert caps.effective_max_targets(w, sv) == 2


def test_flat_spellmod_creates_a_cap_on_uncapped_spell():
    """Rules out 'mods only change existing caps': 0 + flat 5 = 5 (Spell.cpp:507)."""
    w, sv = world([unit("a", 1, 0)], sp=spell("TARGET_UNIT_DEST_AREA_ENEMY", max_targets=0),
                  modifiers={"max_targets": {"flat": 5}, "radius": 0})
    assert caps.effective_max_targets(w, sv) == 5
    w, sv = world([unit("a", 1, 0)], sp=spell("TARGET_UNIT_DEST_AREA_ENEMY", max_targets=0),
                  modifiers={"max_targets": {"flat": -1}, "radius": 0})
    with pytest.raises(FailClosed):
        caps.effective_max_targets(w, sv)


def test_cap_is_per_selector_call_not_shared():
    """TargetA and TargetB each get the full cap (no shared budget)."""
    sp = spell("TARGET_UNIT_DEST_AREA_ENEMY", "TARGET_UNIT_SRC_AREA_ENEMY", max_targets=1)
    sp["effects"][0]["radius_b"] = {"radius": 8.0, "max": 8.0}
    w, sv = world([unit("a", 1, 0), unit("b", 30, 0)], sp=sp,
                  explicit={"dest": [0.0, 0.0, 0.0], "src": [30.0, 0.0, 0.0]}, draws=[])
    assert area.select_area(w, sv, 0, "A", None, check=everyone)["targets"] == ["a"]
    assert area.select_area(w, sv, 0, "B", None, check=everyone)["targets"] == ["b"]


# ---------------------------------------------------------------------------
# cone / nearby
# ---------------------------------------------------------------------------
def test_cone_zero_degrees_defaults_to_90_even_for_cone_180():
    """TG-C-D01: CONE_180 with ConeDegrees 0 is a 90-degree cone (load-time default beats Spell.cpp:1285)."""
    w, sv = world([unit("at60", 5 * 0.5, 5 * 0.866), unit("at40", 5 * 0.766, 5 * 0.643)],
                  sp=spell("TARGET_UNIT_CONE_180_DEG_ENEMY", cone_angle=0.0))
    assert area.cone_angle_degrees(sv, area._selector(54)) == 90.0
    assert area.select_cone(w, sv, 0, "A", None, check=everyone) == ["at40"]


def test_cone_boundary_radius_bypass():
    """A unit behind the caster but within max(bounding, 2) yd (3D) is in any cone."""
    w, sv = world([unit("behind_close", -1.5, 0), unit("behind", -4, 0)],
                  sp=spell("TARGET_UNIT_CONE_CASTER_TO_DEST_ENEMY", cone_angle=60))
    assert area.select_cone(w, sv, 0, "A", None, check=everyone) == ["behind_close"]


def test_cone_width_makes_a_rectangle():
    """Width != 0 -> CU_CONE_LINE: HasInLine(Width + target reach) instead of the arc."""
    sp = spell("TARGET_UNIT_RECT_CASTER_ENEMY", cone_angle=10, width=2.0, attributes_cu=["SPELL_ATTR0_CU_CONE_LINE"])
    w, sv = world([unit("lateral", 6, 3.4), unit("wide", 6, 3.6)], sp=sp)
    assert area.select_cone(w, sv, 0, "A", None, check=everyone) == ["lateral"]


def test_nearby_first_visited_wins_ties():
    """WorldObjectLastSearcher + strict '<': among equal distances the first visited is kept."""
    w, sv = world([unit("x", 5, 0), unit("y", 0, 5), unit("z", 7, 0)], order=["z", "x", "y"],
                  sp=spell("TARGET_UNIT_NEARBY_ENEMY"))
    assert area.search_nearby(w, sv, sv.effect(0), 2, 30.0, None, check=everyone) == "x"


# ---------------------------------------------------------------------------
# integration with Track B's real check
# ---------------------------------------------------------------------------
def test_area_with_track_b_check_stated_relations():
    """End-to-end: relations stated as valid_attack facts; B's CheckTarget reads visibility/alive facts."""
    try:
        from targeting import explicit  # noqa: F401
    except ImportError:
        pytest.skip("Track B explicit.py absent")
    actors = [unit("foe", 2, 0), unit("friend", 3, 0)]
    for a in actors:
        a["facts"].update({"profile": "combat-sim", "visible": True, "ghost": False, "in_combat": False, "magnet": False, "in_flight": False})
    w, sv = world(actors, relations=[
        {"from": "caster", "to": "foe", "valid_attack": True, "can_see": True},
        {"from": "caster", "to": "friend", "valid_attack": False, "can_see": True}],
        caster_facts={"profile": "combat-sim"})
    try:
        r = area.select_area(w, sv, 0, "A", Trace())
    except FailClosed as exc:
        pytest.skip(f"Track B check needs more facts: {exc}")
    assert r["targets"] == ["foe"]


# ---------------------------------------------------------------------------
# reproduced defects are marked only where they decide the outcome (review R2-06)
# ---------------------------------------------------------------------------
def _defects(t):
    return [s.defect for s in t.stages if s.defect]


def test_d01_marked_only_when_180_would_differ():
    t = Trace()
    w, sv = world([unit("at60", 2.5, 4.33)], sp=spell("TARGET_UNIT_CONE_180_DEG_ENEMY", cone_angle=0.0))
    assert area.select_cone(w, sv, 0, "A", t, check=everyone) == []
    assert any("TG-C-D01" in d for d in _defects(t))
    t = Trace()
    w, sv = world([unit("ahead", 5, 0)], sp=spell("TARGET_UNIT_CONE_180_DEG_ENEMY", cone_angle=0.0))
    area.select_cone(w, sv, 0, "A", t, check=everyone)
    assert not _defects(t)


def test_d04_marked_for_degenerate_360_cone():
    t = Trace()
    w, sv = world([unit("behind", -5, 0), unit("ahead", 5, 0)],
                  sp=spell("TARGET_UNIT_CONE_CASTER_TO_DEST_ENEMY", cone_angle=360.0))
    assert area.select_cone(w, sv, 0, "A", t, check=everyone) == ["ahead"]
    assert any("TG-C-D04" in d for d in _defects(t))
    t = Trace()
    w, sv = world([unit("behind", -5, 0)], sp=spell("TARGET_UNIT_CONE_CASTER_TO_DEST_ENEMY", cone_angle=90.0))
    area.select_cone(w, sv, 0, "A", t, check=everyone)
    assert not _defects(t)


def test_d02_marked_only_when_area_check_would_reject():
    sp = spell("TARGET_UNIT_LINE_CASTER_TO_DEST_ENEMY", radius=5.0, width=2.0)
    t = Trace()
    w, sv = world([unit("inside", 3, 0)], sp=sp, explicit={"dest": [5.0, 0.0, 0.0], "dest_orientation": 0.0})
    area.select_line(w, sv, 0, "A", t, check=everyone)
    assert not _defects(t)
    t = Trace()
    w, sv = world([unit("imm", 3, 0, spell_other_immunity=["AoETarget"])], sp=sp,
                  explicit={"dest": [5.0, 0.0, 0.0], "dest_orientation": 0.0})
    assert area.select_line(w, sv, 0, "A", t, check=everyone) == ["imm"]
    assert any("TG-C-D02" in d for d in _defects(t))


def test_d03_marked_only_when_tie_order_changes():
    t = Trace()
    w, sv = world([unit("a", 6, 0), unit("b", 3, 0)], sp=spell("TARGET_UNIT_SRC_AREA_FURTHEST_ENEMY"),
                  explicit={"src": [0.0, 0.0, 0.0]})
    area.select_area(w, sv, 0, "A", t, check=everyone)
    assert not _defects(t)
