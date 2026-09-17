"""Track F: party / raid / summoned membership and caster relationships (synthetic fixtures)."""

from __future__ import annotations

import pytest

from targeting import FailClosed
from targeting import groups as g
from targeting.fixture import World
from targeting.trace import Trace


def actor(aid, kind, **kw):
    base = {"id": aid, "kind": kind, "owner": None, "charmer": None}
    if kind == "player":
        base["group"] = None
    base.update(kw)
    return base


def world(actors, caster="p1", **kw):
    return World.from_dict({"schema": "targeting-fixture/1", "name": "groups", "spell": {}, "caster": caster,
                            "actors": actors, **kw})


RAID = {"id": "G1", "subgroup": 0, "raid": True}
RAID_SG1 = {"id": "G1", "subgroup": 1, "raid": True}


@pytest.fixture
def raid_world():
    return world([
        actor("p1", "player", group=RAID, facts={"class": 1}),
        actor("p2", "player", group=RAID, facts={"class": 1}),
        actor("p3", "player", group=RAID_SG1, facts={"class": 2}),
        actor("p4", "player", group=None, facts={"class": 1}),
        actor("pet1", "pet", owner="p1", summoner="p1", facts={"class": 1}),
        actor("pet3", "pet", owner="p3", summoner="p3", facts={"class": 1}),
        actor("tot1", "totem", owner="p1", summoner="p1", facts={"class": 1}),
        actor("gpet", "guardian", owner="pet1", summoner="pet1", facts={"class": 1}),
        actor("mob", "creature", facts={"treated_as_raid_unit": False, "faction": 14, "is_summon": False, "class": 1}),
        actor("npc", "creature", facts={"treated_as_raid_unit": True, "faction": 35, "is_summon": False, "class": 1}),
        actor("charmed", "creature", charmer="p1", facts={"is_summon": False, "class": 1}),
    ])


def test_party_is_same_group_and_same_subgroup(raid_world):
    """Rules out 'party = same group' (subgroup ignored): p3 is in the raid but another subgroup."""
    w = raid_world
    assert g.is_in_party_with(w, "p1", "p2")
    assert not g.is_in_party_with(w, "p1", "p3")
    assert g.is_in_raid_with(w, "p1", "p3")


def test_raid_flag_is_not_read():
    """Rules out a model where a non-raid party is not a 'raid': IsInSameRaidWith only compares the Group."""
    party = {"id": "G2", "subgroup": 0, "raid": False}
    w = world([actor("p1", "player", group=party), actor("p2", "player", group=party)])
    assert g.is_in_raid_with(w, "p1", "p2")


def test_ungrouped_player_party_is_self_and_own_units(raid_world):
    """Rules out 'ungrouped => nobody': self and one-level owned/charmed units are party members."""
    w = raid_world
    assert g.is_in_party_with(w, "p4", "p4")
    assert not g.is_in_party_with(w, "p4", "p1")
    assert g.is_in_party_with(w, "p1", "pet1")
    assert g.is_in_party_with(w, "p1", "charmed")
    assert g.is_in_party_with(w, "p1", "tot1")          # membership true; the check excludes totems separately


def test_pets_resolve_through_owner(raid_world):
    """Rules out 'pets never pass group checks': pet of a raid member is in raid with the caster."""
    w = raid_world
    assert g.is_in_raid_with(w, "p1", "pet3")
    assert not g.is_in_party_with(w, "p1", "pet3")      # owner p3 is another subgroup
    assert g.is_in_party_with(w, "pet1", "p2")          # pet as the referer (pet caster)


def test_owner_of_owner_is_one_level_only(raid_world):
    """Rules out recursive owner resolution: a guardian owned by a pet resolves to the pet (a creature)."""
    w = raid_world
    t = Trace()
    w.actor("pet1").facts["treated_as_raid_unit"] = False
    assert not g.is_in_raid_with(w, "p1", "gpet", t)
    # pet1 resolves to p1, gpet resolves to pet1: player vs creature -> not even the owning pet
    assert not g.is_in_raid_with(w, "pet1", "gpet")
    assert g.caster_object_target(w, 27, "gpet")[0] == "pet1"


def test_creature_membership_rules(raid_world):
    """Raid-unit flag decides player/creature pairs; creature pairs compare faction."""
    w = raid_world
    assert g.is_in_raid_with(w, "p1", "npc")
    assert not g.is_in_raid_with(w, "p1", "mob")
    w2 = world([actor("c1", "creature", facts={"faction": 7}), actor("c2", "creature", facts={"faction": 7}),
                actor("c3", "creature", facts={"faction": 8})], caster="c1")
    assert g.is_in_party_with(w2, "c1", "c2")
    assert not g.is_in_party_with(w2, "c1", "c3")


def test_missing_group_fact_fails_closed():
    w = world([{"id": "p1", "kind": "player", "owner": None, "charmer": None},
               actor("p2", "player", group=RAID)])
    with pytest.raises(FailClosed):
        g.is_in_raid_with(w, "p1", "p2")
    w = world([{"id": "p1", "kind": "player", "group": None}, actor("p2", "player", group=RAID)])
    with pytest.raises(FailClosed):
        g.is_in_raid_with(w, "p1", "p2")      # owner/charmer links not stated


def _check(w, target, check, referer="p1", caster="p1", assist=lambda c, t: True, ctarget=lambda t: True):
    return g.group_check(w, caster, referer, target, check, ctarget, assist)


def test_check_order_and_totem_exclusion(raid_world):
    """PARTY/RAID exclude totems; SUMMONED does not (rules out a uniform totem filter)."""
    w = raid_world
    assert not _check(w, "tot1", "RAID")
    assert _check(w, "tot1", "SUMMONED")
    assert _check(w, "pet1", "SUMMONED")
    assert not _check(w, "gpet", "SUMMONED")             # summoned by pet1, not by the caster
    assert _check(w, "gpet", "SUMMONED", caster="pet1")


def test_membership_after_assist(raid_world):
    """A hostile raid member (duel) fails on assist before membership is consulted."""
    w = raid_world
    t = Trace()
    ok = g.group_check(w, "p1", "p1", "p2", "RAID", lambda _t: True, lambda c, x: False, trace=t)
    assert not ok
    assert not any(s.name == "groups.is_in_raid_with" for s in t.stages)


def test_raid_class_uses_referer_class(raid_world):
    """Rules out 'caster class': the referer (explicit target for selector 61) supplies the class."""
    w = raid_world
    assert _check(w, "p2", "RAID_CLASS", referer="p1")
    assert not _check(w, "p3", "RAID_CLASS", referer="p1")
    assert _check(w, "p3", "RAID_CLASS", referer="p3")
    assert _check(w, "pet1", "RAID_CLASS", referer="p1")  # pet unit_class 1 == warrior class 1 (quirk)


def test_null_referer_rejects_membership(raid_world):
    assert not _check(raid_world, "p2", "PARTY", referer=None)


def test_corpse_uses_owner_player():
    w = world([actor("p1", "player", group=RAID, facts={"class": 5}),
               actor("p2", "player", group=RAID, alive=False, facts={"class": 5}),
               {"id": "c2", "kind": "corpse", "owner": "p2"},
               {"id": "c9", "kind": "corpse", "owner": None}])
    assert g.group_check(w, "p1", "p1", "c2", "RAID", lambda t: True, lambda c, t: False, object_type="CORPSE")
    assert not g.group_check(w, "p1", "p1", "c9", "RAID", lambda t: True, lambda c, t: True, object_type="CORPSE")


def test_ally_or_raid_mode(raid_world):
    """Rules out 'always area': an out-of-raid target is the only recipient."""
    w = raid_world
    assert g.ally_or_raid_mode(w, "p1", "p3") == "search"
    assert g.ally_or_raid_mode(w, "p1", "p4") == "explicit-only"
    assert g.ally_or_raid_mode(w, "p1", None) == "none"


def test_caster_object_targets():
    w = world([actor("p1", "player", group=None, facts={"pet": "pet1"}),
               actor("pet1", "pet", owner="p1", summoner="p1", charmer=None, facts={"pet": None}),
               actor("m", "minion", owner="p1", summoner="p1"),
               actor("c", "creature", charmer="p2", owner="p1", facts={"is_summon": False}),
               actor("p2", "player", group=None, facts={"pet": "m"})])
    assert g.caster_object_target(w, 1, "p1") == ("p1", False)
    assert g.caster_object_target(w, 5, "p1") == ("pet1", True)
    assert g.caster_object_target(w, 5, "p2") == (None, True)      # PetGUID names a non-guardian
    assert g.caster_object_target(w, 27, "pet1") == ("p1", True)
    assert g.caster_object_target(w, 27, "c") == ("p2", True)      # charmer before owner
    assert g.caster_object_target(w, 92, "pet1") == ("p1", True)
    assert g.caster_object_target(w, 92, "p1") == (None, True)
    assert g.spell_caster(w.__class__.from_dict({**w.raw, "caster": "pet1"}), True) == "p1"
    assert g.spell_caster(w, False) == "p1"
