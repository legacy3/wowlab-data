"""Party / raid / summoned membership and caster-relationship predicates (track F).

Fixture-level mirrors of the membership half of
``Trinity::WorldObjectSpellTargetCheck`` (Spell.cpp:9306) and of the caster-object
selectors that name a relationship (Spell.cpp:1742).  Relation facts that are not
membership (``IsValidAssistTarget``, ``CheckTarget``) are owned by track B
(``targeting.relations``); :func:`group_check` takes them as injected callables so
the order of the consumer is kept without re-implementing them here.

Fixture facts read (all fail closed when a reached fact is not stated):

* actor key ``group``: ``null`` (ungrouped) or ``{"id": ..., "subgroup": int}``.
  ``raid`` may be present but **no consumer reads it**: ``IsInSameRaidWith`` is
  "same Group object" and ``IsInSameGroupWith`` is "same Group object and same
  subgroup" (Player.cpp:2070-2080).  A 5-man party is a Group whose members are all
  in subgroup 0.
* actor keys ``owner`` / ``charmer``: ``null`` = empty GUID; absent = unknown.
* creature facts ``treated_as_raid_unit`` (track B name; ``combat-sim`` profile default false) (CREATURE_STATIC_FLAG_4_TREAT_AS_RAID_UNIT_FOR_HELPFUL_SPELLS),
  ``faction`` (the GetFaction() template id), ``class``; ``is_summon`` for kind ``creature`` (pet/guardian/totem/minion
  kinds are TempSummons by construction).
* caster facts ``pet`` (``PetGUID`` actor id or null), ``critter``, ``vehicle_base``.
* player fact ``class``.

Stable names (published for tracks B and G): :func:`charmer_or_owner`,
:func:`charmer_or_owner_or_self`, :func:`is_in_party_with`, :func:`is_in_raid_with`,
:func:`is_in_same_group_with`, :func:`is_in_same_raid_with`, :func:`membership`,
:func:`group_check`, :func:`caster_object_target`, :func:`spell_caster`,
:func:`ally_or_raid_mode`, :func:`is_summoned_by`.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from . import FailClosed
from .fixture import World
from .trace import Trace

# SpellInfo.h:79 SpellTargetCheckTypes (names only; values are not persisted).
CHECK_TYPES = ("DEFAULT", "ENTRY", "ENEMY", "ALLY", "PARTY", "RAID", "RAID_CLASS", "PASSENGER", "SUMMONED")

#: Selectors whose ``_data`` row (SpellInfo.cpp:246-400) names a membership check or a relationship.
#: value = (category, reference, object, check)
GROUP_SELECTORS: dict[int, tuple[str, str, str, str]] = {
    4: ("NEARBY", "CASTER", "UNIT", "PARTY"),       # TARGET_UNIT_NEARBY_PARTY          SpellInfo.cpp:252
    20: ("AREA", "CASTER", "UNIT", "PARTY"),        # TARGET_UNIT_CASTER_AREA_PARTY     :268
    33: ("AREA", "SRC", "UNIT", "PARTY"),           # TARGET_UNIT_SRC_AREA_PARTY        :281
    34: ("AREA", "DEST", "UNIT", "PARTY"),          # TARGET_UNIT_DEST_AREA_PARTY       :282
    35: ("DEFAULT", "TARGET", "UNIT", "PARTY"),     # TARGET_UNIT_TARGET_PARTY          :283
    37: ("AREA", "LAST", "UNIT", "PARTY"),          # TARGET_UNIT_LASTTARGET_AREA_PARTY :285
    56: ("AREA", "CASTER", "UNIT", "RAID"),         # TARGET_UNIT_CASTER_AREA_RAID      :304
    57: ("DEFAULT", "TARGET", "UNIT", "RAID"),      # TARGET_UNIT_TARGET_RAID           :305
    58: ("NEARBY", "CASTER", "UNIT", "RAID"),       # TARGET_UNIT_NEARBY_RAID           :306
    61: ("AREA", "TARGET", "UNIT", "RAID_CLASS"),   # TARGET_UNIT_TARGET_AREA_RAID_CLASS :309
    95: ("DEFAULT", "TARGET", "UNIT", "PASSENGER"), # TARGET_UNIT_TARGET_PASSENGER      :343
    118: ("AREA", "TARGET", "UNIT", "RAID"),        # TARGET_UNIT_TARGET_ALLY_OR_RAID   :366
    119: ("AREA", "CASTER", "CORPSE", "RAID"),      # TARGET_CORPSE_SRC_AREA_RAID       :367
    120: ("AREA", "CASTER", "UNIT", "SUMMONED"),    # TARGET_UNIT_CASTER_AND_SUMMONS    :368
}
#: Caster-object selectors that resolve a relationship (Spell.cpp:1748-1795).
RELATION_SELECTORS: dict[int, str] = {
    1: "caster", 5: "pet", 27: "master", 92: "summoner", 94: "vehicle", 150: "own-critter",
    **{t: f"passenger-{t - 96}" for t in range(96, 104)},   # TARGET_UNIT_PASSENGER_0..7 (vehicle-base caster)
    105: "caster-and-passengers", 124: "target-tap-list",
}
TARGET_UNIT_TARGET_ALLY_OR_RAID = 118
TARGET_UNIT_CASTER_AND_SUMMONS = 120

UNIT_CREATURE_KINDS = ("creature", "pet", "guardian", "totem", "minion", "vehicle")
SUMMON_KINDS = ("pet", "guardian", "totem", "minion")      # TempSummon subclasses (TemporarySummon.cpp:44)
GUARDIAN_MASK_KINDS = ("pet", "guardian")                  # UNIT_MASK_GUARDIAN (Guardian ctor :517, Pet derives)


# ---------------------------------------------------------------------------
# fixture access
# ---------------------------------------------------------------------------

def _raw_actor(world: World, uid: str) -> dict[str, Any]:
    for a in world.raw.get("actors", []):
        if a.get("id") == uid:
            return a
    if uid in world.actors:  # constructed programmatically
        a = world.actors[uid]
        return {"id": uid, "kind": a.kind, "owner": a.owner, "charmer": a.charmer, "group": a.group,
                "summoner": a.summoner, "facts": a.facts}
    raise FailClosed(f"fixture: unknown actor {uid!r}")


def _link(world: World, uid: str, key: str) -> str | None:
    raw = _raw_actor(world, uid)
    if key not in raw:
        if (raw.get("facts") or {}).get("profile") == "combat-sim":  # track B profile: absent link = empty GUID
            return None
        raise FailClosed(f"fixture: actor {uid!r} does not state {key!r} (null = empty GUID)")
    value = raw[key]
    if value is not None:
        world.actor(value)
    return value


def _fact(world: World, uid: str, key: str) -> Any:
    """An actor fact; B's ``combat-sim`` profile defaults apply (``relations.fact``)."""
    from .relations import fact
    return fact(world, uid, key)


def type_id(world: World, uid: str) -> str:
    """``GetTypeId()``: TYPEID_PLAYER / TYPEID_UNIT for the unit kinds."""
    kind = world.actor(uid).kind
    if kind == "player":
        return "PLAYER"
    if kind in UNIT_CREATURE_KINDS:
        return "UNIT"
    raise FailClosed(f"groups: actor {uid!r} of kind {kind!r} is not a Unit")


def _group(world: World, uid: str) -> dict[str, Any] | None:
    raw = _raw_actor(world, uid)
    if "group" not in raw:
        raise FailClosed(f"fixture: player {uid!r} does not state `group` (null = ungrouped)")
    g = raw["group"]
    if g is not None and ("id" not in g or "subgroup" not in g):
        raise FailClosed(f"fixture: player {uid!r} group must state `id` and `subgroup`")
    return g


# ---------------------------------------------------------------------------
# ownership
# ---------------------------------------------------------------------------

def charmer_or_owner(world: World, uid: str) -> str | None:
    """``GetCharmerOrOwner``: the charmer if charmed, else the owner (``SummonedBy``); one level.

    Mirrors: Unit.h:1220 (``IsCharmed() ? GetCharmer() : GetOwner()``), Unit.h:1235 ``IsCharmed``,
    Unit.h:1190 ``GetOwnerGUID`` = ``SummonedBy``; Object.cpp:1610 (GameObject -> ``CreatedBy`` owner).
    """
    kind = world.actor(uid).kind
    if kind == "gameobject":
        return _link(world, uid, "owner")
    type_id(world, uid)
    charmer = _link(world, uid, "charmer")
    if charmer is not None:
        return charmer
    return _link(world, uid, "owner")


def charmer_or_owner_or_self(world: World, uid: str) -> str | None:
    """Mirrors: Object.cpp:1618 ``WorldObject::GetCharmerOrOwnerOrSelf`` (one level, not recursive).

    Owner/charmer, else self **if a Unit**, else ``None`` (``ToUnit()`` of an ownerless
    GameObject is nullptr).  Single source of truth: ``relations.charmer_or_owner_or_self``.
    """
    from .relations import charmer_or_owner_or_self as impl
    return impl(world, uid)


def is_in_same_group_with(world: World, p1: str, p2: str) -> bool:
    """Mirrors: Player.cpp:2070 ``IsInSameGroupWith`` + Group.cpp:994 ``SameSubGroup``."""
    if p1 == p2:
        return True
    g1, g2 = _group(world, p1), _group(world, p2)
    return g1 is not None and g2 is not None and g1["id"] == g2["id"] and g1["subgroup"] == g2["subgroup"]


def is_in_same_raid_with(world: World, p1: str, p2: str) -> bool:
    """Mirrors: Player.cpp:2077 ``IsInSameRaidWith`` (same Group object; subgroup and raid flag unread)."""
    if p1 == p2:
        return True
    g1, g2 = _group(world, p1), _group(world, p2)
    return g1 is not None and g2 is not None and g1["id"] == g2["id"]


def _membership(world: World, a: str, b: str, same: Callable[[World, str, str], bool], name: str,
                trace: Trace | None) -> bool:
    def done(result: bool, why: str) -> bool:
        if trace is not None:
            trace.add(name, "Unit.cpp:12184-12218", output=result, inputs={"a": a, "b": b}, notes=[why])
        return result

    if a == b:
        return done(True, "this == unit")
    u1 = charmer_or_owner_or_self(world, a)
    u2 = charmer_or_owner_or_self(world, b)
    if u1 == u2:
        return done(True, f"same GetCharmerOrOwnerOrSelf {u1!r}")
    t1, t2 = type_id(world, u1), type_id(world, u2)
    if t1 == "PLAYER" and t2 == "PLAYER":
        return done(same(world, u1, u2), f"players {u1!r},{u2!r}: {same.__name__}")
    if t2 == "PLAYER" and t1 == "UNIT" and _fact(world, u1, "treated_as_raid_unit"):
        return done(True, f"{u1!r} TREAT_AS_RAID_UNIT")
    if t1 == "PLAYER" and t2 == "UNIT" and _fact(world, u2, "treated_as_raid_unit"):
        return done(True, f"{u2!r} TREAT_AS_RAID_UNIT")
    if t1 == "UNIT" and t2 == "UNIT":
        return done(_fact(world, u1, "faction") == _fact(world, u2, "faction"), "creatures: same faction")
    return done(False, f"player/creature pair {u1!r},{u2!r} without raid-unit flag")


def is_in_party_with(world: World, a: str, b: str, trace: Trace | None = None) -> bool:
    """``Unit::IsInPartyWith``.

    Mirrors: Unit.cpp:12184-12201.
    """
    return _membership(world, a, b, is_in_same_group_with, "groups.is_in_party_with", trace)


def is_in_raid_with(world: World, a: str, b: str, trace: Trace | None = None) -> bool:
    """``Unit::IsInRaidWith``.

    Mirrors: Unit.cpp:12203-12218.
    """
    return _membership(world, a, b, is_in_same_raid_with, "groups.is_in_raid_with", trace)


def is_summon(world: World, uid: str) -> bool:
    """Mirrors: Unit.h:748 ``IsSummon`` (UNIT_MASK_SUMMON, set by every TempSummon ctor, TemporarySummon.cpp:44)."""
    kind = world.actor(uid).kind
    if kind in SUMMON_KINDS:
        return True
    if kind == "player":
        return False
    if kind in ("creature", "vehicle"):
        return bool(_fact(world, uid, "is_summon"))  # combat-sim profile default: false
    raise FailClosed(f"groups: is_summon undefined for kind {kind!r}")


def is_summoned_by(world: World, uid: str, caster: str) -> bool:
    """TARGET_CHECK_SUMMONED: ``IsSummon() && GetSummonerGUID() == caster``.

    Mirrors: Spell.cpp:9369-9374; TemporarySummon.h:61 (``m_summonerGUID`` = the creating object).
    """
    if not is_summon(world, uid):
        return False
    return _link(world, uid, "summoner") == caster


# ---------------------------------------------------------------------------
# WorldObjectSpellTargetCheck membership part
# ---------------------------------------------------------------------------

def membership(world: World, caster: str, referer: str | None, target: str, check: str,
               trace: Trace | None = None) -> bool:
    """The membership predicate alone (no totem / assist / CheckTarget), as used by the check.

    ``PARTY`` -> ``referer.IsInPartyWith(target)``; ``RAID`` -> ``referer.IsInRaidWith``;
    ``RAID_CLASS`` -> same class as referer, then RAID; ``SUMMONED`` -> summoner == caster.
    A null or non-unit referer fails PARTY/RAID/RAID_CLASS (``if (!refUnit) return false``).

    Mirrors: Spell.cpp:9341-9374.
    """
    if check == "SUMMONED":
        return is_summoned_by(world, target, caster)
    if check not in ("PARTY", "RAID", "RAID_CLASS"):
        raise FailClosed(f"groups.membership: {check!r} is not a membership check")
    if referer is None or world.actor(referer).kind not in ("player",) + UNIT_CREATURE_KINDS:
        return False
    if check == "PARTY":
        return is_in_party_with(world, referer, target, trace)
    if check == "RAID_CLASS" and world.actor(referer).fact("class") != world.actor(target).fact("class"):
        return False
    return is_in_raid_with(world, referer, target, trace)


def group_check(world: World, caster: str, referer: str | None, target: str, check: str,
                check_target: Callable[[str], bool], valid_assist: Callable[[str, str], bool],
                conditions: Callable[[str], bool] | None = None, object_type: str = "UNIT",
                trace: Trace | None = None) -> bool:
    """``WorldObjectSpellTargetCheck::operator()`` for PARTY / RAID / RAID_CLASS / SUMMONED, in consumer order.

    Order (Spell.cpp:9306-9392):
    1. ``SpellInfo::CheckTarget(caster, target, implicit=true)`` (``check_target``, track B);
    2. corpse -> its owner *player* stands in for the unit (absent owner -> reject);
    3. PARTY: referer unit? -> not totem -> (non-corpse) ``caster.IsValidAssistTarget`` -> ``referer.IsInPartyWith``;
       RAID_CLASS: referer unit? -> class(referer) == class(unit) -> falls through to RAID;
       RAID: referer unit? -> not totem -> assist -> ``referer.IsInRaidWith``;
       SUMMONED: ``IsSummon`` && summoner == **caster** (no totem exclusion, no assist test);
    4. corpse object types: unit must be dead;
    5. implicit-target conditions (``conditions``; None = no condition list).

    Membership is tested **after** friendliness (only observable through traces: both are pure).
    ``caster`` is ``m_caster``; ``referer`` is the selector's referer (caster, explicit
    target, or last target -- Spell.cpp:1328-1378).
    """
    def out(result: bool, why: str) -> bool:
        if trace is not None:
            trace.add("groups.group_check", "Spell.cpp:9306-9392", output=result,
                      inputs={"caster": caster, "referer": referer, "target": target, "check": check}, notes=[why])
        return result

    if not check_target(target):
        return out(False, "SpellInfo::CheckTarget")
    unit = target
    is_corpse = world.actor(target).kind == "corpse"
    if is_corpse:
        owner = _link(world, target, "owner")
        if owner is None or world.actor(owner).kind != "player":
            return out(False, "corpse without in-map player owner")
        unit = owner
    if check in ("PARTY", "RAID", "RAID_CLASS"):
        if referer is None or world.actor(referer).kind not in ("player",) + UNIT_CREATURE_KINDS:
            return out(False, "referer is not a unit")
        if check == "RAID_CLASS" and world.actor(referer).fact("class") != world.actor(unit).fact("class"):
            return out(False, "RAID_CLASS: class differs from referer's")
        if world.actor(unit).kind == "totem":
            return out(False, "totem excluded")
        if not is_corpse and not valid_assist(caster, unit):
            return out(False, "caster->IsValidAssistTarget false")
        member = (is_in_party_with if check == "PARTY" else is_in_raid_with)(world, referer, unit, trace)
        if not member:
            return out(False, f"referer not in {check.lower()} with target")
    elif check == "SUMMONED":
        if not is_summoned_by(world, unit, caster):
            return out(False, "not summoned by caster")
    else:
        raise FailClosed(f"groups.group_check: {check!r} is not a membership check (track B owns it)")
    if object_type in ("CORPSE", "CORPSE_ALLY", "CORPSE_ENEMY"):
        alive = world.actor(unit).need("alive")
        if alive:
            return out(False, "corpse object type: unit alive")
    if conditions is not None and not conditions(target):
        return out(False, "implicit-target conditions")
    return out(True, "accepted")


# ---------------------------------------------------------------------------
# caster identities
# ---------------------------------------------------------------------------

def spell_caster(world: World, originate_from_controller: bool) -> str:
    """``Spell::m_caster``: the charmer/owner when ``SPELL_ATTR6_ORIGINATE_FROM_CONTROLLER`` and one exists.

    Mirrors: Spell.cpp:474-475.  ``world.caster`` is the object ``CastSpell`` was called on.
    """
    if not originate_from_controller:
        return world.caster
    return charmer_or_owner(world, world.caster) or world.caster


def original_caster(world: World, m_caster: str) -> str | None:
    """``m_originalCaster``: explicit original caster GUID, else ``m_caster``; null when not in world.

    Mirrors: Spell.cpp:510-521.  Fixture: ``original_caster`` (absent = ``m_caster``);
    actor fact ``in_world`` must be stated when it differs from ``m_caster``.
    """
    oc = world.original_caster or m_caster
    if oc == m_caster:
        return m_caster if world.actor(m_caster).kind in ("player",) + UNIT_CREATURE_KINDS else None
    return oc if world.actor(oc).fact("in_world") else None


def caster_object_target(world: World, selector: int, m_caster: str, trace: Trace | None = None) -> tuple[str | None, bool]:
    """``SelectImplicitCasterObjectTargets`` target resolution -> (target, checkIfValid).

    Mirrors: Spell.cpp:1742-1795.  CASTER is added without ``CheckTarget``; every other
    relationship target is added with ``checkIfValid = true``.  PET = ``GetGuardianPet``
    (``PetGUID``; only a unit with UNIT_MASK_GUARDIAN, Unit.cpp:6230); SUMMONER only for a
    TempSummon caster; MASTER = ``GetCharmerOrOwner`` (one level: a guardian owned by a pet
    resolves to the pet, not the player).
    """
    kind = world.actor(m_caster).kind
    target: str | None = None
    check = True
    if selector == 1:
        target, check = m_caster, False
    elif selector == 27:
        target = charmer_or_owner(world, m_caster)
    elif selector == 5:
        if kind in ("player",) + UNIT_CREATURE_KINDS:
            pet = world.actor(m_caster).fact("pet")
            if pet is not None and world.actor(pet).kind in GUARDIAN_MASK_KINDS:
                target = pet
    elif selector == 92:
        if kind in ("player",) + UNIT_CREATURE_KINDS and is_summon(world, m_caster):
            s = _link(world, m_caster, "summoner")
            target = s if s is not None and world.actor(s).kind in ("player",) + UNIT_CREATURE_KINDS else None
    elif selector == 94:
        if kind in ("player",) + UNIT_CREATURE_KINDS:
            target = world.actor(m_caster).fact("vehicle_base")
    elif selector == 150:
        if kind in ("player",) + UNIT_CREATURE_KINDS:
            target = world.actor(m_caster).fact("critter")
    else:
        raise FailClosed(f"groups.caster_object_target: selector {selector} not modelled here")
    if trace is not None:
        trace.add("groups.caster_object_target", "Spell.cpp:1742-1806", output=target,
                  inputs={"selector": selector, "m_caster": m_caster}, notes=[f"checkIfValid={check}"])
    return target, check


def ally_or_raid_mode(world: World, m_caster: str, explicit_unit: str | None, trace: Trace | None = None) -> str:
    """TARGET_UNIT_TARGET_ALLY_OR_RAID branch: ``none`` / ``explicit-only`` / ``search``.

    Mirrors: Spell.cpp:1393-1402 -- no explicit unit -> nothing; caster not a unit or not
    ``IsInRaidWith(target)`` -> only the explicit target (no area check at all, no
    RandomResize input beyond it); otherwise an area search centred on the explicit target
    (referer = explicit target), which does **not** force-include the target itself.
    """
    if explicit_unit is None:
        mode = "none"
    elif world.actor(m_caster).kind not in ("player",) + UNIT_CREATURE_KINDS:
        mode = "explicit-only"
    else:
        mode = "search" if is_in_raid_with(world, m_caster, explicit_unit, trace) else "explicit-only"
    if trace is not None:
        trace.add("groups.ally_or_raid_mode", "Spell.cpp:1393-1402", output=mode)
    return mode


def caster_and_summons_seed(m_caster: str) -> list[str]:
    """TARGET_UNIT_CASTER_AND_SUMMONS pushes ``m_caster`` first, then the SUMMONED area search.

    Mirrors: Spell.cpp:1403-1407.  The caster is not re-checked (it is not a summon of itself
    unless the fixture says so; a duplicate would merge in ``AddUnitTarget``).
    """
    return [m_caster]
