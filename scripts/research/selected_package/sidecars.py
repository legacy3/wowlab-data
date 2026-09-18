"""Complete owner-sidecar census and the owner lifetime policy, table by table.

Mirrors: ``supports_unconditional_passive_application_policy``
(``core/crates/combat/src/program/passive_spell_modifier.rs:1099-1110``),
``validate_owner`` / ``validate_owner_attributes`` (``passive_spell_modifier.rs:940-980``,
``:1223-1266``), ``neutral_physical_passive_owner_shell_issue`` and
``passive_modifier_owner_policy_with_count_issue``
(``core/crates/combat/src/program/exact_source.rs:283-458``) and
``selected_trait_owner_policy_issue`` (``selected_trait_package.rs:263-341``).

Where :mod:`selected_package.owner` asks "what do Core's four owner checks say about this
provider", this module asks the strictly larger question the mission poses:

    of **every** auxiliary DB2 row a provider spell can carry, which ones are provably
    inert for immutable passive preparation?

The answer is data, not code: :data:`SIDECAR_POLICY` maps one table name to one
:class:`SidecarRule`, and :meth:`SidecarCensus.classify` is a fold over the rules.  No
spell, entry, definition or talent id appears anywhere in this module.

Fail-closed discipline
----------------------
Three independent drift guards, because a silently short policy turns into a false
admission -- exactly the failure this pass exists to prevent:

1. a spell-keyed table present under ``data/tables`` with no :data:`SIDECAR_POLICY` entry
   raises :class:`~selected_package.FailClosed`;
2. a rule whose pinned ``columns`` no longer match the CSV header raises
   :class:`~selected_package.FailClosed`;
3. a present row of a non-inert table with a nonzero column the rule does not name is
   reported as a class ``I`` (unknown/build skew) blocker rather than ignored.

Policy classes (the mission's A-I)
----------------------------------

==  ===========================================================================
A   presentation / metadata only
B   immutable preparation fact (changes a prepared value, never a lifetime)
C   acquisition gate (decides whether the actor has it at all)
D   conditional activation gate (holds it, but only applies under a condition)
E   finite or mutable lifetime
F   stacking / reapplication policy
G   proc / runtime / activation-cost policy
H   payload-side policy (constrains what it modifies, not the owner)
I   unknown / build skew
==  ===========================================================================

Class membership is *semantic*; inertness is a *separate, argued* decision carried by
``SidecarRule.inert``.  Classes A, B and H are inert by their definition.  Every class-C
rule here is an **inbound grant reference** -- some unrelated system also hands the actor
this spell -- which says nothing about the trait-granted application, so it is inert with
that justification recorded.  Every D/E/F/G rule is non-inert.
"""

from __future__ import annotations

import csv
from collections import defaultdict
from dataclasses import dataclass, field
from functools import cached_property
from typing import Any

from procs.source import Source

from . import TABLES, FailClosed, coreref, evidence

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

#: ``SpellMisc.Attributes_0..16`` bit ``n`` is Core's raw attribute ``word * 32 + bit``.
ATTRIBUTE_WORDS = 17

#: Owner attributes whose Core support class is *not* ``Ignored`` but which this census
#: proves inert **for a Passive owner**, with the pinned consumer that decides it.
#:
#: Core refuses every non-Ignored attribute outright
#: (``passive_spell_modifier.rs:1242-1257``, ``exact_source.rs:319-328``).  That is
#: over-conservative here: both members of Trinity's paired proc-provenance policy are
#: read off the *triggering* spell inside ``Aura::CheckProc``, a path a permanently
#: applied passive never reaches as either side.
PASSIVE_INERT_ATTRIBUTES: dict[int, tuple[str, str, str]] = {
    415: ("G",
          "OnlyProcFromClassAbilities: read off the aura's own spell only while it is "
          "hosting a proc; an owner with no SpellProcEntry never enters Aura::CheckProc",
          "src/server/game/Spells/Auras/SpellAuras.cpp:1859"),
    416: ("G",
          "AllowClassAbilityProcs: read off the TRIGGERING spell (spell->GetSpellInfo()), "
          "never off the aura owner, and only when some other aura carries ATTR12 "
          "OnlyProcFromClassAbilities; it can change neither this owner's application nor "
          "its prepared amounts",
          "src/server/game/Spells/Auras/SpellAuras.cpp:1859"),
}

#: Classes inert by definition.  A class-C rule may additionally declare ``inert=True``
#: with its own recorded justification; D/E/F/G never may.
INERT_BY_CLASS = frozenset({"A", "B", "H"})
NEVER_INERT = frozenset({"D", "E", "F", "G", "I"})


@dataclass(frozen=True)
class SidecarRule:
    """One spell-keyed DB2 table and what carrying a row of it means."""

    table: str
    #: The column that holds the provider spell id.  ``ID`` for the two tables whose
    #: primary key *is* the spell id (``Spell``, ``SpellName``).
    key: str
    policy_class: str
    #: Whether a present row is provably inert for immutable passive preparation.
    inert: bool
    #: Whether the mere existence of a row blocks, whatever it contains.  Set exactly
    #: where Core tests ``row.is_none()``.
    presence_blocks: bool
    #: Columns whose nonzero value blocks.  Empty for ``presence_blocks`` tables and for
    #: inert tables.
    blocking: tuple[str, ...]
    #: The header pinned at audit time; a mismatch fails closed.
    columns: tuple[str, ...]
    justification: str
    evidence: str
    #: Columns of a *non-inert* table whose value is argued inert.  Together with
    #: ``blocking`` these must cover the whole header, so no column of a table that can
    #: block is left unclassified.  Unused (and required empty) for inert tables and for
    #: ``presence_blocks`` tables, where the whole row is already accounted for.
    inert_columns: tuple[str, ...] = ()
    core: tuple[str, ...] = ()
    trinity: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        # The policy literal is written with JSON-shaped lists; normalise to tuples so a
        # header comparison is a value comparison.
        object.__setattr__(self, "blocking", tuple(self.blocking))
        object.__setattr__(self, "inert_columns", tuple(self.inert_columns))
        object.__setattr__(self, "columns", tuple(self.columns))
        object.__setattr__(self, "core", tuple(self.core))
        object.__setattr__(self, "trinity", tuple(self.trinity))
        if self.policy_class not in POLICY_CLASSES:
            raise FailClosed(f"{self.table}: unknown policy class {self.policy_class!r}")
        evidence(self.evidence)
        if self.inert and self.policy_class in NEVER_INERT:
            raise FailClosed(f"{self.table}: class {self.policy_class} may not be inert")
        if self.inert and self.blocking:
            raise FailClosed(f"{self.table}: an inert rule may not name blocking columns")
        if self.presence_blocks and self.blocking:
            raise FailClosed(f"{self.table}: presence_blocks already covers every column")
        unknown = [c for c in self.blocking + self.inert_columns if c not in self.columns]
        if unknown:
            raise FailClosed(f"{self.table}: blocking columns absent from the header: {unknown}")
        if self.inert or self.presence_blocks:
            if self.inert_columns:
                raise FailClosed(f"{self.table}: the whole row is already accounted for")
            return
        overlap = sorted(set(self.blocking) & set(self.inert_columns))
        if overlap:
            raise FailClosed(f"{self.table}: {overlap} are both blocking and inert")
        uncovered = sorted(
            set(self.columns) - set(self.blocking) - set(self.inert_columns) - {"ID", self.key})
        if uncovered:
            raise FailClosed(
                f"{self.table}: {len(uncovered)} columns are neither blocking nor argued inert "
                f"(first: {uncovered[:6]}); a table that can block must classify every column")

    @property
    def policy(self) -> str:
        return POLICY_CLASSES[self.policy_class]

    def to_dict(self) -> dict[str, Any]:
        return {
            "table": self.table,
            "key": self.key,
            "policy_class": self.policy_class,
            "policy": self.policy,
            "inert": self.inert,
            "presence_blocks": self.presence_blocks,
            "blocking_columns": list(self.blocking),
            "inert_columns": list(self.inert_columns),
            "justification": self.justification,
            "evidence": self.evidence,
            "core": list(self.core),
            "trinity": list(self.trinity),
        }


@dataclass
class SidecarHit:
    """One present row of one table, with its consequence for this provider."""

    table: str
    policy_class: str
    inert: bool
    reason: str
    nonzero: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"table": self.table, "policy_class": self.policy_class,
                "policy": POLICY_CLASSES[self.policy_class], "inert": self.inert,
                "reason": self.reason, "nonzero": self.nonzero}


@dataclass
class OwnerVerdict:
    """Whether every sidecar a provider carries is inert for immutable preparation."""

    spell: int
    hits: list[SidecarHit] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)

    @property
    def inert(self) -> bool:
        """``True`` exactly when every present sidecar is provably inert."""
        return not self.blockers

    def tables(self) -> list[str]:
        return sorted({hit.table for hit in self.hits})

    def to_dict(self) -> dict[str, Any]:
        return {
            "spell": self.spell,
            "inert": self.inert,
            "tables": self.tables(),
            "policy_classes": sorted({hit.policy_class for hit in self.hits}),
            "hits": [hit.to_dict() for hit in self.hits],
            "blockers": sorted(self.blockers),
        }


def attribute_ids(misc: dict[str, Any]) -> list[int]:
    """The set bits of ``SpellMisc.Attributes_0..16`` as Core's raw attribute ids."""
    out = []
    for word in range(ATTRIBUTE_WORDS):
        value = int(misc.get(f"Attributes_{word}", 0) or 0) & 0xFFFFFFFF
        out.extend(word * 32 + bit for bit in range(32) if value & (1 << bit))
    return out


def _nonzero_of(rule: SidecarRule, row: dict[str, Any]) -> dict[str, Any]:
    """The row's consequential columns, identity and key dropped."""
    return {column: value for column, value in row.items()
            if column not in ("ID", rule.key) and not _is_zero(value)}


def _is_zero(value: Any) -> bool:
    if value in ("", None):
        return True
    try:
        return float(value) == 0.0
    except (TypeError, ValueError):
        return False


SIDECAR_POLICY: dict[str, SidecarRule] = {
    "AccountStoreItem": SidecarRule(
        table="AccountStoreItem",
        key="SpellID",
        policy_class="C",
        inert=True,
        presence_blocks=False,
        blocking=[],
        columns=["Name_lang", "Description_lang", "ID", "StoreFrontID", "AccountStoreCategoryID", "OrderIndex", "Price", "CurrencyTypesID", "Field_11_0_7_57361_008", "RefundDuration", "Field_11_0_7_57361_010", "Field_11_0_7_57361_011", "SpellID", "TransmogSetID", "CreatureDisplayInfoID", "UiModelSceneID", "Icon", "Field_12_0_0_63534_017", "Field_12_0_0_63534_018", "Field_12_0_0_63534_019"],
        justification=(
            "account shop entry that grants the spell; an inbound grant reference does not gate"
            "the trait-granted application"
        ),
        evidence="source",
        core=[],
        trinity=["no loader in the pinned Trinity tree (no DB2Storage declared in DataStores/DB2Stores.cpp); no Trinity consumer != no Retail behaviour"],
    ),
    "ArenaTrackedItem": SidecarRule(
        table="ArenaTrackedItem",
        key="SpellID",
        policy_class="C",
        inert=True,
        presence_blocks=False,
        blocking=[],
        columns=["ID", "ItemID", "SpellID", "ItemType"],
        justification=(
            "arena item tracking reference; an inbound grant reference does not gate the trait-"
            "granted application"
        ),
        evidence="source",
        core=[],
        trinity=["no loader in the pinned Trinity tree (no DB2Storage declared in DataStores/DB2Stores.cpp); no Trinity consumer != no Retail behaviour"],
    ),
    "ArtifactPowerRank": SidecarRule(
        table="ArtifactPowerRank",
        key="SpellID",
        policy_class="C",
        inert=True,
        presence_blocks=False,
        blocking=[],
        columns=["ID", "RankIndex", "SpellID", "ItemBonusListID", "AuraPointsOverride", "ArtifactPowerID"],
        justification=(
            "artifact-power rank that grants the spell; an inbound grant reference does not"
            "gate the trait-granted application"
        ),
        evidence="source",
        core=[],
        trinity=["src/server/game/DataStores/DB2Stores.cpp:59"],
    ),
    "AssistedCombatStep": SidecarRule(
        table="AssistedCombatStep",
        key="SpellID",
        policy_class="A",
        inert=True,
        presence_blocks=False,
        blocking=[],
        columns=["ID", "SpellID", "AssistedCombatID", "OrderIndex"],
        justification=(
            "assisted-combat rotation-helper ordering; no application semantics"
        ),
        evidence="source",
        core=[],
        trinity=["no loader in the pinned Trinity tree (no DB2Storage declared in DataStores/DB2Stores.cpp); no Trinity consumer != no Retail behaviour"],
    ),
    "AzeritePower": SidecarRule(
        table="AzeritePower",
        key="SpellID",
        policy_class="C",
        inert=True,
        presence_blocks=False,
        blocking=[],
        columns=["ID", "SpellID", "ItemBonusListID", "SpecSetID", "Flags"],
        justification=(
            "azerite power that grants the spell; an inbound grant reference does not gate the"
            "trait-granted application"
        ),
        evidence="source",
        core=[],
        trinity=["src/server/game/DataStores/DB2Stores.cpp:71"],
    ),
    "CastableRaidBuffs": SidecarRule(
        table="CastableRaidBuffs",
        key="SpellID",
        policy_class="A",
        inert=True,
        presence_blocks=False,
        blocking=[],
        columns=["ID", "CastingSpellID", "SpellID"],
        justification=(
            "client raid-buff list membership"
        ),
        evidence="source",
        core=[],
        trinity=["no loader in the pinned Trinity tree (no DB2Storage declared in DataStores/DB2Stores.cpp); no Trinity consumer != no Retail behaviour"],
    ),
    "CharShipment": SidecarRule(
        table="CharShipment",
        key="SpellID",
        policy_class="C",
        inert=True,
        presence_blocks=False,
        blocking=[],
        columns=["ID", "ContainerID", "DummyItemID", "TreasureID", "SpellID", "OnCompleteSpellID", "Duration", "MaxShipments", "GarrFollowerID", "Flags"],
        justification=(
            "garrison shipment that grants the spell; an inbound grant reference does not gate"
            "the trait-granted application"
        ),
        evidence="source",
        core=[],
        trinity=["no loader in the pinned Trinity tree (no DB2Storage declared in DataStores/DB2Stores.cpp); no Trinity consumer != no Retail behaviour"],
    ),
    "ChrUpgradeBucketSpell": SidecarRule(
        table="ChrUpgradeBucketSpell",
        key="SpellID",
        policy_class="C",
        inert=True,
        presence_blocks=False,
        blocking=[],
        columns=["ID", "SpellID", "ChrUpgradeBucketID"],
        justification=(
            "character-upgrade bucket that grants the spell; an inbound grant reference does"
            "not gate the trait-granted application"
        ),
        evidence="source",
        core=[],
        trinity=["no loader in the pinned Trinity tree (no DB2Storage declared in DataStores/DB2Stores.cpp); no Trinity consumer != no Retail behaviour"],
    ),
    "CommentatorTrackedCooldown": SidecarRule(
        table="CommentatorTrackedCooldown",
        key="SpellID",
        policy_class="A",
        inert=True,
        presence_blocks=False,
        blocking=[],
        columns=["ID", "SpellID", "Priority", "Flags", "ClassID", "RaceID", "ChrSpecID"],
        justification=(
            "arena commentator UI tracking"
        ),
        evidence="source",
        core=[],
        trinity=["no loader in the pinned Trinity tree (no DB2Storage declared in DataStores/DB2Stores.cpp); no Trinity consumer != no Retail behaviour"],
    ),
    "CooldownSetLinkedSpell": SidecarRule(
        table="CooldownSetLinkedSpell",
        key="SpellID",
        policy_class="A",
        inert=True,
        presence_blocks=False,
        blocking=[],
        columns=["ID", "SpellID", "Field_11_1_5_59571_001", "CooldownSetSpellID"],
        justification=(
            "client cooldown-manager link"
        ),
        evidence="source",
        core=[],
        trinity=["no loader in the pinned Trinity tree (no DB2Storage declared in DataStores/DB2Stores.cpp); no Trinity consumer != no Retail behaviour"],
    ),
    "CooldownSetSpell": SidecarRule(
        table="CooldownSetSpell",
        key="SpellID",
        policy_class="A",
        inert=True,
        presence_blocks=False,
        blocking=[],
        columns=["ID", "CooldownSetID", "SpellID", "Category", "PlayerConditionID", "OrderIndex", "Flags", "SpellCategoryID", "EquipSlot", "Field_12_1_0_68209_009"],
        justification=(
            "client cooldown-manager grouping and ordering"
        ),
        evidence="source",
        core=[],
        trinity=["no loader in the pinned Trinity tree (no DB2Storage declared in DataStores/DB2Stores.cpp); no Trinity consumer != no Retail behaviour"],
    ),
    "ExtraAbilityInfo": SidecarRule(
        table="ExtraAbilityInfo",
        key="SpellID",
        policy_class="A",
        inert=True,
        presence_blocks=False,
        blocking=[],
        columns=["ID", "TutorialText_lang", "SpellID", "ActionBarOverrideSpellID", "LabelID", "UiTextureKitID", "UiPriority"],
        justification=(
            "tutorial text and action-bar override art"
        ),
        evidence="source",
        core=[],
        trinity=["no loader in the pinned Trinity tree (no DB2Storage declared in DataStores/DB2Stores.cpp); no Trinity consumer != no Retail behaviour"],
    ),
    "FlightCapability": SidecarRule(
        table="FlightCapability",
        key="SpellID",
        policy_class="C",
        inert=True,
        presence_blocks=False,
        blocking=[],
        columns=["ID", "AirFriction", "MaxVel", "Field_10_0_0_44167_002", "DoubleJumpVelMod", "LiftCoefficient", "GlideStartMinHeight", "AddImpulseMaxSpeed", "BankingRateMin", "BankingRateMax", "PitchingRateDownMin", "PitchingRateDownMax", "PitchingRateUpMin", "PitchingRateUpMax", "TurnVelocityThresholdMin", "TurnVelocityThresholdMax", "SurfaceFriction", "OverMaxDeceleration", "Field_10_0_0_45232_017", "Field_10_0_0_45232_018", "Field_10_0_0_45232_019", "Field_10_0_0_45232_020", "Field_10_0_0_45232_021", "LaunchSpeedCoefficient", "Field_10_1_5_49719_023", "SpellID"],
        justification=(
            "flight capability the spell selects; an inbound grant reference does not gate the"
            "trait-granted application"
        ),
        evidence="source",
        core=[],
        trinity=["src/server/game/Entities/Unit/Unit.cpp:9004"],
    ),
    "GarrAutoSpellEffect": SidecarRule(
        table="GarrAutoSpellEffect",
        key="SpellID",
        policy_class="A",
        inert=True,
        presence_blocks=False,
        blocking=[],
        columns=["ID", "SpellID", "EffectIndex", "Effect", "Points", "TargetType", "Flags", "Period"],
        justification=(
            "garrison follower auto-combat mini-game effects; a separate resolution system"
        ),
        evidence="source",
        core=[],
        trinity=["no loader in the pinned Trinity tree (no DB2Storage declared in DataStores/DB2Stores.cpp); no Trinity consumer != no Retail behaviour"],
    ),
    "GlideEvent": SidecarRule(
        table="GlideEvent",
        key="SpellID",
        policy_class="A",
        inert=True,
        presence_blocks=False,
        blocking=[],
        columns=["ID", "AfterTimeInAnimation", "AnimationDataID", "AnimKitID", "Field_10_0_0_44795_002", "Field_10_0_0_44795_003", "Flags", "SpellID", "LabelID", "Field_10_0_0_44795_006", "Field_10_0_0_45232_007", "DefaultBlendIn", "DefaultBlendOut"],
        justification=(
            "dragonriding animation event art"
        ),
        evidence="source",
        core=[],
        trinity=["no loader in the pinned Trinity tree (no DB2Storage declared in DataStores/DB2Stores.cpp); no Trinity consumer != no Retail behaviour"],
    ),
    "GlyphBindableSpell": SidecarRule(
        table="GlyphBindableSpell",
        key="SpellID",
        policy_class="C",
        inert=True,
        presence_blocks=False,
        blocking=[],
        columns=["ID", "SpellID", "GlyphPropertiesID"],
        justification=(
            "glyph bindability reference; an inbound grant reference does not gate the trait-"
            "granted application"
        ),
        evidence="source",
        core=[],
        trinity=["src/server/game/DataStores/DB2Stores.cpp:167"],
    ),
    "GlyphProperties": SidecarRule(
        table="GlyphProperties",
        key="SpellID",
        policy_class="C",
        inert=True,
        presence_blocks=False,
        blocking=[],
        columns=["ID", "SpellID", "GlyphType", "GlyphExclusiveCategoryID", "SpellIconFileDataID"],
        justification=(
            "glyph that grants the spell; an inbound grant reference does not gate the trait-"
            "granted application"
        ),
        evidence="source",
        core=[],
        trinity=["src/server/game/Spells/SpellEffects.cpp:3417"],
    ),
    "GuildPerkSpells": SidecarRule(
        table="GuildPerkSpells",
        key="SpellID",
        policy_class="C",
        inert=True,
        presence_blocks=False,
        blocking=[],
        columns=["ID", "SpellID"],
        justification=(
            "guild perk that grants the spell; an inbound grant reference does not gate the"
            "trait-granted application"
        ),
        evidence="source",
        core=[],
        trinity=["src/server/game/Guilds/Guild.cpp:2322"],
    ),
    "ItemBonusSequenceSpell": SidecarRule(
        table="ItemBonusSequenceSpell",
        key="SpellID",
        policy_class="C",
        inert=True,
        presence_blocks=False,
        blocking=[],
        columns=["ID", "SpellID", "ItemID"],
        justification=(
            "item-bonus sequence reference; an inbound grant reference does not gate the trait-"
            "granted application"
        ),
        evidence="source",
        core=[],
        trinity=["no loader in the pinned Trinity tree (no DB2Storage declared in DataStores/DB2Stores.cpp); no Trinity consumer != no Retail behaviour"],
    ),
    "ItemEffect": SidecarRule(
        table="ItemEffect",
        key="SpellID",
        policy_class="C",
        inert=True,
        presence_blocks=False,
        blocking=[],
        columns=["ID", "LegacySlotIndex", "TriggerType", "Charges", "CoolDownMSec", "CategoryCoolDownMSec", "SpellCategoryID", "SpellID", "ChrSpecializationID", "PlayerConditionID"],
        justification=(
            "item that casts or grants the spell; an inbound grant reference does not gate the"
            "trait-granted application"
        ),
        evidence="source",
        core=[],
        trinity=["src/server/game/DataStores/DB2Stores.cpp:202"],
    ),
    "ItemSetSpell": SidecarRule(
        table="ItemSetSpell",
        key="SpellID",
        policy_class="C",
        inert=True,
        presence_blocks=False,
        blocking=[],
        columns=["ID", "ChrSpecID", "SpellID", "TraitSubTreeID", "Threshold", "ItemSetID"],
        justification=(
            "item-set bonus that grants the spell; an inbound grant reference does not gate the"
            "trait-granted application"
        ),
        evidence="source",
        core=[],
        trinity=["src/server/game/DataStores/DB2Stores.cpp:218"],
    ),
    "JournalEncounterSection": SidecarRule(
        table="JournalEncounterSection",
        key="SpellID",
        policy_class="A",
        inert=True,
        presence_blocks=False,
        blocking=[],
        columns=["ID", "Title_lang", "BodyText_lang", "JournalEncounterID", "OrderIndex", "ParentSectionID", "FirstChildSectionID", "NextSiblingSectionID", "Type", "IconCreatureDisplayInfoID", "UiModelSceneID", "SpellID", "IconFileDataID", "Flags", "IconFlags", "DifficultyMask"],
        justification=(
            "dungeon-journal body text naming the spell"
        ),
        evidence="source",
        core=[],
        trinity=["src/server/game/DataStores/DB2Stores.cpp:226 (loaded); journal text only"],
    ),
    "LiquidType": SidecarRule(
        table="LiquidType",
        key="SpellID",
        policy_class="C",
        inert=True,
        presence_blocks=False,
        blocking=[],
        columns=["ID", "Name", "Texture_0", "Texture_1", "Texture_2", "Texture_3", "Texture_4", "Texture_5", "Flags", "SoundBank", "SoundID", "SpellID", "MaxDarkenDepth", "FogDarkenIntensity", "AmbDarkenIntensity", "DirDarkenIntensity", "LightID", "ParticleScale", "ParticleMovement", "ParticleTexSlots", "MaterialID", "MinimapStaticCol", "FrameCountTexture_0", "FrameCountTexture_1", "FrameCountTexture_2", "FrameCountTexture_3", "FrameCountTexture_4", "FrameCountTexture_5", "Color_0", "Color_1", "Color_2", "Float_0", "Float_1", "Float_2", "Float_3", "Float_4", "Float_5", "Float_6", "Float_7", "Float_8", "Float_9", "Float_10", "Float_11", "Float_12", "Float_13", "Float_14", "Float_15", "Float_16", "Float_17", "Float_18", "Float_19", "Float_20", "Float_21", "Float_22", "Float_23", "Float_24", "Float_25", "Float_26", "Float_27", "Float_28", "Float_29", "Float_30", "Float_31", "Float_32", "Float_33", "Float_34", "Float_35", "Float_36", "Float_37", "Int_0", "Int_1", "Int_2", "Int_3", "Coefficient_0", "Coefficient_1", "Coefficient_2", "Coefficient_3"],
        justification=(
            "liquid that applies the spell on immersion; an inbound grant reference does not"
            "gate the trait-granted application"
        ),
        evidence="source",
        core=[],
        trinity=["src/server/game/DataStores/DB2Stores.cpp:235"],
    ),
    "MawPower": SidecarRule(
        table="MawPower",
        key="SpellID",
        policy_class="C",
        inert=True,
        presence_blocks=False,
        blocking=[],
        columns=["ID", "SpellID", "MawPowerRarityID"],
        justification=(
            "Maw power that grants the spell; an inbound grant reference does not gate the"
            "trait-granted application"
        ),
        evidence="source",
        core=[],
        trinity=["src/server/game/DataStores/DB2Stores.cpp:243"],
    ),
    "MinorTalent": SidecarRule(
        table="MinorTalent",
        key="SpellID",
        policy_class="C",
        inert=True,
        presence_blocks=False,
        blocking=[],
        columns=["ID", "SpellID", "OrderIndex", "ChrSpecializationID"],
        justification=(
            "legacy minor-talent grant; an inbound grant reference does not gate the trait-"
            "granted application"
        ),
        evidence="source",
        core=[],
        trinity=["no loader in the pinned Trinity tree (no DB2Storage declared in DataStores/DB2Stores.cpp); no Trinity consumer != no Retail behaviour"],
    ),
    "ModifiedCraftingSpellSlot": SidecarRule(
        table="ModifiedCraftingSpellSlot",
        key="SpellID",
        policy_class="G",
        inert=False,
        presence_blocks=False,
        blocking=["Field_9_0_1_35679_003", "ModifiedCraftingReagentSlotID", "ReagentCount", "ReagentReCraftCount", "Slot"],
        columns=["ID", "SpellID", "Slot", "ModifiedCraftingReagentSlotID", "Field_9_0_1_35679_003", "ReagentCount", "ReagentReCraftCount"],
        justification=(
            "modified-crafting reagent slots imply a crafting activation"
        ),
        evidence="source",
        core=[],
        trinity=["no loader in the pinned Trinity tree (no DB2Storage declared in DataStores/DB2Stores.cpp); no Trinity consumer != no Retail behaviour"],
        inert_columns=[],
    ),
    "PvpTalent": SidecarRule(
        table="PvpTalent",
        key="SpellID",
        policy_class="C",
        inert=True,
        presence_blocks=False,
        blocking=[],
        columns=["Description_lang", "ID", "SpecID", "SpellID", "OverridesSpellID", "Flags", "ActionBarSpellID", "PvpTalentCategoryID", "LevelRequired", "PlayerConditionID"],
        justification=(
            "PvP talent that grants the spell; an inbound grant reference does not gate the"
            "trait-granted application"
        ),
        evidence="source",
        core=[],
        trinity=["src/server/game/DataStores/DB2Stores.cpp:277"],
    ),
    "QuestFeedbackEffect": SidecarRule(
        table="QuestFeedbackEffect",
        key="SpellID",
        policy_class="A",
        inert=True,
        presence_blocks=False,
        blocking=[],
        columns=["ID", "InteractCursor", "FileDataID", "MinimapAtlasMemberID", "AttachPoint", "PassiveHighlightColorType", "Priority", "Flags", "SpellID"],
        justification=(
            "quest-objective cursor/minimap feedback art"
        ),
        evidence="source",
        core=[],
        trinity=["no loader in the pinned Trinity tree (no DB2Storage declared in DataStores/DB2Stores.cpp); no Trinity consumer != no Retail behaviour"],
    ),
    "RTPCData": SidecarRule(
        table="RTPCData",
        key="SpellID",
        policy_class="A",
        inert=True,
        presence_blocks=False,
        blocking=[],
        columns=["ID", "RTPCID", "Field_9_0_1_33978_001", "CreatureID", "SpellID", "Field_9_0_1_33978_004"],
        justification=(
            "audio real-time parameter control rows"
        ),
        evidence="source",
        core=[],
        trinity=["no loader in the pinned Trinity tree (no DB2Storage declared in DataStores/DB2Stores.cpp); no Trinity consumer != no Retail behaviour"],
    ),
    "RenownRewards": SidecarRule(
        table="RenownRewards",
        key="SpellID",
        policy_class="C",
        inert=True,
        presence_blocks=False,
        blocking=[],
        columns=["ID", "Name_lang", "Description_lang", "ToastDescription_lang", "CovenantID", "Level", "Icon", "Flags", "UiOrder", "ItemID", "SpellID", "MountID", "TransmogID", "TransmogSetID", "CharTitlesID", "GarrFollowerID", "TransmogIllusionID", "Field_12_0_0_63534_016", "QuestID", "PlayerConditionID"],
        justification=(
            "covenant renown reward that grants the spell; an inbound grant reference does not"
            "gate the trait-granted application"
        ),
        evidence="source",
        core=[],
        trinity=["no loader in the pinned Trinity tree (no DB2Storage declared in DataStores/DB2Stores.cpp); no Trinity consumer != no Retail behaviour"],
    ),
    "RenownRewardsPlunderstorm": SidecarRule(
        table="RenownRewardsPlunderstorm",
        key="SpellID",
        policy_class="C",
        inert=True,
        presence_blocks=False,
        blocking=[],
        columns=["ID", "Name_lang", "Description_lang", "CovenantID", "Level", "Icon", "Field_10_2_6_53840_005", "UiOrder", "SpellID"],
        justification=(
            "Plunderstorm renown reward; an inbound grant reference does not gate the trait-"
            "granted application"
        ),
        evidence="source",
        core=[],
        trinity=["no loader in the pinned Trinity tree (no DB2Storage declared in DataStores/DB2Stores.cpp); no Trinity consumer != no Retail behaviour"],
    ),
    "ResearchProject": SidecarRule(
        table="ResearchProject",
        key="SpellID",
        policy_class="C",
        inert=True,
        presence_blocks=False,
        blocking=[],
        columns=["ID", "Name_lang", "Description_lang", "Rarity", "SpellID", "ResearchBranchID", "NumSockets", "TextureFileID", "RequiredWeight"],
        justification=(
            "archaeology project that grants the spell; an inbound grant reference does not"
            "gate the trait-granted application"
        ),
        evidence="source",
        core=[],
        trinity=["no loader in the pinned Trinity tree (no DB2Storage declared in DataStores/DB2Stores.cpp); no Trinity consumer != no Retail behaviour"],
    ),
    "RuneforgeLegendaryAbility": SidecarRule(
        table="RuneforgeLegendaryAbility",
        key="SpellID",
        policy_class="C",
        inert=True,
        presence_blocks=False,
        blocking=[],
        columns=["Name_lang", "ID", "SpecSetID", "InventoryTypeMask", "SpellID", "ItemBonusListID", "PlayerConditionID", "Field_9_0_1_34972_007", "UnlockItemID", "CovenantID", "Field_9_1_0_38549_010"],
        justification=(
            "runeforge legendary that grants the spell; an inbound grant reference does not"
            "gate the trait-granted application"
        ),
        evidence="source",
        core=[],
        trinity=["no loader in the pinned Trinity tree (no DB2Storage declared in DataStores/DB2Stores.cpp); no Trinity consumer != no Retail behaviour"],
    ),
    "SoulbindConduitRank": SidecarRule(
        table="SoulbindConduitRank",
        key="SpellID",
        policy_class="C",
        inert=True,
        presence_blocks=False,
        blocking=[],
        columns=["ID", "RankIndex", "SpellID", "AuraPointsOverride", "SoulbindConduitID"],
        justification=(
            "soulbind conduit rank that grants the spell; an inbound grant reference does not"
            "gate the trait-granted application"
        ),
        evidence="source",
        core=[],
        trinity=["src/server/game/DataStores/DB2Stores.cpp:304"],
    ),
    "SourceInfo": SidecarRule(
        table="SourceInfo",
        key="SpellID",
        policy_class="C",
        inert=True,
        presence_blocks=False,
        blocking=[],
        columns=["SourceText_lang", "ID", "ItemID", "PvpFaction", "SourceTypeEnum", "SpellID"],
        justification=(
            "client 'source' provenance text; an inbound grant reference does not gate the"
            "trait-granted application"
        ),
        evidence="source",
        core=[],
        trinity=["no loader in the pinned Trinity tree (no DB2Storage declared in DataStores/DB2Stores.cpp); no Trinity consumer != no Retail behaviour"],
    ),
    "SpecializationSpells": SidecarRule(
        table="SpecializationSpells",
        key="SpellID",
        policy_class="C",
        inert=True,
        presence_blocks=False,
        blocking=[],
        columns=["Description_lang", "ID", "SpecID", "SpellID", "OverridesSpellID", "DisplayOrder"],
        justification=(
            "specialization grant; the learn path is level-gated at Player.cpp:30639, which the"
            "trait path never takes; an inbound grant reference does not gate the trait-granted"
            "application"
        ),
        evidence="source",
        core=[],
        trinity=["src/server/game/Entities/Player/Player.cpp:30639"],
    ),
    "Spell": SidecarRule(
        table="Spell",
        key="ID",
        policy_class="A",
        inert=True,
        presence_blocks=False,
        blocking=[],
        columns=["ID", "NameSubtext_lang", "Description_lang", "AuraDescription_lang"],
        justification=(
            "subtext/description/aura-description strings"
        ),
        evidence="source",
        core=[],
        trinity=["no sSpellStore in the pinned tree; strings are client-side"],
    ),
    "SpellActionBarPref": SidecarRule(
        table="SpellActionBarPref",
        key="SpellID",
        policy_class="A",
        inert=True,
        presence_blocks=False,
        blocking=[],
        columns=["ID", "SpellID", "PreferredActionBarMask"],
        justification=(
            "preferred action-bar placement mask"
        ),
        evidence="source",
        core=[],
        trinity=["no loader in the pinned Trinity tree (no DB2Storage declared in DataStores/DB2Stores.cpp); no Trinity consumer != no Retail behaviour"],
    ),
    "SpellActivationOverlay": SidecarRule(
        table="SpellActivationOverlay",
        key="SpellID",
        policy_class="A",
        inert=True,
        presence_blocks=False,
        blocking=[],
        columns=["ID", "IconHighlightSpellClassMask_0", "IconHighlightSpellClassMask_1", "IconHighlightSpellClassMask_2", "IconHighlightSpellClassMask_3", "SpellID", "OverlayFileDataID", "ScreenLocationID", "SoundEntriesID", "Color", "Field_9_1_5_39977_006", "Scale", "TriggerType", "TriggerSpell"],
        justification=(
            "spell-alert screen overlay art"
        ),
        evidence="source",
        core=[],
        trinity=["no loader in the pinned Trinity tree (no DB2Storage declared in DataStores/DB2Stores.cpp); no Trinity consumer != no Retail behaviour"],
    ),
    "SpellAuraOptions": SidecarRule(
        table="SpellAuraOptions",
        key="SpellID",
        policy_class="F",
        inert=False,
        presence_blocks=False,
        blocking=["CumulativeAura", "ProcCategoryRecovery", "ProcChance", "ProcCharges",
                  "ProcTypeMask_0", "ProcTypeMask_1", "SpellProcsPerMinuteID"],
        columns=["ID", "DifficultyID", "CumulativeAura", "ProcCategoryRecovery", "ProcChance", "ProcCharges", "SpellProcsPerMinuteID", "ProcTypeMask_0", "ProcTypeMask_1", "SpellID"],
        justification=(
            "stack capacity, proc chance, charges, PPM and the proc-event mask.  Core "
            "requires the row absent outright unless a named package contract pins it "
            "exactly; this rule is deliberately WEAKER and proves inertness instead: a "
            "SpellAuraOptions row generates no runtime behaviour when Trinity produces no "
            "SpellProcEntry for the owner, and CumulativeAura is dormant because a "
            "learn-time passive is applied exactly once and never reapplied"
        ),
        evidence="trinity",
        core=["crates/combat/src/program/exact_source.rs:431-441",
              "crates/combat/src/program/selected_trait_package.rs:263-292",
              "crates/combat/src/program/passive_spell_modifier.rs:1112-1115 "
              "(the authored maximum stack remains dormant source policy)"],
        trinity=["src/server/game/Spells/SpellMgr.cpp:1758-1767 (no ProcFlags -> no generated entry)",
                 "src/server/game/Spells/SpellMgr.cpp:1807-1820 (no trigger-aura effect -> no generated entry)",
                 "src/server/game/Spells/Auras/SpellAuras.cpp:1007 (CheckProc runs only with an entry)",
                 "src/server/game/Spells/SpellMgr.cpp:503 GetSpellProcEntry"],
        inert_columns=["DifficultyID"],
    ),
    "SpellAuraRestrictions": SidecarRule(
        table="SpellAuraRestrictions",
        key="SpellID",
        policy_class="D",
        inert=False,
        presence_blocks=True,
        blocking=[],
        columns=["ID", "DifficultyID", "CasterAuraState", "TargetAuraState", "ExcludeCasterAuraState", "ExcludeTargetAuraState", "CasterAuraSpell", "TargetAuraSpell", "ExcludeCasterAuraSpell", "ExcludeTargetAuraSpell", "CasterAuraType", "TargetAuraType", "ExcludeCasterAuraType", "ExcludeTargetAuraType", "SpellID"],
        justification=(
            "caster/target aura-state or aura-spell condition gates application; Core requires"
            "the row to be absent outright"
        ),
        evidence="core",
        core=["crates/combat/src/program/passive_spell_modifier.rs:1100-1102"],
        trinity=["src/server/game/Spells/Spell.cpp:5872-5875 (CasterAuraState / ExcludeCasterAuraState)", "src/server/game/Spells/SpellInfo.cpp:1401-1403"],
    ),
    "SpellAuraVisibility": SidecarRule(
        table="SpellAuraVisibility",
        key="SpellID",
        policy_class="A",
        inert=True,
        presence_blocks=False,
        blocking=[],
        columns=["ID", "Type", "Flags", "SpellID"],
        justification=(
            "aura-frame visibility type/flags for the client"
        ),
        evidence="source",
        core=[],
        trinity=["no loader in the pinned Trinity tree (no DB2Storage declared in DataStores/DB2Stores.cpp); no Trinity consumer != no Retail behaviour"],
    ),
    "SpellCastingRequirements": SidecarRule(
        table="SpellCastingRequirements",
        key="SpellID",
        policy_class="D",
        inert=False,
        presence_blocks=False,
        blocking=["FacingCasterFlags", "MinFactionID", "MinReputation", "RequiredAreasID", "RequiredAuraVision", "RequiresSpellFocus"],
        columns=["ID", "SpellID", "FacingCasterFlags", "MinFactionID", "MinReputation", "RequiredAreasID", "RequiredAuraVision", "RequiresSpellFocus"],
        justification=(
            "area, reputation, facing, spell-focus and aura-vision requirements gate a cast;"
            "every nonzero field is a condition Core has not proved inert"
        ),
        evidence="source",
        core=[],
        trinity=["src/server/game/Spells/SpellMgr.cpp:2551"],
        inert_columns=[],
    ),
    "SpellCategories": SidecarRule(
        table="SpellCategories",
        key="SpellID",
        policy_class="D",
        inert=False,
        presence_blocks=False,
        blocking=["DefenseType", "DispelType", "Mechanic"],
        columns=["ID", "DifficultyID", "Category", "DefenseType", "DiminishType", "DispelType", "Mechanic", "PreventionType", "StartRecoveryCategory", "ChargeCategory", "SpellID"],
        justification=(
            "Core requires DefenseType/DispelType/Mechanic neutral on the owner; Category,"
            "DiminishType, PreventionType, StartRecoveryCategory and ChargeCategory are"
            "cooldown/DR bookkeeping that a permanent passive never reaches"
        ),
        evidence="core",
        core=["crates/combat/src/program/passive_spell_modifier.rs:952-959", "crates/combat/src/program/exact_source.rs:296-306"],
        trinity=["src/server/game/DataStores/DB2Stores.cpp:312"],
        inert_columns=["DifficultyID", "Category", "DiminishType", "PreventionType", "StartRecoveryCategory", "ChargeCategory"],
    ),
    "SpellClassOptions": SidecarRule(
        table="SpellClassOptions",
        key="SpellID",
        policy_class="D",
        inert=False,
        presence_blocks=False,
        blocking=["ModalNextSpell", "SpellClassMask_0", "SpellClassMask_1", "SpellClassMask_2", "SpellClassMask_3"],
        columns=["ID", "SpellID", "ModalNextSpell", "SpellClassSet", "SpellClassMask_0", "SpellClassMask_1", "SpellClassMask_2", "SpellClassMask_3"],
        justification=(
            "Core requires the owner's own class mask empty; SpellClassSet (family) is an owner"
            "identity fact that is required NONZERO when the package selector is ClassMask,"
            "handled separately"
        ),
        evidence="core",
        core=["crates/combat/src/program/passive_spell_modifier.rs:951-956", "crates/data/src/game_data.rs:599-611"],
        trinity=["src/server/game/Spells/SpellInfo.cpp:2002-2007 IsAffectedBySpellMod"],
        inert_columns=["SpellClassSet"],
    ),
    "SpellCooldowns": SidecarRule(
        table="SpellCooldowns",
        key="SpellID",
        policy_class="G",
        inert=False,
        presence_blocks=False,
        blocking=["AuraSpellID", "CategoryRecoveryTime", "RecoveryTime", "StartRecoveryTime"],
        columns=["ID", "DifficultyID", "CategoryRecoveryTime", "RecoveryTime", "StartRecoveryTime", "AuraSpellID", "SpellID"],
        justification=(
            "authored recovery implies an activation with a readiness deadline, not an"
            "immutable passive"
        ),
        evidence="source",
        core=[],
        trinity=["src/server/game/DataStores/DB2Stores.cpp:315"],
        inert_columns=["DifficultyID"],
    ),
    "SpellEffect": SidecarRule(
        table="SpellEffect",
        key="SpellID",
        policy_class="B",
        inert=True,
        presence_blocks=False,
        blocking=[],
        columns=["ID", "EffectAura", "DifficultyID", "EffectIndex", "Effect", "EffectAmplitude", "EffectAttributes", "EffectAuraPeriod", "EffectBonusCoefficient", "EffectChainAmplitude", "EffectChainTargets", "EffectItemType", "EffectMechanic", "EffectPointsPerResource", "EffectPos_facing", "EffectRealPointsPerLevel", "EffectTriggerSpell", "BonusCoefficientFromAP", "PvpMultiplier", "Coefficient", "Variance", "ResourceCoefficient", "GroupSizeBasePointsCoefficient", "EffectBasePointsF", "ScalingClass", "Node__Field_12_0_0_63534_001", "EffectMiscValue_0", "EffectMiscValue_1", "EffectRadiusIndex_0", "EffectRadiusIndex_1", "EffectSpellClassMask_0", "EffectSpellClassMask_1", "EffectSpellClassMask_2", "EffectSpellClassMask_3", "ImplicitTarget_0", "ImplicitTarget_1", "SpellID"],
        justification=(
            "the payload itself; classified per exact effect by selected_package.effects, never"
            "here"
        ),
        evidence="core",
        core=["crates/combat/src/program/selected_trait_package.rs (SelectedTraitEffectRole)"],
        trinity=["src/server/game/Globals/ObjectMgr.cpp:5883"],
    ),
    "SpellEmpower": SidecarRule(
        table="SpellEmpower",
        key="SpellID",
        policy_class="G",
        inert=False,
        presence_blocks=False,
        blocking=["Field_10_0_0_44649_002"],
        columns=["ID", "SpellID", "Field_10_0_0_44649_002"],
        justification=(
            "an empower stage table implies a held, staged cast"
        ),
        evidence="source",
        core=[],
        trinity=["src/server/game/Spells/SpellMgr.cpp:2565"],
        inert_columns=[],
    ),
    "SpellEquippedItems": SidecarRule(
        table="SpellEquippedItems",
        key="SpellID",
        policy_class="D",
        inert=False,
        presence_blocks=True,
        blocking=[],
        columns=["ID", "SpellID", "EquippedItemClass", "EquippedItemInvTypes", "EquippedItemSubclass"],
        justification=(
            "equipment condition gates application; Trinity adds and removes the passive aura"
            "as the item is equipped and unequipped"
        ),
        evidence="core",
        core=["crates/combat/src/program/exact_source.rs:432-437"],
        trinity=["src/server/game/Entities/Player/Player.cpp:8285-8296 ApplyItemDependentAuras", "src/server/game/Entities/Player/Player.cpp:3089-3096"],
    ),
    "SpellFlyoutItem": SidecarRule(
        table="SpellFlyoutItem",
        key="SpellID",
        policy_class="A",
        inert=True,
        presence_blocks=False,
        blocking=[],
        columns=["ID", "SpellID", "Slot", "SpellFlyoutID"],
        justification=(
            "flyout-menu slot placement"
        ),
        evidence="source",
        core=[],
        trinity=["no loader in the pinned Trinity tree (no DB2Storage declared in DataStores/DB2Stores.cpp); no Trinity consumer != no Retail behaviour"],
    ),
    "SpellInterrupts": SidecarRule(
        table="SpellInterrupts",
        key="SpellID",
        policy_class="E",
        inert=False,
        presence_blocks=False,
        blocking=["AuraInterruptFlags_0", "AuraInterruptFlags_1"],
        columns=["ID", "DifficultyID", "InterruptFlags", "AuraInterruptFlags_0", "AuraInterruptFlags_1", "ChannelInterruptFlags_0", "ChannelInterruptFlags_1", "SpellID"],
        justification=(
            "AuraInterruptFlags make an existing application removable, so the preparation is"
            "not immutable; InterruptFlags and ChannelInterruptFlags govern an in-progress cast"
            "or channel, which a Passive owner never has"
        ),
        evidence="trinity",
        core=[],
        trinity=["src/server/game/Entities/Unit/Unit.cpp:549-554 RemoveAurasWithInterruptFlags", "src/server/game/Spells/SpellMgr.cpp:2545"],
        inert_columns=["DifficultyID", "InterruptFlags", "ChannelInterruptFlags_0", "ChannelInterruptFlags_1"],
    ),
    "SpellLabel": SidecarRule(
        table="SpellLabel",
        key="SpellID",
        policy_class="H",
        inert=True,
        presence_blocks=False,
        blocking=[],
        columns=["ID", "LabelID", "SpellID"],
        justification=(
            "labels make this spell selectable as somebody else's modifier payload; they say"
            "nothing about the owner's own application"
        ),
        evidence="trinity",
        core=[],
        trinity=["src/server/game/Spells/SpellInfo.cpp:2009-2012 (HasLabel drives SPELLMOD_LABEL_*)"],
    ),
    "SpellLearnSpell": SidecarRule(
        table="SpellLearnSpell",
        key="SpellID",
        policy_class="C",
        inert=True,
        presence_blocks=False,
        blocking=[],
        columns=["ID", "SpellID", "LearnSpellID", "OverridesSpellID"],
        justification=(
            "learning this spell also learns another; an acquisition edge, not a lifetime fact;"
            "an inbound grant reference does not gate the trait-granted application"
        ),
        evidence="source",
        core=[],
        trinity=["src/server/game/Spells/SpellMgr.cpp:1106"],
    ),
    "SpellLevels": SidecarRule(
        table="SpellLevels",
        key="SpellID",
        policy_class="C",
        inert=True,
        presence_blocks=False,
        blocking=[],
        columns=["ID", "DifficultyID", "MaxLevel", "MaxPassiveAuraLevel", "BaseLevel", "SpellLevel", "SpellID"],
        justification=(
            "level requirement is an acquisition fact: the trait path (Player.cpp:29391-29410)"
            "calls LearnSpell with no level check, the only level gates are skill-line"
            "(Player.cpp:25429) and specialization (Player.cpp:30639) learning,"
            "MaxPassiveAuraLevel has no consumer at all, and the one value-affecting use"
            "(SpellInfo.cpp:578-588 basePointsPerLevel) is already excluded by Core's neutral-"
            "coefficient requirement EffectRealPointsPerLevel == 0"
        ),
        evidence="trinity",
        core=["crates/combat/src/program/exact_source.rs:196-205 (neutral amount facts)"],
        trinity=["src/server/game/Entities/Player/Player.cpp:29391-29410", "src/server/game/Entities/Player/Player.cpp:25429", "src/server/game/Entities/Player/Player.cpp:30639", "src/server/game/Spells/SpellInfo.cpp:578-588", "src/server/game/DataStores/DB2Structure.h:4043 (MaxPassiveAuraLevel loaded, never read)"],
    ),
    "SpellMisc": SidecarRule(
        table="SpellMisc",
        key="SpellID",
        policy_class="E",
        inert=False,
        presence_blocks=False,
        blocking=["DurationIndex", "LaunchDelay", "MinDuration", "PvPDurationIndex", "Speed"],
        columns=["ID", "Attributes_0", "Attributes_1", "Attributes_2", "Attributes_3", "Attributes_4", "Attributes_5", "Attributes_6", "Attributes_7", "Attributes_8", "Attributes_9", "Attributes_10", "Attributes_11", "Attributes_12", "Attributes_13", "Attributes_14", "Attributes_15", "Attributes_16", "DifficultyID", "CastingTimeIndex", "DurationIndex", "PvPDurationIndex", "RangeIndex", "SchoolMask", "Speed", "LaunchDelay", "MinDuration", "SpellIconFileDataID", "ActiveIconFileDataID", "ContentTuningID", "ShowFutureSpellPlayerConditionID", "SpellVisualScript", "ActiveSpellVisualScript", "SpellID"],
        justification=(
            "DurationIndex/PvPDurationIndex make the application finite;"
            "Speed/LaunchDelay/MinDuration are Core's `spell_missile` row and imply delivery"
            "timing.  SchoolMask and Attributes_* are owner identity facts checked separately;"
            "CastingTimeIndex/RangeIndex/ContentTuningID/icons are cast-time or presentation"
            "fields a Passive owner never reaches"
        ),
        evidence="core",
        core=["crates/combat/src/program/passive_spell_modifier.rs:1103-1106", "crates/combat/src/program/exact_source.rs:421-430", "crates/data/src/missile.rs:7-12"],
        trinity=["src/server/game/DataStores/DB2Stores.cpp:329"],
        inert_columns=["DifficultyID", "CastingTimeIndex", "RangeIndex", "SchoolMask", "SpellIconFileDataID", "ActiveIconFileDataID", "ContentTuningID", "ShowFutureSpellPlayerConditionID", "SpellVisualScript", "ActiveSpellVisualScript", "Attributes_0", "Attributes_1", "Attributes_2", "Attributes_3", "Attributes_4", "Attributes_5", "Attributes_6", "Attributes_7", "Attributes_8", "Attributes_9", "Attributes_10", "Attributes_11", "Attributes_12", "Attributes_13", "Attributes_14", "Attributes_15", "Attributes_16"],
    ),
    "SpellMissile": SidecarRule(
        table="SpellMissile",
        key="SpellID",
        policy_class="A",
        inert=True,
        presence_blocks=False,
        blocking=[],
        columns=["ID", "SpellID", "Flags", "DefaultPitchMin", "DefaultPitchMax", "DefaultSpeedMin", "DefaultSpeedMax", "RandomizeFacingMin", "RandomizeFacingMax", "RandomizePitchMin", "RandomizePitchMax", "RandomizeSpeedMin", "RandomizeSpeedMax", "Gravity", "MaxDuration", "CollisionRadius"],
        justification=(
            "missile ballistics art; NOT Core's `spell_missile` row, which is SpellMisc"
            "Speed/LaunchDelay/MinDuration (crates/data/src/missile.rs:7-12)"
        ),
        evidence="source",
        core=[],
        trinity=["no loader in the pinned Trinity tree (no DB2Storage declared in DataStores/DB2Stores.cpp); no Trinity consumer != no Retail behaviour"],
    ),
    "SpellName": SidecarRule(
        table="SpellName",
        key="ID",
        policy_class="A",
        inert=True,
        presence_blocks=False,
        blocking=[],
        columns=["ID", "Name_lang"],
        justification=(
            "the spell's display name"
        ),
        evidence="source",
        core=[],
        trinity=["src/server/game/DataStores/DB2Stores.cpp:330"],
    ),
    "SpellPower": SidecarRule(
        table="SpellPower",
        key="SpellID",
        policy_class="G",
        inert=False,
        presence_blocks=False,
        blocking=["AltPowerBarID", "ManaCost", "ManaCostPerLevel", "ManaPerSecond", "OptionalCost", "OptionalCostPct", "PowerCostMaxPct", "PowerCostPct", "PowerDisplayID", "PowerPctPerSecond", "PowerType", "RequiredAuraSpellID"],
        columns=["ID", "OrderIndex", "ManaCost", "ManaCostPerLevel", "ManaPerSecond", "PowerDisplayID", "AltPowerBarID", "PowerCostPct", "PowerCostMaxPct", "OptionalCostPct", "PowerPctPerSecond", "PowerType", "RequiredAuraSpellID", "OptionalCost", "SpellID"],
        justification=(
            "an authored power cost implies an activation; RequiredAuraSpellID additionally"
            "gates the cost row on another aura"
        ),
        evidence="source",
        core=[],
        trinity=["src/server/game/Spells/SpellInfo.cpp:4228-4232"],
        inert_columns=["OrderIndex"],
    ),
    "SpellReagents": SidecarRule(
        table="SpellReagents",
        key="SpellID",
        policy_class="G",
        inert=False,
        presence_blocks=False,
        blocking=["ReagentCount_0", "ReagentCount_1", "ReagentCount_2", "ReagentCount_3", "ReagentCount_4", "ReagentCount_5", "ReagentCount_6", "ReagentCount_7", "ReagentReCraftCount_0", "ReagentReCraftCount_1", "ReagentReCraftCount_2", "ReagentReCraftCount_3", "ReagentReCraftCount_4", "ReagentReCraftCount_5", "ReagentReCraftCount_6", "ReagentReCraftCount_7", "ReagentSource_0", "ReagentSource_1", "ReagentSource_2", "ReagentSource_3", "ReagentSource_4", "ReagentSource_5", "ReagentSource_6", "ReagentSource_7", "Reagent_0", "Reagent_1", "Reagent_2", "Reagent_3", "Reagent_4", "Reagent_5", "Reagent_6", "Reagent_7"],
        columns=["ID", "SpellID", "Reagent_0", "Reagent_1", "Reagent_2", "Reagent_3", "Reagent_4", "Reagent_5", "Reagent_6", "Reagent_7", "ReagentCount_0", "ReagentCount_1", "ReagentCount_2", "ReagentCount_3", "ReagentCount_4", "ReagentCount_5", "ReagentCount_6", "ReagentCount_7", "ReagentReCraftCount_0", "ReagentReCraftCount_1", "ReagentReCraftCount_2", "ReagentReCraftCount_3", "ReagentReCraftCount_4", "ReagentReCraftCount_5", "ReagentReCraftCount_6", "ReagentReCraftCount_7", "ReagentSource_0", "ReagentSource_1", "ReagentSource_2", "ReagentSource_3", "ReagentSource_4", "ReagentSource_5", "ReagentSource_6", "ReagentSource_7"],
        justification=(
            "reagents imply a consumed activation (crafting); never an immutable passive"
        ),
        evidence="source",
        core=[],
        trinity=["src/server/game/DataStores/DB2Stores.cpp:337"],
        inert_columns=[],
    ),
    "SpellReagentsCurrency": SidecarRule(
        table="SpellReagentsCurrency",
        key="SpellID",
        policy_class="G",
        inert=False,
        presence_blocks=False,
        blocking=["CurrencyCount", "CurrencyTypesID", "OrderSource", "OverrideRecraftCurrencyCount"],
        columns=["ID", "SpellID", "CurrencyTypesID", "CurrencyCount", "OverrideRecraftCurrencyCount", "OrderSource"],
        justification=(
            "a currency cost implies a consumed activation"
        ),
        evidence="source",
        core=[],
        trinity=["src/server/game/DataStores/DB2Stores.cpp:338"],
        inert_columns=[],
    ),
    "SpellReplacement": SidecarRule(
        table="SpellReplacement",
        key="SpellID",
        policy_class="C",
        inert=True,
        presence_blocks=False,
        blocking=[],
        columns=["ID", "ReplacementSpellID", "SpellID"],
        justification=(
            "spell-replacement reference; an inbound grant reference does not gate the trait-"
            "granted application"
        ),
        evidence="source",
        core=[],
        trinity=["no loader in the pinned Trinity tree (no DB2Storage declared in DataStores/DB2Stores.cpp); no Trinity consumer != no Retail behaviour"],
    ),
    "SpellScaling": SidecarRule(
        table="SpellScaling",
        key="SpellID",
        policy_class="B",
        inert=True,
        presence_blocks=False,
        blocking=[],
        columns=["ID", "SpellID", "MinScalingLevel", "MaxScalingLevel"],
        justification=(
            "MinScalingLevel/MaxScalingLevel only bound a scaled amount; Core's selected"
            "amounts come from TraitDefinitionEffectPoints curves and require ScalingClass == 0"
        ),
        evidence="core",
        core=["crates/combat/src/program/exact_source.rs:196-205"],
        trinity=["src/server/game/DataStores/DB2Stores.cpp:339"],
    ),
    "SpellShapeshift": SidecarRule(
        table="SpellShapeshift",
        key="SpellID",
        policy_class="D",
        inert=False,
        presence_blocks=True,
        blocking=[],
        columns=["ID", "SpellID", "StanceBarOrder", "ShapeshiftExclude_0", "ShapeshiftExclude_1", "ShapeshiftMask_0", "ShapeshiftMask_1"],
        justification=(
            "shapeshift form gates application; Core requires the row to be absent outright"
        ),
        evidence="core",
        core=["crates/combat/src/program/passive_spell_modifier.rs:1107-1109"],
        trinity=["src/server/game/Spells/SpellInfo.cpp:1498-1499", "src/server/game/Spells/SpellInfo.cpp:2111-2153 CheckShapeshift", "src/server/game/Entities/Player/Player.cpp:3083-3085 (passive re-cast on form change)"],
    ),
    "SpellTargetRestrictions": SidecarRule(
        table="SpellTargetRestrictions",
        key="SpellID",
        policy_class="H",
        inert=True,
        presence_blocks=False,
        blocking=[],
        columns=["ID", "DifficultyID", "ConeDegrees", "MaxTargets", "MaxTargetLevel", "TargetCreatureType", "Targets", "Width", "SpellID"],
        justification=(
            "cone/width/max-target/creature-type shaping constrains what an active cast"
            "reaches; a Passive owner with self-only implicit targets never runs a target"
            "selection"
        ),
        evidence="core",
        core=["crates/combat/src/program/exact_source.rs:178-231 (self delivery proved per effect)"],
        trinity=["src/server/game/Spells/SpellMgr.cpp:2615"],
    ),
    "SpellTotems": SidecarRule(
        table="SpellTotems",
        key="SpellID",
        policy_class="G",
        inert=False,
        presence_blocks=False,
        blocking=["RequiredTotemCategoryID_0", "RequiredTotemCategoryID_1", "Totem_0", "Totem_1"],
        columns=["ID", "SpellID", "RequiredTotemCategoryID_0", "RequiredTotemCategoryID_1", "Totem_0", "Totem_1"],
        justification=(
            "a required totem category is a tool requirement on an activation (professions use"
            "it for tools)"
        ),
        evidence="source",
        core=[],
        trinity=["src/server/game/Spells/SpellMgr.cpp:2618"],
        inert_columns=[],
    ),
    "SpellVisualKitDecalAttach": SidecarRule(
        table="SpellVisualKitDecalAttach",
        key="SpellID",
        policy_class="A",
        inert=True,
        presence_blocks=False,
        blocking=[],
        columns=["ID", "Field_12_0_0_64741_000_0", "Field_12_0_0_64741_000_1", "Field_12_0_0_64741_001_0", "Field_12_0_0_64741_001_1", "SpellVisualKitID", "DecalPropertiesID", "Field_12_0_0_64741_004", "Field_12_0_0_64741_005", "Radius", "Field_12_0_0_64741_007", "Field_12_0_0_64741_008", "Field_12_0_0_64741_009", "Priority", "FadeIn", "FadeOut", "ArbitraryBoxHeight", "Field_12_0_0_64741_014", "SpellID"],
        justification=(
            "decal attachment geometry"
        ),
        evidence="source",
        core=[],
        trinity=["no loader in the pinned Trinity tree (no DB2Storage declared in DataStores/DB2Stores.cpp); no Trinity consumer != no Retail behaviour"],
    ),
    "SpellXDescriptionVariables": SidecarRule(
        table="SpellXDescriptionVariables",
        key="SpellID",
        policy_class="A",
        inert=True,
        presence_blocks=False,
        blocking=[],
        columns=["ID", "SpellID", "SpellDescriptionVariablesID"],
        justification=(
            "tooltip description-variable binding"
        ),
        evidence="source",
        core=[],
        trinity=["no loader in the pinned Trinity tree (no DB2Storage declared in DataStores/DB2Stores.cpp); no Trinity consumer != no Retail behaviour"],
    ),
    "SpellXSpellVisual": SidecarRule(
        table="SpellXSpellVisual",
        key="SpellID",
        policy_class="A",
        inert=True,
        presence_blocks=False,
        blocking=[],
        columns=["ID", "DifficultyID", "SpellVisualID", "Probability", "Flags2", "Priority", "SpellIconFileID", "ActiveIconFileID", "ViewerUnitConditionID", "ViewerPlayerConditionID", "CasterUnitConditionID", "CasterPlayerConditionID", "SpellID"],
        justification=(
            "visual-kit selection per difficulty/condition"
        ),
        evidence="source",
        core=[],
        trinity=["src/server/game/DataStores/DB2Stores.cpp:348 (loaded); visual selection only"],
    ),
    "Talent": SidecarRule(
        table="Talent",
        key="SpellID",
        policy_class="C",
        inert=True,
        presence_blocks=False,
        blocking=[],
        columns=["ID", "Description_lang", "TierID", "Flags", "ColumnIndex", "TabID", "ClassID", "SpecID", "SpellID", "OverridesSpellID", "RequiredSpellID", "CategoryMask_0", "CategoryMask_1", "SpellRank_0", "SpellRank_1", "SpellRank_2", "SpellRank_3", "SpellRank_4", "SpellRank_5", "SpellRank_6", "SpellRank_7", "SpellRank_8", "PrereqTalent_0", "PrereqTalent_1", "PrereqTalent_2", "PrereqRank_0", "PrereqRank_1", "PrereqRank_2"],
        justification=(
            "legacy Talent.db2 grant; an inbound grant reference does not gate the trait-"
            "granted application"
        ),
        evidence="source",
        core=[],
        trinity=["src/server/game/DataStores/DB2Stores.cpp:351"],
    ),
    "TraitDefinition": SidecarRule(
        table="TraitDefinition",
        key="SpellID",
        policy_class="C",
        inert=True,
        presence_blocks=False,
        blocking=[],
        columns=["OverrideName_lang", "OverrideSubtext_lang", "OverrideDescription_lang", "ID", "SpellID", "OverrideIcon", "OverridesSpellID", "VisibleSpellID"],
        justification=(
            "the selected trait definition itself; already the provenance input; an inbound"
            "grant reference does not gate the trait-granted application"
        ),
        evidence="source",
        core=[],
        trinity=["src/server/game/Entities/Player/Player.cpp:29391-29410 ApplyTraitEntry -> LearnSpell, with no level or condition check"],
    ),
    "UIChromieTimeExpansionInfo": SidecarRule(
        table="UIChromieTimeExpansionInfo",
        key="SpellID",
        policy_class="A",
        inert=True,
        presence_blocks=False,
        blocking=[],
        columns=["ID", "Name_lang", "Description_lang", "AllianceOverrideDesc_lang", "HordeOverrideDesc_lang", "SpellID", "MapAtlasElement", "PreviewAtlasElement", "ShowPlayerConditionID", "ExpansionMask", "ContentTuningID", "CompletedPlayerConditionID", "SortPriority", "RecommendPlayerConditionID"],
        justification=(
            "Chromie-time expansion picker art"
        ),
        evidence="source",
        core=[],
        trinity=["no loader in the pinned Trinity tree (no DB2Storage declared in DataStores/DB2Stores.cpp); no Trinity consumer != no Retail behaviour"],
    ),
    "UICovenantAbility": SidecarRule(
        table="UICovenantAbility",
        key="SpellID",
        policy_class="C",
        inert=True,
        presence_blocks=False,
        blocking=[],
        columns=["ID", "CovenantPreviewID", "SpellID", "AbilityType", "SoulbindDisplayInfoID"],
        justification=(
            "covenant ability preview UI; an inbound grant reference does not gate the trait-"
            "granted application"
        ),
        evidence="source",
        core=[],
        trinity=["no loader in the pinned Trinity tree (no DB2Storage declared in DataStores/DB2Stores.cpp); no Trinity consumer != no Retail behaviour"],
    ),
    "UIDeadlyDebuff": SidecarRule(
        table="UIDeadlyDebuff",
        key="SpellID",
        policy_class="A",
        inert=True,
        presence_blocks=False,
        blocking=[],
        columns=["WarningText_lang", "ID", "SpellID", "OverrideCriticalTimeRemaining", "Priority", "PlayerConditionID", "SoundKitID", "Field_10_1_5_50199_007"],
        justification=(
            "deadly-debuff warning UI"
        ),
        evidence="source",
        core=[],
        trinity=["no loader in the pinned Trinity tree (no DB2Storage declared in DataStores/DB2Stores.cpp); no Trinity consumer != no Retail behaviour"],
    ),
    "UiPartyPose": SidecarRule(
        table="UiPartyPose",
        key="SpellID",
        policy_class="A",
        inert=True,
        presence_blocks=False,
        blocking=[],
        columns=["TitleText_lang", "ExtraButtonText_lang", "ID", "UiWidgetSetID", "VictoryUiModelSceneID", "DefeatUiModelSceneID", "VictorySoundKitID", "DefeatSoundKitID", "SpellID", "UiTextureKitID", "Flags", "MapID"],
        justification=(
            "end-of-instance party pose scene"
        ),
        evidence="source",
        core=[],
        trinity=["no loader in the pinned Trinity tree (no DB2Storage declared in DataStores/DB2Stores.cpp); no Trinity consumer != no Retail behaviour"],
    ),
}


class SidecarCensus:
    """The A-I owner-policy verdict for any provider spell."""

    def __init__(self, source: Source | None = None) -> None:
        self.source = source or Source()
        self._rows: dict[str, dict[int, list[dict[str, Any]]]] = {}
        self._verify_coverage()

    # -- drift guards -----------------------------------------------------
    @staticmethod
    def spell_keyed_tables() -> dict[str, str]:
        """Every table under ``data/tables`` keyed by a provider spell -> its key column.

        Found by header inspection, never from a hand-kept list: a table with a literal
        ``SpellID`` column, plus the two whose primary key *is* the spell id.
        """
        out: dict[str, str] = {}
        for path in sorted(TABLES.glob("*.csv")):
            with path.open(newline="", encoding="utf-8") as handle:
                try:
                    header = next(csv.reader(handle))
                except StopIteration:
                    continue
            if "SpellID" in header:
                out[path.stem] = "SpellID"
            elif path.stem in ("Spell", "SpellName"):
                out[path.stem] = "ID"
        return out

    def _verify_coverage(self) -> None:
        found = self.spell_keyed_tables()
        missing = sorted(set(found) - set(SIDECAR_POLICY))
        if missing:
            raise FailClosed(
                f"{len(missing)} spell-keyed tables have no SIDECAR_POLICY rule "
                f"(first: {missing[:5]}); the census is out of date"
            )
        stale = sorted(set(SIDECAR_POLICY) - set(found))
        if stale:
            raise FailClosed(f"SIDECAR_POLICY names absent tables {stale}")
        for table, key in found.items():
            if SIDECAR_POLICY[table].key != key:
                raise FailClosed(
                    f"{table}: key column is {key!r}, policy pins "
                    f"{SIDECAR_POLICY[table].key!r}"
                )

    # -- row access -------------------------------------------------------
    def rows(self, table: str) -> dict[int, list[dict[str, Any]]]:
        """``spell -> rows`` for one table, with the pinned header enforced."""
        cached = self._rows.get(table)
        if cached is not None:
            return cached
        rule = SIDECAR_POLICY.get(table)
        if rule is None:
            raise FailClosed(f"no SIDECAR_POLICY rule for {table}")
        path = self.source.path(table)
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.reader(handle)
            header = tuple(next(reader))
            if header != rule.columns:
                raise FailClosed(
                    f"{table}.csv header drifted from the pinned policy "
                    f"(added {sorted(set(header) - set(rule.columns))}, "
                    f"removed {sorted(set(rule.columns) - set(header))})"
                )
            index = header.index(rule.key)
            out: dict[int, list[dict[str, Any]]] = defaultdict(list)
            for row in reader:
                try:
                    spell = int(row[index])
                except (IndexError, ValueError):
                    continue
                out[spell].append(dict(zip(header, row)))
        self._rows[table] = dict(out)
        return self._rows[table]

    @cached_property
    def tables(self) -> tuple[str, ...]:
        return tuple(sorted(SIDECAR_POLICY))

    # -- the one sidecar whose inertness is a proof, not a field test -------
    @cached_property
    def _procs(self):
        """``SpellMgr::LoadSpellProcs`` ported, reusing the proc oracle.

        Track D's ``procpolicy`` is the authority for effective proc generation; this
        is the standalone fallback so :meth:`classify` is usable on its own.
        """
        from procs.definition import ProcEntryStore
        from procs.spells import SpellCatalog
        from procs.trinity import TrinityOverlay

        overlay = TrinityOverlay()
        catalog = SpellCatalog(self.source, overlay.custom_attributes)
        overlay.bind_ranks(catalog)
        return ProcEntryStore(catalog, overlay)

    def proc_generation_is_empty(self, spell: int) -> tuple[bool, str]:
        """Whether the owner can ever reach ``Aura::CheckProc``.

        Mirrors: ``SpellMgr::GetSpellProcEntry`` (SpellMgr.cpp:503) over the pinned
        world overlay plus ``LoadSpellProcs`` default generation.  ``Aura::CheckProc``
        is entered only when an entry exists (SpellAuras.cpp:1007), so no entry means
        the whole ``SpellAuraOptions`` proc half is dead.
        """
        try:
            entry = self._procs.lookup(spell)
        except Exception as error:                 # noqa: BLE001 - fail closed, never guess
            return False, f"proc oracle unavailable ({error})"
        if entry is None:
            reason = self._procs.generation(spell, 0).reason
            return True, f"Trinity generates no SpellProcEntry ({reason})"
        return False, "Trinity resolves a SpellProcEntry for this owner"

    # -- classification ---------------------------------------------------
    def classify(self, spell: int, *, proc_inert: bool | None = None) -> OwnerVerdict:
        """The owner verdict for one provider spell.

        ``blockers`` is empty exactly when every sidecar row the provider carries is
        provably inert for immutable passive preparation.

        ``proc_inert`` lets a caller that has already proved effective proc generation
        empty (Track D's ``procpolicy``) supply that proof.  ``None`` means "prove it
        here" via :meth:`proc_generation_is_empty`; ``False`` fails closed.
        """
        verdict = OwnerVerdict(spell=spell)
        for table in self.tables:
            rule = SIDECAR_POLICY[table]
            rows = self.rows(table).get(spell, [])
            if table == "SpellAuraOptions" and rows:
                self._classify_aura_options(verdict, rule, rows, proc_inert)
                continue
            if table == "SpellMisc":
                self._classify_misc(verdict, rule, rows)
                continue
            for row in rows:
                self._classify_row(verdict, rule, row)
        return verdict

    def _classify_aura_options(self, verdict: OwnerVerdict, rule: SidecarRule,
                               rows: list[dict[str, Any]], proc_inert: bool | None) -> None:
        """``SpellAuraOptions`` is inert exactly when no proc can ever be generated.

        Mirrors: ``SelectedAuraOptionsContract`` (selected_trait_package.rs:184-208).
        Core demands the row be absent or exactly pinned; this rule instead proves the
        row dead.  ``CumulativeAura`` is dormant for a learn-time passive, which is
        applied exactly once and never reapplied
        (passive_spell_modifier.rs:1112-1115).
        """
        if proc_inert is None:
            proc_inert, reason = self.proc_generation_is_empty(verdict.spell)
        else:
            reason = "caller-supplied proc-generation proof"

        for row in rows:
            if int(row.get("DifficultyID", 0) or 0) != 0:
                verdict.hits.append(SidecarHit(
                    rule.table, "I", False,
                    "difficulty-scoped aura-options row; the selected contract resolves one "
                    "canonical row only", _nonzero_of(rule, row)))
                verdict.blockers.append(
                    f"{rule.table} row at DifficultyID {row['DifficultyID']} (unknown/build skew)")
                continue
            nonzero = _nonzero_of(rule, row)
            if proc_inert:
                verdict.hits.append(SidecarHit(
                    rule.table, rule.policy_class, True,
                    f"proc policy is dead: {reason}; CumulativeAura is dormant for a "
                    "learn-time passive applied exactly once", nonzero))
            else:
                verdict.hits.append(SidecarHit(
                    rule.table, rule.policy_class, False, reason, nonzero))
                verdict.blockers.append(
                    f"{rule.table} live proc policy ({rule.policy}): {reason}")

    def _classify_misc(self, verdict: OwnerVerdict, rule: SidecarRule,
                       rows: list[dict[str, Any]]) -> None:
        """``SpellMisc`` carries both the lifetime fields and the owner attribute set.

        Mirrors: ``supports_unconditional_passive_application_policy``
        (passive_spell_modifier.rs:1099-1110) for the duration and missile fields, and
        ``validate_owner_attributes`` (passive_spell_modifier.rs:1223-1266) /
        ``neutral_physical_passive_owner_shell_issue`` (exact_source.rs:311-336) for the
        attribute set.
        """
        base = [row for row in rows if int(row.get("DifficultyID", 0) or 0) == 0]
        if not base:
            verdict.hits.append(SidecarHit(
                rule.table, "I", False, "no base-difficulty SpellMisc row", {}))
            verdict.blockers.append("SpellMisc has no base-difficulty row (unknown/build skew)")
            return
        if len(base) > 1:
            verdict.hits.append(SidecarHit(
                rule.table, "I", False,
                f"{len(base)} base-difficulty SpellMisc rows; the owner has no single shell", {}))
            verdict.blockers.append("SpellMisc has several base-difficulty rows (unknown/build skew)")
            return

        row = base[0]
        self._classify_row(verdict, rule, row)

        attributes = attribute_ids(row)
        if coreref.PASSIVE_ATTRIBUTE not in attributes:
            verdict.hits.append(SidecarHit(
                "SpellMisc.Attributes", "D", False,
                "an owner without the Passive attribute is an active spell: its application is "
                "an event, not a permanent preparation", {}))
            verdict.blockers.append("SpellMisc.Attributes lacks Passive raw 6 "
                                    "(conditional activation gate)")
        for attribute in attributes:
            if attribute == coreref.PASSIVE_ATTRIBUTE:
                continue
            support = coreref.attribute_support(attribute)
            if support == "ignored":
                verdict.hits.append(SidecarHit(
                    f"SpellMisc.Attributes[{attribute}]", "A", True,
                    "catalog-Ignored owner attribute", {}))
                continue
            override = PASSIVE_INERT_ATTRIBUTES.get(attribute)
            if override is not None and coreref.PASSIVE_ATTRIBUTE in attributes:
                policy_class, why, _consumer = override
                verdict.hits.append(SidecarHit(
                    f"SpellMisc.Attributes[{attribute}]", policy_class, True,
                    f"Core support {support!r}, proved inert for a Passive owner: {why}", {}))
                continue
            if support == "unknown":
                verdict.hits.append(SidecarHit(
                    f"SpellMisc.Attributes[{attribute}]", "I", False,
                    "absent from Core's SpellAttributeKind catalog", {}))
                verdict.blockers.append(
                    f"SpellMisc.Attributes[{attribute}] is uncatalogued (unknown/build skew)")
                continue
            verdict.hits.append(SidecarHit(
                f"SpellMisc.Attributes[{attribute}]", "I", False,
                f"Core support class {support!r}, not Ignored", {}))
            verdict.blockers.append(
                f"SpellMisc.Attributes[{attribute}] has support {support!r}, not Ignored")

    def _classify_row(self, verdict: OwnerVerdict, rule: SidecarRule,
                      row: dict[str, Any]) -> None:
        nonzero = _nonzero_of(rule, row)

        if rule.inert:
            verdict.hits.append(
                SidecarHit(rule.table, rule.policy_class, True, rule.justification, nonzero))
            return

        if rule.presence_blocks:
            verdict.hits.append(
                SidecarHit(rule.table, rule.policy_class, False, rule.justification, nonzero))
            verdict.blockers.append(f"{rule.table} row present ({rule.policy})")
            return

        hit = sorted(column for column in nonzero if column in rule.blocking)
        # Fail closed: a nonzero column of a non-inert table that the rule does not name
        # has no proved semantics, so it is build skew, not silence.
        unknown = sorted(
            column for column in nonzero
            if column not in rule.blocking and column not in rule.inert_columns
        )
        if hit:
            verdict.hits.append(
                SidecarHit(rule.table, rule.policy_class, False, rule.justification,
                           {c: nonzero[c] for c in hit}))
            verdict.blockers.append(f"{rule.table} {hit} ({rule.policy})")
        elif nonzero:
            verdict.hits.append(
                SidecarHit(rule.table, rule.policy_class, True,
                           f"row present, every consequential column zero: {rule.justification}",
                           nonzero))
        else:
            verdict.hits.append(
                SidecarHit(rule.table, rule.policy_class, True,
                           "row present but entirely zero", {}))
        if unknown:
            verdict.hits.append(
                SidecarHit(rule.table, "I", False,
                           "nonzero column classified neither blocking nor inert",
                           {c: nonzero[c] for c in unknown}))
            verdict.blockers.append(
                f"{rule.table} uncatalogued columns {unknown} (unknown/build skew)")


# ---------------------------------------------------------------------------
# corpora
# ---------------------------------------------------------------------------

#: Negative-witness classes, each with the blocker it stands for and the condition that
#: would reopen it.  Keyed exactly as ``docs/research/selected-package-corpora/
#: owner-negatives.json`` records them.
NEGATIVE_CLASSES: dict[str, tuple[str, str, str]] = {
    "caster_aura_state": (
        "D", "SpellAuraRestrictions.CasterAuraState gates application on a caster aura state",
        "an aura-state model in Core that can prove a given state permanently held"),
    "aura_restriction_any": (
        "D", "any SpellAuraRestrictions row; Core requires the row absent outright",
        "per-column proof that the present restriction is unreachable for a Passive owner"),
    "not_passive": (
        "D", "the provider lacks SpellMisc attribute raw 6 (Passive)",
        "a represented active-cast acquisition path for selected traits"),
    "finite_duration": (
        "E", "SpellMisc.DurationIndex != 0 makes the application finite",
        "a duration model that proves the authored index resolves to a permanent aura"),
    "shapeshift": (
        "D", "a SpellShapeshift row conditions application on a form",
        "a represented shapeshift state"),
    "equipped_items": (
        "D", "a SpellEquippedItems row conditions application on equipment",
        "a represented equipment-dependent aura lifecycle"),
    "aura_interrupt": (
        "E", "SpellInterrupts.AuraInterruptFlags make the application removable",
        "an interrupt model that proves the flagged events cannot occur"),
    "cooldown": (
        "G", "a SpellCooldowns row with nonzero recovery implies an activation",
        "a proof that recovery on a Passive owner is dormant"),
    "power_cost": (
        "G", "a SpellPower row with a nonzero cost implies an activation",
        "a proof that the cost row is unreachable for a Passive owner"),
    "reagents": (
        "G", "a SpellReagents row with a reagent implies a consumed activation",
        "never; a reagent is definitionally an activation"),
    "attribute_unsupported": (
        "I", "an owner attribute whose Core support class is disabled or unimplemented",
        "Core promoting that attribute to Implemented or Ignored, or a per-attribute "
        "inertness proof like PASSIVE_INERT_ATTRIBUTES"),
    "attribute_uncatalogued": (
        "I", "an owner attribute absent from Core's SpellAttributeKind catalog (build skew)",
        "Core's catalog catching up with this DB2 build"),
    "live_proc_entry": (
        "G", "Trinity resolves a SpellProcEntry for the owner, so its SpellAuraOptions "
             "proc policy is live",
        "Track D proving the generated entry can never fire for this owner"),
}


def census_payload(census: SidecarCensus, spells: list[int],
                   entries: list[tuple[int, int, int, int]]) -> dict[str, Any]:
    """The complete spell-keyed sidecar table census over one provider population.

    ``entries`` is ``(entry_id, definition_id, spell, max_ranks)`` for every resolvable
    selected entry, so the census can report provider *entries* as well as provider
    *spells*.
    """
    population = set(spells)
    per_spell_entries: dict[int, int] = defaultdict(int)
    per_spell_variants: dict[int, int] = defaultdict(int)
    for _entry, _definition, spell, max_ranks in entries:
        per_spell_entries[spell] += 1
        per_spell_variants[spell] += max_ranks

    tables = []
    for table in census.tables:
        rule = SIDECAR_POLICY[table]
        rows = census.rows(table)
        hit = sorted(spell for spell in rows if spell in population)
        shapes: dict[tuple[str, ...], dict[str, Any]] = {}
        for spell in hit:
            for row in rows[spell]:
                key = tuple(sorted(_nonzero_of(rule, row)))
                shape = shapes.setdefault(
                    key, {"nonzero_columns": list(key), "rows": 0, "sample_spell": spell})
                shape["rows"] += 1
        entry = rule.to_dict()
        entry.update({
            "rows_in_population": sum(len(rows[spell]) for spell in hit),
            "providers_with_row": len(hit),
            "provider_entries_with_row": sum(per_spell_entries[spell] for spell in hit),
            "provider_variants_with_row": sum(per_spell_variants[spell] for spell in hit),
            "distinct_nonzero_shapes": sorted(
                shapes.values(), key=lambda s: (-s["rows"], s["nonzero_columns"])),
        })
        tables.append(entry)

    by_class: dict[str, list[str]] = defaultdict(list)
    for table in census.tables:
        by_class[SIDECAR_POLICY[table].policy_class].append(table)

    return {
        "question": "which auxiliary DB2 row a selected provider can carry is provably "
                    "inert for immutable passive preparation",
        "population": {
            "provider_spells": len(population),
            "provider_entries": len(entries),
            "provider_variants": sum(max_ranks for *_rest, max_ranks in entries),
        },
        "spell_keyed_tables": len(SIDECAR_POLICY),
        "tables_used_by_population": sum(
            1 for table in tables if table["providers_with_row"]),
        "policy_classes": POLICY_CLASSES,
        "tables_by_class": {key: sorted(value) for key, value in sorted(by_class.items())},
        "tables": tables,
    }


def _witness(spell: int, name: str, entries: list[int], klass: str, source: dict[str, Any],
             detail: str) -> dict[str, Any]:
    policy_class, blocker, reopen = NEGATIVE_CLASSES[klass]
    return {
        "id": f"{klass}:{spell}",
        "spell": spell,
        "name": name,
        "entries": sorted(entries),
        "class": klass,
        "policy_class": policy_class,
        "policy": POLICY_CLASSES[policy_class],
        "evidence": "source",
        "source": source,
        "blocker": blocker,
        "detail": detail,
        "reopen": reopen,
    }


def negatives_payload(census: SidecarCensus, spells: list[int],
                      entries: list[tuple[int, int, int, int]],
                      names: dict[int, str], *, witnesses_per_class: int = 12,
                      ) -> dict[str, Any]:
    """Real selected providers for every negative owner class, counted and sampled.

    Every class is counted over the whole population; ``witnesses_per_class`` providers
    per class are recorded in full, lowest spell id first, so the corpus is byte-stable.
    """
    population = sorted(set(spells))
    entries_for: dict[int, list[int]] = defaultdict(list)
    variants_for: dict[int, int] = defaultdict(int)
    for entry_id, _definition, spell, max_ranks in entries:
        entries_for[spell].append(entry_id)
        variants_for[spell] += max_ranks

    found: dict[str, list[dict[str, Any]]] = defaultdict(list)

    def record(klass: str, spell: int, source: dict[str, Any], detail: str) -> None:
        found[klass].append(
            _witness(spell, names.get(spell, "?"), entries_for[spell], klass, source, detail))

    for spell in population:
        misc = [row for row in census.rows("SpellMisc").get(spell, [])
                if int(row.get("DifficultyID", 0) or 0) == 0]
        misc_row = misc[0] if misc else {}

        for row in census.rows("SpellAuraRestrictions").get(spell, []):
            nonzero = _nonzero_of(SIDECAR_POLICY["SpellAuraRestrictions"], row)
            record("aura_restriction_any", spell,
                   {"table": "SpellAuraRestrictions", "row_id": int(row["ID"]),
                    "nonzero": nonzero}, f"restriction fields {sorted(nonzero)}")
            if int(row.get("CasterAuraState", 0) or 0):
                record("caster_aura_state", spell,
                       {"table": "SpellAuraRestrictions", "row_id": int(row["ID"]),
                        "CasterAuraState": int(row["CasterAuraState"])},
                       f"CasterAuraState {row['CasterAuraState']}")

        attributes = attribute_ids(misc_row)
        if coreref.PASSIVE_ATTRIBUTE not in attributes:
            record("not_passive", spell,
                   {"table": "SpellMisc", "row_id": int(misc_row.get("ID", 0) or 0),
                    "attributes": attributes},
                   "no attribute raw 6 in the base-difficulty SpellMisc row")
        duration = int(misc_row.get("DurationIndex", 0) or 0)
        if duration:
            record("finite_duration", spell,
                   {"table": "SpellMisc", "row_id": int(misc_row["ID"]),
                    "DurationIndex": duration}, f"DurationIndex {duration}")

        for row in census.rows("SpellShapeshift").get(spell, []):
            nonzero = _nonzero_of(SIDECAR_POLICY["SpellShapeshift"], row)
            record("shapeshift", spell,
                   {"table": "SpellShapeshift", "row_id": int(row["ID"]), "nonzero": nonzero},
                   f"shapeshift fields {sorted(nonzero)}")
        for row in census.rows("SpellEquippedItems").get(spell, []):
            nonzero = _nonzero_of(SIDECAR_POLICY["SpellEquippedItems"], row)
            record("equipped_items", spell,
                   {"table": "SpellEquippedItems", "row_id": int(row["ID"]), "nonzero": nonzero},
                   f"equipment fields {sorted(nonzero)}")
        for row in census.rows("SpellInterrupts").get(spell, []):
            flags = {c: row[c] for c in ("AuraInterruptFlags_0", "AuraInterruptFlags_1")
                     if not _is_zero(row[c])}
            if flags:
                record("aura_interrupt", spell,
                       {"table": "SpellInterrupts", "row_id": int(row["ID"]), "nonzero": flags},
                       f"aura-interrupt flags {sorted(flags)}")
        for table, klass in (("SpellCooldowns", "cooldown"), ("SpellPower", "power_cost"),
                             ("SpellReagents", "reagents")):
            rule = SIDECAR_POLICY[table]
            for row in census.rows(table).get(spell, []):
                blocking = {c: row[c] for c in rule.blocking if not _is_zero(row.get(c))}
                if blocking:
                    record(klass, spell,
                           {"table": table, "row_id": int(row["ID"]), "nonzero": blocking},
                           f"{table} fields {sorted(blocking)}")

        unsupported, uncatalogued = [], []
        for attribute in attributes:
            if attribute == coreref.PASSIVE_ATTRIBUTE:
                continue
            support = coreref.attribute_support(attribute)
            if support == "ignored" or attribute in PASSIVE_INERT_ATTRIBUTES:
                continue
            (uncatalogued if support == "unknown" else unsupported).append(attribute)
        if unsupported:
            record("attribute_unsupported", spell,
                   {"table": "SpellMisc", "row_id": int(misc_row["ID"]),
                    "attributes": {str(a): coreref.attribute_support(a) for a in unsupported}},
                   f"attributes {unsupported}")
        if uncatalogued:
            record("attribute_uncatalogued", spell,
                   {"table": "SpellMisc", "row_id": int(misc_row["ID"]),
                    "attributes": uncatalogued}, f"attributes {uncatalogued}")

        if census.rows("SpellAuraOptions").get(spell):
            empty, reason = census.proc_generation_is_empty(spell)
            if not empty:
                record("live_proc_entry", spell,
                       {"table": "SpellAuraOptions",
                        "row_id": int(census.rows("SpellAuraOptions")[spell][0]["ID"])}, reason)

    classes = {}
    for klass, (policy_class, blocker, reopen) in NEGATIVE_CLASSES.items():
        hits = found.get(klass, [])
        carriers = sorted({hit["spell"] for hit in hits})
        classes[klass] = {
            "policy_class": policy_class,
            "policy": POLICY_CLASSES[policy_class],
            "blocker": blocker,
            "reopen": reopen,
            "providers": len(carriers),
            "provider_entries": sum(len(entries_for[spell]) for spell in carriers),
            "provider_variants": sum(variants_for[spell] for spell in carriers),
            "witnesses": sorted({hit["id"]: hit for hit in hits}.values(),
                                key=lambda hit: hit["spell"])[:witnesses_per_class],
        }

    return {
        "question": "which real selected providers witness each non-inert owner class",
        "population": {"provider_spells": len(population),
                       "provider_entries": len(entries),
                       "provider_variants": sum(m for *_r, m in entries)},
        "classes": classes,
    }
