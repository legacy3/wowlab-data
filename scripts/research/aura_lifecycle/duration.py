"""Track B -- authored aura duration and the Trinity duration pipeline.

Two layers, kept apart on purpose:

* **authored facts** (:func:`authored`, :func:`family`, :func:`census`) -- what the
  snapshot says: ``SpellMisc.DurationIndex`` -> ``SpellDuration`` (Duration,
  MaxDuration, DurationPerResource), ``PvPDurationIndex``, ``MinDuration`` and the
  lifecycle attributes that change duration.  Pure reads, ``db2-fact``.
* **consumer arithmetic** (``spellinfo_*`` .. :func:`hit_duration`) -- Trinity's
  duration pipeline for one application reproduced step by step, including its
  binary32 / int32 arithmetic.  Every function carries a ``Mirrors:`` line.  Inputs
  that belong to other subsystems (spell-mod totals, target duration-mod totals,
  diminishing level, haste values, hastened periods) are explicit arguments: this
  module never guesses them.

Trinity is a consumer oracle here, not Retail truth.  Fields Trinity never reads
(``PvPDurationIndex``) or reads for something else (``MinDuration`` = missile
travel floor, Spell.cpp:888-896) are reported, never given an aura meaning.
"""

from __future__ import annotations

import math
import struct
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from procs.enums import attr

from . import FailClosed

# --------------------------------------------------------------------------
# attributes (word, mask) resolved by Trinity name from SharedDefines.h
# --------------------------------------------------------------------------
ATTR0_PASSIVE = attr("SPELL_ATTR0_PASSIVE")
ATTR0_IS_ABILITY = attr("SPELL_ATTR0_IS_ABILITY")
ATTR0_IS_TRADESKILL = attr("SPELL_ATTR0_IS_TRADESKILL")
ATTR0_USES_RANGED_SLOT = attr("SPELL_ATTR0_USES_RANGED_SLOT")
ATTR1_IS_CHANNELLED = attr("SPELL_ATTR1_IS_CHANNELLED")
ATTR1_IS_SELF_CHANNELLED = attr("SPELL_ATTR1_IS_SELF_CHANNELLED")
ATTR1_AURA_UNIQUE = attr("SPELL_ATTR1_AURA_UNIQUE")
ATTR1_FINISHING_MOVE_DURATION = attr("SPELL_ATTR1_FINISHING_MOVE_DURATION")
ATTR2_AUTO_REPEAT = attr("SPELL_ATTR2_AUTO_REPEAT")
ATTR3_DOT_STACKING_RULE = attr("SPELL_ATTR3_DOT_STACKING_RULE")
ATTR3_IGNORE_CASTER_MODIFIERS = attr("SPELL_ATTR3_IGNORE_CASTER_MODIFIERS")
ATTR5_EXTRA_INITIAL_PERIOD = attr("SPELL_ATTR5_EXTRA_INITIAL_PERIOD")
ATTR5_SPELL_HASTE_AFFECTS_PERIODIC = attr("SPELL_ATTR5_SPELL_HASTE_AFFECTS_PERIODIC")
ATTR5_AURA_UNIQUE_PER_CASTER = attr("SPELL_ATTR5_AURA_UNIQUE_PER_CASTER")
ATTR7_NO_TARGET_DURATION_MOD = attr("SPELL_ATTR7_NO_TARGET_DURATION_MOD")
ATTR8_HASTE_AFFECTS_DURATION = attr("SPELL_ATTR8_HASTE_AFFECTS_DURATION")
ATTR8_MELEE_HASTE_AFFECTS_PERIODIC = attr("SPELL_ATTR8_MELEE_HASTE_AFFECTS_PERIODIC")
ATTR9_DO_NOT_LOG_AURA_REFRESH = attr("SPELL_ATTR9_DO_NOT_LOG_AURA_REFRESH")
ATTR10_ROLLING_PERIODIC = attr("SPELL_ATTR10_ROLLING_PERIODIC")
ATTR13_PERIODIC_REFRESH_EXTENDS_DURATION = attr("SPELL_ATTR13_PERIODIC_REFRESH_EXTENDS_DURATION")
# simc engine/dbc/data_enums.hh:1909 SX_AURA_DOES_NOT_REFRESH = 489 = word 15 bit 9.
# Trinity names it SPELL_ATTR15_UNK9 and has no consumer.
ATTR15_AURA_DOES_NOT_REFRESH_SIMC = attr("SPELL_ATTR15_UNK9")
# simc data_enums.hh:1910 SX_ASYNCHRONOUS_STACKING_AURA = 490 = word 15 bit 10 (buff.cpp:777-779: each stack
# owns its own expiration when max stack > 1).  Trinity SPELL_ATTR15_UNK10, no consumer.  Stacks are track C.
ATTR15_ASYNC_STACKING_SIMC = attr("SPELL_ATTR15_UNK10")

# Attributes that change duration or refresh, with the raw id used by simc (word*32+bit).
LIFECYCLE_ATTRS: dict[str, tuple[int, int]] = {
    "ATTR0_PASSIVE": ATTR0_PASSIVE,
    "ATTR1_AURA_UNIQUE": ATTR1_AURA_UNIQUE,
    "ATTR1_FINISHING_MOVE_DURATION": ATTR1_FINISHING_MOVE_DURATION,
    "ATTR1_CHANNELLED": (1, ATTR1_IS_CHANNELLED[1] | ATTR1_IS_SELF_CHANNELLED[1]),
    "ATTR3_DOT_STACKING_RULE": ATTR3_DOT_STACKING_RULE,
    "ATTR5_AURA_UNIQUE_PER_CASTER": ATTR5_AURA_UNIQUE_PER_CASTER,
    "ATTR5_EXTRA_INITIAL_PERIOD": ATTR5_EXTRA_INITIAL_PERIOD,
    "ATTR5_SPELL_HASTE_AFFECTS_PERIODIC": ATTR5_SPELL_HASTE_AFFECTS_PERIODIC,
    "ATTR7_NO_TARGET_DURATION_MOD": ATTR7_NO_TARGET_DURATION_MOD,
    "ATTR8_HASTE_AFFECTS_DURATION": ATTR8_HASTE_AFFECTS_DURATION,
    "ATTR8_MELEE_HASTE_AFFECTS_PERIODIC": ATTR8_MELEE_HASTE_AFFECTS_PERIODIC,
    "ATTR9_DO_NOT_LOG_AURA_REFRESH": ATTR9_DO_NOT_LOG_AURA_REFRESH,
    "ATTR10_ROLLING_PERIODIC": ATTR10_ROLLING_PERIODIC,
    "ATTR13_PERIODIC_REFRESH_EXTENDS_DURATION": ATTR13_PERIODIC_REFRESH_EXTENDS_DURATION,
    "ATTR15_UNK9_SIMC_AURA_DOES_NOT_REFRESH": ATTR15_AURA_DOES_NOT_REFRESH_SIMC,
    "ATTR15_UNK10_SIMC_ASYNCHRONOUS_STACKING": ATTR15_ASYNC_STACKING_SIMC,
}

SPELL_EMPOWER_HOLD_TIME_AT_MAX = 1000  # Spell.h:88 (1 * IN_MILLISECONDS)
POWER_COMBO_POINTS = 4                 # SharedDefines.h Powers

# --------------------------------------------------------------------------
# binary32 / int32 helpers (reproduce, never "clean up")
# --------------------------------------------------------------------------
_I32_MIN, _I32_MAX = -(2 ** 31), 2 ** 31 - 1


def f32(x: float) -> float:
    """Round a Python double to IEEE binary32 (x86-64 SSE float, -ffp-contract=off)."""
    return struct.unpack("<f", struct.pack("<f", x))[0]


def to_int32(x: float) -> int:
    """C++ ``int32(float)`` for in-range values: truncation toward zero."""
    if math.isnan(x) or x >= 2 ** 31 or x < -(2 ** 31):
        raise FailClosed(f"int32 conversion of {x!r} is undefined behaviour in C++")
    return int(x)


def wrap_int32(x: int) -> int:
    x &= 0xFFFFFFFF
    return x - 2 ** 32 if x >= 2 ** 31 else x


def cdiv(a: int, b: int) -> int:
    """C++ integer division (truncates toward zero)."""
    q = abs(a) // abs(b)
    return q if (a >= 0) == (b > 0) else -q


def calculate_pct(base: int, pct: float) -> int:
    """``CalculatePct<int32>``: ``T(base * static_cast<float>(pct) / 100.0f)``.

    Mirrors: common/Utilities/Util.h:72-75
    """
    return to_int32(f32(f32(f32(float(base)) * f32(pct)) / f32(100.0)))


def add_pct(base: int, pct: float) -> int:
    """Mirrors: common/Utilities/Util.h:85-88 (``base += CalculatePct(base, pct)``)."""
    return wrap_int32(base + calculate_pct(base, pct))


def mul_int_float(value: int, factor: float) -> int:
    """``int32(value * floatField)`` (int promoted to binary32, product in binary32)."""
    return to_int32(f32(f32(float(value)) * f32(factor)))


# --------------------------------------------------------------------------
# authored facts
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class DurationEntry:
    id: int
    duration: int
    max_duration: int
    per_resource: int


@dataclass(frozen=True)
class SpellFacts:
    """The duration-relevant ``SpellInfo`` members of one DIFFICULTY_NONE spell."""
    spell: int
    attributes: tuple[int, ...]
    duration_entry: DurationEntry | None
    pvp_duration_entry: DurationEntry | None = None
    min_duration: float = 0.0
    stack_amount: int = 0
    empower: bool = False
    has_misc: bool = True
    periods: tuple[int, ...] = ()  # authored EffectAuraPeriod of aura effects (index order)

    def has(self, key: tuple[int, int]) -> bool:
        word, mask = key
        return word < len(self.attributes) and bool(self.attributes[word] & mask)

    @property
    def passive(self) -> bool:
        return self.has(ATTR0_PASSIVE)

    @property
    def channeled(self) -> bool:
        """Mirrors: SpellInfo.cpp:1889-1892 ``IsChanneled``."""
        return bool(self.attributes[1] & (ATTR1_IS_CHANNELLED[1] | ATTR1_IS_SELF_CHANNELLED[1])) \
            if self.attributes else False


def _entry(data, index: int) -> DurationEntry | None:
    if not index:
        return None
    row = data.duration(index)
    if row is None:
        raise FailClosed(f"SpellDuration {index} referenced but absent")
    return DurationEntry(index, row["Duration"], row["MaxDuration"], row["DurationPerResource"])


def empower_spells(source) -> frozenset[int]:
    """Spells with at least one SpellEmpowerStage (``SpellInfo::IsEmpowerSpell``, SpellInfo.cpp:1916)."""
    if not source.has("SpellEmpower") or not source.has("SpellEmpowerStage"):
        return frozenset()
    with_stage = {r[0] for r in source.project("SpellEmpowerStage", ("SpellEmpowerID",))}
    return frozenset(s for i, s in source.project("SpellEmpower", ("ID", "SpellID")) if i in with_stage)


def _corrected_attributes(data, spell: int, misc: dict) -> tuple[int, ...]:
    """SpellMisc attribute words with the load-time ``SPELL_ATTR0_PASSIVE`` corrections applied.

    Mirrors: SpellMgr.cpp:3839-3843, 5301-5302 via :func:`aura_lifecycle.passive.effective_passive`
    (one passive definition for every classifier, R1-02).
    """
    from .passive import effective_passive
    words = [misc[f"Attributes_{i}"] for i in range(17)]
    if effective_passive(data, spell)["passive"]:
        words[0] = int(words[0]) | ATTR0_PASSIVE[1]
    return tuple(words)


def facts(data, spell: int, empower: frozenset[int] = frozenset()) -> SpellFacts:
    misc = data.row("SpellMisc", spell)
    opts = data.row("SpellAuraOptions", spell)
    periods = tuple(e["EffectAuraPeriod"] for e in data.effects(spell) if e["EffectAura"])
    if misc is None:
        return SpellFacts(spell, tuple([0] * 17), None, None, 0.0, opts["CumulativeAura"] if opts else 0,
                          spell in empower, False, periods)
    return SpellFacts(
        spell=spell,
        attributes=_corrected_attributes(data, spell, misc),
        duration_entry=_entry(data, misc["DurationIndex"]),
        pvp_duration_entry=_entry(data, misc["PvPDurationIndex"]),
        min_duration=float(misc["MinDuration"]),
        stack_amount=opts["CumulativeAura"] if opts else 0,
        empower=spell in empower,
        has_misc=True,
        periods=periods,
    )


def family(f: SpellFacts) -> str:
    """Authored duration family (db2-fact; names describe the record, not a runtime meaning)."""
    e = f.duration_entry
    if not f.has_misc:
        return "no-misc-row"
    if e is None:
        return "no-duration-index-passive" if f.passive else "no-duration-index-active"
    if e.duration == -1 and e.max_duration == -1:
        return "permanent-sentinel"
    if e.duration < 0 or e.max_duration < 0:
        return "negative-non-sentinel"
    if e.duration == 0 and e.max_duration == 0:
        return "zero"
    if e.duration == e.max_duration:
        return "fixed"
    if e.per_resource:
        return "ranged-per-resource"
    return "ranged-no-increment"


# --------------------------------------------------------------------------
# consumer arithmetic
# --------------------------------------------------------------------------
def spellinfo_get_duration(f: SpellFacts) -> int:
    """Mirrors: SpellInfo.cpp:3986-3991 ``SpellInfo::GetDuration`` (only exactly -1 is permanent; abs otherwise)."""
    e = f.duration_entry
    if e is None:
        return -1 if f.passive else 0
    return -1 if e.duration == -1 else abs(e.duration)


def spellinfo_get_max_duration(f: SpellFacts) -> int:
    """Mirrors: SpellInfo.cpp:3993-3998 ``SpellInfo::GetMaxDuration``."""
    e = f.duration_entry
    if e is None:
        return -1 if f.passive else 0
    return -1 if e.max_duration == -1 else abs(e.max_duration)


def calc_spell_duration(f: SpellFacts, combo_points: int | None) -> int:
    """``WorldObject::CalcSpellDuration``.  ``combo_points=None`` = no power-cost list
    (every non-Spell path, and ``Aura::CalcMaxDuration(caster)`` which passes nullptr).

    Only POWER_COMBO_POINTS is searched; any other consumed resource leaves the minimum.

    Mirrors: Object.cpp:1707-1725
    """
    minduration = spellinfo_get_duration(f)
    if minduration <= 0:
        return minduration
    maxduration = spellinfo_get_max_duration(f)
    if minduration == maxduration:
        return minduration
    if combo_points is None:
        return minduration
    return min(wrap_int32(minduration + f.duration_entry.per_resource * combo_points), maxduration)


@dataclass(frozen=True)
class SpellMods:
    """Totals ``Player::GetSpellModValues`` would return for one SpellModOp (flat, pct-product)."""
    flat: int = 0
    mul: float = 1.0

    def apply(self, value: int) -> int:
        """Mirrors: Player.cpp:22851 ``basevalue = T((double(basevalue) + totalflat) * totalmul)``."""
        return to_int32((float(value) + self.flat) * f32(self.mul))


@dataclass(frozen=True)
class Caster:
    """Caster facts the duration pipeline reads.  ``mod_owner`` = ``GetSpellModOwner() != nullptr``."""
    mod_owner: bool = True
    duration_mods: SpellMods = SpellMods()
    change_cast_time_mods: SpellMods = SpellMods()
    period_mods: SpellMods = SpellMods()
    mod_casting_speed: float = 1.0          # UnitData::ModCastingSpeed (binary32)
    mod_haste: float = 1.0                  # UnitData::ModHaste (binary32, melee haste periodic)
    ranged_attack_speed_pct: float = 1.0    # m_modAttackSpeedPct[RANGED_ATTACK]
    is_player: bool = True
    spell_family: int = 1                   # SpellInfo::SpellFamilyName of the spell (nonzero for class spells)


def calc_max_duration(f: SpellFacts, caster: Caster | None, combo_points: int | None = None) -> int:
    """Static ``Aura::CalcMaxDuration(spellInfo, caster, powerCosts)``.

    Mirrors: SpellAuras.cpp:910-935
    """
    if caster is not None:
        max_duration = calc_spell_duration(f, combo_points)
    else:
        max_duration = spellinfo_get_duration(f)
    if f.passive and f.duration_entry is None:
        max_duration = -1
    if max_duration != -1:
        if caster is not None and caster.mod_owner:
            max_duration = caster.duration_mods.apply(max_duration)
        if f.empower:
            max_duration = wrap_int32(max_duration + SPELL_EMPOWER_HOLD_TIME_AT_MAX)
    return max_duration


@dataclass(frozen=True)
class TargetDurationMods:
    """Totals the target's aura lists return inside ``ModSpellDuration`` (all <= 0 in practice)."""
    mechanic_total: int = 0        # GetTotalAuraModifier(SPELL_AURA_MECHANIC_DURATION_MOD, mechanicCheck)
    mechanic_not_stack: int = 0    # GetMaxNegativeAuraModifier(..._NOT_STACK)
    dispel_total: int = 0          # GetTotalAuraModifierByMiscValue(MOD_AURA_DURATION_BY_DISPEL, Dispel)
    dispel_not_stack: int = 0      # GetMaxNegativeAuraModifierByMiscValue(..._NOT_STACK)
    is_unit: bool = True


def mod_spell_duration(f: SpellFacts, target: TargetDurationMods, duration: int, positive: bool) -> int:
    """``WorldObject::ModSpellDuration`` (negative path; the positive Mixology branch is
    Wrath-era potion policy and is refused).

    Mirrors: Object.cpp:1727-1790
    """
    if duration < 0:
        return duration
    if f.has(ATTR7_NO_TARGET_DURATION_MOD):
        return duration
    if not target.is_unit:
        return duration
    if not positive:
        mod = min(target.mechanic_total, target.mechanic_not_stack)
        if mod != 0:
            duration = add_pct(duration, mod)
        mod = min(target.dispel_total, target.dispel_not_stack)
        if mod != 0:
            duration = add_pct(duration, mod)
    return max(duration, 0)


DR_MULTIPLIER = {1: 1.0, 2: 0.5, 3: 0.25, "immune": 0.0}


def apply_diminishing(duration: int, level: int | str, limit_duration: int = 0,
                      limit_applies: bool = False, group_none: bool = False) -> tuple[int, bool]:
    """``Unit::ApplyDiminishingToDuration`` for the *default* DR group kind (not taunt / knockback).

    Group derivation (``SpellInfo::_LoadSpellDiminishInfo``) is not ported: the caller states
    the level and whether the limit applies (player source vs DR-affected target).

    Mirrors: Unit.cpp:9354-9431
    """
    if duration == -1 or group_none:
        return duration, True
    if limit_duration > 0 and duration > limit_duration and limit_applies:
        duration = limit_duration
    if level not in DR_MULTIPLIER:
        raise FailClosed(f"diminishing level {level!r} not modelled")
    duration = to_int32(f32(f32(float(duration)) * f32(DR_MULTIPLIER[level])))
    return duration, duration != 0


def mod_spell_duration_time(f: SpellFacts, caster: Caster, duration: int, is_player_object: bool = True) -> int:
    """``WorldObject::ModSpellDurationTime`` (channel duration / channel period haste).

    Mirrors: Object.cpp:1818-1838
    """
    if duration < 0:
        return duration
    if f.channeled and not f.has(ATTR5_SPELL_HASTE_AFFECTS_PERIODIC) and not f.has(ATTR8_MELEE_HASTE_AFFECTS_PERIODIC):
        return duration
    if caster.mod_owner:
        duration = caster.change_cast_time_mods.apply(duration)
    ability = f.has(ATTR0_IS_ABILITY) or f.has(ATTR0_IS_TRADESKILL) or f.has(ATTR3_IGNORE_CASTER_MODIFIERS)
    if not ability and ((is_player_object and caster.spell_family) or not is_player_object):
        return mul_int_float(duration, caster.mod_casting_speed)
    if f.has(ATTR0_USES_RANGED_SLOT) and not f.has(ATTR2_AUTO_REPEAT):
        return mul_int_float(duration, caster.ranged_attack_speed_pct)
    return duration


def haste_affects_duration(orig: int, hastened_periods: tuple[int, ...], mod_casting_speed: float) -> int:
    """ATTR8_HASTE_AFFECTS_DURATION on application: align to whole (already hastened) periods of
    the live aura effects; with no periodic effect, multiply by casting speed.

    Note: the *unhastened* duration is floored to whole hastened periods -- haste never lengthens
    it and the result is <= orig whenever a period exists (``max(orig/period, 1)`` -> at least one
    period, which may exceed orig).

    Mirrors: Spell.cpp:3271-3282
    """
    out = 0
    for period in hastened_periods:
        if period:
            out = max(max(cdiv(orig, period), 1) * period, out)
    if not out:
        out = mul_int_float(orig, mod_casting_speed)
    return out


def pandemic_extend(hit_duration: int, current_duration: int) -> int:
    """ATTR13 branch: ``min(hit + aura->GetDuration(), CalculatePct(hit, 130))``.

    ``current_duration`` is whatever ``HitAura->GetDuration()`` returns *at that point* -- see
    :mod:`aura_lifecycle.refresh` for what that is on the real refresh path.

    Mirrors: Spell.cpp:3284-3288
    """
    return min(wrap_int32(hit_duration + current_duration), calculate_pct(hit_duration, 130))


@dataclass
class HitInputs:
    """One unit hit of one cast (``TargetInfo`` + ``SpellValue`` + relation facts)."""
    caster: Caster | None = field(default_factory=Caster)
    combo_points: int | None = None          # m_powerCost combo-point entry (None = none consumed)
    spell_value_duration: int | None = None  # SpellValue::Duration (SPELLVALUE_DURATION)
    duration_mul: float = 1.0                # SpellValue::DurationMul
    positive: bool = False                   # TargetInfo::Positive (IsPositiveEffect not ported)
    friendly_non_self: bool = False          # origCaster != unit && IsFriendlyTo -> Positive stays true
    dr_level: int | str | None = None        # None = no DR group
    dr_limit: int = 0
    dr_limit_applies: bool = False
    target_mods: TargetDurationMods = field(default_factory=TargetDurationMods)
    hastened_periods: tuple[int, ...] = ()   # AuraEffect::GetPeriod() of the hit aura (after CalculatePeriodic)


def hit_quote(f: SpellFacts, h: HitInputs) -> tuple[int, list[dict[str, Any]], bool]:
    """Pre-creation quote ``TargetInfo::AuraDuration`` (+ DR immunity).

    Mirrors: Spell.cpp:3195-3222 (PreprocessSpellHit aura branch)
    """
    steps: list[dict[str, Any]] = []
    positive = True if h.friendly_non_self else h.positive
    if h.spell_value_duration is not None:
        d = h.spell_value_duration
        steps.append({"step": "spell_value_duration", "value": d, "coords": "Spell.cpp:3213-3214"})
    else:
        d = calc_max_duration(f, h.caster, h.combo_points)
        steps.append({"step": "Aura::CalcMaxDuration(spell, origCaster, &m_powerCost)", "value": d,
                      "coords": "Spell.cpp:3216; SpellAuras.cpp:910-935"})
    immune = False
    if not positive and h.dr_level is not None:
        d, ok = apply_diminishing(d, h.dr_level, h.dr_limit, h.dr_limit_applies)
        steps.append({"step": "ApplyDiminishingToDuration", "value": d, "coords": "Spell.cpp:3219; Unit.cpp:9354"})
        immune = not ok
    return d, steps, immune


def hit_commit(f: SpellFacts, h: HitInputs, quoted: int, *, refresh: bool,
               aura_duration_now: int, aura_max_duration_now: int) -> tuple[int, int, int, list[dict[str, Any]]]:
    """Post-``TryRefreshStackOrCreate`` duration commit of one hit.

    ``aura_duration_now`` / ``aura_max_duration_now`` are the found-or-created aura's
    ``GetDuration()`` / ``GetMaxDuration()`` *after* TryRefreshStackOrCreate returned (for a
    refresh these are already the RefreshTimers values -- :mod:`refresh` supplies them).

    Returns ``(final_hit_duration, aura_duration, aura_max_duration, steps)``.

    Mirrors: Spell.cpp:3261-3298
    """
    steps: list[dict[str, Any]] = []
    positive = True if h.friendly_non_self else h.positive
    d = quoted
    if h.spell_value_duration is None:
        d = mod_spell_duration(f, h.target_mods, d, positive)
        steps.append({"step": "ModSpellDuration", "value": d, "coords": "Spell.cpp:3262; Object.cpp:1727"})
        if d > 0:
            d = mul_int_float(d, h.duration_mul)
            steps.append({"step": "*= DurationMul", "value": d, "coords": "Spell.cpp:3266"})
            if f.channeled:
                if h.caster is None:
                    raise FailClosed("channel duration without a caster")
                d = mod_spell_duration_time(f, h.caster, d)
                steps.append({"step": "ModSpellDurationTime (channel)", "value": d, "coords": "Spell.cpp:3269-3270"})
            elif f.has(ATTR8_HASTE_AFFECTS_DURATION):
                if h.caster is None:
                    raise FailClosed("ATTR8 haste duration without a caster")
                d = haste_affects_duration(d, h.hastened_periods, h.caster.mod_casting_speed)
                steps.append({"step": "ATTR8_HASTE_AFFECTS_DURATION", "value": d, "coords": "Spell.cpp:3271-3282"})
            if refresh and f.has(ATTR13_PERIODIC_REFRESH_EXTENDS_DURATION):
                d = pandemic_extend(d, aura_duration_now)
                steps.append({"step": "ATTR13 pandemic", "value": d, "reads_aura_duration": aura_duration_now,
                              "coords": "Spell.cpp:3284-3288"})
    else:
        d = h.spell_value_duration
    dur, mx = aura_duration_now, aura_max_duration_now
    if d != mx:
        mx, dur = d, d
        steps.append({"step": "SetMaxDuration+SetDuration", "value": d, "coords": "Spell.cpp:3294-3298"})
    else:
        steps.append({"step": "no-set (hit == current max)", "value": d, "coords": "Spell.cpp:3294"})
    return d, dur, mx, steps


def channel_duration(f: SpellFacts, caster: Caster, spell_value_duration: int | None = None,
                     duration_mul: float = 1.0, empower_min_hold: bool = False) -> int:
    """``Spell::handle_immediate`` channel duration (m_channelDuration).  Empower stage split
    is not modelled beyond the +1000 ms hold.

    Mirrors: Spell.cpp:3994-4040
    """
    duration = spellinfo_get_duration(f)
    if not (duration > 0 or (spell_value_duration or 0) > 0):
        raise FailClosed("channel with non-positive duration does not start channeling")
    if spell_value_duration is not None:
        return spell_value_duration
    if caster.mod_owner:
        duration = caster.duration_mods.apply(duration)
    duration = mul_int_float(duration, duration_mul)
    duration = mod_spell_duration_time(f, caster, duration)
    if f.empower:
        duration = wrap_int32(duration + SPELL_EMPOWER_HOLD_TIME_AT_MAX)
    return duration


# --------------------------------------------------------------------------
# census
# --------------------------------------------------------------------------
def flags(f: SpellFacts) -> list[str]:
    out = [name for name, key in LIFECYCLE_ATTRS.items()
           if (f.attributes[key[0]] & key[1] if key[0] < len(f.attributes) else 0)]
    if f.empower:
        out.append("EMPOWER")
    return out


def explain(ctx, spell: int) -> dict[str, Any]:
    """``aura_lifecycle.py duration <spell>``."""
    data = ctx.data
    f = facts(data, spell, empower_spells(ctx.bundle.source))
    out: dict[str, Any] = {
        "spell": spell, "name": ctx.name(spell), "build_skew": ctx.is_skew(spell),
        "evidence": ["db2-fact", "trinity-consumer"],
        "authored": {
            "has_misc": f.has_misc,
            "duration_entry": f.duration_entry.__dict__ if f.duration_entry else None,
            "pvp_duration_entry": f.pvp_duration_entry.__dict__ if f.pvp_duration_entry else None,
            "pvp_duration_consumer": "none in Trinity (SpellInfo.cpp:1364 loads DurationIndex only) -> retail-unknown",
            "min_duration": f.min_duration,
            "min_duration_consumer": "missile travel floor Spell.cpp:888-896 / Object.cpp:2621 (not an aura floor)",
            "family": family(f),
            "stack_amount": f.stack_amount,
            "aura_periods": list(f.periods),
            "lifecycle_flags": flags(f),
        },
        "trinity": {
            "GetDuration": spellinfo_get_duration(f),
            "GetMaxDuration": spellinfo_get_max_duration(f),
            "CalcMaxDuration_no_caster": calc_max_duration(f, None),
            "CalcMaxDuration_caster_no_mods": calc_max_duration(f, Caster()),
            "channeled": f.channeled,
            "coords": ["SpellInfo.cpp:3986-3998", "Object.cpp:1707-1725", "SpellAuras.cpp:910-935"],
        },
    }
    if family(f) == "ranged-per-resource":
        out["trinity"]["combo_points"] = {str(cp): calc_max_duration(f, Caster(), cp) for cp in range(0, 8)}
    if f.channeled and spellinfo_get_duration(f) > 0:
        out["trinity"]["channel_duration_no_haste"] = channel_duration(f, Caster())
    if drift := ctx.drift:
        try:
            g = facts(drift, spell)
            out["drift"] = {"snapshot": "12.1.0.69814", "family": family(g),
                            "duration_entry": g.duration_entry.__dict__ if g.duration_entry else None,
                            "lifecycle_flags": flags(g),
                            "changed": (g.duration_entry != f.duration_entry) or flags(g) != [x for x in flags(f) if x != "EMPOWER"],
                            "evidence": "build-drift"}
        except (FailClosed, KeyError) as exc:  # drift release may lack a table
            out["drift"] = {"error": str(exc)}
    return out


def census(ctx, populations: dict[str, frozenset[int]]) -> dict[str, Any]:
    """Duration-family and lifecycle-flag census per population (provider spells)."""
    data = ctx.data
    emp = empower_spells(ctx.bundle.source)
    fam: dict[str, Counter] = {}
    flag: dict[str, Counter] = {}
    entries: dict[str, Counter] = {}
    pvp: dict[str, int] = {}
    witnesses: dict[str, dict[str, list[int]]] = {}
    for pop, spells in populations.items():
        fc, gc, ec = Counter(), Counter(), Counter()
        wit: dict[str, list[int]] = {}
        pv = 0
        for spell in sorted(spells):
            f = facts(data, spell, emp)
            k = family(f)
            fc[k] += 1
            wit.setdefault(k, [])
            if len(wit[k]) < 5:
                wit[k].append(spell)
            for name in flags(f):
                gc[name] += 1
            if f.pvp_duration_entry:
                pv += 1
            if f.duration_entry:
                ec[f.duration_entry.id] += 1
        fam[pop], flag[pop], entries[pop], pvp[pop], witnesses[pop] = fc, gc, ec, pv, wit
    return {
        "families": {p: dict(sorted(c.items())) for p, c in fam.items()},
        "flags": {p: dict(sorted(c.items())) for p, c in flag.items()},
        "pvp_duration_index_nonzero": pvp,
        "distinct_duration_entries": {p: len(c) for p, c in entries.items()},
        "family_witnesses": witnesses,
    }
