#!/usr/bin/env python3
"""Regenerate proc-counterexamples.json (numbered counterexamples to the naive predicate).

Driven by ``tools/regen_selected_package.py --stage tracks``.
Reads ``selected_package.procpolicy``; writes into ``docs/research/selected-package-corpora``.
"""
import pathlib
import sys, json, datetime
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from selected_package import CORPORA
from selected_package.provenance import Provenance, TraitSpellError
from selected_package.effects import classify_package
from selected_package.owner import Owners
from selected_package.procpolicy import ProcPolicy, shape_key, CROWD_CONTROL_AMOUNT_AURAS

p = Provenance(); pp = ProcPolicy(source=p.source); ow = Owners(p.source)
providers = {}
for entry_id, entry in sorted(p.traits.entries.items()):
    for rank in range(1, max(entry.max_ranks, 1) + 1):
        r = p.resolve(entry_id, rank)
        if isinstance(r, TraitSpellError):
            continue
        providers.setdefault(r.spell, []).append((entry_id, rank))
V = {s: pp.classify(s) for s in sorted(providers)}

_rc = {}
def reachable(s):
    if s in _rc: return _rc[s]
    ok = False
    if not ow.policy(s).blockers:
        for e, rk in providers[s]:
            r = p.resolve(e, rk)
            if not isinstance(r, TraitSpellError) and all(c.ok for c in classify_package(r)):
                ok = True; break
    _rc[s] = ok
    return ok

def member(s):
    v = V[s]
    return {
        "spell": s, "name": v.name, "family": v.family,
        "aura_options": v.aura_options.to_dict() if v.aura_options else None,
        "shape": shape_key(v.aura_options),
        "aura_subtypes": list(v.aura_subtypes),
        "proc_triggering_subtypes": list(v.proc_triggering_subtypes),
        "generation_reason": v.generation_reason,
        "entry_origin": v.entry_origin,
        "script_names": list(v.script_names),
        "entries": sorted({e for e, _ in providers[s]}),
        "blockers": list(v.blockers),
        "runtime_inert": v.runtime_inert,
        "reachable_as_selected_package_today": reachable(s),
    }

def pick(pred):
    return sorted(s for s, v in V.items() if pred(v))

CE = []
def add(n, title, why, evidence, ids, note=None):
    CE.append({
        "id": f"D-CE-{n:02d}", "title": title,
        "breaks": why, "evidence_class": evidence,
        "count": len(ids),
        "reachable_count": sum(1 for s in ids if reachable(s)),
        "members": [member(s) for s in ids[:20]],
        "all_ids": ids,
        **({"note": note} if note else {}),
    })

# 1 -- the Absent branch itself
ids = pick(lambda v: v.aura_options is None and v.has_effective_entry)
add(1, "No SpellAuraOptions row at all, yet an effective proc entry exists",
    "Core's SelectedAuraOptionsContract::Absent branch: 'no aura-options row may exist' is "
    "taken as proof that the provider has no proc semantics.  It is not.  A world-DB "
    "spell_proc row keys on SpellID, not on the presence of a SpellAuraOptions row "
    "(SpellMgr::LoadSpellProcs, SpellMgr.cpp:1571-1651), so a provider with zero "
    "SpellAuraOptions facts can still own a full proc lifecycle.",
    "trinity", ids)

# 2 -- rppm without an entry
ids = pick(lambda v: v.family == "rppm")
add(2, "SpellProcsPerMinuteID != 0 with no generated entry",
    "'AuraOptions present but no effective proc generation => inert' would admit these.  "
    "441346 Thousand Cuts carries the SAME ProcTypeMask 4 (PROC_FLAG_DEAL_MELEE_SWING) as "
    "Heart of the Crusader and an RPPM identity of 4.5/min.  An RPPM window is a clock: "
    "Aura::CalcPPMProcChance (SpellAuras.cpp:2034) reads m_lastProcAttemptTime / "
    "m_lastProcSuccessTime, which Aura::Aura initialises for every application "
    "(SpellAuras.cpp:481-484).",
    "source", ids)

# 3 -- ICD without an entry
ids = pick(lambda v: v.family == "proc-icd")
add(3, "ProcCategoryRecovery != 0 (authored internal cooldown) with no generated entry",
    "The naive predicate admits these.  An ICD is a clock; the field's only Trinity "
    "consumer is SpellProcEntry::Cooldown (SpellMgr.cpp:1581, 1885), so Trinity's silence "
    "is a non-implementation, not evidence of inertness.  382517 Deeper Daggers and "
    "383314 Vanguard's Momentum are reachable selected packages today.",
    "source", ids)

# 4 -- charges without an entry
ids = pick(lambda v: v.family == "charges")
add(4, "ProcCharges != 0 with no generated entry",
    "This one breaks the naive predicate on TRINITY's own terms, not merely on authored "
    "intent: Aura::CalcMaxCharges (SpellAuras.cpp:1004-1015) reads SpellInfo::ProcCharges "
    "unconditionally and only *overrides* it when a SpellProcEntry exists, and Aura::Aura "
    "then sets m_procCharges / m_isUsingCharges (SpellAuras.cpp:498-499).  The application "
    "is therefore born with a mutable, consumable counter with no proc entry anywhere.",
    "trinity", ids)

# 5 -- infinite-loop guard
ids = pick(lambda v: v.family == "guarded-proc")
add(5, "No entry only because the infinite-loop guard fired",
    "These providers have BOTH nonzero ProcFlags and a triggering aura subtype (42 = "
    "SPELL_AURA_PROC_TRIGGER_SPELL in every case).  Trinity refuses to synthesise their "
    "entry (SpellMgr.cpp:1893-1902) and logs 'data in `spell_proc` table is required for "
    "it to function'.  'No entry' here means 'a proc Trinity declined to generate', the "
    "opposite of inert.",
    "trinity", ids)

# 6 -- scripts
ids = pick(lambda v: v.family == "script-driven")
add(6, "spell_script_names binding with no generated entry",
    "An AuraScript can install proc hooks, charges, timers or any other per-application "
    "state with no DBC evidence at all (ObjectMgr::LoadSpellScriptNames).  A DBC-only "
    "predicate cannot see it, so 'no entry' proves nothing about these providers.",
    "unknown", ids[:60], note="all_ids truncated to the first 60 of %d" % len(ids))
CE[-1]["all_ids"] = ids
CE[-1]["count"] = len(ids)

# 7 -- cumulative > 1 on a non-passive
ids = pick(lambda v: v.aura_options and v.aura_options.cumulative_aura > 1
           and not v.has_effective_entry and not v.is_passive)
add(7, "CumulativeAura > 1 on a NON-passive provider",
    "The second half of the hypothesis (max stack capacity is not initial stacks) rests on "
    "SpellInfo::IsMultiSlotAura() being true for passives (SpellInfo.cpp:1808-1811), which "
    "makes Unit::_TryStackingOrRefreshingExistingAura (Unit.cpp:3395) skip the stacking "
    "path entirely.  Drop the Passive premise and the argument collapses: a re-applied "
    "non-passive reaches Unit.cpp:3441 foundAura->ModStackAmount(...) and climbs to the cap.",
    "trinity", ids)

# 8 -- cumulative > 1 AND charges
ids = pick(lambda v: v.aura_options and v.aura_options.cumulative_aura > 1
           and v.aura_options.proc_charges)
add(8, "CumulativeAura > 1 together with ProcCharges != 0",
    "Aura::ConsumeProcCharges (SpellAuras.cpp:1819-1831) turns a proc into "
    "ModStackAmount(-1) when the entry carries PROC_ATTR_USE_STACKS_FOR_CHARGES, so the "
    "stack cap becomes the charge pool.  The two fields are not independent and neither "
    "may be read as inert while the other is nonzero.",
    "trinity", ids)

# 9 -- cumulative > 2
ids = pick(lambda v: v.aura_options and v.aura_options.cumulative_aura > 2
           and not v.has_effective_entry)
add(9, "CumulativeAura far above 2 (up to 99) with no entry",
    "Phalanx's CumulativeAura 2 is not a small special case: the selected population "
    "carries caps of 3, 4, 5, 6, 8, 20 and 99 under the same 'no effective proc' "
    "condition.  Any rule that admits 2 must admit 99 or explain the difference; there is "
    "no source fact that distinguishes them (Aura::CalcMaxStackAmount, "
    "SpellAuras.cpp:1083-1090, treats them identically).",
    "source", ids)

# 10 -- nonzero mask AND a proc-triggering subtype
ids = pick(lambda v: v.aura_options and v.aura_options.proc_type_mask
           and v.proc_triggering_subtypes)
add(10, "Nonzero ProcTypeMask AND at least one proc-triggering aura subtype",
    "The exact configuration Core's ModAutoAttackCritChance catalog text says Heart of the "
    "Crusader does NOT have.  Every one of these generates an entry, so the predicate "
    "rejects them -- which is the point: Core's stated reason for Heart ('none of the "
    "provider's aura types is proc-triggering') is load-bearing and, on this population, "
    "sufficient.",
    "trinity", ids[:40], note="listed as the confirming population, not as a failure; "
                             "all of these are correctly rejected")
CE[-1]["all_ids"] = ids
CE[-1]["count"] = len(ids)

# 11 -- fail-closed unknowns
ids = pick(lambda v: v.family == "unknown")
add(11, "Aura subtype absent from Core's own AuraSubtypeKind catalog",
    "A provider whose aura subtype Core does not catalogue cannot be proved proc-free, "
    "because 'is this subtype in isTriggerAura[]' is answered against Trinity's table "
    "while admission is decided against Core's.  Build skew between the two is the "
    "failure mode; these fail closed.",
    "unknown", ids[:40], note="all_ids truncated to the first 40 of %d" % len(ids))
CE[-1]["all_ids"] = ids
CE[-1]["count"] = len(ids)

# 12 -- crowd control (empty, but proved empty)
ids = pick(lambda v: v.aura_options and v.aura_options.proc_type_mask
           and not v.has_effective_entry and (CROWD_CONTROL_AMOUNT_AURAS & set(v.aura_subtypes)))
add(12, "Nonzero ProcTypeMask read OUTSIDE the proc pipeline (crowd-control amount override)",
    "AuraEffect::CalculateAmount (SpellAuraEffects.cpp:786-798) reads "
    "SpellInfo::ProcFlags with no SpellProcEntry, for MOD_CONFUSE 5, MOD_FEAR 7, "
    "MOD_STUN 12, MOD_ROOT 26, TRANSFORM 56 and MOD_ROOT_2 455: a nonzero mask replaces "
    "the effect amount with 10% of max health.  The population is EMPTY here, so the "
    "predicate is safe on this snapshot -- but the check must stay, because it is the one "
    "place where ProcTypeMask is not inert without an entry.",
    "trinity", ids, note="empty on the pinned snapshot; retained as a standing guard")

# 13 -- overlay row but no entry
ids = pick(lambda v: v.has_spell_proc_overlay and not v.has_effective_entry)
add(13, "World-DB spell_proc row that adds behaviour Core's DBC-only view cannot see",
    "Searched for providers with a spell_proc overlay row that does not itself produce the "
    "effective entry.  Empty: every overlay row in the selected population resolves into "
    "an entry, so the overlay never adds hidden behaviour beyond D-CE-01.",
    "trinity", ids, note="empty on the pinned snapshot")

payload = {
    "provenance": {
        "predicate_under_test":
            "AuraOptions present + effective proc generation NONE => inert for immutable "
            "passive admission",
        "verdict": "FALSIFIED as stated; survives with three added conjuncts "
                   "(no ProcCharges, no ProcCategoryRecovery, no SpellProcsPerMinuteID) "
                   "plus the script / overlay / catalog fail-closed guards",
        "population": "every provider spell of the resolvable selected entries",
        "distinct_providers": len(providers),
        "reachable_measured_against":
            "selected_package.effects.classify_package + selected_package.owner.Owners.policy "
            "as of this run; the lead's substrate moves, so this field is a snapshot",
        "generated": datetime.date.today().isoformat(),
    },
    "counterexamples": CE,
    "totals": {
        "counterexample_classes": len(CE),
        "non_empty_classes": sum(1 for c in CE if c["count"]),
        "distinct_providers_implicated": len({s for c in CE for s in c["all_ids"]}),
        "reachable_implicated": len({s for c in CE for s in c["all_ids"] if reachable(s)}),
    },
}
(CORPORA / "proc-counterexamples.json").write_text(
    json.dumps(payload, indent=1, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
print("wrote proc-counterexamples.json")
for c in CE:
    print(f"  {c['id']}  n={c['count']:4d}  reachable={c['reachable_count']:3d}  {c['title'][:72]}")
print(json.dumps(payload["totals"], indent=1))
