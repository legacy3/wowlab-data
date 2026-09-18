"""Track G2 sole-effect admitted census -> docs/research/selected-package-corpora/sole-authorities.json"""
import json, sys
from collections import Counter, defaultdict

sys.path.insert(0, "/home/dev/pallet/wowlab-data/scripts/research")
from selected_package import CORPORA, coreref
from selected_package.provenance import Provenance, TraitSpellError
from selected_package.soleauthority import (
    SOLE_AUTHORITIES, PACKAGE_ONLY_SUBTYPES, UNREACHABLE_SELECTED_SUBTYPES,
    admit_sole_effect,
)

p = Provenance()
entries = sorted(p.traits.entries)

FAMILIES = ("amount", "identity", "metadata", "owner", "source", "structure",
            "fail-closed", "no-sole-authority")

def family(blocker: str) -> str:
    head = blocker.split(":", 1)[0]
    if head.startswith("owner["):
        return "owner"
    return head if head in FAMILIES else "other"

resolvable_entries = set()
variants = 0
sole_variants = 0
admitted_total = 0
per_authority = {s: {"admitted": 0, "examples": [], "candidates": 0,
                     "near_miss_blockers": Counter(), "near_misses": 0,
                     "blocker_families": Counter(), "blockers": Counter()}
                 for s in SOLE_AUTHORITIES}
unclaimed = defaultdict(Counter)       # subtype -> Counter(entry) ... keep counts
unclaimed_examples = defaultdict(list)
package_only_counts = Counter()
unreachable_counts = Counter()
multi_effect_variants = 0
admitted_by_role = Counter()

for entry_id in entries:
    entry = p.traits.entry(entry_id)
    for rank in range(1, (entry.max_ranks or 0) + 1):
        r = p.resolve(entry_id, rank)
        if isinstance(r, TraitSpellError):
            continue
        resolvable_entries.add(entry_id)
        variants += 1
        if len(r.effects) != 1:
            multi_effect_variants += 1
            continue
        sole_variants += 1
        subtype = r.effects[0].aura
        role, blockers = admit_sole_effect(r)
        if subtype in SOLE_AUTHORITIES:
            bucket = per_authority[subtype]
            bucket["candidates"] += 1
            if role is not None:
                bucket["admitted"] += 1
                admitted_total += 1
                admitted_by_role[role] += 1
                if len(bucket["examples"]) < 20:
                    bucket["examples"].append({
                        "entry": entry_id, "definition": r.definition_id,
                        "provider": r.spell, "rank": rank, "max_ranks": r.max_ranks,
                        "authored": r.authored(1), "effective_amount": r.selected_amount(1),
                        "point_operation": r.point_operation(1),
                        "misc_0": r.effects[0].misc0, "misc_1": r.effects[0].misc1,
                    })
            else:
                fams = {family(b) for b in blockers}
                for b in blockers:
                    bucket["blockers"][b] += 1
                for f in sorted(fams):
                    bucket["blocker_families"][f] += 1
                if fams <= {"amount"}:
                    bucket["near_misses"] += 1
                    for b in blockers:
                        bucket["near_miss_blockers"][b] += 1
        elif subtype in PACKAGE_ONLY_SUBTYPES:
            package_only_counts[subtype] += 1
        elif subtype in UNREACHABLE_SELECTED_SUBTYPES:
            unreachable_counts[subtype] += 1
        else:
            unclaimed[subtype]["variants"] += 1
            unclaimed[subtype]["apply_aura" if r.effects[0].kind == 6 else "other_kind"] += 1
            if len(unclaimed_examples[subtype]) < 10:
                unclaimed_examples[subtype].append(
                    {"entry": entry_id, "rank": rank, "provider": r.spell,
                     "effect": r.effects[0].ref, "kind": r.effects[0].kind,
                     "misc_0": r.effects[0].misc0, "misc_1": r.effects[0].misc1,
                     "max_ranks": r.max_ranks})

def top(counter, n=25):
    return [{"blocker": k, "count": v} for k, v in counter.most_common(n)]

payload = {
    "pin": {
        "core": "b1714eda2b4b9393853f94c6517e78cce2dfa21a",
        "tables": "wowlab-data/data/tables",
    },
    "population": {
        "trait_node_entries": len(entries),
        "resolvable_entries": len(resolvable_entries),
        "resolvable_entry_rank_variants": variants,
        "sole_effect_variants": sole_variants,
        "multi_effect_variants": multi_effect_variants,
    },
    "totals": {
        "sole_effect_admitted": admitted_total,
        "admitted_by_role": dict(sorted(admitted_by_role.items())),
        "sole_effect_refused": sole_variants - admitted_total,
    },
    "authorities": {
        str(s): {
            **SOLE_AUTHORITIES[s].to_dict(),
            "candidates": per_authority[s]["candidates"],
            "admitted": per_authority[s]["admitted"],
            "refused": per_authority[s]["candidates"] - per_authority[s]["admitted"],
            "near_misses_amount_only": per_authority[s]["near_misses"],
            "near_miss_blockers": top(per_authority[s]["near_miss_blockers"]),
            "refusal_families": dict(sorted(per_authority[s]["blocker_families"].items())),
            "top_blockers": top(per_authority[s]["blockers"]),
            "examples": per_authority[s]["examples"],
        }
        for s in sorted(SOLE_AUTHORITIES)
    },
    "claimed_but_package_only": {
        str(s): {"reason": PACKAGE_ONLY_SUBTYPES[s],
                 "sole_effect_variants": package_only_counts.get(s, 0)}
        for s in sorted(PACKAGE_ONLY_SUBTYPES)
    },
    "selected_activation_unreachable": {
        str(s): {"reason": UNREACHABLE_SELECTED_SUBTYPES[s],
                 "sole_effect_variants": unreachable_counts.get(s, 0)}
        for s in sorted(UNREACHABLE_SELECTED_SUBTYPES)
    },
    "unclaimed_expansion_surface": {
        str(s): {"sole_effect_variants": unclaimed[s]["variants"],
                 "apply_aura_variants": unclaimed[s]["apply_aura"],
                 "non_apply_aura_variants": unclaimed[s]["other_kind"],
                 "core_subtype_support": coreref.aura_support(s),
                 "core_subtype_name": (coreref.aura_subtypes().get(s).name
                                       if coreref.aura_subtypes().get(s) else None),
                 "examples": unclaimed_examples[s]}
        for s in sorted(unclaimed, key=lambda k: (-unclaimed[k]["variants"], k))
    },
    "uniform_rule_delta": {
        "claim": "one uniform sole-effect rule, parameterised only by per-role selector and "
                 "sign/range facts, reproduces Core's eight branches on 4859 of 4864 "
                 "sole-effect variants",
        "core_admitted": 184,
        "uniform_admitted": 181,
        "uniform_misses": [
            {"entry": 100635, "rank": 1, "subtype": 290, "provider": 378004,
             "reason": "owner attributes 4 and 416 are catalog-disabled; only "
                       "critical_modifier.rs:919-943 tolerates them"},
            {"entry": 100635, "rank": 2, "subtype": 290, "provider": 378004,
             "reason": "owner attributes 4 and 416 are catalog-disabled"},
            {"entry": 126473, "rank": 1, "subtype": 290, "provider": 378004,
             "reason": "owner attributes 4 and 416 are catalog-disabled"},
            {"entry": 126473, "rank": 2, "subtype": 290, "provider": 378004,
             "reason": "owner attributes 4 and 416 are catalog-disabled"},
        ],
        "uniform_extras": [
            {"entry": 112197, "rank": 1, "subtype": 319, "provider": 390354,
             "reason": "semantically identical to Furious Blows; Core refuses it only on "
                       "the entry 116954 / definition 121966 name"},
        ],
    },
    "unclaimed_by_core_support": {
        cls: {
            "subtypes": sorted(s for s in unclaimed if coreref.aura_support(s) == cls),
            "variants": sum(unclaimed[s]["variants"] for s in unclaimed
                            if coreref.aura_support(s) == cls),
        }
        for cls in sorted({coreref.aura_support(s) for s in unclaimed})
    },
    "unclaimed_total_variants": sum(unclaimed[s]["variants"] for s in unclaimed),
    "unclaimed_distinct_subtypes": len(unclaimed),
}

out = CORPORA / "sole-authorities.json"
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(payload, indent=1, sort_keys=True, ensure_ascii=False) + "\n",
               encoding="utf-8")
print("wrote", out)
print("sole-effect variants:", sole_variants, "admitted:", admitted_total)
for s in sorted(SOLE_AUTHORITIES):
    a = per_authority[s]
    print(f"  {s:>4}  cand={a['candidates']:>5}  admitted={a['admitted']:>5}  "
          f"near-miss(amount-only)={a['near_misses']:>4}")
print("package-only:", dict(package_only_counts))
print("unreachable:", dict(unreachable_counts))
print("unclaimed subtypes:", len(unclaimed), "variants:",
      sum(unclaimed[s]['variants'] for s in unclaimed))
print("top unclaimed:", [(s, unclaimed[s]['variants']) for s in
                         sorted(unclaimed, key=lambda k: -unclaimed[k]['variants'])[:20]])
