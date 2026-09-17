"""Track C: binary32 geometry predicates vs verbatim Trinity code (tools/tc_target_geom_probe).

Every Python predicate in ``targeting.geometry`` (and the area/cone/line/traj/nearby
check compositions in ``targeting.area``) is compared with the compiled Trinity text
on random binary32 inputs plus exact-boundary constructions.  Models ruled out are
named per test.
"""

from __future__ import annotations

import math
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from targeting import TC_ROOT, caps
from targeting import geometry as g

PROBE_DIR = Path(__file__).resolve().parents[1] / "tools" / "tc_target_geom_probe"
PROBE = PROBE_DIR / "probe"
SETTINGS = settings(max_examples=int(os.environ.get("TG_C_EXAMPLES", "400")), deadline=None, suppress_health_check=[HealthCheck.too_slow])


class Probe:
    def __init__(self) -> None:
        self.p = subprocess.Popen([str(PROBE)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)

    def ask(self, *parts) -> list[str]:
        line = " ".join(p if isinstance(p, str) else p.hex() if isinstance(p, float) else str(int(p))
                        for p in parts)
        self.p.stdin.write(line + "\n")
        self.p.stdin.flush()
        return self.p.stdout.readline().split()

    def close(self) -> None:
        self.p.stdin.close()
        self.p.wait(timeout=10)


@pytest.fixture(scope="module")
def probe():
    if not (TC_ROOT / "src/server/game/Spells/Spell.cpp").exists():
        pytest.skip("sibling TrinityCore checkout absent")
    if shutil.which("g++") is None:
        pytest.skip("no g++")
    subprocess.run(["make", "-s", "-C", str(PROBE_DIR)], check=True, capture_output=True)
    p = Probe()
    yield p
    p.close()


coord = st.floats(min_value=-60.0, max_value=60.0, width=32, allow_nan=False)
small = st.floats(min_value=0.0, max_value=12.0, width=32, allow_nan=False)
angle = st.floats(min_value=-20.0, max_value=20.0, width=32, allow_nan=False)
pos = st.tuples(coord, coord, coord)


def h(x: str) -> float:
    return float.fromhex(x)


# ---------------------------------------------------------------------------
# primitives
# ---------------------------------------------------------------------------
def test_constants(probe):
    """Rules out 'DegToRad(360) < 2pi' (a full cone would then survive normalisation)."""
    assert h(probe.ask("D", 360.0)[1]) == g.deg_to_rad(360.0) == g.TWO_PI_F
    assert h(probe.ask("N", g.deg_to_rad(360.0))[1]) == 0.0 == g.normalize_orientation(g.deg_to_rad(360.0))
    assert g.LIBM, "glibc float functions should be loadable here"


@SETTINGS
@given(angle)
def test_normalize(probe, o):
    assert h(probe.ask("N", o)[1]) == g.normalize_orientation(o)


@SETTINGS
@given(st.floats(min_value=-720.0, max_value=720.0, width=32))
def test_deg_to_rad(probe, d):
    assert h(probe.ask("D", d)[1]) == g.deg_to_rad(d)


@SETTINGS
@given(pos, angle, st.floats(min_value=-7.0, max_value=7.0, width=32), pos)
def test_has_in_arc(probe, p, o, arc, t):
    r = probe.ask("A", *p, o, arc, *t)
    on = g.normalize_orientation(o)
    assert h(r[3]) == g.absolute_angle(p, t[0], t[1])
    assert h(r[2]) == g.relative_angle(p, on, t)
    assert (r[1] == "1") == g.has_in_arc(p, on, arc, t)


@SETTINGS
@given(pos, angle, pos, small, small)
def test_has_in_line(probe, p, o, t, size, width):
    r = probe.ask("L", *p, o, *t, size, width)
    assert (r[1] == "1") == g.has_in_line(p, g.normalize_orientation(o), t, size, width)


@SETTINGS
@given(pos, small, pos, small, small, small, st.booleans())
def test_ranges(probe, t, treach, c, creach, mn, mx, is3d):
    r = probe.ask("R", *t, treach, *c, creach, mn, mx, is3d)
    assert (r[1] == "1") == g.is_in_range2d(t, treach, c, mn, mx)
    assert (r[2] == "1") == g.is_in_range3d(t, treach, c, mn, mx)
    assert (r[3] == "1") == g.is_in_range_obj(t, treach, c, creach, mn, mx, is3d)


@SETTINGS
@given(pos, small, pos, small, small)
def test_distances(probe, a, ar, b, br, d):
    r = probe.ask("G", *a, ar, *b, br, d)
    assert h(r[1]) == g.distance(a, ar, b, br)
    assert h(r[2]) == g.distance(a, ar, b, None)
    d2 = g.sub(g.sub(g.exact_dist2d(a, b), ar), br)
    assert h(r[3]) == (d2 if d2 > 0 else 0.0)
    assert h(r[4]) == g.exact_dist(a, b)
    assert h(r[5]) == g.exact_dist2d(a, b)
    assert h(r[6]) == g.exact_dist_sq(a, b)
    assert (r[7] == "1") == g.is_within_dist3d(a, ar, b, d)
    assert (r[8] == "1") == g.is_within_dist2d(a, ar, b, d)
    assert (r[9] == "1") == g.is_within_dist(a, ar, b, br, d, True)
    assert (r[10] == "1") == g.is_within_dist(a, ar, b, br, d, False)


@SETTINGS
@given(pos, pos, pos, st.booleans())
def test_distance_order(probe, r_, a, b, is3d):
    assert (probe.ask("O", *r_, *a, *b, is3d)[1] == "1") == g.distance_order(r_, a, b, is3d)


@SETTINGS
@given(pos, small, pos, pos, small)
def test_in_between(probe, t, reach, p1, p2, size):
    assert (probe.ask("B", *t, reach, *p1, *p2, size)[1] == "1") == g.is_in_between(t, reach, p1, p2, size)


@SETTINGS
@given(pos, pos, small)
def test_boundary_radius(probe, a, b, br):
    assert (probe.ask("W", *a, *b, br)[1] == "1") == g.is_within_boundary_radius(a, b, br)


# ---------------------------------------------------------------------------
# composed spell checks
# ---------------------------------------------------------------------------
@SETTINGS
@given(pos, small, st.sampled_from([0, 1, 2, 3]), st.booleans(), pos, small, small, st.booleans(), st.sampled_from([0, 1]))
def test_area_check(probe, t, treach, immune, rel, c, mn, mx, canhit, reason):
    r = probe.ask("AREA", *t, treach, immune, rel, *c, mn, mx, canhit, reason)
    imm = {n for bit, n in ((1, "AoETarget"), (2, "ChainTarget")) if immune & bit}
    ok = g.area_unit_in_cylinder(t, treach, c, mn, mx)
    if ok:
        if reason == 0 and not canhit and "AoETarget" in imm:
            ok = False
        if reason == 1 and "ChainTarget" in imm:
            ok = False
    assert (r[1] == "1") == (ok and rel)


@SETTINGS
@given(pos, angle, small, st.floats(min_value=-400.0, max_value=400.0, width=32), st.sampled_from([0.0, 0.0, 2.5]),
       small, small, pos, small, small, st.booleans())
def test_cone_check(probe, c, co, creach, deg, width, mn, mx, t, treach, tbr, rel):
    cu = 4 if width else 0  # CU_CONE_LINE follows Width (SpellMgr.cpp:3244)
    r = probe.ask("CONE", *c, co, creach, deg, width, mn, mx, cu, *t, treach, tbr, 0, rel)
    con = g.normalize_orientation(co)
    rad = g.deg_to_rad(deg)
    if cu:
        geo = g.has_in_line(c, con, t, treach, width)
    else:
        geo = g.cone_arc_ok(c, con, rad, t, g.is_within_boundary_radius(c, t, tbr))
    ok = geo and g.area_unit_in_cylinder(t, treach, c, mn, mx) and rel
    assert (r[1] == "1") == ok


@SETTINGS
@given(pos, angle, small, st.sampled_from([0, 1, 2]), pos, angle, st.sampled_from([0.0, 1.5, 4.0]), small, small, pos, small)
def test_line_check(probe, c, co, creach, hasdst, d, do, width, mn, mx, t, treach):
    """Rules out 'the line check bounds length by radius' (TG-C-D02): mn/mx never matter."""
    r = probe.ask("LINE", *c, co, creach, hasdst, *d, do, width, mn, mx, *t, treach, 1)
    con = g.normalize_orientation(co)
    w = width if width else creach
    if hasdst == 1:
        from targeting.area import fuzzy_eq32
        same = all(fuzzy_eq32(a, b) for a, b in zip((*c, con), (*d, g.normalize_orientation(do))))
        o = g.line_orientation(c, con, d, same)
    else:
        o = con
    assert h(r[2]) == o
    assert (r[1] == "1") == g.has_in_line(c, o, t, treach, w)


@SETTINGS
@given(pos, angle, small, pos, pos, small)
def test_traj_check(probe, c, co, rng, s, t, treach):
    r = probe.ask("TRAJ", *c, co, rng, *s, *t, treach, 1)
    assert (r[1] == "1") == g.traj_line_ok(c, g.normalize_orientation(co), t, treach, s, rng)


@SETTINGS
@given(small, pos, small, st.lists(st.tuples(pos, small, st.booleans()), min_size=1, max_size=6))
def test_nearby_last_searcher(probe, rng, c, creach, ts):
    args = []
    for p, rr, rel in ts:
        args += [*p, rr, rel]
    r = probe.ask("NEAR", rng, *c, creach, len(ts), *args)
    best, found = g.f32(rng), -1
    for i, (p, rr, rel) in enumerate(ts):
        d = g.nearby_distance(p, rr, c)
        if d < best and rel:
            best, found = d, i
    assert int(r[1]) == found


@SETTINGS
@given(st.booleans(), pos, st.lists(pos, min_size=0, max_size=9), st.integers(0, 3))
def test_list_sort_matches_libstdcxx(probe, asc, ref, pts, dup):
    """Rules out 'stable sort' and 'reverse-stable sort' models for the non-strict descending comparator."""
    pts = list(pts)
    for i in range(min(dup, len(pts))):
        pts.append(pts[i])  # force exact ties
    args = [c for p in pts for c in p]
    r = probe.ask("SORT", asc, *ref, len(pts), *args)
    idx = list(range(len(pts)))
    ours = caps.list_sort(idx, lambda a, b: g.distance_order(ref, pts[a], pts[b]) == asc)
    assert [int(x) for x in r[1:]] == ours


@SETTINGS
@given(pos, angle, pos, angle)
def test_position_equality(probe, a, ao, b, bo):
    from targeting.area import fuzzy_eq32
    r = probe.ask("PEQ", *a, ao, *b, bo)
    ours = all(fuzzy_eq32(x, y) for x, y in zip((*a, g.normalize_orientation(ao)), (*b, g.normalize_orientation(bo))))
    assert (r[1] == "1") == ours


# ---------------------------------------------------------------------------
# exact-boundary constructions (models that disagree)
# ---------------------------------------------------------------------------
def test_area_boundary_is_strict_and_includes_target_reach(probe):
    """Rules out '<=' and 'no combat reach' models: a target exactly at R+reach is excluded,
    one just inside is included, and reach extends the radius."""
    R, reach = 8.0, 1.5
    edge = (R + reach, 0.0, 0.0)
    inside = (float.fromhex("0x1.2ffffep+3"), 0.0, 0.0)   # the binary32 predecessor of 9.5
    for t, want in ((edge, False), (inside, True), ((9.0, 0.0, 0.0), True)):
        r = probe.ask("AREA", *t, reach, 0, 1, 0.0, 0.0, 0.0, 0.0, R, 0, 0)
        assert (r[1] == "1") is want
        assert g.area_unit_in_cylinder(t, reach, (0.0, 0.0, 0.0), 0.0, R) is want
    # without reach 9.0 would be out: the reach model matters
    assert not g.area_unit_in_cylinder((9.0, 0.0, 0.0), 0.0, (0.0, 0.0, 0.0), 0.0, R)


def test_area_is_a_cylinder_not_a_sphere(probe):
    """Rules out the 3D sphere model: dz == Max is included (<=), dz above Max excluded, reach not added vertically."""
    R = 8.0
    for t, want in (((1.0, 0.0, 8.0), True), ((1.0, 0.0, 8.000001), False), ((8.0, 0.0, 7.5), True)):
        r = probe.ask("AREA", *map(g.f32, t), 1.5, 0, 1, 0.0, 0.0, 0.0, 0.0, R, 0, 0)
        assert (r[1] == "1") is want
    # a sphere with reach would reject (8, 0, 7.5)
    assert g.exact_dist((8.0, 0.0, 7.5), (0.0, 0.0, 0.0)) > 9.5


def test_area_min_radius_is_an_annulus(probe):
    """Trinity RadiusMin excludes close targets (inner bound = Min + target reach, not-less-than)."""
    for x, want in ((5.0, False), (5.5, True), (5.49, False)):
        r = probe.ask("AREA", g.f32(x), 0.0, 0.0, 1.5, 0, 1, 0.0, 0.0, 0.0, 4.0, 10.0, 0, 0)
        assert (r[1] == "1") is want
        assert g.area_unit_in_cylinder((g.f32(x), 0.0, 0.0), 1.5, (0.0, 0.0, 0.0), 4.0, 10.0) is want


def test_cone_uses_half_angle_and_inclusive_border(probe):
    """Rules out 'ConeDegrees is the half-angle': a 60-degree cone keeps +-30 degrees only."""
    c = (0.0, 0.0, 0.0)
    for deg_off, want in ((29.0, True), (31.0, False), (-29.0, True), (59.0, False)):
        a = math.radians(deg_off)
        t = (g.f32(6 * math.cos(a)), g.f32(6 * math.sin(a)), 0.0)
        r = probe.ask("CONE", *c, 0.0, 1.5, 60.0, 0.0, 0.0, 10.0, 0, *t, 1.0, 0.5, 0, 1)
        assert (r[1] == "1") is want


def test_cone_360_degenerates(probe):
    """TG-C-D04: a 360-degree cone does not hit a unit behind the caster (outside the boundary radius)."""
    behind = (-6.0, 0.0, 0.0)
    r = probe.ask("CONE", 0.0, 0.0, 0.0, 0.0, 1.5, 360.0, 0.0, 0.0, 10.0, 0, *behind, 1.0, 0.5, 0, 1)
    assert r[1] == "0"
    front = (6.0, 0.0, 0.0)
    assert probe.ask("CONE", 0.0, 0.0, 0.0, 0.0, 1.5, 360.0, 0.0, 0.0, 10.0, 0, *front, 1.0, 0.5, 0, 1)[1] == "1"
    near_behind = (-1.9, 0.0, 0.0)  # inside max(bounding, 2) -> arc bypassed
    assert probe.ask("CONE", 0.0, 0.0, 0.0, 0.0, 1.5, 360.0, 0.0, 0.0, 10.0, 0, *near_behind, 1.0, 0.5, 0, 1)[1] == "1"


def test_cone_negative_angle_selects_back(probe):
    back = (-6.0, 0.5, 0.0)
    front = (6.0, 0.0, 0.0)
    assert probe.ask("CONE", 0.0, 0.0, 0.0, 0.0, 1.5, -90.0, 0.0, 0.0, 10.0, 0, *back, 1.0, 0.5, 0, 1)[1] == "1"
    assert probe.ask("CONE", 0.0, 0.0, 0.0, 0.0, 1.5, -90.0, 0.0, 0.0, 10.0, 0, *front, 1.0, 0.5, 0, 1)[1] == "0"


def test_line_ignores_radius(probe):
    """TG-C-D02: a unit 30 yd down the line of a 10-yd line effect passes the check."""
    r = probe.ask("LINE", 0.0, 0.0, 0.0, 0.0, 1.5, 2, 0.0, 0.0, 0.0, 0.0, 4.0, 0.0, 10.0, 30.0, 0.0, 0.0, 1.0, 1)
    assert r[1] == "1"


def test_furthest_ties_reverse(probe):
    """TG-C-D03 witness: equal-distance units come out of the descending sort reversed."""
    r = probe.ask("SORT", 0, 0.0, 0.0, 0.0, 3, 5.0, 0.0, 0.0, 0.0, 5.0, 0.0, 9.0, 0.0, 0.0)
    assert [int(x) for x in r[1:]] == [2, 1, 0]
    assert caps.list_sort([0, 1, 2], lambda a, b: not g.distance_order(
        (0.0, 0.0, 0.0), [(5.0, 0.0, 0.0), (0.0, 5.0, 0.0), (9.0, 0.0, 0.0)][a],
        [(5.0, 0.0, 0.0), (0.0, 5.0, 0.0), (9.0, 0.0, 0.0)][b])) == [2, 1, 0]


def test_move_position_candidate():
    """Directional offset: angle is relative to the anchor orientation (float cos/sin)."""
    x, y = g.move_position_2d((0.0, 0.0, 0.0), g.f32(math.pi / 2), 5.0, g.DIRECTION_ANGLES["TARGET_DIR_FRONT"])
    assert abs(x) < 1e-5 and y == 5.0
