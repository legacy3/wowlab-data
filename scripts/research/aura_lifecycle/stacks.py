"""Track C: aura stack state -- initial stacks, capacity, mutation, amount scaling.

Two halves:

* a **pure state model** of the stack/charge members of one ``Aura`` object
  (``m_stackAmount``, ``m_procCharges``, ``m_isUsingCharges``, duration pair) and
  the Trinity functions that mutate them, each with a ``Mirrors:`` line;
* a **census** over the shared provider denominator
  (:func:`aura_lifecycle.providers.populations`) that classifies every provider
  spell into a stack family and lists every *external* stack mutator (the
  MODIFY_AURA_STACKS effect, linked-aura stack sync, Doses / MaxAuraStacks spell
  modifiers, scripts).

Separation that the model keeps on purpose (never unified without evidence):

* aura **stacks** (``Aura::m_stackAmount``, uint8)                -- this module
* aura **proc charges** (``Aura::m_procCharges``, uint8)          -- :mod:`.charges`
* **spell charges** (category charges, ``SpellCategories.ChargeCategory``) --
  ``SpellHistory``; not aura state at all
* **independent stack objects** (several ``Aura`` of one SpellId) -- identity (track A)
* proc cooldown / RPPM clocks (``m_procCooldown``, ``m_lastProc*Time``) -- ``procs.state``

``SpellAuraOptions.CumulativeAura`` (``SpellInfo::StackAmount``) is a **capacity**
and a policy switch; it is never an initial stack count
(``Aura::Aura`` reads ``AuraCreateInfo::StackAmount``, default 1).
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field, replace
from typing import Any

from . import TC_ROOT, FailClosed

# ---------------------------------------------------------------------------
# Constants (pinned TrinityCore 7f3d43b)
# ---------------------------------------------------------------------------

#: DBCEnums.h:2407 SpellEffectAttributes::SuppressPointsStacking
EFFECT_ATTR_SUPPRESS_POINTS_STACKING = 0x00000040
#: DBCEnums.h:2410 SpellEffectAttributes::AuraPointsStack
EFFECT_ATTR_AURA_POINTS_STACK = 0x00000200
#: SharedDefines.h:1636
SPELL_EFFECT_MODIFY_AURA_STACKS = 289
#: SpellAuraDefines.h:371-372
SPELL_AURA_LINKED = 284
SPELL_AURA_LINKED_2 = 285
#: SpellAuraDefines.h:194-195, 305-306 (label variants carry the label in MiscValueB)
SPELLMOD_AURAS = {107: "flat", 108: "pct", 218: "label_pct", 219: "label_flat"}
#: SpellDefines.h SpellModOp
SPELLMOD_PROC_CHARGES = 4
SPELLMOD_DOSES = 31
SPELLMOD_MAX_AURA_STACKS = 37
SPELLMOD_OPS = {SPELLMOD_PROC_CHARGES: "ProcCharges", SPELLMOD_DOSES: "Doses",
                SPELLMOD_MAX_AURA_STACKS: "MaxAuraStacks"}
#: SpellInfo.cpp:1805 hardcoded multi-slot ids (Power Spark, Fel Flak Fire, Incanter's Absorption)
MULTISLOT_IDS = frozenset({55849, 40075, 44413})
#: SpellEffectInfo::MinValue / MaxValue (SpellInfo.h:266-267)
EFFECT_VALUE_MIN = -2000000000.0
EFFECT_VALUE_MAX = 2000000000.0
def _rounded_types() -> frozenset[int]:
    """Resolve the rounded aura set by Trinity name (no hand-typed numbers)."""
    from procs.enums import aura_name
    wanted = {"SPELL_AURA_PERIODIC_DAMAGE", "SPELL_AURA_PERIODIC_HEAL", "SPELL_AURA_PERIODIC_LEECH",
              "SPELL_AURA_PERIODIC_HEALTH_FUNNEL", "SPELL_AURA_PERIODIC_WEAPON_PERCENT_DAMAGE",
              "SPELL_AURA_DAMAGE_SHIELD", "SPELL_AURA_PROC_TRIGGER_DAMAGE", "SPELL_AURA_OBS_MOD_HEALTH",
              "SPELL_AURA_OBS_MOD_POWER", "SPELL_AURA_PERIODIC_ENERGIZE", "SPELL_AURA_PERIODIC_MANA_LEECH",
              "SPELL_AURA_PERIODIC_DAMAGE_PERCENT", "SPELL_AURA_POWER_BURN"}
    found = {a for a in range(700) if aura_name(a) in wanted}
    if len(found) != len(wanted):
        raise FailClosed("rounded aura set did not resolve by name")
    return frozenset(found)


#: SpellAuraEffects.cpp:848-864 aura types whose amount is std::round()ed after stack scaling
ROUNDED_AURA_TYPES = _rounded_types()

REMOVE_DEFAULT = "AURA_REMOVE_BY_DEFAULT"


# ---------------------------------------------------------------------------
# Static shape of one aura (what the mutators read from SpellInfo / mods)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AuraShape:
    """Inputs the stack/charge mutators read.  All explicit; no defaults are inferred.

    ``max_stacks`` is ``CalcMaxStackAmount()`` (capacity after the caster's
    MaxAuraStacks spell mods) and ``max_charges`` is ``CalcMaxCharges()``; both are
    *live* lookups in Trinity (evaluated at each call through ``GetCaster()``), so a
    timeline may change them between steps.
    """

    capacity: int                      # SpellInfo::StackAmount == SpellAuraOptions.CumulativeAura
    max_stacks: int                    # CalcMaxStackAmount() result
    max_charges: int                   # CalcMaxCharges() result (before uint8)
    aura_unique: bool = False          # SPELL_ATTR1_AURA_UNIQUE
    aura_unique_per_caster: bool = False  # SPELL_ATTR5_AURA_UNIQUE_PER_CASTER
    pandemic: bool = False             # SPELL_ATTR13_PERIODIC_REFRESH_EXTENDS_DURATION
    max_duration: int = -1             # CalcMaxDuration() result used by RefreshTimers

    @classmethod
    def plain(cls, capacity: int, **kw: Any) -> AuraShape:
        kw.setdefault("max_stacks", capacity)
        kw.setdefault("max_charges", 0)
        return cls(capacity=capacity, **kw)


@dataclass
class StackState:
    """The mutable stack/charge/duration members of one ``Aura``."""

    stacks: int
    charges: int
    using_charges: bool
    duration: int
    max_duration: int
    removed: bool = False
    remove_mode: str | None = None

    def snapshot(self) -> dict[str, Any]:
        return asdict(self)


def u8(value: int) -> int:
    """C++ conversion of an int to ``uint8`` (modulo 256)."""
    return int(value) & 0xFF


# ---------------------------------------------------------------------------
# Initial stacks and charges
# ---------------------------------------------------------------------------

def spell_value_aura_stack(value: int) -> int:
    """``SPELLVALUE_AURA_STACK`` override stored into ``SpellValue::AuraStackAmount``.

    Mirrors: Spell.cpp:8741 (``m_spellValue->AuraStackAmount = uint8(value.Value.I)``);
    default 1 at Spell.cpp:450.
    """
    return u8(value)


def create_info_stack_amount(requested: int) -> int:
    """``AuraCreateInfo::SetStackAmount``: non-positive requests become 1.

    Mirrors: SpellAuras.h:120 (``StackAmount = stackAmount > 0 ? stackAmount : 1``); default 1 at SpellAuras.h:134.
    """
    return requested if requested > 0 else 1


def initial_stacks(override: int | None = None, doses_mod: int | None = None) -> int:
    """``m_stackAmount`` of a *newly constructed* aura from a spell hit.

    ``SpellValue::AuraStackAmount`` (int32) starts at 1; the Spell constructor applies the
    caster's ``SpellModOp::Doses`` mods to it (``doses_mod`` = the modded value); a
    ``SPELLVALUE_AURA_STACK`` override (``override``) is applied **after** the constructor
    and replaces it (stored through ``uint8``).  ``AuraCreateInfo::SetStackAmount`` floors
    non-positive values to 1 and ``Aura::Aura`` narrows into the ``uint8`` member.
    ``CumulativeAura`` does not appear anywhere on this path.

    Mirrors: Spell.cpp:450 (default 1), Spell.cpp:506 (Doses at Spell ctor), Object.cpp:2239-2241
    (``new Spell`` then ``SetSpellValue``), Spell.cpp:8741 (override ``uint8``), Spell.cpp:3251, SpellAuras.h:120,
    SpellAuras.cpp:481 (``m_stackAmount(createInfo.StackAmount)``, uint8 member SpellAuras.h:238).
    """
    value = 1 if doses_mod is None else int(doses_mod)
    if override is not None:
        value = spell_value_aura_stack(override)
    return u8(create_info_stack_amount(value))


def initial_state(shape: AuraShape, requested_stacks: int = 1) -> StackState:
    """State right after ``Aura::Aura``.

    Mirrors: SpellAuras.cpp:481 (stacks), SpellAuras.cpp:496-499 (``m_maxDuration``/``m_duration``,
    ``m_procCharges = CalcMaxCharges(caster)``, ``m_isUsingCharges = m_procCharges != 0``).
    """
    charges = u8(shape.max_charges)
    return StackState(stacks=u8(create_info_stack_amount(requested_stacks)), charges=charges,
                      using_charges=charges != 0, duration=shape.max_duration, max_duration=shape.max_duration)


def reset_periodic_on_hit(capacity: int, dont_reset_trigger_flag: bool = False) -> bool:
    """``AuraCreateInfo::ResetPeriodicTimer`` chosen by a spell hit.

    Mirrors: Spell.cpp:3240 (``(m_spellInfo->StackAmount < 2) && !(TRIGGERED_DONT_RESET_PERIODIC_TIMER)``).
    """
    return capacity < 2 and not dont_reset_trigger_flag


# ---------------------------------------------------------------------------
# Predicates on SpellInfo
# ---------------------------------------------------------------------------

def is_multi_slot(spell: int, passive: bool) -> bool:
    """Mirrors: SpellInfo.cpp:1803 ``IsMultiSlotAura`` (passive, or three hardcoded ids)."""
    return passive or spell in MULTISLOT_IDS


def is_stackable_on_one_slot_with_different_casters(capacity: int, channeled: bool, dot_stacking_rule: bool) -> bool:
    """Mirrors: SpellInfo.cpp:1808 ``IsStackableOnOneSlotWithDifferentCasters``."""
    return capacity > 1 and not channeled and not dot_stacking_rule


def is_using_stacks(capacity: int, stacks: int) -> bool:
    """Mirrors: SpellAuras.cpp:1078 ``Aura::IsUsingStacks``."""
    return capacity > 0 or stacks > 1


def client_applications(capacity: int, stacks: int, charges: int) -> int:
    """The single number the client is sent for an aura.

    Mirrors: SpellAuras.cpp:261 (``Applications = IsUsingStacks() ? GetStackAmount() : GetCharges()``).
    """
    return stacks if is_using_stacks(capacity, stacks) else charges


# ---------------------------------------------------------------------------
# Mutators
# ---------------------------------------------------------------------------

def apply_spell_mod(base: int, flat: int, pct: float, kind: str) -> int:
    """``Player::ApplySpellMod<T>`` arithmetic for one op: ``T((double(base) + flat) * float totalmul)``.

    ``kind`` is ``int32`` (MaxAuraStacks, Doses) or ``uint32`` (ProcCharges).  A negative result converted to
    ``uint32`` is **undefined behaviour** in C++; the value returned here is what gcc/x86-64 produces at the
    pinned flags (``cvttsd2si`` to 64 bit, then truncation) and what the probe observes -- a Trinity-only number.

    Mirrors: Player.cpp:22844-22852.
    """
    import struct
    mul = struct.unpack("f", struct.pack("f", pct))[0]
    value = (float(base) + int(flat)) * mul
    truncated = int(value)          # C++ float->integer conversion truncates toward zero
    if kind == "int32":
        if not -2**31 <= truncated < 2**31:
            raise FailClosed("int32 overflow in ApplySpellMod (undefined behaviour)")
        return truncated
    if kind == "uint32":
        return truncated & 0xFFFFFFFF
    raise FailClosed(f"unknown ApplySpellMod kind {kind!r}")


def calc_max_stack_amount(capacity: int, mod: tuple[int, float] | None = None) -> int:
    """``CalcMaxStackAmount`` as the ``int32`` that ``ModStackAmount`` compares against.

    ``mod`` is the caster's summed ``(flat, pct)`` for ``SpellModOp::MaxAuraStacks`` (``None`` = no spell-mod
    owner).  A negative result survives (``uint32`` return, cast back to ``int32`` by the caller).

    Mirrors: SpellAuras.cpp:1083-1091, SpellAuras.cpp:1096 (``int32(CalcMaxStackAmount())``).
    """
    if mod is None:
        return capacity
    return apply_spell_mod(capacity, mod[0], mod[1], "int32")


def set_stack_amount(state: StackState, value: int) -> list[dict[str, Any]]:
    """``Aura::SetStackAmount``: no cap, no removal at 0, always a REAPPLY of every effect.

    Mirrors: SpellAuras.cpp:1056 (store ``uint8``; ``HandleAuraSpecificMods(.., false, true)``;
    ``ChangeAmount(CalculateAmount(caster), false, true)`` per effect; ``HandleAuraSpecificMods(.., true, true)``).
    """
    state.stacks = u8(value)
    return [{"op": "set_stacks", "stacks": state.stacks},
            {"op": "recalculate_amounts", "handle_mask": "REAPPLY (+CHANGE_AMOUNT if changed)",
             "can_be_recalculated_ignored": True}]


def set_charges(state: StackState, value: int) -> list[dict[str, Any]]:
    """Mirrors: SpellAuras.cpp:994 ``Aura::SetCharges`` (no-op if equal; ``m_isUsingCharges = charges != 0``)."""
    value = u8(value)
    if state.charges == value:
        return []
    state.charges = value
    state.using_charges = value != 0
    return [{"op": "set_charges", "charges": value, "using_charges": state.using_charges}]


def refresh_timers(state: StackState, shape: AuraShape, reset_periodic: bool) -> list[dict[str, Any]]:
    """Duration side of ``Aura::RefreshTimers`` (duration detail belongs to track B, periodic to track D).

    Mirrors: SpellAuras.cpp:976 (``m_maxDuration = CalcMaxDuration()``; pandemic forces
    ``resetPeriodicTimer = false``; ``RefreshDuration()`` -> ``SetDuration(GetMaxDuration())``, ``ResetTicks``;
    ``CalculatePeriodic(caster, resetPeriodicTimer, false)`` per effect).
    """
    state.max_duration = shape.max_duration
    state.duration = shape.max_duration
    effective_reset = False if shape.pandemic else reset_periodic
    return [{"op": "refresh_timers", "duration": state.duration, "reset_periodic_timer": effective_reset,
             "ticks_reset": True}]


def mod_stack_amount(state: StackState, shape: AuraShape, num: int, remove_mode: str = REMOVE_DEFAULT,
                     reset_periodic: bool = True) -> list[dict[str, Any]]:
    """``Aura::ModStackAmount``.

    * the cap applies **only when num > 0**; a zero-capacity aura is pinned to 1;
    * ``m_stackAmount + num <= 0`` removes the aura with ``remove_mode``;
    * refresh (``RefreshTimers`` + charges reset to ``CalcMaxCharges``) iff the new
      count is ``>=`` the old one and (capacity != 0 or the aura is not
      AURA_UNIQUE / AURA_UNIQUE_PER_CASTER) -- ``num == 0`` therefore refreshes;
    * ``SetStackAmount`` (amount recalculation, REAPPLY) runs on every non-removing call.

    Mirrors: SpellAuras.cpp:1093-1127.
    """
    if state.removed:
        raise FailClosed("ModStackAmount on a removed aura")
    stack_amount = state.stacks + num
    max_stack_amount = shape.max_stacks
    events: list[dict[str, Any]] = [{"op": "mod_stacks", "num": num, "before": state.stacks}]
    if num > 0 and stack_amount > max_stack_amount:
        stack_amount = 1 if not shape.capacity else max_stack_amount
    elif stack_amount <= 0:
        state.removed = True
        state.remove_mode = remove_mode
        events.append({"op": "remove", "mode": remove_mode})
        return events
    refresh = stack_amount >= state.stacks and (
        bool(shape.capacity) or (not shape.aura_unique and not shape.aura_unique_per_caster))
    events += set_stack_amount(state, stack_amount)
    if refresh:
        events += refresh_timers(state, shape, reset_periodic)
        events += set_charges(state, shape.max_charges)
    events.append({"op": "refresh" if refresh else "no_refresh"})
    return events


def mod_charges(state: StackState, shape: AuraShape, num: int, remove_mode: str = REMOVE_DEFAULT) -> list[dict[str, Any]]:
    """``Aura::ModCharges``: inert unless the aura is using charges; cap only on increase.

    Mirrors: SpellAuras.cpp:1017-1038.
    """
    if state.removed:
        raise FailClosed("ModCharges on a removed aura")
    if not state.using_charges:
        return [{"op": "mod_charges_ignored", "reason": "IsUsingCharges() false"}]
    charges = state.charges + num
    max_charges = u8(shape.max_charges)
    if num > 0 and charges > max_charges:
        charges = max_charges
    elif charges <= 0:
        state.removed = True
        state.remove_mode = remove_mode
        return [{"op": "remove", "mode": remove_mode}]
    return set_charges(state, charges)


def refresh_base_amount(old: float, new: float, aura_points_stack: bool) -> float:
    """Per-effect ``m_baseAmount`` on a refresh/stack hit.

    Mirrors: Unit.cpp:3423-3426 (``AuraPointsStack`` -> ``old += new`` else ``old = new``).
    """
    return old + new if aura_points_stack else new


def scale_amount(amount: float, stacks: int, suppress_points_stacking: bool, aura_type: int) -> float:
    """Stack scaling tail of ``AuraEffect::CalculateAmount`` (after the script CalcAmount hook).

    Every aura type is multiplied by the stack count unless the effect carries
    ``SuppressPointsStacking``; a fixed list of types is then ``std::round``ed; the
    result is clamped to +-2e9.

    Mirrors: SpellAuraEffects.cpp:843-866.
    """
    if not suppress_points_stacking:
        amount *= stacks
    if aura_type in ROUNDED_AURA_TYPES:
        amount = _cpp_round(amount)
    return min(max(amount, EFFECT_VALUE_MIN), EFFECT_VALUE_MAX)


def stack_amount_for_bonuses(stacks: int, suppress_points_stacking: bool) -> int:
    """Stack multiplier handed to ``SpellDamageBonusDone``/``SpellHealingBonusDone`` at every periodic tick.

    Mirrors: SpellAuraEffects.cpp:5651 / 5780 / 5908 and :880 (estimated amount); Unit.cpp:6873/6887 use it as a
    multiplier of the coefficient bonus.
    """
    return 1 if suppress_points_stacking else stacks


def _cpp_round(x: float) -> float:
    """``std::round`` on a double: half away from zero."""
    import math
    return math.copysign(math.floor(abs(x) + 0.5), x)


# ---------------------------------------------------------------------------
# Reapplication (the refresh branch of TryRefreshStackOrCreate)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Reapply:
    """One incoming application of the same SpellId that reached ``_TryStackingOrRefreshingExistingAura``."""

    stack_amount: int = 1              # AuraCreateInfo::StackAmount
    reset_periodic: bool = True        # AuraCreateInfo::ResetPeriodicTimer
    effect_mask_matches: bool = True   # createInfo.GetAuraEffectMask() == foundAura->GetEffectMask()
    multislot: bool = False            # SpellInfo::IsMultiSlotAura()
    found: bool = True                 # GetOwnedAura(...) located an existing aura (identity: track A)


def reapply(state: StackState, shape: AuraShape, inc: Reapply) -> list[dict[str, Any]]:
    """Stack/charge outcome of a reapplication.

    Returns ``[{"op": "create_new"...}]`` when no existing aura is mutated
    (multi-slot, not found, or effect mask mismatch) -- the new ``Aura`` then starts
    from :func:`initial_state`; coexistence vs replacement is track A's question.

    Mirrors: Unit.cpp:3386-3446 (``_TryStackingOrRefreshingExistingAura``), SpellAuras.cpp:350-387
    (``TryRefreshStackOrCreate``; ``foundAura->IsRemoved()`` -> nullptr).
    """
    if inc.multislot:
        return [{"op": "create_new", "reason": "IsMultiSlotAura (passive): refresh branch skipped"}]
    if not inc.found:
        return [{"op": "create_new", "reason": "no owned aura with the key"}]
    if not inc.effect_mask_matches:
        return [{"op": "create_new", "reason": "effect mask mismatch (Unit.cpp:3406)"}]
    events = mod_stack_amount(state, shape, inc.stack_amount, REMOVE_DEFAULT, inc.reset_periodic)
    if state.removed:
        events.append({"op": "return_nullptr", "reason": "foundAura->IsRemoved()"})
    return events


def owned_aura_key(one_slot: bool, caster_guid: int, enchant_proc: bool, cast_item_guid: int) -> tuple[int, int]:
    """``(casterGUID, itemCasterGUID)`` passed to ``GetOwnedAura`` by the refresh branch (0 = empty = wildcard).

    Mirrors: Unit.cpp:3401 (``IsStackableOnOneSlotWithDifferentCasters() ? ObjectGuid::Empty : createInfo.CasterGUID``;
    cast item only for ``SPELL_ATTR0_CU_ENCHANT_PROC``).  Identity semantics: track A.
    """
    return (0 if one_slot else caster_guid, cast_item_guid if enchant_proc else 0)


# ---------------------------------------------------------------------------
# Millisecond timelines
# ---------------------------------------------------------------------------

def run_timeline(shape: AuraShape, steps: list[dict[str, Any]], shape_changes: dict[int, AuraShape] | None = None
                 ) -> list[dict[str, Any]]:
    """Replay ``steps`` (each ``{"t": ms, "op": ..., ...}``, ordered) on one aura.

    Ops: ``apply`` (``stacks``), ``reapply`` (:class:`Reapply` fields), ``mod_stacks`` (``num``, ``mode``),
    ``set_stacks`` (``value``), ``mod_charges`` (``num``, ``mode``), ``consume_proc`` (``use_stacks``).
    Duration counts down between steps; a non-permanent aura whose duration reaches 0 is
    reported as ``expired`` at that millisecond (removal semantics: track E).
    ``shape_changes`` maps a step index to a new shape (live spell-mod changes).
    """
    out: list[dict[str, Any]] = []
    state: StackState | None = None
    last_t: int | None = None
    for i, step in enumerate(steps):
        if shape_changes and i in shape_changes:
            shape = shape_changes[i]
        t = step["t"]
        if last_t is not None and t < last_t:
            raise FailClosed("timeline steps must be ordered")
        if state is not None and not state.removed and last_t is not None and state.duration > 0:
            state.duration -= t - last_t
            if state.duration <= 0:
                expire_at = t + state.duration
                state.duration = 0
                state.removed = True
                state.remove_mode = "AURA_REMOVE_BY_EXPIRE"
                out.append({"t": expire_at, "op": "expired", "state": state.snapshot()})
        last_t = t
        op = step["op"]
        if op == "apply":
            if state is not None and not state.removed:
                raise FailClosed("apply on a live aura: use reapply")
            state = initial_state(shape, step.get("stacks", 1))
            events = [{"op": "create", "stacks": state.stacks, "charges": state.charges}]
        elif state is None or state.removed:
            if op == "reapply":
                state = initial_state(shape, step.get("stack_amount", 1))
                events = [{"op": "create_new", "reason": "no live aura"}]
            else:
                events = [{"op": "noop", "reason": "no live aura"}]
        elif op == "reapply":
            inc = Reapply(**{k: v for k, v in step.items() if k not in ("t", "op")})
            events = reapply(state, shape, inc)
            if events and events[0]["op"] == "create_new":
                state = initial_state(shape, inc.stack_amount)
        elif op == "mod_stacks":
            events = mod_stack_amount(state, shape, step["num"], step.get("mode", REMOVE_DEFAULT),
                                      step.get("reset_periodic", True))
        elif op == "set_stacks":
            events = set_stack_amount(state, step["value"])
        elif op == "mod_charges":
            events = mod_charges(state, shape, step["num"], step.get("mode", REMOVE_DEFAULT))
        elif op == "consume_proc":
            from .charges import consume_proc
            events = consume_proc(state, shape, step.get("use_stacks", False), step.get("no_consume_event", False))
        else:
            raise FailClosed(f"unknown timeline op {op!r}")
        out.append({"t": t, "op": op, "events": events, "state": state.snapshot() if state else None,
                    "applications": client_applications(shape.capacity, state.stacks, state.charges) if state else None})
    return out


# ---------------------------------------------------------------------------
# Census
# ---------------------------------------------------------------------------

def _attrs():
    from procs.enums import attr
    return {
        "passive": attr("SPELL_ATTR0_PASSIVE"),
        "aura_unique": attr("SPELL_ATTR1_AURA_UNIQUE"),
        "aura_unique_per_caster": attr("SPELL_ATTR5_AURA_UNIQUE_PER_CASTER"),
        "dot_stacking_rule": attr("SPELL_ATTR3_DOT_STACKING_RULE"),
        "channelled": attr("SPELL_ATTR1_IS_CHANNELLED"),
        "self_channelled": attr("SPELL_ATTR1_IS_SELF_CHANNELLED"),
        "pandemic": attr("SPELL_ATTR13_PERIODIC_REFRESH_EXTENDS_DURATION"),
        "dispel_removes_charges": attr("SPELL_ATTR7_DISPEL_REMOVES_CHARGES"),
        # Trinity: SPELL_ATTR15_UNK10 (no consumer); simc: SX_ASYNCHRONOUS_STACKING_AURA = 490 (data_enums.hh:1910);
        # Core: SpellAttributeKind::AsynchronousStackingBuff (crates/dbc/src/spell_attribute.rs:181)
        "async_stacking": attr("SPELL_ATTR15_UNK10"),
    }


def stack_facts(ctx, spell: int) -> dict[str, Any]:
    """DB2 facts the stack mutators read for one spell (DIFFICULTY_NONE)."""
    info = ctx.catalog.get(spell)
    if info is None:
        raise FailClosed(f"spell {spell} has no DIFFICULTY_NONE SpellInfo in the snapshot")
    a = _attrs()
    opts = ctx.data.row("SpellAuraOptions", spell)
    effects = []
    for e in ctx.data.effects(spell):
        ea = int(e["EffectAttributes"] or 0)
        effects.append({"index": e["EffectIndex"], "effect": e["Effect"], "aura": e["EffectAura"],
                        "suppress_points_stacking": bool(ea & EFFECT_ATTR_SUPPRESS_POINTS_STACKING),
                        "aura_points_stack": bool(ea & EFFECT_ATTR_AURA_POINTS_STACK)})
    channeled = info.has_attr(a["channelled"]) or info.has_attr(a["self_channelled"])
    from .passive import effective_passive
    passive = effective_passive(ctx.data, spell)["passive"]  # load-time corrections included (R1-02)
    return {
        "spell": spell,
        "capacity": info.stack_amount,
        "cumulative_aura_row": None if opts is None else opts["CumulativeAura"],
        "passive": passive,
        "multislot": is_multi_slot(spell, passive),
        "channeled": channeled,
        "dot_stacking_rule": info.has_attr(a["dot_stacking_rule"]),
        "aura_unique": info.has_attr(a["aura_unique"]),
        "aura_unique_per_caster": info.has_attr(a["aura_unique_per_caster"]),
        "pandemic": info.has_attr(a["pandemic"]),
        "async_stacking_attr": info.has_attr(a["async_stacking"]),
        "db2_proc_charges": info.proc_charges,
        "one_slot_different_casters": is_stackable_on_one_slot_with_different_casters(
            info.stack_amount, channeled, info.has_attr(a["dot_stacking_rule"])),
        "reset_periodic_on_hit": reset_periodic_on_hit(info.stack_amount),
        "effects": effects,
    }


def stack_family(f: dict[str, Any]) -> str:
    """Reapplication stack behaviour class (Trinity default path, no mods, no scripts).

    Mirrors the branch structure of Unit.cpp:3393 (multi-slot), SpellAuras.cpp:1099-1114 (cap, unique gate),
    SpellInfo.cpp:1808 (shared slot).
    """
    cap = f["capacity"]
    if f["multislot"]:
        return "multislot-no-reapply-path" if cap == 0 else "multislot-dormant-capacity"
    if cap == 0:
        if f["aura_unique"] or f["aura_unique_per_caster"]:
            return "single-no-refresh-unique"
        return "single-refresh"
    if cap == 1:
        return "cap1-refresh"
    if f["one_slot_different_casters"]:
        return "stacking-shared-across-casters"
    return "stacking-per-caster"


FAMILY_DOC = {
    "multislot-no-reapply-path": "passive/multi-slot, capacity 0: every application builds a new Aura (A decides coexistence)",
    "multislot-dormant-capacity": "passive/multi-slot with CumulativeAura>0: reapplication never stacks; capacity only caps "
                                  "external ModStackAmount (MODIFY_AURA_STACKS, scripts, linked sync)",
    "single-refresh": "capacity 0: reapply keeps 1 stack, RefreshTimers, charges reset, amount recalculated",
    "single-no-refresh-unique": "capacity 0 + ATTR1_AURA_UNIQUE/ATTR5_AURA_UNIQUE_PER_CASTER: reapply keeps 1 stack, "
                                "amount recalculated (REAPPLY) but no RefreshTimers and no charge reset",
    "cap1-refresh": "capacity 1: like single-refresh but refreshes even when AURA_UNIQUE; IsUsingStacks true "
                    "(client Applications shows 1 instead of charges)",
    "stacking-per-caster": "capacity>=2, channeled or ATTR3_DOT_STACKING_RULE: +StackAmount per reapply by the same caster",
    "stacking-shared-across-casters": "capacity>=2, not channeled, no DOT_STACKING_RULE: one Aura keyed with empty caster "
                                      "GUID collects stacks from every caster",
}


def _tc_text(rel: str) -> list[str]:
    return (TC_ROOT / rel).read_text(encoding="utf-8", errors="replace").splitlines()


SCRIPT_MUTATORS = ("SetStackAmount", "ModStackAmount", "SetCharges", "ModCharges", "DropCharge", "DropChargeDelayed",
                   "RemoveAuraFromStack", "SetAuraStack", "SetUsingCharges", "SPELLVALUE_AURA_STACK")
_MUT_RE = re.compile(r"\b(" + "|".join(SCRIPT_MUTATORS) + r")\b")


def script_mutator_sites(ctx) -> list[dict[str, Any]]:
    """Every call site of a stack/charge mutator under ``src/server/scripts`` (text census),
    attributed to the enclosing indexed script class and, through ``spell_script_names``, to SpellIDs.

    Evidence: script-consumer (lexical); the call may sit in a branch never reached for a given spell.
    """
    if not TC_ROOT.is_dir():
        raise FailClosed("TrinityCore checkout absent")
    index = ctx.bundle.index
    script_to_spells: dict[str, set[int]] = defaultdict(set)
    for sid, names in ctx.bundle.script_names.items():
        for n in names:
            script_to_spells[n].add(sid)
    # (file) -> [(line, end_line, class name, script name)] for every class a bound script name instantiates
    spans: dict[str, list[tuple[int, int, str, str]]] = defaultdict(list)
    for name in sorted(script_to_spells):
        for sc in index.resolve_script_name(name)["classes"]:
            spans[sc.file].append((sc.line, sc.end_line, sc.name, name))
    all_classes: dict[str, list[tuple[int, int, str]]] = defaultdict(list)
    for sc in index.classes.values():
        all_classes[sc.file].append((sc.line, sc.end_line, sc.name))
    root = TC_ROOT / "src/server/scripts"
    out = []
    for path in sorted(root.rglob("*.cpp")):
        rel = str(path.relative_to(TC_ROOT))
        text = path.read_text(encoding="utf-8", errors="replace").splitlines()
        for n, line in enumerate(text, 1):
            for m in _MUT_RE.finditer(line):
                if line.lstrip().startswith("//"):
                    continue
                bound = [x for x in spans.get(rel, []) if x[0] <= n <= x[1]]
                enclosing = sorted((x for x in all_classes.get(rel, []) if x[0] <= n <= x[1]), key=lambda x: x[1] - x[0])
                cname = enclosing[0][2] if enclosing else None
                scripts = sorted({x[3] for x in bound})
                spells = sorted({s for sc in scripts for s in script_to_spells.get(sc, ())})
                out.append({"file": rel, "line": n, "mutator": m.group(1), "class": cname, "scripts": scripts,
                            "spells": spells, "text": line.strip()[:160]})
    return out


def modify_aura_stacks_rows(ctx) -> list[dict[str, Any]]:
    """Every DIFFICULTY_NONE ``SPELL_EFFECT_MODIFY_AURA_STACKS`` row.

    Mirrors: SpellEffects.cpp:6126 ``EffectModifyAuraStacks`` (target aura = ``GetAura(TriggerSpell)``, any caster;
    MiscValue 0 -> ModStackAmount(value), 1 -> SetStackAmount(value), other -> no-op).
    """
    out = []
    for (spell, diff), effs in ctx.data.table("SpellEffect").items():
        if diff != 0:
            continue
        for idx, e in effs.items():
            if e["Effect"] == SPELL_EFFECT_MODIFY_AURA_STACKS:
                mv = e["EffectMiscValue_0"]
                out.append({"spell": spell, "index": idx, "target_spell": e["EffectTriggerSpell"],
                            "mode": {0: "ModStackAmount", 1: "SetStackAmount"}.get(mv, "no-op (unhandled MiscValue)"),
                            "misc_value": mv, "base_points": e["EffectBasePointsF"]})
    out.sort(key=lambda r: (r["spell"], r["index"]))
    return out


def linked_rows(ctx) -> list[dict[str, Any]]:
    """SPELL_AURA_LINKED / LINKED_2 provider effects: REAPPLY of the parent syncs the child's stacks.

    Mirrors: SpellAuraEffects.cpp:5324-5359 ``HandleAuraLinked`` (REAL apply casts the child with default stacks;
    REAPPLY -> child ``ModStackAmount(parent - child)``, which refreshes the child when the diff is >= 0).
    """
    from procs.enums import is_aura_effect
    out = []
    for (spell, diff), effs in ctx.data.table("SpellEffect").items():
        if diff != 0:
            continue
        for idx, e in effs.items():
            if e["EffectAura"] in (SPELL_AURA_LINKED, SPELL_AURA_LINKED_2) and is_aura_effect(e["Effect"], e["EffectAura"]):
                out.append({"spell": spell, "index": idx, "aura": e["EffectAura"], "child": e["EffectTriggerSpell"]})
    out.sort(key=lambda r: (r["spell"], r["index"]))
    return out


def spellmod_rows(ctx, ops=tuple(SPELLMOD_OPS)) -> list[dict[str, Any]]:
    """Aura effects that add ProcCharges / Doses / MaxAuraStacks spell modifiers (DIFFICULTY_NONE)."""
    out = []
    for (spell, diff), effs in ctx.data.table("SpellEffect").items():
        if diff != 0:
            continue
        for idx, e in effs.items():
            if e["EffectAura"] in SPELLMOD_AURAS and e["EffectMiscValue_0"] in ops:
                info = ctx.catalog.get(spell)
                cm = 0
                for i in range(4):
                    cm |= (int(e[f"EffectSpellClassMask_{i}"]) & 0xFFFFFFFF) << (32 * i)
                out.append({"spell": spell, "index": idx, "kind": SPELLMOD_AURAS[e["EffectAura"]],
                            "op": SPELLMOD_OPS[e["EffectMiscValue_0"]], "value": e["EffectBasePointsF"],
                            "family": info.family if info else None, "class_mask": cm,
                            "label": e["EffectMiscValue_1"] if e["EffectAura"] in (218, 219) else None})
    out.sort(key=lambda r: (r["spell"], r["index"]))
    return out


def spellmod_targets(ctx, mods: list[dict[str, Any]], candidates) -> dict[tuple[int, int], list[int]]:
    """Candidate spells each modifier can affect.

    Mirrors: SpellInfo.cpp ``IsAffectedBySpellMod`` (label mods: label membership; class mods: same family and a
    shared class-mask bit).  ATTR3_IGNORE_CASTER_MODIFIERS is honoured.  Structural: whether the modifier is
    ever owned by the caster of the target is not decided here.
    """
    from procs.enums import attr
    ignore = attr("SPELL_ATTR3_IGNORE_CASTER_MODIFIERS")
    by_family: dict[int, list] = defaultdict(list)
    by_label: dict[int, list[int]] = defaultdict(list)
    for sid in candidates:
        info = ctx.catalog.get(sid)
        if info is None or info.has_attr(ignore):
            continue
        by_family[info.family].append((sid, info.family_flags))
        for lab in info.labels:
            by_label[lab].append(sid)
    out = {}
    for m in mods:
        if m["label"] is not None:
            hits = sorted(by_label.get(m["label"], []))
        elif m["family"] and m["class_mask"]:
            hits = sorted(s for s, ff in by_family.get(m["family"], []) if ff & m["class_mask"])
        else:
            hits = []
        out[(m["spell"], m["index"])] = hits
    return out
