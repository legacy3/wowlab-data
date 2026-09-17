"""Relation predicates: reaction, hostile/friendly, valid attack/assist, and the
relation part of ``WorldObjectSpellTargetCheck``.

Pinned consumers (TrinityCore 7f3d43b)
--------------------------------------
* ``WorldObject::GetReactionTo``          Entities/Object/Object.cpp:2035
* ``WorldObject::GetFactionReactionTo``   Entities/Object/Object.cpp:2141
* ``WorldObject::IsHostileTo``            Entities/Object/Object.cpp:2188  (``<= REP_HOSTILE``)
* ``WorldObject::IsFriendlyTo``           Entities/Object/Object.cpp:2193  (``>= REP_FRIENDLY``)
* ``WorldObject::IsValidAttackTarget``    Entities/Object/Object.cpp:2331
* ``WorldObject::IsValidAssistTarget``    Entities/Object/Object.cpp:2489
* ``FactionTemplateEntry::IsFriendlyTo/IsHostileTo``  DataStores/DB2Structure.h:1720/1735
* ``WorldObject::GetCharmerOrOwnerOrSelf`` Object.cpp:1620, ``GetAffectingPlayer`` Object.cpp:1637,
  ``GetCharmerOrOwnerPlayerOrPlayerItself`` Object.cpp:1628, ``Unit::GetCharmerOrOwner`` Unit.h:1220
* ``WorldObjectSpellTargetCheck::operator()`` Spells/Spell.cpp:9306 (relation part: 9311-9390)

These are *WorldObject* methods at the pin (not Unit), so gameobject casters
(traps) take the same path with ``ToUnit() == nullptr``.

Fixture facts (``Actor.facts``) read here
-----------------------------------------
``unit_flags`` (names, e.g. ``PLAYER_CONTROLLED``, ``NON_ATTACKABLE``, ``NON_ATTACKABLE_2``,
``NOT_ATTACKABLE_1``, ``ON_TAXI``, ``IMMUNE_TO_PC``, ``IMMUNE_TO_NPC``, ``UNINTERACTIBLE``,
``PET_IN_COMBAT``), ``unit_flags2`` (``IGNORE_REPUTATION``), ``pvp_flags`` (``PVP``, ``FFA_PVP``,
``SANCTUARY``, ``UNK1``), ``player_flags`` (``CONTESTED_PVP``, ``UBER``), ``faction_template``
(id or ``null`` = no template entry), ``forced_reactions`` ({faction id: rank}), ``reputation``
({faction id: {rank, at_war}}), ``duel`` ({opponent, in_progress} or null), ``game_master``,
``unattackable_state`` (``UNIT_STATE_UNATTACKABLE`` = ``UNIT_STATE_IN_FLIGHT``), ``mounted``,
``vehicle`` (null or {base, passenger_of}), ``attackable_by_summoner`` (SummonProperties flag),
``treated_as_raid_unit`` (CREATURE_STATIC_FLAG_4), ``creature_type_flag_can_assist``,
``go_type`` (gameobjects), ``class`` (RAID_CLASS).  Faction rows come from
``world.raw["faction_templates"]`` / ``world.raw["factions"]`` (DB2 copies; see
:func:`faction_rows_from_db2`).  Visibility is the directed relation fact ``can_see``.
Directed relation rows may *state* ``reaction`` / ``valid_attack`` / ``valid_assist``
directly; a stated value short-circuits the derivation (trace evidence
``fixture-stated``).

The ``combat-sim`` profile (minimal subset)
-------------------------------------------
``facts.profile == "combat-sim"`` declares the world/PvP machinery *absent* for that
actor: no GM/uber, not in flight, no pvp/ffa/sanctuary/contested flags, no forced
reactions, no duel, not mounted, no vehicle, not attackable-by-summoner, no
IGNORE_REPUTATION, visible to every other profiled actor, players carry exactly
``PLAYER_CONTROLLED``, kind ``creature`` is not a summon.  Everything else (faction
template, unit flags of non-players, the creature TypeFlags facts
``treated_as_raid_unit`` / ``creature_type_flag_can_assist`` (world-DB data, R1-03),
reputation of rep-capable factions, class, group) must be stated; unknown =>
:class:`targeting.FailClosed`.

Creature ``faction_template`` must be the *loaded* value: ``ObjectMgr::CheckCreatureTemplate``
(ObjectMgr.cpp:1022-1027) rewrites a template id with no FactionTemplate row (e.g. 0) to 35;
:func:`loaded_creature_faction` applies it for world-DB rows.
"""

from __future__ import annotations

from typing import Any

from . import FailClosed
from .trace import Trace

# -- SharedDefines.h:208 enum ReputationRank --------------------------------
REP_HATED, REP_HOSTILE, REP_UNFRIENDLY, REP_NEUTRAL, REP_FRIENDLY, REP_HONORED, REP_REVERED, REP_EXALTED = range(8)
REP_NAMES = ("HATED", "HOSTILE", "UNFRIENDLY", "NEUTRAL", "FRIENDLY", "HONORED", "REVERED", "EXALTED")

# -- UnitDefines.h enum UnitFlags / UnitFlags2 / UnitPVPStateFlags ------------
UNIT_FLAGS = {
    "SERVER_CONTROLLED": 0x1, "NON_ATTACKABLE": 0x2, "REMOVE_CLIENT_CONTROL": 0x4,
    "PLAYER_CONTROLLED": 0x8, "NOT_ATTACKABLE_1": 0x80, "IMMUNE_TO_PC": 0x100,
    "IMMUNE_TO_NPC": 0x200, "PET_IN_COMBAT": 0x800, "NON_ATTACKABLE_2": 0x10000,
    "PACIFIED": 0x20000, "ON_TAXI": 0x100000, "UNINTERACTIBLE": 0x2000000, "IMMUNE": 0x80000000,
}
UNIT_FLAGS2 = {"IGNORE_REPUTATION": 0x4}
PVP_FLAGS = {"PVP": 0x1, "UNK1": 0x2, "FFA_PVP": 0x4, "SANCTUARY": 0x8}
PLAYER_FLAGS = ("CONTESTED_PVP", "UBER")
# DBCEnums.h:1001-1003
FACTION_TEMPLATE_FLAG_CONTESTED_GUARD = 0x1000
FACTION_TEMPLATE_FLAG_HOSTILE_BY_DEFAULT = 0x2000
# DB2Structure.h / SharedDefines.h: MAX_FACTION_RELATIONS
MAX_FACTION_RELATIONS = 8

# -- SpellInfo.h:79 enum SpellTargetCheckTypes (the complete set at the pin) --
CHECK_TYPES = ("DEFAULT", "ENTRY", "ENEMY", "ALLY", "PARTY", "RAID", "RAID_CLASS", "PASSENGER", "SUMMONED")
#: names the brief listed that do not exist at the pin
ABSENT_CHECK_TYPES = ("THREAT", "TAP")

UNIT_KINDS = ("player", "creature", "pet", "guardian", "totem", "minion", "vehicle")
SUMMON_KINDS = ("pet", "guardian", "totem", "minion")      # TempSummon subclasses (Pet : Guardian : Minion : TempSummon)

PROFILE = "combat-sim"
_PROFILE_DEFAULTS: dict[str, Any] = {
    "unit_flags2": (), "pvp_flags": (), "player_flags": (), "forced_reactions": {}, "duel": None,
    "game_master": False, "unattackable_state": False, "mounted": False, "vehicle": None,
    "attackable_by_summoner": False,
    # NOT defaulted (R1-03): `treated_as_raid_unit` / `creature_type_flag_can_assist` are world-DB data
    # (creature_template_difficulty.TypeFlags, Creature.cpp:588 / Object.cpp:2596) and must be stated.
    # kind `creature` is a plain Creature in combat-sim fixtures; summons use the pet/guardian/totem/minion kinds
    "is_summon": False,
}
_MISSING = object()


# ---------------------------------------------------------------------------
# fact access
# ---------------------------------------------------------------------------
def fact(world, actor_id: str, key: str) -> Any:
    """A relation fact of an actor; ``combat-sim`` profile supplies the absent-machinery defaults."""
    a = world.actor(actor_id)
    if key in a.facts:
        return a.facts[key]
    if a.facts.get("profile") == PROFILE:
        if key in _PROFILE_DEFAULTS:
            return _PROFILE_DEFAULTS[key]
        if key == "unit_flags" and a.kind == "player":
            return ("PLAYER_CONTROLLED",)
    raise FailClosed(f"relations: actor {actor_id!r} does not state {key!r}")


def is_unit(world, actor_id: str) -> bool:
    kind = world.actor(actor_id).kind
    if kind in UNIT_KINDS:
        return True
    if kind in ("corpse", "gameobject", "dynamicobject", "areatrigger"):
        return False
    raise FailClosed(f"relations: kind {kind!r} has no unit classification")


def is_player(world, actor_id: str) -> bool:
    return world.actor(actor_id).kind == "player"


def is_creature(world, actor_id: str) -> bool:
    """``ToCreature()``: every non-player unit (pets/guardians/totems are Creatures)."""
    return is_unit(world, actor_id) and not is_player(world, actor_id)


def is_summon(world, actor_id: str) -> bool:
    """``Unit::IsSummon`` -- single source of truth: :func:`targeting.groups.is_summon` (Track F)."""
    from . import groups
    return groups.is_summon(world, actor_id)


def has_unit_flag(world, actor_id: str, name: str) -> bool:
    if name not in UNIT_FLAGS:
        raise FailClosed(f"relations: unknown unit flag {name!r}")
    flags = fact(world, actor_id, "unit_flags")
    unknown = [f for f in flags if f not in UNIT_FLAGS]
    if unknown:
        raise FailClosed(f"relations: actor {actor_id!r} states unknown unit flags {unknown}")
    return name in flags


def _flag_in(world, actor_id: str, key: str, name: str, vocabulary) -> bool:
    values = fact(world, actor_id, key)
    unknown = [f for f in values if f not in vocabulary]
    if unknown:
        raise FailClosed(f"relations: actor {actor_id!r} states unknown {key} {unknown}")
    return name in values


def has_pvp_flag(world, actor_id: str, name: str) -> bool:
    return _flag_in(world, actor_id, "pvp_flags", name, PVP_FLAGS)


def has_player_flag(world, actor_id: str, name: str) -> bool:
    return _flag_in(world, actor_id, "player_flags", name, PLAYER_FLAGS)


def player_controlled(world, actor_id: str) -> bool:
    return has_unit_flag(world, actor_id, "PLAYER_CONTROLLED")


def _spell_attr(spell, name: str) -> bool:
    if spell is None:
        return False
    if isinstance(spell, dict):
        attrs = spell.get("attributes")
        if attrs is None:
            raise FailClosed(f"relations: spell view states no `attributes` (needs {name})")
        return name in attrs
    try:
        if name.startswith("SPELL_ATTR0_CU_") and hasattr(spell, "has_cu"):
            return bool(spell.has_cu(name))
        return bool(spell.has_attr(name))
    except KeyError as exc:
        raise FailClosed(f"relations: attribute {name}: {exc}") from None


def _spell_value(spell, key: str) -> Any:
    if isinstance(spell, dict):
        if key not in spell:
            raise FailClosed(f"relations: spell view does not state {key!r}")
        return spell[key]
    value = getattr(spell, key, _MISSING)
    if value is _MISSING:
        raise FailClosed(f"relations: spell view does not provide {key!r}")
    return value() if callable(value) else value


def spell_is_positive(spell) -> bool:
    """``SpellInfo::IsPositive`` (SpellInfo.cpp:1879, ``NegativeEffects.none()``) -- stated by the view."""
    return bool(_spell_value(spell, "is_positive"))


def spell_allows_dead(spell) -> bool:
    """``SpellInfo::IsAllowingDeadTarget`` (SpellInfo.cpp:1838) -- stated or computed by the view."""
    return bool(_spell_value(spell, "is_allowing_dead_target"))


# ---------------------------------------------------------------------------
# ownership resolution
# ---------------------------------------------------------------------------
def charmer_or_owner(world, actor_id: str) -> str | None:
    """Mirrors: Unit.h:1220 ``IsCharmed() ? GetCharmer() : GetOwner()``; Object.cpp:1610 (GO -> owner).

    Delegates to :func:`targeting.groups.charmer_or_owner` (single source of truth for the link facts:
    ``owner``/``charmer`` keys must be present unless the actor uses the ``combat-sim`` profile).
    Other non-unit kinds (corpse, dynobj, areatrigger) have no charmer/owner here (``nullptr``).
    """
    from . import groups
    kind = world.actor(actor_id).kind
    if is_unit(world, actor_id) or kind == "gameobject":
        return groups.charmer_or_owner(world, actor_id)
    return None


def charmer_or_owner_or_self(world, actor_id: str) -> str | None:
    """Mirrors: Object.cpp:1620 -- owner, else self if a Unit, else nullptr.

    NB differs from ``groups.charmer_or_owner_or_self`` for ownerless non-units (Trinity returns
    ``nullptr`` via ``ToUnit()``; that nullptr feeds the reaction short-circuit at Object.cpp:2061).
    """
    owner = charmer_or_owner(world, actor_id)
    if owner is not None:
        return owner
    return actor_id if is_unit(world, actor_id) else None


def _charmer_or_owner_player_or_player_itself(world, actor_id: str) -> str | None:
    """Mirrors: Object.cpp:1628."""
    guid = charmer_or_owner(world, actor_id)
    if guid is not None and is_player(world, guid):
        return guid
    return actor_id if is_player(world, actor_id) else None


def affecting_player(world, actor_id: str) -> str | None:
    """Mirrors: Object.cpp:1637 ``WorldObject::GetAffectingPlayer`` (one owner hop, then owner's owner-player)."""
    if charmer_or_owner(world, actor_id) is None:
        return actor_id if is_player(world, actor_id) else None
    owner = charmer_or_owner(world, actor_id)
    return _charmer_or_owner_player_or_player_itself(world, owner)


# ---------------------------------------------------------------------------
# faction templates (DB2 facts; DB2Structure.h helpers)
# ---------------------------------------------------------------------------
def faction_rows_from_db2(source, template_ids) -> tuple[dict, dict]:
    """Copy FactionTemplate / Faction rows for a fixture (``world.raw['faction_templates'|'factions']``)."""
    tcols = ("ID", "Faction", "Flags", "FactionGroup", "FriendGroup", "EnemyGroup") + \
        tuple(f"Enemies_{i}" for i in range(MAX_FACTION_RELATIONS)) + tuple(f"Friend_{i}" for i in range(MAX_FACTION_RELATIONS))
    wanted = {int(t) for t in template_ids}
    templates = {}
    for r in source.project("FactionTemplate", tcols):
        row = dict(zip(tcols, r))
        if int(row["ID"]) in wanted:
            templates[str(row["ID"])] = {
                "faction": int(row["Faction"]), "flags": int(row["Flags"]),
                "faction_group": int(row["FactionGroup"]), "friend_group": int(row["FriendGroup"]),
                "enemy_group": int(row["EnemyGroup"]),
                "enemies": [int(row[f"Enemies_{i}"]) for i in range(MAX_FACTION_RELATIONS)],
                "friends": [int(row[f"Friend_{i}"]) for i in range(MAX_FACTION_RELATIONS)]}
    fids = {t["faction"] for t in templates.values()}
    factions = {}
    for r in source.project("Faction", ("ID", "ReputationIndex")):
        if int(r[0]) in fids:
            factions[str(r[0])] = {"reputation_index": int(r[1])}
    return templates, factions


def loaded_creature_faction(template_ids: set[int], faction: int) -> tuple[int, bool]:
    """Mirrors: ObjectMgr.cpp:1022-1027 -- a creature_template.faction without a FactionTemplate row is set to 35.

    Returns (loaded faction, rewritten?).  ``template_ids`` = the DB2 FactionTemplate id set.
    """
    if int(faction) in template_ids:
        return int(faction), False
    return 35, True


def template(world, actor_id: str) -> dict | None:
    """``GetFactionTemplateEntry``: None when the actor states ``faction_template: null``."""
    tid = fact(world, actor_id, "faction_template")
    if tid is None:
        return None
    table = world.raw.get("faction_templates")
    if table is None or str(tid) not in table:
        raise FailClosed(f"relations: fixture has no FactionTemplate row {tid} (actor {actor_id!r})")
    row = dict(table[str(tid)])
    row["id"] = int(tid)
    return row


def faction_entry(world, faction_id: int) -> dict | None:
    """``sFactionStore.LookupEntry``; a faction id of 0 has no entry."""
    if not faction_id:
        return None
    table = world.raw.get("factions")
    if table is None or str(faction_id) not in table:
        raise FailClosed(f"relations: fixture has no Faction row {faction_id}")
    return table[str(faction_id)]


def can_have_reputation(entry: dict) -> bool:
    """Mirrors: DB2Structure.h:1698 ``ReputationIndex >= 0``."""
    return entry["reputation_index"] >= 0


def template_is_friendly_to(t: dict, other: dict) -> bool:
    """Mirrors: DB2Structure.h:1720 ``FactionTemplateEntry::IsFriendlyTo``."""
    if t["id"] == other["id"]:
        return True
    if other["faction"]:
        if other["faction"] in t["enemies"]:
            return False
        if other["faction"] in t["friends"]:
            return True
    return bool((t["friend_group"] & other["faction_group"]) or (t["faction_group"] & other["friend_group"]))


def template_is_hostile_to(t: dict, other: dict) -> bool:
    """Mirrors: DB2Structure.h:1735 ``FactionTemplateEntry::IsHostileTo``."""
    if t["id"] == other["id"]:
        return False
    if other["faction"]:
        if other["faction"] in t["enemies"]:
            return True
        if other["faction"] in t["friends"]:
            return False
    return (t["enemy_group"] & other["faction_group"]) != 0


def _forced_rank(world, player_id: str, faction_id: int) -> int | None:
    """``ReputationMgr::GetForcedRankIfAny`` (ReputationMgr.cpp:233)."""
    forced = fact(world, player_id, "forced_reactions")
    value = forced.get(str(faction_id), forced.get(faction_id)) if forced else None
    return None if value is None else _rank(value)


def _rep_state(world, player_id: str, entry: dict, faction_id: int) -> dict | None:
    """``ReputationMgr::GetState(FactionEntry)`` (ReputationMgr.cpp:87): null unless rep-capable."""
    if not can_have_reputation(entry):
        return None
    reps = world.actor(player_id).facts.get("reputation")
    if reps is None or str(faction_id) not in reps:
        raise FailClosed(f"relations: player {player_id!r} reputation with faction {faction_id} not stated")
    return reps[str(faction_id)]


def _rank(value) -> int:
    if isinstance(value, str):
        if value.upper() not in REP_NAMES:
            raise FailClosed(f"relations: unknown reputation rank {value!r}")
        return REP_NAMES.index(value.upper())
    if not 0 <= int(value) <= 7:
        raise FailClosed(f"relations: reputation rank out of range {value!r}")
    return int(value)


def _stated(world, a: str, b: str, key: str):
    rel = world.relations.get((a, b))
    if rel is not None and key in rel:
        return rel[key]
    return _MISSING


# ---------------------------------------------------------------------------
# GetReactionTo / GetFactionReactionTo
# ---------------------------------------------------------------------------
def faction_reaction_to(world, tpl: dict | None, target: str, path: list[str]) -> int:
    """Mirrors: Object.cpp:2141 ``WorldObject::GetFactionReactionTo``."""
    if tpl is None:
        path.append("2144:no-template->NEUTRAL")
        return REP_NEUTRAL
    ttpl = template(world, target)
    if ttpl is None:
        path.append("2148:target-no-template->NEUTRAL")
        return REP_NEUTRAL
    tpo = affecting_player(world, target)
    if tpo is not None:
        if (tpl["flags"] & FACTION_TEMPLATE_FLAG_CONTESTED_GUARD) and has_player_flag(world, tpo, "CONTESTED_PVP"):
            path.append("2154:contested->HOSTILE")
            return REP_HOSTILE
        forced = _forced_rank(world, tpo, tpl["faction"])
        if forced is not None:
            path.append("2157:forced")
            return forced
        if is_unit(world, target) and not _flag_in(world, target, "unit_flags2", "IGNORE_REPUTATION", UNIT_FLAGS2):
            entry = faction_entry(world, tpl["faction"])
            if entry is not None and can_have_reputation(entry):
                state = _rep_state(world, tpo, entry, tpl["faction"])
                rank = _rank(state["rank"])
                if state.get("at_war") is None:
                    raise FailClosed(f"relations: reputation at_war of {tpo!r} not stated")
                if state["at_war"]:
                    rank = min(REP_NEUTRAL, rank)
                path.append("2166:CvP-reputation")
                return rank
    if template_is_hostile_to(tpl, ttpl):
        path.append("2176:template-hostile")
        return REP_HOSTILE
    if template_is_friendly_to(tpl, ttpl):
        path.append("2178:template-friendly")
        return REP_FRIENDLY
    if template_is_friendly_to(ttpl, tpl):
        path.append("2180:target-template-friendly")
        return REP_FRIENDLY
    if tpl["flags"] & FACTION_TEMPLATE_FLAG_HOSTILE_BY_DEFAULT:
        path.append("2182:hostile-by-default")
        return REP_HOSTILE
    path.append("2185:neutral-default")
    return REP_NEUTRAL


def _attackable_by_summoner(world, me: str, target: str) -> bool:
    """Object.cpp:2041 lambda (TempSummon with SummonPropertiesFlags::AttackableBySummoner)."""
    if not is_unit(world, me) or not is_summon(world, me):
        return False
    if not fact(world, me, "attackable_by_summoner"):
        return False
    from . import groups
    return groups.is_summoned_by(world, me, target)


def _in_raid_with(world, a: str, b: str, trace: Trace | None) -> bool:
    try:
        from . import groups  # Track F
    except ImportError:
        groups = None
    if groups is not None and hasattr(groups, "is_in_raid_with"):
        return groups.is_in_raid_with(world, a, b, trace)
    return _local_group_check(world, a, b, raid=True)


def _in_party_with(world, a: str, b: str, trace: Trace | None) -> bool:
    try:
        from . import groups  # Track F
    except ImportError:
        groups = None
    if groups is not None and hasattr(groups, "is_in_party_with"):
        return groups.is_in_party_with(world, a, b, trace)
    return _local_group_check(world, a, b, raid=False)


def _local_group_check(world, a: str, b: str, raid: bool) -> bool:
    """TEMPORARY local helper until ``targeting.groups`` (Track F) lands.

    Mirrors: Unit.cpp:12184 ``IsInPartyWith`` / 12203 ``IsInRaidWith`` with
    Player.cpp:2070 ``IsInSameGroupWith`` (same group and subgroup) / 2077
    ``IsInSameRaidWith`` (same group).
    """
    if a == b:
        return True
    u1 = charmer_or_owner_or_self(world, a)
    u2 = charmer_or_owner_or_self(world, b)
    if u1 == u2:
        return True
    p1, p2 = is_player(world, u1), is_player(world, u2)
    if p1 and p2:
        for u in (u1, u2):
            if not _raw_states(world, u, "group"):
                raise FailClosed(f"relations: group membership of {u!r} not stated (use `group: null` for none)")
        g1, g2 = world.actor(u1).group, world.actor(u2).group
        if g1 is None or g2 is None:
            return False
        if not g1.get("id") or g1.get("id") != g2.get("id"):
            return False
        return True if raid else g1.get("subgroup") == g2.get("subgroup")
    if (p2 and not p1 and fact(world, u1, "treated_as_raid_unit")) or \
            (p1 and not p2 and fact(world, u2, "treated_as_raid_unit")):
        return True
    if not p1 and not p2:
        return fact(world, u1, "faction_template") == fact(world, u2, "faction_template")
    return False


def _raw_states(world, actor_id: str, key: str) -> bool:
    """Whether the fixture JSON explicitly carries ``key`` for the actor (null counts as stated)."""
    for a in world.raw.get("actors", []):
        if a.get("id") == actor_id:
            return key in a
    return False


def reaction(world, a: str, b: str, trace: Trace | None = None) -> int:
    """Mirrors: Object.cpp:2035 ``WorldObject::GetReactionTo``."""
    path: list[str] = []
    result = _reaction(world, a, b, trace, path)
    if trace is not None:
        trace.add("relations.reaction", "Object.cpp:2035", output=REP_NAMES[result],
                  inputs={"from": a, "to": b}, notes=path,
                  evidence="fixture-stated" if path == ["stated"] else "trinity-consumer")
    return result


def _reaction(world, a: str, b: str, trace, path: list[str]) -> int:
    stated = _stated(world, a, b, "reaction")
    if stated is not _MISSING:
        path.append("stated")
        return _rank(stated)
    if a == b:
        path.append("2038:self")
        return REP_FRIENDLY
    if _attackable_by_summoner(world, a, b) or _attackable_by_summoner(world, b, a):
        path.append("2057:attackable-by-summoner")
        return REP_NEUTRAL
    if charmer_or_owner_or_self(world, a) == charmer_or_owner_or_self(world, b):
        # NB: two ownerless non-units (nullptr == nullptr) also take this branch.
        path.append("2061:same-charmer-or-owner-or-self")
        return REP_FRIENDLY
    spo = affecting_player(world, a)
    tpo = affecting_player(world, b)
    if spo is not None:
        ttpl = template(world, b)
        if ttpl is not None:
            forced = _forced_rank(world, spo, ttpl["faction"])
            if forced is not None:
                path.append("2071:forced")
                return forced
    elif tpo is not None:
        stpl = template(world, a)
        if stpl is not None:
            forced = _forced_rank(world, tpo, stpl["faction"])
            if forced is not None:
                path.append("2077:forced")
                return forced
    unit = a if is_unit(world, a) else spo
    tunit = b if is_unit(world, b) else tpo
    if unit is not None and player_controlled(world, unit):
        if tunit is not None and player_controlled(world, tunit):
            if spo is not None and tpo is not None:
                if spo == tpo:
                    path.append("2090:same-player")
                    return REP_FRIENDLY
                duel = fact(world, spo, "duel")
                if duel and duel.get("opponent") == tpo and duel.get("in_progress"):
                    path.append("2094:duel")
                    return REP_HOSTILE
                if _in_raid_with(world, spo, tpo, trace):
                    path.append("2098:same-raid")
                    return REP_FRIENDLY
            if has_pvp_flag(world, unit, "FFA_PVP") and has_pvp_flag(world, tunit, "FFA_PVP"):
                path.append("2105:ffa")
                return REP_HOSTILE
            if spo is not None:
                ttpl = template(world, tunit)
                if ttpl is not None:
                    forced = _forced_rank(world, spo, ttpl["faction"])
                    if forced is not None:
                        path.append("2112:forced")
                        return forced
                    if not _flag_in(world, spo, "unit_flags2", "IGNORE_REPUTATION", UNIT_FLAGS2):
                        entry = faction_entry(world, ttpl["faction"])
                        if entry is not None and can_have_reputation(entry):
                            if (ttpl["flags"] & FACTION_TEMPLATE_FLAG_CONTESTED_GUARD) and \
                                    has_player_flag(world, spo, "CONTESTED_PVP"):
                                path.append("2121:contested")
                                return REP_HOSTILE
                            state = _rep_state(world, spo, entry, ttpl["faction"])
                            if state.get("at_war") is None:
                                raise FailClosed(f"relations: reputation at_war of {spo!r} not stated")
                            path.append("2126:PvP-reputation-at-war" if state["at_war"] else "2128:PvP-reputation")
                            return REP_HOSTILE if state["at_war"] else REP_FRIENDLY
    return faction_reaction_to(world, template(world, a), b, path)


def is_hostile(world, a: str, b: str, trace: Trace | None = None) -> bool:
    """Mirrors: Object.cpp:2188 ``GetReactionTo(target) <= REP_HOSTILE``."""
    stated = _stated(world, a, b, "hostile")
    if stated is not _MISSING:
        return bool(stated)
    return reaction(world, a, b, trace) <= REP_HOSTILE


def is_friendly(world, a: str, b: str, trace: Trace | None = None) -> bool:
    """Mirrors: Object.cpp:2193 ``GetReactionTo(target) >= REP_FRIENDLY``."""
    stated = _stated(world, a, b, "friendly")
    if stated is not _MISSING:
        return bool(stated)
    return reaction(world, a, b, trace) >= REP_FRIENDLY


def can_see(world, a: str, b: str, implicit: bool) -> bool:
    """``WorldObject::CanSeeOrDetect`` -- a fixture fact (directed ``can_see``; profile => visible)."""
    stated = _stated(world, a, b, "can_see")
    if stated is not _MISSING:
        if isinstance(stated, dict):
            key = "implicit" if implicit else "explicit"
            if key not in stated:
                raise FailClosed(f"relations: can_see {a!r}->{b!r} does not state {key!r}")
            return bool(stated[key])
        return bool(stated)
    if a == b:
        return True
    if world.actor(a).facts.get("profile") == PROFILE and world.actor(b).facts.get("profile") == PROFILE:
        return True
    raise FailClosed(f"relations: visibility {a!r}->{b!r} not stated")


def _alive(world, actor_id: str) -> bool:
    return bool(world.actor(actor_id).need("alive"))


# ---------------------------------------------------------------------------
# IsValidAttackTarget / IsValidAssistTarget
# ---------------------------------------------------------------------------
def valid_attack(world, a: str, b: str, spell=None, trace: Trace | None = None) -> bool:
    """Mirrors: Object.cpp:2331 ``WorldObject::IsValidAttackTarget``."""
    path: list[str] = []
    stated = _stated(world, a, b, "valid_attack")
    if stated is not _MISSING:
        result, path = bool(stated), ["stated"]
    else:
        result = _valid_attack(world, a, b, spell, trace, path)
    if trace is not None:
        defect = "TG-B-D1" if any(p.startswith("2469") for p in path) else None
        trace.add("relations.valid_attack", "Object.cpp:2331", output=result, inputs={"from": a, "to": b},
                  notes=path, defect=defect,
                  evidence="fixture-stated" if path == ["stated"] else "trinity-consumer")
    return result


def _valid_attack(world, a, b, spell, trace, path) -> bool:
    is_positive = spell is not None and spell_is_positive(spell)
    if spell is None and a == b:
        path.append("2339:self-without-spell")
        return False
    ub = is_unit(world, b)
    if ub and fact(world, b, "unattackable_state"):
        path.append("2344:unattackable-state")
        return False
    if is_player(world, b) and fact(world, b, "game_master"):
        path.append("2348:gm")
        return False
    ua = is_unit(world, a)
    if ua:
        implicit = bool(_spell_value(spell, "is_affecting_area")) if spell is not None else False
        if not can_see(world, a, b, implicit):
            path.append("2364:cannot-see")
            return False
    if (spell is None or not spell_allows_dead(spell)) and ub and not _alive(world, b):
        path.append("2369:dead")
        return False
    if (spell is None or not _spell_attr(spell, "SPELL_ATTR6_CAN_TARGET_UNTARGETABLE")) and ub \
            and has_unit_flag(world, b, "NON_ATTACKABLE_2"):
        path.append("2373:non-attackable-2")
        return False
    if ub and has_unit_flag(world, b, "UNINTERACTIBLE"):
        path.append("2376:uninteractible")
        return False
    if is_player(world, a) and has_player_flag(world, a, "UBER"):
        path.append("2381:uber")
        return False
    if ub and any(has_unit_flag(world, b, f) for f in ("NON_ATTACKABLE", "ON_TAXI", "NOT_ATTACKABLE_1")):
        path.append("2386:non-attackable/taxi/not-attackable-1")
        return False
    unit_or_owner = a if ua else None
    go_trap = world.actor(a).kind == "gameobject" and fact(world, a, "go_type") == "trap"
    if go_trap:
        unit_or_owner = charmer_or_owner(world, a)       # GameObject::GetOwner
    if unit_or_owner is not None and ub and not (is_positive and _spell_attr(spell, "SPELL_ATTR6_CAN_ASSIST_IMMUNE_PC")):
        if not player_controlled(world, unit_or_owner) and has_unit_flag(world, b, "IMMUNE_TO_NPC"):
            path.append("2397:target-immune-to-npc")
            return False
        if not player_controlled(world, b) and has_unit_flag(world, unit_or_owner, "IMMUNE_TO_NPC"):
            path.append("2400:attacker-immune-to-npc")
            return False
        if spell is None or not _spell_attr(spell, "SPELL_ATTR8_CAN_ATTACK_IMMUNE_PC"):
            if player_controlled(world, unit_or_owner) and has_unit_flag(world, b, "IMMUNE_TO_PC"):
                path.append("2405:target-immune-to-pc")
                return False
            if player_controlled(world, b) and has_unit_flag(world, unit_or_owner, "IMMUNE_TO_PC"):
                path.append("2408:attacker-immune-to-pc")
                return False
    if ua and not player_controlled(world, a) and ub and not player_controlled(world, b):
        path.append("2415:CvC-hostile-either-way")
        return is_hostile(world, a, b, trace) or is_hostile(world, b, a, trace)
    if go_trap:
        owner = charmer_or_owner(world, a)
        if owner is None or not player_controlled(world, owner):
            if ub and not player_controlled(world, b):
                path.append("2423:trap-vs-creature")
                return is_hostile(world, a, b, trace) or is_hostile(world, b, a, trace)
    if is_friendly(world, a, b, trace) or is_friendly(world, b, a, trace):
        path.append("2428:friendly-either-way")
        return False
    is_go = world.actor(a).kind == "gameobject"
    paa = affecting_player(world, a) if ((ua and player_controlled(world, a)) or is_go) else None
    pat = affecting_player(world, b) if (ub and player_controlled(world, b)) else None
    if paa is None and ub and world.actor(b).kind == "pet" and pat is not None and fact(world, pat, "mounted"):
        path.append("2435:pet-of-mounted-player")
        return False
    if (paa is not None) != (pat is not None):
        player = paa if paa is not None else pat
        creature = (b if ub else None) if paa is not None else (a if ua else None)
        if creature is not None:
            ctpl = template(world, creature)
            if ctpl is not None and (ctpl["flags"] & FACTION_TEMPLATE_FLAG_CONTESTED_GUARD) \
                    and has_player_flag(world, player, "CONTESTED_PVP"):
                path.append("2445:contested-guard")
                return True
            if ctpl is not None and _forced_rank(world, player, ctpl["faction"]) is None:
                entry = faction_entry(world, ctpl["faction"])
                if entry is not None:
                    state = _rep_state(world, player, entry, ctpl["faction"])
                    if state is not None:
                        if state.get("at_war") is None:
                            raise FailClosed(f"relations: reputation at_war of {player!r} not stated")
                        if not state["at_war"]:
                            path.append("2453:rep-not-at-war")
                            return False
    if paa is not None and pat is not None:
        duel = fact(world, paa, "duel")
        if duel and duel.get("opponent") == pat and duel.get("in_progress"):
            path.append("2461:duel")
            return True
    if ub and player_controlled(world, b) and unit_or_owner is not None and player_controlled(world, unit_or_owner) \
            and (has_pvp_flag(world, b, "SANCTUARY") or has_pvp_flag(world, unit_or_owner, "SANCTUARY")) \
            and (spell is None or _spell_attr(spell, "SPELL_ATTR8_IGNORE_SANCTUARY")):
        path.append("2469:sanctuary(inverted-attr-polarity)")
        return False
    if paa is not None and pat is not None:
        if has_pvp_flag(world, pat, "PVP") or (spell is not None and _spell_attr(spell, "SPELL_ATTR5_IGNORE_AREA_EFFECT_PVP_CHECK")):
            path.append("2475:target-pvp")
            return True
        if has_pvp_flag(world, paa, "FFA_PVP") and has_pvp_flag(world, pat, "FFA_PVP"):
            path.append("2478:ffa")
            return True
        path.append("2481:unk1-pvp-flag")
        return has_pvp_flag(world, paa, "UNK1") or has_pvp_flag(world, pat, "UNK1")
    path.append("2485:default-true")
    return True


def valid_assist(world, a: str, b: str, spell=None, trace: Trace | None = None) -> bool:
    """Mirrors: Object.cpp:2489 ``WorldObject::IsValidAssistTarget``."""
    path: list[str] = []
    stated = _stated(world, a, b, "valid_assist")
    if stated is not _MISSING:
        result, path = bool(stated), ["stated"]
    else:
        result = _valid_assist(world, a, b, spell, trace, path)
    if trace is not None:
        defect = "TG-B-D1" if any(p.startswith("2584") for p in path) else None
        trace.add("relations.valid_assist", "Object.cpp:2489", output=result, inputs={"from": a, "to": b},
                  notes=path, defect=defect,
                  evidence="fixture-stated" if path == ["stated"] else "trinity-consumer")
    return result


def _valid_assist(world, a, b, spell, trace, path) -> bool:
    is_negative = spell is not None and not spell_is_positive(spell)
    if a == b:
        path.append("2497:self")
        return True
    ub = is_unit(world, b)
    if ub and fact(world, b, "unattackable_state"):
        path.append("2502:unattackable-state")
        return False
    if is_player(world, b) and fact(world, b, "game_master"):
        path.append("2506:gm")
        return False
    ua = is_unit(world, a)
    if ua and ub and fact(world, a, "vehicle") is not None:
        raise FailClosed("relations: vehicle passenger/base relation (Object.cpp:2511) is out of scope")
    implicit = bool(_spell_value(spell, "is_affecting_area")) if spell is not None else False
    if not can_see(world, a, b, implicit):
        path.append("2529:cannot-see")
        return False
    if (spell is None or not spell_allows_dead(spell)) and ub and not _alive(world, b):
        path.append("2533:dead")
        return False
    if (spell is None or not _spell_attr(spell, "SPELL_ATTR6_CAN_TARGET_UNTARGETABLE")) and ub \
            and has_unit_flag(world, b, "NON_ATTACKABLE_2"):
        path.append("2537:non-attackable-2")
        return False
    if (spell is None or not _spell_attr(spell, "SPELL_ATTR11_CAN_ASSIST_UNINTERACTIBLE")) and ub \
            and has_unit_flag(world, b, "UNINTERACTIBLE"):
        path.append("2540:uninteractible")
        return False
    if is_negative and ub and any(has_unit_flag(world, b, f) for f in ("NON_ATTACKABLE", "ON_TAXI", "NOT_ATTACKABLE_1")):
        path.append("2544:negative-vs-non-attackable")
        return False
    if is_negative or spell is None or not _spell_attr(spell, "SPELL_ATTR6_CAN_ASSIST_IMMUNE_PC"):
        if ua and player_controlled(world, a):
            if spell is None or not _spell_attr(spell, "SPELL_ATTR8_CAN_ATTACK_IMMUNE_PC"):
                if ub and has_unit_flag(world, b, "IMMUNE_TO_PC"):
                    path.append("2552:target-immune-to-pc")
                    return False
        elif ub and has_unit_flag(world, b, "IMMUNE_TO_NPC"):
            path.append("2557:target-immune-to-npc")
            return False
    if reaction(world, a, b, trace) < REP_NEUTRAL and reaction(world, b, a, trace) < REP_NEUTRAL \
            and (not is_creature(world, a) or not fact(world, a, "treated_as_raid_unit")):
        path.append("2563:unfriendly-both-ways")
        return False
    if ub and player_controlled(world, b):
        if ua and player_controlled(world, a):
            spo = affecting_player(world, a)
            tpo = affecting_player(world, b)
            if spo is not None and tpo is not None:
                if spo != tpo and fact(world, tpo, "duel"):
                    path.append("2576:target-dueling")
                    return False
            if has_pvp_flag(world, b, "FFA_PVP") and not has_pvp_flag(world, a, "FFA_PVP"):
                path.append("2580:ffa-from-outside")
                return False
            if has_pvp_flag(world, b, "PVP") and (spell is None or _spell_attr(spell, "SPELL_ATTR8_IGNORE_SANCTUARY")):
                if has_pvp_flag(world, a, "SANCTUARY") and not has_pvp_flag(world, b, "SANCTUARY"):
                    path.append("2584:sanctuary(inverted-attr-polarity)")
                    return False
    elif ua and player_controlled(world, a):
        if spell is None or not _spell_attr(spell, "SPELL_ATTR6_CAN_ASSIST_IMMUNE_PC"):
            if ub and not has_pvp_flag(world, b, "PVP") and is_creature(world, b):
                result = bool(fact(world, b, "treated_as_raid_unit")) or bool(fact(world, b, "creature_type_flag_can_assist"))
                path.append("2596:PvC-raid-unit-or-can-assist")
                return result
    path.append("2599:default-true")
    return True


# ---------------------------------------------------------------------------
# WorldObjectSpellTargetCheck (relation part)
# ---------------------------------------------------------------------------
def check(world, spell_view, caster_id: str, target_id: str, check_type: str, trace: Trace | None,
          *, referer_id: str | None = None, object_type: str | None = None) -> bool:
    """Relation part of ``WorldObjectSpellTargetCheck::operator()``.

    Mirrors: Spell.cpp:9311-9390.  ``SpellInfo::CheckTarget`` (9308) and the
    condition list (9392-9395) are *not* evaluated here (see
    :func:`targeting.explicit.check_target` and the conditions boundary).

    Caster identity: on the *spell* path ``_caster`` is always ``m_caster`` at every construction
    site (Spell.cpp:1301, 1892, 1994, 2201, 2216) -- not ``m_originalCaster`` and
    not the owner; owner/charmer resolution happens *inside* GetReactionTo /
    GetAffectingPlayer / IsInPartyWith.  PARTY/RAID membership uses ``_referer``
    (caster, or the area referer, e.g. the explicit target for
    TARGET_UNIT_TARGET_ALLY_OR_RAID).  ``referer_id`` defaults to the caster.
    On the *aura* path (area auras, ``UnitAura::FillTargetMap``) the relation caster is the
    aura caster, or the aura owner when the caster is gone (SpellAuras.cpp:2636; see
    :mod:`targeting.auratargets`); pass that actor as ``caster_id``.
    """
    if check_type in ABSENT_CHECK_TYPES:
        raise FailClosed(f"relations: SpellTargetCheckTypes has no {check_type!r} at the pin (SpellInfo.h:79)")
    if check_type not in CHECK_TYPES:
        raise FailClosed(f"relations: unknown check type {check_type!r}")
    referer = caster_id if referer_id is None else referer_id
    path: list[str] = []
    result = _check(world, spell_view, caster_id, target_id, check_type, referer, object_type, trace, path)
    if trace is not None:
        trace.add("relations.check", "Spell.cpp:9306", output=result,
                  inputs={"caster": caster_id, "target": target_id, "referer": referer,
                          "check": check_type, "object_type": object_type}, notes=path)
    return result


def _check(world, spell, caster, target, check_type, referer, object_type, trace, path) -> bool:
    t = world.actor(target)
    unit_target: str | None = target if is_unit(world, target) else None
    is_corpse = t.kind == "corpse"
    if is_corpse:
        owner = t.owner
        if owner is None or not world.actor(owner).facts.get("in_world", True) or not is_player(world, owner):
            path.append("9318:corpse-without-owner-player")
            return False
        unit_target = owner
        path.append("9316:corpse->owner")
    ref_unit = referer if is_unit(world, referer) else None
    if unit_target is not None:
        is_totem = world.actor(unit_target).kind == "totem"
        if check_type == "ENEMY":
            if is_totem:
                path.append("9329:totem")
                return False
            if not is_corpse and not valid_attack(world, caster, unit_target, spell, trace):
                path.append("9331:not-valid-attack")
                return False
        elif check_type == "ALLY":
            if is_totem:
                path.append("9336:totem")
                return False
            if not is_corpse and not valid_assist(world, caster, unit_target, spell, trace):
                path.append("9338:not-valid-assist")
                return False
        elif check_type == "PARTY":
            if ref_unit is None:
                path.append("9343:no-referer-unit")
                return False
            if is_totem:
                path.append("9345:totem")
                return False
            if not is_corpse and not valid_assist(world, caster, unit_target, spell, trace):
                path.append("9347:not-valid-assist")
                return False
            if not _in_party_with(world, ref_unit, unit_target, trace):
                path.append("9349:not-in-party-with-referer")
                return False
        elif check_type in ("RAID", "RAID_CLASS"):
            if ref_unit is None:
                path.append("9353:no-referer-unit")
                return False
            if check_type == "RAID_CLASS" and fact(world, ref_unit, "class") != fact(world, unit_target, "class"):
                path.append("9355:class-mismatch")
                return False
            if is_totem:
                path.append("9361:totem")
                return False
            if not is_corpse and not valid_assist(world, caster, unit_target, spell, trace):
                path.append("9364:not-valid-assist")
                return False
            if not _in_raid_with(world, ref_unit, unit_target, trace):
                path.append("9366:not-in-raid-with-referer")
                return False
        elif check_type == "SUMMONED":
            from . import groups
            if not is_summon(world, unit_target):
                path.append("9370:not-summon")
                return False
            if not groups.is_summoned_by(world, unit_target, caster):
                path.append("9372:summoner-is-not-caster")
                return False
        else:
            path.append(f"9375:{check_type}-no-relation-check")
        if object_type in ("CORPSE", "CORPSE_ALLY", "CORPSE_ENEMY"):
            if _alive(world, unit_target):
                path.append("9384:corpse-object-type-alive")
                return False
    else:
        path.append("9322:non-unit-no-relation-check")
    return True


__all__ = [
    "ABSENT_CHECK_TYPES", "CHECK_TYPES", "PROFILE", "REP_NAMES", "affecting_player", "can_see",
    "charmer_or_owner", "charmer_or_owner_or_self", "check", "fact", "faction_reaction_to",
    "faction_rows_from_db2", "is_friendly", "is_hostile", "reaction", "template_is_friendly_to",
    "template_is_hostile_to", "valid_assist", "valid_attack",
]


# ===========================================================================
# census / corpus (relations.json)
# ===========================================================================
#: Every fact the relation functions read: (fact, consumer, scope verdict, reopen condition)
FACT_INVENTORY = (
    ("self identity (this == target)", "Object.cpp:2038/2339/2497", "in-scope", None),
    ("SummonProperties AttackableBySummoner + summoner guid", "Object.cpp:2041-2058", "in-scope (profile: false)",
     "a current-player summon's SummonProperties row carries AttackableBySummoner"),
    ("charmer/owner resolution (GetCharmerOrOwnerOrSelf, GetAffectingPlayer)", "Object.cpp:1620/1637/2061", "in-scope", None),
    ("forced reactions (SPELL_AURA_FORCE_REACTION)", "Object.cpp:2071/2077/2112/2157, ReputationMgr.cpp:233", "out-of-scope (profile: none)",
     "a current-player spell applies SPELL_AURA_FORCE_REACTION to the player"),
    ("UNIT_FLAG_PLAYER_CONTROLLED", "Object.cpp:2083/2397/2415/2431/2466/2549/2567", "in-scope", None),
    ("duel state", "Object.cpp:2094/2461/2576", "out-of-scope (profile: none)", "duel simulation"),
    ("group / raid membership (IsInRaidWith)", "Object.cpp:2098, Unit.cpp:12203", "in-scope (Track F groups)", None),
    ("FFA PvP flag", "Object.cpp:2105/2478/2580", "out-of-scope (profile: none)", "PvP simulation"),
    ("reputation state (rank, AtWar), Faction.ReputationIndex", "Object.cpp:2114-2129/2159-2171/2449-2455", "in-scope when the target faction can have reputation (fixture states it); training dummies/most raid bosses cannot",
     None),
    ("UNIT_FLAG2_IGNORE_REPUTATION", "Object.cpp:2114/2159", "in-scope (profile: clear)", "a current creature template sets unit_flags2 & 0x4"),
    ("FactionTemplate rows (groups, enemies, friends, flags)", "Object.cpp:2141-2185, DB2Structure.h:1720/1735", "in-scope (db2-fact)", None),
    ("FACTION_TEMPLATE_FLAG_CONTESTED_GUARD + PLAYER_FLAGS_CONTESTED_PVP", "Object.cpp:2121/2154/2445", "out-of-scope (profile: clear)", "world PvP"),
    ("FACTION_TEMPLATE_FLAG_HOSTILE_BY_DEFAULT", "Object.cpp:2182", "in-scope (db2-fact)", None),
    ("UNIT_STATE_UNATTACKABLE (= IN_FLIGHT)", "Object.cpp:2344/2502", "out-of-scope (profile: false)", "taxi/flight"),
    ("GM mode", "Object.cpp:2348/2506, SpellInfo.cpp:2477", "out-of-scope (profile: false)", "never"),
    ("CanSeeOrDetect (stealth/invisibility/phase; spell ImplicitDetection, ATTR6_IGNORE_PHASE_SHIFT, ATTR8 spawn tracking, CU private object)",
     "Object.cpp:2364/2529, SpellInfo.cpp:2341", "fixture fact `can_see` (profile: visible)", "stealth/invisibility targets in simulation"),
    ("IsAlive + IsAllowingDeadTarget", "Object.cpp:2369/2533", "in-scope", None),
    ("UNIT_FLAG_NON_ATTACKABLE_2 (+ATTR6_CAN_TARGET_UNTARGETABLE)", "Object.cpp:2373/2537", "in-scope (fixture unit_flags)", None),
    ("UNIT_FLAG_UNINTERACTIBLE (+ATTR11_CAN_ASSIST_UNINTERACTIBLE for assist)", "Object.cpp:2376/2540", "in-scope (fixture unit_flags)", None),
    ("PLAYER_FLAGS_UBER", "Object.cpp:2381", "out-of-scope (profile: clear)", "never"),
    ("UNIT_FLAG_NON_ATTACKABLE | ON_TAXI | NOT_ATTACKABLE_1", "Object.cpp:2386/2544", "in-scope (fixture unit_flags)", None),
    ("GameObject trap type + owner", "Object.cpp:2390/2418", "in-scope (fixture go_type)", None),
    ("IMMUNE_TO_PC / IMMUNE_TO_NPC (+ATTR6_CAN_ASSIST_IMMUNE_PC, ATTR8_CAN_ATTACK_IMMUNE_PC)", "Object.cpp:2395-2411/2547-2560", "in-scope (fixture unit_flags)", None),
    ("IsPet + owner mounted", "Object.cpp:2435", "out-of-scope (profile: not mounted)", "mounted combat"),
    ("sanctuary pvp flag (+ATTR8_IGNORE_SANCTUARY, inverted: TG-B-D1)", "Object.cpp:2466/2584", "out-of-scope (profile: clear)", "PvP simulation"),
    ("PvP flag / UNIT_BYTE2_FLAG_UNK1 (+ATTR5_IGNORE_AREA_EFFECT_PVP_CHECK)", "Object.cpp:2475-2482/2584/2594", "out-of-scope (profile: clear)", "PvP simulation"),
    ("vehicle base / passenger", "Object.cpp:2511-2518", "out-of-scope (fails closed when stated)", "vehicle encounters"),
    ("IsTreatedAsRaidUnit (TypeFlags & CREATURE_TYPE_FLAG_TREAT_AS_RAID_UNIT, Creature.cpp:588)", "Object.cpp:2563/2596, Unit.cpp:12196", "in-scope (world-db-fact; must be stated, no profile default)", None),
    ("CREATURE_TYPE_FLAG_CAN_ASSIST (TypeFlags 0x1000)", "Object.cpp:2596", "in-scope (world-db-fact; must be stated, no profile default)", None),
    ("creature_template.faction load rewrite (no FactionTemplate row -> 35)", "ObjectMgr.cpp:1022-1027", "in-scope (applied by loaded_creature_faction)", None),
    ("SpellInfo::IsPositive (NegativeEffects)", "Object.cpp:2336/2494", "stated by spell view (load-time positivity not ported)", "positivity port"),
    ("SpellInfo::IsAffectingArea (implicit detection)", "Object.cpp:2359/2524", "in-scope (derived from selectors)", None),
)

CHECK_TYPE_TABLE = (
    {"check": "DEFAULT", "consumer": "Spell.cpp:9375", "relation": "none", "membership": "none"},
    {"check": "ENTRY", "consumer": "Spell.cpp:9375", "relation": "none (conditions decide)", "membership": "none"},
    {"check": "ENEMY", "consumer": "Spell.cpp:9327", "relation": "!totem && IsValidAttackTarget(m_caster) (skipped for corpses)", "membership": "none"},
    {"check": "ALLY", "consumer": "Spell.cpp:9334", "relation": "!totem && IsValidAssistTarget(m_caster) (skipped for corpses)", "membership": "none"},
    {"check": "PARTY", "consumer": "Spell.cpp:9341", "relation": "referer is unit, !totem, IsValidAssistTarget(m_caster)", "membership": "_referer->IsInPartyWith(target)"},
    {"check": "RAID", "consumer": "Spell.cpp:9358", "relation": "referer is unit, !totem, IsValidAssistTarget(m_caster)", "membership": "_referer->IsInRaidWith(target)"},
    {"check": "RAID_CLASS", "consumer": "Spell.cpp:9352", "relation": "class(referer)==class(target) first, then RAID", "membership": "_referer->IsInRaidWith(target)"},
    {"check": "PASSENGER", "consumer": "Spell.cpp:9375", "relation": "none here (only CheckExplicitTarget TARGET_FLAG_UNIT_PASSENGER)", "membership": "none"},
    {"check": "SUMMONED", "consumer": "Spell.cpp:9369", "relation": "IsSummon && SummonerGUID == m_caster", "membership": "none"},
    {"check": "THREAT", "consumer": "absent (SpellInfo.h:79)", "relation": "does not exist at the pin; THREAT_LIST selector (122) adds without any check", "membership": "n/a"},
    {"check": "TAP", "consumer": "absent (SpellInfo.h:79)", "relation": "does not exist at the pin; TAP_LIST selector (123) adds without any check", "membership": "n/a"},
)

_REL_UNIT_FLAG_BITS = {v: k for k, v in UNIT_FLAGS.items()}


def _decode_unit_flags(value: int) -> list[str]:
    return sorted(name for bit, name in _REL_UNIT_FLAG_BITS.items() if int(value) & bit)


DUMMY_NAME_RE = r"\b(training|target|combat|sparring|practice|healing|tank|tanking|damage|cleave) dummy\b"
DUMMY_EXCLUDE_RE = r"\btest\b|\[dnt\]|bunny|kill credit"
DUMMY_ROLE_RE = r"\b(damage|healing|tanking|cleave|pvp)\b"


def select_dummies(rows: list[dict]) -> list[dict]:
    """Training-dummy selector over creature_template rows (stated in relations.json `dummy_selector`).

    ``ScriptName == 'npc_training_dummy'`` OR (name or subname matches :data:`DUMMY_NAME_RE` and the
    name does not match :data:`DUMMY_EXCLUDE_RE`), case-insensitive.  The ScriptName key alone selects mostly legacy
    templates; current-content dummies carry no ScriptName (R1-02).
    """
    import re
    out = []
    for r in rows:
        name = r.get("name") or ""
        text = f"{name} | {r.get('subname') or ''}"
        if r.get("ScriptName") == "npc_training_dummy" or (
                re.search(DUMMY_NAME_RE, text, re.I) and not re.search(DUMMY_EXCLUDE_RE, name, re.I)):
            out.append(r)
    return out


def _dummy_role(d: dict) -> str:
    import re
    text = f"{d.get('name') or ''} {d.get('subname') or ''}"
    roles = sorted({m.lower() for m in re.findall(DUMMY_ROLE_RE, text, re.I)})
    return "+".join(roles) if roles else "unspecified"


def census(ctx, dummy_extract: dict | None, difficulty_extract: dict | None) -> dict:
    """Player-race templates x training-dummy / sample boss templates (db2 + world-db facts)."""
    from collections import Counter

    from .fixture import World
    src = ctx.bundle.source
    races = []
    for r in src.project("ChrRaces", ("ID", "ClientPrefix", "FactionID", "PlayableRaceBit", "Alliance")):
        if int(r[3]) >= 0:
            races.append({"race": int(r[0]), "prefix": r[1], "faction_template": int(r[2]), "alliance": int(r[4])})
    player_tpls = sorted({r["faction_template"] for r in races})
    dummies = []
    diff_rows: dict[int, dict] = {}
    if difficulty_extract is not None:
        t = difficulty_extract["tables"]["creature_template_difficulty"]
        for row in t["rows"]:
            d = dict(zip(t["columns"], row))
            if int(d["DifficultyID"]) == 0:
                diff_rows[int(d["Entry"])] = d
    if dummy_extract is not None:
        t = dummy_extract["tables"]["creature_template"]
        for row in t["rows"]:
            dummies.append(dict(zip(t["columns"], row)))
    samples = [{"entry": None, "name": "sample hostile boss template 14 (Monster)", "faction": 14, "faction_loaded": 14,
                "faction_rewritten": False, "unit_flags": 0, "unit_flags2": 0,
                "evidence": "db2-fact (synthetic creature, TypeFlags 0)"}]
    all_tpl_ids = {int(r[0]) for r in src.project("FactionTemplate", ("ID",))}
    for d in dummies:
        d["faction_loaded"], d["faction_rewritten"] = loaded_creature_faction(all_tpl_ids, int(d["faction"]))
    creature_tpls = sorted({int(d["faction_loaded"]) for d in dummies} | {14})
    templates, factions = faction_rows_from_db2(src, set(player_tpls) | set(creature_tpls))
    harmful = {"attributes": [], "is_positive": False, "is_affecting_area": False, "is_allowing_dead_target": False}
    helpful = dict(harmful, is_positive=True)
    matrix = []
    for d in [*dummies, *samples]:
        entry = d.get("entry")
        diff = diff_rows.get(int(entry)) if entry is not None else None
        # no DIFFICULTY_NONE row -> DefaultCreatureDifficulty, TypeFlags 0 (Creature.cpp:263-277)
        type_flags = int(diff["TypeFlags"]) if diff else 0
        static4 = int(diff["StaticFlags4"]) if diff else 0
        creature_facts = {"profile": PROFILE, "faction_template": int(d["faction_loaded"]), "is_summon": False,
                          "unit_flags": _decode_unit_flags(d["unit_flags"]),
                          "unit_flags2": ["IGNORE_REPUTATION"] if int(d.get("unit_flags2") or 0) & 0x4 else [],
                          "treated_as_raid_unit": bool(type_flags & 0x04000000),
                          "creature_type_flag_can_assist": bool(type_flags & 0x1000)}
        per_player: dict[str, dict] = {}
        for tpl in player_tpls:
            w = World.from_dict({"schema": "targeting-fixture/1", "caster": "p", "actors": [
                {"id": "p", "kind": "player", "alive": True, "group": None,
                 "facts": {"profile": PROFILE, "faction_template": tpl}},
                {"id": "c", "kind": "creature", "alive": True, "facts": creature_facts}],
                "faction_templates": templates, "factions": factions})
            try:
                row = {"reaction_player_to": REP_NAMES[reaction(w, "p", "c")],
                       "reaction_to_player": REP_NAMES[reaction(w, "c", "p")],
                       "valid_attack": valid_attack(w, "p", "c", harmful),
                       "valid_assist": valid_assist(w, "p", "c", helpful)}
                row["range_column"] = "friendly" if not is_hostile(w, "p", "c") else "hostile"
            except FailClosed as exc:
                row = {"fail_closed": str(exc)}
            per_player[str(tpl)] = row
        distinct = sorted({str(sorted(v.items())) for v in per_player.values()})
        matrix.append({
            "entry": entry, "name": d.get("name"), "subname": d.get("subname"),
            "script_name": d.get("ScriptName") or None, "role": _dummy_role(d),
            "faction_db": int(d["faction"]), "faction_template": int(d["faction_loaded"]),
            "faction_rewritten_to_35": bool(d["faction_rewritten"]),
            "can_assist_type_flag": bool(type_flags & 0x1000),
            "unit_flags": creature_facts["unit_flags"], "type_flags": type_flags,
            "static_flags4_ignore_los_on_me": bool(static4 & 0x2000),
            "treated_as_raid_unit": creature_facts["treated_as_raid_unit"],
            "difficulty_row": diff is not None,
            "evidence": d.get("evidence", "world-db-fact (creature_template + creature_template_difficulty) + db2-fact "
                                          "+ trinity-consumer (ObjectMgr.cpp:1022 load rewrite)"),
            "by_player_template": per_player,
            "uniform_across_player_templates": len(distinct) == 1,
        })
    matrix.sort(key=lambda r: (r["entry"] is None, r["entry"] or 0))
    summary = Counter()
    per_dummy = []
    for r in matrix:
        if r["entry"] is None:
            continue
        verdicts = sorted({f"attack={v.get('valid_attack')},assist={v.get('valid_assist')},col={v.get('range_column')}"
                           for v in r["by_player_template"].values()})
        for v in verdicts:
            summary[v] += 1
        per_dummy.append({"entry": r["entry"], "name": r["name"], "role": r["role"], "script_name": r["script_name"],
                          "faction_db": r["faction_db"], "faction_loaded": r["faction_template"],
                          "can_assist": r["can_assist_type_flag"], "verdicts": verdicts})
    return {
        "player_race_templates": races,
        "faction_templates": templates, "factions": factions,
        "target_matrix": matrix,
        "target_matrix_summary": dict(sorted(summary.items())),
        "target_matrix_summary_note": "counts dummy templates per distinct verdict (a template counts once per verdict "
                                      "across the 15 player faction templates)",
        "per_dummy_verdicts": per_dummy,
        "dummy_selector": dummy_extract.get("selector") if dummy_extract else None,
        "fact_inventory": [{"fact": f, "consumer": c, "scope": s, "reopen_condition": r} for f, c, s, r in FACT_INVENTORY],
        "check_types": list(CHECK_TYPE_TABLE),
    }
