"""Swing scheduling: attack timers, haste application, interleaving, extra attacks, auto-shot.

Research oracle only.  Reproduces the *scheduling* arithmetic of the pinned
TrinityCore checkout (``7f3d43b``) for a unit attacking with main hand, off
hand and ranged auto-repeat.  Weapon speed, haste percentages and damage are
supplied inputs (Track C / gearing own them).

Ordered rule list (:data:`RULES`) is the corpus; :func:`simulate` executes
those rules for a fixed host tick.  The tick is a *host parameter*: Trinity
feeds ``Unit::Update(uint32 p_time)`` whatever ``diff`` the world loop
measured (worldserver ``WorldUpdateLoop``, ``MinWorldUpdateTime`` default 1 ms,
Main.cpp:535-569; map updates ``MapUpdateInterval`` default 10 ms,
World.cpp:721), so there is no canonical tick and swing deadlines are only
ever resolved at update granularity.

Types (Unit.h:1519-1521):
  ``std::array<uint32, MAX_ATTACK> m_baseAttackSpeed`` (ms),
  ``std::array<float,  MAX_ATTACK> m_modAttackSpeedPct`` (multiplier, 1.0 = unhasted),
  ``std::array<uint32, MAX_ATTACK> m_attackTimer`` (ms remaining).
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field, asdict
from typing import Any

from . import CORPORA, SNAPSHOT_BUILD, TRINITY_COMMIT
from .attack_table import f32, f32_add, f32_div, f32_mul, int32_trunc

ATTACK_DISPLAY_DELAY = 200  # Unit.h:630 (#define ATTACK_DISPLAY_DELAY 200), ms
BASE_ATTACK_TIME = 2000     # UnitDefines.h:35 (#define BASE_ATTACK_TIME 2000), ms
ATT = ("base", "off", "ranged")  # WeaponAttackType BASE_ATTACK=0, OFF_ATTACK=1, RANGED_ATTACK=2

#: Ordered rules.  Every entry is one consumer fact with its coordinate.
RULES: list[dict[str, Any]] = [
    {"id": "S01", "rule": "Timers are uint32 milliseconds; a timer is ready when it equals 0 (isAttackReady).",
     "coord": "Unit.h:697-700, Unit.h:1521", "class": "trinity-consumer"},
    {"id": "S02", "rule": "Unit constructor: m_baseAttackSpeed = {}, m_attackTimer = {} (all 0 -> ready), m_modAttackSpeedPct filled with 1.0f.",
     "coord": "Unit.cpp:325-327", "class": "trinity-consumer"},
    {"id": "S03", "rule": "Base attack time: players get the weapon's ItemSparse delay per slot (proto->GetDelay()) or BASE_ATTACK_TIME=2000 when no weapon/shapeshift CombatRoundTime; creatures use creature_template BaseAttackTime/RangeAttackTime.",
     "coord": "Player.cpp:8135-8161 (_ApplyWeaponDamage), Player.cpp:5393-5399, Player.cpp:23391-23393 (shapeshift), Creature.cpp:643-645, Pet.cpp:874-876/1023", "class": "trinity-consumer",
     "note": "supplied input here; Track C owns the weapon model"},
    {"id": "S04", "rule": "resetAttackTimer(type): m_attackTimer[type] = uint32(GetBaseAttackTime(type) * m_modAttackSpeedPct[type]) -- uint32 * float -> float product, truncated.",
     "coord": "Unit.cpp:665-668", "class": "trinity-consumer"},
    {"id": "S05", "rule": "UpdateAttackTimeField: the client-visible AttackRoundBaseTime[att] / RangedAttackRoundBaseTime = uint32(m_baseAttackSpeed[att] * m_modAttackSpeedPct[att]) (same truncation as S04).",
     "coord": "Unit.cpp:10962-10976", "class": "trinity-consumer"},
    {"id": "S06", "rule": "ApplyAttackTimePercentMod(att, val, apply): remainingTimePct = float(timer) / (base * modPct); modPct *= (100+|val|)/100 or 100/(100+|val|) (ApplyPercentModFloatVar, sign of val decides direction); ModHaste (BASE) / ModRangedHaste (RANGED) update fields mirror it; then UpdateAttackTimeField and timer = uint32(base * modPct * remainingTimePct). A haste change preserves the *fraction* of the swing remaining, truncating to ms.",
     "coord": "Unit.cpp:10978-11007", "class": "trinity-consumer"},
    {"id": "S07", "rule": "Haste sources feeding ApplyAttackTimePercentMod: SPELL_AURA_MOD_MELEE_HASTE(138)/MOD_MELEE_HASTE_2(217)/MOD_MELEE_HASTE_3(319) -> BASE+OFF (HandleModMeleeSpeedPct, exclusive spell-group); MOD_MELEE_RANGED_HASTE(192)/MOD_MELEE_RANGED_HASTE_2(342) -> BASE+OFF+RANGED (HandleModMeleeRangedSpeedPct); MELEE_SLOW(193)/AURA_252 -> BASE+OFF+RANGED + cast time (HandleModCombatSpeedPct, exclusive group); MOD_ATTACKSPEED(9) -> BASE only (HandleModAttackSpeed); MOD_RANGED_HASTE(140) -> RANGED only; CR_HASTE_MELEE rating -> BASE+OFF, CR_HASTE_RANGED -> RANGED (old value unapplied then new value applied, each through the rating diminishing curve).",
     "coord": "SpellAuraEffects.cpp:81,210,212,264,265,289,324,391,414; :4528-4603; Player.cpp:5317-5340 (UpdateRating); Player.cpp:8015-8016 (ITEM_MOD_HASTE_RATING feeds all three CR_HASTE_*)", "class": "trinity-consumer"},
    {"id": "S08", "rule": "Unit::Attack(victim, meleeAttack): does not touch the MH timer; for NON-players with an off hand it sets OFF timer = max(OFF timer, BASE timer + uint32(CalculatePct(GetBaseAttackTime(BASE), 50))). Players get no off-hand offset; on target switch it interrupts CURRENT_MELEE_SPELL and keeps both timers.",
     "coord": "Unit.cpp:5852-5962 (:5944-5946 offhand delay, :5909-5912 switch target)", "class": "trinity-consumer"},
    {"id": "S09", "rule": "AttackStop / CombatStop clear UNIT_STATE_MELEE_ATTACKING and interrupt CURRENT_MELEE_SPELL; timers are NOT reset, so a re-engaged attacker resumes with whatever remained (0 -> immediate swing).",
     "coord": "Unit.cpp:5964-5988, 6011-6044", "class": "trinity-consumer"},
    {"id": "S10", "rule": "Unit::Update order: WorldObject::Update (event queue: pending Spell::update / casts) -> _UpdateSpells (incl. _UpdateAutoRepeatSpell) -> combat manager -> extra attacks (HandleProcExtraAttackFor) -> timer decrement for BASE, OFF, RANGED: timer = (p_time >= timer ? 0 : timer - p_time) unless the current generic or channeled spell has SPELL_ATTR6_DELAY_COMBAT_TIMER_DURING_CAST (then no decrement at all).",
     "coord": "Unit.cpp:423-470 (:428, :433, :441-454, :456-469)", "class": "trinity-consumer",
     "note": "_lastDamagedTargetGuid is cleared at :441 before the drain, so AddExtraAttacks from a spell cast outside a swing targets the selection (Unit.cpp:2376-2384); a timer already at 0 is not touched (`if (uint32 t = getAttackTimer(...))`)"},
    {"id": "S11", "rule": "The swing itself happens in Unit::DoMeleeAttackIfReady, called from Player::Update and Creature::Update after Unit::Update in the same tick; it requires UNIT_STATE_MELEE_ATTACKING, not charging, creature CanMelee, not casting (unless the channel allows actions), a victim, melee range and facing (2*pi/3 arc unless within boundary radius).",
     "coord": "Unit.cpp:2176-2251 (:2178-2206), Player.cpp:1012, Creature.cpp:876", "class": "trinity-consumer"},
    {"id": "S12", "rule": "Interleaving: if BASE is ready: (a) if haveOffhandWeapon and OFF timer < ATTACK_DISPLAY_DELAY(200) -> OFF timer = 200; (b) AttackerStateUpdate(BASE); (c) resetAttackTimer(BASE). Then, in the same call, if not feral and haveOffhandWeapon and OFF is ready: (a) if BASE timer < 200 -> BASE timer = 200; (b) AttackerStateUpdate(OFF); (c) resetAttackTimer(OFF). Because (a) of the MH block always pushes OFF to >= 200 before the OH check, MH and OH never swing in the same update; the hand that reads ready first wins (MH is tested first).",
     "coord": "Unit.cpp:2210-2226 (MH), :2231-2247 (OH)", "class": "trinity-consumer"},
    {"id": "S13", "rule": "Out of range / bad facing while ready: timer is set to 100 ms (retry), not reset; players get SetAttackSwingError.",
     "coord": "Unit.cpp:2224-2225, 2244-2245, 2227-2228", "class": "trinity-consumer"},
    {"id": "S14", "rule": "Dual-wield timer initialisation for players: both timers start at 0 (S02) -> first update after Attack() swings MH and pushes OH to 200 ms; OH then swings ~200 ms later and both run at their own full periods thereafter (no half-swing offset for players).",
     "coord": "Unit.cpp:325-327, 5944-5946, 2214-2218", "class": "trinity-consumer"},
    {"id": "S15", "rule": "Same-deadline ordering when both reach 0 in one update: MH first (tested first), OH deferred to +200 ms (S12).",
     "coord": "Unit.cpp:2210-2247", "class": "trinity-consumer"},
    {"id": "S16", "rule": "haveOffhandWeapon: Player -> GetWeaponForAttack(OFF_ATTACK, useable=true) != nullptr (offhand slot holds a non-broken ITEM_CLASS_WEAPON); creatures -> CanDualWield() (m_canDualWield; SPELL_EFFECT_DUAL_WIELD / Creature::SetCanDualWield).",
     "coord": "Unit.cpp:525-531, Player.cpp:9582-9612, Unit.h:702-703, Creature.cpp:1970", "class": "trinity-consumer"},
    {"id": "S17", "rule": "Casts reset swing timers (Spell::ResetCombatTimers: BASE, OFF if haveOffhandWeapon, RANGED, each via resetAttackTimer) when Spell::IsAutoActionResetSpell: not triggered, SpellInterrupts.InterruptFlags has Combat (0x8), not SPELL_ATTR2_DO_NOT_RESET_COMBAT_TIMERS, and not (instant && SPELL_ATTR6_DOESNT_RESET_SWING_TIMER_IF_INSTANT). It happens in Spell::_cast after the launch phase, or in Spell::prepare when SPELL_ATTR7_RESET_SWING_TIMER_AT_SPELL_START.",
     "coord": "Spell.cpp:8640-8650, 8311-8320, 3886-3887, 3572-3573; SpellDefines.h:65", "class": "trinity-consumer"},
    {"id": "S18", "rule": "Pause: SPELL_ATTR6_DELAY_COMBAT_TIMER_DURING_CAST on the current generic/channeled spell freezes all three timers (S10); nothing else pauses them (UNIT_STATE_* only blocks the swing, see S11).",
     "coord": "Unit.cpp:456-461", "class": "trinity-consumer"},
    {"id": "S19", "rule": "Queued melee spells (CURRENT_MELEE_SPELL, SPELL_ATTR0_ON_NEXT_SWING[_NO_DAMAGE]): when BASE is ready and a melee spell is queued and this is not an extra attack, the queued spell is cast instead of the white swing (no CalculateMeleeDamage, no white proc event); the timer still resets.",
     "coord": "Unit.cpp:2292-2293, Spell.cpp:8157-8160 (GetCurrentContainer), SpellInfo.cpp:1899-1902", "class": "trinity-consumer",
     "reachability": "0 spells with either attribute in current-player scope (189 / 396 in the whole catalog) -> legacy-only"},
    {"id": "S20", "rule": "Auto-attack override (SPELL_AURA_OVERRIDE_AUTOATTACK_WITH_MELEE_SPELL, 361): BASE swing casts EffectInfo.TriggerSpell (front aura), OFF swing casts MiscValue of the first aura with MiscValue != 0; a fake HITINFO_NO_ANIMATION attack state is sent; the white damage/outcome/proc path is skipped entirely; the timer still resets.",
     "coord": "Unit.cpp:2299-2316, 2352-2361", "class": "trinity-consumer", "witness": "404542 Crusading Strikes (Retribution) -> 408385"},
    {"id": "S21", "rule": "Extra attacks: SPELL_EFFECT_ADD_EXTRA_ATTACKS -> Unit::AddExtraAttacks(count) queues count against _lastDamagedTargetGuid (or the selection); Spell::_handle_finish_phase records _lastExtraAttackSpell; the NEXT Unit::Update drains the queue with AttackerStateUpdate(victim, BASE_ATTACK, extra=true) before the timer decrement. extra=true bypasses UNIT_STATE_CANNOT_AUTOATTACK and the queued-melee-spell branch, does not reset or touch any timer, and produces the same white-swing proc event as a normal MH swing (CalculateMeleeDamage does not see `extra`). A normal swing clears _lastExtraAttackSpell.",
     "coord": "SpellEffects.cpp:3688-3701, Unit.cpp:2374-2387, Spell.cpp:4209-4213, Unit.cpp:441-454, 2365-2372, 2267, 2288-2293", "class": "trinity-consumer",
     "reachability": "0 spells with SPELL_EFFECT_ADD_EXTRA_ATTACKS in current-player scope (28 in the catalog) -> legacy-only"},
    {"id": "S22", "rule": "Ranged auto-shot is a spell (SPELL_ATTR2_AUTO_REPEAT -> CURRENT_AUTOREPEAT_SPELL). Cadence: _UpdateAutoRepeatSpell (inside _UpdateSpells, BEFORE the timer decrement) fires when isAttackReady(RANGED) and the current auto-repeat spell is not PREPARING: it creates a new Spell(TRIGGERED_IGNORE_GCD) and prepares it; the spell's SpellEvent (scheduled at +1 ms) executes on the NEXT WorldObject::Update, casts, and Spell::_cast -> SendSpellCooldown -> resetAttackTimer(RANGED_ATTACK). CheckCast also refuses when the ranged timer is not ready.",
     "coord": "Unit.cpp:2962-2963, 3019-3055, Spell.cpp:3466-3467, 4226-4235, 3893, 5800-5801, 4372-4374", "class": "trinity-consumer",
     "note": "with a fixed tick T and P = uint32(base * modPct), shots are ceil(P / T) + 1 updates apart: the timer reaches 0 in the decrement of one update, _UpdateAutoRepeatSpell prepares in the next (it runs before the decrement), and the SpellEvent (EventProcessor time + 1 ms, EventProcessor.cpp:40-47, WorldObject::Update Object.cpp:245-247) casts in the update after that. Melee swings fire in the same update the timer reaches 0 (DoMeleeAttackIfReady runs after Unit::Update), so their period is ceil(P / T) updates. Auto Shot 75 has CastingTimeIndex 1 (Base 0) -> no cast time"},
    {"id": "S23", "rule": "Hard-coded Auto Shot exemptions are keyed on spell id 75 only: any other auto-repeat spell (e.g. 467718 Bleak Arrows, the only auto-repeat spell in current-player scope) is interrupted by any generic/channeled cast (SetCurrentCastSpell) and by IsNonMeleeSpellCast(isAutoshoot=false) in _UpdateAutoRepeatSpell.",
     "coord": "Unit.cpp:3028-3033, 3043, 3104-3106, 3119-3122, 3133", "class": "trinity-consumer",
     "reopen": "Retail semantics of a replacement auto-shot are not encoded in data; Trinity behaviour is a consumer fact, not Retail truth"},
    {"id": "S24", "rule": "Ranged haste: m_modAttackSpeedPct[RANGED_ATTACK] is moved by CR_HASTE_RANGED, auras 342/192/193/140 (S07); MOD_MELEE_HASTE_3 (319) does not touch the ranged timer.",
     "coord": "SpellAuraEffects.cpp:4575-4593, 4595-4603, Player.cpp:5335-5338", "class": "trinity-consumer"},
    {"id": "S25", "rule": "Parry haste (victim side): when a white swing is parried and the victim is a player or a creature without CREATURE_FLAG_EXTRA_NO_PARRY_HASTEN, the victim's next swing is hastened: with an off hand and OFF timer < BASE timer the OFF timer is reduced, else the BASE timer: if timer in (20%, 60%] of base -> 20%; if > 60% -> timer - 40% (float arithmetic on the base attack time, uint32 truncation).",
     "coord": "Unit.cpp:1536-1567", "class": "trinity-consumer"},
    {"id": "S26", "rule": "Rage from swings: uint32(GetBaseAttackTime(att) / 1000.f * 1.75f), halved (integer) for OFF; not awarded on MELEE_HIT_MISS (dodge/parry still pay).",
     "coord": "Unit.cpp:2250-2258, 2331-2338", "class": "trinity-consumer", "note": "resource side effect only; recorded for completeness"},
    {"id": "S28", "rule": "Swing suppression after the ready check: DoMeleeAttackIfReady resets the timer after AttackerStateUpdate regardless of whether AttackerStateUpdate returned early (UNIT_FLAG_PACIFIED, UNIT_STATE_CANNOT_AUTOATTACK unless extra, SPELL_AURA_DISABLE_ATTACKING_EXCEPT_ABILITIES, SPELL_AURA_DISABLE_AUTOATTACK (371), dead victim, no LOS): a suppressed swing still consumes its period.",
     "coord": "Unit.cpp:2221-2222, 2241-2242, 2264-2280", "class": "trinity-consumer"},
    {"id": "S29", "rule": "Casting blocks the swing but not the timer: with UNIT_STATE_CASTING (a generic spell with cast time > 0, or a channel) and no SPELL_ATTR5_ALLOW_ACTIONS_DURING_CHANNEL channel, DoMeleeAttackIfReady returns before the ready check; the timers keep decrementing (unless S18) and saturate at 0, so the swing fires on the first update after the cast ends.",
     "coord": "Unit.cpp:2187-2192, 3113-3114, 3124, 456-469", "class": "trinity-consumer"},
    {"id": "S30", "rule": "Auto-repeat interruption: any generic spell with cast time, any channel, or IsNonMeleeSpellCast(false,false,true) while updating interrupts CURRENT_AUTOREPEAT_SPELL unless its id is 75; CheckCast failure also interrupts non-75 auto-repeat spells.",
     "coord": "Unit.cpp:3028-3033, 3041-3048, 3102-3106, 3119-3122", "class": "trinity-consumer"},
    {"id": "S27", "rule": "Tick: Unit::Update(p_time) receives the map update diff; worldserver sleeps to MinWorldUpdateTime (default 1 ms) and maps update at MapUpdateInterval (default 10 ms); Trinity has no fixed combat tick. Sessions (packets, casts) are processed before map updates in World::Update.",
     "coord": "Main.cpp:535-569, World.cpp:721, World.cpp:2268 (UpdateSessions) vs :2312 (sMapMgr->Update)", "class": "trinity-consumer"},
]

NUMERIC_RULES: list[dict[str, Any]] = [
    {"value": "m_attackTimer", "source_type": "uint32", "intermediate_type": "uint32", "aggregation_precision": "exact ms", "rounding": "saturating decrement (p_time >= timer -> 0)", "integer_conversion": "Unit.cpp:463-468", "units": "ms"},
    {"value": "m_modAttackSpeedPct", "source_type": "float", "intermediate_type": "float", "aggregation_precision": "binary32 multiplicative chain, order of aura application matters", "rounding": "none", "integer_conversion": "none", "units": "multiplier"},
    {"value": "reset timer", "source_type": "uint32 base * float pct", "intermediate_type": "float", "aggregation_precision": "binary32", "rounding": "uint32() truncation", "integer_conversion": "Unit.cpp:667", "units": "ms"},
    {"value": "haste re-time", "source_type": "uint32 timer, uint32 base, float pct", "intermediate_type": "float", "aggregation_precision": "binary32: float(timer) / (base*pct); base*pct*remaining", "rounding": "uint32() truncation", "integer_conversion": "Unit.cpp:11006", "units": "ms"},
    {"value": "off-hand offset (creatures)", "source_type": "uint32", "intermediate_type": "float (CalculatePct)", "aggregation_precision": "binary32", "rounding": "uint32() truncation twice (CalculatePct T() then uint32())", "integer_conversion": "Unit.cpp:5946", "units": "ms"},
    {"value": "parry haste", "source_type": "uint32 timers", "intermediate_type": "float", "aggregation_precision": "binary32", "rounding": "uint32() truncation", "integer_conversion": "Unit.cpp:1548-1564", "units": "ms"},
]


# ---------------------------------------------------------------------------
# pure arithmetic (binary32 emulation of the cited lines)
# ---------------------------------------------------------------------------

def reset_timer(base_ms: int, mod_pct: float) -> int:
    """Unit.cpp:667 -- ``uint32(GetBaseAttackTime(type) * m_modAttackSpeedPct[type])``."""
    return int32_trunc(f32_mul(float(base_ms), mod_pct))


def apply_percent_mod_float_var(var: float, val: float, apply: bool) -> float:
    """Unit.cpp:10978-10981 -- ``var *= (apply ? (100.0f + val) / 100.0f : 100.0f / (100.0f + val))``."""
    if apply:
        factor = f32_div(f32_add(100.0, val), 100.0)
    else:
        factor = f32_div(100.0, f32_add(100.0, val))
    return f32_mul(var, factor)


def apply_attack_time_percent_mod(timer: int, base_ms: int, mod_pct: float, val: float, apply: bool) -> tuple[int, float, int]:
    """Unit.cpp:10983-11007.  Returns (new timer, new modPct, new AttackRoundBaseTime field)."""
    denom = f32_mul(float(base_ms), mod_pct)
    remaining = f32_div(float(timer), denom)          # :10985 (division by zero when base*pct == 0: C++ inf/nan, not modelled)
    if val > 0.0:                                     # :10986-10994
        mod_pct = apply_percent_mod_float_var(mod_pct, val, not apply)
    else:                                             # :10995-11003
        mod_pct = apply_percent_mod_float_var(mod_pct, -val, apply)
    field_value = int32_trunc(f32_mul(float(base_ms), mod_pct))   # :11005 -> UpdateAttackTimeField (:10968/:10971)
    new_timer = int32_trunc(f32_mul(f32_mul(float(base_ms), mod_pct), remaining))  # :11006
    return new_timer, mod_pct, field_value


def decrement(timer: int, p_time: int) -> int:
    """Unit.cpp:463-468 -- saturating uint32 decrement."""
    return 0 if p_time >= timer else timer - p_time


def calculate_pct_u32(base: int, pct: int) -> int:
    """Util.h:72-75 ``T(base * static_cast<float>(pct) / 100.0f)`` for T = uint32."""
    return int32_trunc(f32_div(f32_mul(float(base), float(pct)), 100.0))


def creature_offhand_offset(off_timer: int, base_timer: int, base_attack_time: int) -> int:
    """Unit.cpp:5946 -- ``max(OFF, BASE + uint32(CalculatePct(GetBaseAttackTime(BASE), 50)))`` (non-players only)."""
    return max(off_timer, base_timer + calculate_pct_u32(base_attack_time, 50))


def parry_haste(off_timer: int, base_timer: int, base_off: int, base_mh: int, have_offhand: bool) -> tuple[int, int]:
    """Unit.cpp:1536-1567 (victim-side).  Returns (new OFF timer, new BASE timer)."""
    offtime = f32(float(off_timer))
    basetime = f32(float(base_timer))
    if have_offhand and offtime < basetime:
        p20 = f32_mul(float(base_off), 0.20)
        p60 = f32_mul(3.0, p20)
        if offtime > p20 and offtime <= p60:
            return int32_trunc(p20), base_timer
        if offtime > p60:
            return int32_trunc(f32(offtime - f32_mul(2.0, p20))), base_timer
        return off_timer, base_timer
    p20 = f32_mul(float(base_mh), 0.20)
    p60 = f32_mul(3.0, p20)
    if basetime > p20 and basetime <= p60:
        return off_timer, int32_trunc(p20)
    if basetime > p60:
        return off_timer, int32_trunc(f32(basetime - f32_mul(2.0, p20)))
    return off_timer, base_timer


def rage_gain(base_attack_time: int, att: str) -> int:
    """Unit.cpp:2253-2260 -- ``uint32(base / 1000.f * 1.75f)``, ``/= 2`` for OFF."""
    if att not in ("base", "off"):
        return 0
    rage = int32_trunc(f32_mul(f32_div(float(base_attack_time), 1000.0), 1.75))
    return rage // 2 if att == "off" else rage


# ---------------------------------------------------------------------------
# timeline simulation
# ---------------------------------------------------------------------------

@dataclass
class SwingState:
    base: dict[str, int]                      # m_baseAttackSpeed per hand (ms)
    mod: dict[str, float] = field(default_factory=lambda: {a: 1.0 for a in ATT})
    timer: dict[str, int] = field(default_factory=lambda: {a: 0 for a in ATT})
    have_offhand: bool = False
    is_player: bool = True
    melee_attacking: bool = True
    auto_repeat: bool = False                 # CURRENT_AUTOREPEAT_SPELL set (hunter auto shot)
    pending_shot: bool = False                # a prepared auto-repeat Spell waiting for its SpellEvent
    paused: bool = False                      # current spell has DELAY_COMBAT_TIMER_DURING_CAST
    casting: bool = False                     # UNIT_STATE_CASTING (blocks DoMeleeAttackIfReady, S29)
    queued_extra_attacks: int = 0
    last_extra_attack_spell: bool = False
    in_feral_form: bool = False


def simulate(mh_speed: int, oh_speed: int | None, haste_pct: float, duration: int, tick: int = 100,
             events: list[dict[str, Any]] | None = None, ranged_speed: int | None = None,
             is_player: bool = True, attack_at: int = 0, melee: bool = True) -> dict[str, Any]:
    """Deterministic MH/OH/ranged timeline for a fixed host ``tick``.

    ``haste_pct`` is applied once at t=0 as a single ApplyAttackTimePercentMod
    (positive = faster).  ``events``: list of ``{"t": ms, "type": ...}`` with
    types ``haste`` (``val``, ``apply``, optional ``hands``), ``reset`` (ResetCombatTimers),
    ``pause`` / ``unpause`` (a cast with DELAY_COMBAT_TIMER: timers frozen and
    swing blocked), ``cast_start`` / ``cast_end`` (UNIT_STATE_CASTING only: swing
    blocked, timers run), ``extra_attack`` (``count``; an ADD_EXTRA_ATTACKS spell finished),
    ``parry_haste`` (this unit's swing was parried).  Events are applied at the
    start of the update in which they fall (WorldSession::Update precedes
    Map::Update, rule S27).
    """
    if tick <= 0:
        raise ValueError("tick must be positive")
    st = SwingState(base={"base": mh_speed, "off": oh_speed or BASE_ATTACK_TIME, "ranged": ranged_speed or BASE_ATTACK_TIME},
                    have_offhand=oh_speed is not None, is_player=is_player, auto_repeat=ranged_speed is not None,
                    melee_attacking=melee)
    if haste_pct:
        for a in ATT:
            st.timer[a], st.mod[a], _ = apply_attack_time_percent_mod(st.timer[a], st.base[a], st.mod[a], haste_pct, True)
    if not is_player and st.have_offhand:
        st.timer["off"] = creature_offhand_offset(st.timer["off"], st.timer["base"], st.base["base"])   # S08
    events = sorted(events or [], key=lambda e: int(e["t"]))
    timeline: list[dict[str, Any]] = []
    ei = 0
    t = attack_at
    while t <= duration:
        # --- events that fall into this update (before Unit::Update)
        while ei < len(events) and int(events[ei]["t"]) <= t:
            e = events[ei]
            ei += 1
            kind = e["type"]
            if kind == "haste":
                for a in e.get("hands", ATT):
                    st.timer[a], st.mod[a], _ = apply_attack_time_percent_mod(st.timer[a], st.base[a], st.mod[a], float(e["val"]), bool(e.get("apply", True)))
                timeline.append({"t": t, "event": "haste", "val": e["val"], "apply": e.get("apply", True), "timers": dict(st.timer), "mod": {a: st.mod[a] for a in ATT}})
            elif kind == "reset":
                st.timer["base"] = reset_timer(st.base["base"], st.mod["base"])
                if st.have_offhand:
                    st.timer["off"] = reset_timer(st.base["off"], st.mod["off"])
                st.timer["ranged"] = reset_timer(st.base["ranged"], st.mod["ranged"])
                timeline.append({"t": t, "event": "reset_combat_timers", "timers": dict(st.timer)})
            elif kind == "pause":        # cast in progress whose spell has SPELL_ATTR6_DELAY_COMBAT_TIMER_DURING_CAST
                st.paused = True
                st.casting = True
                timeline.append({"t": t, "event": "pause"})
            elif kind == "unpause":
                st.paused = False
                st.casting = False
                timeline.append({"t": t, "event": "unpause"})
            elif kind == "cast_start":   # UNIT_STATE_CASTING without the pause attribute (S29)
                st.casting = True
                timeline.append({"t": t, "event": "cast_start"})
            elif kind == "cast_end":
                st.casting = False
                timeline.append({"t": t, "event": "cast_end"})
            elif kind == "extra_attack":
                st.queued_extra_attacks += int(e.get("count", 1))
                st.last_extra_attack_spell = True
                timeline.append({"t": t, "event": "extra_attacks_queued", "count": e.get("count", 1)})
            elif kind == "parry_haste":
                st.timer["off"], st.timer["base"] = parry_haste(st.timer["off"], st.timer["base"], st.base["off"], st.base["base"], st.have_offhand)
                timeline.append({"t": t, "event": "parry_haste", "timers": dict(st.timer)})
            else:
                raise ValueError(f"unknown event type {kind!r}")
        # --- Unit::Update
        # (a) WorldObject::Update: pending auto-repeat SpellEvent casts now (S22)
        if st.auto_repeat and st.pending_shot:
            st.pending_shot = False
            timeline.append({"t": t, "swing": "ranged", "timer_before": st.timer["ranged"]})
            st.timer["ranged"] = reset_timer(st.base["ranged"], st.mod["ranged"])
        # (b) _UpdateSpells -> _UpdateAutoRepeatSpell
        if st.auto_repeat and st.timer["ranged"] == 0 and not st.pending_shot:
            st.pending_shot = True
            timeline.append({"t": t, "event": "auto_shot_prepared"})
        # (c) extra attacks drained before the decrement (S21)
        if st.last_extra_attack_spell and st.queued_extra_attacks:
            for _ in range(st.queued_extra_attacks):
                timeline.append({"t": t, "swing": "base", "extra": True, "timer_before": st.timer["base"]})
            st.queued_extra_attacks = 0
            st.last_extra_attack_spell = False
        # (d) decrement
        if not st.paused:
            for a in ATT:
                st.timer[a] = decrement(st.timer[a], tick)
        # --- DoMeleeAttackIfReady (S11-S13)
        if st.melee_attacking and not st.casting:
            if st.timer["base"] == 0:
                if st.have_offhand and st.timer["off"] < ATTACK_DISPLAY_DELAY:
                    st.timer["off"] = ATTACK_DISPLAY_DELAY
                timeline.append({"t": t, "swing": "base", "extra": False})
                st.last_extra_attack_spell = False
                st.timer["base"] = reset_timer(st.base["base"], st.mod["base"])
            if not st.in_feral_form and st.have_offhand and st.timer["off"] == 0:
                if st.timer["base"] < ATTACK_DISPLAY_DELAY:
                    st.timer["base"] = ATTACK_DISPLAY_DELAY
                timeline.append({"t": t, "swing": "off", "extra": False})
                st.last_extra_attack_spell = False           # AttackerStateUpdate(extra=false), Unit.cpp:2288-2289
                st.timer["off"] = reset_timer(st.base["off"], st.mod["off"])
        t += tick
    swings = [x for x in timeline if "swing" in x]
    return {"tick_ms": tick, "host_note": "tick is a host parameter (rule S27); Trinity uses the measured update diff",
            "inputs": {"mh_speed": mh_speed, "oh_speed": oh_speed, "ranged_speed": ranged_speed, "haste_pct": haste_pct, "duration": duration, "is_player": is_player, "melee": melee},
            "mod_pct": {a: st.mod[a] for a in ATT}, "period_ms": {a: reset_timer(st.base[a], st.mod[a]) for a in ATT},
            "timeline": timeline, "swing_count": {h: sum(1 for s in swings if s["swing"] == h) for h in ATT}}


# ---------------------------------------------------------------------------
# corpus
# ---------------------------------------------------------------------------

def build_corpus(generator: str) -> dict[str, Any]:
    from .attack_table import _git_head
    examples = {
        "dual_wield_2600_2600_no_haste_tick100": simulate(2600, 2600, 0.0, 6000, 100),
        "dual_wield_2600_2600_haste_20_tick100": simulate(2600, 2600, 20.0, 6000, 100),
        "dual_wield_2600_2600_haste_20_tick1": simulate(2600, 2600, 20.0, 6000, 1),
        "two_hand_3600_haste_10_tick100": simulate(3600, None, 10.0, 8000, 100),
        "ranged_auto_3000_tick100": simulate(2000, None, 0.0, 7000, 100, ranged_speed=3000, melee=False),
        "haste_change_midswing": simulate(2600, None, 0.0, 6000, 100, events=[{"t": 1000, "type": "haste", "val": 30.0}]),
        "cast_reset_midswing": simulate(2600, 2600, 0.0, 6000, 100, events=[{"t": 1500, "type": "reset"}]),
        "pause_window": simulate(2600, None, 0.0, 6000, 100, events=[{"t": 500, "type": "pause"}, {"t": 1500, "type": "unpause"}]),
        "cast_window_2000ms_over_ready": simulate(2600, None, 0.0, 6000, 100, events=[{"t": 2000, "type": "cast_start"}, {"t": 4000, "type": "cast_end"}]),
        "ranged_auto_3000_tick1": simulate(2000, None, 0.0, 7000, 1, ranged_speed=3000, melee=False),
        "extra_attack_legacy": simulate(2600, None, 0.0, 4000, 100, events=[{"t": 1000, "type": "extra_attack", "count": 2}]),
        "creature_dual_wield_2000": simulate(2000, 2000, 0.0, 5000, 100, is_player=False),
    }
    return {
        "provenance": {"snapshot_build": SNAPSHOT_BUILD, "trinitycore_commit": TRINITY_COMMIT, "generator": generator, "wowlab_data_commit": _git_head()},
        "constants": {"ATTACK_DISPLAY_DELAY_ms": ATTACK_DISPLAY_DELAY, "BASE_ATTACK_TIME_ms": BASE_ATTACK_TIME, "retry_timer_ms": 100,
                      "SPELL_ATTR6_DELAY_COMBAT_TIMER_DURING_CAST": "0x00080000 (SharedDefines.h:678)",
                      "SPELL_ATTR2_DO_NOT_RESET_COMBAT_TIMERS": "0x00020000 (SharedDefines.h:528)",
                      "SPELL_ATTR7_RESET_SWING_TIMER_AT_SPELL_START": "0x00008000 (SharedDefines.h:711)",
                      "SPELL_ATTR6_DOESNT_RESET_SWING_TIMER_IF_INSTANT": "0x02000000 (SharedDefines.h:684)",
                      "SpellInterruptFlags::Combat": "0x00000008 (SpellDefines.h:65)",
                      "SPELL_ATTR2_AUTO_REPEAT": "0x00000020 (SharedDefines.h:516)",
                      "SPELL_ATTR0_ON_NEXT_SWING": "0x00000400 (SharedDefines.h:447)", "SPELL_ATTR0_ON_NEXT_SWING_NO_DAMAGE": "0x00000004 (SharedDefines.h:439)"},
        "rules": RULES,
        "numeric_rules": NUMERIC_RULES,
        "examples": examples,
    }


def _cmd_swing(args: argparse.Namespace) -> int:
    events = json.loads(args.events) if args.events else None
    out = simulate(args.mh_speed, args.oh_speed, args.haste_pct, args.duration, args.tick, events, args.ranged_speed, not args.creature,
                   melee=not args.no_melee)
    if args.brief:
        for x in out["timeline"]:
            if "swing" in x:
                print(f"{x['t']:>7} {x['swing']}{' extra' if x.get('extra') else ''}")
            else:
                print(f"{x['t']:>7} [{x['event']}] {json.dumps({k: v for k, v in x.items() if k not in ('t', 'event')})}")
        print("period_ms", out["period_ms"], "swings", out["swing_count"])
    else:
        print(json.dumps(out, indent=1))
    return 0


def _cmd_corpus(args: argparse.Namespace) -> int:
    corpus = build_corpus("python3 weapon_combat.py swing-corpus")
    CORPORA.mkdir(parents=True, exist_ok=True)
    path = CORPORA / "swing.json"
    path.write_text(json.dumps(corpus, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {path} ({len(corpus['rules'])} rules, {len(corpus['examples'])} examples)")
    return 0


def register(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser("swing", help="deterministic MH/OH/ranged swing timeline (Trinity scheduling rules, fixed host tick)")
    p.add_argument("--mh-speed", type=int, required=True, help="main-hand base attack time, ms")
    p.add_argument("--oh-speed", type=int, default=None, help="off-hand base attack time, ms (omit for no off hand)")
    p.add_argument("--ranged-speed", type=int, default=None, help="ranged weapon base attack time, ms (enables auto-repeat)")
    p.add_argument("--haste-pct", type=float, default=0.0, help="haste applied at t=0 via ApplyAttackTimePercentMod (positive = faster)")
    p.add_argument("--duration", type=int, required=True, help="ms to simulate")
    p.add_argument("--tick", type=int, default=100, help="host update tick, ms (Trinity: variable diff)")
    p.add_argument("--events", type=str, default=None, help='JSON list, e.g. [{"t":1000,"type":"haste","val":30}]')
    p.add_argument("--creature", action="store_true", help="non-player attacker (off-hand 50%% offset at Attack())")
    p.add_argument("--no-melee", action="store_true", help="no UNIT_STATE_MELEE_ATTACKING (ranged-only timeline)")
    p.add_argument("--brief", action="store_true")
    p.set_defaults(func=_cmd_swing)
    c = subparsers.add_parser("swing-corpus", help="write docs/research/weapon-combat-corpora/swing.json")
    c.set_defaults(func=_cmd_corpus)
