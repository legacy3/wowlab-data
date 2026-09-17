"""Chain targeting (Track D): ``Spell::SelectImplicitChainTargets`` / ``SearchChainTargets``.

Stage functions over a :class:`targeting.fixture.World` and a
:class:`targeting.oracle.SpellView`; they return data and never mutate the world.

Who chains
----------
Only two selectors call the chain stage (verified by grep at the pin):

* ``SelectImplicitNearbyTargets`` (Spell.cpp:1270) -- after the nearby object is
  added (or, for DEST/GOBJ object types, after the destination is set);
* ``SelectImplicitTargetObjectTargets`` (Spell.cpp:1825) -- after
  ``AddUnitTarget(explicit, mask, checkIfValid=true, implicit=false)``, **even if
  that call rejected the explicit target** (the chain call is outside the add).

Caster-object, area, cone, line, traj, channel and dest selectors never chain.

Fixture facts read here (all fail closed when absent)
-----------------------------------------------------
* ``actor.pos``, ``actor.combat_reach`` (units), ``actor.health``/``max_health``
  (chain heal), ``actor.facts["spell_other_immunity"]`` (Track C's name; ``"ChainTarget"``),
  ``actor.facts["ignore_los_on_me"]`` for creature-kind candidates
  (``Creature::CanIgnoreLineOfSightWhenCastingOnMe``), ``actor.facts["on_transport"]``
  must be false (shared-transport distance is not modelled);
* ``world.los`` (LOS is a fixture fact; the VMAP query and hit-sphere points are world geometry);
* ``SpellView.los_disabled`` (``DisableMgr`` ``SPELL_DISABLE_LOS``, world DB);
* ``world.modifiers["chain_targets"]`` / ``["chain_jump_distance"]`` when the caster has a
  spell-mod owner (see :func:`targeting.oracle.apply_spell_mod`);
* ``world.spell_value["chain_from_caster_max_range"]`` = ``Spell::GetMinMaxRange(false).Max``
  for ``SPELL_ATTR2_CHAIN_FROM_CASTER`` spells (depends on the explicit unit, combat
  reach, movement and the ranged slot: Spell.cpp:7329-7387);
* the ``WorldObjectSpellTargetCheck`` part through Track C's ``area.target_check``
  (Track B's ``CheckTarget`` + ``relations.check``), or an injected ``check`` in isolated tests.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from . import FailClosed
from . import geometry as g
from .oracle import EffectView, SpellView, apply_spell_mod
from .trace import Trace

# SharedDefines.h:3143 SpellDmgClass
DMG_NONE, DMG_MAGIC, DMG_MELEE, DMG_RANGED = 0, 1, 2, 3
TARGET_UNIT_TARGET_CHAINHEAL_ALLY = 45   # SharedDefines.h:3001
CHAIN_CALLER_CATEGORIES = ("NEARBY",)    # + TargetObject: category DEFAULT, reference TARGET, object not DEST/SRC
UNIT_KINDS = ("player", "creature", "pet", "guardian", "totem", "minion", "vehicle")
CREATURE_KINDS = ("creature", "pet", "guardian", "totem", "minion", "vehicle")
EFFECT_ATTR_CHAIN_FROM_INITIAL = 0x00000080          # DBCEnums.h:2410
EFFECT_ATTR_PLAYERS_ONLY = 0x00004000                # DBCEnums.h:2417
EFFECT_ATTR_ENFORCE_LOS_TO_CHAIN = 0x00010000        # DBCEnums.h:2419
M_PI_F = g.f32(math.pi)


def _sel(sel: Any):
    from .selectors import info
    return info(sel) if isinstance(sel, int) else sel


def chains_from(sel: Any) -> str | None:
    """Which selector routine calls the chain stage for this target id, if any.

    Mirrors: Spell.cpp:950-1025 dispatch + the two call sites 1270 / 1825.
    """
    s = _sel(sel)
    if s.category == "NEARBY":
        return "SelectImplicitNearbyTargets (Spell.cpp:1270)"
    if s.category == "DEFAULT" and s.reference == "TARGET" and s.object not in ("SRC", "DEST"):
        return "SelectImplicitTargetObjectTargets (Spell.cpp:1825)"
    return None


def jump_radius_base(sv: SpellView, is_chain_heal: bool) -> float:
    """Mirrors: Spell.cpp:2224-2244 -- by ``DmgClass``: RANGED 7.5, MELEE 5, NONE/MAGIC 10 (12.5 chain heal).

    Any other DmgClass value leaves ``jumpRadius = 0.0f`` (switch without default).
    """
    dc = sv.need("dmg_class")
    if dc == DMG_RANGED:
        return 7.5
    if dc == DMG_MELEE:
        return 5.0
    if dc in (DMG_NONE, DMG_MAGIC):
        return 12.5 if is_chain_heal else 10.0
    return 0.0


def max_chain_targets(world, sv: SpellView, eff: EffectView, trace: Trace | None = None) -> int:
    """Mirrors: Spell.cpp:1834-1836 -- ``int32 maxTargets = ChainTargets`` + ``SpellModOp::ChainTargets``."""
    v = apply_spell_mod(world, world.caster, "chain_targets", eff.chain_targets, is_float=False)
    if trace is not None:
        trace.add("chain.max_targets", "Spell.cpp:1834-1836", output=v,
                  inputs={"authored": eff.chain_targets})
    return int(v)


def jump_radius(world, sv: SpellView, is_chain_heal: bool, trace: Trace | None = None) -> float:
    """Mirrors: Spell.cpp:2224-2247 (base by DmgClass, then ``SpellModOp::ChainJumpDistance``, float)."""
    base = jump_radius_base(sv, is_chain_heal)
    v = apply_spell_mod(world, world.caster, "chain_jump_distance", base, is_float=True)
    if trace is not None:
        trace.add("chain.jump_radius", "Spell.cpp:2224-2247", output=v, inputs={"base": base})
    return v


def search_radius(world, sv: SpellView, eff: EffectView, jump: float, chain_targets: int,
                  trace: Trace | None = None) -> float:
    """Mirrors: Spell.cpp:2250-2259.

    ``ATTR2_CHAIN_FROM_CASTER`` -> ``Spell::GetMinMaxRange(false).Max`` (fixture fact);
    ``ChainFromInitialTarget`` -> ``jumpRadius``; else ``jumpRadius * chainTargets``
    (float * uint32 -> float), where ``chainTargets = maxTargets - 1``.
    """
    if sv.has_attr("SPELL_ATTR2_CHAIN_FROM_CASTER"):
        if "chain_from_caster_max_range" not in world.spell_value:
            raise FailClosed("fixture: CHAIN_FROM_CASTER needs spell_value.chain_from_caster_max_range "
                             "(Spell::GetMinMaxRange(false).Max, Spell.cpp:7329)")
        r = g.f32(float(world.spell_value["chain_from_caster_max_range"]))
        why = "chain-from-caster"
    elif eff.attributes & EFFECT_ATTR_CHAIN_FROM_INITIAL:
        r, why = jump, "chain-from-initial-target"
    else:
        r, why = g.f32(jump * float(chain_targets)), "jump*chainTargets"
    if trace is not None:
        trace.add("chain.search_radius", "Spell.cpp:2250-2259", output=r, notes=[why])
    return r


# ---------------------------------------------------------------------------
# per-candidate predicates
# ---------------------------------------------------------------------------
def _pos(world, a: str) -> g.Vec:
    actor = world.actor(a)
    if actor.facts.get("on_transport"):
        raise FailClosed(f"fixture: {a!r} is on a transport; shared-transport distances (Object.cpp:418-423) not modelled")
    return g.vec(actor.need("pos"))


def _reach(world, a: str) -> float:
    actor = world.actor(a)
    if actor.kind == "gameobject":
        raise FailClosed("chain: GameObject::_IsWithinDist override is not modelled")
    if actor.kind not in UNIT_KINDS:
        return 0.0  # WorldObject::GetCombatReach default (Object.h:302)
    return actor.need("combat_reach")


def is_unit(world, a: str) -> bool:
    return world.actor(a).kind in UNIT_KINDS


def spell_los(world, sv: SpellView, source: str, target: str) -> bool:
    """Mirrors: Spell.cpp:9256-9269 ``Spell::IsWithinLOS(WorldObject, WorldObject, ...)``."""
    if sv.has_attr("SPELL_ATTR2_IGNORE_LINE_OF_SIGHT"):
        return True
    if sv.need("los_disabled"):  # DisableMgr SPELL_DISABLE_LOS (world DB), carried by the view
        return True
    t = world.actor(target)
    if t.kind in CREATURE_KINDS and t.fact("ignore_los_on_me"):
        return True
    return world.in_los(source, target)


def within_jump(world, source: str, target: str, jump: float) -> bool:
    """Mirrors: Object.cpp:496 ``IsWithinDist(obj, jumpRadius)`` -- 3D, both combat reaches, strict ``<``."""
    return g.is_within_dist(_pos(world, source), _reach(world, source), _pos(world, target),
                            _reach(world, target), jump)


def closer(world, ref: str, a: str, b: str) -> bool:
    """Mirrors: Object.cpp:569 ``GetDistanceOrder(a, b)`` -- centre distance², 3D, float, strict ``<``."""
    return g.distance_order(_pos(world, ref), _pos(world, a), _pos(world, b), True)


def chain_candidates(world, sv: SpellView, eff: EffectView, sel: Any, chain_source: str,
                     initial: str, radius: float, trace: Trace | None = None, *, check=None) -> list[str]:
    """The pre-filtered population ``tempTargets`` (Spell.cpp:2261-2276).

    Mirrors: ``SearchAreaTargets({0, searchRadius}, chainSource, referer=m_caster, ..., Chain)``
    (Spell.cpp:2261-2265) -> Track C's :func:`targeting.area.search_area` with reason
    ``"Chain"`` (``WorldObjectSpellAreaTargetCheck``: candidate combat reach extends the 2D
    radius, ``|dz| <= Max``, ``ChainTarget`` immunity; then ``WorldObjectSpellTargetCheck``
    with referer = caster); then ``tempTargets.remove(target)`` (only the initial target --
    **the caster is not removed** and may be jumped to) and the
    ``ATTR5_MELEE_CHAIN_TARGETING`` front-arc filter (Spell.cpp:2269-2276).

    ``check`` replaces the ``WorldObjectSpellTargetCheck`` part (isolated tests only).
    Enumeration order is ``world.visit_order`` (grid visit order).
    """
    from .area import search_area
    s = _sel(sel)
    out = search_area(world, sv, eff, s, chain_source, world.caster, (0.0, radius), "Chain", trace, check=check)
    out = [c for c in out if c != initial]
    if sv.has_attr("SPELL_ATTR5_MELEE_CHAIN_TARGETING"):
        caster = world.actor(world.caster)
        cpos = g.vec(caster.need("pos"))
        out = [c for c in out if g.has_in_arc(cpos, g.normalize_orientation(float(caster.need("orientation"))),
                                              M_PI_F, _pos(world, c), same_object=(c == world.caster))]
    if trace is not None:
        trace.add("chain.candidates", "Spell.cpp:2261-2276", output=list(out),
                  inputs={"source": chain_source, "radius": radius})
    return out


# ---------------------------------------------------------------------------
# the jump loop
# ---------------------------------------------------------------------------
def search_chain_targets(world, sv: SpellView, eff: EffectView, sel: Any, initial_id: str,
                         chain_targets: int, is_chain_heal: bool, trace: Trace, *, check=None) -> list[str]:
    """Jump targets in selection order (the initial target is not included).

    Mirrors: Spell.cpp:2221-2327 ``Spell::SearchChainTargets``.

    * chain heal: ``(deficit > maxHPDeficit || foundItr == end) && chainSource->IsWithinDist(unit, jumpRadius)
      && IsWithinLOS(chainSource, unit)`` with ``uint32 deficit = uint64 max - uint64 health``
      (truncated); ties keep the earlier list element; the first accepted candidate
      needs distance and LOS too; a zero-deficit unit is chosen only when it is the
      first acceptable one; ``EnforceLineOfSightToChainTargets`` is **not** read.
    * otherwise: the first candidate must pass ``IsWithinDist(jumpRadius)``; later ones
      only need ``GetDistanceOrder(itr, found)`` (strictly closer centre distance) --
      **no jump-radius check for replacements** (defect TG-D-DEF-01); then LOS from the
      chain source and, with ``EnforceLineOfSightToChainTargets``, from the caster.
    * the chain source moves to the chosen target unless ``ATTR2_CHAIN_FROM_CASTER``
      (source = caster) or ``ChainFromInitialTarget``.
    """
    jump = jump_radius(world, sv, is_chain_heal, trace)
    radius = search_radius(world, sv, eff, jump, chain_targets, trace)
    from_caster = sv.has_attr("SPELL_ATTR2_CHAIN_FROM_CASTER")
    from_initial = bool(eff.attributes & EFFECT_ATTR_CHAIN_FROM_INITIAL)
    enforce_los = bool(eff.attributes & EFFECT_ATTR_ENFORCE_LOS_TO_CHAIN)
    source = world.caster if from_caster else initial_id
    temp = chain_candidates(world, sv, eff, sel, source, initial_id, radius, trace, check=check)
    chosen: list[str] = []
    remaining = chain_targets
    while remaining:
        found: str | None = None
        notes: list[str] = []
        if is_chain_heal:
            max_def = 0
            for c in temp:
                if not is_unit(world, c):
                    continue
                a = world.actor(c)
                raw_deficit = int(a.need("max_health")) - int(a.need("health"))
                deficit = raw_deficit & 0xFFFFFFFF
                if deficit != raw_deficit:
                    trace.add("chain.heal.deficit_truncated", "Spell.cpp:2291", output=deficit,
                              inputs={"unit": c, "uint64_deficit": raw_deficit}, defect="TG-D-DEF-02")
                if (deficit > max_def or found is None) and within_jump(world, source, c, jump) \
                        and spell_los(world, sv, source, c):
                    found, max_def = c, deficit
        else:
            for c in temp:
                best = closer(world, source, c, found) if found is not None else within_jump(world, source, c, jump)
                if not best:
                    continue
                if not spell_los(world, sv, source, c):
                    continue
                if enforce_los and not spell_los(world, sv, world.caster, c):
                    continue
                if found is not None and not within_jump(world, source, c, jump):
                    notes.append(f"{c} replaced {found} by distance order although outside the jump radius")
                found = c
        if found is None:
            trace.add("chain.jump.end", "Spell.cpp:2316-2318", output=None, inputs={"source": source})
            break
        trace.add("chain.jump", "Spell.cpp:2283-2325", output=found, inputs={"source": source},
                  notes=notes, defect="TG-D-DEF-01" if notes else None)
        if not from_caster and not from_initial:
            source = found
        chosen.append(found)
        temp.remove(found)
        remaining -= 1
    return chosen


@dataclass
class ChainResult:
    max_targets: int
    jumps: list[str] = field(default_factory=list)
    #: (unit, losPosition actor) pairs handed to AddUnitTarget(unit, mask, false, true, losPosition)
    add_calls: list[tuple[str, str]] = field(default_factory=list)
    applied_multiplier_mask: int = 0


def select_implicit_chain_targets(world, sv: SpellView, eff: EffectView, sel: Any, initial_id: str,
                                  eff_mask: int, trace: Trace, area_hook=None, *, check=None) -> ChainResult:
    """Mirrors: Spell.cpp:1832-1864 ``Spell::SelectImplicitChainTargets``.

    ``eff`` is the *first* effect of the effect-mask group (its ``ChainTargets`` is used
    for every grouped effect; the grouping at Spell.cpp:741-775 does not compare
    ``ChainTargets``).  ``area_hook(targets) -> targets`` is the
    ``OnObjectAreaTargetSelect`` script hook, which runs **after** the jumps are
    chosen and before any of them is added (Spell.cpp:1850).  Returns the
    ``AddUnitTarget`` calls with their ``losPosition`` (``CheckEffectTarget`` then
    requires LOS from that position, Spell.cpp:8253-8255); adding is G's stage.
    """
    s = _sel(sel)
    max_targets = max_chain_targets(world, sv, eff, trace)
    res = ChainResult(max_targets=max_targets)
    if max_targets <= 1:
        trace.add("chain.skip", "Spell.cpp:1838", output=[], notes=[f"maxTargets={max_targets} <= 1"])
        return res
    # Spell.cpp:1840-1844: multipliers of every grouped effect with index >= this one are armed
    res.applied_multiplier_mask = sum(1 << k for k in range(eff.index, len(sv.effects)) if eff_mask & (1 << k))
    targets = search_chain_targets(world, sv, eff, s, initial_id, max_targets - 1, s.id == TARGET_UNIT_TARGET_CHAINHEAL_ALLY, trace, check=check)
    res.jumps = list(targets)
    if area_hook is not None:
        targets = list(area_hook(list(targets)))
        trace.add("chain.script_hook", "Spell.cpp:1850 CallScriptObjectAreaTargetSelectHandlers", output=targets,
                  evidence="script-consumer")
    from_caster = sv.has_attr("SPELL_ATTR2_CHAIN_FROM_CASTER")
    los_pos = world.caster if from_caster else initial_id
    for t in targets:
        if is_unit(world, t):
            res.add_calls.append((t, los_pos))
        if not from_caster and not (eff.attributes & EFFECT_ATTR_CHAIN_FROM_INITIAL):
            los_pos = t
    trace.add("chain.add_calls", "Spell.cpp:1852-1862", output=[list(p) for p in res.add_calls])
    return res


# ---------------------------------------------------------------------------
# competing models (for discriminating fixtures and tests only)
# ---------------------------------------------------------------------------
def naive_model(world, candidates: list[str], initial: str, n: int, *, reference: str = "previous",
                metric: str = "nearest", tie: str = "first", jump: float | None = None) -> list[str]:
    """Plausible-but-wrong chain policies used to prove a fixture discriminates.

    ``reference``: previous | initial | caster; ``metric``: nearest | max-deficit |
    lowest-health-pct; ``tie``: first | lowest-id.  Pure, exact double arithmetic.
    Not a Trinity mirror.
    """
    pool = [c for c in candidates if c != initial]
    out: list[str] = []
    src = initial
    for _ in range(n):
        ref = {"previous": src, "initial": initial, "caster": world.caster}[reference]
        rp = world.actor(ref).need("pos")

        def d(c: str) -> float:
            p = world.actor(c).need("pos")
            return math.dist(rp, p)
        cands = [c for c in pool if jump is None or d(c) < jump]
        if not cands:
            break
        if metric == "nearest":
            key = lambda c: d(c)  # noqa: E731
        elif metric == "max-deficit":
            key = lambda c: -(world.actor(c).max_health - world.actor(c).health)  # noqa: E731
        else:
            key = lambda c: world.actor(c).health / world.actor(c).max_health  # noqa: E731
        best = min(key(c) for c in cands)
        tied = [c for c in cands if key(c) == best]
        pick = tied[0] if tie == "first" else min(tied)
        out.append(pick)
        pool.remove(pick)
        src = pick
    return out
