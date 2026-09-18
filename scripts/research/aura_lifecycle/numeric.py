"""Track I -- numeric boundaries of the mutable aura lifecycle.

Complements (never duplicates) ``docs/research/core-audit-corpora/numeric-boundaries.json``:
that corpus maps Core arithmetic against consumers (hasted period NUM-E-001 / NUM-R3-002 /
CSA-J-02, pandemic NUM-E-002, aligned duration NUM-E-005, partial ticks NUM-E-003).  This module
covers the *storage widths and integer/binary32 conversions of the lifecycle state itself*
(Aura / AuraEffect members, the per-update decrement, the tick bound, periodic-cost cadence)
and censuses which authored rows of the 69497 snapshot can reach each boundary.

Two layers:

* reproductions of Trinity arithmetic on explicit inputs (``Mirrors:`` lines; reproduce, never
  clean up; undefined behaviour raises :class:`FailClosed`);
* census functions over :class:`aura_lifecycle.context.AuraData` naming their population.

Standard library only.
"""

from __future__ import annotations

import math
import struct
from typing import Any, Iterable

from procs.enums import attr, aura

from . import FailClosed

F32_EXACT = 2 ** 24            # every integer <= 2^24 is exact in binary32
I32_MIN, I32_MAX = -(2 ** 31), 2 ** 31 - 1

# AuraEffect::CalculatePeriodic marks exactly these aura types periodic.
# Mirrors: SpellAuraEffects.cpp:966-985 (switch in AuraEffect::CalculatePeriodic)
PERIODIC_AURA_NAMES = (
    "OBS_MOD_POWER", "PERIODIC_DAMAGE", "PERIODIC_HEAL", "OBS_MOD_HEALTH", "PERIODIC_TRIGGER_SPELL",
    "PERIODIC_TRIGGER_SPELL_FROM_CLIENT", "PERIODIC_ENERGIZE", "PERIODIC_LEECH", "PERIODIC_HEALTH_FUNNEL",
    "PERIODIC_MANA_LEECH", "PERIODIC_DAMAGE_PERCENT", "POWER_BURN", "PERIODIC_DUMMY",
    "PERIODIC_TRIGGER_SPELL_WITH_VALUE",
)
PERIODIC_AURAS = frozenset(aura(n) for n in PERIODIC_AURA_NAMES)

ATTR5_SPELL_HASTE_AFFECTS_PERIODIC = attr("SPELL_ATTR5_SPELL_HASTE_AFFECTS_PERIODIC")
ATTR8_MELEE_HASTE_AFFECTS_PERIODIC = attr("SPELL_ATTR8_MELEE_HASTE_AFFECTS_PERIODIC")
ATTR8_HASTE_AFFECTS_DURATION = attr("SPELL_ATTR8_HASTE_AFFECTS_DURATION")
ATTR5_EXTRA_INITIAL_PERIOD = attr("SPELL_ATTR5_EXTRA_INITIAL_PERIOD")
ATTR13_PERIODIC_REFRESH_EXTENDS_DURATION = attr("SPELL_ATTR13_PERIODIC_REFRESH_EXTENDS_DURATION")
ATTR1_AURA_UNIQUE = attr("SPELL_ATTR1_AURA_UNIQUE")
ATTR5_AURA_UNIQUE_PER_CASTER = attr("SPELL_ATTR5_AURA_UNIQUE_PER_CASTER")
ATTR1_IS_CHANNELLED = attr("SPELL_ATTR1_IS_CHANNELLED")
ATTR1_IS_SELF_CHANNELLED = attr("SPELL_ATTR1_IS_SELF_CHANNELLED")


# --------------------------------------------------------------------------
# binary32 / fixed-width helpers
# --------------------------------------------------------------------------

def f32(x: float) -> float:
    """IEEE binary32 rounding (x86-64 SSE, ``-ffp-contract=off``)."""
    return struct.unpack("<f", struct.pack("<f", x))[0]


def int32_of_float(x: float) -> int:
    """C++ ``int32(float)``: truncation toward zero; out of range is UB -> FailClosed."""
    if math.isnan(x) or x >= 2 ** 31 or x < -(2 ** 31):
        raise FailClosed(f"int32 conversion of {x!r} is undefined behaviour")
    return int(x)


def uint8_of(x: int) -> int:
    """C++ implicit ``int32 -> uint8`` narrowing (modulo 256)."""
    return x & 0xFF


def int_times_float(value: int, factor: float) -> int:
    """``int32 x float`` then ``int32(...)``: the int is converted to binary32 first.

    Mirrors: Spell.cpp:3265 (``hitInfo.AuraDuration *= m_spellValue->DurationMul``),
    Spell.cpp:3282 and SpellAuras.cpp:960 (``int32(duration * ModCastingSpeed)``),
    SpellAuraEffects.cpp:1007/1009 (``int32(_period * ModCastingSpeed / ModHaste)``)
    """
    return int32_of_float(f32(f32(float(value)) * f32(factor)))


def calculate_pct_int(base: int, pct: float) -> int:
    """``T(base * float(pct) / 100.0f)`` for ``T = int32``.

    Mirrors: common/Utilities/Util.h:72-75
    """
    return int32_of_float(f32(f32(f32(float(base)) * f32(pct)) / 100.0))


# --------------------------------------------------------------------------
# lifecycle-state arithmetic (explicit inputs)
# --------------------------------------------------------------------------

def aura_duration_after(duration: int, diff: int) -> int:
    """One ``Aura::Update`` decrement: only positive durations move, floor 0.

    Mirrors: SpellAuras.cpp:857-862
    """
    if duration > 0:
        duration -= diff
        if duration < 0:
            duration = 0
    return duration


def total_ticks(max_duration: int, period: int, *, extra_initial: bool) -> int:
    """``AuraEffect::GetTotalTicks`` (integer division; permanent = 0 bound, unused).

    Mirrors: SpellAuraEffects.cpp:936-946
    """
    if period and max_duration != -1:
        if max_duration < 0 or period < 0:
            raise FailClosed("uint32(negative / negative) not modelled")
        n = max_duration // period
        return n + 1 if extra_initial else n
    return 0


def hasted_period(period: int, speed: float) -> int:
    """``_period = int32(_period * caster->m_unitData->ModCastingSpeed)`` (binary32 product).

    Note the guard ``if (_period)`` is evaluated on the *unhasted* value (SpellAuraEffects.cpp:996),
    so a hasted result of 0 keeps ``m_isPeriodic`` true.

    Mirrors: SpellAuraEffects.cpp:1004-1010
    """
    return int_times_float(period, speed)


def zero_period_outcome(permanent: bool) -> str:
    """What ``AuraEffect::Update`` does with ``m_isPeriodic && _period == 0``.

    ``GetTotalTicks`` returns 0 (``if (_period && ...)``); ``while (_periodicTimer >= 0)`` is always
    true: a non-permanent aura breaks on ``_ticksDone + 1 > 0`` (never ticks), a permanent one never
    breaks (``_periodicTimer -= 0``) -- an unbounded loop.

    Mirrors: SpellAuraEffects.cpp:1250-1262, 936-946
    """
    return "unbounded-loop" if permanent else "never-ticks"


def periodic_cost_timer(tcla: int, diff: int) -> tuple[int, bool]:
    """One ``Aura::Update`` step of ``m_timeCla`` (per-second periodic cost), for a positive duration
    and a caster that pays.  Returns ``(new m_timeCla, charged)``.  ``m_timeCla == 0`` disables
    the branch permanently (``if (m_timeCla)``) until ``RefreshDuration`` resets it to 1000.

    Mirrors: SpellAuras.cpp:864-872
    """
    if not tcla:
        return tcla, False
    if tcla > diff:
        return tcla - diff, False
    return tcla + 1000 - diff, True


def stack_after_mod(current: int, num: int, max_stack: int, authored_stack_amount: int) -> dict[str, Any]:
    """``Aura::ModStackAmount`` arithmetic (limit, removal, uint8 store, refresh predicate input).

    ``max_stack`` is ``CalcMaxStackAmount()`` (uint32 of int32 after SpellMod MaxAuraStacks).

    Mirrors: SpellAuras.cpp:1093-1111, 1056 (``SetStackAmount(uint8)``)
    """
    stack = current + num
    if num > 0 and stack > max_stack:
        stack = 1 if not authored_stack_amount else max_stack
    elif stack <= 0:
        return {"removed": True, "int32": stack, "stored": current}
    return {"removed": False, "int32": stack, "stored": uint8_of(stack), "wrapped": uint8_of(stack) != stack}


def charges_stored(max_charges: int) -> int:
    """``uint8(maxProcCharges)`` in ``Aura::CalcMaxCharges``.

    Mirrors: SpellAuras.cpp:1003-1015
    """
    return uint8_of(max_charges)


def tick_cap_loss(new_max: int, period: int, phase: int) -> bool:
    """Whether a refresh that keeps the periodic phase (pandemic / DONT_RESET_PERIODIC_TIMER) and
    resets ``_ticksDone`` loses the last timer-due tick to the ``GetTotalTicks`` bound.

    Ticks become due at ``period - phase, 2*period - phase, ...``; those at or before ``new_max``
    number ``floor((new_max + phase) / period)``; the bound admits ``floor(new_max / period)``.
    (Expiry removal runs after the effect update of the same diff, so a tick due exactly at
    ``new_max`` is still timer-due.)

    Mirrors: SpellAuras.cpp:976-992 (RefreshTimers -> RefreshDuration -> ResetTicks),
    SpellAuraEffects.cpp:1250-1264, 936-946
    """
    if period <= 0 or new_max < 0 or not 0 <= phase < period:
        raise FailClosed("tick_cap_loss: period > 0, new_max >= 0, 0 <= phase < period")
    return (new_max + phase) // period > new_max // period


# --------------------------------------------------------------------------
# census over the 69497 snapshot
# --------------------------------------------------------------------------

def _attrs(data, spell: int) -> tuple[int, ...]:
    misc = data.row("SpellMisc", spell)
    if not misc:
        return (0,) * 17
    return tuple(int(misc.get(f"Attributes_{i}", 0) or 0) & 0xFFFFFFFF for i in range(17))


def _has(attrs: tuple[int, ...], key: tuple[int, int]) -> bool:
    word, bit = key
    return bool(attrs[word] & bit)


def _base_duration(data, spell: int) -> dict | None:
    misc = data.row("SpellMisc", spell)
    if not misc or not misc.get("DurationIndex"):
        return None
    return data.duration(misc["DurationIndex"])


def _count(spells: Iterable[int], pops: dict[str, frozenset[int]]) -> dict[str, int]:
    s = set(spells)
    return {name: len(s & pop) for name, pop in sorted(pops.items())}


def _witnesses(spells: Iterable[int], pops: dict[str, frozenset[int]], name, limit: int = 6, skew=None) -> list[dict]:
    s = sorted(set(spells))
    player = [x for x in s if x in pops.get("player", ())]
    rest = [x for x in s if x not in pops.get("player", ())]
    return [{"spell": x, "name": name(x), "player": x in pops.get("player", ()),
             "build_skew": bool(skew(x)) if skew else None} for x in (player + rest)[:limit]]


def census(data, pops: dict[str, frozenset[int]], provider_effects: list[dict], name=lambda s: "", skew=None) -> list[dict]:
    """Every lifecycle numeric boundary reachable from authored rows, with counts per population."""
    import functools
    wit = functools.partial(_witnesses, skew=skew)
    provider_spells = sorted({r["spell"] for r in provider_effects})
    out: list[dict] = []

    # --- uint8 stack store ---------------------------------------------------
    stack_over = [s for s in provider_spells
                  if (r := data.row("SpellAuraOptions", s)) and int(r.get("CumulativeAura") or 0) > 255]
    stack_values = sorted({int(data.row("SpellAuraOptions", s)["CumulativeAura"]) for s in stack_over})
    out.append({
        "id": "NB-I-01", "quantity": "aura stack amount store",
        "conversion": "int32 stackAmount (clamped to CalcMaxStackAmount) -> uint8 m_stackAmount (modulo 256)",
        "coords": ["SpellAuras.h:411", "SpellAuras.cpp:1093-1111", "SpellAuras.cpp:1056", "SpellAuras.cpp:481"],
        "boundary": "authored StackAmount (SpellAuraOptions.CumulativeAura) > 255: the 256th stack stores 0",
        "counts": _count(stack_over, pops), "authored_values": stack_values,
        "witnesses": wit(stack_over, pops, name),
        "evidence": ["db2-fact", "trinity-consumer"], "numeric_evidence": "trinity-only",
        "observable": "stack count wraps 255 -> 0 (then 1) in Trinity; Retail storage width unknown",
    })

    charge_over = [s for s in provider_spells
                   if (r := data.row("SpellAuraOptions", s)) and int(r.get("ProcCharges") or 0) > 255]
    out.append({
        "id": "NB-I-02", "quantity": "aura proc charges store",
        "conversion": "uint32 maxProcCharges (after SpellMod ProcCharges) -> uint8 (CalcMaxCharges return), uint8 m_procCharges",
        "coords": ["SpellAuras.cpp:1003-1015", "SpellAuras.cpp:1017-1038", "SpellAuras.h:410"],
        "boundary": "authored ProcCharges > 255 (or SpellMod-raised) truncates modulo 256",
        "counts": _count(charge_over, pops), "witnesses": wit(charge_over, pops, name),
        "evidence": ["db2-fact", "trinity-consumer"], "numeric_evidence": "trinity-only",
        "observable": "charges; 0 after truncation means 'not using charges' (m_isUsingCharges = charges != 0)",
    })

    # --- binary32 duration products ------------------------------------------
    inexact: list[int] = []
    over_exact: list[int] = []
    max_finite = 0
    negative_non_permanent: list[int] = []
    for s in provider_spells:
        d = _base_duration(data, s)
        if not d:
            continue
        for col in ("Duration", "MaxDuration"):
            v = int(d.get(col) or 0)
            if v > max_finite:
                max_finite = v
            if v < -1:
                negative_non_permanent.append(s)
            if v > F32_EXACT:
                over_exact.append(s)
                if f32(float(v)) != float(v):
                    inexact.append(s)
    out.append({
        "id": "NB-I-03", "quantity": "aura duration through binary32 products",
        "conversion": "int32 duration -> binary32 x binary32 factor -> int32 truncation "
                      "(DurationMul, ATTR8 non-periodic haste, RefreshDuration withMods, CalculatePct 130)",
        "coords": ["Spell.cpp:3265", "Spell.cpp:3282", "SpellAuras.cpp:957-960", "Spell.cpp:3287", "Util.h:72-75"],
        "boundary": "durations > 2^24 ms (4 h 39 m 37 s) lose integer exactness; inexact rows change even at factor 1.0",
        "counts": _count(over_exact, pops), "counts_inexact_in_binary32": _count(inexact, pops),
        "max_finite_authored_ms": max_finite,
        "witnesses": wit(inexact or over_exact, pops, name),
        "evidence": ["db2-fact", "trinity-consumer"], "numeric_evidence": "direct-consumer-reproduced",
        "observable": "max duration of very long auras after any float product",
    })
    out.append({
        "id": "NB-I-04", "quantity": "negative non-permanent authored durations",
        "conversion": "SpellInfo::GetDuration abs() / IsPermanent (== -1 only)",
        "coords": ["SpellInfo.cpp:3986-3998", "SpellAuras.h:227"],
        "boundary": "Duration < -1", "counts": _count(negative_non_permanent, pops),
        "witnesses": wit(negative_non_permanent, pops, name),
        "evidence": ["db2-fact"], "numeric_evidence": "source-backed",
        "observable": "edge to Track B (duration); listed here only as a width/sign boundary",
    })

    # --- periodic effects ----------------------------------------------------
    periodic = [r for r in provider_effects if r["aura"] in PERIODIC_AURAS]
    zero_period: list[int] = []
    negative_period: list[int] = []
    hasted_small: list[dict] = []
    remainder: list[int] = []
    extra_initial: list[int] = []
    for r in periodic:
        s = r["spell"]
        eff = data.effects(s)[[e["EffectIndex"] for e in data.effects(s)].index(r["index"])]
        period = int(eff.get("EffectAuraPeriod") or 0)
        a = _attrs(data, s)
        if period == 0:
            zero_period.append(s)
            continue
        if period < 0:
            negative_period.append(s)
            continue
        hasted = (_has(a, ATTR5_SPELL_HASTE_AFFECTS_PERIODIC) or _has(a, ATTR8_MELEE_HASTE_AFFECTS_PERIODIC)
                  or _has(a, ATTR1_IS_CHANNELLED) or _has(a, ATTR1_IS_SELF_CHANNELLED))
        if hasted and period <= 10:
            hasted_small.append({"spell": s, "index": r["index"], "period": period,
                                 "zero_below_speed": 1.0 / period})
        if _has(a, ATTR5_EXTRA_INITIAL_PERIOD):
            extra_initial.append(s)
        d = _base_duration(data, s)
        if d:
            dur = int(d.get("Duration") or 0)
            if dur > 0 and dur % period:
                remainder.append(s)
    out.append({
        "id": "NB-I-05", "quantity": "periodic effect with authored period 0",
        "conversion": "CalculatePeriodic: _period == 0 -> m_isPeriodic = false (never ticks unless a script sets it)",
        "coords": ["SpellAuraEffects.cpp:995-1013", "SpellAuraEffects.cpp:988"],
        "boundary": "EffectAuraPeriod == 0 on a periodic aura type", "counts": _count(zero_period, pops),
        "witnesses": wit(zero_period, pops, name),
        "evidence": ["db2-fact", "trinity-consumer"], "numeric_evidence": "trinity-only",
        "observable": "no ticks (edge to Track D; scripts may assign a period in DoEffectCalcPeriodic)",
    })
    out.append({
        "id": "NB-I-06", "quantity": "hasted period truncating to zero",
        "conversion": "int32(period x binary32 ModCastingSpeed) evaluated after the `if (_period)` guard",
        "coords": ["SpellAuraEffects.cpp:995-1013", "SpellAuraEffects.cpp:1250-1262", "SpellAuraEffects.cpp:936-946"],
        "boundary": "period x speed < 1 -> _period 0 with m_isPeriodic true: non-permanent never ticks, permanent loops unboundedly",
        "counts": _count([h["spell"] for h in hasted_small], pops),
        "rows": sorted(hasted_small, key=lambda h: (h["spell"], h["index"])),
        "negative_period_counts": _count(negative_period, pops),
        "evidence": ["db2-fact", "trinity-consumer"], "numeric_evidence": "trinity-only",
        "observable": "latent: needs ModCastingSpeed < 1/period (listed rows: hasted periodic effects with period <= 10 ms)",
    })
    out.append({
        "id": "NB-I-07", "quantity": "tick bound floor(maxDuration / period)",
        "conversion": "uint32 totalTicks = maxDuration / _period (integer), +1 with ATTR5_EXTRA_INITIAL_PERIOD",
        "coords": ["SpellAuraEffects.cpp:936-946", "SpellAuraEffects.cpp:1259-1260"],
        "boundary": "authored Duration % period != 0 (fresh application: remainder after the last tick, no partial tick)",
        "counts": _count(remainder, pops), "witnesses": wit(remainder, pops, name),
        "evidence": ["db2-fact", "trinity-consumer"], "numeric_evidence": "direct-consumer-reproduced",
        "observable": "tick count; Core audit NUM-E-003 (Core partial tick) is the divergent consumer",
    })
    out.append(pandemic_tick_cap(data, pops, provider_effects, name, skew))
    out.append({
        "id": "NB-I-09", "quantity": "extra initial period (tick on first owner update)",
        "conversion": "ResetPeriodic: _periodicTimer = _period -> first AuraEffect::Update ticks immediately",
        "coords": ["SpellAuraEffects.cpp:949-959", "SpellAuraEffects.cpp:1258"],
        "boundary": "first tick timestamp = first owner _UpdateSpells after application (world-tick quantized)",
        "counts": _count(extra_initial, pops), "witnesses": wit(extra_initial, pops, name),
        "evidence": ["db2-fact", "trinity-consumer"], "numeric_evidence": "trinity-only",
        "observable": "initial tick in the same world tick as application iff the owner updates after the application point",
    })

    # --- periodic costs ------------------------------------------------------
    cost_spells: list[int] = []
    provider_set = set(provider_spells)
    src = data.source
    if src.has("SpellPower"):
        for spell, mps, pct in src.project("SpellPower", ("SpellID", "ManaPerSecond", "PowerPctPerSecond")):
            if spell in provider_set and (int(mps or 0) != 0 or float(pct or 0) > 0):
                cost_spells.append(spell)
    out.append({
        "id": "NB-I-10", "quantity": "per-second periodic cost timer m_timeCla",
        "conversion": "int32 m_timeCla: charge when m_timeCla <= diff, then m_timeCla += 1000 - diff; 0 disables",
        "coords": ["SpellAuras.cpp:864-872", "SpellAuras.cpp:488-495", "SpellAuras.cpp:969"],
        "boundary": "diff == m_timeCla + 1000 stores 0 -> costs stop until RefreshDuration; diff > m_timeCla + 1000 "
                    "goes negative -> charge every update until caught up (one charge per update, no catch-up loop)",
        "counts": _count(cost_spells, pops), "witnesses": wit(cost_spells, pops, name),
        "evidence": ["db2-fact", "trinity-consumer"], "numeric_evidence": "trinity-only",
        "observable": "power drained per second by channel/aura upkeep (SpellPower.ManaPerSecond / PowerPctPerSecond)",
    })
    return out


COMBO_POINTS = (1, 2, 3, 4, 5)   # CP spent by a finisher; wider ranges (talents) not modelled


def pandemic_new_max(f, combo_points: int | None) -> tuple[int, int, int]:
    """(hit, M, newMax) of a same-caster pandemic refresh on the refreshed-duration branch, unhasted, no spell mods.

    ``hit`` = Aura::CalcMaxDuration(spell, caster, &m_powerCost) (Spell.cpp:3216: combo points when spent);
    ``M`` = Aura::CalcMaxDuration(caster) with no power costs, i.e. the minimum duration (SpellAuras.cpp:976-979);
    ``newMax`` = min(hit + M, CalculatePct(hit, 130)) (Spell.cpp:3284-3288, Track B AL-R-B-07).

    Mirrors: Object.cpp:1707-1725 (via duration.calc_spell_duration), Spell.cpp:3284-3288
    """
    from .duration import calc_spell_duration
    hit = calc_spell_duration(f, combo_points)
    m = calc_spell_duration(f, None)
    return hit, m, min(hit + m, calculate_pct_int(hit, 130))


def pandemic_tick_cap(data, pops, provider_effects, name=lambda s: "", skew=None) -> dict:
    """NB-I-08 (R2-06): rows where a phase-keeping pandemic refresh can drop the last timer-due tick.

    Population: periodic provider effects of spells on Track B's refreshed-duration pandemic branch
    (``refresh.classify(...)["carry"] == "pandemic-reads-refreshed-duration"``), finite positive duration, unhasted
    period (haste 1.0 assumed: with ATTR5/ATTR8 haste the period changes and any row can reach the boundary).
    Channels are excluded and counted separately: a same-caster recast interrupts the running channel
    (Unit.cpp:3113-3117) and Spell::cancel removes its auras with CANCEL (Spell.cpp:3636-3647) before the hit,
    so the recast creates a new aura instead of refreshing.  Combo-point records are evaluated for 1..5 CP.
    """
    from .duration import empower_spells, facts
    from .refresh import classify
    emp = empower_spells(data.source)
    by_spell: dict[int, list[dict]] = {}
    for r in provider_effects:
        if r["aura"] in PERIODIC_AURAS:
            by_spell.setdefault(r["spell"], []).append(r)
    rows, channels, finite_branch = [], [], []
    for spell, effs in sorted(by_spell.items()):
        f = facts(data, spell, emp)
        c = classify(f)
        if c["branch"] == "self-channel-cancel-then-create" and f.has(ATTR13_PERIODIC_REFRESH_EXTENDS_DURATION):
            channels.append(spell)
            continue
        if c["carry"] != "pandemic-reads-refreshed-duration":
            continue
        from .duration import spellinfo_get_duration
        if spellinfo_get_duration(f) <= 0:
            continue
        finite_branch.append(spell)
        e = f.duration_entry
        cps = COMBO_POINTS if (e and e.per_resource and e.duration != e.max_duration) else (None,)
        for eff in effs:
            period = next(int(x["EffectAuraPeriod"] or 0) for x in data.effects(spell) if x["EffectIndex"] == eff["index"])
            if period <= 0:
                continue
            losing = []
            for cp in cps:
                hit, m, new_max = pandemic_new_max(f, cp)
                if new_max % period:
                    losing.append({"cp": cp, "hit": hit, "M": m, "new_max": new_max,
                                   "phase_threshold": period - new_max % period})
            if losing:
                rows.append({"spell": spell, "index": eff["index"], "period": period, "cases": losing,
                             "name": name(spell), "player": spell in pops.get("player", ()),
                             "build_skew": bool(skew(spell)) if skew else None})
    return {
        "id": "NB-I-08", "quantity": "tick bound after a phase-keeping pandemic refresh (unhasted)",
        "conversion": "ResetTicks + kept _periodicTimer phase vs floor(newMax / period), newMax = min(hit + M, trunc(1.3f*hit))",
        "coords": ["SpellAuras.cpp:976-992", "SpellAuras.cpp:970-973", "SpellAuraEffects.cpp:1250-1264", "Spell.cpp:3284-3288"],
        "boundary": "(newMax mod period) + phase >= period drops the last timer-due tick",
        "assumptions": ["haste 1.0 (ModCastingSpeed/ModHaste = 1): counts are valid only unhasted",
                        "no Duration/Period spell mods, no target duration mods / DR",
                        "combo-point records evaluated at 1..5 CP (hit = min + perResource*CP, M = min)",
                        "channels excluded: same-caster recast is cancel-then-create (Unit.cpp:3113-3117, Spell.cpp:3636-3647)"],
        "population_note": "periodic effects of Track B refreshed-duration pandemic spells with finite positive duration",
        "counts": _count([r["spell"] for r in rows], pops),
        "branch_periodic_finite_counts": _count(finite_branch, pops),
        "branch_total_ref": "carryover.json census.carry[pandemic-reads-refreshed-duration] (Track B; this record counts only its periodic, finite-duration subset)",
        "channels_excluded": _count(channels, pops),
        "channels_note": "ATTR13 periodic self-channels on Track B branch self-channel-cancel-then-create (not refreshable by the caster's recast)",
        "channel_player_spells": sorted(set(channels) & pops.get("player", frozenset())),
        "rows": sorted(rows, key=lambda t: (not t["player"], t["spell"], t["index"]))[:40],
        "rows_total": len(rows),
        "evidence": ["db2-fact", "trinity-consumer", "trinity-probe"], "numeric_evidence": "trinity-only",
        "confirms": ["AL-D-D-04", "AL-T-D-19"],
        "observable": "one fewer tick than the timer schedules after a refresh at a phase >= phase_threshold",
    }
