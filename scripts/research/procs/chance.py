"""Pure chance-model calculators.  No randomness is drawn here.

Every model Trinity can execute for a proc provider, with the exact float
arithmetic of the consumer.  Trinity evaluates these expressions in C++
``float`` (IEEE-754 binary32); this module reproduces that by rounding every
intermediate result to binary32 *correctly* (:func:`round_f32`, exact rational
arithmetic), so a Python result is bit-identical to the C++ one for the same
operation order.  C++ literals such as ``0.01f`` are rounded from their decimal
text (:func:`lit`), not from a Python double.  ``tools/tc_proc_probe`` checks that claim.

Mirrors:
* ``Aura::CalcProcChance``                     -> :func:`aura_proc_chance`
* ``Unit::GetPPMProcChance``                   -> :func:`classic_ppm_chance`
* ``SpellInfo::CalcProcPPM`` and the three ``CalcPPM*Mod`` helpers -> :func:`rppm_rate`
* ``Aura::CalcPPMProcChance``                  -> :func:`rppm_chance`
* ``Unit::GetWeaponProcChance``                -> :func:`weapon_proc_chance`
* ``Player::CastItemCombatSpell`` (both halves) -> :func:`item_on_proc_chance`,
  :func:`enchant_combat_spell_chance`
* ``roll_chance<float>`` / ``rand_chance``      -> :func:`roll_succeeds` (given a roll)

RPPM modifier types whose semantics this module does not prove raise
:class:`~procs.UnsupportedSource`.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Any

from . import UnsupportedSource
from .enums import (
    PPM_MOD_NAMES,
    SPELL_PPM_MOD_AURA,
    SPELL_PPM_MOD_BATTLEGROUND,
    SPELL_PPM_MOD_CLASS,
    SPELL_PPM_MOD_CRIT,
    SPELL_PPM_MOD_HASTE,
    SPELL_PPM_MOD_ITEM_LEVEL,
    SPELL_PPM_MOD_RACE,
    SPELL_PPM_MOD_SPEC,
)

# ---------------------------------------------------------------------------
# binary32
# ---------------------------------------------------------------------------

_TWO = Fraction(2)
_F32_OVERFLOW = _TWO ** 128


def round_f32(value: Fraction | float) -> float:
    """Correctly rounded (ties-to-even) conversion of an exact value to binary32.

    binary32 has a 24-bit significand; a finite value with exponent ``e``
    (``2**e <= |x| < 2**(e+1)``) is quantised to ``2**(e-23)``, subnormals
    (``e < -126``) to ``2**-149``.  Anything that rounds to ``>= 2**128``
    overflows to infinity.
    """
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return value
    value = Fraction(value)
    if value == 0:
        return 0.0
    sign = -1.0 if value < 0 else 1.0
    a = abs(value)
    e = a.numerator.bit_length() - a.denominator.bit_length()
    if _TWO ** e > a:
        e -= 1
    shift = 23 - e if e >= -126 else 149
    scaled = a * _TWO ** shift
    q, r = divmod(scaled.numerator, scaled.denominator)
    twice = 2 * r
    if twice > scaled.denominator or (twice == scaled.denominator and q & 1):
        q += 1
    result = Fraction(q) / _TWO ** shift
    if result >= _F32_OVERFLOW:
        return sign * math.inf
    return sign * float(result)


def f32(value: float) -> float:
    """A C++ ``float`` literal / implicit conversion."""
    return round_f32(Fraction(value) if not isinstance(value, float) else value)


def lit(text: str) -> float:
    """A C++ ``float`` literal written as ``text`` (e.g. ``lit("0.01")`` for ``0.01f``)."""
    return round_f32(Fraction(text))


def _fr(x: float) -> Fraction:
    return Fraction(x)


def mul(a: float, b: float) -> float:
    return round_f32(_fr(a) * _fr(b))


def div(a: float, b: float) -> float:
    if b == 0:
        if a == 0 or math.isnan(a):
            return math.nan
        return math.copysign(math.inf, a) * math.copysign(1.0, b)
    if math.isinf(b):
        return 0.0 if not math.isinf(a) else math.nan
    if math.isinf(a):
        return math.copysign(math.inf, a) * math.copysign(1.0, b)
    return round_f32(_fr(a) / _fr(b))


def add(a: float, b: float) -> float:
    if math.isinf(a) or math.isinf(b):
        return a + b
    return round_f32(_fr(a) + _fr(b))


def sub(a: float, b: float) -> float:
    if math.isinf(a) or math.isinf(b):
        return a - b
    return round_f32(_fr(a) - _fr(b))


def fmax(a: float, b: float) -> float:
    """``std::max(a, b)``: returns ``a`` unless ``a < b``."""
    return max(a, b)


def fmin(a: float, b: float) -> float:
    """``std::min(a, b)``: returns ``a`` unless ``b < a``."""
    return min(a, b)


@dataclass
class Stage:
    name: str
    value: float
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"stage": self.name, "value": self.value, "note": self.note}


@dataclass
class ChanceResult:
    model: str
    chance_percent: float
    stages: list[Stage] = field(default_factory=list)
    external: list[str] = field(default_factory=list)

    @property
    def probability(self) -> float:
        """Probability that ``roll_chance`` succeeds: ``P(U[0,100) < chance)``."""
        return min(max(self.chance_percent, 0.0), 100.0) / 100.0

    def to_dict(self) -> dict[str, Any]:
        return {"model": self.model, "chance_percent": self.chance_percent,
                "probability": self.probability,
                "stages": [s.to_dict() for s in self.stages], "external": self.external}


# ---------------------------------------------------------------------------
# RNG boundary
# ---------------------------------------------------------------------------

def roll_succeeds(chance_percent: float, roll: float) -> bool:
    """``roll_chance<float>``: ``chance > rand_chance()`` with ``rand_chance`` in ``[0, 100)``.

    ``roll`` is the value ``rand_chance()`` returned (a binary32 in [0, 100)).
    """
    if not 0.0 <= roll < 100.0:
        raise ValueError("rand_chance() returns a value in [0, 100)")
    return chance_percent > roll


# ---------------------------------------------------------------------------
# classic PPM
# ---------------------------------------------------------------------------

def classic_ppm_chance(weapon_speed_ms: int, ppm: float, *, ppm_after_spellmods: float | None = None
                       ) -> ChanceResult:
    """Mirrors: ``Unit::GetPPMProcChance``.

    ``floor((WeaponSpeed * PPM) / 600.0f)`` with ``WeaponSpeed`` a uint32 of
    milliseconds.  Returns a *whole* percent.  ``ppm_after_spellmods`` stands
    in for ``SpellModOp::ProcFrequency`` (an external input).
    """
    stages = [Stage("ppm", f32(ppm))]
    if f32(ppm) <= 0:
        stages.append(Stage("ppm<=0", 0.0, "early return"))
        return ChanceResult("classic-ppm", 0.0, stages)
    rate = f32(ppm_after_spellmods) if ppm_after_spellmods is not None else f32(ppm)
    if ppm_after_spellmods is not None:
        stages.append(Stage("ppm after ProcFrequency spell mods", rate))
    speed = f32(int(weapon_speed_ms) & 0xFFFFFFFF)
    product = mul(speed, rate)
    stages.append(Stage("WeaponSpeed * PPM", product))
    quotient = div(product, f32(600.0))
    stages.append(Stage("/ 600", quotient))
    chance = float(math.floor(quotient))
    stages.append(Stage("floor", chance, "whole percent"))
    return ChanceResult("classic-ppm", chance, stages,
                        ["SpellModOp::ProcFrequency", "caster base attack time"])


def weapon_proc_chance(main_ready: bool, main_speed_ms: int, has_offhand: bool,
                       off_ready: bool, off_speed_ms: int) -> float:
    """Mirrors: ``Unit::GetWeaponProcChance`` (reads live swing-timer state)."""
    if main_ready:
        return div(mul(f32(main_speed_ms), lit("1.8")), f32(1000.0))
    if has_offhand and off_ready:
        return div(mul(f32(off_speed_ms), lit("1.6")), f32(1000.0))
    return 0.0


# ---------------------------------------------------------------------------
# RPPM
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RppmInputs:
    """Everything ``SpellInfo::CalcProcPPM`` reads from the caster.

    Haste fields are the ``UF::UnitData`` multipliers (``ModHaste`` etc.): a
    unit with 20% haste has ``mod_haste = 1/1.2``.  Crit fields are the
    ``ActivePlayerData`` percentages.
    """

    has_caster: bool = True
    mod_haste: float = 1.0
    mod_ranged_haste: float = 1.0
    mod_spell_haste: float = 1.0
    mod_haste_regen: float = 1.0
    is_player: bool = True
    crit_pct: float = 0.0
    ranged_crit_pct: float = 0.0
    spell_crit_pct: float = 0.0
    class_id: int | None = None
    primary_spec: int | None = None
    race_id: int | None = None
    item_level: int = -1                 # Aura::GetCastItemLevel()
    in_battleground_or_arena: bool | None = None
    auras: frozenset[int] | None = None  # caster->HasAura(id)


#: Trinity::RaceMask::GetRaceBit (RaceMask.h)
_RACE_BIT = {r: r - 1 for r in (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 22, 24, 25, 26, 27, 28, 29, 30, 31, 32)}
_RACE_BIT.update({34: 11, 35: 12, 36: 13, 37: 14, 52: 16, 70: 15, 84: 17, 85: 18, 91: 19, 86: 20})


def race_mask_has_race(mask: int, race_id: int) -> bool:
    """``Trinity::RaceMask<int32>{mask}.HasRace(race)``."""
    bit = _RACE_BIT.get(race_id, -1)
    return 0 <= bit < 32 and bool((mask & 0xFFFFFFFF) & (1 << bit))


def _haste_term(mod: Mapping[str, Any], i: RppmInputs) -> float:
    coeff = f32(mod["coeff"])
    one = f32(1.0)
    haste = {1: i.mod_haste, 2: i.mod_ranged_haste, 3: i.mod_spell_haste,
             4: i.mod_haste_regen}.get(mod["param"])
    if mod["param"] == 5:
        haste = fmin(fmin(fmin(f32(i.mod_haste), f32(i.mod_ranged_haste)),
                          f32(i.mod_spell_haste)), f32(i.mod_haste_regen))
    if haste is None:
        return 0.0
    return mul(sub(div(one, f32(haste)), one), coeff)


def _crit_term(mod: Mapping[str, Any], i: RppmInputs) -> float:
    if not i.is_player:
        return 0.0
    coeff = f32(mod["coeff"])
    crit = {1: i.crit_pct, 2: i.ranged_crit_pct, 3: i.spell_crit_pct}.get(mod["param"])
    if mod["param"] == 4:
        crit = fmin(fmin(f32(i.crit_pct), f32(i.ranged_crit_pct)), f32(i.spell_crit_pct))
    if crit is None:
        return 0.0
    return mul(mul(f32(crit), coeff), lit("0.01"))


def rppm_rate(base_ppm: float, mods: Sequence[Mapping[str, Any]], inputs: RppmInputs,
              random_property_points=None) -> ChanceResult:
    """Mirrors: ``SpellInfo::CalcProcPPM(Unit* caster, int32 itemLevel)``.

    ``random_property_points(item_level) -> float`` must implement
    ``GetRandomPropertyPoints(itemLevel, ITEM_QUALITY_RARE, INVTYPE_CHEST, 0)``;
    it is only called for an ITEM_LEVEL modifier.
    """
    ppm = f32(base_ppm)
    stages = [Stage("BaseProcRate", ppm)]
    external: list[str] = []
    if not inputs.has_caster:
        stages.append(Stage("no caster", ppm, "modifiers skipped"))
        return ChanceResult("rppm-rate", ppm, stages, external)
    one = f32(1.0)
    for mod in mods:
        mtype = mod["type"]
        name = PPM_MOD_NAMES.get(mtype, f"TYPE_{mtype}")
        label = f"mod {mod.get('id', '?')} {name} param={mod['param']} coeff={mod['coeff']}"
        if mtype == SPELL_PPM_MOD_HASTE:
            term = _haste_term(mod, inputs)
            ppm = mul(ppm, add(one, term))
            stages.append(Stage(label, ppm, f"x (1 + {term!r})"))
        elif mtype == SPELL_PPM_MOD_CRIT:
            term = _crit_term(mod, inputs)
            ppm = mul(ppm, add(one, term))
            stages.append(Stage(label, ppm, f"x (1 + {term!r})"))
        elif mtype == SPELL_PPM_MOD_CLASS:
            if inputs.class_id is None:
                raise UnsupportedSource("CLASS modifier needs the caster class")
            applies = bool((1 << (inputs.class_id - 1)) & (mod["param"] & 0xFFFFFFFF))
            if applies:
                ppm = mul(ppm, add(one, f32(mod["coeff"])))
            stages.append(Stage(label, ppm, "applies" if applies else "class not in mask"))
        elif mtype == SPELL_PPM_MOD_SPEC:
            if not inputs.is_player:
                stages.append(Stage(label, ppm, "caster is not a player"))
                continue
            if inputs.primary_spec is None:
                raise UnsupportedSource("SPEC modifier needs the caster's primary specialization")
            applies = inputs.primary_spec == mod["param"]
            if applies:
                ppm = mul(ppm, add(one, f32(mod["coeff"])))
            stages.append(Stage(label, ppm, "applies" if applies else "other spec"))
        elif mtype == SPELL_PPM_MOD_RACE:
            if inputs.race_id is None:
                raise UnsupportedSource("RACE modifier needs the caster race")
            applies = race_mask_has_race(mod["param"], inputs.race_id)
            if applies:
                ppm = mul(ppm, add(one, f32(mod["coeff"])))
            stages.append(Stage(label, ppm, "applies" if applies else "race not in mask"))
        elif mtype == SPELL_PPM_MOD_ITEM_LEVEL:
            if inputs.item_level == mod["param"]:
                term = 0.0
                note = "item level equals Param"
            else:
                if random_property_points is None:
                    raise UnsupportedSource("ITEM_LEVEL modifier needs RandPropPoints")
                points = f32(random_property_points(inputs.item_level))
                base = f32(random_property_points(mod["param"]))
                if points == base:
                    term = 0.0
                    note = "equal RandPropPoints"
                else:
                    term = mul(sub(div(points, base), one), f32(mod["coeff"]))
                    note = f"RandPropPoints {points!r}/{base!r}"
            ppm = mul(ppm, add(one, term))
            stages.append(Stage(label, ppm, note))
            external.append("aura cast item level")
        elif mtype == SPELL_PPM_MOD_BATTLEGROUND:
            if inputs.in_battleground_or_arena is None:
                raise UnsupportedSource("BATTLEGROUND modifier needs the caster's map type")
            if inputs.in_battleground_or_arena:
                ppm = mul(ppm, add(one, f32(mod["coeff"])))
            stages.append(Stage(label, ppm, "in BG/arena" if inputs.in_battleground_or_arena else "not BG/arena"))
        elif mtype == SPELL_PPM_MOD_AURA:
            if inputs.auras is None:
                raise UnsupportedSource("AURA modifier needs the caster's aura set")
            applies = mod["param"] in inputs.auras
            if applies:
                ppm = mul(ppm, add(one, f32(mod["coeff"])))
            stages.append(Stage(label, ppm, "caster has aura" if applies else "aura absent"))
        else:
            # CalcProcPPM's `default: break;` -- the consumer ignores it.  We
            # report the silent skip instead of hiding it.
            stages.append(Stage(label, ppm, "unknown type: Trinity ignores it"))
            external.append(f"modifier type {mtype} is ignored by Trinity (unproved semantics)")
    return ChanceResult("rppm-rate", ppm, stages, external)


def rppm_chance(ppm: float, seconds_since_last_attempt: float, seconds_since_last_proc: float
                ) -> ChanceResult:
    """Mirrors: ``Aura::CalcPPMProcChance`` after ``CalcProcPPM``.

    The two elapsed times are ``duration_cast<FloatSeconds>`` values
    (``double``); the ``std::min`` clamps happen in double and the results are
    narrowed to ``float`` on assignment.
    """
    stages: list[Stage] = []
    ppm = f32(ppm)
    interval = div(f32(60.0), ppm)
    stages.append(Stage("averageProcInterval = 60 / ppm", interval))
    since_attempt = f32(min(float(seconds_since_last_attempt), 10.0))
    since_proc = f32(min(float(seconds_since_last_proc), 1000.0))
    stages.append(Stage("secondsSinceLastAttempt (<= 10)", since_attempt))
    stages.append(Stage("secondsSinceLastProc (<= 1000)", since_proc))
    ratio = div(since_proc, interval)
    blp_inner = add(f32(1.0), mul(sub(ratio, f32(1.5)), f32(3.0)))
    blp = fmax(f32(1.0), blp_inner)
    stages.append(Stage("bad-luck multiplier max(1, 1 + (sinceProc/interval - 1.5) * 3)", blp))
    chance = div(mul(mul(blp, ppm), since_attempt), f32(60.0))
    stages.append(Stage("x ppm x sinceAttempt / 60", chance))
    clamped = chance
    clamped = max(clamped, 0.0)
    clamped = min(clamped, 1.0)
    if math.isnan(chance):
        clamped = chance
    stages.append(Stage("RoundToInterval(0, 1)", clamped))
    percent = mul(clamped, f32(100.0))
    stages.append(Stage("x 100", percent))
    return ChanceResult("rppm", percent, stages,
                        ["per-Aura m_lastProcAttemptTime / m_lastProcSuccessTime"])


# ---------------------------------------------------------------------------
# Aura::CalcProcChance
# ---------------------------------------------------------------------------

def reduce_proc_60(chance: float, actor_level: int) -> float:
    """The PROC_ATTR_REDUCE_PROC_60 tail of ``Aura::CalcProcChance``."""
    if actor_level > 60:
        factor = sub(f32(1.0), div(mul(f32(actor_level - 60), f32(1.0)), f32(30.0)))
        return fmax(f32(0.0), mul(factor, f32(chance)))
    return chance


def aura_proc_chance(entry_chance: float, entry_ppm: float, spell_base_ppm: float, *,
                     has_caster: bool, has_damage_info: bool,
                     weapon_speed_ms: int | None = None,
                     rppm: ChanceResult | None = None,
                     spellmod_chance=None, reduce_60: bool = False,
                     actor_level: int | None = None) -> ChanceResult:
    """Mirrors: ``Aura::CalcProcChance``; branch selection and the final chance.

    ``rppm`` is the already-computed :func:`rppm_chance` result (it depends on
    per-aura timing state, which the caller owns).  ``spellmod_chance`` is an
    optional callable standing in for ``ApplySpellMod(..., ProcChance, chance)``.
    """
    chance = f32(entry_chance)
    stages = [Stage("SpellProcEntry::Chance", chance)]
    external = []
    if has_caster:
        if has_damage_info and entry_ppm != 0:
            if weapon_speed_ms is None:
                raise UnsupportedSource("classic PPM needs the caster's base attack time")
            ppm = classic_ppm_chance(weapon_speed_ms, entry_ppm)
            chance = ppm.chance_percent
            stages += ppm.stages
        if spell_base_ppm > 0.0:
            if rppm is None:
                raise UnsupportedSource("RPPM needs rppm_chance(...) for this aura's timing state")
            chance = rppm.chance_percent
            stages += rppm.stages
        if spellmod_chance is not None:
            chance = f32(spellmod_chance(chance))
            stages.append(Stage("SpellModOp::ProcChance", chance))
        else:
            external.append("SpellModOp::ProcChance (assumed no modifier)")
    else:
        stages.append(Stage("no caster", chance, "PPM/RPPM and spell mods skipped"))
    if reduce_60:
        if actor_level is None:
            raise UnsupportedSource("PROC_ATTR_REDUCE_PROC_60 needs the actor level")
        chance = reduce_proc_60(chance, actor_level)
        stages.append(Stage("REDUCE_PROC_60", chance))
    model = ("rppm" if has_caster and spell_base_ppm > 0.0
             else "classic-ppm" if has_caster and has_damage_info and entry_ppm != 0
             else "fixed")
    return ChanceResult(model, chance, stages, external)


# ---------------------------------------------------------------------------
# Player::CastItemCombatSpell
# ---------------------------------------------------------------------------

def item_on_proc_chance(triggered_spell_proc_chance: int, item_spell_ppm_rate: float,
                        attack_speed_ms: int, weapon_proc_chance_value: float | None) -> ChanceResult:
    """ItemEffect TriggerType ON_PROC half of ``Player::CastItemCombatSpell``.

    Chance comes from the *triggered* spell's ``SpellAuraOptions.ProcChance``
    unless ``item_template_addon.SpellPPMChance`` is set; a stored chance above
    100 means "use ``GetWeaponProcChance``" (live swing-timer state).
    """
    chance = f32(float(triggered_spell_proc_chance))
    stages = [Stage("triggered SpellInfo::ProcChance", chance)]
    if item_spell_ppm_rate:
        res = classic_ppm_chance(attack_speed_ms, item_spell_ppm_rate)
        stages += res.stages
        return ChanceResult("item-classic-ppm", res.chance_percent, stages, res.external)
    if chance > 100.0:
        if weapon_proc_chance_value is None:
            raise UnsupportedSource("chance > 100 selects GetWeaponProcChance (swing-timer state)")
        stages.append(Stage("GetWeaponProcChance", weapon_proc_chance_value))
        return ChanceResult("item-weapon-proc-chance", weapon_proc_chance_value, stages)
    return ChanceResult("item-fixed", chance, stages)


def enchant_combat_spell_chance(effect_points_min: int, enchant_proc: Mapping[str, Any] | None,
                                item_delay_ms: int, weapon_proc_chance_value: float | None
                                ) -> ChanceResult:
    """Enchantment half of ``Player::CastItemCombatSpell`` (before spell mods)."""
    stages = []
    if effect_points_min != 0:
        chance = f32(float(effect_points_min))
        stages.append(Stage("SpellItemEnchantment.EffectPointsMin", chance))
    else:
        if weapon_proc_chance_value is None:
            raise UnsupportedSource("EffectPointsMin == 0 selects GetWeaponProcChance")
        chance = weapon_proc_chance_value
        stages.append(Stage("GetWeaponProcChance", chance))
    model = "enchant-fixed"
    if enchant_proc:
        if enchant_proc.get("ProcsPerMinute"):
            res = classic_ppm_chance(item_delay_ms, enchant_proc["ProcsPerMinute"])
            chance = res.chance_percent
            stages += res.stages
            model = "enchant-classic-ppm"
        elif enchant_proc.get("Chance"):
            chance = f32(float(enchant_proc["Chance"]))
            stages.append(Stage("spell_enchant_proc_data.Chance", chance))
    return ChanceResult(model, chance, stages,
                        ["SpellModOp::ProcChance", "Shiv (5938) temp-enchant override to 100",
                         "two independent roll_chance draws per effect (see docs)"])
