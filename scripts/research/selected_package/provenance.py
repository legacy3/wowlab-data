"""entry -> definition -> provider -> ranked exact-effect amounts.

Mirrors: ``TraitSourceCatalog::try_selected_spell_effect_amounts``
(``core/crates/data/src/trait_source/selected_spell.rs``) and
``exact_rank_curve_points`` (``.../amount.rs``), refusal code for refusal code, so a
disagreement between this oracle and Core is a reportable defect in one of them.

One deliberate difference, recorded because it changes what "complete" means:

    Core's ``IncompleteEffects`` check compares the effects its *adapter retained*
    against ``spell_source_effect_count`` -- an ingestion guard.  This oracle reads
    every ``SpellEffect`` row directly, so retention never drops an effect.  The
    research question that check stands for ("is any sibling effect unrepresentable?")
    is therefore answered explicitly in :mod:`selected_package.effects` instead of
    implicitly by a count mismatch.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from functools import cached_property
from typing import Any

from procs.source import Source

from .source import TraitSource, f32

#: ``TraitSpellErrorCode`` (selected_spell.rs:12-24), verbatim.
class TraitSpellError(str, Enum):
    SOURCE_IDENTITY_MISMATCH = "SourceIdentityMismatch"
    MISSING_ENTRY = "MissingEntry"
    MISSING_DEFINITION = "MissingDefinition"
    UNSUPPORTED_RANK = "UnsupportedRank"
    UNSUPPORTED_DEFINITION = "UnsupportedDefinition"
    MISSING_SPELL = "MissingSpell"
    INCOMPLETE_EFFECTS = "IncompleteEffects"
    UNSUPPORTED_EFFECT_TOPOLOGY = "UnsupportedEffectTopology"
    UNSUPPORTED_EFFECT_POINTS = "UnsupportedEffectPoints"
    MISSING_CURVE = "MissingCurve"
    UNSUPPORTED_CURVE = "UnsupportedCurve"
    UNSUPPORTED_CURVE_POINTS = "UnsupportedCurvePoints"


MAX_RANK_SHAPED_EFFECTS = 4
#: ``SelectedTraitPointOperation`` (selected_spell.rs:76-80).
POINT_OPERATIONS = {0: "Set", 1: "Multiply"}

EFFECT_COLUMNS = (
    "ID", "SpellID", "DifficultyID", "EffectIndex", "Effect", "EffectAura",
    "EffectBasePointsF", "EffectAmplitude", "EffectAttributes", "EffectAuraPeriod",
    "EffectBonusCoefficient", "BonusCoefficientFromAP", "Coefficient", "Variance",
    "ResourceCoefficient", "GroupSizeBasePointsCoefficient", "PvpMultiplier",
    "EffectPointsPerResource", "EffectRealPointsPerLevel", "ScalingClass",
    "EffectChainAmplitude", "EffectChainTargets", "EffectItemType", "EffectMechanic",
    "EffectPos_facing", "EffectTriggerSpell",
    "EffectMiscValue_0", "EffectMiscValue_1",
    "EffectRadiusIndex_0", "EffectRadiusIndex_1",
    "EffectSpellClassMask_0", "EffectSpellClassMask_1",
    "EffectSpellClassMask_2", "EffectSpellClassMask_3",
    "ImplicitTarget_0", "ImplicitTarget_1",
)


def u32(value: int) -> int:
    """DB2 writes class-mask words as signed int32; Core reads them unsigned."""
    return int(value) & 0xFFFFFFFF


@dataclass(frozen=True)
class SourceEffect:
    """One ``SpellEffect`` row, with Core's 1-based effect index."""

    row_id: int
    spell: int
    index: int          # 1-based, == Core's SpellEffectRef::effect_index
    csv_index: int      # 0-based, as the CSV and TraitDefinitionEffectPoints hold it
    kind: int
    aura: int
    base_points: float
    period_ms: int
    amplitude: float
    attributes: int
    bonus_coefficient: float
    ap_coefficient: float
    coefficient: float
    variance: float
    resource_coefficient: float
    group_size_coefficient: float
    pvp_multiplier: float
    points_per_resource: float
    real_points_per_level: float
    scaling_class: int
    chain_amplitude: float
    chain_targets: int
    item_type: int
    mechanic: int
    pos_facing: float
    trigger_spell: int
    misc0: int
    misc1: int
    radius0: int
    radius1: int
    class_mask: tuple[int, int, int, int]
    target_a: int
    target_b: int

    @property
    def ref(self) -> str:
        """Core's ``SpellEffectRef`` spelling, ``spell:index``."""
        return f"{self.spell}:{self.index}"

    @property
    def class_mask_is_empty(self) -> bool:
        return not any(self.class_mask)

    def coefficients_are_neutral(self) -> bool:
        """``EffectAmountFacts::NEUTRAL`` + zero AP/SP coefficients (exact_source.rs:196-205)."""
        return (
            self.bonus_coefficient == 0.0
            and self.ap_coefficient == 0.0
            and self.coefficient == 0.0
            and self.variance == 0.0
            and self.resource_coefficient == 0.0
            and self.group_size_coefficient in (0.0, 1.0)
            and self.points_per_resource == 0.0
            and self.real_points_per_level == 0.0
            and self.scaling_class == 0
        )

    @property
    def pvp_multiplier_is_neutral(self) -> bool:
        """Recorded separately: Trinity loads PvpMultiplier but never consumes it."""
        return self.pvp_multiplier in (0.0, 1.0)

    def chain_is_neutral(self) -> bool:
        """``EffectChainFacts::NEUTRAL``."""
        return self.chain_targets == 0 and self.chain_amplitude in (0.0, 1.0)


@dataclass
class SelectedAmount:
    """``SelectedTraitEffectAmount`` -- authored value plus its two rank values."""

    effect: SourceEffect
    authored: float
    rank_one: float
    rank_two: float


@dataclass
class Resolved:
    """``SelectedTraitSpellEffects`` for one entry at one effective rank."""

    entry_id: int
    definition_id: int
    spell: int
    max_ranks: int
    effective_rank: int
    amounts: list[SelectedAmount]
    shaped_index: dict[int, str] = field(default_factory=dict)   # 1-based index -> "Set"/"Multiply"
    point_rows: list[dict[str, Any]] = field(default_factory=list)

    @property
    def effects(self) -> list[SourceEffect]:
        return [amount.effect for amount in self.amounts]

    def amount_at_rank(self, index: int, rank: int) -> float | None:
        if rank == 0 or rank > self.max_ranks:
            return None
        for amount in self.amounts:
            if amount.effect.index == index:
                return amount.rank_one if rank == 1 else amount.rank_two
        return None

    def selected_amount(self, index: int) -> float | None:
        return self.amount_at_rank(index, self.effective_rank)

    def authored(self, index: int) -> float | None:
        for amount in self.amounts:
            if amount.effect.index == index:
                return amount.authored
        return None

    def point_operation(self, index: int) -> str | None:
        return self.shaped_index.get(index)

    def is_rank_shaped(self, index: int) -> bool:
        return index in self.shaped_index


class Provenance:
    """Resolves selected entries, and explains every refusal."""

    def __init__(self, traits: TraitSource | None = None, source: Source | None = None) -> None:
        self.traits = traits or TraitSource(source)
        self.source = self.traits.source

    @cached_property
    def effects_by_spell(self) -> dict[int, list[SourceEffect]]:
        """``SpellID -> effects`` at ``DifficultyID == 0``, sorted by effect index.

        Difficulty 0 only: every selected trait provider in the current census is a
        player passive with no difficulty-specific rows.  A provider that had them
        would fail closed in :meth:`resolve` rather than silently pick one.
        """
        out: dict[int, list[SourceEffect]] = defaultdict(list)
        for row in self.source.project("SpellEffect", EFFECT_COLUMNS):
            r = dict(zip(EFFECT_COLUMNS, row))
            if int(r["DifficultyID"]) != 0:
                continue
            csv_index = int(r["EffectIndex"])
            out[int(r["SpellID"])].append(
                SourceEffect(
                    row_id=int(r["ID"]), spell=int(r["SpellID"]),
                    index=csv_index + 1, csv_index=csv_index,
                    kind=int(r["Effect"]), aura=int(r["EffectAura"]),
                    base_points=f32(r["EffectBasePointsF"]),
                    period_ms=int(r["EffectAuraPeriod"]),
                    amplitude=f32(r["EffectAmplitude"]),
                    attributes=int(r["EffectAttributes"]),
                    bonus_coefficient=f32(r["EffectBonusCoefficient"]),
                    ap_coefficient=f32(r["BonusCoefficientFromAP"]),
                    coefficient=f32(r["Coefficient"]),
                    variance=f32(r["Variance"]),
                    resource_coefficient=f32(r["ResourceCoefficient"]),
                    group_size_coefficient=f32(r["GroupSizeBasePointsCoefficient"]),
                    pvp_multiplier=f32(r["PvpMultiplier"]),
                    points_per_resource=f32(r["EffectPointsPerResource"]),
                    real_points_per_level=f32(r["EffectRealPointsPerLevel"]),
                    scaling_class=int(r["ScalingClass"]),
                    chain_amplitude=f32(r["EffectChainAmplitude"]),
                    chain_targets=int(r["EffectChainTargets"]),
                    item_type=int(r["EffectItemType"]),
                    mechanic=int(r["EffectMechanic"]),
                    pos_facing=f32(r["EffectPos_facing"]),
                    trigger_spell=int(r["EffectTriggerSpell"]),
                    misc0=int(r["EffectMiscValue_0"]), misc1=int(r["EffectMiscValue_1"]),
                    radius0=int(r["EffectRadiusIndex_0"]), radius1=int(r["EffectRadiusIndex_1"]),
                    class_mask=(u32(r["EffectSpellClassMask_0"]), u32(r["EffectSpellClassMask_1"]),
                                u32(r["EffectSpellClassMask_2"]), u32(r["EffectSpellClassMask_3"])),
                    target_a=int(r["ImplicitTarget_0"]), target_b=int(r["ImplicitTarget_1"]),
                )
            )
        for effects in out.values():
            effects.sort(key=lambda e: e.index)
        return dict(out)

    @cached_property
    def difficulty_spells(self) -> set[int]:
        """Providers that carry any ``DifficultyID != 0`` effect row."""
        out = set()
        for row_id, spell, difficulty, *_ in self.source.project(
            "SpellEffect", ("ID", "SpellID", "DifficultyID")
        ):
            if int(difficulty) != 0:
                out.add(int(spell))
        return out

    # -- the port ---------------------------------------------------------
    def rank_curve_points(self, curve_id: int, max_ranks: int):
        """Mirrors: ``exact_rank_curve_points`` (amount.rs:9-40)."""
        curve = self.traits.curves.get(curve_id)
        if curve is None:
            return TraitSpellError.MISSING_CURVE
        if curve.curve_type != 0 or curve.flags != 0:
            return TraitSpellError.UNSUPPORTED_CURVE
        points = self.traits.curve_points.get(curve.id)
        if points is None:
            return TraitSpellError.MISSING_CURVE
        if len(points) != max_ranks or any(
            point.order_index != order or point.x != f32(order + 1)
            for order, point in enumerate(points)
        ):
            return TraitSpellError.UNSUPPORTED_CURVE_POINTS
        return points

    def resolve(self, entry_id: int, effective_rank: int) -> Resolved | TraitSpellError:
        """Mirrors: ``try_selected_spell_effect_amounts`` (selected_spell.rs:200-372)."""
        traits = self.traits
        entry = traits.entry(entry_id)
        if entry is None:
            return TraitSpellError.MISSING_ENTRY
        definition = traits.definition(entry.definition_id)
        if definition is None:
            return TraitSpellError.MISSING_DEFINITION

        if entry.max_ranks not in (1, 2) or effective_rank == 0 or effective_rank > entry.max_ranks:
            return TraitSpellError.UNSUPPORTED_RANK

        if definition.overrides_spell_id or definition.visible_spell_id or entry.trait_subtree_id:
            return TraitSpellError.UNSUPPORTED_DEFINITION

        if not definition.spell_id:
            return TraitSpellError.MISSING_SPELL
        effects = self.effects_by_spell.get(definition.spell_id)
        if not effects:
            return TraitSpellError.MISSING_SPELL
        if definition.spell_id in self.difficulty_spells:
            # Core resolves one canonical effect list; a difficulty-split provider has
            # no single one.  Fail closed rather than pick difficulty 0 silently.
            return TraitSpellError.INCOMPLETE_EFFECTS
        # Effect indices must be the dense 1..n Core's SpellEffectRef assumes.
        if [effect.index for effect in effects] != list(range(1, len(effects) + 1)):
            return TraitSpellError.INCOMPLETE_EFFECTS

        points = traits.points_for_definition(definition.id)
        if not ((entry.max_ranks, len(points)) in {(1, 0)} or
                (entry.max_ranks == 2 and 1 <= len(points) <= 4)):
            return TraitSpellError.UNSUPPORTED_EFFECT_POINTS
        if len(points) > len(effects):
            return TraitSpellError.UNSUPPORTED_EFFECT_POINTS

        amounts = [
            SelectedAmount(effect, effect.base_points, effect.base_points, effect.base_points)
            for effect in effects
        ]
        by_index = {amount.effect.index: amount for amount in amounts}
        pointed: set[int] = set()
        shaped: dict[int, str] = {}
        point_rows = []

        for position, point in enumerate(points):
            index = point.effect_index + 1
            if index > 255 or index < 1:
                return TraitSpellError.UNSUPPORTED_EFFECT_POINTS
            operation = POINT_OPERATIONS.get(point.operation_type)
            if operation is None:
                return TraitSpellError.UNSUPPORTED_EFFECT_POINTS
            if point.effect_index in pointed:
                return TraitSpellError.UNSUPPORTED_EFFECT_POINTS
            pointed.add(point.effect_index)
            amount = by_index.get(index)
            if amount is None:
                return TraitSpellError.UNSUPPORTED_EFFECT_POINTS

            curve = self.rank_curve_points(point.curve_id, entry.max_ranks)
            if isinstance(curve, TraitSpellError):
                return curve

            applied = _apply(operation, amount.authored, curve[0].y)
            if applied is None:
                return TraitSpellError.UNSUPPORTED_EFFECT_POINTS
            amount.rank_one = applied
            if position < MAX_RANK_SHAPED_EFFECTS:
                shaped[index] = operation
            if len(curve) > 1:
                applied = _apply(operation, amount.authored, curve[1].y)
                if applied is None:
                    return TraitSpellError.UNSUPPORTED_EFFECT_POINTS
                amount.rank_two = applied
            point_rows.append({
                "row_id": point.id, "effect_index": index, "operation": operation,
                "curve_id": point.curve_id,
                "curve_points": [[p.x, p.y] for p in curve],
            })

        return Resolved(
            entry_id=entry_id, definition_id=definition.id, spell=definition.spell_id,
            max_ranks=entry.max_ranks, effective_rank=effective_rank, amounts=amounts,
            shaped_index=shaped, point_rows=point_rows,
        )


def _apply(operation: str, authored: float, curve_y: float) -> float | None:
    """Mirrors: ``SelectedTraitPointOperation::apply`` (selected_spell.rs:82-91)."""
    value = curve_y if operation == "Set" else authored * curve_y
    return value if value == value and abs(value) != float("inf") else None
