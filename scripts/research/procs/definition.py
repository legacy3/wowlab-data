"""Immutable proc definitions: ``SpellProcEntry`` resolution and provider facts.

Mirrors:
* ``SpellMgr::LoadSpellProcs`` -- both halves:
  1. ``spell_proc`` rows merged with DB2 defaults ("take defaults from dbcs")
     and validated (the corrections it applies, not just its log lines);
  2. default generation for every other SpellInfo with ProcFlags
     ("Generating spell proc data from SpellMap"), including the
     ``isTriggerAura`` / ``isAlwaysTriggeredAura`` / ``spellTypeMask`` tables,
     the hit-mask switch, and the ``SPELL_ATTR3_CAN_PROC_FROM_PROCS``
     infinite-loop guard;
* ``SpellMgr::GetSpellProcEntry`` -- lookup by (SpellID, Difficulty) then the
  difficulty fallback chain.  Generated entries are merged *after* the loop,
  so generation only ever skips a spell because of a ``spell_proc`` row.

What a :class:`ProcDefinition` adds on top of the entry is a *description* of
consumer behaviour that is fully decided by immutable data (which effects can
proc, which handler each reaches, which spell it would trigger, which chance
model ``Aura::CalcProcChance`` selects) and an explicit list of everything that
is **not** decided by immutable data (spell mods, conditions, scripts,
equipment, positivity).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: ``DB2Manager::IsValidSpellFamiliyName`` -- the SpellFamilyNames enum values.
from ._trinity_names import SPELL_FAMILY_NAMES
from .enums import (
    ALWAYS_TRIGGERED_AURAS,
    ATTR3_CAN_PROC_FROM_PROCS,
    ATTR3_NO_PROC_EQUIP_REQUIREMENT,
    AURA_SPELL_TYPE_MASK,
    CHECK_EFFECT_PROC_GATES,
    DONE_HIT_PROC_FLAG_MASK,
    HANDLE_PROC_ACTIONS,
    HIT_NAMES,
    INFINITE_LOOP_GUARD_FLAGS,
    KNOWN_PROC_FLAGS,
    PROC_ATTR_ALL_ALLOWED,
    PROC_ATTR_NAMES,
    PROC_ATTR_REQ_EXP_OR_HONOR,
    PROC_ATTR_REQ_SPELLMOD,
    PROC_ATTR_TRIGGERED_CAN_PROC,
    PROC_ATTRIBUTE_CONSUMERS,
    PROC_FLAG_2_CAST_SUCCESSFUL,
    PROC_FLAG_KILL,
    PROC_HIT_BLOCK,
    PROC_HIT_CRITICAL,
    PROC_HIT_MASK_ALL,
    PROC_HIT_MISS,
    PROC_HIT_NONE,
    PROC_HIT_REFLECT,
    PROC_SPELL_PHASE_CAST,
    PROC_SPELL_PHASE_FINISH,
    PROC_SPELL_PHASE_HIT,
    PROC_SPELL_PHASE_MASK_ALL,
    PROC_SPELL_TYPE_MASK_ALL,
    PROC_SPELL_TYPE_NONE,
    REQ_SPELL_PHASE_PROC_FLAG_MASK,
    SPELL_AURA_DUMMY,
    SPELL_AURA_MOD_BLOCK_PERCENT,
    SPELL_AURA_MOD_HIT_CHANCE,
    SPELL_AURA_MOD_WEAPON_CRIT_PERCENT,
    SPELL_AURA_PROC_TRIGGER_DAMAGE,
    SPELL_AURA_PROC_TRIGGER_SPELL,
    SPELL_AURA_PROC_TRIGGER_SPELL_WITH_VALUE,
    SPELL_AURA_REFLECT_SPELLS,
    SPELL_AURA_REFLECT_SPELLS_SCHOOL,
    SPELL_EFFECT_ADD_EXTRA_ATTACKS,
    SPELL_PHASE_NAMES,
    SPELL_PROC_FLAG_MASK,
    SPELL_SCHOOL_MASK_ALL,
    SPELL_TYPE_NAMES,
    SPELLMOD_AURAS,
    TAKEN_HIT_PROC_FLAG_MASK,
    TRIGGER_AURAS,
    TRIGGER_SPELL_HANDLERS,
    UNCONSUMED_PROC_NAMED_ATTRIBUTES,
    attr_name,
    aura_name,
    bit_names,
    effect_name,
    proc_flag_names,
)
from .spells import DIFFICULTY_NONE, SpellCatalog, SpellInfo
from .trinity import SpellProcRow, TrinityOverlay


@dataclass(frozen=True)
class ProcEntry:
    """``struct SpellProcEntry`` (SpellMgr.h), after load-time merge/validation."""

    school_mask: int
    family_name: int
    family_mask: int
    proc_flags: int
    spell_type_mask: int
    spell_phase_mask: int
    hit_mask: int
    attributes_mask: int
    disable_effects_mask: int
    procs_per_minute: float
    chance: float
    cooldown_ms: int
    charges: int
    origin: str                 # "spell_proc" | "generated"
    keyed_difficulty: int

    def to_dict(self) -> dict[str, Any]:
        d = dict(self.__dict__)
        d["family_mask"] = f"0x{self.family_mask:032X}"
        d["proc_flags_hex"] = f"0x{self.proc_flags:016X}"
        d["proc_flag_names"] = proc_flag_names(self.proc_flags)
        d["spell_type_names"] = bit_names(self.spell_type_mask, SPELL_TYPE_NAMES)
        d["spell_phase_names"] = bit_names(self.spell_phase_mask, SPELL_PHASE_NAMES)
        d["hit_mask_names"] = bit_names(self.hit_mask, HIT_NAMES) or ["NONE (default)"]
        d["attribute_names"] = bit_names(self.attributes_mask, PROC_ATTR_NAMES)
        return d


# ---------------------------------------------------------------------------
# LoadSpellProcs
# ---------------------------------------------------------------------------

def merge_spell_proc_row(row: SpellProcRow, info: SpellInfo, log: list[str]) -> ProcEntry:
    """``spell_proc`` half of LoadSpellProcs for one (row, SpellInfo) pair."""
    proc_flags = row.proc_flags
    charges = row.charges
    chance = row.chance
    cooldown = row.cooldown
    # take defaults from dbcs
    if not proc_flags:
        proc_flags = info.proc_flags
    if not charges:
        charges = info.proc_charges
    if not chance and not row.procs_per_minute:
        chance = float(info.proc_chance)
    if cooldown == 0:
        cooldown = info.proc_cooldown
    ppm = row.procs_per_minute
    phase = row.spell_phase_mask
    attrs = row.attributes_mask

    sid = info.id
    if row.school_mask & ~SPELL_SCHOOL_MASK_ALL:
        log.append(f"spell {sid}: wrong SchoolMask {row.school_mask}")
    if row.family_name and row.family_name not in SPELL_FAMILY_NAMES:
        log.append(f"spell {sid}: wrong SpellFamilyName {row.family_name}")
    if chance < 0:
        log.append(f"spell {sid}: negative Chance -> 0")
        chance = 0.0
    if ppm < 0:
        log.append(f"spell {sid}: negative ProcsPerMinute -> 0")
        ppm = 0.0
    if not proc_flags:
        log.append(f"spell {sid}: no ProcFlags, proc will not be triggered")
    if row.spell_type_mask & ~PROC_SPELL_TYPE_MASK_ALL:
        log.append(f"spell {sid}: wrong SpellTypeMask {row.spell_type_mask}")
    if row.spell_type_mask and not (proc_flags & SPELL_PROC_FLAG_MASK):
        log.append(f"spell {sid}: SpellTypeMask set but unused for these ProcFlags")
    if not phase and proc_flags & REQ_SPELL_PHASE_PROC_FLAG_MASK:
        log.append(f"spell {sid}: SpellPhaseMask required by ProcFlags but missing; proc will not be triggered")
    if phase & ~PROC_SPELL_PHASE_MASK_ALL:
        log.append(f"spell {sid}: wrong SpellPhaseMask {phase}")
    if phase and not (proc_flags & REQ_SPELL_PHASE_PROC_FLAG_MASK):
        log.append(f"spell {sid}: SpellPhaseMask set but unused for these ProcFlags")
    if not phase and not (proc_flags & REQ_SPELL_PHASE_PROC_FLAG_MASK) and proc_flags & PROC_FLAG_2_CAST_SUCCESSFUL:
        phase = PROC_SPELL_PHASE_CAST
    if row.hit_mask & ~PROC_HIT_MASK_ALL:
        log.append(f"spell {sid}: wrong HitMask {row.hit_mask}")
    if row.hit_mask and not (proc_flags & TAKEN_HIT_PROC_FLAG_MASK or (
            proc_flags & DONE_HIT_PROC_FLAG_MASK
            and (not phase or phase & (PROC_SPELL_PHASE_HIT | PROC_SPELL_PHASE_FINISH)))):
        log.append(f"spell {sid}: HitMask set but unused for these ProcFlags/SpellPhaseMask")
    for eff in info.effects:
        if row.disable_effects_mask & (1 << eff.index) and not eff.is_aura:
            log.append(f"spell {sid}: DisableEffectsMask names non-aura effect {eff.index}")
    if attrs & PROC_ATTR_REQ_SPELLMOD:
        if not any(e.is_aura and e.aura in SPELLMOD_AURAS for e in info.effects):
            log.append(f"spell {sid}: PROC_ATTR_REQ_SPELLMOD but spell has no spell mods")
    if attrs & ~PROC_ATTR_ALL_ALLOWED:
        log.append(f"spell {sid}: invalid AttributesMask bits 0x{attrs & ~PROC_ATTR_ALL_ALLOWED:02X} dropped")
        attrs &= PROC_ATTR_ALL_ALLOWED

    return ProcEntry(
        school_mask=row.school_mask, family_name=row.family_name, family_mask=row.family_mask,
        proc_flags=proc_flags, spell_type_mask=row.spell_type_mask, spell_phase_mask=phase,
        hit_mask=row.hit_mask, attributes_mask=attrs,
        disable_effects_mask=row.disable_effects_mask, procs_per_minute=ppm,
        chance=chance, cooldown_ms=cooldown, charges=charges,
        origin="spell_proc", keyed_difficulty=info.difficulty)


@dataclass
class GenerationResult:
    entry: ProcEntry | None
    reason: str
    log: list[str] = field(default_factory=list)


def generate_default_entry(info: SpellInfo) -> GenerationResult:
    """Default-generation half of LoadSpellProcs for one SpellInfo."""
    if not info.proc_flags:
        return GenerationResult(None, "no-proc-flags")

    add_trigger_flag = False
    type_mask = PROC_SPELL_TYPE_NONE
    non_proc_mask = 0
    for eff in info.effects:
        if not eff.is_effect:
            continue
        aura = eff.aura
        if not aura:
            continue
        if aura not in TRIGGER_AURAS:
            # explicitly disable non proccing auras to avoid losing charges on self proc
            non_proc_mask |= 1 << eff.index
            continue
        type_mask |= AURA_SPELL_TYPE_MASK.get(aura, PROC_SPELL_TYPE_MASK_ALL)
        if aura in ALWAYS_TRIGGERED_AURAS:
            add_trigger_flag = True
        if not add_trigger_flag and info.proc_flags & TAKEN_HIT_PROC_FLAG_MASK:
            if aura in (SPELL_AURA_PROC_TRIGGER_SPELL, SPELL_AURA_PROC_TRIGGER_DAMAGE):
                add_trigger_flag = True

    if not type_mask:
        log = []
        if any(e.is_aura for e in info.effects):
            log.append(f"spell {info.id}: DB2 ProcFlags but only non-proc aura types; "
                       "needs a spell_proc row")
        return GenerationResult(None, "no-trigger-aura", log)

    family_mask = 0
    for eff in info.effects:
        if eff.is_effect and eff.aura in TRIGGER_AURAS:
            family_mask |= eff.class_mask
    family_name = info.family if family_mask else 0

    phase = PROC_SPELL_PHASE_HIT
    if not (info.proc_flags & REQ_SPELL_PHASE_PROC_FLAG_MASK) and info.proc_flags & PROC_FLAG_2_CAST_SUCCESSFUL:
        phase = PROC_SPELL_PHASE_CAST

    hit_mask = PROC_HIT_NONE
    triggers_spell = False
    unknown_value = False
    for eff in info.effects:
        if not eff.is_aura:
            continue
        a = eff.aura
        if a in (SPELL_AURA_REFLECT_SPELLS, SPELL_AURA_REFLECT_SPELLS_SCHOOL):
            hit_mask = PROC_HIT_REFLECT
        elif a == SPELL_AURA_MOD_WEAPON_CRIT_PERCENT:
            hit_mask = PROC_HIT_CRITICAL
        elif a == SPELL_AURA_MOD_BLOCK_PERCENT:
            hit_mask = PROC_HIT_BLOCK
        elif a == SPELL_AURA_MOD_HIT_CHANCE:
            if eff.index in info.scaling_class_effects:
                unknown_value = True
            if eff.calc_value_as_int_unscaled() <= -100:
                hit_mask = PROC_HIT_MISS
        elif a in (SPELL_AURA_PROC_TRIGGER_SPELL, SPELL_AURA_PROC_TRIGGER_SPELL_WITH_VALUE):
            triggers_spell = eff.trigger_spell != 0
        else:
            continue
        break  # the switch's trailing `break` leaves the effect loop

    attrs = 0
    if info.proc_flags & PROC_FLAG_KILL:
        attrs |= PROC_ATTR_REQ_EXP_OR_HONOR
    if add_trigger_flag:
        attrs |= PROC_ATTR_TRIGGERED_CAN_PROC

    chance = float(info.proc_chance)
    cooldown = info.proc_cooldown
    charges = info.proc_charges

    if (info.has_attr(ATTR3_CAN_PROC_FROM_PROCS) and not family_mask
            and chance >= 100
            and info.base_ppm <= 0.0
            and cooldown <= 0
            and charges <= 0
            and info.proc_flags & INFINITE_LOOP_GUARD_FLAGS
            and triggers_spell):
        return GenerationResult(None, "infinite-loop-guard", [
            f"spell {info.id}: CAN_PROC_FROM_PROCS with no restriction and no cooldown; "
            "proc data not generated, spell_proc row required"])

    entry = ProcEntry(
        school_mask=0, family_name=family_name, family_mask=family_mask,
        proc_flags=info.proc_flags, spell_type_mask=type_mask, spell_phase_mask=phase,
        hit_mask=hit_mask, attributes_mask=attrs, disable_effects_mask=non_proc_mask,
        procs_per_minute=0.0, chance=chance, cooldown_ms=cooldown, charges=charges,
        origin="generated", keyed_difficulty=info.difficulty)
    return GenerationResult(entry, "generated-with-unported-scaling" if unknown_value else "generated")


class ProcEntryStore:
    """``mSpellProcMap`` over a catalog and an overlay (overlay may be ``None``)."""

    def __init__(self, catalog: SpellCatalog, overlay: TrinityOverlay | None) -> None:
        self.catalog = catalog
        self.overlay = overlay
        self.db: dict[tuple[int, int], ProcEntry] = {}
        self.db_log: dict[int, list[str]] = {}
        self.db_skipped: dict[int, str] = {}
        if overlay is not None:
            for spell_id, row in sorted(overlay.spell_proc.items()):
                info = catalog.get(spell_id, DIFFICULTY_NONE)
                if info is None:
                    self.db_skipped[spell_id] = "spell does not exist in this snapshot"
                    continue
                if row.all_ranks and catalog.first_rank(spell_id) != spell_id:
                    self.db_skipped[spell_id] = "all-ranks row on a non-first rank"
                    continue
                current: int | None = spell_id
                while current is not None:
                    cur_info = catalog.require(current, DIFFICULTY_NONE)
                    key = (current, cur_info.difficulty)
                    if key in self.db:
                        self.db_log.setdefault(current, []).append("first rank already in table")
                        break
                    log: list[str] = []
                    self.db[key] = merge_spell_proc_row(row, cur_info, log)
                    if log:
                        self.db_log[current] = log
                    current = catalog.next_rank(current) if row.all_ranks else None
        self._generated: dict[tuple[int, int], GenerationResult] = {}

    def _db_on_chain(self, spell_id: int, difficulty: int) -> ProcEntry | None:
        for diff in self.catalog.fallback_chain(difficulty):
            entry = self.db.get((spell_id, diff))
            if entry is not None:
                return entry
        return None

    def generation(self, spell_id: int, difficulty: int) -> GenerationResult:
        """Generation outcome for the *exact* SpellInfo key (spell_id, difficulty)."""
        key = (spell_id, difficulty)
        cached = self._generated.get(key)
        if cached is not None:
            return cached
        if key not in self.catalog.keys:
            result = GenerationResult(None, "no-spellinfo-at-difficulty")
        elif self._db_on_chain(spell_id, difficulty) is not None:
            result = GenerationResult(None, "spell_proc-row-present")
        else:
            info = self.catalog._build(spell_id, difficulty)
            result = generate_default_entry(info)
        self._generated[key] = result
        return result

    def lookup(self, spell_id: int, difficulty: int = DIFFICULTY_NONE) -> ProcEntry | None:
        """Mirrors: ``SpellMgr::GetSpellProcEntry`` for SpellInfo(spell_id, difficulty)."""
        info = self.catalog.get(spell_id, difficulty)
        if info is None:
            return None
        for diff in self.catalog.fallback_chain(info.difficulty):
            db = self.db.get((spell_id, diff))
            if db is not None:
                return db
            gen = self.generation(spell_id, diff)
            if gen.entry is not None:
                return gen.entry
        return None


# ---------------------------------------------------------------------------
# Provider description
# ---------------------------------------------------------------------------

#: Aura::CalcProcChance model names.
CHANCE_FIXED = "fixed"
CHANCE_GUARANTEED = "fixed-guaranteed"
CHANCE_ZERO = "fixed-zero"
CHANCE_CLASSIC_PPM = "classic-ppm"
CHANCE_RPPM = "rppm"


def chance_model(entry: ProcEntry, info: SpellInfo) -> dict[str, Any]:
    """Which branch of ``Aura::CalcProcChance`` applies, from immutable data alone.

    Order in the consumer: ``chance = Chance``; if caster exists: classic PPM
    replaces it *only when the event carries DamageInfo and ProcsPerMinute != 0*;
    RPPM (``SpellInfo::ProcBasePPM > 0``) then replaces whatever is there;
    spell mods (SpellModOp::ProcChance) apply to the result; finally
    PROC_ATTR_REDUCE_PROC_60 scales by actor level.  With no caster only the
    fixed chance and REDUCE_PROC_60 apply.
    """
    notes = []
    if info.base_ppm > 0.0:
        model = CHANCE_RPPM
        if entry.procs_per_minute:
            notes.append("spell_proc ProcsPerMinute is shadowed by RPPM when a caster exists")
    elif entry.procs_per_minute:
        model = CHANCE_CLASSIC_PPM
        notes.append(f"events without DamageInfo fall back to fixed Chance={entry.chance:g}")
    elif entry.chance >= 100.0:
        model = CHANCE_GUARANTEED
    elif entry.chance <= 0.0:
        model = CHANCE_ZERO
    else:
        model = CHANCE_FIXED
    if info.base_ppm > 0.0 or entry.procs_per_minute:
        notes.append(f"if the aura caster is gone, only fixed Chance={entry.chance:g} applies")
    out = {
        "model": model,
        "fixed_chance": entry.chance,
        "classic_ppm": entry.procs_per_minute,
        "rppm_base": info.base_ppm,
        "rppm_id": info.ppm_id or None,
        "rppm_flags_unconsumed": info.ppm_flags,
        "rppm_modifiers": [dict(m) for m in info.ppm_mods],
        "reduce_proc_60": bool(entry.attributes_mask & 0x80),
        "notes": notes,
        "external_inputs": ["SpellModOp::ProcChance on the aura caster's mod owner"],
    }
    if model == CHANCE_CLASSIC_PPM:
        out["external_inputs"].append("SpellModOp::ProcFrequency (inside Unit::GetPPMProcChance)")
        out["external_inputs"].append("caster base attack time for the event's attack type")
    if model == CHANCE_RPPM:
        out["external_inputs"].append("per-Aura m_lastProcAttemptTime / m_lastProcSuccessTime")
        out["external_inputs"].append("Aura cast item level (SPELL_PPM_MOD_ITEM_LEVEL)")
    return out


@dataclass
class EffectRole:
    index: int
    effect: int
    effect_name: str
    aura: int
    aura_name: str
    is_aura: bool
    trigger_aura: bool
    disabled: bool
    check_gate: str | None
    handler: str | None
    trigger_spell: int
    trigger_spell_exists: bool | None
    trigger_spell_adds_extra_attacks: bool
    script_bindings: list[str]

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


@dataclass
class ProcDefinition:
    spell_id: int
    difficulty: int
    name: str
    entry: ProcEntry | None
    status: str
    generation_reason: str | None
    load_log: list[str]
    unknown_proc_flag_bits: int
    effects: list[EffectRole]
    chance: dict[str, Any] | None
    provider_attributes: list[dict[str, str]]
    unconsumed_attributes: list[str]
    scripts: dict[str, Any] | None
    conditions: list[dict[str, Any]]
    corrections: list[str]
    external_inputs: list[str]
    equipped_requirement: dict[str, Any] | None
    info: SpellInfo = field(repr=False)

    @property
    def triggered_spells(self) -> list[int]:
        return [e.trigger_spell for e in self.effects
                if e.handler and e.aura in TRIGGER_SPELL_HANDLERS and not e.disabled and e.trigger_spell]

    @property
    def proccing_effects(self) -> list[EffectRole]:
        """Aura effects that can be in ``procEffectMask`` (before CheckEffectProc/scripts)."""
        return [e for e in self.effects if e.is_aura and not e.disabled]

    def to_dict(self) -> dict[str, Any]:
        return {
            "spell_id": self.spell_id,
            "difficulty": self.difficulty,
            "name": self.name,
            "status": self.status,
            "generation_reason": self.generation_reason,
            "entry": self.entry.to_dict() if self.entry else None,
            "unknown_proc_flag_bits": f"0x{self.unknown_proc_flag_bits:016X}" if self.unknown_proc_flag_bits else None,
            "load_log": self.load_log,
            "effects": [e.to_dict() for e in self.effects],
            "triggered_spells": self.triggered_spells,
            "chance": self.chance,
            "provider_attributes": self.provider_attributes,
            "unconsumed_attributes": self.unconsumed_attributes,
            "scripts": self.scripts,
            "conditions": self.conditions,
            "trinity_code_corrections": self.corrections,
            "equipped_requirement": self.equipped_requirement,
            "external_inputs": self.external_inputs,
            "provenance": self.info.provenance,
        }


class ProcDefinitions:
    """Facade: catalog + overlay + entry store -> :class:`ProcDefinition`."""

    def __init__(self, catalog: SpellCatalog, overlay: TrinityOverlay | None) -> None:
        self.catalog = catalog
        self.overlay = overlay
        if overlay is not None:
            overlay.bind_ranks(catalog)
        self.store = ProcEntryStore(catalog, overlay)
        self._cache: dict[tuple[int, int], ProcDefinition | None] = {}

    def get(self, spell_id: int, difficulty: int = DIFFICULTY_NONE) -> ProcDefinition | None:
        key = (spell_id, difficulty)
        if key in self._cache:
            return self._cache[key]
        info = self.catalog.get(spell_id, difficulty)
        if info is None:
            self._cache[key] = None
            return None
        definition = self._describe(info)
        self._cache[key] = definition
        return definition

    def _describe(self, info: SpellInfo) -> ProcDefinition:
        entry = self.store.lookup(info.id, info.difficulty)
        gen = self.store.generation(info.id, info.difficulty)
        log = list(self.store.db_log.get(info.id, [])) + list(gen.log)
        if entry is None:
            status = "no-entry"
        elif entry.origin == "spell_proc":
            status = "spell_proc"
        elif entry.keyed_difficulty != info.difficulty:
            status = f"generated-via-difficulty-{entry.keyed_difficulty}"
        else:
            status = "generated"

        flags = entry.proc_flags if entry else info.proc_flags
        unknown = flags & ~KNOWN_PROC_FLAGS

        scripts = self.overlay.script_hooks(info.id) if self.overlay else None
        bindings_by_index: dict[int, list[str]] = {}
        if scripts:
            for binding in scripts["effect_proc_bindings"]:
                idx, _, aura_token = binding.partition(":")
                index = -1
                if idx.startswith("EFFECT_") and idx[7:].isdigit():
                    index = int(idx[7:])
                elif idx == "EFFECT_ALL":
                    index = -1
                bindings_by_index.setdefault(index, []).append(binding)

        roles: list[EffectRole] = []
        disable = entry.disable_effects_mask if entry else 0
        for eff in info.effects:
            trig = eff.trigger_spell
            trig_info = self.catalog.get(trig, info.difficulty) if trig else None
            handler = HANDLE_PROC_ACTIONS.get(eff.aura) if eff.is_aura else None
            if eff.is_aura and eff.aura == SPELL_AURA_DUMMY and not trig:
                handler = "none: DUMMY with no EffectTriggerSpell (warning logged, no action)"
            roles.append(EffectRole(
                index=eff.index, effect=eff.effect, effect_name=effect_name(eff.effect),
                aura=eff.aura, aura_name=aura_name(eff.aura), is_aura=eff.is_aura,
                trigger_aura=eff.is_effect and eff.aura in TRIGGER_AURAS,
                disabled=bool(disable & (1 << eff.index)),
                check_gate=CHECK_EFFECT_PROC_GATES.get(eff.aura) if eff.is_aura else None,
                handler=handler,
                trigger_spell=trig,
                trigger_spell_exists=(trig_info is not None) if trig else None,
                trigger_spell_adds_extra_attacks=bool(trig_info and trig_info.has_effect(SPELL_EFFECT_ADD_EXTRA_ATTACKS)),
                script_bindings=bindings_by_index.get(eff.index, []) + bindings_by_index.get(-1, []),
            ))

        provider_attrs = []
        for key, (side, role, consumer) in PROC_ATTRIBUTE_CONSUMERS.items():
            if side == "provider" and info.has_attr(key):
                provider_attrs.append({"attribute": attr_name(key), "role": role, "consumer": consumer})
        unconsumed = [attr_name(k) for k in UNCONSUMED_PROC_NAMED_ATTRIBUTES if info.has_attr(k)]

        external = [
            "aura application effect mask (area auras may apply a subset of effects)",
            "SpellModOp::ProcChance / ProcCharges / ProcCooldown from the caster's spell mods",
        ]
        conditions = self.overlay.conditions.get(info.id, []) if self.overlay else []
        if conditions:
            external.append("conditions (CONDITION_SOURCE_TYPE_SPELL_PROC) against actor/action target")
        if entry and entry.attributes_mask & PROC_ATTR_REQ_EXP_OR_HONOR:
            external.append("Player::isHonorOrXPTarget(action target)")
        if entry and entry.attributes_mask & PROC_ATTR_REQ_SPELLMOD:
            external.append("event Spell::m_appliedMods contains this aura (if charges/stack-charges)")
        if scripts and scripts["proc_hooks"]:
            external.append("AuraScript proc hooks: " + ", ".join(scripts["proc_hooks"]))

        equipped = None
        if info.is_passive and info.equipped_item_class != -1:
            equipped = {
                "equipped_item_class": info.equipped_item_class,
                "subclass_mask": info.equipped_item_subclass_mask,
                "inventory_type_mask": info.equipped_item_inventory_type_mask,
                "skipped_by_SPELL_ATTR3_NO_PROC_EQUIP_REQUIREMENT": info.has_attr(
                    ATTR3_NO_PROC_EQUIP_REQUIREMENT),
                "consumer": "Aura::GetProcEffectMask (passive + player target only)",
            }
            if not equipped["skipped_by_SPELL_ATTR3_NO_PROC_EQUIP_REQUIREMENT"]:
                external.append("equipped weapon/shield state (Item::IsFitToSpellRequirements, feral form, broken)")

        return ProcDefinition(
            spell_id=info.id, difficulty=info.difficulty, name=info.name,
            entry=entry, status=status,
            generation_reason=gen.reason if entry is None or entry.origin != "spell_proc" else None,
            load_log=log, unknown_proc_flag_bits=unknown, effects=roles,
            chance=chance_model(entry, info) if entry else None,
            provider_attributes=provider_attrs, unconsumed_attributes=unconsumed,
            scripts=scripts, conditions=conditions,
            corrections=self.overlay.correction_members(info.id) if self.overlay else [],
            external_inputs=external, equipped_requirement=equipped, info=info,
        )

