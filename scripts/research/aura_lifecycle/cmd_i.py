"""Track I commands: ``ordering``, ``numeric``, ``generation``.

Each writes one corpus (``--out``) or prints it.  ``ordering`` and ``numeric`` load the shared
context (~12 s); ``generation`` reads only the TrinityCore checkout.  Timelines come from
:mod:`aura_lifecycle.ordering` (differentially tested against ``tools/tc_aura_order_probe``).
"""

from __future__ import annotations

from typing import Any

from . import PINS, TC_ROOT
from .cli import emit
from .records import provenance, validate_corpus

CORE_AUDIT_NUMERIC = "docs/research/core-audit-corpora/numeric-boundaries.json"


def _add_out(p) -> None:
    p.add_argument("--out")


# --------------------------------------------------------------------------
# cross-cutting records
# --------------------------------------------------------------------------

def _rules(counts: dict[str, Any]) -> list[dict[str, Any]]:
    per = counts.get("periodic_provider_spells", {})
    return [
        {"id": "AL-R-I-01", "name": "tick-before-expiry within the owner update",
         "definition": "every periodic tick due at or before an owner update (bounded by floor(maxDuration/period)) fires "
                       "in that update before any aura of the same owner is expire-removed",
         "population": {"periodic_provider_spells": per}, "counterexamples": [],
         "status": "trinity-only", "evidence": ["trinity-consumer", "trinity-probe", "differential"]},
        {"id": "AL-R-I-02", "name": "cross-unit same-ms order is update order",
         "definition": "a refresh, dispel, stack change or cast completion at the same world tick as an owner's tick/expiry "
                       "precedes it iff it is delivered in the session phase or by a unit updated before the owner "
                       "(players, then creatures in each player's cells)",
         "population": "every unit-owned aura (structural)", "counterexamples": [],
         "status": "trinity-only", "evidence": ["trinity-consumer"]},
        {"id": "AL-R-I-03", "name": "owner storage order decides same-ms death and same-pass creation",
         "definition": "within one owner, m_ownedAuras SpellId order decides which same-ms ticks precede a tick-caused "
                       "death and whether an aura created during the pass is decremented in it (death: only for auras the death sweep removes; passive / ATTR3_ALLOW_AURA_WHILE_DEAD auras still tick)",
         "population": "every owner with >= 2 owned auras (structural)", "counterexamples": [],
         "status": "trinity-only", "evidence": ["trinity-consumer", "trinity-probe", "differential"]},
        {"id": "AL-R-I-04", "name": "early credit only when the application precedes the owner's update",
         "definition": "lifecycle events happen at world-tick boundaries; an application made before the owner's update in a "
                       "tick (session phase, the owner's own events, or a unit updated earlier) is decremented by that tick's "
                       "full diff (Track D's early credit, AL-D-D-03); one made by a unit updated after the owner gets no "
                       "decrement until the next tick, so the same aura lives one diff longer",
         "population": "every aura (structural)", "counterexamples": [],
         "status": "trinity-only", "evidence": ["trinity-consumer"]},
        {"id": "AL-R-I-08", "name": "binary32 products keep durations exact below 2^24 ms",
         "definition": "int32 duration x float factor is exact at factor 1.0 for |d| <= 2^24; above, only binary32-representable "
                       "durations survive",
         "population": {"durations_over_2^24": counts.get("dur_over", {}), "inexact": counts.get("dur_inexact", {})},
         "counterexamples": [], "status": "holds-on-census", "evidence": ["db2-fact", "trinity-consumer"]},
        {"id": "AL-R-I-09", "name": "removal ends the generation; refresh keeps the object",
         "definition": "a reapplication after removal creates a new aura object with fresh timers/stacks/charges; a "
                       "refresh of a found aura mutates it in place",
         "population": "every unit-owned aura (structural)", "counterexamples": [],
         "status": "trinity-only", "evidence": ["trinity-consumer", "trinity-probe", "differential"]},
        {"id": "AL-R-I-10", "name": "child spells hold value copies",
         "definition": "a triggered spell never dereferences its triggering AuraEffect after Spell::prepare",
         "population": "every aura-triggered cast (structural)", "counterexamples": [],
         "status": "trinity-only", "evidence": ["trinity-consumer"]},
    ]


FALSIFICATION = [
    {"id": "AL-F-I-01", "rule": "an aura created during an owner's update pass is updated in that pass iff its SpellId "
     "sorts after the ticking aura", "attempt": "probe scenario trigger-insert-before-next (A=100 ticks, creates B=200, no "
     "later neighbour)", "result": "B not decremented in that pass: m_auraUpdateIterator was already end()",
     "action": "refined to 'iff inserted after the already-advanced iterator (after the next aura)' (AL-R-I-03)"},
    {"id": "AL-F-I-02", "rule": "Trinity's ATTR13 commit is min(new + remaining, 130% new) (core-audit NUM-E-002 wording)",
     "attempt": "verbatim ModStackAmount/RefreshTimers/RefreshDuration + Spell.cpp:3260-3298 in the probe, refresh with "
     "2000 ms of 10000 ms remaining", "result": "commit 13000 (=130%), not 12000: GetDuration() already returns the "
     "refreshed CalcMaxDuration", "action": "rule discarded for the refreshing branch; kept only for the unique branch; "
     "same defect as AL-D-B-01 / AL-D-D-02 (independent probe confirmation)"},
    {"id": "AL-F-I-03", "rule": "death at a timestamp cancels every other tick of that timestamp on the dead unit",
     "attempt": "death-higher-id-kills (killing tick on the higher SpellId)", "result": "the lower-SpellId aura ticked first",
     "action": "refined to storage order (AL-R-I-03)"},
    {"id": "AL-F-I-04", "rule": "the tick due at the expiry millisecond is lost", "attempt": "tick-vs-expiry, catch-up-large-diff",
     "result": "the tick fires, then expiry removal", "action": "rule discarded; AL-R-I-01 kept"},
    {"id": "AL-F-I-05", "rule": "aura lifetime is its duration measured from the cast instant",
     "attempt": "session-creation-decrement vs late-owner-creation (same 1000 ms aura, diff 400)",
     "result": "effective spans 800 ms vs 1200 ms of world time after the creation tick", "action": "refined to AL-R-I-04"},
    {"id": "AL-F-I-06", "rule": "after a phase-keeping refresh the tick count is floor((newMax + phase) / period)",
     "attempt": "pandemic-tick-cap (base 10000, period 3000, phase 2500)", "result": "4 ticks delivered, 5 timer-due",
     "action": "refined; same finding as AL-D-D-04 / AL-T-D-19 (confirmation)"},
    {"id": "AL-F-I-07", "rule": "a removed-but-not-yet-deleted aura can be found and refreshed in the same tick",
     "attempt": "remove-then-refresh-same-tick", "result": "refresh misses; Create builds generation 2",
     "action": "rule discarded; AL-R-I-09 kept"},
    {"id": "AL-F-I-08", "rule": "every class member holding Aura*/AuraEffect*/AuraApplication* is invalidated before delete",
     "attempt": "header member scan (generation.scan_holders) + reading each invalidation path",
     "result": "AreaTrigger::_aurEff is not cleared when the caster is unreachable at removal",
     "action": "rule refined with one exception (AL-D-I-02)"},
]

UNKNOWNS = [
    {"id": "AL-U-I-01", "subject": "final tick vs expiry at the same ms (Retail)",
     "question": "Does Retail deliver the tick due at the expiry instant of an aura whose duration is a multiple of its period?",
     "known": "Trinity: yes, tick then expire in the same owner update (AL-R-I-01)", "why_unresolved": "no Retail server source",
     "evidence": ["trinity-probe", "retail-unknown"], "coords": ["Unit.cpp:2975-2991", "SpellAuraEffects.cpp:1258-1274"],
     "blocker": "Retail observation", "reopen_condition": "AL-X-I-01 result", "build_skew": False},
    {"id": "AL-U-I-02", "subject": "storage-order dependence (Retail)",
     "question": "Is same-ms tick/death resolution on Retail ordered by anything observable (spell id, application order)?",
     "known": "Trinity orders by SpellId multimap key (AL-R-I-03); this is a storage artefact",
     "why_unresolved": "no Retail server source; same-ms alignment hard to observe",
     "evidence": ["trinity-probe", "retail-unknown"], "coords": ["Unit.cpp:2975-2980", "Unit.cpp:4472-4493"],
     "blocker": "Retail observation", "reopen_condition": "AL-X-I-03 result", "build_skew": False},
    {"id": "AL-U-I-03", "subject": "cross-unit same-ms ordering and quantization (Retail)",
     "question": "At what granularity does Retail order a refresh/dispel against a due tick of another unit's aura, and are "
                 "durations measured from the application instant or from a server tick?",
     "known": "Trinity: world-tick quantized, delivery-phase ordered, previous-tick dated (AL-R-I-02, AL-R-I-04)",
     "why_unresolved": "no Retail server source; server tick rate unknown",
     "evidence": ["trinity-probe", "retail-unknown"], "coords": ["Map.cpp:662-705", "Spell.cpp:3467"],
     "blocker": "Retail observation", "reopen_condition": "AL-X-I-02/AL-X-I-04 timestamps", "build_skew": False},
    {"id": "AL-U-I-04", "subject": "stack/charge storage width (Retail)",
     "question": "Do authored StackAmount values > 255 (158 providers, 0 current-player) wrap on Retail?",
     "known": "Trinity stores uint8 (AL-D-C-01); DB2 authors up to 65000", "why_unresolved": "no Retail server source",
     "evidence": ["db2-fact", "trinity-consumer", "retail-unknown"], "coords": ["SpellAuras.h:410-411"],
     "blocker": "Retail observation of a >255-stack aura", "reopen_condition": "a current-player carrier appears",
     "build_skew": False},
    {"id": "AL-U-I-05", "subject": "pandemic commit value (Retail)",
     "question": "Is the Retail ATTR13 commit min(new + remaining, 1.3 new) (simc, community) or Trinity's runtime 130%?",
     "known": "Trinity runtime: 130% for the refreshing branch (AL-D-B-01, AL-D-D-02); simc action.cpp:4604 carryover",
     "why_unresolved": "Trinity is a consumer oracle with a probable defect; Retail truth needs observation",
     "evidence": ["trinity-probe", "simc-consumer", "retail-unknown"], "coords": ["Spell.cpp:3284-3288", "SpellAuras.cpp:976-992"],
     "blocker": "Retail observation", "reopen_condition": "AL-X-I-02 result", "build_skew": False},
    {"id": "AL-U-I-06", "subject": "per-second periodic cost cadence at large diffs",
     "question": "What does the m_timeCla stall (diff == m_timeCla + 1000) do on Retail and does any current carrier reach it?",
     "known": "Trinity stops charging until RefreshDuration (AL-D-I-05); 1 current-player provider has per-second costs",
     "why_unresolved": "needs a world diff > 1000 ms (server lag) to trigger", "evidence": ["trinity-consumer", "retail-unknown"],
     "coords": ["SpellAuras.cpp:864-872"], "blocker": "none for Trinity; Retail observation otherwise",
     "reopen_condition": "Core models per-second aura costs", "build_skew": False},
]

UNKNOWNS.append(
    {"id": "AL-U-I-07", "subject": "aura time on units nobody updates (Retail)",
     "question": "Do auras on units outside every updated cell keep counting down on Retail, or freeze as in Trinity?",
     "known": "Trinity: a unit updates only when its cell is visited (Map.cpp:695-760); frozen time is never credited back "
              "(ordering.json update_coverage)", "why_unresolved": "no Retail server source",
     "evidence": ["trinity-consumer", "retail-unknown"], "coords": ["Map.cpp:695-760", "GridNotifiers.cpp:283-288"],
     "blocker": "Retail observation", "reopen_condition": "AL-X-I-05 result", "build_skew": False})

EXPERIMENTS = [
    {"id": "AL-X-I-01", "question": "final tick at expiry",
     "models": [{"name": "trinity-tick-then-expire", "prediction": "n = duration/period ticks; last tick logged at the same "
                 "timestamp as, and before, the aura-removed event"},
                {"name": "expiry-first", "prediction": "n - 1 ticks"}],
     "setup": "apply an unhasted DoT with duration an exact multiple of its period to a target dummy; no refresh",
     "observable": "combat-log periodic damage count and SPELL_AURA_REMOVED ordering", "discriminates": "AL-U-I-01",
     "fidelity": "exact", "related": ["AL-R-I-01", "OR-I-01"]},
    {"id": "AL-X-I-02", "question": "pandemic commit",
     "models": [{"name": "trinity-runtime-130", "prediction": "new duration = 1.3 x base at any remaining"},
                {"name": "carryover", "prediction": "new duration = base + min(remaining, 0.3 x base)"}],
     "setup": "refresh an ATTR13 DoT (a current-player periodic pandemic spell) at 10% and at 50% remaining",
     "observable": "aura duration shown right after refresh", "discriminates": "AL-U-I-05",
     "fidelity": "exact", "related": ["AL-D-B-01", "AL-D-D-02", "AL-R-B-07"]},
    {"id": "AL-X-I-03", "question": "same-ms tick of two DoTs on a dying target",
     "models": [{"name": "spellid-order", "prediction": "only DoTs whose spell id sorts below the killing DoT tick"},
                {"name": "all-due-ticks", "prediction": "every DoT due at that ms ticks"}],
     "setup": "two same-period DoTs applied in the same server tick; target health tuned so one tick kills",
     "observable": "periodic damage events at the death timestamp", "discriminates": "AL-U-I-02",
     "fidelity": "insufficient", "related": ["AL-R-I-03", "OR-I-05"]},
    {"id": "AL-X-I-04", "question": "buff expiring at a white swing",
     "models": [{"name": "trinity-auras-first", "prediction": "a self buff expiring at the swing ms does not affect the swing"},
                {"name": "swing-first", "prediction": "the swing still benefits"}],
     "setup": "self damage buff whose expiry aligns with a swing (fixed weapon speed, no haste changes)",
     "observable": "swing damage relative to the buff", "discriminates": "AL-U-I-03",
     "fidelity": "approximate", "related": ["OR-I-06"]},
    {"id": "AL-X-I-05", "question": "aura countdown far from players",
     "models": [{"name": "trinity-freeze", "prediction": "a debuff on a mob left outside every player's update range "
                 "has more remaining time on return than authored minus wall time"},
                {"name": "wall-clock", "prediction": "remaining = authored - elapsed wall time"}],
     "setup": "apply a long debuff to a stationary mob, move far away (beyond visibility), return after a measured interval",
     "observable": "remaining duration on the mob's aura", "discriminates": "AL-U-I-07",
     "fidelity": "approximate", "related": ["AL-U-I-07"]},
]

DEFECTS = [
    {"id": "AL-D-I-02", "coords": ["SpellAuraEffects.cpp:6397-6402", "AreaTrigger.h:263", "Unit.cpp:5503"],
     "description": "HandleCreateAreaTrigger(remove) removes the area trigger only when the caster is reachable; otherwise "
                    "AreaTrigger::_aurEff dangles and Unit::RemoveAreaTrigger(AuraEffect const*) compares by address",
     "lifecycle_effect": "area trigger outlives its aura until its own duration; possible ABA match on a later effect",
     "oracle_behaviour": "catalogued (generation.HOLDERS); not modelled"},
    {"id": "AL-D-I-03", "coords": ["SpellAuras.cpp:1114", "Spell.cpp:3284-3298", "SpellAuraEffects.cpp:936-946"],
     "description": "ATTR13 with ATTR1_AURA_UNIQUE/ATTR5_AURA_UNIQUE_PER_CASTER and StackAmount 0: no RefreshTimers, so "
                    "_ticksDone keeps counting while SetMaxDuration raises the bound only by floor(newMax/period)",
     "lifecycle_effect": "tail of the extended aura has no ticks (scenario pandemic-unique-keeps-remaining: 8000 ms silent)",
     "oracle_behaviour": "reproduced (probe-confirmed); 15 providers (0 current-player)"},
    {"id": "AL-D-I-04", "coords": ["SpellAuraEffects.cpp:995-1013", "SpellAuraEffects.cpp:1250-1262"],
     "description": "the `if (_period)` guard precedes the haste product; int32(period x speed) == 0 keeps m_isPeriodic and "
                    "GetTotalTicks returns 0: permanent auras loop forever in AuraEffect::Update",
     "lifecycle_effect": "latent: no hasted periodic provider with period <= 10 ms in 69497",
     "oracle_behaviour": "numeric.zero_period_outcome; FailClosed nowhere needed"},
    {"id": "AL-D-I-05", "coords": ["SpellAuras.cpp:864-872"],
     "description": "m_timeCla += 1000 - diff can store exactly 0, which disables the per-second cost branch until "
                    "RefreshDuration; a negative value charges once per update with no catch-up",
     "lifecycle_effect": "per-second aura upkeep stops (or under-charges) after a long world diff",
     "oracle_behaviour": "numeric.periodic_cost_timer"},
]

ANSWERS = [
    {"question": "AL-U-F-03 (event vs owner-update ordering; early credit)", "answer":
     "confirmed for session-phase casts, the owner's own events, and casters updated before the owner; NOT for casters "
     "updated after the owner (creature caster on a player owner): those land after the owner's update and get no "
     "decrement in that tick (AL-R-I-04, OR-I-10, OR-I-18; scenario late-owner-creation)", "evidence": ["trinity-consumer"]},
    {"question": "AL-U-F-04 (AreaTrigger UpdateTargetList vs unit aura countdown)", "answer":
     "proven per cell (OR-I-17): players before the cells they activate; non-pet creatures before area triggers of their cell; "
     "pets after them; cross-cell order is cell visiting order", "evidence": ["trinity-consumer"]},
    {"question": "AL-U-B-09 (hit vs update order in one ms; B vs D)", "answer":
     "D is right; B's 'update(t) then hit(t)' holds only for hits delivered after the owner's update (settlement_b_vs_d, "
     "delivery_channels)", "evidence": ["trinity-consumer"]},
    {"question": "UNK-E-003 caster != target ordering", "answer":
     "map update order (OR-I-18); instant packet casts precede every owner update of the tick; hard-cast completion "
     "precedes the owner's tick iff the caster updates first (OR-I-15)", "evidence": ["trinity-consumer"]},
]

CONFIRMATIONS = [
    {"topic": "pandemic commit reads the refreshed duration", "confirms": ["AL-D-B-01", "AL-D-D-02", "AL-R-B-07"],
     "how": "tc_aura_order_probe compiles ModStackAmount/RefreshTimers/RefreshDuration and the Spell.cpp:3260-3298 block "
            "verbatim; scenario pandemic-refresh-reads-refreshed-duration commits 13000 with 2000 ms remaining",
     "adds": "refresh_path_census uses Track B's refresh.classify (counts equal carryover.json census.carry) and adds the "
             "finite-duration, channel (cancel-then-create) and externally touched (overlays.surface_flags) subsets"},
    {"topic": "tick budget after a phase-keeping refresh", "confirms": ["AL-D-D-04", "AL-T-D-19"],
     "how": "scenario pandemic-tick-cap (probe components); numeric NB-I-08 counts refreshed-branch periodic rows where "
            "newMax = min(hit + M, trunc(1.3f*hit)) (1..5 CP for combo-point records) leaves a remainder, unhasted, "
            "channels excluded",
     "adds": "AL-D-I-03: the unique branch (no ResetTicks) starves instead"},
    {"topic": "early credit of a fresh application", "confirms": ["AL-D-D-03", "AL-T-D-10"],
     "how": "scenarios session-creation-decrement vs late-owner-creation",
     "adds": "AL-R-I-04: no early credit when the applying unit is updated after the owner"},
    {"topic": "uint8 stack / charge storage", "confirms": ["AL-D-C-01", "AL-D-C-03"],
     "how": "numeric.stack_after_mod / charges_stored; NB-I-01 / NB-I-02 census", "adds": "counts incl. player+class-skills"},
]

CORE_NAVIGATION = [
    {"id": "AL-K-I-01", "topic": "same-timestamp tie-break", "core_coords": ["crates/sim/src/scheduler/mod.rs:190"],
     "research_ref": ["OR-I-01", "OR-I-05", "AL-R-I-02", "AL-R-I-03"],
     "observation": "Core's scheduler breaks timestamp ties by priority then insertion order; Trinity resolves them by "
                    "world phase / unit update order and owner SpellId storage order, with tick-before-expiry inside an owner",
     "reopen_condition": "a Core contract claims Trinity parity for final-tick-at-expiry, same-ms death or refresh-vs-tick"},
    {"id": "AL-K-I-02", "topic": "periodic deadline identity", "core_coords": ["crates/combat/src/deadline_queue.rs:8-13",
                                                                                "crates/combat/src/periodic.rs:282-286"],
     "research_ref": ["GN-I-03", "AL-R-I-09"],
     "observation": "Core deadlines carry (sequence, at, slot) with a canonical schedule-slot tie-break; Trinity has no "
                    "queued aura deadlines (polled timers per object), so stale deadlines cannot exist there by construction",
     "reopen_condition": "Core reuses a slot across aura generations"},
    {"id": "AL-K-I-03", "topic": "pandemic numeric record", "core_coords": [CORE_AUDIT_NUMERIC + " NUM-E-002"],
     "research_ref": ["AL-D-B-01", "AL-D-D-02", "AL-F-I-02"],
     "observation": "NUM-E-002 compares Core with the formula text at Spell.cpp:3284-3287 reading it as remaining duration; "
                    "at runtime Trinity reads the refreshed duration",
     "reopen_condition": "Core audit numeric records are regenerated"},
]


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------

def _ctx_counts(ctx) -> tuple[dict, list[dict], dict]:
    from . import numeric, ordering, overlays, providers
    pops = providers.populations(ctx, with_class_skills=True)
    pe = providers.provider_effects(ctx.data)
    rows = numeric.census(ctx.data, pops, pe, ctx.name, ctx.is_skew)
    rp = ordering.refresh_path_census(ctx.data, pops, pe, overlays.surface_flags(ctx))
    periodic = {r["spell"] for r in pe if r["aura"] in numeric.PERIODIC_AURAS}
    by = {r["id"]: r for r in rows}
    counts = {
        "periodic_provider_spells": {k: len(periodic & v) for k, v in sorted(pops.items())},
        "tick_cap": by["NB-I-08"]["counts"], "stack_over": by["NB-I-01"]["counts"],
        "charges_over": by["NB-I-02"]["counts"], "dur_over": by["NB-I-03"]["counts"],
        "dur_inexact": by["NB-I-03"]["counts_inexact_in_binary32"],
        "populations": {k: len(v) for k, v in sorted(pops.items())},
    }
    return counts, rows, rp


def _ordering(args) -> int:
    from . import context, ordering
    ctx = context.get()
    counts, _, rp = _ctx_counts(ctx)
    from . import providers
    survivors = ordering.death_survivor_census(ctx.data, providers.populations(ctx, with_class_skills=True),
                                               providers.provider_effects(ctx.data))
    results = ordering.run_scenarios()
    payload = {
        "provenance": provenance("aura_lifecycle.py ordering --out docs/research/aura-lifecycle-corpora/ordering.json",
                                 probe="scripts/research/tools/tc_aura_order_probe",
                                 populations=counts["populations"]),
        "scheduler": ordering.verify_anchors(TC_ROOT),
        "pairs": ordering.pair_census(results),
        "delivery_channels": list(ordering.DELIVERY),
        "settlement_b_vs_d": ordering.SETTLEMENT_B_D,
        "scenarios": [{"name": n, "script": sc["script"], "question": sc["question"], "rules_out": sc["rules_out"],
                       "timeline": ordering.compact(results[n], include_swings=n == "late-owner-creation"),
                       "final": results[n]["auras"]}
                      for n, sc in sorted(ordering.SCENARIOS.items())],
        "refresh_path_census": rp,
        "death_survivor_census": survivors,
        "update_coverage": list(ordering.UPDATE_COVERAGE),
        "rules": [r for r in _rules(counts) if r["id"] in ("AL-R-I-01", "AL-R-I-02", "AL-R-I-03", "AL-R-I-04")],
        "falsification": [f for f in FALSIFICATION if f["id"] in ("AL-F-I-01", "AL-F-I-02", "AL-F-I-03", "AL-F-I-04",
                                                                  "AL-F-I-05", "AL-F-I-06")],
        "unknowns": [u for u in UNKNOWNS if u["id"] in ("AL-U-I-01", "AL-U-I-02", "AL-U-I-03", "AL-U-I-05", "AL-U-I-07")],
        "retail_experiments": EXPERIMENTS,
        "answers": ANSWERS,
        "trinity_defects": [d for d in DEFECTS if d["id"] == "AL-D-I-03"],
        "confirmations": CONFIRMATIONS,
        "core_navigation": CORE_NAVIGATION,
        "dependency_edges": [
            {"track": "B", "topic": "pandemic formula / RefreshTimers", "refs": ["AL-D-I-03", "OR-I-08", "confirmations"]},
            {"track": "D", "topic": "tick bound, EXTRA_INITIAL_PERIOD, hasted period", "refs": ["NB-I-07", "NB-I-08", "NB-I-09"]},
            {"track": "E", "topic": "death removal content (which auras survive)", "refs": ["OR-I-05"]},
            {"track": "F", "topic": "target map before ticks", "refs": ["OR-I-13"]},
            {"track": "C", "topic": "stack amount recalculation before tick", "refs": ["OR-I-12", "NB-I-01"]},
        ],
    }
    validate_corpus(payload)
    emit(payload, args.out)
    return 0


def _numeric(args) -> int:
    from . import context, numeric
    ctx = context.get()
    counts, rows, _ = _ctx_counts(ctx)
    payload = {
        "provenance": provenance("aura_lifecycle.py numeric --out docs/research/aura-lifecycle-corpora/numeric-boundaries.json",
                                 populations=counts["populations"], complements=CORE_AUDIT_NUMERIC),
        "records": rows,
        "cited_not_duplicated": [
            {"ref": "NUM-E-001 / NUM-R3-002 / NUM-X-001 / CSA-J-02", "topic": "hasted periodic period int32(period x binary32 speed) vs Core division"},
            {"ref": "NUM-E-002", "topic": "pandemic cap arithmetic (see AL-K-I-03: runtime reads the refreshed duration)"},
            {"ref": "NUM-E-003", "topic": "Core terminal partial tick (Trinity: none; NB-I-07)"},
            {"ref": "NUM-E-005", "topic": "ATTR8 aligned duration max(1, d / hasted_period) x hasted_period"},
            {"ref": "NUM-D-004 / NUM-D-005 / NUM-J-026", "topic": "periodic tick amounts (Tracks C/D)"},
        ],
        "arithmetic_witnesses": {
            "hasted_period_3000_at_1.3": numeric.hasted_period(3000, 1 / 1.3),
            "pct130_of_10000": numeric.calculate_pct_int(10000, 130),
            "duration_16777217_times_1.0f": numeric.int_times_float(16777217, 1.0),
            "stack_255_plus_1_max_300": numeric.stack_after_mod(255, 1, 300, 300),
            "timecla_500_diff_1500": numeric.periodic_cost_timer(500, 1500),
            "tick_cap_13000_3000_phase_2500": numeric.tick_cap_loss(13000, 3000, 2500),
        },
        "rules": [r for r in _rules(counts) if r["id"] == "AL-R-I-08"],
        "unknowns": [u for u in UNKNOWNS if u["id"] in ("AL-U-I-04", "AL-U-I-06")],
        "trinity_defects": [d for d in DEFECTS if d["id"] in ("AL-D-I-04", "AL-D-I-05")],
        "confirmations": [c for c in CONFIRMATIONS if c["topic"].startswith("uint8")],
    }
    validate_corpus(payload)
    emit(payload, args.out)
    return 0


def _generation(args) -> int:
    from . import generation, ordering
    results = ordering.run_scenarios([s for f in generation.FACTS for s in f["scenarios"]])
    facts = []
    for f in generation.FACTS:
        row = dict(f)
        row["timelines"] = {s: ordering.compact(results[s]) for s in f["scenarios"]}
        facts.append(row)
    payload = {
        "provenance": provenance("aura_lifecycle.py generation --out docs/research/aura-lifecycle-corpora/generation.json",
                                 trinity_commit=PINS["trinity_commit"]),
        "facts": facts,
        "holders": generation.scan_holders(TC_ROOT),
        "is_removed_sites": generation.count_is_removed(TC_ROOT),
        "rules": [r for r in _rules({}) if r["id"] in ("AL-R-I-09", "AL-R-I-10")],
        "falsification": [f for f in FALSIFICATION if f["id"] in ("AL-F-I-07", "AL-F-I-08")],
        "trinity_defects": [d for d in DEFECTS if d["id"] == "AL-D-I-02"],
    }
    validate_corpus(payload)
    emit(payload, args.out)
    return 0


COMMANDS = {
    "ordering": ("Track I: same-timestamp ordering census + probe-backed timelines", _add_out, _ordering),
    "numeric": ("Track I: lifecycle numeric boundaries census", _add_out, _numeric),
    "generation": ("Track I: generation safety (holders, facts, timelines)", _add_out, _generation),
}
