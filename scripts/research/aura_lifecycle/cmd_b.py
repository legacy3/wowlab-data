"""Track B commands: ``duration`` and ``refresh`` (+ corpora).

    aura_lifecycle.py duration <spell>                 explain one spell's authored duration + Trinity pipeline
    aura_lifecycle.py duration --out duration.json     full corpus (census + witnesses + records)
    aura_lifecycle.py refresh <spell>                  reapplication branch + a real-data refresh timeline
    aura_lifecycle.py refresh --out refresh.json       refresh taxonomy corpus
    aura_lifecycle.py refresh --carryover --out carryover.json   pandemic / rolling carryover corpus
"""

from __future__ import annotations

from typing import Any

from . import records
from .cli import emit
from .refresh import NON_SPELL_REFRESH_PATHS

CORPUS_DIR = "docs/research/aura-lifecycle-corpora"

# Witnesses (current-player providers unless stated; chosen by census branch/family, see records).
DURATION_WITNESSES = (
    146739,   # Corruption: fixed 14 s, ATTR13, hasted periodic
    1943,     # Rupture: ranged-per-resource 4..24 s +4 s/CP, ATTR13
    1079,     # Rip: same record shape as Rupture
    32645,    # Envenom: ranged-per-resource with minimum 0 (Trinity never scales)
    51690,    # Killing Spree: channel, ranged-per-resource minimum 0
    315341,   # Between the Eyes: ranged-per-resource, StackAmount 20
    603,      # Doom: ATTR8_HASTE_AFFECTS_DURATION + ATTR13
    115175,   # Soothing Mist: channel, ATTR8, ATTR13
    101822,   # Debuff All Resist (all population): negative non-sentinel record 427
    115192,   # all population: zero-duration record
    829,      # all population: active spell without DurationIndex
)
REFRESH_WITNESSES = (
    99, 498, 586,          # refresh|none (player)
    974, 44544, 32645,     # stack-and-refresh|none (player)
    146739, 703, 1943,     # refresh|pandemic (player)
    980, 33763,            # stack-and-refresh|pandemic (player)
    452701, 460553,        # unique-no-timer-refresh (player)
    269319, 390914,        # unique + ATTR13 -> pandemic reads live remaining (all population)
    55233, 184364,         # ATTR13 on auras without any aura period (player)
    212431, 262115, 383346,  # ATTR10 rolling periodic (player; 383346 also ATTR13)
)


def _add(p) -> None:
    p.add_argument("spell", type=int, nargs="?")
    p.add_argument("--out")


def _add_refresh(p) -> None:
    _add(p)
    p.add_argument("--carryover", action="store_true", help="write the pandemic/rolling carryover corpus")


# --------------------------------------------------------------------------
# shared records
# --------------------------------------------------------------------------
def _records(counts: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    c = counts
    rules = [
        {"id": "AL-R-B-01", "name": "authored duration source",
         "definition": "Aura max duration starts from SpellMisc.DurationIndex -> SpellDuration.Duration; exactly -1 is "
                       "permanent, any other negative is abs()'d; no DurationIndex -> -1 if Passive else 0.",
         "population": {"name": "all providers", "count": c["pop"]["all"], "families": c["families"]["all"]},
         "counterexamples": ["101822 (record 427 = -600000/600000 -> 600000 ms, not permanent)"],
         "status": "holds-on-census", "evidence": ["db2-fact", "trinity-consumer"],
         "coords": ["SpellInfo.cpp:1364", "SpellInfo.cpp:3986-3998", "SpellAuras.cpp:910-935"]},
        {"id": "AL-R-B-02", "name": "per-resource duration = combo points only, and only when minimum > 0",
         "definition": "WorldObject::CalcSpellDuration returns the minimum unless the cast consumed POWER_COMBO_POINTS; "
                       "then min(min + DurationPerResource*CP, max).  A minimum <= 0 returns early, so the resource "
                       "increment never applies.",
         "population": {"name": "providers with Duration != MaxDuration and DurationPerResource != 0",
                        "count": c["families"]["all"].get("ranged-per-resource", 0),
                        "player": c["families"]["player"].get("ranged-per-resource", 0),
                        "minimum_le_zero": c["per_resource_min_le_zero"]},
         "counterexamples": ["32645 Envenom (0..5000 +1000/CP) -> always 0 in Trinity",
                             "51690 Killing Spree (0..3500 +500/CP, channel) -> handle_immediate never channels"],
         "status": "trinity-only", "evidence": ["trinity-consumer", "db2-fact"],
         "coords": ["Object.cpp:1707-1725", "Spell.cpp:3998-4000"]},
        {"id": "AL-R-B-03", "name": "MinDuration is not an aura floor; PvPDurationIndex is unconsumed",
         "definition": "SpellMisc.MinDuration feeds missile travel time only; PvPDurationIndex has no Trinity consumer.",
         "population": {"name": "providers with nonzero PvPDurationIndex", "count": c["pvp"]},
         "counterexamples": [], "status": "trinity-only", "evidence": ["trinity-consumer", "retail-unknown"],
         "coords": ["Spell.cpp:888-896", "Object.cpp:2621", "SpellInfo.cpp:1364"]},
        {"id": "AL-R-B-04", "name": "reapplication branch is decided by (multislot, StackAmount, unique attrs)",
         "definition": "Same-caster reapplication in Trinity takes exactly one branch: new-object (Passive or 3 hardcoded "
                       "ids: never found), stack-and-refresh (StackAmount>0), unique-no-timer-refresh (StackAmount==0 and "
                       "ATTR1_AURA_UNIQUE|ATTR5_AURA_UNIQUE_PER_CASTER), refresh (otherwise).",
         "population": {"name": "provider spells", "counts": c["branches"]},
         "counterexamples": ["effect-mask mismatch recreates (Unit.cpp:3406-3407) -- runtime, not census-visible",
                             "stack decrease (num<0) never refreshes (track C)"],
         "status": "holds-on-census", "evidence": ["trinity-consumer", "db2-fact"],
         "coords": ["Unit.cpp:3386-3446", "SpellAuras.cpp:1093-1124", "SpellInfo.cpp:1803-1812"]},
        {"id": "AL-R-B-05", "name": "same SpellId does not imply refresh",
         "definition": "A second application of the same SpellId refreshes only when GetOwnedAura finds it: multislot "
                       "auras always create a new Aura, non-shared-slot auras from another caster create their own Aura "
                       "(coexist or replace: track A), effect-mask mismatch recreates.",
         "population": {"name": "provider spells on the new-object branch", "counts": c["branches_new_object"]},
         "counterexamples": [], "status": "holds-on-census", "evidence": ["trinity-consumer"],
         "coords": ["Unit.cpp:3395-3407", "SpellInfo.cpp:1803-1812"]},
        {"id": "AL-R-B-06", "name": "periodic timer on refresh (Spell hit path)",
         "definition": "On a Spell-hit timer refresh the periodic phase is reset iff StackAmount<2 and not TRIGGERED_DONT_RESET_"
                       "PERIODIC_TIMER and not ATTR13 (Spell.cpp:3240 is the only place that computes this); every "
                       "non-Spell refresh (AddAura, effect 289, linked REAPPLY, steal, scripts) uses the default "
                       "resetPeriodicTimer=true and restarts the phase whatever the StackAmount, ATTR13 still overriding "
                       "(R1-05 / R2-04).  ticksDone is always reset; the period is recomputed from the live caster.",
         "population": {"name": "provider spells by periodic-timer outcome", "counts": c["periodic_timer"]},
         "counterexamples": ["unique-no-timer-refresh: timer and ticksDone untouched",
                             "non-Spell refreshes: StackAmount>=2 still resets (SpellAuras.h:135, ModStackAmount default)"],
         "status": "trinity-only", "evidence": ["trinity-consumer", "trinity-probe"],
         "coords": ["Spell.cpp:3240", "SpellAuras.h:135", "SpellAuras.cpp:976-990", "SpellAuraEffects.cpp:949-959,961-1031"]},
        {"id": "AL-R-B-07", "name": "pinned-Trinity pandemic = min(hit + M, trunc(hit*1.3f)), independent of remaining",
         "definition": "Scope: Spell::DoSpellEffectHit, non-unique (a timer-refresh branch), positive hit duration "
                       "(Spell.cpp:3268 gate), not the channelling caster's own recast.  The ATTR13 branch reads "
                       "HitAura->GetDuration() after ModStackAmount->RefreshTimers already set it to M = CalcMaxDuration() "
                       "(aura caster's SpellMod Duration, NO power costs).  New duration = min(hit + M, trunc(1.3*hit)); the "
                       "carry is min(M, trunc(1.3*hit) - hit).  For combo-point records M is the minimum duration, so the "
                       "carry equals the minimum: Rupture 5 CP = 24000 + 4000 = 28000 whatever the remaining.",
         "population": {"name": "ATTR13 providers split by entry/branch/finite/channel (carryover.json pandemic_population)",
                        "counts": c["pandemic_population"], "carry_axis": c["carry"]},
         "counterexamples": ["ATTR13 + unique non-stacking (ATTR1_AURA_UNIQUE/ATTR5_AURA_UNIQUE_PER_CASTER, StackAmount 0): "
                             "no RefreshTimers, the formula reads the live remaining (min(hit + r, 1.3*hit))",
                             "non-Spell refreshes (AL-R-B-14): no ATTR13 branch at all, duration = M",
                             "permanent/zero ATTR13 auras (player 111400, 196099): never reach the branch",
                             "self-channel recast: cancel-then-create (AL-R-B-15)"],
         "status": "trinity-only", "evidence": ["trinity-probe", "differential"],
         "coords": ["Spell.cpp:3284-3288", "Unit.cpp:3441", "SpellAuras.cpp:1119-1121", "SpellAuras.cpp:976-990"]},
        {"id": "AL-R-B-08", "name": "unique non-stacking reapplication keeps remaining unless the hit duration changed",
         "definition": "ModStackAmount does not refresh timers; Spell.cpp then resets duration AND max to the hit duration "
                       "only when hit != current max (e.g. haste/DR/combo points/mods changed); amounts are recalculated "
                       "(SetStackAmount) in every case.",
         "population": {"name": "unique-no-timer-refresh providers", "counts": {p: v.get("unique-no-timer-refresh", 0)
                                                                              for p, v in c["branches"].items()}},
         "counterexamples": [], "status": "trinity-only", "evidence": ["trinity-consumer", "trinity-probe"],
         "coords": ["SpellAuras.cpp:1114", "Spell.cpp:3294-3298"]},
        {"id": "AL-R-B-09", "name": "ATTR13 is authored on non-periodic auras and Trinity applies it there too",
         "definition": "The Spell.cpp ATTR13 branch has no periodic check; ATTR13 providers without any periodic aura "
                       "effect still get the carry.",
         "population": {"name": "ATTR13 providers with no nonzero aura period", "counts": c["pandemic_nonperiodic"]},
         "counterexamples": [], "status": "trinity-only", "evidence": ["db2-fact", "trinity-consumer", "retail-unknown"],
         "coords": ["Spell.cpp:3284-3288"]},
        {"id": "AL-R-B-10", "name": "ATTR8 haste-affects-duration floors to whole hastened periods",
         "definition": "duration = max over live effects of max(orig/period, 1)*period with the hastened period; "
                       "with no period, int32(orig*ModCastingSpeed).  Haste never adds a partial period.",
         "population": {"name": "ATTR8 providers", "counts": c["flag_counts"]["ATTR8_HASTE_AFFECTS_DURATION"]},
         "counterexamples": ["channels take ModSpellDurationTime instead (Spell.cpp:3269)"],
         "status": "trinity-only", "evidence": ["trinity-consumer", "trinity-probe"], "coords": ["Spell.cpp:3268-3282"]},
        {"id": "AL-R-B-11", "name": "max duration is recalculated on every timer refresh",
         "definition": "RefreshTimers sets m_maxDuration = CalcMaxDuration() (live caster mods, no power costs), then "
                       "Spell.cpp overwrites max+current with the hit duration if they differ.  Current == max after "
                       "any timer refresh; pandemic sets both to the extended value.",
         "population": {"name": "timer-refresh providers", "counts": c["timer_refresh"]},
         "counterexamples": ["unique-no-timer-refresh keeps the old max when hit == max"],
         "status": "trinity-only", "evidence": ["trinity-probe"], "coords": ["SpellAuras.cpp:978", "Spell.cpp:3294-3298"]},
        {"id": "AL-R-B-12", "name": "zero duration",
         "definition": "A 0 ms max duration (zero record, active spell without DurationIndex, per-resource min 0) creates "
                       "an aura that IsExpired at the first _UpdateSpells sweep; 0 never means permanent.",
         "population": {"name": "providers whose GetDuration()==0", "counts": c["zero_duration"]},
         "counterexamples": [], "status": "trinity-only", "evidence": ["trinity-consumer"],
         "coords": ["SpellAuras.h:226", "Unit.cpp:2984-2988", "SpellInfo.cpp:3988-3989"]},
        {"id": "AL-R-B-13", "name": "rolling periodic rolls the OLD effect's remaining estimate",
         "definition": "ATTR10: on the refresh path CalculateAmount runs from SetStackAmount before RefreshTimers, adding "
                       "old estimated(bonus-done) amount * (old total - ticksDone)/old total to the new base amount.",
         "population": {"name": "ATTR10 providers", "counts": c["flag_counts"]["ATTR10_ROLLING_PERIODIC"]},
         "counterexamples": ["passive rolling (450867) is multislot: new object, permanent -> totalTicks 0 -> no roll"],
         "status": "trinity-only", "evidence": ["trinity-consumer"], "coords": ["SpellAuraEffects.cpp:829-840",
                                                                                "SpellAuras.cpp:1117"]},
        {"id": "AL-R-B-14", "name": "non-Spell refreshes have no pandemic carry and end at M",
         "definition": "Refresh entry points other than Spell::DoSpellEffectHit run ModStackAmount(+n) -> RefreshTimers "
                       "(phase reset unless ATTR13) but never reach the Spell.cpp:3262-3298 commit: no ATTR13 carry, no "
                       "ModSpellDuration/haste alignment, duration = max = M (steal then SetDuration(stolen)).",
         "population": {"name": "entry points", "paths": [x["path"] + " " + x["coords"] for x in c["non_spell_paths"]]},
         "counterexamples": ["negative stack delta (289 / linked REAPPLY) does not refresh"],
         "status": "trinity-only", "evidence": ["trinity-consumer"],
         "coords": [x["coords"] for x in c["non_spell_paths"]]},
        {"id": "AL-R-B-15", "name": "the channelling caster's recast is cancel-then-create",
         "definition": "A channeled cast always registers as CURRENT_CHANNELED_SPELL (Spell.cpp:3587-3598, never "
                       "willCastDirectly), which interrupts the running channel (Unit.cpp:3113-3117); Spell::cancel in "
                       "SPELL_STATE_CHANNELING removes the channel's auras on its hit targets with AURA_REMOVE_BY_CANCEL "
                       "(Spell.cpp:3636-3647) before the new hit, so the new hit creates a new aura (triggered self-channels "
                       "included).  Channels are never shared-slot (SpellInfo.cpp:1808-1812), so other casters own separate "
                       "auras.  A channel aura can still be found/refreshed by non-Spell paths (AL-R-B-14) or when the aura "
                       "outlived its channel / sits on a target outside the cancelled spell's target list.",
         "population": {"name": "channel providers split out of the refresh census",
                        "counts": {p: v.get("self-channel-cancel-then-create", 0) for p, v in c["branches"].items()},
                        "attr13_channels": {p: v.get("channel", 0) for p, v in c["pandemic_population"].items()}},
         "counterexamples": [], "status": "trinity-only", "evidence": ["trinity-consumer"],
         "coords": ["Spell.cpp:3587-3598", "Unit.cpp:3113-3117", "Spell.cpp:3636-3647", "SpellInfo.cpp:1808-1812"]},
    ]
    falsification = [
        {"id": "AL-F-B-01", "rule": "ATTR13 carries the remaining duration up to 30% (prior audit 08: 'strongly verified')",
         "attempt": "trace ModStackAmount->RefreshTimers->RefreshDuration before the Spell.cpp:3284 read; compile verbatim "
                    "code in tc_aura_duration_probe; refresh an 18 s/3 s DoT at t=14 s (r=4000)",
         "result": "died for pinned Trinity: probe shows after_try_refresh duration=18000 (r overwritten), final 23400 "
                   "for every r; history: before 92773e207c (2025-03-23) RefreshTimers computed the carry from the live r but the "
                   "Spell.cpp commit (hit != max -> SetMaxDuration(hit)) discarded it on the Spell path; the move to Spell.cpp "
                   "made the carry stick but read r too late -- neither Trinity revision implements r-dependent carry",
         "action": "replaced by AL-R-B-07 (trinity-only) + defect AL-D-B-01; Retail question AL-U-B-01"},
        {"id": "AL-F-B-02", "rule": "the 30% cap is of base / max / current duration",
         "attempt": "vary spell-mods, haste (ATTR8) and combo points in model+probe",
         "result": "cap is trunc(float(hit)*130/100) of the *final hit duration* (after ModSpellDuration, DurationMul, "
                   "haste alignment); M (the carried part) is CalcMaxDuration() without power costs",
         "action": "kept as refined formula"},
        {"id": "AL-F-B-03", "rule": "ATTR13 (PERIODIC_REFRESH_EXTENDS_DURATION) only occurs on periodic auras",
         "attempt": "census ATTR13 providers without any nonzero aura period",
         "result": "died: non-periodic ATTR13 providers exist (e.g. 55233 Vampiric Blood, 184364 Enraged Regeneration; note 45438 Ice Block is NOT one: effects 3 and 6 carry a 1000 ms period)",
         "action": "AL-R-B-09; Retail meaning AL-U-B-04"},
        {"id": "AL-F-B-04", "rule": "reapplying the same SpellId refreshes the existing aura",
         "attempt": "branch census + identity lookup (GetOwnedAura keys)",
         "result": "died: multislot (all Passive) never found; other casters on non-shared slots create new auras",
         "action": "AL-R-B-05; identity details to track A"},
        {"id": "AL-F-B-05", "rule": "ATTR1_AURA_UNIQUE: 'Aura will not refresh its duration when recast'",
         "attempt": "trace ModStackAmount refresh bool + Spell.cpp:3294 + probe with a changed hit duration",
         "result": "refined: no timer refresh, but a hit whose duration differs from the current max resets duration "
                   "and max; stackable unique auras refresh normally; amounts always recalculated",
         "action": "AL-R-B-08"},
        {"id": "AL-F-B-06", "rule": "Duration != MaxDuration with DurationPerResource means duration scales with resource",
         "attempt": "census ranged-per-resource minimum values; read CalcSpellDuration",
         "result": "died for minimum <= 0 (Envenom, Killing Spree): early return",
         "action": "AL-R-B-02 + defect AL-D-B-02"},
        {"id": "AL-F-B-07", "rule": "Trinity(intended), simc and Core agree on pandemic",
         "attempt": "pandemic_models sweep over r",
         "result": "carry-before-refresh == simc == Core for r <= hit; for r > hit simc/Core keep r (max), Trinity's "
                   "formula caps at 1.3*hit; Core integer floor vs Trinity float trunc diverge from hit >= 516250 ms",
         "action": "kept as model table in carryover.json; Core coords to track K"},
        {"id": "AL-F-B-08", "rule": "preserving the periodic timer on pandemic refresh preserves every tick",
         "attempt": "timeline 18 s/3 s DoT refreshed at 14 s (model + probe)",
         "result": "died: totalTicks = floor(23400/3000)=7 with preserved phase -> ticks 15..33 s, a due tick at 36 s is "
                   "suppressed, aura lives to 37.4 s (11 ticks over 37.4 s)",
         "action": "kept as timeline; same finding as AL-D-D-04 (track D; timelines AL-T-D-07, AL-T-D-19) -- cite D"},
        {"id": "AL-F-B-09", "rule": "track D AL-D-D-02: 'pinned Trinity pandemic is always 1.3 x base'",
         "attempt": "combo-point and unique branches in model + verbatim probe (test_al_b_probe.py)",
         "result": "refined: always min(hit + M, trunc(1.3*hit)); = 1.3*hit only if M >= 0.3*hit; Rupture 5 CP -> 28000; "
                   "unique non-stacking ATTR13 -> live remaining (22000 at r=4000)",
         "action": "AL-R-B-07 keeps the refined formula; AL-D-B-01 marked same_as AL-D-D-02"},
        {"id": "AL-F-B-10", "rule": "every ATTR13 timer-refresh provider reaches the carry (earlier census: player 61)",
         "attempt": "R1-04: check the Spell.cpp:3268 AuraDuration > 0 gate and the channel recast path (R2-07)",
         "result": "refined: permanent/zero ATTR13 auras never reach the branch (player 111400, 196099); self-channels "
                   "never refresh on their own recast.  Carry axis now n/a for non-positive durations and channels are "
                   "split out; the census remains a classifier restatement of the probe finding, not independent evidence",
         "action": "carryover.json pandemic_population; AL-R-B-07 scope; AL-R-B-15"},
        {"id": "AL-F-B-11", "rule": "a channel aura is refreshed by recasting the channel",
         "attempt": "R2-07: trace SetCurrentCastSpell / Spell::cancel",
         "result": "died for the channelling caster (cancel-then-create); refresh only via non-Spell paths",
         "action": "AL-R-B-15; branch self-channel-cancel-then-create in refresh.json"},
    ]
    defects = [
        {"id": "AL-D-B-01", "coords": ["Spell.cpp:3284-3288", "Unit.cpp:3441", "SpellAuras.cpp:1119-1121",
                                       "SpellAuras.cpp:976-990", "git 92773e207c"],
         "description": "ATTR13 pandemic branch reads HitAura->GetDuration() after the refresh path already reset it to "
                        "the recalculated max, so the remaining time never enters the formula.",
         "lifecycle_effect": "Every ATTR13 refresh yields min(hit + M, trunc(1.3*hit)): the full 30% bonus regardless of "
                             "remaining (and for combo-point spells a carry of M = minimum duration, e.g. Rupture 5 CP "
                             "-> 28000 always).",
         "oracle_behaviour": "reproduced (refresh.pandemic_models['trinity-7f3d43b'], apply_hit), probe-confirmed; "
                             "competing 'carry-before-refresh' model kept for Retail",
         "same_as": ["AL-D-D-02 (track D, probe tc_aura_periodic_probe, timeline AL-T-D-07)", "TL-K-01 / AL-K-B-04"],
         "refinement_over_AL-D-D-02": "the result is min(hit + M, trunc(1.3*hit)) with M = CalcMaxDuration() at "
                                      "RefreshTimers (live caster SpellMod Duration, NO power costs): it equals 1.3*hit "
                                      "only when M >= 0.3*hit.  Combo-point records carry M = minimum (Rupture/Rip 5 CP: "
                                      "28000, not 31200); ATTR1_AURA_UNIQUE/ATTR5_AURA_UNIQUE_PER_CASTER non-stacking "
                                      "ATTR13 auras (15 all, 0 player) skip RefreshTimers and do read the live remaining.",
         "verdict": "defect relative to Trinity's evident intent (newDuration = hit + remaining only makes sense if "
                    "remaining is live; commit 92773e207c is titled 'Fixed ...'), relative to the pre-2025 Trinity code "
                    "(read live r in RefreshTimers, though its Spell-path result was then overwritten), simc "
                    "(action.cpp:4603, buff.cpp:1920) and Core CappedCarryover (aura_state.rs:1143-1199), which all make "
                    "the carry depend on remaining.  Retail itself stays AL-U-B-01.",
         "evidence": ["trinity-probe", "differential"]},
        {"id": "AL-D-B-02", "coords": ["Object.cpp:1709-1711"],
         "description": "CalcSpellDuration returns early when the minimum duration <= 0, so per-resource duration never "
                        "applies to records with minimum 0.",
         "lifecycle_effect": "Envenom 32645 buff has 0 ms duration (expires at first update); Killing Spree 51690 never "
                             "starts channeling (handle_immediate requires duration > 0).",
         "oracle_behaviour": "reproduced (duration.calc_spell_duration); flagged, not corrected",
         "evidence": ["trinity-consumer", "db2-fact"]},
        {"id": "AL-D-B-04", "coords": ["Spell.cpp:3279-3281", "Spell.cpp:3230-3232", "Spell.cpp:515-522"],
         "description": "ATTR8_HASTE_AFFECTS_DURATION with no nonzero GetPeriod() multiplies by "
                        "m_originalCaster->m_unitData->ModCastingSpeed, not the null-checked caster of 3230-3232; "
                        "m_originalCaster is null for GameObject casters and absent/out-of-world original casters.",
         "lifecycle_effect": "latent null dereference on the hit of such auras; otherwise another binary32 site int32(orig*float)",
         "oracle_behaviour": "duration.haste_affects_duration raises nothing but hit_commit fails closed without a caster; "
                             "source-backed, not probed (R2-14)",
         "population": {"name": "non-channel ATTR8 providers, all aura periods 0, positive duration",
                        "counts": {p: v["count"] for p, v in c["attr8_no_period"].items()},
                        "first": c["attr8_no_period"]["all"]["first"],
                        "reconciliation": "R2-14's 63 = every ATTR8 provider without a periodic aura type: 23 reach "
                                          "Spell.cpp:3281 (non-channel, all periods 0, duration > 0), 5 are stopped by "
                                          "the AuraDuration > 0 gate (3268), 35 are channels (IsChanneled branch 3269 "
                                          "precedes)"},
         "evidence": ["trinity-consumer"]},
        {"id": "AL-D-B-03", "coords": ["SpellAuraEffects.cpp:829-840", "SpellAuraEffects.cpp:872-876"],
         "description": "ROLLING_PERIODIC adds the old effect's *estimated* amount (already bonus-done applied) to the new "
                        "base amount, after which CalculateEstimatedAmount applies bonus-done again.",
         "lifecycle_effect": "Rolled remainder may be bonus-compounded (likely defect; depends on DoT bonus path, track D).",
         "oracle_behaviour": "reproduced as refresh.rolling_periodic_amount (pre-stack, pre-round); compounding not "
                             "asserted", "evidence": ["trinity-consumer", "structural-inference"]},
    ]
    unknowns = [
        {"id": "AL-U-B-01", "subject": "Retail pandemic carry",
         "question": "Does Retail carry the remaining duration (min(r, 0.3*D)) or always 30% (pinned Trinity), and what is "
                     "D (hit after haste/mods?) and the rounding?",
         "known": "pinned Trinity: always min(M, trunc(1.3*hit)-hit); pre-2025 Trinity, simc, Core: r-dependent",
         "why_unresolved": "no Retail consumer; Trinity path is defective", "evidence": ["retail-unknown", "trinity-probe"],
         "coords": ["Spell.cpp:3284-3288"], "blocker": "Retail observation", "reopen_condition": "AL-X-B-01/02 result",
         "build_skew": False},
        {"id": "AL-U-B-02", "subject": "PvPDurationIndex",
         "question": "When is the PvP duration record selected and does it compose with DR / mods?",
         "known": f"{c['pvp']['all']} providers carry one ({c['pvp']['player']} player); no Trinity/simc consumer",
         "why_unresolved": "no consumer", "evidence": ["db2-fact", "retail-unknown"], "coords": ["SpellInfo.cpp:1364"],
         "blocker": "Retail PvP observation", "reopen_condition": "client symbol or PvP trace", "build_skew": False},
        {"id": "AL-U-B-03", "subject": "ATTR15 bit 9 (simc SX_AURA_DOES_NOT_REFRESH)",
         "question": "Is reapplication ignored entirely, or only duration kept? (and raw 490 async stacking)",
         "known": f"raw 489 providers {c['flag_counts']['ATTR15_UNK9_SIMC_AURA_DOES_NOT_REFRESH']}, raw 490 "
                  f"{c['flag_counts']['ATTR15_UNK10_SIMC_ASYNCHRONOUS_STACKING']}; Trinity UNK9/UNK10 no consumer; "
                  "simc 489 -> buff DISABLED refresh: stacks bumped, expiry untouched (buff.cpp:740-744, 2540-2554); "
                  "simc 490 -> ASYNCHRONOUS stack behaviour when max stack > 1: each stack own expiry "
                  "(buff.cpp:777-779, 2229-2234); Core catalog names both with simc-style meanings (navigation)",
         "why_unresolved": "single secondary consumer", "evidence": ["simc-consumer", "retail-unknown"],
         "coords": ["simc engine/buff/buff.cpp:739-744", "simc engine/buff/buff.cpp:777-779",
                    "simc engine/dbc/data_enums.hh:1909-1910", "core crates/dbc/src/spell_attribute.rs:180-181,490-491"],
         "blocker": "Retail trace", "reopen_condition": "Retail recast of a flagged aura",
         "build_skew": False},
        {"id": "AL-U-B-04", "subject": "ATTR13 on non-periodic auras",
         "question": "Does Retail extend non-periodic buffs (Vampiric Blood 55233, Enraged Regeneration 184364) on refresh?",
         "known": "authored; Trinity applies; simc buff PANDEMIC only via the flag", "why_unresolved": "no Retail consumer",
         "evidence": ["db2-fact", "retail-unknown"], "coords": ["Spell.cpp:3284"], "blocker": "cooldown-gated refresh in Retail",
         "reopen_condition": "a refreshable non-periodic ATTR13 aura observed", "build_skew": False},
        {"id": "AL-U-B-05", "subject": "per-resource duration with minimum 0",
         "question": "Envenom / Killing Spree duration per combo point in Retail",
         "known": "record (0, max, +per); Trinity yields 0", "why_unresolved": "Trinity defect path",
         "evidence": ["db2-fact", "retail-unknown"], "coords": ["Object.cpp:1707-1725"], "blocker": "Retail observation",
         "reopen_condition": "AL-X-B-03", "build_skew": False},
        {"id": "AL-U-B-06", "subject": "ATTR8 haste alignment",
         "question": "Retail: hasted duration floored to whole hastened periods, or orig with partial tick, or orig*haste?",
         "known": "Trinity floors (Spell.cpp:3271-3282); simc hasted dots keep duration with partial tick",
         "why_unresolved": "consumers disagree", "evidence": ["trinity-consumer", "simc-consumer", "retail-unknown"],
         "coords": ["Spell.cpp:3271-3282"], "blocker": "Retail observation", "reopen_condition": "AL-X-B-04", "build_skew": False},
        {"id": "AL-U-B-07", "subject": "tick schedule after pandemic",
         "question": "Retail: is the phase preserved and is the final partial tick delivered after a carried refresh?",
         "known": "Trinity preserves phase, caps ticks at floor(max/period) -> suppressed tick; simc partial ticks",
         "why_unresolved": "tick policy is track D; no Retail consumer", "evidence": ["trinity-probe", "retail-unknown"],
         "coords": ["SpellAuraEffects.cpp:1250-1276"], "blocker": "Retail combat-log ticks (restricted in 12.x, track L)",
         "reopen_condition": "AL-X-B-05", "build_skew": False},
        {"id": "AL-U-B-08", "subject": "rolling periodic amount",
         "question": "Exact Retail rolled amount (remaining ticks fraction? bonus once or twice?)",
         "known": "Trinity SpellAuraEffects.cpp:829-840", "why_unresolved": "single consumer, likely defect AL-D-B-03",
         "evidence": ["trinity-consumer", "retail-unknown"], "coords": ["SpellAuraEffects.cpp:829-840"],
         "blocker": "Retail damage observation", "reopen_condition": "AL-X-B-06", "build_skew": False},
        {"id": "AL-U-B-09", "subject": "hit vs update order in one ms",
         "question": "When a refresh lands in the same ms as an expiry/tick, which runs first?",
         "known": "timelines assume update(t) then hit(t)", "why_unresolved": "scheduler order is track I",
         "evidence": ["unresolved"], "coords": ["Unit.cpp:2957"], "blocker": "track I", "reopen_condition": "track I ordering corpus",
         "build_skew": False},
    ]
    experiments = [
        {"id": "AL-X-B-01", "question": "pandemic carry depends on remaining? (Corruption 146739, 14 s, 2 s period)",
         "models": [{"name": "trinity-7f3d43b", "predict": "refresh at r=2000 -> 18200; at r=8000 -> 18200"},
                    {"name": "carry-before-refresh/simc/core", "predict": "r=2000 -> 16000; r=8000 -> 18200"}],
         "setup": "Affliction warlock, no haste-duration effects; apply Corruption, refresh at r=2 s and separately r=8 s",
         "observable": "AuraData.duration / expirationTime of the refreshed debuff (UNIT_AURA updated)",
         "discriminates": "AL-D-B-01 / AL-U-B-01", "fidelity": "approximate", "related": ["AL-U-B-01", "AL-R-B-07"]},
        {"id": "AL-X-B-02", "question": "combo-point pandemic (Rupture 1943, 5 CP)",
         "models": [{"name": "trinity-7f3d43b", "predict": "any r -> 28000 (24000 + min duration 4000)"},
                    {"name": "carry-before-refresh", "predict": "r=1000 -> 25000; r=10000 -> 31200"}],
         "setup": "Assassination rogue; 5 CP Rupture, refresh with 5 CP at r=1 s and r=10 s",
         "observable": "AuraData.duration after refresh", "discriminates": "AL-D-B-01 with M != r",
         "fidelity": "approximate", "related": ["AL-U-B-01"]},
        {"id": "AL-X-B-03", "question": "Envenom duration per combo point",
         "models": [{"name": "trinity-7f3d43b", "predict": "0 ms (buff never visible)"},
                    {"name": "record-interpolation", "predict": "min(0 + 1000*CP, 5000)"}],
         "setup": "Assassination rogue, Envenom at 1..5 CP", "observable": "AuraData.duration of 32645",
         "discriminates": "AL-D-B-02", "fidelity": "exact", "related": ["AL-U-B-05"]},
        {"id": "AL-X-B-04", "question": "ATTR8 haste duration (Doom 603 / Soothing Mist)",
         "models": [{"name": "trinity-floor", "predict": "floor(orig/hastedPeriod)*hastedPeriod"},
                    {"name": "orig-partial-tick", "predict": "orig duration unchanged"},
                    {"name": "orig-times-haste", "predict": "orig*ModCastingSpeed"}],
         "setup": "known haste rating, cast once", "observable": "AuraData.duration",
         "discriminates": "AL-U-B-06", "fidelity": "approximate", "related": ["AL-R-B-10"]},
        {"id": "AL-X-B-05", "question": "tick phase and final partial tick after pandemic refresh",
         "models": [{"name": "trinity", "predict": "phase preserved; ticks capped at floor(newmax/period); no partial"},
                    {"name": "simc", "predict": "phase preserved; partial final tick"}],
         "setup": "Corruption refreshed mid-period", "observable": "periodic damage events timing (CLEU if available in 12.x)",
         "discriminates": "AL-U-B-07", "fidelity": "insufficient", "related": ["AL-U-B-07"]},
        {"id": "AL-X-B-06", "question": "rolling periodic amount (Deep Wounds 262115 / Barbed Shot 217200)",
         "models": [{"name": "trinity", "predict": "new + oldEstimated*remainingTicks/totalTicks (bonus reapplied)"},
                    {"name": "bonus-once", "predict": "rolled remainder not re-scaled"}],
         "setup": "refresh at known remaining ticks with fixed stats", "observable": "tick amounts after refresh",
         "discriminates": "AL-D-B-03", "fidelity": "insufficient", "related": ["AL-U-B-08"]},
    ]
    nav = [
        {"id": "AL-K-B-01", "topic": "CappedCarryover formula",
         "core_coords": ["crates/combat/src/aura_state.rs:1143-1199", "crates/combat/src/aura_state.rs:18-19"],
         "research_ref": ["AL-R-B-07", "AL-D-B-01", "AL-U-B-01"],
         "observation": "Core computes base + min(remaining, floor(3*base/10)) and keeps max(projected, old expiry): "
                        "the simc / carry-before-refresh model, not pinned Trinity (which ignores remaining).",
         "reopen_condition": "Retail result of AL-X-B-01/02", "evidence": "core-navigation"},
        {"id": "AL-K-B-02", "topic": "carry rounding",
         "core_coords": ["crates/combat/src/aura_state.rs:1161-1162"], "research_ref": ["AL-F-B-07"],
         "observation": "integer floor(3D/10) vs Trinity trunc(float(D)*130/100)-D: equal for D < 516250 ms",
         "reopen_condition": "durations >= 516250 ms with ATTR13", "evidence": "core-navigation"},
        {"id": "AL-K-B-03", "topic": "reapplication policy vocabulary",
         "core_coords": ["crates/combat/src/aura_state.rs:686-691"], "research_ref": ["AL-R-B-04", "AL-R-B-08"],
         "observation": "RestartLifetime / CappedCarryover / IndependentStackDeadlines / PreserveExistingLifetime vs Trinity "
                        "branches refresh / refresh+ATTR13 / new-object / unique-no-timer-refresh; Trinity's unique branch "
                        "is not pure preservation (resets when the hit duration differs from max).",
         "reopen_condition": "track K coremap", "evidence": "core-navigation"},
        {"id": "AL-K-B-04", "topic": "TL-K-01 (250 ms aura refreshed at 225 ms)",
         "core_coords": ["crates/combat/src/aura_state.rs:1143-1199"], "research_ref": ["AL-D-B-01", "AL-R-B-07"],
         "observation": "verbatim probe confirms K: pinned Trinity 550 ms (325 ms from refresh), Core 500 ms; the divergence "
                        "is exactly the AL-D-B-01 path (Trinity ignores remaining), not a Core arithmetic slip; at a "
                        "150 ms refresh both give 475 because r >= 0.3*hit",
         "reopen_condition": "Retail AL-X-B-01", "evidence": "core-navigation"},
        {"id": "AL-K-B-05", "topic": "raw 489 / 490 meanings",
         "core_coords": ["crates/dbc/src/spell_attribute.rs:180-181", "crates/dbc/src/spell_attribute.rs:490-491"],
         "research_ref": ["AL-U-B-03"],
         "observation": "Core implements simc's meanings (489 preserve expiry while bumping stacks; 490 independent stack "
                        "deadlines); Trinity has no consumer (UNK9/UNK10), so their only source is simc buff.cpp",
         "reopen_condition": "Retail trace of a 489/490 aura", "evidence": "core-navigation"},
    ]
    return {"rules": rules, "falsification": falsification, "trinity_defects": defects, "unknowns": unknowns,
            "retail_experiments": experiments, "core_navigation": nav}


def _counts(ctx) -> dict[str, Any]:
    from . import providers
    from .duration import census as dcensus, empower_spells, facts, spellinfo_get_duration
    from .refresh import census as rcensus
    pops = providers.populations(ctx)
    d = dcensus(ctx, pops)
    r = rcensus(ctx, pops)
    emp = empower_spells(ctx.bundle.source)
    per_res_min0: dict[str, list[int]] = {}
    pandemic_np: dict[str, int] = {}
    zero: dict[str, int] = {}
    for pop, spells in pops.items():
        lst, np_, z = [], 0, 0
        for s in sorted(spells):
            f = facts(ctx.data, s, emp)
            e = f.duration_entry
            if e and e.duration != e.max_duration and e.per_resource and e.duration <= 0 and e.duration != -1:
                lst.append(s)
            if f.has((13, 0x00100000)) and not any(f.periods):
                np_ += 1
            if spellinfo_get_duration(f) == 0:
                z += 1
        per_res_min0[pop], pandemic_np[pop], zero[pop] = lst, np_, z
    return {
        "pop": {k: len(v) for k, v in pops.items()},
        "families": d["families"], "flags": d["flags"], "pvp": d["pvp_duration_index_nonzero"],
        "flag_counts": {name: {p: d["flags"][p].get(name, 0) for p in pops}
                        for name in ("ATTR8_HASTE_AFFECTS_DURATION", "ATTR10_ROLLING_PERIODIC",
                                     "ATTR13_PERIODIC_REFRESH_EXTENDS_DURATION", "ATTR15_UNK9_SIMC_AURA_DOES_NOT_REFRESH",
                                     "ATTR15_UNK10_SIMC_ASYNCHRONOUS_STACKING",
                                     "ATTR1_AURA_UNIQUE", "ATTR5_AURA_UNIQUE_PER_CASTER")},
        "branches": r["branches"], "periodic_timer": r["periodic_timer"], "carry": r["carry"],
        "combos": r["combos"], "refresh_witnesses": r["witnesses"], "family_witnesses": d["family_witnesses"],
        "distinct_duration_entries": d["distinct_duration_entries"],
        "branches_new_object": {p: v.get("new-object", 0) for p, v in r["branches"].items()},
        "pandemic_population": r["pandemic_population"],
        "attr8_no_period": r["attr8_no_period_branch"],
        "non_spell_paths": list(NON_SPELL_REFRESH_PATHS),
        "timer_refresh": {p: v.get("refresh", 0) + v.get("stack-and-refresh", 0) for p, v in r["branches"].items()},
        "per_resource_min_le_zero": per_res_min0,
        "pandemic_nonperiodic": pandemic_np,
        "zero_duration": zero,
    }


def _provenance(cmd: str) -> dict[str, Any]:
    return records.provenance(cmd, track="B")


def _duration_corpus(ctx) -> dict[str, Any]:
    from .duration import explain
    c = _counts(ctx)
    rec = _records(c)
    families = {
        "permanent-sentinel": "Duration == MaxDuration == -1 -> permanent (IsPermanent: max == -1)",
        "negative-non-sentinel": "a negative other than -1 -> abs() (not permanent) in Trinity",
        "no-duration-index-passive": "no record + Passive -> -1 (permanent)",
        "no-duration-index-active": "no record, not Passive -> 0 (expires at first update)",
        "zero": "record 0/0 -> 0 (expires at first update)",
        "fixed": "Duration == MaxDuration > 0",
        "ranged-per-resource": "min + per*combo points (only when min > 0 and CP consumed), capped at max",
        "ranged-no-increment": "min always (no resource increment authored)",
        "no-misc-row": "no SpellMisc row: no DurationEntry -> as no-duration-index",
    }
    payload = {
        "provenance": _provenance(f"aura_lifecycle.py duration --out {CORPUS_DIR}/duration.json"),
        "populations": c["pop"],
        "family_definitions": families,
        "census": {"families": c["families"], "lifecycle_flags": c["flags"], "pvp_duration_index_nonzero": c["pvp"],
                   "distinct_duration_entries": c["distinct_duration_entries"],
                   "per_resource_minimum_le_zero": c["per_resource_min_le_zero"],
                   "zero_duration_providers": c["zero_duration"],
                   "pandemic_without_periodic": c["pandemic_nonperiodic"],
                   "family_witnesses": c["family_witnesses"], "evidence": "db2-fact",
                   "count_unit": "provider spells (DIFFICULTY_NONE)"},
        "pipeline": [
            {"step": "quote", "what": "AuraDuration = SpellValue.Duration or Aura::CalcMaxDuration(spell, origCaster, &m_powerCost)",
             "coords": "Spell.cpp:3213-3216"},
            {"step": "CalcMaxDuration", "what": "CalcSpellDuration (combo points) -> passive&no record=-1 -> SpellMod Duration -> +1000 empower",
             "coords": "SpellAuras.cpp:910-935; Object.cpp:1707-1725"},
            {"step": "DR", "what": "negative hit: limit duration then *mod(level); 0 => immune", "coords": "Spell.cpp:3219-3221; Unit.cpp:9354-9431"},
            {"step": "create-or-refresh", "what": "new Aura: m_maxDuration = CalcMaxDuration(caster) (no power costs); refresh: RefreshTimers",
             "coords": "SpellAuras.cpp:494-495; SpellAuras.cpp:976-990"},
            {"step": "ModSpellDuration", "what": "target mechanic/dispel duration mods (negative only), ATTR7 exempt", "coords": "Object.cpp:1727-1790"},
            {"step": "DurationMul", "what": "int32(float(d)*DurationMul)", "coords": "Spell.cpp:3266"},
            {"step": "haste", "what": "channel: ModSpellDurationTime; else ATTR8: floor to hastened periods / *ModCastingSpeed",
             "coords": "Spell.cpp:3268-3282; Object.cpp:1818-1838"},
            {"step": "pandemic", "what": "refresh && ATTR13: min(d + aura.GetDuration(), CalculatePct(d,130))", "coords": "Spell.cpp:3284-3288"},
            {"step": "commit", "what": "if d != aura max: SetMaxDuration(d); SetDuration(d)", "coords": "Spell.cpp:3294-3298"},
            {"step": "channel", "what": "m_channelDuration = GetDuration() -> SpellMod Duration -> DurationMul -> ModSpellDurationTime (+1000 empower)",
             "coords": "Spell.cpp:3994-4040"},
        ],
        "numeric": {
            "calculate_pct_130_first_divergence_from_integer_floor": 516250,
            "note": "trunc(float(D)*130f/100f) == floor(13D/10) for all D < 516250 ms (checked 1..3,000,000; 162,071 "
                    "values differ above)", "evidence": "differential", "coords": "Util.h:72-75",
            "attr8_no_period": {"formula": "int32(float(orig) * ModCastingSpeed) (binary32 product, truncation)",
                                "coords": "Spell.cpp:3281", "population": c["attr8_no_period"],
                                "defect": "AL-D-B-04 (m_originalCaster may be null)", "evidence": "trinity-consumer"},
        },
        "witnesses": [explain(ctx, s) for s in DURATION_WITNESSES],
    }
    for k in ("rules", "falsification", "trinity_defects", "unknowns", "retail_experiments", "core_navigation"):
        payload[k] = [e for e in rec[k] if e["id"] in _DURATION_IDS]
    records.validate_corpus(payload)
    return payload


_DURATION_IDS = {"AL-R-B-01", "AL-R-B-02", "AL-R-B-03", "AL-R-B-10", "AL-R-B-11", "AL-R-B-12",
                 "AL-F-B-02", "AL-F-B-06", "AL-D-B-02", "AL-D-B-04", "AL-U-B-02", "AL-U-B-05", "AL-U-B-06",
                 "AL-X-B-03", "AL-X-B-04"}
_CARRY_IDS = {"AL-R-B-07", "AL-R-B-09", "AL-R-B-13", "AL-R-B-14", "AL-F-B-10", "AL-F-B-01", "AL-F-B-03", "AL-F-B-07", "AL-F-B-08", "AL-F-B-09",
              "AL-D-B-01", "AL-D-B-03", "AL-U-B-01", "AL-U-B-04", "AL-U-B-07", "AL-U-B-08",
              "AL-X-B-01", "AL-X-B-02", "AL-X-B-05", "AL-X-B-06", "AL-K-B-01", "AL-K-B-02", "AL-K-B-04"}


def _refresh_corpus(ctx) -> dict[str, Any]:
    from .refresh import explain
    c = _counts(ctx)
    rec = _records(c)
    taxonomy = [
        {"branch": "new-object", "when": "IsMultiSlotAura (Passive or 55849/40075/44413)",
         "duration": "fresh CalcMaxDuration + hit commit", "stacks": "new object, own count", "periodic": "new timers",
         "amount": "new", "observable": "two auras of one SpellId coexist", "coords": "Unit.cpp:3395; SpellInfo.cpp:1803-1806"},
        {"branch": "refresh", "when": "found, StackAmount == 0, not unique",
         "duration": "max = CalcMaxDuration(); current = max; then hit commit", "stacks": "stays 1",
         "periodic": "ticks reset; phase reset unless ATTR13 / TRIGGERED_DONT_RESET", "amount": "recalculated from new base points",
         "observable": "duration restarts (or ATTR13 extension)", "coords": "SpellAuras.cpp:1093-1124,976-990"},
        {"branch": "stack-and-refresh", "when": "found, StackAmount > 0",
         "duration": "as refresh (also at the cap)", "stacks": "+StackAmount of the cast (usually 1), capped at CalcMaxStackAmount",
         "periodic": "ticks reset; phase preserved when StackAmount >= 2 or ATTR13; reset when StackAmount == 1",
         "amount": "recalculated (stack multiply unless SuppressPointsStacking; AuraPointsStack accumulates base)",
         "observable": "stack +1 and duration restart", "coords": "Unit.cpp:3409-3441; SpellAuras.cpp:1093-1124"},
        {"branch": "unique-no-timer-refresh", "when": "found, StackAmount == 0, ATTR1_AURA_UNIQUE or ATTR5_AURA_UNIQUE_PER_CASTER",
         "duration": "kept unless hit duration != current max (then both reset to hit)", "stacks": "stays 1",
         "periodic": "untouched (ticksDone kept)", "amount": "recalculated",
         "observable": "recast does not restart the timer", "coords": "SpellAuras.cpp:1114; Spell.cpp:3294-3298"},
        {"branch": "self-channel-cancel-then-create", "when": "the channelling caster recasts a channel (triggered too)",
         "duration": "old channel cancelled (auras removed BY_CANCEL), new aura created",
         "stacks": "new", "periodic": "new timers", "amount": "new",
         "observable": "no refresh / no carry on own recast; non-Spell paths can still refresh (AL-R-B-14)",
         "coords": "Spell.cpp:3587-3598; Unit.cpp:3113-3117; Spell.cpp:3636-3647"},
        {"branch": "non-Spell refresh (entry point)", "when": "AddAura / steal / vehicle / effect 289 / linked REAPPLY / scripts find the aura",
         "duration": "RefreshTimers only: duration = max = M; no ATTR13 carry; no ModSpellDuration/haste commit",
         "stacks": "+n (positive delta refreshes)", "periodic": "phase reset unless ATTR13 (default resetPeriodicTimer=true)",
         "amount": "recalculated", "observable": "-", "coords": "; ".join(x["coords"] for x in NON_SPELL_REFRESH_PATHS)},
        {"branch": "other-caster", "when": "found by any caster only if StackAmount > 1 && !channel && !DOT_STACKING_RULE",
         "duration": "shared-slot: as stack-and-refresh; otherwise a new aura (coexist/replace: track A)",
         "stacks": "shared-slot counts all casters", "periodic": "-", "amount": "-",
         "observable": "-", "coords": "Unit.cpp:3391-3402; SpellInfo.cpp:1808-1812"},
    ]
    witnesses = []
    for s in REFRESH_WITNESSES:
        w = explain(ctx, s)
        witnesses.append(w)
    payload = {
        "provenance": _provenance(f"aura_lifecycle.py refresh --out {CORPUS_DIR}/refresh.json"),
        "populations": c["pop"],
        "call_path": ["Spell.cpp:3226 DoSpellEffectHit", "SpellAuras.cpp:350 TryRefreshStackOrCreate",
                      "Unit.cpp:3386 _TryStackingOrRefreshingExistingAura", "Unit.cpp:3441 ModStackAmount",
                      "SpellAuras.cpp:1117 SetStackAmount", "SpellAuras.cpp:1121 RefreshTimers",
                      "Spell.cpp:3262-3298 duration commit"],
        "taxonomy": taxonomy,
        "census": {"branches": c["branches"], "periodic_timer": c["periodic_timer"], "carry": c["carry"],
                   "combos": c["combos"], "witnesses": c["refresh_witnesses"], "evidence": ["db2-fact", "trinity-consumer"],
                   "count_unit": "provider spells (DIFFICULTY_NONE)"},
        "not_in_census": ["script refresh behaviour (AuraScript SetDuration/RefreshDuration: track H)",
                          "spell_group exclusivity (track A)", "area recipients (track F)",
                          "TRIGGERED_DONT_RESET_PERIODIC_TIMER casts (runtime flag)"],
        "witnesses": witnesses,
    }
    for k in ("rules", "falsification", "trinity_defects", "unknowns", "retail_experiments", "core_navigation"):
        payload[k] = [e for e in rec[k] if e["id"] not in _DURATION_IDS and e["id"] not in _CARRY_IDS]
    records.validate_corpus(payload)
    return payload


def carryover_timelines() -> dict[str, Any]:
    """Synthetic + real-shape carryover timelines (no context needed)."""
    from .duration import (ATTR1_AURA_UNIQUE, ATTR13_PERIODIC_REFRESH_EXTENDS_DURATION, Caster, DurationEntry,
                           HitInputs, SpellFacts)
    from .refresh import pandemic_models, timeline

    def facts(spell, dur, mx, per, periods, *attrs, stack=0):
        a = [0] * 17
        for w, m in attrs:
            a[w] |= m
        return SpellFacts(spell, tuple(a), DurationEntry(0, dur, mx, per), stack_amount=stack, periods=periods)

    def strip(tl):
        for e in tl["events"]:
            e.pop("log", None)
        return tl

    p13 = ATTR13_PERIODIC_REFRESH_EXTENDS_DURATION
    out: dict[str, Any] = {}
    dot = facts(0, 18000, 18000, 0, (3000,), p13)
    out["dot18_refresh_at_14s"] = {
        "question": "18 s / 3 s ATTR13 DoT refreshed at t=14 s (4 s left)",
        "timeline": strip(timeline(dot, [(0, HitInputs()), (14000, HitInputs())], end=40000, step=100)),
        "models": pandemic_models(18000, 4000, 18000),
        "rules_out": "carry-before-refresh / simc / core predict 22000; pinned Trinity gives 23400 (probe-confirmed)",
        "evidence": ["trinity-probe", "differential"]}
    sweep = []
    for r in range(0, 18001, 1000):
        m = pandemic_models(18000, r, 18000)
        sweep.append({"remaining": r, **{k: v["value"] for k, v in m.items()}})
    out["dot18_sweep"] = {"hit": 18000, "recalculated_max": 18000, "rows": sweep, "evidence": "differential"}
    rup = facts(1943, 4000, 24000, 4000, (2000,), p13)
    rows = []
    for r in (1000, 3000, 4000, 7200, 10000):
        m = pandemic_models(24000, r, 4000)
        rows.append({"remaining": r, **{k: v["value"] for k, v in m.items()}})
    out["rupture_5cp_sweep"] = {"spell": 1943, "hit": 24000, "recalculated_max_without_power_costs": 4000, "rows": rows,
                                "timeline_refresh_at_20s": strip(timeline(
                                    rup, [(0, HitInputs(combo_points=5)), (20000, HitInputs(combo_points=5))],
                                    end=60000, step=100)),
                                "note": "RefreshTimers' CalcMaxDuration() passes no power costs -> M = minimum 4000",
                                "evidence": ["trinity-probe", "db2-fact"]}
    uniq = facts(0, 18000, 18000, 0, (3000,), p13, ATTR1_AURA_UNIQUE)
    out["unique_pandemic_reads_live_remaining"] = {
        "question": "ATTR1_AURA_UNIQUE + ATTR13, non-stacking: no RefreshTimers, pandemic reads the live remaining",
        "timeline": strip(timeline(uniq, [(0, HitInputs()), (14000, HitInputs())], end=40000, step=100)),
        "evidence": ["trinity-probe"]}
    np_ = facts(55233, 10000, 10000, 0, (0,), p13)
    out["nonperiodic_pandemic"] = {
        "question": "ATTR13 on a non-periodic aura (Vampiric Blood shape 10 s) refreshed at 5 s",
        "timeline": strip(timeline(np_, [(0, HitInputs()), (5000, HitInputs())], end=20000, step=100,
                                   periodic_effects=(False,))),
        "evidence": ["trinity-consumer"]}
    tiny = facts(0, 250, 250, 0, (0,), p13)
    out["k_tl_01_250ms_refresh_at_225"] = {
        "question": "track K TL-K-01: 250 ms ATTR13 aura refreshed at 225 ms (25 ms left)",
        "timeline": strip(timeline(tiny, [(0, HitInputs()), (225, HitInputs())], end=700, step=25,
                                   periodic_effects=(False,))),
        "models": pandemic_models(250, 25, 250),
        "result": "pinned Trinity expires at 550 ms (probe-confirmed: after_try_refresh duration 250, final 325); "
                  "Core CappedCarryover 500 ms; refresh at 150 ms gives 475 in both -> Core diverges from pinned "
                  "Trinity only because of AL-D-B-01",
        "evidence": ["trinity-probe", "core-navigation"]}
    hasted = facts(0, 18000, 18000, 0, (3000,), p13, (5, 0x2000))
    out["hasted_pandemic"] = {
        "question": "ATTR5 hasted period (ModCastingSpeed 0.8 -> period 2400) + ATTR13 refreshed at 14 s",
        "timeline": strip(timeline(hasted, [(0, HitInputs(caster=Caster(mod_casting_speed=0.8))),
                                            (14000, HitInputs(caster=Caster(mod_casting_speed=0.8)))], end=40000, step=100)),
        "evidence": ["trinity-probe"]}
    return out


def _carryover_corpus(ctx) -> dict[str, Any]:
    from .refresh import explain
    c = _counts(ctx)
    rec = _records(c)
    payload = {
        "provenance": _provenance(f"aura_lifecycle.py refresh --carryover --out {CORPUS_DIR}/carryover.json"),
        "populations": c["pop"],
        "formula": {
            "trinity-7f3d43b": "new = min(hit + M, trunc(float(hit)*130f/100f)); M = CalcMaxDuration() at RefreshTimers "
                               "(live caster SpellMod Duration, no power costs); remaining never read",
            "consumer_coords": ["Spell.cpp:3284-3288", "SpellAuras.cpp:976-990", "Unit.cpp:3441"],
            "periodic": "ATTR13 forces resetPeriodicTimer=false: phase kept, ticksDone reset, totalTicks = floor(newMax/period)",
            "history": "e1f345756b (2023) introduced; 92773e207c (2025-03-23) moved it from RefreshTimers to Spell.cpp",
            "simc": "action.cpp:4603 max(r, min(0.3D, r) + D); buff.cpp:1920-1929 D + min(0.3D, r)",
            "core": "aura_state.rs:1143-1199 D + min(r, floor(3D/10)), max with old expiry (navigation)",
        },
        "census": {"pandemic": c["flag_counts"]["ATTR13_PERIODIC_REFRESH_EXTENDS_DURATION"], "carry": c["carry"],
                   "pandemic_without_periodic": c["pandemic_nonperiodic"],
                   "rolling": c["flag_counts"]["ATTR10_ROLLING_PERIODIC"], "evidence": "db2-fact",
                   "count_unit": "provider spells (DIFFICULTY_NONE)"},
        "pandemic_population": {
            "counts": c["pandemic_population"],
            "unit": "provider spells (DIFFICULTY_NONE) with SPELL_ATTR13_PERIODIC_REFRESH_EXTENDS_DURATION",
            "keys": {
                "attr13_total": "all ATTR13 providers",
                "finite / non_positive_duration": "unmodded SpellInfo::GetDuration() > 0 vs <= 0 (Spell.cpp:3268 gate)",
                "channel": "channeled ATTR13 providers (own recast = cancel-then-create, AL-R-B-15)",
                "new_object": "multislot (Passive) ATTR13 providers: never refreshed",
                "unique_non_stacking": "ATTR1/ATTR5 unique, StackAmount 0: no RefreshTimers",
                "carry:pandemic-reads-refreshed-duration": "Spell hit path, timer refresh, finite, not self-channel "
                                                          "-> AL-D-B-01 formula (cite this for the report's pandemic count)",
                "carry:pandemic-reads-live-remaining": "unique non-stacking, finite, not self-channel",
                "carry:n/a (non-positive duration: Spell.cpp:3268 gate)": "never reaches the branch",
                "carry:none": "self-channel or multislot",
            },
            "note": "classifier-derived (restates the probe finding per spell; not independent evidence, R1-04)",
            "evidence": ["db2-fact", "trinity-probe"]},
        "timelines": carryover_timelines(),
        "cross_track": {
            "AL-D-D-02": "same defect as AL-D-B-01 (B adds the M / combo-point / unique refinements, AL-F-B-09)",
            "AL-D-D-04": "same as AL-F-B-08 (tick budget restarts, phase kept -> dropped tick / idle tail); timelines AL-T-D-07, AL-T-D-19",
            "TL-K-01": "confirmed by the verbatim probe: pinned Trinity 550 ms vs Core 500 ms (AL-K-B-04, timeline k_tl_01_250ms_refresh_at_225)",
        },
        "rolling_periodic": {
            "formula": "amount = CalcValue + oldEstimated * (oldTotal - ticksDone) / oldTotal; then *= stacks; round",
            "coords": "SpellAuraEffects.cpp:829-840 (called via SpellAuras.cpp:1117 before RefreshTimers)",
            "witnesses": [explain(ctx, s)["classification"] | {"spell": s, "name": ctx.name(s)} for s in (212431, 217200, 262115, 383346)],
        },
    }
    for k in ("rules", "falsification", "trinity_defects", "unknowns", "retail_experiments", "core_navigation"):
        payload[k] = [e for e in rec[k] if e["id"] in _CARRY_IDS]
    records.validate_corpus(payload)
    return payload


def _duration(args) -> int:
    from . import context
    from .duration import explain
    ctx = context.get()
    emit(explain(ctx, args.spell) if args.spell is not None else _duration_corpus(ctx), args.out)
    return 0


def _refresh(args) -> int:
    from . import context
    from .refresh import explain
    ctx = context.get()
    if args.carryover:
        emit(_carryover_corpus(ctx), args.out)
    else:
        emit(explain(ctx, args.spell) if args.spell is not None else _refresh_corpus(ctx), args.out)
    return 0


COMMANDS = {
    "duration": ("track B: authored duration + Trinity duration pipeline (no spell: corpus)", _add, _duration),
    "refresh": ("track B: reapplication / refresh / pandemic (no spell: corpus; --carryover)", _add_refresh, _refresh),
}
