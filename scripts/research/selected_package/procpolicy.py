"""AuraOptions -> effective proc generation, as a derived predicate.

Mirrors: ``SelectedAuraOptionsContract`` / ``SelectedTraitAuraOptions`` and the
``aura_options_match`` arm of ``selected_trait_owner_policy_issue``
(``core/crates/combat/src/program/selected_trait_package.rs:177-208, 270-300``).

Core distinguishes selected passive providers by an **exact literal AuraOptions
row**: ``Absent``, or ``Exact`` of six verbatim fields.  Two literals are
hardcoded -- Heart of the Crusader 406154
(selected_trait_package.rs:557-566) and Phalanx 1269312
(selected_trait_package.rs:672-681).  This module asks whether that literal can
be replaced by a semantic predicate:

    AuraOptions present + effective proc generation NONE  ->  inert
    effective proc / script / mutable trigger state       ->  reject

The predicate is derived, never asserted.  Every field of ``SpellAuraOptions``
is traced to the TrinityCore consumer that reads it, and a field is called inert
only when *every* consumer of it is unreachable for this provider:

====================== ============================================== ==============================================
SpellAuraOptions field SpellInfo member (SpellInfo.cpp:1384-1396)      every consumer
====================== ============================================== ==============================================
``ProcTypeMask``       ``ProcFlags``                                  ``LoadSpellProcs`` generation (SpellMgr.cpp:1765, 1884),
                                                                      crowd-control amount override (SpellAuraEffects.cpp:795)
``ProcChance``         ``ProcChance``                                 ``SpellProcEntry::Chance`` only (SpellMgr.cpp:1575, 1884)
``ProcCategoryRecovery`` ``ProcCooldown``                             ``SpellProcEntry::Cooldown`` only (SpellMgr.cpp:1581, 1885)
``ProcCharges``        ``ProcCharges``                                ``SpellProcEntry::Charges`` (SpellMgr.cpp:1577, 1886)
                                                                      **and** ``Aura::CalcMaxCharges`` (SpellAuras.cpp:1004-1015),
                                                                      which reads it with **no** proc entry
``SpellProcsPerMinuteID`` ``ProcBasePPM`` / ``ProcPPMMods``           ``Aura::CalcProcChance`` (proc path only)
``CumulativeAura``     ``StackAmount``                                ``Aura::CalcMaxStackAmount`` (SpellAuras.cpp:1083-1090),
                                                                      ``Aura::ModStackAmount`` clamp (SpellAuras.cpp:1093-1117),
                                                                      ``Aura::IsUsingStacks`` (SpellAuras.cpp:1078-1081),
                                                                      ``SpellInfo::IsStackableOnOneSlotWithDifferentCasters``
                                                                      (SpellInfo.cpp:1813-1815)
====================== ============================================== ==============================================

Two independent lines fall out of that table, and every blocker this module
records belongs to exactly one of them.  Each blocker string is tagged with the
line it comes from.

``[trinity]`` -- **runtime reachability.**  Without a ``SpellProcEntry`` the aura
never enters ``Unit::ProcSkillsAndAuras``, so ``ProcChance``,
``ProcCategoryRecovery`` and ``SpellProcsPerMinuteID`` reach no consumer at all.
The two exceptions are ``ProcCharges``, which ``Aura::CalcMaxCharges``
(SpellAuras.cpp:1004-1015) reads at aura construction with or without an entry,
and ``ProcChance``, which ``Player::CastItemCombatSpell`` (Player.cpp:8646-8683)
reads for a spell an ``ItemEffect`` row binds with
``ITEM_SPELLTRIGGER_ON_PROC``.  Both exceptions are tested from source, not
assumed away.

``[authored]`` -- **does the field imply per-application state?**  Trinity's
non-implementation of a proc is a fact about Trinity's tables, not about what
Blizzard authored, so "no entry" alone is a weak inertness proof.  The strong
one asks whether the authored field needs a counter or a clock to mean anything:

    ProcCharges           a counter          -> state, reject
    ProcCategoryRecovery  a clock (ICD)      -> state, reject
    SpellProcsPerMinuteID a clock (RPPM)     -> state, reject
    ProcTypeMask          an event selector  -> no state, uninterpreted
    ProcChance            a probability      -> no state, uninterpreted
    CumulativeAura        a cap              -> no state for a passive (see below)

That line, and not the presence of an entry, is what separates Heart of the
Crusader and Phalanx (both admitted) from near-identical rows such as Deeper
Daggers 382517 (``ProcCategoryRecovery`` 500) and Thousand Cuts 441346
(``SpellProcsPerMinuteID`` 195, and otherwise the *same* ``ProcTypeMask`` 4 as
Heart).  It is also exactly what Core's own field documentation already says:
"Uninterpreted cumulative-aura field", "Authored proc-chance field", "Open
real-procs-per-minute row identity".

``[unknown]`` -- a fact the pinned source cannot settle.

Fail closed: an unresolvable fact is a blocker, never a pass.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from functools import cached_property
from typing import Any

from procs.definition import ProcDefinitions, ProcEntry
from procs.enums import ALWAYS_TRIGGERED_AURAS, TRIGGER_AURAS, aura_name
from procs.source import Source
from procs.spells import DIFFICULTY_NONE, SpellCatalog
from procs.trinity import TrinityOverlay

from . import coreref

#: ``PROC_FLAG_*`` bit -> name (SpellMgr.h:92-145) and ``PROC_FLAG_2_*`` at bit 32+
#: (SpellMgr.h:189-196).  Used only to *name* a mask, never to decide policy.
PROC_FLAG_NAMES = {
    0: "PROC_FLAG_HEARTBEAT", 1: "PROC_FLAG_KILL",
    2: "PROC_FLAG_DEAL_MELEE_SWING", 3: "PROC_FLAG_TAKE_MELEE_SWING",
    4: "PROC_FLAG_DEAL_MELEE_ABILITY", 5: "PROC_FLAG_TAKE_MELEE_ABILITY",
    6: "PROC_FLAG_DEAL_RANGED_ATTACK", 7: "PROC_FLAG_TAKE_RANGED_ATTACK",
    8: "PROC_FLAG_DEAL_RANGED_ABILITY", 9: "PROC_FLAG_TAKE_RANGED_ABILITY",
    10: "PROC_FLAG_DEAL_HELPFUL_ABILITY", 11: "PROC_FLAG_TAKE_HELPFUL_ABILITY",
    12: "PROC_FLAG_DEAL_HARMFUL_ABILITY", 13: "PROC_FLAG_TAKE_HARMFUL_ABILITY",
    14: "PROC_FLAG_DEAL_HELPFUL_SPELL", 15: "PROC_FLAG_TAKE_HELPFUL_SPELL",
    16: "PROC_FLAG_DEAL_HARMFUL_SPELL", 17: "PROC_FLAG_TAKE_HARMFUL_SPELL",
    18: "PROC_FLAG_DEAL_HARMFUL_PERIODIC", 19: "PROC_FLAG_TAKE_HARMFUL_PERIODIC",
    20: "PROC_FLAG_TAKE_ANY_DAMAGE", 21: "PROC_FLAG_DEAL_HELPFUL_PERIODIC",
    22: "PROC_FLAG_MAIN_HAND_WEAPON_SWING", 23: "PROC_FLAG_OFF_HAND_WEAPON_SWING",
    24: "PROC_FLAG_DEATH", 25: "PROC_FLAG_JUMP", 26: "PROC_FLAG_PROC_CLONE_SPELL",
    27: "PROC_FLAG_ENTER_COMBAT", 28: "PROC_FLAG_ENCOUNTER_START",
    29: "PROC_FLAG_CAST_ENDED", 30: "PROC_FLAG_LOOTED",
    31: "PROC_FLAG_TAKE_HELPFUL_PERIODIC",
    32: "PROC_FLAG_2_TARGET_DIES", 33: "PROC_FLAG_2_KNOCKBACK",
    34: "PROC_FLAG_2_CAST_SUCCESSFUL", 36: "PROC_FLAG_2_SUCCESSFUL_DISPEL",
    38: "PROC_FLAG_2_DO_EMOTE",
}

#: Aura subtypes that override the *initial* stack count of a freshly created aura.
#: ``SpellModOp::Doses`` (SpellDefines.h:185) is applied to ``SpellValue::AuraStackAmount``
#: in ``SpellValue::SpellValue`` (Spell.cpp:506); ``SpellModOp::MaxAuraStacks``
#: (SpellDefines.h:191) raises ``Aura::CalcMaxStackAmount`` (SpellAuras.cpp:1083-1090).
SPELL_MOD_OP_DOSES = 31
SPELL_MOD_OP_MAX_AURA_STACKS = 37
SPELL_MOD_AURAS = (107, 108, 218, 219)

#: The six aura subtypes whose ``AuraEffect::CalculateAmount`` reads
#: ``SpellInfo::ProcFlags`` outside the proc pipeline (SpellAuraEffects.cpp:786-798):
#: MOD_CONFUSE 5, MOD_FEAR 7, MOD_STUN 12, MOD_ROOT 26, TRANSFORM 56, MOD_ROOT_2 455.
CROWD_CONTROL_AMOUNT_AURAS = frozenset({5, 7, 12, 26, 56, 455})

#: ``SpellAttributeKind::Passive`` -- re-exported so a caller need not import coreref.
PASSIVE_ATTRIBUTE = coreref.PASSIVE_ATTRIBUTE

AURA_OPTIONS_COLUMNS = ("ID", "SpellID", "DifficultyID", "CumulativeAura",
                        "ProcCategoryRecovery", "ProcChance", "ProcCharges",
                        "SpellProcsPerMinuteID", "ProcTypeMask_0", "ProcTypeMask_1")
PPM_COLUMNS = ("ID", "BaseProcRate", "Flags")
ITEM_EFFECT_COLUMNS = ("ID", "TriggerType", "SpellID")
#: ``ITEM_SPELLTRIGGER_ON_PROC`` -- the only ``TriggerType`` that reaches
#: ``Player::CastItemCombatSpell`` (Player.cpp:8654-8656), the sole consumer of
#: ``SpellInfo::ProcChance`` outside ``SpellProcEntry``.
ITEM_SPELLTRIGGER_ON_PROC = 2
MISC_ATTRIBUTE_COLUMNS = ("ID", "SpellID", "DifficultyID") + tuple(
    f"Attributes_{i}" for i in range(17))

#: Closed family set.  ``classify`` assigns exactly one, first match wins in this order.
FAMILIES: dict[str, str] = {
    "unknown": (
        "a fact the oracle cannot resolve from the pinned source: more than one "
        "base-difficulty SpellAuraOptions row, no SpellInfo, an aura subtype Core's "
        "own catalog does not name, a SpellProcsPerMinuteID with no "
        "SpellProcsPerMinute row, or a spell_script_names binding whose script body "
        "the script index could not resolve"
    ),
    "effective-proc": (
        "SpellMgr::GetSpellProcEntry returns an entry for this provider -- either a "
        "world-DB spell_proc row (SpellMgr.cpp:1497-1658) or a generated default "
        "(SpellMgr.cpp:1755-1907).  The aura takes part in the proc pipeline and owns "
        "a proc lifecycle"
    ),
    "guarded-proc": (
        "no entry, but only because ``SPELL_ATTR3_CAN_PROC_FROM_PROCS`` with no "
        "restriction and no cooldown tripped the infinite-loop guard "
        "(SpellMgr.cpp:1888-1901).  The provider declares proc flags AND a triggering "
        "aura subtype: it is a proc provider whose data Trinity refuses to synthesise, "
        "not an inert row"
    ),
    "script-driven": (
        "no effective proc entry, but at least one spell_script_names binding exists.  "
        "An AuraScript may create per-application state with no DBC evidence at all, "
        "so the DBC-only view cannot prove inertness"
    ),
    "rppm": (
        "no effective proc entry and no script, but SpellProcsPerMinuteID != 0, so the "
        "provider declares a real-procs-per-minute identity (SpellInfo.cpp:1391-1395)"
    ),
    "proc-icd": (
        "no effective proc entry, script, RPPM identity or charges, but "
        "ProcCategoryRecovery != 0.  With no entry the field reaches no consumer "
        "(SpellMgr.cpp:1581, 1885 are its only readers), yet an authored internal "
        "cooldown is a clock and a clock is per-application state, so the row is not "
        "interpreted as inert"
    ),
    "charges": (
        "no effective proc entry, script or RPPM identity, but ProcCharges != 0.  "
        "Aura::CalcMaxCharges (SpellAuras.cpp:1004-1015) reads SpellInfo::ProcCharges "
        "whether or not a proc entry exists, and Aura::Aura sets "
        "m_isUsingCharges = m_procCharges != 0 (SpellAuras.cpp:498-499): the "
        "application is born with mutable, consumable charge state"
    ),
    "stack-capacity-only": (
        "no effective proc entry, script, RPPM identity or charges; ProcTypeMask == 0 "
        "so LoadSpellProcs bails at SpellMgr.cpp:1765 before reading any other field; "
        "CumulativeAura > 1 declares a maximum stack capacity.  For a passive provider "
        "this is a cap only -- initial stacks are AuraCreateInfo::StackAmount, default 1 "
        "(SpellAuras.h:134, SpellAuras.cpp:481) -- so the row is inert"
    ),
    "inert-no-proc-flags": (
        "AuraOptions present with ProcTypeMask == 0 and no charges, RPPM, script or "
        "stack capacity above 1.  LoadSpellProcs bails at SpellMgr.cpp:1765 and every "
        "other field of the row reaches only SpellProcEntry, which is never built"
    ),
    "inert-no-trigger-aura": (
        "AuraOptions present with ProcTypeMask != 0, but none of the provider's aura "
        "subtypes is in isTriggerAura[] (SpellMgr.cpp:1679-1727), so generation bails "
        "at SpellMgr.cpp:1807-1817 with a sql.sql error and no entry; no charges, RPPM "
        "or script binding either"
    ),
    "no-auraoptions": (
        "the provider carries no SpellAuraOptions row at all; SpellInfo::ProcFlags, "
        "ProcChance, ProcCharges, ProcCooldown and StackAmount all keep their zero "
        "defaults (SpellInfo.h:380-396).  This is Core's SelectedAuraOptionsContract::Absent"
    ),
}

#: Whether a family may be admitted for immutable passive preparation, and why.
FAMILY_ADMITS: dict[str, tuple[bool, str]] = {
    "unknown": (False, "unknown"),
    "effective-proc": (False, "trinity"),
    "guarded-proc": (False, "trinity"),
    "script-driven": (False, "unknown"),
    "rppm": (False, "source"),
    "proc-icd": (False, "source"),
    "charges": (False, "trinity"),
    "stack-capacity-only": (True, "trinity"),
    "inert-no-proc-flags": (True, "trinity"),
    "inert-no-trigger-aura": (True, "trinity"),
    "no-auraoptions": (True, "source"),
}


def proc_flag_names(mask: int) -> list[str]:
    """Names of the set bits of a 64-bit ``ProcTypeMask``; unknown bits are named."""
    out = []
    for bit in range(64):
        if mask & (1 << bit):
            out.append(PROC_FLAG_NAMES.get(bit, f"UNKNOWN_PROC_FLAG_BIT_{bit}"))
    return out


@dataclass(frozen=True)
class AuraOptionsRow:
    """One ``SpellAuraOptions`` row, in Core's ``SelectedTraitAuraOptions`` spelling."""

    row_id: int
    cumulative_aura: int
    proc_category_recovery_ms: int
    proc_chance: int
    proc_charges: int
    procs_per_minute_id: int
    proc_type_mask: int

    def matches_core_literal(self, literal: dict[str, int]) -> bool:
        """The six-field comparison of ``selected_trait_owner_policy_issue`` (:281-292)."""
        return all(getattr(self, key) == value for key, value in literal.items())

    def to_dict(self) -> dict[str, Any]:
        out = dict(self.__dict__)
        out["proc_type_mask_hex"] = f"0x{self.proc_type_mask:016X}"
        out["proc_flag_names"] = proc_flag_names(self.proc_type_mask)
        return out


#: Core's two hardcoded literals, transcribed for differential testing.
CORE_LITERALS: dict[int, dict[str, int]] = {
    # selected_trait_package.rs:557-566
    406154: {"cumulative_aura": 0, "proc_category_recovery_ms": 0, "proc_chance": 101,
             "proc_charges": 0, "procs_per_minute_id": 0, "proc_type_mask": 4},
    # selected_trait_package.rs:672-681
    1269312: {"cumulative_aura": 2, "proc_category_recovery_ms": 0, "proc_chance": 101,
              "proc_charges": 0, "procs_per_minute_id": 0, "proc_type_mask": 0},
}


@dataclass(frozen=True)
class ProcVerdict:
    """Everything the AuraOptions row of one provider can decide, as plain data."""

    spell: int
    name: str
    family: str
    blockers: tuple[str, ...]

    # -- the row itself
    aura_options: AuraOptionsRow | None
    aura_options_row_count: int

    # -- effective proc generation
    has_effective_entry: bool
    entry_origin: str | None            # "spell_proc" | "generated" | None
    generation_reason: str              # ProcEntryStore.generation(...).reason
    effective_reason: str               # why an entry does / does not exist, in words

    # -- the provider's aura subtypes
    aura_subtypes: tuple[int, ...]
    proc_triggering_subtypes: tuple[int, ...]
    always_triggered_subtypes: tuple[int, ...]
    uncatalogued_subtypes: tuple[int, ...]

    # -- world-DB overlay
    has_spell_proc_overlay: bool
    spell_proc_overlay: dict[str, Any] | None
    script_names: tuple[str, ...]
    unresolved_script_names: tuple[str, ...]
    script_proc_hooks: tuple[str, ...]

    # -- RPPM
    procs_per_minute_resolves: bool | None   # None when the id is 0
    procs_per_minute_base_rate: float | None

    # -- mutable per-application state this row would create
    mutable_state: tuple[str, ...]

    # -- authored proc intent the predicate deliberately does not interpret
    authored_proc_signals: tuple[str, ...]

    # -- owner facts the stack argument depends on
    is_passive: bool | None

    @property
    def inert(self) -> bool:
        """``True`` exactly when the row is provably inert for immutable passive prep."""
        return not self.blockers

    @property
    def runtime_inert(self) -> bool:
        """The weaker ``[trinity]``-only reading: nothing this row says reaches a consumer.

        Kept separate from :attr:`inert` because "TrinityCore generates no proc entry"
        is a fact about *Trinity's* tables.  A row can be runtime-inert on this server
        and still be an authored proc (Thousand Cuts 441346).
        """
        return not [b for b in self.blockers if b.startswith("[trinity]")]

    def blockers_by_class(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {"trinity": [], "authored": [], "unknown": []}
        for blocker in self.blockers:
            tag, _, rest = blocker.partition("] ")
            out.setdefault(tag.lstrip("["), []).append(rest or blocker)
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "spell": self.spell,
            "name": self.name,
            "family": self.family,
            "inert": self.inert,
            "blockers": list(self.blockers),
            "blockers_by_class": self.blockers_by_class(),
            "runtime_inert": self.runtime_inert,
            "authored_proc_signals": list(self.authored_proc_signals),
            "aura_options": self.aura_options.to_dict() if self.aura_options else None,
            "aura_options_row_count": self.aura_options_row_count,
            "has_effective_entry": self.has_effective_entry,
            "entry_origin": self.entry_origin,
            "generation_reason": self.generation_reason,
            "effective_reason": self.effective_reason,
            "aura_subtypes": list(self.aura_subtypes),
            "aura_subtype_names": [aura_name(a) for a in self.aura_subtypes],
            "proc_triggering_subtypes": list(self.proc_triggering_subtypes),
            "always_triggered_subtypes": list(self.always_triggered_subtypes),
            "uncatalogued_subtypes": list(self.uncatalogued_subtypes),
            "has_spell_proc_overlay": self.has_spell_proc_overlay,
            "spell_proc_overlay": self.spell_proc_overlay,
            "script_names": list(self.script_names),
            "unresolved_script_names": list(self.unresolved_script_names),
            "script_proc_hooks": list(self.script_proc_hooks),
            "procs_per_minute_resolves": self.procs_per_minute_resolves,
            "procs_per_minute_base_rate": self.procs_per_minute_base_rate,
            "mutable_state": list(self.mutable_state),
            "is_passive": self.is_passive,
        }


class ProcPolicy:
    """Effective-proc classification of one provider spell.

    ``source`` is a :class:`procs.source.Source`; ``bundle`` is a
    :class:`dummy_semantics.loaders.Bundle` whose already-loaded catalog and
    overlay are reused instead of re-reading the snapshot.
    """

    def __init__(self, source: Source | None = None, bundle: Any | None = None) -> None:
        if bundle is not None:
            self.source = bundle.source
            self.overlay: TrinityOverlay | None = bundle.proc_overlay
            self.catalog: SpellCatalog = bundle.catalog
        else:
            self.source = source or Source()
            self.overlay = TrinityOverlay()
            self.catalog = SpellCatalog(self.source, self.overlay.custom_attributes)
        self.definitions = ProcDefinitions(self.catalog, self.overlay)
        self.store = self.definitions.store
        self._cache: dict[int, ProcVerdict] = {}

    # -- projected source -------------------------------------------------
    @cached_property
    def aura_options(self) -> dict[int, list[AuraOptionsRow]]:
        """Base-difficulty ``SpellAuraOptions`` rows, by SpellID.

        A provider with more than one row is *not* reduced to the first: Core's
        ``GameData::spell_aura_options`` returns a single row, so two rows mean the
        oracle and Core cannot be compared.  :meth:`classify` fails closed on it.
        """
        out: dict[int, list[AuraOptionsRow]] = defaultdict(list)
        for row in self.source.project("SpellAuraOptions", AURA_OPTIONS_COLUMNS):
            record = dict(zip(AURA_OPTIONS_COLUMNS, row))
            if int(record["DifficultyID"]) != 0:
                continue
            mask = ((int(record["ProcTypeMask_0"]) & 0xFFFFFFFF)
                    | ((int(record["ProcTypeMask_1"]) & 0xFFFFFFFF) << 32))
            out[int(record["SpellID"])].append(AuraOptionsRow(
                row_id=int(record["ID"]),
                cumulative_aura=int(record["CumulativeAura"]),
                proc_category_recovery_ms=int(record["ProcCategoryRecovery"]),
                proc_chance=int(record["ProcChance"]),
                proc_charges=int(record["ProcCharges"]),
                procs_per_minute_id=int(record["SpellProcsPerMinuteID"]),
                proc_type_mask=mask,
            ))
        return dict(out)

    @cached_property
    def ppm_rows(self) -> dict[int, dict[str, Any]]:
        """``SpellProcsPerMinute`` by ID -- an id that is absent is a fail-closed unknown."""
        return {int(r[0]): dict(zip(PPM_COLUMNS, r))
                for r in self.source.project("SpellProcsPerMinute", PPM_COLUMNS)}

    @cached_property
    def item_proc_spells(self) -> set[int]:
        """Spells an ``ItemEffect`` row binds with ``ITEM_SPELLTRIGGER_ON_PROC``.

        Mirrors: ``Player::CastItemCombatSpell`` (Player.cpp:8654-8683).  For such a
        spell ``SpellInfo::ProcChance`` is rolled directly, with no ``SpellProcEntry``,
        so "no entry" would not prove ProcChance inert.
        """
        return {int(r[2]) for r in self.source.project("ItemEffect", ITEM_EFFECT_COLUMNS)
                if int(r[1]) == ITEM_SPELLTRIGGER_ON_PROC}

    @cached_property
    def passive_spells(self) -> set[int]:
        """Spells carrying ``SpellAttributeKind::Passive`` (raw 6 == word 0 bit 6)."""
        out = set()
        for row in self.source.project("SpellMisc", ("ID", "SpellID", "DifficultyID",
                                                     "Attributes_0")):
            record = dict(zip(("ID", "SpellID", "DifficultyID", "Attributes_0"), row))
            if int(record["DifficultyID"]) != 0:
                continue
            if (int(record["Attributes_0"]) & 0xFFFFFFFF) & (1 << PASSIVE_ATTRIBUTE):
                out.add(int(record["SpellID"]))
        return out

    @cached_property
    def stack_mod_providers(self) -> dict[int, list[dict[str, Any]]]:
        """Every effect that carries a ``Doses`` or ``MaxAuraStacks`` spell modifier.

        Mirrors: ``SpellValue::SpellValue`` (Spell.cpp:506) and
        ``Aura::CalcMaxStackAmount`` (SpellAuras.cpp:1083-1090).  These are the only
        two ways a *max* or *initial* stack count moves without a second application,
        so the population of such modifiers bounds the stack-capacity argument.
        """
        columns = ("SpellID", "DifficultyID", "EffectIndex", "Effect", "EffectAura",
                   "EffectMiscValue_0", "EffectSpellClassMask_0", "EffectSpellClassMask_1",
                   "EffectSpellClassMask_2", "EffectSpellClassMask_3")
        out: dict[int, list[dict[str, Any]]] = {SPELL_MOD_OP_DOSES: [],
                                                SPELL_MOD_OP_MAX_AURA_STACKS: []}
        for row in self.source.project("SpellEffect", columns):
            record = dict(zip(columns, row))
            if int(record["EffectAura"]) not in SPELL_MOD_AURAS:
                continue
            op = int(record["EffectMiscValue_0"])
            if op in out:
                out[op].append(record)
        return out

    # -- classification ---------------------------------------------------
    def classify(self, spell: int) -> ProcVerdict:
        """Mirrors: the ``aura_options_match`` arm of ``selected_trait_owner_policy_issue``."""
        cached = self._cache.get(spell)
        if cached is not None:
            return cached
        verdict = self._classify(spell)
        self._cache[spell] = verdict
        return verdict

    def _classify(self, spell: int) -> ProcVerdict:
        blockers: list[str] = []
        mutable: list[str] = []
        authored: list[str] = []
        fail_closed = False

        rows = self.aura_options.get(spell, [])
        row = rows[0] if rows else None
        if len(rows) > 1:
            fail_closed = True
            blockers.append(
                f"[unknown] {len(rows)} base-difficulty SpellAuraOptions rows; Core reads one")

        info = self.catalog.get(spell, DIFFICULTY_NONE)
        if info is None:
            blockers.append("[unknown] provider has no base-difficulty SpellInfo")
            verdict = ProcVerdict(
                spell=spell, name="", family="unknown", blockers=tuple(sorted(blockers)),
                aura_options=row, aura_options_row_count=len(rows),
                has_effective_entry=False, entry_origin=None,
                generation_reason="no-spellinfo", effective_reason="no SpellInfo to generate from",
                aura_subtypes=(), proc_triggering_subtypes=(), always_triggered_subtypes=(),
                uncatalogued_subtypes=(), has_spell_proc_overlay=spell in (
                    self.overlay.spell_proc if self.overlay else {}),
                spell_proc_overlay=None, script_names=(), unresolved_script_names=(),
                script_proc_hooks=(), procs_per_minute_resolves=None,
                procs_per_minute_base_rate=None, mutable_state=(),
                authored_proc_signals=(), is_passive=None)
            return verdict

        subtypes = tuple(sorted({e.aura for e in info.effects if e.is_aura and e.aura}))
        triggering = tuple(a for a in subtypes if a in TRIGGER_AURAS)
        always = tuple(a for a in subtypes if a in ALWAYS_TRIGGERED_AURAS)
        uncatalogued = tuple(a for a in subtypes if coreref.aura_support(a) == "unknown")
        if uncatalogued:
            fail_closed = True
            blockers.append(
                "[unknown] provider carries aura subtype(s) "
                f"{list(uncatalogued)} absent from Core's AuraSubtypeKind catalog")

        is_passive = spell in self.passive_spells

        # -- effective entry: SpellMgr::GetSpellProcEntry
        entry: ProcEntry | None = self.store.lookup(spell, info.difficulty)
        generation = self.store.generation(spell, info.difficulty)
        has_entry = entry is not None
        origin = entry.origin if entry else None

        overlay_row = None
        if self.overlay is not None and spell in self.overlay.spell_proc:
            overlay_row = self.overlay.spell_proc[spell].to_dict()
        has_overlay = overlay_row is not None

        if has_entry:
            if origin == "spell_proc":
                effective_reason = (
                    "a world-DB spell_proc row supplies the entry "
                    "(SpellMgr::LoadSpellProcs, SpellMgr.cpp:1497-1658)")
            else:
                effective_reason = (
                    f"default generation produced an entry ({generation.reason}); at least "
                    f"one aura subtype {list(triggering)} is in isTriggerAura[] "
                    "(SpellMgr.cpp:1679-1727) and ProcTypeMask != 0")
            blockers.append(f"[trinity] effective proc entry exists (origin {origin})")
            mutable.append("Aura::m_procCooldown (AddProcCooldown, SpellAuras.cpp:1777-1786)")
            mutable.append("Aura::m_lastProcAttemptTime / m_lastProcSuccessTime")
            if entry.charges:
                mutable.append("Aura::m_procCharges (SpellProcEntry::Charges)")
        elif generation.reason == "no-proc-flags":
            effective_reason = (
                "SpellInfo::ProcFlags == 0, so LoadSpellProcs bails before reading any "
                "other AuraOptions field (SpellMgr.cpp:1765-1766)")
        elif generation.reason == "no-trigger-aura":
            effective_reason = (
                "ProcTypeMask != 0 but none of the provider's aura subtypes "
                f"{list(subtypes)} is in isTriggerAura[] (SpellMgr.cpp:1679-1727), so "
                "generation bails at SpellMgr.cpp:1807-1817 and logs a sql.sql error")
        elif generation.reason == "infinite-loop-guard":
            effective_reason = (
                "generation refused by the SPELL_ATTR3_CAN_PROC_FROM_PROCS infinite-loop "
                "guard (SpellMgr.cpp:1888-1901); a spell_proc row would restore it")
            blockers.append(
                "[trinity] generation suppressed by the infinite-loop guard, not by "
                "inertness: the provider has both proc flags and a triggering aura")
            mutable.append("would own a proc lifecycle once a spell_proc row exists")
        else:
            effective_reason = f"no entry: {generation.reason}"
            fail_closed = True
            blockers.append(f"[unknown] unrecognised generation outcome {generation.reason!r}")

        # -- world-DB script bindings
        hooks = self.overlay.script_hooks(spell) if self.overlay else None
        script_names = tuple(hooks["script_names"]) if hooks else ()
        unresolved = tuple(hooks["unresolved_script_names"]) if hooks else ()
        proc_hooks = tuple(hooks["proc_hooks"]) if hooks else ()
        if script_names:
            blockers.append(
                f"[unknown] spell_script_names binding(s) {list(script_names)}; an "
                "AuraScript can create per-application state with no DBC evidence")
            mutable.append("arbitrary AuraScript state")
        if has_overlay and not has_entry:
            fail_closed = True
            blockers.append(
                "[unknown] a world-DB spell_proc row exists but produced no entry; the "
                "DBC-only view cannot account for it")

        # -- fields of the row that reach a consumer without an entry
        ppm_resolves: bool | None = None
        ppm_rate: float | None = None
        if row is not None:
            if row.procs_per_minute_id:
                ppm_row = self.ppm_rows.get(row.procs_per_minute_id)
                ppm_resolves = ppm_row is not None
                ppm_rate = float(ppm_row["BaseProcRate"]) if ppm_row else None
                if ppm_row is None:
                    fail_closed = True
                    blockers.append(
                        f"[unknown] SpellProcsPerMinuteID {row.procs_per_minute_id} "
                        "resolves to no SpellProcsPerMinute row")
                else:
                    blockers.append(
                        f"[authored] SpellProcsPerMinuteID {row.procs_per_minute_id} "
                        f"declares an RPPM identity (base rate {ppm_rate}); an RPPM window "
                        "is a clock, i.e. per-application state")
                mutable.append(
                    "Aura::m_lastProcAttemptTime / m_lastProcSuccessTime (RPPM window)")

            if row.proc_charges:
                blockers.append(
                    f"[trinity] ProcCharges {row.proc_charges} != 0: Aura::CalcMaxCharges "
                    "(SpellAuras.cpp:1004-1015) reads SpellInfo::ProcCharges with or "
                    "without a proc entry, and Aura::Aura sets m_isUsingCharges "
                    "(SpellAuras.cpp:498-499)")
                mutable.append("Aura::m_procCharges / m_isUsingCharges at construction")

            if row.cumulative_aura > 1:
                # Mirrors: Aura::CalcMaxStackAmount (SpellAuras.cpp:1083-1090) -- a CAP.
                # Initial stacks are AuraCreateInfo::StackAmount, default 1
                # (SpellAuras.h:134, Aura::Aura SpellAuras.cpp:481), taken from
                # SpellValue::AuraStackAmount which starts at 1 (Spell.cpp:450).
                # A passive is IsMultiSlotAura (SpellInfo.cpp:1808-1811), so
                # Unit::_TryStackingOrRefreshingExistingAura (Unit.cpp:3395) never
                # stacks it: reapplication creates a separate Aura at stack 1.
                if not is_passive:
                    blockers.append(
                        f"[trinity] CumulativeAura {row.cumulative_aura} > 1 on a non-passive "
                        "provider: reapplication reaches "
                        "Unit::_TryStackingOrRefreshingExistingAura (Unit.cpp:3386-3443) "
                        "and can raise the stack")
                    mutable.append("Aura::m_stackAmount")
                if row.proc_charges:
                    blockers.append(
                        "[trinity] CumulativeAura > 1 together with ProcCharges: with "
                        "PROC_ATTR_USE_STACKS_FOR_CHARGES a proc consumes a stack "
                        "(Aura::ConsumeProcCharges, SpellAuras.cpp:1819-1831)")

            if row.proc_category_recovery_ms:
                # Mirrors: SpellProcEntry::Cooldown (SpellMgr.cpp:1581, 1885) -- with no
                # entry it reaches nothing, so this is an [authored] blocker, not a
                # [trinity] one: an internal cooldown is a clock, and a clock is state.
                blockers.append(
                    f"[authored] ProcCategoryRecovery {row.proc_category_recovery_ms} ms "
                    "declares an internal cooldown; an ICD is a clock, i.e. "
                    "per-application state")

            if row.proc_chance and spell in self.item_proc_spells:
                blockers.append(
                    "[trinity] an ItemEffect row binds this spell with "
                    "ITEM_SPELLTRIGGER_ON_PROC, so Player::CastItemCombatSpell "
                    "(Player.cpp:8669) rolls SpellInfo::ProcChance with no SpellProcEntry")

            # ProcTypeMask and ProcChance are deliberately left uninterpreted: neither
            # implies a counter or a clock.  ProcChance's only non-entry consumer is the
            # ItemEffect path tested just above; ProcTypeMask's only non-proc consumer is
            # the crowd-control amount override tested below.

            if row.proc_type_mask and not has_entry:
                # SpellAuraEffects.cpp:790-798 reads SpellInfo::ProcFlags outside the
                # proc pipeline, but only for the six crowd-control aura subtypes.
                crowd_control = CROWD_CONTROL_AMOUNT_AURAS
                if crowd_control & set(subtypes):
                    blockers.append(
                        "[trinity] nonzero ProcTypeMask on a crowd-control aura subtype "
                        "changes AuraEffect::CalculateAmount (SpellAuraEffects.cpp:790-798)")

        # ``is_passive`` is reported for every provider but is only a *blocker* when the
        # verdict depends on it, i.e. when CumulativeAura > 1 (handled above).  Ownership
        # of the Passive attribute itself belongs to ``owner.py``; ``blockers`` here stays
        # a statement about the SpellAuraOptions row alone.

        if row is not None:
            if row.proc_type_mask:
                authored.append(
                    "ProcTypeMask 0x{:X} ({}) -- an event selector, uninterpreted".format(
                        row.proc_type_mask, ", ".join(proc_flag_names(row.proc_type_mask))))
            if row.proc_chance:
                authored.append(
                    f"ProcChance {row.proc_chance} -- a probability, uninterpreted "
                    "(101 is the 'always' sentinel)")
            if row.proc_category_recovery_ms:
                authored.append(
                    f"ProcCategoryRecovery {row.proc_category_recovery_ms} ms -- a clock")
            if row.procs_per_minute_id:
                authored.append(
                    f"SpellProcsPerMinuteID {row.procs_per_minute_id} -- a clock")
            if row.proc_charges:
                authored.append(f"ProcCharges {row.proc_charges} -- a counter")
            if row.cumulative_aura:
                authored.append(
                    f"CumulativeAura {row.cumulative_aura} -- a cap, not initial stacks")

        family = self._family(row, has_entry, script_names, fail_closed, generation.reason)
        if family == "unknown" and not blockers:            # defensive: never a silent pass
            blockers.append("[unknown] family resolved to 'unknown' with no recorded blocker")

        return ProcVerdict(
            spell=spell, name=info.name, family=family, blockers=tuple(sorted(set(blockers))),
            aura_options=row, aura_options_row_count=len(rows),
            has_effective_entry=has_entry, entry_origin=origin,
            generation_reason=generation.reason, effective_reason=effective_reason,
            aura_subtypes=subtypes, proc_triggering_subtypes=triggering,
            always_triggered_subtypes=always, uncatalogued_subtypes=uncatalogued,
            has_spell_proc_overlay=has_overlay, spell_proc_overlay=overlay_row,
            script_names=script_names, unresolved_script_names=unresolved,
            script_proc_hooks=proc_hooks,
            procs_per_minute_resolves=ppm_resolves, procs_per_minute_base_rate=ppm_rate,
            mutable_state=tuple(sorted(set(mutable))),
            authored_proc_signals=tuple(authored), is_passive=is_passive)

    @staticmethod
    def _family(row: AuraOptionsRow | None, has_entry: bool,
                script_names: tuple[str, ...], fail_closed: bool,
                generation_reason: str) -> str:
        """The closed family cascade of :data:`FAMILIES`; first match wins.

        ``fail_closed`` is set by :meth:`_classify` for every fact the pinned source
        cannot settle, so an unresolved provider can never fall through into an
        admitting family.
        """
        if fail_closed:
            return "unknown"
        if has_entry:
            return "effective-proc"
        if generation_reason == "infinite-loop-guard":
            return "guarded-proc"
        if script_names:
            return "script-driven"
        if row is None:
            return "no-auraoptions"
        if row.procs_per_minute_id:
            return "rppm"
        if row.proc_charges:
            return "charges"
        if row.proc_category_recovery_ms:
            return "proc-icd"
        if generation_reason == "no-trigger-aura":
            return "inert-no-trigger-aura"
        if row.cumulative_aura > 1:
            return "stack-capacity-only"
        if generation_reason == "no-proc-flags":
            return "inert-no-proc-flags"
        return "unknown"


def shape_key(row: AuraOptionsRow | None) -> str:
    """A stable, sortable spelling of one AuraOptions shape (for the distribution)."""
    if row is None:
        return "absent"
    return (f"cum={row.cumulative_aura};rec={row.proc_category_recovery_ms};"
            f"chance={row.proc_chance};charges={row.proc_charges};"
            f"ppm={row.procs_per_minute_id};mask=0x{row.proc_type_mask:X}")
