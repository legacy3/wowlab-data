"""Trait source tables, projected.

Mirrors: ``wowlab_data::TraitSourceCatalog`` (``core/crates/data/src/trait_source/``).
Nothing here interprets selection, eligibility or behaviour -- it only provides the
rows that ``provenance`` walks.

DB2 stores ``float`` columns as IEEE single precision.  The CSV export writes them
as decimal text, so every float this module returns is round-tripped through f32
(:func:`f32`) to reproduce the value Core sees after its own f32 -> f64 widening.
That matters for the exact-bit amount comparisons Core performs
(``authored().to_bits() == 10.0_f64.to_bits()``).
"""

from __future__ import annotations

import struct
from collections import defaultdict
from dataclasses import dataclass
from functools import cached_property

from procs.source import Source


def f32(value: float | int) -> float:
    """The f64 value Core sees after widening a DB2 single-precision field."""
    return struct.unpack("<f", struct.pack("<f", float(value)))[0]


@dataclass(frozen=True)
class Entry:
    """One ``TraitNodeEntry`` row."""

    id: int
    definition_id: int
    max_ranks: int
    node_entry_type: int
    trait_subtree_id: int


@dataclass(frozen=True)
class Definition:
    """One ``TraitDefinition`` row."""

    id: int
    spell_id: int
    overrides_spell_id: int
    visible_spell_id: int


@dataclass(frozen=True)
class EffectPoint:
    """One ``TraitDefinitionEffectPoints`` row (0-based ``EffectIndex``)."""

    id: int
    definition_id: int
    effect_index: int
    operation_type: int
    curve_id: int


@dataclass(frozen=True)
class CurvePoint:
    """One ``CurvePoint`` row; ``x``/``y`` are ``Pos_0``/``Pos_1``."""

    curve_id: int
    order_index: int
    x: float
    y: float


@dataclass(frozen=True)
class Curve:
    id: int
    curve_type: int
    flags: int


ENTRY_COLUMNS = ("ID", "TraitDefinitionID", "MaxRanks", "NodeEntryType", "TraitSubTreeID")
DEFINITION_COLUMNS = ("ID", "SpellID", "OverridesSpellID", "VisibleSpellID")
POINT_COLUMNS = ("ID", "TraitDefinitionID", "EffectIndex", "OperationType", "CurveID")
CURVE_COLUMNS = ("ID", "Type", "Flags")
CURVE_POINT_COLUMNS = ("ID", "CurveID", "OrderIndex", "Pos_0", "Pos_1")
NODE_X_ENTRY_COLUMNS = ("ID", "TraitNodeID", "TraitNodeEntryID", "_Index")
NODE_COLUMNS = ("ID", "TraitTreeID", "Type", "Flags", "TraitSubTreeID")


class TraitSource:
    """Projected trait tables with the indexes ``provenance`` needs."""

    def __init__(self, source: Source | None = None) -> None:
        self.source = source or Source()

    # -- raw rows ---------------------------------------------------------
    @cached_property
    def entries(self) -> dict[int, Entry]:
        out = {}
        for row in self.source.project("TraitNodeEntry", ENTRY_COLUMNS):
            entry = Entry(*(int(v) for v in row))
            out[entry.id] = entry
        return out

    @cached_property
    def definitions(self) -> dict[int, Definition]:
        out = {}
        for row in self.source.project("TraitDefinition", DEFINITION_COLUMNS):
            definition = Definition(*(int(v) for v in row))
            out[definition.id] = definition
        return out

    @cached_property
    def effect_points(self) -> dict[int, list[EffectPoint]]:
        """``TraitDefinitionID -> points`` in ascending row-ID order.

        Mirrors: ``TraitSourceCatalog::effect_points_for_definition``.  Row order is
        load-bearing -- Core writes ``rank_shaped_effect_indices[point_position]``
        by enumeration position.
        """
        out: dict[int, list[EffectPoint]] = defaultdict(list)
        rows = [EffectPoint(*(int(v) for v in row))
                for row in self.source.project("TraitDefinitionEffectPoints", POINT_COLUMNS)]
        for point in sorted(rows, key=lambda p: p.id):
            out[point.definition_id].append(point)
        return dict(out)

    @cached_property
    def curves(self) -> dict[int, Curve]:
        out = {}
        for row in self.source.project("Curve", CURVE_COLUMNS):
            curve = Curve(*(int(v) for v in row))
            out[curve.id] = curve
        return out

    @cached_property
    def curve_points(self) -> dict[int, list[CurvePoint]]:
        """``CurveID -> points`` sorted by ``OrderIndex``.

        Mirrors: ``TraitSourceCatalog::curve_points_for_curve``.
        """
        out: dict[int, list[CurvePoint]] = defaultdict(list)
        for row_id, curve_id, order_index, pos0, pos1 in self.source.project(
            "CurvePoint", CURVE_POINT_COLUMNS
        ):
            out[int(curve_id)].append(
                CurvePoint(int(curve_id), int(order_index), f32(pos0), f32(pos1))
            )
        for points in out.values():
            points.sort(key=lambda p: p.order_index)
        return dict(out)

    # -- topology ---------------------------------------------------------
    @cached_property
    def nodes_for_entry(self) -> dict[int, list[int]]:
        out: dict[int, list[int]] = defaultdict(list)
        for _row, node, entry, _index in self.source.project(
            "TraitNodeXTraitNodeEntry", NODE_X_ENTRY_COLUMNS
        ):
            out[int(entry)].append(int(node))
        return dict(out)

    @cached_property
    def entries_for_definition(self) -> dict[int, list[int]]:
        out: dict[int, list[int]] = defaultdict(list)
        for entry in self.entries.values():
            out[entry.definition_id].append(entry.id)
        return dict(out)

    @cached_property
    def definitions_for_spell(self) -> dict[int, list[int]]:
        out: dict[int, list[int]] = defaultdict(list)
        for definition in self.definitions.values():
            if definition.spell_id:
                out[definition.spell_id].append(definition.id)
        return dict(out)

    # -- accessors --------------------------------------------------------
    def entry(self, entry_id: int) -> Entry | None:
        return self.entries.get(entry_id)

    def definition(self, definition_id: int) -> Definition | None:
        return self.definitions.get(definition_id)

    def points_for_definition(self, definition_id: int) -> list[EffectPoint]:
        return self.effect_points.get(definition_id, [])
