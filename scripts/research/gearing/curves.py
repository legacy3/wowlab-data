"""Curve evaluation.

Mirrors: ``DB2Manager::GetCurveValueAt`` and the file-local ``DetermineCurveType``
in ``src/server/game/DataStores/DB2Stores.cpp``, plus the curve-point assembly in
``DB2Manager::LoadStores``.

Two properties of the direct consumer are load bearing and easy to get wrong:

* curve points are ordered by ``CurvePoint.OrderIndex``, **not** by X.  Trinity
  sorts by ``OrderIndex`` and the interpolators then scan assuming X is
  non-decreasing in that order.  Six curves in the current snapshot violate
  that assumption; see :func:`non_monotonic_curves`.
* ``Curve.Type`` values 4 and 5 exist in the snapshot but have no case in
  ``DetermineCurveType``, so they fall through to its default (Linear for more
  than one point).  That is reproduced here rather than corrected.

This module deliberately does *not* unify unrelated curve consumers.  It only
evaluates a curve; deciding *which* curve and at what X belongs to the consumer.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Sequence

from . import SourceError
from .tables import Tables

LINEAR = "Linear"
COSINE = "Cosine"
CATMULL_ROM = "CatmullRom"
BEZIER3 = "Bezier3"
BEZIER4 = "Bezier4"
BEZIER = "Bezier"
CONSTANT = "Constant"

#: Clamp/degenerate markers recorded on a :class:`CurveEval`.
CLAMP_BELOW_FIRST = "below-first-point"
CLAMP_ABOVE_LAST = "above-last-point"
CLAMP_BELOW_SECOND = "below-second-point"
CLAMP_ABOVE_PENULTIMATE = "above-penultimate-point"
ZERO_WIDTH = "zero-width-segment"
CONSTANT_ONLY = "constant"
UNKNOWN_CURVE = "unknown-curve"


def round_half_away(value: float) -> int:
    """C's ``std::round``: halfway cases go away from zero.

    Python's built-in ``round`` is banker's rounding, so ``round(0.5) == 0``
    while ``std::round(0.5) == 1``.  Every rounding boundary in the gearing
    path goes through ``std::round``, so this must be used instead.
    """
    if value >= 0:
        return int(math.floor(value + 0.5))
    return -int(math.floor(-value + 0.5))


def determine_curve_type(curve_type: int, point_count: int) -> str:
    """Mirrors: ``DetermineCurveType`` (DB2Stores.cpp).

    Types 0, 4, 5 and anything else reach the ``default`` branch.
    """
    if curve_type == 1:
        return COSINE if point_count < 4 else CATMULL_ROM
    if curve_type == 2:
        if point_count == 1:
            return CONSTANT
        if point_count == 2:
            return LINEAR
        if point_count == 3:
            return BEZIER3
        if point_count == 4:
            return BEZIER4
        return BEZIER
    if curve_type == 3:
        return COSINE
    return LINEAR if point_count != 1 else CONSTANT


Point = tuple[float, float]


def interpolate(
    mode: str, points: Sequence[Point], x: float
) -> tuple[float, list[Point], str | None]:
    """Mirrors: ``DB2Manager::GetCurveValueAt(CurveInterpolationMode, span, float)``.

    Returns ``(y, bracketing_points, clamp_marker)``.  The bracketing points are
    exactly the source points the expression read, so a reviewer can redo the
    arithmetic by hand.
    """
    n = len(points)
    if n == 0:
        raise SourceError("cannot interpolate an empty curve")

    if mode in (LINEAR, COSINE):
        i = 0
        while i < n and points[i][0] <= x:
            i += 1
        if i == 0:
            return points[0][1], [points[0]], CLAMP_BELOW_FIRST
        if i >= n:
            return points[-1][1], [points[-1]], CLAMP_ABOVE_LAST
        x0, y0 = points[i - 1]
        x1, y1 = points[i]
        dx = x1 - x0
        if dx == 0.0:
            return y1, [points[i - 1], points[i]], ZERO_WIDTH
        if mode == LINEAR:
            return ((x - x0) / dx) * (y1 - y0) + y0, [points[i - 1], points[i]], None
        return (
            (y1 - y0) * (1.0 - math.cos((x - x0) / dx * math.pi)) * 0.5 + y0,
            [points[i - 1], points[i]],
            None,
        )

    if mode == CATMULL_ROM:
        if n < 4:
            raise SourceError(
                f"CatmullRom needs at least 4 points, curve has {n}")
        i = 1
        while i < n and points[i][0] <= x:
            i += 1
        if i == 1:
            return points[1][1], [points[1]], CLAMP_BELOW_SECOND
        if i >= n - 1:
            return points[n - 2][1], [points[n - 2]], CLAMP_ABOVE_PENULTIMATE
        x0, y0 = points[i - 1]
        x1, y1 = points[i]
        dx = x1 - x0
        if dx == 0.0:
            return y1, [points[i - 1], points[i]], ZERO_WIDTH
        mu = (x - x0) / dx
        pm2 = points[i - 2][1]
        pp1 = points[i + 1][1]
        a0 = -0.5 * pm2 + 1.5 * y0 - 1.5 * y1 + 0.5 * pp1
        a1 = pm2 - 2.5 * y0 + 2.0 * y1 - 0.5 * pp1
        a2 = -0.5 * pm2 + 0.5 * y1
        a3 = y0
        return (
            a0 * mu * mu * mu + a1 * mu * mu + a2 * mu + a3,
            [points[i - 2], points[i - 1], points[i], points[i + 1]],
            None,
        )

    if mode == BEZIER3:
        if n < 3:
            raise SourceError(f"Bezier3 needs 3 points, curve has {n}")
        dx = points[2][0] - points[0][0]
        if dx == 0.0:
            return points[1][1], list(points[:3]), ZERO_WIDTH
        mu = (x - points[0][0]) / dx
        y = (((1.0 - mu) * (1.0 - mu)) * points[0][1]
             + (1.0 - mu) * 2.0 * mu * points[1][1]
             + mu * mu * points[2][1])
        return y, list(points[:3]), None

    if mode == BEZIER4:
        if n < 4:
            raise SourceError(f"Bezier4 needs 4 points, curve has {n}")
        dx = points[3][0] - points[0][0]
        if dx == 0.0:
            return points[1][1], list(points[:4]), ZERO_WIDTH
        mu = (x - points[0][0]) / dx
        inv = 1.0 - mu
        y = (inv * inv * inv * points[0][1]
             + 3.0 * mu * inv * inv * points[1][1]
             + 3.0 * mu * mu * inv * points[2][1]
             + mu * mu * mu * points[3][1])
        return y, list(points[:4]), None

    if mode == BEZIER:
        dx = points[-1][0] - points[0][0]
        if dx == 0.0:
            return points[-1][1], list(points), ZERO_WIDTH
        mu = (x - points[0][0]) / dx
        tmp = [p[1] for p in points]
        i = n - 1
        while i > 0:
            for k in range(i):
                tmp[k] = tmp[k] + mu * (tmp[k + 1] - tmp[k])
            i -= 1
        return tmp[0], list(points), None

    if mode == CONSTANT:
        return points[0][1], [points[0]], CONSTANT_ONLY

    raise SourceError(f"unknown interpolation mode {mode!r}")


@dataclass(frozen=True)
class CurveEval:
    """One curve evaluation, with everything needed to re-check it by hand."""

    curve_id: int
    curve_type: int
    mode: str
    x: float
    y: float
    point_count: int
    bracket: tuple[Point, ...]
    clamped: str | None
    consumer: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "curve_id": self.curve_id,
            "curve_type": self.curve_type,
            "interpolation": self.mode,
            "x": self.x,
            "raw_y": self.y,
            "rounded_y": round_half_away(self.y),
            "point_count": self.point_count,
            "surrounding_points": [list(p) for p in self.bracket],
            "clamped": self.clamped,
            "consumer": self.consumer,
        }


class Curves:
    """Curve store plus a provenance-recording evaluator."""

    def __init__(self, tables: Tables) -> None:
        self._curves = {row["ID"]: row for row in tables("Curve")}
        grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for row in tables("CurvePoint"):
            # Mirrors: Trinity drops CurvePoints whose CurveID has no Curve row.
            if row["CurveID"] in self._curves:
                grouped[row["CurveID"]].append(row)
        self._points: dict[int, list[Point]] = {}
        for curve_id, rows in grouped.items():
            rows.sort(key=lambda r: r["OrderIndex"])
            self._points[curve_id] = [
                (float(r["Pos_0"]), float(r["Pos_1"])) for r in rows
            ]
        self.evaluations: list[CurveEval] = []

    # -- inspection -----------------------------------------------------
    def __contains__(self, curve_id: int) -> bool:
        return curve_id in self._points

    def known_curve_ids(self) -> set[int]:
        return set(self._curves)

    def curve_ids_with_points(self) -> set[int]:
        return set(self._points)

    def points(self, curve_id: int) -> list[Point]:
        return self._points.get(curve_id, [])

    def curve_type(self, curve_id: int) -> int:
        row = self._curves.get(curve_id)
        return int(row["Type"]) if row else -1

    def mode(self, curve_id: int) -> str:
        row = self._curves.get(curve_id)
        if row is None:
            return CONSTANT
        return determine_curve_type(int(row["Type"]),
                                    len(self._points.get(curve_id, ())))

    def x_range(self, curve_id: int) -> tuple[float, float]:
        """Mirrors: ``DB2Manager::GetCurveXAxisRange`` (first/last point, not min/max)."""
        pts = self._points.get(curve_id)
        if not pts:
            return (0.0, 0.0)
        return (pts[0][0], pts[-1][0])

    def non_monotonic_curves(self) -> list[int]:
        """Curves whose X is not non-decreasing in OrderIndex order.

        The scanning interpolators assume monotonic X, so these curves are a
        known hazard rather than a supported case.
        """
        bad = []
        for curve_id, pts in self._points.items():
            if any(pts[i][0] < pts[i - 1][0] for i in range(1, len(pts))):
                bad.append(curve_id)
        return sorted(bad)

    # -- evaluation -----------------------------------------------------
    def value_at(self, curve_id: int, x: float, consumer: str = "") -> float:
        """Evaluate and record provenance.

        Mirrors: ``DB2Manager::GetCurveValueAt(uint32, float)``.  An unknown
        curve id, or a curve with no points, yields ``0.0`` exactly as Trinity
        does; the evaluation is still recorded with ``clamped='unknown-curve'``
        so the caller can see it happened.
        """
        pts = self._points.get(curve_id)
        if not pts:
            evaluation = CurveEval(
                curve_id=curve_id, curve_type=self.curve_type(curve_id),
                mode="Unknown", x=float(x), y=0.0, point_count=0,
                bracket=(), clamped=UNKNOWN_CURVE, consumer=consumer)
            self.evaluations.append(evaluation)
            return 0.0
        mode = self.mode(curve_id)
        y, bracket, clamped = interpolate(mode, pts, float(x))
        self.evaluations.append(CurveEval(
            curve_id=curve_id, curve_type=self.curve_type(curve_id), mode=mode,
            x=float(x), y=y, point_count=len(pts), bracket=tuple(bracket),
            clamped=clamped, consumer=consumer))
        return y

    def mark(self) -> int:
        """Index into :attr:`evaluations`, for slicing one resolution's curves."""
        return len(self.evaluations)

    def since(self, mark: int) -> list[CurveEval]:
        return self.evaluations[mark:]
