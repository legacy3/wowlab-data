"""Core oracle commands: explain, classify, census, admitted.

``explain`` is the fail-closed per-entry view the mission specifies: exact provenance,
the COMPLETE effect set, per-effect classification, rank shaping, owner policy, effective
proc policy, lifecycle classification, candidate package identity, atomicity domain,
verdict, and the exact blocker or reopen condition.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from . import CORPORA, FailClosed
from .cli import emit
from .corebaseline import NAMED_IDENTITIES
from .effects import classify_package
from .provenance import TraitSpellError
from .rule import Context, RULES, core_admitted_shapes

_CTX: Context | None = None


def context() -> Context:
    global _CTX
    if _CTX is None:
        _CTX = Context()
    return _CTX


def _entry_rank(parser) -> None:
    parser.add_argument("entry", type=int)
    parser.add_argument("rank", type=int, nargs="?", default=1)
    parser.add_argument("--out")


def _out_only(parser) -> None:
    parser.add_argument("--out")


def explain_payload(ctx: Context, entry: int, rank: int) -> dict[str, Any]:
    traits = ctx.provenance.traits
    source_entry = traits.entry(entry)
    if source_entry is None:
        raise FailClosed(f"no TraitNodeEntry row {entry}")
    definition = traits.definition(source_entry.definition_id)
    resolved = ctx.provenance.resolve(entry, rank)

    payload: dict[str, Any] = {
        "entry": entry,
        "requested_rank": rank,
        "provenance": {
            "entry": {"id": source_entry.id, "definition_id": source_entry.definition_id,
                      "max_ranks": source_entry.max_ranks,
                      "node_entry_type": source_entry.node_entry_type,
                      "trait_subtree_id": source_entry.trait_subtree_id,
                      "nodes": sorted(traits.nodes_for_entry.get(entry, [])),
                      "detached": not traits.nodes_for_entry.get(entry)},
            "definition": None if definition is None else {
                "id": definition.id, "spell_id": definition.spell_id,
                "overrides_spell_id": definition.overrides_spell_id,
                "visible_spell_id": definition.visible_spell_id},
        },
    }

    if isinstance(resolved, TraitSpellError):
        payload["verdict"] = "refused"
        payload["blocker"] = f"source:{resolved.value}"
        payload["reopen"] = ("the source rows must satisfy "
                             "TraitSourceCatalog::try_selected_spell_effect_amounts")
        return payload

    cls = classify_package(resolved)
    owner = ctx.owners.policy(resolved.spell)
    payload["provenance"]["provider"] = resolved.spell
    payload["provenance"]["sibling_entries_for_provider"] = sorted(
        ctx.resolvable_entries_for_provider(resolved.spell))
    payload["provenance"]["sibling_definitions_for_provider"] = sorted(
        traits.definitions_for_spell.get(resolved.spell, []))
    payload["rank_shaping"] = {
        "max_ranks": resolved.max_ranks,
        "effective_rank": resolved.effective_rank,
        "effect_point_rows": resolved.point_rows,
        "per_effect": {str(e.index): {"operation": resolved.point_operation(e.index) or "none",
                                      "authored": resolved.authored(e.index),
                                      "rank_1": resolved.amount_at_rank(e.index, 1),
                                      "rank_2": resolved.amount_at_rank(e.index, 2),
                                      "selected": resolved.selected_amount(e.index)}
                       for e in resolved.effects},
    }
    payload["complete_effect_set"] = [c.to_dict() for c in cls]
    payload["owner_policy"] = owner.to_dict()

    proc = ctx.proc
    if proc is None:
        payload["proc_policy"] = {"available": False,
                                  "note": "selected_package.procpolicy not present; "
                                          "dimension not evaluated"}
    else:
        try:
            payload["proc_policy"] = proc.classify(resolved.spell).to_dict()
        except Exception as exc:                    # noqa: BLE001 - fail closed
            payload["proc_policy"] = {"available": False, "error": str(exc)}

    payload["lifecycle"] = {
        "unconditional_passive_application":
            ctx.baseline.unconditional_passive(resolved.spell),
        "policy_classes_present": sorted({s.policy_class for s in owner.sidecars}),
    }
    payload["candidate_identity"] = {
        "provider_only": resolved.spell,
        "entry_rank": [entry, rank],
        "definition_rank": [resolved.definition_id, rank],
        "provider_is_ambiguous":
            len(ctx.resolvable_entries_for_provider(resolved.spell)) > 1,
    }
    payload["atomicity_domain"] = {
        "effects": [e.ref for e in resolved.effects],
        "same_actor_all_effects_once": True,
        "caveat": ("holds only while every effect has self-only implicit targets and no "
                   "sibling splits by activation, script or child action; see the report's "
                   "atomicity section"),
        "non_self_delivery": [e.ref for e in resolved.effects
                              if (e.target_a, e.target_b) != (1, 0)],
    }

    core = ctx.baseline.admit_final(entry, rank)
    payload["core"] = {"admitted": not isinstance(core, str),
                       "branch": None if isinstance(core, str) else core.branch,
                       "refusal": core if isinstance(core, str) else None,
                       "named_identities_pinned":
                           None if isinstance(core, str)
                           else NAMED_IDENTITIES.get(core.branch)}
    payload["rules"] = {}
    for rule in RULES:
        verdict = rule.evaluate(ctx, entry, rank)
        payload["rules"][rule.version] = verdict.to_dict()

    final = RULES[0].evaluate(ctx, entry, rank)
    payload["verdict"] = "admitted" if final.admitted else "refused"
    payload["blocker"] = None if final.admitted else sorted(final.blockers)
    payload["reopen"] = None if final.admitted else (
        "each blocker names the exact missing semantic or consumer; it reopens when that "
        "semantic is implemented and audited in Core")
    return payload


def run_explain(args) -> int:
    emit(explain_payload(context(), args.entry, args.rank), args.out)
    return 0


def run_classify(args) -> int:
    ctx = context()
    resolved = ctx.provenance.resolve(args.entry, args.rank)
    if isinstance(resolved, TraitSpellError):
        raise FailClosed(f"entry {args.entry} rank {args.rank}: source:{resolved.value}")
    emit({"entry": args.entry, "rank": args.rank, "provider": resolved.spell,
          "effects": [c.to_dict() for c in classify_package(resolved)]}, args.out)
    return 0


def run_census(args) -> int:
    """Every selected provider and entry x rank variant, with its resolution outcome."""
    ctx = context()
    refusals: Counter[str] = Counter()
    resolved_entries, providers, variants = set(), set(), 0
    roles: Counter[str] = Counter()
    by_effect_count: Counter[int] = Counter()

    for entry, rank in ctx.variants():
        variants += 1
        result = ctx.provenance.resolve(entry, rank)
        if isinstance(result, TraitSpellError):
            refusals[result.value] += 1
            continue
        resolved_entries.add(entry)
        providers.add(result.spell)
        by_effect_count[len(result.effects)] += 1
        for classification in classify_package(result):
            roles[classification.role.key() if classification.role
                  else f"UNCLASSIFIED/aura-{classification.effect.aura}"] += 1

    emit({
        "entries_total": len(ctx.provenance.traits.entries),
        "entry_rank_variants": variants,
        "resolvable_entries": len(resolved_entries),
        "distinct_providers": len(providers),
        "source_refusals": dict(refusals.most_common()),
        "effect_count_distribution": {str(k): v for k, v in sorted(by_effect_count.items())},
        "effect_role_distribution": dict(roles.most_common()),
    }, args.out)
    return 0


def run_admitted(args) -> int:
    """Everything Core admits today, by branch, with the identities each branch pins."""
    ctx = context()
    shapes = core_admitted_shapes(ctx)
    rows = []
    for entry, rank in ctx.variants():
        decision = ctx.baseline.admit_final(entry, rank)
        if isinstance(decision, str):
            continue
        resolved = ctx.provenance.resolve(entry, rank)
        rows.append({"entry": entry, "rank": rank, "provider": resolved.spell,
                     "definition": resolved.definition_id, "branch": decision.branch,
                     "roles": decision.roles, "owner": decision.owner,
                     "detached": not ctx.provenance.traits.nodes_for_entry.get(entry)})
    emit({
        "admitted_entry_rank_variants": len(rows),
        "distinct_entries": len({r["entry"] for r in rows}),
        "distinct_providers": len({r["provider"] for r in rows}),
        "detached_admitted": sum(1 for r in rows if r["detached"]),
        "by_branch": dict(Counter(r["branch"] for r in rows).most_common()),
        "branch_shapes": {branch: {"count": shape["count"],
                                   "multisets": [list(m) for m in sorted(shape["multisets"])]}
                          for branch, shape in sorted(shapes.items())},
        "named_identities": NAMED_IDENTITIES,
        "rows": sorted(rows, key=lambda r: (r["entry"], r["rank"])),
    }, args.out)
    return 0


COMMANDS = {
    "explain": ("complete evidence for one entry x rank, fail-closed", _entry_rank, run_explain),
    "classify": ("per-effect classification for one entry x rank", _entry_rank, run_classify),
    "census": ("the whole selected-provider population and its resolution outcomes",
               _out_only, run_census),
    "admitted": ("everything Core admits today, by branch", _out_only, run_admitted),
}
