"""Track J: aura-side recipient selection (UnitAura / DynObjAura target maps), synthetic fixtures.

Each test names the competing model it rules out.  ``Scene`` is also the scenario builder of
``test_tg_j_probe.py`` (differential against verbatim Trinity code).
"""

from __future__ import annotations

import copy
from typing import Any

import pytest

from targeting import FailClosed
from targeting import auratargets as J
from targeting.fixture import World
from targeting.oracle import for_world
from targeting.trace import Trace

BASE_FACTS = {"profile": "combat-sim", "faction_template": 1, "level": 90, "class": 2, "range_movement_bonus": False,
              "spell_other_immunity": [], "immune_effects": [], "cc_breakable_by_damage": False,
              "in_world": True, "banished": False, "suppressed_by_label": False, "aura_immune": False,
              "aura_immune_existing": False, "aura_immune_effects": [], "in_flight": False}
PARTY = {"id": "G", "raid": False, "subgroup": 0}
RAID0 = {"id": "R", "raid": True, "subgroup": 0}
RAID1 = {"id": "R", "raid": True, "subgroup": 1}


def effect(index: int, eff: int, radius: float | None = 30.0, *, target_a: int = 1, target_b: int = 0,
           per_level: float = 0.0, rmin: float = 0.0, attributes: int | list = 0) -> dict[str, Any]:
    e = {"index": index, "effect": eff, "aura": 4, "target_a": target_a, "target_b": target_b, "attributes": attributes}
    if radius is not None:
        e["radius_a"] = {"radius": radius, "per_level": per_level, "min": rmin, "max": radius}
    return e


class Scene:
    """A mutable fixture builder (World JSON with an ``aura`` block)."""

    def __init__(self, effects: list[dict], *, attributes: list[str] | None = None, spell_id: int = 990001):
        self.doc: dict[str, Any] = {
            "schema": "targeting-fixture/1", "name": "j-synthetic",
            "spell": {"synthetic": True, "id": spell_id, "range": None, "effects": effects,
                      "attributes": attributes or [], "attributes_cu": [], "is_positive": True, "los_disabled": False},
            "caster": None, "actors": [], "relations": [], "visit_order": [], "los": {"default": "clear"},
            "modifiers": {"radius": 0, "range": 0},
            "aura": {"owner": None, "caster": None, "static_applications": {}, "applications": {}},
        }

    def unit(self, uid: str, pos, *, kind: str = "player", group=None, owner=None, alive=True, reach=1.5,
             auras: list | None = None, visit: bool = True, summoner: str | None = None, **facts) -> Scene:
        f = dict(BASE_FACTS)
        f.update(facts)
        self.doc["actors"].append({"id": uid, "kind": kind, "pos": list(pos), "orientation": 0.0, "alive": alive,
                                   "combat_reach": reach, "bounding_radius": 0.389, "owner": owner, "charmer": None,
                                   "summoner": summoner, "group": group, "auras": auras or [], "facts": f})
        if visit:
            self.doc["visit_order"].append(uid)
        if self.doc["caster"] is None:
            self.doc["caster"] = uid
        return self

    def dynobj(self, did: str, pos, caster: str, radius: float) -> Scene:
        self.doc["actors"].append({"id": did, "kind": "dynamicobject", "pos": list(pos), "orientation": 0.0,
                                   "facts": {"caster": caster, "radius": radius}})
        return self

    def rel(self, src: str, dst: str, **kw) -> Scene:
        for r in self.doc["relations"]:
            if (r["from"], r["to"]) == (src, dst):
                r.update(kw)
                return self
        self.doc["relations"].append({"from": src, "to": dst, **kw})
        return self

    def friends(self, src: str, *dsts: str) -> Scene:
        for d in dsts:
            self.rel(src, d, friendly=True, hostile=False, valid_assist=True, valid_attack=False)
        return self

    def foes(self, src: str, *dsts: str) -> Scene:
        for d in dsts:
            self.rel(src, d, friendly=False, hostile=True, valid_assist=False, valid_attack=True)
        return self

    def aura(self, owner: str, caster: str | None, statics=None, apps=None) -> Scene:
        self.doc["aura"].update({"owner": owner, "caster": caster, "static_applications": statics or {},
                                 "applications": apps or {}})
        return self

    def actor(self, uid: str) -> dict:
        return next(a for a in self.doc["actors"] if a["id"] == uid)

    def world(self) -> World:
        return World.from_dict(copy.deepcopy(self.doc))

    def fill(self, eff: int = 0, trace: Trace | None = None) -> list[str]:
        w = self.world()
        return J.fill_target_map(w, for_world(w), self.doc["aura"]["owner"], eff, trace)

    def map(self) -> dict[str, int]:
        w = self.world()
        return J.target_map(w, for_world(w), self.doc["aura"]["owner"])

    def update(self) -> dict:
        w = self.world()
        return J.update_target_map(w, for_world(w), self.doc["aura"]["owner"])


def raid_scene(eff: int = 65, radius: float = 40.0) -> Scene:
    s = Scene([effect(0, eff, radius)])
    s.unit("p1", (0, 0, 0), group=RAID0).unit("p2", (10, 0, 0), group=RAID1).unit("p3", (5, 0, 0), group=RAID0)
    s.unit("pet3", (6, 0, 0), kind="pet", owner="p3").unit("x", (4, 0, 0), group=None)
    s.friends("p1", "p2", "p3", "pet3", "x").aura("p1", "p1")
    return s


# ---------------------------------------------------------------------------
# selection switch
# ---------------------------------------------------------------------------
def test_raid_aura_includes_other_subgroup_party_aura_does_not():
    """Rules out 'RAID and PARTY area auras select the same set' (TARGET_CHECK_RAID vs PARTY, SpellAuras.cpp:2587-2592)."""
    assert raid_scene(65).fill() == ["p1", "p2", "p3", "pet3"]
    assert raid_scene(35).fill() == ["p1", "p3", "pet3"]
    assert raid_scene(271).fill() == ["p1", "p3", "pet3"]      # PARTY_NONRANDOM shares the PARTY branch


def test_friend_aura_ignores_groups_and_enemy_aura_excludes_owner():
    """Rules out 'FRIEND = party' and 'ENEMY aura also hits its owner' (ALLY / ENEMY checks)."""
    assert raid_scene(128).fill() == ["p1", "p2", "p3", "pet3", "x"]
    s = raid_scene(129)
    s.unit("e1", (3, 0, 0), kind="creature").foes("p1", "e1")
    assert s.fill() == ["e1"]


def test_party_aura_includes_party_members_pets():
    """Rules out 'party auras only reach players': IsInPartyWith resolves the pet's owner (Unit.cpp:12184)."""
    assert "pet3" in raid_scene(35).fill()


def test_owner_receives_area_effect_only_via_the_map():
    """Rules out 'the owner always has the area effect': AddStaticApplication strips non-APPLY_AURA bits
    (SpellAuras.cpp:2650) and NOT_ON_PLAYER removes players from the search mask (Spell.cpp:2154)."""
    w = raid_scene().world()
    sv = for_world(w)
    assert J.static_application_mask(sv, 0b1) == 0
    s = Scene([effect(0, 35), effect(1, 6, None)], attributes=["SPELL_ATTR5_NOT_ON_PLAYER"])
    s.unit("p1", (0, 0, 0), group=PARTY).unit("pet", (2, 0, 0), kind="pet", owner="p1").friends("p1", "pet")
    s.aura("p1", "p1", statics={"p1": [1]})
    assert s.map() == {"p1": 0b10, "pet": 0b01}


def test_players_only_effect_attribute_excludes_pets():
    """Rules out 'PlayersOnly is a spell-side-only flag' (GetSearcherTypeMask reads EffectAttributes, Spell.cpp:2150)."""
    s = raid_scene()
    s.doc["spell"]["effects"][0]["attributes"] = ["PlayersOnly"]
    assert s.fill() == ["p1", "p2", "p3"]


def test_summons_aura_owner_plus_casters_summons_only():
    """Rules out 'SUMMONS aura hits every summon nearby' and 'owner excluded': owner pushed (2623), then
    TARGET_CHECK_SUMMONED against the aura *caster* (Spell.cpp:9369)."""
    s = Scene([effect(0, 202, 100.0)])
    s.unit("h", (0, 0, 0)).unit("wolf", (5, 0, 0), kind="guardian", owner="h", summoner="h", is_summon=True)
    s.unit("o", (3, 0, 0)).unit("owl", (4, 0, 0), kind="guardian", owner="o", summoner="o", is_summon=True)
    s.friends("h", "wolf", "o", "owl").aura("h", "h")
    assert s.fill() == ["h", "wolf"]


def test_pet_aura_owner_and_master_with_owner_reach_only():
    """AREA_AURA_PET: owner + GetCharmerOrOwner in IsInRange3d(Position*) -- only the aura owner's combat reach
    counts; rules out the symmetric WorldObject overload (both reaches)."""
    s = Scene([effect(0, 119, 10.0)])
    s.unit("pet", (0, 0, 0), kind="pet", owner="h", reach=1.0).unit("h", (11.5, 0, 0), reach=1.5)
    s.rel("pet", "h", in_same_phase=True).aura("pet", "h")
    assert s.fill() == ["pet"]                              # 11.5 >= 10 + 1.0 (pet reach), hunter reach ignored
    s.actor("h")["pos"] = [10.9, 0, 0]
    assert s.fill() == ["pet", "h"]


def test_owner_aura_phase_and_world_gates():
    """AREA_AURA_OWNER: no owner self push (vs PET), phase and in-world gates (SpellAuras.cpp:2605-2608)."""
    s = Scene([effect(0, 143, 10.0)])
    s.unit("pet", (0, 0, 0), kind="pet", owner="h").unit("h", (3, 0, 0)).rel("pet", "h", in_same_phase=True)
    s.aura("pet", "h")
    assert s.fill() == ["h"]
    s.rel("pet", "h", in_same_phase=False)
    assert s.fill() == []


def test_apply_aura_on_pet_has_no_range_and_skips_owner():
    """APPLY_AURA_ON_PET (174): the owner's pet at any distance, owner excluded (no static app for 174);
    rules out treating 174 as an area aura with a radius."""
    s = Scene([effect(0, 174, 5.0)])
    s.unit("w", (0, 0, 0), pet="imp").unit("imp", (500, 0, 0), kind="pet", owner="w", in_owner_map=True, visit=False)
    s.aura("w", "w")
    assert s.fill() == ["imp"]
    assert s.map() == {"imp": 1}
    s.actor("imp")["facts"]["in_owner_map"] = False
    assert s.fill() == []


def test_plain_apply_aura_effect_never_searches():
    """Rules out 'every aura effect of an area aura spell searches' (IsEffect(APPLY_AURA) -> continue, 2575)."""
    s = raid_scene()
    s.doc["spell"]["effects"].append(effect(1, 6, 40.0))
    assert s.fill(1) == []


# ---------------------------------------------------------------------------
# geometry / radius
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("x,inside", [(41.49, True), (41.5, False)])
def test_radius_extended_by_target_reach_strict(x, inside):
    """Rules out 'plain radius' and '<= at the edge': IsInRange2d adds the target's reach, strict < (Object.cpp:617)."""
    s = Scene([effect(0, 65, 40.0)])
    s.unit("p1", (0, 0, 0), group=RAID0).unit("p2", (x, 0, 0), group=RAID0).friends("p1", "p2").aura("p1", "p1")
    assert ("p2" in s.fill()) is inside


def test_vertical_band_is_max_without_reach():
    """Rules out a 3D sphere: |dz| <= Max (no reach) while 2D uses reach (Spell.cpp:9430)."""
    s = Scene([effect(0, 65, 40.0)])
    s.unit("p1", (0, 0, 0), group=RAID0).unit("p2", (0, 0, 40.0), group=RAID0).unit("p3", (0, 0, 40.01), group=RAID0)
    s.friends("p1", "p2", "p3").aura("p1", "p1")
    assert s.fill() == ["p1", "p2"]


def test_radius_index_zero_is_not_owner_only():
    """RadiusIndex 0 -> {0,0} (SpellInfo.cpp:797-798): units at equal z within their own reach still match;
    rules out 'no radius = self only' and 'no radius = unlimited'."""
    s = Scene([effect(0, 35, None)])
    s.unit("p1", (0, 0, 0), group=PARTY).unit("p2", (1.0, 0, 0), group=PARTY).unit("p3", (1.0, 0, 0.001), group=PARTY)
    s.friends("p1", "p2", "p3").aura("p1", "p1")
    assert s.fill() == ["p1", "p2"]


def test_radius_uses_aura_caster_not_owner():
    """CalcRadius(ref) uses the aura caster's movement / level although the search is centred on the owner
    (SpellAuras.cpp:2581); rules out 'owner-based radius'."""
    s = Scene([effect(0, 35, 10.0)])
    s.unit("p1", (0, 0, 0), group=PARTY).unit("tank", (20, 0, 0), group=PARTY, range_movement_bonus=True)
    s.unit("p2", (11.8, 0, 0), group=PARTY)
    s.friends("tank", "p1", "p2", "tank").aura("p1", "tank")
    assert s.fill() == ["p1", "p2"]                          # 10 + 2 (caster moving) + 1.5 reach
    s.actor("tank")["facts"]["range_movement_bonus"] = False
    assert s.fill() == ["p1"]


def test_caster_missing_falls_back_to_owner():
    """ref = caster ? caster : unitOwner (SpellAuras.cpp:2547): check caster and radius owner switch."""
    s = raid_scene()
    s.aura("p1", None)
    t = Trace()
    assert s.fill(0, t) == ["p1", "p2", "p3", "pet3"]
    assert next(x for x in t.stages if x.name == "auramap.search").inputs["caster"] == "p1"


def test_hostile_owner_party_aura_is_empty():
    """Deathmark-like: the PARTY aura is owned by the enemy target, ref = the rogue; the check needs the rogue to
    assist each candidate *and* the enemy to be in party with it -> nothing (rules out 'applies to the target')."""
    s = Scene([effect(0, 6, None, target_a=6), effect(1, 35, None, target_a=6)])
    s.unit("rogue", (5, 0, 0)).unit("enemy", (0, 0, 0), kind="creature").foes("rogue", "enemy")
    s.aura("enemy", "rogue", statics={"enemy": [0]})
    assert s.fill(1) == []
    assert s.map() == {"enemy": 1}


# ---------------------------------------------------------------------------
# owner state
# ---------------------------------------------------------------------------
def test_disable_while_dead_clears_everything_banish_keeps_statics():
    """DISABLE_AURA_WHILE_DEAD returns before static applications (2543); banished returns after (2566)."""
    s = Scene([effect(0, 35), effect(1, 6, None)], attributes=["SPELL_ATTR7_DISABLE_AURA_WHILE_DEAD"])
    s.unit("p1", (0, 0, 0), group=PARTY, alive=False).aura("p1", "p1", statics={"p1": [1]})
    assert s.map() == {}
    s.actor("p1")["alive"] = True
    s.actor("p1")["facts"]["banished"] = True
    assert s.map() == {"p1": 2}


def test_static_application_with_area_bit_fails_closed():
    s = raid_scene()
    s.aura("p1", "p1", statics={"p1": [0]})
    with pytest.raises(FailClosed):
        s.map()


def test_conditions_fail_closed():
    s = raid_scene()
    s.doc["spell"]["effects"][0]["conditions"] = "c1"
    with pytest.raises(FailClosed):
        s.fill()


def test_missing_visit_order_fails_closed():
    s = raid_scene()
    s.doc["visit_order"] = None
    with pytest.raises(FailClosed):
        s.fill()


# ---------------------------------------------------------------------------
# update
# ---------------------------------------------------------------------------
def test_unit_leaving_range_is_removed_next_update():
    """Rules out 'area auras persist until expiry': units missing from the refreshed map are unapplied (678)."""
    s = raid_scene()
    s.aura("p1", "p1", apps={"p1": [0], "p2": [0], "p3": [0], "pet3": [0]})
    assert s.update() == {"remove": [], "create": {}, "update": {}, "keep": ["p1", "p2", "p3", "pet3"]}
    s.actor("p2")["pos"] = [60, 0, 0]
    assert s.update()["remove"] == ["p2"]


def test_new_immune_and_label_suppressed_units_not_created():
    """Immunity / label suppression gate new applications (711-718)."""
    s = raid_scene()
    s.actor("p2")["facts"]["aura_immune"] = True
    s.actor("p3")["facts"]["suppressed_by_label"] = True
    s.actor("pet3")["facts"]["aura_immune_effects"] = [0]
    assert s.update()["create"] == {"p1": [0]}


def test_existing_application_not_highest_exclusive_is_kept_unchanged():
    """A changed-mask existing application that fails IsHighestExclusiveAura is neither updated nor removed
    (721, 749 erase it from `targets` only); rules out 'fails exclusivity -> removed'."""
    s = Scene([effect(0, 65), effect(1, 65)])
    s.unit("p1", (0, 0, 0), group=RAID0).unit("p2", (3, 0, 0), group=RAID0, auras=[{"spell": 1, "caster": "z"}],
                                                highest_exclusive=False)
    s.friends("p1", "p2").aura("p1", "p1", apps={"p1": [0, 1], "p2": [0]})
    assert s.update() == {"remove": [], "create": {}, "update": {}, "keep": ["p1"]}


def test_existing_immunity_removes_even_when_still_in_range():
    s = raid_scene()
    s.actor("p2")["facts"]["aura_immune_existing"] = True
    s.aura("p1", "p1", apps={"p2": [0]})
    assert s.update()["remove"] == ["p2"]


def test_unit_aura_stacking_needs_fact_dynobj_rule_is_exact():
    """Foreign auras: unit auras fail closed without `can_stack`; dynobj auras apply the same-caster/same-spell rule."""
    s = raid_scene()
    s.actor("p2")["auras"] = [{"spell": 5, "caster": "q", "owner": "q"}]
    s.actor("p2")["facts"]["highest_exclusive"] = True
    with pytest.raises(FailClosed):
        s.update()
    s.actor("p2")["facts"]["can_stack"] = False
    assert "p2" not in s.update()["create"]


def test_owner_skips_stacking_check():
    """`itr->first != GetOwner()` (732): the owner is created even when it states non-stackable auras."""
    s = raid_scene()
    s.actor("p1")["auras"] = [{"spell": 5, "caster": "q", "owner": "q"}]
    s.actor("p1")["facts"].update(highest_exclusive=True)
    assert "p1" in s.update()["create"]


@pytest.mark.parametrize("interval,diff,ran,after", [(0, 16, True, 500), (500, 100, False, 400),
                                                     (100, 100, True, 500), (101, 100, False, 1)])
def test_update_cadence(interval, diff, ran, after):
    """Mirrors SpellAuras.cpp:839-842 (``<=``, reset to 500); rules out '<' and 'per-tick' refresh."""
    assert J.next_update(interval, diff) == (ran, after)


# ---------------------------------------------------------------------------
# dynobj
# ---------------------------------------------------------------------------
def dyn_scene(target_b: int = 28, attributes=None) -> Scene:
    s = Scene([effect(0, 27, 8.0, target_a=87, target_b=target_b)], attributes=attributes)
    s.unit("w", (20, 0, 0)).dynobj("d", (0, 0, 0), "w", 8.0)
    s.unit("e1", (7, 0, 0), kind="creature").unit("p2", (1, 0, 0)).unit("e2", (2, 0, 0), kind="creature")
    s.foes("w", "e1", "e2").friends("w", "p2")
    for u in ("w", "e1", "p2", "e2"):
        s.rel("d", u, in_same_phase=u != "e2")
    s.aura("d", "w")
    return s


def test_dynobj_check_type_from_target_b_dest():
    """Rules out 'TargetA check' when TargetB references DEST (SpellAuras.cpp:2721-2723) and the phase-free
    spell searcher (dynobj searcher phase, 2729)."""
    assert dyn_scene(28).fill() == ["e1"]
    assert dyn_scene(29).fill() == ["p2"]            # DEST_DYNOBJ_ALLY


def test_dynobj_ignores_players_only_effect_attribute():
    """Rules out 'PlayersOnly applies to persistent area auras': DynObjAura never calls GetSearcherTypeMask, and
    CheckTarget re-checks only the spell attributes (SpellInfo.cpp:2443-2450), so NOT_ON_PLAYER still holds."""
    s = dyn_scene(28)
    s.doc["spell"]["effects"][0]["attributes"] = ["PlayersOnly"]
    assert s.fill() == ["e1"]
    u = raid_scene()
    u.doc["spell"]["effects"][0]["attributes"] = ["PlayersOnly"]
    assert "pet3" not in u.fill()
    assert dyn_scene(29, attributes=["SPELL_ATTR5_NOT_ON_PLAYER"]).fill() == []


def test_dynobj_radius_is_max_over_paa_effects_at_creation():
    """SpellEffects.cpp:1533-1538: max CalcRadius(caster).Max over PAA effects up to the handling one."""
    s = Scene([effect(0, 27, 8.0, target_a=87, target_b=28), effect(1, 27, 5.0, target_a=87, target_b=28),
               effect(2, 27, 12.0, target_a=87, target_b=28)])
    s.unit("w", (0, 0, 0), range_movement_bonus=True).aura("w", "w")
    w = s.world()
    assert J.dynobj_radius(w, for_world(w), 1, "w") == 10.0
    assert J.dynobj_radius(w, for_world(w), 2, "w") == 14.0


def test_dynobj_same_caster_same_spell_does_not_stack():
    s = dyn_scene()
    s.actor("e1")["auras"] = [{"spell": 990001, "caster": "w", "owner": "d_old"}]
    s.actor("e1")["facts"]["highest_exclusive"] = True
    assert s.update()["create"] == {}
    s.actor("e1")["auras"] = [{"spell": 990001, "caster": "other", "owner": "d_old"}]
    assert s.update()["create"] == {"e1": [0]}
