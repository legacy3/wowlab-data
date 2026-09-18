"""Periodic timer lifecycle (Track D).

Two layers:

* **Authored profile** (:func:`profile`): per aura effect of a spell, what Trinity's
  ``AuraEffect::CalculatePeriodic`` would make of the DB2 row -- whether the effect
  ticks at all, its base period, which haste input rescales the period, the extra
  initial occurrence (raw attribute 169), the authored tick count and the part of the
  duration no full tick covers, and the refresh-cadence policy inputs.
* **Runtime model** (:class:`PeriodicSim`, :func:`run`): an exact millisecond replay of
  one aura (one ``Aura`` + its periodic ``AuraEffect`` s) driven by an explicit owner
  update schedule and lifecycle events (create, refresh/reapply, stack change,
  removal, haste change).  Every step mirrors a Trinity function; the differential
  probe ``tools/tc_aura_periodic_probe`` compiles those functions verbatim and the
  tests compare both.

Scheduling (proved from call paths, TrinityCore @ 7f3d43b):
``Unit::Update`` (Unit.cpp:423) -> ``WorldObject::Update`` (Object.cpp:245:
``m_Events.Update`` -> ``SpellEvent::Execute`` Spell.cpp:8375 -> ``Spell::update``
Spell.cpp:4237, i.e. the unit's *own* cast completions and delayed hits) -> then
``Unit::_UpdateSpells`` (Unit.cpp:2957): every owned aura, in ``m_ownedAuras``
(multimap keyed by SpellId) order, ``Aura::UpdateOwner`` (SpellAuras.cpp:817) =
``Aura::Update`` (duration) -> target-map timer -> each ``AuraEffect::Update``
(periodic ticks); only after *all* owned auras updated, the expiry loop removes
``IsExpired()`` auras with ``AURA_REMOVE_BY_EXPIRE``.

The runtime model is Trinity's; it is a consumer oracle, not Retail truth.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Any

from procs.enums import attr, aura, aura_name

from . import FailClosed

TC = "TrinityCore@7f3d43b"

# Mirrors: SpellAuraEffects.cpp:966-981 (AuraEffect::CalculatePeriodic "prepare periodics").
PERIODIC_AURA_NAMES = (
    "SPELL_AURA_OBS_MOD_POWER", "SPELL_AURA_PERIODIC_DAMAGE", "SPELL_AURA_PERIODIC_HEAL",
    "SPELL_AURA_OBS_MOD_HEALTH", "SPELL_AURA_PERIODIC_TRIGGER_SPELL",
    "SPELL_AURA_PERIODIC_TRIGGER_SPELL_FROM_CLIENT", "SPELL_AURA_PERIODIC_ENERGIZE",
    "SPELL_AURA_PERIODIC_LEECH", "SPELL_AURA_PERIODIC_HEALTH_FUNNEL", "SPELL_AURA_PERIODIC_MANA_LEECH",
    "SPELL_AURA_PERIODIC_DAMAGE_PERCENT", "SPELL_AURA_POWER_BURN", "SPELL_AURA_PERIODIC_DUMMY",
    "SPELL_AURA_PERIODIC_TRIGGER_SPELL_WITH_VALUE",
)
PERIODIC_AURAS = frozenset(aura(n.removeprefix("SPELL_AURA_")) for n in PERIODIC_AURA_NAMES)
# Mirrors: SpellAuraEffects.cpp:1334 -- PeriodicTick dispatches it, but CalculatePeriodic never sets
# m_isPeriodic for it, so AuraEffect::Update returns at 1252 (AL-D-D-01).
WEAPON_PERCENT_DAMAGE = aura("PERIODIC_WEAPON_PERCENT_DAMAGE")
# Tick handlers (SpellAuraEffects.cpp:1319-1360) that compute an amount at tick time.
TICK_HANDLER = {
    aura("PERIODIC_DUMMY"): ("script-only", "SpellAuraEffects.cpp:1321"),
    aura("PERIODIC_TRIGGER_SPELL"): ("HandlePeriodicTriggerSpellAuraTick", "SpellAuraEffects.cpp:5583"),
    aura("PERIODIC_TRIGGER_SPELL_FROM_CLIENT"): ("client-cast (no server action)", "SpellAuraEffects.cpp:1327"),
    aura("PERIODIC_TRIGGER_SPELL_WITH_VALUE"): ("HandlePeriodicTriggerSpellWithValueAuraTick", "SpellAuraEffects.cpp:5607"),
    aura("PERIODIC_DAMAGE"): ("HandlePeriodicDamageAurasTick", "SpellAuraEffects.cpp:5632"),
    WEAPON_PERCENT_DAMAGE: ("HandlePeriodicDamageAurasTick (unreachable)", "SpellAuraEffects.cpp:5683"),
    aura("PERIODIC_DAMAGE_PERCENT"): ("HandlePeriodicDamageAurasTick", "SpellAuraEffects.cpp:5632"),
    aura("PERIODIC_LEECH"): ("HandlePeriodicHealthLeechAuraTick", "SpellAuraEffects.cpp:5762"),
    aura("PERIODIC_HEALTH_FUNNEL"): ("HandlePeriodicHealthFunnelAuraTick", "SpellAuraEffects.cpp:5863"),
    aura("PERIODIC_HEAL"): ("HandlePeriodicHealAurasTick", "SpellAuraEffects.cpp:5893"),
    aura("OBS_MOD_HEALTH"): ("HandlePeriodicHealAurasTick", "SpellAuraEffects.cpp:5893"),
    aura("PERIODIC_MANA_LEECH"): ("HandlePeriodicManaLeechAuraTick", "SpellAuraEffects.cpp:5951"),
    aura("OBS_MOD_POWER"): ("HandleObsModPowerAuraTick", "SpellAuraEffects.cpp:6010"),
    aura("PERIODIC_ENERGIZE"): ("HandlePeriodicEnergizeAuraTick", "SpellAuraEffects.cpp:6053"),
    aura("POWER_BURN"): ("HandlePeriodicPowerBurnAuraTick", "SpellAuraEffects.cpp:6084"),
}

A0_IS_ABILITY = attr("SPELL_ATTR0_IS_ABILITY")
A0_PASSIVE = attr("SPELL_ATTR0_PASSIVE")
A1_CHANNEL = attr("SPELL_ATTR1_IS_CHANNELLED")
A1_SELF_CHANNEL = attr("SPELL_ATTR1_IS_SELF_CHANNELLED")
A1_AURA_UNIQUE = attr("SPELL_ATTR1_AURA_UNIQUE")
A2_CANT_CRIT = attr("SPELL_ATTR2_CANT_CRIT")
A3_IGNORE_CASTER_MODS = attr("SPELL_ATTR3_IGNORE_CASTER_MODIFIERS")
A3_DOT_STACKING_RULE = attr("SPELL_ATTR3_DOT_STACKING_RULE")
A4_IGNORE_TAKEN_MODS = attr("SPELL_ATTR4_IGNORE_DAMAGE_TAKEN_MODIFIERS")
A5_EXTRA_INITIAL = attr("SPELL_ATTR5_EXTRA_INITIAL_PERIOD")            # raw 169
A5_SPELL_HASTE = attr("SPELL_ATTR5_SPELL_HASTE_AFFECTS_PERIODIC")      # raw 173
A5_UNIQUE_PER_CASTER = attr("SPELL_ATTR5_AURA_UNIQUE_PER_CASTER")
A6_IGNORE_CASTER_DMG_MODS = attr("SPELL_ATTR6_IGNORE_CASTER_DAMAGE_MODIFIERS")
A8_PERIODIC_CAN_CRIT = attr("SPELL_ATTR8_PERIODIC_CAN_CRIT")          # raw 265
A8_HASTE_DURATION = attr("SPELL_ATTR8_HASTE_AFFECTS_DURATION")         # raw 273
A8_MELEE_HASTE = attr("SPELL_ATTR8_MELEE_HASTE_AFFECTS_PERIODIC")      # raw 278
A8_MASTERY_POINTS = attr("SPELL_ATTR8_MASTERY_AFFECTS_POINTS")         # raw 285
A10_ROLLING = attr("SPELL_ATTR10_ROLLING_PERIODIC")                    # raw 334
A13_PANDEMIC = attr("SPELL_ATTR13_PERIODIC_REFRESH_EXTENDS_DURATION")  # raw 436

# SpellEffectAttributes (DBCEnums.h:2400-2425)
EA_SUPPRESS_POINTS_STACKING = 0x00000040
EA_AURA_POINTS_STACK = 0x00000200
EA_COMPUTE_POINTS_ONLY_AT_CAST = 0x00008000   # /*NYI*/ in Trinity (DBCEnums.h:2418); simc EX_COMPUTE_ON_CAST

# SpellDefines.h:291,293
TRIGGERED_DONT_RESET_PERIODIC_TIMER = 0x00020000
TRIGGERED_FULL_MASK = 0x0007FFFF

# Trinity SpellMgr corrections that change periodic inputs (SpellMgr.cpp); the snapshot catalog
# does not apply them, so a profile names them instead of silently diverging.
CORRECTIONS = {
    6474: ("SpellMgr.cpp:3779", "AttributesEx5 |= SPELL_ATTR5_EXTRA_INITIAL_PERIOD (Earthbind Totem)"),
    51912: ("SpellMgr.cpp:3718", "EFFECT_0 ApplyAuraPeriod = 3000"),
    40453: ("SpellMgr.cpp:4616", "EFFECT_0 -> PROC_TRIGGER_SPELL, ApplyAuraPeriod = 0"),
}


def raw_index(key: tuple[int, int]) -> int:
    word, bit = key
    return word * 32 + bit.bit_length() - 1


def f32(x: float) -> float:
    """Round to IEEE binary32 (Trinity float arithmetic)."""
    return struct.unpack("<f", struct.pack("<f", x))[0]


def trunc_i32(x: float) -> int:
    """C++ ``int32(float)``: truncation toward zero."""
    if x != x or abs(x) >= 2 ** 31:
        raise FailClosed(f"int32 conversion of {x!r} is undefined behaviour")
    return int(x)


def has(attrs: tuple[int, ...] | list[int], key: tuple[int, int]) -> bool:
    word, bit = key
    return bool(attrs[word] & bit) if word < len(attrs) else False


# ---------------------------------------------------------------------------
# period / tick-count arithmetic
# ---------------------------------------------------------------------------

def is_channeled(attrs) -> bool:
    """Mirrors: SpellInfo.cpp:1889 ``SpellInfo::IsChanneled``."""
    return has(attrs, A1_CHANNEL) or has(attrs, A1_SELF_CHANNEL)


def haste_mode(attrs) -> str:
    """Which caster input rescales the period.

    Mirrors: SpellAuraEffects.cpp:1004-1010 (``CalculatePeriodic``) and Object.cpp:1818-1841
    (``WorldObject::ModSpellDurationTime``, which returns early for a channel without raw 173/278).
    """
    if is_channeled(attrs):
        if has(attrs, A5_SPELL_HASTE) or has(attrs, A8_MELEE_HASTE):
            return "channel-cast-speed"
        return "channel-unhasted"
    if has(attrs, A5_SPELL_HASTE):
        return "spell-haste"
    if has(attrs, A8_MELEE_HASTE):
        return "melee-haste"
    return "none"


def calc_period(base_period: int, attrs, *, cast_speed: float = 1.0, melee_haste: float = 1.0,
                caster: bool = True, player_family: bool = True, spellmod_period: int | None = None) -> int:
    """Period after caster mods, from ``SpellEffectInfo::ApplyAuraPeriod``.

    ``cast_speed`` / ``melee_haste`` are ``UnitData::ModCastingSpeed`` / ``ModHaste`` (binary32,
    < 1 when hasted).  ``spellmod_period`` (``SpellModOp::Period``) is not modelled: pass ``None``
    (no modifier) or the already-modified period; a non-None value is used as the base.

    Mirrors: SpellAuraEffects.cpp:996-1013 (``AuraEffect::CalculatePeriodic``),
    Object.cpp:1818 (``WorldObject::ModSpellDurationTime``).
    """
    period = base_period if spellmod_period is None else spellmod_period
    if not period or not caster:
        return period
    mode = haste_mode(attrs)
    if mode == "channel-unhasted":
        return period
    if mode == "channel-cast-speed":
        # Object.cpp:1836-1838: ability/tradeskill/ignore-caster-mods keep the period; players only
        # for SpellFamilyName != 0 (``player_family``).
        if has(attrs, A0_IS_ABILITY) or has(attrs, A3_IGNORE_CASTER_MODS) or not player_family:
            raise FailClosed("channel period: ModSpellDurationTime ability/family/ranged branch not modelled")
        return trunc_i32(f32(f32(float(period)) * f32(cast_speed)))
    if mode == "spell-haste":
        return trunc_i32(f32(f32(float(period)) * f32(cast_speed)))
    if mode == "melee-haste":
        return trunc_i32(f32(f32(float(period)) * f32(melee_haste)))
    return period


def total_ticks(max_duration: int, period: int, extra_initial: bool) -> int:
    """Mirrors: SpellAuraEffects.cpp:936-946 (``AuraEffect::GetTotalTicks``); permanent (-1) -> 0.

    ``static_cast<uint32>(int32 / int32)``: C++ division truncates toward zero.
    """
    if not period or max_duration == -1:
        return 0
    if max_duration < 0 or period < 0:
        raise FailClosed("negative non-permanent max duration / period: int32 division sign not modelled")
    ticks = max_duration // period
    return ticks + 1 if extra_initial else ticks


def authored_tick_count(duration: int, period: int, extra_initial: bool) -> int:
    """Mirrors: SpellInfo.cpp:500-514 (``SpellEffectInfo::GetPeriodicTickCount``): unmodified duration."""
    if not period or duration <= 0:
        return 0
    return duration // period + (1 if extra_initial else 0)


# ---------------------------------------------------------------------------
# runtime model
# ---------------------------------------------------------------------------

@dataclass
class SpellShape:
    """The SpellInfo facts the periodic lifecycle reads (small explicit fixture)."""
    base_period: int
    base_duration: int                     # CalcMaxDuration result (caster duration mods applied by caller)
    attrs: tuple[int, ...] = (0,) * 17
    stack_amount: int = 0                  # SpellAuraOptions.CumulativeAura (capacity)
    aura_type: int = aura("PERIODIC_DAMAGE")
    effects: int = 1                       # number of periodic AuraEffects (same period)

    @property
    def extra_initial(self) -> bool:
        return has(self.attrs, A5_EXTRA_INITIAL)

    @property
    def pandemic(self) -> bool:
        return has(self.attrs, A13_PANDEMIC)

    @property
    def passive(self) -> bool:
        return has(self.attrs, A0_PASSIVE)


@dataclass
class EffectState:
    period: int = 0
    periodic_timer: int = 0
    ticks_done: int = 0
    is_periodic: bool = False


@dataclass
class AuraState:
    max_duration: int
    duration: int
    stack: int = 1
    effects: list[EffectState] = field(default_factory=list)
    removed: str | None = None

    @property
    def permanent(self) -> bool:
        return self.max_duration == -1


class PeriodicSim:
    """One aura on one owner.  All mutation goes through mirrored Trinity steps."""

    def __init__(self, spell: SpellShape, *, cast_speed: float = 1.0, melee_haste: float = 1.0) -> None:
        self.spell = spell
        self.cast_speed = cast_speed
        self.melee_haste = melee_haste
        self.aura: AuraState | None = None
        self.log: list[dict[str, Any]] = []
        self.now = 0

    # -- AuraEffect ------------------------------------------------------------------
    def _calculate_periodic(self, eff: EffectState, reset: bool) -> None:
        """Mirrors: SpellAuraEffects.cpp:961-1033 (``AuraEffect::CalculatePeriodic``, load=false)."""
        s = self.spell
        eff.period = s.base_period
        eff.is_periodic = s.aura_type in PERIODIC_AURAS
        if not eff.is_periodic:
            return
        if eff.period:
            eff.period = calc_period(s.base_period, s.attrs, cast_speed=self.cast_speed,
                                     melee_haste=self.melee_haste)
        else:
            eff.is_periodic = False
        self._reset_periodic(eff, reset)

    def _reset_periodic(self, eff: EffectState, reset: bool) -> None:
        """Mirrors: SpellAuraEffects.cpp:949-959 (``AuraEffect::ResetPeriodic``)."""
        eff.ticks_done = 0
        if reset:
            eff.periodic_timer = 0
            if self.spell.extra_initial:
                eff.periodic_timer = eff.period

    def _effect_update(self, index: int, eff: EffectState, diff: int) -> list[int]:
        """Mirrors: SpellAuraEffects.cpp:1250-1276 (``AuraEffect::Update``).  Returns tick numbers."""
        a = self.aura
        if not eff.is_periodic or (a.duration < 0 and not self.spell.passive and not a.permanent):
            return []
        total = total_ticks(a.max_duration, eff.period, self.spell.extra_initial)
        eff.periodic_timer += diff
        ticks = []
        while eff.periodic_timer >= eff.period:
            eff.periodic_timer -= eff.period
            if not a.permanent and eff.ticks_done + 1 > total:
                break
            eff.ticks_done += 1
            ticks.append(eff.ticks_done)
        return ticks

    # -- Aura ------------------------------------------------------------------------
    def _aura_update(self, diff: int) -> None:
        """Mirrors: SpellAuras.cpp:855-905 (``Aura::Update``; periodic power costs not modelled)."""
        a = self.aura
        if a.duration > 0:
            a.duration -= diff
            if a.duration < 0:
                a.duration = 0

    def _refresh_timers(self, reset: bool) -> None:
        """Mirrors: SpellAuras.cpp:976-992 (``Aura::RefreshTimers``) with ``RefreshDuration(false)`` 952-974."""
        a = self.aura
        a.max_duration = self.spell.base_duration           # CalcMaxDuration() (caller-supplied)
        if self.spell.pandemic:
            reset = False                                   # 981-985
        a.duration = a.max_duration                         # RefreshDuration -> SetDuration(GetMaxDuration())
        for eff in a.effects:
            eff.ticks_done = 0                              # ResetTicks (SpellAuraEffects.h:89)
        for eff in a.effects:
            self._calculate_periodic(eff, reset)

    def _mod_stack_amount(self, num: int, reset: bool) -> bool:
        """Mirrors: SpellAuras.cpp:1093-1127 (``Aura::ModStackAmount``; spellmods/charges not modelled)."""
        s, a = self.spell, self.aura
        stack = a.stack + num
        max_stack = s.stack_amount
        if num > 0 and stack > max_stack:
            stack = 1 if not s.stack_amount else max_stack
        elif stack <= 0:
            self._remove("default")
            return True
        refresh = stack >= a.stack and (bool(s.stack_amount) or (
            not has(s.attrs, A1_AURA_UNIQUE) and not has(s.attrs, A5_UNIQUE_PER_CASTER)))
        a.stack = stack                                     # SetStackAmount (amount recalculation: snapshot.py)
        if refresh:
            self._refresh_timers(reset)
        return False

    def _set_spell_duration(self, aura_duration: int, refresh: bool) -> None:
        """The post-``TryRefreshStackOrCreate`` duration override of ``Spell::DoSpellEffectHit``.

        Mirrors: Spell.cpp:3258-3297 with ``ModSpellDuration`` identity and ``DurationMul`` 1
        (duration mods belong to Track B).  NOTE the pandemic branch reads ``GetDuration()`` *after*
        ``RefreshTimers`` already set it to the full new duration (AL-D-D-02).
        """
        a, s = self.aura, self.spell
        if aura_duration > 0:
            if is_channeled(s.attrs):
                raise FailClosed("channel duration haste (ModSpellDurationTime on duration) not modelled here")
            if has(s.attrs, A8_HASTE_DURATION):
                orig = aura_duration
                aura_duration = 0
                for eff in a.effects:
                    if eff.period:
                        aura_duration = max(max(int(orig / eff.period), 1) * eff.period, aura_duration)
                if not aura_duration:
                    aura_duration = trunc_i32(f32(f32(float(orig)) * f32(self.cast_speed)))
            if refresh and s.pandemic:
                new = aura_duration + a.duration
                # CalculatePct(int32, int): T(base * static_cast<float>(pct) / 100.0f) (Util.h:72)
                aura_duration = min(new, trunc_i32(f32(f32(f32(float(aura_duration)) * 130.0) / 100.0)))
        if aura_duration != a.max_duration:
            a.max_duration = aura_duration
            a.duration = aura_duration

    def _remove(self, mode: str) -> None:
        self.aura.removed = mode

    # -- public events -----------------------------------------------------------------
    def create(self, spell_hit: bool = True) -> None:
        """New aura: ``Aura::Create`` -> ``AuraEffect`` ctor (``CalculatePeriodic(caster, true)``,
        SpellAuraEffects.cpp:742), then -- on the Spell-hit path only -- the Spell.cpp duration override
        (3258-3297).  ``Unit::AddAura`` (Unit.cpp:12287-12319) creates without that override."""
        self.aura = AuraState(max_duration=self.spell.base_duration, duration=self.spell.base_duration,
                              effects=[EffectState() for _ in range(self.spell.effects)])
        for eff in self.aura.effects:
            self._calculate_periodic(eff, True)
        if spell_hit:
            self._set_spell_duration(self.spell.base_duration, refresh=False)

    def mod_stacks(self, num: int, reset: bool = True) -> dict[str, Any]:
        """Non-Spell-hit refresh/stack deliveries: ``ModStackAmount(num)`` with the default
        ``resetPeriodicTimer = true`` and no Spell.cpp duration override.

        Mirrors: Unit.cpp:12311 ``Unit::AddAura`` -> ``TryRefreshStackOrCreate`` with the default
        ``AuraCreateInfo::ResetPeriodicTimer = true`` (SpellAuras.h:135) -> Unit.cpp:3441 ``ModStackAmount(1)``;
        SpellEffects.cpp:6138 ``EffectModifyAuraStacks`` (effect 289, MiscValue 0) ``ModStackAmount(value)``;
        AuraScript ``ModStackAmount`` (SpellScript.cpp:1183) passes ``reset`` explicitly.
        """
        removed = self._mod_stack_amount(num, reset)
        return {"reset_requested": reset, "reset_effective": reset and not self.spell.pandemic and not removed}

    def set_duration(self, delta: int) -> None:
        """Script ``SetDuration(GetDuration() + delta)``; ``_ticksDone`` and MaxDuration untouched.
        Mirrors: SpellAuras.cpp:941-950 (``Aura::SetDuration``, withMods=false)."""
        self.aura.duration = self.aura.duration + delta

    def set_max_duration(self, delta: int) -> None:
        """Script ``SetMaxDuration(GetDuration() + delta)`` (SpellAuras.h:218); budget re-read at every update."""
        self.aura.max_duration = self.aura.duration + delta

    def reapply(self, trigger_flags: int = 0) -> dict[str, Any]:
        """Same-caster reapplication through ``Spell::DoSpellEffectHit``.

        Mirrors: Spell.cpp:3240 (``resetPeriodicTimer``), Unit.cpp:3441 (``ModStackAmount(StackAmount=1)``),
        Spell.cpp:3258-3297 (duration override).
        """
        reset = self.spell.stack_amount < 2 and not (trigger_flags & TRIGGERED_DONT_RESET_PERIODIC_TIMER)
        removed = self._mod_stack_amount(1, reset)
        if not removed:
            self._set_spell_duration(self.spell.base_duration, refresh=True)
        return {"reset_requested": reset, "reset_effective": reset and not self.spell.pandemic}

    def update(self, diff: int) -> list[dict[str, Any]]:
        """One owner ``_UpdateSpells(diff)`` for this aura: ``UpdateOwner`` then the expiry loop."""
        a = self.aura
        if a is None or a.removed:
            return []
        self._aura_update(diff)
        ticks = []
        for i, eff in enumerate(a.effects):
            for n in self._effect_update(i, eff, diff):
                ticks.append({"effect": i, "tick": n})
        if a.duration == 0:                    # IsExpired (SpellAuras.h:226), Unit.cpp:2984-2986
            a.removed = "expire"
        return ticks

    def snapshot(self) -> dict[str, Any]:
        a = self.aura
        if a is None:
            return {}
        return {"duration": a.duration, "max_duration": a.max_duration, "stack": a.stack, "removed": a.removed,
                "effects": [{"period": e.period, "timer": e.periodic_timer, "ticks_done": e.ticks_done,
                             "total_ticks": total_ticks(a.max_duration, e.period, self.spell.extra_initial),
                             "is_periodic": e.is_periodic} for e in a.effects]}


def run(scenario: dict[str, Any], trace: bool = False) -> dict[str, Any]:
    """Replay a scenario to an exact millisecond timeline.

    ``scenario``::

        {"spell": {base_period, base_duration, attrs?: {name: bool}|list, stack_amount?, aura_type?, effects?},
         "caster": {"cast_speed": 1.0, "melee_haste": 1.0},
         "updates": [t0, t1, ...]  |  "update_every": ms, "until": ms,
         "events": [{"t": ms, "op": "create"|"reapply"|"remove"|"haste", "trigger_flags"?: int,
                     "phase"?: "before"|"after", ...}]}

    Owner updates happen at the listed times with ``diff = t_k - t_{k-1}`` (the first update
    after ``t=0`` uses ``t_0``); an event at time ``t`` runs before the owner's update at ``t``
    (``phase: before``: the owner's own ``WorldObject::Update`` events, or a unit updated earlier in
    the map) or after it (``phase: after``: a unit updated later in the same map tick).
    """
    sp = scenario["spell"]
    attrs = sp.get("attrs", [0] * 17)
    if isinstance(attrs, dict):
        words = [0] * 17
        for name, on in attrs.items():
            if on:
                w, b = attr(name)
                words[w] |= b
        attrs = words
    shape = SpellShape(base_period=sp["base_period"], base_duration=sp["base_duration"], attrs=tuple(attrs),
                       stack_amount=sp.get("stack_amount", 0),
                       aura_type=sp.get("aura_type", aura("PERIODIC_DAMAGE")),
                       effects=sp.get("effects", 1))
    caster = scenario.get("caster", {})
    sim = PeriodicSim(shape, cast_speed=caster.get("cast_speed", 1.0), melee_haste=caster.get("melee_haste", 1.0))
    if "updates" in scenario:
        updates = list(scenario["updates"])
    else:
        step, until = scenario["update_every"], scenario["until"]
        updates = list(range(step, until + 1, step))
    if updates != sorted(set(updates)) or (updates and updates[0] <= 0):
        raise FailClosed("owner update times must be strictly increasing and > 0")
    events = sorted(scenario.get("events", []), key=lambda e: (e["t"], 0 if e.get("phase", "before") == "before" else 1))
    rows: list[dict[str, Any]] = []
    prev = 0
    ei = 0

    def fire(ev: dict[str, Any]) -> None:
        op = ev["op"]
        row: dict[str, Any] = {"t": ev["t"], "event": op}
        if op == "create":
            sim.create()
        elif op == "reapply":
            path = ev.get("path", "spell-hit")
            if path not in ("spell-hit", "add-aura", "modify-stacks"):
                raise FailClosed(f"unknown delivery path {path!r}")
            row["path"] = path
            if sim.aura is None or sim.aura.removed:
                if path == "modify-stacks":        # EffectModifyAuraStacks needs an existing aura (SpellEffects.cpp:6131-6133)
                    row["event"] = "no-op"
                else:
                    sim.create(spell_hit=path == "spell-hit")
                    row["event"] = "create"
            elif path == "spell-hit":
                row.update(sim.reapply(ev.get("trigger_flags", 0)))
            elif path == "add-aura":
                row.update(sim.mod_stacks(1, True))
            else:
                row.update(sim.mod_stacks(ev.get("value", 1), True))
        elif op in ("set_duration", "set_max_duration"):
            if sim.aura is not None and not sim.aura.removed:
                getattr(sim, op)(ev["delta"])
        elif op == "remove":
            if sim.aura is not None and not sim.aura.removed:
                sim.aura.removed = ev.get("mode", "cancel")
        elif op == "haste":
            sim.cast_speed = ev.get("cast_speed", sim.cast_speed)
            sim.melee_haste = ev.get("melee_haste", sim.melee_haste)
        else:
            raise FailClosed(f"unknown timeline op {op!r}")
        row["state"] = sim.snapshot()
        rows.append(row)

    for t in updates:
        while ei < len(events) and (events[ei]["t"] < t or (events[ei]["t"] == t and events[ei].get("phase", "before") == "before")):
            fire(events[ei])
            ei += 1
        diff = t - prev
        prev = t
        was_live = sim.aura is not None and not sim.aura.removed
        ticks = sim.update(diff)
        if ticks or (was_live and sim.aura.removed) or (trace and was_live):
            rows.append({"t": t, "event": "owner-update", "diff": diff, "ticks": ticks, "state": sim.snapshot()})
        while ei < len(events) and events[ei]["t"] == t:
            fire(events[ei])
            ei += 1
    for ev in events[ei:]:
        fire(ev)
    tick_times = [r["t"] for r in rows if r.get("ticks") for _ in r["ticks"]]
    return {"rows": rows, "tick_times": tick_times, "tick_count": len(tick_times),
            "final": sim.snapshot()}


# ---------------------------------------------------------------------------
# authored profile over the snapshot
# ---------------------------------------------------------------------------

def spell_attrs(data, spell: int) -> tuple[int, ...]:
    misc = data.row("SpellMisc", spell)
    words = [int(misc[f"Attributes_{i}"]) & 0xFFFFFFFF if misc else 0 for i in range(17)]
    if misc:
        from .passive import effective_passive
        if effective_passive(data, spell)["passive"]:  # SpellMgr.cpp:3839-3843, 5301-5302 (R1-02)
            words[0] |= 0x40
    return tuple(words)


def base_duration(data, spell: int) -> dict[str, Any]:
    """Authored ``SpellDuration`` row (no caster mods; Track B owns ``CalcMaxDuration``)."""
    misc = data.row("SpellMisc", spell)
    if not misc or not misc["DurationIndex"]:
        return {"index": 0, "duration": None, "max_duration": None}
    row = data.duration(misc["DurationIndex"]) or {}
    return {"index": misc["DurationIndex"], "duration": row.get("Duration"), "max_duration": row.get("MaxDuration")}


def refresh_cadence(attrs, stack_amount: int, trigger_flags: int | None) -> dict[str, Any]:
    """Whether a same-caster reapplication restarts (``reset``) or preserves the periodic phase.

    ``trigger_flags=None`` = the flags of the reapplying cast are unknown -> both branches listed.
    Mirrors: Spell.cpp:3240, SpellAuras.cpp:981-985, SpellAuraEffects.cpp:1031.
    """
    pandemic = has(attrs, A13_PANDEMIC)

    def one(flags: int) -> str:
        reset = stack_amount < 2 and not (flags & TRIGGERED_DONT_RESET_PERIODIC_TIMER)
        if pandemic:
            return "preserve (pandemic)"
        if stack_amount >= 2:
            return "preserve (StackAmount>=2)"
        return "restart" if reset else "preserve (TRIGGERED_DONT_RESET_PERIODIC_TIMER)"

    if trigger_flags is None:
        return {"untriggered": one(0), "fully_triggered": one(TRIGGERED_FULL_MASK),
                "decided_by": "trigger flags of the reapplying cast (runtime), StackAmount, raw 436"}
    return {"policy": one(trigger_flags)}


def profile(ctx, spell: int, data=None) -> dict[str, Any]:
    """Authored periodic profile of every aura effect of ``spell`` (DIFFICULTY_NONE)."""
    data = data or ctx.data
    effects = data.effects(spell)
    if not effects:
        raise FailClosed(f"spell {spell} has no DIFFICULTY_NONE SpellEffect rows")
    attrs = spell_attrs(data, spell)
    opts = data.row("SpellAuraOptions", spell)
    stack_amount = opts["CumulativeAura"] if opts else 0
    dur = base_duration(data, spell)
    out_effects = []
    for e in effects:
        a = e["EffectAura"]
        if not a:
            continue
        period = e["EffectAuraPeriod"]
        periodic = a in PERIODIC_AURAS
        row: dict[str, Any] = {
            "index": e["EffectIndex"], "aura": a, "aura_name": aura_name(a), "base_period": period,
            "trinity_periodic": periodic and bool(period),
            "tick_handler": TICK_HANDLER.get(a, (None, None))[0],
            "tick_handler_coords": TICK_HANDLER.get(a, (None, None))[1],
            "effect_attributes": e["EffectAttributes"],
            "compute_points_only_at_cast": bool(e["EffectAttributes"] & EA_COMPUTE_POINTS_ONLY_AT_CAST),
            "suppress_points_stacking": bool(e["EffectAttributes"] & EA_SUPPRESS_POINTS_STACKING),
            "aura_points_stack": bool(e["EffectAttributes"] & EA_AURA_POINTS_STACK),
        }
        if a == WEAPON_PERCENT_DAMAGE:
            row["note"] = "AL-D-D-01: dispatched by PeriodicTick but never marked periodic -> no ticks in Trinity"
        elif periodic and not period:
            row["note"] = "zero period: m_isPeriodic cleared (SpellAuraEffects.cpp:1014-1015), no ticks unless a script sets a period"
        elif period and not periodic:
            row["note"] = "EffectAuraPeriod on a non-periodic aura type: Trinity never reads it as a tick interval"
        if periodic and period:
            d = dur["duration"]
            row.update({
                "haste_mode": haste_mode(attrs),
                "extra_initial_period": has(attrs, A5_EXTRA_INITIAL),
                "authored_duration": d,
                "authored_ticks": authored_tick_count(d or 0, period, has(attrs, A5_EXTRA_INITIAL)) if d and d > 0 else None,
                "uncovered_tail_ms": (d % period) if d and d > 0 else None,
                "permanent": d == -1 or (d is None and has(attrs, A0_PASSIVE)),
            })
        out_effects.append(row)
    periodic_any = any(r.get("trinity_periodic") for r in out_effects)
    out = {
        "spell": spell, "name": ctx.name(spell), "build_skew": ctx.is_skew(spell),
        "duration": dur, "stack_amount": stack_amount,
        "attributes": {name: has(attrs, key) for name, key in (
            ("raw169_EXTRA_INITIAL_PERIOD", A5_EXTRA_INITIAL), ("raw173_SPELL_HASTE_AFFECTS_PERIODIC", A5_SPELL_HASTE),
            ("raw278_MELEE_HASTE_AFFECTS_PERIODIC", A8_MELEE_HASTE), ("raw273_HASTE_AFFECTS_DURATION", A8_HASTE_DURATION),
            ("raw436_PERIODIC_REFRESH_EXTENDS_DURATION", A13_PANDEMIC), ("raw334_ROLLING_PERIODIC", A10_ROLLING),
            ("raw265_PERIODIC_CAN_CRIT", A8_PERIODIC_CAN_CRIT), ("CANT_CRIT", A2_CANT_CRIT),
            ("CHANNELED", A1_CHANNEL), ("SELF_CHANNELED", A1_SELF_CHANNEL), ("PASSIVE", A0_PASSIVE),
            ("AURA_UNIQUE", A1_AURA_UNIQUE), ("DOT_STACKING_RULE", A3_DOT_STACKING_RULE),
            ("MASTERY_AFFECTS_POINTS", A8_MASTERY_POINTS))},
        "effects": out_effects,
        "trinity_correction": CORRECTIONS.get(spell),
        "evidence": ["db2-fact", "trinity-consumer"],
    }
    if periodic_any:
        out["refresh_cadence"] = refresh_cadence(attrs, stack_amount, None)
    return out


# ---------------------------------------------------------------------------
# census over the shared provider denominator
# ---------------------------------------------------------------------------

PERIODIC_HOOKS = ("OnEffectPeriodic", "OnEffectUpdatePeriodic", "DoEffectCalcPeriodic", "DoEffectCalcAmount")
TIMER_CALLS = ("SetPeriodicTimer", "ResetPeriodic", "CalculatePeriodic", "RecalculateAmount", "ChangeAmount",
               "RefreshDuration", "SetDuration", "ModStackAmount")


def script_facts(ctx, spell: int) -> dict[str, Any]:
    """Periodic-relevant AuraScript hooks / calls bound to ``spell`` (structural; Track H owns semantics)."""
    names = ctx.bundle.script_names.get(spell, [])
    hooks: set[str] = set()
    calls: set[str] = set()
    for name in names:
        res = ctx.bundle.index.resolve_script_name(name)
        for sc in res.get("classes", []):
            for h in sc.hooks:
                if h.get("list") in PERIODIC_HOOKS:
                    hooks.add(h["list"])
            for m in sc.methods.values():
                for c in m.get("calls", []):
                    if c.get("callee") in TIMER_CALLS:
                        calls.add(c["callee"])
                for c in m.get("other_calls", {}) or {}:
                    if c in TIMER_CALLS:
                        calls.add(c)
    return {"scripts": sorted(names), "periodic_hooks": sorted(hooks), "timer_calls": sorted(calls)}


def _bucket(counter: dict, key, spell: int) -> None:
    counter.setdefault(key, set()).add(spell)


def census(ctx) -> dict[str, Any]:
    """Counts of periodic lifecycle signatures per population (all / player / controlled).

    Unit: provider *effects* (``providers.provider_effects``) unless the key says ``spells``.
    """
    from .providers import populations, provider_effects
    data = ctx.data
    pops = populations(ctx)
    rows = provider_effects(data)
    attrs_cache: dict[int, tuple[int, ...]] = {}
    per_effect: list[dict[str, Any]] = []
    for r in rows:
        spell = r["spell"]
        e = data.table("SpellEffect")[(spell, 0)][r["index"]]
        a, period = e["EffectAura"], e["EffectAuraPeriod"]
        if a not in PERIODIC_AURAS and a != WEAPON_PERCENT_DAMAGE and not period:
            continue
        if spell not in attrs_cache:
            attrs_cache[spell] = spell_attrs(data, spell)
        at = attrs_cache[spell]
        opts = data.row("SpellAuraOptions", spell)
        stack = opts["CumulativeAura"] if opts else 0
        dur = base_duration(data, spell)["duration"]
        keys = []
        if a == WEAPON_PERCENT_DAMAGE:
            keys.append("weapon_percent_damage_never_ticks")
        elif a not in PERIODIC_AURAS:
            keys.append("period_on_non_periodic_aura")
        elif not period:
            keys.append("periodic_type_zero_period")
        else:
            keys.append("trinity_periodic")
            keys.append(f"aura:{aura_name(a)}")
            keys.append(f"haste:{haste_mode(at)}")
            if has(at, A5_EXTRA_INITIAL):
                keys.append("raw169_extra_initial")
            if has(at, A13_PANDEMIC):
                keys.append("raw436_pandemic")
            if has(at, A10_ROLLING):
                keys.append("raw334_rolling")
            if has(at, A8_HASTE_DURATION):
                keys.append("raw273_haste_duration")
            if has(at, A8_PERIODIC_CAN_CRIT) and not has(at, A2_CANT_CRIT):
                keys.append("tick_can_crit")
            if e["EffectAttributes"] & EA_COMPUTE_POINTS_ONLY_AT_CAST:
                keys.append("effattr_compute_points_only_at_cast")
            if has(at, A13_PANDEMIC):
                keys.append("cadence:preserve-pandemic")
            elif stack >= 2:
                keys.append("cadence:preserve-stackable")
            else:
                keys.append("cadence:by-trigger-flags")
            if a in (aura("PERIODIC_TRIGGER_SPELL"), aura("PERIODIC_ENERGIZE")) and not has(at, A13_PANDEMIC) and stack < 2:
                keys.append("unk_e_001_subtype23_24_by_trigger_flags")
            if has(at, A5_EXTRA_INITIAL) and not has(at, A13_PANDEMIC) and stack < 2:
                keys.append("unk_e_002_raw169_restart_extra_tick_possible")
            if dur is None and has(at, A0_PASSIVE):
                keys.append("duration:permanent")                    # SpellInfo.cpp:3988-3989 passive -> -1
            elif dur is None or dur == 0:
                keys.append("duration:zero")                         # non-passive without entry -> 0 (SpellInfo.cpp:3989)
                if has(at, A5_EXTRA_INITIAL):
                    keys.append("duration:zero-raw169-single-occurrence")
            elif dur == -1:
                keys.append("duration:permanent")
            elif dur % period == 0:
                keys.append("duration:multiple-of-period")
            else:
                keys.append("duration:uncovered-tail")
                if dur < period and not has(at, A5_EXTRA_INITIAL):
                    keys.append("duration:shorter-than-period-no-tick")
        per_effect.append({"spell": spell, "index": r["index"], "keys": keys})
    out: dict[str, Any] = {}
    witnesses: dict[str, list[str]] = {}
    for pop, members in sorted(pops.items()):
        counter: dict[str, set] = {}
        spells: dict[str, set] = {}
        for row in per_effect:
            if row["spell"] not in members:
                continue
            for k in row["keys"]:
                _bucket(counter, k, (row["spell"], row["index"]))
                _bucket(spells, k, row["spell"])
        out[pop] = {k: {"effects": len(v), "spells": len(spells[k])} for k, v in sorted(counter.items())}
        if pop == "player":
            for k, v in sorted(counter.items()):
                witnesses[k] = [f"{s}:{i}" for s, i in sorted(v)[:5]]
    return {"counts": out, "player_witnesses": witnesses, "population_sizes": {k: len(v) for k, v in pops.items()}}


# ---------------------------------------------------------------------------
# differential probe (tools/tc_aura_periodic_probe)
# ---------------------------------------------------------------------------

PROBE_SPELL_ID = 100
PROBE_WORDS = (0, 1, 2, 3, 5, 8, 13)


def probe_commands(scenario: dict[str, Any]) -> str:
    """Translate a :func:`run` scenario (single aura, ``phase: before`` events) into probe stdin."""
    sp = scenario["spell"]
    attrs = sp.get("attrs", [0] * 17)
    if isinstance(attrs, dict):
        words = [0] * 17
        for name, on in attrs.items():
            if on:
                w, b = attr(name)
                words[w] |= b
        attrs = words
    if any(attrs[w] for w in range(17) if w not in PROBE_WORDS):
        raise FailClosed("probe carries attribute words 0,1,2,3,5,8,13 only")
    caster = scenario.get("caster", {})
    lines = [f"spell {PROBE_SPELL_ID} " + " ".join(str(attrs[w]) for w in PROBE_WORDS)
             + f" {sp.get('stack_amount', 0)} {sp['base_duration']} 1"]
    for _ in range(sp.get("effects", 1)):
        lines.append(f"effect {PROBE_SPELL_ID} {sp.get('aura_type', aura('PERIODIC_DAMAGE'))} {sp['base_period']}")
    lines.append(f"caster {caster.get('cast_speed', 1.0)!r} {caster.get('melee_haste', 1.0)!r}")
    updates = list(scenario["updates"]) if "updates" in scenario else list(
        range(scenario["update_every"], scenario["until"] + 1, scenario["update_every"]))
    events = sorted(scenario.get("events", []), key=lambda e: e["t"])
    if any(e.get("phase", "before") != "before" for e in events):
        raise FailClosed("probe driver runs events before the owner update only")
    prev, ei = 0, 0
    for t in updates:
        while ei < len(events) and events[ei]["t"] <= t:
            ev = events[ei]
            if ev["op"] == "create":
                lines.append(f"create {PROBE_SPELL_ID}")
            elif ev["op"] == "reapply":     # the driver creates when no aura is owned (expired / removed)
                path = ev.get("path", "spell-hit")
                if path == "spell-hit":
                    lines.append(f"reapply {PROBE_SPELL_ID} {ev.get('trigger_flags', 0)}")
                elif path == "add-aura":
                    lines.append(f"modstack {PROBE_SPELL_ID} 1 1 1")
                else:
                    lines.append(f"modstack {PROBE_SPELL_ID} {ev.get('value', 1)} 1 0")
            elif ev["op"] == "set_duration":
                lines.append(f"setdur {PROBE_SPELL_ID} {ev['delta']}")
            elif ev["op"] == "set_max_duration":
                lines.append(f"setmax {PROBE_SPELL_ID} {ev['delta']}")
            elif ev["op"] == "remove":
                lines.append(f"remove {PROBE_SPELL_ID}")
            elif ev["op"] == "haste":
                lines.append(f"caster {ev.get('cast_speed', 1.0)!r} {ev.get('melee_haste', 1.0)!r}")
            else:
                raise FailClosed(f"probe: unsupported op {ev['op']!r}")
            ei += 1
        lines.append(f"update {t - prev}")
        prev = t
    return "\n".join(lines) + "\n"


def probe_ticks(output: str, updates: list[int]) -> list[int]:
    """Tick times from probe stdout (one ``update`` line per owner update, in order)."""
    import json
    times: list[int] = []
    rows = [json.loads(line) for line in output.splitlines() if line.strip()]
    ups = [r for r in rows if r.get("event") == "update"]
    if len(ups) != len(updates):
        raise FailClosed("probe output does not match the update schedule")
    for t, r in zip(updates, ups):
        times.extend(t for _ in r["ticks"])
    return times


# ---------------------------------------------------------------------------
# corpus: witnesses, timelines, scripts, drift
# ---------------------------------------------------------------------------

# Current-player witnesses (profiles are regenerated from data; the list is not a rule).
WITNESS_SPELLS = (589, 774, 980, 5143, 12654, 22842, 115175, 146739, 191034)

_P = {"SPELL_ATTR13_PERIODIC_REFRESH_EXTENDS_DURATION": True}
_X = {"SPELL_ATTR5_EXTRA_INITIAL_PERIOD": True}
_H = {"SPELL_ATTR5_SPELL_HASTE_AFFECTS_PERIODIC": True}
DOT = {"base_period": 3000, "base_duration": 12000}
TRIGGER = dict(DOT, aura_type=aura("PERIODIC_TRIGGER_SPELL"))

# Each timeline: owner updates every 100 ms unless stated; events run before the owner update at
# the same ms unless ``phase: after``.  ``probe`` = replayable by tools/tc_aura_periodic_probe.
TIMELINE_SPECS: tuple[dict[str, Any], ...] = (
    {"id": "AL-T-D-01", "question": "baseline: does the final tick fire when duration reaches 0 in the same update?",
     "scenario": {"spell": DOT, "update_every": 100, "until": 13000, "events": [{"t": 0, "op": "create"}]},
     "rules_out": "expiry removal before the due final tick (Unit.cpp:2984 runs after every AuraEffect::Update)", "probe": True},
    {"id": "AL-T-D-02", "question": "raw 169 at creation: when is the extra initial occurrence?",
     "scenario": {"spell": dict(DOT, attrs=_X), "update_every": 100, "until": 13000, "events": [{"t": 0, "op": "create"}]},
     "rules_out": "an occurrence inside the application call; Trinity ticks at the first owner update after creation "
                  "(timer=period, SpellAuraEffects.cpp:956) and GetTotalTicks adds one", "probe": True},
    {"id": "AL-T-D-03", "question": "UNK-E-002: raw 169, StackAmount<2, non-pandemic, untriggered reapply",
     "scenario": {"spell": dict(DOT, attrs=_X), "update_every": 100, "until": 20000,
                  "events": [{"t": 0, "op": "create"}, {"t": 7050, "op": "reapply"}]},
     "rules_out": "simc/Core 'no initial occurrence on active reapplication' (dot.cpp:972-975): Trinity restarts the "
                  "timer at period -> extra occurrence at the next owner update (7100)", "probe": True},
    {"id": "AL-T-D-04", "question": "UNK-E-002 control: same aura, fully triggered reapply (TRIGGERED_FULL_MASK)",
     "scenario": {"spell": dict(DOT, attrs=_X), "update_every": 100, "until": 20000,
                  "events": [{"t": 0, "op": "create"}, {"t": 7050, "op": "reapply", "trigger_flags": TRIGGERED_FULL_MASK}]},
     "rules_out": "a per-spell cadence policy: the SAME spell preserves cadence when the reapplying cast is fully triggered",
     "probe": True},
    {"id": "AL-T-D-05", "question": "UNK-E-001: subtype 23 periodic trigger, StackAmount 0, non-pandemic, untriggered reapply",
     "scenario": {"spell": TRIGGER, "update_every": 100, "until": 20000,
                  "events": [{"t": 0, "op": "create"}, {"t": 7050, "op": "reapply"}]},
     "rules_out": "'subtype 23/24 always preserve' (restart: next occurrence 10000 = 7000+3000 with 50 ms early credit)",
     "probe": True},
    {"id": "AL-T-D-06", "question": "UNK-E-001: same subtype-23 aura reapplied by a fully triggered cast",
     "scenario": {"spell": TRIGGER, "update_every": 100, "until": 20000,
                  "events": [{"t": 0, "op": "create"}, {"t": 7050, "op": "reapply", "trigger_flags": TRIGGERED_FULL_MASK}]},
     "rules_out": "'subtype 23/24 always restart' (preserve: 9000, 12000, ...)", "probe": True},
    {"id": "AL-T-D-07", "question": "pandemic (raw 436) refresh with 1950 ms remaining: duration and cadence",
     "scenario": {"spell": dict(DOT, attrs=_P), "update_every": 100, "until": 30000,
                  "events": [{"t": 0, "op": "create"}, {"t": 10050, "op": "reapply"}]},
     "rules_out": "'new = base + min(remaining, 30%)' (would be 13950 at 10050); Trinity reads GetDuration() after "
                  "RefreshTimers already reset it (Spell.cpp:3286 after SpellAuras.cpp:989) -> always 1.3x = 15600 (AL-D-D-02)",
     "probe": True},
    {"id": "AL-T-D-08", "question": "late ticks: one 7000 ms owner update (server hitch)",
     "scenario": {"spell": DOT, "updates": [1000, 8000, 9000, 10000, 11000, 12000, 13000], "events": [{"t": 0, "op": "create"}]},
     "rules_out": "one-occurrence-per-update; the while loop (SpellAuraEffects.cpp:1258) fires 2 ticks at t=8000", "probe": True},
    {"id": "AL-T-D-09", "question": "late ticks capped by count: a single 13000 ms update",
     "scenario": {"spell": DOT, "updates": [13000], "events": [{"t": 0, "op": "create"}]},
     "rules_out": "unbounded catch-up: exactly GetTotalTicks()=4 occurrences, all at t=13000", "probe": True},
    {"id": "AL-T-D-10", "question": "application between owner updates (early credit)",
     "scenario": {"spell": DOT, "update_every": 100, "until": 13000, "events": [{"t": 50, "op": "create"}]},
     "rules_out": "'timer starts at application': the first owner update credits the whole diff (100) although the aura "
                  "existed 50 ms -> ticks 3000..12000 and expiry at 12000, not 3050..12050", "probe": True},
    {"id": "AL-T-D-11", "question": "removal in the same ms as a due tick, removal processed before the owner update",
     "scenario": {"spell": DOT, "update_every": 100, "until": 13000,
                  "events": [{"t": 0, "op": "create"}, {"t": 6000, "op": "remove"}]},
     "rules_out": "'due tick always fires first' (tick 6000 lost when the removing event runs first)", "probe": True},
    {"id": "AL-T-D-12", "question": "removal in the same ms as a due tick, processed after the owner update",
     "scenario": {"spell": DOT, "update_every": 100, "until": 13000,
                  "events": [{"t": 0, "op": "create"}, {"t": 6000, "op": "remove", "phase": "after"}]},
     "rules_out": "'removal always wins' (tick 6000 fires); order = map update order of the two units", "probe": False},
    {"id": "AL-T-D-13", "question": "stackable (StackAmount 5) reapply: cadence and tick budget",
     "scenario": {"spell": dict(DOT, stack_amount=5), "update_every": 100, "until": 25000,
                  "events": [{"t": 0, "op": "create"}, {"t": 7050, "op": "reapply"}]},
     "rules_out": "'stack add restarts cadence' (Spell.cpp:3240 StackAmount>=2 -> preserve)", "probe": True},
    {"id": "AL-T-D-14", "question": "haste change mid-aura (raw 173), then untriggered restart reapply",
     "scenario": {"spell": dict(DOT, attrs=_H), "update_every": 100, "until": 25000,
                  "events": [{"t": 0, "op": "create"}, {"t": 4000, "op": "haste", "cast_speed": 0.8},
                             {"t": 7050, "op": "reapply"}]},
     "rules_out": "'haste rescales the pending period immediately' (period stays 3000 until reapply; 2400 after)",
     "probe": True},
    {"id": "AL-T-D-15", "question": "pandemic refresh after haste increase: preserved timer exceeds the new period",
     "scenario": {"spell": dict(DOT, attrs={**_P, **_H}), "update_every": 100, "until": 25000,
                  "events": [{"t": 0, "op": "create"}, {"t": 5500, "op": "haste", "cast_speed": 0.5},
                             {"t": 5550, "op": "reapply"}]},
     "rules_out": "'pandemic keeps the next tick time' -- period is re-hasted while the timer is kept, so the next "
                  "owner update fires at once (timer 2600 >= 1500)", "probe": True},
    {"id": "AL-T-D-16", "question": "duration not a multiple of period (10000 / 3000)",
     "scenario": {"spell": {"base_period": 3000, "base_duration": 10000}, "update_every": 100, "until": 11000,
                  "events": [{"t": 0, "op": "create"}]},
     "rules_out": "partial final tick (simc dot.cpp:660-699 models one); Trinity: 3 ticks, 1000 ms tail uncovered",
     "probe": True},
    {"id": "AL-T-D-19", "question": "pandemic refresh just before a due tick: is the tick budget enough for the carried phase?",
     "scenario": {"spell": dict(DOT, attrs=_P), "update_every": 100, "until": 26000,
                  "events": [{"t": 0, "op": "create"}, {"t": 8950, "op": "reapply"}]},
     "rules_out": "'every period boundary inside the new duration ticks': GetTotalTicks=floor(15600/3000)=5 counts from 0 "
                  "while the kept timer (2900) makes 6 boundaries fit -> the 24000 occurrence is dropped (AL-D-D-04)",
     "probe": True},
    {"id": "AL-T-D-20", "question": "zero duration (non-passive, no SpellDuration row) with raw 169",
     "scenario": {"spell": {"base_period": 1000, "base_duration": 0, "attrs": _X}, "update_every": 100, "until": 3000,
                  "events": [{"t": 0, "op": "create"}]},
     "rules_out": "'zero duration = no occurrence': budget 0/period+1 = 1 and the effect update runs before the "
                  "expiry loop, so exactly one occurrence at the first owner update, then removal", "probe": True},
    {"id": "AL-T-D-21", "question": "R1-05/R2-04: StackAmount-2 DoT restacked via Unit::AddAura (not the Spell hit)",
     "scenario": {"spell": dict(DOT, stack_amount=2), "update_every": 100, "until": 22000,
                  "events": [{"t": 0, "op": "create"}, {"t": 7050, "op": "reapply", "path": "add-aura"}]},
     "rules_out": "'StackAmount>=2 keeps the phase' as a spell property: AddAura uses the default resetPeriodicTimer=true "
                  "(SpellAuras.h:135, Unit.cpp:3441) -> restart, next occurrence 10000 (spell-hit path: 9000, AL-T-D-13)",
     "probe": True},
    {"id": "AL-T-D-22", "question": "R2-04: same 2-cap DoT stacked by effect 289 MODIFY_AURA_STACKS (MiscValue 0, value 1)",
     "scenario": {"spell": dict(DOT, stack_amount=2), "update_every": 100, "until": 22000,
                  "events": [{"t": 0, "op": "create"}, {"t": 7050, "op": "reapply", "path": "modify-stacks", "value": 1}]},
     "rules_out": "cadence decided by DB2 of the aura alone: the DB2-authored 289 path restarts (SpellEffects.cpp:6138)",
     "probe": True},
    {"id": "AL-T-D-23", "question": "R2-04: effect 289 with value 0 on a 5-cap DoT",
     "scenario": {"spell": dict(DOT, stack_amount=5), "update_every": 100, "until": 22000,
                  "events": [{"t": 0, "op": "create"}, {"t": 7050, "op": "reapply", "path": "modify-stacks", "value": 0}]},
     "rules_out": "'value 0 is a no-op': stack unchanged but ModStackAmount's refresh branch runs -> full duration and phase restart",
     "probe": True},
    {"id": "AL-T-D-24", "question": "R2-05 Painful Punishment 390686 shape: script SetDuration(+4000) on a 16 s / 2 s DoT at 10 s",
     "scenario": {"spell": {"base_period": 2000, "base_duration": 16000}, "update_every": 100, "until": 21000,
                  "events": [{"t": 0, "op": "create"}, {"t": 10000, "op": "set_duration", "delta": 4000}]},
     "rules_out": "'extended duration ticks': MaxDuration unchanged -> budget 8 -> occurrences end at 16000, aura lives "
                  "silently to 20000 (spell_priest.cpp:2995-2998)", "probe": True},
    {"id": "AL-T-D-25", "question": "R2-05 Mental Decay 375994 shape: SetMaxDuration(rem+1000) then SetDuration(rem+1000) at 10 s",
     "scenario": {"spell": {"base_period": 2000, "base_duration": 16000}, "update_every": 100, "until": 18000,
                  "events": [{"t": 0, "op": "create"}, {"t": 10000, "op": "set_max_duration", "delta": 1000},
                             {"t": 10000, "op": "set_duration", "delta": 1000}]},
     "rules_out": "'extension keeps the remaining occurrences': no further occurrence "
                  "for the remaining 7 s; budget 7100/2000=3 < ticks_done 4, so the 10000 occurrence is lost too "
                  "(spell_priest.cpp:2847-2854; hook likely unbound on 69497 data: build skew)",
     "probe": True},
    {"id": "AL-T-D-17", "question": "UNK-E-003: self-cast restart reapply completing at the ms a tick is due",
     "scenario": {"spell": DOT, "update_every": 100, "until": 20000,
                  "events": [{"t": 0, "op": "create"}, {"t": 6000, "op": "reapply"}]},
     "rules_out": "'due tick observed before same-ms cast completion' for owner==caster: the cast completes in "
                  "WorldObject::Update (Object.cpp:247) before _UpdateSpells, restart drops the 6000 occurrence", "probe": True},
    {"id": "AL-T-D-18", "question": "UNK-E-003: same, but the casting unit is updated after the aura owner",
     "scenario": {"spell": DOT, "update_every": 100, "until": 20000,
                  "events": [{"t": 0, "op": "create"}, {"t": 6000, "op": "reapply", "phase": "after"}]},
     "rules_out": "a caster-independent rule: for caster!=owner the outcome follows map update order", "probe": False},
)


def _compact(result: dict[str, Any]) -> dict[str, Any]:
    rows = []
    for r in result["rows"]:
        st = r.get("state") or {}
        effs = st.get("effects") or [{}]
        rows.append({k: v for k, v in {
            "t": r["t"], "event": r["event"], "diff": r.get("diff"),
            "ticks": [t["tick"] for t in r.get("ticks", [])] or None,
            "reset_effective": r.get("reset_effective"),
            "duration": st.get("duration"), "max_duration": st.get("max_duration"), "stack": st.get("stack"),
            "timer": effs[0].get("timer"), "period": effs[0].get("period"), "ticks_done": effs[0].get("ticks_done"),
            "total_ticks": effs[0].get("total_ticks"), "removed": st.get("removed"),
        }.items() if v is not None})
    return {"tick_times": result["tick_times"], "tick_count": result["tick_count"], "rows": rows}


def timelines() -> list[dict[str, Any]]:
    out = []
    for spec in TIMELINE_SPECS:
        res = run(spec["scenario"])
        out.append({**spec, "result": _compact(res),
                    "evidence": ["trinity-consumer", "differential", "trinity-probe"] if spec["probe"] else ["trinity-consumer", "structural-inference"],
                    "probe_scope": ("verbatim components; the call order between them (event before owner update, "
                                    "ModStackAmount before the Spell.cpp duration block, UpdateOwner/expiry loops) is "
                                    "driver-glued after the cited source (R2-13)") if spec["probe"] else None,
                    "probe_test": "tests/test_al_d_probe.py" if spec["probe"] else None})
    return sorted(out, key=lambda r: r["id"])


def script_rows(ctx) -> dict[str, Any]:
    """Current-player periodic spells whose bound scripts touch periodic state (structural, Track H owns semantics)."""
    from .providers import populations, provider_effects
    player = populations(ctx)["player"]
    spells = sorted({r["spell"] for r in provider_effects(ctx.data)
                     if r["spell"] in player and r["aura"] in PERIODIC_AURAS})
    rows = []
    for s in spells:
        f = script_facts(ctx, s)
        if f["periodic_hooks"] or f["timer_calls"]:
            rows.append({"spell": s, "name": ctx.name(s), **f})
    return {"population": "player periodic provider spells", "denominator": len(spells),
            "with_periodic_hooks_or_timer_calls": len(rows), "rows": rows,
            "evidence": "script-consumer", "note": "structural presence only; per-script semantics are Track H"}


def drift_rows(ctx) -> dict[str, Any]:
    """Build drift (69497 -> 69814) of periodic lifecycle inputs for current-player periodic effects."""
    drift = ctx.drift
    if drift is None:
        return {"available": False}
    from .providers import populations
    player = populations(ctx)["player"]
    words = (0, 1, 5, 8, 10, 13)
    changes = []
    compared = 0
    for (spell, diff), effs in sorted(ctx.data.table("SpellEffect").items()):
        if diff or spell not in player:
            continue
        for idx, e in sorted(effs.items()):
            if e["EffectAura"] not in PERIODIC_AURAS:
                continue
            compared += 1
            d = (drift.table("SpellEffect").get((spell, 0)) or {}).get(idx)
            a0, a1 = spell_attrs(ctx.data, spell), spell_attrs(drift, spell)
            before = {"aura": e["EffectAura"], "period": e["EffectAuraPeriod"], "effattr": e["EffectAttributes"],
                      **{f"attr{w}": a0[w] for w in words}, "duration": base_duration(ctx.data, spell)["duration"]}
            after = None if d is None else {"aura": d["EffectAura"], "period": d["EffectAuraPeriod"],
                                             "effattr": d.get("EffectAttributes"),
                                             **{f"attr{w}": a1[w] for w in words},
                                             "duration": base_duration(drift, spell)["duration"]}
            if after != before:
                changes.append({"spell": spell, "index": idx, "before": before, "after": after})
    return {"available": True, "population": "player periodic provider effects", "compared": compared,
            "changed": len(changes), "rows": changes, "evidence": "build-drift"}


MODIFY_AURA_STACKS = 289   # SPELL_EFFECT_MODIFY_AURA_STACKS -> Spell::EffectModifyAuraStacks (SpellEffects.cpp:6126-6146)


def modify_stacks_rows(ctx) -> dict[str, Any]:
    """DB2 effect-289 rows and the periodic auras they restack (R2-04).

    MiscValue 0 -> ``ModStackAmount(value)`` with the default ``resetPeriodicTimer = true`` (restart unless raw 436);
    MiscValue 1 -> ``SetStackAmount(value)`` (no RefreshTimers).  Population = the *source* spell's.
    """
    from .providers import controlled_spells
    # 289 carriers are not aura providers, so populations are over source spells directly:
    # all = every DB2 row, player = Scope.reach, controlled = controlled-unit spells.
    reach = frozenset(ctx.scope.reach)
    ctrl = controlled_spells()
    data = ctx.data
    effects = data.table("SpellEffect")
    rows = []
    for (spell, diff), effs in sorted(effects.items()):
        if diff:
            continue
        for idx, e in sorted(effs.items()):
            if e["Effect"] != MODIFY_AURA_STACKS:
                continue
            target = e["EffectTriggerSpell"]
            t_effs = effects.get((target, 0), {})
            periodic = [i for i, te in sorted(t_effs.items()) if te["EffectAura"] in PERIODIC_AURAS and te["EffectAuraPeriod"]]
            opts = data.row("SpellAuraOptions", target)
            cap = opts["CumulativeAura"] if opts else 0
            value = e["EffectBasePointsF"]
            tat = spell_attrs(data, target) if t_effs else (0,) * 17
            if e["EffectMiscValue_0"] == 0:
                kind = "mod-add" if value > 0 else ("full-refresh(value 0)" if value == 0 else "mod-remove")
            elif e["EffectMiscValue_0"] == 1:
                kind = "set"
            else:
                kind = f"miscvalue-{e['EffectMiscValue_0']}(no-op)"
            rows.append({"spell": spell, "index": idx, "target": target, "kind": kind, "value": value,
                         "target_periodic_effects": periodic, "target_stack_cap": cap,
                         "target_pandemic": has(tat, A13_PANDEMIC),
                         "restarts_periodic_phase": bool(periodic) and kind in ("mod-add", "full-refresh(value 0)")
                                                    and not has(tat, A13_PANDEMIC),
                         "populations": ["all"] + (["controlled"] if spell in ctrl else []) + (["player"] if spell in reach else [])})
    def count(pred, pop):
        return sum(1 for r in rows if pred(r) and pop in r["populations"])
    stacking_add = lambda r: r["kind"] == "mod-add" and r["target_periodic_effects"] and r["target_stack_cap"] >= 2  # noqa: E731
    value0 = lambda r: r["kind"] == "full-refresh(value 0)" and r["target_periodic_effects"]  # noqa: E731
    summary = {pop: {"effect_289_rows": count(lambda r: True, pop),
                     "adds_stacks_to_stacking_periodic_aura": count(stacking_add, pop),
                     "value0_full_refresh_of_periodic_aura": count(value0, pop),
                     "restarts_periodic_phase": count(lambda r: r["restarts_periodic_phase"], pop)}
               for pop in ("all", "player", "controlled")}
    return {"unit": "effect-289 rows (DIFFICULTY_NONE); all = every row, player = source in Scope.reach, controlled = "
                    "source in controlled-unit spells", "summary": summary,
            "rows": [r for r in rows if r["target_periodic_effects"]],
            "evidence": ["db2-fact", "trinity-consumer"], "coords": ["SpellEffects.cpp:6126-6146", "SpellAuras.cpp:1093-1127"],
            "reference": "R2-04 (tc_aura_r2_probe) reported 18 all-population stacking-add rows"}


# Refresh / stack delivery paths and the resetPeriodicTimer they pass (R1-05, R2-04).
DELIVERY_PATHS = [
    {"path": "spell-hit", "coords": ["Spell.cpp:3240", "Spell.cpp:3248", "Unit.cpp:3441"],
     "reset": "StackAmount<2 && !(triggerFlags & TRIGGERED_DONT_RESET_PERIODIC_TIMER)", "duration_override": True},
    {"path": "Unit::AddAura (linked LINK_AURA, scripts)", "coords": ["Unit.cpp:12287-12319", "SpellAuras.h:135", "Unit.cpp:3441"],
     "reset": "true (AuraCreateInfo default)", "duration_override": False},
    {"path": "effect 289 MODIFY_AURA_STACKS MiscValue 0", "coords": ["SpellEffects.cpp:6126-6146"],
     "reset": "true (ModStackAmount default); value 0 = full refresh without a stack", "duration_override": False},
    {"path": "effect 289 MiscValue 1 (SetStackAmount)", "coords": ["SpellEffects.cpp:6141"],
     "reset": "no RefreshTimers (phase and duration untouched)", "duration_override": False},
    {"path": "HandleAuraLinked stack sync", "coords": ["SpellAuraEffects.cpp:5358"], "reset": "true (default)", "duration_override": False},
    {"path": "spell steal", "coords": ["Unit.cpp:4068"], "reset": "true (default)", "duration_override": False},
    {"path": "AuraScript::ModStackAmount wrapper / direct Aura::ModStackAmount in scripts",
     "coords": ["SpellScript.cpp:1181-1184", "spell_item.cpp:4733"],
     "reset": "wrapper: always default true; direct calls may pass false (Arcane Tempest)",
     "duration_override": False},
]
DELIVERY_PATHS_NOTE = "raw 436 forces preserve on every path via RefreshTimers (SpellAuras.cpp:981-985)"
