"""Track I: AreaTrigger shape containment vs verbatim Trinity code (tools/tc_target_at_probe).

``targeting.areatriggers`` (evaluate_shape + unit_in_shape + max_search_radius) is compared
with the compiled ``AreaTrigger::SearchUnitIn*`` / ``AreaTriggerShapeInfo::GetMaxSearchRadius``
text on random binary32 inputs and exact-boundary constructions.  Each test names the
wrong model it rules out.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from targeting import TC_ROOT, FailClosed
from targeting import areatriggers as at
from targeting import geometry as g

PROBE_DIR = Path(__file__).resolve().parents[1] / "tools" / "tc_target_at_probe"
PROBE = PROBE_DIR / "probe"
SETTINGS = settings(max_examples=int(os.environ.get("TG_I_EXAMPLES", "300")), deadline=None,
                    suppress_health_check=[HealthCheck.too_slow])


class Probe:
    def __init__(self) -> None:
        self.p = subprocess.Popen([str(PROBE)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)

    def ask(self, *parts) -> list[str]:
        line = " ".join(p if isinstance(p, str) else p.hex() if isinstance(p, float) else str(int(p)) for p in parts)
        self.p.stdin.write(line + "\n")
        self.p.stdin.flush()
        return self.p.stdout.readline().split()

    def close(self) -> None:
        self.p.stdin.close()
        self.p.wait(timeout=10)


@pytest.fixture(scope="module")
def probe():
    if not (TC_ROOT / "src/server/game/Entities/AreaTrigger/AreaTrigger.cpp").exists():
        pytest.skip("sibling TrinityCore checkout absent")
    if shutil.which("g++") is None:
        pytest.skip("no g++")
    subprocess.run(["make", "-s", "-C", str(PROBE_DIR)], check=True, capture_output=True)
    p = Probe()
    yield p
    p.close()


def f32s(lo, hi):
    return st.floats(min_value=g.f32(lo), max_value=g.f32(hi), width=32, allow_nan=False)


coord = f32s(-30.0, 30.0)
ext = f32s(0.0, 20.0)
frac = f32s(0.0, 1.0)
unit = st.tuples(coord, coord, f32s(-15.0, 15.0), f32s(0.0, 3.0), st.booleans())
vertex = st.tuples(f32s(-15.0, 15.0), f32s(-15.0, 15.0))


def shape_args(shape_type, raw, verts, tverts):
    out = [shape_type, *[float(v) for v in raw], len(verts)]
    for v in verts:
        out += [float(v[0]), float(v[1])]
    out.append(len(tverts))
    for v in tverts:
        out += [float(v[0]), float(v[1])]
    return out


def ask_search(probe, shape_type, raw, verts, tverts, progress, scale, flags, pose, units):
    args = ["S", *shape_args(shape_type, raw, verts, tverts), float(progress), float(scale), flags, *map(float, pose), len(units)]
    for u in units:
        args += [float(u[0]), float(u[1]), float(u[2]), float(u[3]), int(u[4])]
    r = probe.ask(*args)
    assert r[0] == "S", r
    return float.fromhex(r[1]), [int(x) for x in r[2:]]


def oracle_search(shape_type, raw, verts, tverts, progress, scale, flags, pose, units):
    shape = at.shape_from_row(shape_type, raw, verts, tverts)
    pos = g.vec(pose[:3])
    o = g.f32(pose[3])
    bounds = at.max_search_radius(shape)
    state = at.evaluate_shape(shape, pos, o, g.f32(progress), g.f32(scale), bounds_radius_2d=bounds, field_flags=flags)
    kept = [i for i, u in enumerate(units) if at.unit_in_shape(state, pos, o, u[:3], u[3])]
    return g.mul(bounds, g.f32(scale)), kept


def check(probe, *args):
    assert ask_search(probe, *args) == oracle_search(*args)


# ---------------------------------------------------------------------------
@SETTINGS
@given(st.tuples(ext, ext), frac, f32s(0.1, 3.0), st.tuples(coord, coord, coord, f32s(-7.0, 7.0)), st.lists(unit, max_size=8))
def test_sphere(probe, radii, progress, scale, pose, units):
    """Rules out 'sphere is a 2D circle' and 'radius excludes the unit's combat reach' and 'dead units skipped'."""
    raw = [radii[0], radii[1], 0, 0, 0, 0, 0, 0]
    check(probe, 0, raw, [], [], progress, scale, 0, pose, units)


@SETTINGS
@given(st.tuples(ext, ext, ext, ext, ext, ext), frac, f32s(0.1, 3.0), st.tuples(coord, coord, coord, f32s(-7.0, 7.0)),
       st.lists(unit, max_size=8))
def test_box(probe, e, progress, scale, pose, units):
    """Rules out 'box height uses the full Z extent' (Trinity halves it) and 'box ignores AT orientation'."""
    check(probe, 1, list(e) + [0, 0], [], [], progress, scale, 0, pose, units)


@SETTINGS
@given(st.tuples(ext, ext, ext, ext, f32s(-2, 2), f32s(-2, 2)), frac, f32s(0.1, 3.0), st.sampled_from([0, 1]),
       st.tuples(coord, coord, coord, f32s(-7.0, 7.0)), st.lists(unit, max_size=8))
def test_cylinder(probe, c, progress, scale, flags, pose, units):
    """Rules out 'cylinder is [z, z+h]' (Trinity: [z-h, z+h] inclusive) and 'LocationZOffset shifts the search'."""
    check(probe, 4, list(c) + [0, 0], [], [], progress, scale, flags, pose, units)


@SETTINGS
@given(st.tuples(ext, ext, ext, ext, ext, ext, f32s(-2, 2), f32s(-2, 2)), frac, f32s(0.1, 3.0), st.sampled_from([0, 1]),
       st.tuples(coord, coord, coord, f32s(-7.0, 7.0)), st.lists(unit, max_size=8))
def test_disk(probe, d, progress, scale, flags, pose, units):
    """Rules out 'inner radius adds the unit's combat reach' (outer does, inner does not)."""
    check(probe, 5, list(d), [], [], progress, scale, flags, pose, units)


@SETTINGS
@given(st.tuples(ext, ext, ext, ext), frac, f32s(0.1, 3.0), st.tuples(coord, coord, coord, f32s(-7.0, 7.0)),
       st.lists(unit, max_size=8))
def test_bounded_plane(probe, e, progress, scale, pose, units):
    """Rules out 'bounded plane max search radius == box radius' (Trinity quarters the squares)."""
    check(probe, 6, list(e) + [0, 0, 0, 0], [], [], progress, scale, 0, pose, units)


@SETTINGS
@given(st.tuples(f32s(-3, 10), f32s(-3, 10)), st.lists(vertex, min_size=3, max_size=6), st.booleans(), frac,
       f32s(0.1, 3.0), st.tuples(coord, coord, coord, f32s(-7.0, 7.0)), st.lists(unit, max_size=8), st.data())
def test_polygon(probe, heights, verts, with_target, progress, scale, pose, units, data):
    """Rules out 'polygon vertices are world coordinates' (they are AT-relative, rotated by the AT orientation)
    and 'non-positive height means flat' (load fix-up to 1)."""
    tverts = data.draw(st.lists(vertex, min_size=len(verts), max_size=len(verts))) if with_target else []
    check(probe, 3, [heights[0], heights[1], 0, 0, 0, 0, 0, 0], verts, tverts, progress, scale, 0, pose, units)


def test_boundaries(probe):
    """Exact boundaries: strict '<' on radius+reach, inclusive heights and box faces."""
    cases = [
        (0, [8, 8, 0, 0, 0, 0, 0, 0], [(7.5, 0, 0, 0.5, 1), (7.4999995, 0, 0, 0.5, 1)]),
        (4, [8, 8, 4, 4, 0, 0, 0, 0], [(0, 0, 4, 0, 1), (0, 0, -4, 0, 0), (0, 0, 4.0000005, 0, 1), (8, 0, 0, 0, 1)]),
        (1, [5, 3, 6, 5, 3, 6, 0, 0], [(5, 3, 3, 0, 1), (5, 3, 3.0000002, 0, 1), (5.0000005, 0, 0, 0, 1)]),
        (5, [2, 2, 6, 6, 1, 1, 0, 0], [(2, 0, 0, 0, 1), (1.9999999, 0, 0, 5, 1), (6.4, 0, 0, 0.5, 1)]),
    ]
    for shape_type, raw, units in cases:
        for o in (0.0, 1.0):
            check(probe, shape_type, raw, [], [], 0.0, 1.0, 0, (0.0, 0.0, 0.0, o), units)


def test_max_search_radius_db(probe, tg_ctx):
    """GetMaxSearchRadius for every loaded world-DB create-properties row (all shapes present in TDB)."""
    w = at.ATWorld(tg_ctx.bundle.world)
    seen = set()
    for (cp_id, custom) in sorted(w.cp_rows):
        cp = w.create_properties(cp_id, bool(custom))
        if cp is None:
            continue
        seen.add(cp.shape.kind)
        r = w.cp_rows[(cp_id, custom)]
        args = shape_args(int(r["Shape"]), [cp.shape.raw[i] for i in range(8)], cp.shape.vertices, cp.shape.vertices_target)
        if cp.shape.kind == "polygon":
            # the probe re-applies the load fix-up; pass the raw DB heights
            args = shape_args(3, [r["ShapeData0"], r["ShapeData1"]] + [cp.shape.raw[i] for i in range(2, 8)],
                              cp.shape.vertices, cp.shape.vertices_target)
        assert float.fromhex(probe.ask("M", *args)[1]) == at.max_search_radius(cp.shape), cp_id
    assert {"sphere", "cylinder"} <= seen


def test_dynamic_shape_fails_closed():
    """A progress-dependent shape without a stated progress must not default to progress 0 or 1."""
    halo = at.shape_from_row(0, [0, 40, 0, 0, 0, 0, 0, 0])
    assert halo.is_dynamic
    with pytest.raises(FailClosed):
        at.progress_for_shape(halo, 78103, None)
    blizzard = at.shape_from_row(4, [8, 8, 4, 4, 0.3, 0.3, 0, 0])
    assert not blizzard.is_dynamic and at.progress_for_shape(blizzard, 0, None) == 0.0


def test_scale_curve_fails_closed():
    """ScaleCurveId without an evaluated value fails closed; an active override replaces (not multiplies) the curve."""
    with pytest.raises(FailClosed):
        at.calc_current_scale(None, 123, None)
    assert at.calc_current_scale(1.2, 123, None) == g.f32(1.2)
    assert at.calc_current_scale(None, 0, None) == 1.0
    assert at.calc_current_scale(0.0, 0, None) == g.f32(0.000001)
