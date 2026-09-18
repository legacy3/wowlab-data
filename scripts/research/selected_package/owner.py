"""Provider ("owner") sidecar census and the owner-policy vocabulary.

Mirrors: ``SelectedTraitOwnerContract`` / ``selected_trait_owner_policy_issue``
(selected_trait_package.rs:143-338), ``neutral_physical_passive_owner_shell_issue``
and ``passive_modifier_owner_policy_with_count_issue`` (exact_source.rs:255-420),
``supports_unconditional_passive_application_policy``
(passive_spell_modifier.rs:1100-1110) and ``validate_owner``
(passive_spell_modifier.rs:940-980).

The goal is a *vocabulary*, not talent tuples: each sidecar row a provider carries
is classified into one semantic consequence class, and only classes that are
provably inert for immutable passive preparation may be present on an admitted
package.

Policy classes (the mission's A-I):

==  ===========================================================================
A   presentation / metadata only
B   immutable preparation fact (changes a prepared value, never a lifetime)
C   acquisition gate (decides whether the actor has it at all)
D   conditional activation gate (holds it, but only applies under a condition)
E   finite or mutable lifetime
F   stacking / reapplication policy
G   proc / runtime policy
H   payload-side policy (constrains what it modifies, not the owner)
I   unknown / build skew
==  ===========================================================================
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from functools import cached_property
from typing import Any

from procs.source import Source

from . import coreref
from .provenance import f32

POLICY_CLASSES = {
    "A": "presentation/metadata only",
    "B": "immutable preparation fact",
    "C": "acquisition gate",
    "D": "conditional activation gate",
    "E": "finite/mutable lifetime",
    "F": "stacking/reapplication policy",
    "G": "proc/runtime policy",
    "H": "payload-side policy",
    "I": "unknown/build skew",
}

#: Classes that may be present on a provider admitted for immutable passive
#: preparation.  Everything else must be absent, or proved inert case by case.
INERT_CLASSES = frozenset({"A", "B", "H"})

MISC_COLUMNS = (
    "ID", "SpellID", "DifficultyID", "SchoolMask", "DurationIndex", "PvPDurationIndex",
    "RangeIndex", "CastingTimeIndex", "Speed", "LaunchDelay", "MinDuration", "ContentTuningID",
    "ShowFutureSpellPlayerConditionID",
) + tuple(f"Attributes_{i}" for i in range(17))
AURA_OPTIONS_COLUMNS = ("ID", "SpellID", "DifficultyID", "CumulativeAura", "ProcCategoryRecovery",
                        "ProcChance", "ProcCharges", "SpellProcsPerMinuteID",
                        "ProcTypeMask_0", "ProcTypeMask_1")
AURA_RESTRICTION_COLUMNS = ("ID", "SpellID", "DifficultyID", "CasterAuraState", "TargetAuraState",
                            "ExcludeCasterAuraState", "ExcludeTargetAuraState", "CasterAuraSpell",
                            "TargetAuraSpell", "ExcludeCasterAuraSpell", "ExcludeTargetAuraSpell",
                            "CasterAuraType", "TargetAuraType", "ExcludeCasterAuraType",
                            "ExcludeTargetAuraType")
CATEGORIES_COLUMNS = ("ID", "SpellID", "DifficultyID", "Category", "DefenseType", "DiminishType",
                      "DispelType", "Mechanic", "PreventionType", "StartRecoveryCategory",
                      "ChargeCategory")
CLASS_OPTION_COLUMNS = ("ID", "SpellID", "SpellClassSet", "SpellClassMask_0", "SpellClassMask_1",
                        "SpellClassMask_2", "SpellClassMask_3")
SHAPESHIFT_COLUMNS = ("ID", "SpellID", "StanceBarOrder", "ShapeshiftExclude_0",
                      "ShapeshiftExclude_1", "ShapeshiftMask_0", "ShapeshiftMask_1")
EQUIPPED_COLUMNS = ("ID", "SpellID", "EquippedItemClass", "EquippedItemInvTypes",
                    "EquippedItemSubclass")
CASTING_REQ_COLUMNS = ("ID", "SpellID", "FacingCasterFlags", "MinFactionID", "MinReputation",
                       "RequiredAreasID", "RequiredAuraVision", "RequiresSpellFocus")
COOLDOWN_COLUMNS = ("ID", "SpellID", "DifficultyID", "CategoryRecoveryTime", "RecoveryTime",
                    "StartRecoveryTime", "AuraSpellID")
LEVEL_COLUMNS = ("ID", "SpellID", "DifficultyID", "MaxLevel", "MaxPassiveAuraLevel", "BaseLevel",
                 "SpellLevel")
INTERRUPT_COLUMNS = ("ID", "SpellID", "DifficultyID", "InterruptFlags", "AuraInterruptFlags_0",
                     "AuraInterruptFlags_1", "ChannelInterruptFlags_0", "ChannelInterruptFlags_1")
TARGET_RESTRICTION_COLUMNS = ("ID", "SpellID", "DifficultyID", "ConeDegrees", "MaxTargets",
                              "MaxTargetLevel", "TargetCreatureType", "Targets", "Width")
POWER_COLUMNS = ("ID", "SpellID", "OrderIndex", "ManaCost", "PowerType", "PowerCostPct",
                 "ManaPerSecond", "OptionalCost", "RequiredAuraSpellID")
SCALING_COLUMNS = ("ID", "SpellID", "MinScalingLevel", "MaxScalingLevel")
REAGENT_COLUMNS = ("ID", "SpellID") + tuple(f"Reagent_{i}" for i in range(8))

#: ``SpellSchoolMask::Physical``.
PHYSICAL_SCHOOL = 1


@dataclass
class Sidecar:
    """One present auxiliary row, with its semantic consequence."""

    table: str
    policy_class: str
    inert: bool
    reason: str
    row: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"table": self.table, "policy_class": self.policy_class,
                "policy": POLICY_CLASSES[self.policy_class], "inert": self.inert,
                "reason": self.reason, "row": self.row}


@dataclass
class OwnerPolicy:
    """Everything the owner (provider) spell says about lifetime and preparation."""

    spell: int
    school_mask: int
    duration_index: int
    attributes: list[int]
    sidecars: list[Sidecar]
    blockers: list[str]
    family: int
    class_mask: tuple[int, int, int, int]
    defense_type: int
    dispel_type: int
    mechanic: int
    is_passive: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "spell": self.spell,
            "school_mask": self.school_mask,
            "physical_only": self.school_mask == PHYSICAL_SCHOOL,
            "duration_index": self.duration_index,
            "attributes": sorted(self.attributes),
            "attribute_support": {str(a): coreref.attribute_support(a) for a in sorted(self.attributes)},
            "is_passive": self.is_passive,
            "family": self.family,
            "class_mask": list(self.class_mask),
            "defense_type": self.defense_type,
            "dispel_type": self.dispel_type,
            "mechanic": self.mechanic,
            "sidecars": [s.to_dict() for s in self.sidecars],
            "policy_classes": sorted({s.policy_class for s in self.sidecars}),
            "blockers": sorted(self.blockers),
        }


def _by_spell(source: Source, table: str, columns, difficulty_zero: bool = True):
    out: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in source.project(table, columns):
        record = dict(zip(columns, row))
        if difficulty_zero and "DifficultyID" in record and int(record["DifficultyID"]) != 0:
            continue
        out[int(record["SpellID"])].append(record)
    return dict(out)


class Owners:
    """Sidecar census over every provider spell."""

    def __init__(self, source: Source | None = None) -> None:
        self.source = source or Source()

    @cached_property
    def misc(self) -> dict[int, dict[str, Any]]:
        out = {}
        for row in self.source.project("SpellMisc", MISC_COLUMNS):
            record = dict(zip(MISC_COLUMNS, row))
            if int(record["DifficultyID"]) != 0:
                continue
            out.setdefault(int(record["SpellID"]), record)
        return out

    @cached_property
    def aura_options(self):
        return _by_spell(self.source, "SpellAuraOptions", AURA_OPTIONS_COLUMNS)

    @cached_property
    def aura_restrictions(self):
        return _by_spell(self.source, "SpellAuraRestrictions", AURA_RESTRICTION_COLUMNS)

    @cached_property
    def categories(self):
        return _by_spell(self.source, "SpellCategories", CATEGORIES_COLUMNS)

    @cached_property
    def class_options(self):
        return _by_spell(self.source, "SpellClassOptions", CLASS_OPTION_COLUMNS)

    @cached_property
    def shapeshift(self):
        return _by_spell(self.source, "SpellShapeshift", SHAPESHIFT_COLUMNS)

    @cached_property
    def equipped(self):
        return _by_spell(self.source, "SpellEquippedItems", EQUIPPED_COLUMNS)

    @cached_property
    def casting_requirements(self):
        return _by_spell(self.source, "SpellCastingRequirements", CASTING_REQ_COLUMNS)

    @cached_property
    def cooldowns(self):
        return _by_spell(self.source, "SpellCooldowns", COOLDOWN_COLUMNS)

    @cached_property
    def levels(self):
        return _by_spell(self.source, "SpellLevels", LEVEL_COLUMNS)

    @cached_property
    def interrupts(self):
        return _by_spell(self.source, "SpellInterrupts", INTERRUPT_COLUMNS)

    @cached_property
    def target_restrictions(self):
        return _by_spell(self.source, "SpellTargetRestrictions", TARGET_RESTRICTION_COLUMNS)

    @cached_property
    def power(self):
        return _by_spell(self.source, "SpellPower", POWER_COLUMNS)

    @cached_property
    def scaling(self):
        return _by_spell(self.source, "SpellScaling", SCALING_COLUMNS)

    @cached_property
    def reagents(self):
        return _by_spell(self.source, "SpellReagents", REAGENT_COLUMNS)

    @cached_property
    def labels(self) -> dict[int, list[int]]:
        out: dict[int, list[int]] = defaultdict(list)
        for _row, label, spell in self.source.project("SpellLabel", ("ID", "LabelID", "SpellID")):
            out[int(spell)].append(int(label))
        return dict(out)

    # -- classification ---------------------------------------------------
    def policy(self, spell: int) -> OwnerPolicy:
        misc = self.misc.get(spell)
        blockers: list[str] = []
        sidecars: list[Sidecar] = []

        if misc is None:
            blockers.append("provider has no base-difficulty SpellMisc row")
            misc = {}

        school = int(misc.get("SchoolMask", 0) or 0)
        duration_index = int(misc.get("DurationIndex", 0) or 0)
        attributes = _attribute_ids(misc)
        is_passive = coreref.PASSIVE_ATTRIBUTE in attributes

        # -- lifetime: the three facts supports_unconditional_passive_application_policy tests
        if duration_index:
            blockers.append(f"finite/authored duration (DurationIndex {duration_index})")
            sidecars.append(Sidecar("SpellMisc.DurationIndex", "E", False,
                                    "authored duration makes the application finite",
                                    {"DurationIndex": duration_index}))
        for row in self.aura_restrictions.get(spell, []):
            sidecars.append(Sidecar("SpellAuraRestrictions", "D", False,
                                    "caster/target aura-state or aura-spell condition gates application",
                                    _nonzero(row)))
            blockers.append("SpellAuraRestrictions row present")
        for row in self.shapeshift.get(spell, []):
            sidecars.append(Sidecar("SpellShapeshift", "D", False,
                                    "shapeshift form gates application", _nonzero(row)))
            blockers.append("SpellShapeshift row present")

        # -- owner sidecars Core rejects outright
        for row in self.equipped.get(spell, []):
            sidecars.append(Sidecar("SpellEquippedItems", "D", False,
                                    "equipment condition gates application", _nonzero(row)))
            blockers.append("SpellEquippedItems row present")

        speed = f32(misc.get("Speed", 0) or 0)
        launch = f32(misc.get("LaunchDelay", 0) or 0)
        minimum = f32(misc.get("MinDuration", 0) or 0)
        if speed or launch or minimum:
            sidecars.append(Sidecar("SpellMisc.missile", "B", False,
                                    "meaningful missile row implies delivery timing",
                                    {"Speed": speed, "LaunchDelay": launch, "MinDuration": minimum}))
            blockers.append("meaningful missile row")
        elif spell in self.misc:
            sidecars.append(Sidecar("SpellMisc.missile", "A", True,
                                    "missile row is present but fully neutral", {}))

        # -- AuraOptions: stacking/proc policy; inertness is decided by procpolicy
        for row in self.aura_options.get(spell, []):
            mask = (int(row["ProcTypeMask_0"]) & 0xFFFFFFFF) | ((int(row["ProcTypeMask_1"]) & 0xFFFFFFFF) << 32)
            cumulative = int(row["CumulativeAura"])
            sidecars.append(Sidecar(
                "SpellAuraOptions", "F" if cumulative > 1 else "G", False,
                "stack capacity and/or proc policy; see procpolicy for effective generation",
                {"CumulativeAura": cumulative,
                 "ProcCategoryRecovery": int(row["ProcCategoryRecovery"]),
                 "ProcChance": int(row["ProcChance"]),
                 "ProcCharges": int(row["ProcCharges"]),
                 "SpellProcsPerMinuteID": int(row["SpellProcsPerMinuteID"]),
                 "ProcTypeMask": mask}))

        # -- gates and metadata that Core does not currently test
        for row in self.casting_requirements.get(spell, []):
            nonzero = _nonzero(row)
            meaningful = {k: v for k, v in nonzero.items() if k not in ("ID", "SpellID")}
            sidecars.append(Sidecar("SpellCastingRequirements",
                                    "D" if meaningful else "A", not meaningful,
                                    "casting requirement row" if meaningful
                                    else "row present but every gate field is zero", nonzero))
            if meaningful:
                blockers.append(f"SpellCastingRequirements gates {sorted(meaningful)}")
        for row in self.cooldowns.get(spell, []):
            nonzero = {k: v for k, v in _nonzero(row).items() if k not in ("ID", "SpellID")}
            if nonzero:
                sidecars.append(Sidecar("SpellCooldowns", "G", False,
                                        "authored recovery implies an activation, not a passive",
                                        nonzero))
                blockers.append("SpellCooldowns row with nonzero recovery")
        for row in self.power.get(spell, []):
            nonzero = {k: v for k, v in _nonzero(row).items() if k not in ("ID", "SpellID", "OrderIndex")}
            if nonzero:
                sidecars.append(Sidecar("SpellPower", "G", False,
                                        "authored power cost implies an activation", nonzero))
                blockers.append("SpellPower row with a nonzero cost")
        for row in self.reagents.get(spell, []):
            if any(int(row.get(f"Reagent_{i}", 0) or 0) for i in range(8)):
                sidecars.append(Sidecar("SpellReagents", "G", False,
                                        "reagents imply an activation", _nonzero(row)))
                blockers.append("SpellReagents row with a reagent")
        for row in self.interrupts.get(spell, []):
            nonzero = {k: v for k, v in _nonzero(row).items() if k not in ("ID", "SpellID")}
            if nonzero:
                sidecars.append(Sidecar("SpellInterrupts", "E", False,
                                        "aura-interrupt flags make the application removable",
                                        nonzero))
                blockers.append(f"SpellInterrupts flags {sorted(nonzero)}")
        for row in self.target_restrictions.get(spell, []):
            nonzero = {k: v for k, v in _nonzero(row).items() if k not in ("ID", "SpellID")}
            sidecars.append(Sidecar("SpellTargetRestrictions", "H" if nonzero else "A",
                                    not nonzero, "payload/target-side restriction", nonzero))
        for row in self.scaling.get(spell, []):
            nonzero = {k: v for k, v in _nonzero(row).items() if k not in ("ID", "SpellID")}
            sidecars.append(Sidecar("SpellScaling", "B" if nonzero else "A", not nonzero,
                                    "level scaling window for authored amounts", nonzero))
        for row in self.levels.get(spell, []):
            nonzero = {k: v for k, v in _nonzero(row).items() if k not in ("ID", "SpellID")}
            sidecars.append(Sidecar("SpellLevels", "C" if nonzero else "A", not nonzero,
                                    "level requirement is an acquisition fact", nonzero))

        # -- owner identity facts
        categories = self.categories.get(spell, [{}])[0]
        class_options = self.class_options.get(spell, [{}])[0]
        family = int(class_options.get("SpellClassSet", 0) or 0)
        class_mask = tuple(int(class_options.get(f"SpellClassMask_{i}", 0) or 0) & 0xFFFFFFFF
                           for i in range(4))
        defense_type = int(categories.get("DefenseType", 0) or 0)
        dispel_type = int(categories.get("DispelType", 0) or 0)
        mechanic = int(categories.get("Mechanic", 0) or 0)

        if not is_passive:
            blockers.append("provider lacks the Passive owner attribute (raw 6)")
        for attribute in attributes:
            support = coreref.attribute_support(attribute)
            if attribute == coreref.PASSIVE_ATTRIBUTE:
                continue
            if support == "ignored":
                sidecars.append(Sidecar(f"SpellMisc.Attributes[{attribute}]", "A", True,
                                        "catalog-Ignored owner attribute", {}))
                continue
            if support == "unknown":
                blockers.append(f"owner attribute {attribute} is absent from Core's catalog")
            else:
                blockers.append(f"owner attribute {attribute} has support '{support}', not Ignored")
        if defense_type:
            blockers.append(f"owner DefenseType {defense_type}")
        if dispel_type:
            blockers.append(f"owner DispelType {dispel_type}")
        if mechanic:
            blockers.append(f"owner Mechanic {mechanic}")
        if any(class_mask):
            blockers.append("owner carries a nonempty spell class mask")

        return OwnerPolicy(spell, school, duration_index, attributes, sidecars, blockers,
                           family, class_mask, defense_type, dispel_type, mechanic, is_passive)


def _attribute_ids(misc: dict[str, Any]) -> list[int]:
    """The set bits of ``SpellMisc.Attributes_0..16`` as Core's raw attribute ids."""
    out = []
    for word in range(17):
        value = int(misc.get(f"Attributes_{word}", 0) or 0) & 0xFFFFFFFF
        for bit in range(32):
            if value & (1 << bit):
                out.append(word * 32 + bit)
    return out


def _nonzero(row: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in row.items() if v not in (0, 0.0, "", None)}
