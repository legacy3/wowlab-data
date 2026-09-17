"""Track I: AreaTrigger tick pipeline over synthetic fixtures (targeting.areatriggers).

Each test names the competing model it rules out.  World-DB create properties are read
through ``tg_ctx`` where a real AT is used.
"""

from __future__ import annotations

import copy

import pytest

from targeting import FailClosed
from targeting import areatriggers as at
from targeting.fixture import World
from targeting.trace import Trace


def unit(uid, pos, *, kind="creature", reach=1.0, alive=True, flags=(), **facts):
    f = {"profile": "combat-sim", "unit_flags": list(flags)}
    f.update(facts)
    return {"id": uid, "kind": kind, "pos": list(pos), "orientation": 0.0, "alive": alive,
            "combat_reach": reach, "owner": None, "charmer": None, "group": None, "facts": f}


def world(at_facts, units, *, at_pos=(0.0, 0.0, 0.0), at_o=0.0, relations=(), order=None, caster_group=None):
    caster = unit("caster", (0.0, -3.0, 0.0), kind="player", reach=1.5)
    caster["group"] = caster_group
    actors = [caster, {"id": "at", "kind": "areatrigger", "pos": list(at_pos), "orientation": at_o,
                       "facts": {"areatrigger": at_facts}}] + units
    return World.from_dict({
        "schema": "targeting-fixture/1", "name": "at", "spell": {"id": 0}, "caster": "caster",
        "actors": actors, "relations": list(relations),
        "visit_order": order if order is not None else ["caster", "at"] + [u["id"] for u in units],
    })


def at_facts(cp, **kw):
    d = {"create_properties": cp, "caster": "caster", "override_scale": None, "inside": []}
    d.update(kw)
    return d


CONSECRATION = 4488      # sphere r=8, template 9228, areatrigger_pal_consecration
BLIZZARD = 4658          # cylinder r=8 h=4, areatrigger_mage_blizzard
PW_BARRIER = 1489        # sphere r=8, action ADDAURA 81782 FRIEND


@pytest.fixture(scope="module")
def atw(tg_ctx):
    return at.ATWorld(tg_ctx.bundle.world)


def test_consecration_sphere_is_3d_with_reach_and_dead_units(atw):
    """Rules out: 2D circle (u_up is 7 yd above: inside a 3D sphere of 8+reach, the cylinder model would need a
    height band), radius without target reach (u_reach at 8.9 with reach 1), reqAlive (dead creature kept),
    and 'dead players are candidates' (CORPSE player removed by the template filter)."""
    units = [
        unit("u_in", (3.0, 0.0, 0.0)),
        unit("u_up", (0.0, 0.0, 7.0), reach=1.5),
        unit("u_reach", (8.9, 0.0, 0.0), reach=1.0),
        unit("u_out", (9.1, 0.0, 0.0), reach=0.0),
        unit("u_diag", (6.0, 6.0, 1.0), reach=0.0),    # 3D dist 8.54 > 8
        unit("u_dead", (1.0, 1.0, 0.0), alive=False),
        unit("p_corpse", (2.0, 0.0, 0.0), kind="player", alive=False, death_state="CORPSE"),
    ]
    w = world(at_facts(CONSECRATION), units)
    t = Trace()
    r = at.evaluate_tick(w, "at", t, atw)
    assert r["candidates"] == ["caster", "u_in", "u_up", "u_reach", "u_dead", "p_corpse"]
    assert r["targets"] == ["caster", "u_in", "u_up", "u_reach", "u_dead"]
    assert r["entering"] == r["targets"]
    assert r["script"]["uses"] == "inside-units"


def test_enter_order_is_visit_order_and_exits_unordered(atw):
    """Rules out: enter hooks sorted by distance/GUID (they follow the searcher list), and a defined exit order."""
    units = [unit("a", (5.0, 0.0, 0.0)), unit("b", (1.0, 0.0, 0.0)), unit("c", (2.0, 0.0, 0.0))]
    w = world(at_facts(CONSECRATION, inside=["b", "gone1", "gone2"]), units,
              order=["c", "a", "b"])
    r = at.evaluate_tick(w, "at", Trace(), atw)
    assert r["entering"] == ["c", "a"]
    assert r["exiting"] == ["gone1", "gone2"] and r["exit_order_specified"] is False


def test_blizzard_cylinder_band_and_position_only(atw):
    """Rules out: 'Blizzard damage goes to the AT's inside units' -- the AI casts 190357 at the AT position, so the
    AT list (height band [z-4, z+4], 2D radius+reach) is not the damage recipient set."""
    units = [unit("low", (0.0, 1.0, -4.0), reach=0.0), unit("high", (0.0, 1.0, 4.5), reach=0.0),
             unit("edge", (8.5, 0.0, 30.0 - 30.0), reach=0.6)]
    w = world(at_facts(BLIZZARD), units)
    r = at.evaluate_tick(w, "at", Trace(), atw)
    assert r["targets"] == ["caster", "low", "edge"]
    assert r["script"]["uses"] == "position-only"
    assert r["script"]["update"][0]["cast"] == 190357


def test_template_action_friend_requirement(atw):
    """Rules out 'template actions apply to every entering unit' (TargetType FRIEND = IsValidAssistTarget)."""
    units = [unit("ally", (1.0, 0.0, 0.0), kind="player", reach=1.5), unit("enemy", (2.0, 0.0, 0.0))]
    rel = [{"from": "caster", "to": "ally", "valid_assist": True},
           {"from": "caster", "to": "enemy", "valid_assist": False},
           {"from": "caster", "to": "caster", "valid_assist": True}]
    w = world(at_facts(PW_BARRIER), units, relations=rel)
    r = at.evaluate_tick(w, "at", Trace(), atw, spell_view_for=lambda s: None)
    assert [a["spell"] for a in r["actions"]["ally"]] == [81782]
    assert r["actions"]["enemy"] == []
    assert r["actions"]["ally"][0]["action"] == "add-aura"


def test_missing_create_properties_fails_closed(atw):
    """Blessed Hammer (MiscValue 6006) has no world-DB row: no AT exists, the oracle must not invent a shape."""
    w = world(at_facts(6006), [])
    with pytest.raises(FailClosed):
        at.evaluate_tick(w, "at", Trace(), atw)


def test_moving_at_needs_stated_pose(atw):
    """Song of Chi-Ji (5484, MoveCurveId) moves on a path: the tick pose must be stated, not taken from creation."""
    w = world(at_facts(5484), [unit("u", (1.0, 0.0, 0.0))])
    with pytest.raises(FailClosed):
        at.evaluate_tick(w, "at", Trace(), atw)
    w = world(at_facts(5484, tick_pose_stated=True), [unit("u", (1.0, 0.0, 0.0))])
    assert at.evaluate_tick(w, "at", Trace(), atw)["targets"] == ["caster", "u"]


def test_halo_progress(atw):
    """Halo (33742: radius 0 -> 40, MorphCurveId): radius follows the stated (curve-evaluated) progress."""
    u = [unit("near", (9.0, 0.0, 0.0), reach=0.0), unit("far", (21.0, 0.0, 0.0), reach=0.0)]
    with pytest.raises(FailClosed):
        at.evaluate_tick(world(at_facts(33742), u), "at", Trace(), atw)
    r = at.evaluate_tick(world(at_facts(33742, progress=0.25), u), "at", Trace(), atw)
    assert r["targets"] == ["caster", "near"]    # radius 10
    r = at.evaluate_tick(world(at_facts(33742, progress=0.75, inside=["caster", "near"]), u), "at", Trace(), atw)
    assert r["entering"] == ["far"]              # near stays inside: not re-entered (no second hit)


def test_override_scale_replaces_curve_and_scales_height(atw):
    """Rules out 'height ignores scale' (HeightIgnoresScale is never set from create-properties flags)."""
    u = [unit("u", (0.0, 11.0, 5.5), reach=0.0)]
    r = at.evaluate_tick(world(at_facts(BLIZZARD, override_scale=1.5), u), "at", Trace(), atw)
    assert r["targets"] == ["caster", "u"]       # r 12, h 6
    r = at.evaluate_tick(world(at_facts(BLIZZARD, override_scale=1.5, field_flags=1), u), "at", Trace(), atw)
    assert r["targets"] == ["caster"]            # h stays 4


SYN = {"shape": {"type": 0, "data": [10, 10, 0, 0, 0, 0, 0, 0]},
       "template": {"Id": 1, "IsCustom": 0, "Flags": 0, "ActionSetId": 0, "ActionSetFlags": 0}}


def syn(flags, **kw):
    d = copy.deepcopy(SYN)
    d["template"]["ActionSetFlags"] = flags
    d.update(at_facts(0, **kw))
    return d


def test_action_set_flags():
    """Rules out 'CreatorsPartyOnly means same faction' (it is caster->IsInRaidWith) and checks caster exclusions."""
    grp = {"id": "g1", "subgroup": 1, "raid": False}
    friend = unit("friend", (1.0, 0.0, 0.0), kind="player", reach=1.5)
    friend["group"] = grp
    stranger = unit("stranger", (2.0, 0.0, 0.0), kind="player", reach=1.5)
    units = [friend, stranger]

    def run(flags):
        w = world(syn(flags), copy.deepcopy(units), caster_group=grp)
        return at.evaluate_tick(w, "at", Trace())["targets"]

    assert run(0) == ["caster", "friend", "stranger"]
    assert run(at.ACTION_SET_FLAGS["NotTriggeredbyCaster"]) == ["friend", "stranger"]
    assert run(at.ACTION_SET_FLAGS["OnlyTriggeredByCaster"]) == ["caster"]
    assert run(at.ACTION_SET_FLAGS["CreatorsPartyOnly"]) == ["caster", "friend"]


def test_uninteractible_and_ghosts():
    """Rules out 'uninteractible units are never candidates' (only filtered with a template, flag-gated) and
    'ghosts count as corpses' (DEAD needs AllowWhileGhost, CORPSE needs AllowWhileDead)."""
    units = [unit("ui", (1.0, 0.0, 0.0), flags=("UNINTERACTIBLE",)),
             unit("ghost", (2.0, 0.0, 0.0), kind="player", alive=False, death_state="DEAD"),
             unit("corpse", (3.0, 0.0, 0.0), kind="player", alive=False, death_state="CORPSE")]
    t = at.evaluate_tick(world(syn(0), units), "at", Trace())["targets"]
    assert t == ["caster"]
    flags = at.ACTION_SET_FLAGS["CanAffectUninteractible"] | at.ACTION_SET_FLAGS["AllowWhileGhost"]
    assert at.evaluate_tick(world(syn(flags), units), "at", Trace())["targets"] == ["caster", "ui", "ghost"]
    no_tpl = copy.deepcopy(SYN)
    no_tpl["template"] = None
    no_tpl.update(at_facts(0))
    assert at.evaluate_tick(world(no_tpl, units), "at", Trace())["targets"] == ["caster", "ui", "ghost", "corpse"]


def test_expired_at_selects_nothing():
    """Rules out 'the last tick still selects': expiry removes the AT before UpdateTargetList (AreaTrigger.cpp:388)."""
    r = at.evaluate_tick(world(syn(0, inside=["x"], expired=True), [unit("x", (1.0, 0.0, 0.0))]), "at", Trace())
    assert r == {"expired": True, "exiting": ["x"], "exit_order_specified": True}


def test_phase_fact_required():
    """The AT searcher filters by the AT phase shift: a fixture without phase facts fails closed."""
    u = unit("u", (1.0, 0.0, 0.0))
    del u["facts"]["profile"]
    with pytest.raises(FailClosed):
        at.evaluate_tick(world(syn(0), [u]), "at", Trace())
    u["facts"]["in_areatrigger_phase"] = False
    u["facts"]["unit_flags"] = []
    assert at.evaluate_tick(world(syn(0), [u]), "at", Trace())["targets"] == ["caster"]


def test_unknown_script_fails_closed():
    d = syn(0)
    d["script_name"] = "at_unverified_script"
    with pytest.raises(FailClosed):
        at.evaluate_tick(world(d, []), "at", Trace())
