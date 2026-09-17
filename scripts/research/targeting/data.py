"""Targeting columns of the DB2 snapshot.

Only reads source rows; interpretation lives in the stage modules.  Rows are
keyed ``(SpellID, DifficultyID)`` exactly as the CSVs hold them.  Difficulty
fallback (``SpellMgr::LoadSpellInfoStore`` copying DIFFICULTY_NONE rows into
difficulty-specific infos) is applied by :meth:`TargetingData.effects` /
:meth:`TargetingData.restrictions` only for ``difficulty == 0`` callers, which
is every current-player census row; other difficulties fail closed.

Mirrors (loading): ``SpellMgr::LoadSpellInfoStore`` (SpellMgr.cpp) and
``SpellInfo::SpellInfo`` (SpellInfo.cpp) -- effect row ``EffectIndex`` wins,
``SpellTargetRestrictions`` feeds ``ConeAngle``, ``Width``, ``MaxAffectedTargets``,
``MaxTargetLevel``, ``TargetCreatureType``, ``Targets``.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from functools import cached_property

from procs.source import Source

from . import FailClosed

EFFECT_TARGET_COLUMNS = (
    "ID", "SpellID", "DifficultyID", "EffectIndex", "Effect", "EffectAura",
    "ImplicitTarget_0", "ImplicitTarget_1", "EffectRadiusIndex_0", "EffectRadiusIndex_1",
    "EffectChainTargets", "EffectChainAmplitude", "EffectAttributes", "EffectPos_facing",
    "EffectMiscValue_0", "EffectMiscValue_1", "EffectTriggerSpell", "EffectAmplitude",
)
RESTRICTION_COLUMNS = ("ID", "SpellID", "DifficultyID", "ConeDegrees", "MaxTargets", "MaxTargetLevel",
                       "TargetCreatureType", "Targets", "Width")
MISC_COLUMNS = ("ID", "SpellID", "DifficultyID", "RangeIndex", "Speed", "LaunchDelay", "MinDuration",
                "SchoolMask") + tuple(f"Attributes_{i}" for i in range(17))
AURA_RESTRICTION_COLUMNS = ("ID", "SpellID", "DifficultyID", "CasterAuraState", "TargetAuraState",
                            "ExcludeCasterAuraState", "ExcludeTargetAuraState", "CasterAuraSpell",
                            "TargetAuraSpell", "ExcludeCasterAuraSpell", "ExcludeTargetAuraSpell",
                            "CasterAuraType", "TargetAuraType", "ExcludeCasterAuraType", "ExcludeTargetAuraType")
CASTING_REQ_COLUMNS = ("ID", "SpellID", "FacingCasterFlags", "RequiredAreasID", "RequiresSpellFocus")


@dataclass(frozen=True)
class EffectTargeting:
    row_id: int
    spell: int
    difficulty: int
    index: int
    effect: int
    aura: int
    target_a: int
    target_b: int
    radius_a: int
    radius_b: int
    chain_targets: int
    chain_amplitude: float
    attributes: int
    pos_facing: float
    misc0: int
    misc1: int
    trigger_spell: int
    amplitude: float

    @property
    def is_effect(self) -> bool:
        """Mirrors: ``SpellEffectInfo::IsEffect`` (SpellInfo.h) -- ``Effect != 0``."""
        return self.effect != 0


class TargetingData:
    def __init__(self, source: Source | None = None) -> None:
        self.source = source or Source()

    # -- raw tables --------------------------------------------------------
    @cached_property
    def _effects(self) -> dict[tuple[int, int], dict[int, EffectTargeting]]:
        out: dict[tuple[int, int], dict[int, EffectTargeting]] = defaultdict(dict)
        for r in self.source.project("SpellEffect", EFFECT_TARGET_COLUMNS):
            row = dict(zip(EFFECT_TARGET_COLUMNS, r))
            e = EffectTargeting(
                row_id=row["ID"], spell=row["SpellID"], difficulty=row["DifficultyID"],
                index=row["EffectIndex"], effect=row["Effect"], aura=row["EffectAura"],
                target_a=row["ImplicitTarget_0"], target_b=row["ImplicitTarget_1"],
                radius_a=row["EffectRadiusIndex_0"], radius_b=row["EffectRadiusIndex_1"],
                chain_targets=row["EffectChainTargets"], chain_amplitude=float(row["EffectChainAmplitude"]),
                attributes=row["EffectAttributes"], pos_facing=float(row["EffectPos_facing"]),
                misc0=row["EffectMiscValue_0"], misc1=row["EffectMiscValue_1"],
                trigger_spell=row["EffectTriggerSpell"], amplitude=float(row["EffectAmplitude"]))
            out[(e.spell, e.difficulty)][e.index] = e  # later row wins, as LoadSpellInfoStore
        return out

    def _keyed(self, table: str, columns: tuple[str, ...], per_difficulty: bool = True) -> dict:
        out = {}
        for r in self.source.project(table, columns):
            row = dict(zip(columns, r))
            out[(row["SpellID"], row["DifficultyID"] if per_difficulty else 0)] = row
        return out

    @cached_property
    def _restrictions(self) -> dict:
        return self._keyed("SpellTargetRestrictions", RESTRICTION_COLUMNS)

    @cached_property
    def _misc(self) -> dict:
        return self._keyed("SpellMisc", MISC_COLUMNS)

    @cached_property
    def _aura_restrictions(self) -> dict:
        return self._keyed("SpellAuraRestrictions", AURA_RESTRICTION_COLUMNS)

    @cached_property
    def _casting_requirements(self) -> dict:
        return self._keyed("SpellCastingRequirements", CASTING_REQ_COLUMNS, per_difficulty=False)

    @cached_property
    def radii(self) -> dict[int, dict]:
        cols = ("ID", "Radius", "RadiusPerLevel", "RadiusMin", "RadiusMax")
        return {r[0]: dict(zip(cols, r)) for r in self.source.project("SpellRadius", cols)}

    @cached_property
    def ranges(self) -> dict[int, dict]:
        cols = ("ID", "Flags", "RangeMin_0", "RangeMin_1", "RangeMax_0", "RangeMax_1")
        return {r[0]: dict(zip(cols, r)) for r in self.source.project("SpellRange", cols)}

    # -- accessors ---------------------------------------------------------
    @staticmethod
    def _need_none(difficulty: int) -> None:
        if difficulty != 0:
            raise FailClosed("targeting.data: only DIFFICULTY_NONE rows are modelled (difficulty fallback not ported)")

    def effects(self, spell: int, difficulty: int = 0) -> list[EffectTargeting]:
        self._need_none(difficulty)
        return [e for _, e in sorted(self._effects.get((spell, difficulty), {}).items())]

    def restrictions(self, spell: int, difficulty: int = 0) -> dict | None:
        self._need_none(difficulty)
        return self._restrictions.get((spell, difficulty))

    def misc(self, spell: int, difficulty: int = 0) -> dict | None:
        self._need_none(difficulty)
        return self._misc.get((spell, difficulty))

    def aura_restrictions(self, spell: int, difficulty: int = 0) -> dict | None:
        self._need_none(difficulty)
        return self._aura_restrictions.get((spell, difficulty))

    def casting_requirements(self, spell: int) -> dict | None:
        return self._casting_requirements.get((spell, 0))

    def spells(self) -> list[int]:
        return sorted({s for s, d in self._effects if d == 0})
