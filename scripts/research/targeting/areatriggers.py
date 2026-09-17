"""AreaTrigger recipient selection (Track I).

Spells with ``SPELL_EFFECT_CREATE_AREATRIGGER`` (179/353) or
``SPELL_AURA_AREA_TRIGGER`` (395) do not choose recipients through
``Spell::SelectSpellTargets``.  They spawn an ``AreaTrigger`` whose own update
loop selects units every map tick:

    AreaTrigger::Update (AreaTrigger.cpp:361)
      movement -> duration (expire => Remove, no target update) -> _ai->OnUpdate
      -> UpdateTargetList (:641)
           SearchUnitIn<Shape> (:732-851) -> SearchUnits (:717,
               AnyUnitInObjectRangeCheck(reqAlive=false), UnitListSearcher)
           template-gated filters (:661-711: action-set flags, death state,
               uninteractible, CONDITION_SOURCE_TYPE_AREATRIGGER)
           HandleUnitEnterExit (:853): enter in list order, exit in
               GuidUnorderedSet order (unspecified)
      HandleUnitEnter (:881): DoActions (:1140, template actions filtered by
               UnitFitToActionRequirement :1108) then AreaTriggerAI::OnUnitEnter

Shapes, template and actions are **world-DB** facts
(``AreaTriggerDataStore::LoadAreaTriggerTemplates``, AreaTriggerDataStore.cpp:55);
the client ``AreaTriggerCreateProperties`` DB2 is not read by the consumer.

Everything geometric reproduces binary32 arithmetic via :mod:`targeting.geometry`.
Curves (scale / morph / move / facing), splines and orbits are **not**
evaluated: when a shape depends on them the fixture must state the evaluated
value (``progress``, ``scale_curve_value``) or the stage fails closed.

What the AI scripts do with the selected units is a hand-verified table
(:data:`AT_SCRIPTS`, :data:`EXTERNAL_CONSUMERS`), evidence ``script-consumer``
(bodies read at the pin), not inferred from names.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import cache
from typing import Any

from . import CORPORA, FailClosed
from . import geometry as g
from .trace import Trace

AT_WORLD_EXTRACT = CORPORA / "inputs" / "areatrigger-world.json"

SPELL_EFFECT_CREATE_AREATRIGGER = 179
SPELL_EFFECT_CREATE_AREATRIGGER_2 = 353
SPELL_AURA_AREA_TRIGGER = 395
AT_EFFECTS = (SPELL_EFFECT_CREATE_AREATRIGGER, SPELL_EFFECT_CREATE_AREATRIGGER_2)

# DBCEnums.h:195 enum class AreaTriggerShapeType (world-DB `Shape` column)
SHAPE_TYPES = {0: "sphere", 1: "box", 2: "unk", 3: "polygon", 4: "cylinder", 5: "disk", 6: "bounded-plane"}
# AreaTrigger.h:42 AreaTriggerFieldFlags
FIELD_HEIGHT_IGNORES_SCALE = 0x0001
# AreaTriggerTemplate.h:60 AreaTriggerCreatePropertiesFlag
CREATE_FLAGS = {
    0x001: "HasAbsoluteOrientation", 0x002: "HasDynamicShape", 0x004: "HasAttached",
    0x008: "HasFaceMovementDir", 0x010: "HasFollowsTerrain", 0x020: "AlwaysExterior",
    0x040: "HasTargetRollPitchYaw", 0x080: "HasAnimId", 0x100: "VisualAnimIsDecay",
    0x200: "HasAnimKitId", 0x400: "HasCircularMovement", 0x800: "Unk5",
}
# DBCEnums.h:174 AreaTriggerActionSetFlag
ACTION_SET_FLAGS = {
    "OnlyTriggeredByCaster": 0x0001, "ResurrectIfConditionFails": 0x0002, "Obsolete": 0x0004,
    "AllowWhileGhost": 0x0008, "AllowWhileDead": 0x0010, "UnifyAllInstances": 0x0020,
    "SuppressConditionError": 0x0040, "NotTriggeredbyCaster": 0x0080, "CreatorsPartyOnly": 0x0100,
    "DontRunOnLeaveWhenExpiring": 0x0200, "CanAffectUninteractible": 0x0400,
    "DontDespawnWithCreator": 0x0800, "CanAffectBeastmaster": 0x1000, "RequiresLineOfSight": 0x2000,
}
# AreaTriggerTemplate.h:40/49
ACTION_TYPES = {0: "CAST", 1: "ADDAURA", 2: "TELEPORT", 3: "TAVERN"}
ACTION_USER_TYPES = {0: "ANY", 1: "FRIEND", 2: "ENEMY", 3: "RAID", 4: "PARTY", 5: "CASTER"}
DEATH_STATES = ("ALIVE", "JUST_DIED", "CORPSE", "DEAD", "JUST_RESPAWNED")
AT_PROFILE = "combat-sim"
MAP_SIZE = g.mul(g.f32(533.3333), 64.0)      # GridDefines.h:57 (SIZE_OF_GRIDS * MAX_NUMBER_OF_GRIDS)


# ---------------------------------------------------------------------------
# world-DB model (AreaTriggerDataStore.cpp:55-349)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Shape:
    """``AreaTriggerShapeInfo`` (AreaTriggerTemplate.h:96-199); fields per its raw-array constructors."""

    kind: str
    raw: tuple[float, ...]
    vertices: tuple[tuple[float, float], ...] = ()
    vertices_target: tuple[tuple[float, float], ...] = ()

    @property
    def params(self) -> dict[str, Any]:
        r = self.raw
        if self.kind == "sphere":
            return {"radius": r[0], "radius_target": r[1]}
        if self.kind == "box":
            return {"extents": r[0:3], "extents_target": r[3:6]}
        if self.kind == "polygon":
            return {"height": r[0], "height_target": r[1], "vertices": self.vertices,
                    "vertices_target": self.vertices_target}
        if self.kind == "cylinder":
            return {"radius": r[0], "radius_target": r[1], "height": r[2], "height_target": r[3],
                    "location_z_offset": r[4], "location_z_offset_target": r[5]}
        if self.kind == "disk":
            return {"inner_radius": r[0], "inner_radius_target": r[1], "outer_radius": r[2],
                    "outer_radius_target": r[3], "height": r[4], "height_target": r[5],
                    "location_z_offset": r[6], "location_z_offset_target": r[7]}
        if self.kind == "bounded-plane":
            return {"extents": r[0:2], "extents_target": r[2:4]}
        raise FailClosed(f"areatriggers: unknown shape {self.kind!r}")

    @property
    def is_dynamic(self) -> bool:
        """True when the searched extent depends on ``progress`` (start != target)."""
        p = self.params
        pairs = {
            "sphere": [("radius", "radius_target")],
            "box": [("extents", "extents_target")],
            "polygon": [("height", "height_target")],
            "cylinder": [("radius", "radius_target"), ("height", "height_target")],
            "disk": [("inner_radius", "inner_radius_target"), ("outer_radius", "outer_radius_target"),
                     ("height", "height_target")],
            "bounded-plane": [("extents", "extents_target")],
        }[self.kind]
        dyn = any(p[a] != p[b] for a, b in pairs)
        return dyn or (self.kind == "polygon" and bool(self.vertices_target))

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"kind": self.kind}
        for k, v in self.params.items():
            d[k] = [list(x) for x in v] if k.startswith("vertices") else (list(v) if isinstance(v, tuple) else v)
        return d


def shape_from_row(shape_type: int, raw: list[float], vertices=(), vertices_target=()) -> Shape:
    """Mirrors: AreaTriggerDataStore.cpp:196-289 (invalid shape skipped; polygon height fix-up)."""
    kind = SHAPE_TYPES.get(int(shape_type))
    if kind is None or kind == "unk":
        raise FailClosed(f"areatriggers: create properties with invalid shape {shape_type} are skipped at load")
    vals = tuple(g.f32(float(v)) for v in raw)
    if len(vals) != 8:
        raise FailClosed("areatriggers: shape needs 8 ShapeData values")
    verts = tuple((g.f32(x), g.f32(y)) for x, y in vertices)
    vt = tuple((g.f32(x), g.f32(y)) for x, y in vertices_target)
    if kind == "polygon":
        h, ht = vals[0], vals[1]
        if h <= 0.0:                       # AreaTriggerDataStore.cpp:260-265
            h = 1.0
            if ht <= 0.0:
                ht = 1.0
        vals = (h, ht) + vals[2:]
        if vt and len(vt) != len(verts):   # :270-275
            vt = ()
    return Shape(kind, vals, verts, vt)


def shape_from_dict(d: dict[str, Any]) -> Shape:
    """Synthetic fixture shape: ``{"type": <db shape int>, "data": [8 floats], "vertices": [[x,y],..]}``."""
    return shape_from_row(d["type"], d["data"], d.get("vertices", ()), d.get("vertices_target", ()))


def max_search_radius(shape: Shape) -> float:
    """Mirrors: AreaTriggerTemplate.cpp:22-68 ``AreaTriggerShapeInfo::GetMaxSearchRadius`` (float)."""
    p = shape.params
    if shape.kind == "sphere":
        return max(p["radius"], p["radius_target"])
    if shape.kind == "cylinder":
        return max(p["radius"], p["radius_target"])
    if shape.kind == "disk":
        return max(p["outer_radius"], p["outer_radius_target"])
    if shape.kind == "box":
        e, t = p["extents"], p["extents_target"]
        a = g.add(g.mul(e[0], e[0]), g.mul(e[1], e[1]))
        b = g.add(g.mul(t[0], t[0]), g.mul(t[1], t[1]))
        return g.sqrt(max(a, b))
    if shape.kind == "bounded-plane":
        e, t = p["extents"], p["extents_target"]
        a = g.add(g.div(g.mul(e[0], e[0]), 4.0), g.div(g.mul(e[1], e[1]), 4.0))
        b = g.add(g.div(g.mul(t[0], t[0]), 4.0), g.div(g.mul(t[1], t[1]), 4.0))
        return g.sqrt(max(a, b))
    if shape.kind == "polygon":
        r = 0.0
        for v in shape.vertices + shape.vertices_target:
            r = max(r, g.exact_dist2d((0.0, 0.0, 0.0), (v[0], v[1], 0.0)))
        return r
    raise FailClosed(f"areatriggers: unknown shape {shape.kind!r}")


@dataclass
class CreateProperties:
    id: int
    is_custom: bool
    template_id: int
    template: dict[str, Any] | None
    actions: list[dict[str, Any]]
    flags: int
    curves: dict[str, int]
    time_to_target_scale: int
    shape: Shape
    script_name: str
    movement: str                 # none | spline | orbit
    speed: float
    spline_points: int = 0
    conditions: list[dict[str, Any]] = field(default_factory=list)

    @property
    def action_set_flags(self) -> int:
        return int(self.template["ActionSetFlags"]) if self.template else 0

    def flag_names(self) -> list[str]:
        return [n for b, n in sorted(CREATE_FLAGS.items()) if self.flags & b] + \
               ([f"unknown:0x{self.flags & ~sum(CREATE_FLAGS):x}"] if self.flags & ~sum(CREATE_FLAGS) else [])


class ATWorld:
    """World-DB AreaTrigger tables: the Dummy-pass overlay + ``areatrigger-world.json``."""

    def __init__(self, overlay, extract: dict[str, Any] | None = None) -> None:
        if extract is None:
            extract = json.loads(AT_WORLD_EXTRACT.read_text(encoding="utf-8"))
        self.extract_provenance = extract["provenance"]

        def rows(tbl):
            t = extract["tables"][tbl]
            return [dict(zip(t["columns"], r)) for r in t["rows"]]

        self.cp_rows = {(int(r["Id"]), int(r["IsCustom"])): r for r in overlay.table("areatrigger_create_properties").dicts()}
        self.templates = {(int(r["Id"]), int(r["IsCustom"])): r for r in overlay.table("areatrigger_template").dicts()}
        self.actions: dict[tuple[int, int], list[dict[str, Any]]] = {}
        for r in overlay.table("areatrigger_template_actions").dicts():
            # AreaTriggerDataStore.cpp:64-104 (SELECT without ORDER BY: row order is the table's)
            if int(r["ActionType"]) >= 4 or int(r["TargetType"]) >= 6:
                continue
            self.actions.setdefault((int(r["AreaTriggerId"]), int(r["IsCustom"])), []).append(r)
        self.vertices: dict[tuple[int, int], list[dict[str, Any]]] = {}
        for r in sorted(rows("areatrigger_create_properties_polygon_vertex"),
                        key=lambda r: (r["AreaTriggerCreatePropertiesId"], r["IsCustom"], r["Idx"])):
            self.vertices.setdefault((int(r["AreaTriggerCreatePropertiesId"]), int(r["IsCustom"])), []).append(r)
        self.splines: dict[tuple[int, int], int] = {}
        for r in rows("areatrigger_create_properties_spline_point"):
            k = (int(r["AreaTriggerCreatePropertiesId"]), int(r["IsCustom"]))
            self.splines[k] = self.splines.get(k, 0) + 1
        self.orbits = {(int(r["AreaTriggerCreatePropertiesId"]), int(r["IsCustom"])): r
                       for r in rows("areatrigger_create_properties_orbit")}
        self.conditions: dict[tuple[int, int], list[dict[str, Any]]] = {}
        for r in rows("conditions"):
            if int(r["SourceTypeOrReferenceId"]) == 28:
                # ConditionMgr.cpp:1262 key {SourceGroup=AT id, SourceEntry=isServerSide, 0}
                self.conditions.setdefault((int(r["SourceGroup"]), int(r["SourceEntry"])), []).append(r)

    def create_properties(self, cp_id: int, is_custom: bool = False) -> CreateProperties | None:
        """Mirrors: AreaTriggerDataStore.cpp:171-296 (None = not loaded -> AreaTrigger::Create fails, :130)."""
        key = (cp_id, int(is_custom))
        r = self.cp_rows.get(key)
        if r is None:
            return None
        at_key = (int(r["AreaTriggerId"]), int(r["IsAreatriggerCustom"]))
        tpl = self.templates.get(at_key)
        if at_key[0] and tpl is None:          # :189-194
            return None
        try:
            v = self.vertices.get(key, [])
            verts = [(x["VerticeX"], x["VerticeY"]) for x in v]
            vt = [(x["VerticeTargetX"], x["VerticeTargetY"]) for x in v
                  if x["VerticeTargetX"] is not None and x["VerticeTargetY"] is not None]
            shape = shape_from_row(r["Shape"], [r[f"ShapeData{i}"] for i in range(8)], verts, vt)
        except FailClosed:
            return None                         # :196-201 invalid shape => row skipped
        movement = "orbit" if key in self.orbits else ("spline" if key in self.splines else "none")
        return CreateProperties(
            id=cp_id, is_custom=bool(is_custom), template_id=at_key[0], template=tpl,
            actions=list(self.actions.get(at_key, [])) if tpl else [],
            flags=int(r["Flags"]),
            curves={k: int(r[k] or 0) for k in ("MoveCurveId", "ScaleCurveId", "MorphCurveId", "FacingCurveId")},
            time_to_target_scale=int(r["TimeToTargetScale"] or 0), shape=shape,
            script_name=r["ScriptName"] or "", movement=movement, speed=float(r["Speed"] or 0),
            spline_points=self.splines.get(key, 0),
            conditions=list(self.conditions.get(at_key, [])) if tpl else [])


# ---------------------------------------------------------------------------
# scale / progress (AreaTrigger.cpp:502-552)
# ---------------------------------------------------------------------------
def lerp(a: float, b: float, f: float) -> float:
    """Mirrors: g3dmath.h:194 ``G3D::lerp(float, float, float)`` -- ``a + (b - a) * f``."""
    return g.add(g.f32(a), g.mul(g.sub(g.f32(b), g.f32(a)), g.f32(f)))


def calc_current_scale(override_scale: float | None, scale_curve_id: int, scale_curve_value: float | None,
                       extra_scale: float = 1.0) -> float:
    """Mirrors: AreaTrigger.cpp:502 ``CalcCurrentScale``.

    ``override_scale`` is the constant OverrideScaleCurve set at Create (:181-192, the
    SpellMod ``Radius`` *multiplier* only; flat mods are ignored) or None when inactive.
    ``scale_curve_value`` is the evaluated DB2 curve (not modelled -> must be stated).
    ``extra_scale`` is the constant ExtraScaleCurve (Create sets 1.0, :178).
    """
    floor = g.f32(0.000001)
    scale = 1.0
    if override_scale is not None:
        scale = g.mul(scale, max(g.f32(override_scale), floor))
    elif scale_curve_id:
        if scale_curve_value is None:
            raise FailClosed("areatriggers: ScaleCurveId set; curve evaluation not modelled (state `scale_curve_value`)")
        scale = g.mul(scale, max(g.f32(scale_curve_value), floor))
    return g.mul(scale, max(g.f32(extra_scale), floor))


def progress_for_shape(shape: Shape, morph_curve_id: int, stated: float | None) -> float:
    """``GetProgress`` (:515) optionally through MorphCurveId (:735). Needed only for dynamic shapes."""
    if not shape.is_dynamic:
        # lerp(a, a, f) == a for every finite f in [0, 1]; the value is irrelevant
        return 0.0 if stated is None else g.f32(stated)
    if stated is None:
        why = "MorphCurveId curve" if morph_curve_id else "elapsed/total duration"
        raise FailClosed(f"areatriggers: dynamic shape needs stated `progress` ({why} not modelled)")
    return g.f32(stated)


# ---------------------------------------------------------------------------
# shape containment (per unit)
# ---------------------------------------------------------------------------
def rotate_polygon_vertices(vertices, orientation: float) -> list[tuple[float, float]]:
    """Mirrors: AreaTrigger.cpp:1081-1090 (sinf/cosf of the AT orientation, float arithmetic)."""
    s = g.sin(g.f32(orientation))
    c = g.cos(g.f32(orientation))
    out = []
    for x, y in vertices:
        nx = g.sub(g.mul(x, c), g.mul(y, s))
        ny = g.add(g.mul(y, c), g.mul(x, s))
        out.append((nx, ny))
    return out


def is_in_polygon_2d(pos: g.Vec, origin: g.Vec, vertices) -> bool:
    """Mirrors: Position.cpp:104 ``Position::IsInPolygon2D`` (even-odd ray cast, float)."""
    tx, ty = pos[0], pos[1]
    inside = False
    n = len(vertices)
    for i in range(n):
        j = 0 if i == n - 1 else i + 1
        xi = g.add(origin[0], vertices[i][0])
        yi = g.add(origin[1], vertices[i][1])
        xj = g.add(origin[0], vertices[j][0])
        yj = g.add(origin[1], vertices[j][1])
        if (yi > ty) != (yj > ty):
            slope = g.div(g.sub(xj, xi), g.sub(yj, yi))
            on_line = g.add(g.mul(slope, g.sub(ty, yi)), xi)
            if tx < on_line:
                inside = not inside
    return inside


@dataclass(frozen=True)
class ShapeState:
    """Per-tick evaluated shape (``SearchUnitIn*`` locals)."""

    kind: str
    radius: float                   # SearchUnits radius
    check3d: bool
    extents: tuple[float, float, float] | None = None
    min_z: float | None = None
    max_z: float | None = None
    inner_radius: float | None = None
    polygon: tuple[tuple[float, float], ...] | None = None


def evaluate_shape(shape: Shape, at_pos: g.Vec, at_orientation: float, progress: float, scale: float,
                   bounds_radius_2d: float | None = None, field_flags: int = 0) -> ShapeState:
    """Mirrors: AreaTrigger.cpp:732-851 (the radius/height/extents part of each ``SearchUnitIn*``)."""
    p = shape.params
    z = at_pos[2]
    k = shape.kind
    if k == "sphere":
        r = g.mul(lerp(p["radius"], p["radius_target"], progress), scale)
        return ShapeState(k, r, True)
    if k == "box":
        e, t = p["extents"], p["extents_target"]
        ex, ey, ez = (g.mul(lerp(e[i], t[i], progress), scale) for i in range(3))
        r = g.sqrt(g.add(g.mul(ex, ex), g.mul(ey, ey)))
        return ShapeState(k, r, False, extents=(ex, ey, g.div(ez, 2.0)))
    if k == "bounded-plane":
        e, t = p["extents"], p["extents_target"]
        ex, ey = (g.mul(lerp(e[i], t[i], progress), scale) for i in range(2))
        r = g.sqrt(g.add(g.mul(ex, ex), g.mul(ey, ey)))
        return ShapeState(k, r, False, extents=(ex, ey, MAP_SIZE))
    if k == "cylinder":
        r = g.mul(lerp(p["radius"], p["radius_target"], progress), scale)
        h = lerp(p["height"], p["height_target"], progress)
        if not field_flags & FIELD_HEIGHT_IGNORES_SCALE:
            h = g.mul(h, scale)
        return ShapeState(k, r, False, min_z=g.sub(z, h), max_z=g.add(z, h))
    if k == "disk":
        inner = g.mul(lerp(p["inner_radius"], p["inner_radius_target"], progress), scale)
        outer = g.mul(lerp(p["outer_radius"], p["outer_radius_target"], progress), scale)
        h = lerp(p["height"], p["height_target"], progress)
        if not field_flags & FIELD_HEIGHT_IGNORES_SCALE:
            h = g.mul(h, scale)
        return ShapeState(k, outer, False, min_z=g.sub(z, h), max_z=g.add(z, h), inner_radius=inner)
    if k == "polygon":
        h = lerp(p["height"], p["height_target"], progress)
        verts = list(shape.vertices)
        if shape.vertices_target:        # :1065-1079
            verts = [(lerp(v[0], t[0], progress), lerp(v[1], t[1], progress))
                     for v, t in zip(verts, shape.vertices_target)]
        rotated = tuple(rotate_polygon_vertices(verts, at_orientation))
        if bounds_radius_2d is None:
            bounds_radius_2d = max_search_radius(shape)
        r = g.mul(g.f32(bounds_radius_2d), scale)          # GetMaxSearchRadius :1050
        return ShapeState(k, r, False, min_z=g.sub(z, h), max_z=g.add(z, h), polygon=rotated)
    raise FailClosed(f"areatriggers: unknown shape {k!r}")


def unit_in_shape(state: ShapeState, at_pos: g.Vec, at_orientation: float, unit_pos: g.Vec, unit_reach: float) -> bool:
    """Search check + per-shape erase predicate for one unit.

    Mirrors: AreaTrigger.cpp:717-719 (``AnyUnitInObjectRangeCheck`` -> Object.cpp:410
    ``_IsWithinDist``: AT combat reach 0 + unit combat reach, strict ``<``, 2D/3D) and the
    ``EraseIf`` predicates at :759-762 (box), :777-782 (polygon), :802-806 (cylinder),
    :827-830 (disk), :847-850 (bounded plane).
    """
    at_pos = g.vec(at_pos)
    unit_pos = g.vec(unit_pos)
    if not g.is_within_dist(at_pos, 0.0, unit_pos, unit_reach, state.radius, is3d=state.check3d):
        return False
    uz = unit_pos[2]
    if state.kind in ("box", "bounded-plane"):
        ex, ey, ez = state.extents
        return g.is_within_box(unit_pos, at_pos, g.f32(at_orientation), ex, ey, ez)
    if state.kind == "cylinder":
        return not (uz < state.min_z or uz > state.max_z)
    if state.kind == "disk":
        return not (g.is_in_dist2d(unit_pos, at_pos, state.inner_radius) or uz < state.min_z or uz > state.max_z)
    if state.kind == "polygon":
        return not (uz < state.min_z or uz > state.max_z or not is_in_polygon_2d(unit_pos, at_pos, state.polygon))
    return True


# ---------------------------------------------------------------------------
# fixture stages
# ---------------------------------------------------------------------------
def at_facts(world, at_id: str) -> dict[str, Any]:
    a = world.actor(at_id)
    if a.kind != "areatrigger":
        raise FailClosed(f"areatriggers: actor {at_id!r} is not an areatrigger")
    f = a.facts.get("areatrigger")
    if not isinstance(f, dict):
        raise FailClosed(f"areatriggers: actor {at_id!r} does not state `facts.areatrigger`")
    return f


def _need(d: dict[str, Any], key: str, who: str) -> Any:
    if key not in d:
        raise FailClosed(f"areatriggers: {who} does not state {key!r}")
    return d[key]


def resolve_properties(world, at_id: str, at_world: ATWorld | None = None) -> CreateProperties:
    """The AT's create properties: inline (synthetic) or from the world DB by id."""
    f = at_facts(world, at_id)
    if "shape" in f:
        tpl = f.get("template")
        return CreateProperties(
            id=int(f.get("create_properties", 0)), is_custom=bool(f.get("is_custom", False)),
            template_id=int(tpl["Id"]) if tpl else 0, template=tpl, actions=list(f.get("actions", [])),
            flags=int(f.get("flags", 0)),
            curves={k: int(f.get("curves", {}).get(k, 0)) for k in ("MoveCurveId", "ScaleCurveId", "MorphCurveId", "FacingCurveId")},
            time_to_target_scale=int(f.get("time_to_target_scale", 0)), shape=shape_from_dict(f["shape"]),
            script_name=f.get("script_name", ""), movement=f.get("movement", "none"), speed=1.0,
            conditions=list(f.get("conditions", [])))
    cp_id = _need(f, "create_properties", at_id)
    if at_world is None:
        from . import context
        at_world = _at_world(context.get())
    cp = at_world.create_properties(int(cp_id), bool(f.get("is_custom", False)))
    if cp is None:
        raise FailClosed(f"areatriggers: create properties {cp_id} not loaded -> AreaTrigger::Create fails "
                         "(AreaTrigger.cpp:130); there is no areatrigger to select recipients")
    return cp


def _in_phase(world, uid: str) -> bool:
    a = world.actor(uid)
    if "in_areatrigger_phase" in a.facts:
        return bool(a.facts["in_areatrigger_phase"])
    if a.facts.get("profile") == AT_PROFILE:
        return True
    raise FailClosed(f"areatriggers: actor {uid!r} does not state `in_areatrigger_phase` "
                     "(UnitListSearcher uses the AT phase shift inherited from the caster, AreaTrigger.cpp:258)")


def search_units(world, at_id: str, cp: CreateProperties, trace: Trace) -> list[str]:
    """Mirrors: AreaTrigger.cpp:645-659 + 717-851 (shape search; enumeration = ``world.visit_order``)."""
    at = world.actor(at_id)
    f = at_facts(world, at_id)
    if f.get("is_static"):
        raise FailClosed("areatriggers: static spawns (PlayerListSearcher, no caster) are out of scope")
    moving = cp.movement != "none" or cp.curves["MoveCurveId"] or cp.curves["FacingCurveId"]
    if moving and not f.get("tick_pose_stated"):
        raise FailClosed("areatriggers: spline/orbit/move-or-facing-curve AT -- the tick's pos/orientation "
                         "must be stated (`tick_pose_stated: true`); movement is not modelled")
    pos = g.vec(at.need("pos"))
    orientation = g.f32(at.need("orientation"))
    override = _need(f, "override_scale", at_id)            # None = inactive
    scale = calc_current_scale(override, cp.curves["ScaleCurveId"], f.get("scale_curve_value"),
                               f.get("extra_scale", 1.0))
    progress = progress_for_shape(cp.shape, cp.curves["MorphCurveId"], f.get("progress"))
    state = evaluate_shape(cp.shape, pos, orientation, progress, scale,
                           field_flags=int(f.get("field_flags", 0)))
    from . import relations
    out = []
    for uid in world.enumeration():
        if uid == at_id or not relations.is_unit(world, uid):
            continue                     # UnitSearcherBase: creature + player grid containers only
        if not _in_phase(world, uid):
            continue
        u = world.actor(uid)
        if unit_in_shape(state, pos, orientation, u.need("pos"), u.need("combat_reach")):
            out.append(uid)
    trace.add("areatriggers.search_units", "AreaTrigger.cpp:717", output=out,
              inputs={"shape": cp.shape.kind, "radius": state.radius, "check3d": state.check3d,
                      "scale": scale, "progress": progress},
              notes=["reqAlive=false: dead units are candidates (GridNotifiers.h:1322)"])
    return out


def _death_state(world, uid: str) -> str:
    a = world.actor(uid)
    if "death_state" in a.facts:
        ds = a.facts["death_state"]
        if ds not in DEATH_STATES:
            raise FailClosed(f"areatriggers: unknown death_state {ds!r}")
        return ds
    if a.need("alive"):
        return "ALIVE"
    raise FailClosed(f"areatriggers: dead player {uid!r} must state `death_state` (CORPSE vs DEAD)")


def filter_targets(world, at_id: str, cp: CreateProperties, targets: list[str], trace: Trace) -> list[str]:
    """Mirrors: AreaTrigger.cpp:661-711 (only when the AT has a template)."""
    if cp.template is None:
        trace.add("areatriggers.filter_targets", "AreaTrigger.cpp:661", output=list(targets),
                  notes=["no areatrigger_template: no action-set / death-state / uninteractible / condition filter"])
        return list(targets)
    if cp.conditions:
        raise FailClosed("areatriggers: CONDITION_SOURCE_TYPE_AREATRIGGER conditions are not modelled")
    flags = cp.action_set_flags
    caster = _need(at_facts(world, at_id), "caster", at_id)     # None = caster gone
    from . import groups, relations
    kept = []
    notes = []
    for uid in targets:
        if caster is not None and uid == caster:
            if flags & ACTION_SET_FLAGS["NotTriggeredbyCaster"]:
                notes.append(f"{uid}: NotTriggeredbyCaster")
                continue
        else:
            if flags & ACTION_SET_FLAGS["OnlyTriggeredByCaster"]:
                notes.append(f"{uid}: OnlyTriggeredByCaster")
                continue
            if flags & ACTION_SET_FLAGS["CreatorsPartyOnly"] and (
                    caster is None or not groups.is_in_raid_with(world, caster, uid, trace)):
                notes.append(f"{uid}: CreatorsPartyOnly (caster->IsInRaidWith)")
                continue
        if relations.is_player(world, uid):
            ds = _death_state(world, uid)
            if ds == "DEAD" and not flags & ACTION_SET_FLAGS["AllowWhileGhost"]:
                notes.append(f"{uid}: DEAD without AllowWhileGhost")
                continue
            if ds == "CORPSE" and not flags & ACTION_SET_FLAGS["AllowWhileDead"]:
                notes.append(f"{uid}: CORPSE without AllowWhileDead")
                continue
        if not flags & ACTION_SET_FLAGS["CanAffectUninteractible"] and relations.has_unit_flag(world, uid, "UNINTERACTIBLE"):
            notes.append(f"{uid}: uninteractible")
            continue
        kept.append(uid)
    trace.add("areatriggers.filter_targets", "AreaTrigger.cpp:661", output=kept, notes=notes)
    return kept


def handle_enter_exit(previous_inside, new_list: list[str], trace: Trace) -> dict[str, Any]:
    """Mirrors: AreaTrigger.cpp:853-879.

    ``entering`` keeps ``new_list`` order; ``exiting`` iterates a ``GuidUnorderedSet``
    (hash order, unspecified) -- returned sorted and flagged ``exit_order_specified=False``.
    """
    exit_units = set(previous_inside)
    entering = []
    inside: list[str] = []
    for uid in new_list:
        if uid in exit_units:
            exit_units.discard(uid)
        elif uid not in inside:
            entering.append(uid)
        if uid not in inside:
            inside.append(uid)
    out = {"entering": entering, "inside": sorted(inside), "exiting": sorted(exit_units),
           "exit_order_specified": len(exit_units) <= 1}
    trace.add("areatriggers.handle_enter_exit", "AreaTrigger.cpp:853", output=out,
              notes=["enter hooks run for all entering units before any exit hook (:868-873)"])
    return out


def unit_fit_to_action_requirement(world, unit: str, caster: str, action: dict[str, Any], trace: Trace,
                                   spell_view=None) -> bool:
    """Mirrors: AreaTrigger.cpp:1108 ``UnitFitToActionRequirement``."""
    from . import groups, relations
    tt = ACTION_USER_TYPES.get(int(action["TargetType"]))
    if tt == "FRIEND":
        return relations.valid_assist(world, caster, unit, spell_view, trace)
    if tt == "ENEMY":
        return relations.valid_attack(world, caster, unit, spell_view, trace)
    if tt == "RAID":
        return groups.is_in_raid_with(world, caster, unit, trace)
    if tt == "PARTY":
        return groups.is_in_party_with(world, caster, unit, trace)
    if tt == "CASTER":
        return unit == caster
    return True


def do_actions(world, at_id: str, cp: CreateProperties, unit: str, trace: Trace, spell_view_for=None) -> list[dict[str, Any]]:
    """Mirrors: AreaTrigger.cpp:1140 ``DoActions`` (non-static AT: caster = GetCaster())."""
    caster = _need(at_facts(world, at_id), "caster", at_id)
    out: list[dict[str, Any]] = []
    if caster is None or cp.template is None:
        return out
    for action in cp.actions:
        param = int(action["ActionParam"])
        view = spell_view_for(param) if spell_view_for else None
        if not unit_fit_to_action_requirement(world, unit, caster, action, trace, view):
            continue
        kind = ACTION_TYPES.get(int(action["ActionType"]))
        if kind == "CAST":
            out.append({"action": "cast", "caster": caster, "spell": param, "explicit_unit": unit,
                        "triggered": "TRIGGERED_FULL_MASK", "reselects": "child implicit selectors"})
        elif kind == "ADDAURA":
            out.append({"action": "add-aura", "caster": caster, "spell": param, "target": unit,
                        "reselects": "no (Unit::AddAura bypasses target selection)"})
        else:
            out.append({"action": kind.lower() if kind else "unknown", "param": param})
    trace.add("areatriggers.do_actions", "AreaTrigger.cpp:1140", output=out, inputs={"unit": unit})
    return out


def evaluate_tick(world, at_id: str, trace: Trace | None = None, at_world: ATWorld | None = None,
                  spell_view_for=None) -> dict[str, Any]:
    """One ``UpdateTargetList`` + enter/exit + actions; the AI script part is described, not executed.

    Mirrors: AreaTrigger.cpp:361-400 (``Update``: expiry before target update) and :641-714.
    """
    trace = trace if trace is not None else Trace()
    cp = resolve_properties(world, at_id, at_world)
    f = at_facts(world, at_id)
    if f.get("expired"):
        trace.add("areatriggers.update", "AreaTrigger.cpp:388-397", output=None,
                  notes=["duration elapsed: Remove() before UpdateTargetList; exit hooks run with ByExpire"])
        return {"expired": True, "exiting": sorted(f.get("inside", [])), "exit_order_specified": len(f.get("inside", [])) <= 1}
    found = search_units(world, at_id, cp, trace)
    kept = filter_targets(world, at_id, cp, found, trace)
    ee = handle_enter_exit(_need(f, "inside", at_id), kept, trace)
    actions = {u: do_actions(world, at_id, cp, u, trace, spell_view_for) for u in ee["entering"]}
    script = AT_SCRIPTS.get(cp.script_name) if cp.script_name else None
    if cp.script_name and script is None:
        raise FailClosed(f"areatriggers: AreaTriggerAI {cp.script_name!r} not in the verified script table")
    undo = {u: [a["ActionParam"] for a in cp.actions if int(a["ActionType"]) in (0, 1)] for u in ee["exiting"]}
    return {"candidates": found, "targets": kept, **ee, "actions": actions, "undo_actions": undo,
            "script": script}


# ---------------------------------------------------------------------------
# verified script consumers (script-consumer: bodies read at the pin)
# ---------------------------------------------------------------------------
# uses: how the AI consumes the AT's recipient list
#   inside-units   -- OnUnitEnter/OnUnitExit (or GetInsideUnits) act on each selected unit
#   position-only  -- the AI casts a child at at->GetPosition(); the AT's own recipients are unused
#                     and the CHILD's implicit selectors choose recipients
AT_SCRIPTS: dict[str, dict[str, Any]] = {
    "areatrigger_pal_consecration": {
        "file_line": "src/server/scripts/Spells/spell_paladin.cpp:445", "uses": "inside-units",
        "enter": [{"cast": 204242, "on": "unit", "if": "caster->IsValidAttackTarget(unit)"},
                  {"cast": 188370, "on": "caster", "if": "unit == caster && Protection spec"}],
        "exit": [{"remove_aura": 204242}, {"remove_aura": 188370, "if": "unit == caster"}],
        "note": "damage 81297 is cast at the AT position by the 26573 periodic aura (spell_paladin.cpp:431-435)"},
    "areatrigger_sha_earthquake": {
        "file_line": "src/server/scripts/Spells/spell_shaman.cpp:925", "uses": "position-only",
        "update": [{"cast": 77478, "at": "at position", "period": "aura 61882 EFFECT_1 period (default 1s)",
                    "original_caster": "areatrigger"}],
        "note": "77478 OnHit: knockdown 77505 once per unit per AT (_stunnedUnits), roll_chance"},
    "at_hun_binding_shot": {
        "file_line": "src/server/scripts/Spells/spell_hunter.cpp:248", "uses": "inside-units",
        "enter": [{"cast": 117405, "on": "unit", "if": "IsValidAttackTarget && !HasAura(117553, caster)"},
                  {"cast": 117614, "by": "unit", "at": "at position"}],
        "update": [{"every": "1s", "iterate": "GetInsideUnits (unordered)", "cast": 117614, "by": "unit with 117405"}],
        "note": "OnInitialize sets duration 0 on the caster's older Binding Shot ATs"},
    "areatrigger_pri_halo": {
        "file_line": "src/server/scripts/Spells/spell_priest.cpp:2351", "uses": "inside-units",
        "enter": [{"cast": "120696 (holy) / 390964 (shadow)", "on": "unit", "if": "IsValidAttackTarget"},
                  {"cast": "120692 (holy) / 390971 (shadow)", "on": "unit", "if": "else IsValidAssistTarget"}],
        "note": "expanding sphere (MorphCurveId): each unit is entered once while it stays inside"},
    "areatrigger_hun_tar_trap_activate": {
        "file_line": "src/server/scripts/Spells/spell_hunter.cpp:1340", "uses": "inside-units(trigger)",
        "enter": [{"cast": 187700, "at": "at position", "if": "IsValidAttackTarget(unit)", "then": "at->Remove()"}],
        "defect": "TG-I-D02"},
    "areatrigger_mage_blizzard": {
        "file_line": "src/server/scripts/Spells/spell_mage.cpp:344", "uses": "position-only",
        "update": [{"cast": 190357, "at": "at position", "period": "1s"}]},
    "areatrigger_dh_darkness": {
        "file_line": "src/server/scripts/Spells/spell_dh.cpp:865", "uses": "inside-units",
        "enter": [{"cast": 209426, "on": "unit", "if": "IsValidAssistTarget(unit, 209426)",
                   "duration": "at->GetDuration()"}],
        "exit": [{"remove_aura": 209426}]},
    "at_monk_song_of_chi_ji": {
        "file_line": "src/server/scripts/Spells/spell_monk.cpp:532", "uses": "inside-units",
        "enter": [{"cast": 198909, "on": "unit", "if": "IsValidAttackTarget"}],
        "note": "AT moves along a PathGenerator path (OnInitialize InitSplines) -> position is world-geometry dependent"},
    "at_dru_lunar_beam": {
        "file_line": "src/server/scripts/Spells/spell_druid.cpp:1517", "uses": "position-only",
        "update": [{"cast": 204069, "on": "caster", "period": "500ms then 1s"},
                   {"cast": 414613, "at": "at position", "period": "500ms then 1s"}]},
    "at_evo_emerald_blossom": {
        "file_line": "src/server/scripts/Spells/spell_evoker.cpp:289", "uses": "position-only",
        "remove": [{"cast": 355916, "at": "at position"}]},
    "at_evo_firestorm": {
        "file_line": "src/server/scripts/Spells/spell_evoker.cpp:421", "uses": "position-only",
        "update": [{"cast": 369374, "at": "at position", "period": "2s * ModCastingSpeed (first at 0ms)"}]},
}
for _name, _spells in {
    "areatrigger_dh_sigil_of_silence": [204490], "areatrigger_dh_sigil_of_chains": [204834, 208673],
    "areatrigger_dh_sigil_of_flame": [204598, 208710], "areatrigger_dh_sigil_of_misery": [207685],
    "areatrigger_dh_sigil_of_spite": [389860],
}.items():
    AT_SCRIPTS[_name] = {"file_line": "src/server/scripts/Spells/spell_dh.cpp:2484", "uses": "position-only",
                         "remove": [{"cast": s, "at": "at position"} for s in _spells],
                         "note": "areatrigger_dh_generic_sigil<...> template alias (spell_dh.cpp:2500-2504)"}

#: scripts outside the AT's AI that consume an AT of the given creating spell
EXTERNAL_CONSUMERS: dict[int, dict[str, Any]] = {
    5740: {"file_line": "src/server/scripts/Spells/spell_warlock.cpp:954", "uses": "inside-units",
           "tick": [{"cast": 42223, "on": "each inside unit of every caster AT with SpellID 5740",
                     "if": "!IsFriendlyTo", "order": "GuidUnorderedSet (unspecified)"}]},
    26573: {"file_line": "src/server/scripts/Spells/spell_paladin.cpp:431", "uses": "position-only",
            "tick": [{"cast": 81297, "at": "GetAreaTrigger(26573) position"}]},
    145205: {"file_line": "src/server/scripts/Spells/spell_druid.cpp:728", "uses": "none",
             "note": "heal 81269 is cast by the summoned creature's 81262 aura (spell_druid.cpp:751); the AT is not read"},
    267211: {"file_line": "src/server/scripts/Spells/spell_warlock.cpp:261", "uses": "position-only",
             "note": "at_warl_bilescourge_bombers (another AT) reads this AT's position as the bombing target"},
    109248: {"file_line": "src/server/scripts/Spells/spell_hunter.cpp:255", "uses": "lifecycle",
             "note": "older ATs of the caster get duration 0"},
    187699: {"file_line": "src/server/scripts/Spells/spell_hunter.cpp:1347", "uses": "lifecycle",
             "note": "older ATs of the caster get duration 0"},
    61882: {"file_line": "src/server/scripts/Spells/spell_shaman.cpp:1021", "uses": "ai-state",
            "note": "77478 OnHit finds the AT by OriginalCaster among GetAreaTriggers(61882)"},
}


@cache
def _at_world_cached(ctx_id: int) -> ATWorld:  # pragma: no cover - keyed by context identity
    from . import context
    return ATWorld(context.get().bundle.world)


def _at_world(ctx) -> ATWorld:
    return _at_world_cached(id(ctx))


__all__ = [
    "AT_SCRIPTS",
    "EXTERNAL_CONSUMERS",
    "ATWorld",
    "CreateProperties",
    "Shape",
    "ShapeState",
    "calc_current_scale",
    "do_actions",
    "evaluate_shape",
    "evaluate_tick",
    "filter_targets",
    "handle_enter_exit",
    "is_in_polygon_2d",
    "lerp",
    "max_search_radius",
    "rotate_polygon_vertices",
    "search_units",
    "shape_from_row",
    "unit_fit_to_action_requirement",
    "unit_in_shape",
]
