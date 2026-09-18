"""Snapshot vs dynamic periodic inputs (Track D).

For every periodic family (aura operation) and every input that can change a tick,
:data:`MATRIX` states *when* the pinned consumer reads the input (``INPUT_TIMING``),
where that is decided (``POLICY_SOURCE``) and the coordinates.  Two consumers:

* **Trinity** (primary oracle): ``AuraEffect::CalculateAmount`` (SpellAuraEffects.cpp:777)
  runs at construction (ctor 736-745), on stack change / same-caster reapply
  (``Aura::SetStackAmount`` SpellAuras.cpp:1056 -> ``ChangeAmount(CalculateAmount)``) and on
  explicit ``RecalculateAmount`` (spellmod apply 1187-1248, mastery update StatSystem.cpp:558-561,
  aura load SpellAuras.cpp:1286).  Everything the tick handlers compute (``SpellDamageBonusDone`` /
  ``SpellHealingBonusDone`` / ``*BonusTaken`` / crit roll) is read live at each tick.
* **simc** (secondary consumer): ``action_t::init`` (engine/action/action.cpp:2721-2812):
  ``update_flags |= snapshot_flags`` ("WOD: Dot Snapshoting is gone") except persistent
  multiplier, rolling TA, channel haste, and -- for effects flagged ``EX_COMPUTE_ON_CAST``
  (effect attribute 0x8000, *NYI* in Trinity DBCEnums.h:2418) -- player-scoped damage inputs.

:func:`tick_amounts` replays a discriminating timeline (e.g. spell power gained after the DoT
was applied) under the Trinity model and the competing *full application snapshot* model.
"""

from __future__ import annotations

from typing import Any

from . import FailClosed, INPUT_TIMING, POLICY_SOURCE
from .periodic import f32, trunc_i32

T = "trinity-consumer"
S = "simc-consumer"

# family -> aura types (Trinity names without SPELL_AURA_)
FAMILIES = {
    "periodic-damage": ("PERIODIC_DAMAGE",),
    "periodic-leech": ("PERIODIC_LEECH",),
    "periodic-damage-percent": ("PERIODIC_DAMAGE_PERCENT",),
    "periodic-heal": ("PERIODIC_HEAL",),
    "obs-mod-health": ("OBS_MOD_HEALTH",),
    "periodic-energize": ("PERIODIC_ENERGIZE",),
    "obs-mod-power": ("OBS_MOD_POWER",),
    "periodic-trigger-spell": ("PERIODIC_TRIGGER_SPELL",),
    "periodic-trigger-spell-with-value": ("PERIODIC_TRIGGER_SPELL_WITH_VALUE",),
    "periodic-dummy": ("PERIODIC_DUMMY",),
    "health-funnel": ("PERIODIC_HEALTH_FUNNEL",),
    "power-drain": ("PERIODIC_MANA_LEECH", "POWER_BURN"),
    "weapon-percent-damage": ("PERIODIC_WEAPON_PERCENT_DAMAGE",),
}


def _row(family, input_, timing, policy, evidence, coords, note, simc=None):
    if timing not in INPUT_TIMING:
        raise ValueError(timing)
    if policy not in POLICY_SOURCE:
        raise ValueError(policy)
    return {"family": family, "input": input_, "trinity_timing": timing, "policy_source": policy,
            "evidence": evidence, "coords": coords, "note": note, "simc": simc}


DMG = ("periodic-damage", "periodic-leech")
HEAL = ("periodic-heal",)


def _matrix() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for fam in DMG + HEAL:
        done = "SpellHealingBonusDone" if fam in HEAL else "SpellDamageBonusDone"
        done_line = "Unit.cpp:7327" if fam in HEAL else "Unit.cpp:6810"
        tick_line = {"periodic-damage": "SpellAuraEffects.cpp:5663-5665", "periodic-leech": "SpellAuraEffects.cpp:5785-5787",
                     "periodic-heal": "SpellAuraEffects.cpp:5913-5918"}[fam]
        rows += [
            _row(fam, "base points (EffectBasePoints, variance roll, level scaling, trait points, combo points)",
                 "application-snapshot", "trinity-default", T,
                 ["SpellAuraEffects.cpp:736-745", "SpellAuraEffects.cpp:780", "SpellInfo.cpp:521-660"],
                 "CalcValue inside CalculateAmount; re-run (new roll, new combo points) on same-caster reapply/stack "
                 "via SetStackAmount (SpellAuras.cpp:1056-1074) -> also recalculated-on-refresh",
                 {"timing": "application-snapshot", "coords": "engine/action/action.cpp (base_ta per tick uses action state)"}),
            _row(fam, "SpellMod Points / PointsIndexN (caster)", "application-snapshot", "trinity-default", T,
                 ["SpellInfo.cpp:603-604 (ApplyEffectModifiers)", "SpellAuraEffects.cpp:1223-1247"],
                 "applied in CalcValue; a later Points spellmod recalculates only passive/permanent own auras "
                 "(or IsUpdatingTemporaryAuraValuesBySpellMod) -> explicit-recalculation for those", None),
            _row(fam, "stack count", "recalculated-on-stack-change", "trinity-default", T,
                 ["SpellAuraEffects.cpp:844-845", tick_line],
                 "amount *= stack at CalculateAmount AND stack passed live to BonusDone at tick (SP/AP part x stack) "
                 "unless SuppressPointsStacking (0x40)", {"timing": "dynamic (dot stack)"}),
            _row(fam, "spell power / attack power coefficient", "dynamic-each-tick", "trinity-default", T,
                 [done_line, tick_line],
                 f"{done} with DOT at every tick, caster pointer from UpdateOwner (SpellAuras.cpp:820)",
                 {"timing": "dynamic-each-tick (update_flags |= STATE_SP/STATE_AP)", "coords": "engine/action/action.cpp:2775-2787"}),
            _row(fam, "BonusCoefficient spellmod", "dynamic-each-tick", "trinity-default", T, [done_line], "", None),
            _row(fam, "versatility / % damage-healing done auras", "dynamic-each-tick", "trinity-default", T,
                 ["Unit.cpp:6901-6990" if fam in DMG else "Unit.cpp:7439"],
                 "SpellDamagePctDone / SpellHealingPctDone at tick; ATTR3_IGNORE_CASTER_MODIFIERS / ATTR6 skip",
                 {"timing": "dynamic-each-tick unless EX_COMPUTE_ON_CAST (player-scoped snapshot) or persistent multiplier",
                  "coords": "engine/action/action.cpp:2733-2735,2787-2812"}),
            _row(fam, "SpellMod PeriodicHealingAndDamage", "dynamic-each-tick", "trinity-default", T,
                 ["Unit.cpp:6896" if fam in DMG else "Unit.cpp:7434"], "", None),
            _row(fam, "target taken modifiers / absorb / armor / resilience", "target-live-lookup", "trinity-default", T,
                 [tick_line, "SpellAuraEffects.cpp:5694-5726"], "",
                 {"timing": "dynamic (STATE_TGT_* in update_flags)", "coords": "engine/action/action.cpp:507"}),
            _row(fam, "crit chance", "caster-live-lookup", "trinity-default", T,
                 ["SpellAuraEffects.cpp:1278-1281", "SpellAuraEffects.cpp:6132-6143", "SpellAuras.cpp:524-533"],
                 "rolled per tick from the SPELL-MOD OWNER's live SpellCritChanceDone (GetSpellModOwner: a pet/guardian "
                 "caster uses its owner's chance), then target SpellCritChanceTaken; 0 unless ATTR8_PERIODIC_CAN_CRIT "
                 "(raw 265) and not ATTR2_CANT_CRIT; 0 when the caster is absent or has no spell-mod owner (R2-09)",
                 {"timing": "dynamic (STATE_CRIT in update_flags; tick_may_crit)"}),
            _row(fam, "crit chance of a pet/guardian caster", "caster-live-lookup", "trinity-default", T,
                 ["SpellAuraEffects.cpp:6137-6141"],
                 "modOwner = caster->GetSpellModOwner() -> the owning player's crit chance, not the pet's (R2-09)", None),
            _row(fam, "mastery (ATTR8_MASTERY_AFFECTS_POINTS), aura owned by ANOTHER unit (every DoT/HoT on others)",
                 "application-snapshot", "trinity-default", T,
                 ["SpellInfo.cpp:600-601", "StatSystem.cpp:557-561"],
                 "points += Mastery*BonusCoefficient only in CalculateAmount (create / reapply / stack change); "
                 "Player::UpdateMastery walks GetOwnedAuras() of the caster only -> never re-read (R2-09)", None),
            _row(fam, "mastery (ATTR8_MASTERY_AFFECTS_POINTS), aura the caster owns on itself", "explicit-recalculation",
                 "trinity-default", T, ["StatSystem.cpp:557-561"],
                 "RecalculateAmount on every mastery update for owned auras with caster==owner and nonzero BonusCoefficient",
                 None),
            _row(fam, "caster absent (despawned / other map) at tick", "caster-live-lookup", "trinity-default", T,
                 ["SpellAuras.cpp:820", "Unit.cpp:6810-6813"],
                 "caster==nullptr -> no done bonus at all (BonusDone skipped), crit 0; dead caster still present -> full bonus",
                 None),
            _row(fam, "effect attribute ComputePointsOnlyAtCastTime (0x8000)", "unknown", "db2", ["trinity-consumer", "simc-consumer"],
                 ["DBCEnums.h:2418", "engine/action/action.cpp:2802-2812"],
                 "NYI in Trinity (ignored: all done inputs stay dynamic); simc snapshots player-scoped inputs -> known divergence",
                 {"timing": "application-snapshot (player-scoped)"}),
        ]
    rows += [
        _row("periodic-damage", "rolling periodic remainder (raw 334)", "recalculated-on-refresh", "trinity-default", T,
             ["SpellAuraEffects.cpp:829-841"],
             "new amount += old EstimatedAmount (done-bonus at apply) * remaining/total ticks; Track B owns the formula",
             {"timing": "recalculated-on-refresh (STATE_ROLLING_TA not in update_flags)", "coords": "engine/action/action.cpp:2721-2725,2794"}),
        _row("periodic-damage-percent", "target max health", "target-live-lookup", "trinity-default", T,
             ["SpellAuraEffects.cpp:5696-5700"], "ceil(pct of live max health) then BonusTaken; no done bonus", None),
        _row("obs-mod-health", "target max health", "target-live-lookup", "trinity-default", T,
             ["SpellAuraEffects.cpp:5913-5914"], "CountPctFromMaxHealth(amount) at tick; no done bonus", None),
        _row("periodic-energize", "energize amount", "application-snapshot", "trinity-default", T,
             ["SpellAuraEffects.cpp:6053-6083"], "GetAmount() only; no live caster input; recalculated on reapply/stack", None),
        _row("obs-mod-power", "target max power", "target-live-lookup", "trinity-default", T,
             ["SpellAuraEffects.cpp:6010-6051"], "CalculatePct(live max power, amount)", None),
        _row("periodic-trigger-spell", "triggered spell inputs", "dynamic-each-tick", "trinity-default", T,
             ["SpellAuraEffects.cpp:5583-5605"],
             "each tick casts the trigger spell (TRIGGERED_FULL_MASK minus cost-ignore bits): its own values are computed "
             "at that cast from the live trigger caster (caster or target per NeedsToBeTriggeredByCaster); absent caster -> no cast when caster-triggered",
             None),
        _row("periodic-trigger-spell-with-value", "passed base points", "application-snapshot", "trinity-default", T,
             ["SpellAuraEffects.cpp:5607-5630"], "GetAmount() of the aura at tick passed as BASE_POINT0..n", None),
        _row("periodic-dummy", "everything", "script-controlled", "script", T, ["SpellAuraEffects.cpp:1321-1323"],
             "no default tick action; OnEffectPeriodic scripts decide (Track H)", None),
        _row("health-funnel", "amount", "application-snapshot", "trinity-default", T, ["SpellAuraEffects.cpp:5863-5891"],
             "GetAmount capped by caster live health", None),
        _row("power-drain", "drained amount", "application-snapshot", "trinity-default", T,
             ["SpellAuraEffects.cpp:5951-6008", "SpellAuraEffects.cpp:6084-6130"],
             "GetAmount() with target live power checks; no done bonus", None),
        _row("weapon-percent-damage", "everything", "unknown", "trinity-default", T,
             ["SpellAuraEffects.cpp:966-981", "SpellAuraEffects.cpp:1334"],
             "never ticks in Trinity (not in CalculatePeriodic list) -> AL-D-D-01", None),
    ]
    for fam in ("all-periodic",):
        rows += [
            _row(fam, "haste -> period", "application-snapshot", "combined", T,
                 ["SpellAuraEffects.cpp:996-1013", "SpellAuras.cpp:988-991"],
                 "hasted period computed in CalculatePeriodic at create and at RefreshTimers (even pandemic: period is "
                 "re-hasted while the timer is kept); the engine never recalculates on a haste change -> also "
                 "recalculated-on-refresh. SCRIPT EXCEPTIONS (R2-08): Arcane Tempest spell_item.cpp:4733-4734 "
                 "(ModStackAmount(1, NONE, false) then CalculatePeriodic with live haste at each stack), "
                 "zone_zuldrak.cpp:720, stratholme.cpp:382, shadowfang_keep.cpp:39; DoEffectCalcPeriodic hooks re-run "
                 "on every CalculatePeriodic",
                 {"timing": "dynamic for hasted_ticks dots (next tick rescheduled); snapshot for channels",
                  "coords": "engine/action/action.cpp:2784,2797-2800; engine/action/dot.cpp:1000-1010"}),
            _row(fam, "SpellMod Period (caster)", "application-snapshot", "trinity-default", T,
                 ["SpellAuraEffects.cpp:996-999"],
                 "ApplySpellMod(SpellModOp::Period) inside CalculatePeriodic -> captured at create, re-read at RefreshTimers "
                 "(also recalculated-on-refresh); no recalculation when the modifier aura changes (R2-08)", None),
            _row(fam, "SpellMod ChangeCastTime (channels only)", "application-snapshot", "trinity-default", T,
                 ["Object.cpp:1829-1830", "SpellAuraEffects.cpp:1005-1006"],
                 "channel period goes through ModSpellDurationTime, which applies ChangeCastTime then cast speed "
                 "(only when raw 173/278 is set); captured at create/refresh (R2-08)", None),
            _row(fam, "SpellMod Duration (caster)", "application-snapshot", "trinity-default", T,
                 ["SpellAuras.cpp:932", "Spell.cpp:3215", "SpellAuras.cpp:978"],
                 "CalcMaxDuration at hit (PreprocessSpellHit) and again in RefreshTimers -> also recalculated-on-refresh; "
                 "sets MaxDuration and therefore the tick budget (Track B owns duration)", None),
            _row(fam, "target duration mods / diminishing returns", "application-snapshot", "trinity-default", T,
                 ["Spell.cpp:3218", "Spell.cpp:3262"],
                 "ApplyDiminishingToDuration and ModSpellDuration on the Spell-hit path only; never re-read; a non-hit "
                 "refresh (AddAura, effect 289, scripts) uses CalcMaxDuration without them (Track B)", None),
            _row(fam, "haste -> duration (raw 273)", "application-snapshot", "trinity-default", T,
                 ["Spell.cpp:3268-3282"], "duration = max(orig/period,1)*period with the already-hasted period (Track B)", None),
            _row(fam, "periodic timer phase", "recalculated-on-refresh", "combined", T,
                 ["Spell.cpp:3240", "SpellAuras.cpp:976-992", "SpellAuraEffects.cpp:949-959"],
                 "Spell-hit path: restart on untriggered reapply of StackAmount<2 non-pandemic auras, preserved otherwise; "
                 "non-hit paths (AddAura, effect 289, linked, steal, script ModStackAmount default) restart unless raw 436 "
                 "(R1-05, R2-04)",
                 {"timing": "always preserved (dot_t::refresh never touches tick_event)", "coords": "engine/action/dot.cpp:940-969"}),
        ]
    return rows


MATRIX = _matrix()


# ---------------------------------------------------------------------------
# discriminating amount model
# ---------------------------------------------------------------------------

def base_amount(base_points: float, stack: int, suppress_points_stacking: bool = False) -> float:
    """Mirrors: SpellAuraEffects.cpp:777-866 for PERIODIC_DAMAGE without scripts / rolling:
    ``amount = CalcValue`` (caller passes the rolled value), ``*= stack``, ``std::round``."""
    amount = base_points
    if not suppress_points_stacking:
        amount *= stack
    return float(round(amount)) if amount >= 0 else -float(round(-amount))


def damage_bonus_done(pdamage: int, *, spell_power: int, coeff: float, stack: int, pct_done: float = 1.0,
                      ignore_caster_mods: bool = False) -> int:
    """Mirrors: Unit.cpp:6810-6899 (``Unit::SpellDamageBonusDone``, DOT, no AP coefficient, no scripts,
    no spellmods: ``GetSpellModOwner`` spellmods must be absent -- a non-default modifier fails closed
    in the caller).  ``pct_done`` is the ``SpellDamagePctDone`` product (binary32)."""
    if ignore_caster_mods:
        return trunc_i32(max(f32(float(pdamage)) * 1.0, 0.0))
    done_total = 0
    if spell_power:
        done_total += trunc_i32(f32(f32(f32(float(spell_power)) * f32(coeff)) * f32(float(stack))))
    tmp = f32(f32(float(pdamage + done_total)) * f32(pct_done))
    return trunc_i32(max(tmp, 0.0))


def tick_amounts(scenario: dict[str, Any]) -> dict[str, Any]:
    """Per-tick done damage of one PERIODIC_DAMAGE aura under competing models.

    ``scenario``: ``periodic.run`` scenario plus ``"amount": {"base_points", "coeff"}`` and
    ``"caster_state": [{"t": ms, "spell_power": n, "pct_done": f}]`` (step function, first entry t<=0).
    Models: ``trinity`` (base snapshot at create/reapply, done bonus live at tick) and
    ``full-snapshot`` (all caster inputs frozen at the last create/reapply).
    """
    from .periodic import run
    timeline = run(scenario)
    states = sorted(scenario["caster_state"], key=lambda s: s["t"])
    if not states or states[0]["t"] > 0:
        raise FailClosed("caster_state must start at t<=0")

    def state_at(t: int) -> dict[str, Any]:
        cur = states[0]
        for s in states:
            if s["t"] <= t:
                cur = s
        return cur

    amt = scenario["amount"]
    applied_at: int | None = None
    out = []
    for row in timeline["rows"]:
        if row["event"] in ("create", "reapply"):
            applied_at = row["t"]
        for tick in row.get("ticks", []):
            live = state_at(row["t"])
            snap = state_at(applied_at)
            stack = row["state"]["stack"]
            base = int(base_amount(amt["base_points"], stack))
            out.append({
                "t": row["t"], "tick": tick["tick"],
                "trinity": damage_bonus_done(base, spell_power=live["spell_power"], coeff=amt["coeff"], stack=stack,
                                             pct_done=live.get("pct_done", 1.0)),
                "full-snapshot": damage_bonus_done(base, spell_power=snap["spell_power"], coeff=amt["coeff"], stack=stack,
                                                   pct_done=snap.get("pct_done", 1.0)),
            })
    return {"ticks": out, "tick_times": timeline["tick_times"]}


SNAPSHOT_SPECS: tuple[dict[str, Any], ...] = (
    {"id": "S-01", "question": "spell power gained after application (1000 -> 1500 at 4500 ms)",
     "scenario": {"spell": {"base_period": 3000, "base_duration": 12000}, "update_every": 100, "until": 13000,
                  "events": [{"t": 0, "op": "create"}], "amount": {"base_points": 100, "coeff": 0.5},
                  "caster_state": [{"t": 0, "spell_power": 1000}, {"t": 4500, "spell_power": 1500}]},
     "rules_out": "full application snapshot: Trinity ticks 2..4 use SP 1500 (850 vs 600)"},
    {"id": "S-02", "question": "a % damage-done buff (1.1) that expires mid-DoT",
     "scenario": {"spell": {"base_period": 3000, "base_duration": 12000}, "update_every": 100, "until": 13000,
                  "events": [{"t": 0, "op": "create"}], "amount": {"base_points": 100, "coeff": 0.5},
                  "caster_state": [{"t": 0, "spell_power": 1000, "pct_done": 1.1}, {"t": 7000, "spell_power": 1000, "pct_done": 1.0}]},
     "rules_out": "persistent-multiplier style capture of %done at application (simc STATE_MUL_PERSISTENT applies only to "
                  "persistent multipliers, not %done auras)"},
    {"id": "S-03", "question": "stack added by reapply on a StackAmount-5 DoT",
     "scenario": {"spell": {"base_period": 3000, "base_duration": 12000, "stack_amount": 5}, "update_every": 100, "until": 20000,
                  "events": [{"t": 0, "op": "create"}, {"t": 7050, "op": "reapply"}], "amount": {"base_points": 100, "coeff": 0.5},
                  "caster_state": [{"t": 0, "spell_power": 1000}]},
     "rules_out": "stack-independent tick amount: base*stack at recalculation and SP*coeff*stack at tick"},
)


def snapshot_timelines() -> list[dict[str, Any]]:
    out = []
    for spec in SNAPSHOT_SPECS:
        out.append({**spec, "result": tick_amounts(spec["scenario"]),
                    "evidence": ["trinity-consumer", "structural-inference"],
                    "cut_points": "no spellmods, no AP coefficient, no taken/crit; SpellDamagePctDone supplied as pct_done"})
    return out
