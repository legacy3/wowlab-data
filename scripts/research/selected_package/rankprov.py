"""Trait / rank / effect-point *provenance*: which source facts shape an amount.

Mirrors: ``TraitSourceCatalog::try_selected_spell_effect_amounts``
(``core/crates/data/src/trait_source/selected_spell.rs:210-382``) and
``exact_rank_curve_points`` (``core/crates/data/src/trait_source/amount.rs:10-41``).

:mod:`selected_package.provenance` already ports Core's *decision* (resolve or refuse).
This module asks the question one level down, the one a hostile review forced open:

    entry + provider spell is **not** an identity.  Which provenance facts are
    load-bearing, and for what?

Every fact an audit records is tagged into exactly one bucket:

==============  =============================================================
``amount``      changing it changes a resolved effect amount
``acquisition`` changing it changes *whether* Core admits the package at all
``navigation``  reachable-from bookkeeping; dropping it changes no admission
==============  =============================================================

The audit is deliberately *total*: it runs on entries Core refuses too, and records
why, so a refusal can be read as "conservative" or "semantically right" rather than
just counted.  Where Core caps something it does not have to (``max_ranks in 1|2``),
the audit also evaluates the same rule at the entry's own ``MaxRanks`` -- that
extrapolation is always reported separately, under ``generalized``, never mixed into
the Core verdict.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from functools import cached_property, wraps
from typing import Any

from .provenance import MAX_RANK_SHAPED_EFFECTS, POINT_OPERATIONS, Provenance, TraitSpellError
from .source import f32

#: ``TraitPointsOperationType`` (`trinity` DBCEnums.h:2907-2912).  ``None == -1`` means
#: "carry the authored value"; Trinity falls through to ``break`` (SpellInfo.cpp:550-552).
TRINITY_OPERATIONS = {-1: "None", 0: "Set", 1: "Multiply"}

#: The shaping verdict an effect can carry.  ``none`` == unpointed, authored value kept.
SHAPING_VERDICTS = ("Set", "Multiply", "none")

#: Provenance buckets, in the order the report uses them.
FACT_BUCKETS = ("amount", "acquisition", "navigation")

#: Bucket assignment for every provenance fact an audit carries.  ``why`` cites the
#: Core line (or the census finding) that makes the fact load-bearing.
FACT_BUCKET = {
    "entry.id": ("acquisition", "the only key that is 1:1 with a resolved package; "
                 "selected_spell.rs:223 is the lookup root"),
    "entry.MaxRanks": ("amount", "selected_spell.rs:230-238 gates the rank domain and "
                       "amount.rs:26 requires exactly MaxRanks curve points; "
                       "provider 406154 reaches 20 through MaxRanks 2 and only 10 through 1"),
    "entry.NodeEntryType": ("navigation", "never read by selected_spell.rs; admitted values "
                            "span 0,1,2,3,6,8,13 (Phalanx 137000 is 13)"),
    "entry.TraitSubTreeID": ("acquisition", "selected_spell.rs:242 refuses nonzero"),
    "definition.id": ("navigation", "definition -> entry is 1:1 in this snapshot except "
                      "definition 131125; it selects the effect-point rows, but the entry "
                      "already determines it"),
    "definition.SpellID": ("amount", "selected_spell.rs:250 -- the provider whose authored "
                           "base points are shaped"),
    "definition.OverridesSpellID": ("acquisition", "selected_spell.rs:240 refuses nonzero"),
    "definition.VisibleSpellID": ("acquisition", "selected_spell.rs:241 refuses nonzero"),
    "effect_points.row_order": ("acquisition", "selected_spell.rs:301/353 writes "
                                "rank_shaped_effect_indices by enumeration position, so only "
                                "the first 4 rows in row-ID order are retained"),
    "effect_points.EffectIndex": ("amount", "selected_spell.rs:302-341 picks which exact "
                                  "effect the curve shapes"),
    "effect_points.OperationType": ("amount", "selected_spell.rs:309-320 -> Set or Multiply"),
    "effect_points.CurveID": ("amount", "selected_spell.rs:342 -- the rank table itself"),
    "curve.Type": ("acquisition", "amount.rs:19 refuses nonzero; Type 2 is a Bezier control "
                   "hull (`trinity` DB2Stores.cpp DetermineCurveType), not a rank table"),
    "curve.Flags": ("acquisition", "amount.rs:19 refuses nonzero; no referenced curve has any"),
    "curve.point_count": ("acquisition", "amount.rs:26 requires == MaxRanks"),
    "curve.OrderIndex": ("acquisition", "amount.rs:28-34 requires OrderIndex == rank - 1"),
    "curve.Pos_0": ("acquisition", "amount.rs:33 requires Pos_0 == float(rank)"),
    "curve.Pos_1": ("amount", "selected_spell.rs:345-365 -- the shaped value"),
    "provider.base_points": ("amount", "selected_spell.rs:290 authored value; the Multiply "
                             "operand and the value an unpointed sibling keeps"),
    "topology.TraitNodeXTraitNodeEntry": ("navigation", "never read by selected_spell.rs; "
                                          "2,143 resolvable entries have no node row at all"),
}


#: Facts grouped the other way round: bucket -> the facts it owns.
BUCKET_FACTS = {
    bucket: tuple(sorted(f for f, (b, _) in FACT_BUCKET.items() if b == bucket))
    for bucket in FACT_BUCKETS
}


def curve_refusal_verdict(curve: "CurveFacts", max_ranks: int) -> dict[str, Any]:
    """Is refusing this curve semantically right, or merely conservative?

    Conservative iff the curve still carries **exactly one** point at ``Pos_0 == rank``
    for every rank the entry can reach: Core's positional read would then agree with
    Trinity's interpolated ``GetCurveValueAt`` at every integer rank, and only the
    surplus trailing points offend ``amount.rs:26``.
    """
    exact = []
    for rank in range(1, max_ranks + 1):
        matches = [y for (x, y) in curve.points if x == float(rank)]
        exact.append(matches[0] if len(matches) == 1 else None)
    if all(value is not None for value in exact):
        return {
            "verdict": "conservative",
            "why": "the curve has exactly one point at every reachable rank "
                   f"({[float(r) for r in range(1, max_ranks + 1)]} -> {exact}); only the "
                   "surplus points offend amount.rs:26",
            "amounts_if_admitted": exact,
        }
    return {
        "verdict": "semantically-right",
        "why": "no unambiguous point at every reachable rank, so Core's positional read "
               "would disagree with Trinity's interpolated GetCurveValueAt "
               "(`trinity` DB2Stores.cpp:2327-2345)",
        "amounts_if_admitted": None,
    }


def _memoized(method):
    """Per-instance memo for the no-argument census parts (each costs a full sweep)."""
    name = method.__name__

    @wraps(method)
    def wrapper(self):
        memo = self.__dict__.setdefault("_memo", {})
        if name not in memo:
            memo[name] = method(self)
        return memo[name]

    return wrapper


@dataclass(frozen=True)
class CurveFacts:
    """One ``Curve`` row plus its ``CurvePoint`` table, judged against ``amount.rs``."""

    curve_id: int
    curve_type: int
    flags: int
    point_count: int
    #: Exact ``(Pos_0, Pos_1)`` coordinates in ``OrderIndex`` order, f32-round-tripped.
    points: list[tuple[float, float]] = field(default_factory=list)
    order_indices: list[int] = field(default_factory=list)
    #: Why ``exact_rank_curve_points`` refuses this curve for the audited ``MaxRanks``.
    defects: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.defects

    def y_at_rank(self, rank: int) -> float | None:
        """``Pos_1`` of the point whose ``Pos_0`` is exactly ``rank``."""
        for (x, y) in self.points:
            if x == float(rank):
                return y
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "curve_id": self.curve_id,
            "type": self.curve_type,
            "flags": self.flags,
            "point_count": self.point_count,
            "points": [[x, y] for x, y in self.points],
            "order_indices": list(self.order_indices),
            "defects": list(self.defects),
        }


@dataclass(frozen=True)
class PointRowFacts:
    """One ``TraitDefinitionEffectPoints`` row, in row-ID order, with its curve."""

    row_id: int
    definition_id: int
    #: As stored (0-based).  Core adds 1 at selected_spell.rs:302.
    source_effect_index: int
    #: Core's 1-based ``SpellEffectRef`` index.
    effect_index: int
    operation_type: int
    #: ``Set`` / ``Multiply``; ``None``/``unknown-<n>`` for values Core refuses.
    operation: str
    curve_id: int
    curve: CurveFacts | None
    #: Enumeration position; only positions < 4 reach ``rank_shaped_effect_indices``.
    position: int
    defects: list[str] = field(default_factory=list)

    @property
    def retained_by_core(self) -> bool:
        return self.position < MAX_RANK_SHAPED_EFFECTS

    def to_dict(self) -> dict[str, Any]:
        return {
            "row_id": self.row_id,
            "definition_id": self.definition_id,
            "source_effect_index": self.source_effect_index,
            "effect_index": self.effect_index,
            "operation_type": self.operation_type,
            "operation": self.operation,
            "curve_id": self.curve_id,
            "curve": self.curve.to_dict() if self.curve else None,
            "position": self.position,
            "retained_by_core": self.retained_by_core,
            "defects": list(self.defects),
        }


@dataclass(frozen=True)
class EffectShaping:
    """One exact effect of the provider: its authored value and its rank vector."""

    effect_index: int
    effect_row_id: int
    authored: float
    #: ``Set`` / ``Multiply`` / ``none``.
    verdict: str
    point_row_id: int | None
    #: ``rank -> amount`` for every rank in ``1..=MaxRanks`` the audit could resolve.
    amounts: dict[int, float] = field(default_factory=dict)

    @property
    def shaped(self) -> bool:
        return self.verdict != "none"

    def to_dict(self) -> dict[str, Any]:
        return {
            "effect_index": self.effect_index,
            "effect_row_id": self.effect_row_id,
            "authored": self.authored,
            "verdict": self.verdict,
            "point_row_id": self.point_row_id,
            "amounts": {str(r): v for r, v in sorted(self.amounts.items())},
        }


@dataclass(frozen=True)
class RankAudit:
    """Complete rank/effect-point provenance for one ``entry x rank``."""

    entry_id: int
    rank: int
    definition_id: int
    provider: int
    overrides_spell_id: int
    visible_spell_id: int
    trait_subtree_id: int
    max_ranks: int
    node_entry_type: int
    #: ``TraitNodeXTraitNodeEntry`` nodes; empty means a detached entry.
    node_ids: list[int] = field(default_factory=list)
    point_rows: list[PointRowFacts] = field(default_factory=list)
    effects: list[EffectShaping] = field(default_factory=list)
    #: ``unshaped`` / ``set-only`` / ``multiply-only`` / ``mixed-set-multiply``.
    shaping_kind: str = "unshaped"
    #: True when some effect is shaped and some sibling keeps its authored value.
    mixed_shaped_siblings: bool = False
    #: Core's verdict: ``None`` when admitted, else the ``TraitSpellErrorCode`` name.
    refusal: str | None = None
    #: The same rule evaluated at the entry's own ``MaxRanks`` (research extrapolation).
    generalized_ok: bool = False
    defects: list[str] = field(default_factory=list)

    @property
    def admitted(self) -> bool:
        return self.refusal is None

    def amount(self, effect_index: int, rank: int | None = None) -> float | None:
        rank = self.rank if rank is None else rank
        for effect in self.effects:
            if effect.effect_index == effect_index:
                return effect.amounts.get(rank)
        return None

    def selected_amounts(self) -> list[float | None]:
        return [effect.amounts.get(self.rank) for effect in self.effects]

    def to_dict(self) -> dict[str, Any]:
        return {
            "entry_id": self.entry_id,
            "rank": self.rank,
            "definition_id": self.definition_id,
            "provider": self.provider,
            "overrides_spell_id": self.overrides_spell_id,
            "visible_spell_id": self.visible_spell_id,
            "trait_subtree_id": self.trait_subtree_id,
            "max_ranks": self.max_ranks,
            "node_entry_type": self.node_entry_type,
            "node_ids": sorted(self.node_ids),
            "detached": not self.node_ids,
            "point_rows": [row.to_dict() for row in self.point_rows],
            "effects": [effect.to_dict() for effect in self.effects],
            "shaping_kind": self.shaping_kind,
            "mixed_shaped_siblings": self.mixed_shaped_siblings,
            "refusal": self.refusal,
            "admitted": self.admitted,
            "generalized_ok": self.generalized_ok,
            "defects": list(self.defects),
        }


class RankProvenance:
    """Audits rank provenance, and censuses it over the whole entry population."""

    def __init__(self, provenance: Provenance | None = None) -> None:
        self.provenance = provenance or Provenance()
        self.traits = self.provenance.traits
        self._audits: dict[tuple[int, int], RankAudit] = {}

    # -- curves -----------------------------------------------------------
    def curve_facts(self, curve_id: int, max_ranks: int) -> CurveFacts:
        """Mirrors: ``exact_rank_curve_points`` (amount.rs:10-41), defect by defect."""
        curve = self.traits.curves.get(curve_id)
        points = self.traits.curve_points.get(curve_id, [])
        if curve is None:
            return CurveFacts(curve_id, -1, -1, len(points),
                              [(p.x, p.y) for p in points], [p.order_index for p in points],
                              ["missing-curve-row"])
        defects: list[str] = []
        if curve.curve_type != 0:
            defects.append(f"curve-type-{curve.curve_type}")
        if curve.flags != 0:
            defects.append(f"curve-flags-{curve.flags}")
        if not points:
            defects.append("no-curve-points")
        elif len(points) != max_ranks:
            defects.append(f"point-count-{len(points)}-vs-max-ranks-{max_ranks}")
        else:
            for order, point in enumerate(points):
                if point.order_index != order:
                    defects.append(f"order-index-{point.order_index}-at-position-{order}")
                if point.x != f32(order + 1):
                    defects.append(f"pos0-{point.x:g}-at-rank-{order + 1}")
        return CurveFacts(curve.id, curve.curve_type, curve.flags, len(points),
                          [(p.x, p.y) for p in points], [p.order_index for p in points],
                          defects)

    # -- the audit --------------------------------------------------------
    def audit(self, entry_id: int, rank: int) -> RankAudit:
        """Every provenance fact behind ``entry_id`` at ``rank``, admitted or not."""
        cached = self._audits.get((entry_id, rank))
        if cached is None:
            cached = self._audit(entry_id, rank)
            self._audits[(entry_id, rank)] = cached
        return cached

    def _audit(self, entry_id: int, rank: int) -> RankAudit:
        traits = self.traits
        entry = traits.entry(entry_id)
        if entry is None:
            return RankAudit(entry_id, rank, 0, 0, 0, 0, 0, 0, 0,
                             refusal=TraitSpellError.MISSING_ENTRY.value,
                             defects=["no-TraitNodeEntry-row"])
        definition = traits.definition(entry.definition_id)
        nodes = list(traits.nodes_for_entry.get(entry_id, []))
        defects: list[str] = []
        if not nodes:
            defects.append("detached-entry")
        if definition is None:
            return RankAudit(entry_id, rank, entry.definition_id, 0, 0, 0,
                             entry.trait_subtree_id, entry.max_ranks, entry.node_entry_type,
                             node_ids=nodes,
                             refusal=TraitSpellError.MISSING_DEFINITION.value,
                             defects=defects + ["no-TraitDefinition-row"])

        max_ranks = entry.max_ranks
        if max_ranks not in (1, 2):
            defects.append(f"max-ranks-{max_ranks}-outside-core-cap")
        if rank == 0 or (max_ranks and rank > max_ranks):
            defects.append(f"rank-{rank}-outside-1..{max_ranks}")
        if definition.overrides_spell_id:
            defects.append(f"overrides-spell-{definition.overrides_spell_id}")
        if definition.visible_spell_id:
            defects.append(f"visible-spell-{definition.visible_spell_id}")
        if entry.trait_subtree_id:
            defects.append(f"trait-subtree-{entry.trait_subtree_id}")

        provider_effects = self.provenance.effects_by_spell.get(definition.spell_id, [])
        if not definition.spell_id:
            defects.append("definition-spell-id-zero")
        elif not provider_effects:
            defects.append(f"provider-{definition.spell_id}-has-no-SpellEffect-rows")

        rows = traits.points_for_definition(definition.id)
        if max_ranks == 1 and rows:
            defects.append(f"max-ranks-1-with-{len(rows)}-point-rows")
        if max_ranks == 2 and not rows:
            defects.append("max-ranks-2-without-point-rows")
        if len(rows) > MAX_RANK_SHAPED_EFFECTS:
            defects.append(f"{len(rows)}-point-rows-exceeds-{MAX_RANK_SHAPED_EFFECTS}")
        if provider_effects and len(rows) > len(provider_effects):
            defects.append(f"{len(rows)}-point-rows-exceeds-"
                           f"{len(provider_effects)}-provider-effects")

        by_index = {effect.index: effect for effect in provider_effects}
        point_rows: list[PointRowFacts] = []
        shaped: dict[int, PointRowFacts] = {}
        seen: set[int] = set()
        for position, row in enumerate(rows):
            row_defects: list[str] = []
            operation = TRINITY_OPERATIONS.get(row.operation_type,
                                               f"unknown-{row.operation_type}")
            if row.operation_type not in POINT_OPERATIONS:
                row_defects.append(f"operation-type-{row.operation_type}")
            if row.effect_index in seen:
                row_defects.append(f"duplicate-effect-index-{row.effect_index}")
            seen.add(row.effect_index)
            index = row.effect_index + 1
            if provider_effects and index not in by_index:
                row_defects.append(f"foreign-effect-index-{row.effect_index}-of-"
                                   f"{len(provider_effects)}")
            curve = self.curve_facts(row.curve_id, max_ranks) if max_ranks else None
            if curve is not None and curve.defects:
                row_defects.extend(curve.defects)
            if position >= MAX_RANK_SHAPED_EFFECTS:
                row_defects.append(f"row-position-{position}-beyond-core-retention")
            facts = PointRowFacts(row.id, definition.id, row.effect_index, index,
                                  row.operation_type, operation, row.curve_id, curve,
                                  position, row_defects)
            point_rows.append(facts)
            defects.extend(row_defects)
            if index in by_index and row.operation_type in POINT_OPERATIONS \
                    and index not in shaped:
                shaped[index] = facts

        ranks = list(range(1, max_ranks + 1)) if 0 < max_ranks <= 1024 else []
        effects: list[EffectShaping] = []
        for effect in provider_effects:
            facts = shaped.get(effect.index)
            verdict = facts.operation if facts else "none"
            amounts: dict[int, float] = {}
            for candidate in ranks:
                if facts is None:
                    amounts[candidate] = effect.base_points
                    continue
                curve = facts.curve
                y = curve.y_at_rank(candidate) if curve else None
                if y is None:
                    continue
                value = y if verdict == "Set" else effect.base_points * y
                if value == value and abs(value) != float("inf"):
                    amounts[candidate] = value
            effects.append(EffectShaping(effect.index, effect.row_id, effect.base_points,
                                         verdict, facts.row_id if facts else None, amounts))

        operations = {effect.verdict for effect in effects if effect.shaped}
        if not operations:
            kind = "unshaped"
        elif operations == {"Set"}:
            kind = "set-only"
        elif operations == {"Multiply"}:
            kind = "multiply-only"
        else:
            kind = "mixed-set-multiply"
        mixed_siblings = bool(operations) and any(not e.shaped for e in effects)

        resolved = self.provenance.resolve(entry_id, rank)
        refusal = resolved.value if isinstance(resolved, TraitSpellError) else None
        generalized_ok = bool(effects) and not [
            d for d in defects
            if not d.startswith(("max-ranks-", "rank-", "detached-entry"))
        ] and all(
            row.curve is not None and not row.curve.defects for row in point_rows
        ) and bool(ranks)

        return RankAudit(
            entry_id=entry_id, rank=rank, definition_id=definition.id,
            provider=definition.spell_id,
            overrides_spell_id=definition.overrides_spell_id,
            visible_spell_id=definition.visible_spell_id,
            trait_subtree_id=entry.trait_subtree_id,
            max_ranks=max_ranks, node_entry_type=entry.node_entry_type,
            node_ids=nodes, point_rows=point_rows, effects=effects,
            shaping_kind=kind, mixed_shaped_siblings=mixed_siblings,
            refusal=refusal, generalized_ok=generalized_ok, defects=defects,
        )

    # -- population -------------------------------------------------------
    @cached_property
    def resolvable(self) -> list[int]:
        """The 8,279 entries Core resolves, ascending."""
        return sorted(
            entry_id for entry_id in self.traits.entries
            if not isinstance(self.provenance.resolve(entry_id, 1), TraitSpellError)
        )

    @cached_property
    def resolvable_set(self) -> frozenset[int]:
        return frozenset(self.resolvable)

    @cached_property
    def variants(self) -> list[tuple[int, int]]:
        """Every admitted ``(entry, rank)``; 8,846 of them."""
        return [(entry_id, rank)
                for entry_id in self.resolvable
                for rank in range(1, self.traits.entries[entry_id].max_ranks + 1)]

    def census(self) -> dict[str, Any]:
        """The full provenance census over every resolvable entry."""
        return {
            "population": self.census_population(),
            "shaping": self.census_shaping(),
            "curves": self.census_curves(),
            "effect_points": self.census_effect_points(),
            "operation_types": self.census_operation_types(),
            "rank_domain": self.census_rank_domain(),
            "identity": self.census_identity(),
            "fact_buckets": {
                fact: {"bucket": bucket, "why": why}
                for fact, (bucket, why) in sorted(FACT_BUCKET.items())
            },
        }

    # -- census parts -----------------------------------------------------
    @_memoized
    def census_population(self) -> dict[str, Any]:
        refusals = Counter()
        for entry_id in self.traits.entries:
            verdict = self.provenance.resolve(entry_id, 1)
            if isinstance(verdict, TraitSpellError):
                refusals[verdict.value] += 1
        return {
            "trait_node_entry_rows": len(self.traits.entries),
            "resolvable_entries": len(self.resolvable),
            "entry_rank_variants": len(self.variants),
            "refusals": dict(sorted(refusals.items())),
            "detached_entries": sum(1 for e in self.traits.entries
                                    if e not in self.traits.nodes_for_entry),
            "detached_and_resolvable": sum(1 for e in self.resolvable
                                           if e not in self.traits.nodes_for_entry),
        }

    @_memoized
    def census_shaping(self) -> dict[str, Any]:
        kinds: Counter = Counter()
        examples: dict[str, list[int]] = defaultdict(list)
        siblings: Counter = Counter()
        sibling_examples: dict[str, list[int]] = defaultdict(list)
        for entry_id in self.resolvable:
            audit = self.audit(entry_id, 1)
            kinds[audit.shaping_kind] += 1
            examples[audit.shaping_kind].append(entry_id)
            if audit.shaping_kind != "unshaped":
                key = "mixed-shaped-unshaped" if audit.mixed_shaped_siblings else "all-shaped"
                siblings[key] += 1
                sibling_examples[key].append(entry_id)
        return {
            "kinds": dict(sorted(kinds.items())),
            "kind_examples": {k: sorted(v)[:20] for k, v in sorted(examples.items())},
            "siblings": dict(sorted(siblings.items())),
            "sibling_examples": {k: sorted(v)[:20] for k, v in sorted(sibling_examples.items())},
            "witness": self._improved_vivify(),
        }

    def _improved_vivify(self) -> dict[str, Any]:
        """Improved Vivify 101510: shaped and unshaped siblings disagree at rank 1."""
        audit = self.audit(101510, 1)
        return {
            "entry_id": audit.entry_id,
            "note": "effect 1 is Set-shaped 20 -> 40; effect 2 keeps its authored 40, so the "
                    "two siblings carry different values at rank 1 (20 and 40) and the same "
                    "value at rank 2 (40 and 40)",
            "core_hardcodes_this_as": "has_improved_vivify_amounts",
            "audit": audit.to_dict(),
        }

    @_memoized
    def census_curves(self) -> dict[str, Any]:
        referenced: dict[int, list[int]] = defaultdict(list)
        for definition_id, rows in self.traits.effect_points.items():
            for row in rows:
                referenced[row.curve_id].append(definition_id)
        types: Counter = Counter()
        flags: Counter = Counter()
        counts: Counter = Counter()
        failures: list[dict[str, Any]] = []
        for curve_id in sorted(referenced):
            curve = self.traits.curves.get(curve_id)
            points = self.traits.curve_points.get(curve_id, [])
            types[curve.curve_type if curve else -1] += 1
            flags[curve.flags if curve else -1] += 1
            counts[len(points)] += 1
        for entry_id in sorted(self.traits.entries):
            entry = self.traits.entries[entry_id]
            if entry.max_ranks not in (1, 2):
                continue
            verdict = self.provenance.resolve(entry_id, 1)
            if verdict not in (TraitSpellError.UNSUPPORTED_CURVE_POINTS,
                               TraitSpellError.UNSUPPORTED_CURVE,
                               TraitSpellError.MISSING_CURVE):
                continue
            audit = self.audit(entry_id, 1)
            for row in audit.point_rows:
                if row.curve is not None and row.curve.defects:
                    failures.append({
                        "entry_id": entry_id, "definition_id": audit.definition_id,
                        "point_row_id": row.row_id, "max_ranks": audit.max_ranks,
                        "refusal": verdict.value, "curve": row.curve.to_dict(),
                        "reason": "; ".join(row.curve.defects),
                        **curve_refusal_verdict(row.curve, audit.max_ranks),
                    })
        return {
            "referenced_curves": len(referenced),
            "type_distribution": {str(k): v for k, v in sorted(types.items())},
            "flags_distribution": {str(k): v for k, v in sorted(flags.items())},
            "point_count_distribution": {str(k): v for k, v in sorted(counts.items())},
            "non_rank_table_curves": [
                {"curve_id": c, "type": self.traits.curves[c].curve_type,
                 "flags": self.traits.curves[c].flags,
                 "points": [[p.x, p.y] for p in self.traits.curve_points.get(c, [])],
                 "definitions": sorted(set(referenced[c]))[:20],
                 "trinity_interpolation": "Bezier3 (DetermineCurveType, Type 2 + 3 points)",
                 "verdict": "semantically right: a Bezier control hull is not a rank table"}
                for c in sorted(referenced)
                if self.traits.curves.get(c) and
                (self.traits.curves[c].curve_type != 0 or self.traits.curves[c].flags != 0)
            ],
            "unsupported_curve_point_refusals": failures,
            "unsupported_curve_point_entries": sorted({f["entry_id"] for f in failures}),
        }

    @_memoized
    def census_effect_points(self) -> dict[str, Any]:
        duplicate: list[dict[str, Any]] = []
        foreign: list[dict[str, Any]] = []
        overlong: list[dict[str, Any]] = []
        for definition_id, rows in sorted(self.traits.effect_points.items()):
            definition = self.traits.definitions.get(definition_id)
            counts = Counter(row.effect_index for row in rows)
            repeated = sorted(i for i, n in counts.items() if n > 1)
            if repeated:
                duplicate.append({
                    "definition_id": definition_id, "repeated_effect_indices": repeated,
                    "row_ids": [row.id for row in rows],
                    "entries": sorted(self.traits.entries_for_definition.get(definition_id, [])),
                })
            if definition is None or not definition.spell_id:
                continue
            effects = self.provenance.effects_by_spell.get(definition.spell_id, [])
            if not effects:
                continue
            bad = [row.id for row in rows if row.effect_index + 1 > len(effects)]
            if bad:
                foreign.append({
                    "definition_id": definition_id, "provider": definition.spell_id,
                    "provider_effect_count": len(effects), "row_ids": bad,
                    "entries": sorted(self.traits.entries_for_definition.get(definition_id, [])),
                })
            if len(rows) > len(effects):
                overlong.append({
                    "definition_id": definition_id, "provider": definition.spell_id,
                    "point_rows": len(rows), "provider_effect_count": len(effects),
                    "entries": sorted(self.traits.entries_for_definition.get(definition_id, [])),
                })
        causes: Counter = Counter()
        cause_examples: dict[str, list[int]] = defaultdict(list)
        for entry_id in sorted(self.traits.entries):
            if self.provenance.resolve(entry_id, 1) is not \
                    TraitSpellError.UNSUPPORTED_EFFECT_POINTS:
                continue
            audit = self.audit(entry_id, 1)
            key = "+".join(sorted({_normalize(d) for d in audit.defects
                                   if _normalize(d) in EFFECT_POINT_CAUSES}))
            causes[key or "unclassified"] += 1
            cause_examples[key or "unclassified"].append(entry_id)
        return {
            "point_rows": sum(len(v) for v in self.traits.effect_points.values()),
            "pointed_definitions": len(self.traits.effect_points),
            "rows_per_definition": {
                str(k): v for k, v in
                sorted(Counter(len(v) for v in self.traits.effect_points.values()).items())},
            "duplicate_effect_index_definitions": duplicate,
            "foreign_row_definitions": foreign,
            "more_rows_than_effects_definitions": overlong,
            "unsupported_effect_points_causes": dict(sorted(causes.items())),
            "unsupported_effect_points_examples": {
                k: sorted(v)[:20] for k, v in sorted(cause_examples.items())},
        }

    @_memoized
    def census_operation_types(self) -> dict[str, Any]:
        seen: Counter = Counter()
        rows: list[dict[str, Any]] = []
        for definition_id, points in sorted(self.traits.effect_points.items()):
            for row in points:
                seen[row.operation_type] += 1
                if row.operation_type in POINT_OPERATIONS:
                    continue
                definition = self.traits.definitions.get(definition_id)
                rows.append({
                    "row_id": row.id, "definition_id": definition_id,
                    "operation_type": row.operation_type,
                    "trinity_name": TRINITY_OPERATIONS.get(row.operation_type, "?"),
                    "effect_index": row.effect_index, "curve_id": row.curve_id,
                    "provider": definition.spell_id if definition else 0,
                    "entries": sorted(self.traits.entries_for_definition.get(definition_id, [])),
                })
        return {
            "distribution": {str(k): v for k, v in sorted(seen.items())},
            "beyond_set_and_multiply": rows,
            "trinity": {
                "enum": "TraitPointsOperationType { None = -1, Set = 0, Multiply = 1 }",
                "enum_at": "TrinityCore/src/server/game/DataStores/DBCEnums.h:2907-2912",
                "consumer_at": "TrinityCore/src/server/game/Spells/SpellInfo.cpp:536-556",
                "behaviour": "case TraitPointsOperationType::None: default: break; -- the "
                             "authored value is kept unchanged (SpellInfo.cpp:550-552)",
                "core_verdict": "conservative: Core refuses with UnsupportedEffectPoints, but "
                                "the source meaning of -1 is 'no shaping', which Core already "
                                "represents for an unpointed effect",
            },
        }

    @_memoized
    def census_rank_domain(self) -> dict[str, Any]:
        everything = Counter(e.max_ranks for e in self.traits.entries.values())
        resolvable = Counter(self.traits.entries[e].max_ranks for e in self.resolvable)
        beyond = [e for e, entry in self.traits.entries.items() if entry.max_ranks >= 3]
        would_pass = []
        unshaped_beyond = []
        for entry_id in sorted(beyond):
            audit = self.audit(entry_id, 1)
            if not audit.generalized_ok:
                continue
            row = {"entry_id": entry_id, "max_ranks": audit.max_ranks,
                   "definition_id": audit.definition_id, "provider": audit.provider,
                   "point_rows": [r.to_dict() for r in audit.point_rows]}
            # Only an entry with a real rank table proves the source authors >2 ranks;
            # an entry with no point rows at all would resolve to its authored values at
            # every rank, which says nothing about the rank domain.
            (would_pass if audit.point_rows else unshaped_beyond).append(row)
        return {
            "max_ranks_all_rows": {str(k): v for k, v in sorted(everything.items())},
            "max_ranks_resolvable": {str(k): v for k, v in sorted(resolvable.items())},
            "rows_with_max_ranks_ge_3": len(beyond),
            "largest_max_ranks": max(everything),
            "node_entry_type_all_rows": {
                str(k): v for k, v in
                sorted(Counter(e.node_entry_type for e in self.traits.entries.values()).items())},
            "node_entry_type_resolvable": {
                str(k): v for k, v in
                sorted(Counter(self.traits.entries[e].node_entry_type
                               for e in self.resolvable).items())},
            "would_pass_if_cap_lifted": would_pass,
            "would_pass_but_unshaped": [r["entry_id"] for r in unshaped_beyond],
            "verdict": "Core cap, not a source boundary: "
                       f"{len(would_pass)} entries with MaxRanks >= 3 carry a real rank table "
                       "and satisfy "
                       "exact_rank_curve_points verbatim at their own MaxRanks -- their curves "
                       "carry exactly MaxRanks points with OrderIndex == rank - 1 and "
                       "Pos_0 == rank, up to MaxRanks 90",
        }

    @_memoized
    def census_identity(self) -> dict[str, Any]:
        rows = []
        for entry_id, rank in self.variants:
            audit = self.audit(entry_id, rank)
            rows.append((entry_id, audit.definition_id, audit.provider, rank,
                         self._signature(audit)))
        candidates = {
            "(entry)": lambda r: (r[0],),
            "(entry,rank)": lambda r: (r[0], r[3]),
            "(definition)": lambda r: (r[1],),
            "(definition,rank)": lambda r: (r[1], r[3]),
            "(entry,definition,rank)": lambda r: (r[0], r[1], r[3]),
            "(provider)": lambda r: (r[2],),
            "(provider,rank)": lambda r: (r[2], r[3]),
            "(entry,definition,provider,rank)": lambda r: (r[0], r[1], r[2], r[3]),
        }
        out: dict[str, Any] = {}
        for name, key in candidates.items():
            buckets: dict[tuple, set] = defaultdict(set)
            members: dict[tuple, list] = defaultdict(list)
            for row in rows:
                buckets[key(row)].add(row[4])
                members[key(row)].append(row[:4])
            colliding = sorted(k for k, v in buckets.items() if len(v) > 1)
            witness = None
            if colliding:
                chosen = colliding[0]
                witness = [{"entry_id": m[0], "definition_id": m[1], "provider": m[2],
                            "rank": m[3]} for m in members[chosen]][:4]
            out[name] = {
                "keys": len(buckets),
                "variants": len(rows),
                "colliding_keys": len(colliding),
                "sufficient": not colliding,
                "lossy": len(buckets) < len(rows),
                "witness": witness,
            }
        return {
            "candidates": out,
            "minimal_sufficient_tuple": "(entry, rank)",
            "definitions_with_multiple_entries": self._shared_definitions(),
            "providers_with_multiple_definitions": self._shared_providers(),
        }

    @staticmethod
    def _signature(audit: RankAudit) -> tuple:
        """Everything that distinguishes one resolved package from another."""
        return (
            audit.provider, audit.max_ranks,
            tuple((e.effect_index, e.authored, e.verdict, e.amounts.get(audit.rank))
                  for e in audit.effects),
        )

    def _shared_definitions(self) -> dict[str, Any]:
        shared = []
        for definition_id, entries in sorted(self.traits.entries_for_definition.items()):
            if len(entries) < 2 or definition_id not in self.traits.definitions:
                continue
            rows = [self.traits.entries[e] for e in sorted(entries)]
            shared.append({
                "definition_id": definition_id,
                "entries": sorted(entries),
                "max_ranks": sorted({r.max_ranks for r in rows}),
                "node_entry_types": sorted({r.node_entry_type for r in rows}),
                "trait_subtree_ids": sorted({r.trait_subtree_id for r in rows}),
                "differ_in": sorted(
                    name for name, values in (
                        ("MaxRanks", {r.max_ranks for r in rows}),
                        ("NodeEntryType", {r.node_entry_type for r in rows}),
                        ("TraitSubTreeID", {r.trait_subtree_id for r in rows}),
                    ) if len(values) > 1),
                "nodes": {str(e): sorted(self.traits.nodes_for_entry.get(e, []))
                          for e in sorted(entries)},
            })
        return {"count": len(shared), "rows": shared}

    def _shared_providers(self) -> dict[str, Any]:
        differing: list[dict[str, Any]] = []
        same = 0
        for provider, definitions in sorted(self.traits.definitions_for_spell.items()):
            if len(definitions) < 2:
                continue
            shapes = {}
            for definition_id in sorted(definitions):
                rows = self.traits.points_for_definition(definition_id)
                shapes[definition_id] = tuple(
                    (r.effect_index, r.operation_type, r.curve_id) for r in rows)
            if len(set(shapes.values())) == 1:
                same += 1
                continue
            amounts: dict[tuple, list[int]] = defaultdict(list)
            for definition_id in sorted(definitions):
                for entry_id in sorted(self.traits.entries_for_definition.get(definition_id, [])):
                    if entry_id not in self.resolvable_set:
                        continue
                    audit = self.audit(entry_id, 1)
                    amounts[tuple((e.effect_index, e.amounts.get(1))
                                  for e in audit.effects)].append(entry_id)
            differing.append({
                "provider": provider,
                "definitions": sorted(definitions),
                "point_row_shapes": {str(k): [list(t) for t in v] for k, v in shapes.items()},
                "rank_one_amount_classes": [
                    {"amounts": [list(pair) for pair in k], "entries": v}
                    for k, v in amounts.items()],
                "amounts_differ": len(amounts) > 1,
            })
        return {
            "providers_with_multiple_definitions": same + len(differing),
            "same_shaping": same,
            "differing_shaping": len(differing),
            "differing_rank_one_amounts": sum(1 for d in differing if d["amounts_differ"]),
            "rows": [d for d in differing if d["amounts_differ"]][:20],
        }


#: Defect prefixes that decompose ``UnsupportedEffectPoints``.
EFFECT_POINT_CAUSES = frozenset({
    "max-ranks-1-with-point-rows",
    "max-ranks-2-without-point-rows",
    "point-rows-exceed-retention",
    "point-rows-exceed-provider-effects",
    "operation-type",
    "duplicate-effect-index",
    "foreign-effect-index",
})


def _normalize(defect: str) -> str:
    """Collapse a defect string to its cause family."""
    if defect.startswith("max-ranks-1-with-"):
        return "max-ranks-1-with-point-rows"
    if defect == "max-ranks-2-without-point-rows":
        return defect
    if defect.endswith(f"-point-rows-exceeds-{MAX_RANK_SHAPED_EFFECTS}"):
        return "point-rows-exceed-retention"
    if "-point-rows-exceeds-" in defect and defect.endswith("-provider-effects"):
        return "point-rows-exceed-provider-effects"
    if defect.startswith("operation-type-"):
        return "operation-type"
    if defect.startswith("duplicate-effect-index-"):
        return "duplicate-effect-index"
    if defect.startswith("foreign-effect-index-"):
        return "foreign-effect-index"
    return defect


# ---------------------------------------------------------------------------
# negative witnesses
# ---------------------------------------------------------------------------

#: One negative-witness class: what the source says, what Core does, and whether the
#: refusal is *semantically right* (the source genuinely does not carry the fact) or
#: merely *conservative* (the source carries it; Core declines to read it).
@dataclass(frozen=True)
class NegativeClass:
    key: str
    description: str
    table: str
    core_code: str
    verdict: str
    rationale: str
    rows: list[dict[str, Any]] = field(default_factory=list)
    count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "description": self.description,
            "source_table": self.table,
            "core_code": self.core_code,
            "verdict": self.verdict,
            "rationale": self.rationale,
            "count": self.count,
            "rows": self.rows,
        }


#: How many example rows each negative class carries in the corpus.
NEGATIVE_SAMPLE = 20


class RankNegatives:
    """The negative-witness population behind every rank/effect-point refusal."""

    def __init__(self, rankprov: RankProvenance | None = None) -> None:
        self.rp = rankprov or RankProvenance()
        self.traits = self.rp.traits
        self.provenance = self.rp.provenance

    def _refused(self, code: TraitSpellError) -> list[int]:
        return sorted(e for e in self.traits.entries
                      if self.provenance.resolve(e, 1) is code)

    def classes(self) -> list[NegativeClass]:
        traits = self.traits
        out: list[NegativeClass] = []

        detached = sorted(e for e in traits.entries if e not in traits.nodes_for_entry)
        resolvable_detached = [e for e in detached if e in self.rp.resolvable_set]
        out.append(NegativeClass(
            key="detached-entry",
            description="TraitNodeEntry row with no TraitNodeXTraitNodeEntry row: no node "
                        "grants it, so no player can acquire it",
            table="TraitNodeXTraitNodeEntry (absent)",
            core_code="none -- Core admits these",
            verdict="admitted-but-unproved",
            rationale="selected_spell.rs never reads topology, so 2,143 resolvable entries "
                      "with no granting node resolve exactly like granted ones; the source "
                      "fact 'is reachable' is simply not consulted",
            count=len(detached),
            rows=[{"entry_id": e, "definition_id": traits.entries[e].definition_id,
                   "max_ranks": traits.entries[e].max_ranks,
                   "resolvable": e in self.rp.resolvable_set}
                  for e in resolvable_detached[:NEGATIVE_SAMPLE]],
        ))

        missing = self._refused(TraitSpellError.MISSING_DEFINITION)
        out.append(NegativeClass(
            key="foreign-definition",
            description="TraitNodeEntry.TraitDefinitionID has no TraitDefinition row",
            table="TraitNodeEntry -> TraitDefinition (absent)",
            core_code="MissingDefinition",
            verdict="semantically-right",
            rationale="every one of these carries TraitDefinitionID == 0, i.e. the entry "
                      "names no definition at all; there is no provider to resolve",
            count=len(missing),
            rows=[{"entry_id": e, "trait_definition_id": traits.entries[e].definition_id,
                   "trait_subtree_id": traits.entries[e].trait_subtree_id,
                   "max_ranks": traits.entries[e].max_ranks}
                  for e in missing[:NEGATIVE_SAMPLE]],
        ))

        probes = []
        for entry_id in (101510, 117409, 115483, 137000, 136808):
            entry = traits.entries[entry_id]
            for rank in (0, entry.max_ranks + 1):
                verdict = self.provenance.resolve(entry_id, rank)
                probes.append({"entry_id": entry_id, "max_ranks": entry.max_ranks,
                               "rank": rank,
                               "refusal": verdict.value if isinstance(verdict, TraitSpellError)
                               else None})
        out.append(NegativeClass(
            key="rank-outside-domain",
            description="effective rank 0, or greater than the entry's MaxRanks",
            table="TraitNodeEntry.MaxRanks",
            core_code="UnsupportedRank",
            verdict="semantically-right",
            rationale="selected_spell.rs:230-238; a rank outside 1..=MaxRanks has no curve "
                      "point and therefore no authored amount",
            count=len(probes),
            rows=probes,
        ))

        beyond = sorted(e for e, entry in traits.entries.items() if entry.max_ranks >= 3)
        shaped_beyond = [r["entry_id"] for r in
                         self.rp.census_rank_domain()["would_pass_if_cap_lifted"]]
        out.append(NegativeClass(
            key="max-ranks-beyond-core-cap",
            description="TraitNodeEntry.MaxRanks not in {1, 2}",
            table="TraitNodeEntry.MaxRanks",
            core_code="UnsupportedRank",
            verdict="conservative",
            rationale="24 of these carry a curve with exactly MaxRanks points at "
                      "OrderIndex == rank - 1 and Pos_0 == rank -- the identical shape "
                      "amount.rs already accepts, only longer; the {1,2} limit is a Core "
                      "cap, not a source boundary",
            count=len(beyond),
            rows=[{"entry_id": e, "max_ranks": traits.entries[e].max_ranks,
                   "definition_id": traits.entries[e].definition_id,
                   "carries_rank_table": e in set(shaped_beyond)}
                  for e in shaped_beyond[:NEGATIVE_SAMPLE]],
        ))

        overrides, visible = [], []
        for entry_id in self._refused(TraitSpellError.UNSUPPORTED_DEFINITION):
            entry = traits.entries[entry_id]
            definition = traits.definitions[entry.definition_id]
            row = {"entry_id": entry_id, "definition_id": definition.id,
                   "provider": definition.spell_id,
                   "overrides_spell_id": definition.overrides_spell_id,
                   "visible_spell_id": definition.visible_spell_id,
                   "trait_subtree_id": entry.trait_subtree_id}
            if definition.overrides_spell_id:
                overrides.append(row)
            if definition.visible_spell_id:
                visible.append(row)
        out.append(NegativeClass(
            key="overrides-spell-id-nonzero",
            description="TraitDefinition.OverridesSpellID != 0: the definition replaces "
                        "another spell rather than carrying its own package",
            table="TraitDefinition.OverridesSpellID",
            core_code="UnsupportedDefinition",
            verdict="semantically-right",
            rationale="selected_spell.rs:240; the resolved package would not be the "
                      "provider's own effect set, and Core models no override relation",
            count=len(overrides),
            rows=overrides[:NEGATIVE_SAMPLE],
        ))
        out.append(NegativeClass(
            key="visible-spell-id-nonzero",
            description="TraitDefinition.VisibleSpellID != 0: the tooltip spell differs "
                        "from the provider",
            table="TraitDefinition.VisibleSpellID",
            core_code="UnsupportedDefinition",
            verdict="conservative",
            rationale="VisibleSpellID is a presentation field; 107 entries are refused for "
                      "it alone even though the provider's effect rows are unaffected. "
                      "Refusing is safe but it is a *display* fact, not a semantic one",
            count=len(visible),
            rows=visible[:NEGATIVE_SAMPLE],
        ))

        subtree = sorted(e for e, entry in traits.entries.items() if entry.trait_subtree_id)
        out.append(NegativeClass(
            key="trait-subtree-id-nonzero",
            description="TraitNodeEntry.TraitSubTreeID != 0 (hero-talent sub-tree entry)",
            table="TraitNodeEntry.TraitSubTreeID",
            core_code="UnsupportedDefinition (branch is dead in this snapshot)",
            verdict="unreachable",
            rationale="all 140 such entries also carry TraitDefinitionID == 0, so "
                      "MissingDefinition (selected_spell.rs:226) fires first and the "
                      "TraitSubTreeID branch at selected_spell.rs:242 is never the reason",
            count=len(subtree),
            rows=[{"entry_id": e, "trait_subtree_id": traits.entries[e].trait_subtree_id,
                   "definition_id": traits.entries[e].definition_id,
                   "actual_refusal": self.provenance.resolve(e, 1).value}
                  for e in subtree[:NEGATIVE_SAMPLE]],
        ))

        curve_rows = self.rp.census_curves()["unsupported_curve_point_refusals"]
        conservative = [r for r in curve_rows if r["verdict"] == "conservative"]
        out.append(NegativeClass(
            key="malformed-rank-curve",
            description="a Set/Multiply curve that is not an exact rank table",
            table="Curve / CurvePoint",
            core_code="UnsupportedCurvePoints",
            verdict="mixed",
            rationale=f"{len(curve_rows)} defective point rows across "
                      f"{len(self.rp.census_curves()['unsupported_curve_point_entries'])} "
                      f"entries. {len(conservative)} are **conservative**: a 3-point rank "
                      "curve on a MaxRanks-2 entry (curves 54420, 54421, 54425, 54426, 58141) "
                      "still carries exactly one point at Pos_0 == 1 and Pos_0 == 2, so both "
                      "reachable ranks have an unambiguous authored value and only the "
                      "surplus third point offends amount.rs:26. The rest are "
                      "**semantically right**: a curve indexed from rank 0 "
                      "([(0,1),(2,2)] -- curves 92965, 96288, 80165) has no point at rank 1 "
                      "at all, so Core's positional read would return the rank-0 value while "
                      "Trinity's linear GetCurveValueAt returns 1.5; and curve 60382 carries "
                      "two points at Pos_0 == 1, which is ambiguous outright",
            count=len(curve_rows),
            rows=curve_rows[:NEGATIVE_SAMPLE],
        ))

        non_rank = self.rp.census_curves()["non_rank_table_curves"]
        out.append(NegativeClass(
            key="non-rank-table-curve",
            description="Curve.Type != 0 referenced by a trait definition",
            table="Curve.Type",
            core_code="UnsupportedCurve (unreached: those entries fail earlier)",
            verdict="semantically-right",
            rationale="Type 2 with 3 points is a Bezier3 control hull in Trinity "
                      "(DB2Stores.cpp DetermineCurveType); its points are not rank samples",
            count=len(non_rank),
            rows=non_rank,
        ))

        points = self.rp.census_effect_points()
        out.append(NegativeClass(
            key="duplicate-effect-point-rows",
            description="two TraitDefinitionEffectPoints rows for the same EffectIndex",
            table="TraitDefinitionEffectPoints.EffectIndex",
            core_code="UnsupportedEffectPoints",
            verdict="semantically-right",
            rationale="selected_spell.rs:322-331; the source is ambiguous and Trinity "
                      "silently takes the first match (std::ranges::find, "
                      "SpellInfo.cpp:538), which is order-dependent, not authoritative",
            count=len(points["duplicate_effect_index_definitions"]),
            rows=points["duplicate_effect_index_definitions"],
        ))
        out.append(NegativeClass(
            key="foreign-effect-point-rows",
            description="a point row whose EffectIndex exceeds the provider's effect count",
            table="TraitDefinitionEffectPoints.EffectIndex",
            core_code="UnsupportedEffectPoints",
            verdict="semantically-right",
            rationale="selected_spell.rs:333-341; the row shapes an effect the provider "
                      "does not have -- build skew between the definition and the spell",
            count=len(points["foreign_row_definitions"]),
            rows=points["foreign_row_definitions"][:NEGATIVE_SAMPLE],
        ))
        out.append(NegativeClass(
            key="more-point-rows-than-effects",
            description="a definition with more point rows than the provider has effects",
            table="TraitDefinitionEffectPoints",
            core_code="UnsupportedEffectPoints",
            verdict="semantically-right",
            rationale="selected_spell.rs:277-284; at least one row cannot correspond to an "
                      "effect of this provider",
            count=len(points["more_rows_than_effects_definitions"]),
            rows=points["more_rows_than_effects_definitions"][:NEGATIVE_SAMPLE],
        ))

        operations = self.rp.census_operation_types()
        out.append(NegativeClass(
            key="operation-type-none",
            description="TraitDefinitionEffectPoints.OperationType == -1",
            table="TraitDefinitionEffectPoints.OperationType",
            core_code="UnsupportedEffectPoints",
            verdict="conservative",
            rationale="`trinity` DBCEnums.h:2907-2912 names -1 TraitPointsOperationType::None "
                      "and SpellInfo.cpp:550-552 keeps the authored value; Core already has "
                      "that behaviour for an unpointed effect, so this is a representable "
                      "case it declines to read",
            count=len(operations["beyond_set_and_multiply"]),
            rows=operations["beyond_set_and_multiply"],
        ))

        mixed = [e for e in self.rp.resolvable
                 if self.rp.audit(e, 1).mixed_shaped_siblings]
        out.append(NegativeClass(
            key="incomplete-rank-shaping",
            description="a package where some effects are rank-shaped and sibling effects "
                        "keep their authored value",
            table="TraitDefinitionEffectPoints (absent row)",
            core_code="none -- Core admits these",
            verdict="admitted-but-hardcoded",
            rationale="Improved Vivify 101510 is the witness: at rank 1 its two effects "
                      "carry 20 and 40. Core reproduces this through the named branch "
                      "has_improved_vivify_amounts rather than through the general rule, so "
                      "163 sibling cases are admitted by the source rule with no named "
                      "branch behind them",
            count=len(mixed),
            rows=[self.rp.audit(e, 1).to_dict() for e in mixed[:5]],
        ))
        return out

    def corpus(self) -> dict[str, Any]:
        classes = self.classes()
        return {
            "pins": {
                "core": "b1714eda2b4b9393853f94c6517e78cce2dfa21a",
                "core_symbols": [
                    "TraitSourceCatalog::try_selected_spell_effect_amounts "
                    "(crates/data/src/trait_source/selected_spell.rs:210-382)",
                    "exact_rank_curve_points (crates/data/src/trait_source/amount.rs:10-41)",
                ],
                "evidence_classes": {
                    "source": "data/tables/*.csv",
                    "core": "/home/dev/pallet/core",
                    "trinity": "/home/dev/pallet/TrinityCore",
                },
            },
            "verdict_legend": {
                "semantically-right": "the source genuinely does not carry the fact Core "
                                      "would need",
                "conservative": "the source carries the fact; Core declines to read it",
                "mixed": "some rows in the class are one, some the other",
                "unreachable": "an earlier refusal always fires first",
                "admitted-but-unproved": "Core admits it without consulting the fact",
                "admitted-but-hardcoded": "Core admits it through a named branch",
            },
            "classes": {c.key: c.to_dict() for c in classes},
        }


def write_corpora() -> list[str]:
    """Writes ``rank-provenance.json`` and ``rank-negatives.json``, byte-stably."""
    import json

    from . import CORPORA

    rankprov = RankProvenance()
    written = []
    for name, payload in (("rank-provenance.json", rankprov.census()),
                          ("rank-negatives.json", RankNegatives(rankprov).corpus())):
        path = CORPORA / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, indent=1, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8")
        written.append(str(path))
    return written


if __name__ == "__main__":  # pragma: no cover - research entry point
    for written_path in write_corpora():
        print(written_path)
