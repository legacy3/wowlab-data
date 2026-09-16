"""Focused tests for the curve engine.

The evaluator is foundational, so each interpolation mode is checked against an
independently written reference expression rather than against the function
under test.  Reference expressions are transcribed from
``DB2Manager::GetCurveValueAt`` by hand and kept deliberately naive.
"""

from __future__ import annotations

import math

import pytest
from conftest import write_csv

from gearing import SourceError
from gearing.curves import (
    BEZIER,
    BEZIER3,
    BEZIER4,
    CATMULL_ROM,
    CLAMP_ABOVE_LAST,
    CLAMP_ABOVE_PENULTIMATE,
    CLAMP_BELOW_FIRST,
    CLAMP_BELOW_SECOND,
    CONSTANT,
    COSINE,
    LINEAR,
    UNKNOWN_CURVE,
    ZERO_WIDTH,
    Curves,
    determine_curve_type,
    interpolate,
    round_half_away,
)
from gearing.tables import Tables


# -- std::round ----------------------------------------------------------

@pytest.mark.parametrize("value, expected", [
    (0.0, 0), (0.4999999, 0), (0.5, 1), (0.5000001, 1), (1.5, 2), (2.5, 3),
    (-0.5, -1), (-1.5, -2), (-2.5, -3), (-0.4999999, 0),
    (291.5, 292), (292.5, 293),
])
def test_round_half_away_matches_c(value, expected):
    assert round_half_away(value) == expected


def test_round_half_away_differs_from_python_round():
    # Python uses banker's rounding; std::round does not.  If this ever starts
    # passing with round() the port has silently changed meaning.
    assert round(0.5) == 0
    assert round_half_away(0.5) == 1
    assert round(2.5) == 2
    assert round_half_away(2.5) == 3


# -- DetermineCurveType --------------------------------------------------

@pytest.mark.parametrize("curve_type, points, expected", [
    (1, 1, COSINE), (1, 2, COSINE), (1, 3, COSINE), (1, 4, CATMULL_ROM),
    (1, 9, CATMULL_ROM),
    (2, 1, CONSTANT), (2, 2, LINEAR), (2, 3, BEZIER3), (2, 4, BEZIER4),
    (2, 5, BEZIER), (2, 40, BEZIER),
    (3, 1, COSINE), (3, 7, COSINE),
    (0, 1, CONSTANT), (0, 2, LINEAR), (0, 9, LINEAR),
    # Types 4 and 5 exist in the snapshot but have no case in the direct
    # consumer, so they reach the same default branch as type 0.
    (4, 2, LINEAR), (5, 25, LINEAR), (5, 1, CONSTANT), (99, 3, LINEAR),
])
def test_determine_curve_type(curve_type, points, expected):
    assert determine_curve_type(curve_type, points) == expected


# -- Constant ------------------------------------------------------------

def test_constant_ignores_x():
    points = [(10.0, 7.0)]
    for x in (-100.0, 10.0, 1e6):
        y, bracket, clamp = interpolate(CONSTANT, points, x)
        assert y == 7.0
        assert bracket == [(10.0, 7.0)]
        assert clamp == "constant"


# -- Linear --------------------------------------------------------------

LINEAR_POINTS = [(0.0, 0.0), (10.0, 100.0), (20.0, 150.0)]


def _reference_linear(points, x):
    """Independently transcribed from GetCurveValueAt's Linear branch."""
    i = 0
    while i < len(points) and points[i][0] <= x:
        i += 1
    if i == 0:
        return points[0][1]
    if i >= len(points):
        return points[-1][1]
    x0, y0 = points[i - 1]
    x1, y1 = points[i]
    if x1 - x0 == 0.0:
        return y1
    return (((x - x0) / (x1 - x0)) * (y1 - y0)) + y0


@pytest.mark.parametrize("x", [-5.0, 0.0, 0.1, 5.0, 9.9, 10.0, 15.0, 20.0, 25.0])
def test_linear_matches_reference(x):
    y, _, _ = interpolate(LINEAR, LINEAR_POINTS, x)
    assert y == pytest.approx(_reference_linear(LINEAR_POINTS, x))


def test_linear_exact_points():
    # x == a point's X makes the scan step *past* it, so the value comes from
    # the following segment's left endpoint -- which is that same point.
    assert interpolate(LINEAR, LINEAR_POINTS, 10.0)[0] == 100.0
    assert interpolate(LINEAR, LINEAR_POINTS, 0.0)[0] == 0.0


def test_linear_below_and_above_range():
    y, bracket, clamp = interpolate(LINEAR, LINEAR_POINTS, -1.0)
    assert (y, bracket, clamp) == (0.0, [(0.0, 0.0)], CLAMP_BELOW_FIRST)
    y, bracket, clamp = interpolate(LINEAR, LINEAR_POINTS, 1000.0)
    assert (y, bracket, clamp) == (150.0, [(20.0, 150.0)], CLAMP_ABOVE_LAST)


def test_linear_zero_width_segment_returns_right_endpoint():
    points = [(0.0, 1.0), (5.0, 2.0), (5.0, 9.0), (10.0, 3.0)]
    # x = 5 steps past both points with X == 5, so the bracket is (5,9)-(10,3).
    assert interpolate(LINEAR, points, 5.0)[0] == pytest.approx(9.0)
    # x = 4.9 brackets (0,1)-(5,2): an ordinary segment.
    assert interpolate(LINEAR, points, 4.9)[0] == pytest.approx(1.98)


def test_linear_zero_width_marker():
    points = [(0.0, 1.0), (3.0, 2.0), (3.0, 9.0)]
    y, bracket, clamp = interpolate(LINEAR, points, 2.9999999)
    assert clamp is None
    # Force the degenerate branch: a segment whose endpoints share X.
    points = [(3.0, 2.0), (3.0, 9.0)]
    y, bracket, clamp = interpolate(LINEAR, points, 2.0)
    assert clamp == CLAMP_BELOW_FIRST and y == 2.0


# -- Cosine --------------------------------------------------------------

def _reference_cosine(points, x):
    i = 0
    while i < len(points) and points[i][0] <= x:
        i += 1
    if i == 0:
        return points[0][1]
    if i >= len(points):
        return points[-1][1]
    x0, y0 = points[i - 1]
    x1, y1 = points[i]
    if x1 - x0 == 0.0:
        return y1
    return ((y1 - y0) * (1.0 - math.cos((x - x0) / (x1 - x0) * math.pi)) * 0.5) + y0


@pytest.mark.parametrize("x", [-1.0, 0.0, 2.5, 5.0, 7.5, 10.0, 11.0])
def test_cosine_matches_reference(x):
    points = [(0.0, 0.0), (10.0, 100.0)]
    assert interpolate(COSINE, points, x)[0] == pytest.approx(
        _reference_cosine(points, x))


def test_cosine_midpoint_is_half():
    points = [(0.0, 0.0), (10.0, 100.0)]
    assert interpolate(COSINE, points, 5.0)[0] == pytest.approx(50.0)


# -- Catmull-Rom ---------------------------------------------------------

CATMULL_POINTS = [(0.0, 0.0), (1.0, 1.0), (2.0, 8.0), (3.0, 27.0), (4.0, 64.0)]


def _reference_catmull(points, x):
    i = 1
    while i < len(points) and points[i][0] <= x:
        i += 1
    if i == 1:
        return points[1][1]
    if i >= len(points) - 1:
        return points[len(points) - 2][1]
    x0, y0 = points[i - 1]
    x1, y1 = points[i]
    mu = (x - x0) / (x1 - x0)
    pm2 = points[i - 2][1]
    pp1 = points[i + 1][1]
    a0 = -0.5 * pm2 + 1.5 * y0 - 1.5 * y1 + 0.5 * pp1
    a1 = pm2 - 2.5 * y0 + 2.0 * y1 - 0.5 * pp1
    a2 = -0.5 * pm2 + 0.5 * y1
    a3 = y0
    return a0 * mu ** 3 + a1 * mu ** 2 + a2 * mu + a3


@pytest.mark.parametrize("x", [1.5, 2.0, 2.25, 2.75])
def test_catmull_rom_matches_reference(x):
    assert interpolate(CATMULL_ROM, CATMULL_POINTS, x)[0] == pytest.approx(
        _reference_catmull(CATMULL_POINTS, x))


def test_catmull_rom_clamps_at_both_ends():
    y, _, clamp = interpolate(CATMULL_ROM, CATMULL_POINTS, 0.5)
    assert (y, clamp) == (1.0, CLAMP_BELOW_SECOND)
    y, _, clamp = interpolate(CATMULL_ROM, CATMULL_POINTS, 3.5)
    assert (y, clamp) == (27.0, CLAMP_ABOVE_PENULTIMATE)


def test_catmull_rom_requires_four_points():
    with pytest.raises(SourceError):
        interpolate(CATMULL_ROM, [(0.0, 0.0), (1.0, 1.0), (2.0, 2.0)], 1.5)


# -- Bezier --------------------------------------------------------------

def test_bezier3_matches_reference():
    points = [(0.0, 0.0), (5.0, 10.0), (10.0, 0.0)]
    for x in (0.0, 2.5, 5.0, 7.5, 10.0, 12.0):
        mu = (x - points[0][0]) / (points[2][0] - points[0][0])
        expected = ((1 - mu) ** 2) * 0.0 + (1 - mu) * 2 * mu * 10.0 + mu * mu * 0.0
        assert interpolate(BEZIER3, points, x)[0] == pytest.approx(expected)


def test_bezier4_matches_reference():
    points = [(0.0, 0.0), (1.0, 10.0), (2.0, 10.0), (3.0, 0.0)]
    for x in (0.0, 0.75, 1.5, 3.0):
        mu = (x - 0.0) / 3.0
        expected = (((1 - mu) ** 3) * 0.0 + 3 * mu * ((1 - mu) ** 2) * 10.0
                    + 3 * mu * mu * (1 - mu) * 10.0 + mu ** 3 * 0.0)
        assert interpolate(BEZIER4, points, x)[0] == pytest.approx(expected)


def test_bezier_n_point_is_de_casteljau():
    points = [(0.0, 0.0), (1.0, 4.0), (2.0, 8.0), (3.0, 2.0), (4.0, 6.0)]

    def reference(x):
        mu = (x - points[0][0]) / (points[-1][0] - points[0][0])
        tmp = [p[1] for p in points]
        for i in range(len(points) - 1, 0, -1):
            for k in range(i):
                tmp[k] = tmp[k] + mu * (tmp[k + 1] - tmp[k])
        return tmp[0]

    for x in (0.0, 1.0, 2.0, 3.3, 4.0):
        assert interpolate(BEZIER, points, x)[0] == pytest.approx(reference(x))


def test_bezier3_is_not_linear():
    # Guards against the Bezier branches silently collapsing to Linear.
    points = [(0.0, 0.0), (5.0, 10.0), (10.0, 0.0)]
    assert interpolate(BEZIER3, points, 5.0)[0] == pytest.approx(5.0)
    assert interpolate(LINEAR, points, 5.0)[0] == pytest.approx(10.0)


def test_empty_curve_is_an_error():
    with pytest.raises(SourceError):
        interpolate(LINEAR, [], 1.0)


# -- Curves store --------------------------------------------------------

def _tiny_snapshot(tmp_path, curve_rows, point_rows):
    write_csv(tmp_path / "Curve.csv", ["ID", "Type", "Flags"], curve_rows)
    write_csv(tmp_path / "CurvePoint.csv",
              ["Pos_0", "Pos_1", "PosPreSquish_0", "PosPreSquish_1", "ID",
               "CurveID", "OrderIndex"], point_rows)
    return Curves(Tables(tmp_path))


def test_points_are_ordered_by_order_index_not_x(tmp_path):
    # OrderIndex 0 has the *larger* X.  Trinity sorts by OrderIndex, so the
    # stored sequence must be non-monotonic in X.
    curves = _tiny_snapshot(
        tmp_path, [[1, 0, 0]],
        [[100.0, 5.0, 0, 0, 1, 1, 0],
         [0.0, 1.0, 0, 0, 2, 1, 1]])
    assert curves.points(1) == [(100.0, 5.0), (0.0, 1.0)]
    assert curves.non_monotonic_curves() == [1]
    # x_range mirrors GetCurveXAxisRange: first and last point, not min/max.
    assert curves.x_range(1) == (100.0, 0.0)


def test_curve_points_without_a_curve_row_are_dropped(tmp_path):
    curves = _tiny_snapshot(
        tmp_path, [[1, 0, 0]],
        [[0.0, 0.0, 0, 0, 1, 1, 0],
         [1.0, 1.0, 0, 0, 2, 1, 1],
         [9.0, 9.0, 0, 0, 3, 999, 0]])
    assert 999 not in curves
    assert curves.value_at(999, 1.0) == 0.0
    assert curves.evaluations[-1].clamped == UNKNOWN_CURVE


def test_unknown_curve_is_recorded_not_raised(tmp_path):
    curves = _tiny_snapshot(tmp_path, [[1, 0, 0]], [[0.0, 0.0, 0, 0, 1, 1, 0]])
    assert curves.value_at(12345, 3.0, consumer="test") == 0.0
    evaluation = curves.evaluations[-1]
    assert evaluation.curve_id == 12345
    assert evaluation.consumer == "test"
    assert evaluation.point_count == 0


def test_evaluation_records_bracketing_points(tmp_path):
    curves = _tiny_snapshot(
        tmp_path, [[7, 0, 0]],
        [[0.0, 0.0, 0, 0, 1, 7, 0],
         [10.0, 20.0, 0, 0, 2, 7, 1],
         [20.0, 30.0, 0, 0, 3, 7, 2]])
    curves.value_at(7, 5.0, consumer="unit-test")
    evaluation = curves.evaluations[-1]
    assert evaluation.bracket == ((0.0, 0.0), (10.0, 20.0))
    assert evaluation.y == pytest.approx(10.0)
    assert evaluation.to_dict()["rounded_y"] == 10
    assert evaluation.mode == "Linear"


def test_mark_and_since_slice_one_resolution(tmp_path):
    curves = _tiny_snapshot(
        tmp_path, [[1, 0, 0]],
        [[0.0, 0.0, 0, 0, 1, 1, 0], [1.0, 1.0, 0, 0, 2, 1, 1]])
    curves.value_at(1, 0.5)
    mark = curves.mark()
    curves.value_at(1, 0.25)
    curves.value_at(1, 0.75)
    assert len(curves.since(mark)) == 2
