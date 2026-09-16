"""Differential verification against compiled TrinityCore expressions.

``tools/tc_probe`` extracts the numeric kernels verbatim from the sibling
TrinityCore checkout and compiles them, so these tests compare the Python port
against real C++ ``float`` arithmetic rather than against itself.

The probe is optional: if it has not been built the tests skip with a message
telling you how to build it.  Build with::

    make -C scripts/research/tools/tc_probe

Known and intentional difference: TrinityCore stores curve points as ``float``
and evaluates in ``float``; this package evaluates in Python ``double``.  The
tests therefore assert agreement on the *rounded* result (which is what every
consumer actually uses) and separately bound the raw divergence.
"""

from __future__ import annotations

import math
import subprocess
from pathlib import Path

import pytest

from gearing.curves import (
    BEZIER,
    BEZIER3,
    BEZIER4,
    CATMULL_ROM,
    CONSTANT,
    COSINE,
    LINEAR,
    determine_curve_type,
    interpolate,
    round_half_away,
)

PROBE = Path(__file__).resolve().parents[1] / "tools" / "tc_probe" / "probe"


@pytest.fixture(scope="session")
def probe():
    if not PROBE.exists():
        pytest.skip(f"probe not built; run: make -C {PROBE.parent}")
    process = subprocess.Popen(
        [str(PROBE)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
        bufsize=1)

    def ask(request: str) -> str:
        process.stdin.write(request + "\n")
        process.stdin.flush()
        return process.stdout.readline().strip()

    yield ask
    process.stdin.close()
    process.wait(timeout=10)


def _curve_request(mode, points, x):
    flat = " ".join(f"{p[0]!r} {p[1]!r}" for p in points)
    return f"curve {mode} {len(points)} {flat} {x!r}"


# -- DetermineCurveType ---------------------------------------------------

@pytest.mark.parametrize("curve_type", [0, 1, 2, 3, 4, 5, 9])
@pytest.mark.parametrize("count", [1, 2, 3, 4, 5, 8])
def test_determine_curve_type_matches_trinity(probe, curve_type, count):
    assert probe(f"curvetype {curve_type} {count}") == \
        determine_curve_type(curve_type, count)


# -- interpolation --------------------------------------------------------

CURVE_CASES = [
    (LINEAR, [(0.0, 0.0), (1300.0, 1300.0)], [0.0, 1.0, 292.0, 1299.0, 1300.0, 2000.0]),
    (LINEAR, [(342.0, 70.0), (571.0, 80.0)], [342.0, 441.0, 500.0, 571.0]),
    (LINEAR, [(1.0, 1.0), (80.0, 80.0)], [0.0, 1.0, 40.5, 80.0, 90.0]),
    (COSINE, [(0.0, 0.0), (10.0, 100.0)], [-1.0, 0.0, 2.5, 5.0, 7.5, 11.0]),
    (CATMULL_ROM, [(0.0, 0.0), (1.0, 1.0), (2.0, 8.0), (3.0, 27.0), (4.0, 64.0)],
     [0.5, 1.5, 2.0, 2.25, 2.75, 3.5]),
    (BEZIER3, [(0.0, 0.0), (5.0, 10.0), (10.0, 0.0)], [0.0, 2.5, 5.0, 7.5, 10.0]),
    (BEZIER4, [(0.0, 0.0), (1.0, 10.0), (2.0, 10.0), (3.0, 0.0)],
     [0.0, 0.75, 1.5, 2.25, 3.0]),
    (BEZIER, [(0.0, 0.0), (1.0, 4.0), (2.0, 8.0), (3.0, 2.0), (4.0, 6.0)],
     [0.0, 1.0, 2.0, 3.3, 4.0]),
    (CONSTANT, [(10.0, 7.0)], [-5.0, 10.0, 99.0]),
]


@pytest.mark.parametrize("mode, points, xs", CURVE_CASES,
                         ids=[c[0] for c in CURVE_CASES])
def test_interpolation_matches_trinity_after_rounding(probe, mode, points, xs):
    for x in xs:
        cpp = float(probe(_curve_request(mode, points, x)))
        python = interpolate(mode, points, x)[0]
        assert round_half_away(cpp) == round_half_away(python), (mode, x)


@pytest.mark.parametrize("mode, points, xs", CURVE_CASES,
                         ids=[c[0] for c in CURVE_CASES])
def test_interpolation_raw_divergence_is_float_precision_only(probe, mode, points, xs):
    for x in xs:
        cpp = float(probe(_curve_request(mode, points, x)))
        python = interpolate(mode, points, x)[0]
        scale = max(1.0, abs(python))
        # float has ~7 significant decimal digits; anything larger is a real
        # transcription bug, not a precision artefact.
        assert math.isclose(cpp, python, rel_tol=1e-6, abs_tol=1e-6 * scale), \
            (mode, x, cpp, python)


# -- std::round -----------------------------------------------------------

@pytest.mark.parametrize("value", [0.0, 0.5, 1.5, 2.5, -0.5, -1.5, 291.5,
                                   0.49999997, 74.323143])
def test_std_round_matches_trinity(probe, value):
    assert int(probe(f"round {value!r}")) == round_half_away(value)


# -- weapon damage --------------------------------------------------------

DAMAGE_CASES = [
    (122.28, 3600, 0.5), (95.97, 2600, 0.7), (17.28, 3600, 0.25),
    (43.066063, 3600, 0.5), (0.4036421, 2600, 0.4),
]


@pytest.mark.parametrize("dps, delay, variance", DAMAGE_CASES)
def test_weapon_damage_matches_trinity(probe, dps, delay, variance):
    cpp_min, cpp_max = (float(v) for v in
                        probe(f"damage {dps!r} {delay} {variance!r}").split())
    avg = dps * delay * 0.001
    python_min = (variance * -0.5 + 1.0) * avg
    python_max = math.floor(avg * (variance * 0.5 + 1.0) + 0.5)
    assert cpp_max == pytest.approx(python_max)
    assert cpp_min == pytest.approx(python_min, rel=1e-6)


# -- armor ----------------------------------------------------------------

ARMOR_CASES = [
    (0.9, 22.04649353027, 0.11999999732),
    (1.0, 48.90251541138, 0.5),
    (0.94999998808, 12.83871364594, 0.20999999344),
    (0.5, 1.0, 1.0),      # exactly 0.5 before the +0.5f truncation
]


@pytest.mark.parametrize("qualitymod, total, location", ARMOR_CASES)
def test_armor_expression_matches_trinity(probe, qualitymod, total, location):
    cpp = int(probe(f"armor {qualitymod!r} {total!r} {location!r}"))
    python = int(qualitymod * total * location + 0.5)
    assert cpp == python


# -- real snapshot curves --------------------------------------------------

@pytest.mark.snapshot
def test_real_curve_evaluations_match_trinity(probe, resolver, witnesses):
    """Every curve the corpus actually touches, compared point for point."""
    checked = 0
    for witness in witnesses["curves"]:
        curve_id = witness["curve_id"]
        points = resolver.curves.points(curve_id)
        mode = resolver.curves.mode(curve_id)
        assert probe(f"curvetype {resolver.curves.curve_type(curve_id)} "
                     f"{len(points)}") == mode
        for sample in witness["samples"]:
            x = sample["x"]
            cpp = float(probe(_curve_request(mode, points, x)))
            python = resolver.curves.value_at(curve_id, x, consumer="differential")
            assert round_half_away(cpp) == round_half_away(python), (curve_id, x)
            checked += 1
    assert checked >= 8


@pytest.mark.snapshot
def test_squish_curve_agrees_across_its_whole_domain(probe, resolver):
    """The Midnight squish curve is the highest-leverage curve in the snapshot."""
    curve_id = 92181
    points = resolver.curves.points(curve_id)
    mode = resolver.curves.mode(curve_id)
    for x in range(1, 1301, 7):
        cpp = float(probe(_curve_request(mode, points, float(x))))
        python = resolver.curves.value_at(curve_id, x, consumer="differential")
        assert round_half_away(cpp) == round_half_away(python), x
