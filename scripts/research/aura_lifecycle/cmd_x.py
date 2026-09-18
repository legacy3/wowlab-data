"""Lead commands: explain, model, unknowns, falsification."""

from __future__ import annotations

import json

from . import CORPORA, FailClosed, records
from .cli import emit


def _spell(p) -> None:
    p.add_argument("spell", type=int)
    p.add_argument("--out")


def _corpus(p) -> None:
    p.add_argument("--corpus", action="store_true", required=True)
    p.add_argument("--out")


def _explain(args) -> int:
    from .oracle import explain
    emit(explain(args.spell), args.out)
    return 0


def _model(args) -> int:
    from . import context, model
    payload = model.census(context.get())
    payload["provenance"] = records.provenance("aura_lifecycle.py model --corpus --out docs/research/aura-lifecycle-corpora/semantic-axes.json")
    emit(payload, args.out)
    return 0


def _unknowns(args) -> int:
    from .oracle import RECONCILIATION, merged
    m = merged()
    payload = {
        "provenance": records.provenance("aura_lifecycle.py unknowns --corpus --out docs/research/aura-lifecycle-corpora/unknowns.json"),
        "counts": {k: len(v) for k, v in m.items()},
        "unknowns": m["unknowns"],
        "trinity_defects": m["trinity_defects"],
        "core_navigation": m["core_navigation"],
        "retail_experiment_ids": [x["id"] for x in m["retail_experiments"]],
        "reconciliation": RECONCILIATION,
    }
    emit(payload, args.out)
    return 0


def _falsification(args) -> int:
    from .oracle import merged
    m = merged()
    path = CORPORA / "semantic-axes.json"
    if not path.exists():
        raise FailClosed("semantic-axes.json missing; run `model --corpus` first")
    lead = json.loads(path.read_text(encoding="utf-8"))["rules"]
    payload = {
        "provenance": records.provenance("aura_lifecycle.py falsification --corpus --out docs/research/aura-lifecycle-corpora/falsification.json"),
        "track_rules": m["rules"],
        "track_falsification": m["falsification"],
        "lead_rules": lead,
        "status_counts": {
            "track": dict(sorted({s: sum(1 for r in m["rules"] if r["status"] == s)
                                  for s in {r["status"] for r in m["rules"]}}.items())),
            "lead": dict(sorted({s: sum(1 for r in lead if r["status"] == s)
                                 for s in {r["status"] for r in lead}}.items())),
        },
    }
    emit(payload, args.out)
    return 0


COMMANDS = {
    "explain": ("lead: every track's view of one spell + matching unknowns / experiments", _spell, _explain),
    "model": ("lead: semantic axes per provider, state-space counts, redundancy, cross-track rules", _corpus, _model),
    "unknowns": ("lead: merged unknowns / defects / Core navigation of every track", _corpus, _unknowns),
    "falsification": ("lead: merged rules and falsification history (tracks + lead rules)", _corpus, _falsification),
}
