#!/usr/bin/env python3
"""Regenerate proc-policy.json (AuraOptions -> effective proc generation families).

Driven by ``tools/regen_selected_package.py --stage tracks``.
Reads ``selected_package.procpolicy``; writes into ``docs/research/selected-package-corpora``.
"""
import pathlib
import sys, json, collections
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from selected_package import CORPORA
from selected_package.provenance import Provenance, TraitSpellError
from selected_package.effects import classify_package
from selected_package.owner import Owners
from selected_package.procpolicy import (
    ProcPolicy, FAMILIES, FAMILY_ADMITS, shape_key, CORE_LITERALS,
    SPELL_MOD_OP_DOSES, SPELL_MOD_OP_MAX_AURA_STACKS)

p = Provenance()
pp = ProcPolicy(source=p.source)
ow = Owners(p.source)

# -- population -------------------------------------------------------------
providers = {}          # spell -> {"entries": [...], "variants": [(entry, rank)]}
for entry_id, entry in sorted(p.traits.entries.items()):
    for rank in range(1, max(entry.max_ranks, 1) + 1):
        r = p.resolve(entry_id, rank)
        if isinstance(r, TraitSpellError):
            continue
        rec = providers.setdefault(r.spell, {"entries": set(), "variants": []})
        rec["entries"].add(entry_id)
        rec["variants"].append((entry_id, rank))
# an entry is "resolvable" if any rank resolves
resolvable_entries = sorted({e for rec in providers.values() for e in rec["entries"]})

verdicts = {s: pp.classify(s) for s in sorted(providers)}

# -- "reachable as a selected package today" (lead's substrate) --------------
def package_ok(spell):
    """Does any (entry, rank) of this provider pass per-effect + owner policy?"""
    pol = ow.policy(spell)
    if pol.blockers:
        return False
    for entry_id, rank in providers[spell]["variants"]:
        r = p.resolve(entry_id, rank)
        if isinstance(r, TraitSpellError):
            continue
        if all(c.ok for c in classify_package(r)):
            return True
    return False

reach_cache = {}
def reachable(spell):
    if spell not in reach_cache:
        reach_cache[spell] = package_ok(spell)
    return reach_cache[spell]

# -- distribution -----------------------------------------------------------
shapes = collections.Counter(shape_key(v.aura_options) for v in verdicts.values())
shape_members = collections.defaultdict(list)
for s, v in verdicts.items():
    shape_members[shape_key(v.aura_options)].append(s)

families = {}
for name in FAMILIES:
    members = sorted(s for s, v in verdicts.items() if v.family == name)
    admits, evidence = FAMILY_ADMITS[name]
    families[name] = {
        "definition": FAMILIES[name],
        "count": len(members),
        "examples": members[:20],
        "permits_immutable_passive_admission": admits,
        "evidence_class": evidence,
        "members_with_empty_blockers": sum(1 for s in members if verdicts[s].inert),
        "members_runtime_inert": sum(1 for s in members if verdicts[s].runtime_inert),
        "members_reachable_as_selected_package_today":
            sorted(s for s in members if reachable(s))[:20],
    }

unknowns = []
for s in sorted(verdicts):
    v = verdicts[s]
    if v.family != "unknown":
        continue
    unknowns.append({
        "id": f"D-UNK-{s}",
        "spell": s,
        "name": v.name,
        "blockers": list(v.blockers),
        "aura_options": v.aura_options.to_dict() if v.aura_options else None,
        "uncatalogued_subtypes": list(v.uncatalogued_subtypes),
        "reopen": ("re-run when Core's AuraSubtypeKind catalog names the listed subtypes"
                   if v.uncatalogued_subtypes else
                   "re-run when the named source fact resolves in a newer snapshot"),
        "reachable_as_selected_package_today": reachable(s),
    })

payload = {
    "provenance": {
        "question": "Can Core's exact-literal SelectedAuraOptionsContract be replaced by a "
                    "semantically derived effective-proc predicate?",
        "core_symbol": "SelectedAuraOptionsContract / SelectedTraitAuraOptions "
                       "(crates/combat/src/program/selected_trait_package.rs:177-208, 270-300)",
        "trinity_symbol": "SpellMgr::LoadSpellProcs (SpellMgr.cpp:1571-1913), "
                          "SpellInfo::SpellInfo (SpellInfo.cpp:1384-1396)",
        "population": "every provider spell of the resolvable selected entries",
        "resolvable_entries": len(resolvable_entries),
        "distinct_providers": len(providers),
        "family_count": len(FAMILIES),
        "family_count_note":
            "11, one over the mission's soft aim of 10.  The suggested `inert-metadata` is "
            "SPLIT into `inert-no-proc-flags` and `inert-no-trigger-aura` because Trinity "
            "bails at two different places (SpellMgr.cpp:1765 vs 1807), because Core's own "
            "aura_subtype.rs catalog text cites only the second, and because the two Core "
            "witnesses sit one in each.  `guarded-proc` is separate because it has no entry "
            "yet is provably not inert.  `proc-icd` is separate because ProcCategoryRecovery "
            "is rejected on [authored] grounds while `charges` is rejected on [trinity] "
            "grounds; collapsing them would hide which argument does the work.",
    },
    "core_literals": {
        str(sid): {
            "literal": lit,
            "matches_source_row": verdicts[sid].aura_options.matches_core_literal(lit),
            "family": verdicts[sid].family,
            "blockers": list(verdicts[sid].blockers),
        } for sid, lit in CORE_LITERALS.items()
    },
    "aura_options_shape_distribution": {
        "distinct_shapes": len(shapes),
        "shapes": [
            {"shape": k, "count": c,
             "families": sorted({verdicts[s].family for s in shape_members[k]}),
             "examples": sorted(shape_members[k])[:12]}
            for k, c in sorted(shapes.items(), key=lambda kv: (-kv[1], kv[0]))
        ],
    },
    "families": families,
    "family_totals": {k: v["count"] for k, v in families.items()},
    "inert_providers": sum(1 for v in verdicts.values() if v.inert),
    "runtime_inert_providers": sum(1 for v in verdicts.values() if v.runtime_inert),
    "reachable_providers_today": sorted(s for s in verdicts if reachable(s)),
    "reachable_and_inert": sorted(s for s in verdicts if reachable(s) and verdicts[s].inert),
    "reachable_and_not_inert": {
        str(s): {"family": verdicts[s].family, "blockers": list(verdicts[s].blockers)}
        for s in sorted(verdicts) if reachable(s) and not verdicts[s].inert},
    "unknown_providers": unknowns,
    "stack_modifier_population": {
        "note": "the only two ways a stack count moves without a second application: "
                "SpellModOp::Doses (initial, Spell.cpp:506) and SpellModOp::MaxAuraStacks "
                "(cap, SpellAuras.cpp:1083-1090)",
        "doses_modifier_effects": len(pp.stack_mod_providers[SPELL_MOD_OP_DOSES]),
        "max_aura_stacks_modifier_effects": len(pp.stack_mod_providers[SPELL_MOD_OP_MAX_AURA_STACKS]),
        "doses_examples": [int(r["SpellID"]) for r in pp.stack_mod_providers[SPELL_MOD_OP_DOSES][:20]],
        "max_aura_stacks_examples": [int(r["SpellID"]) for r in pp.stack_mod_providers[SPELL_MOD_OP_MAX_AURA_STACKS][:20]],
    },
}

CORPORA.mkdir(parents=True, exist_ok=True)
(CORPORA / "proc-policy.json").write_text(
    json.dumps(payload, indent=1, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
print("wrote proc-policy.json")
print(json.dumps(payload["family_totals"], indent=1))
print("unknowns:", len(unknowns), "inert:", payload["inert_providers"])
print("reachable today:", sum(1 for s in verdicts if reachable(s)))

# stash for the counterexample pass
import pickle
with open("/tmp/claude-1000/-home-dev-pallet-wowlab-data/03ba12a1-2290-4708-ba14-e877ec138bca/scratchpad/state.pkl","wb") as f:
    pickle.dump({"providers": {k:{"entries":sorted(v["entries"]),"variants":v["variants"]} for k,v in providers.items()},
                 "reach": reach_cache}, f)
