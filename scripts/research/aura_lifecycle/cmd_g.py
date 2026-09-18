"""Track G commands: ``passive <spell>`` and ``passive --corpus`` (passive-active.json)."""

from __future__ import annotations

from typing import Any

from .cli import emit, write_json

CORPUS_NAME = "passive-active.json"
NAMED = {
    387095: "Pyrogenics (selected passive; spell_proc + AuraScript OnEffectProc)",
    387096: "Pyrogenics debuff (the child the passive casts; mutable state lives here)",
    1269312: "Phalanx provider (CumulativeAura 2 on a passive)",
    406154: "Heart of the Crusader provider (ProcTypeMask 4 on a passive)",
    48263: "Veteran of the Third War (passive with the Login interrupt flag)",
    187880: "Maelstrom Weapon (passive with the ChangeSpec interrupt flag)",
    197915: "Lifecycles (passive with proc charges)",
}


def _add(p) -> None:
    p.add_argument("spell", type=int, nargs="?")
    p.add_argument("--corpus", action="store_true", help=f"write the full {CORPUS_NAME} corpus")
    p.add_argument("--out")


def _run(args) -> int:
    from . import FailClosed, context, passive
    ctx = context.get()
    if args.corpus:
        payload = build_corpus(ctx, args.out)
        write_json(args.out or _default_out(), payload)
        return 0
    if args.spell is None:
        raise FailClosed("passive: give a spell id or --corpus")
    emit(passive.profile(ctx, args.spell), args.out)
    return 0


def _default_out():
    from . import CORPORA
    return CORPORA / CORPUS_NAME


def _named(ctx, spells: list[int]) -> list[dict[str, Any]]:
    return [{"spell": s, "name": ctx.name(s), "build_skew": ctx.is_skew(s)} for s in spells]


def build_corpus(ctx, out: str | None) -> dict[str, Any]:
    from . import records
    from . import passive as pv
    cen = pv.census(ctx)
    rows = cen.pop("rows")
    counts = cen["counts"]
    wit = {pop: {k: _named(ctx, v) for k, v in d.items()} for pop, d in cen["witnesses"].items()}
    interrupts = {pop: {name: {**e, "witnesses": _named(ctx, e["witnesses"])} for name, e in d.items()}
                  for pop, d in cen["passive_interrupt_flags"].items()}
    routes = {pop: {k: {"count": e["count"], "witnesses": _named(ctx, e["witnesses"])} for k, e in d.items()}
              for pop, d in cen["passive_acquisition_routes"].items()}
    writers = pv.writers_index(ctx.data)
    named = []
    for s, why in sorted(NAMED.items()):
        try:
            named.append({"why": why, **pv.profile(ctx, s, writers)})
        except Exception as exc:  # noqa: BLE001 -- recorded, never defaulted
            named.append({"spell": s, "why": why, "fail_closed": str(exc)})
    player_passives = sorted(s for s in rows if rows[s]["passive"] and s in ctx.scope.reach)
    drift = pv.drift_census(ctx, player_passives)
    a, p = counts["all"], counts["player"]
    rem_writers = {t: v for t, v in writers["remove_aura"].items() if rows.get(t, {}).get("passive")}

    rules = [
        _rule(1, "effective-passive", "Trinity IsPassive = DB2 SPELL_ATTR0_PASSIVE OR ActiveIconFileDataId==135754 OR spell 59630",
              f"all providers {a['providers']}: passive {a['passive']} ({a['passive_by_correction_only']} by correction only); "
              f"player {p['providers']}: passive {p['passive']} (0 by correction only)",
              wit["all"].get("passive_by_correction_only", []), "holds-on-census", ["db2-fact", "trinity-consumer"]),
        _rule(2, "passive-reapplication-never-refreshes",
              "A second application of a passive never finds the existing object (IsMultiSlotAura, Unit.cpp:3395): "
              "same caster & no cast item -> new object, old removed BY_DEFAULT (state reset); item-sourced -> coexist; "
              "passive without APPLY_AURA effect -> coexist (IsPassiveStackableWithRanks)",
              f"passive: all {a['passive']}, player {p['passive']}; no-APPLY_AURA passives all {a['passive_stackable_with_ranks']} / player {p['passive_stackable_with_ranks']}",
              [], "trinity-only", ["trinity-consumer", "retail-unknown"]),
        _rule(3, "passive-duration", "A passive is permanent iff it has no DurationEntry (SpellAuras.cpp:925); with one it expires",
              f"passives with finite duration: all {a['passive_finite_duration']}, player {p['passive_finite_duration']}",
              wit["all"].get("passive_finite_duration", []), "holds-on-census", ["db2-fact", "trinity-consumer"]),
        _rule(4, "passive-survives-death", "Owner death removes neither the owned passive nor its applications (Unit.cpp:4479/4488); "
              "actives survive only with SPELL_ATTR3_ALLOW_AURA_WHILE_DEAD",
              f"passive: all {a['passive']}, player {p['passive']}", [], "trinity-only", ["trinity-consumer", "retail-unknown"]),
        _rule(5, "passive-unremovable-by-players-and-dispels", "Passives are skipped by dispel lists, spell steal, immunity purge, CMSG_CANCEL_AURA and arena entry",
              f"passive: all {a['passive']}, player {p['passive']}", [], "trinity-only", ["trinity-consumer"]),
        _rule(6, "passive-not-saved", "Passives are never saved (CanBeSaved SpellAuras.cpp:1170): all their mutable state resets at every login",
              f"passive: all {a['passive']}, player {p['passive']}; with any mutable carrier: all {a['passive_any_mutable']}, player {p['passive_any_mutable']}",
              [], "trinity-only", ["trinity-consumer", "retail-unknown"]),
        _rule(7, "spellmod-recalculates-passive-amounts", "A points SpellMod change recalculates amounts of the caster's own passive/permanent auras; "
              "temporary actives keep their application snapshot (SpellAuraEffects.cpp:1233), literal exception 384669",
              f"passive: all {a['passive']}, player {p['passive']}", [{"spell": 384669, "why": "IsUpdatingTemporaryAuraValuesBySpellMod literal"}],
              "trinity-only", ["trinity-consumer"]),
        _rule(8, "passive-is-immutable", "Candidate: a passive aura carries no mutable state (the Core/selected-package assumption)",
              f"counterexamples with >=1 mutable carrier: all {a['passive_any_mutable']} of {a['passive']}, player {p['passive_any_mutable']} of {p['passive']}",
              wit["player"].get("passive_any_mutable", []), "discarded", ["db2-fact", "world-db-fact", "script-consumer"]),
        _rule(9, "no-self-restoration", "Once removed by interrupt, REMOVE_AURA effect, charges, expiry, form or aura-state loss, a passive returns "
              "only on an acquisition event (login, relearn, trait/spec edit, equip, form entry, state gain); Trinity has no re-application loop",
              f"passives exposed to such removal (interruptible all {a['passive_interruptible']}/player {p['passive_interruptible']}; "
              f"REMOVE_AURA targets all {a['passive_remove_aura_target']}; charges all {a['passive_proc_charges']}/player {p['passive_proc_charges']}; "
              f"finite all {a['passive_finite_duration']}; stances all {a['passive_stances']}/player {p['passive_stances']}; "
              f"aura-state all {a['passive_caster_aura_state']}/player {p['passive_caster_aura_state']})",
              [], "trinity-only", ["trinity-consumer", "retail-unknown"]),
        _rule(10, "rank-change-recreates", "A trait rank change is ApplyTraitEntry(false)+ApplyTraitEntry(true) (Player.cpp:29266-29268): "
              "RemoveSpell then LearnSpell -> new aura object; amount re-read from the TraitDefinitionEffectPoints curve",
              "every class-trait passive with >1 rank", [], "trinity-only", ["trinity-consumer", "retail-unknown"]),
        _rule(11, "equip-routes-ignore-passive", "Item/set/enchant equip routes cast the spell whether or not it is Passive; actives acquired this way "
              "have their own duration and are not re-cast until re-equip/login",
              f"non-passive providers reached by an equip route: player {p['active_on_equip_route']}",
              wit["player"].get("active_on_equip_route", []), "holds-on-census", ["db2-fact", "trinity-consumer"]),
        _rule(12, "learn-applies-passives-only", "Learning applies an aura only for passives (gated by Stances, EquippedItemClass, CasterAuraState) "
              "and for actives with SPELL_ATTR1_CAST_WHEN_LEARNED (Player.cpp:2912-2927)",
              f"non-passive CAST_WHEN_LEARNED providers: all {a['active_cast_when_learned']}, player {p['active_cast_when_learned']}",
              wit["all"].get("active_cast_when_learned", []), "holds-on-census", ["db2-fact", "trinity-consumer"]),
        _rule(13, "unlearn-removes-only-own-aura", "RemoveSpell removes RemoveOwnedAura(spell, self) and spell_pet_auras only; auras the passive cast "
              "(procs/scripts/triggers, e.g. Pyrogenics 387096) are untouched",
              "every learned passive", [{"spell": 387095, "child": 387096}], "trinity-only", ["trinity-consumer"]),
    ]

    falsification = [
        _fals(1, "AL-R-G-03", "Passive => permanent (-1)", f"falsified on all ({a['passive_finite_duration']} passives with a DurationEntry); holds on player (0)",
              "rule restated as 'permanent iff no DurationEntry'"),
        _fals(2, "AL-R-G-08", "Passive => no stacks", f"{a['passive_stack_cap_gt1']} passives (all) / {p['passive_stack_cap_gt1']} (player) carry CumulativeAura>1; "
              f"MODIFY_AURA_STACKS writers targeting a passive: {a['passive_modify_stacks_target']}; re-application cannot stack (multislot)",
              "capacity is dormant on the census; stack mutation only via scripts / SetAuraStack; kept as a carrier"),
        _fals(3, "AL-R-G-08", "Passive => no charges", f"{a['passive_proc_charges']} passives (all) / {p['passive_proc_charges']} (player) have max charges > 0",
              "falsified; charges consumption removes the passive (no restoration)"),
        _fals(4, "AL-R-G-09", "Passive => present until unlearned",
              f"{a['passive_interruptible']} (all) / {p['passive_interruptible']} (player) passives carry AuraInterruptFlags; "
              f"{len(rem_writers)} passives are REMOVE_AURA targets", "falsified in Trinity; Retail semantics UNKNOWN (AL-U-G-01)"),
        _fals(5, "AL-R-G-02", "Passive re-application refreshes in place like an active", "IsMultiSlotAura true for every passive (SpellInfo.cpp:1805) -> no lookup",
              "falsified; replaced by replace/coexist taxonomy (TL-G-01, TL-G-03)"),
        _fals(6, "AL-R-G-01", "IsPassive == DB2 ATTR0_PASSIVE", f"{a['passive_by_correction_only']} providers passive only by Trinity correction (all), 0 player",
              "refined: effective_passive() adds the corrections"),
        _fals(7, "AL-R-G-08", "Heart of the Crusader has per-aura proc state (ProcTypeMask 4)",
              "ProcEntryStore.lookup(406154) -> None: no trigger aura, generation bails", "kept inert (agrees with selected-package §12)"),
        _fals(8, "AL-R-G-08", "Pyrogenics is immutable (no SpellAuraOptions row)",
              "effective proc entry from spell_proc + spell_warl_pyrogenics OnEffectProc; the finite, re-applied state lives in child 387096",
              "falsified; package lifecycle = immutable-by-DB2 passive owner + proc state + active child"),
        _fals(9, "AL-R-G-12", "Every learned spell with an aura effect is applied at learn",
              "non-passive auras apply only with ATTR1_CAST_WHEN_LEARNED / talent LEARN_SPELL / SKILL_STEP (Player.cpp:2912-2927)", "refined to AL-R-G-12"),
    ]

    unknowns = [
        _unk(1, "AuraInterruptFlags on passives",
             "Retail: is a passive with an implemented interrupt flag (EnterWorld, LeaveWorld, Login, ChangeSpec, EnteringInstance, StartOfEncounter...) "
             "removed and lost, removed and immediately re-applied (state reset), or unaffected?",
             f"Trinity removes it (Unit.cpp:4246-4258) and never restores it; {a['passive_interruptible']} all / {p['passive_interruptible']} player passives",
             "no Retail observation; DB2 cannot say", ["retail-unknown", "trinity-consumer"],
             ["src/server/game/Entities/Unit/Unit.cpp:4250", "src/server/game/Entities/Unit/Unit.cpp:10250",
              "src/server/game/Handlers/CharacterHandler.cpp:1308"], "Retail experiment AL-X-G-01", "observe aura list across the event", False),
        _unk(2, "passive re-application", "Retail: does re-learning / re-picking a passive reset its mutable state (Trinity: new object) or keep it?",
             "Trinity: replace (TL-G-01), rank change = remove+learn (TL-G-07)", "no Retail observation", ["retail-unknown"],
             ["src/server/game/Spells/Auras/SpellAuras.cpp:1651", "src/server/game/Entities/Player/Player.cpp:29266"],
             "AL-X-G-02", "observe charges/proc cooldown after a trait re-pick", False),
        _unk(3, "passive mutable state across logout", "Retail: do passive stacks/charges/proc cooldowns survive logout?",
             "Trinity: never saved (SpellAuras.cpp:1170)", "no Retail observation", ["retail-unknown"],
             ["src/server/game/Spells/Auras/SpellAuras.cpp:1170"], "AL-X-G-03", "observe after relog", False),
        _unk(4, "finite-duration passives", "Retail: do passives with a DurationIndex expire?",
             f"Trinity: yes (SpellAuras.cpp:925); {a['passive_finite_duration']} providers, none in player scope", "no player witness; low value",
             ["db2-fact", "retail-unknown"], ["src/server/game/Spells/Auras/SpellAuras.cpp:925"], "a player-scope witness appears", "", False),
        _unk(5, "application while dead", "What happens when a passive is acquired while the owner is dead outside loading?",
             "Trinity: _CreateAuraApplication refuses non-death-persistent applications on a dead unit (Unit.cpp:3507-3510) -> owned aura without application",
             "which routes can fire while dead (equip? trait edit?) and whether UpdateTargetMap later applies it is track E/F's", ["trinity-consumer", "unresolved"],
             ["src/server/game/Entities/Unit/Unit.cpp:3508"], "E/F timeline of an owned-unapplied aura", "", False),
        _unk(6, "script-held passive state", "Which of the scripted passives hold AuraScript member state?",
             f"{a['passive_scripted']} all / {p['passive_scripted']} player passives have spell_script_names bindings", "script census is track H's",
             ["script-consumer", "unresolved"], ["docs/research/dummy-corpora/script-index.json"], "track H overlay classification", "", False),
    ]
    experiments = [
        _exp(1, "Are interrupt-flagged passives lost, reset, or untouched at the flag's event?",
             [{"name": "trinity-lost", "prediction": "aura absent after the event until relearn/relog"},
              {"name": "reset", "prediction": "aura present after the event, mutable state reset (new aura instance id / cleared stacks)"},
              {"name": "ignored", "prediction": "aura present, same instance, state kept"}],
             "Use a player-scope passive whose flag is StartOfEncounter/EnteringInstance/ChangeSpec (see passive_interrupt_flags.player witnesses); "
             "trigger the event (zone into an instance, pull a boss, swap spec and back)",
             "UnitAura/aura instance id and stack/charge fields before and after", "all three models", "approximate", ["AL-U-G-01", "AL-D-G-01"]),
        _exp(2, "Does a trait re-pick reset a passive's mutable state?",
             [{"name": "recreate", "prediction": "charges/proc cooldown reset after refund+re-pick"},
              {"name": "in-place", "prediction": "state kept"}],
             "Passive with proc charges or ICD (e.g. Lifecycles 197915); consume a charge / trigger the ICD, then refund and re-pick the talent",
             "charges / next possible proc time", "recreate vs in-place", "approximate", ["AL-U-G-02"]),
        _exp(3, "Does passive state survive logout?",
             [{"name": "not-saved", "prediction": "state reset on login"}, {"name": "saved", "prediction": "state restored"}],
             "Same passive as AL-X-G-02; consume state; log out and back in within the ICD", "charges / ICD after login",
             "not-saved vs saved", "approximate", ["AL-U-G-03"]),
        _exp(4, "Does a passive keep its object and state across death and resurrection?",
             [{"name": "survive", "prediction": "same aura instance, ICD still running"},
              {"name": "reapply", "prediction": "new instance, state reset"}],
             "Passive with an ICD; trigger it, die, resurrect before the ICD ends", "aura instance id, next proc time",
             "survive vs reapply", "approximate", ["AL-R-G-04"]),
    ]
    defects = [
        {"id": "AL-D-G-01", "coords": ["src/server/game/Entities/Unit/Unit.cpp:4246-4258", "src/server/game/Entities/Unit/Unit.cpp:10250",
                                       "src/server/game/Handlers/CharacterHandler.cpp:1308", "src/server/game/Entities/Player/Player.cpp:2751"],
         "description": "Passives with an implemented AuraInterruptFlag are removed by the event and, unless the firing path itself relearns "
                        "them (ChangeSpec for trait/spec passives), never re-applied: EnterWorld fires in Unit::AddToWorld after LoadFromDB cast "
                        "the passives, Login fires after login, ChangeGlyph on any glyph apply, and a relearn of a still-known spell early-returns "
                        "in AddSpell (no recast). E.g. 48263 Veteran of the Third War (Login) is absent after every login; 193468 Mastery: "
                        "Sniper Training (ChangeGlyph) is lost on a glyph change until relog/spec change.",
         "lifecycle_effect": "permanent loss of a passive until relearn/relog (relog re-loses Login/EnterWorld ones)",
         "oracle_behaviour": "reproduced as removal_policy()['aura-interrupt'] + no-self-restoration; marked likely-defect, Retail UNKNOWN (AL-U-G-01)"},
        {"id": "AL-D-G-02", "coords": ["src/server/game/Entities/Player/Player.cpp:8393", "src/server/game/Entities/Player/Player.cpp:30668"],
         "description": "Set-bonus removal (ApplyEquipSpell with no item) and mastery removal use RemoveAurasDueToSpell(id) with no caster "
                        "GUID: they also remove applications of that spell cast by other units.",
         "lifecycle_effect": "cross-caster removal on unequip/spec change", "oracle_behaviour": "ROUTES['current-set'] records 'ANY caster'"},
        {"id": "AL-D-G-03", "coords": ["src/server/game/Entities/Player/Player.cpp:2642", "src/server/game/Entities/Player/Player.cpp:29143"],
         "description": "SpellAuraInterruptFlags2::ChangeTalent fires only in legacy Player::AddTalent; UpdateTraitConfig (all current talent edits) never fires it.",
         "lifecycle_effect": "ChangeTalent-flagged auras survive trait edits in Trinity",
         "oracle_behaviour": "INTERRUPT_FLAGS consumer note"},
    ]
    core_nav = [
        {"id": "AL-K-G-01", "topic": "selected passives compiled as immutable", "core_coords": ["crates/combat/src/program/exact_source.rs:153-243"],
         "research_ref": "AL-R-G-08 (discarded), selected-passive-package-generalization.md §13 D1",
         "observation": f"Core admits selected passives through an owner-policy check; the census finds {p['passive_any_mutable']} player passives with a mutable carrier",
         "reopen_condition": "Core admission path consults effective proc entry / scripts / interrupt flags"},
        {"id": "AL-K-G-02", "topic": "passive loss events", "core_coords": ["crates/combat/src/passive_maximum_health.rs:38"],
         "research_ref": "AL-R-G-09, AL-D-G-01",
         "observation": "Core applies passive contributions as static bindings; no loss/reacquisition events exist to model interrupt/charges/expiry of passives",
         "reopen_condition": "Core introduces passive acquisition/loss lifecycle"},
    ]
    payload = {
        "provenance": records.provenance(f"aura_lifecycle.py passive --corpus --out docs/research/aura-lifecycle-corpora/{CORPUS_NAME}",
                                         populations={"all": "providers.provider_spells", "player": "Scope.reach ∩ providers",
                                                      "controlled": "controlled-unit spells ∩ providers"},
                                         passive_definition="aura_lifecycle.passive.effective_passive (Trinity SpellInfo::IsPassive)"),
        "census": {"counts": counts, "witnesses": wit, "fail_closed_total": cen["fail_closed_total"], "fail_closed": cen["fail_closed"]},
        "passive_interrupt_flags": interrupts,
        "passive_acquisition_routes": routes,
        "remove_aura_writers_of_passives": {str(k): v for k, v in sorted(rem_writers.items())},
        "acquisition_routes": pv.ROUTES,
        "non_acquisition_roots": pv.NOT_ACQUISITION,
        "named_cases": named,
        "timelines": pv.timelines(),
        "drift": {**drift, "population": "player passives"},
        "rules": rules, "falsification": falsification, "unknowns": unknowns,
        "retail_experiments": experiments, "trinity_defects": defects, "core_navigation": core_nav,
    }
    records.validate_corpus(payload)
    return payload


def _rule(n, name, definition, population, counterexamples, status, evidence):
    return {"id": f"AL-R-G-{n:02d}", "name": name, "definition": definition, "population": population,
            "counterexamples": counterexamples, "status": status, "evidence": evidence}


def _fals(n, rule, attempt, result, action):
    return {"id": f"AL-F-G-{n:02d}", "rule": rule, "attempt": attempt, "result": result, "action": action}


def _unk(n, subject, question, known, why, evidence, coords, blocker, reopen, skew):
    return {"id": f"AL-U-G-{n:02d}", "subject": subject, "question": question, "known": known, "why_unresolved": why,
            "evidence": evidence, "coords": coords, "blocker": blocker, "reopen_condition": reopen, "build_skew": skew}


def _exp(n, question, models, setup, observable, discriminates, fidelity, related):
    return {"id": f"AL-X-G-{n:02d}", "question": question, "models": models, "setup": setup, "observable": observable,
            "discriminates": discriminates, "fidelity": fidelity, "related": related}


COMMANDS = {
    "passive": ("passive/active lifecycle of one spell, or --corpus for passive-active.json", _add, _run),
}
