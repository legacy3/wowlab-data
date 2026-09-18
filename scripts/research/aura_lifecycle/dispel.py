"""Track E -- dispel, mechanic dispel and spell steal as aura-removal paths.

Runtime model (explicit fixtures, no DB2) mirroring pinned TrinityCore:

* :func:`dispellable_list`   -- ``Unit::GetDispellableAuraList`` (Unit.cpp:4735-4774)
* :func:`effect_dispel`      -- the attempt loop of ``Spell::EffectDispel`` (SpellEffects.cpp:2133-2230)
* :func:`apply_dispel`       -- ``Unit::RemoveAurasDueToSpellByDispel`` (Unit.cpp:4006-4031) +
                                ``Aura::ModCharges`` / ``Aura::ModStackAmount`` removal branches
* :func:`mechanic_dispel`    -- ``Spell::EffectDispelMechanic`` (SpellEffects.cpp:4236-4265)
* :func:`steal_candidates`   -- the candidate filter of ``Spell::EffectStealBeneficialBuff`` (SpellEffects.cpp:4722-4770)

RNG is modelled at the *call* level (``urand`` / ``irand``); the number
of engine words each call consumes is a libstdc++ property measured by the
probe (``tools/tc_aura_removal_probe``), not assumed here.

Data side (:func:`explain`, :func:`census`) reads DispelType / Mechanic /
attributes of providers and the raw-38/108/126 dispeller effects.

Core negative witness (navigation only): Core's raw-38 and raw-108 compilers
refuse any dispellable aura with periodic fanout (``crates/combat/src/program/dispel.rs:244-248``,
``dispel_mechanic.rs:387-395``).  That refusal is preserved as a boundary, not
read as missing semantics: Trinity removes the whole periodic aura with no
final tick (or lowers stacks without touching the periodic timer).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from . import FailClosed
from .removal import attrs_of, has

# DispelType (SharedDefines.h:2920-2936)
DISPEL_TYPES = {0: "NONE", 1: "MAGIC", 2: "CURSE", 3: "DISEASE", 4: "POISON", 5: "STEALTH", 6: "INVISIBILITY",
                7: "ALL", 8: "SPE_NPC_ONLY", 9: "ENRAGE", 10: "ZG_TICKET", 11: "OLD_UNUSED"}
DISPEL_ALL = 7
# SharedDefines.h:2938
DISPEL_ALL_MASK = (1 << 1) | (1 << 2) | (1 << 3) | (1 << 4)

EFFECT_DISPEL, EFFECT_DISPEL_MECHANIC, EFFECT_STEAL = 38, 108, 126
# AuraEffect::CalculatePeriodic periodic aura types (SpellAuraEffects.cpp:968-982); raw 70 is NOT listed.
PERIODIC_AURAS = frozenset({21, 3, 8, 20, 23, 48, 24, 53, 62, 64, 89, 162, 226, 227})


def dispel_mask(dispel_type: int) -> int:
    """Mirrors: SpellInfo.cpp:2695 ``SpellInfo::GetDispelMask(DispelType)``.

    ``1 << type`` for every type except ALL (7), which is Magic|Curse|Disease|Poison
    -- Enrage, Stealth and Invisibility are *not* in the ALL mask.
    """
    if dispel_type == DISPEL_ALL:
        return DISPEL_ALL_MASK
    if not 0 <= dispel_type < 32:
        raise FailClosed(f"dispel type {dispel_type} outside uint32 shift range")
    return 1 << dispel_type


# ---------------------------------------------------------------------------
# Runtime fixtures
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DAura:
    """One aura owned by the dispel target (fields are what the consumers read)."""
    key: str                      # fixture handle
    spell: int
    caster: str                   # caster GUID stand-in
    dispel_type: int = 1
    positive: bool = False        # AuraApplication::IsPositive on the owner
    passive: bool = False
    applied_on_owner: bool = True  # GetApplicationOfTarget(owner) != nullptr
    stacks: int = 1
    charges: int = 0
    max_stacks: int = 0           # SpellInfo::StackAmount (0 = non-stacking)
    dispel_removes_charges: bool = False   # ATTR7_DISPEL_REMOVES_CHARGES
    dispel_all_stacks: bool = False        # ATTR1_DISPEL_ALL_STACKS
    cannot_be_stolen: bool = False         # ATTR4_CANNOT_BE_STOLEN
    resist_pct: int = 0           # caster SpellModOp::DispelResistance result (before clamp)
    caster_present: bool = True   # Aura::GetCaster() != nullptr
    mechanic_mask: int = 0        # SpellInfo::GetAllEffectsMechanicMask
    periodic: bool = False


def dispel_chance(aura: DAura) -> int:
    """Mirrors: SpellAuras.cpp:1237-1249 ``Aura::CalcDispelChance`` (100 - clamp(resist, 0, 100));
    the resistance mod is only applied when the caster is present."""
    resist = aura.resist_pct if aura.caster_present else 0
    return 100 - max(0, min(100, resist))


@dataclass
class Candidate:
    aura: DAura
    chance: int
    charges: int


def dispellable_list(owned: list[DAura], mask: int, target_friendly_to_dispeller: bool,
                     reflect: bool = False) -> list[Candidate]:
    """Mirrors: Unit.cpp:4735-4774 ``Unit::GetDispellableAuraList``.

    Order = owned-aura multimap order (spell id, then insertion); callers pass
    ``owned`` already in that order.
    """
    out = []
    for a in owned:
        if not a.applied_on_owner:
            continue
        if a.passive:
            continue
        if dispel_mask(a.dispel_type) & mask:
            if reflect != (a.positive == target_friendly_to_dispeller):
                continue
            chance = dispel_chance(a)
            if not chance:
                continue
            charges = a.charges if a.dispel_removes_charges else a.stacks
            if charges > 0:
                out.append(Candidate(a, chance, charges))
    return out


class ScriptedRng:
    """Call-level RNG: returns scripted values and records every call.

    ``urand(lo, hi)`` / ``irand(lo, hi)`` consume the next int (must lie in
    [lo, hi]); ``rand_chance()`` the next float in [0, 100).
    """

    def __init__(self, ints: list[int] | None = None, floats: list[float] | None = None) -> None:
        self.ints = list(ints or [])
        self.floats = list(floats or [])
        self.calls: list[tuple[str, Any]] = []

    def urand(self, lo: int, hi: int) -> int:
        if not self.ints:
            raise FailClosed("scripted rng exhausted (urand)")
        v = self.ints.pop(0)
        if not lo <= v <= hi:
            raise FailClosed(f"scripted urand value {v} outside [{lo}, {hi}]")
        self.calls.append(("urand", (lo, hi, v)))
        return v

    def irand(self, lo: int, hi: int) -> int:
        if not self.ints:
            raise FailClosed("scripted rng exhausted (irand)")
        v = self.ints.pop(0)
        if not lo <= v <= hi:
            raise FailClosed(f"scripted irand value {v} outside [{lo}, {hi}]")
        self.calls.append(("irand", (lo, hi, v)))
        return v

    def rand_chance(self) -> float:
        if not self.floats:
            raise FailClosed("scripted rng exhausted (rand_chance)")
        v = self.floats.pop(0)
        self.calls.append(("rand_chance", v))
        return v


def roll_chance(rng: ScriptedRng, chance: int | float) -> bool:
    """Mirrors: Random.h:54-72 ``roll_chance`` overload set.

    ``std::floating_point`` -> ``chance > rand_chance()``; ``std::signed_integral``
    -> ``chance > irand(0, 99)``.  Dispel chances are ``int32``
    (``DispelableAura::_chance``, ``Aura::CalcDispelChance``), so every dispel
    roll is an integer ``irand(0, 99)`` draw -- consumed even at 100 %.
    """
    if isinstance(chance, bool):
        raise FailClosed("roll_chance: bool chance")
    if isinstance(chance, int):
        return chance > rng.irand(0, 99)
    return chance > rng.rand_chance()


@dataclass
class DispelResult:
    attempts: list[dict[str, Any]] = field(default_factory=list)
    success: list[tuple[str, int]] = field(default_factory=list)   # (aura key, charges) grouped
    failed_spells: list[int] = field(default_factory=list)


def effect_dispel(candidates: list[Candidate], amount: int, rng: ScriptedRng) -> DispelResult:
    """Mirrors: SpellEffects.cpp:2162-2194 (the ``for (count < dispelAmount && remaining > 0)`` loop).

    Every attempt consumes one ``urand(0, remaining-1)`` and one ``irand(0, 99)``;
    a failed roll still counts as an attempt and leaves the candidate selectable.
    Successful removals are grouped by (spell id, caster *pointer*): two auras of
    the same spell whose casters are both absent collapse into one group
    (``GetCaster()`` is nullptr for both) -- see AL-D-E-02.
    """
    lst = [Candidate(c.aura, c.chance, c.charges) for c in candidates]
    remaining = len(lst)
    res = DispelResult()
    groups: list[list[Any]] = []     # [aura, charges]
    count = 0
    while count < amount and remaining > 0:
        idx = rng.urand(0, remaining - 1)
        cand = lst[idx]
        if roll_chance(rng, cand.chance):
            a = cand.aura
            dispelled = cand.charges if a.dispel_all_stacks else 1

            def same(g) -> bool:
                ga = g[0]
                caster_ptr_eq = (ga.caster == a.caster and ga.caster_present and a.caster_present) or \
                    (not ga.caster_present and not a.caster_present)
                return ga.spell == a.spell and caster_ptr_eq
            hit = next((g for g in groups if same(g)), None)
            if hit is None:
                groups.append([a, dispelled])
            else:
                hit[1] += 1          # IncrementCharges(): +1 regardless of dispelled (Unit.h:136)
            # DispelableAura::DecrementCharge (Unit.h:137-144), uint8 arithmetic
            if cand.charges == 0:
                keep = False
            else:
                cand.charges = (cand.charges - dispelled) & 0xFF
                keep = cand.charges > 0
            res.attempts.append({"index": idx, "aura": a.key, "success": True, "charges_taken": dispelled})
            if not keep:
                remaining -= 1
                lst[idx], lst[remaining] = lst[remaining], lst[idx]
        else:
            res.failed_spells.append(cand.aura.spell)
            res.attempts.append({"index": idx, "aura": cand.aura.key, "success": False})
        count += 1
    res.success = [(g[0].key, g[1]) for g in groups]
    return res


@dataclass
class AuraState:
    """Mutable state of one aura for the removal step."""
    aura: DAura
    stacks: int
    charges: int
    removed: str | None = None
    refreshed: bool = False
    log: list[str] = field(default_factory=list)


def apply_dispel(state: AuraState, removed_charges: int) -> AuraState:
    """Mirrors: Unit.cpp:4006-4031 ``RemoveAurasDueToSpellByDispel`` with
    SpellAuras.cpp:1017-1036 ``ModCharges`` and 1093-1128 ``ModStackAmount`` (num < 0).

    Order: OnDispel -> ModCharges/ModStackAmount(-n, ENEMY_SPELL) -> AfterDispel.
    Charges path: only when IsUsingCharges (charges != 0) -- otherwise nothing
    happens at all.  Stack path: <= 0 removes; a decrease never refreshes duration
    (``refresh = stackAmount >= GetStackAmount()``) and never resets the periodic timer.
    """
    s = AuraState(state.aura, state.stacks, state.charges, state.removed, state.refreshed, list(state.log))
    s.log.append("OnDispel")
    if s.aura.dispel_removes_charges:
        if s.charges != 0:
            c = s.charges - removed_charges
            if c <= 0:
                s.removed = "ENEMY_SPELL"
                s.log.append("Remove(ENEMY_SPELL)")
            else:
                s.charges = c
                s.log.append(f"SetCharges({c})")
        else:
            s.log.append("ModCharges no-op (IsUsingCharges false)")
    else:
        n = s.stacks - removed_charges
        if n <= 0:
            s.removed = "ENEMY_SPELL"
            s.log.append("Remove(ENEMY_SPELL)")
        else:
            s.refreshed = n >= s.stacks and bool(s.aura.max_stacks)
            s.stacks = n
            s.log.append(f"SetStackAmount({n})" + (" +RefreshTimers" if s.refreshed else ""))
    s.log.append("AfterDispel")
    return s


def mechanic_dispel(owned: list[DAura], mechanic: int, rng: ScriptedRng) -> list[str]:
    """Mirrors: SpellEffects.cpp:4246-4262 ``EffectDispelMechanic``.

    For every owned aura *applied on the owner* (passive and positive ones
    included) the dispel chance is rolled first; only then is the mechanic mask
    tested.  Returns the keys removed (whole aura, ENEMY_SPELL, no dispel hooks).
    """
    out = []
    for a in owned:
        if not a.applied_on_owner:
            continue
        if roll_chance(rng, dispel_chance(a)):
            if a.mechanic_mask & (1 << mechanic):
                out.append(a.key)
    return out


def steal_candidates(owned: list[DAura], mask: int) -> list[Candidate]:
    """Mirrors: SpellEffects.cpp:4735-4766 (EffectStealBeneficialBuff candidate filter).

    Unlike dispel: requires a *positive* application regardless of relation,
    rejects ATTR4_CANNOT_BE_STOLEN; no friendly/reflect logic.
    """
    out = []
    for a in owned:
        if not a.applied_on_owner:
            continue
        if dispel_mask(a.dispel_type) & mask:
            if not a.positive or a.passive or a.cannot_be_stolen:
                continue
            chance = dispel_chance(a)
            if not chance:
                continue
            charges = a.charges if a.dispel_removes_charges else a.stacks
            if charges > 0:
                out.append(Candidate(a, chance, charges))
    return out


# ---------------------------------------------------------------------------
# Data side
# ---------------------------------------------------------------------------

def dispeller_effects(data, spell: int) -> list[dict[str, Any]]:
    out = []
    for e in data.effects(spell):
        eff = int(e["Effect"])
        if eff in (EFFECT_DISPEL, EFFECT_DISPEL_MECHANIC, EFFECT_STEAL):
            misc = int(e["EffectMiscValue_0"])
            row = {"index": int(e["EffectIndex"]), "effect": eff,
                   "kind": {38: "dispel", 108: "dispel_mechanic", 126: "steal"}[eff],
                   "misc": misc, "base_points": e["EffectBasePointsF"]}
            if eff != EFFECT_DISPEL_MECHANIC:
                row["dispel_mask"] = dispel_mask(misc) if 0 <= misc < 32 else None
                row["types_covered"] = [DISPEL_TYPES.get(t, str(t)) for t in range(12)
                                        if row["dispel_mask"] and row["dispel_mask"] & (1 << t)]
            out.append(row)
    return out


def explain(ctx, spell: int) -> dict[str, Any]:
    """``aura_lifecycle.py dispel <spell>``: the spell as a dispellable aura and/or as a dispeller."""
    data = ctx.data
    attrs = attrs_of(data, spell)
    effects = data.effects(spell)
    cats = data.row("SpellCategories", spell) or {}
    dtype = int(cats.get("DispelType", 0))
    auras = sorted({int(e["EffectAura"]) for e in effects if e["EffectAura"]})
    opts = data.row("SpellAuraOptions", spell) or {}
    out: dict[str, Any] = {"spell": spell, "name": ctx.name(spell), "build_skew": ctx.is_skew(spell),
                           "dispeller": dispeller_effects(data, spell)}
    if auras:
        dm = dispel_mask(dtype)
        removed_by = []
        for t in range(12):
            if t and dispel_mask(t) & dm:
                removed_by.append(DISPEL_TYPES[t])
        try:
            from targeting import positivity
            pos: Any = positivity.is_positive(spell)
        except (FailClosed, Exception) as exc:   # positivity oracle fails closed on unported shapes
            pos = f"unknown ({type(exc).__name__}: {exc})"
        periodic = sorted(set(auras) & PERIODIC_AURAS)
        charges = int(opts.get("ProcCharges", 0))
        out["aura"] = {
            "dispel_type": dtype, "dispel_type_name": DISPEL_TYPES.get(dtype, str(dtype)),
            "dispel_mask": dm if dtype else 0,
            "removed_by_dispeller_misc": removed_by if dtype else [],
            "passive_undispellable": has(attrs, "PASSIVE"),
            "spell_positive": pos,
            "unit_of_removal": "charge" if has(attrs, "DISPEL_REMOVES_CHARGES") else "stack",
            "dispel_all_stacks": has(attrs, "DISPEL_ALL_STACKS"),
            "cannot_be_stolen": has(attrs, "CANNOT_BE_STOLEN"),
            "authored_proc_charges": charges,
            "charges_mode_without_charges": has(attrs, "DISPEL_REMOVES_CHARGES") and charges == 0,
            "mechanic": int(cats.get("Mechanic", 0)),
            "effect_mechanics": sorted({int(e["EffectMechanic"]) for e in effects if e["EffectMechanic"]}),
            "periodic_aura_types": periodic,
            "periodic_on_dispel": ("whole-aura dispel removes it with ENEMY_SPELL, no final tick; a stack dispel "
                                   "recalculates amounts (SetStackAmount) and keeps the periodic timer") if periodic else None,
            "core_negative_witness": bool(periodic) and bool(dtype),
            "mode": "ENEMY_SPELL",
        }
    ev = ["db2-fact", "trinity-consumer"]
    out["evidence"] = ev
    if not auras and not out["dispeller"]:
        raise FailClosed(f"spell {spell}: neither an aura provider nor a dispeller")
    return out


def census(ctx, pops: dict[str, frozenset[int]]) -> dict[str, Any]:
    """Dispellable-provider and dispeller counts per population."""
    data = ctx.data
    effect_table = data.table("SpellEffect")
    dispellers_all: dict[int, list[dict[str, Any]]] = {}
    for (spell, diff), effs in effect_table.items():
        if diff != 0:
            continue
        rows = [e for e in effs.values() if int(e["Effect"]) in (EFFECT_DISPEL, EFFECT_DISPEL_MECHANIC, EFFECT_STEAL)]
        if rows:
            dispellers_all[spell] = rows
    out: dict[str, Any] = {}
    for pop, spells in sorted(pops.items()):
        c: Counter = Counter()
        for spell in sorted(spells):
            cats = data.row("SpellCategories", spell) or {}
            dtype = int(cats.get("DispelType", 0))
            if not dtype:
                continue
            try:
                attrs = attrs_of(data, spell)
            except FailClosed:
                c["dispellable_without_spellmisc"] += 1
                continue
            c[f"dispel_type:{DISPEL_TYPES.get(dtype, dtype)}"] += 1
            if has(attrs, "PASSIVE"):
                c["dispel_type_but_passive"] += 1
                continue
            c["dispellable_nonpassive"] += 1
            auras = {int(e["EffectAura"]) for e in data.effects(spell) if e["EffectAura"]}
            if auras & PERIODIC_AURAS:
                c["dispellable_with_periodic_effect"] += 1
            if has(attrs, "DISPEL_REMOVES_CHARGES"):
                c["dispel_removes_charges"] += 1
                opts = data.row("SpellAuraOptions", spell) or {}
                if not int(opts.get("ProcCharges", 0)):
                    c["dispel_removes_charges_but_no_authored_charges"] += 1
            if has(attrs, "DISPEL_ALL_STACKS"):
                c["dispel_all_stacks"] += 1
            if has(attrs, "CANNOT_BE_STOLEN"):
                c["cannot_be_stolen"] += 1
            if dtype in (5, 6, 9):
                c["type_outside_DISPEL_ALL_MASK"] += 1
        d: Counter = Counter()
        reach = _reach(ctx, pop)
        for spell, rows in sorted(dispellers_all.items()):
            if reach is not None and spell not in reach:
                continue
            for e in rows:
                eff = int(e["Effect"])
                kind = {38: "dispel", 108: "dispel_mechanic", 126: "steal"}[eff]
                misc = int(e["EffectMiscValue_0"])
                d[f"{kind}:misc{misc}"] += 1
                d[f"{kind}:effects"] += 1
        out[pop] = {"population": pop, "provider_spells": len(spells), "dispellable": dict(sorted(c.items())),
                    "dispeller_effects": dict(sorted(d.items()))}
    return out


def _reach(ctx, pop: str) -> frozenset[int] | None:
    """Dispellers are not necessarily aura providers: the dispeller population is
    every DIFFICULTY_NONE spell (``all``) or the population's reach set."""
    if pop == "player":
        return frozenset(ctx.scope.reach)
    if pop == "controlled":
        from .providers import controlled_spells
        return controlled_spells()
    return None


__all__ = ["AuraState", "Candidate", "DAura", "DISPEL_ALL_MASK", "DISPEL_TYPES", "ScriptedRng", "apply_dispel",
           "census", "dispel_chance", "dispel_mask", "dispellable_list", "effect_dispel", "explain",
           "mechanic_dispel", "steal_candidates"]
