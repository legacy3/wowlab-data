"""Staged validation of an explicitly selected unit target.

Stages (kept separate on purpose -- one predicate can reject at several stages
with different results):

1. ``init``            ``Spell::InitExplicitTargets`` (Spell.cpp:621) -- which unit becomes
                       ``m_targets`` unit (object kept/removed, player selection, creature victim, self).
2. ``cast_prepare``    target-dependent part of ``Spell::CheckCast(strict=true)`` from
                       ``Spell::prepare`` (Spell.cpp:3492) incl. the TRIGGERED_IGNORE_TARGET_CHECK
                       mapping (3495, BAD_TARGETS only).                     -> *cast eligibility*
3. ``cast_complete``   ``CheckCast(strict=false)`` from ``Spell::_cast`` (3756); skipped when
                       ``_cast(skipCheck=true)`` (instant/cast-directly, 3579/3611).  -> *cast eligibility*
4. ``redirect``        ``Spell::SelectExplicitTargets`` (691; runs in ``_cast`` *after* 3756).
5. ``recipient``       ``SelectImplicitTargetObjectTargets`` (1807) -> ``AddUnitTarget(unit, mask,
                       checkIfValid=true, implicit=false)`` (2443): CheckEffectTarget per effect,
                       CheckTarget, effect immunity, SpellHitResult (canImmune=false). -> *recipient selection*
6. ``launch``          ``PreprocessSpellLaunch`` (8526): immunity with the final mask.
7. ``hit``             ``TargetInfo::PreprocessTarget`` (2749) / ``Spell::PreprocessSpellHit`` (3109) /
                       ``TargetInfo::DoTargetSpellHit`` (2796).                 -> *effect application*

Only target-dependent checks are evaluated; CheckCast checks that do not read the
target (cooldown, power, shapeshift, caster auras, items, ...) are an explicit
boundary (listed in :data:`CHECKCAST_BOUNDARY`) and are assumed to pass.

Spell view contract (dict or object; ``has_attr(name)`` on objects,
``attributes`` list of names on dicts)
------------------------------------------------------------------------------
``id``, ``is_positive``, ``is_affecting_area``, ``is_allowing_dead_target``,
``is_passive``, ``explicit_target_mask``, ``required_explicit_target_mask`` (ints,
SpellDefines.h TARGET_FLAG_*), ``dmg_class`` (0 none, 1 magic, 2 melee, 3 ranged),
``target_creature_type`` (mask), ``family``, ``category``, ``mechanic``,
``aura_restrictions`` ({target_aura_state, exclude_target_aura_state, target_aura_spell,
exclude_target_aura_spell, target_aura_type, exclude_target_aura_type}),
``range`` (null or {flags, min: [hostile, friendly], max: [hostile, friendly]}),
``facing_caster_flags``, ``effects`` ([{index, effect, aura, explicit: bool}]),
``cast_time_ms``, ``has_hit_delay``, ``is_next_melee_swing``, ``resurrect`` (bool),
``has_only_damage_effects``, ``los_disabled`` (DisableMgr SPELL_DISABLE_LOS, world DB).
Missing keys fail closed only when the stage actually reads them.

Runtime facts beyond :mod:`targeting.relations`: ``creature_type``, ``magnet``,
``aura_states``, ``aura_types``, ``ghost``, ``in_combat``, ``tapped_by_other``, ``aoe_immune``,
``grounded``, ``cc_breakable_by_damage``, ``visible`` (players), ``in_flight``, ``evading``,
``immune_effects`` ([effect index]), ``immune_to_spell``, ``damage_immune``, ``charmed``,
``vehicle_kit_controllable``, ``level``, ``moving``, ``bounding_radius`` (Actor),
``combat_reach`` (Actor), ``magnet_auras`` ([{caster, kind: magic|melee}]),
``interfere_targeting`` (list; non-empty fails closed), ``ignore_los_on_me``, and
per-stage ``hit`` facts (``alive_at_hit``, ``evading_at_hit``, ``immune_at_hit``,
``non_attackable_at_hit``, ``sanctuary_during_flight``, ``hit_roll``).
"""

from __future__ import annotations

import math
import struct
from typing import Any

from . import FailClosed
from . import relations as R
from .trace import Trace

# SpellDefines.h:311-343
TARGET_FLAG = {
    "UNIT": 0x2, "UNIT_RAID": 0x4, "UNIT_PARTY": 0x8, "ITEM": 0x10, "SOURCE_LOCATION": 0x20,
    "DEST_LOCATION": 0x40, "UNIT_ENEMY": 0x80, "UNIT_ALLY": 0x100, "CORPSE_ENEMY": 0x200,
    "UNIT_DEAD": 0x400, "GAMEOBJECT": 0x800, "TRADE_ITEM": 0x1000, "STRING": 0x2000,
    "GAMEOBJECT_ITEM": 0x4000, "CORPSE_ALLY": 0x8000, "UNIT_MINIPET": 0x10000, "GLYPH_SLOT": 0x20000,
    "DEST_TARGET": 0x40000, "EXTRA_TARGETS": 0x80000, "UNIT_PASSENGER": 0x100000,
}
TF = TARGET_FLAG
UNIT_MASK = TF["UNIT"] | TF["UNIT_RAID"] | TF["UNIT_PARTY"] | TF["UNIT_ENEMY"] | TF["UNIT_ALLY"] | \
    TF["UNIT_DEAD"] | TF["UNIT_MINIPET"] | TF["UNIT_PASSENGER"]
GAMEOBJECT_MASK = TF["GAMEOBJECT"] | TF["GAMEOBJECT_ITEM"]
CORPSE_MASK = TF["CORPSE_ALLY"] | TF["CORPSE_ENEMY"]
RELATION_FLAGS = TF["UNIT_ENEMY"] | TF["UNIT_ALLY"] | TF["UNIT_RAID"] | TF["UNIT_PARTY"] | \
    TF["UNIT_MINIPET"] | TF["UNIT_PASSENGER"]

SPELL_RANGE_MELEE = 1      # Spell.h:187
SPELL_RANGE_RANGED = 2     # Spell.h:188
MAX_SPELL_RANGE_TOLERANCE = 3.0   # Spell.h:85
NOMINAL_MELEE_RANGE = 5.0         # UnitDefines.h:31
MIN_MELEE_REACH = 2.0             # UnitDefines.h:30
SPELL_FACING_FLAG_INFRONT = 0x1
MECHANIC_DISARM = 1
SPELLFAMILY_WARLOCK = 5
DMG_CLASS = {0: "NONE", 1: "MAGIC", 2: "MELEE", 3: "RANGED"}
CHARM_AURAS = {2: "MOD_POSSESS", 6: "MOD_CHARM", 128: "MOD_POSSESS_PET", 177: "AOE_CHARM"}


class CastResult(str):
    """A SpellCastResult name whose truth value is ``== SPELL_CAST_OK`` (so ``bool(result)`` is safe)."""

    def __bool__(self) -> bool:
        return str.__eq__(self, "SPELL_CAST_OK")


OK = CastResult("SPELL_CAST_OK")

#: CheckCast (Spell.cpp:5742) checks that do not read the explicit target -- boundary, assumed OK
CHECKCAST_BOUNDARY = (
    "5745 caster dead", "5749 GO immune", "5757-5801 cooldown/ranged ready", "5804 custom error",
    "5810 caster aurastate", "5815 GCD", "5826-5832 indoors/outdoors", "5836 charmed", "5842 shapeshift",
    "5860-5905 stealth/caster aura spell/combat/mount", "5917 spell conditions (fail closed if stated)",
    "6012-6092 pet/battleground/taxi/focus/items", "6101 power", "6108 caster auras",
    "6116 script CheckCast hooks", "6120+ per-effect checks",
)


def _f32(x: float) -> float:
    return struct.unpack("<f", struct.pack("<f", float(x)))[0]


# ---------------------------------------------------------------------------
# spell view access
# ---------------------------------------------------------------------------
_MISSING = object()


def sv(spell, key: str) -> Any:
    """Read a spell-view key (dict per the module contract, or a ``targeting.oracle.SpellView``)."""
    if isinstance(spell, dict):
        if key not in spell:
            raise FailClosed(f"explicit: spell view does not state {key!r}")
        return spell[key]
    extra = getattr(spell, "track_b", None)          # optional dict of contract keys carried by the view
    if isinstance(extra, dict) and key in extra:
        return extra[key]
    adapt = _VIEW_ADAPTERS.get(key)
    if adapt is not None:
        return adapt(spell)
    value = getattr(spell, key, _MISSING)
    if value is _MISSING:
        raise FailClosed(f"explicit: spell view does not provide {key!r}")
    return value() if callable(value) else value


def _view_is_affecting_area(view) -> bool:
    """Mirrors: SpellInfo.cpp:1693 ``IsAffectingArea`` (IsTargetingArea, PERSISTENT_AREA_AURA, area auras)."""
    from .attributes import AREA_AURA_EFFECTS
    from .selectors import info
    return any(e.is_effect and (info(e.target_a).is_area or info(e.target_b).is_area or e.effect == 27
                                or e.effect in AREA_AURA_EFFECTS) for e in view.effects)


def _view_masks(view) -> tuple[int, int]:
    from .composition import EffectSlots, explicit_target_mask
    rng = view.range
    slots = [EffectSlots(e.index, e.effect, e.target_a, e.target_b, e.attributes) for e in view.effects]
    return explicit_target_mask(slots, max_range_negative=rng.max[0] if rng else 0.0,
                                max_range_positive=rng.max[1] if rng else 0.0, targets=view.targets,
                                attributes13=view.attributes[13])


_VIEW_ADAPTERS = {
    "is_affecting_area": _view_is_affecting_area,
    "explicit_target_mask": lambda v: _view_masks(v)[0],
    "required_explicit_target_mask": lambda v: _view_masks(v)[1],
    "dmg_class": lambda v: v.need("dmg_class"),
    "is_passive": lambda v: v.has_attr("SPELL_ATTR0_PASSIVE"),
    "has_hit_delay": lambda v: v.speed > 0.0 or v.launch_delay > 0.0,       # SpellInfo.cpp:1926
}


def has_attr(spell, name: str) -> bool:
    return R._spell_attr(spell, name)


def _restriction(spell, key: str) -> int:
    rest = sv(spell, "aura_restrictions")
    if rest is None:
        return 0
    if key not in rest:
        raise FailClosed(f"explicit: aura_restrictions does not state {key!r}")
    return int(rest[key] or 0)


def _tfact(world, actor_id: str, key: str) -> Any:
    """Target runtime fact: stated, or combat-sim profile default where the machinery is absent."""
    a = world.actor(actor_id)
    if key in a.facts:
        return a.facts[key]
    if a.facts.get("profile") == R.PROFILE and key in _PROFILE_DEFAULTS:
        return _PROFILE_DEFAULTS[key]
    raise FailClosed(f"explicit: actor {actor_id!r} does not state {key!r}")


_PROFILE_DEFAULTS = {
    "ghost": False, "tapped_by_other": False, "aoe_immune": False, "grounded": True, "visible": True,
    "in_flight": False, "magnet": False, "interfere_targeting": (), "prevent_resurrection": False,
    "ignore_creature_type_requirements": False, "charmed": False, "vehicle_kit_controllable": False,
    "magnet_auras": (), "evading": False, "dynobj_los_source": None,
}


# ---------------------------------------------------------------------------
# SpellInfo::CheckExplicitTarget / CheckTarget / CheckTargetCreatureType
# ---------------------------------------------------------------------------
def check_explicit_target(world, spell, caster: str, target: str | None, trace: Trace | None = None,
                          *, item_target: bool = False) -> str:
    """Mirrors: SpellInfo.cpp:2530 ``SpellInfo::CheckExplicitTarget`` (uses RequiredExplicitTargetMask)."""
    needed = int(sv(spell, "required_explicit_target_mask"))
    path: list[str] = []
    result = OK
    if target is None:
        if needed & (UNIT_MASK | GAMEOBJECT_MASK | CORPSE_MASK):
            if not (needed & TF["GAMEOBJECT_ITEM"]) or not item_target:
                result = "SPELL_FAILED_BAD_TARGETS"
                path.append("2537:no-object-target")
    elif R.is_unit(world, target) and needed & RELATION_FLAGS:
        caster_unit = R.is_unit(world, caster)
        result = "SPELL_FAILED_BAD_TARGETS"
        path.append("2543:relation-flags")
        if needed & TF["UNIT_ENEMY"] and R.valid_attack(world, caster, target, spell, trace):
            result = OK
            path.append("2547:enemy-ok")
        elif (needed & TF["UNIT_ALLY"]
              or (needed & TF["UNIT_PARTY"] and caster_unit and R._in_party_with(world, caster, target, trace))
              or (needed & TF["UNIT_RAID"] and caster_unit and R._in_raid_with(world, caster, target, trace))) \
                and R.valid_assist(world, caster, target, spell, trace):
            result = OK
            path.append("2552:assist-ok")
        elif needed & TF["UNIT_MINIPET"] and caster_unit and _tfact(world, caster, "critter") == target:
            result = OK
            path.append("2555:minipet")
        elif needed & TF["UNIT_PASSENGER"] and caster_unit:
            raise FailClosed("explicit: UNIT_PASSENGER (vehicle) explicit check is out of scope")
    else:
        path.append("2563:no-relation-flags")
    result = CastResult(result)
    if trace is not None:
        trace.add("explicit.check_explicit_target", "SpellInfo.cpp:2530", output=result,
                  inputs={"caster": caster, "target": target, "required_mask": needed}, notes=path)
    return result


def check_target_creature_type(world, spell, target: str) -> bool:
    """Mirrors: SpellInfo.cpp:2616 ``SpellInfo::CheckTargetCreatureType``."""
    if int(sv(spell, "family")) == SPELLFAMILY_WARLOCK and int(sv(spell, "category")) == 1179:
        return not R.is_player(world, target)
    if _tfact(world, target, "magnet"):
        return True
    wanted = int(sv(spell, "target_creature_type"))
    if not wanted:
        return True
    ctype = int(_tfact(world, target, "creature_type"))
    mask = (1 << (ctype - 1)) if ctype >= 1 else 0          # Unit.cpp:9476
    return not mask or bool(mask & wanted) or bool(_tfact(world, target, "ignore_creature_type_requirements"))


def check_target(world, spell, caster: str, target: str, implicit: bool, trace: Trace | None = None) -> str:
    """Mirrors: SpellInfo.cpp:2329 ``SpellInfo::CheckTarget``."""
    path: list[str] = []
    result = CastResult(_check_target(world, spell, caster, target, implicit, path))
    if trace is not None:
        trace.add("explicit.check_target", "SpellInfo.cpp:2329", output=result,
                  inputs={"caster": caster, "target": target, "implicit": implicit}, notes=path)
    return result


def _check_target(world, spell, caster, target, implicit, path) -> str:
    if has_attr(spell, "SPELL_ATTR1_EXCLUDE_CASTER") and caster == target:
        path.append("2331:exclude-caster")
        return "SPELL_FAILED_BAD_TARGETS"
    if not R.can_see(world, caster, target, implicit):
        path.append("2341:cannot-see")
        return "SPELL_FAILED_BAD_TARGETS"
    t = world.actor(target)
    if has_attr(spell, "SPELL_ATTR8_ONLY_TARGET_IF_SAME_CREATOR"):
        if (world.actor(caster).creator or caster) != (t.creator or target):
            path.append("2356:different-creator")
            return "SPELL_FAILED_BAD_TARGETS"
    unit_target = target
    if R.is_unit(world, target):
        if has_attr(spell, "SPELL_ATTR1_ONLY_PEACEFUL_TARGETS") and (
                _tfact(world, target, "in_combat") or R.has_unit_flag(world, target, "PET_IN_COMBAT")):
            path.append("2364:target-affecting-combat")
            return "SPELL_FAILED_TARGET_AFFECTING_COMBAT"
        ghosts = has_attr(spell, "SPELL_ATTR3_ONLY_ON_GHOSTS")
        if ghosts != bool(_tfact(world, target, "ghost")):
            path.append("2368:ghost-mismatch")
            return "SPELL_FAILED_TARGET_NOT_GHOST" if ghosts else "SPELL_FAILED_BAD_TARGETS"
        if caster != target and R.is_player(world, caster):
            if has_attr(spell, "SPELL_ATTR2_CANNOT_CAST_ON_TAPPED") and R.is_creature(world, target) \
                    and _tfact(world, target, "tapped_by_other"):
                path.append("2383:tapped")
                return "SPELL_FAILED_CANT_CAST_ON_TAPPED"
            if has_attr(spell, "SPELL_ATTR0_CU_PICKPOCKET"):
                raise FailClosed("explicit: pickpocket loot check is out of scope")
            if int(sv(spell, "mechanic")) == MECHANIC_DISARM:
                if not _tfact(world, target, "has_weapon"):
                    path.append("2402:no-weapons")
                    return "SPELL_FAILED_TARGET_NO_WEAPONS"
        if has_attr(spell, "SPELL_ATTR8_ONLY_TARGET_OWN_SUMMONS"):
            from . import groups
            if not R.is_summon(world, target) or not groups.is_summoned_by(world, target, caster):
                path.append("2412:not-own-summon")
                return "SPELL_FAILED_BAD_TARGETS"
        if has_attr(spell, "SPELL_ATTR3_NOT_ON_AOE_IMMUNE") and _tfact(world, target, "aoe_immune"):
            path.append("2416:aoe-immune")
            return "SPELL_FAILED_BAD_TARGETS"
        if has_attr(spell, "SPELL_ATTR9_TARGET_MUST_BE_GROUNDED") and not _tfact(world, target, "grounded"):
            path.append("2422:not-grounded")
            return "SPELL_FAILED_TARGET_NOT_GROUNDED"
    elif t.kind == "corpse":
        if t.facts.get("bones"):
            path.append("2429:bones")
            return "SPELL_FAILED_BAD_TARGETS"
        if t.owner is None or not R.is_player(world, t.owner):
            path.append("2435:ownerless-corpse")
            return "SPELL_FAILED_BAD_TARGETS"
        unit_target = t.owner
    else:
        path.append("2438:non-unit-ok")
        return OK
    if not R.is_player(world, unit_target):
        if has_attr(spell, "SPELL_ATTR3_ONLY_ON_PLAYER"):
            path.append("2444:only-on-player")
            return "SPELL_FAILED_TARGET_NOT_PLAYER"
        # Unit::IsControlledByPlayer (m_ControlledByPlayer) approximated by "has an affecting player"
        if has_attr(spell, "SPELL_ATTR5_NOT_ON_PLAYER_CONTROLLED_NPC") and \
                R.affecting_player(world, unit_target) is not None:
            path.append("2447:player-controlled-npc")
            return "SPELL_FAILED_TARGET_IS_PLAYER_CONTROLLED"
    elif has_attr(spell, "SPELL_ATTR5_NOT_ON_PLAYER"):
        path.append("2450:not-on-player")
        return "SPELL_FAILED_TARGET_IS_PLAYER"
    if not sv(spell, "is_allowing_dead_target") and not world.actor(unit_target).need("alive"):
        path.append("2453:dead")
        return "SPELL_FAILED_TARGETS_DEAD"
    if implicit and has_attr(spell, "SPELL_ATTR6_DO_NOT_CHAIN_TO_CROWD_CONTROLLED_TARGETS") and \
            _tfact(world, unit_target, "cc_breakable_by_damage"):
        path.append("2456:crowd-controlled(implicit-only)")
        return "SPELL_FAILED_BAD_TARGETS"
    if not check_target_creature_type(world, spell, unit_target):
        path.append("2463:creature-type")
        return "SPELL_FAILED_TARGET_IS_PLAYER" if R.is_player(world, target) else "SPELL_FAILED_BAD_TARGETS"
    if unit_target != caster and (R.affecting_player(world, caster) is not None or not sv(spell, "is_positive")) \
            and R.is_player(world, unit_target):
        if not _tfact(world, unit_target, "visible") or R.fact(world, unit_target, "game_master"):
            path.append("2474:gm-or-invisible")
            return "SPELL_FAILED_BM_OR_INVISGOD"
    if _tfact(world, unit_target, "in_flight") and not has_attr(spell, "SPELL_ATTR0_CU_ALLOW_INFLIGHT_TARGET"):
        path.append("2482:in-flight")
        return "SPELL_FAILED_BAD_TARGETS"
    if R.is_unit(world, caster):
        is_vehicle = world.actor(caster).kind == "vehicle"
        # NB 2494 compares the caster's charmer/owner with `target` (the corpse object, not its owner)
        if not is_vehicle and R.charmer_or_owner(world, caster) != target:
            states = None
            for key, want in (("target_aura_state", True), ("exclude_target_aura_state", False)):
                state = _restriction(spell, key)
                if state:
                    if states is None:
                        states = set(_tfact(world, unit_target, "aura_states"))
                    if (state in states) != want:
                        path.append("2496:aura-state" if want else "2499:exclude-aura-state")
                        return "SPELL_FAILED_TARGET_AURASTATE"
    aura_spell = _restriction(spell, "target_aura_spell")
    if aura_spell and not world.actor(unit_target).has_aura(aura_spell):
        path.append("2504:target-aura-spell")
        return "SPELL_FAILED_TARGET_AURASTATE"
    ex_spell = _restriction(spell, "exclude_target_aura_spell")
    if ex_spell and world.actor(unit_target).has_aura(ex_spell):
        path.append("2507:exclude-target-aura-spell")
        return "SPELL_FAILED_TARGET_AURASTATE"
    for key, want, line in (("target_aura_type", True, "2510"), ("exclude_target_aura_type", False, "2513")):
        aura_type = _restriction(spell, key)
        if aura_type and ((aura_type in set(_tfact(world, unit_target, "aura_types"))) != want):
            path.append(f"{line}:{key}")
            return "SPELL_FAILED_TARGET_AURASTATE"
    if sv(spell, "resurrect") and _tfact(world, unit_target, "prevent_resurrection") and \
            not has_attr(spell, "SPELL_ATTR7_BYPASS_NO_RESURRECT_AURA"):
        path.append("2516:prevent-resurrection")
        return "SPELL_FAILED_TARGET_CANNOT_BE_RESURRECTED"
    if has_attr(spell, "SPELL_ATTR8_ENFORCE_IN_COMBAT_RESSURECTION_LIMIT"):
        raise FailClosed("explicit: instance combat-resurrection charges are out of scope")
    path.append("2527:ok")
    return OK


# ---------------------------------------------------------------------------
# geometry helpers (TEMPORARY local binary32 versions until targeting.geometry lands)
# ---------------------------------------------------------------------------
def _pos(world, actor_id: str) -> tuple[float, float, float]:
    return tuple(_f32(v) for v in world.actor(actor_id).need("pos"))


def exact_dist_sq(world, a: str, b: str) -> float:
    """Position.h:121 ``GetExactDistSq`` (float arithmetic)."""
    pa, pb = _pos(world, a), _pos(world, b)
    dx, dy, dz = (_f32(pa[i] - pb[i]) for i in range(3))
    return _f32(_f32(_f32(dx * dx) + _f32(dy * dy)) + _f32(dz * dz))


def _normalize(o: float) -> float:
    """Position.cpp:207 ``NormalizeOrientation``."""
    two_pi = _f32(2.0 * _f32(math.pi))
    if o < 0:
        return _f32(-math.fmod(-o, two_pi) + two_pi)
    return _f32(math.fmod(o, two_pi))


def has_in_arc(world, a: str, b: str, arc: float, border: float = 2.0) -> bool:
    """Position.cpp:173 ``Position::HasInArc`` (atan2 evaluated in double then narrowed: approximation)."""
    try:
        from . import geometry  # Track C
    except ImportError:
        geometry = None
    if geometry is not None and hasattr(geometry, "has_in_arc_actors"):
        return geometry.has_in_arc_actors(world, a, b, arc, border)
    if a == b:
        return True
    arc = _normalize(_f32(arc))
    pa, pb = _pos(world, a), _pos(world, b)
    dx, dy = _f32(pb[0] - pa[0]), _f32(pb[1] - pa[1])
    absolute = _normalize(_f32(math.atan2(dy, dx)))
    angle = _normalize(_f32(absolute - _f32(world.actor(a).need("orientation"))))
    if angle > _f32(math.pi):
        angle = _f32(angle - _f32(2.0 * _f32(math.pi)))
    lborder = _f32(-1 * (arc / border))
    rborder = _f32(arc / border)
    return lborder <= angle <= rborder


def melee_range(world, a: str, b: str) -> float:
    """Unit.cpp:701 ``Unit::GetMeleeRange``."""
    r = _f32(_f32(_f32(world.actor(a).need("combat_reach")) + _f32(world.actor(b).need("combat_reach"))) + _f32(4.0 / 3.0))
    return max(r, NOMINAL_MELEE_RANGE)


def within_boundary_radius(world, a: str, b: str) -> bool:
    """Unit.cpp:707 ``Unit::IsWithinBoundaryRadius`` (same map/phase assumed by profile)."""
    radius = max(_f32(world.actor(b).need("bounding_radius")), MIN_MELEE_REACH)
    return exact_dist_sq(world, a, b) < _f32(radius * radius)


# ---------------------------------------------------------------------------
# Spell::CheckRange / GetMinMaxRange
# ---------------------------------------------------------------------------
def min_max_range(world, spell, caster: str, target: str | None, strict: bool, path: list[str]) -> tuple[float, float]:
    """Mirrors: Spell.cpp:7329 ``Spell::GetMinMaxRange``."""
    if strict and sv(spell, "is_next_melee_swing"):
        path.append("7336:next-melee-swing")
        return 0.0, 100.0
    rmin = rmax = 0.0
    range_mod = 0.0
    entry = sv(spell, "range")
    caster_unit = R.is_unit(world, caster)
    if entry is not None:
        flags = int(entry["flags"])
        if flags & SPELL_RANGE_MELEE:
            if caster_unit:
                range_mod = melee_range(world, caster, target if target else caster)
            path.append("7344:melee-flag(range entry values ignored)")
        else:
            melee = 0.0
            if flags & SPELL_RANGE_RANGED and caster_unit:
                melee = melee_range(world, caster, target if target else caster)
            # Object.cpp:1672: positive = target ? !IsHostileTo(target) : true
            positive = True if target is None else not R.is_hostile(world, caster, target)
            idx = 1 if positive else 0
            path.append(f"1674:range-column-{'friendly' if positive else 'hostile'}")
            rmin, rmax = _f32(entry["min"][idx]), _f32(entry["max"][idx])
            rmin = _f32(rmin + melee)
            if target is not None:
                range_mod = _f32(_f32(world.actor(caster).need("combat_reach")) + _f32(world.actor(target).need("combat_reach")))
                if rmin > 0.0 and not (flags & SPELL_RANGE_RANGED):
                    rmin = _f32(rmin + range_mod)
        if target is not None and caster_unit and _tfact(world, target, "moving") and _tfact(world, caster, "moving") \
                and ((flags & SPELL_RANGE_MELEE) or R.is_player(world, target)):
            range_mod = _f32(range_mod + _f32(8.0 / 3.0))
            path.append("7374:movement-bonus")
    else:
        path.append("7341:no-range-entry(max=0)")
    if has_attr(spell, "SPELL_ATTR0_USES_RANGED_SLOT") and R.is_player(world, caster):
        pct = _tfact(world, caster, "ranged_mod_range")
        if pct is not None:
            rmax = _f32(rmax * _f32(pct * _f32(0.01)))
    mod = world.modifiers.get("range")
    if mod is None:
        raise FailClosed("explicit: fixture modifiers.range ({flat, pct}) not stated (SpellModOp::Range)")
    rmax = _f32((rmax + mod.get("flat", 0.0)) * mod.get("pct", 1.0))
    rmax = _f32(rmax + range_mod)
    return rmin, rmax


def check_range(world, spell, caster: str, target: str | None, strict: bool, trace: Trace | None = None) -> str:
    """Mirrors: Spell.cpp:7274 ``Spell::CheckRange`` (unit target part; GO/dest parts not modelled)."""
    path: list[str] = []
    result = OK
    if not strict and int(sv(spell, "cast_time_ms")) == 0:
        path.append("7277:instant-not-strict-skip")
    else:
        rmin, rmax = min_max_range(world, spell, caster, target, strict, path)
        entry = sv(spell, "range")
        if entry is not None and int(entry["flags"]) != SPELL_RANGE_MELEE and not strict:
            rmax = _f32(rmax + min(MAX_SPELL_RANGE_TOLERANCE, _f32(rmax * _f32(0.1))))
            path.append("7284:tolerance")
        rmin, rmax = _f32(rmin * rmin), _f32(rmax * rmax)
        if target is not None and target != caster:
            d = exact_dist_sq(world, caster, target)
            if d > rmax:
                result = "SPELL_FAILED_OUT_OF_RANGE"
                path.append("7294:beyond-max")
            elif rmin > 0.0 and d < rmin:
                result = "SPELL_FAILED_OUT_OF_RANGE"
                path.append("7297:inside-min")
            elif R.is_player(world, caster) and (int(sv(spell, "facing_caster_flags")) & SPELL_FACING_FLAG_INFRONT) \
                    and not has_in_arc(world, caster, target, math.pi) \
                    and not within_boundary_radius(world, caster, target):
                result = "SPELL_FAILED_UNIT_NOT_INFRONT"
                path.append("7302:not-infront")
    result = CastResult(result)
    if trace is not None:
        trace.add("explicit.check_range", "Spell.cpp:7274", output=result,
                  inputs={"caster": caster, "target": target, "strict": strict}, notes=path)
    return result


# ---------------------------------------------------------------------------
# LOS
# ---------------------------------------------------------------------------
def spell_los(world, spell, source: str, target: str) -> bool:
    """Mirrors: Spell.cpp:9256 ``Spell::IsWithinLOS`` (evaluation order is an OR; blocked LOS read last)."""
    if has_attr(spell, "SPELL_ATTR2_IGNORE_LINE_OF_SIGHT"):
        return True
    if world.in_los(source, target):
        return True
    if sv(spell, "los_disabled"):
        return True
    if R.is_creature(world, target) and _tfact(world, target, "ignore_los_on_me"):
        return True
    return False


# ---------------------------------------------------------------------------
# CheckCast (target part)
# ---------------------------------------------------------------------------
def cast_eligibility(world, spell, target: str | None, strict: bool, trace: Trace | None = None) -> str:
    """Target-dependent part of ``Spell::CheckCast`` (Spell.cpp:5742), in consumer order."""
    caster = world.caster
    path: list[str] = []
    result = CastResult(_cast_eligibility(world, spell, caster, target, strict, trace, path))
    if trace is not None:
        trace.add("explicit.cast_eligibility", "Spell.cpp:5742", output=result,
                  inputs={"target": target, "strict": strict}, notes=path)
    return result


def _cast_eligibility(world, spell, caster, target, strict, trace, path) -> str:
    if world.spell.get("conditions"):
        raise FailClosed("explicit: CONDITION_SOURCE_TYPE_SPELL conditions (Spell.cpp:5917) are not modelled")
    passive_self = sv(spell, "is_passive") and (target is None or target == caster)
    if not passive_self:
        check_caster = caster
        original = world.original_caster
        if original is not None and world.actor(caster).kind != "gameobject":
            check_caster = original
            path.append("5947:explicit-check-uses-original-caster")
        res = check_explicit_target(world, spell, check_caster, target, trace)
        if res != OK:
            path.append("5949:CheckExplicitTarget")
            return res
    else:
        path.append("5940:passive-self-skip")
    if target is not None and R.is_unit(world, target):
        res = check_target(world, spell, caster, target, world.actor(caster).kind == "gameobject", trace)
        if res != OK:
            path.append("5956:CheckTarget")
            return res
        if int(sv(spell, "dmg_class")) != 2 and not has_attr(spell, "SPELL_ATTR2_IGNORE_LINE_OF_SIGHT") \
                and R.is_unit(world, caster):
            if _tfact(world, caster, "interfere_targeting") or _tfact(world, target, "interfere_targeting"):
                raise FailClosed("explicit: SPELL_AURA_INTERFERE_*_TARGETING (5961) not modelled")
        if target != caster:
            if has_attr(spell, "SPELL_ATTR0_CU_REQ_CASTER_BEHIND_TARGET") and has_in_arc(world, target, caster, math.pi):
                path.append("5986:not-behind")
                return "SPELL_FAILED_NOT_BEHIND"
            if has_attr(spell, "SPELL_ATTR0_CU_REQ_TARGET_FACING_CASTER") and not has_in_arc(world, target, caster, math.pi):
                path.append("5990:not-infront")
                return "SPELL_FAILED_NOT_INFRONT"
            if world.actor(caster).kind != "gameobject":
                los_source = caster
                if world.spell.get("triggered_by_aura") and _tfact(world, caster, "dynobj_los_source"):
                    los_source = _tfact(world, caster, "dynobj_los_source")
                    path.append("5998:dynobj-los-source")
                # IsWithinLOS(losTarget, target, targetAsSourceLocation=true): ray from target to source
                if not spell_los(world, spell, los_source, target):
                    path.append("6001:los")
                    return "SPELL_FAILED_LINE_OF_SIGHT"
    res = check_range(world, spell, caster, target, strict, trace)
    if res != OK:
        path.append("6097:CheckRange")
        return res
    return OK


# ---------------------------------------------------------------------------
# InitExplicitTargets / SelectExplicitTargets
# ---------------------------------------------------------------------------
def init_explicit_targets(world, spell, trace: Trace | None = None) -> str | None:
    """Mirrors: Spell.cpp:621 ``Spell::InitExplicitTargets`` (object/unit part)."""
    needed = int(sv(spell, "explicit_target_mask"))
    caster = world.caster
    path: list[str] = []
    unit = world.explicit.get("unit")
    if unit is not None:
        kind = world.actor(unit).kind
        if (R.is_unit(world, unit) and not needed & (UNIT_MASK | CORPSE_MASK)) \
                or (kind == "gameobject" and not needed & GAMEOBJECT_MASK) \
                or (kind == "corpse" and not needed & CORPSE_MASK):
            path.append("637:object-target-removed")
            unit = None
        else:
            path.append("630:object-target-kept")
    elif needed & UNIT_MASK:
        chosen = None
        if R.is_player(world, caster):
            sel = world.explicit.get("selection")
            if sel is not None and R.is_unit(world, sel) and check_explicit_target(world, spell, caster, sel, trace) == OK:
                chosen = sel
                path.append("651:player-selection")
        elif R.is_unit(world, caster) and needed & (TF["UNIT_ENEMY"] | TF["UNIT"]):
            chosen = world.explicit.get("victim")
            path.append("655:creature-victim")
        if chosen is None and not needed & (TF["UNIT_ENEMY"] | TF["UNIT_DEAD"] | TF["UNIT_MINIPET"] | TF["UNIT_PASSENGER"]):
            chosen = caster if R.is_unit(world, caster) else None
            path.append("659:self-fallback")
        unit = chosen
    if trace is not None:
        trace.add("explicit.init", "Spell.cpp:621", output=unit, inputs={"mask": needed}, notes=path)
    return unit


def select_explicit_targets(world, spell, target: str | None, trace: Trace | None = None) -> str | None:
    """Mirrors: Spell.cpp:691 ``Spell::SelectExplicitTargets`` with Object.cpp:2602 / Unit.cpp:6581 redirects."""
    caster = world.caster
    path: list[str] = []
    result = target
    if target is not None and R.is_unit(world, target):
        mask = int(sv(spell, "explicit_target_mask"))
        if (mask & TF["UNIT_ENEMY"]) or ((mask & TF["UNIT"]) and not R.is_friendly(world, caster, target, trace)):
            dmg = int(sv(spell, "dmg_class"))
            redirect = None
            auras = list(_tfact(world, target, "magnet_auras"))
            if dmg == 1:
                if has_attr(spell, "SPELL_ATTR0_IS_ABILITY") or has_attr(spell, "SPELL_ATTR1_NO_REDIRECTION") \
                        or has_attr(spell, "SPELL_ATTR0_NO_IMMUNITIES"):
                    path.append("2605:not-redirectable")
                else:
                    for aura in auras:
                        if aura.get("kind") != "magic" or aura.get("caster") is None:
                            continue
                        magnet = aura["caster"]
                        if check_explicit_target(world, spell, caster, magnet, trace) == OK \
                                and R.valid_attack(world, caster, magnet, spell, trace):
                            redirect = magnet
                            path.append("2632:magic-magnet")
                            break
            elif dmg in (2, 3):
                for aura in auras:
                    if aura.get("kind") != "melee" or aura.get("caster") is None:
                        continue
                    magnet = aura["caster"]
                    if R.valid_attack(world, caster, magnet, spell, trace) and world.in_los(magnet, caster) \
                            and check_explicit_target(world, spell, caster, magnet, trace) == OK \
                            and check_target(world, spell, caster, magnet, False, trace) == OK:
                        redirect = magnet
                        path.append("6591:melee-intercept")
                        break
            if redirect is not None and redirect != target:
                result = redirect
    if trace is not None:
        trace.add("explicit.redirect", "Spell.cpp:691", output=result, inputs={"target": target}, notes=path)
    return result


# ---------------------------------------------------------------------------
# recipient / launch / hit
# ---------------------------------------------------------------------------
def _charmer_link(world, uid: str) -> str | None:
    """``GetCharmerGUID()`` via the shared link rule of :mod:`targeting.groups`."""
    from .groups import _link
    return _link(world, uid, "charmer")


def check_effect_target(world, spell, effect: dict, target: str, path: list[str]) -> bool:
    """Mirrors: Spell.cpp:8174 ``Spell::CheckEffectTarget(Unit const*, ...)`` with losPosition == nullptr."""
    caster = world.caster
    if effect.get("aura") in CHARM_AURAS:
        if _tfact(world, target, "vehicle_kit_controllable") or R.fact(world, target, "mounted") \
                or _charmer_link(world, target) is not None:
            path.append(f"8182:charm-ineligible(eff {effect['index']})")
            return False
        raise FailClosed("explicit: charm level check (8188) needs CalculateDamage -- out of scope")
    if has_attr(spell, "SPELL_ATTR2_IGNORE_LINE_OF_SIGHT"):
        return True
    if world.actor(caster).kind == "gameobject":
        raise FailClosed("explicit: gameobject RequireLOS (8201) not modelled")
    trig = world.spell.get("triggered_by_aura")
    if trig and not has_attr(spell, "SPELL_ATTR5_ALWAYS_LINE_OF_SIGHT") and trig.get("ignores_los"):
        path.append("8206:inherits-aura-los-ignore")
        return True
    if int(effect.get("effect", 0)) == 116:  # SPELL_EFFECT_SKIN_PLAYER_CORPSE (SharedDefines.h:1463)
        raise FailClosed("explicit: SKIN_PLAYER_CORPSE LOS branch not modelled")
    if target != caster and not spell_los(world, spell, caster, target):
        path.append(f"8249:los(eff {effect['index']})")
        return False
    return True


def spell_hit_result(world, spell, caster: str, target: str, can_reflect: bool, path: list[str]) -> str:
    """Mirrors: Object.cpp:1949 ``WorldObject::SpellHitResult`` with canImmune=false."""
    if sv(spell, "has_only_damage_effects") and _tfact(world, target, "damage_immune"):
        path.append("1957:damage-immune")
        return "SPELL_MISS_IMMUNE"
    if sv(spell, "is_positive") and not R.is_hostile(world, caster, target):
        path.append("1962:positive-non-hostile")
        return "SPELL_MISS_NONE"
    if caster == target:
        path.append("1965:self")
        return "SPELL_MISS_NONE"
    if R.is_creature(world, target) and _tfact(world, target, "evading"):
        path.append("1969:evade")
        return "SPELL_MISS_EVADE"
    if can_reflect and _tfact(world, target, "reflect_chance"):
        raise FailClosed("explicit: reflect roll not modelled (payload edge)")
    if has_attr(spell, "SPELL_ATTR3_ALWAYS_HIT"):
        path.append("1982:always-hit")
        return "SPELL_MISS_NONE"
    dmg = int(sv(spell, "dmg_class"))
    if dmg == 0:
        path.append("1990:dmg-class-none")
        return "SPELL_MISS_NONE"
    roll = world.actor(target).facts.get("hit_roll")
    if roll is None:
        path.append("1989:roll-required")
        return "ROLL:" + ("MeleeSpellHitResult" if dmg in (2, 3) else "MagicSpellHitResult")
    return str(roll)


def recipient(world, spell, target: str | None, trace: Trace | None = None) -> dict:
    """Mirrors: Spell.cpp:1819 -> 2443 ``AddUnitTarget(unit, effMask, checkIfValid=true, implicit=false)``."""
    caster = world.caster
    path: list[str] = []
    out: dict[str, Any] = {"target": target, "effect_mask": 0, "added": False, "miss": None}
    effects = [e for e in sv(spell, "effects") if e.get("explicit")]
    mask = 0
    for e in effects:
        mask |= 1 << int(e["index"])
    if target is None or not R.is_unit(world, target):
        path.append("1818:no-unit-target")
    else:
        for e in sv(spell, "effects"):
            bit = 1 << int(e["index"])
            if not e.get("effect") or (mask & bit and not check_effect_target(world, spell, e, target, path)):
                mask &= ~bit
        if not mask:
            path.append("2450:no-effects-left")
        else:
            res = check_target(world, spell, caster, target, False, trace)
            if res != OK:
                path.append(f"2454:CheckTarget->{res}(silent drop)")
                out["check_target"] = res
            else:
                for idx in _tfact(world, target, "immune_effects"):
                    mask &= ~(1 << int(idx))
                out["added"] = True
                out["effect_mask"] = mask
                if mask == 0:
                    path.append("2459:all-effects-immune(target still added with mask 0)")
                hit_caster = world.original_caster or caster
                can_reflect = bool(world.spell.get("can_reflect", False)) and not (
                    sv(spell, "is_positive") and R.is_friendly(world, caster, target, trace))
                out["miss"] = spell_hit_result(world, spell, hit_caster, target, can_reflect, path)
    if trace is not None:
        trace.add("explicit.recipient", "Spell.cpp:2443", output=out, inputs={"target": target}, notes=path)
    return out


def launch(world, spell, rec: dict, trace: Trace | None = None) -> str | None:
    """Mirrors: Spell.cpp:8526 ``Spell::PreprocessSpellLaunch`` (immunity with the complete mask)."""
    if not rec.get("added"):
        return None
    miss = rec["miss"]
    if _tfact(world, rec["target"], "immune_to_spell"):
        miss = "SPELL_MISS_IMMUNE"
    if trace is not None:
        trace.add("explicit.launch", "Spell.cpp:8533", output=miss, inputs={"target": rec["target"]})
    return miss


def hit(world, spell, rec: dict, miss: str | None, trace: Trace | None = None) -> dict:
    """Mirrors: Spell.cpp:2749 PreprocessTarget, 3109 PreprocessSpellHit, 2796 DoTargetSpellHit."""
    caster = world.caster
    out: dict[str, Any] = {"applied": False, "miss": miss, "silent_drop": None}
    path: list[str] = []
    if rec.get("added") and miss is not None:
        target = rec["target"]
        facts = world.actor(target).facts
        hit_facts = facts.get("hit", {})
        if miss == "SPELL_MISS_NONE":
            delayed = bool(sv(spell, "has_hit_delay")) and target != caster
            evading = hit_facts.get("evading_at_hit", _tfact(world, target, "evading"))
            if R.is_creature(world, target) and evading:
                out["miss"] = "SPELL_MISS_EVADE"
                path.append("3116:evade-at-hit")
            elif delayed and hit_facts.get("immune_at_hit", _tfact(world, target, "immune_to_spell")):
                out["miss"] = "SPELL_MISS_IMMUNE"
                path.append("3120:delayed-immunity")
            elif caster != target:
                non_att = hit_facts.get("non_attackable_at_hit", R.has_unit_flag(world, target, "NON_ATTACKABLE"))
                if delayed and non_att and R.charmer_or_owner(world, target) != caster:
                    out["miss"] = "SPELL_MISS_EVADE"
                    path.append("3138:delayed-non-attackable")
                elif R.valid_attack(world, caster, target, spell, trace):
                    path.append("3141:attack-branch(no rejection; HostileActionReceived interrupt)")
                elif R.is_friendly(world, caster, target, trace):
                    if delayed and R.is_player(world, target) and not sv(spell, "is_positive") \
                            and not R.valid_assist(world, caster, target, spell, trace):
                        out["miss"] = "SPELL_MISS_EVADE"
                        path.append("3146:delayed-negative-on-friendly-player")
                    else:
                        path.append("3143:assist-branch")
                else:
                    path.append("3141-3143:neither-branch")
            if out["miss"] == "SPELL_MISS_NONE" and hit_facts.get("dr_immune"):
                out["miss"] = "SPELL_MISS_IMMUNE"
                path.append("3218:diminished-to-zero")
        alive_now = hit_facts.get("alive_at_hit", world.actor(target).need("alive"))
        if alive_now != world.actor(target).need("alive") and not has_attr(spell, "SPELL_ATTR9_FORCE_CORPSE_TARGET"):
            out["silent_drop"] = "2811:alive-state-changed"
        elif not has_attr(spell, "SPELL_ATTR8_IGNORE_SANCTUARY") and bool(sv(spell, "has_hit_delay")) \
                and not sv(spell, "is_positive") and hit_facts.get("sanctuary_during_flight", False):
            out["silent_drop"] = "2814:sanctuary-during-flight"
        out["applied"] = out["miss"] in ("SPELL_MISS_NONE",) and out["silent_drop"] is None and rec["effect_mask"] != 0
    if trace is not None:
        trace.add("explicit.hit", "Spell.cpp:3109", output=out, notes=path)
    return out


def validate(world, spell, trace: Trace | None = None) -> dict:
    """All stages for the fixture's explicit target.  Returns per-stage verdicts (stages never merged)."""
    trace = trace if trace is not None else Trace()
    triggered = set(world.spell.get("triggered_flags", ()))
    unit = init_explicit_targets(world, spell, trace)
    verdict: dict[str, Any] = {"init": unit}
    prep = cast_eligibility(world, spell, unit, True, trace)
    effective = OK if ("IGNORE_TARGET_CHECK" in triggered and prep == "SPELL_FAILED_BAD_TARGETS") else prep
    verdict["cast_prepare"] = {"raw": prep, "result": effective}
    if effective != prep:
        # CheckCast returned at its first failure: every later check (CheckTarget, LOS, range, power,
        # caster auras, script hooks) was never evaluated for this cast.
        verdict["cast_prepare"]["unevaluated_after_mapping"] = True
    if effective != OK:
        return verdict
    skip = int(sv(spell, "cast_time_ms")) == 0 or "CAST_DIRECTLY" in triggered
    if skip:
        verdict["cast_complete"] = {"result": "skipped(_cast skipCheck=true)"}
    else:
        later = world.raw.get("at_cast_complete")
        if later:
            raise FailClosed("explicit: state changes between prepare and cast completion must be a separate fixture")
        verdict["cast_complete"] = {"result": cast_eligibility(world, spell, unit, False, trace)}
        if verdict["cast_complete"]["result"] != OK:
            return verdict
    target = select_explicit_targets(world, spell, unit, trace)
    verdict["redirect"] = target
    rec = recipient(world, spell, target, trace)
    verdict["recipient"] = rec
    miss = launch(world, spell, rec, trace)
    verdict["launch"] = miss
    verdict["hit"] = hit(world, spell, rec, miss, trace)
    return verdict


__all__ = ["CHECKCAST_BOUNDARY", "TARGET_FLAG", "cast_eligibility", "check_effect_target", "check_explicit_target",
           "check_range", "check_target", "check_target_creature_type", "hit", "init_explicit_targets", "launch",
           "recipient", "select_explicit_targets", "spell_hit_result", "validate"]


# ===========================================================================
# census: check matrix over current-player spells (BRIEF §5, §10)
# ===========================================================================
CONDITION_SOURCE_TYPE_SPELL = 17          # ConditionMgr.h:173
CU = {"REQ_TARGET_FACING_CASTER": 0x10000, "REQ_CASTER_BEHIND_TARGET": 0x20000,
      "ALLOW_INFLIGHT_TARGET": 0x40000, "CAN_TARGET_ANY_PRIVATE_OBJECT": 0x2000000}   # SpellInfo.h:160-169

S_CAST, S_RECIP, S_APPLY, S_INIT, S_REDIRECT = ("cast-eligibility", "recipient-selection", "effect-application",
                                                "init", "redirect")

#: (id, category, predicate, consumer, stage, result, scope, witness selector)
#: scope: explicit = explicit target only; implicit = implicit recipients only; both.
#: witness selector: ("attr", NAME) | ("cu", NAME) | ("mask", FLAGS) | ("restr", KEY) | ("range", KIND)
#: | ("facing",) | ("mechanic", N) | ("dead", bool) | ("cond",) | ("dmg", CLASSES) | ("all",) | ("none",)
MATRIX = (
    ("M01", "existence/type", "object target kind not in ExplicitTargetMask -> removed", "Spell.cpp:634", S_INIT, "silent (target dropped)", "explicit", ("all",)),
    ("M02", "existence/type", "no object target but required unit/GO/corpse flag", "SpellInfo.cpp:2535", S_CAST, "SPELL_FAILED_BAD_TARGETS", "explicit", ("mask", UNIT_MASK)),
    ("M03", "existence/type", "explicit object vanished before cast completion", "Spell.cpp:3704", S_CAST, "cancel() (SPELL_FAILED_INTERRUPTED)", "explicit", ("all",)),
    ("M04", "existence/type", "target unit gone at hit", "Spell.cpp:2752", S_APPLY, "silent", "both", ("all",)),
    ("M05", "alive/dead", "IsValidAttack/AssistTarget: dead unless IsAllowingDeadTarget", "Object.cpp:2369", S_CAST, "SPELL_FAILED_BAD_TARGETS (via CheckExplicitTarget)", "explicit", ("dead", False)),
    ("M06", "alive/dead", "CheckTarget: dead unless IsAllowingDeadTarget", "SpellInfo.cpp:2452", S_CAST, "SPELL_FAILED_TARGETS_DEAD", "both", ("dead", False)),
    ("M06r", "alive/dead", "CheckTarget re-run in AddUnitTarget (checkIfValid)", "Spell.cpp:2454", S_RECIP, "silent drop", "both", ("dead", False)),
    ("M07", "alive/dead", "alive state changed between selection and hit (unless ATTR9_FORCE_CORPSE_TARGET)", "Spell.cpp:2811", S_APPLY, "silent drop (no miss info)", "both", ("all",)),
    ("M07a", "alive/dead", "IsAllowingDeadTarget (ATTR2_ALLOW_DEAD_TARGET | corpse/dead Targets | corpse selector)", "SpellInfo.cpp:1838", S_CAST, "allows dead", "both", ("dead", True)),
    ("M08", "alive/dead", "corpse object-type selector requires !IsAlive", "Spell.cpp:9384", S_RECIP, "silent", "implicit", ("none",)),
    ("M09", "relation", "UNIT_ENEMY required: IsValidAttackTarget(originalCaster)", "SpellInfo.cpp:2546", S_CAST, "SPELL_FAILED_BAD_TARGETS", "explicit", ("req", TF["UNIT_ENEMY"])),
    ("M10", "relation", "UNIT_ALLY required: IsValidAssistTarget(originalCaster)", "SpellInfo.cpp:2549", S_CAST, "SPELL_FAILED_BAD_TARGETS", "explicit", ("req", TF["UNIT_ALLY"])),
    ("M11", "party/raid", "UNIT_PARTY/RAID required: IsInPartyWith/IsInRaidWith + assist", "SpellInfo.cpp:2550", S_CAST, "SPELL_FAILED_BAD_TARGETS", "explicit", ("req", TF["UNIT_PARTY"] | TF["UNIT_RAID"])),
    ("M12", "owner/pet", "UNIT_MINIPET / UNIT_PASSENGER required", "SpellInfo.cpp:2554", S_CAST, "SPELL_FAILED_BAD_TARGETS", "explicit", ("req", TF["UNIT_MINIPET"] | TF["UNIT_PASSENGER"])),
    ("M12u", "relation", "explicit unit selectors are NOT re-checked for relation at recipient stage", "Spell.cpp:1819", S_RECIP, "no check", "explicit", ("mask", UNIT_MASK)),
    ("M13", "relation", "ENEMY/ALLY check types: IsValidAttack/AssistTarget(m_caster)", "Spell.cpp:9331", S_RECIP, "silent", "implicit", ("none",)),
    ("M14", "relation", "redirect only for UNIT_ENEMY, or UNIT when !IsFriendlyTo", "Spell.cpp:697", S_REDIRECT, "target replaced", "explicit", ("dmg", (1, 2, 3))),
    ("M14a", "relation", "magic redirect skipped for IS_ABILITY / NO_REDIRECTION / NO_IMMUNITIES", "Object.cpp:2605", S_REDIRECT, "no redirect", "explicit", ("attr", "SPELL_ATTR1_NO_REDIRECTION")),
    ("M15", "relation", "positive spell on non-hostile never misses", "Object.cpp:1962", S_RECIP, "SPELL_MISS_NONE", "both", ("all",)),
    ("M16", "relation", "hit: delayed negative spell on friendly player not assistable", "Spell.cpp:3146", S_APPLY, "SPELL_MISS_EVADE", "both", ("all",)),
    ("M17", "relation", "range column chosen by !IsHostileTo (neutral uses friendly column)", "Object.cpp:1674", S_CAST, "SPELL_FAILED_OUT_OF_RANGE", "explicit", ("range", "columns-differ")),
    ("M18", "self", "SPELL_ATTR1_EXCLUDE_CASTER", "SpellInfo.cpp:2331", S_CAST, "SPELL_FAILED_BAD_TARGETS", "both", ("attr", "SPELL_ATTR1_EXCLUDE_CASTER")),
    ("M19", "self", "self fallback when mask lacks ENEMY/DEAD/MINIPET/PASSENGER", "Spell.cpp:658", S_INIT, "target := caster", "explicit", ("selffallback",)),
    ("M20", "self", "passive spell cast on self skips CheckExplicitTarget", "Spell.cpp:5940", S_CAST, "skip", "explicit", ("attr", "SPELL_ATTR0_PASSIVE")),
    ("M21", "self", "range/LOS/facing skipped for target == caster", "Spell.cpp:7291", S_CAST, "skip", "explicit", ("mask", UNIT_MASK)),
    ("M22", "owner/pet", "ATTR8_ONLY_TARGET_OWN_SUMMONS", "SpellInfo.cpp:2411", S_CAST, "SPELL_FAILED_BAD_TARGETS", "both", ("attr", "SPELL_ATTR8_ONLY_TARGET_OWN_SUMMONS")),
    ("M23", "owner/pet", "ATTR8_ONLY_TARGET_IF_SAME_CREATOR", "SpellInfo.cpp:2346", S_CAST, "SPELL_FAILED_BAD_TARGETS", "both", ("attr", "SPELL_ATTR8_ONLY_TARGET_IF_SAME_CREATOR")),
    ("M24", "owner/pet", "ATTR5_NOT_ON_PLAYER_CONTROLLED_NPC", "SpellInfo.cpp:2446", S_CAST, "SPELL_FAILED_TARGET_IS_PLAYER_CONTROLLED", "both", ("attr", "SPELL_ATTR5_NOT_ON_PLAYER_CONTROLLED_NPC")),
    ("M25", "pvp", "sanctuary block gated on ATTR8_IGNORE_SANCTUARY (inverted, defect TG-B-D1)", "Object.cpp:2469", S_CAST, "SPELL_FAILED_BAD_TARGETS", "both", ("attr", "SPELL_ATTR8_IGNORE_SANCTUARY")),
    ("M25a", "pvp", "ATTR5_IGNORE_AREA_EFFECT_PVP_CHECK makes any PvP target attackable", "Object.cpp:2475", S_CAST, "valid attack", "both", ("attr", "SPELL_ATTR5_IGNORE_AREA_EFFECT_PVP_CHECK")),
    ("M25b", "pvp", "sanctuary entered during flight drops negative delayed spell (unless ATTR8_IGNORE_SANCTUARY)", "Spell.cpp:2814", S_APPLY, "silent drop", "both", ("all",)),
    ("M26", "visibility", "CanSeeOrDetect (implicit flag = !explicit; ATTR6_IGNORE_PHASE_SHIFT)", "SpellInfo.cpp:2341", S_CAST, "SPELL_FAILED_BAD_TARGETS", "both", ("attr", "SPELL_ATTR6_IGNORE_PHASE_SHIFT")),
    ("M26a", "visibility", "GM / invisible player", "SpellInfo.cpp:2472", S_CAST, "SPELL_FAILED_BM_OR_INVISGOD", "both", ("all",)),
    ("M27", "los", "CheckCast LOS (target as ray source; dynobj source for aura-triggered)", "Spell.cpp:6001", S_CAST, "SPELL_FAILED_LINE_OF_SIGHT", "explicit", ("nolos",)),
    ("M27a", "los", "ATTR2_IGNORE_LINE_OF_SIGHT", "Spell.cpp:9258", S_CAST, "skip", "both", ("attr", "SPELL_ATTR2_IGNORE_LINE_OF_SIGHT")),
    ("M28", "los", "CheckEffectTarget LOS per effect (losPosition null for explicit)", "Spell.cpp:8249", S_RECIP, "effect bit cleared (silent)", "both", ("nolos",)),
    ("M28a", "los", "ATTR5_ALWAYS_LINE_OF_SIGHT blocks inheriting the triggering aura's LOS ignore", "Spell.cpp:8206", S_RECIP, "check LOS", "both", ("attr", "SPELL_ATTR5_ALWAYS_LINE_OF_SIGHT")),
    ("M29", "range", "max/min range (squared float), strict at prepare, +min(3,10%) at completion, instant skip", "Spell.cpp:7293", S_CAST, "SPELL_FAILED_OUT_OF_RANGE", "explicit", ("range", "any")),
    ("M29m", "range", "SPELL_RANGE_MELEE: max = GetMeleeRange only (RangeMax ignored)", "Spell.cpp:7344", S_CAST, "SPELL_FAILED_OUT_OF_RANGE", "explicit", ("range", "melee")),
    ("M29r", "range", "SPELL_RANGE_RANGED: min += melee range, min not widened by reach", "Spell.cpp:7353", S_CAST, "SPELL_FAILED_OUT_OF_RANGE", "explicit", ("range", "ranged")),
    ("M29n", "range", "no SpellRange row: max 0 -> any other unit out of range", "Spell.cpp:7341", S_CAST, "SPELL_FAILED_OUT_OF_RANGE", "explicit", ("range", "none")),
    ("M29z", "range", "SpellRange row with max 0 (e.g. 1 'Self Only'): max = both combat reaches only", "Spell.cpp:7365", S_CAST, "SPELL_FAILED_OUT_OF_RANGE", "explicit", ("range", "zero")),
    ("M30", "facing", "FacingCasterFlags INFRONT (players; boundary radius exemption)", "Spell.cpp:7300", S_CAST, "SPELL_FAILED_UNIT_NOT_INFRONT", "explicit", ("facing",)),
    ("M31", "position", "CU_REQ_CASTER_BEHIND_TARGET", "Spell.cpp:5986", S_CAST, "SPELL_FAILED_NOT_BEHIND", "explicit", ("cu", "REQ_CASTER_BEHIND_TARGET")),
    ("M31a", "position", "CU_REQ_TARGET_FACING_CASTER", "Spell.cpp:5990", S_CAST, "SPELL_FAILED_NOT_INFRONT", "explicit", ("cu", "REQ_TARGET_FACING_CASTER")),
    ("M32", "creature/player", "ATTR3_ONLY_ON_PLAYER", "SpellInfo.cpp:2443", S_CAST, "SPELL_FAILED_TARGET_NOT_PLAYER", "both", ("attr", "SPELL_ATTR3_ONLY_ON_PLAYER")),
    ("M32a", "creature/player", "ATTR5_NOT_ON_PLAYER", "SpellInfo.cpp:2449", S_CAST, "SPELL_FAILED_TARGET_IS_PLAYER", "both", ("attr", "SPELL_ATTR5_NOT_ON_PLAYER")),
    ("M33", "aura", "TargetAuraState / ExcludeTargetAuraState (skipped for vehicle caster or caster's charmer/owner)", "SpellInfo.cpp:2496", S_CAST, "SPELL_FAILED_TARGET_AURASTATE", "both", ("restr", ("TargetAuraState", "ExcludeTargetAuraState"))),
    ("M33a", "aura", "TargetAuraSpell / ExcludeTargetAuraSpell", "SpellInfo.cpp:2504", S_CAST, "SPELL_FAILED_TARGET_AURASTATE", "both", ("restr", ("TargetAuraSpell", "ExcludeTargetAuraSpell"))),
    ("M33b", "aura", "TargetAuraType / ExcludeTargetAuraType", "SpellInfo.cpp:2510", S_CAST, "SPELL_FAILED_TARGET_AURASTATE", "both", ("restr", ("TargetAuraType", "ExcludeTargetAuraType"))),
    ("M33c", "aura", "ATTR3_ONLY_ON_GHOSTS (mismatch either way)", "SpellInfo.cpp:2368", S_CAST, "SPELL_FAILED_TARGET_NOT_GHOST / BAD_TARGETS", "both", ("attr", "SPELL_ATTR3_ONLY_ON_GHOSTS")),
    ("M33d", "aura", "ATTR6_DO_NOT_CHAIN_TO_CROWD_CONTROLLED_TARGETS (implicit only)", "SpellInfo.cpp:2456", S_RECIP, "SPELL_FAILED_BAD_TARGETS (silent)", "implicit", ("attr", "SPELL_ATTR6_DO_NOT_CHAIN_TO_CROWD_CONTROLLED_TARGETS")),
    ("M33e", "aura", "ATTR1_ONLY_PEACEFUL_TARGETS", "SpellInfo.cpp:2364", S_CAST, "SPELL_FAILED_TARGET_AFFECTING_COMBAT", "both", ("attr", "SPELL_ATTR1_ONLY_PEACEFUL_TARGETS")),
    ("M33f", "aura", "ATTR3_NOT_ON_AOE_IMMUNE", "SpellInfo.cpp:2415", S_CAST, "SPELL_FAILED_BAD_TARGETS", "both", ("attr", "SPELL_ATTR3_NOT_ON_AOE_IMMUNE")),
    ("M33g", "aura", "ATTR9_TARGET_MUST_BE_GROUNDED", "SpellInfo.cpp:2419", S_CAST, "SPELL_FAILED_TARGET_NOT_GROUNDED", "both", ("attr", "SPELL_ATTR9_TARGET_MUST_BE_GROUNDED")),
    ("M33h", "aura", "SPELL_AURA_INTERFERE_*_TARGETING (non-melee, not IGNORE_LOS)", "Spell.cpp:5961", S_CAST, "SPELL_FAILED_VISION_OBSCURED", "explicit", ("dmgnot", 2)),
    ("M33i", "aura", "prevent-resurrection aura vs resurrect effects", "SpellInfo.cpp:2516", S_CAST, "SPELL_FAILED_TARGET_CANNOT_BE_RESURRECTED", "both", ("resurrect",)),
    ("M34", "mechanic", "MECHANIC_DISARM needs a target weapon (player casters)", "SpellInfo.cpp:2397", S_CAST, "SPELL_FAILED_TARGET_NO_WEAPONS", "both", ("mechanic", MECHANIC_DISARM)),
    ("M34a", "tap", "ATTR2_CANNOT_CAST_ON_TAPPED (player casters)", "SpellInfo.cpp:2381", S_CAST, "SPELL_FAILED_CANT_CAST_ON_TAPPED", "both", ("attr", "SPELL_ATTR2_CANNOT_CAST_ON_TAPPED")),
    ("M35", "immunity", "per-effect immunity clears effect bits (target kept even at mask 0)", "Spell.cpp:2459", S_RECIP, "effect bit cleared", "both", ("all",)),
    ("M35a", "immunity", "damage-only spell vs damage immunity", "Object.cpp:1957", S_RECIP, "SPELL_MISS_IMMUNE", "both", ("dmgonly",)),
    ("M35b", "immunity", "full-mask immunity at launch", "Spell.cpp:8533", S_APPLY, "SPELL_MISS_IMMUNE", "both", ("all",)),
    ("M35c", "immunity", "delayed spell immunity re-check at hit", "Spell.cpp:3120", S_APPLY, "SPELL_MISS_IMMUNE", "both", ("delay",)),
    ("M35d", "immunity", "ATTR2_FAIL_ON_ALL_TARGETS_IMMUNE evaluated before launch immunity (sees damage immunity only; TG-B-D2)", "Spell.cpp:846", S_RECIP, "SPELL_FAILED_IMMUNE", "both", ("attr", "SPELL_ATTR2_FAIL_ON_ALL_TARGETS_IMMUNE")),
    ("M36", "evade", "creature evading at selection", "Object.cpp:1969", S_RECIP, "SPELL_MISS_EVADE", "both", ("all",)),
    ("M36a", "evade", "creature evading at hit / delayed NON_ATTACKABLE not owned by caster", "Spell.cpp:3116", S_APPLY, "SPELL_MISS_EVADE", "both", ("all",)),
    ("M37", "creature-type", "TargetCreatureType mask (magnet / typeless / IGNORE aura bypass; warlock cat 1179 hack)", "SpellInfo.cpp:2463", S_CAST, "SPELL_FAILED_BAD_TARGETS / TARGET_IS_PLAYER", "both", ("creaturetype",)),
    ("M38", "class/spec", "RAID_CLASS check type compares class with referer (no class/spec check exists for explicit targets)", "Spell.cpp:9355", S_RECIP, "silent", "implicit", ("none",)),
    ("M39", "flags", "ATTR6_CAN_TARGET_UNTARGETABLE bypasses UNIT_FLAG_NON_ATTACKABLE_2", "Object.cpp:2373", S_CAST, "valid", "both", ("attr", "SPELL_ATTR6_CAN_TARGET_UNTARGETABLE")),
    ("M39a", "flags", "ATTR8_CAN_ATTACK_IMMUNE_PC / ATTR6_CAN_ASSIST_IMMUNE_PC", "Object.cpp:2403", S_CAST, "valid", "both", ("attr", "SPELL_ATTR8_CAN_ATTACK_IMMUNE_PC")),
    ("M39b", "flags", "ATTR11_CAN_ASSIST_UNINTERACTIBLE", "Object.cpp:2540", S_CAST, "valid", "both", ("attr", "SPELL_ATTR11_CAN_ASSIST_UNINTERACTIBLE")),
    ("M40", "triggered", "TRIGGERED_IGNORE_TARGET_CHECK maps only the first-failure BAD_TARGETS (later checks never run)", "Spell.cpp:3495", S_CAST, "SPELL_CAST_OK", "explicit", ("none",)),
    ("M41", "conditions", "CONDITION_SOURCE_TYPE_SPELL with explicit object", "Spell.cpp:5919", S_CAST, "SPELL_FAILED_BAD_TARGETS / CASTER_AURASTATE", "explicit", ("cond",)),
    ("M42", "required", "ATTR13_DO_NOT_FAIL_IF_NO_TARGET / effect DontFailSpellOnTargetingFailure remove bits from RequiredExplicitTargetMask", "SpellInfo.cpp:4600", S_CAST, "no BAD_TARGETS", "explicit", ("optional",)),
)

RESURRECT_EFFECTS = {18, 94}    # SharedDefines.h:1365/1441 SPELL_EFFECT_RESURRECT / SELF_RESURRECT


def _spell_facts(ctx, spell: int) -> dict | None:
    """DB2 + overlay facts the matrix reads for one current-player spell (None if no IsEffect)."""
    from procs.enums import attr
    from .attributes import corrected_effects, corrections
    from .composition import EffectSlots, explicit_target_mask
    effs = ctx.data.effects(spell)
    if not any(e.is_effect for e in effs):
        return None
    info = ctx.catalog.get(spell)
    if info is None:
        return None
    corrected, _ = corrected_effects(spell, effs)
    misc = ctx.data.misc(spell) or {}
    rng = ctx.data.ranges.get(int(misc.get("RangeIndex", 0) or 0))
    restr = ctx.data.restrictions(spell) or {}
    slots = [EffectSlots(c["index"], c["effect"], c["a"], c["b"], c["attributes"]) for c in sorted(corrected, key=lambda c: c["index"])]
    mx = (float(rng["RangeMax_0"]), float(rng["RangeMax_1"])) if rng else (0.0, 0.0)
    explicit, required = explicit_target_mask(slots, max_range_negative=mx[0], max_range_positive=mx[1],
                                              targets=int(restr.get("Targets", 0) or 0),
                                              attributes13=info.attributes[13])
    from .selectors import info as sel
    allowing_dead = info.has_attr(attr("SPELL_ATTR2_ALLOW_DEAD_TARGET")) or \
        int(restr.get("Targets", 0) or 0) & (TF["CORPSE_ALLY"] | TF["CORPSE_ENEMY"] | TF["UNIT_DEAD"]) or \
        any(c["effect"] and "CORPSE" in (sel(c["a"]).object, sel(c["b"]).object) for c in corrected)
    return {
        "info": info, "effects": effs, "corrected": corrected, "explicit": explicit, "required": required,
        "range": rng, "restr": ctx.data.aura_restrictions(spell) or {}, "targets_restr": restr,
        "facing": int((ctx.data.casting_requirements(spell) or {}).get("FacingCasterFlags", 0) or 0),
        "allowing_dead": bool(allowing_dead),
        "attr_corrections": sorted({w["member"] for w in corrections(spell) if w["member"].startswith("Attributes")
                                    or w["member"] in ("RangeEntry", "Targets", "TargetCreatureType",
                                                       "TargetAuraSpell", "ExcludeTargetAuraSpell")}),
    }


def _witness(ctx, f: dict, sel_: tuple, conditions: dict) -> bool:
    from procs.enums import attr
    info = f["info"]
    kind = sel_[0]
    if kind == "all":
        return True
    if kind == "none":
        return False
    if kind == "attr":
        return info.has_attr(attr(sel_[1]))
    if kind == "cu":
        return bool(info.attributes_cu & CU[sel_[1]])
    if kind == "mask":
        return bool(f["explicit"] & sel_[1])
    if kind == "req":
        return bool(f["required"] & sel_[1])
    if kind == "restr":
        return any(int(f["restr"].get(k, 0) or 0) for k in sel_[1])
    if kind == "range":
        r = f["range"]
        if sel_[1] == "none":
            return r is None
        if r is None:
            return False
        if sel_[1] == "any":
            return True
        if sel_[1] == "zero":
            return not int(r["Flags"]) & SPELL_RANGE_MELEE and float(r["RangeMax_0"]) == 0 and float(r["RangeMax_1"]) == 0
        if sel_[1] == "melee":
            return bool(int(r["Flags"]) & SPELL_RANGE_MELEE)
        if sel_[1] == "ranged":
            return bool(int(r["Flags"]) & SPELL_RANGE_RANGED) and not int(r["Flags"]) & SPELL_RANGE_MELEE
        if sel_[1] == "columns-differ":
            return not int(r["Flags"]) & SPELL_RANGE_MELEE and (
                float(r["RangeMax_0"]) != float(r["RangeMax_1"]) or float(r["RangeMin_0"]) != float(r["RangeMin_1"]))
    if kind == "facing":
        return bool(f["facing"] & SPELL_FACING_FLAG_INFRONT)
    if kind == "mechanic":
        return info.mechanic == sel_[1]
    if kind == "dead":
        return f["allowing_dead"] is sel_[1]
    if kind == "dmg":
        return info.dmg_class in sel_[1]
    if kind == "dmgnot":
        return info.dmg_class != sel_[1] and not info.has_attr(attr("SPELL_ATTR2_IGNORE_LINE_OF_SIGHT"))
    if kind == "nolos":
        return not info.has_attr(attr("SPELL_ATTR2_IGNORE_LINE_OF_SIGHT"))
    if kind == "resurrect":
        return any(e.effect in RESURRECT_EFFECTS for e in f["effects"])
    if kind == "dmgonly":
        live = [e for e in f["effects"] if e.is_effect]
        return bool(live) and all(e.effect in _DAMAGE_EFFECTS for e in live)
    if kind == "delay":   # SpellInfo.cpp:1926 HasHitDelay
        misc = ctx.data.misc(info.id) or {}
        return float(misc.get("Speed", 0) or 0) > 0 or float(misc.get("LaunchDelay", 0) or 0) > 0
    if kind == "creaturetype":
        return bool(int(f["targets_restr"].get("TargetCreatureType", 0) or 0)) or (info.family == SPELLFAMILY_WARLOCK and info.category == 1179)
    if kind == "cond":
        return info.id in conditions
    if kind == "selffallback":
        return bool(f["explicit"] & UNIT_MASK) and not f["explicit"] & (
            TF["UNIT_ENEMY"] | TF["UNIT_DEAD"] | TF["UNIT_MINIPET"] | TF["UNIT_PASSENGER"])
    if kind == "optional":
        return bool((f["explicit"] & ~f["required"]) & UNIT_MASK)
    raise FailClosed(f"explicit census: unknown witness selector {sel_}")


# SpellInfo.cpp:1579-1600 HasOnlyDamageEffects case list (SharedDefines.h ids verified at the pin)
_DAMAGE_EFFECTS = {2, 7, 9, 17, 31, 58, 121, 165}


def census(ctx) -> dict:
    """Check matrix with current-player witness counts and the BRIEF §10 effect_classes map."""
    from collections import Counter

    from procs.enums import attr
    reach = sorted(ctx.scope.reach)
    conditions = ctx.bundle.world.conditions_by_source().get(CONDITION_SOURCE_TYPE_SPELL, {})
    facts: dict[int, dict] = {}
    for spell in reach:
        f = _spell_facts(ctx, spell)
        if f is not None and f["explicit"] & UNIT_MASK:
            facts[spell] = f
    rows = []
    for mid, cat, pred, consumer, stage, result, scope, sel_ in MATRIX:
        hits = [s for s in facts if _witness(ctx, facts[s], sel_, conditions)]
        rows.append({"id": mid, "category": cat, "predicate": pred, "consumer": consumer, "stage": stage,
                     "result": result, "scope": scope, "witness_selector": list(map(str, sel_)),
                     "witness_count": len(hits),
                     "witness_count_build_skew": sum(1 for s in hits if ctx.is_skew(s)),
                     "witnesses": [{"spell": s, "name": ctx.name(s)} for s in sorted(hits)[:6]],
                     "evidence": "trinity-consumer" if sel_[0] in ("all", "none") else "db2-fact+trinity-consumer"})
    mask_counts = Counter()
    for f in facts.values():
        for name, bit in TF.items():
            if f["explicit"] & bit & UNIT_MASK:
                mask_counts[name] += 1
    range_split = Counter()
    for f in facts.values():
        r = f["range"]
        key = "none" if r is None else ("melee" if int(r["Flags"]) & 1 else ("ranged" if int(r["Flags"]) & 2 else "default"))
        range_split[key] += 1
    effect_classes = {}
    for spell, f in facts.items():
        blocked = []
        tags = {"explicit-unit"}
        if f["attr_corrections"]:
            tags.add("trinity-correction")
        info = f["info"]
        for mid, _cat, _p, _c, _st, _r, _sc, sel_ in MATRIX:
            if sel_[0] in ("all", "none", "mask"):
                continue
            if _witness(ctx, f, sel_, conditions):
                tags.add(f"restriction-{mid}")
        cls = "understood"
        unknowns = []
        if spell in conditions:
            cls = "fixture-dependent"
            tags.add("condition")
            unknowns.append("TG-B-04")
        if f["required"] & (TF["UNIT_PASSENGER"] | TF["UNIT_MINIPET"]):
            blocked.append("vehicle/minipet")
        if info.has_attr(attr("SPELL_ATTR8_ENFORCE_IN_COMBAT_RESSURECTION_LIMIT")):
            tags.add("combat-res-limit")
            unknowns.append("TG-B-05")
            cls = "fixture-dependent" if cls == "understood" else cls
        if f["range"] is not None and _witness(ctx, f, ("range", "columns-differ"), conditions):
            tags.add("range-column-relation")
        if info.has_attr(attr("SPELL_ATTR8_IGNORE_SANCTUARY")):
            tags.add("defect-TG-B-D1")
            if cls == "understood":
                cls = "understood-with-defect"
        if info.has_attr(attr("SPELL_ATTR2_FAIL_ON_ALL_TARGETS_IMMUNE")):
            tags.add("defect-TG-B-D2")
            if cls == "understood":
                cls = "understood-with-defect"
        if blocked:
            cls = "blocked"
            tags.update(blocked)
            unknowns.append("TG-B-06")
        for c in f["corrected"]:
            if not c["effect"] and not any(e.index == c["index"] and e.is_effect for e in f["effects"]):
                continue
            raw = next((e for e in f["effects"] if e.index == c["index"]), None)
            if raw is None or not raw.is_effect:
                continue
            from .selectors import info as sel
            etags = set(tags)
            reads = any(sel(t).reference == "TARGET" and sel(t).object in ("UNIT", "UNIT_AND_DEST")
                        and sel(t).category == "DEFAULT" for t in (c["a"], c["b"]) if t)
            etags.add("explicit-recipient" if reads else "explicit-gate-only")
            effect_classes[f"{spell}:{c['index']}"] = {
                "class": cls, "tags": sorted(etags), "unknowns": sorted(set(unknowns)),
                "build_skew": bool(ctx.is_skew(spell))}
    return {
        "scope": {"reach_spells": len(reach), "explicit_unit_spells": len(facts),
                  "explicit_unit_spells_build_skew": sum(1 for s in facts if ctx.is_skew(s)),
                  "effects_classified": len(effect_classes),
                  "definition": "IsEffect spells in ctx.scope.reach whose ExplicitTargetMask "
                                "(composition.explicit_target_mask over load-corrected effects) has a TARGET_FLAG_UNIT_MASK bit"},
        "explicit_mask_flag_counts": dict(sorted(mask_counts.items())),
        "range_kind_counts": dict(sorted(range_split.items())),
        "matrix": rows,
        "effect_classes": dict(sorted(effect_classes.items(), key=lambda kv: tuple(map(int, kv[0].split(":"))))),
        "class_counts": dict(sorted(Counter(v["class"] for v in effect_classes.values()).items())),
    }


def view_from_data(ctx, spell: int, *, positive: bool, cast_time_ms: int) -> dict:
    """Track B contract view of a real spell from DB2 + overlay (positivity and cast time are stated by the caller:
    load-time positivity is not ported (TG-B-01); cast time needs haste/SpellCastTimes)."""
    from procs.enums import attr_name
    from .selectors import info as sel
    f = _spell_facts(ctx, spell)
    if f is None:
        raise FailClosed(f"explicit: spell {spell} has no IsEffect rows")
    info = f["info"]
    names = [attr_name((w, 1 << b)) for w in range(17) for b in range(32) if (info.attributes[w] >> b) & 1]
    names += [f"SPELL_ATTR0_CU_{k}" for k, bit in CU.items() if info.attributes_cu & bit]
    r = f["range"]
    misc = ctx.data.misc(spell) or {}
    restr = f["restr"]
    corrected = {c["index"]: c for c in f["corrected"]}
    effects = []
    for e in f["effects"]:
        c = corrected.get(e.index, {"a": e.target_a, "b": e.target_b, "effect": e.effect})
        reads = any(sel(t).reference == "TARGET" and sel(t).object in ("UNIT", "UNIT_AND_DEST")
                    and sel(t).category == "DEFAULT" for t in (c["a"], c["b"]) if t)
        effects.append({"index": e.index, "effect": c["effect"], "aura": e.aura, "explicit": bool(reads and c["effect"])})
    live = [e for e in f["effects"] if e.is_effect]
    return {
        "id": spell, "attributes": names, "is_positive": positive,
        "is_affecting_area": any(e.is_effect and (sel(corrected[e.index]["a"]).is_area or sel(corrected[e.index]["b"]).is_area
                                                  or e.effect == 27 or e.effect in (35, 65, 119, 128, 129, 143, 202, 271))
                                 for e in f["effects"]),
        "is_allowing_dead_target": f["allowing_dead"], "is_passive": "SPELL_ATTR0_PASSIVE" in names,
        "explicit_target_mask": f["explicit"], "required_explicit_target_mask": f["required"],
        "dmg_class": info.dmg_class, "target_creature_type": int(f["targets_restr"].get("TargetCreatureType", 0) or 0),
        "family": info.family, "category": info.category, "mechanic": info.mechanic,
        "aura_restrictions": {
            "target_aura_state": int(restr.get("TargetAuraState", 0) or 0),
            "exclude_target_aura_state": int(restr.get("ExcludeTargetAuraState", 0) or 0),
            "target_aura_spell": int(restr.get("TargetAuraSpell", 0) or 0),
            "exclude_target_aura_spell": int(restr.get("ExcludeTargetAuraSpell", 0) or 0),
            "target_aura_type": int(restr.get("TargetAuraType", 0) or 0),
            "exclude_target_aura_type": int(restr.get("ExcludeTargetAuraType", 0) or 0)},
        "range": None if r is None else {"flags": int(r["Flags"]), "min": [float(r["RangeMin_0"]), float(r["RangeMin_1"])],
                                         "max": [float(r["RangeMax_0"]), float(r["RangeMax_1"])]},
        "facing_caster_flags": f["facing"], "effects": effects, "cast_time_ms": cast_time_ms,
        "has_hit_delay": float(misc.get("Speed", 0) or 0) > 0 or float(misc.get("LaunchDelay", 0) or 0) > 0,
        "is_next_melee_swing": False, "resurrect": any(e.effect in RESURRECT_EFFECTS for e in live),
        "has_only_damage_effects": bool(live) and all(e.effect in _DAMAGE_EFFECTS for e in live),
        "los_disabled": _los_disabled_from_world(spell),
        "notes": ["positivity and cast time stated by the witness author",
                  "los_disabled: world-DB `disables` (targeting-corpora/inputs/disables.json)"],
    }


def _los_disabled_from_world(spell_id: int) -> bool:
    """``DisableMgr::IsDisabledFor(SPELL, id, nullptr, SPELL_DISABLE_LOS)`` via ``oracle._los_disabled``."""
    from .oracle import _los_disabled
    value = _los_disabled(spell_id)
    if value is None:
        raise FailClosed("explicit: world table `disables` unavailable")
    return value
