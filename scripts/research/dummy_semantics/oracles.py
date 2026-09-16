"""Pure research evaluators for proved server-side families.

Each oracle takes immutable policy + explicit facts and returns *semantic action
declarations*.  No RNG is drawn, no combat is simulated, nothing is mutated.
Every function names the TrinityCore consumer it mirrors.  Anything outside the
mirrored branch raises :class:`FailClosed`.
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass, field
from typing import Any

from . import FailClosed
from .loaders import SPELL_LINK_AURA, SPELL_LINK_CAST, SPELL_LINK_HIT, SPELL_LINK_REMOVE


# ---------------------------------------------------------------------------
# arithmetic helpers (Util.h)
# ---------------------------------------------------------------------------

def f32(x: float) -> float:
    return struct.unpack("f", struct.pack("f", x))[0]


def calculate_pct(base: int | float, pct: int | float, result_type: type = int) -> int | float:
    """Mirrors: ``CalculatePct<T,U>(base, pct) = T(base * static_cast<float>(pct) / 100.0f)``.

    ``base * float(pct)`` promotes to float (binary32) when ``base`` is an
    integer type; the division by ``100.0f`` stays in binary32; ``T(...)``
    truncates toward zero for integer ``T``.
    """
    prod = f32(f32(float(base)) * f32(float(pct))) if isinstance(base, int) else f32(base * f32(float(pct)))
    q = f32(prod / f32(100.0))
    if result_type is int:
        return int(q)  # truncation toward zero, as C++ float -> integer conversion
    return q


def add_pct(base: int | float, pct: int | float) -> int | float:
    """Mirrors: ``AddPct(base, pct) = base += CalculatePct(base, pct)``."""
    return base + calculate_pct(base, pct, type(base))


def apply_pct(base: int | float, pct: int | float) -> int | float:
    """Mirrors: ``ApplyPct(base, pct) = base = CalculatePct(base, pct)``."""
    return calculate_pct(base, pct, type(base))


def compare_values(comp: int, value: float, ref: float) -> bool:
    """Mirrors: ``CompareValues`` (ComparisionType EQ/HIGH/LOW/HIGH_EQ/LOW_EQ)."""
    if comp == 0:
        return value == ref
    if comp == 1:
        return value > ref
    if comp == 2:
        return value < ref
    if comp == 3:
        return value >= ref
    if comp == 4:
        return value <= ref
    raise FailClosed(f"ComparisionType {comp} unknown")


# ---------------------------------------------------------------------------
# action declarations
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Action:
    kind: str                    # cast | remove-aura | apply-immunity | add-aura | mod-stacks | pet-aura | none
    spell: int | None = None
    target: str | None = None    # "unit" | "caster" | "target" | "pet" | "hit-unit"
    caster: str | None = None
    base_points: tuple[float, ...] | None = None
    trigger_flags: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if v not in (None, {}, ())}


# ---------------------------------------------------------------------------
# generic engine consumers whose whole behaviour is decided by data
# ---------------------------------------------------------------------------

def trigger_spell_with_value(child_spell: int, child_effect_count: int, effect_value: float, with_value: bool,
                             delay_ms: int = 0, caster_kind: str = "caster") -> Action:
    """Mirrors: ``Spell::EffectTriggerSpell`` tail (TRIGGER_SPELL / TRIGGER_SPELL_WITH_VALUE).

    WITH_VALUE sets ``SPELLVALUE_BASE_POINT{i}`` = effect value for *every* effect
    of the child.  Plain TRIGGER_SPELL delays by ``EffectMiscValue`` ms.
    """
    if child_spell == 0:
        raise FailClosed("EffectTriggerSpell: TriggerSpell is 0 -- handler logs and returns (no action)")
    bp = tuple(float(effect_value) for _ in range(child_effect_count)) if with_value else None
    return Action("cast", child_spell, target="unit-or-explicit", caster=caster_kind, base_points=bp,
                  trigger_flags="TRIGGERED_FULL_MASK & ~(IGNORE_POWER_COST | IGNORE_REAGENT_COST)",
                  detail={"delay_ms": delay_ms if not with_value else 0, "scheduled": True})


def proc_trigger_spell(child_spell: int, child_effect_count: int, aura_amount: float | None, with_value: bool,
                       triggered_by_caster: bool) -> Action:
    """Mirrors: ``AuraEffect::HandleProcTriggerSpellAuraProc`` / ``...WithValueAuraProc``:
    caster = aura caster if ``NeedsToBeTriggeredByCaster`` else the aura target; target = proc target."""
    if child_spell == 0:
        raise FailClosed("HandleProcTriggerSpellAuraProc: TriggerSpell 0 -- logs and returns")
    bp = tuple(float(aura_amount) for _ in range(child_effect_count)) if with_value else None
    return Action("cast", child_spell, target="proc-target", caster="aura-caster" if triggered_by_caster else "aura-target",
                  base_points=bp, trigger_flags="TRIGGERED_FULL_MASK & ~(IGNORE_POWER_COST | IGNORE_REAGENT_COST)")


def periodic_trigger_spell(child_spell: int, child_effect_count: int, aura_amount: float | None, with_value: bool,
                           triggered_by_caster: bool) -> Action:
    """Mirrors: ``AuraEffect::HandlePeriodicTriggerSpellAuraTick`` / ``...WithValueAuraTick``."""
    if child_spell == 0:
        raise FailClosed("HandlePeriodicTriggerSpellAuraTick: TriggerSpell 0 -- logs and returns")
    bp = tuple(float(aura_amount) for _ in range(child_effect_count)) if with_value else None
    return Action("cast", child_spell, target="aura-target", caster="aura-caster" if triggered_by_caster else "aura-target",
                  base_points=bp, trigger_flags="TRIGGERED_FULL_MASK & ~(IGNORE_POWER_COST | IGNORE_REAGENT_COST)")


def aura_linked(child_spell: int, aura_amount: float, apply: bool, reapply: bool, triggered_by_caster: bool,
                own_stack: int = 1, existing_child_stack: int | None = None) -> list[Action]:
    """Mirrors: ``AuraEffect::HandleAuraLinked`` (SPELL_AURA_LINKED / LINKED_2)."""
    if child_spell == 0:
        return []
    caster = "aura-caster" if triggered_by_caster else "aura-target"
    if reapply and apply:
        if existing_child_stack is None:
            return []
        return [Action("mod-stacks", child_spell, target="aura-target", detail={"delta": own_stack - existing_child_stack})]
    if apply:
        bp = (float(aura_amount),) if aura_amount else None
        return [Action("cast", child_spell, target="aura-target", caster=caster, base_points=bp, detail={"SPELLVALUE_BASE_POINT0_only": bool(bp)})]
    return [Action("remove-aura", child_spell, target="aura-target", detail={"caster_guid": caster})]


def linked_spell_actions(linked: dict[tuple[int, int], list[int]], spell_id: int, event: str, apply: bool | None = None,
                         reapply: bool = False, remove_mode_death: bool = False) -> list[Action]:
    """Mirrors the four ``spell_linked_spell`` consumers.

    ``event``: ``cast`` (Spell::finish tail, after AfterCast), ``hit`` (Spell::DoTriggersOnSpellHit),
    ``aura`` (Aura::HandleAuraSpecificMods apply/remove/reapply).  Negative ids remove
    (CAST/HIT/REMOVE) or add an IMMUNITY_ID (AURA type).
    """
    out: list[Action] = []
    if event == "cast":
        for e in linked.get((SPELL_LINK_CAST, spell_id), []):
            out.append(Action("remove-aura", -e, target="caster") if e < 0 else
                       Action("cast", e, target="explicit-unit-or-caster", caster="caster", trigger_flags="TRIGGERED_FULL_MASK"))
    elif event == "hit":
        for e in linked.get((SPELL_LINK_HIT, spell_id), []):
            out.append(Action("remove-aura", -e, target="hit-unit") if e < 0 else
                       Action("cast", e, target="hit-unit", caster="hit-unit", trigger_flags="TRIGGERED_FULL_MASK",
                              detail={"original_caster": "spell caster"}))
    elif event == "aura":
        if apply is None:
            raise FailClosed("aura event needs apply flag")
        if not reapply:
            if apply:
                for e in linked.get((SPELL_LINK_AURA, spell_id), []):
                    out.append(Action("apply-immunity", -e, target="aura-target") if e < 0 else
                               Action("add-aura", e, target="aura-target", caster="aura-caster"))
            else:
                for e in linked.get((SPELL_LINK_REMOVE, spell_id), []):
                    if e < 0:
                        out.append(Action("remove-aura", -e, target="aura-target"))
                    elif not remove_mode_death:
                        out.append(Action("cast", e, target="aura-target", caster="aura-target", trigger_flags="TRIGGERED_FULL_MASK"))
                for e in linked.get((SPELL_LINK_AURA, spell_id), []):
                    out.append(Action("remove-immunity", -e, target="aura-target") if e < 0 else
                               Action("remove-aura", e, target="aura-target", detail={"caster_guid": "aura-caster"}))
        elif apply:
            for e in linked.get((SPELL_LINK_AURA, spell_id), []):
                if e > 0:
                    out.append(Action("mod-stacks", e, target="aura-target", detail={"to": "own stack amount"}))
    else:
        raise FailClosed(f"unknown linked event {event}")
    return out


def pet_aura(row_aura: int, row_pet: int, pet_entry: int, all_pets_aura: int | None, damage: int) -> Action | None:
    """Mirrors: ``Pet::CastPetAura``: ``auraId = GetAura(GetEntry())`` (entry-specific row, else the
    ``pet = 0`` row), cast on self with ``SPELLVALUE_BASE_POINT0 = damage`` when the PetAura has damage."""
    aura_id = row_aura if row_pet == pet_entry else (all_pets_aura or 0)
    if not aura_id:
        return None
    return Action("cast", aura_id, target="pet", caster="pet", base_points=(float(damage),) if damage else None,
                  trigger_flags="TRIGGERED_FULL_MASK")


@dataclass(frozen=True)
class SpellAreaRow:
    spell: int
    area: int
    quest_start: int
    quest_end: int
    aura_spell: int
    race_mask: int
    gender: int
    flags: int
    quest_start_status: int
    quest_end_status: int


def spell_area_fits(row: SpellAreaRow, player: dict[str, Any] | None, zone: int, area: int) -> bool:
    """Mirrors: ``SpellArea::IsFitToRequirements`` generic part (the hardcoded id switch raises)."""
    if row.spell in (91604, 56618, 56617, 57940, 58045, 74411):
        raise FailClosed(f"spell_area row for {row.spell} has a hardcoded Battlefield branch")
    if row.gender != 2 and (player is None or row.gender != player.get("gender")):
        return False
    if row.race_mask and (player is None or not (row.race_mask & (1 << (player.get("race", 0) - 1)))):
        return False
    if row.area and zone != row.area and area != row.area:
        return False
    if row.quest_start and (player is None or not ((1 << player.get("quest_status", {}).get(row.quest_start, 0)) & row.quest_start_status)):
        return False
    if row.quest_end and (player is None or not ((1 << player.get("quest_status", {}).get(row.quest_end, 0)) & row.quest_end_status)):
        return False
    if row.aura_spell:
        has = set(player.get("auras", ())) if player else set()
        if player is None or (row.aura_spell > 0 and row.aura_spell not in has) or (row.aura_spell < 0 and -row.aura_spell in has):
            return False
    return True


# ---------------------------------------------------------------------------
# script families (verified witnesses in the report)
# ---------------------------------------------------------------------------

def count_pct_from_max_hp(max_health: int, pct: int) -> int:
    """Mirrors: ``spell_gen_count_pct_from_max_hp::RecalculateDamage``:
    ``SetHitDamage(GetHitUnit()->CountPctFromMaxHealth(_damagePct))``; ``CountPctFromMaxHealth``
    is ``CalculatePct(GetMaxHealth(), pct)`` with uint64 health -> ``uint64(float)``."""
    return calculate_pct(max_health, pct, int)


def forward_amount_as_basepoints(child_spell: int, amount: int, effect_indices: tuple[int, ...] = (0,), target: str = "proc-target",
                                 caster: str = "actor") -> Action:
    """The ``cast-child-with-amount`` boundary: ``CastSpellExtraArgs args(...); args.AddSpellMod(SPELLVALUE_BASE_POINT<i>, amount);
    caster->CastSpell(target, child, args)``.  Purely declarative."""
    bp = tuple(float(amount) if i in effect_indices else math.nan for i in range(max(effect_indices) + 1))
    return Action("cast", child_spell, target=target, caster=caster, base_points=bp,
                  detail={"set_effects": list(effect_indices)})


def damage_to_heal_pct(damage: int, pct: int) -> int:
    """``CalculatePct(damage, pct)`` as used by damage-copy families (int32 damage)."""
    return calculate_pct(damage, pct, int)
