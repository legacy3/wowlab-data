"""Track E commands: ``removal``, ``dispel``, ``lifetime``.

``aura_lifecycle.py removal <spell>``            removal sites / modes that can end this spell's auras
``aura_lifecycle.py removal --corpus --out F``   docs/research/aura-lifecycle-corpora/removal.json
``aura_lifecycle.py dispel <spell>``             the spell as dispellable aura and/or dispeller
``aura_lifecycle.py dispel --corpus --out F``    dispel.json
``aura_lifecycle.py lifetime <spell>``           holder / caster / source lifetime classification
``aura_lifecycle.py lifetime --corpus --out F``  lifetime.json (death order, matrix, ms timelines)
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from . import FailClosed, records
from .cli import emit

CORPUS_CMD = "aura_lifecycle.py {cmd} --corpus --out docs/research/aura-lifecycle-corpora/{cmd}.json"

# Current-player witnesses (checked against the player population when the corpus is built).
WITNESSES = {
    33763: "Lifebloom: AfterEffectRemove bloom only on EXPIRE or ENEMY_SPELL (not DEATH / CANCEL / DEFAULT); LIMIT_N: moving it evicts the old one with DEFAULT (RM-38)",
    76856: "Mastery: Unshackled Fury: passive with CasterAuraState ENRAGED -> removed/re-cast on aura-state change (RM-42)",
    212988: "Painbringer: script decays stacks with ModStackAmount(-1, EXPIRE) (writer, spell_dh.cpp:1736)",
    48181: "Haunt: OnEffectRemove branch == DEATH (cooldown reset on holder death)",
    1943: "Rupture: OnEffectRemove returns unless DEATH (Venomous Wounds energy refund)",
    207167: "Blinding Sleet: AfterEffectRemove == EXPIRE",
    343527: "Execution Sentence: AfterEffectRemove != EXPIRE early return",
    974: "Earth Shield: survives death (ATTR3) + ATTR7_DISABLE_AURA_WHILE_DEAD + ATTR1_DISPEL_ALL_STACKS",
    465: "Devotion Aura: non-passive ATTR3_ALLOW_AURA_WHILE_DEAD area aura",
    15407: "Mind Flay: channel -> CANCEL on caster death / interrupt, DEFAULT on range loss",
    774: "Rejuvenation: Magic-dispellable periodic heal (Core refusal shape)",
}


def _ctx_pops():
    from . import context, providers
    ctx = context.get()
    return ctx, providers.populations(ctx)


def _add(p, corpus_help: str) -> None:
    p.add_argument("spell", type=int, nargs="?")
    p.add_argument("--corpus", action="store_true", help=corpus_help)
    p.add_argument("--out")


def _need(args) -> None:
    if args.spell is None and not args.corpus:
        raise FailClosed("give a spell id or --corpus")


# ---------------------------------------------------------------------------
# removal
# ---------------------------------------------------------------------------

def build_removal(ctx, pops) -> dict[str, Any]:
    from . import removal
    bad = removal.verify_anchors()
    if bad:
        raise FailClosed("anchor drift: " + "; ".join(bad))
    rows = removal.script_classes_reading_mode(ctx.bundle)
    classes = [r for r in rows if "class" in r]
    unattributed = next((r["unattributed"] for r in rows if "unattributed" in r), [])
    player = pops["player"]
    mode_classes: Counter = Counter()
    for r in classes:
        for m in r["modes"]:
            mode_classes[m] += 1
    script_summary = {
        "population": "TrinityCore src/server/scripts @ pinned commit",
        "reference_lines": len(removal.script_mode_refs()),
        "classes": len(classes),
        "classes_bound_to_spells": sum(1 for r in classes if r["spells"]),
        "classes_by_mode_named": dict(sorted(mode_classes.items())),
        "classes_testing_DEATH": sum(1 for r in classes if "DEATH" in r["modes"]),
        "spells_bound_all": len({s for r in classes for s in r["spells"]}),
        "spells_bound_player": len({s for r in classes for s in r["spells"] if s in player}),
        "player_spells_by_mode": {m: sorted({s for r in classes if m in r["modes"] for s in r["spells"] if s in player})
                                  for m in sorted(mode_classes)},
        "unattributed_lines": unattributed,
    }
    witnesses = []
    for spell, why in sorted(WITNESSES.items()):
        f = removal.explain(ctx, spell, classes)
        witnesses.append({"spell": spell, "name": f["name"], "why": why, "in_player_population": spell in player,
                          "build_skew": f["build_skew"], "death_class": f["death_class"],
                          "aura_interrupt_flags": f["aura_interrupt_flags"], "dispel_type": f["dispel_type"],
                          "script_mode_readers": [{"class": r["class"], "file": r["file"], "line": r["line"], "modes": r["modes"],
                                                   "comparisons": r["comparisons"]} for r in f["script_mode_readers"]],
                          "evidence": f["evidence"]})
    census = removal.census(ctx, pops, classes)
    writers = removal.script_mode_writers(classes)
    wmodes: Counter = Counter(m for w in writers for m in w["modes"])
    writer_summary = {"population": "TrinityCore src/server/scripts @ pinned commit", "lines": len(writers),
                      "lines_by_mode": dict(sorted(wmodes.items())),
                      "spells_bound_player": sorted({x for w in writers for x in w["spells"] if x in player}),
                      "note": "modes are labels with several producers: EXPIRE also comes from script writers, spell-mod "
                              "charges (Object.cpp:2627/2630), SmartScript (SmartScript.cpp:864) and failed periodic "
                              "trigger casts (RM-47) -- EXPIRE does not imply the duration ran out"}
    dynobj = {}
    for pop, spells in sorted(pops.items()):
        dynobj[pop] = sum(1 for s in spells if any(int(e["Effect"]) == 27 and int(e["EffectAura"]) for e in ctx.data.effects(s)))
    payload = {
        "provenance": records.provenance(CORPUS_CMD.format(cmd="removal")),
        "remove_modes": {str(k): v for k, v in removal.REMOVE_MODES.items()},
        "taxonomy": removal.taxonomy(),
        "engine_mode_branches": [{**b, "coords": removal.coord(b["anchor"])} for b in removal.MODE_BRANCHES],
        "script_mode_readers": sorted(({k: v for k, v in r.items()} for r in classes), key=lambda r: (r["file"], r["line"])),
        "script_summary": script_summary,
        "script_mode_writers": writers,
        "script_writer_summary": writer_summary,
        "interrupt_flag_consumed": removal.interrupt_flag_consumed(),
        "census": census,
        "dynobj_aura_spells": {"population_note": "provider spells with an Effect 27 PERSISTENT_AREA_AURA row, per population",
                                "counts": dict(sorted(dynobj.items()))},
        "witnesses": witnesses,
        **REMOVAL_RECORDS,
    }
    records.validate_corpus(payload)
    return payload


REMOVAL_RECORDS: dict[str, list[dict[str, Any]]] = {
    "rules": [
        {"id": "AL-R-E-01", "name": "removal-never-ticks",
         "definition": "No removal path runs a periodic tick; a tick at the end of an aura's life happens only when the owner's "
                       "update pass reaches the period boundary before the expiry sweep of the same update.",
         "population": "all periodic providers (every removal site RM-01..RM-36)", "counterexamples": [],
         "status": "holds-on-census", "evidence": ["trinity-consumer", "trinity-probe"]},
        {"id": "AL-R-E-02", "name": "expiry-mode-depends-on-owner-kind",
         "definition": "Natural expiry reaches consumers as EXPIRE for unit-owned auras (Unit.cpp:2987) and as DEFAULT for "
                       "dynamic-object auras (DynamicObject.cpp:204); EXPIRE-gated handlers never run for the latter "
                       "(same finding for recipients: track F AL-D-F-03, area-lifecycle.json).",
         "population": "all providers; dynobj subset counted in dynobj_aura_spells", "counterexamples": [],
         "status": "trinity-only", "evidence": ["trinity-consumer"]},
        {"id": "AL-R-E-03", "name": "death-sweep-predicate",
         "definition": "The death sweep removes, on the dying unit only, every applied then owned aura with !IsPassive && "
                       "!ATTR3_ALLOW_AURA_WHILE_DEAD, with mode DEATH. It does not run when ABSORB_OVERKILL skips "
                       "setDeathState (Unit.cpp:1044) -- a Kill without death sweep.",
         "population": "all providers (census.death_class:*)", "counterexamples": [],
         "status": "holds-on-census", "evidence": ["trinity-consumer", "trinity-probe", "db2-fact"]},
        {"id": "AL-R-E-04", "name": "death-is-not-natural-expiry",
         "definition": "Holder death differs from expiry in mode (DEATH vs EXPIRE), final tick (none), EXPIRE-gated handlers and "
                       "scripts (skipped), positive-id spell_linked_spell REMOVE casts (skipped only for DEATH; negative-id removals "
                       "still run, RM-46) and in what ran before it "
                       "(LeavingCombat INTERRUPT, channel CANCEL).  Cross-ref track H (external-policy.json): DEATH-branching "
                       "script handlers and the linked-spell REMOVE exemption.",
         "population": "player witnesses 33763, 48181, 1943 (script branches) + all non-surviving providers",
         "counterexamples": [], "status": "holds-on-census", "evidence": ["trinity-consumer", "script-consumer", "trinity-probe"]},
        {"id": "AL-R-E-05", "name": "mode-is-path-dependent",
         "definition": "The same observable end arrives under different modes by path: last proc charge -> DEFAULT "
                       "(SpellAuras.cpp:1829-1830) but spell-mod charge -> EXPIRE (Object.cpp:2627/2630); absorb depletion and "
                       "dispel both ENEMY_SPELL; area expiry EXPIRE vs dynobj expiry DEFAULT; recipient out of range DEFAULT; "
                       "EXPIRE also from failed periodic trigger casts (RM-47) and script writers (script_mode_writers); "
                       "INTERRUPT also outside interrupt flags (RM-10, RM-41). A mode is a label, not a cause.",
         "population": "taxonomy RM-02, RM-10, RM-14, RM-16, RM-41, RM-47; script_writer_summary", "counterexamples": [], "status": "trinity-only", "evidence": ["trinity-consumer"]},
        {"id": "AL-R-E-06", "name": "stack-loss-keeps-timers",
         "definition": "A stack decrease (dispel, steal, ModStackAmount num<0) never refreshes duration and never resets the "
                       "periodic timer (refresh = new >= old, SpellAuras.cpp:1114); reaching <= 0 removes with the caller's mode.",
         "population": "stacking providers (track C census)", "counterexamples": [], "status": "holds-on-census",
         "evidence": ["trinity-consumer", "trinity-probe"]},
        {"id": "AL-R-E-07", "name": "caster-death-leaves-most-foreign-auras",
         "definition": "Caster death does not remove auras it cast on other holders EXCEPT: channelled auras (CANCEL); "
                       "all dynobjects of the spell it is casting/channelling (DEFAULT, Spell.cpp:3665); the vehicle's "
                       "CONTROL_VEHICLE aura (ExitVehicle, Unit.cpp:12868); charm/possess auras on charmed units "
                       "(RemoveCharmAuras, Unit.cpp:6623); its totems' auras (Totem::UnSummon); area auras it owns (DEATH). "
                       "Despawn/leave-world additionally removes its single-target auras on others (RM-30) and its dynobjects. "
                       "Later ticks read the caster live (dead: found; despawned: nullptr).",
         "population": "all providers", "counterexamples": ["R3-06 dynobjects of the cancelled spell", "R3-07 charm / vehicle auras"],
         "status": "refined", "evidence": ["trinity-consumer"]},
        {"id": "AL-R-E-08", "name": "combat-exit-precedes-death-sweep",
         "definition": "When a unit dies with only PvE combat references, AtExitCombat runs synchronously inside CombatStop before "
                       "RemoveAllAurasOnDeath: LeavingCombat-interrupt auras are removed with INTERRUPT (even death-persistent "
                       "ones) and OnEnterLeaveCombat hooks run with the auras still present.",
         "population": "providers with AuraInterruptFlags LeavingCombat (census aura_interrupt_flag_counts.LeavingCombat)",
         "counterexamples": [], "status": "trinity-only", "evidence": ["trinity-consumer"]},
    ],
    "falsification": [
        {"id": "AL-F-E-01", "rule": "death == natural expiry", "attempt": "TL-E-01 vs TL-E-02 (lifetime.json) and probe death/update runs",
         "result": "3 ticks + DEATH vs 4 ticks + EXPIRE; Lifebloom bloom gated on EXPIRE|ENEMY_SPELL", "action": "discarded; AL-R-E-04"},
        {"id": "AL-F-E-02", "rule": "RemoveAllAurasOnDeath is the only death-time aura removal",
         "attempt": "trace setDeathState call order (Unit.cpp:9167-9179, CombatManager.cpp:416)",
         "result": "CombatStop (LeavingCombat INTERRUPT) and InterruptNonMeleeSpells (channel CANCEL) run first", "action": "discarded; AL-R-E-08"},
        {"id": "AL-F-E-03", "rule": "auras surviving death stay fully applied",
         "attempt": "consumers of ATTR7_DISABLE_AURA_WHILE_DEAD", "result": "UnitAura::FillTargetMap empties the target map while "
         "the owner is dead -> applications unapplied DEFAULT at the next target-map update", "action": "refined; RM-20"},
        {"id": "AL-F-E-04", "rule": "every natural end runs EXPIRE-gated behaviour",
         "attempt": "dynamic-object aura end path", "result": "DynamicObject::RemoveAura uses DEFAULT", "action": "refined; AL-R-E-02"},
        {"id": "AL-F-E-05", "rule": "an aura removed before its tick yields a final or pro-rated tick",
         "attempt": "_UnapplyAura / _Remove bodies (probe) and TL-E-05", "result": "no tick path in removal", "action": "discarded; AL-R-E-01"},
        {"id": "AL-F-E-06", "rule": "area-aura recipients only lose the aura with DEFAULT when the source goes away",
         "attempt": "probe: owner death with a recipient application", "result": "recipient application unapplied with DEATH while alive",
         "action": "refined; lifetime matrix row 'area aura OWNED by the dying unit'"},
        {"id": "AL-F-E-08", "rule": "the 36-row taxonomy lists every removal path", "attempt": "hostile review R3 + R2-10",
         "result": "missing: single-target cap, EXCLUSIVE_HIGHEST displacement / self-removal, mount/vehicle/charm sweeps, aura-state "
                   "loss, save/reload, totem unsummon, spell-cancel dynobject removal, negative linked ids, failed trigger cast",
         "action": "refined: RM-37..47 added; RM-19/25/31/32/34 corrected"},
        {"id": "AL-F-E-09", "rule": "EXPIRE means the duration ran out", "attempt": "R2-10 trigger-cast failure; script writers; spell-mod charges",
         "result": "EXPIRE at 6000 ms with 6000 ms left (TL-E-11); Painbringer/Alter Time scripts write EXPIRE", "action": "discarded; AL-R-E-05"},
        {"id": "AL-F-E-14", "rule": "caster death touches none of the auras it cast on other holders",
         "attempt": "R3-06/07: setDeathState -> Spell::cancel / ExitVehicle / RemoveAllControlled",
         "result": "dynobjects of the cast in progress, vehicle control and charm auras go", "action": "refined; AL-R-E-07"},
        {"id": "AL-F-E-07", "rule": "caster death removes the caster's DoTs",
         "attempt": "search every death-path call for foreign auras; TL-E-07", "result": "none; ticks continue", "action": "discarded; AL-R-E-07"},
    ],
    "unknowns": [
        {"id": "AL-U-E-01", "subject": "death vs expiry on Retail", "question": "Does Retail run on-expire behaviour (e.g. Lifebloom bloom, raw-495 trigger) when the holder dies before expiry?",
         "known": "Trinity: no (DEATH mode, EXPIRE handlers skipped)", "why_unresolved": "no Retail consumer; Trinity is a consumer oracle only",
         "evidence": ["trinity-consumer", "retail-unknown"], "coords": ["Unit.cpp:4480", "spell_druid.cpp:1506"],
         "blocker": "Retail observation", "reopen_condition": "AL-X-E-01 result", "build_skew": False},
        {"id": "AL-U-E-02", "subject": "PvP death combat exit", "question": "When a unit dies with PvP combat references (SuppressPvPCombat), when do LeavingCombat-interrupt auras go and with which mode relative to DEATH?",
         "known": "PvE: INTERRUPT before DEATH sweep; PvP: combat is suppressed, not ended, in CombatStop", "why_unresolved": "suppressed-combat lifecycle not traced",
         "evidence": ["trinity-consumer", "unresolved"], "coords": ["Unit.cpp:6036", "CombatManager.cpp:416"],
         "blocker": "CombatManager PvP suppression path", "reopen_condition": "trace SuppressPvPCombat -> UpdateOwnerCombatState", "build_skew": False},
        {"id": "AL-U-E-03", "subject": "dead caster triggered periodic casts", "question": "Does a PERIODIC_TRIGGER_SPELL tick whose trigger caster is the (dead) aura caster produce a cast?",
         "known": "trigger caster chosen by NeedsToBeTriggeredByCaster; absent caster -> no cast", "why_unresolved": "Spell::prepare/CheckCast for dead triggered casters not traced",
         "evidence": ["trinity-consumer", "unresolved"], "coords": ["SpellAuraEffects.cpp:5594"], "blocker": "Spell cast checks",
         "reopen_condition": "trace TRIGGERED_FULL_MASK CheckCast for a dead caster", "build_skew": False},
        {"id": "AL-U-E-04", "subject": "same-millisecond death and expiry", "question": "When the lethal event and the holder's expiry update share a timestamp, which happens first?",
         "known": "outcome differs (TL-E-03: 3 ticks DEATH; TL-E-04: 4 ticks EXPIRE)", "why_unresolved": "map/unit update ordering (track I)",
         "evidence": ["structural-inference", "unresolved"], "coords": ["Unit.cpp:2976-2991"], "blocker": "track I ordering",
         "reopen_condition": "track I same-timestamp model", "build_skew": False},
        {"id": "AL-U-E-05", "subject": "IGNORE_OWNERS_DEATH", "question": "What does SPELL_ATTR1_IGNORE_OWNERS_DEATH do for summons / their auras?",
         "known": "NYI in Trinity (SharedDefines.h:497); RemoveAllControlled unsummons every summon", "why_unresolved": "no consumer",
         "evidence": ["db2-fact", "retail-unknown"], "coords": ["SharedDefines.h:497", "Unit.cpp:6625"], "blocker": "Retail",
         "reopen_condition": "Retail observation of a pet flagged IGNORE_OWNERS_DEATH", "build_skew": False},
        {"id": "AL-U-E-06", "subject": "recipient unapply order", "question": "Is the order in which one aura's applications on several targets are unapplied observable?",
         "known": "Aura::_Remove iterates an unordered_map keyed by GUID (storage)", "why_unresolved": "cascading remove handlers could make it observable",
         "evidence": ["trinity-consumer", "unresolved"], "coords": ["SpellAuras.cpp:641-647"], "blocker": "no consumer depends on it found",
         "reopen_condition": "a script that reads other recipients during removal", "build_skew": False},
        {"id": "AL-U-E-07", "subject": "charge-exhaustion mode on Retail", "question": "Is the last-charge removal reason distinguishable by path on Retail?",
         "known": "Trinity DEFAULT (proc) vs EXPIRE (spell mod)", "why_unresolved": "Trinity storage of mode, Retail unknown",
         "evidence": ["trinity-consumer", "retail-unknown"], "coords": ["SpellAuras.cpp:1829", "Object.cpp:2630"], "blocker": "Retail",
         "reopen_condition": "Retail combat-log removal reason for a charge-consumed buff", "build_skew": False},
        {"id": "AL-U-E-09", "subject": "unconsumed interrupt bits", "question": "What do the Disconnect and DamageCancelsScript "
         "interrupt bits (and 6 other unconsumed bits) do?",
         "known": "no Trinity consumer (removal.interrupt_flag_consumed); player occurrences in census unconsumed_interrupt_flag_occurrences",
         "why_unresolved": "no consumer != no Retail behaviour", "evidence": ["db2-fact", "retail-unknown"],
         "coords": ["SpellDefines.h:107", "SpellDefines.h:132"], "blocker": "Retail", "reopen_condition": "AL-X-E-08", "build_skew": False},
        {"id": "AL-U-E-22", "subject": "Kill without death sweep", "question": "Does a death absorbed by SCHOOL_ABSORB_OVERKILL "
         "(Spirit of Redemption shape) end any auras on Retail?",
         "known": "Trinity runs KILL/DEATH procs but skips setDeathState: no death sweep, no combat exit", "why_unresolved": "Retail",
         "evidence": ["trinity-consumer", "retail-unknown"], "coords": ["Unit.cpp:998-1044", "Unit.cpp:11385-11411"],
         "blocker": "Retail", "reopen_condition": "Retail combat log around Spirit of Redemption", "build_skew": False},
        {"id": "AL-U-E-08", "subject": "set-mode stacks to zero", "question": "Does raw-289 set-mode with value 0 end the aura?",
         "known": "Trinity SetStackAmount(0) leaves a live 0-stack aura (SpellAuras.cpp:1056, SpellEffects.cpp:6141)", "why_unresolved": "track C owns stack mutation; Retail unknown",
         "evidence": ["trinity-consumer", "retail-unknown"], "coords": ["SpellEffects.cpp:6141"], "blocker": "track C / Retail",
         "reopen_condition": "track C census of raw-289 set-mode rows", "build_skew": False},
    ],
    "trinity_defects": [
        {"id": "AL-D-E-01", "coords": ["spell_druid.cpp:1506-1507"], "description": "Lifebloom AfterEffectRemove calls GetCaster()->CastSpell without a null check; the aura outlives a despawned caster (RM-31 notes) so EXPIRE/ENEMY_SPELL removal then dereferences nullptr",
         "lifecycle_effect": "crash instead of no bloom when the caster left the map before expiry/dispel", "oracle_behaviour": "lifetime/removal models report the branch; FailClosed for absent caster"},
        {"id": "AL-D-E-03", "coords": ["SpellAuraEffects.cpp:5687", "SpellAuraEffects.cpp:968-982"], "description": "PERIODIC_WEAPON_PERCENT_DAMAGE tick dereferences caster without a null check; latent because raw 70 is not in CalculatePeriodic's periodic list (853 providers in the all population carry raw 70 and never tick in Trinity -- periodic semantics are track D's)",
         "lifecycle_effect": "none unless a script marks raw 70 periodic", "oracle_behaviour": "lifetime.tick_effective raises FailClosed for an absent caster"},
    ],
    "retail_experiments": [
        {"id": "AL-X-E-01", "question": "Does an on-expire payoff fire when the holder dies before expiry?",
         "models": [{"name": "death==expiry", "prediction": "bloom heal / on-expire event after the death"},
                    {"name": "trinity", "prediction": "aura removed at death, no bloom, no final periodic heal"}],
         "setup": "Lifebloom (33763) on a friendly target that dies ~0.5 s before expiry", "observable": "combat log SPELL_AURA_REMOVED and the bloom heal event",
         "discriminates": "AL-R-E-04 / AL-U-E-01", "fidelity": "exact", "related": ["AL-U-E-01", "AL-R-E-04"]},
        {"id": "AL-X-E-02", "question": "Does a DoT deal its last tick if the target dies just before it?",
         "models": [{"name": "flush-on-removal", "prediction": "a final (pro-rated) tick at death"},
                    {"name": "trinity", "prediction": "no further tick after UNIT_DIED"}],
         "setup": "periodic damage on a target killed by another source between the penultimate and last tick", "observable": "SPELL_PERIODIC_DAMAGE events after UNIT_DIED",
         "discriminates": "AL-R-E-01", "fidelity": "exact", "related": ["AL-R-E-01"]},
        {"id": "AL-X-E-03", "question": "Do a dead caster's DoTs keep ticking?",
         "models": [{"name": "caster-death-removes", "prediction": "SPELL_AURA_REMOVED at caster death"},
                    {"name": "trinity", "prediction": "ticks continue until expiry"}],
         "setup": "caster applies a DoT and dies", "observable": "SPELL_PERIODIC_DAMAGE with the dead caster as source",
         "discriminates": "AL-R-E-07", "fidelity": "exact", "related": ["AL-R-E-07"]},
        {"id": "AL-X-E-05", "question": "When Lifebloom is moved to a second target, does the old one bloom?",
         "models": [{"name": "trinity", "prediction": "old aura removed DEFAULT by the single-target cap: no bloom"},
                    {"name": "bloom-on-any-end", "prediction": "old aura blooms"}],
         "setup": "Lifebloom (33763) on A, recast on B", "observable": "Lifebloom bloom heal on A in the combat log",
         "discriminates": "RM-38", "fidelity": "exact", "related": ["AL-R-E-05"]},
        {"id": "AL-X-E-06", "question": "Does a movement-interrupted party (area) aura flicker back on the recipient?",
         "models": [{"name": "trinity", "prediction": "removed on move, re-applied within <= 500 ms while in range"},
                    {"name": "gone-until-recast", "prediction": "stays off"}],
         "setup": "party-member recipient of an area aura whose spell has a Moving interrupt flag moves", "observable": "UNIT_AURA remove/add pair",
         "discriminates": "RM-09 foreign_owned_outcome", "fidelity": "approximate", "related": ["AL-U-E-06"]},
        {"id": "AL-X-E-07", "question": "Do positive buffs count down while the player is offline?",
         "models": [{"name": "trinity", "prediction": "positive auras freeze; negative / ATTR4_AURA_EXPIRES_OFFLINE count down"},
                    {"name": "all-count-down", "prediction": "every aura loses the offline time"}],
         "setup": "log out with a 10-min positive buff and a debuff for 2 min", "observable": "remaining durations after login",
         "discriminates": "RM-43", "fidelity": "exact", "related": []},
        {"id": "AL-X-E-08", "question": "Do the Disconnect / DamageCancelsScript interrupt bits remove auras?",
         "models": [{"name": "trinity", "prediction": "no effect (no consumer)"},
                    {"name": "implemented", "prediction": "aura removed on disconnect / on damage"}],
         "setup": "player aura carrying the bit (census unconsumed_interrupt_flag_occurrences, player); disconnect or take damage",
         "observable": "aura presence afterwards", "discriminates": "AL-U-E-09", "fidelity": "approximate", "related": ["AL-U-E-09"]},
        {"id": "AL-X-E-04", "question": "In which order are auras removed when a unit in combat dies?",
         "models": [{"name": "single-death-sweep", "prediction": "all removals share one reason"},
                    {"name": "trinity", "prediction": "leave-combat-interrupt auras and channel auras go before the death sweep"}],
         "setup": "unit with a LeavingCombat-interrupt aura and an active channel dies", "observable": "combat-log order of SPELL_AURA_REMOVED around UNIT_DIED",
         "discriminates": "AL-R-E-08", "fidelity": "approximate", "related": ["AL-R-E-08", "AL-U-E-02"]},
    ],
    "core_navigation": [
        {"id": "AL-K-E-01", "topic": "periodic dispel refusal", "core_coords": ["crates/combat/src/program/dispel.rs:244-248", "crates/combat/src/program/dispel_mechanic.rs:387-395"],
         "research_ref": "dispel.json rules AL-R-E-10..12; RM-11/RM-13", "observation": "Core refuses dispellable auras with periodic fanout; Trinity removes the whole periodic aura with no final tick or lowers stacks keeping the periodic timer. The refusal is a correct boundary, not a gap to fill by inference.",
         "reopen_condition": "Core models periodic removal without a tick and stack decrement without timer reset"},
        {"id": "AL-K-E-02", "topic": "auras on a corpse (CSA-E-01)", "core_coords": ["crates/combat/src/execution/expiry_execution.rs:9-40", "crates/combat/src/state.rs:1368-1397"],
         "research_ref": "lifetime.json death_order, census death_class", "observation": "Trinity removes non-persistent auras at death with DEATH (no on-expire driver) but ATTR3_ALLOW_AURA_WHILE_DEAD auras persist and expire with EXPIRE on the corpse; passive ones too (optionally unapplied by ATTR7).",
         "reopen_condition": "Core adds a death lifecycle transition"},
    ],
}


def _removal(args) -> int:
    _need(args)
    ctx, pops = _ctx_pops()
    if args.corpus:
        emit(build_removal(ctx, pops), args.out)
        return 0
    from . import removal
    rows = [r for r in removal.script_classes_reading_mode(ctx.bundle) if "class" in r]
    emit(removal.explain(ctx, args.spell, rows), args.out)
    return 0


# ---------------------------------------------------------------------------
# dispel
# ---------------------------------------------------------------------------

def dispel_fixtures() -> list[dict[str, Any]]:
    """Deterministic runtime witnesses of the dispel model (probe-checked in tests)."""
    from . import dispel as d
    out = []
    a = d.DAura("A", 100, "c1", positive=False)
    b = d.DAura("B", 200, "c1", stacks=5, max_stacks=5)
    lst = d.dispellable_list([a, b], d.dispel_mask(1), target_friendly_to_dispeller=True)
    rng = d.ScriptedRng(ints=[1, 50, 0, 50])
    res = d.effect_dispel(lst, 2, rng)
    out.append({"id": "DF-01", "title": "one selection weight per aura identity (5-stack B vs A)",
                "candidates": [(c.aura.key, c.charges) for c in lst], "rng_calls": rng.calls,
                "success": res.success, "attempts": res.attempts})
    rng = d.ScriptedRng(ints=[0, 99, 0, 10])
    res = d.effect_dispel(d.dispellable_list([d.DAura("R", 300, "c1", resist_pct=90)], d.dispel_mask(1), True), 2, rng)
    out.append({"id": "DF-02", "title": "failed resistance roll consumes an attempt", "rng_calls": rng.calls,
                "success": res.success, "failed": res.failed_spells})
    s = d.apply_dispel(d.AuraState(d.DAura("S", 400, "c1", stacks=3, max_stacks=5, periodic=True), 3, 0), 1)
    out.append({"id": "DF-03", "title": "one-stack dispel of a 3-stack periodic aura", "log": s.log, "stacks": s.stacks,
                "refreshed": s.refreshed, "removed": s.removed})
    s = d.apply_dispel(d.AuraState(d.DAura("C", 500, "c1", dispel_removes_charges=True), 1, 0), 1)
    out.append({"id": "DF-04", "title": "ATTR7_DISPEL_REMOVES_CHARGES aura without charges", "log": s.log, "removed": s.removed})
    x = d.DAura("X1", 600, "gone1", caster_present=False)
    y = d.DAura("X2", 600, "gone2", caster_present=False)
    rng = d.ScriptedRng(ints=[0, 0, 0, 0])
    res = d.effect_dispel(d.dispellable_list([x, y], d.dispel_mask(1), True), 2, rng)
    out.append({"id": "DF-05", "title": "same spell, two absent casters: success groups collapse (AL-D-E-02)",
                "success": res.success, "attempts": res.attempts})
    return out


def build_dispel(ctx, pops) -> dict[str, Any]:
    from . import dispel as d
    census = d.census(ctx, pops)
    witnesses = []
    for spell in (774, 33763, 974, 1943, 18499, 16870, 527, 370, 30449, 2782, 4987, 374251, 64380):
        try:
            e = d.explain(ctx, spell)
        except FailClosed as exc:
            witnesses.append({"spell": spell, "fail_closed": str(exc)})
            continue
        e["in_player_population"] = spell in frozenset(ctx.scope.reach)
        witnesses.append(e)
    payload = {
        "provenance": records.provenance(CORPUS_CMD.format(cmd="dispel")),
        "dispel_types": {str(k): v for k, v in d.DISPEL_TYPES.items()},
        "dispel_all_mask": d.DISPEL_ALL_MASK,
        "census": census,
        "witnesses": witnesses,
        "fixtures": dispel_fixtures(),
        **DISPEL_RECORDS,
    }
    records.validate_corpus(payload)
    return payload


DISPEL_RECORDS: dict[str, list[dict[str, Any]]] = {
    "rules": [
        {"id": "AL-R-E-10", "name": "dispel-selection",
         "definition": "Candidates are the target's owned auras applied on it, not passive, dispel-mask hit, application positivity "
                       "!= relation (xor reflect), CalcDispelChance > 0 and >0 stacks (or charges under ATTR7); one weight per aura.",
         "population": "dispellable providers (census.*.dispellable)", "counterexamples": [], "status": "trinity-only",
         "evidence": ["trinity-consumer", "trinity-probe", "differential"]},
        {"id": "AL-R-E-11", "name": "dispel-attempt-draws",
         "definition": "Each of the effect-value attempts draws urand(0, remaining-1) then irand(0,99) (int32 roll_chance); a failed "
                       "roll consumes the attempt and leaves the candidate; each call consumes one rand32 word on libstdc++ 13 "
                       "(including urand(0,0) and a 100 % chance) plus one more per Lemire rejection (word w redrawn when "
                       "(w*n) mod 2^32 < 2^32 mod n; e.g. w = 0 for irand(0,99)).",
         "population": "raw-38 dispellers (census.*.dispeller_effects)", "counterexamples": [], "status": "trinity-only",
         "evidence": ["trinity-probe", "differential"]},
        {"id": "AL-R-E-12", "name": "dispel-removal-unit",
         "definition": "A success removes one stack (or one charge under ATTR7_DISPEL_REMOVES_CHARGES), all of them under "
                       "ATTR1_DISPEL_ALL_STACKS; OnDispel runs before and AfterDispel after the mutation; mode ENEMY_SPELL. "
                       "All removals are deferred until every attempt has been rolled (SpellEffects.cpp:2216).",
         "population": "dispellable providers", "counterexamples": [], "status": "trinity-only",
         "evidence": ["trinity-consumer", "trinity-probe"]},
        {"id": "AL-R-E-14", "name": "dispel-type-11-is-a-live-family",
         "definition": "DispelType 11 (Trinity enum name DESPEL_OLD_UNUSED) is carried by current auras (all 12 player-population "
                       "ones are bleeds by name: Rupture 1943, Rip 1079, Rake 155722, Garrote 703, ...) and dispelled by misc-11 "
                       "effects (Cauterizing Flame 374251); Trinity's mask arithmetic (1 << 11) handles it, the DISPEL_ALL mask "
                       "does not include it.",
         "population": "census.*.dispellable dispel_type:OLD_UNUSED (all 818, player 12)", "counterexamples": [],
         "status": "holds-on-census", "evidence": ["db2-fact", "trinity-consumer", "structural-inference"]},
        {"id": "AL-R-E-13", "name": "mechanic-dispel-rolls-first",
         "definition": "Mechanic dispel draws irand(0,99) for every owned aura applied on the target (passive and positive ones "
                       "included) before testing the mechanic mask, then removes whole auras with ENEMY_SPELL and no dispel hooks.",
         "population": "raw-108 dispellers", "counterexamples": [], "status": "trinity-only", "evidence": ["trinity-consumer"]},
    ],
    "falsification": [
        {"id": "AL-F-E-10", "rule": "dispel resistance roll is a float rand_chance()", "attempt": "overload resolution of roll_chance(int32) (Random.h:54-72) and probe rngcount",
         "result": "signed_integral overload -> irand(0, 99)", "action": "discarded (this track's first model); AL-R-E-11"},
        {"id": "AL-F-E-11", "rule": "urand over a singleton domain consumes no draw (Core RandomStream::uniform_index)",
         "attempt": "probe rngcount urand 0 0", "result": "one rand32 word per call on libstdc++ 13", "action": "discarded for Trinity; AL-K-E-03"},
        {"id": "AL-F-E-12", "rule": "stacks weight the dispel selection", "attempt": "GetDispellableAuraList + DF-01", "result": "one entry per aura regardless of stacks",
         "action": "discarded"},
        {"id": "AL-F-E-13", "rule": "a stack dispel refreshes the aura", "attempt": "ModStackAmount(num<0) refresh predicate; DF-03",
         "result": "no RefreshTimers for a decrease", "action": "discarded; AL-R-E-06"},
    ],
    "unknowns": [
        {"id": "AL-U-E-10", "subject": "Retail dispel RNG", "question": "Does Retail spend an attempt on a resisted dispel and select uniformly per aura identity?",
         "known": "Trinity: yes and yes", "why_unresolved": "Retail server code unavailable", "evidence": ["trinity-consumer", "retail-unknown"],
         "coords": ["SpellEffects.cpp:2163-2194"], "blocker": "Retail", "reopen_condition": "AL-X-E-10", "build_skew": False},
        {"id": "AL-U-E-11", "subject": "application positivity", "question": "Which applications count as positive for dispel filtering when caster and holder relation differs from the spell's positivity?",
         "known": "AuraApplication ctor decides per effect mask and caster friendliness (SpellAuras.cpp:120-150)", "why_unresolved": "track A/targeting positivity owns it",
         "evidence": ["trinity-consumer", "unresolved"], "coords": ["SpellAuras.cpp:120-150"], "blocker": "track A", "reopen_condition": "track A application positivity model", "build_skew": False},
    ],
    "trinity_defects": [
        {"id": "AL-D-E-04", "coords": ["Unit.cpp:4765-4768"], "description": "An ATTR7_DISPEL_REMOVES_CHARGES aura with zero charges is never a dispel candidate (charges > 0 filter) although its DispelType matches; 6 dispellable non-passive providers in the all population have no authored ProcCharges (139368, 184239, 247788, 296199, 296429, 333874; none player) -- dispellable only if runtime spell mods / spell_proc grant charges",
         "lifecycle_effect": "aura immune to dispel in Trinity", "oracle_behaviour": "dispel.dispellable_list reproduces the filter; census counts dispel_removes_charges_but_no_authored_charges"},
        {"id": "AL-D-E-02", "coords": ["SpellEffects.cpp:2174", "SpellEffects.cpp:2222"], "description": "EffectDispel groups successes by (spell id, GetCaster() pointer); two auras of the same spell whose casters are both absent compare equal (nullptr == nullptr) and collapse into one group, so RemoveAurasDueToSpellByDispel is called only with the first aura's caster GUID",
         "lifecycle_effect": "the second aura's success is credited to the first (extra charge) and the second aura survives", "oracle_behaviour": "dispel.effect_dispel reproduces the collapse (fixture DF-05)"},
    ],
    "retail_experiments": [
        {"id": "AL-X-E-10", "question": "Is dispel selection uniform per aura identity, and does a resisted attempt consume the attempt?",
         "models": [{"name": "trinity", "prediction": "uniform per identity; resisted attempt consumed"},
                    {"name": "stack-weighted", "prediction": "selection probability proportional to stacks"}],
         "setup": "target with one 5-stack and one 1-stack Magic debuff, many single-attempt dispels", "observable": "frequency of each removal",
         "discriminates": "AL-R-E-10 / AL-R-E-11", "fidelity": "approximate", "related": ["AL-U-E-10"]},
        {"id": "AL-X-E-11", "question": "Does a one-stack dispel of a stacked DoT change its remaining duration or next tick time?",
         "models": [{"name": "trinity", "prediction": "no change"}, {"name": "refresh-on-change", "prediction": "duration refreshed"}],
         "setup": "3-stack Magic DoT, dispel one stack", "observable": "UnitAura expirationTime and tick timestamps", "discriminates": "AL-R-E-06",
         "fidelity": "exact", "related": ["AL-R-E-06"]},
    ],
    "core_navigation": [
        {"id": "AL-K-E-03", "topic": "singleton-domain draws", "core_coords": ["crates/sim/src/random.rs:133-146"],
         "research_ref": "dispel.json AL-R-E-11", "observation": "Core's uniform_index consumes no draw for a singleton domain; Trinity's urand(0,0) consumes one engine word and every resistance roll consumes one irand word even at 100 %. Stream alignment differs (not an exactness claim about Retail).",
         "reopen_condition": "Core adopts a Trinity-aligned draw-address contract"},
        {"id": "AL-K-E-04", "topic": "mechanic-dispel forced trial count (UNK-I-002)", "core_coords": ["crates/combat/src/program/dispel_mechanic.rs:387-395"],
         "research_ref": "AL-R-E-13", "observation": "Trinity draws once per owned aura applied on the target, passive/hidden ones included, before the mechanic test.",
         "reopen_condition": "passive traits represented as auras in Core"},
    ],
}


def _dispel(args) -> int:
    _need(args)
    ctx, pops = _ctx_pops()
    if args.corpus:
        emit(build_dispel(ctx, pops), args.out)
        return 0
    from . import dispel as d
    emit(d.explain(ctx, args.spell), args.out)
    return 0


# ---------------------------------------------------------------------------
# lifetime
# ---------------------------------------------------------------------------

def build_lifetime(ctx, pops) -> dict[str, Any]:
    from . import lifetime as lt
    fx = lt.DeathFixture(auras=[
        lt.HeldAura("dot_on_victim"),
        lt.HeldAura("leave_combat_buff", leaving_combat_interrupt=True, death_persistent=True, combat_hook=True),
        lt.HeldAura("passive_trait", passive=True),
        lt.HeldAura("persistent_buff", death_persistent=True),
        lt.HeldAura("own_area_aura", recipients=("ally",)),
        lt.HeldAura("channel_on_enemy", channel_of_dying=True, owned_by_dying=False, applied_on_dying=False),
    ], is_player=True, has_pet=True, has_summons=True, in_vehicle=True, charmed_auras=("charm_on_other",),
        totem_auras=("totem_buff_on_party",), casting_dynobj_auras=("dynobj_of_channel",))
    witnesses = []
    for spell in (1943, 974, 465, 15407, 755, 33763, 2823, 5740):
        try:
            e = lt.explain(ctx, spell)
            e["in_player_population"] = spell in pops["player"]
        except FailClosed as exc:
            e = {"spell": spell, "fail_closed": str(exc)}
        witnesses.append(e)
    payload = {
        "provenance": records.provenance(CORPUS_CMD.format(cmd="lifetime")),
        "death_order_fixture": {"fixture": {"auras": [a.__dict__ for a in fx.auras], "is_player": True, "has_pet": True,
                                            "has_summons": True, "pve_combat_only": True, "in_vehicle": True,
                                            "charmed_auras": list(fx.charmed_auras), "totem_auras": list(fx.totem_auras),
                                            "casting_dynobj_auras": list(fx.casting_dynobj_auras)},
                                "steps": lt.death_order(fx)},
        "tick_gates": {str(k): {**{x: y for x, y in v.items() if x != "anchor"}} for k, v in sorted(lt.TICK_GATES.items())},
        "matrix": lt.matrix(),
        "timelines": lt.standard_timelines(),
        "witnesses": witnesses,
        **LIFETIME_RECORDS,
    }
    records.validate_corpus(payload)
    return payload


LIFETIME_RECORDS: dict[str, list[dict[str, Any]]] = {
    "rules": [
        {"id": "AL-R-E-20", "name": "caster-read-live-each-tick",
         "definition": "Each periodic tick re-reads the caster via ObjectAccessor (Aura::GetCaster); a dead caster is still found "
                       "and its bonuses apply, a despawned caster is nullptr (no bonus, no periodic crit); funnel/mana-leech need it alive.",
         "population": "periodic providers", "counterexamples": [], "status": "trinity-only", "evidence": ["trinity-consumer"]},
        {"id": "AL-R-E-21", "name": "surviving-auras-keep-running-on-corpse",
         "definition": "Auras that survive the death sweep keep their duration and tick counter; damage/heal/energize/power ticks "
                       "on a dead holder are no-ops, but PERIODIC_TRIGGER_SPELL(_WITH_VALUE) and PERIODIC_DUMMY ticks act (no alive "
                       "gate; a failed triggered cast ends the aura with EXPIRE, RM-47). The aura ends with EXPIRE, or with DEFAULT "
                       "when a creature corpse is removed first (Creature.cpp:442 / despawn RemoveAllAuras).",
         "population": "census death_class survives-* with periodic effects", "counterexamples": [], "status": "trinity-only",
         "evidence": ["trinity-consumer", "differential"]},
    ],
    "falsification": [
        {"id": "AL-F-E-20", "rule": "surviving auras are frozen while the holder is dead",
         "attempt": "Aura::Update / AuraEffect::Update have no liveness gate (TL-E-09)", "result": "duration and ticks keep running",
         "action": "discarded; AL-R-E-21"},
    ],
    "unknowns": [
        {"id": "AL-U-E-21", "subject": "periodic phase after reload", "question": "After logout/pet reload (new object), where is the next tick?",
         "known": "CalculatePeriodic(load=true) derives ticks/timer from max-remaining (SpellAuraEffects.cpp:1016-1026); not modelled here",
         "why_unresolved": "track D owns periodic phase", "evidence": ["trinity-consumer", "unresolved"],
         "coords": ["SpellAuraEffects.cpp:1016-1026", "Player.cpp:18976"], "blocker": "track D", "reopen_condition": "track D reload model", "build_skew": False},
        {"id": "AL-U-E-20", "subject": "dynobj auras after caster death", "question": "Do persistent area auras of a dead caster keep ticking on Retail?",
         "known": "Trinity: yes for spells not being cast at death; all dynobjects of the spell being cast/channelled are removed (Spell.cpp:3665)", "why_unresolved": "Retail", "evidence": ["trinity-consumer", "retail-unknown"],
         "coords": ["Unit.cpp:10279"], "blocker": "Retail", "reopen_condition": "Retail observation", "build_skew": False},
    ],
    "retail_experiments": [
        {"id": "AL-X-E-20", "question": "Does a death-persistent aura keep counting down on a corpse and resume with the remaining time after resurrect?",
         "models": [{"name": "trinity", "prediction": "keeps counting (and ATTR7 ones re-apply after resurrect)"},
                    {"name": "frozen-while-dead", "prediction": "remaining time preserved from the death moment"}],
         "setup": "Earth Shield (974) holder dies and is resurrected 10 s later", "observable": "remaining duration after resurrect",
         "discriminates": "AL-R-E-21", "fidelity": "exact", "related": ["AL-R-E-21"]},
    ],
}


def _lifetime(args) -> int:
    _need(args)
    if args.corpus:
        ctx, pops = _ctx_pops()
        emit(build_lifetime(ctx, pops), args.out)
        return 0
    from . import context
    from . import lifetime as lt
    emit(lt.explain(context.get(), args.spell), args.out)
    return 0


COMMANDS = {
    "removal": ("track E: removal sites / modes of one spell, or the removal corpus",
                lambda p: _add(p, "write the removal corpus"), _removal),
    "dispel": ("track E: dispel view of one spell, or the dispel corpus", lambda p: _add(p, "write the dispel corpus"), _dispel),
    "lifetime": ("track E: death / source lifetime view of one spell, or the lifetime corpus",
                 lambda p: _add(p, "write the lifetime corpus"), _lifetime),
}
