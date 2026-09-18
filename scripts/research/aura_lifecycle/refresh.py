"""Track B -- reapplication / refresh / pandemic carryover.

Answers "what does a second application of the same spell do to the live aura" for
the Trinity consumer, as exact millisecond timelines, and classifies every provider
spell by the *branch* its reapplication takes.  Identity (which existing aura is
"the same one": caster GUID, cast item, multislot) belongs to track A; this module
only needs the three identity outcomes that decide whether the refresh path runs
at all, and cites A for the rest.

The refresh path, proven from call sites (TrinityCore @ 7f3d43b):

    Spell::DoSpellEffectHit                      Spell.cpp:3226
      Aura::TryRefreshStackOrCreate              SpellAuras.cpp:350
        Unit::_TryStackingOrRefreshingExistingAura   Unit.cpp:3386
          (multislot -> nullptr -> new Aura)     Unit.cpp:3395
          base amounts replaced / accumulated    Unit.cpp:3409-3425
          Aura::ModStackAmount(StackAmount, DEFAULT, resetPeriodicTimer)  Unit.cpp:3441
            SetStackAmount -> amounts recalculated (ROLLING_PERIODIC here)  SpellAuras.cpp:1117
            if refresh: RefreshTimers(resetPeriodicTimer)                   SpellAuras.cpp:1119-1121
              m_maxDuration = CalcMaxDuration()   (aura caster, NO power costs)
              ATTR13 -> resetPeriodicTimer = false
              RefreshDuration() -> m_duration = m_maxDuration, ticks reset
              AuraEffect::CalculatePeriodic(caster, reset, false) -> ResetPeriodic
            SetCharges(CalcMaxCharges())
      ... ModSpellDuration / DurationMul / haste (Spell.cpp:3262-3282)
      ATTR13: min(hit + HitAura->GetDuration(), CalculatePct(hit, 130))    Spell.cpp:3284-3288
      if hit != GetMaxDuration(): SetMaxDuration(hit); SetDuration(hit)     Spell.cpp:3294-3298

so the ATTR13 branch reads ``GetDuration()`` *after* RefreshDuration already
overwrote the remaining time with the recalculated maximum.  See
:func:`pandemic_models` and the probe ``tools/tc_aura_duration_probe``.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field, replace
from typing import Any

from . import FailClosed
from .duration import (
    ATTR1_AURA_UNIQUE,
    ATTR3_DOT_STACKING_RULE,
    ATTR5_AURA_UNIQUE_PER_CASTER,
    ATTR5_EXTRA_INITIAL_PERIOD,
    ATTR10_ROLLING_PERIODIC,
    ATTR13_PERIODIC_REFRESH_EXTENDS_DURATION,
    ATTR15_AURA_DOES_NOT_REFRESH_SIMC,
    Caster,
    HitInputs,
    SpellFacts,
    calc_max_duration,
    calculate_pct,
    cdiv,
    hit_commit,
    hit_quote,
    wrap_int32,
)

MULTISLOT_IDS = (55849, 40075, 44413)  # SpellInfo.cpp:1803-1806 (Power Spark, Fel Flak Fire, Incanter's Absorption)


# --------------------------------------------------------------------------
# identity outcome (only what decides whether the refresh path runs; A owns the rest)
# --------------------------------------------------------------------------
def is_multislot(f: SpellFacts) -> bool:
    """Mirrors: SpellInfo.cpp:1803-1806 ``IsMultiSlotAura``."""
    return f.passive or f.spell in MULTISLOT_IDS


def stackable_on_one_slot_with_different_casters(f: SpellFacts) -> bool:
    """Mirrors: SpellInfo.cpp:1808-1812."""
    return f.stack_amount > 1 and not f.channeled and not f.has(ATTR3_DOT_STACKING_RULE)


def lookup_scope(f: SpellFacts) -> str:
    """Which existing aura ``GetOwnedAura`` finds for a reapplication.  Mirrors: Unit.cpp:3395-3402."""
    if is_multislot(f):
        return "never-found (multislot: every application creates a new Aura)"
    if stackable_on_one_slot_with_different_casters(f):
        return "any-caster (one shared aura; casterGUID ignored)"
    return "same-caster (other casters create their own Aura; coexist-or-replace is track A)"


# --------------------------------------------------------------------------
# minimal aura state (only what duration/refresh/tick scheduling touch)
# --------------------------------------------------------------------------
@dataclass
class EffectState:
    base_period: int          # SpellEffectInfo::ApplyAuraPeriod
    periodic: bool = True     # m_isPeriodic (aura-type switch in CalculatePeriodic is track D)
    period: int = 0           # _period after mods/haste
    timer: int = 0            # _periodicTimer
    ticks: int = 0            # _ticksDone


@dataclass
class AuraState:
    spell: int
    duration: int
    max_duration: int
    stacks: int
    effects: list[EffectState]
    passive: bool = False
    generation: int = 0       # new Aura object -> generation + 1 (A/I own identity; kept for timelines)

    @property
    def permanent(self) -> bool:
        """Mirrors: SpellAuras.h:227 ``IsPermanent`` (GetMaxDuration() == -1)."""
        return self.max_duration == -1

    def total_ticks(self, e: EffectState, extra_initial: bool) -> int:
        """Mirrors: SpellAuraEffects.cpp:938-947 ``AuraEffect::GetTotalTicks`` (uint32 division)."""
        total = 0
        if e.period and not self.permanent:
            total = (self.max_duration & 0xFFFFFFFF) // e.period if self.max_duration >= 0 else 0
            if self.max_duration < 0:
                raise FailClosed("negative non-permanent max duration in GetTotalTicks")
            if extra_initial:
                total += 1
        return total

    def snapshot(self) -> dict[str, Any]:
        return {"duration": self.duration, "max_duration": self.max_duration, "stacks": self.stacks,
                "generation": self.generation,
                "effects": [{"period": e.period, "timer": e.timer, "ticks": e.ticks} for e in self.effects]}


def hastened_period(f: SpellFacts, base_period: int, caster: Caster | None) -> int:
    """Period after ``CalculatePeriodic`` mods (SpellMod Period, then haste; CalcPeriodic scripts not modelled).

    Mirrors: SpellAuraEffects.cpp:990-1010
    """
    from .duration import (ATTR5_SPELL_HASTE_AFFECTS_PERIODIC, ATTR8_MELEE_HASTE_AFFECTS_PERIODIC,
                           mod_spell_duration_time, mul_int_float)
    period = base_period
    if period and caster is not None and caster.mod_owner:
        period = caster.period_mods.apply(period)
    if period and caster is not None:
        if f.channeled:
            period = mod_spell_duration_time(f, caster, period)
        elif f.has(ATTR5_SPELL_HASTE_AFFECTS_PERIODIC):
            period = mul_int_float(period, caster.mod_casting_speed)
        elif f.has(ATTR8_MELEE_HASTE_AFFECTS_PERIODIC):
            period = mul_int_float(period, caster.mod_haste)
    return period


def reset_periodic(f: SpellFacts, e: EffectState, reset_timer: bool) -> None:
    """Mirrors: SpellAuraEffects.cpp:949-959 ``AuraEffect::ResetPeriodic``."""
    e.ticks = 0
    if reset_timer:
        e.timer = 0
        if f.has(ATTR5_EXTRA_INITIAL_PERIOD):
            e.timer = e.period


def calculate_periodic(f: SpellFacts, e: EffectState, caster: Caster | None, reset_timer: bool) -> None:
    """Non-load branch of ``AuraEffect::CalculatePeriodic`` (period recomputed from the live caster).

    Mirrors: SpellAuraEffects.cpp:961-1031
    """
    e.period = e.base_period  # _period = ApplyAuraPeriod, even when the aura type is not periodic
    if not e.periodic:
        return
    e.period = hastened_period(f, e.base_period, caster)
    if not e.period:
        e.periodic = False  # "prevent infinite loop on Update" (SpellAuraEffects.cpp:1012-1013)
    reset_periodic(f, e, reset_timer)


def create(f: SpellFacts, caster: Caster | None, periodic_effects: tuple[bool, ...] | None = None,
           stack_amount: int = 1) -> AuraState:
    """``Aura::Aura`` duration/stack init + ``AuraEffect`` ctor ``CalculatePeriodic(caster, true, false)``.

    Mirrors: SpellAuras.cpp:475-499; SpellAuraEffects.cpp:736-746
    """
    mx = calc_max_duration(f, caster)
    periodic_effects = periodic_effects if periodic_effects is not None else tuple(bool(p) for p in f.periods)
    effects = [EffectState(p, periodic) for p, periodic in zip(f.periods, periodic_effects)]
    st = AuraState(f.spell, mx, mx, stack_amount, effects, passive=f.passive)
    for e in effects:
        calculate_periodic(f, e, caster, True)
    return st


def refresh_timers(f: SpellFacts, st: AuraState, caster: Caster | None, reset_periodic_timer: bool) -> dict[str, Any]:
    """Mirrors: SpellAuras.cpp:976-990 ``Aura::RefreshTimers`` + 952-974 ``RefreshDuration(false)``."""
    st.max_duration = calc_max_duration(f, caster)  # CalcMaxDuration() -> GetCaster(), no power costs
    if f.has(ATTR13_PERIODIC_REFRESH_EXTENDS_DURATION):
        reset_periodic_timer = False
    st.duration = st.max_duration  # RefreshDuration(): SetDuration(GetMaxDuration())
    for e in st.effects:
        e.ticks = 0  # ResetTicks
    for e in st.effects:
        calculate_periodic(f, e, caster, reset_periodic_timer)
    return {"step": "RefreshTimers", "max_duration": st.max_duration, "duration": st.duration,
            "reset_periodic_timer": reset_periodic_timer, "coords": "SpellAuras.cpp:976-990"}


def mod_stack_amount(f: SpellFacts, st: AuraState, caster: Caster | None, num: int,
                     reset_periodic_timer: bool, max_stack: int | None = None) -> tuple[bool, dict[str, Any]]:
    """Positive-``num`` path of ``Aura::ModStackAmount`` (stack-to-zero removal is track C/E).

    ``max_stack`` = ``CalcMaxStackAmount()`` (StackAmount after MaxAuraStacks spell-mods); default
    unmodded.  Returns ``(refresh, step)``.

    Mirrors: SpellAuras.cpp:1093-1124
    """
    if num <= 0:
        raise FailClosed("stack decrease / removal path belongs to tracks C/E")
    stack = st.stacks + num
    mx = f.stack_amount if max_stack is None else max_stack
    if stack > mx:
        stack = 1 if not f.stack_amount else mx
    refresh = stack >= st.stacks and bool(
        f.stack_amount or (not f.has(ATTR1_AURA_UNIQUE) and not f.has(ATTR5_AURA_UNIQUE_PER_CASTER)))
    old = st.stacks
    st.stacks = stack
    step = {"step": "ModStackAmount", "stacks": [old, stack], "refresh": refresh,
            "coords": "SpellAuras.cpp:1093-1124"}
    if refresh:
        step["refresh_timers"] = refresh_timers(f, st, caster, reset_periodic_timer)
    return refresh, step


def reset_periodic_flag(f: SpellFacts, triggered_dont_reset: bool = False) -> bool:
    """Mirrors: Spell.cpp:3240 ``(StackAmount < 2) && !(TRIGGERED_DONT_RESET_PERIODIC_TIMER)``."""
    return f.stack_amount < 2 and not triggered_dont_reset


def apply_hit(f: SpellFacts, st: AuraState | None, h: HitInputs, *, aura_caster: Caster | None = None,
              triggered_dont_reset: bool = False, effect_mask_matches: bool = True,
              periodic_effects: tuple[bool, ...] | None = None) -> tuple[AuraState, dict[str, Any]]:
    """One unit hit of one cast against the (same-caster) existing aura ``st`` (or none).

    ``aura_caster`` is what ``Aura::GetCaster()`` returns inside RefreshTimers (default: the hit's
    caster).  Returns the new state and a step log.

    Mirrors: Spell.cpp:3226-3298; SpellAuras.cpp:350-387; Unit.cpp:3386-3446
    """
    aura_caster = aura_caster if aura_caster is not None else h.caster
    quoted, steps, immune = hit_quote(f, h)
    log: dict[str, Any] = {"quote": steps}
    if immune:
        log["result"] = "immune (diminished to 0)"
        if st is None:
            raise FailClosed("DR-immune first application: no aura")
        return st, log
    refresh = False
    if st is not None and not is_multislot(f) and effect_mask_matches:
        st = replace(st, effects=[replace(e) for e in st.effects])
        refresh = True
        _, step = mod_stack_amount(f, st, aura_caster, 1, reset_periodic_flag(f, triggered_dont_reset))
        log["refresh_path"] = step
    else:
        gen = (st.generation + 1) if st is not None else 0
        st = create(f, h.caster, periodic_effects)
        st.generation = gen
        log["created"] = {"max_duration": st.max_duration, "coords": "SpellAuras.cpp:494-495"}
    if not h.hastened_periods:
        # Spell.cpp:3274-3276 iterates GetAuraEffects() -> GetPeriod() of the live aura
        h = replace(h, hastened_periods=tuple(e.period for e in st.effects))
    d, dur, mx, commit = hit_commit(f, h, quoted, refresh=refresh,
                                    aura_duration_now=st.duration, aura_max_duration_now=st.max_duration)
    st.duration, st.max_duration = dur, mx
    log["commit"] = commit
    log["refresh"] = refresh
    return st, log


def update(f: SpellFacts, st: AuraState, diff: int, now: int) -> tuple[list[dict[str, Any]], bool]:
    """One ``Unit::_UpdateSpells`` pass for this aura: ``Aura::Update`` (duration), effect updates
    (ticks), then the expiry sweep.  Target-map / periodic costs not modelled.

    Mirrors: Unit.cpp:2976-2991; SpellAuras.cpp:817-853 (UpdateOwner), 855-905 (Update);
    SpellAuraEffects.cpp:1250-1276 (AuraEffect::Update)
    """
    events: list[dict[str, Any]] = []
    if st.duration > 0:
        st.duration -= diff
        if st.duration < 0:
            st.duration = 0
    extra = f.has(ATTR5_EXTRA_INITIAL_PERIOD)
    for idx, e in enumerate(st.effects):
        if not e.periodic or (st.duration < 0 and not st.passive and not st.permanent):
            continue
        total = st.total_ticks(e, extra)
        e.timer += diff
        while e.timer >= e.period:
            e.timer -= e.period
            if not st.permanent and (e.ticks + 1) > total:
                events.append({"t": now, "event": "tick-suppressed", "effect": idx, "ticks": e.ticks,
                               "total": total, "generation": st.generation})
                break
            e.ticks += 1
            events.append({"t": now, "event": "tick", "effect": idx, "n": e.ticks, "total": total,
                           "generation": st.generation})
    expired = st.duration == 0  # IsExpired: !GetDuration() && !m_dropEvent
    if expired:
        events.append({"t": now, "event": "expire", "generation": st.generation})
    return events, expired


def timeline(f: SpellFacts, applications: list[tuple[int, HitInputs]], *, end: int, step: int = 1,
             periodic_effects: tuple[bool, ...] | None = None, **apply_kw) -> dict[str, Any]:
    """Exact-ms timeline: world updates every ``step`` ms (diff = step), applications at given ms.

    The order of a spell hit relative to ``_UpdateSpells`` inside one map tick is track I's
    question; here an application at ``t`` is processed after the update that ends at ``t``, and
    that assumption is written into the output (``same_ms_order``).
    """
    apps = sorted(applications, key=lambda a: a[0])
    st: AuraState | None = None
    events: list[dict[str, Any]] = []
    t = 0
    ai = 0
    while ai < len(apps) and apps[ai][0] == 0:
        st, log = apply_hit(f, st, apps[ai][1], periodic_effects=periodic_effects, **apply_kw)
        events.append({"t": 0, "event": "apply", "refresh": log["refresh"], "state": st.snapshot(), "log": log})
        ai += 1
    while t < end:
        t += step
        if st is not None:
            ev, expired = update(f, st, step, t)
            events.extend(ev)
            if expired:
                events.append({"t": t, "event": "removed", "state": st.snapshot()})
                st = None
        while ai < len(apps) and apps[ai][0] <= t:
            st, log = apply_hit(f, st, apps[ai][1], periodic_effects=periodic_effects, **apply_kw)
            events.append({"t": t, "event": "apply", "refresh": log["refresh"], "state": st.snapshot(), "log": log})
            ai += 1
    return {"spell": f.spell, "step_ms": step, "same_ms_order": "update(t) then application(t) [assumption; track I]",
            "events": events, "final": st.snapshot() if st else None}


# --------------------------------------------------------------------------
# pandemic: competing models
# --------------------------------------------------------------------------
def pandemic_models(hit: int, remaining: int, recalculated_max: int) -> dict[str, dict[str, Any]]:
    """New duration after a refresh of an ATTR13 aura with ``remaining`` ms left, under each model.

    * ``trinity-7f3d43b``: what the pinned code does -- the ATTR13 branch sees
      ``GetDuration() == recalculated_max`` (RefreshDuration already ran).
    * ``carry-before-refresh``: the same Spell.cpp formula fed the *pre-refresh* remaining
      (the evident intent of commit 92773e207c; equivalent to ``hit + min(r, 0.3*hit)`` up to rounding).
    * ``trinity-pre-92773e2``: RefreshTimers-era code (removed 2025-03-23): RefreshTimers set
      ``P = max(r, min(CalculatePct(M, 30.f), r) + M)``, then the (unchanged) Spell.cpp commit
      overwrote max+duration with ``hit`` whenever ``hit != P`` -- so on the Spell path any nonzero
      carry was discarded (``value``); ``refresh_timers_value`` is P (the Unit::AddAura path keeps it).
    * ``simc``: action.cpp:4603 ``max(r, min(0.3*D, r) + D)`` (double arithmetic).
    * ``core-63f3a49``: aura_state.rs:1143-1199 ``D + min(r, floor(3D/10))``, then ``max`` with the
      old expiry (navigation only).
    """
    legacy = max(remaining, min(calculate_pct(recalculated_max, 30.0), remaining) + recalculated_max)
    return {
        "trinity-7f3d43b": {"value": min(wrap_int32(hit + recalculated_max), calculate_pct(hit, 130)),
                            "evidence": "trinity-consumer", "coords": "Spell.cpp:3284-3288 after SpellAuras.cpp:976-990"},
        "carry-before-refresh": {"value": min(wrap_int32(hit + remaining), calculate_pct(hit, 130)),
                                 "evidence": "structural-inference", "coords": "Spell.cpp:3284-3288 with pre-refresh r"},
        "trinity-pre-92773e2": {"value": hit if hit != legacy else legacy, "refresh_timers_value": legacy,
                                "evidence": "legacy-only",
                                "coords": "git 92773e207c^:SpellAuras.cpp:961-975 + Spell.cpp:3275-3279"},
        "simc": {"value": max(remaining, int(min(hit * 0.3, remaining) + hit)),
                 "evidence": "simc-consumer", "coords": "simc engine/action/action.cpp:4603"},
        "core-63f3a49": {"value": max(hit + min(remaining, cdiv(hit * 3, 10)), remaining),
                         "evidence": "core-navigation", "coords": "core crates/combat/src/aura_state.rs:1143-1199"},
    }


def rolling_periodic_amount(new_amount: float, old_amount: float, old_estimated: float | None,
                            old_remaining_ticks: int, total_ticks: int) -> float:
    """ATTR10_ROLLING_PERIODIC inside ``CalculateAmount`` on the refresh path.

    Runs from ``SetStackAmount`` (before RefreshTimers), so ``this`` effect is the *old* one:
    ``total_ticks`` = old max / period, remaining = total - ticksDone.  Adds the old
    *estimated* (bonus-done-applied) amount when present.  Result before stack multiply/round.

    Mirrors: SpellAuraEffects.cpp:829-840
    """
    if not total_ticks:
        return new_amount
    base = old_estimated if old_estimated is not None else old_amount
    return new_amount + base * float(old_remaining_ticks) / float(total_ticks)


# --------------------------------------------------------------------------
# per-spell classification
# --------------------------------------------------------------------------
def spell_hit_branch(f: SpellFacts) -> str:
    """Branch a found-aura reapplication takes once ``_TryStackingOrRefreshingExistingAura`` runs.

    Mirrors: Unit.cpp:3394-3441; SpellAuras.cpp:1093-1124
    """
    uniq = f.has(ATTR1_AURA_UNIQUE) or f.has(ATTR5_AURA_UNIQUE_PER_CASTER)
    if is_multislot(f):
        return "new-object"
    if f.stack_amount:
        return "stack-and-refresh"
    if uniq:
        return "unique-no-timer-refresh"
    return "refresh"


# Refresh entry points that are not Spell::DoSpellEffectHit: they run ModStackAmount/RefreshTimers
# (default resetPeriodicTimer = true, SpellAuras.h:135 / ModStackAmount default) but never reach the
# Spell.cpp:3262-3298 duration commit, so there is no ATTR13 carry and the result is duration = M.
NON_SPELL_REFRESH_PATHS = (
    {"path": "Unit::AddAura", "coords": "Unit.cpp:12311-12314", "note": "linked LINK_AURA children, scripts"},
    {"path": "aura steal onto an existing aura", "coords": "Unit.cpp:4068-4069 (ModStackAmount then SetDuration(stolen)); 4083"},
    {"path": "vehicle/spellclick aura", "coords": "Unit.cpp:12756, 12770"},
    {"path": "SPELL_EFFECT_MODIFY_AURA_STACKS (289) MiscValue 0", "coords": "SpellEffects.cpp:6138", "note": "positive delta only refreshes"},
    {"path": "HandleAuraLinked REAPPLY", "coords": "SpellAuraEffects.cpp:5358", "note": "delta = parent stacks - own; positive delta only refreshes"},
    {"path": "script ModStackAmount(+n) / RefreshDuration", "coords": "src/server/scripts (track H census)"},
)


def classify(f: SpellFacts) -> dict[str, Any]:
    """Same-caster reapplication through a *cast* (Spell hit path), plus the flags that change it.

    Self-channels are split out: the caster's own recast starts a new channel, which interrupts the
    running one (Unit.cpp:3113-3117 via Spell.cpp:3598) and ``Spell::cancel`` removes the channel's
    auras (Spell.cpp:3636-3647) before the new hit, so the new hit creates a new aura.  The
    underlying refresh branch is kept in ``branch_if_found`` for the entry points that can still find a
    channel aura (non-Spell paths, or an aura that outlived its channel).

    The pandemic carry is ``n/a`` when the unmodded duration is not positive: Spell.cpp:3268 gates the
    whole block on ``AuraDuration > 0`` (permanent and zero-duration auras never reach it).

    Mirrors: Unit.cpp:3386-3446; SpellAuras.cpp:1093-1124, 976-990; Spell.cpp:3240, 3268, 3284-3298
    """
    from .duration import spellinfo_get_duration
    pandemic = f.has(ATTR13_PERIODIC_REFRESH_EXTENDS_DURATION)
    found = spell_hit_branch(f)
    channel = f.channeled and found != "new-object"
    branch = "self-channel-cancel-then-create" if channel else found
    timers = branch in ("stack-and-refresh", "refresh")
    finite = spellinfo_get_duration(f) > 0
    periodic_timer = ("n/a" if branch in ("new-object", "self-channel-cancel-then-create") else
                      "untouched" if not timers else
                      "preserved (ATTR13)" if pandemic else
                      "preserved (StackAmount>=2)" if f.stack_amount >= 2 else "reset")
    if not pandemic or branch in ("new-object", "self-channel-cancel-then-create"):
        carry = "none"
    elif not finite:
        carry = "n/a (non-positive duration: Spell.cpp:3268 gate)"
    elif timers:
        carry = "pandemic-reads-refreshed-duration"
    else:
        carry = "pandemic-reads-live-remaining"
    return {
        "branch": branch,
        "branch_if_found": found,
        "entry_point": "Spell::DoSpellEffectHit (caster's cast); non-Spell refreshes: NON_SPELL_REFRESH_PATHS",
        "lookup": lookup_scope(f),
        "duration": ("new aura: CalcMaxDuration then hit commit" if branch in ("new-object", "self-channel-cancel-then-create") else
                     "reset to recalculated max, then hit commit" if timers else
                     "kept unless hit duration != current max (then full reset)"),
        "periodic_timer": periodic_timer,
        "ticks_done": "n/a" if branch in ("new-object", "self-channel-cancel-then-create") else ("reset" if timers else "kept"),
        "carry": carry,
        "finite_duration": finite,
        "rolling_periodic": f.has(ATTR10_ROLLING_PERIODIC),
        "amount": "new" if branch in ("new-object", "self-channel-cancel-then-create") else
                  "recalculated (SetStackAmount->ChangeAmount) on every found-aura path",
        "simc_does_not_refresh_flag": f.has(ATTR15_AURA_DOES_NOT_REFRESH_SIMC),
        "coords": ["Unit.cpp:3386-3446", "SpellAuras.cpp:1093-1124", "SpellAuras.cpp:976-990",
                   "Spell.cpp:3240", "Spell.cpp:3268", "Spell.cpp:3284-3298", "Unit.cpp:3113-3117", "Spell.cpp:3636-3647"],
    }


def explain(ctx, spell: int) -> dict[str, Any]:
    """``aura_lifecycle.py refresh <spell>``."""
    from .duration import Caster as _C, HitInputs as _H, empower_spells, facts, spellinfo_get_duration
    f = facts(ctx.data, spell, empower_spells(ctx.bundle.source))
    out: dict[str, Any] = {"spell": spell, "name": ctx.name(spell), "build_skew": ctx.is_skew(spell),
                           "facts": {"stack_amount": f.stack_amount, "passive": f.passive, "channeled": f.channeled,
                                     "periods": list(f.periods)},
                           "classification": classify(f),
                           "evidence": ["db2-fact", "trinity-consumer"]}
    ov = overlay_touch(spell)
    if ov:
        out["external_policy"] = ov
    d = spellinfo_get_duration(f)
    if d > 0 and not is_multislot(f):
        # refresh at 90% elapsed (10% left) with a plain caster: pandemic models disagree there
        tl = timeline(f, [(0, _H(caster=_C())), (d * 9 // 10, _H(caster=_C()))], end=d * 2, step=100)
        out["timeline_refresh_at_90pct"] = [e for e in tl["events"] if e["event"] in ("apply", "expire", "removed")]
        for e in out["timeline_refresh_at_90pct"]:
            e.pop("log", None)
        if f.has(ATTR13_PERIODIC_REFRESH_EXTENDS_DURATION):
            out["pandemic_models_at_90pct"] = pandemic_models(d, d - d * 9 // 10, d)
    return out


_OVERLAY = None


def overlay_touch(spell: int) -> dict[str, Any]:
    """World-overlay/script surfaces that can change this spell's duration/refresh (policy source)."""
    global _OVERLAY
    if _OVERLAY is None:
        import json

        from . import ROOT
        path = ROOT / "docs" / "research" / "procs-corpora" / "trinity-world-overlay.json"
        _OVERLAY = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    corr = _OVERLAY.get("spell_info_corrections", {}).get(str(spell), [])
    names = sorted({n for s, n in _OVERLAY.get("spell_script_names", []) if abs(int(s)) == spell})
    touched = [m for m in corr if any(k in m for k in ("DurationEntry", "StackAmount", "Attributes"))]
    out: dict[str, Any] = {}
    if touched:
        out["spell_info_corrections"] = touched
    if names:
        out["spell_script_names"] = names
    if out:
        out["evidence"] = "world-db-fact"
        out["note"] = "script bodies may call SetDuration/SetMaxDuration/RefreshDuration (track H owns the census)"
    return out


def census(ctx, populations: dict[str, frozenset[int]]) -> dict[str, Any]:
    from .duration import ATTR8_HASTE_AFFECTS_DURATION, empower_spells, facts, family, spellinfo_get_duration
    emp = empower_spells(ctx.bundle.source)
    out: dict[str, Any] = {"branches": {}, "periodic_timer": {}, "carry": {}, "combos": {}, "witnesses": {},
                           "pandemic_population": {}, "attr8_no_period_branch": {}}
    for pop, spells in populations.items():
        br, pt, ca, co = Counter(), Counter(), Counter(), Counter()
        wit: dict[str, list[int]] = {}
        pp = Counter()
        a8: list[int] = []
        for spell in sorted(spells):
            f = facts(ctx.data, spell, emp)
            c = classify(f)
            br[c["branch"]] += 1
            pt[c["periodic_timer"]] += 1
            ca[c["carry"]] += 1
            key = f"{c['branch']}|{c['carry']}|rolling={c['rolling_periodic']}|simc489={c['simc_does_not_refresh_flag']}"
            co[key] += 1
            wk = f"{c['branch']}|{c['carry']}"
            if len(wit.setdefault(wk, [])) < 6 and family(f) not in ("permanent-sentinel", "no-duration-index-passive"):
                wit[wk].append(spell)
            if f.has(ATTR13_PERIODIC_REFRESH_EXTENDS_DURATION):
                pp["attr13_total"] += 1
                pp["finite" if c["finite_duration"] else "non_positive_duration"] += 1
                if f.channeled:
                    pp["channel"] += 1
                if c["branch_if_found"] == "new-object":
                    pp["new_object"] += 1
                if c["branch_if_found"] == "unique-no-timer-refresh":
                    pp["unique_non_stacking"] += 1
                pp[f"carry:{c['carry']}"] += 1
            # Spell.cpp:3271-3281: non-channel ATTR8 with no GetPeriod() != 0 -> m_originalCaster deref
            if (f.has(ATTR8_HASTE_AFFECTS_DURATION) and not f.channeled and not any(f.periods)
                    and spellinfo_get_duration(f) > 0):
                a8.append(spell)
        out["branches"][pop] = dict(sorted(br.items()))
        out["periodic_timer"][pop] = dict(sorted(pt.items()))
        out["carry"][pop] = dict(sorted(ca.items()))
        out["combos"][pop] = dict(sorted(co.items()))
        out["witnesses"][pop] = {k: v for k, v in sorted(wit.items())}
        out["pandemic_population"][pop] = dict(sorted(pp.items()))
        out["attr8_no_period_branch"][pop] = {"count": len(a8), "first": a8[:10]}
    return out
