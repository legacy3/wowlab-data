"""Combat-rating accumulation and rating -> percentage conversion.

Mirrors:
* ``Player::ApplyRatingMod`` / ``Player::UpdateRating`` -- accumulation into
  ``ActivePlayerData::CombatRatings[cr]``;
* ``Player::GetRatingMultiplier`` -- ``1 / CombatRatings.txt[level][cr]``;
* ``Player::ApplyRatingDiminishing`` -- the ``GlobalCurve`` lookup per rating;
* ``Player::GetRatingBonusValue`` -- multiplier then diminishing;
* ``Player::UpdateMastery`` (``src/server/game/Entities/Unit/StatSystem.cpp``).

The mastery chain deliberately stops at the generic boundary.  Everything up to
``ActivePlayerData::Mastery`` is generic; what a spec *does* with that number is
``SpellEffectInfo::CalcValue``'s ``value += Mastery * BonusCoefficient`` under
``SPELL_ATTR8_MASTERY_AFFECTS_POINTS``, which is ordinary spell-effect
semantics and is not modelled here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .curves import CurveEval, Curves
from .enums import (
    CR_INDEX,
    CR_NAMES,
    MOD_TO_RATINGS,
    RATING_DIMINISHING_GLOBAL_CURVE,
)
from .tables import Tables

#: Ratings whose CombatRatings.txt column is a placeholder.
UNUSED_RATINGS = frozenset({"Unused7", "Unused12"})


@dataclass
class RatingConversion:
    """One rating amount converted to an effective percentage."""

    rating: str
    combat_ratings_column: int
    amount: float
    per_point: float
    linear_percent: float
    diminishing_curve_id: int | None
    final_percent: float
    curve_evaluation: CurveEval | None = None

    def to_dict(self) -> dict[str, Any]:
        out = {
            "rating": self.rating,
            "combat_ratings_column": self.combat_ratings_column,
            "amount": self.amount,
            "per_point": self.per_point,
            "linear_percent": self.linear_percent,
            "diminishing_curve_id": self.diminishing_curve_id,
            "final_percent": self.final_percent,
        }
        if self.curve_evaluation is not None:
            out["curve"] = self.curve_evaluation.to_dict()
        return out


class RatingEngine:
    def __init__(self, tables: Tables, curves: Curves) -> None:
        self.curves = curves
        self.gt_combat_ratings = tables.gametable("CombatRatings")
        self._global_curve = {int(r["Type"]): int(r["CurveID"])
                              for r in tables("GlobalCurve")}

    def global_curve_id(self, curve_type: int) -> int:
        """Mirrors: ``DB2Manager::GetGlobalCurveId``."""
        return self._global_curve.get(curve_type, 0)

    def rating_multiplier(self, rating: str, player_level: int) -> float:
        """Mirrors: ``Player::GetRatingMultiplier``.

        Returns ``1.0`` both when the level row is missing and when the column
        is zero -- Trinity's "minimum coefficient" fallback.
        """
        row = self.gt_combat_ratings.row(player_level)
        if row is None:
            return 1.0
        value = row[CR_INDEX[rating]]
        if not value:
            return 1.0
        return 1.0 / value

    def apply_diminishing(self, rating: str, bonus_value: float
                          ) -> tuple[float, CurveEval | None]:
        """Mirrors: ``Player::ApplyRatingDiminishing``."""
        curve_type = RATING_DIMINISHING_GLOBAL_CURVE.get(rating)
        if curve_type is None:
            return bonus_value, None
        curve_id = self.global_curve_id(curve_type)
        if not curve_id:
            return bonus_value, None
        mark = self.curves.mark()
        value = self.curves.value_at(
            curve_id, bonus_value,
            consumer=f"Player::ApplyRatingDiminishing/{rating}")
        return value, self.curves.evaluations[mark]

    def convert(self, rating: str, amount: float,
                player_level: int) -> RatingConversion:
        """Mirrors: ``Player::GetRatingBonusValue`` for a single rating.

        ``CR_RESILIENCE_PLAYER_DAMAGE`` has an extra
        ``(1 - 0.99^value) * 100`` step in the direct consumer; it is applied
        here too so the conversion table stays honest.
        """
        per_point = self.rating_multiplier(rating, player_level)
        linear = amount * per_point
        final, evaluation = self.apply_diminishing(rating, linear)
        if rating == "ResiliencePlayerDamage":
            final = (1.0 - pow(0.99, final)) * 100.0
        curve_type = RATING_DIMINISHING_GLOBAL_CURVE.get(rating)
        return RatingConversion(
            rating=rating,
            combat_ratings_column=CR_INDEX[rating],
            amount=amount,
            per_point=per_point,
            linear_percent=linear,
            diminishing_curve_id=(self.global_curve_id(curve_type)
                                  if curve_type is not None else None),
            final_percent=final,
            curve_evaluation=evaluation,
        )

    def convert_all(self, amount: float, player_level: int
                    ) -> list[RatingConversion]:
        return [self.convert(name, amount, player_level)
                for name in CR_NAMES if name not in UNUSED_RATINGS]


def accumulate_ratings(stat_values: list[tuple[int, int]]) -> dict[str, int]:
    """Accumulate ``(ItemModType, rounded amount)`` pairs into rating slots.

    Mirrors: the ``ApplyRatingMod`` calls in ``Player::_ApplyItemBonuses``.
    One ItemModType can feed several slots (``ITEM_MOD_VERSATILITY`` feeds
    three, ``ITEM_MOD_HASTE_RATING`` feeds three), which is why this returns a
    mapping rather than a scalar per stat.
    """
    totals: dict[str, int] = {}
    for stat_type, amount in stat_values:
        for rating in MOD_TO_RATINGS.get(stat_type, ()):
            totals[rating] = totals.get(rating, 0) + amount
    return totals
