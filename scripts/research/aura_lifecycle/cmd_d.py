"""Track D commands: ``periodic <spell>``, ``snapshot <spell>``, ``periodic-timeline``, ``d-corpora``."""

from __future__ import annotations

import json

from .cli import emit


def _add_spell(p) -> None:
    p.add_argument("spell", type=int)
    p.add_argument("--drift", action="store_true", help="read the dbc-resolver drift snapshot instead")
    p.add_argument("--out")


def _periodic(args) -> int:
    from . import FailClosed, context
    from .periodic import profile, script_facts
    ctx = context.get()
    data = ctx.drift if args.drift else ctx.data
    if data is None:
        raise FailClosed("drift snapshot not fetched (tools/fetch_dbc_release.py)")
    out = profile(ctx, args.spell, data)
    out["scripts"] = script_facts(ctx, args.spell)
    emit(out, args.out)
    return 0


def _snapshot(args) -> int:
    from . import FailClosed, context
    from .periodic import PERIODIC_AURAS, WEAPON_PERCENT_DAMAGE, profile
    from .snapshot import FAMILIES, MATRIX
    from procs.enums import aura
    ctx = context.get()
    data = ctx.drift if args.drift else ctx.data
    if data is None:
        raise FailClosed("drift snapshot not fetched (tools/fetch_dbc_release.py)")
    prof = profile(ctx, args.spell, data)
    fam_of = {aura(n): fam for fam, names in FAMILIES.items() for n in names}
    effects = []
    for e in prof["effects"]:
        if e["aura"] not in PERIODIC_AURAS and e["aura"] != WEAPON_PERCENT_DAMAGE:
            continue
        fam = fam_of.get(e["aura"])
        if fam is None:
            raise FailClosed(f"aura {e['aura_name']} has no snapshot family")
        rows = [r for r in MATRIX if r["family"] in (fam, "all-periodic")]
        effects.append({"index": e["index"], "aura_name": e["aura_name"], "family": fam,
                        "compute_points_only_at_cast": e["compute_points_only_at_cast"], "inputs": rows})
    if not effects:
        raise FailClosed(f"spell {args.spell} has no periodic aura effect")
    emit({"spell": args.spell, "name": prof["name"], "build_skew": prof["build_skew"],
          "attributes": prof["attributes"], "effects": effects}, args.out)
    return 0


def _timeline(args) -> int:
    from .periodic import run
    from .snapshot import tick_amounts
    scenario = json.loads(open(args.scenario, encoding="utf-8").read())
    emit(tick_amounts(scenario) if "amount" in scenario else run(scenario), args.out)
    return 0


def _corpora(args) -> int:
    from . import CORPORA, context
    from .cli import write_json
    from pathlib import Path
    out_dir = Path(args.out_dir) if args.out_dir else CORPORA
    ctx = context.get()
    write_json(out_dir / "periodic.json", periodic_corpus(ctx))
    write_json(out_dir / "snapshot-matrix.json", snapshot_corpus(ctx))
    return 0


def periodic_corpus(ctx) -> dict:
    from . import records
    from .periodic import DELIVERY_PATHS, WITNESS_SPELLS, drift_rows, modify_stacks_rows, script_rows, timelines
    from .periodic import census, profile
    payload = {
        "provenance": records.provenance("aura_lifecycle.py d-corpora"),
        "census": census(ctx),
        "census_unit": "provider effects (providers.provider_effects, DIFFICULTY_NONE) and distinct spells; populations all/player/controlled",
        "witnesses": [profile(ctx, s) for s in WITNESS_SPELLS],
        "timelines": timelines(),
        "scripts": script_rows(ctx),
        "drift": drift_rows(ctx),
        "modify_stacks": modify_stacks_rows(ctx),
        "delivery_paths": DELIVERY_PATHS,
    }
    payload.update(findings(payload["census"]))
    records.validate_corpus(payload)
    return payload


def snapshot_corpus(ctx) -> dict:
    from . import records
    from .snapshot import snapshot_timelines
    from .snapshot import MATRIX
    payload = {
        "provenance": records.provenance("aura_lifecycle.py d-corpora"),
        "matrix": sorted(MATRIX, key=lambda r: (r["family"], r["input"])),
        "matrix_key": "(family, input)",
        "timelines": snapshot_timelines(),
    }
    records.validate_corpus(payload)
    return payload


COMMANDS = {
    "periodic": ("authored periodic profile of one spell (Trinity CalculatePeriodic view)", _add_spell, _periodic),
    "snapshot": ("snapshot-vs-dynamic input timing of one spell's periodic effects", _add_spell, _snapshot),
    "periodic-timeline": ("replay a periodic scenario JSON to an exact ms timeline",
                          lambda p: (p.add_argument("scenario"), p.add_argument("--out")), _timeline),
    "d-corpora": ("regenerate periodic.json and snapshot-matrix.json",
                  lambda p: p.add_argument("--out-dir", default=None), _corpora),
}


# ---------------------------------------------------------------------------
# Track D cross-cutting records (records.REQUIRED shapes)
# ---------------------------------------------------------------------------

def _n(census: dict, pop: str, key: str, unit: str = "effects") -> int:
    return census["counts"].get(pop, {}).get(key, {}).get(unit, 0)


def findings(census: dict) -> dict:
    C = lambda key, pop="player", unit="effects": _n(census, pop, key, unit)  # noqa: E731
    TC, PR, DF = "trinity-consumer", "trinity-probe", "differential"
    # R2-13: the probes verify verbatim COMPONENTS; the call order between them (ModStackAmount -> Spell.cpp duration
    # block, UpdateOwner order, event-before-update) is hand-written in the driver following the cited source.
    GLUE = "probe verifies components only; inter-body call order is driver-glued (probe.cpp driver) after the cited source"
    rules = [
        {"id": "AL-R-D-01", "name": "periodic-set",
         "definition": "An aura effect ticks in Trinity iff its aura type is one of the 14 CalculatePeriodic types "
                       "(SpellAuraEffects.cpp:966-981) and the (hasted) period is nonzero; a DoEffectCalcPeriodic script may override.",
         "population": {"all": C("trinity_periodic", "all"), "player": C("trinity_periodic"), "unit": "provider effects"},
         "counterexamples": [f"aura 70 PERIODIC_WEAPON_PERCENT_DAMAGE: dispatched by PeriodicTick but never periodic "
                             f"({C('weapon_percent_damage_never_ticks', 'all')} effects all-pop, 0 player) -> AL-D-D-01",
                             f"periodic type with period 0: {C('periodic_type_zero_period', 'all')} effects all-pop"],
         "status": "holds-on-census", "evidence": [TC, "db2-fact"]},
        {"id": "AL-R-D-02", "name": "period-haste-snapshot",
         "definition": "period = int32(float(ApplyAuraPeriod) * ModCastingSpeed) for raw 173, * ModHaste for raw 278, via "
                       "ModSpellDurationTime (cast speed, only if raw 173/278 present) for channels; computed only in "
                       "CalculatePeriodic (creation, RefreshTimers, DB load) -> application-snapshot + recalculated-on-refresh.",
         "population": {"player": {k: C(f"haste:{k}") for k in ("spell-haste", "channel-cast-speed", "channel-unhasted", "none")},
                        "unit": "periodic provider effects"},
         "counterexamples": ["engine: none; SCRIPTS recompute with live haste: Arcane Tempest spell_item.cpp:4733-4734 "
                             "(ModStackAmount(1, NONE, false) + CalculatePeriodic per stack), zone_zuldrak.cpp:720, "
                             "stratholme.cpp:382, shadowfang_keep.cpp:39; DoEffectCalcPeriodic hooks re-run at every "
                             "CalculatePeriodic (R2-08)",
                             "simc hasted_ticks reschedules on haste change (dot.cpp:1000-1010); Core recomputes after every "
                             "occurrence (AL-K-D-03)"],
         "status": "trinity-only", "evidence": [TC, PR, DF], "probe_scope": GLUE},
        {"id": "AL-R-D-03", "name": "tick-budget",
         "definition": "GetTotalTicks = MaxDuration / period (+1 with raw 169), 0 when permanent; AuraEffect::Update stops at "
                       "the budget; no partial final occurrence; the tail MaxDuration % period is uncovered.",
         "population": {"uncovered_tail": {"all": C("duration:uncovered-tail", "all"), "player": C("duration:uncovered-tail")},
                        "shorter_than_period_no_tick": {"all": C("duration:shorter-than-period-no-tick", "all")},
                        "unit": "periodic provider effects with authored duration"},
         "counterexamples": ["simc models a partial last tick (dot.cpp:660-699); Core 'proportional terminal-partial "
                             "occurrences' (AL-K-D-03)", "AL-T-D-19: budget counted from 0 while the phase is carried",
                             "the budget is re-read from MaxDuration at EVERY AuraEffect::Update (SpellAuraEffects.cpp:1255) "
                             "and is not tied to Duration: script SetDuration-only extensions tick silently (AL-T-D-24, "
                             "AL-D-D-05); SetMaxDuration below the elapsed ticks stops all ticking (AL-T-D-25, AL-D-D-06) (R2-05)",
                             "the budget is not the only early stop: a periodic-trigger tick whose triggered cast fails CheckCast "
                             "sets the aura duration to 0 -> EXPIRE in the same owner update (Spell.cpp:3503-3507, R2-10; Track E)"],
         "status": "trinity-only", "evidence": [TC, PR, DF], "probe_scope": GLUE},
        {"id": "AL-R-D-04", "name": "final-tick-before-expiry",
         "definition": "An occurrence due in the owner update in which duration reaches 0 fires; expiry removal runs after "
                       "all owned auras updated (Unit.cpp:2975-2991).",
         "population": "every non-permanent periodic aura", "counterexamples": [],
         "status": "holds-on-census", "evidence": [TC, PR, DF], "probe_scope": GLUE},
        {"id": "AL-R-D-05", "name": "refresh-cadence-by-delivery-path",
         "definition": "Cadence on a same-caster refresh/stack is a property of (spell, delivery path, cast trigger flags). "
                       "SPELL-HIT path (Spell::DoSpellEffectHit, Spell.cpp:3240): restart iff StackAmount<2 AND the cast lacks "
                       "TRIGGERED_DONT_RESET_PERIODIC_TIMER (included in TRIGGERED_FULL_MASK) AND not raw 436. EVERY OTHER "
                       "path passes the default resetPeriodicTimer=true: Unit::AddAura (Unit.cpp:12311, AuraCreateInfo "
                       "default SpellAuras.h:135; spell_linked_spell LINK_AURA, scripts), effect 289 MODIFY_AURA_STACKS "
                       "(SpellEffects.cpp:6138), HandleAuraLinked stack sync (SpellAuraEffects.cpp:5358), spell steal "
                       "(Unit.cpp:4068), AuraScript ModStackAmount default (SpellScript.cpp:1183) -> restart whatever the "
                       "StackAmount unless raw 436 (SpellAuras.cpp:981-985). Always ticks_done=0 and period re-hasted "
                       "(R1-05, R2-04).",
         "population": {"spell_hit_path": {"by_trigger_flags": C("cadence:by-trigger-flags"),
                                           "preserve_pandemic": C("cadence:preserve-pandemic"),
                                           "preserve_stackable": C("cadence:preserve-stackable"),
                                           "unit": "player periodic provider effects"},
                        "effect_289": "see modify_stacks.summary (18 all-population rows add stacks to stacking periodic "
                                      "auras -> restart; 0 player)"},
         "counterexamples": ["AL-T-D-21/22/23: a 2-cap / 5-cap DoT restarts via AddAura or effect 289 while the Spell-hit "
                             "path keeps it (AL-T-D-13)", "simc never restarts (dot.cpp:940-969)",
                             "Core carries an authored per-action cadence (AL-K-D-01)"],
         "status": "refined", "evidence": [TC, PR, DF], "probe_scope": GLUE},
        {"id": "AL-R-D-06", "name": "raw169-restart-extra-occurrence",
         "definition": "With raw 169 a restart (creation, a restarting Spell-hit reapply, or ANY non-hit refresh path of "
                       "AL-R-D-05 on a non-pandemic aura) sets timer=period, so an occurrence fires at the next owner update "
                       "and the budget gains one; preserve-reapply adds none.",
         "population": {"restart_possible": {"all": C("unk_e_002_raw169_restart_extra_tick_possible", "all"),
                                             "player": C("unk_e_002_raw169_restart_extra_tick_possible")},
                        "unit": "raw-169 non-pandemic StackAmount<2 periodic effects"},
         "counterexamples": ["simc tick_on_application only on start (dot.cpp:972-975); Core catalog text (AL-K-D-02)"],
         "status": "trinity-only", "evidence": [TC, PR, DF], "probe_scope": GLUE},
        {"id": "AL-R-D-07", "name": "owner-update-quantization",
         "definition": "Duration and periodic timers advance only in the owner's _UpdateSpells by the owner's whole diff; "
                       "an aura created (or restarted) between owner updates is credited the full next diff; several "
                       "due occurrences in one update fire back-to-back with identical state (catch-up, capped by budget). "
                       "CAVEAT (R2-12): a unit updates only when its grid cell is visited (near a player, an active non-player "
                       "object, or the special caster/summon visits: Map.cpp:695-760, ObjectUpdater::Visit "
                       "GridNotifiers.cpp:283-287); auras on unvisited units FREEZE (no countdown, no occurrences) and the "
                       "lost time is never credited, since each diff is per map tick. Cf. Track I OR-I-03/04/10, AL-U-I-03.",
         "population": "every periodic aura", "counterexamples": ["freeze of unvisited owners (AL-U-D-08)"],
         "status": "trinity-only", "evidence": [TC, PR, DF], "probe_scope": GLUE},
        {"id": "AL-R-D-08", "name": "amount-snapshot-bonus-live",
         "definition": "Periodic damage/heal/leech: base amount (CalcValue, stacks) captured by CalculateAmount at creation, "
                       "reapply/stack change and explicit RecalculateAmount; caster done bonuses (SP/AP, versatility, %done, "
                       "PeriodicHealingAndDamage), target taken mods and crit roll (spell-mod OWNER's chance: a pet caster "
                       "uses its owner's) are read at every tick. Mastery (ATTR8_MASTERY_AFFECTS_POINTS) is in the base "
                       "amount: snapshotted for auras owned by others (every DoT/HoT on another unit), recalculated only "
                       "for auras the caster owns on itself (StatSystem.cpp:557-561) (R2-09).",
         "population": {"player": {k: C(f"aura:SPELL_AURA_{k}") for k in ("PERIODIC_DAMAGE", "PERIODIC_HEAL", "PERIODIC_LEECH")},
                        "unit": "periodic provider effects"},
         "counterexamples": [f"effect attribute 0x8000 ComputePointsOnlyAtCastTime is NYI in Trinity: "
                             f"{C('effattr_compute_points_only_at_cast', 'all')} all-pop / {C('effattr_compute_points_only_at_cast')} player "
                             "periodic effects (22842:0) where simc snapshots player-scoped inputs"],
         "status": "trinity-only", "evidence": [TC, "simc-consumer", "structural-inference"]},
        {"id": "AL-R-D-09", "name": "same-ms-ordering",
         "definition": "Within one owner: its own spell events (cast completion, hits) -> each owned aura in SpellId order "
                       "(duration, target map, effect ticks) -> expiry removals. Across units: map update order.",
         "population": "every owner", "counterexamples": [],
         "status": "trinity-only", "evidence": [TC, PR], "probe_scope": GLUE},
    ]
    falsification = [
        {"id": "AL-F-D-01", "rule": "AL-R-D-05", "attempt": "treat subtype 23/24 preserve-vs-restart as a per-spell DB2 property (UNK-E-001)",
         "result": "killed: AL-T-D-05/06 show the same spell restarts or preserves by the reapplying cast's trigger flags", "action": "rule reformulated on (spell, cast) pair"},
        {"id": "AL-F-D-02", "rule": "AL-R-D-06", "attempt": "raw 169 gives an extra occurrence only on first application (simc/Core)",
         "result": "false for Trinity (AL-T-D-03, probe-confirmed); true for pandemic/stackable/triggered reapply (AL-T-D-04)", "action": "kept as Trinity-only rule + Retail experiment AL-X-D-01"},
        {"id": "AL-F-D-03", "rule": "AL-R-D-04", "attempt": "expiry removes the aura before the tick due at the same ms",
         "result": "false (AL-T-D-01; Unit.cpp:2984 after all UpdateOwner calls)", "action": "rule kept"},
        {"id": "AL-F-D-04", "rule": "AL-R-D-01", "attempt": "every aura type handled in PeriodicTick ticks",
         "result": "false: aura 70 never gets m_isPeriodic", "action": "defect AL-D-D-01"},
        {"id": "AL-F-D-05", "rule": "AL-R-D-07", "attempt": "occurrences at exactly application + k*period",
         "result": "false: AL-T-D-10 (created at 50 ms, ticks at 3000) and AL-T-D-17 (restart at 6000 ticks at 8900)", "action": "quantization rule AL-R-D-07"},
        {"id": "AL-F-D-06", "rule": "AL-R-D-02", "attempt": "a haste change reschedules the pending occurrence",
         "result": "false: AL-T-D-14 period stays 3000 until reapply", "action": "rule kept (Trinity-only)"},
        {"id": "AL-F-D-07", "rule": "AL-R-D-02", "attempt": "raw 278 on a channel uses melee haste",
         "result": "false: channels take ModSpellDurationTime (ModCastingSpeed, Object.cpp:1838) whenever 173 or 278 is set", "action": "profile haste_mode 'channel-cast-speed'"},
        {"id": "AL-F-D-08", "rule": "AL-R-D-03", "attempt": "pandemic refresh keeps every period boundary inside the new duration",
         "result": "false: AL-T-D-19 drops the 24000 occurrence (budget 5 < 6 boundaries)", "action": "defect AL-D-D-04"},
        {"id": "AL-F-D-09", "rule": "AL-R-D-08", "attempt": "DoTs snapshot caster spell power at application",
         "result": "false for Trinity (tick handler calls SpellDamageBonusDone) and simc default (update_flags |= snapshot_flags); snapshot timeline S-01", "action": "rule kept"},
        {"id": "AL-F-D-10", "rule": "AL-R-D-08", "attempt": "ComputePointsOnlyAtCastTime (0x8000) is honoured by Trinity",
         "result": "false: DBCEnums.h:2418 /*NYI*/, no reader in src/server", "action": "known divergence vs simc; unknown AL-U-D-06"},
        {"id": "AL-F-D-11", "rule": "AL-R-D-03", "attempt": "EffectAmplitude is the tick interval",
         "result": "false: CalculatePeriodic reads ApplyAuraPeriod (EffectAuraPeriod) only (SpellAuraEffects.cpp:963)", "action": "confirms remaining-data-audit/08"},
        {"id": "AL-F-D-13", "rule": "AL-R-D-05", "attempt": "'StackAmount>=2 keeps the phase' as a spell-level rule (R1-05, R2-04)",
         "result": "false outside the Spell-hit path: AddAura / effect 289 / linked / steal / script ModStackAmount default to "
                   "resetPeriodicTimer=true (AL-T-D-21..23, probe)", "action": "rule restated on (spell, delivery path, trigger flags); status refined"},
        {"id": "AL-F-D-14", "rule": "AL-R-D-03", "attempt": "the tick budget follows the aura's remaining duration (R2-05)",
         "result": "false: budget = MaxDuration/period re-read every update, _ticksDone untouched by SetDuration/SetMaxDuration "
                   "(AL-T-D-24 silent tail, AL-T-D-25 all remaining occurrences lost)", "action": "defects AL-D-D-05/06"},
        {"id": "AL-F-D-12", "rule": "AL-R-D-05", "attempt": "pandemic refresh keeps the next occurrence time",
         "result": "false when haste changed: period re-hasted, timer kept -> immediate occurrence (AL-T-D-15, 2 occurrences 400 ms apart)", "action": "rule text says 'period re-hasted'"},
    ]
    unknowns = [
        {"id": "AL-U-D-01", "subject": "UNK-E-001 subtype 23/24 refresh cadence (Retail)",
         "question": "Does Retail restart or preserve the periodic phase of a periodic trigger/energize on same-caster reapply?",
         "known": "Trinity, SPELL-HIT PATH ONLY: restart iff StackAmount<2, not pandemic and the reapplying cast is not fully "
                  "triggered; every non-hit refresh path (AddAura, effect 289, linked, steal, scripts) restarts unless "
                  "pandemic (AL-R-D-05, R2-04); simc: always preserve; no DB2 field of the aura encodes the choice",
         "why_unresolved": "the deciding input in Trinity is a server-side cast flag with no client-data counterpart",
         "evidence": [TC, PR, "retail-unknown"], "coords": ["Spell.cpp:3240", "SpellDefines.h:291-293", "SpellAuras.cpp:976-992"],
         "blocker": "Retail observation", "reopen_condition": "combat-log trigger/energize times around a reapply (AL-X-D-02)", "build_skew": False},
        {"id": "AL-U-D-02", "subject": "UNK-E-002 raw 169 on restart-reapply (Retail)",
         "question": "Does a restart-reapplied raw-169 aura produce an extra immediate occurrence in Retail?",
         "known": "Trinity yes on a restarting Spell-hit reapply (AL-T-D-03) and on every non-hit refresh path of a "
                  "non-pandemic aura (AL-R-D-05/06); simc/Core no", "why_unresolved": "consumers disagree; no Retail log",
         "evidence": [TC, PR, "simc-consumer", "retail-unknown"], "coords": ["SpellAuraEffects.cpp:949-959", "dot.cpp:972-975"],
         "blocker": "Retail observation", "reopen_condition": "AL-X-D-01", "build_skew": False},
        {"id": "AL-U-D-03", "subject": "UNK-E-003 same-ms cast completion vs due occurrence",
         "question": "When a reapplying cast completes at the ms an occurrence is due, which is observed first?",
         "known": "Trinity: owner==caster -> cast first (occurrence dropped on restart, AL-T-D-17); caster!=owner -> map "
                  "update order of the two units (AL-T-D-18), not a spell property",
         "why_unresolved": "Retail server scheduling granularity/order is unobservable at 1 ms; Trinity's cross-unit order is container order",
         "evidence": [TC, PR, "structural-inference", "retail-unknown"], "coords": ["Unit.cpp:423-433", "Object.cpp:245-247", "Spell.cpp:8375-8379", "Unit.cpp:2975-2991"],
         "blocker": "no 1 ms Retail observable", "reopen_condition": "a Retail log with cast-success and periodic events sharing a timestamp", "build_skew": False},
        {"id": "AL-U-D-04", "subject": "partial final occurrence",
         "question": "Does Retail produce a proportional last occurrence for duration % period != 0 (incl. pandemic-extended)?",
         "known": "Trinity no (budget), simc yes (partial), Core yes (terminal-partial)",
         "why_unresolved": "consumer disagreement", "evidence": [TC, "simc-consumer", "retail-unknown"],
         "coords": ["SpellAuraEffects.cpp:936-946", "dot.cpp:660-699"], "blocker": "Retail observation",
         "reopen_condition": "AL-X-D-04", "build_skew": False},
        {"id": "AL-U-D-05", "subject": "haste change during an active periodic",
         "question": "Is the Retail tick interval re-derived from live haste (per occurrence) or fixed at application/refresh?",
         "known": "Trinity fixed until refresh; simc hasted_ticks rescheduled; Core per occurrence",
         "why_unresolved": "three consumers, three models", "evidence": [TC, "simc-consumer", "retail-unknown"],
         "coords": ["SpellAuraEffects.cpp:996-1013", "dot.cpp:1000-1010"], "blocker": "Retail observation",
         "reopen_condition": "AL-X-D-03", "build_skew": False},
        {"id": "AL-U-D-06", "subject": "ComputePointsOnlyAtCastTime (effect attribute 0x8000)",
         "question": "Which periodic inputs does Retail freeze for effects carrying 0x8000?",
         "known": "Trinity NYI (all dynamic); simc freezes player-scoped done inputs",
         "why_unresolved": "no direct consumer implements it", "evidence": ["db2-fact", "simc-consumer", "retail-unknown"],
         "coords": ["DBCEnums.h:2418", "engine/action/action.cpp:2802-2812"], "blocker": "Retail observation",
         "reopen_condition": "AL-X-D-05 on 22842", "build_skew": False},
        {"id": "AL-U-D-07", "subject": "Retail update granularity",
         "question": "Are Retail occurrences quantized to a server update (early credit / catch-up bursts) or scheduled exactly?",
         "known": "Trinity quantized (AL-R-D-07)", "why_unresolved": "server-internal",
         "evidence": [TC, "retail-unknown"], "coords": ["Unit.cpp:2975-2981", "SpellAuras.cpp:855-862"],
         "blocker": "Retail observation", "reopen_condition": "CLEU tick intervals under load", "build_skew": False},
        {"id": "AL-U-D-08", "subject": "owner-update freeze of unvisited units (R2-12)",
         "question": "Do Retail auras on units far from any player keep counting down and ticking in wall-clock time?",
         "known": "Trinity: units update only in visited grid cells (Map.cpp:695-760, GridNotifiers.cpp:283-287); auras on "
                  "unvisited units freeze and the lost time is never credited (diff is per map tick). Cf. OR-I-03/04/10, AL-U-I-03",
         "why_unresolved": "server-internal scheduling; Trinity-only fact", "evidence": [TC, "retail-unknown"],
         "coords": ["Map.cpp:695-760", "GridNotifiers.cpp:283-287", "Unit.cpp:2975-2981"],
         "blocker": "Retail observation", "reopen_condition": "a wall-clock duration claim for owners far from players",
         "build_skew": False},
    ]
    experiments = [
        {"id": "AL-X-D-01", "question": "raw-169 restart-reapply extra occurrence (UNK-E-002)",
         "models": [{"name": "trinity", "prediction": "one occurrence right after the reapply, then every period"},
                    {"name": "simc/core", "prediction": "no extra occurrence; phase preserved"}],
         "setup": "player-cast raw-169, non-pandemic, StackAmount<2 periodic reapplied by the same caster before expiry "
                  "(candidates from periodic.json census 'unk_e_002_raw169_restart_extra_tick_possible')",
         "observable": "CLEU SPELL_PERIODIC_* timestamps around SPELL_AURA_REFRESHED", "discriminates": ["AL-R-D-06"],
         "fidelity": "approximate", "related": ["AL-U-D-02", "UNK-E-002"]},
        {"id": "AL-X-D-02", "question": "periodic trigger (subtype 23) phase after reapply (UNK-E-001)",
         "models": [{"name": "trinity-untriggered", "prediction": "next trigger = reapply + period"},
                    {"name": "preserve", "prediction": "next trigger keeps the old phase"}],
         "setup": "player aura with a periodic trigger effect, reapplied mid-period by a hard cast and (separately) by a proc",
         "observable": "CLEU cast/damage events of the triggered spell", "discriminates": ["AL-R-D-05"],
         "fidelity": "approximate", "related": ["AL-U-D-01", "UNK-E-001"]},
        {"id": "AL-X-D-03", "question": "haste gained mid-DoT changes the pending interval?",
         "models": [{"name": "trinity", "prediction": "interval unchanged until refresh"},
                    {"name": "simc", "prediction": "next interval shortened (rescheduled)"},
                    {"name": "core", "prediction": "interval after the next occurrence shortened"}],
         "setup": "raw-173 DoT (e.g. 589:0) then a large haste buff between two occurrences",
         "observable": "CLEU SPELL_PERIODIC_DAMAGE intervals", "discriminates": ["AL-R-D-02"],
         "fidelity": "approximate", "related": ["AL-U-D-05"]},
        {"id": "AL-X-D-04", "question": "partial final occurrence",
         "models": [{"name": "trinity", "prediction": "no occurrence for the tail"},
                    {"name": "simc/core", "prediction": "a reduced last occurrence"}],
         "setup": "pandemic-refresh a raw-436 DoT so that the new duration is not a multiple of the period",
         "observable": "amount and time of the last SPELL_PERIODIC_DAMAGE before SPELL_AURA_REMOVED",
         "discriminates": ["AL-R-D-03"], "fidelity": "approximate", "related": ["AL-U-D-04"]},
        {"id": "AL-X-D-05", "question": "spell power / versatility gained mid-periodic changes later occurrences?",
         "models": [{"name": "trinity/simc-default", "prediction": "later occurrences scale with the new value"},
                    {"name": "full-snapshot / compute-on-cast", "prediction": "unchanged until reapply"}],
         "setup": "apply 589:0 (no 0x8000) and 22842:0 (0x8000), then gain a stat buff; compare",
         "observable": "SPELL_PERIODIC_* amounts (Midnight combat-log restrictions: see Track L)",
         "discriminates": ["AL-R-D-08"], "fidelity": "insufficient", "related": ["AL-U-D-06"]},
    ]
    defects = [
        {"id": "AL-D-D-01", "coords": ["SpellAuraEffects.cpp:966-981", "SpellAuraEffects.cpp:1334"],
         "description": "SPELL_AURA_PERIODIC_WEAPON_PERCENT_DAMAGE (70) is dispatched by PeriodicTick but not in the "
                        "CalculatePeriodic periodic set, so m_isPeriodic stays false and it never ticks",
         "lifecycle_effect": f"{C('weapon_percent_damage_never_ticks', 'all')} all-pop effects produce no occurrences (0 current-player)",
         "oracle_behaviour": "reproduced (profile note, census key weapon_percent_damage_never_ticks); not corrected"},
        {"id": "AL-D-D-02", "coords": ["Spell.cpp:3284-3288", "SpellAuras.cpp:976-992", "Unit.cpp:3441"],
         "description": "pandemic duration reads HitAura->GetDuration() after ModStackAmount->RefreshTimers->RefreshDuration "
                        "already set it to the full new duration, so min(D+D, 1.3D) = 1.3D regardless of the remaining time "
                        "(for StackAmount>0 or non-unique auras, i.e. whenever RefreshTimers ran)",
         "lifecycle_effect": "every pandemic refresh yields 130% of the base duration; tick budget follows (AL-T-D-07)",
         "oracle_behaviour": "reproduced in periodic._set_spell_duration; Track B owns the duration semantics",
         "evidence": [TC, PR], "probe_scope": "SetDuration/RefreshTimers/ModStackAmount and the Spell.cpp:3264-3298 blocks "
                                              "are verbatim; their order (ModStackAmount before the duration block, as "
                                              "Unit.cpp:3441 inside Spell.cpp:3254) is driver-glued (R2-13)"},
        {"id": "AL-D-D-03", "coords": ["Unit.cpp:2975-2981", "SpellAuras.cpp:855-862", "SpellAuraEffects.cpp:1257"],
         "description": "an aura created/restarted between owner updates is credited the owner's whole next diff "
                        "(time before it existed), shifting duration and every occurrence early by up to one diff",
         "lifecycle_effect": "AL-T-D-10 (50 ms early), AL-T-D-17 (restart at a due ms: next occurrence after period-diff)",
         "oracle_behaviour": "reproduced; CANDIDATE defect / implementation artefact, not a definite defect: no Retail "
                             "observable at sub-update resolution can falsify it (R2 note)",
         "status": "candidate", "evidence": [TC, PR], "probe_scope": GLUE},
        {"id": "AL-D-D-04", "coords": ["SpellAuraEffects.cpp:936-946", "SpellAuraEffects.cpp:1031", "SpellAuras.cpp:976-992"],
         "description": "on a preserve refresh (pandemic/stackable/triggered) _ticksDone is reset and the budget recomputed "
                        "from the new max duration while the phase is kept, so the last period boundary inside the "
                        "new duration can exceed the budget and is dropped; with a shorter re-hasted period the budget "
                        "can be exhausted early leaving an idle tail",
         "lifecycle_effect": "AL-T-D-19 drops the 24000 occurrence; AL-T-D-15 ends ticking ~3 s before expiry",
         "oracle_behaviour": "reproduced", "evidence": [TC, PR], "probe_scope": GLUE},
        {"id": "AL-D-D-05", "coords": ["SpellAuraEffects.cpp:936-947", "SpellAuraEffects.cpp:1255", "spell_priest.cpp:2995-2998"],
         "description": "script duration extensions via SetDuration only (Painful Punishment 390686 on Shadow Word: Pain / "
                        "Purge the Wicked) leave MaxDuration and so the tick budget unchanged: the extension never ticks (R2-05)",
         "lifecycle_effect": "AL-T-D-24: 16 s / 2 s DoT +4000 at 10 s ticks 2000..16000 (8) then lives silently to 20000",
         "oracle_behaviour": "reproduced (periodic op set_duration); probe-checked", "evidence": [TC, "script-consumer", PR],
         "witnesses": ["390686 (effect 0 DUMMY in 69497: the hook binds)"], "build_skew": False, "reference": "R2-05"},
        {"id": "AL-D-D-06", "coords": ["SpellAuraEffects.cpp:936-947", "SpellAuraEffects.cpp:1255", "spell_priest.cpp:2847-2854"],
         "description": "SetMaxDuration(remaining + x) with _ticksDone kept (Mental Decay 375994 on SW:P / Vampiric Touch) "
                        "drops the budget below ticks already done: every remaining occurrence is lost (R2-05)",
         "lifecycle_effect": "AL-T-D-25: +1000 at 10 s -> budget 3 < ticks_done 4 -> no occurrence for the last 7 s",
         "oracle_behaviour": "reproduced (ops set_max_duration + set_duration); probe-checked",
         "evidence": [TC, "script-consumer", PR, "build-skew"],
         "witnesses": ["375994 (hook targets EFFECT_0 SPELL_AURA_DUMMY but effect 0 is aura 108 in 69497: likely unbound)"],
         "build_skew": True, "reference": "R2-05"},
    ]
    core_nav = [
        {"id": "AL-K-D-01", "topic": "cadence on reapply", "core_coords": ["crates/data/src/action/input.rs:1112-1115"],
         "research_ref": ["AL-R-D-05", "UNK-E-001"],
         "observation": "Core carries PreserveCadence/RestartCadence as an authored per-action input; in Trinity the choice "
                        "depends on the reapplying cast's trigger flags, StackAmount and raw 436",
         "reopen_condition": "if Retail AL-X-D-02 distinguishes triggered vs untriggered reapply"},
        {"id": "AL-K-D-02", "topic": "TickOnApplication on active reapply", "core_coords": ["crates/dbc/src/spell_attribute.rs:407"],
         "research_ref": ["AL-R-D-06", "UNK-E-002"],
         "observation": "Core catalog: 'active reapplication never creates another initial occurrence' (simc model); Trinity "
                        "creates one on restart-reapply", "reopen_condition": "AL-X-D-01 outcome"},
        {"id": "AL-K-D-03", "topic": "spell haste period and terminal partial", "core_coords": ["crates/dbc/src/spell_attribute.rs:408"],
         "research_ref": ["AL-R-D-02", "AL-R-D-03"],
         "observation": "Core divides by cast speed and re-derives after each occurrence, with proportional terminal partials; "
                        "Trinity multiplies ModCastingSpeed once at create/refresh and has no partial occurrence",
         "reopen_condition": "AL-X-D-03 / AL-X-D-04 outcomes"},
        {"id": "AL-K-D-04", "topic": "same-timestamp ranks", "core_coords": ["crates/combat/src/state/transition.rs:22-75"],
         "research_ref": ["AL-R-D-04", "AL-R-D-09", "UNK-E-003"],
         "observation": "Core ranks cast(2) < periodic(5) < aura expiry(6): matches Trinity for owner==caster and for "
                        "final-tick-before-expiry; Trinity has no rank for caster!=owner (map order)",
         "reopen_condition": "a Retail same-timestamp observation"},
    ]
    return {"rules": rules, "falsification": falsification, "unknowns": unknowns, "retail_experiments": experiments,
            "trinity_defects": defects, "core_navigation": core_nav}
