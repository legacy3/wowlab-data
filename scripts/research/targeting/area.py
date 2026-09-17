"""Area / cone / line / nearby candidate searches (Track C).

Stage functions over a :class:`targeting.fixture.World` and a
:class:`targeting.oracle.SpellView`.  Candidate enumeration is always
``world.enumeration()`` (the fixture's ``visit_order``) filtered -- the grid
visit order is internal to the map and is never invented here.  What the map
visit order *is* structurally (cited for fixture authors, not modelled):

* ``Spell::SearchTargets`` (Spell.cpp:2163) visits the ``WorldTypeMapContainer``
  (players, player-owned summons created as world objects, resurrectable
  corpses) over the whole cell area first, then the ``GridTypeMapContainer``
  (other creatures, game objects, bones, ...) -- GridDefines.h:91-92;
* ``Cell::Visit`` (CellImpl.h:56-110) visits the standing cell first, then the
  cell rectangle x-then-y (``VisitCircle`` when both spans exceed 4 cells);
* inside a cell the ``GridRefManager`` list is newest-first
  (``GridReference::targetObjectBuildLink`` -> ``push_front``, GridReference.h:34).

Phase: ``SearchAreaTargets`` / ``SearchNearbyTarget`` use
``PhasingHandler::GetAlwaysVisiblePhaseShift()`` (no phase filter,
Spell.cpp:2201, 2217) while cone / line / traj searchers are built from
``m_caster`` (caster phase filter, GridNotifiers.h:448, GridNotifiersImpl.h:191).
Fixtures model phase as the actor fact ``in_caster_phase`` (read only by
cone / line).

Script hooks (``OnObjectAreaTargetSelect``) run after the search and before the
cap; this module refuses (FailClosed) when a hook is bound unless the caller
passes ``script_hook`` (Track E adapters own the hook bodies).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from . import FailClosed
from . import geometry as g
from .trace import Trace

# GridDefines.h:72-84 GRID_MAP_TYPE_MASK_*
GRID_MASK = {"CORPSE": 0x01, "CREATURE": 0x02, "DYNAMICOBJECT": 0x04, "GAMEOBJECT": 0x08, "PLAYER": 0x10,
             "AREATRIGGER": 0x20, "SCENEOBJECT": 0x40, "CONVERSATION": 0x80}
GRID_MASK_ALL = 0xFF
KIND_GRID = {"player": "PLAYER", "creature": "CREATURE", "pet": "CREATURE", "guardian": "CREATURE",
             "totem": "CREATURE", "minion": "CREATURE", "vehicle": "CREATURE", "corpse": "CORPSE",
             "gameobject": "GAMEOBJECT", "dynamicobject": "DYNAMICOBJECT", "areatrigger": "AREATRIGGER"}
UNIT_KINDS = ("player", "creature", "pet", "guardian", "totem", "minion", "vehicle")
HOOK_AREA = "OnObjectAreaTargetSelect"


def _selector(sel):
    from . import selectors
    return selectors.info(sel) if isinstance(sel, int) else sel


def _pos(world, actor_id: str) -> g.Vec:
    return g.vec(world.actor(actor_id).need("pos"))


def _orientation(world, actor_id: str) -> float:
    """Stored orientation is normalised on write (Position::SetOrientation, Position.h:84)."""
    return g.normalize_orientation(float(world.actor(actor_id).need("orientation")))


def combat_reach(world, actor_id: str) -> float:
    """Mirrors: Object.h:302 (WorldObject: 0) / Unit.h:704 (Unit: UnitData CombatReach)."""
    a = world.actor(actor_id)
    if a.kind in UNIT_KINDS:
        return g.f32(float(a.need("combat_reach")))
    return 0.0


def _other_immunity(world, actor_id: str) -> set[str]:
    a = world.actor(actor_id)
    if "spell_other_immunity" not in a.facts:
        raise FailClosed(f"fixture: unit {actor_id!r} does not state fact 'spell_other_immunity' "
                         "(SpellOtherImmunity AoETarget/ChainTarget, SpellMgr.h:554)")
    return set(a.facts["spell_other_immunity"])


# ---------------------------------------------------------------------------
# GetSearcherTypeMask
# ---------------------------------------------------------------------------
def searcher_type_mask(sv, eff, object_type: str) -> int:
    """Mirrors: Spell.cpp:2125 ``Spell::GetSearcherTypeMask``.

    Implicit-target conditions narrow the mask through
    ``ConditionMgr::GetSearcherTypeMaskForConditionList`` (not ported: FailClosed).
    """
    mask = GRID_MASK_ALL
    if object_type in ("UNIT", "UNIT_AND_DEST"):
        mask &= GRID_MASK["PLAYER"] | GRID_MASK["CREATURE"]
    elif object_type in ("CORPSE", "CORPSE_ENEMY", "CORPSE_ALLY"):
        mask &= GRID_MASK["PLAYER"] | GRID_MASK["CORPSE"] | GRID_MASK["CREATURE"]
    elif object_type in ("GOBJ", "GOBJ_ITEM"):
        mask &= GRID_MASK["GAMEOBJECT"]
    if sv.has_attr("SPELL_ATTR3_ONLY_ON_PLAYER") or eff.has_attribute("PlayersOnly"):
        mask &= GRID_MASK["CORPSE"] | GRID_MASK["PLAYER"]
    if sv.has_attr("SPELL_ATTR3_ONLY_ON_GHOSTS"):
        mask &= GRID_MASK["PLAYER"]
    if sv.has_attr("SPELL_ATTR5_NOT_ON_PLAYER"):
        mask &= ~GRID_MASK["PLAYER"] & GRID_MASK_ALL
    if eff.conditions is not None:
        raise FailClosed(f"area: effect {eff.index} has implicit-target conditions; "
                         "GetSearcherTypeMaskForConditionList (ConditionMgr.cpp) is not ported")
    return mask


def _candidates(world, mask: int, trace: Trace | None, stage: str) -> list[str]:
    """Mirrors: Spell.cpp:2163 ``SearchTargets`` + GridNotifiersImpl.h:184 visit (type mask part)."""
    if not mask:
        if trace is not None:
            trace.add(stage + ".mask", "Spell.cpp:2166", output=[], notes=["containerMask == 0: no search"])
        return []
    out = []
    for aid in world.enumeration():
        kind = KIND_GRID.get(world.actor(aid).kind)
        if kind is None:
            raise FailClosed(f"area: actor kind {world.actor(aid).kind!r} has no grid container")
        if mask & GRID_MASK[kind]:
            out.append(aid)
    return out


# ---------------------------------------------------------------------------
# WorldObjectSpellTargetCheck (non-geometric part) -- delegated
# ---------------------------------------------------------------------------
def target_check(world, sv, caster: str, target: str, sel, referer: str, trace: Trace | None) -> bool:
    """``WorldObjectSpellTargetCheck::operator()`` (Spell.cpp:9306-9396).

    CheckTarget (9308): ``targeting.explicit.check_target`` when Track B publishes it;
    until then the fixture fact ``check_target`` (bool) is read (temporary, marked).
    Relation / object-type part: ``targeting.relations.check`` (Track B).
    Conditions (9392): refused upstream in :func:`searcher_type_mask`.
    """
    try:
        from .explicit import check_target as _ct  # type: ignore[attr-defined]
    except ImportError:
        _ct = None
    if _ct is not None:
        ok = _ct(world, sv, caster, target, True, trace) == "SPELL_CAST_OK"
    else:
        ok = bool(world.actor(target).fact("check_target"))  # TEMPORARY local (explicit.check_target absent)
    if not ok:
        return False
    from .relations import check
    return check(world, sv, caster, target, sel.check, trace, referer_id=referer, object_type=sel.object)


# ---------------------------------------------------------------------------
# area
# ---------------------------------------------------------------------------
def area_predicate(world, sv, target: str, center: g.Vec, radius: tuple[float, float], reason: str) -> bool:
    """Geometric + immunity part of ``WorldObjectSpellAreaTargetCheck::operator()``.

    Mirrors: Spell.cpp:9418-9450.
    """
    a = world.actor(target)
    rmin, rmax = radius
    if a.kind == "gameobject":
        # GameObject::IsInRange (GameObject.cpp:3599) reads GameObjectDisplayInfo geobox: world data.
        return bool(a.fact("go_in_area_range")) and (
            rmin <= 0.0 or not bool(a.fact("go_in_area_min_range")))
    if not g.area_unit_in_cylinder(_pos(world, target), combat_reach(world, target), center, rmin, rmax):
        return False
    if a.kind in UNIT_KINDS:
        imm = _other_immunity(world, target)
        if reason == "Area":
            if not sv.has_attr("SPELL_ATTR8_CAN_HIT_AOE_UNTARGETABLE") and "AoETarget" in imm:
                return False
        elif reason == "Chain":
            if "ChainTarget" in imm:
                return False
        else:
            raise FailClosed(f"area: unknown search reason {reason!r}")
    return True


def search_area(world, spell_view, eff, sel, center, referer: str, radius: tuple[float, float],
                reason: str, trace: Trace | None, *, check: Callable | None = None) -> list[str]:
    """Mirrors: Spell.cpp:2207 ``Spell::SearchAreaTargets``.

    ``center`` is an ``[x, y, z]`` or an actor id; ``radius`` is the ``SpellRange``
    (Min, Max) already scaled by ``RadiusMod``.  Returns the matching actors in
    enumeration order.  No phase filter (AlwaysVisible phase shift).
    ``check`` replaces :func:`target_check` (same signature) for isolated tests.
    """
    check = check or target_check
    sel = _selector(sel)
    c = _pos(world, center) if isinstance(center, str) else g.vec(center)
    rmin, rmax = g.f32(radius[0]), g.f32(radius[1])
    mask = searcher_type_mask(spell_view, eff, sel.object)
    out: list[str] = []
    rejected: list[str] = []
    for aid in _candidates(world, mask, trace, "area.search"):
        if area_predicate(world, spell_view, aid, c, (rmin, rmax), reason) and \
                check(world, spell_view, world.caster, aid, sel, referer, trace):
            out.append(aid)
        else:
            rejected.append(aid)
    if trace is not None:
        trace.add("area.search", "Spell.cpp:2207", output=out,
                  inputs={"center": list(c), "radius": [rmin, rmax], "referer": referer, "reason": reason,
                          "mask": mask, "selector": sel.id},
                  notes=[f"rejected: {rejected}", "search radius Max+40 (EXTRA_CELL_SEARCH_RADIUS) only widens the cell set"])
    return out


def _hooks(sv, eff, sel, script_hook, trace, lst: str = HOOK_AREA):
    hooks = sv.target_hooks(eff.index, sel.id, lst)
    if hooks and script_hook is None:
        raise FailClosed(f"area: spell {sv.id} effect {eff.index} binds {lst} hook(s) "
                         f"{[h['script'] for h in hooks]}; pass a script adapter (Track E)")
    return hooks


def _run_hook(targets, hooks, script_hook, trace, mirrors):
    if not hooks:
        return targets
    new = list(script_hook(list(targets), hooks))
    if trace is not None:
        trace.add("area.script_hook", mirrors, output=new, inputs={"before": list(targets)},
                  evidence="script-consumer")
    return new


def resolve_referer_center(world, sel, effect_index: int, unique_targets: list[tuple[str, int]] | None
                           ) -> tuple[str | None, g.Vec | None]:
    """Mirrors: Spell.cpp:1328-1377 (referer and centre per reference type)."""
    ref = sel.reference
    if ref in ("SRC", "DEST", "CASTER"):
        referer = world.caster
    elif ref == "TARGET":
        referer = world.explicit.get("unit")
    elif ref == "LAST":
        if unique_targets is None:
            raise FailClosed("area: LAST reference needs the current m_UniqueTargetInfo (unique_targets)")
        referer = world.caster
        for tid, mask in reversed(unique_targets):
            if mask & (1 << effect_index):
                referer = tid  # ObjectAccessor::GetUnit -- fixture actors always resolve
                break
    else:
        raise FailClosed(f"area: reference {ref!r} hits ABORT_MSG (Spell.cpp:1355)")
    if referer is None:
        return None, None
    if ref == "SRC":
        src = world.explicit.get("src")
        if src is None:
            raise FailClosed("area: SRC reference needs m_targets src position (explicit.src)")
        return referer, g.vec(src)
    if ref == "DEST":
        dst = world.explicit.get("dest")
        if dst is None:
            raise FailClosed("area: DEST reference needs m_targets dst position (explicit.dest); "
                             "GetDstPos never returns null (Spell.cpp:1368)")
        return referer, g.vec(dst)
    return referer, _pos(world, referer)


def select_area(world, sv, effect_index: int, target_index: str, trace: Trace | None, *,
                radius: tuple[float, float] | None = None,
                unique_targets: list[tuple[str, int]] | None = None,
                script_hook: Callable | None = None, check: Callable | None = None) -> dict[str, Any]:
    """Mirrors: Spell.cpp:1326 ``Spell::SelectImplicitAreaTargets`` (up to AddUnitTarget).

    Order: referer/centre -> search (special selectors) -> UNIT_AND_DEST ``ModDst(referer)``
    -> ``OnObjectAreaTargetSelect`` hook -> FURTHEST sort -> cap -> AddUnitTarget
    (with ``center`` as LOS position, Spell.cpp:1456).  Returns
    ``{"targets", "center", "referer", "dest_mod", "los_position"}``.
    """
    from . import caps
    from .oracle import calc_radius
    eff = sv.effect(effect_index)
    sel = _selector(eff.target(target_index))
    referer, center = resolve_referer_center(world, sel, effect_index, unique_targets)
    if referer is None:
        if trace is not None:
            trace.add("area.referer", "Spell.cpp:1359", output=None, notes=["no referer: return"])
        return {"targets": [], "center": None, "referer": None, "dest_mod": None, "los_position": None}
    if radius is None:
        rmin, rmax = calc_radius(world, sv, eff, target_index, world.caster, trace)
        mod = g.f32(float(world.spell_value.get("radius_mod", 1.0)))
        radius = (g.mul(rmin, mod), g.mul(rmax, mod))   # SpellRange::operator*(float) (SpellDefines.h:351)
    tid = sel.id
    targets: list[str]
    if tid == 105:  # TARGET_UNIT_CASTER_AND_PASSENGERS
        raise FailClosed("area: CASTER_AND_PASSENGERS needs vehicle-kit seats (not modelled)")
    elif tid == 118:  # TARGET_UNIT_TARGET_ALLY_OR_RAID
        unit = world.explicit.get("unit")
        targets = []
        if unit is not None:
            from .relations import _in_raid_with  # Track B predicate (IsInRaidWith)
            caster_is_unit = world.actor(world.caster).kind in UNIT_KINDS
            if not caster_is_unit or not _in_raid_with(world, world.caster, unit, trace):
                targets = [unit]
            else:
                targets = search_area(world, sv, eff, sel, unit, referer, radius, "Area", trace, check=check)
    elif tid == 120:  # TARGET_UNIT_CASTER_AND_SUMMONS
        targets = [world.caster] + search_area(world, sv, eff, sel, center, referer, radius, "Area", trace, check=check)
    elif tid in (122, 123):  # THREAT_LIST / TAP_LIST
        key = "threat_list" if tid == 122 else "tap_list"
        targets = list(world.actor(world.caster).fact(key))
    else:
        targets = search_area(world, sv, eff, sel, center, referer, radius, "Area", trace, check=check)

    dest_mod = None
    if sel.object == "UNIT_AND_DEST":
        # SpellDestination dest(*referer) -- the REFERER, not the centre (Spell.cpp:1428)
        dest_mod = {"pos": list(_pos(world, referer)), "orientation": _orientation(world, referer)}
        if sv.has_attr("SPELL_ATTR4_USE_FACING_FROM_SPELL"):
            dest_mod["orientation"] = g.normalize_orientation(eff.pos_facing)
        if sv.target_hooks(effect_index, tid, "OnDestinationTargetSelect"):
            raise FailClosed("area: OnDestinationTargetSelect hook bound (Track E)")
        if trace is not None:
            trace.add("area.unit_and_dest", "Spell.cpp:1426-1435", output=dest_mod,
                      notes=["m_targets.ModDst(referer) (ASSERTs HasDst, Spell.cpp:377)"])

    hooks = _hooks(sv, eff, sel, script_hook, trace)
    targets = _run_hook(targets, hooks, script_hook, trace, "Spell.cpp:1437")
    if tid == 115:  # TARGET_UNIT_SRC_AREA_FURTHEST_ENEMY
        targets = caps.sort_by_distance(world, targets, referer, ascending=False, trace=trace,
                                        mirrors="Spell.cpp:1440")
    targets = caps.apply(world, sv, eff, sel, targets, trace)
    return {"targets": targets, "center": list(center), "referer": referer, "dest_mod": dest_mod,
            "los_position": list(center)}


# ---------------------------------------------------------------------------
# cone
# ---------------------------------------------------------------------------
def cone_angle_degrees(sv, sel) -> float:
    """Mirrors: Spell.cpp:1281-1290 (+ SpellMgr.cpp:5281-5283 load-time default).

    ``LoadSpellInfoCorrections`` sets ``ConeAngle = 90`` for every spell with a
    cone-category effect (A or B) whose ConeAngle ``fuzzyEq``s 0, so the
    ``TARGET_UNIT_CONE_180_DEG_ENEMY`` 0 -> 180 branch never fires (defect
    TG-C-D01).  ``sv.cone_angle`` is the DB2 ``ConeDegrees`` (pre-load value);
    applying the default twice is idempotent.
    """
    angle = g.f32(sv.cone_angle)
    if fuzzy_eq32(angle, 0.0) and any(
            e.is_effect and "CONE" in (_selector(e.target_a).category, _selector(e.target_b).category)
            for e in sv.effects):
        angle = 90.0
    if sel.id == 54 and angle == 0.0:  # TARGET_UNIT_CONE_180_DEG_ENEMY; unreachable at the pin (kept to mirror 1284-1286)
        angle = 180.0
    return angle


_CONE_DEFECTS = {
    "TG-C-D01": "TG-C-D01: CONE_180 with ConeDegrees 0 uses the load-time 90-degree default (Spell.cpp:1285 dead)",
    "TG-C-D04": "TG-C-D04: cone arc DegToRad(angle) normalises to 0 (Position.cpp:180)",
}


def _cone_alternatives(sv, sel, cone_deg: float) -> dict[float | None, tuple[str, str]]:
    """Intended-behaviour cone angles whose outcome would differ only because of a reproduced defect."""
    alts: dict[float | None, tuple[str, str]] = {}
    if sel.id == 54 and fuzzy_eq32(g.f32(sv.cone_angle), 0.0):
        alts[180.0] = ("TG-C-D01", "180-degree default")
    if cone_deg != 0.0 and g.normalize_orientation(g.deg_to_rad(cone_deg)) == 0.0 \
            and not sv.has_cu("SPELL_ATTR0_CU_CONE_LINE") and not sv.has_cu("SPELL_ATTR0_CU_CONE_BACK"):
        alts[None] = ("TG-C-D04", "full circle (no arc test)")
    return alts


def cone_predicate(world, sv, target: str, cone_deg: float, width: float, radius: tuple[float, float]) -> bool:
    """Geometric part of ``WorldObjectSpellConeTargetCheck::operator()`` (Spell.cpp:9459-9479)
    followed by the area cylinder / immunity part (9418)."""
    caster = world.caster
    cpos, co = _pos(world, caster), _orientation(world, caster)
    tpos = _pos(world, target)
    cone_rad = g.deg_to_rad(cone_deg)
    if sv.has_cu("SPELL_ATTR0_CU_CONE_BACK"):
        if not g.cone_back_ok(cpos, co, cone_rad, tpos):
            return False
    elif sv.has_cu("SPELL_ATTR0_CU_CONE_LINE"):
        if not g.has_in_line(cpos, co, tpos, combat_reach(world, target), width):
            return False
    else:
        within = False
        if world.actor(caster).kind in UNIT_KINDS:
            t = world.actor(target)
            # IsWithinBoundaryRadius(target->ToUnit()): null for non-units -> false (Unit.cpp:709)
            if t.kind in UNIT_KINDS:
                within = g.is_within_boundary_radius(cpos, tpos, g.f32(float(t.need("bounding_radius"))))
        if not g.cone_arc_ok(cpos, co, cone_rad, tpos, within):
            return False
    return area_predicate(world, sv, target, cpos, radius, "Area")


def select_cone(world, sv, effect_index: int, target_index: str, trace: Trace | None, *,
                radius: tuple[float, float] | None = None, script_hook: Callable | None = None,
                check: Callable | None = None) -> list[str]:
    """Mirrors: Spell.cpp:1273 ``Spell::SelectImplicitConeTargets`` (up to AddUnitTarget, checkIfValid=false)."""
    from . import caps
    from .oracle import calc_radius
    eff = sv.effect(effect_index)
    sel = _selector(eff.target(target_index))
    if sel.reference != "CASTER":
        raise FailClosed("cone: non-caster reference hits ABORT_MSG (Spell.cpp:1277)")
    cone_deg = cone_angle_degrees(sv, sel)
    if radius is None:
        rmin, rmax = calc_radius(world, sv, eff, target_index, world.caster, trace)
        mod = g.f32(float(world.spell_value.get("radius_mod", 1.0)))
        radius = (g.mul(rmin, mod), g.mul(rmax, mod))
    width = g.f32(sv.width) if sv.width else combat_reach(world, world.caster)
    mask = searcher_type_mask(sv, eff, sel.object)
    targets = []
    defects: set[str] = set()
    for aid in _candidates(world, mask, trace, "cone.search"):
        if not bool(world.actor(aid).fact("in_caster_phase")):
            continue  # WorldObjectListSearcher(m_caster, ...) phase filter (GridNotifiersImpl.h:191)
        got = cone_predicate(world, sv, aid, cone_deg, width, radius)
        for alt_deg, (d_id, wanted) in _cone_alternatives(sv, sel, cone_deg).items():
            alt = (area_predicate(world, sv, aid, _pos(world, world.caster), radius, "Area") if alt_deg is None
                   else cone_predicate(world, sv, aid, alt_deg, width, radius))
            if alt != got:
                defects.add(d_id)
        if got and (check or target_check)(world, sv, world.caster, aid, sel, world.caster, trace):
            targets.append(aid)
    if trace is not None:
        trace.add("cone.search", "Spell.cpp:1298-1303", output=targets,
                  inputs={"cone_degrees": cone_deg, "cone_radians": g.deg_to_rad(cone_deg), "width": width,
                          "radius": list(radius)},
                  defect="; ".join(_CONE_DEFECTS[d] for d in sorted(defects)) or None)
    targets = _run_hook(targets, _hooks(sv, eff, sel, script_hook, trace), script_hook, trace, "Spell.cpp:1305")
    return caps.apply(world, sv, eff, sel, targets, trace)


# ---------------------------------------------------------------------------
# line
# ---------------------------------------------------------------------------
def select_line(world, sv, effect_index: int, target_index: str, trace: Trace | None, *,
                radius: tuple[float, float] | None = None, script_hook: Callable | None = None,
                check: Callable | None = None) -> list[str]:
    """Mirrors: Spell.cpp:1964 ``Spell::SelectImplicitLineTargets`` (up to AddUnitTarget).

    ``WorldObjectSpellLineTargetCheck::operator()`` (9505) calls the base
    ``WorldObjectSpellTargetCheck`` directly: **no** radius / cylinder / AoE-immunity
    test.  Length is bounded only by the cell set visited for ``radius.Max`` (no +40).
    Which candidates lie in visited cells is map data: the fixture must list only
    visited candidates in ``visit_order`` and state ``in_visited_cells`` is implied.
    """
    from . import caps
    from .oracle import calc_radius
    eff = sv.effect(effect_index)
    sel = _selector(eff.target(target_index))
    caster = world.caster
    ref = sel.reference
    if ref == "SRC":
        dst = world.explicit.get("src")
        if dst is None:
            raise FailClosed("line: SRC reference needs explicit.src (GetSrcPos is never null)")
        dst = (g.vec(dst), world.explicit.get("src_orientation"))
    elif ref == "DEST":
        dst = world.explicit.get("dest")
        if dst is None:
            raise FailClosed("line: DEST reference needs explicit.dest")
        dst = (g.vec(dst), world.explicit.get("dest_orientation"))
    elif ref == "CASTER":
        dst = (_pos(world, caster), _orientation(world, caster))
    elif ref == "TARGET":
        unit = world.explicit.get("unit")
        dst = None if unit is None else (_pos(world, unit), _orientation(world, unit))
    else:
        raise FailClosed(f"line: reference {ref!r} hits ABORT_MSG (Spell.cpp:1985)")
    if radius is None:
        rmin, rmax = calc_radius(world, sv, eff, target_index, caster, trace)
        mod = g.f32(float(world.spell_value.get("radius_mod", 1.0)))
        radius = (g.mul(rmin, mod), g.mul(rmax, mod))
    width = g.f32(sv.width) if sv.width else combat_reach(world, caster)
    src, so = _pos(world, caster), _orientation(world, caster)
    orientation = so
    if dst is not None:
        dpos, do = dst
        if do is None and ref in ("SRC", "DEST"):
            raise FailClosed("line: Position::operator!= compares orientation too; state explicit.dest_orientation")
        same = all(fuzzy_eq32(a, b) for a, b in zip((*src, so), (*dpos, g.normalize_orientation(do))))
        orientation = g.line_orientation(src, so, dpos, same)
    mask = searcher_type_mask(sv, eff, sel.object)
    targets = []
    decisive: list[str] = []
    for aid in _candidates(world, mask, trace, "line.search"):
        if not bool(world.actor(aid).fact("in_caster_phase")):
            continue
        if g.has_in_line(src, orientation, _pos(world, aid), combat_reach(world, aid), width) and \
                (check or target_check)(world, sv, caster, aid, sel, caster, trace):
            targets.append(aid)
            if _area_check_would_reject(world, sv, aid, src, radius):
                decisive.append(aid)
    if trace is not None:
        trace.add("line.search", "Spell.cpp:1992-1996, 9497-9510", output=targets,
                  inputs={"orientation": orientation, "width": width, "search_radius": radius[1]},
                  notes=[f"kept only because the area check is skipped: {decisive}"] if decisive else [],
                  defect="TG-C-D02: line check has no length/min-range/vertical/AoE-immunity test" if decisive else None)
    targets = _run_hook(targets, _hooks(sv, eff, sel, script_hook, trace), script_hook, trace, "Spell.cpp:1998")
    return caps.apply(world, sv, eff, sel, targets, trace)


def _area_check_would_reject(world, sv, target: str, centre: g.Vec, radius) -> bool:
    """Would ``WorldObjectSpellAreaTargetCheck`` (skipped by the line check) have dropped ``target``?"""
    a = world.actor(target)
    if a.kind == "gameobject":
        return False  # geobox is world data; not decidable here
    if not g.area_unit_in_cylinder(_pos(world, target), combat_reach(world, target), centre, radius[0], radius[1]):
        return True
    imm = a.facts.get("spell_other_immunity") or []
    return a.kind in UNIT_KINDS and "AoETarget" in imm and not sv.has_attr("SPELL_ATTR8_CAN_HIT_AOE_UNTARGETABLE")


def fuzzy_eq32(a: float, b: float) -> bool:
    """Mirrors: g3dmath.h:854 ``fuzzyEq(float, float)`` (eps = 1e-5f * (|a| + 1))."""
    a, b = g.f32(a), g.f32(b)
    if a == b:
        return True
    aa = g.add(g.f32(abs(a)), 1.0)
    eps = g.f32(0.00001) if aa == float("inf") else g.mul(g.f32(0.00001), aa)
    return abs(g.sub(a, b)) <= eps


# ---------------------------------------------------------------------------
# nearby
# ---------------------------------------------------------------------------
def search_nearby(world, sv, eff, sel, range_: float, trace: Trace | None, *,
                  check: Callable | None = None) -> str | None:
    """Mirrors: Spell.cpp:2194 ``SearchNearbyTarget`` + 9402 ``WorldObjectSpellNearbyTargetCheck``.

    ``WorldObjectLastSearcher``: every accepted object replaces the result and
    shrinks ``_range`` to its distance; acceptance needs ``dist < _range`` (strict),
    so among equal distances the *first visited* wins.  Distance is
    ``target->GetDistance(Position)``: exact 3D minus the target's combat reach
    only, clamped at 0.  No phase filter.
    """
    sel = _selector(sel)
    mask = searcher_type_mask(sv, eff, sel.object)
    caster = world.caster
    cpos = _pos(world, caster)
    rng = g.f32(range_)
    found = None
    for aid in _candidates(world, mask, trace, "nearby.search"):
        d = g.nearby_distance(_pos(world, aid), combat_reach(world, aid), cpos)
        if d < rng and (check or target_check)(world, sv, caster, aid, sel, caster, trace):
            rng = d
            found = aid
    if trace is not None:
        trace.add("nearby.search", "Spell.cpp:2194, 9402", output=found, inputs={"range": g.f32(range_)})
    return found
