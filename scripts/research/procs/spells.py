"""A minimal, proc-relevant port of ``SpellInfo`` construction.

Mirrors:
* ``SpellMgr::LoadSpellInfoStore`` -- per-(SpellID, DifficultyID) assembly and
  the ``Difficulty.FallbackDifficultyID`` "fill blanks" chain;
* ``SpellInfo::SpellInfo(SpellNameEntry const*, Difficulty, SpellInfoLoadHelper const&)``
  -- which DB2 field lands in which ``SpellInfo`` member (only the members the
  proc pipeline reads are ported);
* ``SpellMgr::GetSpellInfo`` -- lookup with the same fallback chain;
* ``SpellMgr::LoadSpellRanks`` -- rank chains from ``SkillLineAbility``;
* ``SpellMgr::LoadSpellInfoCustomAttributes`` -- ``SPELL_ATTR0_CU_CAN_CRIT`` and
  the ``spell_custom_attr`` overlay (only the two CU bits the proc pipeline reads).

Deliberately **not** ported (fail closed where a consumer needs them):
``SpellInfo::IsPositive`` / ``_IsPositiveEffect``, ``LoadSpellInfoCorrections``
(``ApplySpellFix`` bodies -- the touched IDs are reported instead, see
:mod:`procs.trinity`), server-side ``serverside_spell`` rows, power-cost
evaluation, and every non-proc SpellInfo member.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from . import SourceError
from .enums import (
    CAN_CRIT_EFFECTS,
    SPELL_AURA_SPELL_MAGNET,
    attr,
    is_aura_effect,
    proc_flags_from_words,
)
from .source import Source

DIFFICULTY_NONE = 0
SPELL_ATTR0_CU_DONT_BREAK_STEALTH = 0x00000040  # SpellInfo.h SpellCustomAttributes
SPELL_ATTR0_CU_CAN_CRIT = 0x00000080
_ATTR2_CANT_CRIT = attr("SPELL_ATTR2_CANT_CRIT")

EFFECT_COLUMNS = (
    "SpellID", "DifficultyID", "EffectIndex", "Effect", "EffectAura",
    "EffectTriggerSpell", "EffectBasePointsF", "EffectMiscValue_0",
    "EffectMiscValue_1", "EffectSpellClassMask_0", "EffectSpellClassMask_1",
    "EffectSpellClassMask_2", "EffectSpellClassMask_3", "ImplicitTarget_0",
    "ImplicitTarget_1", "EffectAuraPeriod", "EffectMechanic", "EffectAmplitude",
    "EffectAttributes", "ID",
)
AURA_OPTIONS_COLUMNS = (
    "ID", "SpellID", "DifficultyID", "CumulativeAura", "ProcCategoryRecovery",
    "ProcChance", "ProcCharges", "SpellProcsPerMinuteID", "ProcTypeMask_0",
    "ProcTypeMask_1",
)
MISC_COLUMNS = ("ID", "SpellID", "DifficultyID", "SchoolMask", "DurationIndex") + tuple(
    f"Attributes_{i}" for i in range(17))
CATEGORIES_COLUMNS = ("ID", "SpellID", "DifficultyID", "Category", "DefenseType",
                      "DispelType", "Mechanic", "StartRecoveryCategory",
                      "ChargeCategory", "PreventionType")
COOLDOWNS_COLUMNS = ("ID", "SpellID", "DifficultyID", "RecoveryTime",
                     "CategoryRecoveryTime", "StartRecoveryTime", "AuraSpellID")
CLASS_OPTIONS_COLUMNS = ("ID", "SpellID", "SpellClassSet", "SpellClassMask_0",
                         "SpellClassMask_1", "SpellClassMask_2", "SpellClassMask_3")
EQUIPPED_COLUMNS = ("ID", "SpellID", "EquippedItemClass", "EquippedItemSubclass",
                    "EquippedItemInvTypes")
#: Tables whose (SpellID, DifficultyID) pairs create SpellInfo keys but whose
#: *content* the proc pipeline does not read.  Only the key columns are loaded.
KEY_ONLY_PER_DIFFICULTY = ("SpellAuraRestrictions", "SpellInterrupts", "SpellLevels",
                           "SpellTargetRestrictions", "SpellXSpellVisual")
KEY_ONLY_DIFFICULTY_NONE = ("SpellCastingRequirements", "SpellReagents",
                            "SpellReagentsCurrency", "SpellScaling", "SpellShapeshift",
                            "SpellTotems")


def mask128(words: Iterable[int]) -> int:
    """``flag128`` from four signed DB2 int32 words."""
    out = 0
    for i, w in enumerate(words):
        out |= (int(w) & 0xFFFFFFFF) << (32 * i)
    return out


@dataclass(frozen=True)
class EffectInfo:
    """The ``SpellEffectInfo`` members the proc pipeline reads."""

    index: int
    effect: int
    aura: int
    trigger_spell: int
    base_points: float
    misc0: int
    misc1: int
    class_mask: int
    target_a: int
    target_b: int
    aura_period: int
    mechanic: int
    row_id: int
    difficulty: int

    @property
    def is_effect(self) -> bool:
        """Mirrors: ``SpellEffectInfo::IsEffect``."""
        return self.effect != 0

    @property
    def is_aura(self) -> bool:
        """Mirrors: ``SpellEffectInfo::IsAura``."""
        return is_aura_effect(self.effect, self.aura)

    def calc_value_as_int_unscaled(self) -> int:
        """``int32(BasePoints)`` for ``SpellEffectInfo::CalcValueAsInt`` with no caster.

        Only used by the default-generation ``MOD_HIT_CHANCE <= -100`` test,
        which Trinity evaluates at load with no caster and no item.  Scaling
        paths (``Scaling.Class``) are not ported; callers must treat a nonzero
        ``ScalingClass`` as unknown (checked in :mod:`procs.definition`).
        """
        return int(self.base_points)


@dataclass(frozen=True)
class SpellInfo:
    id: int
    difficulty: int
    name: str
    attributes: tuple[int, ...]
    attributes_cu: int
    school_mask: int
    effects: tuple[EffectInfo, ...]
    proc_flags: int
    proc_chance: int
    proc_charges: int
    proc_cooldown: int
    stack_amount: int
    ppm_id: int
    base_ppm: float
    ppm_flags: int | None
    ppm_mods: tuple[dict[str, Any], ...]
    family: int
    family_flags: int
    dmg_class: int
    category: int
    charge_category: int
    mechanic: int
    recovery_time: int
    category_recovery_time: int
    start_recovery_time: int
    equipped_item_class: int
    equipped_item_subclass_mask: int
    equipped_item_inventory_type_mask: int
    labels: tuple[int, ...]
    duration_ms: int | None
    provenance: dict[str, Any] = field(compare=False)
    scaling_class_effects: tuple[int, ...] = ()

    def has_attr(self, key: tuple[int, int]) -> bool:
        word, bit = key
        return bool((self.attributes[word] & 0xFFFFFFFF) & bit)

    def has_aura(self, aura_type: int) -> bool:
        """Mirrors: ``SpellInfo::HasAura`` (any effect that IsAura(type))."""
        return any(e.is_aura and e.aura == aura_type for e in self.effects)

    def has_effect(self, effect_type: int) -> bool:
        return any(e.effect == effect_type for e in self.effects)

    @property
    def is_passive(self) -> bool:
        return self.has_attr(attr("SPELL_ATTR0_PASSIVE"))

    def effect(self, index: int) -> EffectInfo | None:
        for e in self.effects:
            if e.index == index:
                return e
        return None

    def is_affected(self, family_name: int, family_flags: int) -> bool:
        """Mirrors: ``SpellInfo::IsAffected(uint32, flag128 const&)``."""
        if not family_name:
            return True
        if family_name != self.family:
            return False
        if family_flags and not (family_flags & self.family_flags):
            return False
        return True

    def mechanic_mask(self) -> int:
        """``SpellInfo::GetAllEffectsMechanicMask`` (spell + effect mechanics)."""
        mask = 0
        if self.mechanic:
            mask |= 1 << self.mechanic
        for e in self.effects:
            if e.is_effect and e.mechanic:
                mask |= 1 << e.mechanic
        return mask


class SpellCatalog:
    """Lazy ``SpellInfo`` store over one snapshot plus an optional CU overlay."""

    def __init__(self, source: Source, custom_attributes: dict[int, int] | None = None) -> None:
        self.source = source
        self.custom_attributes = custom_attributes or {}
        self._built: dict[tuple[int, int], SpellInfo | None] = {}
        self._load()

    # -- loading -----------------------------------------------------------
    def _load(self) -> None:
        src = self.source
        self.names: dict[int, str] = {r[0]: r[1] for r in src.project("SpellName", ("ID", "Name_lang"))}
        self.fallback: dict[int, int] = {
            r[0]: r[1] for r in src.project("Difficulty", ("ID", "FallbackDifficultyID"))}

        self.effects: dict[tuple[int, int], dict[int, EffectInfo]] = defaultdict(dict)
        scaling = {r[0]: r[1] for r in src.project("SpellEffect", ("ID", "ScalingClass"))}
        self._scaling_class = scaling
        for r in src.project("SpellEffect", EFFECT_COLUMNS):
            row = dict(zip(EFFECT_COLUMNS, r))
            eff = EffectInfo(
                index=row["EffectIndex"], effect=row["Effect"], aura=row["EffectAura"],
                trigger_spell=row["EffectTriggerSpell"],
                base_points=float(row["EffectBasePointsF"]),
                misc0=row["EffectMiscValue_0"], misc1=row["EffectMiscValue_1"],
                class_mask=mask128(row[f"EffectSpellClassMask_{i}"] for i in range(4)),
                target_a=row["ImplicitTarget_0"], target_b=row["ImplicitTarget_1"],
                aura_period=row["EffectAuraPeriod"], mechanic=row["EffectMechanic"],
                row_id=row["ID"], difficulty=row["DifficultyID"])
            # LoadSpellInfoStore: loadData[...].Effects[EffectIndex] = effect (later row wins)
            self.effects[(row["SpellID"], row["DifficultyID"])][eff.index] = eff

        def by_key(table: str, columns: tuple[str, ...], per_difficulty: bool) -> dict:
            out = {}
            for r in src.project(table, columns):
                row = dict(zip(columns, r))
                diff = row["DifficultyID"] if per_difficulty else DIFFICULTY_NONE
                out[(row["SpellID"], diff)] = row
            return out

        self.aura_options = by_key("SpellAuraOptions", AURA_OPTIONS_COLUMNS, True)
        self.misc = by_key("SpellMisc", MISC_COLUMNS, True)
        self.categories = by_key("SpellCategories", CATEGORIES_COLUMNS, True)
        self.cooldowns = by_key("SpellCooldowns", COOLDOWNS_COLUMNS, True)
        self.class_options = by_key("SpellClassOptions", CLASS_OPTIONS_COLUMNS, False)
        self.equipped = by_key("SpellEquippedItems", EQUIPPED_COLUMNS, False)
        self.labels: dict[tuple[int, int], list[int]] = defaultdict(list)
        for spell_id, label in src.project("SpellLabel", ("SpellID", "LabelID")):
            self.labels[(spell_id, DIFFICULTY_NONE)].append(label)

        self.durations = {r[0]: int(r[1]) for r in src.project("SpellDuration", ("ID", "Duration"))}
        self.ppm = {r[0]: {"id": r[0], "base": float(r[1]), "flags": r[2]}
                    for r in src.project("SpellProcsPerMinute", ("ID", "BaseProcRate", "Flags"))}
        self.ppm_mods: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for r in src.project("SpellProcsPerMinuteMod",
                             ("ID", "Type", "Param", "Coeff", "SpellProcsPerMinuteID")):
            # DB2Manager::LoadStores: push_back in store (ascending ID) order.
            self.ppm_mods[r[4]].append({"id": r[0], "type": r[1], "param": r[2],
                                        "coeff": float(r[3])})

        keys: set[tuple[int, int]] = set(self.effects)
        for d in (self.aura_options, self.misc, self.categories, self.cooldowns,
                  self.class_options, self.equipped, self.labels):
            keys.update(d)
        for table in KEY_ONLY_PER_DIFFICULTY:
            keys.update(src.project(table, ("SpellID", "DifficultyID")))
        for table in KEY_ONLY_DIFFICULTY_NONE:
            keys.update((r[0], DIFFICULTY_NONE) for r in src.project(table, ("SpellID",)))
        power_difficulty = {r[0]: r[1] for r in src.project("SpellPowerDifficulty", ("ID", "DifficultyID"))}
        for power_id, spell_id in src.project("SpellPower", ("ID", "SpellID")):
            keys.add((spell_id, power_difficulty.get(power_id, DIFFICULTY_NONE)))
        # LoadSpellInfoStore skips keys without a SpellName row.
        self.keys = {k for k in keys if k[0] in self.names}
        self.spell_ids = sorted({k[0] for k in self.keys})

        self._ranks_next: dict[int, int] = {}
        self._ranks_prev: dict[int, int] = {}
        for spell, supersedes in src.project("SkillLineAbility", ("Spell", "SupercedesSpell")):
            if not supersedes:
                continue
            if (supersedes, DIFFICULTY_NONE) not in self.keys or (spell, DIFFICULTY_NONE) not in self.keys:
                continue
            # std::map assignment: a later row for the same SupercedesSpell wins.
            self._ranks_next[supersedes] = spell
            self._ranks_prev[spell] = supersedes

    # -- lookup ------------------------------------------------------------
    def fallback_chain(self, difficulty: int) -> list[int]:
        """``[difficulty, fallback, fallback-of-fallback, ...]`` as Trinity walks it."""
        chain = [difficulty]
        seen = {difficulty}
        current = difficulty
        while current in self.fallback:
            nxt = self.fallback[current]
            chain.append(nxt)
            if nxt in seen:  # Trinity would loop; the snapshot never does this
                raise SourceError(f"Difficulty fallback cycle at {nxt}")
            seen.add(nxt)
            current = nxt
        return chain

    def get(self, spell_id: int, difficulty: int = DIFFICULTY_NONE) -> SpellInfo | None:
        """Mirrors: ``SpellMgr::GetSpellInfo`` (exact key, then fallback chain)."""
        for diff in self.fallback_chain(difficulty):
            if (spell_id, diff) in self.keys:
                return self._build(spell_id, diff)
        return None

    def require(self, spell_id: int, difficulty: int = DIFFICULTY_NONE) -> SpellInfo:
        info = self.get(spell_id, difficulty)
        if info is None:
            raise SourceError(f"no SpellInfo for spell {spell_id} difficulty {difficulty}")
        return info

    def exists(self, spell_id: int, difficulty: int = DIFFICULTY_NONE) -> bool:
        return any((spell_id, d) in self.keys for d in self.fallback_chain(difficulty))

    def next_rank(self, spell_id: int) -> int | None:
        return self._ranks_next.get(spell_id)

    def is_ranked(self, spell_id: int) -> bool:
        return spell_id in self._ranks_next or spell_id in self._ranks_prev

    def first_rank(self, spell_id: int) -> int:
        while spell_id in self._ranks_prev:
            spell_id = self._ranks_prev[spell_id]
        return spell_id

    def difficulties_with_proc_data(self, spell_id: int) -> list[int]:
        return sorted(d for (s, d) in self.aura_options if s == spell_id)

    # -- assembly ----------------------------------------------------------
    def _fill(self, table: dict, spell_id: int, difficulty: int) -> tuple[Any, int | None]:
        for diff in self.fallback_chain(difficulty):
            row = table.get((spell_id, diff))
            if row is not None:
                return row, diff
        return None, None

    def _build(self, spell_id: int, difficulty: int) -> SpellInfo:
        key = (spell_id, difficulty)
        if key in self._built:
            return self._built[key]
        chain = self.fallback_chain(difficulty)
        provenance: dict[str, Any] = {"difficulty_chain": chain}

        effects: dict[int, EffectInfo] = {}
        for diff in chain:
            for index, eff in self.effects.get((spell_id, diff), {}).items():
                effects.setdefault(index, eff)
        provenance["SpellEffect"] = {i: (e.row_id, e.difficulty) for i, e in sorted(effects.items())}

        options, d_opt = self._fill(self.aura_options, spell_id, difficulty)
        misc, d_misc = self._fill(self.misc, spell_id, difficulty)
        cats, d_cat = self._fill(self.categories, spell_id, difficulty)
        cds, d_cd = self._fill(self.cooldowns, spell_id, difficulty)
        cls, _ = self._fill(self.class_options, spell_id, difficulty)
        equip, _ = self._fill(self.equipped, spell_id, difficulty)
        labels, _ = self._fill(self.labels, spell_id, difficulty)
        for label, row, diff in (("SpellAuraOptions", options, d_opt), ("SpellMisc", misc, d_misc),
                                 ("SpellCategories", cats, d_cat), ("SpellCooldowns", cds, d_cd),
                                 ("SpellClassOptions", cls, 0), ("SpellEquippedItems", equip, 0)):
            if row is not None:
                provenance[label] = {"ID": row["ID"], "DifficultyID": diff}

        attributes = tuple(int(misc[f"Attributes_{i}"]) & 0xFFFFFFFF if misc else 0 for i in range(17))

        proc_flags = proc_chance = proc_charges = proc_cooldown = stack = ppm_id = 0
        base_ppm = 0.0
        ppm_flags = None
        mods: tuple[dict[str, Any], ...] = ()
        if options:
            proc_flags = proc_flags_from_words(options["ProcTypeMask_0"], options["ProcTypeMask_1"])
            proc_chance = int(options["ProcChance"])
            # SpellInfo::ProcCharges is uint32 assigned from DB2 int32.
            proc_charges = int(options["ProcCharges"]) & 0xFFFFFFFF
            proc_cooldown = int(options["ProcCategoryRecovery"]) & 0xFFFFFFFF
            stack = int(options["CumulativeAura"])
            ppm_id = int(options["SpellProcsPerMinuteID"])
            ppm = self.ppm.get(ppm_id)
            if ppm is not None:
                base_ppm = ppm["base"]
                ppm_flags = ppm["flags"]
                mods = tuple(self.ppm_mods.get(ppm_id, ()))

        effect_tuple = tuple(effects[i] for i in sorted(effects))
        # LoadSpellInfoCorrections: "disable proc for magnet auras"
        if any(e.is_aura and e.aura == SPELL_AURA_SPELL_MAGNET for e in effect_tuple):
            if proc_flags:
                provenance["correction"] = "SPELL_AURA_SPELL_MAGNET: ProcFlags cleared"
            proc_flags = 0

        cu = 0
        if any(e.effect in CAN_CRIT_EFFECTS for e in effect_tuple):
            cu |= SPELL_ATTR0_CU_CAN_CRIT
        if cu & SPELL_ATTR0_CU_CAN_CRIT and (attributes[2] & _ATTR2_CANT_CRIT[1]):
            cu &= ~SPELL_ATTR0_CU_CAN_CRIT
        cu |= self.custom_attributes.get(spell_id, 0)

        info = SpellInfo(
            id=spell_id, difficulty=difficulty, name=self.names.get(spell_id, ""),
            attributes=attributes, attributes_cu=cu,
            school_mask=int(misc["SchoolMask"]) if misc else 0,
            effects=effect_tuple,
            proc_flags=proc_flags, proc_chance=proc_chance, proc_charges=proc_charges,
            proc_cooldown=proc_cooldown, stack_amount=stack, ppm_id=ppm_id,
            base_ppm=base_ppm, ppm_flags=ppm_flags, ppm_mods=mods,
            family=int(cls["SpellClassSet"]) if cls else 0,
            family_flags=mask128(cls[f"SpellClassMask_{i}"] for i in range(4)) if cls else 0,
            dmg_class=int(cats["DefenseType"]) if cats else 0,
            category=int(cats["Category"]) if cats else 0,
            charge_category=int(cats["ChargeCategory"]) if cats else 0,
            mechanic=int(cats["Mechanic"]) if cats else 0,
            recovery_time=int(cds["RecoveryTime"]) if cds else 0,
            category_recovery_time=int(cds["CategoryRecoveryTime"]) if cds else 0,
            start_recovery_time=int(cds["StartRecoveryTime"]) if cds else 0,
            equipped_item_class=int(equip["EquippedItemClass"]) if equip else -1,
            equipped_item_subclass_mask=int(equip["EquippedItemSubclass"]) if equip else 0,
            equipped_item_inventory_type_mask=int(equip["EquippedItemInvTypes"]) if equip else 0,
            labels=tuple(labels or ()),
            duration_ms=(self.durations.get(int(misc["DurationIndex"])) if misc and misc["DurationIndex"] else None),
            provenance=provenance,
            scaling_class_effects=tuple(sorted(
                e.index for e in effect_tuple if self._scaling_class.get(e.row_id, 0))),
        )
        self._built[key] = info
        return info

    def proc_flag_spells(self, difficulty: int = DIFFICULTY_NONE) -> list[int]:
        """Spell IDs whose SpellInfo (at ``difficulty``) has nonzero ProcFlags."""
        out = []
        for spell_id in sorted({k[0] for k in self.aura_options}):
            info = self.get(spell_id, difficulty)
            if info is not None and info.difficulty in self.fallback_chain(difficulty) and info.proc_flags:
                out.append(spell_id)
        return out
