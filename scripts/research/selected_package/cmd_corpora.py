"""``corpora`` -- regenerate every corpus this package owns, deterministically."""

from __future__ import annotations

import argparse

from . import CORPORA
from .cmd_core import run_admitted, run_census
from .cmd_rule import (run_authorities, run_false_negatives, run_false_positives, run_ledger,
                       run_owner_policy, run_reachable, run_unknowns)

#: (filename, runner, extra args).  Order is dependency order.
CORPUS_FILES = [
    ("census.json", run_census, {}),
    ("authorities.json", run_authorities, {}),
    ("core-admitted.json", run_admitted, {}),
    ("owner-policy-families.json", run_owner_policy, {}),
    ("falsification-ledger.json", run_ledger, {}),
    ("false-positives.json", run_false_positives, {"rule": "v5"}),
    ("false-negatives.json", run_false_negatives, {"rule": "v5"}),
    ("reachable-score.json", run_reachable, {"rule": "v5"}),
    ("unknowns.json", run_unknowns, {}),
]


def _args(parser) -> None:
    parser.add_argument("--only", action="append", help="regenerate only these files")


def run(args) -> int:
    for name, runner, extra in CORPUS_FILES:
        if args.only and name not in args.only:
            continue
        namespace = argparse.Namespace(out=str(CORPORA / name), **extra)
        print(f"  {name}", flush=True)
        runner(namespace)
    return 0


COMMANDS = {"corpora": ("regenerate every corpus this package owns", _args, run)}
