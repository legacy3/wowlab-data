"""Falsification commands: false-positives, false-negatives, ledger, unknowns, policies."""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from .cli import emit
from .effects import SELECTED_AUTHORITIES, classify_package
from .owner import POLICY_CLASSES
from .provenance import TraitSpellError
from .rule import (RULES, classify_false_positive, core_admitted_shapes, score)
from .cmd_core import context
from . import coreref


#: Every (entry, rank) on which a candidate dimension disagrees with Core, adjudicated.
#: ``fault`` says which side is wrong.  Nothing here is a judgement call without evidence.
ADJUDICATIONS: dict[tuple[int, int], dict[str, str]] = {
    (101510, 1): {
        "fault": "rule", "package": "Improved Vivify",
        "finding": "effect 1 is Set-shaped 40 -> 20/40 while effect 2 keeps its authored 40 "
                   "unshaped, so the siblings carry different values at rank 1",
        "conclusion": "mixed shaped/unshaped siblings are legitimate source authoring; the "
                      "`shaping_uniformity` dimension is refuted",
        "evidence": "source: TraitDefinitionEffectPoints row 21888, curve 62007 [(1,20),(2,40)]",
    },
    (101510, 2): {
        "fault": "rule", "package": "Improved Vivify",
        "finding": "same package at rank 2",
        "conclusion": "see rank 1",
        "evidence": "source: TraitDefinitionEffectPoints row 21888, curve 62007",
    },
    **{(entry, rank): {
        "fault": "contested", "package": "Keen Eyesight, raw-290 (provider 378004)",
        "finding": "the provider carries owner attributes 4 (Ability) and 416 "
                   "(AllowClassAbilityProcs), both catalogued `disabled` in Core's own "
                   "SPELL_ATTRIBUTE_REQUIREMENTS. Core admits it anyway because "
                   "critical_modifier.rs:936 uses a three-name blacklist instead of the "
                   "catalog check that passive_spell_modifier.rs applies",
        "conclusion": "CONTESTED, and the report must not claim otherwise. Core is "
                      "inconsistent -- the same attribute blocks a spell-modifier package "
                      "and passes a critical-chance package -- but the rule is ALSO wrong "
                      "to block on attribute 416: Trinity gates the entire proc-attribute "
                      "block behind `if (!procEntry) return 0;` in Aura::GetProcEffectMask, "
                      "so AllowClassAbilityProcs is unreachable for a proc-inert Passive "
                      "owner, and track C's sidecars.py already encodes that while owner.py "
                      "does not. Attribute 4 (`Is Ability`) is a SEPARATE question the pass "
                      "did not settle: Core catalogues it disabled for recovery-haste "
                      "classification, which a permanent passive with no cooldown cannot "
                      "exercise, but no consumer evidence was gathered. Fault is shared and "
                      "the case stays open",
        "evidence": "core: spell_attribute.rs:169,350,479; critical_modifier.rs:936; "
                    "trinity: Aura::GetProcEffectMask SpellAuras.cpp:1834-1839",
    } for entry, rank in ((100635, 1), (100635, 2), (126473, 1), (126473, 2))},
    (115942, 1): {
        "fault": "core", "package": "Pyrogenics (provider 387095)",
        "finding": "Core admits it as an immutable raw-219 flat-label passive because the "
                   "provider carries NO SpellAuraOptions row, so every DBC-only owner check "
                   "passes; but the world DB supplies a spell_proc row (proc_flags 327680, "
                   "chance 100) and spell_script_names binds spell_warl_pyrogenics with an "
                   "OnEffectProc hook",
        "conclusion": "a Core over-admission, but a CONTESTED one: hostile review 2 showed "
                      "that spell_warl_pyrogenics only casts a debuff on the proc target and "
                      "never touches the compiled effect's amount, stacks, charges or "
                      "lifetime, so the compiled modifier value is not itself wrong today. "
                      "What Core admits is nonetheless a provider whose real lifecycle "
                      "includes proc state it cannot see, and the proc ICD/charge fields "
                      "that DO gate mutable state are invisible to the same check. The "
                      "`proc_policy` dimension is retained on that basis, not on the "
                      "stronger claim that the compiled value is wrong",
        "evidence": "trinity: SpellMgr::LoadSpellProcs (SpellMgr.cpp:1497-1658); world: "
                    "spell_proc + spell_script_names overlay rows",
    },
}


def _out_only(parser) -> None:
    parser.add_argument("--out")


def _rule_arg(parser) -> None:
    parser.add_argument("--rule", default="v1", help="rule version (default v1)")
    parser.add_argument("--out")


def _rule(version: str):
    for rule in RULES:
        if rule.version == version:
            return rule
    from . import FailClosed
    raise FailClosed(f"unknown rule {version!r}; have {[r.version for r in RULES]}")


def run_false_positives(args) -> int:
    ctx = context()
    result = score(ctx, _rule(args.rule))
    shapes = core_admitted_shapes(ctx)
    records = [classify_false_positive(ctx, entry, rank, shapes)
               for entry, rank in result["_fp_keys"]]
    emit({
        "rule": result["rule"],
        "core_admitted": result["core_admitted"],
        "rule_admitted": result["rule_admitted"],
        "false_positives": len(records),
        "by_class": dict(Counter(r["class"] for r in records).most_common()),
        "by_nearest_branch": dict(Counter(str(r.get("nearest_branch")) for r in records).most_common()),
        "records": sorted(records, key=lambda r: (r["entry"], r["rank"])),
    }, args.out)
    return 0


def run_false_negatives(args) -> int:
    """Packages Core admits that the rule refuses -- each one is a rule defect until explained."""
    ctx = context()
    result = score(ctx, _rule(args.rule))
    records = []
    for entry, rank in result["_fn_keys"]:
        verdict = result["_verdicts"][(entry, rank)]
        adjudication = ADJUDICATIONS.get((entry, rank))
        records.append({"entry": entry, "rank": rank, "provider": verdict.provider,
                        "core_branch": result["_core_branch"][(entry, rank)],
                        "rule_blockers": sorted(verdict.blockers),
                        "dimensions": verdict.dimensions,
                        # The adjudication is data, not prose: a false negative with no
                        # `fault` is an unexplained disagreement and falsifies the rule.
                        "fault": (adjudication or {}).get("fault", "UNADJUDICATED"),
                        "adjudication": adjudication})
    emit({"rule": result["rule"], "false_negatives": len(records),
          "by_core_branch": dict(Counter(r["core_branch"] for r in records).most_common()),
          "by_fault": dict(Counter(r["fault"] for r in records).most_common()),
          "records": sorted(records, key=lambda r: (r["entry"], r["rank"]))}, args.out)
    return 0


def run_reachable(args) -> int:
    """The score restricted to entries a host can actually select.

    A detached entry -- one with no ``TraitNodeXTraitNodeEntry`` row -- is unreachable
    through ``TraitMgr::IsValidEntry`` (TrinityCore TraitMgr.cpp:822-836), which resolves a
    selection through its node.  Core admits such entries because it never consults tree
    topology, so the headline score counts packages no player can take.  Both scores belong
    in the report; this command produces the reachable one.
    """
    ctx = context()
    rule = _rule(args.rule)
    result = score(ctx, rule)
    attached = {entry for entry in ctx.provenance.traits.entries
                if ctx.provenance.traits.nodes_for_entry.get(entry)}
    core = {k for k in result["_core_branch"]}
    admitted = {k for k, v in result["_verdicts"].items() if v.admitted}
    reachable = lambda keys: {k for k in keys if k[0] in attached}
    emit({
        "rule": result["rule"],
        "all_entries": {"core_admitted": len(core), "rule_admitted": len(admitted),
                        "false_positives": len(admitted - core),
                        "false_negatives": len(core - admitted)},
        "host_selectable_only": {
            "core_admitted": len(reachable(core)),
            "rule_admitted": len(reachable(admitted)),
            "false_positives": len(reachable(admitted - core)),
            "false_negatives": len(reachable(core - admitted)),
        },
        "detached": {"core_admitted": len(core) - len(reachable(core)),
                     "rule_admitted": len(admitted) - len(reachable(admitted)),
                     "false_positives": len((admitted - core)) - len(reachable(admitted - core))},
    }, args.out)
    return 0


def run_ledger(args) -> int:
    """The falsification ledger: every rule iteration and what each dimension did."""
    ctx = context()
    shapes = core_admitted_shapes(ctx)
    iterations = []
    previous = None
    for rule in RULES:
        result = score(ctx, rule)
        records = [classify_false_positive(ctx, e, r, shapes) for e, r in result["_fp_keys"]]
        entry: dict[str, Any] = {
            "version": rule.version,
            "summary": rule.summary,
            "dimensions": list(rule.dimensions),
            "dimension_added": ([d for d in rule.dimensions if d not in previous.dimensions]
                                if previous else list(rule.dimensions)),
            "population": result["population"],
            "core_admitted": result["core_admitted"],
            "rule_admitted": result["rule_admitted"],
            "false_positives": result["false_positives"],
            "false_negatives": result["false_negatives"],
            "agreement": result["agreement"],
            "false_positive_classes": dict(Counter(r["class"] for r in records).most_common()),
            "false_negative_branches": dict(Counter(
                result["_core_branch"][k] for k in result["_fn_keys"]).most_common()),
        }
        # A false negative means the rule refuses something Core admits.  That is NOT
        # automatically a defect in the rule: it can equally mean Core admits something it
        # should not.  Each one is adjudicated individually against ADJUDICATIONS below;
        # an unadjudicated false negative falsifies the dimension by default, because an
        # unexplained disagreement with the audited implementation is a research failure.
        unadjudicated = [k for k in result["_fn_keys"] if k not in ADJUDICATIONS]
        entry["false_negative_adjudications"] = [
            {"entry": k[0], "rank": k[1], **ADJUDICATIONS[k]}
            for k in result["_fn_keys"] if k in ADJUDICATIONS]
        entry["unadjudicated_false_negatives"] = [list(k) for k in unadjudicated]
        if rule.version.endswith("x") and rule.version != "v2x":
            entry["verdict"] = (
                "MEASUREMENT, not a candidate rule: its false negatives size how much of "
                "Core's admitted population provider-only package identity cannot "
                "distinguish; they are the finding, not a defect")
        elif unadjudicated:
            entry["verdict"] = ("FALSIFIED: refuses packages Core admits, unexplained")
        elif any(ADJUDICATIONS[k]["fault"] == "rule" for k in result["_fn_keys"]):
            entry["verdict"] = ("FALSIFIED: the added dimension is not a real source "
                                "requirement; Core's own population refutes it")
        elif result["false_negatives"]:
            faults = sorted({ADJUDICATIONS[k]["fault"] for k in result["_fn_keys"]})
            entry["verdict"] = ("retained; every disagreement is adjudicated with evidence "
                                f"(faults: {', '.join(faults)}) -- 'contested' means the "
                                "evidence does not settle which side is wrong, and the case "
                                "stays open rather than being scored against Core")
        else:
            entry["verdict"] = "retained"
        iterations.append(entry)
        previous = rule
    emit({"iterations": iterations,
          "note": ("a rule that merely reproduces Core's positives is not a result; the "
                   "false-positive classes are where the evidence is")}, args.out)
    return 0


def run_owner_policy(args) -> int:
    ctx = context()
    families: dict[tuple, dict[str, Any]] = {}
    per_class: Counter[str] = Counter()
    providers = set()
    for entry, rank in ctx.variants():
        resolved = ctx.provenance.resolve(entry, rank)
        if isinstance(resolved, TraitSpellError) or resolved.spell in providers:
            continue
        providers.add(resolved.spell)
        policy = ctx.owners.policy(resolved.spell)
        key = tuple(sorted({s.table for s in policy.sidecars}))
        record = families.setdefault(key, {"tables": list(key), "providers": 0,
                                           "examples": [], "blocked": 0})
        record["providers"] += 1
        if policy.blockers:
            record["blocked"] += 1
        if len(record["examples"]) < 20:
            record["examples"].append(resolved.spell)
        for sidecar in policy.sidecars:
            per_class[sidecar.policy_class] += 1
    emit({"policy_classes": POLICY_CLASSES,
          "distinct_providers": len(providers),
          "sidecar_rows_by_policy_class": {k: per_class[k] for k in sorted(per_class)},
          "sidecar_families": sorted(families.values(),
                                     key=lambda r: (-r["providers"], r["tables"]))}, args.out)
    return 0


def run_proc_policy(args) -> int:
    ctx = context()
    proc = ctx.proc
    if proc is None:
        from . import FailClosed
        raise FailClosed("selected_package.procpolicy is not present (track D deliverable)")
    rows, providers = [], set()
    for entry, rank in ctx.variants():
        resolved = ctx.provenance.resolve(entry, rank)
        if isinstance(resolved, TraitSpellError) or resolved.spell in providers:
            continue
        providers.add(resolved.spell)
        rows.append(proc.classify(resolved.spell).to_dict())
    emit({"providers": len(rows),
          "by_family": dict(Counter(r.get("family", "?") for r in rows).most_common()),
          "rows": rows}, args.out)
    return 0


def run_unknowns(args) -> int:
    """Every unresolved item: stable id, source coordinates, evidence class, blocker, reopen."""
    ctx = context()
    unknowns: list[dict[str, Any]] = []
    seen_subtypes: dict[int, dict[str, Any]] = {}
    attributes: dict[int, dict[str, Any]] = {}

    for entry, rank in ctx.variants():
        resolved = ctx.provenance.resolve(entry, rank)
        if isinstance(resolved, TraitSpellError):
            continue
        for classification in classify_package(resolved):
            effect = classification.effect
            if classification.role is None and effect.aura:
                record = seen_subtypes.setdefault(effect.aura, {
                    "id": f"SP-AURA-{effect.aura:04d}",
                    "what": f"aura subtype {effect.aura} appears on a selected provider "
                            "but has no selected-passive role in Core",
                    "core_support": coreref.aura_support(effect.aura),
                    "evidence_class": "core" if coreref.aura_support(effect.aura) != "unknown"
                                      else "unknown",
                    "packages": 0, "examples": [],
                })
                record["packages"] += 1
                if len(record["examples"]) < 10:
                    record["examples"].append({"entry": entry, "rank": rank, "effect": effect.ref})
        policy = ctx.owners.policy(resolved.spell)
        for attribute in policy.attributes:
            support = coreref.attribute_support(attribute)
            if support == "unknown":
                record = attributes.setdefault(attribute, {
                    "id": f"SP-ATTR-{attribute:04d}",
                    "what": f"owner spell attribute {attribute} is absent from Core's catalog",
                    "evidence_class": "unknown", "providers": set(),
                })
                record["providers"].add(resolved.spell)

    for record in seen_subtypes.values():
        support = record["core_support"]
        record["blocker"] = (f"no Core consumer for aura subtype; Core's catalog class is "
                             f"'{support}'")
        record["reopen"] = ("the subtype gains an implemented Core semantic, or Core's catalog "
                            "reclassifies it")
        unknowns.append(record)
    for attribute in attributes.values():
        attribute["providers"] = sorted(attribute["providers"])
        attribute["blocker"] = "uncataloged owner attribute; admission fails closed"
        attribute["reopen"] = "the attribute is added to SPELL_ATTRIBUTE_REQUIREMENTS"
        unknowns.append(attribute)

    emit({"unknowns": len(unknowns),
          "by_evidence_class": dict(Counter(u["evidence_class"] for u in unknowns).most_common()),
          "records": sorted(unknowns, key=lambda u: u["id"])}, args.out)
    return 0


def run_authorities(args) -> int:
    """The semantic role universe: every authority a selected effect can enter."""
    emit({"authorities": {str(raw): {"role": role, "compiler": compiler,
                                     "in_multi_effect_package": multi,
                                     "core_support": coreref.aura_support(raw)}
                          for raw, (role, compiler, multi)
                          in sorted(SELECTED_AUTHORITIES.items())},
          "spell_modifier_subtypes": {"107": "AddFlatModifier", "108": "AddPercentModifier",
                                      "218": "AddPercentLabelModifier",
                                      "219": "AddFlatLabelModifier"}}, args.out)
    return 0


COMMANDS = {
    "reachable": ("the score restricted to host-selectable (non-detached) entries",
                  _rule_arg, run_reachable),
    "false-positives": ("packages a candidate rule admits that Core does not, each classified",
                        _rule_arg, run_false_positives),
    "false-negatives": ("packages Core admits that a candidate rule refuses", _rule_arg,
                        run_false_negatives),
    "ledger": ("the falsification ledger: every rule iteration and its effect", _out_only,
               run_ledger),
    "owner-policy": ("provider sidecar families and their policy classes", _out_only,
                     run_owner_policy),
    "proc-policy": ("effective proc policy per selected provider (needs procpolicy.py)",
                    _out_only, run_proc_policy),
    "unknowns": ("every unresolved item with blocker and reopen condition", _out_only,
                 run_unknowns),
    "authorities": ("the semantic role universe", _out_only, run_authorities),
}
