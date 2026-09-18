"""Shared loaded evidence, built once per process.

``Context`` reuses the targeting context (Dummy-pass ``Bundle``: snapshot
catalog, world overlay, script index, dispatch tables, build skew; the
current-player ``Scope``; ``TargetingData``) and adds :class:`AuraData`, the
lifecycle-relevant DB2 columns keyed exactly as the CSVs hold them.

``drift()`` loads the same columns from the dbc-resolver release snapshot
(``PINS['drift_snapshot']``) when it is present; it is never required.

Loading costs ~12 s and ~1.7 GB; tests share one instance through ``al_ctx``.
"""

from __future__ import annotations

from collections import defaultdict
from functools import cached_property
from pathlib import Path

from procs.source import Source

from . import DRIFT_TABLES, FailClosed

_CTX: Context | None = None

# Table -> columns read.  Every lifecycle-relevant field of the snapshot is
# listed here so the consumption ledger answers "what must a DB2 export ship".
TABLES: dict[str, tuple[str, ...]] = {
    "SpellEffect": (
        "ID", "SpellID", "DifficultyID", "EffectIndex", "Effect", "EffectAura", "EffectAuraPeriod",
        "EffectAmplitude", "EffectAttributes", "EffectBasePointsF", "EffectBonusCoefficient",
        "BonusCoefficientFromAP", "EffectChainAmplitude", "EffectChainTargets", "EffectMechanic",
        "EffectMiscValue_0", "EffectMiscValue_1", "EffectPointsPerResource", "EffectPos_facing",
        "EffectRealPointsPerLevel", "EffectTriggerSpell", "ImplicitTarget_0", "ImplicitTarget_1",
        "EffectRadiusIndex_0", "EffectRadiusIndex_1", "EffectSpellClassMask_0", "EffectSpellClassMask_1",
        "EffectSpellClassMask_2", "EffectSpellClassMask_3", "PvpMultiplier", "Coefficient", "Variance",
        "ResourceCoefficient", "GroupSizeBasePointsCoefficient", "ScalingClass",
    ),
    "SpellMisc": ("ID", "SpellID", "DifficultyID", "DurationIndex", "PvPDurationIndex", "MinDuration",
                  "RangeIndex", "SchoolMask", "Speed", "LaunchDelay", "CastingTimeIndex",
                  "SpellIconFileDataID", "ActiveIconFileDataID", "ContentTuningID", "ShowFutureSpellPlayerConditionID",
                  *(f"Attributes_{i}" for i in range(17))),
    "SpellAuraOptions": ("ID", "SpellID", "DifficultyID", "CumulativeAura", "ProcCategoryRecovery",
                         "ProcChance", "ProcCharges", "SpellProcsPerMinuteID", "ProcTypeMask_0", "ProcTypeMask_1"),
    "SpellDuration": ("ID", "Duration", "MaxDuration", "DurationPerResource"),
    "SpellCategories": ("ID", "SpellID", "DifficultyID", "Category", "DefenseType", "DiminishType",
                        "DispelType", "Mechanic", "PreventionType", "StartRecoveryCategory", "ChargeCategory"),
    "SpellInterrupts": ("ID", "SpellID", "DifficultyID", "InterruptFlags", "AuraInterruptFlags_0",
                        "AuraInterruptFlags_1", "ChannelInterruptFlags_0", "ChannelInterruptFlags_1"),
    "SpellShapeshift": ("ID", "SpellID", "StanceBarOrder", "ShapeshiftExclude_0", "ShapeshiftExclude_1",
                        "ShapeshiftMask_0", "ShapeshiftMask_1"),
    "SpellEquippedItems": ("ID", "SpellID", "EquippedItemClass", "EquippedItemInvTypes", "EquippedItemSubclass"),
    "SpellAuraRestrictions": ("ID", "SpellID", "DifficultyID", "CasterAuraState", "TargetAuraState",
                              "ExcludeCasterAuraState", "ExcludeTargetAuraState", "CasterAuraSpell",
                              "TargetAuraSpell", "ExcludeCasterAuraSpell", "ExcludeTargetAuraSpell",
                              "CasterAuraType", "TargetAuraType", "ExcludeCasterAuraType", "ExcludeTargetAuraType"),
    "SpellClassOptions": ("ID", "SpellID", "ModalNextSpell", "SpellClassSet",
                          "SpellClassMask_0", "SpellClassMask_1", "SpellClassMask_2", "SpellClassMask_3"),
    "SpellProcsPerMinute": ("ID", "BaseProcRate", "Flags"),
    "SpellCastingRequirements": ("ID", "SpellID", "FacingCasterFlags", "RequiredAreasID", "RequiresSpellFocus"),
}
# Tables keyed per spell only (no DifficultyID column).
NO_DIFFICULTY = {"SpellShapeshift", "SpellEquippedItems", "SpellClassOptions", "SpellCastingRequirements"}
# Tables keyed by their own ID (lookup tables).
BY_ID = {"SpellDuration", "SpellProcsPerMinute"}


class AuraData:
    """Lifecycle columns of one snapshot.  Reads rows; never interprets them.

    Rows are keyed ``(SpellID, DifficultyID)``; ``SpellEffect`` additionally by
    ``EffectIndex`` (later row wins, as ``SpellMgr::LoadSpellInfoStore``).
    Only ``DIFFICULTY_NONE`` accessors are provided: other difficulties raise
    :class:`FailClosed` (difficulty fallback is a research question, not a default).
    """

    def __init__(self, source: Source) -> None:
        self.source = source
        # Tables this snapshot does not ship (a drift release carries only its export list).
        self.absent: set[str] = set()

    def _columns(self, table: str) -> tuple[str, ...]:
        """Requested columns that exist in this snapshot (drift builds may lack some)."""
        with self.source.path(table).open(encoding="utf-8") as handle:
            header = handle.readline().rstrip("\n").split(",")
        return tuple(c for c in TABLES[table] if c in header)

    @cached_property
    def _tables(self) -> dict[str, dict]:
        return {}

    def table(self, table: str) -> dict:
        if table in self._tables:
            return self._tables[table]
        if not self.source.has(table):
            self.absent.add(table)
            self._tables[table] = {}
            return self._tables[table]
        cols = self._columns(table)
        out: dict = defaultdict(dict) if table == "SpellEffect" else {}
        for r in self.source.project(table, cols):
            row = dict(zip(cols, r))
            if table in BY_ID:
                out[row["ID"]] = row
            elif table == "SpellEffect":
                out[(row["SpellID"], row["DifficultyID"])][row["EffectIndex"]] = row
            elif table in NO_DIFFICULTY:
                out.setdefault((row["SpellID"], 0), row)
            else:
                out[(row["SpellID"], row["DifficultyID"])] = row
        self._tables[table] = dict(out)
        return self._tables[table]

    @staticmethod
    def _need_none(difficulty: int) -> None:
        if difficulty != 0:
            raise FailClosed("aura_lifecycle.data: only DIFFICULTY_NONE rows are modelled")

    def effects(self, spell: int, difficulty: int = 0) -> list[dict]:
        self._need_none(difficulty)
        return [e for _, e in sorted(self.table("SpellEffect").get((spell, difficulty), {}).items())]

    def row(self, table: str, spell: int, difficulty: int = 0) -> dict | None:
        self._need_none(difficulty)
        return self.table(table).get((spell, difficulty))

    def duration(self, index: int) -> dict | None:
        return self.table("SpellDuration").get(index)

    def spells(self) -> list[int]:
        return sorted({s for s, d in self.table("SpellEffect") if d == 0})


class Context:
    def __init__(self) -> None:
        from targeting import context as tg_context
        self.tg = tg_context.get()
        self.bundle = self.tg.bundle
        self.data = AuraData(self.bundle.source)

    @property
    def catalog(self):
        return self.bundle.catalog

    @property
    def scope(self):
        """Current-player scope (class trees, spec spells, current gear/sets/gems/enchants + authored reach)."""
        return self.tg.scope

    @property
    def targeting(self):
        return self.tg.data

    def name(self, spell: int) -> str:
        return self.bundle.name(spell)

    def is_skew(self, spell: int) -> bool:
        """Spell newer than what pinned Trinity supports (dummy-corpora/build-skew.json)."""
        return self.bundle.skew.is_newer_than_trinity(spell)

    @cached_property
    def drift(self) -> AuraData | None:
        """The dbc-resolver release snapshot, or ``None`` when it was not fetched."""
        root = Path(DRIFT_TABLES)
        if not (root / "SpellEffect.csv").exists():
            return None
        return AuraData(Source(root))


def get() -> Context:
    global _CTX
    if _CTX is None:
        _CTX = Context()
    return _CTX
