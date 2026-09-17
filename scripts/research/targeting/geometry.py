"""Pure geometry predicates with binary32 (``float``) semantics.

Everything here reproduces the pinned consumer's ``float`` arithmetic
(``Position`` / ``WorldObject`` in ``Entities/Object``) -- no clean-up of
``<`` vs ``<=``, squared distances, combat-reach addition, or the order in
which terms are summed.  Values are Python floats that always hold an exact
binary32 value (:func:`f32`).

Arithmetic model
----------------
* ``+ - *`` of two binary32 values are exact in binary64, so rounding the
  double result once (:func:`f32`) is the correctly rounded binary32 result
  (x86-64 SSE, ``-ffp-contract=off``: no FMA contraction).
* ``/`` is rounded exactly from the rational quotient (:func:`div`).
* ``std::fmod`` is exact; ``std::sqrt`` is correctly rounded.
* ``std::sin/cos/atan2(float)`` call glibc ``sinf/cosf/atan2f``.  When libm is
  loadable via ``ctypes`` those exact functions are called (``LIBM = True``);
  otherwise the binary64 function rounded to binary32 is used and
  ``LIBM = False`` (may differ by 1 ulp; the differential probe measures it).

World-geometry dependencies (height, VMAP/MMAP collision, LOS) are *not*
modelled: :func:`move_position_2d` returns the pre-collision candidate and the
height/collision step is a fixture fact.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import math
import struct
from fractions import Fraction

from . import FailClosed

# ---------------------------------------------------------------------------
# binary32 primitives
# ---------------------------------------------------------------------------
_PACK = struct.Struct("<f")


def f32(x: float) -> float:
    """Round a binary64 value to the nearest binary32 (ties-to-even)."""
    return _PACK.unpack(_PACK.pack(x))[0]


def _round_fraction_f32(q: Fraction) -> float:
    """Correctly rounded (ties-to-even) binary32 of an exact rational."""
    if q == 0:
        return 0.0
    sign = -1.0 if q < 0 else 1.0
    q = abs(q)
    e = q.numerator.bit_length() - q.denominator.bit_length()
    if q < Fraction(2) ** e:
        e -= 1
    # q in [2^e, 2^(e+1)); keep 24 significant bits (fixed scale below 2^-126: subnormals)
    shift = 23 - max(e, -126)
    scaled = q * Fraction(2) ** shift
    m, rem = divmod(scaled.numerator, scaled.denominator)
    if 2 * rem > scaled.denominator or (2 * rem == scaled.denominator and m & 1):
        m += 1
    return sign * f32(math.ldexp(float(m), -shift))


def add(a: float, b: float) -> float:
    return f32(a + b)


def sub(a: float, b: float) -> float:
    return f32(a - b)


def mul(a: float, b: float) -> float:
    return f32(a * b)


def div(a: float, b: float) -> float:
    if b == 0.0:
        if a == 0.0 or math.isnan(a):
            return math.nan
        return math.copysign(math.inf, a) * math.copysign(1.0, b)
    return _round_fraction_f32(Fraction(a) / Fraction(b))


def fmod(a: float, b: float) -> float:
    return f32(math.fmod(a, b))  # exact


def _load_libm():
    name = ctypes.util.find_library("m")
    if not name:
        return None
    try:
        lib = ctypes.CDLL(name)
        fns = {}
        for fn, argc in (("sinf", 1), ("cosf", 1), ("atan2f", 2), ("sqrtf", 1), ("tanf", 1)):
            f = getattr(lib, fn)
            f.restype = ctypes.c_float
            f.argtypes = [ctypes.c_float] * argc
            fns[fn] = f
        return fns
    except (OSError, AttributeError):
        return None


_LIBM = _load_libm()
LIBM = _LIBM is not None


def sin(x: float) -> float:
    return _LIBM["sinf"](x) if _LIBM else f32(math.sin(x))


def cos(x: float) -> float:
    return _LIBM["cosf"](x) if _LIBM else f32(math.cos(x))


def atan2(y: float, x: float) -> float:
    return _LIBM["atan2f"](y, x) if _LIBM else f32(math.atan2(y, x))


def sqrt(x: float) -> float:
    return _LIBM["sqrtf"](x) if _LIBM else f32(math.sqrt(x))


# ---------------------------------------------------------------------------
# constants
# ---------------------------------------------------------------------------
M_PI_F = f32(math.pi)                         # static_cast<float>(M_PI)
TWO_PI_F = mul(2.0, M_PI_F)                   # 2.0f * static_cast<float>(M_PI)
DEG_TO_RAD_FACTOR = div(mul(2.0, M_PI_F), 360.0)   # Util.cpp:882 (2.f * float(M_PI) / 360.f)
MIN_MELEE_REACH = 2.0                         # ObjectDefines.h:43
EXTRA_CELL_SEARCH_RADIUS = 40.0               # ObjectDefines.h:47
TRAJECTORY_MISSILE_SIZE = 3.0                 # Spell.h:86
DEFAULT_PLAYER_BOUNDING_RADIUS = f32(0.388999998569489)  # ObjectDefines.h:39
DEFAULT_PLAYER_COMBAT_REACH = 1.5             # ObjectDefines.h:40
FUZZY_EPSILON64 = 0.0000005                   # g3dmath.h:132

Vec = tuple[float, float, float]


def vec(p) -> Vec:
    if p is None or len(p) != 3:
        raise FailClosed(f"geometry: position must be [x, y, z], got {p!r}")
    return (f32(p[0]), f32(p[1]), f32(p[2]))


def deg_to_rad(degrees: float) -> float:
    """Mirrors: Util.cpp:880 ``DegToRad`` -- ``degrees * (2.f * float(M_PI) / 360.f)``."""
    return mul(f32(degrees), DEG_TO_RAD_FACTOR)


def fuzzy_eps64(a: float) -> float:
    """Mirrors: g3dmath.h:825 ``eps(double, double)``."""
    aa = abs(a) + 1.0
    return FUZZY_EPSILON64 if aa == math.inf else FUZZY_EPSILON64 * aa


def fuzzy_ge(a: float, b: float) -> bool:
    """Mirrors: g3dmath.h:870 ``fuzzyGe(double, double)`` (float args promote to double)."""
    return a > b - fuzzy_eps64(a)


def fuzzy_eq(a: float, b: float) -> bool:
    """Mirrors: g3dmath.h:858 ``fuzzyEq(double, double)``."""
    return a == b or abs(a - b) <= fuzzy_eps64(a)


# ---------------------------------------------------------------------------
# Position (Position.h / Position.cpp)
# ---------------------------------------------------------------------------
def normalize_orientation(o: float) -> float:
    """Mirrors: Position.cpp:207 ``Position::NormalizeOrientation``.

    Nominally [0, 2pi); a tiny negative input yields exactly ``2*float(pi)``
    (``-fmod(-o) + 2pi`` rounds), so the result range is [0, 2pi].
    """
    o = f32(o)
    if o < 0:
        return add(-fmod(-o, TWO_PI_F), TWO_PI_F)
    return fmod(o, TWO_PI_F)


def exact_dist2d_sq(this: Vec, x: float, y: float) -> float:
    """Mirrors: Position.h:108 ``GetExactDist2dSq`` (dx = x - this.x; dx*dx + dy*dy)."""
    dx = sub(x, this[0])
    dy = sub(y, this[1])
    return add(mul(dx, dx), mul(dy, dy))


def exact_dist_sq(this: Vec, other: Vec) -> float:
    """Mirrors: Position.h:121 ``GetExactDistSq`` ((dx*dx + dy*dy) + dz*dz)."""
    dz = sub(other[2], this[2])
    return add(exact_dist2d_sq(this, other[0], other[1]), mul(dz, dz))


def exact_dist2d(this: Vec, other: Vec) -> float:
    """Mirrors: Position.h:117 ``GetExactDist2d`` (std::sqrt of the float square)."""
    return sqrt(exact_dist2d_sq(this, other[0], other[1]))


def exact_dist(this: Vec, other: Vec) -> float:
    """Mirrors: Position.h:129 ``GetExactDist``."""
    return sqrt(exact_dist_sq(this, other))


def absolute_angle(this: Vec, x: float, y: float) -> float:
    """Mirrors: Position.h:136 ``GetAbsoluteAngle`` -- NormalizeOrientation(atan2(dy, dx))."""
    dx = sub(x, this[0])
    dy = sub(y, this[1])
    return normalize_orientation(atan2(dy, dx))


def to_relative_angle(orientation: float, abs_angle: float) -> float:
    """Mirrors: Position.h:146 ``ToRelativeAngle`` -- NormalizeOrientation(abs - m_orientation)."""
    return normalize_orientation(sub(abs_angle, orientation))


def relative_angle(this: Vec, orientation: float, other: Vec) -> float:
    """Mirrors: Position.h:148 ``GetRelativeAngle``."""
    return to_relative_angle(orientation, absolute_angle(this, other[0], other[1]))


def is_in_dist2d(this: Vec, other: Vec, dist: float) -> bool:
    """Mirrors: Position.h:152 ``IsInDist2d`` -- strict ``<`` on squares."""
    d = f32(dist)
    return exact_dist2d_sq(this, other[0], other[1]) < mul(d, d)


def is_in_dist(this: Vec, other: Vec, dist: float) -> bool:
    """Mirrors: Position.h:156 ``IsInDist`` -- strict ``<`` on 3D squares."""
    d = f32(dist)
    return exact_dist_sq(this, other) < mul(d, d)


def has_in_arc(this: Vec, orientation: float, arc: float, other: Vec, border: float = 2.0,
               same_object: bool = False) -> bool:
    """Mirrors: Position.cpp:173 ``Position::HasInArc``.

    ``arc`` is the *full* angle in radians; after ``NormalizeOrientation`` it is
    in [0, 2pi) (so a full 2pi arc normalises to 0).  Accepts relative angles in
    ``[-arc/border, +arc/border]`` inclusive.  ``same_object`` is the pointer
    identity shortcut (``obj == this``).
    """
    if same_object:
        return True
    arc = normalize_orientation(arc)
    angle = relative_angle(this, orientation, other)
    if angle > M_PI_F:
        angle = sub(angle, mul(2.0, M_PI_F))
    lborder = mul(-1.0, div(arc, f32(border)))
    rborder = div(arc, f32(border))
    return lborder <= angle <= rborder


def has_in_line(this: Vec, orientation: float, other: Vec, obj_size: float, width: float) -> bool:
    """Mirrors: Position.cpp:192 ``Position::HasInLine``.

    Front half-plane (``HasInArc(pi)``) and ``|sin(rel)| * dist2d < width + objSize``
    (strict).  No length bound.
    """
    if not has_in_arc(this, orientation, M_PI_F, other, 2.0):
        return False
    width = add(f32(width), f32(obj_size))
    angle = relative_angle(this, orientation, other)
    return mul(abs(sin(angle)), exact_dist2d(this, other)) < width


def is_within_box(this: Vec, origin: Vec, origin_o: float, length: float, width: float, height: float) -> bool:
    """Mirrors: Position.cpp:68 ``Position::IsWithinBox`` (double rotation, float deltas)."""
    rotation = 2 * math.pi - origin_o
    sin_v, cos_v = math.sin(rotation), math.cos(rotation)
    bdx = sub(this[0], origin[0])
    bdy = sub(this[1], origin[1])
    rot_x = f32(origin[0] + bdx * cos_v - bdy * sin_v)
    rot_y = f32(origin[1] + bdy * cos_v + bdx * sin_v)
    dz = sub(this[2], origin[2])
    dx = sub(rot_x, origin[0])
    dy = sub(rot_y, origin[1])
    return not (abs(dx) > length or abs(dy) > width or abs(dz) > height)


def is_within_vertical_cylinder(this: Vec, origin: Vec, radius: float, height: float,
                                double_vertical: bool = False) -> bool:
    """Mirrors: Position.cpp:95 ``Position::IsWithinVerticalCylinder``."""
    dz = sub(this[2], origin[2])
    ok_z = abs(dz) <= height if double_vertical else 0 <= dz <= height
    return ok_z and is_in_dist2d(this, origin, radius)


# ---------------------------------------------------------------------------
# WorldObject (Object.cpp) -- combat reach is an explicit argument
# ---------------------------------------------------------------------------
def distance(this: Vec, this_reach: float, other: Vec, other_reach: float | None = None) -> float:
    """Mirrors: Object.cpp:432/438 ``WorldObject::GetDistance``.

    ``other_reach=None`` is the ``Position`` overload (only this object's reach
    is subtracted); otherwise the ``WorldObject`` overload.  Clamped at 0.
    """
    d = sub(exact_dist(this, other), f32(this_reach))
    if other_reach is not None:
        d = sub(d, f32(other_reach))
    return d if d > 0.0 else 0.0


def distance_order(ref: Vec, a: Vec, b: Vec, is3d: bool = True) -> bool:
    """Mirrors: Object.cpp:569 ``WorldObject::GetDistanceOrder`` -- ``distsq(a) < distsq(b)``."""
    def dsq(o: Vec) -> float:
        dx = sub(ref[0], o[0])
        dy = sub(ref[1], o[1])
        s = add(mul(dx, dx), mul(dy, dy))
        if is3d:
            dz = sub(ref[2], o[2])
            s = add(s, mul(dz, dz))
        return s
    return dsq(a) < dsq(b)


def _range_core(distsq: float, size: float, min_range: float, max_range: float) -> bool:
    if min_range > 0.0:
        mindist = add(f32(min_range), size)
        if distsq < mul(mindist, mindist):
            return False
    maxdist = add(f32(max_range), size)
    return distsq < mul(maxdist, maxdist)


def is_in_range_obj(this: Vec, this_reach: float, other: Vec, other_reach: float,
                    min_range: float, max_range: float, is3d: bool = True) -> bool:
    """Mirrors: Object.cpp:592 ``WorldObject::IsInRange(WorldObject const*, ...)`` (both reaches)."""
    dx = sub(this[0], other[0])
    dy = sub(this[1], other[1])
    distsq = add(mul(dx, dx), mul(dy, dy))
    if is3d:
        dz = sub(this[2], other[2])
        distsq = add(distsq, mul(dz, dz))
    return _range_core(distsq, add(f32(this_reach), f32(other_reach)), min_range, max_range)


def is_in_range2d(this: Vec, this_reach: float, pos: Vec, min_range: float, max_range: float) -> bool:
    """Mirrors: Object.cpp:617 ``WorldObject::IsInRange2d`` -- this object's reach added, strict ``<``."""
    return _range_core(exact_dist2d_sq(this, pos[0], pos[1]), f32(this_reach), min_range, max_range)


def is_in_range3d(this: Vec, this_reach: float, pos: Vec, min_range: float, max_range: float) -> bool:
    """Mirrors: Object.cpp:635 ``WorldObject::IsInRange3d``."""
    return _range_core(exact_dist_sq(this, pos), f32(this_reach), min_range, max_range)


def is_within_dist3d(this: Vec, this_reach: float, pos: Vec, dist: float) -> bool:
    """Mirrors: Object.cpp:481 ``WorldObject::IsWithinDist3d(Position const*, float)``."""
    return is_in_dist(this, pos, add(f32(dist), f32(this_reach)))


def is_within_dist2d(this: Vec, this_reach: float, pos: Vec, dist: float) -> bool:
    """Mirrors: Object.cpp:491 ``WorldObject::IsWithinDist2d(Position const*, float)``."""
    return is_in_dist2d(this, pos, add(f32(dist), f32(this_reach)))


def is_within_dist(this: Vec, this_reach: float, other: Vec, other_reach: float, dist: float,
                   is3d: bool = True, inc_own: bool = True, inc_target: bool = True) -> bool:
    """Mirrors: Object.cpp:410 ``WorldObject::_IsWithinDist`` (no shared transport)."""
    size = 0.0
    size = add(size, f32(this_reach) if inc_own else 0.0)
    size = add(size, f32(other_reach) if inc_target else 0.0)
    maxdist = add(f32(dist), size)
    return is_in_dist(this, other, maxdist) if is3d else is_in_dist2d(this, other, maxdist)


def is_in_between(this: Vec, this_reach: float, pos1: Vec, pos2: Vec, size: float = 0.0) -> bool:
    """Mirrors: Object.cpp:653 ``WorldObject::IsInBetween``."""
    dist = exact_dist2d(this, pos1)
    if mul(dist, dist) >= exact_dist2d_sq(pos1, pos2[0], pos2[1]):
        return False
    size = f32(size)
    if not size:
        size = div(f32(this_reach), 2.0)
    angle = absolute_angle(pos1, pos2[0], pos2[1])
    px = add(pos1[0], mul(cos(angle), dist))
    py = add(pos1[1], mul(sin(angle), dist))
    return mul(size, size) >= exact_dist2d_sq(this, px, py)


def is_within_boundary_radius(this: Vec, other: Vec, other_bounding_radius: float) -> bool:
    """Mirrors: Unit.cpp:707 ``Unit::IsWithinBoundaryRadius`` (same map/phase assumed by caller).

    ``IsInDist(obj, max(obj->BoundingRadius, MIN_MELEE_REACH))`` -- 3D, strict,
    centre to centre, no combat reach.
    """
    return is_in_dist(this, other, max(f32(other_bounding_radius), MIN_MELEE_REACH))


def move_position_2d(pos: Vec, from_orientation: float, dist: float, angle: float) -> tuple[float, float]:
    """The pre-collision XY candidate of ``WorldObject::MovePosition[ToFirstCollision]``.

    Mirrors: Object.cpp:2789/2835 -- ``angle += GetOrientation(); dest = pos + dist*cos/sin(angle)``.
    The following height / VMAP / MMAP / ground steps are world geometry and are
    *not* modelled (fixture fact ``dest`` or FailClosed at the caller).
    """
    a = add(f32(angle), f32(from_orientation))
    d = f32(dist)
    return (add(pos[0], mul(d, cos(a))), add(pos[1], mul(d, sin(a))))


DIRECTION_ANGLES = {
    # Mirrors: SpellInfo.cpp:108 SpellImplicitTargetInfo::CalcDirectionAngle
    "TARGET_DIR_NONE": 0.0,
    "TARGET_DIR_FRONT": 0.0,
    "TARGET_DIR_BACK": f32(math.pi),
    "TARGET_DIR_RIGHT": f32(-math.pi / 2),
    "TARGET_DIR_LEFT": f32(math.pi / 2),
    "TARGET_DIR_FRONT_RIGHT": f32(-math.pi / 4),
    "TARGET_DIR_BACK_RIGHT": f32(-3 * math.pi / 4),
    "TARGET_DIR_BACK_LEFT": f32(3 * math.pi / 4),
    "TARGET_DIR_FRONT_LEFT": f32(math.pi / 4),
}


def direction_angle(direction: str, rand_norm: float | None = None) -> float:
    """Mirrors: SpellInfo.cpp:108 ``CalcDirectionAngle`` (``TARGET_DIR_RANDOM`` needs a rand_norm draw)."""
    if direction == "TARGET_DIR_RANDOM":
        if rand_norm is None:
            raise FailClosed("geometry: TARGET_DIR_RANDOM needs a rand_norm() value")
        return mul(f32(rand_norm), f32(2 * math.pi))
    if direction not in DIRECTION_ANGLES:
        raise FailClosed(f"geometry: unknown direction {direction!r}")
    return DIRECTION_ANGLES[direction]


# ---------------------------------------------------------------------------
# spell target checks (geometric part only)
# ---------------------------------------------------------------------------
def area_unit_in_cylinder(target: Vec, target_reach: float, center: Vec, rmin: float, rmax: float) -> bool:
    """Mirrors: Spell.cpp:9430 -- ``IsInRange2d(center, Min, Max) && |dz| <= Max``.

    Radius is extended by the *target's* combat reach (not the caster's); the
    vertical half-height is ``Max`` without reach and uses ``<=``.
    """
    if not is_in_range2d(target, target_reach, center, rmin, rmax):
        return False
    return abs(sub(target[2], center[2])) <= f32(rmax)


def cone_arc_ok(caster: Vec, caster_o: float, cone_angle_rad: float, target: Vec,
                within_boundary: bool) -> bool:
    """Mirrors: Spell.cpp:9474-9477 default cone branch.

    ``within_boundary`` = ``caster->IsWithinBoundaryRadius(target)`` (skip the arc).
    Otherwise ``HasInArc(coneAngle, target) != fuzzyGe(coneAngle, 0)`` rejects.
    """
    if within_boundary:
        return True
    return has_in_arc(caster, caster_o, cone_angle_rad, target) == fuzzy_ge(cone_angle_rad, 0.0)


def cone_back_ok(caster: Vec, caster_o: float, cone_angle_rad: float, target: Vec) -> bool:
    """Mirrors: Spell.cpp:9461-9464 (SPELL_ATTR0_CU_CONE_BACK; never set at the pin)."""
    return not has_in_arc(caster, caster_o, -abs(cone_angle_rad), target)


def line_ok(src: Vec, src_o: float, target: Vec, target_reach: float, width: float) -> bool:
    """Mirrors: Spell.cpp:9505 ``WorldObjectSpellLineTargetCheck`` geometric part (HasInLine)."""
    return has_in_line(src, src_o, target, target_reach, width)


def line_orientation(src: Vec, src_o: float, dst: Vec | None, same_position: bool) -> float:
    """Mirrors: Spell.cpp:9497-9503 -- orientation = src->GetAbsoluteAngle(dst) unless src == dst.

    ``Position::operator!=`` compares x, y, z and orientation (``same_position``
    must be decided by the caller from the fixture).
    """
    if dst is not None and not same_position:
        # Position::SetOrientation normalises again (Position.h:84): a tiny negative atan2
        # normalises to exactly 2*float(pi) in GetAbsoluteAngle and then to 0 here.
        return normalize_orientation(absolute_angle(src, dst[0], dst[1]))
    return src_o


def traj_line_ok(caster: Vec, caster_o: float, target: Vec, target_reach: float, src: Vec, dist2d: float) -> bool:
    """Mirrors: Spell.cpp:9485 ``WorldObjectSpellTrajTargetCheck`` geometric part."""
    if not has_in_line(caster, caster_o, target, target_reach, TRAJECTORY_MISSILE_SIZE):
        return False
    return not exact_dist2d(target, src) > f32(dist2d)


def nearby_distance(target: Vec, target_reach: float, caster: Vec) -> float:
    """Mirrors: Spell.cpp:9404 -- ``target->GetDistance(*_position)`` (Position overload: target reach only)."""
    return distance(target, target_reach, caster, None)
