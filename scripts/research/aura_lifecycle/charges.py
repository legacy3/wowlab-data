"""Track C: aura proc charges (``Aura::m_procCharges``) and how they relate to stacks.

Kept distinct from:

* **stacks** (:mod:`.stacks`) -- except where ``PROC_ATTR_USE_STACKS_FOR_CHARGES`` makes a
  proc consume a stack, and where ``ModStackAmount``'s refresh branch resets charges;
* **spell (category) charges** -- ``SpellCategories.ChargeCategory`` / ``SpellHistory``;
  a spell can have both and they never touch each other in the aura code;
* proc cooldown / RPPM clocks -- ``procs.state`` (already modelled by the proc pass).

The proc-time drop/consume order (``PrepareProcChargeDrop`` before the proc,
``ConsumeProcCharges`` after it) is the proc pass's model (``procs/state.py``); this
module re-states only the charge-state transitions so stack/charge timelines compose.
"""

from __future__ import annotations

from typing import Any

from . import FailClosed
from .stacks import (REMOVE_DEFAULT, AuraShape, StackState, apply_spell_mod, mod_charges, mod_stack_amount,  # noqa: F401
                     set_charges, u8)


def calc_max_charges(db2_proc_charges: int, proc_entry_charges: int | None,
                     mod: tuple[int, float] | None = None) -> int:
    """``Aura::CalcMaxCharges``.

    ``proc_entry_charges`` is ``SpellProcEntry::Charges`` when ``GetSpellProcEntry`` finds an
    entry (a ``spell_proc`` row with Charges 0 already fell back to the DB2 value at load);
    ``mod`` is the caster's summed ``(flat, pct)`` for ``SpellModOp::ProcCharges`` (``uint32`` arithmetic; a
    negative result is undefined behaviour -- see :func:`.stacks.apply_spell_mod`).

    Mirrors: SpellAuras.cpp:1004-1015 (``uint8(maxProcCharges)``); SpellMgr.cpp:1576-1577 (spell_proc fallback);
    SpellMgr.cpp:1886 (generated entries copy ``ProcCharges``).
    """
    value = db2_proc_charges if proc_entry_charges is None else proc_entry_charges
    if mod is not None:
        value = apply_spell_mod(value, mod[0], mod[1], "uint32")
    return u8(value)


def prepare_proc_charge_drop(state: StackState, use_stacks_for_charges: bool, no_consume_event: bool
                             ) -> list[dict[str, Any]]:
    """Mirrors: SpellAuras.cpp:1810-1818 ``PrepareProcChargeDrop`` (``--m_procCharges`` on a uint8, no removal here;
    skipped for USE_STACKS_FOR_CHARGES, for non-charge auras, and when the event spell has ATTR6_DO_NOT_CONSUME_RESOURCES)."""
    if not use_stacks_for_charges and state.using_charges and not no_consume_event:
        state.charges = u8(state.charges - 1)
        return [{"op": "drop_charge", "charges": state.charges}]
    return []


def consume_proc_charges(state: StackState, shape: AuraShape, use_stacks_for_charges: bool) -> list[dict[str, Any]]:
    """Mirrors: SpellAuras.cpp:1820-1832 ``ConsumeProcCharges`` (USE_STACKS -> ``ModStackAmount(-1)``, which removes at
    0 with AURA_REMOVE_BY_DEFAULT and never refreshes; else remove when ``IsUsingCharges() && !GetCharges()``)."""
    if use_stacks_for_charges:
        return mod_stack_amount(state, shape, -1, REMOVE_DEFAULT)
    if state.using_charges and state.charges == 0:
        state.removed = True
        state.remove_mode = REMOVE_DEFAULT
        return [{"op": "remove", "mode": REMOVE_DEFAULT, "reason": "no charges left"}]
    return []


def consume_proc(state: StackState, shape: AuraShape, use_stacks_for_charges: bool, no_consume_event: bool = False
                 ) -> list[dict[str, Any]]:
    """One *successful* proc of the aura: prepare (drop) then, after the proc handlers, consume.

    Mirrors: SpellAuras.cpp:1793 ``PrepareProcToTrigger`` -> :1810; SpellAuras.cpp:2010-2030 ``TriggerProcOnEvent``
    -> ``ConsumeProcCharges``.  Proc handlers, cooldowns and RPPM clocks: ``procs.state``.
    """
    if state.removed:
        raise FailClosed("proc on a removed aura")
    events = prepare_proc_charge_drop(state, use_stacks_for_charges, no_consume_event)
    events.append({"op": "proc_handlers"})
    return events + consume_proc_charges(state, shape, use_stacks_for_charges)


def proc_gate_open(state: StackState) -> bool:
    """Mirrors: SpellAuras.cpp:1886-1887 (``IsUsingCharges() && !GetCharges()`` -> no proc) and Player.cpp:22964
    (spell mods of a charge aura with 0 charges are not applied)."""
    return not (state.using_charges and state.charges == 0)


# ---------------------------------------------------------------------------
# Census
# ---------------------------------------------------------------------------

def proc_store(ctx):
    """``mSpellProcMap`` over the snapshot catalog + TDB overlay (procs pass)."""
    cached = getattr(ctx, "_al_c_proc_store", None)
    if cached is None:
        from procs.definition import ProcEntryStore
        cached = ProcEntryStore(ctx.catalog, ctx.bundle.proc_overlay)
        ctx._al_c_proc_store = cached
    return cached


def charge_facts(ctx, spell: int) -> dict[str, Any]:
    from procs.enums import PROC_ATTR_USE_STACKS_FOR_CHARGES, attr
    info = ctx.catalog.get(spell)
    if info is None:
        raise FailClosed(f"spell {spell} has no DIFFICULTY_NONE SpellInfo in the snapshot")
    entry = proc_store(ctx).lookup(spell)
    opts = ctx.data.row("SpellAuraOptions", spell)
    cats = ctx.data.row("SpellCategories", spell)
    use_stacks = bool(entry and entry.attributes_mask & PROC_ATTR_USE_STACKS_FOR_CHARGES)
    return {
        "spell": spell,
        "db2_proc_charges": info.proc_charges,
        "proc_charges_row": None if opts is None else opts["ProcCharges"],
        "proc_entry": None if entry is None else {"origin": entry.origin, "charges": entry.charges,
                                                  "attributes_mask": entry.attributes_mask,
                                                  "proc_flags": entry.proc_flags},
        "initial_charges_no_mods": calc_max_charges(info.proc_charges, entry.charges if entry else None),
        "use_stacks_for_charges": use_stacks,
        "capacity": info.stack_amount,
        "dispel_removes_charges": info.has_attr(attr("SPELL_ATTR7_DISPEL_REMOVES_CHARGES")),
        "proc_failure_burns_charge": info.has_attr(attr("SPELL_ATTR0_PROC_FAILURE_BURNS_CHARGE")),
        "spell_charge_category": 0 if cats is None else cats["ChargeCategory"],
    }


def charge_family(f: dict[str, Any]) -> str:
    """Which mutable counter a proc/dispel decrements (Trinity default path)."""
    charges = f["initial_charges_no_mods"]
    entry = f["proc_entry"]
    if f["use_stacks_for_charges"]:
        return "stacks-as-charges" + ("+dormant-charges" if charges else "")
    if charges == 0:
        return "no-charges"
    if entry is None:
        return "charges-without-proc-entry"
    return "proc-charges"


FAMILY_DOC = {
    "no-charges": "CalcMaxCharges()==0: IsUsingCharges false; ModCharges is a no-op; Applications shows stacks or 0",
    "proc-charges": "charges drop on each successful proc (PrepareProcChargeDrop) and the aura is removed at 0 "
                    "(ConsumeProcCharges); reset to CalcMaxCharges by every refreshing ModStackAmount",
    "charges-without-proc-entry": "m_procCharges>0 but GetSpellProcEntry finds nothing: the proc system never drops "
                                  "them; only dispel/steal (ATTR7), scripts, SmartAI or redirect auras can",
    "stacks-as-charges": "spell_proc PROC_ATTR_USE_STACKS_FOR_CHARGES: a proc removes one stack (ModStackAmount(-1))",
    "stacks-as-charges+dormant-charges": "USE_STACKS_FOR_CHARGES and ProcCharges>0: charges are initialised and gate "
                                         "procs (IsUsingCharges && !GetCharges) but are never dropped by procs",
}
