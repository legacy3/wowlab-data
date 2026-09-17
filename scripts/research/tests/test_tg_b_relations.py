"""Track B: relation predicates (GetReactionTo / IsValidAttackTarget / IsValidAssistTarget /
WorldObjectSpellTargetCheck relation part).  Synthetic fixtures discriminate competing models;
the real-data tests use the DB2 FactionTemplate rows and the TDB training-dummy templates."""

from __future__ import annotations

import copy

import pytest

from targeting import FailClosed
from targeting import relations as R
from targeting.fixture import World
from targeting.trace import Trace

TEMPLATES = {
    "1": {"faction": 1, "flags": 72, "faction_group": 3, "friend_group": 2, "enemy_group": 12,
          "enemies": [0] * 8, "friends": [0] * 8},
    "2": {"faction": 2, "flags": 72, "faction_group": 5, "friend_group": 4, "enemy_group": 10,
          "enemies": [0] * 8, "friends": [0] * 8},
    "7": {"faction": 7, "flags": 0, "faction_group": 0, "friend_group": 0, "enemy_group": 0,
          "enemies": [0] * 8, "friends": [0] * 8},
    "11": {"faction": 72, "flags": 2081, "faction_group": 3, "friend_group": 2, "enemy_group": 12,
           "enemies": [0] * 8, "friends": [0] * 8},
    "14": {"faction": 14, "flags": 0, "faction_group": 8, "friend_group": 0, "enemy_group": 1,
           "enemies": [0] * 8, "friends": [0] * 8},
    "35": {"faction": 31, "flags": 0, "faction_group": 0, "friend_group": 1, "enemy_group": 0,
           "enemies": [0] * 8, "friends": [31] + [0] * 7},
}
FACTIONS = {"1": {"reputation_index": -1}, "2": {"reputation_index": -1}, "7": {"reputation_index": -1},
            "14": {"reputation_index": -1}, "31": {"reputation_index": -1}, "72": {"reputation_index": 19}}

HARMFUL = {"attributes": [], "is_positive": False, "is_affecting_area": False, "is_allowing_dead_target": False}
HELPFUL = dict(HARMFUL, is_positive=True)


def actor(aid, kind, tpl, flags=None, **kw):
    facts = {"profile": "combat-sim", "faction_template": tpl}
    if kind == "creature":
        facts["is_summon"] = False
    if kind != "player":   # world-DB TypeFlags facts must be stated (R1-03)
        facts["treated_as_raid_unit"] = False
        facts["creature_type_flag_can_assist"] = False
    if flags is not None:
        facts["unit_flags"] = flags
    facts.update(kw.pop("facts", {}))
    out = {"id": aid, "kind": kind, "alive": kw.pop("alive", True), "facts": facts}
    out.update(kw)
    return out


def world(actors, relations=(), **kw):
    data = {"schema": "targeting-fixture/1", "name": "b", "spell": {}, "caster": actors[0]["id"],
            "actors": actors, "relations": list(relations),
            "faction_templates": copy.deepcopy(TEMPLATES), "factions": copy.deepcopy(FACTIONS)}
    data.update(kw)
    return World.from_dict(data)


def test_same_owner_is_friendly_even_when_templates_are_hostile():
    """Rules out 'reaction = faction-template compare': a pet carrying hostile template 14 is
    FRIENDLY to its owner through GetCharmerOrOwnerOrSelf equality (Object.cpp:2061)."""
    w = world([actor("p", "player", 1, group=None), actor("pet", "pet", 14, ["PLAYER_CONTROLLED"], owner="p")])
    assert R.template_is_hostile_to(dict(TEMPLATES["14"], id=14), dict(TEMPLATES["1"], id=1))
    t = Trace()
    assert R.reaction(w, "p", "pet", t) == R.REP_FRIENDLY
    assert "2061:same-charmer-or-owner-or-self" in t.stages[-1].notes
    assert not R.valid_attack(w, "p", "pet", HARMFUL)


def test_charmed_player_resolves_to_charmer():
    """Rules out 'a player's reaction is its own template': a charmed Alliance player is friendly
    to its hostile charmer and hostile-or-not is decided by the charmer (Unit.h:1220)."""
    w = world([actor("m", "creature", 14, []),
               actor("p", "player", 1, charmer="m", group=None),
               actor("q", "player", 1, group=None)])
    assert R.reaction(w, "m", "p") == R.REP_FRIENDLY
    assert R.affecting_player(w, "p") is None          # charmer is a creature
    # q -> p: p has no affecting player; p is PLAYER_CONTROLLED but spo/tpo mix => template path of q (1) vs p (1)
    assert R.reaction(w, "q", "p") == R.REP_FRIENDLY


def test_player_vs_training_dummy_is_neutral_attackable_not_assistable():
    """Template 7 (training dummy) is NEUTRAL both ways; attack valid (no reputation state), assist
    invalid through the PvC branch (Object.cpp:2596) -- rules out 'neutral => both'."""
    w = world([actor("p", "player", 1, group=None), actor("d", "creature", 7, [])])
    assert R.reaction(w, "p", "d") == R.REP_NEUTRAL
    assert R.reaction(w, "d", "p") == R.REP_NEUTRAL
    assert not R.is_hostile(w, "p", "d")
    assert R.valid_attack(w, "p", "d", HARMFUL)
    t = Trace()
    assert not R.valid_assist(w, "p", "d", HELPFUL, t)
    assert "2596:PvC-raid-unit-or-can-assist" in t.stages[-1].notes


def test_friendly_template_dummy_is_not_attackable():
    """Template 35 (dummies 30527/31143) is friendly to the player group -> IsValidAttackTarget false."""
    w = world([actor("p", "player", 1, group=None), actor("d", "creature", 35, [])])
    assert R.reaction(w, "p", "d") == R.REP_FRIENDLY
    assert not R.valid_attack(w, "p", "d", HARMFUL)


def test_boss_template_hostile():
    w = world([actor("p", "player", 1, group=None), actor("b", "creature", 14, [])])
    assert R.reaction(w, "p", "b") == R.REP_HOSTILE      # target template enemy group bit
    assert R.reaction(w, "b", "p") == R.REP_HOSTILE
    assert R.valid_attack(w, "p", "b", HARMFUL)


def test_reputation_faction_not_at_war_blocks_attack():
    """PvC with a rep-capable faction (72 Stormwind, template 11) and a Horde player: the player's
    template reaction is HOSTILE, yet attack is refused while the reputation state is not AtWar
    (Object.cpp:2453) -- rules out 'hostile reaction => attackable'.  Alliance players never get
    there (template-friendly, 2428).  Unstated reputation fails closed."""
    base = [actor("p", "player", 2, group=None), actor("g", "creature", 11, [])]
    with pytest.raises(FailClosed):
        R.valid_attack(world(copy.deepcopy(base)), "p", "g", HARMFUL)
    a = copy.deepcopy(base)
    a[0]["facts"]["reputation"] = {"72": {"rank": "NEUTRAL", "at_war": False}}
    w = world(a)
    assert R.reaction(w, "p", "g") == R.REP_HOSTILE
    assert R.reaction(w, "g", "p") == R.REP_NEUTRAL
    t = Trace()
    assert not R.valid_attack(w, "p", "g", HARMFUL, t)
    assert t.stages[-1].notes[-1] == "2453:rep-not-at-war"
    a[0]["facts"]["reputation"] = {"72": {"rank": "FRIENDLY", "at_war": True}}
    w = world(a)
    assert R.reaction(w, "g", "p") == R.REP_NEUTRAL          # at war caps the rank at NEUTRAL (2168)
    assert R.valid_attack(w, "p", "g", HARMFUL)
    ally = [actor("p", "player", 1, group=None), actor("g", "creature", 11, [])]
    assert not R.valid_attack(world(ally), "p", "g", HARMFUL)


def test_creature_vs_creature_needs_hostility_either_way():
    """CvC (Object.cpp:2415) returns hostile-either-way, so two neutral creatures cannot fight while
    a player-controlled pet (not CvC) can attack the same neutral creature."""
    w = world([actor("c1", "creature", 7, []), actor("c2", "creature", 7, []),
               actor("p", "player", 1, group=None), actor("pet", "pet", 1, ["PLAYER_CONTROLLED"], owner="p")])
    assert not R.valid_attack(w, "c1", "c2", HARMFUL)
    assert R.valid_attack(w, "pet", "c2", HARMFUL)


def test_dead_target_depends_on_spell_allowing_dead():
    w = world([actor("p", "player", 1, group=None), actor("d", "creature", 14, [], alive=False)])
    assert not R.valid_attack(w, "p", "d", HARMFUL)
    assert R.valid_attack(w, "p", "d", dict(HARMFUL, is_allowing_dead_target=True))
    assert not R.valid_attack(w, "p", "d", None)        # melee/no-spell path never allows dead


def test_self_attack_only_rejected_without_spell():
    """Object.cpp:2339 rejects self only when bySpell is null; with a spell the self case falls
    through to IsFriendlyTo (self is always FRIENDLY) and is rejected there instead."""
    w = world([actor("p", "player", 1, group=None)])
    t = Trace()
    assert not R.valid_attack(w, "p", "p", None, t)
    assert t.stages[-1].notes == ["2339:self-without-spell"]
    t = Trace()
    assert not R.valid_attack(w, "p", "p", HARMFUL, t)
    assert t.stages[-1].notes[-1] == "2428:friendly-either-way"
    assert R.valid_assist(w, "p", "p", HARMFUL)


def test_sanctuary_attribute_polarity_is_inverted():
    """Likely defect TG-B-D1 (Object.cpp:2469): the sanctuary block applies only when the spell HAS
    SPELL_ATTR8_IGNORE_SANCTUARY (or no spell).  The oracle reproduces and marks it."""
    pvp = {"pvp_flags": ["PVP"]}
    sanct = {"pvp_flags": ["PVP", "SANCTUARY"]}
    w = world([actor("a", "player", 1, facts=sanct, group=None), actor("b", "player", 2, facts=pvp, group=None)])
    assert R.valid_attack(w, "a", "b", HARMFUL)                       # sanctuary ignored!
    t = Trace()
    ignore = dict(HARMFUL, attributes=["SPELL_ATTR8_IGNORE_SANCTUARY"])
    assert not R.valid_attack(w, "a", "b", ignore, t)
    assert t.stages[-1].defect == "TG-B-D1"
    assert not R.valid_attack(w, "a", "b", None)


def test_assist_requires_unfriendly_both_ways():
    """IsValidAssistTarget rejects only when BOTH directions are below NEUTRAL (Object.cpp:2563)."""
    w = world([actor("a", "creature", 7, []), actor("b", "creature", 7, [])],
              relations=[{"from": "a", "to": "b", "reaction": "HOSTILE"},
                         {"from": "b", "to": "a", "reaction": "NEUTRAL"}])
    assert R.valid_assist(w, "a", "b", HELPFUL)
    w2 = world([actor("a", "creature", 7, []), actor("b", "creature", 7, [])],
               relations=[{"from": "a", "to": "b", "reaction": "HOSTILE"},
                          {"from": "b", "to": "a", "reaction": "UNFRIENDLY"}])
    assert not R.valid_assist(w2, "a", "b", HELPFUL)


def test_treated_as_raid_unit_is_assistable():
    w = world([actor("p", "player", 1, group=None),
               actor("n", "creature", 7, [], facts={"treated_as_raid_unit": True})])
    assert R.valid_assist(w, "p", "n", HELPFUL)


def test_typeflag_facts_are_not_defaulted_by_profile():
    """R1-03: CAN_ASSIST / TREAT_AS_RAID_UNIT are world-DB TypeFlags, not absent machinery -- the
    combat-sim profile must not answer them; a current healing dummy (225979, TypeFlags 0x1000,
    template 35) is assistable but not attackable."""
    raw = actor("d", "creature", 35, [])
    del raw["facts"]["creature_type_flag_can_assist"]
    w = world([actor("p", "player", 1, group=None), raw])
    with pytest.raises(FailClosed):
        R.valid_assist(w, "p", "d", HELPFUL)
    w.actors["d"].facts["creature_type_flag_can_assist"] = True
    assert not R.valid_attack(w, "p", "d", HARMFUL)
    assert R.valid_assist(w, "p", "d", HELPFUL)


def test_faction_zero_is_loaded_as_35():
    """ObjectMgr.cpp:1022-1027: creature_template.faction 0 has no FactionTemplate row -> 35 (FRIENDLY),
    so current damage dummies such as 194648 are NOT attackable in Trinity."""
    assert R.loaded_creature_faction({1, 7, 35}, 0) == (35, True)
    assert R.loaded_creature_faction({1, 7, 35}, 7) == (7, False)


def test_missing_fact_fails_closed_without_profile():
    w = World.from_dict({"schema": "targeting-fixture/1", "caster": "p", "actors": [
        {"id": "p", "kind": "player", "alive": True, "facts": {"faction_template": 1}},
        {"id": "d", "kind": "creature", "alive": True, "facts": {"faction_template": 7, "unit_flags": []}}],
        "faction_templates": TEMPLATES, "factions": FACTIONS})
    with pytest.raises(FailClosed):
        R.valid_attack(w, "p", "d", HARMFUL)


def _raid():
    return world([
        actor("p1", "player", 1, group={"id": "g", "subgroup": 1}, facts={"class": 5}),
        actor("p2", "player", 1, group={"id": "g", "subgroup": 2}, facts={"class": 5}),
        actor("p3", "player", 1, group={"id": "g", "subgroup": 2}, facts={"class": 1}),
        actor("pet3", "pet", 1, ["PLAYER_CONTROLLED"], owner="p3"),
        actor("tot", "totem", 1, ["PLAYER_CONTROLLED"], owner="p2"),
        actor("x", "player", 1, group=None),
        actor("boss", "creature", 14, []),
        actor("g1", "guardian", 1, ["PLAYER_CONTROLLED"], owner="p1", summoner="p1"),
    ])


@pytest.mark.parametrize("check,target,referer,expected", [
    ("PARTY", "p2", None, False),       # other subgroup
    ("PARTY", "p3", "p2", True),        # referer's subgroup (area referer, e.g. ALLY_OR_RAID)
    ("PARTY", "pet3", "p2", True),      # pet resolved to owner p3 (one hop)
    ("RAID", "p3", None, True),
    ("RAID", "x", None, False),
    ("RAID", "tot", None, False),       # totems excluded before membership
    ("RAID_CLASS", "p2", None, True),
    ("RAID_CLASS", "p3", None, False),  # class compared before anything else
    ("ALLY", "boss", None, False),
    ("ENEMY", "boss", None, True),
    ("ENEMY", "p2", None, False),
    ("ENTRY", "boss", None, True),      # no relation check at all (conditions decide)
    ("DEFAULT", "p2", None, True),
    ("PASSENGER", "boss", None, True),  # no relation check in WorldObjectSpellTargetCheck
    ("SUMMONED", "g1", None, True),
    ("SUMMONED", "pet3", None, False),
])
def test_check_types(check, target, referer, expected):
    """Rules out 'membership decided by caster': PARTY/RAID use _referer (Spell.cpp:9349/9366)."""
    w = _raid()
    assert R.check(w, HELPFUL if check not in ("ENEMY",) else HARMFUL, "p1", target, check, Trace(),
                   referer_id=referer) is expected


def test_absent_check_types_fail_closed():
    w = _raid()
    for name in ("THREAT", "TAP"):
        with pytest.raises(FailClosed):
            R.check(w, HELPFUL, "p1", "p2", name, None)


def test_corpse_uses_owner_and_skips_relation():
    w = world([actor("p", "player", 1, group={"id": "g", "subgroup": 1}),
               actor("q", "player", 2, group=None, alive=False),
               {"id": "c", "kind": "corpse", "owner": "q", "facts": {}}])
    # q is hostile (Horde) and not in the party: ALLY passes (corpse skips IsValidAssist), PARTY fails on membership
    assert R.check(w, HELPFUL, "p", "c", "ALLY", None, object_type="CORPSE_ALLY") is True
    assert R.check(w, HELPFUL, "p", "c", "PARTY", None) is False


# -- real data -----------------------------------------------------------------
def test_real_faction_rows_and_dummy_reactions(tg_ctx):
    from targeting.relations import faction_rows_from_db2
    tpl, fac = faction_rows_from_db2(tg_ctx.bundle.source, [1, 2, 7, 14, 35, 11])
    assert tpl == TEMPLATES and {k: fac[k] for k in FACTIONS} == FACTIONS
